/**
 * App 快照工具函数
 *
 * 处理进度快照的解析和判断逻辑
 */

import type { SnapshotPayload } from '@/app/types/appContracts';

const RUN_ID_PREFIX = 'pm-';

/**
 * 从 Run ID 解析迭代次数
 */
export function parseIterationFromRunId(runId: string): number | null {
  const raw = runId.trim().toLowerCase();
  if (!raw.startsWith(RUN_ID_PREFIX)) return null;
  const suffix = raw.slice(RUN_ID_PREFIX.length);
  if (!/^\d+$/.test(suffix)) return null;
  const value = Number(suffix);
  return Number.isFinite(value) ? value : null;
}

/**
 * 从快照中获取迭代值
 */
export function toIterationValue(snapshot: SnapshotPayload | null): number | null {
  if (!snapshot || typeof snapshot !== 'object') return null;

  const runId = typeof snapshot.run_id === 'string' ? snapshot.run_id : '';
  const fromRunId = parseIterationFromRunId(runId);
  if (fromRunId !== null) return fromRunId;

  const pmState = snapshot.pm_state;
  if (!pmState || typeof pmState !== 'object') return null;
  const raw = pmState['pm_iteration'];
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (typeof raw === 'string') {
    const parsed = Number(raw.trim());
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

/**
 * 生成 Run Key
 */
export function toRunKey(snapshot: SnapshotPayload | null): string {
  if (!snapshot || typeof snapshot !== 'object') return '';
  const runId = typeof snapshot.run_id === 'string' ? snapshot.run_id.trim() : '';
  if (runId) return runId;
  const iteration = toIterationValue(snapshot);
  return iteration !== null ? `${RUN_ID_PREFIX}${String(iteration).padStart(5, '0')}` : '';
}

/**
 * 判断是否为完成状态
 */
export function isDoneLikeStatus(value: unknown): boolean {
  const status = String(value || '').trim().toLowerCase();
  if (!status) return false;
  return ['done', 'complete', 'completed', 'success', 'passed', 'pass', 'ok'].some((token) =>
    status.includes(token)
  );
}

/**
 * 判断快照任务是否全部完成
 */
export function areSnapshotTasksDone(snapshot: SnapshotPayload | null): boolean {
  const tasks = Array.isArray(snapshot?.tasks) ? snapshot.tasks : [];
  if (!tasks.length) return false;
  return tasks.every((task) => {
    if (!task || typeof task !== 'object') return false;
    const candidate = task as Record<string, unknown>;
    if (candidate.done === true || candidate.completed === true) return true;
    return isDoneLikeStatus(candidate.status) || isDoneLikeStatus(candidate.state);
  });
}

/**
 * 统计快照任务数量
 */
export function countSnapshotTasks(snapshot: SnapshotPayload | null): number {
  if (!Array.isArray(snapshot?.tasks)) {
    return 0;
  }
  return snapshot.tasks.filter((task) => Boolean(task && typeof task === 'object')).length;
}

/**
 * 获取快照的 Director 状态
 */
export function getSnapshotDirectorStatus(snapshot: SnapshotPayload | null): string {
  const pmState = snapshot?.pm_state;
  if (!pmState || typeof pmState !== 'object') {
    return '';
  }
  return typeof pmState['last_director_status'] === 'string'
    ? pmState['last_director_status'].trim()
    : '';
}

/**
 * 判断是否应保留更丰富的快照
 */
export function shouldKeepRicherSnapshot(
  previous: SnapshotPayload | null,
  incoming: SnapshotPayload | null,
): boolean {
  const previousTaskCount = countSnapshotTasks(previous);
  const incomingTaskCount = countSnapshotTasks(incoming);
  if (previousTaskCount > 0 && incomingTaskCount === 0) {
    return true;
  }

  const previousDirectorStatus = getSnapshotDirectorStatus(previous);
  const incomingDirectorStatus = getSnapshotDirectorStatus(incoming);
  if (previousDirectorStatus && !incomingDirectorStatus && previousTaskCount >= incomingTaskCount) {
    return true;
  }

  return false;
}
