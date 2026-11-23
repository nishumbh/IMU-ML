import { useId, useState } from 'react';
import { db } from '../lib/db';
import { downloadText } from '../lib/download';
import { forgetWorkerKeys } from '../lib/importer';
import { sessionToJson } from '../lib/exporters';

export function SettingsPage() {
  const confirmId = useId();
  const [status, setStatus] = useState('');
  const [confirmText, setConfirmText] = useState('');

  async function forget() {
    try {
      await forgetWorkerKeys();
      setStatus('Derived keys removed from memory. You will be asked for the passphrase again.');
    } catch {
      setStatus('Could not clear keys. Close this tab to remove them.');
    }
  }

  async function exportAll() {
    const rows = await db.sessions.toArray();
    if (rows.length === 0) {
      setStatus('No sessions to export.');
      return;
    }
    const payload = rows.map((r) => JSON.parse(sessionToJson(r.session)) as unknown);
    downloadText('knee-brace-sessions.json', JSON.stringify(payload, null, 2), 'application/json');
    setStatus(`Exported ${rows.length} session(s) as JSON.`);
  }

  async function deleteAll() {
    if (confirmText !== 'DELETE') {
      setStatus('Type DELETE to confirm.');
      return;
    }
    await db.sessions.clear();
    await forgetWorkerKeys().catch(() => undefined);
    setConfirmText('');
    setStatus('All local data deleted from this browser.');
  }

  return (
    <section aria-labelledby="settings-heading">
      <h1 id="settings-heading">Settings</h1>

      <section className="card" aria-labelledby="key-heading">
        <h2 id="key-heading">Passphrase and keys</h2>
        <p>The passphrase is never stored. A derived key is held only in memory, and only if you chose to keep it during import.</p>
        <button type="button" className="button secondary" onClick={forget}>Forget keys in memory</button>
      </section>

      <section className="card" aria-labelledby="export-heading">
        <h2 id="export-heading">Export</h2>
        <p>Download every decrypted session as one JSON file. This is plain, unencrypted data on your device.</p>
        <button type="button" className="button secondary" onClick={exportAll}>Export all as JSON</button>
      </section>

      <section className="card danger-zone" aria-labelledby="delete-heading">
        <h2 id="delete-heading">Delete local data</h2>
        <p>Removes every decrypted session stored in this browser. Your .kbx files on the SD card are not touched.</p>
        <div className="field">
          <label htmlFor={confirmId}>Type DELETE to confirm</label>
          <input id={confirmId} value={confirmText} onChange={(e) => setConfirmText(e.target.value)} autoComplete="off" />
        </div>
        <button type="button" className="button danger" onClick={deleteAll}>Delete all local data</button>
      </section>

      <p role="status" className="status">{status}</p>
    </section>
  );
}
