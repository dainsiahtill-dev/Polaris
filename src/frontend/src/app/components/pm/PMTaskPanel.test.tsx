import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PMTaskPanel } from './PMTaskPanel';
import { TaskStatus, type PmTask } from '@/types/task';

const searchPmTasksMock = vi.hoisted(() => vi.fn());
const getPmTaskMock = vi.hoisted(() => vi.fn());
const listPmTaskAssignmentsMock = vi.hoisted(() => vi.fn());
const createPmTaskMock = vi.hoisted(() => vi.fn());

vi.mock('@/services/pmService', () => ({
  getPmTask: getPmTaskMock,
  listPmTaskAssignments: listPmTaskAssignmentsMock,
  searchPmTasks: searchPmTasksMock,
}));

vi.mock('@/services/api', () => ({
  pmTaskService: {
    create: createPmTaskMock,
  },
}));

function makeTask(overrides: Partial<PmTask> = {}): PmTask {
  return {
    id: 'PM-1',
    title: '落地 PM 合同详情',
    goal: '任务详情必须可审计',
    summary: '补齐执行步骤和验收标准',
    status: TaskStatus.PENDING,
    done: false,
    priority: 1,
    acceptance: [{ description: '展示验收标准' }],
    execution_checklist: ['读取 PM 合同', '同步 Director 队列'],
    target_files: ['src/frontend/src/app/components/pm/PMTaskPanel.tsx'],
    dependencies: ['PM-0'],
    qa_contract: { acceptance_criteria: ['QA 能看到合同字段'] },
    metadata: {
      blueprint_id: 'BP-PM-1',
      runtime_blueprint_path: 'runtime/contracts/bp-pm-1.json',
      source: 'runtime_projection',
    },
    ...overrides,
  };
}

function PMTaskPanelHarness({
  tasks = [],
  onTaskCreated,
  workspace = 'C:/Temp/Product',
}: {
  tasks?: PmTask[];
  onTaskCreated?: (task: PmTask) => void;
  workspace?: string;
}) {
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  return (
    <PMTaskPanel
      tasks={tasks}
      selectedTaskId={selectedTaskId}
      onTaskSelect={setSelectedTaskId}
      onTaskCreated={onTaskCreated}
      pmRunning={false}
      workspace={workspace}
    />
  );
}

describe('PMTaskPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPmTaskMock.mockResolvedValue({
      ok: false,
      error: 'not found',
    });
    listPmTaskAssignmentsMock.mockResolvedValue({
      ok: true,
      data: {
        task_id: 'PM-1',
        assignments: [],
        count: 0,
      },
    });
    createPmTaskMock.mockResolvedValue({
      ok: false,
      error: 'not created',
    });
  });

  it('renders PM task contract details without relying on raw JSON only', () => {
    const task = makeTask();
    render(
      <PMTaskPanel
        tasks={[task]}
        selectedTaskId={task.id}
        onTaskSelect={() => undefined}
        pmRunning={false}
      />,
    );

    expect(screen.queryByText('新建')).not.toBeInTheDocument();
    const provenance = screen.getByTestId('pm-task-detail-provenance');
    expect(within(provenance).getByText('BP-PM-1')).toBeInTheDocument();
    expect(screen.getByText('读取 PM 合同')).toBeInTheDocument();
    expect(screen.getByText('展示验收标准')).toBeInTheDocument();
    expect(screen.getByText('QA 能看到合同字段')).toBeInTheDocument();
    expect(screen.getByText('src/frontend/src/app/components/pm/PMTaskPanel.tsx')).toBeInTheDocument();
    expect(screen.getByText('PM-0')).toBeInTheDocument();
  });

  it('hydrates selected PM task details from the backend detail route', async () => {
    getPmTaskMock.mockResolvedValueOnce({
      ok: true,
      data: {
        id: 'PM-1',
        title: '后端完整任务详情',
        goal: '使用 PM registry 详情作为审计来源',
        description: 'Backend PM task detail payload',
        status: 'completed',
        priority: 'critical',
        acceptance: ['后端详情验收标准'],
        execution_checklist: ['后端详情执行步骤'],
        target_files: ['runtime/contracts/pm-detail.json'],
        metadata: {
          blueprint_id: 'BP-BACKEND-DETAIL',
        },
      },
    });

    render(
      <PMTaskPanel
        tasks={[makeTask({ summary: 'runtime summary only', acceptance: [] })]}
        selectedTaskId="PM-1"
        onTaskSelect={() => undefined}
        pmRunning={false}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(getPmTaskMock).toHaveBeenCalledWith('PM-1', 'C:/Temp/Product'));
    const backendDetail = await screen.findByTestId('pm-task-backend-detail');
    expect(backendDetail).not.toHaveTextContent('/v2/pm/tasks/PM-1');
    expect(screen.getByTestId('pm-task-backend-detail-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/tasks/PM-1');
    expect(backendDetail).toHaveTextContent('Hydrated');
    expect(backendDetail).toHaveTextContent('pm_task_detail');
    expect(screen.getByText('后端完整任务详情')).toBeInTheDocument();
    expect(screen.getByText('后端详情执行步骤')).toBeInTheDocument();
    expect(screen.getByText('后端详情验收标准')).toBeInTheDocument();
    expect(screen.getByText('runtime/contracts/pm-detail.json')).toBeInTheDocument();
    expect(screen.getByTestId('pm-task-detail-provenance')).toHaveTextContent('BP-BACKEND-DETAIL');
  });

  it('renders PM task assignment history from the backend assignment route', async () => {
    listPmTaskAssignmentsMock.mockResolvedValueOnce({
      ok: true,
      data: {
        task_id: 'PM-1',
        assignments: [
          {
            id: 'assign-1',
            assignee: 'director-alpha',
            status: 'assigned',
            assigned_at: '2026-05-23T10:00:00Z',
          },
        ],
        count: 1,
      },
    });

    render(
      <PMTaskPanel
        tasks={[makeTask()]}
        selectedTaskId="PM-1"
        onTaskSelect={() => undefined}
        pmRunning={false}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(listPmTaskAssignmentsMock).toHaveBeenCalledWith('PM-1', 100, 'C:/Temp/Product'));
    const assignments = await screen.findByTestId('pm-task-assignments-panel');
    expect(assignments).not.toHaveTextContent('/v2/pm/tasks/PM-1/assignments');
    expect(screen.getByTestId('pm-task-assignments-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/tasks/PM-1/assignments');
    expect(assignments).toHaveTextContent('Assignment Evidence');
    expect(screen.getByTestId('pm-task-assignment-count')).toHaveTextContent('1 records');
    expect(screen.getByTestId('pm-task-assignment-row')).toHaveTextContent('director-alpha');
    expect(screen.getByTestId('pm-task-assignment-row')).toHaveTextContent('assigned');
    expect(screen.getByTestId('pm-task-assignment-row')).toHaveTextContent('2026-05-23T10:00:00Z');
  });

  it('uses backend task search results to open auditable task details', async () => {
    searchPmTasksMock.mockResolvedValueOnce({
      ok: true,
      data: {
        query: 'audit',
        results: [
          {
            id: 'PM-42',
            title: '审计 Director 分发结果',
            summary: 'Backend task match from PM search',
            status: 'blocked',
            priority: 2,
            acceptance: ['展示后端搜索返回的验收标准'],
            target_files: ['runtime/contracts/pm-42.json'],
            score: 0.88,
          },
        ],
        count: 1,
      },
    });

    render(<PMTaskPanelHarness />);

    fireEvent.change(screen.getByPlaceholderText('搜索任务...'), { target: { value: 'audit' } });

    await waitFor(() => expect(searchPmTasksMock).toHaveBeenCalledWith('audit', 20, 'C:/Temp/Product'));
    expect(await screen.findByTestId('pm-task-search-results')).toHaveTextContent(
      'Backend task match from PM search',
    );

    fireEvent.click(screen.getByTestId('pm-task-search-result'));

    expect(await screen.findByText('展示后端搜索返回的验收标准')).toBeInTheDocument();
    expect(screen.getByTestId('pm-task-detail-provenance')).toHaveTextContent('pm_task_search');
    expect(screen.getByText('runtime/contracts/pm-42.json')).toBeInTheDocument();
  });

  it('renders PM idle task-search projections without error banners', async () => {
    searchPmTasksMock.mockResolvedValueOnce({
      ok: true,
      data: {
        query: 'audit',
        results: [],
        count: 0,
        initialized: false,
        reason: 'PM_NOT_INITIALIZED',
      },
    });

    render(<PMTaskPanelHarness />);

    fireEvent.change(screen.getByPlaceholderText('搜索任务...'), { target: { value: 'audit' } });

    await waitFor(() => expect(searchPmTasksMock).toHaveBeenCalledWith('audit', 20, 'C:/Temp/Product'));
    expect(await screen.findByTestId('pm-task-search-empty')).toHaveTextContent('后端未返回匹配任务');
    expect(screen.queryByTestId('pm-task-search-error')).not.toBeInTheDocument();
  });

  it('creates PM tasks through the backend task create route', async () => {
    const onTaskCreated = vi.fn();
    createPmTaskMock.mockResolvedValueOnce({
      ok: true,
      data: {
        id: 'PM-created-1',
        subject: '补齐 PM 桌面任务创建',
        title: '补齐 PM 桌面任务创建',
        description: '使用 POST /v2/pm/tasks',
        status: 'pending',
        priority: 'high',
        acceptance: ['返回任务详情', '选择创建后的任务'],
        metadata: { blueprint_id: 'BP-CREATE' },
      },
    });

    render(<PMTaskPanelHarness onTaskCreated={onTaskCreated} />);

    fireEvent.click(screen.getByTestId('pm-task-create-toggle'));
    fireEvent.change(screen.getByTestId('pm-task-create-subject'), {
      target: { value: '补齐 PM 桌面任务创建' },
    });
    fireEvent.change(screen.getByTestId('pm-task-create-description'), {
      target: { value: '使用 POST /v2/pm/tasks' },
    });
    fireEvent.change(screen.getByTestId('pm-task-create-priority'), {
      target: { value: 'high' },
    });
    fireEvent.change(screen.getByTestId('pm-task-create-acceptance'), {
      target: { value: '返回任务详情\n选择创建后的任务' },
    });
    fireEvent.click(screen.getByTestId('pm-task-create-submit'));

    await waitFor(() => expect(createPmTaskMock).toHaveBeenCalledWith(
      {
        subject: '补齐 PM 桌面任务创建',
        description: '使用 POST /v2/pm/tasks',
        priority: 'high',
        status: 'pending',
        acceptance: ['返回任务详情', '选择创建后的任务'],
      },
      'C:/Temp/Product',
    ));
    const evidence = await screen.findByTestId('pm-task-create-evidence');
    expect(evidence).not.toHaveTextContent('/v2/pm/tasks');
    expect(screen.getByTestId('pm-task-create-evidence-endpoint')).toHaveTextContent('POST API');
    expect(screen.getByTestId('pm-task-create-evidence-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/tasks');
    expect(evidence).toHaveTextContent('created · PM-created-1');
    expect(screen.getByText('返回任务详情')).toBeInTheDocument();
    expect(screen.getByTestId('pm-task-detail-provenance')).toHaveTextContent('pm_task_create');
    expect(onTaskCreated).toHaveBeenCalledWith(expect.objectContaining({
      id: 'PM-created-1',
      title: '补齐 PM 桌面任务创建',
    }));
  });
});
