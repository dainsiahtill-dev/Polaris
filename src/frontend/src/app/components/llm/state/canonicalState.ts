/**
 * Canonical State Definition for LLM Settings
 * Phase 2: Single Source of Truth
 * 
 * Architecture Principle:
 * - This is the ONLY mutable state in the system
 * - All views are derived read-only projections
 * - All writes go through UnifiedLlmDataManager
 */

import type { 
  ProviderConfig, 
  ProviderKind, 
  ProviderStatus,
  CostClass,
  InterviewStatus,
  CLIMode,
  ProviderConnection 
} from '../types';

// ============================================================================
// Core Entity Types (Normalized)
// ============================================================================

/** Provider Entity - Canonical representation */
export interface ProviderEntity {
  id: string;
  name: string;
  kind: ProviderKind;
  type: string; // Provider type identifier (e.g., 'codex_sdk', 'openai_compat')
  conn: ProviderConnection;
  cliMode?: CLIMode;
  modelId: string;
  status: ProviderStatus;
  lastError?: string;
  lastTest?: {
    at: string;
    latencyMs?: number;
    usage?: {
      totalTokens?: number;
      estimated?: boolean;
    };
    note?: string;
  };
  costClass?: CostClass;
  outputPath?: string;
  interviewStatus?: InterviewStatus;
  lastInterviewAt?: string;
  interviewDetails?: {
    role?: string;
    runId?: string;
    note?: string;
  };
  // Original config for serialization
  config: ProviderConfig;
  // Metadata
  createdAt: string;
  updatedAt: string;
}

/** Role Assignment - Links role to provider */
export interface RoleAssignment {
  roleId: 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr';
  providerId?: string;
  model?: string;
  profile?: string;
  // Computed readiness status
  ready: boolean;
  grade?: string;
}

/** Role Requirements - Policy configuration */
export interface RoleRequirement {
  roleId: 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr';
  requiresThinking?: boolean;
  minConfidence?: number;
  errorMessage?: string;
}

// ============================================================================
// Visual Graph State (Serializable)
// ============================================================================

export interface VisualNodePosition {
  x: number;
  y: number;
}

export interface VisualViewport {
  x: number;
  y: number;
  zoom: number;
}

export interface VisualNodeState {
  id: string;
  position?: VisualNodePosition;
  expanded?: boolean;
  selected?: boolean;
  hidden?: boolean;
}

export interface VisualEdgeState {
  id: string;
  source: string;
  target: string;
  kind: 'provider-to-model' | 'model-to-role';
}

/** Visual graph layout state - part of canonical state */
export interface VisualGraphState {
  nodes: Record<string, VisualNodeState>;
  edges: Record<string, VisualEdgeState>;
  viewport: VisualViewport;
}

// ============================================================================
// Runtime/UI State (Transient - NOT persisted)
// ============================================================================

export interface UIState {
  // View mode
  viewMode: 'list' | 'visual' | 'split';
  // Selection state
  selectedProviderId?: string;
  selectedRoleId?: string;
  expandedProviderIds: string[];
  // Editing state
  editingProviderId?: string;
  editingRoleId?: string;
  // Modal/dialog state
  activeModal?: 'add-provider' | 'edit-provider' | 'test-provider' | 'interview-role' | null;
  // Loading states
  isLoading: boolean;
  isSaving: boolean;
  // Last error
  lastError?: string;
}

export interface AsyncOperationState {
  testingProviderId?: string;
  savingProviderId?: string;
  deletingProviderId?: string;
  interviewingRoleId?: string;
}

// ============================================================================
// Connectivity Test Results (Persisted)
// ============================================================================

/** Connectivity test result */
export interface ConnectivityResult {
  ok: boolean;
  timestamp: string;
  latencyMs?: number;
  error?: string;
  model?: string;
  sourceRole?: string;
  thinking?: {
    supportsThinking?: boolean;
    confidence?: number;
    format?: string;
  };
}

/** Connectivity results storage - keyed by "role:providerId" */
export interface ConnectivityState {
  results: Record<string, ConnectivityResult>;
  lastTestedAt?: string;
}

// ============================================================================
// Canonical State - Single Source of Truth
// ============================================================================

/**
 * LlmSettingsState - The ONE canonical state for LLM Settings
 * 
 * All data flows through this structure:
 * - UI reads: Via view adapters (derived, read-only)
 * - UI writes: Through UnifiedLlmDataManager (single write path)
 */
export interface LlmSettingsState {
  // === Data Entities (Normalized) ===
  entities: {
    providers: Record<string, ProviderEntity>;
    roleAssignments: Record<string, RoleAssignment>;
    roleRequirements: Record<string, RoleRequirement>;
  };
  
  // === Visual Graph State (Serialized) ===
  visualGraph: VisualGraphState;
  
  // === UI State (Transient, not persisted) ===
  ui: UIState;
  
  // === Async Operations (Transient) ===
  asyncOps: AsyncOperationState;
  
  // === Connectivity Results (Persisted) ===
  connectivity: ConnectivityState;
  
  // === System ===
  version: number; // State schema version for migrations
  lastUpdated: string;
}

// ============================================================================
// Initial State Factory
// ============================================================================

export function createInitialState(): LlmSettingsState {
  return {
    entities: {
      providers: {},
      roleAssignments: {
        pm: { roleId: 'pm', ready: false },
        director: { roleId: 'director', ready: false },
        qa: { roleId: 'qa', ready: false },
        architect: { roleId: 'architect', ready: false },
      },
      roleRequirements: {
        pm: { roleId: 'pm', requiresThinking: true, minConfidence: 0.8 },
        director: { roleId: 'director', requiresThinking: true, minConfidence: 0.9 },
        qa: { roleId: 'qa', requiresThinking: false, minConfidence: 0.7 },
        architect: { roleId: 'architect', requiresThinking: false, minConfidence: 0.6 },
      },
    },
    visualGraph: {
      nodes: {},
      edges: {},
      viewport: { x: 0, y: 0, zoom: 1 },
    },
    ui: {
      viewMode: 'list',
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
  getProviderById: (state: LlmSettingsState, id: string): ProviderEntity | undefined =>
    state.entities.providers[id],
  
  getAllProviders: (state: LlmSettingsState): ProviderEntity[] =>
    Object.values(state.entities.providers),
  
  getProvidersByKind: (state: LlmSettingsState, kind: ProviderKind): ProviderEntity[] =>
    Object.values(state.entities.providers).filter(p => p.kind === kind),
  
  // Role selectors
  getRoleAssignment: (state: LlmSettingsState, roleId: string): RoleAssignment | undefined =>
    state.entities.roleAssignments[roleId],
  
  getAllRoleAssignments: (state: LlmSettingsState): RoleAssignment[] =>
    Object.values(state.entities.roleAssignments),
  
  // Status selectors
  getReadyProviders: (state: LlmSettingsState): ProviderEntity[] =>
    Object.values(state.entities.providers).filter(p => p.status === 'ready'),
  
  getProvidersNeedingApiKey: (state: LlmSettingsState): ProviderEntity[] =>
    Object.values(state.entities.providers).filter(p => 
      p.status === 'failed' && p.lastError?.includes('API key')
    ),
  
  // Visual graph selectors
  getVisualNode: (state: LlmSettingsState, id: string): VisualNodeState | undefined =>
    state.visualGraph.nodes[id],
  
  getVisualViewport: (state: LlmSettingsState): VisualViewport =>
    state.visualGraph.viewport,
  
  // Connectivity selectors
  getConnectivityResult: (state: LlmSettingsState, key: string): ConnectivityResult | undefined =>
    state.connectivity.results[key],
  
  getAllConnectivityResults: (state: LlmSettingsState): Record<string, ConnectivityResult> =>
    state.connectivity.results,
  
  getConnectivityResultForProvider: (state: LlmSettingsState, providerId: string, roleId?: string): ConnectivityResult | undefined => {
    // Try role-specific key first
    if (roleId) {
      const roleResult = state.connectivity.results[`${roleId}:${providerId}`];
      if (roleResult) return roleResult;
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
export function toLegacyProviderConfig(entity: ProviderEntity): ProviderConfig {
  return {
    ...entity.config,
    name: entity.name,
    model: entity.modelId,
    default_model: entity.modelId,
    base_url: entity.conn.kind === 'http' ? entity.conn.baseUrl : undefined,
    api_key: entity.conn.kind === 'http' ? entity.conn.apiKey : undefined,
  };
}

/**
 * Create ProviderEntity from legacy config
 */
export function fromLegacyProviderConfig(
  id: string, 
  config: ProviderConfig
): ProviderEntity {
  const now = new Date().toISOString();
  const conn: ProviderConnection = config.conn 
    ? { kind: 'http', baseUrl: config.base_url || '', ...config.conn } as ProviderConnection
    : { kind: 'http' as const, baseUrl: config.base_url || '' };
  return {
    id,
    name: config.name || id,
    kind: (config.type as ProviderKind) || 'openai_compat',
    type: config.type || '',
    conn,
    cliMode: config.cli_mode,
    modelId: config.model || config.default_model || '',
    status: 'untested',
    costClass: 'FIXED',
    config,
    createdAt: now,
    updatedAt: now,
  };
}
