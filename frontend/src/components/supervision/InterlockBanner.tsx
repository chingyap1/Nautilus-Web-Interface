import { useState } from 'react';
import { ShieldCheck, ShieldOff, AlertTriangle } from 'lucide-react';
import type { InterlockState, InterlockActionResponse } from '@/services/supervisionService';

interface InterlockBannerProps {
  state: InterlockState | null;
  actionResponse: InterlockActionResponse | null;
  loading: boolean;
  onEngage: () => void;
  onResume: () => void;
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(date);
}

export default function InterlockBanner({
  state,
  actionResponse,
  loading,
  onEngage,
  onResume,
}: InterlockBannerProps) {
  const [confirmResume, setConfirmResume] = useState(false);
  const role = localStorage.getItem('nautilus_role');
  const isOperator = role === 'operator' || role === 'admin';
  const isAdmin = role === 'admin';

  const paused = state?.state === 'paused';
  // Fail-closed: if state is null/loading, treat as paused (safe default per D5)
  const displayPaused = paused || state == null;

  const handleResume = () => {
    if (!confirmResume) {
      setConfirmResume(true);
      return;
    }
    setConfirmResume(false);
    onResume();
  };

  return (
    <section
      className={`mb-7 overflow-hidden rounded-2xl border ${
        displayPaused
          ? 'border-amber-500/25 bg-amber-500/[0.06]'
          : 'border-emerald-500/25 bg-emerald-500/[0.06]'
      }`}
    >
      <div className="flex flex-col justify-between gap-5 p-5 sm:flex-row sm:items-center">
        <div className="flex items-start gap-4">
          <div
            className={`mt-0.5 rounded-xl p-2.5 ${
              displayPaused ? 'bg-amber-400/15 text-amber-300' : 'bg-emerald-400/15 text-emerald-300'
            }`}
          >
            {displayPaused ? <ShieldOff className="h-5 w-5" /> : <ShieldCheck className="h-5 w-5" />}
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-semibold text-white">
                Supervisor traffic: {displayPaused ? 'PAUSED' : 'RESUMED'}
              </h2>
              <span
                className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest ${
                  displayPaused
                    ? 'border-amber-500/30 bg-amber-500/10 text-amber-300'
                    : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                }`}
              >
                {displayPaused ? 'Fail-closed' : 'Active'}
              </span>
            </div>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-slate-400">
              {displayPaused
                ? 'All supervisor command proposals are blocked. No new proposals will be created until the interlock is resumed.'
                : 'Supervisor can inspect agents and create proposals. All proposals still require human approval before dispatch.'}
            </p>
            {actionResponse && (
              <p className="mt-2 text-xs text-slate-500">
                Last action by <span className="font-medium text-slate-300">{actionResponse.actor}</span>
                {' — '}
                {actionResponse.reason} · {formatTimestamp(actionResponse.updated_at)}
              </p>
            )}
            {!actionResponse && state?.actor && (
              <p className="mt-2 text-xs text-slate-500">
                Last action by <span className="font-medium text-slate-300">{state.actor}</span>
                {state.reason ? ` — ${state.reason}` : ''}
                {state.updated_at ? ` · ${formatTimestamp(state.updated_at)}` : ''}
              </p>
            )}
            {loading && !state && (
              <p className="mt-2 text-xs text-slate-500">Checking interlock state…</p>
            )}
            {state == null && !loading && (
              <p className="mt-2 flex items-center gap-1.5 text-xs text-amber-300">
                <AlertTriangle className="h-3.5 w-3.5" />
                Interlock state unreachable — treating as PAUSED (fail-closed per D5).
              </p>
            )}
          </div>
        </div>

        <div className="flex shrink-0 gap-3">
          {!displayPaused && isOperator && (
            <button
              type="button"
              onClick={onEngage}
              className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-sm font-medium text-amber-200 transition-colors hover:bg-amber-500/20"
            >
              Pause Supervisor commands
            </button>
          )}
          {displayPaused && isAdmin && (
            <button
              type="button"
              onClick={handleResume}
              className={`rounded-lg border px-4 py-2.5 text-sm font-medium transition-colors ${
                confirmResume
                  ? 'border-rose-500/40 bg-rose-500/15 text-rose-200 hover:bg-rose-500/25'
                  : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20'
              }`}
            >
              {confirmResume ? 'Confirm resume — re-enable proposals' : 'Resume — admin only'}
            </button>
          )}
          {displayPaused && isOperator && !isAdmin && (
            <span className="rounded-lg border border-white/8 bg-white/5 px-4 py-2.5 text-sm text-slate-500">
              Resume requires admin role
            </span>
          )}
        </div>
      </div>
    </section>
  );
}
