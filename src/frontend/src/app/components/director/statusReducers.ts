/** Status Reducers Module
 *
 * Contains pure functions for computing task execution status
 * and session state management for the Director Workspace.
 */

export interface ResolveTaskExecutionStatusParams {
  rawStatus: string;
  done: boolean;
  completed: boolean;
  directorRunning: boolean;
  isCurrent: boolean;
}

export type TaskExecutionStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'blocked';

export function resolveTaskExecutionStatus(params: ResolveTaskExecutionStatusParams): TaskExecutionStatus {
  const normalized = String(params.rawStatus || '').trim().toLowerCase();
  const completed = params.done || params.completed || ['completed', 'done', 'success'].includes(normalized);
  if (completed) {
    return 'completed';
  }
  if (['failed', 'error'].includes(normalized)) {
    return 'failed';
  }
  if (['blocked', 'cancelled', 'canceled'].includes(normalized)) {
    return 'blocked';
  }
  if (['running', 'in_progress', 'claimed'].includes(normalized)) {
    return 'running';
  }
  if (params.directorRunning && params.isCurrent) {
    return 'running';
  }
  return 'pending';
}
