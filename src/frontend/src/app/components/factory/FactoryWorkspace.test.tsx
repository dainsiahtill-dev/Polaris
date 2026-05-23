import { fireEvent, render, screen } from '@testing-library/react';
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
  RealtimeActivityPanel: () => <div data-testid="realtime-activity-mock">Activity</div>,
}));

const baseProps = {
  workspace: 'X:/workspace',
  onBackToMain: vi.fn(),
  tasks: [],
  onStart: vi.fn(),
  onCancel: vi.fn(),
};

describe('FactoryWorkspace', () => {
  it('shows start button for idle state', () => {
    render(<FactoryWorkspace {...baseProps} currentRun={null} events={[]} />);

    expect(screen.getByRole('button', { name: '启动' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument();
  });

  it('shows empty audit evidence states before artifacts are available', () => {
    render(<FactoryWorkspace {...baseProps} currentRun={null} events={[]} />);

    expect(screen.getByText('总监审计 / 交付证据')).toBeInTheDocument();
    expect(screen.getByText('暂无质量门结果')).toBeInTheDocument();
    expect(screen.getByText('暂无交付产物')).toBeInTheDocument();
    expect(screen.getByText('暂无交付摘要')).toBeInTheDocument();
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

    fireEvent.click(screen.getByTestId('factory-role-layer-chief_engineer'));

    expect(screen.getByTestId('factory-chief-layer')).toBeInTheDocument();
    expect(screen.getByText('docs/blueprints/bp-1.md')).toBeInTheDocument();
  });

  it('renders Chief Engineer runtime blueprint artifacts as handoff evidence', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
        tasks={[]}
        pmTasks={[]}
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

    expect(screen.getByTestId('factory-chief-layer')).toBeInTheDocument();
    expect(screen.getAllByText('ce_TASK-1.json').length).toBeGreaterThan(0);
    expect(screen.getAllByText('runtime/blueprints/ce_TASK-1.json').length).toBeGreaterThan(0);
    expect(screen.getByText('1 ready')).toBeInTheDocument();
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
  });

  it('shows cancel button for a running run', () => {
    render(
      <FactoryWorkspace
        {...baseProps}
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

    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启动' })).not.toBeInTheDocument();
    expect(screen.getByText('implementation')).toBeInTheDocument();
    expect(screen.getAllByText('running').length).toBeGreaterThan(0);
    expect(screen.getByText('director_dispatch')).toBeInTheDocument();
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
    expect(screen.getByText('quality gate failed')).toBeInTheDocument();
    expect(screen.getByText(/Inspect the QA report/)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('quality gate failed');
    expect(screen.getByRole('button', { name: '启动' })).toBeInTheDocument();
  });
});
