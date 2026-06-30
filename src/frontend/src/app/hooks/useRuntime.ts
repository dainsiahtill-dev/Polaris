/**
 * useRuntime Hook - Facade for Runtime State (Refactored)
 *
 * This hook provides a unified interface for consuming runtime state.
 * It delegates to the Zustand store and specialized hooks.
 *
 * Architecture (Refactored):
 * - useRuntimeStore: 单一状态源 (Zustand + Immer)
 * - useRuntimeConnection: 连接状态管理
 * - useRoleStatus: PM/Director 状态
 * - useRuntimeLogs: 日志流管理
 * - useTaskProgress/useTaskTrace: 任务追踪
 *
 * IMPORTANT: This hook must be used within a RuntimeTransportProvider.
 * The provider is set up in App.tsx.
 */

import { useCallback, useEffect, useRef, useMemo } from 'react';
import { useRuntimeStore } from './useRuntimeStore';
import { devLogger } from '@/app/utils/devLogger';
import { useRuntimeConnection } from './useRuntimeConnection';
import { useRoleStatus } from './useRoleStatus';
import { useRuntimeLogs } from './useRuntimeLogs';
import {
  getRuntimeProcessStreamKind,
  isProcessStreamChannel,
  normalizeDialogueEvent,
} from '@/app/utils/appRuntime';
import { useSettings } from '@/hooks';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type {
  BackendStatus,
  EngineStatus,
  LlmStatus,
  LanceDbStatus,
  AnthroState,
  SnapshotPayload,
} from '@/app/types/appContracts';
import type { QualityGateData } from '@/app/components/pm';
import type { LogEntry } from '@/types/log';
import { TaskStatus, type PmTask } from '@/types/task';
import type { TaskTraceEvent } from '../types/taskTrace';
import * as Parsing from './runtimeParsing';
import type { RuntimeWorkerState, SequentialTraceEvent, FileEditEvent } from './useRuntimeStore';
import type { RuntimeProjectionPayload } from '@/runtime/projection';
import {
  selectTaskRows,
  selectPrimaryStatus,
  isSystemActive,
} from '@/runtime/projection';
import { normalizeRuntimeProjection } from '@/runtime/projectionAdapter';

export interface WebSocketMessage {
  type: string;
  action?: string;
  channel?: string;
  pm_status?: BackendStatus | null;
  director_status?: BackendStatus | null;
  engine_status?: EngineStatus | null;
  llm_status?: LlmStatus | null;
  snapshot?: SnapshotPayload | null;
  lancedb?: LanceDbStatus | null;
  anthro_state?: AnthroState | null;
  lines?: string[];
  line?: string;
  text?: string;
  trigger?: string;
  timestamp?: string;
  event?: Record<string, unknown> | null;
  events?: unknown[];
  payload?: Record<string, unknown> | null;
}

export interface UseRuntimeOptions {
  channels?: string[];
  tailLines?: number;
  roles?: ('pm' | 'chief_engineer' | 'director' | 'qa')[];
  baseUrl?: string;
  autoConnect?: boolean;
  maxRetries?: number;
  baseDelay?: number;
  workspace?: string;
  includeInternalBench?: boolean;
}

export interface UseRuntimeResult {
  live: boolean;
  connected: boolean;
  isConnected: boolean;
  error: string | null;
  reconnecting: boolean;
  attemptCount: number;
  pmStatus: BackendStatus | null;
  directorStatus: BackendStatus | null;
  engineStatus: EngineStatus | null;
  llmStatus: LlmStatus | null;
  lancedbStatus: LanceDbStatus | null;
  snapshot: SnapshotPayload | null;
  anthroState: AnthroState | null;
  dialogueEvents: DialogueEvent[];
  setDialogueEvents: (events: DialogueEvent[]) => void;
  qualityGate: QualityGateData | null;
  executionLogs: LogEntry[];
  llmStreamEvents: LogEntry[];
  processStreamEvents: LogEntry[];
  currentPhase: string;
  fileEditEvents: FileEditEvent[];
  tasks: PmTask[];
  workers: RuntimeWorkerState[];
  runId: string | null;
  taskProgressMap: Map<string, {
    phase?: string;
    phaseIndex?: number;
    phaseTotal?: number;
    retryCount?: number;
    maxRetries?: number;
    currentFile?: string;
  }>;
  taskTraceMap: Map<string, TaskTraceEvent[]>;
  sequentialTraceMap: Map<string, SequentialTraceEvent[]>;
  connect: () => void;
  disconnect: () => void;
  reconnect: () => void;
  refresh: () => void;
  updateSubscription: (roles: ('pm' | 'chief_engineer' | 'director' | 'qa')[]) => void;
}

const DEFAULT_RUNTIME_ROLES: Array<'pm' | 'chief_engineer' | 'director' | 'qa'> = [
  'pm',
  'chief_engineer',
  'director',
  'qa',
];
const FACTORY_EVENT_CHANNEL = 'event.factory';
const BENCH_EVENT_CHANNEL = 'event.bench';

function isInternalBenchEventChannel(channel: string): boolean {
  return (
    channel === BENCH_EVENT_CHANNEL ||
    channel.startsWith(`${BENCH_EVENT_CHANNEL}:`)
  );
}

function isRuntimeFactoryOrBenchEventChannel(channel: string): boolean {
  return (
    channel === FACTORY_EVENT_CHANNEL ||
    channel.startsWith(`${FACTORY_EVENT_CHANNEL}:`) ||
    isInternalBenchEventChannel(channel)
  );
}

const RUNTIME_WORKSPACE_FIELD_NAMES = new Set([
  'workspace',
  'workspace_path',
  'workspacePath',
  'project_workspace',
  'projectWorkspace',
  'project_root',
  'projectRoot',
  'polaris_workspace',
  'polarisWorkspace',
  'runtime_workspace',
  'runtimeWorkspace',
]);

function normalizeRuntimeWorkspacePath(value: unknown): string {
  const token = Parsing.toStringValue(value).trim();
  if (!token) return '';
  return token.replace(/\\/g, '/').replace(/\/+$/g, '');
}

function collectRuntimeWorkspacePaths(
  value: unknown,
  paths: Set<string>,
  seen: WeakSet<object>,
  depth = 0,
): void {
  if (depth > 5 || !Parsing.isRecord(value)) return;
  if (seen.has(value)) return;
  seen.add(value);

  for (const [key, nested] of Object.entries(value)) {
    if (RUNTIME_WORKSPACE_FIELD_NAMES.has(key)) {
      const normalized = normalizeRuntimeWorkspacePath(nested);
      if (normalized) paths.add(normalized);
    }

    if (Parsing.isRecord(nested)) {
      collectRuntimeWorkspacePaths(nested, paths, seen, depth + 1);
    } else if (Array.isArray(nested)) {
      for (const item of nested.slice(0, 12)) {
        collectRuntimeWorkspacePaths(item, paths, seen, depth + 1);
      }
    }
  }
}

function runtimeRecordMatchesWorkspace(raw: unknown, activeWorkspace: string): boolean {
  const active = normalizeRuntimeWorkspacePath(activeWorkspace);
  if (!active || !Parsing.isRecord(raw)) return true;

  const candidates = new Set<string>();
  collectRuntimeWorkspacePaths(raw, candidates, new WeakSet<object>());
  if (candidates.size === 0) return true;
  return candidates.has(active);
}

function runtimeLineMatchesWorkspace(line: string, activeWorkspace: string): boolean {
  const parsed = Parsing.tryParseJsonObject(line);
  return parsed ? runtimeRecordMatchesWorkspace(parsed, activeWorkspace) : true;
}

function isSettingsChangedEvent(raw: Record<string, unknown>): boolean {
  const eventName = String(raw.event_name || raw.event || raw.name || raw.kind || '').trim().toLowerCase();
  return eventName === 'settings_changed' || eventName.endsWith('.settings_changed');
}

// ============================================================================
// Pure Parsing Functions (moved from useRuntime.ts)
// ============================================================================

function toRuntimeEventPayload(payload: WebSocketMessage): Record<string, unknown> | null {
  if (Parsing.isRecord(payload.event)) {
    return payload.event;
  }
  const rawText = typeof payload.line === 'string' ? payload.line : typeof payload.text === 'string' ? payload.text : '';
  if (!rawText.trim()) {
    return null;
  }
  return Parsing.tryParseJsonObject(rawText);
}

function normalizeRuntimeV2Envelope(eventPayload: Record<string, unknown>): WebSocketMessage {
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
  if (targetChannel === 'runtime_events' || kind === 'runtime_event') {
    return { type: 'line', channel: 'runtime_events', text: JSON.stringify(mergedPayload) };
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
    return { type: 'line', channel: 'llm', text: JSON.stringify(mergedPayload) };
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
  return { type: 'line', channel: 'runtime_events', text: JSON.stringify(mergedPayload) };
}

function isRuntimeV2Envelope(payload: WebSocketMessage): boolean {
  const record = payload as unknown as Record<string, unknown>;
  const schemaVersion = String(record.schema_version || '').trim();
  return schemaVersion === 'runtime.v2' || Boolean(record.channel && record.kind && record.payload);
}

function residentStatusFromDomainPayload(payload: WebSocketMessage): Record<string, unknown> | null {
  const domainPayload = Parsing.isRecord(payload.payload) ? payload.payload : null;
  return firstRecord(
    domainPayload?.resident,
    domainPayload?.projection,
    Parsing.isRecord(domainPayload?.payload) ? domainPayload.payload.resident : null,
  );
}

function mergeResidentIntoSnapshot(
  currentSnapshot: SnapshotPayload | null,
  resident: Record<string, unknown>,
  timestamp: string,
): SnapshotPayload {
  return {
    ...(currentSnapshot ?? { timestamp: timestamp || new Date().toISOString() }),
    resident: resident as SnapshotPayload['resident'],
  };
}

type RuntimeTaskLifecycleUpdate = {
  taskId: string;
  title?: string;
  status: TaskStatus;
  workerId?: string;
  timestamp?: string;
};

function normalizeTaskMatchToken(value: unknown): string {
  return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

function taskMatchTokens(task: PmTask): string[] {
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

function runtimeTaskLifecyclePayload(raw: Record<string, unknown>): RuntimeTaskLifecycleUpdate | null {
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

function mergeRuntimeTaskLifecycle(tasks: PmTask[], update: RuntimeTaskLifecycleUpdate): PmTask[] {
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
        runtime_lifecycle_source: 'runtime_events',
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
        runtime_lifecycle_source: 'runtime_events',
        runtime_lifecycle_status: update.status,
      },
    },
  ];
}

function normalizeChannelToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function isLlmStreamChannel(channel: string): boolean {
  const token = normalizeChannelToken(channel);
  return token === 'llm' || token === 'log.llm' || token === 'llm_stream';
}

function isLlmPayloadCompatible(parsed: Record<string, unknown>): boolean {
  const canonicalChannel = normalizeChannelToken(parsed.channel || parsed.category || parsed.stream);
  if (!canonicalChannel) return true;
  if (isLlmStreamChannel(canonicalChannel)) return true;

  const domain = normalizeChannelToken(parsed.domain);
  if (domain === 'llm') return true;

  const kind = normalizeChannelToken(parsed.kind || parsed.event || parsed.name || parsed.type);
  return kind === 'llm_stream' || kind.startsWith('llm.');
}

function extractLlmRunScope(parsed: Record<string, unknown>): string {
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

function resolveLlmLogRunScope(log: LogEntry): string {
  const meta = Parsing.isRecord(log.meta) ? log.meta : null;
  const runScope = Parsing.toStringValue(meta?.runId || meta?.run_id || meta?.workflowRunId || meta?.workflow_run_id);
  return runScope;
}

function buildLlmDedupKey(log: LogEntry, fallbackRunScope: string): string {
  const scopedRunId = resolveLlmLogRunScope(log) || fallbackRunScope || 'global';
  return `${scopedRunId}:${log.id}`;
}

function withLlmRunScope(log: LogEntry, fallbackRunScope: string): LogEntry {
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

function normalizeStreamEventToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function streamEventLabel(eventType: string): string {
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

function isChunkEvent(eventType: string): boolean {
  return (
    eventType === 'thinking_chunk'
    || eventType === 'content_chunk'
    || eventType === 'thinking_preview'
    || eventType === 'content_preview'
  );
}

function buildLlmMergeKey(log: LogEntry): string {
  const meta = Parsing.isRecord(log.meta) ? log.meta : null;
  const streamEvent = normalizeStreamEventToken(meta?.streamEvent);
  const role = Parsing.toStringValue(meta?.role || '');
  const channel = Parsing.toStringValue(meta?.channel || '');
  return `${streamEvent}|${role}|${channel}|${log.source}`;
}

function appendLlmStreamEntries(prev: LogEntry[], incoming: LogEntry[], limit: number): LogEntry[] {
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

function normalizeTaggedStreamLine(raw: string): Record<string, unknown> | null {
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

function firstRecord(...candidates: unknown[]): Record<string, unknown> | null {
  for (const candidate of candidates) {
    if (Parsing.isRecord(candidate)) {
      return candidate;
    }
  }
  return null;
}

function positiveNumber(...candidates: unknown[]): number {
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

function extractToolPayload(
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

function readToolName(toolPayload: Record<string, unknown> | null): string {
  return Parsing.firstDisplayString(toolPayload?.tool, toolPayload?.tool_name, toolPayload?.name);
}

function readToolArgs(toolPayload: Record<string, unknown> | null): string {
  const args = Parsing.firstDisplayString(toolPayload?.args, toolPayload?.arguments, toolPayload?.input);
  return args ? args.slice(0, 180) : '';
}

function readToolResult(toolPayload: Record<string, unknown> | null): Record<string, unknown> | null {
  return firstRecord(toolPayload?.result, toolPayload?.output);
}

function readLlmContentText(
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

function normalizeContextSnapshotRef(value: string): string {
  const token = String(value || '').trim();
  return CONTEXT_SNAPSHOT_REF_RE.test(token) ? token.toLowerCase() : '';
}

function parseLlmStreamLine(channel: string, line: string): LogEntry | null {
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

function parseProcessStreamLine(channel: string, line: string): LogEntry | null {
  const raw = Parsing.stripAnsi(String(line || '').trim());
  if (!raw) return null;

  const baseSource = (
    channel === 'system'
      ? 'System'
      : channel === 'process'
        ? 'Process'
        : channel === 'pm_subprocess'
        ? 'PM'
        : channel === 'director_console'
          ? 'Director'
          : channel === 'pm_report'
          ? 'PM-Report'
          : channel === 'pm_log'
            ? 'PM-Events'
            : channel === 'ollama'
              ? 'Ollama'
              : channel === 'qa'
                ? 'QA'
                : channel === 'runlog'
                  ? 'RunLog'
                    : channel === 'engine_status'
                    ? 'Engine'
                    : isRuntimeFactoryOrBenchEventChannel(channel)
                      ? 'Factory'
                      : 'Planner'
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
        const token = `${eventName} ${summary} ${dataMsg} ${rawMsg}`.toLowerCase();
        if (/error|failed|exception|traceback|timeout/.test(token)) level = 'error';
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

function parseRuntimeEvent(raw: Record<string, unknown>): LogEntry | null {
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

function parseQualityGateEvent(raw: Record<string, unknown>): QualityGateData | null {
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

// ============================================================================
// Main Hook
// ============================================================================

export function useRuntime(options: UseRuntimeOptions = {}): UseRuntimeResult {
  const {
    roles = DEFAULT_RUNTIME_ROLES,
    baseUrl,
    autoConnect = true,
    maxRetries = Infinity,
    baseDelay = 1000,
    workspace: workspaceProp,
    includeInternalBench = false,
    tailLines,
  } = options;

  // Settings
  const { settings, load: loadRuntimeSettings } = useSettings({ autoLoad: workspaceProp === undefined });
  const workspace = workspaceProp ?? settings?.workspace ?? '';
  const isWorkspaceControlled = workspaceProp !== undefined;

  // Store state
  const store = useRuntimeStore();
  const {
    pmStatus,
    directorStatus,
    engineStatus,
    llmStatus,
    lancedbStatus,
    snapshot,
    anthroState,
    dialogueEvents,
    qualityGate,
    executionLogs,
    llmStreamEvents,
    processStreamEvents,
    currentPhase,
    fileEditEvents,
    tasks,
    workers,
    runId,
    taskProgressMap,
    taskTraceMap,
    sequentialTraceMap,
    setTasks,
    setQualityGate,
    setCurrentPhase,
    setRunId,
    setWorkers,
    setEngineStatus,
    setLlmStatus,
    setLancedbStatus,
    setSnapshot,
    setAnthroState,
    appendDialogueEvent,
    setDialogueEvents,
    appendExecutionLog,
    setExecutionLogs,
    appendLlmStreamEvent,
    setLlmStreamEvents,
    appendProcessStreamEvent,
    setProcessStreamEvents,
    updateTaskProgress,
    appendTaskTrace,
    appendSequentialTrace,
    appendFileEditEvent,
    resetForWorkspace,
  } = store;

  // Connection
  const connection = useRuntimeConnection({
    roles,
    autoConnect,
    workspace: workspaceProp,
    includeInternalBench,
    tailLines,
  });

  // Refs for message processing
  const seenDialogueIdsRef = useRef<Set<string>>(new Set());
  const seenLlmEventIdsRef = useRef<Set<string>>(new Set());
  const seenV2EventIdsRef = useRef<Set<string>>(new Set());
  const llmRunScopeRef = useRef<string>('global');
  const directorRunningRef = useRef(false);

  // Process message handler
  const processMessage = useCallback(
    (message: unknown) => {
      try {
        const eventData = message instanceof MessageEvent ? message.data : message;
        let payload: WebSocketMessage = typeof eventData === 'string' ? JSON.parse(eventData) : (eventData as WebSocketMessage);
        const msgType = String(payload.type || '').trim().toLowerCase();
        let channel = String(payload.channel || '').trim();

        if (msgType === 'event' && payload.action === 'query_result' && Array.isArray(payload.events)) {
          payload.events.forEach((eventItem) => {
            if (Parsing.isRecord(eventItem)) {
              processMessage({ type: 'event', event: eventItem });
            }
          });
          return;
        }

        // Handle v2 protocol EVENT message
        if (msgType === 'event' && payload.event) {
          const eventPayload = payload.event as Record<string, unknown>;
          if (!isSettingsChangedEvent(eventPayload) && !runtimeRecordMatchesWorkspace(eventPayload, workspace)) {
            return;
          }
          const eventId = String(eventPayload.event_id || eventPayload.id || '');

          if (eventId && seenV2EventIdsRef.current.has(eventId)) {
            return;
          }
          if (eventId) {
            seenV2EventIdsRef.current.add(eventId);
            if (seenV2EventIdsRef.current.size > 10000) {
              const entries = Array.from(seenV2EventIdsRef.current);
              seenV2EventIdsRef.current = new Set(entries.slice(-5000));
            }
          }

          payload = normalizeRuntimeV2Envelope(eventPayload);
          channel = String(payload.channel || '').trim();
        } else if (isRuntimeV2Envelope(payload)) {
          const rawPayload = payload as unknown as Record<string, unknown>;
          if (!isSettingsChangedEvent(rawPayload) && !runtimeRecordMatchesWorkspace(rawPayload, workspace)) {
            return;
          }
          payload = normalizeRuntimeV2Envelope(payload as unknown as Record<string, unknown>);
          channel = String(payload.channel || '').trim();
        }

        const finalMsgType = String(payload.type || '').trim().toLowerCase();
        if (!includeInternalBench && isInternalBenchEventChannel(channel)) {
          return;
        }
        if (finalMsgType === 'ping') {
          connection.sendCommand({ type: 'PONG' });
          return;
        }

        if (finalMsgType === 'status_domain' && channel === 'status.resident') {
          const resident = residentStatusFromDomainPayload(payload);
          if (resident) {
            const currentSnapshot = useRuntimeStore.getState().snapshot;
            setSnapshot(mergeResidentIntoSnapshot(currentSnapshot, resident, payload.timestamp || ''));
          }
          return;
        }

        const mergeTaskLifecycleFromRaw = (raw: Record<string, unknown>) => {
          const lifecycleUpdate = runtimeTaskLifecyclePayload(raw);
          if (lifecycleUpdate) {
            setTasks(mergeRuntimeTaskLifecycle(useRuntimeStore.getState().tasks, lifecycleUpdate));
          }
        };

        // Handle settings changed event
        if (finalMsgType === 'settings_changed') {
          const eventPayload = Parsing.isRecord(payload.payload) ? payload.payload : Parsing.isRecord(payload.event) ? payload.event : null;
          if (eventPayload) {
            const newWorkspace = Parsing.toStringValue(eventPayload.workspace);
            if (!isWorkspaceControlled && newWorkspace && newWorkspace !== connection.workspaceRef.current) {
              void loadRuntimeSettings();
            }
          }
          return;
        }

        if (finalMsgType === 'error') {
          const errorPayload = Parsing.isRecord(payload.payload) ? payload.payload : null;
          const errorMessage = Parsing.toStringValue(errorPayload?.error) || 'Runtime websocket error';
          useRuntimeStore.getState().setConnectionState({ error: errorMessage });
          return;
        }

        if (finalMsgType === 'file_edit') {
          const eventPayload = Parsing.isRecord(payload.event) ? payload.event : null;
          const fileEditEvent = eventPayload
            ? Parsing.extractFileEditEvents({ event: eventPayload, timestamp: payload.timestamp })
              || Parsing.extractRuntimeFileEditEvent(eventPayload)
            : null;
          if (fileEditEvent) {
            appendFileEditEvent(fileEditEvent);
          }
          return;
        }

        if (msgType === 'TASK_PROGRESS' || msgType === 'task_progress') {
          const eventPayload = Parsing.isRecord(payload.payload) ? payload.payload : Parsing.isRecord(payload.event) ? payload.event : null;
          if (eventPayload) {
            const taskId = Parsing.toStringValue(eventPayload.task_id) || Parsing.toStringValue(eventPayload.taskId);
            if (taskId) {
              updateTaskProgress(taskId, {
                phase: Parsing.toStringValue(eventPayload.phase) || undefined,
                phaseIndex: Parsing.toNumberValue(eventPayload.phase_index) ?? Parsing.toNumberValue(eventPayload.phaseIndex),
                phaseTotal: Parsing.toNumberValue(eventPayload.phase_total) ?? Parsing.toNumberValue(eventPayload.phaseTotal),
                retryCount: Parsing.toNumberValue(eventPayload.retry_count) ?? Parsing.toNumberValue(eventPayload.retryCount),
                maxRetries: Parsing.toNumberValue(eventPayload.max_retries) ?? Parsing.toNumberValue(eventPayload.maxRetries),
                currentFile: Parsing.toStringValue(eventPayload.current_file) || Parsing.toStringValue(eventPayload.currentFile) || undefined,
              });
            }
          }
          return;
        }

        if (msgType === 'task_trace') {
          const traceEvent = payload.event as TaskTraceEvent | undefined;
          if (traceEvent?.task_id) {
            appendTaskTrace(traceEvent);
          }
          return;
        }

        if (msgType === 'sequential' || msgType.startsWith('seq.')) {
          const rawEvent = payload.event as Record<string, unknown> | undefined;
          if (!rawEvent || typeof rawEvent !== 'object') {
            return;
          }
          const seqEvent: SequentialTraceEvent = {
            eventType: String(rawEvent.event_type ?? rawEvent.eventType ?? msgType),
            runId: String(rawEvent.run_id ?? rawEvent.runId ?? ''),
            role: String(rawEvent.role ?? ''),
            taskId: String(rawEvent.task_id ?? rawEvent.taskId ?? ''),
            stepIndex: Number(rawEvent.step_index ?? rawEvent.stepIndex ?? 0),
            timestamp: String(rawEvent.timestamp ?? payload.timestamp ?? ''),
            payload: (rawEvent.payload as Record<string, unknown>) ?? {},
          };
          if (seqEvent.runId) {
            appendSequentialTrace(seqEvent.runId, seqEvent);
          }
          return;
        }

        if (msgType === 'dialogue_event') {
          payload = { type: 'line', channel: 'dialogue', text: Parsing.isRecord(payload.event) ? JSON.stringify(payload.event) : '' };
          channel = 'dialogue';
        } else if (msgType === 'runtime_event') {
          payload = { type: 'line', channel: 'runtime_events', text: Parsing.isRecord(payload.event) ? JSON.stringify(payload.event) : (typeof payload.line === 'string' ? payload.line : '') };
          channel = 'runtime_events';
        } else if (msgType === 'llm_stream' || msgType === 'process_stream') {
          const eventText = Parsing.isRecord(payload.event) ? JSON.stringify(payload.event) : '';
          const lineText = typeof payload.line === 'string' ? payload.line : '';
          const fallbackChannel = msgType === 'llm_stream' ? 'llm' : 'process';
          payload = { type: 'line', channel: channel || fallbackChannel, text: eventText || lineText };
          channel = String(payload.channel || '').trim();
        }

        if (payload.type === 'status') {
          // Update role statuses
          if ('pm_status' in payload) useRuntimeStore.getState().setPmStatus(payload.pm_status ?? null);
          if ('director_status' in payload) useRuntimeStore.getState().setDirectorStatus(payload.director_status ?? null);
          if ('engine_status' in payload) setEngineStatus(payload.engine_status ?? null);
          if ('llm_status' in payload) setLlmStatus(payload.llm_status ?? null);
          if ('snapshot' in payload) setSnapshot(payload.snapshot ?? null);
          if ('lancedb' in payload) setLancedbStatus(payload.lancedb ?? null);
          if ('anthro_state' in payload) setAnthroState(payload.anthro_state ?? null);

          const projection = normalizeRuntimeProjection(payload);
          const directorState = Parsing.parseDirectorStateToken(payload.director_status ?? null);
          directorRunningRef.current = directorState.running;

          const primaryStatus = selectPrimaryStatus(projection);
          const systemActive = isSystemActive(projection);
          const rawPhase = systemActive ? primaryStatus.replace(/-/g, '_') : 'idle';
          const nextPhase = Parsing.normalizePhaseToken(rawPhase) || 'idle';
          setCurrentPhase(nextPhase);

          const canonicalTasks = selectTaskRows(projection);
          setTasks(canonicalTasks.map(t => ({
            ...(t as unknown as Record<string, unknown>),
            id: t.id,
            title: t.title,
            status: t.status.toUpperCase() as TaskStatus,
            goal: t.title,
            priority: (t.priority === 'high' ? 1 : t.priority === 'medium' ? 3 : t.priority === 'low' ? 5 : 3) as PmTask['priority'],
            assignee: t.assignee,
            done: t.status.toUpperCase() === 'COMPLETED' || t.status.toUpperCase() === 'SUCCESS',
            acceptance: Array.isArray(t.acceptance) ? t.acceptance as PmTask['acceptance'] : [],
          })));

          setWorkers(Parsing.extractDirectorWorkers(payload.director_status ?? null) as RuntimeWorkerState[]);
          const nextRunId = Parsing.extractRunId({
            snapshot: payload.snapshot,
            engine_status: payload.engine_status,
            director_status: payload.director_status,
          });
          const nextRunScope = Parsing.toStringValue(nextRunId);
          if (nextRunScope && nextRunScope !== llmRunScopeRef.current) {
            llmRunScopeRef.current = nextRunScope;
            seenLlmEventIdsRef.current.clear();
          } else if (!nextRunScope && !systemActive && llmRunScopeRef.current !== 'global') {
            // Run finished and runtime turned idle: reset scope to avoid stale cross-run dedup.
            llmRunScopeRef.current = 'global';
            seenLlmEventIdsRef.current.clear();
          }
          setRunId(nextRunId);
          return;
        }

        if (payload.type === 'snapshot' && Array.isArray(payload.lines)) {
          if (channel === 'dialogue') {
            const nextEvents: DialogueEvent[] = [];
            const newIds = new Set<string>();

            payload.lines.forEach((line: string) => {
              if (!line.trim()) return;
              try {
                const raw = JSON.parse(line);
                if (!runtimeRecordMatchesWorkspace(raw, workspace)) return;
                const normalized = normalizeDialogueEvent(raw);
                if (normalized) {
                  const eventId = String((raw as { event_id?: string }).event_id || '');
                  if (eventId) newIds.add(eventId);
                  nextEvents.push(normalized);
                }
              } catch (err) {
                devLogger.warn('[useRuntime] Dialogue parse error:', err);
              }
            });

            seenDialogueIdsRef.current = newIds;
            setDialogueEvents(nextEvents.slice(-500));
          } else if (channel === 'runtime_events') {
            const logs: LogEntry[] = [];
            payload.lines.forEach((line: string) => {
              if (!line.trim()) return;
              try {
                const raw = JSON.parse(line);
                if (!runtimeRecordMatchesWorkspace(raw, workspace)) return;
                if (Parsing.isRecord(raw)) mergeTaskLifecycleFromRaw(raw);
                const log = parseRuntimeEvent(raw);
                if (log) logs.push(log);
                const fileEdit = Parsing.extractRuntimeFileEditEvent(raw);
                if (fileEdit) appendFileEditEvent(fileEdit);
              } catch (err) {
                devLogger.warn('[useRuntime] Runtime event parse error:', err);
              }
            });
            setExecutionLogs(logs.slice(-100));
          } else if (isLlmStreamChannel(channel)) {
            const llmLogs = payload.lines
              .filter((line) => runtimeLineMatchesWorkspace(line, workspace))
              .map((line) => parseLlmStreamLine(channel, line))
              .filter((entry): entry is LogEntry => Boolean(entry));
            const uniqueLogs = llmLogs.filter((log) => {
              const runScope = resolveLlmLogRunScope(log);
              const activeRunId = Parsing.toStringValue(runId);
              if (runScope && runScope !== llmRunScopeRef.current) {
                if (!activeRunId || runScope === activeRunId || llmRunScopeRef.current === 'global') {
                  llmRunScopeRef.current = runScope;
                }
              }
              const dedupKey = buildLlmDedupKey(log, llmRunScopeRef.current);
              if (seenLlmEventIdsRef.current.has(dedupKey)) {
                return false;
              }
              seenLlmEventIdsRef.current.add(dedupKey);
              if (seenLlmEventIdsRef.current.size > 5000) {
                const entries = Array.from(seenLlmEventIdsRef.current);
                seenLlmEventIdsRef.current = new Set(entries.slice(-2500));
              }
              return true;
            }).map((log) => withLlmRunScope(log, llmRunScopeRef.current));
            if (uniqueLogs.length > 0) {
              const current = useRuntimeStore.getState().llmStreamEvents;
              setLlmStreamEvents([...current, ...uniqueLogs].slice(-180));
            }
          } else if (isProcessStreamChannel(channel) || isRuntimeFactoryOrBenchEventChannel(channel)) {
            const processLogs: LogEntry[] = [];
            payload.lines.forEach((line: string) => {
              if (!line.trim()) return;
              if (!runtimeLineMatchesWorkspace(line, workspace)) return;
              try {
                const raw = JSON.parse(line);
                if (Parsing.isRecord(raw)) mergeTaskLifecycleFromRaw(raw);
              } catch {
                // Process-stream parsing below still handles non-JSON text lines.
              }
              const entry = parseProcessStreamLine(channel, line);
              if (entry) processLogs.push(entry);
            });
            if (processLogs.length > 0) {
              const current = useRuntimeStore.getState().processStreamEvents;
              setProcessStreamEvents(Parsing.appendLogEntries(current, processLogs, 240));
            }
          }
          return;
        }

        if (payload.type === 'line' && payload.text) {
          if (channel === 'dialogue') {
            try {
              const raw = JSON.parse(payload.text);
              if (!runtimeRecordMatchesWorkspace(raw, workspace)) return;
              const normalized = normalizeDialogueEvent(raw);
              if (normalized) {
                const eventId = String((raw as { event_id?: string }).event_id || '');
                if (eventId && seenDialogueIdsRef.current.has(eventId)) return;
                if (eventId) {
                  seenDialogueIdsRef.current.add(eventId);
                  if (seenDialogueIdsRef.current.size > 5000) {
                    const entries = Array.from(seenDialogueIdsRef.current);
                    seenDialogueIdsRef.current = new Set(entries.slice(-2500));
                  }
                }
                appendDialogueEvent(normalized);
              }
            } catch (err) {
              devLogger.warn('[useRuntime] Dialogue line parse error:', err);
            }
          } else if (channel === 'runtime_events') {
            try {
              const raw = JSON.parse(payload.text);
              if (!runtimeRecordMatchesWorkspace(raw, workspace)) return;
              if (Parsing.isRecord(raw)) mergeTaskLifecycleFromRaw(raw);
              const log = parseRuntimeEvent(raw);
              if (log) {
                appendExecutionLog(log);
                const fileEdit = Parsing.extractRuntimeFileEditEvent(raw);
                if (fileEdit) appendFileEditEvent(fileEdit);

                if (raw.name === 'pm_quality_gate_retry' || raw.name === 'pm_quality_gate') {
                  const qg = parseQualityGateEvent(raw);
                  if (qg) setQualityGate(qg);
                }

                if (raw.event === 'iteration' || raw.event === 'phase_change') {
                  const phase = Parsing.normalizePhaseToken(Parsing.toStringValue(raw.data?.phase || raw.data?.stage));
                  if (phase) {
                    const currentPhase = useRuntimeStore.getState().currentPhase;
                    let nextPhase = phase;
                    if (directorRunningRef.current && phase === 'planning') {
                      nextPhase = currentPhase || 'executing';
                    } else if (currentPhase === 'executing' && phase === 'planning') {
                      nextPhase = currentPhase;
                    }
                    setCurrentPhase(nextPhase);
                  }
                }
              }
            } catch (err) {
              devLogger.warn('[useRuntime] Runtime events line parse error:', err);
            }
          } else if (isLlmStreamChannel(channel)) {
            if (!runtimeLineMatchesWorkspace(payload.text, workspace)) return;
            const llmLog = parseLlmStreamLine(channel, payload.text);
            if (llmLog) {
              const runScope = resolveLlmLogRunScope(llmLog);
              const activeRunId = Parsing.toStringValue(runId);
              if (runScope && runScope !== llmRunScopeRef.current) {
                if (!activeRunId || runScope === activeRunId || llmRunScopeRef.current === 'global') {
                  llmRunScopeRef.current = runScope;
                }
              }
              const dedupKey = buildLlmDedupKey(llmLog, llmRunScopeRef.current);
              if (seenLlmEventIdsRef.current.has(dedupKey)) {
                // Skip
              } else {
                seenLlmEventIdsRef.current.add(dedupKey);
                if (seenLlmEventIdsRef.current.size > 5000) {
                  const entries = Array.from(seenLlmEventIdsRef.current);
                  seenLlmEventIdsRef.current = new Set(entries.slice(-2500));
                }
                appendLlmStreamEvent(withLlmRunScope(llmLog, llmRunScopeRef.current));
              }
            }
          } else if (isProcessStreamChannel(channel) || isRuntimeFactoryOrBenchEventChannel(channel)) {
            if (!runtimeLineMatchesWorkspace(payload.text, workspace)) return;
            try {
              const raw = JSON.parse(payload.text);
              if (Parsing.isRecord(raw)) mergeTaskLifecycleFromRaw(raw);
            } catch {
              // Process-stream parsing below still handles non-JSON text lines.
            }
            const processLog = parseProcessStreamLine(channel, payload.text);
            if (processLog) {
              appendProcessStreamEvent(processLog);
            }
          }
        }
      } catch (err) {
        devLogger.error('[useRuntime] Message processing error:', err);
      }
    },
    [
      appendDialogueEvent,
      appendExecutionLog,
      appendFileEditEvent,
      appendLlmStreamEvent,
      appendProcessStreamEvent,
      appendSequentialTrace,
      appendTaskTrace,
      connection,
      includeInternalBench,
      isWorkspaceControlled,
      loadRuntimeSettings,
      setCurrentPhase,
      setDialogueEvents,
      setEngineStatus,
      setExecutionLogs,
      setLlmStatus,
      setLlmStreamEvents,
      setLancedbStatus,
      setProcessStreamEvents,
      setQualityGate,
      runId,
      setRunId,
      setSnapshot,
      setTasks,
      setWorkers,
      updateTaskProgress,
      workspace,
    ]
  );

  // Register message handler
  useEffect(() => {
    const unregister = connection.registerMessageHandler(processMessage);
    return () => {
      unregister();
    };
  }, [processMessage, connection.registerMessageHandler]);

  // Workspace change handling — only trigger on actual workspace change.
  // Previously depended on connection.transportConnected/transportReconnecting,
  // which caused an infinite reconnect loop (same bug as useRuntimeConnection).
  const prevWorkspaceRef = useRef<string>(workspace);
  useEffect(() => {
    if (!workspace) return;
    if (workspace === prevWorkspaceRef.current) return;
    prevWorkspaceRef.current = workspace;

    seenDialogueIdsRef.current.clear();
    seenLlmEventIdsRef.current.clear();
    seenV2EventIdsRef.current.clear();
    llmRunScopeRef.current = 'global';
    directorRunningRef.current = false;

    resetForWorkspace();
  }, [workspace]);

  return {
    live: connection.live,
    connected: connection.connected,
    isConnected: connection.isConnected,
    error: connection.error,
    reconnecting: connection.reconnecting,
    attemptCount: connection.attemptCount,
    pmStatus,
    directorStatus,
    engineStatus,
    llmStatus,
    lancedbStatus,
    snapshot,
    anthroState,
    dialogueEvents,
    setDialogueEvents,
    qualityGate,
    executionLogs,
    llmStreamEvents,
    processStreamEvents,
    currentPhase,
    fileEditEvents,
    tasks,
    workers,
    runId,
    taskProgressMap,
    taskTraceMap,
    sequentialTraceMap,
    connect: connection.connect,
    disconnect: connection.disconnect,
    reconnect: connection.reconnect,
    refresh: connection.reconnect,
    updateSubscription: connection.updateSubscription,
  };
}
