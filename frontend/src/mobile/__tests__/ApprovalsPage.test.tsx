import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { SupervisionProposal } from '@/services/supervisionService';

vi.mock('@/services/supervisionService', () => ({
  supervisionService: {
    listProposals: vi.fn(),
    approve: vi.fn(),
    dispatch: vi.fn(),
    reject: vi.fn(),
    getInterlock: vi.fn(),
  },
}));

vi.mock('@/mobile/useOperationsSnapshot', () => ({
  useOperationsSnapshot: vi.fn(),
}));

import ApprovalsPage from '@/mobile/pages/ApprovalsPage';
import { supervisionService } from '@/services/supervisionService';
import { useOperationsSnapshot } from '@/mobile/useOperationsSnapshot';

const paperSnapshot = {
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

const liveSnapshot = {
  ...paperSnapshot,
  execution: { ...paperSnapshot.execution, mode: 'live' },
};

const mockProposal: SupervisionProposal = {
  proposal_id: 'prop-mops-001',
  command_name: 'flatten',
  target_agent_id: 'agent-btc',
  requester: 'supervisor',
  payload: { reason: 'drawdown' },
  status: 'pending',
  created_at: '2026-08-08T10:00:00Z',
  expires_at: '2026-08-08T10:15:00Z',
};

describe('Mobile Ops ApprovalsPage (P2)', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(useOperationsSnapshot).mockReturnValue({
      snapshot: paperSnapshot,
      error: null,
      loading: false,
      refresh: vi.fn(),
    });
    vi.mocked(supervisionService.listProposals).mockResolvedValue({
      proposals: [mockProposal],
      count: 1,
    });
  });

  it('loads and renders pending proposals', async () => {
    localStorage.setItem('nautilus_role', 'approver');
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText('flatten')).toBeInTheDocument();
    });
    expect(screen.getByText('prop-mops-001')).toBeInTheDocument();
  });

  it('hides mutate buttons for viewer', async () => {
    localStorage.setItem('nautilus_role', 'viewer');
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText('flatten')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(screen.getByText(/require/i)).toBeInTheDocument();
  });

  it('approve then dispatch are separate calls', async () => {
    localStorage.setItem('nautilus_role', 'approver');
    vi.mocked(supervisionService.approve).mockResolvedValue({
      approval_id: 'appr-001',
      proposal_id: 'prop-mops-001',
      payload_hash: 'abc',
      target_agent_id: 'agent-btc',
      requester: 'supervisor',
      idempotency_key: 'key-1',
      approver: 'approver',
      approved_at: '2026-08-08T10:05:00Z',
      expires_at: '2026-08-08T10:15:00Z',
      status: 'active',
    });
    vi.mocked(supervisionService.dispatch).mockResolvedValue({
      dispatch_id: 'disp-001',
      proposal_id: 'prop-mops-001',
      approval_id: 'appr-001',
      command: 'flatten',
      target_agent_id: 'agent-btc',
      status: 'dispatched',
      dispatched_at: '2026-08-08T10:06:00Z',
    });

    render(<ApprovalsPage />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => {
      expect(supervisionService.approve).toHaveBeenCalledWith('prop-mops-001', undefined);
    });
    expect(supervisionService.dispatch).not.toHaveBeenCalled();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Dispatch to agent' })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Dispatch to agent' }));
    await waitFor(() => {
      expect(supervisionService.dispatch).toHaveBeenCalledWith('appr-001');
    });
  });

  it('calls reject', async () => {
    localStorage.setItem('nautilus_role', 'admin');
    vi.mocked(supervisionService.reject).mockResolvedValue({
      proposal_id: 'prop-mops-001',
      status: 'rejected',
      reason: '',
    });
    render(<ApprovalsPage />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    await waitFor(() => {
      expect(supervisionService.reject).toHaveBeenCalledWith('prop-mops-001');
    });
  });

  it('keeps inbox read-only outside paper mode', async () => {
    localStorage.setItem('nautilus_role', 'approver');
    vi.mocked(useOperationsSnapshot).mockReturnValue({
      snapshot: liveSnapshot,
      error: null,
      loading: false,
      refresh: vi.fn(),
    });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText('flatten')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(screen.getByText(/Mutations disabled/i)).toBeInTheDocument();
  });
});
