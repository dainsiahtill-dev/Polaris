/**
 * LLM Configuration Service
 *
 * 封装所有LLM配置相关的API调用
 */
import { apiGet, apiPost } from './apiClient';
// ============================================================================
// LLM Config API
// ============================================================================
/**
 * 获取LLM配置
 */
export async function getLLMConfig() {
    return apiGet('/v2/llm/config', '读取LLM配置失败');
}
/**
 * 保存LLM配置
 */
export async function saveLLMConfig(config) {
    return apiPost('/v2/llm/config', config, '保存LLM配置失败');
}
/**
 * 获取LLM状态
 */
export async function getLLMStatus() {
    return apiGet('/v2/llm/status', '读取LLM状态失败');
}
function workspaceQuerySuffix(workspace = '') {
    const value = String(workspace || '').trim();
    return value ? `?workspace=${encodeURIComponent(value)}` : '';
}
/**
 * 获取角色对话状态
 */
export async function getRoleChatStatus(role, workspace = '') {
    return apiGet(`/v2/role/${role}/chat/status${workspaceQuerySuffix(workspace)}`, '获取对话状态失败');
}
