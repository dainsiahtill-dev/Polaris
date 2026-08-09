import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { memo } from 'react';
export const PlanPreview = memo(function PlanPreview({ planText, planUpdated }) {
    return (_jsxs("div", { className: "mt-4 soft-panel-subtle rounded-xl p-4", children: [_jsxs("div", { className: "flex items-center justify-between text-xs text-text-muted", children: [_jsx("span", { className: "font-medium uppercase tracking-wide", children: "\u6555\u4EE4\u603B\u56FE (contracts/plan.md)" }), planUpdated ? (_jsx("span", { className: "text-text-dim font-mono", children: planUpdated })) : (_jsx("span", { className: "text-text-dim", children: "-" }))] }), _jsx("div", { className: "mt-3 max-h-56 overflow-auto soft-inset rounded-xl px-3 py-2 text-xs text-text-code whitespace-pre-wrap leading-relaxed custom-scrollbar", children: planText || '暂无敕令总图' })] }));
});
