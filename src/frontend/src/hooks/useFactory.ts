/**
 * useFactory - Factory Run state management hook with React Query
 *
 * Single frontend source of truth for Factory lifecycle and SSE stream state.
 * Provides:
 * - React Query caching for run status
 * - Automatic request cancellation via AbortController
 * - SSE streaming with reconnection logic
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  connectFactoryStream,
  getFactoryRun,
  listFactoryRuns,
  startFactoryRun,
  stopFactoryRun,
} from '@/services';
import type {
  FactoryAuditEvent,
  FactoryRunStatus,
  FactoryStartOptions,
} from '@/services';
import { QueryKeys } from '@/lib/queryClient';

export type { FactoryAuditEvent, FactoryRunStatus, FactoryStartOptions };

const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_DELAY_MS = 1000;

function isTerminalRun(run: FactoryRunStatus | null): boolean {
  if (!run) {
    return false;
  }
  const status = String(run.status || '').trim().toLowerCase();
  return ['completed', 'failed', 'cancelled'].includes(status);
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

  const queryClient = useQueryClient();

  const connectionRef = useRef<{ eventSource: EventSource; close: () => void } | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const latestRunIdRef = useRef<string | null>(null);
  const manualDisconnectRef = useRef(false);
  const activeWorkspaceRef = useRef<string>('');
  const abortControllerRef = useRef<AbortController | null>(null);

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
      setCurrentRun(run);
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
      setCurrentRun(run);
      if (isTerminalRun(run)) {
        disconnectStream();
      }
    },
    onError: (error: Error) => {
      toast.error(error.message || '停止Factory失败');
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

  const fetchRunStatus = useCallback(async (runId: string) => {
    // Cancel previous request if exists
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();

    try {
      const result = await getFactoryRun(runId);
      if (result.ok && result.data) {
        setCurrentRun(result.data);
        // Update cache
        queryClient.setQueryData<FactoryRunStatus>(factoryRunKey(runId), result.data);
        return result.data;
      }
      return null;
    } finally {
      // Clean up abort controller
      if (abortControllerRef.current) {
        abortControllerRef.current = null;
      }
    }
  }, [queryClient]);

  const connectStream = useCallback(async (runId: string): Promise<boolean> => {
    manualDisconnectRef.current = false;
    clearReconnectTimer();

    if (connectionRef.current) {
      connectionRef.current.close();
      connectionRef.current = null;
    }

    try {
      const connection = await connectFactoryStream(runId, {
        onOpen: () => {
          reconnectAttemptsRef.current = 0;
          setIsStreaming(true);
        },
        onStatus: (run) => {
          latestRunIdRef.current = run.run_id;
          setCurrentRun(run);
          // Update cache
          queryClient.setQueryData<FactoryRunStatus>(factoryRunKey(run.run_id), run);
        },
        onEvent: (event) => {
          setEvents((previous) => [...previous, event].slice(-200));
        },
        onDone: (run) => {
          setCurrentRun(run);
          setIsStreaming(false);
          if (connectionRef.current) {
            connectionRef.current.close();
            connectionRef.current = null;
          }
          // Update cache
          queryClient.setQueryData<FactoryRunStatus>(factoryRunKey(run.run_id), run);
          // Invalidate runs list
          queryClient.invalidateQueries({ queryKey: factoryRunsKey });

          const status = String(run.status || '').trim().toLowerCase();
          if (status === 'completed') {
            toast.success('Factory Run 完成');
          } else if (status === 'failed') {
            toast.error(run.failure?.detail || 'Factory Run 失败');
          } else if (status === 'cancelled') {
            toast.success('Factory Run 已取消');
          }
        },
        onError: (payload) => {
          const detail = String(payload.detail || payload.error || payload.message || '').trim();
          if (detail) {
            toast.error(detail);
          }
        },
        onConnectionError: () => {
          if (manualDisconnectRef.current) {
            return;
          }

          if (connectionRef.current) {
            connectionRef.current.close();
            connectionRef.current = null;
          }
          setIsStreaming(false);

          const reconnectRunId = latestRunIdRef.current;
          if (!reconnectRunId) {
            return;
          }

          void (async () => {
            const snapshot = await fetchRunStatus(reconnectRunId);
            if (!snapshot || isTerminalRun(snapshot)) {
              return;
            }

            if (reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
              toast.error('Factory 实时流重连失败');
              return;
            }

            reconnectAttemptsRef.current += 1;
            reconnectTimerRef.current = window.setTimeout(() => {
              reconnectTimerRef.current = null;
              void connectStream(reconnectRunId);
            }, RECONNECT_DELAY_MS * reconnectAttemptsRef.current);
          })();
        },
      });

      connectionRef.current = connection;
      return true;
    } catch (streamError) {
      const message = streamError instanceof Error ? streamError.message : '连接Factory实时流失败';
      toast.error(message);
      setIsStreaming(false);
      return false;
    }
  }, [clearReconnectTimer, fetchRunStatus, queryClient, factoryRunsKey, factoryRunKey]);

  const startRun = useCallback(async (opts: FactoryStartOptions): Promise<FactoryRunStatus | null> => {
    setEvents([]);

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
    setCurrentRun(latest);
    setEvents([]);

    if (latest) {
      latestRunIdRef.current = latest.run_id;
      if (!isTerminalRun(latest)) {
        const connected = await connectStream(latest.run_id);
        if (!connected) {
          await fetchRunStatus(latest.run_id);
        }
      }
    }

    return latest;
  }, [autoResumeLatest, connectStream, fetchRunStatus, fetchRuns, workspace]);

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
    setCurrentRun(null);
    setEvents([]);
    // Note: Don't reset queryClient here to preserve cache

    if (!workspace) {
      return;
    }

    void resumeLatestRun();
  }, [workspace, disconnectStream, resumeLatestRun]);

  return {
    currentRun,
    events,
    isLoading: startRunMutation.isPending || stopRunMutation.isPending,
    error: (startRunMutation.error || stopRunMutation.error) as Error | null,
    isStreaming,
    startRun,
    stopRun,
    fetchRunStatus,
    fetchRuns,
    resumeLatestRun,
    connectEventStream: connectStream,
    disconnectStream,
  };
}
