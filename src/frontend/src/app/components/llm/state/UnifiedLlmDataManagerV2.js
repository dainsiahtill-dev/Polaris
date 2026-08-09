/**
 * Unified LLM Data Manager V2
 * Phase 2 Implementation: Canonical State + View Adapters
 *
 * Architecture:
 * - Single canonical state (LlmSettingsState)
 * - Read: Via view adapters (derived projections)
 * - Write: Single write path through manager
 */
import { getRoleDisplayLabel } from "@/app/constants/roleLabels";
import { createInitialState } from "./canonicalState";
// ============================================================================
// View Adapter Implementations
// ============================================================================
/** List view adapter */
export class ListViewAdapter {
    constructor() {
        this.viewType = "list";
    }
    adaptToView(state) {
        const providers = Object.values(state.entities.providers).map((p) => ({
            id: p.id,
            name: p.name,
            kind: p.kind,
            status: p.status,
            modelId: p.modelId,
            costClass: p.costClass || "balanced",
            hasError: !!p.lastError,
            lastTestAt: p.lastTest?.at,
            interviewStatus: p.interviewStatus,
        }));
        const roles = Object.values(state.entities.roleAssignments).map((ra) => ({
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
                readyProviders: providers.filter((p) => p.status === "ready").length,
                configuredRoles: roles.filter((r) => r.assignedProviderId).length,
            },
        };
    }
    adaptFromView(viewData, currentState) {
        // List view is read-only for now
        return {};
    }
    getInitialViewState() {
        return {};
    }
    getRoleLabel(roleId) {
        return getRoleDisplayLabel(roleId);
    }
}
/** Visual graph view adapter */
export class VisualGraphViewAdapter {
    constructor() {
        this.viewType = "visual";
    }
    adaptToView(state) {
        const nodes = [];
        const edges = [];
        // Add provider nodes
        Object.values(state.entities.providers).forEach((provider) => {
            const visualNode = state.visualGraph.nodes[provider.id];
            nodes.push({
                id: provider.id,
                kind: "provider",
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
        Object.values(state.entities.roleAssignments).forEach((ra) => {
            const visualNode = state.visualGraph.nodes[ra.roleId];
            nodes.push({
                id: ra.roleId,
                kind: "role",
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
                    kind: "provider-to-role",
                });
            }
        });
        return {
            nodes,
            edges,
            viewport: state.visualGraph.viewport,
        };
    }
    adaptFromView(viewData, currentState) {
        // Convert view data back to visual graph state
        const nodes = {};
        viewData.nodes.forEach((node) => {
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
    getInitialViewState() {
        return {};
    }
    getRoleLabel(roleId) {
        return getRoleDisplayLabel(roleId);
    }
}
export class UnifiedLlmDataManagerV2 {
    constructor(initialState) {
        this.adapters = new Map();
        this.listeners = new Set();
        this.history = [];
        this.maxHistorySize = 50;
        this.state = initialState || createInitialState();
    }
    // === State Access ===
    /** Get full canonical state (for advanced use cases) */
    getState() {
        return this.state;
    }
    /** Get view data through adapter */
    getViewData(viewType) {
        const adapter = this.adapters.get(viewType);
        if (!adapter) {
            throw new Error(`No adapter registered for view type: ${viewType}`);
        }
        return adapter.adaptToView(this.state);
    }
    // === View Adapter Registration ===
    registerAdapter(adapter) {
        this.adapters.set(adapter.viewType, adapter);
    }
    unregisterAdapter(viewType) {
        this.adapters.delete(viewType);
    }
    // === State Updates (Single Write Path) ===
    /**
     * Update state from view data
     * This is the PRIMARY write path for view-driven updates
     */
    updateFromView(viewType, viewData) {
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
    updateEntities(updates) {
        this.applyUpdates({
            entities: {
                ...this.state.entities,
                ...updates,
                providers: { ...this.state.entities.providers, ...updates.providers },
                roleAssignments: {
                    ...this.state.entities.roleAssignments,
                    ...updates.roleAssignments,
                },
            },
        });
    }
    /** Update UI state (transient) */
    updateUI(uiUpdates) {
        this.applyUpdates({
            ui: { ...this.state.ui, ...uiUpdates },
        });
    }
    /** Update async operations state */
    updateAsyncOps(asyncOpsUpdates) {
        this.applyUpdates({
            asyncOps: { ...this.state.asyncOps, ...asyncOpsUpdates },
        });
    }
    /** Update connectivity test results */
    updateConnectivityResult(key, result) {
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
    getConnectivityResult(key) {
        return this.state.connectivity.results[key];
    }
    /** Clear connectivity result by key */
    clearConnectivityResult(key) {
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
    addProvider(entity) {
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
    updateProvider(id, updates) {
        const existing = this.state.entities.providers[id];
        if (!existing)
            return;
        this.applyUpdates({
            entities: {
                ...this.state.entities,
                providers: {
                    ...this.state.entities.providers,
                    [id]: {
                        ...existing,
                        ...updates,
                        updatedAt: new Date().toISOString(),
                    },
                },
            },
        });
    }
    removeProvider(id) {
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
    assignRole(roleId, providerId, model) {
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
    subscribe(listener) {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    }
    // === History (Undo/Redo) ===
    canUndo() {
        return this.history.length > 0;
    }
    undo() {
        if (this.history.length === 0)
            return;
        const prevState = this.history.pop();
        if (prevState) {
            this.state = prevState;
            this.notifyListeners(prevState);
        }
    }
    // === Private ===
    applyUpdates(updates) {
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
    notifyListeners(prevState) {
        this.listeners.forEach((listener) => listener(this.state, prevState));
    }
}
// ============================================================================
// Factory & Singleton
// ============================================================================
let defaultManager = null;
export function getDefaultManager() {
    if (!defaultManager) {
        defaultManager = new UnifiedLlmDataManagerV2();
        // Register default adapters
        defaultManager.registerAdapter(new ListViewAdapter());
        defaultManager.registerAdapter(new VisualGraphViewAdapter());
    }
    return defaultManager;
}
export function resetDefaultManager() {
    defaultManager = null;
}
// ============================================================================
// React Integration Hook (Preparation for Phase 3)
// ============================================================================
import { useState, useEffect, useCallback } from "react";
export function useCanonicalState(manager) {
    const mgr = manager || getDefaultManager();
    const [state, setState] = useState(mgr.getState());
    useEffect(() => {
        return mgr.subscribe((newState) => {
            setState(newState);
        });
    }, [mgr]);
    const updateUI = useCallback((updates) => {
        mgr.updateUI(updates);
    }, [mgr]);
    const updateAsyncOps = useCallback((updates) => {
        mgr.updateAsyncOps(updates);
    }, [mgr]);
    return {
        state,
        updateUI,
        updateAsyncOps,
        manager: mgr,
    };
}
export function useViewData(viewType, manager) {
    const mgr = manager || getDefaultManager();
    const [data, setData] = useState(() => mgr.getViewData(viewType));
    useEffect(() => {
        return mgr.subscribe(() => {
            setData(mgr.getViewData(viewType));
        });
    }, [mgr, viewType]);
    return data;
}
