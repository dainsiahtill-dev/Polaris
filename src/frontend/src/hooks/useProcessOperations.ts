import { useState, useCallback } from 'react';
import { toast } from 'sonner';
import {
  startPm,
  stopPm,
  runPmOnce,
  startDirector,
  stopDirector,
  listDirectorTasks,
  createDirectorTask,
  readLogTail,
  extractErrorDetail,
} from '@/services';
import type { PmTask } from '@/types/task';
import type { DirectorTask, CreateDirectorTaskPayload } from '@/services';

export interface ProcessOperationsState {
  isStartingPM: boolean;
  isStoppingPM: boolean;
  isStartingDirector: boolean;
  isStoppingDirector: boolean;
  pmActionError: string | null;
  directorActionError: string | null;
}

export interface UseProcessOperationsOptions {
  onStatusChange?: () => void;
  onOpenLogs?: (sourceId: string, banner: string) => void;
  lancedbBlocked?: boolean;
  lancedbBlockMessage?: string;
}

interface DirectorStartGuards {
  required: boolean;
  draftReady: boolean;
}

function isPmTaskDone(task: PmTask): boolean {
  const rawStatus = String(task.status || task.state || '').trim().toLowerCase();
  return Boolean(task.done || task.completed || ['done', 'completed', 'success', 'passed'].includes(rawStatus));
}

function toDirectorPriority(task: PmTask): 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' {
  const numeric = Number(task.priority);
  if (Number.isFinite(numeric)) {
    if (numeric <= 0) return 'CRITICAL';
    if (numeric <= 1) return 'HIGH';
    if (numeric <= 2) return 'MEDIUM';
  }
  return 'LOW';
}

function normalizePmTaskId(task: PmTask): string {
  return String(task.id || '').trim();
}

function buildDirectorTaskPayload(task: PmTask): CreateDirectorTaskPayload {
  const title = String(task.title || task.goal || task.id || '未命名任务').trim();
  const description = String(task.summary || task.goal || '').trim();
  const acceptance = Array.isArray(task.acceptance)
    ? task.acceptance
        .map((item) => {
          if (!item || typeof item !== 'object') return '';
          return String(item.description || '').trim();
        })
        .filter((item) => item.length > 0)
    : [];

  return {
    subject: title,
    description,
    priority: toDirectorPriority(task),
    timeout_seconds: 600,
    metadata: {
      pm_task_id: normalizePmTaskId(task),
      pm_task_title: title,
      pm_task_status: String(task.status || task.state || '').trim(),
      acceptance,
    },
  };
}

export function useProcessOperations(options: UseProcessOperationsOptions = {}) {
  const { onStatusChange, onOpenLogs, lancedbBlocked, lancedbBlockMessage } = options;

  const [state, setState] = useState<ProcessOperationsState>({
    isStartingPM: false,
    isStoppingPM: false,
    isStartingDirector: false,
    isStoppingDirector: false,
    pmActionError: null,
    directorActionError: null,
  });

  const setField = useCallback(<K extends keyof ProcessOperationsState>(
    key: K,
    value: ProcessOperationsState[K]
  ) => {
    setState(prev => ({ ...prev, [key]: value }));
  }, []);

  const handleProcessError = useCallback(async (
    errorMessage: string,
    defaultLogPath: string,
    processType: 'pm' | 'director'
  ) => {
    const { getPmStatus, getDirectorStatus } = await import('@/services');

    let combined = errorMessage;
    try {
      const statusResult = processType === 'director'
        ? await getDirectorStatus()
        : await getPmStatus();

      if (statusResult.ok && statusResult.data) {
        const logPath = statusResult.data.log_path || defaultLogPath;
        const tail = await readLogTail(logPath, 20);
        if (tail) {
          combined = `${errorMessage}\n\n${tail}`;
        }
      }
    } catch {
      // Ignore errors when fetching log tail
    }

    return combined;
  }, []);

  const startPmLoop = useCallback(async (resume = false) => {
    setField('pmActionError', null);
    setField('isStartingPM', true);

    const startToastId = toast.loading(resume ? 'Resuming PM...' : 'Starting PM...', {
      duration: 5000,
    });

    try {
      if (lancedbBlocked) {
        toast.warning(lancedbBlockMessage || 'LanceDB is required to start PM.');
        toast.dismiss(startToastId);
        return false;
      }

      const result = await startPm(resume);

      if (!result.ok) {
        toast.dismiss(startToastId);
        const combined = await handleProcessError(
          result.error || 'Failed to start PM',
          'runtime/logs/pm.process.log',
          'pm'
        );
        onOpenLogs?.('pm-subprocess', combined);
        toast.error('Failed to start PM');
        return false;
      }

      toast.dismiss(startToastId);
      toast.success(resume ? 'PM resumed' : 'PM started');
      onStatusChange?.();
      return true;
    } catch (err) {
      toast.dismiss(startToastId);
      const message = err instanceof Error ? err.message : 'PM operation failed';
      onOpenLogs?.('pm-subprocess', message);
      toast.error(message);
      return false;
    } finally {
      setField('isStartingPM', false);
    }
  }, [lancedbBlocked, lancedbBlockMessage, handleProcessError, onOpenLogs, onStatusChange, setField]);

  const stopPmCallback = useCallback(async () => {
    setField('pmActionError', null);
    setField('isStoppingPM', true);

    try {
      const result = await stopPm();
      if (!result.ok) {
        throw new Error(result.error || 'Failed to stop PM');
      }
      onStatusChange?.();
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'PM operation failed';
      setField('pmActionError', message);
      toast.error(message);
      return false;
    } finally {
      setField('isStoppingPM', false);
    }
  }, [onStatusChange, setField]);

  const togglePm = useCallback(async (isRunning: boolean) => {
    if (isRunning) {
      return stopPmCallback();
    } else {
      return startPmLoop(false);
    }
  }, [startPmLoop, stopPmCallback]);

  const runPmOnceCallback = useCallback(async () => {
    setField('pmActionError', null);
    setField('isStartingPM', true);

    const startToastId = toast.loading('Running PM once...', {
      duration: 5000,
    });

    try {
      if (lancedbBlocked) {
        toast.warning(lancedbBlockMessage || 'LanceDB is required to run PM.');
        toast.dismiss(startToastId);
        return false;
      }

      const result = await runPmOnce();

      if (!result.ok) {
        toast.dismiss(startToastId);
        const combined = await handleProcessError(
          result.error || 'PM run once failed',
          'runtime/logs/pm.process.log',
          'pm'
        );
        onOpenLogs?.('pm-subprocess', combined);
        toast.error('PM run once failed');
        return false;
      }

      toast.dismiss(startToastId);
      toast.success('PM run once started');
      onStatusChange?.();
      return true;
    } catch (err) {
      toast.dismiss(startToastId);
      const message = err instanceof Error ? err.message : 'PM run once failed';
      onOpenLogs?.('pm-subprocess', message);
      toast.error(message);
      return false;
    } finally {
      setField('isStartingPM', false);
    }
  }, [lancedbBlocked, lancedbBlockMessage, handleProcessError, onOpenLogs, onStatusChange, setField]);

  const seedDirectorQueueFromPmTasks = useCallback(async (tasks?: PmTask[]) => {
    const candidates = Array.isArray(tasks)
      ? tasks.filter((task) => task && typeof task === 'object' && !isPmTaskDone(task))
      : [];
    if (!candidates.length) {
      return true;
    }

    const existingResult = await listDirectorTasks('local');
    let existingTaskIds = new Set<string>();

    if (existingResult.ok && existingResult.data) {
      const tasks = existingResult.data;
      const ids = tasks
        .filter((item) => {
          const workflowState = String(item?.metadata?.workflow_state || '').trim();
          return workflowState.length === 0;
        })
        .map((item) => String(item?.metadata?.pm_task_id || '').trim())
        .filter((item) => item.length > 0);
      existingTaskIds = new Set(ids);
    }

    for (const task of candidates) {
      const pmTaskId = normalizePmTaskId(task);
      if (pmTaskId && existingTaskIds.has(pmTaskId)) {
        continue;
      }

      const result = await createDirectorTask(buildDirectorTaskPayload(task));
      if (!result.ok) {
        throw new Error(result.error || '同步 PM 任务到 Director 队列失败');
      }
      if (pmTaskId) {
        existingTaskIds.add(pmTaskId);
      }
    }

    return true;
  }, []);

  const startDirectorCallback = useCallback(async (
    checkAgents?: DirectorStartGuards,
    tasks?: PmTask[],
  ) => {
    setField('directorActionError', null);
    setField('isStartingDirector', true);

    const startToastId = toast.loading('Starting Chief Engineer...', {
      duration: 5000,
    });

    try {
      if (checkAgents?.required) {
        toast.dismiss(startToastId);
        if (checkAgents.draftReady) {
          toast.warning('Please review and confirm AGENTS.generated.md before starting Chief Engineer.');
        } else {
          toast.warning('Please run PM first to read the spec and generate AGENTS.generated.md.');
        }
        return false;
      }

      if (lancedbBlocked) {
        toast.dismiss(startToastId);
        toast.warning(lancedbBlockMessage || 'LanceDB is required to start Chief Engineer.');
        return false;
      }

      const result = await startDirector();

      if (!result.ok) {
        toast.dismiss(startToastId);
        const combined = await handleProcessError(
          result.error || 'Failed to start Chief Engineer',
          'runtime/logs/director.process.log',
          'director'
        );
        onOpenLogs?.('director', combined);
        toast.error('Failed to start Chief Engineer');
        return false;
      }

      await seedDirectorQueueFromPmTasks(tasks);

      toast.dismiss(startToastId);
      toast.success('Chief Engineer started');
      onStatusChange?.();
      return true;
    } catch (err) {
      toast.dismiss(startToastId);
      const message = err instanceof Error ? err.message : 'Chief Engineer operation failed';
      setField('directorActionError', message);
      toast.error(message);
      return false;
    } finally {
      setField('isStartingDirector', false);
    }
  }, [lancedbBlocked, lancedbBlockMessage, handleProcessError, onOpenLogs, onStatusChange, seedDirectorQueueFromPmTasks, setField]);

  const stopDirectorCallback = useCallback(async () => {
    setField('directorActionError', null);
    setField('isStoppingDirector', true);

    try {
      const result = await stopDirector();
      if (!result.ok) {
        throw new Error(result.error || 'Failed to stop Chief Engineer');
      }
      onStatusChange?.();
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Chief Engineer operation failed';
      setField('directorActionError', message);
      toast.error(message);
      return false;
    } finally {
      setField('isStoppingDirector', false);
    }
  }, [onStatusChange, setField]);

  const toggleDirector = useCallback(async (
    isRunning: boolean,
    checkAgents?: DirectorStartGuards,
    tasks?: PmTask[],
  ) => {
    if (isRunning) {
      return stopDirectorCallback();
    } else {
      return startDirectorCallback(checkAgents, tasks);
    }
  }, [startDirectorCallback, stopDirectorCallback]);

  return {
    ...state,
    startPmLoop,
    stopPm: stopPmCallback,
    togglePm,
    runPmOnce: runPmOnceCallback,
    startDirector: startDirectorCallback,
    stopDirector: stopDirectorCallback,
    toggleDirector,
    clearPmError: () => setField('pmActionError', null),
    clearDirectorError: () => setField('directorActionError', null),
  };
}
