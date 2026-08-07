import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, RefreshCw, Radar } from 'lucide-react';

import nautilusService, { type AgentSnapshot } from '@/services/nautilusService';
import { supervisionService, type SupervisionResult, type InterlockState, type InterlockActionResponse, type SupervisionProposal } from '@/services/supervisionService';

import InterlockBanner from '@/components/supervision/InterlockBanner';
import AgentHealthCard from '@/components/supervision/AgentHealthCard';
import MetricsCard from '@/components/supervision/MetricsCard';
import RecommendationCard from '@/components/supervision/RecommendationCard';
import PendingProposalsList from '@/components/supervision/PendingProposalsList';
import AuditFeed from '@/components/supervision/AuditFeed';

export default function SupervisionPage() {
  // Agent pair selector — derived from operations snapshot (same source as TraderDashboard)
  const [agents, setAgents] = useState<AgentSnapshot[]>([]);
  const [selectedPair, setSelectedPair] = useState<string>('');
  const [inspecting, setInspecting] = useState(false);
  const [inspectError, setInspectError] = useState<string | null>(null);
  const [inspectResult, setInspectResult] = useState<SupervisionResult | null>(null);

  // Interlock state — polled every 5s
  const [interlockState, setInterlockState] = useState<InterlockState | null>(null);
  const [interlockAction, setInterlockAction] = useState<InterlockActionResponse | null>(null);
  const [interlockLoading, setInterlockLoading] = useState(true);

  // Pending proposals — polled every 10s
  const [proposals, setProposals] = useState<SupervisionProposal[]>([]);
  const [proposalsCount, setProposalsCount] = useState(0);
  const [proposalsLoading, setProposalsLoading] = useState(true);

  // --- Load agent list from operations snapshot (for pair selector) ---
  const loadAgents = useCallback(async () => {
    try {
      const snapshot = await nautilusService.getOperationsSnapshot();
      setAgents(snapshot.agents ?? []);
      // Auto-select first pair if none selected
      if (!selectedPair && (snapshot.agents?.length ?? 0) > 0) {
        setSelectedPair(snapshot.agents[0].pair);
      }
    } catch {
      // Non-fatal — pair selector will show manual input fallback
    }
  }, [selectedPair]);

  useEffect(() => {
    void loadAgents();
    const timer = window.setInterval(() => void loadAgents(), 10_000);
    return () => window.clearInterval(timer);
  }, [loadAgents]);

  // --- Poll interlock state every 5s ---
  const loadInterlock = useCallback(async () => {
    try {
      const state = await supervisionService.getInterlock();
      setInterlockState(state);
    } catch {
      // Fail-closed: leave state as null so InterlockBanner treats it as PAUSED
      setInterlockState(null);
    } finally {
      setInterlockLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadInterlock();
    const timer = window.setInterval(() => void loadInterlock(), 5_000);
    return () => window.clearInterval(timer);
  }, [loadInterlock]);

  // --- Poll pending proposals every 10s ---
  const loadProposals = useCallback(async () => {
    try {
      const resp = await supervisionService.listProposals();
      setProposals(resp.proposals ?? []);
      setProposalsCount(resp.count ?? 0);
    } catch {
      // Non-fatal — keep showing previous data
    } finally {
      setProposalsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProposals();
    const timer = window.setInterval(() => void loadProposals(), 10_000);
    return () => window.clearInterval(timer);
  }, [loadProposals]);

  // --- Inspect (user-triggered, NOT polled — creates proposals as side effect) ---
  const handleInspect = useCallback(async () => {
    if (!selectedPair) return;
    setInspecting(true);
    setInspectError(null);
    try {
      const result = await supervisionService.inspect(selectedPair);
      setInspectResult(result);
      // Refresh proposals since inspect may have created one
      void loadProposals();
    } catch (err) {
      setInspectError(err instanceof Error ? err.message : 'Inspection failed');
    } finally {
      setInspecting(false);
    }
  }, [selectedPair, loadProposals]);

  // --- Interlock actions ---
  const handleEngage = useCallback(async () => {
    try {
      const resp = await supervisionService.engageInterlock('Manual engage via NWI Supervision page');
      setInterlockAction(resp);
      setInterlockState({ state: resp.state });
      void loadProposals();
    } catch {
      // 403 or network error — re-fetch to get true state
      void loadInterlock();
    }
  }, [loadInterlock, loadProposals]);

  const handleResume = useCallback(async () => {
    try {
      const resp = await supervisionService.resumeInterlock('Manual resume via NWI Supervision page');
      setInterlockAction(resp);
      setInterlockState({ state: resp.state });
    } catch {
      void loadInterlock();
    }
  }, [loadInterlock]);

  const availablePairs = useMemo(
    () => Array.from(new Set(agents.map((a) => a.pair))),
    [agents],
  );

  return (
    <div className="px-5 py-7 sm:px-8 lg:px-10 lg:py-9">
      {/* Interlock banner — full width, always visible */}
      <InterlockBanner
        state={interlockState}
        actionResponse={interlockAction}
        loading={interlockLoading}
        onEngage={handleEngage}
        onResume={handleResume}
      />

      {/* Inspect error */}
      {inspectError && (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <span className="font-semibold">Inspection failed.</span> {inspectError}
          </div>
        </div>
      )}

      {/* Main grid: left column (agent picker + cards), right column (proposals) */}
      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.65fr)_minmax(340px,0.85fr)]">
        {/* Left column */}
        <div className="space-y-6">
          {/* Agent picker + inspect button */}
          <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <h2 className="font-semibold text-white">Agent inspection</h2>
                <p className="mt-1 text-xs text-slate-500">
                  Select a pair and inspect to get health, metrics, and a supervision recommendation.
                </p>
              </div>
              <div className="flex items-center gap-3">
                <select
                  value={selectedPair}
                  onChange={(e) => setSelectedPair(e.target.value)}
                  className="rounded-lg border border-white/10 bg-[#0b1727] px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan-400/40"
                >
                  {availablePairs.length === 0 && (
                    <option value="">No agents detected</option>
                  )}
                  {availablePairs.map((pair) => (
                    <option key={pair} value={pair}>{pair}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={handleInspect}
                  disabled={!selectedPair || inspecting}
                  className="flex items-center gap-2 rounded-lg border border-cyan-400/25 bg-cyan-400/10 px-4 py-2 text-sm font-medium text-cyan-200 transition-colors hover:bg-cyan-400/20 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Radar className={`h-4 w-4 ${inspecting ? 'animate-pulse' : ''}`} />
                  {inspecting ? 'Inspecting…' : 'Inspect now'}
                </button>
              </div>
            </div>
            {availablePairs.length === 0 && (
              <p className="mt-3 text-xs text-slate-600">
                No live agents detected from the operations snapshot. You can still select a pair manually
                if you know the agent is running.
              </p>
            )}
            {/* Manual pair input fallback */}
            <div className="mt-3 flex items-center gap-2">
              <input
                type="text"
                placeholder="Or enter pair manually (e.g. XBTUSD)"
                value={selectedPair}
                onChange={(e) => setSelectedPair(e.target.value.toUpperCase())}
                className="w-full max-w-xs rounded-lg border border-white/10 bg-[#0b1727] px-3 py-1.5 text-xs text-slate-300 outline-none focus:border-cyan-400/40"
              />
            </div>
          </div>

          {/* Health card */}
          <AgentHealthCard health={inspectResult?.health ?? null} />

          {/* Metrics card */}
          <MetricsCard metrics={inspectResult?.metrics ?? null} />

          {/* Recommendation card */}
          <RecommendationCard
            recommendation={inspectResult?.recommendation ?? null}
            proposal={inspectResult?.proposal ?? null}
          />
        </div>

        {/* Right column — pending proposals */}
        <div className="space-y-6">
          <PendingProposalsList
            proposals={proposals}
            count={proposalsCount}
            loading={proposalsLoading}
            onProposalsChanged={loadProposals}
          />
          <div className="rounded-2xl border border-white/8 bg-[#0d192a] p-5">
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <RefreshCw className="h-3.5 w-3.5" />
              Interlock polled every 5s · proposals every 10s
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-slate-600">
              Inspect is user-triggered only — it can create a real command proposal as a side effect.
              Auto-polling inspect would silently create proposals, which violates the human-decides-when
              design constraint.
            </p>
          </div>
          <AuditFeed />
        </div>
      </section>
    </div>
  );
}
