/** Task Selectors Module
 *
 * Contains pure functions for task selection, identity resolution,
 * telemetry computation, and patch analysis for the Director Workspace.
 */

import type { PmTask } from '@/types/task';
import type { FileEditEvent } from '@/app/hooks/useRuntime';

export interface TaskLineStats {
  added: number;
  deleted: number;
  modified: number;
}

export interface TaskOperationStats {
  create: number;
  modify: number;
  delete: number;
}

export interface TaskRealtimeTelemetry {
  currentFilePath?: string;
  activityUpdatedAt?: string;
  filesTouchedCount: number;
  lineStats: TaskLineStats;
  operationStats: TaskOperationStats;
  retryCount?: number;
  maxRetries?: number;
  currentPhase?: string;
  phaseIndex?: number;
  phaseTotal?: number;
}

interface TaskRealtimeTelemetryAccumulator {
  currentFilePath?: string;
  activityUpdatedAt?: string;
  filesTouched: Set<string>;
  lineStats: TaskLineStats;
  operationStats: TaskOperationStats;
  retryCount?: number;
  maxRetries?: number;
  currentPhase?: string;
  phaseIndex?: number;
  phaseTotal?: number;
}

function readTaskMetadata(task: PmTask): Record<string, unknown> {
  return task.metadata && typeof task.metadata === 'object'
    ? task.metadata
    : {};
}

function readTaskString(task: PmTask, keys: string[]): string {
  for (const key of keys) {
    const directValue = (task as unknown as Record<string, unknown>)[key];
    if (typeof directValue === 'string' && directValue.trim()) {
      return directValue.trim();
    }
    const metadataValue = readTaskMetadata(task)[key];
    if (typeof metadataValue === 'string' && metadataValue.trim()) {
      return metadataValue.trim();
    }
  }
  return '';
}

function toTaskToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function toNonNegativeInt(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0, Math.round(numeric)) : 0;
}

export function resolveTaskIdentityCandidates(task: PmTask): string[] {
  const metadata = readTaskMetadata(task);
  const candidates = [
    task.id,
    task.title,
    task.goal,
    readTaskString(task, ['pm_task_id', 'task_id', 'id']),
    metadata.pm_task_id,
    metadata.task_id,
    metadata.id,
  ];
  const normalized: string[] = [];
  const seen = new Set<string>();
  for (const candidate of candidates) {
    const token = toTaskToken(candidate);
    if (!token || seen.has(token)) {
      continue;
    }
    seen.add(token);
    normalized.push(token);
  }
  return normalized;
}

export function computePatchLineStats(
  patch: string | undefined,
  operation: FileEditEvent['operation'],
): TaskLineStats {
  const text = String(patch || '');
  if (!text) {
    return { added: 0, deleted: 0, modified: 0 };
  }
  const lines = text.split('\n');
  const hasDiffMarkers = lines.some((line) => line.startsWith('@@') || line.startsWith('+++ ') || line.startsWith('--- '));
  if (!hasDiffMarkers) {
    const rawLineCount = lines.filter((line) => line.trim().length > 0).length;
    if (operation === 'delete') {
      return { added: 0, deleted: rawLineCount, modified: 0 };
    }
    return { added: rawLineCount, deleted: 0, modified: 0 };
  }

  let plus = 0;
  let minus = 0;
  for (const line of lines) {
    if (!line) continue;
    if (line.startsWith('+++ ') || line.startsWith('--- ') || line.startsWith('@@')) {
      continue;
    }
    if (line.startsWith('+')) {
      plus += 1;
      continue;
    }
    if (line.startsWith('-')) {
      minus += 1;
    }
  }
  const modified = Math.min(plus, minus);
  return {
    added: Math.max(0, plus - modified),
    deleted: Math.max(0, minus - modified),
    modified,
  };
}

function resolveEventLineStats(event: FileEditEvent): TaskLineStats {
  const backendStats = {
    added: toNonNegativeInt(event.addedLines),
    deleted: toNonNegativeInt(event.deletedLines),
    modified: toNonNegativeInt(event.modifiedLines),
  };
  if (backendStats.added > 0 || backendStats.deleted > 0 || backendStats.modified > 0) {
    return backendStats;
  }
  return computePatchLineStats(event.patch, event.operation);
}

export function buildTaskRealtimeTelemetry(
  tasks: PmTask[],
  fileEditEvents: FileEditEvent[],
  taskProgressMap?: Map<string, {
    phase?: string;
    phaseIndex?: number;
    phaseTotal?: number;
    retryCount?: number;
    maxRetries?: number;
    currentFile?: string;
  }>,
): Map<string, TaskRealtimeTelemetry> {
  const tokenToTaskId = new Map<string, string>();
  const taskIdSet = new Set<string>();
  for (const task of tasks) {
    const taskId = String(task.id || '').trim();
    if (!taskId) {
      continue;
    }
    taskIdSet.add(taskId);
    const candidates = resolveTaskIdentityCandidates(task);
    for (const token of candidates) {
      tokenToTaskId.set(token, taskId);
    }
  }

  const accumulators = new Map<string, TaskRealtimeTelemetryAccumulator>();

  // Process file edit events
  for (const event of fileEditEvents) {
    const rawTaskId = String(event.taskId || '').trim();
    if (!rawTaskId) {
      continue;
    }
    const mappedTaskId = tokenToTaskId.get(toTaskToken(rawTaskId)) || rawTaskId;
    if (!taskIdSet.has(mappedTaskId)) {
      continue;
    }
    const accumulator = accumulators.get(mappedTaskId) || {
      filesTouched: new Set<string>(),
      lineStats: { added: 0, deleted: 0, modified: 0 },
      operationStats: { create: 0, modify: 0, delete: 0 },
    };
    const lineStats = resolveEventLineStats(event);
    accumulator.lineStats.added += lineStats.added;
    accumulator.lineStats.deleted += lineStats.deleted;
    accumulator.lineStats.modified += lineStats.modified;
    accumulator.operationStats[event.operation] += 1;
    if (event.filePath) {
      accumulator.filesTouched.add(event.filePath);
    }

    const previousEpoch = Date.parse(String(accumulator.activityUpdatedAt || ''));
    const nextEpoch = Date.parse(String(event.timestamp || ''));
    const shouldReplaceCurrentFile = !Number.isFinite(previousEpoch)
      || (Number.isFinite(nextEpoch) && nextEpoch >= previousEpoch);
    if (shouldReplaceCurrentFile) {
      accumulator.currentFilePath = event.filePath || accumulator.currentFilePath;
      accumulator.activityUpdatedAt = event.timestamp || accumulator.activityUpdatedAt;
    }

    accumulators.set(mappedTaskId, accumulator);
  }

  // Merge in task progress data
  if (taskProgressMap) {
    for (const [taskId, progress] of taskProgressMap.entries()) {
      if (!taskIdSet.has(taskId)) {
        continue;
      }
      const accumulator = accumulators.get(taskId) || {
        filesTouched: new Set<string>(),
        lineStats: { added: 0, deleted: 0, modified: 0 },
        operationStats: { create: 0, modify: 0, delete: 0 },
      };

      if (progress.retryCount !== undefined) {
        accumulator.retryCount = progress.retryCount;
      }
      if (progress.maxRetries !== undefined) {
        accumulator.maxRetries = progress.maxRetries;
      }
      if (progress.phase) {
        accumulator.currentPhase = progress.phase;
      }
      if (progress.phaseIndex !== undefined) {
        accumulator.phaseIndex = progress.phaseIndex;
      }
      if (progress.phaseTotal !== undefined) {
        accumulator.phaseTotal = progress.phaseTotal;
      }
      if (progress.currentFile) {
        accumulator.currentFilePath = progress.currentFile;
      }

      accumulators.set(taskId, accumulator);
    }
  }

  const telemetry = new Map<string, TaskRealtimeTelemetry>();
  for (const [taskId, accumulator] of accumulators.entries()) {
    telemetry.set(taskId, {
      currentFilePath: accumulator.currentFilePath,
      activityUpdatedAt: accumulator.activityUpdatedAt,
      filesTouchedCount: accumulator.filesTouched.size,
      lineStats: { ...accumulator.lineStats },
      operationStats: { ...accumulator.operationStats },
      retryCount: accumulator.retryCount,
      maxRetries: accumulator.maxRetries,
      currentPhase: accumulator.currentPhase,
      phaseIndex: accumulator.phaseIndex,
      phaseTotal: accumulator.phaseTotal,
    });
  }
  return telemetry;
}

export function formatTelemetryTime(value: string | undefined): string {
  if (!value) {
    return '';
  }
  const epoch = Date.parse(value);
  if (!Number.isFinite(epoch)) {
    return '';
  }
  return new Date(epoch).toLocaleTimeString();
}
