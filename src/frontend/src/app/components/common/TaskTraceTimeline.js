import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
import { useState, memo } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
function TaskTraceTimelineComponent({ traces, maxTraces = 20, expanded = false, className }) {
    const [isExpanded, setIsExpanded] = useState(expanded);
    const displayTraces = isExpanded ? traces.slice(-maxTraces) : traces.slice(-1);
    const getStatusColor = (status) => {
        switch (status) {
            case 'completed': return 'bg-green-400';
            case 'failed': return 'bg-red-400';
            case 'running': return 'bg-blue-400';
            case 'started': return 'bg-yellow-400';
            case 'retry': return 'bg-orange-400';
            default: return 'bg-gray-400';
        }
    };
    const getStatusIcon = (status) => {
        switch (status) {
            case 'completed': return '✓';
            case 'failed': return '✗';
            case 'running': return '⋯';
            case 'retry': return '↻';
            default: return '○';
        }
    };
    if (traces.length === 0)
        return null;
    return (_jsxs("div", { className: `${className}`, children: [_jsxs("div", { className: "flex items-center justify-between mb-2", children: [_jsxs("span", { className: "text-xs text-gray-500", children: ["\u6267\u884C\u6B65\u9AA4 (", traces.length, ")"] }), traces.length > 1 && (_jsxs("button", { onClick: () => setIsExpanded(!isExpanded), className: "text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1", children: [isExpanded ? '收起' : '展开', isExpanded ? _jsx(ChevronUp, { size: 14 }) : _jsx(ChevronDown, { size: 14 })] }))] }), _jsx("div", { className: "space-y-1", children: displayTraces.map((trace, idx) => (_jsxs("div", { className: "flex items-start gap-2 text-sm", children: [_jsx("span", { className: `inline-flex items-center justify-center w-5 h-5 rounded-full text-xs ${getStatusColor(trace.status)} text-black font-bold shrink-0`, children: getStatusIcon(trace.status) }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "text-gray-300 truncate", children: trace.step_title }), isExpanded && trace.step_detail && (_jsx("div", { className: "text-gray-500 text-xs truncate", children: trace.step_detail }))] }), _jsx("span", { className: "text-gray-600 text-xs shrink-0", children: trace.ts && new Date(trace.ts).toLocaleTimeString() })] }, trace.event_id || idx))) })] }));
}
export const TaskTraceTimeline = memo(TaskTraceTimelineComponent);
