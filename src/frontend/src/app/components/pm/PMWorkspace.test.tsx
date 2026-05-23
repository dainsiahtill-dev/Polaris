import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PMWorkspace } from './PMWorkspace';

const listPmTaskHistoryMock = vi.hoisted(() => vi.fn());
const listPmDirectorTaskHistoryMock = vi.hoisted(() => vi.fn());
const listPmTasksMock = vi.hoisted(() => vi.fn());
const listPmRequirementsMock = vi.hoisted(() => vi.fn());
const getPmRequirementMock = vi.hoisted(() => vi.fn());
const getPmStatusMock = vi.hoisted(() => vi.fn());

vi.mock('@/services/pmService', () => ({
  getPmStatus: getPmStatusMock,
  listPmTasks: listPmTasksMock,
  listPmRequirements: listPmRequirementsMock,
  getPmRequirement: getPmRequirementMock,
  listPmTaskHistory: listPmTaskHistoryMock,
  listPmDirectorTaskHistory: listPmDirectorTaskHistoryMock,
}));

vi.mock('./PMAIDialoguePanel', () => ({
  PMAIDialoguePanel: ({ taskCount }: { taskCount: number }) => (
    <div data-testid="pm-ai-dialogue-mock">taskCount={taskCount}</div>
  ),
}));

vi.mock('./PMTaskPanel', () => ({
  PMTaskPanel: ({ tasks }: { tasks: Array<{ id: string }> }) => (
    <div data-testid="pm-task-panel-mock">tasks={tasks.length}</div>
  ),
}));

vi.mock('./PMDocumentPanel', () => ({
  PMDocumentPanel: () => <div data-testid="pm-document-panel-mock" />,
}));

vi.mock('./PMDiagnosticsPanel', () => ({
  PMDiagnosticsPanel: () => null,
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
      },
    });
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

    await waitFor(() => expect(listPmTasksMock).toHaveBeenCalledWith({ limit: 100, offset: 0 }));
    const evidence = await screen.findByTestId('pm-task-backend-evidence');
    expect(evidence).toHaveTextContent('/v2/pm/tasks');
    expect(evidence).toHaveTextContent('backend=2');
    expect(evidence).toHaveTextContent('runtime=0');
    expect(evidence).toHaveTextContent('merged=2');
    expect(screen.getByTestId('pm-task-panel-mock')).toHaveTextContent('tasks=2');
    expect(screen.getByTestId('pm-ai-dialogue-mock')).toHaveTextContent('taskCount=2');
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

    await waitFor(() => expect(listPmTaskHistoryMock).toHaveBeenCalledWith({ limit: 50, offset: 0 }));
    expect(listPmDirectorTaskHistoryMock).toHaveBeenCalledWith({ limit: 25, offset: 0 });
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

    await waitFor(() => expect(listPmRequirementsMock).toHaveBeenCalledWith({ limit: 100, offset: 0 }));
    const panel = await screen.findByTestId('pm-requirements-panel');
    expect(panel).toHaveTextContent('/v2/pm/requirements');
    await waitFor(() => expect(screen.getByTestId('pm-requirements-count')).toHaveTextContent('1'));
    expect(screen.getByTestId('pm-requirements-list')).toHaveTextContent('Traceable requirement');
    await waitFor(() => expect(getPmRequirementMock).toHaveBeenCalledWith('REQ-1'));
    await waitFor(() => expect(screen.getByTestId('pm-requirement-detail')).toHaveTextContent('Requirement detail payload'));
    const detail = screen.getByTestId('pm-requirement-detail');
    expect(detail).toHaveTextContent('/v2/pm/requirements/REQ-1');
    expect(detail).toHaveTextContent('Requirement accepted');
    expect(detail).toHaveTextContent('PM-1');
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

    await waitFor(() => expect(listPmRequirementsMock).toHaveBeenCalledWith({ limit: 100, offset: 0 }));
    expect(screen.queryByTestId('pm-requirements-error')).not.toBeInTheDocument();
    expect(screen.getByTestId('pm-requirements-list')).toHaveTextContent('暂无需求合同');
    expect(screen.getByTestId('pm-requirements-count')).toHaveTextContent('0');

    fireEvent.click(screen.getByTitle('历史'));

    await waitFor(() => expect(listPmTaskHistoryMock).toHaveBeenCalledWith({ limit: 50, offset: 0 }));
    expect(listPmDirectorTaskHistoryMock).toHaveBeenCalledWith({ limit: 25, offset: 0 });
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
    await waitFor(() => expect(getPmStatusMock).toHaveBeenCalledTimes(1));
    const evidence = await screen.findByTestId('pm-run-once-status-evidence');
    expect(evidence).toHaveTextContent('/v2/pm/status');
    expect(evidence).toHaveTextContent('running');
    expect(evidence).toHaveTextContent('pid=4242');
    expect(evidence).toHaveTextContent('mode=run_once');
    expect(evidence).toHaveTextContent('source=handle');
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
    await waitFor(() => expect(getPmStatusMock).toHaveBeenCalledTimes(1));
    const evidence = await screen.findByTestId('pm-toggle-status-evidence');
    expect(evidence).toHaveTextContent('/v2/pm/status');
    expect(evidence).toHaveTextContent('running');
    expect(evidence).toHaveTextContent('pid=5150');
    expect(evidence).toHaveTextContent('mode=loop');
    expect(evidence).toHaveTextContent('source=status_file');
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
