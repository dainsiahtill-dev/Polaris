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
    expect(within(screen.getByTestId('director-task-board')).getByText('实现任务合同落盘')).toBeInTheDocument();
  });

  it('renders selected task details from real task fields', () => {
    const onExecute = vi.fn();
    const onTaskCancel = vi.fn();
    render(
      <DirectorTaskPanel
        tasks={[task]}
        workers={workers}
        taskMap={taskMap}
        selectedTaskId={task.id}
        onTaskSelect={() => undefined}
        onExecute={onExecute}
        onTaskCancel={onTaskCancel}
        isExecuting
        taskCancelMessage="取消请求已提交: PM-42"
        taskBackendDetail={{
          taskId: task.id,
          loading: false,
          error: null,
          data: {
            id: task.id,
            subject: task.name,
            status: 'RUNNING',
            priority: 'HIGH',
            worker: 'worker-a',
            pm_task_id: 'PM-42',
            goal: '后端权威详情',
            acceptance: ['detail loaded'],
          },
        }}
        taskLLMEvents={{
          taskId: task.id,
          loading: false,
          error: null,
          stats: { total: 1, call_error: 0, call_retry: 0 },
          events: [
            {
              event_type: 'llm_call_start',
              model: 'gpt-test',
              status: 'started',
              timestamp: '2026-05-23T00:00:00Z',
            },
          ],
        }}
        workspace="C:/Temp/Product"
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
    expect(within(screen.getByTestId('director-task-llm-events')).getByText('llm call start')).toBeInTheDocument();
    expect(within(screen.getByTestId('director-task-llm-events')).getByText('gpt-test')).toBeInTheDocument();
    expect(within(screen.getByTestId('director-task-provenance')).getByText('PM-42')).toBeInTheDocument();
    expect(within(screen.getByTestId('director-task-provenance')).getByText('BP-42')).toBeInTheDocument();
    expect(within(screen.getByTestId('director-task-provenance')).getByText('workflow')).toBeInTheDocument();
    expect(screen.getByTestId('director-task-cancel-evidence')).toHaveTextContent('/v2/director/tasks/PM-42/cancel');
    expect(screen.getByTestId('director-task-cancel-evidence')).toHaveTextContent('workspace=C%3A%2FTemp%2FProduct');
    expect(screen.getByTestId('director-task-cancel-evidence')).toHaveTextContent('取消请求已提交: PM-42');
    expect(screen.getByTestId('director-task-backend-detail')).toHaveTextContent('/v2/director/tasks/PM-42');
    expect(screen.getByTestId('director-task-backend-detail')).toHaveTextContent('workspace=C%3A%2FTemp%2FProduct');
    expect(screen.getByTestId('director-task-backend-detail')).toHaveTextContent('后端权威详情');
    expect(screen.getByTestId('director-task-backend-detail')).toHaveTextContent('验收项: 1');
    expect(screen.getByTestId('director-task-llm-events')).toHaveTextContent('/v2/director/tasks/PM-42/llm-events?limit=25');
    expect(screen.getByTestId('director-task-llm-events')).toHaveTextContent('workspace=C%3A%2FTemp%2FProduct');

    fireEvent.click(screen.getByTestId('director-task-execute-selected'));
    expect(onExecute).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('director-task-cancel-selected'));
    expect(onTaskCancel).toHaveBeenCalledWith('PM-42');
  });

  it('submits a desktop Director task creation draft through the provided handler', () => {
    const onTaskCreate = vi.fn();
    render(
      <DirectorTaskPanel
        tasks={[]}
        workers={[]}
        taskMap={new Map()}
        selectedTaskId={null}
        onTaskSelect={() => undefined}
        onExecute={() => undefined}
        onTaskCreate={onTaskCreate}
        isExecuting={false}
        taskCreateMessage="已创建 Director 任务: director-created-1"
        workspace="C:/Temp/Product"
      />,
    );

    fireEvent.change(screen.getByTestId('director-task-create-subject'), {
      target: { value: 'Create regression task' },
    });
    fireEvent.change(screen.getByTestId('director-task-create-description'), {
      target: { value: 'Run focused role desktop regression' },
    });
    fireEvent.change(screen.getByTestId('director-task-create-priority'), {
      target: { value: 'HIGH' },
    });
    fireEvent.change(screen.getByTestId('director-task-create-timeout'), {
      target: { value: '420' },
    });
    fireEvent.click(screen.getByTestId('director-task-create-submit'));

    expect(onTaskCreate).toHaveBeenCalledWith({
      subject: 'Create regression task',
      description: 'Run focused role desktop regression',
      priority: 'HIGH',
      timeoutSeconds: 420,
    });
    expect(screen.getByText('POST /v2/director/tasks?workspace=C%3A%2FTemp%2FProduct')).toBeInTheDocument();
    expect(screen.getByTestId('director-task-create-evidence')).toHaveTextContent('/v2/director/tasks?workspace=C%3A%2FTemp%2FProduct');
    expect(screen.getByTestId('director-task-create-evidence')).toHaveTextContent('已创建 Director 任务: director-created-1');
  });
});
