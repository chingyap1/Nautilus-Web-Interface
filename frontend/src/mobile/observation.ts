import type { AuditEntry } from '@/services/supervisionService';
import type { InterlockState } from '@/services/supervisionService';
import type { OperationsSnapshot } from './types';

/**
 * Gate 5 observation hooks — derive watch items from existing NWI reads
 * until a dedicated sustained-observation drill API exists (framework Gate 5).
 */

export type ObservationSeverity = 'info' | 'watch' | 'alert';

export interface ObservationNote {
  id: string;
  severity: ObservationSeverity;
  title: string;
  detail: string;
  source: 'interlock' | 'agent' | 'command' | 'audit' | 'gate5';
}

function cmdType(cmd: Record<string, unknown>): string {
  return String(cmd.command_type ?? cmd.type ?? '').toLowerCase();
}

function cmdStatus(cmd: Record<string, unknown>): string {
  return String(cmd.status ?? '').toLowerCase();
}

export function buildObservationNotes(input: {
  snapshot: OperationsSnapshot | null;
  interlock: InterlockState | null;
  interlockReachable: boolean;
  recentCommands: Array<Record<string, unknown>>;
  auditEntries: AuditEntry[];
}): ObservationNote[] {
  const notes: ObservationNote[] = [];

  if (!input.interlockReachable) {
    notes.push({
      id: 'interlock-unreachable',
      severity: 'alert',
      title: 'Interlock unreachable',
      detail: 'Treating Supervisor traffic as paused (fail-closed). Direct human emergency controls remain available.',
      source: 'interlock',
    });
  } else if (input.interlock?.state === 'paused') {
    notes.push({
      id: 'interlock-paused',
      severity: 'watch',
      title: 'Supervisor commands paused',
      detail: input.interlock.reason
        ? `Interlock engaged — ${input.interlock.reason}`
        : 'Interlock engaged — no new Supervisor proposals until an admin resumes.',
      source: 'interlock',
    });
  }

  const agents = input.snapshot?.agents ?? [];
  for (const raw of agents) {
    const agent = raw as Record<string, unknown>;
    const id = String(agent.agent_id ?? agent.pair ?? 'agent');
    const freshness = String(agent.freshness ?? agent.status ?? '').toLowerCase();
    if (freshness === 'stale' || freshness === 'offline' || freshness === 'degraded') {
      notes.push({
        id: `agent-${id}-${freshness}`,
        severity: freshness === 'stale' ? 'watch' : 'alert',
        title: `Agent ${id} is ${freshness}`,
        detail: `${String(agent.pair ?? '—')} · last heartbeat ${String(agent.last_heartbeat ?? 'unknown')}`,
        source: 'agent',
      });
    }
  }

  if (input.snapshot && !input.snapshot.execution.can_route_commands) {
    notes.push({
      id: 'cannot-route',
      severity: 'watch',
      title: 'Command routing unavailable',
      detail: 'Operations snapshot reports can_route_commands=false — no online paper agent to claim durable commands.',
      source: 'agent',
    });
  }

  for (const cmd of input.recentCommands.slice(0, 20)) {
    const type = cmdType(cmd);
    if (type.includes('kill') || type.includes('flatten')) {
      const id = String(cmd.command_id ?? cmd.id ?? type);
      notes.push({
        id: `cmd-${id}`,
        severity: 'alert',
        title: `${type.replaceAll('_', ' ')} · ${cmdStatus(cmd) || 'recorded'}`,
        detail: 'Paper emergency command visible in the recent pipeline — confirm agent acknowledgement on Status.',
        source: 'command',
      });
    }
  }

  for (const entry of input.auditEntries.slice(0, 30)) {
    const action = entry.action.toLowerCase();
    if (action.includes('kill') || action.includes('flatten') || action.includes('interlock')) {
      notes.push({
        id: `audit-${entry.audit_id}`,
        severity: action.includes('kill') || action.includes('flatten') ? 'alert' : 'watch',
        title: entry.action.replaceAll('_', ' '),
        detail: `${entry.actor} · ${entry.timestamp}`,
        source: 'audit',
      });
    }
  }

  // Deduplicate by id while preserving order
  const seen = new Set<string>();
  const unique = notes.filter((n) => {
    if (seen.has(n.id)) return false;
    seen.add(n.id);
    return true;
  });

  unique.push({
    id: 'gate5-hook',
    severity: 'info',
    title: 'Gate 5 observation hook',
    detail:
      'Sustained paper-observation drills (injected model/MCP/interlock outages) are not published yet. This list is the Mobile Ops hook: same NWI reads, fail-closed semantics.',
    source: 'gate5',
  });

  return unique;
}
