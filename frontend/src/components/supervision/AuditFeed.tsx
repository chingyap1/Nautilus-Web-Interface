import { useEffect, useState, useCallback } from 'react';
import { ScrollText, RefreshCw } from 'lucide-react';
import { supervisionService, type AuditEntry } from '@/services/supervisionService';

const ACTION_LABELS: Record<string, { label: string; color: string }> = {
  interlock_engage: { label: 'Pause', color: 'text-amber-300' },
  interlock_resume: { label: 'Resume', color: 'text-emerald-300' },
  approve: { label: 'Approve', color: 'text-cyan-300' },
  dispatch: { label: 'Dispatch', color: 'text-rose-300' },
  reject: { label: 'Reject', color: 'text-slate-400' },
  inspect: { label: 'Inspect', color: 'text-slate-400' },
};

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(date);
}

export default function AuditFeed() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAudit = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await supervisionService.getAuditLog();
      setEntries(res.entries);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit log');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAudit();
    const interval = setInterval(() => void fetchAudit(), 15000);
    return () => clearInterval(interval);
  }, [fetchAudit]);

  return (
    <div className="rounded-2xl border border-white/8 bg-[#111c2e] p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <ScrollText className="h-5 w-5 text-slate-400" />
          <div>
            <h2 className="font-semibold text-white">Audit feed</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              Read-only activity log · auto-refreshes every 15s
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void fetchAudit()}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-lg border border-white/8 bg-white/5 px-2.5 py-1.5 text-xs text-slate-400 transition-colors hover:bg-white/10 disabled:opacity-40"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
          {error}
        </div>
      )}

      {!error && entries.length === 0 && !loading && (
        <div className="mt-5 rounded-xl border border-dashed border-white/10 px-4 py-8 text-center">
          <p className="text-sm text-slate-400">No audit entries yet</p>
          <p className="mt-1 text-xs text-slate-600">
            Actions (inspect, approve, dispatch, reject, engage, resume) will appear here.
          </p>
        </div>
      )}

      {entries.length > 0 && (
        <div className="mt-4 max-h-96 space-y-1.5 overflow-y-auto">
          {entries.map((entry) => {
            const meta = ACTION_LABELS[entry.action] ?? { label: entry.action, color: 'text-slate-400' };
            return (
              <div
                key={entry.audit_id}
                className="flex items-start gap-3 rounded-lg border border-white/5 bg-[#0b1727] px-3 py-2"
              >
                <span className={`mt-0.5 shrink-0 text-xs font-semibold ${meta.color}`}>
                  {meta.label}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="font-medium text-slate-300">{entry.actor}</span>
                    <span className="text-slate-600">·</span>
                    <span className="font-mono text-[10px] text-slate-600">{entry.audit_id}</span>
                  </div>
                  {Object.keys(entry.detail).length > 0 && (
                    <div className="mt-0.5 truncate text-[11px] text-slate-500">
                      {Object.entries(entry.detail)
                        .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
                        .join(' · ')}
                    </div>
                  )}
                </div>
                <span className="shrink-0 text-[10px] text-slate-600">
                  {formatTimestamp(entry.timestamp)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
