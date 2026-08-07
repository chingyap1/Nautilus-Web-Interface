import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import AuditFeed from '@/components/supervision/AuditFeed';

vi.mock('@/services/supervisionService', () => ({
  supervisionService: {
    getAuditLog: vi.fn(),
  },
}));

import { supervisionService } from '@/services/supervisionService';

describe('AuditFeed', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it('shows empty state when no entries', async () => {
    vi.mocked(supervisionService.getAuditLog).mockResolvedValue({ entries: [], count: 0 });
    render(<AuditFeed />);
    await waitFor(() => {
      expect(screen.getByText('No audit entries yet')).toBeInTheDocument();
    });
  });

  it('shows error message on failure', async () => {
    vi.mocked(supervisionService.getAuditLog).mockRejectedValue(new Error('Network error'));
    render(<AuditFeed />);
    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeInTheDocument();
    });
  });

  it('renders audit entries with action labels', async () => {
    vi.mocked(supervisionService.getAuditLog).mockResolvedValue({
      entries: [
        {
          audit_id: 'a-001',
          timestamp: '2026-01-01T10:00:00Z',
          action: 'interlock_engage',
          actor: 'admin',
          detail: { reason: 'emergency stop' },
        },
        {
          audit_id: 'a-002',
          timestamp: '2026-01-01T10:05:00Z',
          action: 'approve',
          actor: 'approver1',
          detail: { proposal_id: 'prop-001' },
        },
      ],
      count: 2,
    });
    render(<AuditFeed />);
    await waitFor(() => {
      expect(screen.getByText('Pause')).toBeInTheDocument();
      expect(screen.getByText('Approve')).toBeInTheDocument();
      expect(screen.getByText('admin')).toBeInTheDocument();
      expect(screen.getByText('approver1')).toBeInTheDocument();
    });
  });

  it('shows refresh button that triggers fetch', async () => {
    vi.mocked(supervisionService.getAuditLog).mockResolvedValue({ entries: [], count: 0 });
    render(<AuditFeed />);
    await waitFor(() => {
      expect(screen.getByText('Refresh')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Refresh'));
    await waitFor(() => {
      expect(supervisionService.getAuditLog).toHaveBeenCalledTimes(2);
    });
  });

  it('renders entry detail fields', async () => {
    vi.mocked(supervisionService.getAuditLog).mockResolvedValue({
      entries: [
        {
          audit_id: 'a-001',
          timestamp: '2026-01-01T10:00:00Z',
          action: 'dispatch',
          actor: 'admin',
          detail: { command: 'flatten', target: 'agent-btc' },
        },
      ],
      count: 1,
    });
    const { container } = render(<AuditFeed />);
    await waitFor(() => {
      expect(screen.getByText('Dispatch')).toBeInTheDocument();
    });
    expect(container.textContent).toContain('command=flatten');
    expect(container.textContent).toContain('target=agent-btc');
  });

  it('shows audit id in mono font', async () => {
    vi.mocked(supervisionService.getAuditLog).mockResolvedValue({
      entries: [
        {
          audit_id: 'a-001',
          timestamp: '2026-01-01T10:00:00Z',
          action: 'inspect',
          actor: 'viewer1',
          detail: {},
        },
      ],
      count: 1,
    });
    render(<AuditFeed />);
    await waitFor(() => {
      expect(screen.getByText('a-001')).toBeInTheDocument();
    });
  });
});
