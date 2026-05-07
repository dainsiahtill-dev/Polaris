import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ChevronLeft,
  Gauge,
  Loader2,
  RefreshCw,
  RadioTower,
  Server,
  TimerReset,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { apiFetchFresh } from '@/api';
import { StatusBadge, type StatusBadgeColor } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import type {
  RuntimeDiagnosticEvent,
  RuntimeDiagnosticsConnectionState,
  RuntimeDiagnosticsPayload,
  RuntimeDiagnosticsSection,
} from '@/types/runtimeDiagnostics';

interface RuntimeDiagnosticsWorkspaceProps {
  workspace: string;
  connectionState: RuntimeDiagnosticsConnectionState;
  onBackToMain: () => void;
}

type PanelTone = 'success' | 'warning' | 'error' | 'info' | 'default';

interface SummaryCard {
  id: string;
  title: string;
  subtitle: string;
  statusLabel: string;
  tone: PanelTone;
  rows: Array<[string, string]>;
  events: RuntimeDiagnosticEvent[];
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asSection(value: unknown): RuntimeDiagnosticsSection {
  return asRecord(value) as RuntimeDiagnosticsSection;
}

function pickSection(payload: RuntimeDiagnosticsPayload | null, keys: string[]): RuntimeDiagnosticsSection {
  const record = asRecord(payload);
  for (const key of keys) {
    const section = asSection(record[key]);
    if (Object.keys(section).length > 0) {
      return section;
    }
  }
  return {};
}

function stringValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return '';
}

function numberValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function boolValue(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value;
  const token = stringValue(value).toLowerCase();
  if (['true', '1', 'yes', 'ready', 'ok', 'healthy', 'connected', 'running'].includes(token)) return true;
  if (['false', '0', 'no', 'failed', 'error', 'disconnected', 'stopped'].includes(token)) return false;
  return null;
}

function firstDefined(...values: unknown[]): unknown {
  return values.find((value) => value !== null && value !== undefined && value !== '');
}

function statusFromSection(section: RuntimeDiagnosticsSection): string {
  return (
    stringValue(section.status) ||
    stringValue(section.state) ||
    stringValue(section.phase) ||
    (boolValue(section.ok) === true ? 'ok' : '') ||
    (boolValue(section.connected) === true ? 'connected' : '') ||
    'unknown'
  );
}

function toneFromStatus(status: string, section?: RuntimeDiagnosticsSection): PanelTone {
  const ok = boolValue(section?.ok);
  if (ok === true) return 'success';
  if (ok === false) return 'error';

  const token = status.toLowerCase();
  if (/(ok|ready|healthy|connected|running|open|normal|pass)/.test(token)) return 'success';
  if (/(reconnect|retry|degraded|limited|throttle|warning|pending)/.test(token)) return 'warning';
  if (/(fail|error|blocked|offline|closed|disconnect|unhealthy)/.test(token)) return 'error';
  return 'default';
}

function badgeColor(tone: PanelTone): StatusBadgeColor {
  if (tone === 'success') return 'success';
  if (tone === 'warning') return 'warning';
  if (tone === 'error') return 'error';
  if (tone === 'info') return 'info';
  return 'default';
}

function formatTime(value: unknown): string {
  const raw = stringValue(value);
  if (!raw) return '未记录';
  const epoch = Date.parse(raw);
  if (!Number.isFinite(epoch)) return raw;
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(epoch));
}

function formatBool(value: unknown, trueLabel = '是', falseLabel = '否'): string {
  const parsed = boolValue(value);
  if (parsed === true) return trueLabel;
  if (parsed === false) return falseLabel;
  return '未知';
}

function formatNumber(value: unknown): string {
  const parsed = numberValue(value);
  return parsed === null ? '未知' : String(parsed);
}

function normalizeEvents(value: unknown): RuntimeDiagnosticEvent[] {
  if (Array.isArray(value)) {
    return value
      .filter((item): item is RuntimeDiagnosticEvent => Boolean(item && typeof item === 'object'))
      .slice(-5);
  }

  const record = asRecord(value);
  if (!Object.keys(record).length) return [];
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

function eventsFor(section: RuntimeDiagnosticsSection): RuntimeDiagnosticEvent[] {
  return normalizeEvents(section.lifecycle).concat(normalizeEvents(section.events)).slice(-5);
}

function rateLimitRows(section: RuntimeDiagnosticsSection): Array<[string, string]> {
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

function issueText(issue: RuntimeDiagnosticEvent): string {
  return stringValue(issue.message || issue.detail || issue.status || issue.state) || '未提供详情';
}

function buildCards(
  payload: RuntimeDiagnosticsPayload | null,
  connectionState: RuntimeDiagnosticsConnectionState,
): SummaryCard[] {
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

function DiagnosticCard({ card }: { card: SummaryCard }) {
  return (
    <section
      data-testid={`runtime-diagnostics-card-${card.id}`}
      className="min-h-[220px] rounded-lg border border-white/10 bg-white/[0.035] p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">{card.title}</h2>
          <p className="mt-1 text-[11px] uppercase tracking-wider text-slate-500">{card.subtitle}</p>
        </div>
        <StatusBadge color={badgeColor(card.tone)} variant="dot" pulse={card.tone === 'warning'}>
          <span className="font-mono text-[10px]">{card.statusLabel}</span>
        </StatusBadge>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-2">
        {card.rows.map(([label, value]) => (
          <div key={label} className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2">
            <dt className="font-mono text-[10px] uppercase text-slate-500">{label}</dt>
            <dd className="mt-1 truncate text-xs text-slate-200" title={value}>{value}</dd>
          </div>
        ))}
      </dl>

      <div className="mt-4 border-t border-white/10 pt-3">
        <div className="mb-2 flex items-center gap-2 text-[11px] font-medium text-slate-400">
          <TimerReset className="h-3.5 w-3.5" />
          最近生命周期
        </div>
        <div className="space-y-1.5">
          {card.events.length === 0 ? (
            <div className="rounded-md border border-dashed border-white/10 px-2 py-2 text-[11px] text-slate-500">
              暂无事件
            </div>
          ) : (
            card.events.map((event, index) => (
              <div key={`${card.id}-${index}`} className="rounded-md bg-black/20 px-2 py-1.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[10px] uppercase text-slate-300">
                    {stringValue(event.state || event.status || event.phase) || 'event'}
                  </span>
                  <span className="shrink-0 text-[10px] text-slate-500">{formatTime(event.timestamp)}</span>
                </div>
                {issueText(event) !== '未提供详情' ? (
                  <div className="mt-0.5 truncate text-[11px] text-slate-400" title={issueText(event)}>
                    {issueText(event)}
                  </div>
                ) : null}
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

export function RuntimeDiagnosticsWorkspace({
  workspace,
  connectionState,
  onBackToMain,
}: RuntimeDiagnosticsWorkspaceProps) {
  const [payload, setPayload] = useState<RuntimeDiagnosticsPayload | null>(null);
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
      const nextPayload = await response.json() as RuntimeDiagnosticsPayload;
      setPayload(nextPayload && typeof nextPayload === 'object' ? nextPayload : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '运行诊断读取失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshDiagnostics();
  }, [refreshDiagnostics]);

  const cards = useMemo(() => buildCards(payload, connectionState), [payload, connectionState]);
  const issues = Array.isArray(payload?.issues) ? payload.issues : [];
  const generatedAt = payload?.generated_at || payload?.timestamp || null;

  return (
    <div data-testid="runtime-diagnostics-workspace" className="flex h-full flex-col overflow-hidden bg-slate-950 text-slate-100">
      <header className="flex h-14 items-center justify-between border-b border-emerald-500/20 bg-slate-950/90 px-4">
        <div className="flex min-w-0 items-center gap-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBackToMain}
            data-testid="runtime-diagnostics-back"
            className="text-slate-400 hover:bg-white/5 hover:text-slate-100"
          >
            <ChevronLeft className="mr-1 h-4 w-4" />
            返回
          </Button>
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-200 ring-1 ring-emerald-400/30">
              <Gauge className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <h1 className="text-sm font-semibold text-emerald-100">运行诊断</h1>
              <p className="truncate text-[10px] uppercase tracking-wider text-emerald-400/70" title={workspace}>
                {workspace || '未选择 workspace'}
              </p>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge color={connectionState.live ? 'success' : connectionState.reconnecting ? 'warning' : 'error'} variant="dot" pulse={connectionState.reconnecting}>
            <span className="font-mono text-[10px]">
              {connectionState.live ? 'WS LIVE' : connectionState.reconnecting ? 'WS RECONNECT' : 'WS OFFLINE'}
            </span>
          </StatusBadge>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refreshDiagnostics()}
            disabled={loading}
            data-testid="runtime-diagnostics-refresh"
            className="border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/10"
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            刷新
          </Button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-auto p-4">
        <div className="mb-4 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_280px]">
          <section className="rounded-lg border border-white/10 bg-white/[0.035] px-4 py-3">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <div className="flex items-center gap-2">
                {connectionState.live ? <Wifi className="h-4 w-4 text-emerald-300" /> : <WifiOff className="h-4 w-4 text-amber-300" />}
                <div>
                  <div className="text-[10px] uppercase text-slate-500">current ws</div>
                  <div className="text-xs font-semibold text-slate-200">{connectionState.live ? 'connected' : 'disconnected'}</div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <RadioTower className={cn('h-4 w-4', connectionState.reconnecting ? 'text-amber-300' : 'text-slate-500')} />
                <div>
                  <div className="text-[10px] uppercase text-slate-500">reconnect</div>
                  <div className="text-xs font-semibold text-slate-200">{connectionState.reconnecting ? 'active' : 'idle'}</div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Server className="h-4 w-4 text-cyan-300" />
                <div>
                  <div className="text-[10px] uppercase text-slate-500">attempts</div>
                  <div className="text-xs font-semibold text-slate-200">{connectionState.attemptCount}</div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <TimerReset className="h-4 w-4 text-slate-400" />
                <div>
                  <div className="text-[10px] uppercase text-slate-500">snapshot</div>
                  <div className="text-xs font-semibold text-slate-200">{formatTime(generatedAt)}</div>
                </div>
              </div>
            </div>
          </section>

          <section className="rounded-lg border border-white/10 bg-white/[0.035] px-4 py-3">
            <div className="flex items-center gap-2 text-[11px] font-medium text-slate-400">
              <AlertTriangle className="h-3.5 w-3.5" />
              诊断问题
            </div>
            <div className="mt-2 text-sm font-semibold text-slate-100">{issues.length}</div>
            <div className="mt-1 truncate text-[11px] text-slate-500">
              {error || (issues[0] ? issueText(issues[0]) : '暂无后端上报问题')}
            </div>
          </section>
        </div>

        {error ? (
          <div data-testid="runtime-diagnostics-error" className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          {cards.map((card) => (
            <DiagnosticCard key={card.id} card={card} />
          ))}
        </div>
      </main>
    </div>
  );
}
