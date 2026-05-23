import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PMWorkbenchPanel } from './PMWorkbenchPanel';

const roleSessionMocks = vi.hoisted(() => ({
  createRoleSession: vi.fn(),
  exportRoleSessionToWorkflow: vi.fn(),
  listRoleSessions: vi.fn(),
}));

const pmServiceMocks = vi.hoisted(() => ({
  cancelPmRun: vi.fn(),
  getPmRun: vi.fn(),
  runPm: vi.fn(),
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

describe('PMWorkbenchPanel RoleSession service bridge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    roleSessionMocks.listRoleSessions.mockResolvedValue({
      ok: true,
      data: [{ id: 'pm-session-1', title: 'Planning session', updated_at: '2026-05-23T00:00:00Z' }],
    });
    roleSessionMocks.createRoleSession.mockResolvedValue({
      ok: true,
      data: { id: 'pm-session-2', title: 'New PM session' },
    });
    roleSessionMocks.exportRoleSessionToWorkflow.mockResolvedValue({
      ok: true,
      data: { ok: true, run_id: 'pm-run-1', artifact_count: 2 },
    });
    pmServiceMocks.getPmRun.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'pm-run-1',
        status: 'RUNNING',
        workspace: 'C:/Temp/Product',
        stage: 'architect',
        message: 'Status: RUNNING',
      },
    });
    pmServiceMocks.runPm.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'pm-run-direct',
        status: 'RUNNING',
        workspace: 'C:/Temp/Product',
        stage: 'architect',
        message: 'PM architect run started',
      },
    });
    pmServiceMocks.cancelPmRun.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'pm-run-direct',
        status: 'CANCELLED',
        workspace: 'C:/Temp/Product',
        stage: 'architect',
        message: 'Status: CANCELLED',
      },
    });
  });

  it('loads, switches, and creates PM RoleSessions through the typed service', async () => {
    render(
      <PMWorkbenchPanel
        workspace="C:/Temp/Product"
        pmRunning
        taskCount={3}
      />,
    );

    await waitFor(() => expect(roleSessionMocks.listRoleSessions).toHaveBeenCalledWith({
      role: 'pm',
      hostKind: 'electron_workbench',
      workspace: 'C:/Temp/Product',
      limit: 20,
    }));

    const selector = await screen.findByTestId('pm-role-session-select');
    expect(selector).toHaveTextContent('Planning session');

    fireEvent.change(selector, { target: { value: 'pm-session-1' } });
    await waitFor(() => expect(screen.getByTestId('pm-dialogue')).toHaveAttribute('data-session-id', 'pm-session-1'));

    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));

    await waitFor(() => expect(roleSessionMocks.createRoleSession).toHaveBeenCalledWith({
      role: 'pm',
      host_kind: 'electron_workbench',
      workspace: 'C:/Temp/Product',
      attachment_mode: 'isolated',
      context_config: {
        pm_running: true,
        task_count: 3,
      },
    }));
    await waitFor(() => expect(screen.getByTestId('pm-dialogue')).toHaveAttribute('data-session-id', 'pm-session-2'));
  });

  it('exports the active PM workbench session to workflow through the typed service', async () => {
    render(
      <PMWorkbenchPanel
        workspace="C:/Temp/Product"
        initialSessionId="pm-session-1"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '导出到流程' }));

    await waitFor(() => expect(roleSessionMocks.exportRoleSessionToWorkflow).toHaveBeenCalledWith('pm-session-1', {
      target: 'pm',
      export_kind: 'session_bundle',
      include_audit_log: true,
    }));
    expect(toastMocks.success).toHaveBeenCalledWith('已导出到 PM 工作流', {
      description: 'Run ID: pm-run-1\nArtifacts: 2',
    });
    await waitFor(() => expect(pmServiceMocks.getPmRun).toHaveBeenCalledWith('pm-run-1'));
    const evidence = await screen.findByTestId('pm-workbench-run-evidence');
    expect(evidence).toHaveTextContent('/v2/pm/runs/pm-run-1');
    expect(evidence).toHaveTextContent('RUNNING · architect');
  });

  it('launches PM orchestration through the typed /v2/pm/run service', async () => {
    render(
      <PMWorkbenchPanel
        workspace="C:/Temp/Product"
        initialSessionId="pm-session-1"
      />,
    );

    fireEvent.change(screen.getByTestId('pm-workbench-run-directive'), {
      target: { value: 'Plan the payment workflow' },
    });
    fireEvent.change(screen.getByTestId('pm-workbench-run-stage'), {
      target: { value: 'architect' },
    });
    fireEvent.click(screen.getByTestId('pm-workbench-run-director'));
    fireEvent.click(screen.getByTestId('pm-workbench-run-pm'));

    await waitFor(() => expect(pmServiceMocks.runPm).toHaveBeenCalledWith({
      workspace: 'C:/Temp/Product',
      directive: 'Plan the payment workflow',
      stage: 'architect',
      run_director: true,
      director_iterations: 2,
      metadata: {
        source: 'pm_workbench',
        role_session_id: 'pm-session-1',
        host_kind: 'electron_workbench',
      },
    }));
    expect(toastMocks.success).toHaveBeenCalledWith('PM 编排已启动', {
      description: 'Run ID: pm-run-direct',
    });
    await waitFor(() => expect(pmServiceMocks.getPmRun).toHaveBeenCalledWith('pm-run-direct'));
    const evidence = await screen.findByTestId('pm-workbench-run-evidence');
    expect(evidence).toHaveTextContent('/v2/pm/runs/pm-run-direct');
  });

  it('cancels the visible PM orchestration run from the evidence strip', async () => {
    render(
      <PMWorkbenchPanel
        workspace="C:/Temp/Product"
        initialSessionId="pm-session-1"
      />,
    );

    fireEvent.change(screen.getByTestId('pm-workbench-run-directive'), {
      target: { value: 'Plan the payment workflow' },
    });
    fireEvent.click(screen.getByTestId('pm-workbench-run-pm'));

    await waitFor(() => expect(pmServiceMocks.getPmRun).toHaveBeenCalledWith('pm-run-direct'));
    fireEvent.click(await screen.findByTestId('pm-workbench-run-cancel'));

    await waitFor(() => expect(pmServiceMocks.cancelPmRun).toHaveBeenCalledWith('pm-run-direct'));
    const evidence = await screen.findByTestId('pm-workbench-run-evidence');
    expect(evidence).toHaveTextContent('/v2/pm/runs/pm-run-direct');
    expect(evidence).toHaveTextContent('CANCELLED · architect');
    const cancelEvidence = await screen.findByTestId('pm-workbench-run-cancel-result');
    expect(cancelEvidence).toHaveTextContent('/v2/pm/runs/pm-run-direct/cancel');
    expect(cancelEvidence).toHaveTextContent('取消运行已提交: CANCELLED');
    expect(toastMocks.success).toHaveBeenCalledWith('PM 编排取消已提交', {
      description: 'Run ID: pm-run-direct',
    });
  });
});
