import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  consumeReturnPath,
  consumeSessionReason,
  isTokenExpired,
  isTokenExpiringSoon,
  markSessionEnded,
  sessionReasonMessage,
  stashReturnPath,
  EXPIRY_WARN_MS,
} from '@/mobile/session';

function makeToken(expSeconds: number): string {
  const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
  const payload = btoa(JSON.stringify({ sub: 'ops', role: 'approver', exp: expSeconds }));
  return `${header}.${payload}.sig`;
}

describe('Mobile Ops session helpers (P5)', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('detects expired and soon-to-expire tokens', () => {
    const nowSec = Math.floor(Date.now() / 1000);
    localStorage.setItem('nautilus_token', makeToken(nowSec - 10));
    expect(isTokenExpired()).toBe(true);

    localStorage.setItem('nautilus_token', makeToken(nowSec + 60));
    expect(isTokenExpired()).toBe(false);
    expect(isTokenExpiringSoon(Date.now(), EXPIRY_WARN_MS)).toBe(true);

    localStorage.setItem('nautilus_token', makeToken(nowSec + 60 * 60));
    expect(isTokenExpiringSoon()).toBe(false);
  });

  it('stores and consumes session reasons once', () => {
    markSessionEnded('expired');
    expect(sessionReasonMessage(consumeSessionReason())).toMatch(/expired/i);
    expect(consumeSessionReason()).toBeNull();
  });

  it('only stashes /m return paths', () => {
    stashReturnPath('/trader');
    expect(consumeReturnPath()).toBeNull();
    stashReturnPath('/m/approvals');
    expect(consumeReturnPath()).toBe('/m/approvals');
    expect(consumeReturnPath()).toBeNull();
  });
});
