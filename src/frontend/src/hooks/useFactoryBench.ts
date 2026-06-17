/**
 * useFactoryBench — React hook for the Factory panel to observe L1-L8 bench
 * progress in real time. Wraps `benchService` and exposes:
 *   - benchSessions: list of recent bench sessions
 *   - currentSession: the selected session (with live status)
 *   - events: live event stream for the selected session
 *   - isStreaming: whether the SSE stream is open
 *   - refresh / select / disconnect actions
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  connectBenchStream,
  listBenchSessions,
  type FactoryBenchEvent,
  type FactoryBenchSessionDetail,
  type FactoryBenchSessionSummary,
  type FactoryBenchStreamConnection,
} from '@/services/benchService';

export interface UseFactoryBenchOptions {
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
  return status === 'completed' || status === 'failed';
}

function asSummary(detail: FactoryBenchSessionDetail | null): FactoryBenchSessionSummary | null {
  if (!detail) return null;
  const { events: _ignored, events_path: _ignored2, ...summary } = detail;
  void _ignored;
  void _ignored2;
  return summary as FactoryBenchSessionSummary;
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
  const connRef = useRef<FactoryBenchStreamConnection | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const disconnect = useCallback(() => {
    if (connRef.current) {
      connRef.current.close();
      connRef.current = null;
    }
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const result = await listBenchSessions(20);
    if (result.ok && result.data) {
      setSessions(result.data);
    } else {
      setError(result.error || '加载Factory bench sessions失败');
    }
    setIsLoading(false);
  }, []);

  const select = useCallback(
    async (sessionId: string) => {
      disconnect();
      setCurrentSession(null);
      setEvents([]);
      const result = await fetch(`/v2/factory/bench/sessions/${encodeURIComponent(sessionId)}`, {
        credentials: 'include',
      })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);
      if (result) {
        setCurrentSession(result as FactoryBenchSessionDetail);
        setEvents(((result as FactoryBenchSessionDetail).events || []).slice(-MAX_EVENTS));
      }
      try {
        const conn = await connectBenchStream(sessionId, {
          onOpen: () => setIsStreaming(true),
          onEvent: (event) => {
            setEvents((prev) => {
              const next = prev.length >= MAX_EVENTS ? prev.slice(1) : prev.slice();
              next.push(event);
              return next;
            });
          },
          onStatus: (session) => {
            setCurrentSession((prev) => {
              if (!prev) return prev;
              return { ...prev, ...session };
            });
          },
          onDone: (session) => {
            setCurrentSession((prev) => {
              if (!prev) return prev;
              return { ...prev, ...session };
            });
            setIsStreaming(false);
          },
          onError: () => {
            setIsStreaming(false);
          },
          onConnectionError: () => {
            setIsStreaming(false);
          },
        });
        connRef.current = conn;
      } catch (streamError) {
        const message = streamError instanceof Error ? streamError.message : '连接Factory bench实时流失败';
        setError(message);
        setIsStreaming(false);
      }
    },
    [disconnect],
  );

  useEffect(() => {
    void refresh();
    pollRef.current = setInterval(() => {
      void refresh();
    }, pollIntervalMs);
    return () => {
      disconnect();
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [refresh, pollIntervalMs, disconnect]);

  useEffect(() => {
    if (autoSelect !== 'newest') return;
    const newest = sessions[0];
    if (!newest) return;
    if (currentSession && currentSession.session_id === newest.session_id) return;
    if (isTerminalStatus(newest.status) && currentSession?.session_id === newest.session_id) return;
    void select(newest.session_id);
  }, [sessions, autoSelect, currentSession, select]);

  return {
    sessions,
    currentSession,
    events,
    isStreaming,
    isLoading,
    error,
    refresh,
    select,
    disconnect,
  };
}

export { asSummary };
