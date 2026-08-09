import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Brain } from 'lucide-react';
export function ThinkingDisplay({ thinking, confidence, format, title = 'Thinking Trace' }) {
    const hasThinking = Boolean(thinking && thinking.trim().length > 0);
    const confidencePct = typeof confidence === 'number' && !Number.isNaN(confidence)
        ? `${Math.round(confidence * 100)}%`
        : 'n/a';
    const formatLabel = format ? format.toUpperCase() : 'UNKNOWN';
    return (_jsxs("div", { className: "rounded-lg border border-white/10 bg-black/20 p-3", children: [_jsxs("div", { className: "flex items-center justify-between mb-2", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-semibold text-text-main", children: [_jsx(Brain, { className: "size-4 text-amber-300" }), _jsx("span", { children: title })] }), _jsxs("div", { className: "text-[10px] text-text-dim uppercase tracking-wide", children: [confidencePct, " \u2022 ", formatLabel] })] }), hasThinking ? (_jsx("pre", { className: "text-[11px] text-text-main whitespace-pre-wrap font-mono bg-black/40 rounded p-2 border border-white/5 max-h-40 overflow-auto", children: thinking })) : (_jsx("div", { className: "text-[11px] text-text-dim italic", children: "No thinking trace detected." }))] }));
}
