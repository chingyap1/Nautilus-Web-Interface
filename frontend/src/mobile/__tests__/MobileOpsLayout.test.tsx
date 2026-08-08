import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Router } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';
import MobileOpsLayout from '@/mobile/MobileOpsLayout';
import { isPaperMode, type OperationsSnapshot } from '@/mobile/types';

describe('Mobile Ops scaffold', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  it('renders brand and bottom tabs without TraderLayout chrome', () => {
    const { hook } = memoryLocation({ path: '/m/status' });
    render(
      <Router hook={hook}>
        <MobileOpsLayout>
          <p>status body</p>
        </MobileOpsLayout>
      </Router>,
    );

    expect(screen.getByRole('heading', { name: 'Mobile Ops' })).toBeInTheDocument();
    expect(screen.getByText('Paper')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Mobile Ops' })).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Approvals')).toBeInTheDocument();
    expect(screen.getByText('Controls')).toBeInTheDocument();
    expect(screen.getByText('Activity')).toBeInTheDocument();
    expect(screen.getByText('Account')).toBeInTheDocument();
    expect(screen.queryByText('NAUTILUS')).not.toBeInTheDocument();
    expect(screen.queryByText('Control plane')).not.toBeInTheDocument();
  });

  it('treats only paper execution mode as paper', () => {
    const paper: OperationsSnapshot = {
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
    expect(isPaperMode(paper)).toBe(true);
    expect(isPaperMode({ ...paper, execution: { ...paper.execution, mode: 'live' } })).toBe(false);
    expect(isPaperMode(null)).toBe(false);
  });
});
