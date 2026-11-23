import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { EngineeringEvent, EngineeringRunSnapshot } from '@/services/api.types';
import { MissionControlView } from './MissionControlView';

function snapshot(overrides: Partial<EngineeringRunSnapshot> = {}): EngineeringRunSnapshot {
  return {
    session_id: 'engineering-1',
    run_id: 'engineering-1',
    engine_version: 'engineering_v2',
    workspace: '/workspace/pelican',
    workflow: 'direct',
    role: 'director',
    status: 'running',
    active_roles: ['director'],
    roles: { pm: 'not_called', chief_engineer: 'not_called', director: 'running', qa: 'not_called' },
    text: '',
    verification: 'not_run',
    checks: [],
    required_checks: [],
    permission_profile_id: null,
    resource_url: '/v2/engineering/runs/engineering-1',
    last_seq: 4,
    events: [],
    ...overrides,
  };
}

function event(seq: number, kind: string, payload: Record<string, unknown>, created_at?: number): EngineeringEvent {
  return { session_id: 'engineering-1', workspace: '/workspace/pelican', seq, role: 'director', kind, payload, created_at };
}

function makeBinding(overrides: Record<string, unknown> = {}) {
  const base = {
    projection: { snapshot: snapshot(), events: [] as EngineeringEvent[], error: null, snapshotRequiredSeq: null, historyWindowed: false },
    sessionId: 'engineering-1',
    loading: false,
    pending: null,
    error: null,
    discussion: '',
    discussionMetadata: null,
    connected: true,
    reconnecting: false,
    connectionError: null,
    start: vi.fn(), cancel: vi.fn(), resume: vi.fn(), refresh: vi.fn(), discuss: vi.fn(),
  };
  return { ...base, ...overrides } as never;
}

afterEach(cleanup);

describe('MissionControlView', () => {
  it('renders the new-run composer when no run exists', () => {
    const binding = makeBinding({ projection: { snapshot: null, events: [], error: null, snapshotRequiredSeq: null, historyWindowed: false }, sessionId: '' });
    render(<MissionControlView workspace="/workspace/pelican" binding={binding} role="director" />);
    expect(screen.getByRole('heading', { name: '新建工程运行' })).toBeInTheDocument();
    expect(screen.getByLabelText('工程目标')).toBeInTheDocument();
    expect(screen.getByText('直接执行')).toBeInTheDocument();
    expect(screen.getByText('协作执行')).toBeInTheDocument();
    expect(screen.getByText('运行环境')).toBeInTheDocument();
    expect(screen.getByText('本次验收')).toBeInTheDocument();
  });

  it('renders live run layout with role cards, banner, timeline and verdict strip', () => {
    const events = [
      event(1, 'tool_observed', { tool: 'repo_tree', ok: true, content: 'src/ · tests/' }, 1_700_000_000_000),
      event(2, 'tool_observed', { tool: 'execute_command', ok: false, content: 'FAIL tests/playback.test.ts\nexpected: 0.00 rad\nreceived: 0.14 rad' }, 1_700_000_060_000),
    ];
    const binding = makeBinding({ projection: { snapshot: snapshot(), events, error: null, snapshotRequiredSeq: null, historyWindowed: false } });
    render(<MissionControlView workspace="/workspace/pelican" binding={binding} role="director" />);
    expect(screen.getByRole('heading', { name: '工程运行实况' })).toBeInTheDocument();
    expect(screen.getByTestId('engineering-role-director')).toHaveTextContent('进行中');
    expect(screen.getByTestId('engineering-role-pm')).toHaveTextContent('not_called');
    expect(screen.getByText('验证未通过，Director 正在根据验证输出修复。')).toBeInTheDocument();
    expect(screen.getByTestId('story-tool-1')).toBeInTheDocument();
    expect(screen.getByTestId('engineering-verification')).toHaveTextContent('未运行验证');
    expect(screen.getByTestId('engineering-project-outcome')).toHaveTextContent('未知');
    expect(screen.getByRole('button', { name: '取消运行' })).toBeInTheDocument();
  });

  it('shows the current RunCard with its phase, intent and next actions', () => {
    const events = [
      event(3, 'cartographer_completed', {
        phase: 'EDIT',
        intent: '补齐花园渲染层与帧循环',
        reason: '验证失败落在 renderer',
        completed: ['建立画布与固定步长循环'],
        next_actions: [{ action: '补 renderer', why: '验证失败', expected: '渲染通过' }],
        blocker: null,
        uncertain: false,
        evidence_refs: ['call-1'],
      }, 1_700_000_000_000),
    ];
    const binding = makeBinding({ projection: { snapshot: snapshot(), events, error: null, snapshotRequiredSeq: null, historyWindowed: false } });
    render(<MissionControlView workspace="/workspace/pelican" binding={binding} role="director" />);
    const pane = within(screen.getByTestId('run-card-pane'));
    expect(pane.getByText('当前 RunCard · 制图师')).toBeInTheDocument();
    expect(pane.getByText('EDIT')).toBeInTheDocument();
    expect(pane.getByText('补齐花园渲染层与帧循环')).toBeInTheDocument();
    expect(pane.getByText('建立画布与固定步长循环')).toBeInTheDocument();
    expect(pane.getByText('补 renderer')).toBeInTheDocument();
  });

  it('says the RunCard has not been drawn yet when the cartographer is silent', () => {
    const binding = makeBinding();
    render(<MissionControlView workspace="/workspace/pelican" binding={binding} role="director" />);
    const pane = within(screen.getByTestId('run-card-pane'));
    expect(pane.getByText('尚未绘制')).toBeInTheDocument();
    expect(pane.getByText(/每 12 轮绘制一张 RunCard/)).toBeInTheDocument();
  });

  it('shows the selected receipt in the command-receipt panel', () => {
    const events = [event(2, 'tool_observed', { tool: 'execute_command', ok: false, content: 'FAIL playback\nexit code 1' })];
    const binding = makeBinding({ projection: { snapshot: snapshot(), events, error: null, snapshotRequiredSeq: null, historyWindowed: false } });
    render(<MissionControlView workspace="/workspace/pelican" binding={binding} role="director" />);
    fireEvent.click(screen.getByTestId('story-tool-2'));
    expect(screen.getByText('工具 · execute_command')).toBeInTheDocument();
    expect(screen.getByText('检查未通过')).toBeInTheDocument();
    const detail = within(screen.getByTestId('output-detail-panel'));
    expect(detail.getByText(/FAIL playback/)).toBeInTheDocument();
    expect(detail.getByText('检查未通过，已回到修复流程')).toBeInTheDocument();
  });

  it('switches to acceptance title and banner once the project is accepted', () => {
    const done = snapshot({
      status: 'completed',
      active_roles: [],
      roles: { pm: 'not_called', chief_engineer: 'not_called', director: 'completed', qa: 'completed' },
      verification: 'passed',
      qa_verdict: 'approved',
      require_qa: true,
      project_outcome: 'accepted',
      acceptance_basis: 'independent_qa',
      text: '完成：鹈鹕骑行公园',
      checks: [{ call_id: 'c1', tool: 'execute_command', ok: true, content: '35/35 PASS', exit_code: 0, interrupted: false }],
      verification_commands: ['npm test'],
    });
    const binding = makeBinding({ projection: { snapshot: done, events: [], error: null, snapshotRequiredSeq: null, historyWindowed: false } });
    render(<MissionControlView workspace="/workspace/pelican" binding={binding} role="director" />);
    expect(screen.getByRole('heading', { name: '交付验收' })).toBeInTheDocument();
    expect(screen.getByText('项目已验收（独立QA）。完整运行记录保持可回看。')).toBeInTheDocument();
    expect(screen.getByTestId('engineering-verification')).toHaveTextContent('已通过');
    expect(screen.getByTestId('engineering-qa-verdict')).toHaveTextContent('已批准');
    expect(screen.getByTestId('engineering-project-outcome')).toHaveTextContent('已验收');
  });
});
