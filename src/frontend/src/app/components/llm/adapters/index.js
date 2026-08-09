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
// Phase 4.2: Strict Type Adapters
export { StrictListViewAdapter, TypedOperationExecutor, ListOperations, isListOperation, isVisualOperation, isTestOperation, } from './StrictViewAdapter';
