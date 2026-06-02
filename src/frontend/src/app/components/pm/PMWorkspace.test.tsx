import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PMWorkspace } from './PMWorkspace';

const listPmTaskHistoryMock = vi.hoisted(() => vi.fn());
const listPmDirectorTaskHistoryMock = vi.hoisted(() => vi.fn());
const listPmTasksMock = vi.hoisted(() => vi.fn());
const listPmRequirementsMock = vi.hoisted(() => vi.fn());
const getPmRequirementMock = vi.hoisted(() => vi.fn());
const getPmStatusMock = vi.hoisted(() => vi.fn());
const getPmStartupDiagnosticsMock = vi.hoisted(() => vi.fn());
const getRoleKernelCacheStatsMock = vi.hoisted(() => vi.fn());
const getRoleKernelTokenBudgetStatsMock = vi.hoisted(() => vi.fn());
const getRoleKernelLLMEventsMock = vi.hoisted(() => vi.fn());
const clearRoleKernelCacheMock = vi.hoisted(() => vi.fn());
const getRoleCapabilitiesMock = vi.hoisted(() => vi.fn());
const resolveRoleCapabilitiesMock = vi.hoisted(() => vi.fn((payload: { capabilities?: Record<string, string[]> }, hostKind: string) => {
  const capabilities = payload.capabilities;
  if (!capabilities || typeof capabilities !== 'object') return [];
  return Array.isArray(capabilities[hostKind]) ? capabilities[hostKind] : [];
}));

vi.mock('@/services/pmService', () => ({
  getPmStatus: getPmStatusMock,
  getPmStartupDiagnostics: getPmStartupDiagnosticsMock,
  listPmTasks: listPmTasksMock,
  listPmRequirements: listPmRequirementsMock,
  getPmRequirement: getPmRequirementMock,
  listPmTaskHistory: listPmTaskHistoryMock,
  listPmDirectorTaskHistory: listPmDirectorTaskHistoryMock,
  getRoleKernelCacheStats: getRoleKernelCacheStatsMock,
  getRoleKernelTokenBudgetStats: getRoleKernelTokenBudgetStatsMock,
  getRoleKernelLLMEvents: getRoleKernelLLMEventsMock,
  clearRoleKernelCache: clearRoleKernelCacheMock,
}));

vi.mock('@/services/roleSessionService', () => ({
  getRoleCapabilities: getRoleCapabilitiesMock,
  resolveRoleCapabilities: resolveRoleCapabilitiesMock,
}));

vi.mock('./PMAIDialoguePanel', () => ({
  PMAIDialoguePanel: ({
    taskCount,
    interactionBlockedReason = '',
  }: {
    taskCount: number;
    interactionBlockedReason?: string;
  }) => (
    <div data-testid="pm-ai-dialogue-mock" data-blocked-reason={interactionBlockedReason}>
      taskCount={taskCount}
    </div>
  ),
}));

vi.mock('./PMTaskPanel', () => ({
  PMTaskPanel: ({
    tasks,
    onTaskCreated,
    createDisabledReason = '',
  }: {
    tasks: Array<{ id: string }>;
    onTaskCreated?: (task: {
      id: string;
      title: string;
      status: string;
      done: boolean;
      priority: number;
      acceptance: Array<{ description: string }>;
    }) => void;
    createDisabledReason?: string;
  }) => (
    <div data-testid="pm-task-panel-mock" data-create-disabled-reason={createDisabledReason}>
      tasks={tasks.length}; ids={tasks.map((task) => task.id).join(',')}
      {createDisabledReason ? ` createDisabled=${createDisabledReason}` : ''}
      <button
        type="button"
        data-testid="pm-task-panel-mock-create"
        disabled={Boolean(createDisabledReason)}
        onClick={() => onTaskCreated?.({
          id: 'PM-created-sync',
          title: 'Created PM task sync',
          status: 'pending',
          done: false,
          priority: 1,
          acceptance: [{ description: 'synced' }],
        })}
      >
        create
      </button>
    </div>
  ),
}));

vi.mock('./PMDocumentPanel', () => ({
  PMDocumentPanel: () => <div data-testid="pm-document-panel-mock" />,
}));

vi.mock('./PMDiagnosticsPanel', () => ({
  PMDiagnosticsPanel: () => null,
}));

vi.mock('./PMWorkbenchPanel', () => ({
  PMWorkbenchPanel: ({
    pmRunning,
    workspace,
    taskCount,
  }: {
    pmRunning?: boolean;
    workspace?: string;
    taskCount?: number;
  }) => (
    <div data-testid="pm-workbench-panel-mock">
      workspace={workspace}; taskCount={taskCount}; pmRunning={String(pmRunning)}
    </div>
  ),
}));

vi.mock('./QualityGateCard', () => ({
  QualityGateCard: () => <div data-testid="pm-quality-gate-mock" />,
}));

vi.mock('@/app/components/common/RealtimeActivityPanel', () => ({
  RealtimeActivityPanel: () => <div data-testid="pm-activity-panel-mock" />,
}));

describe('PMWorkspace history panel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listPmTaskHistoryMock.mockResolvedValue({
      ok: true,
      data: {
        history: [
          {
            id: 'hist-1',
            task_id: 'PM-1',
            action: 'created',
            updated_at: '2026-05-23T00:00:00Z',
          },
        ],
      },
    });
    listPmDirectorTaskHistoryMock.mockResolvedValue({
      ok: true,
      data: {
        iterations: [
          {
            iteration: 3,
            tasks: [{ id: 'director-task-1' }, { id: 'director-task-2' }],
            updated_at: '2026-05-23T00:01:00Z',
          },
        ],
      },
    });
    listPmTasksMock.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        tasks: [],
        pagination: { total: 0 },
      },
    });
    listPmRequirementsMock.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        requirements: [],
        items: [],
        total: 0,
      },
    });
    getPmRequirementMock.mockResolvedValue({
      ok: false,
      error: 'not found',
    });
    getPmStatusMock.mockResolvedValue({
      ok: true,
      data: {
        running: true,
        pid: 4242,
        started_at: 1779494400,
        mode: 'run_once',
        source: 'handle',
        workspace: 'C:/Temp/Product',
      },
    });
    getRoleCapabilitiesMock.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        role: 'pm',
        capabilities: {
          electron_workbench: ['chat', 'role_session', 'export_snapshot'],
        },
      },
    });
    getPmStartupDiagnosticsMock.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        can_start: true,
        generated_at: '2026-05-23T00:00:00Z',
        lancedb: { ok: true, state: 'ready' },
        llm: {
          ok: true,
          state: 'ready',
          blocked_roles: [],
          unsupported_roles: [],
          required_ready_roles: ['pm'],
        },
        workspace: {
          ok: true,
          status: 'ready',
          workspace: 'C:/Temp/Product',
          docs_present: true,
        },
        planning_input: {
          ok: true,
          status: 'ready',
          source: 'workspace_requirements',
          path: 'C:/Temp/Product/docs/product/requirements.md',
          bytes: 128,
          chars: 120,
          checked_paths: ['C:/Temp/Product/docs/product/requirements.md'],
        },
        issues: [],
        startup_blockers: [],
      },
    });
    getRoleKernelCacheStatsMock.mockResolvedValue({
      ok: true,
      data: {
        hits: 2,
        misses: 1,
        size: 3,
        max_size: 1000,
        hit_rate: 66.67,
      },
    });
    getRoleKernelTokenBudgetStatsMock.mockResolvedValue({
      ok: true,
      data: {
        total: 12000,
        available_conversation: 7300,
        safety_margin: 800,
      },
    });
    getRoleKernelLLMEventsMock.mockResolvedValue({
      ok: true,
      data: {
        count: 1,
        events: [
          {
            event_type: 'call_complete',
            model: 'Qwen3-Max',
            total_tokens: 4096,
          },
        ],
        stats: { call_error: 0, call_retry: 0 },
      },
    });
    clearRoleKernelCacheMock.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        message: 'cache cleared',
      },
    });
  });

  it('renders PM backend capability, diagnostic, cache, token, and LLM evidence in the desktop surface', async () => {
    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    const strip = await screen.findByTestId('pm-backend-evidence-strip');
    await waitFor(() => expect(getRoleCapabilitiesMock).toHaveBeenCalledWith('pm', 'electron_workbench'));
    expect(getPmStartupDiagnosticsMock).toHaveBeenCalledTimes(1);
    expect(getRoleKernelCacheStatsMock).toHaveBeenCalledWith('pm');
    expect(getRoleKernelTokenBudgetStatsMock).toHaveBeenCalledWith('pm');
    expect(getRoleKernelLLMEventsMock).toHaveBeenCalledWith('pm', {
      role: 'pm',
      limit: 5,
      workspace: 'C:/Temp/Product',
    });
    expect(strip).not.toHaveTextContent('/v2/roles/capabilities/pm?host_kind=electron_workbench');
    expect(screen.getByTestId('pm-capabilities-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/roles/capabilities/pm?host_kind=electron_workbench',
    );
    expect(screen.getByTestId('pm-diagnostics-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/pm/diagnostics?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(screen.getByTestId('pm-cache-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/cache-stats');
    expect(screen.getByTestId('pm-token-budget-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/token-budget-stats');
    expect(screen.getByTestId('pm-llm-events-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/pm/llm-events?role=pm&limit=5&workspace=C%3A%2FTemp%2FProduct',
    );
    expect(strip).toHaveTextContent('chat');
    expect(strip).toHaveTextContent('llm=ready');
    expect(strip).toHaveTextContent('input=ready');
    expect(strip).toHaveTextContent('hits=2');
    expect(strip).toHaveTextContent('total=12000');
    expect(strip).toHaveTextContent('events=1');
    expect(strip).toHaveTextContent('Qwen3-Max');
  });

  it('clears PM kernel cache from the desktop evidence strip and refreshes PM evidence', async () => {
    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(getRoleKernelCacheStatsMock).toHaveBeenCalledTimes(1));
    const clearButton = await screen.findByTestId('pm-kernel-cache-clear');
    await waitFor(() => expect(clearButton).not.toBeDisabled());

    fireEvent.click(clearButton);

    await waitFor(() => expect(clearRoleKernelCacheMock).toHaveBeenCalledWith('pm'));
    await waitFor(() => expect(getRoleKernelCacheStatsMock).toHaveBeenCalledTimes(2));
    const clearResult = await screen.findByTestId('pm-kernel-cache-clear-result');
    expect(clearResult).toHaveAttribute('data-endpoint', '/v2/pm/cache-clear');
    expect(clearResult).toHaveTextContent('cache cleared');
  });

  it('uses backend PM task list fallback when runtime tasks are absent', async () => {
    listPmTasksMock.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        tasks: [
          {
            id: 'PM-backend-1',
            title: 'Backend PM task',
            status: 'in_progress',
            priority: 'high',
            acceptance_criteria: ['backend acceptance'],
          },
          {
            id: 'PM-backend-2',
            subject: 'Completed PM task',
            status: 'completed',
            done: true,
            priority: 2,
          },
        ],
        pagination: { total: 2 },
      },
    });

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(listPmTasksMock).toHaveBeenCalledWith({
      limit: 100,
      offset: 0,
      workspace: 'C:/Temp/Product',
    }));
    const evidence = await screen.findByTestId('pm-task-backend-evidence');
    expect(evidence).not.toHaveTextContent('/v2/pm/tasks');
    expect(screen.getByTestId('pm-task-list-evidence-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/tasks');
    expect(evidence).toHaveTextContent('backend=2');
    expect(evidence).toHaveTextContent('runtime=0');
    expect(evidence).toHaveTextContent('merged=2');
    expect(screen.getByTestId('pm-task-panel-mock')).toHaveTextContent('tasks=2');
    expect(screen.getByTestId('pm-ai-dialogue-mock')).toHaveTextContent('taskCount=2');
  });

  it('keeps the PM task evidence list in sync when the task panel creates a task', async () => {
    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(screen.getByTestId('pm-task-panel-mock')).toHaveTextContent('tasks=0'));
    await waitFor(() => expect(screen.getByTestId('pm-task-panel-mock-create')).not.toBeDisabled());

    fireEvent.click(screen.getByTestId('pm-task-panel-mock-create'));

    await waitFor(() => expect(screen.getByTestId('pm-task-panel-mock')).toHaveTextContent('tasks=1'));
    expect(screen.getByTestId('pm-task-panel-mock')).toHaveTextContent('PM-created-sync');
    const evidence = screen.getByTestId('pm-task-backend-evidence');
    expect(evidence).not.toHaveTextContent('/v2/pm/tasks');
    expect(screen.getByTestId('pm-task-list-evidence-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/tasks');
    expect(evidence).toHaveTextContent('backend=1');
    expect(evidence).toHaveTextContent('merged=1');
    expect(screen.getByTestId('pm-ai-dialogue-mock')).toHaveTextContent('taskCount=1');
  });

  it('keeps PM task creation disabled when the task registry is not initialized', async () => {
    listPmTasksMock.mockResolvedValueOnce({
      ok: true,
      data: {
        initialized: false,
        reason: 'PM system not initialized',
      },
    });

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    const createButton = await screen.findByTestId('pm-task-panel-mock-create');
    await waitFor(() => expect(createButton).toBeDisabled());
    expect(screen.getByTestId('pm-task-panel-mock')).toHaveTextContent('PM 任务注册表未初始化：PM system not initialized');
    expect(screen.queryByTestId('pm-task-backend-evidence')).not.toBeInTheDocument();
  });

  it('loads backend task history and Director dispatch history when opened', async () => {
    render(
      <PMWorkspace
        tasks={[]}
        pmState={{ last_director_status: 'done' }}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    fireEvent.click(screen.getByTitle('历史'));

    await waitFor(() => expect(listPmTaskHistoryMock).toHaveBeenCalledWith({
      limit: 50,
      offset: 0,
      workspace: 'C:/Temp/Product',
    }));
    expect(listPmDirectorTaskHistoryMock).toHaveBeenCalledWith({
      limit: 25,
      offset: 0,
      workspace: 'C:/Temp/Product',
    });
    expect(screen.getByTestId('pm-history-task-list')).toHaveTextContent('PM-1');
    expect(screen.getByTestId('pm-history-task-list')).toHaveTextContent('created');
    expect(screen.getByTestId('pm-history-director-list')).toHaveTextContent('Iteration 3');
    expect(screen.getByTestId('pm-history-director-list')).toHaveTextContent('2 tasks');
  });

  it('loads PM requirement list and detail from backend when requirements view is opened', async () => {
    const requirementRow = {
      id: 'REQ-1',
      title: 'Traceable requirement',
      status: 'open',
      priority: 'high',
      source_doc: 'docs/product/requirements.md',
    };
    listPmRequirementsMock.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        requirements: [requirementRow],
        items: [requirementRow],
        total: 1,
      },
    });
    getPmRequirementMock.mockResolvedValueOnce({
      ok: true,
      data: {
        ...requirementRow,
        description: 'Requirement detail payload',
        acceptance_criteria: ['Requirement accepted'],
        related_task_ids: ['PM-1'],
      },
    });

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{ last_director_status: 'done' }}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    fireEvent.click(screen.getByTitle('需求'));

    await waitFor(() => expect(listPmRequirementsMock).toHaveBeenCalledWith({
      limit: 100,
      offset: 0,
      workspace: 'C:/Temp/Product',
    }));
    const panel = await screen.findByTestId('pm-requirements-panel');
    expect(panel).not.toHaveTextContent('/v2/pm/requirements');
    expect(screen.getByTestId('pm-requirements-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/requirements');
    expect(screen.getByTestId('pm-requirements-list-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/requirements');
    await waitFor(() => expect(screen.getByTestId('pm-requirements-count')).toHaveTextContent('1'));
    expect(screen.getByTestId('pm-requirements-list')).toHaveTextContent('Traceable requirement');
    const matrix = screen.getByTestId('pm-requirement-matrix');
    expect(matrix).toHaveTextContent('需求矩阵');
    expect(screen.getByTestId('pm-requirement-matrix-row')).toHaveAttribute('data-requirement-id', 'REQ-1');
    expect(screen.getByTestId('pm-requirement-matrix-source')).toHaveTextContent('docs/product/requirements.md');
    await waitFor(() => expect(getPmRequirementMock).toHaveBeenCalledWith('REQ-1', 'C:/Temp/Product'));
    await waitFor(() => expect(screen.getByTestId('pm-requirement-detail')).toHaveTextContent('Requirement detail payload'));
    const detail = screen.getByTestId('pm-requirement-detail');
    expect(detail).not.toHaveTextContent('/v2/pm/requirements/REQ-1');
    expect(screen.getByTestId('pm-requirement-detail-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/requirements/REQ-1');
    expect(screen.getByTestId('pm-requirement-detail-body-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/requirements/REQ-1');
    expect(detail).toHaveTextContent('Requirement accepted');
    expect(detail).toHaveTextContent('PM-1');
    await waitFor(() => expect(screen.getByTestId('pm-requirement-matrix-acceptance')).toHaveTextContent('Requirement accepted'));
    await waitFor(() => expect(screen.getByTestId('pm-requirement-matrix-related-task')).toHaveTextContent('PM-1'));
  });

  it('renders idle PM v2 empty projections without error banners', async () => {
    listPmRequirementsMock.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        requirements: [],
        items: [],
        total: 0,
        initialized: false,
        reason: 'PM_NOT_INITIALIZED',
      },
    });
    listPmTaskHistoryMock.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        history: [],
        items: [],
        total: 0,
        initialized: false,
        reason: 'PM_NOT_INITIALIZED',
      },
    });
    listPmDirectorTaskHistoryMock.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        iterations: [],
        items: [],
        total: 0,
        initialized: false,
        reason: 'PM_NOT_INITIALIZED',
      },
    });

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{ initialized: false }}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    fireEvent.click(screen.getByTitle('需求'));

    await waitFor(() => expect(listPmRequirementsMock).toHaveBeenCalledWith({
      limit: 100,
      offset: 0,
      workspace: 'C:/Temp/Product',
    }));
    expect(screen.queryByTestId('pm-requirements-error')).not.toBeInTheDocument();
    expect(screen.getByTestId('pm-requirements-list')).toHaveTextContent('暂无需求合同');
    expect(screen.getByTestId('pm-requirements-count')).toHaveTextContent('0');

    fireEvent.click(screen.getByTitle('历史'));

    await waitFor(() => expect(listPmTaskHistoryMock).toHaveBeenCalledWith({
      limit: 50,
      offset: 0,
      workspace: 'C:/Temp/Product',
    }));
    expect(listPmDirectorTaskHistoryMock).toHaveBeenCalledWith({
      limit: 25,
      offset: 0,
      workspace: 'C:/Temp/Product',
    });
    expect(screen.queryByTestId('pm-history-error')).not.toBeInTheDocument();
    expect(screen.getByTestId('pm-history-task-list')).toHaveTextContent('暂无任务历史');
    expect(screen.getByTestId('pm-history-director-list')).toHaveTextContent('暂无 Director 分发历史');
  });

  it('runs the PM single-iteration callback and shows backend status evidence', async () => {
    const onRunPmOnce = vi.fn().mockResolvedValue(true);

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={onRunPmOnce}
        workspace="C:/Temp/Product"
      />,
    );

    fireEvent.click(screen.getByTestId('pm-workspace-run-once'));

    await waitFor(() => expect(onRunPmOnce).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getPmStatusMock).toHaveBeenCalledWith('C:/Temp/Product'));
    const evidence = await screen.findByTestId('pm-run-once-status-evidence');
    expect(evidence).not.toHaveTextContent('/v2/pm/status');
    expect(screen.getByTestId('pm-run-once-status-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/status');
    expect(evidence).toHaveTextContent('running');
    expect(evidence).toHaveTextContent('pid=4242');
    expect(evidence).toHaveTextContent('mode=run_once');
    expect(evidence).toHaveTextContent('source=handle');
  });

  it('uses PM startup diagnostics blockers to disable PM start controls', async () => {
    const onRunPmOnce = vi.fn();
    const onTogglePm = vi.fn();
    getPmStartupDiagnosticsMock.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_start: false,
        generated_at: '2026-05-23T00:00:00Z',
        lancedb: { ok: false, state: 'unavailable', error: 'missing' },
        llm: {
          ok: false,
          state: 'blocked',
          blocked_roles: ['pm'],
          unsupported_roles: [],
          required_ready_roles: ['pm'],
        },
        workspace: {
          ok: false,
          status: 'missing',
          workspace: 'C:/Temp/Missing',
          docs_present: false,
        },
        planning_input: {
          ok: false,
          status: 'workspace_missing',
          checked_paths: [],
          error: 'workspace_unavailable',
        },
        issues: ['lancedb_unavailable', 'llm_not_ready', 'workspace_unavailable'],
        startup_blockers: ['lancedb_unavailable', 'llm_not_ready', 'workspace_unavailable'],
      },
    });

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={onTogglePm}
        onRunPmOnce={onRunPmOnce}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(screen.getByTestId('pm-backend-evidence-strip')).toHaveTextContent('start=blocked'));
    const runOnce = screen.getByTestId('pm-workspace-run-once');
    const toggle = screen.getByTestId('pm-workspace-toggle');
    expect(runOnce).toBeDisabled();
    expect(toggle).toBeDisabled();
    expect(runOnce).toHaveAttribute('title', 'PM 启动诊断未通过：LanceDB 不可用，另有 2 项阻断');
    expect(toggle).toHaveAttribute('title', 'PM 启动诊断未通过：LanceDB 不可用，另有 2 项阻断');
    expect(screen.getByTestId('pm-runtime-terminal-banner')).toHaveTextContent('PM 启动诊断未通过');

    fireEvent.click(runOnce);
    fireEvent.click(toggle);
    expect(onRunPmOnce).not.toHaveBeenCalled();
    expect(onTogglePm).not.toHaveBeenCalled();
  });

  it('treats can_start=true as authoritative when stale startup blockers remain in diagnostics', async () => {
    const onRunPmOnce = vi.fn();
    const onTogglePm = vi.fn();
    getPmStartupDiagnosticsMock.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        can_start: true,
        generated_at: '2026-05-23T00:00:00Z',
        lancedb: { ok: true, state: 'ready' },
        llm: {
          ok: true,
          state: 'ready',
          blocked_roles: [],
          unsupported_roles: [],
          required_ready_roles: ['pm'],
        },
        workspace: {
          ok: true,
          status: 'ready',
          workspace: 'C:/Temp/Product',
          docs_present: true,
        },
        planning_input: {
          ok: true,
          status: 'ready',
          source: 'workspace_plans_markdown',
          path: 'C:/Temp/Product/.polaris/plans/plan.md',
          bytes: 128,
          chars: 120,
          checked_paths: ['C:/Temp/Product/.polaris/plans/plan.md'],
        },
        issues: ['workspace_docs_missing'],
        startup_blockers: ['workspace_docs_missing'],
      },
    });

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={onTogglePm}
        onRunPmOnce={onRunPmOnce}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(screen.getByTestId('pm-backend-evidence-strip')).toHaveTextContent('ready'));
    expect(screen.queryByTestId('pm-runtime-terminal-banner')).not.toBeInTheDocument();
    expect(screen.getByTestId('pm-workspace-run-once')).not.toBeDisabled();
    expect(screen.getByTestId('pm-workspace-toggle')).not.toBeDisabled();
  });

  it('lets PM diagnostics clear stale external start blockers in the AI panel', async () => {
    getPmStartupDiagnosticsMock.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        can_start: true,
        generated_at: '2026-05-23T00:00:00Z',
        lancedb: { ok: true, state: 'ready' },
        llm: {
          ok: true,
          state: 'ready',
          blocked_roles: [],
          unsupported_roles: [],
          required_ready_roles: ['pm'],
        },
        workspace: {
          ok: true,
          status: 'ready',
          workspace: 'C:/Temp/Product',
          docs_present: true,
        },
        planning_input: {
          ok: true,
          status: 'ready',
          source: 'workspace_plans_markdown',
          path: 'C:/Temp/Product/.polaris/plans/plan.md',
          bytes: 128,
          chars: 120,
          checked_paths: ['C:/Temp/Product/.polaris/plans/plan.md'],
        },
        issues: [],
        startup_blockers: [],
      },
    });

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        pmStartBlockedReason="docs/ 初始化未完成"
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(screen.getByTestId('pm-backend-evidence-strip')).toHaveTextContent('ready'));
    expect(screen.getByTestId('pm-ai-dialogue-mock')).toHaveAttribute('data-blocked-reason', '');
    expect(screen.queryByText('PM 启动被阻止')).not.toBeInTheDocument();
    expect(screen.getByTestId('pm-workspace-run-once')).not.toBeDisabled();
    expect(screen.getByTestId('pm-workspace-toggle')).not.toBeDisabled();
  });

  it('refreshes PM diagnostics when the LLM runtime gate becomes ready', async () => {
    getPmStartupDiagnosticsMock
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ok: false,
          can_start: false,
          generated_at: '2026-05-23T00:00:00Z',
          lancedb: { ok: true, state: 'ready' },
          llm: {
            ok: false,
            state: 'blocked',
            blocked_roles: ['pm'],
            unsupported_roles: [],
            required_ready_roles: ['pm'],
          },
          workspace: {
            ok: true,
            status: 'ready',
            workspace: 'C:/Temp/Product',
            docs_present: true,
          },
          planning_input: {
            ok: true,
            status: 'ready',
            source: 'workspace_plans_markdown',
            path: 'C:/Temp/Product/.polaris/plans/plan.md',
            bytes: 128,
            chars: 120,
            checked_paths: ['C:/Temp/Product/.polaris/plans/plan.md'],
          },
          issues: ['llm_not_ready'],
          startup_blockers: ['llm_not_ready'],
        },
      })
      .mockResolvedValue({
        ok: true,
        data: {
          ok: true,
          can_start: true,
          generated_at: '2026-05-23T00:01:00Z',
          lancedb: { ok: true, state: 'ready' },
          llm: {
            ok: true,
            state: 'ready',
            blocked_roles: [],
            unsupported_roles: [],
            required_ready_roles: ['pm'],
          },
          workspace: {
            ok: true,
            status: 'ready',
            workspace: 'C:/Temp/Product',
            docs_present: true,
          },
          planning_input: {
            ok: true,
            status: 'ready',
            source: 'workspace_plans_markdown',
            path: 'C:/Temp/Product/.polaris/plans/plan.md',
            bytes: 128,
            chars: 120,
            checked_paths: ['C:/Temp/Product/.polaris/plans/plan.md'],
          },
          issues: [],
          startup_blockers: [],
        },
      });

    const { rerender } = render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(screen.getByTestId('pm-backend-evidence-strip')).toHaveTextContent('start=blocked'));
    expect(screen.getByTestId('pm-runtime-terminal-banner')).toHaveTextContent('PM LLM 未通过就绪检查');

    rerender(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
        llmRuntimeState={{
          state: 'READY',
          blockedRoles: [],
          requiredRoles: ['pm'],
          lastUpdated: '2026-05-23T00:01:00Z',
        }}
      />,
    );

    expect(screen.queryByTestId('pm-runtime-terminal-banner')).not.toBeInTheDocument();
    await waitFor(() => expect(getPmStartupDiagnosticsMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId('pm-backend-evidence-strip')).toHaveTextContent('ready'));
    expect(screen.queryByTestId('pm-runtime-terminal-banner')).not.toBeInTheDocument();
    expect(screen.getByTestId('pm-workspace-run-once')).not.toBeDisabled();
    expect(screen.getByTestId('pm-workspace-toggle')).not.toBeDisabled();
  });

  it('shows PM runtime root cause separately from downstream cascade blockers', () => {
    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        pmStartBlockedReason="PM manual blocker"
        runtimeIssue={{
          code: 'PM_ITERATION_FAILED',
          title: 'Polaris 引擎执行失败',
          detail: [
            '阶段: failed',
            'PM: PM iteration failed: task contract validation failed',
            'Chief Engineer: ChiefEngineer skipped because PM iteration failed',
            'Director: Director dispatch skipped because PM iteration failed',
            'QA: QA blocked because PM iteration failed',
          ].join('\n'),
        }}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    const banner = screen.getByTestId('pm-runtime-terminal-banner');
    expect(banner).toHaveTextContent('Polaris 引擎执行失败');
    expect(screen.getByTestId('pm-runtime-error-code')).toHaveTextContent('PM_ITERATION_FAILED');
    expect(screen.getByTestId('pm-runtime-root-cause')).toHaveTextContent('根因 · PM');
    expect(screen.getByTestId('pm-runtime-root-cause')).toHaveTextContent('task contract validation failed');
    const cascade = screen.getByTestId('pm-runtime-cascade');
    expect(cascade).toHaveTextContent('Chief Engineer');
    expect(cascade).toHaveTextContent('Director dispatch skipped because PM iteration failed');
    expect(cascade).toHaveTextContent('QA blocked because PM iteration failed');
    expect(screen.getByTestId('pm-runtime-start-blocker')).toHaveTextContent('当前启动门禁: PM manual blocker');
    expect(screen.getByTestId('pm-ai-dialogue-mock')).toHaveAttribute('data-blocked-reason', 'PM manual blocker');
  });

  it('does not turn a historical PM runtime failure into a current PM dialogue blocker', () => {
    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        runtimeIssue={{
          code: 'PM_ITERATION_FAILED',
          title: 'Polaris 引擎执行失败',
          detail: 'PM: previous task contract validation failed',
        }}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    expect(screen.getByTestId('pm-runtime-terminal-banner')).toHaveTextContent('上次 PM Run 失败');
    expect(screen.getByTestId('pm-ai-dialogue-mock')).toHaveAttribute('data-blocked-reason', '');
  });

  it('suppresses stale PM runtime failure banners while run_once status evidence is running', async () => {
    const onRunPmOnce = vi.fn().mockResolvedValue(true);

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        runtimeIssue={{
          code: 'PM_ITERATION_FAILED',
          title: 'Polaris 引擎执行失败',
          detail: 'PM: previous downstream workflow failed',
        }}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={onRunPmOnce}
        workspace="C:/Temp/Product"
      />,
    );

    expect(screen.getByTestId('pm-runtime-terminal-banner')).toHaveTextContent('上次 PM Run 失败');

    fireEvent.click(screen.getByTestId('pm-workspace-run-once'));

    await waitFor(() => expect(onRunPmOnce).toHaveBeenCalledTimes(1));
    const evidence = await screen.findByTestId('pm-run-once-status-evidence');
    expect(evidence).toHaveTextContent('running');
    await waitFor(() => expect(screen.queryByTestId('pm-runtime-terminal-banner')).not.toBeInTheDocument());
    expect(screen.getByTestId('pm-workspace-run-once')).toBeDisabled();
    expect(screen.getByTestId('pm-workspace-run-once')).toHaveAttribute(
      'title',
      'PM 正在运行，不能同时触发单次督办。',
    );
    expect(screen.getByTestId('pm-workspace-toggle')).toHaveAttribute(
      'title',
      'PM 后端已在运行，等待主状态同步后再操作。',
    );
  });

  it('suppresses stale PM runtime errors after the latest terminal status succeeds', () => {
    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        pmTerminalStatus={{
          status: 'success',
          terminal: true,
          ok: true,
          exit_code: 0,
          error: '',
        }}
        runtimeIssue={{
          code: 'PM_ITERATION_FAILED',
          title: 'Polaris 引擎执行失败',
          detail: 'PM: previous task contract validation failed',
        }}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    expect(screen.queryByTestId('pm-runtime-terminal-banner')).not.toBeInTheDocument();
  });

  it('uses missing docs diagnostics to disable PM workspace start controls', async () => {
    const onRunPmOnce = vi.fn();
    const onTogglePm = vi.fn();
    getPmStartupDiagnosticsMock.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_start: false,
        generated_at: '2026-05-23T00:00:00Z',
        lancedb: { ok: true, state: 'ready' },
        llm: {
          ok: true,
          state: 'ready',
          blocked_roles: [],
          unsupported_roles: [],
          required_ready_roles: ['pm'],
        },
        workspace: {
          ok: true,
          status: 'ok',
          workspace: 'C:/Temp/Product',
          docs_present: false,
        },
        planning_input: {
          ok: false,
          status: 'docs_missing',
          checked_paths: [],
          error: 'workspace_docs_missing',
        },
        issues: ['workspace_docs_missing'],
        startup_blockers: ['workspace_docs_missing'],
      },
    });

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={onTogglePm}
        onRunPmOnce={onRunPmOnce}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(screen.getByTestId('pm-backend-evidence-strip')).toHaveTextContent('start=blocked'));
    const runOnce = screen.getByTestId('pm-workspace-run-once');
    const toggle = screen.getByTestId('pm-workspace-toggle');
    expect(runOnce).toBeDisabled();
    expect(toggle).toBeDisabled();
    expect(runOnce).toHaveAttribute('title', 'PM 启动诊断未通过：docs/ 初始化未完成');
    expect(toggle).toHaveAttribute('title', 'PM 启动诊断未通过：docs/ 初始化未完成');

    fireEvent.click(runOnce);
    fireEvent.click(toggle);
    expect(onRunPmOnce).not.toHaveBeenCalled();
    expect(onTogglePm).not.toHaveBeenCalled();
  });

  it('uses missing planning-input diagnostics to disable PM workspace start controls', async () => {
    const onRunPmOnce = vi.fn();
    const onTogglePm = vi.fn();
    getPmStartupDiagnosticsMock.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_start: false,
        generated_at: '2026-05-23T00:00:00Z',
        lancedb: { ok: true, state: 'ready' },
        llm: {
          ok: true,
          state: 'ready',
          blocked_roles: [],
          unsupported_roles: [],
          required_ready_roles: ['pm'],
        },
        workspace: {
          ok: true,
          status: 'ok',
          workspace: 'C:/Temp/Product',
          docs_present: true,
        },
        planning_input: {
          ok: false,
          status: 'missing',
          checked_paths: ['C:/Temp/Product/docs/product/requirements.md'],
          error: 'planning_input_missing',
        },
        issues: ['planning_input_missing'],
        startup_blockers: ['planning_input_missing'],
      },
    });

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={onTogglePm}
        onRunPmOnce={onRunPmOnce}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(screen.getByTestId('pm-backend-evidence-strip')).toHaveTextContent('start=blocked'));
    expect(screen.getByTestId('pm-backend-evidence-strip')).toHaveTextContent('input=missing');
    const runOnce = screen.getByTestId('pm-workspace-run-once');
    const toggle = screen.getByTestId('pm-workspace-toggle');
    expect(runOnce).toBeDisabled();
    expect(toggle).toBeDisabled();
    expect(runOnce).toHaveAttribute('title', 'PM 启动诊断未通过：缺少需求/计划输入');
    expect(toggle).toHaveAttribute('title', 'PM 启动诊断未通过：缺少需求/计划输入');

    fireEvent.click(runOnce);
    fireEvent.click(toggle);
    expect(onRunPmOnce).not.toHaveBeenCalled();
    expect(onTogglePm).not.toHaveBeenCalled();
  });

  it('locks PM workspace controls while a stop request is pending', () => {
    const onRunPmOnce = vi.fn();
    const onTogglePm = vi.fn();

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={true}
        isStopping={true}
        onBackToMain={vi.fn()}
        onTogglePm={onTogglePm}
        onRunPmOnce={onRunPmOnce}
        workspace="C:/Temp/Product"
      />,
    );

    const runOnce = screen.getByTestId('pm-workspace-run-once');
    const toggle = screen.getByTestId('pm-workspace-toggle');
    expect(runOnce).toBeDisabled();
    expect(toggle).toBeDisabled();
    expect(runOnce).toHaveAttribute('title', 'PM 正在停止，请等待状态回传。');
    expect(toggle).toHaveAttribute('title', 'PM 正在停止，请等待状态回传。');
    expect(toggle).toHaveTextContent('停止中');

    fireEvent.click(runOnce);
    fireEvent.click(toggle);
    expect(onRunPmOnce).not.toHaveBeenCalled();
    expect(onTogglePm).not.toHaveBeenCalled();
  });

  it('runs the PM toggle callback and shows backend status evidence', async () => {
    const onTogglePm = vi.fn().mockResolvedValue(true);
    getPmStatusMock.mockResolvedValueOnce({
      ok: true,
      data: {
        running: true,
        pid: 5150,
        started_at: 1779494460,
        mode: 'loop',
        source: 'status_file',
      },
    });

    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={onTogglePm}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    fireEvent.click(screen.getByTestId('pm-workspace-toggle'));

    await waitFor(() => expect(onTogglePm).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getPmStatusMock).toHaveBeenCalledWith('C:/Temp/Product'));
    const evidence = await screen.findByTestId('pm-toggle-status-evidence');
    expect(evidence).not.toHaveTextContent('/v2/pm/status');
    expect(screen.getByTestId('pm-toggle-status-endpoint')).toHaveAttribute('data-endpoint', '/v2/pm/status');
    expect(evidence).toHaveTextContent('running');
    expect(evidence).toHaveTextContent('pid=5150');
    expect(evidence).toHaveTextContent('mode=loop');
    expect(evidence).toHaveTextContent('source=status_file');
  });

  it('exposes the PM RoleSession orchestration workbench from the desktop navigation', async () => {
    render(
      <PMWorkspace
        tasks={[
          {
            id: 'PM-workbench-source',
            title: 'Workbench source task',
            status: 'pending',
            done: false,
            priority: 1,
            acceptance: [],
          },
        ]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
      />,
    );

    await waitFor(() => expect(screen.getByTestId('pm-ai-dialogue-mock')).toHaveTextContent('taskCount=1'));

    fireEvent.click(screen.getByTitle('编排'));

    const workbench = await screen.findByTestId('pm-workbench-panel-mock');
    expect(workbench).toHaveTextContent('workspace=C:/Temp/Product');
    expect(workbench).toHaveTextContent('taskCount=1');
    expect(workbench).toHaveTextContent('pmRunning=false');
    expect(screen.queryByTestId('pm-ai-dialogue-mock')).not.toBeInTheDocument();
  });

  it('hides the standalone PM header when embedded in Factory mode', () => {
    render(
      <PMWorkspace
        tasks={[]}
        pmState={{}}
        pmRunning={false}
        onBackToMain={vi.fn()}
        onTogglePm={vi.fn()}
        onRunPmOnce={vi.fn()}
        workspace="C:/Temp/Product"
        factoryMode
      />,
    );

    expect(screen.queryByTestId('pm-workspace-back')).not.toBeInTheDocument();
    expect(screen.getByTestId('pm-task-panel-mock')).toBeInTheDocument();
  });
});
