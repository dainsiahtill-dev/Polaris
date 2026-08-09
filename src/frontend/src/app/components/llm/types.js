// Unified LLM Provider Types
// This file centralizes all type definitions to avoid duplication and inconsistency
// Provider Categories
export const PROVIDER_CATEGORIES = {
    AGENT: "AGENT",
    LLM: "LLM",
};
// Provider Kinds (specific provider types)
export const PROVIDER_KINDS = {
    CODEX_CLI: "codex_cli",
    CODEX_SDK: "codex_sdk",
    GEMINI_CLI: "gemini_cli",
    OLLAMA: "ollama",
    OPENAI_COMPAT: "openai_compat",
    ANTHROPIC_COMPAT: "anthropic_compat",
    CUSTOM_HTTPS: "custom_https",
    MINIMAX: "minimax",
    GEMINI_API: "gemini_api",
    KIMI: "kimi",
};
// CLI Modes
export const CLI_MODES = {
    TUI: "tui",
    HEADLESS: "headless",
};
// Provider Status
export const PROVIDER_STATUS = {
    UNTESTED: "untested",
    TESTING: "testing",
    READY: "ready",
    FAILED: "failed",
};
// Cost Classes
export const COST_CLASSES = {
    LOCAL: "LOCAL",
    FIXED: "FIXED",
    METERED: "METERED",
};
// Interview Status
export const INTERVIEW_STATUS = {
    NOT_TESTED: "not_tested",
    PASSED: "passed",
    FAILED: "failed",
};
// Model Listing Methods
export const MODEL_LISTING_METHODS = {
    API: "API",
    TUI: "TUI",
    NONE: "NONE",
};
// Helper Functions
export const isCLIProvider = (kind) => {
    return (kind === PROVIDER_KINDS.CODEX_CLI || kind === PROVIDER_KINDS.GEMINI_CLI);
};
export const isCLIProviderType = (providerType) => {
    return (providerType === PROVIDER_KINDS.CODEX_CLI ||
        providerType === PROVIDER_KINDS.GEMINI_CLI);
};
export const requiresApiKeyForType = (providerType) => {
    if (!providerType)
        return true;
    if (providerType === PROVIDER_KINDS.OLLAMA)
        return false;
    return !isCLIProviderType(providerType);
};
export const requiresApiKey = requiresApiKeyForType;
export const usesBaseUrlForType = (providerType) => {
    return (providerType === PROVIDER_KINDS.CODEX_SDK ||
        providerType === PROVIDER_KINDS.OPENAI_COMPAT ||
        providerType === PROVIDER_KINDS.ANTHROPIC_COMPAT ||
        providerType === PROVIDER_KINDS.MINIMAX ||
        providerType === PROVIDER_KINDS.GEMINI_API ||
        providerType === PROVIDER_KINDS.KIMI);
};
export const isAPIProvider = (kind) => {
    return !isCLIProvider(kind);
};
export const isCodexCLIProvider = (kind, conn) => {
    if (kind === PROVIDER_KINDS.CODEX_CLI)
        return true;
    if (kind === PROVIDER_KINDS.GEMINI_CLI &&
        conn &&
        (conn.kind === "gemini_cli" || conn.kind === "codex_cli") &&
        conn.command.toLowerCase().includes("codex")) {
        return true;
    }
    return false;
};
export const isGeminiCLIProvider = (kind, conn) => {
    if (kind === PROVIDER_KINDS.GEMINI_CLI)
        return true;
    if (kind === PROVIDER_KINDS.CODEX_CLI &&
        conn &&
        (conn.kind === "codex_cli" || conn.kind === "gemini_cli") &&
        conn.command.toLowerCase().includes("gemini")) {
        return true;
    }
    return false;
};
export const isAgentProvider = (info) => {
    return info.provider_category === PROVIDER_CATEGORIES.AGENT;
};
export const isLLMProvider = (info) => {
    return info.provider_category === PROVIDER_CATEGORIES.LLM;
};
export const requiresAPIKey = (kind) => {
    return !isCLIProvider(kind);
};
export const supportsTUI = (kind) => {
    return isCLIProvider(kind);
};
export const supportsAPIListing = (kind) => {
    return isAPIProvider(kind);
};
// Connection type helpers
export const isCLIConnection = (conn) => {
    return conn.kind === "codex_cli" || conn.kind === "gemini_cli";
};
export const isHTTPConnection = (conn) => {
    return conn.kind === "http";
};
// Provider Classification Constants
export const PROVIDER_LABELS = {
    [PROVIDER_KINDS.CODEX_CLI]: "Codex CLI",
    [PROVIDER_KINDS.CODEX_SDK]: "Codex SDK",
    [PROVIDER_KINDS.GEMINI_CLI]: "Gemini CLI",
    [PROVIDER_KINDS.OLLAMA]: "Ollama",
    [PROVIDER_KINDS.OPENAI_COMPAT]: "OpenAI",
    [PROVIDER_KINDS.ANTHROPIC_COMPAT]: "Anthropic-compatible",
    [PROVIDER_KINDS.CUSTOM_HTTPS]: "Custom HTTPS",
    [PROVIDER_KINDS.MINIMAX]: "MiniMax",
    [PROVIDER_KINDS.GEMINI_API]: "Gemini API",
    [PROVIDER_KINDS.KIMI]: "Kimi",
};
export const STATUS_COLORS = {
    [PROVIDER_STATUS.UNTESTED]: "text-gray-400",
    [PROVIDER_STATUS.TESTING]: "text-blue-400",
    [PROVIDER_STATUS.READY]: "text-emerald-400",
    [PROVIDER_STATUS.FAILED]: "text-red-400",
};
export const STATUS_BADGES = {
    [PROVIDER_STATUS.UNTESTED]: "bg-gray-500/20 text-gray-300 border-gray-500/30",
    [PROVIDER_STATUS.TESTING]: "bg-blue-500/20 text-blue-200 border-blue-500/30 animate-pulse",
    [PROVIDER_STATUS.READY]: "bg-emerald-500/20 text-emerald-200 border-emerald-500/30",
    [PROVIDER_STATUS.FAILED]: "bg-red-500/20 text-red-200 border-red-500/30",
};
export const INTERVIEW_BADGES = {
    [INTERVIEW_STATUS.NOT_TESTED]: "bg-gray-500/20 text-gray-400 border-gray-500/30",
    [INTERVIEW_STATUS.PASSED]: "bg-green-500/20 text-green-300 border-green-500/30",
    [INTERVIEW_STATUS.FAILED]: "bg-red-500/20 text-red-300 border-red-500/30",
};
