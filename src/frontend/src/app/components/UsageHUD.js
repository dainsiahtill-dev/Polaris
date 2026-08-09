import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Cpu, Zap } from 'lucide-react';
export function UsageHUD({ stats }) {
    if (!stats)
        return null;
    return (_jsxs("div", { className: "no-drag flex items-center gap-3 px-3 py-1 soft-panel-subtle rounded-lg", children: [_jsxs("div", { className: "flex items-center gap-1.5", title: `Prompt: ${stats.totals.prompt_tokens.toLocaleString()}, Completion: ${stats.totals.completion_tokens.toLocaleString()}`, children: [_jsx(Cpu, { className: "size-3.5 text-accent" }), _jsx("span", { className: "text-[10px] font-mono font-bold text-text-main", children: stats.totals.total_tokens.toLocaleString() }), _jsx("span", { className: "text-[9px] text-text-dim font-bold tracking-wider", children: "TKS" })] }), _jsx("div", { className: "w-px h-3 bg-white/10" }), _jsxs("div", { className: "flex items-center gap-1.5", title: "LLM Calls", children: [_jsx(Zap, { className: "size-3.5 text-accent" }), _jsx("span", { className: "text-[10px] font-mono font-bold text-text-main", children: stats.calls }), stats.estimated_calls > 0 && (_jsx("span", { className: "text-[9px] px-0.5 rounded bg-yellow-500/20 text-yellow-400 font-bold", title: `${stats.estimated_calls} estimated calls`, children: "EST" })), _jsx("span", { className: "text-[9px] text-text-dim font-bold tracking-wider", children: "OPS" })] })] }));
}
