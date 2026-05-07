/**
 * LLM Configuration Service
 *
 * 封装所有LLM配置相关的API调用
 */

import { apiFetch } from '@/api';
import { apiGet, apiPost } from './apiClient';
import type { ApiResult } from './api.types';
import type {
  LLMConfigResponse,
  LLMStatusResponse,
  ProviderConfig,
  RoleConfig,
} from './api.types';

export type {
  LLMConfigResponse,
  LLMStatusResponse,
  ProviderConfig,
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

export interface ChatMessageRequest {
  message: string;
  context?: Record<string, unknown>;
}

/**
 * 获取角色对话状态
 */
export async function getRoleChatStatus(role: string): Promise<ApiResult<ChatStatus>> {
  return apiGet<ChatStatus>(`/v2/role/${role}/chat/status`, '获取对话状态失败');
}

/**
 * 发送角色对话消息（流式）
 * 返回Response对象，需要自行处理流式读取
 */
export async function sendRoleChatMessage(
  role: string,
  request: ChatMessageRequest,
  signal?: AbortSignal
): Promise<Response> {
  return apiFetch(`/v2/role/${role}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  });
}

// ============================================================================
// Stream Processing Helpers
// ============================================================================

export interface ChatStreamEvent {
  type: 'thinking_chunk' | 'content_chunk' | 'complete' | 'error';
  data?: {
    content?: string;
    response?: string;
    message?: string;
    // Backward compatibility fields
    complete?: string;
    error?: string;
  };
}

/**
 * 解析SSE数据行
 */
export function parseSSEData(line: string): ChatStreamEvent | null {
  if (!line.startsWith('data: ')) return null;

  const data = line.slice(6);
  if (data === '[DONE]') return null;

  try {
    return JSON.parse(data) as ChatStreamEvent;
  } catch {
    // Non-JSON data, treat as raw content
    return {
      type: 'content_chunk',
      data: { content: data },
    };
  }
}

/**
 * 创建SSE流读取器
 */
export function createStreamReader(response: Response): ReadableStreamDefaultReader<Uint8Array> | null {
  return response.body?.getReader() ?? null;
}
