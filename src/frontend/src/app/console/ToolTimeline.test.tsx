import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ToolTimeline } from './ToolTimeline';
import type { EngineeringEvent } from '@/services/api.types';

// 实时流只带图像元数据；像素由观察端点按需取回。
vi.mock('@/services/engineeringRunService', () => ({
  getEngineeringObservationImage: vi.fn(),
}));

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
        activity_age: 12,
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
    expect(screen.getByTestId('story-fold-5')).toHaveTextContent('12 轮前');
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
        // Real host shape: the node summary sits inside result, one per endpoint.
        summaries: [
          {
            endpoint_index: 0,
            validation_result: 'VALID',
            result: { node_id: 'core.js', summary: '状态机与循环入口' },
          },
          {
            endpoint_index: 1,
            validation_result: 'REJECTED',
            error: 'ValueError: summary must describe the payload',
          },
        ],
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
    expect(screen.getByTestId('story-advisor-6')).toHaveTextContent('蒸馏器 · 蒸馏内容 · 1 个节点');
    expect(screen.getByTestId('story-advisor-6')).toHaveTextContent('core.js → 状态机与循环入口');
  });

  it('shows Devourer epoch summaries in the live timeline', () => {
    const epoch: EngineeringEvent = {
      session_id: 'run',
      workspace: '/ws',
      seq: 7,
      role: 'devourer',
      kind: 'epoch_card_completed',
      payload: { id: 'epoch-0001-0010', summary: '完成基础模块，仍有失败。', validation_result: 'VALID' },
    };
    render(
      <ToolTimeline
        events={[epoch]}
        selectedSeq={null}
        onSelect={vi.fn()}
        onOpenContext={vi.fn()}
        emptyHint="empty"
      />,
    );
    expect(screen.getByTestId('story-epoch-7')).toHaveTextContent('Devourer 吞噬兽 · epoch-0001-0010');
  });

  it('fetches the frame bytes from the run API instead of a placeholder', async () => {
    // 事件流只带元数据（sha256/media_type），像素由观察端点按需取回。
    const { getEngineeringObservationImage } = await import('@/services/engineeringRunService');
    vi.mocked(getEngineeringObservationImage).mockResolvedValue({ ok: true, data: 'blob:mock-frame' });
    const observed: EngineeringEvent = {
      session_id: 'run',
      workspace: '/ws',
      seq: 8,
      role: 'director',
      kind: 'tool_observed',
      payload: {
        tool: 'browser_observe',
        ok: true,
        call_id: 'cap-8',
        images: [{ media_type: 'image/jpeg', sha256: 'abc' }],
      },
    };
    render(
      <ToolTimeline
        events={[observed]}
        runId="run"
        workspace="/ws"
        selectedSeq={null}
        onSelect={vi.fn()}
        onOpenContext={vi.fn()}
        emptyHint="empty"
      />,
    );
    expect(await screen.findByAltText('浏览器截图 1')).toHaveAttribute('src', 'blob:mock-frame');
    expect(getEngineeringObservationImage).toHaveBeenCalledWith('run', 'cap-8', 0, '/ws');
  });

  it('boxes every tool call of one turn under that turn number', () => {
    const turn = (seq: number, turnSeq: number): EngineeringEvent => ({
      ...event(seq),
      payload: { ...event(seq).payload, turn_seq: turnSeq },
    });
    render(
      <ToolTimeline
        events={[turn(1, 3), turn(2, 3), turn(3, 4)]}
        selectedSeq={null}
        onSelect={vi.fn()}
        onOpenContext={vi.fn()}
        emptyHint="empty"
      />,
    );
    const third = screen.getByTestId('story-turn-3');
    expect(third).toHaveTextContent('第 3 轮');
    expect(third).toHaveTextContent('2 次工具调用');
    expect(within(third).getAllByTestId(/^story-tool-/)).toHaveLength(2);
    const fourth = screen.getByTestId('story-turn-4');
    expect(fourth).toHaveTextContent('第 4 轮');
    expect(within(fourth).getAllByTestId(/^story-tool-/)).toHaveLength(1);
  });

  it('keeps one box per turn when the request marker interleaves its tools', () => {
    const at = (seq: number, turnSeq: number, kind: string = 'tool_observed'): EngineeringEvent => ({
      ...event(seq),
      kind,
      payload: { ...event(seq).payload, turn_seq: turnSeq },
    });
    render(
      <ToolTimeline
        events={[
          at(1, 7, 'llm_request_observed'),
          at(2, 7),
          at(3, 7, 'llm_request_observed'),
          at(4, 7),
          at(5, 8),
        ]}
        selectedSeq={null}
        onSelect={vi.fn()}
        onOpenContext={vi.fn()}
        emptyHint="empty"
      />,
    );
    // llm_request_observed carries the same turn_seq and splits the turn in two;
    // splitting produced two `turn-7` boxes and duplicate React keys.
    expect(screen.getAllByTestId('story-turn-7')).toHaveLength(1);
    expect(within(screen.getByTestId('story-turn-7')).getAllByTestId(/^story-/).length).toBeGreaterThan(0);
    expect(screen.getAllByTestId('story-turn-8')).toHaveLength(1);
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
