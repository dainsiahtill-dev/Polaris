/**
 * PM Service Tests
 *
 * Test PM and Director service API calls
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock the apiClient
const mockApiGet = vi.fn();
const mockApiPost = vi.fn();
const mockApiPostEmpty = vi.fn();
const mockApiDelete = vi.fn();

vi.mock('@/services/apiClient', () => ({
  apiDelete: (...args: unknown[]) => mockApiDelete(...args),
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
  apiPostEmpty: (...args: unknown[]) => mockApiPostEmpty(...args),
}));

import * as pmService from '../pmService';

describe('pmService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  describe('getPmStatus', () => {
    it('should call apiGet with correct path', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          running: true,
          pid: 12345,
          started_at: Date.now(),
        },
      });

      const result = await pmService.getPmStatus();

      expect(mockApiGet).toHaveBeenCalledWith('/v2/pm/status', 'Failed to load PM status');
      expect(result.ok).toBe(true);
      expect(result.data?.running).toBe(true);
    });

    it('should pass explicit workspace to PM status', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { running: false, workspace: 'C:/Temp/Product' },
      });

      await pmService.getPmStatus('C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/status?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load PM status',
      );
    });

    it('should return error on API failure', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: false,
        error: 'Failed to load PM status',
      });

      const result = await pmService.getPmStatus();

      expect(result.ok).toBe(false);
      expect(result.error).toBe('Failed to load PM status');
    });
  });

  describe('getPmStartupDiagnostics', () => {
    it('should call PM diagnostics backend contract', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          generated_at: '2026-05-23T00:00:00Z',
          issues: [],
          lancedb: { ok: true, state: 'ready' },
          llm: { ok: true, state: 'ready', blocked_roles: [] },
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
            chars: 120,
            bytes: 128,
            checked_paths: ['C:/Temp/Product/docs/product/requirements.md'],
          },
        },
      });

      const result = await pmService.getPmStartupDiagnostics();

      expect(mockApiGet).toHaveBeenCalledWith('/v2/pm/diagnostics', 'Failed to load PM diagnostics');
      expect(result.ok).toBe(true);
      expect(result.data?.ok).toBe(true);
      expect(result.data?.workspace.docs_present).toBe(true);
      expect(result.data?.planning_input.ok).toBe(true);
    });

    it('should pass explicit workspace to PM diagnostics', async () => {
      mockApiGet.mockResolvedValueOnce({ ok: true, data: { ok: true } });

      await pmService.getPmStartupDiagnostics('C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/diagnostics?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load PM diagnostics',
      );
    });
  });

  describe('PM management diagnostics', () => {
    it('loads PM management status through the management route', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          initialized: true,
          workspace: 'C:/Temp/Product',
          project: 'Product',
          version: '1',
        },
      });

      const result = await pmService.getPmManagementStatus();

      expect(mockApiGet).toHaveBeenCalledWith('/pm/v2/pm/status', 'Failed to load PM management status');
      expect(result.ok).toBe(true);
      expect(result.data?.initialized).toBe(true);
    });

    it('passes explicit workspace to PM management status', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          initialized: true,
          workspace: 'C:/Temp/Product',
        },
      });

      await pmService.getPmManagementStatus('C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/pm/v2/pm/status?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load PM management status',
      );
    });

    it('loads PM management health through the management route', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          overall: 'healthy',
          components: { docs: 'ok' },
          metrics: { coverage: 0.9 },
          recommendations: [],
        },
      });

      const result = await pmService.getPmManagementHealth();

      expect(mockApiGet).toHaveBeenCalledWith('/pm/v2/pm/health', 'Failed to load PM management health');
      expect(result.ok).toBe(true);
      expect(result.data?.overall).toBe('healthy');
    });

    it('passes explicit workspace to PM management health', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          overall: 'healthy',
          components: {},
          metrics: {},
          recommendations: [],
        },
      });

      await pmService.getPmManagementHealth('C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/pm/v2/pm/health?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load PM management health',
      );
    });

    it('initializes PM management with encoded query parameters', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({
        ok: true,
        data: {
          initialized: true,
          workspace: 'C:/Temp/Product',
          project_name: 'My Project',
        },
      });

      const result = await pmService.initializePmManagement({
        projectName: 'My Project',
        description: 'A PM managed workspace',
      });

      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/pm/v2/pm/init?project_name=My+Project&description=A+PM+managed+workspace',
        'Failed to initialize PM management',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.project_name).toBe('My Project');
    });

    it('passes explicit workspace when initializing PM management', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({
        ok: true,
        data: {
          initialized: true,
          workspace: 'C:/Temp/Product',
          project_name: 'My Project',
        },
      });

      await pmService.initializePmManagement(
        {
          projectName: 'My Project',
          description: 'A PM managed workspace',
        },
        'C:/Temp/Product',
      );

      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/pm/v2/pm/init?project_name=My+Project&description=A+PM+managed+workspace&workspace=C%3A%2FTemp%2FProduct',
        'Failed to initialize PM management',
      );
    });
  });

  describe('role kernel diagnostics', () => {
    it('loads PM cache stats from the role backend contract', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { hits: 3, misses: 1, size: 2, max_size: 100, hit_rate: 75, enabled: true },
      });

      const result = await pmService.getRoleKernelCacheStats('pm');

      expect(mockApiGet).toHaveBeenCalledWith('/v2/pm/cache-stats', 'Failed to load pm cache stats');
      expect(result.ok).toBe(true);
      expect(result.data?.hit_rate).toBe(75);
    });

    it('clears Director cache through the no-body POST endpoint', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({
        ok: true,
        data: { ok: true, message: 'Cache cleared' },
      });

      const result = await pmService.clearRoleKernelCache('director');

      expect(mockApiPostEmpty).toHaveBeenCalledWith('/v2/director/cache-clear', 'Failed to clear director cache');
      expect(result.ok).toBe(true);
      expect(result.data?.message).toBe('Cache cleared');
    });

    it('loads Director token budget stats from the role backend contract', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { total: 11500, available_conversation: 4000, safety_margin: 500 },
      });

      const result = await pmService.getRoleKernelTokenBudgetStats('director');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/token-budget-stats',
        'Failed to load director token budget stats',
      );
      expect(result.data?.total).toBe(11500);
    });

    it('encodes PM LLM event query filters', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { run_id: 'run 1', task_id: 'task/1', events: [], count: 0 },
      });

      await pmService.getRoleKernelLLMEvents('pm', {
        runId: 'run 1',
        taskId: 'task/1',
        limit: 50,
        workspace: 'C:/Temp/Product',
      });

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/llm-events?run_id=run+1&task_id=task%2F1&limit=50&workspace=C%3A%2FTemp%2FProduct',
        'Failed to load pm LLM events',
      );
    });

    it('encodes Director global LLM event filters', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { events: [], count: 0, stats: { total: 0 } },
      });

      await pmService.getRoleKernelLLMEvents('director', {
        role: 'director',
        limit: 5,
        workspace: 'C:/Temp/Product',
      });

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/llm-events?role=director&limit=5&workspace=C%3A%2FTemp%2FProduct',
        'Failed to load director LLM events',
      );
    });

    it('routes Chief Engineer kernel diagnostics through the Chief Engineer v2 path', async () => {
      mockApiGet
        .mockResolvedValueOnce({ ok: true, data: { hits: 3 } })
        .mockResolvedValueOnce({ ok: true, data: { total: 100 } })
        .mockResolvedValueOnce({ ok: true, data: { events: [] } });
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true, data: { ok: true, message: 'Cache cleared' } });

      await pmService.getRoleKernelCacheStats('chief_engineer');
      await pmService.getRoleKernelTokenBudgetStats('chief_engineer');
      await pmService.getRoleKernelLLMEvents('chief_engineer', { limit: 5 });
      await pmService.clearRoleKernelCache('chief_engineer');

      expect(mockApiGet).toHaveBeenNthCalledWith(
        1,
        '/v2/chief-engineer/cache-stats',
        'Failed to load chief_engineer cache stats',
      );
      expect(mockApiGet).toHaveBeenNthCalledWith(
        2,
        '/v2/chief-engineer/token-budget-stats',
        'Failed to load chief_engineer token budget stats',
      );
      expect(mockApiGet).toHaveBeenNthCalledWith(
        3,
        '/v2/chief-engineer/llm-events?limit=5',
        'Failed to load chief_engineer LLM events',
      );
      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/v2/chief-engineer/cache-clear',
        'Failed to clear chief_engineer cache',
      );
    });

    it('encodes Director task LLM event paths and query filters', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { task_id: 'task/1', events: [], stats: { total: 0 } },
      });

      await pmService.getDirectorTaskKernelLLMEvents('task/1', {
        runId: 'run 1',
        limit: 25,
        workspace: 'C:/Temp/Product',
      });

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/tasks/task%2F1/llm-events?run_id=run+1&limit=25&workspace=C%3A%2FTemp%2FProduct',
        'Failed to load Director task LLM events',
      );
    });
  });

  describe('Director task and worker details', () => {
    it('loads a Director task detail with an encoded task id', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { id: 'task/1', subject: 'Task detail', status: 'RUNNING' },
      });

      await pmService.getDirectorTask('task/1');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/tasks/task%2F1',
        'Failed to load Director task',
      );
    });

    it('passes workspace when loading a Director task detail', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { id: 'task/1', subject: 'Task detail', status: 'RUNNING' },
      });

      await pmService.getDirectorTask('task/1', 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/tasks/task%2F1?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load Director task',
      );
    });

    it('lists Director workers through the backend worker route', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: [{ id: 'worker-1', status: 'idle' }],
      });

      const result = await pmService.listDirectorWorkers();

      expect(mockApiGet).toHaveBeenCalledWith('/v2/director/workers', 'Failed to list Director workers');
      expect(result.ok).toBe(true);
      expect(result.data?.[0].id).toBe('worker-1');
    });

    it('passes workspace when listing Director workers', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: [{ id: 'worker-1', status: 'idle' }],
      });

      await pmService.listDirectorWorkers('C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/workers?workspace=C%3A%2FTemp%2FProduct',
        'Failed to list Director workers',
      );
    });

    it('loads a Director worker detail with an encoded worker id', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { id: 'worker/1', status: 'busy' },
      });

      await pmService.getDirectorWorker('worker/1');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/workers/worker%2F1',
        'Failed to load Director worker',
      );
    });

    it('passes workspace when loading a Director worker detail', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { id: 'worker/1', status: 'busy' },
      });

      await pmService.getDirectorWorker('worker/1', 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/workers/worker%2F1?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load Director worker',
      );
    });

    it('cancels a Director task with an encoded task id through the no-body endpoint', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({
        ok: true,
        data: { ok: true, task_id: 'task/1' },
      });

      const result = await pmService.cancelDirectorTask('task/1');

      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/v2/director/tasks/task%2F1/cancel',
        'Failed to cancel Director task',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.task_id).toBe('task/1');
    });

    it('passes workspace when cancelling a Director task', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({
        ok: true,
        data: { ok: true, task_id: 'task/1', workspace: 'C:/Temp/Product' },
      });

      await pmService.cancelDirectorTask('task/1', 'C:/Temp/Product');

      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/v2/director/tasks/task%2F1/cancel?workspace=C%3A%2FTemp%2FProduct',
        'Failed to cancel Director task',
      );
    });
  });

  describe('getDirectorStatus', () => {
    it('should normalize running boolean status', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          running: true,
          pid: 12346,
          started_at: Date.now(),
          mode: 'standard',
          log_path: '/path/to/log',
          source: 'handle',
        },
      });

      const result = await pmService.getDirectorStatus();

      expect(result.ok).toBe(true);
      expect(result.data?.running).toBe(true);
      expect(result.data?.pid).toBe(12346);
    });

    it('should pass explicit workspace to Director status', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          running: false,
          pid: null,
        },
      });

      await pmService.getDirectorStatus('C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/status?source=auto&workspace=C%3A%2FTemp%2FProduct',
        'Failed to load Director status',
      );
    });

    it('should normalize state-based status', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          state: 'RUNNING',
          mode: 'v2_service',
          source: 'v2_service',
        },
      });

      const result = await pmService.getDirectorStatus();

      expect(result.ok).toBe(true);
      expect(result.data?.running).toBe(true);
      expect(result.data?.mode).toBe('v2_service');
    });

    it('should handle non-running state', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          state: 'IDLE',
          mode: 'v2_service',
        },
      });

      const result = await pmService.getDirectorStatus();

      expect(result.ok).toBe(true);
      expect(result.data?.running).toBe(false);
    });

    it('should return error on API failure', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: false,
        error: 'Failed to load Director status',
      });

      const result = await pmService.getDirectorStatus();

      expect(result.ok).toBe(false);
      expect(result.error).toBe('Failed to load Director status');
    });

    it('should handle null pid', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          running: true,
          pid: null,
          started_at: null,
          mode: 'v2_service',
        },
      });

      const result = await pmService.getDirectorStatus();

      expect(result.ok).toBe(true);
      expect(result.data?.pid).toBeNull();
    });

    it('should handle string pid', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          running: true,
          pid: 'not-a-number',
          mode: 'v2_service',
        },
      });

      const result = await pmService.getDirectorStatus();

      expect(result.ok).toBe(true);
      expect(result.data?.pid).toBeNull();
    });
  });

  describe('getAllStatuses', () => {
    it('should return both PM and Director statuses', async () => {
      mockApiGet
        .mockResolvedValueOnce({
          ok: true,
          data: { running: true, pid: 12345 },
        })
        .mockResolvedValueOnce({
          ok: true,
          data: { running: false, pid: null },
        });

      const result = await pmService.getAllStatuses();

      expect(result.pm.ok).toBe(true);
      expect(result.director.ok).toBe(true);
      expect(result.pm.data?.running).toBe(true);
      expect(result.director.data?.running).toBe(false);
    });

    it('should handle partial failures', async () => {
      mockApiGet
        .mockResolvedValueOnce({
          ok: true,
          data: { running: true, pid: 12345 },
        })
        .mockResolvedValueOnce({
          ok: false,
          error: 'Director unavailable',
        });

      const result = await pmService.getAllStatuses();

      expect(result.pm.ok).toBe(true);
      expect(result.director.ok).toBe(false);
    });

    it('should pass explicit workspace to both status endpoints', async () => {
      mockApiGet
        .mockResolvedValueOnce({
          ok: true,
          data: { running: true, pid: 12345 },
        })
        .mockResolvedValueOnce({
          ok: true,
          data: { running: false, pid: null },
        });

      await pmService.getAllStatuses('C:/Temp/Product');

      expect(mockApiGet).toHaveBeenNthCalledWith(
        1,
        '/v2/pm/status?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load PM status',
      );
      expect(mockApiGet).toHaveBeenNthCalledWith(
        2,
        '/v2/director/status?source=auto&workspace=C%3A%2FTemp%2FProduct',
        'Failed to load Director status',
      );
    });
  });

  describe('startPm', () => {
    it('should call apiPostEmpty with correct path', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      const result = await pmService.startPm();

      expect(mockApiPostEmpty).toHaveBeenCalledWith('/v2/pm/start', 'Failed to start PM');
      expect(result.ok).toBe(true);
    });

    it('should include resume parameter when true', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      await pmService.startPm(true);

      expect(mockApiPostEmpty).toHaveBeenCalledWith('/v2/pm/start?resume=true', 'Failed to start PM');
    });

    it('should include explicit workspace when starting or resuming', async () => {
      mockApiPostEmpty.mockResolvedValue({ ok: true });

      await pmService.startPm(false, 'C:/Temp/Product');
      await pmService.startPm(true, 'C:/Temp/Product');

      expect(mockApiPostEmpty).toHaveBeenNthCalledWith(
        1,
        '/v2/pm/start?workspace=C%3A%2FTemp%2FProduct',
        'Failed to start PM',
      );
      expect(mockApiPostEmpty).toHaveBeenNthCalledWith(
        2,
        '/v2/pm/start?resume=true&workspace=C%3A%2FTemp%2FProduct',
        'Failed to start PM',
      );
    });

    it('should return error on API failure', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({
        ok: false,
        error: 'Failed to start PM',
      });

      const result = await pmService.startPm();

      expect(result.ok).toBe(false);
      expect(result.error).toBe('Failed to start PM');
    });
  });

  describe('stopPm', () => {
    it('should call apiPostEmpty with correct path', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      const result = await pmService.stopPm();

      expect(mockApiPostEmpty).toHaveBeenCalledWith('/v2/pm/stop', 'Failed to stop PM');
      expect(result.ok).toBe(true);
    });

    it('should pass explicit workspace to PM stop', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      await pmService.stopPm('C:/Temp/Product');

      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/v2/pm/stop?workspace=C%3A%2FTemp%2FProduct',
        'Failed to stop PM',
      );
    });

    it('should return error on API failure', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({
        ok: false,
        error: 'Failed to stop PM',
      });

      const result = await pmService.stopPm();

      expect(result.ok).toBe(false);
      expect(result.error).toBe('Failed to stop PM');
    });
  });

  describe('runPmOnce', () => {
    it('should call apiPostEmpty with correct path', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      const result = await pmService.runPmOnce();

      expect(mockApiPostEmpty).toHaveBeenCalledWith('/v2/pm/run_once', 'PM Run Once failed');
      expect(result.ok).toBe(true);
    });

    it('should pass explicit workspace to PM run_once', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      await pmService.runPmOnce('C:/Temp/Product');

      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/v2/pm/run_once?workspace=C%3A%2FTemp%2FProduct',
        'PM Run Once failed',
      );
    });
  });

  describe('startDirector', () => {
    it('should call apiPostEmpty with correct path', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      const result = await pmService.startDirector();

      expect(mockApiPostEmpty).toHaveBeenCalledWith('/v2/director/start', 'Failed to start Director');
      expect(result.ok).toBe(true);
    });

    it('should pass explicit workspace to Director start', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      await pmService.startDirector('C:/Temp/Product');

      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/v2/director/start?workspace=C%3A%2FTemp%2FProduct',
        'Failed to start Director',
      );
    });
  });

  describe('stopDirector', () => {
    it('should call apiPostEmpty with correct path', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      const result = await pmService.stopDirector();

      expect(mockApiPostEmpty).toHaveBeenCalledWith('/v2/director/stop', 'Failed to stop Director');
      expect(result.ok).toBe(true);
    });

    it('should pass explicit workspace to Director stop', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      await pmService.stopDirector('C:/Temp/Product');

      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/v2/director/stop?workspace=C%3A%2FTemp%2FProduct',
        'Failed to stop Director',
      );
    });
  });

  describe('runDirector', () => {
    it('should call task-scoped director orchestration endpoint', async () => {
      const payload = {
        workspace: 'C:/Temp/Product',
        task_id: 'PM-42',
        task_filter: 'PM-42',
        execution_mode: 'parallel' as const,
      };
      mockApiPost.mockResolvedValueOnce({
        ok: true,
        data: {
          run_id: 'director-run-1',
          status: 'queued',
          workspace: payload.workspace,
          tasks_queued: 1,
          message: 'queued',
        },
      });

      const result = await pmService.runDirector(payload);

      expect(mockApiPost).toHaveBeenCalledWith('/v2/director/run', payload, 'Failed to run Director');
      expect(result.ok).toBe(true);
      expect(result.data?.run_id).toBe('director-run-1');
    });
  });

  describe('runPm', () => {
    it('should call PM orchestration endpoint with directive and stage payload', async () => {
      const payload: pmService.RunPmPayload = {
        workspace: 'C:/Temp/Product',
        directive: 'Plan checkout workflow',
        stage: 'architect',
        run_director: true,
        director_iterations: 2,
        metadata: {
          source: 'pm_workbench',
          role_session_id: 'pm-session-1',
        },
      };
      mockApiPost.mockResolvedValueOnce({
        ok: true,
        data: {
          run_id: 'pm-run-1',
          status: 'running',
          workspace: payload.workspace,
          stage: 'architect',
          message: 'PM architect run started',
        },
      });

      const result = await pmService.runPm(payload);

      expect(mockApiPost).toHaveBeenCalledWith('/v2/pm/run', payload, 'Failed to run PM');
      expect(result.ok).toBe(true);
      expect(result.data?.run_id).toBe('pm-run-1');
    });
  });

  describe('role orchestration run detail', () => {
    it('loads a PM run detail with an encoded run id', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { run_id: 'pm/run 1', status: 'RUNNING', workspace: 'C:/Temp/Product', stage: 'architect', message: 'Status: RUNNING' },
      });

      await pmService.getPmRun('pm/run 1');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/runs/pm%2Frun%201',
        'Failed to load PM run',
      );
    });

    it('cancels a PM run with an encoded run id', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({
        ok: true,
        data: { run_id: 'pm/run 1', status: 'CANCELLED', workspace: 'C:/Temp/Product', stage: 'pm', message: 'Status: CANCELLED' },
      });

      await pmService.cancelPmRun('pm/run 1');

      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/v2/pm/runs/pm%2Frun%201/cancel',
        'Failed to cancel PM run',
      );
    });

    it('loads a Director run detail with an encoded run id', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { run_id: 'director/run 1', status: 'RUNNING', workspace: 'C:/Temp/Product', tasks_queued: 2, message: 'Status: RUNNING' },
      });

      await pmService.getDirectorRun('director/run 1');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/runs/director%2Frun%201',
        'Failed to load Director run',
      );
    });

    it('cancels a Director run with an encoded run id', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({
        ok: true,
        data: { run_id: 'director/run 1', status: 'CANCELLED', workspace: 'C:/Temp/Product', tasks_queued: 2, message: 'Status: CANCELLED' },
      });

      await pmService.cancelDirectorRun('director/run 1');

      expect(mockApiPostEmpty).toHaveBeenCalledWith(
        '/v2/director/runs/director%2Frun%201/cancel',
        'Failed to cancel Director run',
      );
    });

    it('passes explicit workspace to PM and Director run evidence routes', async () => {
      mockApiGet
        .mockResolvedValueOnce({
          ok: true,
          data: { run_id: 'pm/run 1', status: 'RUNNING', workspace: 'C:/Temp/Product', stage: 'pm', message: 'Status: RUNNING' },
        })
        .mockResolvedValueOnce({
          ok: true,
          data: { run_id: 'director/run 1', status: 'RUNNING', workspace: 'C:/Temp/Product', tasks_queued: 1, message: 'Status: RUNNING' },
        });
      mockApiPostEmpty
        .mockResolvedValueOnce({
          ok: true,
          data: { run_id: 'pm/run 1', status: 'CANCELLED', workspace: 'C:/Temp/Product', stage: 'pm', message: 'Status: CANCELLED' },
        })
        .mockResolvedValueOnce({
          ok: true,
          data: { run_id: 'director/run 1', status: 'CANCELLED', workspace: 'C:/Temp/Product', tasks_queued: 1, message: 'Status: CANCELLED' },
        });

      await pmService.getPmRun('pm/run 1', 'C:/Temp/Product');
      await pmService.cancelPmRun('pm/run 1', 'C:/Temp/Product');
      await pmService.getDirectorRun('director/run 1', 'C:/Temp/Product');
      await pmService.cancelDirectorRun('director/run 1', 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenNthCalledWith(
        1,
        '/v2/pm/runs/pm%2Frun%201?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load PM run',
      );
      expect(mockApiPostEmpty).toHaveBeenNthCalledWith(
        1,
        '/v2/pm/runs/pm%2Frun%201/cancel?workspace=C%3A%2FTemp%2FProduct',
        'Failed to cancel PM run',
      );
      expect(mockApiGet).toHaveBeenNthCalledWith(
        2,
        '/v2/director/runs/director%2Frun%201?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load Director run',
      );
      expect(mockApiPostEmpty).toHaveBeenNthCalledWith(
        2,
        '/v2/director/runs/director%2Frun%201/cancel?workspace=C%3A%2FTemp%2FProduct',
        'Failed to cancel Director run',
      );
    });
  });

  describe('getDirectorCapabilities', () => {
    it('should call Director capability matrix endpoint', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          role: 'director',
          capabilities: {
            electron_workbench: ['read_files', 'write_files'],
            workflow: ['read_files', 'execute_tests'],
          },
        },
      });

      const result = await pmService.getDirectorCapabilities();

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/capabilities',
        'Failed to load Director capabilities',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.role).toBe('director');
    });
  });

  describe('getDirectorDiagnostics', () => {
    it('should call Director diagnostics endpoint', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          role: 'director',
          generated_at: '2026-05-23T00:00:00Z',
          workspace: 'C:/Temp/Product',
          status: { ok: true, state: 'IDLE', running: false, source: 'workflow', projection_source: 'director_merged' },
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
            ready_task_ids: ['director-task-1'],
            blueprint_ready_task_ids: ['director-task-1'],
            missing_blueprint_task_ids: [],
            invalid_blueprint_task_ids: [],
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
          issues: [],
        },
      });

      const result = await pmService.getDirectorDiagnostics();

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/diagnostics',
        'Failed to load Director diagnostics',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.tasks.ready_to_execute).toBe(1);
      expect(result.data?.tasks.invalid_blueprint_task_ids).toEqual([]);
    });

    it('should pass explicit workspace to Director diagnostics', async () => {
      mockApiGet.mockResolvedValueOnce({ ok: true, data: { ok: true } });

      await pmService.getDirectorDiagnostics('C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/diagnostics?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load Director diagnostics',
      );
    });
  });

  describe('listDirectorTasks', () => {
    it('should call apiGet with correct path', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: [
          { id: 'task-1', subject: 'Task 1' },
          { id: 'task-2', subject: 'Task 2' },
        ],
      });

      const result = await pmService.listDirectorTasks();

      expect(mockApiGet).toHaveBeenCalledWith('/v2/director/tasks', 'Failed to list Director tasks');
      expect(result.ok).toBe(true);
      expect(result.data).toHaveLength(2);
    });

    it('should include source query parameter when provided', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: [{ id: 'task-1', subject: 'Task 1', metadata: { source: 'pm' } }],
      });

      await pmService.listDirectorTasks('pm');

      expect(mockApiGet).toHaveBeenCalledWith('/v2/director/tasks?source=pm', 'Failed to list Director tasks');
    });

    it('should include source and workspace query parameters when provided', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: [{ id: 'task-1', subject: 'Task 1' }],
      });

      await pmService.listDirectorTasks('workflow', 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/director/tasks?source=workflow&workspace=C%3A%2FTemp%2FProduct',
        'Failed to list Director tasks',
      );
    });

    it('should return error on API failure', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: false,
        error: 'Failed to list tasks',
      });

      const result = await pmService.listDirectorTasks();

      expect(result.ok).toBe(false);
      expect(result.error).toBe('Failed to list tasks');
    });
  });

  describe('resolveDirectorTaskSources', () => {
    it('should include workflow and local queues while Director is running', () => {
      expect(pmService.resolveDirectorTaskSources(true)).toEqual(['workflow', 'local']);
    });

    it('should include auto and local queues while Director is idle', () => {
      expect(pmService.resolveDirectorTaskSources(false)).toEqual(['auto', 'local']);
    });
  });

  describe('listDirectorTaskFallbackRows', () => {
    it('should load idle fallback rows from auto and local sources with source metadata', async () => {
      mockApiGet
        .mockResolvedValueOnce({
          ok: true,
          data: [
            {
              id: 'director-task-1',
              subject: 'Auto task',
              metadata: { pm_task_id: 'PM-1' },
            },
          ],
        })
        .mockResolvedValueOnce({
          ok: true,
          data: [
            {
              id: 'director-task-2',
              subject: 'Local task',
            },
          ],
        });

      const result = await pmService.listDirectorTaskFallbackRows(false);

      expect(mockApiGet).toHaveBeenNthCalledWith(
        1,
        '/v2/director/tasks?source=auto',
        'Failed to list Director tasks',
      );
      expect(mockApiGet).toHaveBeenNthCalledWith(
        2,
        '/v2/director/tasks?source=local',
        'Failed to list Director tasks',
      );
      expect(result.ok).toBe(true);
      expect(result.data).toEqual([
        {
          id: 'director-task-1',
          subject: 'Auto task',
          metadata: { pm_task_id: 'PM-1', director_task_source: 'auto' },
        },
        {
          id: 'director-task-2',
          subject: 'Local task',
          metadata: { director_task_source: 'local' },
        },
      ]);
    });

    it('should pass workspace through each Director fallback source request', async () => {
      mockApiGet
        .mockResolvedValueOnce({
          ok: true,
          data: [{ id: 'director-task-1', subject: 'Auto task' }],
        })
        .mockResolvedValueOnce({
          ok: true,
          data: [{ id: 'director-task-2', subject: 'Local task' }],
        });

      const result = await pmService.listDirectorTaskFallbackRows(false, 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenNthCalledWith(
        1,
        '/v2/director/tasks?source=auto&workspace=C%3A%2FTemp%2FProduct',
        'Failed to list Director tasks',
      );
      expect(mockApiGet).toHaveBeenNthCalledWith(
        2,
        '/v2/director/tasks?source=local&workspace=C%3A%2FTemp%2FProduct',
        'Failed to list Director tasks',
      );
      expect(result.ok).toBe(true);
    });

    it('should deduplicate later local rows by Director task id', async () => {
      mockApiGet
        .mockResolvedValueOnce({
          ok: true,
          data: [{ id: 'director-task-1', subject: 'Workflow task' }],
        })
        .mockResolvedValueOnce({
          ok: true,
          data: [{ id: 'director-task-1', subject: 'Local task' }],
        });

      const result = await pmService.listDirectorTaskFallbackRows(true);

      expect(mockApiGet).toHaveBeenNthCalledWith(
        1,
        '/v2/director/tasks?source=workflow',
        'Failed to list Director tasks',
      );
      expect(mockApiGet).toHaveBeenNthCalledWith(
        2,
        '/v2/director/tasks?source=local',
        'Failed to list Director tasks',
      );
      expect(result.ok).toBe(true);
      expect(result.data).toEqual([
        {
          id: 'director-task-1',
          subject: 'Local task',
          metadata: { director_task_source: 'local' },
        },
      ]);
    });

    it('should deduplicate local rows already returned by idle auto fallback', async () => {
      mockApiGet
        .mockResolvedValueOnce({
          ok: true,
          data: [{ id: 'director-task-1', subject: 'Auto local fallback' }],
        })
        .mockResolvedValueOnce({
          ok: true,
          data: [{ id: 'director-task-1', subject: 'Local task canonical' }],
        });

      const result = await pmService.listDirectorTaskFallbackRows(false);

      expect(mockApiGet).toHaveBeenNthCalledWith(
        1,
        '/v2/director/tasks?source=auto',
        'Failed to list Director tasks',
      );
      expect(mockApiGet).toHaveBeenNthCalledWith(
        2,
        '/v2/director/tasks?source=local',
        'Failed to list Director tasks',
      );
      expect(result.ok).toBe(true);
      expect(result.data).toEqual([
        {
          id: 'director-task-1',
          subject: 'Local task canonical',
          metadata: { director_task_source: 'local' },
        },
      ]);
    });

    it('should return the last source error when every source fails', async () => {
      mockApiGet
        .mockResolvedValueOnce({ ok: false, error: 'workflow unavailable' })
        .mockResolvedValueOnce({ ok: false, error: 'local unavailable' });

      const result = await pmService.listDirectorTaskFallbackRows(true);

      expect(result.ok).toBe(false);
      expect(result.error).toBe('local unavailable');
    });
  });

  describe('listPmTaskHistory', () => {
    it('should call PM task history endpoint with filters', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          history: [{ id: 'hist-1', task_id: 'PM-1', action: 'created' }],
          pagination: { total: 1 },
        },
      });

      const result = await pmService.listPmTaskHistory({
        taskId: 'PM-1',
        assignee: 'pm',
        status: 'done',
        limit: 20,
        offset: 5,
        workspace: 'C:/Temp/Product',
      });

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/tasks/history?task_id=PM-1&assignee=pm&status=done&limit=20&offset=5&workspace=C%3A%2FTemp%2FProduct',
        'Failed to list PM task history',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.history?.[0].task_id).toBe('PM-1');
    });

    it('should use default task history pagination', async () => {
      mockApiGet.mockResolvedValueOnce({ ok: true, data: { history: [] } });

      await pmService.listPmTaskHistory();

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/tasks/history?limit=50&offset=0',
        'Failed to list PM task history',
      );
    });
  });

  describe('listPmTasks', () => {
    it('should call PM task list endpoint with filters', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          tasks: [{ id: 'PM-1', title: 'Plan task', status: 'pending' }],
          items: [{ id: 'PM-1', title: 'Plan task', status: 'pending' }],
          total: 1,
        },
      });

      const result = await pmService.listPmTasks({
        status: 'pending',
        assignee: 'pm',
        limit: 20,
        offset: 5,
        workspace: 'C:/Temp/Product',
      });

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/tasks?status=pending&assignee=pm&limit=20&offset=5&workspace=C%3A%2FTemp%2FProduct',
        'Failed to list PM tasks',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.tasks?.[0].id).toBe('PM-1');
    });

    it('should use default PM task list pagination', async () => {
      mockApiGet.mockResolvedValueOnce({ ok: true, data: { ok: true, tasks: [], total: 0 } });

      await pmService.listPmTasks();

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/tasks?limit=100&offset=0',
        'Failed to list PM tasks',
      );
    });
  });

  describe('getPmTask', () => {
    it('should load a PM task detail with an encoded task id', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { id: 'PM/task 1', title: 'Task detail', status: 'pending' },
      });

      await pmService.getPmTask('PM/task 1', 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/tasks/PM%2Ftask%201?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load PM task',
      );
    });
  });

  describe('listPmTaskAssignments', () => {
    it('should load PM task assignments with an encoded task id and limit', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          task_id: 'PM/task 1',
          assignments: [{ id: 'assign-1', assignee: 'director', status: 'assigned' }],
          count: 1,
        },
      });

      const result = await pmService.listPmTaskAssignments('PM/task 1', 25, 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/tasks/PM%2Ftask%201/assignments?limit=25&workspace=C%3A%2FTemp%2FProduct',
        'Failed to list PM task assignments',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.assignments[0].assignee).toBe('director');
    });

    it('should use default PM task assignment limit', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { task_id: 'PM-1', assignments: [], count: 0 },
      });

      await pmService.listPmTaskAssignments('PM-1');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/tasks/PM-1/assignments?limit=100',
        'Failed to list PM task assignments',
      );
    });
  });

  describe('PM requirements', () => {
    it('should call PM requirement list endpoint with filters', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          requirements: [{ id: 'REQ-1', title: 'Traceable requirement', status: 'open' }],
          items: [{ id: 'REQ-1', title: 'Traceable requirement', status: 'open' }],
          total: 1,
        },
      });

      const result = await pmService.listPmRequirements({
        status: 'open',
        priority: 'high',
        limit: 20,
        offset: 5,
        workspace: 'C:/Temp/Product',
      });

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/requirements?status=open&priority=high&limit=20&offset=5&workspace=C%3A%2FTemp%2FProduct',
        'Failed to list PM requirements',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.requirements?.[0].id).toBe('REQ-1');
    });

    it('should use default PM requirement list pagination', async () => {
      mockApiGet.mockResolvedValueOnce({ ok: true, data: { ok: true, requirements: [], total: 0 } });

      await pmService.listPmRequirements();

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/requirements?limit=100&offset=0',
        'Failed to list PM requirements',
      );
    });

    it('should load a PM requirement detail with an encoded requirement id', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { id: 'REQ/1', title: 'Requirement detail', status: 'open' },
      });

      await pmService.getPmRequirement('REQ/1', 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/requirements/REQ%2F1?workspace=C%3A%2FTemp%2FProduct',
        'Failed to load PM requirement',
      );
    });
  });

  describe('listPmDirectorTaskHistory', () => {
    it('should call PM Director task history endpoint with iteration filter', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          iterations: [{ iteration: 2, tasks: [{ id: 'director-task-1' }] }],
          pagination: { total: 1 },
        },
      });

      const result = await pmService.listPmDirectorTaskHistory({
        iteration: 2,
        limit: 10,
        offset: 3,
        workspace: 'C:/Temp/Product',
      });

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/tasks/director?iteration=2&limit=10&offset=3&workspace=C%3A%2FTemp%2FProduct',
        'Failed to list PM Director task history',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.iterations?.[0].iteration).toBe(2);
    });

    it('should use default Director history pagination', async () => {
      mockApiGet.mockResolvedValueOnce({ ok: true, data: { iterations: [] } });

      await pmService.listPmDirectorTaskHistory();

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/tasks/director?limit=25&offset=0',
        'Failed to list PM Director task history',
      );
    });
  });

  describe('searchPmTasks', () => {
    it('should call PM task search endpoint with encoded query', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          query: 'quality gate',
          results: [{ id: 'PM-1', title: 'Quality gate task' }],
          count: 1,
        },
      });

      const result = await pmService.searchPmTasks('quality gate', 9, 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/search/tasks?q=quality+gate&limit=9&workspace=C%3A%2FTemp%2FProduct',
        'Failed to search PM tasks',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.results[0].id).toBe('PM-1');
    });

    it('should use default PM task search limit', async () => {
      mockApiGet.mockResolvedValueOnce({ ok: true, data: { query: 'plan', results: [], count: 0 } });

      await pmService.searchPmTasks('plan');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/search/tasks?q=plan&limit=20',
        'Failed to search PM tasks',
      );
    });
  });

  describe('pmDocumentService.search', () => {
    it('should list PM documents with explicit workspace', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: { documents: [], pagination: { total: 0 } },
      });

      const result = await pmService.pmDocumentService.list('C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/documents?workspace=C%3A%2FTemp%2FProduct',
        'Failed to list PM documents',
      );
      expect(result.ok).toBe(true);
    });

    it('should save PM documents with explicit workspace', async () => {
      mockApiPost.mockResolvedValueOnce({
        ok: true,
        data: { success: true, path: 'docs/product plan.md', version: '3' },
      });

      const result = await pmService.pmDocumentService.save(
        'docs/product plan.md',
        '# Updated',
        'verified update',
        'C:/Temp/Product',
      );

      expect(mockApiPost).toHaveBeenCalledWith(
        '/v2/pm/documents/docs/product%20plan.md?workspace=C%3A%2FTemp%2FProduct',
        { content: '# Updated', change_summary: 'verified update' },
        'Failed to save PM document',
      );
      expect(result.ok).toBe(true);
    });

    it('should call PM document search endpoint with encoded query', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          query: 'quality gate',
          results: [{ path: 'docs/plan.md', snippet: 'quality gate passed' }],
          count: 1,
        },
      });

      const result = await pmService.pmDocumentService.search('quality gate', 12, 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/search/documents?q=quality+gate&limit=12&workspace=C%3A%2FTemp%2FProduct',
        'Failed to search PM documents',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.results[0].path).toBe('docs/plan.md');
    });

    it('should use default PM document search limit', async () => {
      mockApiGet.mockResolvedValueOnce({ ok: true, data: { query: 'plan', results: [], count: 0 } });

      await pmService.pmDocumentService.search('plan');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/search/documents?q=plan&limit=20',
        'Failed to search PM documents',
      );
    });
  });

  describe('pmDocumentService versions and compare', () => {
    it('should call PM document detail endpoint with encoded version query', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          path: 'docs/product plan.md',
          current_version: '2',
          version_count: 2,
          last_modified: 'now',
          created_at: 'then',
          content: '# Historical',
        },
      });

      const result = await pmService.pmDocumentService.get('docs/product plan.md', '1.0', 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/documents/docs/product%20plan.md?version=1.0&workspace=C%3A%2FTemp%2FProduct',
        'Failed to read PM document',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.content).toBe('# Historical');
    });

    it('should call PM document versions endpoint with encoded path', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          path: 'docs/product plan.md',
          versions: [{ version: '1', created_at: 'now', created_by: 'pm', change_summary: 'init', checksum: 'abc' }],
        },
      });

      const result = await pmService.pmDocumentService.versions('docs/product plan.md', 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/documents/docs/product%20plan.md/versions?workspace=C%3A%2FTemp%2FProduct',
        'Failed to list PM document versions',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.versions[0].version).toBe('1');
    });

    it('should call PM document compare endpoint with encoded versions', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          path: 'docs/product plan.md',
          old_version: '1',
          new_version: '2',
          diff_text: '+changed',
          changed_sections: [],
          added_requirements: [],
          removed_requirements: [],
          impact_score: 0.2,
        },
      });

      const result = await pmService.pmDocumentService.compare('docs/product plan.md', '1', '2', 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith(
        '/v2/pm/documents/docs/product%20plan.md/compare?old_version=1&new_version=2&workspace=C%3A%2FTemp%2FProduct',
        'Failed to compare PM document versions',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.diff_text).toBe('+changed');
    });
  });

  describe('pmDocumentService delete', () => {
    it('should call PM document delete endpoint with encoded path and delete_file flag', async () => {
      mockApiDelete.mockResolvedValueOnce({
        ok: true,
        data: {
          success: true,
          path: 'docs/product plan.md',
          deleted: true,
        },
      });

      const result = await pmService.pmDocumentService.delete('docs/product plan.md', false, 'C:/Temp/Product');

      expect(mockApiDelete).toHaveBeenCalledWith(
        '/v2/pm/documents/docs/product%20plan.md?delete_file=false&workspace=C%3A%2FTemp%2FProduct',
        'Failed to delete PM document',
      );
      expect(result.ok).toBe(true);
      expect(result.data?.deleted).toBe(true);
    });
  });

  describe('createDirectorTask', () => {
    it('should call apiPost with correct path and payload', async () => {
      const payload = {
        subject: 'New Task',
        description: 'Task description',
        priority: 'HIGH' as const,
        timeout_seconds: 300,
        metadata: {
          pm_task_id: 'pm-task-1',
          pm_task_title: 'PM Task',
          pm_task_status: 'IN_PROGRESS',
          acceptance: ['Acceptance 1'],
        },
      };

      mockApiPost.mockResolvedValueOnce({
        ok: true,
        data: { id: 'director-task-1', ...payload },
      });

      const result = await pmService.createDirectorTask(payload);

      expect(mockApiPost).toHaveBeenCalledWith('/v2/director/tasks', payload, 'Failed to create Director task');
      expect(result.ok).toBe(true);
      expect(result.data?.id).toBe('director-task-1');
    });

    it('should call apiPost with explicit workspace when creating a Director task', async () => {
      const payload = {
        subject: 'New Task',
        description: 'Task description',
        priority: 'HIGH' as const,
        timeout_seconds: 300,
        metadata: {
          pm_task_id: 'pm-task-1',
        },
      };

      mockApiPost.mockResolvedValueOnce({
        ok: true,
        data: { id: 'director-task-1', ...payload },
      });

      const result = await pmService.createDirectorTask(payload, 'C:/Temp/Product');

      expect(mockApiPost).toHaveBeenCalledWith(
        '/v2/director/tasks?workspace=C%3A%2FTemp%2FProduct',
        payload,
        'Failed to create Director task',
      );
      expect(result.ok).toBe(true);
    });

    it('should return error on API failure', async () => {
      const payload = {
        subject: 'New Task',
        description: 'Task description',
        priority: 'HIGH' as const,
        timeout_seconds: 300,
        metadata: {
          pm_task_id: 'pm-task-1',
          pm_task_title: 'PM Task',
          pm_task_status: 'IN_PROGRESS',
          acceptance: ['Acceptance 1'],
        },
      };

      mockApiPost.mockResolvedValueOnce({
        ok: false,
        error: 'Failed to create task',
      });

      const result = await pmService.createDirectorTask(payload);

      expect(result.ok).toBe(false);
      expect(result.error).toBe('Failed to create task');
    });
  });

  // Note: Type exports cannot be tested at runtime in TypeScript
  // These are compile-time only and don't exist at runtime
  describe('Module exports', () => {
    it('should export service functions', () => {
      expect(typeof pmService.getPmStatus).toBe('function');
      expect(typeof pmService.getDirectorStatus).toBe('function');
      expect(typeof pmService.startPm).toBe('function');
      expect(typeof pmService.stopPm).toBe('function');
      expect(typeof pmService.startDirector).toBe('function');
      expect(typeof pmService.stopDirector).toBe('function');
      expect(typeof pmService.listDirectorTasks).toBe('function');
      expect(typeof pmService.listDirectorTaskFallbackRows).toBe('function');
      expect(typeof pmService.resolveDirectorTaskSources).toBe('function');
      expect(typeof pmService.listPmTasks).toBe('function');
      expect(typeof pmService.getPmTask).toBe('function');
      expect(typeof pmService.listPmTaskAssignments).toBe('function');
      expect(typeof pmService.listPmRequirements).toBe('function');
      expect(typeof pmService.getPmRequirement).toBe('function');
      expect(typeof pmService.listPmTaskHistory).toBe('function');
      expect(typeof pmService.listPmDirectorTaskHistory).toBe('function');
      expect(typeof pmService.searchPmTasks).toBe('function');
      expect(typeof pmService.getDirectorCapabilities).toBe('function');
      expect(typeof pmService.createDirectorTask).toBe('function');
    });
  });
});
