import { Link } from 'react-router-dom';
import { useSessions } from '../hooks/useSessions';
import { fmtDate, fmtDuration, fmtPct } from '../lib/format';

export function SessionsPage() {
  const sessions = useSessions();

  if (!sessions) return <p>Loading...</p>;
  if (sessions.length === 0) {
    return (
      <section>
        <h1>Sessions</h1>
        <p>No sessions yet. <Link to="/import">Import your SD card files</Link> to see them here.</p>
      </section>
    );
  }

  const ordered = [...sessions].reverse();
  return (
    <section>
      <h1>Sessions</h1>
      <div className="table-wrap">
        <table>
          <caption className="visually-hidden">Imported sessions, newest first</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Pod</th>
              <th scope="col">Duration</th>
              <th scope="col">Active</th>
              <th scope="col">Correct form</th>
              <th scope="col">Recovery</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map(({ stored, analysis }) => (
              <tr key={stored.id}>
                <td>
                  <Link to={`/sessions/${encodeURIComponent(stored.id)}`}>{fmtDate(stored.dateMs)}</Link>
                </td>
                <td>{stored.deviceId} #{stored.sessionNo}</td>
                <td>{fmtDuration(analysis.durationS)}</td>
                <td>{fmtDuration(analysis.activeS)}</td>
                <td>{fmtPct(analysis.correctFormShare)}</td>
                <td>{analysis.partial ? <span className="badge warn">Partial</span> : <span className="badge">Complete</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Correct form is the share of prediction windows labelled correct by the research model. It is not a clinical score.
      </p>
    </section>
  );
}
