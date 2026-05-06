/**
 * RuntimeTransportProvider - React Context for Runtime WebSocket
 *
 * Provides connection state and message subscription capabilities
 * to all child components through React Context.
 *
 * SPLIT INTO MULTIPLE CONTEXTS FOR PERFORMANCE:
 * - ConnectionStateContext: connected, reconnecting, error, attemptCount
 * - TransportActionsContext: subscribeChannels, sendCommand, reconnect
 * - MessageHandlerContext: registerMessageHandler
 */

import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useRef,
  useMemo,
} from 'react';
import {
  runtimeSocketManager,
  type ConnectionState,
  type ChannelSubscription,
  type RuntimeRole,
} from './runtimeSocketManager';

// ============================================================================
// Split Context Types
// ============================================================================

/** Connection state - changes frequently during connection lifecycle */
export interface ConnectionStateContextValue {
  connected: boolean;
  reconnecting: boolean;
  error: string | null;
  attemptCount: number;
}

/** Transport actions - stable callbacks */
export interface TransportActionsContextValue {
  subscribeChannels: (subscriptions: ChannelSubscription[], roles?: RuntimeRole[]) => () => void;
  sendCommand: (data: unknown) => boolean;
  getLastCursor: () => number;
  reconnect: () => void;
}

/** Message handler registration - returns cleanup function */
export interface MessageHandlerContextValue {
  registerMessageHandler: (
    handler: (message: unknown) => void,
    channelFilter?: string
  ) => () => void;
}

// ============================================================================
// Context Creation
// ============================================================================

const ConnectionStateContext = createContext<ConnectionStateContextValue | null>(null);
const TransportActionsContext = createContext<TransportActionsContextValue | null>(null);
const MessageHandlerContext = createContext<MessageHandlerContextValue | null>(null);

/** Legacy combined context (for backward compatibility) */
export interface RuntimeTransportContextValue
  extends ConnectionStateContextValue,
    TransportActionsContextValue,
    MessageHandlerContextValue {}

const RuntimeTransportContext = createContext<RuntimeTransportContextValue | null>(null);

// ============================================================================
// Provider Component
// ============================================================================

interface RuntimeTransportProviderProps {
  children: React.ReactNode;
  autoConnect?: boolean;
}

export function RuntimeTransportProvider({
  children,
  autoConnect = true,
}: RuntimeTransportProviderProps): React.ReactElement {
  const [state, setState] = useState<ConnectionState>(
    runtimeSocketManager.getState()
  );

  // Track handler refs for cleanup
  const handlerCounterRef = useRef(0);

  // Subscribe to connection state changes
  useEffect(() => {
    const unsubscribe = runtimeSocketManager.registerStateListener((newState) => {
      setState(newState);
    });

    if (autoConnect) {
      runtimeSocketManager.start();
    }

    return () => {
      unsubscribe();
    };
  }, [autoConnect]);

  // Channel subscription helper
  const subscribeChannels = useCallback(
    (subscriptions: ChannelSubscription[], roles?: RuntimeRole[]): (() => void) => {
      runtimeSocketManager.subscribeChannels(subscriptions, roles);

      const channels = subscriptions.map((s) => s.channel);

      // Return cleanup function
      return () => {
        runtimeSocketManager.unsubscribeChannels(channels);
      };
    },
    []
  );

  // Send command helper
  const sendCommand = useCallback((data: unknown): boolean => {
    return runtimeSocketManager.send(data);
  }, []);

  const getLastCursor = useCallback((): number => {
    return runtimeSocketManager.getLastCursor();
  }, []);

  // Reconnect helper
  const reconnect = useCallback((): void => {
    runtimeSocketManager.reconnect();
  }, []);

  // Register message handler
  const registerMessageHandler = useCallback(
    (handler: (message: unknown) => void, channelFilter?: string): (() => void) => {
      const id = `handler-${++handlerCounterRef.current}`;

      const unregister = runtimeSocketManager.registerMessageListener({
        id,
        channel: channelFilter,
        handler,
      });

      return unregister;
    },
    []
  );

  // Memoized split context values
  const connectionStateValue = useMemo<ConnectionStateContextValue>(
    () => ({
      connected: state.connected,
      reconnecting: state.reconnecting,
      error: state.error,
      attemptCount: state.attemptCount,
    }),
    [state.connected, state.reconnecting, state.error, state.attemptCount]
  );

  const actionsValue = useMemo<TransportActionsContextValue>(
    () => ({
      subscribeChannels,
      sendCommand,
      getLastCursor,
      reconnect,
    }),
    [subscribeChannels, sendCommand, getLastCursor, reconnect]
  );

  const messageHandlerValue = useMemo<MessageHandlerContextValue>(
    () => ({ registerMessageHandler }),
    [registerMessageHandler]
  );

  // Legacy combined value (backward compatibility)
  const legacyValue = useMemo<RuntimeTransportContextValue>(
    () => ({
      ...connectionStateValue,
      ...actionsValue,
      ...messageHandlerValue,
    }),
    [connectionStateValue, actionsValue, messageHandlerValue]
  );

  return (
    <ConnectionStateContext.Provider value={connectionStateValue}>
      <TransportActionsContext.Provider value={actionsValue}>
        <MessageHandlerContext.Provider value={messageHandlerValue}>
          <RuntimeTransportContext.Provider value={legacyValue}>
            {children}
          </RuntimeTransportContext.Provider>
        </MessageHandlerContext.Provider>
      </TransportActionsContext.Provider>
    </ConnectionStateContext.Provider>
  );
}

// ============================================================================
// Hooks for consuming split contexts
// ============================================================================

/** Hook for connection state only - minimizes re-renders */
export function useConnectionState(): ConnectionStateContextValue {
  const context = useContext(ConnectionStateContext);
  if (!context) {
    throw new Error(
      'useConnectionState must be used within a RuntimeTransportProvider'
    );
  }
  return context;
}

/** Hook for transport actions only - stable reference */
export function useTransportActions(): TransportActionsContextValue {
  const context = useContext(TransportActionsContext);
  if (!context) {
    throw new Error(
      'useTransportActions must be used within a RuntimeTransportProvider'
    );
  }
  return context;
}

/** Hook for message handler registration - stable reference */
export function useMessageHandler(): MessageHandlerContextValue {
  const context = useContext(MessageHandlerContext);
  if (!context) {
    throw new Error(
      'useMessageHandler must be used within a RuntimeTransportProvider'
    );
  }
  return context;
}

/** Legacy combined hook (for backward compatibility) */
export function useRuntimeTransport(): RuntimeTransportContextValue {
  const context = useContext(RuntimeTransportContext);
  if (!context) {
    throw new Error(
      'useRuntimeTransport must be used within a RuntimeTransportProvider'
    );
  }
  return context;
}

// ============================================================================
// Convenience hook for channel subscription
// ============================================================================

export interface UseChannelSubscriptionOptions {
  channels: string[];
  tailLines?: number;
  onMessage?: (message: unknown) => void;
}

export function useChannelSubscription({
  channels,
  tailLines = 0,
  onMessage,
}: UseChannelSubscriptionOptions): {
  connected: boolean;
  reconnecting: boolean;
  error: string | null;
} {
  // Use split contexts for optimized re-renders
  const { connected, reconnecting, error } = useConnectionState();
  const { subscribeChannels } = useTransportActions();
  const { registerMessageHandler } = useMessageHandler();

  // Subscribe to channels
  useEffect(() => {
    if (channels.length === 0) return;

    const subscriptions = channels.map((channel) => ({ channel, tailLines }));
    const unsubscribe = subscribeChannels(subscriptions);

    return () => {
      unsubscribe();
    };
  }, [channels, tailLines, subscribeChannels]);

  // Register message handler
  useEffect(() => {
    if (!onMessage) return;

    const unregister = registerMessageHandler(onMessage);
    return () => {
      unregister();
    };
  }, [onMessage, registerMessageHandler]);

  return { connected, reconnecting, error };
}
