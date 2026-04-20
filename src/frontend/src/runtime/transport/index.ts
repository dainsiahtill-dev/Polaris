/**
 * Runtime Transport Module
 *
 * Unified WebSocket connection management for runtime domain.
 * Use this for all runtime WebSocket communication.
 *
 * @example
 * ```tsx
 * // Wrap your app with the provider
 * <RuntimeTransportProvider>
 *   <App />
 * </RuntimeTransportProvider>
 *
 * // In components, use the hook
 * const { connected, subscribeChannels, sendCommand } = useRuntimeTransport();
 *
 * // Or use the convenience hook for channel subscription
 * const { connected } = useChannelSubscription({
 *   channels: ['runtime', 'pm_subprocess'],
 *   tailLines: 100,
 *   onMessage: (msg) => console.log(msg),
 * });
 * ```
 */

export {
  runtimeSocketManager,
  type RuntimeSocketManager,
  type ChannelSubscription,
  type MessageListener,
  type ConnectionState,
  type ConnectionStateListener,
} from './runtimeSocketManager';

export {
  RuntimeTransportProvider,
  useRuntimeTransport,
  useConnectionState,
  useTransportActions,
  useMessageHandler,
  useChannelSubscription,
  type RuntimeTransportContextValue,
  type ConnectionStateContextValue,
  type TransportActionsContextValue,
  type MessageHandlerContextValue,
  type UseChannelSubscriptionOptions,
} from './RuntimeTransportProvider';
