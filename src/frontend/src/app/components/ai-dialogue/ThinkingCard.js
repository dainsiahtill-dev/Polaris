import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { Brain, ChevronDown, ChevronRight, CheckCircle2, Circle, Loader2, Sparkles, XCircle, } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
export function ThinkingCard({ intent, planSteps, toolStatus, decisions, thinking, roleName = 'AI', theme = { primary: 'indigo', secondary: 'indigo-400' }, }) {
    const [expanded, setExpanded] = useState(true);
    const hasThinkingContent = intent || planSteps?.length || toolStatus || decisions?.length;
    // 检查是否有正在运行的任务
    const isRunning = planSteps?.some(s => s.status === 'running') ||
        Object.values(toolStatus || {}).some(s => s === 'running');
    if (!hasThinkingContent && !thinking) {
        return null;
    }
    const getStatusIcon = (status) => {
        switch (status) {
            case 'completed':
                return _jsx(CheckCircle2, { className: "w-3 h-3 text-emerald-400" });
            case 'running':
                return _jsx(Loader2, { className: "w-3 h-3 text-amber-400 animate-spin" });
            case 'failed':
                return _jsx(XCircle, { className: "w-3 h-3 text-red-400" });
            default:
                return _jsx(Circle, { className: "w-3 h-3 text-slate-500" });
        }
    };
    return (_jsxs("div", { className: cn("mb-3 rounded-lg border border-white/10 bg-slate-900/50 overflow-hidden", isRunning && "border-amber-500/30"), children: [_jsxs("button", { onClick: () => setExpanded(!expanded), className: "w-full flex items-center justify-between px-3 py-2 hover:bg-white/5 transition-colors", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Brain, { className: cn("w-4 h-4", isRunning ? "text-amber-400" : "text-slate-400") }), _jsxs("span", { className: "text-xs font-medium text-slate-300", children: [roleName, " ", isRunning ? '处理中' : '思考中'] }), intent?.progress !== undefined && (_jsxs("span", { className: cn("text-[10px]", intent.progress === 100 ? "text-emerald-400" : "text-slate-500"), children: ["[", intent.progress, "%]"] }))] }), expanded ? (_jsx(ChevronDown, { className: "w-4 h-4 text-slate-500" })) : (_jsx(ChevronRight, { className: "w-4 h-4 text-slate-500" }))] }), expanded && (_jsxs("div", { className: "px-3 pb-3 space-y-3", children: [intent && (_jsxs("div", { className: "flex items-center gap-2 text-xs", children: [_jsx(Sparkles, { className: "w-3 h-3 text-amber-400" }), _jsx("span", { className: "text-slate-400", children: "\u610F\u56FE:" }), _jsx("span", { className: "text-slate-200", children: intent.target })] })), planSteps && planSteps.length > 0 && (_jsxs("div", { children: [_jsx("div", { className: "text-[10px] text-slate-500 mb-1", children: "\u8BA1\u5212\u8FDB\u5EA6:" }), _jsx("div", { className: "space-y-1", children: planSteps.map((step, idx) => (_jsxs("div", { className: cn('flex items-center gap-2 text-xs px-2 py-1 rounded', step.status === 'running' && 'bg-amber-500/10', step.status === 'completed' && 'bg-emerald-500/10'), children: [getStatusIcon(step.status), _jsxs("span", { className: cn('text-slate-400', step.status === 'completed' && 'text-slate-500 line-through', step.status === 'running' && 'text-amber-400'), children: [step.step, ". ", step.label] })] }, idx))) })] })), toolStatus && Object.keys(toolStatus).length > 0 && (_jsxs("div", { children: [_jsx("div", { className: "text-[10px] text-slate-500 mb-1", children: "\u5DE5\u5177:" }), _jsx("div", { className: "flex flex-wrap gap-2", children: Object.entries(toolStatus).map(([tool, status]) => (_jsxs("div", { className: cn('flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border', status === 'completed' && 'bg-emerald-500/10 border-emerald-500/20', status === 'running' && 'bg-amber-500/10 border-amber-500/20', status === 'failed' && 'bg-red-500/10 border-red-500/20', status === 'pending' && 'bg-slate-800 border-slate-700'), children: [getStatusIcon(status), _jsx("span", { className: "text-slate-400", children: tool })] }, tool))) })] })), decisions && decisions.length > 0 && (_jsx("div", { className: "space-y-1", children: decisions.map((decision, idx) => (_jsxs("div", { className: "text-[10px] text-slate-500", children: [_jsx("span", { className: "text-amber-400", children: "\u51B3\u7B56:" }), ' ', _jsx("span", { className: "text-slate-300", children: decision.content }), decision.reason && (_jsxs("span", { className: "text-slate-500", children: ["\uFF08", decision.reason, "\uFF09"] }))] }, idx))) })), thinking && (_jsx("div", { className: "mt-2 p-2 rounded bg-slate-950/50 border border-white/5", children: _jsx("p", { className: "text-[10px] text-slate-500 whitespace-pre-wrap", children: thinking }) }))] }))] }));
}
