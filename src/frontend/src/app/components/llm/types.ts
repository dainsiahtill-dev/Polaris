// Unified LLM Provider Types
// This file centralizes all type definitions to avoid duplication and inconsistency

import type {
  SDKParams,
  RequestOverrides,
  ModelSpecificConfig,
} from './types/strict';

// Provider Categories
export const PROVIDER_CATEGORIES = {
  AGENT: 'AGENT' as const,
  LLM: 'LLM' as const
} as const;

export type ProviderCategory = typeof PROVIDER_CATEGORIES[keyof typeof PROVIDER_CATEGORIES];

// Provider Kinds (specific provider types)
export const PROVIDER_KINDS = {
  CODEX_CLI: 'codex_cli' as const,
  CODEX_SDK: 'codex_sdk' as const,
  GEMINI_CLI: 'gemini_cli' as const,
  OLLAMA: 'ollama' as const,
  OPENAI_COMPAT: 'openai_compat' as const,
  ANTHROPIC_COMPAT: 'anthropic_compat' as const,
  CUSTOM_HTTPS: 'custom_https' as const,
  MINIMAX: 'minimax' as const,
  GEMINI_API: 'gemini_api' as const,
  KIMI: 'kimi' as const
} as const;

export type ProviderKind = typeof PROVIDER_KINDS[keyof typeof PROVIDER_KINDS];

// Connection Types
export type CLIConnectionKind = 'codex_cli' | 'gemini_cli';
export type HTTPConnectionKind = 'http';

// CLI Modes
export const CLI_MODES = {
  TUI: 'tui' as const,
  HEADLESS: 'headless' as const
} as const;

export type CLIMode = typeof CLI_MODES[keyof typeof CLI_MODES];

export type CLIConnection = {
  kind: CLIConnectionKind;
  command: string;
  args?: string[];
  env?: Record<string, string>;
};

export type HTTPConnection = {
  kind: HTTPConnectionKind;
  baseUrl: string;
  apiKey?: string;
};

export type ProviderConnection = CLIConnection | HTTPConnection;

// Provider Status
export const PROVIDER_STATUS = {
  UNTESTED: 'untested' as const,
  TESTING: 'testing' as const,
  READY: 'ready' as const,
  FAILED: 'failed' as const
} as const;

export type ProviderStatus = typeof PROVIDER_STATUS[keyof typeof PROVIDER_STATUS];

// Cost Classes
export const COST_CLASSES = {
  LOCAL: 'LOCAL' as const,
  FIXED: 'FIXED' as const,
  METERED: 'METERED' as const
} as const;

export type CostClass = typeof COST_CLASSES[keyof typeof COST_CLASSES];

// Interview Status
export const INTERVIEW_STATUS = {
  NOT_TESTED: 'not_tested' as const,
  PASSED: 'passed' as const,
  FAILED: 'failed' as const
} as const;

export type InterviewStatus = typeof INTERVIEW_STATUS[keyof typeof INTERVIEW_STATUS];

// Model Listing Methods
export const MODEL_LISTING_METHODS = {
  API: 'API' as const,
  TUI: 'TUI' as const,
  NONE: 'NONE' as const
} as const;

export type ModelListingMethod = typeof MODEL_LISTING_METHODS[keyof typeof MODEL_LISTING_METHODS];

// Provider Info (from backend)
export interface ProviderInfo {
  name: string;
  type: string;
  description: string;
  version: string;
  author: string;
  documentation_url: string;
  supported_features: string[];
  cost_class: CostClass;
  provider_category: ProviderCategory;
  autonomous_file_access: boolean;
  requires_file_interfaces: boolean;
  model_listing_method: ModelListingMethod;
}

// Provider Config (frontend)
export interface ProviderConfig {
  type?: string;
  name?: string;
  command?: string;
  args?: string[];
  codex_exec?: Record<string, unknown>;
  env?: Record<string, string>;
  base_url?: string;
  api_key?: string;
  api_key_ref?: string;
  list_args?: string[];
  tui_args?: string[];
  output_path?: string;
  timeout?: number;
  retries?: number;
  max_retries?: number;
  api_path?: string;
  models_path?: string;
  headers?: Record<string, string>;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  max_context_tokens?: number;
  max_output_tokens?: number;
  model?: string;
  default_model?: string;
  thinking_mode?: boolean;
  streaming?: boolean;
  sdk_params?: SDKParams;
  request_overrides?: RequestOverrides;
  cli_mode?: CLIMode;
  thinking_extraction?: {
    enabled: boolean;
    patterns: string[];
    confidence_threshold: number;
  };
  model_specific?: ModelSpecificConfig;
  [key: string]: unknown;
}

// Simple Provider (for UI)
export interface SimpleProvider {
  id: string;
  name: string;
  kind: ProviderKind;
  conn: ProviderConnection;
  cliMode?: CLIMode;
  modelId: string;
  status: ProviderStatus;
  lastError?: string;
  lastTest?: {
    at: string;
    latencyMs?: number;
    usage?: { totalTokens?: number; estimated?: boolean };
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
}

// Validation Result
export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
  normalized_config?: ProviderConfig;
}

export type ProviderValidateFn = () => ValidationResult;

// Role Config
export interface RoleConfig {
  provider_id?: string;
  model?: string;
  profile?: string;
}

// Role Requirements
export interface RoleRequirement {
  requires_thinking?: boolean;
  min_confidence?: number;
  error_message?: string;
}

// LLM Config
export interface LLMConfig {
  schema_version: number;
  providers: Record<string, ProviderConfig>;
  roles: Record<string, RoleConfig>;
  policies?: {
    required_ready_roles?: string[];
    test_required_suites?: string[];
    role_requirements?: Record<string, RoleRequirement>;
  };
}

// LLM Status


// LLM Status Role (Rich)
export interface LLMStatusRole {
  provider_id?: string;
  model?: string;
  profile?: string;
  ready?: boolean;
  grade?: string;
  last_run_id?: string | null;
  timestamp?: string | null;
  suites?: Record<string, unknown> | null;
  runtime_supported?: boolean;
}

// LLM Status Provider (Rich)
export interface LLMStatusProvider {
  ready?: boolean | null;
  grade?: string;
  last_run_id?: string | null;
  timestamp?: string | null;
  suites?: Record<string, unknown> | null;
  model?: string | null;
  role?: string | null;
}

// LLM Status (Rich)
export interface LLMStatus {
  state: string;
  required_ready_roles: string[];
  blocked_roles: string[];
  unsupported_roles: string[];
  roles: Record<string, LLMStatusRole>;
  providers?: Record<string, LLMStatusProvider>;
  last_updated?: string | null;
}

export interface LLMStatusSuite {
  status: 'pass' | 'fail' | 'skip';
  note?: string;
  latency_ms?: number;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    estimated?: boolean;
  };
}

// Provider Settings Props
export interface ProviderSettingsProps {
  providerId?: string;
  provider: {
    type: string;
    name: string;
    command?: string;
    args?: string[];
    env?: Record<string, string>;
    base_url?: string;
    api_key?: string;
    timeout?: number;
    retries?: number;
    temperature?: number;
    max_tokens?: number;
    max_context_tokens?: number;
    max_output_tokens?: number;
    [key: string]: unknown;
  };
  onUpdate: (updates: Partial<ProviderConfig>) => void;
  onValidate: ProviderValidateFn;
  children?: React.ReactNode;
}

// Utility Types
export interface ProviderLabels {
  [key: string]: string;
}

export interface StatusColors {
  [key: string]: string;
}

export interface StatusBadges {
  [key: string]: string;
}

// Helper Functions
export const isCLIProvider = (kind: ProviderKind): boolean => {
  return kind === PROVIDER_KINDS.CODEX_CLI || kind === PROVIDER_KINDS.GEMINI_CLI;
};

export const isCLIProviderType = (providerType?: string): providerType is CLIConnectionKind => {
  return providerType === PROVIDER_KINDS.CODEX_CLI || providerType === PROVIDER_KINDS.GEMINI_CLI;
};

export const requiresApiKeyForType = (providerType?: string): boolean => {
  if (!providerType) return true;
  if (providerType === PROVIDER_KINDS.OLLAMA) return false;
  return !isCLIProviderType(providerType);
};

export const requiresApiKey = requiresApiKeyForType;

export const usesBaseUrlForType = (providerType?: string): boolean => {
  return (
    providerType === PROVIDER_KINDS.CODEX_SDK ||
    providerType === PROVIDER_KINDS.OPENAI_COMPAT ||
    providerType === PROVIDER_KINDS.ANTHROPIC_COMPAT ||
    providerType === PROVIDER_KINDS.MINIMAX ||
    providerType === PROVIDER_KINDS.GEMINI_API ||
    providerType === PROVIDER_KINDS.KIMI
  );
};

export const isAPIProvider = (kind: ProviderKind): boolean => {
  return !isCLIProvider(kind);
};

export const isCodexCLIProvider = (kind: ProviderKind, conn?: ProviderConnection): boolean => {
  if (kind === PROVIDER_KINDS.CODEX_CLI) return true;
  if (kind === PROVIDER_KINDS.GEMINI_CLI && conn && 
      (conn.kind === 'gemini_cli' || conn.kind === 'codex_cli') &&
      conn.command.toLowerCase().includes('codex')) {
    return true;
  }
  return false;
};

export const isGeminiCLIProvider = (kind: ProviderKind, conn?: ProviderConnection): boolean => {
  if (kind === PROVIDER_KINDS.GEMINI_CLI) return true;
  if (kind === PROVIDER_KINDS.CODEX_CLI && conn && 
      (conn.kind === 'codex_cli' || conn.kind === 'gemini_cli') &&
      conn.command.toLowerCase().includes('gemini')) {
    return true;
  }
  return false;
};

export const isAgentProvider = (info: ProviderInfo): boolean => {
  return info.provider_category === PROVIDER_CATEGORIES.AGENT;
};

export const isLLMProvider = (info: ProviderInfo): boolean => {
  return info.provider_category === PROVIDER_CATEGORIES.LLM;
};

export const requiresAPIKey = (kind: ProviderKind): boolean => {
  return !isCLIProvider(kind);
};

export const supportsTUI = (kind: ProviderKind): boolean => {
  return isCLIProvider(kind);
};

export const supportsAPIListing = (kind: ProviderKind): boolean => {
  return isAPIProvider(kind);
};

// Connection type helpers
export const isCLIConnection = (conn: ProviderConnection): conn is CLIConnection => {
  return conn.kind === 'codex_cli' || conn.kind === 'gemini_cli';
};

export const isHTTPConnection = (conn: ProviderConnection): conn is HTTPConnection => {
  return conn.kind === 'http';
};

// Provider Classification Constants
export const PROVIDER_LABELS: ProviderLabels = {
  [PROVIDER_KINDS.CODEX_CLI]: 'Codex CLI',
  [PROVIDER_KINDS.CODEX_SDK]: 'Codex SDK',
  [PROVIDER_KINDS.GEMINI_CLI]: 'Gemini CLI',
  [PROVIDER_KINDS.OLLAMA]: 'Ollama',
  [PROVIDER_KINDS.OPENAI_COMPAT]: 'OpenAI',
  [PROVIDER_KINDS.ANTHROPIC_COMPAT]: 'Anthropic-compatible',
  [PROVIDER_KINDS.CUSTOM_HTTPS]: 'Custom HTTPS',
  [PROVIDER_KINDS.MINIMAX]: 'MiniMax',
  [PROVIDER_KINDS.GEMINI_API]: 'Gemini API',
  [PROVIDER_KINDS.KIMI]: 'Kimi'
};

export const STATUS_COLORS: StatusColors = {
  [PROVIDER_STATUS.UNTESTED]: 'text-gray-400',
  [PROVIDER_STATUS.TESTING]: 'text-blue-400',
  [PROVIDER_STATUS.READY]: 'text-emerald-400',
  [PROVIDER_STATUS.FAILED]: 'text-red-400'
};

export const STATUS_BADGES: StatusBadges = {
  [PROVIDER_STATUS.UNTESTED]: 'bg-gray-500/20 text-gray-300 border-gray-500/30',
  [PROVIDER_STATUS.TESTING]: 'bg-blue-500/20 text-blue-200 border-blue-500/30 animate-pulse',
  [PROVIDER_STATUS.READY]: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
  [PROVIDER_STATUS.FAILED]: 'bg-red-500/20 text-red-200 border-red-500/30'
};

export const INTERVIEW_BADGES: StatusBadges = {
  [INTERVIEW_STATUS.NOT_TESTED]: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  [INTERVIEW_STATUS.PASSED]: 'bg-green-500/20 text-green-300 border-green-500/30',
  [INTERVIEW_STATUS.FAILED]: 'bg-red-500/20 text-red-300 border-red-500/30'
};

// ============================================================================
// Unified Data Model (Single Source of Truth)
// ============================================================================

// --- Shared Core Types ---

export interface Position {
  x: number;
  y: number;
}

export interface NodeStyle {
  color?: string;
  icon?: string;
  size?: 'small' | 'medium' | 'large';
  shape?: 'circle' | 'rectangle';
}

export interface EdgeMetadata {
  style?: 'straight' | 'curved' | 'step';
  label?: string;
  color?: string;
}

export interface ViewportState {
  x: number;
  y: number;
  zoom: number;
}

export interface InterviewReference {
  id: string;
  timestamp: string;
  status: 'passed' | 'failed';
  score?: number;
}

export interface CapabilityAssessment {
  score: number;
  confidence: number;
  last_assessed: string;
  notes?: string;
}

export interface TestHistory {
  total_runs: number;
  success_rate: number;
  last_run?: string;
}

// --- Unified Entities ---

export interface UnifiedProviderAttributes {
  // Core
  cost_class: CostClass;
  provider_category: ProviderCategory;
  
  // Status
  connectivity_status: 'unknown' | 'success' | 'failed' | 'testing';
  last_test_timestamp?: string;
  
  // Capabilities
  supported_features: string[];
  thinking_capability?: {
    supported: boolean;
    confidence?: number;
    format?: string;
  };
  
  // Visual Extension
  visual?: {
    position?: Position;
    style?: NodeStyle;
    icon?: string;
    color?: string;
  };
  
  // Testing Extension
  testing?: {
    last_interview?: InterviewReference;
    capability_scores?: Record<string, number>;
    test_history?: TestHistory;
  };
}

export interface UnifiedProvider {
  id: string;
  name: string;
  type: string;
  config: ProviderConfig;
  attributes: UnifiedProviderAttributes;
}

export interface UnifiedRoleAttributes {
  // Status
  readiness_status: 'unknown' | 'ready' | 'not_ready';
  last_interview?: InterviewReference;
  
  // Visual Extension
  visual?: {
    position?: Position;
    style?: NodeStyle;
    color?: string;
  };
  
  // Testing Extension
  testing?: {
    interview_history?: InterviewReference[];
    capability_assessments?: Record<string, CapabilityAssessment>;
  };
}

export interface UnifiedRole {
  id: string;
  name: string;
  description: string;
  
  requirements: {
    requires_thinking: boolean;
    min_confidence: number;
    preferred_capabilities: string[];
  };
  
  assignment?: {
    provider_id: string;
    model: string;
    assigned_at: string;
    confidence: number;
  };
  
  attributes: UnifiedRoleAttributes;
}

// --- Extensions & Relations ---

export interface UnifiedRelationships {
  provider_to_models: Record<string, string[]>;
  role_to_provider_model: Record<string, {
    provider_id: string;
    model: string;
    confidence?: number;
  }>;
  model_connectivity: Record<string, {
    status: 'success' | 'failed' | 'unknown';
    last_checked: string;
    latency_ms?: number;
  }>;
}

export interface InterviewResult {
  id: string;
  role_id: string;
  provider_id: string;
  model: string;
  session_type: 'interactive' | 'auto';
  status: 'passed' | 'failed' | 'running' | 'cancelled';
  start_time: string;
  end_time?: string;
  overall_score?: number;
  summary?: {
    total_questions: number;
    passed_questions: number;
    recommendation: string;
  };
}

export interface ConnectivityTest {
  id: string;
  provider_id: string;
  model: string;
  timestamp: string;
  status: 'success' | 'failed' | 'running';
  latency_ms?: number;
  error?: string;
}

export interface CapabilityScore {
  score: number;
  confidence: number;
  last_updated: string;
}

export interface TestPreferences {
  auto_run_connectivity: boolean;
  auto_run_interviews: boolean;
  concurrency: number;
}

export interface UnifiedExtensions {
  // Visual View Data
  visual?: {
    node_positions: Record<string, Position>;
    node_styles: Record<string, NodeStyle>;
    edge_metadata: Record<string, EdgeMetadata>;
    viewport_state: ViewportState;
  };
  
  // Testing View Data
  testing?: {
    interview_results: Record<string, InterviewResult>;
    connectivity_tests: Record<string, ConnectivityTest>;
    capability_scores: Record<string, CapabilityScore>;
    test_preferences: TestPreferences;
  };
  
  // UI State
  ui?: {
    expanded_nodes: string[];
    selected_nodes: string[];
    view_preferences: {
      show_minimap: boolean;
      show_grid: boolean;
      theme: 'dark' | 'light' | 'cyberpunk';
    };
  };
}

export interface UnifiedMetadata {
  created_at: string;
  updated_at: string;
  version: string;
  integrity_hash: string;
}

// --- Root Configuration ---

export interface UnifiedLlmConfig {
  schema_version: number;
  
  // Entities
  providers: Record<string, UnifiedProvider>;
  roles: Record<string, UnifiedRole>;
  
  // Relations
  relationships: UnifiedRelationships;
  
  // Extensions (View-specific data)
  extensions: UnifiedExtensions;
  
  // Metadata
  metadata: UnifiedMetadata;
}
