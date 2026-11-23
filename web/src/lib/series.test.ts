import { describe, expect, it } from 'vitest';
import { downsampleSeries } from './series';

describe('downsampleSeries', () => {
  it('keeps peaks of every channel and caps the output size', () => {
    const rows = Array.from({ length: 20_000 }, (_, i) => ({
      t: i / 50,
      a: i === 12_345 ? 900 : Math.sin(i / 40),
      b: i === 777 ? -800 : Math.cos(i / 40),
    }));
    const out = downsampleSeries(rows, 500);
    expect(out.length).toBeLessThanOrEqual(500 + 10);
    expect(out.some((r) => r.a === 900)).toBe(true);
    expect(out.some((r) => r.b === -800)).toBe(true);
    for (let i = 1; i < out.length; i++) expect(out[i]!.t).toBeGreaterThan(out[i - 1]!.t);
  });

  it('returns the input unchanged when it is already small', () => {
    const rows = [{ t: 0, a: 1 }, { t: 1, a: 2 }];
    expect(downsampleSeries(rows, 100)).toEqual(rows);
  });
});
