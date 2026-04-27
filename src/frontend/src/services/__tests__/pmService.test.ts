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

vi.mock('@/services/apiClient', () => ({
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
  });

  describe('startDirector', () => {
    it('should call apiPostEmpty with correct path', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      const result = await pmService.startDirector();

      expect(mockApiPostEmpty).toHaveBeenCalledWith('/v2/director/start', 'Failed to start Chief Engineer');
      expect(result.ok).toBe(true);
    });
  });

  describe('stopDirector', () => {
    it('should call apiPostEmpty with correct path', async () => {
      mockApiPostEmpty.mockResolvedValueOnce({ ok: true });

      const result = await pmService.stopDirector();

      expect(mockApiPostEmpty).toHaveBeenCalledWith('/v2/director/stop', 'Failed to stop Chief Engineer');
      expect(result.ok).toBe(true);
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
      expect(typeof pmService.createDirectorTask).toBe('function');
    });
  });
});
