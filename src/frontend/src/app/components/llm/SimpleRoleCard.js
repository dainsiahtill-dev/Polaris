import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { CheckCircle2, AlertTriangle, Loader2, PlayCircle, ChevronDown, Settings, } from "lucide-react";
import { useState } from "react";
import { getLlmRoleDefinition } from "./roleDefinitions";
const STATUS_COLORS = {
    unconfigured: "text-gray-400",
    ready: "text-emerald-400",
    failed: "text-red-400",
    degraded: "text-amber-400",
};
const STATUS_BADGES = {
    unconfigured: "bg-white/[0.06] text-text-dim border-white/10",
    ready: "bg-emerald-500/[0.12] text-emerald-200 border-emerald-500/25",
    failed: "bg-rose-500/[0.12] text-rose-200 border-rose-500/25",
    degraded: "bg-amber-500/[0.12] text-amber-200 border-amber-500/25",
};
const STATUS_LABELS = {
    unconfigured: "未设",
    ready: "就绪",
    failed: "失准",
    degraded: "降级",
};
export function SimpleRoleCard({ role, availableProviders, onUpdate, onTestRole, onViewTestReport, }) {
    const [isExpanded, setIsExpanded] = useState(false);
    const [isTesting, setIsTesting] = useState(false);
    const meta = getLlmRoleDefinition(role.role);
    const readyProviders = availableProviders.filter((p) => p.status === "ready");
    const selectedProvider = availableProviders.find((p) => p.id === role.providerId);
    const handleProviderChange = (providerId) => {
        onUpdate({ providerId, status: "unconfigured" });
    };
    const handleRunRoleTest = async () => {
        setIsTesting(true);
        try {
            await onTestRole();
        }
        finally {
            setIsTesting(false);
        }
    };
    const renderStatusIndicator = () => {
        switch (role.status) {
            case "ready":
                return _jsx(CheckCircle2, { className: "size-4 text-emerald-400" });
            case "failed":
                return _jsx(AlertTriangle, { className: "size-4 text-red-400" });
            case "degraded":
                return _jsx(AlertTriangle, { className: "size-4 text-amber-400" });
            default:
                return _jsx("div", { className: "size-4 rounded-full bg-gray-500/60" });
        }
    };
    const renderCompactView = () => (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-3", children: [renderStatusIndicator(), _jsxs("div", { children: [_jsx("h4", { className: "text-sm font-semibold text-text-main", children: meta.label }), _jsx("p", { className: "text-[10px] text-text-dim", children: meta.description })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("button", { onClick: handleRunRoleTest, disabled: isTesting || !role.providerId || role.status === "degraded", className: "px-3 py-1.5 text-[10px] font-semibold bg-white/[0.12] hover:bg-white/[0.16] text-text-main rounded transition-colors disabled:opacity-60 flex items-center gap-1", children: [isTesting ? (_jsx(Loader2, { className: "size-3 animate-spin" })) : (_jsx(PlayCircle, { className: "size-3" })), "\u8BD5\u8FD0\u884C"] }), _jsx("button", { onClick: () => setIsExpanded(!isExpanded), className: "p-1.5 soft-chip rounded hover:border-white/20 transition-colors", children: isExpanded ? (_jsx(ChevronDown, { className: "size-3 rotate-180" })) : (_jsx(ChevronDown, { className: "size-3" })) })] })] }), _jsxs("div", { className: "flex items-center gap-3", children: [_jsx("label", { className: "text-xs text-text-muted", children: "\u6A21\u578B:" }), _jsxs("select", { value: role.providerId || "", onChange: (e) => handleProviderChange(e.target.value), disabled: readyProviders.length === 0, className: "flex-1 bg-white/5 text-text-main px-3 py-2 rounded border border-white/[0.08] text-sm focus:outline-none focus:border-white/20 focus:ring-1 focus:ring-white/10 disabled:opacity-60", children: [_jsx("option", { value: "", children: "\u8BF7\u9009\u62E9\u6A21\u578B..." }), readyProviders.map((provider) => (_jsxs("option", { value: provider.id, children: [provider.name, " (", provider.modelId, ")"] }, provider.id)))] })] }), readyProviders.length === 0 && (_jsx("div", { className: "text-[10px] text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded p-2", children: "\u6682\u65E0\u53EF\u7528\u6A21\u578B\uFF0C\u8BF7\u5148\u5728\u7B2C\u4E00\u6B65\u5B8C\u6210\u914D\u7F6E\u5E76\u901A\u8FC7\u6D4B\u8BD5\u3002" })), role.status === "degraded" && selectedProvider && (_jsxs("div", { className: "text-[10px] text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded p-2", children: ["\u5DF2\u5206\u914D\u6A21\u578B\u201C", selectedProvider.name, "\u201D\u5F53\u524D\u975E\u5C31\u7EEA\u3002 \u8BF7\u5148\u5728\u7B2C\u4E00\u6B65\u4FEE\u590D\uFF0C\u6216\u6539\u914D\u5176\u4ED6\u6A21\u578B\u3002"] })), role.status === "failed" && role.lastTest?.reason && (_jsxs("div", { className: "text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 rounded p-2", children: ["\u89D2\u8272\u8BD5\u8FD0\u884C\u5931\u8D25: ", role.lastTest.reason] }))] }));
    const renderExpandedView = () => (_jsxs("div", { className: "space-y-4 pt-4 border-t border-white/10", children: [selectedProvider && (_jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "\u5DF2\u9009\u6A21\u578B" }), _jsxs("div", { className: "space-y-2 text-xs", children: [_jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-text-muted", children: "\u540D\u79F0:" }), _jsx("span", { className: "text-text-main", children: selectedProvider.name })] }), _jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-text-muted", children: "\u7C7B\u578B:" }), _jsx("span", { className: "text-text-main capitalize", children: selectedProvider.kind })] }), _jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-text-muted", children: "\u6A21\u578B ID:" }), _jsx("span", { className: "text-text-main font-mono", children: selectedProvider.modelId })] }), selectedProvider.lastTest?.latencyMs && (_jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-text-muted", children: "\u65F6\u5EF6:" }), _jsxs("span", { className: "text-text-main", children: [selectedProvider.lastTest.latencyMs, "ms"] })] }))] })] })), _jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "\u89D2\u8272\u8BD5\u8FD0\u884C" }), _jsx("div", { className: "text-xs text-text-dim", children: meta.testDescription }), role.lastTest && (_jsxs("div", { className: "space-y-2 text-xs", children: [_jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-text-muted", children: "\u6700\u8FD1\u6D4B\u8BD5:" }), _jsx("span", { className: "text-text-main", children: new Date(role.lastTest.at).toLocaleString() })] }), _jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-text-muted", children: "\u7ED3\u679C:" }), _jsx("span", { className: `font-semibold ${role.lastTest.result === "pass"
                                            ? "text-emerald-400"
                                            : "text-red-400"}`, children: role.lastTest.result === "pass" ? "通过" : "失败" })] }), role.lastTest.reason && (_jsx("div", { className: "text-text-main", children: role.lastTest.reason }))] }))] }), _jsxs("div", { className: "flex items-center gap-2 pt-3 border-t border-white/10", children: [_jsxs("button", { onClick: handleRunRoleTest, disabled: isTesting || !role.providerId || role.status === "degraded", className: "px-3 py-1.5 text-[10px] font-semibold bg-white/[0.12] hover:bg-white/[0.16] text-text-main rounded transition-colors disabled:opacity-60 flex items-center gap-1", children: [isTesting ? (_jsx(Loader2, { className: "size-3 animate-spin" })) : (_jsx(PlayCircle, { className: "size-3" })), "\u8BD5\u8FD0\u884C"] }), onViewTestReport && role.lastTest && (_jsxs("button", { onClick: onViewTestReport, className: "px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-accent/40 flex items-center gap-1", children: [_jsx(Settings, { className: "size-3" }), "\u67E5\u770B\u56DE\u6267"] }))] })] }));
    return (_jsxs("div", { className: "bg-white/5 rounded-xl p-4 border border-white/10 hover:border-white/20 transition-all", children: [_jsx("div", { className: "flex items-center justify-between mb-2", children: _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: `px-2 py-1 text-[10px] uppercase font-semibold rounded border ${meta.badge}`, children: meta.label }), _jsx("span", { className: `px-2 py-1 text-[10px] uppercase font-semibold rounded border ${STATUS_BADGES[role.status]}`, children: STATUS_LABELS[role.status] })] }) }), _jsxs(_Fragment, { children: [renderCompactView(), isExpanded && renderExpandedView()] })] }));
}
