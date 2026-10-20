import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EngineeringRunWorkspace } from './EngineeringRunWorkspace';
import { FactoryWorkspace } from './FactoryWorkspace';
import { hydrateEngineeringRun, newEngineeringProjection } from '@/runtime/engineeringRunProjection';
import type { EngineeringRunSnapshot } from '@/services/api.types';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
const saved: EngineeringRunSnapshot = { session_id: 'r1', run_id: 'r1', engine_version: 'engineering_v2', workspace: '/work', workflow: 'direct', role: 'director', status: 'completed', active_roles: [], roles: { pm: 'not_called', chief_engineer: 'not_called', director: 'completed', qa: 'not_called' }, text: 'Tests passed', verification: 'failed', checks: [{ call_id: 'check', tool: 'execute_command', ok: false, content: 'assertion failed', exit_code: 1, interrupted: false }], required_checks: ['python -m pytest'], permission_profile_id: 'none', resource_url: '/v2/engineering/runs/r1', last_seq: 2, events: [{ session_id: 'r1', workspace: '/work', seq: 2, role: 'director', kind: 'tool_observed', payload: { call_id: 'c1', tool: 'read_file', ok: false, content: 'permission denied' } }] };
function binding(snapshot: EngineeringRunSnapshot | null = saved): EngineeringRunBinding {
  return { projection: snapshot ? hydrateEngineeringRun(newEngineeringProjection('/work', 'r1'), snapshot) : newEngineeringProjection('/work', ''), sessionId: snapshot?.session_id || '', loading: false, pending: null, error: null, discussion: '', discussionMetadata: null, connected: true, reconnecting: false, connectionError: null, start: vi.fn(async () => true), cancel: vi.fn(async () => true), resume: vi.fn(async () => true), refresh: vi.fn(async () => { }), discuss: vi.fn(async () => true) };
}
describe('EngineeringRunWorkspace', () => {
  it('shows actual failed checks and tool failures while untouched roles stay not called', () => {
    render(<EngineeringRunWorkspace workspace="/work" binding={binding()} role="director" onBackToMain={() => { }} />);
    expect(screen.getByTestId('engineering-verification')).toHaveTextContent('未通过');
    expect(screen.getByTestId('engineering-role-pm')).toHaveTextContent('未调用');
    expect(screen.getByTestId('engineering-role-chief_engineer')).toHaveTextContent('未调用');
    expect(screen.getByTestId('engineering-role-qa')).toHaveTextContent('未调用');
    expect(screen.getByText('permission denied')).toBeInTheDocument();
    expect(screen.getByText('assertion failed')).toBeInTheDocument();
    expect(screen.queryByText('蓝图覆盖')).not.toBeInTheDocument();
  });
  it('starts the explicit selected role directly, carrying goal and actual required checks', () => {
    const run = binding(null);
    render(<EngineeringRunWorkspace workspace="/work" binding={run} role="director" onBackToMain={() => { }} />);
    fireEvent.change(screen.getByLabelText('工程目标'), { target: { value: 'Implement the requested change' } });
    fireEvent.change(screen.getByLabelText('验证命令'), { target: { value: 'python -m pytest\npython -m compileall src' } });
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }));
    expect(run.start).toHaveBeenCalledWith({ instruction: 'Implement the requested change', role: 'director', workflow: 'direct', required_checks: ['python -m pytest', 'python -m compileall src'] });
  });
  it('uses the new start action before legacy Factory ledger and blueprint gates', () => {
    const run = binding(null);
    const oldStart = vi.fn(() => { throw new Error('legacy admission called'); });
    render(<FactoryWorkspace workspace="/work" tasks={[]} onBackToMain={() => { }} onStart={oldStart} engineeringEnabled engineering={run} />);
    fireEvent.change(screen.getByLabelText('工程目标'), { target: { value: 'Direct change' } });
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }));
    expect(run.start).toHaveBeenCalledOnce();
    expect(oldStart).not.toHaveBeenCalled();
    expect(screen.queryByText('PM 任务合同')).not.toBeInTheDocument();
  });
  it('does not turn discussion into a run, and exposes pending/disconnected feedback', () => {
    const run = binding(null);
    const view = render(<EngineeringRunWorkspace workspace="/work" binding={run} role="pm" onBackToMain={() => { }} />);
    fireEvent.change(screen.getByLabelText('工程目标'), { target: { value: 'Discuss this plan' } });
    fireEvent.click(screen.getByRole('button', { name: '讨论方案' }));
    expect(run.discuss).toHaveBeenCalledWith('pm', 'Discuss this plan');
    expect(run.start).not.toHaveBeenCalled();
    view.rerender(<EngineeringRunWorkspace workspace="/work" binding={{ ...run, pending: 'start', connected: false }} role="pm" onBackToMain={() => { }} />);
    expect(screen.getByRole('button', { name: '启动中…' })).toBeDisabled();
    expect(screen.getByText(/实时连接已断开/)).toBeInTheDocument();
  });
});
