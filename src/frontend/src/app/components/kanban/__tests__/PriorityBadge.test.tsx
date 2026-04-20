import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PriorityBadge } from '../PriorityBadge';

describe('PriorityBadge', () => {
  it('renders urgent priority', () => {
    render(<PriorityBadge priority="urgent" />);
    expect(screen.getByText('Urgent')).toBeInTheDocument();
  });

  it('renders high priority', () => {
    render(<PriorityBadge priority="high" />);
    expect(screen.getByText('High')).toBeInTheDocument();
  });

  it('renders medium priority', () => {
    render(<PriorityBadge priority="medium" />);
    expect(screen.getByText('Medium')).toBeInTheDocument();
  });

  it('renders low priority', () => {
    render(<PriorityBadge priority="low" />);
    expect(screen.getByText('Low')).toBeInTheDocument();
  });

  it('hides label when showLabel is false', () => {
    render(<PriorityBadge priority="high" showLabel={false} />);
    expect(screen.queryByText('High')).not.toBeInTheDocument();
  });

  it('contains icon element', () => {
    const { container } = render(<PriorityBadge priority="urgent" />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('has correct title attribute', () => {
    const { container } = render(<PriorityBadge priority="urgent" />);
    expect(container.querySelector('[title="Priority: Urgent"]')).toBeInTheDocument();
  });
});
