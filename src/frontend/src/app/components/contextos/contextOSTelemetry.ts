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
 * 诚实原则（关键）：识别后端**真实**的规范事件词汇，不臆造、不伪造精度。
 *   - journal `llm` 通道（CanonicalLogEventV2）携带真实 per-call 用量与时延：
 *     raw.stream_event ∈ {llm_waiting, llm_completed, llm_failed}、
 *     raw.data.{prompt_tokens, completion_tokens, context_tokens_after}、
 *     raw.data.metadata.elapsed_ms。这些经 parseLlmStreamLine 注入 LogEntry.meta 实时送达。
 *     → 据此实时还原：调用次数、真实 token 聚合、真实时延、按角色用量、错误数、上下文规模。
 *   - runtime_events 通道（emit_event）携带 prompt_context / context.build（含 items_count /
 *     total_tokens / snapshot_hash）等装配观测，经 parseRuntimeEvent 的 meta=data/output 保真送达。
 *     → 据此识别投影/在窗项数/快照回执。注意：弱模型 PM-only 真实 run 仅发 prompt_context（无
 *     items_count/snapshot），故投影计数可得、in-window items/快照随后端是否发 context.build 而定，
 *     缺失时诚实留空，不臆造。
 *
 * 旧实现轮询 `runtime/events/llm.observations.jsonl`——后端任何代码路径都不写入的幽灵文件，
 * 故真实运行时仪表盘永不更新。本模块改为直接消费 WS 既有实时流，根除该缺陷。
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
  /** 从结构化 meta / 事件名 / 摘要推断出的角色归属，不改变原始 actor。 */
  roleHints: string[];
  name: string;
  kind: string;
  /** 流通道 / 事件子类（llm / runtime_events / process …），缺省 unknown。 */
  mode: string;
  iteration: number | null;
  summary: string;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  /** 是否携带真实 token 用量（journal `llm` 通道 raw.data.prompt/completion_tokens）。 */
  hasUsage: boolean;
  /** 该次 usage 是否为后端字符估算。journal usage 为真实计数，恒 false。 */
  estimatedTokens: boolean;
  /** 真实时延（ms）：meta.durationMs（raw.data.metadata.elapsed_ms）优先，回退从 details 还原。 */
  durationMs: number | null;
  error: string | null;
  /** 是否为落盘快照回执。WS 展示级流无法可靠区分，恒 false（诚实，不过度声明）。 */
  hasReceipt: boolean;
  contextHash: string | null;
  /** 上下文项数（context.build 的 items_count，经 runtime_events meta 送达）；缺失 null。 */
  contextItems: number | null;
  /** 上下文 token 规模（context.build total_tokens 或 llm context_tokens_after）；缺失 null。 */
  contextTokens: number | null;
  /** Final provider request audit: token estimate, tool schema size, and coverage flags. */
  finalRequestContextAudit: Record<string, unknown> | null;
  /** SHA-256 reference to the stored full context (post-compression messages). */
  contextSnapshotRef: string | null;
  /** Structured evidence when the snapshot could not be stored. */
  contextSnapshotDegraded: ContextSnapshotDegraded | null;
  /** SHA-256 of the serialized prompt (for integrity/audit). */
  promptHash: string | null;
  /** Correlates with the turn transaction this call belongs to. */
  turnId: string | null;
  /** Correlates start/end/preview events that belong to the same provider call. */
  callId: string | null;
  /** 是否为上下文装配 / 投影事件（按事件名/消息识别 context.build / prompt_context / projection）。 */
  isProjection: boolean;
  /** 是否为一次离散 LLM 调用（llm_completed / llm_failed 或旧版 invoke_done / invoke_error）。 */
  isCall: boolean;
  /**
   * Phase 3+：该事件归属的 worker id（仅多 worker Director 调用携带）。
   * 来源：meta.worker_id / meta.workerId，缺失时为 null。
   * 后端未发时一律 null——绝不伪造 worker 归属。
   */
  workerId: string | null;
  category: 'projection' | 'call' | 'tool' | 'error' | 'state' | 'event';
}

export interface ContextSnapshotDegraded {
  code: string;
  reason: string;
  message: string;
  exceptionType: string;
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

export interface RoleAggregate {
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  calls: number;
  usageCalls: number;
  events: number;
}

/**
 * Phase 3+ 多 worker LLM 追踪：单 worker 聚合。
 * 来源 = `meta.worker_id` / `meta.workerId` 在 logEntryToEvent 中提取；
 * 后端尚未发出 worker_id 时聚合为空对象（hasWorkers=false），UI 据实降级。
 */
export interface WorkerAggregate {
  workerId: string;
  role: string;
  totalTokens: number;
  calls: number;
  events: number;
  lastEpoch: number | null;
  lastLatencyMs: number | null;
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
  /** 离散 LLM 调用次数（llm_completed / llm_failed 或旧版 invoke_done / invoke_error，真实）。 */
  totalCalls: number;
  /** 其中由后端字符估算得到 usage 的调用数。journal usage 为真实计数，恒 0。 */
  estimatedCalls: number;
  /** 真实 token 聚合（journal `llm` 通道 raw.data 的 prompt/completion usage 之和）。 */
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  /** 上下文装配 / 投影事件数（按事件名识别，真实）。 */
  projectionCount: number;
  /** 落盘快照回执数（context.snapshot 的 snapshot_hash 签名计数）；后端未发时为 0。 */
  receiptCount: number;
  /** 最近一次装配（context.build）的真实上下文项数；后端未发 context.build 时为 null。 */
  contextItemsCount: number | null;
  /** 最近一次装配/调用的上下文 token 规模；缺失时 null。 */
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
  /** Full-stream per-role aggregate. Unlike `events`, this is not truncated to MAX_EVENTS. */
  byRole: Record<string, RoleAggregate>;
  /**
   * Phase 3+：按 worker_id 聚合的实时统计。
   * 无任何事件携带 worker_id 时为空对象（hasWorkers=false），UI 据实降级。
   */
  byWorker: Record<string, WorkerAggregate>;
  /** 是否识别到至少一条带 worker_id 的事件（用于 UI 判断是否渲染多 worker 面板）。 */
  hasWorkers: boolean;
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
  byRole: {},
  byWorker: {},
  hasWorkers: false,
};

/** 事件流截断上限（防止超长流拖垮渲染）。 */
const MAX_EVENTS = 120;

/** WS 各流的环形缓冲上限（与 useRuntime store 保持一致，用于判定 windowed）。 */
const STREAM_CAPS = { llm: 180, execution: 100, process: 240 } as const;

/** 角色 id → 观测 actor 的匹配别名（用于把真实事件归并到 5 个角色卡）。 */
export const ACTOR_ROLE_ALIASES: Record<string, string[]> = {
  pm: ['pm'],
  architect: ['architect'],
  chief_engineer: ['chief', 'engineer'],
  director: ['director'],
  qa: ['qa', 'reviewer'],
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function nonEmptyString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readContextSnapshotDegraded(meta: Record<string, unknown>): ContextSnapshotDegraded | null {
  const degraded = isRecord(meta['contextSnapshotDegraded'])
    ? meta['contextSnapshotDegraded']
    : isRecord(meta['context_snapshot_degraded'])
      ? meta['context_snapshot_degraded']
      : null;
  const reasonAlias =
    nonEmptyString(meta['contextSnapshotDegradedReason']) ||
    nonEmptyString(meta['context_snapshot_degraded_reason']);
  if (!degraded && !reasonAlias) return null;
  return {
    code: nonEmptyString(degraded?.['code']) || 'CONTEXT_SNAPSHOT_DEGRADED',
    reason: nonEmptyString(degraded?.['reason']) || reasonAlias || 'context_snapshot_degraded',
    message: nonEmptyString(degraded?.['message']),
    exceptionType:
      nonEmptyString(degraded?.['exception_type']) ||
      nonEmptyString(degraded?.['exceptionType']),
  };
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

  // 离散 LLM 调用：一次「完成」事件计一次调用。规范 journal `llm` 通道用 llm_completed/llm_failed；
  // 旧版 *.llm.events.jsonl 用 invoke_done/invoke_error。两套词汇都识别。
  const isCall =
    streamEvent === 'invoke_done' ||
    streamEvent === 'invoke_error' ||
    streamEvent === 'llm_completed' ||
    streamEvent === 'llm_failed' ||
    streamEvent === 'llm_call_end' ||
    streamEvent === 'llm_call_error' ||
    streamEvent === 'call_end' ||
    streamEvent === 'call_error';

  let category: ContextOSEvent['category'];
  if (isError || streamEvent === 'invoke_error' || streamEvent === 'llm_failed' || streamEvent === 'llm_call_error' || streamEvent === 'call_error') category = 'error';
  else if (streamEvent === 'tool_call' || streamEvent === 'tool_result') category = 'tool';
  else if (isProjection) category = 'projection';
  else if (isCall) category = 'call';
  else if (streamEvent === 'thinking_chunk' || streamEvent === 'content_chunk' || streamEvent === 'llm_waiting') category = 'state';
  else if (channel === 'llm' || token.includes('invoke') || token.includes('llm')) category = 'call';
  else if (token.includes('waiting') || token.includes('idle') || token.includes('state')) category = 'state';
  else category = 'event';

  return { category, isCall };
}

function addRoleHint(hints: Set<string>, roleId: string): void {
  if (roleId) hints.add(roleId);
}

function collectRoleHints(params: {
  actor: string;
  channel: string;
  streamEvent: string;
  text: string;
  meta: Record<string, unknown>;
}): string[] {
  const { actor, channel, streamEvent, text, meta } = params;
  const metaText = Object.entries(meta)
    .map(([key, value]) => `${key} ${typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : ''}`)
    .join(' ');
  const token = `${actor} ${channel} ${streamEvent} ${text} ${metaText}`.toLowerCase();
  const gate = nonEmptyString(meta['gate']).toLowerCase();
  const benchType = nonEmptyString(meta['bench_event_type']).toLowerCase();
  const role = nonEmptyString(meta['role']).toLowerCase();
  const hints = new Set<string>();

  if (role) {
    if (role.includes('pm')) addRoleHint(hints, 'pm');
    if (role.includes('architect')) addRoleHint(hints, 'architect');
    if (role.includes('chief') || role.includes('engineer')) addRoleHint(hints, 'chief_engineer');
    if (role.includes('director')) addRoleHint(hints, 'director');
    if (role.includes('qa') || role.includes('reviewer')) addRoleHint(hints, 'qa');
  }

  if (
    token.includes('pm_planning') ||
    token.includes('plan_artifact_present') ||
    token.includes('task contract') ||
    token.includes('pm task') ||
    benchType === 'factory_bench.project.started'
  ) {
    addRoleHint(hints, 'pm');
  }

  if (
    token.includes('architect') ||
    token.includes('architecture') ||
    token.includes('design review')
  ) {
    addRoleHint(hints, 'architect');
  }

  if (
    token.includes('chief_engineer') ||
    token.includes('chief engineer') ||
    token.includes('blueprint') ||
    token.includes('handoff') ||
    token.includes('ce_task') ||
    gate.includes('blueprint')
  ) {
    addRoleHint(hints, 'chief_engineer');
  }

  if (
    token.includes('director') ||
    token.includes('implementation') ||
    token.includes('director_dispatch') ||
    token.includes('write_file') ||
    token.includes('tool_call') ||
    token.includes('chain_clean') ||
    benchType === 'factory_bench.project.completed' ||
    gate.includes('chain_clean')
  ) {
    addRoleHint(hints, 'director');
  }

  if (
    token.includes('qa') ||
    token.includes('quality') ||
    token.includes('verdict') ||
    token.includes('integration_qa') ||
    token.includes('wrong_product_guard') ||
    token.includes('test') ||
    gate.includes('qa') ||
    gate.includes('verdict') ||
    gate.includes('wrong_product')
  ) {
    addRoleHint(hints, 'qa');
  }

  return Array.from(hints);
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
  const rawMeta = isRecord(log.meta) ? log.meta : {};
  const nestedOutput = isRecord(rawMeta['output']) ? rawMeta['output'] : {};
  const nestedData = isRecord(rawMeta['data']) ? rawMeta['data'] : {};
  const nestedMetadata = isRecord(rawMeta['metadata'])
    ? rawMeta['metadata']
    : isRecord(nestedData['metadata'])
      ? nestedData['metadata']
      : isRecord(nestedOutput['metadata'])
        ? nestedOutput['metadata']
        : {};
  const meta = { ...nestedOutput, ...nestedData, ...nestedMetadata, ...rawMeta };
  const streamEvent = (
    nonEmptyString(meta['streamEvent']) ||
    nonEmptyString(meta['stream_event']) ||
    nonEmptyString(meta['event_type']) ||
    (log.tags && log.tags[0]) ||
    ''
  ).toLowerCase();
  const channel = nonEmptyString(meta['channel']) || channelFallback;
  const actor = nonEmptyString(log.source) || 'System';
  const isError = log.level === 'error';
  const text = `${log.title || ''} ${log.message || ''}`;
  const token = `${streamEvent} ${text}`.toLowerCase();

  // 结构化信号（来自 meta = 事件 data/output）。
  const contextItems = toFiniteOrNull(meta['items_count']);
  // 上下文规模：context.build 的 total_tokens（全量装配规模）优先；llm 通道的 context_tokens_after 次之。
  const contextTokens = contextItems !== null
    ? toFiniteOrNull(meta['total_tokens']) ?? toFiniteOrNull(meta['contextTokens'])
    : toFiniteOrNull(meta['contextTokens']) ??
      toFiniteOrNull(meta['context_tokens_after']) ??
      toFiniteOrNull(meta['context_tokens_before']);
  const finalRequestContextAudit = isRecord(meta['final_request_context_audit'])
    ? meta['final_request_context_audit']
    : isRecord(meta['finalRequestContextAudit'])
      ? meta['finalRequestContextAudit']
      : null;
  const snapshotHash = nonEmptyString(meta['snapshot_hash']);
  const requestHash = nonEmptyString(meta['request_hash']);
  const contextHash = nonEmptyString(meta['context_hash']) || requestHash || null;
  const contextSnapshotRef = nonEmptyString(meta['contextSnapshotRef']) || nonEmptyString(meta['context_snapshot_ref']);
  const contextSnapshotDegraded = readContextSnapshotDegraded(meta);
  const promptHash = nonEmptyString(meta['promptHash']) || nonEmptyString(meta['prompt_hash']);
  const turnId = nonEmptyString(meta['turnId']) || nonEmptyString(meta['turn_id']);
  const callId = nonEmptyString(meta['callId']) || nonEmptyString(meta['call_id']);
  // Phase 3+：多 worker Director / 并发 LLM 调用的 worker 归属（meta.worker_id / meta.workerId）。
  // 后端未发时一律 null，绝不冒充。
  const workerId = nonEmptyString(meta['worker_id']) || nonEmptyString(meta['workerId']) || null;

  // 真实 per-call 用量（来自 journal `llm` 通道 raw.data，经 parseLlmStreamLine 注入 meta）。
  // 兼容 snake_case（runtime_events 通道可能用 prompt_tokens/completion_tokens/total_tokens）。
  const nestedUsage = isRecord(meta['usage']) ? meta['usage'] : {};
  const usagePromptTokens =
    toFiniteOrNull(meta['promptTokens']) ??
    toFiniteOrNull(meta['prompt_tokens']) ??
    toFiniteOrNull(meta['inputTokens']) ??
    toFiniteOrNull(meta['input_tokens']) ??
    toFiniteOrNull(nestedUsage['promptTokens']) ??
    toFiniteOrNull(nestedUsage['prompt_tokens']) ??
    toFiniteOrNull(nestedUsage['inputTokens']) ??
    toFiniteOrNull(nestedUsage['input_tokens']) ??
    0;
  const usageCompletionTokens =
    toFiniteOrNull(meta['completionTokens']) ??
    toFiniteOrNull(meta['completion_tokens']) ??
    toFiniteOrNull(meta['outputTokens']) ??
    toFiniteOrNull(meta['output_tokens']) ??
    toFiniteOrNull(nestedUsage['completionTokens']) ??
    toFiniteOrNull(nestedUsage['completion_tokens']) ??
    toFiniteOrNull(nestedUsage['outputTokens']) ??
    toFiniteOrNull(nestedUsage['output_tokens']) ??
    0;
  const usageEvent = streamEvent === 'invoke_done' ||
    streamEvent === 'invoke_error' ||
    streamEvent === 'llm_completed' ||
    streamEvent === 'llm_failed' ||
    streamEvent === 'llm_call_end' ||
    streamEvent === 'llm_call_error' ||
    streamEvent === 'call_end' ||
    streamEvent === 'call_error';
  const nonFinalUsageEvent = streamEvent === 'content_preview' ||
    streamEvent === 'content_chunk' ||
    streamEvent === 'thinking_preview' ||
    streamEvent === 'thinking_chunk' ||
    streamEvent === 'llm_call_start' ||
    streamEvent === 'call_start' ||
    streamEvent === 'llm_waiting';
  const usageAliasTotal =
    toFiniteOrNull(meta['totalTokens']) ??
    (usageEvent
      ? toFiniteOrNull(meta['total_tokens']) ??
        toFiniteOrNull(nestedUsage['totalTokens']) ??
        toFiniteOrNull(nestedUsage['total_tokens'])
      : null);
  const usageTotalTokens = usageAliasTotal ?? (usagePromptTokens + usageCompletionTokens);
  const hasUsage = usageTotalTokens > 0 && !nonFinalUsageEvent && (usageEvent || usagePromptTokens > 0 || usageAliasTotal !== null);
  const accountedPromptTokens = hasUsage ? usagePromptTokens : 0;
  const accountedCompletionTokens = hasUsage ? usageCompletionTokens : 0;
  const accountedTotalTokens = hasUsage ? usageTotalTokens : 0;
  const metaDurationMs = toFiniteOrNull(meta['durationMs']) ?? toFiniteOrNull(meta['elapsed_ms']);

  // 投影 / 上下文装配的识别（按可靠性递减）：
  //  ① context.build 携带 items_count（装配规模，最可靠签名）；
  //  ② prompt_context 经 parseRuntimeEvent 后 name 被 summary「Prompt Context Injection」覆盖，但
  //     output(=meta) 保真携带 persona_id / strategy / token_usage_estimate 等投影签名字段；
  //  ③ 文本兜底（覆盖真实 summary 形态：prompt context / context injection / ContextPack …）。
  // 注意 context.snapshot 也带 request_hash，故不能用 request_hash 判投影（否则把快照回执误计为投影）。
  const personaId = nonEmptyString(meta['persona_id']);
  const projectionStrategy = nonEmptyString(meta['strategy']);
  const isProjection =
    contextItems !== null ||
    Boolean(personaId) ||
    Boolean(projectionStrategy) ||
    token.includes('context.build') ||
    token.includes('prompt_context') ||
    token.includes('prompt context') ||
    token.includes('context injection') ||
    token.includes('contextpack') ||
    token.includes('context pack') ||
    token.includes('projection') ||
    token.includes('context_assembl') ||
    token.includes('context.item');

  // 落盘快照回执：以 context.snapshot 的 snapshot_hash 为唯一签名。注意真实 context.build 的 output
  // 也带 snapshot_hash，但它同时带 items_count（是装配事件而非回执），故按「有 snapshot_hash 且无
  // items_count」识别真正的回执，避免把同一次快照在 build + snapshot 两条事件上重复计数。
  const hasReceipt = Boolean(snapshotHash) && contextItems === null;

  const { category, isCall } = classifyStream({ streamEvent, channel, text, isError, isProjection });
  const roleHints = collectRoleHints({ actor, channel, streamEvent, text, meta });
  // 真实时延：meta.durationMs（journal raw.data.metadata.elapsed_ms）优先，回退从 details 文本还原。
  const durationMs = metaDurationMs ?? parseLatencyMs(log.details);

  return {
    id: nonEmptyString(log.id) || `ws-${channel}-${index}`,
    seq: index,
    ts: nonEmptyString(log.timestamp),
    epoch: toEpochMs(nonEmptyString(log.timestamp)),
    actor,
    roleHints,
    name: nonEmptyString(log.title) || streamEvent || nonEmptyString(meta['streamEvent']),
    kind: channel || 'stream',
    mode: channel || 'unknown',
    iteration: null,
    summary: (nonEmptyString(log.message) || nonEmptyString(log.title) || streamEvent).replace(/\s+/g, ' ').trim().slice(0, 160),
    promptTokens: accountedPromptTokens,
    completionTokens: accountedCompletionTokens,
    totalTokens: accountedTotalTokens,
    hasUsage,
    estimatedTokens: false,
    durationMs,
    error: isError ? nonEmptyString(log.details) || nonEmptyString(log.message) || 'error' : null,
    hasReceipt,
    contextHash,
    contextItems,
    contextTokens,
    finalRequestContextAudit,
    contextSnapshotRef: contextSnapshotRef || null,
    contextSnapshotDegraded,
    promptHash: promptHash || null,
    turnId: turnId || null,
    callId: callId || null,
    workerId,
    isProjection,
    isCall,
    category,
  };
}

function eventDedupeKey(event: ContextOSEvent): string {
  const stableRef = event.callId || event.contextSnapshotRef || event.promptHash || event.turnId || '';
  if (stableRef && (event.isCall || event.hasUsage)) {
    return [
      'llm-call',
      event.actor,
      event.workerId ?? '',
      stableRef,
      String(event.promptTokens),
      String(event.completionTokens),
      String(event.totalTokens),
      event.error ?? '',
    ].join('\u001f');
  }
  return [
    event.mode,
    event.name,
    event.category,
    event.actor,
    event.workerId ?? '',
    event.epoch > 0 ? String(event.epoch) : event.ts,
    stableRef,
    event.summary,
    String(event.promptTokens),
    String(event.completionTokens),
    String(event.totalTokens),
    String(event.durationMs ?? ''),
    String(event.contextItems ?? ''),
    String(event.contextTokens ?? ''),
    event.error ?? '',
  ].join('\u001f');
}

function dedupeEvents(events: ContextOSEvent[]): ContextOSEvent[] {
  if (events.length <= 1) return events;
  const seen = new Set<string>();
  const deduped: ContextOSEvent[] = [];
  for (const event of events) {
    const key = eventDedupeKey(event);
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(event);
  }
  return deduped;
}

function aggregateEvents(events: ContextOSEvent[], windowed: boolean): ContextOSTelemetry {
  events = dedupeEvents(events);
  if (events.length === 0) return EMPTY_TELEMETRY;

  let totalCalls = 0;
  let estimatedCalls = 0;
  let totalTokens = 0;
  let promptTokens = 0;
  let completionTokens = 0;
  let projectionCount = 0;
  let receiptCount = 0;
  let errorCount = 0;
  let latencySum = 0;
  let latencyCount = 0;
  const byMode: Record<string, ModeAggregate> = {};
  const byActor: Record<string, ActorAggregate> = {};
  const byRole: Record<string, RoleAggregate> = {};
  const byWorker: Record<string, WorkerAggregate> = {};
  let hasWorkers = false;

  for (const event of events) {
    if (event.isProjection) projectionCount += 1;
    if (event.hasReceipt) receiptCount += 1;
    if (event.category === 'error') errorCount += 1;
    if (event.durationMs !== null) {
      latencySum += event.durationMs;
      latencyCount += 1;
    }

    // 真实 token 聚合（journal `llm` 通道携带 prompt/completion usage）。
    totalTokens += event.totalTokens;
    promptTokens += event.promptTokens;
    completionTokens += event.completionTokens;
    if (event.estimatedTokens) estimatedCalls += 1;

    const actorKey = event.actor;
    const actorAgg = byActor[actorKey] ?? { totalTokens: 0, calls: 0, events: 0 };
    actorAgg.events += 1;
    actorAgg.totalTokens += event.totalTokens;

    if (event.isCall || event.hasUsage) {
      totalCalls += 1;
      const modeKey = event.mode || 'unknown';
      const modeAgg = byMode[modeKey] ?? { totalTokens: 0, calls: 0 };
      modeAgg.calls += 1;
      modeAgg.totalTokens += event.totalTokens;
      byMode[modeKey] = modeAgg;
      actorAgg.calls += 1;
    }
    byActor[actorKey] = actorAgg;

    for (const roleId of Object.keys(ACTOR_ROLE_ALIASES)) {
      if (!eventMatchesRole(event, roleId)) continue;
      const roleAgg = byRole[roleId] ?? {
        totalTokens: 0,
        promptTokens: 0,
        completionTokens: 0,
        calls: 0,
        usageCalls: 0,
        events: 0,
      };
      roleAgg.events += 1;
      roleAgg.totalTokens += event.totalTokens;
      roleAgg.promptTokens += event.promptTokens;
      roleAgg.completionTokens += event.completionTokens;
      if (event.isCall || event.hasUsage) roleAgg.calls += 1;
      if (event.hasUsage) roleAgg.usageCalls += 1;
      byRole[roleId] = roleAgg;
    }

    // Phase 3 多 worker 聚合：仅对携带 worker_id 的事件计入；后端未发时整字段为空（hasWorkers=false）。
    if (event.workerId) {
      hasWorkers = true;
      const workerAgg = byWorker[event.workerId] ?? {
        workerId: event.workerId,
        role: event.actor,
        totalTokens: 0,
        calls: 0,
        events: 0,
        lastEpoch: null,
        lastLatencyMs: null,
      };
      workerAgg.events += 1;
      workerAgg.totalTokens += event.totalTokens;
      if (event.isCall || event.hasUsage) workerAgg.calls += 1;
      if (event.epoch > 0 && (workerAgg.lastEpoch === null || event.epoch > workerAgg.lastEpoch)) {
        workerAgg.lastEpoch = event.epoch;
        // 最近一次活动对应的事件携带的 actor 作为 worker 角色标记。
        workerAgg.role = event.actor;
      }
      if (event.durationMs !== null) {
        if (workerAgg.lastLatencyMs === null || (event.epoch > 0 && event.epoch >= (workerAgg.lastEpoch ?? 0))) {
          workerAgg.lastLatencyMs = event.durationMs;
        }
      }
      byWorker[event.workerId] = workerAgg;
    }
  }

  // 按 epoch 倒序（稳定排序，等时保留出现序）。
  const sorted = events
    .map((event, index) => ({ event, index }))
    .sort((a, b) => (b.event.epoch - a.event.epoch) || (b.event.seq - a.event.seq) || (a.index - b.index))
    .map((entry) => entry.event);

  const lastWithLatency = sorted.find((event) => event.durationMs !== null);
  const lastEventEpoch = sorted.length > 0 ? sorted[0].epoch || null : null;
  // 最近一次装配（context.build）的真实在窗项数（items_count），经 runtime_events meta 送达。
  const lastContextBuild = sorted.find((event) => event.contextItems !== null);
  // 最近一次上下文规模（context.build total_tokens 或 llm 通道 context_tokens_after）。
  const lastContextSize = sorted.find((event) => event.contextTokens !== null);

  return {
    hasData: true,
    parsedLines: events.length,
    windowed,
    events: sorted.slice(0, MAX_EVENTS),
    totalCalls,
    estimatedCalls,
    totalTokens,
    promptTokens,
    completionTokens,
    projectionCount,
    receiptCount,
    contextItemsCount: lastContextBuild ? lastContextBuild.contextItems : null,
    contextTokensLatest: lastContextSize ? lastContextSize.contextTokens : null,
    errorCount,
    avgLatencyMs: latencyCount > 0 ? Math.round(latencySum / latencyCount) : null,
    lastLatencyMs: lastWithLatency ? lastWithLatency.durationMs : null,
    lastEventEpoch: lastEventEpoch && lastEventEpoch > 0 ? lastEventEpoch : null,
    byMode,
    byActor,
    byRole,
    byWorker,
    hasWorkers,
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

function eventMatchesRole(event: ContextOSEvent, roleId: string): boolean {
  const aliases = ACTOR_ROLE_ALIASES[roleId] ?? [roleId];
  const lowered = event.actor.toLowerCase();
  if (aliases.some((alias) => lowered.includes(alias))) return true;
  return event.roleHints.includes(roleId);
}

/** 汇总某角色在真实遥测里的 token（按 actor 别名匹配，来自 journal `llm` 通道的真实 usage）。 */
export function telemetryRoleTokens(telemetry: ContextOSTelemetry, roleId: string): number {
  const aggregate = telemetry.byRole[roleId];
  if (aggregate) return aggregate.totalTokens;
  return filterEventsForRole(telemetry.events, roleId)
    .reduce((total, event) => total + event.totalTokens, 0);
}

/** 汇总某角色在真实遥测里的事件数（按 actor 别名匹配）。 */
export function telemetryRoleEvents(telemetry: ContextOSTelemetry, roleId: string): number {
  const aggregate = telemetry.byRole[roleId];
  if (aggregate) return aggregate.events;
  return filterEventsForRole(telemetry.events, roleId).length;
}

/** 汇总某角色在真实遥测里的离散 LLM 调用次数（按 actor 别名匹配）。 */
export function telemetryRoleCalls(telemetry: ContextOSTelemetry, roleId: string): number {
  const aggregate = telemetry.byRole[roleId];
  if (aggregate) return aggregate.calls;
  return filterEventsForRole(telemetry.events, roleId)
    .filter((event) => event.isCall || event.hasUsage)
    .length;
}

/**
 * 过滤出属于某角色的事件流（按 actor 别名匹配，结果保持原有倒序）。
 *
 * 用于构建每个角色自己的 ContextOS 内部视图：事件、投影、回执、调用等都从该子集再聚合。
 */
export function filterEventsForRole(events: readonly ContextOSEvent[], roleId: string): ContextOSEvent[] {
  return events.filter((event) => eventMatchesRole(event, roleId));
}

/**
 * 该角色是否拥有真实的「带 usage 的观测通道」。
 *
 * journal `llm` 通道（emit_llm_event → MessageBus → WS）在 raw.data 携带真实 prompt/completion
 * tokens，因此凡是在实时流里产生过带 usage 调用的角色（如 PM/Director）都有真实 token 归并。
 * 据此该角色才以 token 归因展示；无 usage 的角色仍以事件数/时延诚实呈现，不伪造 per-role token。
 */
export function telemetryRoleHasUsageChannel(telemetry: ContextOSTelemetry, roleId: string): boolean {
  return telemetryRoleTokens(telemetry, roleId) > 0;
}
