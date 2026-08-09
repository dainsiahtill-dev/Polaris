import { jsx as _jsx } from "react/jsx-runtime";
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
import { createContext, useContext, useEffect, useState, useCallback, useRef, useMemo, } from 'react';
import { runtimeSocketManager, } from './runtimeSocketManager';
// ============================================================================
// Context Creation
// ============================================================================
const ConnectionStateContext = createContext(null);
const TransportActionsContext = createContext(null);
const MessageHandlerContext = createContext(null);
export function RuntimeTransportProvider({ children, autoConnect = true, }) {
    const [state, setState] = useState(runtimeSocketManager.getState());
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
    const subscribeChannels = useCallback((subscriptions, roles) => {
        runtimeSocketManager.subscribeChannels(subscriptions, roles);
        const channels = subscriptions.map((s) => s.channel);
        // Return cleanup function
        return () => {
            runtimeSocketManager.unsubscribeChannels(channels);
        };
    }, []);
    // Send command helper
    const sendCommand = useCallback((data) => {
        return runtimeSocketManager.send(data);
    }, []);
    const getLastCursor = useCallback(() => {
        return runtimeSocketManager.getLastCursor();
    }, []);
    // Reconnect helper
    const reconnect = useCallback(() => {
        runtimeSocketManager.reconnect();
    }, []);
    // Register message handler
    const registerMessageHandler = useCallback((handler, channelFilter) => {
        const id = `handler-${++handlerCounterRef.current}`;
        const unregister = runtimeSocketManager.registerMessageListener({
            id,
            channel: channelFilter,
            handler,
        });
        return unregister;
    }, []);
    // Memoized split context values
    const connectionStateValue = useMemo(() => ({
        connected: state.connected,
        reconnecting: state.reconnecting,
        error: state.error,
        attemptCount: state.attemptCount,
    }), [state.connected, state.reconnecting, state.error, state.attemptCount]);
    const actionsValue = useMemo(() => ({
        subscribeChannels,
        sendCommand,
        getLastCursor,
        reconnect,
    }), [subscribeChannels, sendCommand, getLastCursor, reconnect]);
    const messageHandlerValue = useMemo(() => ({ registerMessageHandler }), [registerMessageHandler]);
    return (_jsx(ConnectionStateContext.Provider, { value: connectionStateValue, children: _jsx(TransportActionsContext.Provider, { value: actionsValue, children: _jsx(MessageHandlerContext.Provider, { value: messageHandlerValue, children: children }) }) }));
}
// ============================================================================
// Hooks for consuming split contexts
// ============================================================================
/** Hook for connection state only - minimizes re-renders */
export function useConnectionState() {
    const context = useContext(ConnectionStateContext);
    if (!context) {
        throw new Error('useConnectionState must be used within a RuntimeTransportProvider');
    }
    return context;
}
/** Hook for transport actions only - stable reference */
export function useTransportActions() {
    const context = useContext(TransportActionsContext);
    if (!context) {
        throw new Error('useTransportActions must be used within a RuntimeTransportProvider');
    }
    return context;
}
/** Hook for message handler registration - stable reference */
export function useMessageHandler() {
    const context = useContext(MessageHandlerContext);
    if (!context) {
        throw new Error('useMessageHandler must be used within a RuntimeTransportProvider');
    }
    return context;
}
export function useChannelSubscription({ channels, tailLines = 0, onMessage, }) {
    // Use split contexts for optimized re-renders
    const { connected, reconnecting, error } = useConnectionState();
    const { subscribeChannels } = useTransportActions();
    const { registerMessageHandler } = useMessageHandler();
    // Subscribe to channels
    useEffect(() => {
        if (channels.length === 0)
            return;
        const subscriptions = channels.map((channel) => ({ channel, tailLines }));
        const unsubscribe = subscribeChannels(subscriptions);
        return () => {
            unsubscribe();
        };
    }, [channels, tailLines, subscribeChannels]);
    // Register message handler
    useEffect(() => {
        if (!onMessage)
            return;
        const unregister = registerMessageHandler(onMessage);
        return () => {
            unregister();
        };
    }, [onMessage, registerMessageHandler]);
    return { connected, reconnecting, error };
}
