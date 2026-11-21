import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ToolTimeline } from './ToolTimeline';
import type { EngineeringEvent } from '@/services/api.types';

function event(seq: number): EngineeringEvent {
  return {
    session_id: 'run',
    workspace: '/ws',
    seq,
    role: 'director',
    kind: 'tool_observed',
    payload: { call_id: `call-${seq}`, tool: 'execute_command', ok: true, content: `done-${seq}` },
  };
}

describe('ToolTimeline live ordering', () => {
  it('shows the newest event first so users do not need to scroll to the bottom', () => {
    render(<ToolTimeline events={[event(1), event(2), event(3)]} selectedSeq={null} onSelect={vi.fn()} onOpenContext={vi.fn()} emptyHint="empty" />);
    const tools = screen.getAllByTestId(/^story-tool-/);
    expect(tools[0]).toHaveAttribute('data-testid', 'story-tool-3');
    expect(tools[1]).toHaveAttribute('data-testid', 'story-tool-2');
    expect(tools[2]).toHaveAttribute('data-testid', 'story-tool-1');
  });
});
