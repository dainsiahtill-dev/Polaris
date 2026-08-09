import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/**
 * ContextOS 实时视图 (ContextOS Real-time Dashboard)
 *
 * 可视化 Polaris 的「上下文操作系统」实时数据流：从用户请求进入，经 TruthLog 真值流 →
 * WorkingMemory 活动窗口 → ProjectionEngine 自适应排序投影（内部含预算规划）→ RoleSignalPlane
 * 角色信号 → project() 消息装配 → CompressionEngine 装配后预算压缩兜底 → LLM 调用，再回流到
 * Receipt / Telemetry 回执遥测的反馈闭环（顺序忠实于后端 gateway.py 真实装配流）。
 *
 * 数据源 = Polaris 既有 WS 实时框架：emit_event/emit_llm_event → MessageBus →
 * WebSocket /v2/ws/runtime → useRuntime → llmStreamEvents/executionLogs/processStreamEvents
 * 这些 props 经 buildTelemetryFromStream 派生为遥测（见 contextOSTelemetry.ts / contextOSData.ts）。
 * 组件随 WS 事件到达即重渲染。真实 per-call token / 时延来自 journal `llm` 通道（raw.data），
 * 实时送达；仅当实时流无 token 时才退回用量统计通道并标注「非实时」，绝不伪造精度。
 */
import { Fragment, useMemo, useState } from 'react';
import { Network, ChevronLeft, ChevronRight, RefreshCw, Cpu, Database, Layers, GitBranch, Boxes, Gauge, FileStack, ShieldCheck, Radio, ArrowRight, Coins, Activity, X, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { StatusBadge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';
import { buildContextOSModel, contextOSFormat, decisionMatchesRole, safeText, summarizeRoleContextState, classifyEventSemantics, groupEventsByEntity, summarizeEntityThread, } from './contextOSData';
import { buildTelemetryFromStream } from './contextOSTelemetry';
import { ContextViewerModal } from './ContextViewerModal';
import { ContextStoreStatsPanel } from './ContextStoreStatsPanel';
const STAGE_ICONS = {
    request: Radio,
    truthlog: Database,
    working_mem: Layers,
    projection: GitBranch,
    role_signal: Boxes,
    budget: Gauge,
    prompt: FileStack,
    llm: Cpu,
    receipt: ShieldCheck,
};
const STATE_STYLES = {
    active: {
        dot: 'bg-accent-secondary',
        ring: 'border-accent-secondary/50 bg-accent-secondary/10 shadow-[0_0_16px_rgba(74,158,158,0.25)]',
        text: 'text-accent-secondary',
        label: '运行',
    },
    blocked: {
        dot: 'bg-status-error',
        ring: 'border-status-error/50 bg-status-error/10',
        text: 'text-status-error',
        label: '受阻',
    },
    idle: {
        dot: 'bg-text-dim',
        ring: 'border-white/10 bg-white/[0.02]',
        text: 'text-text-muted',
        label: '空闲',
    },
};
const AGI_FINAL_REQUEST_COVERAGE = [
    {
        id: 'resident-agi-decision-trace',
        label: 'AGI 决策交接',
        key: 'has_resident_agi_decision_trace',
    },
    {
        id: 'resident-agi-capability-surface',
        label: 'AGI 能力面',
        key: 'has_resident_agi_capability_surface',
    },
    {
        id: 'resident-agi-decision-boundary',
        label: 'AGI 决策边界',
        key: 'has_resident_agi_decision_boundary',
    },
];
function badgeColorForState(state) {
    if (state === 'active')
        return 'success';
    if (state === 'blocked')
        return 'error';
    return 'default';
}
/** 把最近更新时间戳格式化成相对新鲜度（刚刚 / Ns 前 / Nm 前）。 */
function formatFreshness(epochMs) {
    const deltaMs = Date.now() - epochMs;
    if (!Number.isFinite(deltaMs) || deltaMs < 0)
        return '刚刚';
    const seconds = Math.floor(deltaMs / 1000);
    if (seconds < 5)
        return '刚刚';
    if (seconds < 60)
        return `${seconds}s 前`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60)
        return `${minutes}m 前`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h 前`;
}
function controlPlaneProjectionLabel(projection) {
    if (!projection.available) {
        return projection.status === 'pending' ? '账本待生成' : '账本缺失';
    }
    return projection.ok ? '账本一致' : '账本异常';
}
function controlPlaneProjectionSummary(projection) {
    if (!projection.available) {
        return projection.detail || 'Run Ledger projection 尚不可用';
    }
    return `${projection.projected}/${projection.total} 投影 · ${projection.failed} 异常`;
}
function controlPlaneSourceSummary(projection) {
    const source = `source=${projection.source}`;
    if (!projection.compat_ledgers_included)
        return source;
    return `${source} · compat=factory-ledger`;
}
function controlPlaneGatePassed(projection) {
    if (!projection)
        return undefined;
    if (!projection.available)
        return false;
    if (projection.total <= 0 && projection.projects.length === 0)
        return false;
    if (!projection.ok || projection.failed > 0)
        return false;
    return projection.projects.every((project) => project.ok && project.failed_gate_count === 0);
}
function evidencePolicyLabel(projection) {
    const policy = projection.evidence_policy;
    const enabled = policy?.enabled_modalities ?? [];
    if (!policy || (enabled.length === 0 && policy.required_modalities.length === 0)) {
        return '可选验证未启用';
    }
    return policy.ok ? '可选验证已启用' : '可选验证缺证据';
}
function evidencePolicySummary(projection) {
    const policy = projection.evidence_policy;
    const enabled = policy?.enabled_modalities ?? [];
    if (!policy || (enabled.length === 0 && policy.required_modalities.length === 0)) {
        return 'browser / visual / domain verifier 未作为硬门禁';
    }
    if (policy.required_modalities.length === 0) {
        return `可选启用 ${enabled.join(', ')} · 未作为硬门禁`;
    }
    const required = policy.required_modalities.join(', ');
    const failed = policy.failed_required_modalities ?? [];
    if (failed.length > 0) {
        return `启用 ${required} · 失败 ${failed.join(', ')}`;
    }
    if (policy.missing_required_modalities.length > 0) {
        return `启用 ${required} · 缺 ${policy.missing_required_modalities.join(', ')}`;
    }
    return `启用 ${required}`;
}
function readAuditCoverage(audit) {
    if (!audit)
        return {};
    const coverage = audit['coverage'];
    return typeof coverage === 'object' && coverage !== null ? coverage : {};
}
function readAuditMissingCoverage(audit) {
    if (!audit)
        return new Set();
    const contextQuality = audit['context_quality'];
    if (typeof contextQuality !== 'object' || contextQuality === null)
        return new Set();
    const missing = contextQuality['missing_coverage'];
    if (!Array.isArray(missing))
        return new Set();
    return new Set(missing.filter((item) => typeof item === 'string'));
}
function auditFlagValue(value) {
    if (typeof value === 'boolean')
        return value;
    if (typeof value === 'string') {
        const normalized = value.toLowerCase();
        if (normalized === 'true')
            return true;
        if (normalized === 'false')
            return false;
    }
    return null;
}
function finalRequestAgiCoverageChips(audit) {
    if (!audit)
        return [];
    const coverage = readAuditCoverage(audit);
    const missingCoverage = readAuditMissingCoverage(audit);
    return AGI_FINAL_REQUEST_COVERAGE.flatMap((item) => {
        const hasSignal = Object.prototype.hasOwnProperty.call(coverage, item.key) || missingCoverage.has(item.key);
        if (!hasSignal)
            return [];
        const rawValue = coverage[item.key];
        const present = auditFlagValue(rawValue);
        const resolved = present ?? (missingCoverage.has(item.key) ? false : null);
        return [{
                ...item,
                present: resolved,
                title: `${item.key}=${String(rawValue ?? 'n/a')} missing=${String(missingCoverage.has(item.key))}`,
            }];
    });
}
function FinalRequestAgiCoverageBadges({ audit, className, compact = false, }) {
    const chips = finalRequestAgiCoverageChips(audit);
    if (chips.length === 0)
        return null;
    return (_jsxs("div", { className: cn('flex flex-wrap items-center gap-1', className), "data-testid": "contextos-final-request-agi-coverage", children: [!compact && (_jsx("span", { className: "rounded bg-white/5 px-1.5 py-0.5 font-mono text-[9px] text-text-dim", children: "\u6700\u7EC8\u8BF7\u6C42 AGI \u8986\u76D6" })), chips.map((chip) => (_jsxs("span", { "data-testid": `contextos-final-request-agi-${chip.id}`, className: cn('rounded border px-1.5 py-0.5 font-mono text-[9px]', chip.present === true
                    ? 'border-accent-secondary/20 bg-accent-secondary/10 text-accent-secondary'
                    : chip.present === false
                        ? 'border-status-error/20 bg-status-error/10 text-status-error'
                        : 'border-white/[0.08] bg-white/5 text-text-dim'), title: chip.title, children: [chip.label, ": ", chip.present === true ? '已进入' : chip.present === false ? '缺失' : '未知'] }, chip.id)))] }));
}
// ---------------------------------------------------------------------------
// 子组件
// ---------------------------------------------------------------------------
function PipelineNode({ stage, selected = false, onSelect, }) {
    const Icon = STAGE_ICONS[stage.id] ?? Activity;
    const style = STATE_STYLES[stage.state];
    return (_jsxs("button", { type: "button", "data-testid": `contextos-stage-${stage.id}`, "data-state": stage.state, "data-selected": selected, "aria-pressed": selected, onClick: onSelect, className: cn('relative flex w-[104px] shrink-0 flex-col items-center gap-1 rounded-xl border px-2 py-2.5 text-center transition-all duration-500 hover:-translate-y-0.5 hover:border-accent-secondary/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-secondary/70 active:translate-y-0', style.ring, selected && 'border-accent-secondary/70 ring-2 ring-accent-secondary/45'), title: `${stage.component} — ${stage.hint}`, children: [_jsxs("div", { className: cn('flex h-8 w-8 items-center justify-center rounded-lg bg-black/30', style.text), children: [_jsx(Icon, { className: "h-4 w-4" }), stage.state === 'active' && (_jsxs("span", { className: "absolute right-1.5 top-1.5 flex h-1.5 w-1.5", children: [_jsx("span", { className: "absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-secondary opacity-75 motion-reduce:animate-none" }), _jsx("span", { className: "relative inline-flex h-1.5 w-1.5 rounded-full bg-accent-secondary" })] }))] }), _jsx("div", { className: "text-[11px] font-semibold leading-tight text-text-main", children: stage.label }), _jsx("div", { className: cn('mt-0.5 rounded-full bg-black/30 px-1.5 py-0.5 font-mono text-[9px]', style.text), children: stage.metric })] }));
}
function FlowArrow({ active }) {
    return (_jsx("div", { className: "flex shrink-0 items-center", "aria-hidden": true, children: _jsx(ArrowRight, { className: cn('h-4 w-4 transition-colors duration-500', active ? 'text-accent-secondary' : 'text-text-dim/40') }) }));
}
function DetailStat({ label, value, sub, tone = 'idle', }) {
    return (_jsxs("div", { className: "min-w-0 rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2.5", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-[10px] font-semibold uppercase tracking-wider text-text-dim", title: label, children: label }), _jsx("span", { className: cn('h-1.5 w-1.5 shrink-0 rounded-full', STATE_STYLES[tone].dot) })] }), _jsx("div", { className: cn('mt-1 truncate font-mono text-lg font-bold', STATE_STYLES[tone].text), title: String(value), children: value }), sub && _jsx("div", { className: "mt-1 truncate text-[10px] text-text-dim", title: sub, children: sub })] }));
}
function DetailBlock({ title, subtitle, children, }) {
    return (_jsxs("div", { className: "rounded-xl border border-white/[0.08] bg-white/[0.025] p-3", children: [_jsx("div", { className: "mb-2 flex items-center justify-between gap-2", children: _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-xs font-semibold text-text-main", title: title, children: title }), subtitle && _jsx("div", { className: "truncate text-[10px] text-text-dim", title: subtitle, children: subtitle })] }) }), children] }));
}
function DetailEmpty({ label }) {
    return (_jsxs("div", { className: "flex flex-col items-center gap-2 rounded-lg border border-dashed border-white/10 px-3 py-5 text-center", children: [_jsx(Activity, { className: "h-4 w-4 text-text-dim/30" }), _jsx("div", { className: "text-[11px] text-text-dim", children: label })] }));
}
function DetailEventList({ events, emptyLabel }) {
    if (events.length === 0)
        return _jsx(DetailEmpty, { label: emptyLabel });
    return (_jsx("div", { className: "space-y-1", children: events.map((event) => {
            const tone = event.category === 'error' ? 'blocked' : event.isProjection || event.hasUsage ? 'active' : 'idle';
            const summary = safeText(event.summary) || safeText(event.kind) || '事件';
            return (_jsxs("div", { className: "grid grid-cols-[58px_72px_1fr] gap-2 rounded-md px-2 py-1.5 text-[10px] hover:bg-white/[0.04]", children: [_jsx("span", { className: "font-mono text-text-dim", children: contextOSFormat.clock(event.ts) }), _jsx("span", { className: cn('truncate font-medium', STATE_STYLES[tone].text), title: event.actor, children: safeText(event.actor) }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-text-main", title: summary, children: summary }), _jsxs("div", { className: "mt-0.5 flex flex-wrap items-center gap-1", children: [_jsx("span", { className: "rounded bg-white/5 px-1 font-mono text-[9px] text-text-dim", children: safeText(event.kind) || event.category }), event.totalTokens > 0 && (_jsxs("span", { className: "rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary", children: [contextOSFormat.tokens(event.totalTokens), " tok"] })), event.contextTokens !== null && event.contextTokens > 0 && (_jsxs("span", { className: "rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary", children: ["ctx ", contextOSFormat.tokens(event.contextTokens)] })), event.durationMs !== null && event.durationMs > 0 && (_jsxs("span", { className: "rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted", children: [event.durationMs, "ms"] })), event.contextSnapshotRef && (_jsx("span", { className: "rounded bg-gold/10 px-1 font-mono text-[9px] text-gold", children: "snapshot" })), event.contextSnapshotDegraded && (_jsx("span", { className: "rounded bg-status-warning/10 px-1 font-mono text-[9px] text-status-warning", children: "degraded" }))] })] })] }, event.id));
        }) }));
}
function LlmCallCard({ event }) {
    const summary = safeText(event.summary) || safeText(event.kind) || 'LLM 调用';
    const modelLabel = event.model || '未记录模型';
    const providerLabel = event.providerName || event.providerId || 'provider unknown';
    const hasContext = event.contextTokens !== null && event.contextTokens > 0;
    const hasSnapshot = Boolean(event.contextSnapshotRef);
    return (_jsxs("div", { className: "rounded-xl border border-accent-secondary/15 bg-gradient-to-br from-accent-secondary/[0.08] via-white/[0.025] to-black/20 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx("span", { className: "rounded-md border border-accent-secondary/20 bg-accent-secondary/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-accent-secondary", children: safeText(event.actor) }), _jsx("span", { className: "truncate text-xs font-semibold text-text-main", title: modelLabel, children: modelLabel })] }), _jsx("div", { className: "mt-1 truncate font-mono text-[10px] text-text-dim", title: providerLabel, children: providerLabel })] }), _jsxs("div", { className: "shrink-0 text-right", children: [_jsx("div", { className: "font-mono text-[10px] text-text-dim", children: contextOSFormat.clock(event.ts) }), _jsx("div", { className: cn('mt-1 rounded px-1.5 py-0.5 font-mono text-[9px]', event.category === 'error' ? 'bg-status-error/10 text-status-error' : 'bg-accent-secondary/10 text-accent-secondary'), children: event.category === 'error' ? 'failed' : 'completed' })] })] }), _jsx("div", { className: "mt-3 rounded-lg border border-white/[0.06] bg-black/25 px-3 py-2 text-[11px] text-text-muted", title: summary, children: _jsx("span", { className: "line-clamp-2", children: summary }) }), _jsxs("div", { className: "mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4", children: [_jsx(DetailStat, { label: "Prompt", value: contextOSFormat.tokens(event.promptTokens), tone: event.promptTokens > 0 ? 'active' : 'idle', sub: "\u8F93\u5165 token" }), _jsx(DetailStat, { label: "Output", value: contextOSFormat.tokens(event.completionTokens), tone: event.completionTokens > 0 ? 'active' : 'idle', sub: "\u8F93\u51FA token" }), _jsx(DetailStat, { label: "Context", value: hasContext ? contextOSFormat.tokens(event.contextTokens) : 'n/a', tone: hasContext ? 'active' : 'idle', sub: "\u6700\u7EC8\u8BF7\u6C42\u4E0A\u4E0B\u6587" }), _jsx(DetailStat, { label: "Latency", value: event.durationMs !== null ? `${event.durationMs}ms` : 'n/a', tone: event.durationMs !== null ? 'active' : 'idle', sub: hasSnapshot ? 'snapshot linked' : 'no snapshot' })] }), _jsx(FinalRequestAgiCoverageBadges, { audit: event.finalRequestContextAudit, className: "mt-3" })] }));
}
function LlmCallDeck({ events }) {
    if (events.length === 0)
        return _jsx(DetailEmpty, { label: "\u6682\u65E0 LLM \u8C03\u7528\u4E8B\u4EF6" });
    return (_jsx("div", { className: "space-y-2.5", children: events.map((event) => _jsx(LlmCallCard, { event: event }, event.id)) }));
}
function ProjectionEvidenceDeck({ events }) {
    if (events.length === 0)
        return _jsx(DetailEmpty, { label: "\u6682\u65E0\u6295\u5F71\u4E8B\u4EF6" });
    return (_jsx("div", { className: "grid gap-2", children: events.map((event) => {
            const source = event.finalRequestTokenEstimate !== null
                ? 'final request audit'
                : event.contextItems !== null
                    ? 'context.build'
                    : event.projectionKey
                        ? 'projection key'
                        : 'text signal';
            return (_jsxs("div", { className: "rounded-xl border border-accent-secondary/15 bg-black/20 p-3", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-xs font-semibold text-text-main", title: safeText(event.summary), children: safeText(event.summary) || 'Context projection' }), _jsx("div", { className: "mt-1 font-mono text-[10px] text-accent-secondary", children: source })] }), _jsx("span", { className: "shrink-0 rounded bg-white/5 px-1.5 py-0.5 font-mono text-[9px] text-text-dim", children: contextOSFormat.clock(event.ts) })] }), _jsxs("div", { className: "mt-3 grid grid-cols-3 gap-2", children: [_jsx(DetailStat, { label: "Items", value: event.contextItems ?? 'n/a', tone: event.contextItems !== null ? 'active' : 'idle', sub: "WorkingMem" }), _jsx(DetailStat, { label: "Tokens", value: event.contextTokens !== null ? contextOSFormat.tokens(event.contextTokens) : 'n/a', tone: event.contextTokens !== null ? 'active' : 'idle', sub: "context size" }), _jsx(DetailStat, { label: "Final", value: event.finalRequestTokenEstimate !== null ? contextOSFormat.tokens(event.finalRequestTokenEstimate) : 'n/a', tone: event.finalRequestTokenEstimate !== null ? 'active' : 'idle', sub: "provider request" })] })] }, event.id));
        }) }));
}
function ReceiptEvidenceDeck({ events }) {
    if (events.length === 0)
        return _jsx(DetailEmpty, { label: "\u6682\u65E0\u56DE\u6267\u6216\u5FEB\u7167\u4E8B\u4EF6" });
    return (_jsx("div", { className: "space-y-2", children: events.map((event) => {
            const degraded = event.contextSnapshotDegraded;
            const statusTone = degraded || event.category === 'error' ? 'blocked' : event.contextSnapshotRef || event.contextHash ? 'active' : 'idle';
            const statusLabel = degraded ? '快照降级' : event.contextSnapshotRef ? '快照已落盘' : event.contextHash ? '上下文哈希' : '回执观测';
            return (_jsxs("div", { className: cn('rounded-xl border p-3', statusTone === 'blocked' ? 'border-status-error/25 bg-status-error/10' : 'border-gold/20 bg-gold/[0.04]'), children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: cn('text-xs font-semibold', STATE_STYLES[statusTone].text), children: statusLabel }), _jsx("div", { className: "mt-1 truncate text-[11px] text-text-muted", title: safeText(event.summary), children: safeText(event.summary) || safeText(event.kind) })] }), _jsx("span", { className: "shrink-0 rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-dim", children: contextOSFormat.clock(event.ts) })] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-3", children: [_jsx(DetailStat, { label: "Ref", value: event.contextSnapshotRef ? `${event.contextSnapshotRef.slice(0, 8)}...` : event.contextHash ? `${event.contextHash.slice(0, 8)}...` : 'n/a', tone: statusTone, sub: "snapshot/hash" }), _jsx(DetailStat, { label: "Call", value: event.callId ? `${event.callId.slice(0, 12)}...` : 'n/a', tone: event.callId ? 'active' : 'idle', sub: "correlation id" }), _jsx(DetailStat, { label: "Reason", value: degraded?.reason || 'ok', tone: degraded ? 'blocked' : 'active', sub: degraded?.message || 'receipt path' })] })] }, event.id));
        }) }));
}
function DetailDecisionList({ rows }) {
    if (rows.length === 0)
        return _jsx(DetailEmpty, { label: "\u6682\u65E0\u8BF7\u6C42\u6216\u51B3\u7B56\u8BB0\u5F55" });
    return (_jsx("div", { className: "space-y-1", children: rows.slice(0, 6).map((row, index) => (_jsxs("div", { className: "grid grid-cols-[58px_78px_1fr] gap-2 rounded-md px-2 py-1.5 text-[10px] hover:bg-white/[0.04]", children: [_jsx("span", { className: "font-mono text-text-dim", children: row.time }), _jsx("span", { className: "truncate font-medium text-text-muted", title: `${row.actor} ${row.kind}`, children: safeText(row.actor) }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-text-main", title: row.summary, children: safeText(row.summary) || safeText(row.kind) }), (row.tokens || row.latencyMs || row.receipt) && (_jsxs("div", { className: "mt-0.5 flex flex-wrap items-center gap-1", children: [row.tokens && _jsxs("span", { className: "rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary", children: [contextOSFormat.tokens(row.tokens), " tok"] }), row.latencyMs && _jsxs("span", { className: "rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted", children: [row.latencyMs, "ms"] }), row.receipt && _jsx("span", { className: "rounded bg-gold/10 px-1 font-mono text-[9px] text-gold", children: "\u56DE\u6267" })] }))] })] }, `${row.id}-${index}`))) }));
}
function DetailRoleList({ roles }) {
    return (_jsx("div", { className: "grid grid-cols-1 gap-1.5 sm:grid-cols-2", children: roles.map((role) => {
            const ctx = role.internalContext;
            const windowLabel = ctx.contextWindowTokens !== null ? contextOSFormat.windowTokens(ctx.contextWindowTokens) : '未知';
            return (_jsxs("div", { className: "rounded-lg border border-white/[0.06] bg-black/20 px-2.5 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-[11px] font-semibold text-text-main", title: role.title, children: role.title }), _jsxs("div", { className: "truncate font-mono text-[9px] text-text-dim", children: ["T", ctx.eventCount, " \u00B7 P", ctx.projectionCount, " \u00B7 R", ctx.receiptCount] })] }), _jsx("span", { className: cn('rounded px-1.5 py-0.5 text-[9px]', STATE_STYLES[role.state].ring, STATE_STYLES[role.state].text), children: STATE_STYLES[role.state].label })] }), _jsxs("div", { className: "mt-1.5 flex items-center justify-between gap-2 font-mono text-[9px] text-text-dim", children: [_jsx("span", { className: "truncate", children: ctx.windowOccupancyTokens !== null ? `~${contextOSFormat.tokens(ctx.windowOccupancyTokens)}` : '无 usage' }), _jsxs("span", { className: "shrink-0", children: ["/ ", windowLabel] })] })] }, role.id));
        }) }));
}
function DetailBindingBudgetList({ rows }) {
    if (rows.length === 0)
        return _jsx(DetailEmpty, { label: "\u6682\u65E0 provider/model \u7ED1\u5B9A\u9884\u7B97" });
    return (_jsxs("div", { className: "space-y-1.5", children: [rows.slice(0, 6).map((row) => (_jsx(BindingBudgetRow, { row: row }, row.id))), rows.length > 6 && (_jsxs("div", { className: "text-right font-mono text-[9px] text-text-dim", children: ["\u4EC5\u663E\u793A\u524D 6 \u8DEF \u00B7 \u5171 ", rows.length, " \u8DEF"] }))] }));
}
function PipelineDetailModal({ stage, model, telemetry, onClose, }) {
    const Icon = STAGE_ICONS[stage.id] ?? Activity;
    const style = STATE_STYLES[stage.state];
    const recentEvents = telemetry.events.slice(0, 6);
    const projectionEvents = telemetry.events.filter((event) => event.isProjection).slice(0, 6);
    const callEvents = telemetry.events.filter((event) => event.isCall || event.hasUsage).slice(0, 6);
    const receiptEvents = telemetry.events.filter((event) => event.contextSnapshotRef || event.contextSnapshotDegraded || event.contextHash).slice(0, 6);
    const errorEvents = telemetry.events.filter((event) => event.category === 'error' || event.contextSnapshotDegraded).slice(0, 6);
    const activeRoles = model.roles.filter((role) => role.state === 'active').length;
    const blockedRoles = model.roles.filter((role) => role.state === 'blocked').length;
    const roleWindowTotal = model.roles.reduce((sum, role) => sum + (role.internalContext.workingMemoryItems ?? 0), 0);
    const windowDenominator = model.contextWindowTokens !== null ? contextOSFormat.windowTokens(model.contextWindowTokens) : '未知';
    const contextWindowOccupancy = model.contextWindowTokens !== null && model.contextWindowTokens > 0
        ? Math.max(0, Math.min(1, model.windowOccupancyTokens / model.contextWindowTokens))
        : 0;
    const sharedStats = (_jsxs("div", { className: "grid grid-cols-2 gap-2 lg:grid-cols-4", children: [_jsx(DetailStat, { label: "\u72B6\u6001", value: STATE_STYLES[stage.state].label, tone: stage.state, sub: stage.component }), _jsx(DetailStat, { label: "\u8282\u70B9\u6307\u6807", value: stage.metric, tone: stage.state, sub: stage.hint }), _jsx(DetailStat, { label: "\u9065\u6D4B\u4E8B\u4EF6", value: telemetry.events.length, tone: telemetry.events.length > 0 ? 'active' : 'idle', sub: model.telemetryWindowed ? '最近窗口' : '实时流' }), _jsx(DetailStat, { label: "\u9519\u8BEF", value: model.errorCount, tone: model.errorCount > 0 ? 'blocked' : 'idle', sub: "ContextOS / LLM / \u56DE\u6267" })] }));
    let body;
    switch (stage.id) {
        case 'request':
            body = (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid grid-cols-2 gap-2 lg:grid-cols-4", children: [_jsx(DetailStat, { label: "\u8BF7\u6C42\u8F6E\u6B21", value: model.iteration ?? 'n/a', tone: model.iteration !== null ? 'active' : 'idle', sub: "PM iteration / run id" }), _jsx(DetailStat, { label: "\u4EFB\u52A1\u6570", value: model.taskCount, tone: model.taskCount > 0 ? 'active' : 'idle', sub: "snapshot.tasks" }), _jsx(DetailStat, { label: "\u51B3\u7B56\u8BB0\u5F55", value: model.decisions.length, tone: model.decisions.length > 0 ? 'active' : 'idle', sub: "dialogue / telemetry" }), _jsx(DetailStat, { label: "\u8FD0\u884C\u9636\u6BB5", value: model.running ? 'running' : 'idle', tone: model.running ? 'active' : 'idle', sub: model.tokensRealtime ? '实时 token 已接入' : '等待实时 usage' })] }), _jsxs("div", { className: "grid gap-3 lg:grid-cols-[0.95fr_1.05fr]", children: [_jsx(DetailBlock, { title: "\u5165\u53E3\u6458\u8981", subtitle: "\u7528\u6237\u8BF7\u6C42\u8FDB\u5165 ContextOS \u540E\u7684\u53EF\u89C2\u6D4B\u8D1F\u8F7D", children: _jsxs("div", { className: "space-y-2 text-[11px] text-text-muted", children: [_jsxs("div", { className: "rounded-lg bg-black/20 px-3 py-2", children: ["\u5F53\u524D\u9636\u6BB5\uFF1A", _jsx("span", { className: "font-mono text-text-main", children: model.running ? '运行中' : '空闲' })] }), _jsxs("div", { className: "rounded-lg bg-black/20 px-3 py-2", children: ["\u4EFB\u52A1\u770B\u677F\uFF1A", _jsx("span", { className: "font-mono text-text-main", children: model.taskCount }), " \u4E2A\u4EFB\u52A1"] }), _jsxs("div", { className: "rounded-lg bg-black/20 px-3 py-2", children: ["\u8D28\u91CF\u95E8\uFF1A", _jsx("span", { className: "font-mono text-text-main", children: model.errorCount > 0 ? '有风险' : '未见错误' })] })] }) }), _jsx(DetailBlock, { title: "\u6700\u8FD1\u8BF7\u6C42\u8BC1\u636E", subtitle: "\u6765\u81EA\u51B3\u7B56\u6D41\u548C\u8FD0\u884C\u65F6\u63A8\u9001", children: _jsx(DetailDecisionList, { rows: model.decisions }) })] })] }));
            break;
        case 'truthlog':
            body = (_jsxs(_Fragment, { children: [sharedStats, _jsxs("div", { className: "grid gap-3 lg:grid-cols-[0.9fr_1.1fr]", children: [_jsx(DetailBlock, { title: "\u4E8B\u4EF6\u7C7B\u578B\u5206\u5E03", subtitle: "\u6309\u771F\u5B9E\u89C2\u6D4B\u4E8B\u4EF6 category \u805A\u5408", children: model.eventTypes.length > 0 ? _jsx(EventTypeDistribution, { slices: model.eventTypes, total: model.eventTypesTotal }) : _jsx(DetailEmpty, { label: "\u6682\u65E0\u4E8B\u4EF6\u7C7B\u578B\u5206\u5E03" }) }), _jsx(DetailBlock, { title: "TruthLog \u6700\u8FD1\u4E8B\u4EF6", subtitle: "WebSocket \u5B9E\u65F6\u6D41\u5012\u5E8F", children: _jsx(DetailEventList, { events: recentEvents, emptyLabel: "\u6682\u65E0 TruthLog \u4E8B\u4EF6" }) })] })] }));
            break;
        case 'working_mem':
            body = (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid grid-cols-2 gap-2 lg:grid-cols-4", children: [_jsx(DetailStat, { label: "\u5728\u7A97\u9879", value: model.contextItemsCount !== null ? model.contextItemsCount : `~${roleWindowTotal}`, tone: (model.contextItemsCount ?? roleWindowTotal) > 0 ? 'active' : 'idle', sub: model.contextItemsCount !== null ? 'context.build 实测' : '角色窗口估算' }), _jsx(DetailStat, { label: "\u89D2\u8272\u6D3B\u52A8", value: `${activeRoles}/${model.roles.length}`, tone: activeRoles > 0 ? 'active' : 'idle', sub: "\u6709\u5B9E\u65F6\u4E8B\u4EF6\u7684\u89D2\u8272" }), _jsx(DetailStat, { label: "\u6700\u65B0\u7A97\u53E3", value: model.windowOccupancyTokens > 0 ? `~${contextOSFormat.tokens(model.windowOccupancyTokens)}` : '无 usage', tone: model.windowOccupancyTokens > 0 ? 'active' : 'idle', sub: `分母 ${windowDenominator}` }), _jsx(DetailStat, { label: "\u7A97\u53E3\u5360\u7528", value: model.contextWindowTokens !== null ? `${Math.round(contextWindowOccupancy * 100)}%` : '未知', tone: contextWindowOccupancy > 0 ? 'active' : 'idle', sub: model.contextWindowDetail })] }), _jsxs("div", { className: "grid gap-3 lg:grid-cols-[1fr_1fr]", children: [_jsx(DetailBlock, { title: "\u89D2\u8272\u5DE5\u4F5C\u8BB0\u5FC6", subtitle: "\u6BCF\u4E2A\u89D2\u8272\u81EA\u5DF1\u7684\u7A97\u53E3\u548C usage \u72B6\u6001", children: _jsx(DetailRoleList, { roles: model.roles }) }), _jsx(DetailBlock, { title: "WorkingMem \u8BC1\u636E", subtitle: "context.build / prompt_context \u76F8\u5173\u4E8B\u4EF6", children: _jsx(DetailEventList, { events: projectionEvents.length > 0 ? projectionEvents : recentEvents, emptyLabel: "\u6682\u65E0 WorkingMem \u4E8B\u4EF6" }) })] })] }));
            break;
        case 'projection':
            body = (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid grid-cols-2 gap-2 lg:grid-cols-4", children: [_jsx(DetailStat, { label: "\u6295\u5F71\u6570", value: model.projectionCount, tone: model.projectionCount > 0 ? 'active' : 'idle', sub: model.telemetryActive ? '真实投影事件' : '等待实时遥测' }), _jsx(DetailStat, { label: "\u4E0A\u4E0B\u6587\u9879", value: model.contextItemsCount ?? '未知', tone: model.contextItemsCount !== null ? 'active' : 'idle', sub: "context.build items_count" }), _jsx(DetailStat, { label: "\u88C5\u914D token", value: model.contextWindowTokens !== null ? windowDenominator : '未知', tone: model.contextWindowTokens !== null ? 'active' : 'idle', sub: "\u89D2\u8272\u7ED1\u5B9A\u6700\u5C0F\u7A97\u53E3" }), _jsx(DetailStat, { label: "\u4E8B\u4EF6\u7A97\u53E3", value: model.eventTypesTotal, tone: model.eventTypesTotal > 0 ? 'active' : 'idle', sub: model.telemetryWindowed ? '最近窗口' : '完整观测' })] }), _jsxs("div", { className: "grid gap-3 lg:grid-cols-[0.85fr_1.15fr]", children: [_jsx(DetailBlock, { title: "ProjectionEngine \u89E3\u91CA", subtitle: "\u6392\u5E8F\u6295\u5F71\u548C\u9884\u7B97\u89C4\u5212\u8BC1\u636E", children: _jsxs("div", { className: "space-y-2 text-[11px] text-text-muted", children: [_jsxs("div", { className: "rounded-lg bg-black/20 px-3 py-2", children: ["\u6295\u5F71\u6765\u6E90\uFF1A", _jsx("span", { className: "text-text-main", children: "context.build / prompt_context / final_request_context_audit" })] }), _jsxs("div", { className: "rounded-lg bg-black/20 px-3 py-2", children: ["\u8BA1\u6570\u7B56\u7565\uFF1A", _jsx("span", { className: "text-text-main", children: "\u6309 stable projection key \u53BB\u91CD" })] }), _jsxs("div", { className: "rounded-lg bg-black/20 px-3 py-2", children: ["\u5F53\u524D\u53EF\u4FE1\u5EA6\uFF1A", _jsx("span", { className: "text-text-main", children: model.telemetryActive ? '真实遥测' : '无实时证据' })] })] }) }), _jsx(DetailBlock, { title: "\u6295\u5F71\u4E8B\u4EF6", subtitle: "\u6700\u8FD1 context projection \u8BC1\u636E", children: _jsx(ProjectionEvidenceDeck, { events: projectionEvents }) })] })] }));
            break;
        case 'role_signal':
            body = (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid grid-cols-2 gap-2 lg:grid-cols-4", children: [_jsx(DetailStat, { label: "\u4E3B\u89D2\u8272", value: model.roles.length, tone: "active", sub: "PM / Architect / CE / Director / QA" }), _jsx(DetailStat, { label: "\u6D3B\u52A8\u89D2\u8272", value: activeRoles, tone: activeRoles > 0 ? 'active' : 'idle', sub: "\u6709\u5B9E\u65F6\u89D2\u8272\u4E8B\u4EF6" }), _jsx(DetailStat, { label: "\u53D7\u963B\u89D2\u8272", value: blockedRoles, tone: blockedRoles > 0 ? 'blocked' : 'idle', sub: "LLM readiness blocked" }), _jsx(DetailStat, { label: "\u6A21\u578B\u7ED1\u5B9A", value: model.bindingBudgets.length, tone: model.bindingBudgets.length > 0 ? 'active' : 'idle', sub: "provider/model \u9884\u7B97\u884C" })] }), _jsxs("div", { className: "grid gap-3 lg:grid-cols-[1fr_1fr]", children: [_jsx(DetailBlock, { title: "\u89D2\u8272\u4FE1\u53F7\u9762", subtitle: "\u89D2\u8272\u8FD0\u884C\u6001\u548C\u5185\u90E8 ContextOS \u8BA1\u6570", children: _jsx(DetailRoleList, { roles: model.roles }) }), _jsx(DetailBlock, { title: "\u6A21\u578B\u7ED1\u5B9A\u9884\u7B97", subtitle: "\u591A\u8DEF Director \u4F1A\u62C6\u6210\u72EC\u7ACB\u9884\u7B97\u884C", children: _jsx(DetailBindingBudgetList, { rows: model.bindingBudgets }) })] })] }));
            break;
        case 'prompt':
            body = (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid grid-cols-2 gap-2 lg:grid-cols-4", children: [_jsx(DetailStat, { label: "Prompt", value: contextOSFormat.tokens(model.promptTokens), tone: model.promptTokens > 0 ? 'active' : 'idle', sub: model.tokensRealtime ? 'journal llm 实时' : 'usage stats / 空' }), _jsx(DetailStat, { label: "Completion", value: contextOSFormat.tokens(model.completionTokens), tone: model.completionTokens > 0 ? 'active' : 'idle', sub: "\u8F93\u51FA token" }), _jsx(DetailStat, { label: "\u5E73\u5747\u6BCF\u6B21", value: contextOSFormat.tokens(model.avgPerCall), tone: model.avgPerCall > 0 ? 'active' : 'idle', sub: "total / calls" }), _jsx(DetailStat, { label: "\u8C03\u7528\u6570", value: model.calls, tone: model.calls > 0 ? 'active' : 'idle', sub: "\u79BB\u6563 LLM \u8C03\u7528" })] }), _jsxs("div", { className: "grid gap-3 lg:grid-cols-[0.8fr_1.2fr]", children: [_jsx(DetailBlock, { title: "\u63D0\u793A\u6784\u6210", subtitle: "\u771F\u5B9E Prompt / Completion \u4E8C\u5206", children: model.totalTokens > 0 ? (_jsx("div", { className: "space-y-2.5", children: model.budget.map((slice) => (_jsx(BudgetBar, { label: slice.label, tokens: slice.tokens, ratio: slice.ratio, colorClass: slice.colorClass }, slice.key))) })) : _jsx(DetailEmpty, { label: "\u6682\u65E0 token \u7528\u91CF" }) }), _jsx(DetailBlock, { title: "Prompt \u88C5\u914D\u4E8B\u4EF6", subtitle: "\u5305\u542B context token / prompt hash \u7684\u6700\u8FD1\u4E8B\u4EF6", children: _jsx(DetailEventList, { events: callEvents.length > 0 ? callEvents : recentEvents, emptyLabel: "\u6682\u65E0 Prompt \u88C5\u914D\u4E8B\u4EF6" }) })] })] }));
            break;
        case 'budget':
            body = (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid grid-cols-2 gap-2 lg:grid-cols-4", children: [_jsx(DetailStat, { label: "\u7A97\u53E3\u5360\u7528", value: model.contextWindowTokens !== null ? `${Math.round(contextWindowOccupancy * 100)}%` : '未知', tone: contextWindowOccupancy > 0 ? 'active' : 'idle', sub: model.contextWindowDetail }), _jsx(DetailStat, { label: "\u5360\u7528\u5206\u5B50", value: model.windowOccupancyTokens > 0 ? `~${contextOSFormat.tokens(model.windowOccupancyTokens)}` : '无 usage', tone: model.windowOccupancyTokens > 0 ? 'active' : 'idle', sub: "\u6700\u7EC8\u8BF7\u6C42\u6216\u5E73\u5747 prompt" }), _jsx(DetailStat, { label: "\u7A97\u53E3\u5206\u6BCD", value: windowDenominator, tone: model.contextWindowTokens !== null ? 'active' : 'idle', sub: model.contextWindowLabel }), _jsx(DetailStat, { label: "\u7ED1\u5B9A\u884C", value: model.bindingBudgets.length, tone: model.bindingBudgets.length > 0 ? 'active' : 'idle', sub: "provider/model budgets" })] }), _jsxs("div", { className: "grid gap-3 lg:grid-cols-[0.9fr_1.1fr]", children: [_jsx(DetailBlock, { title: "CompressionEngine \u5224\u5B9A", subtitle: "\u88C5\u914D\u540E\u9884\u7B97\u538B\u7F29\u515C\u5E95\u89C6\u89D2", children: _jsxs("div", { className: "space-y-2 text-[11px] text-text-muted", children: [_jsx("div", { className: "h-2 overflow-hidden rounded-full bg-white/5", children: _jsx("div", { className: cn('h-full rounded-full', contextWindowOccupancy > 0.85 ? 'bg-status-error' : contextWindowOccupancy > 0.6 ? 'bg-status-warning' : 'bg-accent-secondary'), style: { width: model.contextWindowTokens !== null ? `${Math.max(2, Math.round(contextWindowOccupancy * 100))}%` : '0%' } }) }), _jsx("div", { children: "\u5206\u5B50\u4F18\u5148\u7EA7\uFF1Afinal request token\uFF0C\u5176\u6B21 context tokens\uFF0C\u6700\u540E\u5E73\u5747 prompt \u4F30\u7B97\u3002" }), _jsx("div", { children: "\u5206\u6BCD\u6765\u6E90\uFF1A\u5F53\u524D\u89D2\u8272\u7ED1\u5B9A\u4E2D\u7684\u6700\u5C0F max_context_tokens\u3002" })] }) }), _jsx(DetailBlock, { title: "\u6A21\u578B\u9884\u7B97\u884C", subtitle: "\u6BCF\u4E2A provider/model \u5355\u72EC\u5C55\u793A", children: _jsx(DetailBindingBudgetList, { rows: model.bindingBudgets }) })] })] }));
            break;
        case 'llm':
            body = (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid grid-cols-2 gap-2 lg:grid-cols-4", children: [_jsx(DetailStat, { label: "\u8C03\u7528", value: model.calls, tone: model.calls > 0 ? 'active' : 'idle', sub: model.tokensRealtime ? '实时 journal llm' : '无实时 usage' }), _jsx(DetailStat, { label: "Token", value: contextOSFormat.tokens(model.totalTokens), tone: model.totalTokens > 0 ? 'active' : 'idle', sub: "prompt + completion" }), _jsx(DetailStat, { label: "\u6700\u8FD1\u65F6\u5EF6", value: model.realLatencyMs !== null ? `${model.realLatencyMs}ms` : '未知', tone: model.realLatencyMs !== null ? 'active' : 'idle', sub: "provider elapsed ms" }), _jsx(DetailStat, { label: "Worker", value: model.workers.length, tone: model.workers.length > 0 ? 'active' : 'idle', sub: model.hasWorkers ? '多 worker 追踪' : '未携带 worker_id' })] }), _jsxs("div", { className: "grid gap-3 lg:grid-cols-[0.95fr_1.05fr]", children: [_jsx(DetailBlock, { title: "LLM \u8C03\u7528\u4E8B\u4EF6", subtitle: "llm_completed / llm_failed", children: _jsx(LlmCallDeck, { events: callEvents }) }), _jsx(DetailBlock, { title: "\u6A21\u578B\u9884\u7B97\u4E0E\u5E76\u53D1", subtitle: "\u591A\u8DEF Director / provider \u5F52\u5C5E", children: model.workers.length > 0 ? (_jsx("div", { className: "grid grid-cols-1 gap-2 sm:grid-cols-2", children: model.workers.slice(0, 4).map((worker) => (_jsxs("div", { className: "rounded-lg border border-white/[0.06] bg-black/20 px-2.5 py-2", children: [_jsx("div", { className: "truncate font-mono text-[11px] font-semibold text-text-main", children: worker.workerId }), _jsxs("div", { className: "mt-1 grid grid-cols-3 gap-1 font-mono text-[9px] text-text-dim", children: [_jsxs("span", { children: [worker.calls, " calls"] }), _jsxs("span", { children: [contextOSFormat.tokens(worker.tokens), " tok"] }), _jsx("span", { children: worker.latencyMs !== null ? `${worker.latencyMs}ms` : 'n/a' })] })] }, worker.workerId))) })) : (_jsx(DetailBindingBudgetList, { rows: model.bindingBudgets })) })] })] }));
            break;
        case 'receipt':
            body = (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid grid-cols-2 gap-2 lg:grid-cols-4", children: [_jsx(DetailStat, { label: "\u56DE\u6267", value: model.receiptCount, tone: model.receiptCount > 0 ? 'active' : 'idle', sub: "context snapshot refs" }), _jsx(DetailStat, { label: "\u9519\u8BEF", value: model.errorCount, tone: model.errorCount > 0 ? 'blocked' : 'idle', sub: "Receipt / LLM / runtime" }), _jsx(DetailStat, { label: "\u5FEB\u7167\u4E8B\u4EF6", value: receiptEvents.length, tone: receiptEvents.length > 0 ? 'active' : 'idle', sub: "\u53EF\u8FFD\u8E2A context ref" }), _jsx(DetailStat, { label: "\u6700\u8FD1\u65F6\u5EF6", value: model.realLatencyMs !== null ? `${model.realLatencyMs}ms` : '未知', tone: model.realLatencyMs !== null ? 'active' : 'idle', sub: "\u56DE\u6267\u95ED\u73AF\u5EF6\u8FDF\u7EBF\u7D22" })] }), _jsxs("div", { className: "grid gap-3 lg:grid-cols-[0.95fr_1.05fr]", children: [_jsx(DetailBlock, { title: "\u56DE\u6267\u4E0E\u5FEB\u7167\u8BC1\u636E", subtitle: "\u53EF\u8FFD\u8E2A context snapshot \u6216\u964D\u7EA7\u539F\u56E0", children: _jsx(ReceiptEvidenceDeck, { events: receiptEvents.length > 0 ? receiptEvents : callEvents }) }), _jsx(DetailBlock, { title: "\u5F02\u5E38\u95ED\u73AF", subtitle: "ReceiptStore / provider / runtime \u9519\u8BEF", children: _jsx(DetailEventList, { events: errorEvents, emptyLabel: "\u6682\u65E0\u9519\u8BEF\u95ED\u73AF" }) })] })] }));
            break;
        default:
            body = (_jsxs(_Fragment, { children: [sharedStats, _jsx(DetailBlock, { title: "\u6700\u8FD1\u8BC1\u636E", subtitle: "\u8BE5\u8282\u70B9\u6682\u65E0\u4E13\u7528\u89C6\u56FE\uFF0C\u5C55\u793A\u6700\u8FD1\u8FD0\u884C\u65F6\u4E8B\u4EF6", children: _jsx(DetailEventList, { events: recentEvents, emptyLabel: "\u6682\u65E0\u4E8B\u4EF6" }) })] }));
    }
    return (_jsx("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-6 backdrop-blur-sm", role: "dialog", "aria-modal": "true", "aria-labelledby": "contextos-pipeline-detail-title", "data-testid": "contextos-pipeline-detail-modal", onMouseDown: (event) => {
            if (event.target === event.currentTarget)
                onClose();
        }, children: _jsxs("div", { className: cn('max-h-[88vh] w-full max-w-5xl overflow-hidden rounded-2xl border bg-bg-panel/95 shadow-[0_0_44px_rgba(74,158,158,0.18)] backdrop-blur-xl', stage.state === 'blocked' ? 'border-status-error/40' : 'border-accent-secondary/30'), "data-testid": `contextos-pipeline-detail-${stage.id}`, children: [_jsxs("header", { className: "flex items-start justify-between gap-3 border-b border-white/[0.08] px-4 py-3", children: [_jsxs("div", { className: "flex min-w-0 items-start gap-3", children: [_jsx("div", { className: cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-black/35', style.text), children: _jsx(Icon, { className: "h-5 w-5" }) }), _jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx("h2", { id: "contextos-pipeline-detail-title", className: "font-heading text-sm font-bold text-text-main", children: stage.label }), _jsx(StatusBadge, { color: badgeColorForState(stage.state), variant: "dot", pulse: stage.state === 'active', children: _jsx("span", { className: "font-mono text-[10px]", children: STATE_STYLES[stage.state].label }) })] }), _jsxs("div", { className: "mt-1 flex flex-wrap items-center gap-2 text-[10px] text-text-dim", children: [_jsx("span", { className: "font-mono text-accent-secondary/80", children: stage.component }), _jsx("span", { children: stage.hint }), _jsx("span", { className: cn('rounded bg-black/30 px-1.5 py-0.5 font-mono', style.text), children: stage.metric })] })] })] }), _jsx("button", { type: "button", onClick: onClose, className: "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-text-muted transition-colors hover:border-accent-secondary/40 hover:text-text-main focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-secondary/70", "aria-label": "\u5173\u95ED\u8BE6\u60C5", "data-testid": "contextos-pipeline-detail-close", children: _jsx(X, { className: "h-4 w-4" }) })] }), _jsx("div", { className: "max-h-[calc(88vh-76px)] space-y-3 overflow-auto p-4", children: body })] }) }));
}
function RoleHex({ role, selected, onSelect }) {
    const style = STATE_STYLES[role.state];
    const ctx = role.internalContext;
    const occupancyLabel = ctx.windowOccupancyTokens !== null
        ? `~${contextOSFormat.tokens(ctx.windowOccupancyTokens)}`
        : '无 usage';
    const windowLabel = ctx.contextWindowTokens !== null
        ? contextOSFormat.windowTokens(ctx.contextWindowTokens)
        : '窗口未知';
    const windowSourceLabel = ctx.contextWindowSource === 'binding'
        ? ctx.bindingBudgets.length > 1
            ? `${ctx.bindingBudgets.length} 路绑定`
            : ctx.contextWindowModel ? `${ctx.contextWindowModel} 绑定` : '绑定'
        : '未知';
    return (_jsxs("button", { type: "button", "data-testid": `contextos-role-${role.id}`, "data-selected": selected, "aria-pressed": selected, onClick: onSelect, title: `${role.title} ${role.courtTitle} · ${ctx.windowOccupancyDetail} · ${role.contextWindowDetail}`, className: cn('flex items-center gap-2 rounded-xl border px-2.5 py-2 text-left transition-all duration-300 hover:border-accent-secondary/40', style.ring, selected && 'ring-2 ring-accent-secondary/60'), children: [_jsx("div", { className: cn('flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-black/30 font-heading text-sm font-bold', style.text), children: role.courtTitle.slice(0, 1) }), _jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("div", { className: "flex items-center gap-1.5", children: [_jsx("span", { className: cn('h-1.5 w-1.5 rounded-full', style.dot) }), _jsx("span", { className: "truncate text-xs font-semibold text-text-main", children: role.title })] }), _jsx("div", { className: cn('truncate font-mono text-[10px]', style.text), children: role.detail }), _jsxs("div", { className: "mt-1 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 font-mono text-[9px]", children: [_jsx("span", { "data-testid": `contextos-role-occupancy-${role.id}`, className: cn('truncate', ctx.windowOccupancyTokens !== null ? 'text-accent-secondary' : 'text-text-dim'), title: ctx.windowOccupancyDetail, children: occupancyLabel }), _jsxs("span", { "data-testid": `contextos-role-window-${role.id}`, className: "shrink-0 rounded bg-white/5 px-1 text-text-muted", title: role.contextWindowDetail, children: ["/ ", windowLabel, " ", _jsxs("span", { className: "text-text-dim/70", children: ["(", windowSourceLabel, ")"] })] })] })] })] }));
}
function RoleInternalStat({ label, value, unit, sub, highlight = false }) {
    return (_jsxs("div", { className: "flex flex-col rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2", children: [_jsx("span", { className: "text-[9px] uppercase tracking-wider text-text-dim", children: label }), _jsxs("div", { className: "mt-0.5 flex items-baseline gap-1", children: [_jsx("span", { className: cn('font-mono text-sm font-bold', highlight ? 'text-accent-secondary' : 'text-text-main'), children: value }), unit && _jsx("span", { className: "text-[9px] text-text-dim", children: unit })] }), sub && _jsx("div", { className: "mt-0.5 truncate text-[9px] text-text-dim", title: sub, children: sub })] }));
}
function RoleInternalEventRow({ event }) {
    // L1 去噪 + 语义化：原始 event.kind（如 event.factory:factory_…）替换为中文语义徽章。
    const semantics = classifyEventSemantics(event);
    const tone = semantics.tone;
    const summaryText = safeText(event.summary) || safeText(event.kind) || '事件';
    const auditTitle = formatFinalRequestAuditTitle(event.finalRequestContextAudit);
    return (_jsxs("div", { className: "grid grid-cols-[68px_1fr] items-start gap-2 rounded-md px-2 py-1.5 text-[11px] hover:bg-white/[0.03]", "aria-label": `${semantics.badge} ${semantics.displaySummary}`, children: [_jsxs("div", { className: "flex items-center gap-1.5", children: [_jsx("span", { className: cn('h-1.5 w-1.5 shrink-0 rounded-full', STATE_STYLES[tone].dot) }), _jsx("span", { className: "font-mono text-[10px] text-text-dim", children: contextOSFormat.clock(event.ts) })] }), _jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-1.5", children: [_jsx("span", { className: cn('shrink-0 rounded px-1 font-mono text-[9px]', STATE_STYLES[tone].ring, STATE_STYLES[tone].text), title: semantics.rawChannel || undefined, children: semantics.badge }), (event.hasUsage || event.durationMs !== null || event.contextTokens !== null || event.hasReceipt || event.contextSnapshotDegraded || auditTitle) && (_jsxs("div", { className: "flex flex-wrap items-center gap-1", children: [event.hasUsage && event.totalTokens > 0 && (_jsxs("span", { className: "rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary", children: [contextOSFormat.tokens(event.totalTokens), " tok"] })), event.contextTokens !== null && event.contextTokens > 0 && (_jsxs("span", { className: "rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary", children: ["ctx ", contextOSFormat.tokens(event.contextTokens)] })), auditTitle && (_jsx("span", { className: "rounded bg-white/5 px-1 font-mono text-[9px] text-text-dim", title: auditTitle, children: "audit" })), _jsx(FinalRequestAgiCoverageBadges, { audit: event.finalRequestContextAudit, compact: true }), event.durationMs !== null && event.durationMs > 0 && (_jsxs("span", { className: "rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted", children: [event.durationMs, "ms"] })), event.hasReceipt && (_jsx("span", { className: "rounded bg-gold/10 px-1 font-mono text-[9px] text-gold", children: "\u5FEB\u7167" })), event.contextSnapshotDegraded && (_jsx("span", { className: "rounded bg-status-warning/10 px-1 font-mono text-[9px] text-status-warning", title: event.contextSnapshotDegraded.message || event.contextSnapshotDegraded.reason, children: "\u5FEB\u7167\u672A\u843D\u76D8" }))] }))] }), _jsx("div", { className: "truncate text-text-muted", title: summaryText, children: semantics.displaySummary || summaryText })] })] }));
}
// L2 实体线程：把同一 task/实体的多个生命周期事件聚合成可展开的状态流。
function EntityThreadRow({ thread }) {
    const [expanded, setExpanded] = useState(false);
    const summary = summarizeEntityThread(thread);
    const style = STATE_STYLES[summary.tone];
    const lastEvent = thread.events[thread.events.length - 1];
    return (_jsxs("div", { className: cn('rounded-md border px-2 py-1.5', style.ring), "data-testid": `contextos-entity-thread-${thread.id}`, children: [_jsxs("button", { type: "button", onClick: () => setExpanded((v) => !v), className: "flex w-full items-center gap-1.5 text-left", "aria-expanded": expanded, "aria-label": `${thread.displayId} ${summary.stateLabel}`, children: [_jsx(ChevronRight, { className: cn('h-3 w-3 shrink-0 text-text-dim transition-transform', expanded && 'rotate-90') }), _jsx("span", { className: "shrink-0 text-[11px] font-semibold text-text-main", children: thread.displayId }), _jsx("span", { className: cn('shrink-0 truncate rounded px-1.5 py-0.5 text-[9px] font-medium', style.ring, style.text), children: summary.stateLabel }), _jsx("span", { className: "ml-auto shrink-0 font-mono text-[9px] text-text-dim", children: contextOSFormat.clock(lastEvent?.ts) })] }), expanded && (_jsx("div", { className: "mt-1.5 flex flex-wrap items-center gap-1 pl-4", children: thread.steps.map((step, idx) => (_jsxs(Fragment, { children: [idx > 0 && _jsx(ArrowRight, { className: "h-2.5 w-2.5 shrink-0 text-text-dim/40" }), _jsx("span", { className: cn('rounded px-1.5 py-0.5 font-mono text-[9px]', STATE_STYLES[step.tone].ring, STATE_STYLES[step.tone].text), title: contextOSFormat.clock(step.ts), children: step.verbZh })] }, `${thread.id}-step-${idx}`))) }))] }));
}
function formatFinalRequestAuditTitle(audit) {
    if (!audit)
        return '';
    const coverage = typeof audit['coverage'] === 'object' && audit['coverage'] !== null
        ? audit['coverage']
        : {};
    const parts = [
        ['final', audit['final_request_token_estimate']],
        ['msg', audit['message_token_estimate']],
        ['tools', audit['tool_schema_token_estimate']],
        ['pm', coverage['has_pm_contract']],
        ['ce', coverage['has_chief_engineer_blueprint']],
        ['files', coverage['has_target_files']],
        ['feedback', coverage['has_failure_feedback']],
        ['agi_decision', coverage['has_resident_agi_decision_trace']],
        ['agi_capability', coverage['has_resident_agi_capability_surface']],
        ['agi_boundary', coverage['has_resident_agi_decision_boundary']],
    ].map(([key, value]) => `${key}=${String(value ?? 'n/a')}`);
    return parts.join(' ');
}
function StructureMetric({ label, value, tone = 'idle', sub, }) {
    return (_jsxs("div", { className: "min-w-0 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-[10px] font-semibold text-text-main", title: label, children: label }), _jsx("span", { className: cn('h-1.5 w-1.5 shrink-0 rounded-full', STATE_STYLES[tone].dot) })] }), _jsx("div", { className: cn('mt-1 font-mono text-sm font-bold', STATE_STYLES[tone].text), children: value }), sub && _jsx("div", { className: "mt-0.5 truncate text-[9px] text-text-dim", title: sub, children: sub })] }));
}
function ContextStructurePanel({ model, telemetry }) {
    const roleWindowTotal = model.roles.reduce((sum, role) => sum + (role.internalContext.workingMemoryItems ?? 0), 0);
    const activeRoles = model.roles.filter((role) => role.internalContext.eventCount > 0);
    const newestEvents = telemetry.events.slice(0, 8);
    return (_jsx(SectionCard, { title: "\u4E0A\u4E0B\u6587\u7ED3\u6784", subtitle: "TruthLog / WorkingMem / Projection / Receipt", icon: Database, className: "border-accent-secondary/20", children: _jsxs("div", { "data-testid": "contextos-structure-panel", className: "space-y-3", children: [_jsxs("div", { className: "grid grid-cols-2 gap-2 lg:grid-cols-4", children: [_jsx(StructureMetric, { label: "TruthLog", value: `${telemetry.events.length} 事件`, tone: telemetry.events.length > 0 ? 'active' : 'idle', sub: model.telemetryWindowed ? '最近窗口' : '实时流' }), _jsx(StructureMetric, { label: "WorkingMem", value: model.contextItemsCount !== null ? `${model.contextItemsCount} 项` : `~${roleWindowTotal} 项`, tone: roleWindowTotal > 0 || (model.contextItemsCount ?? 0) > 0 ? 'active' : 'idle', sub: model.contextItemsCount !== null ? 'context.build' : '角色事件窗口估算' }), _jsx(StructureMetric, { label: "ProjectionEngine", value: `${model.projectionCount} 投影`, tone: model.projectionCount > 0 ? 'active' : 'idle', sub: `${model.eventTypesTotal} 观测基数` }), _jsx(StructureMetric, { label: "ReceiptStore", value: `${model.receiptCount} 回执`, tone: model.receiptCount > 0 ? 'active' : model.errorCount > 0 ? 'blocked' : 'idle', sub: model.errorCount > 0 ? `${model.errorCount} 错误` : 'snapshot receipts' })] }), _jsxs("div", { className: "grid gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]", children: [_jsxs("div", { className: "rounded-lg border border-white/[0.06] bg-black/20 p-2.5", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-2", children: [_jsx("span", { className: "text-[10px] font-semibold uppercase tracking-wider text-text-dim", children: "\u89D2\u8272\u4E0A\u4E0B\u6587\u7A97\u53E3" }), _jsxs("span", { className: "font-mono text-[10px] text-text-muted", children: [activeRoles.length, "/", model.roles.length] })] }), _jsx("div", { className: "space-y-1.5", children: model.roles.map((role) => {
                                        const ctx = role.internalContext;
                                        const occupancyLabel = ctx.windowOccupancyTokens !== null
                                            ? `~${contextOSFormat.tokens(ctx.windowOccupancyTokens)}`
                                            : '无 usage';
                                        const windowLabel = ctx.contextWindowTokens !== null
                                            ? contextOSFormat.windowTokens(ctx.contextWindowTokens)
                                            : '未知';
                                        const windowSourceLabel = ctx.contextWindowSource === 'binding'
                                            ? ctx.contextWindowModel ? `${ctx.contextWindowModel} 绑定` : '绑定'
                                            : '未知';
                                        return (_jsxs("div", { className: "grid grid-cols-[72px_minmax(0,1fr)_64px_58px] items-center gap-2 rounded-md bg-white/[0.02] px-2 py-1.5 text-[10px]", children: [_jsx("span", { className: "truncate font-semibold text-text-main", title: role.title, children: role.title }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "h-1.5 overflow-hidden rounded-full bg-white/5", children: _jsx("div", { className: cn('h-full rounded-full', STATE_STYLES[ctx.state].dot), style: { width: `${Math.max(4, Math.min(100, ctx.eventCount * 12))}%` } }) }), _jsxs("div", { className: "mt-1 truncate text-[9px] text-text-dim", title: "\u4E8B\u4EF6\u6570 / \u5728\u7A97\u4E0A\u4E0B\u6587\u9879 / \u4E0A\u4E0B\u6587\u88C5\u914D\u6B21\u6570 / \u843D\u76D8\u56DE\u6267\u6570", children: ["\u4E8B\u4EF6 ", ctx.eventCount, " \u00B7 \u5728\u7A97 ", ctx.workingMemoryItems ?? 0, ctx.workingMemoryEstimated ? '~' : '', " \u00B7 \u88C5\u914D ", ctx.projectionCount, " \u00B7 \u56DE\u6267 ", ctx.receiptCount] })] }), _jsx("span", { className: cn('truncate text-right font-mono', ctx.windowOccupancyTokens !== null ? 'text-accent-secondary' : 'text-text-dim'), title: ctx.windowOccupancyDetail, children: occupancyLabel }), _jsxs("span", { className: "truncate text-right font-mono text-text-muted", title: ctx.contextWindowDetail, children: [windowLabel, " ", _jsxs("span", { className: "text-text-dim/70", children: ["(", windowSourceLabel, ")"] })] })] }, role.id));
                                    }) })] }), _jsxs("div", { className: "rounded-lg border border-white/[0.06] bg-black/20 p-2.5", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-2", children: [_jsx("span", { className: "text-[10px] font-semibold uppercase tracking-wider text-text-dim", children: "\u6700\u8FD1\u7ED3\u6784\u4E8B\u4EF6" }), _jsx("span", { className: "font-mono text-[10px] text-text-muted", children: newestEvents.length })] }), newestEvents.length > 0 ? (_jsx("div", { className: "space-y-1", children: newestEvents.map((event) => (_jsxs("div", { className: "grid grid-cols-[54px_80px_1fr] gap-2 rounded-md px-2 py-1.5 text-[10px] hover:bg-white/[0.03]", children: [_jsx("span", { className: "font-mono text-text-dim", children: contextOSFormat.clock(event.ts) }), _jsx("span", { className: "truncate text-text-muted", title: event.actor, children: safeText(event.actor) }), _jsx("span", { className: "truncate text-text-main", title: event.summary, children: safeText(event.summary) })] }, event.id))) })) : (_jsxs("div", { className: "flex flex-col items-center gap-2 rounded-md border border-dashed border-white/10 px-3 py-5 text-center", children: [_jsx(Database, { className: "h-4 w-4 text-text-dim/30" }), _jsx("div", { className: "text-[11px] text-text-dim", children: "\u6682\u65E0\u7ED3\u6784\u4E8B\u4EF6" })] }))] })] })] }) }));
}
function RoleInternalPanel({ role, onViewContext }) {
    const ctx = role.internalContext;
    const style = STATE_STYLES[ctx.state];
    // 人话摘要：把 TruthLog/WorkingMem/... 术语翻译成「在执行 / 待机 / 受阻」的明确结论。
    const summary = summarizeRoleContextState(ctx);
    // 事件观测：按实体聚合成生命周期线程（task N），无实体事件回退平铺叙事。
    const grouped = useMemo(() => groupEventsByEntity(ctx.events), [ctx.events]);
    const pipeline = [
        { id: 'truthlog', label: 'TruthLog', component: '事件真值流', hint: '角色专属事件流', state: ctx.eventCount > 0 ? 'active' : 'idle', metric: `${ctx.eventCount} 事件` },
        {
            id: 'working_mem',
            label: 'WorkingMem',
            component: '活动窗口',
            hint: ctx.workingMemoryEstimated ? '实时观测窗口' : '在窗上下文项',
            state: (ctx.workingMemoryItems ?? 0) > 0 ? 'active' : 'idle',
            metric: ctx.workingMemoryItems !== null
                ? `${ctx.workingMemoryEstimated ? '~' : ''}${ctx.workingMemoryItems} 项${ctx.workingMemoryEstimated ? ' 估算' : ''}`
                : '—',
        },
        { id: 'projection', label: 'ProjectionEngine', component: '投影装配', hint: '上下文装配次数', state: ctx.projectionCount > 0 ? 'active' : 'idle', metric: `${ctx.projectionCount} 投影` },
        { id: 'receipt', label: 'ReceiptStore', component: '快照回执', hint: '落盘回执数', state: ctx.receiptCount > 0 ? 'active' : 'idle', metric: `${ctx.receiptCount} 回执` },
    ];
    const displayedEvents = ctx.events.length;
    const hasTruncation = ctx.eventCount > displayedEvents;
    // Collect LLM calls with context_snapshot_ref or explicit snapshot degradation evidence from events.
    const llmCalls = ctx.events
        .filter((event) => (event.contextSnapshotRef || event.contextSnapshotDegraded) && (event.isCall || event.hasUsage))
        .slice(0, 5);
    return (_jsxs("div", { "data-testid": `contextos-role-panel-${role.id}`, className: cn('mt-3 rounded-xl border bg-bg-panel/40 p-3 backdrop-blur-sm transition-all duration-500', style.ring), children: [_jsxs("div", { className: "mb-3 flex items-center justify-between gap-3", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: cn('flex h-9 w-9 items-center justify-center rounded-lg bg-black/30 font-heading text-sm font-bold', style.text), children: role.courtTitle.slice(0, 1) }), _jsxs("div", { children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "text-sm font-semibold text-text-main", children: role.title }), _jsx("span", { className: "text-[10px] text-text-dim", children: role.courtTitle }), _jsx("span", { className: cn('rounded px-1.5 py-0.5 text-[9px] font-medium', style.ring, style.text), children: style.label })] }), _jsx("div", { className: "text-[10px] text-text-dim", children: ctx.lastEventAt !== null ? `最近活动 ${formatFreshness(ctx.lastEventAt)}` : '暂无观测事件' })] })] }), ctx.totalTokens > 0 && (_jsxs("div", { "data-testid": `contextos-role-panel-tokens-${role.id}`, className: "flex items-center gap-1.5 rounded-lg border border-white/10 bg-black/30 px-2 py-1", children: [_jsx(Coins, { className: "h-3.5 w-3.5 text-gold" }), _jsx("span", { className: "font-mono text-[11px] font-bold text-text-main", children: ctx.totalTokens.toLocaleString() }), _jsx("span", { className: "text-[9px] text-gold/70", children: "tok" })] }))] }), _jsx("div", { "data-testid": `contextos-role-summary-${role.id}`, className: cn('mb-3 rounded-lg border px-3 py-2', STATE_STYLES[summary.tone].ring), children: _jsxs("div", { className: "flex items-start gap-2", children: [_jsx("span", { className: cn('mt-[5px] h-2 w-2 shrink-0 rounded-full', STATE_STYLES[summary.tone].dot) }), _jsxs("div", { className: "min-w-0 space-y-0.5", children: [_jsx("div", { className: cn('text-[12px] font-semibold leading-snug', STATE_STYLES[summary.tone].text), children: summary.headline }), summary.detail && (_jsx("div", { className: "text-[10px] leading-relaxed text-text-muted", children: summary.detail }))] })] }) }), _jsxs("div", { className: "relative mb-3", children: [_jsx("div", { className: "flex items-center gap-1 overflow-x-auto pb-2", children: pipeline.map((stage, index) => (_jsxs("div", { className: "flex items-center gap-1", children: [index > 0 && _jsx(ArrowRight, { className: "h-3 w-3 shrink-0 text-text-dim/40" }), _jsxs("div", { "data-testid": `contextos-role-panel-stage-${role.id}-${stage.id}`, "data-state": stage.state, className: "flex w-[80px] shrink-0 flex-col items-center gap-0.5 rounded-lg border border-white/[0.06] bg-white/[0.02] px-1 py-1.5 text-center", children: [_jsx("span", { className: "text-[10px] font-semibold text-text-main", children: stage.label }), _jsx("span", { className: cn('rounded-full bg-black/30 px-1.5 py-0.5 font-mono text-[9px]', STATE_STYLES[stage.state].text), children: stage.metric })] })] }, stage.id))) }), _jsx("div", { className: "pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-bg-panel/70 to-transparent xl:hidden", "aria-hidden": true })] }), _jsxs("div", { className: "mb-3 grid grid-cols-2 gap-2 sm:grid-cols-5", children: [_jsx(RoleInternalStat, { label: "\u6D3B\u52A8", value: `${ctx.eventCount} · ${ctx.projectionCount}`, sub: `回执 ${ctx.receiptCount}` }), _jsx(RoleInternalStat, { label: "\u8C03\u7528", value: ctx.calls, sub: ctx.lastEventAt !== null ? `最近 ${formatFreshness(ctx.lastEventAt)}` : '无活动' }), _jsx(RoleInternalStat, { label: "\u7A97\u53E3", value: ctx.contextWindowTokens !== null ? contextOSFormat.windowTokens(ctx.contextWindowTokens) : '未知', sub: ctx.contextWindowSource === 'binding' ? `${ctx.contextWindowProvider ?? ''}${ctx.contextWindowModel ? ` / ${ctx.contextWindowModel}` : ''} · maxContextTokens` : ctx.contextWindowDetail, highlight: ctx.contextWindowTokens !== null }), _jsx(RoleInternalStat, { label: "\u5360\u7528", value: ctx.windowOccupancyTokens !== null ? `~${contextOSFormat.tokens(ctx.windowOccupancyTokens)}` : '—', sub: ctx.windowOccupancyLabel, highlight: ctx.windowOccupancyTokens !== null }), _jsx(RoleInternalStat, { label: "Token", value: ctx.totalTokens > 0 ? `${contextOSFormat.tokens(ctx.promptTokens)} / ${contextOSFormat.tokens(ctx.completionTokens)}` : '—', sub: ctx.totalTokens > 0 ? '提示 / 输出' : '无 usage 观测', highlight: ctx.totalTokens > 0 })] }), llmCalls.length > 0 && (_jsxs("div", { className: "mb-3", children: [_jsxs("div", { className: "mb-1.5 flex items-center justify-between text-[10px] uppercase tracking-wider text-text-dim", children: [_jsx("span", { children: "\u6700\u8FD1 LLM \u8C03\u7528" }), _jsxs("span", { className: "font-mono normal-case text-text-dim", children: [llmCalls.length, " \u6761"] })] }), _jsx("div", { className: "space-y-1", children: llmCalls.map((event) => (_jsxs("div", { className: "flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-[11px] hover:bg-white/[0.03]", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-1.5", children: [_jsx("span", { className: cn('h-1.5 w-1.5 shrink-0 rounded-full', event.category === 'error' ? 'bg-status-error' : 'bg-accent-secondary') }), _jsx("span", { className: "truncate font-mono text-[10px] text-text-dim", children: contextOSFormat.clock(event.ts) }), _jsx("span", { className: "truncate text-text-muted", title: event.summary, children: event.summary || event.kind })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-1.5", children: [event.totalTokens > 0 && (_jsxs("span", { className: "rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary", children: [contextOSFormat.tokens(event.totalTokens), " tok"] })), event.durationMs !== null && event.durationMs > 0 && (_jsxs("span", { className: "rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted", children: [event.durationMs, "ms"] })), event.contextSnapshotRef ? (_jsx("button", { type: "button", onClick: () => event.contextSnapshotRef && onViewContext(event.contextSnapshotRef), className: "rounded bg-accent-secondary/15 px-1.5 py-0.5 text-[9px] text-accent-secondary hover:bg-accent-secondary/25 transition-colors", title: "\u67E5\u770B\u5B8C\u6574\u4E0A\u4E0B\u6587", children: "\u67E5\u770B\u5B8C\u6574\u4E0A\u4E0B\u6587" })) : event.contextSnapshotDegraded ? (_jsx("span", { className: "rounded bg-status-warning/10 px-1.5 py-0.5 text-[9px] text-status-warning", title: event.contextSnapshotDegraded.message || event.contextSnapshotDegraded.reason, children: "\u5FEB\u7167\u672A\u843D\u76D8" })) : null] })] }, event.id))) })] })), _jsxs("div", { children: [_jsxs("div", { className: "mb-1.5 flex items-center justify-between text-[10px] uppercase tracking-wider text-text-dim", children: [_jsx("span", { children: "\u6700\u8FD1\u4E8B\u4EF6" }), hasTruncation && (_jsxs("span", { className: "font-mono normal-case text-text-dim", children: ["\u5C55\u793A\u6700\u8FD1 ", displayedEvents, " \u6761 \u00B7 \u5171 ", ctx.eventCount, " \u6761"] }))] }), ctx.events.length > 0 ? (_jsxs("div", { className: "space-y-2", "aria-live": "polite", "aria-atomic": "false", children: [grouped.threads.length > 0 && (_jsxs("div", { className: "space-y-1", children: [_jsx("div", { className: "text-[9px] tracking-wider text-text-dim", children: "\u4EFB\u52A1 / \u5B9E\u4F53\u7EBF\u7A0B" }), grouped.threads.map((thread) => (_jsx(EntityThreadRow, { thread: thread }, thread.id)))] })), grouped.loose.length > 0 && (_jsxs("div", { className: "space-y-1", children: [grouped.threads.length > 0 && (_jsx("div", { className: "text-[9px] tracking-wider text-text-dim", children: "\u5176\u5B83\u4E8B\u4EF6" })), grouped.loose.map((event) => (_jsx(RoleInternalEventRow, { event: event }, event.id)))] }))] })) : (_jsxs("div", { className: "flex flex-col items-center gap-2 rounded-lg border border-dashed border-white/10 px-3 py-4 text-center", "data-testid": `contextos-role-panel-empty-events-${role.id}`, children: [_jsx(Activity, { className: "h-4 w-4 text-text-dim/30" }), _jsx("div", { className: "text-[11px] text-text-dim", children: "\u8BE5\u89D2\u8272\u6682\u65E0\u5B9E\u65F6\u89C2\u6D4B\u4E8B\u4EF6" })] }))] })] }));
}
function BudgetBar({ label, tokens, ratio, colorClass }) {
    return (_jsxs("div", { className: "space-y-1", children: [_jsxs("div", { className: "flex items-center justify-between gap-2 text-[11px]", children: [_jsx("span", { className: "truncate text-text-muted", title: label, children: label }), _jsxs("span", { className: "shrink-0 font-mono text-text-main", children: [contextOSFormat.tokens(tokens), _jsxs("span", { className: "ml-1 text-text-dim", children: [Math.round(ratio * 100), "%"] })] })] }), _jsx("div", { className: "h-1.5 overflow-hidden rounded-full bg-white/5", children: _jsx("div", { className: cn('h-full rounded-full transition-all duration-500', colorClass), style: { width: `${Math.max(2, Math.round(ratio * 100))}%` } }) })] }));
}
const USAGE_KIND_LABELS = {
    provider: 'provider usage',
    stream_final: 'stream final',
    request_estimate: 'request estimate',
    char_estimate: 'char estimate',
    mixed: 'mixed usage',
    none: 'no usage',
};
function UsageMetricChip({ label, tokens, tone = 'neutral', }) {
    if (tokens <= 0)
        return null;
    return (_jsxs("span", { className: cn('rounded border px-1.5 py-0.5 font-mono text-[9px]', tone === 'cache'
            ? 'border-status-success/20 bg-status-success/10 text-status-success'
            : tone === 'tool'
                ? 'border-accent/20 bg-accent/10 text-accent'
                : tone === 'output'
                    ? 'border-gold/20 bg-gold/10 text-gold'
                    : tone === 'reasoning'
                        ? 'border-status-warning/20 bg-status-warning/10 text-status-warning'
                        : tone === 'error'
                            ? 'border-status-error/20 bg-status-error/10 text-status-error'
                            : 'border-white/[0.07] bg-white/[0.04] text-text-muted'), title: `${label}: ${tokens.toLocaleString()} tokens`, children: [label, " ", contextOSFormat.tokens(tokens)] }));
}
function UsageBreakdownChips({ promptTokens, completionTokens, cachedTokens, cacheCreationTokens, cacheReadTokens, toolTokens, reasoningTokens = 0, audioTokens = 0, serverToolUseCount = 0, }) {
    const directPromptTokens = Math.max(0, promptTokens - cacheCreationTokens - cacheReadTokens);
    const effectiveCachedTokens = Math.max(cachedTokens, cacheReadTokens);
    const hasBreakdown = directPromptTokens > 0 ||
        completionTokens > 0 ||
        effectiveCachedTokens > 0 ||
        cacheCreationTokens > 0 ||
        toolTokens > 0 ||
        reasoningTokens > 0 ||
        audioTokens > 0 ||
        serverToolUseCount > 0;
    if (!hasBreakdown)
        return null;
    return (_jsxs("div", { className: "flex flex-wrap gap-1", children: [_jsx(UsageMetricChip, { label: "in", tokens: directPromptTokens }), _jsx(UsageMetricChip, { label: "out", tokens: completionTokens, tone: "output" }), _jsx(UsageMetricChip, { label: "cache read", tokens: effectiveCachedTokens, tone: "cache" }), _jsx(UsageMetricChip, { label: "cache write", tokens: cacheCreationTokens, tone: "cache" }), _jsx(UsageMetricChip, { label: "tools", tokens: toolTokens, tone: "tool" }), _jsx(UsageMetricChip, { label: "reasoning", tokens: reasoningTokens, tone: "reasoning" }), _jsx(UsageMetricChip, { label: "audio", tokens: audioTokens, tone: "tool" }), _jsx(UsageMetricChip, { label: "server tools", tokens: serverToolUseCount, tone: "tool" })] }));
}
function BindingBudgetRow({ row }) {
    const hasUsage = row.windowOccupancyTokens !== null;
    const ratio = hasUsage && row.contextWindowTokens !== null && row.contextWindowTokens > 0
        ? Math.max(0, Math.min(1, row.windowOccupancyTokens / row.contextWindowTokens))
        : 0;
    const provider = row.providerName || row.providerId || 'Provider unknown';
    const model = row.model || '未归属模型';
    const usageLabel = row.usageSource === 'matched'
        ? '模型实测'
        : row.usageSource === 'role_aggregate'
            ? '角色聚合'
            : '无 usage';
    const usageKindLabel = USAGE_KIND_LABELS[row.usageKind];
    const provenanceLabel = row.usageProvenance === 'provider'
        ? '真实'
        : row.usageProvenance === 'estimated'
            ? '估算'
            : row.usageProvenance === 'mixed'
                ? '混合'
                : null;
    const taskRef = row.taskId || row.pmTaskId || row.chiefBlueprintId;
    return (_jsxs("div", { "data-testid": `contextos-binding-budget-${row.id}`, className: "rounded-lg border border-white/[0.06] bg-white/[0.025] px-2.5 py-2", title: row.windowOccupancyDetail, children: [_jsxs("div", { className: "mb-1.5 flex items-start justify-between gap-2", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-[11px] font-semibold text-text-main", title: row.label, children: model }), _jsx("div", { className: "truncate font-mono text-[9px] text-text-dim", title: provider, children: provider })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-1", children: [_jsx("span", { className: cn('rounded px-1.5 py-0.5 text-[9px]', row.usageSource === 'matched'
                                    ? 'bg-accent-secondary/10 text-accent-secondary'
                                    : row.usageSource === 'role_aggregate'
                                        ? 'bg-status-warning/10 text-status-warning'
                                        : 'bg-white/5 text-text-dim'), children: usageLabel }), row.calls > 0 && (_jsxs("span", { className: "rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-muted", children: [row.calls, " calls"] })), row.usageKind !== 'none' && (_jsx("span", { className: "rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-[9px] text-text-dim", children: usageKindLabel })), provenanceLabel && (_jsx("span", { className: cn('rounded px-1.5 py-0.5 text-[9px]', row.usageProvenance === 'provider'
                                    ? 'bg-status-success/10 text-status-success'
                                    : row.usageProvenance === 'estimated'
                                        ? 'bg-status-warning/10 text-status-warning'
                                        : 'bg-white/[0.05] text-text-dim'), children: provenanceLabel })), row.skipped && (_jsx("span", { className: "rounded bg-status-error/10 px-1.5 py-0.5 text-[9px] text-status-error", title: row.skipReason || undefined, children: "skipped" }))] })] }), _jsx("div", { className: "h-1.5 overflow-hidden rounded-full bg-white/5", children: _jsx("div", { className: cn('h-full rounded-full transition-all duration-500', ratio > 0.85 ? 'bg-status-error' : ratio > 0.6 ? 'bg-status-warning' : 'bg-accent-secondary'), style: { width: hasUsage ? `${Math.max(2, Math.round(ratio * 100))}%` : '0%' } }) }), _jsxs("div", { className: "mt-1.5 flex items-center justify-between gap-2 font-mono text-[9px] text-text-dim", children: [_jsxs("span", { className: "truncate", children: [hasUsage ? `~${contextOSFormat.tokens(row.windowOccupancyTokens)}` : '无 usage', _jsx("span", { className: "ml-1 text-text-dim/70", children: row.windowOccupancyLabel })] }), _jsxs("span", { className: "shrink-0", children: ["/ ", row.contextWindowTokens !== null ? contextOSFormat.windowTokens(row.contextWindowTokens) : '未知'] })] }), (row.totalTokens > 0 || row.latencyMs !== null) && (_jsxs("div", { className: "mt-1 flex items-center justify-end gap-1.5 font-mono text-[9px] text-text-dim/80", children: [row.totalTokens > 0 && _jsxs("span", { children: [contextOSFormat.tokens(row.totalTokens), " tok"] }), row.latencyMs !== null && _jsxs("span", { children: [row.latencyMs, "ms"] })] })), taskRef && (_jsxs("div", { className: "mt-1 truncate font-mono text-[9px] text-text-dim/80", title: taskRef, children: ["task ", taskRef] })), _jsx("div", { className: "mt-1.5", children: _jsx(UsageBreakdownChips, { promptTokens: row.promptTokens, completionTokens: row.completionTokens, cachedTokens: row.cachedTokens, cacheCreationTokens: row.cacheCreationTokens, cacheReadTokens: row.cacheReadTokens, toolTokens: row.toolTokens, reasoningTokens: row.reasoningTokens, audioTokens: row.audioTokens, serverToolUseCount: row.serverToolUseCount }) })] }));
}
function EventTypeDistribution({ slices, total }) {
    return (_jsxs("div", { className: "space-y-2.5", children: [_jsx("div", { className: "flex h-2 overflow-hidden rounded-full bg-white/5", role: "img", "aria-label": "\u4E8B\u4EF6\u7C7B\u578B\u5206\u5E03", children: slices.map((slice) => (_jsx("div", { className: cn('h-full', slice.colorClass), style: { width: `${Math.max(1, Math.round(slice.ratio * 100))}%` }, title: `${slice.label} · ${slice.count} (${Math.round(slice.ratio * 100)}%)` }, slice.key))) }), _jsx("div", { className: "flex flex-wrap gap-x-3 gap-y-1.5", children: slices.map((slice) => (_jsxs("div", { className: "flex items-center gap-1.5 text-[10px]", children: [_jsx("span", { className: cn('h-2 w-2 shrink-0 rounded-sm', slice.colorClass) }), _jsx("span", { className: "text-text-muted", children: slice.label }), _jsx("span", { className: "font-mono text-text-main", children: slice.count }), _jsxs("span", { className: "text-text-dim", children: [Math.round(slice.ratio * 100), "%"] })] }, slice.key))) }), _jsxs("div", { className: "text-right font-mono text-[9px] text-text-dim", children: ["\u57FA\u4E8E\u6700\u8FD1 ", total, " \u6761\u89C2\u6D4B\u4E8B\u4EF6"] })] }));
}
function DecisionTable({ rows }) {
    const toneClass = {
        info: 'text-text-muted',
        success: 'text-status-success',
        warning: 'text-status-warning',
        error: 'text-status-error',
    };
    if (rows.length === 0) {
        return (_jsxs("div", { className: "flex flex-col items-center gap-2 rounded-lg border border-dashed border-white/10 px-3 py-6 text-center", "data-testid": "contextos-decision-empty", children: [_jsx(Activity, { className: "h-5 w-5 text-text-dim/40" }), _jsx("div", { className: "text-[11px] text-text-dim", children: _jsx("span", { className: "font-medium", children: "\u6682\u65E0\u51B3\u7B56 / \u56DE\u6267\u8BB0\u5F55" }) }), _jsx("div", { className: "text-[10px] text-text-dim/60", children: "\u542F\u52A8 PM \u6216 Director \u540E\u5C06\u5B9E\u65F6\u6D41\u5165" })] }));
    }
    return (_jsx("div", { className: "space-y-1", children: rows.map((row, index) => (_jsxs("div", { className: "grid grid-cols-[64px_72px_1fr] items-start gap-2 rounded-md px-2 py-1.5 text-[11px] hover:bg-white/[0.03]", children: [_jsx("span", { className: "font-mono text-[10px] text-text-dim", children: row.time }), _jsx("span", { className: cn('truncate font-medium', toneClass[row.tone]), title: `${row.actor} · ${row.kind}`, children: safeText(row.actor) }), _jsxs("div", { className: "min-w-0", children: [_jsx("span", { className: "block truncate text-text-muted", title: row.summary, children: safeText(row.summary) || safeText(row.kind) }), (row.source === 'telemetry') && (row.tokens || row.latencyMs || row.receipt) && (_jsxs("div", { className: "mt-0.5 flex flex-wrap items-center gap-1", children: [row.kind && (_jsx("span", { className: "rounded bg-white/5 px-1 font-mono text-[9px] text-text-dim", children: row.kind })), typeof row.tokens === 'number' && row.tokens > 0 && (_jsxs("span", { className: "rounded bg-accent-secondary/10 px-1 font-mono text-[9px] text-accent-secondary", children: [contextOSFormat.tokens(row.tokens), " tok"] })), typeof row.latencyMs === 'number' && row.latencyMs > 0 && (_jsxs("span", { className: "rounded bg-white/5 px-1 font-mono text-[9px] text-text-muted", children: [row.latencyMs, "ms"] })), row.receipt && (_jsx("span", { className: "rounded bg-gold/10 px-1 font-mono text-[9px] text-gold", children: "\u5FEB\u7167" }))] }))] })] }, `${row.id}-${index}`))) }));
}
function SectionCard({ title, subtitle, icon: Icon, children, className, action }) {
    return (_jsxs("section", { className: cn('flex flex-col rounded-xl border border-white/[0.07] bg-bg-panel/40 backdrop-blur-sm', className), children: [_jsxs("header", { className: "flex items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-2.5", children: [_jsxs("div", { className: "flex items-center gap-2 min-w-0", children: [_jsx(Icon, { className: "h-3.5 w-3.5 shrink-0 text-accent-secondary" }), _jsx("span", { className: "truncate text-xs font-semibold text-text-main", children: title }), subtitle && _jsx("span", { className: "truncate text-[10px] text-text-dim", children: subtitle })] }), action] }), _jsx("div", { className: "min-h-0 flex-1 p-3", children: children })] }));
}
function WorkerCardView({ worker, onViewContext }) {
    const style = STATE_STYLES[worker.state];
    return (_jsxs("div", { "data-testid": `contextos-worker-${worker.workerId}`, "data-state": worker.state, className: cn('rounded-lg border bg-white/[0.02] p-2.5 transition-all duration-300', style.ring), children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("div", { className: cn('flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-black/30', style.text), children: _jsx(Cpu, { className: "h-3.5 w-3.5" }) }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate font-mono text-[11px] font-semibold text-text-main", title: worker.workerId, children: worker.workerId }), _jsx("div", { className: cn('truncate text-[9px]', style.text), children: worker.role })] })] }), _jsx("span", { className: cn('rounded px-1.5 py-0.5 text-[9px] font-medium', style.ring, style.text), children: style.label })] }), _jsxs("div", { className: "mt-2 grid grid-cols-3 gap-1.5 text-[10px]", children: [_jsxs("div", { className: "rounded bg-black/20 px-1.5 py-1", children: [_jsx("div", { className: "text-[8px] uppercase tracking-wider text-text-dim", children: "\u8C03\u7528" }), _jsx("div", { className: "font-mono font-semibold text-text-main", children: worker.calls })] }), _jsxs("div", { className: "rounded bg-black/20 px-1.5 py-1", children: [_jsx("div", { className: "text-[8px] uppercase tracking-wider text-text-dim", children: "Token" }), _jsx("div", { className: "font-mono font-semibold text-text-main", children: worker.tokens > 0 ? contextOSFormat.tokens(worker.tokens) : '0' })] }), _jsxs("div", { className: "rounded bg-black/20 px-1.5 py-1", children: [_jsx("div", { className: "text-[8px] uppercase tracking-wider text-text-dim", children: "\u65F6\u5EF6" }), _jsx("div", { className: "font-mono font-semibold text-text-main", children: worker.latencyMs !== null ? `${worker.latencyMs}ms` : '—' })] })] }), worker.latestContextSnapshotRef && (_jsx("button", { type: "button", "data-testid": `contextos-worker-view-${worker.workerId}`, onClick: () => onViewContext(worker.latestContextSnapshotRef, worker.workerId), className: "mt-2 w-full rounded bg-accent-secondary/15 px-2 py-1 text-[10px] text-accent-secondary hover:bg-accent-secondary/25 transition-colors", children: "\u67E5\u770B worker \u4E0A\u4E0B\u6587" }))] }));
}
function WorkerPanel({ workers, onViewContext, }) {
    return (_jsx(SectionCard, { title: "\u591A worker LLM \u8FFD\u8E2A", subtitle: `Multi-worker LLM Tracking · ${workers.length} worker`, icon: Cpu, action: _jsxs("span", { className: "text-[10px] text-text-dim", "data-testid": "contextos-worker-count", children: [workers.length, " \u4E2A\u5E76\u53D1 worker"] }), className: "border-accent/30", children: _jsx("div", { "data-testid": "contextos-worker-panel", className: "space-y-2", children: _jsx("div", { className: "grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3", children: workers.map((worker) => (_jsx(WorkerCardView, { worker: worker, onViewContext: onViewContext }, worker.workerId))) }) }) }));
}
// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------
export function ContextOSWorkspace({ workspace, onBackToMain, onRefresh, live, reconnecting = false, usageStats, currentPhase, pmRunning, directorRunning, llmRuntimeState, dialogueEvents, executionLogs, llmStreamEvents, processStreamEvents, snapshot, qualityGate, controlPlaneProjection, }) {
    // 真实 ContextOS 遥测：直接派生自 useRuntime 经 WebSocket(/v2/ws/runtime) 实时推送的运行时流。
    const telemetry = useMemo(() => buildTelemetryFromStream(llmStreamEvents, executionLogs, processStreamEvents), [llmStreamEvents, executionLogs, processStreamEvents]);
    const model = useMemo(() => buildContextOSModel({
        usageStats,
        dialogueEvents,
        executionLogs,
        snapshot,
        llmRuntimeState,
        currentPhase,
        pmRunning,
        directorRunning,
        telemetry,
    }), [usageStats, dialogueEvents, executionLogs, snapshot, llmRuntimeState, currentPhase, pmRunning, directorRunning, telemetry]);
    const [activeRole, setActiveRole] = useState(null);
    const [showStructure, setShowStructure] = useState(false);
    const [viewerHash, setViewerHash] = useState(null);
    const [viewerRole, setViewerRole] = useState('');
    // Phase 3+：worker-scoped context viewer。
    const [viewerWorkerId, setViewerWorkerId] = useState(null);
    const [pipelineDetailId, setPipelineDetailId] = useState(null);
    const wsTone = live ? 'success' : reconnecting ? 'warning' : 'error';
    const wsLabel = live ? 'WS LIVE' : reconnecting ? 'WS RECONNECT' : 'WS OFFLINE';
    const phaseLabel = (currentPhase || 'idle').trim() || 'idle';
    const ledgerGatePassed = controlPlaneGatePassed(controlPlaneProjection);
    const gatePassed = ledgerGatePassed ?? qualityGate?.passed;
    const gateSource = ledgerGatePassed === undefined ? 'quality gate' : 'Run Ledger';
    // 观测到活动 = PM/Director 运行中 或 真实遥测有内容。
    const observed = model.running || model.telemetryActive;
    // 「真正有数据」= 真实遥测有内容；此时不再视为空闲水印。
    const idle = !model.telemetryActive && (model.dataIdle || (!live && !model.running));
    const pipelineLive = observed && live;
    // 新鲜度以"最近一条 WS 推送事件"的时间为准，避免陈旧数据被误读为实时。
    const lastEventEpoch = model.lastTelemetryEpoch;
    const telemetryAgeMs = lastEventEpoch ? Date.now() - lastEventEpoch : null;
    const telemetryFresh = telemetryAgeMs !== null && telemetryAgeMs < 30000; // 30s 内视为"实时"
    const freshnessLabel = lastEventEpoch ? formatFreshness(lastEventEpoch) : null;
    const contextStoreRefreshSignal = useMemo(() => {
        const latestSnapshotEvent = telemetry.events.find((event) => event.contextSnapshotRef || event.contextSnapshotDegraded || event.contextHash);
        if (!latestSnapshotEvent)
            return null;
        const ref = latestSnapshotEvent.contextSnapshotRef || latestSnapshotEvent.contextHash || latestSnapshotEvent.id;
        return `${ref}:${latestSnapshotEvent.epoch}:${latestSnapshotEvent.seq}`;
    }, [telemetry.events]);
    const filteredDecisions = useMemo(() => model.decisions.filter((row) => decisionMatchesRole(row.actor, activeRole)), [model.decisions, activeRole]);
    const selectedRole = activeRole ? model.roles.find((role) => role.id === activeRole) ?? null : null;
    const budgetWindowTokens = selectedRole?.contextWindowTokens ?? model.contextWindowTokens;
    const budgetWindowSource = selectedRole?.contextWindowSource ?? model.contextWindowSource;
    const budgetWindowLabel = selectedRole
        ? `${selectedRole.id.toUpperCase()} · ${selectedRole.title} · ${selectedRole.contextWindowLabel}${budgetWindowSource === 'binding' ? ' · 绑定' : ''}`
        : `${model.contextWindowLabel}${budgetWindowSource === 'binding' ? ' · 绑定' : ''}`;
    const budgetWindowDetail = selectedRole?.contextWindowDetail ?? model.contextWindowDetail;
    const globalWindowOccupancyTokens = model.windowOccupancyTokens > 0 ? model.windowOccupancyTokens : null;
    const budgetWindowOccupancyTokens = selectedRole
        ? selectedRole.internalContext.windowOccupancyTokens
        : globalWindowOccupancyTokens;
    const budgetWindowOccupancyLabel = selectedRole
        ? selectedRole.internalContext.windowOccupancyLabel
        : globalWindowOccupancyTokens !== null ? '平均提示 (估算)' : '无 usage';
    const budgetWindowOccupancyDetail = selectedRole
        ? `${selectedRole.internalContext.windowOccupancyDetail} · ${budgetWindowDetail}`
        : globalWindowOccupancyTokens !== null ? budgetWindowDetail : `尚无全局 usage 观测 · ${budgetWindowDetail}`;
    const budgetWindowOccupancy = budgetWindowOccupancyTokens !== null && budgetWindowTokens !== null && budgetWindowTokens > 0
        ? Math.max(0, Math.min(1, budgetWindowOccupancyTokens / budgetWindowTokens))
        : 0;
    const hasBudgetWindowUsage = budgetWindowOccupancyTokens !== null;
    const budgetBindingRows = selectedRole
        ? selectedRole.internalContext.bindingBudgets
        : model.bindingBudgets;
    const visibleBudgetBindingRows = budgetBindingRows.slice(0, selectedRole ? 8 : 10);
    const receiptStage = {
        id: 'receipt',
        label: 'Receipt',
        component: 'Context Snapshot + Telemetry',
        hint: '落盘上下文快照与遥测反馈闭环',
        state: model.errorCount > 0 ? 'blocked' : model.receiptCount > 0 || model.calls > 0 ? 'active' : 'idle',
        metric: model.errorCount > 0
            ? `${model.errorCount} 错误`
            : model.receiptCount > 0
                ? `${model.receiptCount} 快照`
                : `${model.calls} 调用`,
    };
    const selectedPipelineStage = pipelineDetailId
        ? [...model.pipeline, receiptStage].find((stage) => stage.id === pipelineDetailId) ?? null
        : null;
    const toggleRole = (roleId) => setActiveRole((prev) => (prev === roleId ? null : roleId));
    // WS 是推送模型——遥测随事件自动更新；刷新按钮仅触发外层（重连/状态拉取）。
    const handleRefresh = () => {
        onRefresh?.();
    };
    return (_jsxs("div", { "data-testid": "contextos-workspace", className: "flex h-full flex-col overflow-hidden bg-bg text-text-main", children: [_jsxs("header", { className: "flex h-14 shrink-0 items-center justify-between border-b border-accent-secondary/20 bg-bg-panel/60 px-4 backdrop-blur", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-4", children: [_jsxs(Button, { variant: "ghost", size: "sm", onClick: onBackToMain, "data-testid": "contextos-back", className: "text-text-muted hover:bg-white/5 hover:text-text-main", children: [_jsx(ChevronLeft, { className: "mr-1 h-4 w-4" }), "\u8FD4\u56DE"] }), _jsxs("div", { className: "flex min-w-0 items-center gap-3", children: [_jsx("div", { className: "flex h-8 w-8 items-center justify-center rounded-lg bg-accent-secondary/15 text-accent-secondary ring-1 ring-accent-secondary/30", children: _jsx(Network, { className: "h-4 w-4" }) }), _jsxs("div", { className: "min-w-0", children: [_jsx("h1", { className: "font-heading text-sm font-bold text-text-main", children: "ContextOS \u5B9E\u65F6\u89C6\u56FE" }), _jsxs("p", { className: "truncate text-[10px] uppercase tracking-wider text-accent-secondary/70", title: workspace, children: ["\u4E0A\u4E0B\u6587\u64CD\u4F5C\u7CFB\u7EDF \u00B7 ", workspaceLabel(workspace, '未选定工作区')] })] })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(StatusBadge, { color: model.running ? 'success' : 'default', variant: "dot", pulse: model.running, children: _jsxs("span", { className: "font-mono text-[10px]", children: ["\u9636\u6BB5 ", phaseLabel] }) }), _jsxs("div", { className: "flex items-center gap-1.5 rounded-lg border border-accent-secondary/20 bg-black/30 px-2.5 py-1", title: "\u5B9E\u65F6 LLM \u6D3B\u52A8\uFF1A\u8C03\u7528\u6B21\u6570 \u00B7 token \u603B\u91CF \u00B7 \u6700\u8FD1\u65F6\u5EF6", "data-testid": "contextos-resource-chip", children: [_jsx(Activity, { className: "h-3.5 w-3.5 text-accent-secondary" }), _jsx("span", { className: "font-mono text-[11px] font-bold text-text-main", children: model.calls.toLocaleString() }), _jsx("span", { className: "text-[9px] font-bold uppercase tracking-wider text-accent-secondary/70", children: "\u8C03\u7528" }), model.totalTokens > 0 && (_jsxs(_Fragment, { children: [_jsx("span", { className: "text-text-dim/60", children: "\u00B7" }), _jsx(Coins, { className: "h-3 w-3 text-gold" }), _jsx("span", { className: "font-mono text-[11px] font-bold text-text-main", children: model.totalTokens.toLocaleString() }), _jsx("span", { className: "text-[9px] font-bold uppercase tracking-wider text-gold/70", children: "tok" })] })), model.realLatencyMs !== null && (_jsxs("span", { className: "font-mono text-[10px] text-text-muted", children: ["\u00B7 ", model.realLatencyMs, "ms"] }))] }), _jsx(StatusBadge, { color: model.telemetryActive ? (telemetryFresh ? 'success' : 'warning') : 'default', variant: "dot", pulse: model.telemetryActive && telemetryFresh, children: _jsxs("span", { className: "font-mono text-[10px]", title: "ContextOS \u9065\u6D4B\uFF1AWebSocket /v2/ws/runtime\uFF0C\u7ECF Nats-JetStream \u63A8\u9001\uFF1B\u65F6\u95F4\u4E3A\u6700\u8FD1\u4E00\u6761\u4E8B\u4EF6", "data-testid": "contextos-telemetry-freshness", children: [model.telemetryActive
                                            ? `${telemetryFresh ? '实时遥测' : '遥测'}${freshnessLabel ? ` · ${freshnessLabel}` : ''}`
                                            : '遥测待命', _jsxs("span", { className: "ml-1 text-text-dim/70", children: ["\u00B7 ", wsLabel] })] }) }), gatePassed !== undefined && (_jsx("span", { className: cn('h-2 w-2 rounded-full', gatePassed ? 'bg-status-success' : 'bg-status-warning'), title: `质量门 ${gatePassed ? 'PASS' : 'HOLD'} · ${gateSource}` })), controlPlaneProjection && (_jsxs(_Fragment, { children: [_jsxs("div", { className: cn('flex items-center gap-1.5 rounded-lg border px-2.5 py-1 font-mono text-[10px]', controlPlaneProjection.ok
                                            ? 'border-cyan-400/30 bg-cyan-400/10 text-cyan-100'
                                            : 'border-amber-400/30 bg-amber-400/10 text-amber-100'), "data-testid": "contextos-control-plane-projection", title: controlPlaneProjection.detail, children: [_jsx(ShieldCheck, { className: "h-3.5 w-3.5" }), _jsx("span", { children: controlPlaneProjectionLabel(controlPlaneProjection) }), _jsx("span", { className: "text-text-dim/60", children: "\u00B7" }), _jsx("span", { children: controlPlaneProjectionSummary(controlPlaneProjection) }), _jsx("span", { className: "text-text-dim/70", children: controlPlaneSourceSummary(controlPlaneProjection) })] }), _jsxs("div", { className: cn('flex items-center gap-1.5 rounded-lg border px-2.5 py-1 font-mono text-[10px]', controlPlaneProjection.evidence_policy?.ok
                                            ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-100'
                                            : 'border-slate-500/30 bg-slate-500/10 text-slate-200'), "data-testid": "contextos-evidence-policy", title: evidencePolicySummary(controlPlaneProjection), children: [_jsx(Gauge, { className: "h-3.5 w-3.5" }), _jsx("span", { children: evidencePolicyLabel(controlPlaneProjection) }), _jsx("span", { className: "text-text-dim/60", children: "\u00B7" }), _jsx("span", { children: evidencePolicySummary(controlPlaneProjection) })] })] })), _jsxs(Button, { variant: showStructure ? 'default' : 'outline', size: "sm", onClick: () => setShowStructure((value) => !value), "data-testid": "contextos-structure-toggle", title: "\u6253\u5F00 ContextOS \u771F\u5B9E\u4E0A\u4E0B\u6587\u7ED3\u6784", "aria-pressed": showStructure, className: cn(showStructure
                                    ? 'bg-accent-secondary text-bg hover:bg-accent-secondary/90'
                                    : 'border-accent-secondary/30 text-accent-secondary hover:bg-accent-secondary/10'), children: [_jsx(Database, { className: "mr-1.5 h-3.5 w-3.5" }), "\u4E0A\u4E0B\u6587\u7ED3\u6784"] }), _jsx(Button, { variant: "outline", size: "sm", onClick: handleRefresh, "data-testid": "contextos-refresh", title: "\u5237\u65B0\u8FD0\u884C\u72B6\u6001\u4E0E\u9065\u6D4B", "aria-label": "\u5237\u65B0\u8FD0\u884C\u72B6\u6001\u4E0E\u9065\u6D4B", className: "border-accent-secondary/30 text-accent-secondary hover:bg-accent-secondary/10", children: _jsx(RefreshCw, { className: "h-3.5 w-3.5" }) })] })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-2 border-b border-white/[0.05] bg-bg-panel/30 px-4 py-1.5", children: [_jsx("span", { className: "text-[10px] uppercase tracking-wider text-text-dim", children: "\u89D2\u8272\u89C6\u89D2" }), _jsxs("div", { className: "flex items-center gap-1", children: [_jsx("button", { type: "button", "data-testid": "contextos-roletab-all", "aria-pressed": activeRole === null, onClick: () => setActiveRole(null), className: cn('rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors', activeRole === null ? 'bg-accent-secondary/15 text-accent-secondary' : 'text-text-muted hover:text-text-main hover:bg-white/5'), children: "\u5168\u90E8" }), model.roles.map((role) => (_jsxs("button", { type: "button", "data-testid": `contextos-roletab-${role.id}`, "aria-pressed": activeRole === role.id, onClick: () => toggleRole(role.id), className: cn('flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors', activeRole === role.id ? 'bg-accent-secondary/15 text-accent-secondary' : 'text-text-muted hover:text-text-main hover:bg-white/5'), children: [_jsx("span", { className: cn('h-1.5 w-1.5 rounded-full', STATE_STYLES[role.state].dot) }), role.title] }, role.id)))] }), activeRole && (_jsxs("span", { className: "ml-auto text-[10px] text-text-dim", children: ["\u5DF2\u8FC7\u6EE4\u51B3\u7B56\u6D41 \u00B7 ", filteredDecisions.length, " \u6761"] }))] }), _jsx("main", { className: "min-h-0 flex-1 overflow-auto p-4", children: _jsxs("div", { className: "grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_300px]", children: [_jsxs("div", { className: "flex flex-col gap-4", children: [_jsx(SectionCard, { title: "\u7CFB\u7EDF\u6D41\u8F6C\u4E0E\u6570\u636E\u6D41\u56FE", subtitle: "\u5B9E\u65F6\u4E0A\u4E0B\u6587\u88C5\u914D\u7BA1\u7EBF (Context Pipeline)", icon: Network, action: _jsx(StatusBadge, { color: observed ? 'success' : 'default', variant: "dot", pulse: observed, children: _jsx("span", { className: "text-[10px]", children: observed ? '装配中' : '空闲' }) }), children: _jsxs("div", { className: "relative", children: [_jsxs("div", { className: cn('flex items-center gap-1 overflow-x-auto pb-2 transition-opacity', idle && 'opacity-40'), children: [model.pipeline.map((stage, index) => (_jsxs("div", { className: "flex items-center gap-1", children: [index > 0 && _jsx(FlowArrow, { active: pipelineLive }), _jsx(PipelineNode, { stage: stage, selected: pipelineDetailId === stage.id, onSelect: () => setPipelineDetailId(stage.id) })] }, stage.id))), _jsx(FlowArrow, { active: pipelineLive }), _jsx(PipelineNode, { stage: receiptStage, selected: pipelineDetailId === receiptStage.id, onSelect: () => setPipelineDetailId(receiptStage.id) })] }), idle && (_jsx("div", { className: "pointer-events-none absolute inset-0 flex items-center justify-center", children: _jsxs("div", { className: "flex flex-col items-center gap-1.5 rounded-full border border-white/10 bg-black/50 px-4 py-2 backdrop-blur-sm", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Network, { className: "h-3.5 w-3.5 text-text-dim/50" }), _jsx("span", { className: "font-heading text-xs tracking-widest text-text-dim", children: "\u7A7A\u95F2 \u00B7 \u7B49\u5F85\u8FD0\u884C" })] }), _jsx("span", { className: "text-[9px] text-text-dim/50", children: "\u542F\u52A8 PM \u6216 Director \u540E\u7BA1\u7EBF\u5C06\u6FC0\u6D3B" })] }) })), _jsx("div", { className: "pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-bg-panel/70 to-transparent xl:hidden", "aria-hidden": true })] }) }), showStructure && _jsx(ContextStructurePanel, { model: model, telemetry: telemetry }), model.hasWorkers && model.workers.length > 0 && (_jsx(WorkerPanel, { workers: model.workers, onViewContext: (ref, workerId) => {
                                        setViewerHash(ref);
                                        setViewerRole('director');
                                        setViewerWorkerId(workerId);
                                    } })), _jsxs(SectionCard, { title: "\u89D2\u8272\u4FE1\u53F7\u9762", subtitle: `RoleSignalPlane · ${model.roles.length} 主角色`, icon: Boxes, action: _jsx("span", { className: "text-[10px] text-text-dim", children: "\u70B9\u51FB\u89D2\u8272\u67E5\u770B\u5185\u90E8 ContextOS \u72B6\u6001" }), children: [_jsx("div", { className: "grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5", children: model.roles.map((role) => (_jsx(RoleHex, { role: role, selected: activeRole === role.id, onSelect: () => toggleRole(role.id) }, role.id))) }), activeRole && (_jsx(RoleInternalPanel, { role: model.roles.find((r) => r.id === activeRole), onViewContext: (hash) => {
                                                setViewerHash(hash);
                                                setViewerRole(activeRole);
                                                setViewerWorkerId(null);
                                            } }))] }), _jsx(SectionCard, { title: "\u51B3\u7B56 / \u56DE\u6267\u6D41", subtitle: model.telemetryActive
                                        ? activeRole
                                            ? `实时事件流 · 仅 ${activeRole.toUpperCase()}`
                                            : '实时事件流 · Nats-JetStream'
                                        : activeRole
                                            ? `决策与回执流 · 仅 ${activeRole.toUpperCase()}`
                                            : '决策与回执流', icon: Activity, className: "min-h-[220px]", action: model.telemetryActive ? (_jsxs("span", { className: "rounded-full border border-accent-secondary/20 bg-accent-secondary/[0.08] px-2 py-0.5 font-mono text-[9px] text-accent-secondary", "data-testid": "contextos-telemetry-source", title: "\u51B3\u7B56\u6D41\u6765\u81EA ContextOS \u5B9E\u65F6\u9065\u6D4B\uFF08WebSocket /v2/ws/runtime \u63A8\u9001\uFF09", children: ["REAL \u00B7 ", model.calls, " \u8C03\u7528 \u00B7 ", model.projectionCount, " \u6295\u5F71", model.telemetryWindowed ? ' · 最近窗口' : ''] })) : undefined, children: _jsx(DecisionTable, { rows: filteredDecisions }) })] }), _jsxs("div", { className: "flex flex-col gap-4", children: [_jsx(SectionCard, { title: "\u4E0A\u4E0B\u6587\u9884\u7B97", subtitle: "Context Budget", icon: Coins, children: _jsxs("div", { className: "space-y-4", children: [_jsx("div", { children: _jsx("div", { className: "flex flex-wrap items-baseline gap-2", children: model.totalTokens > 0 ? (_jsxs(_Fragment, { children: [_jsx("span", { className: "font-heading text-3xl font-bold text-text-main", children: model.totalTokens.toLocaleString() }), _jsx("span", { className: "text-[11px] text-text-dim", children: model.usageSourceLabel }), model.estimatedCalls > 0 && (_jsxs("span", { className: "rounded bg-status-warning/10 px-1 py-0.5 text-[9px] text-status-warning", "data-testid": "contextos-estimated-marker", title: `其中 ${model.estimatedCalls} 次调用的 token 为字符估算（用量统计通道）`, children: ["\u542B\u4F30\u7B97 ", model.estimatedCalls] }))] })) : (_jsxs("div", { className: "flex flex-col items-center gap-2 rounded-lg border border-dashed border-white/10 px-3 py-4 text-center", "data-testid": "contextos-tokens-unavailable", children: [_jsx(Coins, { className: "h-5 w-5 text-text-dim/30" }), _jsx("div", { className: "text-[11px] text-text-dim", children: _jsx("span", { className: "font-medium", children: "\u7B49\u5F85\u9996\u6B21 LLM \u8C03\u7528" }) }), _jsx("div", { className: "text-[10px] text-text-dim/60", children: "\u5B9E\u65F6 token \u968F journal \u6D41\u5230\u8FBE" })] })) }) }), model.totalTokens > 0 ? (_jsxs("div", { className: "space-y-2.5", children: [model.budget.map((slice) => (_jsx(BudgetBar, { label: slice.label, tokens: slice.tokens, ratio: slice.ratio, colorClass: slice.colorClass }, slice.key))), _jsx(UsageBreakdownChips, { promptTokens: model.promptTokens, completionTokens: model.completionTokens, cachedTokens: model.cachedTokens, cacheCreationTokens: model.cacheCreationTokens, cacheReadTokens: model.cacheReadTokens, toolTokens: model.toolTokens, reasoningTokens: model.reasoningTokens, audioTokens: model.audioTokens, serverToolUseCount: model.serverToolUseCount })] })) : (_jsxs("div", { className: "flex flex-col items-center gap-1.5 rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-[11px] text-text-dim", children: [_jsx(Coins, { className: "h-4 w-4 text-text-dim/30" }), _jsx("span", { children: "\u7B49\u5F85\u9996\u6B21\u8C03\u7528 \u00B7 \u6682\u65E0 token \u7528\u91CF" })] })), _jsxs("div", { className: "space-y-1 border-t border-white/[0.06] pt-3", children: [_jsxs("div", { className: "flex items-center justify-between text-[11px]", children: [_jsxs("span", { className: "flex min-w-0 items-center gap-1 text-text-muted", children: [_jsx(Gauge, { className: "h-3 w-3 shrink-0" }), _jsx("span", { className: "truncate", children: "\u4E0A\u4E0B\u6587\u7A97\u53E3\u5360\u7528" }), _jsx("span", { className: cn('shrink-0 rounded px-1 text-[9px]', hasBudgetWindowUsage ? 'bg-white/5 text-text-dim' : 'bg-status-warning/10 text-status-warning'), children: hasBudgetWindowUsage ? budgetWindowOccupancyLabel : '未观测' })] }), _jsx("span", { className: "font-mono text-text-main", children: hasBudgetWindowUsage ? `${Math.round(budgetWindowOccupancy * 100)}%` : '—' })] }), _jsx("div", { className: "h-2 overflow-hidden rounded-full bg-white/5", children: _jsx("div", { className: cn('h-full rounded-full transition-all duration-500', budgetWindowOccupancy > 0.85 ? 'bg-status-error' : budgetWindowOccupancy > 0.6 ? 'bg-status-warning' : 'bg-accent-secondary'), style: { width: hasBudgetWindowUsage ? `${Math.max(2, Math.round(budgetWindowOccupancy * 100))}%` : '0%' } }) }), _jsxs("div", { className: "flex items-center justify-end gap-1 text-right font-mono text-[9px] text-text-dim", "data-testid": "contextos-window-source", "data-usage-state": hasBudgetWindowUsage ? 'observed' : 'none', title: budgetWindowOccupancyDetail, children: [_jsx("span", { children: budgetWindowOccupancyTokens !== null ? `~${contextOSFormat.tokens(budgetWindowOccupancyTokens)}` : '无 usage' }), _jsx("span", { children: "/" }), _jsx("span", { children: budgetWindowTokens !== null ? contextOSFormat.windowTokens(budgetWindowTokens) : '未知' }), _jsx("span", { className: "max-w-[120px] truncate", children: budgetWindowOccupancyLabel }), _jsx("span", { className: "max-w-[170px] truncate", children: budgetWindowLabel })] })] }), visibleBudgetBindingRows.length > 0 && (_jsxs("div", { className: "space-y-2 border-t border-white/[0.06] pt-3", "data-testid": "contextos-binding-budgets", children: [_jsxs("div", { className: "flex items-center justify-between gap-2 text-[11px]", children: [_jsxs("span", { className: "flex min-w-0 items-center gap-1 text-text-muted", children: [_jsx(Cpu, { className: "h-3 w-3 shrink-0" }), _jsx("span", { className: "truncate", children: selectedRole ? `${selectedRole.title} 模型预算` : '模型预算' })] }), _jsxs("span", { className: "font-mono text-[9px] text-text-dim", children: [budgetBindingRows.length, " \u8DEF"] })] }), _jsx("div", { className: "space-y-1.5", children: visibleBudgetBindingRows.map((row) => (_jsx(BindingBudgetRow, { row: row }, row.id))) }), budgetBindingRows.length > visibleBudgetBindingRows.length && (_jsxs("div", { className: "text-right font-mono text-[9px] text-text-dim", children: ["\u4EC5\u663E\u793A\u524D ", visibleBudgetBindingRows.length, " \u8DEF"] }))] }))] }) }), model.eventTypes.length > 0 && (_jsx(SectionCard, { title: "\u4E8B\u4EF6\u7C7B\u578B\u5206\u5E03", subtitle: "Event Types \u00B7 \u771F\u5B9E\u89C2\u6D4B", icon: Activity, children: _jsx("div", { "data-testid": "contextos-event-types", children: _jsx(EventTypeDistribution, { slices: model.eventTypes, total: model.eventTypesTotal }) }) })), _jsx(ContextStoreStatsPanel, { workspace: workspace, refreshSignal: contextStoreRefreshSignal })] })] }) }), selectedPipelineStage && (_jsx(PipelineDetailModal, { stage: selectedPipelineStage, model: model, telemetry: telemetry, onClose: () => setPipelineDetailId(null) })), viewerHash && (_jsx(ContextViewerModal, { contextSnapshotRef: viewerHash, roleId: viewerRole, workspace: workspace, workerId: viewerWorkerId, onClose: () => {
                    setViewerHash(null);
                    setViewerRole('');
                    setViewerWorkerId(null);
                } }))] }));
}
