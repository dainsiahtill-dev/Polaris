import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * ExecutionProgressBar - 目标执行进度条组件
 *
 * Phase 1.2: Goal Execution Projection
 */
import { Clock, Code, FileSearch, Lightbulb, TestTube, CheckCircle2 } from 'lucide-react';
import { Badge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
const stageConfig = {
    planning: {
        label: '规划',
        color: 'text-amber-400',
        bgColor: 'bg-amber-400',
        icon: _jsx(Lightbulb, { className: "size-3" }),
    },
    coding: {
        label: '编码',
        color: 'text-slate-300',
        bgColor: 'bg-slate-300',
        icon: _jsx(Code, { className: "size-3" }),
    },
    testing: {
        label: '测试',
        color: 'text-violet-400',
        bgColor: 'bg-violet-400',
        icon: _jsx(TestTube, { className: "size-3" }),
    },
    review: {
        label: '审查',
        color: 'text-blue-400',
        bgColor: 'bg-blue-400',
        icon: _jsx(FileSearch, { className: "size-3" }),
    },
    completed: {
        label: '完成',
        color: 'text-emerald-400',
        bgColor: 'bg-emerald-400',
        icon: _jsx(CheckCircle2, { className: "size-3" }),
    },
    unknown: {
        label: '未知',
        color: 'text-slate-400',
        bgColor: 'bg-slate-400',
        icon: _jsx(Clock, { className: "size-3" }),
    },
};
export function ExecutionProgressBar({ execution, compact = false }) {
    const stage = execution.stage || 'unknown';
    const config = stageConfig[stage] || stageConfig.unknown;
    const percent = Math.round((execution.percent || 0) * 100);
    const filledBlocks = Math.floor(percent / 10);
    const emptyBlocks = 10 - filledBlocks;
    if (compact) {
        return (_jsxs("div", { className: "flex items-center gap-2 text-xs", children: [_jsxs(Badge, { variant: "outline", className: cn('gap-1 border-transparent px-1.5 py-0 text-xs', config.color), children: [config.icon, config.label] }), _jsxs("div", { className: "flex", children: [Array.from({ length: filledBlocks }).map((_, i) => (_jsx("div", { className: cn('mr-0.5 h-1.5 w-1.5 rounded-sm', config.bgColor) }, i))), Array.from({ length: emptyBlocks }).map((_, i) => (_jsx("div", { className: "mr-0.5 h-1.5 w-1.5 rounded-sm bg-slate-700" }, i)))] }), _jsxs("span", { className: "text-slate-500", children: [percent, "%"] })] }));
    }
    return (_jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs(Badge, { variant: "outline", className: cn('gap-1.5 border-transparent', config.color), children: [config.icon, config.label] }), _jsxs("span", { className: cn('text-sm font-medium', config.color), children: [percent, "%"] })] }), _jsxs("div", { className: "flex", children: [Array.from({ length: filledBlocks }).map((_, i) => (_jsx("div", { className: cn('mr-1 h-2 w-2 rounded-sm', config.bgColor) }, i))), Array.from({ length: emptyBlocks }).map((_, i) => (_jsx("div", { className: "mr-1 h-2 w-2 rounded-sm bg-slate-700" }, i)))] }), execution.current_task && (_jsx("div", { className: "text-xs text-slate-400 truncate", children: execution.current_task })), execution.eta_minutes !== undefined && execution.eta_minutes > 0 && (_jsxs("div", { className: "flex items-center gap-1 text-xs text-slate-500", children: [_jsx(Clock, { className: "size-3" }), "\u9884\u8BA1 ", execution.eta_minutes, " \u5206\u949F"] })), _jsxs("div", { className: "flex items-center gap-3 text-xs text-slate-500", children: [_jsxs("span", { children: ["\u4EFB\u52A1: ", execution.completed_tasks, "/", execution.total_tasks] }), execution.failed_tasks > 0 && (_jsxs("span", { className: "text-red-400", children: ["\u5931\u8D25: ", execution.failed_tasks] }))] })] }));
}
export default ExecutionProgressBar;
