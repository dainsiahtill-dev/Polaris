/**
 * Conversation API Service - 对话会话管理
 *
 * 提供对话的创建、查询、更新、删除和消息管理功能。
 * 使用统一的响应处理模式，返回类型化结果。
 */
import { apiFetch } from '@/api';
// ============================================================================
// Response Handlers
// ============================================================================
/**
 * Unified response handler for API calls
 * Converts Response to typed result with error handling
 */
async function handleResponse(response, errorMessage) {
    if (!response.ok) {
        let detail = errorMessage;
        try {
            const payload = (await response.json());
            detail = payload.detail || payload.error || payload.message || errorMessage;
        }
        catch {
            // Use default error message
        }
        throw new Error(detail);
    }
    const data = (await response.json());
    return data;
}
// ============================================================================
// Conversation API Methods
// ============================================================================
/**
 * 创建新对话
 */
export async function createConversation(data) {
    const res = await apiFetch('/v2/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    return handleResponse(res, '创建对话失败');
}
/**
 * 获取对话列表
 * 后端返回 {conversations: [], total: n} 包装对象
 */
export async function listConversations(params) {
    const searchParams = new URLSearchParams();
    if (params?.role)
        searchParams.set('role', params.role);
    if (params?.workspace)
        searchParams.set('workspace', params.workspace);
    if (params?.limit)
        searchParams.set('limit', params.limit.toString());
    if (params?.offset)
        searchParams.set('offset', params.offset.toString());
    const res = await apiFetch(`/v2/conversations?${searchParams}`);
    const data = await handleResponse(res, '获取对话列表失败');
    return {
        conversations: data.conversations || [],
        total: data.total || 0,
    };
}
/**
 * 获取单个对话详情
 */
export async function getConversation(conversationId, includeMessages = true) {
    const searchParams = new URLSearchParams();
    if (includeMessages)
        searchParams.set('include_messages', 'true');
    const res = await apiFetch(`/v2/conversations/${conversationId}?${searchParams}`);
    return handleResponse(res, '获取对话详情失败');
}
/**
 * 更新对话信息
 */
export async function updateConversation(conversationId, data) {
    const res = await apiFetch(`/v2/conversations/${conversationId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    return handleResponse(res, '更新对话失败');
}
/**
 * 删除对话
 */
export async function deleteConversation(conversationId, hard = false) {
    const res = await apiFetch(`/v2/conversations/${conversationId}?hard=${hard}`, {
        method: 'DELETE',
    });
    return handleResponse(res, '删除对话失败');
}
/**
 * 添加消息到对话
 */
export async function addMessage(conversationId, data) {
    const res = await apiFetch(`/v2/conversations/${conversationId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    return handleResponse(res, '添加消息失败');
}
/**
 * 批量添加消息
 */
export async function addMessagesBatch(conversationId, messages) {
    const res = await apiFetch(`/v2/conversations/${conversationId}/messages/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(messages),
    });
    return handleResponse(res, '批量添加消息失败');
}
/**
 * 获取对话消息列表
 */
export async function listMessages(conversationId, params) {
    const searchParams = new URLSearchParams();
    if (params?.limit)
        searchParams.set('limit', params.limit.toString());
    if (params?.offset)
        searchParams.set('offset', params.offset.toString());
    const res = await apiFetch(`/v2/conversations/${conversationId}/messages?${searchParams}`);
    return handleResponse(res, '获取消息列表失败');
}
/**
 * 删除单条消息
 */
export async function deleteMessage(conversationId, messageId) {
    const res = await apiFetch(`/v2/conversations/${conversationId}/messages/${messageId}`, {
        method: 'DELETE',
    });
    return handleResponse(res, '删除消息失败');
}
/**
 * 保存完整对话（用于页面刷新恢复）
 */
export async function saveFullConversation(conversationId, role, workspace, context, messages) {
    const res = await apiFetch(`/v2/conversations/${conversationId}/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            role,
            workspace,
            context,
            messages,
        }),
    });
    return handleResponse(res, '保存对话失败');
}
