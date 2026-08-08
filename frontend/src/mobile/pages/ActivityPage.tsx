import { useEffect, useMemo, useState } from 'react';
import {
  supervisionService,
  type AuditEntry,
  type InterlockState,
} from '@/services/supervisionService';
import { useOperationsSnapshot } from '../useOperationsSnapshot';
import { buildObservationNotes, type ObservationNote } from '../observation';

/**
 * Activity + P4 Gate 5 observation hooks — audit, recent commands, derived watch list.
 */
export default function ActivityPage() {
  const { snapshot } = useOperationsSnapshot();
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [commands, setCommands] = useState<Array<Record<string, unknown>>>([]);
  const [interlock, setInterlock] = useState<InterlockState | null>(null);
  const [interlockReachable, setInterlockReachable] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [audit, lock] = await Promise.all([
          supervisionService.getAuditLog(),
          supervisionService.getInterlock().then(
            (state) => ({ ok: true as const, state }),
            () => ({ ok: false as const, state: null }),
          ),
        ]);
        if (cancelled) return;
        setEntries(audit.entries);
        setCommands(snapshot?.recent_commands ?? []);
        setInterlock(lock.state);
        setInterlockReachable(lock.ok);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load activity');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [snapshot?.recent_commands]);

  const observations = useMemo(
    () =>
      buildObservationNotes({
        snapshot,
        interlock,
        interlockReachable,
        recentCommands: commands.length ? commands : (snapshot?.recent_commands ?? []),
        auditEntries: entries,
      }),
    [snapshot, interlock, interlockReachable, commands, entries],
  );

  return (
    <section className="space-y-5">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight text-white">Activity</h2>
        <p className="text-sm text-[var(--mops-muted)]">
          Recent paper commands, supervision audit, and Gate 5 observation hooks.
        </p>
      </div>

      {loading ? <p className="text-sm text-[var(--mops-muted)]">Loading…</p> : null}
      {error ? (
        <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      <ObservationSection notes={observations} />

      <div className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--mops-muted)]">
          Recent commands
        </h3>
        {commands.length === 0 && !loading ? (
          <p className="text-sm text-[var(--mops-muted)]">No recent commands.</p>
        ) : null}
        <ul className="space-y-2">
          {(commands.length ? commands : snapshot?.recent_commands ?? []).slice(0, 12).map((cmd, idx) => {
            const id = String(cmd.command_id ?? cmd.id ?? idx);
            return (
              <li
                className="rounded-lg border border-[var(--mops-border)] bg-[var(--mops-panel)] px-3 py-2 text-sm"
                key={id}
              >
                <span className="font-medium text-white">
                  {String(cmd.command_type ?? cmd.type ?? 'command')}
                </span>
                <span className="ml-2 text-[var(--mops-muted)]">{String(cmd.status ?? '')}</span>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--mops-muted)]">
          Supervision audit
        </h3>
        {entries.length === 0 && !loading ? (
          <p className="text-sm text-[var(--mops-muted)]">Audit feed empty.</p>
        ) : null}
        <ul className="space-y-2">
          {entries.slice(0, 20).map((e) => (
            <li
              className="rounded-lg border border-[var(--mops-border)] bg-[var(--mops-panel)] px-3 py-2 text-sm"
              key={e.audit_id}
            >
              <p className="font-medium text-white">{e.action}</p>
              <p className="mt-0.5 text-xs text-[var(--mops-muted)]">
                {e.actor} · {e.timestamp}
              </p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function ObservationSection({ notes }: { notes: ObservationNote[] }) {
  return (
    <div className="space-y-2" data-testid="mops-observation-hooks">
      <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--mops-muted)]">
        Observation hooks
      </h3>
      <ul className="space-y-2">
        {notes.map((note) => {
          const tone =
            note.severity === 'alert'
              ? 'border-rose-400/30 bg-rose-500/10 text-rose-100'
              : note.severity === 'watch'
                ? 'border-amber-400/30 bg-amber-400/10 text-amber-100'
                : 'border-[var(--mops-border)] bg-[var(--mops-panel)] text-[var(--mops-muted)]';
          return (
            <li className={`rounded-lg border px-3 py-2 text-sm ${tone}`} key={note.id}>
              <div className="flex items-start justify-between gap-2">
                <p className="font-medium text-white">{note.title}</p>
                <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide opacity-70">
                  {note.source}
                </span>
              </div>
              <p className="mt-1 text-xs leading-relaxed opacity-90">{note.detail}</p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
