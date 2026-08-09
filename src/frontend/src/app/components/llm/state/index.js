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
// Canonical State
// ============================================================================
export { createInitialState, canonicalSelectors, } from './canonicalState';
// ============================================================================
// Unified Data Manager V2
// ============================================================================
export { 
// Manager class
UnifiedLlmDataManagerV2, 
// View adapters
ListViewAdapter, VisualGraphViewAdapter, 
// Factory & singleton
getDefaultManager, resetDefaultManager, 
// React hooks
useViewData, } from './UnifiedLlmDataManagerV2';
// Re-export hooks from ProviderContext
export { ProviderContextProvider, useProviderState, useProviderActions, useSelectedRole, useSelectedProvider, useSelectedMethod, useActiveTab, useConfigView, useTestPanelState, useInterviewPanelState, useConnectivityStatus, useIsProviderExpanded, useEditingProviderId, useEditFormState, useHasPendingChanges, useIsSavingProvider, useProviderError, useGlobalPendingChangesCount, } from './ProviderContext';
// Re-export form hooks
export { useProviderForm, useProviderFormList, } from './useProviderForm';
// Re-export from connectivityStore
export { useConnectivityStore, } from './connectivityStore';
// Re-export from providerReducer
export { ProviderActions, providerReducer, initialProviderState, } from './providerReducer';
// ============================================================================
// Version
// ============================================================================
export const STATE_MODULE_VERSION = '2.0.0';
