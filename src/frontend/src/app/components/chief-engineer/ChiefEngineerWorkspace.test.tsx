import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChiefEngineerWorkspace } from './ChiefEngineerWorkspace';
import { TaskStatus, type PmTask } from '@/types/task';

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('@/api', () => ({
  apiFetch: apiFetchMock,
}));

const baseProps = {
  workspace: 'C:/Temp/Product',
  tasks: [] as PmTask[],
  workers: [],
  pmState: null,
  engineStatus: null,
  directorRunning: false,
  onBackToMain: vi.fn(),
  onEnterDirectorWorkspace: vi.fn(),
  onToggleDirector: vi.fn(),
};

describe('ChiefEngineerWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ blueprints: [], total: 0 }),
    });
  });

  it('does not invent blueprint content when no evidence exists', async () => {
    render(<ChiefEngineerWorkspace {...baseProps} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/blueprints'));
    expect(screen.getByTestId('chief-engineer-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-blueprint-empty')).toHaveTextContent('未发现已落盘的 Chief Engineer 蓝图证据');
    expect(screen.getByTestId('chief-engineer-director-empty')).toHaveTextContent('暂无 Director worker 心跳');
  });

  it('does not treat task summary as blueprint evidence', async () => {
    const tasks: PmTask[] = [
      {
        id: 'PM-summary-only',
        title: '只有摘要的任务',
        summary: '这里不是 Chief Engineer 蓝图',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [],
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={tasks} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/blueprints'));
    expect(screen.getByTestId('chief-engineer-blueprint-empty')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-start-director')).toBeDisabled();
  });

  it('renders real blueprint evidence and director task lifecycle counts', async () => {
    const tasks: PmTask[] = [
      {
        id: 'PM-1',
        title: '实现任务看板',
        subject: 'Director TaskBoard',
        goal: '显示领取状态',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [],
        metadata: {
          blueprint_id: 'bp-001',
          runtime_blueprint_path: 'runtime/contracts/chief_engineer.blueprint.json',
          target_files: ['src/app.tsx'],
        },
      },
      {
        id: 'PM-2',
        title: '执行实现',
        status: TaskStatus.IN_PROGRESS,
        done: false,
        priority: 1,
        acceptance: [],
      },
    ];

    render(
      <ChiefEngineerWorkspace
        {...baseProps}
        tasks={tasks}
        workers={[{ id: 'director-1', status: 'busy', currentTaskId: 'PM-2', tasksCompleted: 1, tasksFailed: 0 }]}
      />,
    );

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/blueprints'));
    expect(screen.queryByTestId('chief-engineer-blueprint-empty')).not.toBeInTheDocument();
    expect(screen.getByText('实现任务看板')).toBeInTheDocument();
    expect(screen.getByText('bp-001')).toBeInTheDocument();
    expect(screen.getByText('runtime/contracts/chief_engineer.blueprint.json')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-blueprint-provenance')).toHaveTextContent('source · runtime_blueprint_path');
    expect(screen.getByText('src/app.tsx')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-director-list')).toHaveTextContent('director-1');
    expect(screen.getByText('未领取')).toBeInTheDocument();
    expect(screen.getByText('执行中')).toBeInTheDocument();
  });
});
