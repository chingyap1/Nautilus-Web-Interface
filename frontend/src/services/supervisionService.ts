import api from '@/lib/api';

// ---------------------------------------------------------------------------
// Types — mirror the FastAPI response shapes exactly (docs/supervision_ui_plan.md §3)
// ---------------------------------------------------------------------------

export interface AgentHealth {
  agent_id: string;
  pair: string;
  strategy: string;
  interval: string;
  execution_mode: string;
  status: 'healthy' | 'stale' | 'degraded' | 'offline' | 'paused';
  last_heartbeat: string | null;
  heartbeat_age_seconds: number;
  num_fills: number;
  balance_usd: number;
  unrealised_pnl: number;
  open_positions: number;
  source_path: string | null;
}

export interface MetricSnapshot {
  timestamp: string | null;
  latest_equity: number;
  peak_equity: number;
  current_drawdown_pct: number;
  max_drawdown_pct: number;
  total_return_pct: number;
  annualized_volatility: number | null;
  num_fills: number;
  rejected_command_count: number;
  command_count: number;
}

export interface Recommendation {
  kind: 'none' | 'restart' | 'experiment' | 'flatten' | 'review' | 'halt';
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  reason: string;
  requires_approval: boolean;
  proposed_at: string;
  parameters: Record<string, unknown>;
}

export interface CommandProposal {
  proposal_id: string;
  command_name: string;
  command_version: number;
  target_agent_id: string;
  requester: string;
  payload: Record<string, unknown>;
  idempotency_key: string;
  created_at: string;
  expires_at: string | null;
  status: string;
}

export interface SupervisionResult {
  health: AgentHealth;
  metrics: MetricSnapshot;
  recommendation: Recommendation;
  proposal: CommandProposal | null;
}

export interface InterlockState {
  state: 'paused' | 'resumed';
}

export interface InterlockActionResponse {
  state: 'paused' | 'resumed';
  actor: string;
  reason: string;
  updated_at: string;
}

export interface SupervisionProposal {
  proposal_id: string;
  command_name: string;
  target_agent_id: string;
  requester: string;
  payload: Record<string, unknown>;
  status: string;
  created_at: string;
  expires_at: string | null;
}

export interface PendingProposalsResponse {
  proposals: SupervisionProposal[];
  count: number;
}

// ---------------------------------------------------------------------------
// Service — thin wrapper over the central API client (same as copilotService)
// ---------------------------------------------------------------------------

export const supervisionService = {
  inspect: (pair: string, logDir?: string) =>
    api.post<SupervisionResult>('/api/supervision/inspect', { pair, log_dir: logDir }),
  getInterlock: () =>
    api.get<InterlockState>('/api/supervision/interlock'),
  engageInterlock: (reason?: string) =>
    api.post<InterlockActionResponse>('/api/supervision/interlock/engage', { reason }),
  resumeInterlock: (reason?: string) =>
    api.post<InterlockActionResponse>('/api/supervision/interlock/resume', { reason }),
  listProposals: () =>
    api.get<PendingProposalsResponse>('/api/supervision/proposals'),
};
