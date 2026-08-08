import { useCallback, useEffect, useState } from 'react';
import {
  supervisionService,
  type CommandApproval,
  type DispatchResult,
  type SupervisionProposal,
} from '@/services/supervisionService';
import { ApiError } from '@/lib/api';
import StepUpPrompt from '@/components/supervision/StepUpPrompt';
import { useOperationsSnapshot } from '../useOperationsSnapshot';
import { isPaperMode } from '../types';

/**
 * P2 Approvals inbox — approve → dispatch (+ step-up) and reject.
 * Same NWI handlers as desktop PendingProposalsList; Mobile Ops chrome only.
 */

interface ProposalActionState {
  approving?: boolean;
  dispatching?: boolean;
  rejecting?: boolean;
  showStepUp?: boolean;
  stepUpError?: string | null;
  approval?: CommandApproval;
  dispatchResult?: DispatchResult;
  error?: string | null;
  rejected?: boolean;
}

function isStepUpRequired(err: unknown): boolean {
  if (err instanceof ApiError && err.status === 403) {
    const detail = err.detail as { reason?: string } | string | undefined;
    if (typeof detail === 'object' && detail?.reason === 'step_up_required') return true;
  }
  return false;
}

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

function canActRole(role: string | null): boolean {
  return role === 'approver' || role === 'admin';
}

export default function ApprovalsPage() {
  const { snapshot } = useOperationsSnapshot();
  const paper = isPaperMode(snapshot);
  const [proposals, setProposals] = useState<SupervisionProposal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionStates, setActionStates] = useState<Record<string, ProposalActionState>>({});
  const role = typeof localStorage !== 'undefined' ? localStorage.getItem('nautilus_role') : null;
  const canAct = canActRole(role);

  const refresh = useCallback(async () => {
    try {
      const res = await supervisionService.listProposals();
      setProposals(res.proposals);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load proposals');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const updateState = (id: string, patch: Partial<ProposalActionState>) => {
    setActionStates((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  };

  const handleApprove = async (proposalId: string, stepUpCode?: string) => {
    if (!paper || !canAct) return;
    updateState(proposalId, { approving: true, stepUpError: null, error: null });
    try {
      const approval = await supervisionService.approve(proposalId, stepUpCode);
      updateState(proposalId, { approving: false, approval, showStepUp: false });
    } catch (err) {
      if (isStepUpRequired(err)) {
        updateState(proposalId, { approving: false, showStepUp: true });
      } else if (err instanceof ApiError && err.status === 403) {
        const detail = err.detail as { message?: string } | string | undefined;
        const msg =
          typeof detail === 'object' ? (detail?.message ?? 'Invalid step-up code') : 'Invalid step-up code';
        updateState(proposalId, { approving: false, stepUpError: msg, showStepUp: true });
      } else {
        updateState(proposalId, { approving: false, error: getErrorMessage(err) });
      }
    }
  };

  const handleDispatch = async (proposalId: string, approvalId: string) => {
    if (!paper || !canAct) return;
    updateState(proposalId, { dispatching: true, error: null });
    try {
      const result = await supervisionService.dispatch(approvalId);
      updateState(proposalId, { dispatching: false, dispatchResult: result });
      await refresh();
    } catch (err) {
      updateState(proposalId, { dispatching: false, error: getErrorMessage(err) });
    }
  };

  const handleReject = async (proposalId: string) => {
    if (!paper || !canAct) return;
    updateState(proposalId, { rejecting: true, error: null });
    try {
      await supervisionService.reject(proposalId);
      updateState(proposalId, { rejecting: false, rejected: true });
      await refresh();
    } catch (err) {
      updateState(proposalId, { rejecting: false, error: getErrorMessage(err) });
    }
  };

  return (
    <section className="space-y-4">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight text-white">Approvals</h2>
        <p className="text-sm text-[var(--mops-muted)]">
          Durable paper commands. Approve and dispatch are two separate NWI steps; HIGH/CRITICAL may
          ask for a TOTP step-up.
        </p>
      </div>

      {!paper && !loading ? (
        <div className="rounded-xl border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
          Mutations disabled — execution mode is{' '}
          <span className="font-semibold uppercase">{snapshot?.execution.mode ?? 'unknown'}</span>.
          Inbox stays read-only outside paper.
        </div>
      ) : null}

      {!canAct ? (
        <p className="rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-3 text-sm text-[var(--mops-muted)]">
          Signed in as <span className="text-white">{role || 'unknown'}</span> — approve / dispatch /
          reject require <span className="text-white">approver</span> or{' '}
          <span className="text-white">admin</span>.
        </p>
      ) : null}

      {loading ? <p className="text-sm text-[var(--mops-muted)]">Loading…</p> : null}
      {error ? (
        <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      {!loading && !error && proposals.length === 0 ? (
        <p className="rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-6 text-sm text-[var(--mops-muted)]">
          No pending supervision proposals.
        </p>
      ) : null}

      <ul className="space-y-3">
        {proposals.map((p) => {
          const st = actionStates[p.proposal_id] ?? {};
          const statusLabel = st.rejected
            ? 'rejected'
            : st.dispatchResult
              ? 'dispatched'
              : st.approval
                ? 'approved'
                : p.status;
          const showActions =
            paper && canAct && p.status === 'pending' && !st.rejected && !st.dispatchResult;

          return (
            <li
              className="rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-3"
              key={p.proposal_id}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-medium text-white">{p.command_name.replaceAll('_', ' ')}</p>
                  <p className="mt-1 text-xs text-[var(--mops-muted)]">
                    {p.target_agent_id} · {p.requester}
                  </p>
                </div>
                <span className="rounded-md border border-white/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--mops-muted)]">
                  {statusLabel}
                </span>
              </div>

              <p className="mt-2 font-mono text-[10px] text-[var(--mops-muted)]">{p.proposal_id}</p>

              {Object.keys(p.payload).length > 0 ? (
                <pre className="mt-3 overflow-x-auto rounded-lg border border-white/5 bg-black/20 p-2.5 text-[11px] text-[var(--mops-muted)]">
                  {JSON.stringify(p.payload, null, 2)}
                </pre>
              ) : null}

              {st.error ? (
                <p className="mt-3 rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
                  {st.error}
                </p>
              ) : null}

              {st.showStepUp && !st.approval ? (
                <StepUpPrompt
                  error={st.stepUpError}
                  loading={st.approving}
                  onCancel={() =>
                    updateState(p.proposal_id, { showStepUp: false, stepUpError: null })
                  }
                  onSubmit={(code) => void handleApprove(p.proposal_id, code)}
                />
              ) : null}

              {st.approval && !st.dispatchResult ? (
                <div className="mt-3 space-y-2 rounded-lg border border-sky-400/25 bg-sky-400/5 px-3 py-3">
                  <p className="text-xs font-semibold text-sky-200">
                    Approved — dispatch is a separate confirm
                  </p>
                  <button
                    className="min-h-11 w-full rounded-lg border border-sky-400/30 bg-sky-400/15 text-sm font-semibold text-sky-100 disabled:opacity-40"
                    disabled={st.dispatching || !showActions}
                    onClick={() => void handleDispatch(p.proposal_id, st.approval!.approval_id)}
                    type="button"
                  >
                    {st.dispatching ? 'Dispatching…' : 'Dispatch to agent'}
                  </button>
                </div>
              ) : null}

              {st.dispatchResult ? (
                <p className="mt-3 text-xs font-medium text-emerald-300">
                  Dispatched · {st.dispatchResult.status}
                </p>
              ) : null}

              {showActions && !st.approval && !st.showStepUp ? (
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <button
                    className="min-h-11 rounded-lg border border-emerald-400/30 bg-emerald-400/10 text-sm font-semibold text-emerald-100 disabled:opacity-40"
                    disabled={st.approving || st.rejecting}
                    onClick={() => void handleApprove(p.proposal_id)}
                    type="button"
                  >
                    {st.approving ? 'Approving…' : 'Approve'}
                  </button>
                  <button
                    className="min-h-11 rounded-lg border border-rose-400/30 bg-rose-500/10 text-sm font-semibold text-rose-100 disabled:opacity-40"
                    disabled={st.approving || st.rejecting}
                    onClick={() => void handleReject(p.proposal_id)}
                    type="button"
                  >
                    {st.rejecting ? 'Rejecting…' : 'Reject'}
                  </button>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
