import { Link } from 'wouter';
import { useOperationsSnapshot } from '../useOperationsSnapshot';
import { isPaperMode } from '../types';
import { supervisionService, type InterlockState } from '@/services/supervisionService';
import { useEffect, useState } from 'react';

export default function StatusPage() {
  const { snapshot, error, loading } = useOperationsSnapshot();
  const [interlock, setInterlock] = useState<InterlockState | null>(null);

  useEffect(() => {
    let cancelled = false;
    void supervisionService
      .getInterlock()
      .then((state) => {
        if (!cancelled) setInterlock(state);
      })
      .catch(() => {
        if (!cancelled) setInterlock(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const paper = isPaperMode(snapshot);
  const attention = snapshot?.command_pipeline.attention_count ?? 0;
  const authority = snapshot?.execution.authority_status ?? '—';
  const mode = snapshot?.execution.mode ?? '—';
  const agentCount = snapshot?.agents.length ?? 0;
  const interlockLabel =
    interlock == null ? 'unknown (fail-closed)' : interlock.state.toUpperCase();

  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight text-white">
          {loading ? 'Checking paper health…' : paper ? 'Paper path is reachable' : 'Not paper mode'}
        </h2>
        <p className="max-w-prose text-sm leading-relaxed text-[var(--mops-muted)]">
          One glance at agent authority, execution mode, and Supervisor interlock. Mutations stay on
          authenticated NWI APIs.
        </p>
      </div>

      {error ? (
        <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      <dl className="grid gap-3 sm:grid-cols-2">
        <StatusChip label="Mode" value={mode.toUpperCase()} emphasis={paper ? 'ok' : 'warn'} />
        <StatusChip label="Authority" value={authority} />
        <StatusChip label="Agents" value={String(agentCount)} />
        <StatusChip label="Interlock" value={interlockLabel} emphasis={interlock?.state === 'paused' ? 'warn' : 'ok'} />
      </dl>

      {attention > 0 ? (
        <Link
          className="flex min-h-11 items-center justify-between rounded-xl border border-amber-400/25 bg-amber-400/10 px-4 py-3 text-sm font-medium text-amber-100"
          href="/m/approvals"
        >
          <span>{attention} command(s) need attention</span>
          <span aria-hidden>→</span>
        </Link>
      ) : (
        <p className="text-sm text-[var(--mops-muted)]">No command-pipeline attention items.</p>
      )}
    </section>
  );
}

function StatusChip({
  label,
  value,
  emphasis,
}: {
  label: string;
  value: string;
  emphasis?: 'ok' | 'warn';
}) {
  const tone =
    emphasis === 'warn'
      ? 'border-amber-400/25 bg-amber-400/5 text-amber-100'
      : emphasis === 'ok'
        ? 'border-emerald-400/20 bg-emerald-400/5 text-emerald-100'
        : 'border-[var(--mops-border)] bg-[var(--mops-panel)] text-white';

  return (
    <div className={`rounded-xl border px-4 py-3 ${tone}`}>
      <dt className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--mops-muted)]">
        {label}
      </dt>
      <dd className="mt-1 text-base font-semibold tabular-nums">{value}</dd>
    </div>
  );
}
