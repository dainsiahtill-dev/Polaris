/**
 * Canonical State Definition for LLM Settings
 * Phase 2: Single Source of Truth
 *
 * Architecture Principle:
 * - This is the ONLY mutable state in the system
 * - All views are derived read-only projections
 * - All writes go through UnifiedLlmDataManager
 */
// ============================================================================
// Initial State Factory
// ============================================================================
export function createInitialState() {
    return {
        entities: {
            providers: {},
            roleAssignments: {
                pm: { roleId: "pm", ready: false },
                chief_engineer: { roleId: "chief_engineer", ready: false },
                director: { roleId: "director", ready: false },
                qa: { roleId: "qa", ready: false },
                architect: { roleId: "architect", ready: false },
                resident_agi: { roleId: "resident_agi", ready: false },
            },
            roleRequirements: {
                pm: { roleId: "pm", requiresThinking: true, minConfidence: 0.8 },
                chief_engineer: {
                    roleId: "chief_engineer",
                    requiresThinking: true,
                    minConfidence: 0.85,
                },
                director: {
                    roleId: "director",
                    requiresThinking: true,
                    minConfidence: 0.9,
                },
                qa: { roleId: "qa", requiresThinking: false, minConfidence: 0.7 },
                architect: {
                    roleId: "architect",
                    requiresThinking: false,
                    minConfidence: 0.6,
                },
                resident_agi: {
                    roleId: "resident_agi",
                    requiresThinking: true,
                    minConfidence: 0.85,
                },
            },
        },
        visualGraph: {
            nodes: {},
            edges: {},
            viewport: { x: 0, y: 0, zoom: 1 },
        },
        ui: {
            viewMode: "list",
            expandedProviderIds: [],
            isLoading: false,
            isSaving: false,
        },
        asyncOps: {},
        connectivity: {
            results: {},
        },
        version: 1,
        lastUpdated: new Date().toISOString(),
    };
}
// ============================================================================
// Selectors (Read-only projections)
// ============================================================================
export const canonicalSelectors = {
    // Provider selectors
    getProviderById: (state, id) => state.entities.providers[id],
    getAllProviders: (state) => Object.values(state.entities.providers),
    getProvidersByKind: (state, kind) => Object.values(state.entities.providers).filter((p) => p.kind === kind),
    // Role selectors
    getRoleAssignment: (state, roleId) => state.entities.roleAssignments[roleId],
    getAllRoleAssignments: (state) => Object.values(state.entities.roleAssignments),
    // Status selectors
    getReadyProviders: (state) => Object.values(state.entities.providers).filter((p) => p.status === "ready"),
    getProvidersNeedingApiKey: (state) => Object.values(state.entities.providers).filter((p) => p.status === "failed" && p.lastError?.includes("API key")),
    // Visual graph selectors
    getVisualNode: (state, id) => state.visualGraph.nodes[id],
    getVisualViewport: (state) => state.visualGraph.viewport,
    // Connectivity selectors
    getConnectivityResult: (state, key) => state.connectivity.results[key],
    getAllConnectivityResults: (state) => state.connectivity.results,
    getConnectivityResultForProvider: (state, providerId, roleId) => {
        // Try role-specific key first
        if (roleId) {
            const roleResult = state.connectivity.results[`${roleId}:${providerId}`];
            if (roleResult)
                return roleResult;
        }
        // Fall back to any key with this provider
        for (const [key, result] of Object.entries(state.connectivity.results)) {
            if (key.endsWith(`:${providerId}`)) {
                return result;
            }
        }
        return undefined;
    },
};
// ============================================================================
// State Compatibility Layer (Bridge to existing code)
// ============================================================================
/**
 * Convert canonical state to legacy ProviderConfig format
 * Used for backward compatibility during migration
 */
export function toLegacyProviderConfig(entity) {
    return {
        ...entity.config,
        name: entity.name,
        model: entity.modelId,
        default_model: entity.modelId,
        base_url: entity.conn.kind === "http" ? entity.conn.baseUrl : undefined,
        api_key: entity.conn.kind === "http" ? entity.conn.apiKey : undefined,
    };
}
/**
 * Create ProviderEntity from legacy config
 */
export function fromLegacyProviderConfig(id, config) {
    const now = new Date().toISOString();
    const conn = config.conn
        ? {
            kind: "http",
            baseUrl: config.base_url || "",
            ...config.conn,
        }
        : { kind: "http", baseUrl: config.base_url || "" };
    return {
        id,
        name: config.name || id,
        kind: config.type || "openai_compat",
        type: config.type || "",
        conn,
        cliMode: config.cli_mode,
        modelId: config.model || config.default_model || "",
        status: "untested",
        costClass: "FIXED",
        config,
        createdAt: now,
        updatedAt: now,
    };
}
