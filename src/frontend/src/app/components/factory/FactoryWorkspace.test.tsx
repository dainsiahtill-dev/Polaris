import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { PmTask } from '@/types/task';
import { FactoryWorkspace } from './FactoryWorkspace';

vi.mock('@/app/components/pm', () => ({
  PMWorkspace: () => <div data-testid="pm-workspace-mock">PM</div>,
}));

vi.mock('@/app/components/director', () => ({
  DirectorWorkspace: () => <div data-testid="director-workspace-mock">Director</div>,
}));

vi.mock('@/app/components/common/RealtimeActivityPanel', () => ({
  RealtimeActivityPanel: ({
    executionLogs = [],
    llmStreamEvents = [],
    processStreamEvents = [],
    role,
  }: {
    executionLogs?: unknown[];
    llmStreamEvents?: unknown[];
    processStreamEvents?: unknown[];
    role?: string;
  }) => (
    <div
      data-testid="realtime-activity-mock"
      data-role={role}
      data-execution-count={executionLogs.length}
      data-llm-count={llmStreamEvents.length}
      data-process-count={processStreamEvents.length}
    >
      Activity
    </div>
  ),
}));

vi.mock('@/app/components/factory/BenchStatusStrip', () => ({
  BenchStatusStrip: () => <div data-testid="bench-status-strip">Factory Bench probe</div>,
}));

const baseProps = {
  workspace: 'X:/workspace',
  onBackToMain: vi.fn(),
  tasks: [],
  onStart: vi.fn(),
  onCancel: vi.fn(),
  onPause: vi.fn(),
  onResume: vi.fn(),
  onRetryCheckpoint: vi.fn(),
};

describe('FactoryWorkspace', () => {
  it('shows start button for idle state', () => {
    render(<FactoryWorkspace {...baseProps} currentRun={null} events={[]} />);

    expect(screen.getByRole('button', { name: '启动' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument();
    expect(screen.getByTestId('bench-status-strip')).toHaveTextContent('Factory Bench probe');
  });

  it('shows compact workspace labels while preserving the full path as evidence', () => {
    const workspace = 'C:/Users/dains/Documents/GitLab/fashion-gen-studio';

    render(<FactoryWorkspace {...baseProps} workspace={workspace} currentRun={null} events={[]} />);

    expect(screen.getByTestId('factory-workspace-label')).toHaveTextContent('fashion-gen-studio');
    expect(screen.getByTestId('factory-workspace-label')).toHaveAttribute('title', workspace);
    expect(screen.getByTestId('factory-pm-workspace-label')).toHaveTextContent('fashion-gen-studio');
    expect(screen.getByTestId('factory-pm-workspace-label')).not.toHaveTextContent('C:/Users/dains');
  });

  it('shows empty audit evidence states before artifacts are available', () => {
    render(<FactoryWorkspace {...baseProps} currentRun={null} events={[]} />);

    expect(screen.getByText('总监审计 / 交付证据')).toBeInTheDocument();
    expect(screen.getByText('暂无质量门结果')).toBeInTheDocument();
    expect(screen.getByText('暂无交付产物')).toBeInTheDocument();
    expect(screen.getByText('暂无交付摘要')).toBeInTheDocument();
  });

  it('renders RoleSession lineage from Factory run metadata', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
        currentRun={{
          run_id: 'factory-exported',
          phase: 'planning',
          status: 'running',
          current_stage: 'pm_planning',
          last_successful_stage: null,
          progress: 20,
          roles: {},
          gates: [],
          created_at: '2026-05-23T00:00:00Z',
          metadata: {
            export_session_id: 'sess_pm',
            export_bundle_path: '.polaris/exports/sess_pm_export.json',
            directive: 'Build the PM Director desktop handoff.',
          },
        }}
        events={[]}
      />
    );

    const evidence = screen.getByTestId('factory-source-evidence');
    expect(within(evidence).getByText('来源证据')).toBeInTheDocument();
    expect(within(evidence).getByText('sess_pm')).toBeInTheDocument();
    expect(within(evidence).getByText('.polaris/exports/sess_pm_export.json')).toBeInTheDocument();
    expect(within(evidence).getByText('Build the PM Director desktop handoff.')).toBeInTheDocument();
  });

  it('renders three role layers and opens Chief Engineer handoff evidence', () => {
    const blueprintTask = {
      id: 'task-blueprint-1',
      title: 'Prepare implementation blueprint',
      status: 'pending',
      metadata: {
        blueprint_id: 'bp-1',
        blueprint_path: 'docs/blueprints/bp-1.md',
      },
    } as PmTask;

    render(
      <FactoryWorkspace
        {...baseProps}
        tasks={[blueprintTask]}
        pmTasks={[blueprintTask]}
        directorTasks={[]}
        currentRun={null}
        events={[]}
      />
    );

    expect(screen.getByTestId('factory-role-layer-pm')).toBeInTheDocument();
    expect(screen.getByTestId('factory-role-layer-chief_engineer')).toBeInTheDocument();
    expect(screen.getByTestId('factory-role-layer-director')).toBeInTheDocument();
    expect(screen.getByTestId('factory-role-flow-rail')).toBeInTheDocument();
    expect(screen.getByTestId('factory-layered-layout')).toBeInTheDocument();
    expect(screen.getByTestId('factory-role-layer-pm')).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByTestId('factory-role-layer-chief_engineer'));

    expect(screen.getByTestId('factory-role-layer-chief_engineer')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('factory-chief-layer')).toBeInTheDocument();
    expect(screen.getByText('Prepare implementation blueprint')).toBeInTheDocument();
    expect(screen.queryByText('docs/blueprints/bp-1.md')).not.toBeInTheDocument();
  });

  it('uses a compact PM contract layer inside Factory instead of embedding the full PM console', () => {
    const task = {
      id: 'task-contract-1',
      title: 'Define checkout contract',
      goal: 'Clarify checkout implementation scope',
      status: 'pending',
      scope_paths: ['src/checkout'],
      steps: ['Create API route'],
      acceptance_criteria: ['Checkout route is tested'],
    } as PmTask;

    render(<FactoryWorkspace {...baseProps} tasks={[task]} pmTasks={[task]} currentRun={null} events={[]} />);

    expect(screen.getByTestId('factory-pm-layer')).toBeInTheDocument();
    expect(screen.getByTestId('factory-pm-task-item')).toHaveTextContent('Define checkout contract');
    expect(screen.getByText('合同字段覆盖')).toBeInTheDocument();
    expect(screen.queryByTestId('pm-workspace-mock')).not.toBeInTheDocument();
  });

  it('uses a compact Director delivery layer with Factory-owned execution controls', () => {
    const task = {
      id: 'director-task-1',
      title: 'Implement checkout route',
      goal: 'Deliver the route from the approved blueprint',
      status: 'pending',
      steps: ['Edit route handler'],
      acceptance_criteria: ['Route test passes'],
      target_files: ['src/checkout/route.ts'],
    } as PmTask;

    render(<FactoryWorkspace {...baseProps} tasks={[task]} directorTasks={[task]} currentRun={null} events={[]} />);

    fireEvent.click(screen.getByTestId('factory-role-layer-director'));

    expect(screen.getByTestId('director-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('director-execution-guard')).toHaveTextContent('Factory 编排 Director');
    expect(screen.getByTestId('director-workspace-bulk-execute')).toBeDisabled();
    expect(screen.getByTestId('director-task-detail')).toHaveTextContent('Implement checkout route');
    expect(screen.queryByTestId('director-workspace-mock')).not.toBeInTheDocument();
  });

  it('renders Chief Engineer runtime blueprint artifacts as handoff evidence', () => {
    const task = {
      id: 'TASK-1',
      subject: 'Implement checkout workflow',
      goal: 'Deliver checkout workflow',
      status: 'pending',
    } as PmTask;

    render(
      <FactoryWorkspace
        {...baseProps}
        tasks={[task]}
        pmTasks={[task]}
        directorTasks={[]}
        artifacts={[
          {
            name: 'ce_TASK-1.json',
            path: 'runtime/blueprints/ce_TASK-1.json',
            size: 128,
          },
        ]}
        currentRun={{
          run_id: 'run-ce-artifact',
          phase: 'planning',
          status: 'running',
          current_stage: 'chief_engineer_review',
          last_successful_stage: 'pm_planning',
          progress: 50,
          roles: {
            chief_engineer: {
              role: 'chief_engineer',
              status: 'completed',
              current_task: 'chief_engineer_review',
              progress: 100,
            },
          },
          gates: [],
          created_at: '2026-05-23T00:00:00Z',
        }}
        events={[]}
      />
    );

    const chiefLayer = screen.getByTestId('factory-chief-layer');
    expect(chiefLayer).toBeInTheDocument();
    expect(within(chiefLayer).getByText('Implement checkout workflow')).toBeInTheDocument();
    expect(within(chiefLayer).queryByText('ce_TASK-1.json')).not.toBeInTheDocument();
    expect(within(chiefLayer).queryByText('runtime/blueprints/ce_TASK-1.json')).not.toBeInTheDocument();
    expect(within(chiefLayer).queryByText('Chief Engineer runtime blueprint artifact')).not.toBeInTheDocument();
    expect(screen.getByText('1 条蓝图')).toBeInTheDocument();
  });

  it('separates Factory Chief Engineer review artifacts from blueprint handoff evidence', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
        tasks={[]}
        pmTasks={[]}
        directorTasks={[]}
        artifacts={[
          { name: 'ce_TASK-1.json', path: 'runtime/blueprints/ce_TASK-1.json', size: 128 },
          { name: 'ce_TASK-2.json', path: 'runtime/blueprints/ce_TASK-2.json', size: 128 },
          { name: 'ce_TASK-3.json', path: 'runtime/blueprints/ce_TASK-3.json', size: 128 },
          {
            name: 'factory_fc1625758450.review.json',
            path: 'runtime/state/blueprints/factory_fc1625758450.review.json',
            size: 128,
          },
          {
            name: 'review.json',
            path: 'workspace/roles/chief_engineer/factory_fc1625758450/review.json',
            size: 128,
          },
          {
            name: 'factory_fc1625758450.review.json',
            path: 'workspace/blueprints/factory_fc1625758450.review.json',
            size: 128,
          },
          {
            name: 'latest.review.json',
            path: 'workspace/blueprints/latest.review.json',
            size: 128,
          },
        ]}
        currentRun={{
          run_id: 'run-ce-review-artifact',
          phase: 'planning',
          status: 'running',
          current_stage: 'chief_engineer_review',
          last_successful_stage: 'pm_planning',
          progress: 50,
          roles: {
            chief_engineer: {
              role: 'chief_engineer',
              status: 'completed',
              current_task: 'chief_engineer_review',
              progress: 100,
            },
          },
          gates: [],
          created_at: '2026-05-23T00:00:00Z',
        }}
        events={[]}
      />
    );

    const chiefLayer = screen.getByTestId('factory-chief-layer');
    expect(within(chiefLayer).getByText('3 条蓝图')).toBeInTheDocument();
    expect(within(chiefLayer).queryByText('4 条证据')).not.toBeInTheDocument();
    expect(within(chiefLayer).getAllByText('审查回执').length).toBeGreaterThan(0);
    expect(within(chiefLayer).getByText('factory_fc1625758450.review.json')).toBeInTheDocument();
    expect(within(chiefLayer).getByText('runtime/state/blueprints/factory_fc1625758450.review.json')).toBeInTheDocument();
    expect(within(chiefLayer).queryByText('workspace/roles/chief_engineer/factory_fc1625758450/review.json')).not.toBeInTheDocument();
    expect(within(chiefLayer).queryByText('workspace/blueprints/factory_fc1625758450.review.json')).not.toBeInTheDocument();
    expect(within(chiefLayer).queryByText('workspace/blueprints/latest.review.json')).not.toBeInTheDocument();
  });

  it('does not list a PM task as pending when a runtime blueprint artifact matches task_id', () => {
    const pmTask = {
      id: 'TASK-1',
      title: 'Implement checkout workflow',
      goal: 'Deliver checkout workflow',
      status: 'pending',
    } as PmTask;

    render(
      <FactoryWorkspace
        {...baseProps}
        tasks={[pmTask]}
        pmTasks={[pmTask]}
        directorTasks={[]}
        artifacts={[
          {
            name: 'ce_TASK-1.json',
            path: 'runtime/blueprints/ce_TASK-1.json',
            size: 128,
            task_id: 'TASK-1',
          },
        ]}
        currentRun={{
          run_id: 'run-ce-task-match',
          phase: 'planning',
          status: 'running',
          current_stage: 'chief_engineer_review',
          last_successful_stage: 'pm_planning',
          progress: 50,
          roles: {
            chief_engineer: {
              role: 'chief_engineer',
              status: 'completed',
              current_task: 'chief_engineer_review',
              progress: 100,
            },
          },
          gates: [],
          created_at: '2026-05-23T00:00:00Z',
        }}
        events={[]}
      />
    );

    expect(screen.getByTestId('factory-chief-layer')).toBeInTheDocument();
    expect(screen.getByText('Implement checkout workflow')).toBeInTheDocument();
    expect(screen.getByText('当前任务均已具备蓝图字段或暂无 PM 任务。')).toBeInTheDocument();
  });

  it('keeps Factory Chief Engineer handoff blocked when only part of the PM task pool has blueprint coverage', () => {
    const coveredTask = {
      id: 'TASK-covered',
      title: 'Covered implementation blueprint',
      goal: 'This task already has a runtime blueprint',
      status: 'pending',
    } as PmTask;
    const missingTask = {
      id: 'TASK-missing',
      title: 'Missing implementation blueprint',
      goal: 'This task still needs Chief Engineer evidence',
      status: 'pending',
    } as PmTask;

    render(
      <FactoryWorkspace
        {...baseProps}
        tasks={[coveredTask, missingTask]}
        pmTasks={[coveredTask, missingTask]}
        directorTasks={[]}
        artifacts={[
          {
            name: 'ce_TASK-covered.json',
            path: 'runtime/blueprints/ce_TASK-covered.json',
            size: 128,
            task_id: 'TASK-covered',
          },
        ]}
        currentRun={{
          run_id: 'run-ce-partial',
          phase: 'planning',
          status: 'running',
          current_stage: 'chief_engineer_review',
          last_successful_stage: 'pm_planning',
          progress: 50,
          roles: {},
          gates: [],
          created_at: '2026-05-23T00:00:00Z',
        }}
        events={[]}
      />
    );

    const chiefLayer = screen.getByTestId('factory-chief-layer');
    expect(within(chiefLayer).getByText('1/2')).toBeInTheDocument();
    expect(within(chiefLayer).getByText('缺证据')).toBeInTheDocument();
    expect(within(chiefLayer).getByText('Missing implementation blueprint')).toBeInTheDocument();
    expect(within(chiefLayer).getByText('Covered implementation blueprint')).toBeInTheDocument();
  });

  it('focuses Chief Engineer when the factory stage is chief_engineer_review', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
        currentRun={{
          run_id: 'run-ce',
          phase: 'planning',
          status: 'running',
          current_stage: 'chief_engineer_review',
          last_successful_stage: 'pm_planning',
          progress: 45,
          roles: {
            chief_engineer: {
              role: 'chief_engineer',
              status: 'running',
              current_task: 'chief_engineer_review',
              progress: 50,
            },
          },
          gates: [],
          created_at: '2026-05-23T00:00:00Z',
        }}
        events={[]}
      />
    );

    expect(screen.getByTestId('factory-chief-layer')).toBeInTheDocument();
    expect(screen.getAllByText('chief_engineer_review').length).toBeGreaterThan(0);
    expect(screen.getByTestId('realtime-activity-mock')).toHaveAttribute('data-role', 'chief_engineer');
  });

  it('passes the active role layer into the operations activity monitor', () => {
    render(<FactoryWorkspace {...baseProps} currentRun={null} events={[]} />);

    expect(screen.getByTestId('realtime-activity-mock')).toHaveAttribute('data-role', 'pm');

    fireEvent.click(screen.getByTestId('factory-role-layer-director'));

    expect(screen.getByTestId('realtime-activity-mock')).toHaveAttribute('data-role', 'director');
  });

  it('passes audit, LLM and process streams into the operations activity monitor', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
        currentRun={null}
        events={[
          {
            type: 'stage_started',
            stage: 'pm_planning',
            message: 'PM planning started',
            timestamp: '2026-05-23T00:00:00Z',
          },
        ]}
        llmStreamEvents={[
          {
            id: 'llm-1',
            timestamp: '2026-05-23T00:00:01Z',
            level: 'thinking',
            message: 'thinking',
          },
        ]}
        executionLogs={[
          {
            id: 'exec-1',
            timestamp: '2026-05-23T00:00:01Z',
            level: 'exec',
            source: 'EXEC',
            message: 'runtime execution event',
          },
        ]}
        processStreamEvents={[
          {
            id: 'proc-1',
            timestamp: '2026-05-23T00:00:02Z',
            level: 'info',
            message: 'process event',
          },
        ]}
      />
    );

    const activity = screen.getByTestId('realtime-activity-mock');
    expect(activity).toHaveAttribute('data-execution-count', '2');
    expect(activity).toHaveAttribute('data-llm-count', '1');
    expect(activity).toHaveAttribute('data-process-count', '1');
  });

  it('passes file edit events as tool activity into the operations monitor', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
        currentRun={null}
        events={[]}
        fileEditEvents={[
          {
            id: 'file-1',
            timestamp: '2026-05-23T00:00:03Z',
            filePath: 'calculator.py',
            operation: 'create',
            contentSize: 1200,
            taskId: 'TASK-1',
            addedLines: 42,
          },
        ]}
      />
    );

    expect(screen.getByTestId('realtime-activity-mock')).toHaveAttribute('data-execution-count', '1');
  });

  it('shows pause and cancel buttons for a running run', () => {
    const onPause = vi.fn();
    render(
      <FactoryWorkspace
        {...baseProps}
        onPause={onPause}
        currentRun={{
          run_id: 'run-1',
          phase: 'implementation',
          status: 'running',
          current_stage: 'director_dispatch',
          last_successful_stage: 'pm_planning',
          progress: 60,
          roles: {},
          gates: [],
          created_at: '2026-03-07T00:00:00Z',
        }}
        events={[]}
      />
    );

    fireEvent.click(screen.getByTestId('factory-run-pause'));
    expect(onPause).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '暂停' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启动' })).not.toBeInTheDocument();
    expect(screen.getByText('implementation')).toBeInTheDocument();
    expect(screen.getAllByText('running').length).toBeGreaterThan(0);
    expect(screen.getByText('director_dispatch')).toBeInTheDocument();
  });

  it('shows resume control for a paused run', () => {
    const onResume = vi.fn();
    render(
      <FactoryWorkspace
        {...baseProps}
        onResume={onResume}
        currentRun={{
          run_id: 'run-paused',
          phase: 'implementation',
          status: 'paused',
          current_stage: 'director_dispatch',
          last_successful_stage: 'pm_planning',
          progress: 60,
          roles: {},
          gates: [],
          created_at: '2026-03-07T00:00:00Z',
        }}
        events={[]}
      />
    );

    fireEvent.click(screen.getByTestId('factory-run-resume'));
    expect(onResume).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '恢复' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '暂停' })).not.toBeInTheDocument();
  });

  it('shows checkpoint retry control for a failed run', () => {
    const onRetryCheckpoint = vi.fn();
    render(
      <FactoryWorkspace
        {...baseProps}
        onRetryCheckpoint={onRetryCheckpoint}
        currentRun={{
          run_id: 'run-failed',
          phase: 'failed',
          status: 'failed',
          current_stage: 'director_dispatch',
          last_successful_stage: 'pm_planning',
          progress: 60,
          roles: {},
          gates: [],
          failure: {
            failure_type: 'deterministic',
            code: 'director_failed',
            detail: 'Director failed',
            phase: 'implementation',
            recoverable: true,
          },
          created_at: '2026-03-07T00:00:00Z',
        }}
        events={[]}
      />
    );

    fireEvent.click(screen.getByTestId('factory-run-retry-checkpoint'));
    expect(onRetryCheckpoint).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '启动' })).toBeInTheDocument();
  });

  it('renders gates, artifacts and summary in the audit panel', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
        currentRun={{
          run_id: 'run-3',
          phase: 'completed',
          status: 'completed',
          current_stage: 'handover',
          last_successful_stage: 'handover',
          progress: 100,
          roles: {},
          gates: [
            {
              gate_name: 'director_tool_audit',
              status: 'passed',
              score: 92,
              passed: true,
              message: 'No unauthorized tool calls',
            },
          ],
          artifacts: [
            {
              name: 'director-audit.json',
              path: '.polaris/runs/run-3/artifacts/director-audit.json',
              size: 1024,
            },
          ],
          summary_md: 'Director handoff ready.',
          created_at: '2026-03-07T00:00:00Z',
        }}
        events={[]}
      />
    );

    expect(screen.getByText('director_tool_audit')).toBeInTheDocument();
    expect(screen.getByText('No unauthorized tool calls')).toBeInTheDocument();
    expect(screen.getByText('director-audit.json')).toBeInTheDocument();
    expect(screen.getByText('.polaris/runs/run-3/artifacts/director-audit.json')).toBeInTheDocument();
    expect(screen.getByText('1.0 KB')).toBeInTheDocument();
    expect(screen.getByText('Director handoff ready.')).toBeInTheDocument();
  });

  it('renders artifact fetch errors as an alert', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
        currentRun={{
          run_id: 'run-4',
          phase: 'completed',
          status: 'completed',
          current_stage: 'handover',
          last_successful_stage: 'handover',
          progress: 100,
          roles: {},
          gates: [],
          created_at: '2026-03-07T00:00:00Z',
          artifacts_error: 'artifact endpoint unavailable',
        }}
        events={[]}
      />
    );

    expect(screen.getByRole('alert')).toHaveTextContent('artifact endpoint unavailable');
  });

  it('renders failure details from the current run', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
        currentRun={{
          run_id: 'run-2',
          phase: 'failed',
          status: 'failed',
          current_stage: 'quality_gate',
          last_successful_stage: 'director_dispatch',
          progress: 90,
          roles: {},
          gates: [],
          created_at: '2026-03-07T00:00:00Z',
          failure: {
            failure_type: 'transient',
            code: 'FACTORY_STAGE_FAILED',
            detail: 'quality gate failed',
            phase: 'failed',
            recoverable: true,
            suggested_action: 'Inspect the QA report',
          },
        }}
        events={[]}
      />
    );

    expect(screen.getByText('失败信息')).toBeInTheDocument();
    expect(screen.getAllByText('quality gate failed').length).toBeGreaterThan(0);
    expect(screen.getByText(/Inspect the QA report/)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('quality gate failed');
    expect(screen.getByRole('button', { name: '启动' })).toBeInTheDocument();
  });

  it('separates PM root cause from Director and QA cascade blockers', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
        currentRun={{
          run_id: 'run-pm-failed',
          phase: 'failed',
          status: 'failed',
          current_stage: 'pm_planning',
          last_successful_stage: null,
          progress: 20,
          roles: {
            pm: {
              role: 'pm',
              status: 'failed',
              detail: 'PM iteration failed: task contract validation failed',
              current_task: 'pm_planning',
              progress: 20,
            },
            director: {
              role: 'director',
              status: 'blocked',
              detail: 'Director dispatch skipped because PM iteration failed',
              current_task: '',
              progress: 0,
            },
            qa: {
              role: 'qa',
              status: 'blocked',
              detail: 'QA blocked because PM iteration failed',
              current_task: '',
              progress: 0,
            },
          },
          gates: [],
          created_at: '2026-05-25T00:00:00Z',
          failure: {
            failure_type: 'deterministic',
            code: 'PM_ITERATION_FAILED',
            detail: 'Director: Director dispatch skipped because PM iteration failed QA: QA blocked because PM iteration failed',
            phase: 'failed',
            recoverable: true,
          },
        }}
        events={[]}
      />
    );

    const brief = screen.getByTestId('factory-failure-brief');
    expect(brief).toHaveTextContent('PM 阶段失败');
    expect(brief).toHaveTextContent('根因 PM');
    expect(brief).toHaveTextContent('PM_ITERATION_FAILED');
    expect(brief).toHaveTextContent('2 个级联阻塞');
    expect(brief).toHaveTextContent('Director dispatch skipped because PM iteration failed');
    expect(brief).toHaveTextContent('QA blocked because PM iteration failed');
  });

  it('treats canceled, blocked and timeout run states as terminal restartable states', () => {
    const terminalRuns = [
      { run_id: 'run-canceled', status: 'canceled', phase: 'canceled', expectedLabel: '已取消' },
      { run_id: 'run-blocked', status: 'blocked', phase: 'blocked', expectedLabel: '失败' },
      { run_id: 'run-timeout', status: 'timeout', phase: 'timeout', expectedLabel: '失败' },
    ];

    for (const run of terminalRuns) {
      const { unmount } = render(
        <FactoryWorkspace
          {...baseProps}
          currentRun={{
            run_id: run.run_id,
            phase: run.phase,
            status: run.status,
            current_stage: run.status,
            last_successful_stage: 'director_dispatch',
            progress: 100,
            roles: {},
            gates: [],
            created_at: '2026-05-23T00:00:00Z',
          }}
          events={[]}
        />
      );

      expect(screen.getByRole('button', { name: '启动' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument();
      expect(screen.getAllByText(run.expectedLabel).length).toBeGreaterThan(0);
      unmount();
    }
  });
});
