import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Bot, User, CheckCircle, MessageSquare, Activity as ActivityIcon, TrendingUp, AlertTriangle, ChevronDown, ChevronRight, Trash2, } from 'lucide-react';
import { useMemo, useState } from 'react';
import { DialoguePanelSkeleton } from './DialoguePanelSkeleton';
import { StatusBadge } from '@/app/components/ui/badge';
const speakerStyles = {
    PM: {
        icon: User,
        iconBg: 'bg-blue-500/[0.15]',
        iconText: 'text-blue-400',
        nameText: 'text-blue-400',
        filterActive: 'bg-blue-500/20 text-blue-300',
        border: 'border-blue-500/30'
    },
    Director: {
        icon: Bot,
        iconBg: 'bg-slate-500/[0.15]',
        iconText: 'text-slate-400',
        nameText: 'text-slate-300',
        filterActive: 'bg-slate-500/20 text-slate-300',
        border: 'border-slate-500/30'
    },
    QA: {
        icon: CheckCircle,
        iconBg: 'bg-emerald-500/[0.15]',
        iconText: 'text-emerald-400',
        nameText: 'text-emerald-400',
        filterActive: 'bg-emerald-500/20 text-emerald-300',
        border: 'border-emerald-500/30'
    },
    Reviewer: {
        icon: ActivityIcon,
        iconBg: 'bg-amber-500/[0.15]',
        iconText: 'text-amber-400',
        nameText: 'text-amber-400',
        filterActive: 'bg-amber-500/20 text-amber-300',
        border: 'border-amber-500/30'
    },
    System: {
        icon: MessageSquare,
        iconBg: 'bg-accent/10',
        iconText: 'text-text-muted',
        nameText: 'text-text-muted',
        filterActive: 'bg-accent/15 text-accent-text',
        border: 'border-border'
    },
};
const STATUS_RANK = {
    ERROR: 4,
    FAIL: 4,
    FAILED: 4,
    BLOCKED: 3,
    SUCCESS: 2,
    PASS: 2,
};
function formatDialogueContent(content) {
    if (typeof content === 'string')
        return content;
    if (content == null)
        return '';
    try {
        const serialized = JSON.stringify(content, null, 2);
        return typeof serialized === 'string' ? serialized : String(content);
    }
    catch {
        return String(content);
    }
}
function normalizeStatus(status) {
    const raw = status.trim().toUpperCase();
    return raw === 'FAILED' ? 'FAIL' : raw;
}
function mergeStatus(previous, next) {
    const normalizedNext = normalizeStatus(next);
    if (!previous)
        return normalizedNext;
    const normalizedPrevious = normalizeStatus(previous);
    return (STATUS_RANK[normalizedNext] ?? 0) > (STATUS_RANK[normalizedPrevious] ?? 0)
        ? normalizedNext
        : normalizedPrevious;
}
export function DialoguePanel({ events, live, loading = false, onClearLogs, clearingLogs = false, }) {
    const [filterSpeaker, setFilterSpeaker] = useState(null);
    const [viewMode, setViewMode] = useState('tasks');
    const [expandedTasks, setExpandedTasks] = useState({});
    const filteredEvents = filterSpeaker ? events.filter((e) => e.speaker === filterSpeaker) : events;
    const taskGroups = useMemo(() => {
        const groups = new Map();
        const extractTitle = (content) => {
            const assignMatch = content.match(/Assigning task\s+\S+:\s*(.+)$/i);
            if (assignMatch?.[1])
                return assignMatch[1].trim();
            const cnMatch = content.match(/任务《(.+?)》/);
            if (cnMatch?.[1])
                return cnMatch[1].trim();
            return '';
        };
        const extractStatus = (content) => {
            const match = content.match(/(SUCCESS|PASS|FAILED|FAIL|BLOCKED|ERROR)/i);
            if (!match?.[1])
                return '';
            return normalizeStatus(match[1]);
        };
        const extractReviewerFindings = (content) => {
            const markerIdx = content.search(/Reviewer[:：]/);
            if (markerIdx === -1)
                return [];
            const slice = content.slice(markerIdx);
            const parts = slice
                .split(/-\s+/)
                .slice(1)
                .map((part) => part.trim())
                .filter(Boolean);
            if (parts.length > 0)
                return parts;
            const tail = slice.replace(/Reviewer[:：]/, '').trim();
            return tail ? [tail] : [];
        };
        const extractModifiedCount = (content) => {
            const match = content.match(/Modified\s+(\d+)\s+files?/i);
            if (match?.[1])
                return Number(match[1]);
            const cn = content.match(/改动文件数[:：]\s*(\d+)/);
            if (cn?.[1])
                return Number(cn[1]);
            return undefined;
        };
        const extractAttempt = (content) => {
            const match = content.match(/attempt\s+(\d+)\s*\/\s*(\d+)/i);
            if (!match?.[1] || !match?.[2])
                return null;
            return { current: Number(match[1]), total: Number(match[2]) };
        };
        events.forEach((event, index) => {
            const taskId = event.refs?.task_id || 'GLOBAL';
            const existing = groups.get(taskId);
            const group = existing || {
                taskId,
                events: [],
                reviewerFindings: [],
                order: index,
            };
            group.events.push(event);
            group.startTs = group.startTs || event.timestamp;
            group.endTs = event.timestamp || group.endTs;
            const contentText = formatDialogueContent(event.content);
            if (!group.title) {
                const title = extractTitle(contentText);
                if (title)
                    group.title = title;
            }
            const status = extractStatus(contentText);
            if (status)
                group.status = mergeStatus(group.status, status);
            const findings = extractReviewerFindings(contentText);
            if (findings.length)
                group.reviewerFindings.push(...findings);
            const modified = extractModifiedCount(contentText);
            if (typeof modified === 'number')
                group.modifiedCount = modified;
            const attempt = extractAttempt(contentText);
            if (attempt) {
                group.attemptCurrent = attempt.current;
                group.attemptTotal = attempt.total;
            }
            if (!existing)
                groups.set(taskId, group);
        });
        return Array.from(groups.values()).sort((a, b) => a.order - b.order);
    }, [events]);
    const latestTaskId = taskGroups.length > 0 ? taskGroups[taskGroups.length - 1].taskId : '';
    const stats = useMemo(() => {
        const taskIds = new Set();
        const resultByTaskId = new Map();
        events.forEach((event) => {
            const taskId = event.refs?.task_id;
            if (taskId) {
                taskIds.add(taskId);
            }
            if (event.type === 'result' && taskId) {
                const match = formatDialogueContent(event.content).match(/(?:Result|Event receipt):\s*([A-Za-z]+)/i);
                if (match?.[1]) {
                    resultByTaskId.set(taskId, mergeStatus(resultByTaskId.get(taskId), match[1]));
                }
            }
        });
        const totalTasks = taskIds.size;
        const completedTasks = resultByTaskId.size;
        const successCount = Array.from(resultByTaskId.values()).filter((status) => status === 'SUCCESS' || status === 'PASS').length;
        const successRate = completedTasks > 0 ? Math.round((successCount / completedTasks) * 100) : 0;
        return { totalTasks, completedTasks, successRate };
    }, [events]);
    return (_jsx("div", { className: "soft-panel-subtle h-full flex flex-col border-l-0 relative overflow-hidden", children: _jsxs("div", { className: "relative z-20 flex flex-col h-full", children: [_jsxs("div", { className: "soft-panel-subtle px-4 py-4 border-b relative mx-2 mt-2 rounded-lg", children: [_jsxs("div", { className: "relative z-10 mb-3 flex flex-wrap items-start justify-between gap-2", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("div", { className: "soft-raised flex size-9 shrink-0 items-center justify-center rounded-lg text-text-muted", children: _jsx(MessageSquare, { className: "size-5" }) }), _jsx("h2", { className: "whitespace-nowrap text-sm font-heading font-black text-text-main uppercase tracking-[0.18em] leading-tight", children: "\u5BF9\u8BDD\u6D41" })] }), _jsxs("div", { className: "flex min-w-0 flex-wrap items-center justify-end gap-2 text-[10px] text-text-muted font-mono", children: [onClearLogs ? (_jsxs("button", { onClick: () => {
                                                onClearLogs();
                                            }, disabled: clearingLogs, className: "soft-chip flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-text-dim transition-colors hover:border-border-glow hover:text-text-main disabled:cursor-not-allowed disabled:opacity-50", title: clearingLogs ? '清空中...' : '清空对话日志', "aria-label": clearingLogs ? '清空中' : '清空对话日志', children: [_jsx(Trash2, { className: "size-3" }), _jsx("span", { className: "sr-only", children: clearingLogs ? '清空中' : '清空日志' })] })) : null, _jsxs("div", { className: "soft-chip flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-sm px-2", children: [_jsx(ActivityIcon, { className: `size-3 ${live ? 'text-emerald-400 animate-pulse' : 'text-text-dim'}` }), _jsx("span", { className: live ? 'text-emerald-400 font-bold' : 'text-text-dim font-bold tracking-widest', children: live ? '实时' : '离线' })] }), _jsxs("div", { className: "soft-inset flex shrink-0 items-center gap-1 rounded-sm p-0.5 no-drag", children: [_jsx("button", { onClick: () => setViewMode('tasks'), className: `h-6 whitespace-nowrap rounded-sm px-2 transition-all font-black uppercase tracking-tighter text-[9px] ${viewMode === 'tasks'
                                                        ? 'bg-accent/15 text-accent-text border border-border-glow'
                                                        : 'text-text-dim hover:text-text-main'}`, children: "\u4EFB\u52A1\u89C6\u56FE" }), _jsx("button", { onClick: () => setViewMode('stream'), className: `h-6 whitespace-nowrap rounded-sm px-2 transition-all font-black uppercase tracking-tighter text-[9px] ${viewMode === 'stream'
                                                        ? 'bg-accent/15 text-accent-text border border-border-glow'
                                                        : 'text-text-dim hover:text-text-main'}`, children: "\u65E5\u5FD7\u6D41" })] })] })] }), viewMode === 'stream' ? (_jsxs("div", { className: "flex flex-wrap gap-1.5 pt-2", children: [_jsx("button", { onClick: () => setFilterSpeaker(null), className: `px-2 py-1 text-[10px] rounded-md border transition-all ${!filterSpeaker
                                        ? 'bg-accent/15 text-accent-text border-border-glow'
                                        : 'bg-accent/10 text-text-dim border-transparent hover:bg-accent/15'}`, children: "\u5168\u90E8" }), Object.keys(speakerStyles).map((speaker) => {
                                    const style = speakerStyles[speaker];
                                    return (_jsx("button", { onClick: () => setFilterSpeaker(speaker === filterSpeaker ? null : speaker), className: `px-2 py-1 text-[10px] rounded-md border border-transparent transition-all ${filterSpeaker === speaker
                                            ? style.filterActive
                                            : 'bg-accent/10 text-text-dim hover:bg-accent/15'}`, children: speaker }, speaker));
                                })] })) : null] }), _jsx("div", { className: "flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar", children: loading ? (_jsx(DialoguePanelSkeleton, {})) : viewMode === 'tasks' ? (taskGroups.length === 0 ? (_jsxs("div", { className: "text-xs text-text-dim flex flex-col items-center justify-center h-40 opacity-50", children: [_jsx(MessageSquare, { className: "size-8 mb-2 opacity-50" }), _jsx("span", { children: "(\u6682\u65E0\u4EFB\u52A1)" })] })) : (taskGroups.map((group) => {
                        const isExpanded = expandedTasks[group.taskId] ?? (group.taskId === latestTaskId);
                        const status = group.status || 'UNKNOWN';
                        const statusColor = status === 'SUCCESS' || status === 'PASS' ? 'success'
                            : status === 'FAIL' ? 'error'
                                : status === 'BLOCKED' ? 'warning'
                                    : 'default';
                        const conflict = (status === 'SUCCESS' || status === 'PASS') && group.reviewerFindings.length > 0;
                        const modifiedLabel = typeof group.modifiedCount === 'number' ? `${group.modifiedCount} files` : '-';
                        const attemptLabel = group.attemptTotal
                            ? `attempt ${group.attemptCurrent ?? group.attemptTotal}/${group.attemptTotal}`
                            : '';
                        const timeRange = group.startTs && group.endTs ? `${group.startTs} - ${group.endTs}` : group.endTs || '';
                        return (_jsxs("div", { className: "soft-panel rounded-lg p-4 transition-all hover:border-border-glow relative group/task", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2 text-[10px] text-text-muted font-mono", children: [_jsx("span", { className: "rounded px-1.5 py-0.5 bg-accent/10 border border-border", children: group.taskId }), attemptLabel ? (_jsx("span", { className: "rounded px-1.5 py-0.5 bg-accent/10 opacity-70", children: attemptLabel })) : null, timeRange ? _jsx("span", { className: "text-text-dim opacity-50", children: timeRange }) : null] }), _jsx("div", { className: "mt-1 text-sm font-semibold text-text-main", children: group.title || (group.taskId === 'GLOBAL' ? '系统/未归类' : '任务进度') }), _jsxs("div", { className: "mt-2 flex flex-wrap items-center gap-2 text-[10px] text-text-muted", children: [_jsxs(StatusBadge, { color: statusColor, variant: "soft", className: "text-[10px]", children: ["\u7ED3\u679C: ", status] }), _jsxs(StatusBadge, { color: "default", variant: "soft", className: "text-[10px]", children: ["\u6539\u52A8: ", modifiedLabel] }), _jsxs(StatusBadge, { color: "default", variant: "soft", className: "text-[10px]", children: ["\u98CE\u9669: ", group.reviewerFindings.length || 0] }), conflict ? (_jsxs(StatusBadge, { color: "warning", variant: "dot", className: "text-[10px]", children: [_jsx(AlertTriangle, { className: "size-3" }), " \u7ED3\u8BBA\u51B2\u7A81"] })) : null] })] }), _jsxs("button", { type: "button", onClick: () => setExpandedTasks((prev) => ({
                                                ...prev,
                                                [group.taskId]: !isExpanded,
                                            })), className: "flex items-center gap-1 rounded px-2 py-1 text-[10px] text-text-dim hover:text-text-main hover:bg-accent/10 transition-colors", children: [isExpanded ? _jsx(ChevronDown, { className: "size-3" }) : _jsx(ChevronRight, { className: "size-3" }), _jsx("span", { children: isExpanded ? '收起' : '展开' })] })] }), group.reviewerFindings.length > 0 ? (_jsxs("div", { className: "mt-3 rounded-md border border-status-warning/30 bg-status-warning/5 px-3 py-2 text-xs text-status-warning", children: [_jsxs("div", { className: "mb-1 font-semibold flex items-center gap-2", children: [_jsx(AlertTriangle, { className: "size-3" }), " Reviewer \u98CE\u9669\u70B9"] }), _jsxs("ul", { className: "list-disc pl-4 space-y-1 opacity-90", children: [group.reviewerFindings.slice(0, 4).map((item, idx) => (_jsx("li", { children: item }, `${group.taskId}-finding-${idx}`))), group.reviewerFindings.length > 4 ? _jsx("li", { children: "..." }) : null] })] })) : null, isExpanded ? (_jsxs("div", { className: "mt-3 space-y-2 relative", children: [_jsx("div", { className: "absolute left-[11px] top-2 bottom-2 w-px bg-border" }), group.events.map((event, idx) => {
                                            const style = speakerStyles[event.speaker] ?? speakerStyles.System;
                                            const Icon = style.icon;
                                            return (_jsxs("div", { className: "flex gap-3 relative z-10 pl-2 group/msg", children: [_jsx("div", { className: `flex-shrink-0 w-6 h-6 rounded-full ${style.iconBg} flex items-center justify-center ring-2 ring-accent/20 transition-transform group-hover/msg:scale-105`, children: _jsx(Icon, { className: `size-3 ${style.iconText}` }) }), _jsxs("div", { className: "soft-panel-subtle min-w-0 flex-1 p-3 transition-all rounded-md", children: [_jsxs("div", { className: "flex items-center gap-2 text-[10px] text-text-dim font-mono mb-1", children: [_jsx("span", { className: `${style.nameText} font-bold`, children: event.speaker }), _jsx("span", { className: "opacity-50", children: event.type || 'log' }), _jsx("span", { className: "opacity-50 ml-auto", children: event.timestamp })] }), _jsx("div", { className: "text-xs text-text-main whitespace-pre-wrap break-all leading-relaxed opacity-90", children: formatDialogueContent(event.content) })] })] }, event.eventId || `${event.speaker}-${event.seq ?? idx}-${event.timestamp ?? ''}`));
                                        })] })) : null] }, group.taskId));
                    }))) : filteredEvents.length === 0 ? (_jsx("div", { className: "text-xs text-text-dim flex flex-col items-center justify-center h-40 opacity-50", children: _jsx("span", { children: "(\u6682\u65E0\u5BF9\u8BDD\u4E8B\u4EF6)" }) })) : (filteredEvents.map((event, index) => {
                        const style = speakerStyles[event.speaker] ?? speakerStyles.System;
                        const Icon = style.icon;
                        return (_jsxs("div", { className: "flex gap-3 group/msg", children: [_jsx("div", { className: `flex-shrink-0 w-8 h-8 rounded-full ${style.iconBg} flex items-center justify-center ring-2 ring-transparent group-hover/msg:ring-accent/20 transition-all`, children: _jsx(Icon, { className: `size-4 ${style.iconText}` }) }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 mb-1", children: [_jsx("span", { className: `text-sm font-semibold ${style.nameText}`, children: event.speaker }), _jsx("span", { className: "text-[10px] text-text-dim font-mono", children: event.timestamp }), event.refs?.task_id && (_jsx("span", { className: "text-[10px] px-1.5 py-0 rounded bg-accent/10 text-text-dim border border-border", children: event.refs.task_id })), event.refs?.phase && (_jsx("span", { className: "text-[10px] px-1.5 py-0 rounded bg-accent/10 text-text-dim border border-border", children: event.refs.phase }))] }), _jsx("div", { className: "soft-panel-subtle px-5 py-4 transition-all rounded-md", children: _jsx("p", { className: "text-sm text-text-main leading-relaxed break-all whitespace-pre-wrap", children: formatDialogueContent(event.content) }) })] })] }, event.eventId || `${event.speaker}-${event.seq ?? index}-${event.timestamp ?? ''}`));
                    })) }), _jsx("div", { className: "soft-panel-subtle border-t p-3 mx-2 mb-2 relative rounded-lg", children: _jsxs("div", { className: "flex items-center justify-between text-[9px] text-text-dim font-mono relative z-10", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsxs("span", { className: "flex items-center gap-1", children: [_jsx("div", { className: "size-1 bg-slate-400 rounded-full animate-pulse" }), " \u603B\u4E8B\u4EF6: ", events.length] }), _jsxs("span", { className: "flex items-center gap-1", children: [_jsx("div", { className: "size-1 bg-slate-500 rounded-full" }), " \u4EFB\u52A1\u6570: ", stats.totalTasks] }), _jsxs("span", { className: "flex items-center gap-1", children: [_jsx("div", { className: "size-1 bg-slate-500 rounded-full" }), " \u5DF2\u5B8C\u6210: ", stats.completedTasks] })] }), _jsxs(StatusBadge, { color: "success", variant: "dot", pulse: true, children: [_jsx(TrendingUp, { className: "size-3" }), _jsxs("span", { className: "font-black", children: ["\u6210\u529F\u7387: ", stats.successRate, "%"] })] })] }) })] }) }));
}
