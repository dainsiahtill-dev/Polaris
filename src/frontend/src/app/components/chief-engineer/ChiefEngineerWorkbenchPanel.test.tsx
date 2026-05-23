import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChiefEngineerWorkbenchPanel } from './ChiefEngineerWorkbenchPanel';

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

describe('ChiefEngineerWorkbenchPanel RoleSession service bridge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    roleSessionMocks.listRoleSessions.mockResolvedValue({
      ok: true,
      data: [{ id: 'ce-session-1', title: 'Blueprint session', updated_at: '2026-05-23T00:00:00Z' }],
    });
    roleSessionMocks.createRoleSession.mockResolvedValue({
      ok: true,
      data: { id: 'ce-session-2', title: 'New Chief Engineer session' },
    });
    roleSessionMocks.exportRoleSessionToWorkflow.mockResolvedValue({
      ok: true,
      data: { ok: true, run_id: 'director-run-from-ce', artifact_count: 3 },
    });
    pmServiceMocks.getDirectorRun.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'director-run-from-ce',
        status: 'RUNNING',
        workspace: 'C:/Temp/Product',
        tasks_queued: 2,
        message: 'Status: RUNNING',
      },
    });
    pmServiceMocks.cancelDirectorRun.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'director-run-from-ce',
        status: 'CANCELLED',
        workspace: 'C:/Temp/Product',
        tasks_queued: 2,
        message: 'Status: CANCELLED',
      },
    });
  });

  it('loads, switches, creates, and exports Chief Engineer RoleSessions to Director workflow', async () => {
    render(
      <ChiefEngineerWorkbenchPanel
        workspace="C:/Temp/Product"
        taskCount={6}
        blueprintCount={2}
        missingBlueprintCount={1}
        directorRunning={false}
      />,
    );

    await waitFor(() => expect(roleSessionMocks.listRoleSessions).toHaveBeenCalledWith({
      role: 'chief_engineer',
      hostKind: 'electron_workbench',
      workspace: 'C:/Temp/Product',
      limit: 20,
    }));

    const selector = await screen.findByTestId('chief-engineer-role-session-select');
    expect(selector).toHaveTextContent('Blueprint session');

    fireEvent.change(selector, { target: { value: 'ce-session-1' } });
    await waitFor(() => expect(screen.getByTestId('chief_engineer-dialogue')).toHaveAttribute('data-session-id', 'ce-session-1'));

    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));

    await waitFor(() => expect(roleSessionMocks.createRoleSession).toHaveBeenCalledWith({
      role: 'chief_engineer',
      host_kind: 'electron_workbench',
      workspace: 'C:/Temp/Product',
      attachment_mode: 'isolated',
      context_config: {
        task_count: 6,
        blueprint_count: 2,
        missing_blueprint_count: 1,
        director_running: false,
      },
    }));
    await waitFor(() => expect(screen.getByTestId('chief_engineer-dialogue')).toHaveAttribute('data-session-id', 'ce-session-2'));

    fireEvent.click(screen.getByRole('button', { name: '导出 Director' }));

    await waitFor(() => expect(roleSessionMocks.exportRoleSessionToWorkflow).toHaveBeenCalledWith('ce-session-2', {
      target: 'director',
      export_kind: 'session_bundle',
      include_audit_log: true,
    }));
    expect(toastMocks.success).toHaveBeenCalledWith('已导出到 Director 工作流', {
      description: 'Run ID: director-run-from-ce\nArtifacts: 3',
    });
    await waitFor(() => expect(pmServiceMocks.getDirectorRun).toHaveBeenCalledWith('director-run-from-ce'));
    const evidence = await screen.findByTestId('chief-engineer-workbench-run-evidence');
    expect(evidence).toHaveTextContent('/v2/director/runs/director-run-from-ce');
    expect(evidence).toHaveTextContent('RUNNING · queued=2');
  });

  it('cancels the Director run created from Chief Engineer workbench export', async () => {
    render(
      <ChiefEngineerWorkbenchPanel
        workspace="C:/Temp/Product"
        initialSessionId="ce-session-1"
        taskCount={6}
        blueprintCount={2}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '导出 Director' }));

    await waitFor(() => expect(pmServiceMocks.getDirectorRun).toHaveBeenCalledWith('director-run-from-ce'));
    fireEvent.click(await screen.findByTestId('chief-engineer-workbench-run-cancel'));

    await waitFor(() => expect(pmServiceMocks.cancelDirectorRun).toHaveBeenCalledWith('director-run-from-ce'));
    const evidence = await screen.findByTestId('chief-engineer-workbench-run-evidence');
    expect(evidence).toHaveTextContent('/v2/director/runs/director-run-from-ce');
    expect(evidence).toHaveTextContent('CANCELLED · queued=2');
    const cancelEvidence = await screen.findByTestId('chief-engineer-workbench-run-cancel-result');
    expect(cancelEvidence).toHaveTextContent('/v2/director/runs/director-run-from-ce/cancel');
    expect(cancelEvidence).toHaveTextContent('取消运行已提交: CANCELLED');
    expect(toastMocks.success).toHaveBeenCalledWith('Director 编排取消已提交', {
      description: 'Run ID: director-run-from-ce',
    });
  });
});
