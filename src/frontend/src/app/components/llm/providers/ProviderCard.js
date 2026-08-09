import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * ProviderCard Component
 * 单个 Provider 的展示和编辑卡片
 */
import { memo, useCallback, useEffect, useMemo } from "react";
import { devLogger } from "@/app/utils/devLogger";
import { Loader2, Settings, ChevronDown, ChevronUp, Zap, Key, Shield, HelpCircle, Clock, UserCheck, UserX, PlayCircle, CheckCircle2, AlertTriangle, } from "lucide-react";
import { useProviderActions, useProviderState, useIsProviderExpanded } from "../state";
import { isCLIProviderType, requiresApiKey } from "../types";
import { CyberpunkCard, CyberpunkGlitchText, } from "../visual/CyberpunkTestAnimation";
import { getRoleDisplayLabel } from "@/app/constants/roleLabels";
function toProviderSlug(value) {
    return String(value || "")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
}
export const ProviderCard = memo(function ProviderCard({ providerId, provider, providerInfo, ProviderComponent, connectivityStatus, costClass, isDeleting, isSaving, llmStatus, onUpdate, onDelete, onTest, }) {
    const { state } = useProviderState();
    const { startEdit, cancelEdit, toggleExpandProvider } = useProviderActions();
    const isExpanded = useIsProviderExpanded(providerId);
    const isEditing = state.editingProviderId === providerId;
    // Debug: log status changes
    useEffect(() => {
        devLogger.debug("[ProviderCard]", providerId, "status changed to:", connectivityStatus);
    }, [providerId, connectivityStatus]);
    const statusStyles = useMemo(() => {
        const styleKey = connectivityStatus === "running" ? "unknown" : connectivityStatus;
        return {
            unknown: {
                border: "border-status-warning/35",
                bg: "bg-status-warning/10",
                dot: "bg-status-warning",
                text: "text-status-warning",
            },
            success: {
                border: "border-status-success/40",
                bg: "bg-status-success/10",
                dot: "bg-status-success",
                text: "text-status-success",
            },
            failed: {
                border: "border-status-error/40",
                bg: "bg-status-error/10",
                dot: "bg-status-error",
                text: "text-status-error",
            },
        }[styleKey];
    }, [connectivityStatus]);
    const connectivityLabel = useMemo(() => {
        if (connectivityStatus === "running")
            return "测试中";
        if (connectivityStatus === "success")
            return "连通正常";
        if (connectivityStatus === "failed")
            return "连通失败";
        return "连通未知";
    }, [connectivityStatus]);
    const providerInterview = useMemo(() => {
        return llmStatus?.interviews?.latest_by_provider?.[providerId];
    }, [llmStatus, providerId]);
    const providerReadiness = useMemo(() => {
        return llmStatus?.providers?.[providerId];
    }, [llmStatus, providerId]);
    const readinessStatus = useMemo(() => {
        if (providerReadiness?.ready === true)
            return "passed";
        if (providerReadiness?.ready === false)
            return "failed";
        return "unknown";
    }, [providerReadiness]);
    const readinessLabel = useMemo(() => {
        if (readinessStatus === "passed")
            return "就绪通过";
        if (readinessStatus === "failed")
            return "就绪失败";
        return "就绪未知";
    }, [readinessStatus]);
    const deepTestLabel = useMemo(() => {
        if (!providerInterview)
            return "深测未测";
        return providerInterview.status === "passed" ? "深测通过" : "深测失败";
    }, [providerInterview]);
    const providerType = useMemo(() => {
        return isCLIProviderType(provider.type || "") ? "命令行" : "接口";
    }, [provider.type]);
    const authType = useMemo(() => {
        return requiresApiKey(provider.type || "") ? "API 密钥" : "无";
    }, [provider.type]);
    const getRoleDisplayName = useCallback((roleId) => {
        return roleId ? getRoleDisplayLabel(roleId) : "未署名";
    }, []);
    const handleToggleEdit = useCallback(() => {
        if (isEditing) {
            cancelEdit(providerId);
        }
        else {
            startEdit(providerId, provider);
        }
    }, [cancelEdit, isEditing, provider, providerId, startEdit]);
    const handleToggleExpand = useCallback(() => {
        toggleExpandProvider(providerId);
    }, [providerId, toggleExpandProvider]);
    const handleDelete = useCallback(() => {
        onDelete(providerId);
    }, [providerId, onDelete]);
    const handleTest = useCallback(() => {
        onTest(providerId);
    }, [providerId, onTest]);
    const handleUpdate = useCallback((updates) => {
        onUpdate(providerId, updates);
    }, [providerId, onUpdate]);
    const actionsDisabled = isSaving || !!isDeleting;
    const testDisabled = actionsDisabled;
    const providerLabel = provider.name || providerInfo?.name || providerId;
    const providerSlug = toProviderSlug(providerLabel || providerId) || "provider";
    return (_jsxs(CyberpunkCard, { status: connectivityStatus, className: "p-4", "data-testid": `provider-card-${providerSlug}`, "data-provider-id": providerId, "data-provider-type": provider.type || "", "data-provider-name": providerLabel, "data-provider-connectivity-status": connectivityStatus, children: [_jsxs("div", { "data-testid": `provider-card-header-${providerSlug}`, className: "flex min-w-0 flex-col gap-3 lg:flex-row lg:items-start lg:justify-between", children: [_jsxs("div", { className: "flex min-w-0 flex-col gap-1 sm:flex-row sm:items-center sm:gap-3", children: [_jsx(CyberpunkGlitchText, { text: provider.name || providerInfo?.name || providerId, status: connectivityStatus, className: "min-w-0 truncate text-sm font-semibold" }), _jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2 text-[10px] text-text-dim", children: [_jsx("span", { className: "min-w-0 max-w-full truncate font-mono sm:max-w-72", children: provider.model || "默认" }), _jsx("span", { className: `${costClass.toLowerCase() === "local"
                                            ? "text-status-success"
                                            : costClass.toLowerCase() === "fixed"
                                                ? "text-accent-text"
                                                : "text-status-warning"}`, children: costClass })] })] }), _jsxs("div", { "data-testid": `provider-card-actions-${providerSlug}`, className: "flex shrink-0 flex-wrap items-center gap-2 lg:justify-end", children: [_jsx("div", { className: `flex shrink-0 items-center gap-1.5 rounded border px-2 py-1 ${statusStyles.border} ${statusStyles.bg}`, children: _jsx(CyberpunkGlitchText, { text: connectivityLabel, status: connectivityStatus, className: "text-[10px]" }) }), _jsxs("div", { className: "soft-chip flex shrink-0 items-center gap-1.5 px-2 py-1", title: `就绪状态（综合套件）${providerReadiness?.grade ? `: ${providerReadiness.grade}` : ""}`, children: [readinessStatus === "passed" ? (_jsx(CheckCircle2, { className: "size-3 text-status-success" })) : readinessStatus === "failed" ? (_jsx(AlertTriangle, { className: "size-3 text-status-warning" })) : (_jsx(HelpCircle, { className: "size-3 text-text-muted" })), _jsx("span", { className: "text-[10px] text-text-main", children: readinessLabel })] }), _jsxs("div", { className: "soft-chip flex shrink-0 items-center gap-1.5 px-2 py-1", children: [providerInterview ? (providerInterview.status === "passed" ? (_jsx(UserCheck, { className: "size-3 text-status-success" })) : (_jsx(UserX, { className: "size-3 text-status-error" }))) : (_jsx(HelpCircle, { className: "size-3 text-text-muted" })), _jsx("span", { className: "text-[10px] text-text-main", children: deepTestLabel })] }), _jsx("button", { onClick: handleTest, disabled: testDisabled, "data-provider-action": "test", "data-testid": `provider-test-button-${providerSlug}`, className: "rounded border border-accent/35 p-1.5 text-accent-text transition-colors hover:border-accent/60 hover:bg-accent/10 disabled:cursor-not-allowed disabled:opacity-50", title: "\u6D4B\u8BD5\u8FDE\u901A\u6027", children: _jsx(PlayCircle, { className: "size-3" }) }), _jsx("button", { onClick: handleToggleEdit, disabled: actionsDisabled, "data-provider-action": "edit", "data-testid": `provider-edit-button-${providerSlug}`, className: `p-1.5 rounded border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${isEditing
                                    ? "border-accent/55 bg-accent/15 text-accent-text"
                                    : "border-border text-text-muted hover:border-accent/40 hover:text-text-main"}`, title: isEditing ? "完成编辑" : "编辑提供商", children: _jsx(Settings, { className: "size-3" }) }), _jsx("button", { onClick: handleToggleExpand, "data-provider-action": "expand", "data-testid": `provider-expand-button-${providerSlug}`, className: "rounded border border-border p-1.5 text-text-muted transition-colors hover:border-accent/40 hover:text-text-main", title: isExpanded ? "收起详情" : "展开详情", children: isExpanded ? (_jsx(ChevronUp, { className: "size-3" })) : (_jsx(ChevronDown, { className: "size-3" })) }), _jsx("button", { onClick: handleDelete, disabled: actionsDisabled, "data-provider-action": "delete", "data-testid": `provider-delete-button-${providerSlug}`, className: "rounded border border-status-error/35 p-1.5 text-status-error transition-colors hover:border-status-error/55 hover:bg-status-error/10 disabled:cursor-not-allowed disabled:opacity-50", title: "\u5220\u9664\u63D0\u4F9B\u5546", children: isDeleting ? _jsx(Loader2, { className: "size-3 animate-spin" }) : "×" })] })] }), isExpanded && !isEditing && (_jsxs("div", { className: "mt-4 space-y-4 border-t border-border pt-4", children: [_jsxs("div", { className: "grid grid-cols-1 gap-3 md:grid-cols-3", children: [_jsxs("div", { className: "soft-chip flex items-center gap-2 rounded px-3 py-2", children: [_jsx(Zap, { className: "size-3.5 text-status-warning" }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "text-[9px] text-text-dim uppercase tracking-wide", children: "\u7C7B\u578B" }), _jsx("div", { className: "text-xs text-text-main truncate", children: providerType })] })] }), _jsxs("div", { className: "soft-chip flex items-center gap-2 rounded px-3 py-2", children: [_jsx(Key, { className: "size-3.5 text-accent-text" }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "text-[9px] text-text-dim uppercase tracking-wide", children: "\u8BA4\u8BC1" }), _jsx("div", { className: "text-xs text-text-main truncate", children: authType })] })] }), _jsxs("div", { className: "soft-chip flex items-center gap-2 rounded px-3 py-2", children: [_jsx(Shield, { className: "size-3.5 text-status-success" }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "text-[9px] text-text-dim uppercase tracking-wide", children: "\u7279\u6027" }), _jsxs("div", { className: "text-xs text-text-main truncate", children: [providerInfo?.supported_features.slice(0, 2).join(", ") ||
                                                        "-", providerInfo &&
                                                        providerInfo.supported_features.length > 2 &&
                                                        "..."] })] })] })] }), providerInterview && (_jsxs("div", { className: "space-y-2", children: [_jsxs("h5", { className: "text-xs font-semibold text-text-main flex items-center gap-2", children: [_jsx(UserCheck, { className: "size-3.5 text-accent" }), "\u6DF1\u5EA6\u6D4B\u8BD5\u8BB0\u5F55"] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: `px-2 py-1 text-[10px] uppercase font-semibold rounded border ${providerInterview.status === "passed"
                                            ? "bg-status-success/15 text-status-success border-status-success/35"
                                            : "bg-status-error/15 text-status-error border-status-error/35"}`, children: providerInterview.status === "passed" ? "通过" : "失败" }), _jsxs("span", { className: "flex items-center gap-1 text-[10px] text-text-dim", children: [_jsx(Clock, { className: "size-3" }), new Date(providerInterview.timestamp).toLocaleString()] })] }), _jsxs("div", { className: "break-words text-[10px] text-text-muted", children: ["\u89D2\u8272:", " ", _jsx("span", { className: "text-text-main", children: getRoleDisplayName(providerInterview.role) }), " · ", "\u6A21\u578B:", " ", _jsx("span", { className: "font-mono text-text-main", children: providerInterview.model })] })] }))] })), isEditing && ProviderComponent && (_jsx("div", { className: "mt-4 border-t border-border pt-4", children: _jsx(ProviderComponent, { providerId: providerId, provider: {
                        ...provider,
                        type: provider.type || "openai_compat",
                        name: provider.name == null ? "" : String(provider.name),
                    }, onUpdate: handleUpdate, onValidate: () => ({ valid: true, errors: [], warnings: [] }) }) }))] }));
});
