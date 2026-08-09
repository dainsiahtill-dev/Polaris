import { useMemo } from 'react';
function normalizeTasks(tasks) {
    if (!Array.isArray(tasks)) {
        return [];
    }
    return tasks
        .filter((task) => Boolean(task && typeof task === 'object'))
        .map((task) => normalizeTask(task));
}
function normalizeTaskStatus(value) {
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
function normalizeTask(task) {
    const normalizedStatus = normalizeTaskStatus(task.status || task.state);
    const done = Boolean(task.done || task.completed || normalizedStatus === 'completed');
    return {
        ...task,
        status: normalizedStatus,
        state: normalizedStatus,
        done,
        completed: done,
    };
}
function readMetadataString(task, key) {
    const metadata = task.metadata && typeof task.metadata === 'object'
        ? task.metadata
        : null;
    const value = metadata?.[key];
    return typeof value === 'string' ? value.trim() : '';
}
export function getTaskAssignee(task) {
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
export function isDirectorAssignedTask(task) {
    const assignee = getTaskAssignee(task);
    if (!assignee) {
        return false;
    }
    return assignee === 'director' || assignee.includes('director');
}
export function splitTaskQueues(snapshotTasks, directorRealtime) {
    const normalizedSnapshotTasks = normalizeTasks(snapshotTasks);
    const normalizedRealtimeTasks = normalizeTasks(directorRealtime.tasks);
    // PM panel is the canonical backlog view and should retain the full snapshot list.
    const pmTasks = normalizedSnapshotTasks;
    const isDirectorRealtimeReady = Boolean(directorRealtime.runId);
    return {
        pmTasks,
        directorTasks: normalizedRealtimeTasks,
        directorTaskSource: 'realtime',
        isDirectorRealtimeConnected: Boolean(directorRealtime.isConnected),
        isDirectorRealtimeReady,
    };
}
export function useLiveTaskQueues({ snapshotTasks, directorRealtime, }) {
    return useMemo(() => splitTaskQueues(snapshotTasks, directorRealtime), [
        directorRealtime.isConnected,
        directorRealtime.runId,
        directorRealtime.tasks,
        snapshotTasks,
    ]);
}
