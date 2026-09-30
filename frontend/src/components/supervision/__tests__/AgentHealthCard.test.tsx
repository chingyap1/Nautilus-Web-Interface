import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import AgentHealthCard from '@/components/supervision/AgentHealthCard';
import type { AgentHealth } from '@/services/supervisionService';

const BASE: AgentHealth = {
  agent_id: 'agent-btc',
  pair: 'XBTUSD',
  strategy: 'ma_cross',
  interval: '1h',
  execution_mode: 'paper',
  status: 'healthy',
  last_heartbeat: '2026-01-01T10:00:00Z',
  heartbeat_age_seconds: 30,
  num_fills: 3,
  balance_usd: 100000,
  unrealised_pnl: 55,
  open_positions: 1,
  source_path: null,
};

describe('AgentHealthCard', () => {
  it('hides the position detail section when no positions are published', () => {
    render(<AgentHealthCard health={{ ...BASE, positions: [] }} />);
    expect(screen.queryByText(/Position detail/i)).not.toBeInTheDocument();
  });

  it('hides the position detail section when the key is absent (older agents)', () => {
    render(<AgentHealthCard health={{ ...BASE }} />);
    expect(screen.queryByText(/Position detail/i)).not.toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument(); // open_positions still shown
  });

  it('renders each open position with side, quantity and unrealised P&L', () => {
    const health: AgentHealth = {
      ...BASE,
      open_positions: 2,
      unrealised_pnl: 50,
      positions: [
        {
          instrument_id: 'BTC/USD.KRAKEN',
          side: 'LONG',
          quantity: 0.001,
          entry_price: 95000,
          mark_price: 95550,
          mark_source: 'quote',
          unrealised_pnl: 55,
        },
        {
          instrument_id: 'ETH/USD.KRAKEN',
          side: 'SHORT',
          quantity: 0.02,
          entry_price: 2700,
          mark_price: null,
          mark_source: 'none',
          unrealised_pnl: -5,
        },
      ],
    };
    render(<AgentHealthCard health={health} />);
    expect(screen.getByText(/Position detail/i)).toBeInTheDocument();
    expect(screen.getByText('BTC/USD.KRAKEN')).toBeInTheDocument();
    expect(screen.getByText('ETH/USD.KRAKEN')).toBeInTheDocument();
    expect(screen.getByText('LONG')).toBeInTheDocument();
    expect(screen.getByText('SHORT')).toBeInTheDocument();
    expect(screen.getByText('$55.00')).toBeInTheDocument();
    expect(screen.getByText('-$5.00')).toBeInTheDocument();
  });
});
