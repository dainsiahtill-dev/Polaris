/**
 * WebSocket Hook
 *
 * 统一的 WebSocket 连接管理，支持自动重连。
 * Nat-JetStream runtime 只允许 WebSocket 实时链路；旧兼容字段
 * 仅保留配置形状，不再执行额外刷新循环。
 *
 * Features:
 * - 指数退避重连策略
 * - 频道订阅管理
 * - 完整的连接状态追踪
 *
 * Architecture Note:
 * - 使用 Nat-JetStream WebSocket 获取实时数据
 * - 避免与 RuntimeTransportProvider 重复建设第二套实时链路
 */

import { useEffect, useRef, useState, useCallback, useMemo } from 'react';

// ============================================================================
// Types
// ============================================================================

/** 连接状态枚举 */
export type ConnectionState = 'connected' | 'connecting' | 'disconnected' | 'fallback';

/** WebSocket 消息类型 */
export interface WebSocketMessage {
  type: string;
  channel?: string;
  [key: string]: unknown;
}

/** 频道订阅配置 */
export interface ChannelSubscription {
  channel: string;
  tailLines?: number;
}

/** Hook 配置选项 */
export interface UseWebSocketWithFallbackOptions {
  /** WebSocket URL */
  url?: string;
  /** 要订阅的频道列表 */
  channels?: ChannelSubscription[];
  /** 消息接收回调 */
  onMessage?: (message: WebSocketMessage) => void;
  /** 连接成功回调 */
  onConnect?: () => void;
  /** 连接关闭回调 */
  onDisconnect?: () => void;
  /** 错误回调 */
  onError?: (error: Error) => void;
  /** Legacy compatibility callback; WebSocket-only mode never starts fallback. */
  onFallbackStart?: () => void;
  /** Legacy compatibility callback; kept for existing call sites. */
  onFallbackEnd?: () => void;

  /** Legacy compatibility option; ignored in WebSocket-only mode. */
  fallbackEndpoint?: string;
  /** Legacy compatibility option; ignored in WebSocket-only mode. */
  fallbackInterval?: number;
  /** Legacy compatibility option; ignored in WebSocket-only mode. */
  maxFallbackAttempts?: number;

  /** 最大重连次数，默认无限 */
  maxRetries?: number;
  /** 重连基础延迟 (ms)，默认 1000 */
  baseDelay?: number;
  /** 最大重连延迟 (ms)，默认 30000 */
  maxDelay?: number;

  /** 是否自动连接，默认 true */
  autoConnect?: boolean;
}

/** Hook 返回值 */
export interface UseWebSocketWithFallbackReturn {
  /** 当前连接状态 */
  connectionState: ConnectionState;
  /** 是否已连接 WebSocket */
  isConnected: boolean;
  /** 是否使用 WebSocket */
  isWebSocketConnected: boolean;
  /** 兼容旧接口；Nat-JetStream WebSocket-only 模式下始终为 false */
  isFallbackActive: boolean;
  /** 当前重连尝试次数 */
  reconnectAttempt: number;
  /** 兼容旧接口；Nat-JetStream WebSocket-only 模式下始终为 0 */
  fallbackAttempt: number;
  /** 错误信息 */
  error: string | null;

  /** 订阅频道 */
  subscribe: (channels: ChannelSubscription[]) => void;
  /** 取消订阅频道 */
  unsubscribe: (channels: string[]) => void;
  /** 发送消息 */
  send: (message: unknown) => boolean;
  /** 主动断开连接 */
  disconnect: () => void;
  /** 主动重连 */
  reconnect: () => void;
}

// ============================================================================
// Constants
// ============================================================================

const DEFAULT_BASE_DELAY = 1000;
const DEFAULT_MAX_DELAY = 30000;

// ============================================================================
// Helper Functions
// ============================================================================

/** 计算指数退避延迟 */
function calculateBackoffDelay(
  attempt: number,
  baseDelay: number,
  maxDelay: number
): number {
  const exponentialDelay = baseDelay * 2 ** attempt;
  const jitter = Math.random() * 500; // 添加随机抖动避免雷群效应
  return Math.min(exponentialDelay + jitter, maxDelay);
}

// ============================================================================
// Hook Implementation
// ============================================================================

export function useWebSocketWithFallback(
  options: UseWebSocketWithFallbackOptions = {}
): UseWebSocketWithFallbackReturn {
  const {
    url,
    channels: initialChannels = [],
    onMessage,
    onConnect,
    onDisconnect,
    onError,
    onFallbackEnd,
    maxRetries = Infinity,
    baseDelay = DEFAULT_BASE_DELAY,
    maxDelay = DEFAULT_MAX_DELAY,
    autoConnect = true,
  } = options;

  // State
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [fallbackAttempt, setFallbackAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Refs for WebSocket and timers
  const wsRef = useRef<WebSocket | null>(null);
  const connectWebSocketRef = useRef<() => void>(() => {});
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const retryConfigRef = useRef({ maxRetries, baseDelay, maxDelay });

  // Refs for tracking state without causing re-renders
  const isManualCloseRef = useRef(false);
  const subscribedChannelsRef = useRef<ChannelSubscription[]>(initialChannels);

  // ============================================================================
  // Cleanup Functions
  // ============================================================================

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const resetLegacyFallbackState = useCallback(() => {
    setFallbackAttempt(0);
  }, []);

  const closeWebSocket = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const markRealtimeUnavailable = useCallback(() => {
    resetLegacyFallbackState();
    setConnectionState('disconnected');
    setError('WebSocket reconnect exhausted; Nat-JetStream WebSocket is required');
  }, [resetLegacyFallbackState]);

  const stopFallbackPolling = useCallback(() => {
    resetLegacyFallbackState();
    onFallbackEnd?.();
  }, [resetLegacyFallbackState, onFallbackEnd]);

  // ============================================================================
  // Reconnection with Exponential Backoff
  // ============================================================================

  const scheduleReconnect = useCallback(() => {
    if (isManualCloseRef.current) return;

    const currentAttempt = reconnectAttemptRef.current;
    const config = retryConfigRef.current;
    if (currentAttempt >= config.maxRetries) {
      markRealtimeUnavailable();
      return;
    }

    const delay = calculateBackoffDelay(currentAttempt, config.baseDelay, config.maxDelay);

    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      const nextAttempt = reconnectAttemptRef.current + 1;
      reconnectAttemptRef.current = nextAttempt;
      setReconnectAttempt(nextAttempt);
      connectWebSocketRef.current();
    }, delay);
  }, [markRealtimeUnavailable]);

  // ============================================================================
  // WebSocket Connection
  // ============================================================================

  const connectWebSocket = useCallback(() => {
    if (!url) return;

    // Don't connect if already connected
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    setConnectionState('connecting');
    setError(null);

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnectionState('connected');
        reconnectAttemptRef.current = 0;
        setReconnectAttempt(0);
        isManualCloseRef.current = false;
        onConnect?.();

        // Resubscribe to channels
        if (subscribedChannelsRef.current.length > 0) {
          ws.send(
            JSON.stringify({
              type: 'SUBSCRIBE',
              channels: subscribedChannelsRef.current,
            })
          );
        }
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as WebSocketMessage;
          onMessage?.(data);
        } catch {
          // Non-JSON message
          onMessage?.({ type: 'raw', data: event.data });
        }
      };

      ws.onerror = () => {
        const errorMessage = 'WebSocket error occurred';
        setError(errorMessage);
        onError?.(new Error(errorMessage));
      };

      ws.onclose = () => {
        wsRef.current = null;

        if (!isManualCloseRef.current) {
          setConnectionState('disconnected');
          onDisconnect?.();

          // Schedule reconnection with exponential backoff
          scheduleReconnect();
        }
      };

      ws.binaryType = 'arraybuffer';
    } catch (err) {
      setConnectionState('disconnected');
      const errorMessage = err instanceof Error ? err.message : 'Failed to connect';
      setError(errorMessage);
      onError?.(new Error(errorMessage));

    }
  }, [
    url,
    onMessage,
    onConnect,
    onDisconnect,
    onError,
    scheduleReconnect,
  ]);
  connectWebSocketRef.current = connectWebSocket;

  // ============================================================================
  // Public API
  // ============================================================================

  const subscribe = useCallback((newChannels: ChannelSubscription[]) => {
    subscribedChannelsRef.current = [
      ...subscribedChannelsRef.current,
      ...newChannels.filter(
        (nc) => !subscribedChannelsRef.current.some((ec) => ec.channel === nc.channel)
      ),
    ];

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'SUBSCRIBE',
          channels: newChannels,
        })
      );
    }
  }, []);

  const unsubscribe = useCallback((channelNames: string[]) => {
    subscribedChannelsRef.current = subscribedChannelsRef.current.filter(
      (c) => !channelNames.includes(c.channel)
    );

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'UNSUBSCRIBE',
          channels: channelNames,
        })
      );
    }
  }, []);

  const send = useCallback((message: unknown): boolean => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      const data = typeof message === 'string' ? message : JSON.stringify(message);
      wsRef.current.send(data);
      return true;
    }
    return false;
  }, []);

  const disconnect = useCallback(() => {
    isManualCloseRef.current = true;
    clearReconnectTimer();
    resetLegacyFallbackState();
    closeWebSocket();
    setConnectionState('disconnected');
  }, [clearReconnectTimer, resetLegacyFallbackState, closeWebSocket]);

  const reconnect = useCallback(() => {
    isManualCloseRef.current = false;
    stopFallbackPolling();
    clearReconnectTimer();
    closeWebSocket();
    reconnectAttemptRef.current = 0;
    setReconnectAttempt(0);
    connectWebSocket();
  }, [
    connectWebSocket,
    closeWebSocket,
    clearReconnectTimer,
    stopFallbackPolling,
  ]);

  // ============================================================================
  // Lifecycle
  // ============================================================================

  // Auto-connect on mount
  useEffect(() => {
    if (autoConnect && url) {
      isManualCloseRef.current = false;
      connectWebSocket();
    }

    return () => {
      disconnect();
    };
  }, [url, autoConnect, disconnect]);

  // Sync initial channels
  useEffect(() => {
    subscribedChannelsRef.current = initialChannels;
  }, [initialChannels]);

  // Sync retry settings for socket callbacks created by older renders.
  useEffect(() => {
    retryConfigRef.current = { maxRetries, baseDelay, maxDelay };
  }, [maxRetries, baseDelay, maxDelay]);

  // ============================================================================
  // Derived State
  // ============================================================================

  const isWebSocketConnected = connectionState === 'connected';
  const isFallbackActive = false;
  const isConnected = connectionState === 'connected';

  // ============================================================================
  // Return Value
  // ============================================================================

  return useMemo(
    () => ({
      connectionState,
      isConnected,
      isWebSocketConnected,
      isFallbackActive,
      reconnectAttempt,
      fallbackAttempt,
      error,
      subscribe,
      unsubscribe,
      send,
      disconnect,
      reconnect,
    }),
    [
      connectionState,
      isConnected,
      isWebSocketConnected,
      isFallbackActive,
      reconnectAttempt,
      fallbackAttempt,
      error,
      subscribe,
      unsubscribe,
      send,
      disconnect,
      reconnect,
    ]
  );
}

// ============================================================================
// Specialized Hooks
// ============================================================================

/** Court 状态 WebSocket Hook，legacy fallback options are ignored. */
export function useCourtWebSocketWithFallback(
  options: {
    fallbackEndpoint?: string;
    fallbackInterval?: number;
    onCourtStateChange?: (state: Record<string, unknown>) => void;
  } = {}
): UseWebSocketWithFallbackReturn & { courtState: Record<string, unknown> | null } {
  const { onCourtStateChange } = options;
  const [courtState, setCourtState] = useState<Record<string, unknown> | null>(null);

  const handleMessage = useCallback(
    (message: WebSocketMessage) => {
      if (
        (message.type === 'status' || message.type === 'court_status') &&
        message.court_state
      ) {
        const state = message.court_state as Record<string, unknown>;
        setCourtState(state);
        onCourtStateChange?.(state);
      }
    },
    [onCourtStateChange]
  );

  const ws = useWebSocketWithFallback({
    channels: [{ channel: 'status' }],
    onMessage: handleMessage,
  });

  return {
    ...ws,
    courtState,
  };
}

/** Runtime 日志 WebSocket Hook，legacy fallback options are ignored. */
export function useRuntimeWebSocketWithFallback(
  options: {
    channels?: string[];
    tailLines?: number;
    fallbackEndpoint?: string;
    fallbackInterval?: number;
    onLogMessage?: (message: WebSocketMessage) => void;
  } = {}
): UseWebSocketWithFallbackReturn & { sendCommand: (command: string) => boolean } {
  const {
    channels = ['runtime'],
    tailLines = 100,
    onLogMessage,
  } = options;

  const channelSubscriptions: ChannelSubscription[] = channels.map((channel) => ({
    channel,
    tailLines,
  }));

  const ws = useWebSocketWithFallback({
    channels: channelSubscriptions,
    onMessage: onLogMessage,
  });

  const sendCommand = useCallback(
    (command: string): boolean => {
      return ws.send({ type: 'command', command });
    },
    [ws.send]
  );

  return {
    ...ws,
    sendCommand,
  };
}
