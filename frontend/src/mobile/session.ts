/**
 * Mobile Ops session helpers (P5) — expiry reason, return path, JWT clocks.
 * Keys live in sessionStorage so a tab refresh still shows the login reason.
 */

export const SESSION_REASON_KEY = 'nautilus_mops_session_reason';
export const RETURN_PATH_KEY = 'nautilus_mops_return_path';

/** Warn when fewer than this many ms remain before JWT exp. */
export const EXPIRY_WARN_MS = 15 * 60 * 1000;

export type SessionReason = 'expired' | 'unauthorized' | 'signed_out';

export function readJwtPayload(): { sub?: string; role?: string; exp?: number } {
  const token = localStorage.getItem('nautilus_token');
  if (!token) return {};
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return {};
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(base64)) as { sub?: string; role?: string; exp?: number };
  } catch {
    return {};
  }
}

export function tokenExpiresAtMs(claims = readJwtPayload()): number | null {
  return typeof claims.exp === 'number' ? claims.exp * 1000 : null;
}

export function isTokenExpired(now = Date.now()): boolean {
  const exp = tokenExpiresAtMs();
  return exp !== null && exp < now;
}

export function isTokenExpiringSoon(now = Date.now(), warnMs = EXPIRY_WARN_MS): boolean {
  const exp = tokenExpiresAtMs();
  if (exp === null) return false;
  const remaining = exp - now;
  return remaining > 0 && remaining <= warnMs;
}

export function markSessionEnded(reason: SessionReason): void {
  try {
    sessionStorage.setItem(SESSION_REASON_KEY, reason);
  } catch {
    // private mode / blocked storage — ignore
  }
}

export function consumeSessionReason(): SessionReason | null {
  try {
    const value = sessionStorage.getItem(SESSION_REASON_KEY);
    sessionStorage.removeItem(SESSION_REASON_KEY);
    if (value === 'expired' || value === 'unauthorized' || value === 'signed_out') {
      return value;
    }
  } catch {
    // ignore
  }
  return null;
}

export function sessionReasonMessage(reason: SessionReason | null): string | null {
  if (reason === 'expired') {
    return 'Your session expired. Sign in again to continue Mobile Ops.';
  }
  if (reason === 'unauthorized') {
    return 'Your session is no longer valid. Sign in again to continue.';
  }
  if (reason === 'signed_out') {
    return 'Signed out of the trading plane.';
  }
  return null;
}

/** Stash /m/* path so login can restore the deep link (§7.4). */
export function stashReturnPath(path: string): void {
  if (!path.startsWith('/m')) return;
  try {
    sessionStorage.setItem(RETURN_PATH_KEY, path);
  } catch {
    // ignore
  }
}

export function consumeReturnPath(): string | null {
  try {
    const path = sessionStorage.getItem(RETURN_PATH_KEY);
    sessionStorage.removeItem(RETURN_PATH_KEY);
    if (path && path.startsWith('/m')) return path;
  } catch {
    // ignore
  }
  return null;
}

export function currentPathForReturn(): string {
  if (typeof window === 'undefined') return '/m/status';
  const path = `${window.location.pathname}${window.location.search}`;
  return path.startsWith('/m') ? path : '/m/status';
}
