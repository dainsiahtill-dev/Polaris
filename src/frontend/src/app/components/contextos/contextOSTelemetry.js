/**
 * ContextOS 实时遥测 — 真实 WebSocket 运行时流派生层
 *
 * 数据来源是 Polaris **既有的实时框架**，不是文件轮询：
 *   emit_event / emit_llm_event
 *     → runtime bus publisher
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
 *   - system 通道携带 prompt_context / context.build（含 items_count /
 *     total_tokens / snapshot_hash）等装配观测，经 parseRuntimeEvent 的 meta=data/output 保真送达。
 *     → 据此识别投影/在窗项数/快照回执。注意：弱模型 PM-only 真实 run 仅发 prompt_context（无
 *     items_count/snapshot），故投影计数可得、in-window items/快照随后端是否发 context.build 而定，
 *     缺失时诚实留空，不臆造。
 *
 * 旧实现轮询 `runtime/events/llm.observations.jsonl`——后端任何代码路径都不写入的幽灵文件，
 * 故真实运行时仪表盘永不更新。本模块改为直接消费 WS 既有实时流，根除该缺陷。
 */
const CONTEXT_SNAPSHOT_REF_RE = /^[0-9a-f]{24}$/i;
/** 空遥测（无 WS 数据时的稳定缺省）。 */
export const EMPTY_TELEMETRY = {
    hasData: false,
    parsedLines: 0,
    windowed: false,
    events: [],
    totalCalls: 0,
    estimatedCalls: 0,
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
    byProviderModel: {},
    byWorker: {},
    hasWorkers: false,
};
/** 事件流截断上限（防止超长流拖垮渲染）。 */
const MAX_EVENTS = 120;
/** WS 各流的环形缓冲上限（与 useRuntime store 保持一致，用于判定 windowed）。 */
const STREAM_CAPS = { llm: 180, execution: 100, process: 240 };
/** 角色 id → 观测 actor 的匹配别名（用于把真实事件归并到 5 个角色卡）。 */
export const ACTOR_ROLE_ALIASES = {
    pm: ['pm'],
    architect: ['architect'],
    chief_engineer: ['chief', 'engineer'],
    director: ['director'],
    qa: ['qa', 'reviewer'],
};
function isRecord(value) {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}
function nonEmptyString(value) {
    return typeof value === 'string' ? value.trim() : '';
}
function contextSnapshotRefOrNull(value) {
    const text = nonEmptyString(value);
    if (!text)
        return null;
    return CONTEXT_SNAPSHOT_REF_RE.test(text) ? text.toLowerCase() : null;
}
function redactedDisplayText(value) {
    if (value['redacted'] !== true)
        return null;
    const type = nonEmptyString(value['type']);
    const chars = toFiniteOrNull(value['chars']);
    const parts = ['历史事件仅有摘要'];
    if (type)
        parts.push(type);
    if (chars !== null)
        parts.push(`${chars} chars`);
    return parts.join(' · ');
}
function redactedJsonDisplayText(value) {
    const trimmed = value.trim();
    if (!trimmed.startsWith('{') || !trimmed.includes('"redacted"'))
        return null;
    try {
        const parsed = JSON.parse(trimmed);
        return isRecord(parsed) ? redactedDisplayText(parsed) : null;
    }
    catch {
        return null;
    }
}
function displayLogText(value, streamEvent) {
    const text = nonEmptyString(value);
    if (!text)
        return '';
    const redacted = redactedJsonDisplayText(text);
    if (!redacted)
        return text;
    if (streamEvent === 'llm_completed' || streamEvent === 'invoke_done' || streamEvent === 'llm_call_end' || streamEvent === 'call_end') {
        return `LLM 响应已完成 · ${redacted}`;
    }
    if (streamEvent === 'llm_failed' || streamEvent === 'invoke_error' || streamEvent === 'llm_call_error' || streamEvent === 'llm_error' || streamEvent === 'call_error') {
        return `LLM 调用失败 · ${redacted}`;
    }
    return redacted;
}
function readContextSnapshotDegraded(meta) {
    const degraded = isRecord(meta['contextSnapshotDegraded'])
        ? meta['contextSnapshotDegraded']
        : isRecord(meta['context_snapshot_degraded'])
            ? meta['context_snapshot_degraded']
            : null;
    const reasonAlias = nonEmptyString(meta['contextSnapshotDegradedReason']) ||
        nonEmptyString(meta['context_snapshot_degraded_reason']);
    if (!degraded && !reasonAlias)
        return null;
    return {
        code: nonEmptyString(degraded?.['code']) || 'CONTEXT_SNAPSHOT_DEGRADED',
        reason: nonEmptyString(degraded?.['reason']) || reasonAlias || 'context_snapshot_degraded',
        message: nonEmptyString(degraded?.['message']),
        exceptionType: nonEmptyString(degraded?.['exception_type']) ||
            nonEmptyString(degraded?.['exceptionType']),
    };
}
function readFinalRequestTokenEstimate(audit) {
    if (!audit)
        return null;
    return toFiniteOrNull(audit['final_request_token_estimate']) ?? toFiniteOrNull(audit['finalRequestTokenEstimate']);
}
function readAuditToken(audit, snakeKey, camelKey) {
    if (!audit)
        return 0;
    return toFiniteOrNull(audit[snakeKey]) ?? toFiniteOrNull(audit[camelKey]) ?? 0;
}
function firstRecord(...values) {
    for (const value of values) {
        if (isRecord(value))
            return value;
    }
    return null;
}
function sumNumericRecord(value) {
    if (!isRecord(value))
        return 0;
    return Object.values(value).reduce((total, item) => {
        const parsed = toFiniteOrNull(item);
        return total + (parsed ?? 0);
    }, 0);
}
function readContextOSAudit(meta) {
    if (isRecord(meta['context_os_audit']))
        return meta['context_os_audit'];
    if (isRecord(meta['contextOSAudit']))
        return meta['contextOSAudit'];
    return null;
}
function booleanValue(value) {
    if (value === true)
        return true;
    if (typeof value !== 'string')
        return false;
    const normalized = value.trim().toLowerCase();
    return normalized === 'true' || normalized === '1' || normalized === 'yes';
}
function contextOSAuditProjected(audit) {
    if (!audit)
        return false;
    if (booleanValue(audit['projected']))
        return true;
    const stateFirst = isRecord(audit['state_first_context_os'])
        ? audit['state_first_context_os']
        : isRecord(audit['stateFirstContextOS'])
            ? audit['stateFirstContextOS']
            : null;
    return booleanValue(stateFirst?.['projected']);
}
function buildProjectionKey(params) {
    if (!params.isProjection)
        return null;
    if (params.source === 'final-request') {
        const stableRef = params.callId ||
            params.contextSnapshotRef ||
            params.promptHash ||
            params.turnId ||
            params.contextHash;
        return stableRef ? `final:${params.actor}:${stableRef}` : `final:${params.eventId}`;
    }
    if (params.source === 'context-build') {
        const stableRef = params.contextHash || params.contextSnapshotRef || params.promptHash || params.turnId;
        return stableRef ? `build:${params.actor}:${stableRef}` : `build:${params.eventId}`;
    }
    return [
        'text',
        params.actor,
        params.streamEvent,
        params.epoch > 0 ? String(params.epoch) : params.eventId,
        params.summary,
    ].join('\u001f');
}
function toFiniteOrNull(value) {
    if (typeof value === 'number' && Number.isFinite(value))
        return Math.round(value);
    if (typeof value === 'string' && value.trim() !== '') {
        const parsed = Number(value);
        if (Number.isFinite(parsed))
            return Math.round(parsed);
    }
    return null;
}
function toEpochMs(ts) {
    if (!ts)
        return 0;
    const parsed = Date.parse(ts);
    return Number.isFinite(parsed) ? parsed : 0;
}
/** 从 LogEntry.details 中还原时延（ms）。invoke_done 的 details 形如 `backend=x chars=120 2400ms`。 */
function parseLatencyMs(details) {
    if (!details)
        return null;
    const match = /(\d[\d,]*)\s*ms\b/.exec(details);
    if (!match)
        return null;
    const value = Number(match[1].replace(/,/g, ''));
    return Number.isFinite(value) && value > 0 ? Math.round(value) : null;
}
function classifyStream(params) {
    const { streamEvent, channel, text, isError, isProjection } = params;
    const token = `${streamEvent} ${text}`.toLowerCase();
    // 离散 LLM 调用：一次「完成」事件计一次调用。规范 journal `llm` 通道用 llm_completed/llm_failed；
    // 旧版 *.llm.events.jsonl 用 invoke_done/invoke_error。两套词汇都识别。
    const isCall = streamEvent === 'invoke_done' ||
        streamEvent === 'invoke_error' ||
        streamEvent === 'llm_completed' ||
        streamEvent === 'llm_failed' ||
        streamEvent === 'llm_call_end' ||
        streamEvent === 'llm_call_error' ||
        streamEvent === 'llm_error' ||
        streamEvent === 'call_end' ||
        streamEvent === 'call_error' ||
        streamEvent === 'response.completed' ||
        streamEvent === 'response.done' ||
        streamEvent === 'response.failed' ||
        streamEvent === 'message_stop';
    let category;
    if (isError || streamEvent === 'invoke_error' || streamEvent === 'llm_failed' || streamEvent === 'llm_call_error' || streamEvent === 'llm_error' || streamEvent === 'call_error' || streamEvent === 'response.failed')
        category = 'error';
    else if (streamEvent === 'tool_call' || streamEvent === 'tool_result')
        category = 'tool';
    else if (isProjection)
        category = 'projection';
    else if (isCall)
        category = 'call';
    else if (streamEvent === 'thinking_chunk' || streamEvent === 'content_chunk' || streamEvent === 'llm_waiting')
        category = 'state';
    else if (channel === 'llm' || token.includes('invoke') || token.includes('llm'))
        category = 'call';
    else if (token.includes('waiting') || token.includes('idle') || token.includes('state'))
        category = 'state';
    else
        category = 'event';
    return { category, isCall };
}
function addRoleHint(hints, roleId) {
    if (roleId)
        hints.add(roleId);
}
function collectRoleHints(params) {
    const { actor, channel, streamEvent, text, meta } = params;
    const metaText = Object.entries(meta)
        .map(([key, value]) => `${key} ${typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : ''}`)
        .join(' ');
    const token = `${actor} ${channel} ${streamEvent} ${text} ${metaText}`.toLowerCase();
    const gate = nonEmptyString(meta['gate']).toLowerCase();
    const benchType = nonEmptyString(meta['bench_event_type']).toLowerCase();
    const role = nonEmptyString(meta['role']).toLowerCase();
    const hints = new Set();
    if (role) {
        if (role.includes('pm'))
            addRoleHint(hints, 'pm');
        if (role.includes('architect'))
            addRoleHint(hints, 'architect');
        if (role.includes('chief') || role.includes('engineer'))
            addRoleHint(hints, 'chief_engineer');
        if (role.includes('director'))
            addRoleHint(hints, 'director');
        if (role.includes('qa') || role.includes('reviewer'))
            addRoleHint(hints, 'qa');
        if (hints.size > 0)
            return Array.from(hints);
    }
    if (token.includes('pm_planning') ||
        token.includes('plan_artifact_present') ||
        token.includes('task contract') ||
        token.includes('pm task') ||
        benchType === 'factory_bench.project.started') {
        addRoleHint(hints, 'pm');
    }
    if (token.includes('architect') ||
        token.includes('architecture') ||
        token.includes('design review')) {
        addRoleHint(hints, 'architect');
    }
    if (token.includes('chief_engineer') ||
        token.includes('chief engineer') ||
        token.includes('blueprint') ||
        token.includes('handoff') ||
        token.includes('ce_task') ||
        gate.includes('blueprint')) {
        addRoleHint(hints, 'chief_engineer');
    }
    if (token.includes('director') ||
        token.includes('implementation') ||
        token.includes('director_dispatch') ||
        token.includes('write_file') ||
        token.includes('tool_call') ||
        token.includes('chain_clean') ||
        benchType === 'factory_bench.project.completed' ||
        gate.includes('chain_clean')) {
        addRoleHint(hints, 'director');
    }
    if (token.includes('qa') ||
        token.includes('quality') ||
        token.includes('verdict') ||
        token.includes('integration_qa') ||
        token.includes('wrong_product_guard') ||
        token.includes('test') ||
        gate.includes('qa') ||
        gate.includes('verdict') ||
        gate.includes('wrong_product')) {
        addRoleHint(hints, 'qa');
    }
    return Array.from(hints);
}
/**
 * 把一条 WS 推送的 LogEntry 适配成 ContextOSEvent。
 *
 * 关键：system 通道的 LogEntry.meta = 后端事件的 data/output（见 parseRuntimeEvent），
 * 因此 context.build 的 items_count / total_tokens、context.snapshot 的 snapshot_hash 等**结构化信号**
 * 确实经 WS 保真送达——据此识别投影/装配规模/快照回执，而不仅靠文本匹配（事件名在 LogEntry 里会被
 * summary 覆盖，故文本匹配不可靠）。
 *
 * @param channelFallback 当 LogEntry.meta 未携带 channel 时的回退（llm / system / process）。
 */
function logEntryToEvent(log, index, channelFallback) {
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
    const streamEvent = (nonEmptyString(meta['streamEvent']) ||
        nonEmptyString(meta['stream_event']) ||
        nonEmptyString(meta['event_type']) ||
        (log.tags && log.tags[0]) ||
        '').toLowerCase();
    const channel = nonEmptyString(meta['channel']) || channelFallback;
    const actor = nonEmptyString(log.source) || 'System';
    const isError = log.level === 'error';
    const displayTitle = displayLogText(log.title, streamEvent);
    const displayMessage = displayLogText(log.message, streamEvent);
    const text = `${displayTitle} ${displayMessage}`;
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
    const finalRequestTokenEstimate = readFinalRequestTokenEstimate(finalRequestContextAudit);
    const contextOSAudit = readContextOSAudit(meta);
    const rawFinalRequestProjectionEvidence = finalRequestTokenEstimate !== null ||
        finalRequestContextAudit !== null ||
        contextOSAuditProjected(contextOSAudit);
    const snapshotHash = nonEmptyString(meta['snapshot_hash']);
    const requestHash = nonEmptyString(meta['request_hash']);
    const contextHash = nonEmptyString(meta['context_hash']) || requestHash || null;
    const contextSnapshotRef = contextSnapshotRefOrNull(meta['contextSnapshotRef']) || contextSnapshotRefOrNull(meta['context_snapshot_ref']);
    const contextSnapshotDegraded = readContextSnapshotDegraded(meta);
    const promptHash = nonEmptyString(meta['promptHash']) || nonEmptyString(meta['prompt_hash']);
    const turnId = nonEmptyString(meta['turnId']) || nonEmptyString(meta['turn_id']);
    const callId = nonEmptyString(meta['callId']) || nonEmptyString(meta['call_id']);
    // Phase 3+：多 worker Director / 并发 LLM 调用的 worker 归属（meta.worker_id / meta.workerId）。
    // 后端未发时一律 null，绝不冒充。
    const workerId = nonEmptyString(meta['worker_id']) || nonEmptyString(meta['workerId']) || null;
    const providerId = nonEmptyString(meta['provider_id']) || nonEmptyString(meta['providerId']) || null;
    const providerName = nonEmptyString(meta['provider_name']) || nonEmptyString(meta['providerName']) || nonEmptyString(meta['provider']) || null;
    const model = nonEmptyString(meta['model']) || nonEmptyString(meta['model_name']) || null;
    const bindingId = nonEmptyString(meta['binding_id']) || nonEmptyString(meta['bindingId']) || null;
    const taskId = nonEmptyString(meta['task_id']) || nonEmptyString(meta['taskId']) || null;
    const pmTaskId = nonEmptyString(meta['pm_task_id']) || nonEmptyString(meta['pmTaskId']) || null;
    const chiefBlueprintId = nonEmptyString(meta['chief_blueprint_id']) || nonEmptyString(meta['chiefBlueprintId']) || null;
    const errorCode = nonEmptyString(meta['error_code']) || nonEmptyString(meta['errorCode']) || null;
    const errorCategory = nonEmptyString(meta['error_category']) || nonEmptyString(meta['errorCategory']) || null;
    const providerStatus = toFiniteOrNull(meta['provider_status']) ?? toFiniteOrNull(meta['providerStatus']);
    const retryAfterSeconds = toFiniteOrNull(meta['retry_after']) ?? toFiniteOrNull(meta['retryAfter']);
    const circuitOpenRemainingSeconds = toFiniteOrNull(meta['circuit_open_remaining']) ??
        toFiniteOrNull(meta['circuitOpenRemaining']);
    const exceptionType = nonEmptyString(meta['exception_type']) || nonEmptyString(meta['exceptionType']) || null;
    // 真实 per-call 用量（来自 journal `llm` 通道 raw.data，经 parseLlmStreamLine 注入 meta）。
    // 兼容 snake_case（system 通道可能用 prompt_tokens/completion_tokens/total_tokens）。
    const nestedUsage = isRecord(meta['usage']) ? meta['usage'] : {};
    const inputTokenDetails = firstRecord(meta['input_tokens_details'], meta['prompt_tokens_details'], nestedUsage['input_tokens_details'], nestedUsage['prompt_tokens_details']);
    const outputTokenDetails = firstRecord(meta['output_tokens_details'], meta['completion_tokens_details'], nestedUsage['output_tokens_details'], nestedUsage['completion_tokens_details']);
    const promptTokenAlias = toFiniteOrNull(meta['promptTokens']) ??
        toFiniteOrNull(meta['prompt_tokens']) ??
        toFiniteOrNull(nestedUsage['promptTokens']) ??
        toFiniteOrNull(nestedUsage['prompt_tokens']);
    const inputTokenAlias = toFiniteOrNull(meta['inputTokens']) ??
        toFiniteOrNull(meta['input_tokens']) ??
        toFiniteOrNull(nestedUsage['inputTokens']) ??
        toFiniteOrNull(nestedUsage['input_tokens']);
    const usageCacheCreationTokens = toFiniteOrNull(meta['cacheCreationInputTokens']) ??
        toFiniteOrNull(meta['cache_creation_input_tokens']) ??
        toFiniteOrNull(nestedUsage['cacheCreationInputTokens']) ??
        toFiniteOrNull(nestedUsage['cache_creation_input_tokens']) ??
        0;
    const usageCacheReadTokens = toFiniteOrNull(meta['cacheReadInputTokens']) ??
        toFiniteOrNull(meta['cache_read_input_tokens']) ??
        toFiniteOrNull(nestedUsage['cacheReadInputTokens']) ??
        toFiniteOrNull(nestedUsage['cache_read_input_tokens']) ??
        0;
    const usageCachedTokens = toFiniteOrNull(meta['cachedTokens']) ??
        toFiniteOrNull(meta['cached_tokens']) ??
        toFiniteOrNull(meta['cachedPromptTokens']) ??
        toFiniteOrNull(meta['cached_prompt_tokens']) ??
        toFiniteOrNull(nestedUsage['cachedTokens']) ??
        toFiniteOrNull(nestedUsage['cached_tokens']) ??
        toFiniteOrNull(nestedUsage['cachedPromptTokens']) ??
        toFiniteOrNull(nestedUsage['cached_prompt_tokens']) ??
        toFiniteOrNull(inputTokenDetails?.['cached_tokens']) ??
        toFiniteOrNull(inputTokenDetails?.['cachedTokens']) ??
        usageCacheReadTokens;
    const usageReasoningTokens = toFiniteOrNull(meta['reasoningTokens']) ??
        toFiniteOrNull(meta['reasoning_tokens']) ??
        toFiniteOrNull(nestedUsage['reasoningTokens']) ??
        toFiniteOrNull(nestedUsage['reasoning_tokens']) ??
        toFiniteOrNull(outputTokenDetails?.['reasoning_tokens']) ??
        toFiniteOrNull(outputTokenDetails?.['reasoningTokens']) ??
        0;
    const usageAudioTokens = (toFiniteOrNull(meta['audioTokens']) ??
        toFiniteOrNull(meta['audio_tokens']) ??
        toFiniteOrNull(nestedUsage['audioTokens']) ??
        toFiniteOrNull(nestedUsage['audio_tokens']) ??
        0) + (toFiniteOrNull(inputTokenDetails?.['audio_tokens']) ??
        toFiniteOrNull(inputTokenDetails?.['audioTokens']) ??
        0) + (toFiniteOrNull(outputTokenDetails?.['audio_tokens']) ??
        toFiniteOrNull(outputTokenDetails?.['audioTokens']) ??
        0);
    const serverToolUse = firstRecord(meta['server_tool_use'], meta['serverToolUse'], nestedUsage['server_tool_use'], nestedUsage['serverToolUse']);
    const usageServerToolUseCount = sumNumericRecord(serverToolUse);
    const usagePromptTokens = promptTokenAlias ?? (inputTokenAlias !== null
        ? inputTokenAlias + usageCacheCreationTokens + usageCacheReadTokens
        : 0);
    const usageCompletionTokens = toFiniteOrNull(meta['completionTokens']) ??
        toFiniteOrNull(meta['completion_tokens']) ??
        toFiniteOrNull(meta['outputTokens']) ??
        toFiniteOrNull(meta['output_tokens']) ??
        toFiniteOrNull(nestedUsage['completionTokens']) ??
        toFiniteOrNull(nestedUsage['completion_tokens']) ??
        toFiniteOrNull(nestedUsage['outputTokens']) ??
        toFiniteOrNull(nestedUsage['output_tokens']) ??
        0;
    const usageToolTokens = toFiniteOrNull(meta['toolTokens']) ??
        toFiniteOrNull(meta['tool_tokens']) ??
        toFiniteOrNull(nestedUsage['toolTokens']) ??
        toFiniteOrNull(nestedUsage['tool_tokens']) ??
        (readAuditToken(finalRequestContextAudit, 'tool_schema_token_estimate', 'toolSchemaTokenEstimate') +
            readAuditToken(finalRequestContextAudit, 'response_format_token_estimate', 'responseFormatTokenEstimate'));
    const streamFinalUsage = booleanValue(meta['_internal_provider_usage']) ||
        booleanValue(meta['internalProviderUsage']) ||
        (streamEvent === 'complete' && isRecord(meta['usage'])) ||
        (streamEvent === 'message_delta' && isRecord(meta['usage']) && Boolean(nonEmptyString(meta['stop_reason'])));
    const usageEvent = streamEvent === 'invoke_done' ||
        streamEvent === 'invoke_error' ||
        streamEvent === 'llm_completed' ||
        streamEvent === 'llm_failed' ||
        streamEvent === 'llm_call_end' ||
        streamEvent === 'llm_call_error' ||
        streamEvent === 'llm_error' ||
        streamEvent === 'call_end' ||
        streamEvent === 'call_error' ||
        streamEvent === 'response.completed' ||
        streamEvent === 'response.done' ||
        streamEvent === 'response.failed' ||
        streamEvent === 'message_stop' ||
        streamFinalUsage ||
        (streamEvent === 'complete' && isRecord(meta['usage']));
    const nonFinalUsageEvent = streamEvent === 'content_preview' ||
        streamEvent === 'content_chunk' ||
        streamEvent === 'thinking_preview' ||
        streamEvent === 'thinking_chunk' ||
        streamEvent === 'llm_call_start' ||
        streamEvent === 'call_start' ||
        streamEvent === 'llm_waiting';
    const usageAliasTotal = toFiniteOrNull(meta['totalTokens']) ??
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
    const accountedCachedTokens = hasUsage ? usageCachedTokens : 0;
    const accountedCacheCreationTokens = hasUsage ? usageCacheCreationTokens : 0;
    const accountedCacheReadTokens = hasUsage ? usageCacheReadTokens : 0;
    const accountedToolTokens = (hasUsage || finalRequestTokenEstimate !== null) ? usageToolTokens : 0;
    const accountedReasoningTokens = hasUsage ? usageReasoningTokens : 0;
    const accountedAudioTokens = hasUsage ? usageAudioTokens : 0;
    const accountedServerToolUseCount = hasUsage ? usageServerToolUseCount : 0;
    const usageEstimated = booleanValue(meta['estimated']) ||
        booleanValue(meta['estimated_usage']) ||
        booleanValue(nestedUsage['estimated']);
    const usageSource = hasUsage
        ? streamFinalUsage
            ? 'stream_final'
            : usageEstimated
                ? 'char_estimate'
                : 'provider'
        : finalRequestTokenEstimate !== null
            ? 'request_estimate'
            : 'none';
    const metaDurationMs = toFiniteOrNull(meta['durationMs']) ?? toFiniteOrNull(meta['elapsed_ms']);
    const hasFinalRequestProjectionEvidence = rawFinalRequestProjectionEvidence && !nonFinalUsageEvent;
    // 投影 / 上下文装配的识别（按可靠性递减）：
    //  ① context.build 携带 items_count（装配规模，最可靠签名）；
    //  ② prompt_context 经 parseRuntimeEvent 后 name 被 summary「Prompt Context Injection」覆盖，但
    //     output(=meta) 保真携带 persona_id / strategy / token_usage_estimate 等投影签名字段；
    //  ③ 文本兜底（覆盖真实 summary 形态：prompt context / context injection / ContextPack …）。
    // 注意 context.snapshot 也带 request_hash，故不能用 request_hash 判投影（否则把快照回执误计为投影）。
    const personaId = nonEmptyString(meta['persona_id']);
    const projectionStrategy = nonEmptyString(meta['strategy']);
    const isProjection = contextItems !== null ||
        hasFinalRequestProjectionEvidence ||
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
    const projectionSource = contextItems !== null || Boolean(personaId) || Boolean(projectionStrategy)
        ? 'context-build'
        : hasFinalRequestProjectionEvidence
            ? 'final-request'
            : 'text';
    // 落盘快照回执：以 context.snapshot 的 snapshot_hash 为唯一签名。注意真实 context.build 的 output
    // 也带 snapshot_hash，但它同时带 items_count（是装配事件而非回执），故按「有 snapshot_hash 且无
    // items_count」识别真正的回执，避免把同一次快照在 build + snapshot 两条事件上重复计数。
    const hasReceipt = Boolean(snapshotHash) && contextItems === null;
    const { category, isCall } = classifyStream({ streamEvent, channel, text, isError, isProjection });
    const roleHints = collectRoleHints({ actor, channel, streamEvent, text, meta });
    // 真实时延：meta.durationMs（journal raw.data.metadata.elapsed_ms）优先，回退从 details 文本还原。
    const durationMs = metaDurationMs ?? parseLatencyMs(log.details);
    const eventId = nonEmptyString(log.id) || `ws-${channel}-${index}`;
    const eventTs = nonEmptyString(log.timestamp);
    const epoch = toEpochMs(eventTs);
    const summary = (displayMessage || displayTitle || streamEvent)
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 160);
    return {
        id: eventId,
        seq: index,
        ts: eventTs,
        epoch,
        actor,
        roleHints,
        name: nonEmptyString(log.title) || streamEvent || nonEmptyString(meta['streamEvent']),
        kind: channel || 'stream',
        mode: channel || 'unknown',
        iteration: null,
        summary,
        promptTokens: accountedPromptTokens,
        completionTokens: accountedCompletionTokens,
        totalTokens: accountedTotalTokens,
        hasUsage,
        cachedTokens: accountedCachedTokens,
        cacheCreationTokens: accountedCacheCreationTokens,
        cacheReadTokens: accountedCacheReadTokens,
        toolTokens: accountedToolTokens,
        reasoningTokens: accountedReasoningTokens,
        audioTokens: accountedAudioTokens,
        serverToolUseCount: accountedServerToolUseCount,
        usageSource,
        estimatedTokens: usageSource === 'char_estimate',
        durationMs,
        error: isError ? nonEmptyString(log.details) || nonEmptyString(log.message) || 'error' : null,
        hasReceipt,
        contextHash,
        contextItems,
        contextTokens,
        finalRequestContextAudit,
        finalRequestTokenEstimate,
        contextSnapshotRef: contextSnapshotRef || null,
        contextSnapshotDegraded,
        promptHash: promptHash || null,
        turnId: turnId || null,
        callId: callId || null,
        workerId,
        providerId,
        providerName,
        model,
        bindingId,
        taskId,
        pmTaskId,
        chiefBlueprintId,
        errorCode,
        errorCategory,
        providerStatus,
        retryAfterSeconds,
        circuitOpenRemainingSeconds,
        exceptionType,
        isProjection,
        projectionKey: buildProjectionKey({
            isProjection,
            source: projectionSource,
            actor,
            callId: callId || null,
            contextSnapshotRef: contextSnapshotRef || null,
            promptHash: promptHash || null,
            turnId: turnId || null,
            contextHash,
            eventId,
            streamEvent,
            epoch,
            summary,
        }),
        isCall,
        category,
    };
}
function eventDedupeKey(event) {
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
            String(event.cachedTokens),
            String(event.cacheCreationTokens),
            String(event.cacheReadTokens),
            String(event.toolTokens),
            String(event.reasoningTokens),
            String(event.audioTokens),
            String(event.serverToolUseCount),
            String(event.finalRequestTokenEstimate ?? ''),
            event.error ?? '',
            event.errorCode ?? '',
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
        String(event.cachedTokens),
        String(event.cacheCreationTokens),
        String(event.cacheReadTokens),
        String(event.toolTokens),
        String(event.reasoningTokens),
        String(event.audioTokens),
        String(event.serverToolUseCount),
        String(event.finalRequestTokenEstimate ?? ''),
        String(event.durationMs ?? ''),
        String(event.contextItems ?? ''),
        String(event.contextTokens ?? ''),
        event.error ?? '',
        event.errorCode ?? '',
    ].join('\u001f');
}
function dedupeEvents(events) {
    if (events.length <= 1)
        return events;
    const seen = new Set();
    const deduped = [];
    for (const event of events) {
        const key = eventDedupeKey(event);
        if (seen.has(key))
            continue;
        seen.add(key);
        deduped.push(event);
    }
    return deduped;
}
export function contextOSObservedTokens(event) {
    if (event.finalRequestTokenEstimate !== null)
        return event.finalRequestTokenEstimate;
    if (event.isCall || event.hasUsage)
        return event.contextTokens ?? event.totalTokens;
    return 0;
}
function aggregateEvents(events, windowed) {
    events = dedupeEvents(events);
    if (events.length === 0)
        return EMPTY_TELEMETRY;
    let totalCalls = 0;
    let estimatedCalls = 0;
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
    const projectionKeys = new Set();
    let receiptCount = 0;
    let errorCount = 0;
    let latencySum = 0;
    let latencyCount = 0;
    const byMode = {};
    const byActor = {};
    const byRole = {};
    const byProviderModel = {};
    const byWorker = {};
    let hasWorkers = false;
    for (const event of events) {
        if (event.isProjection)
            projectionKeys.add(event.projectionKey || event.id);
        if (event.hasReceipt)
            receiptCount += 1;
        if (event.category === 'error')
            errorCount += 1;
        if (event.durationMs !== null) {
            latencySum += event.durationMs;
            latencyCount += 1;
        }
        // ContextOS 主 token 聚合优先展示最终 provider request token（含 tools/response_format）。
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
        if (event.estimatedTokens)
            estimatedCalls += 1;
        const actorKey = event.actor;
        const actorAgg = byActor[actorKey] ?? {
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
            calls: 0,
            events: 0,
        };
        actorAgg.events += 1;
        actorAgg.totalTokens += observedTokens;
        actorAgg.promptTokens += event.promptTokens;
        actorAgg.completionTokens += event.completionTokens;
        actorAgg.cachedTokens += event.cachedTokens;
        actorAgg.cacheCreationTokens += event.cacheCreationTokens;
        actorAgg.cacheReadTokens += event.cacheReadTokens;
        actorAgg.toolTokens += event.toolTokens;
        actorAgg.reasoningTokens += event.reasoningTokens;
        actorAgg.audioTokens += event.audioTokens;
        actorAgg.serverToolUseCount += event.serverToolUseCount;
        if (event.isCall || event.hasUsage) {
            totalCalls += 1;
            const modeKey = event.mode || 'unknown';
            const modeAgg = byMode[modeKey] ?? {
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
                calls: 0,
            };
            modeAgg.calls += 1;
            modeAgg.totalTokens += observedTokens;
            modeAgg.promptTokens += event.promptTokens;
            modeAgg.completionTokens += event.completionTokens;
            modeAgg.cachedTokens += event.cachedTokens;
            modeAgg.cacheCreationTokens += event.cacheCreationTokens;
            modeAgg.cacheReadTokens += event.cacheReadTokens;
            modeAgg.toolTokens += event.toolTokens;
            modeAgg.reasoningTokens += event.reasoningTokens;
            modeAgg.audioTokens += event.audioTokens;
            modeAgg.serverToolUseCount += event.serverToolUseCount;
            byMode[modeKey] = modeAgg;
            actorAgg.calls += 1;
        }
        byActor[actorKey] = actorAgg;
        if (event.providerId || event.providerName || event.model) {
            const providerKey = [
                event.providerId ?? '',
                event.providerName ?? '',
                event.model ?? '',
            ].join('\u001f') || 'unknown';
            const providerAgg = byProviderModel[providerKey] ?? {
                key: providerKey,
                providerId: event.providerId,
                providerName: event.providerName,
                model: event.model,
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
                calls: 0,
                events: 0,
                lastEpoch: null,
            };
            providerAgg.events += 1;
            providerAgg.totalTokens += observedTokens;
            providerAgg.promptTokens += event.promptTokens;
            providerAgg.completionTokens += event.completionTokens;
            providerAgg.cachedTokens += event.cachedTokens;
            providerAgg.cacheCreationTokens += event.cacheCreationTokens;
            providerAgg.cacheReadTokens += event.cacheReadTokens;
            providerAgg.toolTokens += event.toolTokens;
            providerAgg.reasoningTokens += event.reasoningTokens;
            providerAgg.audioTokens += event.audioTokens;
            providerAgg.serverToolUseCount += event.serverToolUseCount;
            if (event.isCall || event.hasUsage)
                providerAgg.calls += 1;
            if (event.epoch > 0 && (providerAgg.lastEpoch === null || event.epoch > providerAgg.lastEpoch)) {
                providerAgg.lastEpoch = event.epoch;
            }
            byProviderModel[providerKey] = providerAgg;
        }
        for (const roleId of Object.keys(ACTOR_ROLE_ALIASES)) {
            if (!eventMatchesRole(event, roleId))
                continue;
            const roleAgg = byRole[roleId] ?? {
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
                calls: 0,
                usageCalls: 0,
                events: 0,
            };
            roleAgg.events += 1;
            roleAgg.totalTokens += observedTokens;
            roleAgg.promptTokens += event.promptTokens;
            roleAgg.completionTokens += event.completionTokens;
            roleAgg.cachedTokens += event.cachedTokens;
            roleAgg.cacheCreationTokens += event.cacheCreationTokens;
            roleAgg.cacheReadTokens += event.cacheReadTokens;
            roleAgg.toolTokens += event.toolTokens;
            roleAgg.reasoningTokens += event.reasoningTokens;
            roleAgg.audioTokens += event.audioTokens;
            roleAgg.serverToolUseCount += event.serverToolUseCount;
            if (event.isCall || event.hasUsage)
                roleAgg.calls += 1;
            if (event.hasUsage)
                roleAgg.usageCalls += 1;
            byRole[roleId] = roleAgg;
        }
        // Phase 3 多 worker 聚合：仅对携带 worker_id 的事件计入；后端未发时整字段为空（hasWorkers=false）。
        if (event.workerId) {
            hasWorkers = true;
            const workerAgg = byWorker[event.workerId] ?? {
                workerId: event.workerId,
                role: event.actor,
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
                calls: 0,
                events: 0,
                lastEpoch: null,
                lastLatencyMs: null,
            };
            workerAgg.events += 1;
            workerAgg.totalTokens += observedTokens;
            workerAgg.promptTokens += event.promptTokens;
            workerAgg.completionTokens += event.completionTokens;
            workerAgg.cachedTokens += event.cachedTokens;
            workerAgg.cacheCreationTokens += event.cacheCreationTokens;
            workerAgg.cacheReadTokens += event.cacheReadTokens;
            workerAgg.toolTokens += event.toolTokens;
            workerAgg.reasoningTokens += event.reasoningTokens;
            workerAgg.audioTokens += event.audioTokens;
            workerAgg.serverToolUseCount += event.serverToolUseCount;
            if (event.isCall || event.hasUsage)
                workerAgg.calls += 1;
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
    // 最近一次装配（context.build）的真实在窗项数（items_count），经 system meta 送达。
    const lastContextBuild = sorted.find((event) => event.contextItems !== null);
    // 最近一次上下文规模（context.build total_tokens 或 llm 通道 context_tokens_after）。
    const lastContextSize = sorted.find((event) => event.finalRequestTokenEstimate !== null || event.contextTokens !== null);
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
        cachedTokens,
        cacheCreationTokens,
        cacheReadTokens,
        toolTokens,
        reasoningTokens,
        audioTokens,
        serverToolUseCount,
        projectionCount: projectionKeys.size,
        receiptCount,
        contextItemsCount: lastContextBuild ? lastContextBuild.contextItems : null,
        contextTokensLatest: lastContextSize
            ? (lastContextSize.finalRequestTokenEstimate ?? lastContextSize.contextTokens)
            : null,
        errorCount,
        avgLatencyMs: latencyCount > 0 ? Math.round(latencySum / latencyCount) : null,
        lastLatencyMs: lastWithLatency ? lastWithLatency.durationMs : null,
        lastEventEpoch: lastEventEpoch && lastEventEpoch > 0 ? lastEventEpoch : null,
        byMode,
        byActor,
        byRole,
        byProviderModel,
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
 * @param executionLogs     运行时事件流（channel=system，emit_event 经总线推送的规范事件）。
 * @param processStreamEvents 进程/系统流（channel=process）。
 */
export function buildTelemetryFromStream(llmStreamEvents, executionLogs, processStreamEvents) {
    const llm = Array.isArray(llmStreamEvents) ? llmStreamEvents : [];
    const execution = Array.isArray(executionLogs) ? executionLogs : [];
    const process = Array.isArray(processStreamEvents) ? processStreamEvents : [];
    const events = [];
    let cursor = 0;
    for (const log of execution) {
        const event = logEntryToEvent(log, cursor++, 'system');
        if (event)
            events.push(event);
    }
    for (const log of llm) {
        const event = logEntryToEvent(log, cursor++, 'llm');
        // 流式 chunk 噪声不计入离散事件集（保持事件类型分布有信号）。
        if (event && event.category !== 'state')
            events.push(event);
        else if (event && event.category === 'state' && (event.name === 'invoke_start'))
            events.push(event);
    }
    for (const log of process) {
        const event = logEntryToEvent(log, cursor++, 'process');
        if (event)
            events.push(event);
    }
    if (events.length === 0)
        return EMPTY_TELEMETRY;
    const windowed = llm.length >= STREAM_CAPS.llm ||
        execution.length >= STREAM_CAPS.execution ||
        process.length >= STREAM_CAPS.process;
    return aggregateEvents(events, windowed);
}
function eventMatchesRole(event, roleId) {
    if (event.roleHints.length > 0)
        return event.roleHints.includes(roleId);
    const aliases = ACTOR_ROLE_ALIASES[roleId] ?? [roleId];
    const lowered = event.actor.toLowerCase();
    if (aliases.some((alias) => lowered.includes(alias)))
        return true;
    return false;
}
/** 汇总某角色在真实遥测里的 token（按 actor 别名匹配，来自 journal `llm` 通道的真实 usage）。 */
export function telemetryRoleTokens(telemetry, roleId) {
    const aggregate = telemetry.byRole[roleId];
    if (aggregate)
        return aggregate.totalTokens;
    return filterEventsForRole(telemetry.events, roleId)
        .reduce((total, event) => total + contextOSObservedTokens(event), 0);
}
/** 汇总某角色在真实遥测里的事件数（按 actor 别名匹配）。 */
export function telemetryRoleEvents(telemetry, roleId) {
    const aggregate = telemetry.byRole[roleId];
    if (aggregate)
        return aggregate.events;
    return filterEventsForRole(telemetry.events, roleId).length;
}
/** 汇总某角色在真实遥测里的离散 LLM 调用次数（按 actor 别名匹配）。 */
export function telemetryRoleCalls(telemetry, roleId) {
    const aggregate = telemetry.byRole[roleId];
    if (aggregate)
        return aggregate.calls;
    return filterEventsForRole(telemetry.events, roleId)
        .filter((event) => event.isCall || event.hasUsage)
        .length;
}
/**
 * 过滤出属于某角色的事件流（按 actor 别名匹配，结果保持原有倒序）。
 *
 * 用于构建每个角色自己的 ContextOS 内部视图：事件、投影、回执、调用等都从该子集再聚合。
 */
export function filterEventsForRole(events, roleId) {
    return events.filter((event) => eventMatchesRole(event, roleId));
}
/**
 * 该角色是否拥有真实的「带 usage 的观测通道」。
 *
 * journal `llm` 通道（emit_llm_event → MessageBus → WS）在 raw.data 携带真实 prompt/completion
 * tokens，因此凡是在实时流里产生过带 usage 调用的角色（如 PM/Director）都有真实 token 归并。
 * 据此该角色才以 token 归因展示；无 usage 的角色仍以事件数/时延诚实呈现，不伪造 per-role token。
 */
export function telemetryRoleHasUsageChannel(telemetry, roleId) {
    return telemetryRoleTokens(telemetry, roleId) > 0;
}
