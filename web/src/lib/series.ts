/**
 * Downsamples a time series for plotting. Each bucket keeps its first sample
 * and the min and max of every numeric channel, so peaks in any channel survive.
 * Output is sorted by time and has at most about 5 points per bucket.
 */
export function downsampleSeries<T extends { t: number }>(rows: readonly T[], maxPoints: number): T[] {
  if (rows.length <= maxPoints || maxPoints < 8) return [...rows];
  const buckets = Math.max(1, Math.floor(maxPoints / 5));
  const size = rows.length / buckets;
  const keys = Object.keys(rows[0] ?? {}).filter((k) => k !== 't' && typeof (rows[0] as Record<string, unknown>)[k] === 'number');
  const keep = new Set<number>();
  for (let b = 0; b < buckets; b++) {
    const start = Math.floor(b * size);
    const end = Math.min(rows.length, Math.floor((b + 1) * size));
    if (start >= end) continue;
    keep.add(start);
    for (const k of keys) {
      let minI = start;
      let maxI = start;
      for (let i = start; i < end; i++) {
        const v = (rows[i] as Record<string, number>)[k] ?? 0;
        if (v < ((rows[minI] as Record<string, number>)[k] ?? 0)) minI = i;
        if (v > ((rows[maxI] as Record<string, number>)[k] ?? 0)) maxI = i;
      }
      keep.add(minI);
      keep.add(maxI);
    }
  }
  return [...keep].sort((a, b) => a - b).map((i) => rows[i] as T);
}
