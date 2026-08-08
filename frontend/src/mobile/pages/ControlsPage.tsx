import { useCallback, useEffect, useState, type ReactNode } from 'react';
import nautilusService, { type Strategy } from '@/services/nautilusService';
import {
  supervisionService,
  type InterlockActionResponse,
  type InterlockState,
} from '@/services/supervisionService';
import { ApiError } from '@/lib/api';
import { useOperationsSnapshot } from '../useOperationsSnapshot';
import { isPaperMode } from '../types';

/**
 * P3 Paper emergency Controls — kill / flatten / interlock (§7.3 / §8.2 / §8.3).
 * Same NWI handlers as desktop; two-step confirm + reason for mutations.
 */

type ActivePanel = 'kill' | 'flatten' | 'engage' | 'resume' | null;

function getErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = err.detail;
    if (typeof detail === 'string') return detail;
    if (typeof detail === 'object' && detail && 'message' in detail) {
      return String((detail as { message: string }).message);
    }
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}

function canMutateRole(role: string | null): boolean {
  return role === 'operator' || role === 'approver' || role === 'admin';
}

function isAdminRole(role: string | null): boolean {
  return role === 'admin';
}

export default function ControlsPage() {
  const { snapshot, loading, error, refresh } = useOperationsSnapshot();
  const paper = isPaperMode(snapshot);
  const role = typeof localStorage !== 'undefined' ? localStorage.getItem('nautilus_role') : null;
  const canMutate = canMutateRole(role);
  const isAdmin = isAdminRole(role);

  const [interlock, setInterlock] = useState<InterlockState | null>(null);
  const [interlockLoading, setInterlockLoading] = useState(true);
  const [lastAction, setLastAction] = useState<InterlockActionResponse | null>(null);

  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [strategyId, setStrategyId] = useState('');
  const [reason, setReason] = useState('');
  const [activePanel, setActivePanel] = useState<ActivePanel>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionOk, setActionOk] = useState<string | null>(null);

  const loadInterlock = useCallback(async () => {
    try {
      const state = await supervisionService.getInterlock();
      setInterlock(state);
    } catch {
      setInterlock(null);
    } finally {
      setInterlockLoading(false);
    }
  }, []);

  const loadStrategies = useCallback(async () => {
    try {
      const res = await nautilusService.listStrategies();
      const list = res.strategies ?? [];
      setStrategies(list);
      setStrategyId((prev) => prev || list[0]?.id || '');
    } catch {
      setStrategies([]);
    }
  }, []);

  useEffect(() => {
    void loadInterlock();
    const id = window.setInterval(() => void loadInterlock(), 5_000);
    return () => window.clearInterval(id);
  }, [loadInterlock]);

  useEffect(() => {
    if (paper) void loadStrategies();
  }, [paper, loadStrategies]);

  const openPanel = (panel: ActivePanel) => {
    setActivePanel(panel);
    setReason('');
    setActionError(null);
    setActionOk(null);
  };

  const cancelPanel = () => {
    setActivePanel(null);
    setReason('');
    setActionError(null);
  };

  const reasonReady = reason.trim().length >= 3;

  const runKill = async () => {
    if (!paper || !canMutate || !reasonReady) return;
    setBusy(true);
    setActionError(null);
    try {
      const res = await nautilusService.activateKillSwitch();
      setActionOk(res.message || `Kill switch accepted · ${res.command_id}`);
      setActivePanel(null);
      setReason('');
      await refresh();
    } catch (err) {
      setActionError(getErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const runFlatten = async () => {
    if (!paper || !canMutate || !reasonReady || !strategyId.trim()) return;
    setBusy(true);
    setActionError(null);
    try {
      const res = await nautilusService.flattenStrategy(strategyId.trim());
      setActionOk(res.message || `Flatten accepted · ${res.command_id}`);
      setActivePanel(null);
      setReason('');
      await refresh();
    } catch (err) {
      setActionError(getErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const runEngage = async () => {
    if (!paper || !canMutate || !reasonReady) return;
    setBusy(true);
    setActionError(null);
    try {
      const resp = await supervisionService.engageInterlock(reason.trim());
      setLastAction(resp);
      setInterlock({ state: resp.state, actor: resp.actor, reason: resp.reason, updated_at: resp.updated_at });
      setActionOk(`Supervisor commands paused · ${resp.reason}`);
      setActivePanel(null);
      setReason('');
    } catch (err) {
      setActionError(getErrorMessage(err));
      void loadInterlock();
    } finally {
      setBusy(false);
    }
  };

  const runResume = async () => {
    if (!paper || !isAdmin || !reasonReady) return;
    setBusy(true);
    setActionError(null);
    try {
      const resp = await supervisionService.resumeInterlock(reason.trim());
      setLastAction(resp);
      setInterlock({ state: resp.state, actor: resp.actor, reason: resp.reason, updated_at: resp.updated_at });
      setActionOk(`Supervisor commands resumed · ${resp.reason}`);
      setActivePanel(null);
      setReason('');
    } catch (err) {
      setActionError(getErrorMessage(err));
      void loadInterlock();
    } finally {
      setBusy(false);
    }
  };

  const paused = interlock?.state === 'paused' || interlock == null;
  const interlockLabel = interlockLoading
    ? 'checking…'
    : interlock == null
      ? 'unknown (fail-closed)'
      : interlock.state.toUpperCase();

  return (
    <section className="space-y-4">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight text-white">Controls</h2>
        <p className="text-sm text-[var(--mops-muted)]">
          Paper emergency actions only. Pause maps to Supervisor interlock engage — not a missing
          agent pause command. Every mutation needs a reason and a second confirm.
        </p>
      </div>

      {loading ? <p className="text-sm text-[var(--mops-muted)]">Checking mode…</p> : null}
      {error ? (
        <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      {!loading && !paper ? (
        <div className="rounded-xl border border-amber-400/30 bg-amber-400/10 px-4 py-4 text-sm text-amber-100">
          Controls are disabled — execution mode is{' '}
          <span className="font-semibold uppercase">{snapshot?.execution.mode ?? 'unknown'}</span>.
          Mobile Ops v1 refuses non-paper mutations.
        </div>
      ) : null}

      {paper && !canMutate ? (
        <p className="rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-3 text-sm text-[var(--mops-muted)]">
          Signed in as <span className="text-white">{role || 'unknown'}</span> — emergency controls
          require <span className="text-white">operator</span>,{' '}
          <span className="text-white">approver</span>, or <span className="text-white">admin</span>.
        </p>
      ) : null}

      {paper ? (
        <div className="rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--mops-muted)]">
            Supervisor interlock
          </p>
          <p className="mt-1 text-base font-semibold text-white">{interlockLabel}</p>
          {(lastAction || interlock?.actor) && (
            <p className="mt-1 text-xs text-[var(--mops-muted)]">
              {(lastAction ?? interlock)?.actor}
              {(lastAction ?? interlock)?.reason ? ` — ${(lastAction ?? interlock)?.reason}` : ''}
            </p>
          )}
        </div>
      ) : null}

      {actionOk ? (
        <p className="rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">
          {actionOk}
        </p>
      ) : null}
      {actionError && !activePanel ? (
        <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {actionError}
        </p>
      ) : null}

      {paper && canMutate ? (
        <div className="space-y-3">
          <ControlCard
            danger
            label="Kill switch"
            path="POST /api/kill-switch"
            note="Halts trading: cancel orders, flatten, stop strategies."
            open={activePanel === 'kill'}
            onOpen={() => openPanel('kill')}
            onCancel={cancelPanel}
            reason={reason}
            onReasonChange={setReason}
            busy={busy}
            error={activePanel === 'kill' ? actionError : null}
            confirmLabel="Confirm kill switch"
            onConfirm={() => void runKill()}
            confirmDisabled={!reasonReady}
          />

          <ControlCard
            danger
            label="Flatten strategy"
            path="POST /api/strategies/{id}/flatten"
            note="Closes positions for one NWI strategy id."
            open={activePanel === 'flatten'}
            onOpen={() => openPanel('flatten')}
            onCancel={cancelPanel}
            reason={reason}
            onReasonChange={setReason}
            busy={busy}
            error={activePanel === 'flatten' ? actionError : null}
            confirmLabel="Confirm flatten"
            onConfirm={() => void runFlatten()}
            confirmDisabled={!reasonReady || !strategyId.trim()}
          >
            {strategies.length > 0 ? (
              <label className="block space-y-1.5">
                <span className="text-xs font-medium text-[var(--mops-muted)]">Strategy</span>
                <select
                  className="min-h-11 w-full rounded-lg border border-white/10 bg-black/30 px-3 text-sm text-white"
                  onChange={(e) => setStrategyId(e.target.value)}
                  value={strategyId}
                >
                  {strategies.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name || s.id} ({s.id})
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <label className="block space-y-1.5">
                <span className="text-xs font-medium text-[var(--mops-muted)]">Strategy id</span>
                <input
                  className="min-h-11 w-full rounded-lg border border-white/10 bg-black/30 px-3 text-sm text-white placeholder:text-[var(--mops-muted)]"
                  onChange={(e) => setStrategyId(e.target.value)}
                  placeholder="strategy id"
                  value={strategyId}
                />
              </label>
            )}
          </ControlCard>

          {!paused ? (
            <ControlCard
              label="Pause Supervisor commands"
              path="POST /api/supervision/interlock/engage"
              note="Interlock engage — blocks new Supervisor proposals. Not an agent halt."
              open={activePanel === 'engage'}
              onOpen={() => openPanel('engage')}
              onCancel={cancelPanel}
              reason={reason}
              onReasonChange={setReason}
              busy={busy}
              error={activePanel === 'engage' ? actionError : null}
              confirmLabel="Confirm pause Supervisor"
              onConfirm={() => void runEngage()}
              confirmDisabled={!reasonReady}
            />
          ) : null}

          {paused && isAdmin ? (
            <ControlCard
              label="Resume Supervisor commands"
              path="POST /api/supervision/interlock/resume"
              note="Admin only — Supervisor cannot self-resume."
              open={activePanel === 'resume'}
              onOpen={() => openPanel('resume')}
              onCancel={cancelPanel}
              reason={reason}
              onReasonChange={setReason}
              busy={busy}
              error={activePanel === 'resume' ? actionError : null}
              confirmLabel="Confirm resume Supervisor"
              onConfirm={() => void runResume()}
              confirmDisabled={!reasonReady}
            />
          ) : null}

          {paused && !isAdmin ? (
            <div className="rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-3">
              <p className="text-sm font-semibold text-white">Resume Supervisor commands</p>
              <p className="mt-1 font-mono text-[11px] text-[var(--mops-muted)]">
                POST /api/supervision/interlock/resume
              </p>
              <p className="mt-2 text-xs text-[var(--mops-muted)]">
                Interlock is paused (fail-closed). Resume requires admin role.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function ControlCard({
  label,
  path,
  note,
  danger,
  open,
  onOpen,
  onCancel,
  reason,
  onReasonChange,
  busy,
  error,
  confirmLabel,
  onConfirm,
  confirmDisabled,
  children,
}: {
  label: string;
  path: string;
  note: string;
  danger?: boolean;
  open: boolean;
  onOpen: () => void;
  onCancel: () => void;
  reason: string;
  onReasonChange: (value: string) => void;
  busy: boolean;
  error: string | null;
  confirmLabel: string;
  onConfirm: () => void;
  confirmDisabled: boolean;
  children?: ReactNode;
}) {
  return (
    <div
      className={`rounded-xl border px-4 py-3 ${
        danger
          ? 'border-rose-400/35 bg-rose-500/10'
          : 'border-[var(--mops-border)] bg-[var(--mops-panel)]'
      }`}
    >
      <p className={`text-sm font-semibold ${danger ? 'text-rose-100' : 'text-white'}`}>{label}</p>
      <p className="mt-1 font-mono text-[11px] text-[var(--mops-muted)]">{path}</p>
      <p className="mt-2 text-xs text-[var(--mops-muted)]">{note}</p>

      {!open ? (
        <button
          className={`mt-3 min-h-11 w-full rounded-lg border text-sm font-semibold disabled:opacity-40 ${
            danger
              ? 'border-rose-400/40 bg-rose-500/15 text-rose-100'
              : 'border-white/15 bg-white/5 text-white'
          }`}
          onClick={onOpen}
          type="button"
        >
          {label}
        </button>
      ) : (
        <div className="mt-3 space-y-3">
          {children}
          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-[var(--mops-muted)]">Reason (required)</span>
            <input
              className="min-h-11 w-full rounded-lg border border-white/10 bg-black/30 px-3 text-sm text-white placeholder:text-[var(--mops-muted)]"
              onChange={(e) => onReasonChange(e.target.value)}
              placeholder="Why are you doing this?"
              value={reason}
            />
          </label>
          {error ? (
            <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
              {error}
            </p>
          ) : null}
          <div className="grid grid-cols-2 gap-2">
            <button
              className="min-h-11 rounded-lg border border-white/10 bg-white/5 text-sm font-medium text-[var(--mops-muted)]"
              disabled={busy}
              onClick={onCancel}
              type="button"
            >
              Cancel
            </button>
            <button
              className={`min-h-11 rounded-lg border text-sm font-semibold disabled:opacity-40 ${
                danger
                  ? 'border-rose-400/50 bg-rose-500/25 text-rose-50'
                  : 'border-amber-400/40 bg-amber-400/15 text-amber-100'
              }`}
              disabled={busy || confirmDisabled}
              onClick={onConfirm}
              type="button"
            >
              {busy ? 'Working…' : confirmLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
