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
  getDirectorDiagnostics: vi.fn(),
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
  RealTimeFileDiff: ({ filePath, patch }: { filePath?: string; patch?: string }) => (
    <div data-testid="real-time-file-diff" data-file-path={filePath}>
      {patch}
    </div>
  ),
}));

vi.mock('../DirectorWorkbenchPanel', () => ({
  DirectorWorkbenchPanel: ({
    workspace,
    tasksCount,
    runningTasks,
  }: {
    workspace?: string;
    tasksCount?: number;
    runningTasks?: number;
  }) => (
    <div data-testid="director-workbench-panel-mock">
      workspace={workspace}; tasksCount={tasksCount}; runningTasks={runningTasks}
    </div>
  ),
}));

vi.mock('../DirectorStrategyPanel', () => ({
  DirectorStrategyPanel: ({
    workspace,
    tasksCount,
    runningTasks,
  }: {
    workspace?: string;
    tasksCount?: number;
    runningTasks?: number;
  }) => (
    <div data-testid="director-strategy-panel-mock">
      workspace={workspace}; tasksCount={tasksCount}; runningTasks={runningTasks}
    </div>
  ),
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
    serviceMocks.getDirectorDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        role: 'director',
        generated_at: '2026-05-23T00:00:00Z',
        workspace: 'C:/Temp/Product',
        status: {
          ok: true,
          state: 'IDLE',
          running: false,
          source: 'workflow',
          projection_source: 'director_merged',
        },
        tasks: {
          ok: true,
          source: 'workflow',
          total: 1,
          pending: 1,
          claimed: 0,
          running: 0,
          blocked: 0,
          failed: 0,
          completed: 0,
          cancelled: 0,
          ready_to_execute: 1,
          ready_task_ids: ['director-task-1'],
          blocked_task_ids: [],
          running_task_ids: [],
        },
        workers: {
          ok: true,
          total: 1,
          idle: 1,
          busy: 0,
          healthy: 1,
          unhealthy: 0,
          active_task_ids: [],
        },
        issues: [],
      },
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
    expect(strip).not.toHaveTextContent('/v2/director/capabilities');
    expect(screen.getByTestId('director-capability-endpoint')).toHaveAttribute('data-endpoint', '/v2/director/capabilities');
    expect(strip).toHaveTextContent('electron_workbench');
    expect(strip).toHaveTextContent('workflow');
    expect(strip).toHaveTextContent('execute commands');
    expect(strip).toHaveTextContent('apply patches');
    expect(screen.getByTestId('director-delete-capability')).toHaveTextContent('delete_files blocked');
  });

  it('keeps the task queue view when historical Director runtime events arrive while idle', async () => {
    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
        processStreamEvents={[
          {
            id: 'director-live-1',
            timestamp: '2026-05-23T00:00:00Z',
            level: 'info',
            source: 'Process',
            message: 'Director live process event',
          },
        ]}
      />,
    );

    expect(await screen.findByText('当前没有可执行任务')).toBeInTheDocument();
    expect(screen.queryByTestId('realtime-activity-panel')).not.toBeInTheDocument();
  });

  it('auto-opens realtime activity when live Director runtime events arrive while running', async () => {
    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={true}
        onToggleDirector={vi.fn()}
        processStreamEvents={[
          {
            id: 'director-live-1',
            timestamp: '2026-05-23T00:00:00Z',
            level: 'info',
            source: 'Process',
            message: 'Director live process event',
          },
        ]}
      />,
    );

    expect(await screen.findByTestId('realtime-activity-panel')).toBeInTheDocument();
    expect(screen.getByText('Director live process event')).toBeInTheDocument();
  });

  it('renders realtime file edits and diff details in the code view', async () => {
    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
        fileEditEvents={[
          {
            id: 'file-1',
            filePath: 'src/app.ts',
            operation: 'modify',
            contentSize: 128,
            taskId: 'director-task-1',
            patch: '--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new',
            timestamp: '2026-06-02T00:00:00.000Z',
          },
          {
            id: 'file-2',
            filePath: 'src/new.ts',
            operation: 'create',
            contentSize: 32,
            taskId: 'director-task-1',
            patch: 'export const value = 1;\n',
            timestamp: '2026-06-02T00:00:01.000Z',
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByTestId('director-nav-代码'));

    expect(await screen.findByText('实时代码变更')).toBeInTheDocument();
    expect(screen.getByText('2 个文件')).toBeInTheDocument();
    expect(screen.getByText('src/app.ts')).toBeInTheDocument();
    expect(screen.getByText('src/new.ts')).toBeInTheDocument();
    expect(screen.getAllByText('创建').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('修改').length).toBeGreaterThanOrEqual(1);
    const defaultDiff = await screen.findByTestId('real-time-file-diff');
    expect(defaultDiff).toHaveAttribute('data-file-path', 'src/new.ts');
    expect(defaultDiff).toHaveTextContent('export const value = 1;');

    fireEvent.click(screen.getByText('src/app.ts'));
    expect(screen.getByTestId('real-time-file-diff')).toHaveAttribute('data-file-path', 'src/app.ts');
  });

  it('renders realtime file edit statistics when backend events do not include a patch', async () => {
    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
        fileEditEvents={[
          {
            id: 'file-without-patch',
            filePath: 'src/generated.ts',
            operation: 'modify',
            contentSize: 96,
            taskId: 'director-task-2',
            timestamp: '2026-06-02T00:00:02.000Z',
            addedLines: 4,
            deletedLines: 1,
            sourceChannel: 'event.file_edit',
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByTestId('director-nav-代码'));

    expect(await screen.findByText('src/generated.ts')).toBeInTheDocument();
    expect(screen.getByText('统计')).toBeInTheDocument();
    expect(screen.getAllByText('+4').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('-1').length).toBeGreaterThanOrEqual(1);

    expect(screen.getByTestId('director-file-edit-summary')).toBeInTheDocument();
    expect(screen.getByText('未收到 diff patch，已显示文件变更统计。')).toBeInTheDocument();
    expect(screen.getByText('来源: event.file_edit')).toBeInTheDocument();
  });

  it('renders task snapshot file changes in the code view when realtime events are unavailable', async () => {
    const completedTask = {
      id: 42,
      title: 'Generate implementation files',
      status: 'completed',
      metadata: {
        pm_task_id: 'PM-1',
        adapter_result: {
          new_files: ['src/new.ts'],
          modified_files: ['src/generated.ts'],
          tools_executed: 2,
        },
        runtime_execution: {
          last_result_summary: 'changed_files=2; tools_executed=2',
        },
      },
    } as unknown as PmTask;

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[completedTask]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
        fileEditEvents={[]}
      />,
    );

    fireEvent.click(screen.getByTestId('director-nav-代码'));

    expect(await screen.findByTestId('director-code-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('director-code-empty')).not.toBeInTheDocument();
    expect(screen.getByText('2 个文件')).toBeInTheDocument();
    expect(screen.getByText('src/generated.ts')).toBeInTheDocument();
    expect(screen.getByText('src/new.ts')).toBeInTheDocument();

    fireEvent.click(screen.getByText('src/generated.ts'));

    expect(screen.getByTestId('director-file-edit-summary')).toBeInTheDocument();
    expect(screen.getByText('来源: task-runtime-snapshot')).toBeInTheDocument();
  });

  it('auto-opens the newest patch diff when realtime diff arrives after snapshot statistics', async () => {
    const completedTask = {
      id: 6,
      title: 'Implement interactive game renderer',
      status: 'completed',
      metadata: {
        pm_task_id: 'PM-AUTO-RENDERER',
        adapter_result: {
          modified_files: ['src/ai/enemy_ai.py'],
          tools_executed: 1,
        },
        runtime_execution: {
          last_result_summary: 'changed_files=1; tools_executed=1',
        },
      },
    } as unknown as PmTask;

    const baseProps = {
      workspace: 'C:/Temp/Product',
      onBackToMain: vi.fn(),
      tasks: [completedTask],
      directorRunning: false,
      onToggleDirector: vi.fn(),
    };

    const { rerender } = render(
      <DirectorWorkspace
        {...baseProps}
        fileEditEvents={[]}
      />,
    );

    fireEvent.click(screen.getByTestId('director-nav-代码'));

    expect(await screen.findByTestId('director-file-edit-summary')).toHaveTextContent('来源: task-runtime-snapshot');

    rerender(
      <DirectorWorkspace
        {...baseProps}
        fileEditEvents={[
          {
            id: 'renderer-diff',
            filePath: 'src/renderer/game-view.tsx',
            operation: 'create',
            contentSize: 693,
            taskId: '6',
            patch: '--- /dev/null\n+++ b/src/renderer/game-view.tsx\n@@ -0,0 +1 @@\n+export const GameView = () => null;',
            timestamp: '2026-06-02T21:25:49.000Z',
          },
        ]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('real-time-file-diff')).toHaveAttribute(
        'data-file-path',
        'src/renderer/game-view.tsx',
      );
    });
    expect(screen.queryByTestId('director-file-edit-summary')).not.toBeInTheDocument();
  });

  it('renders Director readiness diagnostics from the backend route', async () => {
    serviceMocks.getDirectorDiagnostics.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: false,
        can_execute: true,
        role: 'director',
        generated_at: '2026-05-23T00:00:00Z',
        workspace: 'C:/Temp/Product',
        status: {
          ok: true,
          state: 'IDLE',
          running: false,
          source: 'workflow',
          projection_source: 'director_merged',
        },
        tasks: {
          ok: false,
          source: 'workflow',
          total: 2,
          pending: 1,
          claimed: 0,
          running: 0,
          blocked: 1,
          failed: 0,
          completed: 0,
          cancelled: 0,
          ready_to_execute: 1,
          ready_task_ids: ['director-ready'],
          blocked_task_ids: ['director-blocked'],
          running_task_ids: [],
        },
        workers: {
          ok: true,
          total: 1,
          idle: 1,
          busy: 0,
          healthy: 1,
          unhealthy: 0,
          active_task_ids: [],
        },
        issues: ['director_tasks_blocked'],
        execution_blockers: [],
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

    await waitFor(() => expect(serviceMocks.getDirectorDiagnostics).toHaveBeenCalledTimes(1));

    const strip = await screen.findByTestId('director-readiness-diagnostics');
    expect(strip).not.toHaveTextContent('/v2/director/diagnostics');
    expect(screen.getByTestId('director-readiness-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/diagnostics?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(screen.getByTestId('director-readiness-state')).toHaveTextContent('ready');
    expect(strip).toHaveTextContent('ready 1/2');
    expect(strip).toHaveTextContent('idle 1/1');
    expect(strip).toHaveTextContent('tasks blocked');
  });

  it('uses Director diagnostics execution blockers to disable start controls', async () => {
    serviceMocks.getDirectorDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_execute: false,
        role: 'director',
        generated_at: '2026-05-23T00:00:00Z',
        workspace: 'C:/Temp/Product',
        status: {
          ok: true,
          state: 'IDLE',
          running: false,
          source: 'workflow',
          projection_source: 'director_merged',
        },
        tasks: {
          ok: false,
          source: 'workflow',
          total: 1,
          pending: 1,
          claimed: 0,
          running: 0,
          blocked: 0,
          failed: 0,
          completed: 0,
          cancelled: 0,
          ready_to_execute: 0,
          ready_task_ids: [],
          blocked_task_ids: [],
          running_task_ids: [],
        },
        workers: {
          ok: false,
          total: 0,
          idle: 0,
          busy: 0,
          healthy: 0,
          unhealthy: 0,
          active_task_ids: [],
        },
        issues: ['director_no_ready_tasks', 'director_no_workers'],
        execution_blockers: ['director_no_ready_tasks', 'director_no_workers'],
      },
    });
    const directorTask = {
      id: 'director-waiting-task',
      title: 'Waiting for CE handoff',
      status: 'pending',
    } as PmTask;

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[directorTask]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId('director-readiness-state')).toHaveTextContent('blocked'));
    const headerExecute = screen.getByTestId('director-workspace-execute');
    expect(headerExecute).toBeDisabled();
    expect(headerExecute).toHaveAttribute(
      'title',
      'Director 交接诊断未通过：没有 ready 任务，需先完成 PM/Chief Engineer 交接，另有 1 项阻断',
    );

    const guard = await screen.findByTestId('director-execution-guard');
    expect(guard).toHaveTextContent('Director 交接诊断未通过');
    expect(guard).toHaveTextContent('没有 ready 任务');

    fireEvent.click(headerExecute);
    expect(serviceMocks.runDirector).not.toHaveBeenCalled();
  });

  it('does not show no-ready handoff blockers after all Director tasks completed', async () => {
    serviceMocks.getDirectorDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_execute: false,
        role: 'director',
        generated_at: '2026-05-23T00:00:00Z',
        workspace: 'C:/Temp/Product',
        status: {
          ok: true,
          state: 'COMPLETED',
          running: false,
          source: 'workflow',
          projection_source: 'director_merged',
        },
        tasks: {
          ok: true,
          source: 'workflow',
          total: 3,
          pending: 0,
          claimed: 0,
          running: 0,
          blocked: 0,
          failed: 0,
          completed: 3,
          cancelled: 0,
          ready_to_execute: 0,
          ready_task_ids: [],
          blocked_task_ids: [],
          running_task_ids: [],
        },
        workers: {
          ok: false,
          total: 0,
          idle: 0,
          busy: 0,
          healthy: 0,
          unhealthy: 0,
          active_task_ids: [],
        },
        issues: ['director_no_ready_tasks', 'director_no_workers'],
        execution_blockers: ['director_no_ready_tasks', 'director_no_workers'],
      },
    });
    const completedTasks = [
      { id: '1', title: '实现 CLI 科学计算器核心模块', status: 'completed', completed: true },
      { id: '2', title: '编写 README', status: 'completed', completed: true },
      { id: '3', title: '实现验证与 QA 闭环', status: 'completed', completed: true },
    ] as PmTask[];

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={completedTasks}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId('director-readiness-state')).toHaveTextContent('completed'));
    expect(screen.getByTestId('director-readiness-diagnostics')).toHaveTextContent('completed 3/3');
    expect(screen.queryByTestId('director-execution-guard')).not.toBeInTheDocument();

    const headerExecute = screen.getByTestId('director-workspace-execute');
    expect(headerExecute).not.toHaveAttribute('title', expect.stringContaining('Director 交接诊断未通过'));
  });

  it('uses snapshot blueprint tasks when Director diagnostics lag behind the workflow queue', async () => {
    serviceMocks.getDirectorDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_execute: false,
        role: 'director',
        generated_at: '2026-05-23T00:00:00Z',
        workspace: 'C:/Temp/Product',
        status: {
          ok: true,
          state: 'IDLE',
          running: false,
          source: 'workflow',
          projection_source: 'director_merged',
        },
        tasks: {
          ok: true,
          source: 'workflow',
          total: 3,
          pending: 0,
          claimed: 0,
          running: 0,
          blocked: 0,
          failed: 0,
          completed: 0,
          cancelled: 0,
          ready_to_execute: 0,
          ready_task_ids: [],
          blueprint_ready_task_ids: [],
          missing_blueprint_task_ids: [],
          invalid_blueprint_task_ids: [],
          blocked_task_ids: [],
          running_task_ids: [],
        },
        workers: {
          ok: true,
          total: 1,
          idle: 1,
          busy: 0,
          healthy: 1,
          unhealthy: 0,
          active_task_ids: [],
        },
        issues: ['director_no_ready_tasks'],
        execution_blockers: ['director_no_ready_tasks'],
      },
    });
    const snapshotTasks = [1, 2, 3].map((index) => ({
      id: `TASK-${index}`,
      title: `Factory task ${index}`,
      status: 'pending',
      blueprint_id: `ce_TASK-${index}`,
      runtime_blueprint_path: `runtime/blueprints/ce_TASK-${index}.json`,
      acceptance: [{ description: `acceptance ${index}` }],
    })) as PmTask[];

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={snapshotTasks}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId('director-readiness-state')).toHaveTextContent('ready'));
    const strip = screen.getByTestId('director-readiness-diagnostics');
    expect(strip).toHaveTextContent('ready 3/3');
    expect(strip).not.toHaveTextContent('no ready');

    const headerExecute = screen.getByTestId('director-workspace-execute');
    expect(headerExecute).not.toBeDisabled();
    expect(headerExecute).not.toHaveAttribute('title', expect.stringContaining('没有 ready 任务'));
  });

  it('uses snapshot blueprint evidence when Director diagnostics lag behind blueprint coverage', async () => {
    serviceMocks.getDirectorDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_execute: false,
        role: 'director',
        generated_at: '2026-05-23T00:00:00Z',
        workspace: 'C:/Temp/Product',
        status: {
          ok: true,
          state: 'IDLE',
          running: false,
          source: 'workflow',
          projection_source: 'director_merged',
        },
        tasks: {
          ok: false,
          source: 'workflow',
          total: 3,
          pending: 3,
          claimed: 0,
          running: 0,
          blocked: 0,
          failed: 0,
          completed: 0,
          cancelled: 0,
          ready_to_execute: 3,
          ready_task_ids: ['TASK-1', 'TASK-2', 'TASK-3'],
          blueprint_ready_task_ids: [],
          missing_blueprint_task_ids: ['TASK-1', 'TASK-2', 'TASK-3'],
          invalid_blueprint_task_ids: [],
          blocked_task_ids: [],
          running_task_ids: [],
        },
        workers: {
          ok: true,
          total: 0,
          idle: 0,
          busy: 0,
          healthy: 0,
          unhealthy: 0,
          active_task_ids: [],
        },
        llm: {
          ok: true,
          state: 'ready',
          role: 'director',
          blocked_roles: [],
          unsupported_roles: [],
          required_ready_roles: ['director'],
          provider_id: 'qwen',
          model: 'qwen3-max',
        },
        issues: ['director_ready_tasks_missing_blueprints'],
        execution_blockers: ['director_ready_tasks_missing_blueprints'],
      },
    });
    const snapshotTasks = [1, 2, 3].map((index) => ({
      id: `TASK-${index}`,
      title: `Factory task ${index}`,
      status: 'pending',
      blueprint_id: `ce_TASK-${index}`,
      runtime_blueprint_path: `runtime/blueprints/ce_TASK-${index}.json`,
      acceptance: [{ description: `acceptance ${index}` }],
    })) as PmTask[];

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={snapshotTasks}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId('director-readiness-state')).toHaveTextContent('ready'));
    const strip = screen.getByTestId('director-readiness-diagnostics');
    expect(strip).toHaveTextContent('ready 3/3');
    expect(strip).not.toHaveTextContent('missing BP');
    expect(strip).not.toHaveTextContent('ready tasks missing blueprints');

    const headerExecute = screen.getByTestId('director-workspace-execute');
    expect(headerExecute).not.toBeDisabled();
    expect(headerExecute).not.toHaveAttribute('title', expect.stringContaining('缺少 Chief Engineer 蓝图证据'));
  });

  it('surfaces Director LLM readiness as an execution blocker', async () => {
    serviceMocks.getDirectorDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_execute: false,
        role: 'director',
        generated_at: '2026-05-23T00:00:00Z',
        workspace: 'C:/Temp/Product',
        status: {
          ok: true,
          state: 'IDLE',
          running: false,
          source: 'workflow',
          projection_source: 'director_merged',
        },
        tasks: {
          ok: true,
          source: 'workflow',
          total: 1,
          pending: 1,
          claimed: 0,
          running: 0,
          blocked: 0,
          failed: 0,
          completed: 0,
          cancelled: 0,
          ready_to_execute: 1,
          ready_task_ids: ['director-ready'],
          blocked_task_ids: [],
          running_task_ids: [],
        },
        workers: {
          ok: true,
          total: 1,
          idle: 1,
          busy: 0,
          healthy: 1,
          unhealthy: 0,
          active_task_ids: [],
        },
        llm: {
          ok: false,
          state: 'blocked',
          role: 'director',
          blocked_roles: ['director'],
          unsupported_roles: [],
          required_ready_roles: ['director'],
          provider_id: 'qwen',
          model: 'qwen3-max',
        },
        issues: ['director_llm_not_ready'],
        execution_blockers: ['director_llm_not_ready'],
      },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[{ id: 'director-ready', title: 'Ready implementation task', status: 'pending' } as PmTask]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId('director-readiness-state')).toHaveTextContent('blocked'));
    const strip = screen.getByTestId('director-readiness-diagnostics');
    expect(strip).toHaveTextContent('LLM');
    expect(strip).toHaveTextContent('blocked director');
    const headerExecute = screen.getByTestId('director-workspace-execute');
    expect(headerExecute).toBeDisabled();
    expect(headerExecute).toHaveAttribute(
      'title',
      'Director 交接诊断未通过：Director LLM 角色未通过运行前测试',
    );
    fireEvent.click(headerExecute);
    expect(serviceMocks.runDirector).not.toHaveBeenCalled();
  });

  it('blocks workflow execution when Director tasks lack Chief Engineer blueprint evidence', async () => {
    serviceMocks.getDirectorDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_execute: false,
        role: 'director',
        generated_at: '2026-05-23T00:00:00Z',
        workspace: 'C:/Temp/Product',
        status: {
          ok: true,
          state: 'IDLE',
          running: false,
          source: 'workflow',
          projection_source: 'director_merged',
        },
        tasks: {
          ok: false,
          source: 'workflow',
          total: 1,
          pending: 1,
          claimed: 0,
          running: 0,
          blocked: 0,
          failed: 0,
          completed: 0,
          cancelled: 0,
          ready_to_execute: 0,
          ready_task_ids: [],
          blueprint_ready_task_ids: [],
          missing_blueprint_task_ids: ['director-without-blueprint'],
          blocked_task_ids: [],
          running_task_ids: [],
        },
        workers: {
          ok: true,
          total: 1,
          idle: 1,
          busy: 0,
          healthy: 1,
          unhealthy: 0,
          active_task_ids: [],
        },
        issues: ['director_ready_tasks_missing_blueprints'],
        execution_blockers: ['director_ready_tasks_missing_blueprints'],
      },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[{ id: 'director-without-blueprint', title: 'Missing CE blueprint', status: 'pending' } as PmTask]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId('director-readiness-state')).toHaveTextContent('blocked'));
    const headerExecute = screen.getByTestId('director-workspace-execute');
    expect(headerExecute).toBeDisabled();
    expect(headerExecute).toHaveAttribute(
      'title',
      'Director 交接诊断未通过：workflow 任务缺少 Chief Engineer 蓝图证据',
    );
    expect(screen.getByTestId('director-readiness-diagnostics')).toHaveTextContent('missing BP 1');
    expect(await screen.findByTestId('director-execution-guard')).toHaveTextContent(
      'workflow 任务缺少 Chief Engineer 蓝图证据',
    );
  });

  it('blocks workflow execution when Director blueprint artifacts are invalid', async () => {
    serviceMocks.getDirectorDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_execute: false,
        role: 'director',
        generated_at: '2026-05-25T00:00:00Z',
        workspace: 'C:/Temp/Product',
        status: {
          ok: true,
          state: 'IDLE',
          running: false,
          source: 'workflow',
          projection_source: 'director_merged',
        },
        tasks: {
          ok: false,
          source: 'workflow',
          total: 1,
          pending: 1,
          claimed: 0,
          running: 0,
          blocked: 0,
          failed: 0,
          completed: 0,
          cancelled: 0,
          ready_to_execute: 0,
          ready_task_ids: [],
          blueprint_ready_task_ids: [],
          missing_blueprint_task_ids: [],
          invalid_blueprint_task_ids: ['director-stale-blueprint'],
          blocked_task_ids: [],
          running_task_ids: [],
        },
        workers: {
          ok: true,
          total: 1,
          idle: 1,
          busy: 0,
          healthy: 1,
          unhealthy: 0,
          active_task_ids: [],
        },
        issues: ['director_ready_tasks_invalid_blueprints'],
        execution_blockers: ['director_ready_tasks_invalid_blueprints'],
      },
    });

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[{ id: 'director-stale-blueprint', title: 'Stale CE blueprint', status: 'pending' } as PmTask]}
        directorRunning={false}
        onToggleDirector={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId('director-readiness-state')).toHaveTextContent('blocked'));
    const headerExecute = screen.getByTestId('director-workspace-execute');
    expect(headerExecute).toBeDisabled();
    expect(headerExecute).toHaveAttribute(
      'title',
      'Director 交接诊断未通过：workflow 任务引用的 Chief Engineer 蓝图不可审计',
    );
    expect(screen.getByTestId('director-readiness-diagnostics')).toHaveTextContent('invalid BP 1');
    expect(await screen.findByTestId('director-execution-guard')).toHaveTextContent(
      'workflow 任务引用的 Chief Engineer 蓝图不可审计',
    );
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
    expect(await screen.findByTestId('director-terminal-output')).toHaveTextContent(/Director 运行中: Run target/);

    fireEvent.click(screen.getByTestId('director-terminal-clear'));

    expect(screen.getByTestId('director-terminal-empty')).toHaveTextContent('等待执行...');
  });

  it('renders execution and process stream evidence in the Director terminal panel', async () => {
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
        executionLogs={[
          {
            id: 'exec-1',
            timestamp: '2000-01-01T00:00:00.000Z',
            level: 'exec',
            source: 'director',
            message: 'pytest passed',
          },
        ]}
        processStreamEvents={[
          {
            id: 'proc-1',
            timestamp: '2000-01-01T00:00:01.000Z',
            level: 'info',
            source: 'stdout',
            message: 'writing src/generated.ts',
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByTitle('终端'));
    const output = await screen.findByTestId('director-terminal-output');
    expect(output).toHaveTextContent('pytest passed');
    expect(output).toHaveTextContent('writing src/generated.ts');

    fireEvent.click(screen.getByTestId('director-terminal-clear'));

    await waitFor(() => expect(screen.getByTestId('director-terminal-empty')).toHaveTextContent('等待执行...'));
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
    await waitFor(() => expect(serviceMocks.cancelDirectorTask).toHaveBeenCalledWith('task-failed', 'C:/Temp/Product'));

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
    expect(serviceMocks.getRoleKernelLLMEvents).toHaveBeenCalledWith('director', {
      role: 'director',
      limit: 5,
      workspace: 'C:/Temp/Product',
    });

    const strip = await screen.findByTestId('director-kernel-diagnostics-strip');
    expect(strip).not.toHaveTextContent('/v2/director/cache-stats');
    expect(screen.getByTitle('/v2/director/cache-stats')).toHaveAttribute('data-endpoint', '/v2/director/cache-stats');
    expect(strip).toHaveTextContent('hit 80.00%');
    expect(screen.getByTitle('/v2/director/token-budget-stats')).toHaveAttribute('data-endpoint', '/v2/director/token-budget-stats');
    expect(strip).toHaveTextContent('total 11,500');
    expect(screen.getByTitle('/v2/director/llm-events?role=director&limit=5&workspace=C%3A%2FTemp%2FProduct')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/llm-events?role=director&limit=5&workspace=C%3A%2FTemp%2FProduct',
    );
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
    await waitFor(() => expect(serviceMocks.getDirectorWorker).toHaveBeenCalledWith('worker-backend-1', 'C:/Temp/Product'));
    const workerDetail = await screen.findByTestId('director-worker-backend-detail');
    expect(workerDetail).not.toHaveTextContent('/v2/director/workers/worker-backend-1');
    expect(screen.getByTestId('director-worker-backend-detail-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/workers/worker-backend-1',
    );
    expect(workerDetail).toHaveTextContent('Backend Worker 1');
    expect(workerDetail).toHaveTextContent('director-task-1');
    expect(workerDetail).toHaveTextContent('Done');
    expect(workerDetail).toHaveTextContent('3');

    fireEvent.click(await screen.findByTestId('director-task-item'));
    expect(screen.getByTestId('director-task-detail')).toHaveTextContent('Worker: Backend Worker 1');
    await waitFor(() => expect(serviceMocks.getDirectorTaskKernelLLMEvents).toHaveBeenCalledWith(
      'director-task-1',
      { limit: 25, workspace: 'C:/Temp/Product' },
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
      { limit: 25, workspace: 'C:/Temp/Product' },
    ));
    await waitFor(() => expect(serviceMocks.getDirectorTask).toHaveBeenCalledWith('director-task-llm', 'C:/Temp/Product'));
    const backendDetail = await view.findByTestId('director-task-backend-detail');
    expect(backendDetail).not.toHaveTextContent('/v2/director/tasks/director-task-llm');
    expect(view.getByTestId('director-task-backend-detail-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/tasks/director-task-llm?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(backendDetail).toHaveTextContent('HIGH');
    expect(backendDetail).toHaveTextContent('worker-detail');
    expect(backendDetail).toHaveTextContent('验收项: 1');
    const llmPanel = await view.findByTestId('director-task-llm-events');
    expect(llmPanel).not.toHaveTextContent('/v2/director/tasks/director-task-llm/llm-events');
    expect(view.getByTestId('director-task-llm-events-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/tasks/director-task-llm/llm-events?limit=25&workspace=C%3A%2FTemp%2FProduct',
    );
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

    await waitFor(() => expect(serviceMocks.cancelDirectorTask).toHaveBeenCalledWith('director-task-cancel', 'C:/Temp/Product'));
    const cancelEvidence = view.getByTestId('director-task-cancel-evidence');
    expect(cancelEvidence).not.toHaveTextContent('/v2/director/tasks/director-task-cancel/cancel');
    expect(view.getByTestId('director-task-cancel-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/tasks/director-task-cancel/cancel?workspace=C%3A%2FTemp%2FProduct',
    );
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
    }), 'C:/Temp/Product'));
    expect(await screen.findByTestId('director-task-create-evidence')).toHaveTextContent('已创建 Director 任务: director-created-1');
    await waitFor(() => {
      expect(within(screen.getByTestId('director-task-board')).getByText('Create backend task')).toBeInTheDocument();
    });
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

    await waitFor(() => expect(serviceMocks.listDirectorTaskFallbackRows).toHaveBeenCalledWith(false, 'C:/Temp/Product'));
    expect(await screen.findByText('Implement runtime contract')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('director-task-item'));
    fireEvent.click(screen.getByTestId('director-workspace-execute'));

    await waitFor(() => expect(serviceMocks.runDirector).toHaveBeenCalledWith({
      workspace: 'C:/Temp/Product',
      task_id: 'director-task-1',
      task_filter: 'director-task-1',
      execution_mode: 'parallel',
    }));
    await waitFor(() => expect(serviceMocks.getDirectorRun).toHaveBeenCalledWith('director-run-1', 'C:/Temp/Product'));
    const runEvidence = await screen.findByTestId('director-run-evidence');
    expect(runEvidence).not.toHaveTextContent('/v2/director/runs/director-run-1');
    expect(screen.getByTestId('director-run-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/runs/director-run-1?workspace=C%3A%2FTemp%2FProduct',
    );
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
    await waitFor(() => expect(serviceMocks.getDirectorRun).toHaveBeenCalledWith('director-queue-run', 'C:/Temp/Product'));
    const runEvidence = await screen.findByTestId('director-run-evidence');
    expect(runEvidence).not.toHaveTextContent('/v2/director/runs/director-queue-run');
    expect(screen.getByTestId('director-run-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/runs/director-queue-run?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(runEvidence).toHaveTextContent('queued · queued=2');
    expect(screen.getByTestId('director-run-evidence-auto-refresh')).toHaveTextContent('自动刷新');

    serviceMocks.getDirectorRun.mockResolvedValueOnce({
      ok: true,
      data: { run_id: 'director-queue-run', status: 'RUNNING', workspace: 'C:/Temp/Product', tasks_queued: 2, message: 'Status: RUNNING' },
    });

    fireEvent.click(screen.getByTestId('director-run-refresh'));

    await waitFor(() => expect(serviceMocks.getDirectorRun).toHaveBeenCalledTimes(2));
    expect(serviceMocks.getDirectorRun).toHaveBeenLastCalledWith('director-queue-run', 'C:/Temp/Product');
    await waitFor(() => expect(runEvidence).toHaveTextContent('RUNNING · queued=2'));
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

    await waitFor(() => expect(serviceMocks.getDirectorRun).toHaveBeenCalledWith('director-queue-run', 'C:/Temp/Product'));
    fireEvent.click(await screen.findByTestId('director-run-cancel'));

    await waitFor(() => expect(serviceMocks.cancelDirectorRun).toHaveBeenCalledWith('director-queue-run', 'C:/Temp/Product'));
    const runEvidence = await screen.findByTestId('director-run-evidence');
    expect(runEvidence).not.toHaveTextContent('/v2/director/runs/director-queue-run');
    expect(screen.getByTestId('director-run-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/runs/director-queue-run?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(runEvidence).toHaveTextContent('CANCELLED · queued=2');
    expect(screen.getByTestId('director-run-cancel-result')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/runs/director-queue-run/cancel?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(screen.getByTestId('director-run-cancel-result')).not.toHaveTextContent('/v2/director/runs/director-queue-run/cancel');
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
    await waitFor(() => expect(serviceMocks.getDirectorStatus).toHaveBeenCalledWith('C:/Temp/Product'));
    expect(serviceMocks.runDirector).not.toHaveBeenCalled();
    const statusEvidence = await screen.findByTestId('director-toggle-status-evidence');
    expect(statusEvidence).not.toHaveTextContent('/v2/director/status?source=auto');
    expect(screen.getByTestId('director-toggle-status-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/status?source=auto&workspace=C%3A%2FTemp%2FProduct',
    );
    expect(statusEvidence).toHaveTextContent('idle');
    expect(statusEvidence).toHaveTextContent('pid=none');
    expect(statusEvidence).toHaveTextContent('mode=desktop_service');
    expect(statusEvidence).toHaveTextContent('source=status_file');
  });

  it('locks Director execution controls while a stop request is pending', () => {
    const onToggleDirector = vi.fn();

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[]}
        directorRunning={true}
        isStopping={true}
        onToggleDirector={onToggleDirector}
        currentTaskTitle="正在执行的任务"
        currentTaskStatus="RUNNING"
      />,
    );

    const headerExecute = screen.getByTestId('director-workspace-execute');
    expect(headerExecute).toBeDisabled();
    expect(headerExecute).toHaveTextContent('停止中');
    expect(headerExecute).toHaveAttribute('title', 'Director 正在停止，请等待状态回传。');

    const bulkExecute = screen.getByTestId('director-workspace-bulk-execute');
    expect(bulkExecute).toBeDisabled();
    expect(bulkExecute).toHaveAttribute('title', 'Director 正在停止，请等待状态回传。');
    expect(screen.getByTestId('director-workspace-pause')).toBeDisabled();

    fireEvent.click(headerExecute);
    fireEvent.click(bulkExecute);
    expect(onToggleDirector).not.toHaveBeenCalled();
    expect(serviceMocks.runDirector).not.toHaveBeenCalled();
  });

  it('exposes the Director RoleSession workbench from the desktop navigation', async () => {
    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[
          {
            id: 'director-workbench-source',
            title: 'Workbench source task',
            status: 'running',
            done: false,
            priority: 1,
            acceptance: [],
          } as PmTask,
        ]}
        directorRunning={true}
        onToggleDirector={vi.fn()}
      />,
    );

    expect(await screen.findByTestId('director-ai-dialogue')).toBeInTheDocument();

    fireEvent.click(screen.getByTitle('工作台'));

    const workbench = await screen.findByTestId('director-workbench-panel-mock');
    expect(workbench).toHaveTextContent('workspace=C:/Temp/Product');
    expect(workbench).toHaveTextContent('tasksCount=1');
    expect(workbench).toHaveTextContent('runningTasks=1');
    expect(screen.queryByTestId('director-ai-dialogue')).not.toBeInTheDocument();
  });

  it('exposes Director execution strategy controls from the desktop navigation', async () => {
    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[
          {
            id: 'director-strategy-source',
            title: 'Strategy source task',
            status: 'running',
            done: false,
            priority: 1,
            acceptance: [],
          } as PmTask,
        ]}
        directorRunning={true}
        onToggleDirector={vi.fn()}
      />,
    );

    expect(await screen.findByTestId('director-ai-dialogue')).toBeInTheDocument();

    fireEvent.click(screen.getByTitle('策略'));

    const strategyPanel = await screen.findByTestId('director-strategy-panel-mock');
    expect(strategyPanel).toHaveTextContent('workspace=C:/Temp/Product');
    expect(strategyPanel).toHaveTextContent('tasksCount=1');
    expect(strategyPanel).toHaveTextContent('runningTasks=1');
    expect(screen.queryByTestId('director-ai-dialogue')).not.toBeInTheDocument();
  });

  it('hides the standalone Director header when embedded in Factory mode', async () => {
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
    expect(screen.queryByTestId('director-capability-strip')).not.toBeInTheDocument();
    expect(screen.queryByTestId('director-kernel-diagnostics-strip')).not.toBeInTheDocument();
    expect(await screen.findByTestId('director-readiness-diagnostics')).toBeInTheDocument();
    expect(serviceMocks.getDirectorCapabilities).not.toHaveBeenCalled();
    expect(serviceMocks.getRoleKernelCacheStats).not.toHaveBeenCalled();
  });

  it('blocks standalone Director execution when a start gate reason is present', async () => {
    const onToggleDirector = vi.fn();
    const blockedReason =
      'LLM 就绪检查未通过：Director 角色当前绑定的 provider/model 没有通过真实测试，请先在 LLM 设置中重新测试并保存。';
    const directorTask = {
      id: 'director-gated-task',
      title: 'Implement gated task',
      description: 'Should not execute before readiness gates pass',
      status: 'pending',
    } as PmTask;

    render(
      <DirectorWorkspace
        workspace="C:/Temp/Product"
        onBackToMain={vi.fn()}
        tasks={[directorTask]}
        directorRunning={false}
        startBlockedReason={blockedReason}
        onToggleDirector={onToggleDirector}
      />,
    );

    const headerExecute = screen.getByTestId('director-workspace-execute');
    expect(headerExecute).toBeDisabled();
    expect(headerExecute).toHaveAttribute('title', blockedReason);

    const guard = await screen.findByTestId('director-execution-guard');
    expect(guard).toHaveTextContent('LLM 就绪检查未通过');

    const bulkExecute = screen.getByTestId('director-workspace-bulk-execute');
    expect(bulkExecute).toBeDisabled();
    fireEvent.click(bulkExecute);
    expect(serviceMocks.runDirector).not.toHaveBeenCalled();
    expect(onToggleDirector).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('director-task-item'));
    const selectedExecute = await screen.findByTestId('director-task-execute-selected');
    expect(selectedExecute).toBeDisabled();
    expect(selectedExecute).toHaveAttribute('title', blockedReason);
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
