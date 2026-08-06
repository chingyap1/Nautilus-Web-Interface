import { TrendingDown, TrendingUp, BarChart3, Radio } from 'lucide-react';
import type { MetricSnapshot } from '@/services/supervisionService';

function formatPct(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${value.toFixed(digits)}%`;
}

function formatMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: 2,
  }).format(value);
}

function MetricRow({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex items-center justify-between border-t border-white/6 py-3">
      <span className="text-xs text-slate-500">{label}</span>
      <div className="text-right">
        <span className="text-sm font-semibold text-white">{value}</span>
        {sub && <span className="ml-2 text-[11px] text-slate-600">{sub}</span>}
      </div>
    </div>
  );
}

export default function MetricsCard({ metrics }: { metrics: MetricSnapshot | null }) {
  if (!metrics) {
    return (
      <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5">
        <div className="flex items-center gap-3">
          <BarChart3 className="h-5 w-5 text-slate-600" />
          <h2 className="font-semibold text-white">Performance metrics</h2>
        </div>
        <div className="mt-5 rounded-xl border border-dashed border-white/10 px-4 py-8 text-center">
          <p className="text-sm text-slate-400">No metrics available</p>
          <p className="mt-1 text-xs text-slate-600">Run an inspection to see equity, drawdown, and volatility.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <BarChart3 className="h-5 w-5 text-cyan-300" />
          <h2 className="font-semibold text-white">Performance metrics</h2>
        </div>
        {metrics.timestamp && (
          <span className="text-[11px] text-slate-600">
            {new Intl.DateTimeFormat('en-US', {
              month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
            }).format(new Date(metrics.timestamp))}
          </span>
        )}
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <div className="rounded-xl border border-white/7 bg-[#0b1727] p-4">
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-slate-600">
            <TrendingUp className="h-3 w-3" /> Latest equity
          </div>
          <div className="mt-2 text-xl font-semibold text-white">{formatMoney(metrics.latest_equity)}</div>
          <div className="mt-1 text-[11px] text-slate-600">Peak: {formatMoney(metrics.peak_equity)}</div>
        </div>
        <div className="rounded-xl border border-white/7 bg-[#0b1727] p-4">
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-slate-600">
            <TrendingDown className="h-3 w-3" /> Current drawdown
          </div>
          <div
            className={`mt-2 text-xl font-semibold ${
              metrics.current_drawdown_pct > 5 ? 'text-rose-300' : 'text-amber-300'
            }`}
          >
            {formatPct(metrics.current_drawdown_pct)}
          </div>
          <div className="mt-1 text-[11px] text-slate-600">Max: {formatPct(metrics.max_drawdown_pct)}</div>
        </div>
      </div>

      <div className="mt-2">
        <MetricRow label="Total return" value={formatPct(metrics.total_return_pct)} />
        <MetricRow
          label="Annualized volatility"
          value={metrics.annualized_volatility != null ? formatPct(metrics.annualized_volatility) : '—'}
        />
        <MetricRow label="Fills" value={String(metrics.num_fills)} />
        <MetricRow
          label="Commands"
          value={String(metrics.command_count)}
          sub={`${metrics.rejected_command_count} rejected`}
        />
      </div>
    </div>
  );
}
