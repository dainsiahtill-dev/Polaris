/**
 * useConnectionNotifications - 连接状态通知 Hook
 *
 * 职责:
 * - 监听 WebSocket 连接状态变化
 * - 在状态切换时显示 Toast 通知用户
 * - 支持重连、恢复连接等场景
 *
 * Features:
 * - 连接恢复通知
 * - 断开连接警告
 * - 可选的重连按钮
 */
import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
const RUNTIME_DISCONNECTED_TOAST_DELAY_MS = 4000;
// ============================================================================
// Default Notification Messages
// ============================================================================
const DEFAULT_NOTIFICATIONS = {
    restored: {
        title: '连接已恢复',
        description: '实时更新已恢复',
    },
    disconnected: {
        title: '连接已断开',
        description: '正在尝试重新连接...',
    },
};
// ============================================================================
// Hook Implementation
// ============================================================================
export function useConnectionNotifications(options) {
    const { connectionState, enabled = true, notifications = {}, } = options;
    // Merge with defaults
    const messages = {
        restored: { ...DEFAULT_NOTIFICATIONS.restored, ...notifications.restored },
        disconnected: { ...DEFAULT_NOTIFICATIONS.disconnected, ...notifications.disconnected },
    };
    // Refs for tracking state changes
    const prevStateRef = useRef(connectionState);
    const disconnectedToastIdRef = useRef(null);
    useEffect(() => {
        if (!enabled)
            return;
        const prevState = prevStateRef.current;
        // Skip if state hasn't changed
        if (prevState === connectionState)
            return;
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
        // State Transition: connected/connecting -> disconnected (Lost Connection)
        // =========================================================================
        if (connectionState === 'disconnected' &&
            prevState !== 'disconnected') {
            disconnectedToastIdRef.current = toast.error(messages.disconnected.title, {
                description: messages.disconnected.description,
                duration: 5000,
            });
        }
        // Update previous state
        prevStateRef.current = connectionState;
    }, [connectionState, enabled, messages]);
    // Cleanup toasts on unmount
    useEffect(() => {
        return () => {
            if (disconnectedToastIdRef.current) {
                toast.dismiss(disconnectedToastIdRef.current);
            }
        };
    }, []);
}
/**
 * useRuntimeConnectionNotifications - 针对 useRuntime 连接的通知 Hook
 *
 * 基于 useRuntime 的连接状态 (live, reconnecting) 触发通知
 */
export function useRuntimeConnectionNotifications(options) {
    const { live, reconnecting, enabled = true } = options;
    const prevLiveRef = useRef(live);
    const liveRef = useRef(live);
    const enabledRef = useRef(enabled);
    const disconnectedToastDelayRef = useRef(null);
    const disconnectedToastIdRef = useRef(null);
    const disconnectedToastShownRef = useRef(false);
    useEffect(() => {
        liveRef.current = live;
        enabledRef.current = enabled;
        const clearPendingDisconnectedToast = () => {
            if (disconnectedToastDelayRef.current) {
                clearTimeout(disconnectedToastDelayRef.current);
                disconnectedToastDelayRef.current = null;
            }
        };
        if (!enabled) {
            clearPendingDisconnectedToast();
            if (disconnectedToastIdRef.current) {
                toast.dismiss(disconnectedToastIdRef.current);
                disconnectedToastIdRef.current = null;
            }
            disconnectedToastShownRef.current = false;
            prevLiveRef.current = live;
            return;
        }
        if (live) {
            clearPendingDisconnectedToast();
            if (!prevLiveRef.current && disconnectedToastShownRef.current) {
                if (disconnectedToastIdRef.current) {
                    toast.dismiss(disconnectedToastIdRef.current);
                    disconnectedToastIdRef.current = null;
                }
                disconnectedToastShownRef.current = false;
                toast.success('连接已恢复', {
                    description: '实时更新已恢复',
                    duration: 3000,
                });
            }
            prevLiveRef.current = true;
            return;
        }
        if (prevLiveRef.current && !disconnectedToastDelayRef.current && !disconnectedToastShownRef.current) {
            disconnectedToastDelayRef.current = setTimeout(() => {
                disconnectedToastDelayRef.current = null;
                if (!enabledRef.current || liveRef.current)
                    return;
                disconnectedToastShownRef.current = true;
                disconnectedToastIdRef.current = toast.error('连接已断开', {
                    description: reconnecting ? '正在重新连接...' : '实时更新已暂停',
                    duration: 5000,
                });
            }, RUNTIME_DISCONNECTED_TOAST_DELAY_MS);
        }
        prevLiveRef.current = false;
    }, [live, reconnecting, enabled]);
    // Cleanup
    useEffect(() => {
        return () => {
            if (disconnectedToastDelayRef.current) {
                clearTimeout(disconnectedToastDelayRef.current);
                disconnectedToastDelayRef.current = null;
            }
            if (disconnectedToastIdRef.current) {
                toast.dismiss(disconnectedToastIdRef.current);
            }
        };
    }, []);
}
