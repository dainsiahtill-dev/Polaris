import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { CheckCircle2, AlertTriangle, Activity, Brain, Clock } from 'lucide-react';
const formatTokens = (count) => {
    if (typeof count !== 'number' || Number.isNaN(count))
        return '—';
    return count.toLocaleString();
};
export function TestResultDisplay({ result }) {
    if (!result)
        return null;
    const ready = result.ready;
    const grade = result.grade || (ready ? 'PASS' : 'FAIL');
    return (_jsxs("div", { className: "soft-panel-subtle space-y-3 rounded-xl p-3", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-text-main", children: [ready ? (_jsx(CheckCircle2, { className: "size-4 text-status-success" })) : (_jsx(AlertTriangle, { className: "size-4 text-status-error" })), _jsx("span", { className: "font-semibold", children: "\u6D4B\u8BD5\u7ED3\u679C" })] }), _jsx("span", { className: `text-[10px] uppercase tracking-wider px-2 py-1 rounded border ${ready
                            ? 'bg-status-success/15 text-status-success border-status-success/40'
                            : 'bg-status-error/15 text-status-error border-status-error/40'}`, children: grade })] }), _jsxs("div", { className: "grid grid-cols-3 gap-3 text-xs", children: [_jsxs("div", { className: "soft-chip rounded-lg p-2", children: [_jsxs("div", { className: "flex items-center gap-1 text-[10px] text-text-dim", children: [_jsx(Clock, { className: "size-3" }), "\u5EF6\u8FDF"] }), _jsx("div", { className: "text-sm text-text-main mt-1", children: typeof result.latencyMs === 'number' ? `${Math.round(result.latencyMs)} ms` : '—' })] }), _jsxs("div", { className: "soft-chip rounded-lg p-2", children: [_jsxs("div", { className: "flex items-center gap-1 text-[10px] text-text-dim", children: [_jsx(Activity, { className: "size-3" }), "Tokens"] }), _jsxs("div", { className: "text-sm text-text-main mt-1", children: [formatTokens(result.usage?.totalTokens), result.usage?.estimated ? _jsx("span", { className: "text-[9px] text-text-dim ml-1", children: "(\u4F30\u7B97)" }) : null] })] }), _jsxs("div", { className: "soft-chip rounded-lg p-2", children: [_jsxs("div", { className: "flex items-center gap-1 text-[10px] text-text-dim", children: [_jsx(Brain, { className: "size-3" }), "\u601D\u8003\u80FD\u529B"] }), _jsxs("div", { className: "text-sm text-text-main mt-1", children: [result.thinking?.supportsThinking === undefined
                                        ? '—'
                                        : result.thinking.supportsThinking
                                            ? '支持'
                                            : '不支持', typeof result.thinking?.confidence === 'number' ? (_jsxs("span", { className: "text-[9px] text-text-dim ml-1", children: [Math.round(result.thinking.confidence * 100), "%"] })) : null] })] })] }), result.suites && result.suites.length > 0 ? (_jsxs("div", { className: "space-y-2", children: [_jsx("div", { className: "text-[10px] text-text-dim", children: "\u5957\u4EF6\u7ED3\u679C" }), _jsx("div", { className: "grid grid-cols-2 gap-2", children: result.suites.map((suite) => (_jsxs("div", { className: `rounded border px-2 py-1 text-[10px] flex items-center justify-between ${suite.ok
                                ? 'border-status-success/35 bg-status-success/10 text-status-success'
                                : 'border-status-error/35 bg-status-error/10 text-status-error'}`, children: [_jsx("span", { className: "capitalize", children: suite.name }), _jsx("span", { children: suite.ok ? 'PASS' : 'FAIL' })] }, suite.name))) })] })) : null, result.report ? (_jsxs("details", { className: "text-[10px] text-text-dim", children: [_jsx("summary", { className: "cursor-pointer", children: "\u67E5\u770B\u539F\u59CB\u62A5\u544A" }), _jsx("pre", { className: "mt-2 whitespace-pre-wrap break-words text-[10px] text-text-muted font-mono", children: JSON.stringify(result.report, null, 2) })] })) : null] }));
}
