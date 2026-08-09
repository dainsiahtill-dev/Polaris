import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ChevronLeft, Gauge, Loader2, RefreshCw, RadioTower, Server, TimerReset, Wifi, WifiOff, } from 'lucide-react';
import { apiFetchFresh } from '@/api';
import { StatusBadge } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
function asRecord(value) {
    return value && typeof value === 'object' && !Array.isArray(value)
        ? value
        : {};
}
function asSection(value) {
    return asRecord(value);
}
function pickSection(payload, keys) {
    const record = asRecord(payload);
    for (const key of keys) {
        const section = asSection(record[key]);
        if (Object.keys(section).length > 0) {
            return section;
        }
    }
    return {};
}
function stringValue(value) {
    if (value === null || value === undefined)
        return '';
    if (typeof value === 'string')
        return value.trim();
    if (typeof value === 'number' && Number.isFinite(value))
        return String(value);
    if (typeof value === 'boolean')
        return value ? 'true' : 'false';
    return '';
}
function numberValue(value) {
    if (typeof value === 'number' && Number.isFinite(value))
        return value;
    if (typeof value === 'string') {
        const parsed = Number(value.trim());
        return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
}
function boolValue(value) {
    if (typeof value === 'boolean')
        return value;
    const token = stringValue(value).toLowerCase();
    if (['true', '1', 'yes', 'ready', 'ok', 'healthy', 'connected', 'running'].includes(token))
        return true;
    if (['false', '0', 'no', 'failed', 'error', 'disconnected', 'stopped'].includes(token))
        return false;
    return null;
}
function firstDefined(...values) {
    return values.find((value) => value !== null && value !== undefined && value !== '');
}
function statusFromSection(section) {
    return (stringValue(section.status) ||
        stringValue(section.state) ||
        stringValue(section.phase) ||
        (boolValue(section.ok) === true ? 'ok' : '') ||
        (boolValue(section.connected) === true ? 'connected' : '') ||
        'unknown');
}
function toneFromStatus(status, section) {
    const ok = boolValue(section?.ok);
    if (ok === true)
        return 'success';
    if (ok === false)
        return 'error';
    const token = status.toLowerCase();
    if (/(ok|ready|healthy|connected|running|open|normal|pass)/.test(token))
        return 'success';
    if (/(reconnect|retry|degraded|limited|throttle|warning|pending)/.test(token))
        return 'warning';
    if (/(fail|error|blocked|offline|closed|disconnect|unhealthy)/.test(token))
        return 'error';
    return 'default';
}
function badgeColor(tone) {
    if (tone === 'success')
        return 'success';
    if (tone === 'warning')
        return 'warning';
    if (tone === 'error')
        return 'error';
    if (tone === 'info')
        return 'info';
    return 'default';
}
function formatTime(value) {
    const raw = stringValue(value);
    if (!raw)
        return '未记录';
    const epoch = Date.parse(raw);
    if (!Number.isFinite(epoch))
        return raw;
    return new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        month: '2-digit',
        day: '2-digit',
    }).format(new Date(epoch));
}
function workspaceLabel(workspace) {
    const normalized = String(workspace || '').replace(/\\/g, '/').trim();
    if (!normalized)
        return '未选择 workspace';
    return normalized.split('/').filter(Boolean).pop() || normalized;
}
function formatBool(value, trueLabel = '是', falseLabel = '否') {
    const parsed = boolValue(value);
    if (parsed === true)
        return trueLabel;
    if (parsed === false)
        return falseLabel;
    return '未知';
}
function formatNumber(value) {
    const parsed = numberValue(value);
    return parsed === null ? '未知' : String(parsed);
}
function normalizeEvents(value) {
    if (Array.isArray(value)) {
        return value
            .filter((item) => Boolean(item && typeof item === 'object'))
            .slice(-5);
    }
    const record = asRecord(value);
    if (!Object.keys(record).length)
        return [];
    return Object.entries(record)
        .map(([key, raw]) => {
        const eventRecord = asRecord(raw);
        if (Object.keys(eventRecord).length > 0) {
            return {
                state: key,
                status: stringValue(eventRecord.status || eventRecord.state),
                message: stringValue(eventRecord.message || eventRecord.detail),
                timestamp: stringValue(eventRecord.timestamp || eventRecord.updated_at),
            };
        }
        return { state: key, message: stringValue(raw) };
    })
        .filter((event) => stringValue(event.state || event.message || event.status))
        .slice(-5);
}
function eventsFor(section) {
    return normalizeEvents(section.lifecycle).concat(normalizeEvents(section.events)).slice(-5);
}
function rateLimitRows(section) {
    const details = asRecord(section.details);
    const store = asRecord(details.store);
    const buckets = section.buckets;
    const bucketCount = Array.isArray(buckets)
        ? buckets.length
        : Object.keys(asRecord(buckets)).length;
    return [
        ['rps', formatNumber(firstDefined(section.requests_per_second, details.requests_per_second))],
        ['burst', formatNumber(firstDefined(section.limit, details.burst_size))],
        ['blocked', formatNumber(firstDefined(section.blocked_count, store.blocked_count))],
        ['violations', formatNumber(firstDefined(section.total_violations, store.total_violations))],
        ['remaining', formatNumber(section.remaining)],
        ['retry_after', stringValue(section.retry_after_ms) ? `${formatNumber(section.retry_after_ms)} ms` : `${formatNumber(section.retry_after_sec)} s`],
        ['buckets', bucketCount > 0 ? String(bucketCount) : formatNumber(store.entry_count)],
    ];
}
function issueText(issue) {
    return stringValue(issue.message || issue.detail || issue.status || issue.state) || '未提供详情';
}
function buildCards(payload, connectionState) {
    const nats = pickSection(payload, ['nats', 'nats_lifecycle']);
    const websocket = pickSection(payload, ['websocket', 'web_socket', 'runtime_v2']);
    const rateLimit = pickSection(payload, ['rate_limit', 'rate_limits']);
    const natsStatus = statusFromSection(nats);
    const natsDetails = asRecord(nats.details);
    const natsClient = asRecord(natsDetails.client);
    const managedServer = asRecord(natsDetails.managed_server);
    const websocketDetails = asRecord(websocket.details);
    const wsStatus = connectionState.live
        ? 'live'
        : connectionState.reconnecting
            ? 'reconnecting'
            : statusFromSection(websocket);
    const rateStatus = statusFromSection(rateLimit);
    return [
        {
            id: 'nats',
            title: 'NATS lifecycle',
            subtitle: 'runtime.v2 message bus',
            statusLabel: natsStatus.toUpperCase(),
            tone: toneFromStatus(natsStatus, nats),
            rows: [
                ['enabled', formatBool(firstDefined(nats.enabled, natsDetails.enabled))],
                ['required', formatBool(firstDefined(nats.required, natsDetails.required))],
                ['connected', formatBool(firstDefined(nats.connected, natsClient.is_connected, managedServer.tcp_reachable), '在线', '离线')],
                ['managed', formatBool(managedServer.managed)],
                ['process', stringValue(managedServer.process_pid) || '未托管'],
                ['last_error', stringValue(firstDefined(nats.last_error, nats.error, asRecord(natsClient.last_connect_failure).message)) || '无'],
            ],
            events: eventsFor(nats),
        },
        {
            id: 'websocket',
            title: 'WebSocket reconnect',
            subtitle: '复用当前 runtime WS',
            statusLabel: wsStatus.toUpperCase(),
            tone: connectionState.live ? 'success' : connectionState.reconnecting ? 'warning' : toneFromStatus(wsStatus, websocket),
            rows: [
                ['live', connectionState.live ? '在线' : '离线'],
                ['reconnecting', connectionState.reconnecting ? '是' : '否'],
                ['attempts', String(connectionState.attemptCount)],
                ['backend_attempts', formatNumber(firstDefined(websocket.attempt_count, websocket.reconnect_attempts))],
                ['active', formatNumber(websocketDetails.active_connections)],
                ['total', formatNumber(websocketDetails.total_connections)],
            ],
            events: eventsFor(websocket),
        },
        {
            id: 'rate-limit',
            title: 'Rate limit',
            subtitle: 'HTTP policy and LLM throttling',
            statusLabel: rateStatus.toUpperCase(),
            tone: toneFromStatus(rateStatus, rateLimit),
            rows: rateLimitRows(rateLimit),
            events: eventsFor(rateLimit),
        },
    ];
}
function DiagnosticCard({ card }) {
    return (_jsxs("section", { "data-testid": `runtime-diagnostics-card-${card.id}`, className: "min-h-[220px] rounded-lg border border-white/10 bg-white/[0.035] p-4", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { children: [_jsx("h2", { className: "text-sm font-semibold text-slate-100", children: card.title }), _jsx("p", { className: "mt-1 text-[11px] uppercase tracking-wider text-slate-500", children: card.subtitle })] }), _jsx(StatusBadge, { color: badgeColor(card.tone), variant: "dot", pulse: card.tone === 'warning', children: _jsx("span", { className: "font-mono text-[10px]", children: card.statusLabel }) })] }), _jsx("dl", { className: "mt-4 grid grid-cols-2 gap-2", children: card.rows.map(([label, value]) => (_jsxs("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-2", children: [_jsx("dt", { className: "font-mono text-[10px] uppercase text-slate-500", children: label }), _jsx("dd", { className: "mt-1 truncate text-xs text-slate-200", title: value, children: value })] }, label))) }), _jsxs("div", { className: "mt-4 border-t border-white/10 pt-3", children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-[11px] font-medium text-slate-400", children: [_jsx(TimerReset, { className: "h-3.5 w-3.5" }), "\u6700\u8FD1\u751F\u547D\u5468\u671F"] }), _jsx("div", { className: "space-y-1.5", children: card.events.length === 0 ? (_jsx("div", { className: "rounded-md border border-dashed border-white/10 px-2 py-2 text-[11px] text-slate-500", children: "\u6682\u65E0\u4E8B\u4EF6" })) : (card.events.map((event, index) => (_jsxs("div", { className: "rounded-md bg-black/20 px-2 py-1.5", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate font-mono text-[10px] uppercase text-slate-300", children: stringValue(event.state || event.status || event.phase) || 'event' }), _jsx("span", { className: "shrink-0 text-[10px] text-slate-500", children: formatTime(event.timestamp) })] }), issueText(event) !== '未提供详情' ? (_jsx("div", { className: "mt-0.5 truncate text-[11px] text-slate-400", title: issueText(event), children: issueText(event) })) : null] }, `${card.id}-${index}`)))) })] })] }));
}
export function RuntimeDiagnosticsWorkspace({ workspace, connectionState, onBackToMain, }) {
    const [payload, setPayload] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const refreshDiagnostics = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const response = await apiFetchFresh('/v2/runtime/diagnostics');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const nextPayload = await response.json();
            setPayload(nextPayload && typeof nextPayload === 'object' ? nextPayload : null);
        }
        catch (err) {
            setError(err instanceof Error ? err.message : '运行诊断读取失败');
        }
        finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => {
        void refreshDiagnostics();
    }, [refreshDiagnostics]);
    const cards = useMemo(() => buildCards(payload, connectionState), [payload, connectionState]);
    const issues = Array.isArray(payload?.issues) ? payload.issues : [];
    const generatedAt = payload?.generated_at || payload?.timestamp || null;
    const workspaceDisplay = workspaceLabel(workspace);
    return (_jsxs("div", { "data-testid": "runtime-diagnostics-workspace", className: "flex h-full flex-col overflow-hidden bg-slate-950 text-slate-100", children: [_jsxs("header", { className: "flex h-14 items-center justify-between border-b border-emerald-500/20 bg-slate-950/90 px-4", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-4", children: [_jsxs(Button, { variant: "ghost", size: "sm", onClick: onBackToMain, "data-testid": "runtime-diagnostics-back", className: "text-slate-400 hover:bg-white/5 hover:text-slate-100", children: [_jsx(ChevronLeft, { className: "mr-1 h-4 w-4" }), "\u8FD4\u56DE"] }), _jsxs("div", { className: "flex min-w-0 items-center gap-3", children: [_jsx("div", { className: "flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/[0.15] text-emerald-200 ring-1 ring-emerald-400/30", children: _jsx(Gauge, { className: "h-4 w-4" }) }), _jsxs("div", { className: "min-w-0", children: [_jsx("h1", { className: "text-sm font-semibold text-emerald-100", children: "\u8FD0\u884C\u8BCA\u65AD" }), _jsx("p", { "data-testid": "runtime-diagnostics-workspace-label", className: "truncate text-[10px] uppercase tracking-wider text-emerald-400/70", title: workspace, children: workspaceDisplay })] })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(StatusBadge, { color: connectionState.live ? 'success' : connectionState.reconnecting ? 'warning' : 'error', variant: "dot", pulse: connectionState.reconnecting, children: _jsx("span", { className: "font-mono text-[10px]", children: connectionState.live ? 'WS LIVE' : connectionState.reconnecting ? 'WS RECONNECT' : 'WS OFFLINE' }) }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => void refreshDiagnostics(), disabled: loading, "data-testid": "runtime-diagnostics-refresh", className: "border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/10", children: [loading ? _jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }) : _jsx(RefreshCw, { className: "h-3.5 w-3.5" }), "\u5237\u65B0"] })] })] }), _jsxs("main", { className: "min-h-0 flex-1 overflow-auto p-4", children: [_jsxs("div", { className: "mb-4 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_280px]", children: [_jsx("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] px-4 py-3", children: _jsxs("div", { className: "grid grid-cols-2 gap-3 md:grid-cols-4", children: [_jsxs("div", { className: "flex items-center gap-2", children: [connectionState.live ? _jsx(Wifi, { className: "h-4 w-4 text-emerald-300" }) : _jsx(WifiOff, { className: "h-4 w-4 text-amber-300" }), _jsxs("div", { children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "current ws" }), _jsx("div", { className: "text-xs font-semibold text-slate-200", children: connectionState.live ? 'connected' : 'disconnected' })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(RadioTower, { className: cn('h-4 w-4', connectionState.reconnecting ? 'text-amber-300' : 'text-slate-500') }), _jsxs("div", { children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "reconnect" }), _jsx("div", { className: "text-xs font-semibold text-slate-200", children: connectionState.reconnecting ? 'active' : 'idle' })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Server, { className: "h-4 w-4 text-cyan-300" }), _jsxs("div", { children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "attempts" }), _jsx("div", { className: "text-xs font-semibold text-slate-200", children: connectionState.attemptCount })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(TimerReset, { className: "h-4 w-4 text-slate-400" }), _jsxs("div", { children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "snapshot" }), _jsx("div", { className: "text-xs font-semibold text-slate-200", children: formatTime(generatedAt) })] })] })] }) }), _jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] px-4 py-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-[11px] font-medium text-slate-400", children: [_jsx(AlertTriangle, { className: "h-3.5 w-3.5" }), "\u8BCA\u65AD\u95EE\u9898"] }), _jsx("div", { className: "mt-2 text-sm font-semibold text-slate-100", children: issues.length }), _jsx("div", { className: "mt-1 truncate text-[11px] text-slate-500", children: error || (issues[0] ? issueText(issues[0]) : '暂无后端上报问题') })] })] }), error ? (_jsx("div", { "data-testid": "runtime-diagnostics-error", className: "mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200", children: error })) : null, _jsx("div", { className: "grid grid-cols-1 gap-4 xl:grid-cols-3", children: cards.map((card) => (_jsx(DiagnosticCard, { card: card }, card.id))) })] })] }));
}
