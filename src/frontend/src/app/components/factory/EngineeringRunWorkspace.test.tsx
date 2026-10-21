import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EngineeringModeBoundary, EngineeringRunWorkspace } from './EngineeringRunWorkspace';
import { FactoryWorkspace } from './FactoryWorkspace';
import { applyEngineeringEvent, hydrateEngineeringRun, newEngineeringProjection } from '@/runtime/engineeringRunProjection';
import type { EngineeringRunSnapshot } from '@/services/api.types';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
const saved: EngineeringRunSnapshot = { session_id: 'r1', run_id: 'r1', engine_version: 'engineering_v2', workspace: '/work', workflow: 'direct', role: 'director', status: 'completed', active_roles: [], roles: { pm: 'not_called', chief_engineer: 'not_called', director: 'completed', qa: 'not_called' }, text: 'Tests passed', verification: 'failed', checks: [{ call_id: 'check', tool: 'execute_command', ok: false, content: 'assertion failed', exit_code: 1, interrupted: false }], required_checks: ['python -m pytest'], permission_profile_id: 'none', resource_url: '/v2/engineering/runs/r1', last_seq: 2, events: [{ session_id: 'r1', workspace: '/work', seq: 2, role: 'director', kind: 'tool_observed', payload: { call_id: 'c1', tool: 'read_file', ok: false, content: 'permission denied' } }] };
function binding(snapshot: EngineeringRunSnapshot | null = saved): EngineeringRunBinding {
  return { projection: snapshot ? hydrateEngineeringRun(newEngineeringProjection('/work', 'r1'), snapshot) : newEngineeringProjection('/work', ''), sessionId: snapshot?.session_id || '', loading: false, pending: null, error: null, discussion: '', discussionMetadata: null, connected: true, reconnecting: false, connectionError: null, start: vi.fn(async () => true), cancel: vi.fn(async () => true), resume: vi.fn(async () => true), refresh: vi.fn(async () => { }), discuss: vi.fn(async () => true) };
}
describe('EngineeringRunWorkspace', () => {
  it('lets users leave checks empty for Agent selection and displays the selected result command', () => {
    const run = binding(null);
    const view = render(<EngineeringRunWorkspace workspace="/work" binding={run} onBackToMain={() => { }} />);
    expect(screen.getByLabelText('验证命令')).toHaveAttribute('placeholder', expect.stringContaining('Agent 选择并执行'));
    fireEvent.change(screen.getByLabelText('工程目标'), { target: { value: 'Build and verify the project' } });
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }));
    expect(run.start).toHaveBeenCalledWith({ instruction: 'Build and verify the project', role: 'director', workflow: 'direct', required_checks: [] });
    const live = binding({ ...saved, status: 'running', required_checks: [], checks: [], verification: 'not_run' });
    const projection = applyEngineeringEvent(live.projection, { session_id: 'r1', workspace: '/work', seq: 3, role: 'director', kind: 'verification_completed', payload: { verification: 'failed', verification_commands: ['npm test'], checks: [{ call_id: 'selected-test', tool: 'execute_command', ok: false, content: 'selected test failed', exit_code: 1, interrupted: false }] } });
    view.rerender(<EngineeringRunWorkspace workspace="/work" binding={{ ...live, projection }} onBackToMain={() => { }} />);
    expect(screen.getByText('npm test')).toBeInTheDocument();
    expect(screen.getByText('selected test failed')).toBeInTheDocument();
    expect(screen.getByTestId('engineering-verification')).toHaveTextContent('未通过');
    expect(screen.getByLabelText('验证命令')).toHaveValue('');
  });
  it('labels historical checks with the original command when effective commands are absent', () => {
    render(<EngineeringRunWorkspace workspace="/work" binding={binding()} onBackToMain={() => { }} />);
    expect(screen.getByText('python -m pytest')).toBeInTheDocument();
    expect(screen.getByText('assertion failed')).toBeInTheDocument();
  });
  it('withdraws old QA approval and project acceptance as soon as a new verification starts', () => {
    const run = binding({ ...saved, verification: 'passed', require_qa: true, roles: { ...saved.roles, qa: 'completed' }, qa_verdict: 'approved', project_outcome: 'accepted', acceptance_basis: 'independent_qa' });
    const view = render(<EngineeringRunWorkspace workspace="/work" binding={run} onBackToMain={() => { }} />);
    expect(screen.getByTestId('engineering-qa-verdict')).toHaveTextContent('已批准');
    expect(screen.getByTestId('engineering-project-outcome')).toHaveTextContent('已验收');
    const projection = applyEngineeringEvent(run.projection, { session_id: 'r1', workspace: '/work', seq: 3, role: 'director', kind: 'verification_started', payload: { verification_commands: ['npm run build'], qa_verdict: 'unknown', project_outcome: 'unknown', acceptance_basis: null } });
    view.rerender(<EngineeringRunWorkspace workspace="/work" binding={{ ...run, projection }} onBackToMain={() => { }} />);
    expect(screen.getByTestId('engineering-qa-verdict')).toHaveTextContent('未知');
    expect(screen.getByTestId('engineering-qa-verdict')).not.toHaveTextContent('已批准');
    expect(screen.getByTestId('engineering-project-outcome')).toHaveTextContent('未知');
    expect(screen.getByTestId('engineering-project-outcome')).not.toHaveTextContent('已验收');
    expect(screen.getByTestId('engineering-verification')).toHaveTextContent('未运行验证');
    expect(screen.queryByText('assertion failed')).not.toBeInTheDocument();
  });
  it.each([['checks_only', '仅检查'], ['independent_qa', '独立QA']])('shows explicit acceptance basis %s without inventing a QA pass', (basis, label) => {
    render(<EngineeringRunWorkspace workspace="/work" binding={binding({ ...saved, verification: 'passed', require_qa: false, qa_verdict: 'not_run', project_outcome: 'accepted', acceptance_basis: basis } as EngineeringRunSnapshot)} onBackToMain={() => { }} />);
    expect(screen.getByTestId('engineering-project-outcome')).toHaveTextContent(`已验收（${label}）`);
    expect(screen.getByTestId('engineering-qa-verdict')).toHaveTextContent('未调用');
  });
  it('does not accept a project or pass uncalled QA merely because execution and checks completed', () => {
    render(<EngineeringRunWorkspace workspace="/work" binding={binding({ ...saved, verification: 'passed', require_qa: true, qa_verdict: 'not_run', project_outcome: 'not_accepted' } as EngineeringRunSnapshot)} onBackToMain={() => { }} />);
    expect(screen.getByTestId('engineering-verification')).toHaveTextContent('已通过');
    expect(screen.getByTestId('engineering-qa-verdict')).toHaveTextContent('未调用');
    expect(screen.getByTestId('engineering-project-outcome')).toHaveTextContent('未验收');
  });
  it('shows only an explicit actual QA verdict and project acceptance, and keeps unavailable snapshots unknown', () => {
    const run = binding({ ...saved, verification: 'passed', roles: { ...saved.roles, qa: 'completed' }, require_qa: true, qa_verdict: 'approved', project_outcome: 'accepted' } as EngineeringRunSnapshot);
    const view = render(<EngineeringRunWorkspace workspace="/work" binding={run} onBackToMain={() => { }} />);
    expect(screen.getByTestId('engineering-qa-verdict')).toHaveTextContent('已批准');
    expect(screen.getByTestId('engineering-project-outcome')).toHaveTextContent('已验收');
    view.rerender(<EngineeringRunWorkspace workspace="/work" binding={{ ...run, projection: { ...run.projection, error: 'snapshot offline' } }} onBackToMain={() => { }} />);
    expect(screen.getByTestId('engineering-qa-verdict')).toHaveTextContent('未知');
    expect(screen.getByTestId('engineering-project-outcome')).toHaveTextContent('未知');
  });
  it('keeps missing outcome metadata unknown instead of deriving acceptance from passed checks', () => {
    render(<EngineeringRunWorkspace workspace="/work" binding={binding({ ...saved, verification: 'passed' })} onBackToMain={() => { }} />);
    expect(screen.getByTestId('engineering-project-outcome')).toHaveTextContent('未知');
    expect(screen.getByTestId('engineering-qa-verdict')).toHaveTextContent('未调用');
  });
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


describe('EngineeringModeBoundary retirement', () => {
  it.each([undefined, false, true])('never mounts legacy actions when engineering binding is unavailable (%s)', (enabled) => {
    const legacyMount = vi.fn();
    function LegacyWriter() { legacyMount(); return <button>Legacy write</button>; }
    render(<EngineeringModeBoundary enabled={enabled} workspace="/work" role="director" onBackToMain={() => { }}><LegacyWriter /></EngineeringModeBoundary>);
    expect(screen.getByRole('alert')).toHaveTextContent('工程运行绑定不可用');
    expect(screen.queryByRole('button', { name: 'Legacy write' })).not.toBeInTheDocument();
    expect(legacyMount).not.toHaveBeenCalled();
  });
  it('keeps an existing engineering binding writable even when a stale legacy mode was requested', () => {
    const run = binding(null);
    const changeMode = vi.fn();
    render(<EngineeringModeBoundary enabled={false} binding={run} onModeChange={changeMode} workspace="/work" role="director" onBackToMain={() => { }}><button>Legacy write</button></EngineeringModeBoundary>);
    fireEvent.change(screen.getByLabelText('工程目标'), { target: { value: 'Continue engineering' } });
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }));
    expect(run.start).toHaveBeenCalledWith(expect.objectContaining({ instruction: 'Continue engineering' }));
    expect(screen.queryByText('查看历史界面')).not.toBeInTheDocument();
    expect(changeMode).not.toHaveBeenCalled();
  });
});
