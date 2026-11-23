import { useMemo } from 'react';
import { useLiveQuery } from 'dexie-react-hooks';
import { db, type StoredSession } from '../lib/db';
import { analyzeSession, type SessionAnalysis } from '../lib/analysis';
import { computeJourney, type DatedAnalysis } from '../lib/journey';

export interface LoadedSession {
  stored: StoredSession;
  analysis: SessionAnalysis;
}

/** All stored sessions with their analysis. Recomputed when the database changes. */
export function useSessions(): LoadedSession[] | undefined {
  const rows = useLiveQuery(() => db.sessions.orderBy('dateMs').toArray(), []);
  return useMemo(
    () => rows?.map((stored) => ({ stored, analysis: analyzeSession(stored.session) })),
    [rows],
  );
}

export function useJourney(sessions: LoadedSession[] | undefined) {
  return useMemo(() => {
    if (!sessions) return undefined;
    const items: DatedAnalysis[] = sessions.map((s) => ({
      key: s.stored.id,
      dateMs: s.stored.dateMs,
      analysis: s.analysis,
    }));
    return computeJourney(items, Date.now());
  }, [sessions]);
}
