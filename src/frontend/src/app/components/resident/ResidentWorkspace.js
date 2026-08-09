import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from "react";
import { Activity, ArrowLeft, AlertTriangle, Bot, Brain, CheckCircle2, ChevronDown, ChevronRight, ClipboardCheck, Eye, FileText, Play, Plus, Radio, RefreshCw, Send, Settings, ShieldCheck, Square, Target, Terminal, X, FileSearch, Sparkles, FlaskConical, Wrench, Ban, Package, Pencil, } from "lucide-react";
import { EvidenceViewer } from "./EvidenceViewer";
import { ExecutionProgressBar } from "./ExecutionProgressBar";
import { useResident } from "@/hooks/useResident";
import { Button } from "@/app/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, } from "@/app/components/ui/card";
import { Input } from "@/app/components/ui/input";
import { Textarea } from "@/app/components/ui/textarea";
import { Badge } from "@/app/components/ui/badge";
import { Switch } from "@/app/components/ui/switch";
import { cn } from "@/app/components/ui/utils";
const TAB_OPTIONS = ["overview", "goals", "decisions", "evolution"];
const AGI_EVIDENCE_INTERFACE_CATEGORIES = new Set([
    "audit_diagnosis",
    "audit_verdict",
    "audit_evidence",
    "context_discovery",
    "director_repair_advisory",
    "director_repair_strategy",
    "llm_audit",
    "run_ledger",
    "verification_policy",
]);
const DEFAULT_AGI_PARTICIPATION_FLAGS = [
    "final_request_audit",
    "quality_gate_response",
    "architecture_option_selection",
    "evidence_interface_selection",
    "goal_promotion",
    "decision_trace",
    "capability_surface",
    "decision_boundary",
    "director_repair_strategy_catalog",
    "director_repair_coverage",
    "director_repair_advisory_policy",
];
const AGI_PARTICIPATION_LABELS = {
    final_request_audit: "最终请求审计",
    quality_gate_response: "质量门禁响应",
    architecture_option_selection: "架构选型研判",
    evidence_interface_selection: "证据接口选择",
    goal_promotion: "目标推进判断",
    decision_trace: "决策交接记录",
    capability_surface: "能力面可见性",
    decision_boundary: "决策边界审计",
    director_repair_strategy_catalog: "Director 修复策略目录",
    director_repair_coverage: "Director 修复覆盖审计",
    director_repair_advisory_policy: "Director 修复建议边界",
};
const AGI_REPAIR_ADVISORY_SCOPE_IDS = [
    "director.repair.advisory",
    "director_repair_advisory_policy",
    "director_repair_coverage",
    "director_repair_strategy_catalog",
];
const AGI_PARTICIPATION_FOCUS_SCOPE_KEYS = [
    "quality_gate_response",
    "evidence_interface_selection",
    "architecture_option_selection",
    "goal_promotion_readiness",
    "goal_promotion",
    "director_repair_advisory_policy",
];
const AGI_UI_TOKEN_LABELS = {
    active: "已激活",
    advisory_only: "仅建议",
    allowed: "允许",
    available: "可用",
    blocked: "已阻断",
    contract_fallback: "契约兜底",
    disabled: "已停用",
    eligible: "可注入",
    enabled: "已启用",
    fail: "失败",
    failure: "失败",
    false: "否",
    governed_execute_only: "仅受控执行",
    governed_execution: "受控执行",
    governed_write: "受控写入",
    high: "高",
    hold: "暂缓",
    inactive: "未激活",
    invalid: "无效",
    later: "稍后",
    low: "低",
    materialized: "已固化",
    medium: "中",
    metadata_only: "仅元数据",
    needs_public_facade: "需要公开门面",
    none: "无",
    now: "现在",
    pass: "通过",
    pending: "待处理",
    platform: "平台",
    read_only: "只读",
    ready: "就绪",
    rejected: "已拒绝",
    request_evidence: "请求证据",
    request_missing_evidence: "请求缺失证据",
    runtime_fresh: "运行态已刷新",
    success: "成功",
    true: "是",
    unavailable: "不可用",
    unknown: "未知",
};
function formatAgiUiToken(value) {
    const token = String(value ?? "")
        .trim()
        .toLowerCase();
    if (!token)
        return "暂无";
    if (token.startsWith("invalid"))
        return "无效";
    return AGI_UI_TOKEN_LABELS[token] || String(value);
}
function formatAgiBoolean(value) {
    return value ? "是" : "否";
}
function formatAgiAllowed(value) {
    return value ? "允许" : "已阻断";
}
function formatAgiActive(value) {
    return value ? "已激活" : "未激活";
}
function formatAgiRoleChain(value) {
    const token = String(value || "").trim();
    if (!token)
        return "只读/观察优先";
    if (token === "PM → Chief Engineer → Director") {
        return "项目经理 → 总工程师 → 执行官";
    }
    return token
        .split("Chief Engineer")
        .join("总工程师")
        .split("Director")
        .join("执行官")
        .split("PM")
        .join("项目经理");
}
function formatTime(value) {
    if (!value)
        return "暂无";
    const parsed = Date.parse(value);
    if (!Number.isFinite(parsed))
        return value;
    const date = new Date(parsed);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    if (minutes < 1)
        return "刚刚";
    if (minutes < 60)
        return `${minutes}分钟前`;
    if (hours < 24)
        return `${hours}小时前`;
    if (days < 7)
        return `${days}天前`;
    return date.toLocaleDateString();
}
function uniqueStrings(values) {
    const seen = new Set();
    const result = [];
    values.forEach((value) => {
        const token = String(value || "").trim();
        if (!token || seen.has(token))
            return;
        seen.add(token);
        result.push(token);
    });
    return result;
}
function normalizeAgiParticipationScope(scope) {
    return String(scope || "")
        .trim()
        .toLowerCase()
        .replace(/[.\-\s]+/g, "_");
}
function isAgiParticipationScopeSelected(scope, selectedScopes) {
    const scopeKey = normalizeAgiParticipationScope(scope);
    return selectedScopes.some((selectedScope) => normalizeAgiParticipationScope(selectedScope) === scopeKey);
}
function buildAgiParticipationFlags(scopes, knownScopes = DEFAULT_AGI_PARTICIPATION_FLAGS) {
    const selected = new Set(scopes.map(normalizeAgiParticipationScope));
    const keys = uniqueStrings([...knownScopes, ...scopes]);
    return keys.reduce((acc, scope) => {
        acc[scope] = selected.has(normalizeAgiParticipationScope(scope));
        return acc;
    }, {});
}
function selectedAgiParticipationScopes(participation) {
    if (!participation)
        return [];
    const selectedFromFlags = Object.entries(participation.participation || {})
        .filter(([, enabled]) => enabled)
        .map(([scope]) => scope);
    return uniqueStrings([...(participation.scopes || []), ...selectedFromFlags]);
}
function agiParticipationScopeTestId(scope) {
    return normalizeAgiParticipationScope(scope).replace(/_/g, "-");
}
function selectAgiParticipationFocusOptions(options) {
    const byKey = new Map();
    options.forEach((option) => {
        const key = normalizeAgiParticipationScope(option.scope);
        if (key && !byKey.has(key)) {
            byKey.set(key, option);
        }
    });
    const selectedKeys = new Set();
    const selected = [];
    AGI_PARTICIPATION_FOCUS_SCOPE_KEYS.forEach((key) => {
        const option = byKey.get(key);
        if (!option ||
            selectedKeys.has(normalizeAgiParticipationScope(option.scope))) {
            return;
        }
        selected.push(option);
        selectedKeys.add(normalizeAgiParticipationScope(option.scope));
    });
    options.forEach((option) => {
        if (selected.length >= 4)
            return;
        const key = normalizeAgiParticipationScope(option.scope);
        if (!key || selectedKeys.has(key))
            return;
        selected.push(option);
        selectedKeys.add(key);
    });
    return selected.slice(0, 4);
}
function describeAgiParticipationScope(option) {
    const key = normalizeAgiParticipationScope(option.scope);
    if (key === "quality_gate_response") {
        return "根据构建、测试、审计证据选择阻断、补证据或继续。";
    }
    if (key === "evidence_interface_selection") {
        return "决定先读取哪些 ContextOS、Run Ledger、Audit 证据。";
    }
    if (key === "architecture_option_selection") {
        return "基于当前任务合同与仓库证据比较架构/依赖选项。";
    }
    if (key === "goal_promotion_readiness" || key === "goal_promotion") {
        return "判断目标是否足够成熟，是否进入项目经理 → 总工程师 → 执行官链路。";
    }
    if (key === "director_repair_advisory_policy") {
        return "只给 Director 修复策略建议，不直接写入或放行。";
    }
    return option.category
        ? `${formatAgiUiToken(option.category)} 范围，由后端策略目录声明。`
        : "由后端参与策略声明，保存后进入常驻 AGI 契约。";
}
function GoalStatusBadge({ status }) {
    const token = status.toLowerCase();
    if (token === "approved" || token === "materialized") {
        return (_jsx(Badge, { className: "border-slate-700 bg-slate-950 text-slate-300", children: "\u5DF2\u6279\u51C6" }));
    }
    if (token === "rejected") {
        return (_jsx(Badge, { className: "bg-red-500/10 text-red-400 border-red-500/20", children: "\u5DF2\u62D2\u7EDD" }));
    }
    return (_jsx(Badge, { className: "border-slate-800 bg-slate-950 text-slate-500", children: "\u5F85\u5BA1\u6279" }));
}
function clampPercent(value) {
    if (!Number.isFinite(value))
        return 0;
    return Math.max(0, Math.min(100, value));
}
function ratioPercent(numerator, denominator) {
    if (!Number.isFinite(denominator) || denominator <= 0)
        return 0;
    return clampPercent((numerator / denominator) * 100);
}
function formatPercent(value) {
    return `${Math.round(clampPercent(value))}%`;
}
function ProgressTrack({ value, tone = "neutral", }) {
    return (_jsx("div", { className: "h-1.5 overflow-hidden rounded-full bg-slate-900", children: _jsx("div", { className: cn("h-full rounded-full transition-[width] duration-500 ease-out", tone === "warning" ? "bg-amber-300/70" : "bg-slate-300/70"), style: { width: `${formatPercent(value)}` } }) }));
}
function SegmentedMeter({ segments, }) {
    const filtered = segments.filter((segment) => segment.value > 0);
    const total = filtered.reduce((sum, segment) => sum + segment.value, 0);
    if (total <= 0)
        return null;
    return (_jsxs("div", { className: "space-y-2", children: [_jsx("div", { className: "flex h-2 overflow-hidden rounded-full bg-slate-900", children: filtered.map((segment) => (_jsx("div", { className: cn("h-full transition-[width] duration-500 ease-out", segment.className || "bg-slate-500"), style: { width: `${ratioPercent(segment.value, total)}%` }, title: `${segment.label}: ${segment.value}` }, segment.label))) }), _jsx("div", { className: "flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-500", children: filtered.map((segment) => (_jsxs("span", { className: "inline-flex items-center gap-1.5", children: [_jsx("span", { className: cn("size-1.5 rounded-full", segment.className || "bg-slate-500") }), segment.label, " ", segment.value] }, segment.label))) })] }));
}
function severityClass(severity) {
    if (severity === "danger") {
        return "border-rose-500/25 bg-rose-500/10 text-rose-200";
    }
    if (severity === "warn") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
    }
    if (severity === "ok") {
        return "border-emerald-500/20 bg-emerald-500/10 text-emerald-200";
    }
    return "border-slate-800 bg-slate-950/70 text-slate-400";
}
function toolTraceStatusClass(status) {
    const normalized = status.toLowerCase();
    if (["passed", "available", "read"].includes(normalized)) {
        return "border-emerald-500/20 bg-emerald-500/10 text-emerald-200";
    }
    if (["blocked", "failed", "error"].includes(normalized)) {
        return "border-rose-500/25 bg-rose-500/10 text-rose-200";
    }
    return "border-slate-700 bg-slate-900/70 text-slate-300";
}
function decisionRouteStatusClass(status) {
    const normalized = status.toLowerCase();
    if (normalized.includes("blocked")) {
        return "border-rose-500/25 bg-rose-500/10 text-rose-200";
    }
    if (normalized.includes("handoff") || normalized.includes("role_turn")) {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
    }
    if (normalized.includes("read_only")) {
        return "border-sky-500/20 bg-sky-500/10 text-sky-100";
    }
    return "border-slate-800 bg-slate-950/60 text-slate-300";
}
function actionTimelineSeverity(status) {
    const normalized = status.toLowerCase();
    if (normalized.includes("blocked") ||
        normalized.includes("failed") ||
        normalized.includes("error")) {
        return "danger";
    }
    if (normalized.includes("judged") ||
        normalized.includes("executed") ||
        normalized.includes("handoff") ||
        normalized.includes("role_turn")) {
        return "ok";
    }
    if (normalized.includes("read"))
        return "idle";
    return "warn";
}
function quickCommandIcon(icon) {
    const className = "size-3.5";
    if (icon === "blocker")
        return _jsx(AlertTriangle, { className: className });
    if (icon === "evidence")
        return _jsx(FileSearch, { className: className });
    if (icon === "judgement")
        return _jsx(Brain, { className: className });
    if (icon === "repair")
        return _jsx(Wrench, { className: className });
    if (icon === "tick")
        return _jsx(RefreshCw, { className: className });
    if (icon === "model")
        return _jsx(Settings, { className: className });
    return _jsx(Activity, { className: className });
}
function quickCommandClass(severity = "idle") {
    if (severity === "danger") {
        return "border-rose-500/25 bg-rose-500/10 text-rose-100 hover:border-rose-300/50";
    }
    if (severity === "warn") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-100 hover:border-amber-300/50";
    }
    if (severity === "ok") {
        return "border-emerald-500/20 bg-emerald-500/10 text-emerald-100 hover:border-emerald-300/50";
    }
    return "border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-600 hover:text-slate-100";
}
function shortQuickCommandDetail(value, fallback) {
    const normalized = String(value || "").trim();
    const source = normalized || fallback;
    if (source.length <= 28)
        return source;
    return `${source.slice(0, 27)}…`;
}
function actionRiskToSeverity(action) {
    const risk = String(action?.risk_level || "")
        .trim()
        .toLowerCase();
    if (risk === "high")
        return "danger";
    if (risk === "medium")
        return "warn";
    if (risk === "low")
        return "ok";
    return "idle";
}
function findAgiCatalogAction(catalog, actionId) {
    return (catalog?.items?.find((item) => String(item.action_id || "").trim() === actionId) || null);
}
function statusRingClass(severity) {
    if (severity === "danger")
        return "border-rose-400/70 shadow-rose-500/20";
    if (severity === "warn")
        return "border-amber-300/70 shadow-amber-500/20";
    if (severity === "ok")
        return "border-emerald-300/70 shadow-emerald-500/20";
    return "border-slate-600 shadow-slate-900/30";
}
function buildConsoleId(prefix) {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}
function buildAgiActionTimeline(messages) {
    return messages
        .filter((message) => message.role === "agi" &&
        (message.receipt || message.decisionRoute || message.toolTrace))
        .slice(-4)
        .reverse()
        .map((message, index) => {
        const status = message.receipt?.status ||
            message.decisionRoute?.status ||
            message.toolTrace?.items[0]?.status ||
            "READ";
        const actionIds = uniqueStrings([
            ...(message.decisionRoute?.recommendedActionIds || []),
            ...(message.actions || []).map((action) => action.actionId),
        ]).slice(0, 4);
        return {
            id: `${message.id}:${index}`,
            title: message.receipt?.title ||
                (message.decisionRoute ? "决策路线" : "指令流"),
            status,
            summary: message.receipt?.summary ||
                message.decisionRoute?.reason ||
                message.text,
            source: message.toolTrace?.schemaVersion ||
                message.receipt?.rows.find((row) => row.label === "事实源")?.value ||
                message.decisionRoute?.schemaVersion ||
                "resident.agi_tactical_console",
            severity: actionTimelineSeverity(status),
            actionIds,
        };
    });
}
function roleTrackStateClass(state) {
    if (state === "complete") {
        return "border-emerald-500/20 bg-emerald-500/10 text-emerald-100";
    }
    if (state === "active") {
        return "border-amber-300/40 bg-amber-500/10 text-amber-100";
    }
    if (state === "blocked") {
        return "border-rose-500/25 bg-rose-500/10 text-rose-100";
    }
    return "border-slate-800 bg-slate-950/60 text-slate-400";
}
function roleTrackStatusLabel(state) {
    if (state === "complete")
        return "就绪";
    if (state === "active")
        return "进行";
    if (state === "blocked")
        return "阻断";
    return "等待";
}
function roleTrackDisplayLabel(role) {
    const normalized = role.trim().toLowerCase();
    if (normalized === "pm")
        return "项目经理";
    if (normalized === "ce")
        return "总工程师";
    if (normalized === "director")
        return "执行官";
    if (normalized === "qa")
        return "质检";
    return role;
}
function targetRolesInclude(handoff, role) {
    const aliases = {
        pm: ["pm", "project_manager"],
        ce: ["ce", "chief_engineer", "chief engineer"],
        director: ["director"],
        qa: ["qa", "quality_assurance", "quality assurance"],
    };
    const values = handoff?.target_roles || [];
    return values.some((value) => {
        const token = String(value || "")
            .trim()
            .toLowerCase()
            .replace(/[-\s]+/g, "_");
        return aliases[role].some((alias) => token === alias.replace(/\s+/g, "_"));
    });
}
function buildAgiRoleTrackItems({ runtimeActive, pendingGoalCount, approvedGoalCount, materializedGoalCount, decisionCount, handoff, evidenceGateStatus, runLedgerStatus, }) {
    const handoffStatus = String(handoff?.handoff_status || "").toLowerCase();
    const downstreamBlocked = handoffStatus === "blocked" || handoff?.downstream_allowed === false;
    const hasReadyHandoff = handoffStatus === "ready";
    const evidenceFailed = evidenceGateStatus === "fail" ||
        runLedgerStatus === "failed" ||
        runLedgerStatus === "failure";
    const evidencePassed = evidenceGateStatus === "pass" ||
        runLedgerStatus === "pass" ||
        runLedgerStatus === "success";
    const pmState = pendingGoalCount > 0
        ? {
            role: "PM",
            title: "目标待审",
            state: "active",
            detail: `${pendingGoalCount} 个目标等待治理`,
            evidence: "resident.agenda.pending_goal_ids",
        }
        : approvedGoalCount > 0 || materializedGoalCount > 0
            ? {
                role: "PM",
                title: "目标就绪",
                state: "complete",
                detail: `${approvedGoalCount + materializedGoalCount} 个目标已进入链路`,
                evidence: "resident.agenda.approved/materialized",
            }
            : {
                role: "PM",
                title: runtimeActive ? "看护中" : "待启动",
                state: runtimeActive ? "active" : "waiting",
                detail: runtimeActive ? "等待新目标或用户指令" : "Resident 未运行",
                evidence: "resident.runtime.active",
            };
    const ceState = downstreamBlocked
        ? {
            role: "CE",
            title: "交接阻断",
            state: "blocked",
            detail: "AGI handoff 不允许下游推进",
            evidence: "resident.agi_decision_handoff.downstream_allowed",
        }
        : hasReadyHandoff && targetRolesInclude(handoff, "ce")
            ? {
                role: "CE",
                title: "等待蓝图",
                state: "active",
                detail: "AGI 建议已交给受控角色链",
                evidence: "resident.agi_decision_handoff.target_roles",
            }
            : decisionCount > 0 || approvedGoalCount > 0
                ? {
                    role: "CE",
                    title: "链路保留",
                    state: "complete",
                    detail: "禁止 PM 直连 Director",
                    evidence: "platform invariant",
                }
                : {
                    role: "CE",
                    title: "等待合同",
                    state: "waiting",
                    detail: "等待 PM 目标或合同",
                    evidence: "resident.goal_governance",
                };
    const directorState = downstreamBlocked
        ? {
            role: "Director",
            title: "受控阻断",
            state: "blocked",
            detail: "AGI 不能直接调用 Director",
            evidence: "resident.agi_decision_handoff.blocked_actions",
        }
        : hasReadyHandoff && targetRolesInclude(handoff, "director")
            ? {
                role: "Director",
                title: "待受控执行",
                state: "active",
                detail: "必须经 CE 交接后执行",
                evidence: "resident.agi_decision_handoff.target_roles",
            }
            : materializedGoalCount > 0
                ? {
                    role: "Director",
                    title: "可执行",
                    state: "active",
                    detail: "已有固化目标等待执行",
                    evidence: "resident.agenda.materialized_goal_ids",
                }
                : {
                    role: "Director",
                    title: "待 CE 交接",
                    state: "waiting",
                    detail: "没有可直接执行的授权",
                    evidence: "PM → CE → Director invariant",
                };
    const qaState = evidenceFailed
        ? {
            role: "QA",
            title: "门禁失败",
            state: "blocked",
            detail: "失败证据不能被 AGI 放行",
            evidence: "resident.agi_evidence_gate/run_ledger",
        }
        : evidencePassed
            ? {
                role: "QA",
                title: "证据通过",
                state: "complete",
                detail: "可作为推进证据",
                evidence: "resident.agi_evidence_gate/run_ledger",
            }
            : decisionCount > 0 || hasReadyHandoff
                ? {
                    role: "QA",
                    title: "请求证据",
                    state: "active",
                    detail: "等待运行账本或审计结果",
                    evidence: "resident.agi_evidence_gate",
                }
                : {
                    role: "QA",
                    title: "等待验证",
                    state: "waiting",
                    detail: "尚无可验收决策",
                    evidence: "resident.decisions",
                };
    return [pmState, ceState, directorState, qaState];
}
function AgiRoleTrack({ items }) {
    return (_jsx("div", { className: "grid grid-cols-4 gap-2", "data-testid": "agi-role-track", children: items.map((item) => {
            return (_jsxs("div", { "data-testid": `agi-role-track-${item.role.toLowerCase()}`, title: `${item.evidence}: ${item.detail}`, className: cn("relative min-w-0 rounded-md border px-2 py-2 text-center transition-colors", roleTrackStateClass(item.state)), children: [_jsx("div", { className: "text-[10px] tracking-[0.08em]", children: roleTrackDisplayLabel(item.role) }), _jsx("div", { className: "mt-1 truncate text-xs font-medium", children: item.title }), _jsx("div", { className: "mt-0.5 truncate text-[10px] opacity-65", children: roleTrackStatusLabel(item.state) }), (item.state === "active" || item.state === "blocked") && (_jsx("div", { className: "mx-auto mt-1 h-0.5 w-8 rounded-full bg-amber-300/80" }))] }, item.role));
        }) }));
}
function AgiCockpitOverview({ statusLabel, statusDetail, severity, mission, nextAction, blockers, trustSignals, roleTrackItems, goalsCount, decisionsCount, evidenceCoverage, lastUpdated, onOpenAdvanced, onExplainBlocker, onRunTick, }) {
    return (_jsx(Card, { className: "overflow-hidden border-slate-800 bg-slate-950/70", "data-testid": "agi-cockpit-overview", children: _jsx(CardContent, { className: "p-0", children: _jsxs("div", { className: "grid gap-0 lg:grid-cols-[220px_minmax(0,1fr)]", children: [_jsxs("div", { className: "border-b border-slate-800 bg-slate-950/90 p-4 lg:border-b-0 lg:border-r", children: [_jsx("div", { className: cn("mx-auto flex size-28 items-center justify-center rounded-full border bg-slate-950 shadow-2xl", statusRingClass(severity)), children: _jsx(Bot, { className: "size-11 text-slate-100" }) }), _jsxs("div", { className: "mt-4 text-center", children: [_jsx("div", { className: "text-sm font-semibold text-slate-100", children: "\u9A7B\u573A AGI" }), _jsx(Badge, { className: cn("mt-2 border text-xs", severityClass(severity)), children: statusLabel }), _jsx("div", { className: "mt-2 text-xs leading-5 text-slate-500", children: statusDetail })] })] }), _jsxs("div", { className: "space-y-4 p-4", children: [_jsxs("div", { children: [_jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs uppercase tracking-[0.16em] text-slate-500", children: "\u5F53\u524D\u770B\u62A4\u4EFB\u52A1" }), _jsx("div", { className: "mt-1 text-lg font-semibold text-slate-50", children: mission })] }), _jsxs("div", { className: "hidden items-center gap-1 rounded-full border border-slate-800 bg-slate-950 px-2 py-1 text-[10px] text-slate-500 sm:flex", children: [_jsx(Radio, { className: "size-3" }), lastUpdated] })] }), _jsx("div", { className: "mt-3", children: _jsx(AgiRoleTrack, { items: roleTrackItems }) })] }), _jsxs("div", { className: "grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]", children: [_jsxs("div", { className: "rounded-lg border border-slate-800 bg-slate-950/60 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium text-slate-200", children: [_jsx(Activity, { className: "size-4 text-slate-400" }), "\u4E0B\u4E00\u6B65\u5EFA\u8BAE"] }), _jsx("p", { className: "mt-2 text-sm leading-6 text-slate-300", children: nextAction }), _jsxs("div", { className: "mt-3 flex flex-wrap gap-2", children: [_jsxs(Button, { size: "sm", className: "bg-slate-100 text-slate-950 hover:bg-white", onClick: onExplainBlocker, "data-testid": "agi-explain-blocker", children: [_jsx(Terminal, { className: "mr-1.5 size-3.5" }), "\u8BA9 AGI \u89E3\u91CA"] }), _jsxs(Button, { size: "sm", variant: "outline", className: "border-slate-700 text-slate-200 hover:bg-slate-900", onClick: onRunTick, children: [_jsx(Brain, { className: "mr-1.5 size-3.5" }), "\u53CD\u601D\u4E00\u8F6E"] }), _jsxs(Button, { size: "sm", variant: "ghost", className: "text-slate-400 hover:text-slate-100", onClick: onOpenAdvanced, children: [_jsx(Eye, { className: "mr-1.5 size-3.5" }), "\u9AD8\u7EA7\u5BA1\u8BA1"] })] })] }), _jsxs("div", { className: "rounded-lg border border-slate-800 bg-slate-950/60 p-3", children: [_jsx("div", { className: "text-xs uppercase tracking-[0.14em] text-slate-500", children: "\u8FD0\u884C\u8109\u51B2" }), _jsxs("div", { className: "mt-3 grid grid-cols-3 gap-2 text-center", children: [_jsxs("div", { children: [_jsx("div", { className: "text-lg font-semibold text-slate-100", children: goalsCount }), _jsx("div", { className: "text-[10px] text-slate-500", children: "\u76EE\u6807" })] }), _jsxs("div", { children: [_jsx("div", { className: "text-lg font-semibold text-slate-100", children: decisionsCount }), _jsx("div", { className: "text-[10px] text-slate-500", children: "\u51B3\u7B56" })] }), _jsxs("div", { children: [_jsx("div", { className: "text-lg font-semibold text-slate-100", children: evidenceCoverage }), _jsx("div", { className: "text-[10px] text-slate-500", children: "\u8BC1\u636E" })] })] })] })] }), _jsxs("div", { className: "grid gap-3 md:grid-cols-2", children: [_jsxs("div", { className: "rounded-lg border border-slate-800 bg-slate-950/60 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium text-slate-200", children: [_jsx(AlertTriangle, { className: "size-4 text-amber-300" }), "\u9700\u8981\u6CE8\u610F"] }), _jsx("div", { className: "mt-2 space-y-1.5", children: blockers.length > 0 ? (blockers.slice(0, 3).map((blocker) => (_jsx("div", { className: "rounded border border-amber-500/20 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-100", children: blocker }, blocker)))) : (_jsx("div", { className: "rounded border border-slate-800 bg-slate-950 px-2 py-1.5 text-xs text-slate-400", children: "\u5F53\u524D\u6CA1\u6709\u9700\u8981\u4EBA\u5DE5\u5904\u7406\u7684\u963B\u65AD\u9879\u3002" })) })] }), _jsxs("div", { className: "rounded-lg border border-slate-800 bg-slate-950/60 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium text-slate-200", children: [_jsx(ShieldCheck, { className: "size-4 text-slate-400" }), "\u4FE1\u4EFB\u6761"] }), _jsx("div", { className: "mt-2 grid grid-cols-2 gap-2", children: trustSignals.map((signal) => (_jsxs("div", { className: cn("rounded border px-2 py-1.5 text-xs", severityClass(signal.severity)), children: [_jsx("div", { className: "text-[10px] opacity-70", children: signal.label }), _jsx("div", { className: "mt-0.5 font-medium", children: signal.value })] }, signal.label))) })] })] })] })] }) }) }));
}
function AgiParticipationDock({ enabled, options, selectedScopes, repairAdvisoryEnabled, llmReady, llmIssue, isSaving, onEnabledChange, onToggleScope, onToggleRepairAdvisory, onSave, onOpenAdvanced, }) {
    const focusOptions = useMemo(() => selectAgiParticipationFocusOptions(options), [options]);
    const selectedCount = uniqueStrings(selectedScopes).length;
    const totalCount = Math.max(options.length, DEFAULT_AGI_PARTICIPATION_FLAGS.length);
    const participationLabel = enabled ? "允许参与" : "仅观察";
    const boundaryLabel = enabled
        ? "可研判，不可越权写入"
        : "关闭后只保留只读解释能力";
    return (_jsxs(Card, { className: "border-slate-800/80 bg-slate-950/55", "data-testid": "agi-participation-dock", children: [_jsx(CardHeader, { className: "pb-3", children: _jsxs("div", { className: "flex flex-wrap items-start justify-between gap-3", children: [_jsxs("div", { children: [_jsxs(CardTitle, { className: "flex items-center gap-2 text-sm text-slate-200", children: [_jsx(Settings, { className: "size-4 text-slate-400" }), "AGI \u53C2\u4E0E\u6743\u9650"] }), _jsx("div", { className: "mt-1 text-xs text-slate-500", children: "\u7ED1\u5B9A Resident AGI participation \u5951\u7EA6\uFF0C\u4E0D\u65B0\u589E\u7B2C\u4E8C\u5957\u6743\u9650\u6E90\u3002" })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Badge, { className: cn("border text-xs", enabled
                                        ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-200"
                                        : "border-slate-700 bg-slate-950 text-slate-500"), children: participationLabel }), _jsx(Switch, { "aria-label": "AGI \u53C2\u4E0E\u6743\u9650\u603B\u5F00\u5173", "data-testid": "agi-participation-master", checked: enabled, onCheckedChange: onEnabledChange })] })] }) }), _jsxs(CardContent, { className: "space-y-3", children: [_jsxs("div", { className: "grid gap-2 sm:grid-cols-3", children: [_jsxs("div", { className: "rounded-md border border-slate-800 bg-slate-950/70 px-3 py-2", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.12em] text-slate-500", children: "\u5DF2\u9009\u8303\u56F4" }), _jsxs("div", { className: "mt-1 text-lg font-semibold text-slate-100", children: [selectedCount, "/", totalCount] })] }), _jsxs("div", { className: "rounded-md border border-slate-800 bg-slate-950/70 px-3 py-2", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.12em] text-slate-500", children: "\u6A21\u578B\u72B6\u6001" }), _jsx("div", { className: cn("mt-1 truncate text-sm font-medium", llmReady ? "text-emerald-200" : "text-amber-200"), title: llmIssue, children: llmReady ? "已绑定" : "待确认" })] }), _jsxs("div", { className: "rounded-md border border-slate-800 bg-slate-950/70 px-3 py-2", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.12em] text-slate-500", children: "\u8FB9\u754C" }), _jsx("div", { className: "mt-1 truncate text-sm font-medium text-slate-200", children: boundaryLabel })] })] }), _jsx("div", { className: "grid gap-2 sm:grid-cols-2", children: focusOptions.map((option) => {
                            const selected = isAgiParticipationScopeSelected(option.scope, selectedScopes);
                            return (_jsxs("button", { type: "button", "aria-pressed": selected, disabled: !enabled, "data-testid": `agi-participation-quick-${agiParticipationScopeTestId(option.scope)}`, onClick: () => onToggleScope(option.scope), className: cn("min-h-24 rounded-md border px-3 py-2 text-left transition-colors", selected
                                    ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-100"
                                    : "border-slate-800 bg-slate-950/70 text-slate-300", !enabled && "cursor-not-allowed opacity-45"), children: [_jsxs("div", { className: "flex items-start justify-between gap-2", children: [_jsx("span", { className: "min-w-0 truncate text-sm font-medium", children: option.label }), _jsx("span", { className: "shrink-0 rounded border border-current/15 px-1.5 py-0.5 text-[10px] opacity-80", children: selected ? "ON" : "OFF" })] }), _jsx("div", { className: "mt-1 line-clamp-2 text-xs leading-5 opacity-75", children: describeAgiParticipationScope(option) }), (option.riskLevel || option.category) && (_jsx("div", { className: "mt-2 truncate text-[10px] opacity-60", children: [formatAgiUiToken(option.riskLevel), option.category]
                                            .filter((item) => item && item !== "暂无")
                                            .join(" · ") }))] }, option.scope));
                        }) }), _jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-800 bg-slate-950/70 px-3 py-2", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium text-slate-200", children: [_jsx(Wrench, { className: "size-4 text-slate-400" }), "Director \u4FEE\u590D\u5EFA\u8BAE"] }), _jsx("div", { className: "mt-1 text-xs text-slate-500", children: "AGI \u53EA\u53EF\u63D0\u51FA suggested_rules\uFF0C\u4E0D\u80FD\u6CE8\u518C\u89C4\u5219\u6216\u7ED5\u8FC7\u4FEE\u590D\u5185\u6838\u3002" })] }), _jsx(Switch, { "aria-label": "Director \u4FEE\u590D\u5EFA\u8BAE\u53C2\u4E0E", "data-testid": "agi-participation-repair-advisory", disabled: !enabled, checked: repairAdvisoryEnabled, onCheckedChange: onToggleRepairAdvisory })] }), _jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsx("div", { className: "text-xs text-slate-500", children: llmIssue || "保存后由 Resident AGI 角色回合与公开 Cell 契约消费。" }), _jsxs("div", { className: "flex gap-2", children: [_jsxs(Button, { size: "sm", variant: "ghost", className: "text-slate-400 hover:text-slate-100", onClick: onOpenAdvanced, children: [_jsx(Eye, { className: "mr-1 size-3.5" }), "\u9ED1\u5323\u5B50"] }), _jsx(Button, { size: "sm", className: "bg-slate-100 text-slate-950 hover:bg-white", disabled: isSaving, "data-testid": "agi-save-participation", onClick: onSave, children: "\u4FDD\u5B58\u6743\u9650" })] })] })] })] }));
}
function AgiTacticalConsole({ messages, value, disabled = false, quickCommands, pendingAction, onChange, onSubmit, onQuickCommand, onAction, onConfirmAction, onCancelAction, onOpenAdvanced, onOpenOperatorSettings, onOpenGoals, }) {
    const fallbackQuickCommands = [
        {
            label: "检查进度",
            command: "/检查进度",
            detail: "读取当前态势",
            icon: "status",
        },
        {
            label: "解释卡住",
            command: "/解释卡住",
            detail: "说明阻塞原因",
            icon: "blocker",
            severity: "warn",
        },
        {
            label: "刷新证据",
            command: "/刷新证据",
            detail: "重读事实源",
            icon: "evidence",
        },
        {
            label: "反思一轮",
            command: "/反思一轮",
            detail: "反思轮次",
            icon: "tick",
        },
    ];
    const visibleQuickCommands = quickCommands?.length
        ? quickCommands
        : fallbackQuickCommands;
    const resolveActionHandler = (action) => {
        if (action.uiHandler)
            return action.uiHandler;
        if (action.actionId === "open_evidence_black_box") {
            return "open_advanced_audit";
        }
        if (action.actionId === "refresh_evidence_interfaces") {
            return "refresh_evidence_interfaces";
        }
        if (action.actionId === "open_goals_tab") {
            return "open_goals_tab";
        }
        if (action.actionId === "open_operator_settings") {
            return "open_operator_settings";
        }
        if (action.actionId === "request_director_controlled_repair" ||
            action.actionId === "request_resident_agi_judgement") {
            return "execute_governed_action";
        }
        return "";
    };
    return (_jsxs(Card, { className: "flex min-h-[560px] flex-col border-slate-800 bg-slate-950/80", "data-testid": "agi-tactical-console", children: [_jsx(CardHeader, { className: "border-b border-slate-800 pb-3", children: _jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsxs(CardTitle, { className: "flex items-center gap-2 text-sm text-slate-200", children: [_jsx(Terminal, { className: "size-4 text-slate-400" }), "\u6218\u672F\u63A7\u5236\u53F0"] }), _jsx(Badge, { className: "border-slate-700 bg-slate-950 text-[10px] text-slate-400", children: "\u53EA\u8BFB\u4F18\u5148 \u00B7 \u53D7\u63A7\u6267\u884C" })] }) }), _jsxs(CardContent, { className: "flex flex-1 flex-col gap-3 p-3", children: [_jsx("div", { className: "min-h-0 flex-1 space-y-3 overflow-auto pr-1", children: messages.map((message) => (_jsxs("div", { className: cn("rounded-lg border p-3", message.role === "user"
                                ? "ml-8 border-slate-700 bg-slate-900/70"
                                : "mr-4 border-slate-800 bg-slate-950/70"), children: [_jsx("div", { className: "mb-1 flex items-center gap-2 text-[10px] uppercase tracking-[0.14em] text-slate-500", children: message.role === "user" ? "用户指令" : "驻场 AGI" }), _jsx("div", { className: "text-sm leading-6 text-slate-200", children: message.text }), message.missionBrief && (_jsxs("div", { className: cn("mt-3 rounded-md border bg-slate-950/60 p-3", severityClass(message.missionBrief.severity)), children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium", children: [_jsx(Activity, { className: "size-4 shrink-0" }), message.missionBrief.title] }), _jsx("div", { className: "mt-1 truncate text-xs opacity-80", children: message.missionBrief.currentFocus })] }), _jsx("span", { className: "shrink-0 rounded border border-current/20 px-2 py-1 text-[10px] font-medium", children: message.missionBrief.statusLabel })] }), _jsx("div", { className: "mt-3 h-1.5 overflow-hidden rounded-full bg-slate-900/80", children: _jsx("div", { className: "h-full rounded-full bg-current transition-[width] duration-300", style: {
                                                    width: `${message.missionBrief.progressPercent}%`,
                                                } }) }), _jsx("div", { className: "mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4", children: message.missionBrief.metrics.map((metric) => (_jsxs("div", { className: "rounded border border-current/10 bg-slate-950/40 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] opacity-60", children: metric.label }), _jsx("div", { className: "mt-0.5 truncate font-mono text-xs", children: metric.value })] }, `${metric.label}:${metric.value}`))) }), _jsxs("div", { className: "mt-3 grid gap-2 md:grid-cols-2", children: [_jsxs("div", { children: [_jsxs("div", { className: "mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-[0.12em] opacity-60", children: [_jsx(AlertTriangle, { className: "size-3" }), "\u963B\u585E"] }), _jsx("div", { className: "space-y-1 text-xs leading-5", children: message.missionBrief.blockers.length
                                                                ? message.missionBrief.blockers.map((item) => (_jsx("div", { children: item }, item)))
                                                                : "当前没有硬阻断。" })] }), _jsxs("div", { children: [_jsxs("div", { className: "mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-[0.12em] opacity-60", children: [_jsx(Target, { className: "size-3" }), "\u4E0B\u4E00\u6B65"] }), _jsx("div", { className: "space-y-1 text-xs leading-5", children: message.missionBrief.nextActions.map((item) => (_jsx("div", { children: item }, item))) })] })] }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-2 text-[10px] opacity-70", children: [_jsxs("span", { children: ["\u9636\u6BB5\uFF1A", message.missionBrief.currentStage] }), message.missionBrief.latestVerdict && (_jsxs("span", { children: ["\u7ED3\u8BBA\uFF1A", message.missionBrief.latestVerdict] }))] })] })), message.toolTrace && (_jsxs("div", { className: "mt-3 rounded-md border border-slate-800 bg-black/25 p-2", "data-testid": "agi-tool-trace", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex items-center gap-2 text-[11px] font-medium text-slate-300", children: [_jsx(Terminal, { className: "size-3.5 text-slate-500" }), "\u6307\u4EE4\u6D41"] }), _jsx("span", { className: "font-mono text-[10px] text-slate-500", children: message.toolTrace.schemaVersion })] }), _jsx("div", { className: "space-y-1.5", children: message.toolTrace.items.map((item) => (_jsxs("div", { className: "grid grid-cols-[auto_1fr] gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5 sm:grid-cols-[auto_minmax(120px,0.7fr)_1fr]", title: item.contract, children: [_jsx("span", { className: cn("h-5 rounded border px-1.5 py-0.5 font-mono text-[10px] leading-4", toolTraceStatusClass(item.status)), children: item.status }), _jsx("span", { className: "truncate text-xs text-slate-200", children: item.label }), _jsx("span", { className: "col-span-2 truncate text-[11px] text-slate-500 sm:col-span-1", children: item.summary || item.mode })] }, item.stepId))) })] })), message.participationGate && (_jsxs("div", { className: cn("mt-3 rounded-md border p-2", message.participationGate.status === "allowed"
                                        ? severityClass("ok")
                                        : severityClass("warn")), "data-testid": "agi-participation-gate", children: [_jsxs("div", { className: "flex flex-wrap items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-[11px] font-medium", children: [_jsx(ShieldCheck, { className: "size-3.5 shrink-0" }), "\u6743\u9650\u95F8\u95E8"] }), _jsx("div", { className: "mt-1 text-xs leading-5 opacity-80", children: message.participationGate.summary })] }), _jsx("span", { className: "shrink-0 rounded border border-current/20 px-1.5 py-0.5 font-mono text-[10px]", children: formatAgiUiToken(message.participationGate.status) })] }), _jsxs("div", { className: "mt-2 grid gap-2 sm:grid-cols-3", children: [_jsxs("div", { className: "rounded border border-current/15 bg-black/20 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] opacity-60", children: "\u9700\u8981\u8303\u56F4" }), _jsx("div", { className: "mt-0.5 truncate text-[11px]", children: message.participationGate.requiredScopeIds
                                                                .map((scope) => AGI_PARTICIPATION_LABELS[scope] || scope)
                                                                .join("、") || "无" })] }), _jsxs("div", { className: "rounded border border-current/15 bg-black/20 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] opacity-60", children: "\u7F3A\u5931\u8303\u56F4" }), _jsx("div", { className: "mt-0.5 truncate text-[11px]", children: message.participationGate.missingScopeIds
                                                                .map((scope) => AGI_PARTICIPATION_LABELS[scope] || scope)
                                                                .join("、") || "无" })] }), _jsxs("div", { className: "rounded border border-current/15 bg-black/20 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] opacity-60", children: "\u8BBE\u5B9A\u5165\u53E3" }), _jsx("div", { className: "mt-0.5 truncate font-mono text-[10px]", children: message.participationGate.settingsActionAvailable
                                                                ? "可打开"
                                                                : "不需要" })] })] })] })), message.decisionRoute && (_jsxs("div", { className: cn("mt-3 rounded-md border p-2", decisionRouteStatusClass(message.decisionRoute.status)), "data-testid": "agi-decision-route", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-[11px] font-medium", children: [_jsx(Brain, { className: "size-3.5 shrink-0" }), "\u51B3\u7B56\u8DEF\u7EBF"] }), _jsx("div", { className: "mt-1 truncate text-[11px] opacity-75", children: message.decisionRoute.reason })] }), _jsx("span", { className: "shrink-0 rounded border border-current/20 px-1.5 py-0.5 font-mono text-[10px]", children: message.decisionRoute.status })] }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1.5", children: [message.decisionRoute.recommendedActionIds
                                                    .slice(0, 4)
                                                    .map((actionId) => (_jsx("span", { className: "rounded border border-current/15 bg-black/20 px-1.5 py-0.5 font-mono text-[10px]", children: actionId }, actionId))), message.decisionRoute.governedActionIds.length > 0 && (_jsxs("span", { className: "rounded border border-current/15 bg-black/20 px-1.5 py-0.5 text-[10px]", children: ["\u53D7\u63A7\u52A8\u4F5C", " ", message.decisionRoute.governedActionIds.length] })), message.decisionRoute.blockedReasons.length > 0 && (_jsxs("span", { className: "rounded border border-current/15 bg-black/20 px-1.5 py-0.5 text-[10px]", children: ["\u963B\u65AD ", message.decisionRoute.blockedReasons.length] }))] })] })), message.flow && message.flow.length > 0 && (_jsx("div", { className: "mt-3 rounded-md border border-slate-800 bg-black/30 p-2 font-mono text-[10px] leading-5 text-slate-400", children: message.flow.map((line) => (_jsx("div", { children: line }, line))) })), message.receipt && (_jsxs("div", { className: "mt-3 rounded-md border border-emerald-500/20 bg-emerald-500/10 p-3", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium text-emerald-100", children: [_jsx(ClipboardCheck, { className: "size-4" }), message.receipt.title] }), _jsxs("span", { className: "rounded border border-emerald-400/30 px-1.5 py-0.5 font-mono text-[10px] text-emerald-200", children: ["[", message.receipt.status || "EXECUTED", "]"] })] }), _jsx("div", { className: "mt-1 text-xs text-emerald-100/80", children: message.receipt.summary }), _jsx("div", { className: "mt-2 grid gap-1.5 sm:grid-cols-2", children: message.receipt.rows.map((row) => (_jsxs("div", { className: "rounded border border-emerald-400/10 bg-slate-950/40 px-2 py-1", children: [_jsx("div", { className: "text-[10px] text-emerald-100/60", children: row.label }), _jsx("div", { className: "truncate font-mono text-[10px] text-emerald-50", children: row.value })] }, `${row.label}:${row.value}`))) })] })), message.actions && message.actions.length > 0 && (_jsx("div", { className: "mt-3 flex flex-wrap gap-2", children: message.actions.map((action) => (_jsxs(Button, { size: "sm", variant: "outline", className: "h-7 border-slate-700 text-xs text-slate-200 hover:bg-slate-900", title: action.contractRef
                                            ? `${action.reason} · ${action.contractRef}`
                                            : action.reason, onClick: () => {
                                            const handler = resolveActionHandler(action);
                                            if (handler === "open_advanced_audit") {
                                                onOpenAdvanced();
                                                return;
                                            }
                                            if (handler === "refresh_evidence_interfaces") {
                                                onQuickCommand("/刷新证据");
                                                return;
                                            }
                                            if (handler === "open_goals_tab") {
                                                onOpenGoals();
                                                return;
                                            }
                                            if (handler === "open_operator_settings") {
                                                onOpenOperatorSettings();
                                                return;
                                            }
                                            if (handler === "execute_governed_action") {
                                                onAction(action);
                                            }
                                        }, children: [_jsx(Sparkles, { className: "mr-1 size-3" }), action.label] }, action.actionId))) })), message.role === "agi" &&
                                    !message.actions?.some((action) => action.actionId === "open_evidence_black_box") && (_jsx("div", { className: "mt-3 flex flex-wrap gap-2", children: _jsxs(Button, { size: "sm", variant: "outline", className: "h-7 border-slate-700 text-xs text-slate-200 hover:bg-slate-900", onClick: onOpenAdvanced, children: [_jsx(Eye, { className: "mr-1 size-3" }), "\u67E5\u770B\u8BC1\u636E\u9ED1\u5323\u5B50"] }) }))] }, message.id))) }), pendingAction && (_jsxs("div", { className: cn("rounded-lg border p-3", actionRiskToSeverity(pendingAction) === "danger"
                            ? "border-amber-500/25 bg-amber-500/10 text-amber-100"
                            : "border-slate-700 bg-slate-950/70 text-slate-200"), "data-testid": "agi-action-confirmation", children: [_jsxs("div", { className: "flex flex-wrap items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium", children: [_jsx(ShieldCheck, { className: "size-4 shrink-0" }), "\u53D7\u63A7\u52A8\u4F5C\u786E\u8BA4"] }), _jsxs("div", { className: "mt-1 text-xs leading-5 opacity-80", children: [pendingAction.label, " \u5C06\u901A\u8FC7 Polaris \u516C\u5F00\u5951\u7EA6\u8FDB\u5165\u6CBB\u7406\u94FE\u8DEF\uFF0C \u4E0D\u4F1A\u7531 AGI \u76F4\u63A5\u5199\u6587\u4EF6\u3001\u6267\u884C Director \u4FEE\u590D\u6216\u653E\u884C\u5931\u8D25\u95E8\u7981\u3002"] })] }), _jsxs(Badge, { className: "border-current/20 bg-black/20 text-current", children: ["\u98CE\u9669 ", formatAgiUiToken(pendingAction.riskLevel)] })] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-4", children: [_jsxs("div", { className: "rounded border border-current/15 bg-black/20 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] opacity-60", children: "\u5951\u7EA6" }), _jsx("div", { className: "mt-0.5 truncate font-mono text-[10px]", title: pendingAction.contractRef, children: pendingAction.contractRef || "resident public contract" })] }), _jsxs("div", { className: "rounded border border-current/15 bg-black/20 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] opacity-60", children: "\u8FB9\u754C" }), _jsx("div", { className: "mt-0.5 truncate font-mono text-[10px]", title: pendingAction.executionBoundary, children: pendingAction.executionBoundary || pendingAction.mode })] }), _jsxs("div", { className: "rounded border border-current/15 bg-black/20 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] opacity-60", children: "\u89D2\u8272\u94FE" }), _jsx("div", { className: "mt-0.5 truncate font-mono text-[10px]", children: "\u9879\u76EE\u7ECF\u7406\u2192\u603B\u5DE5\u7A0B\u5E08\u2192\u6267\u884C\u5B98\u2192\u8D28\u68C0" })] }), _jsxs("div", { className: "rounded border border-current/15 bg-black/20 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] opacity-60", children: "\u53C2\u4E0E\u5F00\u5173" }), _jsx("div", { className: "mt-0.5 truncate font-mono text-[10px]", children: pendingAction.requiresParticipation ? "必需" : "不需要" })] })] }), _jsxs("div", { className: "mt-3 flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { className: "text-[11px] opacity-70", children: ["AGI \u76F4\u63A5\u6267\u884C\uFF1A", pendingAction.agiDirectExecutionAllowed ? "允许" : "已阻断"] }), _jsxs("div", { className: "flex gap-2", children: [_jsx(Button, { size: "sm", variant: "ghost", className: "h-7 text-xs text-slate-300 hover:text-white", onClick: onCancelAction, children: "\u53D6\u6D88" }), _jsx(Button, { size: "sm", className: "h-7 bg-slate-100 text-xs text-slate-950 hover:bg-white", disabled: disabled, onClick: onConfirmAction, children: "\u63D0\u4EA4\u53D7\u63A7\u52A8\u4F5C" })] })] })] })), _jsxs("div", { className: "rounded-lg border border-slate-800 bg-slate-950/70 p-2", children: [_jsx("div", { className: "mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-3", "data-testid": "agi-quick-command-bar", children: visibleQuickCommands.map((item) => (_jsxs("button", { type: "button", "aria-label": item.label, className: cn("flex min-h-9 cursor-pointer items-center gap-2 rounded border px-2 py-1.5 text-left text-[11px] transition-colors", quickCommandClass(item.severity)), title: item.detail
                                        ? `${item.detail} · ${item.command}`
                                        : item.command, onClick: () => onQuickCommand(item.command), children: [_jsx("span", { className: "grid size-5 shrink-0 place-items-center rounded border border-current/15 bg-black/20", children: quickCommandIcon(item.icon) }), _jsxs("span", { className: "min-w-0", children: [_jsx("span", { className: "block truncate font-medium", children: item.label }), item.detail && (_jsx("span", { className: "block truncate text-[10px] opacity-60", "aria-hidden": "true", children: item.detail }))] })] }, item.command))) }), _jsx("label", { htmlFor: "agi-tactical-console-input", className: "sr-only", children: "\u7ED9\u9A7B\u573A AGI \u4E0B\u8FBE\u6307\u4EE4" }), _jsxs("div", { className: "flex items-end gap-2", children: [_jsx(Textarea, { id: "agi-tactical-console-input", "aria-label": "\u7ED9\u9A7B\u573A AGI \u4E0B\u8FBE\u6307\u4EE4", value: value, onChange: (event) => onChange(event.target.value), onKeyDown: (event) => {
                                            if (event.key === "Enter" && !event.shiftKey) {
                                                event.preventDefault();
                                                onSubmit();
                                            }
                                        }, placeholder: "\u4F8B\u5982\uFF1A\u5E2E\u6211\u770B\u4E0B\u5F53\u524D\u9879\u76EE\u8FDB\u5EA6\uFF0C\u4E3A\u4EC0\u4E48\u5361\u4F4F\u4E86\uFF1F", className: "min-h-16 resize-none border-slate-800 bg-slate-950 text-sm text-slate-100 placeholder:text-slate-600" }), _jsx(Button, { size: "sm", className: "h-10 bg-slate-100 text-slate-950 hover:bg-white", disabled: disabled || !value.trim(), onClick: onSubmit, "data-testid": "agi-console-submit", children: _jsx(Send, { className: "size-4" }) })] })] })] })] }));
}
function AgiActionTimeline({ entries }) {
    return (_jsxs(Card, { className: "border-slate-800/80 bg-slate-950/60", "data-testid": "agi-action-timeline", children: [_jsx(CardHeader, { className: "pb-2", children: _jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsxs(CardTitle, { className: "flex items-center gap-2 text-sm text-slate-300", children: [_jsx(ClipboardCheck, { className: "size-4 text-slate-400" }), "\u6700\u8FD1\u884C\u52A8\u8F68\u8FF9"] }), _jsx(Badge, { className: "border-slate-700 bg-slate-950 text-[10px] text-slate-400", children: "\u56DE\u6267 / \u8DEF\u7EBF / \u6307\u4EE4\u6D41" })] }) }), _jsx(CardContent, { children: entries.length === 0 ? (_jsx("div", { className: "rounded-md border border-slate-800 bg-slate-950/70 px-3 py-3 text-sm text-slate-500", children: "\u7B49\u5F85\u7528\u6237\u6307\u4EE4\u3002\u884C\u52A8\u8F68\u8FF9\u53EA\u5C55\u793A\u5E38\u9A7B AGI \u5951\u7EA6\u8FD4\u56DE\u7684\u56DE\u6267\u3001\u51B3\u7B56\u8DEF\u7EBF\u548C\u6307\u4EE4\u6D41\u3002" })) : (_jsx("div", { className: "grid gap-2 md:grid-cols-2 xl:grid-cols-4", children: entries.map((entry) => (_jsxs("div", { className: cn("min-h-32 rounded-md border p-3", severityClass(entry.severity)), "data-testid": "agi-action-timeline-entry", children: [_jsxs("div", { className: "flex items-start justify-between gap-2", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-sm font-medium", children: entry.title }), _jsx("div", { className: "mt-1 truncate font-mono text-[10px] opacity-60", children: entry.source })] }), _jsx("span", { className: "shrink-0 rounded border border-current/20 px-1.5 py-0.5 font-mono text-[10px]", children: formatAgiUiToken(entry.status) })] }), _jsx("div", { className: "mt-2 line-clamp-2 text-xs leading-5 opacity-80", children: entry.summary }), entry.actionIds.length > 0 && (_jsx("div", { className: "mt-3 flex flex-wrap gap-1", children: entry.actionIds.map((actionId) => (_jsx("span", { className: "rounded border border-current/15 bg-black/20 px-1.5 py-0.5 font-mono text-[10px]", children: actionId }, actionId))) }))] }, entry.id))) })) })] }));
}
export function ResidentWorkspace({ workspace, onBackToMain, residentSnapshot = null, initialTab = "overview", residentAgiLlmStatus = null, }) {
    const resident = useResident({ workspace, liveResident: residentSnapshot });
    const [activeTab, setActiveTab] = useState(initialTab);
    const [showNewGoal, setShowNewGoal] = useState(initialTab === "goals");
    const [expandedGoal, setExpandedGoal] = useState(null);
    // New goal form state
    const [newGoalTitle, setNewGoalTitle] = useState("");
    const [newGoalDesc, setNewGoalDesc] = useState("");
    const [agiDecisionObjective, setAgiDecisionObjective] = useState("审计当前运行证据，判断是否允许进入下一步。");
    const [agiDecisionType, setAgiDecisionType] = useState("evidence.interface.selection");
    // Identity edit state
    const [editingIdentity, setEditingIdentity] = useState(false);
    const [identityName, setIdentityName] = useState("");
    const [identityMission, setIdentityMission] = useState("");
    const [agiParticipationEnabled, setAgiParticipationEnabled] = useState(false);
    const [agiParticipationScopes, setAgiParticipationScopes] = useState([]);
    const [advancedAuditOpen, setAdvancedAuditOpen] = useState(false);
    const [operatorSettingsOpen, setOperatorSettingsOpen] = useState(false);
    const [consoleInput, setConsoleInput] = useState("");
    const [pendingConsoleAction, setPendingConsoleAction] = useState(null);
    const [consoleMessages, setConsoleMessages] = useState([
        {
            id: "agi-console-boot",
            role: "agi",
            text: "战术控制台已连接当前 Polaris 工作区。我会优先读取平台事实源，再给出建议；涉及写入、命令或修复的动作仍会进入项目经理 → 总工程师 → 执行官 → 质检的受控链路。",
            flow: [
                "[连接] runtime.v2 状态投影已挂载",
                "[边界] 常驻 AGI 只提供建议或受控入口",
                "[事实源] 上下文、运行账本、执行回执优先",
            ],
        },
    ]);
    const agiActionTimelineEntries = useMemo(() => buildAgiActionTimeline(consoleMessages), [consoleMessages]);
    const isActive = Boolean(resident.residentRuntime?.active);
    const mode = resident.residentRuntime?.mode || "observe";
    const runtimeEvidence = resident.residentRuntimeEvidence;
    const residentAgiParticipation = resident.residentIdentity?.resident_agi_participation || null;
    const residentAgiParticipationEnabled = Boolean(residentAgiParticipation?.enabled);
    const residentAgiLlmProvider = String(residentAgiLlmStatus?.providerName ||
        residentAgiLlmStatus?.providerId ||
        "").trim();
    const residentAgiLlmModel = String(residentAgiLlmStatus?.model || "").trim();
    const residentAgiLlmBound = Boolean(residentAgiLlmProvider && residentAgiLlmModel);
    const residentAgiLlmBlocked = Boolean(residentAgiLlmStatus?.blocked || residentAgiLlmStatus?.unsupported);
    const residentAgiLlmReady = Boolean(residentAgiLlmStatus?.ready &&
        residentAgiLlmBound &&
        !residentAgiLlmBlocked);
    const residentAgiLlmRiskVisible = residentAgiParticipationEnabled &&
        (!residentAgiLlmBound || residentAgiLlmBlocked);
    // Current focus - simplified
    const currentFocus = resident.residentAgenda?.current_focus?.[0] || null;
    const latestInsight = resident.residentInsights?.[0] || null;
    const capabilities = resident.residentCapabilityGraph?.capabilities || [];
    const agiCapabilitySurface = resident.residentAgiCapabilitySurface;
    const agiAuditPack = resident.residentAgiAuditPack;
    const agiEvidenceInterfaces = resident.residentAgiEvidenceInterfaces;
    const agiHandoffs = resident.residentAgiHandoffs;
    const agiActionCatalog = resident.residentAgiActionCatalog || null;
    const agiAuthorityMatrix = agiAuditPack?.authority_matrix || agiCapabilitySurface?.authority_matrix;
    const agiDecisionProfile = agiAuditPack?.decision_profile;
    const tickAutonomyBoundary = resident.residentRuntime?.last_summary?.autonomy_boundary ||
        agiAuditPack?.autonomy_boundary ||
        null;
    const agiCapabilities = agiCapabilitySurface?.items || [];
    const agiDecisionCapabilities = agiCapabilitySurface?.decision_capabilities || [];
    const hardcodedRepairCatalog = agiCapabilitySurface?.hardcoded_repair_strategy_catalog || null;
    const repairAdvisoryPolicy = agiCapabilitySurface?.director_repair_advisory_policy || null;
    const agiDecisionCapabilityRegistry = agiCapabilitySurface?.decision_capability_registry ||
        agiDecisionProfile?.decision_capability_registry;
    const agiCapabilityAccessRegistry = agiCapabilitySurface?.capability_access_registry || null;
    const agiEvidenceInterfaceContract = agiCapabilitySurface?.evidence_interface_contract;
    const agiDecisionBoundaries = agiCapabilitySurface?.decision_boundaries || [];
    const agiDecisionBoundaryPolicy = agiCapabilitySurface?.decision_boundary_policy || null;
    const agiParticipationPolicy = resident.status?.agi_participation_policy ||
        agiCapabilitySurface?.participation_policy ||
        null;
    const lastAgiDecisionHandoff = resident.lastAgiDecisionResult?.decision_handoff || null;
    const lastRepairAdvisoryOverlay = resident.lastAgiDecisionResult?.repair_advisory_overlay || null;
    const queriedRepairAdvisoryOverlay = resident.residentAgiRepairAdvisoryOverlay?.overlay || null;
    const queriedRepairAdvisoryOverlaySource = resident.residentAgiRepairAdvisoryOverlay?.found &&
        resident.residentAgiRepairAdvisoryOverlay?.decision_ref?.decision_id
        ? `public_query:${shortDecisionId(resident.residentAgiRepairAdvisoryOverlay.decision_ref.decision_id)}`
        : resident.residentAgiRepairAdvisoryOverlay?.found
            ? "public_query"
            : "";
    const auditPackRepairAdvisoryOverlay = agiAuditPack?.repair_advisory_overlay_query?.overlay ||
        agiAuditPack?.latest_repair_advisory_overlay ||
        null;
    const auditPackRepairAdvisoryOverlaySource = agiAuditPack?.repair_advisory_overlay_query?.found &&
        agiAuditPack.repair_advisory_overlay_query.decision_ref?.decision_id
        ? `audit_pack_query:${shortDecisionId(agiAuditPack.repair_advisory_overlay_query.decision_ref.decision_id)}`
        : auditPackRepairAdvisoryOverlay
            ? "audit_pack"
            : "";
    const persistedRepairAdvisoryOverlay = useMemo(() => latestDecisionRepairAdvisoryOverlay(resident.decisions), [resident.decisions]);
    const activeRepairAdvisoryOverlay = lastRepairAdvisoryOverlay ||
        queriedRepairAdvisoryOverlay ||
        auditPackRepairAdvisoryOverlay ||
        persistedRepairAdvisoryOverlay?.overlay ||
        null;
    const activeRepairAdvisoryOverlaySource = lastRepairAdvisoryOverlay
        ? "runtime decision result"
        : queriedRepairAdvisoryOverlay
            ? queriedRepairAdvisoryOverlaySource
            : auditPackRepairAdvisoryOverlay
                ? auditPackRepairAdvisoryOverlaySource
                : persistedRepairAdvisoryOverlay?.source || "";
    const agiParticipationOptions = useMemo(() => {
        const dynamicOptions = agiParticipationPolicy?.available_scopes
            ?.map((scope) => {
            const scopeId = String(scope.scope_id || "").trim();
            if (!scopeId)
                return null;
            return {
                scope: scopeId,
                label: String(scope.name || "").trim() ||
                    AGI_PARTICIPATION_LABELS[scopeId] ||
                    scopeId,
                category: String(scope.category || "").trim() || undefined,
                riskLevel: String(scope.risk_level || "").trim() || undefined,
            };
        })
            .filter((scope) => scope !== null) ||
            [];
        const flags = agiParticipationPolicy?.participation_flags?.filter(Boolean) ||
            DEFAULT_AGI_PARTICIPATION_FLAGS;
        const flagOptions = flags.map((scope) => ({
            scope,
            label: AGI_PARTICIPATION_LABELS[scope] || scope,
        }));
        const seen = new Set();
        return [...dynamicOptions, ...flagOptions].filter((option) => {
            const key = normalizeAgiParticipationScope(option.scope);
            if (!key || seen.has(key))
                return false;
            seen.add(key);
            return true;
        });
    }, [agiParticipationPolicy]);
    useEffect(() => {
        setAgiParticipationEnabled(Boolean(residentAgiParticipation?.enabled));
        setAgiParticipationScopes(selectedAgiParticipationScopes(residentAgiParticipation));
    }, [residentAgiParticipation]);
    const agiDecisionTypeOptions = useMemo(() => {
        const options = agiDecisionCapabilities
            .map((capability) => {
            const decisionId = String(capability.decision_id || "").trim();
            if (!decisionId)
                return null;
            return {
                decisionId,
                label: String(capability.name || decisionId).trim(),
                owner: String(capability.owner || "").trim(),
                riskLevel: String(capability.risk_level || "").trim(),
            };
        })
            .filter((option) => option !== null);
        return options.length
            ? options
            : [
                {
                    decisionId: "platform_supervision",
                    label: "平台监督",
                    owner: "resident_agi",
                    riskLevel: "medium",
                },
            ];
    }, [agiDecisionCapabilities]);
    useEffect(() => {
        if (agiDecisionTypeOptions.length === 0)
            return;
        if (agiDecisionTypeOptions.some((option) => option.decisionId === agiDecisionType)) {
            return;
        }
        setAgiDecisionType(agiDecisionTypeOptions[0].decisionId);
    }, [agiDecisionType, agiDecisionTypeOptions]);
    const selectedAgiDecisionCapability = agiDecisionCapabilities.find((capability) => capability.decision_id === agiDecisionType) || null;
    const identityParticipationScopes = selectedAgiParticipationScopes(residentAgiParticipation);
    const decisionStats = useMemo(() => buildDecisionStats(resident.decisions), [resident.decisions]);
    const capabilityGovernance = useMemo(() => buildCapabilityGovernanceStats(agiCapabilities), [agiCapabilities]);
    const totalGoals = resident.goals.length;
    const agiRepairAdvisoryParticipationEnabled = agiParticipationEnabled &&
        AGI_REPAIR_ADVISORY_SCOPE_IDS.some((scope) => isAgiParticipationScopeSelected(scope, agiParticipationScopes));
    const hardRuleGateStatus = String(agiAuditPack?.hard_rule_gate?.status || "unknown").toLowerCase();
    const evidenceGateStatus = String(agiAuditPack?.evidence_gate?.status || "unknown").toLowerCase();
    const runLedgerStatus = String(agiAuditPack?.run_ledger_summary?.status || "unknown").toLowerCase();
    const evidenceMatrixSummary = agiEvidenceInterfaces?.capability_matrix?.summary || null;
    const requiredEvidenceTotal = Number(evidenceMatrixSummary?.required || 0);
    const requiredEvidenceAvailable = Number(evidenceMatrixSummary?.required_available || 0);
    const runtimeEvidenceTotal = Number(agiEvidenceInterfaces?.summary?.total || 0);
    const runtimeEvidenceAvailable = Number(agiEvidenceInterfaces?.summary?.available || 0);
    const missingRequiredEvidence = Number(evidenceMatrixSummary?.missing_required ||
        agiEvidenceInterfaces?.summary?.missing_required_interface_ids?.length ||
        0);
    const cockpitEvidenceCoverage = requiredEvidenceTotal > 0
        ? `${requiredEvidenceAvailable}/${requiredEvidenceTotal}`
        : runtimeEvidenceTotal > 0
            ? `${runtimeEvidenceAvailable}/${runtimeEvidenceTotal}`
            : "暂无";
    const roleTrackItems = useMemo(() => buildAgiRoleTrackItems({
        runtimeActive: isActive,
        pendingGoalCount: resident.residentAgenda?.pending_goal_ids?.length ?? 0,
        approvedGoalCount: resident.residentAgenda?.approved_goal_ids?.length ?? 0,
        materializedGoalCount: resident.residentAgenda?.materialized_goal_ids?.length ?? 0,
        decisionCount: resident.decisions.length,
        handoff: lastAgiDecisionHandoff,
        evidenceGateStatus,
        runLedgerStatus,
    }), [
        evidenceGateStatus,
        isActive,
        lastAgiDecisionHandoff,
        resident.decisions.length,
        resident.residentAgenda?.approved_goal_ids?.length,
        resident.residentAgenda?.materialized_goal_ids?.length,
        resident.residentAgenda?.pending_goal_ids?.length,
        runLedgerStatus,
    ]);
    const agiBlockers = uniqueStrings([
        residentAgiLlmRiskVisible
            ? residentAgiLlmStatus?.readinessIssue ||
                residentAgiLlmStatus?.runtimeIssue ||
                "常驻 AGI 参与已开启，但模型绑定不可用。"
            : "",
        hardRuleGateStatus === "fail" ? "平台硬规则门禁失败，AGI 不能放行。" : "",
        evidenceGateStatus === "fail"
            ? agiAuditPack?.evidence_gate?.reason ||
                "证据门禁失败，必须先处理失败证据。"
            : "",
        evidenceGateStatus === "hold"
            ? agiAuditPack?.evidence_gate?.reason ||
                "证据门禁暂缓，必须先补齐必要证据。"
            : "",
        evidenceGateStatus === "missing" ? "缺少必要证据，不能标记完成。" : "",
        missingRequiredEvidence > 0
            ? `${missingRequiredEvidence} 个必需证据接口尚未满足。`
            : "",
        (agiEvidenceInterfaces?.summary?.unavailable ?? 0) > 0
            ? `${agiEvidenceInterfaces?.summary?.unavailable ?? 0} 个证据接口不可用。`
            : "",
    ]);
    const cockpitSeverity = !isActive
        ? "idle"
        : hardRuleGateStatus === "fail" || evidenceGateStatus === "fail"
            ? "danger"
            : residentAgiLlmRiskVisible || agiBlockers.length > 0
                ? "warn"
                : "ok";
    const cockpitStatusLabel = !isActive
        ? "已离线"
        : cockpitSeverity === "danger"
            ? "不能放行"
            : cockpitSeverity === "warn"
                ? "受限值守"
                : "正在值守";
    const cockpitStatusDetail = !isActive
        ? "Resident 未运行；可启动后进入观察或审计。"
        : residentAgiParticipationEnabled
            ? "已接入平台事实源，遵守角色链路与受控执行边界。"
            : "当前以普通 Resident 方式运行，AGI 自动参与未开启。";
    const cockpitMission = currentFocus || resident.goals[0]?.title || "等待新的平台看护任务";
    const cockpitNextAction = cockpitSeverity === "danger"
        ? "先查看失败证据并交给受控角色链处理，AGI 不能把失败门禁标记为通过。"
        : cockpitSeverity === "warn"
            ? "先补齐模型绑定、证据接口或运行态证据，再允许 AGI 给出推进建议。"
            : "当前可以继续值守；如需深入排查，可让 AGI 解释当前状态或刷新证据。";
    const trustSignals = [
        {
            label: "角色链路",
            value: "项目经理→总工程师→执行官→质检",
            severity: "ok",
        },
        {
            label: "证据门禁",
            value: formatAgiUiToken(evidenceGateStatus),
            severity: evidenceGateStatus === "fail"
                ? "danger"
                : evidenceGateStatus === "pass"
                    ? "ok"
                    : "warn",
        },
        {
            label: "任务账本",
            value: formatAgiUiToken(runLedgerStatus),
            severity: runLedgerStatus === "failed"
                ? "danger"
                : runLedgerStatus === "pass" || runLedgerStatus === "success"
                    ? "ok"
                    : "warn",
        },
        {
            label: "直接写入",
            value: "已阻断",
            severity: "ok",
        },
    ];
    const agiTacticalQuickCommands = useMemo(() => {
        const commands = [];
        const seen = new Set();
        const catalogCommand = (actionId, fallback) => {
            const action = findAgiCatalogAction(agiActionCatalog, actionId);
            if (!action)
                return fallback;
            return {
                ...fallback,
                label: String(action.label || "").trim() || fallback.label,
                detail: shortQuickCommandDetail(String(action.reason || "").trim(), fallback.detail || ""),
                severity: fallback.severity === "danger"
                    ? "danger"
                    : actionRiskToSeverity(action),
            };
        };
        const pushCommand = (command) => {
            const key = `${command.label}:${command.command}`;
            if (seen.has(key))
                return;
            seen.add(key);
            commands.push(command);
        };
        pushCommand({
            label: "检查进度",
            command: "/检查进度",
            detail: "读取项目态势",
            icon: "status",
            severity: cockpitSeverity,
        });
        if (agiBlockers.length > 0) {
            pushCommand({
                label: "解释卡住",
                command: "/解释卡住",
                detail: "说明阻塞原因",
                icon: "blocker",
                severity: cockpitSeverity === "danger" ? "danger" : "warn",
            });
        }
        pushCommand(catalogCommand("refresh_evidence_interfaces", {
            label: "刷新证据",
            command: "/刷新证据",
            detail: "重读事实源",
            icon: "evidence",
            severity: evidenceGateStatus === "fail" ? "danger" : "idle",
        }));
        if (residentAgiLlmRiskVisible) {
            pushCommand({
                label: "检查 AGI 模型",
                command: "请检查 Resident AGI 的模型绑定、参与开关和当前阻塞。",
                detail: "先修复绑定",
                icon: "model",
                severity: "warn",
            });
        }
        else if (residentAgiParticipationEnabled) {
            pushCommand(catalogCommand("request_resident_agi_judgement", {
                label: "请求 AGI 判断",
                command: "请让 AGI 基于当前证据判断下一步怎么办。",
                detail: "角色回合",
                icon: "judgement",
                severity: "ok",
            }));
        }
        if (agiBlockers.length > 0 && residentAgiParticipationEnabled) {
            pushCommand(catalogCommand("request_director_controlled_repair", {
                label: "交给修复链",
                command: "交给 Director 受控修复这个阻塞。",
                detail: "治理目标",
                icon: "repair",
                severity: "danger",
            }));
        }
        else {
            pushCommand({
                label: "反思一轮",
                command: "/反思一轮",
                detail: "反思轮次",
                icon: "tick",
                severity: "idle",
            });
        }
        return commands.slice(0, 5);
    }, [
        agiBlockers,
        agiActionCatalog,
        cockpitSeverity,
        evidenceGateStatus,
        residentAgiLlmRiskVisible,
        residentAgiParticipationEnabled,
    ]);
    const toggleAgiParticipationScope = (scope) => {
        const scopeKey = normalizeAgiParticipationScope(scope);
        setAgiParticipationScopes((current) => current.some((item) => normalizeAgiParticipationScope(item) === scopeKey)
            ? current.filter((item) => normalizeAgiParticipationScope(item) !== scopeKey)
            : [...current, scope]);
    };
    const setAgiRepairAdvisoryParticipation = (enabled) => {
        setAgiParticipationScopes((current) => {
            const selectedKeys = new Set(current.map((item) => normalizeAgiParticipationScope(item)));
            if (enabled) {
                const next = [...current];
                for (const scope of AGI_REPAIR_ADVISORY_SCOPE_IDS) {
                    const key = normalizeAgiParticipationScope(scope);
                    if (!selectedKeys.has(key)) {
                        next.push(scope);
                        selectedKeys.add(key);
                    }
                }
                return next;
            }
            return current.filter((scope) => !AGI_REPAIR_ADVISORY_SCOPE_IDS.some((repairScope) => normalizeAgiParticipationScope(repairScope) ===
                normalizeAgiParticipationScope(scope)));
        });
    };
    const handleSaveAgiParticipation = async () => {
        await resident.saveIdentity({
            resident_agi_participation: {
                enabled: agiParticipationEnabled,
                scopes: agiParticipationScopes,
                participation: buildAgiParticipationFlags(agiParticipationScopes, agiParticipationOptions.map((option) => option.scope)),
                custom_scopes_allowed: resident.residentIdentity?.resident_agi_participation
                    ?.custom_scopes_allowed ?? true,
            },
        });
    };
    const handleCreateGoal = async () => {
        if (!newGoalTitle.trim())
            return;
        const created = await resident.createGoal({
            title: newGoalTitle.trim(),
            goal_type: "maintenance",
            motivation: newGoalDesc.trim(),
            source: "manual",
            scope: [],
            evidence_refs: [],
        });
        if (created) {
            setNewGoalTitle("");
            setNewGoalDesc("");
            setShowNewGoal(false);
        }
    };
    const handleRunAgiDecision = async () => {
        const objective = agiDecisionObjective.trim();
        if (!objective)
            return;
        const latestDecision = resident.decisions[0] || null;
        const candidateActions = uniqueStrings([
            ...(selectedAgiDecisionCapability?.candidate_actions || []),
            ...(agiDecisionProfile?.candidate_actions || []),
            "continue",
            "block",
            "request_evidence",
            "escalate",
        ]);
        const constraints = uniqueStrings([
            "preserve_pm_chief_engineer_director_qa_chain",
            "request_evidence_or_block_when_context_is_insufficient",
            ...(selectedAgiDecisionCapability?.hard_constraints || []),
            ...(agiDecisionProfile?.required_constraints || []),
        ]);
        await resident.runAgiDecision({
            decision_type: agiDecisionType,
            objective,
            evidence: {
                workspace,
                runtime_active: isActive,
                mode,
                goal_count: resident.goals.length,
                decision_count: resident.decisions.length,
                latest_decision_id: latestDecision?.decision_id || "",
                latest_verdict: latestDecision?.verdict || "",
                resident_agi_audit_pack_loaded: Boolean(agiAuditPack),
                resident_agi_audit_pack_schema: agiAuditPack?.schema_version || "",
                resident_agi_available: Boolean(agiAuditPack?.role_registry?.resident_agi_available),
                resident_agi_hard_rule_gate_status: agiAuditPack?.hard_rule_gate?.status || "",
                resident_agi_evidence_gate_status: agiAuditPack?.evidence_gate?.status || "",
                resident_agi_evidence_gate_recommended_verdict: agiAuditPack?.evidence_gate?.recommended_verdict || "",
                resident_agi_authority_matrix_schema: agiAuthorityMatrix?.schema_version || "",
                resident_agi_chain_required: Boolean(agiAuthorityMatrix?.chain_required),
                resident_agi_decision_profile_schema: agiDecisionProfile?.schema_version || "",
                resident_agi_decision_profile_recommended_verdict: agiDecisionProfile?.recommended_verdict || "",
                resident_agi_decision_profile_next_action: agiDecisionProfile?.recommended_next_action || "",
                resident_agi_role_turn_allowed: Boolean(agiDecisionProfile?.role_turn_allowed),
                resident_agi_downstream_precheck: agiDecisionProfile?.downstream_precheck || "",
                selected_decision_capability_id: selectedAgiDecisionCapability?.decision_id || agiDecisionType,
                selected_decision_capability_name: selectedAgiDecisionCapability?.name || "",
                selected_decision_capability_owner: selectedAgiDecisionCapability?.owner || "",
                selected_decision_capability_risk: selectedAgiDecisionCapability?.risk_level || "",
                selected_decision_required_evidence_interfaces: selectedAgiDecisionCapability?.required_evidence_interfaces || [],
                selected_decision_optional_evidence_interfaces: selectedAgiDecisionCapability?.optional_evidence_interfaces || [],
            },
            constraints,
            candidate_actions: candidateActions,
            context_refs: latestDecision?.context_refs || [],
            evidence_refs: latestDecision?.evidence_refs || [],
            confidence: latestDecision ? 0.7 : 0.5,
            include_audit_pack: true,
            audit_pack_decision_limit: 12,
        });
    };
    const appendConsoleMessage = (message) => {
        setConsoleMessages((current) => [
            ...current,
            { ...message, id: buildConsoleId(message.role) },
        ]);
    };
    const toConsoleReceipt = (response) => {
        const receipt = response.receipt;
        if (!receipt)
            return undefined;
        const rows = (receipt.rows || [])
            .map((row) => ({
            label: String(row.label || "").trim(),
            value: String(row.value || "").trim(),
        }))
            .filter((row) => row.label && row.value);
        return {
            title: receipt.title || "战术问答凭证",
            summary: receipt.summary || "已读取 Polaris 平台事实源。",
            status: receipt.status || "READ",
            rows,
        };
    };
    const toConsoleMissionBrief = (brief) => {
        if (!brief)
            return undefined;
        const severityRaw = String(brief.severity || "").trim();
        const severity = severityRaw === "ok" ||
            severityRaw === "warn" ||
            severityRaw === "danger" ||
            severityRaw === "idle"
            ? severityRaw
            : "idle";
        const metrics = (brief.metrics || [])
            .map((item) => ({
            label: String(item.label || "").trim(),
            value: String(item.value || "").trim(),
        }))
            .filter((item) => item.label && item.value)
            .slice(0, 4);
        return {
            title: String(brief.title || "项目态势").trim(),
            severity,
            statusLabel: String(brief.status_label || "未知").trim(),
            progressPercent: Math.max(0, Math.min(100, Number(brief.progress_percent || 0))),
            currentFocus: String(brief.current_focus || "等待任务").trim(),
            currentStage: String(brief.current_stage || "observe").trim(),
            latestVerdict: String(brief.latest_verdict || "").trim(),
            blockers: (brief.blockers || [])
                .map((item) => String(item || "").trim())
                .filter(Boolean)
                .slice(0, 3),
            nextActions: (brief.next_actions || [])
                .map((item) => String(item || "").trim())
                .filter(Boolean)
                .slice(0, 4),
            metrics,
        };
    };
    const toConsoleToolTrace = (trace) => {
        if (!trace)
            return undefined;
        const items = (trace.items || [])
            .map((item) => ({
            stepId: String(item.step_id || "").trim(),
            label: String(item.label || "").trim(),
            mode: String(item.mode || "").trim(),
            status: String(item.status || "unknown").trim(),
            contract: String(item.contract || "").trim(),
            summary: String(item.summary || "").trim(),
        }))
            .filter((item) => item.stepId && item.label)
            .slice(0, 6);
        if (items.length === 0)
            return undefined;
        return {
            schemaVersion: String(trace.schema_version || "resident.agi_tactical_tool_trace.v1"),
            items,
        };
    };
    const toConsoleDecisionRoute = (route) => {
        if (!route)
            return undefined;
        const status = String(route.route_status || "").trim();
        if (!status)
            return undefined;
        return {
            schemaVersion: String(route.schema_version || "resident.agi_tactical_decision_route.v1"),
            status,
            reason: String(route.route_reason || "").trim() || "已生成决策路线。",
            recommendedActionIds: (route.recommended_action_ids || [])
                .map((item) => String(item || "").trim())
                .filter(Boolean),
            governedActionIds: (route.governed_action_ids || [])
                .map((item) => String(item || "").trim())
                .filter(Boolean),
            blockedReasons: (route.blocked_reasons || [])
                .map((item) => String(item || "").trim())
                .filter(Boolean),
        };
    };
    const toConsoleParticipationGate = (gate) => {
        if (!gate)
            return undefined;
        const status = String(gate.status || "").trim();
        if (!status)
            return undefined;
        return {
            schemaVersion: String(gate.schema_version || "resident.agi_tactical_participation_gate.v1"),
            status,
            summary: String(gate.summary || gate.reason || "").trim(),
            requiredScopeIds: (gate.required_scope_ids || [])
                .map((item) => String(item || "").trim())
                .filter(Boolean),
            configuredScopeIds: (gate.configured_scope_ids || [])
                .map((item) => String(item || "").trim())
                .filter(Boolean),
            missingScopeIds: (gate.missing_scope_ids || [])
                .map((item) => String(item || "").trim())
                .filter(Boolean),
            settingsActionAvailable: Boolean(gate.settings_action_available),
            governedActionsAvailable: Boolean(gate.governed_actions_available),
            directPermissionChangeAllowed: Boolean(gate.agi_direct_permission_change_allowed),
        };
    };
    const toConsoleActions = (actions, sourceMessage) => (actions || [])
        .map((action) => ({
        actionId: String(action.action_id || "").trim(),
        label: String(action.label || "").trim(),
        status: String(action.status || "").trim(),
        mode: String(action.mode || "").trim(),
        reason: String(action.reason || "").trim(),
        uiHandler: String(action.ui_handler || "").trim(),
        capabilityId: String(action.capability_id || "").trim(),
        contractRef: String(action.contract_ref || "").trim(),
        riskLevel: String(action.risk_level || "").trim(),
        executionBoundary: String(action.execution_boundary || "").trim(),
        requiresParticipation: Boolean(action.requires_participation),
        agiDirectExecutionAllowed: Boolean(action.agi_direct_execution_allowed),
        sourceMessage,
        goalDraft: action.goal_draft,
    }))
        .filter((action) => action.actionId && action.label);
    const consoleStatusAnswer = () => {
        const blockerText = agiBlockers.length
            ? `当前我看到 ${agiBlockers.length} 个需要注意的问题：${agiBlockers
                .slice(0, 2)
                .join("；")}。`
            : "当前没有发现需要人工处理的阻断项。";
        return `我已读取当前 Polaris 元项目投影。${cockpitStatusLabel}：${cockpitMission}。目标 ${totalGoals} 个，决策 ${decisionStats.total} 条，证据覆盖 ${decisionStats.evidenceBacked}/${decisionStats.total}。${blockerText}`;
    };
    const handleConsoleCommand = async (rawCommand) => {
        const command = String(rawCommand ?? consoleInput).trim();
        if (!command)
            return;
        setConsoleInput("");
        setPendingConsoleAction(null);
        appendConsoleMessage({ role: "user", text: command });
        const normalized = command.toLowerCase();
        const flow = [
            "[授权] 校验当前 workspace 绑定... 通过",
            "[事实源] 读取 Resident runtime / audit pack / evidence projection",
            "[边界] 高风险写入与门禁放行仍交由角色链处理",
        ];
        if (normalized.includes("刷新证据") ||
            normalized.includes("evidence") ||
            normalized.includes("证据接口")) {
            await resident.refreshAgiEvidenceInterfaces(agiDecisionType);
            appendConsoleMessage({
                role: "agi",
                text: "我已通过 Resident 公共接口刷新当前决策的证据接口投影。这不是绕过门禁，只是重新读取事实源，刷新后仍需要角色链和 QA 证据确认。",
                flow: [
                    ...flow,
                    `[调用] resident.refreshAgiEvidenceInterfaces(${agiDecisionType})`,
                    "[结果] 证据接口刷新请求已完成",
                ],
                receipt: {
                    title: "证据刷新凭证",
                    summary: "已触发 AGI evidence interface read model 刷新。",
                    rows: [
                        { label: "决策类型", value: agiDecisionType },
                        { label: "执行边界", value: "read_only_public_contract" },
                        { label: "事实源", value: "resident.agi_evidence_interfaces.v1" },
                        { label: "AGI 写入", value: "blocked" },
                    ],
                },
            });
            return;
        }
        if (normalized.includes("反思") || normalized.includes("tick")) {
            await resident.tick();
            appendConsoleMessage({
                role: "agi",
                text: "我已触发 Resident 反思一轮。这个动作只会让 Resident 生成或刷新自身元认知/目标候选，代码写入和修复仍不会由 AGI 直接执行。",
                flow: [...flow, "[调用] resident.tick()", "[结果] 反思轮次已提交"],
                receipt: {
                    title: "反思轮次凭证",
                    summary: "已请求 Resident 执行一次受控 tick。",
                    rows: [
                        { label: "动作", value: "resident.tick" },
                        { label: "实时投影", value: "runtime.v2.status.resident" },
                        { label: "写入权限", value: "resident_contract_only" },
                        { label: "角色链", value: "项目经理→总工程师→执行官→质检已保持" },
                    ],
                },
            });
            return;
        }
        const latestDecision = resident.decisions[0] || null;
        const chatResponse = await resident.chatAgi({
            message: command,
            decision_type: agiDecisionType,
            run_id: latestDecision?.run_id || "",
            task_id: latestDecision?.task_id || "",
            goal_id: latestDecision?.goal_id || "",
            context: {
                cockpit_status: cockpitStatusLabel,
                cockpit_mission: cockpitMission,
                blockers: agiBlockers,
                goal_count: resident.goals.length,
                decision_count: resident.decisions.length,
            },
            context_refs: latestDecision?.context_refs || [],
            evidence_refs: latestDecision?.evidence_refs || [],
            decision_limit: 12,
            max_runs: 20,
        });
        if (!chatResponse) {
            appendConsoleMessage({
                role: "agi",
                text: "我没有拿到 Resident AGI chat 契约返回，因此不会用前端本地摘要冒充平台判断。请先确认后端 `/v2/resident/agi/chat` 可用，再继续让 AGI 判断或操作。",
                flow: [...flow, "[停止] 未取得 Resident public contract 响应"],
            });
            return;
        }
        appendConsoleMessage({
            role: "agi",
            text: chatResponse.message || consoleStatusAnswer(),
            flow: chatResponse.flow?.length ? chatResponse.flow : flow,
            missionBrief: toConsoleMissionBrief(chatResponse.mission_brief),
            toolTrace: toConsoleToolTrace(chatResponse.tool_trace),
            participationGate: toConsoleParticipationGate(chatResponse.participation_gate),
            decisionRoute: toConsoleDecisionRoute(chatResponse.decision_route),
            receipt: toConsoleReceipt(chatResponse),
            actions: toConsoleActions(chatResponse.suggested_actions, command),
        });
    };
    const handleConsoleAction = async (action) => {
        setPendingConsoleAction(null);
        const executesThroughBackend = action.uiHandler === "execute_governed_action" ||
            action.mode === "controlled_execution" ||
            action.mode === "execute_through_role_runtime";
        if (!executesThroughBackend) {
            return;
        }
        const latestDecision = resident.decisions[0] || null;
        const result = await resident.executeAgiAction({
            message: action.sourceMessage || action.reason,
            action_id: action.actionId,
            decision_type: agiDecisionType,
            run_id: latestDecision?.run_id || "",
            task_id: latestDecision?.task_id || "",
            goal_id: latestDecision?.goal_id || "",
            context: {
                cockpit_status: cockpitStatusLabel,
                cockpit_mission: cockpitMission,
                blockers: agiBlockers,
                goal_count: resident.goals.length,
                decision_count: resident.decisions.length,
            },
            context_refs: latestDecision?.context_refs || [],
            evidence_refs: latestDecision?.evidence_refs || [],
            decision_limit: 12,
            max_runs: 20,
        });
        if (!result || result.status !== "executed") {
            const blockedBoundary = action.actionId === "request_resident_agi_judgement"
                ? "[边界] 未产生 AGI 判断，也未改变项目状态"
                : "[边界] 未进入项目经理 → 总工程师 → 执行官 → 质检链路";
            appendConsoleMessage({
                role: "agi",
                text: result?.reason
                    ? `后端没有执行这个受控动作：${result.reason}。平台不会用前端本地判断冒充 AGI 结果。`
                    : "我尝试通过 Resident AGI 受控动作入口执行当前动作，但后端没有返回成功结果。平台不会用前端本地判断冒充 AGI 结果。",
                flow: [
                    "[调用] resident.executeAgiAction",
                    "[结果] blocked_or_empty",
                    blockedBoundary,
                ],
                toolTrace: toConsoleToolTrace(result?.tool_trace),
                actions: toConsoleActions(result?.follow_up_actions, action.sourceMessage || action.reason),
                receipt: result?.receipt
                    ? {
                        title: result.receipt.title || "受控动作阻断凭证",
                        summary: result.receipt.summary || result.reason || "受控动作未执行。",
                        status: result.receipt.status || "BLOCKED",
                        rows: (result.receipt.rows || [])
                            .map((row) => ({
                            label: String(row.label || "").trim(),
                            value: String(row.value || "").trim(),
                        }))
                            .filter((row) => row.label && row.value),
                    }
                    : undefined,
            });
            return;
        }
        const receiptRows = result.receipt?.rows?.length
            ? result.receipt.rows.map((row) => ({
                label: String(row.label || "").trim(),
                value: String(row.value || "").trim(),
            }))
            : [
                {
                    label: action.actionId === "request_resident_agi_judgement"
                        ? "决策"
                        : "目标",
                    value: action.actionId === "request_resident_agi_judgement"
                        ? String(result.decision?.decision_id || "not_recorded")
                        : String(result.goal?.goal_id || "pending"),
                },
                {
                    label: "动作",
                    value: action.actionId,
                },
                { label: "角色链", value: "项目经理→总工程师→执行官→质检已保持" },
            ];
        if (action.actionId === "request_resident_agi_judgement") {
            appendConsoleMessage({
                role: "agi",
                text: `已通过 resident_agi 角色回合完成一次受控判断，结论为“${result.decision?.verdict || "unknown"}”。这不会创建目标、不会直接修复，也不会把失败门禁标记为通过。`,
                flow: [
                    "[调用] resident.executeAgiAction",
                    "[角色] resident_agi role runtime + ContextOS + TurnEngine",
                    result.decision
                        ? "[记录] resident.decision_trace 已写入"
                        : "[记录] resident.decision_trace 未写入",
                    "[边界] 未直接执行 Director 修复",
                ],
                toolTrace: toConsoleToolTrace(result.tool_trace),
                actions: toConsoleActions(result.follow_up_actions, action.sourceMessage || action.reason),
                receipt: {
                    title: result.receipt?.title || "AGI 判断凭证",
                    summary: result.receipt?.summary ||
                        "已通过 Resident AGI public contract 完成受控判断。",
                    status: result.receipt?.status || "JUDGED",
                    rows: receiptRows,
                },
            });
            return;
        }
        appendConsoleMessage({
            role: "agi",
            text: `已创建 Resident 受控目标：“${result.goal?.title || "未命名目标"}”。这只是把诉求送入治理队列，后续仍需批准、阶段化，并通过项目经理 → 总工程师 → 执行官 → 质检链路执行。`,
            flow: [
                "[调用] resident.executeAgiAction",
                "[写入] resident.goal_governance.commands + resident.decision_trace",
                result.decision
                    ? "[记录] resident.decision_trace 已写入"
                    : "[记录] resident.decision_trace 未写入",
                "[边界] Director 修复未直接执行",
            ],
            toolTrace: toConsoleToolTrace(result.tool_trace),
            actions: toConsoleActions(result.follow_up_actions, action.sourceMessage || action.reason),
            receipt: {
                title: result.receipt?.title || "受控动作执行凭证",
                summary: result.receipt?.summary ||
                    "已通过 Resident public contract 创建目标并写入 decision trace。",
                status: result.receipt?.status || "EXECUTED",
                rows: receiptRows,
            },
        });
    };
    return (_jsxs("div", { "data-testid": "resident-workspace", className: "flex h-full flex-col bg-slate-950 text-slate-100", children: [_jsxs("header", { className: "flex items-center justify-between border-b border-slate-800 px-4 py-3", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx(Button, { variant: "ghost", size: "sm", onClick: onBackToMain, className: "text-slate-400 hover:text-white", children: _jsx(ArrowLeft, { className: "size-4" }) }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Bot, { className: "size-5 text-slate-300" }), _jsx("span", { className: "font-medium", children: "AGI \u5DE5\u4F5C\u533A" })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Badge, { variant: "outline", className: cn("border-slate-700 bg-slate-950/40", isActive ? "text-slate-100" : "text-slate-500"), children: isActive ? "运行中" : "已停止" }), isActive ? (_jsxs(Button, { size: "sm", variant: "destructive", onClick: () => void resident.stop(), children: [_jsx(Square, { className: "mr-1 size-3" }), "\u505C\u6B62"] })) : (_jsxs(Button, { size: "sm", onClick: () => void resident.start(mode), className: "bg-slate-100 text-slate-950 hover:bg-white", children: [_jsx(Play, { className: "mr-1 size-3" }), "\u542F\u52A8"] })), _jsxs(Button, { size: "sm", variant: "outline", "data-testid": "resident-tick", title: "\u7ACB\u5373\u8FD0\u884C\u4E00\u8F6E\u53CD\u601D (Tick)\uFF1A\u5143\u8BA4\u77E5 / \u6280\u80FD / \u53CD\u4E8B\u5B9E / \u81EA\u6539 / \u76EE\u6807\u751F\u6210", onClick: () => void resident.tick(), disabled: resident.isActing("tick"), className: "border-slate-700 text-slate-200 hover:bg-slate-900", children: [_jsx(Brain, { className: cn("mr-1 size-3", resident.isActing("tick") && "animate-pulse") }), "\u53CD\u601D\u4E00\u8F6E"] }), _jsx(Button, { size: "sm", variant: "ghost", onClick: () => void resident.refresh(), disabled: resident.loading, children: _jsx(RefreshCw, { className: cn("size-4", resident.loading && "animate-spin") }) })] })] }), _jsxs("div", { className: "flex-1 overflow-auto p-4", children: [_jsx("div", { className: "mb-4 flex gap-1 border-b border-slate-800", children: [
                            { key: "overview", label: "概览" },
                            { key: "goals", label: "目标" },
                            { key: "decisions", label: "决策" },
                            { key: "evolution", label: "进化" },
                        ].map((tab) => (_jsx("button", { onClick: () => setActiveTab(tab.key), "data-testid": `resident-tab-${tab.key}`, className: cn("px-4 py-2 text-sm font-medium transition-colors", activeTab === tab.key
                                ? "border-b-2 border-slate-200 text-slate-100"
                                : "text-slate-400 hover:text-slate-200"), children: tab.label }, tab.key))) }), activeTab === "overview" && (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(380px,0.85fr)]", children: [_jsx(AgiCockpitOverview, { statusLabel: cockpitStatusLabel, statusDetail: cockpitStatusDetail, severity: cockpitSeverity, mission: cockpitMission, nextAction: cockpitNextAction, blockers: agiBlockers, trustSignals: trustSignals, roleTrackItems: roleTrackItems, goalsCount: totalGoals, decisionsCount: decisionStats.total, evidenceCoverage: cockpitEvidenceCoverage, lastUpdated: formatTime(resident.residentRuntime?.last_tick_at), onOpenAdvanced: () => setAdvancedAuditOpen(true), onExplainBlocker: () => void handleConsoleCommand("/解释卡住"), onRunTick: () => void handleConsoleCommand("/反思一轮") }), _jsx(AgiTacticalConsole, { messages: consoleMessages, value: consoleInput, disabled: Boolean(resident.actionKey), quickCommands: agiTacticalQuickCommands, pendingAction: pendingConsoleAction, onChange: setConsoleInput, onSubmit: () => void handleConsoleCommand(), onQuickCommand: (command) => void handleConsoleCommand(command), onAction: setPendingConsoleAction, onConfirmAction: () => {
                                            if (pendingConsoleAction) {
                                                void handleConsoleAction(pendingConsoleAction);
                                            }
                                        }, onCancelAction: () => setPendingConsoleAction(null), onOpenAdvanced: () => setAdvancedAuditOpen(true), onOpenOperatorSettings: () => {
                                            setOperatorSettingsOpen(true);
                                            setEditingIdentity(false);
                                        }, onOpenGoals: () => {
                                            setActiveTab("goals");
                                            setShowNewGoal(false);
                                        } })] }), _jsx(AgiActionTimeline, { entries: agiActionTimelineEntries }), _jsxs("div", { className: "grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]", children: [_jsx(Card, { className: "border-slate-800 bg-slate-950/55", "data-testid": "agi-operator-briefing", children: _jsxs(CardContent, { className: "p-4", children: [_jsxs("div", { className: "flex flex-wrap items-start justify-between gap-4", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-500", children: [_jsx(Bot, { className: "size-4 text-slate-400" }), "\u503C\u5B88\u673A\u5668\u4EBA"] }), _jsx("div", { className: "mt-2 text-base font-semibold text-slate-50", children: resident.residentIdentity?.name || "常驻 AGI 监督员" }), _jsx("div", { className: "mt-1 max-w-3xl text-sm leading-6 text-slate-400", children: resident.residentIdentity?.mission ||
                                                                        "尚未设定任务宣言" }), _jsxs("div", { className: "mt-3 flex flex-wrap gap-1.5", children: [_jsx(Badge, { className: cn("border text-xs", residentAgiParticipationEnabled
                                                                                ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-200"
                                                                                : "border-slate-700 bg-slate-950 text-slate-500"), children: residentAgiParticipationEnabled
                                                                                ? "AGI 可参与"
                                                                                : "AGI 仅观察" }), _jsx(Badge, { className: "border-slate-700 bg-slate-950 text-xs text-slate-300", children: "\u53D7\u63A7\u6267\u884C" }), _jsx(Badge, { className: "border-slate-700 bg-slate-950 text-xs text-slate-300", children: "\u76F4\u63A5\u5199\u5165\u5DF2\u963B\u65AD" })] })] }), _jsxs("div", { className: "flex shrink-0 flex-wrap gap-2", children: [_jsxs(Button, { size: "sm", variant: "outline", className: "border-slate-700 text-slate-200 hover:bg-slate-900", "data-testid": "resident-edit-identity", onClick: () => {
                                                                        setIdentityName(resident.residentIdentity?.name || "");
                                                                        setIdentityMission(resident.residentIdentity?.mission || "");
                                                                        setOperatorSettingsOpen(true);
                                                                        setEditingIdentity(true);
                                                                    }, children: [_jsx(Pencil, { className: "mr-1 size-3.5" }), "\u7F16\u8F91\u8EAB\u4EFD"] }), _jsxs(Button, { size: "sm", variant: "outline", className: "border-slate-700 text-slate-200 hover:bg-slate-900", "data-testid": "agi-open-operator-settings", onClick: () => setOperatorSettingsOpen((open) => !open), children: [_jsx(Settings, { className: "mr-1 size-3.5" }), operatorSettingsOpen ? "收起设定" : "值守设定"] })] })] }), _jsxs("div", { className: cn("mt-4 rounded-md border px-3 py-2 text-xs", residentAgiLlmReady
                                                        ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-100"
                                                        : residentAgiLlmRiskVisible
                                                            ? "border-amber-500/25 bg-amber-500/10 text-amber-200"
                                                            : "border-slate-800 bg-slate-950/70 text-slate-400"), "data-testid": "resident-agi-llm-binding-status", children: [_jsxs("div", { className: "flex items-center gap-2 font-medium", children: [residentAgiLlmReady ? (_jsx(CheckCircle2, { className: "size-3.5" })) : residentAgiLlmRiskVisible ? (_jsx(Ban, { className: "size-3.5" })) : (_jsx(Bot, { className: "size-3.5" })), _jsx("span", { children: residentAgiLlmReady
                                                                        ? "常驻 AGI 模型已绑定"
                                                                        : residentAgiLlmRiskVisible
                                                                            ? "常驻 AGI 参与已开启但模型不可用"
                                                                            : "常驻 AGI 模型绑定状态未确认" })] }), _jsxs("div", { className: "mt-1 text-[11px] opacity-80", children: [residentAgiLlmBound
                                                                    ? `${residentAgiLlmProvider}/${residentAgiLlmModel}${residentAgiLlmStatus?.grade
                                                                        ? ` · ${residentAgiLlmStatus.grade}`
                                                                        : ""}`
                                                                    : "请在 LLM 视觉配置编辑器中为常驻 AGI 绑定模型。", residentAgiLlmStatus?.readinessIssue
                                                                    ? ` ${residentAgiLlmStatus.readinessIssue}`
                                                                    : "", residentAgiLlmStatus?.runtimeIssue
                                                                    ? ` ${residentAgiLlmStatus.runtimeIssue}`
                                                                    : ""] })] })] }) }), _jsx(Card, { className: "border-slate-800 bg-slate-950/55", children: _jsxs(CardContent, { className: "space-y-2 p-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-500", children: [_jsx(Target, { className: "size-4 text-slate-400" }), "\u5FEB\u901F\u5165\u53E3"] }), _jsxs(Button, { size: "sm", variant: "outline", className: "w-full justify-start border-slate-700 text-slate-200 hover:bg-slate-900", onClick: () => setActiveTab("goals"), children: [_jsx(FileText, { className: "mr-2 size-3.5" }), "\u67E5\u770B\u76EE\u6807\u961F\u5217"] }), _jsxs(Button, { size: "sm", variant: "outline", className: "w-full justify-start border-slate-700 text-slate-200 hover:bg-slate-900", onClick: () => setActiveTab("decisions"), children: [_jsx(Brain, { className: "mr-2 size-3.5" }), "\u6253\u5F00\u51B3\u7B56\u56DE\u5408"] }), _jsxs(Button, { size: "sm", variant: "outline", className: "w-full justify-start border-slate-700 text-slate-200 hover:bg-slate-900", onClick: () => setAdvancedAuditOpen(true), children: [_jsx(Eye, { className: "mr-2 size-3.5" }), "\u67E5\u770B\u8BC1\u636E\u9ED1\u5323\u5B50"] })] }) })] }), operatorSettingsOpen && (_jsxs("div", { className: "grid gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]", "data-testid": "agi-operator-settings", children: [_jsxs(Card, { className: "border-slate-800/80 bg-slate-950/45", children: [_jsxs(CardHeader, { className: "flex flex-row items-center justify-between pb-2", children: [_jsxs(CardTitle, { className: "flex items-center gap-2 text-sm text-slate-300", children: [_jsx(Bot, { className: "size-4 text-slate-400" }), "AGI \u8EAB\u4EFD\u8BBE\u5B9A"] }), !editingIdentity && (_jsx(Button, { size: "sm", variant: "ghost", "data-testid": "resident-edit-identity-inline", onClick: () => {
                                                            setIdentityName(resident.residentIdentity?.name || "");
                                                            setIdentityMission(resident.residentIdentity?.mission || "");
                                                            setAgiParticipationEnabled(Boolean(resident.residentIdentity
                                                                ?.resident_agi_participation?.enabled));
                                                            setAgiParticipationScopes(selectedAgiParticipationScopes(resident.residentIdentity
                                                                ?.resident_agi_participation));
                                                            setEditingIdentity(true);
                                                        }, children: _jsx(Pencil, { className: "size-3" }) }))] }), _jsx(CardContent, { children: editingIdentity ? (_jsxs("div", { className: "space-y-2", children: [_jsx(Input, { "aria-label": "AGI \u540D\u79F0", value: identityName, onChange: (e) => setIdentityName(e.target.value), placeholder: "\u540D\u79F0", className: "bg-slate-950" }), _jsx(Textarea, { "aria-label": "AGI \u4EFB\u52A1\u5BA3\u8A00", value: identityMission, onChange: (e) => setIdentityMission(e.target.value), placeholder: "\u4EFB\u52A1\u5BA3\u8A00", className: "bg-slate-950" }), _jsxs("div", { className: "flex gap-2", children: [_jsx(Button, { size: "sm", "data-testid": "resident-save-identity", disabled: resident.isActing("save-identity"), onClick: async () => {
                                                                        await resident.saveIdentity({
                                                                            name: identityName.trim(),
                                                                            mission: identityMission.trim(),
                                                                        });
                                                                        setEditingIdentity(false);
                                                                    }, className: "bg-slate-100 text-slate-950 hover:bg-white", children: "\u4FDD\u5B58" }), _jsx(Button, { size: "sm", variant: "ghost", onClick: () => setEditingIdentity(false), children: "\u53D6\u6D88" })] })] })) : (_jsxs(_Fragment, { children: [_jsx("div", { className: "text-base font-medium text-white", children: resident.residentIdentity?.name || "常驻 AGI 监督员" }), _jsx("div", { className: "mt-1 text-sm text-slate-400", children: resident.residentIdentity?.mission ||
                                                                "尚未设定任务宣言" }), _jsxs("div", { className: "mt-3 flex flex-wrap gap-1.5", children: [_jsx(Badge, { className: cn("border text-xs", residentAgiParticipationEnabled
                                                                        ? "border-slate-700 bg-slate-950 text-slate-200"
                                                                        : "border-slate-700 bg-slate-950 text-slate-500"), children: residentAgiParticipationEnabled
                                                                        ? "AGI 参与已开启"
                                                                        : "AGI 参与未开启" }), identityParticipationScopes
                                                                    .slice(0, 4)
                                                                    .map((scope) => (_jsx(Badge, { className: "border-slate-700 bg-slate-950 text-xs text-slate-300", children: AGI_PARTICIPATION_LABELS[scope] || scope }, scope)))] }), _jsxs("div", { className: cn("mt-3 rounded border px-3 py-2 text-xs", residentAgiLlmReady
                                                                ? "border-slate-700 bg-slate-950/70 text-slate-200"
                                                                : residentAgiLlmRiskVisible
                                                                    ? "border-amber-500/25 bg-amber-500/10 text-amber-200"
                                                                    : "border-slate-800 bg-slate-950/70 text-slate-400"), "data-testid": "resident-agi-llm-binding-status-inline", children: [_jsxs("div", { className: "flex items-center gap-2 font-medium", children: [residentAgiLlmReady ? (_jsx(CheckCircle2, { className: "size-3.5" })) : residentAgiLlmRiskVisible ? (_jsx(Ban, { className: "size-3.5" })) : (_jsx(Bot, { className: "size-3.5" })), _jsx("span", { children: residentAgiLlmReady
                                                                                ? "常驻 AGI 模型已绑定"
                                                                                : residentAgiLlmRiskVisible
                                                                                    ? "常驻 AGI 参与已开启但模型不可用"
                                                                                    : "常驻 AGI 模型绑定状态未确认" })] }), _jsxs("div", { className: "mt-1 text-[11px] opacity-80", children: [residentAgiLlmBound
                                                                            ? `${residentAgiLlmProvider}/${residentAgiLlmModel}${residentAgiLlmStatus?.grade
                                                                                ? ` · ${residentAgiLlmStatus.grade}`
                                                                                : ""}`
                                                                            : "请在 LLM 视觉配置编辑器中为常驻 AGI 绑定模型。", residentAgiLlmStatus?.readinessIssue
                                                                            ? ` ${residentAgiLlmStatus.readinessIssue}`
                                                                            : "", residentAgiLlmStatus?.runtimeIssue
                                                                            ? ` ${residentAgiLlmStatus.runtimeIssue}`
                                                                            : ""] })] })] })) })] }), _jsx(AgiParticipationDock, { enabled: agiParticipationEnabled, options: agiParticipationOptions, selectedScopes: agiParticipationScopes, repairAdvisoryEnabled: agiRepairAdvisoryParticipationEnabled, llmReady: residentAgiLlmReady, llmIssue: residentAgiLlmRiskVisible
                                            ? residentAgiLlmStatus?.readinessIssue ||
                                                residentAgiLlmStatus?.runtimeIssue ||
                                                "常驻 AGI 模型绑定不可用。"
                                            : "", isSaving: resident.isActing("save-identity"), onEnabledChange: setAgiParticipationEnabled, onToggleScope: toggleAgiParticipationScope, onToggleRepairAdvisory: setAgiRepairAdvisoryParticipation, onSave: () => void handleSaveAgiParticipation(), onOpenAdvanced: () => setAdvancedAuditOpen(true) })] })), advancedAuditOpen && latestInsight && (_jsxs(Card, { className: "border-slate-800 bg-slate-900/50", children: [_jsx(CardHeader, { className: "pb-2", children: _jsxs(CardTitle, { className: "flex items-center gap-2 text-sm text-slate-300", children: [_jsx(FileSearch, { className: "size-4 text-slate-400" }), "\u6700\u65B0\u5143\u8BA4\u77E5"] }) }), _jsx(CardContent, { children: _jsxs("div", { className: "space-y-1", children: [_jsx("div", { className: "text-sm font-medium text-white", children: latestInsight.summary }), _jsxs("div", { className: "text-xs text-slate-500", children: [latestInsight.strategy_tag ||
                                                            latestInsight.insight_type ||
                                                            "未分类", " ", "\u00B7 \u7F6E\u4FE1\u5EA6", " ", Math.round((latestInsight.confidence ?? 0) * 100), "%"] })] }) })] })), advancedAuditOpen && capabilities.length > 0 && (_jsxs(Card, { className: "border-slate-800 bg-slate-900/50", children: [_jsx(CardHeader, { className: "pb-2", children: _jsx(CardTitle, { className: "text-sm text-slate-300", children: "\u80FD\u529B\u56FE\u8C31" }) }), _jsx(CardContent, { children: _jsx("div", { className: "grid gap-2 sm:grid-cols-2", children: capabilities.slice(0, 4).map((capability) => (_jsxs("div", { className: "rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2", children: [_jsx("div", { className: "text-sm font-medium text-slate-200", children: capability.name }), _jsxs("div", { className: "mt-1 text-xs text-slate-500", children: ["\u6210\u529F\u7387", " ", Math.round((capability.success_rate ?? 0) * 100), "% \u00B7 \u8BC1\u636E ", capability.evidence_count ?? 0] })] }, capability.capability_id))) }) })] })), _jsx("div", { className: "rounded-lg border border-slate-800 bg-slate-950/70 p-3", "data-testid": "agi-advanced-audit-dock", children: _jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3", children: [_jsxs("div", { children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium text-slate-200", children: [_jsx(FileSearch, { className: "size-4 text-slate-400" }), "\u8BC1\u636E\u9ED1\u5323\u5B50"] }), _jsx("div", { className: "mt-1 text-xs text-slate-500", children: "\u4FDD\u5B58 Polaris \u5143\u9879\u76EE\u5BA1\u8BA1\u7EC6\u8282\uFF1A\u8FD0\u884C\u6295\u5F71\u3001\u80FD\u529B\u8FB9\u754C\u3001\u4FEE\u590D\u7B56\u7565\u3001 \u8BC1\u636E\u63A5\u53E3\u4E0E\u5BA1\u8BA1\u5305\u3002\u9ED8\u8BA4\u6536\u8D77\uFF0C\u907F\u514D\u5E72\u6270\u9A7E\u9A76\u8231\u3002" })] }), _jsxs(Button, { size: "sm", variant: "outline", className: "border-slate-700 text-slate-200 hover:bg-slate-900", onClick: () => setAdvancedAuditOpen((open) => !open), "data-testid": "agi-toggle-advanced-audit", children: [advancedAuditOpen ? (_jsx(ChevronDown, { className: "mr-1 size-3.5" })) : (_jsx(ChevronRight, { className: "mr-1 size-3.5" })), advancedAuditOpen ? "收起黑匣子" : "打开黑匣子"] })] }) }), advancedAuditOpen && (_jsxs(_Fragment, { children: [_jsxs("div", { className: "rounded-md border border-slate-800 bg-slate-950/45 px-3 py-2 font-mono text-[10px] text-slate-400", "data-testid": "resident-runtime-evidence", children: [_jsx("span", { children: runtimeEvidence?.schema_version ||
                                                    "resident.runtime_projection_evidence.v1" }), _jsxs("span", { children: [" ", "\u00B7", " ", runtimeEvidence?.realtime_channel ||
                                                        "runtime.v2.status.resident"] }), _jsxs("span", { children: [" ", "\u00B7", " ", runtimeEvidence?.snapshot_channel ||
                                                        "runtime.v2.status.snapshot"] }), _jsxs("span", { children: [" ", "\u00B7 ", runtimeEvidence?.projection_field || "snapshot.resident"] }), _jsxs("span", { children: [" ", "\u00B7 \u6765\u6E90\uFF1A", runtimeEvidence?.source || formatAgiUiToken("unavailable")] })] }), tickAutonomyBoundary && (_jsxs("div", { className: "rounded-md border border-slate-800 bg-slate-950/45 px-3 py-2 font-mono text-[10px] text-slate-400", "data-testid": "resident-tick-autonomy-boundary", children: [_jsx("span", { children: tickAutonomyBoundary.schema_version ||
                                                    "resident.tick_autonomy_boundary.v1" }), _jsxs("span", { children: [" ", "\u00B7 \u8F6E\u6B21\u89D2\u8272\uFF1A", tickAutonomyBoundary.tick_role || "evidence_only"] }), _jsxs("span", { children: [" ", "\u00B7 \u5224\u65AD\u5165\u53E3\uFF1A", tickAutonomyBoundary.agi_judgement_entrypoint ||
                                                        "resident_agi_decision_turn"] }), _jsxs("span", { children: [" ", "\u00B7 \u65C1\u8DEF\u6A21\u578B\uFF1A", tickAutonomyBoundary.sidecar_llm_allowed
                                                        ? formatAgiUiToken("allowed")
                                                        : formatAgiUiToken("blocked")] })] })), _jsxs(Card, { className: "border-slate-800 bg-slate-900/50", children: [_jsx(CardHeader, { className: "pb-2", children: _jsxs(CardTitle, { className: "flex items-center gap-2 text-sm text-slate-300", children: [_jsx(Brain, { className: "size-4 text-slate-400" }), "AGI \u89D2\u8272\u80FD\u529B\u9762"] }) }), _jsxs(CardContent, { children: [_jsxs("div", { className: "grid gap-2 sm:grid-cols-3", children: [_jsx(CapabilityMetric, { label: "\u89D2\u8272", value: agiCapabilitySurface?.role_id || "resident_agi" }), _jsx(CapabilityMetric, { label: "\u8FD0\u884C\u5E95\u5EA7", value: agiCapabilitySurface?.runtime_foundation ||
                                                                    "RoleRuntime / ContextOS / TurnEngine" }), _jsx(CapabilityMetric, { label: "\u80FD\u529B\u6570", value: String(agiCapabilitySurface?.count ?? agiCapabilities.length) })] }), _jsxs("div", { className: "mt-3 rounded-md border border-slate-800 bg-slate-950/45 px-3 py-2 text-xs text-slate-300", "data-testid": "resident-agi-role-foundation", children: [_jsx("span", { className: "font-mono text-slate-100", children: "resident_agi" }), " ", "\u8FD0\u884C\u5728\u540C\u4E00 RoleRuntime / ContextOS / TurnEngine \u5E95\u5EA7\u4E0A\uFF1B\u5E73\u53F0\u7EA7\u8BC1\u636E\u8BBF\u95EE\u66F4\u5BBD\uFF0C\u4F46\u6267\u884C\u5FC5\u987B\u670D\u4ECE\u786C\u89C4\u5219\u3001\u80FD\u529B\u76EE\u5F55\u3001 \u6743\u5A01\u5951\u7EA6\u548C", formatAgiRoleChain("PM → Chief Engineer → Director"), "\u3002"] }), _jsx(CapabilityGovernanceMatrix, { stats: capabilityGovernance, authorityMatrix: agiAuthorityMatrix, accessRegistry: agiCapabilityAccessRegistry, runtimeFoundation: agiCapabilitySurface?.runtime_foundation ||
                                                            "roles.runtime + ContextOS + TurnEngine" }), _jsx(AgiRepairStrategyCatalogPanel, { catalog: hardcodedRepairCatalog }), _jsx(AgiRepairAdvisoryPolicyPanel, { policy: repairAdvisoryPolicy }), _jsx(AgiRepairAdvisoryOverlayPanel, { overlay: activeRepairAdvisoryOverlay, source: activeRepairAdvisoryOverlaySource }), _jsx(AgiDecisionCapabilityRegistry, { schema: agiCapabilitySurface?.decision_capability_schema, registry: agiDecisionCapabilityRegistry, decisions: agiDecisionCapabilities }), _jsx(AgiEvidenceInterfaceMatrix, { capabilities: agiCapabilities, contract: agiEvidenceInterfaceContract }), _jsx(AgiEvidenceInterfaceReadiness, { payload: agiEvidenceInterfaces }), _jsx(DecisionBoundaryMatrix, { schema: agiCapabilitySurface?.decision_boundary_schema, boundaries: agiDecisionBoundaries }), _jsx(DecisionBoundaryPolicyPanel, { policy: agiDecisionBoundaryPolicy }), _jsx(AgiAuditPackPanel, { pack: agiAuditPack }), _jsxs("div", { className: "mt-3 grid gap-2 lg:grid-cols-2", children: [agiCapabilities.map((capability) => (_jsxs("div", { className: "rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-sm font-medium text-slate-200", children: capability.name || "未命名能力" }), _jsx("span", { className: "shrink-0 text-[10px] uppercase text-slate-500", children: formatAgiUiToken(capability.access || "read_only") })] }), _jsx("div", { className: "mt-1 text-xs text-slate-500", children: [capability.category, capability.contract_ref]
                                                                            .filter(Boolean)
                                                                            .join(" · ") }), _jsxs("div", { className: "mt-1 flex flex-wrap gap-1", children: [capability.risk_level && (_jsxs("span", { className: cn("rounded border px-1.5 py-0.5 text-[10px]", capability.risk_level === "high"
                                                                                    ? "border-amber-500/20 bg-amber-500/10 text-amber-300"
                                                                                    : "border-slate-700 bg-slate-900 text-slate-400"), children: ["\u98CE\u9669 ", formatAgiUiToken(capability.risk_level)] })), (capability.guardrails || [])
                                                                                .slice(0, 1)
                                                                                .map((guardrail) => (_jsx("span", { className: "truncate rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400", title: guardrail, children: guardrail }, guardrail))), (capability.evidence_refs || [])
                                                                                .slice(0, 1)
                                                                                .map((evidenceRef) => (_jsx("span", { className: "truncate rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", title: evidenceRef, children: evidenceRef }, evidenceRef)))] })] }, capability.capability_id || capability.name))), agiCapabilities.length === 0 && (_jsx("div", { className: "rounded-lg border border-dashed border-slate-700 p-4 text-sm text-slate-500", children: "\u6682\u65E0\u80FD\u529B\u9762\u6295\u5F71" }))] })] })] })] }))] })), activeTab === "goals" && (_jsxs("div", { className: "space-y-3", children: [!showNewGoal ? (_jsxs(Button, { variant: "outline", className: "w-full border-dashed border-slate-700 text-slate-400 hover:text-white", onClick: () => setShowNewGoal(true), children: [_jsx(Plus, { className: "mr-1 size-4" }), "\u65B0\u5EFA\u76EE\u6807"] })) : (_jsxs(Card, { className: "border-slate-800 bg-slate-900/50", children: [_jsx(CardHeader, { className: "pb-2", children: _jsxs(CardTitle, { className: "flex items-center justify-between text-sm", children: [_jsx("span", { children: "\u76EE\u6807\u751F\u6210\u53F0" }), _jsx(Button, { size: "sm", variant: "ghost", onClick: () => setShowNewGoal(false), children: _jsx(X, { className: "size-4" }) })] }) }), _jsxs(CardContent, { className: "space-y-3", children: [_jsx(Input, { "aria-label": "\u76EE\u6807\u6807\u9898", placeholder: "\u76EE\u6807\u6807\u9898", value: newGoalTitle, onChange: (e) => setNewGoalTitle(e.target.value), className: "border-slate-700 bg-slate-950" }), _jsx(Textarea, { "aria-label": "\u76EE\u6807\u63CF\u8FF0", placeholder: "\u76EE\u6807\u63CF\u8FF0\uFF08\u53EF\u9009\uFF09", value: newGoalDesc, onChange: (e) => setNewGoalDesc(e.target.value), className: "border-slate-700 bg-slate-950", rows: 2 }), _jsxs("div", { className: "flex gap-2", children: [_jsx(Button, { onClick: handleCreateGoal, disabled: !newGoalTitle.trim() || resident.isActing("create-goal"), className: "bg-slate-100 text-slate-950 hover:bg-white", children: "\u521B\u5EFA AGI \u76EE\u6807" }), _jsx(Button, { variant: "ghost", onClick: () => setShowNewGoal(false), children: "\u53D6\u6D88" })] })] })] })), _jsxs("div", { className: "space-y-2", children: [resident.goals.map((goal) => (_jsx(GoalItem, { goal: goal, execution: goal.goal_id
                                            ? resident.getGoalExecution?.(goal.goal_id)
                                            : undefined, expanded: expandedGoal === goal.goal_id, onToggle: () => setExpandedGoal(expandedGoal === goal.goal_id
                                            ? null
                                            : goal.goal_id || null), onApprove: () => void resident.approveGoal(String(goal.goal_id)), onReject: () => void resident.rejectGoal(String(goal.goal_id)), onMaterialize: () => void resident.materializeGoal(String(goal.goal_id)), onStage: () => void resident.stageGoal(String(goal.goal_id), false), onPromoteToPm: () => void resident.stageGoal(String(goal.goal_id), true), onRun: () => void resident.runGoal(String(goal.goal_id), false, 1), disabled: Boolean(resident.actionKey) }, goal.goal_id))), resident.goals.length === 0 && !showNewGoal && (_jsx("div", { className: "rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-500", children: "\u6682\u65E0\u76EE\u6807\uFF0C\u70B9\u51FB\u4E0A\u65B9\u6309\u94AE\u521B\u5EFA" }))] })] })), activeTab === "decisions" && (_jsxs("div", { className: "space-y-3", children: [_jsxs(Card, { className: "border-slate-800 bg-slate-900/50", "data-testid": "resident-agi-decision-turn", children: [_jsx(CardHeader, { className: "pb-2", children: _jsxs(CardTitle, { className: "flex items-center justify-between gap-2 text-sm text-slate-300", children: [_jsxs("span", { className: "flex items-center gap-2", children: [_jsx(Brain, { className: "size-4 text-slate-400" }), "AGI \u51B3\u7B56\u56DE\u5408"] }), _jsx(Badge, { className: "border-slate-700 bg-slate-950 text-slate-300", children: "resident_agi" })] }) }), _jsxs(CardContent, { className: "space-y-3", children: [_jsxs("div", { className: "grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end", children: [_jsxs("label", { className: "block min-w-0", children: [_jsx("span", { className: "mb-1 block text-xs text-slate-500", children: "\u51B3\u7B56\u7C7B\u578B" }), _jsx("select", { "aria-label": "AGI \u51B3\u7B56\u7C7B\u578B", "data-testid": "resident-agi-decision-type", value: agiDecisionType, onChange: (event) => setAgiDecisionType(event.target.value), className: "h-9 w-full rounded border border-slate-700 bg-slate-950 px-2 text-xs text-slate-200 outline-none focus:border-slate-500", children: agiDecisionTypeOptions.map((option) => (_jsx("option", { value: option.decisionId, children: option.label }, option.decisionId))) })] }), _jsxs("div", { className: "flex flex-wrap gap-1", "data-testid": "resident-agi-selected-decision-meta", children: [_jsx("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: selectedAgiDecisionCapability?.decision_id ||
                                                                    agiDecisionType }), selectedAgiDecisionCapability?.owner && (_jsx("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: selectedAgiDecisionCapability.owner })), selectedAgiDecisionCapability?.risk_level && (_jsxs("span", { className: "rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[10px] text-amber-200", children: ["\u98CE\u9669\uFF1A", formatAgiUiToken(selectedAgiDecisionCapability.risk_level)] }))] })] }), _jsx(Textarea, { "aria-label": "AGI \u51B3\u7B56\u76EE\u6807", value: agiDecisionObjective, onChange: (event) => setAgiDecisionObjective(event.target.value), className: "min-h-20 border-slate-700 bg-slate-950" }), _jsx(AgiDecisionProfilePanel, { profile: agiDecisionProfile, testId: "resident-agi-decision-turn-profile" }), _jsx(AgiSelectedDecisionEvidencePanel, { decision: selectedAgiDecisionCapability, evidencePayload: agiEvidenceInterfaces, contract: agiEvidenceInterfaceContract, refreshing: resident.isActing("agi-evidence-interfaces"), onRefresh: () => void resident.refreshAgiEvidenceInterfaces(agiDecisionType) }), _jsx(AgiDecisionHandoffPanel, { handoff: lastAgiDecisionHandoff }), _jsx(AgiHandoffInboxPanel, { inbox: agiHandoffs }), _jsx("div", { className: "flex items-center justify-end", children: _jsxs(Button, { size: "sm", "data-testid": "resident-run-agi-decision", disabled: !agiDecisionObjective.trim() ||
                                                        resident.isActing("agi-decide"), onClick: () => void handleRunAgiDecision(), className: "bg-slate-100 text-slate-950 hover:bg-white", children: [_jsx(Brain, { className: cn("mr-1 size-3", resident.isActing("agi-decide") && "animate-pulse") }), "\u8FD0\u884C\u51B3\u7B56"] }) })] })] }), _jsx(DecisionAuditSummary, { stats: decisionStats }), _jsx("div", { className: "space-y-2", children: resident.decisions.map((decision) => (_jsx(DecisionItem, { decision: decision, workspace: workspace }, decision.decision_id || decision.timestamp))) }), resident.decisions.length === 0 && (_jsx("div", { className: "rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-500", children: "\u6682\u65E0\u51B3\u7B56\u8BB0\u5F55" }))] })), activeTab === "evolution" && (_jsxs("div", { className: "space-y-4", children: [_jsx(EvolutionSection, { icon: _jsx(Sparkles, { className: "size-4 text-slate-400" }), title: "\u6280\u80FD\u5DE5\u574A", count: resident.residentSkills.length, actionLabel: "\u63D0\u70BC\u6280\u80FD", actionTestId: "resident-extract-skills", onAction: () => void resident.extractSkills(), acting: resident.isActing("extract-skills"), emptyHint: "\u5C1A\u65E0\u6280\u80FD\uFF08\u8FD0\u884C\u4E00\u8F6E\u53CD\u601D\u540E\u751F\u6210\uFF09", children: resident.residentSkills.map((skill, idx) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-950/50 p-2", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "text-sm font-medium text-slate-200", children: skill.name || "未命名技能" }), _jsxs("span", { className: "text-xs text-slate-500", children: ["v", skill.version ?? 1, " \u00B7", " ", Math.round((skill.confidence ?? 0) * 100), "%"] })] }), skill.trigger && (_jsxs("div", { className: "mt-1 text-xs text-slate-400", children: ["\u89E6\u53D1: ", skill.trigger] }))] }, skill.skill_id || idx))) }), _jsx(EvolutionSection, { icon: _jsx(FlaskConical, { className: "size-4 text-slate-400" }), title: "\u53CD\u4E8B\u5B9E\u5B9E\u9A8C", count: resident.residentExperiments.length, actionLabel: "\u8FD0\u884C\u5B9E\u9A8C", actionTestId: "resident-run-experiments", onAction: () => void resident.runExperiments(), acting: resident.isActing("run-experiments"), emptyHint: "\u5C1A\u65E0\u5B9E\u9A8C\uFF08\u9700\u6709\u5931\u8D25\u51B3\u7B56\u4F5C\u4E3A\u8F93\u5165\uFF09", children: resident.residentExperiments.map((exp, idx) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-950/50 p-2", children: [_jsx("div", { className: "text-sm text-slate-200", children: (exp.baseline_strategy || "基线") +
                                                " → " +
                                                (exp.counterfactual_strategy || "反事实") }), exp.recommendation && (_jsxs("div", { className: "mt-1 text-xs text-slate-400", children: ["\u5EFA\u8BAE: ", exp.recommendation] })), exp.status && (_jsxs("div", { className: "mt-1 text-xs text-slate-500", children: ["\u72B6\u6001: ", exp.status] }))] }, exp.experiment_id || idx))) }), _jsx(EvolutionSection, { icon: _jsx(Wrench, { className: "size-4 text-slate-400" }), title: "\u81EA\u6539\u63D0\u6848", count: resident.residentImprovements.length, actionLabel: "\u751F\u6210\u63D0\u6848", actionTestId: "resident-run-improvements", onAction: () => void resident.runImprovements(), acting: resident.isActing("run-improvements"), emptyHint: "\u5C1A\u65E0\u63D0\u6848\uFF08\u9700\u6709\u9AD8\u5206\u5B9E\u9A8C\u4F5C\u4E3A\u8F93\u5165\uFF09", children: resident.residentImprovements.map((imp, idx) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-950/50 p-2", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "text-sm font-medium text-slate-200", children: imp.title || "未命名提案" }), imp.status && (_jsx("span", { className: "text-xs text-slate-500", children: imp.status }))] }), (imp.category || imp.target_surface) && (_jsx("div", { className: "mt-1 text-xs text-slate-400", children: [imp.category, imp.target_surface]
                                                .filter(Boolean)
                                                .join(" · ") }))] }, imp.improvement_id || idx))) })] }))] })] }));
}
// Evolution section: skill / experiment / improvement list with a run action
function EvolutionSection({ icon, title, count, actionLabel, actionTestId, onAction, acting, emptyHint, children, }) {
    return (_jsxs(Card, { className: "border-slate-800 bg-slate-900/50", children: [_jsxs(CardHeader, { className: "flex flex-row items-center justify-between pb-2", children: [_jsxs(CardTitle, { className: "flex items-center gap-2 text-sm text-slate-300", children: [icon, title, " (", count, ")"] }), _jsx(Button, { size: "sm", variant: "outline", "data-testid": actionTestId, onClick: onAction, disabled: acting, children: actionLabel })] }), _jsx(CardContent, { className: "space-y-2", children: count === 0 ? (_jsx("div", { className: "text-xs text-slate-500", children: emptyHint })) : (children) })] }));
}
function CapabilityMetric({ label, value }) {
    return (_jsxs("div", { className: "min-w-0 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.12em] text-slate-500", children: label }), _jsx("div", { className: "mt-1 truncate text-xs font-medium text-slate-200", title: value, children: value })] }));
}
function buildCapabilityGovernanceStats(capabilities) {
    const categories = new Set();
    const contractRefs = new Set();
    let readOnly = 0;
    let governedMutation = 0;
    let highRisk = 0;
    let chainRequired = false;
    for (const capability of capabilities) {
        const access = String(capability.access || "").toLowerCase();
        const risk = String(capability.risk_level || "").toLowerCase();
        const category = String(capability.category || "").trim();
        const contractRef = String(capability.contract_ref || "").trim();
        if (category)
            categories.add(category);
        if (contractRef)
            contractRefs.add(contractRef);
        if (access === "read_only")
            readOnly += 1;
        if (access.includes("write") || access.includes("execute"))
            governedMutation += 1;
        if (risk === "high")
            highRisk += 1;
        if (access.includes("pm_ce_director") ||
            contractRef.includes("goal_bridge"))
            chainRequired = true;
    }
    return {
        readOnly,
        governedMutation,
        highRisk,
        categories: Array.from(categories).sort(),
        contractRefs: Array.from(contractRefs).sort(),
        chainRequired,
    };
}
function CapabilityGovernanceMatrix({ stats, authorityMatrix, accessRegistry, runtimeFoundation, }) {
    const chainLabel = authorityMatrix?.chain_required
        ? formatAgiRoleChain(authorityMatrix.chain || "PM → Chief Engineer → Director")
        : stats.chainRequired
            ? formatAgiRoleChain("PM → Chief Engineer → Director")
            : "只读/观察优先";
    const counts = authorityMatrix?.counts || {};
    const accessCounts = accessRegistry?.counts || {};
    const accessGovernedOps = (accessCounts.governed_execution || 0) + (accessCounts.governed_write || 0);
    const readOnly = counts.read_only_capabilities ?? accessCounts.read_only ?? stats.readOnly;
    const governedOps = counts.governed_operation_capabilities ??
        (accessGovernedOps || stats.governedMutation);
    const highRisk = counts.high_risk_capabilities ?? accessCounts.high_risk ?? stats.highRisk;
    const contracts = counts.canonical_contracts ??
        accessCounts.canonical_contracts ??
        stats.contractRefs.length;
    const contractRefs = authorityMatrix?.canonical_contracts ||
        accessRegistry?.canonical_contracts ||
        stats.contractRefs;
    const policy = authorityMatrix?.decision_policy || {};
    const directToolAllowed = Boolean(accessRegistry?.execution_policy?.agi_direct_tool_execution_allowed);
    const directWriteAllowed = Boolean(accessRegistry?.execution_policy?.agi_direct_writes_allowed);
    const domains = accessRegistry?.interface_domains || [];
    return (_jsxs("div", { className: "mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", "data-testid": "resident-agi-governance-matrix", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "\u80FD\u529B\u6CBB\u7406\u77E9\u9635" }), _jsxs("div", { className: "mt-0.5 text-[10px] text-slate-500", children: ["\u5E95\u5EA7: ", authorityMatrix?.runtime_foundation || runtimeFoundation] })] }), _jsx(Badge, { className: "border-slate-700 bg-slate-950/50 text-slate-300", children: chainLabel })] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-4", children: [_jsx(CapabilityMetric, { label: "\u53EA\u8BFB", value: String(readOnly) }), _jsx(CapabilityMetric, { label: "\u53D7\u63A7\u64CD\u4F5C", value: String(governedOps) }), _jsx(CapabilityMetric, { label: "\u9AD8\u98CE\u9669", value: String(highRisk) }), _jsx(CapabilityMetric, { label: "\u5951\u7EA6", value: String(contracts) })] }), _jsx("div", { className: "mt-3 rounded border border-slate-800 bg-slate-950/35 px-2.5 py-2", children: _jsx(SegmentedMeter, { segments: [
                        {
                            label: "只读",
                            value: readOnly,
                            className: "bg-slate-500",
                        },
                        {
                            label: "受控操作",
                            value: governedOps,
                            className: "bg-slate-300",
                        },
                        {
                            label: "高风险",
                            value: highRisk,
                            className: "bg-amber-300/75",
                        },
                    ] }) }), authorityMatrix && (_jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/35 px-2 py-1 font-mono text-[10px] text-slate-400", "data-testid": "resident-agi-authority-matrix", children: [authorityMatrix.schema_version || "resident.agi_authority_matrix.v1", " ", "\u00B7 \u786C\u89C4\u5219 ", counts.platform_hard_rules ?? 0, " \u00B7 AGI \u5224\u65AD", " ", counts.agi_recommendations ?? 0, " \u00B7 \u53D7\u63A7\u6267\u884C", " ", counts.governed_execution_boundaries ?? 0] })), accessRegistry && (_jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/35 px-2 py-1 font-mono text-[10px] text-slate-400", "data-testid": "resident-agi-capability-access-registry", children: [accessRegistry.schema_version ||
                        "resident.agi_capability_access_registry.v1", " ", "\u00B7 \u76F4\u63A5\u5DE5\u5177 ", formatAgiAllowed(directToolAllowed), " \u00B7 \u76F4\u63A5\u5199\u5165", " ", formatAgiAllowed(directWriteAllowed), " \u00B7 \u4EC5\u5EFA\u8BAE", " ", accessCounts.advisory_only ?? 0] })), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", "data-testid": "resident-agi-governance-tags", children: [domains.slice(0, 6).map((domain) => (_jsxs("span", { className: "rounded border border-slate-800 bg-slate-950/45 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: [domain.domain_id, ":r", domain.read_only ?? 0, "/g", domain.governed_execution ?? 0] }, domain.domain_id))), stats.categories.slice(0, 8).map((category) => (_jsx("span", { className: "rounded bg-slate-950/45 px-1.5 py-0.5 font-mono text-[10px] text-slate-500", children: category }, category))), contractRefs.slice(0, 6).map((contractRef) => (_jsx("span", { className: "rounded border border-slate-800 bg-slate-950/35 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: contractRef }, contractRef))), Object.values(policy)
                        .slice(0, 3)
                        .map((policyValue) => (_jsx("span", { className: "rounded border border-slate-800 bg-slate-950/35 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: policyValue }, policyValue)))] })] }));
}
function catalogSummaryEntries(values, limit = 5) {
    return Object.entries(values || {})
        .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
        .slice(0, limit);
}
function AgiRepairStrategyCatalogPanel({ catalog, }) {
    if (!catalog)
        return null;
    const summary = catalog.summary || {};
    const items = catalog.items || [];
    const total = summary.total ?? items.length;
    const executionBoundary = catalog.execution_boundary || "director_authorized_tools_only";
    const chain = formatAgiRoleChain(catalog.chain || "PM → Chief Engineer → Director");
    const agiExecutionAuthority = Boolean(catalog.agi_execution_authority);
    return (_jsxs("div", { className: "mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", "data-testid": "resident-agi-repair-strategy-catalog", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "Director \u786E\u5B9A\u6027\u4FEE\u590D\u7B56\u7565\u76EE\u5F55" }), _jsxs("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: [catalog.schema_version ||
                                        "director.deterministic_repair_strategy_catalog.v1", " ", "\u00B7 ", catalog.source || "director.runtime.repair_kernel"] })] }), _jsxs(Badge, { className: "border-slate-700 bg-slate-950/50 text-slate-300", children: [total, " \u6761\u7B56\u7565"] })] }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", "data-testid": "resident-agi-repair-strategy-catalog-summary", children: [_jsx("span", { className: "rounded border border-slate-700 bg-slate-950/50 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: executionBoundary }), _jsx("span", { className: "rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-[10px] text-slate-300", children: chain }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["AGI \u6267\u884C\uFF1A", formatAgiAllowed(agiExecutionAuthority)] }), _jsx("span", { className: "rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: catalog.unknown_source_tool_policy || "fail_closed_high_risk" }), catalog.director_tool_execution_required && (_jsx("span", { className: "rounded border border-slate-700 bg-slate-950/50 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: "Director \u5DE5\u5177\u5FC5\u9700" }))] }), _jsxs("div", { className: "mt-2 grid gap-2 sm:grid-cols-3", children: [_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] text-slate-500", children: "\u8BED\u8A00" }), _jsx("div", { className: "mt-1 flex flex-wrap gap-1", children: catalogSummaryEntries(summary.by_language).map(([key, count]) => (_jsxs("span", { className: "rounded bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: [key, ":", count] }, key))) })] }), _jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] text-slate-500", children: "\u9636\u6BB5" }), _jsx("div", { className: "mt-1 flex flex-wrap gap-1", children: catalogSummaryEntries(summary.by_phase).map(([key, count]) => (_jsxs("span", { className: "rounded bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: [key, ":", count] }, key))) })] }), _jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] text-slate-500", children: "\u98CE\u9669" }), _jsx("div", { className: "mt-1 flex flex-wrap gap-1", children: catalogSummaryEntries(summary.by_risk).map(([key, count]) => (_jsxs("span", { className: cn("rounded border px-1.5 py-0.5 font-mono text-[10px]", key === "high"
                                        ? "border-rose-500/20 bg-rose-500/10 text-rose-200"
                                        : "border-slate-700 bg-slate-950 text-slate-300"), children: [formatAgiUiToken(key), ":", count] }, key))) })] })] }), items.length > 0 && (_jsx("div", { className: "mt-2 grid gap-2 lg:grid-cols-2", children: items.slice(0, 5).map((item) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2", "data-testid": "resident-agi-repair-strategy-catalog-item", children: [_jsx("div", { className: "truncate font-mono text-[10px] text-slate-200", title: item.source_tool || "", children: item.source_tool || "unknown_source_tool" }), _jsx("div", { className: "mt-1 flex flex-wrap gap-1", children: [item.language, item.phase, item.concern, item.risk_level]
                                .filter(Boolean)
                                .map((token) => (_jsx("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: token }, token))) })] }, item.source_tool))) }))] }));
}
function AgiRepairAdvisoryPolicyPanel({ policy, }) {
    if (!policy)
        return null;
    const summary = policy.summary || {};
    const allowedFields = policy.allowed_suggested_rule_fields || [];
    const forbiddenFields = policy.forbidden_suggested_rule_fields || [];
    const suggestedRulesAllowed = Boolean(summary.suggested_rules_allowed);
    return (_jsxs("div", { className: "mt-2 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", "data-testid": "resident-agi-repair-advisory-policy", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "AGI \u4FEE\u590D\u5EFA\u8BAE\u8FB9\u754C" }), _jsxs("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: [policy.schema_version || "director.repair_advisory_policy.v1", " \u00B7", " ", policy.source || "director.runtime.repair_kernel.advisory_policy"] })] }), _jsxs(Badge, { className: "border-slate-700 bg-slate-950/50 text-slate-300", children: ["\u5EFA\u8BAE\u89C4\u5219 ", formatAgiAllowed(suggestedRulesAllowed)] })] }), _jsxs("div", { className: "mt-2 grid gap-2 sm:grid-cols-4", children: [_jsx(CapabilityMetric, { label: "AGI \u6267\u884C", value: formatAgiAllowed(policy.agi_execution_authority) }), _jsx(CapabilityMetric, { label: "\u5199\u5165", value: formatAgiAllowed(policy.writes_allowed) }), _jsx(CapabilityMetric, { label: "\u6CE8\u518C", value: formatAgiAllowed(policy.registration_allowed) }), _jsx(CapabilityMetric, { label: "\u6743\u5A01\u56DE\u6267", value: formatAgiAllowed(policy.authoritative_receipts_allowed) })] }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", children: [allowedFields.slice(0, 8).map((field) => (_jsxs("span", { className: "rounded border border-slate-800 bg-slate-950/35 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: ["\u5141\u8BB8\u5B57\u6BB5\uFF1A", field] }, field))), forbiddenFields.slice(0, 8).map((field) => (_jsxs("span", { className: "rounded border border-slate-800 bg-slate-950/35 px-1.5 py-0.5 font-mono text-[10px] text-slate-500", children: ["\u7981\u6B62\u5B57\u6BB5\uFF1A", field] }, field)))] }), _jsxs("div", { className: "mt-2 font-mono text-[10px] text-slate-500", children: [policy.execution_boundary ||
                        "read_only_advisory_no_writes_no_registration", " ", "\u00B7 Director \u8FD0\u884C\u65F6\u4FDD\u6301\u6743\u5A01"] })] }));
}
function AgiRepairAdvisoryOverlayPanel({ overlay, source, }) {
    if (!overlay)
        return null;
    const advisorNotes = overlay.advisor_notes || [];
    const suggestedRuleCount = advisorNotes.reduce((count, note) => count + (note.suggested_rules?.length || 0), 0);
    return (_jsxs("div", { className: "mt-2 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", "data-testid": "resident-agi-repair-advisory-overlay", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "AGI \u4FEE\u590D\u5EFA\u8BAE\u8986\u76D6\u5C42" }), _jsxs("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: [overlay.schema_version ||
                                        "resident.agi_repair_advisory_overlay.v1", " ", "\u00B7", " ", overlay.director_runtime_contract ||
                                        "director.repair_advisory_policy.v1"] }), source && (_jsxs("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", "data-testid": "resident-agi-repair-advisory-overlay-source", children: ["\u6765\u6E90\uFF1A", source] }))] }), _jsx(Badge, { className: cn("border-slate-700 bg-slate-950/50 text-slate-300", overlay.status === "ready" &&
                            "border-slate-600 bg-slate-900 text-slate-100", overlay.status?.startsWith("invalid") &&
                            "border-slate-600 bg-slate-900 text-slate-100"), children: formatAgiUiToken(overlay.status || "unknown") })] }), _jsxs("div", { className: "mt-2 grid gap-2 sm:grid-cols-4", children: [_jsx(CapabilityMetric, { label: "\u6CE8\u5165", value: overlay.eligible_for_director_injection
                            ? formatAgiUiToken("eligible")
                            : formatAgiUiToken("blocked") }), _jsx(CapabilityMetric, { label: "\u53C2\u4E0E", value: overlay.participation_enabled
                            ? formatAgiUiToken("enabled")
                            : formatAgiUiToken("disabled") }), _jsx(CapabilityMetric, { label: "\u5EFA\u8BAE", value: String(advisorNotes.length) }), _jsx(CapabilityMetric, { label: "\u89C4\u5219", value: String(suggestedRuleCount) })] }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", children: [_jsxs("span", { className: "rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["\u4EC5\u5EFA\u8BAE\uFF1A", formatAgiBoolean(overlay.advisory_only)] }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["\u6743\u5A01\uFF1A", formatAgiBoolean(overlay.authoritative)] }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["AGI \u6267\u884C\uFF1A", formatAgiAllowed(overlay.agi_execution_authority)] })] }), (overlay.reason || overlay.error) && (_jsx("div", { className: "mt-2 text-[11px] text-slate-500", children: overlay.reason || overlay.error }))] }));
}
function AgiDecisionCapabilityRegistry({ schema, registry, decisions, }) {
    if (!registry && decisions.length === 0)
        return null;
    const counts = registry?.counts || {};
    const platformOwned = counts.platform_owned ?? 0;
    const agiOwned = counts.agi_owned ?? 0;
    const governedExecution = counts.governed_execution ?? 0;
    const evidenceInterfaces = counts.evidence_interfaces ?? registry?.evidence_interface_ids?.length ?? 0;
    const policy = registry?.decision_policy || {};
    return (_jsxs("div", { className: "mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", "data-testid": "resident-agi-decision-capability-registry", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "AGI \u51B3\u7B56\u80FD\u529B\u6CE8\u518C\u8868" }), _jsx("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: schema ||
                                    registry?.schema_version ||
                                    "resident.agi_decision_capability.v1" })] }), _jsxs(Badge, { className: "border-slate-700 bg-slate-950/50 text-slate-300", children: [counts.decisions ?? decisions.length, " \u4E2A\u51B3\u7B56"] })] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-4", children: [_jsx(CapabilityMetric, { label: "\u5E73\u53F0", value: String(platformOwned) }), _jsx(CapabilityMetric, { label: "AGI", value: String(agiOwned) }), _jsx(CapabilityMetric, { label: "\u53D7\u63A7", value: String(governedExecution) }), _jsx(CapabilityMetric, { label: "\u8BC1\u636E", value: String(evidenceInterfaces) })] }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", children: [Object.values(policy).map((policyValue) => (_jsx("span", { className: "rounded border border-slate-800 bg-slate-950/50 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: policyValue }, policyValue))), (registry?.candidate_actions || []).map((action) => (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["\u52A8\u4F5C\uFF1A", action] }, action)))] }), _jsx("div", { className: "mt-2 grid gap-2 lg:grid-cols-2", children: decisions.map((decision) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-xs font-medium text-slate-200", children: decision.name || decision.decision_id }), _jsx("span", { className: cn("shrink-0 rounded border px-1.5 py-0.5 text-[10px]", decision.platform_enforced
                                        ? "border-rose-500/20 bg-rose-500/10 text-rose-200"
                                        : "border-slate-700 bg-slate-950 text-slate-300"), children: decision.platform_enforced
                                        ? formatAgiUiToken("platform")
                                        : decision.owner })] }), _jsx("div", { className: "mt-1 line-clamp-2 text-[10px] text-slate-400", children: decision.decision_scope }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", children: [(decision.required_evidence_interfaces || [])
                                    .slice(0, 4)
                                    .map((interfaceId) => (_jsx("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: interfaceId }, interfaceId))), decision.risk_level && (_jsxs("span", { className: "rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200", children: ["\u98CE\u9669 ", formatAgiUiToken(decision.risk_level)] }))] })] }, decision.decision_id || decision.name))) })] }));
}
function evidenceInterfaceStatusClass(status) {
    const normalized = String(status || "").toLowerCase();
    if (normalized === "available") {
        return "border-slate-700 bg-slate-950 text-slate-300";
    }
    if (normalized === "metadata_only") {
        return "border-slate-800 bg-slate-950 text-slate-400";
    }
    if (normalized === "needs_public_facade" ||
        normalized === "governed_execute_only") {
        return "border-amber-500/20 bg-amber-500/10 text-amber-300";
    }
    return "border-rose-500/20 bg-rose-500/10 text-rose-300";
}
function AgiSelectedDecisionEvidencePanel({ decision, evidencePayload, contract, refreshing = false, onRefresh, }) {
    if (!decision?.decision_id)
        return null;
    const payloadDecisionType = String(evidencePayload?.decision_type || "").trim();
    const runtimePayloadMatchesDecision = Boolean(payloadDecisionType) &&
        payloadDecisionType === decision.decision_id;
    const runtimeInterfaces = evidencePayload?.interfaces || [];
    const contractInterfaces = contract?.interfaces || [];
    const interfaceById = new Map();
    contractInterfaces.forEach((item) => {
        if (item.interface_id)
            interfaceById.set(item.interface_id, item);
    });
    if (runtimePayloadMatchesDecision) {
        runtimeInterfaces.forEach((item) => {
            if (item.interface_id)
                interfaceById.set(item.interface_id, item);
        });
    }
    const required = decision.required_evidence_interfaces || [];
    const optional = decision.optional_evidence_interfaces || [];
    const rows = [
        ...required.map((interfaceId) => ({ interfaceId, required: true })),
        ...optional.map((interfaceId) => ({ interfaceId, required: false })),
    ];
    if (rows.length === 0)
        return null;
    const availableRequired = required.filter((interfaceId) => {
        const status = String(interfaceById.get(interfaceId)?.status || "");
        return status === "available";
    }).length;
    return (_jsxs("div", { className: "rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", "data-testid": "resident-agi-selected-decision-evidence", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "\u5F53\u524D\u51B3\u7B56\u8BC1\u636E\u9884\u68C0" }), _jsx("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: decision.decision_id })] }), _jsxs("div", { className: "flex flex-wrap items-center gap-1", children: [_jsx(Badge, { className: cn("border-amber-500/20 bg-amber-500/10 text-amber-200", runtimePayloadMatchesDecision &&
                                    "border-slate-700 bg-slate-950 text-slate-300"), children: runtimePayloadMatchesDecision ? "运行态已刷新" : "契约兜底" }), _jsxs(Badge, { className: "border-slate-700 bg-slate-950 text-slate-300", children: ["\u5FC5\u9700\u8BC1\u636E ", availableRequired, "/", required.length] }), onRefresh && (_jsxs(Button, { size: "sm", variant: "ghost", "data-testid": "resident-refresh-agi-evidence-interfaces", disabled: refreshing, onClick: onRefresh, className: "h-6 px-2 text-[10px] text-slate-300", children: [_jsx(RefreshCw, { className: cn("mr-1 size-3", refreshing && "animate-spin") }), "\u5237\u65B0"] }))] })] }), !runtimePayloadMatchesDecision && payloadDecisionType && (_jsxs("div", { className: "mt-2 rounded border border-amber-500/20 bg-amber-500/10 px-2 py-1 font-mono text-[10px] text-amber-200", children: ["\u8FD0\u884C\u6001\u8BC1\u636E\u5DF2\u8FC7\u671F\uFF1A", payloadDecisionType] })), _jsx("div", { className: "mt-2 grid gap-2 lg:grid-cols-2", children: rows.map(({ interfaceId, required }) => {
                    const item = interfaceById.get(interfaceId) || {};
                    const status = String(item.status || "unknown");
                    const source = String(item.source || item.contract_ref || "");
                    return (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate font-mono text-[10px] text-slate-200", children: interfaceId }), _jsx("span", { className: cn("shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px]", evidenceInterfaceStatusClass(status)), children: formatAgiUiToken(status) })] }), _jsxs("div", { className: "mt-1 flex flex-wrap gap-1", children: [_jsx("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: required ? "必需" : "可选" }), source && (_jsx("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: source }))] })] }, `${required ? "required" : "optional"}:${interfaceId}`));
                }) })] }));
}
function AgiEvidenceInterfaceReadiness({ payload, }) {
    if (!payload)
        return null;
    const summary = payload.summary || {};
    const interfaces = payload.interfaces || [];
    const matrix = payload.capability_matrix || null;
    const matrixSummary = matrix?.summary || {};
    const matrixGroups = matrix?.groups || [];
    if (interfaces.length === 0)
        return null;
    return (_jsxs("div", { className: "mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", "data-testid": "resident-agi-evidence-interface-readiness", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "AGI \u8BC1\u636E\u63A5\u53E3\u53EF\u7528\u6027" }), _jsxs("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: [payload.schema_version || "resident.agi_evidence_interfaces.v1", " \u00B7", " ", payload.decision_type || "platform_supervision"] })] }), _jsxs(Badge, { className: "border-slate-700 bg-slate-950 text-slate-300", children: ["\u53EF\u7528 ", summary.available ?? 0, "/", summary.total ?? interfaces.length] })] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-4", children: [_jsx(CapabilityMetric, { label: "Metadata", value: String(summary.metadata_only ?? 0) }), _jsx(CapabilityMetric, { label: "Facade gaps", value: String(summary.needs_public_facade ?? 0) }), _jsx(CapabilityMetric, { label: "Governed", value: String(summary.governed_execute_only ?? 0) }), _jsx(CapabilityMetric, { label: "Unavailable", value: String(summary.unavailable ?? 0) })] }), matrix && (_jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2", "data-testid": "resident-agi-evidence-runtime-matrix", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsx("span", { className: "font-mono text-[10px] text-slate-400", children: matrix.schema_version ||
                                    "resident.agi_evidence_capability_matrix.v1" }), _jsxs("span", { className: "text-[10px] text-slate-500", children: ["\u5FC5\u9700 ", matrixSummary.required_available ?? 0, "/", matrixSummary.required ?? 0, " \u00B7 \u63A8\u8350", " ", matrixSummary.recommended_now ?? 0] })] }), _jsx("div", { className: "mt-2 grid gap-1.5 sm:grid-cols-3", children: matrixGroups.slice(0, 6).map((group) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-[11px] text-slate-200", children: group.name || group.group_id || "group" }), _jsxs("span", { className: "font-mono text-[10px] text-slate-500", children: [group.available ?? 0, "/", group.total ?? 0] })] }), _jsxs("div", { className: "mt-1 flex flex-wrap gap-1", children: [(group.required ?? 0) > 0 && (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["\u5FC5\u9700\uFF1A", group.required] })), (group.missing_required ?? 0) > 0 && (_jsxs("span", { className: "rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[10px] text-amber-200", children: ["\u7F3A\u5931\uFF1A", group.missing_required] })), (group.governed_execute ?? 0) > 0 && (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["\u53D7\u63A7\uFF1A", group.governed_execute] }))] })] }, group.group_id || group.name))) }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", children: [_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: ["\u4EC5\u5EFA\u8BAE\uFF1A", formatAgiBoolean(matrixSummary.advisory_only ?? true)] }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: ["\u6743\u5A01\uFF1A", formatAgiBoolean(matrixSummary.authoritative ?? false)] }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: ["AGI \u6267\u884C\uFF1A", formatAgiAllowed(matrixSummary.agi_execution_authority)] })] })] })), _jsx("div", { className: "mt-2 grid gap-2 lg:grid-cols-2", children: interfaces.map((item) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-xs font-medium text-slate-200", children: item.name || item.interface_id || "未命名接口" }), _jsx("span", { className: cn("shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px]", evidenceInterfaceStatusClass(item.status)), children: formatAgiUiToken(item.status || "unknown") })] }), _jsxs("div", { className: "mt-1 truncate font-mono text-[10px] text-slate-500", children: [item.interface_id || "", " \u00B7 ", item.source || "unknown_source"] }), item.recommended_next_action && (_jsx("div", { className: "mt-1 truncate text-[10px] text-slate-400", children: item.recommended_next_action })), (item.gaps || []).length > 0 && (_jsx("div", { className: "mt-1 truncate text-[10px] text-amber-200/80", children: (item.gaps || [])[0] }))] }, item.interface_id || item.name))) })] }));
}
function isAgiEvidenceInterface(capability) {
    const category = String(capability.category || "").trim();
    const contractRef = String(capability.contract_ref || "").trim();
    return (AGI_EVIDENCE_INTERFACE_CATEGORIES.has(category) ||
        contractRef.startsWith("audit.") ||
        contractRef.startsWith("context.") ||
        contractRef.startsWith("control_plane.verifier") ||
        contractRef === "control_plane.run_ledger" ||
        contractRef === "roles.final_request_context_audit");
}
function AgiEvidenceInterfaceMatrix({ capabilities, contract, }) {
    const interfaces = capabilities.filter(isAgiEvidenceInterface);
    if (interfaces.length === 0)
        return null;
    const readOnly = interfaces.filter((capability) => String(capability.access || "").toLowerCase() === "read_only").length;
    const governedRequests = interfaces.filter((capability) => String(capability.access || "")
        .toLowerCase()
        .includes("execute")).length;
    const highRisk = interfaces.filter((capability) => String(capability.risk_level || "").toLowerCase() === "high").length;
    const contracts = uniqueStrings(interfaces.map((capability) => capability.contract_ref || ""));
    const declaredCount = contract?.declared_interface_ids?.length;
    const requiredCount = contract?.required_interface_ids?.length;
    const optionalCount = contract?.optional_interface_ids?.length;
    const missingIds = contract?.missing_interface_ids || [];
    const coverageComplete = contract?.coverage_complete;
    return (_jsxs("div", { className: "mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", "data-testid": "resident-agi-evidence-interface-matrix", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "AGI \u8BC1\u636E\u63A5\u53E3\u77E9\u9635" }), _jsx("div", { className: "mt-0.5 text-[10px] text-slate-500", children: "\u4EC5\u4F7F\u7528\u516C\u5F00 Cell \u5951\u7EA6" })] }), _jsx(Badge, { className: cn(coverageComplete === false
                            ? "border-amber-500/20 bg-amber-500/10 text-amber-300"
                            : "border-slate-700 bg-slate-950 text-slate-300"), children: coverageComplete === false ? "契约有缺口" : "契约已覆盖" })] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-4", children: [_jsx(CapabilityMetric, { label: "\u53EA\u8BFB", value: String(readOnly) }), _jsx(CapabilityMetric, { label: "\u53D7\u63A7\u8BF7\u6C42", value: String(governedRequests) }), _jsx(CapabilityMetric, { label: "\u9AD8\u98CE\u9669", value: String(highRisk) }), _jsx(CapabilityMetric, { label: "\u5DF2\u58F0\u660E", value: String(declaredCount ?? interfaces.length) })] }), contract && (_jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2", "data-testid": "resident-agi-evidence-interface-contract", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsx("span", { className: "font-mono text-[10px] text-slate-400", children: contract.schema_version ||
                                    "resident.agi_evidence_interface_contract.v1" }), _jsxs("span", { className: "text-[10px] text-slate-500", children: ["\u5FC5\u9700 ", requiredCount ?? 0, " \u00B7 \u53EF\u9009 ", optionalCount ?? 0, " \u00B7 \u7F3A\u5931", " ", missingIds.length] })] }), missingIds.length > 0 && (_jsxs("div", { className: "mt-1 truncate font-mono text-[10px] text-amber-300", children: ["\u7F3A\u5931\uFF1A", missingIds.slice(0, 4).join(", ")] }))] })), contracts.length > 0 && (_jsx("div", { className: "mt-2 flex flex-wrap gap-1", children: contracts.slice(0, 6).map((contractRef) => (_jsx("span", { className: "rounded border border-slate-700 bg-slate-950/50 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: contractRef }, contractRef))) })), _jsx("div", { className: "mt-2 grid gap-2 lg:grid-cols-2", children: interfaces.map((capability) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-xs font-medium text-slate-200", children: capability.name || capability.capability_id || "未命名接口" }), _jsx("span", { className: cn("shrink-0 rounded border px-1.5 py-0.5 text-[10px]", capability.risk_level === "high"
                                        ? "border-amber-500/20 bg-amber-500/10 text-amber-300"
                                        : "border-slate-700 bg-slate-950 text-slate-300"), children: formatAgiUiToken(capability.access || "read_only") })] }), _jsxs("div", { className: "mt-1 truncate font-mono text-[10px] text-slate-500", children: [capability.capability_id || "", " \u00B7", " ", capability.contract_ref || "unknown_contract"] }), (capability.evidence_refs || []).length > 0 && (_jsxs("div", { className: "mt-1 truncate font-mono text-[10px] text-slate-400", children: ["\u8BC1\u636E\uFF1A ", (capability.evidence_refs || []).slice(0, 2).join(", ")] }))] }, capability.capability_id || capability.name))) })] }));
}
function boundaryAuthorityLabel(authority) {
    const normalized = String(authority || "").toLowerCase();
    if (normalized === "platform_hard_rule")
        return "平台硬规则";
    if (normalized === "agi_governed_execution")
        return "AGI 受控执行";
    if (normalized === "agi_recommendation")
        return "AGI 智能判断";
    return authority || "未分类";
}
function boundaryAuthorityClass(authority) {
    const normalized = String(authority || "").toLowerCase();
    if (normalized === "platform_hard_rule")
        return "border-rose-500/20 bg-rose-500/10 text-rose-300";
    if (normalized === "agi_governed_execution")
        return "border-amber-500/20 bg-amber-500/10 text-amber-300";
    if (normalized === "agi_recommendation")
        return "border-cyan-500/20 bg-cyan-500/10 text-cyan-200";
    return "border-slate-700 bg-slate-900 text-slate-300";
}
function countBoundariesByAuthority(boundaries, authority) {
    return boundaries.filter((boundary) => boundary.authority === authority)
        .length;
}
function DecisionBoundaryMatrix({ schema, boundaries, }) {
    if (boundaries.length === 0)
        return null;
    return (_jsxs("div", { className: "mt-3 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2", "data-testid": "resident-agi-decision-boundaries", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-100", children: "AGI \u51B3\u7B56\u8FB9\u754C" }), _jsx("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: schema || "resident.agi_decision_boundary.v1" })] }), _jsxs("div", { className: "flex flex-wrap gap-1", children: [_jsxs(Badge, { className: "border-rose-500/20 bg-rose-500/10 text-rose-300", children: ["\u786C\u89C4\u5219", " ", countBoundariesByAuthority(boundaries, "platform_hard_rule")] }), _jsxs(Badge, { className: "border-cyan-500/20 bg-cyan-500/10 text-cyan-200", children: ["\u667A\u80FD\u5224\u65AD", " ", countBoundariesByAuthority(boundaries, "agi_recommendation")] }), _jsxs(Badge, { className: "border-amber-500/20 bg-amber-500/10 text-amber-300", children: ["\u53D7\u63A7\u6267\u884C", " ", countBoundariesByAuthority(boundaries, "agi_governed_execution")] })] })] }), _jsx("div", { className: "mt-3 grid gap-2 lg:grid-cols-2", children: boundaries.map((boundary) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-xs font-medium text-slate-200", children: boundary.name || "未命名边界" }), _jsx("span", { className: cn("shrink-0 rounded border px-1.5 py-0.5 text-[10px]", boundaryAuthorityClass(boundary.authority)), children: boundaryAuthorityLabel(boundary.authority) })] }), _jsxs("div", { className: "mt-1 line-clamp-2 text-[11px] text-slate-500", title: boundary.platform_hard_rule || "", children: ["\u786C\u7EA6\u675F: ", boundary.platform_hard_rule || "未声明"] }), _jsxs("div", { className: "mt-1 line-clamp-2 text-[11px] text-slate-400", title: boundary.agi_decision_scope || "", children: ["AGI: ", boundary.agi_decision_scope || "未声明"] }), (boundary.evidence_required || []).length > 0 && (_jsxs("div", { className: "mt-1 truncate font-mono text-[10px] text-slate-500", children: ["\u8BC1\u636E\uFF1A", " ", (boundary.evidence_required || []).slice(0, 3).join(", ")] }))] }, boundary.boundary_id || boundary.name))) })] }));
}
function DecisionBoundaryPolicyPanel({ policy, }) {
    if (!policy)
        return null;
    const counts = policy.counts || {};
    const executionPolicy = policy.capability_execution_policy || {};
    const modes = Object.entries(policy.decision_modes || {});
    const boundaryPolicies = policy.boundary_policies || [];
    const nonOverridable = policy.non_overridable_rules || [];
    const agiJudgement = policy.agi_judgement_boundaries || [];
    const governedExecution = policy.governed_execution_boundaries || [];
    return (_jsxs("div", { className: "mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", "data-testid": "resident-agi-decision-boundary-policy", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "AGI \u51B3\u7B56\u8FB9\u754C\u7B56\u7565" }), _jsxs("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: [policy.schema_version ||
                                        "resident.agi_decision_boundary_policy.v1", " ", "\u00B7 ", policy.source || "resident.autonomy.capability_surface"] })] }), _jsx(Badge, { className: "border-slate-700 bg-slate-950 text-slate-300", children: policy.chain || "PM → Chief Engineer → Director" })] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-4", children: [_jsx(CapabilityMetric, { label: "Hard rules", value: String(counts.platform_hard_rules ?? nonOverridable.length) }), _jsx(CapabilityMetric, { label: "AGI judgement", value: String(counts.agi_judgement ?? agiJudgement.length) }), _jsx(CapabilityMetric, { label: "Governed", value: String(counts.governed_execution ?? governedExecution.length) }), _jsx(CapabilityMetric, { label: "High risk", value: String(counts.high_risk_capabilities ?? 0) })] }), _jsx("div", { className: "mt-2 grid gap-2 lg:grid-cols-3", children: modes.map(([modeId, mode]) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate font-mono text-[10px] text-slate-200", children: modeId }), _jsxs("span", { className: cn("shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px]", mode.llm_decision_allowed
                                        ? "border-slate-700 bg-slate-950 text-slate-300"
                                        : "border-rose-500/20 bg-rose-500/10 text-rose-200"), children: ["LLM\uFF1A", formatAgiAllowed(mode.llm_decision_allowed)] })] }), _jsxs("div", { className: "mt-1 truncate text-[10px] text-slate-500", children: ["\u8D23\u4EFB\u65B9\uFF1A", mode.owner || formatAgiUiToken("unknown")] }), _jsxs("div", { className: "mt-1 flex flex-wrap gap-1", children: [_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: ["\u6267\u884C\uFF1A", formatAgiUiToken(mode.execution_authority || "none")] }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: ["\u8986\u76D6\uFF1A", formatAgiBoolean(mode.override_allowed ?? false)] })] })] }, modeId))) }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", children: [_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: ["AGI \u76F4\u63A5\u5199\u5165\uFF1A", formatAgiAllowed(executionPolicy.agi_direct_writes_allowed)] }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: ["AGI \u76F4\u63A5\u5DE5\u5177\uFF1A", formatAgiAllowed(executionPolicy.agi_direct_tool_execution_allowed)] }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: ["Director \u6743\u5A01\uFF1A", executionPolicy.director_runtime_remains_authoritative
                                ? "保留"
                                : formatAgiUiToken("unknown")] })] }), boundaryPolicies.length > 0 && (_jsx("div", { className: "mt-2 grid gap-2 lg:grid-cols-2", children: boundaryPolicies.slice(0, 4).map((item) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-[11px] font-medium text-slate-200", children: item.name || item.boundary_id || "boundary" }), _jsx("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: item.execution_authority || "none" })] }), _jsxs("div", { className: "mt-1 truncate font-mono text-[10px] text-slate-500", children: ["\u8D23\u4EFB\u65B9\uFF1A", item.decision_owner || formatAgiUiToken("unknown"), " \u00B7 \u9ED8\u8BA4\u52A8\u4F5C\uFF1A", formatAgiUiToken(item.default_action || "request_evidence")] })] }, item.boundary_id || item.name))) }))] }));
}
function AgiAuditPackPanel({ pack, }) {
    if (!pack) {
        return (_jsx("div", { className: "mt-3 rounded-lg border border-dashed border-slate-700 bg-slate-950/40 px-3 py-2 text-xs text-slate-500", "data-testid": "resident-agi-audit-pack", children: "AGI \u5BA1\u8BA1\u5305\u5C1A\u672A\u52A0\u8F7D" }));
    }
    const roleRegistry = pack.role_registry;
    const missingRoles = roleRegistry?.missing_required_roles || [];
    const evidenceRefs = pack.evidence_refs || [];
    const recentDecisions = pack.recent_decisions || [];
    const constraints = pack.execution_constraints || [];
    const boundaryIds = pack.boundary_summary?.boundary_ids || [];
    const hardRuleGate = pack.hard_rule_gate;
    const hardRuleStatus = String(hardRuleGate?.status || "unknown").toLowerCase();
    const hardRuleFailedChecks = hardRuleGate?.failed_check_ids || [];
    const evidenceGate = pack.evidence_gate;
    const evidenceGateStatus = String(evidenceGate?.status || "unknown").toLowerCase();
    const decisionProfile = pack.decision_profile;
    const runLedgerSummary = pack.run_ledger_summary;
    const authorityMatrix = pack.authority_matrix;
    const authorityCounts = authorityMatrix?.counts || {};
    const directorRepairContract = pack.director_repair_contract;
    const directorRepairAdvisory = directorRepairContract?.agi_advisory || {};
    const capabilityIds = (pack.capability_surface?.items || [])
        .map((capability) => capability.capability_id || "")
        .filter(Boolean);
    return (_jsxs("div", { className: "mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2", "data-testid": "resident-agi-audit-pack", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "AGI \u5BA1\u8BA1\u5305" }), _jsx("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: pack.schema_version || "resident.agi_audit_pack.v1" })] }), _jsxs(Badge, { className: cn(hardRuleStatus === "pass"
                            ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300"
                            : "border-rose-500/20 bg-rose-500/10 text-rose-300"), children: ["\u786C\u89C4\u5219\u95E8\u7981 ", formatAgiUiToken(hardRuleStatus)] })] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-4", children: [_jsx(CapabilityMetric, { label: "\u5BF9\u8BDD\u89D2\u8272", value: String(roleRegistry?.dialogue_roles?.length ?? 0) }), _jsx(CapabilityMetric, { label: "\u9002\u914D\u5668\u89D2\u8272", value: String(roleRegistry?.adapter_roles?.length ?? 0) }), _jsx(CapabilityMetric, { label: "\u8BC1\u636E\u95E8\u7981", value: formatAgiUiToken(evidenceGateStatus) }), _jsx(CapabilityMetric, { label: "\u786C\u68C0\u67E5", value: String(hardRuleGate?.checks?.length ?? 0) })] }), authorityMatrix && (_jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2", "data-testid": "resident-agi-audit-authority-matrix", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "\u6743\u5A01\u77E9\u9635" }), _jsx(Badge, { className: "border-slate-700 bg-slate-950 text-slate-300", children: authorityMatrix.chain_required
                                    ? formatAgiRoleChain(authorityMatrix.chain || "PM → Chief Engineer → Director")
                                    : formatAgiUiToken("read_only") })] }), _jsxs("div", { className: "mt-1 font-mono text-[10px] text-slate-400", children: [authorityMatrix.schema_version ||
                                "resident.agi_authority_matrix.v1", " ", "\u00B7 \u786C\u89C4\u5219 ", authorityCounts.platform_hard_rules ?? 0, " \u00B7 AGI \u5224\u65AD", " ", authorityCounts.agi_recommendations ?? 0, " \u00B7 \u53D7\u63A7\u64CD\u4F5C", " ", authorityCounts.governed_operation_capabilities ?? 0] })] })), _jsxs("div", { className: "mt-2 grid gap-2 lg:grid-cols-2", children: [_jsxs("div", { className: "rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2", children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "\u6267\u884C\u7EA6\u675F" }), _jsx("div", { className: "mt-1 space-y-1", children: constraints.slice(0, 4).map((constraint) => (_jsx("div", { className: "text-[11px] text-slate-300", children: constraint }, constraint))) })] }), _jsxs("div", { className: "rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2", children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "\u5BA1\u8BA1\u6765\u6E90" }), _jsxs("div", { className: "mt-1 flex flex-wrap gap-1", children: [(pack.truth_sources || []).slice(0, 6).map((source) => (_jsx("span", { className: "rounded bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: source }, source))), boundaryIds.slice(0, 4).map((boundaryId) => (_jsx("span", { className: "rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: boundaryId }, boundaryId))), capabilityIds.slice(0, 4).map((capabilityId) => (_jsx("span", { className: "rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: capabilityId }, capabilityId)))] })] })] }), directorRepairContract && (_jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2", "data-testid": "resident-agi-director-repair-contract", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "Director \u4FEE\u590D\u5951\u7EA6" }), _jsx("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: directorRepairContract.schema_version ||
                                            "resident.agi_director_repair_contract.v1" })] }), _jsx(Badge, { className: "border-slate-700 bg-slate-950 text-slate-300", children: directorRepairContract.owner_cell || "director.runtime" })] }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", children: [_jsx("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: directorRepairContract.execution_boundary ||
                                    "director_authorized_tools_only" }), _jsx("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 text-[10px] text-slate-300", children: formatAgiRoleChain(directorRepairContract.chain ||
                                    "PM → Chief Engineer → Director") }), _jsx("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: directorRepairContract.unknown_source_tool_policy ||
                                    "fail_closed_high_risk" }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["AGI \u6267\u884C\uFF1A", formatAgiAllowed(directorRepairContract.agi_execution_authority)] }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["\u5199\u5165\uFF1A", formatAgiAllowed(directorRepairAdvisory.writes_allowed)] }), _jsxs("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["\u5EFA\u8BAE\uFF1A", formatAgiActive(directorRepairAdvisory.active)] })] }), _jsxs("div", { className: "mt-2 grid gap-2 sm:grid-cols-3", children: [_jsx(CapabilityMetric, { label: "\u7B56\u7565\u6570", value: String(directorRepairContract.strategy_count ?? 0) }), _jsx(CapabilityMetric, { label: "\u76EE\u5F55", value: directorRepairContract.catalog_schema ||
                                    "director.deterministic_repair_strategy_catalog.v1" }), _jsx(CapabilityMetric, { label: "\u753B\u50CF", value: directorRepairContract.profile_summary_schema ||
                                    "director.deterministic_repair_profile_summary.v1" })] })] })), _jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "\u8BC1\u636E\u95E8\u7981" }), _jsxs(Badge, { className: cn(evidenceGateStatus === "pass"
                                    ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300"
                                    : evidenceGateStatus === "fail"
                                        ? "border-rose-500/20 bg-rose-500/10 text-rose-300"
                                        : "border-amber-500/20 bg-amber-500/10 text-amber-300"), children: [formatAgiUiToken(evidenceGateStatus), " \u2192", " ", formatAgiUiToken(evidenceGate?.recommended_verdict || "request_evidence")] })] }), _jsx("div", { className: "mt-1 text-[11px] text-slate-400", children: evidenceGate?.reason || "暂无证据门说明" }), _jsxs("div", { className: "mt-1 font-mono text-[10px] text-slate-500", children: ["\u8FD0\u884C\u8D26\u672C ", formatAgiUiToken(runLedgerSummary?.status || "unknown"), " \u00B7 \u5DF2\u6295\u5F71 ", runLedgerSummary?.projected ?? 0, "/", runLedgerSummary?.total ?? 0, " \u00B7 \u5931\u8D25 ", runLedgerSummary?.failed ?? 0, " ", "\u00B7 \u4E0A\u4E0B\u6587\u5F15\u7528", " ", evidenceGate?.context_snapshot_ref_count ?? evidenceRefs.length] })] }), _jsx(AgiDecisionProfilePanel, { profile: decisionProfile, testId: "resident-agi-audit-decision-profile" }), missingRoles.length > 0 && (_jsxs("div", { className: "mt-2 rounded border border-rose-500/20 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200", children: ["\u7F3A\u5931\u89D2\u8272\uFF1A", missingRoles.join(", ")] })), hardRuleFailedChecks.length > 0 && (_jsxs("div", { className: "mt-2 rounded border border-rose-500/20 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200", children: ["\u5931\u8D25\u786C\u89C4\u5219\uFF1A", hardRuleFailedChecks.join(", ")] })), _jsxs("div", { className: "mt-2 text-[10px] text-slate-500", children: ["\u6700\u8FD1\u51B3\u7B56\uFF1A", recentDecisions.length, " \u00B7 LLM \u8986\u76D6\uFF1A", formatAgiAllowed(hardRuleGate?.llm_override_allowed)] }), pack.decision_endpoint && (_jsxs("div", { className: "mt-2 font-mono text-[10px] text-slate-500", children: ["\u51B3\u7B56\u5165\u53E3\uFF1A", pack.decision_endpoint] }))] }));
}
function AgiDecisionProfilePanel({ profile, testId, }) {
    if (!profile)
        return null;
    const roleTurnAllowed = profile.role_turn_allowed !== false;
    const downstreamPrecheck = profile.downstream_precheck || "unknown";
    const recommendedVerdict = profile.recommended_verdict || "request_evidence";
    const nextAction = profile.recommended_next_action || "request_missing_evidence";
    const candidateActions = profile.candidate_actions || [];
    const requiredConstraints = profile.required_constraints || [];
    const requiredEvidence = profile.required_evidence || [];
    const evidenceInterfaceRecommendations = profile.evidence_interface_recommendations || [];
    const contractRefs = profile.contract_refs || [];
    return (_jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/35 px-2.5 py-2", "data-testid": testId, children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "AGI \u6267\u884C\u753B\u50CF" }), _jsx("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: profile.schema_version || "resident.agi_decision_profile.v1" })] }), _jsxs(Badge, { className: cn(roleTurnAllowed
                            ? "border-slate-700 bg-slate-950 text-slate-300"
                            : "border-rose-500/20 bg-rose-500/10 text-rose-300"), children: ["\u89D2\u8272\u56DE\u5408 ", formatAgiAllowed(roleTurnAllowed)] })] }), _jsxs("div", { className: "mt-2 grid gap-2 sm:grid-cols-4", children: [_jsx(CapabilityMetric, { label: "\u5E95\u5EA7", value: profile.runtime_foundation || "RoleRuntime / ContextOS / TurnEngine" }), _jsx(CapabilityMetric, { label: "\u9884\u68C0", value: formatAgiUiToken(downstreamPrecheck) }), _jsx(CapabilityMetric, { label: "\u88C1\u51B3", value: formatAgiUiToken(recommendedVerdict) }), _jsx(CapabilityMetric, { label: "\u4E0B\u4E00\u6B65", value: formatAgiUiToken(nextAction) })] }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", children: [candidateActions.map((action) => (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: ["\u52A8\u4F5C\uFF1A", formatAgiUiToken(action)] }, action))), requiredConstraints.map((constraint) => (_jsx("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: constraint }, constraint))), requiredEvidence.slice(0, 4).map((evidence) => (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: ["\u8BC1\u636E\uFF1A", evidence] }, evidence))), contractRefs.slice(0, 4).map((contractRef) => (_jsx("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-400", children: contractRef }, contractRef)))] }), evidenceInterfaceRecommendations.length > 0 && (_jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "\u8BC1\u636E\u63A5\u53E3" }), _jsxs(Badge, { className: "border-slate-700 bg-slate-950 text-slate-300", children: [evidenceInterfaceRecommendations.filter((recommendation) => recommendation.recommended_now).length, " ", "\u4E2A\u63A8\u8350"] })] }), _jsx("div", { className: "mt-2 grid gap-1.5 lg:grid-cols-2", children: evidenceInterfaceRecommendations
                            .slice(0, 6)
                            .map((recommendation) => (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-[11px] font-medium text-slate-200", children: recommendation.name ||
                                                recommendation.capability_id ||
                                                "证据接口" }), _jsx("span", { className: cn("shrink-0 rounded border px-1.5 py-0.5 text-[10px]", recommendation.recommended_now
                                                ? "border-slate-700 bg-slate-950 text-slate-300"
                                                : "border-slate-700 bg-slate-950 text-slate-400"), children: recommendation.recommended_now ? "现在" : "稍后" })] }), _jsxs("div", { className: "mt-1 truncate font-mono text-[10px] text-slate-500", children: [recommendation.capability_id || "", " \u00B7", " ", recommendation.contract_ref || ""] }), recommendation.reason && (_jsx("div", { className: "mt-1 line-clamp-2 text-[10px] text-slate-400", children: recommendation.reason }))] }, recommendation.capability_id ||
                            recommendation.contract_ref ||
                            recommendation.name))) })] }))] }));
}
function AgiDecisionHandoffPanel({ handoff, }) {
    if (!handoff)
        return null;
    const status = String(handoff.handoff_status || "hold").toLowerCase();
    const targetRoles = handoff.target_roles || [];
    const blockedActions = handoff.blocked_actions || [];
    return (_jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/35 px-2.5 py-2", "data-testid": "resident-agi-decision-handoff", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-200", children: "AGI \u51B3\u7B56\u4EA4\u63A5" }), _jsx("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: handoff.schema_version || "resident.agi_decision_handoff.v1" })] }), _jsx(Badge, { className: cn(status === "ready"
                            ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300"
                            : status === "blocked"
                                ? "border-rose-500/20 bg-rose-500/10 text-rose-300"
                                : "border-amber-500/20 bg-amber-500/10 text-amber-300"), children: formatAgiUiToken(status) })] }), _jsxs("div", { className: "mt-2 grid gap-2 sm:grid-cols-3", children: [_jsx(CapabilityMetric, { label: "\u76EE\u6807\u89D2\u8272", value: targetRoles.join(" → ") || formatAgiUiToken("hold") }), _jsx(CapabilityMetric, { label: "\u4E0B\u6E38", value: formatAgiAllowed(handoff.downstream_allowed) }), _jsx(CapabilityMetric, { label: "AGI \u6267\u884C", value: formatAgiAllowed(handoff.agi_execution_authority) })] }), _jsx("div", { className: "mt-2 line-clamp-2 text-[11px] text-slate-400", children: handoff.reason || "等待 AGI 决策交接说明" }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-1", children: [(handoff.allowed_actions || []).slice(0, 5).map((action) => (_jsx("span", { className: "rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: action }, action))), blockedActions.slice(0, 5).map((action) => (_jsxs("span", { className: "rounded border border-rose-700/30 bg-rose-950/20 px-1.5 py-0.5 font-mono text-[10px] text-rose-200/80", children: ["\u5DF2\u963B\u65AD\uFF1A", action] }, action)))] }), _jsxs("div", { className: "mt-2 font-mono text-[10px] text-slate-500", children: ["\u94FE\u8DEF\uFF1A", formatAgiRoleChain(handoff.required_chain || "PM → Chief Engineer → Director"), " ", "\u00B7 \u4EC5\u5EFA\u8BAE\uFF1A", formatAgiBoolean(handoff.advisory_only !== false)] })] }));
}
function AgiHandoffInboxPanel({ inbox, }) {
    if (!inbox || (inbox.items || []).length === 0)
        return null;
    const items = inbox.items || [];
    const summary = inbox.summary || {};
    const byStatus = summary.by_status || {};
    return (_jsxs("div", { className: "mt-2 rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2", "data-testid": "resident-agi-handoff-inbox", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-medium text-slate-100", children: "AGI \u4EA4\u63A5\u961F\u5217" }), _jsx("div", { className: "mt-0.5 font-mono text-[10px] text-slate-500", children: inbox.schema_version || "resident.agi_handoff_inbox.v1" })] }), _jsxs(Badge, { className: "border-slate-700 bg-slate-900 text-slate-300", children: [items.length, " \u4E2A\u4EA4\u63A5"] })] }), _jsxs("div", { className: "mt-2 grid gap-2 sm:grid-cols-4", children: [_jsx(CapabilityMetric, { label: "\u5C31\u7EEA", value: String(byStatus.ready ?? 0) }), _jsx(CapabilityMetric, { label: "\u6682\u7F13", value: String(byStatus.hold ?? 0) }), _jsx(CapabilityMetric, { label: "\u963B\u65AD", value: String(byStatus.blocked ?? 0) }), _jsx(CapabilityMetric, { label: "AGI \u6267\u884C", value: formatAgiAllowed(summary.agi_execution_authority) })] }), _jsx("div", { className: "mt-2 space-y-1.5", children: items.slice(0, 4).map((item) => {
                    const handoff = item.handoff || {};
                    const targetRoles = handoff.target_roles || [];
                    return (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-[11px] font-medium text-slate-200", children: item.summary || handoff.reason || item.decision_id }), _jsx("span", { className: "rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300", children: formatAgiUiToken(handoff.handoff_status || "hold") })] }), _jsxs("div", { className: "mt-1 truncate font-mono text-[10px] text-slate-500", children: [targetRoles.join(" → ") || "resident_agi", " \u00B7", " ", formatAgiRoleChain(handoff.required_chain || "PM → Chief Engineer → Director")] })] }, item.decision_id || item.timestamp));
                }) })] }));
}
// Simplified Goal Item
function GoalItem({ goal, execution, expanded, onToggle, onApprove, onReject, onMaterialize, onStage, onPromoteToPm, onRun, disabled, }) {
    const status = goal.status || "pending";
    const isPending = status === "pending";
    const isApproved = status === "approved" || status === "materialized";
    return (_jsxs(Card, { className: cn("border-slate-800 bg-slate-900/50", expanded && "border-slate-700"), children: [_jsxs("div", { className: "flex cursor-pointer items-center justify-between p-3", onClick: onToggle, children: [_jsxs("div", { className: "flex items-center gap-3", children: [expanded ? (_jsx(ChevronDown, { className: "size-4 text-slate-400" })) : (_jsx(ChevronRight, { className: "size-4 text-slate-400" })), _jsxs("div", { className: "flex-1", children: [_jsx("div", { className: "font-medium text-slate-200", children: goal.title || "未命名目标" }), execution ? (_jsx("div", { className: "mt-1", children: _jsx(ExecutionProgressBar, { execution: execution, compact: true }) })) : (_jsx("div", { className: "text-xs text-slate-500", children: formatTime(goal.updated_at) }))] })] }), _jsx(GoalStatusBadge, { status: status })] }), expanded && (_jsxs("div", { className: "border-t border-slate-800 px-3 pb-3", children: [_jsx("div", { className: "pt-3 text-sm text-slate-400", children: goal.motivation || "暂无描述" }), execution && (_jsx("div", { className: "mt-3 rounded bg-slate-950 p-3", children: _jsx(ExecutionProgressBar, { execution: execution }) })), _jsxs("div", { className: "mt-3 flex gap-2", children: [isPending && (_jsxs(_Fragment, { children: [_jsxs(Button, { size: "sm", onClick: onApprove, disabled: disabled, className: "bg-slate-100 text-slate-950 hover:bg-white", children: [_jsx(CheckCircle2, { className: "mr-1 size-3" }), "\u6279\u51C6"] }), _jsxs(Button, { size: "sm", variant: "outline", "data-testid": "resident-reject-goal", onClick: onReject, disabled: disabled, className: "border-rose-500/30 text-rose-300 hover:bg-rose-500/10", children: [_jsx(Ban, { className: "mr-1 size-3" }), "\u62D2\u7EDD"] })] })), isApproved && (_jsxs(_Fragment, { children: [status === "approved" && (_jsxs(Button, { size: "sm", variant: "outline", "data-testid": "resident-materialize-goal", onClick: onMaterialize, disabled: disabled, children: [_jsx(Package, { className: "mr-1 size-3" }), "\u56FA\u5316"] })), _jsx(Button, { size: "sm", variant: "outline", onClick: onStage, disabled: disabled, children: "\u6682\u5B58" }), _jsx(Button, { size: "sm", variant: "outline", onClick: onPromoteToPm, disabled: disabled, children: "\u5199\u5165 PM" }), _jsxs(Button, { size: "sm", onClick: onRun, disabled: disabled, className: "bg-slate-100 text-slate-950 hover:bg-white", children: [_jsx(Play, { className: "mr-1 size-3" }), "\u4EA4\u7ED9 PM"] })] }))] })] }))] }));
}
function decisionString(value) {
    if (typeof value === "string")
        return value.trim();
    if (typeof value === "number" || typeof value === "boolean")
        return String(value);
    return "";
}
function decisionNumber(value) {
    if (typeof value === "number" && Number.isFinite(value))
        return value;
    if (typeof value === "string" && value.trim()) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
}
function decisionStringList(value) {
    if (!Array.isArray(value))
        return [];
    return value.map(decisionString).filter(Boolean);
}
function decisionObject(value) {
    if (!value || typeof value !== "object" || Array.isArray(value))
        return {};
    return value;
}
function toRepairAdvisoryOverlay(value) {
    const overlay = decisionObject(value);
    if (!Object.keys(overlay).length)
        return null;
    const schema = decisionString(overlay.schema_version);
    const status = decisionString(overlay.status);
    const notes = overlay.advisor_notes;
    if (schema !== "resident.agi_repair_advisory_overlay.v1" &&
        !status &&
        !Array.isArray(notes)) {
        return null;
    }
    return overlay;
}
function latestDecisionRepairAdvisoryOverlay(decisions) {
    const ordered = decisions
        .map((decision, index) => ({
        decision,
        index,
        timestamp: Date.parse(decision.timestamp || ""),
    }))
        .sort((left, right) => {
        const leftTime = Number.isFinite(left.timestamp)
            ? left.timestamp
            : left.index;
        const rightTime = Number.isFinite(right.timestamp)
            ? right.timestamp
            : right.index;
        return rightTime - leftTime;
    });
    for (const item of ordered) {
        const actual = decisionObject(item.decision.actual_outcome);
        const overlay = toRepairAdvisoryOverlay(actual.resident_agi_repair_advisory_overlay) ||
            toRepairAdvisoryOverlay(actual.repair_advisory_overlay);
        if (!overlay)
            continue;
        const sourceId = shortDecisionId(item.decision.decision_id);
        return {
            overlay,
            source: sourceId ? `decision_trace:${sourceId}` : "decision_trace",
        };
    }
    return null;
}
function shortDecisionId(value) {
    const token = String(value || "").trim();
    if (!token)
        return "";
    if (token.length <= 14)
        return token;
    return `${token.slice(0, 10)}...${token.slice(-4)}`;
}
function formatConfidence(value) {
    if (typeof value !== "number" || !Number.isFinite(value))
        return "暂无";
    return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}
function decisionHasEvidence(decision) {
    return Boolean(decision.evidence_bundle_id ||
        (decision.evidence_refs || []).length > 0 ||
        (decision.context_refs || []).length > 0 ||
        (decision.affected_files || []).length > 0 ||
        (decision.affected_symbols || []).length > 0);
}
function decisionHasHandoffImpact(decision) {
    const haystack = [
        decision.stage,
        decision.goal_id,
        decision.task_id,
        ...(decision.strategy_tags || []),
        ...(decision.evidence_refs || []),
        decisionString(decision.actual_outcome?.pm_run_id),
        decisionString(decision.actual_outcome?.pm_contract_path),
        decisionString(decision.actual_outcome?.promoted_to_pm_runtime),
    ]
        .join(" ")
        .toLowerCase();
    return [
        "handoff",
        "goal_staging",
        "pm_bridge",
        "pm_runtime",
        "pm_contract",
        "chief_engineer",
        "director",
    ].some((token) => haystack.includes(token));
}
function decisionRuntimeContractGate(decision) {
    return decisionObject(decision.actual_outcome?.resident_agi_runtime_contract_gate);
}
function decisionHasRuntimeContractReceipt(decision) {
    const gate = decisionRuntimeContractGate(decision);
    return gate.passed === true || decisionString(gate.status) === "pass";
}
function decisionHasRuntimeContractFailure(decision) {
    const gate = decisionRuntimeContractGate(decision);
    const status = decisionString(gate.status);
    return (status === "fail" ||
        decisionStringList(gate.failed_check_ids).length > 0 ||
        (gate.passed === false && Boolean(gate.required)));
}
function buildDecisionStats(decisions) {
    const total = decisions.length;
    const evidenceBacked = decisions.filter(decisionHasEvidence).length;
    const handoffImpact = decisions.filter(decisionHasHandoffImpact).length;
    const runtimeReceipts = decisions.filter(decisionHasRuntimeContractReceipt).length;
    const runtimeContractFailures = decisions.filter(decisionHasRuntimeContractFailure).length;
    const blockedOrFailed = decisions.filter((decision) => {
        const verdict = String(decision.verdict || "").toLowerCase();
        const actual = decision.actual_outcome || {};
        const blockers = decisionStringList(actual.hard_rule_blockers);
        return (verdict === "failure" || verdict === "blocked" || blockers.length > 0);
    }).length;
    return {
        total,
        evidenceBacked,
        handoffImpact,
        runtimeReceipts,
        runtimeContractFailures,
        blockedOrFailed,
    };
}
function DecisionAuditSummary({ stats, }) {
    return (_jsxs("div", { className: "rounded-lg border border-slate-800 bg-slate-900/40 p-3", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium text-slate-200", children: [_jsx(FileSearch, { className: "size-4 text-slate-400" }), "\u51B3\u7B56\u5BA1\u8BA1\u9762"] }), _jsx("div", { className: "mt-1 text-xs text-slate-500", children: "\u552F\u4E00\u4E8B\u5B9E\u6E90\uFF1Adecision_trace.jsonl" })] }), _jsx(Badge, { className: "border-slate-700 bg-slate-950/50 text-slate-300", children: "resident.decision_event.v1" })] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-5", children: [_jsx(DecisionMetric, { label: "\u51B3\u7B56", value: String(stats.total), tone: "neutral" }), _jsx(DecisionMetric, { label: "\u8BC1\u636E", value: String(stats.evidenceBacked), tone: "cyan" }), _jsx(DecisionMetric, { label: "\u4EA4\u63A5", value: String(stats.handoffImpact), tone: "emerald" }), _jsx(DecisionMetric, { label: "\u8FD0\u884C\u65F6", value: String(stats.runtimeReceipts), tone: stats.runtimeContractFailures ? "amber" : "cyan" }), _jsx(DecisionMetric, { label: "\u963B\u65AD", value: String(stats.blockedOrFailed), tone: stats.blockedOrFailed ? "amber" : "neutral" })] }), _jsxs("div", { className: "mt-3 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2", children: [_jsx(SegmentedMeter, { segments: [
                            {
                                label: "证据",
                                value: stats.evidenceBacked,
                                className: "bg-slate-300",
                            },
                            {
                                label: "运行时",
                                value: stats.runtimeReceipts,
                                className: "bg-slate-500",
                            },
                            {
                                label: "阻断",
                                value: stats.blockedOrFailed,
                                className: "bg-amber-300/75",
                            },
                        ] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-3", children: [_jsxs("div", { className: "space-y-1", children: [_jsxs("div", { className: "flex justify-between text-[10px] text-slate-500", children: [_jsx("span", { children: "\u8BC1\u636E\u8986\u76D6" }), _jsx("span", { children: formatPercent(ratioPercent(stats.evidenceBacked, stats.total)) })] }), _jsx(ProgressTrack, { value: ratioPercent(stats.evidenceBacked, stats.total) })] }), _jsxs("div", { className: "space-y-1", children: [_jsxs("div", { className: "flex justify-between text-[10px] text-slate-500", children: [_jsx("span", { children: "\u8FD0\u884C\u65F6\u56DE\u6267" }), _jsx("span", { children: formatPercent(ratioPercent(stats.runtimeReceipts, stats.total)) })] }), _jsx(ProgressTrack, { value: ratioPercent(stats.runtimeReceipts, stats.total) })] }), _jsxs("div", { className: "space-y-1", children: [_jsxs("div", { className: "flex justify-between text-[10px] text-slate-500", children: [_jsx("span", { children: "\u963B\u585E/\u5931\u8D25" }), _jsx("span", { children: formatPercent(ratioPercent(stats.blockedOrFailed, stats.total)) })] }), _jsx(ProgressTrack, { value: ratioPercent(stats.blockedOrFailed, stats.total), tone: stats.blockedOrFailed > 0 ? "warning" : "neutral" })] })] })] })] }));
}
function DecisionMetric({ label, value, tone, }) {
    return (_jsxs("div", { className: cn("rounded border bg-slate-950/70 px-3 py-2", tone === "neutral" && "border-slate-800", tone === "cyan" && "border-slate-700", tone === "emerald" && "border-slate-700", tone === "amber" && "border-amber-500/20"), children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.16em] text-slate-500", children: label }), _jsx("div", { className: cn("mt-1 text-lg font-semibold", tone === "neutral" && "text-slate-200", tone === "cyan" && "text-slate-200", tone === "emerald" && "text-slate-200", tone === "amber" && "text-amber-300"), children: value })] }));
}
// Decision Item with Evidence support
function DecisionItem({ decision, workspace, }) {
    const verdict = decision.verdict || "unknown";
    const isSuccess = verdict === "success";
    const isFailure = verdict === "failure";
    const hasEvidence = Boolean(decision.evidence_bundle_id);
    const [showEvidence, setShowEvidence] = useState(false);
    const actual = decision.actual_outcome || {};
    const decisionSource = decisionString(actual.decision_source) || decision.actor || "";
    const evidenceSchema = decisionString(actual.evidence_schema);
    const profileSchema = decisionString(actual.execution_profile_schema) ||
        decisionString(actual.profile_schema);
    const validatorResult = decisionString(actual.validator_result) ||
        decisionString(actual.validation_status);
    const selectedOption = (decision.options || []).find((option) => option.option_id === decision.selected_option_id);
    const taskCount = decisionNumber(actual.task_count);
    const confidence = formatConfidence(decision.confidence);
    const runtimeContractGate = decisionRuntimeContractGate(decision);
    const runtimeContractStatus = decisionString(runtimeContractGate.status) || "unknown";
    const runtimeContractPassed = decisionHasRuntimeContractReceipt(decision);
    const runtimeContractFailed = decisionHasRuntimeContractFailure(decision);
    const runtimeContractSchema = decisionString(runtimeContractGate.schema_version);
    const runtimeFailedChecks = decisionStringList(runtimeContractGate.failed_check_ids);
    const runtimeEntrypoint = decisionString(actual.role_runtime_entrypoint);
    const agiDecisionProfile = decisionObject(actual.resident_agi_decision_profile);
    const agiDecisionProfileSchema = decisionString(agiDecisionProfile.schema_version);
    const agiDecisionCapability = decisionObject(actual.resident_agi_decision_capability);
    const agiDecisionCapabilityId = decisionString(agiDecisionCapability.decision_id);
    const agiRequiredEvidenceInterfaces = decisionStringList(actual.resident_agi_required_evidence_interfaces);
    const evidenceRefs = (decision.evidence_refs || []).filter(Boolean);
    const affectedFiles = (decision.affected_files || []).filter(Boolean);
    const affectedSymbols = (decision.affected_symbols || []).filter(Boolean);
    const strategyTags = (decision.strategy_tags || []).filter(Boolean);
    const hardRuleBlockers = Array.isArray(actual.hard_rule_blockers)
        ? actual.hard_rule_blockers.map(decisionString).filter(Boolean)
        : [];
    const handoffImpact = decisionHasHandoffImpact(decision);
    return (_jsxs(Card, { className: cn("border-slate-800 bg-slate-900/50", handoffImpact && "border-slate-700"), children: [_jsxs("div", { className: "p-3", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(FileText, { className: "size-4 shrink-0 text-slate-500" }), _jsx("span", { className: "truncate text-sm text-slate-300", title: decision.summary || "未命名决策", children: decision.summary || "未命名决策" })] }), _jsxs("div", { className: "mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500", children: [decision.actor && _jsx("span", { children: decision.actor }), decision.stage && _jsx("span", { children: decision.stage }), decision.decision_id && (_jsxs("span", { title: decision.decision_id, children: ["#", shortDecisionId(decision.decision_id)] })), _jsx("span", { children: formatTime(decision.timestamp) })] })] }), _jsx(Badge, { className: cn(isSuccess && "bg-emerald-500/10 text-emerald-400", isFailure && "bg-red-500/10 text-red-400", !isSuccess && !isFailure && "bg-slate-500/10 text-slate-400"), children: formatAgiUiToken(verdict) })] }), _jsxs("div", { className: "mt-3 grid gap-2 sm:grid-cols-4", children: [_jsxs("div", { className: "rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.14em] text-slate-500", children: "\u7F6E\u4FE1\u5EA6" }), _jsx("div", { className: "mt-1 text-xs font-medium text-slate-200", children: confidence })] }), _jsxs("div", { className: "rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.14em] text-slate-500", children: "\u6821\u9A8C" }), _jsx("div", { className: "mt-1 truncate text-xs font-medium text-slate-200", title: validatorResult || "unknown", children: formatAgiUiToken(validatorResult || "unknown") })] }), _jsxs("div", { className: "rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.14em] text-slate-500", children: "\u4EA4\u63A5" }), _jsx("div", { className: cn("mt-1 text-xs font-medium", handoffImpact ? "text-slate-200" : "text-slate-500"), children: handoffImpact
                                            ? formatAgiRoleChain("PM → Chief Engineer → Director")
                                            : formatAgiUiToken("none") })] }), _jsxs("div", { className: cn("rounded border bg-slate-950/70 px-2 py-1.5", runtimeContractPassed && "border-slate-700", runtimeContractFailed && "border-rose-500/20", !runtimeContractPassed &&
                                    !runtimeContractFailed &&
                                    "border-slate-800"), children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.14em] text-slate-500", children: "\u8FD0\u884C\u65F6" }), _jsx("div", { className: cn("mt-1 truncate text-xs font-medium", runtimeContractPassed && "text-slate-200", runtimeContractFailed && "text-rose-300", !runtimeContractPassed &&
                                            !runtimeContractFailed &&
                                            "text-slate-500"), title: runtimeContractSchema || runtimeContractStatus, children: formatAgiUiToken(runtimeContractStatus) })] })] }), _jsx("div", { className: "mt-2 flex items-center justify-end", children: hasEvidence && (_jsxs("button", { onClick: () => setShowEvidence(!showEvidence), className: cn("flex cursor-pointer items-center gap-1 text-xs transition-colors", showEvidence
                                ? "text-slate-100"
                                : "text-slate-400 hover:text-slate-200"), children: [_jsx(FileSearch, { className: "size-3" }), showEvidence ? "隐藏证据" : "查看证据"] })) }), _jsxs("div", { className: "mt-3 flex flex-wrap gap-2 text-[11px]", children: [decision.stage && (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300", children: ["\u9636\u6BB5\uFF1A", decision.stage] })), decisionSource && (_jsxs("span", { className: "rounded border border-cyan-500/20 bg-cyan-500/10 px-2 py-1 text-cyan-200", children: ["\u6765\u6E90\uFF1A", decisionSource] })), evidenceSchema && (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300", children: ["\u8BC1\u636E\uFF1A", evidenceSchema] })), profileSchema && (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300", children: ["\u753B\u50CF\uFF1A", profileSchema] })), agiDecisionProfileSchema && (_jsxs("span", { className: "rounded border border-cyan-700/40 bg-slate-950 px-2 py-1 text-cyan-200", children: ["AGI \u753B\u50CF\uFF1A", agiDecisionProfileSchema] })), agiDecisionCapabilityId && (_jsxs("span", { className: "rounded border border-cyan-700/40 bg-cyan-950/20 px-2 py-1 text-cyan-200", children: ["AGI \u51B3\u7B56\uFF1A", agiDecisionCapabilityId] })), agiRequiredEvidenceInterfaces.slice(0, 3).map((interfaceId) => (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300", children: ["\u8BC1\u636E\u63A5\u53E3\uFF1A", interfaceId] }, interfaceId))), runtimeContractSchema && (_jsxs("span", { className: cn("rounded border px-2 py-1", runtimeContractPassed &&
                                    "border-cyan-500/20 bg-cyan-500/10 text-cyan-200", runtimeContractFailed &&
                                    "border-rose-500/20 bg-rose-500/10 text-rose-200", !runtimeContractPassed &&
                                    !runtimeContractFailed &&
                                    "border-slate-700 bg-slate-950 text-slate-300"), children: ["\u8FD0\u884C\u65F6\u5951\u7EA6\uFF1A", formatAgiUiToken(runtimeContractStatus)] })), runtimeEntrypoint && (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300", children: ["\u8FD0\u884C\u65F6\uFF1A", runtimeEntrypoint] })), taskCount !== null && (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300", children: ["\u4EFB\u52A1\uFF1A", taskCount] })), decision.run_id && (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300", children: ["\u8FD0\u884C\uFF1A", shortDecisionId(decision.run_id)] })), decision.task_id && (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300", children: ["\u4EFB\u52A1\uFF1A", decision.task_id] })), decision.goal_id && (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300", children: ["\u76EE\u6807\uFF1A", shortDecisionId(decision.goal_id)] })), strategyTags.slice(0, 4).map((tag) => (_jsxs("span", { className: "rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300", children: ["\u6807\u7B7E\uFF1A", tag] }, tag))), hardRuleBlockers.map((blocker) => (_jsxs("span", { className: "rounded border border-amber-500/20 bg-amber-500/10 px-2 py-1 text-amber-200", children: ["\u963B\u65AD\uFF1A", blocker] }, blocker))), runtimeFailedChecks.map((failedCheck) => (_jsxs("span", { className: "rounded border border-rose-500/20 bg-rose-500/10 px-2 py-1 text-rose-200", children: ["\u8FD0\u884C\u65F6\u963B\u65AD\uFF1A", failedCheck] }, failedCheck)))] }), selectedOption && (_jsxs("div", { className: "mt-3 rounded border border-slate-800 bg-slate-950/70 p-2", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2 text-xs", children: [_jsx("span", { className: "font-medium text-slate-200", children: selectedOption.label ||
                                            selectedOption.option_id ||
                                            "selected option" }), typeof selectedOption.estimated_score === "number" && (_jsxs("span", { className: "text-slate-500", children: ["\u5206\u6570 ", Math.round(selectedOption.estimated_score * 100), "%"] }))] }), selectedOption.rationale && (_jsx("div", { className: "mt-1 text-xs text-slate-500", children: selectedOption.rationale }))] })), (decision.context_refs || []).length > 0 && (_jsxs("div", { className: "mt-2 truncate text-xs text-slate-500", title: (decision.context_refs || []).join(" · "), children: ["\u4E0A\u4E0B\u6587\uFF1A", (decision.context_refs || []).slice(0, 3).join(" · ")] })), evidenceRefs.length > 0 && (_jsxs("div", { className: "mt-2 truncate text-xs text-slate-500", title: evidenceRefs.join(" · "), children: ["\u8BC1\u636E\u5F15\u7528\uFF1A", evidenceRefs.slice(0, 3).join(" · ")] })), affectedFiles.length > 0 && (_jsxs("div", { className: "mt-2 truncate text-xs text-slate-500", title: affectedFiles.join(" · "), children: ["\u6587\u4EF6\uFF1A", affectedFiles.slice(0, 3).join(" · ")] })), affectedSymbols.length > 0 && (_jsxs("div", { className: "mt-2 truncate text-xs text-slate-500", title: affectedSymbols.join(" · "), children: ["\u7B26\u53F7\uFF1A", affectedSymbols.slice(0, 4).join(" · ")] }))] }), showEvidence && decision.decision_id && (_jsx("div", { className: "border-t border-slate-800 p-3", children: _jsx(EvidenceViewer, { decisionId: decision.decision_id, workspace: workspace, onClose: () => setShowEvidence(false) }) }))] }));
}
