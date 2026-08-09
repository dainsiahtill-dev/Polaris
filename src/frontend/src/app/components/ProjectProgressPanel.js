import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from 'react';
import { Activity, AlertTriangle, ArrowRight, CheckCircle, ChevronDown, ChevronRight, Clock, ListChecks, Target } from 'lucide-react';
import { ProgressBar, CurrentTaskCard, TaskList, } from './ProjectProgressPanel/index';
import { PlanBoard } from './PlanBoard';
import { UI_TERMS } from '@/app/constants/uiTerminology';
import { StatusBadge } from '@/app/components/ui/badge';
import { PhaseIndicator, QualityGateCard, ExecutionLog } from './pm';
const toText = (value) => {
    if (typeof value === 'string')
        return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean')
        return String(value).trim();
    return '';
};
const isReadableTaskText = (value) => {
    const text = toText(value);
    if (!text)
        return false;
    return !/^\d+$/.test(text);
};
const readTaskDisplayText = (task, keys) => {
    if (!task)
        return '';
    const record = task;
    for (const key of keys) {
        const value = record[key];
        if (isReadableTaskText(value))
            return toText(value);
    }
    return '';
};
const clampText = (value, maxLen) => {
    const text = toText(value);
    if (!text || text.length <= maxLen)
        return text;
    return text.slice(0, Math.max(0, maxLen - 1)).trimEnd() + '...';
};
const isTaskDone = (task) => {
    if (task.completed || task.done)
        return true;
    const status = String(task.status || task.state || '').toLowerCase();
    return ['done', 'complete', 'completed', 'success', 'passed', 'pass', 'ok'].some((key) => status.includes(key));
};
const isTaskActive = (task) => {
    const status = String(task.status || task.state || '').toLowerCase();
    return ['in_progress', 'running', 'executing'].some((key) => status.includes(key));
};
const taskKey = (task) => toText(task.id) || readTaskDisplayText(task, ['subject', 'title', 'goal']);
const pickTaskSummary = (task) => readTaskDisplayText(task, ['summary', 'subject', 'title', 'goal']);
function normalizeRoleKey(value) {
    return value.toLowerCase().replace(/[^a-z0-9]/g, '');
}
function readEngineRole(engineStatus, aliases) {
    const roles = engineStatus?.roles;
    if (!roles)
        return null;
    const normalizedAliases = new Set(aliases.map(normalizeRoleKey));
    for (const [key, value] of Object.entries(roles)) {
        if (normalizedAliases.has(normalizeRoleKey(key))) {
            return value;
        }
    }
    return null;
}
function rolePipelineStatus(role, defaultStatus, realtimeOverride = '') {
    return realtimeOverride || toText(role?.status || (role?.running ? 'running' : '')) || defaultStatus;
}
function rolePipelineTask(role, defaultTask, realtimeOverride = '') {
    return realtimeOverride || toText(role?.task_title) || toText(role?.task_id) || defaultTask;
}
function isTerminalSuccessStatus(value) {
    const status = toText(value).toLowerCase();
    return ['completed', 'complete', 'success', 'passed', 'pass', 'ok'].includes(status);
}
function runLedgerGuardedPipelineStatus(status, ledgerEvidence) {
    if (!ledgerEvidence || ledgerEvidence.passed === true || !isTerminalSuccessStatus(status)) {
        return status;
    }
    if (ledgerEvidence.grade === 'run_ledger_failed' || ledgerEvidence.grade === 'run_ledger_unavailable') {
        return 'failed';
    }
    return 'blocked';
}
const QA_EVIDENCE_LABELS = {
    run_ledger_passed: 'run ledger passed',
    run_ledger_failed: 'run ledger failed',
    run_ledger_unavailable: 'run ledger unavailable',
    run_ledger_pending: 'run ledger pending',
    real_command_passed: 'real command passed',
    real_command_failed: 'real command failed',
    structural_fallback_passed: 'structural fallback',
    structural_fallback_failed: 'fallback failed',
    blocked_missing_dependencies: 'deps missing',
    not_run: 'not run',
    not_run_docs_only: 'docs only',
    qa_error: 'qa error',
    unknown: 'unknown',
};
function isRecord(value) {
    return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}
function normalizeQaEvidenceGrade(value) {
    const grade = String(value || '').trim().toLowerCase();
    if (grade === 'real_command_passed'
        || grade === 'real_command_failed'
        || grade === 'structural_fallback_passed'
        || grade === 'structural_fallback_failed'
        || grade === 'blocked_missing_dependencies'
        || grade === 'not_run'
        || grade === 'not_run_docs_only'
        || grade === 'qa_error') {
        return grade;
    }
    return 'unknown';
}
function qaEvidenceCandidateMeta(entry) {
    const meta = entry.meta;
    return isRecord(meta) ? meta : {};
}
function qaEvidenceCandidateMessage(entry) {
    const message = entry.message;
    if (typeof message === 'string')
        return message;
    const content = entry.content;
    return typeof content === 'string' ? content : '';
}
function qaEvidenceCandidatePhase(entry) {
    const refs = entry.refs;
    if (!isRecord(refs))
        return '';
    return toText(refs.phase).toLowerCase();
}
function qaEvidenceColor(grade) {
    if (grade === 'run_ledger_passed')
        return 'success';
    if (grade === 'run_ledger_pending')
        return 'warning';
    if (grade === 'run_ledger_failed' || grade === 'run_ledger_unavailable')
        return 'error';
    if (grade === 'real_command_passed')
        return 'success';
    if (grade === 'structural_fallback_passed' || grade.startsWith('not_run'))
        return 'warning';
    if (grade === 'blocked_missing_dependencies' || grade.endsWith('_failed') || grade === 'qa_error')
        return 'error';
    return 'default';
}
function pickFailedLedgerProject(projects) {
    return projects.find((project) => !project.ok || project.failed_gate_count > 0) ?? null;
}
export function extractRunLedgerQaEvidence(projection) {
    if (!projection)
        return null;
    const detail = toText(projection.detail);
    if (projection.available === false) {
        return {
            grade: 'run_ledger_unavailable',
            reason: 'run_ledger_unavailable',
            summary: detail || 'Run Ledger projection unavailable',
            passed: false,
        };
    }
    if (projection.total <= 0 && projection.projects.length === 0) {
        return {
            grade: 'run_ledger_pending',
            reason: 'run_ledger_pending',
            summary: detail || 'Run Ledger projection has no projected projects yet',
            passed: false,
        };
    }
    const failedProject = pickFailedLedgerProject(projection.projects);
    if (!projection.ok || projection.failed > 0 || failedProject) {
        return {
            grade: 'run_ledger_failed',
            reason: failedProject?.latest_token_id
                ? `run_ledger_failed_gate:${failedProject.latest_token_id}`
                : 'run_ledger_failed_gate',
            summary: failedProject?.detail || detail || 'Run Ledger gate failed',
            passed: false,
        };
    }
    return {
        grade: 'run_ledger_passed',
        reason: 'run_ledger_projected',
        summary: detail || `Run Ledger projected ${projection.projected}/${projection.total} project(s)`,
        passed: true,
    };
}
export function extractLatestQaEvidence(logs, dialogueEvents = []) {
    const candidates = [...logs, ...dialogueEvents].reverse();
    for (const entry of candidates) {
        const meta = qaEvidenceCandidateMeta(entry);
        const grade = normalizeQaEvidenceGrade(meta.evidence_grade);
        const reason = toText(meta.reason);
        const message = qaEvidenceCandidateMessage(entry);
        const phase = qaEvidenceCandidatePhase(entry);
        const hasQaEvidence = grade !== 'unknown'
            || reason.startsWith('integration_qa_')
            || phase === 'integration_qa'
            || message.includes('integration_qa')
            || message.includes('Project integration QA');
        if (!hasQaEvidence) {
            continue;
        }
        return {
            grade,
            reason: reason || toText(message),
            summary: toText(meta.summary) || toText(message),
            passed: typeof meta.passed === 'boolean' ? meta.passed : null,
        };
    }
    return null;
}
export function ProjectProgressPanel({ tasks, directorTasks, pmState, focus, notes, goals, planText, planMtime, planTextNormalized, successStats, pmRunning, engineStatus, onOpenDocsPanel, className, 
// 新增
qualityGate, executionLogs = [], dialogueEvents = [], currentPhase = 'idle', directorTaskSource = 'realtime', directorRealtimeConnected = false, controlPlaneProjection, }) {
    const [isGoalsExpanded, setIsGoalsExpanded] = useState(true);
    const normalizedTasks = Array.isArray(tasks)
        ? tasks.filter((task) => Boolean(task && typeof task === 'object'))
        : [];
    const normalizedDirectorTasks = Array.isArray(directorTasks)
        ? directorTasks.filter((task) => Boolean(task && typeof task === 'object'))
        : [];
    const totalTasks = normalizedTasks.length;
    const completedIdsRaw = Array.isArray(pmState?.completed_task_ids) ? pmState.completed_task_ids : [];
    const completedIds = completedIdsRaw
        .map((item) => (typeof item === 'string' ? item.trim() : ''))
        .filter((item) => item.length > 0);
    const completedSet = new Set(completedIds);
    const completedInList = normalizedTasks.filter((task) => completedSet.has(taskKey(task))).length;
    const doneCount = normalizedTasks.filter((task) => isTaskDone(task) || completedSet.has(taskKey(task))).length;
    const reportedCompletedRaw = pmState?.completed_task_count;
    const reportedCompletedCount = typeof reportedCompletedRaw === 'number'
        ? reportedCompletedRaw
        : typeof reportedCompletedRaw === 'string'
            ? Number(reportedCompletedRaw)
            : null;
    const reportedCompleted = reportedCompletedCount !== null && Number.isFinite(reportedCompletedCount)
        ? Math.max(0, reportedCompletedCount)
        : 0;
    const completedCount = totalTasks > 0
        ? Math.max(doneCount, completedInList, reportedCompleted)
        : reportedCompleted > 0
            ? reportedCompleted
            : completedSet.size;
    const lastTaskId = toText(pmState?.last_director_task_id);
    const lastTaskTitle = toText(pmState?.last_director_task_title);
    const lastStatus = toText(pmState?.last_director_status).toLowerCase();
    const lastUpdated = toText(pmState?.last_updated_ts);
    const iterationRaw = pmState?.pm_iteration;
    const iteration = typeof iterationRaw === 'number'
        ? iterationRaw
        : typeof iterationRaw === 'string'
            ? Number(iterationRaw)
            : null;
    const pmRole = readEngineRole(engineStatus, ['PM', 'pm']);
    const chiefEngineerRole = readEngineRole(engineStatus, [
        'ChiefEngineer',
        'Chief Engineer',
        'chief_engineer',
        'chief-engineer',
        'ce',
    ]);
    const directorRole = readEngineRole(engineStatus, ['Director', 'director']);
    // 从 Engine 状态获取 Director 当前任务（实时来源优先）
    const engineDirectorTaskId = directorRole?.task_id;
    const engineDirectorTaskTitle = directorRole?.task_title;
    const engineDirectorStatus = directorRole?.status;
    const engineDirectorDetail = directorRole?.detail;
    // PM 当前任务高亮策略：Engine task_id/title 命中 > nextPending 推断 > lastTaskId/Title 回退
    let highlightedTask;
    let currentIndex = -1;
    if ((engineDirectorTaskId || engineDirectorTaskTitle) && normalizedTasks.length > 0) {
        // 策略1：Engine 的 Director task_id/title 命中 PM 任务
        const engineTaskIndex = normalizedTasks.findIndex((task) => taskKey(task) === toText(engineDirectorTaskId) || pickTaskSummary(task) === toText(engineDirectorTaskTitle));
        if (engineTaskIndex >= 0) {
            highlightedTask = normalizedTasks[engineTaskIndex];
            currentIndex = engineTaskIndex;
        }
    }
    if (!highlightedTask) {
        // 策略2：nextPending 推断
        const nextPendingTask = normalizedTasks.find((task) => {
            const key = taskKey(task);
            if (!key)
                return false;
            return !completedSet.has(key) && !isTaskDone(task);
        });
        if (nextPendingTask) {
            highlightedTask = nextPendingTask;
            currentIndex = normalizedTasks.findIndex((t) => t.id === nextPendingTask.id);
        }
    }
    if (!highlightedTask) {
        // 策略3：lastTaskId/Title 回退
        currentIndex = normalizedTasks.findIndex((task) => (lastTaskId && taskKey(task) === lastTaskId) || (lastTaskTitle && pickTaskSummary(task) === lastTaskTitle));
        if (currentIndex >= 0) {
            highlightedTask = normalizedTasks[currentIndex];
        }
    }
    const liveDirectorTask = normalizedDirectorTasks.find((task) => isTaskActive(task))
        ?? normalizedDirectorTasks.find((task) => !isTaskDone(task));
    const lastDirectorTask = normalizedDirectorTasks.length > 0
        ? normalizedDirectorTasks[normalizedDirectorTasks.length - 1]
        : undefined;
    const directorCompletedCount = normalizedDirectorTasks.filter((task) => isTaskDone(task)).length;
    const directorQueueComplete = normalizedDirectorTasks.length > 0 && directorCompletedCount >= normalizedDirectorTasks.length;
    const directorTaskLabel = pickTaskSummary(liveDirectorTask)
        || pickTaskSummary(lastDirectorTask)
        || engineDirectorTaskTitle
        || lastTaskTitle;
    const positionIndex = currentIndex >= 0 ? currentIndex : totalTasks > 0 ? 0 : -1;
    // 状态展示：pm_state 缺失时回退使用 Engine Director 状态
    const effectiveStatus = lastStatus || engineDirectorStatus?.toLowerCase() || '';
    const effectiveDetail = toText(pmState?.last_director_detail) || engineDirectorDetail || '';
    const runLedgerQaEvidence = extractRunLedgerQaEvidence(controlPlaneProjection);
    const latestRuntimeQaEvidence = extractLatestQaEvidence(executionLogs, dialogueEvents);
    const hasTerminalRuntimeClaim = (totalTasks > 0 && completedCount >= totalTasks)
        || isTerminalSuccessStatus(effectiveStatus)
        || directorQueueComplete
        || isTerminalSuccessStatus(pmRole?.status)
        || isTerminalSuccessStatus(chiefEngineerRole?.status)
        || isTerminalSuccessStatus(directorRole?.status)
        || latestRuntimeQaEvidence?.passed === true;
    const missingRunLedgerQaEvidence = !runLedgerQaEvidence && hasTerminalRuntimeClaim
        ? {
            grade: 'run_ledger_pending',
            reason: 'run_ledger_required',
            summary: 'Run Ledger projection is required before terminal completion',
            passed: false,
        }
        : null;
    const effectiveRunLedgerQaEvidence = runLedgerQaEvidence ?? missingRunLedgerQaEvidence;
    const runLedgerBlocksTerminalCompletion = Boolean(effectiveRunLedgerQaEvidence && effectiveRunLedgerQaEvidence.passed !== true);
    const displayedCompletedCount = runLedgerBlocksTerminalCompletion && totalTasks > 0 && completedCount >= totalTasks
        ? Math.max(0, totalTasks - 1)
        : completedCount;
    const effectiveStatusForDisplay = runLedgerBlocksTerminalCompletion && effectiveStatus === 'success'
        ? effectiveRunLedgerQaEvidence?.grade === 'run_ledger_failed'
            ? 'failed'
            : 'blocked'
        : effectiveStatus;
    let progress = 0;
    let progressHint = '待 PM 出具任务';
    let progressMode = 'idle';
    if (totalTasks > 0 && displayedCompletedCount > 0) {
        progress = displayedCompletedCount / totalTasks;
        progressHint = runLedgerBlocksTerminalCompletion && completedCount >= totalTasks
            ? `等待 Run Ledger 证据 · ${Math.min(displayedCompletedCount, totalTasks)}/${totalTasks}`
            : `已完成 ${Math.min(displayedCompletedCount, totalTasks)}/${totalTasks}`;
        progressMode = 'done';
    }
    else if (totalTasks > 0 && positionIndex >= 0) {
        progress = (positionIndex + 1) / totalTasks;
        progressHint = `当前任务 ${positionIndex + 1}/${totalTasks}（估算）`;
        progressMode = 'position';
    }
    else if (typeof successStats?.rate === 'number') {
        progress = successStats.rate;
        progressHint = `历史成功率 ${Math.round(progress * 100)}%（估算）`;
        progressMode = 'success';
    }
    progress = Math.max(0, Math.min(1, progress));
    const statusIcon = effectiveStatusForDisplay === 'success' ? (_jsx(CheckCircle, { className: "size-4 text-emerald-300" })) : effectiveStatusForDisplay === 'blocked' ? (_jsx(AlertTriangle, { className: "size-4 text-amber-300" })) : (_jsx(Activity, { className: "size-4 text-slate-300" }));
    const focusText = focus ? clampText(focus, 160) : '';
    const notesText = notes ? clampText(notes, 180) : '';
    const currentSummary = clampText(directorTaskLabel || '', 160);
    const goalList = Array.isArray(goals) ? goals.filter((item) => typeof item === 'string' && item.trim().length > 0) : [];
    const directorQueueHint = normalizedDirectorTasks.length > 0
        ? `${directorCompletedCount}/${normalizedDirectorTasks.length} Director queue 已完成`
        : directorRealtimeConnected
            ? 'Director live queue 为空'
            : 'Director live queue 已断开';
    const pmContractsReady = totalTasks > 0;
    const pmContractsComplete = totalTasks > 0 && displayedCompletedCount >= totalTasks;
    const directorHandoffReady = normalizedDirectorTasks.length > 0;
    const chiefEngineerDefaultStatus = currentPhase === 'chief_engineer'
        ? 'running'
        : directorHandoffReady
            ? directorQueueComplete
                ? 'success'
                : 'ready'
            : pmContractsReady
                ? 'ready'
                : 'waiting';
    const chiefEngineerDefaultTask = highlightedTask
        ? `蓝图审查：${pickTaskSummary(highlightedTask)}`
        : directorHandoffReady
            ? `蓝图已交接：${directorQueueHint}`
            : pmContractsReady
                ? 'PM 合同已接收，等待蓝图生成'
                : '等待 PM 合同';
    const pmDefaultTask = highlightedTask
        ? pickTaskSummary(highlightedTask)
        : pmContractsComplete
            ? `任务合同已完成：${displayedCompletedCount}/${totalTasks}`
            : pmContractsReady
                ? `任务合同已生成：${totalTasks} 项`
                : '等待任务合同';
    const ledgerVerifiedDirectorQueueComplete = directorQueueComplete && !runLedgerBlocksTerminalCompletion;
    const directorDefaultStatus = ledgerVerifiedDirectorQueueComplete
        ? 'success'
        : liveDirectorTask
            ? 'running'
            : runLedgerBlocksTerminalCompletion
                ? 'blocked'
                : 'waiting';
    const qaEvidence = effectiveRunLedgerQaEvidence ?? latestRuntimeQaEvidence;
    const pipelineRoles = [
        {
            id: 'pm',
            label: 'PM',
            detail: `${totalTasks} contracts · ${displayedCompletedCount}/${totalTasks || 0} done`,
            status: runLedgerGuardedPipelineStatus(rolePipelineStatus(pmRole, pmRunning ? 'running' : pmContractsComplete ? 'success' : pmContractsReady ? 'ready' : 'waiting'), effectiveRunLedgerQaEvidence),
            task: rolePipelineTask(pmRole, pmDefaultTask),
        },
        {
            id: 'chief-engineer',
            label: 'Chief Engineer',
            detail: 'blueprint / handoff gate',
            status: runLedgerGuardedPipelineStatus(rolePipelineStatus(chiefEngineerRole, chiefEngineerDefaultStatus), effectiveRunLedgerQaEvidence),
            task: rolePipelineTask(chiefEngineerRole, chiefEngineerDefaultTask),
        },
        {
            id: 'director',
            label: 'Director',
            detail: directorQueueHint,
            status: runLedgerGuardedPipelineStatus(rolePipelineStatus(directorRole, effectiveStatusForDisplay || directorDefaultStatus), effectiveRunLedgerQaEvidence),
            task: rolePipelineTask(directorRole, currentSummary || '等待 CE 交接'),
        },
    ];
    return (_jsxs("div", { "data-testid": "project-progress-panel", className: `border-b border-white/5 bg-transparent px-5 py-4 flex flex-col min-h-0 overflow-y-auto ${className || ''}`, children: [_jsxs("div", { className: "flex flex-wrap items-start justify-between gap-4", children: [_jsxs("div", { className: "flex min-w-0 flex-1 items-start gap-3", children: [_jsx("div", { className: "flex size-10 items-center justify-center rounded-xl bg-white/5 text-accent", children: _jsx(Target, { className: "size-5" }) }), _jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx("span", { className: "text-sm font-heading font-bold text-text-main", children: "PM \u653F\u52A1\u8FDB\u5EA6" }), pmRunning ? (_jsx(StatusBadge, { color: "success", variant: "dot", pulse: true, children: UI_TERMS.states.running })) : (_jsx(StatusBadge, { color: "default", variant: "soft", children: UI_TERMS.states.idle })), iteration !== null && Number.isFinite(iteration) ? (_jsxs(StatusBadge, { color: "accent", variant: "soft", children: ["\u8F6E\u6B21 ", iteration] })) : null, _jsx(StatusBadge, { color: directorRealtimeConnected ? 'accent' : 'warning', variant: "dot", pulse: directorRealtimeConnected, children: directorRealtimeConnected ? 'Director live queue' : 'Director live 断线' })] }), _jsx("div", { className: "mt-1 text-xs text-text-muted", children: focusText || notesText ? (_jsxs(_Fragment, { children: [focusText ? _jsxs("span", { children: ["Focus: ", _jsx("span", { className: "text-text-main", children: focusText })] }) : null, focusText && notesText ? _jsx("span", { className: "mx-2 text-white/10", children: "|" }) : null, notesText ? _jsxs("span", { children: ["\u6279\u6CE8: ", notesText] }) : null] })) : (_jsx("span", { children: "PM \u6B63\u5728\u6574\u7406\u4EFB\u52A1" })) })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [onOpenDocsPanel ? (_jsxs("button", { type: "button", "data-testid": "open-docs-init", onClick: onOpenDocsPanel, className: "inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs font-medium text-emerald-200 transition-colors hover:bg-emerald-500/20", children: [_jsx("span", { children: "\u751F\u6210\u8BA1\u5212" }), _jsx(ArrowRight, { className: "size-3" })] })) : null, _jsxs("div", { className: "flex items-center gap-2 soft-chip rounded-lg px-3 py-2 text-xs text-text-main", title: effectiveDetail || undefined, children: [statusIcon, _jsx(StatusBadge, { color: effectiveStatusForDisplay === 'success' ? 'success'
                                            : effectiveStatusForDisplay === 'blocked' ? 'warning'
                                                : effectiveStatusForDisplay === 'failure' || effectiveStatusForDisplay === 'failed' ? 'error'
                                                    : 'default', variant: "outlined", className: "font-mono uppercase", children: effectiveStatusForDisplay || '未有回执' }), lastUpdated ? (_jsxs(_Fragment, { children: [_jsx("span", { className: "text-white/10", children: "|" }), _jsx(Clock, { className: "size-3 text-text-dim" }), _jsx("span", { className: "text-text-dim", children: lastUpdated })] })) : null] })] })] }), _jsxs("div", { className: "mt-4 grid gap-4 lg:grid-cols-[1fr_300px]", children: [_jsx(ProgressBar, { progress: progress, progressHint: progressHint, progressMode: progressMode, totalTasks: totalTasks, completedCount: displayedCompletedCount, successRate: successStats?.rate }), _jsxs("div", { className: "rounded-xl border border-white/10 bg-white/5 p-4 hover:border-accent/30 transition-all flex flex-col", children: [_jsx(CurrentTaskCard, { currentSummary: currentSummary, lastTaskId: liveDirectorTask?.id || lastTaskId }), _jsx("div", { className: "mt-3 text-xs text-text-muted", children: _jsx("span", { className: "font-mono", children: directorQueueHint }) })] })] }), pmRunning && currentPhase && currentPhase !== 'idle' && (_jsx("div", { className: "mt-4", children: _jsx(PhaseIndicator, { currentPhase: currentPhase, qualityScore: qualityGate?.score, retryAttempt: qualityGate?.attempt, maxRetries: qualityGate?.maxAttempts }) })), pmRunning && qualityGate && currentPhase === 'planning' && (_jsx("div", { className: "mt-4", children: _jsx(QualityGateCard, { data: qualityGate }) })), qaEvidence ? (_jsxs("div", { "data-testid": "qa-evidence-grade", className: "mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-text-muted", title: qaEvidence.summary || undefined, children: [_jsx("span", { className: "font-medium text-text-main", children: "QA evidence" }), _jsx(StatusBadge, { color: qaEvidenceColor(qaEvidence.grade), variant: "outlined", className: "font-mono", children: QA_EVIDENCE_LABELS[qaEvidence.grade] }), qaEvidence.reason ? (_jsx("span", { className: "font-mono text-text-dim", children: qaEvidence.reason })) : null] })) : null, pmRunning && executionLogs.length > 0 && (_jsx("div", { className: "mt-4", children: _jsx(ExecutionLog, { logs: executionLogs, maxHeight: "180px" }) })), goalList.length > 0 && (_jsxs("div", { className: "mt-4 rounded-xl border border-white/5 bg-white/5 overflow-hidden", children: [_jsxs("button", { onClick: () => setIsGoalsExpanded(!isGoalsExpanded), className: "w-full flex items-center justify-between px-4 py-3 hover:bg-white/5 transition-colors", children: [_jsxs("div", { className: "flex items-center gap-2", children: [isGoalsExpanded ? (_jsx(ChevronDown, { className: "w-4 h-4 text-text-muted" })) : (_jsx(ChevronRight, { className: "w-4 h-4 text-text-muted" })), _jsx("span", { className: "text-xs font-medium uppercase tracking-wide text-text-muted", children: "Focus \u603B\u89C8" })] }), _jsxs("span", { className: "text-xs font-mono text-text-muted", children: [goalList.length, " \u9879"] })] }), isGoalsExpanded && (_jsx("div", { className: "px-4 pb-4 max-h-40 overflow-auto custom-scrollbar", children: _jsx("div", { className: "mt-2 space-y-2 text-xs text-text-main", children: goalList.map((item, idx) => (_jsxs("div", { className: "flex items-start gap-2", children: [_jsxs("span", { className: "mt-0.5 text-accent font-mono text-[10px]", children: [idx + 1, "."] }), _jsx("span", { className: "leading-relaxed", children: item })] }, `${idx}-${item}`))) }) }))] })), _jsx("div", { className: "mt-4", children: _jsx(PlanBoard, { planText: planText ?? '', planMtime: planMtime, planTextNormalized: planTextNormalized }) }), _jsxs("div", { className: "mt-4 flex min-h-0 flex-1 flex-col", children: [_jsxs("section", { className: "mb-3 rounded-xl border border-white/10 bg-white/[0.045] p-3", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(ListChecks, { className: "size-4 text-text-dim" }), _jsx("span", { "data-testid": "project-chain-heading", className: "font-medium tracking-wide", children: "\u5168\u94FE\u8DEF\u4EFB\u52A1\u6D41\uFF08PM \u2192 Chief Engineer \u2192 Director\uFF09" })] }), _jsx("span", { className: "font-mono", children: totalTasks ? `${totalTasks} \u9879` : '\u6682\u65e0\u4efb\u52a1' })] }), _jsx("div", { className: "mt-3 grid gap-2 md:grid-cols-3", children: pipelineRoles.map((role, index) => (_jsxs("div", { "data-testid": `project-chain-role-${role.id}`, className: "min-w-0 rounded-lg border border-white/10 bg-bg-surface/40 p-3", children: [_jsxs("div", { className: "flex items-start justify-between gap-2", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-semibold text-text-main", children: [_jsx("span", { className: "font-mono text-text-dim", children: index + 1 }), _jsx("span", { children: role.label })] }), _jsx("div", { className: "mt-1 truncate text-[11px] text-text-muted", children: role.detail })] }), _jsx(StatusBadge, { color: role.status === 'completed' || role.status === 'success' ? 'success'
                                                        : role.status === 'running' || role.status === 'in_progress' ? 'accent'
                                                            : role.status === 'failed' || role.status === 'error' ? 'error'
                                                                : 'default', variant: "outlined", className: "shrink-0 font-mono uppercase", children: role.status })] }), _jsx("div", { className: "mt-2 line-clamp-2 text-xs leading-5 text-text-muted", children: role.task })] }, role.id))) })] }), _jsx(TaskList, { tasks: normalizedTasks, completedSet: completedSet, currentTaskKey: highlightedTask ? taskKey(highlightedTask) : undefined, taskKey: taskKey, isTaskDone: isTaskDone, clampText: clampText })] })] }));
}
