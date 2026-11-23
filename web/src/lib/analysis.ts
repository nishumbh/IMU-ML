import { CONFIG } from './config';
import type { DecodedSession, ImuRecord, KbxHeader } from './types';

export interface KneeEstimate {
  /** Robust low and high percentiles of the estimated flexion, in degrees. */
  lowDeg: number;
  highDeg: number;
  rangeDeg: number;
  /** Always true: the estimate has no per-user calibration. */
  uncalibrated: true;
}

export interface ExerciseSummary {
  exercise: number;
  name: string;
  /** Runs of at least config.minRunWindows consecutive windows. */
  runs: number;
  /** Classified windows (rejects excluded). */
  windows: number;
  correctShare: number | null;
}

export interface ErrorCount {
  exercise: number;
  label: number;
  name: string;
  count: number;
  /** Share of all error windows in the session. */
  share: number;
}

export interface SessionAnalysis {
  sessionNumber: number;
  deviceId: string;
  wallClockUnixS: number | null;
  durationS: number;
  activeS: number;
  linkLossPct: number;
  /** Classified windows for exercises 1-3 (rejects and exercise 0 excluded). */
  windowCount: number;
  rejectedWindows: number;
  /** Share of classified windows labelled correct form; null when there are none. */
  correctFormShare: number | null;
  exercisesDone: number;
  perExercise: ExerciseSummary[];
  errorBreakdown: ErrorCount[];
  kneeEstimate: KneeEstimate | null;
  partial: boolean;
}

export interface Lsb {
  accel: number;
  gyro: number;
}

export function lsbFromHeader(h: Pick<KbxHeader, 'accelLsbMps2' | 'gyroLsbRadps'>): Lsb {
  return { accel: h.accelLsbMps2, gyro: h.gyroLsbRadps };
}

/**
 * Sagittal tilt from the accelerometer, in degrees: atan2(-ax, az).
 * Same convention as knee_rehab.knee_angle, where the gyro rate about +Y equals the
 * derivative of this tilt (verified by a kinematic simulation in the Python tests).
 * Scale-free, so units do not matter.
 */
function accelPitchDeg(ax: number, az: number): number {
  return (Math.atan2(-ax, az) * 180) / Math.PI;
}

interface AxisIndex {
  ax: number;
  ay: number;
  az: number;
  gy: number;
}

/**
 * Pitch of one segment via a complementary filter: gyro integration for the short
 * term, accelerometer tilt for the long term. Returns one angle per input sample.
 * A gap longer than maxGapMs, or a non-increasing time, restarts from the accelerometer.
 */
export function complementaryPitch(
  samples: readonly ImuRecord[],
  axes: AxisIndex,
  lsb: Lsb,
  alpha: number = CONFIG.filter.alpha,
  maxGapMs: number = CONFIG.maxGapMs,
): number[] {
  const out: number[] = [];
  const radToDeg = 180 / Math.PI;
  let pitch = 0;
  let prevT = Number.NaN;
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    if (!s) continue;
    const accP = accelPitchDeg(s.raw[axes.ax] ?? 0, s.raw[axes.az] ?? 0);
    const dtMs = s.tMs - prevT;
    if (!Number.isFinite(dtMs) || dtMs <= 0 || dtMs > maxGapMs) {
      pitch = accP;
    } else {
      const gyroDegPerS = (s.raw[axes.gy] ?? 0) * lsb.gyro * radToDeg * CONFIG.filter.gyroSign;
      pitch = alpha * (pitch + gyroDegPerS * (dtMs / 1000)) + (1 - alpha) * accP;
    }
    out.push(pitch);
    prevT = s.tMs;
  }
  return out;
}

/** Linear-interpolated percentile of a numeric array (p in 0..100). */
export function percentile(values: readonly number[], p: number): number {
  if (values.length === 0) return Number.NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  const a = sorted[lo] ?? 0;
  const b = sorted[hi] ?? a;
  return a + (b - a) * (rank - lo);
}

/** Filtered sagittal pitch for the thigh and shank, one value per IMU sample (degrees). */
export function segmentPitches(imu: readonly ImuRecord[], lsb: Lsb): { thigh: number[]; shank: number[] } {
  const seg = (c: { ax: number; ay: number; az: number; gy: number }) => complementaryPitch(imu, c, lsb);
  return { thigh: seg(CONFIG.channels.thigh), shank: seg(CONFIG.channels.shank) };
}

/**
 * ESTIMATED knee flexion: signed thigh-minus-shank pitch, zeroed on the first
 * CONFIG.estimate.standingSeconds (assumed standing straight). Not validated against a
 * goniometer, so it stays flagged uncalibrated. Samples flagged LINK_LOSS are dropped
 * because the shank channels are zero-filled there.
 * Mirrors knee_rehab.knee_angle.estimate_knee_angle.
 */
export function estimateKneeRange(imu: readonly ImuRecord[], lsb: Lsb): KneeEstimate | null {
  const linkUp = imu.filter((s) => !s.linkLoss);
  if (linkUp.length < CONFIG.estimate.minSamples) return null;
  const { thigh, shank } = segmentPitches(linkUp, lsb);
  const flexRaw = thigh.map((t, i) => CONFIG.estimate.flexionSign * (t - (shank[i] ?? t)));
  const t0 = linkUp[0]?.tMs ?? 0;
  const standing = flexRaw.filter((_, i) => ((linkUp[i]?.tMs ?? t0) - t0) / 1000 <= CONFIG.estimate.standingSeconds);
  const zero = standing.length >= 2 ? percentile(standing, 50) : (flexRaw[0] ?? 0);
  const flex = flexRaw.map((v) => v - zero);
  const lowDeg = percentile(flex, CONFIG.estimate.lowPercentile);
  const highDeg = percentile(flex, CONFIG.estimate.highPercentile);
  if (!Number.isFinite(lowDeg) || !Number.isFinite(highDeg)) return null;
  return { lowDeg, highDeg, rangeDeg: highDeg - lowDeg, uncalibrated: true };
}

/** Active time: sum of IMU sample intervals no longer than maxGapMs, in seconds. */
export function activeSeconds(imu: readonly ImuRecord[], maxGapMs: number = CONFIG.maxGapMs): number {
  let ms = 0;
  for (let i = 1; i < imu.length; i++) {
    const dt = (imu[i]?.tMs ?? 0) - (imu[i - 1]?.tMs ?? 0);
    if (dt > 0 && dt <= maxGapMs) ms += dt;
  }
  return ms / 1000;
}

export function analyzeSession(session: DecodedSession): SessionAnalysis {
  const { imu, infer, header } = session;
  const first = imu[0]?.tMs ?? infer[0]?.tMs ?? 0;
  const last = imu[imu.length - 1]?.tMs ?? infer[infer.length - 1]?.tMs ?? first;
  const linkLost = imu.filter((s) => s.linkLoss).length;

  const windows = [...infer].sort((a, b) => a.tMs - b.tMs);
  const exerciseWindows = windows.filter((w) => w.exercise !== 0);
  const rejected = exerciseWindows.filter((w) => w.label === CONFIG.rejectLabel).length;
  const classified = exerciseWindows.filter((w) => w.label !== CONFIG.rejectLabel);
  const correctCount = classified.filter((w) => w.label === CONFIG.correctClass).length;

  // Runs: consecutive classified windows with the same exercise id.
  const runs: { exercise: number; length: number }[] = [];
  for (const w of classified) {
    const prevRun = runs[runs.length - 1];
    if (prevRun && prevRun.exercise === w.exercise) prevRun.length += 1;
    else runs.push({ exercise: w.exercise, length: 1 });
  }
  const qualifying = runs.filter((r) => r.length >= CONFIG.minRunWindows);

  const exerciseIds = [...new Set(classified.map((w) => w.exercise))].sort((a, b) => a - b);
  const perExercise: ExerciseSummary[] = exerciseIds.map((ex) => {
    const mine = classified.filter((w) => w.exercise === ex);
    const ok = mine.filter((w) => w.label === CONFIG.correctClass).length;
    return {
      exercise: ex,
      name: CONFIG.exercises[ex] ?? `Exercise ${ex}`,
      runs: qualifying.filter((r) => r.exercise === ex).length,
      windows: mine.length,
      correctShare: mine.length ? ok / mine.length : null,
    };
  });

  const errorTotals = new Map<string, { exercise: number; label: number; count: number }>();
  for (const w of classified) {
    if (w.label === CONFIG.correctClass) continue;
    const key = `${w.exercise}:${w.label}`;
    const entry = errorTotals.get(key) ?? { exercise: w.exercise, label: w.label, count: 0 };
    entry.count += 1;
    errorTotals.set(key, entry);
  }
  const errorTotal = [...errorTotals.values()].reduce((a, e) => a + e.count, 0);
  const errorBreakdown: ErrorCount[] = [...errorTotals.values()]
    .sort((a, b) => b.count - a.count)
    .map((e) => ({
      exercise: e.exercise,
      label: e.label,
      name: CONFIG.classNames[e.exercise]?.[e.label] ?? `Class ${e.label}`,
      count: e.count,
      share: errorTotal ? e.count / errorTotal : 0,
    }));

  return {
    sessionNumber: header.sessionNumber,
    deviceId: header.deviceId,
    wallClockUnixS: header.wallClockUnixS,
    durationS: Math.max(0, (last - first) / 1000),
    activeS: activeSeconds(imu),
    linkLossPct: imu.length ? (linkLost / imu.length) * 100 : 0,
    windowCount: classified.length,
    rejectedWindows: rejected,
    correctFormShare: classified.length ? correctCount / classified.length : null,
    exercisesDone: qualifying.length,
    perExercise,
    errorBreakdown,
    kneeEstimate: estimateKneeRange(imu, lsbFromHeader(header)),
    partial: !(session.verified && !session.truncated),
  };
}
