import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import StepUpPrompt from '@/components/supervision/StepUpPrompt';

describe('StepUpPrompt', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders the TOTP input and verify button', () => {
    render(<StepUpPrompt onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByPlaceholderText('000000')).toBeInTheDocument();
    expect(screen.getByText('Verify')).toBeInTheDocument();
  });

  it('disables verify button when code is less than 6 digits', () => {
    render(<StepUpPrompt onSubmit={vi.fn()} onCancel={vi.fn()} />);
    const verifyBtn = screen.getByText('Verify');
    expect(verifyBtn).toBeDisabled();
  });

  it('enables verify button when code is 6+ digits', () => {
    render(<StepUpPrompt onSubmit={vi.fn()} onCancel={vi.fn()} />);
    const input = screen.getByPlaceholderText('000000');
    fireEvent.change(input, { target: { value: '123456' } });
    expect(screen.getByText('Verify')).not.toBeDisabled();
  });

  it('calls onSubmit with the code when verify is clicked', () => {
    const onSubmit = vi.fn();
    render(<StepUpPrompt onSubmit={onSubmit} onCancel={vi.fn()} />);
    const input = screen.getByPlaceholderText('000000');
    fireEvent.change(input, { target: { value: '123456' } });
    fireEvent.click(screen.getByText('Verify'));
    expect(onSubmit).toHaveBeenCalledWith('123456');
  });

  it('calls onCancel when X button is clicked', () => {
    const onCancel = vi.fn();
    render(<StepUpPrompt onSubmit={vi.fn()} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole('button', { name: '' }));
    expect(onCancel).toHaveBeenCalled();
  });

  it('shows loading state', () => {
    render(<StepUpPrompt onSubmit={vi.fn()} onCancel={vi.fn()} loading />);
    expect(screen.getByText('Verifying…')).toBeInTheDocument();
  });

  it('shows error message', () => {
    render(<StepUpPrompt onSubmit={vi.fn()} onCancel={vi.fn()} error="Invalid code" />);
    expect(screen.getByText('Invalid code')).toBeInTheDocument();
  });

  it('strips non-numeric characters from input', () => {
    render(<StepUpPrompt onSubmit={vi.fn()} onCancel={vi.fn()} />);
    const input = screen.getByPlaceholderText('000000') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'abc123def' } });
    expect(input.value).toBe('123');
  });
});
