import api from '@/lib/api';

export interface CopilotWorkspace {
  id: string;
  owner_id: string;
  title: string;
  strategy_id: string | null;
  lifecycle: 'IDEA';
  created_at: string;
  updated_at: string;
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
};