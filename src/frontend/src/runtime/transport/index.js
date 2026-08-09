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
 * // In components, use focused hooks
 * const { connected } = useConnectionState();
 * const { subscribeChannels, sendCommand } = useTransportActions();
 *
 * // Or use the convenience hook for channel subscription
 * const { connected } = useChannelSubscription({
 *   channels: ['runtime', 'pm_subprocess'],
 *   tailLines: 0,
 *   onMessage: (msg) => console.log(msg),
 * });
 * ```
 */
export { runtimeSocketManager, } from './runtimeSocketManager';
export { RuntimeTransportProvider, useConnectionState, useTransportActions, useMessageHandler, useChannelSubscription, } from './RuntimeTransportProvider';
