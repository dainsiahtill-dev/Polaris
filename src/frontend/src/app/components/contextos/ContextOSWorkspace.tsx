/**
 * ContextOS 实时视图 (ContextOS Real-time Dashboard)
 *
 * 可视化 Polaris 的「上下文操作系统」实时数据流：从用户请求进入，经 TruthLog 真值流 →
 * WorkingMemory 活动窗口 → ProjectionEngine 自适应排序投影（内部含预算规划）→ RoleSignalPlane
 * 角色信号 → project() 消息装配 → CompressionEngine 装配后预算压缩兜底 → LLM 调用，再回流到
 * Receipt / Telemetry 回执遥测的反馈闭环（顺序忠实于后端 gateway.py 真实装配流）。
 *
 * 数据源 = Polaris 既有 WS 实时框架：emit_event/emit_llm_event → MessageBus →
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
  X,
  type LucideIcon,
} from 'lucide-react';

import {
  Button } from '@/app/components/ui/button';
import { StatusBadge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';
import type { UsageStats } from '@/app/components/UsageHUD';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';
import type { LlmRuntimeGateState } from '@/app/hooks/useLlmRuntimeGate';
import type { SnapshotPayload } from '@/app/types/appContracts';
import type { QualityGateData } from '@/app/components/pm';
import type { ControlPlaneProjection } from '@/services/controlPlane';

import {
  buildContextOSModel,
  contextOSFormat,
  decisionMatchesRole,
  safeText,
  type ContextOSModel,
  type DecisionRow,
  type EventTypeSlice,
  type PipelineStage,
  type PipelineState,
  type RoleCard,
  type RoleBindingBudget,
  type RoleInternalContext,
  type WorkerCard,
} from './contextOSData';
import { buildTelemetryFromStream, type ContextOSEvent } from './contextOSTelemetry';
import { ContextViewerModal } from './ContextViewerModal';
import { ContextStoreStatsPanel } from './ContextStoreStatsPanel';

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
  controlPlaneProjection?: ControlPlaneProjection;
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
  receipt: ShieldCheck,
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

type FinalRequestCoverageChip = {
  id: string;
  label: string;
  key: string;
  present: boolean | null;
  title: string;
};

const AGI_FINAL_REQUEST_COVERAGE: Array<{ id: string; label: string; key: string }> = [
  {
    id: 'resident-agi-decision-trace',
    label: 'AGI 决策交接',
    key: 'has_resident_agi_decision_trace',
  },
  {
    id: 'resident-agi-capability-surface',
    label: 'AGI 能力面',
    key: 'has_resident_agi_capability_surface',
  },
];

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

function controlPlaneProjectionLabel(projection: ControlPlaneProjection): string {
  if (!projection.available) {
    return projection.status === 'pending' ? '账本待生成' : '账本缺失';
  }
  return projection.ok ? '账本一致' : '账本异常';
}

function controlPlaneProjectionSummary(projection: ControlPlaneProjection): string {
  if (!projection.available) {
    return projection.detail || 'Run Ledger projection 尚不可用';
  }
  return `${projection.projected}/${projection.total} 投影 · ${projection.failed} 异常`;
}

function controlPlaneSourceSummary(projection: ControlPlaneProjection): string {
  const source = `source=${projection.source}`;
  if (!projection.compat_ledgers_included) return source;
  return `${source} · compat=factory-ledger`;
}

function controlPlaneGatePassed(projection: ControlPlaneProjection | undefined): boolean | undefined {
  if (!projection) return undefined;
  if (!projection.available) return false;
  if (projection.total <= 0 && projection.projects.length === 0) return false;
  if (!projection.ok || projection.failed > 0) return false;
  return projection.projects.every((project) => project.ok && project.failed_gate_count === 0);
}

function evidencePolicyLabel(projection: ControlPlaneProjection): string {
  const policy = projection.evidence_policy;
  const enabled = policy?.enabled_modalities ?? [];
  if (!policy || (enabled.length === 0 && policy.required_modalities.length === 0)) {
    return '可选验证未启用';
  }
  return policy.ok ? '可选验证已启用' : '可选验证缺证据';
}

function evidencePolicySummary(projection: ControlPlaneProjection): string {
  const policy = projection.evidence_policy;
  const enabled = policy?.enabled_modalities ?? [];
  if (!policy || (enabled.length === 0 && policy.required_modalities.length === 0)) {
    return 'browser / visual / domain verifier 未作为硬门禁';
  }
  if (policy.required_modalities.length === 0) {
    return `可选启用 ${enabled.join(', ')} · 未作为硬门禁`;
  }
  const required = policy.required_modalities.join(', ');
  if (policy.missing_required_modalities.length > 0) {
    return `启用 ${required} · 缺 ${policy.missing_required_modalities.join(', ')}`;
  }
  return `启用 ${required}`;
}

function readAuditCoverage(audit: Record<string, unknown> | null): Record<string, unknown> {
  if (!audit) return {};
  const coverage = audit['coverage'];
  return typeof coverage === 'object' && coverage !== null ? coverage as Record<string, unknown> : {};
}

function readAuditMissingCoverage(audit: Record<string, unknown> | null): Set<string> {
  if (!audit) return new Set();
  const contextQuality = audit['context_quality'];
  if (typeof contextQuality !== 'object' || contextQuality === null) return new Set();
  const missing = (contextQuality as Record<string, unknown>)['missing_coverage'];
  if (!Array.isArray(missing)) return new Set();
  return new Set(missing.filter((item): item is string => typeof item === 'string'));
}

function auditFlagValue(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const normalized = value.toLowerCase();
    if (normalized === 'true') return true;
    if (normalized === 'false') return false;
  }
  return null;
}

function finalRequestAgiCoverageChips(audit: Record<string, unknown> | null): FinalRequestCoverageChip[] {
  if (!audit) return [];
  const coverage = readAuditCoverage(audit);
  const missingCoverage = readAuditMissingCoverage(audit);
  return AGI_FINAL_REQUEST_COVERAGE.flatMap((item) => {
    const hasSignal = Object.prototype.hasOwnProperty.call(coverage, item.key) || missingCoverage.has(item.key);
    if (!hasSignal) return [];
    const rawValue = coverage[item.key];
    const present = auditFlagValue(rawValue);
    const resolved = present ?? (missingCoverage.has(item.key) ? false : null);
    return [{
      ...item,
      present: resolved,
      title: `${item.key}=${String(rawValue ?? 'n/a')} missing=${String(missingCoverage.has(item.key))}`,
    }];
  });
}

function FinalRequestAgiCoverageBadges({
  audit,
  className,
  compact = false,
}: {
  audit: Record<string, unknown> | null;
  className?: string;
  compact?: boolean;
}) {
  const chips = finalRequestAgiCoverageChips(audit);
  if (chips.length === 0) return null;
  return (
    <div
      className={cn('flex flex-wrap items-center gap-1', className)}
      data-testid="contextos-final-request-agi-coverage"
    >
      {!compact && (
        <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[9px] text-text-dim">
          最终请求 AGI 覆盖
        </span>
      )}
      {chips.map((chip) => (
        <span
          key={chip.id}
          data-testid={`contextos-final-request-agi-${chip.id}`}
          className={cn(
            'rounded border px-1.5 py-0.5 font-mono text-[9px]',
            chip.present === true
              ? 'border-accent-secondary/20 bg-accent-secondary/10 text-accent-secondary'
              : chip.present === false
                ? 'border-status-error/20 bg-status-error/10 text-status-error'
                : 'border-white/[0.08] bg-white/5 text-text-dim',
          )}
          title={chip.title}
        >
          {chip.label}: {chip.present === true ? '已进入' : chip.present === false ? '缺失' : '未知'}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 子组件
// ---------------------------------------------------------------------------

function PipelineNode({
  stage,
  selected = false,
  onSelect,
}: {
  stage: PipelineStage;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const Icon = STAGE_ICONS[stage.id] ?? Activity;
  const style = STATE_STYLES[stage.state];
  return (
    <button
      type="button"
      data-testid={`contextos-stage-${stage.id}`}
      data-state={stage.state}
      data-selected={selected}
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        'relative flex w-[104px] shrink-0 flex-col items-center gap-1 rounded-xl border px-2 py-2.5 text-center transition-all duration-500 hover:-translate-y-0.5 hover:border-accent-secondary/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-secondary/70 active:translate-y-0',
        style.ring,
        selected && 'border-accent-secondary/70 ring-2 ring-accent-secondary/45',
      )}
      title={`${stage.component} — ${stage.hint}`}
    >
      <div className={cn('flex h-8 w-8 items-center justify-center rounded-lg bg-black/30', style.text)}>
        <Icon className="h-4 w-4" />
        {stage.state === 'active' && (
          <span className="absolute right-1.5 top-1.5 flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-secondary opacity-75 motion-reduce:animate-none" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent-secondary" />
          </span>
        )}
      </div>
      <div className="text-[11px] font-semibold leading-tight text-text-main">{stage.label}</div>
      <div className={cn('mt-0.5 rounded-full bg-black/30 px-1.5 py-0.5 font-mono text-[9px]', style.text)}>
        {stage.metric}
      </div>
    </button>
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

type ContextOSTelemetryView = ReturnType<typeof buildTelemetryFromStream>;

function DetailStat({
  label,
  value,
  sub,
  tone = 'idle',
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  tone?: PipelineState;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[10px] font-semibold uppercase tracking-wider text-text-dim" title={label}>
          {label}
        </span>
        <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', STATE_STYLES[tone].dot)} />
      </div>
      <div className={cn('mt-1 truncate font-mono text-lg font-bold', STATE_STYLES[tone].text)} title={String(value)}>
        {value}
      </div>
      {sub && <div className="mt-1 truncate text-[10px] text-text-dim" title={sub}>{sub}</div>}
    </div>
  );
}

function DetailBlock({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-xs font-semibold text-text-main" title={title}>{title}</div>
          {subtitle && <div className="truncate text-[10px] text-text-dim" title={subtitle}>{subtitle}</div>}
        </div>
      </div>
      {children}
    </div>
  );
}

function DetailEmpty({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-white/10 px-3 py-5 text-center">
      <Activity className="h-4 w-4 text-text-dim/30" />
      <div className="text-[11px] text-text-dim">{label}</div>
    </div>
  );
}

function DetailEventList({ events, emptyLabel }: { events: ContextOSEvent[]; emptyLabel: string }) {
  if (events.length === 0) return <DetailEmpty label={emptyLabel} />;
  return (
    <div className="space-y-1">
      {events.map((event) => {
        const tone: PipelineState = event.category === 'error' ? 'blocked' : event.isProjection || event.hasUsage ? 'active' : 'idle';
        const summary = safeText(event.summary) || safeText(event.kind) || '事件';
        return (
          <div
            key={event.id}
            className="grid grid-cols-[58px_72px_1fr] gap-2 rounded-md px-2 py-1.5 text-[10px] hover:bg-white/[0.04]"
          >
            <span className="font-mono text-text-dim">{contextOSFormat.clock(event.ts)}</span>
            <span className={cn('truncate font-medium', STATE_STYLES[tone].text)} title={event.actor}>
              {safeText(event.actor)}
            </span>
            <div className="min-w-0">
              <div className="truncate text-text-main" title={summary}>{summary}</div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1">
                <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-dim">{safeText(event.kind) || event.category}</span>
                {event.totalTokens > 0 && (
                  <span className="rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary">
                    {contextOSFormat.tokens(event.totalTokens)} tok
                  </span>
                )}
                {event.contextTokens !== null && event.contextTokens > 0 && (
                  <span className="rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary">
                    ctx {contextOSFormat.tokens(event.contextTokens)}
                  </span>
                )}
                {event.durationMs !== null && event.durationMs > 0 && (
                  <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted">{event.durationMs}ms</span>
                )}
                {event.contextSnapshotRef && (
                  <span className="rounded bg-gold/10 px-1 font-mono text-[9px] text-gold">snapshot</span>
                )}
                {event.contextSnapshotDegraded && (
                  <span className="rounded bg-status-warning/10 px-1 font-mono text-[9px] text-status-warning">degraded</span>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function LlmCallCard({ event }: { event: ContextOSEvent }) {
  const summary = safeText(event.summary) || safeText(event.kind) || 'LLM 调用';
  const modelLabel = event.model || '未记录模型';
  const providerLabel = event.providerName || event.providerId || 'provider unknown';
  const hasContext = event.contextTokens !== null && event.contextTokens > 0;
  const hasSnapshot = Boolean(event.contextSnapshotRef);
  return (
    <div className="rounded-xl border border-accent-secondary/15 bg-gradient-to-br from-accent-secondary/[0.08] via-white/[0.025] to-black/20 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-md border border-accent-secondary/20 bg-accent-secondary/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-accent-secondary">
              {safeText(event.actor)}
            </span>
            <span className="truncate text-xs font-semibold text-text-main" title={modelLabel}>{modelLabel}</span>
          </div>
          <div className="mt-1 truncate font-mono text-[10px] text-text-dim" title={providerLabel}>{providerLabel}</div>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-[10px] text-text-dim">{contextOSFormat.clock(event.ts)}</div>
          <div className={cn('mt-1 rounded px-1.5 py-0.5 font-mono text-[9px]', event.category === 'error' ? 'bg-status-error/10 text-status-error' : 'bg-accent-secondary/10 text-accent-secondary')}>
            {event.category === 'error' ? 'failed' : 'completed'}
          </div>
        </div>
      </div>
      <div className="mt-3 rounded-lg border border-white/[0.06] bg-black/25 px-3 py-2 text-[11px] text-text-muted" title={summary}>
        <span className="line-clamp-2">{summary}</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <DetailStat label="Prompt" value={contextOSFormat.tokens(event.promptTokens)} tone={event.promptTokens > 0 ? 'active' : 'idle'} sub="输入 token" />
        <DetailStat label="Output" value={contextOSFormat.tokens(event.completionTokens)} tone={event.completionTokens > 0 ? 'active' : 'idle'} sub="输出 token" />
        <DetailStat label="Context" value={hasContext ? contextOSFormat.tokens(event.contextTokens!) : 'n/a'} tone={hasContext ? 'active' : 'idle'} sub="最终请求上下文" />
        <DetailStat label="Latency" value={event.durationMs !== null ? `${event.durationMs}ms` : 'n/a'} tone={event.durationMs !== null ? 'active' : 'idle'} sub={hasSnapshot ? 'snapshot linked' : 'no snapshot'} />
      </div>
      <FinalRequestAgiCoverageBadges audit={event.finalRequestContextAudit} className="mt-3" />
    </div>
  );
}

function LlmCallDeck({ events }: { events: ContextOSEvent[] }) {
  if (events.length === 0) return <DetailEmpty label="暂无 LLM 调用事件" />;
  return (
    <div className="space-y-2.5">
      {events.map((event) => <LlmCallCard key={event.id} event={event} />)}
    </div>
  );
}

function ProjectionEvidenceDeck({ events }: { events: ContextOSEvent[] }) {
  if (events.length === 0) return <DetailEmpty label="暂无投影事件" />;
  return (
    <div className="grid gap-2">
      {events.map((event) => {
        const source = event.finalRequestTokenEstimate !== null
          ? 'final request audit'
          : event.contextItems !== null
            ? 'context.build'
            : event.projectionKey
              ? 'projection key'
              : 'text signal';
        return (
          <div key={event.id} className="rounded-xl border border-accent-secondary/15 bg-black/20 p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-xs font-semibold text-text-main" title={safeText(event.summary)}>
                  {safeText(event.summary) || 'Context projection'}
                </div>
                <div className="mt-1 font-mono text-[10px] text-accent-secondary">{source}</div>
              </div>
              <span className="shrink-0 rounded bg-white/5 px-1.5 py-0.5 font-mono text-[9px] text-text-dim">
                {contextOSFormat.clock(event.ts)}
              </span>
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2">
              <DetailStat label="Items" value={event.contextItems ?? 'n/a'} tone={event.contextItems !== null ? 'active' : 'idle'} sub="WorkingMem" />
              <DetailStat label="Tokens" value={event.contextTokens !== null ? contextOSFormat.tokens(event.contextTokens) : 'n/a'} tone={event.contextTokens !== null ? 'active' : 'idle'} sub="context size" />
              <DetailStat label="Final" value={event.finalRequestTokenEstimate !== null ? contextOSFormat.tokens(event.finalRequestTokenEstimate) : 'n/a'} tone={event.finalRequestTokenEstimate !== null ? 'active' : 'idle'} sub="provider request" />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ReceiptEvidenceDeck({ events }: { events: ContextOSEvent[] }) {
  if (events.length === 0) return <DetailEmpty label="暂无回执或快照事件" />;
  return (
    <div className="space-y-2">
      {events.map((event) => {
        const degraded = event.contextSnapshotDegraded;
        const statusTone: PipelineState = degraded || event.category === 'error' ? 'blocked' : event.contextSnapshotRef || event.contextHash ? 'active' : 'idle';
        const statusLabel = degraded ? '快照降级' : event.contextSnapshotRef ? '快照已落盘' : event.contextHash ? '上下文哈希' : '回执观测';
        return (
          <div key={event.id} className={cn('rounded-xl border p-3', statusTone === 'blocked' ? 'border-status-error/25 bg-status-error/10' : 'border-gold/20 bg-gold/[0.04]')}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className={cn('text-xs font-semibold', STATE_STYLES[statusTone].text)}>{statusLabel}</div>
                <div className="mt-1 truncate text-[11px] text-text-muted" title={safeText(event.summary)}>
                  {safeText(event.summary) || safeText(event.kind)}
                </div>
              </div>
              <span className="shrink-0 rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-dim">
                {contextOSFormat.clock(event.ts)}
              </span>
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              <DetailStat label="Ref" value={event.contextSnapshotRef ? `${event.contextSnapshotRef.slice(0, 8)}...` : event.contextHash ? `${event.contextHash.slice(0, 8)}...` : 'n/a'} tone={statusTone} sub="snapshot/hash" />
              <DetailStat label="Call" value={event.callId ? `${event.callId.slice(0, 12)}...` : 'n/a'} tone={event.callId ? 'active' : 'idle'} sub="correlation id" />
              <DetailStat label="Reason" value={degraded?.reason || 'ok'} tone={degraded ? 'blocked' : 'active'} sub={degraded?.message || 'receipt path'} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DetailDecisionList({ rows }: { rows: DecisionRow[] }) {
  if (rows.length === 0) return <DetailEmpty label="暂无请求或决策记录" />;
  return (
    <div className="space-y-1">
      {rows.slice(0, 6).map((row, index) => (
        <div key={`${row.id}-${index}`} className="grid grid-cols-[58px_78px_1fr] gap-2 rounded-md px-2 py-1.5 text-[10px] hover:bg-white/[0.04]">
          <span className="font-mono text-text-dim">{row.time}</span>
          <span className="truncate font-medium text-text-muted" title={`${row.actor} ${row.kind}`}>
            {safeText(row.actor)}
          </span>
          <div className="min-w-0">
            <div className="truncate text-text-main" title={row.summary}>{safeText(row.summary) || safeText(row.kind)}</div>
            {(row.tokens || row.latencyMs || row.receipt) && (
              <div className="mt-0.5 flex flex-wrap items-center gap-1">
                {row.tokens && <span className="rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary">{contextOSFormat.tokens(row.tokens)} tok</span>}
                {row.latencyMs && <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted">{row.latencyMs}ms</span>}
                {row.receipt && <span className="rounded bg-gold/10 px-1 font-mono text-[9px] text-gold">回执</span>}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function DetailRoleList({ roles }: { roles: RoleCard[] }) {
  return (
    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
      {roles.map((role) => {
        const ctx = role.internalContext;
        const windowLabel = ctx.contextWindowTokens !== null ? contextOSFormat.windowTokens(ctx.contextWindowTokens) : '未知';
        return (
          <div key={role.id} className="rounded-lg border border-white/[0.06] bg-black/20 px-2.5 py-2">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-[11px] font-semibold text-text-main" title={role.title}>{role.title}</div>
                <div className="truncate font-mono text-[9px] text-text-dim">T{ctx.eventCount} · P{ctx.projectionCount} · R{ctx.receiptCount}</div>
              </div>
              <span className={cn('rounded px-1.5 py-0.5 text-[9px]', STATE_STYLES[role.state].ring, STATE_STYLES[role.state].text)}>
                {STATE_STYLES[role.state].label}
              </span>
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-2 font-mono text-[9px] text-text-dim">
              <span className="truncate">{ctx.windowOccupancyTokens !== null ? `~${contextOSFormat.tokens(ctx.windowOccupancyTokens)}` : '无 usage'}</span>
              <span className="shrink-0">/ {windowLabel}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DetailBindingBudgetList({ rows }: { rows: RoleBindingBudget[] }) {
  if (rows.length === 0) return <DetailEmpty label="暂无 provider/model 绑定预算" />;
  return (
    <div className="space-y-1.5">
      {rows.slice(0, 6).map((row) => (
        <BindingBudgetRow key={row.id} row={row} />
      ))}
      {rows.length > 6 && (
        <div className="text-right font-mono text-[9px] text-text-dim">仅显示前 6 路 · 共 {rows.length} 路</div>
      )}
    </div>
  );
}

function PipelineDetailModal({
  stage,
  model,
  telemetry,
  onClose,
}: {
  stage: PipelineStage;
  model: ContextOSModel;
  telemetry: ContextOSTelemetryView;
  onClose: () => void;
}) {
  const Icon = STAGE_ICONS[stage.id] ?? Activity;
  const style = STATE_STYLES[stage.state];
  const recentEvents = telemetry.events.slice(0, 6);
  const projectionEvents = telemetry.events.filter((event) => event.isProjection).slice(0, 6);
  const callEvents = telemetry.events.filter((event) => event.isCall || event.hasUsage).slice(0, 6);
  const receiptEvents = telemetry.events.filter((event) => event.contextSnapshotRef || event.contextSnapshotDegraded || event.contextHash).slice(0, 6);
  const errorEvents = telemetry.events.filter((event) => event.category === 'error' || event.contextSnapshotDegraded).slice(0, 6);
  const activeRoles = model.roles.filter((role) => role.state === 'active').length;
  const blockedRoles = model.roles.filter((role) => role.state === 'blocked').length;
  const roleWindowTotal = model.roles.reduce((sum, role) => sum + (role.internalContext.workingMemoryItems ?? 0), 0);
  const windowDenominator = model.contextWindowTokens !== null ? contextOSFormat.windowTokens(model.contextWindowTokens) : '未知';
  const contextWindowOccupancy = model.contextWindowTokens !== null && model.contextWindowTokens > 0
    ? Math.max(0, Math.min(1, model.windowOccupancyTokens / model.contextWindowTokens))
    : 0;

  const sharedStats = (
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <DetailStat label="状态" value={STATE_STYLES[stage.state].label} tone={stage.state} sub={stage.component} />
      <DetailStat label="节点指标" value={stage.metric} tone={stage.state} sub={stage.hint} />
      <DetailStat label="遥测事件" value={telemetry.events.length} tone={telemetry.events.length > 0 ? 'active' : 'idle'} sub={model.telemetryWindowed ? '最近窗口' : '实时流'} />
      <DetailStat label="错误" value={model.errorCount} tone={model.errorCount > 0 ? 'blocked' : 'idle'} sub="ContextOS / LLM / 回执" />
    </div>
  );

  let body: ReactNode;
  switch (stage.id) {
    case 'request':
      body = (
        <>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <DetailStat label="请求轮次" value={model.iteration ?? 'n/a'} tone={model.iteration !== null ? 'active' : 'idle'} sub="PM iteration / run id" />
            <DetailStat label="任务数" value={model.taskCount} tone={model.taskCount > 0 ? 'active' : 'idle'} sub="snapshot.tasks" />
            <DetailStat label="决策记录" value={model.decisions.length} tone={model.decisions.length > 0 ? 'active' : 'idle'} sub="dialogue / telemetry" />
            <DetailStat label="运行阶段" value={model.running ? 'running' : 'idle'} tone={model.running ? 'active' : 'idle'} sub={model.tokensRealtime ? '实时 token 已接入' : '等待实时 usage'} />
          </div>
          <div className="grid gap-3 lg:grid-cols-[0.95fr_1.05fr]">
            <DetailBlock title="入口摘要" subtitle="用户请求进入 ContextOS 后的可观测负载">
              <div className="space-y-2 text-[11px] text-text-muted">
                <div className="rounded-lg bg-black/20 px-3 py-2">当前阶段：<span className="font-mono text-text-main">{model.running ? '运行中' : '空闲'}</span></div>
                <div className="rounded-lg bg-black/20 px-3 py-2">任务看板：<span className="font-mono text-text-main">{model.taskCount}</span> 个任务</div>
                <div className="rounded-lg bg-black/20 px-3 py-2">质量门：<span className="font-mono text-text-main">{model.errorCount > 0 ? '有风险' : '未见错误'}</span></div>
              </div>
            </DetailBlock>
            <DetailBlock title="最近请求证据" subtitle="来自决策流和运行时推送">
              <DetailDecisionList rows={model.decisions} />
            </DetailBlock>
          </div>
        </>
      );
      break;
    case 'truthlog':
      body = (
        <>
          {sharedStats}
          <div className="grid gap-3 lg:grid-cols-[0.9fr_1.1fr]">
            <DetailBlock title="事件类型分布" subtitle="按真实观测事件 category 聚合">
              {model.eventTypes.length > 0 ? <EventTypeDistribution slices={model.eventTypes} total={model.eventTypesTotal} /> : <DetailEmpty label="暂无事件类型分布" />}
            </DetailBlock>
            <DetailBlock title="TruthLog 最近事件" subtitle="WebSocket 实时流倒序">
              <DetailEventList events={recentEvents} emptyLabel="暂无 TruthLog 事件" />
            </DetailBlock>
          </div>
        </>
      );
      break;
    case 'working_mem':
      body = (
        <>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <DetailStat label="在窗项" value={model.contextItemsCount !== null ? model.contextItemsCount : `~${roleWindowTotal}`} tone={(model.contextItemsCount ?? roleWindowTotal) > 0 ? 'active' : 'idle'} sub={model.contextItemsCount !== null ? 'context.build 实测' : '角色窗口估算'} />
            <DetailStat label="角色活动" value={`${activeRoles}/${model.roles.length}`} tone={activeRoles > 0 ? 'active' : 'idle'} sub="有实时事件的角色" />
            <DetailStat label="最新窗口" value={model.windowOccupancyTokens > 0 ? `~${contextOSFormat.tokens(model.windowOccupancyTokens)}` : '无 usage'} tone={model.windowOccupancyTokens > 0 ? 'active' : 'idle'} sub={`分母 ${windowDenominator}`} />
            <DetailStat label="窗口占用" value={model.contextWindowTokens !== null ? `${Math.round(contextWindowOccupancy * 100)}%` : '未知'} tone={contextWindowOccupancy > 0 ? 'active' : 'idle'} sub={model.contextWindowDetail} />
          </div>
          <div className="grid gap-3 lg:grid-cols-[1fr_1fr]">
            <DetailBlock title="角色工作记忆" subtitle="每个角色自己的窗口和 usage 状态">
              <DetailRoleList roles={model.roles} />
            </DetailBlock>
            <DetailBlock title="WorkingMem 证据" subtitle="context.build / prompt_context 相关事件">
              <DetailEventList events={projectionEvents.length > 0 ? projectionEvents : recentEvents} emptyLabel="暂无 WorkingMem 事件" />
            </DetailBlock>
          </div>
        </>
      );
      break;
    case 'projection':
      body = (
        <>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <DetailStat label="投影数" value={model.projectionCount} tone={model.projectionCount > 0 ? 'active' : 'idle'} sub={model.telemetryActive ? '真实投影事件' : '等待实时遥测'} />
            <DetailStat label="上下文项" value={model.contextItemsCount ?? '未知'} tone={model.contextItemsCount !== null ? 'active' : 'idle'} sub="context.build items_count" />
            <DetailStat label="装配 token" value={model.contextWindowTokens !== null ? windowDenominator : '未知'} tone={model.contextWindowTokens !== null ? 'active' : 'idle'} sub="角色绑定最小窗口" />
            <DetailStat label="事件窗口" value={model.eventTypesTotal} tone={model.eventTypesTotal > 0 ? 'active' : 'idle'} sub={model.telemetryWindowed ? '最近窗口' : '完整观测'} />
          </div>
          <div className="grid gap-3 lg:grid-cols-[0.85fr_1.15fr]">
            <DetailBlock title="ProjectionEngine 解释" subtitle="排序投影和预算规划证据">
              <div className="space-y-2 text-[11px] text-text-muted">
                <div className="rounded-lg bg-black/20 px-3 py-2">投影来源：<span className="text-text-main">context.build / prompt_context / final_request_context_audit</span></div>
                <div className="rounded-lg bg-black/20 px-3 py-2">计数策略：<span className="text-text-main">按 stable projection key 去重</span></div>
                <div className="rounded-lg bg-black/20 px-3 py-2">当前可信度：<span className="text-text-main">{model.telemetryActive ? '真实遥测' : '无实时证据'}</span></div>
              </div>
            </DetailBlock>
            <DetailBlock title="投影事件" subtitle="最近 context projection 证据">
              <ProjectionEvidenceDeck events={projectionEvents} />
            </DetailBlock>
          </div>
        </>
      );
      break;
    case 'role_signal':
      body = (
        <>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <DetailStat label="主角色" value={model.roles.length} tone="active" sub="PM / Architect / CE / Director / QA" />
            <DetailStat label="活动角色" value={activeRoles} tone={activeRoles > 0 ? 'active' : 'idle'} sub="有实时角色事件" />
            <DetailStat label="受阻角色" value={blockedRoles} tone={blockedRoles > 0 ? 'blocked' : 'idle'} sub="LLM readiness blocked" />
            <DetailStat label="模型绑定" value={model.bindingBudgets.length} tone={model.bindingBudgets.length > 0 ? 'active' : 'idle'} sub="provider/model 预算行" />
          </div>
          <div className="grid gap-3 lg:grid-cols-[1fr_1fr]">
            <DetailBlock title="角色信号面" subtitle="角色运行态和内部 ContextOS 计数">
              <DetailRoleList roles={model.roles} />
            </DetailBlock>
            <DetailBlock title="模型绑定预算" subtitle="多路 Director 会拆成独立预算行">
              <DetailBindingBudgetList rows={model.bindingBudgets} />
            </DetailBlock>
          </div>
        </>
      );
      break;
    case 'prompt':
      body = (
        <>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <DetailStat label="Prompt" value={contextOSFormat.tokens(model.promptTokens)} tone={model.promptTokens > 0 ? 'active' : 'idle'} sub={model.tokensRealtime ? 'journal llm 实时' : 'usage stats / 空'} />
            <DetailStat label="Completion" value={contextOSFormat.tokens(model.completionTokens)} tone={model.completionTokens > 0 ? 'active' : 'idle'} sub="输出 token" />
            <DetailStat label="平均每次" value={contextOSFormat.tokens(model.avgPerCall)} tone={model.avgPerCall > 0 ? 'active' : 'idle'} sub="total / calls" />
            <DetailStat label="调用数" value={model.calls} tone={model.calls > 0 ? 'active' : 'idle'} sub="离散 LLM 调用" />
          </div>
          <div className="grid gap-3 lg:grid-cols-[0.8fr_1.2fr]">
            <DetailBlock title="提示构成" subtitle="真实 Prompt / Completion 二分">
              {model.totalTokens > 0 ? (
                <div className="space-y-2.5">
                  {model.budget.map((slice) => (
                    <BudgetBar key={slice.key} label={slice.label} tokens={slice.tokens} ratio={slice.ratio} colorClass={slice.colorClass} />
                  ))}
                </div>
              ) : <DetailEmpty label="暂无 token 用量" />}
            </DetailBlock>
            <DetailBlock title="Prompt 装配事件" subtitle="包含 context token / prompt hash 的最近事件">
              <DetailEventList events={callEvents.length > 0 ? callEvents : recentEvents} emptyLabel="暂无 Prompt 装配事件" />
            </DetailBlock>
          </div>
        </>
      );
      break;
    case 'budget':
      body = (
        <>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <DetailStat label="窗口占用" value={model.contextWindowTokens !== null ? `${Math.round(contextWindowOccupancy * 100)}%` : '未知'} tone={contextWindowOccupancy > 0 ? 'active' : 'idle'} sub={model.contextWindowDetail} />
            <DetailStat label="占用分子" value={model.windowOccupancyTokens > 0 ? `~${contextOSFormat.tokens(model.windowOccupancyTokens)}` : '无 usage'} tone={model.windowOccupancyTokens > 0 ? 'active' : 'idle'} sub="最终请求或平均 prompt" />
            <DetailStat label="窗口分母" value={windowDenominator} tone={model.contextWindowTokens !== null ? 'active' : 'idle'} sub={model.contextWindowLabel} />
            <DetailStat label="绑定行" value={model.bindingBudgets.length} tone={model.bindingBudgets.length > 0 ? 'active' : 'idle'} sub="provider/model budgets" />
          </div>
          <div className="grid gap-3 lg:grid-cols-[0.9fr_1.1fr]">
            <DetailBlock title="CompressionEngine 判定" subtitle="装配后预算压缩兜底视角">
              <div className="space-y-2 text-[11px] text-text-muted">
                <div className="h-2 overflow-hidden rounded-full bg-white/5">
                  <div
                    className={cn(
                      'h-full rounded-full',
                      contextWindowOccupancy > 0.85 ? 'bg-status-error' : contextWindowOccupancy > 0.6 ? 'bg-status-warning' : 'bg-accent-secondary',
                    )}
                    style={{ width: model.contextWindowTokens !== null ? `${Math.max(2, Math.round(contextWindowOccupancy * 100))}%` : '0%' }}
                  />
                </div>
                <div>分子优先级：final request token，其次 context tokens，最后平均 prompt 估算。</div>
                <div>分母来源：当前角色绑定中的最小 max_context_tokens。</div>
              </div>
            </DetailBlock>
            <DetailBlock title="模型预算行" subtitle="每个 provider/model 单独展示">
              <DetailBindingBudgetList rows={model.bindingBudgets} />
            </DetailBlock>
          </div>
        </>
      );
      break;
    case 'llm':
      body = (
        <>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <DetailStat label="调用" value={model.calls} tone={model.calls > 0 ? 'active' : 'idle'} sub={model.tokensRealtime ? '实时 journal llm' : '无实时 usage'} />
            <DetailStat label="Token" value={contextOSFormat.tokens(model.totalTokens)} tone={model.totalTokens > 0 ? 'active' : 'idle'} sub="prompt + completion" />
            <DetailStat label="最近时延" value={model.realLatencyMs !== null ? `${model.realLatencyMs}ms` : '未知'} tone={model.realLatencyMs !== null ? 'active' : 'idle'} sub="provider elapsed ms" />
            <DetailStat label="Worker" value={model.workers.length} tone={model.workers.length > 0 ? 'active' : 'idle'} sub={model.hasWorkers ? '多 worker 追踪' : '未携带 worker_id'} />
          </div>
          <div className="grid gap-3 lg:grid-cols-[0.95fr_1.05fr]">
            <DetailBlock title="LLM 调用事件" subtitle="llm_completed / llm_failed">
              <LlmCallDeck events={callEvents} />
            </DetailBlock>
            <DetailBlock title="模型预算与并发" subtitle="多路 Director / provider 归属">
              {model.workers.length > 0 ? (
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {model.workers.slice(0, 4).map((worker) => (
                    <div key={worker.workerId} className="rounded-lg border border-white/[0.06] bg-black/20 px-2.5 py-2">
                      <div className="truncate font-mono text-[11px] font-semibold text-text-main">{worker.workerId}</div>
                      <div className="mt-1 grid grid-cols-3 gap-1 font-mono text-[9px] text-text-dim">
                        <span>{worker.calls} calls</span>
                        <span>{contextOSFormat.tokens(worker.tokens)} tok</span>
                        <span>{worker.latencyMs !== null ? `${worker.latencyMs}ms` : 'n/a'}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <DetailBindingBudgetList rows={model.bindingBudgets} />
              )}
            </DetailBlock>
          </div>
        </>
      );
      break;
    case 'receipt':
      body = (
        <>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <DetailStat label="回执" value={model.receiptCount} tone={model.receiptCount > 0 ? 'active' : 'idle'} sub="context snapshot refs" />
            <DetailStat label="错误" value={model.errorCount} tone={model.errorCount > 0 ? 'blocked' : 'idle'} sub="Receipt / LLM / runtime" />
            <DetailStat label="快照事件" value={receiptEvents.length} tone={receiptEvents.length > 0 ? 'active' : 'idle'} sub="可追踪 context ref" />
            <DetailStat label="最近时延" value={model.realLatencyMs !== null ? `${model.realLatencyMs}ms` : '未知'} tone={model.realLatencyMs !== null ? 'active' : 'idle'} sub="回执闭环延迟线索" />
          </div>
          <div className="grid gap-3 lg:grid-cols-[0.95fr_1.05fr]">
            <DetailBlock title="回执与快照证据" subtitle="可追踪 context snapshot 或降级原因">
              <ReceiptEvidenceDeck events={receiptEvents.length > 0 ? receiptEvents : callEvents} />
            </DetailBlock>
            <DetailBlock title="异常闭环" subtitle="ReceiptStore / provider / runtime 错误">
              <DetailEventList events={errorEvents} emptyLabel="暂无错误闭环" />
            </DetailBlock>
          </div>
        </>
      );
      break;
    default:
      body = (
        <>
          {sharedStats}
          <DetailBlock title="最近证据" subtitle="该节点暂无专用视图，展示最近运行时事件">
            <DetailEventList events={recentEvents} emptyLabel="暂无事件" />
          </DetailBlock>
        </>
      );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-6 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="contextos-pipeline-detail-title"
      data-testid="contextos-pipeline-detail-modal"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className={cn(
          'max-h-[88vh] w-full max-w-5xl overflow-hidden rounded-2xl border bg-bg-panel/95 shadow-[0_0_44px_rgba(74,158,158,0.18)] backdrop-blur-xl',
          stage.state === 'blocked' ? 'border-status-error/40' : 'border-accent-secondary/30',
        )}
        data-testid={`contextos-pipeline-detail-${stage.id}`}
      >
        <header className="flex items-start justify-between gap-3 border-b border-white/[0.08] px-4 py-3">
          <div className="flex min-w-0 items-start gap-3">
            <div className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-black/35', style.text)}>
              <Icon className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 id="contextos-pipeline-detail-title" className="font-heading text-sm font-bold text-text-main">
                  {stage.label}
                </h2>
                <StatusBadge color={badgeColorForState(stage.state)} variant="dot" pulse={stage.state === 'active'}>
                  <span className="font-mono text-[10px]">{STATE_STYLES[stage.state].label}</span>
                </StatusBadge>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-text-dim">
                <span className="font-mono text-accent-secondary/80">{stage.component}</span>
                <span>{stage.hint}</span>
                <span className={cn('rounded bg-black/30 px-1.5 py-0.5 font-mono', style.text)}>{stage.metric}</span>
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-text-muted transition-colors hover:border-accent-secondary/40 hover:text-text-main focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-secondary/70"
            aria-label="关闭详情"
            data-testid="contextos-pipeline-detail-close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="max-h-[calc(88vh-76px)] space-y-3 overflow-auto p-4">
          {body}
        </div>
      </div>
    </div>
  );
}

function RoleHex({ role, selected, onSelect }: { role: RoleCard; selected: boolean; onSelect: () => void }) {
  const style = STATE_STYLES[role.state];
  const ctx = role.internalContext;
  const occupancyLabel = ctx.windowOccupancyTokens !== null
    ? `~${contextOSFormat.tokens(ctx.windowOccupancyTokens)}`
    : '无 usage';
  const windowLabel = ctx.contextWindowTokens !== null
    ? contextOSFormat.windowTokens(ctx.contextWindowTokens)
    : '窗口未知';
  const windowSourceLabel = ctx.contextWindowSource === 'binding'
    ? ctx.bindingBudgets.length > 1
      ? `${ctx.bindingBudgets.length} 路绑定`
      : ctx.contextWindowModel ? `${ctx.contextWindowModel} 绑定` : '绑定'
    : '未知';
  return (
    <button
      type="button"
      data-testid={`contextos-role-${role.id}`}
      data-selected={selected}
      aria-pressed={selected}
      onClick={onSelect}
      title={`${role.title} ${role.courtTitle} · ${ctx.windowOccupancyDetail} · ${role.contextWindowDetail}`}
      className={cn(
        'flex items-center gap-2 rounded-xl border px-2.5 py-2 text-left transition-all duration-300 hover:border-accent-secondary/40',
        style.ring,
        selected && 'ring-2 ring-accent-secondary/60',
      )}
    >
      <div className={cn('flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-black/30 font-heading text-sm font-bold', style.text)}>
        {role.courtTitle.slice(0, 1)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={cn('h-1.5 w-1.5 rounded-full', style.dot)} />
          <span className="truncate text-xs font-semibold text-text-main">{role.title}</span>
        </div>
        <div className={cn('truncate font-mono text-[10px]', style.text)}>{role.detail}</div>
        <div className="mt-1 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 font-mono text-[9px]">
          <span
            data-testid={`contextos-role-occupancy-${role.id}`}
            className={cn('truncate', ctx.windowOccupancyTokens !== null ? 'text-accent-secondary' : 'text-text-dim')}
            title={ctx.windowOccupancyDetail}
          >
            {occupancyLabel}
          </span>
          <span
            data-testid={`contextos-role-window-${role.id}`}
            className="shrink-0 rounded bg-white/5 px-1 text-text-muted"
            title={role.contextWindowDetail}
          >
            / {windowLabel} <span className="text-text-dim/70">({windowSourceLabel})</span>
          </span>
        </div>
      </div>
    </button>
  );
}

function RoleInternalStat({ label, value, unit, sub, highlight = false }: { label: string; value: string | number; unit?: string; sub?: string; highlight?: boolean }) {
  return (
    <div className="flex flex-col rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2">
      <span className="text-[9px] uppercase tracking-wider text-text-dim">{label}</span>
      <div className="mt-0.5 flex items-baseline gap-1">
        <span className={cn('font-mono text-sm font-bold', highlight ? 'text-accent-secondary' : 'text-text-main')}>{value}</span>
        {unit && <span className="text-[9px] text-text-dim">{unit}</span>}
      </div>
      {sub && <div className="mt-0.5 truncate text-[9px] text-text-dim" title={sub}>{sub}</div>}
    </div>
  );
}

function RoleInternalEventRow({ event }: { event: ContextOSEvent }) {
  const tone: PipelineState = event.category === 'error' ? 'blocked' : event.isProjection || event.hasReceipt ? 'active' : 'idle';
  const summaryText = safeText(event.summary) || safeText(event.kind) || '事件';
  const auditTitle = formatFinalRequestAuditTitle(event.finalRequestContextAudit);
  return (
    <div
      className="grid grid-cols-[68px_1fr] items-start gap-2 rounded-md px-2 py-1.5 text-[11px] hover:bg-white/[0.03]"
      aria-label={`${event.category === 'error' ? '错误事件' : '事件'} ${safeText(event.kind)} ${summaryText}`}
    >
      <div className="flex items-center gap-1.5">
        <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', STATE_STYLES[tone].dot)} />
        <span className="font-mono text-[10px] text-text-dim">{contextOSFormat.clock(event.ts)}</span>
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-dim">{safeText(event.kind)}</span>
          {(event.hasUsage || event.durationMs !== null || event.contextTokens !== null || event.hasReceipt || event.contextSnapshotDegraded || auditTitle) && (
            <div className="flex flex-wrap items-center gap-1">
              {event.hasUsage && event.totalTokens > 0 && (
                <span className="rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary">
                  {contextOSFormat.tokens(event.totalTokens)} tok
                </span>
              )}
              {event.contextTokens !== null && event.contextTokens > 0 && (
                <span className="rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary">
                  ctx {contextOSFormat.tokens(event.contextTokens)}
                </span>
              )}
              {auditTitle && (
                <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-dim" title={auditTitle}>
                  audit
                </span>
              )}
              <FinalRequestAgiCoverageBadges audit={event.finalRequestContextAudit} compact />
              {event.durationMs !== null && event.durationMs > 0 && (
                <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted">{event.durationMs}ms</span>
              )}
              {event.hasReceipt && (
                <span className="rounded bg-gold/10 px-1 font-mono text-[9px] text-gold">快照</span>
              )}
              {event.contextSnapshotDegraded && (
                <span
                  className="rounded bg-status-warning/10 px-1 font-mono text-[9px] text-status-warning"
                  title={event.contextSnapshotDegraded.message || event.contextSnapshotDegraded.reason}
                >
                  快照未落盘
                </span>
              )}
            </div>
          )}
        </div>
        <div className="truncate text-text-muted" title={summaryText}>{summaryText}</div>
      </div>
    </div>
  );
}

function formatFinalRequestAuditTitle(audit: Record<string, unknown> | null): string {
  if (!audit) return '';
  const coverage = typeof audit['coverage'] === 'object' && audit['coverage'] !== null
    ? audit['coverage'] as Record<string, unknown>
    : {};
  const parts = [
    ['final', audit['final_request_token_estimate']],
    ['msg', audit['message_token_estimate']],
    ['tools', audit['tool_schema_token_estimate']],
    ['pm', coverage['has_pm_contract']],
    ['ce', coverage['has_chief_engineer_blueprint']],
    ['files', coverage['has_target_files']],
    ['feedback', coverage['has_failure_feedback']],
    ['agi_decision', coverage['has_resident_agi_decision_trace']],
    ['agi_capability', coverage['has_resident_agi_capability_surface']],
  ].map(([key, value]) => `${key}=${String(value ?? 'n/a')}`);
  return parts.join(' ');
}

function StructureMetric({
  label,
  value,
  tone = 'idle',
  sub,
}: {
  label: string;
  value: string | number;
  tone?: PipelineState;
  sub?: string;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[10px] font-semibold text-text-main" title={label}>{label}</span>
        <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', STATE_STYLES[tone].dot)} />
      </div>
      <div className={cn('mt-1 font-mono text-sm font-bold', STATE_STYLES[tone].text)}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[9px] text-text-dim" title={sub}>{sub}</div>}
    </div>
  );
}

function ContextStructurePanel({ model, telemetry }: { model: ContextOSModel; telemetry: ReturnType<typeof buildTelemetryFromStream> }) {
  const roleWindowTotal = model.roles.reduce((sum, role) => sum + (role.internalContext.workingMemoryItems ?? 0), 0);
  const activeRoles = model.roles.filter((role) => role.internalContext.eventCount > 0);
  const newestEvents = telemetry.events.slice(0, 8);

  return (
    <SectionCard
      title="上下文结构"
      subtitle="TruthLog / WorkingMem / Projection / Receipt"
      icon={Database}
      className="border-accent-secondary/20"
    >
      <div data-testid="contextos-structure-panel" className="space-y-3">
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          <StructureMetric
            label="TruthLog"
            value={`${telemetry.events.length} 事件`}
            tone={telemetry.events.length > 0 ? 'active' : 'idle'}
            sub={model.telemetryWindowed ? '最近窗口' : '实时流'}
          />
          <StructureMetric
            label="WorkingMem"
            value={model.contextItemsCount !== null ? `${model.contextItemsCount} 项` : `~${roleWindowTotal} 项`}
            tone={roleWindowTotal > 0 || (model.contextItemsCount ?? 0) > 0 ? 'active' : 'idle'}
            sub={model.contextItemsCount !== null ? 'context.build' : '角色事件窗口估算'}
          />
          <StructureMetric
            label="ProjectionEngine"
            value={`${model.projectionCount} 投影`}
            tone={model.projectionCount > 0 ? 'active' : 'idle'}
            sub={`${model.eventTypesTotal} 观测基数`}
          />
          <StructureMetric
            label="ReceiptStore"
            value={`${model.receiptCount} 回执`}
            tone={model.receiptCount > 0 ? 'active' : model.errorCount > 0 ? 'blocked' : 'idle'}
            sub={model.errorCount > 0 ? `${model.errorCount} 错误` : 'snapshot receipts'}
          />
        </div>

        <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <div className="rounded-lg border border-white/[0.06] bg-black/20 p-2.5">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-text-dim">角色上下文窗口</span>
              <span className="font-mono text-[10px] text-text-muted">{activeRoles.length}/{model.roles.length}</span>
            </div>
            <div className="space-y-1.5">
              {model.roles.map((role) => {
                const ctx = role.internalContext;
                const occupancyLabel = ctx.windowOccupancyTokens !== null
                  ? `~${contextOSFormat.tokens(ctx.windowOccupancyTokens)}`
                  : '无 usage';
                const windowLabel = ctx.contextWindowTokens !== null
                  ? contextOSFormat.windowTokens(ctx.contextWindowTokens)
                  : '未知';
                const windowSourceLabel = ctx.contextWindowSource === 'binding'
                  ? ctx.contextWindowModel ? `${ctx.contextWindowModel} 绑定` : '绑定'
                  : '未知';
                return (
                  <div key={role.id} className="grid grid-cols-[72px_minmax(0,1fr)_64px_58px] items-center gap-2 rounded-md bg-white/[0.02] px-2 py-1.5 text-[10px]">
                    <span className="truncate font-semibold text-text-main" title={role.title}>{role.title}</span>
                    <div className="min-w-0">
                      <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
                        <div
                          className={cn('h-full rounded-full', STATE_STYLES[ctx.state].dot)}
                          style={{ width: `${Math.max(4, Math.min(100, ctx.eventCount * 12))}%` }}
                        />
                      </div>
                      <div className="mt-1 truncate font-mono text-[9px] text-text-dim">
                        T{ctx.eventCount} · W{ctx.workingMemoryItems ?? 0}{ctx.workingMemoryEstimated ? '~' : ''} · P{ctx.projectionCount} · R{ctx.receiptCount}
                      </div>
                    </div>
                    <span
                      className={cn('truncate text-right font-mono', ctx.windowOccupancyTokens !== null ? 'text-accent-secondary' : 'text-text-dim')}
                      title={ctx.windowOccupancyDetail}
                    >
                      {occupancyLabel}
                    </span>
                    <span
                      className="truncate text-right font-mono text-text-muted"
                      title={ctx.contextWindowDetail}
                    >
                      {windowLabel} <span className="text-text-dim/70">({windowSourceLabel})</span>
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="rounded-lg border border-white/[0.06] bg-black/20 p-2.5">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-text-dim">最近结构事件</span>
              <span className="font-mono text-[10px] text-text-muted">{newestEvents.length}</span>
            </div>
            {newestEvents.length > 0 ? (
              <div className="space-y-1">
                {newestEvents.map((event) => (
                  <div key={event.id} className="grid grid-cols-[54px_80px_1fr] gap-2 rounded-md px-2 py-1.5 text-[10px] hover:bg-white/[0.03]">
                    <span className="font-mono text-text-dim">{contextOSFormat.clock(event.ts)}</span>
                    <span className="truncate text-text-muted" title={event.actor}>{safeText(event.actor)}</span>
                    <span className="truncate text-text-main" title={event.summary}>{safeText(event.summary)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-white/10 px-3 py-5 text-center">
                <Database className="h-4 w-4 text-text-dim/30" />
                <div className="text-[11px] text-text-dim">暂无结构事件</div>
              </div>
            )}
          </div>
        </div>
      </div>
    </SectionCard>
  );
}

function RoleInternalPanel({ role, onViewContext }: { role: RoleCard; onViewContext: (ref: string) => void }) {
  const ctx = role.internalContext;
  const style = STATE_STYLES[ctx.state];

  const pipeline: PipelineStage[] = [
    { id: 'truthlog', label: 'TruthLog', component: '事件真值流', hint: '角色专属事件流', state: ctx.eventCount > 0 ? 'active' : 'idle', metric: `${ctx.eventCount} 事件` },
    {
      id: 'working_mem',
      label: 'WorkingMem',
      component: '活动窗口',
      hint: ctx.workingMemoryEstimated ? '实时观测窗口' : '在窗上下文项',
      state: (ctx.workingMemoryItems ?? 0) > 0 ? 'active' : 'idle',
      metric: ctx.workingMemoryItems !== null
        ? `${ctx.workingMemoryEstimated ? '~' : ''}${ctx.workingMemoryItems} 项${ctx.workingMemoryEstimated ? ' 估算' : ''}`
        : '—',
    },
    { id: 'projection', label: 'ProjectionEngine', component: '投影装配', hint: '上下文装配次数', state: ctx.projectionCount > 0 ? 'active' : 'idle', metric: `${ctx.projectionCount} 投影` },
    { id: 'receipt', label: 'ReceiptStore', component: '快照回执', hint: '落盘回执数', state: ctx.receiptCount > 0 ? 'active' : 'idle', metric: `${ctx.receiptCount} 回执` },
  ];

  const displayedEvents = ctx.events.length;
  const hasTruncation = ctx.eventCount > displayedEvents;

  // Collect LLM calls with context_snapshot_ref or explicit snapshot degradation evidence from events.
  const llmCalls = ctx.events
    .filter((event) => (event.contextSnapshotRef || event.contextSnapshotDegraded) && (event.isCall || event.hasUsage))
    .slice(0, 5);

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
                className="flex w-[80px] shrink-0 flex-col items-center gap-0.5 rounded-lg border border-white/[0.06] bg-white/[0.02] px-1 py-1.5 text-center"
              >
                <span className="text-[10px] font-semibold text-text-main">{stage.label}</span>
                <span className={cn('rounded-full bg-black/30 px-1.5 py-0.5 font-mono text-[9px]', STATE_STYLES[stage.state].text)}>{stage.metric}</span>
              </div>
            </div>
          ))}
        </div>
        <div className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-bg-panel/70 to-transparent xl:hidden" aria-hidden />
      </div>

      {/* 统计卡 */}
      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
        <RoleInternalStat
          label="活动"
          value={`${ctx.eventCount} · ${ctx.projectionCount}`}
          sub={`回执 ${ctx.receiptCount}`}
        />
        <RoleInternalStat
          label="调用"
          value={ctx.calls}
          sub={ctx.lastEventAt !== null ? `最近 ${formatFreshness(ctx.lastEventAt)}` : '无活动'}
        />
        <RoleInternalStat
          label="窗口"
          value={ctx.contextWindowTokens !== null ? contextOSFormat.windowTokens(ctx.contextWindowTokens) : '未知'}
          sub={ctx.contextWindowSource === 'binding' ? `${ctx.contextWindowProvider ?? ''}${ctx.contextWindowModel ? ` / ${ctx.contextWindowModel}` : ''} · maxContextTokens` : ctx.contextWindowDetail}
          highlight={ctx.contextWindowTokens !== null}
        />
        <RoleInternalStat
          label="占用"
          value={ctx.windowOccupancyTokens !== null ? `~${contextOSFormat.tokens(ctx.windowOccupancyTokens)}` : '—'}
          sub={ctx.windowOccupancyLabel}
          highlight={ctx.windowOccupancyTokens !== null}
        />
        <RoleInternalStat
          label="Token"
          value={ctx.totalTokens > 0 ? `${contextOSFormat.tokens(ctx.promptTokens)} / ${contextOSFormat.tokens(ctx.completionTokens)}` : '—'}
          sub={ctx.totalTokens > 0 ? '提示 / 输出' : '无 usage 观测'}
          highlight={ctx.totalTokens > 0}
        />
      </div>

      {/* 最近 LLM 调用（带上下文查看） */}
      {llmCalls.length > 0 && (
        <div className="mb-3">
          <div className="mb-1.5 flex items-center justify-between text-[10px] uppercase tracking-wider text-text-dim">
            <span>最近 LLM 调用</span>
            <span className="font-mono normal-case text-text-dim">{llmCalls.length} 条</span>
          </div>
          <div className="space-y-1">
            {llmCalls.map((event) => (
              <div
                key={event.id}
                className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-[11px] hover:bg-white/[0.03]"
              >
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', event.category === 'error' ? 'bg-status-error' : 'bg-accent-secondary')} />
                  <span className="truncate font-mono text-[10px] text-text-dim">{contextOSFormat.clock(event.ts)}</span>
                  <span className="truncate text-text-muted" title={event.summary}>{event.summary || event.kind}</span>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {event.totalTokens > 0 && (
                    <span className="rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary">
                      {contextOSFormat.tokens(event.totalTokens)} tok
                    </span>
                  )}
                  {event.durationMs !== null && event.durationMs > 0 && (
                    <span className="rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted">{event.durationMs}ms</span>
                  )}
                  {event.contextSnapshotRef ? (
                    <button
                      type="button"
                      onClick={() => event.contextSnapshotRef && onViewContext(event.contextSnapshotRef)}
                      className="rounded bg-accent-secondary/15 px-1.5 py-0.5 text-[9px] text-accent-secondary hover:bg-accent-secondary/25 transition-colors"
                      title="查看完整上下文"
                    >
                      查看完整上下文
                    </button>
                  ) : event.contextSnapshotDegraded ? (
                    <span
                      className="rounded bg-status-warning/10 px-1.5 py-0.5 text-[9px] text-status-warning"
                      title={event.contextSnapshotDegraded.message || event.contextSnapshotDegraded.reason}
                    >
                      快照未落盘
                    </span>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

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
          <div
            className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-white/10 px-3 py-4 text-center"
            data-testid={`contextos-role-panel-empty-events-${role.id}`}
          >
            <Activity className="h-4 w-4 text-text-dim/30" />
            <div className="text-[11px] text-text-dim">该角色暂无实时观测事件</div>
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

const USAGE_KIND_LABELS: Record<RoleBindingBudget['usageKind'], string> = {
  provider: 'provider usage',
  stream_final: 'stream final',
  request_estimate: 'request estimate',
  char_estimate: 'char estimate',
  mixed: 'mixed usage',
  none: 'no usage',
};

function UsageMetricChip({
  label,
  tokens,
  tone = 'neutral',
}: {
  label: string;
  tokens: number;
  tone?: 'neutral' | 'cache' | 'tool' | 'output' | 'reasoning' | 'error';
}) {
  if (tokens <= 0) return null;
  return (
    <span
      className={cn(
        'rounded border px-1.5 py-0.5 font-mono text-[9px]',
        tone === 'cache'
          ? 'border-status-success/20 bg-status-success/10 text-status-success'
          : tone === 'tool'
            ? 'border-accent/20 bg-accent/10 text-accent'
            : tone === 'output'
              ? 'border-gold/20 bg-gold/10 text-gold'
              : tone === 'reasoning'
                ? 'border-status-warning/20 bg-status-warning/10 text-status-warning'
                : tone === 'error'
                  ? 'border-status-error/20 bg-status-error/10 text-status-error'
                  : 'border-white/[0.07] bg-white/[0.04] text-text-muted',
      )}
      title={`${label}: ${tokens.toLocaleString()} tokens`}
    >
      {label} {contextOSFormat.tokens(tokens)}
    </span>
  );
}

function UsageBreakdownChips({
  promptTokens,
  completionTokens,
  cachedTokens,
  cacheCreationTokens,
  cacheReadTokens,
  toolTokens,
  reasoningTokens = 0,
  audioTokens = 0,
  serverToolUseCount = 0,
}: {
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
  toolTokens: number;
  reasoningTokens?: number;
  audioTokens?: number;
  serverToolUseCount?: number;
}) {
  const directPromptTokens = Math.max(0, promptTokens - cacheCreationTokens - cacheReadTokens);
  const effectiveCachedTokens = Math.max(cachedTokens, cacheReadTokens);
  const hasBreakdown =
    directPromptTokens > 0 ||
    completionTokens > 0 ||
    effectiveCachedTokens > 0 ||
    cacheCreationTokens > 0 ||
    toolTokens > 0 ||
    reasoningTokens > 0 ||
    audioTokens > 0 ||
    serverToolUseCount > 0;
  if (!hasBreakdown) return null;
  return (
    <div className="flex flex-wrap gap-1">
      <UsageMetricChip label="in" tokens={directPromptTokens} />
      <UsageMetricChip label="out" tokens={completionTokens} tone="output" />
      <UsageMetricChip label="cache read" tokens={effectiveCachedTokens} tone="cache" />
      <UsageMetricChip label="cache write" tokens={cacheCreationTokens} tone="cache" />
      <UsageMetricChip label="tools" tokens={toolTokens} tone="tool" />
      <UsageMetricChip label="reasoning" tokens={reasoningTokens} tone="reasoning" />
      <UsageMetricChip label="audio" tokens={audioTokens} tone="tool" />
      <UsageMetricChip label="server tools" tokens={serverToolUseCount} tone="tool" />
    </div>
  );
}

function BindingBudgetRow({ row }: { row: RoleBindingBudget }) {
  const hasUsage = row.windowOccupancyTokens !== null;
  const ratio = hasUsage && row.contextWindowTokens !== null && row.contextWindowTokens > 0
    ? Math.max(0, Math.min(1, row.windowOccupancyTokens! / row.contextWindowTokens))
    : 0;
  const provider = row.providerName || row.providerId || 'Provider unknown';
  const model = row.model || '未归属模型';
  const usageLabel = row.usageSource === 'matched'
    ? '模型实测'
    : row.usageSource === 'role_aggregate'
      ? '角色聚合'
      : '无 usage';
  const usageKindLabel = USAGE_KIND_LABELS[row.usageKind];
  const provenanceLabel = row.usageProvenance === 'provider'
    ? '真实'
    : row.usageProvenance === 'estimated'
      ? '估算'
      : row.usageProvenance === 'mixed'
        ? '混合'
        : null;
  const taskRef = row.taskId || row.pmTaskId || row.chiefBlueprintId;

  return (
    <div
      data-testid={`contextos-binding-budget-${row.id}`}
      className="rounded-lg border border-white/[0.06] bg-white/[0.025] px-2.5 py-2"
      title={row.windowOccupancyDetail}
    >
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[11px] font-semibold text-text-main" title={row.label}>
            {model}
          </div>
          <div className="truncate font-mono text-[9px] text-text-dim" title={provider}>
            {provider}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <span
            className={cn(
              'rounded px-1.5 py-0.5 text-[9px]',
              row.usageSource === 'matched'
                ? 'bg-accent-secondary/10 text-accent-secondary'
                : row.usageSource === 'role_aggregate'
                  ? 'bg-status-warning/10 text-status-warning'
                  : 'bg-white/5 text-text-dim',
            )}
          >
            {usageLabel}
          </span>
          {row.calls > 0 && (
            <span className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-muted">
              {row.calls} calls
            </span>
          )}
          {row.usageKind !== 'none' && (
            <span className="rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-[9px] text-text-dim">
              {usageKindLabel}
            </span>
          )}
          {provenanceLabel && (
            <span
              className={cn(
                'rounded px-1.5 py-0.5 text-[9px]',
                row.usageProvenance === 'provider'
                  ? 'bg-status-success/10 text-status-success'
                  : row.usageProvenance === 'estimated'
                    ? 'bg-status-warning/10 text-status-warning'
                    : 'bg-white/[0.05] text-text-dim',
              )}
            >
              {provenanceLabel}
            </span>
          )}
          {row.skipped && (
            <span className="rounded bg-status-error/10 px-1.5 py-0.5 text-[9px] text-status-error" title={row.skipReason || undefined}>
              skipped
            </span>
          )}
        </div>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500',
            ratio > 0.85 ? 'bg-status-error' : ratio > 0.6 ? 'bg-status-warning' : 'bg-accent-secondary',
          )}
          style={{ width: hasUsage ? `${Math.max(2, Math.round(ratio * 100))}%` : '0%' }}
        />
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2 font-mono text-[9px] text-text-dim">
        <span className="truncate">
          {hasUsage ? `~${contextOSFormat.tokens(row.windowOccupancyTokens!)}` : '无 usage'}
          <span className="ml-1 text-text-dim/70">{row.windowOccupancyLabel}</span>
        </span>
        <span className="shrink-0">
          / {row.contextWindowTokens !== null ? contextOSFormat.windowTokens(row.contextWindowTokens) : '未知'}
        </span>
      </div>
      {(row.totalTokens > 0 || row.latencyMs !== null) && (
        <div className="mt-1 flex items-center justify-end gap-1.5 font-mono text-[9px] text-text-dim/80">
          {row.totalTokens > 0 && <span>{contextOSFormat.tokens(row.totalTokens)} tok</span>}
          {row.latencyMs !== null && <span>{row.latencyMs}ms</span>}
        </div>
      )}
      {taskRef && (
        <div className="mt-1 truncate font-mono text-[9px] text-text-dim/80" title={taskRef}>
          task {taskRef}
        </div>
      )}
      <div className="mt-1.5">
        <UsageBreakdownChips
          promptTokens={row.promptTokens}
          completionTokens={row.completionTokens}
          cachedTokens={row.cachedTokens}
          cacheCreationTokens={row.cacheCreationTokens}
          cacheReadTokens={row.cacheReadTokens}
          toolTokens={row.toolTokens}
          reasoningTokens={row.reasoningTokens}
          audioTokens={row.audioTokens}
          serverToolUseCount={row.serverToolUseCount}
        />
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
      <div
        className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-white/10 px-3 py-6 text-center"
        data-testid="contextos-decision-empty"
      >
        <Activity className="h-5 w-5 text-text-dim/40" />
        <div className="text-[11px] text-text-dim">
          <span className="font-medium">暂无决策 / 回执记录</span>
        </div>
        <div className="text-[10px] text-text-dim/60">
          启动 PM 或 Director 后将实时流入
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-1">
      {rows.map((row, index) => (
        <div key={`${row.id}-${index}`} className="grid grid-cols-[64px_72px_1fr] items-start gap-2 rounded-md px-2 py-1.5 text-[11px] hover:bg-white/[0.03]">
          <span className="font-mono text-[10px] text-text-dim">{row.time}</span>
          <span className={cn('truncate font-medium', toneClass[row.tone])} title={`${row.actor} · ${row.kind}`}>
            {safeText(row.actor)}
          </span>
          <div className="min-w-0">
            <span className="block truncate text-text-muted" title={row.summary}>{safeText(row.summary) || safeText(row.kind)}</span>
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
      <div className="min-h-0 flex-1 p-3">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// WorkerPanel — Phase 3+ 多 worker LLM 追踪面板
// ---------------------------------------------------------------------------

interface WorkerCardViewProps {
  worker: WorkerCard;
  onViewContext: (ref: string, workerId: string) => void;
}

function WorkerCardView({ worker, onViewContext }: WorkerCardViewProps) {
  const style = STATE_STYLES[worker.state];
  return (
    <div
      data-testid={`contextos-worker-${worker.workerId}`}
      data-state={worker.state}
      className={cn(
        'rounded-lg border bg-white/[0.02] p-2.5 transition-all duration-300',
        style.ring,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <div className={cn('flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-black/30', style.text)}>
            <Cpu className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0">
            <div className="truncate font-mono text-[11px] font-semibold text-text-main" title={worker.workerId}>
              {worker.workerId}
            </div>
            <div className={cn('truncate text-[9px]', style.text)}>{worker.role}</div>
          </div>
        </div>
        <span className={cn('rounded px-1.5 py-0.5 text-[9px] font-medium', style.ring, style.text)}>
          {style.label}
        </span>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-1.5 text-[10px]">
        <div className="rounded bg-black/20 px-1.5 py-1">
          <div className="text-[8px] uppercase tracking-wider text-text-dim">调用</div>
          <div className="font-mono font-semibold text-text-main">{worker.calls}</div>
        </div>
        <div className="rounded bg-black/20 px-1.5 py-1">
          <div className="text-[8px] uppercase tracking-wider text-text-dim">Token</div>
          <div className="font-mono font-semibold text-text-main">
            {worker.tokens > 0 ? contextOSFormat.tokens(worker.tokens) : '0'}
          </div>
        </div>
        <div className="rounded bg-black/20 px-1.5 py-1">
          <div className="text-[8px] uppercase tracking-wider text-text-dim">时延</div>
          <div className="font-mono font-semibold text-text-main">
            {worker.latencyMs !== null ? `${worker.latencyMs}ms` : '—'}
          </div>
        </div>
      </div>
      {worker.latestContextSnapshotRef && (
        <button
          type="button"
          data-testid={`contextos-worker-view-${worker.workerId}`}
          onClick={() => onViewContext(worker.latestContextSnapshotRef as string, worker.workerId)}
          className="mt-2 w-full rounded bg-accent-secondary/15 px-2 py-1 text-[10px] text-accent-secondary hover:bg-accent-secondary/25 transition-colors"
        >
          查看 worker 上下文
        </button>
      )}
    </div>
  );
}

function WorkerPanel({
  workers,
  onViewContext,
}: {
  workers: WorkerCard[];
  onViewContext: (ref: string, workerId: string) => void;
}) {
  return (
    <SectionCard
      title="多 worker LLM 追踪"
      subtitle={`Multi-worker LLM Tracking · ${workers.length} worker`}
      icon={Cpu}
      action={
        <span className="text-[10px] text-text-dim" data-testid="contextos-worker-count">
          {workers.length} 个并发 worker
        </span>
      }
      className="border-accent/30"
    >
      <div data-testid="contextos-worker-panel" className="space-y-2">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {workers.map((worker) => (
            <WorkerCardView key={worker.workerId} worker={worker} onViewContext={onViewContext} />
          ))}
        </div>
      </div>
    </SectionCard>
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
  controlPlaneProjection,
}: ContextOSWorkspaceProps) {
  // 真实 ContextOS 遥测：直接派生自 useRuntime 经 WebSocket(/v2/ws/runtime) 实时推送的运行时流。
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
  const [showStructure, setShowStructure] = useState(false);
  const [viewerHash, setViewerHash] = useState<string | null>(null);
  const [viewerRole, setViewerRole] = useState<string>('');
  // Phase 3+：worker-scoped context viewer。
  const [viewerWorkerId, setViewerWorkerId] = useState<string | null>(null);
  const [pipelineDetailId, setPipelineDetailId] = useState<string | null>(null);

  const wsTone = live ? 'success' : reconnecting ? 'warning' : 'error';
  const wsLabel = live ? 'WS LIVE' : reconnecting ? 'WS RECONNECT' : 'WS OFFLINE';
  const phaseLabel = (currentPhase || 'idle').trim() || 'idle';
  const ledgerGatePassed = controlPlaneGatePassed(controlPlaneProjection);
  const gatePassed = ledgerGatePassed ?? qualityGate?.passed;
  const gateSource = ledgerGatePassed === undefined ? 'quality gate' : 'Run Ledger';
  // 观测到活动 = PM/Director 运行中 或 真实遥测有内容。
  const observed = model.running || model.telemetryActive;
  // 「真正有数据」= 真实遥测有内容；此时不再视为空闲水印。
  const idle = !model.telemetryActive && (model.dataIdle || (!live && !model.running));
  const pipelineLive = observed && live;

  // 新鲜度以"最近一条 WS 推送事件"的时间为准，避免陈旧数据被误读为实时。
  const lastEventEpoch = model.lastTelemetryEpoch;
  const telemetryAgeMs = lastEventEpoch ? Date.now() - lastEventEpoch : null;
  const telemetryFresh = telemetryAgeMs !== null && telemetryAgeMs < 30_000; // 30s 内视为"实时"
  const freshnessLabel = lastEventEpoch ? formatFreshness(lastEventEpoch) : null;
  const contextStoreRefreshSignal = useMemo(() => {
    const latestSnapshotEvent = telemetry.events.find(
      (event) => event.contextSnapshotRef || event.contextSnapshotDegraded || event.contextHash,
    );
    if (!latestSnapshotEvent) return null;
    const ref = latestSnapshotEvent.contextSnapshotRef || latestSnapshotEvent.contextHash || latestSnapshotEvent.id;
    return `${ref}:${latestSnapshotEvent.epoch}:${latestSnapshotEvent.seq}`;
  }, [telemetry.events]);

  const filteredDecisions = useMemo(
    () => model.decisions.filter((row) => decisionMatchesRole(row.actor, activeRole)),
    [model.decisions, activeRole],
  );
  const selectedRole = activeRole ? model.roles.find((role) => role.id === activeRole) ?? null : null;
  const budgetWindowTokens = selectedRole?.contextWindowTokens ?? model.contextWindowTokens;
  const budgetWindowSource = selectedRole?.contextWindowSource ?? model.contextWindowSource;
  const budgetWindowLabel = selectedRole
    ? `${selectedRole.id.toUpperCase()} · ${selectedRole.title} · ${selectedRole.contextWindowLabel}${budgetWindowSource === 'binding' ? ' · 绑定' : ''}`
    : `${model.contextWindowLabel}${budgetWindowSource === 'binding' ? ' · 绑定' : ''}`;
  const budgetWindowDetail = selectedRole?.contextWindowDetail ?? model.contextWindowDetail;
  const globalWindowOccupancyTokens = model.windowOccupancyTokens > 0 ? model.windowOccupancyTokens : null;
  const budgetWindowOccupancyTokens = selectedRole
    ? selectedRole.internalContext.windowOccupancyTokens
    : globalWindowOccupancyTokens;
  const budgetWindowOccupancyLabel = selectedRole
    ? selectedRole.internalContext.windowOccupancyLabel
    : globalWindowOccupancyTokens !== null ? '平均提示 (估算)' : '无 usage';
  const budgetWindowOccupancyDetail = selectedRole
    ? `${selectedRole.internalContext.windowOccupancyDetail} · ${budgetWindowDetail}`
    : globalWindowOccupancyTokens !== null ? budgetWindowDetail : `尚无全局 usage 观测 · ${budgetWindowDetail}`;
  const budgetWindowOccupancy = budgetWindowOccupancyTokens !== null && budgetWindowTokens !== null && budgetWindowTokens > 0
    ? Math.max(0, Math.min(1, budgetWindowOccupancyTokens / budgetWindowTokens))
    : 0;
  const hasBudgetWindowUsage = budgetWindowOccupancyTokens !== null;
  const budgetBindingRows = selectedRole
    ? selectedRole.internalContext.bindingBudgets
    : model.bindingBudgets;
  const visibleBudgetBindingRows = budgetBindingRows.slice(0, selectedRole ? 8 : 10);
  const receiptStage: PipelineStage = {
    id: 'receipt',
    label: 'Receipt',
    component: 'Context Snapshot + Telemetry',
    hint: '落盘上下文快照与遥测反馈闭环',
    state: model.errorCount > 0 ? 'blocked' : model.receiptCount > 0 || model.calls > 0 ? 'active' : 'idle',
    metric: model.errorCount > 0
      ? `${model.errorCount} 错误`
      : model.receiptCount > 0
        ? `${model.receiptCount} 快照`
        : `${model.calls} 调用`,
  };
  const selectedPipelineStage = pipelineDetailId
    ? [...model.pipeline, receiptStage].find((stage) => stage.id === pipelineDetailId) ?? null
    : null;

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
              <p
              className="truncate text-[10px] uppercase tracking-wider text-accent-secondary/70"
                title={workspace}
              >
                上下文操作系统 · {workspaceLabel(workspace, '未选定工作区')}
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <StatusBadge color={model.running ? 'success' : 'default'} variant="dot" pulse={model.running}>
            <span className="font-mono text-[10px]">阶段 {phaseLabel}</span>
          </StatusBadge>

          <div
            className="flex items-center gap-1.5 rounded-lg border border-accent-secondary/20 bg-black/30 px-2.5 py-1"
            title="实时 LLM 活动：调用次数 · token 总量 · 最近时延"
            data-testid="contextos-resource-chip"
          >
            <Activity className="h-3.5 w-3.5 text-accent-secondary" />
            <span className="font-mono text-[11px] font-bold text-text-main">{model.calls.toLocaleString()}</span>
            <span className="text-[9px] font-bold uppercase tracking-wider text-accent-secondary/70">调用</span>
            {model.totalTokens > 0 && (
              <>
                <span className="text-text-dim/60">·</span>
                <Coins className="h-3 w-3 text-gold" />
                <span className="font-mono text-[11px] font-bold text-text-main">{model.totalTokens.toLocaleString()}</span>
                <span className="text-[9px] font-bold uppercase tracking-wider text-gold/70">tok</span>
              </>
            )}
            {model.realLatencyMs !== null && (
              <span className="font-mono text-[10px] text-text-muted">· {model.realLatencyMs}ms</span>
            )}
          </div>

          <StatusBadge
            color={model.telemetryActive ? (telemetryFresh ? 'success' : 'warning') : 'default'}
            variant="dot"
            pulse={model.telemetryActive && telemetryFresh}
          >
            <span
              className="font-mono text-[10px]"
              title="ContextOS 遥测：WebSocket /v2/ws/runtime，经 Nats-JetStream 推送；时间为最近一条事件"
              data-testid="contextos-telemetry-freshness"
            >
              {model.telemetryActive
                ? `${telemetryFresh ? '实时遥测' : '遥测'}${freshnessLabel ? ` · ${freshnessLabel}` : ''}`
                : '遥测待命'}
              <span className="ml-1 text-text-dim/70">· {wsLabel}</span>
            </span>
          </StatusBadge>

          {gatePassed !== undefined && (
            <span
              className={cn(
                'h-2 w-2 rounded-full',
                gatePassed ? 'bg-status-success' : 'bg-status-warning',
              )}
              title={`质量门 ${gatePassed ? 'PASS' : 'HOLD'} · ${gateSource}`}
            />
          )}

          {controlPlaneProjection && (
            <>
              <div
                className={cn(
                  'flex items-center gap-1.5 rounded-lg border px-2.5 py-1 font-mono text-[10px]',
                  controlPlaneProjection.ok
                    ? 'border-cyan-400/30 bg-cyan-400/10 text-cyan-100'
                    : 'border-amber-400/30 bg-amber-400/10 text-amber-100',
                )}
                data-testid="contextos-control-plane-projection"
                title={controlPlaneProjection.detail}
              >
                <ShieldCheck className="h-3.5 w-3.5" />
                <span>{controlPlaneProjectionLabel(controlPlaneProjection)}</span>
                <span className="text-text-dim/60">·</span>
                <span>{controlPlaneProjectionSummary(controlPlaneProjection)}</span>
                <span className="text-text-dim/70">{controlPlaneSourceSummary(controlPlaneProjection)}</span>
              </div>
              <div
                className={cn(
                  'flex items-center gap-1.5 rounded-lg border px-2.5 py-1 font-mono text-[10px]',
                  controlPlaneProjection.evidence_policy?.ok
                    ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-100'
                    : 'border-slate-500/30 bg-slate-500/10 text-slate-200',
                )}
                data-testid="contextos-evidence-policy"
                title={evidencePolicySummary(controlPlaneProjection)}
              >
                <Gauge className="h-3.5 w-3.5" />
                <span>{evidencePolicyLabel(controlPlaneProjection)}</span>
                <span className="text-text-dim/60">·</span>
                <span>{evidencePolicySummary(controlPlaneProjection)}</span>
              </div>
            </>
          )}

          <Button
            variant={showStructure ? 'default' : 'outline'}
            size="sm"
            onClick={() => setShowStructure((value) => !value)}
            data-testid="contextos-structure-toggle"
            title="打开 ContextOS 真实上下文结构"
            aria-pressed={showStructure}
            className={cn(
              showStructure
                ? 'bg-accent-secondary text-bg hover:bg-accent-secondary/90'
                : 'border-accent-secondary/30 text-accent-secondary hover:bg-accent-secondary/10',
            )}
          >
            <Database className="mr-1.5 h-3.5 w-3.5" />
            上下文结构
          </Button>
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
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
          {/* LEFT/CENTER — pipeline hero + roles + decision log */}
          <div className="flex flex-col gap-4">
            <SectionCard
              title="系统流转与数据流图"
              subtitle="实时上下文装配管线 (Context Pipeline)"
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
                      <PipelineNode
                        stage={stage}
                        selected={pipelineDetailId === stage.id}
                        onSelect={() => setPipelineDetailId(stage.id)}
                      />
                    </div>
                  ))}
                  <FlowArrow active={pipelineLive} />
                  <PipelineNode
                    stage={receiptStage}
                    selected={pipelineDetailId === receiptStage.id}
                    onSelect={() => setPipelineDetailId(receiptStage.id)}
                  />
                </div>
                {idle && (
                  <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <div className="flex flex-col items-center gap-1.5 rounded-full border border-white/10 bg-black/50 px-4 py-2 backdrop-blur-sm">
                      <div className="flex items-center gap-2">
                        <Network className="h-3.5 w-3.5 text-text-dim/50" />
                        <span className="font-heading text-xs tracking-widest text-text-dim">
                          空闲 · 等待运行
                        </span>
                      </div>
                      <span className="text-[9px] text-text-dim/50">
                        启动 PM 或 Director 后管线将激活
                      </span>
                    </div>
                  </div>
                )}
                {/* 窄屏下提示右侧仍有节点（Receipt 反馈闭环）可横向滚动查看 */}
                <div className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-bg-panel/70 to-transparent xl:hidden" aria-hidden />
              </div>
            </SectionCard>

            {showStructure && <ContextStructurePanel model={model} telemetry={telemetry} />}

            {model.hasWorkers && model.workers.length > 0 && (
              <WorkerPanel
                workers={model.workers}
                onViewContext={(ref, workerId) => {
                  setViewerHash(ref);
                  setViewerRole('director');
                  setViewerWorkerId(workerId);
                }}
              />
            )}

            <SectionCard
              title="角色信号面"
              subtitle={`RoleSignalPlane · ${model.roles.length} 主角色`}
              icon={Boxes}
              action={<span className="text-[10px] text-text-dim">点击角色查看内部 ContextOS 状态</span>}
            >
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
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
                <RoleInternalPanel
                  role={model.roles.find((r) => r.id === activeRole)!}
                  onViewContext={(hash) => {
                    setViewerHash(hash);
                    setViewerRole(activeRole);
                    setViewerWorkerId(null);
                  }}
                />
              )}
            </SectionCard>

            <SectionCard
              title="决策 / 回执流"
              subtitle={
                model.telemetryActive
                  ? activeRole
                    ? `实时事件流 · 仅 ${activeRole.toUpperCase()}`
                    : '实时事件流 · Nats-JetStream'
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
                          {model.usageSourceLabel}
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
                  <div
                    className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-white/10 px-3 py-4 text-center"
                    data-testid="contextos-tokens-unavailable"
                  >
                    <Coins className="h-5 w-5 text-text-dim/30" />
                    <div className="text-[11px] text-text-dim">
                      <span className="font-medium">等待首次 LLM 调用</span>
                    </div>
                    <div className="text-[10px] text-text-dim/60">
                      实时 token 随 journal 流到达
                    </div>
                  </div>
                )}
                  </div>
                </div>

                {model.totalTokens > 0 ? (
                  <div className="space-y-2.5">
                    {model.budget.map((slice) => (
                      <BudgetBar key={slice.key} label={slice.label} tokens={slice.tokens} ratio={slice.ratio} colorClass={slice.colorClass} />
                    ))}
                    <UsageBreakdownChips
                      promptTokens={model.promptTokens}
                      completionTokens={model.completionTokens}
                      cachedTokens={model.cachedTokens}
                      cacheCreationTokens={model.cacheCreationTokens}
                      cacheReadTokens={model.cacheReadTokens}
                      toolTokens={model.toolTokens}
                      reasoningTokens={model.reasoningTokens}
                      audioTokens={model.audioTokens}
                      serverToolUseCount={model.serverToolUseCount}
                    />
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-1.5 rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-[11px] text-text-dim">
                    <Coins className="h-4 w-4 text-text-dim/30" />
                    <span>等待首次调用 · 暂无 token 用量</span>
                  </div>
                )}

                {/* Context window occupancy (estimated) */}
                <div className="space-y-1 border-t border-white/[0.06] pt-3">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="flex min-w-0 items-center gap-1 text-text-muted">
                      <Gauge className="h-3 w-3 shrink-0" />
                      <span className="truncate">上下文窗口占用</span>
                      <span
                        className={cn(
                          'shrink-0 rounded px-1 text-[9px]',
                          hasBudgetWindowUsage ? 'bg-white/5 text-text-dim' : 'bg-status-warning/10 text-status-warning',
                        )}
                      >
                        {hasBudgetWindowUsage ? budgetWindowOccupancyLabel : '未观测'}
                      </span>
                    </span>
                    <span className="font-mono text-text-main">{hasBudgetWindowUsage ? `${Math.round(budgetWindowOccupancy * 100)}%` : '—'}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-white/5">
                    <div
                      className={cn(
                        'h-full rounded-full transition-all duration-500',
                        budgetWindowOccupancy > 0.85 ? 'bg-status-error' : budgetWindowOccupancy > 0.6 ? 'bg-status-warning' : 'bg-accent-secondary',
                      )}
                      style={{ width: hasBudgetWindowUsage ? `${Math.max(2, Math.round(budgetWindowOccupancy * 100))}%` : '0%' }}
                    />
                  </div>
                  <div
                    className="flex items-center justify-end gap-1 text-right font-mono text-[9px] text-text-dim"
                    data-testid="contextos-window-source"
                    data-usage-state={hasBudgetWindowUsage ? 'observed' : 'none'}
                    title={budgetWindowOccupancyDetail}
                  >
                    <span>{budgetWindowOccupancyTokens !== null ? `~${contextOSFormat.tokens(budgetWindowOccupancyTokens)}` : '无 usage'}</span>
                    <span>/</span>
                    <span>{budgetWindowTokens !== null ? contextOSFormat.windowTokens(budgetWindowTokens) : '未知'}</span>
                    <span className="max-w-[120px] truncate">{budgetWindowOccupancyLabel}</span>
                    <span className="max-w-[170px] truncate">{budgetWindowLabel}</span>
                  </div>
                </div>

                {visibleBudgetBindingRows.length > 0 && (
                  <div
                    className="space-y-2 border-t border-white/[0.06] pt-3"
                    data-testid="contextos-binding-budgets"
                  >
                    <div className="flex items-center justify-between gap-2 text-[11px]">
                      <span className="flex min-w-0 items-center gap-1 text-text-muted">
                        <Cpu className="h-3 w-3 shrink-0" />
                        <span className="truncate">
                          {selectedRole ? `${selectedRole.title} 模型预算` : '模型预算'}
                        </span>
                      </span>
                      <span className="font-mono text-[9px] text-text-dim">
                        {budgetBindingRows.length} 路
                      </span>
                    </div>
                    <div className="space-y-1.5">
                      {visibleBudgetBindingRows.map((row) => (
                        <BindingBudgetRow key={row.id} row={row} />
                      ))}
                    </div>
                    {budgetBindingRows.length > visibleBudgetBindingRows.length && (
                      <div className="text-right font-mono text-[9px] text-text-dim">
                        仅显示前 {visibleBudgetBindingRows.length} 路
                      </div>
                    )}
                  </div>
                )}
              </div>
            </SectionCard>

            {model.eventTypes.length > 0 && (
              <SectionCard title="事件类型分布" subtitle="Event Types · 真实观测" icon={Activity}>
                <div data-testid="contextos-event-types">
                  <EventTypeDistribution slices={model.eventTypes} total={model.eventTypesTotal} />
                </div>
              </SectionCard>
            )}

            <ContextStoreStatsPanel workspace={workspace} refreshSignal={contextStoreRefreshSignal} />
          </div>
        </div>
      </main>

      {selectedPipelineStage && (
        <PipelineDetailModal
          stage={selectedPipelineStage}
          model={model}
          telemetry={telemetry}
          onClose={() => setPipelineDetailId(null)}
        />
      )}

      {/* Context Viewer Modal */}
      {viewerHash && (
        <ContextViewerModal
          contextSnapshotRef={viewerHash}
          roleId={viewerRole}
          workerId={viewerWorkerId}
          onClose={() => {
            setViewerHash(null);
            setViewerRole('');
            setViewerWorkerId(null);
          }}
        />
      )}
    </div>
  );
}
