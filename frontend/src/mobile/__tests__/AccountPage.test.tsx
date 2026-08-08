import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import AccountPage from '@/mobile/pages/AccountPage';

const fetchPushStatus = vi.fn();
const enablePushNotifications = vi.fn();
const disablePushNotifications = vi.fn();
const pushSupported = vi.fn(() => true);

vi.mock('@/services/pushService', () => ({
  fetchPushStatus: (...args: unknown[]) => fetchPushStatus(...args),
  enablePushNotifications: (...args: unknown[]) => enablePushNotifications(...args),
  disablePushNotifications: (...args: unknown[]) => disablePushNotifications(...args),
  pushSupported: () => pushSupported(),
}));

function makeToken(expSeconds: number): string {
  const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
  const payload = btoa(JSON.stringify({ sub: 'alice', role: 'admin', exp: expSeconds }));
  return `${header}.${payload}.sig`;
}

describe('AccountPage (P5 + push opt-in)', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
    fetchPushStatus.mockReset();
    enablePushNotifications.mockReset();
    disablePushNotifications.mockReset();
    pushSupported.mockReturnValue(true);
    fetchPushStatus.mockResolvedValue({
      available: true,
      reason: null,
      subscribed: false,
      subscription_count: 0,
    });
  });

  it('loads push status and offers enable when not subscribed', async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    localStorage.setItem('nautilus_token', makeToken(nowSec + 3600));
    localStorage.setItem('nautilus_role', 'admin');
    render(<AccountPage />);
    expect(screen.getByTestId('account-push-panel')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('account-push-status')).toHaveTextContent(/Not enabled/i);
    });
    expect(screen.getByRole('button', { name: 'Enable push' })).toBeEnabled();
    expect(screen.getByText(/mobile_ops_threat_model/i)).toBeInTheDocument();
    expect(screen.getByText('alice')).toBeInTheDocument();
  });

  it('enables push via service and refreshes status', async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    localStorage.setItem('nautilus_token', makeToken(nowSec + 3600));
    enablePushNotifications.mockResolvedValue({
      available: true,
      reason: null,
      subscribed: true,
      subscription_count: 1,
    });
    render(<AccountPage />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Enable push' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Enable push' }));
    await waitFor(() => {
      expect(enablePushNotifications).toHaveBeenCalled();
      expect(screen.getByTestId('account-push-status')).toHaveTextContent(/Enabled/i);
    });
  });

  it('warns when session is expiring soon', async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    localStorage.setItem('nautilus_token', makeToken(nowSec + 120));
    render(<AccountPage />);
    expect(screen.getByTestId('account-expiry-warning')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('account-push-status')).toBeInTheDocument();
    });
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
