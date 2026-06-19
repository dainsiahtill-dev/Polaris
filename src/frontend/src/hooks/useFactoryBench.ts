/**
 * useFactoryBench — React hook for the Factory panel to observe L1-L8 bench
 * progress in real time.
 *
 * Transport: the platform's unified WebSocket + NAT JetStream pipeline
 * (the same one the runtime event subsystem uses for log.llm / log.process
 * / etc.). We subscribe to ``event.bench`` via the existing
 * ``RuntimeTransportProvider``; the bench subprocess publishes to NAT
 * JetStream subject ``hp.runtime.bench.<session_id>`` and the WebSocket's
 * JetStream consumer forwards every envelope to subscribers.
 *
 * There is one realtime path here: the wildcard ``event.bench`` channel is
 * the live source, while HTTP is used only for explicit snapshot hydration.
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
  if (typeof payload.channel === 'string' && payload.channel.startsWith('event.bench')) return true;
  if (typeof payload.kind === 'string' && payload.kind.startsWith('factory_bench.')) return true;
  return false;
}

function readString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function readStringArray(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.map((item) => String(item || '').trim()).filter(Boolean);
}

function readObject(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function sessionIdFromEnvelope(
  envelope: Record<string, unknown>,
  payload: Record<string, unknown>,
): string | null {
  const direct = readString(payload.session_id) || readString(payload.sessionId) || readString(envelope.run_id);
  if (direct) return direct;
  const channel = readString(envelope.channel);
  if (channel?.startsWith('event.bench:')) {
    return channel.slice('event.bench:'.length).trim() || null;
  }
  return null;
}

function benchEventFromEnvelope(
  envelope: Record<string, unknown>,
): FactoryBenchEvent | null {
  if (!isBenchEnvelope(envelope)) return null;
  const payload = readObject(envelope.payload);
  if (!payload) return null;
  const sessionId = sessionIdFromEnvelope(envelope, payload);
  if (!sessionId) return null;
  const meta = readObject(payload.meta) || {};
  return {
    seq: readNumber(payload.seq) ?? readNumber(envelope.cursor) ?? undefined,
    type: String(payload.type || envelope.kind || 'bench.event'),
    name: readString(payload.name),
    actor: readString(payload.actor),
    summary: readString(payload.summary),
    ok: typeof payload.ok === 'boolean' ? payload.ok : null,
    meta,
    ts: readString(payload.ts) || readString(envelope.ts) || undefined,
    session_id: sessionId,
  };
}

function mergeSessionSummary(
  existing: FactoryBenchSessionSummary | undefined,
  event: FactoryBenchEvent,
): FactoryBenchSessionSummary {
  const meta = event.meta || {};
  const projectIds = readStringArray(meta.project_ids) || existing?.project_ids || [];
  const terminalStatus = terminalStatusFromBenchEvent(event.type, meta);
  const status = terminalStatus || readString(meta.status) || existing?.status || 'running';
  const eventTs = event.ts || new Date().toISOString();
  const metadata = readObject(meta.metadata) || existing?.metadata || {};
  return {
    session_id: event.session_id || existing?.session_id || '',
    work_dir: readString(meta.work_dir) || existing?.work_dir || '',
    project_ids: projectIds,
    total: readNumber(meta.total) ?? existing?.total ?? projectIds.length,
    completed: readNumber(meta.completed) ?? existing?.completed ?? 0,
    failed: readNumber(meta.failed) ?? existing?.failed ?? 0,
    status,
    created_at: readString(meta.created_at) || existing?.created_at || eventTs,
    updated_at: readString(meta.updated_at) || eventTs,
    completed_at: readString(meta.completed_at) || existing?.completed_at,
    metadata,
  };
}

export function useFactoryBench(
  options: UseFactoryBenchOptions = {},
): UseFactoryBenchResult {
  const { autoSelect = 'newest' } = options;
  const [sessions, setSessions] = useState<FactoryBenchSessionSummary[]>([]);
  const [currentSession, setCurrentSession] = useState<FactoryBenchSessionDetail | null>(null);
  const [events, setEvents] = useState<FactoryBenchEvent[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedSessionRef = useRef<string | null>(null);
  const currentSessionRef = useRef<FactoryBenchSessionDetail | null>(null);
  const autoSelectRef = useRef(autoSelect);

  const { subscribeChannels, registerMessageHandler } = useRuntimeTransport();

  useEffect(() => {
    currentSessionRef.current = currentSession;
  }, [currentSession]);

  useEffect(() => {
    autoSelectRef.current = autoSelect;
  }, [autoSelect]);

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

  const disconnect = useCallback(() => {
    selectedSessionRef.current = null;
    setIsStreaming(false);
  }, []);

  const disconnectRef = useRef(disconnect);

  useEffect(() => {
    disconnectRef.current = disconnect;
  }, [disconnect]);

  const loadSessionDetail = useCallback(
    async (sessionId: string, resetBeforeLoad: boolean) => {
      selectedSessionRef.current = sessionId;
      if (resetBeforeLoad) {
        setCurrentSession(null);
        setEvents([]);
      }

      // Initial fetch via the standard apiGet path (with Authorization header).
      const detailResult = await getBenchSession(sessionId);
      if (selectedSessionRef.current !== sessionId) return;
      if (detailResult.ok && detailResult.data) {
        const detail = detailResult.data;
        setCurrentSession(detail);
        setEvents((prev) => {
          const detailEvents = (detail.events || []).slice(-MAX_EVENTS);
          if (resetBeforeLoad || detailEvents.length > 0) return detailEvents;
          return prev;
        });
        setIsStreaming(!isTerminalStatus(detail.status));
      } else if (!detailResult.ok) {
        setError(detailResult.error || '加载Factory bench session失败');
        setIsStreaming(false);
        return;
      }
    },
    [],
  );

  const select = useCallback(
    async (sessionId: string) => {
      await loadSessionDetail(sessionId, true);
    },
    [loadSessionDetail],
  );

  const selectRef = useRef(select);

  useEffect(() => {
    selectRef.current = select;
  }, [select]);

  useEffect(() => {
    const unsubscribe = subscribeChannels([{ channel: 'event.bench', tailLines: 0 }]);
    const unregister = registerMessageHandler((message: unknown) => {
      if (!message || typeof message !== 'object') return;
      const m = message as Record<string, unknown>;
      const envelope = (m.event && typeof m.event === 'object'
        ? (m.event as Record<string, unknown>)
        : m);
      const event = benchEventFromEnvelope(envelope);
      if (!event || !event.session_id) return;

      let shouldAutoSelect = false;
      setSessions((prev) => {
        const existing = prev.find((session) => session.session_id === event.session_id);
        const merged = mergeSessionSummary(existing, event);
        const withoutCurrent = prev.filter((session) => session.session_id !== event.session_id);
        return [merged, ...withoutCurrent];
      });

      const selectedSessionId = selectedSessionRef.current;
      if (autoSelectRef.current === 'newest' && selectedSessionId !== event.session_id) {
        const currentStatus = currentSessionRef.current?.status;
        shouldAutoSelect = !selectedSessionId || isTerminalStatus(currentStatus);
      }

      if (selectedSessionId === event.session_id) {
        setEvents((prev) => {
          const next = prev.length >= MAX_EVENTS ? prev.slice(1) : prev.slice();
          next.push(event);
          return next;
        });
        const terminalStatus = terminalStatusFromBenchEvent(event.type, event.meta || {});
        setCurrentSession((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            ...mergeSessionSummary(prev, event),
            events: prev.events,
            events_path: prev.events_path,
          };
        });
        if (terminalStatus) {
          setIsStreaming(false);
        }
      }

      if (shouldAutoSelect) {
        const liveSummary = mergeSessionSummary(undefined, event);
        const liveDetail: FactoryBenchSessionDetail = {
          ...liveSummary,
          events_path: '',
          events: [event],
        };
        selectedSessionRef.current = event.session_id;
        currentSessionRef.current = liveDetail;
        setCurrentSession(liveDetail);
        setEvents([event]);
        setIsStreaming(!isTerminalStatus(liveSummary.status));
        void loadSessionDetail(event.session_id, false);
      }
    });

    void refresh();
    return () => {
      unregister();
      unsubscribe();
      disconnectRef.current();
    };
  }, [loadSessionDetail, refresh, registerMessageHandler, subscribeChannels]);

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
