import { describe, it, expect } from 'vitest';
import { buildObservationNotes } from '@/mobile/observation';
import type { OperationsSnapshot } from '@/mobile/types';

const baseSnapshot: OperationsSnapshot = {
  generated_at: '2026-08-08T00:00:00Z',
  execution: {
    mode: 'paper',
    venue: 'KRAKEN',
    authority: 'nautilus_agent',
    authority_status: 'online',
    can_route_commands: true,
  },
  agents: [],
  command_pipeline: { in_flight_count: 0, attention_count: 0 },
  recent_commands: [],
};

describe('buildObservationNotes (Gate 5 hooks)', () => {
  it('always includes the Gate 5 hook info note', () => {
    const notes = buildObservationNotes({
      snapshot: baseSnapshot,
      interlock: { state: 'resumed' },
      interlockReachable: true,
      recentCommands: [],
      auditEntries: [],
    });
    expect(notes.some((n) => n.id === 'gate5-hook' && n.source === 'gate5')).toBe(true);
  });

  it('alerts when interlock is unreachable', () => {
    const notes = buildObservationNotes({
      snapshot: baseSnapshot,
      interlock: null,
      interlockReachable: false,
      recentCommands: [],
      auditEntries: [],
    });
    expect(notes.find((n) => n.id === 'interlock-unreachable')?.severity).toBe('alert');
  });

  it('watches paused interlock and stale agents', () => {
    const notes = buildObservationNotes({
      snapshot: {
        ...baseSnapshot,
        agents: [
          {
            agent_id: 'agent-btc',
            pair: 'XBTUSD',
            freshness: 'stale',
            last_heartbeat: '2026-08-08T10:00:00Z',
          },
        ],
      },
      interlock: { state: 'paused', reason: 'on-call' },
      interlockReachable: true,
      recentCommands: [],
      auditEntries: [],
    });
    expect(notes.some((n) => n.id === 'interlock-paused')).toBe(true);
    expect(notes.some((n) => n.id === 'agent-agent-btc-stale')).toBe(true);
  });

  it('surfaces kill/flatten commands as alerts', () => {
    const notes = buildObservationNotes({
      snapshot: baseSnapshot,
      interlock: { state: 'resumed' },
      interlockReachable: true,
      recentCommands: [{ command_id: 'c1', command_type: 'kill_switch', status: 'validated' }],
      auditEntries: [],
    });
    expect(notes.some((n) => n.id === 'cmd-c1' && n.severity === 'alert')).toBe(true);
  });
});
