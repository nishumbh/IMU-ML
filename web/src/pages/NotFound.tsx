import { Link } from 'react-router-dom';

export function NotFound() {
  return (
    <section>
      <h1>Page not found</h1>
      <p>
        <Link to="/">Go to your journey</Link>
      </p>
    </section>
  );
}
