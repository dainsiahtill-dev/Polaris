import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Loader2, RefreshCw, XCircle } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
const TONE_CLASSES = {
    amber: {
        border: 'border-amber-500/[0.15]',
        endpoint: 'text-amber-200/80',
        result: 'text-amber-200/80',
    },
    cyan: {
        border: 'border-cyan-500/[0.15]',
        endpoint: 'text-cyan-200/80',
        result: 'text-cyan-200/80',
    },
    emerald: {
        border: 'border-emerald-500/[0.15]',
        endpoint: 'text-emerald-200/80',
        result: 'text-emerald-200/80',
    },
}, satisfies, Record;
() => ;
() => ;
 > ;
export function roleRunEvidenceEndpoint(endpoint, workspace) {
    const value = String(workspace || '').trim();
    if (!value) {
        return endpoint;
    }
    const separator = endpoint.includes('?') ? '&' : '?';
    return `${endpoint}${separator}workspace=${encodeURIComponent(value)}`;
}
export function RoleRunEvidenceStrip({ tone, testId, endpoint, workspace, loading, error, status, details = [], message, refreshTestId, refreshDisabled = false, refreshLoading = false, refreshLabel = '刷新运行快照', realtimePushActive = false, onRefresh, cancelTestId, cancelDisabled, cancelLoading, onCancel, cancelResultTestId, cancelResultEndpoint, cancelResultVisible, cancelResultLoading, cancelResultMessage, cancelResultError, }) {
    const styles = TONE_CLASSES[tone];
    const visibleEndpoint = roleRunEvidenceEndpoint(endpoint, workspace);
    const visibleCancelEndpoint = roleRunEvidenceEndpoint(cancelResultEndpoint, workspace);
    const snapshotParts = [status || 'unknown', ...details.filter(Boolean)];
    const hasSnapshot = Boolean(status) || details.filter(Boolean).length > 0;
    const statusText = loading
        ? hasSnapshot
            ? [...snapshotParts, '刷新中'].join(' · ')
            : '正在读取运行快照...'
        : error
            ? error
            : snapshotParts.join(' · ');
    return (_jsxs("div", { className: cn('flex flex-wrap items-center justify-between gap-2 border-b bg-slate-950/70 px-4 py-2 text-[11px]', styles.border), "data-testid": testId, "data-endpoint": visibleEndpoint, children: [_jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1", children: [_jsx("span", { className: cn('rounded border border-white/10 bg-slate-950/70 px-1.5 py-0.5 text-[9px] font-medium', styles.endpoint), title: visibleEndpoint, "data-testid": `${testId}-endpoint`, "data-endpoint": visibleEndpoint, children: "API" }), _jsx("span", { className: error ? 'text-rose-300' : 'text-slate-300', children: statusText }), !loading && !error && message ? (_jsx("span", { className: "max-w-[360px] truncate text-slate-500", title: message, children: message })) : null] }), _jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2", children: [realtimePushActive ? (_jsx("span", { className: "rounded border border-emerald-500/[0.15] bg-emerald-500/10 px-2 py-1 font-mono text-[10px] text-emerald-200", "data-testid": `${testId}-realtime-push`, children: "\u5B9E\u65F6\u63A8\u9001" })) : null, onRefresh ? (_jsx("button", { type: "button", onClick: onRefresh, "data-testid": refreshTestId, disabled: refreshDisabled || refreshLoading, title: refreshLabel, "aria-label": refreshLabel, className: "inline-flex h-6 w-6 cursor-pointer items-center justify-center rounded border border-white/10 bg-white/5 text-slate-300 transition-colors hover:bg-white/10 hover:text-slate-100 disabled:cursor-not-allowed disabled:border-slate-600/20 disabled:bg-slate-700/20 disabled:text-slate-500", children: refreshLoading ? (_jsx(Loader2, { className: "h-3 w-3 animate-spin" })) : (_jsx(RefreshCw, { className: "h-3 w-3" })) })) : null, _jsxs("button", { type: "button", onClick: onCancel, "data-testid": cancelTestId, disabled: cancelDisabled, className: "inline-flex h-6 cursor-pointer items-center gap-1 rounded border border-rose-500/20 bg-rose-500/10 px-2 text-[11px] text-rose-200 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:border-slate-600/20 disabled:bg-slate-700/20 disabled:text-slate-500", children: [cancelLoading ? (_jsx(Loader2, { className: "h-3 w-3 animate-spin" })) : (_jsx(XCircle, { className: "h-3 w-3" })), "\u53D6\u6D88"] }), cancelResultVisible ? (_jsx("span", { className: cancelResultError ? 'text-rose-300' : styles.result, "data-testid": cancelResultTestId, "data-endpoint": visibleCancelEndpoint, title: visibleCancelEndpoint, children: cancelResultLoading ? 'cancelling' : cancelResultError || cancelResultMessage })) : null] })] }));
}
