import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * ContextStoreStatsPanel — ContextOS 实时视图的「运行时上下文存储 · TTL/容量」面板。
 *
 * 数据源：
 *   - GET  /v2/context/admin/stats     → 容量 + 配置 + 最近一次 sweep 报告
 *   - POST /v2/context/admin/sweep     → 用户手动触发 sweep（sweep 是 destructive 的，故仅暴露按钮）
 *
 * 状态：
 *   - disabled（admin 端点未启用，默认）  → 渲染 stats-disabled hint，提示用户启用 KERNELONE_CONTEXT_ADMIN_ENABLED
 *   - error                              → 错误信息 + 保留 last successful data
 *   - ready                              → 完整面板：file_count / total_bytes / 利用条 / 配置 / 最近 sweep 报告 / sweep 按钮
 *   - loading (无历史)                    → 骨架占位（"读取中…"）
 *   - idle (组件未挂载 / 关闭)            → 不渲染（fail-closed 静默）
 *
 * 原则：
 *   - 完全只读 + 显式 destructive 按钮；按钮在 disabled / loading 状态下 disabled。
 *   - 「强制 sweep」是 destructive 操作（删除最早文件直到回到 cap 内），按钮 label 明确
 *     注明「清理 (destructive)」以避免误点。
 *   - 任何伪造精度都用占位符（—）而非伪 0；缺字段一律 null。
 *   - 复用 contextos 既有视觉语言（status dot / ring / text color），与决策表/角色卡保持一致。
 */
import { useMemo, useState } from 'react';
import { AlertCircle, Database, Loader2, RefreshCw, Settings2, Trash2, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { StatusBadge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import { classifyStatus, deriveNextSweepAt, deriveOldestAgeSeconds, formatBytes, formatElapsedShort, formatRelativeSeconds, STATS_STATUS_COLOR, STATS_STATUS_LABEL, } from './contextosStoreStats';
import { useContextStoreStats } from './useContextStoreStats';
export function ContextStoreStatsPanel({ workspace, enabled = true, refreshSignal = null }) {
    const { state, refresh, triggerSweep } = useContextStoreStats({ workspace, enabled, refreshSignal });
    const [sweepPending, setSweepPending] = useState(false);
    const [sweepError, setSweepError] = useState(null);
    const onTriggerSweep = async () => {
        if (sweepPending)
            return;
        setSweepPending(true);
        setSweepError(null);
        const result = await triggerSweep();
        setSweepPending(false);
        if (!result.ok)
            setSweepError(result.error);
    };
    if (!enabled)
        return null;
    return (_jsxs("section", { "data-testid": "contextos-store-stats-panel", className: "flex flex-col rounded-xl border border-white/[0.07] bg-bg-panel/40 backdrop-blur-sm", children: [_jsxs("header", { className: "flex items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-2.5", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx(Database, { className: "h-3.5 w-3.5 shrink-0 text-accent-secondary" }), _jsx("span", { className: "truncate text-xs font-semibold text-text-main", children: "\u4E0A\u4E0B\u6587\u5B58\u50A8 \u00B7 TTL/\u5BB9\u91CF" }), _jsx("span", { className: "truncate text-[10px] text-text-dim", children: "runtime/contexts \u00B7 ContextStoreRetention" })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-1.5", children: [state.kind === 'ready' && _jsx(StatusDot, { status: classifyStatusFromStats(state.data) }), state.kind === 'ready' && !state.isAdmin && (_jsx("span", { className: "rounded bg-white/5 px-1.5 py-0.5 text-[9px] text-text-dim", children: "\u53EA\u8BFB" })), _jsx(Button, { type: "button", variant: "outline", size: "sm", onClick: () => void refresh(), disabled: state.kind === 'loading', "data-testid": "contextos-store-stats-refresh", title: "\u7ACB\u5373\u62C9\u53D6\u6700\u65B0\u7EDF\u8BA1", "aria-label": "\u5237\u65B0\u4E0A\u4E0B\u6587\u5B58\u50A8\u7EDF\u8BA1", className: "border-accent-secondary/30 text-accent-secondary hover:bg-accent-secondary/10", children: _jsx(RefreshCw, { className: cn('h-3.5 w-3.5', state.kind === 'loading' && 'animate-spin') }) })] })] }), _jsx("div", { className: "min-h-0 flex-1 p-3", "data-testid": "contextos-store-stats-body", children: renderBody({
                    state,
                    sweepPending,
                    sweepError,
                    onRetry: () => {
                        void refresh();
                    },
                    onTriggerSweep: () => {
                        void onTriggerSweep();
                    },
                }) })] }));
}
function renderBody({ state, sweepPending, sweepError, onRetry, onTriggerSweep }) {
    if (state.kind === 'idle') {
        return (_jsx("div", { className: "rounded-lg border border-dashed border-white/10 px-3 py-5 text-center text-[11px] text-text-dim", children: "\u5F85\u547D" }));
    }
    if (state.kind === 'loading' && !state.previous) {
        return (_jsxs("div", { className: "flex items-center gap-2 px-3 py-5 text-[11px] text-text-dim", "data-testid": "contextos-store-stats-loading", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }), "\u8BFB\u53D6\u4E2D\u2026"] }));
    }
    if (state.kind === 'disabled') {
        return _jsx(DisabledHint, { reason: state.reason });
    }
    if (state.kind === 'error' && !state.previous) {
        return _jsx(ErrorMessage, { message: state.message, onRetry: onRetry });
    }
    // ready | error-with-previous | loading-with-previous → 渲染历史数据
    const data = state.kind === 'ready' ? state.data
        : state.kind === 'error' ? state.previous
            : state.previous;
    if (!data)
        return null;
    const isAdmin = state.kind === 'ready' ? state.isAdmin : false;
    return _jsx(ReadyView, { data: data, sweepPending: sweepPending, sweepError: sweepError, onTriggerSweep: onTriggerSweep, errorMessage: state.kind === 'error' ? state.message : null, isAdmin: isAdmin });
}
// --- disabled hint ---------------------------------------------------------
function DisabledHint({ reason }) {
    return (_jsx("div", { "data-testid": "contextos-store-stats-disabled", className: "rounded-lg border border-dashed border-white/10 bg-white/[0.02] px-3 py-3", children: _jsxs("div", { className: "flex items-start gap-2", children: [_jsx(Settings2, { className: "mt-0.5 h-3.5 w-3.5 shrink-0 text-text-dim" }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "text-[12px] font-semibold text-text-main", children: "\u7BA1\u7406\u5458\u7AEF\u70B9\u672A\u542F\u7528" }), _jsxs("p", { className: "mt-1 text-[10px] leading-relaxed text-text-muted", children: ["\u4E0A\u4E0B\u6587\u5B58\u50A8\u7EDF\u8BA1\u7531 ", _jsx("code", { className: "rounded bg-black/30 px-1 font-mono text-[10px] text-text-main", children: "/v2/context/admin/stats" }), " \u63D0\u4F9B\uFF1B\u8BE5\u7AEF\u70B9\u7531\u73AF\u5883\u53D8\u91CF", _jsx("code", { className: "mx-0.5 rounded bg-black/30 px-1 font-mono text-[10px] text-text-main", children: "KERNELONE_CONTEXT_ADMIN_ENABLED" }), "\u63A7\u5B88\uFF0C\u672A\u542F\u7528\u65F6\u8FD4\u56DE 404/ADMIN_DISABLED\u3002"] }), _jsxs("p", { className: "mt-1 text-[10px] leading-relaxed text-text-dim", children: ["\u542F\u7528\u65B9\u5F0F\uFF1A\u5728\u540E\u7AEF\u8FDB\u7A0B\u73AF\u5883\u53D8\u91CF\u4E2D\u8BBE\u7F6E ", _jsx("code", { className: "font-mono", children: "KERNELONE_CONTEXT_ADMIN_ENABLED=1" }), " \u540E\u91CD\u542F\u3002\u5B58\u50A8 TTL/\u5BB9\u91CF\u7B56\u7565\u672C\u8EAB\uFF08", _jsx("code", { className: "font-mono", children: "ContextStoreRetention" }), "\uFF0C\u9ED8\u8BA4 TTL=7d / 500MB / 20k \u6587\u4EF6\uFF09\u4ECD\u5728\u540E\u53F0 on-read gate \u6301\u7EED\u8FD0\u884C\uFF0C\u4EC5\u7EDF\u8BA1\u4E0D\u53EF\u89C1\u3002"] }), reason && (_jsxs("div", { className: "mt-2 rounded border border-white/10 bg-black/20 px-2 py-1 font-mono text-[10px] text-text-dim", children: ["\u540E\u7AEF\u54CD\u5E94\uFF1A", reason] }))] })] }) }));
}
// --- error -----------------------------------------------------------------
function ErrorMessage({ message, onRetry }) {
    return (_jsxs("div", { "data-testid": "contextos-store-stats-error", className: "flex items-start gap-2 rounded-lg border border-status-error/30 bg-status-error/10 px-3 py-3", children: [_jsx(AlertCircle, { className: "mt-0.5 h-3.5 w-3.5 shrink-0 text-status-error" }), _jsxs("div", { className: "min-w-0 flex-1", children: [_jsx("div", { className: "text-[12px] font-semibold text-status-error", children: "\u8BFB\u53D6\u7EDF\u8BA1\u5931\u8D25" }), _jsx("div", { className: "mt-1 truncate font-mono text-[10px] text-text-muted", title: message, children: message })] }), _jsx(Button, { variant: "ghost", size: "sm", onClick: onRetry, className: "text-text-muted hover:bg-white/5", children: "\u91CD\u8BD5" })] }));
}
function ReadyView({ data, sweepPending, sweepError, onTriggerSweep, errorMessage, isAdmin }) {
    const status = classifyStatusFromStats(data);
    const statusColor = STATS_STATUS_COLOR[status];
    const oldestAgeSec = deriveOldestAgeSeconds(data);
    const oldestAgeLabel = formatRelativeSeconds(typeof data.oldest_mtime === 'number' ? data.oldest_mtime : null);
    const lastSweepLabel = data.last_sweep_at > 0 ? formatRelativeSeconds(data.last_sweep_at) : '从未';
    const nextSweepAt = deriveNextSweepAt(data);
    const nextSweepLabel = nextSweepAt !== null ? formatRelativeSeconds(nextSweepAt) : null;
    const ttlLabel = data.config.ttl_seconds ? formatElapsedShort(data.config.ttl_seconds * 1000) : null;
    const sweepIntervalLabel = data.config.sweep_min_interval_seconds
        ? formatElapsedShort(data.config.sweep_min_interval_seconds * 1000)
        : null;
    const enabled = data.config.enabled !== false;
    const primaryStore = data.primary_store ?? null;
    const hasStoreBreakdown = Boolean(primaryStore);
    const filesRatio = useMemo(() => {
        if (!data.config.max_files || data.config.max_files <= 0)
            return null;
        return data.file_count / data.config.max_files;
    }, [data.file_count, data.config.max_files]);
    const bytesRatio = useMemo(() => {
        if (!data.config.max_total_bytes || data.config.max_total_bytes <= 0)
            return null;
        return data.total_bytes / data.config.max_total_bytes;
    }, [data.total_bytes, data.config.max_total_bytes]);
    return (_jsxs("div", { className: "space-y-3", "data-testid": "contextos-store-stats-ready", children: [errorMessage && (_jsxs("div", { "data-testid": "contextos-store-stats-freshness-warning", className: "flex items-start gap-2 rounded-md border border-status-warning/30 bg-status-warning/10 px-2 py-1.5 text-[10px] text-status-warning", children: [_jsx(AlertCircle, { className: "mt-0.5 h-3 w-3 shrink-0" }), _jsxs("span", { className: "font-mono", children: ["\u6700\u65B0\u62C9\u53D6\u5931\u8D25\uFF1A", errorMessage, "\uFF08\u5C55\u793A\u4E3A\u6700\u8FD1\u4E00\u6B21\u6210\u529F\u6570\u636E\uFF09"] })] })), _jsxs("div", { className: "grid grid-cols-1 gap-2 sm:grid-cols-3", children: [_jsx(Pill, { label: "\u72B6\u6001", tone: statusColor, value: STATS_STATUS_LABEL[status], sub: enabled ? 'on-read gate' : 'retention disabled' }), _jsx(Pill, { label: "\u6587\u4EF6\u6570", tone: "neutral", value: data.file_count.toLocaleString(), sub: data.config.max_files ? `上限 ${data.file_count >= data.config.max_files ? data.config.max_files.toLocaleString() : data.config.max_files.toLocaleString()}` : '无上限' }), _jsx(Pill, { label: "\u5360\u7528\u5B57\u8282", tone: "neutral", value: formatBytes(data.total_bytes), sub: data.config.max_total_bytes ? `上限 ${formatBytes(data.config.max_total_bytes)}` : '无上限' })] }), _jsxs("div", { className: "space-y-2 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5", children: [_jsx(UtilizationBar, { label: "\u6587\u4EF6\u6570\u5229\u7528\u6BD4", ratio: filesRatio, current: data.file_count, max: data.config.max_files, formatMax: (n) => n.toLocaleString() }), _jsx(UtilizationBar, { label: "\u5B57\u8282\u5229\u7528\u6BD4", ratio: bytesRatio, current: data.total_bytes, max: data.config.max_total_bytes, formatMax: formatBytes })] }), _jsxs("div", { className: "grid grid-cols-1 gap-2 sm:grid-cols-2", children: [_jsx(InfoCard, { title: "\u7B56\u7565\u914D\u7F6E", entries: [
                            { k: 'TTL', v: ttlLabel ?? '—' },
                            { k: '最大字节', v: data.config.max_total_bytes ? formatBytes(data.config.max_total_bytes) : '—' },
                            { k: '最大文件数', v: data.config.max_files ? data.config.max_files.toLocaleString() : '—' },
                            { k: 'sweep 间隔', v: sweepIntervalLabel ?? '—' },
                        ] }), _jsx(InfoCard, { title: "\u65F6\u95F4\u8F74", entries: [
                            { k: '最近 sweep', v: lastSweepLabel ?? '—' },
                            { k: '下次 sweep', v: nextSweepLabel ?? '—' },
                            { k: '最旧文件', v: oldestAgeLabel ?? '—' },
                            { k: '年龄（秒）', v: oldestAgeSec !== null ? oldestAgeSec.toLocaleString() : '—' },
                        ] })] }), hasStoreBreakdown && (_jsx(InfoCard, { title: "\u5B58\u50A8\u6839", entries: [
                    {
                        k: 'current',
                        v: primaryStore
                            ? `${primaryStore.file_count.toLocaleString()} · ${compactPath(primaryStore.contexts_root)}`
                            : '—',
                    },
                ] })), data.last_sweep_report && (_jsx(SweepReportCard, { report: data.last_sweep_report })), isAdmin && (_jsxs("div", { className: "flex items-center justify-between gap-2 rounded-lg border border-status-warning/20 bg-status-warning/5 px-3 py-2", children: [_jsxs("div", { className: "min-w-0 text-[10px] leading-relaxed text-text-muted", children: [_jsx("div", { className: "font-semibold text-text-main", children: "\u5F3A\u5236\u6E05\u7406\uFF08destructive\uFF09" }), _jsx("div", { className: "truncate", title: "\u6309 oldest-first \u987A\u5E8F\u5220\u9664\u6700\u65E9\u6587\u4EF6\u76F4\u5230\u56DE\u5230 TTL/\u5BB9\u91CF\u4E0A\u9650\uFF1B\u4E0D\u53EF\u6062\u590D\u3002", children: "\u6309 oldest-first \u987A\u5E8F\u5220\u9664\u6700\u65E9\u6587\u4EF6\u76F4\u5230\u56DE\u5230 TTL/\u5BB9\u91CF\u4E0A\u9650\u3002" })] }), _jsxs(Button, { type: "button", variant: "outline", size: "sm", onClick: onTriggerSweep, disabled: sweepPending || !enabled, "data-testid": "contextos-store-stats-sweep", "aria-label": "\u5F3A\u5236\u6E05\u7406\u4E0A\u4E0B\u6587\u5B58\u50A8", className: "border-status-warning/40 text-status-warning hover:bg-status-warning/15", children: [sweepPending ? (_jsx(Loader2, { className: "mr-1 h-3.5 w-3.5 animate-spin" })) : (_jsx(Trash2, { className: "mr-1 h-3.5 w-3.5" })), "\u6E05\u7406"] })] })), sweepError && (_jsxs("div", { className: "rounded-md border border-status-error/30 bg-status-error/10 px-2 py-1.5 font-mono text-[10px] text-status-error", children: ["sweep \u5931\u8D25\uFF1A", sweepError] }))] }));
}
function SweepReportCard({ report }) {
    if (!report)
        return null;
    const triggers = report.triggers ?? [];
    const removedBytes = report.removed_bytes ?? 0;
    const removedFiles = report.removed_files ?? 0;
    const elapsed = formatElapsedShort(report.elapsed_ms);
    return (_jsxs("div", { "data-testid": "contextos-store-stats-last-sweep", className: "rounded-lg border border-white/[0.06] bg-black/20 p-2.5", children: [_jsxs("div", { className: "mb-1.5 flex items-center justify-between text-[10px] uppercase tracking-wider text-text-dim", children: [_jsx("span", { children: "\u6700\u8FD1 sweep \u62A5\u544A" }), elapsed && _jsx("span", { className: "font-mono normal-case", children: elapsed })] }), _jsxs("div", { className: "grid grid-cols-3 gap-2", children: [_jsx(Mini, { label: "\u626B\u63CF", value: (report.scanned_files ?? 0).toLocaleString() }), _jsx(Mini, { label: "\u5220\u9664\u6587\u4EF6", value: removedFiles.toLocaleString() }), _jsx(Mini, { label: "\u91CA\u653E\u5B57\u8282", value: formatBytes(removedBytes) })] }), triggers.length > 0 && (_jsx("div", { className: "mt-2 flex flex-wrap gap-1", children: triggers.map((trigger) => (_jsx("span", { className: "rounded bg-white/5 px-1.5 py-0.5 font-mono text-[9px] text-text-muted", children: trigger }, trigger))) }))] }));
}
function Pill({ label, value, sub, tone, }) {
    const style = tone === 'neutral' ? null : tone;
    return (_jsxs("div", { className: cn('rounded-lg border px-2.5 py-2', style ? style.ring : 'border-white/[0.06] bg-white/[0.02]'), children: [_jsx("div", { className: "text-[9px] uppercase tracking-wider text-text-dim", children: label }), _jsx("div", { className: cn('mt-0.5 font-mono text-sm font-bold', style ? style.text : 'text-text-main'), children: value }), sub && _jsx("div", { className: "mt-0.5 truncate text-[9px] text-text-dim", title: sub, children: sub })] }));
}
function UtilizationBar({ label, ratio, current, max, formatMax, }) {
    const r = typeof ratio === 'number' && Number.isFinite(ratio) ? Math.max(0, Math.min(1, ratio)) : null;
    const widthPct = r === null ? 0 : Math.max(2, Math.round(r * 100));
    const tone = r === null
        ? 'bg-text-dim'
        : r >= 0.95
            ? 'bg-status-error'
            : r >= 0.7
                ? 'bg-status-warning'
                : 'bg-accent-secondary';
    return (_jsxs("div", { className: "space-y-1", children: [_jsxs("div", { className: "flex items-center justify-between text-[10px]", children: [_jsx("span", { className: "text-text-muted", children: label }), _jsxs("span", { className: "font-mono text-text-main", children: [r === null ? '—' : `${Math.round(r * 100)}%`, current !== null && max !== null && (_jsxs("span", { className: "ml-1 text-text-dim", children: [typeof current === 'number' && current > 1024 ? formatBytes(current) : current.toLocaleString(), ' / ', formatMax(max)] }))] })] }), _jsx("div", { className: "h-1.5 overflow-hidden rounded-full bg-white/5", children: _jsx("div", { className: cn('h-full rounded-full transition-all duration-500', tone), style: { width: `${widthPct}%` } }) })] }));
}
function InfoCard({ title, entries, }) {
    return (_jsxs("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5", children: [_jsx("div", { className: "mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-text-dim", children: title }), _jsx("dl", { className: "space-y-1", children: entries.map((entry) => (_jsxs("div", { className: "grid grid-cols-[1fr_auto] items-baseline gap-2 text-[10px]", children: [_jsx("dt", { className: "text-text-muted", children: entry.k }), _jsx("dd", { className: "font-mono text-text-main", children: entry.v })] }, entry.k))) })] }));
}
function Mini({ label, value }) {
    return (_jsxs("div", { className: "rounded-md bg-white/[0.02] px-2 py-1", children: [_jsx("div", { className: "text-[9px] uppercase tracking-wider text-text-dim", children: label }), _jsx("div", { className: "font-mono text-[11px] font-bold text-text-main", children: value })] }));
}
function StatusDot({ status }) {
    const color = STATS_STATUS_COLOR[status];
    return (_jsx(StatusBadge, { color: status === 'ok' ? 'success' : status === 'critical' ? 'error' : status === 'warning' ? 'warning' : 'default', variant: "dot", children: _jsx("span", { className: cn('font-mono text-[10px]', color.text), children: STATS_STATUS_LABEL[status] }) }));
}
function classifyStatusFromStats(data) {
    return classifyStatus({
        file_count: data.file_count,
        total_bytes: data.total_bytes,
        max_files: data.config.max_files,
        max_total_bytes: data.config.max_total_bytes,
        enabled: data.config.enabled,
    });
}
function compactPath(path) {
    const text = String(path || '').trim();
    if (text.length <= 54)
        return text || '—';
    return `${text.slice(0, 20)}…${text.slice(-30)}`;
}
