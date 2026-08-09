import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { Crown, ScrollText, CheckCircle2, MessageSquare, Settings, ChevronLeft, FileText, ListTodo, History, Sparkles, BarChart3, Loader2, Stethoscope, Activity, Zap, Brain, FileCode, Clock, AlertCircle, RefreshCw, GitBranch, Database, Coins, Trash2, Wrench, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { PMTaskPanel } from './PMTaskPanel';
import { PMDocumentPanel } from './PMDocumentPanel';
import { PMAIDialoguePanel } from './PMAIDialoguePanel';
import { PMStatusBar } from './PMStatusBar';
import { PMDiagnosticsPanel } from './PMDiagnosticsPanel';
import { PMWorkbenchPanel } from './PMWorkbenchPanel';
import { QualityGateCard } from './QualityGateCard';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import { getPmStartupDiagnostics, getPmRequirement, listPmDirectorTaskHistory, listPmRequirements, listPmTaskHistory, clearRoleKernelCache, getRoleKernelCacheStats, getRoleKernelLLMEvents, getRoleKernelTokenBudgetStats, } from '@/services/pmService';
import { getRoleCapabilities, resolveRoleCapabilities } from '@/services/roleSessionService';
// 阶段到视图的映射
const PHASE_TO_VIEW = {
    'idle': { view: 'tasks', icon: _jsx(ListTodo, { className: "w-4 h-4" }), label: '任务', color: 'text-slate-400' },
    'planning': { view: 'tasks', icon: _jsx(Brain, { className: "w-4 h-4" }), label: '规划', color: 'text-blue-400' },
    'analyzing': { view: 'activity', icon: _jsx(Activity, { className: "w-4 h-4" }), label: '分析', color: 'text-purple-400' },
    'executing': { view: 'activity', icon: _jsx(Zap, { className: "w-4 h-4" }), label: '执行', color: 'text-amber-400' },
    'llm_calling': { view: 'activity', icon: _jsx(Brain, { className: "w-4 h-4" }), label: '思考', color: 'text-cyan-400' },
    'tool_running': { view: 'activity', icon: _jsx(FileCode, { className: "w-4 h-4" }), label: '工具', color: 'text-emerald-400' },
    'verification': { view: 'activity', icon: _jsx(CheckCircle2, { className: "w-4 h-4" }), label: '验证', color: 'text-teal-400' },
    'completed': { view: 'tasks', icon: _jsx(CheckCircle2, { className: "w-4 h-4" }), label: '完成', color: 'text-green-400' },
    'error': { view: 'activity', icon: _jsx(Activity, { className: "w-4 h-4" }), label: '错误', color: 'text-red-400' },
};
const EMPTY_PM_BACKEND_EVIDENCE = {
    loading: false,
    capabilities: [],
    capabilitiesError: '',
    diagnostics: null,
    diagnosticsError: '',
    cacheStats: null,
    cacheError: '',
    llmEvents: null,
    llmEventsError: '',
    tokenBudgetStats: null,
    tokenBudgetError: '',
};
function stringValue(value) {
    return typeof value === 'string' ? value.trim() : '';
}
function readRecordNumber(record, keys) {
    if (!record)
        return undefined;
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'number' && Number.isFinite(value))
            return value;
        if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value)))
            return Number(value);
    }
    return undefined;
}
function readKernelEventString(event, keys) {
    if (!event)
        return '';
    for (const key of keys) {
        const value = event[key];
        if (typeof value === 'string' && value.trim())
            return value.trim();
        if (typeof value === 'number' && Number.isFinite(value))
            return String(value);
    }
    return '';
}
function formatPmKernelEvent(event) {
    if (!event)
        return 'none';
    const eventType = readKernelEventString(event, ['event_type', 'type', 'status']) || 'event';
    const model = readKernelEventString(event, ['model', 'model_name', 'provider']);
    const tokens = readRecordNumber(event, ['tokens', 'token_count', 'total_tokens']);
    return [
        eventType.replace(/_/g, ' '),
        model,
        typeof tokens === 'number' ? `${tokens} tokens` : '',
    ].filter(Boolean).join(' · ');
}
function formatPmCacheStats(stats) {
    if (!stats)
        return 'unavailable';
    const hits = readRecordNumber(stats, ['hits']) ?? 0;
    const misses = readRecordNumber(stats, ['misses']) ?? 0;
    const size = readRecordNumber(stats, ['size']) ?? 0;
    const maxSize = readRecordNumber(stats, ['max_size', 'maxSize']);
    const hitRate = readRecordNumber(stats, ['hit_rate', 'hitRate']);
    return [
        `hits=${hits}`,
        `misses=${misses}`,
        `size=${size}${typeof maxSize === 'number' ? `/${maxSize}` : ''}`,
        typeof hitRate === 'number' ? `hit=${hitRate}%` : '',
    ].filter(Boolean).join(' · ');
}
function formatPmTokenBudget(stats) {
    if (!stats)
        return 'unavailable';
    const total = readRecordNumber(stats, ['total', 'total_budget']);
    const available = readRecordNumber(stats, ['available_conversation', 'remaining']);
    const safety = readRecordNumber(stats, ['safety_margin']);
    return [
        typeof total === 'number' ? `total=${total}` : '',
        typeof available === 'number' ? `available=${available}` : '',
        typeof safety === 'number' ? `margin=${safety}` : '',
    ].filter(Boolean).join(' · ') || 'stats ready';
}
function evidenceEndpoint(endpoint, workspace = '') {
    const value = String(workspace || '').trim();
    if (!value)
        return endpoint;
    const separator = endpoint.includes('?') ? '&' : '?';
    return `${endpoint}${separator}workspace=${encodeURIComponent(value)}`;
}
function EvidenceEndpointBadge({ endpoint, testId, }) {
    return (_jsx("span", { className: "shrink-0 rounded border border-white/10 bg-slate-950/70 px-1.5 py-0.5 text-[9px] font-medium text-slate-500", title: endpoint, "data-endpoint": endpoint, "data-testid": testId, children: "API" }));
}
function formatPmDiagnostics(diagnostics) {
    if (!diagnostics)
        return 'unavailable';
    const llmState = diagnostics.llm?.state || (diagnostics.llm?.ok ? 'ready' : 'blocked');
    const workspaceState = diagnostics.workspace?.status || (diagnostics.workspace?.ok ? 'ready' : 'blocked');
    const planningInputState = diagnostics.planning_input?.status || (diagnostics.planning_input?.ok ? 'ready' : 'missing');
    const blockedRoles = Array.isArray(diagnostics.llm?.blocked_roles) ? diagnostics.llm.blocked_roles : [];
    const issues = Array.isArray(diagnostics.issues) ? diagnostics.issues.length : 0;
    const startupBlockers = pmStartupBlockers(diagnostics).length;
    return [
        startupBlockers > 0 ? 'start=blocked' : diagnostics.ok ? 'ready' : 'degraded',
        `llm=${llmState}`,
        blockedRoles.length > 0 ? `blocked=${blockedRoles.join(',')}` : '',
        `workspace=${workspaceState}`,
        `input=${planningInputState}`,
        issues > 0 ? `issues=${issues}` : '',
    ].filter(Boolean).join(' · ');
}
const PM_STARTUP_BLOCKER_LABELS = {
    lancedb_unavailable: 'LanceDB 不可用',
    llm_not_ready: 'PM LLM 未通过就绪检查',
    workspace_unavailable: '工作区不可用',
    workspace_docs_missing: 'docs/ 初始化未完成',
    planning_input_missing: '缺少需求/计划输入',
    planning_input_empty: '需求/计划输入为空',
    planning_input_unreadable: '需求/计划输入无法读取',
};
const PM_HARD_BLOCKER_ISSUES = new Set(Object.keys(PM_STARTUP_BLOCKER_LABELS));
function pmStartupBlockers(diagnostics) {
    if (!diagnostics) {
        return [];
    }
    if (diagnostics.can_start === true) {
        return [];
    }
    if (Array.isArray(diagnostics.startup_blockers) && diagnostics.startup_blockers.length > 0) {
        return diagnostics.startup_blockers
            .map((issue) => String(issue || '').trim())
            .filter((issue) => issue.length > 0);
    }
    const hasExplicitStartupSignal = typeof diagnostics.can_start === 'boolean' || Array.isArray(diagnostics.startup_blockers);
    if (hasExplicitStartupSignal && diagnostics.can_start !== false) {
        return [];
    }
    return (diagnostics.issues || []).filter((issue) => PM_HARD_BLOCKER_ISSUES.has(issue));
}
function formatPmStartupBlockReason(diagnostics) {
    const blockers = pmStartupBlockers(diagnostics);
    if (blockers.length === 0) {
        return '';
    }
    const primary = PM_STARTUP_BLOCKER_LABELS[blockers[0]] || blockers[0].replace(/_/g, ' ');
    const extraCount = blockers.length - 1;
    return `PM 启动诊断未通过：${primary}${extraCount > 0 ? `，另有 ${extraCount} 项阻断` : ''}`;
}
function isLlmStartupBlockReason(value) {
    const token = String(value || '').trim().toLowerCase();
    return Boolean(token && (token.includes('llm') || token.includes('provider') || token.includes('model')));
}
function mergePmTaskEvidenceRows(runtimeTasks, commandSnapshots) {
    const rows = new Map();
    for (const task of commandSnapshots) {
        if (task.id)
            rows.set(task.id, task);
    }
    for (const task of runtimeTasks) {
        if (task.id)
            rows.set(task.id, task);
    }
    return Array.from(rows.values());
}
const PM_RUNTIME_PUSH_ENDPOINT = '/v2/ws/runtime';
const PM_COMMAND_ACCEPTED_MESSAGE = '命令已提交，等待 runtime.v2 推送确认。';
const PM_RUNTIME_ROLE_LABELS = {
    pm: 'PM',
    chiefengineer: 'Chief Engineer',
    director: 'Director',
    qa: 'QA',
};
function normalizeRuntimeRoleLabel(value) {
    return value.replace(/[^a-z0-9]/gi, '').toLowerCase();
}
function parseRuntimeRoleLine(line) {
    const match = /^([A-Za-z][A-Za-z _-]{1,32}):\s*(.+)$/.exec(line.trim());
    if (!match)
        return null;
    const roleToken = normalizeRuntimeRoleLabel(match[1]);
    const role = PM_RUNTIME_ROLE_LABELS[roleToken];
    const detail = match[2].trim();
    if (!role || !detail)
        return null;
    return { role, detail };
}
function runtimeIssueLines(issue) {
    return String(issue?.detail || '')
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
}
function splitPMRuntimeIssue(issue) {
    const lines = runtimeIssueLines(issue);
    const roleLines = lines.map(parseRuntimeRoleLine).filter((line) => Boolean(line));
    const rootCause = roleLines.find((line) => line.role === 'PM') ?? null;
    const cascades = roleLines.filter((line) => line.role !== rootCause?.role);
    const nonRoleLines = lines.filter((line) => !parseRuntimeRoleLine(line));
    const detail = rootCause?.detail || nonRoleLines.join('\n') || String(issue?.detail || issue?.code || '').trim();
    return { rootCause, cascades, detail };
}
function isPMTerminalSuccess(pmTerminalStatus) {
    if (!pmTerminalStatus || pmTerminalStatus.terminal !== true) {
        return false;
    }
    const status = stringValue(pmTerminalStatus.status).toLowerCase();
    const exitCode = typeof pmTerminalStatus.exit_code === 'number' ? pmTerminalStatus.exit_code : null;
    const error = stringValue(pmTerminalStatus.error);
    if (error || pmTerminalStatus.ok === false || status === 'failed') {
        return false;
    }
    return pmTerminalStatus.ok === true || status === 'success' || status === 'completed' || exitCode === 0;
}
function resolvePMRuntimeBanner({ pmRunning, pmStartBlockedReason, runtimeIssue, pmTerminalStatus, }) {
    const hasConfirmedTerminalSuccess = isPMTerminalSuccess(pmTerminalStatus);
    if (runtimeIssue && !pmRunning && !hasConfirmedTerminalSuccess) {
        const breakdown = splitPMRuntimeIssue(runtimeIssue);
        const hasStartBlocker = Boolean(pmStartBlockedReason);
        return {
            title: hasStartBlocker ? runtimeIssue.title || 'PM 运行已终止' : '上次 PM Run 失败',
            detail: breakdown.detail || runtimeIssue.code || 'PM 运行失败，请查看运行日志。',
            severity: hasStartBlocker ? 'error' : 'warning',
            refs: [],
            code: runtimeIssue.code,
            rootCause: breakdown.rootCause,
            cascades: breakdown.cascades,
            startBlocker: pmStartBlockedReason || '',
        };
    }
    if (!pmRunning && hasConfirmedTerminalSuccess && !pmStartBlockedReason) {
        return null;
    }
    if (!pmRunning && pmStartBlockedReason) {
        return {
            title: 'PM 启动被阻止',
            detail: pmStartBlockedReason,
            severity: 'warning',
            refs: [],
        };
    }
    if (!pmTerminalStatus || pmRunning)
        return null;
    const status = stringValue(pmTerminalStatus.status).toLowerCase();
    const exitCode = typeof pmTerminalStatus.exit_code === 'number' ? pmTerminalStatus.exit_code : null;
    const error = stringValue(pmTerminalStatus.error);
    const failed = ((exitCode !== null && exitCode !== 0)
        || pmTerminalStatus.ok === false
        || (pmTerminalStatus.terminal === true && status === 'failed')
        || Boolean(error));
    if (!failed)
        return null;
    const detailParts = [
        exitCode !== null ? `退出码: ${exitCode}` : '',
        error || '',
    ].filter(Boolean);
    return {
        title: 'PM 运行已终止',
        detail: detailParts.join('\n') || 'PM 进程已进入失败终态，请查看运行日志和任务合同。',
        severity: 'error',
        refs: [
            stringValue(pmTerminalStatus.contract_path),
            stringValue(pmTerminalStatus.log_path),
        ].filter(Boolean),
    };
}
function PMBackendEvidenceStrip({ evidence, cacheClearing, cacheClearStatus, onRefresh, onClearCache, workspace, }) {
    const llmEventCount = evidence.llmEvents?.count
        ?? (Array.isArray(evidence.llmEvents?.events) ? evidence.llmEvents.events.length : 0);
    const latestLLMEvent = evidence.llmEvents?.events?.[0] ?? null;
    const diagnosticsLabel = formatPmDiagnostics(evidence.diagnostics);
    const cacheLabel = formatPmCacheStats(evidence.cacheStats);
    const tokenBudgetLabel = formatPmTokenBudget(evidence.tokenBudgetStats);
    const hasError = Boolean(evidence.capabilitiesError
        || evidence.diagnosticsError
        || evidence.llmEventsError
        || evidence.cacheError
        || evidence.tokenBudgetError);
    const canStart = evidence.diagnostics?.can_start;
    const summaryTone = evidence.loading
        ? 'border-slate-500/20 bg-slate-500/10 text-slate-300'
        : hasError
            ? 'border-rose-400/25 bg-rose-500/10 text-rose-200'
            : canStart === false
                ? 'border-amber-400/25 bg-amber-500/10 text-amber-200'
                : 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200';
    const summaryLabel = evidence.loading
        ? '检查中'
        : hasError
            ? '需要查看'
            : canStart === false
                ? '门禁阻断'
                : '就绪';
    const capabilityLabel = evidence.loading
        ? '能力 ...'
        : evidence.capabilitiesError
            ? '能力 ?'
            : `能力 ${evidence.capabilities.length}`;
    const blockerCount = pmStartupBlockers(evidence.diagnostics).length || evidence.diagnostics?.issues?.length || 0;
    const diagnosticsSummaryLabel = evidence.loading
        ? '诊断中'
        : evidence.diagnosticsError
            ? '诊断失败'
            : canStart === false
                ? `阻断 ${blockerCount || 1}`
                : evidence.diagnostics?.ok
                    ? '启动可用'
                    : '诊断未知';
    const llmSummaryLabel = evidence.loading
        ? 'LLM ...'
        : evidence.diagnosticsError
            ? 'LLM ?'
            : `LLM ${evidence.diagnostics?.llm?.state || (evidence.diagnostics?.llm?.ok ? 'ready' : 'unknown')}`;
    const inputSummaryLabel = evidence.loading
        ? '输入 ...'
        : evidence.diagnosticsError
            ? '输入 ?'
            : `输入 ${evidence.diagnostics?.planning_input?.status || (evidence.diagnostics?.planning_input?.ok ? 'ready' : 'unknown')}`;
    return (_jsx("section", { className: "border-b border-white/10 bg-slate-950/45 px-4 py-2 text-xs text-slate-300", "data-testid": "pm-backend-evidence-strip", "aria-label": "PM backend evidence", children: _jsxs("details", { className: "group", children: [_jsxs("summary", { className: "flex min-w-0 cursor-pointer list-none flex-wrap items-center gap-2 outline-none [&::-webkit-details-marker]:hidden", children: [_jsx(Wrench, { className: "h-3.5 w-3.5 shrink-0 text-amber-300" }), _jsx("span", { className: "shrink-0 font-medium text-slate-100", children: "PM \u540E\u7AEF\u72B6\u6001" }), _jsx("span", { className: cn('shrink-0 rounded-full border px-2 py-0.5 text-[10px]', summaryTone), children: summaryLabel }), _jsx("span", { className: "shrink-0 rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", title: diagnosticsLabel, children: diagnosticsSummaryLabel }), _jsx("span", { className: "hidden shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-slate-400 lg:inline-flex", children: capabilityLabel }), _jsx("span", { className: "hidden shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-slate-400 xl:inline-flex", children: llmSummaryLabel }), _jsx("span", { className: "hidden shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-slate-400 2xl:inline-flex", children: inputSummaryLabel }), _jsxs("span", { className: "hidden shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-slate-400 xl:inline-flex", children: ["\u4E8B\u4EF6 ", llmEventCount] }), _jsx("span", { className: "ml-auto shrink-0 rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] text-slate-500 group-open:hidden", children: "\u8BE6\u60C5" }), _jsx("span", { className: "ml-auto hidden shrink-0 rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] text-slate-500 group-open:inline", children: "\u6536\u8D77" }), _jsx(Button, { variant: "ghost", size: "icon", onClick: (event) => {
                                event.preventDefault();
                                onRefresh();
                            }, disabled: evidence.loading || cacheClearing, title: "\u5237\u65B0 PM \u540E\u7AEF\u72B6\u6001", className: "h-7 w-7 shrink-0 text-slate-400 hover:bg-amber-500/10 hover:text-amber-300", children: _jsx(RefreshCw, { className: cn('h-3.5 w-3.5', evidence.loading && 'animate-spin') }) })] }), _jsxs("div", { className: "mt-2 grid gap-2 rounded-lg border border-white/10 bg-slate-950/50 p-2 md:grid-cols-2 xl:grid-cols-3", children: [_jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2 rounded-md bg-white/[0.025] px-2 py-1.5", children: [_jsx(Wrench, { className: "h-3.5 w-3.5 shrink-0 text-amber-300" }), _jsx("span", { className: "shrink-0 font-medium text-amber-100", children: "\u80FD\u529B" }), _jsx(EvidenceEndpointBadge, { endpoint: "/v2/roles/capabilities/pm?host_kind=electron_workbench", testId: "pm-capabilities-endpoint" }), evidence.loading ? (_jsx("span", { className: "text-slate-400", children: "\u8BFB\u53D6\u4E2D..." })) : evidence.capabilitiesError ? (_jsx("span", { className: "text-rose-300", children: evidence.capabilitiesError })) : (_jsx("span", { className: "max-w-[260px] truncate text-emerald-300", title: evidence.capabilities.join(', '), children: evidence.capabilities.length > 0 ? evidence.capabilities.slice(0, 5).join(', ') : 'none' }))] }), _jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2 rounded-md bg-white/[0.025] px-2 py-1.5", children: [_jsx(Activity, { className: "h-3.5 w-3.5 shrink-0 text-amber-300" }), _jsx("span", { className: "shrink-0 font-medium text-amber-100", children: "\u8BCA\u65AD" }), _jsx(EvidenceEndpointBadge, { endpoint: evidenceEndpoint('/v2/pm/diagnostics', workspace), testId: "pm-diagnostics-endpoint" }), evidence.loading ? (_jsx("span", { className: "text-slate-400", children: "\u8BFB\u53D6\u4E2D..." })) : evidence.diagnosticsError ? (_jsx("span", { className: "text-rose-300", children: evidence.diagnosticsError })) : (_jsx("span", { className: "max-w-[320px] truncate text-emerald-300", title: diagnosticsLabel, children: diagnosticsLabel }))] }), _jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2 rounded-md bg-white/[0.025] px-2 py-1.5", children: [_jsx(Brain, { className: "h-3.5 w-3.5 shrink-0 text-amber-300" }), _jsx("span", { className: "shrink-0 font-medium text-amber-100", children: "LLM \u4E8B\u4EF6" }), _jsx(EvidenceEndpointBadge, { endpoint: evidenceEndpoint('/v2/pm/llm-events?role=pm&limit=5', workspace), testId: "pm-llm-events-endpoint" }), evidence.loading ? (_jsx("span", { className: "text-slate-400", children: "\u8BFB\u53D6\u4E2D..." })) : evidence.llmEventsError ? (_jsx("span", { className: "text-rose-300", children: evidence.llmEventsError })) : (_jsxs("span", { className: "max-w-[260px] truncate text-emerald-300", children: ["events=", llmEventCount, latestLLMEvent ? ` · ${formatPmKernelEvent(latestLLMEvent)}` : ''] }))] }), _jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2 rounded-md bg-white/[0.025] px-2 py-1.5", children: [_jsx(Database, { className: "h-3.5 w-3.5 shrink-0 text-amber-300" }), _jsx("span", { className: "shrink-0 font-medium text-amber-100", children: "\u7F13\u5B58" }), _jsx(EvidenceEndpointBadge, { endpoint: "/v2/pm/cache-stats", testId: "pm-cache-endpoint" }), evidence.loading ? (_jsx("span", { className: "text-slate-400", children: "\u8BFB\u53D6\u4E2D..." })) : evidence.cacheError ? (_jsx("span", { className: "text-rose-300", children: evidence.cacheError })) : (_jsx("span", { className: "max-w-[260px] truncate text-emerald-300", children: cacheLabel })), _jsx(Button, { variant: "ghost", size: "icon", onClick: onClearCache, disabled: evidence.loading || cacheClearing, "data-testid": "pm-kernel-cache-clear", title: "\u6E05\u7A7A PM LLM \u7F13\u5B58", className: "h-6 w-6 text-slate-400 hover:bg-red-500/10 hover:text-red-300", children: cacheClearing ? (_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" })) : (_jsx(Trash2, { className: "h-3.5 w-3.5" })) }), cacheClearStatus ? (_jsx("span", { "data-testid": "pm-kernel-cache-clear-result", "data-endpoint": "/v2/pm/cache-clear", title: "/v2/pm/cache-clear", className: "truncate text-amber-200", children: cacheClearStatus })) : null] }), _jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2 rounded-md bg-white/[0.025] px-2 py-1.5", children: [_jsx(Coins, { className: "h-3.5 w-3.5 shrink-0 text-amber-300" }), _jsx("span", { className: "shrink-0 font-medium text-amber-100", children: "Token \u9884\u7B97" }), _jsx(EvidenceEndpointBadge, { endpoint: "/v2/pm/token-budget-stats", testId: "pm-token-budget-endpoint" }), evidence.loading ? (_jsx("span", { className: "text-slate-400", children: "\u8BFB\u53D6\u4E2D..." })) : evidence.tokenBudgetError ? (_jsx("span", { className: "text-rose-300", children: evidence.tokenBudgetError })) : (_jsx("span", { className: "max-w-[260px] truncate text-emerald-300", children: tokenBudgetLabel }))] })] })] }) }));
}
function normalizeSelectedTaskId(value) {
    if (typeof value === 'string') {
        const trimmed = value.trim();
        return trimmed || null;
    }
    if (typeof value === 'number' && Number.isFinite(value))
        return String(value);
    if (typeof value === 'bigint')
        return String(value);
    if (value && typeof value === 'object') {
        const record = value;
        return normalizeSelectedTaskId(record.id ?? record.task_id);
    }
    return null;
}
export function PMWorkspace({ tasks, pmState, pmRunning, pmTerminalStatus = null, pmStartBlockedReason = '', runtimeIssue = null, isStarting, isStopping = false, onBackToMain, onTogglePm, onRunPmOnce, workspace, executionLogs = [], llmStreamEvents = [], processStreamEvents = [], currentPhase = 'idle', factoryMode = false, qualityGate = null, taskTraceMap, llmRuntimeState, onOpenSettings, }) {
    const [activeView, setActiveView] = useState('tasks');
    const [selectedTaskId, setSelectedTaskId] = useState(null);
    const [selectedDocumentPath, setSelectedDocumentPath] = useState(null);
    const [showAIDialogue, setShowAIDialogue] = useState(true);
    const [showDiagnostics, setShowDiagnostics] = useState(false);
    const [runOnceStatusEvidence, setRunOnceStatusEvidence] = useState({
        triggered: false,
        loading: false,
        message: null,
        error: null,
    });
    const [toggleStatusEvidence, setToggleStatusEvidence] = useState({
        triggered: false,
        loading: false,
        message: null,
        error: null,
    });
    const [commandSnapshotTasks, setCommandSnapshotTasks] = useState([]);
    const [pmBackendEvidence, setPmBackendEvidence] = useState(EMPTY_PM_BACKEND_EVIDENCE);
    const [pmKernelCacheClearing, setPmKernelCacheClearing] = useState(false);
    const [pmKernelCacheClearStatus, setPmKernelCacheClearStatus] = useState('');
    useEffect(() => {
        setCommandSnapshotTasks([]);
    }, [workspace]);
    const loadPmBackendEvidence = useCallback(async () => {
        if (!workspace || factoryMode) {
            setPmBackendEvidence(EMPTY_PM_BACKEND_EVIDENCE);
            setPmKernelCacheClearStatus('');
            return;
        }
        setPmBackendEvidence((current) => ({
            ...current,
            loading: true,
        }));
        try {
            const [capabilityResult, diagnosticsResult, cacheResult, tokenBudgetResult, llmResult] = await Promise.all([
                getRoleCapabilities('pm', 'electron_workbench'),
                getPmStartupDiagnostics(workspace),
                getRoleKernelCacheStats('pm'),
                getRoleKernelTokenBudgetStats('pm'),
                getRoleKernelLLMEvents('pm', { role: 'pm', limit: 5, workspace }),
            ]);
            setPmBackendEvidence({
                loading: false,
                capabilities: capabilityResult.ok && capabilityResult.data
                    ? resolveRoleCapabilities(capabilityResult.data, 'electron_workbench').sort()
                    : [],
                capabilitiesError: capabilityResult.ok ? '' : capabilityResult.error || 'PM capabilities unavailable',
                diagnostics: diagnosticsResult.ok && diagnosticsResult.data ? diagnosticsResult.data : null,
                diagnosticsError: diagnosticsResult.ok ? '' : diagnosticsResult.error || 'PM diagnostics unavailable',
                cacheStats: cacheResult.ok && cacheResult.data ? cacheResult.data : null,
                cacheError: cacheResult.ok ? '' : cacheResult.error || 'PM cache stats unavailable',
                llmEvents: llmResult.ok && llmResult.data
                    ? { ...llmResult.data, events: Array.isArray(llmResult.data.events) ? llmResult.data.events : [] }
                    : null,
                llmEventsError: llmResult.ok ? '' : llmResult.error || 'PM LLM events unavailable',
                tokenBudgetStats: tokenBudgetResult.ok && tokenBudgetResult.data ? tokenBudgetResult.data : null,
                tokenBudgetError: tokenBudgetResult.ok ? '' : tokenBudgetResult.error || 'PM token budget unavailable',
            });
        }
        catch (error) {
            const message = error instanceof Error ? error.message : 'PM backend evidence unavailable';
            setPmBackendEvidence({
                ...EMPTY_PM_BACKEND_EVIDENCE,
                capabilitiesError: message,
                diagnosticsError: message,
                cacheError: message,
                llmEventsError: message,
                tokenBudgetError: message,
            });
        }
    }, [factoryMode, workspace]);
    useEffect(() => {
        void loadPmBackendEvidence();
    }, [loadPmBackendEvidence]);
    const llmRuntimeRefreshKey = [
        llmRuntimeState?.state || '',
        llmRuntimeState?.lastUpdated || '',
        ...(llmRuntimeState?.blockedRoles || []),
    ].join('|');
    useEffect(() => {
        if (!llmRuntimeState?.lastUpdated) {
            return;
        }
        void loadPmBackendEvidence();
    }, [llmRuntimeRefreshKey, llmRuntimeState?.lastUpdated, loadPmBackendEvidence]);
    const handleClearPmKernelCache = useCallback(async () => {
        setPmKernelCacheClearing(true);
        setPmKernelCacheClearStatus('');
        try {
            const result = await clearRoleKernelCache('pm');
            if (result.ok) {
                setPmKernelCacheClearStatus(result.data?.message || 'cleared');
                await loadPmBackendEvidence();
            }
            else {
                setPmKernelCacheClearStatus(result.error || 'clear failed');
            }
        }
        catch (error) {
            setPmKernelCacheClearStatus(error instanceof Error ? error.message : 'clear failed');
        }
        finally {
            setPmKernelCacheClearing(false);
        }
    }, [loadPmBackendEvidence]);
    const pmTaskEvidenceRows = useMemo(() => mergePmTaskEvidenceRows(tasks, commandSnapshotTasks), [commandSnapshotTasks, tasks]);
    // 用户手动切换视图的标记（避免自动切换覆盖用户选择）
    const userSwitchedViewRef = useRef(false);
    const lastPhaseRef = useRef('');
    const lastRealtimeEventCountRef = useRef(0);
    // 自动切换视图基于当前阶段
    useEffect(() => {
        if (!pmRunning || userSwitchedViewRef.current)
            return;
        const phaseConfig = PHASE_TO_VIEW[currentPhase] || PHASE_TO_VIEW['idle'];
        // 只有当阶段真正改变时才切换
        if (currentPhase !== lastPhaseRef.current) {
            lastPhaseRef.current = currentPhase;
            // 如果当前视图不是推荐的视图，则自动切换
            if (phaseConfig.view !== activeView) {
                setActiveView(phaseConfig.view);
            }
        }
    }, [currentPhase, pmRunning, activeView]);
    useEffect(() => {
        const eventCount = executionLogs.length + llmStreamEvents.length + processStreamEvents.length;
        const previousCount = lastRealtimeEventCountRef.current;
        lastRealtimeEventCountRef.current = eventCount;
        const runtimeActive = pmRunning;
        if (!runtimeActive || eventCount <= previousCount || eventCount <= 0 || userSwitchedViewRef.current)
            return;
        if (activeView !== 'activity') {
            setActiveView('activity');
        }
    }, [
        activeView,
        executionLogs.length,
        llmStreamEvents.length,
        pmRunning,
        processStreamEvents.length,
    ]);
    // 当用户手动点击导航时，记录用户偏好
    const handleViewChange = useCallback((view) => {
        userSwitchedViewRef.current = true;
        setActiveView(view);
    }, []);
    const handleTaskSelect = useCallback((taskId) => {
        userSwitchedViewRef.current = true;
        setSelectedTaskId(normalizeSelectedTaskId(taskId));
        setActiveView('tasks');
    }, []);
    const handleBackendPmTaskCreated = useCallback((task) => {
        setCommandSnapshotTasks((current) => {
            if (!task.id)
                return current;
            return mergePmTaskEvidenceRows([], [task, ...current.filter((item) => item.id !== task.id)]);
        });
    }, []);
    const pmLlmRuntimeReady = llmRuntimeState?.state === 'READY';
    const pmDiagnosticStartReason = useMemo(() => {
        const reason = formatPmStartupBlockReason(pmBackendEvidence.diagnostics);
        return pmLlmRuntimeReady && isLlmStartupBlockReason(reason) ? '' : reason;
    }, [pmBackendEvidence.diagnostics, pmLlmRuntimeReady]);
    const pmDiagnosticsAllowStart = pmBackendEvidence.diagnostics?.can_start === true;
    const pmDiagnosticsPending = Boolean(!factoryMode
        && workspace
        && (pmBackendEvidence.loading || (!pmBackendEvidence.diagnostics && !pmBackendEvidence.diagnosticsError)));
    const pendingLlmBlockReason = pmDiagnosticsPending && isLlmStartupBlockReason(pmStartBlockedReason);
    const externalPmStartBlockedReason = pmDiagnosticsAllowStart || pendingLlmBlockReason ? '' : pmStartBlockedReason;
    const observedPmRunning = pmRunning;
    const pmStatusSyncPending = false;
    const pmStartBlockReason = !observedPmRunning ? externalPmStartBlockedReason || pmDiagnosticStartReason : '';
    const pmTaskCreateDisabledReason = factoryMode
        ? '工厂模式下无法手动创建 PM 任务。'
        : !workspace
            ? '未选择工作区，无法创建 PM 任务。'
            : pmDiagnosticsPending
                ? 'PM 后端诊断中，请等待启动门禁确认。'
                : pmBackendEvidence.diagnosticsError
                    ? `PM 后端诊断不可用：${pmBackendEvidence.diagnosticsError}`
                    : pmStartBlockReason;
    const pmStarting = Boolean(isStarting);
    const pmStopping = Boolean(isStopping);
    const pmToggleBusyReason = pmStarting
        ? 'PM 正在启动，请等待状态回传。'
        : pmStopping
            ? 'PM 正在停止，请等待状态回传。'
            : toggleStatusEvidence.loading
                ? 'PM 命令提交中，请等待 runtime.v2 回传。'
                : '';
    const pmRunOnceBusyReason = pmStarting
        ? 'PM 正在启动，请等待状态回传。'
        : pmStopping
            ? 'PM 正在停止，请等待状态回传。'
            : runOnceStatusEvidence.loading
                ? 'PM 单次督办命令提交中，请等待 runtime.v2 回传。'
                : '';
    const pmRunOnceDisabledReason = factoryMode
        ? '工厂模式下无法使用此功能'
        : pmRunOnceBusyReason
            || (observedPmRunning ? 'PM 正在运行，不能同时触发单次督办。' : '')
            || pmStartBlockReason;
    const pmToggleDisabledReason = factoryMode
        ? '工厂模式下无法使用此功能'
        : pmToggleBusyReason
            || (pmStatusSyncPending ? 'PM 后端已在运行，等待主状态同步后再操作。' : '')
            || pmStartBlockReason;
    const handleRunPmOnce = useCallback(async () => {
        if (pmRunOnceBusyReason) {
            return;
        }
        if (pmStartBlockReason) {
            setRunOnceStatusEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: pmStartBlockReason,
            });
            return;
        }
        setRunOnceStatusEvidence({
            triggered: true,
            loading: true,
            message: null,
            error: null,
        });
        try {
            const accepted = await Promise.resolve(onRunPmOnce());
            setRunOnceStatusEvidence({
                triggered: true,
                loading: false,
                message: accepted === false ? '命令未被接受。' : PM_COMMAND_ACCEPTED_MESSAGE,
                error: accepted === false ? 'PM command was not accepted' : null,
            });
        }
        catch (error) {
            setRunOnceStatusEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: error instanceof Error ? error.message : 'PM status unavailable',
            });
        }
    }, [onRunPmOnce, pmRunOnceBusyReason, pmStartBlockReason]);
    const handleTogglePm = useCallback(async () => {
        if (pmToggleBusyReason) {
            return;
        }
        if (!observedPmRunning && pmStartBlockReason) {
            setToggleStatusEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: pmStartBlockReason,
            });
            return;
        }
        setToggleStatusEvidence({
            triggered: true,
            loading: true,
            message: null,
            error: null,
        });
        try {
            const accepted = await Promise.resolve(onTogglePm());
            setToggleStatusEvidence({
                triggered: true,
                loading: false,
                message: accepted === false ? '命令未被接受。' : PM_COMMAND_ACCEPTED_MESSAGE,
                error: accepted === false ? 'PM command was not accepted' : null,
            });
        }
        catch (error) {
            setToggleStatusEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: error instanceof Error ? error.message : 'PM status unavailable',
            });
        }
    }, [observedPmRunning, onTogglePm, pmStartBlockReason, pmToggleBusyReason]);
    const handleDocumentSelect = useCallback((path) => {
        userSwitchedViewRef.current = true;
        setSelectedDocumentPath(path);
        setActiveView('documents');
    }, []);
    const completedTasks = pmTaskEvidenceRows.filter(t => t.status === 'completed' || t.done).length;
    const totalTasks = pmTaskEvidenceRows.length;
    const progress = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
    // 实时任务统计
    const taskStats = {
        pending: pmTaskEvidenceRows.filter(t => !t.status || t.status === 'pending').length,
        running: pmTaskEvidenceRows.filter(t => String(t.status) === 'running' || t.status === 'in_progress').length,
        completed: completedTasks,
        blocked: pmTaskEvidenceRows.filter(t => t.status === 'blocked' || t.status === 'failed').length,
    };
    // 获取当前阶段信息
    const currentPhaseConfig = PHASE_TO_VIEW[currentPhase] || PHASE_TO_VIEW['idle'];
    // 获取当前正在执行的任务
    const currentTask = pmTaskEvidenceRows.find((task) => task.status === 'in_progress' || String(task.status) === 'running') ?? null;
    const pmStartBlocked = Boolean(pmStartBlockReason && !observedPmRunning);
    const pmRunOnceDisabled = Boolean(pmRunOnceDisabledReason);
    const pmToggleDisabled = Boolean(pmToggleDisabledReason);
    const pmRuntimeBanner = resolvePMRuntimeBanner({
        pmRunning: observedPmRunning || pmStarting || runOnceStatusEvidence.loading || toggleStatusEvidence.loading,
        pmStartBlockedReason: pmStartBlockReason,
        runtimeIssue,
        pmTerminalStatus,
    });
    const shouldShowSideAIDialogue = showAIDialogue && activeView !== 'workbench';
    return (_jsxs("div", { "data-testid": "pm-workspace", className: "flex flex-col h-full bg-gradient-to-br from-[var(--ink-indigo)] via-[rgba(28,18,48,0.8)] to-[rgba(14,20,40,0.95)] text-slate-100 overflow-hidden", children: [!factoryMode && (_jsxs("header", { className: "h-14 flex items-center justify-between px-4 border-b border-amber-500/20 bg-gradient-to-r from-slate-900 via-slate-900 to-amber-950/20", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsxs(Button, { variant: "ghost", size: "sm", onClick: onBackToMain, "data-testid": "pm-workspace-back", "aria-label": "\u8FD4\u56DE\u4E3B\u754C\u9762", className: "text-slate-400 hover:text-slate-100 hover:bg-white/5", children: [_jsx(ChevronLeft, { className: "w-4 h-4 mr-1" }), "\u8FD4\u56DE"] }), _jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: "w-8 h-8 rounded-lg bg-gradient-to-br from-amber-500 to-amber-700 flex items-center justify-center shadow-lg shadow-amber-500/20", children: _jsx(Crown, { className: "w-4 h-4 text-amber-100" }) }), _jsxs("div", { children: [_jsx("h1", { className: "text-sm font-semibold text-amber-100", children: "PM" }), _jsx("p", { className: "text-[10px] text-amber-500/70 uppercase tracking-wider", children: "PM Console" })] })] })] }), _jsxs("div", { className: "flex items-center gap-4", children: [_jsxs("div", { className: "flex items-center gap-1 px-2 py-1 rounded-lg bg-white/5 border border-white/10", children: [_jsx(Clock, { className: "w-3.5 h-3.5 text-slate-400" }), _jsx("span", { className: "text-xs text-slate-400", children: "\u5F85\u529E:" }), _jsx("span", { className: "text-xs font-mono text-slate-300 min-w-[20px] text-center", children: taskStats.pending }), _jsx("span", { className: "text-slate-600", children: "|" }), _jsx(Zap, { className: "w-3.5 h-3.5 text-amber-400" }), _jsx("span", { className: "text-xs text-amber-400 font-medium min-w-[20px] text-center", children: taskStats.running }), _jsx("span", { className: "text-slate-600", children: "|" }), _jsx(CheckCircle2, { className: "w-3.5 h-3.5 text-emerald-400" }), _jsx("span", { className: "text-xs text-emerald-400 font-medium min-w-[20px] text-center", children: taskStats.completed }), taskStats.blocked > 0 && (_jsxs(_Fragment, { children: [_jsx("span", { className: "text-slate-600", children: "|" }), _jsx(AlertCircle, { className: "w-3.5 h-3.5 text-red-400" }), _jsx("span", { className: "text-xs text-red-400 font-medium min-w-[20px] text-center", children: taskStats.blocked })] }))] }), observedPmRunning && (_jsxs("div", { className: cn("flex items-center gap-2 px-3 py-1.5 rounded-lg border transition-all duration-300", currentPhaseConfig.color.replace('text-', 'bg-').replace('400', '500/20'), currentPhaseConfig.color), children: [currentPhaseConfig.icon, _jsx("span", { className: "text-xs font-medium", children: currentPhaseConfig.label })] })), _jsxs("div", { className: "flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10", children: [_jsx(ScrollText, { className: "w-4 h-4 text-amber-500/70" }), _jsx("span", { className: "text-xs text-slate-400", children: "\u8FDB\u5EA6" }), _jsxs("span", { className: "text-xs font-mono text-amber-400", children: [completedTasks, "/", totalTasks] }), _jsx("div", { className: "w-20 h-1.5 rounded-full bg-slate-800 overflow-hidden", children: _jsx("div", { className: "h-full rounded-full bg-gradient-to-r from-amber-500 to-amber-400 transition-all duration-500", style: { width: `${progress}%` } }) }), _jsxs("span", { className: "text-xs font-mono text-slate-500", children: [progress, "%"] })] }), currentTask && observedPmRunning && (_jsxs("div", { className: "flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/20 max-w-[250px] animate-pulse", children: [_jsx(Zap, { className: "w-3.5 h-3.5 text-amber-400 flex-shrink-0 animate-pulse" }), _jsxs("span", { className: "text-xs text-amber-300 truncate", title: currentTask.title, children: ["\u6B63\u5728\u6267\u884C: ", currentTask.title] })] }))] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Button, { variant: "ghost", size: "icon", onClick: () => setShowDiagnostics(true), className: "text-slate-400 hover:text-amber-400 hover:bg-amber-500/10", title: "\u8FD0\u884C\u8BCA\u65AD", children: _jsx(Stethoscope, { className: "w-4 h-4" }) }), _jsx("div", { className: "w-px h-6 bg-white/10" }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void handleRunPmOnce(); }, "data-testid": "pm-workspace-run-once", disabled: pmRunOnceDisabled, title: pmRunOnceDisabledReason || undefined, className: "text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 border border-amber-500/20", children: [pmStarting || pmStopping || runOnceStatusEvidence.loading ? _jsx(Loader2, { className: "w-3.5 h-3.5 mr-1.5 animate-spin" }) : _jsx(Sparkles, { className: "w-3.5 h-3.5 mr-1.5" }), "\u5355\u6B21 Run"] }), _jsx(Button, { variant: observedPmRunning ? 'default' : 'outline', size: "sm", onClick: () => { void handleTogglePm(); }, "data-testid": "pm-workspace-toggle", disabled: pmToggleDisabled, title: pmToggleDisabledReason || undefined, className: cn(observedPmRunning
                                    ? 'bg-amber-600 hover:bg-amber-700 text-white'
                                    : 'border-amber-500/30 text-amber-400 hover:bg-amber-500/10'), children: pmStarting || pmStopping || toggleStatusEvidence.loading ? (_jsxs(_Fragment, { children: [_jsx(Loader2, { className: "w-3.5 h-3.5 mr-1.5 animate-spin" }), pmStopping ? '停止中' : pmStarting ? '启动中' : '确认中'] })) : observedPmRunning ? (_jsxs(_Fragment, { children: [_jsx("div", { className: "w-1.5 h-1.5 rounded-full bg-white animate-pulse mr-2" }), "\u8FD0\u884C\u4E2D"] })) : (_jsxs(_Fragment, { children: [_jsx(CheckCircle2, { className: "w-3.5 h-3.5 mr-1.5" }), "\u542F\u52A8"] })) }), _jsx("div", { className: "w-px h-6 bg-white/10 mx-2" }), _jsx(Button, { variant: "ghost", size: "icon", onClick: () => setShowAIDialogue(!showAIDialogue), className: cn('text-slate-400 hover:text-slate-100', showAIDialogue && 'text-amber-400 bg-amber-500/10'), children: _jsx(MessageSquare, { className: "w-4 h-4" }) }), _jsx(Button, { variant: "ghost", size: "icon", onClick: onOpenSettings, disabled: !onOpenSettings, className: "text-slate-400 hover:text-slate-100", title: "\u7CFB\u7EDF\u914D\u7F6E", children: _jsx(Settings, { className: "w-4 h-4" }) })] })] })), !factoryMode && (_jsx(PMBackendEvidenceStrip, { evidence: pmBackendEvidence, cacheClearing: pmKernelCacheClearing, cacheClearStatus: pmKernelCacheClearStatus, onRefresh: () => { void loadPmBackendEvidence(); }, onClearCache: () => { void handleClearPmKernelCache(); }, workspace: workspace })), runOnceStatusEvidence.triggered && (_jsx("section", { className: "border-b border-white/10 bg-slate-950/45 px-4 py-1.5 text-xs text-slate-300", "data-testid": "pm-run-once-status-evidence", children: _jsxs("details", { className: "group", children: [_jsxs("summary", { className: "flex cursor-pointer list-none items-center gap-2 outline-none", children: [_jsx(Clock, { className: "h-3.5 w-3.5 shrink-0 text-slate-500" }), _jsx("span", { className: "font-medium text-slate-200", children: "PM \u5355\u6B21\u547D\u4EE4\u56DE\u6267" }), _jsx("span", { className: "min-w-0 truncate text-slate-500", children: runOnceStatusEvidence.loading
                                        ? '正在提交命令...'
                                        : runOnceStatusEvidence.error || runOnceStatusEvidence.message || PM_COMMAND_ACCEPTED_MESSAGE }), _jsx("span", { className: "ml-auto text-[10px] text-slate-500 group-open:hidden", children: "\u8BE6\u60C5" }), _jsx("span", { className: "ml-auto hidden text-[10px] text-slate-500 group-open:inline", children: "\u6536\u8D77" })] }), _jsxs("div", { className: "mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-white/10 bg-slate-950/55 px-2 py-1.5", children: [_jsx("span", { className: "font-medium text-amber-100", children: "PM run_once command" }), _jsx(EvidenceEndpointBadge, { endpoint: PM_RUNTIME_PUSH_ENDPOINT, testId: "pm-run-once-status-endpoint" }), runOnceStatusEvidence.loading ? (_jsx("span", { className: "text-slate-400", children: "\u6B63\u5728\u63D0\u4EA4\u547D\u4EE4..." })) : runOnceStatusEvidence.error ? (_jsx("span", { className: "text-rose-300", children: runOnceStatusEvidence.error })) : (_jsx("span", { className: "text-emerald-300", children: runOnceStatusEvidence.message || PM_COMMAND_ACCEPTED_MESSAGE }))] })] }) })), toggleStatusEvidence.triggered && (_jsx("section", { className: "border-b border-white/10 bg-slate-950/45 px-4 py-1.5 text-xs text-slate-300", "data-testid": "pm-toggle-status-evidence", children: _jsxs("details", { className: "group", children: [_jsxs("summary", { className: "flex cursor-pointer list-none items-center gap-2 outline-none", children: [_jsx(Activity, { className: "h-3.5 w-3.5 shrink-0 text-slate-500" }), _jsx("span", { className: "font-medium text-slate-200", children: "PM \u542F\u505C\u547D\u4EE4\u56DE\u6267" }), _jsx("span", { className: "min-w-0 truncate text-slate-500", children: toggleStatusEvidence.loading
                                        ? '正在提交命令...'
                                        : toggleStatusEvidence.error || toggleStatusEvidence.message || PM_COMMAND_ACCEPTED_MESSAGE }), _jsx("span", { className: "ml-auto text-[10px] text-slate-500 group-open:hidden", children: "\u8BE6\u60C5" }), _jsx("span", { className: "ml-auto hidden text-[10px] text-slate-500 group-open:inline", children: "\u6536\u8D77" })] }), _jsxs("div", { className: "mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-white/10 bg-slate-950/55 px-2 py-1.5", children: [_jsx("span", { className: "font-medium text-amber-100", children: "PM toggle command" }), _jsx(EvidenceEndpointBadge, { endpoint: PM_RUNTIME_PUSH_ENDPOINT, testId: "pm-toggle-status-endpoint" }), toggleStatusEvidence.loading ? (_jsx("span", { className: "text-slate-400", children: "\u6B63\u5728\u63D0\u4EA4\u547D\u4EE4..." })) : toggleStatusEvidence.error ? (_jsx("span", { className: "text-rose-300", children: toggleStatusEvidence.error })) : (_jsx("span", { className: "text-emerald-300", children: toggleStatusEvidence.message || PM_COMMAND_ACCEPTED_MESSAGE }))] })] }) })), (tasks.length > 0 || commandSnapshotTasks.length > 0) && (_jsx("section", { className: "border-b border-white/10 bg-slate-950/45 px-4 py-1.5 text-xs text-slate-300", "data-testid": "pm-task-backend-evidence", children: _jsxs("details", { className: "group", children: [_jsxs("summary", { className: "flex cursor-pointer list-none items-center gap-2 outline-none", children: [_jsx(ListTodo, { className: "h-3.5 w-3.5 shrink-0 text-slate-500" }), _jsx("span", { className: "font-medium text-slate-200", children: "\u4EFB\u52A1\u5408\u540C\u6765\u6E90" }), _jsx("span", { className: cn('rounded-full border px-2 py-0.5 text-[10px]', 'border-slate-500/25 bg-slate-500/10 text-slate-300'), children: "runtime push" }), _jsxs("span", { className: "min-w-0 truncate text-slate-500", children: ["runtime ", tasks.length, " \u00B7 command snapshot ", commandSnapshotTasks.length, " \u00B7 visible ", pmTaskEvidenceRows.length] }), _jsx("span", { className: "ml-auto text-[10px] text-slate-500 group-open:hidden", children: "\u8BE6\u60C5" }), _jsx("span", { className: "ml-auto hidden text-[10px] text-slate-500 group-open:inline", children: "\u6536\u8D77" })] }), _jsxs("div", { className: "mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-white/10 bg-slate-950/55 px-2 py-1.5", children: [_jsx("span", { className: "font-medium text-amber-100", children: "PM runtime task evidence" }), _jsx(EvidenceEndpointBadge, { endpoint: "/v2/pm/tasks", testId: "pm-task-list-evidence-endpoint" }), _jsxs(_Fragment, { children: [_jsxs("span", { className: "text-slate-400", children: ["runtime=", tasks.length] }), _jsxs("span", { className: "text-slate-400", children: ["command_snapshot=", commandSnapshotTasks.length] }), _jsxs("span", { className: "text-slate-400", children: ["visible=", pmTaskEvidenceRows.length] })] })] })] }) })), pmRuntimeBanner && (_jsx("div", { "data-testid": "pm-runtime-terminal-banner", className: cn("mx-4 mt-3 rounded-lg border px-3 py-2.5 text-sm shadow-lg", pmRuntimeBanner.severity === 'error'
                    ? "border-red-500/30 bg-red-950/40 text-red-100"
                    : "border-amber-500/30 bg-amber-950/35 text-amber-100"), children: _jsxs("div", { className: "flex items-start gap-2", children: [_jsx(AlertCircle, { className: cn("mt-0.5 size-4 shrink-0", pmRuntimeBanner.severity === 'error' ? "text-red-300" : "text-amber-300") }), _jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx("span", { className: "font-medium", children: pmRuntimeBanner.title }), pmRuntimeBanner.code ? (_jsx("span", { "data-testid": "pm-runtime-error-code", className: "rounded border border-red-400/25 bg-red-500/10 px-1.5 py-0.5 font-mono text-[10px] text-red-100/85", children: pmRuntimeBanner.code })) : null] }), pmRuntimeBanner.rootCause ? (_jsxs("div", { "data-testid": "pm-runtime-root-cause", className: "mt-2 rounded-md border border-red-400/20 bg-red-500/10 px-2.5 py-2 text-xs", children: [_jsxs("div", { className: "mb-1 text-[10px] font-semibold uppercase tracking-wide text-red-200/75", children: ["\u6839\u56E0 \u00B7 ", pmRuntimeBanner.rootCause.role] }), _jsx("div", { className: "whitespace-pre-line text-red-50/90", children: pmRuntimeBanner.rootCause.detail })] })) : (_jsx("div", { className: "mt-1 whitespace-pre-line text-xs opacity-85", children: pmRuntimeBanner.detail })), pmRuntimeBanner.cascades && pmRuntimeBanner.cascades.length > 0 ? (_jsxs("div", { "data-testid": "pm-runtime-cascade", className: "mt-2 rounded-md border border-amber-300/[0.15] bg-amber-500/10 px-2.5 py-2 text-xs text-amber-50/85", children: [_jsx("div", { className: "mb-1 text-[10px] font-semibold uppercase tracking-wide text-amber-200/75", children: "\u7EA7\u8054\u963B\u65AD" }), _jsx("div", { className: "space-y-1", children: pmRuntimeBanner.cascades.map((item) => (_jsxs("div", { className: "grid gap-2 sm:grid-cols-[112px_minmax(0,1fr)]", children: [_jsx("span", { className: "font-medium text-amber-100", children: item.role }), _jsx("span", { className: "min-w-0 break-words", children: item.detail })] }, `${item.role}-${item.detail}`))) })] })) : null, pmRuntimeBanner.startBlocker ? (_jsxs("div", { "data-testid": "pm-runtime-start-blocker", className: "mt-2 rounded-md border border-white/10 bg-slate-950/35 px-2 py-1.5 text-[11px] text-slate-300", children: ["\u5F53\u524D\u542F\u52A8\u95E8\u7981: ", pmRuntimeBanner.startBlocker] })) : null, pmRuntimeBanner.refs.length > 0 && (_jsx("div", { className: "mt-1.5 space-y-0.5 font-mono text-[10px] opacity-65", children: pmRuntimeBanner.refs.map((ref) => (_jsx("div", { className: "truncate", title: ref, children: ref }, ref))) }))] }), pmStartBlocked && onOpenSettings && (_jsxs(Button, { variant: "outline", size: "sm", onClick: onOpenSettings, className: "shrink-0 border-amber-400/30 text-amber-100 hover:bg-amber-500/10", children: [_jsx(Settings, { className: "mr-1.5 size-3.5" }), "LLM \u8BBE\u7F6E"] }))] }) })), _jsxs("div", { className: "flex-1 flex overflow-hidden", children: [_jsxs("nav", { className: "w-14 flex flex-col items-center py-4 gap-2 border-r border-white/5 bg-slate-950/50", children: [_jsx(NavButton, { icon: _jsx(ListTodo, { className: "w-4 h-4" }), label: "\u4EFB\u52A1", active: activeView === 'tasks', onClick: () => handleViewChange('tasks') }), _jsx(NavButton, { icon: _jsx(Activity, { className: "w-4 h-4" }), label: "\u5B9E\u65F6", active: activeView === 'activity', onClick: () => handleViewChange('activity') }), _jsx(NavButton, { icon: _jsx(FileText, { className: "w-4 h-4" }), label: "\u6587\u6863", active: activeView === 'documents', onClick: () => handleViewChange('documents') }), _jsx(NavButton, { icon: _jsx(ScrollText, { className: "w-4 h-4" }), label: "\u9700\u6C42", active: activeView === 'requirements', onClick: () => handleViewChange('requirements') }), _jsx(NavButton, { icon: _jsx(History, { className: "w-4 h-4" }), label: "\u5386\u53F2", active: activeView === 'history', onClick: () => handleViewChange('history') }), _jsx(NavButton, { icon: _jsx(BarChart3, { className: "w-4 h-4" }), label: "\u7EDF\u8BA1", active: activeView === 'analytics', onClick: () => handleViewChange('analytics') }), _jsx(NavButton, { icon: _jsx(GitBranch, { className: "w-4 h-4" }), label: "\u7F16\u6392", active: activeView === 'workbench', onClick: () => handleViewChange('workbench') })] }), _jsxs(PanelGroup, { direction: "horizontal", className: "flex-1", children: [_jsx(Panel, { defaultSize: shouldShowSideAIDialogue ? 65 : 85, minSize: 40, children: _jsxs("div", { className: "h-full overflow-hidden", children: [activeView === 'tasks' && (_jsxs("div", { className: "flex h-full min-h-0 flex-col", children: [qualityGate ? (_jsx("div", { className: "shrink-0 border-b border-white/10 bg-slate-950/35 p-3", children: _jsx(QualityGateCard, { data: qualityGate, className: "rounded-lg" }) })) : null, _jsx("div", { className: "min-h-0 flex-1", children: _jsx(PMTaskPanel, { tasks: pmTaskEvidenceRows, selectedTaskId: selectedTaskId, onTaskSelect: handleTaskSelect, onTaskCreated: handleBackendPmTaskCreated, pmRunning: observedPmRunning, taskTraceMap: taskTraceMap, workspace: workspace, createDisabledReason: pmTaskCreateDisabledReason }) })] })), activeView === 'activity' && (_jsx(RealtimeActivityPanel, { executionLogs: executionLogs, llmStreamEvents: llmStreamEvents, processStreamEvents: processStreamEvents, currentPhase: currentPhase, isRunning: observedPmRunning, role: "pm" })), activeView === 'documents' && (_jsx(PMDocumentPanel, { workspace: workspace, selectedPath: selectedDocumentPath, onDocumentSelect: handleDocumentSelect })), activeView === 'requirements' && (_jsx(PMRequirementsPanel, { workspace: workspace })), activeView === 'history' && (_jsx(PMHistoryPanel, { pmState: pmState, workspace: workspace })), activeView === 'analytics' && (_jsx(PMAnalyticsPanel, { tasks: pmTaskEvidenceRows })), activeView === 'workbench' && (_jsx(PMWorkbenchPanel, { pmRunning: observedPmRunning, workspace: workspace, taskCount: totalTasks, hostKind: "electron_workbench", attachmentMode: "isolated" }))] }) }), shouldShowSideAIDialogue && (_jsxs(_Fragment, { children: [_jsx(PanelResizeHandle, { className: "w-1 bg-white/5 hover:bg-amber-500/30 transition-colors" }), _jsx(Panel, { defaultSize: 35, minSize: 25, maxSize: 50, children: _jsx(PMAIDialoguePanel, { pmRunning: observedPmRunning, workspace: workspace, taskCount: totalTasks, selectedTaskId: selectedTaskId, interactionBlockedReason: pmStartBlockReason }) })] }))] })] }), _jsx(PMStatusBar, { pmRunning: observedPmRunning, taskCount: totalTasks, completedCount: completedTasks, iteration: pmState?.pm_iteration }), _jsx(PMDiagnosticsPanel, { isOpen: showDiagnostics, onClose: () => setShowDiagnostics(false), workspace: workspace })] }));
}
function NavButton({ icon, label, active, onClick }) {
    return (_jsxs("button", { onClick: onClick, "aria-label": `切换到${label}`, "data-testid": `pm-nav-${label}`, className: cn('w-10 h-10 cursor-pointer rounded-xl flex flex-col items-center justify-center gap-0.5 transition-all duration-200', active
            ? 'bg-amber-500/[0.15] text-amber-400 shadow-lg shadow-amber-500/10'
            : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'), title: label, children: [icon, _jsx("span", { className: "text-[8px] font-medium", children: label })] }));
}
function requirementRecord(requirement) {
    return requirement;
}
function requirementMetadata(requirement) {
    const metadata = requirementRecord(requirement).metadata;
    return metadata && typeof metadata === 'object' && !Array.isArray(metadata)
        ? metadata
        : {};
}
function readRequirementValue(requirement, keys) {
    const record = requirementRecord(requirement);
    const metadata = requirementMetadata(requirement);
    for (const key of keys) {
        const directValue = record[key];
        if (directValue !== undefined && directValue !== null)
            return directValue;
        const metadataValue = metadata[key];
        if (metadataValue !== undefined && metadataValue !== null)
            return metadataValue;
    }
    return undefined;
}
function readRequirementString(requirement, keys) {
    const value = readRequirementValue(requirement, keys);
    if (typeof value === 'string')
        return value.trim();
    if (typeof value === 'number' && Number.isFinite(value))
        return String(value);
    if (typeof value === 'boolean')
        return String(value);
    return '';
}
function requirementStringList(value) {
    if (!Array.isArray(value)) {
        const token = typeof value === 'string' ? value.trim() : '';
        return token ? [token] : [];
    }
    return value
        .map((item) => {
        if (typeof item === 'string')
            return item.trim();
        if (item && typeof item === 'object') {
            const record = item;
            return String(record.description || record.title || record.name || record.path || record.id || '').trim();
        }
        return String(item || '').trim();
    })
        .filter(Boolean);
}
function requirementApiId(requirement) {
    return readRequirementString(requirement, ['id', 'req_id', 'requirement_id']);
}
function requirementRowKey(requirement, index) {
    return requirementApiId(requirement) || readRequirementString(requirement, ['title', 'subject', 'name']) || `requirement-${index}`;
}
function requirementTitle(requirement) {
    return readRequirementString(requirement, ['title', 'subject', 'name']) || requirementApiId(requirement) || 'Untitled requirement';
}
function requirementStatus(requirement) {
    return readRequirementString(requirement, ['status', 'state']) || 'unknown';
}
function requirementPriority(requirement) {
    return readRequirementString(requirement, ['priority']) || 'unset';
}
function requirementSource(requirement) {
    return readRequirementString(requirement, ['source_doc', 'sourceDoc', 'source', 'path']);
}
function requirementAcceptanceCriteria(requirement) {
    return requirement
        ? requirementStringList(readRequirementValue(requirement, ['acceptance_criteria', 'acceptanceCriteria', 'criteria']))
        : [];
}
function requirementRelatedTasks(requirement) {
    return requirement
        ? requirementStringList(readRequirementValue(requirement, ['related_task_ids', 'relatedTaskIds', 'task_ids', 'tasks']))
        : [];
}
function PMRequirementsPanel({ workspace }) {
    const [requirements, setRequirements] = useState([]);
    const [selectedRequirementId, setSelectedRequirementId] = useState(null);
    const [selectedRequirement, setSelectedRequirement] = useState(null);
    const [isLoadingRequirements, setIsLoadingRequirements] = useState(false);
    const [requirementsError, setRequirementsError] = useState('');
    const [isLoadingRequirementDetail, setIsLoadingRequirementDetail] = useState(false);
    const [requirementDetailError, setRequirementDetailError] = useState('');
    const loadRequirements = useCallback(async () => {
        setIsLoadingRequirements(true);
        setRequirementsError('');
        try {
            const result = await listPmRequirements({ limit: 100, offset: 0, workspace });
            if (!result.ok || !result.data) {
                throw new Error(result.error || 'PM requirements unavailable');
            }
            const rows = Array.isArray(result.data.requirements)
                ? result.data.requirements
                : Array.isArray(result.data.items)
                    ? result.data.items
                    : [];
            setRequirements(rows);
            const firstRequirementId = rows.map(requirementApiId).find(Boolean) || null;
            setSelectedRequirementId((currentId) => {
                if (currentId && rows.some((requirement) => requirementApiId(requirement) === currentId)) {
                    return currentId;
                }
                return firstRequirementId;
            });
            if (!firstRequirementId) {
                setSelectedRequirement(null);
            }
        }
        catch (error) {
            setRequirements([]);
            setSelectedRequirement(null);
            setSelectedRequirementId(null);
            setRequirementsError(error instanceof Error ? error.message : 'PM requirements unavailable');
        }
        finally {
            setIsLoadingRequirements(false);
        }
    }, [workspace]);
    useEffect(() => {
        void loadRequirements();
    }, [loadRequirements]);
    useEffect(() => {
        if (!selectedRequirementId) {
            setSelectedRequirement(null);
            setRequirementDetailError('');
            setIsLoadingRequirementDetail(false);
            return;
        }
        let cancelled = false;
        setIsLoadingRequirementDetail(true);
        setRequirementDetailError('');
        void getPmRequirement(selectedRequirementId, workspace)
            .then((result) => {
            if (cancelled)
                return;
            if (!result.ok || !result.data) {
                throw new Error(result.error || 'PM requirement detail unavailable');
            }
            setSelectedRequirement(result.data);
        })
            .catch((error) => {
            if (cancelled)
                return;
            setSelectedRequirement(null);
            setRequirementDetailError(error instanceof Error ? error.message : 'PM requirement detail unavailable');
        })
            .finally(() => {
            if (!cancelled) {
                setIsLoadingRequirementDetail(false);
            }
        });
        return () => {
            cancelled = true;
        };
    }, [selectedRequirementId, workspace]);
    const selectedListRequirement = useMemo(() => requirements.find((requirement) => requirementApiId(requirement) === selectedRequirementId) || null, [requirements, selectedRequirementId]);
    const detailRequirement = selectedRequirement || selectedListRequirement;
    const matrixRequirements = useMemo(() => {
        const selectedId = selectedRequirement ? requirementApiId(selectedRequirement) : '';
        if (!selectedId) {
            return requirements;
        }
        return requirements.map((requirement) => (requirementApiId(requirement) === selectedId
            ? { ...requirement, ...selectedRequirement }
            : requirement));
    }, [requirements, selectedRequirement]);
    const acceptanceCriteria = requirementAcceptanceCriteria(detailRequirement);
    const relatedTasks = requirementRelatedTasks(detailRequirement);
    const source = detailRequirement ? requirementSource(detailRequirement) : '';
    const detailEndpoint = selectedRequirementId ? `/v2/pm/requirements/${selectedRequirementId}` : '/v2/pm/requirements/{id}';
    return (_jsxs("div", { "data-testid": "pm-requirements-panel", className: "flex h-full min-h-0 flex-col p-6", children: [_jsxs("div", { className: "mb-4 flex items-center justify-between gap-3", children: [_jsxs("div", { children: [_jsx("h2", { className: "text-lg font-semibold text-slate-100", children: "\u9700\u6C42\u8FFD\u8E2A" }), _jsxs("div", { className: "mt-1 flex items-center gap-2 text-xs text-slate-500", children: [_jsx("span", { children: "\u6765\u81EA PM \u9700\u6C42\u5408\u540C\u63A5\u53E3" }), _jsx(EvidenceEndpointBadge, { endpoint: "/v2/pm/requirements", testId: "pm-requirements-endpoint" })] })] }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void loadRequirements(); }, disabled: isLoadingRequirements, "data-testid": "pm-requirements-refresh", className: "h-8 px-2 text-xs text-slate-400 hover:bg-white/5 hover:text-slate-100", children: [_jsx(RefreshCw, { className: cn('mr-1.5 h-3.5 w-3.5', isLoadingRequirements && 'animate-spin') }), "\u5237\u65B0"] })] }), requirementsError ? (_jsx("div", { "data-testid": "pm-requirements-error", className: "mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-200", children: requirementsError })) : null, _jsxs("section", { "data-testid": "pm-requirement-matrix", className: "mb-3 shrink-0 overflow-hidden rounded-lg border border-white/10 bg-slate-950/35", children: [_jsxs("div", { className: "flex h-9 items-center justify-between border-b border-white/10 px-3 text-xs", children: [_jsx("div", { className: "font-medium text-slate-200", children: "\u9700\u6C42\u77E9\u9635" }), _jsxs("span", { className: "rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: [matrixRequirements.length, " rows"] })] }), _jsx("div", { className: "max-h-40 overflow-auto", children: matrixRequirements.length === 0 ? (_jsx("div", { "data-testid": "pm-requirement-matrix-empty", className: "px-3 py-4 text-xs text-slate-500", children: "\u6682\u65E0\u9700\u6C42\u77E9\u9635\u6570\u636E" })) : (_jsxs("div", { className: "min-w-[720px] divide-y divide-white/5", children: [_jsxs("div", { className: "grid grid-cols-[1.2fr_0.75fr_1.2fr_1fr] gap-2 px-3 py-2 text-[10px] uppercase text-slate-500", children: [_jsx("span", { children: "Requirement" }), _jsx("span", { children: "Source" }), _jsx("span", { children: "Acceptance" }), _jsx("span", { children: "Related Tasks" })] }), matrixRequirements.slice(0, 100).map((requirement, index) => {
                                    const apiId = requirementApiId(requirement);
                                    const matrixAcceptance = requirementAcceptanceCriteria(requirement);
                                    const matrixTasks = requirementRelatedTasks(requirement);
                                    return (_jsxs("button", { type: "button", "data-testid": "pm-requirement-matrix-row", "data-requirement-id": apiId || '', onClick: () => apiId && setSelectedRequirementId(apiId), disabled: !apiId, className: cn('grid w-full grid-cols-[1.2fr_0.75fr_1.2fr_1fr] gap-2 px-3 py-2 text-left text-xs transition-colors', apiId && apiId === selectedRequirementId
                                            ? 'bg-amber-500/10 text-slate-100'
                                            : 'text-slate-300 hover:bg-white/5', !apiId && 'cursor-not-allowed opacity-60'), children: [_jsxs("span", { className: "min-w-0", children: [_jsx("span", { className: "block truncate font-medium", children: requirementTitle(requirement) }), _jsx("span", { className: "mt-0.5 block truncate font-mono text-[10px] text-slate-500", children: apiId || 'no-id' })] }), _jsx("span", { "data-testid": "pm-requirement-matrix-source", className: "min-w-0 truncate font-mono text-[11px] text-slate-400", children: requirementSource(requirement) || 'unlinked' }), _jsx("span", { "data-testid": "pm-requirement-matrix-acceptance", className: "min-w-0 truncate text-slate-300", children: matrixAcceptance.length > 0 ? matrixAcceptance.join(' · ') : '未记录验收条件' }), _jsx("span", { "data-testid": "pm-requirement-matrix-related-task", className: "min-w-0 truncate font-mono text-[11px] text-amber-200", children: matrixTasks.length > 0 ? matrixTasks.join(', ') : '未关联 PM 任务' })] }, `matrix-${requirementRowKey(requirement, index)}`));
                                })] })) })] }), _jsxs("div", { className: "grid min-h-0 flex-1 gap-3 overflow-hidden xl:grid-cols-[minmax(260px,0.42fr)_minmax(0,0.58fr)]", children: [_jsxs("section", { className: "min-h-0 rounded-lg border border-white/10 bg-white/5", children: [_jsxs("div", { className: "flex h-10 items-center justify-between border-b border-white/10 px-3 text-xs text-slate-400", children: [_jsx(EvidenceEndpointBadge, { endpoint: "/v2/pm/requirements", testId: "pm-requirements-list-endpoint" }), _jsx("span", { "data-testid": "pm-requirements-count", className: "rounded border border-white/10 bg-slate-950/40 px-1.5 py-0.5 text-[10px] text-slate-300", children: requirements.length })] }), _jsx("div", { "data-testid": "pm-requirements-list", className: "max-h-full space-y-1 overflow-auto p-2", children: isLoadingRequirements && requirements.length === 0 ? (_jsxs("div", { className: "flex items-center gap-2 px-2 py-4 text-xs text-slate-500", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }), "\u52A0\u8F7D\u9700\u6C42..."] })) : requirements.length === 0 ? (_jsx("div", { className: "px-2 py-4 text-xs text-slate-500", children: "\u6682\u65E0\u9700\u6C42\u5408\u540C" })) : (requirements.slice(0, 100).map((requirement, index) => {
                                    const apiId = requirementApiId(requirement);
                                    const sourcePath = requirementSource(requirement);
                                    return (_jsxs("button", { type: "button", "data-testid": "pm-requirement-row", onClick: () => apiId && setSelectedRequirementId(apiId), disabled: !apiId, className: cn('w-full rounded-md border px-2 py-2 text-left text-xs transition-colors', apiId && apiId === selectedRequirementId
                                            ? 'border-amber-400/30 bg-amber-500/10 text-slate-100'
                                            : 'border-white/5 bg-slate-950/35 text-slate-300 hover:border-white/[0.15] hover:bg-white/5', !apiId && 'cursor-not-allowed opacity-60'), children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "min-w-0 truncate font-medium", children: requirementTitle(requirement) }), _jsx("span", { className: "shrink-0 rounded bg-slate-900/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: apiId || 'no-id' })] }), _jsxs("div", { className: "mt-1 flex items-center gap-2 text-[10px] text-slate-500", children: [_jsx("span", { className: "rounded bg-cyan-500/10 px-1.5 py-0.5 text-cyan-200", children: requirementStatus(requirement) }), _jsxs("span", { className: "rounded bg-purple-500/10 px-1.5 py-0.5 text-purple-200", children: ["P:", requirementPriority(requirement)] })] }), sourcePath ? _jsx("div", { className: "mt-1 truncate font-mono text-[10px] text-slate-500", children: sourcePath }) : null] }, requirementRowKey(requirement, index)));
                                })) })] }), _jsxs("section", { className: "min-h-0 overflow-hidden rounded-lg border border-white/10 bg-white/5", children: [_jsxs("div", { className: "flex h-10 items-center justify-between border-b border-white/10 px-3 text-xs text-slate-400", children: [_jsx(EvidenceEndpointBadge, { endpoint: detailEndpoint, testId: "pm-requirement-detail-endpoint" }), isLoadingRequirementDetail ? _jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin text-slate-500" }) : null] }), requirementDetailError ? (_jsx("div", { "data-testid": "pm-requirement-detail-error", className: "m-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-200", children: requirementDetailError })) : null, detailRequirement ? (_jsxs("div", { "data-testid": "pm-requirement-detail", className: "max-h-full space-y-3 overflow-auto p-3 text-xs text-slate-300", children: [_jsx("div", { className: "rounded-md border border-white/5 bg-slate-950/35 px-2 py-1.5", children: _jsx(EvidenceEndpointBadge, { endpoint: detailEndpoint, testId: "pm-requirement-detail-body-endpoint" }) }), _jsxs("div", { children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "Title" }), _jsx("div", { className: "mt-1 break-words text-base font-semibold text-slate-100", children: requirementTitle(detailRequirement) })] }), _jsxs("div", { className: "grid gap-2 sm:grid-cols-3", children: [_jsxs("div", { className: "rounded-md border border-white/5 bg-slate-950/35 p-2", children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "Status" }), _jsx("div", { className: "mt-1 text-slate-100", children: requirementStatus(detailRequirement) })] }), _jsxs("div", { className: "rounded-md border border-white/5 bg-slate-950/35 p-2", children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "Priority" }), _jsx("div", { className: "mt-1 text-slate-100", children: requirementPriority(detailRequirement) })] }), _jsxs("div", { className: "rounded-md border border-white/5 bg-slate-950/35 p-2", children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "Source" }), _jsx("div", { className: "mt-1 break-words font-mono text-[11px] text-slate-300", children: source || 'unlinked' })] })] }), readRequirementString(detailRequirement, ['description', 'summary']) ? (_jsxs("div", { children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "Description" }), _jsx("p", { className: "mt-1 whitespace-pre-wrap break-words leading-relaxed text-slate-300", children: readRequirementString(detailRequirement, ['description', 'summary']) })] })) : null, _jsxs("div", { className: "grid gap-3 lg:grid-cols-2", children: [_jsxs("div", { className: "rounded-md border border-white/5 bg-slate-950/35 p-2", children: [_jsx("div", { className: "mb-1 text-[10px] uppercase text-slate-500", children: "Acceptance Criteria" }), acceptanceCriteria.length > 0 ? (_jsx("ul", { className: "space-y-1", children: acceptanceCriteria.map((criterion, index) => (_jsx("li", { className: "break-words rounded bg-white/5 px-2 py-1 text-slate-300", children: criterion }, `${criterion}-${index}`))) })) : (_jsx("div", { className: "text-slate-500", children: "\u672A\u8BB0\u5F55\u9A8C\u6536\u6761\u4EF6" }))] }), _jsxs("div", { className: "rounded-md border border-white/5 bg-slate-950/35 p-2", children: [_jsx("div", { className: "mb-1 text-[10px] uppercase text-slate-500", children: "Related Tasks" }), relatedTasks.length > 0 ? (_jsx("div", { className: "flex flex-wrap gap-1", children: relatedTasks.map((taskId, index) => (_jsx("span", { className: "rounded bg-amber-500/10 px-1.5 py-0.5 font-mono text-[11px] text-amber-200", children: taskId }, `${taskId}-${index}`))) })) : (_jsx("div", { className: "text-slate-500", children: "\u672A\u5173\u8054 PM \u4EFB\u52A1" }))] })] }), _jsxs("details", { className: "rounded-md border border-white/5 bg-slate-950/35", children: [_jsx("summary", { className: "cursor-pointer px-2 py-1.5 text-[11px] text-slate-400", children: "Raw requirement payload" }), _jsx("pre", { className: "max-h-52 overflow-auto border-t border-white/5 p-2 font-mono text-[11px] text-slate-400", children: JSON.stringify(detailRequirement, null, 2) })] })] })) : (_jsx("div", { "data-testid": "pm-requirement-detail", className: "flex h-full items-center justify-center px-3 text-sm text-slate-500", children: "\u9009\u62E9\u9700\u6C42\u540E\u67E5\u770B\u8BE6\u60C5" }))] })] })] }));
}
function historyValue(value) {
    return typeof value === 'string' && value.trim() ? value.trim() : '';
}
function historyEntryId(entry) {
    return historyValue(entry.task_id) || historyValue(entry.id) || historyValue(entry.title) || 'history';
}
function historyEntryAction(entry) {
    return historyValue(entry.action) || historyValue(entry.status) || historyValue(entry.type) || 'event';
}
function historyEntryTime(entry) {
    return historyValue(entry.updated_at) || historyValue(entry.created_at) || historyValue(entry.timestamp);
}
function directorIterationTaskCount(iteration) {
    return Array.isArray(iteration.tasks) ? iteration.tasks.length : 0;
}
function PMHistoryPanel({ pmState, workspace }) {
    const [taskHistory, setTaskHistory] = useState([]);
    const [directorIterations, setDirectorIterations] = useState([]);
    const [isLoadingHistory, setIsLoadingHistory] = useState(false);
    const [historyError, setHistoryError] = useState('');
    const loadHistory = useCallback(async () => {
        setIsLoadingHistory(true);
        setHistoryError('');
        try {
            const [taskResult, directorResult] = await Promise.all([
                listPmTaskHistory({ limit: 50, offset: 0, workspace }),
                listPmDirectorTaskHistory({ limit: 25, offset: 0, workspace }),
            ]);
            if (!taskResult.ok || !taskResult.data) {
                throw new Error(taskResult.error || 'PM 任务历史加载失败');
            }
            if (!directorResult.ok || !directorResult.data) {
                throw new Error(directorResult.error || 'Director 分发历史加载失败');
            }
            setTaskHistory(Array.isArray(taskResult.data.history) ? taskResult.data.history : []);
            setDirectorIterations(Array.isArray(directorResult.data.iterations) ? directorResult.data.iterations : []);
        }
        catch (error) {
            setTaskHistory([]);
            setDirectorIterations([]);
            setHistoryError(error instanceof Error ? error.message : 'PM 历史加载失败');
        }
        finally {
            setIsLoadingHistory(false);
        }
    }, [workspace]);
    useEffect(() => {
        void loadHistory();
    }, [loadHistory]);
    return (_jsxs("div", { "data-testid": "pm-history-panel", className: "h-full flex flex-col p-6", children: [_jsxs("div", { className: "mb-4 flex items-center justify-between gap-3", children: [_jsxs("div", { children: [_jsx("h2", { className: "text-lg font-semibold text-slate-100", children: "\u6267\u884C\u5386\u53F2" }), _jsx("p", { className: "text-xs text-slate-500", children: "\u6765\u81EA PM \u4EFB\u52A1\u5386\u53F2\u4E0E Director \u5206\u53D1\u5386\u53F2\u63A5\u53E3" })] }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void loadHistory(); }, disabled: isLoadingHistory, "data-testid": "pm-history-refresh", className: "h-8 px-2 text-xs text-slate-400 hover:bg-white/5 hover:text-slate-100", children: [_jsx(RefreshCw, { className: cn('mr-1.5 h-3.5 w-3.5', isLoadingHistory && 'animate-spin') }), "\u5237\u65B0"] })] }), historyError ? (_jsx("div", { "data-testid": "pm-history-error", className: "mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-200", children: historyError })) : null, _jsxs("div", { className: "grid min-h-0 flex-1 gap-3 overflow-hidden lg:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)]", children: [_jsxs("section", { className: "min-h-0 rounded-lg border border-white/10 bg-white/5", children: [_jsxs("div", { className: "flex h-10 items-center justify-between border-b border-white/10 px-3 text-xs text-slate-400", children: [_jsx("span", { children: "PM Task History" }), _jsx("span", { "data-testid": "pm-history-task-count", className: "rounded border border-white/10 bg-slate-950/40 px-1.5 py-0.5 text-[10px] text-slate-300", children: taskHistory.length })] }), _jsx("div", { "data-testid": "pm-history-task-list", className: "max-h-full space-y-1 overflow-auto p-2", children: isLoadingHistory && taskHistory.length === 0 ? (_jsxs("div", { className: "flex items-center gap-2 px-2 py-4 text-xs text-slate-500", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }), "\u52A0\u8F7D\u4EFB\u52A1\u5386\u53F2..."] })) : taskHistory.length === 0 ? (_jsx("div", { "data-testid": "pm-history-task-empty", className: "px-2 py-4 text-xs text-slate-500", children: "\u6682\u65E0\u4EFB\u52A1\u5386\u53F2" })) : (taskHistory.slice(0, 50).map((entry, index) => {
                                    const key = historyValue(entry.id) || `${historyEntryId(entry)}-${index}`;
                                    const time = historyEntryTime(entry);
                                    return (_jsxs("div", { "data-testid": "pm-history-task-row", className: "rounded-md border border-white/5 bg-slate-950/35 px-2 py-1.5 text-xs", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "min-w-0 truncate font-mono text-slate-200", children: historyEntryId(entry) }), _jsx("span", { className: "shrink-0 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200", children: historyEntryAction(entry) })] }), time ? _jsx("div", { className: "mt-1 truncate text-[10px] text-slate-500", children: time }) : null] }, key));
                                })) })] }), _jsxs("section", { className: "min-h-0 rounded-lg border border-white/10 bg-white/5", children: [_jsxs("div", { className: "flex h-10 items-center justify-between border-b border-white/10 px-3 text-xs text-slate-400", children: [_jsx("span", { children: "Director Dispatch" }), _jsx("span", { "data-testid": "pm-history-director-count", className: "rounded border border-white/10 bg-slate-950/40 px-1.5 py-0.5 text-[10px] text-slate-300", children: directorIterations.length })] }), _jsx("div", { "data-testid": "pm-history-director-list", className: "max-h-full space-y-1 overflow-auto p-2", children: isLoadingHistory && directorIterations.length === 0 ? (_jsxs("div", { className: "flex items-center gap-2 px-2 py-4 text-xs text-slate-500", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }), "\u52A0\u8F7D\u5206\u53D1\u5386\u53F2..."] })) : directorIterations.length === 0 ? (_jsx("div", { "data-testid": "pm-history-director-empty", className: "px-2 py-4 text-xs text-slate-500", children: "\u6682\u65E0 Director \u5206\u53D1\u5386\u53F2" })) : (directorIterations.slice(0, 25).map((iteration, index) => {
                                    const iterationId = typeof iteration.iteration === 'number' ? iteration.iteration : index + 1;
                                    return (_jsxs("div", { "data-testid": "pm-history-director-row", className: "rounded-md border border-white/5 bg-slate-950/35 px-2 py-1.5 text-xs", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsxs("span", { className: "text-slate-200", children: ["Iteration ", iterationId] }), _jsxs("span", { className: "rounded bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-200", children: [directorIterationTaskCount(iteration), " tasks"] })] }), historyValue(iteration.updated_at) || historyValue(iteration.created_at) ? (_jsx("div", { className: "mt-1 truncate text-[10px] text-slate-500", children: historyValue(iteration.updated_at) || historyValue(iteration.created_at) })) : null] }, `${iterationId}-${index}`));
                                })) })] })] }), _jsxs("details", { className: "mt-3 shrink-0 rounded-lg border border-white/10 bg-slate-950/35", children: [_jsx("summary", { className: "cursor-pointer px-3 py-2 text-xs text-slate-400", children: "PM \u72B6\u6001\u5FEB\u7167" }), pmState ? (_jsx("pre", { "data-testid": "pm-history-state-snapshot", className: "max-h-40 overflow-auto border-t border-white/10 p-3 font-mono text-xs text-slate-400", children: JSON.stringify(pmState, null, 2) })) : (_jsx("div", { "data-testid": "pm-history-state-empty", className: "border-t border-white/10 px-3 py-4 text-xs text-slate-500", children: "\u6682\u65E0 PM \u72B6\u6001\u5FEB\u7167" }))] })] }));
}
function PMAnalyticsPanel({ tasks }) {
    const statusCounts = tasks.reduce((acc, task) => {
        const status = task.status || 'unknown';
        acc[status] = (acc[status] || 0) + 1;
        return acc;
    }, {});
    return (_jsxs("div", { "data-testid": "pm-analytics-panel", className: "h-full flex flex-col p-6", children: [_jsx("h2", { className: "text-lg font-semibold text-slate-100 mb-4", children: "\u4EFB\u52A1\u7EDF\u8BA1" }), tasks.length > 0 ? (_jsx("div", { className: "grid grid-cols-2 gap-4", children: Object.entries(statusCounts).map(([status, count]) => (_jsxs("div", { className: "rounded-xl border border-white/10 bg-white/5 p-4", children: [_jsx("p", { className: "text-xs uppercase text-slate-500", children: status }), _jsx("p", { className: "text-2xl font-bold text-amber-400", children: count })] }, status))) })) : (_jsx("div", { className: "flex flex-1 items-center justify-center rounded-xl border border-white/10 bg-white/5 text-sm text-slate-500", children: "\u6682\u65E0\u4EFB\u52A1\u6570\u636E\uFF0C\u7EDF\u8BA1\u9762\u677F\u4E0D\u4F1A\u4F7F\u7528\u793A\u4F8B\u6570\u636E\u3002" }))] }));
}
