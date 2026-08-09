import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/**
 * DirectorTaskPanel - task board and drill-down details for Director execution.
 */
import { useMemo, useState } from 'react';
import { AlertTriangle, BarChart3, Brain, CheckCircle2, Clock, Code2, FileCode, FilePlus, Filter, GitBranch, Layers, ListChecks, ListTodo, Loader2, Pause, RotateCcw, ShieldCheck, Target, User, XCircle, Zap, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { TaskTraceTimeline } from '../common/TaskTraceTimeline';
const FILTERS = [
    { id: 'all', label: '全部' },
    { id: 'unclaimed', label: '未领取' },
    { id: 'claimed', label: '已领取/运行中' },
    { id: 'attention', label: '阻塞/报错' },
    { id: 'completed', label: '完成' },
];
export function buildTaskBoardGroups(tasks, filter = 'all') {
    const groups = [
        {
            id: 'unclaimed',
            label: '未领取',
            description: '等待 Director 或 worker 领取',
            tasks: tasks.filter((task) => task.status === 'pending'),
        },
        {
            id: 'claimed',
            label: '已领取 / 运行中',
            description: '已分配 worker 或正在执行',
            tasks: tasks.filter((task) => task.status === 'running'),
        },
        {
            id: 'attention',
            label: '阻塞 / 报错',
            description: '需要排障、重试或回流 PM',
            tasks: tasks.filter((task) => task.status === 'blocked' || task.status === 'failed'),
        },
        {
            id: 'completed',
            label: '完成',
            description: '已完成并可进入 QA 观察',
            tasks: tasks.filter((task) => task.status === 'completed'),
        },
    ];
    if (filter === 'all') {
        return groups;
    }
    return groups.filter((group) => group.id === filter);
}
function StatCard({ icon, label, value, color }) {
    const colorClasses = {
        blue: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
        slate: 'text-slate-400 bg-slate-500/10 border-slate-500/20',
        emerald: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
        red: 'text-red-400 bg-red-500/10 border-red-500/20',
        yellow: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
    };
    return (_jsxs("div", { className: cn('flex min-h-16 flex-col items-center justify-center rounded-lg border p-2', colorClasses[color]), children: [icon, _jsx("span", { className: "mt-1 text-lg font-bold", children: value }), _jsx("span", { className: "text-[9px] opacity-70", children: label })] }));
}
function getStatusIcon(status) {
    switch (status) {
        case 'completed': return _jsx(CheckCircle2, { className: "h-4 w-4 text-emerald-400" });
        case 'running': return _jsx(Loader2, { className: "h-4 w-4 animate-spin text-blue-400" });
        case 'failed': return _jsx(AlertTriangle, { className: "h-4 w-4 text-red-400" });
        case 'blocked': return _jsx(Pause, { className: "h-4 w-4 text-yellow-400" });
        default: return _jsx(Clock, { className: "h-4 w-4 text-slate-500" });
    }
}
function getStatusLabel(status) {
    switch (status) {
        case 'running': return '运行中';
        case 'pending': return '未领取';
        case 'completed': return '完成';
        case 'failed': return '报错';
        case 'blocked': return '阻塞';
    }
}
function getStatusColor(status) {
    switch (status) {
        case 'running': return 'text-blue-300 border-blue-500/25 bg-blue-500/10';
        case 'pending': return 'text-slate-300 border-slate-500/25 bg-white/5';
        case 'completed': return 'text-emerald-300 border-emerald-500/25 bg-emerald-500/10';
        case 'failed': return 'text-red-300 border-red-500/25 bg-red-500/10';
        case 'blocked': return 'text-yellow-300 border-yellow-500/25 bg-yellow-500/10';
    }
}
function getTypeIcon(type) {
    switch (type) {
        case 'test': return _jsx(ShieldCheck, { className: "h-3.5 w-3.5 text-purple-400" });
        case 'debug': return _jsx(AlertTriangle, { className: "h-3.5 w-3.5 text-red-400" });
        case 'review': return _jsx(ListChecks, { className: "h-3.5 w-3.5 text-amber-400" });
        default: return _jsx(Code2, { className: "h-3.5 w-3.5 text-blue-400" });
    }
}
function formatDuration(ms) {
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
}
function formatListValue(items) {
    return Array.isArray(items) ? items.filter((item) => String(item || '').trim().length > 0) : [];
}
function readEventText(event, keys) {
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
function readStatNumber(stats, keys) {
    if (!stats) {
        return null;
    }
    for (const key of keys) {
        const value = stats[key];
        if (typeof value === 'number' && Number.isFinite(value)) {
            return value;
        }
        if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
            return Number(value);
        }
    }
    return null;
}
function readWorkerDetailText(worker, keys) {
    if (!worker) {
        return '';
    }
    for (const key of keys) {
        const value = worker[key];
        if (typeof value === 'string' && value.trim()) {
            return value.trim();
        }
        if (typeof value === 'number' && Number.isFinite(value)) {
            return String(value);
        }
        if (typeof value === 'boolean') {
            return value ? 'true' : 'false';
        }
    }
    return '';
}
function formatEventType(value) {
    return value.replace(/_/g, ' ') || 'event';
}
function formatEventTimestamp(value) {
    if (!value) {
        return '';
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
}
function evidenceEndpoint(endpoint, workspace = '') {
    const value = String(workspace || '').trim();
    if (!value) {
        return endpoint;
    }
    const separator = endpoint.includes('?') ? '&' : '?';
    return `${endpoint}${separator}workspace=${encodeURIComponent(value)}`;
}
function EndpointBadge({ endpoint, method, testId, }) {
    return (_jsx("span", { className: "shrink-0 rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] font-medium text-slate-500", title: endpoint, "data-endpoint": endpoint, "data-testid": testId, children: method ? `${method} API` : 'API' }));
}
function directorTaskEndpoint(taskId, suffix = '', workspace = '', query = '') {
    return evidenceEndpoint(`/v2/director/tasks/${encodeURIComponent(taskId)}${suffix}${query}`, workspace);
}
function DetailSection({ icon, title, items, emptyText }) {
    const rows = formatListValue(items);
    return (_jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.03] p-3", "data-testid": `director-task-detail-${title}`, children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-medium text-slate-200", children: [icon, _jsx("span", { children: title })] }), rows.length === 0 ? (_jsx("p", { className: "text-[11px] text-slate-500", children: emptyText })) : (_jsx("ul", { className: "space-y-1.5", children: rows.map((item, index) => (_jsxs("li", { className: "flex gap-2 text-[11px] leading-5 text-slate-300", children: [_jsx("span", { className: "mt-2 h-1 w-1 shrink-0 rounded-full bg-indigo-400" }), _jsx("span", { className: "break-words", children: item })] }, `${title}-${index}`))) }))] }));
}
export function DirectorTaskPanel({ tasks, workers, taskMap, selectedTaskId, onTaskSelect, onExecute, onTaskCancel, isExecuting, isTaskCancelling = false, taskCancelMessage, taskCancelError, taskTraceMap, workerFallbackError, workerBackendDetail, onWorkerSelect, onTaskCreate, isTaskCreating = false, taskCreateMessage, taskCreateError, taskBackendDetail, taskLLMEvents, executionDisabledReason, workspace = '', }) {
    const [activeFilter, setActiveFilter] = useState('all');
    const [createSubject, setCreateSubject] = useState('');
    const [createDescription, setCreateDescription] = useState('');
    const [createPriority, setCreatePriority] = useState('MEDIUM');
    const [createTimeout, setCreateTimeout] = useState(300);
    const groups = useMemo(() => buildTaskBoardGroups(tasks, activeFilter), [tasks, activeFilter]);
    const selectedTask = selectedTaskId ? taskMap.get(selectedTaskId) || null : null;
    const selectedExecuteLabel = selectedTask?.status === 'failed' || selectedTask?.status === 'blocked'
        ? '重试选中任务'
        : selectedTask?.status === 'running' || isExecuting
            ? '停止 Director'
            : '执行选中任务';
    const runningCount = tasks.filter((task) => task.status === 'running').length;
    const pendingCount = tasks.filter((task) => task.status === 'pending').length;
    const completedCount = tasks.filter((task) => task.status === 'completed').length;
    const blockedCount = tasks.filter((task) => task.status === 'blocked').length;
    const failedCount = tasks.filter((task) => task.status === 'failed').length;
    const progress = tasks.length > 0 ? Math.round((completedCount / tasks.length) * 100) : 0;
    const workerRows = useMemo(() => workers
        .filter((worker) => worker && typeof worker === 'object')
        .map((worker) => {
        const taskId = String(worker.currentTaskId || '').trim();
        return {
            id: worker.id,
            name: worker.name || worker.id,
            status: worker.status,
            taskId,
            taskName: taskId ? taskMap.get(taskId)?.name || taskId : '',
            healthy: worker.healthy,
            tasksCompleted: worker.tasksCompleted || 0,
            tasksFailed: worker.tasksFailed || 0,
        };
    }), [workers, taskMap]);
    const selectedWorker = selectedTask
        ? workerRows.find((worker) => worker.taskId === selectedTask.id || worker.id === selectedTask.assignedWorker)
        : null;
    const hasSelectedBackendDetail = Boolean(selectedTask && taskBackendDetail?.taskId === selectedTask.id);
    const backendTask = hasSelectedBackendDetail ? taskBackendDetail?.data ?? null : null;
    const idleWorkerCount = workerRows.filter((worker) => worker.status === 'idle').length;
    const busyWorkerCount = workerRows.filter((worker) => worker.status === 'busy' || worker.status === 'running').length;
    const failedWorkerCount = workerRows.filter((worker) => worker.status === 'failed' || worker.healthy === false).length;
    const workerDetail = workerBackendDetail?.data ?? null;
    const workerDetailTask = readWorkerDetailText(workerDetail, ['currentTaskId', 'current_task_id', 'task_id', 'current_task']);
    const workerDetailCompleted = readWorkerDetailText(workerDetail, ['tasksCompleted', 'tasks_completed', 'completed_tasks']) || '0';
    const workerDetailFailed = readWorkerDetailText(workerDetail, ['tasksFailed', 'tasks_failed', 'failed_tasks']) || '0';
    const hasSelectedTaskLLMEvents = Boolean(selectedTask && taskLLMEvents?.taskId === selectedTask.id);
    const llmEventRows = hasSelectedTaskLLMEvents ? taskLLMEvents?.events ?? [] : [];
    const llmStats = hasSelectedTaskLLMEvents ? taskLLMEvents?.stats ?? null : null;
    const llmStatsTotal = readStatNumber(llmStats, ['total', 'count']) ?? llmEventRows.length;
    const llmStatsErrors = readStatNumber(llmStats, ['call_error', 'llm_error', 'errors']) ?? 0;
    const llmStatsRetries = readStatNumber(llmStats, ['call_retry', 'llm_retry', 'retries']) ?? 0;
    const canCancelSelectedTask = Boolean(selectedTask && selectedTask.status !== 'completed');
    const normalizedCreateSubject = createSubject.trim();
    const normalizedCreateDescription = createDescription.trim() || normalizedCreateSubject;
    const canCreateTask = Boolean(onTaskCreate && normalizedCreateSubject && !isTaskCreating);
    const executionBlocked = Boolean(executionDisabledReason);
    const taskCreateEndpoint = evidenceEndpoint('/v2/director/tasks', workspace);
    const selectedTaskCancelEndpoint = selectedTask
        ? directorTaskEndpoint(selectedTask.id, '/cancel', workspace)
        : evidenceEndpoint('/v2/director/tasks/{task_id}/cancel', workspace);
    const selectedTaskDetailEndpoint = selectedTask
        ? directorTaskEndpoint(selectedTask.id, '', workspace)
        : evidenceEndpoint('/v2/director/tasks/{task_id}', workspace);
    const selectedTaskLLMEndpoint = selectedTask
        ? directorTaskEndpoint(selectedTask.id, '/llm-events', workspace, '?limit=25')
        : evidenceEndpoint('/v2/director/tasks/{task_id}/llm-events?limit=25', workspace);
    const submitCreateTask = () => {
        if (!canCreateTask) {
            return;
        }
        onTaskCreate?.({
            subject: normalizedCreateSubject,
            description: normalizedCreateDescription,
            priority: createPriority,
            timeoutSeconds: Math.max(30, Math.round(Number(createTimeout) || 300)),
        });
    };
    const TaskCard = ({ task }) => {
        const isSelected = selectedTaskId === task.id;
        const traces = taskTraceMap?.get(task.id) || [];
        const currentFile = task.currentFilePath || task.currentFile;
        const hasFileActivity = Boolean(currentFile
            || task.activityUpdatedAt
            || task.lineStats?.added
            || task.lineStats?.deleted
            || task.lineStats?.modified
            || task.operationStats?.create
            || task.operationStats?.modify
            || task.operationStats?.delete);
        return (_jsxs("button", { type: "button", "data-testid": "director-task-item", onClick: () => onTaskSelect(task.id), className: cn('w-full rounded-lg border p-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400/60', isSelected ? 'border-indigo-400/50 bg-indigo-500/[0.12]' : 'border-white/10 bg-white/[0.04] hover:border-white/20 hover:bg-white/[0.07]'), children: [_jsxs("div", { className: "flex items-start gap-3", children: [_jsx("div", { className: "mt-0.5", children: getStatusIcon(task.status) }), _jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx("span", { className: "truncate text-sm font-medium text-slate-100", children: task.name }), _jsx("span", { className: cn('rounded-md border px-1.5 py-0.5 text-[10px]', getStatusColor(task.status)), children: getStatusLabel(task.status) }), task.assignedWorker && (_jsx("span", { className: "rounded-md border border-cyan-400/20 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-200", children: task.assignedWorker }))] }), _jsx("p", { className: "mt-1 line-clamp-2 text-[11px] leading-5 text-slate-400", children: task.description || task.goal || '暂无任务描述' })] })] }), _jsxs("div", { className: "mt-3 grid grid-cols-3 gap-2 text-[10px] text-slate-400", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [getTypeIcon(task.type), task.type] }), _jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(Clock, { className: "h-3 w-3" }), formatDuration(task.actualTime)] }), _jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(FileCode, { className: "h-3 w-3" }), task.filesModified || 0, " \u6587\u4EF6"] })] }), task.status === 'running' && (_jsxs("div", { className: "mt-3", children: [_jsxs("div", { className: "mb-1 flex justify-between text-[10px] text-slate-400", children: [_jsx("span", { children: task.currentPhase || '执行中' }), _jsxs("span", { children: [task.progress || 0, "%"] })] }), _jsx("div", { className: "h-1.5 overflow-hidden rounded-full bg-slate-800", children: _jsx("div", { className: "h-full rounded-full bg-indigo-500 transition-all", style: { width: `${task.progress || 0}%` } }) })] })), hasFileActivity && (_jsxs("div", { className: "mt-3 rounded-lg border border-cyan-400/[0.15] bg-cyan-500/5 p-2 text-[10px] text-cyan-100", children: [currentFile && _jsxs("div", { className: "truncate", title: currentFile, children: ["\u6587\u4EF6: ", currentFile] }), task.lineStats && (_jsxs("div", { className: "mt-1 flex gap-2 font-mono", children: [_jsxs("span", { className: "text-emerald-300", children: ["+", task.lineStats.added] }), _jsxs("span", { className: "text-red-300", children: ["-", task.lineStats.deleted] }), _jsxs("span", { className: "text-amber-300", children: ["~", task.lineStats.modified] })] }))] })), traces.length > 0 && (_jsx("div", { className: "mt-3 border-t border-white/5 pt-2", children: _jsx(TaskTraceTimeline, { traces: traces, maxTraces: task.status === 'running' ? 4 : 1, expanded: task.status === 'running' }) })), task.error && (_jsx("div", { className: "mt-3 rounded-md border border-red-500/20 bg-red-500/10 p-2 text-[10px] text-red-300", children: task.error }))] }));
    };
    return (_jsxs("div", { className: "flex h-full flex-col", children: [_jsxs("div", { className: "border-b border-white/5", children: [_jsxs("div", { className: "flex h-12 items-center justify-between px-4", children: [_jsxs("div", { children: [_jsx("h2", { className: "text-sm font-medium text-slate-200", children: "\u4EFB\u52A1\u961F\u5217" }), _jsx("p", { className: "text-[10px] text-slate-500", children: "\u6309\u9886\u53D6\u72B6\u6001\u5206\u533A\uFF0C\u70B9\u51FB\u4EFB\u52A1\u67E5\u770B\u5B8C\u6574\u6267\u884C\u5408\u540C" })] }), _jsx(Button, { size: "sm", onClick: onExecute, "data-testid": "director-workspace-bulk-execute", disabled: executionBlocked, title: executionDisabledReason || undefined, className: cn(isExecuting ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700', 'text-white'), children: isExecuting ? _jsxs(_Fragment, { children: [_jsx(Pause, { className: "mr-1.5 h-3.5 w-3.5" }), "\u505C\u6B62\u6267\u884C"] }) : _jsxs(_Fragment, { children: [_jsx(Zap, { className: "mr-1.5 h-3.5 w-3.5" }), "\u5168\u90E8\u6267\u884C"] }) })] }), executionBlocked ? (_jsxs("div", { className: "mx-4 mb-3 flex items-center gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-100", "data-testid": "director-execution-guard", children: [_jsx(AlertTriangle, { className: "h-3.5 w-3.5 shrink-0 text-amber-300" }), _jsx("span", { children: executionDisabledReason })] })) : null, _jsxs("div", { className: "grid grid-cols-5 gap-2 px-4 pb-3", children: [_jsx(StatCard, { icon: _jsx(Loader2, { className: "h-3.5 w-3.5 text-blue-400" }), label: "\u8FD0\u884C", value: runningCount, color: "blue" }), _jsx(StatCard, { icon: _jsx(Clock, { className: "h-3.5 w-3.5 text-slate-400" }), label: "\u672A\u9886\u53D6", value: pendingCount, color: "slate" }), _jsx(StatCard, { icon: _jsx(Pause, { className: "h-3.5 w-3.5 text-yellow-400" }), label: "\u963B\u585E", value: blockedCount, color: "yellow" }), _jsx(StatCard, { icon: _jsx(AlertTriangle, { className: "h-3.5 w-3.5 text-red-400" }), label: "\u62A5\u9519", value: failedCount, color: "red" }), _jsx(StatCard, { icon: _jsx(CheckCircle2, { className: "h-3.5 w-3.5 text-emerald-400" }), label: "\u5B8C\u6210", value: completedCount, color: "emerald" })] }), _jsxs("div", { className: "px-4 pb-3", children: [_jsxs("div", { className: "mb-1 flex items-center justify-between text-[10px] text-slate-400", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(BarChart3, { className: "h-3 w-3" }), "\u603B\u4F53\u8FDB\u5EA6 ", completedCount, "/", tasks.length] }), _jsxs("span", { className: "font-medium text-indigo-300", children: [progress, "%"] })] }), _jsx("div", { className: "h-1.5 overflow-hidden rounded-full bg-slate-800", children: _jsx("div", { className: "h-full rounded-full bg-indigo-500 transition-all", style: { width: `${progress}%` } }) })] }), _jsxs("div", { className: "mx-4 mb-3 rounded-lg border border-indigo-400/20 bg-indigo-500/[0.06] p-2", "data-testid": "director-task-create-panel", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-2", children: [_jsxs("span", { className: "flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-indigo-100", children: [_jsx(FilePlus, { className: "h-3.5 w-3.5 text-indigo-300" }), "\u521B\u5EFA Director \u4EFB\u52A1"] }), _jsx(EndpointBadge, { endpoint: taskCreateEndpoint, method: "POST", testId: "director-task-create-endpoint" })] }), _jsxs("div", { className: "grid gap-2", children: [_jsx("input", { "aria-label": "Director task subject", "data-testid": "director-task-create-subject", value: createSubject, onChange: (event) => setCreateSubject(event.target.value), placeholder: "\u4EFB\u52A1\u6807\u9898", className: "h-8 min-w-0 rounded-md border border-white/10 bg-slate-950/65 px-2 text-xs text-slate-100 outline-none transition-colors placeholder:text-slate-500 focus:border-indigo-300/60" }), _jsx("textarea", { "aria-label": "Director task description", "data-testid": "director-task-create-description", value: createDescription, onChange: (event) => setCreateDescription(event.target.value), placeholder: "\u6267\u884C\u8BF4\u660E", rows: 2, className: "min-h-14 min-w-0 resize-none rounded-md border border-white/10 bg-slate-950/65 px-2 py-1.5 text-xs text-slate-100 outline-none transition-colors placeholder:text-slate-500 focus:border-indigo-300/60" }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsxs("select", { "aria-label": "Director task priority", "data-testid": "director-task-create-priority", value: createPriority, onChange: (event) => setCreatePriority(event.target.value), className: "h-8 rounded-md border border-white/10 bg-slate-950/65 px-2 text-xs text-slate-100 outline-none transition-colors focus:border-indigo-300/60", children: [_jsx("option", { value: "CRITICAL", children: "CRITICAL" }), _jsx("option", { value: "HIGH", children: "HIGH" }), _jsx("option", { value: "MEDIUM", children: "MEDIUM" }), _jsx("option", { value: "LOW", children: "LOW" })] }), _jsx("input", { "aria-label": "Director task timeout seconds", "data-testid": "director-task-create-timeout", type: "number", min: 30, step: 30, value: createTimeout, onChange: (event) => setCreateTimeout(Number(event.target.value)), className: "h-8 w-24 rounded-md border border-white/10 bg-slate-950/65 px-2 text-xs text-slate-100 outline-none transition-colors focus:border-indigo-300/60" }), _jsxs(Button, { type: "button", size: "sm", onClick: submitCreateTask, disabled: !canCreateTask, "data-testid": "director-task-create-submit", className: "h-8 bg-indigo-600 px-2 text-xs text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50", children: [isTaskCreating ? (_jsx(Loader2, { className: "mr-1.5 h-3.5 w-3.5 animate-spin" })) : (_jsx(FilePlus, { className: "mr-1.5 h-3.5 w-3.5" })), "\u521B\u5EFA"] })] })] }), (taskCreateMessage || taskCreateError || isTaskCreating) ? (_jsxs("div", { className: cn('mt-2 rounded-md border px-2 py-1.5 text-[11px]', taskCreateError
                                    ? 'border-amber-500/25 bg-amber-500/10 text-amber-100'
                                    : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-100'), "data-testid": "director-task-create-evidence", "data-endpoint": taskCreateEndpoint, children: [_jsx(EndpointBadge, { endpoint: taskCreateEndpoint, testId: "director-task-create-evidence-endpoint" }), _jsx("span", { className: "mx-1.5 text-emerald-200/50", children: "\u00B7" }), isTaskCreating ? '正在提交 Director 任务...' : taskCreateError || taskCreateMessage] })) : null] }), _jsxs("div", { className: "px-4 pb-3", "data-testid": "director-worker-strip", children: [_jsxs("div", { className: "mb-1 flex items-center justify-between gap-2 text-[10px] text-slate-400", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(Layers, { className: "h-3 w-3" }), "Worker \u540E\u7AEF\u8BC1\u636E"] }), _jsxs("span", { children: ["\u603B\u8BA1 ", workerRows.length, " / \u7A7A\u95F2 ", idleWorkerCount, " / \u6267\u884C\u4E2D ", busyWorkerCount, failedWorkerCount > 0 ? ` / 异常 ${failedWorkerCount}` : ''] })] }), workerFallbackError ? (_jsx("div", { className: "rounded-md border border-amber-500/20 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-100", "data-testid": "director-worker-fallback-error", children: workerFallbackError })) : workerRows.length === 0 ? (_jsx("div", { className: "rounded-md border border-white/10 bg-white/[0.03] px-2 py-1.5 text-[11px] text-slate-500", children: "\u6682\u65E0 worker \u8BB0\u5F55\uFF1B\u7B49\u5F85\u540E\u7AEF worker \u6216\u5B9E\u65F6\u6D41\u8FD4\u56DE\u6570\u636E\u3002" })) : (_jsx("div", { className: "flex gap-2 overflow-x-auto", children: workerRows.slice(0, 6).map((worker) => (_jsxs("button", { type: "button", className: cn('flex shrink-0 items-center gap-2 rounded-md border px-2 py-1 text-left text-[10px] transition-colors', worker.healthy === false || worker.status === 'failed'
                                        ? 'border-red-500/25 bg-red-500/10 text-red-100'
                                        : worker.status === 'busy' || worker.status === 'running'
                                            ? 'border-blue-500/25 bg-blue-500/10 text-blue-100'
                                            : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-100', workerBackendDetail?.workerId === worker.id && 'ring-1 ring-cyan-300/40'), title: worker.taskName ? `${worker.name}: ${worker.taskName}` : worker.name, onClick: () => onWorkerSelect?.(worker.id), "data-testid": "director-worker-item", children: [_jsx("span", { className: "max-w-28 truncate font-medium", children: worker.name }), _jsx("span", { className: "rounded bg-slate-950/45 px-1.5 py-0.5 font-mono", children: worker.status }), worker.taskName ? _jsx("span", { className: "max-w-32 truncate text-slate-300", children: worker.taskName }) : null] }, worker.id))) })), workerBackendDetail?.workerId ? (_jsxs("section", { className: "mt-2 rounded-lg border border-cyan-400/20 bg-cyan-500/5 p-2 text-[11px]", "data-testid": "director-worker-backend-detail", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-2", children: [_jsxs("span", { className: "flex items-center gap-1.5 font-medium text-cyan-100", children: [_jsx(User, { className: "h-3.5 w-3.5" }), "Worker \u8BE6\u60C5"] }), _jsx(EndpointBadge, { endpoint: `/v2/director/workers/${workerBackendDetail.workerId}`, testId: "director-worker-backend-detail-endpoint" })] }), workerBackendDetail.loading ? (_jsxs("div", { className: "flex items-center gap-1.5 text-cyan-100", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }), "\u6B63\u5728\u8BFB\u53D6 worker \u5FEB\u7167..."] })) : workerBackendDetail.error ? (_jsx("div", { className: "rounded border border-amber-500/25 bg-amber-500/10 px-2 py-1.5 text-amber-100", children: workerBackendDetail.error })) : workerDetail ? (_jsxs("div", { className: "flex flex-wrap gap-1.5", children: [_jsx(ProvenanceChip, { label: "Name", value: readWorkerDetailText(workerDetail, ['name', 'display_name', 'worker_name']) || workerBackendDetail.workerId }), _jsx(ProvenanceChip, { label: "Status", value: readWorkerDetailText(workerDetail, ['status', 'state']) || 'unknown' }), _jsx(ProvenanceChip, { label: "Task", value: workerDetailTask || '空闲' }), _jsx(ProvenanceChip, { label: "Healthy", value: readWorkerDetailText(workerDetail, ['healthy', 'is_healthy']) || 'unknown' }), _jsx(ProvenanceChip, { label: "Done", value: workerDetailCompleted }), _jsx(ProvenanceChip, { label: "Failed", value: workerDetailFailed })] })) : (_jsx("div", { className: "rounded border border-dashed border-white/10 px-2 py-2 text-slate-500", children: "\u9009\u62E9 worker \u540E\u8BFB\u53D6\u540E\u7AEF\u5FEB\u7167\u3002" }))] })) : null] }), _jsxs("div", { className: "flex flex-wrap items-center gap-2 px-4 pb-3", children: [_jsxs("span", { className: "flex items-center gap-1 text-[10px] text-slate-500", children: [_jsx(Filter, { className: "h-3 w-3" }), "\u7B5B\u9009"] }), FILTERS.map((filter) => (_jsx("button", { type: "button", "data-testid": `director-task-filter-${filter.id}`, onClick: () => setActiveFilter(filter.id), className: cn('rounded-md border px-2 py-1 text-[10px] transition-colors', activeFilter === filter.id
                                    ? 'border-indigo-400/50 bg-indigo-500/[0.15] text-indigo-200'
                                    : 'border-white/10 bg-white/[0.03] text-slate-400 hover:text-slate-200'), children: filter.label }, filter.id)))] })] }), _jsxs("div", { className: "grid min-h-0 flex-1 grid-cols-[minmax(320px,0.95fr)_minmax(360px,1.05fr)] overflow-hidden", children: [_jsx("div", { className: "overflow-auto border-r border-white/5 p-4", "data-testid": "director-task-board", children: tasks.length === 0 ? (_jsxs("div", { className: "flex h-full flex-col items-center justify-center text-slate-500", children: [_jsx(ListTodo, { className: "mb-4 h-12 w-12 text-indigo-500/30" }), _jsx("p", { children: "\u5F53\u524D\u6CA1\u6709\u53EF\u6267\u884C\u4EFB\u52A1" })] })) : (_jsx("div", { className: "space-y-4", children: groups.map((group) => (_jsxs("section", { "data-testid": `director-task-group-${group.id}`, children: [_jsxs("div", { className: "mb-2 flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: group.label }), _jsx("div", { className: "text-[10px] text-slate-500", children: group.description })] }), _jsx("span", { className: "rounded-md bg-white/10 px-2 py-0.5 text-xs font-mono text-slate-300", children: group.tasks.length })] }), group.tasks.length === 0 ? (_jsx("div", { className: "rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-[11px] text-slate-500", children: "\u6682\u65E0\u4EFB\u52A1" })) : (_jsx("div", { className: "space-y-2", children: group.tasks.map((task) => _jsx(TaskCard, { task: task }, task.id)) }))] }, group.id))) })) }), _jsx("aside", { className: "min-w-0 overflow-auto p-4", "data-testid": "director-task-detail", children: !selectedTask ? (_jsxs("div", { className: "flex h-full flex-col items-center justify-center rounded-lg border border-dashed border-white/10 text-slate-500", children: [_jsx(Target, { className: "mb-3 h-10 w-10 text-indigo-500/30" }), _jsx("p", { className: "text-sm", children: "\u9009\u62E9\u5DE6\u4FA7\u4EFB\u52A1\u67E5\u770B\u8BE6\u60C5" })] })) : (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "rounded-lg border border-white/10 bg-white/[0.04] p-4", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "mb-2 flex flex-wrap items-center gap-2", children: [getStatusIcon(selectedTask.status), _jsx("h3", { className: "truncate text-base font-semibold text-slate-100", children: selectedTask.name }), _jsx("span", { className: cn('rounded-md border px-1.5 py-0.5 text-[10px]', getStatusColor(selectedTask.status)), children: getStatusLabel(selectedTask.status) })] }), _jsx("p", { className: "text-xs leading-5 text-slate-400", children: selectedTask.description || selectedTask.goal || '暂无描述' })] }), _jsxs("div", { className: "flex shrink-0 flex-wrap items-center justify-end gap-2", children: [_jsxs(Button, { size: "sm", onClick: onExecute, "data-testid": "director-task-execute-selected", disabled: executionBlocked, title: executionDisabledReason || undefined, className: cn(isExecuting ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700', 'text-white'), children: [isExecuting ? _jsx(Pause, { className: "mr-1.5 h-3.5 w-3.5" }) : _jsx(Zap, { className: "mr-1.5 h-3.5 w-3.5" }), selectedExecuteLabel] }), _jsxs(Button, { size: "sm", variant: "outline", onClick: () => selectedTask && onTaskCancel?.(selectedTask.id), disabled: !onTaskCancel || !canCancelSelectedTask || isTaskCancelling, "data-testid": "director-task-cancel-selected", className: "border-red-500/35 bg-red-500/10 text-red-100 hover:bg-red-500/20 hover:text-red-50", children: [isTaskCancelling ? (_jsx(Loader2, { className: "mr-1.5 h-3.5 w-3.5 animate-spin" })) : (_jsx(XCircle, { className: "mr-1.5 h-3.5 w-3.5" })), "\u53D6\u6D88\u4EFB\u52A1"] })] })] }), _jsxs("div", { className: "mt-3 grid grid-cols-2 gap-2 text-[11px] text-slate-400", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(User, { className: "h-3.5 w-3.5" }), "Worker: ", selectedWorker?.name || selectedTask.assignedWorker || '未分配'] }), _jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(Clock, { className: "h-3.5 w-3.5" }), "\u8017\u65F6: ", formatDuration(selectedTask.actualTime)] }), _jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(FileCode, { className: "h-3.5 w-3.5" }), "\u6587\u4EF6\u6D3B\u52A8: ", selectedTask.filesModified || 0] }), _jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(RotateCcw, { className: "h-3.5 w-3.5" }), "\u91CD\u8BD5: ", selectedTask.retries || 0, selectedTask.maxRetries ? `/${selectedTask.maxRetries}` : ''] })] }), _jsxs("div", { className: "mt-3 flex flex-wrap gap-1.5 text-[10px]", "data-testid": "director-task-provenance", "aria-label": "Director task provenance", children: [_jsx(ProvenanceChip, { label: "PM", value: selectedTask.pmTaskId || selectedTask.id }), _jsx(ProvenanceChip, { label: "BP", value: selectedTask.blueprintId || selectedTask.blueprintPath || '未绑定' }), _jsx(ProvenanceChip, { label: "Owner", value: selectedTask.claimedBy || selectedTask.assignedWorker || selectedWorker?.name || '未分配' }), _jsx(ProvenanceChip, { label: "Source", value: selectedTask.source || 'runtime' })] }), _jsxs("div", { className: "mt-3 rounded-md border border-red-500/20 bg-red-500/10 px-2 py-2 text-[11px] text-red-100", "data-testid": "director-task-cancel-evidence", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(XCircle, { className: "h-3.5 w-3.5" }), "\u53D6\u6D88\u7AEF\u70B9"] }), _jsx(EndpointBadge, { endpoint: selectedTaskCancelEndpoint, testId: "director-task-cancel-endpoint" })] }), isTaskCancelling ? (_jsxs("div", { className: "mt-1.5 flex items-center gap-1.5 text-red-50", children: [_jsx(Loader2, { className: "h-3 w-3 animate-spin" }), "\u6B63\u5728\u63D0\u4EA4\u53D6\u6D88\u8BF7\u6C42"] })) : null, taskCancelMessage ? _jsx("div", { className: "mt-1.5 text-red-50", children: taskCancelMessage }) : null, taskCancelError ? _jsx("div", { className: "mt-1.5 text-amber-100", children: taskCancelError }) : null] })] }), _jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.03] p-3", "data-testid": "director-task-backend-detail", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-medium text-slate-200", children: [_jsx(Code2, { className: "h-3.5 w-3.5 text-cyan-300" }), _jsx("span", { children: "\u540E\u7AEF\u4EFB\u52A1\u8BE6\u60C5" })] }), _jsx(EndpointBadge, { endpoint: selectedTaskDetailEndpoint, testId: "director-task-backend-detail-endpoint" })] }), hasSelectedBackendDetail && taskBackendDetail?.loading ? (_jsxs("div", { className: "flex items-center gap-2 rounded-md border border-cyan-500/20 bg-cyan-500/10 px-2 py-2 text-[11px] text-cyan-100", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }), "\u6B63\u5728\u8BFB\u53D6\u540E\u7AEF\u4EFB\u52A1\u8BE6\u60C5..."] })) : hasSelectedBackendDetail && taskBackendDetail?.error ? (_jsx("div", { className: "rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-2 text-[11px] text-amber-100", children: taskBackendDetail.error })) : backendTask ? (_jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "flex flex-wrap gap-1.5 text-[10px]", children: [_jsx(ProvenanceChip, { label: "Status", value: String(backendTask.status || 'unknown') }), _jsx(ProvenanceChip, { label: "Priority", value: String(backendTask.priority || 'MEDIUM') }), _jsx(ProvenanceChip, { label: "Worker", value: String(backendTask.worker || backendTask.claimed_by || '未分配') }), _jsx(ProvenanceChip, { label: "PM", value: String(backendTask.pm_task_id || backendTask.metadata?.pm_task_id || backendTask.id) }), _jsx(ProvenanceChip, { label: "BP", value: String(backendTask.blueprint_id || backendTask.blueprint_path || '未绑定') })] }), _jsxs("div", { className: "grid gap-1.5 text-[11px] text-slate-300", children: [_jsxs("div", { className: "break-words", children: ["\u76EE\u6807: ", backendTask.goal || backendTask.description || '暂无'] }), _jsxs("div", { className: "break-words", children: ["\u5F53\u524D\u6587\u4EF6: ", backendTask.current_file || '暂无'] }), _jsxs("div", { children: ["\u9A8C\u6536\u9879: ", Array.isArray(backendTask.acceptance) ? backendTask.acceptance.length : 0] })] })] })) : (_jsx("div", { className: "rounded-md border border-dashed border-white/10 px-2 py-3 text-[11px] text-slate-500", children: "\u9009\u62E9\u4EFB\u52A1\u540E\u8BFB\u53D6\u6743\u5A01\u5FEB\u7167\u3002" }))] }), _jsx(DetailSection, { icon: _jsx(Target, { className: "h-3.5 w-3.5 text-indigo-300" }), title: "PM\u76EE\u6807", items: selectedTask.goal ? [selectedTask.goal] : [], emptyText: "\u6682\u65E0 PM \u76EE\u6807" }), _jsx(DetailSection, { icon: _jsx(ListChecks, { className: "h-3.5 w-3.5 text-blue-300" }), title: "\u6267\u884C\u6B65\u9AA4", items: selectedTask.executionSteps, emptyText: "\u6682\u65E0\u6267\u884C\u6B65\u9AA4" }), _jsx(DetailSection, { icon: _jsx(ShieldCheck, { className: "h-3.5 w-3.5 text-emerald-300" }), title: "\u9A8C\u6536\u6807\u51C6", items: selectedTask.acceptanceCriteria, emptyText: "\u6682\u65E0\u9A8C\u6536\u6807\u51C6" }), _jsx(DetailSection, { icon: _jsx(FileCode, { className: "h-3.5 w-3.5 text-cyan-300" }), title: "\u76EE\u6807\u6587\u4EF6", items: selectedTask.targetFiles, emptyText: "\u6682\u65E0\u76EE\u6807\u6587\u4EF6" }), _jsx(DetailSection, { icon: _jsx(GitBranch, { className: "h-3.5 w-3.5 text-amber-300" }), title: "\u4F9D\u8D56", items: [...(selectedTask.dependencies || []), ...(selectedTask.blockedBy || [])], emptyText: "\u6682\u65E0\u4F9D\u8D56" }), _jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.03] p-3", children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-medium text-slate-200", children: [_jsx(Layers, { className: "h-3.5 w-3.5 text-cyan-300" }), _jsx("span", { children: "\u5B9E\u65F6\u6587\u4EF6\u6D3B\u52A8" })] }), _jsxs("div", { className: "space-y-1.5 text-[11px] text-slate-300", children: [_jsxs("div", { children: ["\u5F53\u524D/\u6700\u8FD1\u6587\u4EF6: ", selectedTask.currentFilePath || selectedTask.currentFile || '暂无'] }), selectedTask.lineStats ? (_jsxs("div", { className: "flex gap-3 font-mono", children: [_jsxs("span", { className: "text-emerald-300", children: ["+", selectedTask.lineStats.added] }), _jsxs("span", { className: "text-red-300", children: ["-", selectedTask.lineStats.deleted] }), _jsxs("span", { className: "text-amber-300", children: ["~", selectedTask.lineStats.modified] })] })) : null, selectedTask.operationStats ? (_jsxs("div", { children: ["\u64CD\u4F5C: \u521B\u5EFA ", selectedTask.operationStats.create, " / \u4FEE\u6539 ", selectedTask.operationStats.modify, " / \u5220\u9664 ", selectedTask.operationStats.delete] })) : null, _jsxs("div", { children: ["\u66F4\u65B0\u65F6\u95F4: ", selectedTask.activityUpdatedAt || '暂无'] })] })] }), _jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.03] p-3", "data-testid": "director-task-llm-events", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-medium text-slate-200", children: [_jsx(Brain, { className: "h-3.5 w-3.5 text-indigo-300" }), _jsx("span", { children: "LLM \u8C03\u7528\u8BC1\u636E" })] }), _jsx(EndpointBadge, { endpoint: selectedTaskLLMEndpoint, testId: "director-task-llm-events-endpoint" })] }), _jsxs("div", { className: "mb-2 flex flex-wrap gap-1.5 text-[10px]", children: [_jsx(ProvenanceChip, { label: "Total", value: String(llmStatsTotal) }), _jsx(ProvenanceChip, { label: "Errors", value: String(llmStatsErrors) }), _jsx(ProvenanceChip, { label: "Retries", value: String(llmStatsRetries) })] }), taskLLMEvents?.taskId === selectedTask.id && taskLLMEvents.loading ? (_jsxs("div", { className: "flex items-center gap-2 rounded-md border border-indigo-500/20 bg-indigo-500/10 px-2 py-2 text-[11px] text-indigo-100", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }), "\u6B63\u5728\u8BFB\u53D6\u4EFB\u52A1 LLM \u4E8B\u4EF6..."] })) : taskLLMEvents?.taskId === selectedTask.id && taskLLMEvents.error ? (_jsx("div", { className: "rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-2 text-[11px] text-amber-100", children: taskLLMEvents.error })) : llmEventRows.length === 0 ? (_jsx("div", { className: "rounded-md border border-dashed border-white/10 px-2 py-3 text-[11px] text-slate-500", children: "\u8BE5\u4EFB\u52A1\u6682\u65E0\u540E\u7AEF LLM \u4E8B\u4EF6\u8BB0\u5F55\u3002" })) : (_jsx("div", { className: "space-y-1.5", children: llmEventRows.slice(0, 5).map((event, index) => {
                                                const eventType = readEventText(event, ['event_type', 'type', 'name']);
                                                const timestamp = formatEventTimestamp(readEventText(event, ['timestamp', 'created_at', 'time']));
                                                const model = readEventText(event, ['model', 'provider', 'provider_type']);
                                                const status = readEventText(event, ['status', 'state', 'result']);
                                                return (_jsxs("div", { className: "grid grid-cols-[minmax(90px,0.45fr)_minmax(70px,0.35fr)_minmax(70px,0.2fr)] gap-2 rounded-md border border-white/10 bg-slate-950/45 px-2 py-1.5 text-[11px]", children: [_jsx("span", { className: "truncate font-medium text-indigo-100", title: eventType, children: formatEventType(eventType) }), _jsx("span", { className: "truncate text-slate-300", title: model, children: model || 'model -' }), _jsx("span", { className: "truncate text-right text-slate-500", title: status || timestamp, children: status || timestamp || '-' })] }, `${eventType || 'event'}-${timestamp || index}-${index}`));
                                            }) }))] }), (selectedTask.error || selectedTask.output) && (_jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.03] p-3", children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-medium text-slate-200", children: [_jsx(AlertTriangle, { className: "h-3.5 w-3.5 text-red-300" }), _jsx("span", { children: "\u9519\u8BEF / \u8F93\u51FA" })] }), selectedTask.error && _jsx("pre", { className: "whitespace-pre-wrap rounded-md bg-red-500/10 p-2 text-[11px] text-red-200", children: selectedTask.error }), selectedTask.output && _jsx("pre", { className: "mt-2 whitespace-pre-wrap rounded-md bg-slate-900/60 p-2 text-[11px] text-slate-300", children: selectedTask.output })] })), taskTraceMap?.get(selectedTask.id)?.length ? (_jsx("section", { className: "rounded-lg border border-white/10 bg-white/[0.03] p-3", children: _jsx(TaskTraceTimeline, { traces: taskTraceMap.get(selectedTask.id) || [], maxTraces: 12, expanded: true }) })) : null] })) })] })] }));
}
function ProvenanceChip({ label, value }) {
    return (_jsxs("span", { className: "max-w-full truncate rounded-md border border-white/10 bg-slate-950/55 px-2 py-1 text-slate-300", title: `${label}: ${value}`, children: [_jsx("span", { className: "text-slate-500", children: label }), _jsx("span", { className: "mx-1 text-slate-600", children: "\u00B7" }), _jsx("span", { children: value })] }));
}
