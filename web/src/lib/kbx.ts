/**
 * KBX1 reader, browser side. Mirrors docs/SD_LOG_FORMAT.md section 4-7 and the
 * Python reference (src/knee_rehab/secure_log.py). Little-endian throughout.
 */
import { KbxError } from './errors';
import {
  EVENT_KIND_NAMES,
  type DecodedSession,
  type EventKindName,
  type EventRecord,
  type ImuRecord,
  type InferRecord,
  type KbxFile,
  type KbxHeader,
  type KbxRecord,
} from './types';

export const HEADER_LENGTH = 96;
export const PBKDF2_ITERATIONS = 600_000;
const CHUNK_OVERHEAD = 32; // ct_len(4) + nonce(12) + tag(16)
const MAX_CT_LEN = 65_536;
const IMU_BODY = 29;
const INFER_BODY = 18;
const EVENT_BODY = 7;
const MAGIC = 'KBX1';
const DEVICE_ID_RE = /^[A-Za-z0-9._-]{1,16}$/;

/** Derived keys kept in memory only, keyed by the header salt (hex). */
export type KeyCache = Map<string, CryptoKey>;

export interface ReadOptions {
  keyCache?: KeyCache;
  /** Store the derived key in keyCache after the first chunk verifies. */
  keepKey?: boolean;
}

const decoder = new TextDecoder('ascii');
const encoder = new TextEncoder();

function hex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

function bad(msg: string): KbxError {
  return new KbxError('BAD_HEADER', msg);
}

/** Reads a NUL-padded field: the value is the bytes before the first NUL, and everything after must be NUL. */
function readPadded(bytes: Uint8Array, start: number, len: number, what: string): string {
  const field = bytes.subarray(start, start + len);
  const nul = field.indexOf(0);
  const valueBytes = nul === -1 ? field : field.subarray(0, nul);
  if (nul !== -1 && field.subarray(nul).some((b) => b !== 0)) throw bad(`${what}: bytes after NUL padding are not zero.`);
  return decoder.decode(valueBytes);
}

/** Parses and validates the 96-byte header (section 4). Throws KbxError on any violation. */
export function parseHeader(bytes: Uint8Array): KbxHeader {
  if (bytes.length < 4 || decoder.decode(bytes.subarray(0, 4)) !== MAGIC) {
    throw new KbxError('NOT_KBX', 'Magic is not KBX1.');
  }
  if (bytes.length < HEADER_LENGTH) throw bad('Header incomplete: fewer than 96 bytes.');
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  const formatVersion = dv.getUint8(4);
  if (formatVersion !== 1) throw new KbxError('UNSUPPORTED_VERSION', `format_version ${formatVersion} is not 1.`);
  if (dv.getUint8(5) !== 0) throw bad('header_flags is nonzero.');
  if (dv.getUint16(6, true) !== HEADER_LENGTH) throw bad('header_length is not 96.');

  const deviceId = readPadded(bytes, 8, 16, 'device_id');
  if (!DEVICE_ID_RE.test(deviceId)) throw bad('device_id has invalid characters or length.');

  const sessionNumber = dv.getUint16(24, true);
  if (sessionNumber > 9999) throw bad('session_number is above 9999.');

  const sampleRateHz = dv.getUint16(26, true);
  if (sampleRateHz < 1 || sampleRateHz > 1000) throw bad('sample_rate_hz is outside 1-1000.');

  const accelLsbMps2 = dv.getFloat32(28, true);
  const gyroLsbRadps = dv.getFloat32(32, true);
  if (!Number.isFinite(accelLsbMps2) || accelLsbMps2 <= 0) throw bad('accel_lsb_mps2 must be finite and > 0.');
  if (!Number.isFinite(gyroLsbRadps) || gyroLsbRadps <= 0) throw bad('gyro_lsb_radps must be finite and > 0.');

  const pbkdf2Iterations = dv.getUint32(52, true);
  if (pbkdf2Iterations !== PBKDF2_ITERATIONS) throw bad(`pbkdf2_iterations must be ${PBKDF2_ITERATIONS}.`);

  const wall = dv.getBigUint64(60, true);
  if (wall > BigInt(Number.MAX_SAFE_INTEGER)) throw bad('wall_clock_unix_s is out of range.');

  const firmwareVersion = readPadded(bytes, 68, 24, 'firmware_version');
  if (!/^[\x20-\x7e]{1,24}$/.test(firmwareVersion)) throw bad('firmware_version must be 1-24 printable ASCII bytes.');

  if (dv.getUint32(92, true) !== 0) throw bad('reserved field is nonzero.');

  return {
    formatVersion,
    deviceId,
    sessionNumber,
    sampleRateHz,
    accelLsbMps2,
    gyroLsbRadps,
    pbkdf2SaltHex: hex(bytes.subarray(36, 52)),
    pbkdf2Iterations,
    noncePrefixHex: hex(bytes.subarray(56, 60)),
    wallClockUnixS: wall === 0n ? null : Number(wall),
    firmwareVersion,
  };
}

/** PBKDF2-HMAC-SHA256 (600 000 iterations) to an AES-256-GCM key. Passphrase is NFC-normalised UTF-8. */
export async function deriveKey(passphrase: string, saltHex: string): Promise<CryptoKey> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new KbxError('NO_WEBCRYPTO', 'Web Crypto is not available.');
  const salt = hexToBytes(saltHex);
  const base = await subtle.importKey('raw', encoder.encode(passphrase.normalize('NFC')), 'PBKDF2', false, ['deriveKey']);
  return subtle.deriveKey(
    { name: 'PBKDF2', hash: 'SHA-256', salt, iterations: PBKDF2_ITERATIONS },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['decrypt'],
  );
}

function hexToBytes(h: string): Uint8Array {
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function u64le(n: number): Uint8Array {
  const out = new Uint8Array(8);
  new DataView(out.buffer).setBigUint64(0, BigInt(n), true);
  return out;
}

function isAllZeroFrom(bytes: Uint8Array, start: number): boolean {
  for (let i = start; i < bytes.length; i++) if (bytes[i] !== 0) return false;
  return true;
}

/** Parses the plaintext records of one chunk (section 7). */
export function parseRecords(plain: Uint8Array): { records: KbxRecord[]; unknown: number } {
  const dv = new DataView(plain.buffer, plain.byteOffset, plain.byteLength);
  const records: KbxRecord[] = [];
  let unknown = 0;
  let p = 0;
  while (p < plain.length) {
    if (plain.length - p < 3) throw new KbxError('BAD_RECORD', 'Record header cut off inside a chunk.');
    const type = dv.getUint8(p);
    const len = dv.getUint16(p + 1, true);
    const body = p + 3;
    if (body + len > plain.length) throw new KbxError('BAD_RECORD', 'Record body runs past the end of its chunk.');
    p = body + len;

    if (type === 0x01) {
      if (len < IMU_BODY) throw new KbxError('BAD_RECORD', 'IMU record body too short.');
      const channels: number[] = [];
      for (let c = 0; c < 12; c++) channels.push(dv.getInt16(body + 5 + c * 2, true));
      records.push({ type: 'imu', seq: dv.getUint32(body, true), flags: dv.getUint8(body + 4), channels });
    } else if (type === 0x02) {
      if (len < INFER_BODY) throw new KbxError('BAD_RECORD', 'INFER record body too short.');
      const probabilities = [dv.getFloat32(body + 5, true), dv.getFloat32(body + 9, true), dv.getFloat32(body + 13, true)];
      if (probabilities.some((v) => !Number.isFinite(v) || v < 0 || v > 1)) {
        throw new KbxError('BAD_RECORD', 'INFER probability outside [0, 1].');
      }
      const label = dv.getUint8(body + 17);
      if (label !== 0 && label !== 1 && label !== 2 && label !== 255) throw new KbxError('BAD_RECORD', 'INFER label not in {0, 1, 2, 255}.');
      records.push({
        type: 'infer',
        windowSeq: dv.getUint32(body, true),
        exercise: dv.getUint8(body + 4),
        probabilities,
        label,
      });
    } else if (type === 0x03) {
      if (len < EVENT_BODY) throw new KbxError('BAD_RECORD', 'EVENT record body too short.');
      const kind = dv.getUint8(body);
      const kindName: EventKindName = kind < EVENT_KIND_NAMES.length ? EVENT_KIND_NAMES[kind]! : 'unknown';
      records.push({ type: 'event', kind, kindName, batteryMv: dv.getUint16(body + 1, true), payload: dv.getUint32(body + 3, true) });
    } else {
      unknown++;
    }
  }
  return { records, unknown };
}

async function resolveKey(passphrase: string, header: KbxHeader, opts: ReadOptions): Promise<CryptoKey> {
  if (passphrase === '') {
    const cached = opts.keyCache?.get(header.pbkdf2SaltHex);
    if (cached) return cached;
    throw new KbxError('PASSPHRASE_REQUIRED', 'No passphrase given and no key held in memory.');
  }
  return deriveKey(passphrase, header.pbkdf2SaltHex);
}

/**
 * Reads and authenticates a whole .kbx file (sections 4-6). Returns every record
 * from chunks that verified. Throws KbxError on wrong passphrase or a corrupt chunk.
 */
export async function readKbx(bytes: Uint8Array, passphrase: string, opts: ReadOptions = {}): Promise<KbxFile> {
  const header = parseHeader(bytes);
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new KbxError('NO_WEBCRYPTO', 'Web Crypto is not available.');
  const key = await resolveKey(passphrase, header, opts);
  const noncePrefix = hexToBytes(header.noncePrefixHex);
  const headerBytes = bytes.subarray(0, HEADER_LENGTH);

  const records: KbxRecord[] = [];
  let unknownRecords = 0;
  let truncated = false;
  let verified = false;
  let chunks = 0;
  let counter = 0;
  let off = HEADER_LENGTH;

  while (off < bytes.length) {
    const remaining = bytes.length - off;
    if (remaining < 4 || isAllZeroFrom(bytes, off)) {
      truncated = true; // cut inside a length field, or a zero-filled tail from a power cut
      break;
    }
    const ctLen = new DataView(bytes.buffer, bytes.byteOffset + off, 4).getUint32(0, true);
    if (ctLen === 0 || ctLen > MAX_CT_LEN) throw new KbxError('BAD_CHUNK', `Chunk ${counter} has length ${ctLen}.`);
    if (CHUNK_OVERHEAD + ctLen > remaining) {
      truncated = true; // the last chunk was cut short; drop the partial bytes
      break;
    }

    const nonce = new Uint8Array(12);
    nonce.set(noncePrefix, 0);
    nonce.set(u64le(counter), 4);
    const aad = new Uint8Array(HEADER_LENGTH + 8);
    aad.set(headerBytes, 0);
    aad.set(u64le(counter), HEADER_LENGTH);
    const sealed = bytes.subarray(off + 16, off + 16 + ctLen + 16); // ciphertext || tag

    let plain: ArrayBuffer;
    try {
      plain = await subtle.decrypt({ name: 'AES-GCM', iv: nonce, additionalData: aad, tagLength: 128 }, key, sealed);
    } catch {
      if (counter === 0) {
        throw new KbxError('WRONG_PASSPHRASE', 'Chunk 0 failed authentication: wrong passphrase, or the file was tampered with.');
      }
      throw new KbxError('CORRUPT_CHUNK', `Chunk ${counter} failed authentication: the file is corrupted or tampered with.`);
    }

    if (counter === 0) {
      verified = true;
      if (opts.keepKey && opts.keyCache) opts.keyCache.set(header.pbkdf2SaltHex, key);
    }
    const parsed = parseRecords(new Uint8Array(plain));
    records.push(...parsed.records);
    unknownRecords += parsed.unknown;
    chunks++;
    counter++;
    off += CHUNK_OVERHEAD + ctLen;
  }

  return { header, records, truncated, verified, chunks, unknownRecords };
}

/** Converts the spec-level records into the analysis model, with times in ms. */
export function toDecodedSession(file: KbxFile): DecodedSession {
  const rate = file.header.sampleRateHz;
  const imu: ImuRecord[] = [];
  const infer: InferRecord[] = [];
  const events: EventRecord[] = [];
  let lastSeq: number | null = null;

  for (const r of file.records) {
    if (r.type === 'imu') {
      imu.push({ tMs: (r.seq * 1000) / rate, seq: r.seq, raw: Int16Array.from(r.channels), linkLoss: (r.flags & 1) !== 0 });
      lastSeq = r.seq;
    } else if (r.type === 'infer') {
      infer.push({
        tMs: (r.windowSeq * 1000) / rate,
        windowSeq: r.windowSeq,
        exercise: r.exercise,
        probs: r.probabilities,
        label: r.label,
      });
    } else {
      events.push({ seq: lastSeq, kind: r.kindName, batteryMv: r.batteryMv, payload: r.payload });
    }
  }

  let recoveryNote: string | null = null;
  if (!file.verified) {
    recoveryNote = 'No chunk could be verified, so nothing in this file is confirmed.';
  } else if (file.truncated) {
    recoveryNote = `The file ends early (for example, power was lost). ${file.chunks} complete chunk(s) recovered.`;
  }

  return {
    header: file.header,
    imu,
    infer,
    events,
    truncated: file.truncated,
    verified: file.verified,
    chunks: file.chunks,
    unknownRecords: file.unknownRecords,
    recoveryNote,
  };
}

/** Full decode used by the worker. Throws KbxError for wrong passphrase or tampering. */
export async function decryptKbx(bytes: Uint8Array, passphrase: string, opts: ReadOptions = {}): Promise<DecodedSession> {
  return toDecodedSession(await readKbx(bytes, passphrase, opts));
}
