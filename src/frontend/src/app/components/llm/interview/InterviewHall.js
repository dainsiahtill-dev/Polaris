import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { CheckCircle2, AlertTriangle, PlayCircle, ShieldCheck, Loader2, Cpu, Zap, Info, } from "lucide-react";
import { useState } from "react";
import { MultiRoleInterviewStatus, InterviewDetailsModal, } from "./MultiRoleInterviewStatus";
import { getLlmRoleDefinition } from "../roleDefinitions";
const STATUS_STYLES = {
    ready: {
        border: "border-emerald-500/40",
        bg: "bg-emerald-500/10",
        dot: "bg-emerald-400",
        text: "text-emerald-300",
    },
    failed: {
        border: "border-rose-500/40",
        bg: "bg-rose-500/10",
        dot: "bg-rose-400",
        text: "text-rose-300",
    },
    testing: {
        border: "border-amber-500/30",
        bg: "bg-amber-500/10",
        dot: "bg-amber-300",
        text: "text-amber-200",
    },
    untested: {
        border: "border-white/10",
        bg: "bg-white/5",
        dot: "bg-white/[0.04]0",
        text: "text-text-dim",
    },
};
const STATUS_LABELS = {
    ready: "连通正常",
    failed: "连通失败",
    testing: "连通测试中",
    untested: "连通未测",
};
const formatTimestamp = (timestamp) => {
    if (!timestamp)
        return "未测试";
    try {
        return new Date(timestamp).toLocaleString();
    }
    catch {
        return timestamp;
    }
};
function InterviewHallLegacy({ roles, candidates, selectedRole, onSelectRole, onStartInterview, onRunReadiness, disabledReason, running, }) {
    const activeRole = roles.find((role) => role.id === selectedRole);
    return (_jsxs("div", { className: "space-y-6", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs text-text-dim uppercase tracking-wide", children: "LLM \u9762\u8BD5\u4E2D\u67A2" }), _jsx("h3", { className: "text-lg font-semibold text-text-main", children: "\u9762\u8BD5\u5927\u5385" })] }), _jsxs("div", { className: "flex items-center gap-2 text-[10px] text-text-dim", children: [_jsx(ShieldCheck, { className: "size-4 text-emerald-300" }), "\u6838\u5FC3\u5C97\u4F4D\u987B\u914D\u601D\u8003\u578B\u6A21\u578B\u3002"] })] }), _jsxs("div", { className: "grid grid-cols-1 lg:grid-cols-[1.2fr_1fr] gap-6", children: [_jsxs("div", { className: "space-y-4", children: [_jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\uD83C\uDFAF \u9762\u8BD5\u5C97\u4F4D" }), roles.map((role) => {
                                const isActive = role.id === selectedRole;
                                const badge = getLlmRoleDefinition(role.id).badge;
                                return (_jsxs("button", { "data-testid": `llm-auto-role-${role.id}`, onClick: () => onSelectRole(role.id), className: `w-full text-left rounded-xl border p-4 transition-all ${isActive
                                        ? "soft-raised border-white/[0.15]"
                                        : "border-white/10 bg-white/5 hover:border-white/20"}`, children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: `px-2 py-1 text-[10px] uppercase font-semibold rounded border ${badge}`, children: role.label }), role.readiness?.ready ? (_jsx(CheckCircle2, { className: "size-4 text-emerald-400" })) : (_jsx(AlertTriangle, { className: "size-4 text-amber-300" }))] }), _jsx("div", { className: "text-[10px] text-text-dim uppercase tracking-wide", children: role.requiresThinking ? "需要思考" : "可选思考" })] }), _jsx("div", { className: "mt-2 text-xs text-text-dim", children: role.description }), _jsxs("div", { className: "mt-3 text-[11px] text-text-main", children: ["\u5019\u9009\u4EBA\uFF1A", role.candidate?.providerName || "未指派", " ", role.candidate?.model ? `• ${role.candidate.model}` : ""] })] }, role.id));
                            })] }), _jsxs("div", { className: "space-y-4", children: [_jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\uD83D\uDC65 \u5E94\u8058\u8005\u5217\u8868" }), _jsx("div", { className: "rounded-xl border border-white/10 bg-white/5 p-4 space-y-3", children: candidates.length === 0 ? (_jsx("div", { className: "text-xs text-text-dim", children: "\u6682\u65E0\u5DF2\u914D\u7F6E\u6A21\u578B\u3002" })) : (candidates.map((candidate) => (_jsxs("div", { className: "flex items-center justify-between text-xs", children: [_jsxs("div", { children: [_jsx("div", { className: "text-text-main font-semibold", children: candidate.providerName }), _jsx("div", { className: "text-text-dim", children: candidate.model })] }), _jsxs("div", { className: "text-[10px] text-text-dim text-right", children: [_jsx("div", { children: candidate.roleLabel }), _jsxs("div", { children: ["\u601D\u8003 ", candidate.thinkingSupported ? "通过" : "—", " ", candidate.thinkingConfidence !== null &&
                                                            candidate.thinkingConfidence !== undefined
                                                            ? `${Math.round(candidate.thinkingConfidence * 100)}%`
                                                            : ""] })] })] }, candidate.id)))) }), _jsxs("div", { className: "rounded-xl border border-white/10 bg-black/30 p-4 space-y-3", children: [_jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\uD83D\uDE80 \u5F00\u59CB\u9762\u8BD5" }), _jsx("div", { className: "text-xs text-text-dim", children: activeRole?.requiresThinking
                                            ? `核心岗位要求思考型模型（最低 ${Math.round(activeRole.minConfidence * 100)}% 置信度）。`
                                            : "辅助岗位可使用高效模型，思考能力为加分项。" }), _jsxs("div", { className: "text-[11px] text-text-dim", children: ["\u601D\u8003\u68C0\u6D4B\uFF1A", activeRole?.thinkingConfidence !== null &&
                                                activeRole?.thinkingConfidence !== undefined
                                                ? `${Math.round(activeRole.thinkingConfidence * 100)}%`
                                                : "未检测"] }), disabledReason ? (_jsx("div", { className: "text-[11px] text-red-200 bg-red-500/10 border border-red-500/20 rounded p-2", children: disabledReason })) : null, _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("button", { onClick: onStartInterview, disabled: !!disabledReason || running, className: "px-3 py-2 text-[11px] font-semibold bg-emerald-500/[0.08]0 hover:bg-emerald-500 text-white rounded transition-colors disabled:opacity-60 flex items-center gap-1", children: [_jsx(PlayCircle, { className: "size-3" }), running ? "面试进行中..." : "开始面试"] }), onRunReadiness ? (_jsx("button", { onClick: onRunReadiness, className: "px-3 py-2 text-[11px] soft-chip rounded hover:border-white/20", children: "\u5FEB\u901F\u7B5B\u68C0" })) : null] })] })] })] })] }));
}
function InterviewHallV2({ roles, providers, selectedRole, selectedProvider, onSelectRole, onSelectProvider, onRunConnectivityTest, onRunInterview, connectivityResults, interviewRunning, connectivityRunning, onSkipConnectivityTest, }) {
    const [inspectingProvider, setInspectingProvider] = useState(null);
    const activeRole = roles.find((role) => role.id === selectedRole);
    const activeProvider = providers.find((provider) => provider.id === selectedProvider);
    const activeProviderModel = activeProvider?.model?.trim() || "";
    const connectivityKey = activeRole && selectedProvider
        ? `${activeRole.id}::${selectedProvider}`
        : null;
    const directConnectivity = connectivityKey
        ? connectivityResults.get(connectivityKey)
        : undefined;
    const desiredModel = activeProviderModel;
    const matchesModel = (value) => {
        if (!desiredModel)
            return false;
        if (!value || !value.model)
            return false;
        return value.model === desiredModel;
    };
    const directMatch = matchesModel(directConnectivity);
    let fallbackConnectivity;
    if (!directMatch && selectedProvider && desiredModel) {
        let latest = 0;
        connectivityResults.forEach((value, key) => {
            if (!key.endsWith(`::${selectedProvider}`))
                return;
            if (!matchesModel(value))
                return;
            const time = Date.parse(value.timestamp);
            const parsed = Number.isNaN(time) ? 0 : time;
            if (parsed >= latest) {
                latest = parsed;
                fallbackConnectivity = value;
            }
        });
    }
    const connectivity = directMatch ? directConnectivity : fallbackConnectivity;
    const connectivityNote = connectivity?.sourceRole && connectivity?.sourceRole !== activeRole?.id
        ? `（复用自 ${connectivity.sourceRole}）`
        : !directConnectivity && fallbackConnectivity
            ? "（来自其他岗位）"
            : null;
    const connectivityState = connectivity?.ok === true
        ? "passed"
        : connectivity?.ok === false
            ? "failed"
            : "unknown";
    const connectivityLabel = connectivityState === "passed"
        ? "连通正常"
        : connectivityState === "failed"
            ? "连通失败"
            : "连通未测";
    const connectivityColor = connectivityState === "passed"
        ? "text-emerald-300"
        : connectivityState === "failed"
            ? "text-amber-300"
            : "text-text-dim";
    const connectivityOk = connectivityState === "passed";
    const canRunConnectivity = Boolean(activeRole && activeProvider && activeProviderModel);
    const canRunInterview = Boolean(activeRole && activeProvider && activeProviderModel);
    const disabledReason = !activeRole
        ? "请选择岗位"
        : !activeProvider
            ? "请选择 LLM 卡片"
            : !activeProviderModel
                ? "当前提供商未配置模型"
                : null;
    return (_jsxs("div", { className: "space-y-6", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs text-text-dim uppercase tracking-wide", children: "LLM \u9762\u8BD5\u4E2D\u67A2" }), _jsx("h3", { className: "text-lg font-semibold text-text-main", children: "\u9762\u8BD5\u5927\u5385" })] }), _jsxs("div", { className: "flex items-center gap-2 text-[10px] text-text-dim", children: [_jsx(ShieldCheck, { className: "size-4 text-emerald-300" }), "\u6838\u5FC3\u5C97\u4F4D\u987B\u914D\u601D\u8003\u578B\u6A21\u578B\u3002"] })] }), _jsxs("div", { className: "grid grid-cols-1 xl:grid-cols-[1.1fr_1.3fr_1fr] gap-6", children: [_jsxs("div", { className: "space-y-4", children: [_jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\uD83C\uDFAF \u9762\u8BD5\u5C97\u4F4D" }), roles.map((role) => {
                                const isActive = role.id === selectedRole;
                                const badge = getLlmRoleDefinition(role.id).badge;
                                return (_jsxs("button", { "data-testid": `llm-auto-role-${role.id}`, onClick: () => onSelectRole(role.id), className: `w-full text-left rounded-xl border p-4 transition-all ${isActive
                                        ? "soft-raised border-white/[0.15]"
                                        : "border-white/10 bg-white/5 hover:border-white/20"}`, children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: `px-2 py-1 text-[10px] uppercase font-semibold rounded border ${badge}`, children: role.label }), role.readiness?.ready ? (_jsx(CheckCircle2, { className: "size-4 text-emerald-400" })) : (_jsx(AlertTriangle, { className: "size-4 text-amber-300" }))] }), _jsx("div", { className: "text-[10px] text-text-dim uppercase tracking-wide", children: role.requiresThinking ? "需要思考" : "可选思考" })] }), _jsx("div", { className: "mt-2 text-xs text-text-dim", children: role.description }), _jsxs("div", { className: "mt-3 text-[11px] text-text-main", children: ["\u9ED8\u8BA4\u4EBA\u9009: ", role.candidate?.providerName || "未指定", " ", role.candidate?.model ? `• ${role.candidate.model}` : ""] })] }, role.id));
                            })] }), _jsxs("div", { className: "space-y-4", children: [_jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\uD83E\uDD16 LLM \u5361\u7247" }), providers.length === 0 ? (_jsx("div", { className: "rounded-xl border border-white/10 bg-white/5 p-6 text-center text-xs text-text-dim", children: "\u6682\u65E0\u53EF\u7528 LLM \u63D0\u4F9B\u5546\uFF0C\u8BF7\u5148\u5728\u914D\u7F6E\u9875\u6DFB\u52A0\u3002" })) : (_jsx("div", { className: "space-y-3", children: providers.map((provider) => {
                                    const isActive = provider.id === selectedProvider;
                                    const styles = STATUS_STYLES[provider.status] || STATUS_STYLES.untested;
                                    return (_jsxs("button", { "data-testid": `llm-auto-provider-${provider.id}`, onClick: () => onSelectProvider(provider.id), className: `w-full text-left rounded-xl border p-4 transition-all ${isActive
                                            ? "border-emerald-400/50 bg-emerald-500/10"
                                            : `${styles.border} ${styles.bg} hover:border-white/20`}`, children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "text-sm font-semibold text-text-main", children: provider.name }), _jsx("span", { className: `text-[10px] uppercase tracking-wide px-2 py-0.5 rounded border ${styles.border} ${styles.text}`, children: STATUS_LABELS[provider.status] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(MultiRoleInterviewStatus, { provider: provider, compact: true }), provider.interviewResults &&
                                                                                Object.keys(provider.interviewResults).length >
                                                                                    0 && (_jsx("button", { onClick: (e) => {
                                                                                    e.stopPropagation();
                                                                                    setInspectingProvider(provider);
                                                                                }, className: "text-text-dim hover:text-text-main transition-colors", children: _jsx(Info, { className: "size-3" }) }))] })] }), _jsxs("div", { className: "mt-1 text-[10px] text-text-dim", children: [provider.providerType, " \u2022", " ", provider.model || "未设置模型"] })] }), _jsxs("div", { className: "flex flex-col items-end text-[10px] text-text-dim", children: [_jsxs("span", { className: `flex items-center gap-1 ${styles.text}`, children: [_jsx("span", { className: `size-2 rounded-full ${styles.dot}` }), provider.status === "testing"
                                                                        ? "连通测试中"
                                                                        : "连通状态"] }), _jsx("span", { className: "mt-1", children: formatTimestamp(provider.lastConnectivityTest?.timestamp) })] })] }), provider.lastConnectivityTest ? (_jsxs("div", { className: "mt-3 text-[10px] text-text-dim", children: ["\u5EF6\u8FDF", " ", provider.lastConnectivityTest.latencyMs
                                                        ? `${Math.round(provider.lastConnectivityTest.latencyMs)}ms`
                                                        : "—", provider.lastConnectivityTest.error
                                                        ? ` • ${provider.lastConnectivityTest.error}`
                                                        : ""] })) : null, provider.thinkingConfidence !== undefined &&
                                                provider.thinkingConfidence !== null ? (_jsxs("div", { className: "mt-2 text-[10px] text-text-dim", children: ["\u601D\u8003\u7F6E\u4FE1\u5EA6\uFF1A", Math.round(provider.thinkingConfidence * 100), "%", provider.thinkingSupported === false
                                                        ? " (不支持)"
                                                        : ""] })) : null, provider.lastInterview ? (_jsxs("div", { className: "mt-2 text-[10px] text-text-dim", children: ["\u6700\u8FD1\u9762\u8BD5\uFF1A", provider.interviewStatus === "passed"
                                                        ? "通过"
                                                        : "未通过", " ", "\u2022 ", formatTimestamp(provider.lastInterview.timestamp)] })) : null] }, provider.id));
                                }) }))] }), _jsxs("div", { className: "space-y-4", children: [_jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\uD83E\uDDEA \u6D4B\u8BD5\u63A7\u5236\u533A" }), _jsxs("div", { className: "rounded-xl border border-white/10 bg-black/30 p-4 space-y-4", children: [_jsxs("div", { className: "space-y-2", children: [_jsx("div", { className: "text-xs text-text-main font-semibold", children: "\u5F53\u524D\u7EC4\u5408" }), _jsxs("div", { className: "text-[11px] text-text-dim", children: ["\u5C97\u4F4D\uFF1A", activeRole?.label || "未选择"] }), _jsxs("div", { className: "text-[11px] text-text-dim", children: ["\u6A21\u578B\uFF1A", activeProvider?.name || "未选择", " ", activeProviderModel ? `• ${activeProviderModel}` : ""] })] }), _jsx("div", { className: "rounded-lg border border-white/10 bg-white/5 p-3 text-[11px] text-text-dim", children: activeRole?.requiresThinking
                                            ? `核心岗位要求思考型模型（最低 ${Math.round(activeRole.minConfidence * 100)}%）。`
                                            : "辅助岗位可使用高效模型，思考能力为加分项。" }), _jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "flex items-center justify-between text-[11px]", children: [_jsx("span", { className: "text-text-dim", children: "\u8FDE\u901A\u6027\u6D4B\u8BD5" }), connectivityRunning ? (_jsxs("span", { className: "flex items-center gap-1 text-amber-200", children: [_jsx(Loader2, { className: "size-3 animate-spin" }), "\u8FD0\u884C\u4E2D"] })) : (_jsx("span", { className: connectivityColor, children: connectivityLabel }))] }), _jsx("div", { className: "text-[10px] text-text-dim", children: connectivity?.timestamp
                                                    ? `最近：${formatTimestamp(connectivity.timestamp)}${connectivityNote ? ` ${connectivityNote}` : ""}`
                                                    : "尚无记录" }), connectivity?.error ? (_jsx("div", { className: "text-[10px] text-red-300", children: connectivity.error })) : null, !connectivityOk &&
                                                onSkipConnectivityTest &&
                                                activeRole &&
                                                activeProvider ? (_jsx("button", { type: "button", onClick: () => onSkipConnectivityTest(activeRole.id, activeProvider.id), className: "px-2 py-1 text-[10px] border border-amber-500/40 text-amber-300 rounded hover:bg-amber-500/10 transition-colors", children: "\u8DF3\u8FC7\u8FDE\u901A\u6027\u6D4B\u8BD5" })) : null] }), disabledReason ? (_jsx("div", { className: "text-[11px] text-red-200 bg-red-500/10 border border-red-500/20 rounded p-2", children: disabledReason })) : null, _jsxs("div", { className: "flex flex-col gap-2", children: [_jsxs("button", { "data-testid": "llm-auto-run-connectivity", onClick: () => {
                                                    if (activeRole && activeProvider && activeProviderModel) {
                                                        onRunConnectivityTest({
                                                            role: activeRole.id,
                                                            providerId: activeProvider.id,
                                                            model: activeProviderModel,
                                                        });
                                                    }
                                                }, disabled: !canRunConnectivity || connectivityRunning, className: "px-3 py-2 text-[11px] font-semibold bg-white/[0.12] hover:bg-white/[0.16] text-text-main rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1", children: [_jsx(Cpu, { className: "size-3" }), connectivityRunning ? "连通性测试中..." : "连通性测试"] }), _jsxs("button", { "data-testid": "llm-auto-run-interview", onClick: () => {
                                                    if (activeRole && activeProvider && activeProviderModel) {
                                                        onRunInterview({
                                                            role: activeRole.id,
                                                            providerId: activeProvider.id,
                                                            model: activeProviderModel,
                                                        });
                                                    }
                                                }, disabled: !canRunInterview || interviewRunning, className: "px-3 py-2 text-[11px] font-semibold bg-emerald-500/[0.08]0 hover:bg-emerald-500 text-white rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1", children: [_jsx(Zap, { className: "size-3" }), interviewRunning ? "面试进行中..." : "深度面试"] })] })] })] })] }), inspectingProvider && (_jsx(InterviewDetailsModal, { provider: inspectingProvider, onClose: () => setInspectingProvider(null) }))] }));
}
export function InterviewHall(props) {
    if ("providers" in props) {
        return _jsx(InterviewHallV2, { ...props });
    }
    return _jsx(InterviewHallLegacy, { ...props });
}
