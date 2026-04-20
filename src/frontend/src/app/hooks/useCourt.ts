/**
 * 宫廷投影系统 API Hooks
 *
 * 提供宫廷拓扑、状态查询等功能的 React Hooks
 * 使用统一的 RuntimeTransportProvider 进行 WebSocket 通信
 *
 * Architecture Note (WS/SSE Alignment):
 * - 使用 useRuntimeTransport 替代直接 connectWebSocket
 * - 共享全局 WebSocket 连接，避免多连问题
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getCourtTopology,
  getCourtState,
  getActorDetail,
  getSceneConfig,
  getRoleMapping,
} from '@/services';
import { useRuntimeTransport } from '@/runtime/transport';
import type {
  CourtState,
  CourtTopologyResponse,
  CourtActorState,
  CourtSceneConfig,
  CourtMappingResponse,
} from '@/services';

export type {
  CourtState,
  CourtTopologyResponse,
  CourtActorState,
  CourtSceneConfig,
} from '@/services';

// ============================================================================
// Court Topology
// ============================================================================

export function useCourtTopology() {
  const [topology, setTopology] = useState<CourtTopologyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;

    const fetchTopology = async () => {
      try {
        setLoading(true);
        const result = await getCourtTopology();

        if (!cancelled) {
          if (result.ok && result.data) {
            setTopology(result.data);
            setError(null);
          } else {
            setError(new Error(result.error || 'Failed to fetch court topology'));
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    fetchTopology();

    return () => {
      cancelled = true;
    };
  }, []);

  return { topology, loading, error };
}

// ============================================================================
// Court State (with shared WebSocket via RuntimeTransportProvider + polling fallback)
// ============================================================================

export function useCourtState(pollInterval = 3000) {
  const [state, setState] = useState<CourtState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const {
    connected: transportConnected,
    subscribeChannels,
    registerMessageHandler,
    sendCommand,
  } = useRuntimeTransport();

  const fetchState = useCallback(async () => {
    try {
      const result = await getCourtState();

      if (result.ok && result.data) {
        setState(result.data);
        setError(null);
      } else {
        setError(new Error(result.error || 'Failed to fetch court state'));
      }
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setLoading(false);
    }
  }, []);

  // Subscribe to status channel via transport
  useEffect(() => {
    const unsubscribe = subscribeChannels([{ channel: 'status' }]);
    return () => unsubscribe();
  }, [subscribeChannels]);

  // Register message handler for court state updates
  useEffect(() => {
    const unregister = registerMessageHandler((message) => {
      try {
        const msg = message as Record<string, unknown>;
        if (
          (msg.type === 'status' || msg.type === 'court_status') &&
          msg.court_state
        ) {
          setState(msg.court_state as CourtState);
          setLoading(false);
        }
      } catch {
        // 忽略解析错误
      }
    });
    return () => unregister();
  }, [registerMessageHandler]);

  // Send STATUS command when connected
  useEffect(() => {
    if (transportConnected) {
      sendCommand({ type: 'STATUS' });
    }
  }, [transportConnected, sendCommand]);

  // Polling fallback when transport is not connected
  useEffect(() => {
    // 首先获取初始数据
    fetchState();

    // 如果 transport 未连接，启动轮询作为降级
    if (!transportConnected) {
      pollIntervalRef.current = setInterval(fetchState, pollInterval);
    }

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, [fetchState, pollInterval, transportConnected]);

  return {
    state,
    loading,
    error,
    isWebSocketConnected: transportConnected,
    refetch: fetchState,
  };
}

// ============================================================================
// Actor Detail
// ============================================================================

export function useActorDetail(roleId: string | null) {
  const [actor, setActor] = useState<CourtActorState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;

    if (!roleId) {
      setActor(null);
      return;
    }

    const fetchActor = async () => {
      try {
        setLoading(true);
        const result = await getActorDetail(roleId);

        if (!cancelled) {
          if (result.ok && result.data) {
            setActor(result.data);
            setError(null);
          } else {
            setError(new Error(result.error || 'Failed to fetch actor detail'));
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    fetchActor();

    return () => {
      cancelled = true;
    };
  }, [roleId]);

  return { actor, loading, error };
}

// ============================================================================
// Scene Config
// ============================================================================

export function useSceneConfig(sceneId: string | null) {
  const [config, setConfig] = useState<CourtSceneConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;

    if (!sceneId) {
      setConfig(null);
      return;
    }

    const fetchConfig = async () => {
      try {
        setLoading(true);
        const result = await getSceneConfig(sceneId);

        if (!cancelled) {
          if (result.ok && result.data) {
            setConfig(result.data);
            setError(null);
          } else {
            setError(new Error(result.error || 'Failed to fetch scene config'));
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    fetchConfig();

    return () => {
      cancelled = true;
    };
  }, [sceneId]);

  return { config, loading, error };
}

// ============================================================================
// Role Mapping
// ============================================================================

export function useRoleMapping() {
  const [mapping, setMapping] = useState<CourtMappingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;

    const fetchMapping = async () => {
      try {
        setLoading(true);
        const result = await getRoleMapping();

        if (!cancelled) {
          if (result.ok && result.data) {
            setMapping(result.data);
            setError(null);
          } else {
            setError(new Error(result.error || 'Failed to fetch role mapping'));
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    fetchMapping();

    return () => {
      cancelled = true;
    };
  }, []);

  return { mapping, loading, error };
}

// ============================================================================
// Court WebSocket (uses shared transport)
// ============================================================================

export function useCourtWebSocket() {
  const [state, setState] = useState<CourtState | null>(null);

  const {
    connected,
    subscribeChannels,
    registerMessageHandler,
    sendCommand,
  } = useRuntimeTransport();

  // Subscribe to status channel
  useEffect(() => {
    const unsubscribe = subscribeChannels([{ channel: 'status' }]);
    return () => unsubscribe();
  }, [subscribeChannels]);

  // Register message handler
  useEffect(() => {
    const unregister = registerMessageHandler((message) => {
      try {
        const msg = message as Record<string, unknown>;
        if (msg.type === 'status' && msg.court_state) {
          setState(msg.court_state as CourtState);
        }
      } catch {
        // 忽略解析错误
      }
    });
    return () => unregister();
  }, [registerMessageHandler]);

  // Send subscribe command when connected
  useEffect(() => {
    if (connected) {
      sendCommand({ type: 'subscribe', channels: ['status'] });
    }
  }, [connected, sendCommand]);

  return { state, connected };
}
