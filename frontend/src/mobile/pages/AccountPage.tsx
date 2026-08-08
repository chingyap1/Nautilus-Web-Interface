/**
 * Account tab — decode JWT locally until GET /api/auth/me lands (§8.5 gap 1).
 */
function readJwtClaims(): { sub?: string; role?: string; exp?: number } {
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

export default function AccountPage() {
  const role = localStorage.getItem('nautilus_role') ?? 'unknown';
  const claims = readJwtClaims();
  const expLabel =
    typeof claims.exp === 'number'
      ? new Date(claims.exp * 1000).toLocaleString()
      : 'unknown';

  const handleSignOut = () => {
    window.dispatchEvent(new CustomEvent('nautilus:unauthorized'));
  };

  return (
    <section className="space-y-5">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight text-white">Account</h2>
        <p className="text-sm text-[var(--mops-muted)]">
          NWI principal on the trading plane. Auth is Bearer JWT — same as desktop.
        </p>
      </div>

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

      <p className="text-xs text-[var(--mops-muted)]">
        Push opt-in and GET /api/auth/me are tracked gaps (§8.5). Sign-out clears the local Bearer
        session.
      </p>

      <button
        className="min-h-11 w-full rounded-xl border border-white/15 bg-white/5 text-sm font-semibold text-white active:bg-white/10"
        onClick={handleSignOut}
        type="button"
      >
        Sign out
      </button>
    </section>
  );
}
