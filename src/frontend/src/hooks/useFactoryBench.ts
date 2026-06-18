/**
 * useFactoryBench — React hook for the Factory panel to observe L1-L8 bench
 * progress in real time.
 *
 * Transport: the platform's unified WebSocket + NAT JetStream pipeline
 * (the same one the runtime event subsystem uses for log.llm / log.process
 * / etc.). We subscribe to ``event.bench:<session_id>`` via the existing
 * ``RuntimeTransportProvider``; the bench subprocess publishes to NAT
 * JetStream subject ``hp.runtime.bench.<session_id>`` and the WebSocket's
 * JetStream consumer forwards every envelope to subscribers.
 *
 * There is one realtime path here: adding a new ``event.bench:<id>`` channel
 * in the WebSocket's subject builder is the only plumbing change needed on
 * the backend.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  listBenchSessions,
  getBenchSession,
  type FactoryBenchEvent,
  type FactoryBenchSessionDetail,
  type FactoryBenchSessionSummary,
} from '@/services/benchService';
import { useRuntimeTransport } from '@/runtime/transport';

export interface UseFactoryBenchOptions {
  /** List-poll cadence. Default 4s. */
  pollIntervalMs?: number;
  autoSelect?: 'newest' | 'none';
}

export interface UseFactoryBenchResult {
  sessions: FactoryBenchSessionSummary[];
  currentSession: FactoryBenchSessionDetail | null;
  events: FactoryBenchEvent[];
  isStreaming: boolean;
  isLoading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  select: (sessionId: string) => Promise<void>;
  disconnect: () => void;
}

const MAX_EVENTS = 240;

function isTerminalStatus(status: string | undefined | null): boolean {
  return status === 'completed' || status === 'failed' || status === 'cancelled';
}

function terminalStatusFromBenchEvent(eventType: string, meta: Record<string, unknown>): string | null {
  const explicitStatus = typeof meta.status === 'string' ? meta.status.trim() : '';
  if (isTerminalStatus(explicitStatus)) return explicitStatus;
  if (eventType === 'factory_bench.run.completed') return 'completed';
  if (eventType === 'factory_bench.run.failed') return 'failed';
  if (eventType === 'factory_bench.run.cancelled') return 'cancelled';
  return null;
}

function isBenchEnvelope(payload: Record<string, unknown>): boolean {
  if (!payload) return false;
  if (payload.channel === 'event.bench') return true;
  if (typeof payload.kind === 'string' && payload.kind.startsWith('factory_bench.')) return true;
  return false;
}

export function useFactoryBench(
  options: UseFactoryBenchOptions = {},
): UseFactoryBenchResult {
  const { pollIntervalMs = 4000, autoSelect = 'newest' } = options;
  const [sessions, setSessions] = useState<FactoryBenchSessionSummary[]>([]);
  const [currentSession, setCurrentSession] = useState<FactoryBenchSessionDetail | null>(null);
  const [events, setEvents] = useState<FactoryBenchEvent[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedSessionRef = useRef<string | null>(null);
  const subscribedChannelRef = useRef<string | null>(null);
  const unsubscribeChannelRef = useRef<(() => void) | null>(null);
  const registeredHandlerRef = useRef<(() => void) | null>(null);

  const { subscribeChannels, registerMessageHandler } = useRuntimeTransport();

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const result = await listBenchSessions(20);
    if (result.ok && result.data) {
      setSessions(result.data);
      setCurrentSession((prev) => {
        if (!prev) return prev;
        const refreshed = result.data?.find((session) => session.session_id === prev.session_id);
        if (!refreshed) return prev;
        if (isTerminalStatus(refreshed.status)) {
          setIsStreaming(false);
        }
        return { ...prev, ...refreshed };
      });
    } else {
      setError(result.error || '加载Factory bench sessions失败');
    }
    setIsLoading(false);
  }, []);

  const cleanupRealtimeSubscription = useCallback(() => {
    if (unsubscribeChannelRef.current) {
      try {
        unsubscribeChannelRef.current();
      } catch {
        // ignore — best-effort teardown
      }
      unsubscribeChannelRef.current = null;
    }
    subscribedChannelRef.current = null;

    if (registeredHandlerRef.current) {
      try {
        registeredHandlerRef.current();
      } catch {
        // ignore — best-effort teardown
      }
      registeredHandlerRef.current = null;
    }
  }, []);

  const disconnect = useCallback(() => {
    selectedSessionRef.current = null;
    cleanupRealtimeSubscription();
    setIsStreaming(false);
  }, [cleanupRealtimeSubscription]);

  const disconnectRef = useRef(disconnect);

  useEffect(() => {
    disconnectRef.current = disconnect;
  }, [disconnect]);

  const select = useCallback(
    async (sessionId: string) => {
      disconnect();
      setCurrentSession(null);
      setEvents([]);
      selectedSessionRef.current = sessionId;

      // Initial fetch via the standard apiGet path (with Authorization header).
      const detailResult = await getBenchSession(sessionId);
      if (detailResult.ok && detailResult.data) {
        const detail = detailResult.data;
        setCurrentSession(detail);
        setEvents((detail.events || []).slice(-MAX_EVENTS));
      } else if (!detailResult.ok) {
        setError(detailResult.error || '加载Factory bench session失败');
        return;
      }

      // Subscribe to the bench channel via the unified WS transport. The
      // WebSocket's JetStream consumer subscribes to the corresponding
      // subject ``hp.runtime.bench.<session_id>`` and forwards every
      // envelope to the registered handler. This is the same pipeline
      // log.llm / log.process / event.file_edit / etc. use.
      const channel = `event.bench:${sessionId}`;
      const subscriptions = [{ channel, tailLines: 0 }];
      const unsubscribe = subscribeChannels(subscriptions);
      subscribedChannelRef.current = channel;
      unsubscribeChannelRef.current = unsubscribe;

      const handler = (message: unknown) => {
        if (!message || typeof message !== 'object') return;
        const m = message as Record<string, unknown>;
        // The runtime.v2 protocol delivers an ``EVENT`` message with
        // ``event`` (envelope) inside.
        const envelope = (m.event && typeof m.event === 'object'
          ? (m.event as Record<string, unknown>)
          : m);
        if (!isBenchEnvelope(envelope)) return;
        const payload = (envelope.payload && typeof envelope.payload === 'object'
          ? (envelope.payload as Record<string, unknown>)
          : null);
        if (!payload) return;
        const event: FactoryBenchEvent = {
          seq: Number(envelope.cursor ?? 0) || undefined,
          type: String(payload.type || envelope.kind || 'bench.event'),
          name: typeof payload.name === 'string' ? payload.name : null,
          actor: typeof payload.actor === 'string' ? payload.actor : null,
          summary: typeof payload.summary === 'string' ? payload.summary : null,
          ok: typeof payload.ok === 'boolean' ? payload.ok : null,
          meta: (payload.meta && typeof payload.meta === 'object'
            ? (payload.meta as Record<string, unknown>)
            : {}),
          ts: typeof envelope.ts === 'string' ? envelope.ts : undefined,
          session_id: sessionId,
        };
        setEvents((prev) => {
          const next = prev.length >= MAX_EVENTS ? prev.slice(1) : prev.slice();
          next.push(event);
          return next;
        });
        // Reflect counter / status updates from the bench service JSONL into
        // the currentSession snapshot. The /state endpoint still serves
        // counters, but the session detail already includes the most recent
        // counter values from the initial fetch; subsequent counter changes
        // also arrive via the same payload.meta.completed / failed fields.
        const terminalStatus = terminalStatusFromBenchEvent(event.type, event.meta || {});
        if (
          event.meta
          && (
            event.meta.completed !== undefined
            || event.meta.failed !== undefined
            || event.meta.status !== undefined
            || event.meta.completed_at !== undefined
            || terminalStatus
          )
        ) {
          setCurrentSession((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              completed: Number(event.meta?.completed ?? prev.completed),
              failed: Number(event.meta?.failed ?? prev.failed),
              status: terminalStatus ?? prev.status,
              completed_at: typeof event.meta?.completed_at === 'string' ? event.meta.completed_at : prev.completed_at,
              updated_at: typeof event.ts === 'string' ? event.ts : prev.updated_at,
            };
          });
        }
        if (terminalStatus) {
          setIsStreaming(false);
        }
      };
      const unregisterHandler = registerMessageHandler(handler);
      registeredHandlerRef.current = unregisterHandler;

      setIsStreaming(true);
    },
    [disconnect, subscribeChannels, registerMessageHandler],
  );

  useEffect(() => {
    void refresh();
    const id = setInterval(() => {
      void refresh();
    }, pollIntervalMs);
    return () => {
      clearInterval(id);
      disconnectRef.current();
    };
  }, [refresh, pollIntervalMs]);

  useEffect(() => {
    if (autoSelect !== 'newest') return;
    const newest = sessions[0];
    if (!newest) return;
    if (
      isTerminalStatus(newest.status) &&
      currentSession?.session_id === newest.session_id
    )
      return;
    if (currentSession && currentSession.session_id === newest.session_id) return;
    void select(newest.session_id);
  }, [sessions, autoSelect, currentSession]);

  return useMemo(
    () => ({
      sessions,
      currentSession,
      events,
      isStreaming,
      isLoading,
      error,
      refresh,
      select,
      disconnect,
    }),
    [sessions, currentSession, events, isStreaming, isLoading, error, refresh, select, disconnect],
  );
}
