import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { AlertTriangle, CheckCircle, Info, XCircle } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
const alertConfig = {
    success: {
        icon: CheckCircle,
        base: 'bg-green-500/10 border-green-500/20 text-green-400',
        iconColor: 'text-green-400',
    },
    error: {
        icon: XCircle,
        base: 'bg-red-500/10 border-red-500/20 text-red-400',
        iconColor: 'text-red-400',
    },
    warning: {
        icon: AlertTriangle,
        base: 'bg-yellow-500/10 border-yellow-500/20 text-yellow-400',
        iconColor: 'text-yellow-400',
    },
    info: {
        icon: Info,
        base: 'bg-blue-500/10 border-blue-500/20 text-blue-400',
        iconColor: 'text-blue-400',
    },
};
export function EnhancedAlert({ type, title, message, details, action, onClose, className }) {
    const config = alertConfig[type];
    const Icon = config.icon;
    return (_jsx("div", { className: cn('relative p-4 rounded-lg border', config.base, className), children: _jsxs("div", { className: "flex gap-3", children: [_jsx("div", { className: "flex-shrink-0", children: _jsx(Icon, { className: cn('h-5 w-5', config.iconColor) }) }), _jsxs("div", { className: "flex-1 min-w-0", children: [title && (_jsx("h3", { className: "text-sm font-semibold mb-1", children: title })), _jsx("p", { className: "text-sm leading-relaxed", children: message }), details && (_jsxs("details", { className: "mt-2", children: [_jsx("summary", { className: "text-xs cursor-pointer hover:text-gray-300 transition-colors", children: "\u67E5\u770B\u8BE6\u60C5" }), _jsx("div", { className: "mt-2 text-xs text-gray-400 bg-black/20 rounded p-2", children: details })] })), action && (_jsx("div", { className: "mt-3", children: _jsx("button", { onClick: action.onClick, className: "text-xs px-3 py-1.5 rounded bg-white/10 hover:bg-white/20 transition-colors", children: action.label }) }))] }), onClose && (_jsx("button", { onClick: onClose, className: "flex-shrink-0 text-gray-400 hover:text-gray-200 transition-colors", children: _jsx("svg", { className: "h-4 w-4", fill: "currentColor", viewBox: "0 0 20 20", children: _jsx("path", { fillRule: "evenodd", d: "M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z", clipRule: "evenodd" }) }) }))] }) }));
}
