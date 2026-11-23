import type { WorkerRequest, WorkerResponse } from '../workers/protocol';
import { db, sessionId, type StoredSession } from './db';
import type { KbxErrorCode } from './errors';
import { sessionStartMs, type DecodedSession } from './types';

export interface ImportResult {
  name: string;
  ok: boolean;
  session?: DecodedSession;
  code?: KbxErrorCode;
  message?: string;
}

let worker: Worker | null = null;
let nextId = 1;
const pending = new Map<number, (r: WorkerResponse) => void>();

function getWorker(): Worker {
  if (!worker) {
    worker = new Worker(new URL('../workers/kbx.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (e: MessageEvent<WorkerResponse>) => {
      const resolve = pending.get(e.data.id);
      pending.delete(e.data.id);
      resolve?.(e.data);
    };
  }
  return worker;
}

function send(req: WorkerRequest, transfer: Transferable[] = []): Promise<WorkerResponse> {
  return new Promise((resolve) => {
    pending.set(req.id, resolve);
    getWorker().postMessage(req, transfer);
  });
}

/** Decrypts files one at a time in the worker. Errors are per file, never fatal for the batch. */
export async function decryptFiles(
  files: { name: string; bytes: ArrayBuffer }[],
  passphrase: string,
  keepKey: boolean,
): Promise<ImportResult[]> {
  const results: ImportResult[] = [];
  for (const f of files) {
    const id = nextId++;
    const res = await send(
      { type: 'decrypt', id, name: f.name, bytes: f.bytes, passphrase, keepKey },
      [f.bytes],
    );
    if (res.type === 'ok') results.push({ name: f.name, ok: true, session: res.session });
    else if (res.type === 'error') results.push({ name: f.name, ok: false, code: res.code, message: res.message });
  }
  return results;
}

/** Drops any key the worker holds in memory. */
export async function forgetWorkerKeys(): Promise<void> {
  await send({ type: 'forget', id: nextId++ });
}

/**
 * Stores decrypted sessions. Re-importing the same session replaces the stored
 * copy only when the new copy has at least as many verified chunks.
 */
export async function saveSessions(results: ImportResult[], importedAtMs = Date.now()): Promise<number> {
  let saved = 0;
  for (const r of results) {
    if (!r.ok || !r.session) continue;
    const s = r.session;
    const id = sessionId(s.header.deviceId, s.header.sessionNumber);
    const existing = await db.sessions.get(id);
    if (existing && existing.session.chunks > s.chunks) continue;
    const row: StoredSession = {
      id,
      deviceId: s.header.deviceId,
      sessionNo: s.header.sessionNumber,
      dateMs: sessionStartMs(s.header) ?? importedAtMs,
      importedAtMs,
      sourceNames: [...new Set([...(existing?.sourceNames ?? []), r.name])],
      session: s,
    };
    await db.sessions.put(row);
    saved++;
  }
  return saved;
}
