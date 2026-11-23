import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { decryptKbx, parseHeader, readKbx, toDecodedSession } from './kbx';
import { KbxError } from './errors';
import { isComplete } from './types';

const here = dirname(fileURLToPath(import.meta.url));
const fixtureDir = join(here, '..', '__fixtures__');
const load = (name: string) => new Uint8Array(readFileSync(join(fixtureDir, name)));
const expected = JSON.parse(readFileSync(join(fixtureDir, 'expected.json'), 'utf8')) as {
  passphrase: string;
  valid: {
    header: Record<string, unknown>;
    records: Record<string, unknown>[];
    verified: boolean;
    truncated: boolean;
    unknown_records: number;
    link_loss_samples: number;
  };
  truncated: { chunks: number; record_count: number; truncated: boolean; verified: boolean; last_record: { seq: number; channels: number[] } };
  tampered: { chunk: number; error: string };
};
const PASS = expected.passphrase;

describe('KBX1 reader against public fixtures', () => {
  it('parses the valid header exactly as expected.json', () => {
    const h = parseHeader(load('valid.kbx'));
    expect(h).toEqual({
      formatVersion: 1,
      deviceId: expected.valid.header.device_id,
      sessionNumber: 1,
      sampleRateHz: 50,
      accelLsbMps2: expected.valid.header.accel_lsb_mps2,
      gyroLsbRadps: expected.valid.header.gyro_lsb_radps,
      pbkdf2SaltHex: '000102030405060708090a0b0c0d0e0f',
      pbkdf2Iterations: 600000,
      noncePrefixHex: 'a55ac33c',
      wallClockUnixS: 1767225600,
      firmwareVersion: 'fixture 1.0.0',
    });
  });

  it('decrypts valid.kbx to the expected records, counts and flags', async () => {
    const file = await readKbx(load('valid.kbx'), PASS);
    expect(file.verified).toBe(expected.valid.verified);
    expect(file.truncated).toBe(expected.valid.truncated);
    expect(file.chunks).toBe(4);
    expect(file.unknownRecords).toBe(expected.valid.unknown_records);
    expect(file.records.length).toBe(expected.valid.records.length);

    const imu = file.records.filter((r) => r.type === 'imu');
    expect(imu.filter((r) => r.type === 'imu' && (r.flags & 1) === 1).length).toBe(expected.valid.link_loss_samples);

    // Compare every record with expected.json, field by field.
    file.records.forEach((r, i) => {
      const e = expected.valid.records[i]!;
      if (r.type === 'imu') {
        expect(e.type).toBe('imu');
        expect(r.seq).toBe(e.seq);
        expect(r.flags).toBe(e.flags);
        expect(r.channels).toEqual(e.channels);
      } else if (r.type === 'infer') {
        expect(e.type).toBe('infer');
        expect(r.windowSeq).toBe(e.window_seq);
        expect(r.exercise).toBe(e.exercise);
        expect(r.label).toBe(e.label);
        expect(r.probabilities).toEqual(e.probabilities);
      } else {
        expect(e.type).toBe('event');
        expect(r.kind).toBe(e.kind);
        expect(r.kindName).toBe(e.kind_name);
        expect(r.batteryMv).toBe(e.battery_mv);
        expect(r.payload).toBe(e.payload);
      }
    });
  });

  it('flags the truncated fixture as partial and keeps the complete chunks', async () => {
    const file = await readKbx(load('truncated.kbx'), PASS);
    expect(file.truncated).toBe(expected.truncated.truncated);
    expect(file.verified).toBe(expected.truncated.verified);
    expect(file.chunks).toBe(expected.truncated.chunks);
    expect(file.records.length).toBe(expected.truncated.record_count);
    const last = file.records[file.records.length - 1];
    expect(last).toMatchObject({ type: 'imu', seq: expected.truncated.last_record.seq });
    expect(last && last.type === 'imu' ? Array.from(last.channels) : null).toEqual(expected.truncated.last_record.channels);

    const session = toDecodedSession(file);
    expect(isComplete(session)).toBe(false);
    expect(session.recoveryNote).toContain('ends early');
  });

  it('fails the tag check on the tampered fixture at chunk 1', async () => {
    const err = await readKbx(load('tampered.kbx'), PASS).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(KbxError);
    expect((err as KbxError).code).toBe('CORRUPT_CHUNK');
    expect((err as KbxError).message).toContain(`Chunk ${expected.tampered.chunk} failed authentication`);
  });

  it('rejects a wrong passphrase at chunk 0', async () => {
    const err = await decryptKbx(load('valid.kbx'), 'correct horse battery stapl3').then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(KbxError);
    expect((err as KbxError).code).toBe('WRONG_PASSPHRASE');
  });

  it('requires a passphrase when none is given and nothing is cached', async () => {
    await expect(decryptKbx(load('valid.kbx'), '')).rejects.toMatchObject({ code: 'PASSPHRASE_REQUIRED' });
  });

  it('reuses a key held in memory when the passphrase is empty', async () => {
    const keyCache = new Map<string, CryptoKey>();
    await readKbx(load('valid.kbx'), PASS, { keyCache, keepKey: true });
    expect(keyCache.size).toBe(1);
    const file = await readKbx(load('valid.kbx'), '', { keyCache });
    expect(file.verified).toBe(true);
  });

  it('does not cache a key after a wrong passphrase', async () => {
    const keyCache = new Map<string, CryptoKey>();
    await expect(readKbx(load('valid.kbx'), 'wrong passphrase value', { keyCache, keepKey: true })).rejects.toBeInstanceOf(KbxError);
    expect(keyCache.size).toBe(0);
  });

  it('rejects a file that is not KBX1', async () => {
    const bytes = load('valid.kbx');
    bytes[0] = 0x58 ^ 0xff;
    await expect(decryptKbx(bytes, PASS)).rejects.toMatchObject({ code: 'NOT_KBX' });
  });

  it('rejects an unsupported format version without a best-effort parse', async () => {
    const bytes = load('valid.kbx');
    bytes[4] = 2;
    let caught: unknown = null;
    try {
      parseHeader(bytes);
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(KbxError);
    expect((caught as KbxError).code).toBe('UNSUPPORTED_VERSION');
  });

  it('never reports a header-only file as verified', async () => {
    const headerOnly = load('valid.kbx').subarray(0, 96);
    const file = await readKbx(headerOnly, PASS);
    expect(file.verified).toBe(false);
    expect(file.records).toEqual([]);
    const session = toDecodedSession(file);
    expect(isComplete(session)).toBe(false);
    expect(session.recoveryNote).toContain('No chunk could be verified');
  });

  it('reports a file cut inside the first chunk length field as truncated and unverified', async () => {
    const cut = load('valid.kbx').subarray(0, 96 + 2);
    const file = await readKbx(cut, PASS);
    expect(file.truncated).toBe(true);
    expect(file.verified).toBe(false);
  });
});

