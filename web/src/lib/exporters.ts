import { isComplete, type DecodedSession } from './types';

/** Full decoded session as JSON. Int16 channels are exported as plain arrays. */
export function sessionToJson(session: DecodedSession): string {
  return JSON.stringify(
    {
      format: 'knee-brace-journey-export',
      note: 'Decrypted local data. Research prototype. Not medical advice.',
      header: session.header,
      complete: isComplete(session),
      verified: session.verified,
      truncated: session.truncated,
      chunks: session.chunks,
      imu: session.imu.map((s) => ({ tMs: s.tMs, seq: s.seq, linkLoss: s.linkLoss, raw: Array.from(s.raw) })),
      infer: session.infer,
      events: session.events,
    },
    null,
    2,
  );
}

/** IMU samples as CSV: one row per sample, 12 raw channels. */
export function imuToCsv(session: DecodedSession): string {
  const channels = Array.from({ length: 12 }, (_, i) => `ch${i}`);
  const lines = ['t_ms,seq,link_loss,' + channels.join(',')];
  for (const s of session.imu) {
    const raw = Array.from(s.raw).join(',');
    lines.push(`${s.tMs},${s.seq},${s.linkLoss ? 1 : 0},${raw}`);
  }
  return lines.join('\n') + '\n';
}

/** Window predictions as CSV. */
export function inferToCsv(session: DecodedSession): string {
  const lines = ['t_ms,exercise,label,probs'];
  for (const w of session.infer) {
    lines.push(`${w.tMs},${w.exercise},${w.label},"${w.probs.join(';')}"`);
  }
  return lines.join('\n') + '\n';
}
