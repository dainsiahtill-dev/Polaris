import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChiefEngineerWorkspace } from './ChiefEngineerWorkspace';
import { TaskStatus, type PmTask } from '@/types/task';
import { RuntimeTransportProvider } from '@/runtime/transport';

const apiFetchMock = vi.hoisted(() => vi.fn());
const runtimeTransportMock = vi.hoisted(() => ({
  connected: true,
  reconnecting: false,
  error: null as string | null,
  attemptCount: 0,
  subscribeChannels: vi.fn(() => vi.fn()),
  sendCommand: vi.fn(() => true),
  getLastCursor: vi.fn(() => 0),
  reconnect: vi.fn(),
  registerMessageHandler: vi.fn(() => vi.fn()),
}));

vi.mock('@/api', () => ({
  apiFetch: apiFetchMock,
}));

vi.mock('@/runtime/transport', () => ({
  RuntimeTransportProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useRuntimeTransport: () => runtimeTransportMock,
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

const CE_WORKSPACE_QUERY = 'workspace=C%3A%2FTemp%2FProduct';

function cePath(path: string): string {
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}${CE_WORKSPACE_QUERY}`;
}

function directorPath(path: string): string {
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}${CE_WORKSPACE_QUERY}`;
}

describe('ChiefEngineerWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let diagnosticsCalls = 0;
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === cePath('/v2/chief-engineer/blueprints') && init?.method === 'POST') {
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
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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
              plan_status: hasBlueprint ? 'ready' : 'empty',
              plan_path: 'C:/Temp/Product/.polaris/runtime/tasks/plan.json',
              plan_error: hasBlueprint ? null : 'pm_task_plan_empty',
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
            issues: hasBlueprint ? [] : ['blueprint_task_plan_empty'],
            generate_blockers: [],
            handoff_blockers: hasBlueprint ? [] : ['blueprint_task_plan_empty'],
          }),
        });
      }
      if (path === cePath('/v2/chief-engineer/blueprints/status?task_id=PM-summary-only')) {
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
      if (path === cePath('/v2/chief-engineer/blueprints')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ blueprints: [], total: 0 }),
        });
      }
      if (path === cePath('/v2/chief-engineer/blueprints/bp-001')) {
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
      if (path === directorPath('/v2/director/workers')) {
        return Promise.resolve({
          ok: true,
          json: async () => [],
        });
      }
      if (path === directorPath('/v2/director/status?source=auto')) {
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
      if (path === cePath('/v2/chief-engineer/llm-events?limit=5')) {
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

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/blueprints')));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/diagnostics')));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/llm-events?limit=5')));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/cache-stats'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/token-budget-stats'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/role/chief_engineer/chat/status?workspace=C%3A%2FTemp%2FProduct'));
    expect(screen.getByTestId('chief-engineer-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-dialogue')).toBeInTheDocument();
    const backendStrip = screen.getByTestId('chief-engineer-backend-strip');
    expect(backendStrip).not.toHaveTextContent('/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench');
    expect(screen.getByTestId('chief-engineer-capabilities-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench',
    );
    expect(backendStrip).toHaveTextContent('read_files, write_blueprint');
    expect(screen.getByTestId('chief-engineer-llm-events-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/chief-engineer/llm-events?limit=5&workspace=C%3A%2FTemp%2FProduct',
    );
    expect(backendStrip).toHaveTextContent('events=1 · llm_call_start · gpt-test · 321 tokens');
    expect(screen.getByTestId('chief-engineer-cache-endpoint')).toHaveAttribute('data-endpoint', '/v2/chief-engineer/cache-stats');
    expect(backendStrip).toHaveTextContent('hits=4 · misses=1 · size=5 · hit=80%');
    expect(screen.getByTestId('chief-engineer-token-budget-endpoint')).toHaveAttribute('data-endpoint', '/v2/chief-engineer/token-budget-stats');
    expect(backendStrip).toHaveTextContent('total=12000 · available=6000 · used=2048');
    fireEvent.click(screen.getByTestId('chief-engineer-kernel-cache-clear'));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/v2/chief-engineer/cache-clear', { method: 'POST' }));
    const clearResult = await screen.findByTestId('chief-engineer-kernel-cache-clear-result');
    expect(clearResult).toHaveAttribute('data-endpoint', '/v2/chief-engineer/cache-clear');
    expect(clearResult).toHaveTextContent('Cache cleared');
    expect(screen.getByTestId('chief-engineer-diagnostics-status')).toHaveTextContent('degraded');
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('ready · Qwen3-Max');
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('0/0');
    expect(screen.getByTestId('chief-engineer-diagnostics-issues')).toHaveTextContent('blueprint_task_plan_empty');
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

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/blueprints')));
    const activity = screen.getByTestId('chief-engineer-runtime-activity');
    expect(activity).toHaveTextContent('调用 LLM');
    expect(activity).toHaveTextContent('2 条记录');
    expect(activity).toHaveTextContent('Reviewing blueprint handoff constraints');
  });

  it('exports Chief Engineer dialogue RoleSession to the Director workflow contract', async () => {
    render(<ChiefEngineerWorkspace {...baseProps} />);

    const exportButton = await screen.findByTestId('ai-role-session-export');
    await waitFor(() => expect(exportButton).not.toBeDisabled());
    expect(exportButton).toHaveAttribute('aria-label', '导出当前 RoleSession 到 director 工作流');
    expect(exportButton).toHaveAttribute('title', '导出当前 RoleSession 到 director 工作流');

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

    render(
      <RuntimeTransportProvider autoConnect={false}>
        <ChiefEngineerWorkspace {...baseProps} tasks={tasks} />
      </RuntimeTransportProvider>,
    );

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/blueprints')));
    expect(screen.getByTestId('chief-engineer-blueprint-empty')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-start-director')).toBeDisabled();
  });

  it('does not list a PM task as pending when a runtime blueprint matches raw task_id', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === cePath('/v2/chief-engineer/blueprints')) {
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
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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
      if (path === directorPath('/v2/director/tasks?source=auto') || path === directorPath('/v2/director/tasks?source=local')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === directorPath('/v2/director/workers')) {
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

    render(
      <RuntimeTransportProvider autoConnect={false}>
        <ChiefEngineerWorkspace {...baseProps} tasks={tasks} />
      </RuntimeTransportProvider>,
    );

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/blueprints')));
    expect(screen.queryByTestId('chief-engineer-blueprint-empty')).not.toBeInTheDocument();
    expect(screen.getByText('Runtime-backed blueprint')).toBeInTheDocument();
    expect(screen.getByText('src/runtime.ts')).toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-runtime')).not.toBeInTheDocument();
  });

  it('does not list numeric PM tasks as pending when runtime evidence exists even if diagnostics are stale', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === cePath('/v2/chief-engineer/blueprints')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            total: 3,
            blueprints: [
              {
                blueprint_id: 'ce_TASK-1_20260618070900420840',
                task_id: 'TASK-1',
                title: '实现创建语义化 HTML5 简历结构',
                summary: 'Chief Engineer blueprint for TASK-1',
                status: 'generated',
                source: 'runtime/blueprints',
                target_files: ['src/html5', 'tests'],
                updated_at: '2026-06-18T07:09:00Z',
              },
              {
                blueprint_id: 'ce_TASK-2_20260618070900430145',
                title: '实现响应式 CSS3 样式表',
                summary: 'Chief Engineer blueprint for TASK-2',
                status: 'generated',
                source: 'runtime/blueprints',
                target_files: ['src/css3', 'tests'],
                updated_at: '2026-06-18T07:09:00Z',
              },
              {
                blueprint_id: 'ce_TASK-3_20260618070900438769',
                title: '交付验证与 README 编写',
                summary: 'Chief Engineer blueprint for TASK-3',
                status: 'generated',
                source: 'runtime/blueprints',
                target_files: ['src/readme', 'tests'],
                updated_at: '2026-06-18T07:09:00Z',
              },
            ],
          }),
        });
      }
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            role: 'chief_engineer',
            can_handoff: true,
            can_generate: true,
            generated_at: '2026-06-18T07:09:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
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
              status: 'ready',
              source: 'runtime/blueprints',
              plan_status: 'ready',
              plan_path: 'C:/Temp/Product/.polaris/runtime/tasks/plan.json',
              plan_error: null,
              total: 3,
              loadable: 3,
              invalid_payloads: 0,
              planned_tasks: 3,
              covered_tasks: 0,
              missing_task_ids: ['TASK-1', 'TASK-2', 'TASK-3'],
              director_handoff_ready: false,
              latest_updated_at: '2026-06-18T07:09:00Z',
              error: null,
            },
            issues: ['blueprint_coverage_incomplete'],
            generate_blockers: [],
            handoff_blockers: ['blueprint_coverage_incomplete'],
          }),
        });
      }
      if (path === directorPath('/v2/director/tasks?source=auto') || path === directorPath('/v2/director/tasks?source=local')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === directorPath('/v2/director/workers')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ ok: true, ready: true, configured: true, role: 'chief_engineer' }),
      });
    });

    const tasks: PmTask[] = [
      {
        id: 1 as unknown as string,
        title: '实现创建语义化 HTML5 简历结构',
        summary: 'PM task has no inline blueprint fields',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [],
      },
      {
        id: 2 as unknown as string,
        title: '实现响应式 CSS3 样式表',
        summary: 'PM task has no inline blueprint fields',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [],
      },
      {
        id: 3 as unknown as string,
        title: '交付验证与 README 编写',
        summary: 'PM task has no inline blueprint fields',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [],
      },
    ];

    render(
      <RuntimeTransportProvider autoConnect={false}>
        <ChiefEngineerWorkspace {...baseProps} tasks={tasks} />
      </RuntimeTransportProvider>,
    );

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/blueprints')));
    expect(screen.getByText('Chief Engineer blueprint for TASK-1')).toBeInTheDocument();
    expect(screen.getByText('Chief Engineer blueprint for TASK-2')).toBeInTheDocument();
    expect(screen.getByText('Chief Engineer blueprint for TASK-3')).toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-2')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-3')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-candidates')).not.toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('3/3');
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('none');
    expect(screen.getByTestId('chief-engineer-diagnostics')).not.toHaveTextContent('TASK-1');
    expect(screen.queryByTestId('chief-engineer-diagnostics-issues')).not.toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-start-director')).not.toBeDisabled();

    fireEvent.click(screen.getByTestId('chief-engineer-toggle-workbench'));
    expect(await screen.findByTestId('chief-engineer-workbench-panel-mock')).toHaveTextContent('missingBlueprintCount=0');
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
        acceptance: [{ description: 'Director 可追踪' }],
        acceptance_criteria: ['Director 可追踪'],
        execution_checklist: ['生成蓝图', '校验交接字段'],
        metadata: { target_files: ['src/app.tsx'] },
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={tasks} />);

    fireEvent.click(await screen.findByTestId('chief-engineer-blueprint-generate-PM-summary-only'));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(
      cePath('/v2/chief-engineer/blueprints'),
      expect.objectContaining({ method: 'POST' }),
    ));
    const postCall = apiFetchMock.mock.calls.find((call) => call[0] === cePath('/v2/chief-engineer/blueprints') && call[1]?.method === 'POST');
    expect(JSON.parse(String(postCall?.[1]?.body || '{}'))).toMatchObject({
      task_id: 'PM-summary-only',
      objective: '这里不是 Chief Engineer 蓝图',
      context: {
        target_files: ['src/app.tsx'],
        acceptance_criteria: ['Director 可追踪'],
        acceptance: ['Director 可追踪'],
        execution_checklist: ['生成蓝图', '校验交接字段'],
        steps: ['生成蓝图', '校验交接字段'],
        task: expect.objectContaining({
          id: 'PM-summary-only',
          acceptance_criteria: ['Director 可追踪'],
          execution_checklist: ['生成蓝图', '校验交接字段'],
        }),
      },
    });
    expect(await screen.findByTestId('chief-engineer-blueprint-detail')).toHaveTextContent('Generated Director TaskBoard blueprint');
    expect(screen.getAllByText('ce_PM-summary-only')).toHaveLength(2);
    await waitFor(() => expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('1/1'));
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('ready');
  });

  it('bulk generates missing Chief Engineer blueprints before Director handoff', async () => {
    const defaultApiFetch = apiFetchMock.getMockImplementation();
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === cePath('/v2/chief-engineer/blueprints/bulk') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            workspace: 'C:/Temp/Product',
            total: 2,
            generated: 2,
            failed: 0,
            results: [
              {
                ok: true,
                task_id: 'PM-bulk-1',
                workspace: 'C:/Temp/Product',
                status: 'generated',
                blueprint_id: 'ce_PM-bulk-1',
                blueprint_path: 'runtime/blueprints/ce_PM-bulk-1.json',
                source: 'runtime/blueprints',
                summary: 'Bulk generated blueprint one',
                recommendations: [],
                risks: [],
                blueprint: {
                  blueprint_id: 'ce_PM-bulk-1',
                  task_id: 'PM-bulk-1',
                  title: 'Bulk blueprint one',
                  summary: 'Bulk generated blueprint one',
                  status: 'generated',
                  target_files: ['src/one.tsx'],
                },
              },
              {
                ok: true,
                task_id: 'PM-bulk-2',
                workspace: 'C:/Temp/Product',
                status: 'generated',
                blueprint_id: 'ce_PM-bulk-2',
                blueprint_path: 'runtime/blueprints/ce_PM-bulk-2.json',
                source: 'runtime/blueprints',
                summary: 'Bulk generated blueprint two',
                recommendations: [],
                risks: [],
                blueprint: {
                  blueprint_id: 'ce_PM-bulk-2',
                  task_id: 'PM-bulk-2',
                  title: 'Bulk blueprint two',
                  summary: 'Bulk generated blueprint two',
                  status: 'generated',
                  target_files: ['src/two.tsx'],
                },
              },
            ],
            errors: [],
          }),
        });
      }
      return defaultApiFetch
        ? defaultApiFetch(path, init)
        : Promise.resolve({ ok: true, json: async () => ({ ready: true }) });
    });
    const tasks: PmTask[] = [
      {
        id: 'PM-bulk-1',
        title: 'Bulk source one',
        summary: 'Generate first blueprint',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [{ description: 'first acceptance' }],
        acceptance_criteria: ['first acceptance'],
        execution_checklist: ['first step'],
        metadata: { target_files: ['src/one.tsx'] },
      },
      {
        id: 'PM-bulk-2',
        title: 'Bulk source two',
        summary: 'Generate second blueprint',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [{ description: 'second acceptance' }],
        acceptance_criteria: ['second acceptance'],
        execution_checklist: ['second step'],
        metadata: { target_files: ['src/two.tsx'] },
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={tasks} />);

    fireEvent.click(await screen.findByTestId('chief-engineer-blueprint-generate-all'));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(
      cePath('/v2/chief-engineer/blueprints/bulk'),
      expect.objectContaining({ method: 'POST' }),
    ));
    const postCall = apiFetchMock.mock.calls.find((call) => (
      call[0] === cePath('/v2/chief-engineer/blueprints/bulk') && call[1]?.method === 'POST'
    ));
    const body = JSON.parse(String(postCall?.[1]?.body || '{}'));
    expect(body).toMatchObject({
      stop_on_error: false,
      tasks: [
        {
          task_id: 'PM-bulk-1',
          objective: 'Generate first blueprint',
          context: {
            target_files: ['src/one.tsx'],
            acceptance_criteria: ['first acceptance'],
            acceptance: ['first acceptance'],
            execution_checklist: ['first step'],
          },
        },
        {
          task_id: 'PM-bulk-2',
          objective: 'Generate second blueprint',
          context: {
            target_files: ['src/two.tsx'],
            acceptance_criteria: ['second acceptance'],
            acceptance: ['second acceptance'],
            execution_checklist: ['second step'],
          },
        },
      ],
    });
    expect(await screen.findByTestId('chief-engineer-blueprint-bulk-evidence')).toHaveTextContent(
      '/v2/chief-engineer/blueprints/bulk?workspace=C%3A%2FTemp%2FProduct · generated 2/2',
    );
    expect(screen.getByText('Bulk blueprint one')).toBeInTheDocument();
    expect(screen.getByText('Bulk blueprint two')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-blueprint-detail')).toHaveTextContent('Bulk generated blueprint one');
  });

  it('allows diagnostics to regenerate tasks with stale blueprint references', async () => {
    const defaultApiFetch = apiFetchMock.getMockImplementation();
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: false,
            role: 'chief_engineer',
            can_handoff: false,
            can_generate: true,
            generated_at: '2026-05-23T08:00:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            blueprints: {
              ok: true,
              status: 'ready',
              source: 'runtime/blueprints',
              total: 1,
              loadable: 1,
              invalid_payloads: 1,
              planned_tasks: 1,
              covered_tasks: 0,
              missing_task_ids: ['PM-stale-blueprint'],
              director_handoff_ready: false,
              latest_updated_at: '2026-05-23T08:00:00Z',
              error: null,
            },
            issues: ['blueprint_coverage_incomplete'],
            generate_blockers: [],
            handoff_blockers: ['blueprint_coverage_incomplete'],
          }),
        });
      }
      if (path === cePath('/v2/chief-engineer/blueprints/bulk') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            workspace: 'C:/Temp/Product',
            total: 1,
            generated: 1,
            failed: 0,
            results: [
              {
                ok: true,
                task_id: 'PM-stale-blueprint',
                workspace: 'C:/Temp/Product',
                status: 'generated',
                blueprint_id: 'ce_PM-stale-blueprint',
                blueprint_path: 'runtime/blueprints/ce_PM-stale-blueprint.json',
                source: 'runtime/blueprints',
                summary: 'Regenerated stale blueprint',
                recommendations: [],
                risks: [],
                blueprint: {
                  blueprint_id: 'ce_PM-stale-blueprint',
                  task_id: 'PM-stale-blueprint',
                  title: 'Regenerated stale blueprint',
                  summary: 'Regenerated stale blueprint',
                  status: 'generated',
                  target_files: ['src/stale.ts'],
                },
              },
            ],
            errors: [],
          }),
        });
      }
      return defaultApiFetch
        ? defaultApiFetch(path, init)
        : Promise.resolve({ ok: true, json: async () => ({ ready: true }) });
    });
    const tasks: PmTask[] = [
      {
        id: 'PM-stale-blueprint',
        title: 'Stale blueprint source',
        summary: 'Regenerate stale blueprint',
        status: TaskStatus.PENDING,
        done: false,
        priority: 1,
        acceptance: [{ description: 'stale acceptance' }],
        acceptance_criteria: ['stale acceptance'],
        execution_checklist: ['stale step'],
        metadata: {
          target_files: ['src/stale.ts'],
          blueprint_id: 'old-hollow-blueprint',
          runtime_blueprint_path: 'runtime/blueprints/old-hollow-blueprint.json',
        },
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={tasks} />);

    const generateAll = await screen.findByTestId('chief-engineer-blueprint-generate-all');
    expect(generateAll).not.toBeDisabled();
    fireEvent.click(generateAll);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(
      cePath('/v2/chief-engineer/blueprints/bulk'),
      expect.objectContaining({ method: 'POST' }),
    ));
    const postCall = apiFetchMock.mock.calls.find((call) => (
      call[0] === cePath('/v2/chief-engineer/blueprints/bulk') && call[1]?.method === 'POST'
    ));
    const body = JSON.parse(String(postCall?.[1]?.body || '{}'));
    expect(body.tasks).toHaveLength(1);
    expect(body.tasks[0]).toMatchObject({
      task_id: 'PM-stale-blueprint',
      objective: 'Regenerate stale blueprint',
      context: {
        target_files: ['src/stale.ts'],
        acceptance_criteria: ['stale acceptance'],
        execution_checklist: ['stale step'],
      },
    });
    expect(await screen.findByTestId('chief-engineer-blueprint-bulk-evidence')).toHaveTextContent('generated 1/1');
  });

  it('disables blueprint generation when Chief Engineer LLM diagnostics are blocked', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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
      if (path === cePath('/v2/chief-engineer/blueprints')) {
        return Promise.resolve({ ok: true, json: async () => ({ blueprints: [], total: 0 }) });
      }
      if (path === directorPath('/v2/director/workers')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === directorPath('/v2/director/tasks')) {
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
    expect(apiFetchMock.mock.calls.some((call) => call[0] === cePath('/v2/chief-engineer/blueprints') && call[1]?.method === 'POST')).toBe(false);
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
      cePath('/v2/chief-engineer/blueprints/status?task_id=PM-summary-only'),
    ));
    expect(await screen.findByText('已有蓝图状态')).toBeInTheDocument();
    expect(screen.getByText('Existing backend blueprint status')).toBeInTheDocument();
    expect(await screen.findByTestId('chief-engineer-blueprint-detail')).toHaveTextContent('src/status.ts');
    expect(screen.queryByTestId('chief-engineer-blueprint-status-result-PM-summary-only')).not.toBeInTheDocument();
    const postCall = apiFetchMock.mock.calls.find((call) => call[0] === cePath('/v2/chief-engineer/blueprints') && call[1]?.method === 'POST');
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

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/blueprints')));
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

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/blueprints/bp-001')));
    expect(screen.getByTestId('chief-engineer-blueprint-detail')).toHaveTextContent('Director TaskBoard detail');
    expect(screen.getByTestId('chief-engineer-blueprint-detail')).toHaveTextContent('do not edit target project');
  });

  it('deletes a persisted Chief Engineer blueprint and refreshes diagnostics evidence', async () => {
    let diagnosticsCalls = 0;
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === cePath('/v2/chief-engineer/blueprints') && !init?.method) {
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
      if (path === cePath('/v2/chief-engineer/blueprints/bp-delete') && init?.method === 'DELETE') {
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
      if (path === cePath('/v2/chief-engineer/blueprints/bp-delete')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            blueprint_id: 'bp-delete',
            source: 'runtime/blueprints',
            blueprint: { summary: 'Stale blueprint detail' },
          }),
        });
      }
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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
      if (
        path === directorPath('/v2/director/tasks?source=auto')
        || path === directorPath('/v2/director/tasks?source=local')
        || path === directorPath('/v2/director/workers')
      ) {
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
      cePath('/v2/chief-engineer/blueprints/bp-delete'),
      expect.objectContaining({ method: 'DELETE' }),
    ));
    expect(await screen.findByTestId('chief-engineer-blueprint-delete-evidence')).toHaveTextContent(
      '/v2/chief-engineer/blueprints/bp-delete?workspace=C%3A%2FTemp%2FProduct · deleted',
    );
    await waitFor(() => expect(screen.queryByTestId('chief-engineer-blueprint-delete-bp-delete')).not.toBeInTheDocument());
    expect(screen.getByTestId('chief-engineer-blueprint-detail-empty')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-blueprint-empty')).toHaveTextContent('未发现已落盘的 Chief Engineer 蓝图证据');
    expect(apiFetchMock.mock.calls.filter(([path]) => path === cePath('/v2/chief-engineer/diagnostics'))).toHaveLength(2);
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
    expect(apiFetchMock).not.toHaveBeenCalledWith(directorPath('/v2/director/status?source=auto'));
    const statusEvidence = await screen.findByTestId('chief-engineer-director-status-evidence');
    expect(statusEvidence).not.toHaveTextContent('/v2/director/status?source=auto');
    expect(screen.getByTestId('chief-engineer-director-status-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/ws/runtime',
    );
    expect(statusEvidence).toHaveTextContent('等待 runtime.v2 推送确认');
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
    const blockedReason =
      'LLM 就绪检查未通过：Director 角色当前绑定的 provider/model 没有通过真实测试，请先在 LLM 设置中重新测试并保存。';

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
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/diagnostics')));
  });

  it('uses backend Chief Engineer handoff blockers even when legacy blueprint flag is ready', async () => {
    const onToggleDirector = vi.fn();
    const defaultApiFetch = apiFetchMock.getMockImplementation();
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/diagnostics')));
    const startDirector = screen.getByTestId('chief-engineer-start-director');
    expect(startDirector).toBeDisabled();
    expect(startDirector).toHaveAttribute('title', 'Chief Engineer 交接诊断未通过：存在无效蓝图 payload');
    expect(screen.getByTestId('chief-engineer-director-start-gate')).toHaveTextContent('存在无效蓝图 payload');

    fireEvent.click(startDirector);
    expect(onToggleDirector).not.toHaveBeenCalled();
  });

  it('blocks Director handoff when Chief Engineer diagnostics cannot read the PM task plan', async () => {
    const onToggleDirector = vi.fn();
    const defaultApiFetch = apiFetchMock.getMockImplementation();
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: false,
            can_handoff: false,
            can_generate: true,
            role: 'chief_engineer',
            generated_at: '2026-05-25T00:00:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            llm: { ok: true, state: 'ready', role: 'chief_engineer', blocked_roles: [], unsupported_roles: [], required_ready_roles: ['chief_engineer'], provider_id: 'qwen', model: 'Qwen3-Max', error: null, details: {} },
            blueprints: {
              ok: false,
              status: 'ready',
              source: 'runtime/blueprints',
              plan_status: 'missing',
              plan_path: 'C:/Temp/Product/.polaris/runtime/tasks/plan.json',
              plan_error: 'pm_task_plan_missing',
              total: 1,
              loadable: 1,
              invalid_payloads: 0,
              planned_tasks: 0,
              covered_tasks: 0,
              missing_task_ids: [],
              director_handoff_ready: false,
              latest_updated_at: '2026-05-25T00:00:00Z',
              error: null,
            },
            issues: ['blueprint_task_plan_unavailable'],
            generate_blockers: [],
            handoff_blockers: ['blueprint_task_plan_unavailable'],
          }),
        });
      }
      return defaultApiFetch?.(path, init) ?? Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<ChiefEngineerWorkspace {...baseProps} onToggleDirector={onToggleDirector} tasks={[]} />);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/diagnostics')));
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('missing');
    expect(screen.getByTestId('chief-engineer-diagnostics-issues')).toHaveTextContent('blueprint_task_plan_unavailable');
    const startDirector = screen.getByTestId('chief-engineer-start-director');
    expect(startDirector).toBeDisabled();
    expect(startDirector).toHaveAttribute('title', 'Chief Engineer 缺少可审计的 PM 任务计划，不能启动 Director');
    fireEvent.click(startDirector);
    expect(onToggleDirector).not.toHaveBeenCalled();
  });

  it('allows Director handoff when snapshot tasks carry blueprint evidence despite stale plan diagnostics', async () => {
    const defaultApiFetch = apiFetchMock.getMockImplementation();
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: false,
            can_handoff: false,
            can_generate: true,
            role: 'chief_engineer',
            generated_at: '2026-05-25T00:00:00Z',
            workspace: { ok: true, status: 'ok', workspace: 'C:/Temp/Product', exists: true, error: null },
            llm: { ok: true, state: 'ready', role: 'chief_engineer', blocked_roles: [], unsupported_roles: [], required_ready_roles: ['chief_engineer'], provider_id: 'qwen', model: 'Qwen3-Max', error: null, details: {} },
            blueprints: {
              ok: false,
              status: 'ready',
              source: 'runtime/blueprints',
              plan_status: 'missing',
              plan_path: 'C:/Temp/Product/.polaris/runtime/tasks/plan.json',
              plan_error: 'pm_task_plan_missing',
              total: 3,
              loadable: 3,
              invalid_payloads: 0,
              planned_tasks: 0,
              covered_tasks: 0,
              missing_task_ids: [],
              director_handoff_ready: false,
              latest_updated_at: '2026-05-25T00:00:00Z',
              error: null,
            },
            issues: ['blueprint_task_plan_unavailable'],
            generate_blockers: [],
            handoff_blockers: ['blueprint_task_plan_unavailable'],
          }),
        });
      }
      if (path === cePath('/v2/chief-engineer/blueprints')) {
        return Promise.resolve({ ok: true, json: async () => ({ blueprints: [], total: 0 }) });
      }
      return defaultApiFetch?.(path, init) ?? Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(
      <ChiefEngineerWorkspace
        {...baseProps}
        tasks={[
          { id: 'PM-alpha', title: 'Alpha task', blueprint_id: 'bp-alpha', runtime_blueprint_path: 'runtime/blueprints/bp-alpha.json' } as PmTask,
          { id: 'PM-beta', title: 'Beta task', blueprint_id: 'bp-beta', runtime_blueprint_path: 'runtime/blueprints/bp-beta.json' } as PmTask,
          { id: 'PM-gamma', title: 'Gamma task', blueprint_id: 'bp-gamma', runtime_blueprint_path: 'runtime/blueprints/bp-gamma.json' } as PmTask,
        ]}
      />,
    );

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/diagnostics')));
    expect(screen.queryByTestId('chief-engineer-director-start-gate')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-diagnostics-issues')).not.toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-diagnostics')).toHaveTextContent('3/3');
    expect(screen.getByTestId('chief-engineer-start-director')).not.toBeDisabled();
  });

  it('loads Director workers through the backend route when realtime heartbeats are absent', async () => {
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === directorPath('/v2/director/workers')) {
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
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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
      if (path === cePath('/v2/chief-engineer/blueprints')) {
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

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(directorPath('/v2/director/workers')));
    const directorList = await screen.findByTestId('chief-engineer-director-list');
    expect(screen.getByText('/v2/director/workers')).toBeInTheDocument();
    expect(directorList).toHaveTextContent('Backend Worker 1');
    expect(directorList).toHaveTextContent('busy');
    expect(directorList).toHaveTextContent('PM-backend-task');
    expect(directorList).toHaveTextContent('完成 4');
    expect(directorList).toHaveTextContent('失败 1');
  });

  it('counts runtime Director task blueprint fields as Chief Engineer handoff evidence', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === directorPath('/v2/director/tasks?source=auto')) {
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
      if (path === directorPath('/v2/director/tasks?source=local')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === directorPath('/v2/director/workers')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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
      if (path === cePath('/v2/chief-engineer/blueprints')) {
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

    const runtimeTasks: PmTask[] = [
      {
        id: 'director-blueprint-backed',
        title: 'Backend blueprint-backed task',
        subject: 'Backend blueprint-backed task',
        status: TaskStatus.PENDING,
        metadata: {
          pm_task_id: 'PM-blueprint',
          blueprint_id: 'bp-backend-task',
          runtime_blueprint_path: 'runtime/blueprints/bp-backend-task.json',
          target_files: ['src/backend-task.ts'],
        },
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={runtimeTasks} />);

    expect(apiFetchMock).not.toHaveBeenCalledWith(directorPath('/v2/director/tasks?source=auto'));
    expect(screen.queryByTestId('chief-engineer-blueprint-empty')).not.toBeInTheDocument();
    expect(await screen.findByText('Backend blueprint-backed task')).toBeInTheDocument();
    expect(screen.getByText('bp-backend-task')).toBeInTheDocument();
    expect(screen.getByText('runtime/blueprints/bp-backend-task.json')).toBeInTheDocument();
    expect(screen.getByText('src/backend-task.ts')).toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-blueprint')).not.toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-start-director')).not.toBeDisabled();
  });

  it('does not show pending generation when task contracts already carry blueprint evidence despite stale diagnostics', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === directorPath('/v2/director/tasks?source=auto')) {
        return Promise.resolve({
          ok: true,
          json: async () => [
            {
              id: 'director-alpha',
              subject: 'Alpha covered task',
              status: 'TODO',
              metadata: {
                pm_task_id: 'PM-alpha',
                blueprint_id: 'bp-alpha',
                runtime_blueprint_path: 'runtime/blueprints/bp-alpha.json',
              },
            },
            {
              id: 'director-beta',
              subject: 'Beta covered task',
              status: 'TODO',
              metadata: {
                pm_task_id: 'PM-beta',
                blueprint_id: 'bp-beta',
                runtime_blueprint_path: 'runtime/blueprints/bp-beta.json',
              },
            },
            {
              id: 'director-gamma',
              subject: 'Gamma covered task',
              status: 'TODO',
              metadata: {
                pm_task_id: 'PM-gamma',
                blueprint_id: 'bp-gamma',
                runtime_blueprint_path: 'runtime/blueprints/bp-gamma.json',
              },
            },
          ],
        });
      }
      if (path === directorPath('/v2/director/tasks?source=local')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === directorPath('/v2/director/workers')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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
              total: 0,
              loadable: 0,
              invalid_payloads: 0,
              planned_tasks: 3,
              covered_tasks: 0,
              missing_task_ids: ['PM-alpha', 'PM-beta', 'PM-gamma'],
              director_handoff_ready: false,
              latest_updated_at: null,
              error: null,
            },
            issues: ['blueprint_coverage_incomplete'],
            handoff_blockers: ['blueprint_coverage_incomplete'],
          }),
        });
      }
      if (path === cePath('/v2/chief-engineer/blueprints')) {
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

    const runtimeTasks: PmTask[] = [
      {
        id: 'director-alpha',
        title: 'Alpha covered task',
        subject: 'Alpha covered task',
        status: TaskStatus.PENDING,
        metadata: {
          pm_task_id: 'PM-alpha',
          blueprint_id: 'bp-alpha',
          runtime_blueprint_path: 'runtime/blueprints/bp-alpha.json',
        },
      },
      {
        id: 'director-beta',
        title: 'Beta covered task',
        subject: 'Beta covered task',
        status: TaskStatus.PENDING,
        metadata: {
          pm_task_id: 'PM-beta',
          blueprint_id: 'bp-beta',
          runtime_blueprint_path: 'runtime/blueprints/bp-beta.json',
        },
      },
      {
        id: 'director-gamma',
        title: 'Gamma covered task',
        subject: 'Gamma covered task',
        status: TaskStatus.PENDING,
        metadata: {
          pm_task_id: 'PM-gamma',
          blueprint_id: 'bp-gamma',
          runtime_blueprint_path: 'runtime/blueprints/bp-gamma.json',
        },
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={runtimeTasks} />);

    expect(apiFetchMock).not.toHaveBeenCalledWith(directorPath('/v2/director/tasks?source=auto'));
    expect(await screen.findAllByText('Alpha covered task')).not.toHaveLength(0);
    expect(screen.getAllByText('Beta covered task')).not.toHaveLength(0);
    expect(screen.getAllByText('Gamma covered task')).not.toHaveLength(0);
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-alpha')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-beta')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-gamma')).not.toBeInTheDocument();
    expect(screen.getByTitle('3/3')).toBeInTheDocument();
    expect(screen.getByTitle('none')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-start-director')).not.toBeDisabled();
  });

  it('blocks Director start when only part of the runtime task pool has blueprint coverage', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === directorPath('/v2/director/tasks?source=auto')) {
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
      if (path === directorPath('/v2/director/tasks?source=local')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === directorPath('/v2/director/workers')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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
      if (path === cePath('/v2/chief-engineer/blueprints')) {
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

    const runtimeTasks: PmTask[] = [
      {
        id: 'director-covered',
        title: 'Covered backend task',
        subject: 'Covered backend task',
        status: TaskStatus.PENDING,
        metadata: {
          pm_task_id: 'PM-covered',
          blueprint_id: 'bp-covered',
          runtime_blueprint_path: 'runtime/blueprints/bp-covered.json',
        },
      },
      {
        id: 'director-missing',
        title: 'Missing blueprint backend task',
        subject: 'Missing blueprint backend task',
        status: TaskStatus.PENDING,
        metadata: { pm_task_id: 'PM-missing' },
      },
    ];

    render(<ChiefEngineerWorkspace {...baseProps} tasks={runtimeTasks} />);

    expect(apiFetchMock).not.toHaveBeenCalledWith(directorPath('/v2/director/tasks?source=auto'));
    expect(screen.getByText('Covered backend task')).toBeInTheDocument();
    expect(screen.getByText('bp-covered')).toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-covered')).not.toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-blueprint-generate-PM-missing')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-start-director')).toBeDisabled();
  });

  it('blocks Director start from diagnostics even when Director task rows are temporarily empty', async () => {
    const onToggleDirector = vi.fn();
    apiFetchMock.mockImplementation((path: string) => {
      if (path === directorPath('/v2/director/tasks?source=auto') || path === directorPath('/v2/director/tasks?source=local')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === directorPath('/v2/director/workers')) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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
      if (path === cePath('/v2/chief-engineer/blueprints')) {
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

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(cePath('/v2/chief-engineer/diagnostics')));
    await waitFor(() => expect(screen.getByTitle('0/2')).toBeInTheDocument());
    expect(screen.getByTitle('PM-plan-1, PM-plan-2')).toBeInTheDocument();
    expect(screen.getByTestId('chief-engineer-diagnostics-issues')).toHaveTextContent('blueprint_coverage_incomplete');
    const startDirector = screen.getByTestId('chief-engineer-start-director');

    expect(startDirector).toBeDisabled();
    expect(startDirector).toHaveAttribute('title', '诊断显示 2 个 PM 任务缺少蓝图证据，不能启动 Director');

    fireEvent.click(startDirector);
    expect(onToggleDirector).not.toHaveBeenCalled();
  });

  it('keeps Director task pool metrics on runtime push data when runtime tasks are absent', async () => {
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === directorPath('/v2/director/tasks?source=auto')) {
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
      if (path === directorPath('/v2/director/tasks?source=local')) {
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
      if (path === directorPath('/v2/director/workers')) {
        return Promise.resolve({
          ok: true,
          json: async () => [],
        });
      }
      if (path === cePath('/v2/chief-engineer/diagnostics')) {
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
      if (path === cePath('/v2/chief-engineer/blueprints')) {
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

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(directorPath('/v2/director/workers')));
    expect(apiFetchMock).not.toHaveBeenCalledWith(directorPath('/v2/director/tasks?source=auto'));
    expect(apiFetchMock).not.toHaveBeenCalledWith(directorPath('/v2/director/tasks?source=local'));
    const pool = await screen.findByTestId('chief-engineer-director-task-pool');
    expect(pool).not.toHaveTextContent('/v2/director/tasks');
    expect(screen.getByTestId('chief-engineer-director-task-pool-endpoint')).toHaveAttribute('data-endpoint', '/v2/director/tasks');
    expect(screen.getByTestId('chief-engineer-director-task-source')).toHaveTextContent('runtime push');

    expect(within(pool).getByText('未领取').parentElement).toHaveTextContent('0');
    expect(within(pool).getByText('执行中').parentElement).toHaveTextContent('0');
    expect(within(pool).getByText('完成').parentElement).toHaveTextContent('0');
    expect(within(pool).getByText('总计').parentElement).toHaveTextContent('0');
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-backlog')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-status-PM-running')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chief-engineer-blueprint-generate-PM-done')).not.toBeInTheDocument();
  });
});
