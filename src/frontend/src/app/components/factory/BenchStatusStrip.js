import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * BenchStatusStrip — compact internal-test bench status indicator.
 *
 * Bench is not a formal production workspace surface. Callers must guard this
 * component behind the internal bench/test-mode flag before rendering it. The
 * strip auto-hides when no bench session is active.
 *
 * Drives off the same `useFactoryBench` hook as the Factory page's
 * BenchPanel, so the same Nats-JetStream WebSocket stream powers every
 * internal test surface.
 */
import { useMemo } from 'react';
import { Activity, CheckCircle2, CircleDashed, Hammer, Loader2, ShieldCheck, XCircle } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { useFactoryBench } from '@/hooks/useFactoryBench';
const STATUS_COLOR = {
    running: 'text-sky-300',
    completed: 'text-emerald-300',
    failed: 'text-rose-300',
    cancelled: 'text-amber-300',
};
function statusLabel(status) {
    if (!status)
        return '空闲';
    if (status === 'running')
        return '运行中';
    if (status === 'completed')
        return '已完成';
    if (status === 'failed')
        return '失败';
    if (status === 'cancelled')
        return '已取消';
    return status;
}
function progressPct(session) {
    if (!session)
        return 0;
    const total = session.total || session.project_ids?.length || 0;
    const done = (session.completed || 0) + (session.failed || 0);
    return total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
}
function summarize(s) {
    const total = s.total || s.project_ids?.length || 0;
    return `${s.completed || 0}/${total} 通过${s.failed ? ` · ${s.failed} 失败` : ''}`;
}
function summarizeControlPlane(projection) {
    if (!projection)
        return '账本未装载';
    if (!projection.available)
        return projection.status === 'pending' ? '账本待生成' : '账本缺失';
    return `${projection.projected}/${projection.total} 账本投影${projection.failed ? ` · ${projection.failed} 异常` : ''}`;
}
function lastBenchEvent(events) {
    for (let i = events.length - 1; i >= 0; i -= 1) {
        const event = events[i];
        if (event && event.type)
            return event;
    }
    return null;
}
export function BenchStatusStrip({ bench, enabled = false, globalObserver = false, ...props }) {
    if (!enabled) {
        return null;
    }
    if (bench) {
        return _jsx(BenchStatusStripView, { ...props, bench: bench });
    }
    if (!globalObserver) {
        return null;
    }
    return _jsx(BenchStatusStripSubscribed, { ...props });
}
function BenchStatusStripSubscribed(props) {
    const bench = useFactoryBench({ autoSelect: 'newest' });
    return _jsx(BenchStatusStripView, { ...props, bench: bench });
}
function BenchStatusStripView({ className, websocketLive, websocketReconnecting = false, websocketAttemptCount = 0, bench, }) {
    const { sessions, currentSession, events, isStreaming } = bench;
    const active = useMemo(() => (sessions.find((session) => session.session_id === currentSession?.session_id)
        || currentSession
        || sessions[0]), [sessions, currentSession]);
    if (!active) {
        return null;
    }
    const progress = progressPct(active);
    const last = lastBenchEvent(events);
    const color = STATUS_COLOR[active.status] || 'text-slate-300';
    const StatusIcon = active.status === 'completed'
        ? CheckCircle2
        : active.status === 'failed'
            ? XCircle
            : active.status === 'running'
                ? Loader2
                : CircleDashed;
    const projectId = last && typeof last.meta?.['project_id'] === 'string' ? last.meta['project_id'] : null;
    const lastLabel = last
        ? `${last.type}${projectId ? ` · ${projectId}` : ''}${last.summary ? ` · ${last.summary}` : ''}`
        : '等待事件…';
    const showWebsocketState = typeof websocketLive === 'boolean';
    const websocketLabel = websocketReconnecting
        ? `WS RECONNECTING${websocketAttemptCount > 0 ? ` #${websocketAttemptCount}` : ''}`
        : websocketLive
            ? 'WS LIVE'
            : 'WS OFFLINE';
    const websocketClass = websocketReconnecting
        ? 'border-amber-400/30 bg-amber-400/10 text-amber-200'
        : websocketLive
            ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200'
            : 'border-rose-400/30 bg-rose-400/10 text-rose-200';
    return (_jsxs("div", { className: cn('soft-panel-subtle flex h-8 shrink-0 items-center gap-3 border-b border-white/10 px-4 text-[11px]', className), "data-testid": "bench-status-strip", "data-bench-session": active.session_id, "data-bench-status": active.status, children: [_jsxs("div", { className: "flex items-center gap-1.5 text-slate-200", children: [_jsx(Hammer, { className: "h-3.5 w-3.5 text-emerald-300" }), _jsx("span", { className: "font-medium", children: "Factory Bench" }), _jsx("span", { className: "text-slate-500", children: "\u00B7" }), _jsx(StatusIcon, { className: cn('h-3 w-3', color, active.status === 'running' && isStreaming && 'animate-spin') }), _jsx("span", { className: color, children: statusLabel(active.status) })] }), _jsxs("div", { className: "flex min-w-0 flex-1 items-center gap-2", children: [_jsx("div", { className: "h-1.5 w-32 shrink-0 overflow-hidden rounded bg-slate-800", children: _jsx("div", { className: "h-full bg-emerald-500 transition-all", style: { width: `${progress}%` }, "data-testid": "bench-strip-progress", "data-progress": progress }) }), _jsx("span", { className: "font-mono text-[10px] text-slate-400", children: summarize(active) })] }), _jsxs("div", { className: "flex min-w-0 items-center gap-1.5 text-slate-400", "data-testid": "bench-strip-last-event", title: lastLabel, children: [_jsx(Activity, { className: "h-3 w-3 shrink-0 text-slate-500" }), _jsx("span", { className: "truncate font-mono text-[10px]", children: lastLabel })] }), _jsx("span", { className: "shrink-0 font-mono text-[10px] text-slate-600", children: active.session_id }), _jsxs("span", { className: cn('flex shrink-0 items-center gap-1 rounded border px-2 py-0.5 font-mono text-[10px]', active.control_plane_projection?.ok
                    ? 'border-cyan-400/30 bg-cyan-400/10 text-cyan-100'
                    : 'border-amber-400/30 bg-amber-400/10 text-amber-100'), "data-testid": "bench-strip-control-plane", title: active.control_plane_projection?.detail || 'run_ledger_projection', children: [_jsx(ShieldCheck, { className: "h-3 w-3" }), summarizeControlPlane(active.control_plane_projection)] }), showWebsocketState ? (_jsx("span", { className: cn('shrink-0 rounded border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide', websocketClass), "data-testid": "bench-strip-ws-status", "data-ws-live": websocketLive ? 'true' : 'false', "data-ws-reconnecting": websocketReconnecting ? 'true' : 'false', "data-ws-attempts": websocketAttemptCount, children: websocketLabel })) : null] }));
}
