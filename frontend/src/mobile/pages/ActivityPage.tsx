import { useEffect, useState } from 'react';
import { supervisionService, type AuditEntry } from '@/services/supervisionService';
import api from '@/lib/api';

/**
 * P1 scaffold: supervision audit feed + recent durable commands from snapshot.
 */
export default function ActivityPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [commands, setCommands] = useState<Array<Record<string, unknown>>>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [audit, snapshot] = await Promise.all([
          supervisionService.getAuditLog(),
          api.get<{ recent_commands: Array<Record<string, unknown>> }>('/api/operations/snapshot'),
        ]);
        if (cancelled) return;
        setEntries(audit.entries);
        setCommands(snapshot.recent_commands ?? []);
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
  }, []);

  return (
    <section className="space-y-5">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight text-white">Activity</h2>
        <p className="text-sm text-[var(--mops-muted)]">
          Recent paper commands and supervision audit entries.
        </p>
      </div>

      {loading ? <p className="text-sm text-[var(--mops-muted)]">Loading…</p> : null}
      {error ? (
        <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      <div className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--mops-muted)]">
          Recent commands
        </h3>
        {commands.length === 0 && !loading ? (
          <p className="text-sm text-[var(--mops-muted)]">No recent commands.</p>
        ) : null}
        <ul className="space-y-2">
          {commands.slice(0, 12).map((cmd, idx) => {
            const id = String(cmd.command_id ?? cmd.id ?? idx);
            return (
              <li
                className="rounded-lg border border-[var(--mops-border)] bg-[var(--mops-panel)] px-3 py-2 text-sm"
                key={id}
              >
                <span className="font-medium text-white">
                  {String(cmd.command_type ?? cmd.type ?? 'command')}
                </span>
                <span className="ml-2 text-[var(--mops-muted)]">
                  {String(cmd.status ?? '')}
                </span>
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
