import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Clock, Zap, PlayCircle, Square, Cpu, Database, Wifi, FileCode } from 'lucide-react';
import { useState, useEffect } from 'react';
import { UI_TERMS } from '@/app/constants/uiTerminology';
import { AnimateCountUp } from '@/app/components/ui/animate-count-up';
import { StatusBadge } from '@/app/components/ui/badge';
import { normalizeStartedAtSeconds } from '@/app/utils/runtimeDisplay';
function formatDuration(startedAt) {
    const normalizedStartedAt = normalizeStartedAtSeconds(startedAt);
    if (!normalizedStartedAt)
        return '';
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - normalizedStartedAt));
    if (seconds < 60)
        return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60)
        return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h${minutes % 60}m`;
}
function activeLabel(duration) {
    return [UI_TERMS.states.active, duration].filter(Boolean).join(' ');
}
function displayRuntimeStatus(status) {
    if (status === 'ready')
        return '就绪';
    if (status === 'blocked')
        return '阻塞';
    return '未判';
}
export function RealTimeStatusBar({ pmRunning, directorRunning, pmStartedAt, directorStartedAt, pmIteration, llmStatus, lancedbOk, fileEditEvents = [], }) {
    const [currentTime, setCurrentTime] = useState(new Date());
    useEffect(() => {
        // UI-only clock tick for elapsed-time display; no network request is made.
        const timer = setInterval(() => setCurrentTime(new Date()), 1000);
        return () => clearInterval(timer);
    }, []);
    const pmDuration = formatDuration(pmStartedAt);
    const directorDuration = formatDuration(directorStartedAt);
    const latestFileEdit = fileEditEvents
        .filter((event) => Boolean(event.filePath))
        .slice()
        .sort((a, b) => Date.parse(String(b.timestamp || '')) - Date.parse(String(a.timestamp || '')))[0] || null;
    return (_jsxs("div", { className: "soft-panel-subtle h-11 backdrop-blur-xl border-b border-white/10 flex items-center px-5 relative overflow-hidden", children: [_jsxs("div", { className: "flex items-center gap-2 flex-1 relative z-10", children: [_jsxs("div", { className: "soft-chip backdrop-blur-md rounded-lg px-3 py-1.5 flex items-center gap-2", children: [pmRunning ? (_jsx(PlayCircle, { className: "w-4 h-4 text-accent" })) : (_jsx(Square, { className: "w-4 h-4 text-text-dim" })), _jsxs("div", { className: "flex flex-col", children: [_jsx("div", { className: "text-[10px] font-semibold text-accent tracking-wide", children: UI_TERMS.roles.pm }), _jsx("div", { className: "text-[9px] text-text-muted font-mono", children: pmRunning ? activeLabel(pmDuration) : UI_TERMS.states.idle })] })] }), _jsxs("div", { className: "soft-chip backdrop-blur-md rounded-lg px-3 py-1.5 flex items-center gap-2", children: [directorRunning ? (_jsx(Cpu, { className: "w-4 h-4 text-status-info" })) : (_jsx(Square, { className: "w-4 h-4 text-text-dim" })), _jsxs("div", { className: "flex flex-col", children: [_jsx("div", { className: "text-[10px] font-semibold text-status-info tracking-wide", children: UI_TERMS.roles.director }), _jsx("div", { className: "text-[9px] text-text-muted font-mono", children: directorRunning ? activeLabel(directorDuration) : UI_TERMS.states.idle })] })] }), pmIteration !== null && (_jsxs("div", { className: "soft-chip backdrop-blur-md rounded-lg px-3 py-1.5 flex items-center gap-2", children: [_jsx(Zap, { className: "w-4 h-4 text-gold" }), _jsxs("div", { className: "flex flex-col", children: [_jsx("div", { className: "text-[10px] font-bold text-gold tracking-wider", children: "\u8F6E\u6B21" }), _jsx(AnimateCountUp, { to: pmIteration, prefix: "#", padStart: 3, duration: 0.8, className: "text-[9px] text-gold font-mono font-bold" })] })] }))] }), _jsxs("div", { className: "flex items-center gap-2 flex-1 justify-end relative z-10", children: [llmStatus && (_jsxs("div", { className: "soft-chip backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5", children: [_jsx(Wifi, { className: "w-3.5 h-3.5 text-accent shrink-0" }), _jsxs("div", { className: "flex flex-col", children: [_jsx("div", { className: "text-[8px] text-text-muted font-mono tracking-wider", children: "LLM" }), _jsx(StatusBadge, { color: llmStatus === 'ready' ? 'success' : llmStatus === 'blocked' ? 'error' : 'warning', variant: "dot", pulse: llmStatus === 'ready', className: "text-[9px] border-0 bg-transparent p-0", children: displayRuntimeStatus(llmStatus) })] })] })), lancedbOk !== undefined && (_jsxs("div", { className: "soft-chip backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5", children: [_jsx(Database, { className: "w-3.5 h-3.5 text-status-info shrink-0" }), _jsxs("div", { className: "flex flex-col", children: [_jsx("div", { className: "text-[8px] text-text-muted font-mono tracking-wider", children: "\u7ECF\u7C4D\u5E93" }), _jsx(StatusBadge, { color: lancedbOk ? 'success' : 'error', variant: "dot", pulse: lancedbOk, className: "text-[9px] border-0 bg-transparent p-0", children: lancedbOk ? '就绪' : '离线' })] })] })), latestFileEdit && (_jsxs("div", { className: "soft-chip backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5 max-w-[240px]", "data-testid": "runtime-file-edit-status", title: latestFileEdit.filePath, children: [_jsx(FileCode, { className: "w-3.5 h-3.5 text-emerald-400 shrink-0" }), _jsxs("div", { className: "flex min-w-0 flex-col", children: [_jsx("div", { className: "text-[8px] text-text-muted font-mono tracking-wider", children: "\u6587\u4EF6\u53D8\u66F4" }), _jsxs("div", { className: "truncate text-[10px] font-mono text-text-main", children: [latestFileEdit.operation, " ", latestFileEdit.filePath] })] })] })), _jsxs("div", { className: "soft-chip backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5", children: [_jsx(Clock, { className: "w-3.5 h-3.5 text-text-muted shrink-0" }), _jsxs("div", { className: "flex flex-col", children: [_jsx("div", { className: "text-[8px] text-text-muted font-mono tracking-wider", children: "\u6F0F\u523B\u65F6\u8FB0" }), _jsx("div", { className: "text-[10px] font-mono text-text-main font-bold", children: currentTime.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) })] })] })] })] }));
}
