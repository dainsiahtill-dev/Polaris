import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DirectorWorkbenchPanel } from '../DirectorWorkbenchPanel';

const roleSessionMocks = vi.hoisted(() => ({
  createRoleSession: vi.fn(),
  exportRoleSessionToWorkflow: vi.fn(),
  listRoleSessions: vi.fn(),
}));

const pmServiceMocks = vi.hoisted(() => ({
  cancelDirectorRun: vi.fn(),
  getDirectorRun: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
}));

vi.mock('@/services/roleSessionService', () => roleSessionMocks);
vi.mock('@/services/pmService', () => pmServiceMocks);

vi.mock('sonner', () => ({
  toast: toastMocks,
}));

vi.mock('@/app/components/ai-dialogue', () => ({
  AIDialoguePanel: ({ dialogueRole, sessionId }: { dialogueRole: string; sessionId?: string }) => (
    <div data-testid={`${dialogueRole}-dialogue`} data-session-id={sessionId || ''} />
  ),
}));

describe('DirectorWorkbenchPanel RoleSession service bridge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    roleSessionMocks.listRoleSessions.mockResolvedValue({
      ok: true,
      data: [{ id: 'director-session-1', title: 'Execution session', updated_at: '2026-05-23T00:00:00Z' }],
    });
    roleSessionMocks.createRoleSession.mockResolvedValue({
      ok: true,
      data: { id: 'director-session-2', title: 'New Director session' },
    });
    roleSessionMocks.exportRoleSessionToWorkflow.mockResolvedValue({
      ok: true,
      data: { ok: true, run_id: 'director-run-1', artifact_count: 4 },
    });
    pmServiceMocks.getDirectorRun.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'director-run-1',
        status: 'RUNNING',
        workspace: 'C:/Temp/Product',
        tasks_queued: 5,
        message: 'Status: RUNNING',
      },
    });
    pmServiceMocks.cancelDirectorRun.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'director-run-1',
        status: 'CANCELLED',
        workspace: 'C:/Temp/Product',
        tasks_queued: 5,
        message: 'Status: CANCELLED',
      },
    });
  });

  it('loads, switches, creates, and exports Director RoleSessions through the typed service', async () => {
    render(
      <DirectorWorkbenchPanel
        workspace="C:/Temp/Product"
        tasksCount={5}
        runningTasks={1}
      />,
    );

    await waitFor(() => expect(roleSessionMocks.listRoleSessions).toHaveBeenCalledWith({
      role: 'director',
      hostKind: 'electron_workbench',
      workspace: 'C:/Temp/Product',
      limit: 20,
    }));

    const selector = await screen.findByTestId('director-role-session-select');
    expect(selector).toHaveTextContent('Execution session');

    fireEvent.change(selector, { target: { value: 'director-session-1' } });
    await waitFor(() => expect(screen.getByTestId('director-dialogue')).toHaveAttribute('data-session-id', 'director-session-1'));

    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));

    await waitFor(() => expect(roleSessionMocks.createRoleSession).toHaveBeenCalledWith({
      role: 'director',
      host_kind: 'electron_workbench',
      workspace: 'C:/Temp/Product',
      attachment_mode: 'isolated',
      context_config: {
        tasks_count: 5,
        running_tasks: 1,
      },
    }));
    await waitFor(() => expect(screen.getByTestId('director-dialogue')).toHaveAttribute('data-session-id', 'director-session-2'));

    fireEvent.click(screen.getByRole('button', { name: '导出补丁' }));

    await waitFor(() => expect(roleSessionMocks.exportRoleSessionToWorkflow).toHaveBeenCalledWith('director-session-2', {
      target: 'director',
      export_kind: 'session_bundle',
      include_audit_log: true,
    }));
    expect(toastMocks.success).toHaveBeenCalledWith('已导出到 Director 工作流', {
      description: 'Run ID: director-run-1\nArtifacts: 4',
    });
    await waitFor(() => expect(pmServiceMocks.getDirectorRun).toHaveBeenCalledWith('director-run-1'));
    const evidence = await screen.findByTestId('director-workbench-run-evidence');
    expect(evidence).toHaveTextContent('/v2/director/runs/director-run-1');
    expect(evidence).toHaveTextContent('RUNNING · queued=5');
  });

  it('cancels the visible Director orchestration run from the workbench evidence strip', async () => {
    render(
      <DirectorWorkbenchPanel
        workspace="C:/Temp/Product"
        initialSessionId="director-session-1"
        tasksCount={5}
        runningTasks={1}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '导出补丁' }));

    await waitFor(() => expect(pmServiceMocks.getDirectorRun).toHaveBeenCalledWith('director-run-1'));
    fireEvent.click(await screen.findByTestId('director-workbench-run-cancel'));

    await waitFor(() => expect(pmServiceMocks.cancelDirectorRun).toHaveBeenCalledWith('director-run-1'));
    const evidence = await screen.findByTestId('director-workbench-run-evidence');
    expect(evidence).toHaveTextContent('/v2/director/runs/director-run-1');
    expect(evidence).toHaveTextContent('CANCELLED · queued=5');
    const cancelEvidence = await screen.findByTestId('director-workbench-run-cancel-result');
    expect(cancelEvidence).toHaveTextContent('/v2/director/runs/director-run-1/cancel');
    expect(cancelEvidence).toHaveTextContent('取消运行已提交: CANCELLED');
    expect(toastMocks.success).toHaveBeenCalledWith('Director 编排取消已提交', {
      description: 'Run ID: director-run-1',
    });
  });
});
