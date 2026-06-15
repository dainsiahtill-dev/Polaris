/**
 * ContextOS 实时遥测 — 真实观测流解析层
 *
 * Polaris 把每一次上下文装配 / LLM 调用都写入规范观测日志
 * `runtime/events/llm.observations.jsonl`（schema 见后端 io_events.emit_event）。
 * 每条 `kind:"observation"` 记录携带真实的 ContextOS 内部遥测：
 *   - actor            角色（PM / Director / System / QA …）
 *   - name             事件名：
 *                        · `context.build`   = ContextEngine 装配一份 ContextPack（全角色，actor=System）
 *                        · `context.snapshot`= 落盘一份上下文快照（output.snapshot_path）
 *                        · `prompt_context`  = PM 规划路径的提示上下文注入（仅 PM）
 *                        · `llm_invoke`      = 一次带 usage 的 LLM 调用（track_usage 发出）
 *   - refs.mode/run_id/step
 *   - output.usage     真实 token（prompt / completion / total，含 estimated 标志）
 *   - output.items_count / total_tokens   ContextPack 装配规模（context.build）
 *   - output.snapshot_path                落盘快照路径（context.snapshot）
 *   - output.duration_ms / duration_ms    真实时延（llm_invoke 的时延在 output.duration_ms）
 *   - error            真实错误
 *   - ts / ts_epoch    真实时间线
 *
 * 本模块把这条 JSONL 解析成 ContextOS 仪表盘可直接消费的「真实」遥测模型，
 * 字段全部源自后端实际写入的观测记录 —— 这是「真正能看到 ContextOS 实时数据」的数据底座。
 * 后端确未写入的量一律标「估算」，绝不伪造精度。
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
  /** 该次 usage 是否为后端字符估算（output.usage.estimated === true）。 */
  estimatedTokens: boolean;
  /** 真实时延（ms）；llm_invoke 的时延在 output.duration_ms，已回退读取。 */
  durationMs: number | null;
  error: string | null;
  /** 是否落盘了上下文快照（name==='context.snapshot' 且 output.snapshot_path 非空）。 */
  hasReceipt: boolean;
  contextHash: string | null;
  /** context.build 装配的上下文项数（output.items_count）；非 context.build 事件为 null。 */
  contextItems: number | null;
  /** context.build 装配的上下文 token 数（output.total_tokens）；非 context.build 事件为 null。 */
  contextTokens: number | null;
  /** name==='context.build'（全角色 ContextEngine 装配）/ 'prompt_context'（PM 注入）—— 投影/上下文装配事件。 */
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
  /** 是否只读到尾部窗口（解析行数已达读取上限，更早的记录未纳入聚合）。 */
  windowed: boolean;
  /** 事件流（按时间倒序，已截断）。 */
  events: ContextOSEvent[];
  /** 携带 usage 的观测条数（= 真实 LLM 调用次数）。 */
  totalCalls: number;
  /** 其中由后端字符估算得到 usage 的调用数（output.usage.estimated）。 */
  estimatedCalls: number;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  /** 上下文装配事件数（context.build 全角色 + PM prompt_context，真实）。 */
  projectionCount: number;
  /** 落盘的上下文快照数（真实，name==='context.snapshot' 计数）。 */
  receiptCount: number;
  /** 最近一次 context.build 装配的上下文项数（真实），无则 null。 */
  contextItemsCount: number | null;
  /** 最近一次 context.build 装配的上下文 token 数（真实），无则 null。 */
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

/** 空遥测（无运行 / 无观测文件时的稳定缺省）。 */
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
  // 后端对部分 provider / 预算兜底用字符估算 usage 并打 estimated=true（usage_metrics.py）。
  const estimatedTokens = hasUsage && usage['estimated'] === true;

  const errorText = nonEmptyString(entry['error']);
  const isError = kind === 'error' || Boolean(errorText) || entry['ok'] === false;

  const contextHash = nonEmptyString(output['context_hash']) || null;
  const loweredName = name.toLowerCase();

  // 落盘快照：以规范的 ContextEngine 事件 name==='context.snapshot'（output.snapshot_path，全角色）
  // 为唯一事实来源。PM 的 prompt_context 也带 context_snapshot 键，但指向同一物理文件，
  // 若同时计数会重复，故只认 context.snapshot。
  const snapshotPath = nonEmptyString(output['snapshot_path']);
  const hasReceipt = loweredName === 'context.snapshot' && Boolean(snapshotPath);

  // 投影 / 上下文装配：name==='context.build' 是后端 ContextEngine 对所有角色装配 ContextPack 的
  // 规范事件（actor=System）；'prompt_context' 是 PM 规划路径的提示注入。两者都是真实的上下文装配信号。
  // 旧的 kind==='context_projection' 分支是死代码（该枚举只走 Python logging，从不写入本观测日志），已移除。
  const isProjection =
    loweredName === 'context.build' ||
    loweredName === 'prompt_context' ||
    loweredName.includes('projection');

  // context.build 携带真实的上下文装配规模（items_count / total_tokens）。
  const isContextBuild = loweredName === 'context.build';
  const contextItems = isContextBuild ? Math.round(toFiniteNumber(output['items_count'])) : null;
  const contextTokens = isContextBuild ? Math.round(toFiniteNumber(output['total_tokens'])) : null;

  // 时延：llm_invoke 由 track_usage 发出，其 duration 在 output.duration_ms；
  // 其余（PM 节点 / 内核事务）写在顶层 duration_ms。两处都回退读取，才能覆盖真实调用时延。
  const topLevelDuration = toFiniteNumber(entry['duration_ms']);
  const durationRaw = topLevelDuration > 0 ? topLevelDuration : toFiniteNumber(output['duration_ms']);
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
    estimatedTokens,
    durationMs,
    error: errorText || null,
    hasReceipt,
    contextHash,
    contextItems,
    contextTokens,
    isProjection,
    category: classify({ kind, name, isProjection, hasUsage, isError }),
  };
}

/**
 * 解析 `llm.observations.jsonl` 文本为 ContextOS 真实遥测模型。
 *
 * 防御式解析：逐行 JSON.parse，跳过坏行；缺字段一律取安全缺省，绝不抛出。
 *
 * @param windowLimit 读取该日志时使用的尾部行上限（如 readFile 的 tailLines）。
 *        当解析到的行数达到该上限时，说明只看到尾部窗口，更早的记录未纳入聚合，
 *        windowed=true 让 UI 诚实标注「最近 N 条窗口」而非冒充全程累计。
 */
export function parseObservationLog(content: string | null | undefined, windowLimit?: number): ContextOSTelemetry {
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
      if (event.estimatedTokens) estimatedCalls += 1;
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
  // 最近一次 context.build 的真实装配规模（items_count / total_tokens）。
  const lastContextBuild = sorted.find((event) => event.contextItems !== null);

  return {
    hasData: true,
    parsedLines,
    windowed: typeof windowLimit === 'number' && windowLimit > 0 && parsedLines >= windowLimit,
    events: sorted.slice(0, MAX_EVENTS),
    totalCalls,
    estimatedCalls,
    totalTokens,
    promptTokens,
    completionTokens,
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

/** 角色 id → 观测 actor 的匹配别名（用于把真实事件归并到 5 个角色卡）。 */
const ACTOR_ROLE_ALIASES: Record<string, string[]> = {
  pm: ['pm'],
  architect: ['architect'],
  chief_engineer: ['chief', 'engineer'],
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

/**
 * 该角色是否拥有真实的「带 usage 的观测通道」。
 *
 * 后端目前只有 PM 路径构造 UsageContext（usage_metrics.py / pm_planning），
 * 因此只有 PM 的观测携带 output.usage → 只有 PM 有真实 token 归并。
 * 其余角色（architect/director/qa/chief_engineer）即便在跑，也不产生带 usage 的观测，
 * 其 token 恒为 0。UI 据此把「无 usage 通道」与「空闲」区分开，避免把结构性的 0 当作真实零用量。
 */
export function telemetryRoleHasUsageChannel(telemetry: ContextOSTelemetry, roleId: string): boolean {
  const aliases = ACTOR_ROLE_ALIASES[roleId] ?? [roleId];
  for (const [actor, agg] of Object.entries(telemetry.byActor)) {
    const lowered = actor.toLowerCase();
    if (aliases.some((alias) => lowered.includes(alias)) && agg.calls > 0) {
      return true;
    }
  }
  return false;
}
