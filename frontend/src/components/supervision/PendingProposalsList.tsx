import { Clock3, FileClock } from 'lucide-react';
import type { SupervisionProposal } from '@/services/supervisionService';

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

export default function PendingProposalsList({
  proposals,
  count,
  loading,
}: {
  proposals: SupervisionProposal[];
  count: number;
  loading: boolean;
}) {
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
            Approval and dispatch happen outside this UI until Phase 2.
          </p>
        </div>
      ) : (
        <div className="mt-5 space-y-3">
          {proposals.map((p) => (
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
                  {p.status}
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
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
