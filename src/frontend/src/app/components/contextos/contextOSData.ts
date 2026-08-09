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
  contextOSObservedTokens,
  filterEventsForRole,
  telemetryRoleEvents,
  telemetryRoleHasUsageChannel,
  telemetryRoleTokens,
  type ContextOSEvent,
  type ContextOSTelemetry,
  type WorkerAggregate,
} from './contextOSTelemetry';

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

export interface RoleBindingBudget {
  id: string;
  roleId: string;
  label: string;
  providerId: string | null;
  providerName: string | null;
  providerType: string | null;
  model: string | null;
  profile: string | null;
  contextWindowTokens: number | null;
  maxOutputTokens: number | null;
  contextWindowLabel: string;
  contextWindowDetail: string;
  contextWindowSource: 'binding' | 'unknown';
  calls: number;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
  toolTokens: number;
  reasoningTokens: number;
  audioTokens: number;
  serverToolUseCount: number;
  realProviderTokens: number;
  estimatedTokens: number;
  contextTokensLatest: number | null;
  windowOccupancyTokens: number | null;
  windowOccupancyLabel: string;
  windowOccupancyDetail: string;
  latencyMs: number | null;
  lastEventAt: number | null;
  matchedEvents: number;
  usageSource: 'matched' | 'role_aggregate' | 'none';
  usageKind: 'provider' | 'stream_final' | 'request_estimate' | 'char_estimate' | 'mixed' | 'none';
  usageProvenance: 'provider' | 'estimated' | 'mixed' | 'none';
  bindingId: string | null;
  taskId: string | null;
  pmTaskId: string | null;
  chiefBlueprintId: string | null;
  skipped: boolean;
  skipReason: string | null;
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
  /** 上下文窗口数据来源：binding=LLM角色绑定，unknown=无绑定。 */
  contextWindowSource: 'binding' | 'unknown';
  /** 绑定的 provider 名称（如 "Kimi Coding"），无绑定时 null。 */
  contextWindowProvider: string | null;
  /** 绑定的 model 名称（如 "kimi-for-coding"），无绑定时 null。 */
  contextWindowModel: string | null;
  /** 当前窗口占用的角色级分子。优先最终 provider request token，其次 context token，再退回平均 prompt。 */
  windowOccupancyTokens: number | null;
  windowOccupancyLabel: string;
  windowOccupancyDetail: string;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
  toolTokens: number;
  reasoningTokens: number;
  audioTokens: number;
  serverToolUseCount: number;
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
  /** Per provider/model context budget rows for this role. */
  bindingBudgets: RoleBindingBudget[];
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
  /** 上下文窗口数据来源：binding=LLM角色绑定，unknown=无绑定。 */
  contextWindowSource: 'binding' | 'unknown';
  /** 绑定的 provider 名称，无绑定时 null。 */
  contextWindowProvider: string | null;
  /** 绑定的 model 名称，无绑定时 null。 */
  contextWindowModel: string | null;
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
  cachedTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
  toolTokens: number;
  reasoningTokens: number;
  audioTokens: number;
  serverToolUseCount: number;
  providerUsageTokens: number;
  usageSourceLabel: string;
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
  /** Per-role provider/model context budget rows. */
  bindingBudgets: RoleBindingBudget[];
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
  { id: 'pm', key: 'pm', courtTitle: '尚书令', title: 'Project Manager' },
  { id: 'architect', key: 'architect', courtTitle: '中书令', title: 'Architect' },
  { id: 'chief_engineer', key: 'chief_engineer', courtTitle: '工部尚书', title: 'Chief Engineer' },
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function redactedDisplayText(value: Record<string, unknown>): string | null {
  if (value['redacted'] !== true) return null;
  const type = typeof value['type'] === 'string' && value['type'].trim() ? value['type'].trim() : null;
  const chars = typeof value['chars'] === 'number' && Number.isFinite(value['chars'])
    ? Math.max(0, Math.round(value['chars']))
    : null;
  const parts = ['历史事件仅有摘要'];
  if (type) parts.push(type);
  if (chars !== null) parts.push(`${chars} chars`);
  return parts.join(' · ');
}

function redactedJsonDisplayText(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed.startsWith('{') || !trimmed.includes('"redacted"')) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return isRecord(parsed) ? redactedDisplayText(parsed) : null;
  } catch {
    return null;
  }
}

/**
 * Safely convert any value to a displayable string.
 * Prevents [object Object] from leaking into the UI.
 * Legacy summary payloads are humanized; primitives are coerced; null/undefined become fallback.
 */
export function safeText(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'string') return redactedJsonDisplayText(value) ?? value;
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') return String(value);
  if (isRecord(value)) {
    const redacted = redactedDisplayText(value);
    if (redacted) return redacted;
    try {
      const json = JSON.stringify(value);
      if (json === '{}') return fallback;
      if (json.length > 200) return json.slice(0, 197) + '...';
      return json;
    } catch {
      return fallback;
    }
  }
  return fallback;
}

function formatTokens(value: number): string {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  }
  return String(Math.round(value));
}

function isProviderRequestSnapshotCandidate(event: ContextOSEvent): boolean {
  return Boolean(
    event.contextSnapshotRef
    && (
      event.isCall
      || event.callId
      || event.hasUsage
      || event.finalRequestTokenEstimate !== null
      || event.finalRequestContextAudit !== null
    ),
  );
}

function formatWindowTokens(value: number): string {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(value % 1_000_000 === 0 ? 0 : 1)}M`;
  }
  if (value >= 100_000) {
    return `${(value / 1000).toFixed(0)}k`;
  }
  if (value >= 10_000) {
    return `${(value / 1000).toFixed(1)}k`;
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)}k`;
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

function keyToken(value: string | null | undefined): string {
  return String(value || '').trim().toLowerCase();
}

function idToken(value: string | null | undefined): string {
  const normalized = keyToken(value).replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
  return normalized || 'unknown';
}

function bindingDisplayName(binding: Pick<RoleBindingBudget, 'providerName' | 'providerId' | 'model'>): string {
  const provider = binding.providerName || binding.providerId || '';
  const model = binding.model || '未知模型';
  return provider ? `${provider} / ${model}` : model;
}

function roleDetailAsBinding(detail: LlmRuntimeRoleDetail): LlmRuntimeRoleBinding {
  return {
    bindingId: null,
    providerId: detail.providerId,
    providerName: detail.providerName,
    providerType: detail.providerType,
    model: detail.model,
    profile: '',
    maxContextTokens: detail.maxContextTokens,
    maxOutputTokens: detail.maxOutputTokens,
  };
}

function emptyBindingBudget(
  roleKey: string,
  binding: LlmRuntimeRoleBinding,
  index: number,
  source: 'binding' | 'unknown',
): RoleBindingBudget {
  const tokens = bindingWindow(binding);
  const providerName = binding.providerName || binding.providerId || null;
  const model = binding.model || null;
  const label = bindingDisplayName({ providerName, providerId: binding.providerId || null, model });
  return {
    id: `${idToken(roleKey)}-${idToken(binding.providerId || providerName)}-${idToken(model)}-${index}`,
    roleId: roleKey,
    label,
    providerId: binding.providerId || null,
    providerName,
    providerType: binding.providerType || null,
    model,
    profile: binding.profile || null,
    contextWindowTokens: tokens,
    maxOutputTokens: typeof binding.maxOutputTokens === 'number' && binding.maxOutputTokens > 0
      ? binding.maxOutputTokens
      : null,
    contextWindowLabel: tokens !== null ? `${contextOSFormat.windowTokens(tokens)} 窗口` : '窗口未知',
    contextWindowDetail: tokens !== null ? `${label} · maxContextTokens` : `${label} · 无 maxContextTokens`,
    contextWindowSource: tokens !== null ? source : 'unknown',
    calls: 0,
    totalTokens: 0,
    promptTokens: 0,
    completionTokens: 0,
    cachedTokens: 0,
    cacheCreationTokens: 0,
    cacheReadTokens: 0,
    toolTokens: 0,
    reasoningTokens: 0,
    audioTokens: 0,
    serverToolUseCount: 0,
    realProviderTokens: 0,
    estimatedTokens: 0,
    contextTokensLatest: null,
    windowOccupancyTokens: null,
    windowOccupancyLabel: '无 usage',
    windowOccupancyDetail: '该绑定尚无可归属的实时 usage 事件',
    latencyMs: null,
    lastEventAt: null,
    matchedEvents: 0,
    usageSource: 'none',
    usageKind: 'none',
    usageProvenance: 'none',
    bindingId: binding.bindingId || null,
    taskId: null,
    pmTaskId: null,
    chiefBlueprintId: null,
    skipped: binding.skipped === true,
    skipReason: binding.skipReason || null,
  };
}

function deriveRoleBindingBudgetTemplates(state: LlmRuntimeGateState, roleKey: string): RoleBindingBudget[] {
  const detail = roleDetailForWindow(state, roleKey);
  if (!detail) return [];

  const bindings = detail.bindings.length > 0
    ? detail.bindings
    : (detail.providerId || detail.providerName || detail.model || detail.maxContextTokens)
      ? [roleDetailAsBinding(detail)]
      : [];

  return bindings.map((binding, index) => {
    const row = emptyBindingBudget(roleKey, binding, index, 'binding');
    const skipped = (state.skippedBindings || []).find((item) => {
      const roleMatches = !item.roleId || keyToken(item.roleId) === keyToken(roleKey);
      if (!roleMatches) return false;
      if (item.bindingId && row.bindingId) return keyToken(item.bindingId) === keyToken(row.bindingId);
      if (item.providerId && row.providerId) return keyToken(item.providerId) === keyToken(row.providerId) && (!item.model || !row.model || keyToken(item.model) === keyToken(row.model));
      return Boolean(item.model && row.model && keyToken(item.model) === keyToken(row.model));
    });
    return skipped
      ? { ...row, skipped: true, skipReason: skipped.skipReason || row.skipReason || 'binding_skipped' }
      : row;
  });
}

function eventMatchesBinding(event: ContextOSEvent, binding: RoleBindingBudget): boolean {
  const eventBindingId = keyToken(event.bindingId);
  const bindingId = keyToken(binding.bindingId);
  const eventProviderId = keyToken(event.providerId);
  const eventProviderName = keyToken(event.providerName);
  const eventModel = keyToken(event.model);
  const bindingProviderId = keyToken(binding.providerId);
  const bindingProviderName = keyToken(binding.providerName);
  const bindingModel = keyToken(binding.model);

  if (eventBindingId && bindingId) return eventBindingId === bindingId;
  if (eventProviderId && bindingProviderId) {
    return eventProviderId === bindingProviderId && (!eventModel || !bindingModel || eventModel === bindingModel);
  }
  if (eventProviderName && bindingProviderName) {
    return eventProviderName === bindingProviderName && (!eventModel || !bindingModel || eventModel === bindingModel);
  }
  if (eventModel && bindingModel && eventModel === bindingModel) {
    return !bindingProviderId && !bindingProviderName;
  }
  return false;
}

function summarizeUsageKind(events: ContextOSEvent[]): RoleBindingBudget['usageKind'] {
  const kinds = new Set(events.map((event) => event.usageSource).filter((source) => source !== 'none'));
  if (kinds.size === 0) return 'none';
  if (kinds.size > 1) return 'mixed';
  const [kind] = Array.from(kinds);
  return kind;
}

function summarizeUsageProvenance(events: ContextOSEvent[]): RoleBindingBudget['usageProvenance'] {
  const hasProvider = events.some((event) => event.hasUsage && !event.estimatedTokens);
  const hasEstimated = events.some((event) => event.estimatedTokens || event.usageSource === 'request_estimate');
  if (hasProvider && hasEstimated) return 'mixed';
  if (hasProvider) return 'provider';
  if (hasEstimated) return 'estimated';
  return 'none';
}

function summarizeBindingBudget(
  template: RoleBindingBudget,
  events: ContextOSEvent[],
  usageSource: RoleBindingBudget['usageSource'],
): RoleBindingBudget {
  if (events.length === 0) return template;
  const orderedEvents = [...events].sort((a, b) => (b.epoch - a.epoch) || (b.seq - a.seq));

  let totalTokens = 0;
  let promptTokens = 0;
  let completionTokens = 0;
  let cachedTokens = 0;
  let cacheCreationTokens = 0;
  let cacheReadTokens = 0;
  let toolTokens = 0;
  let reasoningTokens = 0;
  let audioTokens = 0;
  let serverToolUseCount = 0;
  let realProviderTokens = 0;
  let estimatedTokens = 0;
  let calls = 0;

  for (const event of orderedEvents) {
    const observedTokens = contextOSObservedTokens(event);
    totalTokens += observedTokens;
    promptTokens += event.promptTokens;
    completionTokens += event.completionTokens;
    cachedTokens += event.cachedTokens;
    cacheCreationTokens += event.cacheCreationTokens;
    cacheReadTokens += event.cacheReadTokens;
    toolTokens += event.toolTokens;
    reasoningTokens += event.reasoningTokens;
    audioTokens += event.audioTokens;
    serverToolUseCount += event.serverToolUseCount;
    if (event.hasUsage && !event.estimatedTokens) realProviderTokens += event.totalTokens;
    if (event.estimatedTokens || event.usageSource === 'request_estimate') estimatedTokens += observedTokens;
    if (event.isCall || event.hasUsage) calls += 1;
  }

  const latestContextSize = orderedEvents.find(
    (event) => event.finalRequestTokenEstimate !== null || event.contextTokens !== null,
  );
  const contextTokensLatest = latestContextSize
    ? (latestContextSize.finalRequestTokenEstimate ?? latestContextSize.contextTokens)
    : null;
  const usageCalls = orderedEvents.filter((event) => event.hasUsage).length;
  const promptAverage = usageCalls > 0 && promptTokens > 0 ? Math.round(promptTokens / usageCalls) : null;
  const windowOccupancyTokens = contextTokensLatest !== null && contextTokensLatest > 0
    ? contextTokensLatest
    : promptAverage;
  const windowOccupancyLabel = contextTokensLatest !== null && contextTokensLatest > 0
    ? '最新最终请求 (实测)'
    : promptAverage !== null
      ? usageSource === 'matched' ? '匹配事件 prompt 均值' : '角色聚合 prompt 均值'
      : '无 usage';
  const windowOccupancyDetail = contextTokensLatest !== null && contextTokensLatest > 0
    ? '来自该绑定最近一次 final_request_context_audit/context_tokens_after'
    : promptAverage !== null
      ? usageSource === 'matched'
        ? '按 provider/model 匹配到的实时 usage 事件计算'
        : '事件未携带 provider/model，只能显示为角色聚合'
      : '该绑定尚无可归属的实时 usage 事件';
  const latestLatency = orderedEvents.find((event) => event.durationMs !== null);
  const latestEvent = orderedEvents.find((event) => event.epoch > 0);
  const latestTaskEvent = orderedEvents.find((event) => event.taskId || event.pmTaskId || event.chiefBlueprintId);

  return {
    ...template,
    calls,
    totalTokens,
    promptTokens,
    completionTokens,
    cachedTokens,
    cacheCreationTokens,
    cacheReadTokens,
    toolTokens,
    reasoningTokens,
    audioTokens,
    serverToolUseCount,
    realProviderTokens,
    estimatedTokens,
    contextTokensLatest,
    windowOccupancyTokens,
    windowOccupancyLabel,
    windowOccupancyDetail,
    latencyMs: latestLatency ? latestLatency.durationMs : null,
    lastEventAt: latestEvent ? latestEvent.epoch : null,
    matchedEvents: orderedEvents.length,
    usageSource,
    usageKind: summarizeUsageKind(orderedEvents),
    usageProvenance: summarizeUsageProvenance(orderedEvents),
    taskId: latestTaskEvent?.taskId ?? null,
    pmTaskId: latestTaskEvent?.pmTaskId ?? null,
    chiefBlueprintId: latestTaskEvent?.chiefBlueprintId ?? null,
  };
}

function deriveRoleContextWindow(
  state: LlmRuntimeGateState,
  roleKey: string,
): { tokens: number | null; label: string; detail: string; source: 'binding' | 'unknown'; provider: string | null; model: string | null } {
  const detail = roleDetailForWindow(state, roleKey);
  if (!detail) {
    return { tokens: null, label: '窗口未知', detail: 'LLM status 未提供角色绑定窗口', source: 'unknown', provider: null, model: null };
  }

  const bindingWindows = detail.bindings
    .map((binding) => bindingWindow(binding))
    .filter((value): value is number => typeof value === 'number');
  const directWindow = typeof detail.maxContextTokens === 'number' && detail.maxContextTokens > 0
    ? detail.maxContextTokens
    : null;
  const tokens = bindingWindows.length > 0 ? Math.min(...bindingWindows) : directWindow;
  const provider = detail.providerName || detail.providerId || null;
  const model = detail.model || detail.bindings[0]?.model || null;
  const bindingCount = detail.bindings.length;

  if (!tokens) {
    return {
      tokens: null,
      label: model ? `${modelLabel(model)} 窗口未知` : '窗口未知',
      detail: provider ? `${provider}${model ? ` / ${model}` : ''} · 无 maxContextTokens 绑定` : 'LLM status 未提供窗口字段',
      source: 'unknown',
      provider,
      model,
    };
  }

  const windowLabel = bindingCount > 1
    ? `${bindingCount} 路最小窗口`
    : model
      ? `${modelLabel(model)} 绑定`
      : '绑定窗口';

  return {
    tokens,
    label: windowLabel,
    detail: provider ? `${provider}${model ? ` / ${model}` : ''} · maxContextTokens` : `绑定窗口 ${contextOSFormat.windowTokens(tokens)}`,
    source: 'binding',
    provider,
    model,
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
  if (token.includes('implement') || token.includes('exec') || token.includes('code') || token.includes('cod')) return 'llm';
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
    const speaker = safeText(event.speaker, 'System');
    const kind = safeText(event.type || event.refs?.phase, 'message');
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
        summary: safeText(event.content).replace(/\s+/g, ' ').trim().slice(0, 120),
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
        actor: safeText(log.source, 'runtime'),
        kind: safeText(log.level, 'info'),
        summary: safeText(log.message).replace(/\s+/g, ' ').trim().slice(0, 120),
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
  if (event.name) return safeText(event.name);
  if (event.isProjection) return 'projection';
  return safeText(event.kind, 'observation');
}

function deriveTelemetryDecisions(telemetry: ContextOSTelemetry, limit = 12): DecisionRow[] {
  return telemetry.events.slice(0, limit).map((event) => ({
    id: event.id,
    time: formatClock(event.ts) || '--:--:--',
    actor: safeText(event.actor),
    kind: telemetryDecisionKind(event),
    summary: safeText(event.summary) || telemetryDecisionKind(event),
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
  const cachedTokens = telemetryActive ? telemetry.cachedTokens : 0;
  const cacheCreationTokens = telemetryActive ? telemetry.cacheCreationTokens : 0;
  const cacheReadTokens = telemetryActive ? telemetry.cacheReadTokens : 0;
  const toolTokens = telemetryActive ? telemetry.toolTokens : 0;
  const reasoningTokens = telemetryActive ? telemetry.reasoningTokens : 0;
  const audioTokens = telemetryActive ? telemetry.audioTokens : 0;
  const serverToolUseCount = telemetryActive ? telemetry.serverToolUseCount : 0;
  const providerUsageTokens = promptTokens + completionTokens;
  // token 是否实时（来自 journal `llm` 通道的真实 usage）；否则退回用量统计通道（非实时）。
  const tokensRealtime = telemetryActive && telemetry.totalTokens > 0;
  const usageSourceLabel = tokensRealtime
    ? 'provider usage · 实时'
    : totalTokens > 0
      ? '用量统计 · 非实时'
      : '无 usage';
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
  // receiptCount / contextItemsCount：来自 system 的 context.snapshot/context.build 结构化签名；
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

  const roleWindowByKey: Record<string, { tokens: number | null; label: string; detail: string; source: 'binding' | 'unknown'; provider: string | null; model: string | null }> = Object.fromEntries(
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

  const contextTokensLatest = telemetryActive ? telemetry.contextTokensLatest : null;
  // 窗口压力分子：优先最终 provider request token；缺失时才退回平均 prompt 估算。
  const avgPromptPerCall = calls > 0 ? promptTokens / calls : 0;
  const totalContextTokens = contextTokensLatest ?? promptTokens;  // 当前可观测上下文 token
  const windowOccupancyTokens = contextTokensLatest !== null && contextTokensLatest > 0
    ? contextTokensLatest
    : Math.round(avgPromptPerCall);
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
    { id: 'budget', label: 'CompressionEngine', component: 'CompressionEngine', hint: '装配后预算压缩兜底', state: stateFor('budget'), metric: contextTokensLatest !== null ? `${formatTokens(contextTokensLatest)} 最终请求` : `${formatTokens(avgPerCall)} tok/次` },
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

  // 预算构成：provider 真实 usage 细分；cache/tool 缺失时不臆造。
  const directPromptTokens = Math.max(0, promptTokens - cacheCreationTokens - cacheReadTokens);
  const budgetDenominator = Math.max(
    totalTokens,
    directPromptTokens + cacheCreationTokens + cacheReadTokens + completionTokens + toolTokens + reasoningTokens + audioTokens,
    0,
  );
  const rawBudget: Array<Omit<BudgetSlice, 'ratio'>> = [
    { key: 'prompt', label: cacheCreationTokens > 0 || cacheReadTokens > 0 ? '输入 (未缓存)' : '输入 / Prompt', tokens: directPromptTokens, colorClass: 'bg-accent-secondary' },
    { key: 'cache_creation', label: '缓存写入', tokens: cacheCreationTokens, colorClass: 'bg-status-info' },
    { key: 'cache_read', label: '缓存读取', tokens: cacheReadTokens, colorClass: 'bg-status-success' },
    { key: 'tools', label: '工具/格式开销', tokens: toolTokens, colorClass: 'bg-accent' },
    { key: 'reasoning', label: '推理开销', tokens: reasoningTokens, colorClass: 'bg-status-warning' },
    { key: 'audio', label: '音频 token', tokens: audioTokens, colorClass: 'bg-status-info' },
    { key: 'completion', label: '输出 / Completion', tokens: completionTokens, colorClass: 'bg-gold' },
  ];
  const budget: BudgetSlice[] = rawBudget
    .filter((slice) => slice.tokens > 0 || slice.key === 'prompt' || slice.key === 'completion')
    .map((slice) => ({
      ...slice,
      ratio: budgetDenominator > 0 ? slice.tokens / budgetDenominator : 0,
    }));

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
    let cachedTokens = 0;
    let cacheCreationTokens = 0;
    let cacheReadTokens = 0;
    let toolTokens = 0;
    let reasoningTokens = 0;
    let audioTokens = 0;
    let serverToolUseCount = 0;
    let errorCount = 0;
    let latencySum = 0;
    let latencyCount = 0;

    for (const event of roleEvents) {
      if (event.isProjection) projectionCount += 1;
      if (event.hasReceipt) receiptCount += 1;
      if (event.category === 'error') errorCount += 1;
      totalTokens += contextOSObservedTokens(event);
      promptTokens += event.promptTokens;
      completionTokens += event.completionTokens;
      cachedTokens += event.cachedTokens;
      cacheCreationTokens += event.cacheCreationTokens;
      cacheReadTokens += event.cacheReadTokens;
      toolTokens += event.toolTokens;
      reasoningTokens += event.reasoningTokens;
      audioTokens += event.audioTokens;
      serverToolUseCount += event.serverToolUseCount;
      if (event.durationMs !== null) {
        latencySum += event.durationMs;
        latencyCount += 1;
      }
    }

    const roleAggregate = telemetryActive ? telemetry.byRole[role.key] : undefined;
    const calls = roleAggregate?.calls ?? roleEvents.filter((event) => event.isCall || event.hasUsage).length;
    totalTokens = roleAggregate?.totalTokens ?? totalTokens;
    promptTokens = roleAggregate?.promptTokens ?? promptTokens;
    completionTokens = roleAggregate?.completionTokens ?? completionTokens;
    cachedTokens = roleAggregate?.cachedTokens ?? cachedTokens;
    cacheCreationTokens = roleAggregate?.cacheCreationTokens ?? cacheCreationTokens;
    cacheReadTokens = roleAggregate?.cacheReadTokens ?? cacheReadTokens;
    toolTokens = roleAggregate?.toolTokens ?? toolTokens;
    reasoningTokens = roleAggregate?.reasoningTokens ?? reasoningTokens;
    audioTokens = roleAggregate?.audioTokens ?? audioTokens;
    serverToolUseCount = roleAggregate?.serverToolUseCount ?? serverToolUseCount;

    // 最近一次 context.build 的 items_count 和最终请求 token 来自该角色自身的事件子集。
    const lastContextBuild = roleEvents.find((event) => event.contextItems !== null);
    const lastContextSize = roleEvents.find(
      (event) => event.finalRequestTokenEstimate !== null || event.contextTokens !== null,
    );
    const contextItemsCount = lastContextBuild ? lastContextBuild.contextItems : null;
    const contextTokensLatest = lastContextSize
      ? (lastContextSize.finalRequestTokenEstimate ?? lastContextSize.contextTokens)
      : null;
    const workingMemoryItems = contextItemsCount ?? (roleEvents.length > 0 ? roleEvents.length : null);
    const roleWindow = roleWindowByKey[role.key] ?? { tokens: null, label: '窗口未知', detail: 'LLM status 未提供角色绑定窗口', source: 'unknown' as const, provider: null, model: null };
    const usageCallCount = roleAggregate?.usageCalls ?? roleEvents.filter((event) => event.hasUsage).length;
    const promptAverage = usageCallCount > 0 && promptTokens > 0 ? Math.round(promptTokens / usageCallCount) : null;
    const windowOccupancyTokens = contextTokensLatest !== null && contextTokensLatest > 0
      ? contextTokensLatest
      : promptAverage;
    const windowOccupancyLabel = contextTokensLatest !== null && contextTokensLatest > 0
      ? '最新最终请求 (实测)'
      : promptAverage !== null
        ? '平均提示 (估算)'
        : '无 usage';
    const windowOccupancyDetail = contextTokensLatest !== null && contextTokensLatest > 0
      ? '来自该角色最近一次 final_request_context_audit/context_tokens_after (含 tools/response_format)'
      : promptAverage !== null
        ? '来自该角色 usage 事件的 prompt_tokens 平均值 (非窗口实测)'
        : '该角色尚无带 usage 的实时观测事件';
    const bindingTemplates = deriveRoleBindingBudgetTemplates(llmRuntimeState, role.key);
    const usageEvents = roleEvents.filter((event) =>
      event.isCall || event.hasUsage || event.finalRequestTokenEstimate !== null || event.contextTokens !== null,
    );
    const bindingBudgets = bindingTemplates.map((template) => {
      const matched = usageEvents.filter((event) => eventMatchesBinding(event, template));
      if (matched.length > 0) return summarizeBindingBudget(template, matched, 'matched');
      if (bindingTemplates.length === 1 && usageEvents.length > 0) {
        return summarizeBindingBudget(template, usageEvents, 'role_aggregate');
      }
      return template;
    });
    if (bindingTemplates.length > 1) {
      const unassignedEvents = usageEvents.filter(
        (event) => !bindingTemplates.some((template) => eventMatchesBinding(event, template)),
      );
      if (unassignedEvents.length > 0) {
        const aggregateTemplate: RoleBindingBudget = {
          id: `${idToken(role.key)}-role-aggregate-unassigned`,
          roleId: role.key,
          label: '角色聚合 / 未归属模型',
          providerId: null,
          providerName: '角色聚合',
          providerType: null,
          model: null,
          profile: null,
          contextWindowTokens: roleWindow.tokens,
          maxOutputTokens: null,
          contextWindowLabel: roleWindow.tokens !== null ? `${contextOSFormat.windowTokens(roleWindow.tokens)} 最小窗口` : '窗口未知',
          contextWindowDetail: '多路绑定事件未携带 provider/model，无法精确归属到单个模型',
          contextWindowSource: roleWindow.source,
          calls: 0,
          totalTokens: 0,
          promptTokens: 0,
          completionTokens: 0,
          cachedTokens: 0,
          cacheCreationTokens: 0,
          cacheReadTokens: 0,
          toolTokens: 0,
          reasoningTokens: 0,
          audioTokens: 0,
          serverToolUseCount: 0,
          realProviderTokens: 0,
          estimatedTokens: 0,
          contextTokensLatest: null,
          windowOccupancyTokens: null,
          windowOccupancyLabel: '无 usage',
          windowOccupancyDetail: '多路绑定事件未携带 provider/model，无法精确归属到单个模型',
          latencyMs: null,
          lastEventAt: null,
          matchedEvents: 0,
          usageSource: 'none',
          usageKind: 'none',
          usageProvenance: 'none',
          bindingId: null,
          taskId: null,
          pmTaskId: null,
          chiefBlueprintId: null,
          skipped: false,
          skipReason: null,
        };
        bindingBudgets.push(summarizeBindingBudget(aggregateTemplate, unassignedEvents, 'role_aggregate'));
      }
    }

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

    // Prefer the latest provider-call snapshot. Context projection snapshots can be newer
    // but do not prove final request tools / response_format and must not shadow LLM calls.
    const lastCallWithSnapshot = roleEvents.find(isProviderRequestSnapshotCandidate)
      ?? roleEvents.find((event) => event.contextSnapshotRef);
    const latestContextSnapshotRef = lastCallWithSnapshot ? lastCallWithSnapshot.contextSnapshotRef : null;
    const latestCallId = lastCallWithSnapshot ? lastCallWithSnapshot.callId : null;
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
      contextWindowSource: roleWindow.source,
      contextWindowProvider: roleWindow.provider ?? null,
      contextWindowModel: roleWindow.model ?? null,
      windowOccupancyTokens,
      windowOccupancyLabel,
      windowOccupancyDetail,
      totalTokens,
      promptTokens,
      completionTokens,
      cachedTokens,
      cacheCreationTokens,
      cacheReadTokens,
      toolTokens,
      reasoningTokens,
      audioTokens,
      serverToolUseCount,
      calls,
      lastEventAt,
      currentTaskId,
      currentTaskTitle,
      latestContextSnapshotRef,
      latestCallId,
      latestTurnId,
      bindingBudgets,
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
      contextWindowSource: internalContext.contextWindowSource,
      contextWindowProvider: internalContext.contextWindowProvider,
      contextWindowModel: internalContext.contextWindowModel,
      receiptCount: internalContext.receiptCount,
      internalContext,
    };
  });
  const bindingBudgets = roles.flatMap((role) => role.internalContext.bindingBudgets);

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
    cachedTokens,
    cacheCreationTokens,
    cacheReadTokens,
    toolTokens,
    reasoningTokens,
    audioTokens,
    serverToolUseCount,
    providerUsageTokens,
    usageSourceLabel,
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
    bindingBudgets,
  };
}

export const contextOSFormat = {
  tokens: formatTokens,
  windowTokens: formatWindowTokens,
  clock: formatClock,
};

// ---------------------------------------------------------------------------
// summarizeRoleContextState — 把角色内部 ContextOS 的术语化指标翻译成人话摘要
//
// 背景：RoleInternalPanel 直接展示 TruthLog/WorkingMem/ProjectionEngine/ReceiptStore
// 与 T·W·P·R 代码、「无 usage」等术语，非工程师无法判断当前到底是在执行还是只是
// 事件在空转。本函数从同一份 RoleInternalContext 派生一句诚实的人话结论，供面板
// 顶部作为「明确摘要信息」展示。所有结论只来自真实字段，不臆造、不冒充。
// ---------------------------------------------------------------------------

export interface RoleContextSummary {
  /** 一句话核心结论（人话）。 */
  headline: string;
  /** 补充细节（装配/回执/usage 来源等），可为空字符串。 */
  detail: string;
  /** 语义色调：active=运行中、idle=待机/观测、blocked=受阻。 */
  tone: PipelineState;
}

/**
 * 根据角色内部 ContextOS 状态生成人话摘要。
 *
 * 判定优先级（每条都对应真实字段，诚实可审计）：
 *  1. 受阻（state=blocked 且有错误事件）→ blocked，提示查看事件流；
 *  2. 完全无事件 → 空闲待命；
 *  3. 有事件但 0 调用 0 token → 「事件观测/待机」而非真正执行（最关键的人话区分）；
 *  4. 有调用但无 token → usage 缺失（流式未结束或 provider 未回传）；
 *  5. 有调用且有 token → 运行中，给出真实用量。
 */
export function summarizeRoleContextState(ctx: RoleInternalContext): RoleContextSummary {
  const title = ctx.title || ctx.roleId || '该角色';
  const errorCount = ctx.events.reduce<number>(
    (count, event) => count + (event.category === 'error' ? 1 : 0),
    0,
  );

  // 1. 受阻：优先暴露错误，避免被「有调用」掩盖。
  if (ctx.state === 'blocked' && errorCount > 0) {
    return {
      tone: 'blocked',
      headline: `${title} 观测到 ${errorCount} 条错误事件，已受阻`,
      detail: `请查看下方事件流定位错误${
        ctx.calls > 0 ? `；此前已调用 ${ctx.calls} 次` : '；尚未成功调用模型'
      }`,
    };
  }

  // 2. 完全无事件。
  if (ctx.eventCount === 0) {
    return {
      tone: 'idle',
      headline: `${title} 尚未产生运行事件`,
      detail: '本会话没有观测到该角色的实时活动',
    };
  }

  // 3. 有事件、但既无调用也无 token ——「事件观测 / 待机」而非执行。
  if (ctx.calls === 0 && ctx.totalTokens === 0) {
    const assembly = ctx.projectionCount > 0 ? `，上下文已装配 ${ctx.projectionCount} 次` : '';
    return {
      tone: 'idle',
      headline: `已记录 ${ctx.eventCount} 条事件，但还未真正调用模型`,
      detail: `事件持续到达${assembly}，但没有真实的模型调用或 token 消耗——属于「事件观测 / 待机」状态，不是真正的执行`,
    };
  }

  // 4. 有调用但无 token 用量。
  if (ctx.calls > 0 && ctx.totalTokens === 0) {
    return {
      tone: 'idle',
      headline: `已发起 ${ctx.calls} 次模型调用，但未记录 token`,
      detail: '调用未返回真实 usage（可能流式未结束，或 provider 未回传用量）',
    };
  }

  // 5. 运行中：有调用且有真实 token。
  const assembly = ctx.projectionCount > 0 ? `；装配 ${ctx.projectionCount} 次` : '';
  const receipt = ctx.receiptCount > 0 ? `、回执 ${ctx.receiptCount} 条` : '';
  return {
    tone: 'active',
    headline: `已发起 ${ctx.calls} 次模型调用，消耗约 ${formatTokens(ctx.totalTokens)} token`,
    detail: `提示约 ${formatTokens(ctx.promptTokens)} / 输出约 ${formatTokens(ctx.completionTokens)}${assembly}${receipt}`,
  };
}

// ---------------------------------------------------------------------------
// 事件观测：去噪 + 语义化 + 实体线程
//
// 痛点：RoleInternalEventRow 原样渲染 event.kind（如 event.factory:factory_a1e8…）
// 与 English lifecycle 动词，且 task4/task5 事件交错平铺——非工程师无法判断
// 「每个任务经历了什么、现在是什么态」。下面三个纯函数把同一份 ContextOSEvent[]
// 翻译成中文语义徽章 + 按实体聚合的生命周期线程，供面板分区渲染。
// 全部只读派生，不臆造：原始 channel 保留在 rawChannel，未识别事件回退平铺。
// ---------------------------------------------------------------------------

/** 已知的 factory / 任务生命周期动词（英文 → 中文 + 状态 + 色调）。顺序：长词优先避免子串误匹配。 */
const LIFECYCLE_VERBS: ReadonlyArray<{
  re: RegExp;
  zh: string;
  state: string;
  tone: PipelineState;
}> = [
  { re: /\bdependencies_unblocked\b|\bdeps_unblocked\b/i, zh: '依赖已就绪', state: 'unblocked', tone: 'active' },
  { re: /\bmaterialized\b/i, zh: '已物化', state: 'materialized', tone: 'active' },
  { re: /\bcompleted\b/i, zh: '已完成', state: 'completed', tone: 'active' },
  { re: /\bclaimed\b/i, zh: '已领取', state: 'claimed', tone: 'active' },
  { re: /\bsuspended\b/i, zh: '已挂起', state: 'suspended', tone: 'idle' },
  { re: /\bdispatched\b/i, zh: '已派发', state: 'dispatched', tone: 'active' },
  { re: /\bcreated\b/i, zh: '已创建', state: 'created', tone: 'active' },
  { re: /\bfailed\b/i, zh: '已失败', state: 'failed', tone: 'blocked' },
];

/** 实体引用识别：task N / 任务 N / task #N。 */
const ENTITY_RE = /\btask\s*#?\s*(\d+)|任务\s*#?\s*(\d+)/i;

export interface EventSemantics {
  /** 中文徽章标签（替代原始 event.kind 噪声）。 */
  badge: string;
  /** 语义色调。 */
  tone: PipelineState;
  /** 翻译后的人话摘要（实体 + 动词中文化）。 */
  displaySummary: string;
  /** 原始 channel/类型，保留在 tooltip，不丢信息。 */
  rawChannel: string;
}

/** 把一条事件的人话徽章 / 色调 / 翻译摘要派生出来（L1 去噪 + 语义化）。 */
export function classifyEventSemantics(event: ContextOSEvent): EventSemantics {
  const rawChannel = event.kind || '';
  const summary = event.summary || '';

  // 错误优先（即便含生命周期动词，错误就是错误）。
  if (event.category === 'error') {
    return { badge: '错误', tone: 'blocked', displaySummary: translateEventSummary(summary), rawChannel };
  }
  // LLM 调用 / 投影 / 工具优先于生命周期动词，避免把 "llm response completed" 误判为生命周期。
  if (event.isCall) {
    return { badge: '调用', tone: 'active', displaySummary: translateEventSummary(summary), rawChannel };
  }
  if (event.isProjection) {
    return { badge: '投影', tone: 'active', displaySummary: translateEventSummary(summary), rawChannel };
  }
  if (event.category === 'tool') {
    return { badge: '工具', tone: 'active', displaySummary: translateEventSummary(summary), rawChannel };
  }

  const verb = parseLifecycleVerb(summary);
  if (verb) {
    return { badge: '生命周期', tone: verb.tone, displaySummary: translateEventSummary(summary), rawChannel };
  }

  return { badge: '事件', tone: 'idle', displaySummary: summary, rawChannel };
}

/** 解析事件摘要中的生命周期动词，未命中返回 null。 */
function parseLifecycleVerb(summary: string): { zh: string; state: string; tone: PipelineState } | null {
  for (const verb of LIFECYCLE_VERBS) {
    if (verb.re.test(summary)) {
      return { zh: verb.zh, state: verb.state, tone: verb.tone };
    }
  }
  return null;
}

/** 把事件摘要里的 task N / English 动词翻译成中文（不动其它部分）。 */
function translateEventSummary(summary: string): string {
  if (!summary) return '';
  let s = summary;
  // 实体优先：task 5 / 任务 5 / task #5 → 任务5
  s = s.replace(/\btask\s*#?\s*(\d+)/gi, '任务$1');
  s = s.replace(/任务\s*#?\s*(\d+)/g, '任务$1');
  // 动词：长词优先（dependencies_unblocked 先于其它）。
  s = s.replace(/\bdependencies_unblocked\b|\bdeps_unblocked\b/gi, '依赖已就绪');
  s = s.replace(/\bmaterialized\b/gi, '已物化');
  s = s.replace(/\bcompleted\b/gi, '已完成');
  s = s.replace(/\bclaimed\b/gi, '已领取');
  s = s.replace(/\bsuspended\b/gi, '已挂起');
  s = s.replace(/\bdispatched\b/gi, '已派发');
  s = s.replace(/\bcreated\b/gi, '已创建');
  s = s.replace(/\bfailed\b/gi, '已失败');
  return s;
}

export interface EventEntity {
  kind: string;
  id: string;
  displayId: string;
}

/** 从事件摘要中解析实体引用（task N），未检测到返回 null。 */
function parseEventEntity(event: ContextOSEvent): EventEntity | null {
  const match = ENTITY_RE.exec(event.summary || '');
  if (!match) return null;
  const id = match[1] || match[2];
  if (!id) return null;
  return { kind: 'task', id, displayId: `任务${id}` };
}

export interface EntityLifecycleStep {
  ts: string;
  epoch: number;
  /** 状态机态：created/materialized/claimed/suspended/unblocked/completed/failed/dispatched/event。 */
  verbState: string;
  /** 中文动作标签。 */
  verbZh: string;
  tone: PipelineState;
}

export interface EntityThread {
  id: string;
  entityKind: string;
  entityId: string;
  displayId: string;
  events: ContextOSEvent[];
  /** 按时间正序的生命周期步骤。 */
  steps: EntityLifecycleStep[];
  /** 当前态 = 最后一步。 */
  currentState: string;
  currentStateZh: string;
  currentTone: PipelineState;
  firstEpoch: number;
  lastEpoch: number;
  /** last - first（毫秒）；任一端未知或倒序时为 null。 */
  elapsedMs: number | null;
}

export interface GroupedEvents {
  /** 按 lastEpoch 倒序的实体线程。 */
  threads: EntityThread[];
  /** 未识别到实体的事件，按 epoch 倒序（平铺叙事回退）。 */
  loose: ContextOSEvent[];
}

/** 把事件流按实体聚合成生命周期线程；无实体的事件回退 loose（L2 实体线程）。 */
export function groupEventsByEntity(events: readonly ContextOSEvent[]): GroupedEvents {
  const threadMap = new Map<string, ContextOSEvent[]>();
  const loose: ContextOSEvent[] = [];

  for (const event of events) {
    const entity = parseEventEntity(event);
    if (entity) {
      const key = `${entity.kind}:${entity.id}`;
      const bucket = threadMap.get(key);
      if (bucket) bucket.push(event);
      else threadMap.set(key, [event]);
    } else {
      loose.push(event);
    }
  }

  const threads: EntityThread[] = [];
  for (const [key, bucket] of threadMap) {
    // 按时间正序（epoch，等时回退 seq）排生命周期。
    const ordered = [...bucket].sort((a, b) => a.epoch - b.epoch || a.seq - b.seq);
    const steps: EntityLifecycleStep[] = ordered.map((event) => {
      const verb = parseLifecycleVerb(event.summary || '');
      return {
        ts: event.ts,
        epoch: event.epoch,
        verbState: verb?.state ?? 'event',
        verbZh: verb?.zh ?? (event.summary || '事件'),
        tone: verb?.tone ?? 'idle',
      };
    });
    const first = ordered[0];
    const last = ordered[ordered.length - 1];
    const firstEpoch = first?.epoch ?? 0;
    const lastEpoch = last?.epoch ?? 0;
    const lastStep = steps[steps.length - 1];
    const entityId = key.includes(':') ? key.slice(key.indexOf(':') + 1) : key;
    threads.push({
      id: key,
      entityKind: 'task',
      entityId,
      displayId: `任务${entityId}`,
      events: ordered,
      steps,
      currentState: lastStep?.verbState ?? 'event',
      currentStateZh: lastStep?.verbZh ?? '',
      currentTone: lastStep?.tone ?? 'idle',
      firstEpoch,
      lastEpoch,
      elapsedMs: firstEpoch > 0 && lastEpoch > 0 && lastEpoch >= firstEpoch ? lastEpoch - firstEpoch : null,
    });
  }

  threads.sort((a, b) => b.lastEpoch - a.lastEpoch);
  loose.sort((a, b) => b.epoch - a.epoch);

  return { threads, loose };
}

export interface EntityThreadSummary {
  /** 当前态人话标签（不含实体名，实体名由调用方单独展示）。 */
  stateLabel: string;
  tone: PipelineState;
}

/** 把一条实体线程的当前态翻译成人话摘要（L3 实体级当前态）。 */
export function summarizeEntityThread(thread: EntityThread): EntityThreadSummary {
  const elapsedLabel = thread.elapsedMs !== null && thread.elapsedMs > 0 ? ` · 耗时 ${formatDurationMs(thread.elapsedMs)}` : '';
  switch (thread.currentState) {
    case 'completed':
      return { stateLabel: `已完成${elapsedLabel}`, tone: 'active' };
    case 'failed':
      return { stateLabel: '已失败', tone: 'blocked' };
    case 'suspended':
      return { stateLabel: '已挂起（等待依赖）', tone: 'idle' };
    case 'unblocked':
      return { stateLabel: '依赖已就绪，可继续', tone: 'active' };
    default:
      return { stateLabel: `进行中（${thread.currentStateZh}）${elapsedLabel}`, tone: thread.currentTone };
  }
}

/** 毫秒 → 紧凑时长（27s / 1m 3s / 2m / 1h 5m）。<1s 显示毫秒。 */
function formatDurationMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '--';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remSeconds = seconds % 60;
  if (minutes < 60) return remSeconds ? `${minutes}m ${remSeconds}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}
