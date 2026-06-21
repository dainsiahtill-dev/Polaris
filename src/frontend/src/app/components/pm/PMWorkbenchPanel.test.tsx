import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PMWorkbenchPanel } from './PMWorkbenchPanel';

const roleSessionMocks = vi.hoisted(() => ({
  createRoleSession: vi.fn(),
  exportRoleSessionToWorkflow: vi.fn(),
  listRoleSessionArtifactEvidence: vi.fn(),
  listRoleSessionAuditEvidence: vi.fn(),
  listRoleSessionMessageEvidence: vi.fn(),
  listRoleSessions: vi.fn(),
}));

const pmServiceMocks = vi.hoisted(() => ({
  cancelPmRun: vi.fn(),
  getDirectorDiagnostics: vi.fn(),
  getPmRun: vi.fn(),
  runPm: vi.fn(),
}));

const chiefEngineerServiceMocks = vi.hoisted(() => ({
  getChiefEngineerDiagnostics: vi.fn(),
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
vi.mock('@/services/chiefEngineerService', () => chiefEngineerServiceMocks);
vi.mock('@/services/factoryService', () => factoryServiceMocks);

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
    roleSessionMocks.listRoleSessionMessageEvidence.mockResolvedValue({
      ok: true,
      data: {
        items: [{ id: 'pm-message-1', role: 'assistant', content: 'PM contract evidence ready' }],
        total: 6,
      },
    });
    roleSessionMocks.listRoleSessionArtifactEvidence.mockResolvedValue({
      ok: true,
      data: {
        items: [{ id: 'pm-artifact-1', type: 'directive' }],
        total: 1,
      },
    });
    roleSessionMocks.listRoleSessionAuditEvidence.mockResolvedValue({
      ok: true,
      data: {
        items: [{ id: 'pm-audit-1', event_type: 'workflow_exported', timestamp: '2026-05-23T00:00:00Z' }],
        total: 3,
      },
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
    pmServiceMocks.getDirectorDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
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
          source: 'empty',
          total: 0,
          pending: 0,
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
          ok: true,
          total: 1,
          idle: 1,
          busy: 0,
          healthy: 1,
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
        issues: ['director_no_tasks'],
        execution_blockers: ['director_no_tasks'],
      },
    });
    chiefEngineerServiceMocks.getChiefEngineerDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        can_handoff: true,
        role: 'chief_engineer',
        generated_at: '2026-05-23T00:00:00Z',
        workspace: {
          ok: true,
          status: 'ready',
          workspace: 'C:/Temp/Product',
          exists: true,
          error: null,
        },
        llm: {
          ok: true,
          state: 'ready',
          role: 'chief_engineer',
          blocked_roles: [],
          unsupported_roles: [],
          required_ready_roles: ['chief_engineer'],
          provider_id: 'qwen',
          model: 'qwen3-max',
          error: null,
          details: {},
        },
        blueprints: {
          ok: true,
          status: 'ready',
          source: 'chief_engineer_blueprints',
          total: 1,
          loadable: 1,
          invalid_payloads: 0,
          planned_tasks: 1,
          covered_tasks: 1,
          missing_task_ids: [],
          director_handoff_ready: true,
          latest_updated_at: '2026-05-23T00:00:00Z',
          error: null,
        },
        can_generate: true,
        issues: [],
        generate_blockers: [],
        handoff_blockers: [],
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
    factoryServiceMocks.getFactoryRun.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'factory-run-from-pm',
        status: 'running',
        phase: 'planning',
        progress: 25,
        roles: {},
        gates: [],
        created_at: '2026-05-23T00:00:00Z',
      },
    });
    factoryServiceMocks.stopFactoryRun.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'factory-run-from-pm',
        status: 'cancelled',
        phase: 'cancelled',
        progress: 25,
        roles: {},
        gates: [],
        created_at: '2026-05-23T00:00:00Z',
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
    await waitFor(() => expect(roleSessionMocks.listRoleSessionMessageEvidence).toHaveBeenCalledWith('pm-session-1', {
      limit: 5,
      offset: 0,
    }));
    expect(roleSessionMocks.listRoleSessionArtifactEvidence).toHaveBeenCalledWith('pm-session-1');
    expect(roleSessionMocks.listRoleSessionAuditEvidence).toHaveBeenCalledWith('pm-session-1', {
      limit: 5,
      offset: 0,
    });
    expect(await screen.findByTestId('role-session-evidence-panel')).not.toHaveTextContent('/v2/roles/sessions/pm-session-1');
    expect(screen.getByTestId('role-session-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/roles/sessions/pm-session-1',
    );
    expect(screen.getByTestId('role-session-evidence-messages')).toHaveTextContent('assistant: PM contract evidence ready');
    expect(screen.getByTestId('role-session-evidence-messages')).toHaveTextContent('6');
    expect(screen.getByTestId('role-session-evidence-artifacts')).toHaveTextContent('directive: pm-artifact-1');
    expect(screen.getByTestId('role-session-evidence-audit')).toHaveTextContent('workflow_exported');

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
    await waitFor(() => expect(pmServiceMocks.getPmRun).toHaveBeenCalledWith('pm-run-1', 'C:/Temp/Product'));
    const evidence = await screen.findByTestId('pm-workbench-run-evidence');
    expect(evidence).not.toHaveTextContent('/v2/pm/runs/pm-run-1');
    expect(screen.getByTestId('pm-workbench-run-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/pm/runs/pm-run-1?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(evidence).toHaveTextContent('RUNNING · architect');
    expect(screen.getByTestId('pm-workbench-run-evidence-realtime-push')).toHaveTextContent('实时推送');

    pmServiceMocks.getPmRun.mockResolvedValueOnce({
      ok: true,
      data: {
        run_id: 'pm-run-1',
        status: 'COMPLETED',
        workspace: 'C:/Temp/Product',
        stage: 'architect',
        message: 'Status: COMPLETED',
      },
    });
    fireEvent.click(screen.getByTestId('pm-workbench-run-refresh'));

    await waitFor(() => expect(pmServiceMocks.getPmRun).toHaveBeenCalledTimes(2));
    expect(evidence).toHaveTextContent('COMPLETED · architect');
    expect(screen.queryByTestId('pm-workbench-run-evidence-realtime-push')).not.toBeInTheDocument();
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
    const readiness = screen.getByTestId('pm-workbench-director-readiness');
    await waitFor(() => expect(readiness).toHaveTextContent('director-llm'));
    expect(readiness).toHaveTextContent('ce-blueprint');
    expect(readiness).toHaveTextContent('ready');
    expect(chiefEngineerServiceMocks.getChiefEngineerDiagnostics).toHaveBeenCalledWith('C:/Temp/Product');
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
    await waitFor(() => expect(pmServiceMocks.getPmRun).toHaveBeenCalledWith('pm-run-direct', 'C:/Temp/Product'));
    const evidence = await screen.findByTestId('pm-workbench-run-evidence');
    expect(evidence).not.toHaveTextContent('/v2/pm/runs/pm-run-direct');
    expect(screen.getByTestId('pm-workbench-run-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/pm/runs/pm-run-direct?workspace=C%3A%2FTemp%2FProduct',
    );
  });

  it('blocks PM auto-dispatch when Director LLM readiness is blocked', async () => {
    pmServiceMocks.getDirectorDiagnostics.mockResolvedValue({
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
      <PMWorkbenchPanel
        workspace="C:/Temp/Product"
        initialSessionId="pm-session-1"
      />,
    );

    fireEvent.change(screen.getByTestId('pm-workbench-run-directive'), {
      target: { value: 'Plan the payment workflow' },
    });
    fireEvent.click(screen.getByTestId('pm-workbench-run-director'));

    const readiness = await screen.findByTestId('pm-workbench-director-readiness');
    await waitFor(() => expect(readiness).toHaveTextContent('blocked'));
    const runButton = screen.getByTestId('pm-workbench-run-pm');
    expect(runButton).toBeDisabled();
    expect(runButton).toHaveAttribute('title', 'Director LLM 未就绪: director');

    fireEvent.click(runButton);
    expect(pmServiceMocks.runPm).not.toHaveBeenCalled();
  });

  it('blocks PM auto-dispatch when Chief Engineer blueprint handoff is incomplete', async () => {
    chiefEngineerServiceMocks.getChiefEngineerDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: false,
        can_handoff: false,
        role: 'chief_engineer',
        generated_at: '2026-05-23T00:00:00Z',
        workspace: {
          ok: true,
          status: 'ready',
          workspace: 'C:/Temp/Product',
          exists: true,
          error: null,
        },
        llm: {
          ok: true,
          state: 'ready',
          role: 'chief_engineer',
          blocked_roles: [],
          unsupported_roles: [],
          required_ready_roles: ['chief_engineer'],
          provider_id: 'qwen',
          model: 'qwen3-max',
          error: null,
          details: {},
        },
        blueprints: {
          ok: false,
          status: 'blocked',
          source: 'chief_engineer_blueprints',
          total: 0,
          loadable: 0,
          invalid_payloads: 0,
          planned_tasks: 2,
          covered_tasks: 0,
          missing_task_ids: ['PM-1', 'PM-2'],
          director_handoff_ready: false,
          latest_updated_at: null,
          error: null,
        },
        can_generate: true,
        issues: ['blueprint_coverage_incomplete'],
        generate_blockers: [],
        handoff_blockers: ['blueprint_coverage_incomplete'],
      },
    });

    render(
      <PMWorkbenchPanel
        workspace="C:/Temp/Product"
        initialSessionId="pm-session-1"
      />,
    );

    fireEvent.click(screen.getByTestId('pm-workbench-run-director'));

    const readiness = await screen.findByTestId('pm-workbench-director-readiness');
    await waitFor(() => expect(readiness).toHaveTextContent('ce-blueprint'));
    expect(readiness).toHaveTextContent('missing 2');
    const runButton = screen.getByTestId('pm-workbench-run-pm');
    expect(runButton).toBeDisabled();
    expect(runButton).toHaveAttribute('title', 'Chief Engineer 蓝图覆盖不足：缺少 2 个 PM 任务');

    fireEvent.click(runButton);
    expect(pmServiceMocks.runPm).not.toHaveBeenCalled();
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
    const readiness = await screen.findByTestId('pm-workbench-director-readiness');
    await waitFor(() => expect(readiness).toHaveTextContent('ready'));
    fireEvent.click(screen.getByTestId('pm-workbench-run-pm'));

    await waitFor(() => expect(pmServiceMocks.getPmRun).toHaveBeenCalledWith('pm-run-direct', 'C:/Temp/Product'));
    fireEvent.click(await screen.findByTestId('pm-workbench-run-cancel'));

    await waitFor(() => expect(pmServiceMocks.cancelPmRun).toHaveBeenCalledWith('pm-run-direct', 'C:/Temp/Product'));
    const evidence = await screen.findByTestId('pm-workbench-run-evidence');
    expect(evidence).not.toHaveTextContent('/v2/pm/runs/pm-run-direct');
    expect(screen.getByTestId('pm-workbench-run-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/pm/runs/pm-run-direct?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(evidence).toHaveTextContent('CANCELLED · architect');
    const cancelEvidence = await screen.findByTestId('pm-workbench-run-cancel-result');
    expect(cancelEvidence).toHaveAttribute(
      'data-endpoint',
      '/v2/pm/runs/pm-run-direct/cancel?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(cancelEvidence).not.toHaveTextContent('/v2/pm/runs/pm-run-direct/cancel');
    expect(cancelEvidence).toHaveTextContent('取消运行已提交: CANCELLED');
    expect(toastMocks.success).toHaveBeenCalledWith('PM 编排取消已提交', {
      description: 'Run ID: pm-run-direct',
    });
  });

  it('exports the active PM session to Factory and shows cancellable Factory evidence', async () => {
    roleSessionMocks.exportRoleSessionToWorkflow.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, run_id: 'factory-run-from-pm', artifact_count: 5 },
    });
    render(
      <PMWorkbenchPanel
        workspace="C:/Temp/Product"
        initialSessionId="pm-session-1"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '导出 Factory' }));

    await waitFor(() => expect(roleSessionMocks.exportRoleSessionToWorkflow).toHaveBeenCalledWith('pm-session-1', {
      target: 'factory',
      export_kind: 'session_bundle',
      include_audit_log: true,
    }));
    expect(toastMocks.success).toHaveBeenCalledWith('已导出到 Factory 流水线', {
      description: 'Run ID: factory-run-from-pm\nArtifacts: 5',
    });
    await waitFor(() => expect(factoryServiceMocks.getFactoryRun).toHaveBeenCalledWith('factory-run-from-pm'));
    const evidence = await screen.findByTestId('pm-workbench-factory-evidence');
    expect(evidence).not.toHaveTextContent('/v2/factory/runs/factory-run-from-pm');
    expect(screen.getByTestId('pm-workbench-factory-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/factory/runs/factory-run-from-pm',
    );
    expect(evidence).toHaveTextContent('running · phase=planning · progress=25%');

    fireEvent.click(screen.getByTestId('pm-workbench-factory-evidence-cancel'));

    await waitFor(() => expect(factoryServiceMocks.stopFactoryRun).toHaveBeenCalledWith('factory-run-from-pm'));
    expect(evidence).toHaveTextContent('cancelled · phase=cancelled · progress=25%');
    expect(screen.getByTestId('pm-workbench-factory-evidence-cancel-result')).toHaveAttribute(
      'data-endpoint',
      '/v2/factory/runs/factory-run-from-pm/control',
    );
    expect(toastMocks.success).toHaveBeenCalledWith('Factory 流水线取消已提交', {
      description: 'Run ID: factory-run-from-pm',
    });
  });
});
