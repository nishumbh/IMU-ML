import { useId, useRef, useState } from 'react';
import { decryptFiles, saveSessions, type ImportResult } from '../lib/importer';
import { USER_MESSAGES } from '../lib/errors';
import { isComplete } from '../lib/types';

interface Props {
  onSaved?: (count: number) => void;
}

/** True if the string has U+0000-U+001F or U+007F (spec section 2). */
function hasControlChars(s: string): boolean {
  for (const ch of s) {
    const c = ch.codePointAt(0) ?? 0;
    if (c <= 0x1f || c === 0x7f) return true;
  }
  return false;
}

/** Collects .kbx files from a directory tree via the File System Access API, when available. */
async function collectFromDirectory(): Promise<File[]> {
  const picker = window.showDirectoryPicker;
  if (!picker) return [];
  const dir = await picker({ mode: 'read' });
  const out: File[] = [];
  async function walk(d: FsaDirectoryHandle): Promise<void> {
    for await (const entry of d.values()) {
      if (entry.kind === 'directory') await walk(entry);
      else if (entry.name.toLowerCase().endsWith('.kbx')) out.push(await entry.getFile());
    }
  }
  await walk(dir);
  return out;
}

export function ImportPanel({ onSaved }: Props) {
  const passId = useId();
  const keepId = useId();
  const fileInputId = useId();
  const fileInput = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [passphrase, setPassphrase] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [keepKey, setKeepKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<ImportResult[] | null>(null);
  const [status, setStatus] = useState('');
  const hasFsa = typeof window !== 'undefined' && typeof window.showDirectoryPicker === 'function';

  async function pickFolder() {
    try {
      const found = await collectFromDirectory();
      setFiles(found);
      setStatus(found.length ? `${found.length} .kbx file(s) found in folder.` : 'No .kbx files in that folder.');
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setStatus('Could not read that folder.');
    }
  }

  async function run() {
    if (files.length === 0) {
      setStatus('Choose at least one .kbx file first.');
      return;
    }
    if (hasControlChars(passphrase)) {
      setStatus('The passphrase contains control characters, which are not allowed.');
      return;
    }
    if (!window.crypto?.subtle) {
      setStatus(USER_MESSAGES.NO_WEBCRYPTO);
      return;
    }
    setBusy(true);
    setStatus('Decrypting in a background worker. Nothing leaves this device.');
    try {
      const inputs = await Promise.all(files.map(async (f) => ({ name: f.name, bytes: await f.arrayBuffer() })));
      const res = await decryptFiles(inputs, passphrase, keepKey);
      const saved = await saveSessions(res);
      setResults(res);
      const failed = res.filter((r) => !r.ok).length;
      setStatus(`Saved ${saved} session(s).${failed ? ` ${failed} file(s) failed.` : ''}`);
      onSaved?.(saved);
    } catch {
      setStatus('Import failed unexpectedly. Check that the browser supports Web Workers and Web Crypto.');
    } finally {
      setPassphrase(''); // never keep the passphrase in state after an attempt
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="import-heading">
      <h2 id="import-heading">Import from the SD card</h2>
      <p className="muted">
        Copy the <code>/KB</code> folder from the thigh pod's SD card, or pick the <code>.kbx</code> files directly.
      </p>

      <div className="field-row">
        <label htmlFor={fileInputId} className="button secondary">
          Choose .kbx files
        </label>
        <input
          id={fileInputId}
          ref={fileInput}
          className="visually-hidden"
          type="file"
          accept=".kbx"
          multiple
          onChange={(e) => {
            const list = Array.from(e.target.files ?? []);
            setFiles(list);
            setStatus(`${list.length} file(s) selected.`);
          }}
        />
        {hasFsa ? (
          <button type="button" className="button secondary" onClick={pickFolder}>
            Choose SD folder
          </button>
        ) : null}
      </div>
      <p className="muted" aria-live="polite">
        {files.length ? `${files.length} file(s) ready: ${files.slice(0, 3).map((f) => f.name).join(', ')}${files.length > 3 ? ', ...' : ''}` : 'No files selected.'}
      </p>

      <div className="field">
        <label htmlFor={passId}>Passphrase</label>
        <div className="field-row">
          <input
            id={passId}
            type={showPass ? 'text' : 'password'}
            autoComplete="off"
            spellCheck={false}
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            aria-describedby={`${passId}-hint`}
          />
          <button type="button" className="button ghost" onClick={() => setShowPass((v) => !v)} aria-pressed={showPass}>
            {showPass ? 'Hide' : 'Show'}
          </button>
        </div>
        <p id={`${passId}-hint`} className="muted small">
          Used only to derive a key in memory. It is not saved. Leave empty to reuse a key already held for this tab.
          {passphrase.length > 0 && Array.from(passphrase.normalize('NFC')).length < 12
            ? ' Note: the device passphrase rules call for at least 12 characters.'
            : null}
        </p>
      </div>

      <div className="field">
        <label className="checkbox" htmlFor={keepId}>
          <input id={keepId} type="checkbox" checked={keepKey} onChange={(e) => setKeepKey(e.target.checked)} />
          Keep the derived key in memory until I close this tab
        </label>
      </div>

      <button type="button" className="button primary" onClick={run} disabled={busy}>
        {busy ? 'Decrypting...' : 'Decrypt and save'}
      </button>

      <p role="status" className="status">{status}</p>

      {results ? (
        <ul className="result-list" aria-label="Import results">
          {results.map((r) => (
            <li key={r.name} className={r.ok ? 'ok' : 'err'}>
              <strong>{r.name}</strong>:{' '}
              {r.ok ? (
                r.session && isComplete(r.session) ? 'decrypted and verified.' : `partially recovered. ${r.session?.recoveryNote ?? ''}`
              ) : (
                <span>{r.message ?? (r.code ? USER_MESSAGES[r.code] : 'Failed.')}</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
