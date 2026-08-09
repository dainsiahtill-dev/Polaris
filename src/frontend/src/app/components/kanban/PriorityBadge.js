import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { memo } from 'react';
import { AlertOctagon, AlertCircle, Minus, ArrowDown } from 'lucide-react';
const PRIORITY_CONFIG = {
    urgent: {
        label: 'Urgent',
        icon: AlertOctagon,
        color: 'text-red-400',
        bg: 'bg-red-500/20',
    },
    high: {
        label: 'High',
        icon: AlertCircle,
        color: 'text-orange-400',
        bg: 'bg-orange-500/20',
    },
    medium: {
        label: 'Medium',
        icon: Minus,
        color: 'text-yellow-400',
        bg: 'bg-yellow-500/20',
    },
    low: {
        label: 'Low',
        icon: ArrowDown,
        color: 'text-slate-400',
        bg: 'bg-slate-500/20',
    },
};
function PriorityBadgeComponent({ priority, showLabel = true }) {
    const config = PRIORITY_CONFIG[priority] ?? PRIORITY_CONFIG.medium;
    const Icon = config.icon;
    return (_jsxs("span", { className: `inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${config.color} ${config.bg}`, title: `Priority: ${config.label}`, children: [_jsx(Icon, { className: "size-3" }), showLabel && _jsx("span", { children: config.label })] }));
}
export const PriorityBadge = memo(PriorityBadgeComponent);
