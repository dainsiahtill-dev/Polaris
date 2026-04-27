import { RefreshCw, Activity, Trash2, Search, Filter, Clock, ArrowDown } from 'lucide-react';
import { Virtuoso } from 'react-virtuoso';
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch, connectWebSocket } from '@/api';
import { LlmEventCard } from '@/app/components/logs/LlmEventCard';
import { parseLlmEventLine, parseLlmEventLines, type LlmEvent } from '@/app/components/logs/LlmEventTypes';
import { PolarisTerminalRenderer } from '@/app/components/PolarisTerminalRenderer';
import { LogExporter } from '@/app/components/logs/LogExporter';
import { parseLogLines, type LogEntry } from '@/app/utils/exportUtils';
import { toast } from 'sonner';
import { devLogger } from '@/app/utils/devLogger';

export const DEFAULT_LOG_SOURCES = [
  { id: 'pm-subprocess', label: 'PM 案牍', path: 'runtime/logs/pm.process.log', channel: 'pm_subprocess', llmChannel: 'llm' },
  { id: 'pm-report', label: 'PM 禀报', path: 'runtime/results/pm.report.md', channel: 'pm_report', llmChannel: '' },
  { id: 'pm-log', label: 'PM 纪要（jsonl）', path: 'runtime/events/pm.events.jsonl', channel: 'pm_log', llmChannel: '' },
  { id: 'director', label: 'Director 子进程', path: 'runtime/logs/director.process.log', channel: 'director_console', llmChannel: 'llm' },
  { id: 'planner', label: '谋划稿', path: 'runtime/results/planner.output.md', channel: 'planner', llmChannel: '' },
  { id: 'ollama', label: 'Ollama', path: 'runtime/results/director_llm.output.md', channel: 'ollama', llmChannel: '' },
  { id: 'qa', label: '审校', path: 'runtime/results/qa.review.md', channel: 'qa', llmChannel: '' },
  { id: 'runlog', label: '运行纪要', path: 'runtime/logs/director.runlog.md', channel: 'runlog', llmChannel: '' },
];

interface LogViewerProps {
  sourceId: string;
  runId?: string | null;
  className?: string;
}

type RuntimeClearScope = 'pm' | 'director' | 'dialogue' | 'all';

const CLEAR_SCOPE_BY_SOURCE_ID: Record<string, RuntimeClearScope | undefined> = {
  'pm-subprocess': 'pm',
  director: 'director',
};

export const LogViewer = memo(function LogViewer({ sourceId, runId, className }: LogViewerProps) {
  const source = useMemo(() => {
    const base = DEFAULT_LOG_SOURCES.find(s => s.id === sourceId) || DEFAULT_LOG_SOURCES[0];
    if (!runId) return base;
    return {
      ...base,
      path: `runtime/runs/${runId}/${base.path.split('/').pop()}`,
    };
  }, [sourceId, runId]);

  const hasLlmChannel = !!source.llmChannel;
  const allowSmart = hasLlmChannel || sourceId === 'runlog';
  const allowJson = sourceId === 'pm-log';
  const allowRaw = sourceId !== 'pm-log';

  const [rawLines, setRawLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [viewMode, setViewMode] = useState<'raw' | 'smart' | 'json'>('smart');
  const [query, setQuery] = useState('');
  const [logLevelFilter, setLogLevelFilter] = useState<string>('all');
  const [showTimestamp, setShowTimestamp] = useState(true);
  const [autoScroll, setAutoScroll] = useState(true);
  const socketRef = useRef<WebSocket | null>(null);
  const [isClearing, setIsClearing] = useState(false);

  const [llmEvents, setLlmEvents] = useState<LlmEvent[]>([]);
  const seenIds = useRef<Set<string>>(new Set());
  const clearScope = CLEAR_SCOPE_BY_SOURCE_ID[sourceId];

  useEffect(() => {
    if (allowSmart) setViewMode('smart');
    else if (allowJson) setViewMode('json');
    else setViewMode('raw');
  }, [sourceId]);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/files/read?path=${encodeURIComponent(source.path)}&tail_lines=400`);
      if (!res.ok) throw new Error('读取案牍失败');
      const payload = (await res.json()) as { content?: string; mtime?: string };
      setRawLines(payload.content ? payload.content.split('\n') : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取案牍失败');
      setRawLines([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await apiFetch(`/files/read?path=${encodeURIComponent(source.path)}&tail_lines=400`, {
          signal: controller.signal,
        });
        if (!res.ok) throw new Error('读取案牍失败');
        const payload = (await res.json()) as { content?: string; mtime?: string };
        setRawLines(payload.content ? payload.content.split('\n') : []);
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : '读取案牍失败');
        setRawLines([]);
      } finally {
        setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [source.path]);

  const clearLogs = async () => {
    if (!clearScope || isClearing) return;
    setIsClearing(true);
    setError(null);
    try {
      const res = await apiFetch('/runtime/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: clearScope }),
      });
      if (!res.ok) {
        let detail = '清空日志失败';
        try {
          const payload = (await res.json()) as { detail?: string };
          if (payload.detail) detail = payload.detail;
        } catch {
          // ignore parse errors
        }
        throw new Error(detail);
      }
      setRawLines([]);
      setLlmEvents([]);
      seenIds.current.clear();
      await refresh();
      toast.success('日志已清空');
    } catch (err) {
      const message = err instanceof Error ? err.message : '清空日志失败';
      setError(message);
      toast.error(message);
    } finally {
      setIsClearing(false);
    }
  };

  useEffect(() => {
    let activeSocket: WebSocket | null = null;
    let alive = true;

    const channels: string[] = [source.channel];
    if (source.llmChannel) channels.push(source.llmChannel);

    const connect = async () => {
      try {
        activeSocket = await connectWebSocket();
      } catch {
        if (alive) setLive(false);
        return;
      }
      socketRef.current = activeSocket;
      if (!alive) return;

      activeSocket.onopen = () => {
        setLive(true);
        activeSocket?.send(JSON.stringify({ type: 'subscribe', channels, tail_lines: 200 }));
      };

      activeSocket.onmessage = (wsEvent) => {
        try {
          const payload = JSON.parse(wsEvent.data);
          const ch = String(payload.channel || '').trim();
          const msgType = String(payload.type || '').trim().toLowerCase();
          const eventText = payload.event && typeof payload.event === 'object' ? JSON.stringify(payload.event) : '';
          const lineText = typeof payload.line === 'string' ? payload.line : '';
          const text = eventText || lineText || (typeof payload.text === 'string' ? payload.text : '');

          if (ch === source.channel) {
            if (msgType === 'snapshot' && Array.isArray(payload.lines)) {
              setRawLines(payload.lines);
            } else if ((msgType === 'line' || msgType === 'process_stream' || msgType === 'runtime_event' || msgType === 'dialogue_event') && text) {
              setRawLines(prev => [...prev, text].slice(-1000));
            }
          }

          if (ch === source.llmChannel) {
            if (msgType === 'snapshot' && Array.isArray(payload.lines)) {
              const parsed = parseLlmEventLines(payload.lines);
              const ids = new Set<string>();
              for (const ev of parsed) ids.add(ev.event_id);
              seenIds.current = ids;
              setLlmEvents(parsed);
            } else if ((msgType === 'line' || msgType === 'llm_stream') && text) {
              const ev = parseLlmEventLine(text);
              if (ev && !seenIds.current.has(ev.event_id)) {
                seenIds.current.add(ev.event_id);
                setLlmEvents(prev => [...prev, ev].slice(-500));
              }
            }
          }
        } catch { /* ignore */ }
      };

      activeSocket.onclose = () => { if (alive) setLive(false); };
      activeSocket.onerror = () => { if (alive) setLive(false); };
    };

    connect();
    return () => {
      alive = false;
      if (socketRef.current) { socketRef.current.close(); socketRef.current = null; }
    };
  }, [source.channel, source.llmChannel]);

  const filteredLlmEvents = useMemo(() => {
    if (!query.trim()) return llmEvents;
    const q = query.toLowerCase();
    return llmEvents.filter(ev => JSON.stringify(ev).toLowerCase().includes(q));
  }, [llmEvents, query]);

  // Filter raw lines by log level
  const filteredRawLines = useMemo(() => {
    if (logLevelFilter === 'all') return rawLines;
    const levelKeywords: Record<string, string[]> = {
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
    if (!query.trim()) return filteredRawLines;
    const q = query.toLowerCase();
    return filteredRawLines.filter(line => line.toLowerCase().includes(q));
  }, [filteredRawLines, query]);

  // Convert raw lines to LogEntry format for export
  const exportableLogs = useMemo((): LogEntry[] => {
    return parseLogLines(displayLines);
  }, [displayLines]);

  return (
    <div className={`flex flex-col h-full bg-[rgba(18,14,42,0.95)] ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between p-2 border-b border-amber-400/10 bg-[linear-gradient(165deg,rgba(50,35,18,0.30),rgba(28,18,48,0.40))]">
        <div className="flex items-center gap-2 overflow-hidden">
          <div className="flex items-center gap-1 rounded bg-[rgba(18,14,42,0.40)] p-0.5">
            {allowRaw && (
              <button
                onClick={() => setViewMode('raw')}
                className={`px-2 py-0.5 text-[10px] rounded transition-colors ${viewMode === 'raw' ? 'bg-amber-500/20 text-amber-200' : 'text-gray-500 hover:text-gray-300'}`}
              >
                原始
              </button>
            )}
            {allowSmart && (
              <button
                onClick={() => setViewMode('smart')}
                className={`px-2 py-0.5 text-[10px] rounded transition-colors ${viewMode === 'smart' ? 'bg-cyan-500/20 text-cyan-200' : 'text-gray-500 hover:text-gray-300'}`}
              >
                智析
              </button>
            )}
          </div>
          {viewMode === 'smart' && hasLlmChannel && (
            <span className="text-[10px] text-gray-500">{llmEvents.length} events</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* Log Level Filter */}
          {!allowSmart && allowRaw && (
            <div className="flex items-center gap-1">
              <Filter className="size-3 text-gray-500" />
              <select
                value={logLevelFilter}
                onChange={e => setLogLevelFilter(e.target.value)}
                className="bg-[rgba(18,14,42,0.40)] border border-amber-400/10 rounded px-1.5 py-0.5 text-[10px] text-gray-300 focus:outline-none focus:border-cyan-400/30"
              >
                <option value="all">全部</option>
                <option value="error">Error</option>
                <option value="warn">Warn</option>
                <option value="info">Info</option>
                <option value="debug">Debug</option>
              </select>
            </div>
          )}

          {/* Timestamp Toggle */}
          {allowRaw && rawLines.length > 0 && (
            <button
              onClick={() => setShowTimestamp(!showTimestamp)}
              title={showTimestamp ? '隐藏时间戳' : '显示时间戳'}
              className={`p-1 rounded transition-colors ${showTimestamp ? 'text-cyan-400 bg-cyan-500/10' : 'text-gray-500 hover:text-gray-300'}`}
            >
              <Clock className="size-3" />
            </button>
          )}

          {/* Auto-scroll Toggle */}
          {rawLines.length > 0 && (
            <button
              onClick={() => setAutoScroll(!autoScroll)}
              title={autoScroll ? '自动滚动: 开' : '自动滚动: 关'}
              className={`p-1 rounded transition-colors ${autoScroll ? 'text-emerald-400 bg-emerald-500/10' : 'text-gray-500 hover:text-gray-300'}`}
            >
              <ArrowDown className="size-3" />
            </button>
          )}

          {/* Export Logs */}
          <LogExporter
            logs={exportableLogs}
            filename={`polaris-${sourceId}-logs`}
            onExportSuccess={() => toast.success('日志导出成功')}
            onExportError={(_, err) => toast.error(err.message)}
          />

          {clearScope && (
            <button
              onClick={() => {
                clearLogs().catch((err) => {
                  devLogger.error('[LogViewer] Clear logs failed:', err);
                });
              }}
              disabled={isClearing}
              title={isClearing ? '清空中...' : '清空当前日志'}
              className="text-[10px] flex items-center gap-1 px-2 py-1 rounded bg-white/5 text-gray-400 hover:text-amber-200 hover:bg-amber-500/10 border border-white/10 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <Trash2 className="size-3" />
              {isClearing ? '清空中' : '清空日志'}
            </button>
          )}
          <span className={`text-[10px] flex items-center gap-1 ${live ? 'text-emerald-400' : 'text-gray-500'}`}>
            <Activity className="size-3" />
            {live ? '在线' : '离线'}
          </span>
          <button onClick={refresh} title="刷新" className="text-gray-500 hover:text-gray-300">
            <RefreshCw className="size-3" />
          </button>
        </div>
      </div>

      {/* Search bar for smart mode */}
      {viewMode === 'smart' && hasLlmChannel && (
        <div className="p-2 border-b border-amber-400/10 bg-[rgba(28,18,48,0.30)]">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-gray-500" />
            <input
              className="w-full bg-[rgba(35,25,14,0.40)] border border-amber-400/10 rounded pl-7 pr-2 py-1 text-[10px] text-gray-300 placeholder-gray-600 focus:outline-none focus:border-cyan-400/30"
              placeholder="搜索事件..."
              value={query}
              onChange={e => setQuery(e.target.value)}
            />
          </div>
        </div>
      )}

      {/* Search bar for raw mode */}
      {viewMode === 'raw' && allowRaw && (
        <div className="p-2 border-b border-amber-400/10 bg-[rgba(28,18,48,0.30)]">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-gray-500" />
            <input
              className="w-full bg-[rgba(35,25,14,0.40)] border border-amber-400/10 rounded pl-7 pr-2 py-1 text-[10px] text-gray-300 placeholder-gray-600 focus:outline-none focus:border-cyan-400/30"
              placeholder="搜索日志..."
              value={query}
              onChange={e => setQuery(e.target.value)}
            />
          </div>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 min-h-0">
        {/* Filter status bar */}
        {(logLevelFilter !== 'all' || query.trim()) && viewMode === 'raw' && (
          <div className="px-2 py-1 bg-amber-500/10 border-b border-amber-400/20 flex items-center justify-between text-[10px]">
            <span className="text-amber-200/60">
              {query.trim() || logLevelFilter !== 'all'
                ? `显示 ${displayLines.length} / ${rawLines.length} 行`
                : `${rawLines.length} 行`}
            </span>
            <button
              onClick={() => { setQuery(''); setLogLevelFilter('all'); }}
              className="text-cyan-400 hover:text-cyan-300"
            >
              清除过滤
            </button>
          </div>
        )}
        {error ? (
          <div className="text-red-400 p-2">{error}</div>
        ) : viewMode === 'smart' && hasLlmChannel ? (
          <Virtuoso
            className="h-full"
            data={filteredLlmEvents}
            followOutput="auto"
            itemContent={(_: number, event: LlmEvent) => (
              <div className="mx-2 my-1">
                <LlmEventCard event={event} />
              </div>
            )}
          />
        ) : viewMode === 'smart' && sourceId === 'runlog' ? (
          <PolarisTerminalRenderer text={rawLines.join('\n')} className="text-slate-100 p-2" />
        ) : (
          <Virtuoso
            className="h-full"
            data={displayLines}
            followOutput={autoScroll ? "auto" : false}
            itemContent={(_: number, line: string) => (
              <div className="px-2 font-mono text-xs text-gray-400 whitespace-pre-wrap break-all leading-tight">
                {line}
              </div>
            )}
          />
        )}
      </div>
    </div>
  );
});

