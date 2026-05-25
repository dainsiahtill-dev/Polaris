import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PMDiagnosticsPanel } from './PMDiagnosticsPanel';

const serviceMocks = vi.hoisted(() => ({
  getPmManagementHealth: vi.fn(),
  getPmManagementStatus: vi.fn(),
  getPmStartupDiagnostics: vi.fn(),
  initializePmManagement: vi.fn(),
  getRoleKernelCacheStats: vi.fn(),
  getRoleKernelLLMEvents: vi.fn(),
  getRoleKernelTokenBudgetStats: vi.fn(),
  clearRoleKernelCache: vi.fn(),
}));

vi.mock('@/services/pmService', () => serviceMocks);

describe('PMDiagnosticsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    serviceMocks.getPmStartupDiagnostics.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        generated_at: '2026-05-23T00:00:00Z',
        issues: [],
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
          ok: true,
          status: 'ready',
          source: 'workspace_requirements',
          path: 'C:/Temp/Product/docs/product/requirements.md',
          bytes: 128,
          chars: 120,
          checked_paths: ['C:/Temp/Product/docs/product/requirements.md'],
        },
      },
    });
    serviceMocks.getPmManagementStatus.mockResolvedValue({
      ok: true,
      data: {
        initialized: true,
        workspace: 'C:/Temp/Product',
        project: 'Product',
        version: '1',
      },
    });
    serviceMocks.getPmManagementHealth.mockResolvedValue({
      ok: true,
      data: {
        overall: 'healthy',
        components: { docs: 'ok' },
        metrics: { coverage: 0.9 },
        recommendations: ['Keep docs current'],
      },
    });
    serviceMocks.initializePmManagement.mockResolvedValue({
      ok: true,
      data: {
        initialized: true,
        workspace: 'C:/Temp/Product',
        project_name: 'Product',
      },
    });
    serviceMocks.getRoleKernelCacheStats.mockResolvedValue({
      ok: true,
      data: {
        hits: 8,
        misses: 2,
        evictions: 0,
        size: 4,
        max_size: 100,
        hit_rate: 80,
        enabled: true,
      },
    });
    serviceMocks.getRoleKernelTokenBudgetStats.mockResolvedValue({
      ok: true,
      data: {
        system_context: 4000,
        task_context: 2000,
        conversation: 4000,
        override: 1000,
        safety_margin: 500,
        total: 11500,
        available_conversation: 4000,
      },
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
  });

  it('loads PM startup diagnostics through the backend contract', async () => {
    render(<PMDiagnosticsPanel isOpen onClose={vi.fn()} workspace="C:/Temp/Product" />);

    await waitFor(() => expect(serviceMocks.getPmStartupDiagnostics).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('所有检查通过')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /LanceDB 向量数据库/ }));
    expect(screen.getByText('LanceDB 正常运行')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /LLM 配置/ }));
    expect(screen.getByText('LLM 配置正常')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /工作区/ }));
    expect(screen.getByText('工作区已配置')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /规划输入/ }));
    expect(screen.getByTestId('pm-planning-input-diagnostics')).toHaveTextContent('PM 已找到可规划输入');
    expect(screen.getByTestId('pm-planning-input-diagnostics')).toHaveTextContent('workspace requirements');
  });

  it('treats a missing docs directory as a PM startup blocker', async () => {
    serviceMocks.getPmStartupDiagnostics.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: false,
        can_start: false,
        generated_at: '2026-05-23T00:00:00Z',
        issues: ['workspace_docs_missing'],
        startup_blockers: ['workspace_docs_missing'],
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
      },
    });

    render(<PMDiagnosticsPanel isOpen onClose={vi.fn()} workspace="C:/Temp/Product" />);

    expect(await screen.findByText('检测到问题')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /工作区/ }));
    expect(screen.getByText('docs/ 目录不存在，PM 启动已被阻断')).toBeInTheDocument();
    expect(screen.getByText('返回主界面完成 docs 初始化')).toBeInTheDocument();
  });

  it('treats missing planning input as a PM startup blocker', async () => {
    serviceMocks.getPmStartupDiagnostics.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: false,
        can_start: false,
        generated_at: '2026-05-23T00:00:00Z',
        issues: ['planning_input_missing'],
        startup_blockers: ['planning_input_missing'],
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
          checked_paths: [
            'C:/Temp/Product/docs/product/requirements.md',
            'C:/Temp/Product/docs/product/plan.md',
          ],
          error: 'planning_input_missing',
        },
      },
    });

    render(<PMDiagnosticsPanel isOpen onClose={vi.fn()} workspace="C:/Temp/Product" />);

    expect(await screen.findByText('检测到问题')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /规划输入/ }));
    const planning = await screen.findByTestId('pm-planning-input-diagnostics');
    expect(planning).toHaveTextContent('未找到需求或计划输入');
    expect(planning).toHaveTextContent('docs/product/requirements.md');
    expect(planning).toHaveTextContent('在 PM Workbench 中输入明确 directive 后再运行');
  });

  it('loads and clears PM kernel cache and token budget diagnostics', async () => {
    render(<PMDiagnosticsPanel isOpen onClose={vi.fn()} workspace="C:/Temp/Product" />);

    await waitFor(() => expect(serviceMocks.getRoleKernelCacheStats).toHaveBeenCalledWith('pm'));
    expect(serviceMocks.getRoleKernelTokenBudgetStats).toHaveBeenCalledWith('pm');
    expect(serviceMocks.getRoleKernelLLMEvents).toHaveBeenCalledWith('pm', {
      limit: 5,
      workspace: 'C:/Temp/Product',
    });

    fireEvent.click(screen.getByRole('button', { name: /LLM 缓存与预算/ }));
    const kernel = await screen.findByTestId('pm-kernel-diagnostics');
    expect(kernel).toHaveTextContent('/v2/pm/cache-stats');
    expect(kernel).toHaveTextContent('80.00%');
    expect(kernel).toHaveTextContent('/v2/pm/token-budget-stats');
    expect(kernel).toHaveTextContent('11,500');
    const llmEvents = screen.getByTestId('pm-llm-events-diagnostics');
    expect(llmEvents).toHaveTextContent('/v2/pm/llm-events?limit=5');
    expect(llmEvents).toHaveTextContent('/v2/pm/llm-events?limit=5&workspace=C%3A%2FTemp%2FProduct');
    expect(llmEvents).toHaveTextContent('llm call start');
    expect(llmEvents).toHaveTextContent('gpt-test');

    fireEvent.click(screen.getByTestId('pm-kernel-cache-clear'));
    await waitFor(() => expect(serviceMocks.clearRoleKernelCache).toHaveBeenCalledWith('pm'));
  });

  it('loads PM management status and health through management backend contracts', async () => {
    render(<PMDiagnosticsPanel isOpen onClose={vi.fn()} workspace="C:/Temp/Product" />);

    await waitFor(() => expect(serviceMocks.getPmManagementStatus).toHaveBeenCalledTimes(1));
    expect(serviceMocks.getPmManagementHealth).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: /PM 管理状态/ }));
    const management = await screen.findByTestId('pm-management-diagnostics');
    expect(management).toHaveTextContent('/pm/v2/pm/status');
    expect(management).toHaveTextContent('/pm/v2/pm/status?workspace=C%3A%2FTemp%2FProduct');
    expect(management).toHaveTextContent('Product');
    expect(management).toHaveTextContent('/pm/v2/pm/health');
    expect(management).toHaveTextContent('/pm/v2/pm/health?workspace=C%3A%2FTemp%2FProduct');
    expect(management).toHaveTextContent('healthy');
    expect(management).toHaveTextContent('docs · ok');
  });

  it('passes the active workspace to PM management diagnostics', async () => {
    render(<PMDiagnosticsPanel isOpen onClose={vi.fn()} workspace="C:/Temp/Product" />);

    await waitFor(() => expect(serviceMocks.getPmStartupDiagnostics).toHaveBeenCalledWith('C:/Temp/Product'));
    expect(serviceMocks.getPmManagementStatus).toHaveBeenCalledWith('C:/Temp/Product');
    expect(serviceMocks.getPmManagementHealth).toHaveBeenCalledWith('C:/Temp/Product');
  });

  it('initializes PM management from diagnostics when status is not initialized', async () => {
    serviceMocks.getPmManagementStatus
      .mockResolvedValueOnce({
        ok: true,
        data: {
          initialized: false,
          workspace: 'C:/Temp/Product',
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          initialized: true,
          workspace: 'C:/Temp/Product',
          project: 'Recovered Project',
          version: '1',
        },
      });
    serviceMocks.getPmManagementHealth.mockResolvedValueOnce({
      ok: true,
      data: {
        overall: 'healthy',
        components: { docs: 'ok' },
        metrics: {},
        recommendations: [],
      },
    });
    serviceMocks.initializePmManagement.mockResolvedValueOnce({
      ok: true,
      data: {
        initialized: true,
        workspace: 'C:/Temp/Product',
        project_name: 'Recovered Project',
      },
    });

    render(<PMDiagnosticsPanel isOpen onClose={vi.fn()} workspace="C:/Temp/Product" />);

    await waitFor(() => expect(serviceMocks.getPmManagementStatus).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: /PM 管理状态/ }));

    const initPanel = await screen.findByTestId('pm-management-init-panel');
    expect(initPanel).toHaveTextContent('POST /pm/v2/pm/init');
    expect(initPanel).toHaveTextContent('POST /pm/v2/pm/init?workspace=C%3A%2FTemp%2FProduct');
    fireEvent.change(screen.getByTestId('pm-management-init-project'), {
      target: { value: 'Recovered Project' },
    });
    fireEvent.change(screen.getByTestId('pm-management-init-description'), {
      target: { value: 'Initialize PM management state' },
    });
    fireEvent.click(screen.getByTestId('pm-management-init-submit'));

    await waitFor(() => expect(serviceMocks.initializePmManagement).toHaveBeenCalledWith(
      {
        projectName: 'Recovered Project',
        description: 'Initialize PM management state',
      },
      'C:/Temp/Product',
    ));
    await waitFor(() => expect(serviceMocks.getPmManagementStatus).toHaveBeenCalledTimes(2));
    expect(await screen.findByTestId('pm-management-init-result')).toHaveTextContent('Recovered Project');
  });

  it('renders service errors and supports manual refresh', async () => {
    serviceMocks.getPmStartupDiagnostics
      .mockResolvedValueOnce({
        ok: false,
        error: 'diagnostics unavailable',
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ok: false,
          generated_at: '2026-05-23T00:00:01Z',
          issues: ['llm_not_ready'],
          lancedb: { ok: true, state: 'ready' },
          llm: {
            ok: false,
            state: 'blocked',
            blocked_roles: ['pm'],
            unsupported_roles: [],
            required_ready_roles: ['pm'],
            details: {
              roles: {
                pm: {
                  ready: false,
                  provider_id: 'openai_compat',
                  model: 'Qwen3-Max',
                  readiness_issue: 'model_mismatch',
                  readiness_source: 'role_index',
                  tested_model: 'MiniMax-M2.5',
                },
              },
            },
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
        },
      });

    render(<PMDiagnosticsPanel isOpen onClose={vi.fn()} />);

    expect(await screen.findByTestId('pm-diagnostics-error')).toHaveTextContent('diagnostics unavailable');

    fireEvent.click(screen.getByRole('button', { name: /重新检查/ }));

    await waitFor(() => expect(serviceMocks.getPmStartupDiagnostics).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('检测到问题')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /LLM 配置/ }));
    expect(screen.getByText('状态: blocked')).toBeInTheDocument();
    expect(screen.getByText('阻塞的角色: pm')).toBeInTheDocument();
    expect(screen.getByTestId('pm-llm-role-evidence')).toHaveTextContent('pm: model_mismatch');
    expect(screen.getByTestId('pm-llm-role-evidence')).toHaveTextContent('MiniMax-M2.5');
  });
});
