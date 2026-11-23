import { HashRouter, NavLink, Route, Routes } from 'react-router-dom';
import { SafetyBanner } from './components/SafetyBanner';
import { JourneyPage } from './pages/JourneyPage';
import { SessionsPage } from './pages/SessionsPage';
import { SessionDetailPage } from './pages/SessionDetailPage';
import { ImportPage } from './pages/ImportPage';
import { SettingsPage } from './pages/SettingsPage';
import { NotFound } from './pages/NotFound';

const links = [
  { to: '/', label: 'Your journey' },
  { to: '/sessions', label: 'Sessions' },
  { to: '/import', label: 'Import' },
  { to: '/settings', label: 'Settings' },
];

export default function App() {
  return (
    <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="site-header">
        <div className="brand">Knee Brace Journey</div>
        <nav aria-label="Main">
          <ul className="nav-list">
            {links.map((l) => (
              <li key={l.to}>
                <NavLink to={l.to} end={l.to === '/'} className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
                  {l.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <SafetyBanner />
      <main id="main" className="container" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<JourneyPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/sessions/:id" element={<SessionDetailPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer className="site-footer">
        All data stays in this browser. Nothing is uploaded. Research prototype, not medical advice.
      </footer>
    </HashRouter>
  );
}
