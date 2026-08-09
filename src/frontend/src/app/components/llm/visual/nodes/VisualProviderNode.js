import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Handle, Position } from '@xyflow/react';
const STATUS_STYLES = {
    ready: 'text-emerald-300',
    success: 'text-emerald-300',
    failed: 'text-rose-300',
    testing: 'text-slate-300',
    running: 'text-slate-300',
    unknown: 'text-amber-300',
};
const STATUS_LABELS = {
    ready: '就绪',
    success: '连通正常',
    failed: '连通失败',
    testing: '测试中',
    running: '测试中',
    unknown: '连通未知',
};
export function VisualProviderNode({ data }) {
    const statusClass = data.status ? STATUS_STYLES[data.status] || 'text-text-dim' : 'text-text-dim';
    const statusLabel = data.status ? STATUS_LABELS[data.status] || data.status : '待命';
    return (_jsxs("div", { className: "min-w-[200px] rounded-lg border border-slate-500/40 bg-black/70 px-3 py-2 text-text-main", "data-provider-id": data.providerId, "data-provider-status": data.status || 'unknown', children: [_jsx(Handle, { type: "source", position: Position.Right, className: "!bg-slate-400 !border-slate-300" }), _jsxs("div", { className: "flex items-center justify-between", children: [_jsx("div", { className: "text-xs font-semibold", children: data.label }), _jsx("div", { className: `text-[9px] ${statusClass}`, children: statusLabel })] }), _jsxs("div", { className: "mt-1 text-[10px] text-text-dim", children: [data.providerType ? data.providerType : '提供商', typeof data.modelCount === 'number' ? ` • ${data.modelCount} 个模型` : ''] }), data.costClass ? (_jsx("div", { className: "mt-2 inline-flex rounded bg-black/40 px-2 py-0.5 text-[9px] text-text-dim", children: data.costClass })) : null] }));
}
