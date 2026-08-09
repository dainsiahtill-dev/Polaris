"use client";
import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useRef, useEffect } from 'react';
import { cn } from '@/app/components/ui/utils';
import { CheckCircle2, XCircle, Loader2, AlertTriangle, Brain, FileText, Zap, Clock, MessageSquare, } from 'lucide-react';
const levelIcons = {
    info: _jsx(Clock, { className: "h-3.5 w-3.5 text-white/40" }),
    success: _jsx(CheckCircle2, { className: "h-3.5 w-3.5 text-emerald-400" }),
    warning: _jsx(AlertTriangle, { className: "h-3.5 w-3.5 text-amber-400" }),
    error: _jsx(XCircle, { className: "h-3.5 w-3.5 text-red-400" }),
    thinking: _jsx(Brain, { className: "h-3.5 w-3.5 text-blue-400" }),
    tool: _jsx(Zap, { className: "h-3.5 w-3.5 text-cyan-400" }),
    exec: _jsx(Loader2, { className: "h-3.5 w-3.5 text-orange-400" }),
};
const levelColors = {
    info: 'border-white/5 bg-white/[0.02]',
    success: 'border-emerald-500/20 bg-emerald-500/5',
    warning: 'border-amber-500/20 bg-amber-500/5',
    error: 'border-red-500/20 bg-red-500/5',
    thinking: 'border-blue-500/20 bg-blue-500/5',
    tool: 'border-cyan-500/20 bg-cyan-500/5',
    exec: 'border-orange-500/20 bg-orange-500/5',
};
const sourceIcons = {
    PM: _jsx(Brain, { className: "h-3 w-3" }),
    Director: _jsx(Zap, { className: "h-3 w-3" }),
    QA: _jsx(CheckCircle2, { className: "h-3 w-3" }),
    CE: _jsx(FileText, { className: "h-3 w-3" }),
    System: _jsx(Clock, { className: "h-3 w-3" }),
    AGENTS: _jsx(FileText, { className: "h-3 w-3" }),
};
export function ExecutionLog({ logs, maxHeight = "200px", className }) {
    const scrollRef = useRef(null);
    // 自动滚动到底部
    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [logs]);
    const formatTime = (timestamp) => {
        try {
            const date = new Date(timestamp);
            return date.toLocaleTimeString('zh-CN', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
            });
        }
        catch {
            return timestamp;
        }
    };
    return (_jsxs("div", { className: cn("rounded-xl border border-white/10 bg-white/5", className), children: [_jsxs("div", { className: "flex items-center justify-between border-b border-white/5 px-3 py-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(MessageSquare, { className: "h-4 w-4 text-white/40" }), _jsx("span", { className: "text-xs font-medium text-white/60", children: "\u6267\u884C\u65E5\u5FD7" })] }), _jsxs("div", { className: "text-[10px] text-white/30", children: [logs.length, " \u6761"] })] }), _jsx("div", { ref: scrollRef, className: "space-y-1 overflow-y-auto p-2", style: { maxHeight }, children: logs.length === 0 ? (_jsx("div", { className: "py-4 text-center text-xs text-white/20", children: "\u7B49\u5F85\u6267\u884C..." })) : (logs.map((log, index) => {
                    const isLatest = index === logs.length - 1;
                    return (_jsx("div", { className: cn("rounded-lg border p-2 text-[11px] transition-all", levelColors[log.level], isLatest && "ring-1 ring-white/10"), children: _jsxs("div", { className: "flex items-start gap-2", children: [_jsx("div", { className: "mt-0.5 shrink-0", children: levelIcons[log.level] }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-white/40", children: [_jsx("span", { className: "font-mono text-[10px]", children: formatTime(log.timestamp) }), log.source && (_jsxs("span", { className: "flex items-center gap-1 rounded bg-white/5 px-1.5 py-0.5 text-[10px]", children: [sourceIcons[log.source] || null, log.source] }))] }), _jsx("div", { className: "mt-0.5 text-white/70", children: log.message }), log.details && (_jsx("div", { className: "mt-1 text-white/40", children: log.details })), log.meta && Object.keys(log.meta).length > 0 && (_jsx("div", { className: "mt-1.5 flex flex-wrap gap-1", children: Object.entries(log.meta).map(([key, value]) => (_jsxs("span", { className: "rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-white/30", children: [key, ": ", String(value)] }, key))) }))] })] }) }, log.id));
                })) })] }));
}
