import { X, Activity, Coins, Layers, Clock, User } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { contextOSFormat } from './contextOSData';
import type { RoleInternalContext, PipelineState } from './contextOSData';

export interface RoleContextDrawerProps {
  context: RoleInternalContext;
  open: boolean;
  onClose: () => void;
}

const STATE_STYLES: Record<PipelineState, { dot: string; label: string; text: string }> = {
  active: { dot: 'bg-accent-secondary', label: '运行', text: 'text-accent-secondary' },
  blocked: { dot: 'bg-status-error', label: '受阻', text: 'text-status-error' },
  idle: { dot: 'bg-text-dim', label: '空闲', text: 'text-text-muted' },
};

function formatAge(epochMs: number | null): string {
  if (!epochMs) return '—';
  const seconds = Math.floor((Date.now() - epochMs) / 1000);
  if (seconds < 5) return '刚刚';
  if (seconds < 60) return `${seconds}s 前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m 前`;
  return `${Math.floor(seconds / 3600)}h 前`;
}

export function RoleContextDrawer({ context, open, onClose }: RoleContextDrawerProps) {
  if (!open) return null;

  const style = STATE_STYLES[context.state];

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} role="button" tabIndex={-1} aria-label="关闭" />
      <aside
        className="relative z-10 flex h-full w-full max-w-2xl flex-col border-l border-white/[0.07] bg-bg-panel/95 backdrop-blur"
        role="dialog"
        aria-modal="true"
        aria-label={`${context.title} 内部上下文`}
      >
        <header className="flex items-center justify-between border-b border-white/[0.06] px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-black/30 font-heading text-sm font-bold text-text-main">
              {context.courtTitle.slice(0, 1)}
            </div>
            <div>
              <h2 className="text-sm font-bold text-text-main">{context.title}</h2>
              <p className="text-[11px] text-text-dim">{context.courtTitle} · 内部上下文视图</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className={cn('flex items-center gap-1.5 rounded-full border border-white/10 bg-black/30 px-2.5 py-1 text-[10px]', style.text)}>
              <span className={cn('h-1.5 w-1.5 rounded-full', style.dot)} />
              {style.label}
            </span>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md p-1 text-text-muted hover:bg-white/5 hover:text-text-main"
              aria-label="关闭"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div className="grid flex-1 grid-cols-1 gap-4 overflow-auto p-4 md:grid-cols-[260px_1fr]">
          {/* Summary column */}
          <div className="space-y-3">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <h3 className="mb-2 flex items-center gap-2 text-[11px] font-semibold text-text-main">
                <Activity className="h-3.5 w-3.5 text-accent-secondary" />
                活动摘要
              </h3>
              <div className="space-y-2 text-[11px]">
                <div className="flex justify-between">
                  <span className="text-text-dim">事件</span>
                  <span className="font-mono text-text-main">{context.eventCount}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-dim">调用</span>
                  <span className="font-mono text-text-main">{context.calls}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-dim">投影</span>
                  <span className="font-mono text-text-main">{context.projectionCount}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-dim">回执</span>
                  <span className="font-mono text-text-main">{context.receiptCount}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-dim">最近活动</span>
                  <span className="font-mono text-text-main">{formatAge(context.lastEventAt)}</span>
                </div>
              </div>
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <h3 className="mb-2 flex items-center gap-2 text-[11px] font-semibold text-text-main">
                <Coins className="h-3.5 w-3.5 text-gold" />
                Token 用量
              </h3>
              <div className="space-y-2 text-[11px]">
                <div className="flex justify-between">
                  <span className="text-text-dim">累计</span>
                  <span className="font-mono text-text-main">{contextOSFormat.tokens(context.totalTokens)} tok</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-dim">提示</span>
                  <span className="font-mono text-text-main">{contextOSFormat.tokens(context.promptTokens)} tok</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-dim">输出</span>
                  <span className="font-mono text-text-main">{contextOSFormat.tokens(context.completionTokens)} tok</span>
                </div>
              </div>
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <h3 className="mb-2 flex items-center gap-2 text-[11px] font-semibold text-text-main">
                <Layers className="h-3.5 w-3.5 text-accent" />
                上下文规模
              </h3>
              <div className="space-y-2 text-[11px]">
                <div className="flex justify-between">
                  <span className="text-text-dim">在窗项数</span>
                  <span className="font-mono text-text-main">
                    {context.contextItemsCount !== null ? context.contextItemsCount : '—'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-dim">上下文 token</span>
                  <span className="font-mono text-text-main">
                    {context.contextTokensLatest !== null ? contextOSFormat.tokens(context.contextTokensLatest) : '—'}
                  </span>
                </div>
              </div>
            </div>

            {context.currentTaskId && (
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                <h3 className="mb-2 flex items-center gap-2 text-[11px] font-semibold text-text-main">
                  <User className="h-3.5 w-3.5 text-status-info" />
                  当前任务
                </h3>
                <p className="text-[11px] text-text-main">{context.currentTaskTitle || context.currentTaskId}</p>
                <p className="mt-1 font-mono text-[9px] text-text-dim">{context.currentTaskId}</p>
              </div>
            )}
          </div>

          {/* Timeline column */}
          <div className="flex min-h-0 flex-col rounded-xl border border-white/[0.06] bg-white/[0.02]">
            <h3 className="flex items-center gap-2 border-b border-white/[0.06] px-3 py-2 text-[11px] font-semibold text-text-main">
              <Clock className="h-3.5 w-3.5 text-accent-secondary" />
              实时事件流
            </h3>
            <div className="flex-1 overflow-auto p-2">
              {context.events.length === 0 ? (
                <div className="py-8 text-center text-[11px] text-text-dim">
                  暂无该角色事件 — 启动对应角色后将实时流入
                </div>
              ) : (
                <div className="space-y-1">
                  {context.events.map((event) => (
                    <div
                      key={event.id}
                      className="rounded-md px-2 py-1.5 text-[11px] hover:bg-white/[0.03]"
                    >
                      <div className="flex items-start gap-2">
                        <span className="mt-0.5 font-mono text-[9px] text-text-dim">
                          {contextOSFormat.clock(event.ts) || '--:--:--'}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            <span className="truncate text-text-main">{event.name || event.kind}</span>
                            {event.category === 'error' && (
                              <span className="rounded bg-status-error/10 px-1 text-[9px] text-status-error">错误</span>
                            )}
                            {event.isProjection && (
                              <span className="rounded bg-accent-secondary/10 px-1 text-[9px] text-accent-secondary">投影</span>
                            )}
                            {event.hasReceipt && (
                              <span className="rounded bg-gold/10 px-1 text-[9px] text-gold">回执</span>
                            )}
                          </div>
                          <p className="truncate text-text-dim" title={event.summary}>{event.summary || '—'}</p>
                          <div className="mt-0.5 flex flex-wrap items-center gap-1">
                            {event.hasUsage && (
                              <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted">
                                {contextOSFormat.tokens(event.totalTokens)} tok
                              </span>
                            )}
                            {event.durationMs !== null && event.durationMs > 0 && (
                              <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted">
                                {event.durationMs}ms
                              </span>
                            )}
                            {event.contextItems !== null && (
                              <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted">
                                {event.contextItems} 项
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </aside>
    </div>
  );
}
