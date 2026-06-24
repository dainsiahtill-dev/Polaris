import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProjectProgressPanel, extractLatestQaEvidence, extractRunLedgerQaEvidence } from './ProjectProgressPanel';
import type { DialogueEvent } from './DialoguePanel';
import type { LogEntry } from './pm';
import type { ControlPlaneProjection } from '@/services/controlPlane';

const qaLog: LogEntry = {
  id: 'qa-1',
  timestamp: '2026-06-02T00:00:00.000Z',
  level: 'success',
  source: 'QA',
  message: 'Project integration QA passed',
  meta: {
    reason: 'integration_qa_passed',
    evidence_grade: 'real_command_passed',
    summary: 'Integration verification passed: npm run test',
    passed: true,
  },
};

const qaDialogueEvent: DialogueEvent = {
  eventId: 'qa-dialogue-1',
  speaker: 'QA',
  type: 'review',
  content: 'Project integration QA: PASS; Integration verification passed: npm run test -- --watch=false',
  refs: { phase: 'integration_qa' },
  meta: {
    reason: 'integration_qa_passed',
    evidence_grade: 'real_command_passed',
    summary: 'Integration verification passed: npm run test -- --watch=false',
    passed: true,
  },
};

const failedLedgerProjection: ControlPlaneProjection = {
  schema_version: 1,
  source: 'run_ledger_projection',
  available: true,
  ok: false,
  status: 'failed',
  audit_path: 'runtime/control_plane/ledger/run.jsonl',
  compat_ledgers_included: false,
  total: 1,
  projected: 1,
  missing: 0,
  failed: 1,
  detail: '1 failed gate',
  projects: [
    {
      project_id: 'project-1',
      ok: false,
      integrity_ok: true,
      outcome_ok: false,
      gate_count: 2,
      failed_gate_count: 1,
      latest_token_id: 'jt-failed',
      detail: 'build gate failed',
      missing: [],
    },
  ],
};

const pendingLedgerProjection: ControlPlaneProjection = {
  schema_version: 1,
  source: 'run_ledger_projection',
  available: true,
  ok: false,
  status: 'pending',
  audit_path: 'runtime/control_plane/ledger',
  compat_ledgers_included: false,
  total: 0,
  projected: 0,
  missing: 0,
  failed: 0,
  detail: 'run ledger projection is pending',
  projects: [],
};

const passedLedgerProjection: ControlPlaneProjection = {
  schema_version: 1,
  source: 'run_ledger_projection',
  available: true,
  ok: true,
  status: 'passed',
  audit_path: 'runtime/control_plane/ledger/run.jsonl',
  compat_ledgers_included: false,
  total: 1,
  projected: 1,
  missing: 0,
  failed: 0,
  detail: 'run ledger projected successfully',
  projects: [
    {
      project_id: 'project-1',
      ok: true,
      integrity_ok: true,
      outcome_ok: true,
      gate_count: 2,
      failed_gate_count: 0,
      latest_token_id: 'jt-passed',
      detail: 'all gates passed',
      missing: [],
    },
  ],
};

describe('ProjectProgressPanel QA evidence', () => {
  it('extracts the latest integration QA evidence grade from runtime logs', () => {
    expect(extractLatestQaEvidence([qaLog])).toEqual({
      grade: 'real_command_passed',
      reason: 'integration_qa_passed',
      summary: 'Integration verification passed: npm run test',
      passed: true,
    });
  });

  it('holds QA pass logs until Run Ledger projection is available', () => {
    render(
      <ProjectProgressPanel
        tasks={[]}
        pmRunning={false}
        executionLogs={[qaLog]}
        currentPhase="qa"
      />,
    );

    const evidence = screen.getByTestId('qa-evidence-grade');
    expect(evidence).toHaveTextContent('QA evidence');
    expect(evidence).toHaveTextContent('run ledger pending');
    expect(evidence).toHaveTextContent('run_ledger_required');
    expect(evidence).not.toHaveTextContent('real command passed');
  });

  it('extracts QA evidence from Run Ledger projection', () => {
    expect(extractRunLedgerQaEvidence(failedLedgerProjection)).toEqual({
      grade: 'run_ledger_failed',
      reason: 'run_ledger_failed_gate:jt-failed',
      summary: 'build gate failed',
      passed: false,
    });
  });

  it('prioritizes Run Ledger failure over stale QA log success', () => {
    render(
      <ProjectProgressPanel
        tasks={[]}
        pmRunning={false}
        executionLogs={[qaLog]}
        currentPhase="qa"
        controlPlaneProjection={failedLedgerProjection}
      />,
    );

    const evidence = screen.getByTestId('qa-evidence-grade');
    expect(evidence).toHaveTextContent('QA evidence');
    expect(evidence).toHaveTextContent('run ledger failed');
    expect(evidence).toHaveTextContent('run_ledger_failed_gate:jt-failed');
    expect(evidence).not.toHaveTextContent('real command passed');
  });

  it('does not fall back to stale QA success while Run Ledger projection is pending', () => {
    render(
      <ProjectProgressPanel
        tasks={[]}
        pmRunning={false}
        executionLogs={[qaLog]}
        currentPhase="qa"
        controlPlaneProjection={pendingLedgerProjection}
      />,
    );

    const evidence = screen.getByTestId('qa-evidence-grade');
    expect(evidence).toHaveTextContent('QA evidence');
    expect(evidence).toHaveTextContent('run ledger pending');
    expect(evidence).toHaveTextContent('run_ledger_pending');
    expect(evidence).not.toHaveTextContent('real command passed');
  });

  it('does not show terminal progress success while Run Ledger projection is pending', () => {
    const tasks = [
      { id: '1', subject: '实现 CLI 科学计算器核心模块', status: 'completed' },
      { id: '2', subject: '编写 README', status: 'completed' },
      { id: '3', subject: '实现验证与 QA 闭环', status: 'completed' },
    ];

    render(
      <ProjectProgressPanel
        tasks={tasks}
        directorTasks={tasks}
        pmRunning={false}
        pmState={{
          completed_task_count: 3,
          last_director_status: 'success',
        }}
        controlPlaneProjection={pendingLedgerProjection}
      />,
    );

    expect(screen.getByText('等待 Run Ledger 证据 · 2/3')).toBeInTheDocument();
    expect(screen.getByText('67%')).toBeInTheDocument();
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
    expect(screen.getByTestId('project-chain-role-director')).toHaveTextContent('blocked');
    expect(screen.getByTestId('project-chain-role-director')).not.toHaveTextContent('success');
  });

  it('prevents runtime role completion from bypassing a pending Run Ledger projection', () => {
    const tasks = [
      { id: '1', subject: '实现 CLI 科学计算器核心模块', status: 'completed' },
      { id: '2', subject: '编写 README', status: 'completed' },
      { id: '3', subject: '实现验证与 QA 闭环', status: 'completed' },
    ];

    render(
      <ProjectProgressPanel
        tasks={tasks}
        directorTasks={tasks}
        pmRunning={false}
        pmState={{
          completed_task_count: 3,
          last_director_status: 'success',
        }}
        engineStatus={{
          roles: {
            PM: { status: 'completed', task_id: '1', task_title: 'PM 合同已完成' },
            ChiefEngineer: { status: 'completed', task_id: '1', task_title: 'CE 蓝图已完成' },
            Director: { status: 'success', task_id: '3', task_title: 'Director 已执行' },
          },
        }}
        controlPlaneProjection={pendingLedgerProjection}
      />,
    );

    for (const role of ['pm', 'chief-engineer', 'director']) {
      const card = screen.getByTestId(`project-chain-role-${role}`);
      expect(card).toHaveTextContent('blocked');
      expect(card).not.toHaveTextContent('success');
      expect(card).not.toHaveTextContent('completed');
    }
  });

  it('allows runtime role completion after Run Ledger projection passes', () => {
    const tasks = [
      { id: '1', subject: '实现 CLI 科学计算器核心模块', status: 'completed' },
      { id: '2', subject: '编写 README', status: 'completed' },
      { id: '3', subject: '实现验证与 QA 闭环', status: 'completed' },
    ];

    render(
      <ProjectProgressPanel
        tasks={tasks}
        directorTasks={tasks}
        pmRunning={false}
        pmState={{
          completed_task_count: 3,
          last_director_status: 'success',
        }}
        engineStatus={{
          roles: {
            PM: { status: 'completed', task_id: '1', task_title: 'PM 合同已完成' },
            ChiefEngineer: { status: 'completed', task_id: '1', task_title: 'CE 蓝图已完成' },
            Director: { status: 'success', task_id: '3', task_title: 'Director 已执行' },
          },
        }}
        controlPlaneProjection={passedLedgerProjection}
      />,
    );

    expect(screen.getByTestId('project-chain-role-pm')).toHaveTextContent('completed');
    expect(screen.getByTestId('project-chain-role-chief-engineer')).toHaveTextContent('completed');
    expect(screen.getByTestId('project-chain-role-director')).toHaveTextContent('success');
  });

  it('recovers QA evidence from dialogue transcript metadata after reload', () => {
    expect(extractLatestQaEvidence([], [qaDialogueEvent])).toEqual({
      grade: 'real_command_passed',
      reason: 'integration_qa_passed',
      summary: 'Integration verification passed: npm run test -- --watch=false',
      passed: true,
    });

    render(
      <ProjectProgressPanel
        tasks={[]}
        pmRunning={false}
        executionLogs={[]}
        dialogueEvents={[qaDialogueEvent]}
        currentPhase="qa"
      />,
    );

    const evidence = screen.getByTestId('qa-evidence-grade');
    expect(evidence).toHaveTextContent('QA evidence');
    expect(evidence).toHaveTextContent('run ledger pending');
    expect(evidence).toHaveTextContent('run_ledger_required');
    expect(evidence).not.toHaveTextContent('real command passed');
  });

  it('renders the PM to Chief Engineer to Director chain and ignores numeric task titles', () => {
    render(
      <ProjectProgressPanel
        tasks={[
          {
            id: 1 as unknown as string,
            title: 1 as unknown as string,
            subject: '实现账户服务 API',
            goal: '交付可运行的账户服务',
            priority: 1,
            status: 'in_progress',
            acceptance: [{ description: '账户服务测试通过' }],
          },
        ]}
        pmRunning={false}
        engineStatus={{
          roles: {
            PM: { status: 'completed', task_id: '1', task_title: '实现账户服务 API' },
            ChiefEngineer: { status: 'running', task_id: '1', task_title: '蓝图审查' },
            Director: { status: 'waiting', task_id: '1', task_title: '等待实现' },
          },
        }}
      />,
    );

    expect(screen.getByTestId('project-chain-heading')).toHaveTextContent('PM → Chief Engineer → Director');
    expect(screen.getByTestId('project-chain-role-pm')).toHaveTextContent('PM');
    expect(screen.getByTestId('project-chain-role-chief-engineer')).toHaveTextContent('Chief Engineer');
    expect(screen.getByTestId('project-chain-role-director')).toHaveTextContent('Director');
    expect(screen.getByTestId('project-task-title')).toHaveTextContent('实现账户服务 API');
    expect(screen.getByTestId('project-task-title')).not.toHaveTextContent(/^1$/);
    expect(screen.queryByText(`任务队列（${['PM', 'Director'].join(' → ')}）`)).not.toBeInTheDocument();
  });

  it('puts readable task titles before sequence and priority metadata in the task queue', () => {
    render(
      <ProjectProgressPanel
        tasks={[
          {
            id: 1 as unknown as string,
            title: 1 as unknown as string,
            subject: '实现创建语义化 HTML5 简历结构',
            goal: '构建个人简历的语义化 HTML5 骨架',
            priority: 1,
            status: 'in_progress',
          },
          {
            id: 2 as unknown as string,
            title: 2 as unknown as string,
            subject: '实现响应式 CSS3 样式表',
            goal: '通过 CSS Grid 和 Flexbox 适配桌面与移动端',
            priority: 1,
            status: 'pending',
          },
          {
            id: 3 as unknown as string,
            title: 3 as unknown as string,
            subject: '交付验证与 README 编写',
            goal: '完成 QA 闭环并编写运行说明',
            priority: 1,
            status: 'pending',
          },
        ]}
        pmRunning={false}
      />,
    );

    const rows = screen.getAllByTestId('project-task-item');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent(/^实现创建语义化 HTML5 简历结构/);
    expect(rows[1]).toHaveTextContent(/^实现响应式 CSS3 样式表/);
    expect(rows[2]).toHaveTextContent(/^交付验证与 README 编写/);
  });

  it('shows CE handoff and completed Director queue instead of stale waiting labels', () => {
    const tasks = [
      { id: '1', subject: '实现 CLI 科学计算器核心模块', status: 'completed' },
      { id: '2', subject: '编写 README', status: 'completed' },
      { id: '3', subject: '实现验证与 QA 闭环', status: 'completed' },
    ];

    render(
      <ProjectProgressPanel
        tasks={tasks}
        directorTasks={tasks}
        pmRunning={false}
        pmState={{
          completed_task_count: 3,
          last_director_status: 'success',
        }}
        controlPlaneProjection={passedLedgerProjection}
      />,
    );

    const pm = screen.getByTestId('project-chain-role-pm');
    const chiefEngineer = screen.getByTestId('project-chain-role-chief-engineer');
    const director = screen.getByTestId('project-chain-role-director');

    expect(pm).toHaveTextContent('success');
    expect(pm).toHaveTextContent('任务合同已完成：3/3');
    expect(pm).not.toHaveTextContent('等待任务合同');
    expect(chiefEngineer).toHaveTextContent('success');
    expect(chiefEngineer).toHaveTextContent('蓝图已交接：3/3 Director queue 已完成');
    expect(chiefEngineer).not.toHaveTextContent('等待 PM 合同');
    expect(director).toHaveTextContent('success');
    expect(director).toHaveTextContent('实现验证与 QA 闭环');
    expect(director).not.toHaveTextContent('等待 CE 交接');
  });

  it('does not show completed chain success before Run Ledger projection loads', () => {
    const tasks = [
      { id: '1', subject: '实现 CLI 科学计算器核心模块', status: 'completed' },
      { id: '2', subject: '编写 README', status: 'completed' },
      { id: '3', subject: '实现验证与 QA 闭环', status: 'completed' },
    ];

    render(
      <ProjectProgressPanel
        tasks={tasks}
        directorTasks={tasks}
        pmRunning={false}
        pmState={{
          completed_task_count: 3,
          last_director_status: 'success',
        }}
      />,
    );

    expect(screen.getByText('等待 Run Ledger 证据 · 2/3')).toBeInTheDocument();
    expect(screen.getByText('67%')).toBeInTheDocument();
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
    expect(screen.getByTestId('qa-evidence-grade')).toHaveTextContent('run_ledger_required');
    expect(screen.getByTestId('project-chain-role-pm')).not.toHaveTextContent('success');
    expect(screen.getByTestId('project-chain-role-chief-engineer')).toHaveTextContent('blocked');
    expect(screen.getByTestId('project-chain-role-director')).toHaveTextContent('blocked');
  });
});
