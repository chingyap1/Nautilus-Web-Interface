import { useEffect, useState } from 'react';
import { API_CONFIG } from '@/config';
import {
  isTokenExpiringSoon,
  markSessionEnded,
  readJwtPayload,
  tokenExpiresAtMs,
} from '../session';

/**
 * Account tab — JWT claims locally until GET /api/auth/me lands (§8.5).
 * P5: expiry warning, honest push gap, logout via /api/auth/logout.
 */
export default function AccountPage() {
  const role = localStorage.getItem('nautilus_role') ?? 'unknown';
  const claims = readJwtPayload();
  const expMs = tokenExpiresAtMs(claims);
  const expLabel = expMs !== null ? new Date(expMs).toLocaleString() : 'unknown';
  const [expiringSoon, setExpiringSoon] = useState(() => isTokenExpiringSoon());
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    const id = window.setInterval(() => setExpiringSoon(isTokenExpiringSoon()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const handleSignOut = async () => {
    setSigningOut(true);
    const token = localStorage.getItem('nautilus_token');
    if (token) {
      try {
        await fetch(`${API_CONFIG.NAUTILUS_API_URL}/api/auth/logout`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        });
      } catch {
        // local sign-out still proceeds
      }
    }
    markSessionEnded('signed_out');
    localStorage.removeItem('nautilus_token');
    localStorage.removeItem('nautilus_role');
    window.dispatchEvent(new CustomEvent('nautilus:unauthorized'));
  };

  return (
    <section className="space-y-5">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight text-white">Account</h2>
        <p className="text-sm text-[var(--mops-muted)]">
          NWI principal on the trading plane. Auth is Bearer JWT — same as desktop. Live market is
          deferred; this shell stays paper-only.
        </p>
      </div>

      {expiringSoon ? (
        <p
          className="rounded-xl border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100"
          data-testid="account-expiry-warning"
          role="status"
        >
          Session expires soon ({expLabel}). Re-authenticate before approving or using Controls.
        </p>
      ) : null}

      <dl className="space-y-3 rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-4">
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--mops-muted)]">
            Subject
          </dt>
          <dd className="mt-1 text-sm text-white">{claims.sub ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--mops-muted)]">
            Role
          </dt>
          <dd className="mt-1 text-sm font-medium text-white">{claims.role ?? role}</dd>
        </div>
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--mops-muted)]">
            Token expires
          </dt>
          <dd className="mt-1 text-sm tabular-nums text-white">{expLabel}</dd>
        </div>
      </dl>

      <div className="rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-3">
        <p className="text-sm font-semibold text-white">Push notifications</p>
        <p className="mt-1 text-xs leading-relaxed text-[var(--mops-muted)]">
          Opt-in is unavailable — NWI has no web-push registration API yet (§8.5). Keep the PWA open
          or use desktop Supervision for attention until that lands.
        </p>
      </div>

      <div className="rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-3">
        <p className="text-sm font-semibold text-white">Threat model</p>
        <p className="mt-1 text-xs leading-relaxed text-[var(--mops-muted)]">
          P5 paper hardening is documented in{' '}
          <span className="font-mono text-[11px] text-sky-200">docs/mobile_ops_threat_model.md</span>
          . Tailscale is reachability only; Bearer JWT is authority. Live trading is out of scope.
        </p>
      </div>

      <button
        className="min-h-11 w-full rounded-xl border border-white/15 bg-white/5 text-sm font-semibold text-white active:bg-white/10 disabled:opacity-40"
        disabled={signingOut}
        onClick={() => void handleSignOut()}
        type="button"
      >
        {signingOut ? 'Signing out…' : 'Sign out'}
      </button>
    </section>
  );
}
