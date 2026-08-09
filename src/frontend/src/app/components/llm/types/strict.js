/**
 * Strict Type Definitions for LLM Module
 * 移除所有 any/unknown，使用具体的类型定义
 */
// ============================================================================
// Helper Functions
// ============================================================================
/**
 * 类型守卫：检查是否为有效的 ProviderConfig
 */
export function isValidProviderConfig(config) {
    if (!config || typeof config !== "object")
        return false;
    const c = config;
    return typeof c.type === "string" && c.type.length > 0;
}
/**
 * 类型守卫：检查是否为 CLI Provider
 */
export function isCLIProviderConfig(config) {
    return config.type === "codex_cli" || config.type === "gemini_cli";
}
/**
 * 类型守卫：检查是否为 HTTP Provider
 */
export function isHTTPProviderConfig(config) {
    return !isCLIProviderConfig(config);
}
/**
 * 获取 Provider 的显示名称
 */
export function getProviderDisplayName(config) {
    return config.name || config.type;
}
/**
 * 获取 Provider 的模型
 */
export function getProviderModel(config) {
    return config.model || config.defaultModel;
}
