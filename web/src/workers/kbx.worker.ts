import { decryptKbx, type KeyCache } from '../lib/kbx';
import { KbxError } from '../lib/errors';
import type { WorkerRequest, WorkerResponse } from './protocol';

/** Untyped worker scope: the app tsconfig uses the DOM lib, so the worker global is declared here. */
const scope = self as unknown as {
  onmessage: ((event: MessageEvent<WorkerRequest>) => void) | null;
  postMessage(message: WorkerResponse): void;
};

const keyCache: KeyCache = new Map();

scope.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const req = event.data;
  if (req.type === 'forget') {
    keyCache.clear();
    scope.postMessage({ type: 'forgotten', id: req.id });
    return;
  }
  try {
    const session = await decryptKbx(new Uint8Array(req.bytes), req.passphrase, { keyCache, keepKey: req.keepKey });
    scope.postMessage({ type: 'ok', id: req.id, name: req.name, session });
  } catch (err) {
    if (err instanceof KbxError) {
      scope.postMessage({ type: 'error', id: req.id, name: req.name, code: err.code, message: err.message });
    } else {
      scope.postMessage({ type: 'error', id: req.id, name: req.name, code: 'INTERNAL', message: 'Unexpected error while decrypting.' });
    }
  }
};
