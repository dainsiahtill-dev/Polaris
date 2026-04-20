/**
 * useConnectionNotifications - 连接状态通知 Hook
 *
 * 职责:
 * - 监听 WebSocket 连接状态变化
 * - 在状态切换时显示 Toast 通知用户
 * - 支持降级模式、重连、恢复连接等场景
 *
 * Features:
 * - 连接恢复通知
 * - 降级模式进入通知
 * - 断开连接警告
 * - 可选的重连按钮
 */

import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import type { ConnectionState } from './useWebSocketWithFallback';

// ============================================================================
// Types
// ============================================================================

export interface UseConnectionNotificationsOptions {
  /** 当前连接状态 */
  connectionState: ConnectionState;
  /** 重连回调 */
  reconnect?: () => void;
  /** 是否启用通知，默认 true */
  enabled?: boolean;
  /** 自定义通知配置 */
  notifications?: {
    /** 连接恢复消息 */
    restored?: { title: string; description: string };
    /** 降级模式消息 */
    fallback?: { title: string; description: string };
    /** 断开连接消息 */
    disconnected?: { title: string; description: string };
  };
}

// ============================================================================
// Default Notification Messages
// ============================================================================

const DEFAULT_NOTIFICATIONS = {
  restored: {
    title: '连接已恢复',
    description: '实时更新已恢复',
  },
  fallback: {
    title: '网络降级模式',
    description: '实时更新已暂停，将自动重连',
  },
  disconnected: {
    title: '连接已断开',
    description: '正在尝试重新连接...',
  },
} as const;

// ============================================================================
// Hook Implementation
// ============================================================================

export function useConnectionNotifications(
  options: UseConnectionNotificationsOptions
): void {
  const {
    connectionState,
    reconnect,
    enabled = true,
    notifications = {},
  } = options;

  // Merge with defaults
  const messages = {
    restored: { ...DEFAULT_NOTIFICATIONS.restored, ...notifications.restored },
    fallback: { ...DEFAULT_NOTIFICATIONS.fallback, ...notifications.fallback },
    disconnected: { ...DEFAULT_NOTIFICATIONS.disconnected, ...notifications.disconnected },
  };

  // Refs for tracking state changes
  const prevStateRef = useRef<ConnectionState>(connectionState);
  const fallbackToastIdRef = useRef<string | number | null>(null);
  const disconnectedToastIdRef = useRef<string | number | null>(null);

  useEffect(() => {
    if (!enabled) return;

    const prevState = prevStateRef.current;

    // Skip if state hasn't changed
    if (prevState === connectionState) return;

    // =========================================================================
    // State Transition: disconnected -> connected (Recovery)
    // =========================================================================
    if (prevState === 'disconnected' && connectionState === 'connected') {
      // Dismiss any existing disconnected warning
      if (disconnectedToastIdRef.current) {
        toast.dismiss(disconnectedToastIdRef.current);
        disconnectedToastIdRef.current = null;
      }

      toast.success(messages.restored.title, {
        description: messages.restored.description,
        duration: 3000,
      });
    }

    // =========================================================================
    // State Transition: any -> fallback (Entering Fallback Mode)
    // =========================================================================
    if (connectionState === 'fallback') {
      // Dismiss disconnected toast if exists
      if (disconnectedToastIdRef.current) {
        toast.dismiss(disconnectedToastIdRef.current);
        disconnectedToastIdRef.current = null;
      }

      fallbackToastIdRef.current = toast.warning(messages.fallback.title, {
        description: messages.fallback.description,
        duration: 10000,
        action: reconnect
          ? {
              label: '重连',
              onClick: () => reconnect(),
            }
          : undefined,
      });
    }

    // =========================================================================
    // State Transition: connected/fallback -> disconnected (Lost Connection)
    // =========================================================================
    if (
      connectionState === 'disconnected' &&
      prevState !== 'disconnected'
    ) {
      // Dismiss fallback toast if exists
      if (fallbackToastIdRef.current) {
        toast.dismiss(fallbackToastIdRef.current);
        fallbackToastIdRef.current = null;
      }

      disconnectedToastIdRef.current = toast.error(messages.disconnected.title, {
        description: messages.disconnected.description,
        duration: 5000,
      });
    }

    // =========================================================================
    // State Transition: fallback -> connected (Fallback Recovered)
    // =========================================================================
    if (prevState === 'fallback' && connectionState === 'connected') {
      // Dismiss fallback toast
      if (fallbackToastIdRef.current) {
        toast.dismiss(fallbackToastIdRef.current);
        fallbackToastIdRef.current = null;
      }

      toast.success(messages.restored.title, {
        description: messages.restored.description,
        duration: 3000,
      });
    }

    // Update previous state
    prevStateRef.current = connectionState;
  }, [connectionState, enabled, messages, reconnect]);

  // Cleanup toasts on unmount
  useEffect(() => {
    return () => {
      if (fallbackToastIdRef.current) {
        toast.dismiss(fallbackToastIdRef.current);
      }
      if (disconnectedToastIdRef.current) {
        toast.dismiss(disconnectedToastIdRef.current);
      }
    };
  }, []);
}

// ============================================================================
// Specialized Hook for useRuntime Connection
// ============================================================================

export interface UseRuntimeConnectionNotificationOptions {
  /** 连接是否活跃 */
  live: boolean;
  /** 是否正在重连 */
  reconnecting: boolean;
  /** 重连回调 */
  reconnect?: () => void;
  /** 是否启用通知 */
  enabled?: boolean;
}

/**
 * useRuntimeConnectionNotifications - 针对 useRuntime 连接的通知 Hook
 *
 * 基于 useRuntime 的连接状态 (live, reconnecting) 触发通知
 */
export function useRuntimeConnectionNotifications(
  options: UseRuntimeConnectionNotificationOptions
): void {
  const { live, reconnecting, reconnect, enabled = true } = options;

  // Track previous live state
  const prevLiveRef = useRef<boolean>(live);
  const prevReconnectingRef = useRef<boolean>(reconnecting);
  const disconnectedToastIdRef = useRef<string | number | null>(null);

  useEffect(() => {
    if (!enabled) return;

    // Connection restored
    if (!prevLiveRef.current && live) {
      if (disconnectedToastIdRef.current) {
        toast.dismiss(disconnectedToastIdRef.current);
        disconnectedToastIdRef.current = null;
      }

      toast.success('连接已恢复', {
        description: '实时更新已恢复',
        duration: 3000,
      });
    }

    // Connection lost (not reconnecting yet)
    if (prevLiveRef.current && !live && !reconnecting) {
      disconnectedToastIdRef.current = toast.error('连接已断开', {
        description: '正在尝试重新连接...',
        duration: 5000,
      });
    }

    // Reconnecting started
    if (prevReconnectingRef.current !== reconnecting && reconnecting) {
      toast.warning('正在重连...', {
        description: 'WebSocket 连接中断，尝试恢复中',
        duration: 3000,
      });
    }

    prevLiveRef.current = live;
    prevReconnectingRef.current = reconnecting;
  }, [live, reconnecting, enabled]);

  // Cleanup
  useEffect(() => {
    return () => {
      if (disconnectedToastIdRef.current) {
        toast.dismiss(disconnectedToastIdRef.current);
      }
    };
  }, []);
}
