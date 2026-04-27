import { renderHook, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useProcessOperations } from './useProcessOperations';

// Mock toast
vi.mock('sonner', () => ({
  toast: {
    loading: vi.fn().mockReturnValue('toast-id'),
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    dismiss: vi.fn(),
  },
}));

// Mock service functions
const mockStartPm = vi.fn();
const mockStopPm = vi.fn();
const mockRunPmOnce = vi.fn();
const mockStartDirector = vi.fn();
const mockStopDirector = vi.fn();
const mockListDirectorTasks = vi.fn();
const mockCreateDirectorTask = vi.fn();
const mockReadLogTail = vi.fn();
const mockExtractErrorDetail = vi.fn();
const mockGetPmStatus = vi.fn();
const mockGetDirectorStatus = vi.fn();

vi.mock('@/services', () => ({
  startPm: (...args: unknown[]) => mockStartPm(...args),
  stopPm: (...args: unknown[]) => mockStopPm(...args),
  runPmOnce: (...args: unknown[]) => mockRunPmOnce(...args),
  startDirector: (...args: unknown[]) => mockStartDirector(...args),
  stopDirector: (...args: unknown[]) => mockStopDirector(...args),
  listDirectorTasks: (...args: unknown[]) => mockListDirectorTasks(...args),
  createDirectorTask: (...args: unknown[]) => mockCreateDirectorTask(...args),
  readLogTail: (...args: unknown[]) => mockReadLogTail(...args),
  extractErrorDetail: (...args: unknown[]) => mockExtractErrorDetail(...args),
  getPmStatus: (...args: unknown[]) => mockGetPmStatus(...args),
  getDirectorStatus: (...args: unknown[]) => mockGetDirectorStatus(...args),
}));

// Import toast for spy
import { toast } from 'sonner';

describe('useProcessOperations', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockStartPm.mockResolvedValue({ ok: true });
    mockStopPm.mockResolvedValue({ ok: true });
    mockRunPmOnce.mockResolvedValue({ ok: true });
    mockStartDirector.mockResolvedValue({ ok: true });
    mockStopDirector.mockResolvedValue({ ok: true });
    mockListDirectorTasks.mockResolvedValue({ ok: true, data: [] });
    mockCreateDirectorTask.mockResolvedValue({ ok: true });
    mockReadLogTail.mockResolvedValue('');
    mockGetPmStatus.mockResolvedValue({ ok: true, data: { log_path: 'test.log' } });
    mockGetDirectorStatus.mockResolvedValue({ ok: true, data: { log_path: 'test.log' } });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('Initial State', () => {
    it('returns correct initial state', () => {
      const { result } = renderHook(() => useProcessOperations());

      expect(result.current.isStartingPM).toBe(false);
      expect(result.current.isStoppingPM).toBe(false);
      expect(result.current.isStartingDirector).toBe(false);
      expect(result.current.isStoppingDirector).toBe(false);
      expect(result.current.pmActionError).toBe(null);
      expect(result.current.directorActionError).toBe(null);
    });

    it('returns all handler functions', () => {
      const { result } = renderHook(() => useProcessOperations());

      expect(result.current.startPmLoop).toBeDefined();
      expect(result.current.stopPm).toBeDefined();
      expect(result.current.togglePm).toBeDefined();
      expect(result.current.runPmOnce).toBeDefined();
      expect(result.current.startDirector).toBeDefined();
      expect(result.current.stopDirector).toBeDefined();
      expect(result.current.toggleDirector).toBeDefined();
      expect(result.current.clearPmError).toBeDefined();
      expect(result.current.clearDirectorError).toBeDefined();
    });
  });

  describe('startPmLoop', () => {
    it('starts PM loop successfully', async () => {
      const { result } = renderHook(() => useProcessOperations());

      let successResult: boolean | undefined;
      await act(async () => {
        successResult = await result.current.startPmLoop();
      });

      expect(successResult).toBe(true);
      expect(mockStartPm).toHaveBeenCalledWith(false);
      expect(toast.success).toHaveBeenCalledWith('PM started');
      expect(result.current.isStartingPM).toBe(false);
    });

    it('shows loading toast when starting', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        await result.current.startPmLoop();
      });

      expect(toast.loading).toHaveBeenCalledWith('Starting PM...', expect.any(Object));
      expect(toast.dismiss).toHaveBeenCalledWith('toast-id');
    });

    it('returns false and opens logs on failure', async () => {
      mockStartPm.mockResolvedValue({ ok: false, error: 'Connection failed' });
      const onOpenLogs = vi.fn();
      const { result } = renderHook(() => useProcessOperations({ onOpenLogs }));

      await act(async () => {
        const success = await result.current.startPmLoop();
        expect(success).toBe(false);
      });

      expect(onOpenLogs).toHaveBeenCalledWith('pm-subprocess', expect.any(String));
      expect(toast.error).toHaveBeenCalledWith('Failed to start PM');
    });

    it('returns false when lancedbBlocked', async () => {
      const { result } = renderHook(() =>
        useProcessOperations({ lancedbBlocked: true, lancedbBlockMessage: 'LanceDB is required' })
      );

      await act(async () => {
        const success = await result.current.startPmLoop();
        expect(success).toBe(false);
      });

      expect(mockStartPm).not.toHaveBeenCalled();
      expect(toast.warning).toHaveBeenCalledWith('LanceDB is required');
    });

    it('resumes PM loop when resume is true', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.startPmLoop(true);
        expect(success).toBe(true);
      });

      expect(toast.loading).toHaveBeenCalledWith('Resuming PM...', expect.any(Object));
      expect(toast.success).toHaveBeenCalledWith('PM resumed');
    });

    it('calls onStatusChange on success', async () => {
      const onStatusChange = vi.fn();
      const { result } = renderHook(() => useProcessOperations({ onStatusChange }));

      await act(async () => {
        await result.current.startPmLoop();
      });

      expect(onStatusChange).toHaveBeenCalledTimes(1);
    });

    it('handles exceptions gracefully', async () => {
      mockStartPm.mockRejectedValue(new Error('Network error'));
      const onOpenLogs = vi.fn();
      const { result } = renderHook(() => useProcessOperations({ onOpenLogs }));

      await act(async () => {
        const success = await result.current.startPmLoop();
        expect(success).toBe(false);
      });

      expect(onOpenLogs).toHaveBeenCalledWith('pm-subprocess', 'Network error');
      expect(toast.error).toHaveBeenCalledWith('Network error');
    });
  });

  describe('stopPm', () => {
    it('stops PM successfully', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.stopPm();
        expect(success).toBe(true);
      });

      expect(mockStopPm).toHaveBeenCalledWith();
    });

    it('returns false on failure', async () => {
      mockStopPm.mockResolvedValue({ ok: false, error: 'Stop failed' });
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.stopPm();
        expect(success).toBe(false);
      });

      expect(result.current.pmActionError).toBe('Stop failed');
      expect(toast.error).toHaveBeenCalledWith('Stop failed');
    });

    it('calls onStatusChange on success', async () => {
      const onStatusChange = vi.fn();
      const { result } = renderHook(() => useProcessOperations({ onStatusChange }));

      await act(async () => {
        await result.current.stopPm();
      });

      expect(onStatusChange).toHaveBeenCalledTimes(1);
    });
  });

  describe('togglePm', () => {
    it('starts PM when not running', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.togglePm(false);
        expect(success).toBe(true);
      });

      expect(mockStartPm).toHaveBeenCalled();
    });

    it('stops PM when running', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.togglePm(true);
        expect(success).toBe(true);
      });

      expect(mockStopPm).toHaveBeenCalled();
    });
  });

  describe('runPmOnce', () => {
    it('runs PM once successfully', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.runPmOnce();
        expect(success).toBe(true);
      });

      expect(mockRunPmOnce).toHaveBeenCalledWith();
      expect(toast.success).toHaveBeenCalledWith('Run Once started');
    });

    it('returns false when lancedbBlocked', async () => {
      const { result } = renderHook(() =>
        useProcessOperations({ lancedbBlocked: true })
      );

      await act(async () => {
        const success = await result.current.runPmOnce();
        expect(success).toBe(false);
      });

      expect(mockRunPmOnce).not.toHaveBeenCalled();
      expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining('LanceDB'));
    });
  });

  describe('clearPmError', () => {
    it('clears PM action error', async () => {
      mockStopPm.mockResolvedValue({ ok: false, error: 'Error' });
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        await result.current.stopPm();
      });

      expect(result.current.pmActionError).toBe('Error');

      await act(async () => {
        result.current.clearPmError();
      });

      expect(result.current.pmActionError).toBe(null);
    });
  });

  describe('startDirector', () => {
    it('starts director successfully', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.startDirector();
        expect(success).toBe(true);
      });

      expect(mockStartDirector).toHaveBeenCalledWith();
      expect(toast.success).toHaveBeenCalledWith('Chief Engineer started');
    });

    it('returns false when checkAgents.required and not draftReady', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.startDirector({
          required: true,
          draftReady: false,
        });
        expect(success).toBe(false);
      });

      expect(mockStartDirector).not.toHaveBeenCalled();
      expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining('AGENTS.generated.md'));
    });

    it('returns false when checkAgents.required and draftReady but not confirmed', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.startDirector({
          required: true,
          draftReady: true,
        });
        expect(success).toBe(false);
      });

      expect(toast.warning).toHaveBeenCalledWith('Please review and confirm AGENTS.generated.md before starting Chief Engineer.');
    });

    it('seeds director queue from PM tasks', async () => {
      const tasks = [
        {
          id: 'task-1',
          title: 'Task 1',
          status: 'pending',
          priority: 1,
        },
      ];
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        await result.current.startDirector(undefined, tasks);
      });

      expect(mockListDirectorTasks).toHaveBeenCalledWith('local');
      expect(mockCreateDirectorTask).toHaveBeenCalled();
    });

    it('does not seed duplicate tasks', async () => {
      mockListDirectorTasks.mockResolvedValue({
        ok: true,
        data: [{ metadata: { pm_task_id: 'task-1' } }],
      });

      const tasks = [
        {
          id: 'task-1',
          title: 'Task 1',
          status: 'pending',
        },
      ];
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        await result.current.startDirector(undefined, tasks);
      });

      expect(mockCreateDirectorTask).not.toHaveBeenCalled();
    });

    it('skips completed tasks', async () => {
      const tasks = [
        {
          id: 'task-1',
          title: 'Task 1',
          status: 'completed',
          done: true,
        },
      ];
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        await result.current.startDirector(undefined, tasks);
      });

      expect(mockCreateDirectorTask).not.toHaveBeenCalled();
    });
  });

  describe('stopDirector', () => {
    it('stops director successfully', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.stopDirector();
        expect(success).toBe(true);
      });

      expect(mockStopDirector).toHaveBeenCalledWith();
    });

    it('returns false on failure', async () => {
      mockStopDirector.mockResolvedValue({ ok: false, error: 'Stop failed' });
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.stopDirector();
        expect(success).toBe(false);
      });

      expect(result.current.directorActionError).toBe('Stop failed');
    });
  });

  describe('toggleDirector', () => {
    it('starts director when not running', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.toggleDirector(false);
        expect(success).toBe(true);
      });

      expect(mockStartDirector).toHaveBeenCalled();
    });

    it('stops director when running', async () => {
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        const success = await result.current.toggleDirector(true);
        expect(success).toBe(true);
      });

      expect(mockStopDirector).toHaveBeenCalled();
    });
  });

  describe('clearDirectorError', () => {
    it('clears director action error', async () => {
      mockStopDirector.mockResolvedValue({ ok: false, error: 'Error' });
      const { result } = renderHook(() => useProcessOperations());

      await act(async () => {
        await result.current.stopDirector();
      });

      expect(result.current.directorActionError).toBe('Error');

      await act(async () => {
        result.current.clearDirectorError();
      });

      expect(result.current.directorActionError).toBe(null);
    });
  });

  describe('Loading States', () => {
    it('sets isStartingPM during start', async () => {
      let resolveStartPm: (value: { ok: boolean }) => void;
      mockStartPm.mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveStartPm = resolve;
          })
      );

      const { result } = renderHook(() => useProcessOperations());

      act(() => {
        result.current.startPmLoop();
      });

      expect(result.current.isStartingPM).toBe(true);

      await act(async () => {
        resolveStartPm!({ ok: true });
      });

      expect(result.current.isStartingPM).toBe(false);
    });

    it('sets isStoppingPM during stop', async () => {
      let resolveStopPm: (value: { ok: boolean }) => void;
      mockStopPm.mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveStopPm = resolve;
          })
      );

      const { result } = renderHook(() => useProcessOperations());

      act(() => {
        result.current.stopPm();
      });

      expect(result.current.isStoppingPM).toBe(true);

      await act(async () => {
        resolveStopPm!({ ok: true });
      });

      expect(result.current.isStoppingPM).toBe(false);
    });

    it('sets isStartingDirector during start', async () => {
      let resolveStartDirector: (value: { ok: boolean }) => void;
      mockStartDirector.mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveStartDirector = resolve;
          })
      );

      const { result } = renderHook(() => useProcessOperations());

      act(() => {
        result.current.startDirector();
      });

      expect(result.current.isStartingDirector).toBe(true);

      await act(async () => {
        resolveStartDirector!({ ok: true });
      });

      expect(result.current.isStartingDirector).toBe(false);
    });

    it('sets isStoppingDirector during stop', async () => {
      let resolveStopDirector: (value: { ok: boolean }) => void;
      mockStopDirector.mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveStopDirector = resolve;
          })
      );

      const { result } = renderHook(() => useProcessOperations());

      act(() => {
        result.current.stopDirector();
      });

      expect(result.current.isStoppingDirector).toBe(true);

      await act(async () => {
        resolveStopDirector!({ ok: true });
      });

      expect(result.current.isStoppingDirector).toBe(false);
    });
  });
});
