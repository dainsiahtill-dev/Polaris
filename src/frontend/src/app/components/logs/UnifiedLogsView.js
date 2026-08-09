import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/**
 * UnifiedLogsView - High-signal Timeline View for Log Events
 *
 * Default view that shows a filtered, high-signal timeline of log events
 * with support for channel switching, filtering, and noise folding.
 */
import { Brain, ChevronDown, ChevronRight, Cpu, Filter, RefreshCw, Terminal, X, } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useConnectionState, useMessageHandler, useTransportActions } from '@/runtime/transport';
import { CHANNEL_METADATA, KIND_STYLES, SEVERITY_STYLES, } from './types';
// Filter chip component
function FilterChip({ label, active, onClick, color = 'blue', }) {
    const colorClasses = {
        blue: active ? 'bg-blue-500/30 border-blue-400' : 'bg-blue-500/10 border-blue-500/30 hover:bg-blue-500/20',
        green: active ? 'bg-green-500/30 border-green-400' : 'bg-green-500/10 border-green-500/30 hover:bg-green-500/20',
        purple: active ? 'bg-purple-500/30 border-purple-400' : 'bg-purple-500/10 border-purple-500/30 hover:bg-purple-500/20',
        yellow: active ? 'bg-yellow-500/30 border-yellow-400' : 'bg-yellow-500/10 border-yellow-500/30 hover:bg-yellow-500/20',
        red: active ? 'bg-red-500/30 border-red-400' : 'bg-red-500/10 border-red-500/30 hover:bg-red-500/20',
    };
    return (_jsx("button", { onClick: onClick, className: `px-2 py-1 rounded-full text-xs font-medium border transition-colors ${colorClasses[color]} ${active ? 'text-white' : 'text-gray-300'}`, children: label }));
}
// Log event card component
function LogEventCard({ event, expanded, onToggle, }) {
    const severityStyle = SEVERITY_STYLES[event.severity] || SEVERITY_STYLES.info;
    const kindStyle = KIND_STYLES[event.kind] || KIND_STYLES.observation;
    // Signal score from enrichment
    const signalScore = event.enrichment?.signal_score ?? (event.severity === 'error' ? 0.8 : 0.5);
    const isNoise = event.enrichment?.noise ?? false;
    // Format timestamp
    const timeStr = useMemo(() => {
        try {
            const date = new Date(event.ts);
            return date.toLocaleTimeString('zh-CN', { hour12: false });
        }
        catch {
            return event.ts;
        }
    }, [event.ts]);
    return (_jsxs("div", { className: `border rounded-lg mb-2 overflow-hidden ${isNoise ? 'border-gray-700/50 opacity-60' : 'border-gray-600'}`, children: [_jsxs("div", { className: "flex items-start gap-2 p-3 cursor-pointer hover:bg-gray-800/50", onClick: onToggle, children: [_jsx("div", { className: "mt-0.5", children: expanded ? (_jsx(ChevronDown, { className: "w-4 h-4 text-gray-400" })) : (_jsx(ChevronRight, { className: "w-4 h-4 text-gray-400" })) }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-gray-400 mb-1", children: [_jsx("span", { children: timeStr }), _jsxs("span", { className: "text-gray-600", children: ["#", event.seq] }), event.run_id && _jsxs("span", { className: "text-gray-500", children: ["\u2022 ", event.run_id.slice(0, 8)] })] }), _jsx("div", { className: "text-sm text-gray-200 truncate mb-2", children: event.message || '(无消息)' }), _jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("span", { className: `px-1.5 py-0.5 rounded text-[10px] font-medium ${event.channel === 'system'
                                            ? 'bg-blue-500/20 text-blue-300'
                                            : event.channel === 'process'
                                                ? 'bg-emerald-500/20 text-emerald-300'
                                                : 'bg-slate-500/20 text-slate-300'}`, children: event.channel }), _jsx("span", { className: `px-1.5 py-0.5 rounded text-[10px] font-medium ${severityStyle.bg} ${severityStyle.text}`, children: severityStyle.label }), _jsx("span", { className: `px-1.5 py-0.5 rounded text-[10px] font-medium ${kindStyle.bg} ${kindStyle.text}`, children: kindStyle.label }), event.actor && (_jsx("span", { className: "px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-500/20 text-gray-300", children: event.actor })), !isNoise && signalScore > 0 && (_jsxs("div", { className: "flex items-center gap-1 ml-auto", children: [_jsx("div", { className: "w-12 h-1.5 bg-gray-700 rounded-full overflow-hidden", children: _jsx("div", { className: "h-full bg-emerald-500/60", style: { width: `${signalScore * 100}%` } }) }), _jsxs("span", { className: "text-[10px] text-gray-500", children: [Math.round(signalScore * 100), "%"] })] })), isNoise && (_jsx("span", { className: "ml-auto text-[10px] text-gray-500 italic", children: "\u5DF2\u6298\u53E0" }))] })] })] }), expanded && (_jsxs("div", { className: "border-t border-gray-700 p-3 bg-gray-900/50", children: [event.enrichment?.summary && (_jsxs("div", { className: "mb-3", children: [_jsx("div", { className: "text-xs text-gray-500 mb-1", children: "\u6458\u8981" }), _jsx("div", { className: "text-sm text-gray-300", children: event.enrichment.summary })] })), event.raw && (_jsxs("div", { className: "mb-3", children: [_jsx("div", { className: "text-xs text-gray-500 mb-1", children: "\u539F\u59CB\u6570\u636E" }), _jsx("pre", { className: "text-xs text-gray-400 bg-gray-800 p-2 rounded overflow-x-auto max-h-40", children: JSON.stringify(event.raw, null, 2) })] })), event.refs && Object.keys(event.refs).length > 0 && (_jsxs("div", { children: [_jsx("div", { className: "text-xs text-gray-500 mb-1", children: "\u5F15\u7528" }), _jsx("div", { className: "flex flex-wrap gap-1", children: Object.entries(event.refs).map(([key, value]) => (_jsxs("span", { className: "px-2 py-0.5 bg-gray-700 rounded text-xs text-gray-300", children: [key, ": ", String(value).slice(0, 30)] }, key))) })] }))] }))] }));
}
// Channel tab component
function ChannelTab({ channel, active, count, onClick, }) {
    const meta = CHANNEL_METADATA[channel];
    const IconComponent = channel === 'system' ? Cpu : channel === 'process' ? Terminal : Brain;
    return (_jsxs("button", { onClick: onClick, className: `flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${active
            ? 'bg-blue-500/20 border border-blue-500/40 text-blue-300'
            : 'bg-gray-800/50 border border-gray-700 text-gray-400 hover:bg-gray-800 hover:text-gray-300'}`, children: [_jsx(IconComponent, { className: "w-4 h-4" }), _jsx("span", { className: "font-medium", children: meta.label }), count !== undefined && count > 0 && (_jsx("span", { className: "ml-1 px-1.5 py-0.5 bg-gray-700 rounded text-xs", children: count }))] }));
}
export function UnifiedLogsView({ workspace, runId, isOpen, onClose, }) {
    // State
    const [activeChannel, setActiveChannel] = useState('system');
    const [events, setEvents] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [cursor, setCursor] = useState(null);
    const [hasMore, setHasMore] = useState(false);
    const [highSignalOnly, setHighSignalOnly] = useState(true);
    const [expandedEvents, setExpandedEvents] = useState(new Set());
    const [severityFilter, setSeverityFilter] = useState(null);
    // Unify onto the runtime transport: the legacy path opened its own
    // WebSocket and sent ``type: 'event'`` without ``protocol: 'runtime.v2'``;
    // the runtime.v2 backend rejects those with RUNTIME_V2_REQUIRED, so we
    // now route queries through ``sendCommand`` and include the v2 protocol
    // marker explicitly.
    const { connected: transportConnected } = useConnectionState();
    const { sendCommand } = useTransportActions();
    const { registerMessageHandler } = useMessageHandler();
    const queryEvents = useCallback((params) => {
        const message = {
            type: 'event',
            action: 'query',
            protocol: 'runtime.v2',
            ...params,
        };
        sendCommand(message);
    }, [sendCommand]);
    useEffect(() => {
        if (!isOpen)
            return;
        if (!transportConnected)
            return;
        const unregister = registerMessageHandler((raw) => {
            if (!raw || typeof raw !== 'object')
                return;
            const msg = raw;
            if (msg.action === 'query_result') {
                const response = msg;
                setEvents(response.events);
                setCursor(response.next_cursor);
                setHasMore(response.has_more);
                setLoading(false);
            }
        });
        return () => {
            try {
                unregister();
            }
            catch { /* noop */ }
        };
    }, [isOpen, transportConnected, registerMessageHandler]);
    // Re-query when filters change
    useEffect(() => {
        if (!transportConnected)
            return;
        setLoading(true);
        queryEvents({
            channel: activeChannel,
            run_id: runId,
            limit: 50,
            high_signal_only: highSignalOnly,
            severity: severityFilter || undefined,
        });
    }, [activeChannel, highSignalOnly, severityFilter, runId, transportConnected, queryEvents]);
    // Toggle event expansion
    const toggleEventExpanded = useCallback((eventId) => {
        setExpandedEvents((prev) => {
            const next = new Set(prev);
            if (next.has(eventId)) {
                next.delete(eventId);
            }
            else {
                next.add(eventId);
            }
            return next;
        });
    }, []);
    // Load more events
    const loadMore = useCallback(() => {
        if (!cursor)
            return;
        setLoading(true);
        queryEvents({
            channel: activeChannel,
            run_id: runId,
            limit: 50,
            cursor,
            high_signal_only: highSignalOnly,
            severity: severityFilter || undefined,
        });
    }, [cursor, activeChannel, runId, highSignalOnly, severityFilter, queryEvents]);
    // Filtered events (for high signal mode)
    const displayEvents = useMemo(() => {
        if (!highSignalOnly)
            return events;
        return events.filter((e) => {
            if (e.enrichment?.noise)
                return false;
            if (e.severity === 'debug')
                return false;
            return true;
        });
    }, [events, highSignalOnly]);
    // Group events by foldable noise
    const groupedEvents = useMemo(() => {
        const noiseGroups = [];
        const normalEvents = [];
        for (const event of displayEvents) {
            if (event.enrichment?.noise && event.dedupe_count > 1) {
                noiseGroups.push(event);
            }
            else {
                normalEvents.push(event);
            }
        }
        return { noiseGroups, normalEvents };
    }, [displayEvents]);
    if (!isOpen)
        return null;
    return (_jsx("div", { className: "fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4", children: _jsxs("div", { className: "bg-gray-900 border border-gray-700 rounded-lg w-full max-w-4xl h-[80vh] flex flex-col", children: [_jsxs("div", { className: "flex items-center justify-between p-4 border-b border-gray-700", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsx("h2", { className: "text-lg font-semibold text-white", children: "\u7EDF\u4E00\u65E5\u5FD7" }), _jsx("span", { className: "text-sm text-gray-400", children: runId ? `Run: ${runId.slice(0, 8)}` : '最新运行' })] }), _jsx("button", { onClick: onClose, className: "p-2 hover:bg-gray-800 rounded-lg transition-colors", children: _jsx(X, { className: "w-5 h-5 text-gray-400" }) })] }), _jsxs("div", { className: "flex items-center gap-4 p-4 border-b border-gray-700 bg-gray-800/50", children: [_jsx("div", { className: "flex items-center gap-2", children: ['system', 'process', 'llm'].map((channel) => (_jsx(ChannelTab, { channel: channel, active: activeChannel === channel, count: events.filter((e) => e.channel === channel).length, onClick: () => setActiveChannel(channel) }, channel))) }), _jsx("div", { className: "flex-1" }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("button", { onClick: () => setHighSignalOnly(!highSignalOnly), className: `flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors ${highSignalOnly
                                        ? 'bg-green-500/20 text-green-300 border border-green-500/40'
                                        : 'bg-gray-700 text-gray-400 border border-gray-600'}`, children: [_jsx(Filter, { className: "w-4 h-4" }), "\u9AD8\u4FE1\u53F7"] }), _jsx("button", { onClick: () => {
                                        setLoading(true);
                                        queryEvents({
                                            channel: activeChannel,
                                            run_id: runId,
                                            limit: 50,
                                            high_signal_only: highSignalOnly,
                                        });
                                    }, className: "p-2 hover:bg-gray-700 rounded-lg transition-colors", disabled: loading, children: _jsx(RefreshCw, { className: `w-4 h-4 text-gray-400 ${loading ? 'animate-spin' : ''}` }) })] })] }), _jsxs("div", { className: "flex items-center gap-2 px-4 py-2 border-b border-gray-700/50", children: [_jsx("span", { className: "text-xs text-gray-500", children: "\u7B5B\u9009:" }), ['debug', 'info', 'warn', 'error', 'critical'].map((sev) => (_jsx(FilterChip, { label: SEVERITY_STYLES[sev].label, active: severityFilter === sev, onClick: () => setSeverityFilter(severityFilter === sev ? null : sev), color: sev === 'error' ? 'red' : sev === 'warn' ? 'yellow' : 'blue' }, sev)))] }), _jsx("div", { className: "flex-1 overflow-y-auto p-4", children: loading && events.length === 0 ? (_jsx("div", { className: "flex items-center justify-center h-full", children: _jsxs("div", { className: "flex items-center gap-2 text-gray-400", children: [_jsx(RefreshCw, { className: "w-5 h-5 animate-spin" }), _jsx("span", { children: "\u52A0\u8F7D\u4E2D..." })] }) })) : error ? (_jsx("div", { className: "flex items-center justify-center h-full", children: _jsx("div", { className: "text-red-400", children: error }) })) : displayEvents.length === 0 ? (_jsx("div", { className: "flex items-center justify-center h-full", children: _jsx("div", { className: "text-gray-500", children: "\u6682\u65E0\u65E5\u5FD7\u4E8B\u4EF6" }) })) : (_jsxs(_Fragment, { children: [groupedEvents.noiseGroups.length > 0 && highSignalOnly && (_jsx("div", { className: "mb-4 p-3 bg-gray-800/50 rounded-lg border border-gray-700", children: _jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("span", { className: "text-sm text-gray-400", children: ["\u5DF2\u6298\u53E0 ", groupedEvents.noiseGroups.length, " \u4E2A\u91CD\u590D/\u566A\u97F3\u4E8B\u4EF6"] }), _jsx("button", { onClick: () => setHighSignalOnly(false), className: "text-xs text-blue-400 hover:text-blue-300", children: "\u67E5\u770B\u5168\u90E8" })] }) })), groupedEvents.normalEvents.map((event) => (_jsx(LogEventCard, { event: event, expanded: expandedEvents.has(event.event_id), onToggle: () => toggleEventExpanded(event.event_id) }, event.event_id))), hasMore && (_jsx("div", { className: "flex justify-center mt-4", children: _jsx("button", { onClick: loadMore, disabled: loading, className: "px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm text-gray-300 transition-colors", children: loading ? '加载中...' : '加载更多' }) }))] })) })] }) }));
}
