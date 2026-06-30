/**
 * Runtime Module - Types, selectors, and guards for runtime state management
 *
 * This module provides:
 * - v2: Runtime V2 types (snapshot, events, phases)
 * - projection: Canonical RuntimeProjection contract types
 * - projectionAdapter: Runtime event to projection adapter
 * - selectors: React hooks for selecting runtime state
 * - guards: Type guards for runtime types
 * - directorWorkspace: Director workspace utilities
 * - transport: Unified WebSocket transport layer (Nats-JetStream alignment)
 *
 * Architecture Note (Nats-JetStream Runtime Transport):
 * - Runtime domain uses WebSocket ONLY via RuntimeTransportProvider
 * - Runtime components must use useRuntime() or focused transport hooks
 * - Direct connectWebSocket() calls are deprecated for runtime domain
 *
 * @example
 * ```tsx
 * // In your app root (already done in App.tsx):
 * <RuntimeTransportProvider>
 *   <App />
 * </RuntimeTransportProvider>
 *
 * // In components:
 * import { useRuntime } from '@/app/hooks/useRuntime';
 *
 * function MyComponent() {
 *   const { connected, pmStatus, directorStatus } = useRuntime();
 *   return <div>{pmStatus?.running ? 'Running' : 'Idle'}</div>;
 * }
 * ```
 */

// Core V2 runtime types
export * from './v2';

// Canonical projection contract
export * from './projection';

// Runtime.v2 status-event adapter
export * from './projectionAdapter';

// React selectors
export * from './selectors';

// Type guards
export * from './guards';

// Director workspace utilities
export * from './directorWorkspace';

// Unified transport layer - Use this for all runtime WebSocket needs
export {
  RuntimeTransportProvider,
  useConnectionState,
  useTransportActions,
  useMessageHandler,
  useChannelSubscription,
  runtimeSocketManager,
  type ConnectionStateContextValue,
  type TransportActionsContextValue,
  type MessageHandlerContextValue,
  type ChannelSubscription,
  type MessageListener,
  type ConnectionState,
} from './transport';

// Runtime hooks live in '@/app/hooks/useRuntime' and require RuntimeTransportProvider.
// Use useRuntime from '@/app/hooks/useRuntime' (requires RuntimeTransportProvider)
// or focused transport hooks for lower-level access.
