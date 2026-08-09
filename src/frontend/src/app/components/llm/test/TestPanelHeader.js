import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { X, Loader2, ChevronDown, ChevronUp, Copy, Download } from 'lucide-react';
const STATUS_TEXT = {
    idle: '准备就绪',
    running: '测试中',
    success: '成功',
    failed: '失败'
};
const STATUS_BADGES = {
    idle: 'bg-accent/10 text-text-muted border-border',
    running: 'bg-accent/15 text-accent-text border-accent/35',
    success: 'bg-status-success/15 text-status-success border-status-success/35',
    failed: 'bg-status-error/15 text-status-error border-status-error/35'
};
export function TestPanelHeader({ provider, status, onClose, running, collapsed, onToggleCollapse, onCopyLogs, onExportLogs, title, subtitle, statusText, extraActions }) {
    const resolvedTitle = title || `Testing: ${provider.name}`;
    const resolvedSubtitle = subtitle || `Provider: ${provider.name} · Model: ${provider.modelId || 'default'}`;
    const resolvedStatusText = statusText?.[status] || STATUS_TEXT[status];
    return (_jsxs("div", { className: "flex items-start justify-between gap-3 border-b border-border bg-[rgba(6,15,28,0.88)] p-3 shadow-[inset_0_1px_0_rgba(0,216,255,0.16)]", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2 text-sm font-semibold text-text-main", children: [_jsx("span", { className: "truncate", children: resolvedTitle }), _jsx("span", { "data-testid": "llm-test-panel-status", className: `text-[9px] uppercase tracking-wider px-2 py-0.5 rounded border ${STATUS_BADGES[status]}`, children: resolvedStatusText })] }), _jsx("div", { className: "mt-1 truncate text-[10px] text-text-dim", children: resolvedSubtitle })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-2", children: [extraActions, onCopyLogs ? (_jsx("button", { type: "button", onClick: onCopyLogs, className: "rounded border border-border p-1.5 text-text-dim hover:border-accent/40 hover:text-text-main", title: "\u590D\u5236\u65E5\u5FD7", children: _jsx(Copy, { className: "size-3" }) })) : null, onExportLogs ? (_jsx("button", { type: "button", onClick: onExportLogs, className: "rounded border border-border p-1.5 text-text-dim hover:border-accent/40 hover:text-text-main", title: "\u5BFC\u51FA\u4F1A\u8BDD", children: _jsx(Download, { className: "size-3" }) })) : null, onToggleCollapse ? (_jsx("button", { type: "button", onClick: onToggleCollapse, className: "rounded border border-border p-1.5 text-text-dim hover:border-accent/40 hover:text-text-main", title: collapsed ? '展开' : '折叠', children: collapsed ? _jsx(ChevronDown, { className: "size-3" }) : _jsx(ChevronUp, { className: "size-3" }) })) : null, _jsx("button", { type: "button", "data-testid": "llm-test-panel-close", onClick: onClose, disabled: running, className: "rounded border border-border p-1.5 text-text-dim hover:border-accent/40 hover:text-text-main disabled:opacity-50", children: running ? _jsx(Loader2, { className: "size-3 animate-spin" }) : _jsx(X, { className: "size-3" }) })] })] }));
}
