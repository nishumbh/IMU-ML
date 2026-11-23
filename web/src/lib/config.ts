/**
 * Every tunable constant used by the analysis lives here so it can change
 * without touching logic. Class and exercise ids follow docs/SD_LOG_FORMAT.md section 7.2.
 */
export const CONFIG = {
  /** Exercise id 0 means "none" and is excluded from exercise metrics. */
  exercises: {
    1: 'Squat',
    2: 'Seated leg extension',
    3: 'Gait',
  } as Record<number, string>,
  /**
   * Class names per exercise: index 0 is the correct class, 1 and 2 are error classes.
   * Research categories only, not clinical results.
   */
  classNames: {
    1: ['Correct form', 'Weight transfer to healthy leg', 'Injured leg placed in front'],
    2: ['Correct form', 'Incomplete extension', 'Limb lift from chair'],
    3: ['Correct form', 'Incomplete knee extension', 'Hip abduction with extended knee'],
  } as Record<number, readonly string[]>,
  /** IMU channel indices (raw record order, section 7.1). Each IMU: ax, ay, az, gx, gy, gz. */
  channels: {
    thigh: { ax: 0, ay: 1, az: 2, gx: 3, gy: 4, gz: 5 },
    shank: { ax: 6, ay: 7, az: 8, gx: 9, gy: 10, gz: 11 },
  },
  /** Class index of the correct form. */
  correctClass: 0,
  /** Label value meaning "no confident class". Excluded from shares. */
  rejectLabel: 255,
  /** Complementary filter: fraction of the gyro-integrated angle kept each step. */
  filter: {
    alpha: 0.98,
    /**
     * Sign applied to the gyro rate before integration. +1 matches the accelerometer
     * tilt atan2(-ax, az) used in analysis.ts (rotation about the sensor +Y axis).
     * Verified by a kinematic simulation in tests/test_knee_angle.py. Still to confirm
     * on real sensors: the bending axis must be the sensor Y axis.
     */
    gyroSign: 1,
  },
  /** Knee-angle estimate (uncalibrated). */
  estimate: {
    /** Flexion = flexionSign * (thigh - shank). Use -1 if flexion shows the wrong way. */
    flexionSign: 1,
    /** Standing reference window at the start, used as the zero. */
    standingSeconds: 2,
    /** Robust percentiles used for the reported range. */
    lowPercentile: 5,
    highPercentile: 95,
    /** Minimum IMU samples (with link up) before an estimate is reported. */
    minSamples: 50,
  },
  /** A run of same-exercise windows counts as an exercise done only if this long. */
  minRunWindows: 3,
  /** Gaps between IMU samples longer than this (ms) are not counted as active time. */
  maxGapMs: 2000,
  /** Days without a session that still keep a streak alive (1 = yesterday counts). */
  streakGraceDays: 1,
  /** Milestone thresholds. */
  milestones: {
    sessions: [1, 5, 10, 25, 50],
    streakDays: [3, 7, 14],
    activeMinutes: [30, 120, 300],
  },
  /** Maximum points per plotted series on the session detail page. */
  maxPlotPoints: 1500,
} as const;
