import StatusPage from './pages/StatusPage';
import ApprovalsPage from './pages/ApprovalsPage';
import { useIsIpadShell } from './useMinWidth';

/**
 * P4 — iPad (≥900px) two-pane Status | Approvals workspace (§6 / roadmap P4).
 * Phone keeps a single focused page.
 */
export default function StatusApprovalsSplit({ focus }: { focus: 'status' | 'approvals' }) {
  const wide = useIsIpadShell();

  if (!wide) {
    return focus === 'status' ? <StatusPage /> : <ApprovalsPage />;
  }

  return (
    <div
      className="grid min-h-0 grid-cols-1 gap-4 lg:grid-cols-2 lg:gap-5"
      data-testid="mops-status-approvals-split"
    >
      <section
        aria-label="Status pane"
        className={`min-w-0 rounded-xl border px-3 py-3 sm:px-4 ${
          focus === 'status'
            ? 'border-sky-400/30 bg-sky-400/[0.04]'
            : 'border-[var(--mops-border)] bg-transparent'
        }`}
      >
        <StatusPage />
      </section>
      <section
        aria-label="Approvals pane"
        className={`min-w-0 rounded-xl border px-3 py-3 sm:px-4 ${
          focus === 'approvals'
            ? 'border-sky-400/30 bg-sky-400/[0.04]'
            : 'border-[var(--mops-border)] bg-transparent'
        }`}
      >
        <ApprovalsPage />
      </section>
    </div>
  );
}
