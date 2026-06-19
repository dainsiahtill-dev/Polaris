/**
 * useFactory - Factory Run state management hook with React Query
 *
 * Single frontend source of truth for Factory lifecycle and runtime event state.
 * Provides:
 * - React Query caching for run status
 * - Automatic request cancellation via AbortController
 * - Nat-JetStream/WebSocket event subscription with reconnection logic
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useRuntimeTransport } from '@/runtime/transport';
import {
  
  getFactoryRun,
  getFactoryRunArtifacts,
  listFactoryRuns,
  pauseFactoryRun,
  resumeFactoryRun,
  retryFactoryRunFromCheckpoint,
  startFactoryRun,
  stopFactoryRun,
} from '@/services';
import type {
  FactoryAuditEvent,
  FactoryControlAction,
  FactoryRunArtifact,
  FactoryRunArtifactsResponse,
  FactoryRunStatus,
  FactoryStartOptions,
} from '@/services';
import { QueryKeys } from '@/lib/queryClient';

export type {
  FactoryAuditEvent,
  FactoryRunArtifact,
  FactoryRunArtifactsResponse,
  FactoryRunStatus,
  FactoryStartOptions,
};

const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_DELAY_MS = 1000;
const CANCELLED_FACTORY_RUN_STATUSES = new Set(['cancelled', 'canceled']);
const FAILED_FACTORY_RUN_STATUSES = new Set(['failed', 'error', 'blocked', 'timeout']);
const TERMINAL_FACTORY_RUN_STATUSES = new Set([
  'completed',
  ...CANCELLED_FACTORY_RUN_STATUSES,
  ...FAILED_FACTORY_RUN_STATUSES,
]);

type FactoryControlMutationInput = {
  runId: string;
  action: FactoryControlAction;
  reason?: string;
};

function factoryRunArtifactsKey(runId: string) {
  return ['factory', 'run', runId, 'artifacts'] as const;
}

function runToken(value: string | null | undefined): string {
  return String(value || '').trim().toLowerCase();
}

function terminalRunToken(run: FactoryRunStatus | null): string {
  if (!run) {
    return '';
  }
  const status = runToken(run.status);
  if (TERMINAL_FACTORY_RUN_STATUSES.has(status)) {
    return status;
  }
  const phase = runToken(run.phase);
  if (TERMINAL_FACTORY_RUN_STATUSES.has(phase)) {
    return phase;
  }
  return '';
}

function isTerminalRun(run: FactoryRunStatus | null): boolean {
  return Boolean(terminalRunToken(run));
}

function runStatusFromFactoryEvent(
  runId: string,
  payload: Record<string, unknown>,
  previous: FactoryRunStatus | null
): FactoryRunStatus | null {
  const eventType = runToken(String(payload.type || payload.kind || ''));
  if (!eventType) return null;
  const timestamp = String(payload.timestamp || new Date().toISOString());
  const stage = String(payload.stage || previous?.current_stage || previous?.phase || '').trim();
  const result = (payload.result && typeof payload.result === 'object'
    ? (payload.result as Record<string, unknown>)
    : {}) as Record<string, unknown>;
  const resultStatus = runToken(String(result.status || ''));

  let status = previous?.status || 'running';
  if (eventType === 'paused') {
    status = 'paused';
  } else if (eventType === 'cancelled' || eventType === 'canceled' || resultStatus === 'cancelled' || resultStatus === 'canceled') {
    status = 'cancelled';
  } else if (eventType === 'completed') {
    status = 'completed';
  } else if (eventType === 'failed' || resultStatus === 'failed') {
    status = 'failed';
  } else if (
    eventType === 'started' ||
    eventType === 'stage_started' ||
    eventType === 'stage_heartbeat' ||
    eventType === 'stage_completed' ||
    eventType === 'metadata_updated' ||
    eventType === 'resumed'
  ) {
    status = 'running';
  }

  const isTerminal = TERMINAL_FACTORY_RUN_STATUSES.has(runToken(status));
  const progress = status === 'completed'
    ? 100
    : typeof previous?.progress === 'number'
      ? previous.progress
      : 0;
  const failureDetail = String(payload.reason || payload.message || result.output || '').trim();

  return {
    run_id: runId,
    phase: stage || previous?.phase || 'implementation',
    status,
    current_stage: stage || previous?.current_stage || null,
    last_successful_stage: previous?.last_successful_stage ?? null,
    progress,
    roles: previous?.roles ?? {},
    gates: previous?.gates ?? [],
    failure: status === 'failed' || status === 'cancelled'
      ? previous?.failure ?? {
          failure_type: status === 'cancelled' ? 'cancelled' : 'runtime',
          code: status === 'cancelled' ? 'FACTORY_RUN_CANCELLED' : 'FACTORY_RUN_FAILED',
          detail: failureDetail || (status === 'cancelled' ? 'Factory run cancelled' : 'Factory run failed'),
          phase: stage || previous?.phase || 'implementation',
          recoverable: status !== 'cancelled',
        }
      : previous?.failure,
    created_at: previous?.created_at || timestamp,
    started_at: previous?.started_at || (eventType === 'started' ? timestamp : undefined),
    updated_at: timestamp,
    completed_at: isTerminal ? timestamp : previous?.completed_at,
    summary_md: previous?.summary_md,
    summary_json: previous?.summary_json,
    metadata: {
      ...(previous?.metadata ?? {}),
      last_factory_event_type: eventType,
    },
    artifacts: previous?.artifacts,
    artifacts_error: previous?.artifacts_error,
  };
}

function mergeRunEvidenceFields(
  run: FactoryRunStatus,
  previous: FactoryRunStatus | null
): FactoryRunStatus {
  if (!previous || previous.run_id !== run.run_id) {
    return run;
  }

  return {
    ...run,
    artifacts: run.artifacts ?? previous.artifacts,
    summary_md: run.summary_md ?? previous.summary_md,
    summary_json: run.summary_json ?? previous.summary_json,
    artifacts_error: run.artifacts_error ?? previous.artifacts_error,
  };
}

export interface UseFactoryOptions {
  workspace?: string | null;
  autoResumeLatest?: boolean;
}

export function useFactory(options: UseFactoryOptions = {}) {
  const workspace = String(options.workspace || '').trim();
  const autoResumeLatest = options.autoResumeLatest !== false;
  const [currentRun, setCurrentRun] = useState<FactoryRunStatus | null>(null);
  const [events, setEvents] = useState<FactoryAuditEvent[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [artifactsSnapshot, setArtifactsSnapshot] = useState<FactoryRunArtifactsResponse | null>(null);
  const [artifactsError, setArtifactsError] = useState<string | null>(null);
  const [isArtifactsLoading, setIsArtifactsLoading] = useState(false);

  const queryClient = useQueryClient();
  // Unified WebSocket transport — factory events flow through NAT
  // JetStream (subject ``hp.runtime.<ws>.event.factory.<run_id>``) via
  // the same RuntimeTransportProvider that carries log.llm /
  // log.process / event.bench through one runtime transport.
  const transport = useRuntimeTransport();

  const connectionRef = useRef<{ close: () => void } | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const latestRunIdRef = useRef<string | null>(null);
  const manualDisconnectRef = useRef(false);
  const activeWorkspaceRef = useRef<string>('');
  const abortControllerRef = useRef<AbortController | null>(null);
  const artifactsRequestSeqRef = useRef(0);

  // Query keys
  const factoryRunsKey = QueryKeys.factoryRuns();
  const factoryRunKey = (runId: string) => QueryKeys.factoryRun(runId);

  // Query for fetching a single run status (with cancellation support)
  const fetchRunQuery = useQuery({
    queryKey: ['factory', 'run', 'fetching'] as const,
    queryFn: async ({ queryKey }) => {
      // This is a placeholder - actual fetching is done via fetchRunStatus
      return null;
    },
    enabled: false,
  });

  // Mutation for starting a new run
  const startRunMutation = useMutation({
    mutationFn: async (opts: FactoryStartOptions) => {
      // Cancel any pending requests
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      abortControllerRef.current = new AbortController();

      const result = await startFactoryRun(opts);
      if (!result.ok || !result.data) {
        throw new Error(result.error || '启动Factory失败');
      }
      return result.data;
    },
    onSuccess: (run) => {
      // Invalidate runs list cache
      queryClient.invalidateQueries({ queryKey: factoryRunsKey });
      // Set individual run cache
      queryClient.setQueryData<FactoryRunStatus>(factoryRunKey(run.run_id), run);
      latestRunIdRef.current = run.run_id;
      setCurrentRun((previous) => mergeRunEvidenceFields(run, previous));
    },
    onError: (error: Error) => {
      toast.error(error.message || '启动Factory失败');
    },
  });

  // Mutation for stopping a run
  const stopRunMutation = useMutation({
    mutationFn: async ({ runId, reason }: { runId: string; reason?: string }) => {
      // Cancel any pending requests
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      abortControllerRef.current = new AbortController();

      const result = await stopFactoryRun(runId, reason);
      if (!result.ok || !result.data) {
        throw new Error(result.error || '停止Factory失败');
      }
      return result.data;
    },
    onSuccess: (run) => {
      // Invalidate runs list cache
      queryClient.invalidateQueries({ queryKey: factoryRunsKey });
      // Update individual run cache
      queryClient.setQueryData<FactoryRunStatus>(factoryRunKey(run.run_id), run);
      setCurrentRun((previous) => mergeRunEvidenceFields(run, previous));
      if (isTerminalRun(run)) {
        void fetchRunArtifacts(run.run_id);
        disconnectStream();
      }
    },
    onError: (error: Error) => {
      toast.error(error.message || '停止Factory失败');
    },
  });

  const controlRunMutation = useMutation({
    mutationFn: async ({ runId, action, reason }: FactoryControlMutationInput) => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      abortControllerRef.current = new AbortController();

      const result = action === 'pause'
        ? await pauseFactoryRun(runId, reason)
        : action === 'resume'
          ? await resumeFactoryRun(runId, reason)
          : action === 'retry_from_checkpoint'
            ? await retryFactoryRunFromCheckpoint(runId, reason)
            : await stopFactoryRun(runId, reason);
      if (!result.ok || !result.data) {
        throw new Error(result.error || '控制Factory失败');
      }
      return result.data;
    },
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: factoryRunsKey });
      queryClient.setQueryData<FactoryRunStatus>(factoryRunKey(run.run_id), run);
      latestRunIdRef.current = run.run_id;
      setCurrentRun((previous) => mergeRunEvidenceFields(run, previous));
      if (isTerminalRun(run)) {
        void fetchRunArtifacts(run.run_id);
        disconnectStream();
      }
    },
    onError: (error: Error) => {
      toast.error(error.message || '控制Factory失败');
    },
  });

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const disconnectStream = useCallback(() => {
    manualDisconnectRef.current = true;
    clearReconnectTimer();

    // Cancel any pending requests
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    if (connectionRef.current) {
      connectionRef.current.close();
      connectionRef.current = null;
    }
    setIsStreaming(false);
  }, [clearReconnectTimer]);

  const fetchRunArtifacts = useCallback(async (runId: string) => {
    const normalizedRunId = String(runId || '').trim();
    if (!normalizedRunId) {
      return null;
    }

    const requestSeq = artifactsRequestSeqRef.current + 1;
    artifactsRequestSeqRef.current = requestSeq;
    setIsArtifactsLoading(true);
    setArtifactsError(null);

    try {
      const result = await getFactoryRunArtifacts(normalizedRunId);
      if (artifactsRequestSeqRef.current !== requestSeq) {
        return null;
      }

      if (result.ok && result.data) {
        const snapshot: FactoryRunArtifactsResponse = {
          ...result.data,
          artifacts: result.data.artifacts || [],
        };
        setArtifactsSnapshot(snapshot);
        queryClient.setQueryData<FactoryRunArtifactsResponse>(
          factoryRunArtifactsKey(normalizedRunId),
          snapshot
        );
        setCurrentRun((previous) => {
          if (!previous || previous.run_id !== snapshot.run_id) {
            return previous;
          }
          return {
            ...previous,
            artifacts: snapshot.artifacts,
            summary_md: snapshot.summary_md ?? undefined,
            summary_json: snapshot.summary_json ?? null,
            artifacts_error: null,
          };
        });
        return snapshot;
      }

      const message = result.error || '获取Factory产物失败';
      setArtifactsError(message);
      setCurrentRun((previous) => {
        if (!previous || previous.run_id !== normalizedRunId) {
          return previous;
        }
        return { ...previous, artifacts_error: message };
      });
      return null;
    } catch (error) {
      if (artifactsRequestSeqRef.current !== requestSeq) {
        return null;
      }

      const message = error instanceof Error ? error.message : '获取Factory产物失败';
      setArtifactsError(message);
      setCurrentRun((previous) => {
        if (!previous || previous.run_id !== normalizedRunId) {
          return previous;
        }
        return { ...previous, artifacts_error: message };
      });
      return null;
    } finally {
      if (artifactsRequestSeqRef.current === requestSeq) {
        setIsArtifactsLoading(false);
      }
    }
  }, [queryClient]);

  const fetchRunStatus = useCallback(async (runId: string) => {
    // Cancel previous request if exists
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();

    try {
      const result = await getFactoryRun(runId);
      if (result.ok && result.data) {
        const run = result.data;
        setCurrentRun((previous) => mergeRunEvidenceFields(run, previous));
        // Update cache
        queryClient.setQueryData<FactoryRunStatus>(factoryRunKey(runId), run);
        if (isTerminalRun(run)) {
          void fetchRunArtifacts(runId);
        }
        return run;
      }
      return null;
    } finally {
      // Clean up abort controller
      if (abortControllerRef.current) {
        abortControllerRef.current = null;
      }
    }
  }, [fetchRunArtifacts, queryClient]);

  const connectStream = useCallback(async (runId: string): Promise<boolean> => {
    manualDisconnectRef.current = false;
    clearReconnectTimer();

    if (connectionRef.current) {
      connectionRef.current.close();
      connectionRef.current = null;
    }

    const channel = `event.factory:${runId}`;

    // Translate the runtime.v2 envelope into the
    // factory event shape consumed by this hook. Factory events are
    // published to NAT JetStream by
    // ``FactoryRunService._append_event``; the platform's WebSocket's
    // JetStream consumer forwards every envelope to this handler.
    const handler = (message: unknown): void => {
      if (!message || typeof message !== 'object') return;
      const m = message as Record<string, unknown>;
      const envelope = (m.event && typeof m.event === 'object'
        ? (m.event as Record<string, unknown>)
        : m) as Record<string, unknown>;
      const eventChannel = String(envelope.channel || '').trim();
      if (eventChannel !== 'event.factory' && eventChannel !== channel) return;
      const payload = (envelope.payload && typeof envelope.payload === 'object'
        ? (envelope.payload as Record<string, unknown>)
        : {}) as Record<string, unknown>;
      const eventRunId = String(envelope.run_id || payload.run_id || runId).trim();
      if (eventRunId !== runId) return;
      const kind = String(envelope.kind || payload.type || '');
      const factoryEvent: FactoryAuditEvent = {
        type: String(payload.type || kind || 'unknown'),
        timestamp: String(payload.timestamp || envelope.ts || new Date().toISOString()),
        ...payload,
        run_id: eventRunId,
      } as FactoryAuditEvent;
      setEvents((previous) => [...previous, factoryEvent].slice(-200));

      if (payload.run_id && (payload.status || typeof payload.progress === 'number')) {
        const run = payload as unknown as FactoryRunStatus;
        latestRunIdRef.current = run.run_id;
        setCurrentRun((previous) => mergeRunEvidenceFields(run, previous));
        queryClient.setQueryData<FactoryRunStatus>(factoryRunKey(run.run_id), run);
        if (isTerminalRun(run)) {
          setIsStreaming(false);
          queryClient.invalidateQueries({ queryKey: factoryRunsKey });
          void fetchRunArtifacts(run.run_id);
        }
        return;
      }

      const cached = queryClient.getQueryData<FactoryRunStatus>(factoryRunKey(eventRunId)) || currentRun;
      const runPatch = runStatusFromFactoryEvent(eventRunId, payload, cached);
      if (!runPatch) return;

      latestRunIdRef.current = eventRunId;
      setCurrentRun((previous) => mergeRunEvidenceFields(runPatch, previous));
      queryClient.setQueryData<FactoryRunStatus>(factoryRunKey(eventRunId), runPatch);

      if (isTerminalRun(runPatch)) {
        setIsStreaming(false);
        queryClient.invalidateQueries({ queryKey: factoryRunsKey });
        void fetchRunArtifacts(eventRunId);
        const status = terminalRunToken(runPatch);
        if (status === 'completed') {
          toast.success('Factory Run 完成');
        } else if (FAILED_FACTORY_RUN_STATUSES.has(status)) {
          toast.error(runPatch.failure?.detail || 'Factory Run 失败');
        } else if (CANCELLED_FACTORY_RUN_STATUSES.has(status)) {
          toast.success('Factory Run 已取消');
        }
      }
    };

    let closed = false;
    let messageUnregister: (() => void) | null = null;
    let channelUnsubscribe: (() => void) | null = null;

    try {
      channelUnsubscribe = transport.subscribeChannels([{ channel, tailLines: 0 }]);
      messageUnregister = transport.registerMessageHandler(handler);
      reconnectAttemptsRef.current = 0;
      setIsStreaming(true);
      connectionRef.current = {
        close: () => {
          if (closed) return;
          closed = true;
          try { messageUnregister?.(); } catch { /* ignore */ }
          try { channelUnsubscribe?.(); } catch { /* ignore */ }
        },
      };
      return true;
    } catch (streamError) {
      const message = streamError instanceof Error ? streamError.message : '连接Factory实时流失败';
      toast.error(message);
      setIsStreaming(false);
      return false;
    }
  }, [clearReconnectTimer, currentRun, fetchRunArtifacts, queryClient, factoryRunKey, factoryRunsKey, transport]);

  const startRun = useCallback(async (opts: FactoryStartOptions): Promise<FactoryRunStatus | null> => {
    setEvents([]);
    setArtifactsSnapshot(null);
    setArtifactsError(null);

    try {
      const run = await startRunMutation.mutateAsync(opts);

      const connected = await connectStream(run.run_id);
      if (!connected) {
        await fetchRunStatus(run.run_id);
      }

      toast.success(`Factory 已启动: ${run.run_id}`);
      return run;
    } catch {
      return null;
    }
  }, [connectStream, fetchRunStatus, startRunMutation]);

  const stopRun = useCallback(async (runId: string, reason?: string) => {
    try {
      return await stopRunMutation.mutateAsync({ runId, reason });
    } catch {
      return null;
    }
  }, [stopRunMutation]);

  const controlRun = useCallback(async (
    runId: string,
    action: FactoryControlAction,
    reason?: string
  ): Promise<FactoryRunStatus | null> => {
    try {
      return await controlRunMutation.mutateAsync({ runId, action, reason });
    } catch {
      return null;
    }
  }, [controlRunMutation]);

  const pauseRun = useCallback(
    (runId: string, reason?: string) => controlRun(runId, 'pause', reason),
    [controlRun],
  );

  const resumeRun = useCallback(
    (runId: string, reason?: string) => controlRun(runId, 'resume', reason),
    [controlRun],
  );

  const retryRunFromCheckpoint = useCallback(async (runId: string, reason?: string) => {
    const run = await controlRun(runId, 'retry_from_checkpoint', reason);
    if (run && !isTerminalRun(run)) {
      const connected = await connectStream(run.run_id);
      if (!connected) {
        await fetchRunStatus(run.run_id);
      }
    }
    return run;
  }, [connectStream, controlRun, fetchRunStatus]);

  const fetchRuns = useCallback(async (limit = 20) => {
    // Cancel any pending requests
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();

    try {
      const result = await listFactoryRuns(limit);
      if (result.ok && result.data) {
        // Update cache
        queryClient.setQueryData<FactoryRunStatus[]>(factoryRunsKey, result.data);
        return result.data;
      }
      return [];
    } finally {
      if (abortControllerRef.current) {
        abortControllerRef.current = null;
      }
    }
  }, [queryClient, factoryRunsKey]);

  const resumeLatestRun = useCallback(async () => {
    if (!workspace || !autoResumeLatest) {
      return null;
    }

    const latestRuns = await fetchRuns(1);
    const latest = latestRuns[0] || null;
    const latestRunId = latest?.run_id || '';
    const sameLatestRun = Boolean(latestRunId && latestRunIdRef.current === latestRunId);
    setCurrentRun((previous) => latest ? mergeRunEvidenceFields(latest, previous) : null);
    setEvents([]);
    if (!sameLatestRun) {
      setArtifactsSnapshot(null);
    }
    setArtifactsError(null);

    if (latest) {
      latestRunIdRef.current = latest.run_id;
      if (!isTerminalRun(latest)) {
        const connected = await connectStream(latest.run_id);
        if (!connected) {
          await fetchRunStatus(latest.run_id);
        }
      } else {
        await fetchRunArtifacts(latest.run_id);
      }
    }

    return latest;
  }, [autoResumeLatest, connectStream, fetchRunArtifacts, fetchRunStatus, fetchRuns, workspace]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      disconnectStream();
    };
  }, [disconnectStream]);

  // Handle workspace changes
  useEffect(() => {
    if (activeWorkspaceRef.current === workspace) {
      return;
    }

    activeWorkspaceRef.current = workspace;
    disconnectStream();
    reconnectAttemptsRef.current = 0;
    latestRunIdRef.current = null;
    artifactsRequestSeqRef.current += 1;
    setCurrentRun(null);
    setEvents([]);
    setArtifactsSnapshot(null);
    setArtifactsError(null);
    setIsArtifactsLoading(false);
    // Note: Don't reset queryClient here to preserve cache

    if (!workspace) {
      return;
    }

    void resumeLatestRun();
  }, [workspace, disconnectStream, resumeLatestRun]);

  const currentRunId = currentRun?.run_id || '';
  useEffect(() => {
    if (!currentRunId) {
      setArtifactsSnapshot(null);
      setArtifactsError(null);
      return;
    }

    void fetchRunArtifacts(currentRunId);
  }, [currentRunId, fetchRunArtifacts]);

  const activeArtifactsSnapshot =
    artifactsSnapshot?.run_id === currentRunId ? artifactsSnapshot : null;

  return {
    currentRun,
    events,
    isLoading: startRunMutation.isPending || stopRunMutation.isPending || controlRunMutation.isPending,
    error: (startRunMutation.error || stopRunMutation.error || controlRunMutation.error) as Error | null,
    isStreaming,
    artifacts: activeArtifactsSnapshot?.artifacts || currentRun?.artifacts || [],
    summaryMd: activeArtifactsSnapshot?.summary_md ?? currentRun?.summary_md ?? null,
    summaryJson: activeArtifactsSnapshot?.summary_json ?? currentRun?.summary_json ?? null,
    artifactsError: artifactsError || currentRun?.artifacts_error || null,
    isArtifactsLoading,
    startRun,
    stopRun,
    pauseRun,
    resumeRun,
    retryRunFromCheckpoint,
    fetchRunStatus,
    fetchRunArtifacts,
    fetchRuns,
    resumeLatestRun,
    connectEventStream: connectStream,
    disconnectStream,
  };
}
