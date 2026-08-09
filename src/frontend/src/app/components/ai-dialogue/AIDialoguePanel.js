import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * AI 对话面板容器组件
 *
 * 主容器，协调各个子组件
 */
import { Activity, AlertCircle, Clock, Database, Download, Eye, Link2, Link2Off, List as ListIcon, Loader2, MessageSquare, Plus, RefreshCw, Search, ShieldCheck, Upload, } from 'lucide-react';
import { AIDialogueHeader } from './AIDialogueHeader';
import { AIMessageList } from './AIMessageList';
import { AIInputArea } from './AIInputArea';
import { AIStatusBar, AIHistoryPanel } from './AIStatusBar';
import { useAIDialogue, } from './useAIDialogue';
import { Button } from '@/app/components/ui/button';
import { RoleSessionEvidencePanel as CommonRoleSessionEvidencePanel } from '@/app/components/common/RoleSessionEvidencePanel';
const DEFAULT_THEMES = {
    pm: { primary: 'amber', secondary: 'amber-400', gradient: 'from-amber-500 to-amber-700' },
    architect: { primary: 'purple', secondary: 'purple-400', gradient: 'from-purple-500 to-purple-700' },
    chief_engineer: { primary: 'cyan', secondary: 'cyan-400', gradient: 'from-cyan-500 to-cyan-700' },
    director: { primary: 'emerald', secondary: 'emerald-400', gradient: 'from-emerald-500 to-emerald-700' },
    qa: { primary: 'rose', secondary: 'rose-400', gradient: 'from-rose-500 to-rose-700' },
    scout: { primary: 'indigo', secondary: 'indigo-400', gradient: 'from-indigo-500 to-indigo-700' },
};
function getRoleSessionEvidenceTone(theme) {
    const supported = ['amber', 'emerald', 'cyan', 'purple', 'rose', 'indigo'];
    return supported.includes(theme.primary) ? theme.primary : 'cyan';
}
/**
 * 获取状态显示组件
 */
function getStatusDisplay(statusKind, theme) {
    if (statusKind === 'blocked') {
        return (_jsxs("div", { className: "flex items-center gap-1.5 px-2 py-1 rounded-full bg-amber-500/10 border border-amber-500/20", children: [_jsx(AlertCircle, { className: "w-3 h-3 text-amber-400" }), _jsx("span", { className: "text-[10px] text-amber-400", children: "\u963B\u585E" })] }));
    }
    if (statusKind === 'loading') {
        return (_jsxs("div", { className: "flex items-center gap-1.5 px-2 py-1 rounded-full bg-slate-500/10 border border-slate-500/20", children: [_jsx("div", { className: "w-1.5 h-1.5 rounded-full bg-slate-400 animate-pulse" }), _jsx("span", { className: "text-[10px] text-slate-400", children: "\u68C0\u67E5\u4E2D..." })] }));
    }
    if (statusKind === 'unconfigured' || statusKind === 'error') {
        return (_jsxs("div", { className: "flex items-center gap-1.5 px-2 py-1 rounded-full bg-red-500/10 border border-red-500/20", children: [_jsx(AlertCircle, { className: "w-3 h-3 text-red-400" }), _jsx("span", { className: "text-[10px] text-red-400", children: statusKind === 'unconfigured' ? '未配置' : '异常' })] }));
    }
    const colorMap = {
        amber: { bg: 'rgba(245, 158, 11, 0.1)', border: 'rgba(245, 158, 11, 0.2)', dot: '#fbbf24', text: '#fbbf24' },
        purple: { bg: 'rgba(168, 85, 247, 0.1)', border: 'rgba(168, 85, 247, 0.2)', dot: '#a78bfa', text: '#a78bfa' },
        emerald: { bg: 'rgba(16, 185, 129, 0.1)', border: 'rgba(16, 185, 129, 0.2)', dot: '#34d399', text: '#34d399' },
        rose: { bg: 'rgba(244, 63, 94, 0.1)', border: 'rgba(244, 63, 94, 0.2)', dot: '#fb7185', text: '#fb7185' },
        cyan: { bg: 'rgba(6, 182, 212, 0.1)', border: 'rgba(6, 182, 212, 0.2)', dot: '#22d3ee', text: '#22d3ee' },
        indigo: { bg: 'rgba(99, 102, 241, 0.1)', border: 'rgba(99, 102, 241, 0.2)', dot: '#818cf8', text: '#818cf8' },
    };
    const colors = colorMap[theme.primary] || { bg: 'rgba(148, 163, 184, 0.1)', border: 'rgba(148, 163, 184, 0.2)', dot: '#94a3b8', text: '#94a3b8' };
    return (_jsxs("div", { className: "flex items-center gap-1.5 px-2 py-1 rounded-full border", style: { backgroundColor: colors.bg, borderColor: colors.border }, children: [_jsx("div", { className: "w-1.5 h-1.5 rounded-full", style: { backgroundColor: colors.dot } }), _jsx("span", { className: "text-[10px]", style: { color: colors.text }, children: "\u5C31\u7EEA" })] }));
}
function formatShortId(value) {
    const text = String(value || '').trim();
    if (!text)
        return '';
    return text.length > 12 ? `${text.slice(0, 10)}...` : text;
}
function getSessionStateLabel(state) {
    const normalized = String(state || '').trim().toLowerCase();
    if (!normalized)
        return '未知';
    if (['active', 'ready', 'idle', 'open'].includes(normalized))
        return '就绪';
    if (['running', 'streaming', 'in_progress', 'busy'].includes(normalized))
        return '运行中';
    if (['failed', 'error', 'blocked'].includes(normalized))
        return '异常';
    if (['closed', 'archived', 'detached'].includes(normalized))
        return '已归档';
    return normalized;
}
function getAttachmentModeLabel(mode) {
    if (mode === 'attached_readonly')
        return '附着';
    if (mode === 'attached_collaborative')
        return '协同';
    return '隔离';
}
function formatSessionTime(value) {
    const epoch = Date.parse(String(value || ''));
    if (!Number.isFinite(epoch))
        return '';
    return new Date(epoch).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
    });
}
function RoleSessionStrip({ sessionId, isInitializingSession, sessionError, attachmentMode, attachedRunId, attachedTaskId, theme, workflowExportTarget, workflowExportLabel, isExportingWorkflow, workflowExportStatus, showRoleSessions, isLoadingRoleSessions, showRoleSessionEvidence, showRoleSessionMemory, isLoadingRoleSessionMemory, showRoleSessionSnapshotExport, isExportingRoleSessionSnapshot, roleSessionSnapshotExportStatus, roleCapabilities, isLoadingRoleCapabilities, roleCapabilitiesError, activeSessionDetail, isLoadingSessionDetail, sessionDetailError, isDetachingRoleSession, roleSessionDetachStatus, onNewSession, onToggleRoleSessions, onToggleRoleSessionEvidence, onToggleRoleSessionMemory, onToggleRoleSessionSnapshotExport, onDetachRoleSession, onExportToWorkflow, }) {
    const themeColors = {
        amber: 'text-amber-300 border-amber-500/20 bg-amber-500/10',
        purple: 'text-purple-300 border-purple-500/20 bg-purple-500/10',
        cyan: 'text-cyan-300 border-cyan-500/20 bg-cyan-500/10',
        emerald: 'text-emerald-300 border-emerald-500/20 bg-emerald-500/10',
        rose: 'text-rose-300 border-rose-500/20 bg-rose-500/10',
        indigo: 'text-indigo-300 border-indigo-500/20 bg-indigo-500/10',
    };
    const tone = themeColors[theme.primary] || 'text-slate-300 border-slate-500/20 bg-slate-500/10';
    const hasDetailTaskId = Boolean(activeSessionDetail && Object.prototype.hasOwnProperty.call(activeSessionDetail, 'attached_task_id'));
    const hasDetailRunId = Boolean(activeSessionDetail && Object.prototype.hasOwnProperty.call(activeSessionDetail, 'attached_run_id'));
    const effectiveAttachmentMode = activeSessionDetail?.attachment_mode || attachmentMode;
    const effectiveAttachedTaskId = hasDetailTaskId ? activeSessionDetail?.attached_task_id || undefined : attachedTaskId;
    const effectiveAttachedRunId = hasDetailRunId ? activeSessionDetail?.attached_run_id || undefined : attachedRunId;
    const attachedTarget = effectiveAttachedTaskId
        ? '任务'
        : effectiveAttachedRunId
            ? 'Run'
            : '';
    const attachedTargetTitle = effectiveAttachedTaskId || effectiveAttachedRunId;
    const exportLabel = workflowExportLabel || '导出流程';
    const canExport = Boolean(workflowExportTarget);
    const canDetach = Boolean(sessionId && !isInitializingSession && (effectiveAttachmentMode !== 'isolated' || effectiveAttachedRunId || effectiveAttachedTaskId));
    const exportStatusTone = workflowExportStatus.kind === 'success'
        ? 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200'
        : 'border-red-400/20 bg-red-500/10 text-red-200';
    const workflowExportTitle = workflowExportStatus.runId
        ? [
            workflowExportStatus.runId,
            `artifacts=${workflowExportStatus.artifactCount ?? 0}`,
            `messages=${workflowExportStatus.messageCount ?? 0}`,
        ].join(' · ')
        : workflowExportStatus.message;
    const detachStatusTone = roleSessionDetachStatus.kind === 'success'
        ? 'border-cyan-400/20 bg-cyan-500/10 text-cyan-100'
        : 'border-red-400/20 bg-red-500/10 text-red-200';
    const snapshotStatusTone = roleSessionSnapshotExportStatus.kind === 'success'
        ? 'border-indigo-400/20 bg-indigo-500/10 text-indigo-100'
        : 'border-red-400/20 bg-red-500/10 text-red-200';
    const detailTitle = activeSessionDetail
        ? [
            activeSessionDetail.title,
            activeSessionDetail.host_kind,
            activeSessionDetail.attachment_mode,
            activeSessionDetail.updated_at,
        ].filter(Boolean).join(' · ')
        : sessionDetailError || 'RoleSession 详情尚未加载';
    const detailLabel = isLoadingSessionDetail
        ? '同步中'
        : activeSessionDetail
            ? getSessionStateLabel(activeSessionDetail.state)
            : sessionDetailError
                ? '异常'
                : '';
    const messageCount = activeSessionDetail?.message_count ?? 0;
    const messageTitle = activeSessionDetail
        ? `messages=${messageCount}`
        : sessionDetailError || 'RoleSession 消息数尚未加载';
    return (_jsx("div", { "data-testid": "ai-role-session-strip", className: "shrink-0 border-b border-white/10 bg-slate-950/60 px-3 py-2 text-[11px]", children: _jsxs("div", { className: "grid min-w-0 grid-rows-[auto_auto] gap-1.5", children: [_jsxs("div", { "data-testid": "ai-role-session-status-row", className: "flex min-h-6 min-w-0 max-w-full flex-wrap items-center gap-x-1.5 gap-y-1 overflow-hidden pr-1", children: [isInitializingSession ? (_jsx(Loader2, { className: "h-3.5 w-3.5 shrink-0 animate-spin text-slate-400" })) : sessionId ? (_jsx(ShieldCheck, { className: "h-3.5 w-3.5 shrink-0 text-emerald-300" })) : (_jsx(AlertCircle, { className: "h-3.5 w-3.5 shrink-0 text-amber-300" })), _jsx("span", { "data-testid": "ai-role-session-id", className: "inline-flex max-w-[7rem] shrink-0 items-center overflow-hidden truncate whitespace-nowrap rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono leading-none text-slate-300", title: sessionId || sessionError || 'RoleSession 尚未创建', children: isInitializingSession ? '创建中' : sessionId ? `RS ${formatShortId(sessionId)}` : '未建' }), _jsx("span", { className: `inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 leading-none ${tone}`, title: `附着模式: ${getAttachmentModeLabel(effectiveAttachmentMode)}`, children: getAttachmentModeLabel(effectiveAttachmentMode) }), detailLabel ? (_jsxs("span", { "data-testid": "ai-role-session-detail-chip", className: `inline-flex max-w-[5.5rem] shrink-0 items-center gap-1 overflow-hidden rounded border px-1.5 py-0.5 leading-none ${sessionDetailError
                                ? 'border-amber-400/20 bg-amber-500/10 text-amber-200'
                                : 'border-white/10 bg-white/5 text-slate-300'}`, title: detailTitle, children: [isLoadingSessionDetail ? _jsx(Loader2, { className: "h-3 w-3 shrink-0 animate-spin" }) : _jsx(Clock, { className: "h-3 w-3 shrink-0" }), _jsx("span", { className: "min-w-0 truncate", children: detailLabel })] })) : null, activeSessionDetail || sessionDetailError ? (_jsxs("span", { "data-testid": "ai-role-session-message-chip", className: "inline-flex shrink-0 items-center gap-1 rounded border border-white/10 bg-white/5 px-1.5 py-0.5 leading-none text-slate-300", title: messageTitle, children: [_jsx(MessageSquare, { className: "h-3 w-3 shrink-0" }), _jsx("span", { className: "whitespace-nowrap", children: messageCount })] })) : null, attachedTarget ? (_jsxs("span", { "data-testid": "ai-role-session-attachment", className: "inline-flex max-w-[6.5rem] shrink-0 items-center gap-1 overflow-hidden rounded border border-white/10 bg-white/5 px-1.5 py-0.5 leading-none text-slate-300", title: attachedTargetTitle, children: [_jsx(Link2, { className: "h-3 w-3 shrink-0" }), _jsx("span", { className: "min-w-0 truncate", children: attachedTarget })] })) : null, roleSessionDetachStatus.kind !== 'idle' && roleSessionDetachStatus.message ? (_jsx("span", { "data-testid": "ai-role-session-detach-status", className: `inline-flex max-w-28 shrink items-center overflow-hidden rounded border px-1.5 py-0.5 leading-none ${detachStatusTone}`, title: roleSessionDetachStatus.message, children: _jsx("span", { className: "min-w-0 truncate", children: roleSessionDetachStatus.message }) })) : null, roleSessionSnapshotExportStatus.kind !== 'idle' && roleSessionSnapshotExportStatus.message ? (_jsx("span", { "data-testid": "ai-role-session-snapshot-status", className: `inline-flex max-w-28 shrink items-center overflow-hidden rounded border px-1.5 py-0.5 leading-none ${snapshotStatusTone}`, title: roleSessionSnapshotExportStatus.message, children: _jsx("span", { className: "min-w-0 truncate", children: roleSessionSnapshotExportStatus.format || 'snapshot' }) })) : null, workflowExportStatus.kind !== 'idle' && workflowExportStatus.message ? (_jsx("span", { "data-testid": "ai-role-session-export-status", className: `inline-flex max-w-28 shrink items-center overflow-hidden rounded border px-1.5 py-0.5 leading-none ${exportStatusTone}`, title: workflowExportTitle, children: _jsx("span", { className: "min-w-0 truncate", children: workflowExportStatus.runId
                                    ? `Run ${formatShortId(workflowExportStatus.runId)}`
                                    : workflowExportStatus.message }) })) : null, _jsxs("span", { "data-testid": "ai-role-capability-chip", className: `inline-flex max-w-[4.5rem] shrink-0 items-center gap-1 overflow-hidden rounded border px-1.5 py-0.5 leading-none ${roleCapabilitiesError
                                ? 'border-amber-400/20 bg-amber-500/10 text-amber-200'
                                : 'border-white/10 bg-white/5 text-slate-300'}`, title: roleCapabilitiesError || roleCapabilities.join(', ') || '未加载角色能力', children: [isLoadingRoleCapabilities ? (_jsx(Loader2, { className: "h-3 w-3 shrink-0 animate-spin" })) : (_jsx(ShieldCheck, { className: "h-3 w-3 shrink-0" })), _jsxs("span", { className: "min-w-0 truncate whitespace-nowrap tabular-nums", children: [isLoadingRoleCapabilities ? '...' : roleCapabilitiesError ? '?' : roleCapabilities.length, "\u9879"] })] })] }), _jsxs("div", { "data-testid": "ai-role-session-actions", className: "flex min-w-0 max-w-full items-center justify-end gap-1 overflow-x-auto overflow-y-hidden border-t border-white/5 pt-1.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden", children: [_jsx(Button, { variant: "ghost", size: "icon", onClick: onToggleRoleSessions, "data-testid": "ai-role-session-list", "aria-label": "\u67E5\u770B RoleSession \u5217\u8868", className: `h-6 w-6 shrink-0 text-slate-400 hover:bg-white/5 hover:text-slate-100 ${showRoleSessions ? 'bg-white/5 text-slate-100' : ''}`, title: "\u67E5\u770B RoleSession \u5217\u8868", children: isLoadingRoleSessions ? (_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" })) : (_jsx(ListIcon, { className: "h-3.5 w-3.5" })) }), _jsx(Button, { variant: "ghost", size: "icon", onClick: onToggleRoleSessionEvidence, disabled: !sessionId || isInitializingSession, "data-testid": "ai-role-session-evidence-toggle", "aria-label": "\u67E5\u770B RoleSession \u4EA7\u7269\u4E0E\u5BA1\u8BA1", className: `h-6 w-6 shrink-0 text-slate-400 hover:bg-white/5 hover:text-slate-100 disabled:opacity-50 ${showRoleSessionEvidence ? 'bg-white/5 text-slate-100' : ''}`, title: "\u67E5\u770B RoleSession \u4EA7\u7269\u4E0E\u5BA1\u8BA1", children: _jsx(Activity, { className: "h-3.5 w-3.5" }) }), _jsx(Button, { variant: "ghost", size: "icon", onClick: onToggleRoleSessionMemory, disabled: !sessionId || isInitializingSession, "data-testid": "ai-role-session-memory-toggle", "aria-label": "\u67E5\u770B Context OS RoleSession \u8BB0\u5FC6", className: `h-6 w-6 shrink-0 text-slate-400 hover:bg-white/5 hover:text-slate-100 disabled:opacity-50 ${showRoleSessionMemory ? 'bg-white/5 text-slate-100' : ''}`, title: "\u67E5\u770B Context OS RoleSession \u8BB0\u5FC6", children: isLoadingRoleSessionMemory ? (_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" })) : (_jsx(Database, { className: "h-3.5 w-3.5" })) }), _jsx(Button, { variant: "ghost", size: "icon", onClick: onToggleRoleSessionSnapshotExport, disabled: !sessionId || isInitializingSession, "data-testid": "ai-role-session-snapshot-toggle", "aria-label": "\u5BFC\u51FA\u5F53\u524D RoleSession \u5FEB\u7167", className: `h-6 w-6 shrink-0 text-slate-400 hover:bg-white/5 hover:text-slate-100 disabled:opacity-50 ${showRoleSessionSnapshotExport ? 'bg-white/5 text-slate-100' : ''}`, title: "\u5BFC\u51FA\u5F53\u524D RoleSession \u5FEB\u7167", children: isExportingRoleSessionSnapshot ? (_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" })) : (_jsx(Download, { className: "h-3.5 w-3.5" })) }), canDetach || isDetachingRoleSession ? (_jsx(Button, { variant: "ghost", size: "icon", onClick: onDetachRoleSession, disabled: !sessionId || isInitializingSession || isDetachingRoleSession, "data-testid": "ai-role-session-detach", "aria-label": "\u89E3\u9664\u5F53\u524D RoleSession \u4E0E\u5DE5\u4F5C\u6D41\u4EFB\u52A1\u7684\u9644\u7740", className: "h-6 w-6 shrink-0 text-slate-400 hover:bg-cyan-500/10 hover:text-cyan-100 disabled:opacity-50", title: "\u89E3\u9664\u5F53\u524D RoleSession \u4E0E\u5DE5\u4F5C\u6D41\u4EFB\u52A1\u7684\u9644\u7740", children: isDetachingRoleSession ? (_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" })) : (_jsx(Link2Off, { className: "h-3.5 w-3.5" })) })) : null, canExport ? (_jsx(Button, { variant: "ghost", size: "icon", onClick: onExportToWorkflow, disabled: !sessionId || isInitializingSession || isExportingWorkflow, "data-testid": "ai-role-session-export", "aria-label": `导出当前 RoleSession 到 ${workflowExportTarget} 工作流`, className: "h-6 w-6 shrink-0 text-slate-400 hover:bg-emerald-500/10 hover:text-emerald-100 disabled:opacity-50", title: `导出当前 RoleSession 到 ${workflowExportTarget} 工作流`, children: isExportingWorkflow ? (_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" })) : (_jsx(Upload, { className: "h-3.5 w-3.5" })) })) : null, _jsx(Button, { variant: "ghost", size: "icon", onClick: onNewSession, disabled: isInitializingSession, "data-testid": "ai-role-session-new", "aria-label": "\u65B0\u5EFA RoleSession", className: "h-6 w-6 shrink-0 text-slate-400 hover:bg-white/5 hover:text-slate-100", title: "\u65B0\u5EFA RoleSession", children: _jsx(Plus, { className: "h-3.5 w-3.5" }) })] })] }) }));
}
function memoryItemLabel(item) {
    return String(item.text || item.content || item.entity || item.path || item.id || '').trim();
}
function compactMemoryDetail(value) {
    if (value === undefined || value === null)
        return '';
    if (typeof value === 'string')
        return value.trim();
    try {
        return JSON.stringify(value, null, 2);
    }
    catch {
        return String(value);
    }
}
function RoleSessionMemoryPanel({ query, items, detail, isLoading, error, isLoadingDetail, detailError, onQueryChange, onSearch, onReadItem, }) {
    const detailPayload = compactMemoryDetail(detail?.payload);
    return (_jsxs("div", { "data-testid": "ai-role-session-memory-panel", className: "border-b border-white/10 bg-slate-900/85 px-3 py-2", children: [_jsxs("form", { className: "mb-2 flex items-center gap-2", onSubmit: (event) => {
                    event.preventDefault();
                    onSearch(query);
                }, children: [_jsxs("div", { className: "flex min-w-0 flex-1 items-center gap-2 text-[11px] text-slate-400", children: [_jsx(Database, { className: "h-3.5 w-3.5 text-slate-500" }), _jsx("span", { children: "RoleSession \u8BB0\u5FC6" }), _jsx("input", { "data-testid": "ai-role-session-memory-query", value: query, onChange: (event) => onQueryChange(event.target.value), className: "h-7 min-w-0 flex-1 rounded-md border border-white/10 bg-slate-950/70 px-2 text-[11px] text-slate-200 outline-none placeholder:text-slate-600 focus:border-cyan-400/40", placeholder: "task, artifact, state" })] }), _jsxs(Button, { type: "submit", variant: "ghost", size: "sm", disabled: isLoading, "data-testid": "ai-role-session-memory-search", className: "h-7 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100", title: "\u641C\u7D22 RoleSession \u8BB0\u5FC6", children: [_jsx(Search, { className: `mr-1 h-3 w-3 ${isLoading ? 'animate-pulse' : ''}` }), "\u641C\u7D22"] })] }), error ? (_jsx("div", { className: "mb-2 rounded border border-red-500/20 bg-red-500/10 px-2 py-1 text-[10px] text-red-200", children: error })) : null, _jsxs("div", { className: "grid max-h-60 gap-2 overflow-auto md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]", children: [_jsxs("section", { className: "min-w-0 rounded-md border border-white/10 bg-slate-950/45 p-2", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500", children: [_jsx("span", { children: "Matches" }), _jsx("span", { children: items.length })] }), isLoading && items.length === 0 ? (_jsxs("div", { className: "flex items-center gap-2 py-3 text-[11px] text-slate-500", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }), "\u68C0\u7D22\u4E2D..."] })) : items.length === 0 ? (_jsx("p", { className: "py-3 text-[11px] text-slate-500", children: "\u6682\u65E0\u8BB0\u5FC6" })) : (_jsx("div", { className: "space-y-1", children: items.slice(0, 8).map((item, index) => {
                                    const label = memoryItemLabel(item);
                                    const kind = String(item.kind || 'memory');
                                    const key = item.id || `${kind}-${index}`;
                                    return (_jsxs("button", { type: "button", onClick: () => onReadItem(item), "data-testid": "ai-role-session-memory-row", className: "w-full rounded border border-white/5 bg-white/[0.035] px-2 py-1.5 text-left hover:border-cyan-400/20 hover:bg-cyan-500/10", children: [_jsxs("div", { className: "flex items-center justify-between gap-2 text-[11px]", children: [_jsx("span", { className: "min-w-0 truncate text-slate-300", children: label || formatShortId(item.id) }), _jsx("span", { className: "shrink-0 rounded bg-cyan-500/10 px-1.5 py-0.5 text-[9px] text-cyan-200", children: kind })] }), item.entity || item.path ? (_jsx("div", { className: "mt-1 truncate text-[10px] text-slate-500", children: item.entity || item.path })) : null] }, key));
                                }) }))] }), _jsxs("section", { "data-testid": "ai-role-session-memory-detail", className: "min-w-0 rounded-md border border-white/10 bg-slate-950/45 p-2", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500", children: [_jsx("span", { children: "Detail" }), isLoadingDetail ? _jsx(Loader2, { className: "h-3 w-3 animate-spin" }) : _jsx(Eye, { className: "h-3 w-3" })] }), detailError ? (_jsx("div", { className: "rounded border border-red-500/20 bg-red-500/10 px-2 py-1 text-[10px] text-red-200", children: detailError })) : detailPayload ? (_jsx("pre", { className: "max-h-44 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950/60 p-2 text-[10px] leading-4 text-slate-300", children: detailPayload })) : (_jsx("p", { className: "py-3 text-[11px] text-slate-500", children: "\u9009\u62E9\u4E00\u6761\u8BB0\u5FC6" }))] })] })] }));
}
function formatSnapshotPayload(payload, format) {
    if (payload === undefined || payload === null)
        return '';
    if (format === 'markdown'
        && typeof payload === 'object'
        && payload
        && typeof payload.markdown === 'string') {
        return String(payload.markdown);
    }
    if (typeof payload === 'string')
        return payload;
    try {
        return JSON.stringify(payload, null, 2);
    }
    catch {
        return String(payload);
    }
}
function RoleSessionSnapshotExportPanel({ format, payload, isLoading, status, onFormatChange, onExport, }) {
    const preview = formatSnapshotPayload(payload, format);
    const statusTone = status.kind === 'error'
        ? 'border-red-500/20 bg-red-500/10 text-red-200'
        : 'border-white/10 bg-white/5 text-slate-300';
    const handleFormatChange = (nextFormat) => {
        onFormatChange(nextFormat);
        onExport(nextFormat);
    };
    return (_jsxs("div", { "data-testid": "ai-role-session-snapshot-panel", className: "border-b border-white/10 bg-slate-900/85 px-3 py-2", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2 text-[11px] text-slate-400", children: [_jsx(Download, { className: "h-3.5 w-3.5 text-slate-500" }), _jsx("span", { children: "RoleSession \u5FEB\u7167" }), status.message ? (_jsx("span", { "data-testid": "ai-role-session-snapshot-message", className: `truncate rounded border px-1.5 py-0.5 text-[10px] ${statusTone}`, title: status.message, children: status.message })) : null] }), _jsxs("div", { className: "flex shrink-0 items-center gap-1", children: [['json', 'markdown'].map((option) => (_jsx(Button, { type: "button", variant: "ghost", size: "sm", onClick: () => handleFormatChange(option), disabled: isLoading, "data-testid": `ai-role-session-snapshot-format-${option}`, className: `h-6 px-2 text-[10px] ${format === option
                                    ? 'bg-white/10 text-slate-100'
                                    : 'text-slate-400 hover:bg-white/5 hover:text-slate-100'}`, title: `导出 ${option.toUpperCase()} 快照`, children: option === 'json' ? 'JSON' : 'MD' }, option))), _jsxs(Button, { type: "button", variant: "ghost", size: "sm", onClick: () => onExport(format), disabled: isLoading, "data-testid": "ai-role-session-snapshot-refresh", className: "h-6 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100", title: "\u5237\u65B0 RoleSession \u5FEB\u7167", children: [_jsx(RefreshCw, { className: `mr-1 h-3 w-3 ${isLoading ? 'animate-spin' : ''}` }), "\u5237\u65B0"] })] })] }), status.kind === 'error' ? (_jsx("div", { className: "mb-2 rounded border border-red-500/20 bg-red-500/10 px-2 py-1 text-[10px] text-red-200", children: status.message })) : null, _jsx("pre", { "data-testid": "ai-role-session-snapshot-preview", className: "max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-white/10 bg-slate-950/55 p-2 text-[10px] leading-4 text-slate-300", children: isLoading && !preview ? '导出中...' : preview || '暂无快照' })] }));
}
function RoleSessionListPanel({ sessions, activeSessionId, isLoading, error, theme, onReload, onSelect, }) {
    const themeColors = {
        amber: 'border-amber-500/25 bg-amber-500/10 text-amber-100',
        purple: 'border-purple-500/25 bg-purple-500/10 text-purple-100',
        cyan: 'border-cyan-500/25 bg-cyan-500/10 text-cyan-100',
        emerald: 'border-emerald-500/25 bg-emerald-500/10 text-emerald-100',
        rose: 'border-rose-500/25 bg-rose-500/10 text-rose-100',
        indigo: 'border-indigo-500/25 bg-indigo-500/10 text-indigo-100',
    };
    const activeTone = themeColors[theme.primary] || 'border-slate-500/25 bg-slate-500/10 text-slate-100';
    return (_jsxs("div", { "data-testid": "ai-role-session-list-panel", className: "border-b border-white/10 bg-slate-900/85 px-3 py-2", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between", children: [_jsx("span", { className: "text-[11px] text-slate-400", children: "RoleSession \u5386\u53F2" }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: onReload, disabled: isLoading, className: "h-6 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100", title: "\u5237\u65B0 RoleSession \u5217\u8868", children: [_jsx(RefreshCw, { className: `mr-1 h-3 w-3 ${isLoading ? 'animate-spin' : ''}` }), "\u5237\u65B0"] })] }), error ? (_jsx("div", { className: "mb-2 rounded border border-red-500/20 bg-red-500/10 px-2 py-1 text-[10px] text-red-200", children: error })) : null, _jsx("div", { className: "max-h-48 space-y-1 overflow-auto", children: isLoading && sessions.length === 0 ? (_jsxs("div", { className: "flex items-center justify-center gap-2 py-4 text-[11px] text-slate-500", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }), "\u52A0\u8F7D RoleSession..."] })) : sessions.length === 0 ? (_jsx("p", { className: "py-4 text-center text-[11px] text-slate-500", children: "\u6682\u65E0\u53EF\u6062\u590D\u7684 RoleSession" })) : (sessions.map((session) => {
                    const updatedAt = formatSessionTime(session.updated_at || session.created_at);
                    const isActive = session.id === activeSessionId;
                    return (_jsxs("button", { type: "button", onClick: () => onSelect(session.id), "data-testid": `ai-role-session-option-${session.id}`, className: `w-full rounded-md border px-2 py-2 text-left text-[11px] transition-colors ${isActive
                            ? activeTone
                            : 'border-white/10 bg-white/[0.035] text-slate-300 hover:border-white/20 hover:bg-white/[0.06]'}`, children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "min-w-0 truncate font-mono", children: formatShortId(session.id) }), session.state ? (_jsx("span", { className: "shrink-0 rounded border border-white/10 bg-slate-950/40 px-1.5 py-0.5 text-[9px] uppercase text-slate-400", children: session.state })) : null] }), _jsxs("div", { className: "mt-1 flex min-w-0 items-center gap-2 text-[10px] text-slate-500", children: [updatedAt ? (_jsxs("span", { className: "inline-flex shrink-0 items-center gap-1", children: [_jsx(Clock, { className: "h-3 w-3" }), updatedAt] })) : null, _jsx("span", { className: "truncate", children: session.title || getAttachmentModeLabel(session.attachment_mode || 'isolated') })] })] }, session.id));
                })) })] }));
}
/**
 * AI 对话面板
 */
export function AIDialoguePanel({ dialogueRole, roleDisplayName, roleTheme, welcomeMessage: welcomeMessageProp, context, visible = true, initialConversationId, workspace, onConversationChange, sessionId, hostKind = 'electron_workbench', attachmentMode = 'isolated', attachedRunId, attachedTaskId, capabilityProfile, workflowExportTarget, workflowExportLabel, onSessionChange, interactionBlockedReason, statusNoticeMode = 'full', }) {
    const theme = roleTheme || DEFAULT_THEMES[dialogueRole];
    const defaultWelcome = `${roleDisplayName} 已就绪。您可以开始对话。`;
    const welcomeMessage = welcomeMessageProp || defaultWelcome;
    const blockedReason = String(interactionBlockedReason || '').trim();
    const isInteractionBlocked = Boolean(blockedReason);
    const { messages, inputValue, setInputValue, isLoading, chatStatus, statusKind, isChatReady, isExplicitlyUnconfigured, sessionId: activeSessionId, isInitializingSession, sessionError, isExportingWorkflow, workflowExportStatus, showRoleSessions, roleSessions, isLoadingRoleSessions, roleSessionListError, showRoleSessionEvidence, showRoleSessionMemory, roleSessionMemoryQuery, roleSessionMemoryItems, isLoadingRoleSessionMemory, roleSessionMemoryError, roleSessionMemoryDetail, isLoadingRoleSessionMemoryDetail, roleSessionMemoryDetailError, showRoleSessionSnapshotExport, roleSessionSnapshotExportFormat, roleSessionSnapshotExportPayload, isExportingRoleSessionSnapshot, roleSessionSnapshotExportStatus, roleCapabilities, isLoadingRoleCapabilities, roleCapabilitiesError, activeRoleSessionDetail, isLoadingRoleSessionDetail, roleSessionDetailError, isDetachingRoleSession, roleSessionDetachStatus, conversationId, showHistory, conversations, configuredProviderLabel, configuredModelLabel, checkStatus, handleSend, handleClear, handleNewRoleSession, handleLoadRoleSessions, handleToggleRoleSessions, handleSelectRoleSession, handleToggleRoleSessionEvidence, setRoleSessionMemoryQuery, handleLoadRoleSessionMemory, handleToggleRoleSessionMemory, handleReadRoleSessionMemoryItem, setRoleSessionSnapshotExportFormat, handleExportRoleSessionSnapshot, handleToggleRoleSessionSnapshotExport, handleDetachRoleSession, handleExportToWorkflow, handleKeyDown, handleToggleHistory, handleNewConversation, handleSelectConversation, } = useAIDialogue({
        role: dialogueRole,
        roleName: roleDisplayName,
        welcomeMessage,
        context,
        workspace,
        initialConversationId,
        sessionId,
        hostKind,
        attachmentMode,
        attachedRunId,
        attachedTaskId,
        capabilityProfile,
        workflowExportTarget,
        onSessionChange,
        onConversationChange,
    });
    if (!visible)
        return null;
    const effectiveStatusKind = isInteractionBlocked ? 'blocked' : statusKind;
    const effectiveIsChatReady = isChatReady && !isInteractionBlocked;
    const statusDisplay = getStatusDisplay(effectiveStatusKind, theme);
    return (_jsxs("div", { className: "flex h-full min-w-0 flex-col overflow-hidden border-l border-white/10 bg-slate-950/50", children: [_jsx(AIDialogueHeader, { theme: theme, roleName: roleDisplayName, statusDisplay: statusDisplay, configuredProviderLabel: configuredProviderLabel, configuredModelLabel: configuredModelLabel, hasConversation: !!conversationId, showHistory: showHistory, isChatReady: effectiveIsChatReady, statusKind: effectiveStatusKind, onLoadHistory: handleToggleHistory, onClear: handleClear, onToggleHistory: handleToggleHistory }), _jsx(AIStatusBar, { statusKind: effectiveStatusKind, roleName: roleDisplayName, error: blockedReason || chatStatus?.error, debug: chatStatus?.debug, theme: theme, onRetry: checkStatus, noticeMode: statusNoticeMode }), _jsx(RoleSessionStrip, { sessionId: activeSessionId, isInitializingSession: isInitializingSession, sessionError: sessionError, attachmentMode: attachmentMode, attachedRunId: attachedRunId, attachedTaskId: attachedTaskId, theme: theme, workflowExportTarget: workflowExportTarget, workflowExportLabel: workflowExportLabel, isExportingWorkflow: isExportingWorkflow, workflowExportStatus: workflowExportStatus, showRoleSessions: showRoleSessions, isLoadingRoleSessions: isLoadingRoleSessions, showRoleSessionEvidence: showRoleSessionEvidence, showRoleSessionMemory: showRoleSessionMemory, isLoadingRoleSessionMemory: isLoadingRoleSessionMemory, showRoleSessionSnapshotExport: showRoleSessionSnapshotExport, isExportingRoleSessionSnapshot: isExportingRoleSessionSnapshot, roleSessionSnapshotExportStatus: roleSessionSnapshotExportStatus, roleCapabilities: roleCapabilities, isLoadingRoleCapabilities: isLoadingRoleCapabilities, roleCapabilitiesError: roleCapabilitiesError, activeSessionDetail: activeRoleSessionDetail, isLoadingSessionDetail: isLoadingRoleSessionDetail, sessionDetailError: roleSessionDetailError, isDetachingRoleSession: isDetachingRoleSession, roleSessionDetachStatus: roleSessionDetachStatus, onNewSession: handleNewRoleSession, onToggleRoleSessions: handleToggleRoleSessions, onToggleRoleSessionEvidence: handleToggleRoleSessionEvidence, onToggleRoleSessionMemory: handleToggleRoleSessionMemory, onToggleRoleSessionSnapshotExport: handleToggleRoleSessionSnapshotExport, onDetachRoleSession: handleDetachRoleSession, onExportToWorkflow: handleExportToWorkflow }), showRoleSessionEvidence && (_jsx(CommonRoleSessionEvidencePanel, { sessionId: activeSessionId, tone: getRoleSessionEvidenceTone(theme) })), showRoleSessionMemory && (_jsx(RoleSessionMemoryPanel, { query: roleSessionMemoryQuery, items: roleSessionMemoryItems, detail: roleSessionMemoryDetail, isLoading: isLoadingRoleSessionMemory, error: roleSessionMemoryError, isLoadingDetail: isLoadingRoleSessionMemoryDetail, detailError: roleSessionMemoryDetailError, onQueryChange: setRoleSessionMemoryQuery, onSearch: (query) => { void handleLoadRoleSessionMemory(query); }, onReadItem: (item) => { void handleReadRoleSessionMemoryItem(item); } })), showRoleSessionSnapshotExport && (_jsx(RoleSessionSnapshotExportPanel, { format: roleSessionSnapshotExportFormat, payload: roleSessionSnapshotExportPayload, isLoading: isExportingRoleSessionSnapshot, status: roleSessionSnapshotExportStatus, onFormatChange: setRoleSessionSnapshotExportFormat, onExport: (format) => { void handleExportRoleSessionSnapshot(format); } })), showRoleSessions && (_jsx(RoleSessionListPanel, { sessions: roleSessions, activeSessionId: activeSessionId, isLoading: isLoadingRoleSessions, error: roleSessionListError, theme: theme, onReload: handleLoadRoleSessions, onSelect: (id) => { void handleSelectRoleSession(id); } })), showHistory && (_jsx(AIHistoryPanel, { conversations: conversations, currentConversationId: conversationId, theme: theme, welcomeMessage: welcomeMessage, onNewConversation: handleNewConversation, onSelectConversation: handleSelectConversation })), _jsx(AIMessageList, { messages: messages, isLoading: isLoading, theme: theme, roleName: roleDisplayName }), _jsx(AIInputArea, { value: inputValue, onChange: setInputValue, onKeyDown: handleKeyDown, onSend: handleSend, isLoading: isLoading, isChatReady: effectiveIsChatReady, isExplicitlyUnconfigured: isExplicitlyUnconfigured, statusKind: effectiveStatusKind, blockedReason: blockedReason, roleName: roleDisplayName, theme: theme })] }));
}
