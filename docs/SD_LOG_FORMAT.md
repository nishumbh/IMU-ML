# KBX1: encrypted SD session log format

Status: version 1, draft for the firmware (`firmware/knee_pod_s3/`), Python tools
(`src/knee_rehab/secure_log.py`) and website (`web/`) implementers.
This file is the source of truth. The Python reference implementation must match
it; where the two disagree, fix the code or open a spec revision.

Not a medical device. Research logging only.

## 1. Summary

- One file per session: `/KB/S<NNNN>.kbx` on the microSD card, `NNNN` = session
  number in decimal, zero-padded to four digits (`S0001.kbx`). Range 0000-9999.
- Header: 96 bytes, plaintext, fully authenticated as AES-GCM additional data.
- Body: a sequence of chunks. Each chunk is one AES-256-GCM ciphertext holding
  about 1 second of records.
- Key: PBKDF2-HMAC-SHA256 of the passphrase, 600 000 iterations, 32-byte output.
- Records inside a chunk are tag-length-value style so readers skip unknown types.
- All multi-byte integers and floats are **little-endian**. Floats are IEEE 754
  binary32 unless stated otherwise.
- Streaming: a reader needs only the 96-byte header and one chunk at a time.

## 2. Security model and limits (read this first)

- **No ESP32 flash encryption.** The derived AES key is stored in the thigh pod's
  NVS. Anyone with physical access and a serial/JTAG path can read it, which
  makes every log on that device readable without the passphrase. Enabling flash
  encryption (and NVS encryption) is a listed hardening step, not part of v1.
- **Wrong passphrase detection.** There is no separate password-check value. A wrong
  passphrase produces a wrong key, so the GCM tag of the first chunk fails. Readers
  report this as "wrong passphrase or tampered data". A file with **zero** chunks
  cannot be verified: any passphrase "succeeds" and yields no records. Readers set
  `verified = false` in that case.
- **Integrity.** Every chunk tag covers the full header and the chunk counter.
  Any bit flip in a complete chunk, or in the header, fails the tag check. The
  only unauthenticated bytes are the chunk length field (a wrong length makes
  the tag fail or makes the chunk look truncated, see section 6) and the
  zero-filled tail rule.
- **Truncation is not tampering.** Bytes missing at end-of-file are reported as
  `truncated`, not as an error. Anything else is an error.
- **No forward secrecy.** The key depends only on the passphrase and the per-device
  salt. Compromise of the passphrase or the key decrypts every log from that device,
  past and future. Per-file keys would need a PBKDF2 run per file on the device,
  which is not possible (see section 4).
- **Nonce uniqueness.** Nonces are `prefix(4) || counter(8)`. The prefix is random
  per file (device hardware RNG, `esp_random()`), and the counter starts at 0 in
  each file. With one key per device, two files collide only if their prefixes
  match (probability about N²/2³³ across N files, roughly 1 in 10⁴ for 1 000
  logs). Writers MUST NOT reuse a prefix for a file they are rewriting.
- **Passphrase is transmitted in the clear** when used with the local review
  server (`interface.py`, HTTP on localhost). Use only on a trusted computer.
- **Side channels, RNG quality, and key storage on the PC** are out of scope.
- **Passphrase rules.** NFC-normalise the passphrase, encode as UTF-8, do not trim
  whitespace. Tools MUST reject fewer than 12 Unicode code points or more than
  1024 UTF-8 bytes, and reject control characters U+0000-U+001F and U+007F. The
  reader applies the NFC normalisation and the UTF-8 encoding but does not reject
  short passphrases, so a file written under a shorter passphrase still reads.
- **Fixture passphrases are public test values.** The passphrase
  `correct horse battery staple` and the salts and nonces in `tests/fixtures/kbx/`
  are published on purpose. Never use them for real recordings.

## 3. Key derivation

```
passphrase_bytes = UTF8(NFC(passphrase))
key = PBKDF2-HMAC-SHA256(password = passphrase_bytes, salt = header.pbkdf2_salt,
                         iterations = 600000, dkLen = 32)
```

- The salt is **per device**, generated at provisioning (16 random bytes), and
  stored in the header of every file from that device. The device never runs
  PBKDF2. Provisioning (PC side) computes the key and writes key + salt to NVS.
- Readers MUST reject any `pbkdf2_iterations` other than 600000 in version 1.

## 4. Header (96 bytes, plaintext, byte offsets)

| Offset | Size | Type | Name | Meaning / rule |
|---:|---:|---|---|---|
| 0 | 4 | bytes | `magic` | ASCII `KBX1`. Otherwise reject as not KBX. |
| 4 | 1 | u8 | `format_version` | `1`. Reject any other value (no best-effort parse). |
| 5 | 1 | u8 | `header_flags` | `0`. Reserved; reject nonzero in v1. |
| 6 | 2 | u16 | `header_length` | `96`. Reject any other value in v1. |
| 8 | 16 | bytes | `device_id` | ASCII `[A-Za-z0-9._-]`, 1-16 chars, NUL-padded to 16. |
| 24 | 2 | u16 | `session_number` | 0-9999. Should equal `NNNN` in the file name. |
| 26 | 2 | u16 | `sample_rate_hz` | Nominal IMU rate, 1-1000 Hz (firmware: 50). |
| 28 | 4 | f32 | `accel_lsb_mps2` | m/s² per raw accelerometer LSB. Finite, > 0. |
| 32 | 4 | f32 | `gyro_lsb_radps` | rad/s per raw gyroscope LSB. Finite, > 0. |
| 36 | 16 | bytes | `pbkdf2_salt` | Per-device random salt (section 3). |
| 52 | 4 | u32 | `pbkdf2_iterations` | `600000`. |
| 56 | 4 | bytes | `nonce_prefix` | Random per file (section 2). |
| 60 | 8 | u64 | `wall_clock_unix_s` | Unix seconds at session start, from NTP during provisioning. `0` = unknown. |
| 68 | 24 | ASCII | `firmware_version` | e.g. `knee_pod_s3 0.1.0`, NUL-padded, 1-24 printable ASCII bytes. |
| 92 | 4 | bytes | `reserved` | `00 00 00 00`. Reject nonzero in v1. |

Factory full scale for the MPU6050 used by the pods (ESP32 firmware must store
these exact values as float32; they match `firmware/two_imu_logger` ±4 g and
±500 °/s):

- `accel_lsb_mps2` = 9.80665 / 8192 (≈ 0.00119708 m/s² per LSB at ±4 g)
- `gyro_lsb_radps` = (π / 180) / 65.536 (≈ 2.6632e-4 rad/s per LSB at ±500 °/s)

The header is the AAD prefix of every chunk (section 5). Readers MUST read all
96 bytes before any chunk and MUST check the fields above before deriving a key.

## 5. Chunks

File = header followed by zero or more chunks, back to back, no padding.

| Offset in chunk | Size | Type | Name |
|---:|---:|---|---|
| 0 | 4 | u32 | `ct_len` = length of ciphertext, in bytes, excluding tag. Range 1 to 65536. |
| 4 | 12 | bytes | `nonce` = `nonce_prefix` (4 bytes) ‖ `counter` (u64 LE, 8 bytes) |
| 16 | `ct_len` | bytes | `ciphertext` = AES-256-GCM encryption of the chunk plaintext |
| 16 + `ct_len` | 16 | bytes | `tag` = GCM authentication tag |

- Chunk size on disk = 32 + `ct_len`.
- `counter` starts at 0 for the first chunk and increments by 1 for each chunk in
  the file. It is never reset within a file.
- **AAD** for chunk `k`: the 96 header bytes exactly as stored, followed by the
  chunk counter `k` as u64 LE (104 bytes in total). The `ct_len` field is not in
  the AAD. A wrong length always fails the tag check on that chunk.
- Writers flush one chunk per ~1 s of IMU data (`sample_rate_hz` IMU records), or
  earlier if the plaintext reaches 32 KiB, or on stop. Each chunk is written with
  one write call and then synced (`fsync`), so a power cut can only affect the
  chunk being written.
- A chunk plaintext MUST contain at least one record. Writers never write empty chunks.

### 5.1 Plaintext of a chunk

A chunk plaintext is a sequence of records, back to back. No record spans two
chunks. The plaintext MUST end exactly at a record boundary. Trailing bytes after
the last complete record are a format error, not truncation (the tag already
authenticated them).

## 6. Reading and truncation rules

Readers process the file in order, chunk by chunk:

1. Read 96 bytes. Fewer than 96 bytes is a format error ("header incomplete"): the file is not a usable session. A power cut during header write is the only way this happens, and the writer's exclusive create makes it visible.
2. Validate the header (section 4). Derive the key (section 3).
3. For each chunk, starting at counter 0:
   a. If fewer than 4 bytes remain → **truncated**, stop.
   b. Read `ct_len`. If the remaining bytes (from this chunk start) are all `0x00` → **truncated** (zero-filled tail left by a power cut), stop.
   c. If `ct_len` is 0 or greater than 65536 (and the rest is not all zero) → format error.
   d. If the chunk (`32 + ct_len` bytes) extends past end-of-file → **truncated**, stop. Discard the partial bytes.
   e. Decrypt with the AAD and nonce above. Tag failure → **authentication error**, stop. Do not return data from later chunks.
   f. Parse the plaintext records (section 7). Any record that does not parse → format error.
4. Clean end of file on a chunk boundary → `truncated = false`.

Tag failure on a chunk that is complete on disk is never treated as truncation.
A change to `ct_len` that makes the final chunk extend past end-of-file cannot be
told apart from a power cut, so it is reported as `truncated = true` (flagged,
never silent). Every other change to a chunk fails its tag.
A wrong passphrase therefore fails at chunk 0 with an authentication error.

Readers MUST NOT return partial records from a chunk whose tag fails.

## 7. Records

Every record is:

| Offset in record | Size | Type | Name |
|---:|---:|---|---|
| 0 | 1 | u8 | `type` |
| 1 | 2 | u16 | `body_length` = number of body bytes that follow |
| 3 | `body_length` | bytes | `body` |

Readers MUST skip unknown types using `body_length`. An unknown record is
counted (`unknown_records`) and otherwise ignored. Readers MUST NOT fail on an
unknown type.

Type codes:

| Code | Name | Required body length (v1) |
|---:|---|---:|
| 0x01 | IMU | 29 |
| 0x02 | INFER | 18 |
| 0x03 | EVENT | 7 |
| 0x04-0x3F | reserved for future versions | |
| 0x40-0xFF | free for experiments; ignored by v1 readers | |

For a known type, a body shorter than the required length is a format error. A
longer body is accepted and the extra bytes are ignored, so future versions can
append fields.

### 7.1 IMU (type 0x01, body 29 bytes)

| Body offset | Size | Type | Name | Meaning |
|---:|---:|---|---|---|
| 0 | 4 | u32 | `seq` | IMU sample index, counted from 0 at session start. Strictly increasing across the file. Gaps are allowed and mean samples were lost. |
| 4 | 1 | u8 | `flags` | bit 0 `LINK_LOSS`: the shank packet was missing for this sample, so shank channels are zero-filled and must not be used as measurements. bits 1-7 reserved, 0 in v1; readers keep but ignore them. |
| 5 | 2 | i16 | `ch0` | thigh ax (raw) |
| 7 | 2 | i16 | `ch1` | thigh ay |
| 9 | 2 | i16 | `ch2` | thigh az |
| 11 | 2 | i16 | `ch3` | thigh gx |
| 13 | 2 | i16 | `ch4` | thigh gy |
| 15 | 2 | i16 | `ch5` | thigh gz |
| 17 | 2 | i16 | `ch6` | shank ax |
| 19 | 2 | i16 | `ch7` | shank ay |
| 21 | 2 | i16 | `ch8` | shank az |
| 23 | 2 | i16 | `ch9` | shank gx |
| 25 | 2 | i16 | `ch10` | shank gy |
| 27 | 2 | i16 | `ch11` | shank gz |

Channel order is exactly the firmware CSV order: `thigh_ax, thigh_ay, thigh_az,
thigh_gx, thigh_gy, thigh_gz, shank_ax, shank_ay, shank_az, shank_gx, shank_gy,
shank_gz`. Values are raw int16 counts. Physical values are `raw × lsb` from the
header, and the axis is the sensor frame (no mounting correction).

Time: `time_s = seq / sample_rate_hz`. The CSV `time_ms` column is `seq × 1000 / sample_rate_hz`.

### 7.2 INFER (type 0x02, body 18 bytes)

One record per classified 2-second window, written by the on-device model.

| Body offset | Size | Type | Name | Meaning |
|---:|---:|---|---|---|
| 0 | 4 | u32 | `window_seq` | IMU `seq` of the **last** sample in the window. The window covers `[window_seq − N + 1, window_seq]`, `N` = window length in samples. |
| 4 | 1 | u8 | `exercise` | 0 = none, 1 = squat, 2 = seated leg extension, 3 = gait. |
| 5 | 4 | f32 | `p0` | probability of class 0 (the exercise's correct class) |
| 9 | 4 | f32 | `p1` | probability of class 1 (error class 1) |
| 13 | 4 | f32 | `p2` | probability of class 2 (error class 2) |
| 17 | 1 | u8 | `label` | class index 0, 1 or 2 after the confidence rule, or `255` = reject (no confident class). |

Class names per exercise (from `docs/DATASETS.md`, dataset class IDs in brackets):

| `exercise` | class 0 (correct) | class 1 | class 2 |
|---|---|---|---|
| 1 squat | Correct Form [0] | Weight Transfer to Healthy Leg [1] | Injured Leg Placed in Front [2] |
| 2 extension | Correct Form [3] | Incomplete Extension [4] | Limb Lift from Chair [5] |
| 3 gait | Correct Form [6] | Incomplete Knee Extension [7] | Hip Abduction with Extended Knee [8] |

Probabilities MUST be finite and in [0, 1]. Readers reject an INFER record that
violates this as a format error. The label is a research category, not a
clinical result; it is never a "correct" approval.

### 7.3 EVENT (type 0x03, body 7 bytes)

| Body offset | Size | Type | Name | Meaning |
|---:|---:|---|---|---|
| 0 | 1 | u8 | `kind` | 0 = start, 1 = stop, 2 = exercise_change, 3 = low_battery. Other values: readers keep the record and treat it as unknown kind (no error). |
| 1 | 2 | u16 | `battery_mv` | Battery voltage in mV at the event. `0` = unknown. |
| 3 | 4 | u32 | `payload` | start: `0`. stop: number of IMU samples written (`last seq + 1`). exercise_change: new `exercise` id (0-3). low_battery: `0`. |

The event's position in the stream is its position among the records. There is no timestamp; use the nearest preceding IMU `seq`.

## 8. Writer (device and PC) rules

- Create the file with exclusive create. Never overwrite an existing `S<NNNN>.kbx`. Pick the next unused session number.
- Write the header, then chunks in order. Counter increments per chunk.
- IMU `seq` strictly increasing. Each sample's channel values are the raw int16 counts.
- Never write a plaintext record that exceeds 64 KiB total chunk plaintext.
- On stop: flush the pending chunk, then `fsync`.

## 9. Versioning

- `format_version` changes only for incompatible layout changes. v1 readers reject
  any other version (no best-effort parsing).
- Compatible additions in v1, without a version bump:
  - new record type codes (readers skip unknown types);
  - extra trailing bytes at the end of a known record body (readers ignore them);
  - new flag bits in IMU `flags` (readers ignore bits they do not know).
- Changing any header field offset, its size or its meaning requires `format_version = 2`.
- The magic `KBX1` identifies the family. A future file family would use a different magic.

## 10. Python reference

`src/knee_rehab/secure_log.py`:

- `KBXWriter(path, passphrase, *, device_id, session_number, sample_rate_hz, accel_lsb, gyro_lsb, firmware_version, wall_clock_unix_s=0, salt=None, nonce_prefix=None)`, with `write_imu`, `write_infer`, `write_event`, `write_raw_record` (tests and experiments), `flush`, `close`. Context manager. Exclusive create: never overwrites.
- `read_kbx(path, passphrase) -> Session` and `read_kbx_bytes(data, passphrase) -> Session`. `Session` has `header`, `records`, `truncated`, `chunks`, `unknown_records`, `verified`.
- `KBXReader(stream, passphrase).chunks()` for streaming: yields one chunk's records at a time.
- Exceptions: `KBXError` (base), `KBXFormatError`, `KBXAuthError` (wrong passphrase or tampered chunk), `KBXPassphraseError`.
- CLI: `python -m knee_rehab.secure_log encrypt|decrypt|inspect|make-fixtures`. The passphrase comes from the `KBX_PASSPHRASE` environment variable or an interactive prompt. It is never a command-line argument.
  - `encrypt --csv IN.csv --output S0001.kbx --device-id ... --session 1 --sample-rate 50 --firmware-version ...`: takes the firmware CSV (SI units). Quantises to raw counts (`round(v / lsb)`). Refuses on int16 overflow instead of clipping.
  - `decrypt IN.kbx --output OUT.csv`: writes the firmware CSV column order (`time_ms`, then `thigh_ax` … `shank_gz`) in SI units (`raw × lsb`). Only IMU records go into the CSV. It prints truncation and link-loss counts to stderr. Output is never overwritten.
  - `inspect IN.kbx [--header-only]`: JSON to stdout.
  - `make-fixtures [--out-dir DIR]`: writes the deterministic vectors below.

Decrypted CSV is the same schema that `interface.py` and `predict.py` already read. Quantisation is the only difference: the CSV values are `raw × lsb`, not the originals.

## 11. Test vectors (`tests/fixtures/kbx/`)

Generated by `make-fixtures`, byte-for-byte deterministic. Fixed values:

- passphrase: `correct horse battery staple` (public test value)
- device_id: `KBX-TEST-01`, session 1, sample rate 50 Hz, firmware `fixture 1.0.0`
- wall clock: 1767225600 (2026-01-01T00:00:00Z)
- salt: `00 01 02 … 0f`; nonce prefix: `a5 5a c3 3c`
- accel/gyro LSB: the MPU6050 factors in section 4

Content: 150 IMU samples (3 s) with a deterministic integer pattern; a START event at seq 0; an INFER record (exercise 1) written after seq 99; a LINK_LOSS flag on seq 120 with zero shank channels; an unknown record (type `0x70`, 3-byte body) and a STOP event with payload 150 at the end.

Chunking that results from the writer rule (flush every 50 IMU records): chunk 0 = START + seq 0-49; chunk 1 = seq 50-99; chunk 2 = INFER + seq 100-149; chunk 3 = unknown record + STOP. `valid.kbx` has 4 chunks, 153 records (150 IMU, 1 INFER, 2 EVENT), 1 unknown record skipped, and 1 link-loss sample.

Files:

| File | Contents |
|---|---|
| `valid.kbx` | Four complete chunks. Must decrypt with `verified = true`, `truncated = false`. |
| `truncated.kbx` | `valid.kbx` with the last 10 bytes removed. Chunk 3 is cut, so the result is chunks 0-2 (152 records, no STOP) with `truncated = true`. |
| `tampered.kbx` | `valid.kbx` with one bit flipped in chunk 1's ciphertext. Must raise an authentication error at chunk 1. |
| `expected.json` | Expected results for all three, including the decoded records of `valid.kbx`. The passphrase is stored there as a public test value. |

The website (`web/src/__fixtures__/`) copies these files verbatim. Its tests must
produce the same records and the same error outcomes.

## 12. Open items for implementers

- Firmware: the 1 s chunk trigger and the 32 KiB cap must keep the writer task
  inside the PSRAM ring buffer budget. Measure on hardware.
- Firmware: provisioning writes key + salt to NVS. Document whether `session_number`
  is persisted in NVS or derived from the SD directory listing.
- Website: a file with zero chunks reports `verified = false`, not success.
