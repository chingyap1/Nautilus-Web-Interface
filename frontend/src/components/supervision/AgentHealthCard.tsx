import { Activity, Bot, Clock3, CircleDollarSign, WalletCards } from 'lucide-react';
import type { AgentHealth } from '@/services/supervisionService';

const STATUS_COLORS: Record<string, string> = {
  healthy: 'text-emerald-300',
  stale: 'text-amber-300',
  degraded: 'text-orange-300',
  offline: 'text-rose-300',
  paused: 'text-slate-400',
};

const STATUS_DOT: Record<string, string> = {
  healthy: 'bg-emerald-400',
  stale: 'bg-amber-400',
  degraded: 'bg-orange-400',
  offline: 'bg-rose-400',
  paused: 'bg-slate-500',
};

function formatMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: 2,
  }).format(value);
}

function formatQty(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toLocaleString('en-US', { maximumFractionDigits: 8 });
}

function formatAge(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return 'Unknown';
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

export default function AgentHealthCard({ health }: { health: AgentHealth | null }) {
  if (!health) {
    return (
      <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5">
        <div className="flex items-center gap-3">
          <Bot className="h-5 w-5 text-slate-600" />
          <h2 className="font-semibold text-white">Agent health</h2>
        </div>
        <div className="mt-5 rounded-xl border border-dashed border-white/10 px-4 py-8 text-center">
          <p className="text-sm text-slate-400">No inspection result yet</p>
          <p className="mt-1 text-xs text-slate-600">Select a pair and click "Inspect now" to check agent health.</p>
        </div>
      </div>
    );
  }

  const statusColor = STATUS_COLORS[health.status] ?? STATUS_COLORS.offline;
  const dotColor = STATUS_DOT[health.status] ?? STATUS_DOT.offline;

  return (
    <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="relative flex h-2.5 w-2.5">
            {health.status === 'healthy' && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-50" />
            )}
            <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${dotColor}`} />
          </span>
          <div>
            <h2 className="font-semibold text-white">{health.agent_id}</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              {health.pair} · {health.strategy} · {health.interval}
            </p>
          </div>
        </div>
        <span className={`text-xs font-semibold uppercase tracking-wide ${statusColor}`}>
          {health.status}
        </span>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 border-t border-white/6 pt-4 sm:grid-cols-4">
        <div>
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-slate-600">
            <Clock3 className="h-3 w-3" /> Heartbeat
          </div>
          <div className="mt-1 text-sm font-semibold text-white">{formatAge(health.heartbeat_age_seconds)}</div>
        </div>
        <div>
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-slate-600">
            <CircleDollarSign className="h-3 w-3" /> Balance
          </div>
          <div className="mt-1 text-sm font-semibold text-white">{formatMoney(health.balance_usd)}</div>
        </div>
        <div>
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-slate-600">
            <Activity className="h-3 w-3" /> Unrealised P&L
          </div>
          <div
            className={`mt-1 text-sm font-semibold ${
              health.unrealised_pnl >= 0 ? 'text-emerald-300' : 'text-rose-300'
            }`}
          >
            {formatMoney(health.unrealised_pnl)}
          </div>
        </div>
        <div>
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-slate-600">
            <WalletCards className="h-3 w-3" /> Positions
          </div>
          <div className="mt-1 text-sm font-semibold text-white">{health.open_positions}</div>
        </div>
      </div>

      {health.positions && health.positions.length > 0 && (
        <div className="mt-3 border-t border-white/6 pt-3">
          <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-600">
            Position detail · latest snapshot
          </div>
          <ul className="space-y-1.5">
            {health.positions.map((pos, i) => (
              <li
                key={`${pos.instrument_id ?? 'row'}-${i}`}
                className="flex items-center justify-between gap-2 text-xs"
              >
                <span className="text-slate-300">
                  <span
                    className={
                      pos.side === 'LONG'
                        ? 'text-emerald-300'
                        : pos.side === 'SHORT'
                          ? 'text-rose-300'
                          : 'text-slate-400'
                    }
                  >
                    {pos.side ?? '—'}
                  </span>{' '}
                  <span className="text-slate-500">{formatQty(pos.quantity)}</span>{' '}
                  <span className="text-slate-200">{pos.instrument_id ?? '—'}</span>
                </span>
                <span className="text-slate-500">
                  @{formatMoney(pos.entry_price)} →{' '}
                  {pos.mark_price != null ? formatMoney(pos.mark_price) : '—'}
                  <span
                    className={`ml-2 ${
                      pos.unrealised_pnl == null
                        ? 'text-slate-500'
                        : pos.unrealised_pnl >= 0
                          ? 'text-emerald-300'
                          : 'text-rose-300'
                    }`}
                  >
                    {pos.unrealised_pnl == null ? 'uP&L —' : formatMoney(pos.unrealised_pnl)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 border-t border-white/6 pt-3 text-xs text-slate-600">
        {health.execution_mode} mode · {health.num_fills} fills
        {health.source_path && <> · source: {health.source_path}</>}
      </div>
    </div>
  );
}
