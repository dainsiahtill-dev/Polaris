/**
 * LLM Configuration Service
 *
 * 封装所有LLM配置相关的API调用
 */

import { apiGet, apiPost } from './apiClient';
import type { ApiResult } from './api.types';
import type {
  LLMConfigResponse,
  LLMStatusResponse,
  ProviderConfig,
  RoleChatRole,
  RoleConfig,
} from './api.types';

export type {
  LLMConfigResponse,
  LLMStatusResponse,
  ProviderConfig,
  RoleChatRole,
  RoleConfig,
} from './api.types';

// ============================================================================
// LLM Config API
// ============================================================================

/**
 * 获取LLM配置
 */
export async function getLLMConfig(): Promise<ApiResult<LLMConfigResponse>> {
  return apiGet<LLMConfigResponse>('/v2/llm/config', '读取LLM配置失败');
}

/**
 * 保存LLM配置
 */
export async function saveLLMConfig(config: LLMConfigResponse): Promise<ApiResult<LLMConfigResponse>> {
  return apiPost<LLMConfigResponse>('/v2/llm/config', config, '保存LLM配置失败');
}

/**
 * 获取LLM状态
 */
export async function getLLMStatus(): Promise<ApiResult<LLMStatusResponse>> {
  return apiGet<LLMStatusResponse>('/v2/llm/status', '读取LLM状态失败');
}

// ============================================================================
// Role Chat API
// ============================================================================

export interface ChatStatus {
  ready: boolean;
  error?: string;
  role?: string;
  role_config?: {
    provider_id: string;
    model: string;
    profile?: string;
  };
  provider_type?: string;
  debug?: Record<string, unknown>;
}

function workspaceQuerySuffix(workspace = ''): string {
  const value = String(workspace || '').trim();
  return value ? `?workspace=${encodeURIComponent(value)}` : '';
}

/**
 * 获取角色对话状态
 */
export async function getRoleChatStatus(role: RoleChatRole, workspace = ''): Promise<ApiResult<ChatStatus>> {
  return apiGet<ChatStatus>(`/v2/role/${role}/chat/status${workspaceQuerySuffix(workspace)}`, '获取对话状态失败');
}
