/**
 * View Adapters
 * 
 * Provides adapters for transforming between unified config and view-specific data formats.
 * Includes strict type-safe adapters for enhanced development experience.
 */

// Core adapters
export { ListViewAdapter } from './ListViewAdapter';
export { VisualViewAdapter } from './VisualViewAdapter';
export { DeepTestViewAdapter } from './DeepTestViewAdapter';

// Type definitions
export type { ViewAdapter, ViewActions } from './types';
export type { ListViewData, ListViewState } from './ListViewAdapter';
export type { VisualViewData, VisualViewState } from './VisualViewAdapter';
export type { DeepTestViewData, DeepTestViewState } from './DeepTestViewAdapter';

// Phase 4.2: Strict Type Adapters
export {
  StrictListViewAdapter,
  TypedOperationExecutor,
  ListOperations,
  isListOperation,
  isVisualOperation,
  isTestOperation,
} from './StrictViewAdapter';

export type {
  StrictViewAdapter,
  ListUpdateOperation,
  VisualUpdateOperation,
  TestUpdateOperation,
  ViewUpdateOperation,
  ExtractOperationParams,
} from './StrictViewAdapter';
