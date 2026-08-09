import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * DirectorTerminalPanel - 终端面板展示组件
 */
import { RotateCcw } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
export function DirectorTerminalPanel({ output, onClear }) {
    return (_jsxs("div", { className: "h-full flex flex-col", children: [_jsxs("div", { className: "h-12 flex items-center justify-between px-4 border-b border-white/5", children: [_jsx("h2", { className: "text-sm font-medium text-slate-200", children: "\u6267\u884C\u7EC8\u7AEF" }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: onClear, disabled: !output || !onClear, "data-testid": "director-terminal-clear", className: "text-slate-400", children: [_jsx(RotateCcw, { className: "w-4 h-4 mr-1.5" }), "\u6E05\u7A7A"] })] }), _jsx("div", { className: "flex-1 p-4", children: _jsx("div", { className: "h-full rounded-xl border border-white/10 bg-slate-950 p-4 font-mono text-xs overflow-auto", children: output ? (_jsx("pre", { className: "text-slate-300 whitespace-pre-wrap", children: output })) : (_jsx("div", { className: "text-slate-600", children: "\u7B49\u5F85\u6267\u884C..." })) }) })] }));
}
