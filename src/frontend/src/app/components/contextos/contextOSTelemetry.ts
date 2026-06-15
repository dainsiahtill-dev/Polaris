/**
 * ContextOS 实时遥测 — 真实观测流解析层
 *
 * Polaris 把每一次上下文装配 / LLM 调用都写入规范观测日志
 * `runtime/events/llm.observations.jsonl`（schema 见后端 io_events.emit_event）。
 * 每条 `kind:"observation"` 记录携带真实的 ContextOS 内部遥测：
 *   - actor            角色（PM / Director / Architect / QA …）
 *   - name             事件名（如 `prompt_context` = 一次 ProjectionEngine 投影）
 *   - refs.mode/run_id/step
 *   - output.usage     真实 token（prompt / completion / total）
 *   - output.context_snapshot  持久化的上下文快照路径（落盘产物）
 *   - duration_ms      真实时延
 *   - error            真实错误
 *   - ts / ts_epoch    真实时间线
 *
 * 本模块把这条 JSONL 解析成 ContextOS 仪表盘可直接消费的「真实」遥测模型，
 * 不再依赖代理估算 —— 这是「真正能看到 ContextOS 实时数据」的数据底座。
 */

/** ContextOS 真实观测事件（来自 llm.observations.jsonl 的一条记录）。 */
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
  /** refs.mode（任务模式 / 角色路由），缺省 unknown。 */
  mode: string;
  iteration: number | null;
  summary: string;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  /** 是否携带 token 用量（区分「LLM 调用」与「纯投影 / 状态」事件）。 */
  hasUsage: boolean;
  durationMs: number | null;
  error: string | null;
  /** 是否落盘了上下文快照(output.context_snapshot) —— 一次持久化的上下文快照产物。 */
  hasReceipt: boolean;
  contextHash: string | null;
  /** name===prompt_context / context_projection —— ProjectionEngine 投影事件。 */
  isProjection: boolean;
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
  /** 是否解析到任何真实观测记录。 */
  hasData: boolean;
  /** 解析的原始行数（用于「读取了多少条」展示）。 */
  parsedLines: number;
  /** 事件流（按时间倒序，已截断）。 */
  events: ContextOSEvent[];
  /** 携带 usage 的观测条数（= 真实 LLM 调用次数）。 */
  totalCalls: number;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  /** ProjectionEngine 投影事件数（真实）。 */
  projectionCount: number;
  /** 落盘的上下文快照数（真实，output.context_snapshot 计数）。 */
  receiptCount: number;
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

/** 空遥测（无运行 / 无观测文件时的稳定缺省）。 */
export const EMPTY_TELEMETRY: ContextOSTelemetry = {
  hasData: false,
  parsedLines: 0,
  events: [],
  totalCalls: 0,
  totalTokens: 0,
  promptTokens: 0,
  completionTokens: 0,
  projectionCount: 0,
  receiptCount: 0,
  errorCount: 0,
  avgLatencyMs: null,
  lastLatencyMs: null,
  lastEventEpoch: null,
  byMode: {},
  byActor: {},
};

/** 事件流截断上限（防止超长日志拖垮渲染）。 */
const MAX_EVENTS = 120;

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function toFiniteNumber(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function toEpochMs(entry: Record<string, unknown>): number {
  const tsEpoch = entry['ts_epoch'];
  if (typeof tsEpoch === 'number' && Number.isFinite(tsEpoch)) {
    // ts_epoch 是秒级浮点；统一成毫秒。
    return tsEpoch > 1e12 ? tsEpoch : tsEpoch * 1000;
  }
  const ts = entry['ts'];
  if (typeof ts === 'string') {
    const parsed = Date.parse(ts);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function nonEmptyString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function classify(params: {
  kind: string;
  name: string;
  isProjection: boolean;
  hasUsage: boolean;
  isError: boolean;
}): ContextOSEvent['category'] {
  if (params.isError) return 'error';
  if (params.isProjection) return 'projection';
  const token = `${params.kind} ${params.name}`.toLowerCase();
  if (token.includes('tool')) return 'tool';
  if (params.hasUsage || token.includes('call') || token.includes('llm') || token.includes('completed')) {
    return 'call';
  }
  if (params.kind === 'state' || token.includes('waiting') || token.includes('state')) return 'state';
  return 'event';
}

function parseEntry(entry: Record<string, unknown>, index: number): ContextOSEvent | null {
  const kind = nonEmptyString(entry['kind']) || 'observation';
  const actor = nonEmptyString(entry['actor']) || 'system';
  const name = nonEmptyString(entry['name']);
  const refs = asRecord(entry['refs']);
  const output = asRecord(entry['output']);
  const usage = asRecord(output['usage']);
  const hasUsage = Object.keys(usage).length > 0;

  const promptTokens = toFiniteNumber(usage['prompt_tokens']);
  const completionTokens = toFiniteNumber(usage['completion_tokens']);
  const totalFromUsage = toFiniteNumber(usage['total_tokens']);
  const totalTokens = totalFromUsage > 0 ? totalFromUsage : promptTokens + completionTokens;

  const errorText = nonEmptyString(entry['error']);
  const isError = kind === 'error' || Boolean(errorText) || entry['ok'] === false;

  const contextHash = nonEmptyString(output['context_hash']) || null;
  const contextSnapshot = nonEmptyString(output['context_snapshot']);
  // 仅当存在真实落盘的上下文快照路径(context_snapshot)时才算一次"快照"。
  // context_hash 只是投影上下文的标识(每次投影都有)，不代表持久化产物，不计入。
  const hasReceipt = Boolean(contextSnapshot);

  const loweredName = name.toLowerCase();
  const isProjection =
    loweredName === 'prompt_context' ||
    kind === 'context_projection' ||
    loweredName.includes('projection') ||
    loweredName === 'context_projection';

  const durationRaw = toFiniteNumber(entry['duration_ms']);
  const durationMs = durationRaw > 0 ? Math.round(durationRaw) : null;

  // 区分"字段缺失"与"值为 0"：合法的 step/iteration 0（首步）必须保留，不能当缺失。
  const iterationRaw = refs['step'] ?? refs['iteration'];
  const iterationPresent =
    typeof iterationRaw === 'number'
      ? Number.isFinite(iterationRaw)
      : typeof iterationRaw === 'string' && iterationRaw.trim() !== '' && Number.isFinite(Number(iterationRaw));
  const iteration = iterationPresent ? Math.round(Number(iterationRaw)) : null;

  const summary =
    nonEmptyString(entry['summary']) ||
    nonEmptyString(entry['message']) ||
    name ||
    kind;

  return {
    id: nonEmptyString(entry['event_id']) || `obs-${toFiniteNumber(entry['seq']) || index}`,
    seq: Math.round(toFiniteNumber(entry['seq'])),
    ts: nonEmptyString(entry['ts']),
    epoch: toEpochMs(entry),
    actor,
    name,
    kind,
    mode: nonEmptyString(refs['mode']) || 'unknown',
    iteration,
    summary: summary.replace(/\s+/g, ' ').trim().slice(0, 160),
    promptTokens,
    completionTokens,
    totalTokens,
    hasUsage,
    durationMs,
    error: errorText || null,
    hasReceipt,
    contextHash,
    isProjection,
    category: classify({ kind, name, isProjection, hasUsage, isError }),
  };
}

/**
 * 解析 `llm.observations.jsonl` 文本为 ContextOS 真实遥测模型。
 *
 * 防御式解析：逐行 JSON.parse，跳过坏行；缺字段一律取安全缺省，绝不抛出。
 */
export function parseObservationLog(content: string | null | undefined): ContextOSTelemetry {
  if (!content || !content.trim()) return EMPTY_TELEMETRY;

  const lines = content.split('\n');
  const events: ContextOSEvent[] = [];
  let parsedLines = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    let entry: Record<string, unknown>;
    try {
      entry = asRecord(JSON.parse(line));
    } catch {
      continue;
    }
    if (Object.keys(entry).length === 0) continue;
    parsedLines += 1;
    const event = parseEntry(entry, i);
    if (event) events.push(event);
  }

  if (events.length === 0) return EMPTY_TELEMETRY;

  // 聚合（基于全部解析事件，再截断展示）。
  let totalCalls = 0;
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
    byActor[actorKey] = actorAgg;

    if (event.hasUsage) {
      totalCalls += 1;
      totalTokens += event.totalTokens;
      promptTokens += event.promptTokens;
      completionTokens += event.completionTokens;

      const modeKey = event.mode || 'unknown';
      const modeAgg = byMode[modeKey] ?? { totalTokens: 0, calls: 0 };
      modeAgg.totalTokens += event.totalTokens;
      modeAgg.calls += 1;
      byMode[modeKey] = modeAgg;

      actorAgg.totalTokens += event.totalTokens;
      actorAgg.calls += 1;
    }
  }

  // 按 epoch 倒序（稳定排序，等时保留出现序）。
  const sorted = events
    .map((event, index) => ({ event, index }))
    .sort((a, b) => (b.event.epoch - a.event.epoch) || (b.event.seq - a.event.seq) || (a.index - b.index))
    .map((entry) => entry.event);

  const lastWithLatency = sorted.find((event) => event.durationMs !== null);
  const lastEventEpoch = sorted.length > 0 ? sorted[0].epoch || null : null;

  return {
    hasData: true,
    parsedLines,
    events: sorted.slice(0, MAX_EVENTS),
    totalCalls,
    totalTokens,
    promptTokens,
    completionTokens,
    projectionCount,
    receiptCount,
    errorCount,
    avgLatencyMs: latencyCount > 0 ? Math.round(latencySum / latencyCount) : null,
    lastLatencyMs: lastWithLatency ? lastWithLatency.durationMs : null,
    lastEventEpoch: lastEventEpoch && lastEventEpoch > 0 ? lastEventEpoch : null,
    byMode,
    byActor,
  };
}

/** 角色 id → 观测 actor 的匹配别名（用于把真实事件归并到 4 个角色卡）。 */
const ACTOR_ROLE_ALIASES: Record<string, string[]> = {
  pm: ['pm'],
  architect: ['architect'],
  director: ['director'],
  qa: ['qa', 'reviewer'],
};

/** 汇总某角色在真实遥测里的 token（按 actor 别名匹配）。 */
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
