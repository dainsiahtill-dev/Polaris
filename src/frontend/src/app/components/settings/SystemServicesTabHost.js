import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * System Services Tab Host
 *
 * Host component for system services configuration and status.
 * Wraps the existing SystemServicesTab component with settings-specific styling.
 */
import { lazy, Suspense } from 'react';
import { Loader2, Terminal } from 'lucide-react';
// Lazy load the SystemServicesTab
const SystemServicesTab = lazy(() => import('@/app/components/SystemServicesTab').then((module) => ({ default: module.SystemServicesTab })));
/**
 * System Services Tab Host Component
 *
 * Provides a settings-compatible wrapper around the SystemServicesTab component.
 */
export function SystemServicesTabHost({ className }) {
    return (_jsxs("div", { className: `space-y-6 pb-20 ${className || ''}`, children: [_jsxs("div", { children: [_jsxs("h2", { className: "text-xl font-bold text-slate-100 flex items-center gap-2", children: [_jsx(Terminal, { className: "w-6 h-6 text-slate-400" }), "\u7CFB\u7EDF\u670D\u52A1"] }), _jsx("p", { className: "text-sm text-slate-400 mt-1", children: "\u67E5\u770B\u548C\u7BA1\u7406\u540E\u7AEF\u670D\u52A1\u72B6\u6001\u3001MCP \u670D\u52A1\u3001\u4EE3\u7801\u641C\u7D22\u7B49\u7CFB\u7EDF\u7EC4\u4EF6" })] }), _jsx(Suspense, { fallback: _jsx("div", { className: "flex items-center justify-center py-12", children: _jsxs("div", { className: "flex items-center gap-2 text-text-muted", children: [_jsx(Loader2, { className: "size-4 animate-spin" }), _jsx("span", { className: "text-sm", children: "\u6B63\u5728\u8F7D\u5165\u7CFB\u7EDF\u670D\u52A1..." })] }) }), children: _jsx(SystemServicesTab, {}) })] }));
}
