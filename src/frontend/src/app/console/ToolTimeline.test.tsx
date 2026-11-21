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
  it('shows an animated folding marker with the fold reason code', () => {
    const folded: EngineeringEvent = {
      session_id: 'run',
      workspace: '/ws',
      seq: 4,
      role: 'director',
      kind: 'tool_observed',
      payload: {
        tool: 'execute_command',
        ok: true,
        command_card: { fold_state: 'FOLDED', fold_reason_code: 'DUPLICATE_PAYLOAD' },
      },
    };
    render(
      <ToolTimeline
        events={[event(1), folded]}
        selectedSeq={null}
        onSelect={vi.fn()}
        onOpenContext={vi.fn()}
        emptyHint="empty"
      />,
    );
    expect(screen.getByTestId('fold-marker')).toHaveTextContent('DUPLICATE_PAYLOAD');
  });

  it('renders a realtime context_projection_folded event as an animated card', () => {
    const folded: EngineeringEvent = {
      session_id: 'run',
      workspace: '/ws',
      seq: 5,
      role: 'director',
      kind: 'context_projection_folded',
      payload: {
        path: 'index.html',
        projection_ref: 'filecard:abc',
        fold_state: 'FOLDED',
        fold_reason_code: 'INACTIVE_PAYLOAD',
      },
    };
    render(
      <ToolTimeline
        events={[folded]}
        selectedSeq={null}
        onSelect={vi.fn()}
        onOpenContext={vi.fn()}
        emptyHint="empty"
      />,
    );
    expect(screen.getByTestId('story-fold-5')).toHaveTextContent('智能折叠已触发');
    expect(screen.getByTestId('story-fold-5')).toHaveTextContent('index.html');
    expect(screen.getByTestId('fold-marker')).toHaveTextContent('INACTIVE_PAYLOAD');
  });

  it('shows remote advisor evaluations in the live timeline', () => {
    const advice: EngineeringEvent = {
      session_id: 'run',
      workspace: '/ws',
      seq: 6,
      role: 'director',
      kind: 'semantic_advice_completed',
      payload: {
        validation_result: 'VALID',
        result: { assessments: [{ suggested_view: 'CARD' }] },
      },
    };
    render(
      <ToolTimeline
        events={[advice]}
        selectedSeq={null}
        onSelect={vi.fn()}
        onOpenContext={vi.fn()}
        emptyHint="empty"
      />,
    );
    expect(screen.getByTestId('story-advisor-6')).toHaveTextContent('远程小模型评估 · CARD');
  });

  it('shows the newest event first so users do not need to scroll to the bottom', () => {
    render(
      <ToolTimeline
        events={[event(1), event(2), event(3)]}
        selectedSeq={null}
        onSelect={vi.fn()}
        onOpenContext={vi.fn()}
        emptyHint="empty"
      />,
    );
    const tools = screen.getAllByTestId(/^story-tool-/);
    expect(tools[0]).toHaveAttribute('data-testid', 'story-tool-3');
    expect(tools[1]).toHaveAttribute('data-testid', 'story-tool-2');
    expect(tools[2]).toHaveAttribute('data-testid', 'story-tool-1');
  });
});
