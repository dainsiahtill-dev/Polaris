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
import {
  DEFAULT_RUNTIME_ROLES,
  FACTORY_EVENT_CHANNEL,
  BENCH_EVENT_CHANNEL,
  isInternalBenchEventChannel,
  isRuntimeFactoryOrBenchEventChannel,
  normalizeRuntimeWorkspacePath,
  collectRuntimeWorkspacePaths,
  runtimeRecordMatchesWorkspace,
  runtimeLineMatchesWorkspace,
  isSettingsChangedEvent,
} from './_runtimeEventFilter';
import {
  buildLlmDedupKey,
  firstRecord,
  isLlmStreamChannel,
  isRuntimeV2Envelope,
  mergeResidentIntoSnapshot,
  mergeRuntimeTaskLifecycle,
  normalizeRuntimeV2Envelope,
  parseLlmStreamLine,
  parseProcessStreamLine,
  parseQualityGateEvent,
  parseRuntimeEvent,
  residentStatusFromDomainPayload,
  resolveLlmLogRunScope,
  runtimeTaskLifecyclePayload,
  withLlmRunScope,
} from './_runtimeParsers';
import type { RuntimeTaskLifecycleUpdate } from './_runtimeParsers';
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

// ============================================================================
// Pure Parsing Functions (moved from useRuntime.ts)
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

        const applyRuntimeEventRecord = (raw: Record<string, unknown>): LogEntry | null => {
          mergeTaskLifecycleFromRaw(raw);
          const log = parseRuntimeEvent(raw);
          const fileEdit = Parsing.extractRuntimeFileEditEvent(raw);
          if (fileEdit) appendFileEditEvent(fileEdit);

          if (raw.name === 'pm_quality_gate_retry' || raw.name === 'pm_quality_gate') {
            const qg = parseQualityGateEvent(raw);
            if (qg) setQualityGate(qg);
          }

          if (raw.event === 'iteration' || raw.event === 'phase_change') {
            const data = firstRecord(raw.data);
            const phase = Parsing.normalizePhaseToken(Parsing.toStringValue(data?.phase || data?.stage));
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

          return log;
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
            const runtimeLogs: LogEntry[] = [];
            payload.lines.forEach((line: string) => {
              if (!line.trim()) return;
              if (!runtimeLineMatchesWorkspace(line, workspace)) return;
              try {
                const raw = JSON.parse(line);
                if (Parsing.isRecord(raw)) {
                  if (channel === 'system') {
                    const runtimeLog = applyRuntimeEventRecord(raw);
                    if (runtimeLog) runtimeLogs.push(runtimeLog);
                  } else {
                    mergeTaskLifecycleFromRaw(raw);
                  }
                }
              } catch {
                // Process-stream parsing below still handles non-JSON text lines.
              }
              const entry = parseProcessStreamLine(channel, line);
              if (entry) processLogs.push(entry);
            });
            if (runtimeLogs.length > 0) {
              setExecutionLogs(runtimeLogs.slice(-100));
            }
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
              if (Parsing.isRecord(raw)) {
                if (channel === 'system') {
                  const runtimeLog = applyRuntimeEventRecord(raw);
                  if (runtimeLog) appendExecutionLog(runtimeLog);
                } else {
                  mergeTaskLifecycleFromRaw(raw);
                }
              }
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
