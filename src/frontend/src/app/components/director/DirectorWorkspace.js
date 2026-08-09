import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/** DirectorWorkspace - Director 执行工作区
 *
 * 角色特性：
 * - 任务执行与代码实现
 * - 调试与问题解决
 * - 测试用例执行
 * - 执行状态汇报
 * - 阻塞问题上报
 */
import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { openPath } from '@/api';
import { Hammer, Code2, Play, Bug, Terminal, CheckCircle2, MessageSquare, Settings, ChevronLeft, FileCode, ListTodo, Activity, Loader2, AlertTriangle, Zap, Pause, RotateCcw, RefreshCw, FilePlus, FileEdit, FileX, Clock, Coins, BarChart3, Layers, ChevronDown, ChevronRight, Hash, Brain, Wrench, Database, Trash2, SlidersHorizontal, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { AIDialoguePanel } from '@/app/components/ai-dialogue';
import { RealTimeFileDiff } from './RealTimeFileDiff';
import { compareFileEditEventsForCodePanel, hasRenderablePatch, selectDefaultCodePanelEvent, } from './directorCodeEvents';
import { resolveDirectorOpenTarget } from './directorFileActions';
import { TaskTraceTimeline } from '../common/TaskTraceTimeline';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import { RoleRunEvidenceStrip } from '@/app/components/common/RoleRunEvidenceStrip';
import { DirectorTaskPanel as DirectorTaskPanelView, } from './DirectorTaskPanel';
import { DirectorWorkbenchPanel } from './DirectorWorkbenchPanel';
import { DirectorStrategyPanel } from './DirectorStrategyPanel';
import { cancelDirectorRun, cancelDirectorTask, createDirectorTask, getDirectorCapabilities, getDirectorDiagnostics, getDirectorRun, getDirectorTask, getDirectorWorker, clearRoleKernelCache, getDirectorTaskKernelLLMEvents, getRoleKernelCacheStats, getRoleKernelLLMEvents, getRoleKernelTokenBudgetStats, listDirectorTaskSnapshotRows, listDirectorWorkers, runDirector, } from '@/services';
import { TaskStatus } from '@/types/task';
function evidenceEndpoint(endpoint, workspace) {
    const value = String(workspace || '').trim();
    if (!value)
        return endpoint;
    const separator = endpoint.includes('?') ? '&' : '?';
    return `${endpoint}${separator}workspace=${encodeURIComponent(value)}`;
}
function EvidenceEndpointBadge({ endpoint, testId, }) {
    return (_jsx("span", { className: "shrink-0 rounded border border-white/10 bg-slate-950/70 px-1.5 py-0.5 text-[9px] font-medium text-slate-500", title: endpoint, "data-endpoint": endpoint, "data-testid": testId, children: "API" }));
}
const DIRECTOR_RUNTIME_PUSH_ENDPOINT = '/v2/ws/runtime';
const DIRECTOR_COMMAND_ACCEPTED_MESSAGE = '命令已提交，等待 runtime.v2 推送确认。';
const DIRECTOR_TERMINAL_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled', 'canceled', 'blocked', 'timeout']);
const isDirectorRunTerminal = (status) => {
    const token = String(status || '').trim().toLowerCase();
    return DIRECTOR_TERMINAL_RUN_STATUSES.has(token);
};
export function resolveTaskExecutionStatus(params) {
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
function readTaskMetadata(task) {
    return task.metadata && typeof task.metadata === 'object'
        ? task.metadata
        : {};
}
function readTaskString(task, keys) {
    for (const key of keys) {
        const directValue = task[key];
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
function readStringList(value) {
    if (!Array.isArray(value)) {
        return [];
    }
    return value
        .map((item) => {
        if (typeof item === 'string') {
            return item.trim();
        }
        if (item && typeof item === 'object') {
            const record = item;
            return String(record.description || record.title || record.name || record.path || record.id || '').trim();
        }
        return String(item || '').trim();
    })
        .filter((item) => item.length > 0);
}
function readTaskStringList(task, keys) {
    const metadata = readTaskMetadata(task);
    for (const key of keys) {
        const directList = readStringList(task[key]);
        if (directList.length > 0) {
            return directList;
        }
        const metadataList = readStringList(metadata[key]);
        if (metadataList.length > 0) {
            return metadataList;
        }
    }
    return [];
}
function hasUsableTaskValue(value) {
    if (Array.isArray(value)) {
        return value.length > 0;
    }
    if (value && typeof value === 'object') {
        return Object.keys(value).length > 0;
    }
    if (typeof value === 'string') {
        return value.trim().length > 0;
    }
    return value !== undefined && value !== null;
}
function mergeTaskRows(detailRow, liveRow) {
    const detailRecord = detailRow;
    const liveRecord = liveRow;
    const merged = { ...detailRecord, ...liveRecord };
    const detailMetadata = readTaskMetadata(detailRow);
    const liveMetadata = readTaskMetadata(liveRow);
    const mergedMetadata = { ...detailMetadata, ...liveMetadata };
    if (hasUsableTaskValue(mergedMetadata)) {
        merged.metadata = mergedMetadata;
    }
    for (const key of [
        'goal',
        'description',
        'acceptance',
        'acceptance_criteria',
        'execution_steps',
        'target_files',
        'current_file',
        'current_file_path',
        'dependencies',
        'pm_task_id',
    ]) {
        if (!hasUsableTaskValue(liveRecord[key]) && hasUsableTaskValue(detailRecord[key])) {
            merged[key] = detailRecord[key];
        }
    }
    if (typeof detailRecord.description === 'string'
        && detailRecord.description.trim()
        && typeof liveRecord.description === 'string'
        && [liveRecord.subject, liveRecord.title, liveRecord.id].some((value) => liveRecord.description === value)) {
        merged.description = detailRecord.description;
    }
    return merged;
}
function normalizeDirectorCreatedTaskRow(value, payload, fallbackTaskId) {
    const taskId = String(fallbackTaskId || '').trim();
    if (!taskId) {
        return null;
    }
    const record = value && typeof value === 'object' ? value : {};
    const metadata = record.metadata && typeof record.metadata === 'object'
        ? record.metadata
        : {};
    const subject = String(record.subject || record.title || payload.subject || taskId).trim();
    const description = String(record.description || payload.description || subject).trim();
    const status = String(record.status || 'PENDING').trim().toLowerCase();
    const priority = String(record.priority || payload.priority || 'MEDIUM').trim().toUpperCase();
    const acceptance = readStringList(record.acceptance).length > 0
        ? readStringList(record.acceptance)
        : payload.metadata.acceptance;
    return {
        id: taskId,
        title: subject,
        subject,
        goal: String(record.goal || payload.metadata.pm_task_title || subject).trim(),
        description,
        status: status === 'completed'
            ? TaskStatus.COMPLETED
            : status === 'failed'
                ? TaskStatus.FAILED
                : status === 'blocked'
                    ? TaskStatus.BLOCKED
                    : status === 'running' || status === 'claimed' || status === 'in_progress'
                        ? TaskStatus.IN_PROGRESS
                        : TaskStatus.PENDING,
        state: String(record.state || record.status || 'PENDING'),
        done: false,
        completed: false,
        priority: priority === 'CRITICAL' ? 4 : priority === 'HIGH' ? 3 : priority === 'LOW' ? 1 : 2,
        acceptance: acceptance.map((descriptionText) => ({ description: descriptionText })),
        acceptance_criteria: acceptance,
        command: typeof record.command === 'string' ? record.command : undefined,
        execution_checklist: readStringList(record.execution_steps || record.execution_checklist || record.steps),
        target_files: readStringList(record.target_files || record.files),
        dependencies: readStringList(record.dependencies),
        blueprint_id: String(record.blueprint_id || payload.metadata.blueprint_id || '').trim() || null,
        blueprint_path: String(record.blueprint_path || payload.metadata.blueprint_path || '').trim() || null,
        runtime_blueprint_path: String(record.runtime_blueprint_path || payload.metadata.runtime_blueprint_path || '').trim() || null,
        pm_task_id: String(record.pm_task_id || metadata.pm_task_id || payload.metadata.pm_task_id || '').trim(),
        metadata: {
            ...payload.metadata,
            ...metadata,
            director_task_source: metadata.director_task_source || 'local',
            priority,
            subject,
        },
        created_at: typeof record.created_at === 'string' ? record.created_at : new Date().toISOString(),
    };
}
function upsertDirectorCommandSnapshotTaskRow(current, task) {
    const taskId = String(task.id || '').trim();
    if (!taskId) {
        return current;
    }
    const existing = current.find((item) => String(item.id || '').trim() === taskId);
    const nextTask = existing ? mergeTaskRows(existing, task) : task;
    return [
        nextTask,
        ...current.filter((item) => String(item.id || '').trim() !== taskId),
    ];
}
function toTaskToken(value) {
    return String(value || '').trim().toLowerCase();
}
function toNonNegativeInt(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? Math.max(0, Math.round(numeric)) : 0;
}
function resolveTaskIdentityCandidates(task) {
    const metadata = readTaskMetadata(task);
    const rawTask = task;
    const candidates = [
        task.id,
        task.title,
        rawTask.subject,
        rawTask.pm_task_id,
        task.goal,
        metadata.pm_task_id,
        metadata.task_id,
        metadata.subject,
        metadata.id,
    ];
    const normalized = [];
    const seen = new Set();
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
export function computePatchLineStats(patch, operation) {
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
        if (!line)
            continue;
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
function resolveEventLineStats(event) {
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
export function buildTaskRealtimeTelemetry(tasks, fileEditEvents, taskProgressMap) {
    const tokenToTaskId = new Map();
    const taskIdSet = new Set();
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
        const rawTask = task;
        for (const aliasKey of ['subject', 'pm_task_id', 'task_id', 'backlog_ref']) {
            const aliasToken = toTaskToken(rawTask[aliasKey] ?? readTaskMetadata(task)[aliasKey]);
            if (aliasToken) {
                tokenToTaskId.set(aliasToken, taskId);
            }
        }
    }
    const accumulators = new Map();
    // Process file edit events
    for (const event of fileEditEvents) {
        const rawTaskId = String(event.taskId || '').trim();
        if (!rawTaskId) {
            continue;
        }
        const rawTaskToken = toTaskToken(rawTaskId);
        let mappedTaskId = tokenToTaskId.get(rawTaskToken) || "";
        if (!mappedTaskId) {
            for (const task of tasks) {
                const taskId = String(task.id || '').trim();
                if (!taskId) {
                    continue;
                }
                const aliases = [
                    ...resolveTaskIdentityCandidates(task),
                    ...readTaskStringList(task, ['target_task_ids', 'related_task_ids']),
                ];
                if (aliases.some((alias) => toTaskToken(alias) === rawTaskToken)) {
                    mappedTaskId = taskId;
                    break;
                }
            }
        }
        mappedTaskId = mappedTaskId || rawTaskId;
        if (!taskIdSet.has(mappedTaskId)) {
            continue;
        }
        const accumulator = accumulators.get(mappedTaskId) || {
            filesTouched: new Set(),
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
    // Merge in task progress data (retry count, phase info, current file from backend)
    if (taskProgressMap) {
        for (const [taskId, progress] of taskProgressMap.entries()) {
            if (!taskIdSet.has(taskId)) {
                continue;
            }
            const accumulator = accumulators.get(taskId) || {
                filesTouched: new Set(),
                lineStats: { added: 0, deleted: 0, modified: 0 },
                operationStats: { create: 0, modify: 0, delete: 0 },
            };
            // Update retry count from progress
            if (progress.retryCount !== undefined) {
                accumulator.retryCount = progress.retryCount;
            }
            if (progress.maxRetries !== undefined) {
                accumulator.maxRetries = progress.maxRetries;
            }
            // Update phase info
            if (progress.phase) {
                accumulator.currentPhase = progress.phase;
            }
            if (progress.phaseIndex !== undefined) {
                accumulator.phaseIndex = progress.phaseIndex;
            }
            if (progress.phaseTotal !== undefined) {
                accumulator.phaseTotal = progress.phaseTotal;
            }
            // Update current file from progress (takes precedence over file edit events)
            if (progress.currentFile) {
                accumulator.currentFilePath = progress.currentFile;
            }
            accumulators.set(taskId, accumulator);
        }
    }
    const telemetry = new Map();
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
function formatTelemetryTime(value) {
    if (!value) {
        return '';
    }
    const epoch = Date.parse(value);
    if (!Number.isFinite(epoch)) {
        return '';
    }
    return new Date(epoch).toLocaleTimeString();
}
function resolveSessionStatus(directorRunning, isStarting, tasks) {
    if (directorRunning || isStarting) {
        return 'running';
    }
    if (tasks.length > 0 && tasks.every((task) => task.status === 'completed')) {
        return 'completed';
    }
    if (tasks.some((task) => task.status === 'blocked')) {
        return 'paused';
    }
    return 'idle';
}
export function normalizeDirectorCapabilityHosts(payload) {
    const capabilities = payload?.capabilities;
    if (Array.isArray(capabilities)) {
        return [{ hostKind: payload?.role || 'default', capabilities: capabilities.filter(Boolean).map(String).sort() }];
    }
    if (!capabilities || typeof capabilities !== 'object') {
        return [];
    }
    return Object.entries(capabilities)
        .map(([hostKind, values]) => ({
        hostKind,
        capabilities: Array.isArray(values) ? values.filter(Boolean).map(String).sort() : [],
    }))
        .filter((entry) => entry.capabilities.length > 0)
        .sort((left, right) => left.hostKind.localeCompare(right.hostKind));
}
function formatCapabilityLabel(value) {
    return value.replace(/_/g, ' ');
}
function formatKernelNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '-';
}
function formatKernelPercent(value) {
    return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}%` : '-';
}
function logTimestampMs(entry) {
    const timestamp = String(entry.timestamp || '').trim();
    if (!timestamp) {
        return null;
    }
    const parsed = Date.parse(timestamp);
    return Number.isFinite(parsed) ? parsed : null;
}
function formatTerminalTimestamp(entry) {
    const parsed = logTimestampMs(entry);
    if (parsed !== null) {
        return new Date(parsed).toLocaleTimeString();
    }
    return String(entry.timestamp || '').trim();
}
function formatTerminalLogEntry(entry, fallbackSource) {
    const timestamp = formatTerminalTimestamp(entry);
    const source = String(entry.source || fallbackSource).trim();
    const level = String(entry.level || 'info').trim();
    const body = [
        String(entry.title || '').trim(),
        String(entry.message || '').trim(),
        String(entry.details || '').trim(),
    ].filter(Boolean).join(' | ');
    const prefix = [
        timestamp ? `[${timestamp}]` : '',
        source ? source : fallbackSource,
        level ? `<${level}>` : '',
    ].filter(Boolean).join(' ');
    return `${prefix}${body ? ` ${body}` : ''}`.trim();
}
function formatDirectorTerminalStreamOutput(executionLogs, processStreamEvents, hiddenBeforeMs) {
    const keyed = new Map();
    for (const [source, rows] of [['process', processStreamEvents], ['execution', executionLogs]]) {
        rows.forEach((entry, index) => {
            const timestampMs = logTimestampMs(entry);
            if (hiddenBeforeMs > 0 && (timestampMs === null || timestampMs <= hiddenBeforeMs)) {
                return;
            }
            const key = entry.id || `${source}:${entry.timestamp}:${entry.message}:${index}`;
            keyed.set(key, { entry, source, timestampMs: timestampMs ?? Number.MAX_SAFE_INTEGER });
        });
    }
    return Array.from(keyed.values())
        .sort((left, right) => left.timestampMs - right.timestampMs)
        .slice(-200)
        .map(({ entry, source }) => formatTerminalLogEntry(entry, source))
        .filter(Boolean)
        .join('\n');
}
function readKernelEventText(event, keys) {
    if (!event) {
        return '';
    }
    for (const key of keys) {
        const value = event[key];
        if (typeof value === 'string' && value.trim()) {
            return value.trim();
        }
        if (typeof value === 'number' && Number.isFinite(value)) {
            return String(value);
        }
    }
    return '';
}
function readKernelStatNumber(stats, keys) {
    if (!stats) {
        return undefined;
    }
    for (const key of keys) {
        const value = stats[key];
        if (typeof value === 'number' && Number.isFinite(value)) {
            return value;
        }
    }
    return undefined;
}
function formatKernelEventType(event) {
    return readKernelEventText(event, ['event_type', 'type', 'status']).replace(/_/g, ' ') || '-';
}
function formatKernelEventModel(event) {
    return readKernelEventText(event, ['model', 'model_name', 'provider']) || '-';
}
function readWorkerText(row, keys) {
    for (const key of keys) {
        const value = row[key];
        if (typeof value === 'string' && value.trim()) {
            return value.trim();
        }
        if (typeof value === 'number' && Number.isFinite(value)) {
            return String(value);
        }
    }
    return '';
}
function readWorkerNumber(row, keys) {
    for (const key of keys) {
        const value = row[key];
        if (typeof value === 'number' && Number.isFinite(value)) {
            return value;
        }
        if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
            return Number(value);
        }
    }
    return undefined;
}
function readWorkerBoolean(row, keys) {
    for (const key of keys) {
        const value = row[key];
        if (typeof value === 'boolean') {
            return value;
        }
        if (typeof value === 'string' && value.trim()) {
            const normalized = value.trim().toLowerCase();
            if (['true', 'healthy', 'ok', 'ready'].includes(normalized)) {
                return true;
            }
            if (['false', 'unhealthy', 'failed', 'error'].includes(normalized)) {
                return false;
            }
        }
    }
    return undefined;
}
export function normalizeDirectorWorkerRows(rows) {
    if (!Array.isArray(rows)) {
        return [];
    }
    return rows
        .map((row) => {
        if (!row || typeof row !== 'object') {
            return null;
        }
        const record = row;
        const id = readWorkerText(record, ['id', 'worker_id', 'name']);
        if (!id) {
            return null;
        }
        const worker = {
            id,
            name: readWorkerText(record, ['name', 'display_name', 'worker_name']) || id,
            status: readWorkerText(record, ['status', 'state']) || 'idle',
            currentTaskId: readWorkerText(record, ['currentTaskId', 'current_task_id', 'task_id', 'current_task']) || undefined,
            healthy: readWorkerBoolean(record, ['healthy', 'is_healthy']),
            tasksCompleted: readWorkerNumber(record, ['tasksCompleted', 'tasks_completed', 'completed_tasks']),
            tasksFailed: readWorkerNumber(record, ['tasksFailed', 'tasks_failed', 'failed_tasks']),
        };
        return worker;
    })
        .filter((row) => Boolean(row));
}
export function mergeDirectorWorkers(realtimeWorkers, backendWorkers) {
    const merged = new Map();
    for (const worker of backendWorkers) {
        if (worker?.id) {
            merged.set(worker.id, worker);
        }
    }
    for (const worker of realtimeWorkers) {
        if (worker?.id) {
            merged.set(worker.id, {
                ...merged.get(worker.id),
                ...worker,
            });
        }
    }
    return Array.from(merged.values()).sort((left, right) => left.id.localeCompare(right.id));
}
function DirectorCapabilityStrip({ hosts, isLoading, error, compact = false, }) {
    const allCapabilities = new Set(hosts.flatMap((host) => host.capabilities));
    const deleteAllowed = allCapabilities.has('delete_files');
    const capabilityCount = allCapabilities.size;
    return (_jsx("section", { className: cn(compact ? 'min-w-0' : 'border-b border-white/10 bg-slate-950/55 px-4 py-2'), "data-testid": "director-capability-strip", "aria-label": "Director capability matrix", children: _jsxs("details", { className: "group h-full rounded-lg border border-[var(--soft-border)] bg-[var(--soft-surface)] px-3 py-2", children: [_jsxs("summary", { className: "flex cursor-pointer items-center gap-3 text-[11px] select-none", children: [_jsxs("div", { className: "flex shrink-0 items-center gap-2 text-xs font-medium text-slate-200", children: [_jsx(Wrench, { className: "h-3.5 w-3.5 text-slate-400" }), "\u80FD\u529B"] }), _jsxs("div", { className: "flex min-w-0 flex-1 flex-wrap items-center gap-2", children: [_jsx("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: isLoading ? '读取中' : error ? '能力异常' : `${hosts.length} host` }), !isLoading && !error ? (_jsxs("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: [capabilityCount, " capabilities"] })) : null, !isLoading && !error ? (_jsxs("div", { className: cn('flex shrink-0 items-center gap-1.5 rounded border px-2 py-0.5 text-[10px]', deleteAllowed
                                        ? 'border-red-500/25 bg-red-500/10 text-red-200'
                                        : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'), "data-testid": "director-delete-capability", children: [deleteAllowed ? _jsx(AlertTriangle, { className: "h-3 w-3" }) : _jsx(CheckCircle2, { className: "h-3 w-3" }), "delete_files ", deleteAllowed ? 'allowed' : 'blocked'] })) : null] }), _jsx("span", { className: "ml-auto shrink-0 text-[10px] text-slate-500 group-open:hidden", children: "\u8BE6\u60C5" }), _jsx("span", { className: "ml-auto hidden shrink-0 text-[10px] text-slate-400 group-open:inline", children: "\u6536\u8D77" })] }), _jsxs("div", { className: "mt-2 flex min-w-0 items-center gap-3 border-t border-[var(--soft-border)] pt-2", children: [_jsx(EvidenceEndpointBadge, { endpoint: "/v2/director/capabilities", testId: "director-capability-endpoint" }), isLoading ? (_jsxs("div", { className: "flex items-center gap-2 text-[11px] text-slate-400", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin text-slate-300" }), "\u6B63\u5728\u8BFB\u53D6 Director \u80FD\u529B"] })) : error ? (_jsxs("div", { className: "flex items-center gap-2 rounded border border-red-500/25 bg-red-500/10 px-2 py-1 text-[11px] text-red-200", "data-testid": "director-capability-error", children: [_jsx(AlertTriangle, { className: "h-3.5 w-3.5" }), error] })) : hosts.length > 0 ? (_jsx("div", { className: "flex min-w-0 flex-1 items-center gap-2 overflow-x-auto", "data-testid": "director-capability-hosts", children: hosts.map((host) => (_jsxs("div", { className: "flex shrink-0 items-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-2 py-1", "data-testid": "director-capability-host", children: [_jsx(Brain, { className: "h-3.5 w-3.5 text-cyan-300" }), _jsx("span", { className: "text-[10px] font-medium text-slate-200", children: host.hostKind }), _jsx("span", { className: "soft-chip rounded px-1.5 py-0.5 text-[9px] text-slate-300", children: host.capabilities.length }), _jsx("div", { className: "flex items-center gap-1", children: host.capabilities.slice(0, 4).map((capability) => (_jsx("span", { className: "rounded border border-white/10 bg-slate-950/70 px-1.5 py-0.5 text-[9px] text-slate-300", title: capability, children: formatCapabilityLabel(capability) }, `${host.hostKind}-${capability}`))) })] }, host.hostKind))) })) : (_jsx("div", { className: "text-[11px] text-slate-500", "data-testid": "director-capability-empty", children: "\u540E\u7AEF\u672A\u8FD4\u56DE\u80FD\u529B\u77E9\u9635" }))] })] }) }));
}
function DirectorKernelDiagnosticsStrip({ cacheStats, llmEvents, tokenBudgetStats, isLoading, isClearing, error, onRefresh, onClearCache, workspace, compact = false, }) {
    const eventCount = llmEvents?.count ?? llmEvents?.events?.length;
    return (_jsx("section", { className: cn(compact ? 'min-w-0' : 'border-b border-white/10 bg-slate-950/45 px-4 py-2'), "data-testid": "director-kernel-diagnostics-strip", "aria-label": "Director Kernel diagnostics", children: _jsxs("details", { className: "group h-full rounded-lg border border-[var(--soft-border)] bg-[var(--soft-surface)] px-3 py-2", children: [_jsxs("summary", { className: "flex cursor-pointer items-center gap-3 text-[11px] select-none", children: [_jsxs("div", { className: "flex shrink-0 items-center gap-2 text-xs font-medium text-slate-200", children: [_jsx(BarChart3, { className: "h-3.5 w-3.5 text-slate-400" }), "Kernel"] }), _jsx("div", { className: "flex min-w-0 flex-1 flex-wrap items-center gap-2", children: isLoading ? (_jsxs("span", { className: "flex items-center gap-1 rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: [_jsx(Loader2, { className: "h-3 w-3 animate-spin text-slate-300" }), "\u8BFB\u53D6\u4E2D"] })) : error ? (_jsx("span", { className: "rounded border border-red-500/25 bg-red-500/10 px-2 py-0.5 text-[10px] text-red-200", children: "\u7EDF\u8BA1\u5F02\u5E38" })) : (_jsxs(_Fragment, { children: [_jsxs("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: ["cache hit ", formatKernelPercent(cacheStats?.hit_rate)] }), _jsxs("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: ["tokens ", formatKernelNumber(tokenBudgetStats?.total)] }), _jsxs("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: ["LLM events ", formatKernelNumber(eventCount)] })] })) }), _jsx("span", { className: "ml-auto shrink-0 text-[10px] text-slate-500 group-open:hidden", children: "\u8BE6\u60C5" }), _jsx("span", { className: "ml-auto hidden shrink-0 text-[10px] text-slate-400 group-open:inline", children: "\u6536\u8D77" })] }), _jsxs("div", { className: "mt-2 flex min-w-0 items-center gap-3 border-t border-[var(--soft-border)] pt-2", children: [isLoading ? (_jsxs("div", { className: "flex items-center gap-2 text-[11px] text-slate-400", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin text-slate-300" }), "\u6B63\u5728\u8BFB\u53D6\u7F13\u5B58\u3001\u9884\u7B97\u4E0E LLM \u4E8B\u4EF6"] })) : error ? (_jsxs("div", { className: "flex min-w-0 items-center gap-2 rounded border border-red-500/25 bg-red-500/10 px-2 py-1 text-[11px] text-red-200", "data-testid": "director-kernel-diagnostics-error", children: [_jsx(AlertTriangle, { className: "h-3.5 w-3.5 shrink-0" }), _jsx("span", { className: "truncate", children: error })] })) : (_jsxs("div", { className: "flex min-w-0 flex-1 items-center gap-2 overflow-x-auto", children: [_jsx(KernelStripMetric, { icon: _jsx(Database, { className: "h-3.5 w-3.5 text-cyan-300" }), label: "\u7F13\u5B58", endpoint: "/v2/director/cache-stats", values: [
                                        `hit ${formatKernelPercent(cacheStats?.hit_rate)}`,
                                        `${formatKernelNumber(cacheStats?.size)} / ${formatKernelNumber(cacheStats?.max_size)}`,
                                        cacheStats?.enabled === false ? 'disabled' : 'enabled',
                                    ] }), _jsx(KernelStripMetric, { icon: _jsx(Coins, { className: "h-3.5 w-3.5 text-emerald-300" }), label: "\u9884\u7B97", endpoint: "/v2/director/token-budget-stats", values: [
                                        `total ${formatKernelNumber(tokenBudgetStats?.total)}`,
                                        `dialogue ${formatKernelNumber(tokenBudgetStats?.available_conversation)}`,
                                        `margin ${formatKernelNumber(tokenBudgetStats?.safety_margin)}`,
                                    ] }), _jsx(KernelStripMetric, { icon: _jsx(Brain, { className: "h-3.5 w-3.5 text-slate-400" }), label: "LLM", endpoint: evidenceEndpoint('/v2/director/llm-events?role=director&limit=5', workspace), values: [
                                        `events ${formatKernelNumber(eventCount)}`,
                                        `last ${formatKernelEventType(llmEvents?.events?.[0])}`,
                                        `model ${formatKernelEventModel(llmEvents?.events?.[0])}`,
                                        `err/retry ${formatKernelNumber(readKernelStatNumber(llmEvents?.stats, ['call_error', 'llm_error', 'errors']))}/${formatKernelNumber(readKernelStatNumber(llmEvents?.stats, ['call_retry', 'llm_retry', 'retries']))}`,
                                    ] })] })), _jsxs("div", { className: "ml-auto flex shrink-0 items-center gap-1", children: [_jsx(Button, { variant: "ghost", size: "icon", onClick: onRefresh, disabled: isLoading || isClearing, title: "\u5237\u65B0 Kernel \u7EDF\u8BA1", className: "h-7 w-7 text-slate-400 hover:bg-white/5 hover:text-slate-200", children: _jsx(RefreshCw, { className: cn('h-3.5 w-3.5', isLoading && 'animate-spin') }) }), _jsx(Button, { variant: "ghost", size: "icon", onClick: onClearCache, disabled: isLoading || isClearing, title: "\u6E05\u7A7A Director LLM \u7F13\u5B58", "data-testid": "director-kernel-cache-clear", className: "h-7 w-7 text-slate-400 hover:bg-red-500/10 hover:text-red-300", children: isClearing ? _jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }) : _jsx(Trash2, { className: "h-3.5 w-3.5" }) })] })] })] }) }));
}
function formatDirectorDiagnosticIssue(issue) {
    return String(issue || '')
        .replace(/^director_/, '')
        .replace(/_/g, ' ')
        .trim() || 'unknown';
}
const DIRECTOR_EXECUTION_BLOCKER_LABELS = {
    director_llm_not_ready: 'Director LLM 角色未通过运行前测试',
    director_status_unavailable: 'Director 状态投影不可用',
    director_tasks_unavailable: 'Director 任务队列不可用',
    director_no_tasks: '没有可执行的 Director 任务',
    director_no_ready_tasks: '没有 ready 任务，需先完成 PM/Chief Engineer 交接',
    director_ready_tasks_missing_blueprints: 'workflow 任务缺少 Chief Engineer 蓝图证据',
    director_ready_tasks_invalid_blueprints: 'workflow 任务引用的 Chief Engineer 蓝图不可审计',
    director_workers_unavailable: 'Director worker 池不可用',
    director_no_workers: '没有可用 worker',
    director_no_idle_workers: '有 ready 任务但没有空闲 worker',
};
const DIRECTOR_HARD_BLOCKER_ISSUES = new Set(Object.keys(DIRECTOR_EXECUTION_BLOCKER_LABELS));
const DIRECTOR_STALE_QUEUE_BLOCKER_ISSUES = new Set(['director_no_tasks', 'director_no_ready_tasks']);
const DIRECTOR_BLUEPRINT_BLOCKER_ISSUES = new Set([
    'director_ready_tasks_missing_blueprints',
    'director_ready_tasks_invalid_blueprints',
]);
function isDirectorDiagnosticCompleted(diagnostics) {
    if (!diagnostics) {
        return false;
    }
    const total = Number(diagnostics.tasks?.total ?? 0);
    const completed = Number(diagnostics.tasks?.completed ?? 0);
    return total > 0 && completed >= total && diagnostics.status?.running !== true;
}
function effectiveDirectorMissingBlueprintTaskIds(diagnostics, snapshotBlueprintTaskIds) {
    return (diagnostics?.tasks?.missing_blueprint_task_ids || [])
        .map((taskId) => String(taskId || '').trim())
        .filter((taskId) => taskId.length > 0)
        .filter((taskId) => !snapshotBlueprintTaskIds?.has(toTaskToken(taskId)));
}
function hasEffectiveDirectorInvalidBlueprints(diagnostics) {
    if (!diagnostics) {
        return false;
    }
    if ((diagnostics.tasks?.invalid_blueprint_task_ids || []).length > 0) {
        return true;
    }
    const diagnosticIssues = [
        ...(diagnostics.execution_blockers || []),
        ...(diagnostics.issues || []),
    ];
    return diagnosticIssues.some((issue) => String(issue || '').trim() === 'director_ready_tasks_invalid_blueprints');
}
function canUseSnapshotReadyTasks(diagnostics, snapshotReadyTaskCount = 0, snapshotBlueprintTaskIds) {
    if (!diagnostics || snapshotReadyTaskCount <= 0) {
        return false;
    }
    if (hasEffectiveDirectorInvalidBlueprints(diagnostics)
        || effectiveDirectorMissingBlueprintTaskIds(diagnostics, snapshotBlueprintTaskIds).length > 0) {
        return false;
    }
    return true;
}
function filterDirectorQueueBlockers(blockers, diagnostics, snapshotReadyTaskCount = 0, snapshotBlueprintTaskIds) {
    const suppressStaleQueueBlockers = canUseSnapshotReadyTasks(diagnostics, snapshotReadyTaskCount, snapshotBlueprintTaskIds);
    const suppressMissingBlueprintBlocker = Boolean(diagnostics
        && !hasEffectiveDirectorInvalidBlueprints(diagnostics)
        && effectiveDirectorMissingBlueprintTaskIds(diagnostics, snapshotBlueprintTaskIds).length === 0);
    return blockers.filter((issue) => {
        if (suppressStaleQueueBlockers && DIRECTOR_STALE_QUEUE_BLOCKER_ISSUES.has(issue)) {
            return false;
        }
        if (suppressMissingBlueprintBlocker && issue === 'director_ready_tasks_missing_blueprints') {
            return false;
        }
        return true;
    });
}
function directorExecutionBlockers(diagnostics, snapshotReadyTaskCount = 0, snapshotBlueprintTaskIds) {
    if (!diagnostics) {
        return [];
    }
    if (isDirectorDiagnosticCompleted(diagnostics)) {
        return [];
    }
    if (Array.isArray(diagnostics.execution_blockers) && diagnostics.execution_blockers.length > 0) {
        const blockers = diagnostics.execution_blockers
            .map((issue) => String(issue || '').trim())
            .filter((issue) => issue.length > 0);
        return filterDirectorQueueBlockers(blockers, diagnostics, snapshotReadyTaskCount, snapshotBlueprintTaskIds);
    }
    if (diagnostics.status?.running) {
        return [];
    }
    const hasExplicitExecutionSignal = typeof diagnostics.can_execute === 'boolean' || Array.isArray(diagnostics.execution_blockers);
    if (hasExplicitExecutionSignal && diagnostics.can_execute !== false) {
        return [];
    }
    return filterDirectorQueueBlockers((diagnostics.issues || []).filter((issue) => DIRECTOR_HARD_BLOCKER_ISSUES.has(issue)), diagnostics, snapshotReadyTaskCount, snapshotBlueprintTaskIds);
}
function formatDirectorExecutionBlockReason(diagnostics, snapshotReadyTaskCount = 0, snapshotBlueprintTaskIds) {
    const blockers = directorExecutionBlockers(diagnostics, snapshotReadyTaskCount, snapshotBlueprintTaskIds);
    if (blockers.length === 0) {
        return '';
    }
    const primary = DIRECTOR_EXECUTION_BLOCKER_LABELS[blockers[0]] || formatDirectorDiagnosticIssue(blockers[0]);
    const extraCount = blockers.length - 1;
    return `Director 交接诊断未通过：${primary}${extraCount > 0 ? `，另有 ${extraCount} 项阻断` : ''}`;
}
function DirectorReadinessDiagnosticsStrip({ diagnostics, isLoading, error, onRefresh, compact = false, workspace, snapshotReadyTaskCount = 0, snapshotTaskTotal = 0, snapshotBlueprintTaskIds, }) {
    const completed = isDirectorDiagnosticCompleted(diagnostics);
    const suppressStaleQueueIssues = canUseSnapshotReadyTasks(diagnostics, snapshotReadyTaskCount, snapshotBlueprintTaskIds);
    const effectiveMissingBlueprintTaskIds = effectiveDirectorMissingBlueprintTaskIds(diagnostics, snapshotBlueprintTaskIds);
    const suppressMissingBlueprintIssue = Boolean(diagnostics
        && !hasEffectiveDirectorInvalidBlueprints(diagnostics)
        && effectiveMissingBlueprintTaskIds.length === 0);
    const issues = completed
        ? (diagnostics?.issues || []).filter((issue) => !DIRECTOR_HARD_BLOCKER_ISSUES.has(issue))
        : diagnostics?.issues || [];
    const filteredIssues = issues.filter((issue) => ((!suppressStaleQueueIssues || !DIRECTOR_STALE_QUEUE_BLOCKER_ISSUES.has(issue))
        && (!suppressMissingBlueprintIssue || issue !== 'director_ready_tasks_missing_blueprints')));
    const executionBlockers = directorExecutionBlockers(diagnostics, snapshotReadyTaskCount, snapshotBlueprintTaskIds);
    const visibleIssues = [...new Set([...executionBlockers, ...filteredIssues])].slice(0, compact ? 1 : 3);
    const blocked = executionBlockers.length > 0;
    const stateLabel = completed ? 'completed' : blocked ? 'blocked' : 'ready';
    const displayedReadyTaskCount = Math.max(diagnostics?.tasks.ready_to_execute ?? 0, snapshotReadyTaskCount);
    const displayedTaskTotal = Math.max(diagnostics?.tasks.total ?? 0, snapshotTaskTotal);
    const taskReadinessLabel = completed
        ? `completed ${diagnostics?.tasks.completed ?? 0}/${diagnostics?.tasks.total ?? 0}`
        : `ready ${displayedReadyTaskCount}/${displayedTaskTotal}`;
    const llmValues = diagnostics?.llm
        ? [
            diagnostics.llm.state || (diagnostics.llm.ok ? 'ready' : 'blocked'),
            diagnostics.llm.model || diagnostics.llm.provider_id || 'model n/a',
            ...(diagnostics.llm.blocked_roles?.length ? [`blocked ${diagnostics.llm.blocked_roles.join(',')}`] : []),
        ]
        : ['checking'];
    return (_jsx("section", { className: cn(compact ? 'min-w-0' : 'border-b border-white/10 bg-slate-950/50 px-4 py-2'), "data-testid": "director-readiness-diagnostics", "aria-label": "Director readiness diagnostics", children: _jsxs("details", { className: "group h-full rounded-lg border border-indigo-500/[0.15] bg-slate-900/35 px-3 py-2", children: [_jsxs("summary", { className: "flex min-w-0 cursor-pointer list-none flex-wrap items-center gap-2 [&::-webkit-details-marker]:hidden", children: [_jsxs("div", { className: "flex shrink-0 items-center gap-2 text-xs font-medium text-indigo-100", children: [blocked ? (_jsx(AlertTriangle, { className: "h-3.5 w-3.5 text-amber-300" })) : (_jsx(CheckCircle2, { className: "h-3.5 w-3.5 text-emerald-300" })), "\u4EA4\u63A5"] }), _jsx("div", { className: "flex min-w-0 flex-1 flex-wrap items-center gap-2", children: isLoading ? (_jsxs("span", { className: "flex items-center gap-1 rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: [_jsx(Loader2, { className: "h-3 w-3 animate-spin text-indigo-300" }), "\u8BFB\u53D6\u4E2D"] })) : error ? (_jsx("span", { className: "rounded border border-red-500/25 bg-red-500/10 px-2 py-0.5 text-[10px] text-red-200", children: "\u8BCA\u65AD\u5F02\u5E38" })) : diagnostics ? (_jsxs(_Fragment, { children: [_jsxs("div", { className: cn('flex shrink-0 items-center gap-1.5 rounded border px-2 py-0.5 text-[10px]', blocked
                                            ? 'border-amber-500/25 bg-amber-500/10 text-amber-200'
                                            : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'), "data-testid": "director-readiness-state", children: [blocked ? _jsx(AlertTriangle, { className: "h-3 w-3" }) : _jsx(CheckCircle2, { className: "h-3 w-3" }), stateLabel] }), _jsx("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: taskReadinessLabel }), _jsxs("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: ["worker ", diagnostics.workers.idle, "/", diagnostics.workers.total, " idle"] }), _jsxs("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: ["LLM ", llmValues[0]] })] })) : (_jsx("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-400", children: "\u7B49\u5F85\u8BCA\u65AD\u5FEB\u7167" })) }), _jsx("span", { className: "ml-auto shrink-0 text-[10px] text-slate-500 group-open:hidden", children: "\u8BE6\u60C5" }), _jsx("span", { className: "ml-auto hidden shrink-0 text-[10px] text-slate-400 group-open:inline", children: "\u6536\u8D77" })] }), _jsxs("div", { className: "mt-2 flex min-w-0 items-center gap-3 border-t border-[var(--soft-border)] pt-2", children: [_jsx(EvidenceEndpointBadge, { endpoint: evidenceEndpoint('/v2/director/capabilities', workspace), testId: "director-capability-endpoint" }), isLoading ? (_jsxs("div", { className: "flex items-center gap-2 text-[11px] text-slate-400", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin text-slate-300" }), "\u6B63\u5728\u8BFB\u53D6\u4EFB\u52A1\u961F\u5217\u4E0E worker \u72B6\u6001"] })) : error ? (_jsxs("div", { className: "flex min-w-0 items-center gap-2 rounded border border-red-500/25 bg-red-500/10 px-2 py-1 text-[11px] text-red-200", "data-testid": "director-readiness-error", children: [_jsx(AlertTriangle, { className: "h-3.5 w-3.5 shrink-0" }), _jsx("span", { className: "truncate", children: error })] })) : diagnostics ? (_jsxs("div", { className: "flex min-w-0 flex-1 items-center gap-2 overflow-x-auto", children: [_jsx(KernelStripMetric, { icon: _jsx(ListTodo, { className: "h-3.5 w-3.5 text-cyan-300" }), label: "\u4EFB\u52A1", endpoint: diagnostics.tasks.source, values: [
                                        taskReadinessLabel,
                                        ...(effectiveMissingBlueprintTaskIds.length
                                            ? [`missing BP ${effectiveMissingBlueprintTaskIds.length}`]
                                            : []),
                                        ...(diagnostics.tasks.invalid_blueprint_task_ids?.length
                                            ? [`invalid BP ${diagnostics.tasks.invalid_blueprint_task_ids.length}`]
                                            : []),
                                        `blocked ${diagnostics.tasks.blocked}`,
                                        `running ${diagnostics.tasks.running}`,
                                    ] }), _jsx(KernelStripMetric, { icon: _jsx(Layers, { className: "h-3.5 w-3.5 text-emerald-300" }), label: "Worker", endpoint: "pool", values: [
                                        `idle ${diagnostics.workers.idle}/${diagnostics.workers.total}`,
                                        `busy ${diagnostics.workers.busy}`,
                                        `bad ${diagnostics.workers.unhealthy}`,
                                    ] }), _jsx(KernelStripMetric, { icon: _jsx(Activity, { className: "h-3.5 w-3.5 text-indigo-300" }), label: "\u72B6\u6001", endpoint: diagnostics.status.projection_source || 'projection', values: [
                                        diagnostics.status.running ? 'running' : diagnostics.status.state.toLowerCase(),
                                        `src ${diagnostics.status.source || 'none'}`,
                                    ] }), _jsx(KernelStripMetric, { icon: _jsx(Zap, { className: "h-3.5 w-3.5 text-amber-300" }), label: "LLM", endpoint: "/v2/llm/status", values: llmValues }), visibleIssues.length > 0 ? (_jsx("div", { className: "flex shrink-0 items-center gap-1", "data-testid": "director-readiness-issues", children: visibleIssues.map((issue) => (_jsx("span", { className: "rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-200", title: issue, children: formatDirectorDiagnosticIssue(issue) }, issue))) })) : null] })) : (_jsx("div", { className: "text-[11px] text-slate-500", children: "\u7B49\u5F85 Director \u8BCA\u65AD\u5FEB\u7167" })), _jsx(Button, { variant: "ghost", size: "icon", onClick: onRefresh, disabled: isLoading, title: "\u5237\u65B0 Director \u4EA4\u63A5\u8BCA\u65AD", className: "ml-auto h-7 w-7 shrink-0 text-slate-400 hover:bg-indigo-500/10 hover:text-indigo-300", children: _jsx(RefreshCw, { className: cn('h-3.5 w-3.5', isLoading && 'animate-spin') }) })] })] }) }));
}
function KernelStripMetric({ icon, label, endpoint, values, }) {
    return (_jsxs("div", { className: "flex min-w-[12rem] shrink-0 flex-wrap items-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-2 py-1", children: [icon, _jsx("span", { className: "text-[10px] font-medium text-slate-200", children: label }), _jsx(EvidenceEndpointBadge, { endpoint: endpoint, testId: `director-kernel-${label}-endpoint` }), _jsx("div", { className: "flex items-center gap-1", children: values.map((value) => (_jsx("span", { className: "rounded border border-white/10 bg-slate-950/70 px-1.5 py-0.5 text-[9px] text-slate-300", children: value }, `${label}-${value}`))) })] }));
}
export function DirectorWorkspace({ workspace, onBackToMain, tasks, workers = [], directorRunning, isStarting, isStopping = false, startBlockedReason = '', onToggleDirector, onOpenSettings, currentTaskId, currentTaskTitle, currentTaskStatus, fileEditEvents = [], executionLogs = [], llmStreamEvents = [], processStreamEvents = [], currentPhase = 'idle', factoryMode = false, taskProgressMap = new Map(), taskTraceMap, }) {
    const [activeView, setActiveView] = useState('tasks');
    const [showAIDialogue, setShowAIDialogue] = useState(true);
    const [session] = useState({
        id: `dir-${Date.now()}`,
        status: 'idle',
        logs: [],
    });
    const [selectedTaskId, setSelectedTaskId] = useState(null);
    const [terminalOutput, setTerminalOutput] = useState('');
    const [terminalClearedAt, setTerminalClearedAt] = useState(0);
    const [commandSnapshotTasks, setCommandSnapshotTasks] = useState([]);
    const [backendWorkers, setBackendWorkers] = useState([]);
    const [workerFallbackError, setWorkerFallbackError] = useState(null);
    const [workerBackendDetail, setWorkerBackendDetail] = useState({
        workerId: null,
        data: null,
        loading: false,
        error: null,
    });
    const [taskLLMEvents, setTaskLLMEvents] = useState({
        taskId: null,
        events: [],
        stats: null,
        loading: false,
        error: null,
    });
    const [taskCancelState, setTaskCancelState] = useState({
        taskId: null,
        loading: false,
        message: null,
        error: null,
    });
    const [taskCreateState, setTaskCreateState] = useState({
        loading: false,
        message: null,
        error: null,
        taskId: null,
    });
    const [directorRunEvidence, setDirectorRunEvidence] = useState({
        runId: null,
        loading: false,
        data: null,
        error: null,
    });
    const [directorRunCancelState, setDirectorRunCancelState] = useState({
        runId: null,
        loading: false,
        message: null,
        error: null,
    });
    const [directorToggleStatusEvidence, setDirectorToggleStatusEvidence] = useState({
        triggered: false,
        loading: false,
        message: null,
        error: null,
    });
    const [directorDiagnostics, setDirectorDiagnostics] = useState({
        loading: false,
        data: null,
        error: null,
    });
    const [taskBackendDetail, setTaskBackendDetail] = useState({
        taskId: null,
        data: null,
        loading: false,
        error: null,
    });
    const [capabilityHosts, setCapabilityHosts] = useState([]);
    const [capabilityError, setCapabilityError] = useState(null);
    const [isCapabilityLoading, setIsCapabilityLoading] = useState(false);
    const [kernelCacheStats, setKernelCacheStats] = useState(null);
    const [kernelLLMEvents, setKernelLLMEvents] = useState(null);
    const [kernelTokenBudgetStats, setKernelTokenBudgetStats] = useState(null);
    const [kernelDiagnosticsError, setKernelDiagnosticsError] = useState(null);
    const [isKernelDiagnosticsLoading, setIsKernelDiagnosticsLoading] = useState(false);
    const [isKernelCacheClearing, setIsKernelCacheClearing] = useState(false);
    // 用户手动切换视图的标记
    const userSwitchedViewRef = useRef(false);
    const lastPhaseRef = useRef('');
    const lastRealtimeEventCountRef = useRef(0);
    // 阶段到视图的映射
    const PHASE_TO_VIEW = {
        'idle': { view: 'tasks', label: '等待' },
        'planning': { view: 'tasks', label: '规划' },
        'analyzing': { view: 'activity', label: '分析' },
        'executing': { view: 'code', label: '代码' },
        'llm_calling': { view: 'activity', label: '思考' },
        'tool_running': { view: 'terminal', label: '执行' },
        'verification': { view: 'activity', label: '验证' },
        'completed': { view: 'tasks', label: '完成' },
        'error': { view: 'activity', label: '错误' },
    };
    // 自动切换视图基于当前阶段
    useEffect(() => {
        if (!directorRunning || userSwitchedViewRef.current)
            return;
        const phaseConfig = PHASE_TO_VIEW[currentPhase] || PHASE_TO_VIEW['idle'];
        if (currentPhase !== lastPhaseRef.current) {
            lastPhaseRef.current = currentPhase;
            if (phaseConfig.view !== activeView) {
                setActiveView(phaseConfig.view);
            }
        }
    }, [currentPhase, directorRunning, activeView]);
    useEffect(() => {
        const eventCount = executionLogs.length + llmStreamEvents.length + processStreamEvents.length;
        const previousCount = lastRealtimeEventCountRef.current;
        lastRealtimeEventCountRef.current = eventCount;
        if (!directorRunning || eventCount <= previousCount || eventCount <= 0 || userSwitchedViewRef.current)
            return;
        if (activeView !== 'activity') {
            setActiveView('activity');
        }
    }, [activeView, directorRunning, executionLogs.length, llmStreamEvents.length, processStreamEvents.length]);
    useEffect(() => {
        setCommandSnapshotTasks([]);
    }, [workspace]);
    // 用户手动点击导航时记录偏好
    const handleViewChange = useCallback((view) => {
        userSwitchedViewRef.current = true;
        setActiveView(view);
    }, []);
    useEffect(() => {
        if (!workspace || factoryMode) {
            setCapabilityHosts([]);
            setCapabilityError(null);
            return;
        }
        let cancelled = false;
        const loadCapabilities = async () => {
            setIsCapabilityLoading(true);
            setCapabilityError(null);
            const result = await getDirectorCapabilities();
            if (cancelled)
                return;
            if (result.ok && result.data) {
                setCapabilityHosts(normalizeDirectorCapabilityHosts(result.data));
            }
            else {
                setCapabilityHosts([]);
                setCapabilityError(result.error || 'Director capability matrix unavailable');
            }
            setIsCapabilityLoading(false);
        };
        void loadCapabilities();
        return () => {
            cancelled = true;
        };
    }, [factoryMode, workspace]);
    const loadKernelDiagnostics = useCallback(async () => {
        if (!workspace || factoryMode) {
            setKernelCacheStats(null);
            setKernelLLMEvents(null);
            setKernelTokenBudgetStats(null);
            setKernelDiagnosticsError(null);
            return;
        }
        setIsKernelDiagnosticsLoading(true);
        setKernelDiagnosticsError(null);
        try {
            const [cacheResult, tokenResult, llmResult] = await Promise.all([
                getRoleKernelCacheStats('director'),
                getRoleKernelTokenBudgetStats('director'),
                getRoleKernelLLMEvents('director', { role: 'director', limit: 5, workspace }),
            ]);
            const errors = [];
            if (cacheResult.ok && cacheResult.data) {
                setKernelCacheStats(cacheResult.data);
            }
            else {
                setKernelCacheStats(null);
                errors.push(cacheResult.error || 'Director LLM cache stats unavailable');
            }
            if (tokenResult.ok && tokenResult.data) {
                setKernelTokenBudgetStats(tokenResult.data);
            }
            else {
                setKernelTokenBudgetStats(null);
                errors.push(tokenResult.error || 'Director token budget stats unavailable');
            }
            if (llmResult.ok && llmResult.data) {
                setKernelLLMEvents(llmResult.data);
            }
            else {
                setKernelLLMEvents(null);
                errors.push(llmResult.error || 'Director LLM events unavailable');
            }
            setKernelDiagnosticsError(errors.length > 0 ? errors.join('；') : null);
        }
        catch (err) {
            setKernelCacheStats(null);
            setKernelLLMEvents(null);
            setKernelTokenBudgetStats(null);
            setKernelDiagnosticsError(err instanceof Error ? err.message : 'Director Kernel diagnostics unavailable');
        }
        finally {
            setIsKernelDiagnosticsLoading(false);
        }
    }, [factoryMode, workspace]);
    useEffect(() => {
        void loadKernelDiagnostics();
    }, [loadKernelDiagnostics]);
    const loadDirectorDiagnostics = useCallback(async () => {
        if (!workspace) {
            setDirectorDiagnostics({
                loading: false,
                data: null,
                error: null,
            });
            return;
        }
        setDirectorDiagnostics((prev) => ({
            ...prev,
            loading: true,
            error: null,
        }));
        try {
            const result = await getDirectorDiagnostics(workspace);
            if (result.ok && result.data) {
                setDirectorDiagnostics({
                    loading: false,
                    data: result.data,
                    error: null,
                });
            }
            else {
                setDirectorDiagnostics({
                    loading: false,
                    data: null,
                    error: result.error || 'Director diagnostics unavailable',
                });
            }
        }
        catch (err) {
            setDirectorDiagnostics({
                loading: false,
                data: null,
                error: err instanceof Error ? err.message : 'Director diagnostics unavailable',
            });
        }
    }, [workspace]);
    useEffect(() => {
        let cancelled = false;
        const syncDiagnostics = async () => {
            if (cancelled) {
                return;
            }
            await loadDirectorDiagnostics();
        };
        void syncDiagnostics();
        return () => {
            cancelled = true;
        };
    }, [directorRunning, loadDirectorDiagnostics, workspace]);
    const handleClearKernelCache = useCallback(async () => {
        setIsKernelCacheClearing(true);
        setKernelDiagnosticsError(null);
        try {
            const result = await clearRoleKernelCache('director');
            if (result.ok) {
                await loadKernelDiagnostics();
            }
            else {
                setKernelDiagnosticsError(result.error || 'Director LLM cache clear failed');
            }
        }
        catch (err) {
            setKernelDiagnosticsError(err instanceof Error ? err.message : 'Director LLM cache clear failed');
        }
        finally {
            setIsKernelCacheClearing(false);
        }
    }, [loadKernelDiagnostics]);
    useEffect(() => {
        if (!workspace) {
            setBackendWorkers([]);
            setWorkerFallbackError(null);
            setWorkerBackendDetail({
                workerId: null,
                data: null,
                loading: false,
                error: null,
            });
            return;
        }
        let cancelled = false;
        const syncWorkers = async () => {
            try {
                const result = await listDirectorWorkers(workspace);
                if (cancelled) {
                    return;
                }
                if (result.ok && Array.isArray(result.data)) {
                    setBackendWorkers(normalizeDirectorWorkerRows(result.data));
                    setWorkerFallbackError(null);
                }
                else {
                    setWorkerFallbackError(result.error || 'Director worker backend unavailable');
                }
            }
            catch (err) {
                if (!cancelled) {
                    setWorkerFallbackError(err instanceof Error ? err.message : 'Director worker backend unavailable');
                }
            }
        };
        void syncWorkers();
        return () => {
            cancelled = true;
        };
    }, [workspace, directorRunning]);
    useEffect(() => {
        const taskId = String(selectedTaskId || '').trim();
        if (!taskId) {
            setTaskBackendDetail({
                taskId: null,
                data: null,
                loading: false,
                error: null,
            });
            setTaskLLMEvents({
                taskId: null,
                events: [],
                stats: null,
                loading: false,
                error: null,
            });
            return;
        }
        let detailCancelled = false;
        setTaskBackendDetail((current) => ({
            taskId,
            data: current.taskId === taskId ? current.data : null,
            loading: true,
            error: null,
        }));
        const loadTaskBackendDetail = async () => {
            const result = await getDirectorTask(taskId, workspace);
            if (detailCancelled) {
                return;
            }
            if (result.ok && result.data) {
                setTaskBackendDetail({
                    taskId,
                    data: result.data,
                    loading: false,
                    error: null,
                });
            }
            else {
                setTaskBackendDetail({
                    taskId,
                    data: null,
                    loading: false,
                    error: result.error || 'Director task detail unavailable',
                });
            }
        };
        void loadTaskBackendDetail();
        let cancelled = false;
        setTaskLLMEvents((current) => ({
            taskId,
            events: current.taskId === taskId ? current.events : [],
            stats: current.taskId === taskId ? current.stats : null,
            loading: true,
            error: null,
        }));
        const loadTaskLLMEvents = async () => {
            const result = await getDirectorTaskKernelLLMEvents(taskId, { limit: 25, workspace });
            if (cancelled) {
                return;
            }
            if (result.ok && result.data) {
                setTaskLLMEvents({
                    taskId,
                    events: Array.isArray(result.data.events) ? result.data.events : [],
                    stats: result.data.stats || null,
                    loading: false,
                    error: null,
                });
            }
            else {
                setTaskLLMEvents({
                    taskId,
                    events: [],
                    stats: null,
                    loading: false,
                    error: result.error || 'Director task LLM events unavailable',
                });
            }
        };
        void loadTaskLLMEvents();
        return () => {
            detailCancelled = true;
            cancelled = true;
        };
    }, [selectedTaskId, workspace]);
    const visibleTasks = useMemo(() => {
        const toTaskId = (task) => String(task.id || '').trim();
        const merged = new Map();
        // Live realtime rows own volatile state. The local command snapshot only
        // reflects explicit create/cancel actions and must not replace runtime push.
        for (const task of commandSnapshotTasks) {
            const taskId = toTaskId(task);
            if (taskId) {
                merged.set(taskId, task);
            }
        }
        for (const task of tasks) {
            const taskId = toTaskId(task);
            if (taskId) {
                const existing = merged.get(taskId);
                merged.set(taskId, existing ? mergeTaskRows(existing, task) : task);
            }
        }
        const orderedIds = [];
        for (const task of commandSnapshotTasks) {
            const taskId = toTaskId(task);
            if (taskId && !orderedIds.includes(taskId)) {
                orderedIds.push(taskId);
            }
        }
        for (const task of tasks) {
            const taskId = toTaskId(task);
            if (taskId && !orderedIds.includes(taskId)) {
                orderedIds.push(taskId);
            }
        }
        return orderedIds
            .map((taskId) => merged.get(taskId))
            .filter((task) => Boolean(task));
    }, [tasks, commandSnapshotTasks]);
    const visibleWorkers = useMemo(() => mergeDirectorWorkers(workers, backendWorkers), [workers, backendWorkers]);
    const taskRealtimeTelemetry = useMemo(() => buildTaskRealtimeTelemetry(visibleTasks, fileEditEvents, taskProgressMap), [visibleTasks, fileEditEvents, taskProgressMap]);
    const executionTasks = visibleTasks.map((task) => {
        const metadata = readTaskMetadata(task);
        const adapterResult = (metadata.adapter_result && typeof metadata.adapter_result === 'object')
            ? metadata.adapter_result
            : {};
        const adapterChangedFiles = [
            ...readStringList(adapterResult.new_files),
            ...readStringList(adapterResult.modified_files),
            ...readStringList(adapterResult.deleted_files),
            ...readStringList(adapterResult.changed_files),
        ].filter((item, index, all) => Boolean(item) && all.indexOf(item) === index);
        const taskId = String(task.id || '').trim();
        const rawStatus = String(task.status || task.state || '').trim().toLowerCase();
        const isCurrent = currentTaskId
            ? task.id === currentTaskId
            : currentTaskTitle
                ? (task.title || task.subject || task.goal || '').trim() === String(currentTaskTitle || '').trim()
                : false;
        const status = resolveTaskExecutionStatus({
            rawStatus,
            done: Boolean(task.done),
            completed: Boolean(task.completed),
            directorRunning,
            isCurrent,
        });
        const title = readTaskString(task, ['title', 'subject', 'goal', 'id']) || '未命名任务';
        const goal = readTaskString(task, ['goal', 'pm_task_goal', 'summary']);
        const description = readTaskString(task, ['description', 'goal', 'summary']);
        const lowered = `${title} ${goal}`.toLowerCase();
        const type = lowered.includes('test')
            ? 'test'
            : lowered.includes('debug') || lowered.includes('fix')
                ? 'debug'
                : lowered.includes('review') || lowered.includes('audit')
                    ? 'review'
                    : 'code';
        const budgetRaw = (metadata.budget && typeof metadata.budget === 'object')
            ? metadata.budget
            : task.budget;
        const budgetInfo = budgetRaw && typeof budgetRaw === 'object'
            ? {
                used: Number(budgetRaw.used) || 0,
                total: Number(budgetRaw.total) || 100,
                unit: (budgetRaw.unit || 'tokens'),
            }
            : undefined;
        const createdAt = task.created_at || task.createdAt;
        const startedAt = task.started_at || task.startedAt;
        const completedAt = task.completed_at || task.completedAt;
        let actualTime;
        if (completedAt && startedAt) {
            actualTime = new Date(completedAt).getTime() - new Date(startedAt).getTime();
        }
        else if (startedAt && status === 'running') {
            actualTime = Date.now() - new Date(startedAt).getTime();
        }
        const priorityValue = readTaskString(task, ['priority']) || 'medium';
        const dependencies = task.dependencies
            || task.blocked_by
            || (Array.isArray(metadata.dependencies) ? metadata.dependencies : undefined);
        const blockedBy = readTaskStringList(task, ['blocked_by', 'blockedBy']);
        const tags = task.tags || (Array.isArray(metadata.tags) ? metadata.tags : []);
        const telemetry = taskRealtimeTelemetry.get(taskId);
        const filesModified = Math.max(Number(task.files_modified || metadata.files_modified || 0) || 0, adapterChangedFiles.length, telemetry?.filesTouchedCount || 0);
        const retries = Number(task.retries
            || task.retry_count
            || metadata.retry_count
            || metadata.retries
            || 0) || 0;
        const assignedWorker = readTaskString(task, [
            'assigned_worker',
            'worker_id',
            'claimed_by',
            'assignedTo',
            'assignee',
        ]);
        const claimedBy = readTaskString(task, ['claimed_by', 'claimedBy', 'worker_id']);
        const identityTokens = new Set(resolveTaskIdentityCandidates(task));
        const taskScopedFileEvents = fileEditEvents.filter((event) => {
            const token = toTaskToken(event.taskId);
            return Boolean(token && identityTokens.has(token));
        });
        const progressFromTelemetry = telemetry?.phaseIndex !== undefined
            && telemetry?.phaseTotal !== undefined
            && telemetry.phaseTotal > 0
            ? Math.min(99, Math.max(1, Math.round((telemetry.phaseIndex / telemetry.phaseTotal) * 100)))
            : undefined;
        return {
            id: String(task.id || title),
            name: title,
            rawStatus,
            goal,
            description,
            status,
            type,
            priority: String(priorityValue).toLowerCase(),
            progress: status === 'running' ? (progressFromTelemetry ?? 50) : status === 'completed' ? 100 : status === 'failed' ? 0 : undefined,
            output: readTaskString(task, ['summary', 'output', 'result_summary']),
            error: status === 'failed' || status === 'blocked'
                ? readTaskString(task, ['error', 'error_detail', 'state', 'status'])
                : '',
            budget: budgetInfo,
            estimatedTime: task.estimated_time || task.estimatedTime,
            actualTime,
            dependencies: Array.isArray(dependencies) ? dependencies.map((item) => String(item)) : undefined,
            blockedBy,
            tags: Array.isArray(tags) ? tags.map((tag) => String(tag)) : [],
            createdAt,
            startedAt,
            completedAt,
            assignedWorker: assignedWorker || undefined,
            claimedBy: claimedBy || undefined,
            pmTaskId: readTaskString(task, ['pm_task_id', 'task_id']) || taskId || undefined,
            blueprintId: readTaskString(task, ['blueprint_id', 'blueprintId']) || undefined,
            blueprintPath: readTaskString(task, ['blueprint_path', 'runtime_blueprint_path']) || undefined,
            source: readTaskString(task, ['director_task_source', 'source']) || undefined,
            filesModified,
            executionSteps: readTaskStringList(task, ['execution_steps', 'executionSteps', 'execution_checklist', 'steps', 'checklist']),
            acceptanceCriteria: [
                ...readStringList(task.acceptance),
                ...readTaskStringList(task, ['acceptance_criteria', 'acceptanceCriteria', 'acceptance']),
            ].filter((item, index, all) => all.indexOf(item) === index),
            targetFiles: [
                ...readTaskStringList(task, ['target_files', 'scope_paths', 'files', 'targetFiles']),
                ...adapterChangedFiles,
            ].filter((item, index, all) => all.indexOf(item) === index),
            // Progress tracking from telemetry (merged from taskProgressMap and fileEditEvents)
            retries: telemetry?.retryCount ?? retries,
            maxRetries: telemetry?.maxRetries,
            currentFilePath: telemetry?.currentFilePath || readTaskString(task, ['current_file', 'current_file_path']) || adapterChangedFiles.at(-1),
            activityUpdatedAt: telemetry?.activityUpdatedAt,
            lineStats: telemetry?.lineStats || metadata.line_stats,
            operationStats: telemetry?.operationStats || metadata.operation_stats,
            currentPhase: telemetry?.currentPhase,
            phaseIndex: telemetry?.phaseIndex,
            phaseTotal: telemetry?.phaseTotal,
            taskScopedFileEvents,
        };
    });
    const executionTaskMap = useMemo(() => {
        const mapping = new Map();
        executionTasks.forEach((task) => mapping.set(task.id, task));
        return mapping;
    }, [executionTasks]);
    const snapshotReadyTaskCount = useMemo(() => executionTasks.filter((task) => (task.status === 'pending' || task.status === 'running') &&
        Boolean(String(task.blueprintId || task.blueprintPath || '').trim())).length, [executionTasks]);
    const snapshotBlueprintTaskIds = useMemo(() => {
        const taskIds = new Set();
        for (const task of visibleTasks) {
            const blueprintRef = readTaskString(task, [
                'blueprint_id',
                'blueprintId',
                'blueprint_path',
                'runtime_blueprint_path',
            ]);
            if (!blueprintRef) {
                continue;
            }
            for (const candidate of resolveTaskIdentityCandidates(task)) {
                taskIds.add(candidate);
            }
        }
        return taskIds;
    }, [visibleTasks]);
    const snapshotTaskTotal = executionTasks.length;
    const directorStarting = Boolean(isStarting);
    const directorStopping = Boolean(isStopping);
    const isExecuting = directorRunning || directorStarting || directorStopping;
    const sessionStatus = resolveSessionStatus(directorRunning || directorStopping, directorStarting, executionTasks);
    const handleTaskSelect = useCallback((taskId) => {
        setSelectedTaskId(taskId);
        setTaskCancelState({
            taskId,
            loading: false,
            message: null,
            error: null,
        });
        const task = executionTasks.find(t => t.id === taskId);
        if (task) {
            setTerminalOutput(`选中任务: ${task.name}\n状态: ${task.status}\n类型: ${task.type}\n`);
        }
    }, [executionTasks]);
    const handleWorkerSelect = useCallback(async (workerId) => {
        const normalizedWorkerId = String(workerId || '').trim();
        if (!normalizedWorkerId) {
            return;
        }
        setWorkerBackendDetail({
            workerId: normalizedWorkerId,
            data: null,
            loading: true,
            error: null,
        });
        setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] 读取 Director worker: ${normalizedWorkerId}\n`);
        try {
            const result = await getDirectorWorker(normalizedWorkerId, workspace);
            if (!result.ok || !result.data) {
                setWorkerBackendDetail({
                    workerId: normalizedWorkerId,
                    data: null,
                    loading: false,
                    error: result.error || 'Director worker detail unavailable',
                });
                return;
            }
            setWorkerBackendDetail({
                workerId: normalizedWorkerId,
                data: result.data,
                loading: false,
                error: null,
            });
        }
        catch (error) {
            setWorkerBackendDetail({
                workerId: normalizedWorkerId,
                data: null,
                loading: false,
                error: error instanceof Error ? error.message : 'Director worker detail unavailable',
            });
        }
    }, [workspace]);
    const handleTaskCreate = useCallback(async (draft) => {
        const subject = String(draft.subject || '').trim();
        if (!subject) {
            return;
        }
        const selectedTask = selectedTaskId ? executionTaskMap.get(selectedTaskId) || null : null;
        const selectedTaskIdForMetadata = selectedTask?.pmTaskId || selectedTask?.id || `director-desktop-${Date.now()}`;
        const acceptance = selectedTask?.acceptanceCriteria?.length
            ? selectedTask.acceptanceCriteria
            : [`Desktop-created Director task: ${subject}`];
        const payload = {
            subject,
            description: String(draft.description || subject).trim() || subject,
            command: null,
            priority: draft.priority,
            timeout_seconds: Math.max(30, Math.round(Number(draft.timeoutSeconds) || 300)),
            metadata: {
                pm_task_id: selectedTaskIdForMetadata,
                pm_task_title: selectedTask?.name || subject,
                pm_task_status: selectedTask?.status || 'desktop_created',
                acceptance,
                blueprint_id: selectedTask?.blueprintId || null,
                blueprint_path: selectedTask?.blueprintPath || null,
                runtime_blueprint_path: selectedTask?.blueprintPath || null,
                guardrails: {
                    source: 'director_desktop_task_create',
                },
                context_snapshot_ref: null,
            },
        };
        setTaskCreateState({
            loading: true,
            message: null,
            error: null,
            taskId: null,
        });
        setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] 创建 Director 任务: ${subject}\n`);
        try {
            const result = await createDirectorTask(payload, workspace);
            if (!result.ok || !result.data) {
                setTaskCreateState({
                    loading: false,
                    message: null,
                    error: result.error || 'Director task create failed',
                    taskId: null,
                });
                return;
            }
            const createdTaskId = String(result.data.id || result.data.task_id || subject).trim();
            const createdTask = normalizeDirectorCreatedTaskRow(result.data, payload, createdTaskId);
            if (createdTask) {
                setCommandSnapshotTasks((current) => upsertDirectorCommandSnapshotTaskRow(current, createdTask));
            }
            setTaskCreateState({
                loading: false,
                message: `已创建 Director 任务: ${createdTaskId}`,
                error: null,
                taskId: createdTaskId,
            });
            setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director 任务已创建: ${createdTaskId}\n`);
            if (createdTaskId) {
                setSelectedTaskId(createdTaskId);
            }
            try {
                const refreshed = await listDirectorTaskSnapshotRows(directorRunning, workspace);
                if (refreshed.ok && Array.isArray(refreshed.data)) {
                    const refreshedTasks = refreshed.data;
                    setCommandSnapshotTasks(createdTask ? upsertDirectorCommandSnapshotTaskRow(refreshedTasks, createdTask) : refreshedTasks);
                }
            }
            catch {
                // The create evidence is still valid if the command snapshot read fails.
            }
        }
        catch (error) {
            setTaskCreateState({
                loading: false,
                message: null,
                error: error instanceof Error ? error.message : 'Director task create failed',
                taskId: null,
            });
        }
    }, [directorRunning, executionTaskMap, selectedTaskId, workspace]);
    const handleTaskCancel = useCallback(async (taskId) => {
        const normalizedTaskId = String(taskId || '').trim();
        if (!normalizedTaskId) {
            return;
        }
        const startedAt = new Date().toLocaleTimeString();
        setTaskCancelState({
            taskId: normalizedTaskId,
            loading: true,
            message: null,
            error: null,
        });
        setTerminalOutput((prev) => `${prev}[${startedAt}] 请求取消 Director 任务: ${normalizedTaskId}\n`);
        try {
            const result = await cancelDirectorTask(normalizedTaskId, workspace);
            if (!result.ok || !result.data) {
                const error = result.error || 'Director task cancel failed';
                setTaskCancelState({
                    taskId: normalizedTaskId,
                    loading: false,
                    message: null,
                    error,
                });
                setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director 任务取消失败: ${error}\n`);
                return;
            }
            const responseTaskId = String(result.data.task_id || result.data.id || normalizedTaskId).trim();
            const status = String(result.data.status || '').trim();
            const message = status
                ? `取消请求已提交: ${responseTaskId} (${status})`
                : `取消请求已提交: ${responseTaskId}`;
            setTaskCancelState({
                taskId: normalizedTaskId,
                loading: false,
                message,
                error: null,
            });
            setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director 任务取消请求已提交: ${responseTaskId}${status ? ` status=${status}` : ''}\n`);
            try {
                const refreshed = await listDirectorTaskSnapshotRows(directorRunning, workspace);
                if (refreshed.ok && Array.isArray(refreshed.data)) {
                    setCommandSnapshotTasks(refreshed.data);
                }
            }
            catch {
                // Keep the submitted cancellation evidence visible even if the command snapshot read fails.
            }
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error || 'Director task cancel failed');
            setTaskCancelState({
                taskId: normalizedTaskId,
                loading: false,
                message: null,
                error: message,
            });
            setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director 任务取消失败: ${message}\n`);
        }
    }, [directorRunning, workspace]);
    const loadDirectorRunEvidence = useCallback(async (runId, options = {}) => {
        const normalizedRunId = String(runId || '').trim();
        if (!normalizedRunId) {
            return;
        }
        setDirectorRunEvidence((current) => ({
            runId: normalizedRunId,
            loading: true,
            data: options.preserveData && current.runId === normalizedRunId ? current.data : null,
            error: null,
        }));
        if (!options.preserveCancel) {
            setDirectorRunCancelState({
                runId: normalizedRunId,
                loading: false,
                message: null,
                error: null,
            });
        }
        try {
            const result = await getDirectorRun(normalizedRunId, workspace);
            if (!result.ok || !result.data) {
                setDirectorRunEvidence({
                    runId: normalizedRunId,
                    loading: false,
                    data: null,
                    error: result.error || 'Director run evidence unavailable',
                });
                return;
            }
            setDirectorRunEvidence({
                runId: normalizedRunId,
                loading: false,
                data: result.data,
                error: null,
            });
        }
        catch (error) {
            setDirectorRunEvidence({
                runId: normalizedRunId,
                loading: false,
                data: null,
                error: error instanceof Error ? error.message : 'Director run evidence unavailable',
            });
        }
    }, [workspace]);
    const handleCancelDirectorRun = useCallback(async () => {
        const normalizedRunId = String(directorRunEvidence.runId || '').trim();
        if (!normalizedRunId) {
            return;
        }
        setDirectorRunCancelState({
            runId: normalizedRunId,
            loading: true,
            message: null,
            error: null,
        });
        setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] 请求取消 Director run: ${normalizedRunId}\n`);
        try {
            const result = await cancelDirectorRun(normalizedRunId, workspace);
            if (!result.ok || !result.data) {
                const error = result.error || 'Director run cancel failed';
                setDirectorRunCancelState({
                    runId: normalizedRunId,
                    loading: false,
                    message: null,
                    error,
                });
                setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director run 取消失败: ${error}\n`);
                return;
            }
            const statusText = String(result.data.status || 'unknown').trim() || 'unknown';
            setDirectorRunEvidence({
                runId: normalizedRunId,
                loading: false,
                data: result.data,
                error: null,
            });
            setDirectorRunCancelState({
                runId: normalizedRunId,
                loading: false,
                message: `取消运行已提交: ${statusText}`,
                error: null,
            });
            setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director run 取消请求已提交: ${normalizedRunId} status=${statusText}\n`);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error || 'Director run cancel failed');
            setDirectorRunCancelState({
                runId: normalizedRunId,
                loading: false,
                message: null,
                error: message,
            });
            setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director run 取消失败: ${message}\n`);
        }
    }, [directorRunEvidence.runId, workspace]);
    const toggleDirectorWithStatusEvidence = useCallback(async () => {
        setDirectorToggleStatusEvidence({
            triggered: true,
            loading: true,
            message: null,
            error: null,
        });
        try {
            const accepted = await Promise.resolve(onToggleDirector());
            setDirectorToggleStatusEvidence({
                triggered: true,
                loading: false,
                message: accepted === false ? '命令未被接受。' : DIRECTOR_COMMAND_ACCEPTED_MESSAGE,
                error: accepted === false ? 'Director command was not accepted' : null,
            });
        }
        catch (error) {
            setDirectorToggleStatusEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: error instanceof Error ? error.message : 'Director command unavailable',
            });
        }
    }, [onToggleDirector]);
    const directorDiagnosticExecutionReason = useMemo(() => formatDirectorExecutionBlockReason(directorDiagnostics.data, snapshotReadyTaskCount, snapshotBlueprintTaskIds), [directorDiagnostics.data, snapshotBlueprintTaskIds, snapshotReadyTaskCount]);
    const executionBlockReasonForStart = factoryMode
        ? '工厂模式下由 Factory 编排 Director，不能在嵌入层直接启动。'
        : !directorRunning
            ? startBlockedReason || directorDiagnosticExecutionReason
            : '';
    const directorToggleBusy = directorToggleStatusEvidence.loading;
    const directorControlBusyReason = directorStarting
        ? 'Director 正在启动，请等待状态回传。'
        : directorStopping
            ? 'Director 正在停止，请等待状态回传。'
            : directorToggleBusy
                ? 'Director 命令提交中，请等待 runtime.v2 回传。'
                : '';
    const executionDisabledReason = executionBlockReasonForStart || directorControlBusyReason;
    const directorPrimaryActionLabel = directorStarting
        ? '启动中'
        : directorStopping
            ? '停止中'
            : directorRunning
                ? '停止'
                : '执行';
    const handleExecute = useCallback(async () => {
        if (directorControlBusyReason) {
            setTerminalOutput(prev => `${prev}[${new Date().toLocaleTimeString()}] Director 控制请求等待中: ${directorControlBusyReason}\n`);
            return;
        }
        if (!directorRunning && executionBlockReasonForStart) {
            setTerminalOutput(prev => `${prev}[${new Date().toLocaleTimeString()}] Director 启动被阻断: ${executionBlockReasonForStart}\n`);
            return;
        }
        const nextAction = directorRunning ? '停止' : '启动';
        const targetName = selectedTaskId
            ? executionTasks.find((task) => task.id === selectedTaskId)?.name || selectedTaskId
            : currentTaskTitle || '当前任务队列';
        const newLog = `[${new Date().toLocaleTimeString()}] ${nextAction} Director 执行: ${targetName}`;
        setTerminalOutput(prev => prev + newLog + '\n');
        if (directorRunning) {
            await toggleDirectorWithStatusEvidence();
            return;
        }
        const payload = {
            workspace,
            execution_mode: 'parallel',
        };
        if (selectedTaskId) {
            payload.task_id = selectedTaskId;
            payload.task_filter = selectedTaskId;
        }
        const result = await runDirector(payload);
        if (!result.ok || !result.data) {
            setTerminalOutput(prev => `${prev}[${new Date().toLocaleTimeString()}] Director 任务启动失败: ${result.error || 'unknown error'}\n`);
            return;
        }
        const data = result.data;
        setTerminalOutput(prev => `${prev}[${new Date().toLocaleTimeString()}] Director run 已创建: ${data.run_id} queued=${data.tasks_queued}\n`);
        if (data.run_id) {
            void loadDirectorRunEvidence(data.run_id);
        }
    }, [
        currentTaskTitle,
        directorControlBusyReason,
        directorRunning,
        executionTasks,
        executionBlockReasonForStart,
        loadDirectorRunEvidence,
        selectedTaskId,
        toggleDirectorWithStatusEvidence,
        workspace,
    ]);
    const handlePause = useCallback(async () => {
        if (!directorRunning || directorControlBusyReason) {
            return;
        }
        setTerminalOutput(prev => prev + `[${new Date().toLocaleTimeString()}] 停止 Director 执行\n`);
        await toggleDirectorWithStatusEvidence();
    }, [directorControlBusyReason, directorRunning, toggleDirectorWithStatusEvidence]);
    const handleReset = useCallback(() => {
        setSelectedTaskId(null);
        setTerminalOutput('');
        setTerminalClearedAt(Date.now());
    }, []);
    const handleClearTerminal = useCallback(() => {
        setTerminalOutput('');
        setTerminalClearedAt(Date.now());
    }, []);
    useEffect(() => {
        const statusText = String(currentTaskStatus || '').trim();
        if (directorRunning) {
            const currentLabel = String(currentTaskTitle || currentTaskId || '等待任务').trim();
            setTerminalOutput((prev) => {
                const nextLine = `[${new Date().toLocaleTimeString()}] Director 运行中: ${currentLabel}${statusText ? ` (${statusText})` : ''}\n`;
                if (prev.includes(nextLine)) {
                    return prev;
                }
                return prev + nextLine;
            });
            return;
        }
        if (statusText) {
            setTerminalOutput((prev) => {
                const nextLine = `[${new Date().toLocaleTimeString()}] Director 状态: ${statusText}\n`;
                if (prev.includes(nextLine)) {
                    return prev;
                }
                return prev + nextLine;
            });
        }
    }, [currentTaskId, currentTaskStatus, currentTaskTitle, directorRunning]);
    const terminalStreamOutput = useMemo(() => formatDirectorTerminalStreamOutput(executionLogs, processStreamEvents, terminalClearedAt), [executionLogs, processStreamEvents, terminalClearedAt]);
    const terminalPanelOutput = useMemo(() => [terminalOutput.trimEnd(), terminalStreamOutput].filter(Boolean).join('\n'), [terminalOutput, terminalStreamOutput]);
    const runningTasks = executionTasks.filter(t => t.status === 'running').length;
    const completedTasks = executionTasks.filter(t => t.status === 'completed').length;
    const failedTasks = executionTasks.filter(t => t.status === 'failed').length;
    const pendingTasks = executionTasks.filter(t => t.status === 'pending').length;
    const totalTasks = executionTasks.length;
    const progress = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
    const handleRefreshDirectorRun = useCallback(() => {
        const normalizedRunId = String(directorRunEvidence.runId || '').trim();
        if (!normalizedRunId)
            return;
        void loadDirectorRunEvidence(normalizedRunId, {
            preserveData: true,
            preserveCancel: true,
        });
    }, [directorRunEvidence.runId, loadDirectorRunEvidence]);
    const directorRunCancelDisabled = !directorRunEvidence.runId ||
        directorRunEvidence.loading ||
        directorRunCancelState.loading ||
        isDirectorRunTerminal(directorRunEvidence.data?.status);
    const shouldShowSideAIDialogue = showAIDialogue && activeView !== 'workbench' && activeView !== 'strategy';
    return (_jsxs("div", { "data-testid": "director-workspace", className: "soft-app-bg soft-ambient relative flex flex-col h-full text-slate-100 overflow-hidden", children: [!factoryMode && (_jsxs("header", { className: "soft-panel h-14 flex items-center justify-between px-4 border-b border-[var(--soft-border)]", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsxs(Button, { variant: "ghost", size: "sm", onClick: onBackToMain, "data-testid": "director-workspace-back", className: "text-slate-400 hover:text-slate-100 hover:bg-white/5", children: [_jsx(ChevronLeft, { className: "w-4 h-4 mr-1" }), "\u8FD4\u56DE"] }), _jsxs("div", { className: "flex items-center gap-3", children: [_jsxs("div", { className: "relative", children: [_jsx("div", { className: "soft-raised w-8 h-8 rounded-lg flex items-center justify-center", children: _jsx(Hammer, { className: "w-4 h-4 text-slate-200" }) }), sessionStatus === 'running' && (_jsx("div", { className: "absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-emerald-500" }))] }), _jsxs("div", { children: [_jsx("h1", { className: "text-sm font-semibold text-slate-100", children: "Director" }), _jsx("p", { className: "text-[10px] text-slate-500 uppercase tracking-wider", children: "Director Console" })] })] })] }), _jsxs("div", { className: "flex items-center gap-4", children: [_jsxs("div", { className: "soft-chip flex items-center gap-1 px-2 py-1 rounded-lg", children: [_jsx(Clock, { className: "w-3.5 h-3.5 text-slate-400" }), _jsx("span", { className: "text-xs text-slate-400", children: "\u672A\u9886\u53D6:" }), _jsx("span", { className: "text-xs font-mono text-slate-300 min-w-[20px] text-center", children: pendingTasks }), _jsx("span", { className: "text-slate-600", children: "|" }), _jsx(Loader2, { className: "w-3.5 h-3.5 text-blue-400 animate-spin" }), _jsx("span", { className: "text-xs text-blue-400 font-medium min-w-[20px] text-center", children: runningTasks }), _jsx("span", { className: "text-slate-600", children: "|" }), _jsx(CheckCircle2, { className: "w-3.5 h-3.5 text-emerald-400" }), _jsx("span", { className: "text-xs text-emerald-400 font-medium min-w-[20px] text-center", children: completedTasks }), failedTasks > 0 && (_jsxs(_Fragment, { children: [_jsx("span", { className: "text-slate-600", children: "|" }), _jsx(AlertTriangle, { className: "w-3.5 h-3.5 text-red-400" }), _jsx("span", { className: "text-xs text-red-400 font-medium min-w-[20px] text-center", children: failedTasks })] }))] }), _jsxs("div", { className: "soft-chip flex items-center gap-2 px-3 py-1.5 rounded-lg", children: [_jsx(Activity, { className: "w-4 h-4 text-slate-400" }), _jsx("span", { className: "text-xs text-slate-400", children: "\u8FDB\u5EA6" }), _jsxs("span", { className: "text-xs font-mono text-slate-300", children: [completedTasks, "/", totalTasks] }), _jsx("div", { className: "soft-divider w-px h-3 mx-1" }), _jsx("div", { className: "w-20 h-1.5 rounded-full bg-slate-800 overflow-hidden", children: _jsx("div", { className: "soft-progress h-full rounded-full transition-all duration-500", style: { width: `${progress}%` } }) }), _jsxs("span", { className: "text-xs font-mono text-slate-500", children: [progress, "%"] })] }), currentTaskTitle && directorRunning && (_jsxs("div", { className: "soft-chip flex items-center gap-2 px-3 py-1.5 rounded-lg max-w-[250px]", children: [_jsx(Loader2, { className: "w-3.5 h-3.5 text-slate-300 animate-spin flex-shrink-0" }), _jsxs("span", { className: "text-xs text-slate-300 truncate", title: currentTaskTitle || '', children: ["\u6B63\u5728\u6267\u884C: ", currentTaskTitle] })] })), failedTasks > 0 && (_jsxs("div", { className: "flex items-center gap-1.5 px-2 py-1 rounded-lg bg-red-500/10 border border-red-500/20", children: [_jsx(AlertTriangle, { className: "w-3.5 h-3.5 text-red-400" }), _jsxs("span", { className: "text-xs text-red-400", children: [failedTasks, " \u5931\u8D25"] })] }))] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs(Button, { variant: "outline", size: "sm", onClick: handleExecute, "data-testid": "director-workspace-execute", disabled: Boolean(executionDisabledReason) || directorToggleBusy, title: executionDisabledReason || undefined, className: "soft-chip border-[var(--soft-border)] text-slate-300 hover:bg-white/5", children: [directorStarting || directorStopping || directorToggleBusy ? (_jsx(Loader2, { className: "w-3.5 h-3.5 mr-1.5 animate-spin" })) : (_jsx(Play, { className: "w-3.5 h-3.5 mr-1.5" })), directorPrimaryActionLabel] }), _jsx(Button, { variant: "ghost", size: "icon", onClick: () => { void handlePause(); }, "data-testid": "director-workspace-pause", disabled: !directorRunning || Boolean(directorControlBusyReason), className: "text-slate-400 hover:text-slate-200 hover:bg-white/5", children: _jsx(Pause, { className: "w-4 h-4" }) }), _jsx(Button, { variant: "ghost", size: "icon", onClick: handleReset, "data-testid": "director-workspace-reset", className: "text-slate-400 hover:text-slate-100", children: _jsx(RotateCcw, { className: "w-4 h-4" }) }), _jsx("div", { className: "soft-divider w-px h-6 mx-2" }), _jsx(Button, { variant: "ghost", size: "icon", onClick: () => setShowAIDialogue(!showAIDialogue), className: cn('text-slate-400 hover:text-slate-100', showAIDialogue && 'text-slate-200 bg-white/5'), children: _jsx(MessageSquare, { className: "w-4 h-4" }) }), _jsx(Button, { variant: "ghost", size: "icon", onClick: onOpenSettings, disabled: !onOpenSettings, "data-testid": "director-workspace-open-settings", title: onOpenSettings ? '系统配置' : '系统配置需由主界面打开', className: "text-slate-400 hover:text-slate-100", children: _jsx(Settings, { className: "w-4 h-4" }) })] })] })), !factoryMode ? (_jsxs("section", { className: "grid gap-2 border-b border-[var(--soft-border)] bg-[var(--soft-surface-muted)] px-4 py-2 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)_minmax(0,1.2fr)]", "data-testid": "director-operational-evidence-grid", "aria-label": "Director operational evidence", children: [_jsx(DirectorCapabilityStrip, { hosts: capabilityHosts, isLoading: isCapabilityLoading, error: capabilityError, compact: true }), _jsx(DirectorKernelDiagnosticsStrip, { cacheStats: kernelCacheStats, llmEvents: kernelLLMEvents, tokenBudgetStats: kernelTokenBudgetStats, isLoading: isKernelDiagnosticsLoading, isClearing: isKernelCacheClearing, error: kernelDiagnosticsError, onRefresh: () => void loadKernelDiagnostics(), onClearCache: () => void handleClearKernelCache(), workspace: workspace, compact: true }), _jsx(DirectorReadinessDiagnosticsStrip, { diagnostics: directorDiagnostics.data, isLoading: directorDiagnostics.loading, error: directorDiagnostics.error, onRefresh: () => void loadDirectorDiagnostics(), compact: true, workspace: workspace, snapshotReadyTaskCount: snapshotReadyTaskCount, snapshotTaskTotal: snapshotTaskTotal, snapshotBlueprintTaskIds: snapshotBlueprintTaskIds })] })) : (_jsx(DirectorReadinessDiagnosticsStrip, { diagnostics: directorDiagnostics.data, isLoading: directorDiagnostics.loading, error: directorDiagnostics.error, onRefresh: () => void loadDirectorDiagnostics(), compact: true, workspace: workspace, snapshotReadyTaskCount: snapshotReadyTaskCount, snapshotTaskTotal: snapshotTaskTotal, snapshotBlueprintTaskIds: snapshotBlueprintTaskIds })), directorRunEvidence.runId && (_jsx(RoleRunEvidenceStrip, { tone: "cyan", testId: "director-run-evidence", endpoint: `/v2/director/runs/${directorRunEvidence.runId}`, workspace: workspace, loading: directorRunEvidence.loading, error: directorRunEvidence.error, status: directorRunEvidence.data?.status, details: directorRunEvidence.data ? [`queued=${directorRunEvidence.data.tasks_queued ?? 0}`] : [], message: directorRunEvidence.data?.message, refreshTestId: "director-run-refresh", refreshDisabled: !directorRunEvidence.runId || directorRunEvidence.loading, refreshLoading: directorRunEvidence.loading, onRefresh: handleRefreshDirectorRun, cancelTestId: "director-run-cancel", cancelDisabled: directorRunCancelDisabled, cancelLoading: directorRunCancelState.loading, onCancel: () => { void handleCancelDirectorRun(); }, cancelResultTestId: "director-run-cancel-result", cancelResultEndpoint: `/v2/director/runs/${directorRunEvidence.runId}/cancel`, cancelResultVisible: directorRunCancelState.runId === directorRunEvidence.runId
                    && (directorRunCancelState.loading || Boolean(directorRunCancelState.message) || Boolean(directorRunCancelState.error)), cancelResultLoading: directorRunCancelState.loading, cancelResultMessage: directorRunCancelState.message, cancelResultError: directorRunCancelState.error })), directorToggleStatusEvidence.triggered && (_jsx("div", { className: "border-b border-white/10 bg-slate-950/70 px-4 py-2 text-xs text-slate-300", "data-testid": "director-toggle-status-evidence", children: _jsxs("div", { className: "flex flex-wrap items-center gap-x-3 gap-y-1", children: [_jsx("span", { className: "font-medium text-slate-100", children: "Director command evidence" }), _jsx(EvidenceEndpointBadge, { endpoint: DIRECTOR_RUNTIME_PUSH_ENDPOINT, testId: "director-toggle-status-endpoint" }), directorToggleStatusEvidence.loading ? (_jsx("span", { className: "text-slate-400", children: "\u6B63\u5728\u63D0\u4EA4\u547D\u4EE4..." })) : directorToggleStatusEvidence.error ? (_jsx("span", { className: "text-rose-300", children: directorToggleStatusEvidence.error })) : (_jsx("span", { className: "text-emerald-300", children: directorToggleStatusEvidence.message || DIRECTOR_COMMAND_ACCEPTED_MESSAGE }))] }) })), _jsxs("div", { className: "flex-1 flex overflow-hidden", children: [_jsxs("nav", { className: "w-14 flex flex-col items-center py-4 gap-2 border-r border-[var(--soft-border)] bg-[var(--soft-surface-muted)]", children: [_jsx(NavButton, { icon: _jsx(ListTodo, { className: "w-4 h-4" }), label: "\u4EFB\u52A1", active: activeView === 'tasks', onClick: () => handleViewChange('tasks') }), _jsx(NavButton, { icon: _jsx(Activity, { className: "w-4 h-4" }), label: "\u5B9E\u65F6", active: activeView === 'activity', onClick: () => handleViewChange('activity') }), _jsx(NavButton, { icon: _jsx(FileCode, { className: "w-4 h-4" }), label: "\u4EE3\u7801", active: activeView === 'code', onClick: () => handleViewChange('code') }), _jsx(NavButton, { icon: _jsx(Terminal, { className: "w-4 h-4" }), label: "\u7EC8\u7AEF", active: activeView === 'terminal', onClick: () => handleViewChange('terminal') }), _jsx(NavButton, { icon: _jsx(Bug, { className: "w-4 h-4" }), label: "\u8C03\u8BD5", active: activeView === 'debug', onClick: () => handleViewChange('debug') }), _jsx(NavButton, { icon: _jsx(SlidersHorizontal, { className: "w-4 h-4" }), label: "\u7B56\u7565", active: activeView === 'strategy', onClick: () => handleViewChange('strategy') }), _jsx(NavButton, { icon: _jsx(Wrench, { className: "w-4 h-4" }), label: "\u5DE5\u4F5C\u53F0", active: activeView === 'workbench', onClick: () => handleViewChange('workbench') })] }), _jsxs(PanelGroup, { direction: "horizontal", className: "flex-1", children: [_jsx(Panel, { defaultSize: shouldShowSideAIDialogue ? 60 : 85, minSize: 40, children: _jsxs("div", { className: "h-full overflow-hidden", children: [activeView === 'tasks' && (_jsx(DirectorTaskPanelView, { tasks: executionTasks, workers: visibleWorkers, taskMap: executionTaskMap, selectedTaskId: selectedTaskId, onTaskSelect: handleTaskSelect, onExecute: handleExecute, onTaskCancel: handleTaskCancel, onTaskCreate: handleTaskCreate, isExecuting: isExecuting, isTaskCreating: taskCreateState.loading, taskCreateMessage: taskCreateState.message, taskCreateError: taskCreateState.error, isTaskCancelling: taskCancelState.taskId === selectedTaskId && taskCancelState.loading, taskCancelMessage: taskCancelState.taskId === selectedTaskId ? taskCancelState.message : null, taskCancelError: taskCancelState.taskId === selectedTaskId ? taskCancelState.error : null, taskTraceMap: taskTraceMap, workerFallbackError: workerFallbackError, workerBackendDetail: workerBackendDetail, onWorkerSelect: handleWorkerSelect, taskBackendDetail: taskBackendDetail, taskLLMEvents: taskLLMEvents, executionDisabledReason: executionDisabledReason, workspace: workspace })), activeView === 'activity' && (_jsx(RealtimeActivityPanel, { executionLogs: executionLogs, llmStreamEvents: llmStreamEvents, processStreamEvents: processStreamEvents, currentPhase: currentPhase, isRunning: directorRunning, role: "director" })), activeView === 'code' && (_jsx(DirectorCodePanel, { workspace: workspace, fileEditEvents: fileEditEvents, tasks: executionTasks })), activeView === 'terminal' && (_jsx(DirectorTerminalPanel, { output: terminalPanelOutput, onClear: handleClearTerminal })), activeView === 'debug' && (_jsx(DirectorDebugPanel, { tasks: executionTasks.filter((task) => task.status === 'failed' || task.status === 'blocked'), cancellingTaskId: taskCancelState.loading ? taskCancelState.taskId : null, onInspectTask: (taskId) => {
                                                handleTaskSelect(taskId);
                                                setActiveView('tasks');
                                            }, onCancelTask: (taskId) => { void handleTaskCancel(taskId); } })), activeView === 'strategy' && (_jsx(DirectorStrategyPanel, { workspace: workspace, tasksCount: totalTasks, runningTasks: runningTasks })), activeView === 'workbench' && (_jsx(DirectorWorkbenchPanel, { workspace: workspace, hostKind: "electron_workbench", attachmentMode: (selectedTaskId || currentTaskId) ? 'attached_readonly' : 'isolated', attachedTaskId: selectedTaskId || currentTaskId || undefined, tasksCount: totalTasks, runningTasks: runningTasks }))] }) }), shouldShowSideAIDialogue && (_jsxs(_Fragment, { children: [_jsx(PanelResizeHandle, { className: "w-1 bg-[var(--soft-border)] hover:bg-white/10 transition-colors" }), _jsx(Panel, { defaultSize: 40, minSize: 25, maxSize: 50, children: _jsx(AIDialoguePanel, { dialogueRole: "director", roleDisplayName: "Director", roleTheme: {
                                                primary: 'slate',
                                                secondary: 'slate-400',
                                                gradient: 'from-slate-500 to-slate-700',
                                            }, welcomeMessage: "Director \u6267\u884C\u7CFB\u7EDF\u5DF2\u5C31\u7EEA\u3002\u6211\u53EF\u4EE5\u5E2E\u60A8\u6267\u884C\u4EE3\u7801\u3001\u8C03\u8BD5\u95EE\u9898\u3001\u8FD0\u884C\u6D4B\u8BD5\u3002", context: {
                                                workspace,
                                                session_id: session.id,
                                                tasks_count: executionTasks.length,
                                                running_tasks: runningTasks,
                                                workers_count: visibleWorkers.length,
                                                selected_task_id: selectedTaskId || null,
                                                current_task_id: currentTaskId || null,
                                            }, workspace: workspace, hostKind: "electron_workbench", attachmentMode: (selectedTaskId || currentTaskId) ? 'attached_readonly' : 'isolated', attachedTaskId: selectedTaskId || currentTaskId || undefined, workflowExportTarget: "director", workflowExportLabel: "\u5BFC\u51FA\u6267\u884C" }) })] }))] })] }), _jsxs("footer", { className: "soft-panel-subtle h-8 flex items-center justify-between px-4 border-t border-[var(--soft-border)] text-[11px] text-slate-500", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx("div", { className: cn("w-1.5 h-1.5 rounded-full", sessionStatus === 'running' ? 'bg-emerald-500' :
                                            sessionStatus === 'paused' ? 'bg-amber-500' :
                                                sessionStatus === 'completed' ? 'bg-blue-500' : 'bg-slate-500') }), sessionStatus === 'idle' ? '就绪' :
                                        sessionStatus === 'running' ? '执行中' :
                                            sessionStatus === 'paused' ? '已暂停' : '已完成'] }), _jsxs("span", { children: ["\u4F1A\u8BDD: ", session.id.slice(0, 8)] })] }), _jsxs("div", { className: "flex items-center gap-4", children: [_jsxs("span", { children: ["\u5DE5\u4F5C\u533A: ", workspace] }), _jsx("span", { className: "text-slate-500", children: "Director Console v1.0" })] })] })] }));
}
function NavButton({ icon, label, active, onClick }) {
    return (_jsxs("button", { onClick: onClick, "aria-label": `切换到${label}`, "data-testid": `director-nav-${label}`, className: cn('w-10 h-10 cursor-pointer rounded-lg flex flex-col items-center justify-center gap-0.5 transition-all duration-200', active
            ? 'soft-raised text-slate-200'
            : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'), title: label, children: [icon, _jsx("span", { className: "text-[8px] font-medium", children: label })] }));
}
function DirectorTaskPanel({ tasks, workers, taskMap, selectedTaskId, onTaskSelect, onExecute, isExecuting, taskTraceMap, }) {
    const [expandedGroups, setExpandedGroups] = useState({
        running: true,
        pending: true,
        completed: true,
        failed: true,
        blocked: true,
    });
    const toggleGroup = (group) => {
        setExpandedGroups(prev => ({ ...prev, [group]: !prev[group] }));
    };
    // 按状态分组任务
    const groupedTasks = {
        running: tasks.filter(t => t.status === 'running'),
        pending: tasks.filter(t => t.status === 'pending'),
        blocked: tasks.filter(t => t.status === 'blocked'),
        failed: tasks.filter(t => t.status === 'failed'),
        completed: tasks.filter(t => t.status === 'completed'),
    };
    const getStatusIcon = (status) => {
        switch (status) {
            case 'completed': return _jsx(CheckCircle2, { className: "w-4 h-4 text-emerald-400" });
            case 'running': return _jsx(Loader2, { className: "w-4 h-4 text-blue-400 animate-spin" });
            case 'failed': return _jsx(AlertTriangle, { className: "w-4 h-4 text-red-400" });
            case 'blocked': return _jsx(Pause, { className: "w-4 h-4 text-yellow-400" });
            default: return _jsx("div", { className: "w-4 h-4 rounded-full border-2 border-slate-600" });
        }
    };
    const getStatusLabel = (status) => {
        switch (status) {
            case 'running': return '正在进行';
            case 'pending': return '未领取';
            case 'completed': return '已完成';
            case 'failed': return '失败';
            case 'blocked': return '阻塞';
            default: return status;
        }
    };
    const getStatusColor = (status) => {
        switch (status) {
            case 'running': return 'text-blue-400 bg-blue-500/10 border-blue-500/20';
            case 'pending': return 'text-slate-400 bg-slate-500/10 border-slate-500/20';
            case 'completed': return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20';
            case 'failed': return 'text-red-400 bg-red-500/10 border-red-500/20';
            case 'blocked': return 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20';
            default: return 'text-slate-400';
        }
    };
    const getTypeIcon = (type) => {
        switch (type) {
            case 'code': return _jsx(Code2, { className: "w-3.5 h-3.5 text-blue-400" });
            case 'test': return _jsx(CheckCircle2, { className: "w-3.5 h-3.5 text-emerald-400" });
            case 'debug': return _jsx(Bug, { className: "w-3.5 h-3.5 text-red-400" });
            case 'review': return _jsx(FileCode, { className: "w-3.5 h-3.5 text-amber-400" });
        }
    };
    const getTypeLabel = (type) => {
        switch (type) {
            case 'code': return '编码';
            case 'test': return '测试';
            case 'debug': return '调试';
            case 'review': return '审查';
        }
    };
    const getPriorityColor = (priority) => {
        switch (priority) {
            case 'critical': return 'text-red-400 bg-red-500/20';
            case 'high': return 'text-orange-400 bg-orange-500/20';
            case 'medium': return 'text-yellow-400 bg-yellow-500/20';
            case 'low': return 'text-slate-400 bg-slate-500/20';
            default: return 'text-slate-400 bg-slate-500/20';
        }
    };
    const formatDuration = (ms) => {
        if (!ms || ms <= 0)
            return '-';
        const seconds = Math.floor(ms / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);
        if (hours > 0)
            return `${hours}h ${minutes % 60}m`;
        if (minutes > 0)
            return `${minutes}m ${seconds % 60}s`;
        return `${seconds}s`;
    };
    const formatBytes = (bytes) => {
        if (!bytes || bytes <= 0)
            return '-';
        if (bytes < 1024)
            return `${bytes} B`;
        if (bytes < 1024 * 1024)
            return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    };
    // 计算总体统计
    const totalTasks = tasks.length;
    const completedCount = groupedTasks.completed.length;
    const runningCount = groupedTasks.running.length;
    const failedCount = groupedTasks.failed.length;
    const pendingCount = groupedTasks.pending.length;
    const blockedCount = groupedTasks.blocked.length;
    const progress = totalTasks > 0 ? Math.round((completedCount / totalTasks) * 100) : 0;
    // 计算总预算消耗
    const totalBudget = tasks.reduce((acc, t) => acc + (t.budget?.total || 0), 0);
    const usedBudget = tasks.reduce((acc, t) => acc + (t.budget?.used || 0), 0);
    const budgetProgress = totalBudget > 0 ? Math.round((usedBudget / totalBudget) * 100) : 0;
    const workerRows = workers
        .filter((worker) => worker && typeof worker === 'object')
        .map((worker) => {
        const taskId = String(worker.currentTaskId || '').trim();
        const taskName = taskId ? taskMap.get(taskId)?.name || taskId : '';
        return {
            id: worker.id,
            name: worker.name || worker.id,
            status: worker.status,
            taskId,
            taskName,
            healthy: worker.healthy,
            tasksCompleted: worker.tasksCompleted,
            tasksFailed: worker.tasksFailed,
        };
    });
    const workerBusyCount = workerRows.filter((worker) => worker.status === 'busy').length;
    const workerIdleCount = workerRows.filter((worker) => worker.status === 'idle').length;
    const workerFailedCount = workerRows.filter((worker) => worker.status === 'failed').length;
    const selectedTask = selectedTaskId ? taskMap.get(selectedTaskId) || null : null;
    const getWorkerStatusLabel = (status) => {
        if (status === 'busy')
            return '执行中';
        if (status === 'idle')
            return '空闲';
        if (status === 'stopping')
            return '停止中';
        if (status === 'stopped')
            return '已停止';
        if (status === 'failed')
            return '异常';
        return '未知';
    };
    const getWorkerStatusColor = (status) => {
        if (status === 'busy')
            return 'text-blue-300 border-blue-500/30 bg-blue-500/10';
        if (status === 'idle')
            return 'text-emerald-300 border-emerald-500/30 bg-emerald-500/10';
        if (status === 'stopping')
            return 'text-amber-300 border-amber-500/30 bg-amber-500/10';
        if (status === 'stopped')
            return 'text-slate-300 border-slate-500/30 bg-slate-500/10';
        if (status === 'failed')
            return 'text-red-300 border-red-500/30 bg-red-500/10';
        return 'text-slate-300 border-slate-500/30 bg-slate-500/10';
    };
    const renderCompactList = (items, empty) => {
        if (!items || items.length === 0) {
            return _jsx("span", { className: "text-slate-500", children: empty });
        }
        return (_jsx("div", { className: "flex flex-wrap gap-1", children: items.map((item) => (_jsx("span", { className: "rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-300", children: item }, item))) }));
    };
    const TaskGroup = ({ status, tasks: groupTasks }) => {
        if (groupTasks.length === 0)
            return null;
        const isExpanded = expandedGroups[status];
        return (_jsxs("div", { className: "mb-4", children: [_jsxs("button", { onClick: () => toggleGroup(status), className: cn('w-full flex items-center justify-between px-3 py-2 rounded-lg border text-xs font-medium transition-all', getStatusColor(status)), children: [_jsxs("div", { className: "flex items-center gap-2", children: [status === 'running' && _jsx(Loader2, { className: "w-3.5 h-3.5 animate-spin" }), status === 'pending' && _jsx(Clock, { className: "w-3.5 h-3.5" }), status === 'completed' && _jsx(CheckCircle2, { className: "w-3.5 h-3.5" }), status === 'failed' && _jsx(AlertTriangle, { className: "w-3.5 h-3.5" }), status === 'blocked' && _jsx(Pause, { className: "w-3.5 h-3.5" }), _jsx("span", { children: getStatusLabel(status) }), _jsxs("span", { className: "opacity-70", children: ["(", groupTasks.length, ")"] })] }), isExpanded ? _jsx(ChevronDown, { className: "w-4 h-4" }) : _jsx(ChevronRight, { className: "w-4 h-4" })] }), isExpanded && (_jsx("div", { className: "mt-2 space-y-2", children: groupTasks.map((task) => (_jsx(TaskCard, { task: task }, task.id))) }))] }));
    };
    const TaskCard = ({ task }) => {
        const isSelected = selectedTaskId === task.id;
        const budgetPercent = task.budget && task.budget.total > 0
            ? Math.round((task.budget.used / task.budget.total) * 100)
            : 0;
        const hasLineStats = Boolean(task.lineStats
            && (task.lineStats.added > 0 || task.lineStats.deleted > 0 || task.lineStats.modified > 0));
        const hasOperationStats = Boolean(task.operationStats
            && (task.operationStats.create > 0 || task.operationStats.modify > 0 || task.operationStats.delete > 0));
        const traces = taskTraceMap?.get(task.id) || [];
        const failedTrace = traces.find((t) => t.status === 'failed');
        return (_jsxs("button", { "data-testid": "director-task-item", onClick: () => onTaskSelect(task.id), className: cn('w-full p-3 rounded-xl text-left transition-all border', isSelected
                ? 'soft-raised border-[var(--soft-border)]'
                : 'bg-white/5 border-white/5 hover:border-white/10 hover:bg-white/[0.07]'), children: [_jsxs("div", { className: "flex items-start gap-3", children: [getStatusIcon(task.status), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("span", { className: "text-sm text-slate-200 font-medium truncate", children: task.name }), task.priority && (_jsx("span", { className: cn('text-[9px] px-1.5 py-0.5 rounded', getPriorityColor(task.priority)), children: task.priority === 'critical' ? '紧急' : task.priority === 'high' ? '高' : task.priority === 'medium' ? '中' : '低' }))] }), task.description && (_jsx("p", { className: "mt-1 text-[11px] text-slate-500 line-clamp-2", children: task.description }))] })] }), task.status === 'running' && (_jsxs("div", { className: "mt-3", children: [_jsxs("div", { className: "flex items-center justify-between text-[10px] text-slate-400 mb-1", children: [_jsx("span", { children: "\u8FDB\u5EA6" }), _jsxs("span", { children: [task.progress || 0, "%"] })] }), _jsx("div", { className: "h-1.5 rounded-full bg-slate-800 overflow-hidden", children: _jsx("div", { className: "soft-progress h-full rounded-full transition-all", style: { width: `${task.progress || 0}%` } }) })] })), _jsxs("div", { className: "mt-3 grid grid-cols-3 gap-2 text-[10px]", children: [_jsxs("div", { className: "flex items-center gap-1.5 text-slate-400", children: [getTypeIcon(task.type), _jsx("span", { children: getTypeLabel(task.type) })] }), _jsxs("div", { className: "flex items-center gap-1.5 text-slate-400", children: [_jsx(Clock, { className: "w-3 h-3" }), _jsx("span", { children: formatDuration(task.actualTime) })] }), _jsxs("div", { className: "flex items-center gap-1.5 text-slate-400", children: [_jsx(FileCode, { className: "w-3 h-3" }), _jsxs("span", { children: [task.filesModified || 0, " \u6587\u4EF6"] })] })] }), (task.currentFilePath || hasLineStats || hasOperationStats || (task.retries || 0) > 0) && (_jsx("div", { className: "mt-2 pt-2 border-t border-white/5", children: _jsxs("div", { className: "flex flex-wrap items-center gap-1.5 text-[9px]", children: [task.currentFilePath && (_jsxs("span", { className: "inline-flex max-w-full items-center gap-1 rounded-md border border-cyan-400/30 bg-cyan-500/10 px-1.5 py-0.5 text-cyan-200", title: task.currentFilePath, children: [_jsx(FileCode, { className: "h-2.5 w-2.5 shrink-0" }), _jsxs("span", { className: "truncate max-w-[220px]", children: [task.status === 'running' ? '当前文件' : '最近文件', ": ", task.currentFilePath] })] })), hasLineStats && task.lineStats && (_jsxs(_Fragment, { children: [_jsxs("span", { className: "inline-flex items-center rounded-md border border-emerald-400/30 bg-emerald-500/10 px-1.5 py-0.5 text-emerald-200", children: ["+", task.lineStats.added] }), _jsxs("span", { className: "inline-flex items-center rounded-md border border-rose-400/30 bg-rose-500/10 px-1.5 py-0.5 text-rose-200", children: ["-", task.lineStats.deleted] }), _jsxs("span", { className: "inline-flex items-center rounded-md border border-amber-400/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-200", children: ["~", task.lineStats.modified] })] })), hasOperationStats && task.operationStats && (_jsxs("span", { className: "inline-flex items-center gap-1 rounded-md border border-slate-400/20 bg-white/5 px-1.5 py-0.5 text-slate-300", children: ["C:", task.operationStats.create, " M:", task.operationStats.modify, " D:", task.operationStats.delete] })), (task.retries || 0) > 0 && (_jsxs("span", { className: "inline-flex items-center gap-1 rounded-md border border-orange-400/30 bg-orange-500/10 px-1.5 py-0.5 text-orange-200", children: [_jsx(RotateCcw, { className: "h-2.5 w-2.5" }), "\u91CD\u8BD5 ", task.retries, " \u6B21"] })), task.activityUpdatedAt && (_jsxs("span", { className: "inline-flex items-center gap-1 rounded-md border border-[var(--soft-border)] bg-white/5 px-1.5 py-0.5 text-slate-300", children: [_jsx(Clock, { className: "h-2.5 w-2.5" }), formatTelemetryTime(task.activityUpdatedAt)] }))] }) })), task.budget && (_jsxs("div", { className: "mt-2 pt-2 border-t border-white/5", children: [_jsxs("div", { className: "flex items-center justify-between text-[10px]", children: [_jsxs("div", { className: "flex items-center gap-1.5 text-slate-400", children: [_jsx(Coins, { className: "w-3 h-3" }), _jsx("span", { children: "Budget" })] }), _jsxs("span", { className: cn(budgetPercent > 90 ? 'text-red-400' : budgetPercent > 70 ? 'text-yellow-400' : 'text-emerald-400'), children: [formatBytes(task.budget.used), " / ", formatBytes(task.budget.total)] })] }), _jsx("div", { className: "mt-1 h-1 rounded-full bg-slate-800 overflow-hidden", children: _jsx("div", { className: cn('h-full rounded-full transition-all', budgetPercent > 90 ? 'bg-red-500' : budgetPercent > 70 ? 'bg-yellow-500' : 'bg-emerald-500'), style: { width: `${Math.min(budgetPercent, 100)}%` } }) })] })), task.tags && task.tags.length > 0 && (_jsxs("div", { className: "mt-2 flex flex-wrap gap-1", children: [task.tags.slice(0, 3).map((tag, idx) => (_jsx("span", { className: "text-[9px] px-1.5 py-0.5 rounded bg-white/10 text-slate-400", children: tag }, idx))), task.tags.length > 3 && (_jsxs("span", { className: "text-[9px] px-1.5 py-0.5 rounded bg-white/10 text-slate-400", children: ["+", task.tags.length - 3] }))] })), traces.length > 0 && (_jsx("div", { className: "mt-2 pt-2 border-t border-white/5", children: _jsx(TaskTraceTimeline, { traces: traces, maxTraces: task.status === 'running' ? 5 : 1, expanded: task.status === 'running' }) })), task.status === 'failed' && failedTrace?.step_detail && (_jsx("div", { className: "text-red-400 text-sm mt-2", children: failedTrace.step_detail })), task.error && (_jsx("div", { className: "mt-2 p-2 rounded bg-red-500/10 border border-red-500/20", children: _jsx("p", { className: "text-[10px] text-red-400 line-clamp-2", children: task.error }) }))] }));
    };
    return (_jsxs("div", { className: "h-full flex flex-col", children: [_jsxs("div", { className: "h-auto border-b border-white/5", children: [_jsxs("div", { className: "h-12 flex items-center justify-between px-4", children: [_jsx("h2", { className: "text-sm font-medium text-slate-200", children: "\u4EFB\u52A1\u961F\u5217" }), _jsx(Button, { size: "sm", onClick: onExecute, "data-testid": "director-workspace-bulk-execute", className: cn(isExecuting
                                    ? 'bg-red-600 hover:bg-red-700'
                                    : 'bg-emerald-600 hover:bg-emerald-700', 'text-white'), children: isExecuting ? (_jsxs(_Fragment, { children: [_jsx(Pause, { className: "w-3.5 h-3.5 mr-1.5" }), " \u505C\u6B62\u6267\u884C"] })) : (_jsxs(_Fragment, { children: [_jsx(Zap, { className: "w-3.5 h-3.5 mr-1.5" }), " \u5168\u90E8\u6267\u884C"] })) })] }), _jsxs("div", { className: "px-4 pb-3 grid grid-cols-5 gap-2", children: [_jsx(StatCard, { icon: _jsx(Loader2, { className: "w-3.5 h-3.5 text-blue-400" }), label: "\u8FDB\u884C\u4E2D", value: runningCount, color: "blue" }), _jsx(StatCard, { icon: _jsx(Clock, { className: "w-3.5 h-3.5 text-slate-400" }), label: "\u672A\u9886\u53D6", value: pendingCount, color: "slate" }), _jsx(StatCard, { icon: _jsx(CheckCircle2, { className: "w-3.5 h-3.5 text-emerald-400" }), label: "\u5DF2\u5B8C\u6210", value: completedCount, color: "emerald" }), _jsx(StatCard, { icon: _jsx(AlertTriangle, { className: "w-3.5 h-3.5 text-red-400" }), label: "\u5931\u8D25", value: failedCount, color: "red" }), _jsx(StatCard, { icon: _jsx(Pause, { className: "w-3.5 h-3.5 text-yellow-400" }), label: "\u963B\u585E", value: blockedCount, color: "yellow" })] }), _jsxs("div", { className: "px-4 pb-3", children: [_jsxs("div", { className: "flex items-center justify-between text-[10px] text-slate-400 mb-1", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(BarChart3, { className: "w-3 h-3" }), "\u603B\u4F53\u8FDB\u5EA6 ", completedCount, "/", totalTasks] }), _jsxs("span", { className: "text-slate-300 font-medium", children: [progress, "%"] })] }), _jsx("div", { className: "h-2 rounded-full bg-slate-800 overflow-hidden", children: _jsx("div", { className: "soft-progress h-full rounded-full transition-all", style: { width: `${progress}%` } }) })] }), totalBudget > 0 && (_jsxs("div", { className: "px-4 pb-3", children: [_jsxs("div", { className: "flex items-center justify-between text-[10px] text-slate-400 mb-1", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(Coins, { className: "w-3 h-3" }), "\u9884\u7B97\u6D88\u8017"] }), _jsxs("span", { className: cn(budgetProgress > 90 ? 'text-red-400' : 'text-emerald-400', 'font-medium'), children: [formatBytes(usedBudget), " / ", formatBytes(totalBudget), " (", budgetProgress, "%)"] })] }), _jsx("div", { className: "h-1.5 rounded-full bg-slate-800 overflow-hidden", children: _jsx("div", { className: cn('h-full rounded-full transition-all', budgetProgress > 90 ? 'bg-red-500' : 'bg-emerald-500'), style: { width: `${Math.min(budgetProgress, 100)}%` } }) })] })), _jsx("div", { "data-testid": "director-task-detail", className: "soft-panel-subtle mx-4 mb-3 rounded-xl p-3", children: selectedTask ? (_jsxs("div", { children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-100", children: [getStatusIcon(selectedTask.status), _jsx("span", { className: "truncate", children: selectedTask.name })] }), _jsxs("div", { className: "mt-1 flex flex-wrap gap-1 text-[10px]", children: [_jsx("span", { className: cn('rounded border px-1.5 py-0.5', getStatusColor(selectedTask.status)), children: getStatusLabel(selectedTask.status) }), selectedTask.rawStatus ? (_jsxs("span", { className: "rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-slate-400", children: ["raw: ", selectedTask.rawStatus] })) : null, selectedTask.pmTaskId ? (_jsxs("span", { className: "rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-slate-400", children: ["PM: ", selectedTask.pmTaskId] })) : null, selectedTask.claimedBy || selectedTask.assignedWorker ? (_jsxs("span", { className: "rounded border border-[var(--soft-border)] bg-white/5 px-1.5 py-0.5 text-slate-300", children: ["owner: ", selectedTask.claimedBy || selectedTask.assignedWorker] })) : (_jsx("span", { className: "rounded border border-[var(--soft-border)] bg-white/5 px-1.5 py-0.5 text-slate-400", children: "\u672A\u9886\u53D6" }))] })] }), selectedTask.blueprintId || selectedTask.blueprintPath ? (_jsx("span", { className: "shrink-0 rounded border border-cyan-400/25 bg-cyan-500/10 px-2 py-1 text-[10px] text-cyan-200", children: selectedTask.blueprintId || 'blueprint' })) : null] }), (selectedTask.goal || selectedTask.description) && (_jsx("div", { className: "mt-3 text-xs leading-5 text-slate-300", children: selectedTask.goal || selectedTask.description })), _jsxs("div", { className: "mt-3 grid grid-cols-2 gap-3 text-[11px]", children: [_jsx(DetailBlock, { title: "\u6267\u884C\u6B65\u9AA4", children: renderCompactList(selectedTask.executionSteps, '无步骤字段') }), _jsx(DetailBlock, { title: "\u9A8C\u6536\u6807\u51C6", children: renderCompactList(selectedTask.acceptanceCriteria, '无验收字段') }), _jsx(DetailBlock, { title: "\u76EE\u6807\u6587\u4EF6", children: renderCompactList(selectedTask.targetFiles, '无目标文件') }), _jsx(DetailBlock, { title: "\u4F9D\u8D56/\u963B\u585E", children: renderCompactList([...(selectedTask.dependencies || []), ...(selectedTask.blockedBy || [])], '无依赖或阻塞') })] }), selectedTask.blueprintPath ? (_jsxs("div", { className: "mt-3 truncate rounded-md border border-cyan-400/20 bg-cyan-500/5 px-2 py-1 text-[10px] text-cyan-100", title: selectedTask.blueprintPath, children: ["\u84DD\u56FE\u8DEF\u5F84: ", selectedTask.blueprintPath] })) : null, selectedTask.error ? (_jsx("div", { className: "mt-3 rounded-md border border-red-500/25 bg-red-500/10 p-2 text-[11px] leading-5 text-red-200", children: selectedTask.error })) : null, _jsxs("div", { className: "mt-3 rounded-md border border-white/10 bg-white/[0.035] p-2", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-400", children: [_jsx("span", { children: "\u4EFB\u52A1\u7EA7\u5B9E\u65F6\u6587\u4EF6\u53D8\u66F4" }), _jsxs("span", { children: [selectedTask.taskScopedFileEvents?.length || 0, " events"] })] }), selectedTask.taskScopedFileEvents && selectedTask.taskScopedFileEvents.length > 0 ? (_jsx("div", { className: "space-y-1", children: selectedTask.taskScopedFileEvents.slice(-4).reverse().map((event) => (_jsxs("div", { className: "flex items-center justify-between gap-2 rounded border border-white/5 bg-slate-950/50 px-2 py-1 text-[10px]", children: [_jsx("span", { className: "truncate text-slate-300", children: event.filePath }), _jsx("span", { className: cn('shrink-0 rounded px-1.5 py-0.5', event.operation === 'create' ? 'bg-emerald-500/[0.15] text-emerald-200' :
                                                            event.operation === 'delete' ? 'bg-red-500/[0.15] text-red-200' :
                                                                'bg-blue-500/[0.15] text-blue-200'), children: event.operation })] }, event.id))) })) : (_jsx("div", { className: "text-[11px] text-slate-500", children: "\u8BE5\u4EFB\u52A1\u6682\u672A\u6536\u5230\u6587\u4EF6\u589E\u5220\u6539\u4E8B\u4EF6\u3002" }))] })] })) : (_jsxs("div", { className: "flex items-center gap-2 text-xs text-slate-400", children: [_jsx(Hash, { className: "h-3.5 w-3.5" }), "\u70B9\u51FB\u5DE6\u4FA7\u4EFB\u52A1\u5361\u67E5\u770B\u5B8C\u6574\u4EFB\u52A1\u5408\u540C\u3001\u9886\u53D6\u72B6\u6001\u3001\u9A8C\u6536\u6807\u51C6\u548C\u5B9E\u65F6\u6587\u4EF6\u53D8\u66F4\u3002"] })) }), _jsxs("div", { className: "px-4 pb-3", children: [_jsxs("div", { className: "flex items-center justify-between text-[10px] text-slate-400 mb-2", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(Layers, { className: "w-3 h-3" }), "Worker \u8FD0\u884C\u770B\u677F"] }), _jsxs("span", { children: ["\u603B\u8BA1 ", workerRows.length, " / \u7A7A\u95F2 ", workerIdleCount, " / \u6267\u884C\u4E2D ", workerBusyCount, workerFailedCount > 0 ? ` / 异常 ${workerFailedCount}` : ''] })] }), workerRows.length === 0 ? (_jsx("div", { className: "rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-[11px] text-slate-400", children: "\u6682\u65E0 worker \u5B9E\u65F6\u6570\u636E\uFF0C\u7B49\u5F85 Director \u63A8\u9001..." })) : (_jsx("div", { className: "grid grid-cols-1 gap-2", children: workerRows.map((worker) => (_jsxs("div", { className: cn('rounded-lg border px-3 py-2 text-[11px] transition-colors', getWorkerStatusColor(worker.status)), children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "font-medium truncate", children: worker.name }), _jsx("span", { className: "text-[10px]", children: getWorkerStatusLabel(worker.status) })] }), _jsx("div", { className: "mt-1 text-[10px] text-slate-300/90", children: worker.taskName
                                                ? `当前任务: ${worker.taskName}`
                                                : '当前任务: 空闲' }), _jsxs("div", { className: "mt-1 text-[10px] text-slate-400", children: ["\u5B8C\u6210 ", worker.tasksCompleted, " / \u5931\u8D25 ", worker.tasksFailed, worker.healthy === false ? ' / 健康检查失败' : ''] })] }, worker.id))) }))] })] }), _jsx("div", { className: "flex-1 overflow-auto p-4", children: tasks.length === 0 ? (_jsxs("div", { className: "h-full flex flex-col items-center justify-center text-slate-500", children: [_jsx(ListTodo, { className: "w-12 h-12 mb-4 text-indigo-500/30" }), _jsx("p", { children: "\u5F53\u524D\u6CA1\u6709\u53EF\u6267\u884C\u4EFB\u52A1" })] })) : (_jsxs("div", { children: [_jsx(TaskGroup, { status: "running", tasks: groupedTasks.running }), _jsx(TaskGroup, { status: "pending", tasks: groupedTasks.pending }), _jsx(TaskGroup, { status: "blocked", tasks: groupedTasks.blocked }), _jsx(TaskGroup, { status: "failed", tasks: groupedTasks.failed }), _jsx(TaskGroup, { status: "completed", tasks: groupedTasks.completed })] })) })] }));
}
function StatCard({ icon, label, value, color }) {
    const colorClasses = {
        blue: 'text-blue-400',
        slate: 'text-slate-400',
        emerald: 'text-emerald-400',
        red: 'text-red-400',
        yellow: 'text-amber-400',
    };
    return (_jsxs("div", { className: "soft-chip flex flex-col items-center p-2 rounded-lg", children: [icon, _jsx("span", { className: cn('text-lg font-bold mt-1', colorClasses[color]), children: value }), _jsx("span", { className: "text-[9px] text-slate-500", children: label })] }));
}
function DetailBlock({ title, children }) {
    return (_jsxs("div", { className: "min-w-0 rounded-md border border-white/10 bg-white/[0.025] p-2", children: [_jsx("div", { className: "mb-1 text-[10px] uppercase tracking-wider text-slate-500", children: title }), _jsx("div", { className: "min-w-0", children: children })] }));
}
function buildTaskSnapshotFileEditEvents(tasks) {
    const fallbackEvents = [];
    for (const task of tasks) {
        if ((task.taskScopedFileEvents?.length || 0) > 0) {
            continue;
        }
        const files = [
            task.currentFilePath,
            ...(task.targetFiles || []),
        ].filter((item, index, all) => {
            const value = String(item || "").trim();
            return Boolean(value) && all.indexOf(item) === index;
        });
        if (files.length === 0 || (task.filesModified || 0) <= 0) {
            continue;
        }
        const timestamp = task.completedAt || task.activityUpdatedAt || task.startedAt || task.createdAt || new Date(0).toISOString();
        const taskId = String(task.id || "");
        files.slice(0, 20).forEach((filePath, index) => {
            fallbackEvents.push({
                id: `task-snapshot-${taskId}-${index}-${filePath}`,
                filePath,
                operation: "modify",
                contentSize: 0,
                taskId,
                timestamp,
                addedLines: index === 0 ? task.lineStats?.added : undefined,
                deletedLines: index === 0 ? task.lineStats?.deleted : undefined,
                modifiedLines: index === 0 ? task.lineStats?.modified : undefined,
                sourceChannel: "task-runtime",
                eventKind: "task_snapshot_file_change",
                provenance: "task-runtime-snapshot",
            });
        });
    }
    return fallbackEvents;
}
function mergeCodePanelEvents(fileEditEvents, tasks) {
    const merged = [...fileEditEvents];
    const seen = new Set(merged.map((event) => `${event.taskId || ""}:${event.filePath}`));
    for (const event of buildTaskSnapshotFileEditEvents(tasks)) {
        const key = `${event.taskId || ""}:${event.filePath}`;
        if (!seen.has(key)) {
            seen.add(key);
            merged.push(event);
        }
    }
    return merged;
}
function DirectorCodePanel({ workspace, fileEditEvents, tasks }) {
    const [expandedEventId, setExpandedEventId] = useState(null);
    const [openFileStatus, setOpenFileStatus] = useState({ kind: 'idle', message: null });
    const getOperationIcon = (operation) => {
        switch (operation) {
            case 'create':
                return _jsx(FilePlus, { className: "w-3.5 h-3.5 text-emerald-400" });
            case 'delete':
                return _jsx(FileX, { className: "w-3.5 h-3.5 text-red-400" });
            case 'modify':
            default:
                return _jsx(FileEdit, { className: "w-3.5 h-3.5 text-blue-400" });
        }
    };
    const getOperationLabel = (operation) => {
        switch (operation) {
            case 'create':
                return '创建';
            case 'delete':
                return '删除';
            case 'modify':
                return '修改';
            default:
                return operation;
        }
    };
    const getOperationColor = (operation) => {
        switch (operation) {
            case 'create':
                return 'text-emerald-400';
            case 'delete':
                return 'text-red-400';
            case 'modify':
                return 'text-blue-400';
            default:
                return 'text-slate-400';
        }
    };
    const codePanelEvents = useMemo(() => mergeCodePanelEvents(fileEditEvents, tasks), [fileEditEvents, tasks]);
    const recentEvents = useMemo(() => [...codePanelEvents].sort(compareFileEditEventsForCodePanel).slice(0, 20), [codePanelEvents]);
    useEffect(() => {
        if (recentEvents.length === 0) {
            setExpandedEventId(null);
            return;
        }
        const defaultEvent = selectDefaultCodePanelEvent(recentEvents);
        setExpandedEventId((previous) => {
            const previousEvent = recentEvents.find((event) => event.id === previous);
            if (!previousEvent) {
                return defaultEvent?.id ?? null;
            }
            if (defaultEvent && !hasRenderablePatch(previousEvent) && hasRenderablePatch(defaultEvent)) {
                return defaultEvent.id;
            }
            return previous;
        });
    }, [recentEvents]);
    const defaultCodePanelEvent = useMemo(() => selectDefaultCodePanelEvent(recentEvents), [recentEvents]);
    const selectedOpenEvent = useMemo(() => recentEvents.find((event) => event.id === expandedEventId) ?? defaultCodePanelEvent, [defaultCodePanelEvent, expandedEventId, recentEvents]);
    const defaultCodePanelEventId = defaultCodePanelEvent?.id ?? null;
    const toggleExpand = (eventId) => {
        setExpandedEventId(prev => prev === eventId ? null : eventId);
    };
    const renderLineStats = (event) => {
        const stats = resolveEventLineStats(event);
        const hasStats = stats.added > 0 || stats.deleted > 0 || stats.modified > 0;
        return { stats, hasStats };
    };
    const handleOpenFile = useCallback(async () => {
        const target = resolveDirectorOpenTarget(workspace, selectedOpenEvent?.filePath);
        if (!target) {
            setOpenFileStatus({ kind: 'error', message: '没有可打开的工作区文件' });
            return;
        }
        setOpenFileStatus({ kind: 'loading', message: `正在打开 ${selectedOpenEvent?.filePath || target}` });
        try {
            const result = await openPath(target);
            if (!result.ok) {
                setOpenFileStatus({ kind: 'error', message: result.error || '打开文件失败' });
                return;
            }
            setOpenFileStatus({ kind: 'success', message: `已请求打开 ${selectedOpenEvent?.filePath || target}` });
        }
        catch (error) {
            setOpenFileStatus({
                kind: 'error',
                message: error instanceof Error ? error.message : '打开文件失败',
            });
        }
    }, [selectedOpenEvent, workspace]);
    return (_jsxs("div", { "data-testid": "director-code-panel", className: "h-full flex flex-col", children: [_jsxs("div", { className: "h-12 flex items-center justify-between px-4 border-b border-[var(--soft-border)]", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("h2", { className: "text-sm font-medium text-slate-200", children: "\u5B9E\u65F6\u4EE3\u7801\u53D8\u66F4" }), codePanelEvents.length > 0 && (_jsxs("span", { className: "soft-chip text-[10px] px-2 py-0.5 rounded-full text-slate-300", children: [codePanelEvents.length, " \u4E2A\u6587\u4EF6"] }))] }), _jsx("div", { className: "flex items-center gap-2", children: _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void handleOpenFile(); }, disabled: !selectedOpenEvent || openFileStatus.kind === 'loading', "data-testid": "director-code-open-file", title: selectedOpenEvent?.filePath ? `打开 ${selectedOpenEvent.filePath}` : '没有可打开的文件', className: "text-slate-400", children: [_jsx(FileCode, { className: "w-4 h-4 mr-1.5" }), openFileStatus.kind === 'loading' ? '打开中' : '打开文件'] }) })] }), openFileStatus.message ? (_jsx("div", { className: cn('border-b px-4 py-1.5 text-[11px]', openFileStatus.kind === 'error'
                    ? 'border-amber-500/20 bg-amber-500/10 text-amber-100'
                    : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-100'), "data-testid": "director-code-open-file-evidence", children: openFileStatus.message })) : null, _jsxs("div", { className: "flex-1 overflow-hidden flex", children: [_jsx("div", { className: "flex-1 overflow-auto p-4", children: recentEvents.length === 0 ? (_jsxs("div", { "data-testid": "director-code-empty", className: "h-full flex flex-col items-center justify-center text-slate-500", children: [_jsx(FileCode, { className: "w-12 h-12 mb-4 text-indigo-500/30" }), _jsx("p", { children: "\u7B49\u5F85\u4EE3\u7801\u53D8\u66F4..." }), _jsx("p", { className: "text-xs mt-2 opacity-70", children: "Director \u6267\u884C\u65F6\u5C06\u5B9E\u65F6\u663E\u793A\u6587\u4EF6\u4FEE\u6539" })] })) : (_jsx("div", { "data-testid": "director-code-event-list", className: "space-y-2", children: recentEvents.map((event) => {
                                const hasPatch = hasRenderablePatch(event);
                                const { stats, hasStats } = renderLineStats(event);
                                const sourceLabel = event.provenance || event.sourceChannel || event.eventKind || 'runtime';
                                const noContentChange = !hasPatch && event.patchUnavailableReason === 'no_content_change';
                                const emptyFile = !hasPatch && event.patchUnavailableReason === 'empty_file';
                                const fallbackBadge = noContentChange ? '无变化' : '统计';
                                const fallbackAction = noContentChange || emptyFile ? '查看状态' : '展开统计';
                                const fallbackTitle = noContentChange
                                    ? '文件内容未变化，未生成 diff patch。'
                                    : emptyFile
                                        ? '空文件写入未生成 diff patch。'
                                        : '未收到 diff patch，已显示文件变更统计。';
                                return (_jsxs("div", { children: [_jsx("div", { "data-testid": "director-code-event-row", "data-file-path": event.filePath, "data-event-id": event.id, className: cn('p-3 rounded-xl border transition-all cursor-pointer', hasPatch && event.id === defaultCodePanelEventId
                                                ? 'soft-raised border-[var(--soft-border)]'
                                                : 'bg-white/5 border-white/5 hover:border-white/10', expandedEventId === event.id && 'ring-1 ring-white/10'), onClick: () => toggleExpand(event.id), children: _jsxs("div", { className: "flex items-start gap-3", children: [_jsx("div", { className: "mt-0.5", children: getOperationIcon(event.operation) }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("span", { className: "text-xs font-mono text-slate-300 truncate flex-1", title: event.filePath, children: event.filePath }), _jsx("span", { className: cn('text-[10px] px-1.5 py-0.5 rounded bg-white/10', getOperationColor(event.operation)), children: getOperationLabel(event.operation) }), _jsx("span", { className: cn('text-[10px] px-1.5 py-0.5 rounded', hasPatch
                                                                            ? 'bg-cyan-500/20 text-cyan-400'
                                                                            : noContentChange || emptyFile
                                                                                ? 'bg-slate-500/[0.15] text-slate-300'
                                                                                : 'bg-amber-500/[0.15] text-amber-300'), children: hasPatch ? 'Diff' : fallbackBadge })] }), _jsxs("div", { className: "mt-1 flex items-center gap-3 text-[10px] text-slate-500", children: [_jsxs("span", { children: [event.contentSize, " bytes"] }), event.taskId && _jsxs("span", { className: "text-slate-600", children: ["\u4EFB\u52A1: ", String(event.taskId).slice(0, 8)] }), hasStats && (_jsxs("span", { className: "flex items-center gap-1.5 font-mono", children: [stats.added > 0 && _jsxs("span", { className: "text-emerald-400", children: ["+", stats.added] }), stats.deleted > 0 && _jsxs("span", { className: "text-red-400", children: ["-", stats.deleted] }), stats.modified > 0 && _jsxs("span", { className: "text-blue-400", children: ["~", stats.modified] })] })), _jsx("span", { className: "text-slate-600", children: new Date(event.timestamp).toLocaleTimeString() }), _jsx("span", { className: hasPatch ? 'text-cyan-400' : 'text-amber-300', children: expandedEventId === event.id ? '▼ 收起' : hasPatch ? '▶ 展开 Diff' : `▶ ${fallbackAction}` })] })] })] }) }), expandedEventId === event.id && (_jsx("div", { className: "mt-2", children: hasPatch ? (_jsx(RealTimeFileDiff, { filePath: event.filePath, operation: event.operation, patch: event.patch, compact: true })) : (_jsxs("div", { className: cn('rounded-lg border p-3 text-xs', noContentChange || emptyFile
                                                    ? 'border-slate-500/20 bg-slate-500/5 text-slate-200'
                                                    : 'border-amber-500/20 bg-amber-500/5 text-amber-100'), "data-testid": "director-file-edit-summary", children: [_jsx("div", { className: "font-medium", children: fallbackTitle }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-2 font-mono", children: [_jsxs("span", { className: "rounded bg-white/5 px-2 py-1 text-emerald-300", children: ["+", stats.added] }), _jsxs("span", { className: "rounded bg-white/5 px-2 py-1 text-red-300", children: ["-", stats.deleted] }), _jsxs("span", { className: "rounded bg-white/5 px-2 py-1 text-blue-300", children: ["~", stats.modified] }), _jsxs("span", { className: "rounded bg-white/5 px-2 py-1 text-slate-300", children: [event.contentSize, " bytes"] })] }), _jsxs("div", { className: cn('mt-2 text-[11px]', noContentChange || emptyFile ? 'text-slate-400' : 'text-amber-200/70'), children: ["\u6765\u6E90: ", sourceLabel, event.patchUnavailableReason ? ` · 原因: ${event.patchUnavailableReason}` : ''] })] })) }))] }, event.id));
                            }) })) }), _jsxs("div", { className: "w-48 border-l border-[var(--soft-border)] p-4 bg-[var(--soft-surface-muted)]", children: [_jsx("h3", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-3", children: "\u53D8\u66F4\u7EDF\u8BA1" }), _jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "flex items-center justify-between p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20", children: [_jsxs("span", { className: "text-xs text-emerald-400 flex items-center gap-1.5", children: [_jsx(FilePlus, { className: "w-3 h-3" }), "\u521B\u5EFA"] }), _jsx("span", { className: "text-xs font-mono text-emerald-300", children: fileEditEvents.filter(e => e.operation === 'create').length })] }), _jsxs("div", { className: "flex items-center justify-between p-2 rounded-lg bg-blue-500/10 border border-blue-500/20", children: [_jsxs("span", { className: "text-xs text-blue-400 flex items-center gap-1.5", children: [_jsx(FileEdit, { className: "w-3 h-3" }), "\u4FEE\u6539"] }), _jsx("span", { className: "text-xs font-mono text-blue-300", children: fileEditEvents.filter(e => e.operation === 'modify').length })] }), _jsxs("div", { className: "flex items-center justify-between p-2 rounded-lg bg-red-500/10 border border-red-500/20", children: [_jsxs("span", { className: "text-xs text-red-400 flex items-center gap-1.5", children: [_jsx(FileX, { className: "w-3 h-3" }), "\u5220\u9664"] }), _jsx("span", { className: "text-xs font-mono text-red-300", children: fileEditEvents.filter(e => e.operation === 'delete').length })] })] }), _jsxs("div", { className: "mt-6 pt-4 border-t border-white/5", children: [_jsx("h3", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2", children: "\u5DE5\u4F5C\u533A" }), _jsx("p", { className: "text-xs text-slate-400 truncate", title: workspace, children: workspace })] })] })] })] }));
}
// Terminal Panel
function DirectorTerminalPanel({ output, onClear }) {
    return (_jsxs("div", { "data-testid": "director-terminal-panel", className: "h-full flex flex-col", children: [_jsxs("div", { className: "h-12 flex items-center justify-between px-4 border-b border-[var(--soft-border)]", children: [_jsx("h2", { className: "text-sm font-medium text-slate-200", children: "\u6267\u884C\u7EC8\u7AEF" }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: onClear, disabled: !output, "data-testid": "director-terminal-clear", className: "text-slate-400", children: [_jsx(RotateCcw, { className: "w-4 h-4 mr-1.5" }), "\u6E05\u7A7A"] })] }), _jsx("div", { className: "flex-1 p-4", children: _jsx("div", { className: "h-full rounded-xl soft-inset p-4 font-mono text-xs overflow-auto", children: output ? (_jsx("pre", { "data-testid": "director-terminal-output", className: "text-slate-300 whitespace-pre-wrap", children: output })) : (_jsx("div", { "data-testid": "director-terminal-empty", className: "text-slate-600", children: "\u7B49\u5F85\u6267\u884C..." })) }) })] }));
}
// Debug Panel
function DirectorDebugPanel({ tasks, cancellingTaskId, onInspectTask, onCancelTask, }) {
    return (_jsxs("div", { "data-testid": "director-debug-panel", className: "h-full flex flex-col", children: [_jsx("div", { className: "h-12 flex items-center px-4 border-b border-[var(--soft-border)]", children: _jsx("h2", { className: "text-sm font-medium text-slate-200", children: "\u8C03\u8BD5\u4E2D\u5FC3" }) }), _jsx("div", { className: "flex-1 overflow-auto p-4", children: tasks.length === 0 ? (_jsxs("div", { "data-testid": "director-debug-empty", className: "h-full flex flex-col items-center justify-center text-slate-500", children: [_jsx(CheckCircle2, { className: "w-12 h-12 mb-4 text-blue-500/30" }), _jsx("p", { children: "\u6CA1\u6709\u9700\u8981\u8C03\u8BD5\u7684\u95EE\u9898" })] })) : (_jsx("div", { className: "space-y-2", children: tasks.map((task) => (_jsxs("div", { "data-testid": "director-debug-task", className: "soft-panel-subtle p-4 rounded-xl", children: [_jsxs("div", { className: "flex items-center gap-2 mb-2", children: [_jsx(Bug, { className: "w-4 h-4 text-red-400" }), _jsx("span", { className: "text-sm text-slate-200 font-medium", children: task.name })] }), task.error && (_jsx("pre", { className: "text-xs text-red-400 font-mono bg-red-950/30 p-2 rounded", children: task.error })), _jsxs("div", { className: "mt-3 flex gap-2", children: [_jsx(Button, { size: "sm", variant: "outline", onClick: () => onInspectTask(task.id), "data-testid": `director-debug-inspect-${task.id}`, className: "border-red-500/30 text-red-400", children: "\u5B9A\u4F4D" }), _jsx(Button, { size: "sm", variant: "ghost", onClick: () => onCancelTask(task.id), disabled: cancellingTaskId === task.id, "data-testid": `director-debug-cancel-${task.id}`, className: "text-slate-400", children: cancellingTaskId === task.id ? '取消中' : '取消' })] })] }, task.id))) })) })] }));
}
