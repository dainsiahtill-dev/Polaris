/**
 * Unified WebSocket Hook
 *
 * 提供统一的WebSocket连接管理，支持自动重连和频道订阅
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { connectWebSocket } from '@/api';

// ============================================================================
// Types
// ============================================================================

export interface WebSocketMessage {
  type: string;
  [key: string]: unknown;
}

export interface WebSocketOptions {
  /** 自动订阅的频道 */
  channels?: string[];
  /** 历史消息行数 */
  tailLines?: number;
  /** 最大重连次数，默认无限 */
  maxRetries?: number;
  /** 重连基础延迟(ms)，默认1000 */
  baseDelay?: number;
  /** 最大重连延迟(ms)，默认30000 */
  maxDelay?: number;
  /** 连接成功回调 */
  onOpen?: () => void;
  /** 消息接收回调 */
  onMessage?: (message: WebSocketMessage) => void;
  /** 连接关闭回调 */
  onClose?: () => void;
  /** 重连回调 */
  onReconnecting?: (attempt: number) => void;
  /** 重连耗尽回调 */
  onFailed?: () => void;
  /** 连接错误回调 */
  onError?: (error: Event) => void;
}

export interface WebSocketState {
  connected: boolean;
  connecting: boolean;
  reconnectAttempt: number;
  error: string | null;
}

export interface UseWebSocketReturn {
  state: WebSocketState;
  send: (message: unknown) => boolean;
  subscribe: (channels: string[], tailLines?: number) => void;
  close: () => void;
  reconnect: () => void;
}

// ============================================================================
// Hook Implementation
// ============================================================================

export function useWebSocket(options: WebSocketOptions = {}): UseWebSocketReturn {
  const {
    channels = [],
    tailLines = 0,
    maxRetries = Infinity,
    baseDelay = 1000,
    maxDelay = 30000,
    onOpen,
    onMessage,
    onClose,
    onReconnecting,
    onFailed,
    onError,
  } = options;

  const [state, setState] = useState<WebSocketState>({
    connected: false,
    connecting: false,
    reconnectAttempt: 0,
    error: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isManualCloseRef = useRef(false);
  const attemptRef = useRef(0);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const subscribe = useCallback((chs: string[], lines = 0) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({ type: 'subscribe', channels: chs, tail_lines: lines })
      );
    }
  }, []);

  const send = useCallback((message: unknown): boolean => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof message === 'string' ? message : JSON.stringify(message));
      return true;
    }
    return false;
  }, []);

  const close = useCallback(() => {
    isManualCloseRef.current = true;
    clearReconnectTimer();
    wsRef.current?.close();
    wsRef.current = null;
    setState(prev => ({
      ...prev,
      connected: false,
      connecting: false,
    }));
  }, [clearReconnectTimer]);

  const scheduleReconnect = useCallback(() => {
    if (isManualCloseRef.current) return;
    if (attemptRef.current >= maxRetries) {
      onFailed?.();
      return;
    }

    attemptRef.current++;
    onReconnecting?.(attemptRef.current);

    const jitter = Math.random() * 500;
    const delay = Math.min(baseDelay * 2 ** (attemptRef.current - 1), maxDelay) + jitter;

    setState(prev => ({
      ...prev,
      reconnectAttempt: attemptRef.current,
      connecting: true,
    }));

    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      connect();
    }, delay);
  }, [maxRetries, baseDelay, maxDelay, onReconnecting, onFailed]);

  const connect = useCallback(async () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    setState(prev => ({ ...prev, connecting: true, error: null }));

    try {
      const socket = await connectWebSocket(attemptRef.current > 0);

      socket.onopen = () => {
        attemptRef.current = 0;
        setState({
          connected: true,
          connecting: false,
          reconnectAttempt: 0,
          error: null,
        });

        // Subscribe to channels
        if (channels.length > 0) {
          subscribe(channels, tailLines);
        }

        onOpen?.();
      };

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as WebSocketMessage;
          onMessage?.(message);
        } catch {
          // Handle non-JSON messages
          onMessage?.({ type: 'raw', data: event.data });
        }
      };

      socket.onclose = () => {
        setState(prev => ({
          ...prev,
          connected: false,
          connecting: false,
        }));
        onClose?.();

        if (!isManualCloseRef.current) {
          scheduleReconnect();
        }
      };

      socket.onerror = (error) => {
        setState(prev => ({
          ...prev,
          error: 'WebSocket error occurred',
        }));
        onError?.(error);
        socket.close();
      };

      wsRef.current = socket;
    } catch (error) {
      setState(prev => ({
        ...prev,
        connecting: false,
        error: error instanceof Error ? error.message : 'Failed to connect',
      }));
      scheduleReconnect();
    }
  }, [channels, tailLines, onOpen, onMessage, onClose, onError, subscribe, scheduleReconnect]);

  const reconnect = useCallback(() => {
    isManualCloseRef.current = false;
    attemptRef.current = 0;
    close();
    connect();
  }, [close, connect]);

  // Connect on mount
  useEffect(() => {
    isManualCloseRef.current = false;
    connect();

    return () => {
      close();
    };
  }, []);

  return {
    state,
    send,
    subscribe,
    close,
    reconnect,
  };
}

// ============================================================================
// Specialized Hooks
// ============================================================================

/**
 * Court状态WebSocket Hook
 */
export function useCourtWebSocket(
  onStateChange?: (state: Record<string, unknown>) => void
): WebSocketState & { reconnect: () => void } {
  const handleMessage = useCallback((message: WebSocketMessage) => {
    if ((message.type === 'status' || message.type === 'court_status') && message.court_state) {
      onStateChange?.(message.court_state as Record<string, unknown>);
    }
  }, [onStateChange]);

  const { state, reconnect } = useWebSocket({
    channels: ['status'],
    onMessage: handleMessage,
  });

  return { ...state, reconnect };
}

/**
 * Runtime日志WebSocket Hook
 */
export function useRuntimeWebSocket(
  options: {
    channels?: string[];
    tailLines?: number;
    onLogMessage?: (message: WebSocketMessage) => void;
  } = {}
): WebSocketState & { sendCommand: (command: string) => boolean } {
  const { channels = ['runtime'], tailLines = 100, onLogMessage } = options;

  const { state, send } = useWebSocket({
    channels,
    tailLines,
    onMessage: onLogMessage,
  });

  const sendCommand = useCallback((command: string): boolean => {
    return send({ type: 'command', command });
  }, [send]);

  return { ...state, sendCommand };
}
