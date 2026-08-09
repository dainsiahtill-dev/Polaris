import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/** FactoryWorkspace - 无人值守开发工厂工作区 */
import { useEffect, useMemo, useState } from 'react';
import { Activity, AlertCircle, BadgeCheck, Brain, CheckCircle2, ChevronRight, ClipboardList, FileCode, FileText, Hammer, Layers, Loader2, PackageCheck, Pause, Play, RotateCcw, Route, ShieldCheck, Square, Terminal, XCircle, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import { TaskStatus } from '@/types/task';
const CANCELLED_RUN_STATUSES = new Set(['cancelled', 'canceled']);
const FAILED_RUN_STATUSES = new Set(['failed', 'error', 'blocked', 'timeout']);
const TERMINAL_RUN_STATUSES = new Set(['completed', ...CANCELLED_RUN_STATUSES, ...FAILED_RUN_STATUSES]);
const RUN_LEDGER_GUARD_STATUSES = new Set(['ledger_pending', 'ledger_unavailable', 'ledger_failed']);
const PHASE_CONFIG = {
    idle: { label: '等待启动', color: 'text-slate-400', icon: _jsx(Hammer, { className: "h-4 w-4" }) },
    planning: { label: '规划中', color: 'text-amber-300', icon: _jsx(ClipboardList, { className: "h-4 w-4" }) },
    executing: { label: '执行中', color: 'text-indigo-300', icon: _jsx(Terminal, { className: "h-4 w-4" }) },
    verifying: { label: '验证中', color: 'text-cyan-300', icon: _jsx(CheckCircle2, { className: "h-4 w-4" }) },
    completed: { label: '已完成', color: 'text-emerald-300', icon: _jsx(CheckCircle2, { className: "h-4 w-4" }) },
    failed: { label: '失败', color: 'text-red-300', icon: _jsx(AlertCircle, { className: "h-4 w-4" }) },
    cancelled: { label: '已取消', color: 'text-orange-300', icon: _jsx(XCircle, { className: "h-4 w-4" }) },
};
const ROLE_LAYER_LABELS = {
    pm: { short: 'PM', route: '任务合同' },
    chief_engineer: { short: 'CE', route: '技术蓝图' },
    director: { short: 'DIR', route: '执行交付' },
};
function normalizeToken(value) {
    return String(value || '').trim().toLowerCase();
}
function mapRunToFactoryPhase(run) {
    const status = normalizeToken(run?.status);
    const phase = normalizeToken(run?.phase);
    const stage = normalizeToken(run?.current_stage);
    if (CANCELLED_RUN_STATUSES.has(status) || CANCELLED_RUN_STATUSES.has(phase))
        return 'cancelled';
    if (FAILED_RUN_STATUSES.has(status) || FAILED_RUN_STATUSES.has(phase))
        return 'failed';
    if (phase === 'completed' || status === 'completed')
        return 'completed';
    if (['verification', 'qa_gate', 'handover', 'quality_gate'].includes(phase) || stage.includes('quality')) {
        return 'verifying';
    }
    if (phase === 'implementation' || stage.includes('director'))
        return 'executing';
    if (stage.includes('chief') || stage.includes('blueprint'))
        return 'planning';
    if (['architect', 'planning', 'pending', 'intake', 'docs_check'].includes(phase) || stage.includes('pm')) {
        return 'planning';
    }
    return 'idle';
}
function mapRunToWorkspacePhase(run) {
    const phase = mapRunToFactoryPhase(run);
    if (phase === 'planning')
        return 'planning';
    if (phase === 'executing')
        return 'executing';
    if (phase === 'verifying')
        return 'verification';
    if (phase === 'completed')
        return 'completed';
    if (phase === 'failed' || phase === 'cancelled')
        return 'error';
    return 'idle';
}
function hasCompletedRunClaim(run) {
    const status = normalizeToken(run?.status);
    const phase = normalizeToken(run?.phase);
    return status === 'completed' || phase === 'completed';
}
function failedLedgerProjectDetail(projection) {
    const failedProject = projection.projects.find((project) => !project.ok || project.failed_gate_count > 0);
    return failedProject?.detail || projection.detail || 'Run Ledger gate failed';
}
function runLedgerGuardedFactoryState(run, projection) {
    const basePhase = mapRunToFactoryPhase(run);
    const baseStatus = normalizeToken(run?.status) || 'idle';
    const baseProgress = percent(Number(run?.progress || 0));
    if (!hasCompletedRunClaim(run)) {
        return {
            phase: basePhase,
            workspacePhase: mapRunToWorkspacePhase(run),
            status: baseStatus,
            progress: baseProgress,
            detail: '',
        };
    }
    if (!projection) {
        return {
            phase: 'verifying',
            workspacePhase: 'verification',
            status: 'ledger_pending',
            progress: Math.min(baseProgress || 99, 99),
            detail: 'Run Ledger projection is required before Factory completion',
        };
    }
    if (!projection.available) {
        return {
            phase: 'failed',
            workspacePhase: 'error',
            status: 'ledger_unavailable',
            progress: baseProgress,
            detail: projection.detail || 'Run Ledger projection unavailable',
        };
    }
    if (projection.total <= 0 && projection.projects.length === 0) {
        return {
            phase: 'verifying',
            workspacePhase: 'verification',
            status: 'ledger_pending',
            progress: Math.min(baseProgress || 99, 99),
            detail: projection.detail || 'Run Ledger projection has no projected projects yet',
        };
    }
    if (!projection.ok || projection.failed > 0 || projection.projects.some((project) => !project.ok)) {
        return {
            phase: 'failed',
            workspacePhase: 'error',
            status: 'ledger_failed',
            progress: baseProgress,
            detail: failedLedgerProjectDetail(projection),
        };
    }
    return {
        phase: 'completed',
        workspacePhase: 'completed',
        status: baseStatus,
        progress: baseProgress,
        detail: projection.detail || `Run Ledger verified ${projection.projected}/${projection.total}`,
    };
}
function hasRunLedgerGuard(state) {
    return RUN_LEDGER_GUARD_STATUSES.has(normalizeToken(state.status));
}
function guardRoleStatusForLedger(role, roleName, state) {
    if (!hasRunLedgerGuard(state))
        return role;
    return {
        role: role?.role || roleName,
        status: state.status,
        detail: state.detail,
        current_task: state.detail,
        progress: Math.min(percent(Number(role?.progress ?? state.progress)), state.progress),
    };
}
function guardRoleLayersForLedger(layers, state) {
    if (!hasRunLedgerGuard(state))
        return layers;
    return layers.map((layer) => ({
        ...layer,
        status: state.status,
        progress: Math.min(layer.progress, state.progress),
        detail: state.detail || layer.detail,
    }));
}
function preferredRoleLayer(run) {
    const stage = normalizeToken(run?.current_stage);
    const phase = mapRunToFactoryPhase(run);
    if (stage.includes('director') || phase === 'executing' || phase === 'verifying')
        return 'director';
    if (stage.includes('chief') || stage.includes('blueprint') || stage.includes('architect'))
        return 'chief_engineer';
    return 'pm';
}
function toEventLevel(event) {
    const type = normalizeToken(event.type);
    const resultStatus = normalizeToken(String(event.result?.status || ''));
    if (CANCELLED_RUN_STATUSES.has(type))
        return 'warning';
    if (FAILED_RUN_STATUSES.has(type))
        return 'error';
    if (type === 'stage_started')
        return 'exec';
    if (type === 'stage_completed' && resultStatus === 'failed')
        return 'error';
    if (type === 'stage_completed' && resultStatus === 'success')
        return 'success';
    if (type === 'completed')
        return 'success';
    return 'info';
}
function toActivityLogs(events) {
    return events.map((event, index) => {
        const message = String(event.message || event.type || 'Factory event').trim();
        const tags = [event.stage, event.type].filter((value) => Boolean(value));
        return {
            id: String(event.event_id || `${event.type}-${index}`),
            timestamp: String(event.timestamp || new Date().toISOString()),
            level: toEventLevel(event),
            source: 'FACTORY',
            title: event.stage ? `阶段: ${event.stage}` : 'Factory 事件',
            message,
            details: event.result ? JSON.stringify(event.result, null, 2) : undefined,
            tags,
        };
    });
}
function toFileEditActivityLogs(events) {
    return events.map((event) => {
        const lineStats = [
            typeof event.addedLines === 'number' ? `+${event.addedLines}` : '',
            typeof event.deletedLines === 'number' ? `-${event.deletedLines}` : '',
            typeof event.modifiedLines === 'number' ? `~${event.modifiedLines}` : '',
        ].filter(Boolean);
        const details = [
            `path=${event.filePath}`,
            event.taskId ? `task=${event.taskId}` : '',
            event.contentSize > 0 ? `bytes=${event.contentSize}` : '',
            lineStats.length > 0 ? `lines=${lineStats.join(' ')}` : '',
        ].filter(Boolean).join(' ');
        return {
            id: `file-edit-${event.id}`,
            timestamp: event.timestamp,
            level: 'tool',
            source: 'Director',
            title: '文件工具',
            message: `${event.operation === 'create' ? '创建' : event.operation === 'delete' ? '删除' : '修改'} ${event.filePath}`,
            details: details || undefined,
            meta: {
                channel: event.sourceChannel || 'event.file_edit',
                streamEvent: 'tool_result',
                tool: event.operation === 'delete' ? 'delete_file' : 'write_file',
                filePath: event.filePath,
                taskId: event.taskId,
            },
            tags: [event.operation, event.taskId].filter((value) => Boolean(value)),
        };
    });
}
function formatBytes(size) {
    if (typeof size !== 'number' || Number.isNaN(size) || size < 0) {
        return 'size n/a';
    }
    if (size < 1024) {
        return `${size} B`;
    }
    if (size < 1024 * 1024) {
        return `${(size / 1024).toFixed(1)} KB`;
    }
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
function formatSummaryValue(value) {
    if (value === null || value === undefined || value === '') {
        return 'n/a';
    }
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        return String(value);
    }
    return JSON.stringify(value);
}
function toSummaryRows(summaryJson) {
    if (!summaryJson) {
        return [];
    }
    return Object.entries(summaryJson)
        .slice(0, 5)
        .map(([key, value]) => [key, formatSummaryValue(value)]);
}
function recordValue(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}
function runMetadata(run) {
    return recordValue(run?.metadata);
}
function metadataString(record, keys) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'string' && value.trim())
            return value.trim();
        if (typeof value === 'number' || typeof value === 'boolean')
            return String(value);
    }
    return '';
}
function buildRunSourceEvidence(run) {
    const metadata = runMetadata(run);
    const startRequest = recordValue(metadata.factory_start_request);
    const rows = [];
    const sessionId = metadataString(metadata, ['export_session_id', 'session_id']);
    const bundlePath = metadataString(metadata, ['export_bundle_path', 'bundle_path']);
    const directive = metadataString(metadata, ['directive']) || metadataString(startRequest, ['directive']);
    const inputSource = metadataString(startRequest, ['input_source']) || metadataString(metadata, ['input_source']);
    const startFrom = metadataString(startRequest, ['start_from']);
    if (sessionId) {
        rows.push({ label: '会话', value: sessionId, tone: 'text-cyan-200' });
    }
    if (bundlePath) {
        rows.push({ label: '证据包', value: bundlePath, tone: 'text-emerald-200' });
    }
    if (directive) {
        rows.push({ label: '指令', value: directive, tone: 'text-slate-200' });
    }
    if (inputSource || startFrom) {
        rows.push({
            label: '入口',
            value: [inputSource, startFrom].filter(Boolean).join(' / '),
            tone: 'text-amber-200',
        });
    }
    return rows;
}
function gateTone(gate) {
    const status = normalizeToken(gate.status);
    if (gate.passed || status === 'passed' || status === 'success') {
        return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300';
    }
    if (status === 'failed' || status === 'error') {
        return 'border-red-500/30 bg-red-500/10 text-red-300';
    }
    if (status === 'running' || status === 'pending') {
        return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
    }
    return 'border-slate-500/30 bg-slate-500/10 text-slate-300';
}
function roleStatusTone(status) {
    const token = normalizeToken(status);
    if (token === 'ledger_pending') {
        return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200';
    }
    if (token === 'ledger_unavailable' || token === 'ledger_failed') {
        return 'border-red-500/30 bg-red-500/10 text-red-200';
    }
    if (['completed', 'ready', 'success', 'passed'].includes(token)) {
        return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
    }
    if (['running', 'active', 'in_progress'].includes(token)) {
        return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200';
    }
    if (['failed', 'error', 'blocked'].includes(token)) {
        return 'border-red-500/30 bg-red-500/10 text-red-200';
    }
    if (['pending', 'waiting'].includes(token)) {
        return 'border-amber-500/30 bg-amber-500/10 text-amber-200';
    }
    return 'border-slate-500/30 bg-slate-500/10 text-slate-300';
}
function roleStatusLabel(status) {
    const token = normalizeToken(status);
    if (token === 'ledger_pending')
        return '账本待验证';
    if (token === 'ledger_unavailable')
        return '账本不可用';
    if (token === 'ledger_failed')
        return '账本失败';
    if (['completed', 'complete', 'ready', 'success', 'passed'].includes(token))
        return '已就绪';
    if (['running', 'active', 'in_progress'].includes(token))
        return '运行中';
    if (['failed', 'error'].includes(token))
        return '失败';
    if (token === 'blocked')
        return '阻塞';
    if (token === 'waiting')
        return '等待';
    if (token === 'pending')
        return '待处理';
    if (token === 'cancelled' || token === 'canceled')
        return '已取消';
    return token || '空闲';
}
function taskStatusToken(task) {
    return normalizeToken(String(task.status || task.state || ''));
}
function isTaskDone(task) {
    const status = taskStatusToken(task);
    return task.done || task.completed === true || status === 'completed' || status === 'success' || status === 'done';
}
function isTaskRunning(task) {
    const status = taskStatusToken(task);
    return status === 'running' || status === 'in_progress' || status === 'active';
}
function taskRecord(task) {
    return task;
}
function taskMetadata(task) {
    return task.metadata && typeof task.metadata === 'object' ? task.metadata : {};
}
function readTaskString(task, keys) {
    const direct = taskRecord(task);
    const metadata = taskMetadata(task);
    for (const key of keys) {
        const directValue = direct[key];
        if (typeof directValue === 'string' && directValue.trim())
            return directValue.trim();
        const metadataValue = metadata[key];
        if (typeof metadataValue === 'string' && metadataValue.trim())
            return metadataValue.trim();
    }
    return '';
}
function stringifyListItem(value) {
    if (typeof value === 'string')
        return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean')
        return String(value);
    if (value && typeof value === 'object') {
        const record = value;
        for (const key of ['description', 'title', 'goal', 'path', 'file', 'name']) {
            const item = record[key];
            if (typeof item === 'string' && item.trim())
                return item.trim();
        }
    }
    return '';
}
function readTaskStringList(task, keys) {
    const direct = taskRecord(task);
    const metadata = taskMetadata(task);
    const rows = [];
    const seen = new Set();
    for (const key of keys) {
        const candidates = [direct[key], metadata[key]];
        for (const candidate of candidates) {
            const values = Array.isArray(candidate)
                ? candidate
                : typeof candidate === 'string' && candidate.trim()
                    ? candidate.split(/\r?\n|;/)
                    : [];
            for (const value of values) {
                const text = stringifyListItem(value);
                const token = normalizeToken(text);
                if (!text || seen.has(token))
                    continue;
                seen.add(token);
                rows.push(text);
            }
        }
    }
    return rows;
}
function taskTitle(task) {
    return task.title || task.subject || readTaskString(task, ['title', 'subject']) || task.id || '未命名任务';
}
function taskGoal(task) {
    return readTaskString(task, ['goal', 'summary', 'description']) || '等待 PM 补齐目标与验收上下文';
}
function taskScopeItems(task) {
    return readTaskStringList(task, ['scope_paths', 'target_files', 'files', 'file_paths']);
}
function taskStepItems(task) {
    return readTaskStringList(task, ['execution_checklist', 'execution_steps', 'steps', 'checklist']);
}
function taskAcceptanceItems(task) {
    return readTaskStringList(task, ['acceptance', 'acceptance_criteria', 'acceptanceCriteria', 'qa_contract']);
}
function isTaskBlocked(task) {
    const status = taskStatusToken(task);
    return status === 'blocked' || status === 'failed' || Boolean(task.error);
}
function isTaskPending(task) {
    const status = taskStatusToken(task);
    return !isTaskDone(task) && !isTaskRunning(task) && !isTaskBlocked(task)
        && (status === '' || status === 'pending' || status === 'idle' || status === 'todo');
}
function taskDisplayStatus(task) {
    if (isTaskDone(task))
        return '完成';
    if (isTaskRunning(task))
        return '执行中';
    if (isTaskBlocked(task))
        return '阻塞';
    return '待办';
}
function taskStatusTone(task) {
    if (isTaskDone(task))
        return 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200';
    if (isTaskRunning(task))
        return 'border-cyan-500/25 bg-cyan-500/10 text-cyan-200';
    if (isTaskBlocked(task))
        return 'border-red-500/25 bg-red-500/10 text-red-200';
    return 'border-amber-500/25 bg-amber-500/10 text-amber-200';
}
function taskPriorityLabel(task) {
    const metadata = taskMetadata(task);
    const value = metadata.priority ?? task.priority;
    if (typeof value === 'number' && Number.isFinite(value))
        return `P${value}`;
    const text = String(value || '').trim();
    return text || 'P-';
}
function buildContractStats(tasks) {
    return tasks.reduce((stats, task) => {
        stats.total += 1;
        if (isTaskDone(task))
            stats.completed += 1;
        else if (isTaskRunning(task))
            stats.running += 1;
        else if (isTaskBlocked(task))
            stats.blocked += 1;
        else
            stats.pending += 1;
        if (readTaskString(task, ['goal', 'summary', 'description']))
            stats.withGoal += 1;
        if (taskScopeItems(task).length > 0)
            stats.withScope += 1;
        if (taskStepItems(task).length > 0)
            stats.withSteps += 1;
        if (taskAcceptanceItems(task).length > 0)
            stats.withAcceptance += 1;
        return stats;
    }, {
        total: 0,
        pending: 0,
        running: 0,
        blocked: 0,
        completed: 0,
        withGoal: 0,
        withScope: 0,
        withSteps: 0,
        withAcceptance: 0,
    });
}
function contractCompleteness(stats) {
    if (stats.total === 0)
        return 0;
    return percent(((stats.withGoal + stats.withScope + stats.withSteps + stats.withAcceptance) / (stats.total * 4)) * 100);
}
function buildDeliveryStats(tasks) {
    return tasks.reduce((stats, task) => {
        stats.total += 1;
        if (isTaskDone(task))
            stats.completed += 1;
        else if (isTaskRunning(task))
            stats.running += 1;
        else if (isTaskBlocked(task))
            stats.blocked += 1;
        else
            stats.ready += 1;
        const claimed = Boolean(task.assigned_to || task.assignedTo || task.assignee || task.assigned_worker || task.worker_id
            || readTaskString(task, ['assigned_to', 'assigned_worker', 'worker_id']));
        if (claimed || isTaskRunning(task) || isTaskDone(task))
            stats.claimed += 1;
        return stats;
    }, {
        total: 0,
        ready: 0,
        running: 0,
        blocked: 0,
        completed: 0,
        claimed: 0,
    });
}
function latestLogRows(logs, limit) {
    const toTimestamp = (value) => {
        const timestamp = Date.parse(String(value || ''));
        return Number.isFinite(timestamp) ? timestamp : 0;
    };
    return [...logs]
        .filter((entry) => Boolean(String(entry.message || entry.title || '').trim()))
        .sort((a, b) => toTimestamp(b.timestamp) - toTimestamp(a.timestamp))
        .slice(0, limit);
}
function hasBlueprintEvidence(task) {
    return Boolean(readTaskString(task, ['blueprint_id', 'blueprint_path', 'runtime_blueprint_path']));
}
function taskDisplayText(task) {
    return String(taskTitle(task) || task.goal || task.summary || task.description || task.id || '').trim();
}
function taskIdentityTokens(task) {
    const metadata = taskMetadata(task);
    const direct = taskRecord(task);
    const values = [
        task.id,
        direct.task_id,
        direct.pm_task_id,
        direct.taskId,
        direct.subject,
        metadata.task_id,
        metadata.pm_task_id,
        metadata.taskId,
        metadata.id,
        metadata.subject,
    ];
    const seen = new Set();
    const tokens = [];
    for (const value of values) {
        const token = normalizeToken(String(value || ''));
        if (!token || seen.has(token))
            continue;
        seen.add(token);
        tokens.push(token);
    }
    return tokens;
}
function mergeFactoryTaskPools(...pools) {
    const byKey = new Map();
    for (const pool of pools) {
        for (const task of pool) {
            const tokens = taskIdentityTokens(task).map(canonicalFactoryTaskId).filter(Boolean);
            const key = tokens[0] || canonicalFactoryTaskId(task.id);
            if (!key)
                continue;
            const current = byKey.get(key);
            if (!current || (!taskDisplayText(current) && taskDisplayText(task))) {
                byKey.set(key, task);
            }
            for (const token of tokens) {
                if (!byKey.has(token)) {
                    byKey.set(token, byKey.get(key) || task);
                }
            }
        }
    }
    return Array.from(new Set(byKey.values()));
}
function canonicalFactoryTaskId(value) {
    const text = String(value || '').trim();
    if (!text)
        return '';
    const normalized = normalizeToken(text);
    const numericAlias = normalized.match(/^(?:task|pm-task|pm)[-_]?(\d+)$/);
    return numericAlias ? numericAlias[1] : normalized;
}
function directorTaskOverlayStatus(task) {
    if (isTaskRunning(task))
        return TaskStatus.IN_PROGRESS;
    if (isTaskBlocked(task)) {
        const status = taskStatusToken(task);
        return status === 'failed' || status === 'error' ? TaskStatus.FAILED : TaskStatus.BLOCKED;
    }
    if (isTaskDone(task))
        return TaskStatus.COMPLETED;
    return null;
}
function directorTaskOverlayRank(status) {
    if (status === TaskStatus.IN_PROGRESS)
        return 4;
    if (status === TaskStatus.FAILED || status === TaskStatus.BLOCKED)
        return 3;
    if (status === TaskStatus.COMPLETED || status === TaskStatus.SUCCESS)
        return 2;
    return 0;
}
function overlayDirectorRuntimeState(pmTasks, directorTasks) {
    if (!pmTasks.length || !directorTasks.length)
        return pmTasks;
    const overlays = new Map();
    for (const task of directorTasks) {
        const status = directorTaskOverlayStatus(task);
        const rank = directorTaskOverlayRank(status);
        if (!status || rank <= 0)
            continue;
        const tokens = taskIdentityTokens(task).map(canonicalFactoryTaskId).filter(Boolean);
        for (const token of tokens) {
            const current = overlays.get(token);
            if (!current || current.rank < rank) {
                overlays.set(token, { status, task, rank });
            }
        }
    }
    if (overlays.size === 0)
        return pmTasks;
    return pmTasks.map((task) => {
        const overlay = taskIdentityTokens(task)
            .map(canonicalFactoryTaskId)
            .map((token) => overlays.get(token))
            .find(Boolean);
        if (!overlay)
            return task;
        const completed = overlay.status === TaskStatus.COMPLETED || overlay.status === TaskStatus.SUCCESS;
        return {
            ...task,
            status: overlay.status,
            state: overlay.status,
            done: completed,
            completed,
            started_at: overlay.task.started_at || overlay.task.startedAt || task.started_at,
            completed_at: completed ? overlay.task.completed_at || overlay.task.completedAt || task.completed_at : task.completed_at,
            worker_id: overlay.task.worker_id || overlay.task.assigned_worker || task.worker_id,
            assigned_worker: overlay.task.assigned_worker || overlay.task.worker_id || task.assigned_worker,
            metadata: {
                ...(task.metadata || {}),
                runtime_overlay_source: 'director_realtime',
                runtime_overlay_status: overlay.status,
            },
        };
    });
}
function isChiefEngineerArtifact(artifact) {
    const path = normalizeToken(artifact.path);
    const name = normalizeToken(artifact.name);
    return (path.includes('runtime/blueprints/')
        || (name.includes('blueprint') && !name.includes('review')));
}
function isChiefEngineerReviewArtifact(artifact) {
    const path = normalizeToken(artifact.path);
    const name = normalizeToken(artifact.name);
    return path.includes('runtime/state/blueprints/') || name.includes('.review') || name.includes('review');
}
function isLatestReviewArtifact(artifact) {
    return normalizeToken(artifact.name || artifact.path).includes('latest.review');
}
function reviewArtifactGroupKey(artifact) {
    const path = String(artifact.path || '').trim();
    const name = String(artifact.name || basename(path)).trim();
    const token = `${path}/${name}`;
    const runMatch = token.match(/factory[_-][a-z0-9_-]+/i);
    return normalizeToken(runMatch?.[0] || path || name);
}
function reviewArtifactRank(artifact) {
    const path = normalizeToken(artifact.path);
    if (path.includes('runtime/state/blueprints/'))
        return 0;
    if (path.includes('workspace/blueprints/') && !path.includes('latest.review'))
        return 1;
    if (path.includes('workspace/roles/chief_engineer/'))
        return 2;
    if (path.includes('latest.review'))
        return 9;
    return 5;
}
function basename(path) {
    const normalized = String(path || '').replace(/\\/g, '/').trim();
    return normalized.split('/').filter(Boolean).pop() || normalized || 'blueprint';
}
function workspaceLabel(workspace) {
    const normalized = String(workspace || '').replace(/\\/g, '/').trim();
    if (!normalized)
        return '未设置工作区';
    return normalized.split('/').filter(Boolean).pop() || normalized;
}
function readBenchMetaString(meta, key) {
    const value = meta[key];
    return typeof value === 'string' && value.trim() ? value.trim() : '';
}
function joinBenchWorkspace(workDir, projectId) {
    if (!workDir || !projectId)
        return '';
    return `${workDir.replace(/[\\/]+$/, '')}/${projectId.replace(/^[\\/]+/, '')}`;
}
function latestBenchProjectWorkspace(bench) {
    const events = bench?.events || [];
    for (const event of [...events].reverse()) {
        if (!event.type?.startsWith('factory_bench.project.'))
            continue;
        const meta = event.meta || {};
        const explicit = readBenchMetaString(meta, 'workspace') ||
            readBenchMetaString(meta, 'workspace_path') ||
            readBenchMetaString(meta, 'project_workspace') ||
            readBenchMetaString(meta, 'projectWorkspace');
        if (explicit)
            return explicit;
        const joined = joinBenchWorkspace(readBenchMetaString(meta, 'work_dir'), readBenchMetaString(meta, 'project_id') || readBenchMetaString(meta, 'projectId'));
        if (joined)
            return joined;
    }
    return '';
}
function artifactTaskIdFromName(value) {
    const base = basename(value).replace(/\.[^.]+$/, '').trim();
    if (!base)
        return '';
    const normalized = base.toLowerCase();
    for (const prefix of ['ce_', 'ce-', 'blueprint_', 'blueprint-', 'chief_engineer_', 'chief-engineer-']) {
        if (normalized.startsWith(prefix)) {
            return base.slice(prefix.length).trim();
        }
    }
    return '';
}
function artifactTaskId(artifact) {
    const record = artifact;
    for (const key of ['task_id', 'pm_task_id', 'taskId']) {
        const value = String(record[key] || '').trim();
        if (value)
            return value;
    }
    return artifactTaskIdFromName(artifact.name) || artifactTaskIdFromName(artifact.path);
}
function buildBlueprintEvidence(tasks, artifacts) {
    const rows = [];
    const seen = new Set();
    const tasksByCanonicalId = new Map();
    for (const task of tasks) {
        for (const token of taskIdentityTokens(task)) {
            const canonical = canonicalFactoryTaskId(token);
            if (canonical && !tasksByCanonicalId.has(canonical)) {
                tasksByCanonicalId.set(canonical, task);
            }
        }
    }
    for (const task of tasks) {
        if (!hasBlueprintEvidence(task))
            continue;
        const blueprintId = readTaskString(task, ['blueprint_id']) || task.id;
        const path = readTaskString(task, ['blueprint_path', 'runtime_blueprint_path']) || blueprintId;
        const key = path || blueprintId;
        if (seen.has(key))
            continue;
        seen.add(key);
        rows.push({
            id: blueprintId,
            taskId: String(task.id || '').trim(),
            title: taskDisplayText(task) || blueprintId,
            path,
            summary: task.summary || task.goal || task.description || '任务合同携带的 Chief Engineer 蓝图字段',
            source: 'task',
        });
    }
    for (const artifact of artifacts.filter(isChiefEngineerArtifact)) {
        const path = String(artifact.path || '').trim();
        const name = String(artifact.name || basename(path)).trim();
        const key = path || name;
        if (!key || seen.has(key))
            continue;
        seen.add(key);
        const taskId = artifactTaskId(artifact);
        const matchedTask = tasksByCanonicalId.get(canonicalFactoryTaskId(taskId));
        rows.push({
            id: name,
            taskId,
            title: matchedTask ? taskDisplayText(matchedTask) || taskId || name : taskId || name,
            path: path || name,
            summary: matchedTask?.summary || matchedTask?.goal || matchedTask?.description || '',
            source: 'artifact',
        });
    }
    return rows;
}
function buildChiefEngineerReviewArtifacts(artifacts) {
    const byKey = new Map();
    const reviewArtifacts = artifacts.filter(isChiefEngineerReviewArtifact);
    const hasSpecificReview = reviewArtifacts.some((artifact) => !isLatestReviewArtifact(artifact));
    for (const artifact of reviewArtifacts) {
        if (hasSpecificReview && isLatestReviewArtifact(artifact))
            continue;
        const path = String(artifact.path || '').trim();
        const name = String(artifact.name || basename(path)).trim();
        const key = reviewArtifactGroupKey(artifact);
        if (!key)
            continue;
        const rank = reviewArtifactRank(artifact);
        const current = byKey.get(key);
        if (current && current.rank <= rank)
            continue;
        byKey.set(key, {
            rank,
            row: {
                id: name,
                title: name,
                path: path || name,
                summary: 'Factory Chief Engineer review summary artifact',
                source: 'artifact',
            },
        });
    }
    return Array.from(byKey.values()).map((entry) => entry.row);
}
function buildBlueprintCoverage(tasks, blueprintEvidence) {
    const evidenceTaskIds = new Set(blueprintEvidence.map((item) => canonicalFactoryTaskId(item.taskId)).filter(Boolean));
    const byTaskKey = new Map();
    for (const task of tasks) {
        const tokens = taskIdentityTokens(task);
        const key = tokens[0] || normalizeToken(task.id);
        if (!key)
            continue;
        const covered = hasBlueprintEvidence(task) || tokens.some((token) => evidenceTaskIds.has(canonicalFactoryTaskId(token)));
        const completed = isTaskDone(task);
        const existing = byTaskKey.get(key);
        if (!existing) {
            byTaskKey.set(key, { task, covered, completed });
            continue;
        }
        existing.covered = existing.covered || covered;
        existing.completed = existing.completed && completed;
        if (!existing.covered && covered) {
            existing.task = task;
        }
    }
    const rows = Array.from(byTaskKey.values());
    const activeRows = rows.filter((row) => !row.completed);
    const missing = activeRows.filter((row) => !row.covered).map((row) => row.task);
    return {
        required: activeRows.length,
        covered: activeRows.length - missing.length,
        completed: rows.length - activeRows.length,
        missing,
    };
}
function getRunRole(roles, keys) {
    if (!roles)
        return null;
    const normalizedKeys = keys.map(normalizeToken);
    for (const key of keys) {
        const direct = roles[key];
        if (direct)
            return direct;
    }
    for (const [key, role] of Object.entries(roles)) {
        const normalizedKey = normalizeToken(key);
        const normalizedRoleName = normalizeToken(role.role);
        if (normalizedKeys.includes(normalizedKey) || normalizedKeys.includes(normalizedRoleName)) {
            return role;
        }
    }
    return null;
}
function roleDetail(role) {
    return String(role?.detail || role?.current_task || '').trim();
}
function isFailedRole(role) {
    return ['failed', 'error', 'blocked', 'timeout'].includes(normalizeToken(role?.status));
}
function buildFactoryFailureBrief(run) {
    const status = normalizeToken(run?.status);
    if (!run?.failure && !FAILED_RUN_STATUSES.has(status)) {
        return null;
    }
    const pmRole = getRunRole(run?.roles, ['pm']);
    const chiefRole = getRunRole(run?.roles, ['chief_engineer', 'chiefengineer', 'architect']);
    const directorRole = getRunRole(run?.roles, ['director']);
    const qaRole = getRunRole(run?.roles, ['qa']);
    const failureDetail = String(run?.failure?.detail || '').trim();
    const pmDetail = roleDetail(pmRole);
    const chiefDetail = roleDetail(chiefRole);
    const directorDetail = roleDetail(directorRole);
    const qaDetail = roleDetail(qaRole);
    const combined = [failureDetail, pmDetail, chiefDetail, directorDetail, qaDetail].join(' ').toLowerCase();
    if (isFailedRole(pmRole) || combined.includes('pm iteration failed')) {
        return {
            rootRole: 'PM',
            headline: 'PM 阶段失败',
            detail: pmDetail || failureDetail || 'PM iteration failed',
            cascades: [chiefDetail, directorDetail, qaDetail].filter((item) => item && item !== pmDetail),
            code: run?.failure?.code || 'PM_ITERATION_FAILED',
            recoverable: Boolean(run?.failure?.recoverable),
        };
    }
    if (isFailedRole(chiefRole) || combined.includes('chief')) {
        return {
            rootRole: 'Chief Engineer',
            headline: 'Chief Engineer 蓝图层阻塞',
            detail: chiefDetail || failureDetail || 'Chief Engineer handoff blocked',
            cascades: [directorDetail, qaDetail].filter(Boolean),
            code: run?.failure?.code || 'CHIEF_ENGINEER_BLOCKED',
            recoverable: Boolean(run?.failure?.recoverable),
        };
    }
    if (isFailedRole(directorRole) || combined.includes('director')) {
        return {
            rootRole: 'Director',
            headline: 'Director 执行层失败',
            detail: directorDetail || failureDetail || 'Director execution failed',
            cascades: [qaDetail].filter(Boolean),
            code: run?.failure?.code || 'DIRECTOR_FAILED',
            recoverable: Boolean(run?.failure?.recoverable),
        };
    }
    return {
        rootRole: 'Factory',
        headline: 'Factory 运行失败',
        detail: failureDetail || '运行已进入失败态',
        cascades: [pmDetail, chiefDetail, directorDetail, qaDetail].filter(Boolean),
        code: run?.failure?.code || status || 'FACTORY_FAILED',
        recoverable: Boolean(run?.failure?.recoverable),
    };
}
function percent(value) {
    if (!Number.isFinite(value))
        return 0;
    return Math.max(0, Math.min(100, Math.round(value)));
}
function buildRoleLayers({ currentRun, pmTasks, directorTasks, blueprintEvidenceCount, blueprintCoverage, }) {
    const pmRole = getRunRole(currentRun?.roles, ['pm']);
    const chiefRole = getRunRole(currentRun?.roles, ['chief_engineer', 'chiefengineer', 'architect']);
    const directorRole = getRunRole(currentRun?.roles, ['director']);
    const completedPmTasks = pmTasks.filter(isTaskDone).length;
    const runningDirectorTasks = directorTasks.filter(isTaskRunning).length;
    const completedDirectorTasks = directorTasks.filter(isTaskDone).length;
    const pmProgress = pmRole ? percent(pmRole.progress) : pmTasks.length > 0 ? percent((completedPmTasks / pmTasks.length) * 100) : 0;
    const chiefProgress = chiefRole
        ? percent(chiefRole.progress)
        : blueprintCoverage.required > 0
            ? percent((blueprintCoverage.covered / blueprintCoverage.required) * 100)
            : blueprintEvidenceCount > 0
                ? 100
                : 0;
    const chiefFallbackStatus = blueprintCoverage.missing.length > 0
        ? 'waiting'
        : blueprintCoverage.required > 0 || blueprintEvidenceCount > 0
            ? 'ready'
            : 'waiting';
    const directorProgress = directorRole
        ? percent(directorRole.progress)
        : directorTasks.length > 0
            ? percent((completedDirectorTasks / directorTasks.length) * 100)
            : 0;
    return [
        {
            id: 'pm',
            order: '01',
            title: 'PM',
            subtitle: '任务合同层',
            status: pmRole?.status || (pmTasks.length > 0 ? 'ready' : 'idle'),
            progress: pmProgress,
            metric: `${completedPmTasks}/${pmTasks.length}`,
            detail: pmRole?.detail || pmRole?.current_task || '规划目标、范围、验收与任务拆分',
            icon: _jsx(ClipboardList, { className: "h-4 w-4" }),
            tone: {
                idle: 'border-amber-500/20 bg-amber-500/5 hover:border-amber-400/40',
                active: 'border-amber-400/60 bg-amber-500/14',
                text: 'text-amber-100',
                progress: 'from-amber-500 to-yellow-300',
            },
        },
        {
            id: 'chief_engineer',
            order: '02',
            title: 'Chief Engineer',
            subtitle: '蓝图交接层',
            status: chiefRole?.status || chiefFallbackStatus,
            progress: chiefProgress,
            metric: blueprintCoverage.required > 0
                ? `${blueprintCoverage.covered}/${blueprintCoverage.required} 蓝图`
                : `${blueprintEvidenceCount} 条蓝图`,
            detail: chiefRole?.detail
                || chiefRole?.current_task
                || (blueprintCoverage.missing.length > 0
                    ? `还有 ${blueprintCoverage.missing.length} 个任务缺少蓝图证据`
                    : '审阅任务，沉淀施工蓝图与 Director 交接条件'),
            icon: _jsx(Brain, { className: "h-4 w-4" }),
            tone: {
                idle: 'border-cyan-500/20 bg-cyan-500/5 hover:border-cyan-400/40',
                active: 'border-cyan-400/50 bg-cyan-500/[0.12] shadow-[0_0_24px_rgba(34,211,238,0.12)]',
                text: 'text-cyan-100',
                progress: 'from-cyan-500 to-sky-300',
            },
        },
        {
            id: 'director',
            order: '03',
            title: 'Director',
            subtitle: '执行交付层',
            status: directorRole?.status || (runningDirectorTasks > 0 ? 'running' : directorTasks.length > 0 ? 'ready' : 'idle'),
            progress: directorProgress,
            metric: `${runningDirectorTasks} 执行中`,
            detail: directorRole?.detail || directorRole?.current_task || '领取任务，执行文件变更、命令与验证',
            icon: _jsx(Hammer, { className: "h-4 w-4" }),
            tone: {
                idle: 'border-indigo-500/20 bg-indigo-500/5 hover:border-indigo-400/40',
                active: 'border-indigo-400/50 bg-indigo-500/[0.12] shadow-[0_0_24px_rgba(99,102,241,0.14)]',
                text: 'text-indigo-100',
                progress: 'from-indigo-500 to-violet-300',
            },
        },
    ];
}
export function FactoryWorkspace({ workspace, onBackToMain, tasks, pmTasks, directorTasks, executionLogs = [], llmStreamEvents = [], processStreamEvents = [], fileEditEvents = [], currentRun = null, events = [], artifacts, summaryMd, summaryJson, artifactsError, isArtifactsLoading = false, onStart, onCancel, onPause, onResume, onRetryCheckpoint, isLoading = false, bench, internalBenchEnabled = false, websocketLive, websocketReconnecting, websocketAttemptCount, controlPlaneProjection, }) {
    const guardedFactoryState = runLedgerGuardedFactoryState(currentRun, controlPlaneProjection);
    const factoryPhase = guardedFactoryState.phase;
    const workspacePhase = guardedFactoryState.workspacePhase;
    const phaseConfig = PHASE_CONFIG[factoryPhase];
    const runStatus = guardedFactoryState.status;
    const isRunActive = runStatus === 'running' || runStatus === 'recovering';
    const isRunPaused = runStatus === 'paused';
    const canStart = !currentRun || TERMINAL_RUN_STATUSES.has(runStatus);
    const canCancel = runStatus === 'running' || runStatus === 'recovering' || isRunPaused;
    const canPause = runStatus === 'running' || runStatus === 'recovering';
    const canResume = isRunPaused;
    const canRetryCheckpoint = runStatus === 'failed' || runStatus === 'blocked' || runStatus === 'timeout';
    const pmWorkflowTasks = pmTasks ?? tasks;
    const directorWorkflowTasks = directorTasks ?? tasks;
    const pmWorkflowTasksWithRuntimeState = useMemo(() => overlayDirectorRuntimeState(pmWorkflowTasks, directorWorkflowTasks), [directorWorkflowTasks, pmWorkflowTasks]);
    const blueprintTaskPool = useMemo(() => mergeFactoryTaskPools(pmWorkflowTasks, directorWorkflowTasks), [directorWorkflowTasks, pmWorkflowTasks]);
    const activityLogs = useMemo(() => toActivityLogs(events), [events]);
    const fileEditActivityLogs = useMemo(() => toFileEditActivityLogs(fileEditEvents), [fileEditEvents]);
    const operationsActivityLogs = useMemo(() => [...activityLogs, ...executionLogs, ...fileEditActivityLogs], [activityLogs, executionLogs, fileEditActivityLogs]);
    const gateResults = currentRun?.gates || [];
    const deliveryArtifacts = artifacts || currentRun?.artifacts || [];
    const blueprintEvidence = useMemo(() => buildBlueprintEvidence(blueprintTaskPool, deliveryArtifacts), [blueprintTaskPool, deliveryArtifacts]);
    const chiefReviewArtifacts = useMemo(() => buildChiefEngineerReviewArtifacts(deliveryArtifacts), [deliveryArtifacts]);
    const blueprintCoverage = useMemo(() => buildBlueprintCoverage(blueprintTaskPool, blueprintEvidence), [blueprintEvidence, blueprintTaskPool]);
    const summaryMarkdown = String(summaryMd ?? currentRun?.summary_md ?? '').trim();
    const summaryRows = toSummaryRows(summaryJson ?? currentRun?.summary_json ?? null);
    const artifactErrorMessage = String(artifactsError || currentRun?.artifacts_error || '').trim();
    const suggestedLayer = preferredRoleLayer(currentRun);
    const [activeLayer, setActiveLayer] = useState(() => suggestedLayer);
    useEffect(() => {
        setActiveLayer(suggestedLayer);
    }, [suggestedLayer]);
    const pmRoleStatus = guardRoleStatusForLedger(getRunRole(currentRun?.roles, ['pm']), 'PM', guardedFactoryState);
    const chiefEngineerRoleStatus = guardRoleStatusForLedger(getRunRole(currentRun?.roles, ['chief_engineer', 'chiefengineer', 'architect']), 'Chief Engineer', guardedFactoryState);
    const directorRoleStatus = guardRoleStatusForLedger(getRunRole(currentRun?.roles, ['director']), 'Director', guardedFactoryState);
    const roleLayers = useMemo(() => guardRoleLayersForLedger(buildRoleLayers({
        currentRun,
        pmTasks: pmWorkflowTasks,
        directorTasks: directorWorkflowTasks,
        blueprintEvidenceCount: blueprintEvidence.length,
        blueprintCoverage,
    }), guardedFactoryState), [blueprintCoverage, blueprintEvidence.length, currentRun, directorWorkflowTasks, guardedFactoryState, pmWorkflowTasks]);
    const activeLayerView = roleLayers.find((layer) => layer.id === activeLayer) || roleLayers[0];
    const effectiveBench = internalBenchEnabled ? bench : undefined;
    const effectiveWorkspace = latestBenchProjectWorkspace(effectiveBench) || workspace;
    const workspaceDisplay = workspaceLabel(effectiveWorkspace);
    return (_jsxs("div", { className: "polaris-soft-scope soft-app-bg relative flex h-full min-h-[100dvh] flex-col overflow-hidden text-slate-100", children: [_jsxs("header", { className: "soft-panel-subtle flex h-16 shrink-0 items-center justify-between border-b border-white/10 px-4", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-3", children: [_jsx("button", { type: "button", onClick: onBackToMain, className: "rounded-lg p-2 text-slate-400 transition-colors hover:bg-white/10 hover:text-slate-100", "aria-label": "\u8FD4\u56DE\u4E3B\u754C\u9762", children: _jsx(RotateCcw, { className: "h-4 w-4" }) }), _jsx("div", { className: "h-6 w-px bg-white/10" }), _jsx("div", { className: "soft-raised flex h-9 w-9 items-center justify-center rounded-lg text-accent", children: _jsx(Hammer, { className: "h-5 w-5" }) }), _jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("h1", { className: "truncate text-sm font-semibold text-slate-100", children: "Factory \u6A21\u5F0F" }), _jsx("span", { className: "rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-slate-400", children: "\u5206\u5C42\u89C6\u56FE" })] }), _jsx("p", { "data-testid": "factory-workspace-label", className: "truncate text-[11px] text-slate-500", title: effectiveWorkspace || workspaceDisplay, children: workspaceDisplay })] })] }), _jsxs("div", { className: "flex min-w-0 items-center justify-end gap-2", children: [_jsxs("div", { className: cn('soft-chip flex shrink-0 items-center gap-2 rounded-lg px-3 py-1.5', factoryPhase === 'planning' && 'border-amber-500/30 bg-amber-500/10', factoryPhase === 'executing' && 'border-indigo-500/30 bg-indigo-500/10', factoryPhase === 'verifying' && 'border-cyan-500/30 bg-cyan-500/10', factoryPhase === 'completed' && 'border-emerald-500/30 bg-emerald-500/10', factoryPhase === 'failed' && 'border-red-500/30 bg-red-500/10', factoryPhase === 'cancelled' && 'border-orange-500/30 bg-orange-500/10', factoryPhase === 'idle' && 'border-slate-500/30 bg-slate-500/10'), children: [(isRunActive || isLoading) ? (_jsx(Loader2, { className: cn('h-4 w-4 animate-spin', phaseConfig.color) })) : (phaseConfig.icon), _jsx("span", { className: cn('text-sm font-medium', phaseConfig.color), children: phaseConfig.label })] }), _jsxs("div", { className: "hidden min-w-0 items-center gap-2 lg:flex", children: [_jsx(StatusChip, { label: "\u9636\u6BB5", value: workspacePhase }), _jsx(StatusChip, { label: "\u72B6\u6001", value: runStatus }), _jsx(StatusChip, { label: "\u6B65\u9AA4", value: currentRun?.current_stage || 'n/a' }), _jsx(StatusChip, { label: "\u8FDB\u5EA6", value: `${guardedFactoryState.progress}%` })] }), _jsxs("div", { className: "ml-1 flex items-center gap-2", children: [canStart && onStart && (_jsxs(Button, { size: "sm", onClick: onStart, disabled: isLoading, className: "bg-emerald-600 hover:bg-emerald-700", children: [isLoading ? (_jsx(Loader2, { className: "mr-1 h-4 w-4 animate-spin" })) : (_jsx(Play, { className: "mr-1 h-4 w-4" })), isLoading ? '启动中...' : '启动'] })), canPause && onPause && (_jsxs(Button, { size: "sm", variant: "outline", onClick: onPause, disabled: isLoading, className: "border-amber-500/25 bg-amber-500/10 text-amber-100 hover:bg-amber-500/20", "data-testid": "factory-run-pause", children: [_jsx(Pause, { className: "mr-1 h-4 w-4" }), "\u6682\u505C"] })), canResume && onResume && (_jsxs(Button, { size: "sm", variant: "outline", onClick: onResume, disabled: isLoading, className: "border-emerald-500/25 bg-emerald-500/10 text-emerald-100 hover:bg-emerald-500/20", "data-testid": "factory-run-resume", children: [_jsx(Play, { className: "mr-1 h-4 w-4" }), "\u6062\u590D"] })), canRetryCheckpoint && onRetryCheckpoint && (_jsxs(Button, { size: "sm", variant: "outline", onClick: onRetryCheckpoint, disabled: isLoading, className: "border-cyan-500/25 bg-cyan-500/10 text-cyan-100 hover:bg-cyan-500/20", "data-testid": "factory-run-retry-checkpoint", children: [_jsx(RotateCcw, { className: "mr-1 h-4 w-4" }), "\u91CD\u8BD5"] })), canCancel && onCancel && (_jsxs(Button, { size: "sm", variant: "destructive", onClick: onCancel, disabled: isLoading, "data-testid": "factory-run-cancel", children: [_jsx(Square, { className: "mr-1 h-4 w-4" }), "\u53D6\u6D88"] }))] })] })] }), internalBenchEnabled ? (_jsx(BenchStatusStrip, { enabled: internalBenchEnabled, bench: effectiveBench, websocketLive: websocketLive, websocketReconnecting: websocketReconnecting, websocketAttemptCount: websocketAttemptCount })) : null, _jsxs("main", { "data-testid": "factory-layered-layout", className: "flex min-h-0 flex-1 flex-col overflow-hidden", children: [_jsx("section", { "data-testid": "factory-role-flow-rail", className: "soft-panel-subtle shrink-0 border-b border-white/10 px-4 py-3", children: _jsx(RoleLayerRail, { layers: roleLayers, activeLayer: activeLayerView.id, suggestedLayer: suggestedLayer, onSelect: setActiveLayer }) }), _jsxs("div", { className: "grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)_minmax(260px,34vh)] overflow-hidden xl:grid-cols-[minmax(0,1fr)_360px] xl:grid-rows-1 2xl:grid-cols-[minmax(0,1fr)_400px]", children: [_jsxs("section", { className: "h-full min-w-0 overflow-hidden", "data-testid": "factory-focused-layer", children: [activeLayerView.id === 'pm' && (_jsx(FactoryPmLayer, { tasks: pmWorkflowTasksWithRuntimeState, workspace: effectiveWorkspace, executionLogs: executionLogs, roleStatus: pmRoleStatus, factoryPhase: factoryPhase, blueprintCoverage: blueprintCoverage })), activeLayerView.id === 'chief_engineer' && (_jsx(FactoryChiefEngineerLayer, { workspace: effectiveWorkspace, blueprintEvidence: blueprintEvidence, reviewArtifacts: chiefReviewArtifacts, blueprintCoverage: blueprintCoverage, roleStatus: chiefEngineerRoleStatus, currentRun: currentRun })), activeLayerView.id === 'director' && (_jsx(FactoryDirectorLayer, { workspace: effectiveWorkspace, tasks: directorWorkflowTasks, fileEditEvents: fileEditEvents, executionLogs: executionLogs, roleStatus: directorRoleStatus, factoryPhase: factoryPhase, blueprintCoverage: blueprintCoverage }))] }), _jsx(FactoryOperationsRail, { currentRun: currentRun, guardedFactoryState: guardedFactoryState, factoryPhase: factoryPhase, workspacePhase: workspacePhase, activeLayer: activeLayerView.id, activityLogs: operationsActivityLogs, llmStreamEvents: llmStreamEvents, processStreamEvents: processStreamEvents, gateResults: gateResults, deliveryArtifacts: deliveryArtifacts, summaryMarkdown: summaryMarkdown, summaryRows: summaryRows, artifactErrorMessage: artifactErrorMessage, isArtifactsLoading: isArtifactsLoading, isRunning: isRunActive || isLoading })] })] })] }));
}
function RoleLayerRail({ layers, activeLayer, suggestedLayer, onSelect, }) {
    return (_jsxs("div", { className: "grid gap-3 xl:grid-cols-[220px_minmax(0,1fr)] xl:items-stretch", children: [_jsxs("div", { className: "flex min-w-0 flex-wrap items-center justify-between gap-3 xl:block", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400", children: [_jsx(Layers, { className: "h-3.5 w-3.5 text-emerald-300" }), _jsx("span", { children: "\u89D2\u8272\u5206\u5C42" })] }), _jsxs("div", { className: "hidden items-center gap-1.5 text-[11px] text-slate-500 md:flex xl:mt-3", children: [_jsx(Route, { className: "h-3.5 w-3.5" }), _jsx("span", { children: "PM \u4EFB\u52A1\u5408\u540C" }), _jsx(ChevronRight, { className: "h-3 w-3" }), _jsx("span", { children: "CE \u6280\u672F\u84DD\u56FE" }), _jsx(ChevronRight, { className: "h-3 w-3" }), _jsx("span", { children: "Director \u6267\u884C\u4EA4\u4ED8" })] })] }), _jsx("div", { className: "grid min-w-0 grid-cols-1 gap-2 md:grid-cols-3", children: layers.map((layer, index) => {
                    const label = ROLE_LAYER_LABELS[layer.id];
                    const isActive = activeLayer === layer.id;
                    const isSuggested = suggestedLayer === layer.id;
                    return (_jsxs("div", { className: "min-w-0", children: [_jsxs("button", { type: "button", onClick: () => onSelect(layer.id), "data-testid": `factory-role-layer-${layer.id}`, "aria-pressed": isActive, className: cn('group flex h-full min-h-[86px] w-full cursor-pointer flex-col rounded-lg border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/70', isActive ? layer.tone.active : layer.tone.idle), children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("div", { className: cn('rounded-md border border-white/10 bg-white/10 p-1.5', layer.tone.text), children: layer.icon }), _jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "font-mono text-[10px] text-slate-500", children: layer.order }), _jsx("span", { className: cn('truncate text-sm font-semibold', layer.tone.text), children: layer.title })] }), _jsx("div", { className: "truncate text-[11px] text-slate-500", children: layer.subtitle })] })] }), _jsx("span", { className: cn('shrink-0 rounded-md border px-1.5 py-0.5 text-[10px]', roleStatusTone(layer.status)), children: roleStatusLabel(layer.status) })] }), _jsxs("div", { className: "mt-3 flex flex-1 items-end justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-[11px] text-slate-400", children: layer.detail }), _jsxs("div", { className: "mt-1 flex items-center gap-2 font-mono text-[11px] text-slate-500", children: [_jsx("span", { children: label.short }), _jsx("span", { className: "h-1 w-1 rounded-full bg-slate-700" }), _jsx("span", { className: "truncate", children: layer.metric })] }), _jsx("div", { className: "mt-1 truncate text-[10px] text-slate-600", children: label.route })] }), isSuggested ? (_jsx("span", { className: "shrink-0 rounded-md border border-emerald-500/25 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-200", children: "\u5F53\u524D\u9636\u6BB5" })) : null] }), _jsx("div", { className: "mt-3 h-1.5 overflow-hidden rounded-full bg-slate-900", children: _jsx("div", { className: cn('h-full rounded-full bg-gradient-to-r transition-all duration-500', layer.tone.progress), style: { width: `${layer.progress}%` } }) })] }), index < layers.length - 1 ? (_jsx(ChevronRight, { className: "mx-auto my-1 h-4 w-4 rotate-90 text-slate-600 md:hidden", "aria-hidden": "true" })) : null] }, layer.id));
                }) })] }));
}
function FactoryPmLayer({ workspace, tasks, executionLogs, roleStatus, factoryPhase, blueprintCoverage, }) {
    const stats = buildContractStats(tasks);
    const completeness = contractCompleteness(stats);
    const visibleTasks = tasks.slice(0, 12);
    const recentLogs = latestLogRows(executionLogs, 5);
    const handoffReady = stats.total > 0 && stats.blocked === 0 && stats.withGoal === stats.total
        && stats.withScope === stats.total && stats.withSteps === stats.total && stats.withAcceptance === stats.total;
    const status = roleStatus?.status || (stats.total > 0 ? 'ready' : 'waiting');
    const contractGaps = [
        { label: '目标', value: stats.withGoal },
        { label: '范围', value: stats.withScope },
        { label: '步骤', value: stats.withSteps },
        { label: '验收', value: stats.withAcceptance },
    ];
    const workspaceDisplay = workspaceLabel(workspace);
    return (_jsxs("div", { "data-testid": "factory-pm-layer", className: "flex h-full flex-col overflow-hidden bg-[#070b14]", children: [_jsxs("header", { className: "flex h-14 shrink-0 items-center justify-between border-b border-amber-500/20 bg-slate-950/80 px-4", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-3", children: [_jsx("div", { className: "flex h-8 w-8 items-center justify-center rounded-lg border border-amber-400/30 bg-amber-500/10 text-amber-100", children: _jsx(ClipboardList, { className: "h-4 w-4" }) }), _jsxs("div", { className: "min-w-0", children: [_jsx("h2", { className: "truncate text-sm font-semibold text-amber-100", children: "PM \u4EFB\u52A1\u5408\u540C\u5C42" }), _jsx("p", { className: "truncate text-[10px] uppercase tracking-wider text-amber-400/70", children: "Contract Planning Layer" })] })] }), _jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("span", { className: cn('rounded-md border px-2 py-1 text-[10px] tracking-wider', roleStatusTone(status)), children: roleStatusLabel(status) }), _jsx("span", { "data-testid": "factory-pm-workspace-label", className: "max-w-[180px] truncate rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-slate-400", title: workspace || workspaceDisplay, children: workspaceDisplay })] })] }), _jsxs("div", { className: "grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden p-4 2xl:grid-cols-[minmax(0,1fr)_320px]", children: [_jsxs("section", { className: "flex min-h-0 flex-col overflow-hidden rounded-lg border border-amber-500/[0.15] bg-white/[0.03]", children: [_jsx("div", { className: "shrink-0 border-b border-white/10 px-4 py-3", children: _jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("h3", { className: "truncate text-sm font-semibold text-slate-100", children: "PM task list evidence" }), _jsx("p", { className: "mt-1 text-xs text-slate-500", children: "\u53EA\u5C55\u793A Factory \u9700\u8981\u4EA4\u63A5\u7684\u5408\u540C\u5B57\u6BB5\uFF0C\u907F\u514D\u5D4C\u5165\u5B8C\u6574 PM \u63A7\u5236\u53F0\u3002" })] }), _jsxs("span", { className: "shrink-0 rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-100", children: [stats.total, " \u4E2A\u4EFB\u52A1"] })] }) }), _jsx("div", { className: "min-h-0 flex-1 overflow-auto p-3", children: visibleTasks.length > 0 ? (_jsx("div", { className: "space-y-2", children: visibleTasks.map((task) => {
                                        const goal = readTaskString(task, ['goal', 'summary', 'description']);
                                        const scopeItems = taskScopeItems(task);
                                        const stepItems = taskStepItems(task);
                                        const acceptanceItems = taskAcceptanceItems(task);
                                        const checks = [
                                            { label: '目标', ok: Boolean(goal) },
                                            { label: '范围', ok: scopeItems.length > 0 },
                                            { label: '步骤', ok: stepItems.length > 0 },
                                            { label: '验收', ok: acceptanceItems.length > 0 },
                                        ];
                                        return (_jsxs("article", { "data-testid": "factory-pm-task-item", className: "rounded-lg border border-white/10 bg-slate-950/45 px-3 py-2.5", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("span", { className: "font-mono text-[10px] text-slate-500", children: task.id || 'task' }), _jsx("span", { className: "truncate text-sm font-medium text-slate-100", children: taskTitle(task) })] }), _jsx("p", { className: "mt-1 line-clamp-2 text-xs leading-5 text-slate-400", children: goal || taskGoal(task) })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-1.5", children: [_jsx("span", { className: "rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: taskPriorityLabel(task) }), _jsx("span", { className: cn('rounded-md border px-1.5 py-0.5 text-[10px]', taskStatusTone(task)), children: taskDisplayStatus(task) })] })] }), _jsx("div", { className: "mt-2 flex flex-wrap gap-1.5", children: checks.map((check) => (_jsxs("span", { className: cn('inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px]', check.ok
                                                            ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
                                                            : 'border-slate-700 bg-slate-900/70 text-slate-500'), children: [_jsx(CheckCircle2, { className: "h-3 w-3" }), check.label] }, check.label))) })] }, task.id || taskTitle(task)));
                                    }) })) : (_jsx("div", { className: "flex h-full min-h-[260px] items-center justify-center rounded-lg border border-dashed border-white/10 bg-slate-950/35 text-center", children: _jsxs("div", { children: [_jsx(ClipboardList, { className: "mx-auto h-8 w-8 text-slate-600" }), _jsx("p", { className: "mt-3 text-sm text-slate-400", children: "\u6682\u65E0 PM \u5408\u540C\u4EFB\u52A1" }), _jsx("p", { className: "mt-1 text-xs text-slate-600", children: "\u7B49\u5F85 PM \u751F\u6210\u53EF\u4EA4\u63A5\u4EFB\u52A1\u5408\u540C\u3002" })] }) })) })] }), _jsxs("aside", { className: "grid min-h-0 grid-cols-1 gap-3 overflow-auto lg:grid-cols-3 2xl:flex 2xl:flex-col", children: [_jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(Route, { className: "h-3.5 w-3.5 text-amber-300" }), "\u4EA4\u63A5\u95E8\u7981"] }), _jsxs("div", { className: "space-y-2", children: [_jsx(MetricRow, { label: "\u5408\u540C\u5B8C\u6574\u5EA6", value: `${completeness}%`, tone: completeness >= 80 ? 'text-emerald-200' : 'text-amber-200' }), _jsx(MetricRow, { label: "\u5F85\u529E\u4EFB\u52A1", value: String(stats.pending), tone: "text-amber-200" }), _jsx(MetricRow, { label: "\u963B\u585E\u4EFB\u52A1", value: String(stats.blocked), tone: stats.blocked > 0 ? 'text-red-200' : 'text-emerald-200' }), _jsx(MetricRow, { label: "CE \u5F85\u84DD\u56FE", value: String(blueprintCoverage.missing.length), tone: blueprintCoverage.missing.length > 0 ? 'text-amber-200' : 'text-emerald-200' }), _jsx(MetricRow, { label: "Factory \u9636\u6BB5", value: PHASE_CONFIG[factoryPhase].label, tone: "text-slate-300" }), _jsx(MetricRow, { label: "\u53EF\u4EA4\u63A5", value: handoffReady ? '就绪' : '待补齐', tone: handoffReady ? 'text-emerald-200' : 'text-amber-200' })] })] }), _jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(BadgeCheck, { className: "h-3.5 w-3.5 text-emerald-300" }), "\u5408\u540C\u5B57\u6BB5\u8986\u76D6"] }), _jsx("div", { className: "space-y-2", children: contractGaps.map((item) => (_jsxs("div", { children: [_jsxs("div", { className: "mb-1 flex justify-between text-[11px] text-slate-400", children: [_jsx("span", { children: item.label }), _jsxs("span", { children: [item.value, "/", stats.total] })] }), _jsx("div", { className: "h-1.5 overflow-hidden rounded-full bg-slate-900", children: _jsx("div", { className: "h-full rounded-full bg-gradient-to-r from-amber-500 to-emerald-300", style: { width: `${stats.total > 0 ? percent((item.value / stats.total) * 100) : 0}%` } }) })] }, item.label))) })] }), _jsxs("section", { className: "min-h-0 rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(Activity, { className: "h-3.5 w-3.5 text-cyan-300" }), "\u6700\u8FD1 PM \u8BC1\u636E"] }), _jsx("div", { className: "space-y-2", children: recentLogs.length > 0 ? (recentLogs.map((log, index) => (_jsxs("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-2", children: [_jsx("div", { className: "truncate text-xs font-medium text-slate-200", children: log.title || log.source || 'PM 事件' }), _jsx("div", { className: "mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500", children: log.message })] }, `pm-log-${log.id || 'no-id'}-${index}`)))) : (_jsx("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs text-slate-500", children: "\u6682\u65E0 PM \u8FD0\u884C\u8BC1\u636E\u3002" })) })] })] })] })] }));
}
function FactoryDirectorLayer({ workspace, tasks, fileEditEvents, executionLogs, roleStatus, factoryPhase, blueprintCoverage, }) {
    const stats = buildDeliveryStats(tasks);
    const [selectedTaskId, setSelectedTaskId] = useState(() => tasks[0]?.id || '');
    const selectedTask = tasks.find((task) => task.id === selectedTaskId) || tasks[0] || null;
    const recentLogs = latestLogRows(executionLogs, 4);
    const eventTimestamp = (value) => {
        const timestamp = Date.parse(String(value || ''));
        return Number.isFinite(timestamp) ? timestamp : 0;
    };
    const recentFileEvents = [...fileEditEvents]
        .sort((a, b) => eventTimestamp(b.timestamp) - eventTimestamp(a.timestamp))
        .slice(0, 6);
    const status = roleStatus?.status || (stats.running > 0 ? 'running' : stats.total > 0 ? 'ready' : 'waiting');
    const deliveryReady = blueprintCoverage.required === 0 || blueprintCoverage.missing.length === 0;
    const workspaceDisplay = workspaceLabel(workspace);
    useEffect(() => {
        if (!tasks.some((task) => task.id === selectedTaskId)) {
            setSelectedTaskId(tasks[0]?.id || '');
        }
    }, [selectedTaskId, tasks]);
    return (_jsxs("div", { "data-testid": "director-workspace", className: "flex h-full flex-col overflow-hidden bg-[#070b14]", children: [_jsxs("header", { className: "flex h-14 shrink-0 items-center justify-between border-b border-indigo-500/20 bg-slate-950/80 px-4", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-3", children: [_jsx("div", { className: "flex h-8 w-8 items-center justify-center rounded-lg border border-indigo-400/30 bg-indigo-500/10 text-indigo-100", children: _jsx(Hammer, { className: "h-4 w-4" }) }), _jsxs("div", { className: "min-w-0", children: [_jsx("h2", { className: "truncate text-sm font-semibold text-indigo-100", children: "Director \u6267\u884C\u4EA4\u4ED8\u5C42" }), _jsx("p", { className: "truncate text-[10px] uppercase tracking-wider text-indigo-400/70", children: "Delivery Execution Layer" })] })] }), _jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("span", { className: cn('rounded-md border px-2 py-1 text-[10px] tracking-wider', roleStatusTone(status)), children: roleStatusLabel(status) }), _jsx("span", { "data-testid": "factory-director-workspace-label", className: "max-w-[180px] truncate rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-slate-400", title: workspace || workspaceDisplay, children: workspaceDisplay })] })] }), _jsxs("div", { className: "grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden p-4 2xl:grid-cols-[300px_minmax(0,1fr)_300px]", children: [_jsxs("aside", { className: "flex min-h-0 flex-col overflow-hidden rounded-lg border border-indigo-500/[0.15] bg-white/[0.03]", children: [_jsx("div", { className: "shrink-0 border-b border-white/10 px-3 py-3", children: _jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("h3", { className: "truncate text-sm font-semibold text-slate-100", children: "Director \u4EFB\u52A1\u961F\u5217" }), _jsx("p", { className: "mt-1 text-xs text-slate-500", children: "\u53EA\u8BFB\u67E5\u770B Factory \u5206\u6D3E\u961F\u5217\u3002" })] }), _jsx("button", { type: "button", "data-testid": "director-workspace-bulk-execute", disabled: true, title: "\u5DE5\u5382\u6A21\u5F0F\u4E0B\u7531 Factory \u7F16\u6392 Director", className: "shrink-0 rounded-md border border-slate-700 bg-slate-900/80 px-2 py-1 text-[10px] text-slate-500", children: "\u5168\u90E8\u6267\u884C" })] }) }), _jsx("div", { className: "min-h-0 flex-1 overflow-auto p-3", children: tasks.length > 0 ? (_jsx("div", { className: "space-y-2", children: tasks.slice(0, 18).map((task) => {
                                        const isSelected = selectedTask?.id === task.id;
                                        return (_jsxs("button", { type: "button", "data-testid": "director-task-item", onClick: () => setSelectedTaskId(task.id), className: cn('w-full rounded-lg border px-3 py-2.5 text-left transition-colors', isSelected
                                                ? 'border-indigo-400/45 bg-indigo-500/10'
                                                : 'border-white/10 bg-slate-950/45 hover:border-indigo-400/30'), children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-xs font-medium text-slate-100", children: taskTitle(task) }), _jsx("span", { className: cn('shrink-0 rounded-md border px-1.5 py-0.5 text-[10px]', taskStatusTone(task)), children: taskDisplayStatus(task) })] }), _jsx("div", { className: "mt-1 truncate font-mono text-[10px] text-slate-500", children: task.id || 'task-id n/a' })] }, task.id || taskTitle(task)));
                                    }) })) : (_jsx("div", { className: "flex h-full min-h-[260px] items-center justify-center rounded-lg border border-dashed border-white/10 bg-slate-950/35 text-center", children: _jsxs("div", { children: [_jsx(Hammer, { className: "mx-auto h-8 w-8 text-slate-600" }), _jsx("p", { className: "mt-3 text-sm text-slate-400", children: "\u6682\u65E0 Director \u961F\u5217" }), _jsx("p", { className: "mt-1 text-xs text-slate-600", children: "\u7B49\u5F85\u84DD\u56FE\u4EA4\u63A5\u540E\u751F\u6210\u6267\u884C\u4EFB\u52A1\u3002" })] }) })) })] }), _jsxs("section", { className: "flex min-h-0 flex-col overflow-hidden rounded-lg border border-indigo-500/[0.15] bg-white/[0.03]", children: [_jsx("div", { className: "shrink-0 border-b border-white/10 px-4 py-3", children: _jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("h3", { className: "truncate text-sm font-semibold text-slate-100", children: "\u6267\u884C\u4EA4\u4ED8\u8BE6\u60C5" }), _jsx("p", { className: "mt-1 text-xs text-slate-500", children: "\u6587\u4EF6\u53D8\u66F4\u3001\u547D\u4EE4\u548C\u9A8C\u8BC1\u7531 Factory \u7EDF\u4E00\u8C03\u5EA6\u3002" })] }), _jsx("span", { className: cn('shrink-0 rounded-md border px-2 py-1 text-[10px]', deliveryReady
                                                ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
                                                : 'border-amber-500/25 bg-amber-500/10 text-amber-200'), children: deliveryReady ? '可接收' : '待蓝图' })] }) }), _jsxs("div", { className: "min-h-0 flex-1 overflow-auto p-4", children: [_jsx("div", { "data-testid": "director-execution-guard", className: "mb-4 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-100", children: "\u5DE5\u5382\u6A21\u5F0F\u4E0B\u7531 Factory \u7F16\u6392 Director\uFF0C\u4E0D\u80FD\u5728\u5D4C\u5165\u5C42\u76F4\u63A5\u542F\u52A8\u3002" }), selectedTask ? (_jsxs("article", { "data-testid": "director-task-detail", className: "space-y-4", children: [_jsxs("section", { className: "rounded-lg border border-white/10 bg-slate-950/45 p-3", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-3", children: [_jsx("h4", { className: "truncate text-sm font-semibold text-slate-100", children: taskTitle(selectedTask) }), _jsx("span", { className: cn('rounded-md border px-1.5 py-0.5 text-[10px]', taskStatusTone(selectedTask)), children: taskDisplayStatus(selectedTask) })] }), _jsx("p", { className: "text-xs leading-5 text-slate-400", children: taskGoal(selectedTask) })] }), _jsxs("section", { className: "grid grid-cols-1 gap-3 lg:grid-cols-2", children: [_jsx(DetailList, { title: "\u6267\u884C\u6B65\u9AA4", items: taskStepItems(selectedTask), empty: "\u7B49\u5F85 PM \u5408\u540C\u8865\u5145\u6B65\u9AA4" }), _jsx(DetailList, { title: "\u9A8C\u6536\u6807\u51C6", items: taskAcceptanceItems(selectedTask), empty: "\u7B49\u5F85 PM \u5408\u540C\u8865\u5145\u9A8C\u6536" })] }), _jsxs("section", { className: "rounded-lg border border-white/10 bg-slate-950/45 p-3", children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(FileText, { className: "h-3.5 w-3.5 text-indigo-300" }), "\u76EE\u6807\u6587\u4EF6"] }), _jsx("div", { className: "flex flex-wrap gap-1.5", children: taskScopeItems(selectedTask).length > 0 ? (taskScopeItems(selectedTask).slice(0, 10).map((item) => (_jsx("span", { className: "rounded-md border border-white/10 bg-white/5 px-2 py-1 text-[10px] text-slate-300", children: item }, item)))) : (_jsx("span", { className: "text-xs text-slate-500", children: "\u6682\u65E0\u76EE\u6807\u6587\u4EF6\u5B57\u6BB5" })) })] })] })) : (_jsx("div", { className: "flex h-full min-h-[320px] items-center justify-center text-center text-slate-500", children: _jsxs("div", { children: [_jsx(FileText, { className: "mx-auto h-8 w-8 text-slate-600" }), _jsx("p", { className: "mt-3 text-sm", children: "\u9009\u62E9\u4EFB\u52A1\u67E5\u770B\u4EA4\u4ED8\u8BE6\u60C5" })] }) }))] })] }), _jsxs("aside", { className: "grid min-h-0 grid-cols-1 gap-3 overflow-auto lg:grid-cols-3 2xl:flex 2xl:flex-col", children: [_jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(Route, { className: "h-3.5 w-3.5 text-indigo-300" }), "\u4EA4\u4ED8\u72B6\u6001"] }), _jsxs("div", { className: "space-y-2", children: [_jsx(MetricRow, { label: "\u961F\u5217\u4EFB\u52A1", value: String(stats.total), tone: "text-indigo-200" }), _jsx(MetricRow, { label: "\u5DF2\u9886\u53D6", value: String(stats.claimed), tone: "text-slate-300" }), _jsx(MetricRow, { label: "\u6267\u884C\u4E2D", value: String(stats.running), tone: "text-cyan-200" }), _jsx(MetricRow, { label: "\u963B\u585E", value: String(stats.blocked), tone: stats.blocked > 0 ? 'text-red-200' : 'text-emerald-200' }), _jsx(MetricRow, { label: "\u5B8C\u6210", value: String(stats.completed), tone: "text-emerald-200" }), _jsx(MetricRow, { label: "Factory \u9636\u6BB5", value: PHASE_CONFIG[factoryPhase].label, tone: "text-slate-300" })] })] }), _jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(FileCode, { className: "h-3.5 w-3.5 text-emerald-300" }), "\u5B9E\u65F6\u6587\u4EF6\u6D3B\u52A8"] }), _jsx("div", { className: "space-y-2", children: recentFileEvents.length > 0 ? (recentFileEvents.map((event, index) => (_jsxs("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2 text-[10px]", children: [_jsx("span", { className: "uppercase text-emerald-300", children: event.operation }), _jsxs("span", { className: "text-slate-600", children: [event.addedLines || 0, "+ / ", event.deletedLines || 0, "-"] })] }), _jsx("div", { className: "mt-1 truncate text-xs text-slate-300", children: event.filePath })] }, `file-${event.id || event.filePath || 'no-id'}-${index}`)))) : (_jsx("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs text-slate-500", children: "\u6682\u65E0\u6587\u4EF6\u53D8\u66F4\u4E8B\u4EF6\u3002" })) })] }), _jsxs("section", { className: "min-h-0 rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(Activity, { className: "h-3.5 w-3.5 text-cyan-300" }), "\u6700\u8FD1\u6267\u884C\u8BC1\u636E"] }), _jsx("div", { className: "space-y-2", children: recentLogs.length > 0 ? (recentLogs.map((log, index) => (_jsxs("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-2", children: [_jsx("div", { className: "truncate text-xs font-medium text-slate-200", children: log.title || log.source || 'Director 事件' }), _jsx("div", { className: "mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500", children: log.message })] }, `director-log-${log.id || 'no-id'}-${index}`)))) : (_jsx("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs text-slate-500", children: "\u6682\u65E0\u6267\u884C\u8BC1\u636E\u3002" })) })] })] })] })] }));
}
function DetailList({ title, items, empty }) {
    return (_jsxs("section", { className: "rounded-lg border border-white/10 bg-slate-950/45 p-3", children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(BadgeCheck, { className: "h-3.5 w-3.5 text-cyan-300" }), title] }), items.length > 0 ? (_jsx("ul", { className: "space-y-1.5", children: items.slice(0, 6).map((item) => (_jsxs("li", { className: "flex gap-2 text-xs leading-5 text-slate-400", children: [_jsx(CheckCircle2, { className: "mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300" }), _jsx("span", { className: "min-w-0 break-words", children: item })] }, item))) })) : (_jsx("p", { className: "text-xs text-slate-500", children: empty }))] }));
}
function roleLayerDisplayName(layer) {
    if (layer === 'chief_engineer')
        return 'Chief Engineer';
    if (layer === 'director')
        return 'Director';
    return 'PM';
}
function FactoryChiefEngineerLayer({ workspace, blueprintEvidence, reviewArtifacts, blueprintCoverage, roleStatus, currentRun, }) {
    const candidateTasks = blueprintCoverage.missing.slice(0, 5);
    const status = roleStatus?.status || (blueprintCoverage.missing.length > 0 ? 'waiting' : blueprintEvidence.length > 0 ? 'ready' : 'waiting');
    const directorStageActive = normalizeToken(currentRun?.current_stage).includes('director');
    const handoffReady = blueprintCoverage.required > 0 && blueprintCoverage.missing.length === 0;
    const handoffLabel = blueprintCoverage.missing.length > 0
        ? '缺证据'
        : handoffReady
            ? '就绪'
            : directorStageActive
                ? '已进入 Director'
                : '等待';
    const handoffTone = blueprintCoverage.missing.length > 0
        ? 'text-red-200'
        : handoffReady || directorStageActive
            ? 'text-emerald-200'
            : 'text-amber-200';
    const workspaceDisplay = workspaceLabel(workspace);
    return (_jsxs("div", { "data-testid": "factory-chief-layer", className: "flex h-full flex-col overflow-hidden bg-[#070b14]", children: [_jsxs("header", { className: "flex h-14 shrink-0 items-center justify-between border-b border-cyan-500/20 bg-slate-950/80 px-4", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: "flex h-8 w-8 items-center justify-center rounded-lg border border-cyan-400/30 bg-cyan-500/10 text-cyan-100", children: _jsx(Brain, { className: "h-4 w-4" }) }), _jsxs("div", { children: [_jsx("h2", { className: "text-sm font-semibold text-cyan-100", children: "Chief Engineer" }), _jsx("p", { className: "text-[10px] uppercase tracking-wider text-cyan-400/70", children: "Blueprint Handoff Layer" })] })] }), _jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("span", { className: cn('rounded-md border px-2 py-1 text-[10px] tracking-wider', roleStatusTone(status)), children: roleStatusLabel(status) }), _jsx("span", { "data-testid": "factory-chief-workspace-label", className: "max-w-[180px] truncate rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-slate-400", title: workspace || workspaceDisplay, children: workspaceDisplay })] })] }), _jsxs("div", { className: "grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden p-4 2xl:grid-cols-[minmax(0,1fr)_340px]", children: [_jsxs("section", { className: "min-h-0 overflow-auto rounded-lg border border-cyan-500/[0.15] bg-white/[0.035]", children: [_jsx("div", { className: "border-b border-white/10 px-4 py-3", children: _jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsxs("div", { children: [_jsx("h3", { className: "text-sm font-semibold text-slate-100", children: "\u65BD\u5DE5\u84DD\u56FE\u8BC1\u636E" }), _jsx("p", { className: "mt-1 text-xs text-slate-500", children: "\u4EC5\u5C55\u793A\u4EFB\u52A1\u5408\u540C\u5B57\u6BB5\u548C Factory \u8FD0\u884C\u65F6\u84DD\u56FE\u4EA7\u7269\u3002" })] }), _jsxs("span", { className: "rounded-md border border-cyan-500/25 bg-cyan-500/10 px-2 py-1 text-[10px] text-cyan-100", children: [blueprintEvidence.length, " \u6761\u84DD\u56FE"] })] }) }), _jsx("div", { className: "space-y-3 p-4", children: blueprintEvidence.length > 0 ? (blueprintEvidence.map((evidence) => {
                                    return (_jsx("article", { className: "rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-3", title: evidence.path || evidence.id, children: _jsx("div", { className: "flex items-start justify-between gap-3", children: _jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium text-cyan-100", children: [_jsx(FileText, { className: "h-4 w-4 shrink-0" }), _jsx("span", { className: "truncate", children: evidence.title })] }), evidence.summary ? (_jsx("p", { className: "mt-1 line-clamp-2 text-xs leading-5 text-slate-400", children: evidence.summary })) : null] }) }) }, `${evidence.source}-${evidence.id}-${evidence.path}`));
                                })) : (_jsxs("div", { className: "rounded-lg border border-amber-500/25 bg-amber-500/10 p-4 text-sm text-amber-100", children: [_jsxs("div", { className: "flex items-center gap-2 font-medium", children: [_jsx(AlertCircle, { className: "h-4 w-4" }), "\u6682\u65E0 Chief Engineer \u84DD\u56FE\u8BC1\u636E"] }), _jsx("p", { className: "mt-2 text-xs leading-5 text-amber-100/75", children: "\u7B49\u5F85 PM/CE \u94FE\u8DEF\u5199\u5165 `blueprint_id`\u3001`blueprint_path` \u6216 `runtime_blueprint_path` \u540E\uFF0C\u518D\u5F00\u653E Director \u4EA4\u63A5\u5224\u65AD\u3002" })] })) })] }), _jsxs("aside", { className: "grid min-h-0 grid-cols-1 gap-3 overflow-auto lg:grid-cols-2 2xl:flex 2xl:flex-col", children: [_jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(Route, { className: "h-3.5 w-3.5 text-cyan-300" }), "\u4EA4\u63A5\u72B6\u6001"] }), _jsxs("div", { className: "space-y-2", children: [_jsx(MetricRow, { label: "\u4EFB\u52A1\u8986\u76D6", value: `${blueprintCoverage.covered}/${blueprintCoverage.required}`, tone: "text-cyan-200" }), _jsx(MetricRow, { label: "\u84DD\u56FE\u8BC1\u636E", value: String(blueprintEvidence.length), tone: "text-cyan-200" }), _jsx(MetricRow, { label: "\u5BA1\u67E5\u56DE\u6267", value: String(reviewArtifacts.length), tone: "text-slate-300" }), _jsx(MetricRow, { label: "\u5F85\u84DD\u56FE\u4EFB\u52A1", value: String(blueprintCoverage.missing.length), tone: blueprintCoverage.missing.length > 0 ? 'text-red-200' : 'text-emerald-200' }), _jsx(MetricRow, { label: "\u5DF2\u5B8C\u6210\u4EFB\u52A1", value: String(blueprintCoverage.completed), tone: "text-slate-300" }), _jsx(MetricRow, { label: "Director \u4EA4\u63A5", value: handoffLabel, tone: handoffTone }), _jsx(MetricRow, { label: "Factory \u9636\u6BB5", value: currentRun?.current_stage || 'n/a', tone: "text-slate-300" })] })] }), _jsxs("section", { className: "min-h-0 rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(BadgeCheck, { className: "h-3.5 w-3.5 text-emerald-300" }), "\u5BA1\u67E5\u56DE\u6267"] }), _jsx("div", { className: "space-y-2", children: reviewArtifacts.length > 0 ? (reviewArtifacts.map((artifact) => (_jsxs("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-2", children: [_jsx("div", { className: "truncate text-xs font-medium text-slate-200", children: artifact.title }), _jsx("div", { className: "mt-1 truncate text-[10px] text-slate-500", title: artifact.path, children: artifact.path })] }, `${artifact.source}-${artifact.id}-${artifact.path}`)))) : (_jsx("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs text-slate-400", children: "\u6682\u65E0 Factory Chief Engineer \u5BA1\u67E5\u56DE\u6267\u3002" })) })] }), _jsxs("section", { className: "min-h-0 rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(ClipboardList, { className: "h-3.5 w-3.5 text-amber-300" }), "\u5F85\u84DD\u56FE\u4EFB\u52A1"] }), _jsx("div", { className: "space-y-2", children: candidateTasks.length > 0 ? (candidateTasks.map((task, index) => (_jsxs("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-2", children: [_jsx("div", { className: "truncate text-xs font-medium text-slate-200", children: task.title || task.id }), _jsx("div", { className: "mt-1 truncate text-[10px] text-slate-500", children: task.goal || task.summary || task.description || '等待蓝图输入' })] }, `candidate-${task.id || taskTitle(task) || 'task'}-${index}`)))) : (_jsx("div", { className: "rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-2 text-xs text-emerald-100", children: "\u5F53\u524D\u4EFB\u52A1\u5747\u5DF2\u5177\u5907\u84DD\u56FE\u5B57\u6BB5\u6216\u6682\u65E0 PM \u4EFB\u52A1\u3002" })) })] })] })] })] }));
}
function FactoryOperationsRail({ currentRun, guardedFactoryState, factoryPhase, workspacePhase, activeLayer, activityLogs, llmStreamEvents, processStreamEvents, gateResults, deliveryArtifacts, summaryMarkdown, summaryRows, artifactErrorMessage, isArtifactsLoading, isRunning, }) {
    const sourceEvidence = buildRunSourceEvidence(currentRun);
    const failureBrief = buildFactoryFailureBrief(currentRun);
    return (_jsxs("aside", { className: "flex h-full min-h-0 min-w-0 flex-col overflow-hidden border-t border-white/10 bg-slate-950/80 xl:border-l xl:border-t-0", "data-testid": "factory-operations-rail", children: [_jsxs("section", { className: "shrink-0 border-b border-white/10 p-3", children: [_jsxs("div", { className: "mb-3 flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(Activity, { className: "h-3.5 w-3.5 text-emerald-300" }), "\u8FD0\u884C\u89C2\u6D4B"] }), _jsx("span", { className: cn('rounded-md border px-1.5 py-0.5 text-[10px] uppercase', roleStatusTone(guardedFactoryState.status)), children: roleStatusLabel(guardedFactoryState.status) })] }), _jsxs("div", { className: "grid grid-cols-2 gap-2", children: [_jsx(MiniMetric, { label: "\u89D2\u8272\u5C42", value: roleLayerDisplayName(activeLayer) }), _jsx(MiniMetric, { label: "\u9636\u6BB5", value: PHASE_CONFIG[factoryPhase].label }), _jsx(MiniMetric, { label: "\u8FD0\u884CID", value: currentRun?.run_id || 'n/a' }), _jsx(MiniMetric, { label: "\u8FDB\u5EA6", value: `${guardedFactoryState.progress}%` })] }), failureBrief ? _jsx(FactoryFailureBriefPanel, { brief: failureBrief }) : null, sourceEvidence.length > 0 ? (_jsxs("div", { "data-testid": "factory-source-evidence", className: "mt-3 rounded-lg border border-emerald-500/[0.15] bg-emerald-500/[0.04] p-2", children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-emerald-300", children: [_jsx(Route, { className: "h-3.5 w-3.5" }), "\u6765\u6E90\u8BC1\u636E"] }), _jsx("div", { className: "space-y-1.5", children: sourceEvidence.map((row) => (_jsxs("div", { className: "grid grid-cols-[48px_minmax(0,1fr)] gap-2 text-[11px]", children: [_jsx("span", { className: "text-slate-500", children: row.label }), _jsx("span", { className: cn('min-w-0 break-words font-medium leading-4', row.label === '指令' ? 'line-clamp-3' : 'truncate', row.tone), title: row.value, children: row.value })] }, `${row.label}-${row.value}`))) })] })) : null] }), _jsx("section", { className: "min-h-[260px] flex-[1.05] overflow-hidden border-b border-white/10", children: _jsx(RealtimeActivityPanel, { executionLogs: activityLogs, llmStreamEvents: llmStreamEvents, processStreamEvents: processStreamEvents, currentPhase: workspacePhase, isRunning: isRunning, role: activeLayer }) }), _jsx("section", { className: "min-h-0 flex-1 overflow-y-auto p-3", children: _jsx(FactoryAuditEvidencePanel, { gateResults: gateResults, guardedFactoryState: guardedFactoryState, deliveryArtifacts: deliveryArtifacts, summaryMarkdown: summaryMarkdown, summaryRows: summaryRows, artifactErrorMessage: artifactErrorMessage, isArtifactsLoading: isArtifactsLoading, failure: currentRun?.failure }) })] }));
}
function FactoryFailureBriefPanel({ brief }) {
    return (_jsxs("section", { "data-testid": "factory-failure-brief", className: "mt-3 rounded-lg border border-red-500/30 bg-red-500/10 p-2.5", "aria-label": "Factory failure root cause", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-semibold text-red-200", children: [_jsx(AlertCircle, { className: "h-3.5 w-3.5 shrink-0" }), _jsx("span", { children: brief.headline })] }), _jsx("p", { className: "mt-1 line-clamp-3 text-[11px] leading-4 text-red-100/80", children: brief.detail })] }), _jsxs("span", { className: "shrink-0 rounded-md border border-red-400/25 bg-red-950/45 px-1.5 py-0.5 text-[10px] text-red-200", children: ["\u6839\u56E0 ", brief.rootRole] })] }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1.5", children: [_jsx("span", { className: "rounded-md border border-white/10 bg-black/20 px-1.5 py-0.5 font-mono text-[10px] text-red-200", children: brief.code }), _jsx("span", { className: cn('rounded-md border px-1.5 py-0.5 text-[10px]', brief.recoverable
                            ? 'border-amber-500/25 bg-amber-500/10 text-amber-200'
                            : 'border-slate-700 bg-slate-900/70 text-slate-400'), children: brief.recoverable ? '可重试' : '需先修复根因' }), brief.cascades.length > 0 ? (_jsxs("span", { className: "rounded-md border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200", children: [brief.cascades.length, " \u4E2A\u7EA7\u8054\u963B\u585E"] })) : null] }), brief.cascades.length > 0 ? (_jsx("div", { className: "mt-2 space-y-1", children: brief.cascades.slice(0, 3).map((cascade) => (_jsx("p", { className: "truncate text-[10px] text-red-100/65", title: cascade, children: cascade }, cascade))) })) : null] }));
}
function FactoryAuditEvidencePanel({ gateResults, guardedFactoryState, deliveryArtifacts, summaryMarkdown, summaryRows, artifactErrorMessage, isArtifactsLoading, failure, }) {
    const ledgerGuarded = hasRunLedgerGuard(guardedFactoryState);
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-emerald-300", children: [_jsx(ShieldCheck, { className: "h-4 w-4" }), _jsx("h3", { className: "text-xs font-semibold uppercase tracking-wider", children: "\u603B\u76D1\u5BA1\u8BA1 / \u4EA4\u4ED8\u8BC1\u636E" })] }), _jsxs("section", { children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-medium text-slate-300", children: [_jsx(BadgeCheck, { className: "h-3.5 w-3.5 text-cyan-300" }), _jsx("span", { children: "\u8D28\u91CF\u95E8" })] }), _jsx("div", { className: "space-y-2", children: ledgerGuarded ? (_jsxs("div", { "data-testid": "factory-run-ledger-gate", className: cn('rounded-lg border px-3 py-2 text-xs', guardedFactoryState.status === 'ledger_pending'
                                ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200'
                                : 'border-red-500/30 bg-red-500/10 text-red-200'), children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate font-medium", children: "Run Ledger" }), _jsx("span", { className: "shrink-0 uppercase", children: guardedFactoryState.status })] }), _jsx("p", { className: "mt-1 text-[10px] leading-relaxed opacity-80", children: guardedFactoryState.detail })] })) : gateResults.length > 0 ? (gateResults.map((gate) => (_jsxs("div", { className: cn('rounded-lg border px-3 py-2 text-xs', gateTone(gate)), children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate font-medium", children: gate.gate_name }), _jsx("span", { className: "shrink-0 uppercase", children: gate.status || 'n/a' })] }), _jsxs("div", { className: "mt-1 flex items-center justify-between gap-2 text-[10px] opacity-80", children: [_jsx("span", { children: gate.passed ? '通过' : '阻塞' }), typeof gate.score === 'number' && _jsxs("span", { children: ["score ", gate.score] })] }), gate.message && (_jsx("p", { className: "mt-1 text-[10px] leading-relaxed opacity-80", children: gate.message }))] }, gate.gate_name)))) : (_jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 px-3 py-2", children: [_jsx("p", { className: "text-xs text-slate-400", children: "\u6682\u65E0\u8D28\u91CF\u95E8\u7ED3\u679C" }), _jsx("p", { className: "mt-1 text-[10px] text-slate-600", children: "\u7B49\u5F85\u53EF\u5BA1\u8BA1\u95E8\u7981\u8BB0\u5F55" })] })) })] }), _jsxs("section", { children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-medium text-slate-300", children: [_jsx(PackageCheck, { className: "h-3.5 w-3.5 text-emerald-300" }), _jsx("span", { children: "\u4EA4\u4ED8\u4EA7\u7269" })] }), _jsxs("div", { className: "space-y-2", children: [isArtifactsLoading && (_jsxs("div", { className: "flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-400", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin text-emerald-300" }), _jsx("span", { children: "\u540C\u6B65\u8BC1\u636E\u4E2D" })] })), artifactErrorMessage && (_jsxs("div", { role: "alert", className: "rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200", children: ["\u4EA7\u7269\u540C\u6B65\u5931\u8D25: ", artifactErrorMessage] })), deliveryArtifacts.length > 0 ? (deliveryArtifacts.map((artifact) => (_jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 px-3 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx(FileText, { className: "h-3.5 w-3.5 shrink-0 text-slate-400" }), _jsx("span", { className: "truncate text-xs text-slate-200", children: artifact.name })] }), _jsx("span", { className: "shrink-0 text-[10px] text-slate-500", children: formatBytes(artifact.size) })] }), _jsx("p", { className: "mt-1 break-all text-[10px] leading-relaxed text-slate-500", children: artifact.path })] }, `${artifact.path}-${artifact.name}`)))) : (_jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 px-3 py-2", children: [_jsx("p", { className: "text-xs text-slate-400", children: "\u6682\u65E0\u4EA4\u4ED8\u4EA7\u7269" }), _jsx("p", { className: "mt-1 text-[10px] text-slate-600", children: "\u7B49\u5F85 Director \u8BC1\u636E\u6587\u4EF6" })] }))] })] }), _jsxs("section", { children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-medium text-slate-300", children: [_jsx(FileCode, { className: "h-3.5 w-3.5 text-purple-300" }), _jsx("span", { children: "\u4EA4\u4ED8\u6458\u8981" })] }), ledgerGuarded ? (_jsxs("div", { className: "rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2", children: [_jsx("p", { className: "text-xs text-cyan-100", children: "Run Ledger \u5C1A\u672A\u786E\u8BA4\u7EC8\u6001\u6458\u8981" }), _jsx("p", { className: "mt-1 text-[10px] leading-relaxed text-cyan-200/80", children: guardedFactoryState.detail })] })) : summaryMarkdown ? (_jsx("div", { className: "max-h-28 overflow-y-auto rounded-lg border border-white/10 bg-white/5 px-3 py-2", children: _jsx("p", { className: "whitespace-pre-line text-xs leading-relaxed text-slate-300", children: summaryMarkdown }) })) : summaryRows.length > 0 ? (_jsx("div", { className: "space-y-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs", children: summaryRows.map(([key, value]) => (_jsxs("div", { className: "flex justify-between gap-2", children: [_jsx("span", { className: "text-slate-500", children: key }), _jsx("span", { className: "min-w-0 truncate text-right text-slate-300", children: value })] }, key))) })) : (_jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 px-3 py-2", children: [_jsx("p", { className: "text-xs text-slate-400", children: "\u6682\u65E0\u4EA4\u4ED8\u6458\u8981" }), _jsx("p", { className: "mt-1 text-[10px] text-slate-600", children: "\u7B49\u5F85\u7EC8\u6001\u6458\u8981" })] }))] }), failure && (_jsxs("section", { role: "alert", className: "rounded-lg border border-red-500/30 bg-red-500/10 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-red-300", children: [_jsx(AlertCircle, { className: "h-4 w-4" }), _jsx("span", { className: "text-sm font-medium", children: "\u5931\u8D25\u4FE1\u606F" })] }), _jsx("p", { className: "mt-2 text-xs text-red-200", children: failure.detail }), failure.suggested_action && (_jsxs("p", { className: "mt-2 text-xs text-red-300/80", children: ["\u5EFA\u8BAE: ", failure.suggested_action] }))] }))] }));
}
function StatusChip({ label, value }) {
    return (_jsxs("div", { className: "rounded-md border border-white/10 bg-white/5 px-2 py-0.5", children: [_jsx("div", { className: "text-[9px] uppercase tracking-wider text-slate-500", children: label }), _jsx("div", { className: "max-w-16 truncate text-[11px] text-slate-200", children: value })] }));
}
function MiniMetric({ label, value }) {
    return (_jsxs("div", { className: "rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wider text-slate-500", children: label }), _jsx("div", { className: "truncate text-xs font-medium text-slate-200", children: value })] }));
}
function MetricRow({ label, value, tone }) {
    return (_jsxs("div", { className: "flex items-center justify-between gap-3 rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs", children: [_jsx("span", { className: "text-slate-500", children: label }), _jsx("span", { className: cn('min-w-0 truncate text-right font-medium', tone), children: value })] }));
}
