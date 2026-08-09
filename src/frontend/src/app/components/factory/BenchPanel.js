import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/**
 * BenchPanel — internal test-mode panel for Factory Bench batch progress.
 *
 * Bench is not a production workspace surface. Callers must guard this
 * component behind the internal bench/test-mode flag before rendering it.
 * Driven by `useFactoryBench`; no polling, all events arrive over the unified
 * Nats-JetStream/WebSocket runtime transport.
 */
import { useMemo } from 'react';
import { Activity, CheckCircle2, CircleDashed, CircleSlash, Clock, Loader2, RefreshCw, ShieldCheck, XCircle, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { StatusBadge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import { useFactoryBench, } from '@/hooks/useFactoryBench';
const STATUS_LABELS = {
    running: { label: '运行中', color: 'info' },
    completed: { label: '已完成', color: 'success' },
    failed: { label: '失败', color: 'error' },
};
function formatTime(iso) {
    if (!iso)
        return '—';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime()))
        return iso;
    return date.toLocaleTimeString();
}
function statusColor(status) {
    if (!status)
        return 'info';
    return STATUS_LABELS[status]?.color ?? 'info';
}
function statusLabel(status) {
    if (!status)
        return '未知';
    return STATUS_LABELS[status]?.label ?? status;
}
function eventTone(event) {
    if (event.ok === false)
        return 'error';
    if (event.type.endsWith('.completed') || event.ok === true)
        return 'success';
    if (event.type.endsWith('.started'))
        return 'info';
    if (event.type.endsWith('.failed'))
        return 'error';
    return 'event';
}
function summarizeSession(session) {
    const total = session.total || session.project_ids?.length || 0;
    const completed = session.completed || 0;
    const failed = session.failed || 0;
    return `${completed}/${total} 已完成${failed > 0 ? ` · ${failed} 失败` : ''}`;
}
function controlPlaneColor(projection) {
    if (!projection?.available)
        return 'info';
    return projection.ok ? 'success' : 'error';
}
function controlPlaneLabel(projection) {
    if (!projection)
        return '账本待装载';
    if (!projection.available)
        return projection.status === 'pending' ? '账本待生成' : '账本缺失';
    return projection.ok ? '账本一致' : '账本异常';
}
function summarizeControlPlane(projection) {
    if (!projection)
        return 'run_ledger_projection 未装载';
    if (!projection.available)
        return projection.detail || 'factory_audits.json 尚不可用';
    return `${projection.projected}/${projection.total} 投影 · ${projection.failed} 异常`;
}
export function BenchPanel({ enabled = false, globalObserver = false, bench, ...props }) {
    if (!enabled) {
        return null;
    }
    if (bench) {
        return _jsx(BenchPanelView, { ...props, bench: bench });
    }
    if (!globalObserver) {
        return null;
    }
    return _jsx(BenchPanelSubscribed, { ...props });
}
function BenchPanelSubscribed({ className, onWorkspaceChange }) {
    const bench = useFactoryBench({ autoSelect: 'newest', onWorkspaceChange });
    return _jsx(BenchPanelView, { className: className, bench: bench });
}
function BenchPanelView({ className, bench, }) {
    const { sessions, currentSession, events, isStreaming, isLoading, error, refresh, select } = bench;
    const progress = useMemo(() => {
        const total = currentSession?.total || currentSession?.project_ids?.length || 0;
        const done = (currentSession?.completed || 0) + (currentSession?.failed || 0);
        return total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    }, [currentSession]);
    return (_jsxs("section", { className: cn('flex h-full flex-col gap-3 rounded-md border border-slate-800 bg-slate-950/40 p-3 text-xs', className), "data-testid": "bench-panel", children: [_jsxs("header", { className: "flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex items-center gap-2 text-slate-200", children: [_jsx(Activity, { className: "h-4 w-4 text-emerald-400" }), _jsx("span", { className: "font-medium", children: "Factory Bench\uFF08L1-L8 \u6279\u6B21\uFF09" }), isStreaming ? (_jsxs("span", { className: "inline-flex items-center gap-1 text-emerald-300", children: [_jsx(Loader2, { className: "h-3 w-3 animate-spin" }), " \u5B9E\u65F6"] })) : (_jsxs("span", { className: "inline-flex items-center gap-1 text-slate-500", children: [_jsx(CircleDashed, { className: "h-3 w-3" }), " \u5DF2\u6682\u505C"] }))] }), _jsxs(Button, { size: "sm", variant: "ghost", onClick: () => void refresh(), disabled: isLoading, className: "h-7 px-2 text-slate-300", children: [_jsx(RefreshCw, { className: cn('h-3 w-3', isLoading && 'animate-spin') }), _jsx("span", { className: "ml-1", children: "\u5237\u65B0" })] })] }), error ? (_jsx("div", { className: "rounded border border-amber-700/50 bg-amber-900/20 p-2 text-amber-200", children: error })) : null, _jsxs("div", { className: "grid grid-cols-1 gap-3 lg:grid-cols-[220px_1fr]", children: [_jsx("aside", { className: "flex max-h-72 flex-col gap-1 overflow-y-auto rounded border border-slate-800 bg-slate-900/40 p-2", children: sessions.length === 0 ? (_jsx("div", { className: "px-2 py-3 text-slate-500", children: "\u6682\u65E0 bench session" })) : (sessions.map((session) => (_jsxs("button", { type: "button", onClick: () => void select(session.session_id), className: cn('flex flex-col items-start gap-1 rounded px-2 py-1.5 text-left text-slate-300 hover:bg-slate-800/70', currentSession?.session_id === session.session_id && 'bg-slate-800/80'), children: [_jsxs("div", { className: "flex w-full items-center justify-between gap-2", children: [_jsx("span", { className: "truncate font-mono text-[11px] text-slate-200", children: session.session_id }), _jsx(StatusBadge, { color: statusColor(session.status), variant: "soft", children: statusLabel(session.status) })] }), _jsx("div", { className: "text-[11px] text-slate-400", children: summarizeSession(session) }), _jsxs("div", { className: "flex items-center gap-1 text-[10px] text-slate-500", children: [_jsx(ShieldCheck, { className: "h-3 w-3 text-cyan-300" }), _jsx("span", { className: "truncate", children: summarizeControlPlane(session.control_plane_projection) })] }), _jsxs("div", { className: "flex w-full items-center justify-between text-[10px] text-slate-500", children: [_jsx("span", { children: formatTime(session.updated_at) }), _jsx("span", { className: "truncate", children: session.work_dir })] })] }, session.session_id)))) }), _jsx("div", { className: "flex min-h-72 flex-col gap-2 rounded border border-slate-800 bg-slate-900/40 p-3", children: !currentSession ? (_jsx("div", { className: "flex flex-1 items-center justify-center text-slate-500", children: "\u9009\u62E9\u5DE6\u4FA7 session \u67E5\u770B\u5B9E\u65F6\u4E8B\u4EF6" })) : (_jsxs(_Fragment, { children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2 text-slate-200", children: [_jsx("span", { className: "font-mono text-[11px] text-slate-300", children: currentSession.session_id }), _jsx(StatusBadge, { color: statusColor(currentSession.status), variant: "soft", children: statusLabel(currentSession.status) }), _jsx("span", { className: "text-slate-400", children: summarizeSession(currentSession) })] }), _jsxs("div", { className: "text-[11px] text-slate-500", title: `创建 ${formatTime(currentSession.created_at)} · 更新 ${formatTime(currentSession.updated_at)}${currentSession.completed_at ? ` · 完成 ${formatTime(currentSession.completed_at)}` : ''}`, children: ["\u66F4\u65B0 ", formatTime(currentSession.updated_at)] }), _jsx("div", { className: "h-1.5 w-full overflow-hidden rounded bg-slate-800", children: _jsx("div", { className: "h-full bg-emerald-500 transition-all", style: { width: `${progress}%` }, "data-testid": "bench-progress", "data-progress": progress }) }), _jsxs("div", { className: "flex flex-wrap items-center gap-2 rounded border border-cyan-500/20 bg-cyan-950/20 px-2 py-1.5 text-[11px] text-slate-300", "data-testid": "bench-control-plane-projection", title: currentSession.control_plane_projection?.detail, children: [_jsx(ShieldCheck, { className: "h-3.5 w-3.5 text-cyan-300" }), _jsx(StatusBadge, { color: controlPlaneColor(currentSession.control_plane_projection), variant: "soft", children: controlPlaneLabel(currentSession.control_plane_projection) }), _jsx("span", { className: "font-mono text-cyan-100", children: summarizeControlPlane(currentSession.control_plane_projection) }), _jsxs("span", { className: "text-slate-500", children: ["source=", currentSession.control_plane_projection?.source || 'run_ledger_projection'] })] }), _jsx("div", { className: "mt-1 flex flex-1 flex-col gap-1 overflow-y-auto rounded border border-slate-800 bg-slate-950/60 p-2 font-mono text-[11px] leading-5", children: events.length === 0 ? (_jsx("div", { className: "text-slate-500", children: "\u6682\u65E0\u4E8B\u4EF6" })) : (events.slice().reverse().map((event, idx) => (_jsx(BenchEventLine, { event: event }, `${event.ts ?? 't'}-${idx}`)))) })] })) })] })] }));
}
function BenchEventLine({ event }) {
    const tone = eventTone(event);
    const Icon = tone === 'error'
        ? XCircle
        : tone === 'success'
            ? CheckCircle2
            : tone === 'info'
                ? Clock
                : CircleSlash;
    const color = tone === 'error'
        ? 'text-rose-300'
        : tone === 'success'
            ? 'text-emerald-300'
            : tone === 'info'
                ? 'text-sky-300'
                : 'text-slate-300';
    const projectId = typeof event.meta?.['project_id'] === 'string' ? event.meta['project_id'] : null;
    return (_jsxs("div", { className: "flex items-start gap-2", "data-event-type": event.type, "data-event-tone": tone, children: [_jsx(Icon, { className: cn('mt-0.5 h-3 w-3 shrink-0', color) }), _jsx("span", { className: "text-slate-500", children: formatTime(event.ts) }), _jsx("span", { className: cn('shrink-0', color), children: event.type }), projectId ? _jsxs("span", { className: "text-slate-400", children: ["[", projectId, "]"] }) : null, event.summary ? _jsx("span", { className: "truncate text-slate-300", children: event.summary }) : null] }));
}
