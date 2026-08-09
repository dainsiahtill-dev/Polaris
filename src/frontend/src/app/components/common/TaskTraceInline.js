import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { memo } from 'react';
function TaskTraceInlineComponent({ traces, maxLines = 1, className }) {
    const latestTrace = traces[traces.length - 1];
    if (!latestTrace)
        return null;
    const getStatusColor = (status) => {
        switch (status) {
            case 'completed': return 'text-green-400';
            case 'failed': return 'text-red-400';
            case 'running': return 'text-blue-400';
            case 'started': return 'text-yellow-400';
            default: return 'text-gray-400';
        }
    };
    const getStatusDotColor = (status) => {
        switch (status) {
            case 'completed': return 'bg-green-400';
            case 'failed': return 'bg-red-400';
            case 'running': return 'bg-blue-400';
            case 'started': return 'bg-yellow-400';
            case 'retry': return 'bg-orange-400';
            default: return 'bg-gray-400';
        }
    };
    return (_jsxs("div", { className: `text-sm ${className}`, children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: `inline-block w-2 h-2 rounded-full ${getStatusDotColor(latestTrace.status)}` }), _jsx("span", { className: "text-gray-300", children: latestTrace.step_title }), _jsx("span", { className: "text-gray-500 text-xs", children: latestTrace.ts && new Date(latestTrace.ts).toLocaleTimeString() })] }), maxLines > 1 && latestTrace.step_detail && (_jsx("p", { className: "text-gray-400 mt-1 truncate", children: latestTrace.step_detail }))] }));
}
export const TaskTraceInline = memo(TaskTraceInlineComponent);
