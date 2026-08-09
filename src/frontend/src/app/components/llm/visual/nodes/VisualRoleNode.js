import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Handle, Position } from '@xyflow/react';
export function VisualRoleNode({ data }) {
    const readiness = data.readiness;
    const runtimeStatus = data.runtimeStatus;
    // Determine status color based on readiness and runtime status
    let statusColor = 'bg-amber-400';
    let statusLabel = '待命';
    if (runtimeStatus?.running) {
        statusColor = 'bg-amber-300';
        statusLabel = '运行中';
    }
    else if (readiness?.ready) {
        statusColor = 'bg-emerald-400';
        statusLabel = '就绪';
    }
    else if (readiness?.grade) {
        statusColor = 'bg-rose-400';
        statusLabel = readiness.grade;
    }
    else if (runtimeStatus?.lastRun) {
        statusColor = runtimeStatus.lastStatus === 'success' ? 'bg-emerald-400' : 'bg-rose-400';
        statusLabel = runtimeStatus.lastStatus === 'success' ? '就绪' : '失败';
    }
    // Format last run time
    const formatLastRun = (timestamp) => {
        if (!timestamp)
            return '';
        const date = new Date(timestamp);
        const now = new Date();
        const diff = now.getTime() - date.getTime();
        const minutes = Math.floor(diff / 60000);
        const hours = Math.floor(diff / 3600000);
        const days = Math.floor(diff / 86400000);
        if (minutes < 1)
            return '刚刚';
        if (minutes < 60)
            return `${minutes}分钟前`;
        if (hours < 24)
            return `${hours}小时前`;
        return `${days}天前`;
    };
    return (_jsxs("div", { className: "min-w-[200px] rounded-lg border border-slate-500/40 bg-black/80 px-3 py-2 text-text-main", children: [_jsx(Handle, { type: "target", position: Position.Left, className: "!bg-slate-300 !border-slate-200" }), _jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "text-xs font-semibold tracking-wide", children: data.label }), _jsxs("span", { className: `inline-flex items-center gap-1 rounded-full border border-white/10 px-2 py-0.5 text-[9px] uppercase ${statusColor} text-black font-bold`, children: [runtimeStatus?.running && (_jsx("span", { className: "inline-block w-1.5 h-1.5 bg-white/20 rounded-full animate-pulse" })), statusLabel] })] }), data.description ? (_jsx("div", { className: "mt-1 text-[10px] text-text-dim", children: data.description })) : null, runtimeStatus?.config?.model && (_jsx("div", { className: "mt-2 text-[9px] text-slate-300/80 border-t border-white/10 pt-1", children: _jsxs("div", { className: "flex items-center gap-1", children: [_jsx("span", { className: "text-text-dim", children: "\u6A21\u578B:" }), _jsx("span", { className: "font-medium text-slate-200", children: runtimeStatus.config.model })] }) })), runtimeStatus?.lastRun && !runtimeStatus.running && (_jsxs("div", { className: "mt-1 text-[8px] text-text-dim", children: ["\u4E0A\u6B21\u8FD0\u884C: ", formatLastRun(runtimeStatus.lastRun)] })), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1 text-[9px]", children: [data.requiresThinking ? (_jsx("span", { className: "rounded bg-slate-500/20 px-2 py-0.5 text-slate-200", children: "\u601D\u8003\u8981\u6C42" })) : (_jsx("span", { className: "rounded bg-emerald-500/20 px-2 py-0.5 text-emerald-200", children: "\u57FA\u7840\u80FD\u529B" })), typeof data.minConfidence === 'number' ? (_jsxs("span", { className: "rounded bg-black/40 px-2 py-0.5 text-text-dim", children: ["\u6700\u4F4E\u7F6E\u4FE1 ", data.minConfidence] })) : null] })] }));
}
