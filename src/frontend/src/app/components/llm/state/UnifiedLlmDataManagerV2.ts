/**
 * Unified LLM Data Manager V2
 * Phase 2 Implementation: Canonical State + View Adapters
 * 
 * Architecture:
 * - Single canonical state (LlmSettingsState)
 * - Read: Via view adapters (derived projections)
 * - Write: Single write path through manager
 */

import type {
  LlmSettingsState,
  ProviderEntity,
  RoleAssignment,
  VisualNodeState,
  VisualViewport,
} from './canonicalState';
import { createInitialState, canonicalSelectors } from './canonicalState';
import type { ProviderConfig, ProviderKind, ProviderStatus } from '../types';

// ============================================================================
// View Adapter Interfaces
// ============================================================================

/** Base view adapter interface */
export interface ViewAdapter<TViewData, TViewState = unknown> {
  /** Unique adapter identifier */
  readonly viewType: string;
  
  /** Convert canonical state to view data */
  adaptToView(state: LlmSettingsState): TViewData;
  
  /** Convert view data back to partial canonical updates */
  adaptFromView(viewData: TViewData, currentState: LlmSettingsState): Partial<LlmSettingsState>;
  
  /** Get initial view state */
  getInitialViewState(): TViewState;
}

/** View adapter with operation support */
export interface ViewAdapterWithOperations<TViewData, TViewState, TOperations extends { type: string }>
  extends ViewAdapter<TViewData, TViewState> {
  /** Get supported operation types */
  getSupportedOperations(): TOperations['type'][];
  
  /** Execute operation and return state updates */
  executeOperation(
    operation: TOperations,
    state: LlmSettingsState
  ): Partial<LlmSettingsState>;
}

// ============================================================================
// View Data Types
// ============================================================================

/** List view data structure */
export interface ListViewData {
  providers: Array<{
    id: string;
    name: string;
    kind: ProviderKind;
    status: ProviderStatus;
    modelId: string;
    costClass: string;
    hasError: boolean;
    lastTestAt?: string;
    interviewStatus?: string;
  }>;
  roles: Array<{
    id: string;
    label: string;
    assignedProviderId?: string;
    assignedModel?: string;
    ready: boolean;
  }>;
  summary: {
    totalProviders: number;
    readyProviders: number;
    configuredRoles: number;
  };
}

/** Visual graph view data */
export interface VisualGraphViewData {
  nodes: Array<{
    id: string;
    kind: 'provider' | 'role' | 'model';
    label: string;
    position: { x: number; y: number };
    data: Record<string, unknown>;
  }>;
  edges: Array<{
    id: string;
    source: string;
    target: string;
    kind: string;
  }>;
  viewport: VisualViewport;
}

/** Detail/edit view data for a single provider */
export interface ProviderDetailViewData {
  provider: ProviderEntity | null;
  isEditing: boolean;
  isTesting: boolean;
  isSaving: boolean;
  validationErrors: string[];
}

// ============================================================================
// View Adapter Implementations
// ============================================================================

/** List view adapter */
export class ListViewAdapter implements ViewAdapter<ListViewData> {
  readonly viewType = 'list';

  adaptToView(state: LlmSettingsState): ListViewData {
    const providers = Object.values(state.entities.providers).map(p => ({
      id: p.id,
      name: p.name,
      kind: p.kind,
      status: p.status,
      modelId: p.modelId,
      costClass: p.costClass || 'balanced',
      hasError: !!p.lastError,
      lastTestAt: p.lastTest?.at,
      interviewStatus: p.interviewStatus,
    }));

    const roles = Object.values(state.entities.roleAssignments).map(ra => ({
      id: ra.roleId,
      label: this.getRoleLabel(ra.roleId),
      assignedProviderId: ra.providerId,
      assignedModel: ra.model,
      ready: ra.ready,
    }));

    return {
      providers,
      roles,
      summary: {
        totalProviders: providers.length,
        readyProviders: providers.filter(p => p.status === 'ready').length,
        configuredRoles: roles.filter(r => r.assignedProviderId).length,
      },
    };
  }

  adaptFromView(viewData: ListViewData, currentState: LlmSettingsState): Partial<LlmSettingsState> {
    // List view is read-only for now
    return {};
  }

  getInitialViewState(): unknown {
    return {};
  }

  private getRoleLabel(roleId: string): string {
    const labels: Record<string, string> = {
      pm: 'PM',
      director: 'Chief Engineer',
      qa: 'QA',
      architect: 'Architect',
    };
    return labels[roleId] || roleId;
  }
}

/** Visual graph view adapter */
export class VisualGraphViewAdapter implements ViewAdapter<VisualGraphViewData> {
  readonly viewType = 'visual';

  adaptToView(state: LlmSettingsState): VisualGraphViewData {
    const nodes: VisualGraphViewData['nodes'] = [];
    const edges: VisualGraphViewData['edges'] = [];

    // Add provider nodes
    Object.values(state.entities.providers).forEach(provider => {
      const visualNode = state.visualGraph.nodes[provider.id];
      nodes.push({
        id: provider.id,
        kind: 'provider',
        label: provider.name,
        position: visualNode?.position || { x: 0, y: 0 },
        data: {
          status: provider.status,
          kind: provider.kind,
          modelId: provider.modelId,
        },
      });
    });

    // Add role nodes
    Object.values(state.entities.roleAssignments).forEach(ra => {
      const visualNode = state.visualGraph.nodes[ra.roleId];
      nodes.push({
        id: ra.roleId,
        kind: 'role',
        label: this.getRoleLabel(ra.roleId),
        position: visualNode?.position || { x: 0, y: 0 },
        data: {
          ready: ra.ready,
          assignedProviderId: ra.providerId,
        },
      });

      // Add edge if provider assigned
      if (ra.providerId) {
        edges.push({
          id: `${ra.providerId}-${ra.roleId}`,
          source: ra.providerId,
          target: ra.roleId,
          kind: 'provider-to-role',
        });
      }
    });

    return {
      nodes,
      edges,
      viewport: state.visualGraph.viewport,
    };
  }

  adaptFromView(viewData: VisualGraphViewData, currentState: LlmSettingsState): Partial<LlmSettingsState> {
    // Convert view data back to visual graph state
    const nodes: Record<string, VisualNodeState> = {};
    
    viewData.nodes.forEach(node => {
      nodes[node.id] = {
        id: node.id,
        position: node.position,
      };
    });

    return {
      visualGraph: {
        ...currentState.visualGraph,
        nodes,
        viewport: viewData.viewport,
      },
    };
  }

  getInitialViewState(): unknown {
    return {};
  }

  private getRoleLabel(roleId: string): string {
    const labels: Record<string, string> = {
      pm: 'PM',
      director: 'Chief Engineer',
      qa: 'QA',
      architect: 'Architect',
    };
    return labels[roleId] || roleId;
  }
}

// ============================================================================
// Unified Data Manager V2
// ============================================================================

type ChangeListener = (state: LlmSettingsState, prevState: LlmSettingsState) => void;

export class UnifiedLlmDataManagerV2 {
  private state: LlmSettingsState;
  private adapters: Map<string, ViewAdapter<unknown, unknown>> = new Map();
  private listeners: Set<ChangeListener> = new Set();
  private history: LlmSettingsState[] = [];
  private maxHistorySize = 50;

  constructor(initialState?: LlmSettingsState) {
    this.state = initialState || createInitialState();
  }

  // === State Access ===

  /** Get full canonical state (for advanced use cases) */
  getState(): LlmSettingsState {
    return this.state;
  }

  /** Get view data through adapter */
  getViewData<T>(viewType: string): T {
    const adapter = this.adapters.get(viewType);
    if (!adapter) {
      throw new Error(`No adapter registered for view type: ${viewType}`);
    }
    return adapter.adaptToView(this.state) as T;
  }

  // === View Adapter Registration ===

  registerAdapter<TViewData, TViewState>(adapter: ViewAdapter<TViewData, TViewState>): void {
    this.adapters.set(adapter.viewType, adapter as ViewAdapter<unknown, unknown>);
  }

  unregisterAdapter(viewType: string): void {
    this.adapters.delete(viewType);
  }

  // === State Updates (Single Write Path) ===

  /**
   * Update state from view data
   * This is the PRIMARY write path for view-driven updates
   */
  updateFromView<T>(viewType: string, viewData: T): void {
    const adapter = this.adapters.get(viewType);
    if (!adapter) {
      throw new Error(`No adapter registered for view type: ${viewType}`);
    }

    const updates = adapter.adaptFromView(viewData, this.state);
    this.applyUpdates(updates);
  }

  /**
   * Direct state update (for entity operations)
   * Prefer updateFromView for view-driven changes
   */
  updateEntities(updates: Partial<LlmSettingsState['entities']>): void {
    this.applyUpdates({
      entities: {
        ...this.state.entities,
        ...updates,
        providers: { ...this.state.entities.providers, ...updates.providers },
        roleAssignments: { ...this.state.entities.roleAssignments, ...updates.roleAssignments },
      },
    });
  }

  /** Update UI state (transient) */
  updateUI(uiUpdates: Partial<LlmSettingsState['ui']>): void {
    this.applyUpdates({
      ui: { ...this.state.ui, ...uiUpdates },
    });
  }

  /** Update async operations state */
  updateAsyncOps(asyncOpsUpdates: Partial<LlmSettingsState['asyncOps']>): void {
    this.applyUpdates({
      asyncOps: { ...this.state.asyncOps, ...asyncOpsUpdates },
    });
  }

  /** Update connectivity test results */
  updateConnectivityResult(key: string, result: import('./canonicalState').ConnectivityResult): void {
    this.applyUpdates({
      connectivity: {
        ...this.state.connectivity,
        results: {
          ...this.state.connectivity.results,
          [key]: result,
        },
        lastTestedAt: new Date().toISOString(),
      },
    });
  }

  /** Get connectivity result by key */
  getConnectivityResult(key: string): import('./canonicalState').ConnectivityResult | undefined {
    return this.state.connectivity.results[key];
  }

  /** Clear connectivity result by key */
  clearConnectivityResult(key: string): void {
    const results = { ...this.state.connectivity.results };
    delete results[key];
    this.applyUpdates({
      connectivity: {
        ...this.state.connectivity,
        results,
      },
    });
  }

  // === Provider Operations ===

  addProvider(entity: ProviderEntity): void {
    this.applyUpdates({
      entities: {
        ...this.state.entities,
        providers: {
          ...this.state.entities.providers,
          [entity.id]: entity,
        },
      },
    });
  }

  updateProvider(id: string, updates: Partial<ProviderEntity>): void {
    const existing = this.state.entities.providers[id];
    if (!existing) return;

    this.applyUpdates({
      entities: {
        ...this.state.entities,
        providers: {
          ...this.state.entities.providers,
          [id]: { ...existing, ...updates, updatedAt: new Date().toISOString() },
        },
      },
    });
  }

  removeProvider(id: string): void {
    const providers = { ...this.state.entities.providers };
    delete providers[id];

    this.applyUpdates({
      entities: {
        ...this.state.entities,
        providers,
      },
    });
  }

  // === Role Operations ===

  assignRole(roleId: 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr', providerId: string, model?: string): void {
    this.applyUpdates({
      entities: {
        ...this.state.entities,
        roleAssignments: {
          ...this.state.entities.roleAssignments,
          [roleId]: {
            ...this.state.entities.roleAssignments[roleId],
            roleId,
            providerId,
            model,
          },
        },
      },
    });
  }

  // === Subscriptions ===

  subscribe(listener: ChangeListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  // === History (Undo/Redo) ===

  canUndo(): boolean {
    return this.history.length > 0;
  }

  undo(): void {
    if (this.history.length === 0) return;
    const prevState = this.history.pop();
    if (prevState) {
      this.state = prevState;
      this.notifyListeners(prevState);
    }
  }

  // === Private ===

  private applyUpdates(updates: Partial<LlmSettingsState>): void {
    const prevState = this.state;
    
    // Save to history
    this.history.push(prevState);
    if (this.history.length > this.maxHistorySize) {
      this.history.shift();
    }

    // Apply updates
    this.state = {
      ...this.state,
      ...updates,
      lastUpdated: new Date().toISOString(),
    };

    this.notifyListeners(prevState);
  }

  private notifyListeners(prevState: LlmSettingsState): void {
    this.listeners.forEach(listener => listener(this.state, prevState));
  }
}

// ============================================================================
// Factory & Singleton
// ============================================================================

let defaultManager: UnifiedLlmDataManagerV2 | null = null;

export function getDefaultManager(): UnifiedLlmDataManagerV2 {
  if (!defaultManager) {
    defaultManager = new UnifiedLlmDataManagerV2();
    // Register default adapters
    defaultManager.registerAdapter(new ListViewAdapter());
    defaultManager.registerAdapter(new VisualGraphViewAdapter());
  }
  return defaultManager;
}

export function resetDefaultManager(): void {
  defaultManager = null;
}

// ============================================================================
// React Integration Hook (Preparation for Phase 3)
// ============================================================================

import { useState, useEffect, useCallback } from 'react';

export function useCanonicalState(manager?: UnifiedLlmDataManagerV2) {
  const mgr = manager || getDefaultManager();
  const [state, setState] = useState(mgr.getState());

  useEffect(() => {
    return mgr.subscribe((newState) => {
      setState(newState);
    });
  }, [mgr]);

  const updateUI = useCallback((updates: Partial<LlmSettingsState['ui']>) => {
    mgr.updateUI(updates);
  }, [mgr]);

  const updateAsyncOps = useCallback((updates: Partial<LlmSettingsState['asyncOps']>) => {
    mgr.updateAsyncOps(updates);
  }, [mgr]);

  return {
    state,
    updateUI,
    updateAsyncOps,
    manager: mgr,
  };
}

export function useViewData<T>(viewType: string, manager?: UnifiedLlmDataManagerV2): T {
  const mgr = manager || getDefaultManager();
  const [data, setData] = useState<T>(() => mgr.getViewData(viewType));

  useEffect(() => {
    return mgr.subscribe(() => {
      setData(mgr.getViewData(viewType));
    });
  }, [mgr, viewType]);

  return data;
}
