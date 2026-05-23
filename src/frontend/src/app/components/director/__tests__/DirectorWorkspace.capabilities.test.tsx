import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PmTask } from '@/types/task';
import {
  DirectorWorkspace,
  mergeDirectorWorkers,
  normalizeDirectorCapabilityHosts,
  normalizeDirectorWorkerRows,
} from '../DirectorWorkspace';

const serviceMocks = vi.hoisted(() => ({
  cancelDirectorRun: vi.fn(),
  cancelDirectorTask: vi.fn(),
  createDirectorTask: vi.fn(),
  getDirectorCapabilities: vi.fn(),
  getDirectorRun: vi.fn(),
  getDirectorStatus: vi.fn(),
  getDirectorTask: vi.fn(),
  getDirectorWorker: vi.fn(),
  getRoleKernelCacheStats: vi.fn(),
  getRoleKernelLLMEvents: vi.fn(),
  getRoleKernelTokenBudgetStats: vi.fn(),
  clearRoleKernelCache: vi.fn(),
  getDirectorTaskKernelLLMEvents: vi.fn(),
  listDirectorWorkers: vi.fn(),
  listDirectorTaskFallbackRows: vi.fn(),
  runDirector: vi.fn(),
}));
const apiMocks = vi.hoisted(() => ({
  openPath: vi.fn(),
}));

vi.mock('@/services', () => serviceMocks);
vi.mock('@/api', () => apiMocks);

vi.mock('@/app/components/ai-dialogue', () => ({
  AIDialoguePanel: () => <div data-testid="director-ai-dialogue" />,
}));

vi.mock('../RealTimeFileDiff', () => ({
  RealTimeFileDiff: () => <div data-testid="real-time-file-diff" />,
}));

vi.mock('react-resizable-panels', () => ({
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PanelGroup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PanelResizeHandle: () => <div data-testid="resize-handle" />,
}));

describe.sequential('Director capability desktop integration', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    apiMocks.openPath.mockResolvedValue({ ok: true });
    serviceMocks.listDirectorTaskFallbackRows.mockResolvedValue({
      ok: true,
      data: [],
    });
    serviceMocks.listDirectorWorkers.mockResolvedValue({
      ok: true,
      data: [],
    });
    serviceMocks.getDirectorCapabilities.mockResolvedValue({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });
    serviceMocks.getRoleKernelCacheStats.mockResolvedValue({
      ok: true,
      data: { hits: 4, misses: 1, size: 2, max_size: 100, hit_rate: 80, enabled: true },
    });
    serviceMocks.getRoleKernelTokenBudgetStats.mockResolvedValue({
      ok: true,
      data: { total: 11500, available_conversation: 4000, safety_margin: 500 },
    });
    serviceMocks.getRoleKernelLLMEvents.mockResolvedValue({
      ok: true,
      data: {
        events: [
          {
            event_type: 'llm_call_start',
            model: 'gpt-test',
            status: 'started',
            timestamp: '2026-05-23T00:00:00Z',
          },
        ],
        count: 1,
        stats: { call_error: 0, call_retry: 0 },
      },
    });
    serviceMocks.clearRoleKernelCache.mockResolvedValue({
      ok: true,
      data: { ok: true, message: 'Cache cleared' },
    });
    serviceMocks.getDirectorTaskKernelLLMEvents.mockResolvedValue({
      ok: true,
      data: { task_id: 'task-default', events: [], stats: { total: 0 } },
    });
    serviceMocks.cancelDirectorTask.mockResolvedValue({
      ok: true,
      data: { ok: true, task_id: 'task-default' },
    });
    serviceMocks.createDirectorTask.mockResolvedValue({
      ok: true,
      data: { id: 'director-created-default', subject: 'Default created task', status: 'PENDING' },
    });
    serviceMocks.getDirectorTask.mockResolvedValue({
      ok: true,
      data: { id: 'task-default', subject: 'Default task detail', status: 'PENDING', priority: 'MEDIUM' },
    });
    serviceMocks.getDirectorWorker.mockResolvedValue({
      ok: true,
      data: {
        id: 'worker-default',
        name: 'Default worker',
        status: 'idle',
        healthy: true,
        tasks_completed: 0,
        tasks_failed: 0,
      },
    });
    serviceMocks.runDirector.mockResolvedValue({
      ok: true,
      data: { run_id: 'director-run-1', status: 'queued', workspace: 'C:/Temp/Product', tasks_queued: 0, message: 'queued' },
    });
    serviceMocks.getDirectorRun.mockResolvedValue({
      ok: true,
      data: { run_id: 'director-run-1', status: 'queued', workspace: 'C:/Temp/Product', tasks_queued: 1, message: 'Status: queued' },
    });
    serviceMocks.cancelDirectorRun.mockResolvedValue({
      ok: true,
      data: { run_id: 'director-run-1', status: 'CANCELLED', workspace: 'C:/Temp/Product', tasks_queued: 1, message: 'Status: CANCELLED' },
    });
    serviceMocks.getDirectorStatus.mockResolvedValue({
      ok: true,
      data: { running: false, pid: null, started_at: null, mode: 'desktop_service', source: 'status_file' },
    });
  });

  it('normalizes backend capability maps into sorted host rows', () => {
    const hosts = normalizeDirectorCapabilityHosts({
      ok: true,
      role: 'director',
      capabilities: {
        workflow: ['execute_tests', 'read_files'],
        electron_workbench: ['write_files', 'read_files'],
      },
    });

    expect(hosts).toEqual([
      { hostKind: 'electron_workbench', capabilities: ['read_files', 'write_files'] },
      { hostKind: 'workflow', capabilities: ['execute_tests', 'read_files'] },
    ]);
  });

  it('normalizes and merges backend worker rows with realtime precedence', () => {
    const backendRows = normalizeDirectorWorkerRows([
      {
        id: 'worker-a',
        name: 'Backend worker A',
        status: 'idle',
        current_task_id: 'task-1',
        tasks_completed: 2,
        tasks_failed: 1,
        healthy: true,
      },
    ]);

    expect(backendRows).toEqual([
      {
        id: 'worker-a',
        name: 'Backend worker A',
        status: 'idle',
        currentTaskId: 'task-1',
        healthy: true,
        tasksCompleted: 2,
        tasksFailed: 1,
      },
    ]);

    expect(mergeDirectorWorkers([{ id: 'worker-a', name: 'Realtime worker A', status: 'busy' }], backendRows)).toEqual([
      {
        id: 'worker-a',
        name: 'Realtime worker A',
        status: 'busy',
        currentTaskId: 'task-1',
        healthy: true,
        tasksCompleted: 2,
        tasksFailed: 1,
      },
    ]);
  });

  it('renders Director capabilities from the backend capability route', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        role: 'director',
        capabilities: {
          electron_workbench: ['read_files', 'write_files', 'execute_commands', 'view_metrics'],
          workflow: ['read_files', 'write_files', 'execute_tests', 'apply_patches'],
        },
      },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(serviceMocks.getDirectorCapabilities).toHaveBeenCalledTimes(1));

    const strip = await screen.findByTestId('director-capability-strip');
    expect(strip).toHaveTextContent('/v2/director/capabilities');
    expect(strip).toHaveTextContent('electron_workbench');
    expect(strip).toHaveTextContent('workflow');
    expect(strip).toHaveTextContent('execute commands');
    expect(strip).toHaveTextContent('apply patches');
    expect(screen.getByTestId('director-delete-capability')).toHaveTextContent('delete_files blocked');
  });

  it('opens the shared settings surface from the Director header control', () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });
    const onOpenSettings = vi.fn();

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
        onOpenSettings={onOpenSettings}
      />,
    );

    fireEvent.click(screen.getByTestId('director-workspace-open-settings'));

    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it('opens the latest Director file edit through the Electron open path bridge', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
        fileEditEvents={[
          {
            id: 'edit-1',
            filePath: 'src/feature.ts',
            operation: 'modify',
            contentSize: 24,
            timestamp: '2026-05-23T00:00:00Z',
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByTitle('代码'));
    fireEvent.click(screen.getByTestId('director-code-open-file'));

    await waitFor(() => expect(apiMocks.openPath).toHaveBeenCalledWith('C:/Temp/Product\\src\\feature.ts'));
    expect(await screen.findByTestId('director-code-open-file-evidence')).toHaveTextContent('已请求打开 src/feature.ts');
  });

  it('clears terminal output from the Director terminal panel', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning
        currentTaskTitle="Run target"
        currentTaskStatus="running"
        onToggleDirector={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTitle('终端'));
    expect(await screen.findByText(/Director 运行中: Run target/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('director-terminal-clear'));

    expect(screen.getByText('等待执行...')).toBeInTheDocument();
  });

  it('wires Director debug panel actions to task inspection and cancellation', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });
    const failedTask = {
      id: 'task-failed',
      title: 'Fix failing contract',
      status: 'failed',
      done: false,
      error: 'pytest failed',
    } as unknown as PmTask;

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[failedTask]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTitle('调试'));
    fireEvent.click(screen.getByTestId('director-debug-cancel-task-failed'));
    await waitFor(() => expect(serviceMocks.cancelDirectorTask).toHaveBeenCalledWith('task-failed'));

    fireEvent.click(screen.getByTestId('director-debug-inspect-task-failed'));
    expect(screen.getByTestId('director-task-detail')).toHaveTextContent('Fix failing contract');
  });

  it('disables the Director settings control when no host callback is available', () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    expect(screen.getByTestId('director-workspace-open-settings')).toBeDisabled();
  });

  it('renders Director Kernel cache, token, and LLM event diagnostics from backend routes', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(serviceMocks.getRoleKernelCacheStats).toHaveBeenCalledWith('director'));
    expect(serviceMocks.getRoleKernelTokenBudgetStats).toHaveBeenCalledWith('director');
    expect(serviceMocks.getRoleKernelLLMEvents).toHaveBeenCalledWith('director', { role: 'director', limit: 5 });

    const strip = await screen.findByTestId('director-kernel-diagnostics-strip');
    expect(strip).toHaveTextContent('/v2/director/cache-stats');
    expect(strip).toHaveTextContent('hit 80.00%');
    expect(strip).toHaveTextContent('/v2/director/token-budget-stats');
    expect(strip).toHaveTextContent('total 11,500');
    expect(strip).toHaveTextContent('/v2/director/llm-events?role=director&limit=5');
    expect(strip).toHaveTextContent('events 1');
    expect(strip).toHaveTextContent('last llm call start');
    expect(strip).toHaveTextContent('model gpt-test');

    fireEvent.click(screen.getByTestId('director-kernel-cache-clear'));
    await waitFor(() => expect(serviceMocks.clearRoleKernelCache).toHaveBeenCalledWith('director'));
  });

  it('renders Director workers from the backend worker route when realtime rows are absent', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });
    serviceMocks.listDirectorWorkers.mockResolvedValueOnce({
      ok: true,
      data: [
        {
          id: 'worker-backend-1',
          name: 'Backend Worker 1',
          status: 'busy',
          current_task_id: 'director-task-1',
          tasks_completed: 3,
          tasks_failed: 0,
          healthy: true,
        },
      ],
    });
    serviceMocks.getDirectorWorker.mockResolvedValueOnce({
      ok: true,
      data: {
        id: 'worker-backend-1',
        name: 'Backend Worker 1',
        status: 'busy',
        current_task_id: 'director-task-1',
        healthy: true,
        tasks_completed: 3,
        tasks_failed: 0,
      },
    });
    serviceMocks.listDirectorTaskFallbackRows.mockResolvedValueOnce({
      ok: true,
      data: [
        {
          id: 'director-task-1',
          subject: 'Backend worker task',
          title: 'Backend worker task',
          status: 'RUNNING',
          metadata: { director_task_source: 'workflow' },
        },
      ],
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        workers={[]}
        directorRunning
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(serviceMocks.listDirectorWorkers).toHaveBeenCalledTimes(1));

    const strip = await screen.findByTestId('director-worker-strip');
    expect(strip).toHaveTextContent('Backend Worker 1');
    expect(strip).toHaveTextContent('busy');
    expect(strip).toHaveTextContent('Backend worker task');

    fireEvent.click(await screen.findByTestId('director-worker-item'));
    await waitFor(() => expect(serviceMocks.getDirectorWorker).toHaveBeenCalledWith('worker-backend-1'));
    const workerDetail = await screen.findByTestId('director-worker-backend-detail');
    expect(workerDetail).toHaveTextContent('/v2/director/workers/worker-backend-1');
    expect(workerDetail).toHaveTextContent('Backend Worker 1');
    expect(workerDetail).toHaveTextContent('director-task-1');
    expect(workerDetail).toHaveTextContent('Done');
    expect(workerDetail).toHaveTextContent('3');

    fireEvent.click(await screen.findByTestId('director-task-item'));
    expect(screen.getByTestId('director-task-detail')).toHaveTextContent('Worker: Backend Worker 1');
    await waitFor(() => expect(serviceMocks.getDirectorTaskKernelLLMEvents).toHaveBeenCalledWith(
      'director-task-1',
      { limit: 25 },
    ));
    const workerTaskLLMPanel = await screen.findByTestId('director-task-llm-events');
    await waitFor(() => expect(workerTaskLLMPanel).toHaveTextContent('该任务暂无后端 LLM 事件记录。'));
  });

  it('loads selected task LLM event history through the backend task endpoint', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });
    serviceMocks.listDirectorTaskFallbackRows.mockResolvedValueOnce({
      ok: true,
      data: [
        {
          id: 'director-task-llm',
          subject: 'Inspect LLM calls',
          title: 'Inspect LLM calls',
          status: 'RUNNING',
          metadata: { director_task_source: 'workflow' },
        },
      ],
    });
    serviceMocks.getDirectorTaskKernelLLMEvents.mockResolvedValueOnce({
      ok: true,
      data: {
        task_id: 'director-task-llm',
        events: [
          {
            event_type: 'llm_call_start',
            model: 'gpt-test',
            status: 'started',
            timestamp: '2026-05-23T00:00:00Z',
          },
        ],
        stats: { total: 1, call_error: 0, call_retry: 0 },
      },
    });
    serviceMocks.getDirectorTask.mockResolvedValueOnce({
      ok: true,
      data: {
        id: 'director-task-llm',
        subject: 'Inspect LLM calls',
        status: 'RUNNING',
        priority: 'HIGH',
        worker: 'worker-detail',
        pm_task_id: 'PM-LLM',
        goal: 'Inspect backend task detail',
        acceptance: ['backend detail visible'],
      },
    });

    const view = within(render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning
        onToggleDirector={vi.fn()}
      />,
    ).container);

    await view.findByText('Inspect LLM calls');
    const taskButton = await view.findByTestId('director-task-item');
    fireEvent.click(taskButton);
    await waitFor(() => expect(view.getByTestId('director-task-detail')).toHaveTextContent('Inspect LLM calls'));

    await waitFor(() => expect(serviceMocks.getDirectorTaskKernelLLMEvents).toHaveBeenCalledWith(
      'director-task-llm',
      { limit: 25 },
    ));
    await waitFor(() => expect(serviceMocks.getDirectorTask).toHaveBeenCalledWith('director-task-llm'));
    const backendDetail = await view.findByTestId('director-task-backend-detail');
    expect(backendDetail).toHaveTextContent('/v2/director/tasks/director-task-llm');
    expect(backendDetail).toHaveTextContent('HIGH');
    expect(backendDetail).toHaveTextContent('worker-detail');
    expect(backendDetail).toHaveTextContent('验收项: 1');
    const llmPanel = await view.findByTestId('director-task-llm-events');
    expect(llmPanel).toHaveTextContent('/v2/director/tasks/director-task-llm/llm-events');
    expect(llmPanel).toHaveTextContent('llm call start');
    expect(llmPanel).toHaveTextContent('gpt-test');
  });

  it('submits selected Director task cancellation through the backend route', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });
    serviceMocks.cancelDirectorTask.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, task_id: 'director-task-cancel', status: 'CANCELLED' },
    });

    const view = within(render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[
          {
            id: 'director-task-cancel',
            title: 'Cancelable Director task',
            status: 'RUNNING',
            metadata: { director_task_source: 'workflow' },
          },
        ]}
        directorRunning
        onToggleDirector={vi.fn()}
      />,
    ).container);

    const taskButton = await view.findByTestId('director-task-item');
    expect(taskButton).toHaveTextContent('Cancelable Director task');
    fireEvent.click(within(view.getByTestId('director-task-board')).getByText('Cancelable Director task'));
    await waitFor(() => expect(view.getByTestId('director-task-detail')).toHaveTextContent('Cancelable Director task'));

    fireEvent.click(view.getByTestId('director-task-cancel-selected'));

    await waitFor(() => expect(serviceMocks.cancelDirectorTask).toHaveBeenCalledWith('director-task-cancel'));
    const cancelEvidence = view.getByTestId('director-task-cancel-evidence');
    expect(cancelEvidence).toHaveTextContent('/v2/director/tasks/director-task-cancel/cancel');
    await waitFor(() => expect(cancelEvidence).toHaveTextContent('取消请求已提交: director-task-cancel (CANCELLED)'));
  });

  it('creates a Director task from the desktop task board through the backend route', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });
    serviceMocks.createDirectorTask.mockResolvedValueOnce({
      ok: true,
      data: { id: 'director-created-1', subject: 'Create backend task', status: 'PENDING' },
    });
    serviceMocks.listDirectorTaskFallbackRows.mockResolvedValue({
      ok: true,
      data: [
        {
          id: 'director-created-1',
          subject: 'Create backend task',
          title: 'Create backend task',
          status: 'PENDING',
          metadata: { director_task_source: 'local' },
        },
      ],
    });
    serviceMocks.listDirectorTaskFallbackRows.mockResolvedValueOnce({
      ok: true,
      data: [],
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[
          {
            id: 'PM-create',
            title: 'PM source task',
            status: 'pending',
            acceptance: ['created task can be audited'],
            metadata: { director_task_source: 'workflow' },
          },
        ]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await screen.findByText('PM source task');
    fireEvent.click(screen.getByTestId('director-task-item'));
    await waitFor(() => expect(screen.getByTestId('director-task-detail')).toHaveTextContent('PM source task'));
    fireEvent.change(screen.getByTestId('director-task-create-subject'), {
      target: { value: 'Create backend task' },
    });
    fireEvent.change(screen.getByTestId('director-task-create-description'), {
      target: { value: 'Persist a backend Director task from desktop' },
    });
    fireEvent.change(screen.getByTestId('director-task-create-priority'), {
      target: { value: 'HIGH' },
    });
    fireEvent.click(screen.getByTestId('director-task-create-submit'));

    await waitFor(() => expect(serviceMocks.createDirectorTask).toHaveBeenCalledWith(expect.objectContaining({
      subject: 'Create backend task',
      description: 'Persist a backend Director task from desktop',
      priority: 'HIGH',
      timeout_seconds: 300,
      metadata: expect.objectContaining({
        pm_task_id: 'PM-create',
        pm_task_title: 'PM source task',
        acceptance: ['created task can be audited'],
        guardrails: { source: 'director_desktop_task_create' },
      }),
    })));
    expect(await screen.findByTestId('director-task-create-evidence')).toHaveTextContent('已创建 Director 任务: director-created-1');
  });

  it('loads fallback Director task rows through the shared Director fallback service', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });
    serviceMocks.listDirectorTaskFallbackRows.mockResolvedValueOnce({
      ok: true,
      data: [
        {
          id: 'director-task-1',
          subject: 'Implement runtime contract',
          title: 'Implement runtime contract',
          description: 'From Director task backend',
          status: 'PENDING',
          metadata: { director_task_source: 'auto' },
        },
      ],
    });
    serviceMocks.runDirector.mockResolvedValueOnce({
      ok: true,
      data: { run_id: 'director-run-1', status: 'queued', workspace: 'C:/Temp/Product', tasks_queued: 1, message: 'queued' },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(serviceMocks.listDirectorTaskFallbackRows).toHaveBeenCalledWith(false));
    expect(await screen.findByText('Implement runtime contract')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('director-task-item'));
    fireEvent.click(screen.getByTestId('director-workspace-execute'));

    await waitFor(() => expect(serviceMocks.runDirector).toHaveBeenCalledWith({
      workspace: 'C:/Temp/Product',
      task_id: 'director-task-1',
      task_filter: 'director-task-1',
      execution_mode: 'parallel',
    }));
    await waitFor(() => expect(serviceMocks.getDirectorRun).toHaveBeenCalledWith('director-run-1'));
    const runEvidence = await screen.findByTestId('director-run-evidence');
    expect(runEvidence).toHaveTextContent('/v2/director/runs/director-run-1');
    expect(runEvidence).toHaveTextContent('queued · queued=1');
    fireEvent.click(screen.getByTitle('终端'));
    expect(await screen.findByText(/Director run 已创建: director-run-1 queued=1/)).toBeInTheDocument();
  });

  it('runs the Director queue through orchestration when no task is selected', async () => {
    const onToggleDirector = vi.fn();
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });
    serviceMocks.runDirector.mockResolvedValueOnce({
      ok: true,
      data: { run_id: 'director-queue-run', status: 'queued', workspace: 'C:/Temp/Product', tasks_queued: 2, message: 'queued' },
    });
    serviceMocks.getDirectorRun.mockResolvedValueOnce({
      ok: true,
      data: { run_id: 'director-queue-run', status: 'queued', workspace: 'C:/Temp/Product', tasks_queued: 2, message: 'Status: queued' },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={onToggleDirector}
      />,
    );

    fireEvent.click(screen.getByTestId('director-workspace-execute'));

    await waitFor(() => expect(serviceMocks.runDirector).toHaveBeenCalledWith({
      workspace: 'C:/Temp/Product',
      execution_mode: 'parallel',
    }));
    expect(onToggleDirector).not.toHaveBeenCalled();
    await waitFor(() => expect(serviceMocks.getDirectorRun).toHaveBeenCalledWith('director-queue-run'));
    const runEvidence = await screen.findByTestId('director-run-evidence');
    expect(runEvidence).toHaveTextContent('/v2/director/runs/director-queue-run');
    expect(runEvidence).toHaveTextContent('queued · queued=2');
  });

  it('cancels the visible Director orchestration run from the evidence strip', async () => {
    serviceMocks.runDirector.mockResolvedValueOnce({
      ok: true,
      data: { run_id: 'director-queue-run', status: 'queued', workspace: 'C:/Temp/Product', tasks_queued: 2, message: 'queued' },
    });
    serviceMocks.getDirectorRun.mockResolvedValueOnce({
      ok: true,
      data: { run_id: 'director-queue-run', status: 'RUNNING', workspace: 'C:/Temp/Product', tasks_queued: 2, message: 'Status: RUNNING' },
    });
    serviceMocks.cancelDirectorRun.mockResolvedValueOnce({
      ok: true,
      data: { run_id: 'director-queue-run', status: 'CANCELLED', workspace: 'C:/Temp/Product', tasks_queued: 2, message: 'Status: CANCELLED' },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTestId('director-workspace-execute'));

    await waitFor(() => expect(serviceMocks.getDirectorRun).toHaveBeenCalledWith('director-queue-run'));
    fireEvent.click(await screen.findByTestId('director-run-cancel'));

    await waitFor(() => expect(serviceMocks.cancelDirectorRun).toHaveBeenCalledWith('director-queue-run'));
    const runEvidence = await screen.findByTestId('director-run-evidence');
    expect(runEvidence).toHaveTextContent('/v2/director/runs/director-queue-run');
    expect(runEvidence).toHaveTextContent('CANCELLED · queued=2');
    expect(screen.getByTestId('director-run-cancel-result')).toHaveTextContent(
      '/v2/director/runs/director-queue-run/cancel',
    );
    expect(screen.getByTestId('director-run-cancel-result')).toHaveTextContent('取消运行已提交: CANCELLED');
  });

  it('stops a running Director and shows backend status evidence', async () => {
    const onToggleDirector = vi.fn().mockResolvedValue(undefined);
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });
    serviceMocks.getDirectorStatus.mockResolvedValueOnce({
      ok: true,
      data: {
        running: false,
        pid: null,
        started_at: null,
        mode: 'desktop_service',
        source: 'status_file',
      },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={true}
        onToggleDirector={onToggleDirector}
        currentTaskTitle="正在执行的任务"
        currentTaskStatus="RUNNING"
      />,
    );

    fireEvent.click(screen.getByTestId('director-workspace-execute'));

    await waitFor(() => expect(onToggleDirector).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(serviceMocks.getDirectorStatus).toHaveBeenCalledTimes(1));
    expect(serviceMocks.runDirector).not.toHaveBeenCalled();
    const statusEvidence = await screen.findByTestId('director-toggle-status-evidence');
    expect(statusEvidence).toHaveTextContent('/v2/director/status?source=auto');
    expect(statusEvidence).toHaveTextContent('idle');
    expect(statusEvidence).toHaveTextContent('pid=none');
    expect(statusEvidence).toHaveTextContent('mode=desktop_service');
    expect(statusEvidence).toHaveTextContent('source=status_file');
  });

  it('hides the standalone Director header when embedded in Factory mode', async () => {
    serviceMocks.getDirectorCapabilities.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'director', capabilities: { electron_workbench: ['read_files'] } },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
        factoryMode
      />,
    );

    expect(screen.queryByTestId('director-workspace-back')).not.toBeInTheDocument();
    expect(await screen.findByTestId('director-capability-strip')).toBeInTheDocument();
  });

  it('disables Director task execution controls when embedded in Factory mode', async () => {
    const directorTask = {
      id: 'director-factory-task',
      title: 'Implement from Factory queue',
      description: 'Factory owns Director orchestration',
      status: 'pending',
    } as PmTask;

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[directorTask]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
        factoryMode
      />,
    );

    const guard = await screen.findByTestId('director-execution-guard');
    expect(guard).toHaveTextContent('工厂模式下由 Factory 编排 Director');

    const bulkExecute = screen.getByTestId('director-workspace-bulk-execute');
    expect(bulkExecute).toBeDisabled();
    fireEvent.click(bulkExecute);
    expect(serviceMocks.runDirector).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('director-task-item'));
    const selectedExecute = await screen.findByTestId('director-task-execute-selected');
    expect(selectedExecute).toBeDisabled();
    fireEvent.click(selectedExecute);
    expect(serviceMocks.runDirector).not.toHaveBeenCalled();
  });
});
