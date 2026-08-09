import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/** RealtimeActivityPanel - 角色实时活动面板
 *
 * 统一承载 PM、Chief Engineer 和 Director 的流式思考、工具调用、
 * 运行日志与文件活动，保持角色色仅用于状态识别。
 */
import { useState, useMemo, useEffect, useRef } from 'react';
import { Brain, Terminal, FileCode, Activity, ChevronRight, ChevronDown, Clock, Zap, Wrench, Play, CheckCircle2, AlertCircle, Loader2, Search, Sparkles, Cpu, TerminalSquare, Layers, ScrollText, } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { filterExecutionActivityLogs } from '@/app/utils/appRuntime';
// 日志级别颜色
const LOG_LEVEL_COLORS = {
    thinking: { bg: 'bg-purple-500/20', border: 'border-purple-500/30', text: 'text-purple-400', icon: 'text-purple-400' },
    info: { bg: 'bg-blue-500/20', border: 'border-blue-500/30', text: 'text-blue-400', icon: 'text-blue-400' },
    success: { bg: 'bg-emerald-500/20', border: 'border-emerald-500/30', text: 'text-emerald-400', icon: 'text-emerald-400' },
    warning: { bg: 'bg-amber-500/20', border: 'border-amber-500/30', text: 'text-amber-400', icon: 'text-amber-400' },
    error: { bg: 'bg-red-500/20', border: 'border-red-500/30', text: 'text-red-400', icon: 'text-red-400' },
    tool: { bg: 'bg-cyan-500/20', border: 'border-cyan-500/30', text: 'text-cyan-400', icon: 'text-cyan-400' },
    exec: { bg: 'bg-orange-500/20', border: 'border-orange-500/30', text: 'text-orange-400', icon: 'text-orange-400' },
};
const ROLE_ACTIVITY_THEMES = {
    pm: {
        label: 'PM',
        primaryColor: 'text-amber-100',
        runningDot: 'bg-amber-400',
        border: 'border-amber-500/30',
        bg: 'bg-amber-500/5',
        tag: 'bg-amber-500/10 text-amber-300 border-amber-500/20',
    },
    chief_engineer: {
        label: 'Chief Engineer',
        primaryColor: 'text-cyan-100',
        runningDot: 'bg-cyan-400',
        border: 'border-cyan-500/30',
        bg: 'bg-cyan-500/5',
        tag: 'bg-cyan-500/10 text-cyan-300 border-cyan-500/20',
    },
    director: {
        label: 'Director',
        primaryColor: 'text-indigo-100',
        runningDot: 'bg-indigo-400',
        border: 'border-indigo-500/30',
        bg: 'bg-indigo-500/5',
        tag: 'bg-indigo-500/10 text-indigo-300 border-indigo-500/20',
    },
};
const VIEW_TAB_TONES = {
    purple: 'bg-purple-500/20 text-purple-300 border border-purple-500/30',
    cyan: 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30',
    blue: 'bg-blue-500/20 text-blue-300 border border-blue-500/30',
    emerald: 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30',
};
function streamEventToken(log) {
    const meta = log.meta && typeof log.meta === 'object' ? log.meta : null;
    return String(meta?.streamEvent || '').trim().toLowerCase();
}
function isThinkingStreamEvent(token) {
    return (token === 'thinking_chunk'
        || token === 'content_chunk'
        || token === 'thinking_preview'
        || token === 'content_preview'
        || token === 'llm_waiting'
        || token === 'call_start'
        || token === 'llm_call_start'
        || token === 'llm_completed'
        || token === 'llm_failed'
        || token === 'call_end'
        || token === 'llm_call_end'
        || token === 'llm_call_error'
        || token === 'invoke_done'
        || token === 'invoke_error');
}
function isToolStreamEvent(token) {
    return (token === 'tool_call'
        || token === 'tool_result'
        || token === 'tool_start'
        || token === 'tool_end'
        || token === 'tool_error'
        || token === 'tool_execution'
        || token === 'tool_executed'
        || token === 'file_written'
        || token === 'file_created'
        || token === 'file_modified'
        || token === 'file_deleted'
        || token === 'artifact_created'
        || token === 'delivery_artifact');
}
function isObjectObjectText(value) {
    return /^\[object(?:\s+object)?\]$/i.test(String(value || '').trim());
}
function displayLogText(value, fallback) {
    const text = String(value || '').trim();
    if (text && !isObjectObjectText(text))
        return text;
    return fallback;
}
function thinkingSignalPriority(log) {
    const token = streamEventToken(log);
    if (token === 'thinking_chunk' || token === 'content_chunk')
        return 0;
    if (token === 'thinking_preview' || token === 'content_preview')
        return 1;
    if (log.level === 'thinking' && !token)
        return 2;
    if (token === 'llm_waiting' || token === 'call_start' || token === 'llm_call_start')
        return 4;
    if (token === 'llm_completed' || token === 'call_end' || token === 'llm_call_end')
        return 5;
    return 3;
}
function sortLogsForView(logs, view) {
    const sorted = [...logs];
    sorted.sort((a, b) => {
        if (view === 'thinking') {
            const priorityDelta = thinkingSignalPriority(a) - thinkingSignalPriority(b);
            if (priorityDelta !== 0)
                return priorityDelta;
        }
        return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
    });
    return sorted;
}
function activityLogMatchesView(log, view) {
    if (view === 'files')
        return true;
    const token = streamEventToken(log);
    if (view === 'thinking') {
        return isThinkingStreamEvent(token) || log.level === 'thinking';
    }
    if (view === 'tools') {
        return isToolStreamEvent(token) || log.level === 'tool' || log.level === 'exec';
    }
    if (view === 'logs') {
        if (isThinkingStreamEvent(token) || isToolStreamEvent(token))
            return false;
        return log.level === 'info' || log.level === 'warning';
    }
    return false;
}
function filterLogsForView(logs, view) {
    if (view === 'files')
        return logs;
    return logs.filter((log) => activityLogMatchesView(log, view));
}
function firstNonEmptyActivityView(logs) {
    for (const view of ['logs', 'tools', 'thinking']) {
        if (logs.some((log) => activityLogMatchesView(log, view))) {
            return view;
        }
    }
    return logs.length > 0 ? 'files' : null;
}
// 状态描述映射
const PHASE_DESCRIPTIONS = {
    'idle': { text: '等待指令', icon: _jsx(Clock, { className: "w-4 h-4" }), color: 'text-slate-400' },
    'planning': { text: '规划中...', icon: _jsx(Brain, { className: "w-4 h-4" }), color: 'text-purple-400' },
    'analyzing': { text: '分析中...', icon: _jsx(Search, { className: "w-4 h-4" }), color: 'text-blue-400' },
    'executing': { text: '执行中...', icon: _jsx(Zap, { className: "w-4 h-4" }), color: 'text-amber-400' },
    'llm_calling': { text: '调用 LLM...', icon: _jsx(Cpu, { className: "w-4 h-4" }), color: 'text-cyan-400' },
    'tool_running': { text: '工具执行中...', icon: _jsx(Wrench, { className: "w-4 h-4" }), color: 'text-orange-400' },
    'completed': { text: '已完成', icon: _jsx(CheckCircle2, { className: "w-4 h-4" }), color: 'text-emerald-400' },
    'error': { text: '出错', icon: _jsx(AlertCircle, { className: "w-4 h-4" }), color: 'text-red-400' },
};
export function RealtimeActivityPanel({ executionLogs = [], llmStreamEvents = [], processStreamEvents = [], currentPhase = 'idle', isRunning = false, role = 'pm', }) {
    const [activeView, setActiveView] = useState('thinking');
    const [manualViewSelected, setManualViewSelected] = useState(false);
    const [expandedLogs, setExpandedLogs] = useState(new Set());
    const logsEndRef = useRef(null);
    // 合并所有日志
    const allLogs = useMemo(() => {
        const processExecutionLogs = filterExecutionActivityLogs(processStreamEvents);
        const logs = [
            ...llmStreamEvents,
            ...executionLogs.map(l => ({ ...l, source: l.source || 'EXEC' })),
            ...processExecutionLogs.map(l => ({ ...l, source: 'PROC' })),
        ];
        return logs.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
    }, [executionLogs, llmStreamEvents, processStreamEvents]);
    // 自动滚动到底部
    useEffect(() => {
        if (logsEndRef.current) {
            logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [allLogs.length]);
    // 过滤日志
    const filteredLogs = useMemo(() => {
        return sortLogsForView(filterLogsForView(allLogs, activeView), activeView);
    }, [allLogs, activeView]);
    useEffect(() => {
        if (manualViewSelected || allLogs.length === 0 || filteredLogs.length > 0)
            return;
        const nextView = firstNonEmptyActivityView(allLogs);
        if (nextView && nextView !== activeView) {
            setActiveView(nextView);
        }
    }, [activeView, allLogs, filteredLogs.length, manualViewSelected]);
    const selectActivityView = (view) => {
        setManualViewSelected(true);
        setActiveView(view);
    };
    // 获取当前状态描述
    const currentStatus = PHASE_DESCRIPTIONS[currentPhase] || PHASE_DESCRIPTIONS['idle'];
    // 角色主题色
    const theme = ROLE_ACTIVITY_THEMES[role];
    const toggleLogExpand = (id) => {
        setExpandedLogs(prev => {
            const next = new Set(prev);
            if (next.has(id)) {
                next.delete(id);
            }
            else {
                next.add(id);
            }
            return next;
        });
    };
    return (_jsxs("div", { "data-testid": "realtime-activity-panel", className: "h-full flex flex-col bg-slate-950", children: [_jsxs("div", { className: cn('flex h-16 items-center justify-between gap-3 border-b px-4', isRunning ? theme.border + ' ' + theme.bg : 'border-white/10'), children: [_jsxs("div", { className: "flex min-w-0 items-center gap-3", children: [_jsxs("div", { className: "relative", children: [_jsx("div", { className: cn('w-3 h-3 rounded-full', isRunning ? `${theme.runningDot} animate-pulse` : 'bg-slate-500') }), isRunning && (_jsx("div", { className: cn('absolute inset-0 w-3 h-3 rounded-full', `${theme.runningDot} animate-ping opacity-75`) }))] }), _jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: cn('truncate text-sm font-semibold', isRunning ? theme.primaryColor : 'text-slate-400'), children: currentStatus.text }), isRunning && (_jsx(Loader2, { className: cn('w-3.5 h-3.5 animate-spin', currentStatus.color) }))] }), _jsxs("div", { className: "flex min-w-0 items-center gap-1.5 text-[10px] text-slate-500", children: [_jsx(Activity, { className: "h-3 w-3 shrink-0" }), _jsx("span", { className: "truncate", children: "\u5B9E\u65F6\u6D3B\u52A8" }), _jsx("span", { className: "text-slate-600", children: "\u2022" }), _jsxs("span", { className: "shrink-0", children: [allLogs.length, " \u6761\u8BB0\u5F55"] })] })] })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-1 rounded-lg border border-white/10 bg-white/5 p-1", children: [_jsx(ViewTab, { icon: _jsx(Brain, { className: "w-3.5 h-3.5" }), label: "\u601D\u8003", active: activeView === 'thinking', onClick: () => selectActivityView('thinking'), color: "purple" }), _jsx(ViewTab, { icon: _jsx(Wrench, { className: "w-3.5 h-3.5" }), label: "\u5DE5\u5177", active: activeView === 'tools', onClick: () => selectActivityView('tools'), color: "cyan" }), _jsx(ViewTab, { icon: _jsx(ScrollText, { className: "w-3.5 h-3.5" }), label: "\u65E5\u5FD7", active: activeView === 'logs', onClick: () => selectActivityView('logs'), color: "blue" }), _jsx(ViewTab, { icon: _jsx(FileCode, { className: "w-3.5 h-3.5" }), label: "\u6587\u4EF6", active: activeView === 'files', onClick: () => selectActivityView('files'), color: "emerald" })] })] }), _jsx("div", { className: "flex-1 overflow-hidden", children: _jsxs("div", { className: "h-full overflow-y-auto p-4 space-y-2 custom-scrollbar", children: [filteredLogs.length === 0 && (_jsxs("div", { className: "flex flex-col items-center justify-center h-full text-slate-500", children: [_jsx(Sparkles, { className: "w-8 h-8 mb-2 opacity-50" }), _jsxs("p", { className: "text-sm", children: ["\u6682\u65E0", activeView === 'thinking' ? '思考' : activeView === 'tools' ? '工具' : activeView === 'logs' ? '日志' : '文件', "\u8BB0\u5F55"] })] })), filteredLogs.map((log, index) => (_jsx(LogItem, { log: log, isExpanded: expandedLogs.has(log.id), onToggle: () => toggleLogExpand(log.id), role: role }, `${log.source || log.level || 'log'}-${log.id || 'no-id'}-${index}`))), _jsx("div", { ref: logsEndRef })] }) }), _jsxs("div", { className: "h-12 flex items-center justify-between px-4 border-t border-white/10 bg-white/5", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-slate-500", children: [_jsx(TerminalSquare, { className: "w-3.5 h-3.5" }), _jsxs("span", { children: [theme.label, " \u76D1\u63A7"] })] }), _jsxs("div", { className: "flex items-center gap-2 text-xs text-slate-500", children: [_jsx(Layers, { className: "w-3.5 h-3.5" }), _jsxs("span", { children: [filteredLogs.length, " / ", allLogs.length] })] })] })] }));
}
function ViewTab({ icon, label, active, onClick, color }) {
    return (_jsxs("button", { type: "button", onClick: onClick, "aria-label": `查看${label}记录`, title: `查看${label}记录`, className: cn('flex h-7 w-7 shrink-0 items-center justify-center rounded text-xs font-medium transition-colors', active
            ? VIEW_TAB_TONES[color]
            : 'text-slate-500 hover:text-slate-300'), children: [icon, _jsx("span", { className: "sr-only", children: label })] }));
}
function readMetaString(meta, keys) {
    if (!meta)
        return '';
    for (const key of keys) {
        const value = meta[key];
        const token = String(value || '').trim();
        if (token)
            return token;
    }
    return '';
}
function evidenceChipsForLog(log) {
    const meta = log.meta && typeof log.meta === 'object' ? log.meta : null;
    const tool = readMetaString(meta, ['tool', 'tool_name', 'name']);
    const path = readMetaString(meta, ['path', 'file', 'filePath', 'file_path', 'target', 'targetPath', 'target_path']);
    const operation = readMetaString(meta, ['operation', 'op', 'action']);
    const chips = [tool, path, operation].filter((chip) => chip.length > 0);
    return Array.from(new Set(chips)).slice(0, 4);
}
function LogItem({ log, isExpanded, onToggle, role }) {
    const level = log.level || 'info';
    const streamToken = streamEventToken(log);
    const colors = LOG_LEVEL_COLORS[level] || LOG_LEVEL_COLORS.info;
    const evidenceChips = evidenceChipsForLog(log);
    const time = new Date(log.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const eventBadgeLabel = streamToken === 'thinking_chunk'
        ? '思考流'
        : streamToken === 'thinking_preview'
            ? '思考预览'
            : streamToken === 'content_chunk'
                ? '输出流'
                : streamToken === 'content_preview'
                    ? '输出预览'
                    : streamToken === 'llm_waiting' || streamToken === 'call_start' || streamToken === 'llm_call_start'
                        ? '等待响应'
                        : streamToken === 'tool_call'
                            ? '工具调用'
                            : streamToken === 'tool_result'
                                ? '工具结果'
                                : streamToken === 'file_written' || streamToken === 'file_created'
                                    ? '文件写入'
                                    : streamToken === 'file_modified'
                                        ? '文件修改'
                                        : streamToken === 'file_deleted'
                                            ? '文件删除'
                                            : streamToken === 'artifact_created' || streamToken === 'delivery_artifact'
                                                ? '交付物'
                                                : isToolStreamEvent(streamToken)
                                                    ? '工具事件'
                                                    : '';
    const displayMessage = displayLogText(log.message, eventBadgeLabel || streamToken || level.toUpperCase());
    const displayDetails = isObjectObjectText(log.details) ? '' : log.details;
    return (_jsxs("div", { className: cn('rounded-lg border backdrop-blur-sm transition-all', colors.bg, colors.border, isExpanded ? 'shadow-lg' : 'hover:shadow-md'), children: [_jsxs("button", { type: "button", onClick: onToggle, className: "w-full flex items-start gap-3 p-3 text-left", children: [_jsxs("div", { className: cn('mt-0.5', colors.icon), children: [streamToken === 'thinking_chunk' && _jsx(Brain, { className: "w-4 h-4" }), streamToken === 'thinking_preview' && _jsx(Brain, { className: "w-4 h-4" }), streamToken === 'content_chunk' && _jsx(Terminal, { className: "w-4 h-4" }), streamToken === 'content_preview' && _jsx(Terminal, { className: "w-4 h-4" }), streamToken === 'tool_call' && _jsx(Wrench, { className: "w-4 h-4" }), streamToken === 'tool_result' && _jsx(CheckCircle2, { className: "w-4 h-4" }), isToolStreamEvent(streamToken) && streamToken !== 'tool_call' && streamToken !== 'tool_result' && _jsx(Wrench, { className: "w-4 h-4" }), streamToken && !isToolStreamEvent(streamToken) && level === 'tool' && _jsx(Wrench, { className: "w-4 h-4" }), !streamToken && level === 'thinking' && _jsx(Brain, { className: "w-4 h-4" }), !streamToken && level === 'tool' && _jsx(Wrench, { className: "w-4 h-4" }), !streamToken && level === 'exec' && _jsx(Play, { className: "w-4 h-4" }), !streamToken && level === 'success' && _jsx(CheckCircle2, { className: "w-4 h-4" }), !streamToken && level === 'warning' && _jsx(AlertCircle, { className: "w-4 h-4" }), !streamToken && level === 'error' && _jsx(AlertCircle, { className: "w-4 h-4" }), !streamToken && level === 'info' && _jsx(Activity, { className: "w-4 h-4" })] }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: cn('text-xs font-semibold', colors.text), children: log.source || level.toUpperCase() }), eventBadgeLabel && (_jsx("span", { className: "rounded border border-cyan-400/30 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-200", children: eventBadgeLabel })), log.title && (_jsx("span", { className: "text-xs text-slate-400 truncate", children: log.title }))] }), _jsxs("div", { className: "flex items-center gap-2 text-[10px] text-slate-500", children: [_jsx(Clock, { className: "w-3 h-3" }), _jsx("span", { children: time }), isExpanded ? _jsx(ChevronDown, { className: "w-3 h-3" }) : _jsx(ChevronRight, { className: "w-3 h-3" })] })] }), _jsx("div", { className: cn('mt-1 text-xs text-slate-200', !isExpanded && 'line-clamp-2'), children: displayMessage }), log.tags && log.tags.length > 0 && (_jsx("div", { className: "mt-2 flex flex-wrap gap-1", children: log.tags.map((tag, i) => (_jsx("span", { className: cn('px-1.5 py-0.5 text-[10px] rounded border', ROLE_ACTIVITY_THEMES[role].tag), children: tag }, i))) })), evidenceChips.length > 0 && (_jsx("div", { className: "mt-2 flex flex-wrap gap-1", children: evidenceChips.map((chip) => (_jsx("span", { className: "rounded border border-white/10 bg-black/20 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: chip }, chip))) }))] })] }), isExpanded && displayDetails && (_jsx("div", { className: "px-4 pb-3", children: _jsx("div", { className: "text-xs text-slate-400 bg-black/20 rounded p-2 font-mono whitespace-pre-wrap", children: displayDetails }) }))] }));
}
