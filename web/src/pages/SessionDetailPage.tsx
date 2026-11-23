import { useLiveQuery } from 'dexie-react-hooks';
import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts';
import { analyzeSession, lsbFromHeader, segmentPitches } from '../lib/analysis';
import { isComplete, type DecodedSession } from '../lib/types';
import { db } from '../lib/db';
import { CONFIG } from '../lib/config';

import { downsampleSeries } from '../lib/series';
import { SERIES_COLORS, AXIS_COLOR } from '../lib/palette';
import { fmtDate, fmtDuration, fmtPct } from '../lib/format';
import { downloadText } from '../lib/download';
import { imuToCsv, inferToCsv, sessionToJson } from '../lib/exporters';

export function SessionDetailPage() {
  const { id: rawId } = useParams();
  const id = rawId ? decodeURIComponent(rawId) : '';
  const stored = useLiveQuery(() => db.sessions.get(id), [id]);
  const model = useMemo(() => (stored ? buildModel(stored.session) : null), [stored]);

  if (stored === undefined) return <p>Loading session...</p>;
  if (stored === null || !model) {
    return (
      <section>
        <h1>Session not found</h1>
        <p><Link to="/sessions">Back to sessions</Link></p>
      </section>
    );
  }

  const { analysis, session } = model;
  const partial = !isComplete(session);
  const errorTotal = analysis.errorBreakdown.reduce((a, e) => a + e.count, 0);

  return (
    <section aria-labelledby="detail-heading">
      <p><Link to="/sessions">&larr; All sessions</Link></p>
      <h1 id="detail-heading">Session {session.header.sessionNumber} on {session.header.deviceId}</h1>
      <p className="muted">{fmtDate(stored.dateMs)} &middot; firmware {session.header.firmwareVersion}</p>

      {partial ? (
        <div className="notice warn" role="alert">
          <strong>Partially recovered.</strong> {session.recoveryNote ?? 'The file was cut short.'} Showing the {session.chunks} verified chunk(s).
        </div>
      ) : null}

      <div className="tiles">
        <div className="stat-tile"><div className="stat-label">Duration</div><div className="stat-value">{fmtDuration(analysis.durationS)}</div></div>
        <div className="stat-tile"><div className="stat-label">Active</div><div className="stat-value">{fmtDuration(analysis.activeS)}</div></div>
        <div className="stat-tile"><div className="stat-label">Correct form</div><div className="stat-value">{fmtPct(analysis.correctFormShare)}</div></div>
        <div className="stat-tile"><div className="stat-label">Link loss</div><div className="stat-value">{analysis.linkLossPct.toFixed(1)}%</div></div>
        <div className="stat-tile"><div className="stat-label">Exercises done</div><div className="stat-value">{analysis.exercisesDone}</div></div>
      </div>

      <figure className="card">
        <figcaption>Thigh and shank tilt (degrees, filtered; downsampled for display)</figcaption>
        {model.signal.length === 0 ? (
          <p className="muted">No IMU samples.</p>
        ) : (
          <div className="chart" role="img" aria-label={`Tilt of thigh and shank over ${model.signal.length} plotted points`}>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={model.signal} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#8884" />
                <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} tickFormatter={(v: number) => `${Math.round(v)}s`} tick={{ fill: AXIS_COLOR, fontSize: 12 }} />
                <YAxis tick={{ fill: AXIS_COLOR, fontSize: 12 }} />
                <Tooltip />
                <Line type="linear" dataKey="thigh" name="Thigh" stroke={SERIES_COLORS[0]} dot={false} isAnimationActive={false} />
                <Line type="linear" dataKey="shank" name="Shank" stroke={SERIES_COLORS[1]} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </figure>

      <figure className="card">
        <figcaption>Window predictions over time</figcaption>
        {model.timeline.length === 0 ? (
          <p className="muted">No prediction windows in this session.</p>
        ) : (
          <div className="chart" role="img" aria-label={`${model.timeline.length} prediction windows over time`}>
            <ResponsiveContainer width="100%" height={220}>
              <ScatterChart margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#8884" />
                <XAxis dataKey="t" type="number" name="Time" tickFormatter={(v: number) => `${Math.round(v)}s`} tick={{ fill: AXIS_COLOR, fontSize: 12 }} />
                <YAxis dataKey="label" type="number" name="Class" allowDecimals={false} tick={{ fill: AXIS_COLOR, fontSize: 12 }} />
                <Tooltip />
                <Scatter data={model.timeline} fill={SERIES_COLORS[2]} isAnimationActive={false} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        )}
        <p className="muted small">Class ids are listed in the error breakdown. Class {CONFIG.correctClass} is correct form; label 255 (reject) is not plotted.</p>
      </figure>

      <section className="card" aria-labelledby="errors-heading">
        <h2 id="errors-heading">Error breakdown</h2>
        {analysis.errorBreakdown.length === 0 ? (
          <p>No errors predicted in this session.</p>
        ) : (
          <table>
            <thead>
              <tr><th scope="col">Error type</th><th scope="col">Windows</th><th scope="col">Share of errors</th></tr>
            </thead>
            <tbody>
              {analysis.errorBreakdown.map((e) => (
                <tr key={e.label}>
                  <td>{e.name}</td>
                  <td>{e.count}</td>
                  <td>{errorTotal ? Math.round(e.share * 100) : 0}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card" aria-labelledby="estimate-heading">
        <h2 id="estimate-heading">Estimated knee range</h2>
        {analysis.kneeEstimate ? (
          <p>
            About {Math.round(analysis.kneeEstimate.lowDeg)} to {Math.round(analysis.kneeEstimate.highDeg)} degrees (range {Math.round(analysis.kneeEstimate.rangeDeg)}).{' '}
            <span className="badge warn">Uncalibrated estimate</span>
          </p>
        ) : (
          <p className="muted">Not enough IMU samples to estimate.</p>
        )}
      </section>

      <section className="card" aria-labelledby="export-heading">
        <h2 id="export-heading">Export this session</h2>
        <div className="field-row">
          <button type="button" className="button secondary" onClick={() => downloadText(`${stored.id.replace(/[^\w.-]/g, '_')}.json`, sessionToJson(session), 'application/json')}>
            Download JSON
          </button>
          <button type="button" className="button secondary" onClick={() => downloadText(`${stored.id.replace(/[^\w.-]/g, '_')}_imu.csv`, imuToCsv(session), 'text/csv')}>
            Download IMU CSV
          </button>
          <button type="button" className="button secondary" onClick={() => downloadText(`${stored.id.replace(/[^\w.-]/g, '_')}_windows.csv`, inferToCsv(session), 'text/csv')}>
            Download windows CSV
          </button>
        </div>
      </section>
    </section>
  );
}

function buildModel(session: DecodedSession) {
  const analysis = analyzeSession(session);
  const pitches = segmentPitches(session.imu, lsbFromHeader(session.header));
  const rows = session.imu.map((s, i) => ({
    t: s.tMs / 1000,
    thigh: pitches.thigh[i] ?? 0,
    shank: pitches.shank[i] ?? 0,
  }));
  const signal = downsampleSeries(rows, CONFIG.maxPlotPoints);
  const timeline = session.infer
    .filter((w) => w.label !== CONFIG.rejectLabel)
    .map((w) => ({ t: w.tMs / 1000, label: w.label }));
  return { analysis, session, signal, timeline };
}
