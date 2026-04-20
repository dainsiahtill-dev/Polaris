/**
 * Runtime Module - Types, selectors, and guards for runtime state management
 *
 * This module provides:
 * - v2: Runtime V2 types (snapshot, events, phases)
 * - projection: Canonical RuntimeProjection contract types
 * - projectionCompat: Compatibility layer for legacy formats
 * - selectors: React hooks for selecting runtime state
 * - guards: Type guards for runtime types
 * - dashboard: Dashboard-specific runtime utilities
 * - directorWorkspace: Director workspace utilities
 * - transport: Unified WebSocket transport layer (NEW - WS/SSE Alignment)
 *
 * Architecture Note (WS/SSE Alignment):
 * - Runtime domain uses WebSocket ONLY via RuntimeTransportProvider
 * - All runtime components must use useRuntime() or useRuntimeTransport()
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

// Canonical projection contract (Phase 8)
export * from './projection';

// Compatibility layer for legacy formats (Phase 8)
export * from './projectionCompat';

// React selectors
export * from './selectors';

// Type guards
export * from './guards';

// Dashboard utilities
export * from './dashboard';

// Director workspace utilities
export * from './directorWorkspace';

// Unified transport layer - Use this for all runtime WebSocket needs
export {
  RuntimeTransportProvider,
  useRuntimeTransport,
  useConnectionState,
  useTransportActions,
  useMessageHandler,
  useChannelSubscription,
  runtimeSocketManager,
  type RuntimeTransportContextValue,
  type ConnectionStateContextValue,
  type TransportActionsContextValue,
  type MessageHandlerContextValue,
  type ChannelSubscription,
  type MessageListener,
  type ConnectionState,
} from './transport';

// Note: Legacy hook references removed as part of WS/SSE alignment.
// Use useRuntime from '@/app/hooks/useRuntime' (requires RuntimeTransportProvider)
// or useRuntimeTransport for lower-level access.
