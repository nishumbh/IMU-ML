import Dexie, { type Table } from 'dexie';
import type { DecodedSession } from './types';

/** One imported session, stored as decrypted local data. Never leaves this browser. */
export interface StoredSession {
  /** `${deviceId}:${sessionNo}`, stable across re-imports. */
  id: string;
  deviceId: string;
  sessionNo: number;
  /** Start time from the header, or the import time when the pod had no clock. */
  dateMs: number;
  importedAtMs: number;
  sourceNames: string[];
  session: DecodedSession;
}

export class JourneyDb extends Dexie {
  sessions!: Table<StoredSession, string>;

  constructor(name = 'knee-brace-journey') {
    super(name);
    this.version(1).stores({
      sessions: 'id, deviceId, sessionNo, dateMs, importedAtMs',
    });
  }
}

export const db = new JourneyDb();

export function sessionId(deviceId: string, sessionNo: number): string {
  return `${deviceId}:${sessionNo}`;
}
