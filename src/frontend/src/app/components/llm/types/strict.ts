/**
 * Strict Type Definitions for LLM Module
 * 移除所有 any/unknown，使用具体的类型定义
 */

import type { ProviderKind, ProviderStatus, CostClass, InterviewStatus, CLIMode } from '../types';

// ============================================================================
// Provider Configuration Types
// ============================================================================

/** SDK 特定参数 */
export interface SDKParams {
  timeout?: number;
  maxRetries?: number;
  /** 是否启用流式响应 */
  streaming?: boolean;
  /** 思考模式 */
  thinkingMode?: boolean;
}

/** 模型特定配置 */
export interface ModelSpecificConfig {
  temperature?: number;
  topP?: number;
  maxTokens?: number;
  frequencyPenalty?: number;
  presencePenalty?: number;
}

/** 请求覆盖配置 */
export interface RequestOverrides {
  headers?: Record<string, string>;
  queryParams?: Record<string, string>;
  bodyFields?: Record<string, unknown>;
}

/** 思考提取配置 */
export interface ThinkingExtractionConfig {
  enabled: boolean;
  patterns: string[];
  confidenceThreshold: number;
}

/** CLI 执行配置 */
export interface CodexExecConfig {
  workingDirectory?: string;
  timeout?: number;
  env?: Record<string, string>;
}

// ============================================================================
// Discriminated Union for Provider Types
// ============================================================================

/** 基础 Provider 配置 */
interface BaseProviderConfig {
  name?: string;
  model?: string;
  defaultModel?: string;
  cliMode?: CLIMode;
}

/** Codex SDK Provider */
export interface CodexSDKProviderConfig extends BaseProviderConfig {
  type: 'codex_sdk';
  baseUrl: string;
  apiKey?: string;
  apiKeyRef?: string;
  sdkParams?: SDKParams;
  modelSpecific?: ModelSpecificConfig;
}

/** Codex CLI Provider */
export interface CodexCLIProviderConfig extends BaseProviderConfig {
  type: 'codex_cli';
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  codexExec?: CodexExecConfig;
  listArgs?: string[];
  tuiArgs?: string[];
  outputPath?: string;
  timeout?: number;
  retries?: number;
  maxRetries?: number;
}

/** Gemini CLI Provider */
export interface GeminiCLIProviderConfig extends BaseProviderConfig {
  type: 'gemini_cli';
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  timeout?: number;
  retries?: number;
}

/** OpenAI Compatible Provider */
export interface OpenAICompatProviderConfig extends BaseProviderConfig {
  type: 'openai_compat';
  baseUrl: string;
  apiKey?: string;
  apiKeyRef?: string;
  apiPath?: string;
  modelsPath?: string;
  headers?: Record<string, string>;
  temperature?: number;
  topP?: number;
  maxTokens?: number;
  requestOverrides?: RequestOverrides;
}

/** Anthropic Compatible Provider */
export interface AnthropicCompatProviderConfig extends BaseProviderConfig {
  type: 'anthropic_compat';
  baseUrl: string;
  apiKey?: string;
  apiKeyRef?: string;
  headers?: Record<string, string>;
  modelSpecific?: ModelSpecificConfig;
  thinkingExtraction?: ThinkingExtractionConfig;
}

/** Gemini API Provider */
export interface GeminiAPIProviderConfig extends BaseProviderConfig {
  type: 'gemini_api';
  apiKey?: string;
  apiKeyRef?: string;
  model?: string;
  temperature?: number;
  maxTokens?: number;
}

/** Ollama Provider */
export interface OllamaProviderConfig extends BaseProviderConfig {
  type: 'ollama';
  baseUrl: string;
  model?: string;
  temperature?: number;
  topP?: number;
  maxTokens?: number;
}

/** MiniMax Provider */
export interface MiniMaxProviderConfig extends BaseProviderConfig {
  type: 'minimax';
  baseUrl: string;
  apiKey?: string;
  apiKeyRef?: string;
  model?: string;
}

/** Kimi Provider */
export interface KimiProviderConfig extends BaseProviderConfig {
  type: 'kimi';
  baseUrl: string;
  apiKey?: string;
  apiKeyRef?: string;
  model?: string;
  temperature?: number;
  maxTokens?: number;
}

/** Custom HTTPS Provider */
export interface CustomHTTPSProviderConfig extends BaseProviderConfig {
  type: 'custom_https';
  baseUrl: string;
  apiKey?: string;
  headers?: Record<string, string>;
}

/** 严格的 Provider Config Union 类型 */
export type ProviderConfigStrict =
  | CodexSDKProviderConfig
  | CodexCLIProviderConfig
  | GeminiCLIProviderConfig
  | OpenAICompatProviderConfig
  | AnthropicCompatProviderConfig
  | GeminiAPIProviderConfig
  | OllamaProviderConfig
  | MiniMaxProviderConfig
  | KimiProviderConfig
  | CustomHTTPSProviderConfig;

/** 带索引签名的宽松版本（用于兼容现有代码） */
export interface ProviderConfigLoose extends Record<string, unknown> {
  type?: string;
  name?: string;
  model?: string;
  default_model?: string;
  base_url?: string;
  api_key?: string;
  api_key_ref?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
}

// ============================================================================
// Visual Graph Types
// ============================================================================

/** 视觉角色节点数据 */
export interface VisualRoleNodeDataStrict {
  kind: 'role';
  roleId: 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr';
  label: string;
  description?: string;
  requiresThinking?: boolean;
  minConfidence?: number;
  readiness?: {
    ready?: boolean;
    grade?: string;
  };
}

/** 视觉 Provider 节点数据 */
export interface VisualProviderNodeDataStrict {
  kind: 'provider';
  providerId: string;
  label: string;
  providerType?: string;
  costClass?: CostClass;
  status?: ProviderStatus;
  modelCount?: number;
}

/** 视觉模型节点数据 */
export interface VisualModelNodeDataStrict {
  kind: 'model';
  providerId: string;
  model: string;
  label: string;
  assignedRoles?: Array<'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr'>;
}

/** 视觉节点数据 Union */
export type VisualNodeDataStrict =
  | VisualRoleNodeDataStrict
  | VisualProviderNodeDataStrict
  | VisualModelNodeDataStrict;

/** 视觉边类型 */
export type VisualEdgeKindStrict = 'provider-to-model' | 'model-to-role';

/** 视觉边数据 */
export interface VisualEdgeDataStrict {
  kind: VisualEdgeKindStrict;
}

/** 节点位置 */
export interface VisualNodePosition {
  x: number;
  y: number;
}

/** 节点状态 */
export interface VisualNodeStateStrict {
  position?: VisualNodePosition;
  expanded?: boolean;
  selected?: boolean;
  hidden?: boolean;
  data?: {
    roleData?: {
      lastInterviewStatus?: 'passed' | 'failed' | 'none';
      lastInterviewTimestamp?: string;
      readinessScore?: number;
    };
    providerData?: {
      connectivityStatus?: 'success' | 'failed' | 'unknown';
      lastTestTimestamp?: string;
      enabledModels?: string[];
    };
    modelData?: {
      assignedRoles?: string[];
      lastUsedTimestamp?: string;
      performanceScore?: number;
    };
  };
}

/** Provider 配置（视觉编辑器用） */
export interface VisualProviderConfig {
  type?: string;
  name?: string;
  default_model?: string;
  manual_models?: string[];
}

/** 视觉图形配置 */
export interface VisualGraphConfigStrict {
  providers: Record<string, VisualProviderConfig>;
  roles: Record<string, {
    provider_id?: string;
    model?: string;
    profile?: string;
  }>;
  visual_layout?: Record<string, VisualNodePosition>;
  visual_node_states?: Record<string, VisualNodeStateStrict>;
  visual_viewport?: {
    x: number;
    y: number;
    zoom: number;
  };
  policies?: {
    role_requirements?: Record<string, {
      requires_thinking?: boolean;
      min_confidence?: number;
      error_message?: string;
    }>;
  };
}

/** 视觉图形状态 */
export interface VisualGraphStatusStrict {
  roles?: Record<string, {
    ready?: boolean;
    grade?: string;
  }>;
}

// ============================================================================
// Test & Interview Types
// ============================================================================

/** 测试事件类型 */
export type TestEventType = 'stdout' | 'stderr' | 'error' | 'result' | 'command' | 'info';

/** 测试事件 */
export interface TestEventStrict {
  type: TestEventType;
  timestamp: string;
  content: string;
  details?: Record<string, unknown>;
}

/** 测试结果 */
export interface TestResultStrict {
  ready?: boolean;
  grade?: string;
  suites?: Array<{
    name: string;
    ok: boolean;
    details?: Record<string, unknown>;
  }>;
}

/** 连通性结果 */
export interface ConnectivityResultStrict {
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

/** 面试报告 */
export interface InterviewSuiteReportStrict {
  status?: string;
  final_score?: number;
  thinking?: {
    supports_thinking?: boolean;
    confidence?: number;
    format?: string;
    thinking_text?: string;
  };
  cases?: Array<Record<string, unknown>>;
  details?: {
    recommendation?: string;
    reason?: string;
    threshold?: number;
  };
}

/** 实时思考事件 */
export type RealtimeThinkingKind = 'reasoning' | 'command_execution' | 'agent_message';

export interface RealtimeThinkingEventStrict {
  id: string;
  kind: RealtimeThinkingKind;
  timestamp: string;
  text?: string;
  command?: string;
  output?: string;
  status?: string;
  exitCode?: number | null;
  thinking?: string | null;
  answer?: string | null;
  raw?: string;
}

// ============================================================================
// Simple Provider Types
// ============================================================================

/** CLI 连接 */
export interface CLIConnectionStrict {
  kind: 'codex_cli' | 'gemini_cli';
  command: string;
  args?: string[];
  env?: Record<string, string>;
}

/** HTTP 连接 */
export interface HTTPConnectionStrict {
  kind: 'http';
  baseUrl: string;
  apiKey?: string;
}

/** Provider 连接 Union */
export type ProviderConnectionStrict = CLIConnectionStrict | HTTPConnectionStrict;

/** Simple Provider（UI 用） */
export interface SimpleProviderStrict {
  id: string;
  name: string;
  kind: ProviderKind;
  conn: ProviderConnectionStrict;
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
}

// ============================================================================
// Role & Policy Types
// ============================================================================

/** 角色 ID */
export type RoleIdStrict = 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr';

/** 角色配置 */
export interface RoleConfigStrict {
  provider_id?: string;
  model?: string;
  profile?: string;
}

/** 角色要求 */
export interface RoleRequirementStrict {
  requires_thinking?: boolean;
  min_confidence?: number;
  error_message?: string;
}

/** 角色元数据 */
export interface RoleMetaStrict {
  label: string;
  description: string;
  badge: string;
}

// ============================================================================
// Validation Types
// ============================================================================

/** 验证错误级别 */
export type ValidationSeverity = 'error' | 'warning' | 'info';

/** 验证问题 */
export interface ValidationIssue {
  field: string;
  message: string;
  severity: ValidationSeverity;
  code?: string;
}

/** 验证结果 */
export interface ValidationResultStrict {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  normalizedConfig?: ProviderConfigStrict;
}

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * 类型守卫：检查是否为有效的 ProviderConfig
 */
export function isValidProviderConfig(config: unknown): config is ProviderConfigStrict {
  if (!config || typeof config !== 'object') return false;
  const c = config as Record<string, unknown>;
  return typeof c.type === 'string' && c.type.length > 0;
}

/**
 * 类型守卫：检查是否为 CLI Provider
 */
export function isCLIProviderConfig(config: ProviderConfigStrict): config is CodexCLIProviderConfig | GeminiCLIProviderConfig {
  return config.type === 'codex_cli' || config.type === 'gemini_cli';
}

/**
 * 类型守卫：检查是否为 HTTP Provider
 */
export function isHTTPProviderConfig(config: ProviderConfigStrict): config is 
  | CodexSDKProviderConfig 
  | OpenAICompatProviderConfig 
  | AnthropicCompatProviderConfig
  | GeminiAPIProviderConfig
  | OllamaProviderConfig
  | MiniMaxProviderConfig
  | KimiProviderConfig
  | CustomHTTPSProviderConfig {
  return !isCLIProviderConfig(config);
}

/**
 * 获取 Provider 的显示名称
 */
export function getProviderDisplayName(config: ProviderConfigStrict): string {
  return config.name || config.type;
}

/**
 * 获取 Provider 的模型
 */
export function getProviderModel(config: ProviderConfigStrict): string | undefined {
  return config.model || config.defaultModel;
}
