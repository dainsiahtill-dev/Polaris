import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { getLlmRoleDefinition, normalizeLlmRoleId, } from "../roleDefinitions";
const FALLBACK_ROLE_META = {
    label: "Unknown",
    badge: "bg-white/[0.08] text-text-main border-white/[0.12]",
};
const resolveRoleMeta = (roleId) => {
    const normalized = normalizeLlmRoleId(roleId);
    if (!normalized)
        return { ...FALLBACK_ROLE_META, label: roleId || "Unknown" };
    const definition = getLlmRoleDefinition(normalized);
    return { label: definition.label, badge: definition.badge };
};
export const RoleBadge = ({ roleId, result }) => {
    if (result.status === "none")
        return null;
    const isSuccess = result.status === "passed";
    const meta = resolveRoleMeta(roleId);
    return (_jsxs("div", { className: `inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded border ${isSuccess ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" : "border-rose-500/30 bg-rose-500/10 text-rose-300"}`, children: [_jsx("span", { className: `px-1 py-0.5 rounded ${meta.badge}`, children: meta.label.split(" ")[0] }), _jsx("span", { children: isSuccess ? "✓" : "✗" })] }));
};
export const MultiRoleInterviewStatus = ({ provider, compact = false }) => {
    const results = provider.interviewResults || {};
    const resultValues = Object.values(results);
    const resultEntries = Object.entries(results);
    const passedCount = resultValues.filter((r) => r.status === "passed").length;
    const failedCount = resultValues.filter((r) => r.status === "failed").length;
    if (passedCount === 0 && failedCount === 0) {
        if (provider.interviewStatus && provider.interviewStatus !== "none") {
            const isSuccess = provider.interviewStatus === "passed";
            return (_jsx("span", { className: `text-[9px] uppercase px-1.5 py-0.5 rounded border ${isSuccess ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" : "border-rose-500/30 bg-rose-500/10 text-rose-300"}`, children: isSuccess ? "面试通过" : "面试失败" }));
        }
        return null;
    }
    if (compact) {
        return (_jsxs("div", { className: "flex items-center gap-1", children: [passedCount > 0 && (_jsxs("div", { className: "flex items-center gap-1", children: [_jsxs("span", { className: "text-[8px] text-emerald-300 bg-emerald-500/10 px-1.5 py-0.5 rounded", children: ["+", passedCount] }), _jsx("div", { className: "flex -space-x-1", children: resultEntries
                                .filter(([_, r]) => r.status === "passed")
                                .map(([roleId]) => {
                                const meta = resolveRoleMeta(roleId);
                                return (_jsx("div", { className: `w-3 h-3 rounded-full border border-white/20 ${meta.badge}`, title: meta.label }, roleId));
                            }) })] })), failedCount > 0 && (_jsxs("div", { className: "flex items-center gap-1", children: [_jsxs("span", { className: "text-[8px] text-rose-300 bg-rose-500/10 px-1.5 py-0.5 rounded", children: ["-", failedCount] }), _jsx("div", { className: "flex -space-x-1 opacity-50", children: resultEntries
                                .filter(([_, r]) => r.status === "failed")
                                .map(([roleId]) => {
                                const meta = resolveRoleMeta(roleId);
                                return (_jsx("div", { className: `w-3 h-3 rounded-full border border-white/20 ${meta.badge}`, title: meta.label }, roleId));
                            }) })] }))] }));
    }
    return (_jsx("div", { className: "flex flex-wrap gap-1", children: resultEntries.map(([roleId, result]) => (_jsx(RoleBadge, { roleId: roleId, result: result }, roleId))) }));
};
export const InterviewDetailsModal = ({ provider, onClose }) => {
    const results = provider.interviewResults || {};
    const resultValues = Object.values(results);
    const resultEntries = Object.entries(results);
    return (_jsx("div", { className: "fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4", children: _jsxs("div", { className: "soft-panel rounded-xl p-4 max-w-md w-full max-h-[80vh] overflow-y-auto", children: [_jsxs("div", { className: "flex items-center justify-between mb-4", children: [_jsxs("h3", { className: "text-sm font-semibold text-text-main", children: [provider.name, " \u9762\u8BD5\u8BE6\u60C5"] }), _jsx("button", { onClick: onClose, className: "text-text-dim hover:text-text-main", children: "\u2715" })] }), _jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "soft-inset rounded-lg p-3", children: [_jsx("div", { className: "text-[10px] text-text-dim mb-1", children: "\u6A21\u578B\u4FE1\u606F" }), _jsx("div", { className: "text-xs text-text-main", children: provider.model }), _jsx("div", { className: "text-[9px] text-text-dim", children: provider.providerType })] }), _jsxs("div", { children: [_jsx("div", { className: "text-[10px] text-text-dim mb-2", children: "\u9762\u8BD5\u7ED3\u679C" }), _jsxs("div", { className: "space-y-2", children: [resultEntries.map(([roleId, result]) => {
                                            if (result.status === "none")
                                                return null;
                                            const meta = resolveRoleMeta(roleId);
                                            const isSuccess = result.status === "passed";
                                            return (_jsxs("div", { className: "flex items-center justify-between soft-inset rounded p-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: `px-2 py-1 text-[9px] rounded ${meta.badge}`, children: meta.label }), _jsx("span", { className: `text-[10px] ${isSuccess ? "text-emerald-300" : "text-rose-300"}`, children: isSuccess ? "通过" : "失败" })] }), _jsxs("div", { className: "flex items-center gap-2 text-[9px] text-text-dim", children: [result.score && (_jsxs("span", { className: "bg-white/10 px-1.5 py-0.5 rounded", children: ["\u5206\u6570: ", result.score.toFixed(1)] })), result.timestamp && (_jsx("span", { children: new Date(result.timestamp).toLocaleDateString() }))] })] }, roleId));
                                        }), resultValues.filter((r) => r.status !== "none").length === 0 && (_jsx("div", { className: "text-[10px] text-text-dim text-center py-4", children: "\u6682\u65E0\u9762\u8BD5\u8BB0\u5F55" }))] })] })] }), _jsx("button", { onClick: onClose, className: "mt-4 w-full px-3 py-1.5 text-[10px] bg-white/10 hover:bg-white/20 rounded", children: "\u5173\u95ED" })] }) }));
};
