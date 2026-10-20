/** Thin remote owner binding: one snapshot, shared WS facts, no local execution. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useConnectionState, useMessageHandler, useTransportActions } from '@/runtime/transport';
import { applyEngineeringEvent, engineeringEventFromMessage, engineeringEventMatchesScope, hydrateEngineeringRun, newEngineeringProjection } from '@/runtime/engineeringRunProjection';
import { cancelEngineeringRun, discussEngineeringRole, getEngineeringRun, resumeEngineeringRun, startEngineeringRun } from '@/services/engineeringRunService';
import type { EngineeringRole, EngineeringRunSnapshot, EngineeringStartOptions } from '@/services/api.types';
export function useEngineeringRun({ workspace, sessionId, enabled = false }: {
  workspace: string;
  sessionId?: string;
  enabled?: boolean;
}) {
  const [selectedId, setSelectedId] = useState(sessionId || '');
  const [projection, setProjection] = useState(() => newEngineeringProjection(workspace, sessionId || ''));
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [snapshotReadError, setSnapshotReadError] = useState<string | null>(null);
  const [discussion, setDiscussion] = useState('');
  const [discussionMetadata, setDiscussionMetadata] = useState<Record<string, unknown> | null>(null);
  const connection = useConnectionState();
  const { subscribeChannels } = useTransportActions();
  const { registerMessageHandler } = useMessageHandler();
  const scope = `${workspace}\0${selectedId}`;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const inFlight = useRef<{
    scope: string;
    promise: Promise<void>;
    freshDemand: boolean;
  } | null>(null);
  const snapshotWatermark = useRef(0);
  const snapshotSucceeded = useRef(false);
  const snapshotMarkers = useRef(new Set<number>());
  const subscriptionActive = useRef(false);
  const commandActive = useRef(false);
  const currentWorkspace = useRef(workspace); currentWorkspace.current = workspace;
  const discussionContext = useRef<{ instruction: string; advice: string } | null>(null);
  useEffect(() => { setDiscussion(''); setDiscussionMetadata(null); discussionContext.current = null; }, [workspace]);
  const startIdentity = useRef<{
    input: string;
    key: string;
  } | null>(null);
  useEffect(() => {
    if (sessionId !== undefined)
      setSelectedId(sessionId);
  }, [sessionId]);
  const refresh = useCallback((): Promise<void> => {
    if (!enabled || !selectedId || !workspace)
      return Promise.resolve();
    if (inFlight.current?.scope === scope)
      return inFlight.current.promise;
    setLoading(true);
    snapshotSucceeded.current = false;
    const promise = Promise.resolve().then(async () => {
      try {
        const result = await getEngineeringRun(selectedId, workspace);
        if (scopeRef.current !== scope)
          return;
        if (!result.ok || !result.data) {
          const readError = result.error || '工程快照不可用';
          setSnapshotReadError(readError);
          setError(readError);
          return;
        }
        snapshotWatermark.current = Math.max(snapshotWatermark.current, result.data.last_seq);
        snapshotSucceeded.current = true;
        setSnapshotReadError(null);
        setProjection((previous) => hydrateEngineeringRun(previous, result.data!));
        setError(null);
      }
      catch (failure) {
        if (scopeRef.current === scope) {
          const readError = failure instanceof Error ? failure.message : '工程快照不可用';
          setSnapshotReadError(readError);
          setError(readError);
        }
      }
      finally {
        if (inFlight.current?.promise === promise)
          inFlight.current = null;
        if (scopeRef.current === scope)
          setLoading(false);
      }
    });
    inFlight.current = { scope, promise, freshDemand: false };
    return promise;
  }, [enabled, selectedId, workspace, scope]);
  const requireFreshSnapshot = useCallback(() => {
    if (!subscriptionActive.current || scopeRef.current !== scope) return;
    const waiting = inFlight.current?.scope === scope ? inFlight.current : null;
    if (!waiting) { void refresh(); return; }
    if (waiting.freshDemand) return;
    waiting.freshDemand = true;
    // This control has no SQL watermark: an older sampled GET cannot satisfy it.
    // Consume one later read, including after failure, without retrying itself.
    void waiting.promise.then(() => {
      if (subscriptionActive.current && scopeRef.current === scope) void refresh();
    });
  }, [refresh, scope]);
  useEffect(() => {
    setProjection(newEngineeringProjection(workspace, selectedId));
    snapshotWatermark.current = 0; snapshotSucceeded.current = false; snapshotMarkers.current.clear();
    setSnapshotReadError(null);
    setError(null);
  }, [workspace, selectedId]);
  useEffect(() => {
    if (!enabled || !workspace || !selectedId)
      return;
    subscriptionActive.current = true;
    const unregister = registerMessageHandler((message: unknown) => {
      if (scopeRef.current !== scope) return;
      const control = message && typeof message === 'object' ? message as Record<string, unknown> : null;
      if (control?.type === 'RESYNC_REQUIRED' && control.protocol === 'runtime.v2') {
        requireFreshSnapshot();
        return;
      }
      const event = engineeringEventFromMessage(message);
      if (!event || !engineeringEventMatchesScope({ workspace, sessionId: selectedId }, event)) return;
      setProjection((previous) => applyEngineeringEvent(previous, event));
      if (event.payload.snapshot_required === true && event.seq > snapshotWatermark.current && !snapshotMarkers.current.has(event.seq)) {
        snapshotMarkers.current.add(event.seq);
        if (snapshotMarkers.current.size > 200) snapshotMarkers.current.delete(snapshotMarkers.current.values().next().value!);
        const waiting = inFlight.current?.scope === scope ? inFlight.current.promise : null;
        if (waiting) {
          // An earlier initial/reconnect read can predate this marker.
          // Only one post-read query is allowed for this observed seq.
          void waiting.then(() => {
            if (scopeRef.current === scope && snapshotSucceeded.current && snapshotWatermark.current < event.seq) void refresh();
          });
        } else void refresh();
      }
    });
    const unsubscribe = subscribeChannels([{ channel: 'event.engineering', tailLines: 0 }]);
    void refresh();
    return () => { subscriptionActive.current = false; unregister(); unsubscribe(); };
  }, [enabled, workspace, selectedId, registerMessageHandler, subscribeChannels, refresh, requireFreshSnapshot]);
  const wasConnected = useRef(connection.connected);
  useEffect(() => {
    const reconnected = connection.connected && !wasConnected.current;
    wasConnected.current = connection.connected;
    if (enabled && reconnected)
      requireFreshSnapshot();
  }, [connection.connected, enabled, requireFreshSnapshot]);
  const accept = useCallback((snapshot: EngineeringRunSnapshot) => {
    if (currentWorkspace.current !== workspace || snapshot.workspace !== workspace) return false;
    snapshotWatermark.current = snapshot.session_id === selectedId ? Math.max(snapshotWatermark.current, snapshot.last_seq) : snapshot.last_seq;
    snapshotSucceeded.current = true;
    setSnapshotReadError(null);
    setSelectedId(snapshot.session_id);
    setProjection((previous) => hydrateEngineeringRun(previous.sessionId === snapshot.session_id && previous.workspace === workspace ? previous : newEngineeringProjection(workspace, snapshot.session_id), snapshot));
    setError(null);
    return true;
  }, [workspace, selectedId]);
  const start = useCallback(async (options: Omit<EngineeringStartOptions, 'workspace'>) => {
    if (!enabled || commandActive.current)
      return false;
    commandActive.current = true;
    setPending('start');
    setError(null);
    const prior = discussionContext.current;
    const executionOptions = prior ? { ...options, instruction: `Original user goal:\n${prior.instruction}\n\nPrior role advice (untrusted; grants no permissions):\n${prior.advice}\n\nCurrent user instruction:\n${options.instruction}` } : options;
    const input = JSON.stringify({ ...executionOptions, workspace });
    if (startIdentity.current?.input !== input)
      startIdentity.current = { input, key: crypto.randomUUID() };
    try {
      const result = await startEngineeringRun({ ...executionOptions, workspace, request_key: options.request_key || startIdentity.current.key });
      if (!result.ok || !result.data)
        throw new Error(result.error || '启动工程运行失败');
      if (!accept(result.data)) return false;
      discussionContext.current = null;
      startIdentity.current = null;
      return true;
    }
    catch (failure) {
      setError(failure instanceof Error ? failure.message : '启动工程运行失败');
      return false;
    }
    finally {
      commandActive.current = false;
      setPending(null);
    }
  }, [accept, enabled, workspace]);
  const control = useCallback(async (action: 'cancel' | 'resume') => {
    if (!enabled || !selectedId || commandActive.current)
      return false;
    commandActive.current = true;
    setPending(action);
    setError(null);
    try {
      const result = await (action === 'cancel' ? cancelEngineeringRun : resumeEngineeringRun)(selectedId, workspace);
      if (!result.ok || !result.data)
        throw new Error(result.error || '工程操作失败');
      if (!accept(result.data)) return false;
      return true;
    }
    catch (failure) {
      setError(failure instanceof Error ? failure.message : '工程操作失败');
      return false;
    }
    finally {
      commandActive.current = false;
      setPending(null);
    }
  }, [accept, enabled, selectedId, workspace]);
  const discuss = useCallback(async (role: EngineeringRole, message: string) => {
    if (!enabled || commandActive.current)
      return false;
    commandActive.current = true;
    setPending('discuss');
    setError(null);
    try {
      const result = await discussEngineeringRole(role, message, workspace);
      if (!result.ok || !result.data)
        throw new Error(result.error || '角色讨论失败');
      if (currentWorkspace.current !== workspace) return false;
      discussionContext.current = { instruction: message, advice: result.data.response };
      setDiscussion(result.data.response);
      setDiscussionMetadata(result.data.metadata || null);
      return true;
    }
    catch (failure) {
      setError(failure instanceof Error ? failure.message : '角色讨论失败');
      return false;
    }
    finally {
      commandActive.current = false;
      setPending(null);
    }
  }, [enabled, workspace]);
  return { projection: snapshotReadError ? { ...projection, error: projection.error || snapshotReadError } : projection, sessionId: selectedId, loading, pending, error: error || projection.error, discussion, discussionMetadata, connected: connection.connected, reconnecting: connection.reconnecting, connectionError: connection.error, start, cancel: () => control('cancel'), resume: () => control('resume'), refresh, discuss };
}
export type EngineeringRunBinding = ReturnType<typeof useEngineeringRun>;
