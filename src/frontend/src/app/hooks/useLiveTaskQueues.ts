import { useMemo } from 'react';
import type { PmTask } from '@/types/task';

export interface DirectorRealtimeFeed {
  tasks: PmTask[];
  isConnected: boolean;
  runId: string | null;
}

export interface LiveTaskQueues {
  pmTasks: PmTask[];
  directorTasks: PmTask[];
  directorTaskSource: 'realtime';
  isDirectorRealtimeConnected: boolean;
  isDirectorRealtimeReady: boolean;
}

interface UseLiveTaskQueuesOptions {
  snapshotTasks: PmTask[];
  directorRealtime: DirectorRealtimeFeed;
}

function normalizeTasks(tasks: PmTask[]): PmTask[] {
  if (!Array.isArray(tasks)) {
    return [];
  }
  return tasks
    .filter((task): task is PmTask => Boolean(task && typeof task === 'object'))
    .map((task) => normalizeTask(task));
}

function normalizeTaskStatus(value: unknown): string {
  const status = String(value || '').trim().toLowerCase();
  if (!status) {
    return 'pending';
  }
  if (status === 'done' || status === 'success' || status === 'completed') {
    return 'completed';
  }
  if (status === 'failed' || status === 'error') {
    return 'failed';
  }
  if (status === 'running' || status === 'in_progress' || status === 'claimed') {
    return 'in_progress';
  }
  if (status === 'blocked') {
    return 'blocked';
  }
  if (status === 'todo' || status === 'pending' || status === 'queued' || status === 'ready') {
    return 'pending';
  }
  return status;
}

function normalizeTask(task: PmTask): PmTask {
  const normalizedStatus = normalizeTaskStatus(task.status || task.state);
  const done = Boolean(task.done || task.completed || normalizedStatus === 'completed');
  return {
    ...task,
    status: normalizedStatus as PmTask['status'],
    state: normalizedStatus,
    done,
    completed: done,
  };
}

function normalizeTaskIdentity(value: unknown): string | null {
  // Missing optional identity is empty; malformed JSON values remain invalid
  // so another Director hint cannot turn them into an authorized projection.
  if (value === undefined || value === null) return '';
  return typeof value === 'string' ? value.trim().toLowerCase() : null;
}

function getTaskAssignees(task: PmTask): (string | null)[] {
  const metadata = task.metadata || {};
  const candidates = [
    task.assigned_to,
    task.assignedTo,
    task.assignee,
    metadata.assigned_to,
    metadata.assignedTo,
    metadata.assignee,
  ];

  return candidates
    .map(normalizeTaskIdentity)
    .filter((value) => value !== '');
}

export function getTaskAssignee(task: PmTask): string {
  return getTaskAssignees(task)[0] || '';
}

export function isDirectorAssignedTask(task: PmTask): boolean {
  const assignees = getTaskAssignees(task);
  return assignees.length > 0 && assignees.every((assignee) => assignee !== null && /^(?:lead\s+)?director$/.test(assignee));
}

function isDirectorRuntimeTask(task: PmTask): boolean {
  const record = task as PmTask & Record<string, unknown>;
  const metadata = task.metadata || {};
  const execution = metadata.runtime_execution && typeof metadata.runtime_execution === 'object'
    ? metadata.runtime_execution as Record<string, unknown>
    : {};
  const roles = [
    record.role,
    record.role_id,
    metadata.role,
    metadata.role_id,
    execution.role,
    execution.role_id,
  ].map(normalizeTaskIdentity).filter((value) => value !== '');

  // Shared TaskRuntime rows include CE/PM/QA work. Conflicting or unknown
  // explicit roles cannot become Director work through a legacy assignment.
  if (roles.some((role) => role !== 'director')) return false;
  if (getTaskAssignees(task).length) return isDirectorAssignedTask(task);
  return roles.length > 0;
}

export function splitTaskQueues(
  snapshotTasks: PmTask[],
  directorRealtime: DirectorRealtimeFeed,
): LiveTaskQueues {
  const normalizedSnapshotTasks = normalizeTasks(snapshotTasks);
  const normalizedRealtimeTasks = normalizeTasks(directorRealtime.tasks);
  // PM panel is the canonical backlog view and should retain the full snapshot list.
  const pmTasks = normalizedSnapshotTasks;
  const isDirectorRealtimeReady = Boolean(directorRealtime.runId);

  return {
    pmTasks,
    directorTasks: normalizedRealtimeTasks.filter(isDirectorRuntimeTask),
    directorTaskSource: 'realtime',
    isDirectorRealtimeConnected: Boolean(directorRealtime.isConnected),
    isDirectorRealtimeReady,
  };
}

export function useLiveTaskQueues({
  snapshotTasks,
  directorRealtime,
}: UseLiveTaskQueuesOptions): LiveTaskQueues {
  return useMemo(
    () => splitTaskQueues(snapshotTasks, directorRealtime),
    [
      directorRealtime.isConnected,
      directorRealtime.runId,
      directorRealtime.tasks,
      snapshotTasks,
    ],
  );
}
