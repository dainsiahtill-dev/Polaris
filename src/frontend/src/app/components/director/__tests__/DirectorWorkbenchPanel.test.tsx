import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DirectorWorkbenchPanel } from '../DirectorWorkbenchPanel';

const roleSessionMocks = vi.hoisted(() => ({
  createRoleSession: vi.fn(),
  exportRoleSessionToWorkflow: vi.fn(),
  listRoleSessionArtifactEvidence: vi.fn(),
  listRoleSessionAuditEvidence: vi.fn(),
  listRoleSessionMessageEvidence: vi.fn(),
  listRoleSessions: vi.fn(),
}));

const pmServiceMocks = vi.hoisted(() => ({
  cancelDirectorRun: vi.fn(),
  getDirectorRun: vi.fn(),
}));

const factoryServiceMocks = vi.hoisted(() => ({
  getFactoryRun: vi.fn(),
  stopFactoryRun: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
}));

vi.mock('@/services/roleSessionService', () => roleSessionMocks);
vi.mock('@/services/pmService', () => pmServiceMocks);
vi.mock('@/services/factoryService', () => factoryServiceMocks);

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
    roleSessionMocks.listRoleSessionMessageEvidence.mockResolvedValue({
      ok: true,
      data: {
        items: [{ id: 'director-message-1', role: 'assistant', content: 'Director patch evidence ready' }],
        total: 7,
      },
    });
    roleSessionMocks.listRoleSessionArtifactEvidence.mockResolvedValue({
      ok: true,
      data: {
        items: [{ id: 'director-artifact-1', type: 'patch' }],
        total: 1,
      },
    });
    roleSessionMocks.listRoleSessionAuditEvidence.mockResolvedValue({
      ok: true,
      data: {
        items: [{ id: 'director-audit-1', event_type: 'workflow_exported', timestamp: '2026-05-23T00:00:00Z' }],
        total: 4,
      },
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
    factoryServiceMocks.getFactoryRun.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'factory-run-from-director',
        status: 'running',
        phase: 'implementation',
        progress: 65,
        roles: {},
        gates: [],
        created_at: '2026-05-23T00:00:00Z',
      },
    });
    factoryServiceMocks.stopFactoryRun.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'factory-run-from-director',
        status: 'cancelled',
        phase: 'cancelled',
        progress: 65,
        roles: {},
        gates: [],
        created_at: '2026-05-23T00:00:00Z',
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
    await waitFor(() => expect(roleSessionMocks.listRoleSessionMessageEvidence).toHaveBeenCalledWith('director-session-1', {
      limit: 5,
      offset: 0,
    }));
    expect(roleSessionMocks.listRoleSessionArtifactEvidence).toHaveBeenCalledWith('director-session-1');
    expect(roleSessionMocks.listRoleSessionAuditEvidence).toHaveBeenCalledWith('director-session-1', {
      limit: 5,
      offset: 0,
    });
    expect(await screen.findByTestId('role-session-evidence-panel')).not.toHaveTextContent('/v2/roles/sessions/director-session-1');
    expect(screen.getByTestId('role-session-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/roles/sessions/director-session-1',
    );
    expect(screen.getByTestId('role-session-evidence-messages')).toHaveTextContent('assistant: Director patch evidence ready');
    expect(screen.getByTestId('role-session-evidence-messages')).toHaveTextContent('7');
    expect(screen.getByTestId('role-session-evidence-artifacts')).toHaveTextContent('patch: director-artifact-1');
    expect(screen.getByTestId('role-session-evidence-audit')).toHaveTextContent('workflow_exported');

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
    await waitFor(() => expect(pmServiceMocks.getDirectorRun).toHaveBeenCalledWith('director-run-1', 'C:/Temp/Product'));
    const evidence = await screen.findByTestId('director-workbench-run-evidence');
    expect(evidence).not.toHaveTextContent('/v2/director/runs/director-run-1');
    expect(screen.getByTestId('director-workbench-run-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/runs/director-run-1?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(evidence).toHaveTextContent('RUNNING · queued=5');
    expect(screen.getByTestId('director-workbench-run-evidence-realtime-push')).toHaveTextContent('实时推送');

    pmServiceMocks.getDirectorRun.mockResolvedValueOnce({
      ok: true,
      data: {
        run_id: 'director-run-1',
        status: 'COMPLETED',
        workspace: 'C:/Temp/Product',
        tasks_queued: 5,
        message: 'Status: COMPLETED',
      },
    });
    fireEvent.click(screen.getByTestId('director-workbench-run-refresh'));

    await waitFor(() => expect(pmServiceMocks.getDirectorRun).toHaveBeenCalledTimes(2));
    expect(evidence).toHaveTextContent('COMPLETED · queued=5');
    expect(screen.queryByTestId('director-workbench-run-evidence-realtime-push')).not.toBeInTheDocument();
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

    await waitFor(() => expect(pmServiceMocks.getDirectorRun).toHaveBeenCalledWith('director-run-1', 'C:/Temp/Product'));
    fireEvent.click(await screen.findByTestId('director-workbench-run-cancel'));

    await waitFor(() => expect(pmServiceMocks.cancelDirectorRun).toHaveBeenCalledWith('director-run-1', 'C:/Temp/Product'));
    const evidence = await screen.findByTestId('director-workbench-run-evidence');
    expect(evidence).not.toHaveTextContent('/v2/director/runs/director-run-1');
    expect(screen.getByTestId('director-workbench-run-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/runs/director-run-1?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(evidence).toHaveTextContent('CANCELLED · queued=5');
    const cancelEvidence = await screen.findByTestId('director-workbench-run-cancel-result');
    expect(cancelEvidence).toHaveAttribute(
      'data-endpoint',
      '/v2/director/runs/director-run-1/cancel?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(cancelEvidence).not.toHaveTextContent('/v2/director/runs/director-run-1/cancel');
    expect(cancelEvidence).toHaveTextContent('取消运行已提交: CANCELLED');
    expect(toastMocks.success).toHaveBeenCalledWith('Director 编排取消已提交', {
      description: 'Run ID: director-run-1',
    });
  });

  it('exports Director RoleSession to Factory with run evidence', async () => {
    roleSessionMocks.exportRoleSessionToWorkflow.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, run_id: 'factory-run-from-director', artifact_count: 7 },
    });
    render(
      <DirectorWorkbenchPanel
        workspace="C:/Temp/Product"
        initialSessionId="director-session-1"
        tasksCount={5}
        runningTasks={1}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '导出 Factory' }));

    await waitFor(() => expect(roleSessionMocks.exportRoleSessionToWorkflow).toHaveBeenCalledWith('director-session-1', {
      target: 'factory',
      export_kind: 'session_bundle',
      include_audit_log: true,
    }));
    expect(toastMocks.success).toHaveBeenCalledWith('已导出到 Factory 流水线', {
      description: 'Run ID: factory-run-from-director\nArtifacts: 7',
    });
    await waitFor(() => expect(factoryServiceMocks.getFactoryRun).toHaveBeenCalledWith('factory-run-from-director'));
    const evidence = await screen.findByTestId('director-workbench-factory-evidence');
    expect(evidence).not.toHaveTextContent('/v2/factory/runs/factory-run-from-director');
    expect(screen.getByTestId('director-workbench-factory-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/factory/runs/factory-run-from-director',
    );
    expect(evidence).toHaveTextContent('running · phase=implementation · progress=65%');

    fireEvent.click(screen.getByTestId('director-workbench-factory-evidence-cancel'));

    await waitFor(() => expect(factoryServiceMocks.stopFactoryRun).toHaveBeenCalledWith('factory-run-from-director'));
    expect(evidence).toHaveTextContent('cancelled · phase=cancelled · progress=65%');
  });
});
