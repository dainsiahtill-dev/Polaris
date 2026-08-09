/**
 * 宫廷投影系统 API Hooks
 *
 * 提供宫廷拓扑、状态查询等功能的 React Hooks
 * 使用统一的 RuntimeTransportProvider 进行 WebSocket 通信
 *
 * Architecture Note (Nats-JetStream Runtime Transport):
 * - 使用 RuntimeTransportProvider split contexts 替代直接 connectWebSocket
 * - 共享全局 WebSocket 连接，避免多连问题
 */
import { useState, useEffect, useCallback } from 'react';
import { getCourtTopology, getCourtState, getActorDetail, getSceneConfig, getRoleMapping, } from '@/services';
import { useConnectionState, useMessageHandler, useTransportActions } from '@/runtime/transport';
// ============================================================================
// Court Topology
// ============================================================================
export function useCourtTopology() {
    const [topology, setTopology] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
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
                    }
                    else {
                        setError(new Error(result.error || 'Failed to fetch court topology'));
                    }
                }
            }
            catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err : new Error(String(err)));
                }
            }
            finally {
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
// Court State (shared WebSocket via RuntimeTransportProvider only)
// ============================================================================
export function useCourtState(options = {}) {
    const { enabled = true } = options;
    const [state, setState] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const { connected: transportConnected } = useConnectionState();
    const { subscribeChannels, sendCommand } = useTransportActions();
    const { registerMessageHandler } = useMessageHandler();
    const fetchState = useCallback(async () => {
        try {
            const result = await getCourtState();
            if (result.ok && result.data) {
                setState(result.data);
                setError(null);
            }
            else {
                setError(new Error(result.error || 'Failed to fetch court state'));
            }
        }
        catch (err) {
            setError(err instanceof Error ? err : new Error(String(err)));
        }
        finally {
            setLoading(false);
        }
    }, []);
    // Subscribe to status channel via transport
    useEffect(() => {
        if (!enabled)
            return undefined;
        const unsubscribe = subscribeChannels([{ channel: 'status' }]);
        return () => unsubscribe();
    }, [enabled, subscribeChannels]);
    // Register message handler for court state updates
    useEffect(() => {
        if (!enabled)
            return undefined;
        const unregister = registerMessageHandler((message) => {
            try {
                const msg = message;
                if ((msg.type === 'status' || msg.type === 'court_status') &&
                    msg.court_state) {
                    setState(msg.court_state);
                    setLoading(false);
                }
            }
            catch {
                // 忽略解析错误
            }
        });
        return () => unregister();
    }, [enabled, registerMessageHandler]);
    // Send STATUS command when connected
    useEffect(() => {
        if (enabled && transportConnected) {
            sendCommand({ type: 'STATUS' });
        }
    }, [enabled, transportConnected, sendCommand]);
    // Initial snapshot only; all subsequent updates arrive through runtime.v2.
    useEffect(() => {
        void fetchState();
    }, [fetchState]);
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
export function useActorDetail(roleId) {
    const [actor, setActor] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
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
                    }
                    else {
                        setError(new Error(result.error || 'Failed to fetch actor detail'));
                    }
                }
            }
            catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err : new Error(String(err)));
                }
            }
            finally {
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
export function useSceneConfig(sceneId) {
    const [config, setConfig] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
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
                    }
                    else {
                        setError(new Error(result.error || 'Failed to fetch scene config'));
                    }
                }
            }
            catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err : new Error(String(err)));
                }
            }
            finally {
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
    const [mapping, setMapping] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
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
                    }
                    else {
                        setError(new Error(result.error || 'Failed to fetch role mapping'));
                    }
                }
            }
            catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err : new Error(String(err)));
                }
            }
            finally {
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
    const [state, setState] = useState(null);
    const { connected } = useConnectionState();
    const { subscribeChannels } = useTransportActions();
    const { registerMessageHandler } = useMessageHandler();
    // Subscribe to status channel
    useEffect(() => {
        const unsubscribe = subscribeChannels([{ channel: 'status' }]);
        return () => unsubscribe();
    }, [subscribeChannels]);
    // Register message handler
    useEffect(() => {
        const unregister = registerMessageHandler((message) => {
            try {
                const msg = message;
                if (msg.type === 'status' && msg.court_state) {
                    setState(msg.court_state);
                }
            }
            catch {
                // 忽略解析错误
            }
        });
        return () => unregister();
    }, [registerMessageHandler]);
    return { state, connected };
}
