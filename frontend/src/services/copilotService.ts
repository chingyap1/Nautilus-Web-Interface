import api from '@/lib/api';

export interface CopilotWorkspace {
  id: string;
  owner_id: string;
  title: string;
  strategy_id: string | null;
  lifecycle: CopilotLifecycle;
  created_at: string;
  updated_at: string;
}

export type CopilotLifecycle = 'IDEA' | 'SPECIFICATION' | 'DRAFT' | 'VALIDATING' | 'CANDIDATE' | 'APPROVED_FOR_PAPER' | 'PAPER_OBSERVATION' | 'ELIGIBLE_FOR_LIVE';
export type CopilotArtifactKind = 'specification' | 'strategy_draft';

export interface CopilotArtifact { id: string; workspace_id: string; kind: CopilotArtifactKind; title: string; current_revision: number; created_at: string; updated_at: string; }
export interface CopilotArtifactRevision { id: string; artifact_id: string; revision: number; content: string; content_hash: string; created_by: string; created_at: string; }
export interface CopilotApproval { id: string; artifact_revision_id: string; decision: 'approved' | 'rejected'; reason: string; decided_by: string; decided_at: string; }
export interface CopilotTransition { id: string; workspace_id: string; from_lifecycle: CopilotLifecycle; to_lifecycle: CopilotLifecycle; actor_id: string; created_at: string; }
export interface CopilotEligibility { eligible: boolean; target: CopilotLifecycle | null; required_artifact_kind?: CopilotArtifactKind; reason: string; }

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
  role: 'user' | 'system';
  content: string;
  status: 'saved' | 'queued_for_supervisor';
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
  listRevisions: (artifactId: string) => api.get<{ revisions: CopilotArtifactRevision[] }>(`/api/copilot/artifacts/${artifactId}/revisions`),
  createRevision: (artifactId: string, content: string) => api.post<{ revision: CopilotArtifactRevision }>(`/api/copilot/artifacts/${artifactId}/revisions`, { content }),
  decideRevision: (artifactId: string, revisionId: string, decision: CopilotApproval['decision'], reason: string) => api.post<{ approval: CopilotApproval }>(`/api/copilot/artifacts/${artifactId}/revisions/${revisionId}/approval`, { decision, reason }),
  lifecycle: (workspaceId: string) => api.get<{ workspace: CopilotWorkspace; eligibility: CopilotEligibility; transitions: CopilotTransition[] }>(`/api/copilot/workspaces/${workspaceId}/lifecycle`),
  advanceLifecycle: (workspaceId: string) => api.post<{ workspace: CopilotWorkspace; transition: CopilotTransition }>(`/api/copilot/workspaces/${workspaceId}/lifecycle/advance`, {}),
};