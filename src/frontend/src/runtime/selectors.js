import { useMemo } from 'react';
import { useRuntime } from '@/app/hooks/useRuntime';
const FULL_CHAIN_RUNTIME_ROLES = ['pm', 'chief_engineer', 'director', 'qa'];
function createIdleRoleView() {
    return {
        state: 'idle',
        task_id: null,
        task_title: null,
        detail: null,
        updated_at: '',
    };
}
function normalizeRoleState(value) {
    const token = String(value || '').trim().toLowerCase();
    if (!token)
        return 'idle';
    if (token === 'analyzing')
        return 'analyzing';
    if (token === 'planning' || token === 'intake' || token === 'docs_check' || token === 'architect')
        return 'planning';
    if (token === 'verification' || token === 'qa_gate')
        return 'verification';
    if (token === 'completed' || token === 'done' || token === 'success' || token === 'handover')
        return 'completed';
    if (token === 'failed' || token === 'error' || token === 'cancelled' || token === 'canceled')
        return 'failed';
    if (token === 'blocked')
        return 'blocked';
    if (token === 'executing' ||
        token === 'implementation' ||
        token === 'running' ||
        token === 'in_progress' ||
        token === 'claimed' ||
        token === 'tool_running' ||
        token === 'llm_calling' ||
        token.startsWith('director_')) {
        return 'executing';
    }
    return 'idle';
}
function normalizePhase(value) {
    const token = String(value || '').trim().toLowerCase();
    if (!token)
        return null;
    if (token === 'pending' ||
        token === 'intake' ||
        token === 'docs_check' ||
        token === 'architect' ||
        token === 'planning' ||
        token === 'implementation' ||
        token === 'verification' ||
        token === 'qa_gate' ||
        token === 'handover' ||
        token === 'completed' ||
        token === 'failed' ||
        token === 'blocked' ||
        token === 'cancelled') {
        return token;
    }
    if (token === 'idle')
        return 'pending';
    if (token === 'executing' || token === 'tool_running' || token === 'llm_calling')
        return 'implementation';
    if (token === 'analyzing')
        return 'planning';
    if (token === 'error' || token === 'canceled')
        return 'failed';
    return null;
}
function normalizeTaskState(value) {
    const token = String(value || '').trim().toLowerCase();
    if (token === 'ready')
        return 'ready';
    if (token === 'claimed')
        return 'claimed';
    if (token === 'in_progress' || token === 'running')
        return 'in_progress';
    if (token === 'completed' || token === 'done' || token === 'success')
        return 'completed';
    if (token === 'failed' || token === 'error')
        return 'failed';
    if (token === 'blocked')
        return 'blocked';
    if (token === 'cancelled' || token === 'canceled')
        return 'cancelled';
    return 'pending';
}
function normalizeWorkerState(value) {
    const token = String(value || '').trim().toLowerCase();
    if (token === 'claimed')
        return 'claimed';
    if (token === 'in_progress' || token === 'busy' || token === 'running')
        return 'in_progress';
    if (token === 'completed' || token === 'success')
        return 'completed';
    if (token === 'failed' || token === 'error')
        return 'failed';
    return 'idle';
}
function mapSeverity(level) {
    if (level === 'error')
        return 'error';
    if (level === 'warning')
        return 'warning';
    return 'info';
}
function mapRoleType(source) {
    const token = source.trim().toLowerCase();
    if (token === 'pm' || token.includes('pm'))
        return 'PM';
    if (token === 'ce'
        || token.includes('chief')
        || token.includes('engineer')
        || token.includes('chief_engineer')
        || token.includes('chiefengineer'))
        return 'ChiefEngineer';
    if (token.includes('director'))
        return 'Director';
    if (token === 'qa' || token.includes('qa'))
        return 'QA';
    return null;
}
export function useCurrentPhase() {
    const runtime = useRuntime({ roles: [...FULL_CHAIN_RUNTIME_ROLES] });
    return normalizePhase(runtime.currentPhase);
}
export function useRoles() {
    const runtime = useRuntime({ roles: [...FULL_CHAIN_RUNTIME_ROLES] });
    const roles = runtime.engineStatus?.roles ?? {};
    return useMemo(() => {
        const now = new Date().toISOString();
        const toRoleView = (keys, fallback) => {
            const payload = keys.map((key) => roles[key]).find((value) => value && typeof value === 'object');
            if (!payload || typeof payload !== 'object') {
                return {
                    ...createIdleRoleView(),
                    state: normalizeRoleState(fallback),
                    updated_at: now,
                };
            }
            const obj = payload;
            return {
                state: normalizeRoleState(obj.status || obj.state || fallback),
                task_id: typeof obj.task_id === 'string' ? obj.task_id : null,
                task_title: typeof obj.task_title === 'string' ? obj.task_title : null,
                detail: typeof obj.detail === 'string' ? obj.detail : null,
                updated_at: typeof obj.updated_at === 'string' ? obj.updated_at : now,
            };
        };
        return {
            PM: toRoleView(['PM', 'pm'], runtime.pmStatus?.running ? 'planning' : 'idle'),
            ChiefEngineer: toRoleView(['ChiefEngineer', 'chief_engineer', 'chiefEngineer', 'chief engineer', 'CE', 'ce'], 'idle'),
            Director: toRoleView(['Director', 'director'], runtime.directorStatus?.running ? 'executing' : 'idle'),
            QA: toRoleView(['QA', 'qa'], 'idle'),
        };
    }, [roles, runtime.pmStatus?.running, runtime.directorStatus?.running]);
}
export function useRoleState(role) {
    const roles = useRoles();
    return roles[role];
}
export function useWorkers() {
    const runtime = useRuntime({ roles: [...FULL_CHAIN_RUNTIME_ROLES] });
    return useMemo(() => {
        const now = new Date().toISOString();
        return runtime.workers.map((worker) => ({
            id: worker.id,
            state: normalizeWorkerState(worker.status),
            task_id: worker.currentTaskId ?? null,
            updated_at: now,
        }));
    }, [runtime.workers]);
}
export function useTasks() {
    const runtime = useRuntime({ roles: [...FULL_CHAIN_RUNTIME_ROLES] });
    return useMemo(() => {
        return runtime.tasks.map((task) => {
            const taskRecord = task;
            const state = normalizeTaskState(task.status || task.state);
            const blockedBy = Array.isArray(task.blocked_by) ? task.blocked_by.map((v) => String(v)) : [];
            const progressRaw = taskRecord.progress;
            const progress = typeof progressRaw === 'number'
                ? progressRaw
                : state === 'completed'
                    ? 100
                    : state === 'in_progress'
                        ? 50
                        : 0;
            return {
                id: task.id,
                title: task.title || task.goal || task.id,
                level: 1,
                parent_id: null,
                state,
                blocked_by: blockedBy,
                progress: Math.max(0, Math.min(100, progress)),
            };
        });
    }, [runtime.tasks]);
}
export function useSummary() {
    const tasks = useTasks();
    return useMemo(() => {
        const total = tasks.length;
        const completed = tasks.filter((task) => task.state === 'completed').length;
        const failed = tasks.filter((task) => task.state === 'failed').length;
        const blocked = tasks.filter((task) => task.state === 'blocked').length;
        return { total, completed, failed, blocked };
    }, [tasks]);
}
export function useRuntimeEvents() {
    const runtime = useRuntime({ roles: [...FULL_CHAIN_RUNTIME_ROLES] });
    const phase = normalizePhase(runtime.currentPhase) ?? 'pending';
    return useMemo(() => {
        return runtime.executionLogs.map((entry, index) => {
            const meta = entry.meta && typeof entry.meta === 'object' ? entry.meta : {};
            const taskId = typeof meta.task_id === 'string' ? meta.task_id : null;
            return {
                type: 'runtime_event_v2',
                schema_version: 2,
                event_id: entry.id,
                seq: index,
                run_id: runtime.runId ?? '',
                ts: entry.timestamp,
                phase,
                role: mapRoleType(entry.source),
                node_level: null,
                state: null,
                task_id: taskId,
                worker_id: null,
                severity: mapSeverity(entry.level),
                message: entry.message,
                detail: entry.details || null,
                metrics: meta,
            };
        });
    }, [runtime.executionLogs, runtime.runId, phase]);
}
export function useRecentEvents(count = 50) {
    const events = useRuntimeEvents();
    return useMemo(() => events.slice(-count), [events, count]);
}
export function useEventsBySeverity(severity) {
    const events = useRuntimeEvents();
    return useMemo(() => events.filter((event) => event.severity === severity), [events, severity]);
}
export function useBlockedTasks() {
    const tasks = useTasks();
    return useMemo(() => tasks.filter((task) => task.state === 'blocked'), [tasks]);
}
export function useRootTasks() {
    const tasks = useTasks();
    return useMemo(() => tasks.filter((task) => task.level === 1), [tasks]);
}
export function useChildTasks(parentId) {
    const tasks = useTasks();
    return useMemo(() => tasks.filter((task) => task.parent_id === parentId), [tasks, parentId]);
}
