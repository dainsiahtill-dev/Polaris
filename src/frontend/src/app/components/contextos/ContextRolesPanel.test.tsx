import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ContextRolesPanel } from './ContextRolesPanel';
import type { EngineeringEvent } from '@/services/api.types';

function event(seq: number, role: string, payload: Record<string, unknown>, createdAt: number): EngineeringEvent {
  return { session_id: 'run', workspace: '/ws', seq, role, kind: 'tool_observed', payload, created_at: createdAt };
}

const now = 1_800_000_000;

describe('ContextRolesPanel', () => {
  it('renders distiller, devourer and cartographer with idle state', () => {
    render(<ContextRolesPanel events={[]} now={now} />);
    expect(screen.getByTestId('context-role-distiller')).toHaveTextContent('Distiller');
    expect(screen.getByTestId('context-role-devourer')).toHaveTextContent('Devourer');
    expect(screen.getByTestId('context-role-cartographer')).toHaveTextContent('Cartographer');
    expect(screen.getByTestId('context-role-distiller-state')).toHaveTextContent('待命');
  });

  it('shows working state and current task for recent activity', () => {
    const events = [
      event(1, 'distiller', { agent_role: 'distiller', summaries: [{ node_id: 'core.js' }] }, now - 10),
      event(2, 'devourer', { agent_role: 'devourer', id: 'epoch-0001-0010', summary: '完成基础模块' }, now - 20),
      event(3, 'cartographer', { agent_role: 'cartographer', summary: '绘制当前阶段' }, now - 30),
    ];
    render(<ContextRolesPanel events={events} now={now} />);
    expect(screen.getByTestId('context-role-distiller-state')).toHaveTextContent('工作中');
    expect(screen.getByTestId('context-role-distiller')).toHaveTextContent('蒸馏节点：core.js');
    expect(screen.getByTestId('context-role-devourer')).toHaveTextContent('吞噬 epoch-0001-0010');
    expect(screen.getByTestId('context-role-cartographer')).toHaveTextContent('绘制运行地图');
  });
});
