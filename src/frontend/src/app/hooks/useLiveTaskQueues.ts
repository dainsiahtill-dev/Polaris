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
  directorFallbackTasks: PmTask[];
  directorTaskSource: 'realtime' | 'snapshot';
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

function buildDirectorFallbackTasks(snapshotTasks: PmTask[]): PmTask[] {
  const explicitlyAssignedTasks = snapshotTasks.filter((task) => isDirectorAssignedTask(task));
  if (explicitlyAssignedTasks.length > 0) {
    return explicitlyAssignedTasks;
  }
  return snapshotTasks;
}

function readMetadataString(task: PmTask, key: string): string {
  const metadata = task.metadata && typeof task.metadata === 'object'
    ? task.metadata
    : null;
  const value = metadata?.[key];
  return typeof value === 'string' ? value.trim() : '';
}

export function getTaskAssignee(task: PmTask): string {
  const candidates = [
    task.assigned_to,
    task.assignedTo,
    task.assignee,
    readMetadataString(task, 'assigned_to'),
    readMetadataString(task, 'assignedTo'),
    readMetadataString(task, 'assignee'),
  ];

  const match = candidates
    .map((value) => String(value || '').trim())
    .find((value) => value.length > 0);

  return String(match || '').toLowerCase();
}

export function isDirectorAssignedTask(task: PmTask): boolean {
  const assignee = getTaskAssignee(task);
  if (!assignee) {
    return false;
  }
  return assignee === 'director' || assignee.includes('director');
}

export function splitTaskQueues(
  snapshotTasks: PmTask[],
  directorRealtime: DirectorRealtimeFeed,
): LiveTaskQueues {
  const normalizedSnapshotTasks = normalizeTasks(snapshotTasks);
  const normalizedRealtimeTasks = normalizeTasks(directorRealtime.tasks);
  const directorFallbackTasks = buildDirectorFallbackTasks(normalizedSnapshotTasks);
  // PM panel is the canonical backlog view and should retain the full snapshot list.
  const pmTasks = normalizedSnapshotTasks;
  const isDirectorRealtimeReady = Boolean(directorRealtime.runId);
  const shouldUseRealtimeTasks =
    normalizedRealtimeTasks.length > 0 || (!directorFallbackTasks.length && isDirectorRealtimeReady);
  const directorTasks = shouldUseRealtimeTasks
    ? normalizedRealtimeTasks
    : directorFallbackTasks;

  return {
    pmTasks,
    directorTasks,
    directorFallbackTasks,
    directorTaskSource: shouldUseRealtimeTasks ? 'realtime' : 'snapshot',
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
