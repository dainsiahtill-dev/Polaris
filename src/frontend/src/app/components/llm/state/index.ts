/**
 * State Management Module - Index
 * LLM Settings State Architecture
 * 
 * Export Pattern:
 * - Types: All state-related type definitions
 * - Canonical: Single source of truth state
 * - Manager: Unified data manager
 * - Adapters: View adapters for different views
 * - Hooks: React integration hooks
 */

// ============================================================================
// Core Types
// ============================================================================

export type {
  // Entity types
  ProviderEntity,
  RoleAssignment,
  RoleRequirement,
  
  // Visual graph types
  VisualNodePosition,
  VisualViewport,
  VisualNodeState,
  VisualEdgeState,
  VisualGraphState,
  
  // UI state types
  UIState,
  AsyncOperationState,
  
  // Connectivity types
  ConnectivityResult,
  ConnectivityState,
  
  // Canonical state
  LlmSettingsState,
} from './canonicalState';

// ============================================================================
// Canonical State
// ============================================================================

export {
  createInitialState,
  canonicalSelectors,
} from './canonicalState';

// ============================================================================
// Unified Data Manager V2
// ============================================================================

export {
  // Manager class
  UnifiedLlmDataManagerV2,
  
  // View adapters
  ListViewAdapter,
  VisualGraphViewAdapter,
  
  // Factory & singleton
  getDefaultManager,
  resetDefaultManager,
  
  // React hooks
  useViewData,
} from './UnifiedLlmDataManagerV2';

// View adapter types
export type {
  ViewAdapter,
  ViewAdapterWithOperations,
  ListViewData,
  VisualGraphViewData,
  ProviderDetailViewData,
} from './UnifiedLlmDataManagerV2';

// ============================================================================
// Legacy Exports (Backward Compatibility)
// ============================================================================

// Re-export from legacy modules for compatibility during migration
export type { ConnectivityStatus } from './providerReducer';

// Re-export hooks from ProviderContext
export {
  ProviderContextProvider,
  useProviderState,
  useProviderActions,
  useProviderContext,
  useSelectedRole,
  useSelectedProvider,
  useActiveTab,
  useTestPanelState,
  useInterviewPanelState,
  useConnectivityStatus,
  useIsProviderExpanded,
  useEditingProviderId,
  useEditFormState,
  useHasPendingChanges,
  useIsSavingProvider,
  useProviderError,
  useGlobalPendingChangesCount,
} from './ProviderContext';

// Re-export form hooks
export {
  useProviderForm,
  useProviderFormList,
} from './useProviderForm';

// Re-export from connectivityStore
export {
  useConnectivityStore,
  type RoleId,
} from './connectivityStore';

// Re-export from providerReducer
export {
  type ConnectionMethodId,
  type ProviderState,
  ProviderActions,
  providerReducer,
  initialProviderState,
} from './providerReducer';

// Legacy exports removed


// ============================================================================
// Bridge Layer (Phase 3)
// ============================================================================

export {
  // Bridge Provider
  CanonicalBridgeProvider,
  useCanonicalBridge,
  
  // Canonical state access
  useCanonicalState,
  useListViewData,
  useVisualGraphViewData,
} from './CanonicalProviderBridge';

// ============================================================================
// Version
// ============================================================================

export const STATE_MODULE_VERSION = '2.0.0';
