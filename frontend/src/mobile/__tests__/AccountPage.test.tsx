import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import AccountPage from '@/mobile/pages/AccountPage';

function makeToken(expSeconds: number): string {
  const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
  const payload = btoa(JSON.stringify({ sub: 'alice', role: 'admin', exp: expSeconds }));
  return `${header}.${payload}.sig`;
}

describe('AccountPage (P5)', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
  });

  it('states push is unavailable and surfaces threat model pointer', () => {
    const nowSec = Math.floor(Date.now() / 1000);
    localStorage.setItem('nautilus_token', makeToken(nowSec + 3600));
    localStorage.setItem('nautilus_role', 'admin');
    render(<AccountPage />);
    expect(screen.getByText(/Push notifications/i)).toBeInTheDocument();
    expect(screen.getByText(/no web-push registration API/i)).toBeInTheDocument();
    expect(screen.getByText(/mobile_ops_threat_model/i)).toBeInTheDocument();
    expect(screen.getByText('alice')).toBeInTheDocument();
  });

  it('warns when session is expiring soon', () => {
    const nowSec = Math.floor(Date.now() / 1000);
    localStorage.setItem('nautilus_token', makeToken(nowSec + 120));
    render(<AccountPage />);
    expect(screen.getByTestId('account-expiry-warning')).toBeInTheDocument();
  });

  it('signs out via logout endpoint and clears session', async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    localStorage.setItem('nautilus_token', makeToken(nowSec + 3600));
    const spy = vi.fn();
    window.addEventListener('nautilus:unauthorized', spy);
    render(<AccountPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => {
      expect(spy).toHaveBeenCalled();
      expect(localStorage.getItem('nautilus_token')).toBeNull();
      expect(sessionStorage.getItem('nautilus_mops_session_reason')).toBe('signed_out');
    });
    window.removeEventListener('nautilus:unauthorized', spy);
  });
});
