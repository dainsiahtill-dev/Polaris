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
  type FactoryBenchControlPlaneProjection,
  type FactoryBenchSessionDetail,
  type FactoryBenchSessionSummary,
} from '@/services/benchService';
import { useMessageHandler, useTransportActions } from '@/runtime/transport';

export interface UseFactoryBenchOptions {
  autoSelect?: 'newest' | 'none';
  enabled?: boolean;
  onWorkspaceChange?: (workspace: string, event: FactoryBenchEvent) => void;
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

function joinBenchWorkspace(workDir: string | null, projectId: string | null): string | null {
  if (!workDir || !projectId) return null;
  return `${workDir.replace(/[\\/]+$/, '')}/${projectId.replace(/^[\\/]+/, '')}`;
}

function singleProjectId(value: unknown): string | null {
  const projectIds = readStringArray(value);
  return projectIds && projectIds.length === 1 ? projectIds[0] : null;
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

function workspaceFromBenchEvent(event: FactoryBenchEvent): string | null {
  const meta = event.meta || {};
  if (event.type.startsWith('factory_bench.project.')) {
    return (
      readString(meta.workspace) ||
      readString(meta.workspace_path) ||
      readString(meta.project_workspace) ||
      readString(meta.projectWorkspace) ||
      joinBenchWorkspace(readString(meta.work_dir), readString(meta.project_id) || readString(meta.projectId))
    );
  }
  if (event.type === 'factory_bench.session.started') {
    return joinBenchWorkspace(readString(meta.work_dir), singleProjectId(meta.project_ids));
  }
  if (event.type === 'factory_bench.session.workspace') {
    return readString(meta.workspace);
  }
  return null;
}

function workspaceFromBenchSession(session: FactoryBenchSessionDetail): string | null {
  return joinBenchWorkspace(readString(session.work_dir), session.project_ids.length === 1 ? session.project_ids[0] : null);
}

function sessionWorkspaceEvent(session: FactoryBenchSessionDetail, workspace: string): FactoryBenchEvent {
  return {
    type: 'factory_bench.session.workspace',
    summary: `Factory bench workspace observed: ${workspace}`,
    meta: {
      session_id: session.session_id,
      work_dir: session.work_dir,
      project_id: session.project_ids[0] || '',
      workspace,
    },
    session_id: session.session_id,
    ts: session.updated_at,
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
  const controlPlaneProjection =
    (readObject(meta.control_plane_projection) as FactoryBenchControlPlaneProjection | null)
    || existing?.control_plane_projection;
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
    control_plane_projection: controlPlaneProjection,
  };
}

function benchEventKey(event: FactoryBenchEvent): string {
  return [
    event.seq ?? '',
    event.ts ?? '',
    event.session_id ?? '',
    event.type ?? '',
    event.summary ?? '',
    event.name ?? '',
  ].join('|');
}

function mergeBenchEvents(
  hydratedEvents: FactoryBenchEvent[],
  liveEvents: FactoryBenchEvent[],
): FactoryBenchEvent[] {
  const merged: FactoryBenchEvent[] = [];
  const seen = new Set<string>();
  for (const event of [...hydratedEvents, ...liveEvents]) {
    const key = benchEventKey(event);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(event);
  }
  return merged.slice(-MAX_EVENTS);
}

export function useFactoryBench(
  options: UseFactoryBenchOptions = {},
): UseFactoryBenchResult {
  const { autoSelect = 'newest', enabled = true, onWorkspaceChange } = options;
  const [sessions, setSessions] = useState<FactoryBenchSessionSummary[]>([]);
  const [currentSession, setCurrentSession] = useState<FactoryBenchSessionDetail | null>(null);
  const [events, setEvents] = useState<FactoryBenchEvent[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedSessionRef = useRef<string | null>(null);
  const currentSessionRef = useRef<FactoryBenchSessionDetail | null>(null);
  const sessionsRef = useRef<FactoryBenchSessionSummary[]>([]);
  const autoSelectRef = useRef(autoSelect);
  const onWorkspaceChangeRef = useRef(onWorkspaceChange);
  const loadingSessionRef = useRef<string | null>(null);
  const manualSelectionRef = useRef(false);

  const { subscribeChannels } = useTransportActions();
  const { registerMessageHandler } = useMessageHandler();

  useEffect(() => {
    currentSessionRef.current = currentSession;
  }, [currentSession]);

  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  useEffect(() => {
    autoSelectRef.current = autoSelect;
  }, [autoSelect]);

  useEffect(() => {
    onWorkspaceChangeRef.current = onWorkspaceChange;
  }, [onWorkspaceChange]);

  const notifyWorkspaceChange = useCallback((event: FactoryBenchEvent) => {
    const workspace = workspaceFromBenchEvent(event);
    if (!workspace) return;
    onWorkspaceChangeRef.current?.(workspace, event);
  }, []);

  const refresh = useCallback(async () => {
    if (!enabled) {
      setSessions([]);
      setCurrentSession(null);
      setEvents([]);
      setIsStreaming(false);
      setIsLoading(false);
      setError(null);
      return;
    }
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
  }, [enabled]);

  const disconnect = useCallback(() => {
    selectedSessionRef.current = null;
    manualSelectionRef.current = false;
    setIsStreaming(false);
  }, []);

  const disconnectRef = useRef(disconnect);

  useEffect(() => {
    disconnectRef.current = disconnect;
  }, [disconnect]);

  const loadSessionDetail = useCallback(
    async (sessionId: string, resetBeforeLoad: boolean, manualSelection = false) => {
      if (!enabled) return;
      if (loadingSessionRef.current === sessionId) return;
      if (!resetBeforeLoad && currentSessionRef.current?.session_id === sessionId) return;
      loadingSessionRef.current = sessionId;
      selectedSessionRef.current = sessionId;
      manualSelectionRef.current = manualSelection;
      if (resetBeforeLoad) {
        setCurrentSession(null);
        setEvents([]);
      }

      try {
        // Initial fetch via the standard apiGet path (with Authorization header).
        const detailResult = await getBenchSession(sessionId);
        if (selectedSessionRef.current !== sessionId) return;
        if (detailResult.ok && detailResult.data) {
          const detail = detailResult.data;
          const detailEvents = (detail.events || []).slice(-MAX_EVENTS);
          const latestWorkspaceEvent = [...detailEvents].reverse().find((item) => workspaceFromBenchEvent(item));
          if (latestWorkspaceEvent) {
            notifyWorkspaceChange(latestWorkspaceEvent);
          } else {
            const sessionWorkspace = workspaceFromBenchSession(detail);
            if (sessionWorkspace) {
              notifyWorkspaceChange(sessionWorkspaceEvent(detail, sessionWorkspace));
            }
          }
          setCurrentSession(detail);
          setEvents((prev) => {
            if (resetBeforeLoad || detailEvents.length > 0) return mergeBenchEvents(detailEvents, prev);
            return prev;
          });
          setIsStreaming(!isTerminalStatus(detail.status));
        } else if (!detailResult.ok) {
          setError(detailResult.error || '加载Factory bench session失败');
          setIsStreaming(false);
          return;
        }
      } finally {
        if (loadingSessionRef.current === sessionId) {
          loadingSessionRef.current = null;
        }
      }
    },
    [enabled, notifyWorkspaceChange],
  );

  const select = useCallback(
    async (sessionId: string) => {
      await loadSessionDetail(sessionId, true, true);
    },
    [loadSessionDetail],
  );

  const selectRef = useRef(select);

  useEffect(() => {
    selectRef.current = select;
  }, [select]);

  useEffect(() => {
    if (!enabled) {
      setSessions([]);
      setCurrentSession(null);
      setEvents([]);
      setIsStreaming(false);
      setIsLoading(false);
      setError(null);
      disconnectRef.current();
      return;
    }
    const unsubscribe = subscribeChannels([{ channel: 'event.bench', tailLines: 0 }]);
    const unregister = registerMessageHandler((message: unknown) => {
      if (!message || typeof message !== 'object') return;
      const m = message as Record<string, unknown>;
      const envelope = (m.event && typeof m.event === 'object'
        ? (m.event as Record<string, unknown>)
        : m);
      const event = benchEventFromEnvelope(envelope);
      if (!event || !event.session_id) return;
      notifyWorkspaceChange(event);

      let shouldAutoSelect = false;
      setSessions((prev) => {
        const existing = prev.find((session) => session.session_id === event.session_id);
        const merged = mergeSessionSummary(existing, event);
        const withoutCurrent = prev.filter((session) => session.session_id !== event.session_id);
        const next = [merged, ...withoutCurrent];
        sessionsRef.current = next;
        return next;
      });

      const selectedSessionId = selectedSessionRef.current;
      const currentSessionId = currentSessionRef.current?.session_id;
      const newestSessionId = sessionsRef.current[0]?.session_id;
      const isActiveSession =
        selectedSessionId === event.session_id ||
        currentSessionId === event.session_id ||
        newestSessionId === event.session_id;
      if (autoSelectRef.current === 'newest' && selectedSessionId !== event.session_id) {
        const currentStatus = currentSessionRef.current?.status;
        shouldAutoSelect = !selectedSessionId || !manualSelectionRef.current || isTerminalStatus(currentStatus);
      }

      if (isActiveSession) {
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
          manualSelectionRef.current = false;
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
        manualSelectionRef.current = false;
        currentSessionRef.current = liveDetail;
        setCurrentSession(liveDetail);
        setEvents([event]);
        setIsStreaming(!isTerminalStatus(liveSummary.status));
      }
    });

    void refresh();
    return () => {
      unregister();
      unsubscribe();
      disconnectRef.current();
    };
  }, [enabled, notifyWorkspaceChange, refresh, registerMessageHandler, subscribeChannels]);

  useEffect(() => {
    if (!enabled) return;
    if (autoSelect !== 'newest') return;
    const newest = sessions[0];
    if (!newest) return;
    if (
      isTerminalStatus(newest.status) &&
      currentSession?.session_id === newest.session_id
    )
      return;
    if (selectedSessionRef.current === newest.session_id) return;
    if (currentSession && manualSelectionRef.current && !isTerminalStatus(currentSession.status)) return;
    if (currentSession && currentSession.session_id === newest.session_id) return;
    void loadSessionDetail(newest.session_id, true, false);
  }, [enabled, sessions, autoSelect, currentSession, loadSessionDetail]);

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
