import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Handle, Position } from '@xyflow/react';
import { getRoleDisplayLabel } from '@/app/constants/roleLabels';
export function VisualModelNode({ data }) {
    return (_jsxs("div", { className: "min-w-[200px] rounded-lg border border-slate-500/30 bg-black/60 px-3 py-2 text-text-main", children: [_jsx(Handle, { type: "target", position: Position.Left, className: "!bg-slate-300 !border-slate-200" }), _jsx(Handle, { type: "source", position: Position.Right, className: "!bg-emerald-200 !border-emerald-100" }), _jsx("div", { className: "text-xs font-semibold", children: data.label }), _jsxs("div", { className: "mt-1 text-[10px] text-text-dim", children: ["\u63D0\u4F9B\u5546: ", data.providerId] }), data.assignedRoles && data.assignedRoles.length > 0 ? (_jsx("div", { className: "mt-2 flex flex-wrap gap-1", children: data.assignedRoles.map((role) => (_jsx("span", { className: "rounded bg-emerald-500/20 px-2 py-0.5 text-[9px] text-emerald-200", children: getRoleDisplayLabel(role) }, role))) })) : (_jsx("div", { className: "mt-2 text-[9px] text-text-dim", children: "\u672A\u8FDE\u63A5\u89D2\u8272" }))] }));
}
