/** Operations snapshot shape from GET /api/operations/snapshot (§8.2 / §8.3). */
export interface OperationsSnapshot {
  generated_at: string;
  execution: {
    mode: string;
    venue: string;
    authority: string;
    authority_status: string;
    can_route_commands: boolean;
  };
  agents: Array<Record<string, unknown>>;
  command_pipeline: {
    in_flight_count: number;
    attention_count: number;
    [key: string]: unknown;
  };
  recent_commands: Array<Record<string, unknown>>;
}

export function isPaperMode(snapshot: OperationsSnapshot | null): boolean {
  return (snapshot?.execution.mode ?? '').toLowerCase() === 'paper';
}
