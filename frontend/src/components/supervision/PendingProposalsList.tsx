import { useState } from 'react';
import { CheckCircle2, Clock3, FileClock, Rocket, XCircle } from 'lucide-react';
import { supervisionService, type SupervisionProposal, type CommandApproval, type DispatchResult } from '@/services/supervisionService';
import { ApiError } from '@/lib/api';
import StepUpPrompt from './StepUpPrompt';

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(date);
}

const COMMAND_BADGE: Record<string, string> = {
  flatten: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
  kill_switch: 'border-rose-600/40 bg-rose-600/20 text-rose-200',
  start_strategy: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  stop_strategy: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  cancel_order: 'border-slate-600 bg-slate-700/50 text-slate-300',
  submit_order: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
};

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

export default function PendingProposalsList({
  proposals,
  count,
  loading,
  onProposalsChanged,
}: {
  proposals: SupervisionProposal[];
  count: number;
  loading: boolean;
  onProposalsChanged?: () => void;
}) {
  const role = localStorage.getItem('nautilus_role');
  const canAct = role === 'approver' || role === 'admin';
  const [actionStates, setActionStates] = useState<Record<string, ProposalActionState>>({});

  const updateState = (id: string, patch: Partial<ProposalActionState>) => {
    setActionStates((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  };

  const handleApprove = async (proposalId: string, stepUpCode?: string) => {
    updateState(proposalId, { approving: true, stepUpError: null, error: null });
    try {
      const approval = await supervisionService.approve(proposalId, stepUpCode);
      updateState(proposalId, { approving: false, approval, showStepUp: false });
    } catch (err) {
      if (isStepUpRequired(err)) {
        updateState(proposalId, { approving: false, showStepUp: true });
      } else if (err instanceof ApiError && err.status === 403) {
        const detail = err.detail as { message?: string } | string | undefined;
        const msg = typeof detail === 'object' ? detail?.message ?? 'Invalid step-up code' : 'Invalid step-up code';
        updateState(proposalId, { approving: false, stepUpError: msg, showStepUp: true });
      } else {
        updateState(proposalId, { approving: false, error: getErrorMessage(err) });
      }
    }
  };

  const handleDispatch = async (proposalId: string, approvalId: string) => {
    updateState(proposalId, { dispatching: true, error: null });
    try {
      const result = await supervisionService.dispatch(approvalId);
      updateState(proposalId, { dispatching: false, dispatchResult: result });
      onProposalsChanged?.();
    } catch (err) {
      updateState(proposalId, { dispatching: false, error: getErrorMessage(err) });
    }
  };

  const handleReject = async (proposalId: string) => {
    updateState(proposalId, { rejecting: true, error: null });
    try {
      await supervisionService.reject(proposalId);
      updateState(proposalId, { rejecting: false, rejected: true });
      onProposalsChanged?.();
    } catch (err) {
      updateState(proposalId, { rejecting: false, error: getErrorMessage(err) });
    }
  };

  return (
    <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <FileClock className="h-5 w-5 text-cyan-300" />
          <div>
            <h2 className="font-semibold text-white">Pending proposals</h2>
            <p className="mt-0.5 text-xs text-slate-500">Supervision-originated, awaiting human approval</p>
          </div>
        </div>
        {count > 0 && (
          <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-[11px] font-semibold text-amber-300">
            {count} pending
          </span>
        )}
      </div>

      {loading && proposals.length === 0 ? (
        <div className="mt-5 py-8 text-center text-sm text-slate-500">Loading proposals…</div>
      ) : proposals.length === 0 ? (
        <div className="mt-5 rounded-xl border border-dashed border-white/10 px-4 py-8 text-center">
          <p className="text-sm text-slate-400">No supervision proposals awaiting approval</p>
          <p className="mt-1 text-xs text-slate-600">
            When the supervisor creates a command proposal, it will appear here.
          </p>
        </div>
      ) : (
        <div className="mt-5 space-y-3">
          {proposals.map((p) => {
            const st = actionStates[p.proposal_id] ?? {};
            const isPending = p.status === 'pending';
            return (
              <div
                key={p.proposal_id}
                className="rounded-xl border border-white/7 bg-[#0b1727] p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wide ${
                          COMMAND_BADGE[p.command_name] ?? 'border-slate-600 bg-slate-700/50 text-slate-300'
                        }`}
                      >
                        {p.command_name.replaceAll('_', ' ')}
                      </span>
                      <span className="font-mono text-[11px] text-slate-600">{p.proposal_id}</span>
                    </div>
                    <div className="mt-2 text-xs text-slate-500">
                      Target: <span className="font-mono text-slate-300">{p.target_agent_id}</span>
                      {' · '}
                      Requester: <span className="text-slate-300">{p.requester}</span>
                    </div>
                  </div>
                  <span className="rounded-full border border-slate-600 bg-slate-700/50 px-2 py-0.5 text-[10px] font-semibold uppercase text-slate-300">
                    {st.rejected ? 'rejected' : st.dispatchResult ? 'dispatched' : p.status}
                  </span>
                </div>

                {Object.keys(p.payload).length > 0 && (
                  <pre className="mt-3 overflow-x-auto rounded-lg border border-white/6 bg-black/20 p-2.5 text-[11px] text-slate-400">
                    {JSON.stringify(p.payload, null, 2)}
                  </pre>
                )}

                <div className="mt-3 flex items-center gap-4 text-[11px] text-slate-600">
                  <span className="flex items-center gap-1">
                    <Clock3 className="h-3 w-3" /> Created {formatTimestamp(p.created_at)}
                  </span>
                  {p.expires_at && (
                    <span className="flex items-center gap-1">
                      <Clock3 className="h-3 w-3" /> Expires {formatTimestamp(p.expires_at)}
                    </span>
                  )}
                </div>

                {/* Error display */}
                {st.error && (
                  <div className="mt-3 rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200">
                    {st.error}
                  </div>
                )}

                {/* Step-up prompt */}
                {st.showStepUp && !st.approval && (
                  <StepUpPrompt
                    loading={st.approving}
                    error={st.stepUpError}
                    onSubmit={(code) => void handleApprove(p.proposal_id, code)}
                    onCancel={() => updateState(p.proposal_id, { showStepUp: false, stepUpError: null })}
                  />
                )}

                {/* Approval result + dispatch button */}
                {st.approval && !st.dispatchResult && (
                  <div className="mt-3 rounded-lg border border-cyan-500/25 bg-cyan-500/5 p-3">
                    <div className="flex items-center gap-2">
                      <CheckCircle2 className="h-4 w-4 text-cyan-300" />
                      <span className="text-xs font-semibold text-cyan-200">Approved</span>
                      <span className="font-mono text-[10px] text-slate-500">{st.approval.approval_id}</span>
                    </div>
                    <div className="mt-2 text-[11px] text-slate-500">
                      Approval expires at {formatTimestamp(st.approval.expires_at)}
                    </div>
                    {/* Re-display payload for final sanity check */}
                    {Object.keys(p.payload).length > 0 && (
                      <pre className="mt-2 overflow-x-auto rounded-lg border border-white/6 bg-black/20 p-2 text-[10px] text-slate-400">
                        {JSON.stringify(p.payload, null, 2)}
                      </pre>
                    )}
                    <button
                      type="button"
                      onClick={() => void handleDispatch(p.proposal_id, st.approval!.approval_id)}
                      disabled={st.dispatching}
                      className="mt-2.5 flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-xs font-medium text-rose-200 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <Rocket className="h-3.5 w-3.5" />
                      {st.dispatching ? 'Dispatching…' : 'Dispatch now'}
                    </button>
                  </div>
                )}

                {/* Dispatch result */}
                {st.dispatchResult && (
                  <div className="mt-3 rounded-lg border border-emerald-500/25 bg-emerald-500/5 p-3">
                    <div className="flex items-center gap-2">
                      <CheckCircle2 className="h-4 w-4 text-emerald-300" />
                      <span className="text-xs font-semibold text-emerald-200">Dispatched</span>
                      <span className="font-mono text-[10px] text-slate-500">{st.dispatchResult.dispatch_id}</span>
                    </div>
                    <div className="mt-1.5 text-[11px] text-slate-500">
                      Status: {st.dispatchResult.status} · {formatTimestamp(st.dispatchResult.dispatched_at)}
                    </div>
                  </div>
                )}

                {/* Rejected confirmation */}
                {st.rejected && (
                  <div className="mt-3 rounded-lg border border-slate-600 bg-slate-700/30 p-3">
                    <div className="flex items-center gap-2">
                      <XCircle className="h-4 w-4 text-slate-400" />
                      <span className="text-xs font-semibold text-slate-400">Proposal rejected</span>
                    </div>
                  </div>
                )}

                {/* Action buttons — only for pending proposals, only for approver/admin */}
                {isPending && canAct && !st.approval && !st.rejected && !st.dispatchResult && !st.showStepUp && (
                  <div className="mt-3 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void handleApprove(p.proposal_id)}
                      disabled={st.approving}
                      className="flex items-center gap-1.5 rounded-lg border border-cyan-400/25 bg-cyan-400/10 px-3 py-1.5 text-xs font-medium text-cyan-200 transition-colors hover:bg-cyan-400/20 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      {st.approving ? 'Approving…' : 'Approve'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleReject(p.proposal_id)}
                      disabled={st.rejecting}
                      className="flex items-center gap-1.5 rounded-lg border border-slate-600 bg-slate-700/30 px-3 py-1.5 text-xs font-medium text-slate-400 transition-colors hover:bg-slate-700/50 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <XCircle className="h-3.5 w-3.5" />
                      {st.rejecting ? 'Rejecting…' : 'Reject'}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
