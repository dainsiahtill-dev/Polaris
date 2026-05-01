/**
 * useRuntimeConnection - 连接状态管理 Hook
 *
 * 职责:
 * - 管理 WebSocket 连接状态
 * - 提供连接控制方法 (connect, disconnect, reconnect)
 * - 订阅角色通道
 */

import { useCallback, useEffect, useRef } from 'react';
import { useRuntimeStore } from './useRuntimeStore';
import { useRuntimeTransport } from '@/runtime/transport';
import { useSettings } from '@/hooks';

interface UseRuntimeConnectionOptions {
  roles?: ('pm' | 'director' | 'qa')[];
  autoConnect?: boolean;
  workspace?: string;
}

function serializeRoles(roles: ('pm' | 'director' | 'qa')[]): string {
  const unique = Array.from(new Set(roles.map((role) => role.trim().toLowerCase())));
  return unique
    .filter((role): role is 'pm' | 'director' | 'qa' => role === 'pm' || role === 'director' || role === 'qa')
    .join(',');
}

/**
 * useRuntimeConnection - 管理运行时连接状态
 *
 * 代理到 useRuntimeTransport，同时同步状态到 store
 */
export function useRuntimeConnection(options: UseRuntimeConnectionOptions = {}) {
  const {
    roles = ['pm', 'director', 'qa'],
    autoConnect = true,
    workspace: workspaceProp,
  } = options;

  const isWorkspaceControlled = workspaceProp !== undefined;
  const { settings, load: loadRuntimeSettings } = useSettings({ autoLoad: !isWorkspaceControlled });
  const workspace = workspaceProp ?? settings?.workspace ?? '';

  // Store state
  const live = useRuntimeStore((s) => s.live);
  const error = useRuntimeStore((s) => s.error);
  const reconnecting = useRuntimeStore((s) => s.reconnecting);
  const attemptCount = useRuntimeStore((s) => s.attemptCount);
  const setConnectionState = useRuntimeStore((s) => s.setConnectionState);
  const resetForWorkspace = useRuntimeStore((s) => s.resetForWorkspace);

  // Transport
  const {
    connected: transportConnected,
    reconnecting: transportReconnecting,
    error: transportError,
    attemptCount: transportAttemptCount,
    subscribeChannels,
    sendCommand,
    reconnect: transportReconnect,
    registerMessageHandler,
  } = useRuntimeTransport();

  // Refs
  const activeRef = useRef(true);
  const rolesRef = useRef<('pm' | 'director' | 'qa')[]>(roles);
  const workspaceRef = useRef<string>(workspace);

  // Sync connection state to store
  useEffect(() => {
    setConnectionState({
      live: transportConnected,
      error: transportError,
      reconnecting: transportReconnecting,
      attemptCount: transportAttemptCount,
    });
  }, [transportConnected, transportError, transportReconnecting, transportAttemptCount, setConnectionState]);

  // Subscribe to channels based on roles
  useEffect(() => {
    const channels = [`roles:${serializeRoles(rolesRef.current)}`];
    const unsubscribe = subscribeChannels(channels.map(channel => ({ channel, tailLines: 100 })));
    return () => {
      unsubscribe();
    };
  }, [subscribeChannels]);

  // Connect action
  const connect = useCallback(
    (forceRefresh = true) => {
      activeRef.current = true;
      if (forceRefresh) {
        transportReconnect();
      }
    },
    [transportReconnect]
  );

  // Disconnect action
  const disconnect = useCallback(() => {
    activeRef.current = false;
  }, []);

  // Reconnect action
  const reconnect = useCallback(() => {
    transportReconnect();
  }, [transportReconnect]);

  // Update subscription
  const updateSubscription = useCallback(
    (nextRoles: ('pm' | 'director' | 'qa')[]) => {
      rolesRef.current = nextRoles;
      sendCommand({ type: 'SUBSCRIBE', protocol: 'runtime.v2', roles: nextRoles });
    },
    [sendCommand]
  );

  // Sync refs with props
  useEffect(() => {
    rolesRef.current = roles;
  }, [roles]);

  // Reset state ONLY on workspace change (not on every connection state flip).
  // The previous version depended on transportConnected/transportReconnecting,
  // which caused an infinite loop: connected=true → effect re-runs → reconnect()
  // → disconnect → reconnect → connected=true → effect re-runs → ...
  const prevWorkspaceRef = useRef<string>(workspace);
  useEffect(() => {
    if (!workspace) return;
    if (workspace === prevWorkspaceRef.current) return;
    prevWorkspaceRef.current = workspace;

    resetForWorkspace();
    if (autoConnect && activeRef.current) {
      transportReconnect();
    }
  }, [workspace]);

  // Initial activation
  useEffect(() => {
    activeRef.current = true;
    return () => {
      activeRef.current = false;
    };
  }, []);

  return {
    // State
    live,
    connected: live,
    isConnected: live,
    error,
    reconnecting,
    attemptCount,

    // Actions
    connect,
    disconnect,
    reconnect,
    updateSubscription,

    // Transport
    transportConnected,
    transportReconnecting,
    transportError,
    transportAttemptCount,
    transportReconnect,
    registerMessageHandler,
    sendCommand,

    // Refs for message handler
    workspaceRef,
    rolesRef,
    activeRef,
  };
}