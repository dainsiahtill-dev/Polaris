/**
 * Runtime stream/event parsers + helpers.
 *
 * Extracted losslessly from useRuntime.ts. Pure functions for parsing the
 * runtime.v2 WebSocket stream: LLM/process/quality-gate stream lines, runtime
 * task-lifecycle merges, resident-status merging, and runtime.v2 envelope
 * normalization. No React or hook state — independently testable.
 */

import * as Parsing from './runtimeParsing';
import type { WebSocketMessage } from './useRuntime';
import type { LogEntry } from '@/types/log';
import { TaskStatus, type PmTask } from '@/types/task';
import type { QualityGateData } from '@/app/components/pm';
import {
  isRuntimeFactoryOrBenchEventChannel,
} from './_runtimeEventFilter';
import type { SnapshotPayload } from '@/app/types/appContracts';
import { getRuntimeProcessStreamKind } from '@/app/utils/appRuntime';

export function toRuntimeEventPayload(payload: WebSocketMessage): Record<string, unknown> | null {
  if (Parsing.isRecord(payload.event)) {
    return payload.event;
  }
  const rawText = typeof payload.line === 'string' ? payload.line : typeof payload.text === 'string' ? payload.text : '';
  if (!rawText.trim()) {
    return null;
  }
  return Parsing.tryParseJsonObject(rawText);
}

export function normalizeRuntimeV2Envelope(eventPayload: Record<string, unknown>): WebSocketMessage {
  const v2Channel = String(eventPayload.channel || eventPayload.category || '').trim().toLowerCase();
  const v2Domain = String(eventPayload.domain || '').trim().toLowerCase();
  const kind = String(eventPayload.kind || '').trim().toLowerCase();
  const envelopePayload = Parsing.isRecord(eventPayload.payload) ? eventPayload.payload : null;
  const rawPayload = Parsing.isRecord(envelopePayload?.raw) ? envelopePayload.raw : null;
  const nestedEvent = Parsing.isRecord(eventPayload.event) ? eventPayload.event : null;
  const eventName = String(eventPayload.event_name || eventPayload.event || eventPayload.name || kind || '')
    .trim()
    .toLowerCase();
  const mergedPayload = {
    ...(envelopePayload || {}),
    ...eventPayload,
    payload: envelopePayload || eventPayload.payload,
  };
  const source = String(eventPayload.source || envelopePayload?.source || '').trim().toLowerCase();

  let targetChannel = v2Channel.startsWith('log.') ? v2Channel.slice(4) : v2Channel;
  if (!targetChannel && v2Domain) {
    if (v2Domain === 'llm') targetChannel = 'llm';
    else if (v2Domain === 'process') targetChannel = 'process';
    else if (v2Domain === 'system') targetChannel = 'system';
  }

  if (eventName === 'settings_changed' || eventName.endsWith('.settings_changed')) {
    return { type: 'settings_changed', payload: envelopePayload || rawPayload || mergedPayload };
  }
  if (targetChannel === 'dialogue' || kind === 'dialogue' || source === 'dialogue') {
    return { type: 'line', channel: 'dialogue', text: JSON.stringify(rawPayload || mergedPayload) };
  }
  if (targetChannel === 'event.file_edit' || targetChannel === 'file_edit' || kind === 'file_edit') {
    return {
      type: 'file_edit',
      event: {
        ...mergedPayload,
        ...(rawPayload || nestedEvent || {}),
        schema_version: eventPayload.schema_version || envelopePayload?.schema_version,
        event_schema: eventPayload.event_schema || envelopePayload?.event_schema,
        channel: eventPayload.channel || eventPayload.category,
        kind: eventPayload.kind || eventPayload.event || eventPayload.name,
        source: eventPayload.source || envelopePayload?.source,
      },
      timestamp: String(eventPayload.timestamp || eventPayload.ts || rawPayload?.timestamp || nestedEvent?.timestamp || ''),
    };
  }
  if (isRuntimeFactoryOrBenchEventChannel(targetChannel)) {
    return { type: 'line', channel: targetChannel, text: JSON.stringify(mergedPayload) };
  }
  if (targetChannel === 'llm' || v2Domain === 'llm' || kind.startsWith('llm.')) {
    return { type: 'line', channel: 'llm', text: JSON.stringify(rawPayload ? { ...mergedPayload, raw: rawPayload } : mergedPayload) };
  }
  if (
    targetChannel === 'process' ||
    v2Domain === 'process' ||
    kind.startsWith('process.') ||
    targetChannel === 'system' ||
    v2Domain === 'system' ||
    kind.startsWith('system.')
  ) {
    return { type: 'line', channel: targetChannel === 'system' ? 'system' : 'process', text: JSON.stringify(mergedPayload) };
  }
  if (targetChannel.startsWith('status.') && targetChannel !== 'status.snapshot') {
    return {
      type: 'status_domain',
      channel: targetChannel,
      payload: mergedPayload,
      timestamp: String(eventPayload.timestamp || eventPayload.ts || envelopePayload?.timestamp || ''),
    };
  }
  return { type: 'line', channel: 'system', text: JSON.stringify(mergedPayload) };
}

export function isRuntimeV2Envelope(payload: WebSocketMessage): boolean {
  const record = payload as unknown as Record<string, unknown>;
  const schemaVersion = String(record.schema_version || '').trim();
  return schemaVersion === 'runtime.v2' || Boolean(record.channel && record.kind && record.payload);
}

export function residentStatusFromDomainPayload(payload: WebSocketMessage): Record<string, unknown> | null {
  const domainPayload = Parsing.isRecord(payload.payload) ? payload.payload : null;
  return firstRecord(
    domainPayload?.resident,
    domainPayload?.projection,
    Parsing.isRecord(domainPayload?.payload) ? domainPayload.payload.resident : null,
  );
}

export function mergeResidentIntoSnapshot(
  currentSnapshot: SnapshotPayload | null,
  resident: Record<string, unknown>,
  timestamp: string,
): SnapshotPayload {
  return {
    ...(currentSnapshot ?? { timestamp: timestamp || new Date().toISOString() }),
    resident: resident as SnapshotPayload['resident'],
  };
}

export type RuntimeTaskLifecycleUpdate = {
  taskId: string;
  title?: string;
  status: TaskStatus;
  workerId?: string;
  timestamp?: string;
};

export function normalizeTaskMatchToken(value: unknown): string {
  return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

export function taskMatchTokens(task: PmTask): string[] {
  const metadata = task.metadata && typeof task.metadata === 'object' ? task.metadata : {};
  const record = task as PmTask & Record<string, unknown>;
  const values = [
    task.id,
    record.task_id,
    record.taskId,
    record.pm_task_id,
    metadata.task_id,
    metadata.taskId,
    metadata.pm_task_id,
    metadata.id,
  ];
  const seen = new Set<string>();
  return values
    .map(normalizeTaskMatchToken)
    .filter((token) => {
      if (!token || seen.has(token)) return false;
      seen.add(token);
      return true;
    });
}

export function runtimeTaskLifecyclePayload(raw: Record<string, unknown>): RuntimeTaskLifecycleUpdate | null {
  const payload = firstRecord(raw.payload);
  const data = firstRecord(raw.data, raw.output, payload?.data, payload?.raw, payload);
  const meta = firstRecord(raw.meta, payload?.meta, data?.metadata, data?.meta);
  const eventText = [
    raw.event,
    raw.name,
    raw.kind,
    raw.type,
    payload?.event,
    payload?.name,
    payload?.kind,
    data?.event,
    data?.name,
    data?.kind,
    data?.stream_event,
    data?.event_type,
  ].map((value) => String(value || '').trim().toLowerCase()).filter(Boolean).join(' ');
  const domainText = [
    raw.stream,
    raw.source,
    raw.channel,
    raw.domain,
    raw.actor,
    raw.role,
    payload?.stream,
    payload?.source,
    payload?.channel,
    payload?.domain,
    payload?.actor,
    payload?.role,
    data?.stream,
    data?.source,
    data?.actor,
    data?.role,
  ].map((value) => String(value || '').trim().toLowerCase()).filter(Boolean).join(' ');
  const hasLifecycleStateToken =
    eventText.includes('started') ||
    eventText.includes('running') ||
    eventText.includes('claimed') ||
    eventText.includes('in_progress') ||
    eventText.includes('completed') ||
    eventText.includes('success') ||
    eventText.includes('failed') ||
    eventText.includes('error') ||
    eventText.includes('blocked');

  const isTaskLifecycle =
    eventText.includes('director_task') ||
    eventText.includes('task_started') ||
    eventText.includes('task_completed') ||
    eventText.includes('task_failed') ||
    eventText.includes('task_blocked') ||
    (domainText.includes('director') && eventText.includes('task')) ||
    ((domainText.includes('task_runtime') || domainText.includes('runtime.task')) && hasLifecycleStateToken);
  if (!isTaskLifecycle) return null;

  const taskId =
    Parsing.toStringValue(data?.task_id) ||
    Parsing.toStringValue(data?.taskId) ||
    Parsing.toStringValue(data?.pm_task_id) ||
    Parsing.toStringValue(meta?.task_id) ||
    Parsing.toStringValue(meta?.taskId) ||
    Parsing.toStringValue(meta?.pm_task_id) ||
    Parsing.toStringValue(raw.task_id) ||
    Parsing.toStringValue(raw.taskId);
  if (!taskId) return null;

  let status: TaskStatus | null = null;
  if (eventText.includes('failed') || eventText.includes('error')) status = TaskStatus.FAILED;
  else if (eventText.includes('blocked')) status = TaskStatus.BLOCKED;
  else if (eventText.includes('completed') || eventText.includes('success')) status = TaskStatus.COMPLETED;
  else if (
    eventText.includes('started') ||
    eventText.includes('running') ||
    eventText.includes('claimed') ||
    eventText.includes('in_progress')
  ) {
    status = TaskStatus.IN_PROGRESS;
  }
  if (!status) return null;

  return {
    taskId,
    status,
    title:
      Parsing.toStringValue(data?.task_title) ||
      Parsing.toStringValue(data?.title) ||
      Parsing.toStringValue(data?.subject) ||
      Parsing.toStringValue(meta?.task_title) ||
      Parsing.toStringValue(meta?.title) ||
      undefined,
    workerId:
      Parsing.toStringValue(data?.worker_id) ||
      Parsing.toStringValue(data?.claimed_by) ||
      Parsing.toStringValue(meta?.worker_id) ||
      Parsing.toStringValue(meta?.claimed_by) ||
      undefined,
    timestamp:
      Parsing.toStringValue(raw.ts) ||
      Parsing.toStringValue(raw.timestamp) ||
      Parsing.toStringValue(data?.timestamp) ||
      undefined,
  };
}

export function mergeRuntimeTaskLifecycle(tasks: PmTask[], update: RuntimeTaskLifecycleUpdate): PmTask[] {
  const updateToken = normalizeTaskMatchToken(update.taskId);
  if (!updateToken) return tasks;
  const terminalDone = update.status === TaskStatus.COMPLETED || update.status === TaskStatus.SUCCESS;
  let matched = false;
  const nextTasks = tasks.map((task) => {
    if (!taskMatchTokens(task).includes(updateToken)) return task;
    matched = true;
    return {
      ...task,
      title: task.title || update.title || update.taskId,
      subject: task.subject || update.title,
      status: update.status,
      state: update.status,
      done: terminalDone,
      completed: terminalDone,
      started_at: update.status === TaskStatus.IN_PROGRESS ? update.timestamp || task.started_at : task.started_at,
      completed_at: terminalDone ? update.timestamp || task.completed_at : task.completed_at,
      worker_id: update.workerId || task.worker_id,
      assigned_worker: update.workerId || task.assigned_worker,
      metadata: {
        ...(task.metadata || {}),
        runtime_lifecycle_source: 'runtime_v2',
        runtime_lifecycle_status: update.status,
      },
    };
  });
  if (matched) return nextTasks;
  return [
    ...nextTasks,
    {
      id: update.taskId,
      title: update.title || update.taskId,
      subject: update.title,
      status: update.status,
      state: update.status,
      done: terminalDone,
      completed: terminalDone,
      priority: 3,
      acceptance: [],
      started_at: update.status === TaskStatus.IN_PROGRESS ? update.timestamp : undefined,
      completed_at: terminalDone ? update.timestamp : undefined,
      worker_id: update.workerId,
      assigned_worker: update.workerId,
      metadata: {
        runtime_lifecycle_source: 'runtime_v2',
        runtime_lifecycle_status: update.status,
      },
    },
  ];
}

export function normalizeChannelToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

export function isLlmStreamChannel(channel: string): boolean {
  const token = normalizeChannelToken(channel);
  return token === 'llm' || token === 'log.llm' || token === 'llm_stream';
}

export function isLlmPayloadCompatible(parsed: Record<string, unknown>): boolean {
  const canonicalChannel = normalizeChannelToken(parsed.channel || parsed.category || parsed.stream);
  if (!canonicalChannel) return true;
  if (isLlmStreamChannel(canonicalChannel)) return true;

  const domain = normalizeChannelToken(parsed.domain);
  if (domain === 'llm') return true;

  const kind = normalizeChannelToken(parsed.kind || parsed.event || parsed.name || parsed.type);
  return kind === 'llm_stream' || kind.startsWith('llm.');
}

export function extractLlmRunScope(parsed: Record<string, unknown>): string {
  const raw = Parsing.isRecord(parsed.raw) ? parsed.raw : null;
  const data = Parsing.isRecord(parsed.data) ? parsed.data : null;

  const candidates: unknown[] = [
    parsed.run_id,
    parsed.runId,
    parsed.workflow_run_id,
    parsed.workflowRunId,
    raw?.run_id,
    raw?.runId,
    raw?.workflow_run_id,
    raw?.workflowRunId,
    data?.run_id,
    data?.runId,
    data?.workflow_run_id,
    data?.workflowRunId,
  ];

  for (const candidate of candidates) {
    const token = Parsing.toStringValue(candidate);
    if (token) return token;
  }
  return '';
}

export function resolveLlmLogRunScope(log: LogEntry): string {
  const meta = Parsing.isRecord(log.meta) ? log.meta : null;
  const runScope = Parsing.toStringValue(meta?.runId || meta?.run_id || meta?.workflowRunId || meta?.workflow_run_id);
  return runScope;
}

export function buildLlmDedupKey(log: LogEntry, fallbackRunScope: string): string {
  const scopedRunId = resolveLlmLogRunScope(log) || fallbackRunScope || 'global';
  return `${scopedRunId}:${log.id}`;
}

export function withLlmRunScope(log: LogEntry, fallbackRunScope: string): LogEntry {
  const scopedRunId = resolveLlmLogRunScope(log) || fallbackRunScope || 'global';
  if (!scopedRunId || scopedRunId === 'global') return log;
  const meta = Parsing.isRecord(log.meta) ? log.meta : {};
  if (Parsing.toStringValue(meta.runId || meta.run_id || meta.workflowRunId || meta.workflow_run_id)) {
    return log;
  }
  return {
    ...log,
    meta: {
      ...meta,
      runId: scopedRunId,
    },
  };
}

export function normalizeStreamEventToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

export function streamEventLabel(eventType: string): string {
  switch (eventType) {
    case 'thinking_chunk':
      return '思考流';
    case 'thinking_preview':
      return '思考预览';
    case 'content_chunk':
      return '输出流';
    case 'content_preview':
      return '输出预览';
    case 'tool_call':
      return '工具调用';
    case 'tool_result':
      return '工具结果';
    case 'error':
      return '错误';
    default:
      return '';
  }
}

export function isChunkEvent(eventType: string): boolean {
  return (
    eventType === 'thinking_chunk'
    || eventType === 'content_chunk'
    || eventType === 'thinking_preview'
    || eventType === 'content_preview'
  );
}

export function buildLlmMergeKey(log: LogEntry): string {
  const meta = Parsing.isRecord(log.meta) ? log.meta : null;
  const streamEvent = normalizeStreamEventToken(meta?.streamEvent);
  const role = Parsing.toStringValue(meta?.role || '');
  const channel = Parsing.toStringValue(meta?.channel || '');
  return `${streamEvent}|${role}|${channel}|${log.source}`;
}

export function appendLlmStreamEntries(prev: LogEntry[], incoming: LogEntry[], limit: number): LogEntry[] {
  if (!incoming.length) return prev;
  const next = [...prev];
  for (const entry of incoming) {
    const meta = Parsing.isRecord(entry.meta) ? entry.meta : null;
    const streamEvent = normalizeStreamEventToken(meta?.streamEvent);
    const canMerge = isChunkEvent(streamEvent) && next.length > 0;
    if (canMerge) {
      const last = next[next.length - 1];
      const lastMeta = Parsing.isRecord(last.meta) ? last.meta : null;
      const lastEvent = normalizeStreamEventToken(lastMeta?.streamEvent);
      if (lastEvent === streamEvent && buildLlmMergeKey(last) === buildLlmMergeKey(entry)) {
        const mergedMessage = `${last.message || ''}${entry.message || ''}`.slice(-2400);
        const mergedDetailsRaw = `${last.details || ''}${entry.details || ''}`.slice(-1200);
        const merged: LogEntry = {
          ...entry,
          id: last.id,
          message: mergedMessage,
          details: mergedDetailsRaw || undefined,
        };
        next[next.length - 1] = merged;
        continue;
      }
    }
    next.push(entry);
  }
  return next.slice(-limit);
}

export function normalizeTaggedStreamLine(raw: string): Record<string, unknown> | null {
  const match = raw.match(/^(?:\[[^\]]+\]\s*>\s*)?\[(thinking_chunk|content_chunk|tool_call|tool_result)\]\s*(\{.*\})\s*$/i);
  if (!match) return null;

  const streamEvent = match[1].toLowerCase();
  const parsedPayload = Parsing.tryParseJsonObject(match[2]);
  if (!parsedPayload) return null;

  const rawPayload = Parsing.isRecord(parsedPayload.raw) ? parsedPayload.raw : {};
  return {
    ...parsedPayload,
    event: streamEvent,
    raw: {
      ...rawPayload,
      stream_event: String(rawPayload.stream_event || streamEvent),
      event_type: String(rawPayload.event_type || streamEvent),
      content: parsedPayload.content ?? rawPayload.content,
      payload: Parsing.isRecord(rawPayload.payload) ? rawPayload.payload : parsedPayload,
    },
  };
}

export function firstRecord(...candidates: unknown[]): Record<string, unknown> | null {
  for (const candidate of candidates) {
    if (Parsing.isRecord(candidate)) {
      return candidate;
    }
  }
  return null;
}

export function positiveNumber(...candidates: unknown[]): number {
  for (const candidate of candidates) {
    const value = typeof candidate === 'number'
      ? candidate
      : typeof candidate === 'string' && candidate.trim()
        ? Number(candidate)
        : NaN;
    if (Number.isFinite(value) && value > 0) return value;
  }
  return 0;
}

export function extractToolPayload(
  rawObj: Record<string, unknown> | null,
  eventData: Record<string, unknown> | null,
  parsed: Record<string, unknown>,
): Record<string, unknown> | null {
  return firstRecord(
    rawObj?.payload,
    eventData?.payload,
    rawObj,
    eventData,
    parsed.tool || parsed.tool_name ? parsed : null,
  );
}

export function readToolName(toolPayload: Record<string, unknown> | null): string {
  return Parsing.firstDisplayString(toolPayload?.tool, toolPayload?.tool_name, toolPayload?.name);
}

export function readToolArgs(toolPayload: Record<string, unknown> | null): string {
  const args = Parsing.firstDisplayString(toolPayload?.args, toolPayload?.arguments, toolPayload?.input);
  return args ? args.slice(0, 180) : '';
}

export function readToolResult(toolPayload: Record<string, unknown> | null): Record<string, unknown> | null {
  return firstRecord(toolPayload?.result, toolPayload?.output);
}

export function readLlmContentText(
  rawObj: Record<string, unknown> | null,
  eventData: Record<string, unknown> | null,
  parsed: Record<string, unknown>,
): string {
  const metadata = firstRecord(eventData?.metadata, rawObj?.metadata, parsed.metadata);
  const extraFields = firstRecord(metadata?.extra_fields, metadata?.extraFields);
  const candidates = [
    rawObj?.response_content,
    eventData?.response_content,
    metadata?.response_content,
    extraFields?.response_content,
    rawObj?.content,
    rawObj?.preview,
    parsed.content,
    parsed.preview,
    parsed.response_content,
    eventData?.content,
    eventData?.preview,
    eventData?.summary,
    metadata?.content,
    metadata?.preview,
    metadata?.summary,
    extraFields?.content,
    extraFields?.preview,
    extraFields?.summary,
    rawObj?.message,
    parsed.message,
    eventData?.message,
    metadata?.message,
    extraFields?.message,
  ];
  for (const candidate of candidates) {
    const token = Parsing.toDisplayString(candidate);
    if (token) return token;
  }
  return '';
}

const CONTEXT_SNAPSHOT_REF_RE = /^[0-9a-f]{24}$/i;

export function normalizeContextSnapshotRef(value: string): string {
  const token = String(value || '').trim();
  return CONTEXT_SNAPSHOT_REF_RE.test(token) ? token.toLowerCase() : '';
}

export function parseLlmStreamLine(channel: string, line: string): LogEntry | null {
  const raw = String(line || '').trim();
  if (!raw) return null;

  const parsed = normalizeTaggedStreamLine(raw) ?? Parsing.tryParseJsonObject(raw);
  let message = raw;
  let timestamp = new Date().toISOString();
  let source = 'LLM';
  let level: LogEntry['level'] = 'thinking';
  let details = '';

  if (parsed) {
    if (isLlmStreamChannel(channel) && !isLlmPayloadCompatible(parsed)) return null;

    const ts = String(parsed.ts || parsed.timestamp || '').trim();
    if (ts) timestamp = ts;

    const actor = Parsing.firstDisplayString(parsed.actor, parsed.role, parsed.source);
    if (actor) source = Parsing.normalizeActorLabel(actor);

    const thinking = Parsing.firstDisplayString(parsed.thinking, parsed.content, parsed.message);
    const eventName =
      Parsing.toStringValue(parsed.event) ||
      Parsing.toStringValue(parsed.name) ||
      Parsing.toStringValue(parsed.kind);
    const eventToken = eventName.toLowerCase();
    let modelName = Parsing.firstDisplayString(parsed.model, parsed.model_name);
    const rawObj = Parsing.isRecord(parsed.raw) ? parsed.raw : null;
    const streamEvent = rawObj
      ? Parsing.firstDisplayString(rawObj.stream_event, rawObj.event_type).toLowerCase()
      : '';
    const rawEvent = rawObj ? Parsing.firstDisplayString(rawObj.event, rawObj.name) : '';
    const rawSummary = rawObj ? Parsing.firstDisplayString(rawObj.summary, rawObj.message) : '';
    const rawContent = rawObj ? Parsing.firstDisplayString(rawObj.content) : '';
    // 规范 LLM 事件（journal `llm` 通道，CanonicalLogEventV2）把数据放在 raw.data；
    // 旧版 *.llm.events.jsonl 放在顶层 data。两者都兼容（顶层优先）。
    const eventData = parsed.data && typeof parsed.data === 'object'
      ? (parsed.data as Record<string, unknown>)
      : (rawObj && rawObj.data && typeof rawObj.data === 'object'
          ? (rawObj.data as Record<string, unknown>)
          : null);
    const parsedRefs = firstRecord(parsed.refs, rawObj?.refs);
    if (!modelName && eventData) {
      modelName = Parsing.firstDisplayString(eventData.model, eventData.model_name);
    }
    const dataSummary = eventData ? Parsing.firstDisplayString(eventData.summary, eventData.message) : '';
    const dataPreview = eventData ? Parsing.firstDisplayString(eventData.preview) : '';
    const dataBackend = eventData ? Parsing.firstDisplayString(eventData.backend) : '';
    const dataDuration = eventData ? Parsing.firstDisplayString(eventData.duration_ms) : '';
    const dataError = eventData ? Parsing.firstDisplayString(eventData.error) : '';
    const dataTaskCount = eventData ? Parsing.firstDisplayString(eventData.task_count) : '';
    const dataOutputChars = eventData ? Number(eventData.output_chars || 0) : 0;
    const dataStage = eventData ? String(eventData.stage || '').trim().toLowerCase() : '';

    // 真实 per-call 用量与时延：journal `llm` 通道在 raw.data 携带 prompt/completion tokens、
    // context_tokens_after，以及 raw.data.metadata.elapsed_ms（真实时延）。这些是实时遥测的核心信号。
    const dataMetadata = firstRecord(eventData?.metadata, rawObj?.metadata, parsed.metadata);
    const dataUsage = firstRecord(eventData?.usage, dataMetadata?.usage, rawObj?.usage, parsed.usage);
    const dataProviderId = Parsing.firstDisplayString(
      eventData?.provider_id,
      eventData?.providerId,
      dataMetadata?.provider_id,
      dataMetadata?.providerId,
      parsed.provider_id,
      parsed.providerId,
    );
    const dataProviderName = Parsing.firstDisplayString(
      eventData?.provider_name,
      eventData?.providerName,
      eventData?.provider,
      dataMetadata?.provider_name,
      dataMetadata?.providerName,
      dataMetadata?.provider,
      parsed.provider_name,
      parsed.providerName,
      parsed.provider,
    );
    const dataPromptTokens = positiveNumber(
      eventData?.prompt_tokens,
      eventData?.promptTokens,
      eventData?.input_tokens,
      eventData?.inputTokens,
      dataUsage?.prompt_tokens,
      dataUsage?.promptTokens,
      dataUsage?.input_tokens,
      dataUsage?.inputTokens,
      dataMetadata?.prompt_tokens,
      dataMetadata?.promptTokens,
      dataMetadata?.input_tokens,
      dataMetadata?.inputTokens,
    );
    const dataCompletionTokens = positiveNumber(
      eventData?.completion_tokens,
      eventData?.completionTokens,
      eventData?.output_tokens,
      eventData?.outputTokens,
      dataUsage?.completion_tokens,
      dataUsage?.completionTokens,
      dataUsage?.output_tokens,
      dataUsage?.outputTokens,
      dataMetadata?.completion_tokens,
      dataMetadata?.completionTokens,
      dataMetadata?.output_tokens,
      dataMetadata?.outputTokens,
    );
    const dataCacheCreationTokens = positiveNumber(
      eventData?.cache_creation_input_tokens,
      eventData?.cacheCreationInputTokens,
      dataUsage?.cache_creation_input_tokens,
      dataUsage?.cacheCreationInputTokens,
      dataMetadata?.cache_creation_input_tokens,
      dataMetadata?.cacheCreationInputTokens,
    );
    const dataCacheReadTokens = positiveNumber(
      eventData?.cache_read_input_tokens,
      eventData?.cacheReadInputTokens,
      dataUsage?.cache_read_input_tokens,
      dataUsage?.cacheReadInputTokens,
      dataMetadata?.cache_read_input_tokens,
      dataMetadata?.cacheReadInputTokens,
    );
    const dataCachedTokens = positiveNumber(
      eventData?.cached_tokens,
      eventData?.cachedTokens,
      eventData?.cached_prompt_tokens,
      dataUsage?.cached_tokens,
      dataUsage?.cachedTokens,
      dataUsage?.cached_prompt_tokens,
      dataMetadata?.cached_tokens,
      dataMetadata?.cachedTokens,
      dataCacheReadTokens,
    );
    const dataReasoningTokens = positiveNumber(
      eventData?.reasoning_tokens,
      eventData?.reasoningTokens,
      dataUsage?.reasoning_tokens,
      dataUsage?.reasoningTokens,
      dataUsage?.output_tokens_details && typeof dataUsage.output_tokens_details === 'object'
        ? (dataUsage.output_tokens_details as Record<string, unknown>).reasoning_tokens
        : undefined,
      dataUsage?.completion_tokens_details && typeof dataUsage.completion_tokens_details === 'object'
        ? (dataUsage.completion_tokens_details as Record<string, unknown>).reasoning_tokens
        : undefined,
    );
    const dataAudioTokens = positiveNumber(
      eventData?.audio_tokens,
      eventData?.audioTokens,
      dataUsage?.audio_tokens,
      dataUsage?.audioTokens,
    );
    const dataToolTokens = positiveNumber(
      eventData?.tool_tokens,
      eventData?.toolTokens,
      dataUsage?.tool_tokens,
      dataUsage?.toolTokens,
    );
    const dataTotalTokens = positiveNumber(
      eventData?.total_tokens,
      eventData?.totalTokens,
      dataUsage?.total_tokens,
      dataUsage?.totalTokens,
      dataMetadata?.total_tokens,
      dataMetadata?.totalTokens,
    );
    const dataContextTokens = positiveNumber(
      eventData?.context_tokens_after,
      eventData?.contextTokens,
      eventData?.context_tokens_before,
      dataMetadata?.context_tokens_after,
      dataMetadata?.contextTokens,
      dataMetadata?.context_tokens_before,
    );
    const dataContextSnapshotRef = normalizeContextSnapshotRef(Parsing.firstDisplayString(
      eventData?.context_snapshot_ref,
      eventData?.contextSnapshotRef,
      dataMetadata?.context_snapshot_ref,
      dataMetadata?.contextSnapshotRef,
      parsedRefs?.context_snapshot_ref,
      parsedRefs?.contextSnapshotRef,
    ));
    const dataContextSnapshotDegraded = firstRecord(
      eventData?.context_snapshot_degraded,
      eventData?.contextSnapshotDegraded,
      dataMetadata?.context_snapshot_degraded,
      dataMetadata?.contextSnapshotDegraded,
    );
    const dataContextSnapshotDegradedReason = Parsing.firstDisplayString(
      eventData?.context_snapshot_degraded_reason,
      eventData?.contextSnapshotDegradedReason,
      dataMetadata?.context_snapshot_degraded_reason,
      dataMetadata?.contextSnapshotDegradedReason,
    );
    const dataPromptHash = Parsing.firstDisplayString(
      eventData?.prompt_hash,
      eventData?.promptHash,
      dataMetadata?.prompt_hash,
      dataMetadata?.promptHash,
      parsedRefs?.prompt_hash,
      parsedRefs?.promptHash,
    );
    const dataTurnId = Parsing.firstDisplayString(
      eventData?.turn_id,
      eventData?.turnId,
      dataMetadata?.turn_id,
      dataMetadata?.turnId,
      parsedRefs?.turn_id,
      parsedRefs?.turnId,
    );
    const dataCallId = Parsing.firstDisplayString(
      eventData?.call_id,
      eventData?.callId,
      dataMetadata?.call_id,
      dataMetadata?.callId,
      parsedRefs?.call_id,
      parsedRefs?.callId,
    );
    const dataFinalRequestContextAudit = firstRecord(
      eventData?.final_request_context_audit,
      eventData?.finalRequestContextAudit,
      dataMetadata?.final_request_context_audit,
      dataMetadata?.finalRequestContextAudit,
      parsed.final_request_context_audit,
      parsed.finalRequestContextAudit,
    );
    const dataContextOSAudit = firstRecord(
      eventData?.context_os_audit,
      eventData?.contextOSAudit,
      dataMetadata?.context_os_audit,
      dataMetadata?.contextOSAudit,
      parsed.context_os_audit,
      parsed.contextOSAudit,
    );
    const dataElapsedMs = dataMetadata ? Number(dataMetadata.elapsed_ms ?? 0) : 0;
    const dataDurationMs = dataDuration && Number.isFinite(Number(dataDuration)) && Number(dataDuration) > 0
      ? Number(dataDuration)
      : (Number.isFinite(dataElapsedMs) && dataElapsedMs > 0 ? dataElapsedMs : 0);
    const safePromptTokens = Number.isFinite(dataPromptTokens) && dataPromptTokens > 0
      ? dataPromptTokens + dataCacheCreationTokens + dataCacheReadTokens
      : 0;
    const safeCompletionTokens = Number.isFinite(dataCompletionTokens) && dataCompletionTokens > 0 ? dataCompletionTokens : 0;
    const safeContextTokens = Number.isFinite(dataContextTokens) && dataContextTokens > 0 ? dataContextTokens : 0;
    const usageTotalTokens = dataTotalTokens > 0 ? dataTotalTokens : safePromptTokens + safeCompletionTokens;

    const normalizedEvent = streamEvent || eventToken;
    const toolPayload = extractToolPayload(rawObj, eventData, parsed);

    if (normalizedEvent === 'thinking_chunk' || normalizedEvent === 'thinking_preview') {
      message = readLlmContentText(rawObj, eventData, parsed) || rawContent || thinking || 'LLM thinking';
      level = 'thinking';
      details = modelName ? `model=${modelName}` : '';
    } else if (normalizedEvent === 'content_chunk' || normalizedEvent === 'content_preview') {
      message = readLlmContentText(rawObj, eventData, parsed) || rawContent || thinking || 'LLM output';
      level = 'info';
      details = modelName ? `model=${modelName}` : '';
    } else if (normalizedEvent === 'tool_call') {
      const toolName = readToolName(toolPayload);
      const toolArgs = readToolArgs(toolPayload);
      message = toolName ? `调用工具: ${toolName}` : '调用工具';
      details = toolArgs ? `args=${toolArgs}` : '';
      level = 'tool';
    } else if (normalizedEvent === 'tool_result') {
      const toolName = readToolName(toolPayload);
      const rawSuccess = toolPayload ? toolPayload.success : undefined;
      const status = rawSuccess === undefined ? 'done' : (rawSuccess ? 'ok' : 'failed');
      message = toolName ? `工具结果: ${toolName} (${status})` : `工具结果 (${status})`;
      const resultObj = readToolResult(toolPayload);
      details = resultObj ? Parsing.firstDisplayString(resultObj.error, resultObj.message) : '';
      level = status === 'failed' ? 'error' : 'tool';
    } else if (normalizedEvent === 'llm_waiting' || normalizedEvent === 'call_start' || eventToken === 'llm_call_start') {
      // 规范 LLM 生命周期（journal `llm` 通道）：等待响应 = 一次调用的开始。
      message = `正在请求 ${modelName || dataBackend || 'LLM'} 响应…`;
      details = modelName ? `model=${modelName}` : '';
      level = 'thinking';
    } else if (
      normalizedEvent === 'llm_completed' ||
      normalizedEvent === 'call_end' ||
      normalizedEvent === 'complete' ||
      normalizedEvent === 'response.completed' ||
      normalizedEvent === 'response.done' ||
      normalizedEvent === 'message_stop' ||
      eventToken === 'llm_call_end'
    ) {
      // 规范 LLM 生命周期：调用成功完成，携带真实 prompt/completion tokens 与时延。
      const baseMsg = Parsing.firstDisplayString(parsed.message);
      const responseContent = readLlmContentText(rawObj, eventData, parsed);
      message = responseContent || dataSummary || baseMsg || (safeCompletionTokens > 0 ? 'LLM 响应已返回' : 'LLM 响应已完成');
      const detailTokens = [
        modelName ? `model=${modelName}` : '',
        safePromptTokens > 0 ? `prompt=${safePromptTokens}` : '',
        safeCompletionTokens > 0 ? `completion=${safeCompletionTokens}` : '',
        dataDurationMs > 0 ? `${Math.round(dataDurationMs)}ms` : '',
      ].filter((token) => token.length > 0);
      details = detailTokens.join(' ');
      level = 'success';
    } else if (normalizedEvent === 'llm_failed' || normalizedEvent === 'response.failed' || eventToken === 'llm_call_error') {
      // 规范 LLM 生命周期：调用失败。
      const errMsg = (eventData ? Parsing.firstDisplayString(eventData.error_message, eventData.error) : '') || dataError;
      message = errMsg ? `LLM 调用失败: ${errMsg}` : 'LLM 调用失败';
      details = [
        modelName ? `model=${modelName}` : '',
        dataDurationMs > 0 ? `${Math.round(dataDurationMs)}ms` : '',
      ].filter((token) => token.length > 0).join(' ');
      level = 'error';
    } else if (eventToken === 'invoke_start') {
      message = `正在请求 ${dataBackend || 'LLM'}...`;
      details = dataBackend ? `backend=${dataBackend}` : '';
      level = 'thinking';
    } else if (eventToken === 'invoke_done') {
      message = dataSummary || dataPreview || (dataOutputChars <= 0 ? 'LLM 返回空响应' : 'LLM 响应已返回');
      const detailTokens = [
        dataBackend ? `backend=${dataBackend}` : '',
        `chars=${Number.isFinite(dataOutputChars) ? dataOutputChars : 0}`,
        dataTaskCount ? `tasks=${dataTaskCount}` : '',
        dataDuration ? `${dataDuration}ms` : '',
      ].filter((token) => token.length > 0);
      details = detailTokens.join(' ');
      level = 'success';
    } else if (eventToken === 'invoke_error') {
      message = dataError ? `LLM 调用失败: ${dataError}` : 'LLM 调用失败';
      details = dataBackend ? `backend=${dataBackend}` : '';
      level = 'error';
    } else if (eventToken === 'iteration') {
      if (dataStage === 'started') {
        message = '开始新一轮规划';
        level = 'info';
      } else if (dataStage === 'completed') {
        message = '本轮规划完成';
        level = 'info';
      } else if (dataStage === 'failed') {
        message = '本轮规划失败';
        level = 'error';
      } else {
        message = dataSummary || dataPreview || '规划阶段更新';
        level = 'info';
      }
      details = dataBackend ? `backend=${dataBackend}` : '';
    } else if (eventToken === 'task_generated') {
      message = dataSummary || `生成任务: ${dataTaskCount || '1'} 个`;
      details = dataTaskCount ? `共 ${dataTaskCount} 个任务` : '';
      level = 'success';
    } else if (eventToken === 'task_contract_validated') {
      message = '任务合同校验通过';
      details = dataTaskCount ? `${dataTaskCount} 个任务` : '';
      level = 'success';
    } else if (eventToken === 'director_started') {
      message = 'Director 工作流已启动';
      details = dataTaskCount ? `${dataTaskCount} 个任务待执行` : '';
      level = 'info';
    } else if (eventToken === 'director_completed') {
      message = dataSummary || 'Director 工作流已完成';
      level = 'success';
    } else if (eventToken === 'director_task_started') {
      message = dataSummary || '开始执行任务';
      const taskId = eventData ? Parsing.firstDisplayString(eventData.task_id) : '';
      details = taskId ? `task=${taskId}` : (dataTaskCount ? `tasks=${dataTaskCount}` : '');
      level = 'info';
    } else if (eventToken === 'director_task_completed') {
      message = dataSummary || '任务已完成';
      const taskId = eventData ? String(eventData.task_id || '').trim() : '';
      details = taskId ? `task=${taskId}` : '';
      level = 'success';
    } else if (eventToken === 'director_task_failed') {
      message = dataSummary || '任务执行失败';
      level = 'error';
    } else if (eventToken === 'qa_started') {
      message = 'QA 验证已启动';
      level = 'info';
    } else if (eventToken === 'qa_completed') {
      message = dataSummary || 'QA 验证完成';
      level = 'success';
    } else if (eventToken === 'config') {
      message = dataSummary || dataPreview || 'LLM 配置已加载';
      level = 'info';
    } else {
      message = thinking || rawSummary || dataSummary || dataPreview || eventName || rawEvent || raw;
      details = modelName ? `model=${modelName}` : '';
      level = Parsing.mapSeverityToLevel(Parsing.firstDisplayString(parsed.severity), 'thinking');
    }

    const eventLabel = streamEventLabel(normalizedEvent);
    const tags = [normalizedEvent].filter((token) => token.length > 0);
    const runScope = extractLlmRunScope(parsed);
    const meta: Record<string, unknown> = {
      channel,
      streamEvent: normalizedEvent || undefined,
      role: actor || undefined,
      providerId: dataProviderId || undefined,
      providerName: dataProviderName || undefined,
      model: modelName || undefined,
      runId: runScope || undefined,
      // 真实 per-call 用量 / 上下文规模 / 时延（来自 journal raw.data）——供 ContextOS 实时遥测消费。
      usage: dataUsage || undefined,
      promptTokens: safePromptTokens > 0 ? safePromptTokens : undefined,
      completionTokens: safeCompletionTokens > 0 ? safeCompletionTokens : undefined,
      totalTokens: usageTotalTokens > 0 ? usageTotalTokens : undefined,
      cachedTokens: dataCachedTokens > 0 ? dataCachedTokens : undefined,
      cacheCreationInputTokens: dataCacheCreationTokens > 0 ? dataCacheCreationTokens : undefined,
      cacheReadInputTokens: dataCacheReadTokens > 0 ? dataCacheReadTokens : undefined,
      reasoningTokens: dataReasoningTokens > 0 ? dataReasoningTokens : undefined,
      audioTokens: dataAudioTokens > 0 ? dataAudioTokens : undefined,
      toolTokens: dataToolTokens > 0 ? dataToolTokens : undefined,
      contextTokens: safeContextTokens > 0 ? safeContextTokens : undefined,
      durationMs: dataDurationMs > 0 ? Math.round(dataDurationMs) : undefined,
      // NEW fields for context viewer
      contextSnapshotRef: dataContextSnapshotRef || undefined,
      contextSnapshotDegraded: dataContextSnapshotDegraded || undefined,
      contextSnapshotDegradedReason: dataContextSnapshotDegradedReason || undefined,
      promptHash: dataPromptHash || undefined,
      turnId: dataTurnId || undefined,
      callId: dataCallId || undefined,
      finalRequestContextAudit: dataFinalRequestContextAudit || undefined,
      contextOSAudit: dataContextOSAudit || undefined,
    };

    const compact = message.replace(/\s+/g, ' ').trim();
    if (!compact) return null;

    return {
      id: Parsing.buildStableLogId(channel, raw, parsed),
      timestamp,
      level,
      source,
      title: eventLabel || undefined,
      message: compact.slice(0, 220),
      details: details || undefined,
      meta,
      tags: tags.length > 0 ? tags : undefined,
    };
  }

  const compact = message.replace(/\s+/g, ' ').trim();
  if (!compact) return null;

  return {
    id: Parsing.buildStableLogId(channel, raw, parsed),
    timestamp,
    level,
    source,
    message: compact.slice(0, 220),
    details: details || undefined,
  };
}

export function parseProcessStreamLine(channel: string, line: string): LogEntry | null {
  const raw = Parsing.stripAnsi(String(line || '').trim());
  if (!raw) return null;

  const baseSource = (
    channel === 'system'
      ? 'System'
      : channel === 'process'
        ? 'Process'
        : isRuntimeFactoryOrBenchEventChannel(channel)
          ? 'Factory'
          : 'Process'
  );

  let timestamp = new Date().toISOString();
  let source = baseSource;
  let message = raw;
  let details = '';
  let level: LogEntry['level'] = 'info';
  let streamEvent = '';

  const parsed = Parsing.tryParseJsonObject(raw);
  if (parsed) {
    const canonicalChannel = Parsing.toStringValue(parsed.channel);
    if (channel === 'process' && canonicalChannel && canonicalChannel !== 'process') return null;
    if (channel === 'system' && canonicalChannel && canonicalChannel !== 'system') return null;

    const parsedTs = Parsing.firstDisplayString(parsed.ts, parsed.timestamp, parsed.time);
    if (parsedTs) timestamp = parsedTs;

    const parsedRole = Parsing.firstDisplayString(parsed.role, parsed.actor, parsed.source);
    if (parsedRole) source = Parsing.normalizeActorLabel(parsedRole);

    const eventName =
      Parsing.toStringValue(parsed.event) ||
      Parsing.toStringValue(parsed.name) ||
      Parsing.toStringValue(parsed.kind) ||
      Parsing.toStringValue(parsed.type);
    const eventToken = eventName.toLowerCase();
    const summary = Parsing.firstDisplayString(parsed.summary, parsed.message, parsed.text);
    const dataObj = parsed.data && typeof parsed.data === 'object' ? (parsed.data as Record<string, unknown>) : null;
    const rawObj = parsed.raw && typeof parsed.raw === 'object' ? (parsed.raw as Record<string, unknown>) : null;
    const resultObj = parsed.result && typeof parsed.result === 'object'
      ? (parsed.result as Record<string, unknown>)
      : dataObj?.result && typeof dataObj.result === 'object'
        ? (dataObj.result as Record<string, unknown>)
        : rawObj?.result && typeof rawObj.result === 'object'
          ? (rawObj.result as Record<string, unknown>)
          : null;
    const dataMsg = dataObj ? Parsing.firstDisplayString(dataObj.message, dataObj.summary) : '';
    const rawMsg = rawObj ? Parsing.firstDisplayString(rawObj.message, rawObj.summary) : '';
    streamEvent = (
      Parsing.toStringValue(dataObj?.stream_event) ||
      Parsing.toStringValue(dataObj?.event_type) ||
      Parsing.toStringValue(rawObj?.stream_event) ||
      Parsing.toStringValue(rawObj?.event_type) ||
      eventToken
    ).toLowerCase();
    const toolName = dataObj
      ? Parsing.firstDisplayString(dataObj.tool, dataObj.tool_name, rawObj?.tool, rawObj?.tool_name)
      : Parsing.firstDisplayString(rawObj?.tool, rawObj?.tool_name);
    const command = dataObj
      ? Parsing.firstDisplayString(dataObj.command, rawObj?.command)
      : Parsing.firstDisplayString(rawObj?.command);

    if (streamEvent === 'tool_call' || streamEvent === 'tool_result' || toolName) {
      const rawSuccess = dataObj ? dataObj.success : rawObj?.success;
      const status = rawSuccess === undefined ? 'done' : (rawSuccess ? 'ok' : 'failed');
      message = streamEvent === 'tool_result'
        ? (toolName ? `工具结果: ${toolName} (${status})` : `工具结果 (${status})`)
        : (toolName ? `调用工具: ${toolName}` : '调用工具');
      const args = Parsing.firstDisplayString(dataObj?.args, rawObj?.args, dataObj?.arguments, rawObj?.arguments);
      details = [
        args ? `args=${args.slice(0, 180)}` : '',
        command ? `cmd=${command}` : '',
      ].filter((item) => item.length > 0).join(' ');
      level = status === 'failed' ? 'error' : 'tool';
    } else {
      message = summary || dataMsg || rawMsg || eventName || raw;
      details = [toolName ? `tool=${toolName}` : '', command ? `cmd=${command}` : '']
        .filter((item) => item.length > 0)
        .join(' ');

      level = Parsing.mapSeverityToLevel(Parsing.firstDisplayString(parsed.severity), level);
      if (level === 'info') {
        // Formal Factory events carry an authoritative StageResult. Consume it
        // before text heuristics: successful summaries intentionally include
        // `error_code=none`, which used to match `/error/` and made PM/CE
        // stage_completed events appear under ContextOS "异常闭环".
        const structuredStatus = Parsing.firstDisplayString(
          resultObj?.status,
          parsed.status,
          dataObj?.status,
          rawObj?.status,
        ).toLowerCase();
        const structuredFailure =
          parsed.ok === false ||
          dataObj?.ok === false ||
          rawObj?.ok === false ||
          ['failed', 'error', 'cancelled', 'blocked'].includes(structuredStatus);
        const structuredSuccess =
          parsed.ok === true ||
          dataObj?.ok === true ||
          rawObj?.ok === true ||
          ['success', 'completed', 'passed'].includes(structuredStatus);
        const token = `${eventName} ${summary} ${dataMsg} ${rawMsg}`.toLowerCase();
        if (structuredFailure) level = 'error';
        else if (structuredSuccess) level = 'success';
        else if (/error|failed|exception|traceback|timeout/.test(token)) level = 'error';
        else if (/warn|retry|blocked/.test(token)) level = 'warning';
        else if (/tool|invoke|llm|thinking|prompt/.test(token)) level = 'thinking';
        else if (/success|completed|done|passed/.test(token)) level = 'success';
      }
    }
  } else {
    const text = raw.toLowerCase();
    if (/error|failed|exception|traceback|timeout/.test(text)) level = 'error';
    else if (/warn|retry|blocked/.test(text)) level = 'warning';
    else if (/tool|invoke|llm|thinking|prompt/.test(text)) level = 'thinking';
    else if (/success|completed|done|passed/.test(text)) level = 'success';
  }

  const normalized = message.replace(/\s+/g, ' ').trim();
  if (!normalized) return null;

  return {
    id: Parsing.buildStableLogId(channel, raw, parsed),
    timestamp,
    level,
    source,
    message: normalized.slice(0, 260),
    details: details || undefined,
    meta: {
      channel,
      streamKind: getRuntimeProcessStreamKind(channel) || 'execution',
      streamEvent: streamEvent || undefined,
    },
  };
}

export function parseRuntimeEvent(raw: Record<string, unknown>): LogEntry | null {
  const eventId = Parsing.firstDisplayString(raw.event_id, raw.seq) || String(Date.now());
  const ts = Parsing.firstDisplayString(raw.ts, raw.timestamp) || new Date().toISOString();
  const payload = firstRecord(raw.payload);
  const payloadRaw = firstRecord(payload?.raw);
  const rawBody = firstRecord(raw.raw, payloadRaw);
  const data = firstRecord(
    raw.data,
    raw.output,
    rawBody?.data,
    payload?.data,
    rawBody,
    payload,
  ) ?? {};
  const metadata = firstRecord(data.metadata, rawBody?.metadata, payload?.metadata, raw.metadata);
  const meta = metadata ? { ...metadata, ...data } : data;
  const actor = Parsing.firstDisplayString(raw.actor, raw.role, data.actor, data.role, rawBody?.actor, rawBody?.role) || 'System';
  const eventName =
    Parsing.toStringValue(raw.name) ||
    Parsing.toStringValue(raw.event) ||
    Parsing.toStringValue(data.event_type) ||
    Parsing.toStringValue(data.stream_event) ||
    Parsing.toStringValue(rawBody?.event_type) ||
    Parsing.toStringValue(rawBody?.stream_event) ||
    Parsing.toStringValue(raw.kind) ||
    'unknown';

  let level: LogEntry['level'] = 'info';
  if (raw.error || raw.ok === false) level = 'error';
  else if (eventName.includes('retry')) level = 'warning';
  else if (eventName.includes('thinking') || eventName.includes('llm')) level = 'thinking';
  else if (raw.ok === true) level = 'success';

  let message = eventName;
  let details = '';

  switch (eventName) {
    case 'pm_quality_gate_retry':
      message = '质量检查未通过，正在重试生成';
      details = Parsing.firstDisplayString(data.quality_summary);
      break;
    case 'pm_quality_gate':
      message = data.passed ? '质量检查通过' : '质量检查未通过';
      details = `分数: ${Parsing.firstDisplayString(data.score, data.quality_summary) || 'N/A'}`;
      break;
    case 'llm_invoke':
      message = `LLM 调用完成 (${Parsing.firstDisplayString(data.model) || 'unknown'})`;
      details = `${Parsing.firstDisplayString(Parsing.isRecord(data.usage) ? data.usage.total_tokens : undefined) || '?'} tokens`;
      break;
    case 'iteration':
      message = `开始第 ${Parsing.firstDisplayString(data.iteration) || '?'} 轮迭代`;
      break;
    case 'config':
      message = '配置加载完成';
      break;
    default:
      message = Parsing.firstDisplayString(raw.summary, raw.message, raw.text) || eventName;
  }

  return {
    id: eventId,
    timestamp: ts,
    level,
    source: actor,
    message,
    details,
    meta,
  };
}

export function parseQualityGateEvent(raw: Record<string, unknown>): QualityGateData | null {
  const data = Parsing.isRecord(raw.data)
    ? raw.data
    : Parsing.isRecord(raw.output)
      ? raw.output
      : {};

  const qualitySummary = Parsing.firstDisplayString(data.quality_summary);
  const scoreMatch = qualitySummary.match(/\d+/);
  const score = parseInt(Parsing.firstDisplayString(scoreMatch?.[0], data.score) || '0', 10);
  const attempt = parseInt(Parsing.firstDisplayString(data.attempt) || '1', 10);
  const maxAttempts = parseInt(Parsing.firstDisplayString(data.max_attempts) || '3', 10);

  const issues: QualityGateData['issues'] = [];
  const criticalIssues = Array.isArray(data.critical_issues) ? data.critical_issues : [];
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];

  criticalIssues.forEach((msg) => {
    const message = Parsing.toDisplayString(msg);
    if (message) issues.push({ type: 'critical', message });
  });
  warnings.forEach((msg) => {
    const message = Parsing.toDisplayString(msg);
    if (message) issues.push({ type: 'warning', message });
  });

  return {
    score,
    passed: score >= 80 && issues.filter(i => i.type === 'critical').length === 0,
    attempt,
    maxAttempts,
    summary: Parsing.firstDisplayString(data.quality_summary),
    issues,
    metrics: {
      critical: criticalIssues.length,
      warnings: warnings.length,
      score,
    },
  };
}
