import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import LoginPage from '@/pages/LoginPage';
import { markSessionEnded } from '@/mobile/session';

describe('LoginPage session notice (P5)', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.stubGlobal('fetch', vi.fn());
  });

  it('shows expired-session notice from sessionStorage', () => {
    markSessionEnded('expired');
    render(<LoginPage onLogin={vi.fn()} />);
    expect(screen.getByTestId('session-expired-notice')).toHaveTextContent(/session expired/i);
  });
});
