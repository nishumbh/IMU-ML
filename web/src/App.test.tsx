// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('App shell', () => {
  it('shows the research banner and the journey page with an empty database', async () => {
    window.location.hash = '#/';
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<App />);
    });
    // Live queries resolve asynchronously; give them a tick.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(host.textContent).toContain('Research prototype.');
    expect(host.textContent).toContain('Not medical advice.');
    expect(host.textContent).toContain('Do not use to guide treatment without your clinician.');
    expect(host.querySelector('h1')?.textContent).toBe('Your journey');
    await act(async () => root.unmount());
    host.remove();
  });
});
