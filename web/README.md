# Knee Brace Journey (web)

A browser-only viewer for the encrypted `.kbx` logs written to the thigh pod's SD card
(format: `docs/SD_LOG_FORMAT.md`, KBX1). It decrypts the files, analyses them and shows
a rehab journey. There is no backend.

> **Research prototype. Not medical advice. Do not use to guide treatment without your clinician.**

## Run it

Requires Node 22 and npm.

```sh
cd web
npm install
npm run dev        # local dev server
npm test           # Vitest: fixture decryption, tamper/truncation, analysis, storage, smoke render
npm run typecheck  # tsc, strict
npm run build      # typecheck + production build into dist/
npm run lint       # ESLint
npm run preview    # serve dist/ locally
```

Crypto needs a secure context: `localhost` or HTTPS. The directory picker uses the File
System Access API where available (Chromium); other browsers get a multi-file picker.

## Privacy model

- Every file is read, decrypted and analysed in this browser. Nothing is uploaded.
  The app makes no network requests for your data.
- Decryption runs in a Web Worker with `crypto.subtle`: PBKDF2-HMAC-SHA256 (600 000
  iterations, salt from the header) to AES-256-GCM, with the 96-byte header plus the chunk
  counter as additional data. Every chunk's tag is checked.
- Decrypted sessions are stored in IndexedDB (Dexie) under this origin, in this browser
  only. Settings can export them, or delete them all.
- The passphrase is never stored. It is held only while you type it and is cleared after
  each decrypt. Optionally, the derived key is kept in worker memory until you close the
  tab or choose "Forget keys in memory".
- Exported JSON and CSV are plain, unencrypted files on your device. Treat them as
  health-adjacent data.

## Limits

- **No key-in-device protection.** The pod stores the derived key in its NVS (see the spec,
  section 2). Anyone with physical access to a pod can read its logs without the passphrase.
  This website cannot change that.
- **Estimated knee angle is not calibrated.** The range of motion is a complementary-filter
  estimate from the thigh and shank tilt sensors. It has no per-user zero point and no
  validation against a goniometer. Only changes over time are meaningful. It is labelled
  "Uncalibrated estimate" everywhere it appears.
- **Predicted form labels are research categories**, not clinical results. "Correct form"
  is a share of model windows, not a score.
- **Partial recovery.** A file cut short (power loss) shows only its verified chunks and is
  marked partial. A tampered chunk stops decryption of that file with an error; later data
  is not shown.
- Analysis constants (filter gain, gyro sign, class and exercise names, thresholds) live in
  `src/lib/config.ts`. The gyro sign and the exercise and class names are marked TODO to
  confirm against the firmware.

## Fixtures

`src/__fixtures__/` holds `valid.kbx`, `truncated.kbx`, `tampered.kbx` and `expected.json`,
copied from `tests/fixtures/kbx/` (do not edit them there). The fixture passphrase
`correct horse battery staple` is a **public test value**, published on purpose. Never use it
for real recordings.

## Layout

```
src/
  lib/kbx.ts          KBX1 reader (header, chunks, records); mirrors the spec
  lib/analysis.ts     per-session metrics, knee-angle estimate (uncalibrated)
  lib/journey.ts      weekly counts, streak, trends, milestones
  lib/config.ts       every tunable constant
  lib/db.ts           Dexie schema
  lib/importer.ts     worker client; saves sessions (keeps the most complete copy)
  lib/exporters.ts    JSON and CSV export
  workers/            decrypt worker and its message protocol
  pages/              Journey, Sessions, Session detail, Import, Settings
  components/         import panel, safety banner, stat tile
  __fixtures__/       public test vectors
```
