import { Info, AlertTriangle, ShieldAlert, Zap, RotateCcw, CircleStop } from 'lucide-react';
import type { Recommendation, CommandProposal } from '@/services/supervisionService';

const KIND_CONFIG: Record<
  string,
  { icon: typeof Info; badge: string; label: string }
> = {
  none: { icon: Info, badge: 'border-slate-600 bg-slate-700/50 text-slate-300', label: 'No action' },
  review: { icon: Info, badge: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300', label: 'Review' },
  experiment: { icon: Zap, badge: 'border-violet-500/30 bg-violet-500/10 text-violet-300', label: 'Experiment' },
  restart: { icon: RotateCcw, badge: 'border-amber-500/30 bg-amber-500/10 text-amber-300', label: 'Restart' },
  flatten: { icon: ShieldAlert, badge: 'border-rose-500/30 bg-rose-500/10 text-rose-300', label: 'Flatten' },
  halt: { icon: CircleStop, badge: 'border-rose-600/40 bg-rose-600/20 text-rose-200', label: 'Halt' },
};

const RISK_BADGE: Record<string, string> = {
  low: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  medium: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  high: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
  critical: 'border-rose-600/40 bg-rose-600/20 text-rose-200',
};

// Recommendation kinds that create a command proposal (from supervision/bridge.py)
const PROPOSAL_KINDS = new Set(['restart', 'flatten', 'halt']);

export default function RecommendationCard({
  recommendation,
  proposal,
}: {
  recommendation: Recommendation | null;
  proposal: CommandProposal | null;
}) {
  if (!recommendation) {
    return (
      <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5">
        <div className="flex items-center gap-3">
          <Info className="h-5 w-5 text-slate-600" />
          <h2 className="font-semibold text-white">Recommendation</h2>
        </div>
        <div className="mt-5 rounded-xl border border-dashed border-white/10 px-4 py-8 text-center">
          <p className="text-sm text-slate-400">No recommendation yet</p>
          <p className="mt-1 text-xs text-slate-600">Run an inspection to get a supervision recommendation.</p>
        </div>
      </div>
    );
  }

  const config = KIND_CONFIG[recommendation.kind] ?? KIND_CONFIG.none;
  const KindIcon = config.icon;
  const createsProposal = PROPOSAL_KINDS.has(recommendation.kind);

  return (
    <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <KindIcon className="h-5 w-5 text-cyan-300" />
          <h2 className="font-semibold text-white">Recommendation</h2>
        </div>
        <div className="flex gap-2">
          <span
            className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-semibold tracking-wide ${config.badge}`}
          >
            {config.label}
          </span>
          <span
            className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-semibold tracking-wide ${
              RISK_BADGE[recommendation.risk_level] ?? RISK_BADGE.low
            }`}
          >
            {recommendation.risk_level} risk
          </span>
        </div>
      </div>

      <p className="mt-4 text-sm leading-relaxed text-slate-300">{recommendation.reason}</p>

      <div className="mt-3 text-xs text-slate-600">
        Proposed at:{' '}
        {new Intl.DateTimeFormat('en-US', {
          month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
        }).format(new Date(recommendation.proposed_at))}
      </div>

      {createsProposal && proposal && (
        <div className="mt-4 rounded-xl border border-amber-500/20 bg-amber-500/[0.07] p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
            <div>
              <div className="text-sm font-medium text-amber-200">
                Proposal {proposal.proposal_id} created — awaiting human approval
              </div>
              <div className="mt-1 text-xs text-slate-400">
                Command: <span className="font-mono text-slate-300">{proposal.command_name}</span>
                {' · '}
                Target: <span className="font-mono text-slate-300">{proposal.target_agent_id}</span>
              </div>
              <div className="mt-2 text-xs text-slate-500">
                Approval and dispatch happen outside this UI (CLI or direct Python) until Phase 2 lands.
                No action buttons are provided here — by design.
              </div>
            </div>
          </div>
        </div>
      )}

      {createsProposal && !proposal && (
        <div className="mt-4 rounded-xl border border-white/7 bg-white/[0.025] p-4 text-xs text-slate-500">
          A command proposal was expected for this recommendation kind but none was returned.
          This may indicate the interlock was paused (D5) or the command was rejected by the catalog.
        </div>
      )}

      {!createsProposal && (
        <div className="mt-4 rounded-xl border border-white/7 bg-white/[0.025] p-3 text-xs text-slate-500">
          Advisory only — no command proposal created. The human operator decides whether to take action.
        </div>
      )}
    </div>
  );
}
