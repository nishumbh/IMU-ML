import { describe, expect, it } from 'vitest';
import { analyzeSession, activeSeconds, estimateKneeRange, percentile } from './analysis';
import { CONFIG } from './config';
import { computeJourney, currentStreak, dayKey, weekStartKey } from './journey';
import type { DecodedSession, ImuRecord, InferRecord, KbxHeader } from './types';

const LSB = { accel: 9.80665 / 8192, gyro: (Math.PI / 180) / 65.536 };
const G = 1 / LSB.accel; // raw counts for 1 g

const header: KbxHeader = {
  formatVersion: 1,
  deviceId: 'KBX-TEST-01',
  sessionNumber: 1,
  sampleRateHz: 50,
  accelLsbMps2: LSB.accel,
  gyroLsbRadps: LSB.gyro,
  pbkdf2SaltHex: '00'.repeat(16),
  pbkdf2Iterations: 600000,
  noncePrefixHex: 'a55ac33c',
  wallClockUnixS: Date.UTC(2026, 0, 5, 12) / 1000,
  firmwareVersion: 'test',
};

function imuAt(tMs: number, raw: number[] = [], seq = 0, linkLoss = false): ImuRecord {
  const arr = new Int16Array(12);
  raw.forEach((v, i) => (arr[i] = v));
  return { tMs, seq, raw: arr, linkLoss };
}

function session(overrides: Partial<DecodedSession> = {}): DecodedSession {
  return { header, imu: [], infer: [], events: [], truncated: false, verified: true, chunks: 1, unknownRecords: 0, recoveryNote: null, ...overrides };
}

function win(tMs: number, exercise: number, label: number): InferRecord {
  return { tMs, windowSeq: Math.round(tMs / 20), exercise, probs: [0.5, 0.25, 0.25], label };
}

describe('percentile', () => {
  it('interpolates linearly', () => {
    expect(percentile([0, 10], 50)).toBe(5);
    expect(percentile([1, 2, 3, 4, 5], 0)).toBe(1);
    expect(percentile([1, 2, 3, 4, 5], 100)).toBe(5);
  });
});

describe('activeSeconds', () => {
  it('ignores gaps longer than maxGapMs', () => {
    expect(activeSeconds([imuAt(0), imuAt(1000), imuAt(2000), imuAt(60_000)], 2000)).toBe(2);
  });
});

describe('estimateKneeRange', () => {
  it('returns null with too few samples', () => {
    expect(estimateKneeRange([imuAt(0)], LSB)).toBeNull();
  });

  it('reports a range for a swinging shank and is flagged uncalibrated', () => {
    const imu: ImuRecord[] = [];
    const fs = 50;
    for (let i = 0; i < 200; i++) {
      // Shank swings +-30 deg about the sensor Y axis; thigh stays still.
      // Physical convention: tilt = atan2(-ax, az), so ax = -G sin(a), az = G cos(a).
      const aDeg = i < 20 ? 0 : 30 * Math.sin((i - 20) / 10);
      const a = (aDeg * Math.PI) / 180;
      // Gyro about +Y is d(tilt)/dt in deg/s, as raw counts.
      const rateDegS = i < 20 ? 0 : 30 * Math.cos((i - 20) / 10) * (fs / 10);
      const raw = new Array<number>(12).fill(0);
      raw[2] = G; // thigh: gravity on az, stationary
      raw[6] = Math.round(-Math.sin(a) * G);
      raw[8] = Math.round(Math.cos(a) * G);
      raw[10] = Math.round((rateDegS * Math.PI) / 180 / LSB.gyro);
      imu.push(imuAt(i * (1000 / fs), raw, i));
    }
    const est = estimateKneeRange(imu, LSB);
    expect(est?.uncalibrated).toBe(true);
    // Flexion is thigh minus shank, so it swings +-30 deg: a 5th-95th percentile range near 57.
    expect(est?.rangeDeg ?? 0).toBeGreaterThan(50);
    expect(est?.rangeDeg ?? 0).toBeLessThan(60);
  });

  it('stays near zero for a stationary limb', () => {
    const imu: ImuRecord[] = [];
    for (let i = 0; i < 100; i++) {
      const raw = new Array<number>(12).fill(0);
      raw[2] = G;
      raw[8] = G;
      imu.push(imuAt(i * 20, raw, i));
    }
    expect(estimateKneeRange(imu, LSB)?.rangeDeg).toBeCloseTo(0, 6);
  });

  it('ignores LINK_LOSS samples', () => {
    const imu: ImuRecord[] = [];
    for (let i = 0; i < 100; i++) imu.push(imuAt(i * 20, [], i, i % 2 === 0));
    // Only 50 link-up samples remain; the threshold is 50, so this should still estimate.
    expect(estimateKneeRange(imu, LSB)).not.toBeNull();
    const fewer = imu.map((s, i) => ({ ...s, linkLoss: i > 10 }));
    expect(estimateKneeRange(fewer, LSB)).toBeNull();
  });
});

describe('analyzeSession', () => {
  it('computes correct-form share, exercises and error breakdown, excluding rejects and exercise 0', () => {
    const infer: InferRecord[] = [
      ...[0, 1, 2, 3].map((i) => win(i * 1000, 1, 0)), // 4 correct, exercise 1
      ...[4, 5].map((i) => win(i * 1000, 1, 1)), // 2 weight-transfer errors
      win(6000, 1, 255), // reject: excluded
      win(7000, 0, 0), // exercise none: excluded
      win(8000, 2, 0), // single window: not a qualifying run
    ];
    const a = analyzeSession(session({ infer }));
    expect(a.windowCount).toBe(7);
    expect(a.rejectedWindows).toBe(1);
    expect(a.correctFormShare).toBeCloseTo(5 / 7, 6);
    expect(a.exercisesDone).toBe(1);
    expect(a.perExercise.find((e) => e.exercise === 1)?.correctShare).toBeCloseTo(4 / 6, 6);
    expect(a.errorBreakdown).toEqual([
      expect.objectContaining({ exercise: 1, label: 1, name: CONFIG.classNames[1]?.[1], count: 2, share: 1 }),
    ]);
  });

  it('reports link loss and marks partial sessions', () => {
    const imu = [imuAt(0), imuAt(20, [], 1, true), imuAt(40), imuAt(60, [], 3, true)];
    const a = analyzeSession(session({ imu, truncated: true, chunks: 3 }));
    expect(a.linkLossPct).toBeCloseTo(50, 6);
    expect(a.partial).toBe(true);
  });

  it('treats a zero-chunk file as partial', () => {
    expect(analyzeSession(session({ verified: false, chunks: 0 })).partial).toBe(true);
  });

  it('returns null shares for an empty session', () => {
    const a = analyzeSession(session());
    expect(a.correctFormShare).toBeNull();
    expect(a.kneeEstimate).toBeNull();
    expect(a.durationS).toBe(0);
  });
});

describe('journey', () => {
  const now = new Date(2026, 4, 20, 15).getTime(); // local Wed 2026-05-20

  it('computes local week start (Monday)', () => {
    expect(weekStartKey(new Date(2026, 4, 20, 9).getTime())).toBe('2026-05-18');
    expect(weekStartKey(new Date(2026, 4, 17, 9).getTime())).toBe('2026-05-11');
  });

  it('counts a streak that includes today or yesterday', () => {
    const days = new Set([dayKey(new Date(2026, 4, 18, 9).getTime()), dayKey(new Date(2026, 4, 19, 9).getTime())]);
    expect(currentStreak(days, now)).toBe(2);
    expect(currentStreak(new Set([dayKey(new Date(2026, 4, 17, 9).getTime())]), now)).toBe(0);
  });

  it('aggregates sessions and milestones', () => {
    const a = analyzeSession(session({ infer: [win(0, 1, 0), win(1000, 1, 0), win(2000, 1, 0)] }));
    const j = computeJourney([{ key: 'a', dateMs: new Date(2026, 4, 19, 9).getTime(), analysis: { ...a, activeS: 600 } }], now);
    expect(j.sessionsPerWeek).toEqual([{ weekStart: '2026-05-18', count: 1 }]);
    expect(j.totalActiveMinutes).toBe(10);
    expect(j.milestones.find((m) => m.id === 'sessions-1')?.achieved).toBe(true);
    expect(j.milestones.find((m) => m.id === 'sessions-5')?.achieved).toBe(false);
  });
});
