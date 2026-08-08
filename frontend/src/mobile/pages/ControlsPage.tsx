import { useOperationsSnapshot } from '../useOperationsSnapshot';
import { isPaperMode } from '../types';

/**
 * P3 scaffold: paper emergency controls.
 * Kill / flatten / interlock engage only when execution.mode === paper (§7.3 / §8.2).
 */
export default function ControlsPage() {
  const { snapshot, loading, error } = useOperationsSnapshot();
  const paper = isPaperMode(snapshot);

  return (
    <section className="space-y-4">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight text-white">Controls</h2>
        <p className="text-sm text-[var(--mops-muted)]">
          Paper emergency actions only. Pause maps to Supervisor interlock engage — not a missing
          agent pause command.
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

      {paper ? (
        <div className="space-y-3">
          <ControlStub
            danger
            label="Kill switch"
            path="POST /api/kill-switch"
            note="Two-step confirm required in P3"
          />
          <ControlStub
            danger
            label="Flatten strategy"
            path="POST /api/strategies/{id}/flatten"
            note="Requires strategy id; two-step confirm in P3"
          />
          <ControlStub
            label="Pause Supervisor commands"
            path="POST /api/supervision/interlock/engage"
            note="Interlock engage — honest label, not agent halt"
          />
          <ControlStub
            label="Resume Supervisor commands"
            path="POST /api/supervision/interlock/resume"
            note="Admin + reason; Supervisor cannot self-resume"
          />
        </div>
      ) : null}
    </section>
  );
}

function ControlStub({
  label,
  path,
  note,
  danger,
}: {
  label: string;
  path: string;
  note: string;
  danger?: boolean;
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
      <button
        className="mt-3 min-h-11 w-full rounded-lg border border-white/10 bg-white/5 text-sm font-medium text-[var(--mops-muted)]"
        disabled
        type="button"
      >
        Scaffold — action wired in P3
      </button>
    </div>
  );
}
