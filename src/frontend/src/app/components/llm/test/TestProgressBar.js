import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Loader2 } from 'lucide-react';
export function TestProgressBar({ progress, running }) {
    const safe = Math.max(0, Math.min(progress, 100));
    return (_jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "flex items-center justify-between text-[10px] text-text-dim", children: [_jsx("span", { children: "\u8FDB\u5EA6" }), _jsxs("div", { className: "flex items-center gap-1", children: [running ? _jsx(Loader2, { className: "size-3 animate-spin" }) : null, _jsxs("span", { children: [safe, "%"] })] })] }), _jsx("div", { className: "soft-inset h-2 overflow-hidden rounded-full", children: _jsx("div", { className: "soft-progress h-full transition-all duration-300", style: { width: `${safe}%` } }) })] }));
}
