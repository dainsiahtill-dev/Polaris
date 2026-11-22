import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { EngineeringEvent } from '@/services/api.types';
import { ContextOSNavigator } from './ContextOSNavigator';

vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ nodes, onNodeClick, children }: {
    nodes: Array<{ id: string; data: { label: string } }>;
    onNodeClick?: (event: unknown, node: { id: string }) => void;
    children?: React.ReactNode;
  }) => (
    <div data-testid="contextos-nav-flow">
      {nodes.map((node) => (
        <button key={node.id} type="button" data-testid={`nav-node-${node.id}`} onClick={() => onNodeClick?.({}, node)}>
          {node.data.label}
        </button>
      ))}
      {children}
    </div>
  ),
  Background: () => <div data-testid="nav-background" />,
  Controls: () => <div data-testid="nav-controls" />,
  useNodesState: (initial: unknown) => [initial, vi.fn(), vi.fn()] as const,
  useEdgesState: (initial: unknown) => [initial, vi.fn(), vi.fn()] as const,
}));

vi.mock('./ContextOSNavigator3D', () => ({
  ContextOSNavigator3D: () => <div data-testid="nav-3d" />,
}));

const events: EngineeringEvent[] = [
  { session_id: 'run', workspace: '/ws', seq: 1, role: 'director', kind: 'run_updated', payload: { workspace_revision: 3, verification: 'not_run', status: 'running' }, created_at: 1 },
  {
    session_id: 'run', workspace: '/ws', seq: 2, role: 'director', kind: 'llm_request_observed',
    payload: {
      turn_seq: 8, model: 'kimi-for-coding', phase: 'EDIT', context_snapshot_ref: 'b'.repeat(24), status: 'ok',
      audit: { final_request_token_estimate: 6335, context_window_tokens: 1000000, context_window_utilization: 0.0063 },
    },
    created_at: 2,
  },
  {
    session_id: 'run', workspace: '/ws', seq: 3, role: 'director', kind: 'tool_observed',
    payload: { turn_seq: 8, call_id: 'call_1', tool: 'read_file', ok: true, content: JSON.stringify({ file_card: { path: 'src/app.ts', content_ref: 'filecard:src/app.ts', fold_state: 'ACTIVE', summary: '入口文件' } }) },
    created_at: 3,
  },
  { session_id: 'run', workspace: '/ws', seq: 4, role: 'resident_agi', kind: 'resident_agi_decision', payload: { project_type: 'node', reason: 'Node 项目' }, created_at: 4 },
];

const compressionMarker = '【上下文已压缩】为控制在有界上下文预算（约 32000 tokens）内，省略了 69 条较早的工具轮次；这些轮次的完整记录仍在工程运行日志中，可按证据引用取回。以下保留最近的关键轮次。';

const compressionEvents: EngineeringEvent[] = [
  ...events,
  {
    session_id: 'run', workspace: '/ws', seq: 5, role: 'director', kind: 'request_prepared',
    payload: {
      turn_seq: 8,
      request: {
        context: { phase: 'EDIT' },
        options: {
          messages: [
            { role: 'user', content: compressionMarker },
            {
              role: 'tool',
              content: JSON.stringify({
                call_id: 'call_2',
                tool: 'execute_command',
                content: JSON.stringify({
                  command_card: {
                    command: 'npm test',
                    content_ref: 'command:call_2',
                    fold_state: 'FOLDED',
                    fold_reason: 'outside_current_focus',
                    summary: '测试通过',
                    summary_kind: 'model_inference',
                  },
                }),
              }),
            },
          ],
        },
      },
    },
    created_at: 5,
  },
  {
    session_id: 'run', workspace: '/ws', seq: 6, role: 'director', kind: 'tool_observed',
    payload: {
      turn_seq: 8, call_id: 'call_2', tool: 'execute_command', ok: true,
      content: JSON.stringify({ command_card: { command: 'npm test', content_ref: 'command:call_2', fold_state: 'FOLDED', fold_reason: 'outside_current_focus', summary: '测试通过', summary_kind: 'model_inference' } }),
    },
    created_at: 6,
  },
];

describe('ContextOSNavigator', () => {
  it('renders the live structure rail with fold legend and node counts', () => {
    render(<ContextOSNavigator events={events} workspace="/ws" />);
    expect(screen.getByTestId('contextos-navigator')).toBeInTheDocument();
    expect(screen.getByTestId('nav-node-working_state')).toBeInTheDocument();
    expect(screen.getByTestId('nav-node-card:call_1')).toBeInTheDocument();
    expect(screen.getByTestId('contextos-nav-flow')).toBeInTheDocument();
    expect(screen.getByTestId('contextos-nav-tab-structure')).toHaveAttribute('aria-pressed', 'true');
  });

  it('selects a node and links its evidence back to the final request', () => {
    const onOpenContext = vi.fn();
    render(<ContextOSNavigator events={events} workspace="/ws" onOpenContext={onOpenContext} />);
    fireEvent.click(screen.getByTestId('nav-node-card:call_1'));
    expect(screen.getByTestId('contextos-nav-detail')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('contextos-nav-evidence-link'));
    expect(onOpenContext).toHaveBeenCalledWith('filecard:src/app.ts', 'director');
  });

  it('shows memory cards and live cost metrics', () => {
    const onOpenContext = vi.fn();
    render(<ContextOSNavigator events={events} workspace="/ws" onOpenContext={onOpenContext} />);
    fireEvent.click(screen.getByTestId('contextos-nav-tab-memory'));
    expect(screen.getByTestId('contextos-nav-memory')).toBeInTheDocument();
    expect(screen.getByTestId('contextos-nav-memory')).toHaveTextContent('Node 项目');
    fireEvent.click(screen.getByTestId('contextos-nav-tab-metrics'));
    expect(screen.getByTestId('contextos-nav-metrics')).toHaveTextContent('6335');
    fireEvent.click(screen.getByTestId('contextos-nav-open-final-request'));
    expect(onOpenContext).toHaveBeenCalledWith('b'.repeat(24), 'director');
  });

  it('shows the detail of the node backing a selected timeline seq', () => {
    render(<ContextOSNavigator events={events} selectedSeq={3} />);
    const detail = screen.getByTestId('contextos-nav-detail');
    expect(detail).toBeInTheDocument();
    expect(detail).toHaveTextContent('FileCard · src/app.ts');
  });

  it('shows the real host compression note and the working-set numbers', () => {
    render(<ContextOSNavigator events={compressionEvents} workspace="/ws" />);
    expect(screen.getByTestId('contextos-nav-compression-chip')).toHaveTextContent('丢弃 69 条工具轮次');
    fireEvent.click(screen.getByTestId('contextos-nav-tab-metrics'));
    const note = screen.getByTestId('contextos-nav-compression');
    expect(note).toHaveTextContent(compressionMarker);
    expect(note).toHaveTextContent('预算约 32,000 tokens');
    expect(screen.getByTestId('contextos-nav-metrics')).toHaveTextContent('蒸馏折叠（model_inference）');
    expect(screen.getByTestId('contextos-nav-metrics')).toHaveTextContent('折叠 / 未折叠卡片');
  });

  it('distinguishes distiller folding from structural fallback in the node detail', () => {
    render(<ContextOSNavigator events={compressionEvents} workspace="/ws" />);
    fireEvent.click(screen.getByTestId('nav-node-card:call_2'));
    const detail = screen.getByTestId('contextos-nav-detail');
    expect(detail).toHaveTextContent('摘要来源 · 蒸馏折叠（model_inference）');
    expect(detail).toHaveTextContent('outside_current_focus');
    expect(screen.getByTestId('contextos-nav-dropped-note')).toHaveTextContent(compressionMarker);
  });

  it('switches to the opt-in 3D exploration view', () => {
    render(<ContextOSNavigator events={events} workspace="/ws" />);
    fireEvent.click(screen.getByTestId('contextos-nav-view-3d'));
    expect(screen.getByTestId('contextos-navigator')).toHaveAttribute('data-view', '3d');
    expect(screen.getByTestId('nav-3d')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('contextos-nav-view-2d'));
    expect(screen.getByTestId('contextos-navigator')).toHaveAttribute('data-view', '2d');
  });
});
