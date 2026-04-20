/**
 * UnifiedLogsView - High-signal Timeline View for Log Events
 *
 * Default view that shows a filtered, high-signal timeline of log events
 * with support for channel switching, filtering, and noise folding.
 */

import {
  Brain,
  ChevronDown,
  ChevronRight,
  Cpu,
  Filter,
  RefreshCw,
  Search,
  Terminal,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { connectWebSocket } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
import {
  CHANNEL_METADATA,
  KIND_STYLES,
  SEVERITY_STYLES,
  type CanonicalLogEvent,
  type LogChannel,
  type LogEventMessage,
  type LogEventResponse,
  type LogQueryParams,
  type LogSeverity,
} from './types';

// Filter chip component
function FilterChip({
  label,
  active,
  onClick,
  color = 'blue',
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  color?: 'blue' | 'green' | 'purple' | 'yellow' | 'red';
}) {
  const colorClasses = {
    blue: active ? 'bg-blue-500/30 border-blue-400' : 'bg-blue-500/10 border-blue-500/30 hover:bg-blue-500/20',
    green: active ? 'bg-green-500/30 border-green-400' : 'bg-green-500/10 border-green-500/30 hover:bg-green-500/20',
    purple: active ? 'bg-purple-500/30 border-purple-400' : 'bg-purple-500/10 border-purple-500/30 hover:bg-purple-500/20',
    yellow: active ? 'bg-yellow-500/30 border-yellow-400' : 'bg-yellow-500/10 border-yellow-500/30 hover:bg-yellow-500/20',
    red: active ? 'bg-red-500/30 border-red-400' : 'bg-red-500/10 border-red-500/30 hover:bg-red-500/20',
  };

  return (
    <button
      onClick={onClick}
      className={`px-2 py-1 rounded-full text-xs font-medium border transition-colors ${colorClasses[color]} ${
        active ? 'text-white' : 'text-gray-300'
      }`}
    >
      {label}
    </button>
  );
}

// Log event card component
function LogEventCard({
  event,
  expanded,
  onToggle,
}: {
  event: CanonicalLogEvent;
  expanded: boolean;
  onToggle: () => void;
}) {
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
    } catch {
      return event.ts;
    }
  }, [event.ts]);

  return (
    <div
      className={`border rounded-lg mb-2 overflow-hidden ${
        isNoise ? 'border-gray-700/50 opacity-60' : 'border-gray-600'
      }`}
    >
      {/* Card header - always visible */}
      <div
        className="flex items-start gap-2 p-3 cursor-pointer hover:bg-gray-800/50"
        onClick={onToggle}
      >
        {/* Expand icon */}
        <div className="mt-0.5">
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-gray-400" />
          ) : (
            <ChevronRight className="w-4 h-4 text-gray-400" />
          )}
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          {/* Time and sequence */}
          <div className="flex items-center gap-2 text-xs text-gray-400 mb-1">
            <span>{timeStr}</span>
            <span className="text-gray-600">#{event.seq}</span>
            {event.run_id && <span className="text-gray-500">• {event.run_id.slice(0, 8)}</span>}
          </div>

          {/* Message */}
          <div className="text-sm text-gray-200 truncate mb-2">
            {event.message || '(无消息)'}
          </div>

          {/* Tags row */}
          <div className="flex items-center gap-2 flex-wrap">
            {/* Channel badge */}
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                event.channel === 'system'
                  ? 'bg-blue-500/20 text-blue-300'
                  : event.channel === 'process'
                    ? 'bg-green-500/20 text-green-300'
                    : 'bg-purple-500/20 text-purple-300'
              }`}
            >
              {event.channel}
            </span>

            {/* Severity badge */}
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${severityStyle.bg} ${severityStyle.text}`}
            >
              {severityStyle.label}
            </span>

            {/* Kind badge */}
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${kindStyle.bg} ${kindStyle.text}`}
            >
              {kindStyle.label}
            </span>

            {/* Actor badge */}
            {event.actor && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-500/20 text-gray-300">
                {event.actor}
              </span>
            )}

            {/* Signal score indicator */}
            {!isNoise && signalScore > 0 && (
              <div className="flex items-center gap-1 ml-auto">
                <div className="w-12 h-1.5 bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-yellow-400 to-green-400"
                    style={{ width: `${signalScore * 100}%` }}
                  />
                </div>
                <span className="text-[10px] text-gray-500">{Math.round(signalScore * 100)}%</span>
              </div>
            )}

            {/* Noise indicator */}
            {isNoise && (
              <span className="ml-auto text-[10px] text-gray-500 italic">已折叠</span>
            )}
          </div>
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-gray-700 p-3 bg-gray-900/50">
          {/* Summary from LLM enrichment */}
          {event.enrichment?.summary && (
            <div className="mb-3">
              <div className="text-xs text-gray-500 mb-1">摘要</div>
              <div className="text-sm text-gray-300">{event.enrichment.summary}</div>
            </div>
          )}

          {/* Raw data */}
          {event.raw && (
            <div className="mb-3">
              <div className="text-xs text-gray-500 mb-1">原始数据</div>
              <pre className="text-xs text-gray-400 bg-gray-800 p-2 rounded overflow-x-auto max-h-40">
                {JSON.stringify(event.raw, null, 2)}
              </pre>
            </div>
          )}

          {/* References */}
          {event.refs && Object.keys(event.refs).length > 0 && (
            <div>
              <div className="text-xs text-gray-500 mb-1">引用</div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(event.refs).map(([key, value]) => (
                  <span
                    key={key}
                    className="px-2 py-0.5 bg-gray-700 rounded text-xs text-gray-300"
                  >
                    {key}: {String(value).slice(0, 30)}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Channel tab component
function ChannelTab({
  channel,
  active,
  count,
  onClick,
}: {
  channel: LogChannel;
  active: boolean;
  count?: number;
  onClick: () => void;
}) {
  const meta = CHANNEL_METADATA[channel];
  const IconComponent =
    channel === 'system' ? Cpu : channel === 'process' ? Terminal : Brain;

  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${
        active
          ? 'bg-blue-500/20 border border-blue-500/40 text-blue-300'
          : 'bg-gray-800/50 border border-gray-700 text-gray-400 hover:bg-gray-800 hover:text-gray-300'
      }`}
    >
      <IconComponent className="w-4 h-4" />
      <span className="font-medium">{meta.label}</span>
      {count !== undefined && count > 0 && (
        <span className="ml-1 px-1.5 py-0.5 bg-gray-700 rounded text-xs">{count}</span>
      )}
    </button>
  );
}

// Main UnifiedLogsView component
interface UnifiedLogsViewProps {
  workspace: string;
  runId?: string;
  isOpen: boolean;
  onClose: () => void;
}

export function UnifiedLogsView({
  workspace,
  runId,
  isOpen,
  onClose,
}: UnifiedLogsViewProps) {
  // State
  const [activeChannel, setActiveChannel] = useState<LogChannel>('system');
  const [events, setEvents] = useState<CanonicalLogEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [highSignalOnly, setHighSignalOnly] = useState(true);
  const [expandedEvents, setExpandedEvents] = useState<Set<string>>(new Set());
  const [severityFilter, setSeverityFilter] = useState<LogSeverity | null>(null);

  // WebSocket connection
  const [ws, setWs] = useState<WebSocket | null>(null);

  // Query events via WebSocket
  const queryEvents = useCallback(
    (params: LogQueryParams) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      const message: LogEventMessage = {
        type: 'event',
        action: 'query',
        ...params,
      };

      ws.send(JSON.stringify(message));
    },
    [ws]
  );

  // Initial load and WebSocket setup
  useEffect(() => {
    if (!isOpen) return;
    let alive = true;
    let websocket: WebSocket | null = null;
    setError(null);

    const handleMessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data);

        // Handle event responses
        if (msg.type === 'event' && msg.action === 'query_result') {
          const response = msg as LogEventResponse;
          setEvents(response.events);
          setCursor(response.next_cursor);
          setHasMore(response.has_more);
          setLoading(false);
        }
      } catch (e) {
        devLogger.error('Failed to parse WebSocket message:', e);
      }
    };

    const connect = async () => {
      setLoading(true);
      try {
        websocket = await connectWebSocket();
      } catch {
        if (alive) {
          setError('WebSocket 连接错误');
          setLoading(false);
        }
        return;
      }
      if (!alive || !websocket) {
        websocket?.close();
        return;
      }

      websocket.onopen = () => {
        if (!alive || !websocket) {
          return;
        }
        setWs(websocket);
      };

      websocket.onmessage = handleMessage;

      websocket.onerror = () => {
        if (!alive) {
          return;
        }
        setError('WebSocket 连接错误');
        setLoading(false);
      };

      websocket.onclose = () => {
        if (!alive) {
          return;
        }
        setWs(null);
      };
    };

    void connect();

    return () => {
      alive = false;
      websocket?.close();
      setWs(null);
    };
  }, [isOpen]);

  // Re-query when filters change
  useEffect(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    setLoading(true);
    queryEvents({
      channel: activeChannel,
      run_id: runId,
      limit: 50,
      high_signal_only: highSignalOnly,
      severity: severityFilter || undefined,
    });
  }, [activeChannel, highSignalOnly, severityFilter, runId, ws, queryEvents]);

  // Toggle event expansion
  const toggleEventExpanded = useCallback((eventId: string) => {
    setExpandedEvents((prev) => {
      const next = new Set(prev);
      if (next.has(eventId)) {
        next.delete(eventId);
      } else {
        next.add(eventId);
      }
      return next;
    });
  }, []);

  // Load more events
  const loadMore = useCallback(() => {
    if (!ws || !cursor) return;

    setLoading(true);
    queryEvents({
      channel: activeChannel,
      run_id: runId,
      limit: 50,
      cursor,
      high_signal_only: highSignalOnly,
      severity: severityFilter || undefined,
    });
  }, [ws, cursor, activeChannel, runId, highSignalOnly, severityFilter, queryEvents]);

  // Filtered events (for high signal mode)
  const displayEvents = useMemo(() => {
    if (!highSignalOnly) return events;
    return events.filter((e) => {
      if (e.enrichment?.noise) return false;
      if (e.severity === 'debug') return false;
      return true;
    });
  }, [events, highSignalOnly]);

  // Group events by foldable noise
  const groupedEvents = useMemo(() => {
    const noiseGroups: CanonicalLogEvent[] = [];
    const normalEvents: CanonicalLogEvent[] = [];

    for (const event of displayEvents) {
      if (event.enrichment?.noise && event.dedupe_count > 1) {
        noiseGroups.push(event);
      } else {
        normalEvents.push(event);
      }
    }

    return { noiseGroups, normalEvents };
  }, [displayEvents]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-4xl h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div className="flex items-center gap-4">
            <h2 className="text-lg font-semibold text-white">统一日志</h2>
            <span className="text-sm text-gray-400">
              {runId ? `Run: ${runId.slice(0, 8)}` : '最新运行'}
            </span>
          </div>

          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-800 rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-4 p-4 border-b border-gray-700 bg-gray-800/50">
          {/* Channel tabs */}
          <div className="flex items-center gap-2">
            {(['system', 'process', 'llm'] as LogChannel[]).map((channel) => (
              <ChannelTab
                key={channel}
                channel={channel}
                active={activeChannel === channel}
                count={events.filter((e) => e.channel === channel).length}
                onClick={() => setActiveChannel(channel)}
              />
            ))}
          </div>

          <div className="flex-1" />

          {/* Filters */}
          <div className="flex items-center gap-2">
            {/* High signal toggle */}
            <button
              onClick={() => setHighSignalOnly(!highSignalOnly)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors ${
                highSignalOnly
                  ? 'bg-green-500/20 text-green-300 border border-green-500/40'
                  : 'bg-gray-700 text-gray-400 border border-gray-600'
              }`}
            >
              <Filter className="w-4 h-4" />
              高信号
            </button>

            {/* Refresh */}
            <button
              onClick={() => {
                setLoading(true);
                queryEvents({
                  channel: activeChannel,
                  run_id: runId,
                  limit: 50,
                  high_signal_only: highSignalOnly,
                });
              }}
              className="p-2 hover:bg-gray-700 rounded-lg transition-colors"
              disabled={loading}
            >
              <RefreshCw className={`w-4 h-4 text-gray-400 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Severity filters */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-700/50">
          <span className="text-xs text-gray-500">筛选:</span>
          {(['debug', 'info', 'warn', 'error', 'critical'] as LogSeverity[]).map((sev) => (
            <FilterChip
              key={sev}
              label={SEVERITY_STYLES[sev].label}
              active={severityFilter === sev}
              onClick={() => setSeverityFilter(severityFilter === sev ? null : sev)}
              color={sev === 'error' ? 'red' : sev === 'warn' ? 'yellow' : 'blue'}
            />
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading && events.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <div className="flex items-center gap-2 text-gray-400">
                <RefreshCw className="w-5 h-5 animate-spin" />
                <span>加载中...</span>
              </div>
            </div>
          ) : error ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-red-400">{error}</div>
            </div>
          ) : displayEvents.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-gray-500">暂无日志事件</div>
            </div>
          ) : (
            <>
              {/* Noise group summary */}
              {groupedEvents.noiseGroups.length > 0 && highSignalOnly && (
                <div className="mb-4 p-3 bg-gray-800/50 rounded-lg border border-gray-700">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-400">
                      已折叠 {groupedEvents.noiseGroups.length} 个重复/噪音事件
                    </span>
                    <button
                      onClick={() => setHighSignalOnly(false)}
                      className="text-xs text-blue-400 hover:text-blue-300"
                    >
                      查看全部
                    </button>
                  </div>
                </div>
              )}

              {/* Event cards */}
              {groupedEvents.normalEvents.map((event) => (
                <LogEventCard
                  key={event.event_id}
                  event={event}
                  expanded={expandedEvents.has(event.event_id)}
                  onToggle={() => toggleEventExpanded(event.event_id)}
                />
              ))}

              {/* Load more */}
              {hasMore && (
                <div className="flex justify-center mt-4">
                  <button
                    onClick={loadMore}
                    disabled={loading}
                    className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm text-gray-300 transition-colors"
                  >
                    {loading ? '加载中...' : '加载更多'}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
