import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState, useMemo } from 'react';
import { ScrollText, FileText, CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react';
import { normalizePlanText } from '@/app/utils/planRender';
import { cn } from '@/app/components/ui/utils';
import { StatusBadge } from '@/app/components/ui/badge';
export function PlanBoard({ planText, planMtime, planTextNormalized, className, defaultExpanded = true }) {
    const [isExpanded, setIsExpanded] = useState(defaultExpanded);
    // 处理 plan 文本，只进行规范化
    const processedText = useMemo(() => {
        const text = planText || '';
        if (planTextNormalized) {
            return text;
        }
        const { text: normalized } = normalizePlanText(text);
        return normalized;
    }, [planText, planTextNormalized]);
    // 空状态
    if (!planText || planText.trim().length === 0) {
        return (_jsxs("div", { className: cn('rounded-xl border border-border bg-bg-panel/50 p-6', className), children: [_jsxs("div", { className: "flex items-center gap-2 mb-4", children: [_jsx(ScrollText, { className: "w-5 h-5 text-accent" }), _jsx("h3", { className: "font-heading font-bold text-text-main", children: "\u6555\u4EE4\u603B\u56FE" }), planMtime && (_jsx("span", { className: "text-xs text-text-muted ml-auto", children: planMtime }))] }), _jsxs("div", { className: "text-center py-8 text-text-muted", children: [_jsx(ScrollText, { className: "w-12 h-12 mx-auto mb-3 opacity-30" }), _jsx("p", { children: "\u6682\u65E0\u6555\u4EE4\u603B\u56FE" }), _jsx("p", { className: "text-xs mt-1", children: "\u8BF7\u5728 plan.md \u4E2D\u7F16\u5199\u4EFB\u52A1\u8BA1\u5212" })] })] }));
    }
    return (_jsxs("div", { className: cn('rounded-xl border border-border bg-bg-panel/50 overflow-hidden flex flex-col', className), children: [_jsxs("button", { onClick: () => setIsExpanded(!isExpanded), className: "w-full flex items-center justify-between px-4 py-3 border-b border-border bg-bg-tertiary/30 hover:bg-bg-tertiary/50 transition-colors text-left", children: [_jsxs("div", { className: "flex items-center gap-2", children: [isExpanded ? (_jsx(ChevronDown, { className: "w-4 h-4 text-text-muted" })) : (_jsx(ChevronRight, { className: "w-4 h-4 text-text-muted" })), _jsx(ScrollText, { className: "w-5 h-5 text-accent" }), _jsx("h3", { className: "font-heading font-bold text-text-main", children: "\u6555\u4EE4\u603B\u56FE" }), planTextNormalized && (_jsxs(StatusBadge, { color: "success", variant: "dot", className: "text-xs", children: [_jsx(CheckCircle2, { className: "w-3 h-3" }), "\u5DF2\u89C4\u8303\u5316"] }))] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("span", { className: "text-xs text-text-muted", children: [processedText.length, " \u5B57\u7B26"] }), planMtime && (_jsxs("span", { className: "text-xs text-text-muted hidden sm:inline", children: ["\u00B7 ", planMtime] }))] })] }), isExpanded && (_jsxs(_Fragment, { children: [_jsx("div", { className: "flex-1 p-4 overflow-auto max-h-80", children: _jsx("pre", { className: "text-sm text-text-main whitespace-pre-wrap break-words font-sans leading-relaxed", children: processedText }) }), _jsxs("div", { className: "px-4 py-2 border-t border-border bg-bg-tertiary/20 text-xs text-text-muted flex items-center justify-between shrink-0", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(FileText, { className: "w-3 h-3" }), _jsx("span", { children: "\u70B9\u51FB\u5934\u90E8\u53EF\u6298\u53E0" }), planTextNormalized && (_jsx("span", { className: "text-status-success", children: "\u00B7 \u6570\u636E\u5DF2\u89C4\u8303\u5316" }))] }), _jsx("span", { className: "text-text-dim", children: "\u53EA\u8BFB" })] })] }))] }));
}
export default PlanBoard;
