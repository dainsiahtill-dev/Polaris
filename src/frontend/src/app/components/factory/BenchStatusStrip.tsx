/**
 * BenchStatusStrip — compact cross-page bench status indicator.
 *
 * PM / ChiefEngineer / Director / ContextOS pages all render this strip so
 * the user can see L1-L8 batch progress regardless of which role panel
 * they have open. The strip auto-hides when no bench session is active.
 *
 * Drives off the same `useFactoryBench` hook as the Factory page's
 * BenchPanel, so the same Nat-JetStream WebSocket stream powers every
 * surface.
 */

import { useMemo } from 'react';
import { Activity, CheckCircle2, CircleDashed, Hammer, Loader2, XCircle } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { useFactoryBench } from '@/hooks/useFactoryBench';
import type { FactoryBenchEvent, FactoryBenchSessionSummary } from '@/services/benchService';

interface BenchStatusStripProps {
  className?: string;
  websocketLive?: boolean;
  websocketReconnecting?: boolean;
  websocketAttemptCount?: number;
}

const STATUS_COLOR: Record<string, string> = {
  running: 'text-sky-300',
  completed: 'text-emerald-300',
  failed: 'text-rose-300',
  cancelled: 'text-amber-300',
};

function statusLabel(status: string | undefined | null): string {
  if (!status) return '空闲';
  if (status === 'running') return '运行中';
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'cancelled') return 'cancelled';
  return status;
}

function progressPct(session: FactoryBenchSessionSummary | null): number {
  if (!session) return 0;
  const total = session.total || session.project_ids?.length || 0;
  const done = (session.completed || 0) + (session.failed || 0);
  return total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
}

function summarize(s: FactoryBenchSessionSummary): string {
  const total = s.total || s.project_ids?.length || 0;
  return `${s.completed || 0}/${total} 通过${s.failed ? ` · ${s.failed} 失败` : ''}`;
}

function lastBenchEvent(events: FactoryBenchEvent[]): FactoryBenchEvent | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    if (event && event.type) return event;
  }
  return null;
}

export function BenchStatusStrip({
  className,
  websocketLive,
  websocketReconnecting = false,
  websocketAttemptCount = 0,
}: BenchStatusStripProps): JSX.Element | null {
  const { sessions, currentSession, events, isStreaming } = useFactoryBench({ autoSelect: 'newest' });
  const active = useMemo(
    () => (
      sessions.find((session) => session.session_id === currentSession?.session_id)
      || currentSession
      || sessions[0]
    ),
    [sessions, currentSession],
  );

  if (!active) {
    return null;
  }

  const progress = progressPct(active);
  const last = lastBenchEvent(events);
  const color = STATUS_COLOR[active.status] || 'text-slate-300';
  const StatusIcon =
    active.status === 'completed'
      ? CheckCircle2
      : active.status === 'failed'
        ? XCircle
        : active.status === 'running'
          ? Loader2
          : CircleDashed;
  const projectId =
    last && typeof last.meta?.['project_id'] === 'string' ? last.meta['project_id'] : null;
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

  return (
    <div
      className={cn(
        'flex h-8 shrink-0 items-center gap-3 border-b border-white/10 bg-slate-950/80 px-4 text-[11px]',
        className,
      )}
      data-testid="bench-status-strip"
      data-bench-session={active.session_id}
      data-bench-status={active.status}
    >
      <div className="flex items-center gap-1.5 text-slate-200">
        <Hammer className="h-3.5 w-3.5 text-emerald-300" />
        <span className="font-medium">Factory Bench</span>
        <span className="text-slate-500">·</span>
        <StatusIcon
          className={cn(
            'h-3 w-3',
            color,
            active.status === 'running' && isStreaming && 'animate-spin',
          )}
        />
        <span className={color}>{statusLabel(active.status)}</span>
      </div>

      <div className="flex min-w-0 flex-1 items-center gap-2">
        <div className="h-1.5 w-32 shrink-0 overflow-hidden rounded bg-slate-800">
          <div
            className="h-full bg-emerald-500 transition-all"
            style={{ width: `${progress}%` }}
            data-testid="bench-strip-progress"
            data-progress={progress}
          />
        </div>
        <span className="font-mono text-[10px] text-slate-400">{summarize(active)}</span>
      </div>

      <div
        className="flex min-w-0 items-center gap-1.5 text-slate-400"
        data-testid="bench-strip-last-event"
        title={lastLabel}
      >
        <Activity className="h-3 w-3 shrink-0 text-slate-500" />
        <span className="truncate font-mono text-[10px]">{lastLabel}</span>
      </div>

      <span className="shrink-0 font-mono text-[10px] text-slate-600">
        {active.session_id}
      </span>

      {showWebsocketState ? (
        <span
          className={cn(
            'shrink-0 rounded border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide',
            websocketClass,
          )}
          data-testid="bench-strip-ws-status"
          data-ws-live={websocketLive ? 'true' : 'false'}
          data-ws-reconnecting={websocketReconnecting ? 'true' : 'false'}
          data-ws-attempts={websocketAttemptCount}
        >
          {websocketLabel}
        </span>
      ) : null}
    </div>
  );
}
