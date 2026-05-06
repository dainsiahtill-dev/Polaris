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
import { toCanonicalProjection } from '@/runtime/projectionCompat';

// ============================================================================
// Types (re-exported for backward compatibility)
// ============================================================================

export type { FileEditEvent, RuntimeWorkerState, SequentialTraceEvent };

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
  roles?: ('pm' | 'director' | 'qa')[];
  baseUrl?: string;
  autoConnect?: boolean;
  maxRetries?: number;
  baseDelay?: number;
  workspace?: string;
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
  setTaskProgressMap: (map: Map<string, {
    phase?: string;
    phaseIndex?: number;
    phaseTotal?: number;
    retryCount?: number;
    maxRetries?: number;
    currentFile?: string;
  }>) => void;
  taskTraceMap: Map<string, TaskTraceEvent[]>;
  setTaskTraceMap: (map: Map<string, TaskTraceEvent[]>) => void;
  sequentialTraceMap: Map<string, SequentialTraceEvent[]>;
  setSequentialTraceMap: (map: Map<string, SequentialTraceEvent[]>) => void;
  connect: () => void;
  disconnect: () => void;
  reconnect: () => void;
  refresh: () => void;
  updateSubscription: (roles: ('pm' | 'director' | 'qa')[]) => void;
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
  return { type: 'line', channel: 'runtime_events', text: JSON.stringify(mergedPayload) };
}

function isRuntimeV2Envelope(payload: WebSocketMessage): boolean {
  const record = payload as unknown as Record<string, unknown>;
  const schemaVersion = String(record.schema_version || '').trim();
  return schemaVersion === 'runtime.v2' || Boolean(record.channel && record.kind && record.payload);
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

function normalizeStreamEventToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function streamEventLabel(eventType: string): string {
  switch (eventType) {
    case 'thinking_chunk':
      return '思考流';
    case 'content_chunk':
      return '输出流';
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
  return eventType === 'thinking_chunk' || eventType === 'content_chunk';
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

function parseLlmStreamLine(channel: string, line: string): LogEntry | null {
  const raw = String(line || '').trim();
  if (!raw) return null;

  const parsed = Parsing.tryParseJsonObject(raw);
  let message = raw;
  let timestamp = new Date().toISOString();
  let source = 'LLM';
  let level: LogEntry['level'] = 'thinking';
  let details = '';

  if (parsed) {
    if (isLlmStreamChannel(channel) && !isLlmPayloadCompatible(parsed)) return null;

    const ts = String(parsed.ts || parsed.timestamp || '').trim();
    if (ts) timestamp = ts;

    const actor = String(parsed.actor || parsed.role || parsed.source || '').trim();
    if (actor) source = Parsing.normalizeActorLabel(actor);

    const thinking = String(parsed.thinking || parsed.content || parsed.message || '').trim();
    const eventName = String(parsed.event || parsed.name || parsed.kind || '').trim();
    const eventToken = eventName.toLowerCase();
    const modelName = String(parsed.model || parsed.model_name || '').trim();
    const rawObj = parsed.raw && typeof parsed.raw === 'object' ? (parsed.raw as Record<string, unknown>) : null;
    const streamEvent = rawObj ? String(rawObj.stream_event || '').trim().toLowerCase() : '';
    const rawEvent = rawObj ? String(rawObj.event || rawObj.name || '').trim() : '';
    const rawSummary = rawObj ? String(rawObj.summary || rawObj.message || '').trim() : '';
    const rawContent = rawObj ? String(rawObj.content || '').trim() : '';
    const eventData = parsed.data && typeof parsed.data === 'object' ? (parsed.data as Record<string, unknown>) : null;
    const dataSummary = eventData ? String(eventData.summary || eventData.message || '').trim() : '';
    const dataPreview = eventData ? String(eventData.preview || '').trim() : '';
    const dataBackend = eventData ? String(eventData.backend || '').trim() : '';
    const dataDuration = eventData ? String(eventData.duration_ms || '').trim() : '';
    const dataError = eventData ? String(eventData.error || '').trim() : '';
    const dataTaskCount = eventData ? String(eventData.task_count || '').trim() : '';
    const dataOutputChars = eventData ? Number(eventData.output_chars || 0) : 0;
    const dataStage = eventData ? String(eventData.stage || '').trim().toLowerCase() : '';

    const normalizedEvent = streamEvent || eventToken;

    if (normalizedEvent === 'thinking_chunk') {
      message = rawContent || thinking || 'LLM thinking';
      level = 'thinking';
      details = modelName ? `model=${modelName}` : '';
    } else if (normalizedEvent === 'content_chunk') {
      message = rawContent || thinking || 'LLM output';
      level = 'info';
      details = modelName ? `model=${modelName}` : '';
    } else if (normalizedEvent === 'tool_call') {
      const toolName = rawObj ? String(rawObj.tool || '').trim() : '';
      const toolArgs = rawObj && typeof rawObj.args === 'object' ? JSON.stringify(rawObj.args) : '';
      message = toolName ? `调用工具: ${toolName}` : '调用工具';
      details = toolArgs ? `args=${toolArgs.slice(0, 180)}` : '';
      level = 'thinking';
    } else if (normalizedEvent === 'tool_result') {
      const toolName = rawObj ? String(rawObj.tool || '').trim() : '';
      const rawSuccess = rawObj ? rawObj.success : undefined;
      const status = rawSuccess === undefined ? 'done' : (rawSuccess ? 'ok' : 'failed');
      message = toolName ? `工具结果: ${toolName} (${status})` : `工具结果 (${status})`;
      const resultObj = rawObj && typeof rawObj.result === 'object' ? (rawObj.result as Record<string, unknown>) : null;
      details = resultObj ? String(resultObj.error || resultObj.message || '') : '';
      level = status === 'failed' ? 'error' : 'success';
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
      const taskId = eventData ? String(eventData.task_id || '').trim() : '';
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
      level = Parsing.mapSeverityToLevel(String(parsed.severity || '').trim(), 'thinking');
    }

    const eventLabel = streamEventLabel(normalizedEvent);
    const tags = [normalizedEvent].filter((token) => token.length > 0);
    const runScope = extractLlmRunScope(parsed);
    const meta: Record<string, unknown> = {
      channel,
      streamEvent: normalizedEvent || undefined,
      role: actor || undefined,
      model: modelName || undefined,
      runId: runScope || undefined,
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
                    : 'Planner'
  );

  let timestamp = new Date().toISOString();
  let source = baseSource;
  let message = raw;
  let details = '';
  let level: LogEntry['level'] = 'info';

  const parsed = Parsing.tryParseJsonObject(raw);
  if (parsed) {
    const canonicalChannel = String(parsed.channel || '').trim();
    if (channel === 'process' && canonicalChannel && canonicalChannel !== 'process') return null;
    if (channel === 'system' && canonicalChannel && canonicalChannel !== 'system') return null;

    const parsedTs = String(parsed.ts || parsed.timestamp || parsed.time || '').trim();
    if (parsedTs) timestamp = parsedTs;

    const parsedRole = String(parsed.role || parsed.actor || parsed.source || '').trim();
    if (parsedRole) source = Parsing.normalizeActorLabel(parsedRole);

    const eventName = String(parsed.event || parsed.name || parsed.kind || parsed.type || '').trim();
    const summary = String(parsed.summary || parsed.message || parsed.text || '').trim();
    const dataObj = parsed.data && typeof parsed.data === 'object' ? (parsed.data as Record<string, unknown>) : null;
    const rawObj = parsed.raw && typeof parsed.raw === 'object' ? (parsed.raw as Record<string, unknown>) : null;
    const dataMsg = dataObj ? String(dataObj.message || dataObj.summary || '').trim() : '';
    const rawMsg = rawObj ? String(rawObj.message || rawObj.summary || '').trim() : '';
    const toolName = dataObj
      ? String(dataObj.tool || dataObj.tool_name || rawObj?.tool || rawObj?.tool_name || '').trim()
      : String(rawObj?.tool || rawObj?.tool_name || '').trim();
    const command = dataObj
      ? String(dataObj.command || rawObj?.command || '').trim()
      : String(rawObj?.command || '').trim();

    message = summary || dataMsg || rawMsg || eventName || raw;
    details = [toolName ? `tool=${toolName}` : '', command ? `cmd=${command}` : '']
      .filter((item) => item.length > 0)
      .join(' ');

    level = Parsing.mapSeverityToLevel(String(parsed.severity || '').trim(), level);
    if (level === 'info') {
      const token = `${eventName} ${summary} ${dataMsg} ${rawMsg}`.toLowerCase();
      if (/error|failed|exception|traceback|timeout/.test(token)) level = 'error';
      else if (/warn|retry|blocked/.test(token)) level = 'warning';
      else if (/tool|invoke|llm|thinking|prompt/.test(token)) level = 'thinking';
      else if (/success|completed|done|passed/.test(token)) level = 'success';
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
    },
  };
}

function parseRuntimeEvent(raw: Record<string, unknown>): LogEntry | null {
  const eventId = String(raw.event_id || raw.seq || Date.now());
  const ts = String(raw.ts || raw.timestamp || new Date().toISOString());
  const actor = String(raw.actor || raw.role || 'System');
  const eventName = String(raw.name || raw.event || 'unknown');
  const data = (raw.data || raw.output || {}) as Record<string, unknown>;

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
      details = String((data as Record<string, string>)?.quality_summary || '');
      break;
    case 'pm_quality_gate':
      message = (data as Record<string, boolean>)?.passed ? '质量检查通过' : '质量检查未通过';
      details = `分数: ${String((data as Record<string, unknown>)?.score || (data as Record<string, string>)?.quality_summary || 'N/A')}`;
      break;
    case 'llm_invoke':
      message = `LLM 调用完成 (${String((data as Record<string, string>)?.model || 'unknown')})`;
      details = `${String(((data as Record<string, Record<string, number>>)?.usage)?.total_tokens || '?')} tokens`;
      break;
    case 'iteration':
      message = `开始第 ${String((data as Record<string, number>)?.iteration || '?')} 轮迭代`;
      break;
    case 'config':
      message = '配置加载完成';
      break;
    default:
      message = String(raw.summary || eventName);
  }

  return {
    id: eventId,
    timestamp: ts,
    level,
    source: actor,
    message,
    details,
    meta: data,
  };
}

function parseQualityGateEvent(raw: Record<string, unknown>): QualityGateData | null {
  const data = (raw.data || raw.output || {}) as Record<string, unknown>;

  const qualitySummary = String((data as Record<string, string>)?.quality_summary || '');
  const scoreMatch = qualitySummary.match(/\d+/);
  const score = parseInt(String(scoreMatch?.[0] || (data as Record<string, number>)?.score || 0), 10);
  const attempt = parseInt(String(data.attempt || 1), 10);
  const maxAttempts = parseInt(String(data.max_attempts || 3), 10);

  const issues: QualityGateData['issues'] = [];
  const criticalIssues = (data.critical_issues || []) as string[];
  const warnings = (data.warnings || []) as string[];

  criticalIssues.forEach((msg) => {
    issues.push({ type: 'critical', message: msg });
  });
  warnings.forEach((msg) => {
    issues.push({ type: 'warning', message: msg });
  });

  return {
    score,
    passed: score >= 80 && issues.filter(i => i.type === 'critical').length === 0,
    attempt,
    maxAttempts,
    summary: String(data.quality_summary || ''),
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
    roles = ['pm', 'director', 'qa'],
    baseUrl,
    autoConnect = true,
    maxRetries = Infinity,
    baseDelay = 1000,
    workspace: workspaceProp,
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
          payload = normalizeRuntimeV2Envelope(payload as unknown as Record<string, unknown>);
          channel = String(payload.channel || '').trim();
        }

        const finalMsgType = String(payload.type || '').trim().toLowerCase();
        if (finalMsgType === 'ping') {
          connection.sendCommand({ type: 'PONG' });
          return;
        }

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

        if (msgType === 'file_edit') {
          const fileEditEvent = Parsing.extractFileEditEvents({ event: payload.event, timestamp: payload.timestamp });
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
        } else if (msgType === 'runtime_event') {
          payload = { type: 'line', channel: 'runtime_events', text: Parsing.isRecord(payload.event) ? JSON.stringify(payload.event) : (typeof payload.line === 'string' ? payload.line : '') };
        } else if (msgType === 'llm_stream' || msgType === 'process_stream') {
          const eventText = Parsing.isRecord(payload.event) ? JSON.stringify(payload.event) : '';
          const lineText = typeof payload.line === 'string' ? payload.line : '';
          const fallbackChannel = msgType === 'llm_stream' ? 'llm' : 'process';
          payload = { type: 'line', channel: channel || fallbackChannel, text: eventText || lineText };
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

          const projection = toCanonicalProjection(payload);
          const directorState = Parsing.parseDirectorStateToken(payload.director_status ?? null);
          directorRunningRef.current = directorState.running;

          const primaryStatus = selectPrimaryStatus(projection);
          const systemActive = isSystemActive(projection);
          const rawPhase = systemActive ? primaryStatus.replace(/-/g, '_') : 'idle';
          const nextPhase = Parsing.normalizePhaseToken(rawPhase) || 'idle';
          setCurrentPhase(nextPhase);

          const canonicalTasks = selectTaskRows(projection);
          setTasks(canonicalTasks.map(t => ({
            id: t.id,
            title: t.title,
            status: t.status.toUpperCase() as TaskStatus,
            goal: t.title,
            priority: (t.priority === 'high' ? 1 : t.priority === 'medium' ? 3 : t.priority === 'low' ? 5 : 3) as PmTask['priority'],
            assignee: t.assignee,
            done: t.status.toUpperCase() === 'COMPLETED' || t.status.toUpperCase() === 'SUCCESS',
            acceptance: [],
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
                const log = parseRuntimeEvent(raw);
                if (log) logs.push(log);
              } catch (err) {
                devLogger.warn('[useRuntime] Runtime event parse error:', err);
              }
            });
            setExecutionLogs(logs.slice(-100));
          } else if (isLlmStreamChannel(channel)) {
            const llmLogs = payload.lines
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
            });
            if (uniqueLogs.length > 0) {
              const current = useRuntimeStore.getState().llmStreamEvents;
              setLlmStreamEvents([...current, ...uniqueLogs].slice(-180));
            }
          } else if (isProcessStreamChannel(channel)) {
            const processLogs = payload.lines
              .map((line) => parseProcessStreamLine(channel, line))
              .filter((entry): entry is LogEntry => Boolean(entry));
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
              const log = parseRuntimeEvent(raw);
              if (log) {
                appendExecutionLog(log);

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
                appendLlmStreamEvent(llmLog);
              }
            }
          } else if (isProcessStreamChannel(channel)) {
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

  // Setters for mutable maps (backward compatibility)
  const setTaskProgressMap = useCallback(
    (map: Map<string, { phase?: string; phaseIndex?: number; phaseTotal?: number; retryCount?: number; maxRetries?: number; currentFile?: string }>) => {
      useRuntimeStore.setState({ taskProgressMap: map });
    },
    []
  );

  const setTaskTraceMap = useCallback(
    (map: Map<string, TaskTraceEvent[]>) => {
      useRuntimeStore.setState({ taskTraceMap: map });
    },
    []
  );

  const setSequentialTraceMap = useCallback(
    (map: Map<string, SequentialTraceEvent[]>) => {
      useRuntimeStore.setState({ sequentialTraceMap: map });
    },
    []
  );

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
    setTaskProgressMap,
    taskTraceMap,
    setTaskTraceMap,
    sequentialTraceMap,
    setSequentialTraceMap,
    connect: connection.connect,
    disconnect: connection.disconnect,
    reconnect: connection.reconnect,
    refresh: connection.reconnect,
    updateSubscription: connection.updateSubscription,
  };
}

// Re-exports for backward compatibility
export const useV2WebSocket = useRuntime;
export const useHybridDataProvider = useRuntime;

export function refreshRuntime(): void {
  // hook-scoped runtime has no global singleton
}

export function resetRuntime(): void {
  useRuntimeStore.getState().resetAll();
}
