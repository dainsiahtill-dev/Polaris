import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChiefEngineerWorkspace } from './ChiefEngineerWorkspace';
import { TaskStatus, type PmTask } from '@/types/task';

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('@/api', () => ({
  apiFetch: apiFetchMock,
}));

vi.mock('./ChiefEngineerWorkbenchPanel', () => ({
  ChiefEngineerWorkbenchPanel: ({
    workspace,
    taskCount,
    blueprintCount,
    missingBlueprintCount,
    directorRunning,
  }: {
    workspace?: string;
    taskCount?: number;
    blueprintCount?: number;
    missingBlueprintCount?: number;
    directorRunning?: boolean;
  }) => (
    <div data-testid="chief-engineer-workbench-panel-mock">
      workspace={workspace}; taskCount={taskCount}; blueprintCount={blueprintCount};
      missingBlueprintCount={missingBlueprintCount}; directorRunning={String(Boolean(directorRunning))}
    </div>
  ),
}));

const baseProps = {
  workspace: 'C:/Temp/Product',
  tasks: [] as PmTask[],
  workers: [],
  pmState: null,
  engineStatus: null,
  directorRunning: false,
  onBackToMain: vi.fn(),
  onEnterDirectorWorkspace: vi.fn(),
  onToggleDirector: vi.fn(),
};

describe('ChiefEngineerWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let diagnosticsCalls = 0;
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/v2/chief-engineer/blueprints' && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            task_id: 'PM-summary-only',
            workspace: 'C:/Temp/Product',
            status: 'generated',
            blueprint_id: 'ce_PM-summary-only',
            blueprint_path: 'runtime/blueprints/ce_PM-summary-only.json',
            source: 'runtime/blueprints',
            summary: 'Generated Director TaskBoard blueprint',
            recommendations: ['Validate PM acceptance criteria before Director execution.'],
            risks: [],
            blueprint: {
              blueprint_id: 'ce_PM-summary-only',
              task_id: 'PM-summary-only',
              title: '只有摘要的任务',
              summary: 'Generated Director TaskBoard blueprint',
              status: 'generated',
              target_files: ['src/app.tsx'],
              updated_at: '2026-05-23T08:10:00Z',
            },
          }),
        });
      }
      if (path === '/v2/chief-engineer/diagnostics') {
        diagnosticsCalls += 1;
        const hasBlueprint = diagnosticsCalls > 1;
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            can_handoff: hasBlueprint,
            can_generate: true,
            generated_at: '2026-05-23T08:00:00Z',
            workspace: {
              ok: true,
              status: 'ok',
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
              model: 'Qwen3-Max',
              error: null,
              details: {},
            },
            blueprints: {
              ok: true,
              status: hasBlueprint ? 'ready' : 'empty',
              source: 'runtime/blueprints',
              total: hasBlueprint ? 1 : 0,
              loadable: hasBlueprint ? 1 : 0,
              invalid_payloads: 0,
              planned_tasks: hasBlueprint ? 1 : 0,
              covered_tasks: hasBlueprint ? 1 : 0,
              missing_task_ids: [],
              director_handoff_ready: hasBlueprint,
              latest_updated_at: hasBlueprint ? '2026-05-23T08:10:00Z' : null,
              error: null,
            },
            issues: hasBlueprint ? [] : ['blueprint_handoff_not_ready'],
            generate_blockers: [],
            handoff_blockers: hasBlueprint ? [] : ['blueprint_handoff_not_ready'],
          }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints/status?task_id=PM-summary-only') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            task_id: 'PM-summary-only',
            workspace: 'C:/Temp/Product',
            status: 'found',
            blueprint_id: 'ce_existing_PM-summary-only',
            blueprint_path: 'runtime/blueprints/ce_existing_PM-summary-only.json',
            source: 'runtime/blueprints',
            summary: 'Existing backend blueprint status',
            recommendations: [],
            risks: [],
            blueprint: {
              blueprint_id: 'ce_existing_PM-summary-only',
              task_id: 'PM-summary-only',
              title: '已有蓝图状态',
              summary: 'Existing backend blueprint status',
              status: 'found',
              target_files: ['src/status.ts'],
            },
          }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ blueprints: [], total: 0 }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints/bp-001') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            blueprint_id: 'bp-001',
            source: 'runtime/blueprints',
            blueprint: {
              blueprint_id: 'bp-001',
              summary: 'Director TaskBoard detail',
              guardrails: ['do not edit target project'],
            },
          }),
        });
      }
      if (path === '/v2/director/workers') {
        return Promise.resolve({
          ok: true,
          json: async () => [],
        });
      }
      if (path === '/v2/director/status?source=auto') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            running: true,
            pid: 7242,
            started_at: 1779512400,
            mode: 'desktop_service',
            source: 'status_file',
          }),
        });
      }
      if (path === '/v2/roles/sessions') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, session: { id: 'ce-session-1' } }),
        });
      }
      if (path === '/v2/roles/sessions/ce-session-1') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            session: {
              id: 'ce-session-1',
              role: 'chief_engineer',
              host_kind: 'electron_workbench',
              attachment_mode: 'isolated',
              state: 'active',
              message_count: 0,
            },
          }),
        });
      }
      if (path === '/v2/roles/sessions/ce-session-1/actions/export-to-workflow') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            exported_to: 'director',
            run_id: 'director-run-from-ce',
            session_id: 'ce-session-1',
            artifact_count: 2,
          }),
        });
      }
      if (path === '/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            capabilities: { electron_workbench: ['read_files', 'write_blueprint'] },
          }),
        });
      }
      if (path === '/v2/chief-engineer/llm-events?limit=5') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            role: 'chief_engineer',
            events: [
              {
                event_id: 'ce-llm-1',
                role: 'chief_engineer',
                event_type: 'llm_call_start',
                model: 'gpt-test',
                tokens: 321,
              },
            ],
            count: 1,
            stats: { total: 1, call_start: 1 },
          }),
        });
      }
      if (path === '/v2/chief-engineer/cache-stats') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            hits: 4,
            misses: 1,
            size: 5,
            hit_rate: 80,
            enabled: true,
          }),
        });
      }
      if (path === '/v2/chief-engineer/token-budget-stats') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            total: 12000,
            available_conversation: 6000,
            used_tokens: 2048,
          }),
        });
      }
      if (path === '/v2/chief-engineer/cache-clear' && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, message: 'Cache cleared' }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ ready: true, configured: true, role: 'chief_engineer' }),
      });
    });
  });

  it('does not invent blueprint content when no evidence exists', async () => {
    render(<ChiefEngineerWorkspace {...baseProps} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/blueprints'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/diagnostics'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/llm-events?limit=5'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/cache-stats'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/token-budget-stats'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/role/chief_engineer/chat/status'));
    expect(screen.getByTestId('chief-engineer-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-dialogue')).toBeInTheDocument();
    const backendStrip = screen.getByTestId('chief-engineer-backend-strip');
    expect(backendStrip).toHaveTextContent('/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench');
    expect(backendStrip).toHaveTextContent('read_files, write_blueprint');
    expect(backendStrip).toHaveTextContent('/v2/chief-engineer/llm-events?limit=5');
    expect(backendStrip).toHaveTextContent('events=1 · llm_call_start · gpt-test · 321 tokens');
    expect(backendStrip).toHaveTextContent('/v2/chief-engineer/cache-stats');
    expect(backendStrip).toHaveTextContent('hits=4 · misses=1 · size=5 · hit=80%');
    expect(backendStrip).toHaveTextContent('/v2/chief-engineer/token-budget-stats');
    expect(backendStrip).toHaveTextContent('total=12000 · available=6000 · used=2048');
    fireEvent.click(screen.getByTestId('chief-engineer-kernel-cache-clear'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/cache-clear', { method: 'POST' }));
    expect(await screen.findByTestId('chief-engineer-kernel-cache-clear-result')).toHaveTextContent('/v2/chief-engineer/cache-clear · Cache cleared');
    expect(screen.getByTestId('chief-engineer-diagnostics-status')).toHaveTextContent('degraded');
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('ready · Qwen3-Max');
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('0/0');
    expect(screen.getByTestId('chief-engineer-diagnostics-issues')).toHaveTextContent('blueprint_handoff_not_ready');
    expect(screen.getByTestId('chief-engineer-blueprint-empty')).toHaveTextContent('未发现已落盘的 Chief Engineer 蓝图证据');
    expect(screen.getByTestId('chief-engineer-director-empty')).toHaveTextContent('暂无 Director worker 心跳');
  });

  it('renders Chief Engineer runtime activity evidence inside the control room', async () => {
    render(
      <ChiefEngineerWorkspace
        {...baseProps}
        currentPhase="llm_calling"
        llmStreamEvents={[
          {
            id: 'ce-thinking-1',
            timestamp: '2026-05-23T08:00:00Z',
            level: 'thinking',
            source: 'Chief Engineer',
            message: 'Reviewing blueprint handoff constraints',
            meta: { streamEvent: 'thinking_chunk' },
          },
        ]}
        executionLogs={[
          {
            id: 'ce-log-1',
            timestamp: '2026-05-23T08:00:01Z',
            level: 'info',
            source: 'CE',
            message: 'Blueprint diagnostics refreshed',
          },
        ]}
      />,
    );

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/blueprints'));
    const activity = screen.getByTestId('chief-engineer-runtime-activity');
    expect(activity).toHaveTextContent('调用 LLM');
    expect(activity).toHaveTextContent('2 条记录');
    expect(activity).toHaveTextContent('Reviewing blueprint handoff constraints');
  });

  it('exports Chief Engineer dialogue RoleSession to the Director workflow contract', async () => {
    render(<ChiefEngineerWorkspace {...baseProps} />);

    const exportButton = await screen.findByTestId('ai-role-session-export');
    await waitFor(() => expect(exportButton).not.toBeDisabled());
    expect(exportButton).toHaveTextContent('导出 Director');

    fireEvent.click(exportButton);

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith(
        '/v2/roles/sessions/ce-session-1/actions/export-to-workflow',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    const exportCall = apiFetchMock.mock.calls.find(
      ([path]) => path === '/v2/roles/sessions/ce-session-1/actions/export-to-workflow',
    );
    const body = JSON.parse(String((exportCall?.[1] as RequestInit | undefined)?.body || '{}'));
    expect(body).toMatchObject({
      target: 'director',
      export_kind: 'session_bundle',
      include_audit_log: true,
    });
    const exportStatus = await screen.findByTestId('ai-role-session-export-status');
    expect(exportStatus).toHaveTextContent('Run director-r...');
    expect(exportStatus).toHaveAttribute('title', 'director-run-from-ce · artifacts=2 · messages=0');
  });

  it('opens the shared settings surface from the Chief Engineer header control', () => {
    const onOpenSettings = vi.fn();

    render(<ChiefEngineerWorkspace {...baseProps} onOpenSettings={onOpenSettings} />);

    fireEvent.click(screen.getByTestId('chief-engineer-open-settings'));

    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it('opens the Chief Engineer RoleSession workbench from the desktop header', async () => {
    render(
      <ChiefEngineerWorkspace
        {...baseProps}
        tasks={[
          {
            id: 'pm-workbench-source',
            title: 'Workbench blueprint source',
            status: TaskStatus.PENDING,
            acceptance: [],
          } as PmTask,
        ]}
      />,
    );

    expect(await screen.findByTestId('chief-engineer-dialogue')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('chief-engineer-toggle-workbench'));

    const workbench = await screen.findByTestId('chief-engineer-workbench-panel-mock');
    expect(workbench).toHaveTextContent('workspace=C:/Temp/Product');
    expect(workbench).toHaveTextContent('taskCount=1');
    expect(workbench).toHaveTextContent('directorRunning=false');
    expect(screen.queryByTestId('chief-engineer-dialogue')).not.toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-toggle-dialogue')).toBeDisabled();
  });

  it('does not treat task summary as blueprint evidence', async () => {
    const tasks: PmTask[] = [
      {
        id: 'PM-summary-only',
        title: '只有摘要的任务',
        summary: '这里不是 Chief Engineer 蓝图',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [],
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={tasks} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/blueprints'));
    expect(screen.getByTestId('chief-engineer-blueprint-empty')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-start-director')).toBeDisabled();
  });

  it('does not list a PM task as pending when a runtime blueprint matches raw task_id', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/v2/chief-engineer/blueprints') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            total: 1,
            blueprints: [
              {
                blueprint_id: 'ce_PM-runtime',
                title: 'Runtime-backed blueprint',
                summary: 'Persisted backend blueprint',
                status: 'generated',
                source: 'runtime/blueprints',
                target_files: ['src/runtime.ts'],
                updated_at: '2026-05-23T08:20:00Z',
                raw: {
                  blueprint_id: 'ce_PM-runtime',
                  task_id: 'PM-runtime',
                  title: 'Runtime-backed blueprint',
                  summary: 'Persisted backend blueprint',
                  target_files: ['src/runtime.ts'],
                },
              },
            ],
          }),
        });
      }
      if (path === '/v2/chief-engineer/diagnostics') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            generated_at: '2026-05-23T08:20:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            blueprints: {
              ok: true,
              status: 'ready',
              source: 'runtime/blueprints',
              total: 1,
              loadable: 1,
              invalid_payloads: 0,
              planned_tasks: 1,
              covered_tasks: 1,
              missing_task_ids: [],
              director_handoff_ready: true,
              latest_updated_at: '2026-05-23T08:20:00Z',
              error: null,
            },
            issues: [],
          }),
        });
      }
      if (path === '/v2/director/tasks?source=auto' || path === '/v2/director/tasks?source=local') {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === '/v2/director/workers') {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === '/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            capabilities: { electron_workbench: ['read_files', 'write_blueprint'] },
          }),
        });
      }
      if (path === '/v2/roles/sessions') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, session: { id: 'ce-session-runtime' } }),
        });
      }
      if (path === '/v2/roles/sessions/ce-session-runtime') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            session: {
              id: 'ce-session-runtime',
              role: 'chief_engineer',
              host_kind: 'electron_workbench',
              attachment_mode: 'isolated',
              state: 'active',
              message_count: 0,
            },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ ok: true, ready: true, configured: true, role: 'chief_engineer' }),
      });
    });
    const tasks: PmTask[] = [
      {
        id: 'PM-runtime',
        title: 'Runtime-backed task',
        summary: 'PM task has no inline blueprint fields',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [],
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={tasks} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/blueprints'));
    expect(screen.queryByTestId('chief-engineer-blueprint-empty')).not.toBeInTheDocument();
    expect(screen.getByText('Runtime-backed blueprint')).toBeInTheDocument();
    expect(screen.getByText('src/runtime.ts')).toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-runtime')).not.toBeInTheDocument();
  });

  it('generates a Chief Engineer blueprint through the backend command route', async () => {
    const tasks: PmTask[] = [
      {
        id: 'PM-summary-only',
        title: '只有摘要的任务',
        summary: '这里不是 Chief Engineer 蓝图',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: ['Director 可追踪'],
        metadata: { target_files: ['src/app.tsx'] },
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={tasks} />);

    fireEvent.click(await screen.findByTestId('chief-engineer-blueprint-generate-PM-summary-only'));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints',
      expect.objectContaining({ method: 'POST' }),
    ));
    const postCall = apiFetchMock.mock.calls.find((call) => call[0] === '/v2/chief-engineer/blueprints' && call[1]?.method === 'POST');
    expect(JSON.parse(String(postCall?.[1]?.body || '{}'))).toMatchObject({
      task_id: 'PM-summary-only',
      objective: '这里不是 Chief Engineer 蓝图',
      context: {
        target_files: ['src/app.tsx'],
        acceptance: ['Director 可追踪'],
      },
    });
    expect(await screen.findByTestId('chief-engineer-blueprint-detail')).toHaveTextContent('Generated Director TaskBoard blueprint');
    expect(screen.getAllByText('ce_PM-summary-only')).toHaveLength(2);
    await waitFor(() => expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('1/1'));
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('ready');
  });

  it('disables blueprint generation when Chief Engineer LLM diagnostics are blocked', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/v2/chief-engineer/diagnostics') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: false,
            role: 'chief_engineer',
            can_handoff: false,
            can_generate: false,
            generated_at: '2026-05-23T08:00:00Z',
            workspace: {
              ok: true,
              status: 'ok',
              workspace: 'C:/Temp/Product',
              exists: true,
              error: null,
            },
            llm: {
              ok: false,
              state: 'blocked',
              role: 'chief_engineer',
              blocked_roles: ['chief_engineer'],
              unsupported_roles: [],
              required_ready_roles: ['chief_engineer'],
              provider_id: 'qwen',
              model: 'Qwen3-Max',
              error: null,
              details: {},
            },
            blueprints: {
              ok: false,
              status: 'empty',
              source: 'runtime/blueprints',
              total: 0,
              loadable: 0,
              invalid_payloads: 0,
              planned_tasks: 1,
              covered_tasks: 0,
              missing_task_ids: ['PM-summary-only'],
              director_handoff_ready: false,
              latest_updated_at: null,
              error: null,
            },
            issues: ['llm_not_ready', 'blueprint_coverage_incomplete'],
            generate_blockers: ['llm_not_ready'],
            handoff_blockers: ['blueprint_coverage_incomplete'],
          }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints') {
        return Promise.resolve({ ok: true, json: async () => ({ blueprints: [], total: 0 }) });
      }
      if (path === '/v2/director/workers') {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === '/v2/director/tasks') {
        return Promise.resolve({ ok: true, json: async () => ({ tasks: [] }) });
      }
      if (path === '/v2/roles/sessions') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, session: { id: 'ce-session-blocked' } }),
        });
      }
      if (path === '/v2/roles/sessions/ce-session-blocked') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            session: {
              id: 'ce-session-blocked',
              role: 'chief_engineer',
              host_kind: 'electron_workbench',
              attachment_mode: 'isolated',
              state: 'active',
              message_count: 0,
            },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ ready: true, configured: true, role: 'chief_engineer', events: [], count: 0 }),
      });
    });
    const tasks: PmTask[] = [
      {
        id: 'PM-summary-only',
        title: '只有摘要的任务',
        summary: '这里不是 Chief Engineer 蓝图',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: ['Director 可追踪'],
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={tasks} />);

    const generate = await screen.findByTestId('chief-engineer-blueprint-generate-PM-summary-only');
    expect(generate).toBeDisabled();
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('blocked · Qwen3-Max');
    expect(screen.getByTestId('chief-engineer-diagnostics-issues')).toHaveTextContent('llm_not_ready');
    expect(apiFetchMock.mock.calls.some((call) => call[0] === '/v2/chief-engineer/blueprints' && call[1]?.method === 'POST')).toBe(false);
  });

  it('checks task blueprint status through the backend query route without generating', async () => {
    const tasks: PmTask[] = [
      {
        id: 'PM-summary-only',
        title: '只有摘要的任务',
        summary: '这里不是 Chief Engineer 蓝图',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: ['Director 可追踪'],
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={tasks} />);

    fireEvent.click(await screen.findByTestId('chief-engineer-blueprint-status-PM-summary-only'));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints/status?task_id=PM-summary-only',
    ));
    expect(await screen.findByText('已有蓝图状态')).toBeInTheDocument();
    expect(screen.getByText('Existing backend blueprint status')).toBeInTheDocument();
    expect(await screen.findByTestId('chief-engineer-blueprint-detail')).toHaveTextContent('src/status.ts');
    expect(screen.queryByTestId('chief-engineer-blueprint-status-result-PM-summary-only')).not.toBeInTheDocument();
    const postCall = apiFetchMock.mock.calls.find((call) => call[0] === '/v2/chief-engineer/blueprints' && call[1]?.method === 'POST');
    expect(postCall).toBeUndefined();
  });

  it('renders real blueprint evidence and director task lifecycle counts', async () => {
    const tasks: PmTask[] = [
      {
        id: 'PM-1',
        title: '实现任务看板',
        subject: 'Director TaskBoard',
        goal: '显示领取状态',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [],
        metadata: {
          blueprint_id: 'bp-001',
          runtime_blueprint_path: 'runtime/contracts/chief_engineer.blueprint.json',
          target_files: ['src/app.tsx'],
        },
      },
      {
        id: 'PM-2',
        title: '执行实现',
        status: TaskStatus.IN_PROGRESS,
        done: false,
        priority: 1,
        acceptance: [],
      },
    ];

    render(
      <ChiefEngineerWorkspace
        {...baseProps}
        tasks={tasks}
        workers={[{ id: 'director-1', status: 'busy', currentTaskId: 'PM-2', tasksCompleted: 1, tasksFailed: 0 }]}
      />,
    );

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/blueprints'));
    expect(screen.queryByTestId('chief-engineer-blueprint-empty')).not.toBeInTheDocument();
    expect(screen.getByText('实现任务看板')).toBeInTheDocument();
    expect(screen.getByText('bp-001')).toBeInTheDocument();
    expect(screen.getByText('runtime/contracts/chief_engineer.blueprint.json')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-blueprint-provenance')).toHaveTextContent('source · runtime_blueprint_path');
    expect(screen.getByText('src/app.tsx')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-director-list')).toHaveTextContent('director-1');
    expect(screen.getByText('未领取')).toBeInTheDocument();
    expect(screen.getByText('执行中')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('chief-engineer-blueprint-open-bp-001'));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/blueprints/bp-001'));
    expect(screen.getByTestId('chief-engineer-blueprint-detail')).toHaveTextContent('Director TaskBoard detail');
    expect(screen.getByTestId('chief-engineer-blueprint-detail')).toHaveTextContent('do not edit target project');
  });

  it('deletes a persisted Chief Engineer blueprint and refreshes diagnostics evidence', async () => {
    let diagnosticsCalls = 0;
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/v2/chief-engineer/blueprints' && !init?.method) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            total: 1,
            blueprints: [
              {
                blueprint_id: 'bp-delete',
                title: 'Blueprint to delete',
                summary: 'Stale blueprint record',
                status: 'generated',
                source: 'runtime/blueprints',
                target_files: ['src/stale.ts'],
                updated_at: '2026-05-24T00:00:00Z',
                raw: { task_id: 'PM-delete' },
              },
            ],
          }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints/bp-delete' && init?.method === 'DELETE') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            blueprint_id: 'bp-delete',
            deleted: true,
            source: 'runtime/blueprints',
          }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints/bp-delete') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            blueprint_id: 'bp-delete',
            source: 'runtime/blueprints',
            blueprint: { summary: 'Stale blueprint detail' },
          }),
        });
      }
      if (path === '/v2/chief-engineer/diagnostics') {
        diagnosticsCalls += 1;
        const hasBlueprint = diagnosticsCalls === 1;
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: hasBlueprint,
            role: 'chief_engineer',
            generated_at: '2026-05-24T00:00:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            blueprints: {
              ok: hasBlueprint,
              status: hasBlueprint ? 'ready' : 'empty',
              source: 'runtime/blueprints',
              total: hasBlueprint ? 1 : 0,
              loadable: hasBlueprint ? 1 : 0,
              invalid_payloads: 0,
              planned_tasks: 0,
              covered_tasks: 0,
              missing_task_ids: [],
              director_handoff_ready: false,
              latest_updated_at: hasBlueprint ? '2026-05-24T00:00:00Z' : null,
              error: null,
            },
            issues: [],
          }),
        });
      }
      if (path === '/v2/director/tasks?source=auto' || path === '/v2/director/tasks?source=local' || path === '/v2/director/workers') {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === '/v2/roles/sessions') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, session: { id: 'ce-delete-session' } }),
        });
      }
      if (path === '/v2/roles/sessions/ce-delete-session') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            session: {
              id: 'ce-delete-session',
              role: 'chief_engineer',
              host_kind: 'electron_workbench',
              attachment_mode: 'isolated',
              state: 'active',
              message_count: 0,
            },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ ok: true, ready: true, configured: true, role: 'chief_engineer' }),
      });
    });

    render(<ChiefEngineerWorkspace {...baseProps} />);

    expect(await screen.findByText('Blueprint to delete')).toBeInTheDocument();
    fireEvent.click(await screen.findByTestId('chief-engineer-blueprint-open-bp-delete'));
    expect(await screen.findByTestId('chief-engineer-blueprint-detail')).toHaveTextContent('Stale blueprint detail');

    fireEvent.click(screen.getByTestId('chief-engineer-blueprint-delete-bp-delete'));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints/bp-delete',
      expect.objectContaining({ method: 'DELETE' }),
    ));
    expect(await screen.findByTestId('chief-engineer-blueprint-delete-evidence')).toHaveTextContent(
      '/v2/chief-engineer/blueprints/bp-delete · deleted',
    );
    await waitFor(() => expect(screen.queryByTestId('chief-engineer-blueprint-delete-bp-delete')).not.toBeInTheDocument());
    expect(screen.getByTestId('chief-engineer-blueprint-detail-empty')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-blueprint-empty')).toHaveTextContent('未发现已落盘的 Chief Engineer 蓝图证据');
    expect(apiFetchMock.mock.calls.filter(([path]) => path === '/v2/chief-engineer/diagnostics')).toHaveLength(2);
  });

  it('toggles Director from Chief Engineer and shows backend status evidence', async () => {
    const onToggleDirector = vi.fn().mockResolvedValue(undefined);
    const tasks: PmTask[] = [
      {
        id: 'PM-ready',
        title: '已具备蓝图的任务',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [],
        metadata: {
          blueprint_id: 'bp-ready',
          runtime_blueprint_path: 'runtime/blueprints/bp-ready.json',
        },
      },
    ];

    render(
      <ChiefEngineerWorkspace
        {...baseProps}
        tasks={tasks}
        onToggleDirector={onToggleDirector}
      />,
    );

    fireEvent.click(screen.getByTestId('chief-engineer-start-director'));

    await waitFor(() => expect(onToggleDirector).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/director/status?source=auto'));
    const statusEvidence = await screen.findByTestId('chief-engineer-director-status-evidence');
    expect(statusEvidence).toHaveTextContent('/v2/director/status?source=auto');
    expect(statusEvidence).toHaveTextContent('running');
    expect(statusEvidence).toHaveTextContent('pid=7242');
    expect(statusEvidence).toHaveTextContent('mode=desktop_service');
    expect(statusEvidence).toHaveTextContent('source=status_file');
  });

  it('locks Chief Engineer Director control while Director is stopping', () => {
    const onToggleDirector = vi.fn();

    render(
      <ChiefEngineerWorkspace
        {...baseProps}
        directorRunning={true}
        isStoppingDirector={true}
        onToggleDirector={onToggleDirector}
      />,
    );

    const startDirector = screen.getByTestId('chief-engineer-start-director');
    expect(startDirector).toBeDisabled();
    expect(startDirector).toHaveTextContent('停止中');
    expect(startDirector).toHaveAttribute('title', 'Director 正在停止，请等待状态回传。');

    fireEvent.click(startDirector);
    expect(onToggleDirector).not.toHaveBeenCalled();
  });

  it('blocks Director start from Chief Engineer when Director LLM readiness is blocked', async () => {
    const onToggleDirector = vi.fn().mockResolvedValue(undefined);
    const tasks: PmTask[] = [
      {
        id: 'PM-ready',
        title: '已具备蓝图的任务',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [],
        metadata: {
          blueprint_id: 'bp-ready',
          runtime_blueprint_path: 'runtime/blueprints/bp-ready.json',
        },
      },
    ];
    const blockedReason = 'LLM 就绪检查未通过：Director 角色当前绑定的 provider/model 没有通过真实测试。';

    render(
      <ChiefEngineerWorkspace
        {...baseProps}
        tasks={tasks}
        directorStartBlockedReason={blockedReason}
        onToggleDirector={onToggleDirector}
      />,
    );

    const gate = screen.getByTestId('chief-engineer-director-start-gate');
    expect(gate).toHaveTextContent(blockedReason);

    const startDirector = screen.getByTestId('chief-engineer-start-director');
    expect(startDirector).toBeDisabled();
    expect(startDirector).toHaveAttribute('title', blockedReason);

    fireEvent.click(startDirector);
    expect(onToggleDirector).not.toHaveBeenCalled();
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/diagnostics'));
  });

  it('uses backend Chief Engineer handoff blockers even when legacy blueprint flag is ready', async () => {
    const onToggleDirector = vi.fn();
    const defaultApiFetch = apiFetchMock.getMockImplementation();
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/v2/chief-engineer/diagnostics') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: false,
            can_handoff: false,
            role: 'chief_engineer',
            generated_at: '2026-05-23T08:00:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            blueprints: {
              ok: false,
              status: 'degraded',
              source: 'runtime/blueprints',
              total: 2,
              loadable: 1,
              invalid_payloads: 1,
              planned_tasks: 0,
              covered_tasks: 0,
              missing_task_ids: [],
              director_handoff_ready: true,
              latest_updated_at: '2026-05-23T08:00:00Z',
              error: null,
            },
            issues: ['blueprint_payload_invalid'],
            handoff_blockers: ['blueprint_payload_invalid'],
          }),
        });
      }
      return defaultApiFetch?.(path, init) ?? Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<ChiefEngineerWorkspace {...baseProps} onToggleDirector={onToggleDirector} tasks={[]} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/diagnostics'));
    const startDirector = screen.getByTestId('chief-engineer-start-director');
    expect(startDirector).toBeDisabled();
    expect(startDirector).toHaveAttribute('title', 'Chief Engineer 交接诊断未通过：存在无效蓝图 payload');
    expect(screen.getByTestId('chief-engineer-director-start-gate')).toHaveTextContent('存在无效蓝图 payload');

    fireEvent.click(startDirector);
    expect(onToggleDirector).not.toHaveBeenCalled();
  });

  it('loads Director workers through the backend route when realtime heartbeats are absent', async () => {
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/v2/director/workers') {
        return Promise.resolve({
          ok: true,
          json: async () => [
            {
              id: 'backend-worker-1',
              name: 'Backend Worker 1',
              status: 'busy',
              current_task_id: 'PM-backend-task',
              tasks_completed: 4,
              tasks_failed: 1,
              healthy: true,
            },
          ],
        });
      }
      if (path === '/v2/chief-engineer/diagnostics') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            generated_at: '2026-05-23T08:00:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            blueprints: {
              ok: true,
              status: 'ready',
              source: 'runtime/blueprints',
              total: 1,
              loadable: 1,
              invalid_payloads: 0,
              planned_tasks: 1,
              covered_tasks: 1,
              missing_task_ids: [],
              director_handoff_ready: true,
              latest_updated_at: '2026-05-23T08:00:00Z',
              error: null,
            },
            issues: [],
          }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ blueprints: [], total: 0 }),
        });
      }
      if (path === '/v2/roles/sessions') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, session: { id: 'ce-session-1' } }),
        });
      }
      if (path === '/v2/roles/sessions/ce-session-1') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            session: {
              id: 'ce-session-1',
              role: 'chief_engineer',
              host_kind: 'electron_workbench',
              attachment_mode: 'isolated',
              state: 'active',
              message_count: 0,
            },
          }),
        });
      }
      if (path === '/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            capabilities: { electron_workbench: ['read_files', 'write_blueprint'] },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ ok: true, ready: true, configured: true, role: 'chief_engineer' }),
      });
    });

    render(<ChiefEngineerWorkspace {...baseProps} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/director/workers'));
    const directorList = await screen.findByTestId('chief-engineer-director-list');
    expect(screen.getByText('/v2/director/workers')).toBeInTheDocument();
    expect(directorList).toHaveTextContent('Backend Worker 1');
    expect(directorList).toHaveTextContent('busy');
    expect(directorList).toHaveTextContent('PM-backend-task');
    expect(directorList).toHaveTextContent('完成 4');
    expect(directorList).toHaveTextContent('失败 1');
  });

  it('counts backend Director task blueprint fields as Chief Engineer handoff evidence', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/v2/director/tasks?source=auto') {
        return Promise.resolve({
          ok: true,
          json: async () => [
            {
              id: 'director-blueprint-backed',
              subject: 'Backend blueprint-backed task',
              status: 'TODO',
              metadata: {
                pm_task_id: 'PM-blueprint',
                blueprint_id: 'bp-backend-task',
                runtime_blueprint_path: 'runtime/blueprints/bp-backend-task.json',
                target_files: ['src/backend-task.ts'],
              },
            },
          ],
        });
      }
      if (path === '/v2/director/tasks?source=local') {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === '/v2/director/workers') {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === '/v2/chief-engineer/diagnostics') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            generated_at: '2026-05-23T08:00:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            blueprints: {
              ok: true,
              status: 'empty',
              source: 'runtime/blueprints',
              total: 0,
              loadable: 0,
              invalid_payloads: 0,
              planned_tasks: 0,
              covered_tasks: 0,
              missing_task_ids: [],
              director_handoff_ready: false,
              latest_updated_at: null,
              error: null,
            },
            issues: [],
          }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints') {
        return Promise.resolve({ ok: true, json: async () => ({ blueprints: [], total: 0 }) });
      }
      if (path === '/v2/roles/sessions') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, session: { id: 'ce-session-1' } }),
        });
      }
      if (path === '/v2/roles/sessions/ce-session-1') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            session: {
              id: 'ce-session-1',
              role: 'chief_engineer',
              host_kind: 'electron_workbench',
              attachment_mode: 'isolated',
              state: 'active',
              message_count: 0,
            },
          }),
        });
      }
      if (path === '/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            capabilities: { electron_workbench: ['read_files', 'write_blueprint'] },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ ok: true, ready: true, configured: true, role: 'chief_engineer' }),
      });
    });

    render(<ChiefEngineerWorkspace {...baseProps} tasks={[]} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/director/tasks?source=auto'));
    expect(screen.queryByTestId('chief-engineer-blueprint-empty')).not.toBeInTheDocument();
    expect(await screen.findByText('Backend blueprint-backed task')).toBeInTheDocument();
    expect(screen.getByText('bp-backend-task')).toBeInTheDocument();
    expect(screen.getByText('runtime/blueprints/bp-backend-task.json')).toBeInTheDocument();
    expect(screen.getByText('src/backend-task.ts')).toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-blueprint')).not.toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-start-director')).not.toBeDisabled();
  });

  it('blocks Director start when only part of the backend task pool has blueprint coverage', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/v2/director/tasks?source=auto') {
        return Promise.resolve({
          ok: true,
          json: async () => [
            {
              id: 'director-covered',
              subject: 'Covered backend task',
              status: 'TODO',
              metadata: {
                pm_task_id: 'PM-covered',
                blueprint_id: 'bp-covered',
                runtime_blueprint_path: 'runtime/blueprints/bp-covered.json',
              },
            },
            {
              id: 'director-missing',
              subject: 'Missing blueprint backend task',
              status: 'TODO',
              metadata: { pm_task_id: 'PM-missing' },
            },
          ],
        });
      }
      if (path === '/v2/director/tasks?source=local') {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === '/v2/director/workers') {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === '/v2/chief-engineer/diagnostics') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: false,
            role: 'chief_engineer',
            generated_at: '2026-05-23T08:00:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            blueprints: {
              ok: false,
              status: 'ready',
              source: 'runtime/blueprints',
              total: 1,
              loadable: 1,
              invalid_payloads: 0,
              planned_tasks: 2,
              covered_tasks: 1,
              missing_task_ids: ['PM-missing'],
              director_handoff_ready: false,
              latest_updated_at: '2026-05-23T08:00:00Z',
              error: null,
            },
            issues: ['blueprint_coverage_incomplete'],
          }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints') {
        return Promise.resolve({ ok: true, json: async () => ({ blueprints: [], total: 0 }) });
      }
      if (path === '/v2/roles/sessions') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, session: { id: 'ce-session-1' } }),
        });
      }
      if (path === '/v2/roles/sessions/ce-session-1') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            session: {
              id: 'ce-session-1',
              role: 'chief_engineer',
              host_kind: 'electron_workbench',
              attachment_mode: 'isolated',
              state: 'active',
              message_count: 0,
            },
          }),
        });
      }
      if (path === '/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            capabilities: { electron_workbench: ['read_files', 'write_blueprint'] },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ ok: true, ready: true, configured: true, role: 'chief_engineer' }),
      });
    });

    render(<ChiefEngineerWorkspace {...baseProps} tasks={[]} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/director/tasks?source=auto'));
    expect(screen.getByText('Covered backend task')).toBeInTheDocument();
    expect(screen.getByText('bp-covered')).toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-covered')).not.toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-blueprint-generate-PM-missing')).toBeInTheDocument();
    expect(screen.getByTitle('1/2')).toBeInTheDocument();
    expect(screen.getByTitle('missing 1')).toBeInTheDocument();
    expect(screen.getByTitle('PM-missing')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-start-director')).toBeDisabled();
  });

  it('blocks Director start from diagnostics even when Director task rows are temporarily empty', async () => {
    const onToggleDirector = vi.fn();
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/v2/director/tasks?source=auto' || path === '/v2/director/tasks?source=local') {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === '/v2/director/workers') {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === '/v2/chief-engineer/diagnostics') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: false,
            role: 'chief_engineer',
            generated_at: '2026-05-23T08:00:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            blueprints: {
              ok: false,
              status: 'empty',
              source: 'runtime/blueprints',
              total: 0,
              loadable: 0,
              invalid_payloads: 0,
              planned_tasks: 2,
              covered_tasks: 0,
              missing_task_ids: ['PM-plan-1', 'PM-plan-2'],
              director_handoff_ready: false,
              latest_updated_at: null,
              error: null,
            },
            issues: ['blueprint_coverage_incomplete'],
          }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints') {
        return Promise.resolve({ ok: true, json: async () => ({ blueprints: [], total: 0 }) });
      }
      if (path === '/v2/roles/sessions') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, session: { id: 'ce-session-1' } }),
        });
      }
      if (path === '/v2/roles/sessions/ce-session-1') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            session: {
              id: 'ce-session-1',
              role: 'chief_engineer',
              host_kind: 'electron_workbench',
              attachment_mode: 'isolated',
              state: 'active',
              message_count: 0,
            },
          }),
        });
      }
      if (path === '/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            capabilities: { electron_workbench: ['read_files', 'write_blueprint'] },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ ok: true, ready: true, configured: true, role: 'chief_engineer' }),
      });
    });

    render(<ChiefEngineerWorkspace {...baseProps} onToggleDirector={onToggleDirector} tasks={[]} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/diagnostics'));
    await waitFor(() => expect(screen.getByTitle('0/2')).toBeInTheDocument());
    expect(screen.getByTitle('PM-plan-1, PM-plan-2')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-diagnostics-issues')).toHaveTextContent('blueprint_coverage_incomplete');
    const startDirector = screen.getByTestId('chief-engineer-start-director');

    expect(startDirector).toBeDisabled();
    expect(startDirector).toHaveAttribute('title', '诊断显示 2 个 PM 任务缺少蓝图证据，不能启动 Director');

    fireEvent.click(startDirector);
    expect(onToggleDirector).not.toHaveBeenCalled();
  });

  it('loads Director task pool metrics through the backend route when runtime tasks are absent', async () => {
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/v2/director/tasks?source=auto') {
        return Promise.resolve({
          ok: true,
          json: async () => [
            {
              id: 'director-backlog-1',
              subject: 'Backend backlog task',
              status: 'TODO',
              metadata: { pm_task_id: 'PM-backlog', director_task_source: 'auto' },
            },
            {
              id: 'director-running-1',
              subject: 'Backend running task',
              status: 'IN_PROGRESS',
              metadata: { pm_task_id: 'PM-running', director_task_source: 'auto' },
            },
          ],
        });
      }
      if (path === '/v2/director/tasks?source=local') {
        return Promise.resolve({
          ok: true,
          json: async () => [
            {
              id: 'director-done-1',
              subject: 'Backend completed task',
              status: 'COMPLETED',
              completed: true,
              metadata: { pm_task_id: 'PM-done', director_task_source: 'local' },
            },
          ],
        });
      }
      if (path === '/v2/director/workers') {
        return Promise.resolve({
          ok: true,
          json: async () => [],
        });
      }
      if (path === '/v2/chief-engineer/diagnostics') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            generated_at: '2026-05-23T08:00:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            blueprints: {
              ok: true,
              status: 'empty',
              source: 'runtime/blueprints',
              total: 0,
              loadable: 0,
              invalid_payloads: 0,
              planned_tasks: 0,
              covered_tasks: 0,
              missing_task_ids: [],
              director_handoff_ready: false,
              latest_updated_at: null,
              error: null,
            },
            issues: [],
          }),
        });
      }
      if (path === '/v2/chief-engineer/blueprints') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ blueprints: [], total: 0 }),
        });
      }
      if (path === '/v2/roles/sessions') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true, session: { id: 'ce-session-1' } }),
        });
      }
      if (path === '/v2/roles/sessions/ce-session-1') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            session: {
              id: 'ce-session-1',
              role: 'chief_engineer',
              host_kind: 'electron_workbench',
              attachment_mode: 'isolated',
              state: 'active',
              message_count: 0,
            },
          }),
        });
      }
      if (path === '/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            capabilities: { electron_workbench: ['read_files', 'write_blueprint'] },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ ok: true, ready: true, configured: true, role: 'chief_engineer' }),
      });
    });

    render(<ChiefEngineerWorkspace {...baseProps} tasks={[]} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/director/tasks?source=auto'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/director/tasks?source=local'));
    const pool = await screen.findByTestId('chief-engineer-director-task-pool');
    expect(pool).toHaveTextContent('/v2/director/tasks');
    expect(screen.getByTestId('chief-engineer-director-task-source')).toHaveTextContent('backend fallback');

    expect(within(pool).getByText('未领取').parentElement).toHaveTextContent('1');
    expect(within(pool).getByText('执行中').parentElement).toHaveTextContent('1');
    expect(within(pool).getByText('完成').parentElement).toHaveTextContent('1');
    expect(within(pool).getByText('总计').parentElement).toHaveTextContent('3');
    expect(screen.getByTestId('chief-engineer-blueprint-generate-PM-backlog')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-blueprint-status-PM-running')).toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-done')).not.toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-start-director')).toBeDisabled();
  });
});
