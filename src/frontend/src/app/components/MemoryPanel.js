import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Database, Clock, AlertCircle, CheckCircle, XCircle, FileText, ListChecks, Target } from 'lucide-react';
import { useMemo, useState } from 'react';
export function MemoryPanel({ content, mtime, loading, error, collapsed, onToggle }) {
    const [showRaw, setShowRaw] = useState(false);
    const parsed = useMemo(() => {
        const text = (content || '').trim();
        if (!text)
            return null;
        try {
            return JSON.parse(text);
        }
        catch {
            return null;
        }
    }, [content]);
    const lastRunAt = typeof parsed?.last_run_at === 'string' ? parsed?.last_run_at : '';
    const lastRoundIndex = typeof parsed?.last_round_index === 'number' ? parsed?.last_round_index : null;
    const lastTargetIndex = typeof parsed?.last_target_index === 'number' ? parsed?.last_target_index : null;
    const lastTarget = typeof parsed?.last_target === 'string' ? parsed?.last_target : '';
    const lastSummary = typeof parsed?.last_summary === 'string' ? parsed?.last_summary : '';
    const lastNext = typeof parsed?.last_next_step === 'string' ? parsed?.last_next_step : '';
    const lastLogPath = typeof parsed?.last_log_path === 'string' ? parsed?.last_log_path : '';
    const lastRespPath = typeof parsed?.last_response_path === 'string' ? parsed?.last_response_path : '';
    const lastExit = typeof parsed?.last_exit_code === 'number' ? parsed?.last_exit_code : null;
    const lastError = typeof parsed?.last_error === 'string' ? parsed?.last_error : '';
    const gapAt = typeof parsed?.last_gap_review_at === 'string' ? parsed?.last_gap_review_at : '';
    const gapPath = typeof parsed?.last_gap_report_path === 'string' ? parsed?.last_gap_report_path : '';
    const statusOk = lastExit === 0 && !lastError;
    const knownKeys = useMemo(() => new Set([
        'last_run_at',
        'last_round_index',
        'last_target_index',
        'last_target',
        'last_summary',
        'last_next_step',
        'last_log_path',
        'last_response_path',
        'last_exit_code',
        'last_error',
        'last_gap_review_at',
        'last_gap_report_path',
    ]), []);
    const otherEntries = useMemo(() => {
        if (!parsed)
            return [];
        const entries = Object.entries(parsed).filter(([k]) => !knownKeys.has(k));
        entries.sort(([a], [b]) => a.localeCompare(b));
        return entries;
    }, [parsed, knownKeys]);
    const [expanded, setExpanded] = useState({});
    const toggleKey = (k) => setExpanded((prev) => ({ ...prev, [k]: !prev[k] }));
    const isComplex = (v) => typeof v === 'object' && v !== null;
    const brief = (v) => {
        if (v === null || v === undefined)
            return '(null)';
        if (typeof v === 'string') {
            const t = v.trim();
            return t.length > 160 ? t.slice(0, 157) + '...' : t;
        }
        if (typeof v === 'number' || typeof v === 'boolean')
            return String(v);
        try {
            const t = JSON.stringify(v);
            return t.length > 200 ? t.slice(0, 197) + '...' : t;
        }
        catch {
            return String(v);
        }
    };
    return (_jsxs("div", { className: "soft-panel-subtle h-full flex flex-col", children: [_jsxs("div", { className: "soft-panel-subtle px-4 py-3 border-b flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Database, { className: "size-4 text-accent" }), _jsx("h2", { className: "text-sm font-semibold text-text-main", children: "\u8BB0\u5FC6" })] }), _jsxs("div", { className: "flex items-center gap-3 text-xs text-text-dim", children: [_jsxs("div", { className: "flex items-center gap-1", children: [_jsx(Clock, { className: "size-3" }), _jsx("span", { children: mtime || '-' })] }), !collapsed ? (_jsx("button", { type: "button", onClick: () => setShowRaw((prev) => !prev), className: "rounded px-2 py-1 text-[11px] text-text-muted hover:bg-white/70", "aria-label": showRaw ? '隐藏原始 JSON' : '显示原始 JSON', children: showRaw ? '隐藏原始' : '显示原始' })) : null] })] }), collapsed ? null : (_jsxs("div", { className: "flex-1 overflow-auto", children: [error ? (_jsxs("div", { className: "p-4 text-sm text-status-error flex items-center gap-2", children: [_jsx(AlertCircle, { className: "size-4" }), _jsx("span", { children: error })] })) : null, loading ? (_jsx("div", { className: "p-4 text-sm text-text-muted", children: "\u52A0\u8F7D\u4E2D..." })) : (_jsxs("div", { className: "p-3 space-y-3", children: [parsed ? (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("div", { className: "soft-panel rounded p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-text-muted", children: [statusOk ? (_jsx(CheckCircle, { className: "size-4 text-status-success" })) : (_jsx(XCircle, { className: "size-4 text-status-error" })), _jsx("span", { className: `rounded px-2 py-0.5 text-[10px] ${statusOk ? 'bg-emerald-500/15 text-status-success' : 'bg-red-500/15 text-status-error'}`, children: statusOk ? '通过' : '失败' }), lastRunAt ? (_jsxs("span", { className: "ml-2 flex items-center gap-1 text-text-muted", children: [_jsx(Clock, { className: "size-3" }), lastRunAt] })) : null] }), _jsxs("div", { className: "mt-2 text-xs text-text-main", children: [typeof lastRoundIndex === 'number' || typeof lastTargetIndex === 'number' ? (_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Target, { className: "size-3.5 text-accent" }), _jsxs("span", { children: ["\u8F6E\u6B21: ", lastRoundIndex ?? '-', " / \u76EE\u6807\u5E8F\u53F7: ", lastTargetIndex ?? '-'] })] })) : null, lastTarget ? _jsxs("div", { className: "mt-1 text-text-muted", children: ["\u76EE\u6807\uFF1A", lastTarget] }) : null] })] }), _jsxs("div", { className: "soft-panel rounded p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx(ListChecks, { className: "size-4 text-gold" }), _jsx("span", { children: "\u6458\u8981\u4E0E\u4E0B\u4E00\u6B65" })] }), _jsxs("div", { className: "mt-2 space-y-1", children: [_jsxs("div", { className: "text-xs text-text-main", children: [_jsx("span", { className: "text-text-muted", children: "\u6458\u8981\uFF1A" }), lastSummary || '(无)'] }), _jsxs("div", { className: "text-xs text-text-main", children: [_jsx("span", { className: "text-text-muted", children: "\u4E0B\u4E00\u6B65\uFF1A" }), lastNext || '(无)'] })] })] })] }), _jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("div", { className: "soft-panel rounded p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx(FileText, { className: "size-4 text-accent" }), _jsx("span", { children: "\u5173\u8054\u6587\u4EF6" })] }), _jsxs("div", { className: "mt-2 space-y-1 text-[11px] text-text-muted", children: [lastLogPath ? _jsxs("div", { children: ["\u65E5\u5FD7\uFF1A", lastLogPath] }) : null, lastRespPath ? _jsxs("div", { children: ["\u54CD\u5E94\uFF1A", lastRespPath] }) : null] })] }), _jsxs("div", { className: "soft-panel rounded p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx(AlertCircle, { className: "size-4 text-gold" }), _jsx("span", { children: "\u7F3A\u53E3\u590D\u76D8" })] }), _jsxs("div", { className: "mt-2 space-y-1 text-[11px] text-text-muted", children: [gapAt ? _jsxs("div", { children: ["\u65F6\u95F4\uFF1A", gapAt] }) : _jsx("div", { className: "text-text-dim", children: "(\u672A\u8BB0\u5F55)" }), gapPath ? _jsxs("div", { children: ["\u62A5\u544A\uFF1A", gapPath] }) : null] })] })] }), lastError ? (_jsxs("div", { className: "rounded border border-red-500/20 bg-red-500/10 p-3 text-xs text-status-error", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(XCircle, { className: "size-4" }), _jsx("span", { children: "\u9519\u8BEF" })] }), _jsx("div", { className: "mt-2", children: lastError })] })) : null, otherEntries.length > 0 ? (_jsxs("div", { className: "soft-panel rounded p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx(ListChecks, { className: "size-4 text-accent" }), _jsx("span", { children: "\u5176\u4ED6\u5B57\u6BB5" })] }), _jsx("div", { className: "mt-2 space-y-2", children: otherEntries.map(([key, value]) => {
                                                    const complex = isComplex(value);
                                                    const open = !!expanded[key];
                                                    return (_jsxs("div", { className: "soft-inset rounded p-2", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("div", { className: "text-[11px] text-text-muted", children: key }), complex ? (_jsx("button", { type: "button", onClick: () => toggleKey(key), className: "rounded px-2 py-0.5 text-[10px] text-text-muted hover:bg-white/70", children: open ? '收起' : '展开' })) : null] }), _jsx("div", { className: "mt-1 text-xs text-text-main", children: brief(value) }), complex && open ? (_jsx("pre", { className: "soft-inset mt-2 rounded p-2 text-[11px] text-text-main font-mono leading-relaxed whitespace-pre-wrap", children: _jsx("code", { children: (() => {
                                                                        try {
                                                                            return JSON.stringify(value, null, 2);
                                                                        }
                                                                        catch {
                                                                            return String(value);
                                                                        }
                                                                    })() }) })) : null] }, key));
                                                }) })] })) : null] })) : (_jsx("div", { className: "soft-panel rounded p-3 text-xs text-text-muted", children: "(\u65E0\u53EF\u89E3\u6790\u7684\u5185\u5B58\u5FEB\u7167)" })), showRaw ? (_jsx("pre", { className: "soft-inset rounded p-3 text-[11px] text-text-main font-mono leading-relaxed whitespace-pre-wrap", children: _jsx("code", { children: content || '(空)' }) })) : null] }))] }))] }));
}
