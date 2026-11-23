import { CONFIG } from './config';
import type { SessionAnalysis } from './analysis';

export interface DatedAnalysis {
  key: string;
  dateMs: number;
  analysis: SessionAnalysis;
}

export interface Milestone {
  id: string;
  label: string;
  achieved: boolean;
}

export interface JourneyStats {
  sessionsPerWeek: { weekStart: string; count: number }[];
  currentStreakDays: number;
  totalActiveMinutes: number;
  correctTrend: { exercise: string; points: { date: string; share: number }[] }[];
  romTrend: { date: string; rangeDeg: number }[];
  milestones: Milestone[];
}

/** Local calendar day key, YYYY-MM-DD. */
export function dayKey(ms: number): string {
  const d = new Date(ms);
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

/** Key of the Monday that starts the local week containing ms. */
export function weekStartKey(ms: number): string {
  const d = new Date(ms);
  const offset = (d.getDay() + 6) % 7; // Monday = 0
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - offset);
  return dayKey(d.getTime());
}

/**
 * Streak: consecutive local days with at least one session, counted back from
 * today. If today has no session yet, the streak is still alive when the last
 * session was within the grace period (config.streakGraceDays).
 */
export function currentStreak(dayKeys: Set<string>, nowMs: number): number {
  const cursor = new Date(nowMs);
  cursor.setHours(12, 0, 0, 0); // noon avoids DST edges when stepping by days
  if (!dayKeys.has(dayKey(cursor.getTime()))) {
    cursor.setDate(cursor.getDate() - CONFIG.streakGraceDays);
    if (!dayKeys.has(dayKey(cursor.getTime()))) return 0;
  }
  let streak = 0;
  while (dayKeys.has(dayKey(cursor.getTime()))) {
    streak++;
    cursor.setDate(cursor.getDate() - 1);
  }
  return streak;
}

export function computeJourney(items: DatedAnalysis[], nowMs: number): JourneyStats {
  const sorted = [...items].sort((a, b) => a.dateMs - b.dateMs);

  const weekCounts = new Map<string, number>();
  for (const it of sorted) {
    const wk = weekStartKey(it.dateMs);
    weekCounts.set(wk, (weekCounts.get(wk) ?? 0) + 1);
  }
  const sessionsPerWeek = [...weekCounts.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .map(([weekStart, count]) => ({ weekStart, count }));

  const dayKeys = new Set(sorted.map((it) => dayKey(it.dateMs)));
  const currentStreakDays = currentStreak(dayKeys, nowMs);

  const totalActiveSeconds = sorted.reduce((acc, it) => acc + it.analysis.activeS, 0);
  const totalActiveMinutes = totalActiveSeconds / 60;

  // Correct-form trend per exercise name, one point per session that performed it.
  const trendMap = new Map<string, { date: string; share: number }[]>();
  for (const it of sorted) {
    for (const ex of it.analysis.perExercise) {
      if (ex.correctShare === null) continue;
      const list = trendMap.get(ex.name) ?? [];
      list.push({ date: dayKey(it.dateMs), share: ex.correctShare });
      trendMap.set(ex.name, list);
    }
  }
  const correctTrend = [...trendMap.entries()].map(([exercise, points]) => ({ exercise, points }));

  const romTrend = sorted
    .filter((it) => it.analysis.kneeEstimate !== null)
    .map((it) => ({ date: dayKey(it.dateMs), rangeDeg: it.analysis.kneeEstimate?.rangeDeg ?? 0 }));

  const milestones: Milestone[] = [];
  const sessionCount = sorted.length;
  for (const n of CONFIG.milestones.sessions) {
    milestones.push({ id: `sessions-${n}`, label: `${n} session${n === 1 ? '' : 's'}`, achieved: sessionCount >= n });
  }
  for (const n of CONFIG.milestones.streakDays) {
    milestones.push({ id: `streak-${n}`, label: `${n}-day streak`, achieved: currentStreakDays >= n });
  }
  for (const n of CONFIG.milestones.activeMinutes) {
    milestones.push({ id: `minutes-${n}`, label: `${n} active minutes`, achieved: totalActiveMinutes >= n });
  }

  return { sessionsPerWeek, currentStreakDays, totalActiveMinutes, correctTrend, romTrend, milestones };
}
