/**
 * Conversation API Service - 对话会话管理
 *
 * 提供对话的创建、查询、更新、删除和消息管理功能。
 * 使用统一的响应处理模式，返回类型化结果。
 */

import { apiFetch } from '@/api';

export type MessageRole = 'user' | 'assistant' | 'system';
export type DialogueRole = 'pm' | 'architect' | 'director' | 'qa';

// ============================================================================
// Type Definitions
// ============================================================================

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  sequence: number;
  role: MessageRole;
  content: string;
  thinking?: string;
  meta?: Record<string, unknown>;
  created_at: string;
}

export interface Conversation {
  id: string;
  title?: string;
  role: DialogueRole;
  workspace?: string;
  context_config: Record<string, unknown>;
  message_count: number;
  created_at: string;
  updated_at: string;
  messages?: ConversationMessage[];
}

export interface ConversationListResponse {
  conversations: Conversation[];
  total: number;
}

export interface CreateConversationRequest {
  title?: string;
  role: DialogueRole;
  workspace?: string;
  context_config?: Record<string, unknown>;
  initial_message?: {
    role: MessageRole;
    content: string;
    thinking?: string;
    meta?: Record<string, unknown>;
  };
}

export interface UpdateConversationRequest {
  title?: string;
  context_config?: Record<string, unknown>;
}

export interface AddMessageRequest {
  role: MessageRole;
  content: string;
  thinking?: string;
  meta?: Record<string, unknown>;
}

// ============================================================================
// Response Handlers
// ============================================================================

/**
 * Unified response handler for API calls
 * Converts Response to typed result with error handling
 */
async function handleResponse<T>(response: Response, errorMessage: string): Promise<T> {
  if (!response.ok) {
    let detail = errorMessage;
    try {
      const payload = (await response.json()) as { detail?: string; error?: string; message?: string };
      detail = payload.detail || payload.error || payload.message || errorMessage;
    } catch {
      // Use default error message
    }
    throw new Error(detail);
  }

  const data = (await response.json()) as T;
  return data;
}

// ============================================================================
// Conversation API Methods
// ============================================================================

/**
 * 创建新对话
 */
export async function createConversation(
  data: CreateConversationRequest
): Promise<Conversation> {
  const res = await apiFetch('/v2/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

  return handleResponse<Conversation>(res, '创建对话失败');
}

/**
 * 获取对话列表
 * 后端返回 {conversations: [], total: n} 包装对象
 */
export async function listConversations(params?: {
  role?: DialogueRole;
  workspace?: string;
  limit?: number;
  offset?: number;
}): Promise<ConversationListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.role) searchParams.set('role', params.role);
  if (params?.workspace) searchParams.set('workspace', params.workspace);
  if (params?.limit) searchParams.set('limit', params.limit.toString());
  if (params?.offset) searchParams.set('offset', params.offset.toString());

  const res = await apiFetch(`/v2/conversations?${searchParams}`);

  const data = await handleResponse<ConversationListResponse>(res, '获取对话列表失败');
  return {
    conversations: data.conversations || [],
    total: data.total || 0,
  };
}

/**
 * 获取单个对话详情
 */
export async function getConversation(
  conversationId: string,
  includeMessages: boolean = true
): Promise<Conversation> {
  const searchParams = new URLSearchParams();
  if (includeMessages) searchParams.set('include_messages', 'true');

  const res = await apiFetch(
    `/v2/conversations/${conversationId}?${searchParams}`
  );

  return handleResponse<Conversation>(res, '获取对话详情失败');
}

/**
 * 更新对话信息
 */
export async function updateConversation(
  conversationId: string,
  data: UpdateConversationRequest
): Promise<Conversation> {
  const res = await apiFetch(`/v2/conversations/${conversationId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

  return handleResponse<Conversation>(res, '更新对话失败');
}

/**
 * 删除对话
 */
export async function deleteConversation(
  conversationId: string,
  hard: boolean = false
): Promise<{ ok: boolean; deleted_id: string }> {
  const res = await apiFetch(
    `/v2/conversations/${conversationId}?hard=${hard}`,
    {
      method: 'DELETE',
    }
  );

  return handleResponse<{ ok: boolean; deleted_id: string }>(res, '删除对话失败');
}

/**
 * 添加消息到对话
 */
export async function addMessage(
  conversationId: string,
  data: AddMessageRequest
): Promise<ConversationMessage> {
  const res = await apiFetch(
    `/v2/conversations/${conversationId}/messages`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }
  );

  return handleResponse<ConversationMessage>(res, '添加消息失败');
}

/**
 * 批量添加消息
 */
export async function addMessagesBatch(
  conversationId: string,
  messages: AddMessageRequest[]
): Promise<{ ok: boolean; added_count: number }> {
  const res = await apiFetch(
    `/v2/conversations/${conversationId}/messages/batch`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(messages),
    }
  );

  return handleResponse<{ ok: boolean; added_count: number }>(res, '批量添加消息失败');
}

/**
 * 获取对话消息列表
 */
export async function listMessages(
  conversationId: string,
  params?: { limit?: number; offset?: number }
): Promise<ConversationMessage[]> {
  const searchParams = new URLSearchParams();
  if (params?.limit) searchParams.set('limit', params.limit.toString());
  if (params?.offset) searchParams.set('offset', params.offset.toString());

  const res = await apiFetch(
    `/v2/conversations/${conversationId}/messages?${searchParams}`
  );

  return handleResponse<ConversationMessage[]>(res, '获取消息列表失败');
}

/**
 * 删除单条消息
 */
export async function deleteMessage(
  conversationId: string,
  messageId: string
): Promise<{ ok: boolean; deleted_id: string }> {
  const res = await apiFetch(
    `/v2/conversations/${conversationId}/messages/${messageId}`,
    {
      method: 'DELETE',
    }
  );

  return handleResponse<{ ok: boolean; deleted_id: string }>(res, '删除消息失败');
}

/**
 * 保存完整对话（用于页面刷新恢复）
 */
export async function saveFullConversation(
  conversationId: string,
  role: DialogueRole,
  workspace: string,
  context: Record<string, unknown>,
  messages: Array<{
    role: MessageRole;
    content: string;
    thinking?: string;
  }>
): Promise<{ ok: boolean }> {
  const res = await apiFetch(
    `/v2/conversations/${conversationId}/save`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        role,
        workspace,
        context,
        messages,
      }),
    }
  );

  return handleResponse<{ ok: boolean }>(res, '保存对话失败');
}
