import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import InterlockBanner from '@/components/supervision/InterlockBanner';
import type { InterlockState, InterlockActionResponse } from '@/services/supervisionService';

describe('InterlockBanner', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('shows paused state when state is paused', () => {
    localStorage.setItem('nautilus_role', 'admin');
    const state: InterlockState = { state: 'paused', actor: 'admin', reason: 'test', updated_at: '2026-01-01T00:00:00Z' };
    render(
      <InterlockBanner state={state} actionResponse={null} loading={false} onEngage={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText(/PAUSED/)).toBeInTheDocument();
  });

  it('shows resumed state when state is resumed', () => {
    localStorage.setItem('nautilus_role', 'admin');
    const state: InterlockState = { state: 'resumed' };
    render(
      <InterlockBanner state={state} actionResponse={null} loading={false} onEngage={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText(/RESUMED/)).toBeInTheDocument();
  });

  it('shows fail-closed when state is null', () => {
    localStorage.setItem('nautilus_role', 'admin');
    render(
      <InterlockBanner state={null} actionResponse={null} loading={false} onEngage={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText(/treating as PAUSED/i)).toBeInTheDocument();
  });

  it('shows engage button for operator role when resumed', () => {
    localStorage.setItem('nautilus_role', 'operator');
    const state: InterlockState = { state: 'resumed' };
    render(
      <InterlockBanner state={state} actionResponse={null} loading={false} onEngage={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText('Pause Supervisor commands')).toBeInTheDocument();
  });

  it('hides engage button for viewer role', () => {
    localStorage.setItem('nautilus_role', 'viewer');
    const state: InterlockState = { state: 'resumed' };
    render(
      <InterlockBanner state={state} actionResponse={null} loading={false} onEngage={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.queryByText('Pause Supervisor commands')).not.toBeInTheDocument();
  });

  it('shows resume button for admin when paused', () => {
    localStorage.setItem('nautilus_role', 'admin');
    const state: InterlockState = { state: 'paused' };
    render(
      <InterlockBanner state={state} actionResponse={null} loading={false} onEngage={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText('Resume — admin only')).toBeInTheDocument();
  });

  it('shows "requires admin role" for operator when paused', () => {
    localStorage.setItem('nautilus_role', 'operator');
    const state: InterlockState = { state: 'paused' };
    render(
      <InterlockBanner state={state} actionResponse={null} loading={false} onEngage={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText('Resume requires admin role')).toBeInTheDocument();
  });

  it('requires confirm click before resume', () => {
    localStorage.setItem('nautilus_role', 'admin');
    const onResume = vi.fn();
    const state: InterlockState = { state: 'paused' };
    render(
      <InterlockBanner state={state} actionResponse={null} loading={false} onEngage={vi.fn()} onResume={onResume} />,
    );
    const resumeBtn = screen.getByText('Resume — admin only');
    fireEvent.click(resumeBtn);
    expect(screen.getByText('Confirm resume — re-enable proposals')).toBeInTheDocument();
    expect(onResume).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Confirm resume — re-enable proposals'));
    expect(onResume).toHaveBeenCalled();
  });

  it('shows extended metadata from state when no actionResponse', () => {
    localStorage.setItem('nautilus_role', 'admin');
    const state: InterlockState = {
      state: 'paused',
      actor: 'operator1',
      reason: 'emergency stop',
      updated_at: '2026-01-01T12:00:00Z',
    };
    render(
      <InterlockBanner state={state} actionResponse={null} loading={false} onEngage={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText('operator1')).toBeInTheDocument();
    expect(screen.getByText(/emergency stop/)).toBeInTheDocument();
  });

  it('prefers actionResponse over state metadata', () => {
    localStorage.setItem('nautilus_role', 'admin');
    const state: InterlockState = { state: 'paused', actor: 'state-actor' };
    const actionResponse: InterlockActionResponse = {
      state: 'paused',
      actor: 'action-actor',
      reason: 'action reason',
      updated_at: '2026-01-01T12:00:00Z',
    };
    render(
      <InterlockBanner state={state} actionResponse={actionResponse} loading={false} onEngage={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText('action-actor')).toBeInTheDocument();
    expect(screen.queryByText('state-actor')).not.toBeInTheDocument();
  });
});
