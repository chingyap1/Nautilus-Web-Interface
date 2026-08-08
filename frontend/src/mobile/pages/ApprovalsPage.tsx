import { useEffect, useState } from 'react';
import {
  supervisionService,
  type SupervisionProposal,
} from '@/services/supervisionService';

/**
 * P1 scaffold: read pending supervision proposals.
 * P2 will wire approve → dispatch (+ step-up) and reject per §7.2 / §8.3.
 */
export default function ApprovalsPage() {
  const [proposals, setProposals] = useState<SupervisionProposal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void supervisionService
      .listProposals()
      .then((res) => {
        if (!cancelled) {
          setProposals(res.proposals);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load proposals');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="space-y-4">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight text-white">Approvals</h2>
        <p className="text-sm text-[var(--mops-muted)]">
          Inbox for durable paper commands. Approve and dispatch remain two separate NWI steps.
        </p>
      </div>

      {loading ? <p className="text-sm text-[var(--mops-muted)]">Loading…</p> : null}
      {error ? (
        <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      {!loading && !error && proposals.length === 0 ? (
        <p className="rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-6 text-sm text-[var(--mops-muted)]">
          No pending supervision proposals.
        </p>
      ) : null}

      <ul className="space-y-3">
        {proposals.map((p) => (
          <li
            className="rounded-xl border border-[var(--mops-border)] bg-[var(--mops-panel)] px-4 py-3"
            key={p.proposal_id}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-medium text-white">{p.command_name}</p>
                <p className="mt-1 text-xs text-[var(--mops-muted)]">
                  {p.target_agent_id} · {p.status}
                </p>
              </div>
              <span className="font-mono text-[10px] text-[var(--mops-muted)]">
                {p.proposal_id.slice(0, 8)}
              </span>
            </div>
            <p className="mt-3 text-xs text-[var(--mops-muted)]">
              Mutating actions (approve / dispatch / reject) land in P2 — contract paths are ready.
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
