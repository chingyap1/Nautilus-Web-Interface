import api from '@/lib/api';

export interface CopilotWorkspace {
  id: string;
  owner_id: string;
  title: string;
  strategy_id: string | null;
  lifecycle: CopilotLifecycle;
  promotion_id: string | null;
  created_at: string;
  updated_at: string;
}

export type CopilotLifecycle = 'IDEA' | 'SPECIFICATION' | 'DRAFT' | 'VALIDATING' | 'CANDIDATE' | 'APPROVED_FOR_PAPER' | 'PAPER_OBSERVATION' | 'ELIGIBLE_FOR_LIVE';
export type CopilotArtifactKind =
  | 'specification'
  | 'strategy_draft'
  | 'experiment_result'
  | 'comparison_table'
  | 'optuna_summary';

export type CopilotResearchTool =
  | 'run_backtest'
  | 'run_walk_forward'
  | 'compare_strategies'
  | 'optimise_params'
  | 'registry_status'
  | 'propose_registry_patch';

export interface CopilotArtifact { id: string; workspace_id: string; kind: CopilotArtifactKind; title: string; current_revision: number; created_at: string; updated_at: string; }
export interface CopilotArtifactRevision { id: string; artifact_id: string; revision: number; content: string; content_hash: string; created_by: string; created_at: string; }
export interface CopilotApproval { id: string; artifact_revision_id: string; decision: 'approved' | 'rejected'; reason: string; decided_by: string; decided_at: string; }
export interface CopilotTransition { id: string; workspace_id: string; from_lifecycle: CopilotLifecycle; to_lifecycle: CopilotLifecycle; actor_id: string; created_at: string; }
export interface CopilotEligibility {
  eligible: boolean;
  target: CopilotLifecycle | null;
  required_artifact_kind?: CopilotArtifactKind | null;
  reason: string;
  promotion_id?: string | null;
  promotion_state?: CopilotLifecycle | null;
}

export interface SupervisionIngressRequest {
  pair: string;
  reason: string;
  strategy?: string | null;
  recommendation_kind?: string;
  parameters?: Record<string, unknown>;
}

export interface CopilotConversation {
  id: string;
  workspace_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface CopilotMessage {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  status: 'saved' | 'completed' | 'queued_for_supervisor' | 'supervisor_error';
  sequence: number;
  created_at: string;
}

export const copilotService = {
  listWorkspaces: () => api.get<{ workspaces: CopilotWorkspace[] }>('/api/copilot/workspaces'),
  createWorkspace: (title: string, strategyId?: string) =>
    api.post<{ workspace: CopilotWorkspace }>('/api/copilot/workspaces', {
      title,
      strategy_id: strategyId || null,
    }),
  createFromSupervision: (body: SupervisionIngressRequest) =>
    api.post<{
      workspace: CopilotWorkspace;
      conversation: CopilotConversation;
      artifact: CopilotArtifact;
      revision: CopilotArtifactRevision;
    }>('/api/copilot/workspaces/from-supervision', {
      pair: body.pair,
      reason: body.reason,
      strategy: body.strategy ?? null,
      recommendation_kind: body.recommendation_kind ?? 'experiment',
      parameters: body.parameters ?? {},
    }),
  listConversations: (workspaceId: string) =>
    api.get<{ conversations: CopilotConversation[] }>(
      `/api/copilot/workspaces/${workspaceId}/conversations`,
    ),
  createConversation: (workspaceId: string, title = 'Strategy discussion') =>
    api.post<{ conversation: CopilotConversation }>(
      `/api/copilot/workspaces/${workspaceId}/conversations`,
      { title },
    ),
  listMessages: (conversationId: string) =>
    api.get<{ messages: CopilotMessage[] }>(
      `/api/copilot/conversations/${conversationId}/messages`,
    ),
  createMessage: (conversationId: string, content: string) =>
    api.post<{ message: CopilotMessage; acknowledgement: CopilotMessage }>(
      `/api/copilot/conversations/${conversationId}/messages`,
      { content },
    ),
  listArtifacts: (workspaceId: string) => api.get<{ artifacts: CopilotArtifact[] }>(`/api/copilot/workspaces/${workspaceId}/artifacts`),
  createArtifact: (workspaceId: string, kind: CopilotArtifactKind, title: string, content: string) => api.post<{ artifact: CopilotArtifact; revision: CopilotArtifactRevision }>(`/api/copilot/workspaces/${workspaceId}/artifacts`, { kind, title, content }),
  runExperiment: (workspaceId: string, tool: CopilotResearchTool, params: Record<string, unknown> = {}) =>
    api.post<{
      artifact: CopilotArtifact;
      revision: CopilotArtifactRevision;
      summary: string;
      metrics: Record<string, unknown>;
      tool: CopilotResearchTool;
    }>(`/api/copilot/workspaces/${workspaceId}/experiments`, { tool, params }),
  importArtifact: (workspaceId: string, kind: Exclude<CopilotArtifactKind, 'specification' | 'strategy_draft'>, title: string, content: string) =>
    api.post<{ artifact: CopilotArtifact; revision: CopilotArtifactRevision }>(
      `/api/copilot/workspaces/${workspaceId}/artifacts/import`,
      { kind, title, content },
    ),
  listRevisions: (artifactId: string) => api.get<{ revisions: CopilotArtifactRevision[] }>(`/api/copilot/artifacts/${artifactId}/revisions`),
  createRevision: (artifactId: string, content: string) => api.post<{ revision: CopilotArtifactRevision }>(`/api/copilot/artifacts/${artifactId}/revisions`, { content }),
  listApprovals: (artifactId: string) => api.get<{ approvals: CopilotApproval[] }>(`/api/copilot/artifacts/${artifactId}/approvals`),
  decideRevision: (artifactId: string, revisionId: string, decision: CopilotApproval['decision'], reason: string) => api.post<{ approval: CopilotApproval }>(`/api/copilot/artifacts/${artifactId}/revisions/${revisionId}/approval`, { decision, reason }),
  applyRegistryPatch: (artifactId: string, dryRun = false) =>
    api.post<{
      result: {
        applied: string[];
        skipped: string[];
        strategies: string[];
        dry_run: boolean;
        remaining_missing?: Record<string, string[]>;
        git_push: boolean;
        message?: string;
      };
      artifact: CopilotArtifact;
      revision: CopilotArtifactRevision;
    }>(`/api/copilot/artifacts/${artifactId}/apply-registry-patch`, { dry_run: dryRun }),
  lifecycle: (workspaceId: string) => api.get<{ workspace: CopilotWorkspace; eligibility: CopilotEligibility; transitions: CopilotTransition[] }>(`/api/copilot/workspaces/${workspaceId}/lifecycle`),
  advanceLifecycle: (workspaceId: string) => api.post<{ workspace: CopilotWorkspace; transition: CopilotTransition }>(`/api/copilot/workspaces/${workspaceId}/lifecycle/advance`, {}),
};