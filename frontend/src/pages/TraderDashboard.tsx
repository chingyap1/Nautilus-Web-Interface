import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, AlertTriangle, ArrowRight, Bot, ChevronRight, CircleDollarSign,
  Clock3, Landmark, ListOrdered, Radio, RefreshCw, WalletCards, Wifi, WifiOff,
  type LucideIcon,
} from 'lucide-react';

import { useWebSocket } from '@/hooks/useWebSocket';
import nautilusService, {
  type AgentSnapshot,
  type CommandSnapshot,
  type OperationsSnapshot,
} from '@/services/nautilusService';

const STATUS_STYLES: Record<string, string> = {
  PENDING: 'border-slate-600 bg-slate-700/50 text-slate-200',
  VALIDATED: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
  SUBMITTED: 'border-blue-500/30 bg-blue-500/10 text-blue-300',
  ACCEPTED: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
  PARTIALLY_FILLED: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  FILLED: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  CANCELLING: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  CANCELLED: 'border-slate-600 bg-slate-700/50 text-slate-300',
  REJECTED: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
  FAILED: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
  EXPIRED: 'border-orange-500/30 bg-orange-500/10 text-orange-300',
  RECONCILIATION_REQUIRED: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
};

function formatMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: 2,
  }).format(value);
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(date);
}

function heartbeatAge(agent: AgentSnapshot): string {
  if (agent.heartbeat_age_seconds == null) return 'Unknown';
  return agent.heartbeat_age_seconds < 60
    ? `${agent.heartbeat_age_seconds}s ago`
    : `${Math.floor(agent.heartbeat_age_seconds / 60)}m ago`;
}

function modeStyle(mode: string): string {
  if (mode === 'live') return 'border-rose-400/40 bg-rose-500/15 text-rose-200';
  if (mode === 'paper') return 'border-amber-400/40 bg-amber-500/15 text-amber-100';
  return 'border-cyan-400/40 bg-cyan-500/15 text-cyan-100';
}

function StatusDot({ online }: { online: boolean }) {
  return <span className="relative flex h-2.5 w-2.5">
    {online && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-50" />}
    <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${online ? 'bg-emerald-400' : 'bg-slate-500'}`} />
  </span>;
}

function MetricCard({ label, value, detail, icon: Icon, tone = 'cyan' }: {
  label: string; value: string; detail: string; icon: LucideIcon;
  tone?: 'cyan' | 'emerald' | 'amber' | 'violet';
}) {
  const tones = {
    cyan: 'bg-cyan-400/10 text-cyan-300 ring-cyan-400/20',
    emerald: 'bg-emerald-400/10 text-emerald-300 ring-emerald-400/20',
    amber: 'bg-amber-400/10 text-amber-300 ring-amber-400/20',
    violet: 'bg-violet-400/10 text-violet-300 ring-violet-400/20',
  };
  return <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5 shadow-[0_18px_50px_rgba(2,8,23,0.16)]">
    <div className="flex items-start justify-between gap-4">
      <div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">{label}</p><p className="mt-3 text-2xl font-semibold tracking-tight text-white">{value}</p></div>
      <div className={`rounded-xl p-2.5 ring-1 ${tones[tone]}`}><Icon className="h-5 w-5" /></div>
    </div>
    <p className="mt-4 text-xs text-slate-500">{detail}</p>
  </div>;
}

function CommandStatus({ status }: { status: string }) {
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-semibold tracking-wide ${STATUS_STYLES[status] ?? STATUS_STYLES.PENDING}`}>
    {status.replaceAll('_', ' ')}
  </span>;
}

function CommandRow({ command }: { command: CommandSnapshot }) {
  const descriptor = command.instrument || command.strategy_id || 'Account-wide';
  return <tr className="border-t border-white/6 transition-colors hover:bg-white/[0.025]">
    <td className="px-5 py-4"><div className="font-medium capitalize text-slate-100">{command.command_type.replaceAll('_', ' ')}</div><div className="mt-1 font-mono text-[11px] text-slate-600">{command.command_id.slice(0, 12)}</div></td>
    <td className="px-5 py-4"><div className="text-sm text-slate-300">{descriptor}</div>{(command.side || command.quantity) && <div className="mt-1 text-xs text-slate-500">{[command.side, command.quantity, command.order_type].filter(Boolean).join(' · ')}</div>}</td>
    <td className="px-5 py-4"><CommandStatus status={command.status} /></td>
    <td className="px-5 py-4 text-right text-xs text-slate-500">{formatTimestamp(command.created_at)}</td>
  </tr>;
}

export default function TraderDashboard() {
  const { connected: wsConnected, reconnect } = useWebSocket();
  const [snapshot, setSnapshot] = useState<OperationsSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSnapshot = useCallback(async (quiet = false) => {
    if (!quiet) setRefreshing(true);
    try {
      setSnapshot(await nautilusService.getOperationsSnapshot());
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The operations snapshot is unavailable.');
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadSnapshot(true);
    const timer = window.setInterval(() => void loadSnapshot(true), 5_000);
    return () => window.clearInterval(timer);
  }, [loadSnapshot]);

  const totals = useMemo(() => (snapshot?.agents ?? []).reduce((total, agent) => ({
    balance: total.balance + agent.balance_usd,
    pnl: total.pnl + agent.unrealised_pnl,
    positions: total.positions + agent.open_positions,
    fills: total.fills + agent.num_fills,
  }), { balance: 0, pnl: 0, positions: 0, fills: 0 }), [snapshot]);

  const authorityOnline = snapshot?.execution.authority_status === 'online';
  const hasAuthoritativeState = (snapshot?.agents.length ?? 0) > 0;
  const executionMode = snapshot?.execution.mode ?? 'paper';
  const attentionCount = snapshot?.command_pipeline.attention_count ?? 0;

  return <div className="px-5 py-7 sm:px-8 lg:px-10 lg:py-9">
          <div className="mb-5 flex justify-end gap-2"><div className={`rounded-lg border px-3 py-2 text-xs font-semibold uppercase tracking-[0.14em] ${modeStyle(executionMode)}`}>{executionMode} execution</div><button type="button" onClick={() => void loadSnapshot()} className="rounded-lg border border-white/10 bg-white/5 p-2.5 text-slate-400 hover:bg-white/10 hover:text-white" aria-label="Refresh operations snapshot"><RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} /></button></div>
          <section className={`mb-7 overflow-hidden rounded-2xl border ${executionMode === 'live' ? 'border-rose-500/30 bg-rose-500/[0.07]' : 'border-amber-500/25 bg-amber-500/[0.06]'}`}>
            <div className="flex flex-col justify-between gap-5 p-5 sm:flex-row sm:items-center"><div className="flex items-start gap-4"><div className={`mt-0.5 rounded-xl p-2.5 ${executionMode === 'live' ? 'bg-rose-400/15 text-rose-300' : 'bg-amber-400/15 text-amber-300'}`}><Landmark className="h-5 w-5" /></div><div><div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold text-white">{snapshot?.execution.venue ?? 'Kraken'} · {executionMode.toUpperCase()}</h2><span className="rounded-full border border-white/10 bg-black/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-slate-400">Agent-owned</span></div><p className="mt-1 max-w-2xl text-sm leading-relaxed text-slate-400">FastAPI accepts durable requests. Venue submission and authoritative account state remain exclusively inside the Nautilus execution process.</p></div></div><div className="flex shrink-0 items-center gap-3 rounded-xl border border-white/8 bg-black/10 px-4 py-3"><StatusDot online={authorityOnline} /><div><div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Execution authority</div><div className={`text-sm font-semibold capitalize ${authorityOnline ? 'text-emerald-300' : 'text-amber-300'}`}>{snapshot?.execution.authority_status ?? 'checking'}</div></div></div></div>
          </section>

          {error && <div className="mb-7 flex items-start gap-3 rounded-xl border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-200"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><div><span className="font-semibold">Control-plane snapshot unavailable.</span> {error} Cached values, if shown, may be stale.</div></div>}

          <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Agent balance" value={hasAuthoritativeState ? formatMoney(totals.balance) : '—'} detail="Source · Nautilus heartbeat" icon={CircleDollarSign} />
            <MetricCard label="Unrealised P&L" value={hasAuthoritativeState ? formatMoney(totals.pnl) : '—'} detail="Source · Nautilus heartbeat" icon={Activity} tone={totals.pnl >= 0 ? 'emerald' : 'amber'} />
            <MetricCard label="Open positions" value={hasAuthoritativeState ? String(totals.positions) : '—'} detail="Agent-reported account state" icon={WalletCards} tone="violet" />
            <MetricCard label="Commands in flight" value={snapshot ? String(snapshot.command_pipeline.in_flight_count) : '—'} detail={`${snapshot?.command_pipeline.processing_files ?? 0} currently claimed by agent`} icon={Radio} tone="amber" />
          </section>

          <section className="mt-7 grid gap-6 xl:grid-cols-[minmax(0,1.65fr)_minmax(340px,0.85fr)]">
            <div className="overflow-hidden rounded-2xl border border-white/8 bg-[#111c2e]">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/7 px-5 py-4"><div><h2 className="font-semibold text-white">Durable command flow</h2><p className="mt-1 text-xs text-slate-500">Requested state is not execution state. Agent results advance each command.</p></div><a href="/trader/orders" className="flex items-center gap-1 text-xs font-medium text-cyan-300 hover:text-cyan-200">Open command center <ChevronRight className="h-3.5 w-3.5" /></a></div>
              <div className="overflow-x-auto"><table className="w-full min-w-[680px]"><thead><tr className="text-left text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-600"><th className="px-5 py-3">Command</th><th className="px-5 py-3">Target</th><th className="px-5 py-3">State</th><th className="px-5 py-3 text-right">Created</th></tr></thead><tbody>{snapshot?.recent_commands.map(command => <CommandRow key={command.command_id} command={command} />)}</tbody></table>{!loading && (snapshot?.recent_commands.length ?? 0) === 0 && <div className="border-t border-white/6 px-5 py-12 text-center"><ListOrdered className="mx-auto h-7 w-7 text-slate-700" /><p className="mt-3 text-sm text-slate-400">No durable commands recorded</p><p className="mt-1 text-xs text-slate-600">New order and lifecycle requests will appear here.</p></div>}</div>
            </div>

            <div className="space-y-6">
              <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5"><div className="flex items-center justify-between"><div><h2 className="font-semibold text-white">Execution agents</h2><p className="mt-1 text-xs text-slate-500">Agent-authored heartbeat state</p></div><Bot className="h-5 w-5 text-cyan-300" /></div><div className="mt-5 space-y-3">{snapshot?.agents.map(agent => <div key={agent.agent_id} className="rounded-xl border border-white/7 bg-[#0b1727] p-4"><div className="flex items-start justify-between gap-3"><div className="flex items-center gap-3"><StatusDot online={agent.freshness === 'online'} /><div><div className="text-sm font-semibold text-slate-100">{agent.agent_id}</div><div className="mt-0.5 text-xs text-slate-500">{agent.pair} · {agent.strategy} · {agent.interval}</div></div></div><span className={`text-xs font-medium capitalize ${agent.freshness === 'online' ? 'text-emerald-300' : 'text-amber-300'}`}>{agent.freshness}</span></div><div className="mt-4 grid grid-cols-3 gap-2 border-t border-white/6 pt-3 text-center"><div><div className="text-sm font-semibold text-white">{agent.open_positions}</div><div className="text-[10px] uppercase tracking-wide text-slate-600">Positions</div></div><div><div className="text-sm font-semibold text-white">{agent.num_fills}</div><div className="text-[10px] uppercase tracking-wide text-slate-600">Fills</div></div><div><div className="text-sm font-semibold text-white">{heartbeatAge(agent)}</div><div className="text-[10px] uppercase tracking-wide text-slate-600">Heartbeat</div></div></div></div>)}{!loading && (snapshot?.agents.length ?? 0) === 0 && <div className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center"><WifiOff className="mx-auto h-6 w-6 text-slate-700" /><p className="mt-3 text-sm text-slate-400">No agent heartbeat found</p><p className="mt-1 text-xs leading-relaxed text-slate-600">Commands remain durable, but execution cannot be confirmed.</p></div>}</div></div>
              <div className={`rounded-2xl border p-5 ${attentionCount > 0 ? 'border-rose-500/25 bg-rose-500/[0.07]' : 'border-white/8 bg-[#111c2e]'}`}><div className="flex items-center gap-3"><AlertTriangle className={`h-5 w-5 ${attentionCount > 0 ? 'text-rose-300' : 'text-emerald-300'}`} /><div><div className="text-sm font-semibold text-white">{attentionCount > 0 ? `${attentionCount} command${attentionCount === 1 ? '' : 's'} need attention` : 'No command exceptions'}</div><div className="mt-1 text-xs text-slate-500">Rejected, failed, expired, or reconciliation-required states</div></div></div></div>
            </div>
          </section>

          <section className="mt-7 rounded-2xl border border-white/8 bg-[#0d192a] p-5 sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-4"><div><h2 className="font-semibold text-white">Authority path</h2><p className="mt-1 text-xs text-slate-500">Every trading action follows one observable ownership boundary.</p></div><div className="flex items-center gap-2 text-xs text-slate-500">{wsConnected ? <Wifi className="h-4 w-4 text-emerald-400" /> : <WifiOff className="h-4 w-4 text-rose-400" />} Event stream {wsConnected ? 'connected' : 'offline'}{!wsConnected && <button type="button" onClick={reconnect} className="ml-1 text-cyan-300 hover:text-cyan-200">Reconnect</button>}</div></div>
            <div className="mt-6 grid gap-3 md:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr] md:items-center">{[
              ['React / API', 'Request intent'], ['Durable command', 'Validated & persisted'],
              ['Nautilus agent', 'Risk & execution authority'], [snapshot?.execution.venue ?? 'Venue', 'Orders, account & fills'],
            ].map(([title, subtitle], index) => <div className="contents" key={title}><div className={`rounded-xl border p-4 ${index === 2 ? 'border-cyan-400/25 bg-cyan-400/[0.07]' : 'border-white/7 bg-white/[0.025]'}`}><div className="text-sm font-semibold text-slate-100">{title}</div><div className="mt-1 text-xs text-slate-600">{subtitle}</div></div>{index < 3 && <ArrowRight className="hidden h-4 w-4 text-slate-700 md:block" />}</div>)}</div>
          </section>

          <footer className="mt-7 flex flex-wrap items-center justify-between gap-3 text-[11px] text-slate-600"><div className="flex items-center gap-2"><Clock3 className="h-3.5 w-3.5" /> Snapshot {formatTimestamp(snapshot?.generated_at)}</div><div>Account values are shown only when authored by a Nautilus agent heartbeat.</div></footer>
  </div>;
}