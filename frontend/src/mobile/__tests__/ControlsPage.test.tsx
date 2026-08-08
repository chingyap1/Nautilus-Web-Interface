import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('@/services/nautilusService', () => ({
  default: {
    listStrategies: vi.fn(),
    activateKillSwitch: vi.fn(),
    flattenStrategy: vi.fn(),
  },
}));

vi.mock('@/services/supervisionService', () => ({
  supervisionService: {
    getInterlock: vi.fn(),
    engageInterlock: vi.fn(),
    resumeInterlock: vi.fn(),
  },
}));

vi.mock('@/mobile/useOperationsSnapshot', () => ({
  useOperationsSnapshot: vi.fn(),
}));

import ControlsPage from '@/mobile/pages/ControlsPage';
import nautilusService from '@/services/nautilusService';
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

describe('Mobile Ops ControlsPage (P3)', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(useOperationsSnapshot).mockReturnValue({
      snapshot: paperSnapshot,
      error: null,
      loading: false,
      refresh: vi.fn(),
    });
    vi.mocked(supervisionService.getInterlock).mockResolvedValue({ state: 'resumed' });
    vi.mocked(nautilusService.listStrategies).mockResolvedValue({
      success: true,
      strategies: [{ id: 'strat-btc', name: 'BTC MA', type: 'ma_cross', status: 'running' }],
      count: 1,
    });
  });

  it('disables mutations outside paper mode', async () => {
    localStorage.setItem('nautilus_role', 'operator');
    vi.mocked(useOperationsSnapshot).mockReturnValue({
      snapshot: liveSnapshot,
      error: null,
      loading: false,
      refresh: vi.fn(),
    });
    render(<ControlsPage />);
    await waitFor(() => {
      expect(screen.getByText(/Controls are disabled/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Kill switch' })).not.toBeInTheDocument();
  });

  it('hides mutate controls for viewer', async () => {
    localStorage.setItem('nautilus_role', 'viewer');
    render(<ControlsPage />);
    await waitFor(() => {
      expect(screen.getByText(/require/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Kill switch' })).not.toBeInTheDocument();
  });

  it('requires reason + second confirm before kill switch', async () => {
    localStorage.setItem('nautilus_role', 'operator');
    vi.mocked(nautilusService.activateKillSwitch).mockResolvedValue({
      success: true,
      command_id: 'cmd-kill-1',
      status: 'validated',
      message: 'Kill switch accepted and awaiting execution-agent confirmation',
    });

    render(<ControlsPage />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Kill switch' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Kill switch' }));
    expect(nautilusService.activateKillSwitch).not.toHaveBeenCalled();

    const confirm = screen.getByRole('button', { name: 'Confirm kill switch' });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/Why are you doing this/i), {
      target: { value: 'drawdown' },
    });
    expect(confirm).not.toBeDisabled();

    fireEvent.click(confirm);
    await waitFor(() => {
      expect(nautilusService.activateKillSwitch).toHaveBeenCalledTimes(1);
    });
  });

  it('flattens selected strategy after confirm', async () => {
    localStorage.setItem('nautilus_role', 'approver');
    vi.mocked(nautilusService.flattenStrategy).mockResolvedValue({
      success: true,
      command_id: 'cmd-flat-1',
      strategy_id: 'strat-btc',
      status: 'validated',
      message: 'Strategy strat-btc flatten requested',
    });

    render(<ControlsPage />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Flatten strategy' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Flatten strategy' }));
    fireEvent.change(screen.getByPlaceholderText(/Why are you doing this/i), {
      target: { value: 'risk limit' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm flatten' }));

    await waitFor(() => {
      expect(nautilusService.flattenStrategy).toHaveBeenCalledWith('strat-btc');
    });
  });

  it('engages interlock with reason', async () => {
    localStorage.setItem('nautilus_role', 'operator');
    vi.mocked(supervisionService.engageInterlock).mockResolvedValue({
      state: 'paused',
      actor: 'operator',
      reason: 'on-call pause',
      updated_at: '2026-08-08T12:00:00Z',
    });

    render(<ControlsPage />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Pause Supervisor commands' })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Pause Supervisor commands' }));
    fireEvent.change(screen.getByPlaceholderText(/Why are you doing this/i), {
      target: { value: 'on-call pause' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm pause Supervisor' }));

    await waitFor(() => {
      expect(supervisionService.engageInterlock).toHaveBeenCalledWith('on-call pause');
    });
  });

  it('shows resume for admin when paused and requires confirm', async () => {
    localStorage.setItem('nautilus_role', 'admin');
    vi.mocked(supervisionService.getInterlock).mockResolvedValue({
      state: 'paused',
      actor: 'admin',
      reason: 'earlier',
      updated_at: '2026-08-08T11:00:00Z',
    });
    vi.mocked(supervisionService.resumeInterlock).mockResolvedValue({
      state: 'resumed',
      actor: 'admin',
      reason: 'all clear',
      updated_at: '2026-08-08T12:30:00Z',
    });

    render(<ControlsPage />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Resume Supervisor commands' })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Resume Supervisor commands' }));
    expect(supervisionService.resumeInterlock).not.toHaveBeenCalled();
    fireEvent.change(screen.getByPlaceholderText(/Why are you doing this/i), {
      target: { value: 'all clear' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm resume Supervisor' }));

    await waitFor(() => {
      expect(supervisionService.resumeInterlock).toHaveBeenCalledWith('all clear');
    });
  });

  it('tells non-admin that resume requires admin when paused', async () => {
    localStorage.setItem('nautilus_role', 'operator');
    vi.mocked(supervisionService.getInterlock).mockResolvedValue({ state: 'paused' });

    render(<ControlsPage />);
    await waitFor(() => {
      expect(screen.getByText(/Resume requires admin role/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Resume Supervisor commands' })).not.toBeInTheDocument();
  });
});
