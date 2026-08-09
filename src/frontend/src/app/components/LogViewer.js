import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { RefreshCw, Activity, Trash2, Search, Filter, Clock, ArrowDown } from 'lucide-react';
import { Virtuoso } from 'react-virtuoso';
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch } from '@/api';
import { useConnectionState, useMessageHandler, useTransportActions } from '@/runtime/transport';
import { LlmEventCard } from '@/app/components/logs/LlmEventCard';
import { parseLlmEventLine, parseLlmEventLines } from '@/app/components/logs/LlmEventTypes';
import { PolarisTerminalRenderer } from '@/app/components/PolarisTerminalRenderer';
import { LogExporter } from '@/app/components/logs/LogExporter';
import { parseLogLines } from '@/app/utils/exportUtils';
import { toast } from 'sonner';
import { devLogger } from '@/app/utils/devLogger';
export const DEFAULT_LOG_SOURCES = [
    { id: 'pm-subprocess', label: 'PM 案牍', path: 'runtime/logs/pm.process.log', channel: 'process', llmChannel: 'llm' },
    { id: 'pm-report', label: 'PM 禀报', path: 'runtime/results/pm.report.md', channel: '', llmChannel: '' },
    { id: 'pm-log', label: 'PM 纪要（jsonl）', path: 'runtime/events/pm.events.jsonl', channel: '', llmChannel: '' },
    { id: 'director', label: 'Director 子进程', path: 'runtime/logs/director.process.log', channel: 'process', llmChannel: 'llm' },
    { id: 'planner', label: '谋划稿', path: 'runtime/results/planner.output.md', channel: '', llmChannel: '' },
    { id: 'ollama', label: 'Ollama', path: 'runtime/results/director_llm.output.md', channel: 'llm', llmChannel: '' },
    { id: 'qa', label: '审校', path: 'runtime/results/qa.review.md', channel: '', llmChannel: '' },
    { id: 'runlog', label: '运行纪要', path: 'runtime/logs/director.runlog.md', channel: 'process', llmChannel: '' },
];
const CLEAR_SCOPE_BY_SOURCE_ID = {
    'pm-subprocess': 'pm',
    director: 'director',
};
export const LogViewer = memo(function LogViewer({ sourceId, runId, className }) {
    const source = useMemo(() => {
        const base = DEFAULT_LOG_SOURCES.find(s => s.id === sourceId) || DEFAULT_LOG_SOURCES[0];
        if (!runId)
            return base;
        return {
            ...base,
            path: `runtime/runs/${runId}/${base.path.split('/').pop()}`,
        };
    }, [sourceId, runId]);
    const hasLlmChannel = !!source.llmChannel;
    const allowSmart = hasLlmChannel || sourceId === 'runlog';
    const allowJson = sourceId === 'pm-log';
    const allowRaw = sourceId !== 'pm-log';
    const [rawLines, setRawLines] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [live, setLive] = useState(false);
    const [viewMode, setViewMode] = useState('smart');
    const [query, setQuery] = useState('');
    const [logLevelFilter, setLogLevelFilter] = useState('all');
    const [showTimestamp, setShowTimestamp] = useState(true);
    const [autoScroll, setAutoScroll] = useState(true);
    const { connected: transportConnected } = useConnectionState();
    const { subscribeChannels } = useTransportActions();
    const { registerMessageHandler } = useMessageHandler();
    const [subscriptionEpoch, setSubscriptionEpoch] = useState(0);
    const [isClearing, setIsClearing] = useState(false);
    const [llmEvents, setLlmEvents] = useState([]);
    const seenIds = useRef(new Set());
    const clearScope = CLEAR_SCOPE_BY_SOURCE_ID[sourceId];
    useEffect(() => {
        if (allowSmart)
            setViewMode('smart');
        else if (allowJson)
            setViewMode('json');
        else
            setViewMode('raw');
    }, [sourceId]);
    const refresh = async () => {
        setError(null);
        if (!transportConnected) {
            setLive(false);
            setError('实时通道未连接');
            return;
        }
        setLoading(false);
        setLive(true);
        setSubscriptionEpoch((value) => value + 1);
    };
    useEffect(() => {
        setRawLines([]);
        setLlmEvents([]);
        seenIds.current.clear();
        setError(null);
    }, [source.channel, source.llmChannel, sourceId]);
    const clearLogs = async () => {
        if (!clearScope || isClearing)
            return;
        setIsClearing(true);
        setError(null);
        try {
            const res = await apiFetch('/v2/runtime/clear', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scope: clearScope }),
            });
            if (!res.ok) {
                let detail = '清空日志失败';
                try {
                    const payload = (await res.json());
                    if (payload.detail)
                        detail = payload.detail;
                }
                catch {
                    // ignore parse errors
                }
                throw new Error(detail);
            }
            setRawLines([]);
            setLlmEvents([]);
            seenIds.current.clear();
            setSubscriptionEpoch((value) => value + 1);
            toast.success('日志已清空');
        }
        catch (err) {
            const message = err instanceof Error ? err.message : '清空日志失败';
            setError(message);
            toast.error(message);
        }
        finally {
            setIsClearing(false);
        }
    };
    useEffect(() => {
        const channels = Array.from(new Set([source.channel, source.llmChannel].filter(Boolean)));
        if (!transportConnected) {
            setLive(false);
            return;
        }
        if (channels.length === 0) {
            setLive(false);
            return;
        }
        setLive(true);
        setLoading(false);
        const unsubscribe = subscribeChannels(channels.map((channel) => ({ channel, tailLines: 400 })));
        const unregister = registerMessageHandler((raw) => {
            // The runtime transport unwraps the runtime.v2 envelope, so we
            // receive the inner ``event`` object directly: {channel, kind,
            // cursor, ts, payload, ...}. Legacy v1 fields (``type`` /
            // ``line`` / ``lines``) are not produced by the runtime.v2
            // pipeline and are ignored — the bench audit (Playwright) shows
            // every event from the v2 backend already carries ``kind`` and
            // ``payload``.
            if (!raw || typeof raw !== 'object')
                return;
            const ev = raw;
            const ch = String(ev.channel || '').trim();
            const kind = String(ev.kind || ev.type || '').trim().toLowerCase();
            const payload = (ev.payload && typeof ev.payload === 'object'
                ? ev.payload
                : null);
            const line = typeof ev.line === 'string' ? ev.line : null;
            const text = line
                || (payload && typeof payload.line === 'string' ? payload.line : '')
                || (payload ? JSON.stringify(payload) : '');
            if (source.channel && ch === source.channel) {
                if (kind === 'snapshot' && payload && Array.isArray(payload.lines)) {
                    setRawLines(payload.lines);
                }
                else if ((kind === 'line'
                    || kind === 'process_stream'
                    || kind === 'dialogue_event'
                    || kind === 'process_line')
                    && text) {
                    setRawLines((prev) => [...prev, text].slice(-1000));
                }
            }
            if (ch === source.llmChannel) {
                if (kind === 'snapshot' && payload && Array.isArray(payload.lines)) {
                    const parsed = parseLlmEventLines(payload.lines);
                    const ids = new Set();
                    for (const ev2 of parsed)
                        ids.add(ev2.event_id);
                    seenIds.current = ids;
                    setLlmEvents(parsed);
                }
                else if ((kind === 'line' || kind === 'llm_stream') && text) {
                    const ev2 = parseLlmEventLine(text);
                    if (ev2 && !seenIds.current.has(ev2.event_id)) {
                        seenIds.current.add(ev2.event_id);
                        setLlmEvents((prev) => [...prev, ev2].slice(-500));
                    }
                }
            }
        });
        return () => {
            try {
                unsubscribe();
            }
            catch { /* noop */ }
            try {
                unregister();
            }
            catch { /* noop */ }
        };
    }, [
        source.channel,
        source.llmChannel,
        transportConnected,
        subscribeChannels,
        registerMessageHandler,
        subscriptionEpoch,
    ]);
    const filteredLlmEvents = useMemo(() => {
        if (!query.trim())
            return llmEvents;
        const q = query.toLowerCase();
        return llmEvents.filter(ev => JSON.stringify(ev).toLowerCase().includes(q));
    }, [llmEvents, query]);
    // Filter raw lines by log level
    const filteredRawLines = useMemo(() => {
        if (logLevelFilter === 'all')
            return rawLines;
        const levelKeywords = {
            error: ['error', 'err', 'fatal', 'critical'],
            warn: ['warn', 'warning'],
            info: ['info', 'information'],
            debug: ['debug', 'trace', 'verbose'],
        };
        const keywords = levelKeywords[logLevelFilter] || [];
        return rawLines.filter(line => {
            const lower = line.toLowerCase();
            return keywords.some(kw => lower.includes(kw));
        });
    }, [rawLines, logLevelFilter]);
    // Filter raw lines by query and log level
    const displayLines = useMemo(() => {
        if (!query.trim())
            return filteredRawLines;
        const q = query.toLowerCase();
        return filteredRawLines.filter(line => line.toLowerCase().includes(q));
    }, [filteredRawLines, query]);
    // Convert raw lines to LogEntry format for export
    const exportableLogs = useMemo(() => {
        return parseLogLines(displayLines);
    }, [displayLines]);
    return (_jsxs("div", { className: `soft-panel-subtle flex flex-col h-full ${className}`, children: [_jsxs("div", { className: "soft-panel-subtle flex flex-wrap items-center justify-between gap-2 p-2 border-b border-white/10", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2 overflow-hidden", children: [_jsxs("div", { className: "soft-inset flex shrink-0 items-center gap-1 rounded p-0.5", children: [allowRaw && (_jsx("button", { onClick: () => setViewMode('raw'), className: `h-6 whitespace-nowrap rounded px-2 text-[10px] transition-colors ${viewMode === 'raw' ? 'soft-raised text-accent' : 'text-text-dim hover:text-text-main'}`, children: "\u539F\u59CB" })), allowSmart && (_jsx("button", { onClick: () => setViewMode('smart'), className: `h-6 whitespace-nowrap rounded px-2 text-[10px] transition-colors ${viewMode === 'smart' ? 'soft-raised text-accent' : 'text-text-dim hover:text-text-main'}`, children: "\u667A\u6790" }))] }), viewMode === 'smart' && hasLlmChannel && (_jsxs("span", { className: "text-[10px] text-text-dim", children: [llmEvents.length, " events"] }))] }), _jsxs("div", { className: "flex min-w-0 shrink-0 items-center justify-end gap-1.5", children: [!allowSmart && allowRaw && (_jsxs("div", { className: "flex items-center gap-1", children: [_jsx(Filter, { className: "size-3 text-text-dim" }), _jsxs("select", { value: logLevelFilter, onChange: e => setLogLevelFilter(e.target.value), className: "soft-inset rounded px-1.5 py-0.5 text-[10px] text-text-main focus:outline-none focus:border-accent/30", children: [_jsx("option", { value: "all", children: "\u5168\u90E8" }), _jsx("option", { value: "error", children: "Error" }), _jsx("option", { value: "warn", children: "Warn" }), _jsx("option", { value: "info", children: "Info" }), _jsx("option", { value: "debug", children: "Debug" })] })] })), allowRaw && rawLines.length > 0 && (_jsx("button", { onClick: () => setShowTimestamp(!showTimestamp), title: showTimestamp ? '隐藏时间戳' : '显示时间戳', className: `p-1 rounded transition-colors ${showTimestamp ? 'soft-raised text-accent' : 'text-text-dim hover:text-text-main'}`, children: _jsx(Clock, { className: "size-3" }) })), rawLines.length > 0 && (_jsx("button", { onClick: () => setAutoScroll(!autoScroll), title: autoScroll ? '自动滚动: 开' : '自动滚动: 关', className: `p-1 rounded transition-colors ${autoScroll ? 'soft-raised text-accent' : 'text-text-dim hover:text-text-main'}`, children: _jsx(ArrowDown, { className: "size-3" }) })), _jsx(LogExporter, { logs: exportableLogs, filename: `polaris-${sourceId}-logs`, onExportSuccess: () => toast.success('日志导出成功'), onExportError: (_, err) => toast.error(err.message) }), clearScope && (_jsxs("button", { onClick: () => {
                                    clearLogs().catch((err) => {
                                        devLogger.error('[LogViewer] Clear logs failed:', err);
                                    });
                                }, disabled: isClearing, title: isClearing ? '清空中...' : '清空当前日志', "aria-label": isClearing ? '清空中' : '清空当前日志', className: "soft-chip flex h-7 w-7 shrink-0 items-center justify-center rounded text-text-muted transition-colors hover:text-accent disabled:cursor-not-allowed disabled:opacity-50", children: [_jsx(Trash2, { className: "size-3" }), _jsx("span", { className: "sr-only", children: isClearing ? '清空中' : '清空日志' })] })), _jsxs("span", { className: `flex h-7 shrink-0 items-center gap-1 whitespace-nowrap text-[10px] ${live ? 'text-emerald-300' : 'text-text-dim'}`, children: [_jsx(Activity, { className: "size-3" }), live ? '在线' : '离线'] }), _jsx("button", { onClick: refresh, title: "\u5237\u65B0", className: "flex h-7 w-7 shrink-0 items-center justify-center text-text-dim hover:text-text-main", children: _jsx(RefreshCw, { className: "size-3" }) })] })] }), viewMode === 'smart' && hasLlmChannel && (_jsx("div", { className: "p-2 border-b border-white/10 bg-transparent", children: _jsxs("div", { className: "relative", children: [_jsx(Search, { className: "absolute left-2 top-1/2 -translate-y-1/2 size-3 text-text-dim" }), _jsx("input", { className: "soft-inset w-full rounded pl-7 pr-2 py-1 text-[10px] text-text-main placeholder:text-text-dim/70 focus:outline-none focus:border-accent/30", placeholder: "\u641C\u7D22\u4E8B\u4EF6...", value: query, onChange: e => setQuery(e.target.value) })] }) })), viewMode === 'raw' && allowRaw && (_jsx("div", { className: "p-2 border-b border-white/10 bg-transparent", children: _jsxs("div", { className: "relative", children: [_jsx(Search, { className: "absolute left-2 top-1/2 -translate-y-1/2 size-3 text-text-dim" }), _jsx("input", { className: "soft-inset w-full rounded pl-7 pr-2 py-1 text-[10px] text-text-main placeholder:text-text-dim/70 focus:outline-none focus:border-accent/30", placeholder: "\u641C\u7D22\u65E5\u5FD7...", value: query, onChange: e => setQuery(e.target.value) })] }) })), _jsxs("div", { className: "flex-1 min-h-0", children: [(logLevelFilter !== 'all' || query.trim()) && viewMode === 'raw' && (_jsxs("div", { className: "px-2 py-1 bg-amber-500/10 border-b border-amber-400/20 flex items-center justify-between text-[10px]", children: [_jsx("span", { className: "text-amber-200/60", children: query.trim() || logLevelFilter !== 'all'
                                    ? `显示 ${displayLines.length} / ${rawLines.length} 行`
                                    : `${rawLines.length} 行` }), _jsx("button", { onClick: () => { setQuery(''); setLogLevelFilter('all'); }, className: "text-cyan-400 hover:text-cyan-300", children: "\u6E05\u9664\u8FC7\u6EE4" })] })), error ? (_jsx("div", { className: "text-red-400 p-2", children: error })) : viewMode === 'smart' && hasLlmChannel ? (_jsx(Virtuoso, { className: "h-full", data: filteredLlmEvents, followOutput: "auto", itemContent: (_, event) => (_jsx("div", { className: "mx-2 my-1", children: _jsx(LlmEventCard, { event: event }) })) })) : viewMode === 'smart' && sourceId === 'runlog' ? (_jsx(PolarisTerminalRenderer, { text: rawLines.join('\n'), className: "text-slate-100 p-2" })) : (_jsx(Virtuoso, { className: "h-full", data: displayLines, followOutput: autoScroll ? "auto" : false, itemContent: (_, line) => (_jsx("div", { className: "px-2 font-mono text-xs text-gray-400 whitespace-pre-wrap break-all leading-tight", children: line })) }))] })] }));
});
