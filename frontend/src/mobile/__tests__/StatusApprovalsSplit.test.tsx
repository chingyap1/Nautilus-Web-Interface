import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@/mobile/pages/StatusPage', () => ({
  default: () => <div>Status pane body</div>,
}));
vi.mock('@/mobile/pages/ApprovalsPage', () => ({
  default: () => <div>Approvals pane body</div>,
}));

import StatusApprovalsSplit from '@/mobile/StatusApprovalsSplit';

function mockMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

describe('StatusApprovalsSplit (P4)', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows only the focused page on phone widths', () => {
    mockMatchMedia(false);
    render(<StatusApprovalsSplit focus="status" />);
    expect(screen.getByText('Status pane body')).toBeInTheDocument();
    expect(screen.queryByText('Approvals pane body')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mops-status-approvals-split')).not.toBeInTheDocument();
  });

  it('shows Status | Approvals split at iPad widths', () => {
    mockMatchMedia(true);
    render(<StatusApprovalsSplit focus="approvals" />);
    expect(screen.getByTestId('mops-status-approvals-split')).toBeInTheDocument();
    expect(screen.getByText('Status pane body')).toBeInTheDocument();
    expect(screen.getByText('Approvals pane body')).toBeInTheDocument();
    expect(screen.getByLabelText('Approvals pane')).toBeInTheDocument();
  });
});

describe('MobileOpsLayout iPad rail', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('uses a side rail instead of bottom tabs at ≥900px', async () => {
    mockMatchMedia(true);
    const { default: MobileOpsLayout } = await import('@/mobile/MobileOpsLayout');
    const { Router } = await import('wouter');
    const { memoryLocation } = await import('wouter/memory-location');
    const { hook } = memoryLocation({ path: '/m/status' });
    render(
      <Router hook={hook}>
        <MobileOpsLayout>
          <p>body</p>
        </MobileOpsLayout>
      </Router>,
    );
    expect(screen.getByLabelText('Mobile Ops').tagName.toLowerCase()).toBe('aside');
    expect(screen.queryByRole('navigation', { name: 'Mobile Ops' })).not.toBeInTheDocument();
    expect(screen.getByText('body')).toBeInTheDocument();
  });
});
