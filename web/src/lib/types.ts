/** Types mirroring docs/SD_LOG_FORMAT.md (KBX1). Field names follow the spec. */

export const EVENT_KIND_NAMES = ['start', 'stop', 'exercise_change', 'low_battery'] as const;
export type EventKindName = (typeof EVENT_KIND_NAMES)[number] | 'unknown';

export interface KbxHeader {
  formatVersion: number;
  deviceId: string;
  sessionNumber: number;
  sampleRateHz: number;
  /** m/s^2 per raw accelerometer LSB. */
  accelLsbMps2: number;
  /** rad/s per raw gyroscope LSB. */
  gyroLsbRadps: number;
  pbkdf2SaltHex: string;
  pbkdf2Iterations: number;
  noncePrefixHex: string;
  /** Unix seconds at session start; 0 in the file means unknown (null here). */
  wallClockUnixS: number | null;
  firmwareVersion: string;
}

/** One record as stored in the file, before any conversion. Mirrors the spec's field names. */
export type KbxRecord =
  | { type: 'imu'; seq: number; flags: number; channels: number[] }
  | { type: 'infer'; windowSeq: number; exercise: number; probabilities: number[]; label: number }
  | { type: 'event'; kind: number; kindName: EventKindName; batteryMv: number; payload: number };

/** Result of reading a .kbx file (format level). */
export interface KbxFile {
  header: KbxHeader;
  records: KbxRecord[];
  /** File ended mid-chunk or on a zero-filled tail. Earlier chunks are kept. */
  truncated: boolean;
  /** True when at least one chunk passed its tag check. A zero-chunk file is never verified. */
  verified: boolean;
  chunks: number;
  unknownRecords: number;
}

/** Analysis-friendly model used by the UI, with times in ms from session start. */
export interface ImuRecord {
  tMs: number;
  seq: number;
  /** Raw int16 counts, 12 channels (thigh ax..gz, then shank ax..gz). */
  raw: Int16Array;
  /** LINK_LOSS flag: shank channels are zero-filled and must not be used. */
  linkLoss: boolean;
}

export interface InferRecord {
  tMs: number;
  windowSeq: number;
  exercise: number;
  /** p0, p1, p2 as stored (float32 widened to number). */
  probs: number[];
  /** 0, 1, 2, or 255 = reject (no confident class). */
  label: number;
}

export interface EventRecord {
  /** Seq of the nearest preceding IMU sample, or null if none precedes. */
  seq: number | null;
  kind: EventKindName;
  batteryMv: number;
  payload: number;
}

export interface DecodedSession {
  header: KbxHeader;
  imu: ImuRecord[];
  infer: InferRecord[];
  events: EventRecord[];
  truncated: boolean;
  verified: boolean;
  chunks: number;
  unknownRecords: number;
  /** Human-readable note for partial recovery, or null. */
  recoveryNote: string | null;
}

export function isComplete(s: Pick<DecodedSession, 'truncated' | 'verified'>): boolean {
  return s.verified && !s.truncated;
}

export function sessionStartMs(h: KbxHeader): number | null {
  return h.wallClockUnixS === null ? null : h.wallClockUnixS * 1000;
}
