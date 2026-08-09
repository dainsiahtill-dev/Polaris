import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
import { X, Loader2, RotateCw, Clipboard } from 'lucide-react';
import { TestLogViewer } from './TestLogViewer';
import { TestProgressBar } from './TestProgressBar';
import { TestResultDisplay } from './TestResultDisplay';
const STATUS_LABELS = {
    idle: '等待中',
    running: '测试中',
    success: '通过',
    failed: '失败',
    cancelled: '已取消'
};
const STATUS_BADGES = {
    idle: 'bg-accent/10 text-text-muted border-border',
    running: 'bg-accent/15 text-accent-text border-accent/35',
    success: 'bg-status-success/15 text-status-success border-status-success/35',
    failed: 'bg-status-error/15 text-status-error border-status-error/35',
    cancelled: 'bg-status-warning/15 text-status-warning border-status-warning/35'
};
export function TestProgressModal({ open, state, steps, onClose, onCancel, onRetry, onCopyReport }) {
    if (!open)
        return null;
    const targetName = state.target?.providerName || 'LLM Provider';
    const modelName = state.target?.model ? ` • ${state.target.model}` : '';
    const running = state.status === 'running';
    return (_jsx("div", { className: "fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[70] p-4", children: _jsxs("div", { className: "soft-panel w-full max-w-3xl max-h-[90vh] flex flex-col rounded-lg", children: [_jsxs("div", { className: "flex items-center justify-between p-4 border-b border-border", children: [_jsxs("div", { children: [_jsxs("div", { className: "text-sm font-semibold text-text-main", children: ["\u6D4B\u8BD5\u8FDB\u5EA6 - ", targetName, modelName] }), _jsx("div", { className: "text-[10px] text-text-dim mt-1", children: state.currentStep || '准备中' })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: `text-[10px] uppercase tracking-wider px-2 py-1 rounded border ${STATUS_BADGES[state.status]}`, children: STATUS_LABELS[state.status] }), _jsx("button", { type: "button", onClick: onClose, className: "p-1.5 rounded border border-border hover:border-accent/40 transition-colors", disabled: running, children: running ? _jsx(Loader2, { className: "size-3 animate-spin" }) : _jsx(X, { className: "size-3" }) })] })] }), _jsxs("div", { className: "p-4 space-y-4 overflow-auto", children: [_jsx(TestProgressBar, { progress: state.progress, running: running }), steps && steps.length > 0 ? (_jsx("div", { className: "grid grid-cols-5 gap-2 text-[9px] text-text-dim", children: steps.map((step) => (_jsx("div", { className: `px-2 py-1 rounded border text-center ${state.currentStep === step.label
                                    ? 'border-accent/50 text-accent-text bg-accent/10'
                                    : 'border-border'}`, children: step.label }, step.key))) })) : null, state.error ? (_jsx("div", { className: "text-xs text-status-error bg-status-error/10 border border-status-error/30 rounded p-2", children: state.error })) : null, _jsx(TestLogViewer, { logs: state.logs }), state.result ? _jsx(TestResultDisplay, { result: state.result }) : null] }), _jsxs("div", { className: "flex items-center justify-between p-4 border-t border-border bg-[rgba(3,8,17,0.74)]", children: [_jsxs("div", { className: "text-[10px] text-text-dim", children: [state.startedAt ? `开始时间 ${new Date(state.startedAt).toLocaleTimeString()}` : '', state.finishedAt ? ` · 完成时间 ${new Date(state.finishedAt).toLocaleTimeString()}` : ''] }), _jsxs("div", { className: "flex items-center gap-2", children: [state.result?.report && onCopyReport ? (_jsxs("button", { type: "button", onClick: onCopyReport, className: "px-3 py-1.5 text-[10px] border border-border rounded hover:border-accent/40 flex items-center gap-1", children: [_jsx(Clipboard, { className: "size-3" }), "\u590D\u5236\u62A5\u544A"] })) : null, state.status === 'failed' && onRetry ? (_jsxs("button", { type: "button", onClick: onRetry, className: "px-3 py-1.5 text-[10px] border border-border rounded hover:border-status-success/40 flex items-center gap-1", children: [_jsx(RotateCw, { className: "size-3" }), "\u91CD\u8BD5"] })) : null, running ? (_jsx("button", { type: "button", onClick: onCancel, className: "px-3 py-1.5 text-[10px] border border-status-error/30 text-status-error rounded hover:border-status-error/60", children: "\u53D6\u6D88\u6D4B\u8BD5" })) : (_jsx("button", { type: "button", onClick: onClose, className: "px-3 py-1.5 text-[10px] border border-border rounded hover:border-accent/40", children: "\u5173\u95ED" }))] })] })] }) }));
}
