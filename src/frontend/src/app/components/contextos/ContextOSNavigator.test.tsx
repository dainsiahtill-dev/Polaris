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

  it('switches to the opt-in 3D exploration view', () => {
    render(<ContextOSNavigator events={events} workspace="/ws" />);
    fireEvent.click(screen.getByTestId('contextos-nav-view-3d'));
    expect(screen.getByTestId('contextos-navigator')).toHaveAttribute('data-view', '3d');
    expect(screen.getByTestId('nav-3d')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('contextos-nav-view-2d'));
    expect(screen.getByTestId('contextos-navigator')).toHaveAttribute('data-view', '2d');
  });
});
