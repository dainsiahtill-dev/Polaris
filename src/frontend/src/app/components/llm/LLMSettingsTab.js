import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * LLMSettingsTab
 * LLM 设置主组件，使用 Context + Reducer 模式
 */
import { useCallback, useEffect, useMemo, useRef, useState, } from "react";
import { createPortal } from "react-dom";
import { Loader2, CheckCircle2, AlertTriangle } from "lucide-react";
import { ProviderContextProvider, useActiveTab, useProviderActions, useProviderState, useSelectedRole, useConnectivityStore, } from "./state";
import { devLogger } from "@/app/utils/devLogger";
import { ProviderListManager } from "./providers";
import { PROVIDER_KINDS, isCLIProviderType } from "./types";
import { TestPanel } from "./test/TestPanel";
import { useTestEvents } from "./test/hooks/useTestEvents";
import { useProviderRegistry } from "./ProviderRegistry";
import { getLlmRoleDefinition, getVisibleLlmBindingRoleIds, } from "./roleDefinitions";
import { LLMVisualEditor } from "./visual/LLMVisualEditor";
import { resolveModelName, validateModelName, getModelResolutionLog, } from "./utils";
import { buildBlockedRoleDiagnostics, formatBlockedRoleTitle, } from "./readinessDiagnostics";
import { InterviewHall, } from "./interview/InterviewHall";
import { InterviewSession } from "./interview/InterviewSession";
import { InteractiveInterviewHall, } from "./interview/InteractiveInterviewHall";
// ============================================================================
// Helper Functions
// ============================================================================
function buildSimpleProvider(providerId, provider, roles) {
    const kind = (provider.type || PROVIDER_KINDS.OPENAI_COMPAT);
    const isCli = isCLIProviderType(provider.type) || Boolean(provider.command);
    const conn = isCli
        ? {
            kind: provider.type === PROVIDER_KINDS.GEMINI_CLI
                ? "gemini_cli"
                : "codex_cli",
            command: provider.command ||
                (provider.type === PROVIDER_KINDS.GEMINI_CLI ? "gemini" : "codex"),
            args: provider.args || [],
            env: provider.env || {},
        }
        : {
            kind: "http",
            baseUrl: provider.base_url || "",
            apiKey: provider.api_key,
        };
    // 解析模型
    let modelId = "";
    if (typeof provider.model === "string" && provider.model.trim()) {
        modelId = provider.model.trim();
    }
    else if (typeof provider.default_model === "string" &&
        provider.default_model.trim()) {
        modelId = provider.default_model.trim();
    }
    if (!modelId && roles) {
        for (const roleCfg of Object.values(roles)) {
            if (roleCfg?.provider_id === providerId && roleCfg.model) {
                modelId = roleCfg.model;
                break;
            }
        }
    }
    return {
        id: providerId,
        name: provider.name || providerId,
        kind,
        conn,
        cliMode: provider.cli_mode,
        modelId,
        status: "untested",
    };
}
function mergeVisualConfigIntoLlmConfig(current, nextVisual) {
    const nextProviders = (nextVisual.providers || {});
    const nextRoles = (nextVisual.roles || {});
    return {
        ...current,
        providers: nextProviders,
        roles: nextRoles,
        policies: nextVisual.policies || current.policies,
        visual_layout: nextVisual.visual_layout || {},
        visual_node_states: nextVisual.visual_node_states || current.visual_node_states,
        visual_viewport: nextVisual.visual_viewport,
    };
}
function resolveModelForSelection(roleId, providerId, config, providers) {
    const context = {
        roleId,
        providerId,
        llmConfig: config,
        providers: providers,
    };
    const result = resolveModelName(context);
    devLogger.debug("[ModelResolver] " + getModelResolutionLog(context));
    if (result.warning) {
        devLogger.warn("[ModelResolver] 警告:", result.warning);
    }
    const validation = validateModelName(result.model);
    if (!validation.isValid) {
        devLogger.error("[ModelResolver] 模型验证失败:", validation.error);
    }
    return result.model;
}
// ============================================================================
// Navigation Component
// ============================================================================
function TabNavigation({ globalReadiness, factoryReadiness, blockedRoles, unsupportedRoles, factoryBlockedRoles, factoryUnsupportedRoles, blockedRoleDiagnostics, }) {
    const activeTab = useActiveTab();
    const { switchTab } = useProviderActions();
    const hasBlock = globalReadiness.state === "BLOCKED" || factoryReadiness.state === "BLOCKED";
    const statusLabel = factoryReadiness.state === "BLOCKED"
        ? `${globalReadiness.state} · FACTORY BLOCKED`
        : globalReadiness.state;
    const blockedRoleLabels = blockedRoleDiagnostics.length
        ? blockedRoleDiagnostics.map((item) => item.roleLabel)
        : [...blockedRoles, ...factoryBlockedRoles];
    const blockedRoleDetailTitle = blockedRoleDiagnostics.length
        ? blockedRoleDiagnostics.map(formatBlockedRoleTitle).join("\n")
        : blockedRoleLabels.join(", ");
    const tips = [];
    if (blockedRoleLabels.length)
        tips.push(`未通过深度测试: ${blockedRoleLabels.join(", ")}`);
    if (unsupportedRoles.length)
        tips.push(`运行时不支持: ${unsupportedRoles.join(", ")}`);
    if (factoryUnsupportedRoles.length)
        tips.push(`Factory 不支持: ${factoryUnsupportedRoles.join(", ")}`);
    const tipText = tips.length ? tips.join(" | ") : "请完成必需的 LLM 测试";
    const tipTitle = blockedRoleDetailTitle || tipText;
    const showDetailedDiagnostics = activeTab === "config" && hasBlock && blockedRoleDiagnostics.length > 0;
    return (_jsxs("div", { className: `soft-panel-subtle rounded-xl px-3 ${activeTab === "deepTest" ? "py-1.5" : "py-2"}`, children: [_jsxs("div", { className: "flex min-w-0 flex-col gap-2 xl:flex-row xl:items-center xl:justify-between", children: [_jsxs("div", { className: "flex shrink-0 flex-wrap items-center gap-1.5", children: [_jsx("button", { "data-testid": "llm-settings-tab-config", onClick: () => switchTab("config"), className: `px-3 py-1.5 text-[10px] font-semibold rounded-md border transition-colors ${activeTab === "config"
                                    ? "bg-accent/15 text-accent-text border-accent/45 shadow-[0_8px_20px_rgba(47,127,120,0.13)]"
                                    : "text-text-dim border-border hover:border-accent/35 hover:text-text-main hover:bg-white/70"}`, children: "\u914D\u7F6E" }), _jsx("button", { type: "button", "data-testid": "llm-settings-tab-deep-test", onClick: () => switchTab("deepTest"), className: `px-3 py-1.5 text-[10px] font-semibold rounded-md border transition-colors ${activeTab === "deepTest"
                                    ? "bg-status-success/15 text-status-success border-status-success/45 shadow-[0_8px_20px_rgba(40,122,85,0.13)]"
                                    : "text-text-dim border-border hover:border-status-success/35 hover:text-status-success hover:bg-white/70"}`, children: "\u6DF1\u6D4B" })] }), _jsxs("div", { "data-testid": "llm-readiness-summary", className: "flex min-w-0 flex-wrap items-center justify-start gap-1.5 xl:justify-end", children: [!hasBlock ? (_jsx(CheckCircle2, { className: "size-3.5 text-status-success" })) : (_jsx(AlertTriangle, { className: "size-3.5 text-status-warning" })), _jsx("span", { className: "soft-chip px-2 py-1 text-[10px] text-text-main", children: statusLabel }), hasBlock ? (_jsx("span", { className: "min-w-0 max-w-full truncate rounded-md border border-status-warning/40 bg-status-warning/10 px-2 py-1 text-[10px] text-status-warning", title: tipTitle, children: tipText })) : null] })] }), showDetailedDiagnostics ? (_jsx("div", { className: "soft-inset mt-2 max-h-24 overflow-y-auto rounded-lg", "data-testid": "llm-readiness-diagnostics", children: _jsx("div", { className: "divide-y divide-border", children: blockedRoleDiagnostics.map((detail) => (_jsxs("div", { "data-testid": "llm-readiness-diagnostic-row", className: "grid min-w-0 gap-2 px-2.5 py-1.5 text-[10px] md:grid-cols-[96px_minmax(0,1fr)_minmax(160px,0.74fr)]", title: formatBlockedRoleTitle(detail), children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("span", { className: "rounded border border-status-warning/35 bg-status-warning/10 px-2 py-0.5 font-semibold text-status-warning", children: detail.roleLabel }), _jsx("span", { className: `h-2 w-2 rounded-full ${detail.runtimeSupported ? "bg-status-warning" : "bg-status-error"}` })] }), _jsxs("div", { className: "min-w-0 text-text-muted", children: [_jsxs("div", { "data-testid": "llm-readiness-diagnostic-provider", className: "break-words text-text-main", children: ["Provider:", " ", _jsx("span", { className: "font-semibold text-text-main", children: detail.providerName }), detail.providerId &&
                                                detail.providerName !== detail.providerId ? (_jsxs("span", { className: "ml-1 text-text-dim", children: ["(", detail.providerId, ")"] })) : null] }), _jsxs("div", { "data-testid": "llm-readiness-diagnostic-model", className: "break-words", children: ["Model:", " ", _jsx("span", { className: "text-status-success", children: detail.configuredModel })] })] }), _jsxs("div", { className: "min-w-0 text-status-warning", children: [_jsxs("div", { "data-testid": "llm-readiness-diagnostic-reason", className: "break-words", children: ["\u539F\u56E0: ", detail.issueLabel] }), _jsxs("div", { "data-testid": "llm-readiness-diagnostic-tested", className: "break-words text-text-dim", children: ["\u6700\u8FD1\u6D4B\u8BD5:", " ", detail.testedProviderId || detail.testedModel
                                                ? `${detail.testedProviderName}/${detail.testedModel || "未知模型"}`
                                                : "无记录"] }), detail.testedTimestamp ? (_jsxs("div", { className: "break-all text-text-dim", children: ["\u6D4B\u8BD5\u65F6\u95F4: ", detail.testedTimestamp] })) : null] })] }, detail.roleId))) }) })) : null] }));
}
// ============================================================================
// Deep Test Panel
// ============================================================================
function DeepTestPanel({ llmConfig, llmStatus, onRunConnectivityTest, onAskInteractiveInterview, onSaveInteractiveInterview, resolveProviderEnvOverrides, addTestEvent, resetTestEvents, }) {
    const { state } = useProviderState();
    const { setInterviewMode, setDeepView, selectRole, selectProvider, openTestPanel, startTest, completeTest, } = useProviderActions();
    const { interviewMode, deepView, interviewPanel, interviewRunning, connectivityRunning, } = state;
    const selectedRole = useSelectedRole();
    const { buildProviderSummaries, buildConnectivityMap } = useConnectivityStore();
    const providers = useMemo(() => {
        if (!llmConfig?.providers)
            return [];
        return buildProviderSummaries(llmConfig.providers);
    }, [llmConfig?.providers, buildProviderSummaries]);
    const connectivityResults = useMemo(() => {
        return buildConnectivityMap();
    }, [buildConnectivityMap]);
    const selectedProviderId = state.selectedProviderId;
    const roles = useMemo(() => {
        const roleIds = getVisibleLlmBindingRoleIds(llmConfig?.roles, llmStatus?.roles);
        return roleIds.map((roleId) => {
            const roleMeta = getLlmRoleDefinition(roleId);
            const roleCfg = llmConfig?.roles?.[roleId] ||
                (roleId === "architect" ? llmConfig?.roles?.docs : undefined);
            const status = llmStatus?.roles?.[roleId] ||
                (roleId === "architect" ? llmStatus?.roles?.docs : undefined);
            return {
                id: roleId,
                label: roleMeta.label,
                description: roleMeta.description,
                requiresThinking: roleMeta.requiresThinking,
                minConfidence: roleMeta.minConfidence,
                candidate: {
                    providerId: roleCfg?.provider_id || "",
                    providerName: roleCfg?.provider_id
                        ? llmConfig?.providers?.[roleCfg.provider_id]?.name ||
                            roleCfg.provider_id
                        : "未指派",
                    model: roleCfg?.model || "",
                },
                readiness: {
                    ready: status?.ready,
                    grade: status?.grade,
                },
            };
        });
    }, [llmConfig, llmStatus]);
    const selectedMeta = roles.find((r) => r.id === selectedRole);
    const handleRunConnectivity = useCallback(async (payload) => {
        await onRunConnectivityTest(payload.role, payload.providerId, payload.model);
    }, [onRunConnectivityTest]);
    const handleStartInterview = useCallback(async (payload) => {
        // 使用 TestPanel 流式测试替代直接调用 onRunInterview
        // 配置测试运行参数：connectivity + thinking + interview suites
        const runConfig = {
            suites: ["connectivity", "thinking", "interview"],
            role: payload.role,
            model: payload.model,
        };
        devLogger.debug("[DeepTestPanel] Starting interview with runConfig:", runConfig);
        // 打开 TestPanel 并开始测试
        resetTestEvents();
        openTestPanel(payload.providerId, runConfig);
        startTest(payload.providerId, runConfig);
    }, [openTestPanel, resetTestEvents, startTest]);
    const handleInteractivePanelStateSync = useCallback((payload) => {
        const runConfig = {
            suites: ["interactive_stream_view"],
            role: payload.roleId,
            ...(payload.model ? { model: payload.model } : {}),
        };
        if (payload.status === "idle") {
            openTestPanel(payload.providerId, runConfig);
            return;
        }
        if (payload.status === "running") {
            openTestPanel(payload.providerId, runConfig);
            startTest(payload.providerId, runConfig);
            return;
        }
        completeTest(payload.providerId, payload.status === "success");
    }, [completeTest, openTestPanel, startTest]);
    return (_jsxs("div", { className: "flex min-h-0 min-w-0 flex-1 basis-0 flex-col gap-2 overflow-hidden", children: [_jsx("div", { className: "soft-panel-subtle rounded-xl px-3 py-2", children: _jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2 text-[10px] text-text-dim", children: [_jsx("span", { className: "font-semibold text-status-success", children: "\u6DF1\u6D4B" }), _jsxs("span", { className: "truncate", children: [selectedMeta?.label || selectedRole, " /", " ", selectedProviderId || "未选择提供商"] })] }), _jsxs("div", { className: "soft-inset flex items-center gap-1 rounded-md p-0.5", children: [_jsx("button", { "data-testid": "llm-deep-mode-interactive", onClick: () => setInterviewMode("interactive"), className: `px-2.5 py-1 text-[10px] font-semibold rounded transition-colors ${interviewMode === "interactive"
                                        ? "bg-status-success/15 text-status-success"
                                        : "text-text-dim hover:text-status-success"}`, children: "\u4EA4\u4E92\u95EE\u7B54" }), _jsx("button", { "data-testid": "llm-deep-mode-auto", onClick: () => {
                                        setInterviewMode("auto");
                                        setDeepView("hall");
                                    }, className: `px-2.5 py-1 text-[10px] font-semibold rounded transition-colors ${interviewMode === "auto"
                                        ? "bg-accent/15 text-accent-text"
                                        : "text-text-dim hover:text-text-main"}`, children: "\u81EA\u52A8\u5DE1\u68C0" })] })] }) }), _jsx("div", { className: "min-h-[360px] min-w-0 flex-1 basis-0 overflow-hidden", children: interviewMode === "interactive" ? (_jsx(InteractiveInterviewHall, { roles: roles, providers: providers, selectedRole: selectedRole, selectedProvider: selectedProviderId, selectedModel: resolveModelForSelection(selectedRole, selectedProviderId ?? "", llmConfig, providers) ?? null, onSelectRole: selectRole, onSelectProvider: selectProvider, onAskQuestion: onAskInteractiveInterview, onSaveReport: onSaveInteractiveInterview, resolveEnvOverrides: resolveProviderEnvOverrides, onTestEvent: addTestEvent, onResetTestEvents: resetTestEvents, onSyncTestPanelState: handleInteractivePanelStateSync, isDeepTestMode: true })) : deepView === "hall" ? (_jsx(InterviewHall, { roles: roles, selectedRole: selectedRole, providers: providers, selectedProvider: selectedProviderId, onSelectRole: selectRole, onSelectProvider: selectProvider, onRunConnectivityTest: handleRunConnectivity, onRunInterview: handleStartInterview, connectivityResults: connectivityResults, interviewRunning: interviewRunning, connectivityRunning: connectivityRunning, onSkipConnectivityTest: () => { } })) : (_jsx(InterviewSession, { roleLabel: selectedMeta?.label || selectedRole, roleId: selectedRole, report: interviewPanel.report || null, running: interviewRunning, error: interviewPanel.error, onBack: () => setDeepView("hall") })) })] }));
}
// ============================================================================
// Main Component
// ============================================================================
function LLMSettingsTabInner({ llmConfig, llmStatus, llmLoading, llmSaving, llmError, deletingProviders, onSaveConfig, onRunInterview: _onRunInterview, onRunConnectivityTest, onAskInteractiveInterview, onSaveInteractiveInterview, resolveProviderEnvOverrides, onAddProvider, onUpdateProvider, onDeleteProvider, onUpdateConfig, onTestProvider, onCancelTestProvider, onCancelInterview: _onCancelInterview, }) {
    const { state } = useProviderState();
    const { switchTab, startTest, completeTest, closeTestPanel, setConfigView, } = useProviderActions();
    const { activeTab, configView, testPanel } = state;
    const { events, addEvent, resetEvents } = useTestEvents();
    const [panelHost, setPanelHost] = useState(null);
    // Use ref to avoid closure staleness in TestPanel callbacks
    const completeTestRef = useRef(completeTest);
    const selectedProviderIdRef = useRef(testPanel.selectedProviderId);
    useEffect(() => {
        completeTestRef.current = completeTest;
        selectedProviderIdRef.current = testPanel.selectedProviderId;
    }, [completeTest, testPanel.selectedProviderId]);
    // Provider Registry
    const { loading: providersLoading, error: providersError, providers, getProviderInfo, getProviderDefaultConfig, getProviderComponent, getCostClass, } = useProviderRegistry();
    // Global readiness
    const globalReadiness = useMemo(() => {
        const s = llmStatus?.state || "UNKNOWN";
        if (s === "READY")
            return { state: "READY", color: "text-emerald-400" };
        if (s === "BLOCKED")
            return { state: "BLOCKED", color: "text-amber-400" };
        return { state: "UNKNOWN", color: "text-gray-400" };
    }, [llmStatus]);
    const factoryReadiness = useMemo(() => {
        const s = llmStatus?.factory_state || "UNKNOWN";
        if (s === "READY")
            return { state: "READY", color: "text-emerald-400" };
        if (s === "BLOCKED")
            return { state: "BLOCKED", color: "text-amber-400" };
        return { state: "UNKNOWN", color: "text-gray-400" };
    }, [llmStatus]);
    const blockedRoles = useMemo(() => llmStatus?.blocked_roles || [], [llmStatus]);
    const unsupportedRoles = useMemo(() => llmStatus?.unsupported_roles || [], [llmStatus]);
    const factoryBlockedRoles = useMemo(() => llmStatus?.factory_blocked_roles || [], [llmStatus]);
    const factoryUnsupportedRoles = useMemo(() => llmStatus?.factory_unsupported_roles || [], [llmStatus]);
    const displayedBlockedRoles = useMemo(() => Array.from(new Set([...blockedRoles, ...factoryBlockedRoles])), [blockedRoles, factoryBlockedRoles]);
    const displayedUnsupportedRoles = useMemo(() => Array.from(new Set([...unsupportedRoles, ...factoryUnsupportedRoles])), [unsupportedRoles, factoryUnsupportedRoles]);
    const blockedRoleDiagnostics = useMemo(() => buildBlockedRoleDiagnostics({
        blockedRoles: displayedBlockedRoles,
        unsupportedRoles: displayedUnsupportedRoles,
        roles: llmStatus?.roles || {},
        providers: llmConfig?.providers || {},
    }), [
        displayedBlockedRoles,
        displayedUnsupportedRoles,
        llmConfig?.providers,
        llmStatus?.roles,
    ]);
    // Visual config
    const visualConfig = useMemo(() => {
        if (!llmConfig)
            return null;
        return {
            providers: llmConfig.providers || {},
            roles: llmConfig.roles || {},
            visual_layout: llmConfig
                .visual_layout || {},
            visual_node_states: llmConfig
                .visual_node_states || {},
            visual_viewport: llmConfig
                .visual_viewport ||
                undefined,
            policies: llmConfig.policies,
        };
    }, [llmConfig]);
    const { getLatestProviderConnectivity } = useConnectivityStore();
    const visualStatus = useMemo(() => {
        const rolesStatus = {};
        Object.entries(llmStatus?.roles || {}).forEach(([roleId, role]) => {
            rolesStatus[roleId] = { ready: role.ready, grade: role.grade };
        });
        const providersStatus = {};
        Object.keys(llmConfig?.providers || {}).forEach((providerId) => {
            const cachedConnectivityStatus = state.providerTestStatus[providerId] || "unknown";
            const latestConnectivity = getLatestProviderConnectivity(providerId);
            const persistedConnectivitySuite = llmStatus?.providers?.[providerId]
                ?.suites?.connectivity;
            const persistedConnectivityOk = typeof persistedConnectivitySuite?.ok === "boolean"
                ? persistedConnectivitySuite.ok
                : undefined;
            const status = cachedConnectivityStatus !== "unknown"
                ? cachedConnectivityStatus
                : latestConnectivity
                    ? latestConnectivity.ok
                        ? "success"
                        : "failed"
                    : persistedConnectivityOk === true
                        ? "success"
                        : persistedConnectivityOk === false
                            ? "failed"
                            : "unknown";
            providersStatus[providerId] = { status };
        });
        if (Object.keys(rolesStatus).length === 0 &&
            Object.keys(providersStatus).length === 0) {
            return null;
        }
        return {
            roles: rolesStatus,
            providers: providersStatus,
        };
    }, [
        getLatestProviderConnectivity,
        llmConfig?.providers,
        llmStatus,
        state.providerTestStatus,
    ]);
    // Test handlers
    const handleTestProvider = useCallback(async (providerId) => {
        devLogger.debug("[LLMSettingsTab] handleTestProvider called for:", providerId);
        if (!onTestProvider || !llmConfig) {
            devLogger.debug("[LLMSettingsTab] Missing onTestProvider or llmConfig, returning early");
            return;
        }
        const cfg = llmConfig.providers?.[providerId];
        if (!cfg) {
            devLogger.debug("[LLMSettingsTab] No config found for provider:", providerId);
            return;
        }
        const simpleProvider = buildSimpleProvider(providerId, cfg, llmConfig.roles);
        devLogger.debug("[LLMSettingsTab] Starting test for provider:", providerId);
        startTest(providerId);
        resetEvents();
        try {
            devLogger.debug("[LLMSettingsTab] Calling onTestProvider...");
            const result = await onTestProvider(simpleProvider, (event) => {
                addEvent(event);
            });
            devLogger.debug("[LLMSettingsTab] onTestProvider returned:", result);
            devLogger.debug("[LLMSettingsTab] Calling completeTest from handleTestProvider, success:", result?.ready ?? false);
            completeTest(providerId, result?.ready ?? false);
        }
        catch (err) {
            devLogger.debug("[LLMSettingsTab] onTestProvider threw error:", err);
            devLogger.debug("[LLMSettingsTab] Calling completeTest from handleTestProvider catch block, success: false");
            completeTest(providerId, false);
        }
    }, [llmConfig, onTestProvider, startTest, completeTest, addEvent, resetEvents]);
    // Handle visual config change
    const handleVisualConfigChange = useCallback((nextConfig) => {
        if (!onUpdateConfig || !llmConfig)
            return;
        onUpdateConfig(mergeVisualConfigIntoLlmConfig(llmConfig, nextConfig));
    }, [llmConfig, onUpdateConfig]);
    const handleVisualSave = useCallback((nextConfig) => {
        if (!onSaveConfig)
            return;
        if (nextConfig && llmConfig) {
            const merged = mergeVisualConfigIntoLlmConfig(llmConfig, nextConfig);
            onUpdateConfig?.(merged);
            void onSaveConfig(merged);
            return;
        }
        void onSaveConfig();
    }, [llmConfig, onSaveConfig, onUpdateConfig]);
    // Loading state
    if (llmLoading || providersLoading) {
        return (_jsx("div", { className: "flex items-center justify-center py-12", children: _jsxs("div", { className: "flex items-center gap-2 text-text-muted", children: [_jsx(Loader2, { className: "size-4 animate-spin" }), _jsx("span", { className: "text-sm", children: "\u6B63\u5728\u8F7D\u5165 LLM \u914D\u7F6E..." })] }) }));
    }
    const testPanelProviderId = testPanel.selectedProviderId;
    const isInteractiveStreamPanel = Boolean(testPanel.runConfig?.suites?.includes("interactive_stream_view"));
    return (_jsxs("div", { className: "flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto pr-1 custom-scrollbar", children: [_jsx(TabNavigation, { globalReadiness: globalReadiness, factoryReadiness: factoryReadiness, blockedRoles: blockedRoles, unsupportedRoles: unsupportedRoles, factoryBlockedRoles: factoryBlockedRoles, factoryUnsupportedRoles: factoryUnsupportedRoles, blockedRoleDiagnostics: blockedRoleDiagnostics }), (llmError || providersError) && (_jsx("div", { className: "text-xs text-status-error bg-status-error/10 border border-status-error/20 rounded p-2", children: llmError || providersError })), llmSaving && (_jsxs("div", { className: "flex shrink-0 items-center gap-2 text-[10px] text-text-dim", children: [_jsx(Loader2, { className: "size-3 animate-spin" }), _jsx("span", { children: "Saving LLM configuration..." })] })), _jsxs("div", { className: `grid min-h-[420px] min-w-0 flex-1 basis-0 gap-3 overflow-visible xl:overflow-hidden ${testPanelProviderId
                    ? "grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(340px,420px)]"
                    : "grid-cols-1"}`, children: [_jsxs("div", { className: "min-h-0 min-w-0 overflow-hidden", children: [activeTab === "config" && (_jsxs("div", { className: "flex h-full min-h-0 flex-col gap-3 overflow-hidden", children: [_jsxs("div", { className: "soft-panel-subtle flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-xl px-3 py-2", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("h3", { className: "text-xs font-semibold text-text-main", children: "LLM \u63D0\u4F9B\u5546\u914D\u7F6E" }), _jsx("p", { className: "truncate text-[10px] text-text-dim", children: "\u5217\u8868\u914D\u7F6E\u4E0E\u89D2\u8272-\u6A21\u578B\u8FDE\u7EBF\u3002" })] }), _jsxs("div", { className: "soft-inset flex items-center gap-1 rounded-md p-0.5", children: [_jsx("button", { onClick: () => setConfigView("list"), "data-testid": "llm-config-view-list", className: `px-2.5 py-1 text-[10px] font-semibold rounded transition-colors ${configView === "list"
                                                            ? "bg-accent/15 text-accent-text"
                                                            : "text-text-dim hover:text-text-main"}`, children: "\u5217\u8868" }), _jsx("button", { onClick: () => setConfigView("visual"), "data-testid": "llm-config-view-visual", "aria-label": "\u89C6\u89C9\u89C6\u56FE", className: `px-2.5 py-1 text-[10px] font-semibold rounded transition-colors ${configView === "visual"
                                                            ? "bg-accent/15 text-accent-text"
                                                            : "text-text-dim hover:text-text-main"}`, children: "\u89C6\u89C9\u89C6\u56FE" })] })] }), _jsx("div", { className: "min-h-0 flex-1 overflow-y-auto pr-1 custom-scrollbar", children: configView === "visual" ? (_jsx(LLMVisualEditor, { config: visualConfig, status: visualStatus, onConfigChange: handleVisualConfigChange, onSave: handleVisualSave })) : (_jsx(ProviderListManager, { providers: providers, configuredProviders: llmConfig?.providers || {}, llmStatus: llmStatus, isSaving: llmSaving, deletingProviders: deletingProviders, getProviderInfo: (type) => {
                                                const entry = getProviderInfo(type);
                                                if (!entry)
                                                    return undefined;
                                                const defaults = getProviderDefaultConfig(type);
                                                if (!defaults)
                                                    return undefined;
                                                const component = getProviderComponent(type);
                                                if (!component)
                                                    return undefined;
                                                return {
                                                    info: entry,
                                                    defaultConfig: defaults,
                                                    component,
                                                };
                                            }, getProviderComponent: (type) => getProviderComponent(type) ?? null, getCostClass: getCostClass, onAddProvider: onAddProvider || (() => { }), onUpdateProvider: onUpdateProvider || (() => { }), onDeleteProvider: onDeleteProvider || (() => { }), onTestProvider: handleTestProvider, onEnterDeepTest: () => switchTab("deepTest") })) })] })), activeTab === "deepTest" && (_jsx("div", { className: "flex h-full min-h-0 min-w-0 flex-col overflow-hidden", children: _jsx(DeepTestPanel, { llmConfig: llmConfig, llmStatus: llmStatus, onRunConnectivityTest: onRunConnectivityTest, onAskInteractiveInterview: onAskInteractiveInterview, onSaveInteractiveInterview: onSaveInteractiveInterview, resolveProviderEnvOverrides: resolveProviderEnvOverrides, addTestEvent: addEvent, resetTestEvents: resetEvents }) }))] }), testPanelProviderId ? (_jsx("aside", { ref: setPanelHost, "data-testid": "llm-test-panel-host", className: "soft-panel pointer-events-auto min-h-[280px] min-w-0 max-w-full overflow-hidden rounded-xl p-2 xl:min-h-0", "aria-label": "LLM \u6D4B\u8BD5\u9762\u677F" })) : null] }), panelHost &&
                testPanelProviderId &&
                createPortal(_jsx(TestPanel, { provider: buildSimpleProvider(testPanelProviderId, llmConfig?.providers?.[testPanelProviderId] || {}, llmConfig?.roles), embedded: true, events: events, status: testPanel.status, runConfig: testPanel.runConfig, autoStart: Boolean(testPanel.runConfig?.suites && !isInteractiveStreamPanel), panelMode: isInteractiveStreamPanel ? "event-viewer" : "stream-runner", title: isInteractiveStreamPanel ? "交互式面试日志" : undefined, subtitle: isInteractiveStreamPanel
                        ? `供应商：${llmConfig?.providers?.[testPanelProviderId]?.name || testPanelProviderId} · 模型：${testPanel.runConfig?.model || llmConfig?.providers?.[testPanelProviderId]?.model || "默认"}`
                        : undefined, placeholder: isInteractiveStreamPanel ? "$ 尚未发送面试问题..." : undefined, onClearEvents: isInteractiveStreamPanel ? resetEvents : undefined, onClose: () => {
                        closeTestPanel();
                        resetEvents();
                    }, onCancel: () => {
                        onCancelTestProvider?.();
                        closeTestPanel();
                        resetEvents();
                    }, onTestComplete: ({ success }) => {
                        // Use ref to ensure we call the latest completeTest with latest providerId
                        const providerId = selectedProviderIdRef.current;
                        devLogger.debug("[LLMSettingsTab] onTestComplete called:", {
                            providerId,
                            success,
                        });
                        if (providerId) {
                            devLogger.debug("[LLMSettingsTab] Calling completeTest for provider:", providerId, "success:", success);
                            completeTestRef.current(providerId, success);
                        }
                        else {
                            devLogger.warn("[LLMSettingsTab] onTestComplete called but no providerId available");
                        }
                    } }), panelHost)] }));
}
// ============================================================================
// Exported Component with Provider
// ============================================================================
export function LLMSettingsTab(props) {
    return (_jsx(ProviderContextProvider, { children: _jsx(LLMSettingsTabInner, { ...props }) }));
}
export default LLMSettingsTab;
