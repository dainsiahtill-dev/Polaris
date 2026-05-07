import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DirectorTaskPanel, buildTaskBoardGroups, type TaskBoardFilter } from '../DirectorTaskPanel';
import type { ExecutionTask } from '../hooks/useDirectorWorkspace';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntime';

function makeTask(overrides: Partial<ExecutionTask>): ExecutionTask {
  return {
    id: 'task-1',
    name: '默认任务',
    status: 'pending',
    type: 'code',
    ...overrides,
  };
}

describe('buildTaskBoardGroups', () => {
  const tasks = [
    makeTask({ id: 'pending', status: 'pending' }),
    makeTask({ id: 'running', status: 'running' }),
    makeTask({ id: 'blocked', status: 'blocked' }),
    makeTask({ id: 'failed', status: 'failed' }),
    makeTask({ id: 'done', status: 'completed' }),
  ];

  it('partitions tasks into productized Director board groups', () => {
    const groups = buildTaskBoardGroups(tasks);

    expect(groups.map((group) => group.id)).toEqual(['unclaimed', 'claimed', 'attention', 'completed']);
    expect(groups.find((group) => group.id === 'unclaimed')?.tasks.map((task) => task.id)).toEqual(['pending']);
    expect(groups.find((group) => group.id === 'claimed')?.tasks.map((task) => task.id)).toEqual(['running']);
    expect(groups.find((group) => group.id === 'attention')?.tasks.map((task) => task.id)).toEqual(['blocked', 'failed']);
    expect(groups.find((group) => group.id === 'completed')?.tasks.map((task) => task.id)).toEqual(['done']);
  });

  it('filters to a single group when requested', () => {
    const groups = buildTaskBoardGroups(tasks, 'attention' satisfies TaskBoardFilter);

    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe('attention');
    expect(groups[0].tasks.map((task) => task.status)).toEqual(['blocked', 'failed']);
  });
});

describe('DirectorTaskPanel', () => {
  const task = makeTask({
    id: 'PM-42',
    name: '实现任务合同落盘',
    goal: 'PM 目标：合同必须可审计',
    description: '把 PM 输出转为 Director 可执行任务',
    status: 'running',
    assignedWorker: 'worker-a',
    claimedBy: 'worker-a',
    pmTaskId: 'PM-42',
    blueprintId: 'BP-42',
    source: 'workflow',
    executionSteps: ['读取 PM contract', '生成 Director task', '写入 runtime projection'],
    acceptanceCriteria: ['存在 pm_task_id', '任务可追踪到验收标准'],
    targetFiles: ['src/backend/runtime/contracts/pm_tasks.contract.json'],
    dependencies: ['PM-1'],
    error: 'retryable writer timeout',
    currentFilePath: 'src/backend/runtime/tasks/director.json',
    lineStats: { added: 12, deleted: 1, modified: 2 },
    operationStats: { create: 1, modify: 2, delete: 0 },
  });
  const taskMap = new Map([[task.id, task]]);
  const workers: RuntimeWorkerState[] = [
    {
      id: 'worker-a',
      name: 'Director worker A',
      status: 'busy',
      currentTaskId: task.id,
      tasksCompleted: 3,
      tasksFailed: 1,
    },
  ];

  it('shows filtered task partitions and full clicked task details', () => {
    render(
      <DirectorTaskPanel
        tasks={[
          makeTask({ id: 'PM-1', name: '等待任务', status: 'pending' }),
          task,
          makeTask({ id: 'PM-3', name: '完成任务', status: 'completed' }),
        ]}
        workers={workers}
        taskMap={new Map([
          ['PM-1', makeTask({ id: 'PM-1', name: '等待任务', status: 'pending' })],
          [task.id, task],
          ['PM-3', makeTask({ id: 'PM-3', name: '完成任务', status: 'completed' })],
        ])}
        selectedTaskId={null}
        onTaskSelect={() => undefined}
        onExecute={() => undefined}
        isExecuting={false}
      />,
    );

    expect(screen.getByTestId('director-task-group-unclaimed')).toBeInTheDocument();
    expect(screen.getByTestId('director-task-group-claimed')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('director-task-filter-claimed'));
    expect(screen.queryByTestId('director-task-group-unclaimed')).not.toBeInTheDocument();
    expect(screen.getByText('实现任务合同落盘')).toBeInTheDocument();
  });

  it('renders selected task details from real task fields', () => {
    const onExecute = vi.fn();
    render(
      <DirectorTaskPanel
        tasks={[task]}
        workers={workers}
        taskMap={taskMap}
        selectedTaskId={task.id}
        onTaskSelect={() => undefined}
        onExecute={onExecute}
        isExecuting
      />,
    );

    const detail = screen.getByTestId('director-task-detail');
    expect(within(detail).getByText('PM 目标：合同必须可审计')).toBeInTheDocument();
    expect(within(detail).getByText('读取 PM contract')).toBeInTheDocument();
    expect(within(detail).getByText('存在 pm_task_id')).toBeInTheDocument();
    expect(within(detail).getByText('src/backend/runtime/contracts/pm_tasks.contract.json')).toBeInTheDocument();
    expect(within(detail).getByText('PM-1')).toBeInTheDocument();
    expect(within(detail).getByText(/Director worker A/)).toBeInTheDocument();
    expect(within(detail).getByText(/retryable writer timeout/)).toBeInTheDocument();
    expect(within(detail).getByText(/src\/backend\/runtime\/tasks\/director.json/)).toBeInTheDocument();
    expect(within(detail).getByText(/\+12/)).toBeInTheDocument();
    expect(within(screen.getByTestId('director-task-provenance')).getByText('PM-42')).toBeInTheDocument();
    expect(within(screen.getByTestId('director-task-provenance')).getByText('BP-42')).toBeInTheDocument();
    expect(within(screen.getByTestId('director-task-provenance')).getByText('workflow')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('director-task-execute-selected'));
    expect(onExecute).toHaveBeenCalledTimes(1);
  });
});
