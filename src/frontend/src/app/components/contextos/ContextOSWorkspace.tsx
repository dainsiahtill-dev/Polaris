/**
 * ContextOS 实时视图 (ContextOS Real-time Dashboard)
 *
 * 可视化 Polaris 的「上下文操作系统」实时数据流：从用户请求进入，经 TruthLog 真值流 →
 * WorkingMemory 活动窗口 → ProjectionEngine 自适应排序投影（内部含预算规划）→ RoleSignalPlane
 * 角色信号 → project() 消息装配 → CompressionEngine 装配后预算压缩兜底 → LLM 调用，再回流到
 * Receipt / Telemetry 回执遥测的反馈闭环（顺序忠实于后端 gateway.py 真实装配流）。
 *
 * 数据源 = Polaris 既有实时框架（无轮询）：emit_event/emit_llm_event → MessageBus →
 * WebSocket /v2/ws/runtime → useRuntime → llmStreamEvents/executionLogs/processStreamEvents
 * 这些 props 经 buildTelemetryFromStream 派生为遥测（见 contextOSTelemetry.ts / contextOSData.ts）。
 * 组件随 WS 事件到达即重渲染。真实 per-call token / 时延来自 journal `llm` 通道（raw.data），
 * 实时送达；仅当实时流无 token 时才退回用量统计通道并标注「非实时」，绝不伪造精度。
 */

import { useMemo, useState, type ReactNode } from 'react';
import {
  Network,
  ChevronLeft,
  RefreshCw,
  Cpu,
  Database,
  Layers,
  GitBranch,
  Boxes,
  Gauge,
  FileStack,
  ShieldCheck,
  Radio,
  ArrowRight,
  Coins,
  Activity,
  type LucideIcon,
} from 'lucide-react';

import { Button } from '@/app/components/ui/button';
import { StatusBadge } from '@/app/components/ui/badge';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import { cn } from '@/app/components/ui/utils';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';
import type { UsageStats } from '@/app/components/UsageHUD';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';
import type { LlmRuntimeGateState } from '@/app/hooks/useLlmRuntimeGate';
import type { SnapshotPayload } from '@/app/types/appContracts';
import type { QualityGateData } from '@/app/components/pm';

import {
  buildContextOSModel,
  contextOSFormat,
  decisionMatchesRole,
  NOMINAL_CONTEXT_WINDOW,
  type ComponentHealth,
  type DecisionRow,
  type EventTypeSlice,
  type PipelineStage,
  type PipelineState,
  type RoleCard,
  type RoleInternalContext,
} from './contextOSData';
import { buildTelemetryFromStream, type ContextOSEvent } from './contextOSTelemetry';

export interface ContextOSWorkspaceProps {
  workspace: string;
  onBackToMain: () => void;
  onRefresh?: () => void;
  live: boolean;
  reconnecting?: boolean;
  usageStats: UsageStats | null;
  currentPhase: string;
  pmRunning: boolean;
  directorRunning: boolean;
  llmRuntimeState: LlmRuntimeGateState;
  dialogueEvents: DialogueEvent[];
  executionLogs: LogEntry[];
  /** LLM 流（WebSocket 实时推送，channel=llm）。 */
  llmStreamEvents: LogEntry[];
  /** 进程/系统流（WebSocket 实时推送，channel=process）。 */
  processStreamEvents: LogEntry[];
  snapshot: SnapshotPayload | null;
  qualityGate?: QualityGateData | null;
}

const STAGE_ICONS: Record<string, LucideIcon> = {
  request: Radio,
  truthlog: Database,
  working_mem: Layers,
  projection: GitBranch,
  role_signal: Boxes,
  budget: Gauge,
  prompt: FileStack,
  llm: Cpu,
};

const COMPONENT_ICONS: Record<string, LucideIcon> = {
  truthlog: Database,
  working_mem: Layers,
  projection: GitBranch,
  role_signal: Boxes,
  budget: Gauge,
  prompt: FileStack,
  telemetry: ShieldCheck,
};

const STATE_STYLES: Record<PipelineState, { dot: string; ring: string; text: string; label: string }> = {
  active: {
    dot: 'bg-accent-secondary',
    ring: 'border-accent-secondary/50 bg-accent-secondary/10 shadow-[0_0_16px_rgba(74,158,158,0.25)]',
    text: 'text-accent-secondary',
    label: '运行',
  },
  blocked: {
    dot: 'bg-status-error',
    ring: 'border-status-error/50 bg-status-error/10',
    text: 'text-status-error',
    label: '受阻',
  },
  idle: {
    dot: 'bg-text-dim',
    ring: 'border-white/10 bg-white/[0.02]',
    text: 'text-text-muted',
    label: '空闲',
  },
};

function badgeColorForState(state: PipelineState): 'success' | 'error' | 'default' {
  if (state === 'active') return 'success';
  if (state === 'blocked') return 'error';
  return 'default';
}

/** 把最近更新时间戳格式化成相对新鲜度（刚刚 / Ns 前 / Nm 前）。 */
function formatFreshness(epochMs: number): string {
  const deltaMs = Date.now() - epochMs;
  if (!Number.isFinite(deltaMs) || deltaMs < 0) return '刚刚';
  const seconds = Math.floor(deltaMs / 1000);
  if (seconds < 5) return '刚刚';
  if (seconds < 60) return `${seconds}s 前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m 前`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h 前`;
}

// ---------------------------------------------------------------------------
// 子组件
// ---------------------------------------------------------------------------

function PipelineNode({ stage }: { stage: PipelineStage }) {
  const Icon = STAGE_ICONS[stage.id] ?? Activity;
  const style = STATE_STYLES[stage.state];
  return (
    <div
      data-testid={`contextos-stage-${stage.id}`}
      data-state={stage.state}
      className={cn(
        'relative flex w-[112px] shrink-0 flex-col items-center gap-1.5 rounded-xl border px-2 py-3 text-center transition-all duration-500',
        style.ring,
      )}
      title={`${stage.component} — ${stage.hint}`}
    >
      <div className={cn('flex h-9 w-9 items-center justify-center rounded-lg bg-black/30', style.text)}>
        <Icon className="h-4 w-4" />
        {stage.state === 'active' && (
          <span className="absolute right-1.5 top-1.5 flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-secondary opacity-75 motion-reduce:animate-none" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent-secondary" />
          </span>
        )}
      </div>
      <div className="text-[11px] font-semibold leading-tight text-text-main">{stage.label}</div>
      <div className="text-[9px] uppercase tracking-wide text-text-dim">{stage.hint}</div>
      <div className={cn('mt-0.5 rounded-full bg-black/30 px-1.5 py-0.5 font-mono text-[9px]', style.text)}>
        {stage.metric}
      </div>
    </div>
  );
}

function FlowArrow({ active }: { active: boolean }) {
  return (
    <div className="flex shrink-0 items-center" aria-hidden>
      <ArrowRight
        className={cn(
          'h-4 w-4 transition-colors duration-500',
          active ? 'text-accent-secondary' : 'text-text-dim/40',
        )}
      />
    </div>
  );
}

function ComponentRow({ health }: { health: ComponentHealth }) {
  const Icon = COMPONENT_ICONS[health.id] ?? Activity;
  const style = STATE_STYLES[health.state];
  return (
    <div
      data-testid={`contextos-component-${health.id}`}
      className="flex items-center gap-3 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 transition-colors hover:border-accent-secondary/25"
    >
      <div className={cn('flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-black/30', style.text)}>
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-xs font-semibold text-text-main">{health.name}</span>
          <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', style.dot)} title={style.label} />
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-[10px] text-text-dim">{health.component}</span>
          <span className={cn('shrink-0 font-mono text-[10px]', style.text)}>{health.metric}</span>
        </div>
        {health.intensity !== null && (
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/5">
            <div
              className={cn('h-full rounded-full transition-all duration-500', health.state === 'blocked' ? 'bg-status-error' : 'bg-accent-secondary/70')}
              style={{ width: `${Math.round(health.intensity * 100)}%` }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function RoleHex({ role, selected, onSelect }: { role: RoleCard; selected: boolean; onSelect: () => void }) {
  const style = STATE_STYLES[role.state];
  return (
    <button
      type="button"
      data-testid={`contextos-role-${role.id}`}
      data-selected={selected}
      aria-pressed={selected}
      onClick={onSelect}
      title={`查看 ${role.title} ${role.courtTitle} 的内部 ContextOS 状态`}
      className={cn(
        'flex flex-col items-center gap-1 rounded-xl border px-3 py-3 text-center transition-all duration-500 hover:border-accent-secondary/40',
        style.ring,
        selected && 'ring-2 ring-accent-secondary/60',
      )}
    >
      <div className={cn('flex h-10 w-10 items-center justify-center rounded-lg bg-black/30 font-heading text-sm font-bold', style.text)}>
        {role.courtTitle.slice(0, 1)}
      </div>
      <div className="text-xs font-semibold text-text-main">{role.title}</div>
      <div className="text-[10px] text-text-dim">{role.courtTitle}</div>
      <div className={cn('mt-0.5 font-mono text-[11px]', style.text)}>{role.detail}</div>
      {role.lastEventAt !== null && (
        <div className="mt-0.5 font-mono text-[9px] text-text-dim">{formatFreshness(role.lastEventAt)}</div>
      )}
    </button>
  );
}

function RoleInternalStat({ label, value, unit, highlight = false }: { label: string; value: string | number; unit?: string; highlight?: boolean }) {
  return (
    <div className="flex flex-col rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2">
      <span className="text-[9px] uppercase tracking-wider text-text-dim">{label}</span>
      <div className="mt-0.5 flex items-baseline gap-1">
        <span className={cn('font-mono text-sm font-bold', highlight ? 'text-accent-secondary' : 'text-text-main')}>{value}</span>
        {unit && <span className="text-[9px] text-text-dim">{unit}</span>}
      </div>
    </div>
  );
}

function RoleInternalEventRow({ event }: { event: ContextOSEvent }) {
  const tone: PipelineState = event.category === 'error' ? 'blocked' : event.isProjection || event.hasReceipt ? 'active' : 'idle';
  const summaryText = event.summary || event.kind || '事件';
  return (
    <div
      className="flex items-start gap-2 rounded-md px-2 py-1.5 text-[11px] hover:bg-white/[0.03]"
      aria-label={`${event.category === 'error' ? '错误事件' : '事件'} ${event.kind} ${summaryText}`}
    >
      <span className={cn('mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full', STATE_STYLES[tone].dot)} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-text-dim">{contextOSFormat.clock(event.ts)}</span>
          <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-dim">{event.kind}</span>
        </div>
        <div className="truncate text-text-muted" title={summaryText}>{summaryText}</div>
        {(event.hasUsage || event.durationMs !== null || event.hasReceipt) && (
          <div className="mt-0.5 flex flex-wrap items-center gap-1">
            {event.hasUsage && event.totalTokens > 0 && (
              <span className="rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary">
                {contextOSFormat.tokens(event.totalTokens)} tok
              </span>
            )}
            {event.durationMs !== null && event.durationMs > 0 && (
              <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted">{event.durationMs}ms</span>
            )}
            {event.hasReceipt && (
              <span className="rounded bg-gold/10 px-1 font-mono text-[9px] text-gold">快照</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function RoleInternalPanel({ role }: { role: RoleCard }) {
  const ctx = role.internalContext;
  const style = STATE_STYLES[ctx.state];

  const pipeline: PipelineStage[] = [
    { id: 'truthlog', label: 'TruthLog', component: '事件真值流', hint: '角色专属事件流', state: ctx.eventCount > 0 ? 'active' : 'idle', metric: `${ctx.eventCount} 事件` },
    { id: 'working_mem', label: 'WorkingMem', component: '活动窗口', hint: '在窗上下文项', state: (ctx.contextItemsCount ?? 0) > 0 ? 'active' : 'idle', metric: ctx.contextItemsCount !== null ? `${ctx.contextItemsCount} 项` : '—' },
    { id: 'projection', label: 'ProjectionEngine', component: '投影装配', hint: '上下文装配次数', state: ctx.projectionCount > 0 ? 'active' : 'idle', metric: `${ctx.projectionCount} 投影` },
    { id: 'receipt', label: 'ReceiptStore', component: '快照回执', hint: '落盘回执数', state: ctx.receiptCount > 0 ? 'active' : 'idle', metric: `${ctx.receiptCount} 回执` },
  ];

  const displayedEvents = ctx.events.length;
  const hasTruncation = ctx.eventCount > displayedEvents;

  return (
    <div
      data-testid={`contextos-role-panel-${role.id}`}
      className={cn(
        'mt-3 rounded-xl border bg-bg-panel/40 p-3 backdrop-blur-sm transition-all duration-500',
        style.ring,
      )}
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className={cn('flex h-9 w-9 items-center justify-center rounded-lg bg-black/30 font-heading text-sm font-bold', style.text)}>
            {role.courtTitle.slice(0, 1)}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-text-main">{role.title}</span>
              <span className="text-[10px] text-text-dim">{role.courtTitle}</span>
              <span className={cn('rounded px-1.5 py-0.5 text-[9px] font-medium', style.ring, style.text)}>{style.label}</span>
            </div>
            <div className="text-[10px] text-text-dim">
              {ctx.lastEventAt !== null ? `最近活动 ${formatFreshness(ctx.lastEventAt)}` : '暂无观测事件'}
            </div>
          </div>
        </div>
        {ctx.totalTokens > 0 && (
          <div
            data-testid={`contextos-role-panel-tokens-${role.id}`}
            className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-black/30 px-2 py-1"
          >
            <Coins className="h-3.5 w-3.5 text-gold" />
            <span className="font-mono text-[11px] font-bold text-text-main">{ctx.totalTokens.toLocaleString()}</span>
            <span className="text-[9px] text-gold/70">tok</span>
          </div>
        )}
      </div>

      {/* 该角色的内部 ContextOS 管线 */}
      <div className="relative mb-3">
        <div className="flex items-center gap-1 overflow-x-auto pb-2">
          {pipeline.map((stage, index) => (
            <div key={stage.id} className="flex items-center gap-1">
              {index > 0 && <ArrowRight className="h-3 w-3 shrink-0 text-text-dim/40" />}
              <div
                data-testid={`contextos-role-panel-stage-${role.id}-${stage.id}`}
                data-state={stage.state}
                className="flex w-[92px] shrink-0 flex-col items-center gap-1 rounded-lg border border-white/[0.06] bg-white/[0.02] px-1.5 py-2 text-center"
              >
                <span className="text-[10px] font-semibold text-text-main">{stage.label}</span>
                <span className="text-[9px] text-text-dim">{stage.component}</span>
                <span className={cn('mt-0.5 rounded-full bg-black/30 px-1.5 py-0.5 font-mono text-[9px]', STATE_STYLES[stage.state].text)}>{stage.metric}</span>
              </div>
            </div>
          ))}
        </div>
        <div className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-bg-panel/70 to-transparent xl:hidden" aria-hidden />
      </div>

      {/* 统计卡 */}
      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        <RoleInternalStat label="事件" value={ctx.eventCount} />
        <RoleInternalStat label="投影" value={ctx.projectionCount} />
        <RoleInternalStat label="回执" value={ctx.receiptCount} />
        <RoleInternalStat label="调用" value={ctx.calls} />
        <RoleInternalStat label="提示 tok" value={contextOSFormat.tokens(ctx.promptTokens)} highlight={ctx.promptTokens > 0} />
        <RoleInternalStat label="输出 tok" value={contextOSFormat.tokens(ctx.completionTokens)} highlight={ctx.completionTokens > 0} />
      </div>

      {/* 最近事件 */}
      <div>
        <div className="mb-1.5 flex items-center justify-between text-[10px] uppercase tracking-wider text-text-dim">
          <span>最近事件</span>
          {hasTruncation && (
            <span className="font-mono normal-case text-text-dim">
              展示最近 {displayedEvents} 条 · 共 {ctx.eventCount} 条
            </span>
          )}
        </div>
        {ctx.events.length > 0 ? (
          <div className="space-y-1" aria-live="polite" aria-atomic="false">
            {ctx.events.map((event) => (
              <RoleInternalEventRow key={event.id} event={event} />
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-[11px] text-text-dim">
            该角色暂无实时观测事件
          </div>
        )}
      </div>
    </div>
  );
}

function BudgetBar({ label, tokens, ratio, colorClass }: { label: string; tokens: number; ratio: number; colorClass: string }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="truncate text-text-muted" title={label}>{label}</span>
        <span className="shrink-0 font-mono text-text-main">
          {contextOSFormat.tokens(tokens)}
          <span className="ml-1 text-text-dim">{Math.round(ratio * 100)}%</span>
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
        <div className={cn('h-full rounded-full transition-all duration-500', colorClass)} style={{ width: `${Math.max(2, Math.round(ratio * 100))}%` }} />
      </div>
    </div>
  );
}

function EventTypeDistribution({ slices, total }: { slices: EventTypeSlice[]; total: number }) {
  return (
    <div className="space-y-2.5">
      <div className="flex h-2 overflow-hidden rounded-full bg-white/5" role="img" aria-label="事件类型分布">
        {slices.map((slice) => (
          <div
            key={slice.key}
            className={cn('h-full', slice.colorClass)}
            style={{ width: `${Math.max(1, Math.round(slice.ratio * 100))}%` }}
            title={`${slice.label} · ${slice.count} (${Math.round(slice.ratio * 100)}%)`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1.5">
        {slices.map((slice) => (
          <div key={slice.key} className="flex items-center gap-1.5 text-[10px]">
            <span className={cn('h-2 w-2 shrink-0 rounded-sm', slice.colorClass)} />
            <span className="text-text-muted">{slice.label}</span>
            <span className="font-mono text-text-main">{slice.count}</span>
            <span className="text-text-dim">{Math.round(slice.ratio * 100)}%</span>
          </div>
        ))}
      </div>
      <div className="text-right font-mono text-[9px] text-text-dim">基于最近 {total} 条观测事件</div>
    </div>
  );
}

function DecisionTable({ rows }: { rows: DecisionRow[] }) {
  const toneClass: Record<DecisionRow['tone'], string> = {
    info: 'text-text-muted',
    success: 'text-status-success',
    warning: 'text-status-warning',
    error: 'text-status-error',
  };
  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-white/10 px-3 py-6 text-center text-[11px] text-text-dim">
        暂无决策 / 回执记录 — 启动 PM 或 Director 后将实时流入
      </div>
    );
  }
  return (
    <div className="space-y-1">
      {rows.map((row) => (
        <div key={row.id} className="grid grid-cols-[64px_72px_1fr] items-start gap-2 rounded-md px-2 py-1.5 text-[11px] hover:bg-white/[0.03]">
          <span className="font-mono text-[10px] text-text-dim">{row.time}</span>
          <span className={cn('truncate font-medium', toneClass[row.tone])} title={`${row.actor} · ${row.kind}`}>
            {row.actor}
          </span>
          <div className="min-w-0">
            <span className="block truncate text-text-muted" title={row.summary}>{row.summary || row.kind}</span>
            {(row.source === 'telemetry') && (row.tokens || row.latencyMs || row.receipt) && (
              <div className="mt-0.5 flex flex-wrap items-center gap-1">
                {row.kind && (
                  <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-dim">{row.kind}</span>
                )}
                {typeof row.tokens === 'number' && row.tokens > 0 && (
                  <span className="rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary">
                    {contextOSFormat.tokens(row.tokens)} tok
                  </span>
                )}
                {typeof row.latencyMs === 'number' && row.latencyMs > 0 && (
                  <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted">{row.latencyMs}ms</span>
                )}
                {row.receipt && (
                  <span className="rounded bg-gold/10 px-1 font-mono text-[9px] text-gold">快照</span>
                )}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function SectionCard({ title, subtitle, icon: Icon, children, className, action }: {
  title: string;
  subtitle?: string;
  icon: LucideIcon;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  return (
    <section className={cn('flex flex-col rounded-xl border border-white/[0.07] bg-bg-panel/40 backdrop-blur-sm', className)}>
      <header className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className="h-3.5 w-3.5 shrink-0 text-accent-secondary" />
          <span className="truncate text-xs font-semibold text-text-main">{title}</span>
          {subtitle && <span className="truncate text-[10px] text-text-dim">{subtitle}</span>}
        </div>
        {action}
      </header>
      <BenchStatusStrip />
      <div className="min-h-0 flex-1 p-3">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------

export function ContextOSWorkspace({
  workspace,
  onBackToMain,
  onRefresh,
  live,
  reconnecting = false,
  usageStats,
  currentPhase,
  pmRunning,
  directorRunning,
  llmRuntimeState,
  dialogueEvents,
  executionLogs,
  llmStreamEvents,
  processStreamEvents,
  snapshot,
  qualityGate,
}: ContextOSWorkspaceProps) {
  // 真实 ContextOS 遥测：直接派生自 useRuntime 经 WebSocket(/v2/ws/runtime) 实时推送的运行时流。
  // 完全无轮询——这些 props 随 WS 事件到达即变化，组件随之重渲染。
  const telemetry = useMemo(
    () => buildTelemetryFromStream(llmStreamEvents, executionLogs, processStreamEvents),
    [llmStreamEvents, executionLogs, processStreamEvents],
  );

  const model = useMemo(
    () => buildContextOSModel({
      usageStats,
      dialogueEvents,
      executionLogs,
      snapshot,
      llmRuntimeState,
      currentPhase,
      pmRunning,
      directorRunning,
      telemetry,
    }),
    [usageStats, dialogueEvents, executionLogs, snapshot, llmRuntimeState, currentPhase, pmRunning, directorRunning, telemetry],
  );

  const [activeRole, setActiveRole] = useState<string | null>(null);

  const wsTone = live ? 'success' : reconnecting ? 'warning' : 'error';
  const wsLabel = live ? 'WS LIVE' : reconnecting ? 'WS RECONNECT' : 'WS OFFLINE';
  const phaseLabel = (currentPhase || 'idle').trim() || 'idle';
  const gatePassed = qualityGate?.passed;
  // 观测到活动 = PM/Director 运行中 或 真实遥测有内容。
  const observed = model.running || model.telemetryActive;
  // 「真正有数据」= 真实遥测有内容；此时不再视为空闲水印。
  const idle = !model.telemetryActive && (model.dataIdle || (!live && !model.running));
  const pipelineLive = observed && live;

  // 新鲜度以"最近一条 WS 推送事件"的时间为准（而非轮询时刻），避免陈旧数据被误读为实时。
  const lastEventEpoch = model.lastTelemetryEpoch;
  const telemetryAgeMs = lastEventEpoch ? Date.now() - lastEventEpoch : null;
  const telemetryFresh = telemetryAgeMs !== null && telemetryAgeMs < 30_000; // 30s 内视为"实时"
  const freshnessLabel = lastEventEpoch ? formatFreshness(lastEventEpoch) : null;

  const filteredDecisions = useMemo(
    () => model.decisions.filter((row) => decisionMatchesRole(row.actor, activeRole)),
    [model.decisions, activeRole],
  );

  const toggleRole = (roleId: string) => setActiveRole((prev) => (prev === roleId ? null : roleId));

  // WS 是推送模型——遥测随事件自动更新；刷新按钮仅触发外层（重连/状态拉取）。
  const handleRefresh = () => {
    onRefresh?.();
  };

  return (
    <div data-testid="contextos-workspace" className="flex h-full flex-col overflow-hidden bg-bg text-text-main">
      {/* Header */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-accent-secondary/20 bg-bg-panel/60 px-4 backdrop-blur">
        <div className="flex min-w-0 items-center gap-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBackToMain}
            data-testid="contextos-back"
            className="text-text-muted hover:bg-white/5 hover:text-text-main"
          >
            <ChevronLeft className="mr-1 h-4 w-4" />
            返回
          </Button>
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-secondary/15 text-accent-secondary ring-1 ring-accent-secondary/30">
              <Network className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <h1 className="font-heading text-sm font-bold text-text-main">ContextOS 实时视图</h1>
              <p className="truncate text-[10px] uppercase tracking-wider text-accent-secondary/70" title={workspace}>
                上下文操作系统 · {workspaceLabel(workspace, '未选定工作区')}
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <StatusBadge color={model.running ? 'success' : 'default'} variant="dot" pulse={model.running}>
            <span className="font-mono text-[10px]">阶段 {phaseLabel}</span>
          </StatusBadge>
          {model.iteration !== null && (
            <span
              className="rounded-md border border-white/10 bg-black/30 px-2 py-1 font-mono text-[10px] text-text-muted"
              title="PM 迭代轮次（来自运行快照）"
            >
              迭代 {model.iteration}
            </span>
          )}
          {gatePassed !== undefined && (
            <StatusBadge color={gatePassed ? 'success' : 'warning'} variant="soft">
              <span className="font-mono text-[10px]">质量门 {gatePassed ? 'PASS' : 'HOLD'}</span>
            </StatusBadge>
          )}
          {/* 实时活动（WebSocket 推送）：LLM 调用次数 + 最近时延——这是仪表盘的实时主指标。 */}
          <div
            className="flex items-center gap-1.5 rounded-lg border border-accent-secondary/20 bg-black/30 px-2.5 py-1"
            title="实时 LLM 活动（WebSocket /v2/ws/runtime 推送）：调用次数 · 最近时延"
            data-testid="contextos-activity-chip"
          >
            <Activity className="h-3.5 w-3.5 text-accent-secondary" />
            <span className="font-mono text-[11px] font-bold text-text-main">{model.calls.toLocaleString()}</span>
            <span className="text-[9px] font-bold uppercase tracking-wider text-accent-secondary/70">调用</span>
            {model.realLatencyMs !== null && (
              <span className="font-mono text-[10px] text-text-muted">· {model.realLatencyMs}ms</span>
            )}
          </div>
          {/* 真实 token：journal `llm` 通道 raw.data 携带，实时送达；无实时 token 时退回用量统计并标注。 */}
          {model.totalTokens > 0 && (
            <div
              className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-black/30 px-2.5 py-1"
              title={model.tokensRealtime
                ? '累计 token（来自 journal llm 通道的真实 usage，WebSocket 实时推送）'
                : '累计 token（来自用量统计通道，非实时）'}
            >
              <Coins className="h-3.5 w-3.5 text-gold" />
              <span className="font-mono text-[11px] font-bold text-text-main">{model.totalTokens.toLocaleString()}</span>
              <span className="text-[9px] font-bold uppercase tracking-wider text-gold/70">
                {model.tokensRealtime ? 'tok · 实时' : 'tok'}
              </span>
            </div>
          )}
          {/* 真实遥测实时性指示：以最近一条 WS 推送事件的时间为准。新鲜=实时(脉冲)，陈旧=不脉冲。 */}
          <StatusBadge
            color={model.telemetryActive ? (telemetryFresh ? 'success' : 'warning') : 'default'}
            variant="dot"
            pulse={model.telemetryActive && telemetryFresh}
          >
            <span
              className="font-mono text-[10px]"
              title="ContextOS 实时遥测（WebSocket /v2/ws/runtime 推送），时间为最近一条事件"
              data-testid="contextos-telemetry-freshness"
            >
              {model.telemetryActive
                ? `${telemetryFresh ? '实时遥测' : '遥测'}${freshnessLabel ? ` · ${freshnessLabel}` : ''}`
                : '遥测待命'}
            </span>
          </StatusBadge>
          <StatusBadge color={wsTone} variant="dot" pulse={reconnecting}>
            <span className="font-mono text-[10px]">{wsLabel}</span>
          </StatusBadge>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            data-testid="contextos-refresh"
            title="刷新运行状态与遥测"
            aria-label="刷新运行状态与遥测"
            className="border-accent-secondary/30 text-accent-secondary hover:bg-accent-secondary/10"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      {/* Role filter tabs (cross-filters the decision stream) */}
      <div className="flex shrink-0 items-center gap-2 border-b border-white/[0.05] bg-bg-panel/30 px-4 py-1.5">
        <span className="text-[10px] uppercase tracking-wider text-text-dim">角色视角</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            data-testid="contextos-roletab-all"
            aria-pressed={activeRole === null}
            onClick={() => setActiveRole(null)}
            className={cn(
              'rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors',
              activeRole === null ? 'bg-accent-secondary/15 text-accent-secondary' : 'text-text-muted hover:text-text-main hover:bg-white/5',
            )}
          >
            全部
          </button>
          {model.roles.map((role) => (
            <button
              key={role.id}
              type="button"
              data-testid={`contextos-roletab-${role.id}`}
              aria-pressed={activeRole === role.id}
              onClick={() => toggleRole(role.id)}
              className={cn(
                'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors',
                activeRole === role.id ? 'bg-accent-secondary/15 text-accent-secondary' : 'text-text-muted hover:text-text-main hover:bg-white/5',
              )}
            >
              <span className={cn('h-1.5 w-1.5 rounded-full', STATE_STYLES[role.state].dot)} />
              {role.title}
            </button>
          ))}
        </div>
        {activeRole && (
          <span className="ml-auto text-[10px] text-text-dim">
            已过滤决策流 · {filteredDecisions.length} 条
          </span>
        )}
      </div>

      {/* Main grid */}
      <main className="min-h-0 flex-1 overflow-auto p-4">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[260px_minmax(0,1fr)_300px]">
          {/* LEFT — component health */}
          <div className="flex flex-col gap-4">
            <SectionCard title="组件健康" subtitle="System Components" icon={Boxes}>
              <div className="space-y-2">
                {model.components.map((health) => (
                  <ComponentRow key={health.id} health={health} />
                ))}
              </div>
            </SectionCard>
          </div>

          {/* CENTER — pipeline hero + roles + decision log */}
          <div className="flex flex-col gap-4">
            <SectionCard
              title="系统流转与数据流图"
              subtitle="实时上下文装配管线"
              icon={Network}
              action={
                <StatusBadge color={observed ? 'success' : 'default'} variant="dot" pulse={observed}>
                  <span className="text-[10px]">{observed ? '装配中' : '空闲'}</span>
                </StatusBadge>
              }
            >
              <div className="relative">
                <div className={cn('flex items-center gap-1 overflow-x-auto pb-2 transition-opacity', idle && 'opacity-40')}>
                  {model.pipeline.map((stage, index) => (
                    <div key={stage.id} className="flex items-center gap-1">
                      {index > 0 && <FlowArrow active={pipelineLive} />}
                      <PipelineNode stage={stage} />
                    </div>
                  ))}
                  <FlowArrow active={pipelineLive} />
                  <div
                    className={cn(
                      'flex w-[112px] shrink-0 flex-col items-center gap-1.5 rounded-xl border px-2 py-3 text-center transition-all duration-500',
                      model.errorCount > 0 ? 'border-status-error/50 bg-status-error/10' : 'border-gold/30 bg-gold/5',
                    )}
                    title="Context Snapshot + Telemetry — 落盘上下文快照(context.snapshot)与遥测反馈闭环"
                  >
                    <div className={cn('relative flex h-9 w-9 items-center justify-center rounded-lg bg-black/30', model.errorCount > 0 ? 'text-status-error' : 'text-gold')}>
                      <ShieldCheck className="h-4 w-4" />
                      {model.errorCount > 0 && (
                        <span className="absolute right-1.5 top-1.5 flex h-1.5 w-1.5">
                          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-status-error opacity-75 motion-reduce:animate-none" />
                          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-status-error" />
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] font-semibold leading-tight text-text-main">Receipt</div>
                    <div className="text-[9px] uppercase tracking-wide text-text-dim">遥测反馈</div>
                    <div className={cn('mt-0.5 rounded-full bg-black/30 px-1.5 py-0.5 font-mono text-[9px]', model.errorCount > 0 ? 'text-status-error' : 'text-gold')}>
                      {model.errorCount > 0
                        ? `${model.errorCount} 错误`
                        : model.receiptCount > 0
                          ? `${model.receiptCount} 快照`
                          : `${model.calls} 调用`}
                    </div>
                  </div>
                </div>
                {idle && (
                  <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <span className="rounded-full border border-white/10 bg-black/40 px-3 py-1 font-heading text-xs tracking-widest text-text-dim backdrop-blur-sm">
                      空闲 · 等待运行
                    </span>
                  </div>
                )}
                {/* 窄屏下提示右侧仍有节点（Receipt 反馈闭环）可横向滚动查看 */}
                <div className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-bg-panel/70 to-transparent xl:hidden" aria-hidden />
              </div>
              <div className="mt-1 flex items-center gap-2 border-t border-white/[0.06] pt-2 text-[10px] text-text-dim">
                <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono">投影排序(含预算规划) → 角色信号 → 装配 → 压缩兜底</span>
                <span className="text-text-dim/60">·</span>
                <span>自适应权重: route / confidence / recency / dialog_act / role</span>
              </div>
            </SectionCard>

            <SectionCard
              title="角色信号面"
              subtitle={`RoleSignalPlane · ${model.roles.length} 主角色`}
              icon={Boxes}
              action={<span className="text-[10px] text-text-dim">点击角色查看内部 ContextOS 状态</span>}
            >
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                {model.roles.map((role) => (
                  <RoleHex
                    key={role.id}
                    role={role}
                    selected={activeRole === role.id}
                    onSelect={() => toggleRole(role.id)}
                  />
                ))}
              </div>
              {activeRole && (
                <RoleInternalPanel role={model.roles.find((r) => r.id === activeRole)!} />
              )}
              <p className="mt-2.5 text-[9px] leading-relaxed text-text-dim">
                角色卡按 WebSocket 实时事件量呈现活跃度；点击角色展开其内部 ContextOS 视图（TruthLog / WorkingMem / ProjectionEngine / ReceiptStore）。产生过带 usage 调用的角色（journal llm 通道）以真实 token 归因，其余以事件数呈现。
              </p>
            </SectionCard>

            <SectionCard
              title="Decision Log / Receipts"
              subtitle={
                model.telemetryActive
                  ? activeRole
                    ? `实时事件流 · 仅 ${activeRole.toUpperCase()}`
                    : '实时事件流 · WebSocket 推送'
                  : activeRole
                    ? `决策与回执流 · 仅 ${activeRole.toUpperCase()}`
                    : '决策与回执流'
              }
              icon={Activity}
              className="min-h-[220px]"
              action={
                model.telemetryActive ? (
                  <span
                    className="rounded-full border border-accent-secondary/20 bg-accent-secondary/[0.08] px-2 py-0.5 font-mono text-[9px] text-accent-secondary"
                    data-testid="contextos-telemetry-source"
                    title="决策流来自 ContextOS 实时遥测（WebSocket /v2/ws/runtime 推送）"
                  >
                    REAL · {model.calls} 调用 · {model.projectionCount} 投影
                    {model.telemetryWindowed ? ' · 最近窗口' : ''}
                  </span>
                ) : undefined
              }
            >
              <DecisionTable rows={filteredDecisions} />
            </SectionCard>
          </div>

          {/* RIGHT — context budget */}
          <div className="flex flex-col gap-4">
            <SectionCard title="上下文预算" subtitle="Context Budget" icon={Coins}>
              <div className="space-y-4">
                <div>
                  <div className="flex flex-wrap items-baseline gap-2">
                    {model.totalTokens > 0 ? (
                      <>
                        <span className="font-heading text-3xl font-bold text-text-main">{model.totalTokens.toLocaleString()}</span>
                        <span className="text-[11px] text-text-dim">
                          {model.tokensRealtime ? 'tokens · 实时 (journal llm)' : 'tokens · 用量统计 (非实时)'}
                        </span>
                        {model.estimatedCalls > 0 && (
                          <span
                            className="rounded bg-status-warning/10 px-1 py-0.5 text-[9px] text-status-warning"
                            data-testid="contextos-estimated-marker"
                            title={`其中 ${model.estimatedCalls} 次调用的 token 为字符估算（用量统计通道）`}
                          >
                            含估算 {model.estimatedCalls}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-[12px] leading-relaxed text-text-dim" data-testid="contextos-tokens-unavailable">
                        等待首次 LLM 调用 · 实时 token 随 journal 流到达
                      </span>
                    )}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-dim">
                    <span>{model.calls} 次调用（实时）</span>
                    {model.realLatencyMs !== null && (
                      <>
                        <span>·</span>
                        <span>{model.realLatencyMs}ms 最近时延</span>
                      </>
                    )}
                    {model.avgPerCall > 0 && (
                      <>
                        <span>·</span>
                        <span>{contextOSFormat.tokens(model.avgPerCall)} tok / 次</span>
                      </>
                    )}
                    {model.telemetryWindowed && (
                      <>
                        <span>·</span>
                        <span
                          className="text-status-warning/80"
                          title="某条 WS 实时流已达环形缓冲上限，统计为最近窗口而非全程累计"
                        >
                          最近窗口
                        </span>
                      </>
                    )}
                  </div>
                </div>

                {model.totalTokens > 0 ? (
                  <div className="space-y-2.5">
                    {model.budget.map((slice) => (
                      <BudgetBar key={slice.key} label={slice.label} tokens={slice.tokens} ratio={slice.ratio} colorClass={slice.colorClass} />
                    ))}
                  </div>
                ) : (
                  <div className="rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-[11px] text-text-dim">
                    等待首次调用 · 暂无 token 用量
                  </div>
                )}

                {/* Context window occupancy (estimated) */}
                <div className="space-y-1 border-t border-white/[0.06] pt-3">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="flex items-center gap-1 text-text-muted">
                      <Gauge className="h-3 w-3" />
                      上下文窗口占用
                      <span className="rounded bg-white/5 px-1 text-[9px] text-text-dim">估算</span>
                    </span>
                    <span className="font-mono text-text-main">{Math.round(model.windowOccupancy * 100)}%</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-white/5">
                    <div
                      className={cn(
                        'h-full rounded-full transition-all duration-500',
                        model.windowOccupancy > 0.85 ? 'bg-status-error' : model.windowOccupancy > 0.6 ? 'bg-status-warning' : 'bg-accent-secondary',
                      )}
                      style={{ width: `${Math.max(2, Math.round(model.windowOccupancy * 100))}%` }}
                    />
                  </div>
                  <div className="text-right font-mono text-[9px] text-text-dim">
                    ~{contextOSFormat.tokens(model.windowOccupancyTokens)} / {contextOSFormat.tokens(NOMINAL_CONTEXT_WINDOW)} 窗口
                  </div>
                </div>
              </div>
            </SectionCard>

            <SectionCard title="按模式分布" subtitle="usageStats.by_mode" icon={GitBranch}>
              {model.byModeSlices.length > 0 ? (
                <div className="space-y-2.5">
                  {model.byModeSlices.map((slice) => (
                    <BudgetBar key={slice.key} label={slice.label} tokens={slice.tokens} ratio={slice.ratio} colorClass={slice.colorClass} />
                  ))}
                </div>
              ) : (
                <div className="rounded-lg border border-dashed border-white/10 px-3 py-6 text-center text-[11px] text-text-dim">
                  暂无分模式 token 统计
                </div>
              )}
            </SectionCard>

            {model.eventTypes.length > 0 && (
              <SectionCard title="事件类型分布" subtitle="Event Types · 真实观测" icon={Activity}>
                <div data-testid="contextos-event-types">
                  <EventTypeDistribution slices={model.eventTypes} total={model.eventTypesTotal} />
                </div>
              </SectionCard>
            )}
          </div>
        </div>

        {/* Footer — Outcome Feedback Loop */}
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-white/[0.07] bg-bg-panel/40 px-4 py-3 backdrop-blur-sm">
          <div className="flex items-center gap-2">
            <Activity className={cn('h-4 w-4', observed ? 'text-accent-secondary' : 'text-text-dim')} />
            <span className="text-xs font-semibold text-text-main">Outcome Feedback Loop</span>
            <span className="text-[10px] text-text-dim">结果反馈闭环</span>
          </div>
          <div className="h-4 w-px bg-white/10" />
          <div className="flex flex-wrap items-center gap-2">
            {model.policies.map((policy) => (
              <span
                key={policy}
                className="rounded-full border border-accent-secondary/20 bg-accent-secondary/[0.08] px-2 py-0.5 font-mono text-[10px] text-accent-secondary"
              >
                {policy}
              </span>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-4 font-mono text-[10px] text-text-dim">
            <span>提示 {contextOSFormat.tokens(model.promptTokens)}</span>
            <span>输出 {contextOSFormat.tokens(model.completionTokens)}</span>
            {model.realLatencyMs !== null && (
              <span title="最近真实调用时延">时延 {model.realLatencyMs}ms</span>
            )}
            <StatusBadge color={badgeColorForState(observed ? 'active' : 'idle')} variant="dot">
              <span>{observed ? 'LIVE' : 'IDLE'}</span>
            </StatusBadge>
          </div>
        </div>
      </main>
    </div>
  );
}
