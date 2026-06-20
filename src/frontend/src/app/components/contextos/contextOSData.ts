/**
 * ContextOS 实时视图 - 数据派生层
 *
 * 该模块把 App 已有的真实运行时数据（usageStats / dialogueEvents / executionLogs /
 * snapshot / llmRuntimeState / phase）派生成 ContextOS 仪表盘所需的展示模型。
 *
 * 设计原则（诚实优先）：
 *  - 能从真实 props 计算的指标 → 直接计算（real）。
 *  - 图中暗示但无真实数据源的指标 → 标注「估算」(estimated)，绝不伪造精度。
 */

import type { UsageStats } from '@/app/components/UsageHUD';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';
import type { LlmRuntimeGateState, LlmRuntimeRoleBinding, LlmRuntimeRoleDetail } from '@/app/hooks/useLlmRuntimeGate';
import type { SnapshotPayload } from '@/app/types/appContracts';
import {
  EMPTY_TELEMETRY,
  filterEventsForRole,
  telemetryRoleEvents,
  telemetryRoleHasUsageChannel,
  telemetryRoleTokens,
  type ContextOSEvent,
  type ContextOSTelemetry,
  type WorkerAggregate,
} from './contextOSTelemetry';

/** 无绑定窗口时的兜底上下文窗口；真实显示优先使用 /v2/llm/status 的角色绑定窗口。 */
export const NOMINAL_CONTEXT_WINDOW = 128_000;

export type PipelineState = 'active' | 'idle' | 'blocked';

export interface PipelineStage {
  id: string;
  label: string;
  /** 对应的真实后端组件名 */
  component: string;
  /** 该节点展示的一句话职责 */
  hint: string;
  state: PipelineState;
  /** 节点上展示的实时小指标（可空） */
  metric: string;
}

export interface ComponentHealth {
  id: string;
  name: string;
  component: string;
  state: PipelineState;
  metric: string;
  /** 0..1 强度条（真实占比；无意义时为 null 不渲染） */
  intensity: number | null;
}

export interface BudgetSlice {
  key: string;
  label: string;
  tokens: number;
  ratio: number;
  /** tailwind 颜色类（背景） */
  colorClass: string;
}

/** 真实事件类型分布的一段（来自观测事件的 category 归类）。 */
export interface EventTypeSlice {
  key: string;
  label: string;
  count: number;
  ratio: number;
  colorClass: string;
}

export interface RoleInternalContext {
  roleId: string;
  title: string;
  courtTitle: string;
  state: PipelineState;
  events: ContextOSEvent[];
  eventCount: number;
  projectionCount: number;
  receiptCount: number;
  contextItemsCount: number | null;
  workingMemoryItems: number | null;
  workingMemoryEstimated: boolean;
  contextTokensLatest: number | null;
  contextWindowTokens: number | null;
  contextWindowLabel: string;
  contextWindowDetail: string;
  /** 当前窗口占用的角色级分子。优先真实 context token，其次该角色平均 prompt token；无数据为 null。 */
  windowOccupancyTokens: number | null;
  windowOccupancyLabel: string;
  windowOccupancyDetail: string;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  calls: number;
  lastEventAt: number | null;
  currentTaskId: string | null;
  currentTaskTitle: string | null;
  /** Reference to the most recent stored full context (click to fetch detail). */
  latestContextSnapshotRef: string | null;
  /** Most recent call ID for this role. */
  latestCallId: string | null;
  /** Most recent turn ID for this role. */
  latestTurnId: string | null;
  detail: string;
}

export interface RoleCard {
  id: string;
  title: string;
  /** 古风官职名 */
  courtTitle: string;
  tokens: number;
  state: PipelineState;
  detail: string;
  /**
   * 该角色的 token 是否来自真实带 usage 的观测通道。
   * 只有该角色自己的实时事件携带 usage 时才归并 token；无 usage 通道时
   * detail 以事件数/无观测呈现，不冒充 token 归因。
   */
  tokensReal: boolean;
  /** 是否为只读辅助角色 */
  readOnly?: boolean;
  lastEventAt: number | null;
  projectionCount: number;
  contextItemsCount: number | null;
  contextWindowTokens: number | null;
  contextWindowLabel: string;
  contextWindowDetail: string;
  receiptCount: number;
  internalContext: RoleInternalContext;
}

export interface DecisionRow {
  id: string;
  time: string;
  actor: string;
  kind: string;
  summary: string;
  tone: 'info' | 'success' | 'warning' | 'error';
  /** 数据来源：真实观测流 / 对话 / 日志。 */
  source: 'telemetry' | 'dialogue' | 'log';
  /** 该事件的真实 token 用量（仅 telemetry 来源且有 usage 时）。 */
  tokens?: number;
  /** 真实时延（ms，仅 telemetry 来源且有 duration 时）。 */
  latencyMs?: number | null;
  /** 是否落盘了上下文快照（output.context_snapshot）。 */
  receipt?: boolean;
}

/**
 * Phase 3+：单 worker 的实时 LLM 追踪卡。
 * 字段完全派生自真实遥测（journal `llm` 通道的 meta.worker_id / meta.workerId）。
 * 无 worker 归属时聚合为空（hasWorkers=false，UI 据实降级）。
 */
export interface WorkerCard {
  workerId: string;
  role: string;
  tokens: number;
  calls: number;
  events: number;
  /** 真实时延（ms），无则 null。 */
  latencyMs: number | null;
  /** 最近活动 epoch（ms），无则 null。 */
  lastEpoch: number | null;
  /** Pipeline 状态（有事件 → active；纯空 → idle）。 */
  state: PipelineState;
  /** 该 worker 命中的最近一次 context snapshot ref（按事件截取）。 */
  latestContextSnapshotRef: string | null;
}

export interface ContextOSModel {
  running: boolean;
  /** 无运行、无 token、无事件 → 数据空闲（用于「空闲」水印，避免陈旧数据被误读为实时） */
  dataIdle: boolean;
  /** error 级日志 + 失败对话事件计数 */
  errorCount: number;
  /** 从最近日志解析出的单次调用时延（毫秒），无则 null */
  lastLatencyMs: number | null;
  /** PM 迭代轮次（来自 snapshot.pm_state.pm_iteration / run_id），无则 null */
  iteration: number | null;
  /** 任务看板任务数（来自 snapshot.tasks） */
  taskCount: number;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  /** token 是否来自实时遥测（journal `llm` 通道）。false = 退回用量统计通道（非实时）或无数据。 */
  tokensRealtime: boolean;
  calls: number;
  avgPerCall: number;
  /** 估算的单次上下文窗口占用比例 0..1 */
  windowOccupancy: number;
  windowOccupancyTokens: number;
  /** 用于全部视角的窗口分母：优先取已绑定角色窗口的最小值，无数据为 null（不冒充 128k）。 */
  contextWindowTokens: number | null;
  contextWindowLabel: string;
  contextWindowDetail: string;
  contextWindowSource: 'binding' | 'unknown';
  /** 是否绑定了真实实时遥测（WebSocket 运行时流有事件）。 */
  telemetryActive: boolean;
  /** 真实 ProjectionEngine 投影次数（telemetry 优先，无则回退 calls 估算）。 */
  projectionCount: number;
  /** 真实落盘的上下文快照数（output.context_snapshot 计数）。 */
  receiptCount: number;
  /** 真实平均/最近时延（ms），无则 null。 */
  realLatencyMs: number | null;
  /** 最近真实事件 epoch（毫秒），无则 null。 */
  lastTelemetryEpoch: number | null;
  /** 由后端字符估算得到 usage 的调用数（output.usage.estimated），用于在 token 总量旁标「含估算」。 */
  estimatedCalls: number;
  /** 最近一次 context.build 装配的真实上下文项数（WorkingMem 在窗项数），无则 null。 */
  contextItemsCount: number | null;
  /** 遥测是否只覆盖尾部窗口（解析行数达读取上限）；用于把「累计」诚实降级为「最近窗口」。 */
  telemetryWindowed: boolean;
  pipeline: PipelineStage[];
  components: ComponentHealth[];
  budget: BudgetSlice[];
  byModeSlices: BudgetSlice[];
  /** 真实事件类型分布（projection/call/tool/state/error），仅遥测激活时非空。 */
  eventTypes: EventTypeSlice[];
  /** 事件类型分布的统计基数（= 最近事件窗口大小）。 */
  eventTypesTotal: number;
  roles: RoleCard[];
  decisions: DecisionRow[];
  policies: string[];
  /** Phase 3+：多 worker LLM 追踪卡（按 worker_id 聚合）。无 worker 归属时为空数组。 */
  workers: WorkerCard[];
  /** 是否识别到任何带 worker_id 的真实事件（用于 UI 判断是否展示多 worker 面板）。 */
  hasWorkers: boolean;
}

/** 事件类型 → 展示标签与颜色（与解析层 category 一致）。 */
const EVENT_TYPE_META: ReadonlyArray<{ key: ContextOSEvent['category']; label: string; colorClass: string }> = [
  { key: 'projection', label: '投影', colorClass: 'bg-accent-secondary' },
  { key: 'call', label: '调用', colorClass: 'bg-gold' },
  { key: 'tool', label: '工具', colorClass: 'bg-accent' },
  { key: 'state', label: '状态', colorClass: 'bg-status-info' },
  { key: 'error', label: '错误', colorClass: 'bg-status-error' },
  { key: 'event', label: '其他', colorClass: 'bg-text-dim' },
];

/** 角色 → Decision Log 中 actor/speaker 的匹配别名（用于角色页签交叉过滤）。 */
export const ROLE_DECISION_ALIASES: Record<string, string[]> = {
  pm: ['pm'],
  architect: ['architect'],
  chief_engineer: ['chief', 'engineer'],
  director: ['director'],
  qa: ['qa', 'reviewer'],
};

/**
 * ContextOS 角色信号面对应的 5 个主角色（与后端 `ROLE_PROMPT_TEMPLATES` /
 * 统一角色对话 API「所有 5 个角色」一致）。scout 为只读辅助 sub-agent，按设计不入此面。
 */
const ROLE_DEFINITIONS: ReadonlyArray<{ id: string; key: string; courtTitle: string; title: string }> = [
  { id: 'pm', key: 'pm', courtTitle: '尚书令', title: 'PM' },
  { id: 'architect', key: 'architect', courtTitle: '中书令', title: 'Architect' },
  { id: 'chief_engineer', key: 'chief_engineer', courtTitle: '工部尚书', title: 'Chief Eng' },
  { id: 'director', key: 'director', courtTitle: '工部侍郎', title: 'Director' },
  { id: 'qa', key: 'qa', courtTitle: '门下侍中', title: 'QA' },
];
/** 主角色总数（角色信号面 N/N 角色的分母）。 */
const ROLE_COUNT = ROLE_DEFINITIONS.length;

/** 判断某条决策记录是否属于给定角色（roleId 为 null 时表示「全部」）。 */
export function decisionMatchesRole(actor: string, roleId: string | null): boolean {
  if (!roleId) return true;
  const aliases = ROLE_DECISION_ALIASES[roleId];
  if (!aliases) return true;
  const lowered = actor.toLowerCase();
  return aliases.some((alias) => lowered.includes(alias));
}

function safeNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function formatTokens(value: number): string {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  }
  return String(Math.round(value));
}

function formatWindowTokens(value: number): string {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(value % 1_000_000 === 0 ? 0 : 1)}M`;
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 100_000 ? 0 : 1)}k`;
  }
  return String(Math.round(value));
}

function modelLabel(value: string): string {
  const text = String(value || '').trim();
  if (!text) return '';
  return text.length > 26 ? `${text.slice(0, 23)}...` : text;
}

function roleDetailForWindow(state: LlmRuntimeGateState, roleKey: string): LlmRuntimeRoleDetail | undefined {
  const details = state.roleDetails || {};
  return details[roleKey] || (roleKey === 'architect' ? details.docs : undefined);
}

function bindingWindow(binding: LlmRuntimeRoleBinding): number | null {
  return typeof binding.maxContextTokens === 'number' && Number.isFinite(binding.maxContextTokens) && binding.maxContextTokens > 0
    ? binding.maxContextTokens
    : null;
}

function deriveRoleContextWindow(
  state: LlmRuntimeGateState,
  roleKey: string,
): { tokens: number | null; label: string; detail: string } {
  const detail = roleDetailForWindow(state, roleKey);
  if (!detail) {
    return { tokens: null, label: '窗口未知', detail: 'LLM status 未提供角色绑定窗口' };
  }

  const bindingWindows = detail.bindings
    .map((binding) => bindingWindow(binding))
    .filter((value): value is number => typeof value === 'number');
  const directWindow = typeof detail.maxContextTokens === 'number' && detail.maxContextTokens > 0
    ? detail.maxContextTokens
    : null;
  const tokens = bindingWindows.length > 0 ? Math.min(...bindingWindows) : directWindow;
  const provider = detail.providerName || detail.providerId || 'Provider';
  const model = detail.model || detail.bindings[0]?.model || '';
  const bindingCount = detail.bindings.length;

  if (!tokens) {
    return {
      tokens: null,
      label: model ? `${modelLabel(model)} 窗口未知` : '窗口未知',
      detail: `${provider}${model ? ` / ${model}` : ''}`,
    };
  }

  return {
    tokens,
    label: bindingCount > 1 ? `${bindingCount} 路最小窗口` : '绑定窗口',
    detail: `${provider}${model ? ` / ${model}` : ''}`,
  };
}

function formatClock(raw: string | undefined): string {
  if (!raw) return '--:--:--';
  const epoch = Date.parse(raw);
  if (!Number.isFinite(epoch)) {
    // 已是 HH:MM:SS 形式则原样返回
    return raw.length > 12 ? raw.slice(11, 19) : raw;
  }
  return new Date(epoch).toLocaleTimeString('zh-CN', { hour12: false });
}

/** 从最近的执行日志中解析单次调用时延（形如 "1234 ms" / "1234ms"）。 */
function parseLatencyMs(logs: LogEntry[]): number | null {
  const start = Math.max(0, logs.length - 24);
  for (let i = logs.length - 1; i >= start; i--) {
    const text = `${logs[i]?.message ?? ''} ${logs[i]?.details ?? ''}`;
    const match = text.match(/(\d{2,7})\s*ms\b/i);
    if (match) {
      const value = Number.parseInt(match[1], 10);
      if (Number.isFinite(value)) return value;
    }
  }
  return null;
}

function phaseToActiveStage(phase: string, running: boolean): string | null {
  const token = (phase || '').trim().toLowerCase();
  if (!running) return null;
  if (token.includes('plan')) return 'projection';
  if (token.includes('dispatch')) return 'budget';
  if (token.includes('implement') || token.includes('exec') || token.includes('code')) return 'llm';
  if (token.includes('review') || token.includes('qa') || token.includes('test')) return 'telemetry';
  return 'working_mem';
}

/** 把时间戳解析成可比较的 epoch（毫秒）；不可解析 → 0（排到末尾，保留插入序）。 */
function parseEpoch(raw: string | undefined): number {
  if (!raw) return 0;
  const epoch = Date.parse(raw);
  return Number.isFinite(epoch) ? epoch : 0;
}

function deriveDecisions(dialogueEvents: DialogueEvent[], executionLogs: LogEntry[]): DecisionRow[] {
  const rows: Array<{ row: DecisionRow; epoch: number }> = [];

  dialogueEvents.slice(-14).forEach((event, index) => {
    const speaker = String(event.speaker || 'System');
    const kind = String(event.type || event.refs?.phase || 'message');
    const tone: DecisionRow['tone'] = /error|fail/i.test(kind)
      ? 'error'
      : /warn|block/i.test(kind)
        ? 'warning'
        : /done|complete|pass|success/i.test(kind)
          ? 'success'
          : 'info';
    rows.push({
      epoch: parseEpoch(event.timestamp),
      row: {
        id: event.eventId || `dlg-${event.seq ?? index}`,
        time: formatClock(event.timestamp),
        actor: speaker,
        kind,
        summary: String(event.content || '').replace(/\s+/g, ' ').trim().slice(0, 120),
        tone,
        source: 'dialogue',
      },
    });
  });

  executionLogs.slice(-6).forEach((log, index) => {
    const tone: DecisionRow['tone'] = log.level === 'error'
      ? 'error'
      : log.level === 'warning'
        ? 'warning'
        : log.level === 'success'
          ? 'success'
          : 'info';
    rows.push({
      epoch: parseEpoch(log.timestamp),
      row: {
        id: log.id || `log-${index}`,
        time: formatClock(log.timestamp),
        actor: String(log.source || 'runtime'),
        kind: String(log.level || 'info'),
        summary: String(log.message || '').replace(/\s+/g, ' ').trim().slice(0, 120),
        tone,
        source: 'log',
      },
    });
  });

  // 真·按时间倒序合并（Array.sort 稳定：等时/不可解析项保留插入序），最多 10 条。
  return rows
    .sort((a, b) => b.epoch - a.epoch)
    .slice(0, 10)
    .map((entry) => entry.row);
}

/** ContextOS 真实观测事件 → 决策行（事件已按时间倒序）。 */
function telemetryDecisionTone(event: ContextOSEvent): DecisionRow['tone'] {
  if (event.category === 'error') return 'error';
  if (event.isProjection || event.hasReceipt) return 'success';
  if (event.category === 'call') return 'info';
  return 'info';
}

function telemetryDecisionKind(event: ContextOSEvent): string {
  if (event.name) return event.name;
  if (event.isProjection) return 'projection';
  return event.kind || 'observation';
}

function deriveTelemetryDecisions(telemetry: ContextOSTelemetry, limit = 12): DecisionRow[] {
  return telemetry.events.slice(0, limit).map((event) => ({
    id: event.id,
    time: formatClock(event.ts) || '--:--:--',
    actor: event.actor,
    kind: telemetryDecisionKind(event),
    summary: event.summary || telemetryDecisionKind(event),
    tone: telemetryDecisionTone(event),
    source: 'telemetry' as const,
    tokens: event.hasUsage ? event.totalTokens : undefined,
    latencyMs: event.durationMs,
    receipt: event.hasReceipt,
  }));
}

export function buildContextOSModel(input: {
  usageStats: UsageStats | null;
  dialogueEvents: DialogueEvent[];
  executionLogs: LogEntry[];
  snapshot: SnapshotPayload | null;
  llmRuntimeState: LlmRuntimeGateState;
  currentPhase: string;
  pmRunning: boolean;
  directorRunning: boolean;
  /** 真实运行时遥测（派生自 useRuntime 经 WebSocket 实时推送的运行时流）；缺省时退回 usageStats 代理。 */
  telemetry?: ContextOSTelemetry | null;
}): ContextOSModel {
  const {
    usageStats,
    dialogueEvents,
    executionLogs,
    snapshot,
    llmRuntimeState,
    currentPhase,
    pmRunning,
    directorRunning,
  } = input;

  const telemetry = input.telemetry ?? EMPTY_TELEMETRY;
  const telemetryActive = telemetry.hasData;

  const running = pmRunning || directorRunning;

  // 真实快照派生：任务看板规模 + PM 迭代轮次。
  const snapshotTasks = Array.isArray(snapshot?.tasks)
    ? snapshot.tasks.filter((task) => Boolean(task && typeof task === 'object'))
    : [];
  const taskCount = snapshotTasks.length;
  const pmState = snapshot?.pm_state && typeof snapshot.pm_state === 'object'
    ? (snapshot.pm_state as Record<string, unknown>)
    : null;
  const rawIteration = pmState?.['pm_iteration'];
  const iteration = typeof rawIteration === 'number' && Number.isFinite(rawIteration)
    ? rawIteration
    : typeof rawIteration === 'string' && Number.isFinite(Number(rawIteration))
      ? Number(rawIteration)
      : null;
  // 数据来源（全部来自既有 WS 实时框架）：
  //  - 活动指标（调用 / 投影 / 时延 / 事件 / 错误）来自实时遥测——随事件到达即更新。
  //  - 真实 token 来自 journal `llm` 通道（raw.data.prompt/completion_tokens），同样实时送达；
  //    故遥测激活且有 token 时以遥测为准。仅当无实时 token 时，退回 usageStats（用量统计通道）作
  //    best-effort，且仅在确有数值时呈现，绝不把 0 或估算冒充成实时精确 token。
  const usageTotals = usageStats?.totals ?? { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
  const totalTokens = telemetryActive && telemetry.totalTokens > 0
    ? telemetry.totalTokens
    : safeNumber(usageTotals.total_tokens);
  const promptTokens = telemetryActive && telemetry.promptTokens > 0
    ? telemetry.promptTokens
    : safeNumber(usageTotals.prompt_tokens);
  const completionTokens = telemetryActive && telemetry.completionTokens > 0
    ? telemetry.completionTokens
    : safeNumber(usageTotals.completion_tokens);
  // token 是否实时（来自 journal `llm` 通道的真实 usage）；否则退回用量统计通道（非实时）。
  const tokensRealtime = telemetryActive && telemetry.totalTokens > 0;
  const calls = telemetryActive ? telemetry.totalCalls : safeNumber(usageStats?.calls);
  const byMode: Record<string, { total_tokens: number; calls: number }> = telemetryActive
    ? Object.fromEntries(
        Object.entries(telemetry.byMode).map(([key, value]) => [key, { total_tokens: value.totalTokens, calls: value.calls }]),
      )
    : usageStats?.by_mode ?? {};
  const avgPerCall = calls > 0 && totalTokens > 0 ? Math.round(totalTokens / calls) : 0;

  // 真实 ContextOS 内部计数（来自 WS 实时遥测）。
  // projectionCount = 实时流里识别为上下文装配/投影的事件（context.build / prompt_context / projection）。
  const projectionCount = telemetryActive ? telemetry.projectionCount : 0;
  // receiptCount / contextItemsCount：来自 runtime_events 的 context.snapshot/context.build 结构化签名；
  // 后端未发这些事件（如弱模型 PM-only run 仅发 prompt_context）时诚实为 0 / null，不臆造。
  const receiptCount = telemetry.receiptCount;
  const realLatencyMs = telemetry.lastLatencyMs ?? telemetry.avgLatencyMs;
  const lastTelemetryEpoch = telemetry.lastEventEpoch;
  // 字符估算的调用数与 token 同源：实时 token（journal usage 为真实计数）→ 遥测 estimatedCalls（0）；
  // token 退回用量统计通道时 → 用量统计的 estimated_calls。
  const estimatedCalls = tokensRealtime ? telemetry.estimatedCalls : safeNumber(usageStats?.estimated_calls);
  const contextItemsCount = telemetry.contextItemsCount;
  // 遥测只看到尾部窗口（某条 WS 流已达环形缓冲上限）时为 true：把「累计」诚实降级为「最近窗口」。
  const telemetryWindowed = telemetry.windowed;

  const roleWindowByKey: Record<string, { tokens: number | null; label: string; detail: string }> = Object.fromEntries(
    ROLE_DEFINITIONS.map((role) => [role.key, deriveRoleContextWindow(llmRuntimeState, role.key)] as const),
  );
  const configuredRoleWindows = Object.values(roleWindowByKey)
    .map((entry) => entry.tokens)
    .filter((value): value is number => typeof value === 'number' && value > 0);
  const contextWindowTokens = configuredRoleWindows.length > 0 ? Math.min(...configuredRoleWindows) : null;
  const contextWindowSource: ContextOSModel['contextWindowSource'] = configuredRoleWindows.length > 0 ? 'binding' : 'unknown';
  const contextWindowLabel = contextWindowSource === 'binding' ? '最小绑定窗口' : '未知';
  const contextWindowDetail = contextWindowSource === 'binding'
    ? '按当前角色绑定中的最小 max_context_tokens 估算'
    : 'LLM status 未提供窗口字段，显示未知';

  // 估算单次上下文窗口占用：以「平均单次提示 token」近似当前窗口压力。
  const avgPromptPerCall = calls > 0 ? promptTokens / calls : 0;
  const totalContextTokens = promptTokens;  // 真实上下文 token
  const windowOccupancyTokens = Math.round(avgPromptPerCall);  // 平均 prompt 估算
  const windowOccupancy = contextWindowTokens !== null ? Math.max(0, Math.min(1, windowOccupancyTokens / contextWindowTokens)) : 0;

  const blockedRoles = new Set(llmRuntimeState.blockedRoles.map((role) => role.toLowerCase()));
  const llmBlocked = llmRuntimeState.state === 'BLOCKED';

  // 由最近一条真实遥测事件推断「正在活动」的管线阶段（投影 / 调用 / 记忆窗口）。
  const latestEvent = telemetry.events[0];
  const impliedStage =
    telemetryActive && latestEvent
      ? latestEvent.isProjection
        ? 'projection'
        : latestEvent.category === 'call' || latestEvent.category === 'tool'
          ? 'llm'
          : 'working_mem'
      : null;
  const activeStageId = phaseToActiveStage(currentPhase, running) ?? impliedStage;
  const stateFor = (id: string): PipelineState => {
    if (llmBlocked && (id === 'llm' || id === 'prompt')) return 'blocked';
    if (id === 'role_signal' && blockedRoles.size > 0) return 'blocked';
    if (activeStageId === id) return 'active';
    return 'idle';
  };

  const eventCount = dialogueEvents.length;
  const logCount = executionLogs.length;
  // 用 parsedLines（解析到的全部观测条数）作为"观测"计数，而非被截断到 120 的事件流长度，
  // 避免出现"显示 120 观测 / 聚合按全量"的内部不一致。
  const telemetryEventCount = telemetryActive ? telemetry.parsedLines : telemetry.events.length;
  // WorkingMem「在窗项数」优先用真实 context.build 的 items_count；无则用事件流长度估算（明确标注）。
  const realWindowItems = telemetryActive ? telemetry.contextItemsCount : null;
  const windowItemsReal = realWindowItems !== null;
  const windowItems = windowItemsReal
    ? realWindowItems
    : Math.min(Math.max(eventCount, telemetry.events.length), 32);

  // 观测到活动 = PM/Director 运行中 或 真实遥测有数据（如一次角色对话）。
  const observed = running || telemetryActive;

  const errorCount = telemetryActive
    ? telemetry.errorCount
    : executionLogs.filter((log) => log.level === 'error').length +
      dialogueEvents.filter((event) => /error|fail/i.test(String(event.type || ''))).length;
  const lastLatencyMs = realLatencyMs ?? parseLatencyMs(executionLogs);
  const dataIdle = !observed && totalTokens === 0 && eventCount === 0 && logCount === 0;

  const turfEventTotal = Math.max(eventCount + logCount, telemetryEventCount);
  const contextTokensLatest = telemetryActive ? telemetry.contextTokensLatest : null;
  // 管线顺序忠实于后端真实装配流（gateway.py + ProjectionEngine 内部 7 段）：
  //   投影(ProjectionEngine.project，内部含 BudgetPlanner 预算「规划」) → 角色信号(supplemental_turns)
  //   → 装配(project()→messages) → CompressionEngine 预算「压缩兜底」(装配后最后一步) → LLM。
  // 预算分两处发生：规划在投影内部，压缩在装配之后——故 CompressionEngine 节点排在 prompt 之后。
  const pipeline: PipelineStage[] = [
    { id: 'request', label: '用户请求', component: 'UserTurn', hint: '进入的指令 / 反馈', state: observed ? 'active' : 'idle', metric: taskCount > 0 ? `${eventCount} 轮 · ${taskCount} 任务` : `${eventCount} 轮` },
    { id: 'truthlog', label: 'TruthLog', component: 'TruthLogService', hint: '事件真值流', state: stateFor('truthlog'), metric: `${turfEventTotal} 事件` },
    { id: 'working_mem', label: 'WorkingMem', component: 'WorkingMemoryWindow', hint: '活动上下文窗口', state: stateFor('working_mem'), metric: windowItemsReal ? `${windowItems} 项在窗` : `~${windowItems} 项 (估算)` },
    { id: 'projection', label: 'ProjectionEngine', component: 'ProjectionEngine', hint: '自适应排序投影 · 含预算规划', state: stateFor('projection'), metric: telemetryActive ? `${projectionCount} 投影` : `~${calls} 次 (估算)` },
    { id: 'role_signal', label: 'RoleSignalPlane', component: 'allocate_role_signals', hint: '角色信号注入', state: stateFor('role_signal'), metric: `${Math.max(0, ROLE_COUNT - blockedRoles.size)}/${ROLE_COUNT} 角色` },
    { id: 'prompt', label: 'Projection.project', component: 'project() → messages', hint: '消息装配', state: stateFor('prompt'), metric: `${formatTokens(promptTokens)} 提示` },
    { id: 'budget', label: 'CompressionEngine', component: 'CompressionEngine', hint: '装配后预算压缩兜底', state: stateFor('budget'), metric: contextTokensLatest !== null ? `${formatTokens(contextTokensLatest)} 上下文` : `${formatTokens(avgPerCall)} tok/次` },
    { id: 'llm', label: 'LLM Invoke', component: 'AIExecutor', hint: '模型调用', state: stateFor('llm'), metric: lastLatencyMs !== null ? `${formatTokens(completionTokens)} · ${lastLatencyMs}ms` : `${formatTokens(completionTokens)} 输出` },
  ];

  const componentIntensityBase = Math.max(promptTokens + completionTokens, 1);
  const components: ComponentHealth[] = [
    {
      id: 'truthlog', name: 'TruthLog', component: '真值事件流',
      state: observed ? 'active' : 'idle',
      metric: telemetryActive ? `${telemetryEventCount} 观测 · ${logCount} 日志` : `${eventCount} 轮 · ${logCount} 日志`,
      intensity: turfEventTotal > 0 ? Math.min(1, turfEventTotal / 48) : null,
    },
    {
      id: 'working_mem', name: 'WorkingMem', component: '活动窗口',
      state: observed ? 'active' : 'idle',
      // 真实遥测时用 context.build 的 items_count（真实在窗项数）；无则用事件流长度估算，明确标注。
      metric: windowItemsReal ? `${windowItems} 项在窗` : `~${windowItems} 项 (估算)`,
      intensity: windowItems > 0 ? Math.min(1, windowItems / 32) : null,
    },
    {
      id: 'projection', name: 'ProjectionEngine', component: '排序投影',
      state: observed ? 'active' : 'idle',
      // 真实遥测时显示真实投影数（context.build 全角色 + PM 注入）；无遥测时用调用数估算，明确标注。
      metric: telemetryActive ? `投影 ${projectionCount} 次` : `~${calls} 次 (估算)`,
      intensity: telemetryActive && projectionCount > 0 ? Math.min(1, projectionCount / 24) : null,
    },
    {
      // 注意：该卡的「就绪/受阻」来自 LLM 运行时门（某角色是否绑定了可用 provider），
      // 而非 RoleSignalPlane 的信号注入（后者不写观测日志，无法在此呈现）。据实命名为「LLM 角色门」。
      id: 'role_signal', name: 'LLM 角色门', component: 'LLM 绑定就绪',
      state: blockedRoles.size > 0 ? 'blocked' : observed ? 'active' : 'idle',
      metric: blockedRoles.size > 0 ? `${blockedRoles.size} 角色受阻` : `${Math.max(0, ROLE_COUNT - blockedRoles.size)} 角色就绪`,
      intensity: null,
    },
    {
      // avgPerCall = 累计 token / 调用数 = 事后实际平均消耗，并非 PhaseAwareBudgetPlanner 的事前预算分配
      // （后者不写观测日志）。据实命名为「平均消耗」，不冒充预算规划器。
      id: 'budget', name: '平均消耗', component: '单次平均用量',
      state: observed ? 'active' : 'idle',
      metric: `${formatTokens(avgPerCall)} tok / 次`,
      intensity: avgPerCall > 0 && contextWindowTokens !== null && contextWindowTokens > 0 ? Math.min(1, avgPerCall / contextWindowTokens * 4) : null,
    },
    {
      id: 'prompt', name: 'Projection.project', component: '消息装配',
      state: llmBlocked ? 'blocked' : observed ? 'active' : 'idle',
      metric: `${formatTokens(promptTokens)} 提示 tok`,
      intensity: promptTokens > 0 ? Math.min(1, promptTokens / componentIntensityBase) : null,
    },
    {
      id: 'telemetry', name: 'Receipt · Telemetry', component: '回执遥测',
      state: errorCount > 0 ? 'blocked' : llmBlocked ? 'blocked' : observed || llmRuntimeState.state === 'READY' ? 'active' : 'idle',
      metric: errorCount > 0
        ? `${errorCount} 错误 · ${calls} 调用`
        : receiptCount > 0
          ? `${receiptCount} 快照 · ${formatTokens(completionTokens)} 输出`
          : `${formatTokens(completionTokens)} 输出 tok`,
      intensity: completionTokens > 0 ? Math.min(1, completionTokens / componentIntensityBase) : null,
    },
  ];

  // 预算构成：真实的 提示/输出 二分（不伪造 System/Memory/Tool 细分）。
  const budget: BudgetSlice[] = [
    { key: 'prompt', label: '提示 (Prompt)', tokens: promptTokens, ratio: totalTokens > 0 ? promptTokens / totalTokens : 0, colorClass: 'bg-accent-secondary' },
    { key: 'completion', label: '输出 (Completion)', tokens: completionTokens, ratio: totalTokens > 0 ? completionTokens / totalTokens : 0, colorClass: 'bg-gold' },
  ];

  // 按模式分布（真实 by_mode），取 token 最高的前 6 个。
  const modePalette = ['bg-accent-secondary', 'bg-gold', 'bg-accent', 'bg-status-info', 'bg-status-success', 'bg-status-warning'];
  const byModeSlices: BudgetSlice[] = Object.entries(byMode)
    .map(([key, value]) => ({ key, tokens: safeNumber(value?.total_tokens) }))
    .filter((entry) => entry.tokens > 0)
    .sort((a, b) => b.tokens - a.tokens)
    .slice(0, 6)
    .map((entry, index) => ({
      key: entry.key,
      label: entry.key,
      tokens: entry.tokens,
      ratio: totalTokens > 0 ? entry.tokens / totalTokens : 0,
      colorClass: modePalette[index % modePalette.length],
    }));

  // 真实事件类型分布（基于最近事件窗口的 category 归类）。
  const eventTypesTotal = telemetry.events.length;
  const categoryCounts = new Map<string, number>();
  for (const event of telemetry.events) {
    categoryCounts.set(event.category, (categoryCounts.get(event.category) ?? 0) + 1);
  }
  const eventTypes: EventTypeSlice[] = telemetryActive
    ? EVENT_TYPE_META.map((meta) => {
        const count = categoryCounts.get(meta.key) ?? 0;
        return {
          key: meta.key,
          label: meta.label,
          count,
          ratio: eventTypesTotal > 0 ? count / eventTypesTotal : 0,
          colorClass: meta.colorClass,
        };
      }).filter((slice) => slice.count > 0)
    : [];

  const speakerTokens = (speakers: string[]): number => {
    const lowered = speakers.map((s) => s.toLowerCase());
    return dialogueEvents.filter((event) => lowered.includes(String(event.speaker || '').toLowerCase())).length;
  };

  const roleState = (roleKey: string): PipelineState => {
    if (blockedRoles.has(roleKey)) return 'blocked';
    if (roleKey === 'pm' && pmRunning) return 'active';
    if (roleKey === 'director' && directorRunning) return 'active';
    // 真实遥测里该角色产生过事件 → 活动（如一次角色对话）。
    if (telemetryActive && telemetryRoleEvents(telemetry, roleKey) > 0) return 'active';
    return 'idle';
  };

  const roleTokens = (roleKey: string): number => {
    // 角色卡只展示真实 per-role usage。usageStats/by_mode 是全局用量统计维度，
    // 不能冒充某个角色的当前窗口占用，否则会让多个角色显示同一个估算 token。
    if (telemetryActive) {
      return telemetryRoleTokens(telemetry, roleKey);
    }
    return 0;
  };

  /** 每个角色内部 ContextOS 面板展示的最大事件数。 */
  const MAX_ROLE_EVENTS = 8;

  function buildRoleInternalContext(
    role: { id: string; key: string; courtTitle: string; title: string },
  ): RoleInternalContext {
    const roleEvents = telemetryActive ? filterEventsForRole(telemetry.events, role.key) : [];
    // 与 RoleCard.state 保持一致：运行中 / 有事件 → active；blocked 优先；否则 idle。
    const state: PipelineState = roleState(role.key);

    let projectionCount = 0;
    let receiptCount = 0;
    let totalTokens = 0;
    let promptTokens = 0;
    let completionTokens = 0;
    let errorCount = 0;
    let latencySum = 0;
    let latencyCount = 0;

    for (const event of roleEvents) {
      if (event.isProjection) projectionCount += 1;
      if (event.hasReceipt) receiptCount += 1;
      if (event.category === 'error') errorCount += 1;
      totalTokens += event.totalTokens;
      promptTokens += event.promptTokens;
      completionTokens += event.completionTokens;
      if (event.durationMs !== null) {
        latencySum += event.durationMs;
        latencyCount += 1;
      }
    }

    const calls = roleEvents.filter((event) => event.isCall || event.hasUsage).length;

    // 最近一次 context.build 的 items_count / total_tokens 来自该角色自身的事件子集。
    const lastContextBuild = roleEvents.find((event) => event.contextItems !== null);
    const lastContextSize = roleEvents.find((event) => event.contextTokens !== null);
    const contextItemsCount = lastContextBuild ? lastContextBuild.contextItems : null;
    const contextTokensLatest = lastContextSize ? lastContextSize.contextTokens : null;
    const workingMemoryItems = contextItemsCount ?? (roleEvents.length > 0 ? roleEvents.length : null);
    const roleWindow = roleWindowByKey[role.key] ?? { tokens: null, label: '窗口未知', detail: 'LLM status 未提供角色绑定窗口' };
    const usageCallCount = roleEvents.filter((event) => event.hasUsage).length;
    const promptAverage = usageCallCount > 0 && promptTokens > 0 ? Math.round(promptTokens / usageCallCount) : null;
    const windowOccupancyTokens = contextTokensLatest !== null && contextTokensLatest > 0
      ? contextTokensLatest
      : promptAverage;
    const windowOccupancyLabel = contextTokensLatest !== null && contextTokensLatest > 0
      ? '最新上下文'
      : promptAverage !== null
        ? '平均提示'
        : '无 usage';
    const windowOccupancyDetail = contextTokensLatest !== null && contextTokensLatest > 0
      ? '来自该角色最近一次 context.build/context_tokens_after'
      : promptAverage !== null
        ? '来自该角色 usage 事件的 prompt_tokens 平均值'
        : '该角色尚无带 usage 的实时观测事件';

    // 当前任务：ContextOSEvent 目前未携带 refs，先诚实留空；后续可在 logEntryToEvent 中扩展
    // refs/task_id 字段后再精确填充。
    const currentTaskId: string | null = null;
    const currentTaskTitle: string | null = null;

    // epoch <= 0 表示不可解析时间戳，按「无有效时间」处理；否则保留真实 epoch。
    const lastEventAt = roleEvents.length > 0 ? (roleEvents[0].epoch > 0 ? roleEvents[0].epoch : null) : null;

    const detail = telemetryActive && telemetryRoleHasUsageChannel(telemetry, role.key) && totalTokens > 0
      ? `${formatTokens(totalTokens)} tok`
      : roleEvents.length > 0
        ? `${roleEvents.length} 事件`
        : '待命';

    // Find the most recent call with a context snapshot ref
    const lastCallWithSnapshot = roleEvents.find((event) => event.contextSnapshotRef);
    const latestContextSnapshotRef = lastCallWithSnapshot ? lastCallWithSnapshot.contextSnapshotRef : null;
    const latestCallId = lastCallWithSnapshot ? (lastCallWithSnapshot as any).callId || null : null;
    const latestTurnId = lastCallWithSnapshot ? lastCallWithSnapshot.turnId : null;

    return {
      roleId: role.id,
      title: role.title,
      courtTitle: role.courtTitle,
      state,
      events: roleEvents.slice(0, MAX_ROLE_EVENTS),
      eventCount: roleEvents.length,
      projectionCount,
      receiptCount,
      contextItemsCount,
      workingMemoryItems,
      workingMemoryEstimated: contextItemsCount === null && workingMemoryItems !== null,
      contextTokensLatest,
      contextWindowTokens: roleWindow.tokens,
      contextWindowLabel: roleWindow.label,
      contextWindowDetail: roleWindow.detail,
      windowOccupancyTokens,
      windowOccupancyLabel,
      windowOccupancyDetail,
      totalTokens,
      promptTokens,
      completionTokens,
      calls,
      lastEventAt,
      currentTaskId,
      currentTaskTitle,
      latestContextSnapshotRef,
      latestCallId,
      latestTurnId,
      detail,
    };
  }

  const roles: RoleCard[] = ROLE_DEFINITIONS.map((role) => {
    const tokens = roleTokens(role.key);
    const telemetryEvents = telemetryActive ? telemetryRoleEvents(telemetry, role.key) : 0;
    const speeches = speakerTokens(role.key === 'qa' ? ['QA', 'Reviewer'] : [role.title]);
    // 只有拥有真实 usage 观测通道的角色才以 token 归因；其余据实以事件数呈现，
    // 不把「无 usage 通道」误读为真实零用量。
    const tokensReal = telemetryActive && telemetryRoleHasUsageChannel(telemetry, role.key) && tokens > 0;
    const detail = tokensReal
      ? `${formatTokens(tokens)} tok`
      : telemetryEvents > 0
        ? `${telemetryEvents} 事件`
        : speeches > 0
          ? `${speeches} 次发言`
          : '待命';
    const internalContext = buildRoleInternalContext(role);
    return {
      id: role.id,
      title: role.title,
      courtTitle: role.courtTitle,
      tokens,
      state: roleState(role.key),
      detail,
      tokensReal,
      lastEventAt: internalContext.lastEventAt,
      projectionCount: internalContext.projectionCount,
      contextItemsCount: internalContext.contextItemsCount,
      contextWindowTokens: internalContext.contextWindowTokens,
      contextWindowLabel: internalContext.contextWindowLabel,
      contextWindowDetail: internalContext.contextWindowDetail,
      receiptCount: internalContext.receiptCount,
      internalContext,
    };
  });

  const policies: string[] = [
    '自适应排序',
    '回执卸载',
    '相位感知预算',
    running ? '运行中' : telemetryActive ? '实时观测' : '空闲',
    llmRuntimeState.state === 'READY' ? 'LLM 就绪' : llmBlocked ? 'LLM 受阻' : 'LLM 未知',
  ];

  // 决策流：有真实观测遥测时直接展示真实事件流（最准最实时），
  // 否则退回 对话 + 执行日志 的合并派生。
  const decisions = telemetryActive
    ? deriveTelemetryDecisions(telemetry)
    : deriveDecisions(dialogueEvents, executionLogs);

  // Phase 3+：多 worker LLM 追踪卡（按 worker_id 聚合）。无 worker 归属时为空数组。
  // 字段完全派生自真实遥测（meta.worker_id / meta.workerId），后端未发时据实为空。
  // 按最近活动 epoch 倒序，确保活跃 worker 排在前面。
  const workers: WorkerCard[] = telemetryActive
    ? Object.values(telemetry.byWorker)
        .map((agg: WorkerAggregate): WorkerCard => {
          // 该 worker 命中的最近一次 context snapshot ref（按 epoch 倒序扫）。
          const lastSnapshot = telemetry.events.find(
            (event) => event.workerId === agg.workerId && event.contextSnapshotRef,
          );
          return {
            workerId: agg.workerId,
            role: agg.role || 'Worker',
            tokens: agg.totalTokens,
            calls: agg.calls,
            events: agg.events,
            latencyMs: agg.lastLatencyMs,
            lastEpoch: agg.lastEpoch,
            state: agg.events > 0 ? 'active' : 'idle',
            latestContextSnapshotRef: lastSnapshot ? lastSnapshot.contextSnapshotRef : null,
          };
        })
        .sort((a, b) => (b.lastEpoch ?? 0) - (a.lastEpoch ?? 0))
    : [];
  const hasWorkers = telemetryActive ? telemetry.hasWorkers : false;

  return {
    running,
    dataIdle,
    errorCount,
    lastLatencyMs,
    iteration,
    taskCount,
    totalTokens,
    promptTokens,
    completionTokens,
    tokensRealtime,
    calls,
    avgPerCall,
    windowOccupancy,
    windowOccupancyTokens,
    contextWindowTokens,
    contextWindowLabel,
    contextWindowDetail,
    contextWindowSource,
    telemetryActive,
    projectionCount,
    receiptCount,
    realLatencyMs,
    lastTelemetryEpoch,
    estimatedCalls,
    contextItemsCount,
    telemetryWindowed,
    pipeline,
    components,
    budget,
    byModeSlices,
    eventTypes,
    eventTypesTotal,
    roles,
    decisions,
    policies,
    workers,
    hasWorkers,
  };
}

export const contextOSFormat = {
  tokens: formatTokens,
  windowTokens: formatWindowTokens,
  clock: formatClock,
};
