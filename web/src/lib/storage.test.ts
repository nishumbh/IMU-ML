import { beforeEach, describe, expect, it } from 'vitest';
import { db } from './db';
import { saveSessions, type ImportResult } from './importer';
import { imuToCsv, inferToCsv, sessionToJson } from './exporters';
import type { DecodedSession } from './types';

function fakeSession(chunks: number, sessionNumber = 1): DecodedSession {
  return {
    header: {
      formatVersion: 1,
      deviceId: 'KBX-TEST-01',
      sessionNumber,
      sampleRateHz: 50,
      accelLsbMps2: 0.0012,
      gyroLsbRadps: 0.000266,
      pbkdf2SaltHex: '00'.repeat(16),
      pbkdf2Iterations: 600000,
      noncePrefixHex: 'a55ac33c',
      wallClockUnixS: 1767225600,
      firmwareVersion: 'test',
    },
    imu: [{ tMs: 0, seq: 0, raw: new Int16Array(12).fill(1), linkLoss: false }],
    infer: [{ tMs: 40, windowSeq: 2, exercise: 1, probs: [0.5, 0.3, 0.2], label: 0 }],
    events: [{ seq: 0, kind: 'start', batteryMv: 0, payload: 0 }],
    truncated: false,
    verified: true,
    chunks,
    unknownRecords: 0,
    recoveryNote: null,
  };
}

const ok = (name: string, session: DecodedSession): ImportResult => ({ name, ok: true, session });

describe('saveSessions', () => {
  beforeEach(async () => {
    await db.sessions.clear();
  });

  it('stores a session under a stable id and keeps the source name', async () => {
    const saved = await saveSessions([ok('S0001.kbx', fakeSession(4))], 1000);
    expect(saved).toBe(1);
    const row = await db.sessions.get('KBX-TEST-01:1');
    expect(row?.sourceNames).toEqual(['S0001.kbx']);
    expect(row?.dateMs).toBe(1767225600 * 1000);
  });

  it('does not replace a copy that has more verified chunks', async () => {
    await saveSessions([ok('a.kbx', fakeSession(4))]);
    const saved = await saveSessions([ok('b.kbx', fakeSession(2))]);
    expect(saved).toBe(0);
    expect((await db.sessions.get('KBX-TEST-01:1'))?.session.chunks).toBe(4);
  });

  it('skips failed results', async () => {
    const saved = await saveSessions([{ name: 'bad.kbx', ok: false, code: 'WRONG_PASSPHRASE' }]);
    expect(saved).toBe(0);
    expect(await db.sessions.count()).toBe(0);
  });
});

describe('exporters', () => {
  it('writes IMU and window CSV with the expected columns', () => {
    const s = fakeSession(4);
    expect(imuToCsv(s).split('\n')[0]).toBe('t_ms,seq,link_loss,' + Array.from({ length: 12 }, (_, i) => `ch${i}`).join(','));
    expect(inferToCsv(s).split('\n')[1]).toBe('40,1,0,"0.5;0.3;0.2"');
  });

  it('writes JSON that records completeness and the header', () => {
    const parsed = JSON.parse(sessionToJson(fakeSession(4))) as { complete: boolean; header: { deviceId: string } };
    expect(parsed.complete).toBe(true);
    expect(parsed.header.deviceId).toBe('KBX-TEST-01');
  });
});
