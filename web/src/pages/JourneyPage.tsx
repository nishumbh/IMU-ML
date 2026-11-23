import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { StatTile } from '../components/StatTile';
import { useJourney, useSessions } from '../hooks/useSessions';
import { SERIES_COLORS, AXIS_COLOR } from '../lib/palette';
import { fmtPct } from '../lib/format';
import { Link } from 'react-router-dom';

export function JourneyPage() {
  const sessions = useSessions();
  const journey = useJourney(sessions);

  if (!journey || !sessions) return <p>Loading your journey...</p>;
  if (sessions.length === 0) {
    return (
      <section>
        <h1>Your journey</h1>
        <p>
          No sessions yet. <Link to="/import">Import files from your SD card</Link> to start. Everything is decrypted and kept in this browser.
        </p>
      </section>
    );
  }

  const thisWeek = journey.sessionsPerWeek[journey.sessionsPerWeek.length - 1];
  const exerciseNames = journey.correctTrend.map((t) => t.exercise);
  // Merge the per-exercise points into one row per date for a single chart.
  const byDate = new Map<string, Record<string, number | string>>();
  for (const t of journey.correctTrend) {
    for (const p of t.points) {
      const row = byDate.get(p.date) ?? { date: p.date };
      row[t.exercise] = Math.round(p.share * 100);
      byDate.set(p.date, row);
    }
  }
  const correctRows = [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const achieved = journey.milestones.filter((m) => m.achieved);

  return (
    <section aria-labelledby="journey-heading">
      <h1 id="journey-heading">Your journey</h1>

      <div className="tiles">
        <StatTile label="Sessions" value={String(sessions.length)} />
        <StatTile label="Current streak" value={`${journey.currentStreakDays} day${journey.currentStreakDays === 1 ? '' : 's'}`} />
        <StatTile label="Active minutes" value={String(Math.round(journey.totalActiveMinutes))} />
        <StatTile label="Sessions this week" value={String(thisWeek?.count ?? 0)} />
      </div>

      <figure className="card">
        <figcaption>Sessions per week</figcaption>
        <div className="chart" role="img" aria-label={`Sessions per week, ${journey.sessionsPerWeek.map((w) => `${w.weekStart}: ${w.count}`).join('; ') || 'none'}`}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={journey.sessionsPerWeek} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#8884" />
              <XAxis dataKey="weekStart" tick={{ fill: AXIS_COLOR, fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fill: AXIS_COLOR, fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="count" name="Sessions" fill={SERIES_COLORS[0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </figure>

      <figure className="card">
        <figcaption>Correct-form share per exercise (%)</figcaption>
        {correctRows.length === 0 ? (
          <p className="muted">No prediction windows yet.</p>
        ) : (
          <div className="chart" role="img" aria-label={`Correct-form share by session for ${exerciseNames.join(', ')}`}>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={correctRows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#8884" />
                <XAxis dataKey="date" tick={{ fill: AXIS_COLOR, fontSize: 12 }} />
                <YAxis domain={[0, 100]} tick={{ fill: AXIS_COLOR, fontSize: 12 }} />
                <Tooltip />
                <Legend />
                {exerciseNames.map((name, i) => (
                  <Line key={name} type="monotone" dataKey={name} name={name} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} connectNulls dot />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </figure>

      <figure className="card">
        <figcaption>
          Estimated range of motion (degrees) <span className="badge warn">Uncalibrated estimate</span>
        </figcaption>
        <p className="muted small">
          Estimated from the thigh and shank tilt sensors. It has no calibration to your knee, so only the change over time is meaningful. Not a measured angle.
        </p>
        {journey.romTrend.length === 0 ? (
          <p className="muted">No range estimates yet.</p>
        ) : (
          <div className="chart" role="img" aria-label={`Estimated range per session, ${journey.romTrend.map((r) => `${r.date}: ${Math.round(r.rangeDeg)} degrees`).join('; ')}`}>
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={journey.romTrend} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#8884" />
                <XAxis dataKey="date" tick={{ fill: AXIS_COLOR, fontSize: 12 }} />
                <YAxis tick={{ fill: AXIS_COLOR, fontSize: 12 }} />
                <Tooltip formatter={(v) => `${Math.round(Number(v))} deg (estimate)`} />
                <Line type="monotone" dataKey="rangeDeg" name="Estimated range (deg)" stroke={SERIES_COLORS[1]} dot />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </figure>

      <section className="card" aria-labelledby="milestones-heading">
        <h2 id="milestones-heading">Milestones</h2>
        <p>
          {achieved.length} of {journey.milestones.length} reached.
        </p>
        <ul className="milestones">
          {journey.milestones.map((m) => (
            <li key={m.id} className={m.achieved ? 'done' : 'todo'}>
              <span aria-hidden="true">{m.achieved ? '✓' : '○'}</span>{' '}
              <span>{m.label}</span>
              <span className="visually-hidden">{m.achieved ? ' (reached)' : ' (not yet)'}</span>
            </li>
          ))}
        </ul>
        <p className="muted small">Overall correct-form share across all sessions: {fmtPct(averageShare(sessions))}.</p>
      </section>
    </section>
  );
}

function averageShare(sessions: { analysis: { correctFormShare: number | null } }[]): number | null {
  const vals = sessions.map((s) => s.analysis.correctFormShare).filter((v): v is number => v !== null);
  if (vals.length === 0) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}
