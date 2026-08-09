"use client";
import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMemo } from 'react';
import { CheckCircle2, XCircle, AlertTriangle, AlertCircle, RotateCcw, Target } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
export function QualityGateCard({ data, className }) {
    const scoreColor = useMemo(() => {
        if (!data)
            return 'text-white/30';
        if (data.score >= 80)
            return 'text-emerald-400';
        if (data.score >= 60)
            return 'text-amber-400';
        return 'text-red-400';
    }, [data]);
    const scoreBg = useMemo(() => {
        if (!data)
            return 'bg-white/5';
        if (data.score >= 80)
            return 'bg-emerald-500/10 border-emerald-500/30';
        if (data.score >= 60)
            return 'bg-amber-500/10 border-amber-500/30';
        return 'bg-red-500/10 border-red-500/30';
    }, [data]);
    if (!data)
        return null;
    const criticalCount = data.issues.filter((i) => i.type === 'critical').length;
    const warningCount = data.issues.filter((i) => i.type === 'warning').length;
    return (_jsxs("div", { className: cn("rounded-xl border p-4 transition-all", scoreBg, className), children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Target, { className: cn("h-4 w-4", scoreColor) }), _jsx("span", { className: "text-xs font-bold text-white/70", children: "\u8D28\u91CF\u95E8\u63A7\u68C0\u67E5" })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("div", { className: cn("text-lg font-bold", scoreColor), children: [data.score, _jsx("span", { className: "text-xs font-normal text-white/30", children: "/100" })] }), data.passed ? (_jsx(CheckCircle2, { className: "h-5 w-5 text-emerald-400" })) : (_jsx(XCircle, { className: "h-5 w-5 text-red-400" }))] })] }), data.maxAttempts > 1 && (_jsxs("div", { className: "mt-2 flex items-center gap-2 text-[10px] text-white/40", children: [_jsx(RotateCcw, { className: "h-3 w-3" }), _jsxs("span", { children: ["\u91CD\u8BD5 ", data.attempt, "/", data.maxAttempts, data.attempt >= data.maxAttempts && !data.passed && (_jsx("span", { className: "ml-1 text-red-400", children: "(\u5DF2\u8FBE\u6700\u5927\u91CD\u8BD5)" }))] })] })), data.summary && (_jsx("div", { className: "mt-2 text-[11px] text-white/50", children: data.summary })), _jsxs("div", { className: "mt-3 flex gap-3", children: [criticalCount > 0 && (_jsxs("div", { className: "flex items-center gap-1 text-[10px] text-red-400", children: [_jsx(AlertCircle, { className: "h-3 w-3" }), criticalCount, " \u5173\u952E\u95EE\u9898"] })), warningCount > 0 && (_jsxs("div", { className: "flex items-center gap-1 text-[10px] text-amber-400", children: [_jsx(AlertTriangle, { className: "h-3 w-3" }), warningCount, " \u8B66\u544A"] }))] }), data.issues.length > 0 && (_jsx("div", { className: "mt-3 space-y-2 max-h-32 overflow-y-auto", children: data.issues.map((issue, idx) => (_jsx("div", { className: cn("rounded-lg border p-2 text-[11px]", issue.type === 'critical'
                        ? "border-red-500/20 bg-red-500/5"
                        : issue.type === 'warning'
                            ? "border-amber-500/20 bg-amber-500/5"
                            : "border-white/5 bg-white/[0.02]"), children: _jsxs("div", { className: "flex items-start gap-2", children: [issue.type === 'critical' ? (_jsx(XCircle, { className: "mt-0.5 h-3.5 w-3.5 shrink-0 text-red-400" })) : issue.type === 'warning' ? (_jsx(AlertTriangle, { className: "mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" })) : (_jsx(AlertCircle, { className: "mt-0.5 h-3.5 w-3.5 shrink-0 text-white/40" })), _jsxs("div", { className: "flex-1", children: [_jsx("div", { className: "text-white/70", children: issue.message }), issue.suggestion && (_jsxs("div", { className: "mt-1 text-white/40", children: ["\uD83D\uDCA1 ", issue.suggestion] }))] })] }) }, idx))) })), data.metrics && Object.keys(data.metrics).length > 0 && (_jsx("div", { className: "mt-3 grid grid-cols-3 gap-2", children: Object.entries(data.metrics).map(([key, value]) => (_jsxs("div", { className: "rounded bg-white/5 p-2 text-center", children: [_jsx("div", { className: "text-[10px] text-white/40", children: key }), _jsx("div", { className: "text-xs font-mono text-white/70", children: value })] }, key))) }))] }));
}
