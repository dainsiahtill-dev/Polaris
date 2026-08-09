import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Activity as ActivityIcon, CheckCircle, Zap, FileText } from 'lucide-react';
function formatDuration(startedAt) {
    if (!startedAt)
        return '-';
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
}
export function StatusBar({ pmRunning, directorRunning, pmStartedAt, directorStartedAt, pmMode, failures, iteration, pmBackend, directorModel, backendError, onOpenLogs, gitPresent, pmError, directorError, ollamaError, successes, total, rate, onPingHealth, healthStatus, lancedbOk, lancedbError, }) {
    const pmDuration = pmRunning ? formatDuration(pmStartedAt) : '-';
    const directorDuration = directorRunning ? formatDuration(directorStartedAt) : '-';
    const pmModeLabel = pmRunning && pmMode ? ` (${pmMode})` : '';
    const backendState = backendError ? 'Error' : 'OK';
    const backendClass = backendError ? 'text-red-400' : 'text-green-400';
    const gitState = gitPresent === null ? '未知' : gitPresent ? 'OK' : '缺失';
    const gitClass = gitPresent === null ? 'text-gray-500' : gitPresent ? 'text-green-400' : 'text-yellow-400';
    const lancedbState = lancedbOk === null || lancedbOk === undefined ? '未知' : lancedbOk ? 'OK' : '缺失';
    const lancedbClass = lancedbOk === null || lancedbOk === undefined
        ? 'text-gray-500'
        : lancedbOk
            ? 'text-green-400'
            : 'text-red-400';
    const successLabel = typeof successes === 'number' && typeof total === 'number'
        ? `${successes}/${total}${typeof rate === 'number' ? ` (${Math.round(rate * 100)}%)` : ''}`
        : '—';
    return (_jsxs("div", { className: "fixed bottom-4 right-4 z-[60] flex items-center gap-3 px-3 py-1.5 rounded-full glass-bubble shadow-lg border-white/10 animate-in fade-in slide-in-from-bottom-4 duration-500 cursor-default", children: [_jsxs("div", { className: "flex items-center gap-2 pr-3 border-r border-white/10", children: [_jsxs("div", { className: "flex items-center gap-1.5", children: [_jsx("div", { className: `w-2 h-2 rounded-full ${pmRunning ? 'bg-status-success text-status-success animate-pulse' : 'bg-text-dim text-text-dim'}` }), _jsx("span", { className: "text-[10px] text-text-muted font-bold tracking-tight", children: "PM" })] }), _jsxs("div", { className: "flex items-center gap-1.5", children: [_jsx("div", { className: `w-2 h-2 rounded-full ${directorRunning ? 'bg-status-info text-status-info animate-pulse' : 'bg-text-dim text-text-dim'}` }), _jsx("span", { className: "text-[10px] text-text-muted font-bold tracking-tight", children: "Director" })] })] }), _jsxs("div", { className: "flex items-center gap-3 pr-3 border-r border-white/10", children: [_jsxs("div", { className: "flex items-center gap-1", title: "QA Pass Rate", children: [_jsx(CheckCircle, { className: "size-3 text-status-success" }), _jsx("span", { className: "text-[10px] text-text-main font-bold", children: successLabel })] }), _jsxs("div", { className: "flex items-center gap-1", title: "\u8F6E\u6B21", children: [_jsx(Zap, { className: "size-3 text-accent" }), _jsx("span", { className: "text-[10px] text-text-main", children: iteration ?? '0' })] })] }), _jsxs("div", { className: "flex items-center gap-1", children: [onPingHealth && (_jsxs("button", { onClick: onPingHealth, className: "p-1 px-1.5 rounded-full hover:bg-white/10 text-text-dim hover:text-white transition-colors flex items-center gap-1", children: [_jsx(ActivityIcon, { className: "size-3" }), _jsx("span", { className: "text-[9px] uppercase font-bold", children: healthStatus === 'ok' ? '在线' : healthStatus || 'Ping' })] })), onOpenLogs && (_jsxs("button", { onClick: onOpenLogs, className: "p-1 px-1.5 rounded-full hover:bg-white/10 text-text-dim hover:text-white transition-colors flex items-center gap-1", children: [_jsx(FileText, { className: "size-3" }), _jsx("span", { className: "text-[9px] uppercase font-bold", children: "Logs" })] }))] })] }));
}
