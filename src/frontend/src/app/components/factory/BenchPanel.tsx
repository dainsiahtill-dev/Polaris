/**
 * BenchPanel — a Factory sub-panel that streams L1-L8 bench progress in
 * real time. Driven by `useFactoryBench`; no polling, all events arrive
 * over the unified Nats-JetStream/WebSocket runtime transport, keyed to the
 * bench session instead of the chain subprocess workspace.
 */

import { useMemo } from 'react';
import {
  Activity,
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  Clock,
  Loader2,
  RefreshCw,
  XCircle,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { StatusBadge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import {
  useFactoryBench,
  type UseFactoryBenchOptions,
  type UseFactoryBenchResult,
} from '@/hooks/useFactoryBench';
import type { FactoryBenchEvent, FactoryBenchSessionSummary } from '@/services/benchService';

interface BenchPanelProps {
  className?: string;
  onWorkspaceChange?: UseFactoryBenchOptions['onWorkspaceChange'];
}

const STATUS_LABELS: Record<string, { label: string; color: 'info' | 'success' | 'error' }> = {
  running: { label: '运行中', color: 'info' },
  completed: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
};

function formatTime(iso: string | undefined | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString();
}

function statusColor(status: string | undefined | null): 'info' | 'success' | 'error' {
  if (!status) return 'info';
  return STATUS_LABELS[status]?.color ?? 'info';
}

function statusLabel(status: string | undefined | null): string {
  if (!status) return '未知';
  return STATUS_LABELS[status]?.label ?? status;
}

function eventTone(event: FactoryBenchEvent): string {
  if (event.ok === false) return 'error';
  if (event.type.endsWith('.completed') || event.ok === true) return 'success';
  if (event.type.endsWith('.started')) return 'info';
  if (event.type.endsWith('.failed')) return 'error';
  return 'event';
}

function summarizeSession(session: FactoryBenchSessionSummary): string {
  const total = session.total || session.project_ids?.length || 0;
  const completed = session.completed || 0;
  const failed = session.failed || 0;
  return `${completed}/${total} 已完成${failed > 0 ? ` · ${failed} 失败` : ''}`;
}

export function BenchPanel({ className, onWorkspaceChange }: BenchPanelProps): JSX.Element {
  const bench: UseFactoryBenchResult = useFactoryBench({ autoSelect: 'newest', onWorkspaceChange });
  const { sessions, currentSession, events, isStreaming, isLoading, error, refresh, select } = bench;

  const progress = useMemo(() => {
    const total = currentSession?.total || currentSession?.project_ids?.length || 0;
    const done = (currentSession?.completed || 0) + (currentSession?.failed || 0);
    return total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  }, [currentSession]);

  return (
    <section
      className={cn(
        'flex h-full flex-col gap-3 rounded-md border border-slate-800 bg-slate-950/40 p-3 text-xs',
        className,
      )}
      data-testid="bench-panel"
    >
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-slate-200">
          <Activity className="h-4 w-4 text-emerald-400" />
          <span className="font-medium">Factory Bench（L1-L8 批次）</span>
          {isStreaming ? (
            <span className="inline-flex items-center gap-1 text-emerald-300">
              <Loader2 className="h-3 w-3 animate-spin" /> 实时
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-slate-500">
              <CircleDashed className="h-3 w-3" /> 已暂停
            </span>
          )}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void refresh()}
          disabled={isLoading}
          className="h-7 px-2 text-slate-300"
        >
          <RefreshCw className={cn('h-3 w-3', isLoading && 'animate-spin')} />
          <span className="ml-1">刷新</span>
        </Button>
      </header>

      {error ? (
        <div className="rounded border border-amber-700/50 bg-amber-900/20 p-2 text-amber-200">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[220px_1fr]">
        <aside className="flex max-h-72 flex-col gap-1 overflow-y-auto rounded border border-slate-800 bg-slate-900/40 p-2">
          {sessions.length === 0 ? (
            <div className="px-2 py-3 text-slate-500">暂无 bench session</div>
          ) : (
            sessions.map((session) => (
              <button
                key={session.session_id}
                type="button"
                onClick={() => void select(session.session_id)}
                className={cn(
                  'flex flex-col items-start gap-1 rounded px-2 py-1.5 text-left text-slate-300 hover:bg-slate-800/70',
                  currentSession?.session_id === session.session_id && 'bg-slate-800/80',
                )}
              >
                <div className="flex w-full items-center justify-between gap-2">
                  <span className="truncate font-mono text-[11px] text-slate-200">
                    {session.session_id}
                  </span>
                  <StatusBadge color={statusColor(session.status)} variant="soft">
                    {statusLabel(session.status)}
                  </StatusBadge>
                </div>
                <div className="text-[11px] text-slate-400">
                  {summarizeSession(session)}
                </div>
                <div className="flex w-full items-center justify-between text-[10px] text-slate-500">
                  <span>{formatTime(session.updated_at)}</span>
                  <span className="truncate">{session.work_dir}</span>
                </div>
              </button>
            ))
          )}
        </aside>

        <div className="flex min-h-72 flex-col gap-2 rounded border border-slate-800 bg-slate-900/40 p-3">
          {!currentSession ? (
            <div className="flex flex-1 items-center justify-center text-slate-500">
              选择左侧 session 查看实时事件
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 text-slate-200">
                <span className="font-mono text-[11px] text-slate-300">
                  {currentSession.session_id}
                </span>
                <StatusBadge color={statusColor(currentSession.status)} variant="soft">
                  {statusLabel(currentSession.status)}
                </StatusBadge>
                <span className="text-slate-400">{summarizeSession(currentSession)}</span>
              </div>
              <div className="text-[11px] text-slate-500" title={`创建 ${formatTime(currentSession.created_at)} · 更新 ${formatTime(currentSession.updated_at)}${currentSession.completed_at ? ` · 完成 ${formatTime(currentSession.completed_at)}` : ''}`}>
                更新 {formatTime(currentSession.updated_at)}
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded bg-slate-800">
                <div
                  className="h-full bg-emerald-500 transition-all"
                  style={{ width: `${progress}%` }}
                  data-testid="bench-progress"
                  data-progress={progress}
                />
              </div>
              <div className="mt-1 flex flex-1 flex-col gap-1 overflow-y-auto rounded border border-slate-800 bg-slate-950/60 p-2 font-mono text-[11px] leading-5">
                {events.length === 0 ? (
                  <div className="text-slate-500">暂无事件</div>
                ) : (
                  events.slice().reverse().map((event, idx) => (
                    <BenchEventLine key={`${event.ts ?? 't'}-${idx}`} event={event} />
                  ))
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function BenchEventLine({ event }: { event: FactoryBenchEvent }): JSX.Element {
  const tone = eventTone(event);
  const Icon =
    tone === 'error'
      ? XCircle
      : tone === 'success'
        ? CheckCircle2
        : tone === 'info'
          ? Clock
          : CircleSlash;
  const color =
    tone === 'error'
      ? 'text-rose-300'
      : tone === 'success'
        ? 'text-emerald-300'
        : tone === 'info'
          ? 'text-sky-300'
          : 'text-slate-300';
  const projectId = typeof event.meta?.['project_id'] === 'string' ? event.meta['project_id'] : null;
  return (
    <div className="flex items-start gap-2" data-event-type={event.type} data-event-tone={tone}>
      <Icon className={cn('mt-0.5 h-3 w-3 shrink-0', color)} />
      <span className="text-slate-500">{formatTime(event.ts)}</span>
      <span className={cn('shrink-0', color)}>{event.type}</span>
      {projectId ? <span className="text-slate-400">[{projectId}]</span> : null}
      {event.summary ? <span className="truncate text-slate-300">{event.summary}</span> : null}
    </div>
  );
}
