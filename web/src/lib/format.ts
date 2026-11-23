export function fmtDate(ms: number): string {
  return new Date(ms).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function fmtMinutes(seconds: number): string {
  const m = Math.round(seconds / 60);
  return `${m} min`;
}

export function fmtPct(share: number | null): string {
  return share === null ? 'n/a' : `${Math.round(share * 100)}%`;
}

export function fmtDuration(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m ${String(r).padStart(2, '0')}s`;
}
