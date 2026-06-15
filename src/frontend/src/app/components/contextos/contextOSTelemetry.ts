/**
 * ContextOS 实时遥测 — 真实 WebSocket 运行时流派生层
 *
 * 数据来源是 Polaris **既有的实时框架**，不是文件轮询：
 *   emit_event / emit_llm_event
 *     → _publish_runtime_event_to_bus(MessageBus)
 *     → WebSocket /v2/ws/runtime
 *     → useRuntime hook（store）
 *     → llmStreamEvents / executionLogs / processStreamEvents（LogEntry[]，WS 推送）
 *
 * 本模块把这些由 WS 实时推送的 `LogEntry` 流派生成 ContextOS 仪表盘可消费的遥测模型。
 * 仪表盘随 WS 事件到达即重渲染，无任何轮询。
 *
 * 诚实原则（关键）：WS 推送的是**展示级**事件（source/message/level/details/meta），
 * 不是结构化观测记录。因此：
 *   - 可实时还原：活动/新鲜度、按类别的事件计数、LLM 调用次数与时延（invoke_done）、
 *     按角色的活动量、错误数、阶段。
 *   - 实时流**不含**精确 per-call token / context items_count / 快照路径 等结构化量，
 *     一律不伪造精度（token 聚合恒为 0，对应 UI 以"事件/时延"诚实呈现）。
 *
 * 旧实现轮询 `runtime/events/llm.observations.jsonl`——这是一个后端任何代码路径都不写入的
 * 幽灵文件（规范事件日志见 kernelone/runtime/defaults.py：runtime.events.jsonl /
 * pm.llm.events.jsonl / director.llm.events.jsonl …），故真实运行时仪表盘永不更新。本模块改为
 * 直接消费 WS 实时流，根除该缺陷。
 */

import type { LogEntry } from '@/types/log';

/** ContextOS 真实观测事件（由一条 WS 推送的 LogEntry 派生）。 */
export interface ContextOSEvent {
  id: string;
  seq: number;
  ts: string;
  /** 可比较的 epoch（毫秒）；不可解析 → 0。 */
  epoch: number;
  /** 原始 actor（保留大小写用于展示与角色过滤）。 */
  actor: string;
  name: string;
  kind: string;
  /** 流通道 / 事件子类（llm / runtime_events / process …），缺省 unknown。 */
  mode: string;
  iteration: number | null;
  summary: string;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  /** 是否携带 token 用量。WS 展示级流不含精确 usage，恒 false（诚实）。 */
  hasUsage: boolean;
  /** 该次 usage 是否为后端字符估算。WS 流无此信息，恒 false。 */
  estimatedTokens: boolean;
  /** 真实时延（ms）；llm_invoke 的时延从 invoke_done 的 details 中还原。 */
  durationMs: number | null;
  error: string | null;
  /** 是否为落盘快照回执。WS 展示级流无法可靠区分，恒 false（诚实，不过度声明）。 */
  hasReceipt: boolean;
  contextHash: string | null;
  /** 上下文项数。WS 流不含 items_count，恒 null（诚实）。 */
  contextItems: number | null;
  /** 上下文 token 数。WS 流不含 total_tokens，恒 null（诚实）。 */
  contextTokens: number | null;
  /** 是否为上下文装配 / 投影事件（按事件名/消息识别 context.build / prompt_context / projection）。 */
  isProjection: boolean;
  /** 是否为一次离散 LLM 调用（invoke_done / invoke_error）——用于实时调用计数。 */
  isCall: boolean;
  category: 'projection' | 'call' | 'tool' | 'error' | 'state' | 'event';
}

export interface ModeAggregate {
  totalTokens: number;
  calls: number;
}

export interface ActorAggregate {
  totalTokens: number;
  calls: number;
  events: number;
}

export interface ContextOSTelemetry {
  /** 是否解析到任何真实 WS 事件。 */
  hasData: boolean;
  /** 纳入聚合的事件条数。 */
  parsedLines: number;
  /** 是否只看到尾部窗口（某条流已达环形缓冲上限，更早的记录已被丢弃）。 */
  windowed: boolean;
  /** 事件流（按时间倒序，已截断）。 */
  events: ContextOSEvent[];
  /** 离散 LLM 调用次数（invoke_done / invoke_error，真实）。 */
  totalCalls: number;
  /** 其中由后端字符估算得到 usage 的调用数。WS 流无此信息，恒 0。 */
  estimatedCalls: number;
  /** token 聚合。WS 展示级流不含精确 usage，恒 0（诚实，不伪造精度）。 */
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  /** 上下文装配 / 投影事件数（按事件名识别，真实）。 */
  projectionCount: number;
  /** 落盘快照回执数。WS 流无法可靠区分，恒 0（诚实）。 */
  receiptCount: number;
  /** 最近一次装配的上下文项数。WS 流不含，恒 null。 */
  contextItemsCount: number | null;
  /** 最近一次装配的上下文 token 数。WS 流不含，恒 null。 */
  contextTokensLatest: number | null;
  /** 错误事件数（真实）。 */
  errorCount: number;
  /** 平均时延（ms，仅统计有 duration 的事件），无则 null。 */
  avgLatencyMs: number | null;
  /** 最近一次有 duration 的事件时延（ms），无则 null。 */
  lastLatencyMs: number | null;
  /** 最近事件 epoch（毫秒），无则 null。 */
  lastEventEpoch: number | null;
  byMode: Record<string, ModeAggregate>;
  byActor: Record<string, ActorAggregate>;
}

/** 空遥测（无 WS 数据时的稳定缺省）。 */
export const EMPTY_TELEMETRY: ContextOSTelemetry = {
  hasData: false,
  parsedLines: 0,
  windowed: false,
  events: [],
  totalCalls: 0,
  estimatedCalls: 0,
  totalTokens: 0,
  promptTokens: 0,
  completionTokens: 0,
  projectionCount: 0,
  receiptCount: 0,
  contextItemsCount: null,
  contextTokensLatest: null,
  errorCount: 0,
  avgLatencyMs: null,
  lastLatencyMs: null,
  lastEventEpoch: null,
  byMode: {},
  byActor: {},
};

/** 事件流截断上限（防止超长流拖垮渲染）。 */
const MAX_EVENTS = 120;

/** WS 各流的环形缓冲上限（与 useRuntime store 保持一致，用于判定 windowed）。 */
const STREAM_CAPS = { llm: 180, execution: 100, process: 240 } as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function nonEmptyString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function toFiniteOrNull(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.round(value);
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return Math.round(parsed);
  }
  return null;
}

function toEpochMs(ts: string): number {
  if (!ts) return 0;
  const parsed = Date.parse(ts);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** 从 LogEntry.details 中还原时延（ms）。invoke_done 的 details 形如 `backend=x chars=120 2400ms`。 */
function parseLatencyMs(details: string | undefined): number | null {
  if (!details) return null;
  const match = /(\d[\d,]*)\s*ms\b/.exec(details);
  if (!match) return null;
  const value = Number(match[1].replace(/,/g, ''));
  return Number.isFinite(value) && value > 0 ? Math.round(value) : null;
}

function classifyStream(params: {
  streamEvent: string;
  channel: string;
  text: string;
  isError: boolean;
  isProjection: boolean;
}): { category: ContextOSEvent['category']; isCall: boolean } {
  const { streamEvent, channel, text, isError, isProjection } = params;
  const token = `${streamEvent} ${text}`.toLowerCase();

  // 离散 LLM 调用：invoke_done 是成功完成，invoke_error 是失败完成；两者都计一次调用。
  const isCall = streamEvent === 'invoke_done' || streamEvent === 'invoke_error';

  let category: ContextOSEvent['category'];
  if (isError || streamEvent === 'invoke_error') category = 'error';
  else if (streamEvent === 'tool_call' || streamEvent === 'tool_result') category = 'tool';
  else if (isProjection) category = 'projection';
  else if (isCall) category = 'call';
  else if (streamEvent === 'thinking_chunk' || streamEvent === 'content_chunk') category = 'state';
  else if (channel === 'llm' || token.includes('invoke') || token.includes('llm')) category = 'call';
  else if (token.includes('waiting') || token.includes('idle') || token.includes('state')) category = 'state';
  else category = 'event';

  return { category, isCall };
}

/**
 * 把一条 WS 推送的 LogEntry 适配成 ContextOSEvent。
 *
 * 关键：runtime_events 通道的 LogEntry.meta = 后端事件的 data/output（见 parseRuntimeEvent），
 * 因此 context.build 的 items_count / total_tokens、context.snapshot 的 snapshot_hash 等**结构化信号**
 * 确实经 WS 保真送达——据此识别投影/装配规模/快照回执，而不仅靠文本匹配（事件名在 LogEntry 里会被
 * summary 覆盖，故文本匹配不可靠）。
 *
 * @param channelFallback 当 LogEntry.meta 未携带 channel 时的回退（llm / runtime_events / process）。
 */
function logEntryToEvent(log: LogEntry, index: number, channelFallback: string): ContextOSEvent | null {
  const meta = isRecord(log.meta) ? log.meta : {};
  const streamEvent = (nonEmptyString(meta['streamEvent']) || (log.tags && log.tags[0]) || '').toLowerCase();
  const channel = nonEmptyString(meta['channel']) || channelFallback;
  const actor = nonEmptyString(log.source) || 'System';
  const isError = log.level === 'error';
  const text = `${log.title || ''} ${log.message || ''}`;
  const token = `${streamEvent} ${text}`.toLowerCase();

  // 结构化信号（来自 meta = 事件 data/output）。
  const contextItems = toFiniteOrNull(meta['items_count']);
  const contextTokens = toFiniteOrNull(meta['total_tokens']);
  const snapshotHash = nonEmptyString(meta['snapshot_hash']);
  const requestHash = nonEmptyString(meta['request_hash']);
  const snapshotPath = nonEmptyString(meta['snapshot_path']);
  const contextHash = nonEmptyString(meta['context_hash']) || requestHash || null;

  // 投影 / 上下文装配：context.build 携带 items_count（装配规模，唯一可靠签名）；
  // 其余投影类（prompt_context / projection）靠文本信号。注意 context.snapshot 也带 request_hash，
  // 故不能用 request_hash 判投影（否则会把快照回执误计为投影）。
  const isProjection =
    contextItems !== null ||
    token.includes('context.build') ||
    token.includes('prompt_context') ||
    token.includes('projection') ||
    token.includes('context_assembl') ||
    token.includes('context.item');

  // 落盘快照回执：以 context.snapshot 的 snapshot_hash 为唯一签名（context.build 也带 snapshot_path，
  // 但无 snapshot_hash，避免重复计数）。
  const hasReceipt = Boolean(snapshotHash) || (Boolean(snapshotPath) && token.includes('snapshot'));

  const { category, isCall } = classifyStream({ streamEvent, channel, text, isError, isProjection });
  const durationMs = parseLatencyMs(log.details);

  return {
    id: nonEmptyString(log.id) || `ws-${channel}-${index}`,
    seq: index,
    ts: nonEmptyString(log.timestamp),
    epoch: toEpochMs(nonEmptyString(log.timestamp)),
    actor,
    name: nonEmptyString(log.title) || streamEvent || nonEmptyString(meta['streamEvent']),
    kind: channel || 'stream',
    mode: channel || 'unknown',
    iteration: null,
    summary: (nonEmptyString(log.message) || nonEmptyString(log.title) || streamEvent).replace(/\s+/g, ' ').trim().slice(0, 160),
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    hasUsage: false,
    estimatedTokens: false,
    durationMs,
    error: isError ? nonEmptyString(log.details) || nonEmptyString(log.message) || 'error' : null,
    hasReceipt,
    contextHash,
    contextItems,
    contextTokens,
    isProjection,
    isCall,
    category,
  };
}

function aggregateEvents(events: ContextOSEvent[], windowed: boolean): ContextOSTelemetry {
  if (events.length === 0) return EMPTY_TELEMETRY;

  let totalCalls = 0;
  let projectionCount = 0;
  let receiptCount = 0;
  let errorCount = 0;
  let latencySum = 0;
  let latencyCount = 0;
  const byMode: Record<string, ModeAggregate> = {};
  const byActor: Record<string, ActorAggregate> = {};

  for (const event of events) {
    if (event.isProjection) projectionCount += 1;
    if (event.hasReceipt) receiptCount += 1;
    if (event.category === 'error') errorCount += 1;
    if (event.durationMs !== null) {
      latencySum += event.durationMs;
      latencyCount += 1;
    }

    const actorKey = event.actor;
    const actorAgg = byActor[actorKey] ?? { totalTokens: 0, calls: 0, events: 0 };
    actorAgg.events += 1;

    if (event.isCall || event.hasUsage) {
      totalCalls += 1;
      const modeKey = event.mode || 'unknown';
      const modeAgg = byMode[modeKey] ?? { totalTokens: 0, calls: 0 };
      modeAgg.calls += 1;
      byMode[modeKey] = modeAgg;
      actorAgg.calls += 1;
    }
    byActor[actorKey] = actorAgg;
  }

  // 按 epoch 倒序（稳定排序，等时保留出现序）。
  const sorted = events
    .map((event, index) => ({ event, index }))
    .sort((a, b) => (b.event.epoch - a.event.epoch) || (b.event.seq - a.event.seq) || (a.index - b.index))
    .map((entry) => entry.event);

  const lastWithLatency = sorted.find((event) => event.durationMs !== null);
  const lastEventEpoch = sorted.length > 0 ? sorted[0].epoch || null : null;
  // 最近一次装配（context.build）的真实规模（items_count / total_tokens），经 WS meta 送达。
  const lastContextBuild = sorted.find((event) => event.contextItems !== null);

  return {
    hasData: true,
    parsedLines: events.length,
    windowed,
    events: sorted.slice(0, MAX_EVENTS),
    totalCalls,
    estimatedCalls: 0,
    totalTokens: 0,
    promptTokens: 0,
    completionTokens: 0,
    projectionCount,
    receiptCount,
    contextItemsCount: lastContextBuild ? lastContextBuild.contextItems : null,
    contextTokensLatest: lastContextBuild ? lastContextBuild.contextTokens : null,
    errorCount,
    avgLatencyMs: latencyCount > 0 ? Math.round(latencySum / latencyCount) : null,
    lastLatencyMs: lastWithLatency ? lastWithLatency.durationMs : null,
    lastEventEpoch: lastEventEpoch && lastEventEpoch > 0 ? lastEventEpoch : null,
    byMode,
    byActor,
  };
}

/**
 * 从 useRuntime 经 WebSocket 实时推送的运行时流派生 ContextOS 遥测。
 *
 * 完全无轮询：组件随 llmStreamEvents / executionLogs / processStreamEvents 这些 props 变化即重渲染。
 *
 * @param llmStreamEvents   LLM 流（channel=llm；invoke / tool / chunk 等子事件）。
 * @param executionLogs     运行时事件流（channel=runtime_events，emit_event 经总线推送的规范事件）。
 * @param processStreamEvents 进程/系统流（channel=process）。
 */
export function buildTelemetryFromStream(
  llmStreamEvents: readonly LogEntry[] | null | undefined,
  executionLogs: readonly LogEntry[] | null | undefined,
  processStreamEvents: readonly LogEntry[] | null | undefined,
): ContextOSTelemetry {
  const llm = Array.isArray(llmStreamEvents) ? llmStreamEvents : [];
  const execution = Array.isArray(executionLogs) ? executionLogs : [];
  const process = Array.isArray(processStreamEvents) ? processStreamEvents : [];

  const events: ContextOSEvent[] = [];
  let cursor = 0;

  for (const log of execution) {
    const event = logEntryToEvent(log, cursor++, 'runtime_events');
    if (event) events.push(event);
  }
  for (const log of llm) {
    const event = logEntryToEvent(log, cursor++, 'llm');
    // 流式 chunk 噪声不计入离散事件集（保持事件类型分布有信号）。
    if (event && event.category !== 'state') events.push(event);
    else if (event && event.category === 'state' && (event.name === 'invoke_start')) events.push(event);
  }
  for (const log of process) {
    const event = logEntryToEvent(log, cursor++, 'process');
    if (event) events.push(event);
  }

  if (events.length === 0) return EMPTY_TELEMETRY;

  const windowed =
    llm.length >= STREAM_CAPS.llm ||
    execution.length >= STREAM_CAPS.execution ||
    process.length >= STREAM_CAPS.process;

  return aggregateEvents(events, windowed);
}

/** 角色 id → 观测 actor 的匹配别名（用于把真实事件归并到 5 个角色卡）。 */
const ACTOR_ROLE_ALIASES: Record<string, string[]> = {
  pm: ['pm'],
  architect: ['architect'],
  chief_engineer: ['chief', 'engineer'],
  director: ['director'],
  qa: ['qa', 'reviewer'],
};

/** 汇总某角色在真实遥测里的 token（按 actor 别名匹配）。WS 流无精确 token，恒 0。 */
export function telemetryRoleTokens(telemetry: ContextOSTelemetry, roleId: string): number {
  const aliases = ACTOR_ROLE_ALIASES[roleId] ?? [roleId];
  let total = 0;
  for (const [actor, agg] of Object.entries(telemetry.byActor)) {
    const lowered = actor.toLowerCase();
    if (aliases.some((alias) => lowered.includes(alias))) {
      total += agg.totalTokens;
    }
  }
  return total;
}

/** 汇总某角色在真实遥测里的事件数（按 actor 别名匹配）。 */
export function telemetryRoleEvents(telemetry: ContextOSTelemetry, roleId: string): number {
  const aliases = ACTOR_ROLE_ALIASES[roleId] ?? [roleId];
  let total = 0;
  for (const [actor, agg] of Object.entries(telemetry.byActor)) {
    const lowered = actor.toLowerCase();
    if (aliases.some((alias) => lowered.includes(alias))) {
      total += agg.events;
    }
  }
  return total;
}

/**
 * 该角色是否拥有真实的「带 usage 的观测通道」。
 *
 * WS 实时流是展示级事件，不含精确 per-call token，因此任何角色都没有「带 usage 的通道」，
 * 恒返回 false。UI 据此对所有角色统一以「事件/时延」诚实呈现，而非伪造 per-role token。
 * （精确 token 需走 /v2/{pm,director}/llm-events 证据端点，非实时 WS 通道。）
 */
export function telemetryRoleHasUsageChannel(_telemetry: ContextOSTelemetry, _roleId: string): boolean {
  return false;
}
