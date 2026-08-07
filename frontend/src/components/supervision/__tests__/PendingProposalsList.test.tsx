import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import PendingProposalsList from '@/components/supervision/PendingProposalsList';
import type { SupervisionProposal } from '@/services/supervisionService';

vi.mock('@/services/supervisionService', () => ({
  supervisionService: {
    approve: vi.fn(),
    dispatch: vi.fn(),
    reject: vi.fn(),
  },
}));

import { supervisionService } from '@/services/supervisionService';

const mockProposal: SupervisionProposal = {
  proposal_id: 'prop-001',
  command_name: 'flatten',
  target_agent_id: 'agent-btc',
  requester: 'supervisor',
  payload: { reason: 'drawdown limit exceeded' },
  status: 'pending',
  created_at: '2026-01-01T10:00:00Z',
  expires_at: '2026-01-01T10:15:00Z',
};

describe('PendingProposalsList', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it('renders empty state when no proposals', () => {
    render(<PendingProposalsList proposals={[]} count={0} loading={false} />);
    expect(screen.getByText('No supervision proposals awaiting approval')).toBeInTheDocument();
  });

  it('renders loading state', () => {
    render(<PendingProposalsList proposals={[]} count={0} loading={true} />);
    expect(screen.getByText('Loading proposals…')).toBeInTheDocument();
  });

  it('renders proposal with command name and id', () => {
    render(<PendingProposalsList proposals={[mockProposal]} count={1} loading={false} />);
    expect(screen.getByText('flatten')).toBeInTheDocument();
    expect(screen.getByText('prop-001')).toBeInTheDocument();
  });

  it('hides action buttons for viewer role', () => {
    localStorage.setItem('nautilus_role', 'viewer');
    render(<PendingProposalsList proposals={[mockProposal]} count={1} loading={false} />);
    expect(screen.queryByText('Approve')).not.toBeInTheDocument();
    expect(screen.queryByText('Reject')).not.toBeInTheDocument();
  });

  it('hides action buttons for operator role', () => {
    localStorage.setItem('nautilus_role', 'operator');
    render(<PendingProposalsList proposals={[mockProposal]} count={1} loading={false} />);
    expect(screen.queryByText('Approve')).not.toBeInTheDocument();
  });

  it('shows approve and reject buttons for approver role', () => {
    localStorage.setItem('nautilus_role', 'approver');
    render(<PendingProposalsList proposals={[mockProposal]} count={1} loading={false} />);
    expect(screen.getByText('Approve')).toBeInTheDocument();
    expect(screen.getByText('Reject')).toBeInTheDocument();
  });

  it('shows approve and reject buttons for admin role', () => {
    localStorage.setItem('nautilus_role', 'admin');
    render(<PendingProposalsList proposals={[mockProposal]} count={1} loading={false} />);
    expect(screen.getByText('Approve')).toBeInTheDocument();
    expect(screen.getByText('Reject')).toBeInTheDocument();
  });

  it('calls approve on click', async () => {
    localStorage.setItem('nautilus_role', 'approver');
    vi.mocked(supervisionService.approve).mockResolvedValue({
      approval_id: 'appr-001',
      proposal_id: 'prop-001',
      payload_hash: 'abc',
      target_agent_id: 'agent-btc',
      requester: 'supervisor',
      idempotency_key: 'key-1',
      approver: 'approver',
      approved_at: '2026-01-01T10:05:00Z',
      expires_at: '2026-01-01T10:15:00Z',
      status: 'active',
    });
    render(<PendingProposalsList proposals={[mockProposal]} count={1} loading={false} />);
    fireEvent.click(screen.getByText('Approve'));
    await waitFor(() => {
      expect(supervisionService.approve).toHaveBeenCalledWith('prop-001', undefined);
    });
  });

  it('calls reject on click', async () => {
    localStorage.setItem('nautilus_role', 'approver');
    vi.mocked(supervisionService.reject).mockResolvedValue({
      proposal_id: 'prop-001',
      status: 'rejected',
      reason: 'Rejected via NWI',
    });
    render(<PendingProposalsList proposals={[mockProposal]} count={1} loading={false} />);
    fireEvent.click(screen.getByText('Reject'));
    await waitFor(() => {
      expect(supervisionService.reject).toHaveBeenCalledWith('prop-001');
    });
  });

  it('shows dispatch button after approval succeeds', async () => {
    localStorage.setItem('nautilus_role', 'approver');
    vi.mocked(supervisionService.approve).mockResolvedValue({
      approval_id: 'appr-001',
      proposal_id: 'prop-001',
      payload_hash: 'abc',
      target_agent_id: 'agent-btc',
      requester: 'supervisor',
      idempotency_key: 'key-1',
      approver: 'approver',
      approved_at: '2026-01-01T10:05:00Z',
      expires_at: '2026-01-01T10:15:00Z',
      status: 'active',
    });
    render(<PendingProposalsList proposals={[mockProposal]} count={1} loading={false} />);
    fireEvent.click(screen.getByText('Approve'));
    await waitFor(() => {
      expect(screen.getByText('Dispatch now')).toBeInTheDocument();
    });
  });

  it('dispatch is a separate click — not auto-fired after approve', async () => {
    localStorage.setItem('nautilus_role', 'approver');
    vi.mocked(supervisionService.approve).mockResolvedValue({
      approval_id: 'appr-001',
      proposal_id: 'prop-001',
      payload_hash: 'abc',
      target_agent_id: 'agent-btc',
      requester: 'supervisor',
      idempotency_key: 'key-1',
      approver: 'approver',
      approved_at: '2026-01-01T10:05:00Z',
      expires_at: '2026-01-01T10:15:00Z',
      status: 'active',
    });
    vi.mocked(supervisionService.dispatch).mockResolvedValue({
      dispatch_id: 'disp-001',
      proposal_id: 'prop-001',
      approval_id: 'appr-001',
      command: 'flatten',
      target_agent_id: 'agent-btc',
      status: 'dispatched',
      dispatched_at: '2026-01-01T10:06:00Z',
    });
    render(<PendingProposalsList proposals={[mockProposal]} count={1} loading={false} />);
    fireEvent.click(screen.getByText('Approve'));
    await waitFor(() => {
      expect(screen.getByText('Dispatch now')).toBeInTheDocument();
    });
    // dispatch should NOT have been called yet
    expect(supervisionService.dispatch).not.toHaveBeenCalled();
    // now click dispatch
    fireEvent.click(screen.getByText('Dispatch now'));
    await waitFor(() => {
      expect(supervisionService.dispatch).toHaveBeenCalledWith('appr-001');
    });
  });

  it('re-displays payload next to dispatch button', async () => {
    localStorage.setItem('nautilus_role', 'approver');
    vi.mocked(supervisionService.approve).mockResolvedValue({
      approval_id: 'appr-001',
      proposal_id: 'prop-001',
      payload_hash: 'abc',
      target_agent_id: 'agent-btc',
      requester: 'supervisor',
      idempotency_key: 'key-1',
      approver: 'approver',
      approved_at: '2026-01-01T10:05:00Z',
      expires_at: '2026-01-01T10:15:00Z',
      status: 'active',
    });
    const { container } = render(<PendingProposalsList proposals={[mockProposal]} count={1} loading={false} />);
    fireEvent.click(screen.getByText('Approve'));
    await waitFor(() => {
      expect(screen.getByText('Dispatch now')).toBeInTheDocument();
    });
    // The payload should appear twice — once in the proposal card, once next to dispatch
    const preElements = container.querySelectorAll('pre');
    expect(preElements.length).toBeGreaterThanOrEqual(2);
  });
});
