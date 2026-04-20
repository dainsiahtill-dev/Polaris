import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

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
    expect(screen.getByText('running')).toBeInTheDocument();
    expect(screen.getByText('director_dispatch')).toBeInTheDocument();
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
    expect(screen.getByRole('button', { name: '启动' })).toBeInTheDocument();
  });
});
