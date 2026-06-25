import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  ArrowLeft,
  AlertTriangle,
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  Eye,
  FileText,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Send,
  Settings,
  ShieldCheck,
  Square,
  Target,
  Terminal,
  X,
  FileSearch,
  Sparkles,
  FlaskConical,
  Wrench,
  Ban,
  Package,
  Pencil,
} from "lucide-react";

import { EvidenceViewer } from "./EvidenceViewer";
import { ExecutionProgressBar } from "./ExecutionProgressBar";

import { useResident } from "@/hooks/useResident";
import type {
  ResidentAgiAuditPackPayload,
  ResidentAgiCapabilityAccessRegistryPayload,
  ResidentAgiAuthorityMatrixPayload,
  ResidentAgiCapabilityPayload,
  ResidentAgiDecisionCapabilityPayload,
  ResidentAgiDecisionCapabilityRegistryPayload,
  ResidentAgiDecisionBoundaryPolicyPayload,
  ResidentAgiEvidenceInterfaceContractPayload,
  ResidentAgiEvidenceInterfacesPayload,
  ResidentAgiDecisionHandoffPayload,
  ResidentAgiHandoffInboxPayload,
  ResidentAgiHardcodedRepairStrategyCatalogPayload,
  ResidentAgiRepairAdvisoryPolicyPayload,
  ResidentAgiRepairAdvisoryOverlayPayload,
  ResidentAgiTacticalActionCatalogPayload,
  ResidentAgiTacticalChatActionPayload,
  ResidentAgiTacticalChatGoalDraftPayload,
  ResidentAgiTacticalDecisionRoutePayload,
  ResidentAgiTacticalMissionBriefPayload,
  ResidentAgiTacticalParticipationGatePayload,
  ResidentAgiTacticalToolTracePayload,
  ResidentAgiTacticalChatResponse,
  ResidentAgiDecisionProfilePayload,
  ResidentAgiDecisionBoundaryPayload,
  ResidentAgiParticipationPayload,
  ResidentDecisionPayload,
  ResidentGoalPayload,
  ResidentStatusDetailsPayload,
} from "@/app/types/appContracts";
import { Button } from "@/app/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/app/components/ui/card";
import { Input } from "@/app/components/ui/input";
import { Textarea } from "@/app/components/ui/textarea";
import { Badge } from "@/app/components/ui/badge";
import { Switch } from "@/app/components/ui/switch";
import { cn } from "@/app/components/ui/utils";

const TAB_OPTIONS = ["overview", "goals", "decisions", "evolution"] as const;
type AgiTab = (typeof TAB_OPTIONS)[number];

interface CapabilityGovernanceStats {
  readOnly: number;
  governedMutation: number;
  highRisk: number;
  categories: string[];
  contractRefs: string[];
  chainRequired: boolean;
}

interface ResidentWorkspaceProps {
  workspace: string;
  onBackToMain: () => void;
  residentSnapshot?: ResidentStatusDetailsPayload | null;
  initialTab?: AgiTab;
  residentAgiLlmStatus?: ResidentAgiLlmStatus | null;
}

interface AgiParticipationOption {
  scope: string;
  label: string;
  category?: string;
  riskLevel?: string;
}

export interface ResidentAgiLlmStatus {
  ready?: boolean | null;
  providerId?: string | null;
  providerName?: string | null;
  model?: string | null;
  grade?: string | null;
  blocked?: boolean;
  unsupported?: boolean;
  readinessIssue?: string | null;
  runtimeIssue?: string | null;
  lastUpdated?: string | null;
}

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

const AGI_PARTICIPATION_LABELS: Record<string, string> = {
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

const AGI_UI_TOKEN_LABELS: Record<string, string> = {
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

function formatAgiUiToken(value?: unknown): string {
  const token = String(value ?? "")
    .trim()
    .toLowerCase();
  if (!token) return "暂无";
  if (token.startsWith("invalid")) return "无效";
  return AGI_UI_TOKEN_LABELS[token] || String(value);
}

function formatAgiBoolean(value?: boolean | null): string {
  return value ? "是" : "否";
}

function formatAgiAllowed(value?: boolean | null): string {
  return value ? "允许" : "已阻断";
}

function formatAgiActive(value?: boolean | null): string {
  return value ? "已激活" : "未激活";
}

function formatAgiRoleChain(value?: string | null): string {
  const token = String(value || "").trim();
  if (!token) return "只读/观察优先";
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

function formatTime(value?: string | null): string {
  if (!value) return "暂无";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  const date = new Date(parsed);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes}分钟前`;
  if (hours < 24) return `${hours}小时前`;
  if (days < 7) return `${days}天前`;
  return date.toLocaleDateString();
}

function uniqueStrings(values: Array<string | undefined | null>): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  values.forEach((value) => {
    const token = String(value || "").trim();
    if (!token || seen.has(token)) return;
    seen.add(token);
    result.push(token);
  });
  return result;
}

function normalizeAgiParticipationScope(scope: string): string {
  return String(scope || "")
    .trim()
    .toLowerCase()
    .replace(/[.\-\s]+/g, "_");
}

function isAgiParticipationScopeSelected(
  scope: string,
  selectedScopes: string[],
): boolean {
  const scopeKey = normalizeAgiParticipationScope(scope);
  return selectedScopes.some(
    (selectedScope) =>
      normalizeAgiParticipationScope(selectedScope) === scopeKey,
  );
}

function buildAgiParticipationFlags(
  scopes: string[],
  knownScopes: string[] = DEFAULT_AGI_PARTICIPATION_FLAGS,
): Record<string, boolean> {
  const selected = new Set(scopes.map(normalizeAgiParticipationScope));
  const keys = uniqueStrings([...knownScopes, ...scopes]);
  return keys.reduce<Record<string, boolean>>((acc, scope) => {
    acc[scope] = selected.has(normalizeAgiParticipationScope(scope));
    return acc;
  }, {});
}

function selectedAgiParticipationScopes(
  participation?: ResidentAgiParticipationPayload | null,
): string[] {
  if (!participation) return [];
  const selectedFromFlags = Object.entries(participation.participation || {})
    .filter(([, enabled]) => enabled)
    .map(([scope]) => scope);
  return uniqueStrings([...(participation.scopes || []), ...selectedFromFlags]);
}

function agiParticipationScopeTestId(scope: string): string {
  return normalizeAgiParticipationScope(scope).replace(/_/g, "-");
}

function selectAgiParticipationFocusOptions(
  options: AgiParticipationOption[],
): AgiParticipationOption[] {
  const byKey = new Map<string, AgiParticipationOption>();
  options.forEach((option) => {
    const key = normalizeAgiParticipationScope(option.scope);
    if (key && !byKey.has(key)) {
      byKey.set(key, option);
    }
  });
  const selectedKeys = new Set<string>();
  const selected: AgiParticipationOption[] = [];
  AGI_PARTICIPATION_FOCUS_SCOPE_KEYS.forEach((key) => {
    const option = byKey.get(key);
    if (
      !option ||
      selectedKeys.has(normalizeAgiParticipationScope(option.scope))
    ) {
      return;
    }
    selected.push(option);
    selectedKeys.add(normalizeAgiParticipationScope(option.scope));
  });
  options.forEach((option) => {
    if (selected.length >= 4) return;
    const key = normalizeAgiParticipationScope(option.scope);
    if (!key || selectedKeys.has(key)) return;
    selected.push(option);
    selectedKeys.add(key);
  });
  return selected.slice(0, 4);
}

function describeAgiParticipationScope(option: AgiParticipationOption): string {
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

function GoalStatusBadge({ status }: { status: string }) {
  const token = status.toLowerCase();
  if (token === "approved" || token === "materialized") {
    return (
      <Badge className="border-slate-700 bg-slate-950 text-slate-300">
        已批准
      </Badge>
    );
  }
  if (token === "rejected") {
    return (
      <Badge className="bg-red-500/10 text-red-400 border-red-500/20">
        已拒绝
      </Badge>
    );
  }
  return (
    <Badge className="border-slate-800 bg-slate-950 text-slate-500">
      待审批
    </Badge>
  );
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

function ratioPercent(numerator: number, denominator: number): number {
  if (!Number.isFinite(denominator) || denominator <= 0) return 0;
  return clampPercent((numerator / denominator) * 100);
}

function formatPercent(value: number): string {
  return `${Math.round(clampPercent(value))}%`;
}

function ProgressTrack({
  value,
  tone = "neutral",
}: {
  value: number;
  tone?: "neutral" | "warning";
}) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-slate-900">
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-500 ease-out",
          tone === "warning" ? "bg-amber-300/70" : "bg-slate-300/70",
        )}
        style={{ width: `${formatPercent(value)}` }}
      />
    </div>
  );
}

function SegmentedMeter({
  segments,
}: {
  segments: Array<{
    label: string;
    value: number;
    className?: string;
  }>;
}) {
  const filtered = segments.filter((segment) => segment.value > 0);
  const total = filtered.reduce((sum, segment) => sum + segment.value, 0);
  if (total <= 0) return null;
  return (
    <div className="space-y-2">
      <div className="flex h-2 overflow-hidden rounded-full bg-slate-900">
        {filtered.map((segment) => (
          <div
            key={segment.label}
            className={cn(
              "h-full transition-[width] duration-500 ease-out",
              segment.className || "bg-slate-500",
            )}
            style={{ width: `${ratioPercent(segment.value, total)}%` }}
            title={`${segment.label}: ${segment.value}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-500">
        {filtered.map((segment) => (
          <span
            key={segment.label}
            className="inline-flex items-center gap-1.5"
          >
            <span
              className={cn(
                "size-1.5 rounded-full",
                segment.className || "bg-slate-500",
              )}
            />
            {segment.label} {segment.value}
          </span>
        ))}
      </div>
    </div>
  );
}

type AgiSeverity = "ok" | "warn" | "danger" | "idle";
type AgiRoleTrackState = "complete" | "active" | "blocked" | "waiting";

interface AgiTrustSignal {
  label: string;
  value: string;
  severity: AgiSeverity;
}

interface AgiRoleTrackItem {
  role: "PM" | "CE" | "Director" | "QA";
  title: string;
  state: AgiRoleTrackState;
  detail: string;
  evidence: string;
}

interface AgiConsoleReceipt {
  title: string;
  summary: string;
  status?: string;
  rows: Array<{ label: string; value: string }>;
}

interface AgiConsoleMissionBrief {
  title: string;
  severity: AgiSeverity;
  statusLabel: string;
  progressPercent: number;
  currentFocus: string;
  currentStage: string;
  latestVerdict: string;
  blockers: string[];
  nextActions: string[];
  metrics: Array<{ label: string; value: string }>;
}

interface AgiConsoleToolTraceItem {
  stepId: string;
  label: string;
  mode: string;
  status: string;
  contract: string;
  summary: string;
}

interface AgiConsoleToolTrace {
  schemaVersion: string;
  items: AgiConsoleToolTraceItem[];
}

interface AgiConsoleDecisionRoute {
  schemaVersion: string;
  status: string;
  reason: string;
  recommendedActionIds: string[];
  governedActionIds: string[];
  blockedReasons: string[];
}

interface AgiConsoleParticipationGate {
  schemaVersion: string;
  status: string;
  summary: string;
  requiredScopeIds: string[];
  configuredScopeIds: string[];
  missingScopeIds: string[];
  settingsActionAvailable: boolean;
  governedActionsAvailable: boolean;
  directPermissionChangeAllowed: boolean;
}

interface AgiConsoleAction {
  actionId: string;
  label: string;
  status: string;
  mode: string;
  reason: string;
  uiHandler: string;
  capabilityId: string;
  contractRef: string;
  riskLevel: string;
  executionBoundary: string;
  requiresParticipation: boolean;
  agiDirectExecutionAllowed: boolean;
  sourceMessage: string;
  goalDraft?: ResidentAgiTacticalChatGoalDraftPayload;
}

type AgiQuickCommandIcon =
  | "status"
  | "blocker"
  | "evidence"
  | "judgement"
  | "repair"
  | "tick"
  | "model";

interface AgiQuickCommand {
  label: string;
  command: string;
  detail?: string;
  icon: AgiQuickCommandIcon;
  severity?: AgiSeverity;
}

interface AgiConsoleMessage {
  id: string;
  role: "user" | "agi";
  text: string;
  flow?: string[];
  missionBrief?: AgiConsoleMissionBrief;
  toolTrace?: AgiConsoleToolTrace;
  participationGate?: AgiConsoleParticipationGate;
  decisionRoute?: AgiConsoleDecisionRoute;
  receipt?: AgiConsoleReceipt;
  actions?: AgiConsoleAction[];
}

interface AgiActionTimelineEntry {
  id: string;
  title: string;
  status: string;
  summary: string;
  source: string;
  severity: AgiSeverity;
  actionIds: string[];
}

function severityClass(severity: AgiSeverity): string {
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

function toolTraceStatusClass(status: string): string {
  const normalized = status.toLowerCase();
  if (["passed", "available", "read"].includes(normalized)) {
    return "border-emerald-500/20 bg-emerald-500/10 text-emerald-200";
  }
  if (["blocked", "failed", "error"].includes(normalized)) {
    return "border-rose-500/25 bg-rose-500/10 text-rose-200";
  }
  return "border-slate-700 bg-slate-900/70 text-slate-300";
}

function decisionRouteStatusClass(status: string): string {
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

function actionTimelineSeverity(status: string): AgiSeverity {
  const normalized = status.toLowerCase();
  if (
    normalized.includes("blocked") ||
    normalized.includes("failed") ||
    normalized.includes("error")
  ) {
    return "danger";
  }
  if (
    normalized.includes("judged") ||
    normalized.includes("executed") ||
    normalized.includes("handoff") ||
    normalized.includes("role_turn")
  ) {
    return "ok";
  }
  if (normalized.includes("read")) return "idle";
  return "warn";
}

function quickCommandIcon(icon: AgiQuickCommandIcon): ReactNode {
  const className = "size-3.5";
  if (icon === "blocker") return <AlertTriangle className={className} />;
  if (icon === "evidence") return <FileSearch className={className} />;
  if (icon === "judgement") return <Brain className={className} />;
  if (icon === "repair") return <Wrench className={className} />;
  if (icon === "tick") return <RefreshCw className={className} />;
  if (icon === "model") return <Settings className={className} />;
  return <Activity className={className} />;
}

function quickCommandClass(severity: AgiSeverity = "idle"): string {
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

function shortQuickCommandDetail(value: string, fallback: string): string {
  const normalized = String(value || "").trim();
  const source = normalized || fallback;
  if (source.length <= 28) return source;
  return `${source.slice(0, 27)}…`;
}

function actionRiskToSeverity(
  action?: ResidentAgiTacticalChatActionPayload,
): AgiSeverity {
  const risk = String(action?.risk_level || "")
    .trim()
    .toLowerCase();
  if (risk === "high") return "danger";
  if (risk === "medium") return "warn";
  if (risk === "low") return "ok";
  return "idle";
}

function findAgiCatalogAction(
  catalog: ResidentAgiTacticalActionCatalogPayload | null | undefined,
  actionId: string,
): ResidentAgiTacticalChatActionPayload | null {
  return (
    catalog?.items?.find(
      (item) => String(item.action_id || "").trim() === actionId,
    ) || null
  );
}

function statusRingClass(severity: AgiSeverity): string {
  if (severity === "danger") return "border-rose-400/70 shadow-rose-500/20";
  if (severity === "warn") return "border-amber-300/70 shadow-amber-500/20";
  if (severity === "ok") return "border-emerald-300/70 shadow-emerald-500/20";
  return "border-slate-600 shadow-slate-900/30";
}

function buildConsoleId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function buildAgiActionTimeline(
  messages: AgiConsoleMessage[],
): AgiActionTimelineEntry[] {
  return messages
    .filter(
      (message) =>
        message.role === "agi" &&
        (message.receipt || message.decisionRoute || message.toolTrace),
    )
    .slice(-4)
    .reverse()
    .map((message, index) => {
      const status =
        message.receipt?.status ||
        message.decisionRoute?.status ||
        message.toolTrace?.items[0]?.status ||
        "READ";
      const actionIds = uniqueStrings([
        ...(message.decisionRoute?.recommendedActionIds || []),
        ...(message.actions || []).map((action) => action.actionId),
      ]).slice(0, 4);
      return {
        id: `${message.id}:${index}`,
        title:
          message.receipt?.title ||
          (message.decisionRoute ? "决策路线" : "指令流"),
        status,
        summary:
          message.receipt?.summary ||
          message.decisionRoute?.reason ||
          message.text,
        source:
          message.toolTrace?.schemaVersion ||
          message.receipt?.rows.find((row) => row.label === "事实源")?.value ||
          message.decisionRoute?.schemaVersion ||
          "resident.agi_tactical_console",
        severity: actionTimelineSeverity(status),
        actionIds,
      };
    });
}

function roleTrackStateClass(state: AgiRoleTrackState): string {
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

function roleTrackStatusLabel(state: AgiRoleTrackState): string {
  if (state === "complete") return "就绪";
  if (state === "active") return "进行";
  if (state === "blocked") return "阻断";
  return "等待";
}

function roleTrackDisplayLabel(role: string): string {
  const normalized = role.trim().toLowerCase();
  if (normalized === "pm") return "项目经理";
  if (normalized === "ce") return "总工程师";
  if (normalized === "director") return "执行官";
  if (normalized === "qa") return "质检";
  return role;
}

function targetRolesInclude(
  handoff: ResidentAgiDecisionHandoffPayload | null | undefined,
  role: "pm" | "ce" | "director" | "qa",
): boolean {
  const aliases: Record<typeof role, string[]> = {
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

function buildAgiRoleTrackItems({
  runtimeActive,
  pendingGoalCount,
  approvedGoalCount,
  materializedGoalCount,
  decisionCount,
  handoff,
  evidenceGateStatus,
  runLedgerStatus,
}: {
  runtimeActive: boolean;
  pendingGoalCount: number;
  approvedGoalCount: number;
  materializedGoalCount: number;
  decisionCount: number;
  handoff?: ResidentAgiDecisionHandoffPayload | null;
  evidenceGateStatus: string;
  runLedgerStatus: string;
}): AgiRoleTrackItem[] {
  const handoffStatus = String(handoff?.handoff_status || "").toLowerCase();
  const downstreamBlocked =
    handoffStatus === "blocked" || handoff?.downstream_allowed === false;
  const hasReadyHandoff = handoffStatus === "ready";
  const evidenceFailed =
    evidenceGateStatus === "fail" ||
    runLedgerStatus === "failed" ||
    runLedgerStatus === "failure";
  const evidencePassed =
    evidenceGateStatus === "pass" ||
    runLedgerStatus === "pass" ||
    runLedgerStatus === "success";

  const pmState: AgiRoleTrackItem =
    pendingGoalCount > 0
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

  const ceState: AgiRoleTrackItem = downstreamBlocked
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

  const directorState: AgiRoleTrackItem = downstreamBlocked
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

  const qaState: AgiRoleTrackItem = evidenceFailed
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

function AgiRoleTrack({ items }: { items: AgiRoleTrackItem[] }) {
  return (
    <div className="grid grid-cols-4 gap-2" data-testid="agi-role-track">
      {items.map((item) => {
        return (
          <div
            key={item.role}
            data-testid={`agi-role-track-${item.role.toLowerCase()}`}
            title={`${item.evidence}: ${item.detail}`}
            className={cn(
              "relative min-w-0 rounded-md border px-2 py-2 text-center transition-colors",
              roleTrackStateClass(item.state),
            )}
          >
            <div className="text-[10px] tracking-[0.08em]">
              {roleTrackDisplayLabel(item.role)}
            </div>
            <div className="mt-1 truncate text-xs font-medium">
              {item.title}
            </div>
            <div className="mt-0.5 truncate text-[10px] opacity-65">
              {roleTrackStatusLabel(item.state)}
            </div>
            {(item.state === "active" || item.state === "blocked") && (
              <div className="mx-auto mt-1 h-0.5 w-8 rounded-full bg-amber-300/80" />
            )}
          </div>
        );
      })}
    </div>
  );
}

function AgiCockpitOverview({
  statusLabel,
  statusDetail,
  severity,
  mission,
  nextAction,
  blockers,
  trustSignals,
  roleTrackItems,
  goalsCount,
  decisionsCount,
  evidenceCoverage,
  lastUpdated,
  onOpenAdvanced,
  onExplainBlocker,
  onRunTick,
}: {
  statusLabel: string;
  statusDetail: string;
  severity: AgiSeverity;
  mission: string;
  nextAction: string;
  blockers: string[];
  trustSignals: AgiTrustSignal[];
  roleTrackItems: AgiRoleTrackItem[];
  goalsCount: number;
  decisionsCount: number;
  evidenceCoverage: string;
  lastUpdated: string;
  onOpenAdvanced: () => void;
  onExplainBlocker: () => void;
  onRunTick: () => void;
}) {
  return (
    <Card
      className="overflow-hidden border-slate-800 bg-slate-950/70"
      data-testid="agi-cockpit-overview"
    >
      <CardContent className="p-0">
        <div className="grid gap-0 lg:grid-cols-[220px_minmax(0,1fr)]">
          <div className="border-b border-slate-800 bg-slate-950/90 p-4 lg:border-b-0 lg:border-r">
            <div
              className={cn(
                "mx-auto flex size-28 items-center justify-center rounded-full border bg-slate-950 shadow-2xl",
                statusRingClass(severity),
              )}
            >
              <Bot className="size-11 text-slate-100" />
            </div>
            <div className="mt-4 text-center">
              <div className="text-sm font-semibold text-slate-100">
                驻场 AGI
              </div>
              <Badge
                className={cn("mt-2 border text-xs", severityClass(severity))}
              >
                {statusLabel}
              </Badge>
              <div className="mt-2 text-xs leading-5 text-slate-500">
                {statusDetail}
              </div>
            </div>
          </div>

          <div className="space-y-4 p-4">
            <div>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">
                    当前看护任务
                  </div>
                  <div className="mt-1 text-lg font-semibold text-slate-50">
                    {mission}
                  </div>
                </div>
                <div className="hidden items-center gap-1 rounded-full border border-slate-800 bg-slate-950 px-2 py-1 text-[10px] text-slate-500 sm:flex">
                  <Radio className="size-3" />
                  {lastUpdated}
                </div>
              </div>
              <div className="mt-3">
                <AgiRoleTrack items={roleTrackItems} />
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]">
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
                  <Activity className="size-4 text-slate-400" />
                  下一步建议
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-300">
                  {nextAction}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    className="bg-slate-100 text-slate-950 hover:bg-white"
                    onClick={onExplainBlocker}
                    data-testid="agi-explain-blocker"
                  >
                    <Terminal className="mr-1.5 size-3.5" />让 AGI 解释
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-slate-700 text-slate-200 hover:bg-slate-900"
                    onClick={onRunTick}
                  >
                    <Brain className="mr-1.5 size-3.5" />
                    反思一轮
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-slate-400 hover:text-slate-100"
                    onClick={onOpenAdvanced}
                  >
                    <Eye className="mr-1.5 size-3.5" />
                    高级审计
                  </Button>
                </div>
              </div>

              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                <div className="text-xs uppercase tracking-[0.14em] text-slate-500">
                  运行脉冲
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                  <div>
                    <div className="text-lg font-semibold text-slate-100">
                      {goalsCount}
                    </div>
                    <div className="text-[10px] text-slate-500">目标</div>
                  </div>
                  <div>
                    <div className="text-lg font-semibold text-slate-100">
                      {decisionsCount}
                    </div>
                    <div className="text-[10px] text-slate-500">决策</div>
                  </div>
                  <div>
                    <div className="text-lg font-semibold text-slate-100">
                      {evidenceCoverage}
                    </div>
                    <div className="text-[10px] text-slate-500">证据</div>
                  </div>
                </div>
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
                  <AlertTriangle className="size-4 text-amber-300" />
                  需要注意
                </div>
                <div className="mt-2 space-y-1.5">
                  {blockers.length > 0 ? (
                    blockers.slice(0, 3).map((blocker) => (
                      <div
                        key={blocker}
                        className="rounded border border-amber-500/20 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-100"
                      >
                        {blocker}
                      </div>
                    ))
                  ) : (
                    <div className="rounded border border-slate-800 bg-slate-950 px-2 py-1.5 text-xs text-slate-400">
                      当前没有需要人工处理的阻断项。
                    </div>
                  )}
                </div>
              </div>

              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
                  <ShieldCheck className="size-4 text-slate-400" />
                  信任条
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {trustSignals.map((signal) => (
                    <div
                      key={signal.label}
                      className={cn(
                        "rounded border px-2 py-1.5 text-xs",
                        severityClass(signal.severity),
                      )}
                    >
                      <div className="text-[10px] opacity-70">
                        {signal.label}
                      </div>
                      <div className="mt-0.5 font-medium">{signal.value}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function AgiParticipationDock({
  enabled,
  options,
  selectedScopes,
  repairAdvisoryEnabled,
  llmReady,
  llmIssue,
  isSaving,
  onEnabledChange,
  onToggleScope,
  onToggleRepairAdvisory,
  onSave,
  onOpenAdvanced,
}: {
  enabled: boolean;
  options: AgiParticipationOption[];
  selectedScopes: string[];
  repairAdvisoryEnabled: boolean;
  llmReady: boolean;
  llmIssue: string;
  isSaving: boolean;
  onEnabledChange: (enabled: boolean) => void;
  onToggleScope: (scope: string) => void;
  onToggleRepairAdvisory: (enabled: boolean) => void;
  onSave: () => void;
  onOpenAdvanced: () => void;
}) {
  const focusOptions = useMemo(
    () => selectAgiParticipationFocusOptions(options),
    [options],
  );
  const selectedCount = uniqueStrings(selectedScopes).length;
  const totalCount = Math.max(
    options.length,
    DEFAULT_AGI_PARTICIPATION_FLAGS.length,
  );
  const participationLabel = enabled ? "允许参与" : "仅观察";
  const boundaryLabel = enabled
    ? "可研判，不可越权写入"
    : "关闭后只保留只读解释能力";

  return (
    <Card
      className="border-slate-800/80 bg-slate-950/55"
      data-testid="agi-participation-dock"
    >
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm text-slate-200">
              <Settings className="size-4 text-slate-400" />
              AGI 参与权限
            </CardTitle>
            <div className="mt-1 text-xs text-slate-500">
              绑定 Resident AGI participation 契约，不新增第二套权限源。
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge
              className={cn(
                "border text-xs",
                enabled
                  ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-200"
                  : "border-slate-700 bg-slate-950 text-slate-500",
              )}
            >
              {participationLabel}
            </Badge>
            <Switch
              aria-label="AGI 参与权限总开关"
              data-testid="agi-participation-master"
              checked={enabled}
              onCheckedChange={onEnabledChange}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-2 sm:grid-cols-3">
          <div className="rounded-md border border-slate-800 bg-slate-950/70 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">
              已选范围
            </div>
            <div className="mt-1 text-lg font-semibold text-slate-100">
              {selectedCount}/{totalCount}
            </div>
          </div>
          <div className="rounded-md border border-slate-800 bg-slate-950/70 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">
              模型状态
            </div>
            <div
              className={cn(
                "mt-1 truncate text-sm font-medium",
                llmReady ? "text-emerald-200" : "text-amber-200",
              )}
              title={llmIssue}
            >
              {llmReady ? "已绑定" : "待确认"}
            </div>
          </div>
          <div className="rounded-md border border-slate-800 bg-slate-950/70 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">
              边界
            </div>
            <div className="mt-1 truncate text-sm font-medium text-slate-200">
              {boundaryLabel}
            </div>
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          {focusOptions.map((option) => {
            const selected = isAgiParticipationScopeSelected(
              option.scope,
              selectedScopes,
            );
            return (
              <button
                key={option.scope}
                type="button"
                aria-pressed={selected}
                disabled={!enabled}
                data-testid={`agi-participation-quick-${agiParticipationScopeTestId(
                  option.scope,
                )}`}
                onClick={() => onToggleScope(option.scope)}
                className={cn(
                  "min-h-24 rounded-md border px-3 py-2 text-left transition-colors",
                  selected
                    ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-100"
                    : "border-slate-800 bg-slate-950/70 text-slate-300",
                  !enabled && "cursor-not-allowed opacity-45",
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0 truncate text-sm font-medium">
                    {option.label}
                  </span>
                  <span className="shrink-0 rounded border border-current/15 px-1.5 py-0.5 text-[10px] opacity-80">
                    {selected ? "ON" : "OFF"}
                  </span>
                </div>
                <div className="mt-1 line-clamp-2 text-xs leading-5 opacity-75">
                  {describeAgiParticipationScope(option)}
                </div>
                {(option.riskLevel || option.category) && (
                  <div className="mt-2 truncate text-[10px] opacity-60">
                    {[formatAgiUiToken(option.riskLevel), option.category]
                      .filter((item) => item && item !== "暂无")
                      .join(" · ")}
                  </div>
                )}
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-800 bg-slate-950/70 px-3 py-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
              <Wrench className="size-4 text-slate-400" />
              Director 修复建议
            </div>
            <div className="mt-1 text-xs text-slate-500">
              AGI 只可提出 suggested_rules，不能注册规则或绕过修复内核。
            </div>
          </div>
          <Switch
            aria-label="Director 修复建议参与"
            data-testid="agi-participation-repair-advisory"
            disabled={!enabled}
            checked={repairAdvisoryEnabled}
            onCheckedChange={onToggleRepairAdvisory}
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs text-slate-500">
            {llmIssue || "保存后由 Resident AGI 角色回合与公开 Cell 契约消费。"}
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="ghost"
              className="text-slate-400 hover:text-slate-100"
              onClick={onOpenAdvanced}
            >
              <Eye className="mr-1 size-3.5" />
              黑匣子
            </Button>
            <Button
              size="sm"
              className="bg-slate-100 text-slate-950 hover:bg-white"
              disabled={isSaving}
              data-testid="agi-save-participation"
              onClick={onSave}
            >
              保存权限
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function AgiTacticalConsole({
  messages,
  value,
  disabled = false,
  quickCommands,
  pendingAction,
  onChange,
  onSubmit,
  onQuickCommand,
  onAction,
  onConfirmAction,
  onCancelAction,
  onOpenAdvanced,
  onOpenOperatorSettings,
  onOpenGoals,
}: {
  messages: AgiConsoleMessage[];
  value: string;
  disabled?: boolean;
  quickCommands?: AgiQuickCommand[];
  pendingAction?: AgiConsoleAction | null;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onQuickCommand: (command: string) => void;
  onAction: (action: AgiConsoleAction) => void;
  onConfirmAction: () => void;
  onCancelAction: () => void;
  onOpenAdvanced: () => void;
  onOpenOperatorSettings: () => void;
  onOpenGoals: () => void;
}) {
  const fallbackQuickCommands: AgiQuickCommand[] = [
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
  const resolveActionHandler = (action: AgiConsoleAction): string => {
    if (action.uiHandler) return action.uiHandler;
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
    if (
      action.actionId === "request_director_controlled_repair" ||
      action.actionId === "request_resident_agi_judgement"
    ) {
      return "execute_governed_action";
    }
    return "";
  };

  return (
    <Card
      className="flex min-h-[560px] flex-col border-slate-800 bg-slate-950/80"
      data-testid="agi-tactical-console"
    >
      <CardHeader className="border-b border-slate-800 pb-3">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 text-sm text-slate-200">
            <Terminal className="size-4 text-slate-400" />
            战术控制台
          </CardTitle>
          <Badge className="border-slate-700 bg-slate-950 text-[10px] text-slate-400">
            只读优先 · 受控执行
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3 p-3">
        <div className="min-h-0 flex-1 space-y-3 overflow-auto pr-1">
          {messages.map((message) => (
            <div
              key={message.id}
              className={cn(
                "rounded-lg border p-3",
                message.role === "user"
                  ? "ml-8 border-slate-700 bg-slate-900/70"
                  : "mr-4 border-slate-800 bg-slate-950/70",
              )}
            >
              <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-[0.14em] text-slate-500">
                {message.role === "user" ? "用户指令" : "驻场 AGI"}
              </div>
              <div className="text-sm leading-6 text-slate-200">
                {message.text}
              </div>
              {message.missionBrief && (
                <div
                  className={cn(
                    "mt-3 rounded-md border bg-slate-950/60 p-3",
                    severityClass(message.missionBrief.severity),
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Activity className="size-4 shrink-0" />
                        {message.missionBrief.title}
                      </div>
                      <div className="mt-1 truncate text-xs opacity-80">
                        {message.missionBrief.currentFocus}
                      </div>
                    </div>
                    <span className="shrink-0 rounded border border-current/20 px-2 py-1 text-[10px] font-medium">
                      {message.missionBrief.statusLabel}
                    </span>
                  </div>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-900/80">
                    <div
                      className="h-full rounded-full bg-current transition-[width] duration-300"
                      style={{
                        width: `${message.missionBrief.progressPercent}%`,
                      }}
                    />
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {message.missionBrief.metrics.map((metric) => (
                      <div
                        key={`${metric.label}:${metric.value}`}
                        className="rounded border border-current/10 bg-slate-950/40 px-2 py-1.5"
                      >
                        <div className="text-[10px] opacity-60">
                          {metric.label}
                        </div>
                        <div className="mt-0.5 truncate font-mono text-xs">
                          {metric.value}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="mt-3 grid gap-2 md:grid-cols-2">
                    <div>
                      <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-[0.12em] opacity-60">
                        <AlertTriangle className="size-3" />
                        阻塞
                      </div>
                      <div className="space-y-1 text-xs leading-5">
                        {message.missionBrief.blockers.length
                          ? message.missionBrief.blockers.map((item) => (
                              <div key={item}>{item}</div>
                            ))
                          : "当前没有硬阻断。"}
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-[0.12em] opacity-60">
                        <Target className="size-3" />
                        下一步
                      </div>
                      <div className="space-y-1 text-xs leading-5">
                        {message.missionBrief.nextActions.map((item) => (
                          <div key={item}>{item}</div>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2 text-[10px] opacity-70">
                    <span>阶段：{message.missionBrief.currentStage}</span>
                    {message.missionBrief.latestVerdict && (
                      <span>结论：{message.missionBrief.latestVerdict}</span>
                    )}
                  </div>
                </div>
              )}
              {message.toolTrace && (
                <div
                  className="mt-3 rounded-md border border-slate-800 bg-black/25 p-2"
                  data-testid="agi-tool-trace"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-[11px] font-medium text-slate-300">
                      <Terminal className="size-3.5 text-slate-500" />
                      指令流
                    </div>
                    <span className="font-mono text-[10px] text-slate-500">
                      {message.toolTrace.schemaVersion}
                    </span>
                  </div>
                  <div className="space-y-1.5">
                    {message.toolTrace.items.map((item) => (
                      <div
                        key={item.stepId}
                        className="grid grid-cols-[auto_1fr] gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5 sm:grid-cols-[auto_minmax(120px,0.7fr)_1fr]"
                        title={item.contract}
                      >
                        <span
                          className={cn(
                            "h-5 rounded border px-1.5 py-0.5 font-mono text-[10px] leading-4",
                            toolTraceStatusClass(item.status),
                          )}
                        >
                          {item.status}
                        </span>
                        <span className="truncate text-xs text-slate-200">
                          {item.label}
                        </span>
                        <span className="col-span-2 truncate text-[11px] text-slate-500 sm:col-span-1">
                          {item.summary || item.mode}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {message.participationGate && (
                <div
                  className={cn(
                    "mt-3 rounded-md border p-2",
                    message.participationGate.status === "allowed"
                      ? severityClass("ok")
                      : severityClass("warn"),
                  )}
                  data-testid="agi-participation-gate"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-[11px] font-medium">
                        <ShieldCheck className="size-3.5 shrink-0" />
                        权限闸门
                      </div>
                      <div className="mt-1 text-xs leading-5 opacity-80">
                        {message.participationGate.summary}
                      </div>
                    </div>
                    <span className="shrink-0 rounded border border-current/20 px-1.5 py-0.5 font-mono text-[10px]">
                      {formatAgiUiToken(message.participationGate.status)}
                    </span>
                  </div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    <div className="rounded border border-current/15 bg-black/20 px-2 py-1.5">
                      <div className="text-[10px] opacity-60">需要范围</div>
                      <div className="mt-0.5 truncate text-[11px]">
                        {message.participationGate.requiredScopeIds
                          .map((scope) => AGI_PARTICIPATION_LABELS[scope] || scope)
                          .join("、") || "无"}
                      </div>
                    </div>
                    <div className="rounded border border-current/15 bg-black/20 px-2 py-1.5">
                      <div className="text-[10px] opacity-60">缺失范围</div>
                      <div className="mt-0.5 truncate text-[11px]">
                        {message.participationGate.missingScopeIds
                          .map((scope) => AGI_PARTICIPATION_LABELS[scope] || scope)
                          .join("、") || "无"}
                      </div>
                    </div>
                    <div className="rounded border border-current/15 bg-black/20 px-2 py-1.5">
                      <div className="text-[10px] opacity-60">设定入口</div>
                      <div className="mt-0.5 truncate font-mono text-[10px]">
                        {message.participationGate.settingsActionAvailable
                          ? "可打开"
                          : "不需要"}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              {message.decisionRoute && (
                <div
                  className={cn(
                    "mt-3 rounded-md border p-2",
                    decisionRouteStatusClass(message.decisionRoute.status),
                  )}
                  data-testid="agi-decision-route"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-[11px] font-medium">
                        <Brain className="size-3.5 shrink-0" />
                        决策路线
                      </div>
                      <div className="mt-1 truncate text-[11px] opacity-75">
                        {message.decisionRoute.reason}
                      </div>
                    </div>
                    <span className="shrink-0 rounded border border-current/20 px-1.5 py-0.5 font-mono text-[10px]">
                      {message.decisionRoute.status}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {message.decisionRoute.recommendedActionIds
                      .slice(0, 4)
                      .map((actionId) => (
                        <span
                          key={actionId}
                          className="rounded border border-current/15 bg-black/20 px-1.5 py-0.5 font-mono text-[10px]"
                        >
                          {actionId}
                        </span>
                      ))}
                    {message.decisionRoute.governedActionIds.length > 0 && (
                      <span className="rounded border border-current/15 bg-black/20 px-1.5 py-0.5 text-[10px]">
                        受控动作{" "}
                        {message.decisionRoute.governedActionIds.length}
                      </span>
                    )}
                    {message.decisionRoute.blockedReasons.length > 0 && (
                      <span className="rounded border border-current/15 bg-black/20 px-1.5 py-0.5 text-[10px]">
                        阻断 {message.decisionRoute.blockedReasons.length}
                      </span>
                    )}
                  </div>
                </div>
              )}
              {message.flow && message.flow.length > 0 && (
                <div className="mt-3 rounded-md border border-slate-800 bg-black/30 p-2 font-mono text-[10px] leading-5 text-slate-400">
                  {message.flow.map((line) => (
                    <div key={line}>{line}</div>
                  ))}
                </div>
              )}
              {message.receipt && (
                <div className="mt-3 rounded-md border border-emerald-500/20 bg-emerald-500/10 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-sm font-medium text-emerald-100">
                      <ClipboardCheck className="size-4" />
                      {message.receipt.title}
                    </div>
                    <span className="rounded border border-emerald-400/30 px-1.5 py-0.5 font-mono text-[10px] text-emerald-200">
                      [{message.receipt.status || "EXECUTED"}]
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-emerald-100/80">
                    {message.receipt.summary}
                  </div>
                  <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                    {message.receipt.rows.map((row) => (
                      <div
                        key={`${row.label}:${row.value}`}
                        className="rounded border border-emerald-400/10 bg-slate-950/40 px-2 py-1"
                      >
                        <div className="text-[10px] text-emerald-100/60">
                          {row.label}
                        </div>
                        <div className="truncate font-mono text-[10px] text-emerald-50">
                          {row.value}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {message.actions && message.actions.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {message.actions.map((action) => (
                    <Button
                      key={action.actionId}
                      size="sm"
                      variant="outline"
                      className="h-7 border-slate-700 text-xs text-slate-200 hover:bg-slate-900"
                      title={
                        action.contractRef
                          ? `${action.reason} · ${action.contractRef}`
                          : action.reason
                      }
                      onClick={() => {
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
                      }}
                    >
                      <Sparkles className="mr-1 size-3" />
                      {action.label}
                    </Button>
                  ))}
                </div>
              )}
              {message.role === "agi" &&
                !message.actions?.some(
                  (action) => action.actionId === "open_evidence_black_box",
                ) && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 border-slate-700 text-xs text-slate-200 hover:bg-slate-900"
                      onClick={onOpenAdvanced}
                    >
                      <Eye className="mr-1 size-3" />
                      查看证据黑匣子
                    </Button>
                  </div>
                )}
            </div>
          ))}
        </div>

        {pendingAction && (
          <div
            className={cn(
              "rounded-lg border p-3",
              actionRiskToSeverity(pendingAction) === "danger"
                ? "border-amber-500/25 bg-amber-500/10 text-amber-100"
                : "border-slate-700 bg-slate-950/70 text-slate-200",
            )}
            data-testid="agi-action-confirmation"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <ShieldCheck className="size-4 shrink-0" />
                  受控动作确认
                </div>
                <div className="mt-1 text-xs leading-5 opacity-80">
                  {pendingAction.label} 将通过 Polaris 公开契约进入治理链路，
                  不会由 AGI 直接写文件、执行 Director 修复或放行失败门禁。
                </div>
              </div>
              <Badge className="border-current/20 bg-black/20 text-current">
                风险 {formatAgiUiToken(pendingAction.riskLevel)}
              </Badge>
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-4">
              <div className="rounded border border-current/15 bg-black/20 px-2 py-1.5">
                <div className="text-[10px] opacity-60">契约</div>
                <div
                  className="mt-0.5 truncate font-mono text-[10px]"
                  title={pendingAction.contractRef}
                >
                  {pendingAction.contractRef || "resident public contract"}
                </div>
              </div>
              <div className="rounded border border-current/15 bg-black/20 px-2 py-1.5">
                <div className="text-[10px] opacity-60">边界</div>
                <div
                  className="mt-0.5 truncate font-mono text-[10px]"
                  title={pendingAction.executionBoundary}
                >
                  {pendingAction.executionBoundary || pendingAction.mode}
                </div>
              </div>
              <div className="rounded border border-current/15 bg-black/20 px-2 py-1.5">
                <div className="text-[10px] opacity-60">角色链</div>
                <div className="mt-0.5 truncate font-mono text-[10px]">
                  项目经理→总工程师→执行官→质检
                </div>
              </div>
              <div className="rounded border border-current/15 bg-black/20 px-2 py-1.5">
                <div className="text-[10px] opacity-60">参与开关</div>
                <div className="mt-0.5 truncate font-mono text-[10px]">
                  {pendingAction.requiresParticipation ? "必需" : "不需要"}
                </div>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <div className="text-[11px] opacity-70">
                AGI 直接执行：
                {pendingAction.agiDirectExecutionAllowed ? "允许" : "已阻断"}
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-xs text-slate-300 hover:text-white"
                  onClick={onCancelAction}
                >
                  取消
                </Button>
                <Button
                  size="sm"
                  className="h-7 bg-slate-100 text-xs text-slate-950 hover:bg-white"
                  disabled={disabled}
                  onClick={onConfirmAction}
                >
                  提交受控动作
                </Button>
              </div>
            </div>
          </div>
        )}

        <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-2">
          <div
            className="mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-3"
            data-testid="agi-quick-command-bar"
          >
            {visibleQuickCommands.map((item) => (
              <button
                key={item.command}
                type="button"
                aria-label={item.label}
                className={cn(
                  "flex min-h-9 cursor-pointer items-center gap-2 rounded border px-2 py-1.5 text-left text-[11px] transition-colors",
                  quickCommandClass(item.severity),
                )}
                title={
                  item.detail
                    ? `${item.detail} · ${item.command}`
                    : item.command
                }
                onClick={() => onQuickCommand(item.command)}
              >
                <span className="grid size-5 shrink-0 place-items-center rounded border border-current/15 bg-black/20">
                  {quickCommandIcon(item.icon)}
                </span>
                <span className="min-w-0">
                  <span className="block truncate font-medium">
                    {item.label}
                  </span>
                  {item.detail && (
                    <span
                      className="block truncate text-[10px] opacity-60"
                      aria-hidden="true"
                    >
                      {item.detail}
                    </span>
                  )}
                </span>
              </button>
            ))}
          </div>
          <label htmlFor="agi-tactical-console-input" className="sr-only">
            给驻场 AGI 下达指令
          </label>
          <div className="flex items-end gap-2">
            <Textarea
              id="agi-tactical-console-input"
              aria-label="给驻场 AGI 下达指令"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  onSubmit();
                }
              }}
              placeholder="例如：帮我看下当前项目进度，为什么卡住了？"
              className="min-h-16 resize-none border-slate-800 bg-slate-950 text-sm text-slate-100 placeholder:text-slate-600"
            />
            <Button
              size="sm"
              className="h-10 bg-slate-100 text-slate-950 hover:bg-white"
              disabled={disabled || !value.trim()}
              onClick={onSubmit}
              data-testid="agi-console-submit"
            >
              <Send className="size-4" />
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function AgiActionTimeline({ entries }: { entries: AgiActionTimelineEntry[] }) {
  return (
    <Card
      className="border-slate-800/80 bg-slate-950/60"
      data-testid="agi-action-timeline"
    >
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 text-sm text-slate-300">
            <ClipboardCheck className="size-4 text-slate-400" />
            最近行动轨迹
          </CardTitle>
          <Badge className="border-slate-700 bg-slate-950 text-[10px] text-slate-400">
            回执 / 路线 / 指令流
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <div className="rounded-md border border-slate-800 bg-slate-950/70 px-3 py-3 text-sm text-slate-500">
            等待用户指令。行动轨迹只展示常驻 AGI
            契约返回的回执、决策路线和指令流。
          </div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
            {entries.map((entry) => (
              <div
                key={entry.id}
                className={cn(
                  "min-h-32 rounded-md border p-3",
                  severityClass(entry.severity),
                )}
                data-testid="agi-action-timeline-entry"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">
                      {entry.title}
                    </div>
                    <div className="mt-1 truncate font-mono text-[10px] opacity-60">
                      {entry.source}
                    </div>
                  </div>
                  <span className="shrink-0 rounded border border-current/20 px-1.5 py-0.5 font-mono text-[10px]">
                    {formatAgiUiToken(entry.status)}
                  </span>
                </div>
                <div className="mt-2 line-clamp-2 text-xs leading-5 opacity-80">
                  {entry.summary}
                </div>
                {entry.actionIds.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1">
                    {entry.actionIds.map((actionId) => (
                      <span
                        key={actionId}
                        className="rounded border border-current/15 bg-black/20 px-1.5 py-0.5 font-mono text-[10px]"
                      >
                        {actionId}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function ResidentWorkspace({
  workspace,
  onBackToMain,
  residentSnapshot = null,
  initialTab = "overview",
  residentAgiLlmStatus = null,
}: ResidentWorkspaceProps) {
  const resident = useResident({ workspace, liveResident: residentSnapshot });
  const [activeTab, setActiveTab] = useState<AgiTab>(initialTab);
  const [showNewGoal, setShowNewGoal] = useState(initialTab === "goals");
  const [expandedGoal, setExpandedGoal] = useState<string | null>(null);

  // New goal form state
  const [newGoalTitle, setNewGoalTitle] = useState("");
  const [newGoalDesc, setNewGoalDesc] = useState("");
  const [agiDecisionObjective, setAgiDecisionObjective] = useState(
    "审计当前运行证据，判断是否允许进入下一步。",
  );
  const [agiDecisionType, setAgiDecisionType] = useState(
    "evidence.interface.selection",
  );

  // Identity edit state
  const [editingIdentity, setEditingIdentity] = useState(false);
  const [identityName, setIdentityName] = useState("");
  const [identityMission, setIdentityMission] = useState("");
  const [agiParticipationEnabled, setAgiParticipationEnabled] = useState(false);
  const [agiParticipationScopes, setAgiParticipationScopes] = useState<
    string[]
  >([]);
  const [advancedAuditOpen, setAdvancedAuditOpen] = useState(false);
  const [operatorSettingsOpen, setOperatorSettingsOpen] = useState(false);
  const [consoleInput, setConsoleInput] = useState("");
  const [pendingConsoleAction, setPendingConsoleAction] =
    useState<AgiConsoleAction | null>(null);
  const [consoleMessages, setConsoleMessages] = useState<AgiConsoleMessage[]>([
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
  const agiActionTimelineEntries = useMemo(
    () => buildAgiActionTimeline(consoleMessages),
    [consoleMessages],
  );

  const isActive = Boolean(resident.residentRuntime?.active);
  const mode = resident.residentRuntime?.mode || "observe";
  const runtimeEvidence = resident.residentRuntimeEvidence;
  const residentAgiParticipation =
    resident.residentIdentity?.resident_agi_participation || null;
  const residentAgiParticipationEnabled = Boolean(
    residentAgiParticipation?.enabled,
  );
  const residentAgiLlmProvider = String(
    residentAgiLlmStatus?.providerName ||
      residentAgiLlmStatus?.providerId ||
      "",
  ).trim();
  const residentAgiLlmModel = String(residentAgiLlmStatus?.model || "").trim();
  const residentAgiLlmBound = Boolean(
    residentAgiLlmProvider && residentAgiLlmModel,
  );
  const residentAgiLlmBlocked = Boolean(
    residentAgiLlmStatus?.blocked || residentAgiLlmStatus?.unsupported,
  );
  const residentAgiLlmReady = Boolean(
    residentAgiLlmStatus?.ready &&
    residentAgiLlmBound &&
    !residentAgiLlmBlocked,
  );
  const residentAgiLlmRiskVisible =
    residentAgiParticipationEnabled &&
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
  const agiAuthorityMatrix =
    agiAuditPack?.authority_matrix || agiCapabilitySurface?.authority_matrix;
  const agiDecisionProfile = agiAuditPack?.decision_profile;
  const tickAutonomyBoundary =
    resident.residentRuntime?.last_summary?.autonomy_boundary ||
    agiAuditPack?.autonomy_boundary ||
    null;
  const agiCapabilities = agiCapabilitySurface?.items || [];
  const agiDecisionCapabilities =
    agiCapabilitySurface?.decision_capabilities || [];
  const hardcodedRepairCatalog =
    agiCapabilitySurface?.hardcoded_repair_strategy_catalog || null;
  const repairAdvisoryPolicy =
    agiCapabilitySurface?.director_repair_advisory_policy || null;
  const agiDecisionCapabilityRegistry =
    agiCapabilitySurface?.decision_capability_registry ||
    agiDecisionProfile?.decision_capability_registry;
  const agiCapabilityAccessRegistry =
    agiCapabilitySurface?.capability_access_registry || null;
  const agiEvidenceInterfaceContract =
    agiCapabilitySurface?.evidence_interface_contract;
  const agiDecisionBoundaries = agiCapabilitySurface?.decision_boundaries || [];
  const agiDecisionBoundaryPolicy =
    agiCapabilitySurface?.decision_boundary_policy || null;
  const agiParticipationPolicy =
    resident.status?.agi_participation_policy ||
    agiCapabilitySurface?.participation_policy ||
    null;
  const lastAgiDecisionHandoff =
    resident.lastAgiDecisionResult?.decision_handoff || null;
  const lastRepairAdvisoryOverlay =
    resident.lastAgiDecisionResult?.repair_advisory_overlay || null;
  const queriedRepairAdvisoryOverlay =
    resident.residentAgiRepairAdvisoryOverlay?.overlay || null;
  const queriedRepairAdvisoryOverlaySource =
    resident.residentAgiRepairAdvisoryOverlay?.found &&
    resident.residentAgiRepairAdvisoryOverlay?.decision_ref?.decision_id
      ? `public_query:${shortDecisionId(
          resident.residentAgiRepairAdvisoryOverlay.decision_ref.decision_id,
        )}`
      : resident.residentAgiRepairAdvisoryOverlay?.found
        ? "public_query"
        : "";
  const auditPackRepairAdvisoryOverlay =
    agiAuditPack?.repair_advisory_overlay_query?.overlay ||
    agiAuditPack?.latest_repair_advisory_overlay ||
    null;
  const auditPackRepairAdvisoryOverlaySource =
    agiAuditPack?.repair_advisory_overlay_query?.found &&
    agiAuditPack.repair_advisory_overlay_query.decision_ref?.decision_id
      ? `audit_pack_query:${shortDecisionId(
          agiAuditPack.repair_advisory_overlay_query.decision_ref.decision_id,
        )}`
      : auditPackRepairAdvisoryOverlay
        ? "audit_pack"
        : "";
  const persistedRepairAdvisoryOverlay = useMemo(
    () => latestDecisionRepairAdvisoryOverlay(resident.decisions),
    [resident.decisions],
  );
  const activeRepairAdvisoryOverlay =
    lastRepairAdvisoryOverlay ||
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
    const dynamicOptions =
      agiParticipationPolicy?.available_scopes
        ?.map((scope): AgiParticipationOption | null => {
          const scopeId = String(scope.scope_id || "").trim();
          if (!scopeId) return null;
          return {
            scope: scopeId,
            label:
              String(scope.name || "").trim() ||
              AGI_PARTICIPATION_LABELS[scopeId] ||
              scopeId,
            category: String(scope.category || "").trim() || undefined,
            riskLevel: String(scope.risk_level || "").trim() || undefined,
          };
        })
        .filter((scope): scope is AgiParticipationOption => scope !== null) ||
      [];
    const flags =
      agiParticipationPolicy?.participation_flags?.filter(Boolean) ||
      DEFAULT_AGI_PARTICIPATION_FLAGS;
    const flagOptions: AgiParticipationOption[] = flags.map((scope) => ({
      scope,
      label: AGI_PARTICIPATION_LABELS[scope] || scope,
    }));
    const seen = new Set<string>();
    return [...dynamicOptions, ...flagOptions].filter((option) => {
      const key = normalizeAgiParticipationScope(option.scope);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [agiParticipationPolicy]);
  useEffect(() => {
    setAgiParticipationEnabled(Boolean(residentAgiParticipation?.enabled));
    setAgiParticipationScopes(
      selectedAgiParticipationScopes(residentAgiParticipation),
    );
  }, [residentAgiParticipation]);
  const agiDecisionTypeOptions = useMemo(() => {
    const options = agiDecisionCapabilities
      .map((capability) => {
        const decisionId = String(capability.decision_id || "").trim();
        if (!decisionId) return null;
        return {
          decisionId,
          label: String(capability.name || decisionId).trim(),
          owner: String(capability.owner || "").trim(),
          riskLevel: String(capability.risk_level || "").trim(),
        };
      })
      .filter(
        (
          option,
        ): option is {
          decisionId: string;
          label: string;
          owner: string;
          riskLevel: string;
        } => option !== null,
      );
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
    if (agiDecisionTypeOptions.length === 0) return;
    if (
      agiDecisionTypeOptions.some(
        (option) => option.decisionId === agiDecisionType,
      )
    ) {
      return;
    }
    setAgiDecisionType(agiDecisionTypeOptions[0].decisionId);
  }, [agiDecisionType, agiDecisionTypeOptions]);
  const selectedAgiDecisionCapability =
    agiDecisionCapabilities.find(
      (capability) => capability.decision_id === agiDecisionType,
    ) || null;
  const identityParticipationScopes = selectedAgiParticipationScopes(
    residentAgiParticipation,
  );
  const decisionStats = useMemo(
    () => buildDecisionStats(resident.decisions),
    [resident.decisions],
  );
  const capabilityGovernance = useMemo(
    () => buildCapabilityGovernanceStats(agiCapabilities),
    [agiCapabilities],
  );
  const totalGoals = resident.goals.length;
  const agiRepairAdvisoryParticipationEnabled =
    agiParticipationEnabled &&
    AGI_REPAIR_ADVISORY_SCOPE_IDS.some((scope) =>
      isAgiParticipationScopeSelected(scope, agiParticipationScopes),
    );
  const hardRuleGateStatus = String(
    agiAuditPack?.hard_rule_gate?.status || "unknown",
  ).toLowerCase();
  const evidenceGateStatus = String(
    agiAuditPack?.evidence_gate?.status || "unknown",
  ).toLowerCase();
  const runLedgerStatus = String(
    agiAuditPack?.run_ledger_summary?.status || "unknown",
  ).toLowerCase();
  const evidenceMatrixSummary =
    agiEvidenceInterfaces?.capability_matrix?.summary || null;
  const requiredEvidenceTotal = Number(evidenceMatrixSummary?.required || 0);
  const requiredEvidenceAvailable = Number(
    evidenceMatrixSummary?.required_available || 0,
  );
  const runtimeEvidenceTotal = Number(
    agiEvidenceInterfaces?.summary?.total || 0,
  );
  const runtimeEvidenceAvailable = Number(
    agiEvidenceInterfaces?.summary?.available || 0,
  );
  const missingRequiredEvidence = Number(
    evidenceMatrixSummary?.missing_required ||
      agiEvidenceInterfaces?.summary?.missing_required_interface_ids?.length ||
      0,
  );
  const cockpitEvidenceCoverage =
    requiredEvidenceTotal > 0
      ? `${requiredEvidenceAvailable}/${requiredEvidenceTotal}`
      : runtimeEvidenceTotal > 0
        ? `${runtimeEvidenceAvailable}/${runtimeEvidenceTotal}`
        : "暂无";
  const roleTrackItems = useMemo(
    () =>
      buildAgiRoleTrackItems({
        runtimeActive: isActive,
        pendingGoalCount:
          resident.residentAgenda?.pending_goal_ids?.length ?? 0,
        approvedGoalCount:
          resident.residentAgenda?.approved_goal_ids?.length ?? 0,
        materializedGoalCount:
          resident.residentAgenda?.materialized_goal_ids?.length ?? 0,
        decisionCount: resident.decisions.length,
        handoff: lastAgiDecisionHandoff,
        evidenceGateStatus,
        runLedgerStatus,
      }),
    [
      evidenceGateStatus,
      isActive,
      lastAgiDecisionHandoff,
      resident.decisions.length,
      resident.residentAgenda?.approved_goal_ids?.length,
      resident.residentAgenda?.materialized_goal_ids?.length,
      resident.residentAgenda?.pending_goal_ids?.length,
      runLedgerStatus,
    ],
  );
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
  const cockpitSeverity: AgiSeverity = !isActive
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
  const cockpitMission =
    currentFocus || resident.goals[0]?.title || "等待新的平台看护任务";
  const cockpitNextAction =
    cockpitSeverity === "danger"
      ? "先查看失败证据并交给受控角色链处理，AGI 不能把失败门禁标记为通过。"
      : cockpitSeverity === "warn"
        ? "先补齐模型绑定、证据接口或运行态证据，再允许 AGI 给出推进建议。"
        : "当前可以继续值守；如需深入排查，可让 AGI 解释当前状态或刷新证据。";
  const trustSignals: AgiTrustSignal[] = [
    {
      label: "角色链路",
      value: "项目经理→总工程师→执行官→质检",
      severity: "ok",
    },
    {
      label: "证据门禁",
      value: formatAgiUiToken(evidenceGateStatus),
      severity:
        evidenceGateStatus === "fail"
          ? "danger"
          : evidenceGateStatus === "pass"
            ? "ok"
            : "warn",
    },
    {
      label: "任务账本",
      value: formatAgiUiToken(runLedgerStatus),
      severity:
        runLedgerStatus === "failed"
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
    const commands: AgiQuickCommand[] = [];
    const seen = new Set<string>();
    const catalogCommand = (
      actionId: string,
      fallback: AgiQuickCommand,
    ): AgiQuickCommand => {
      const action = findAgiCatalogAction(agiActionCatalog, actionId);
      if (!action) return fallback;
      return {
        ...fallback,
        label: String(action.label || "").trim() || fallback.label,
        detail: shortQuickCommandDetail(
          String(action.reason || "").trim(),
          fallback.detail || "",
        ),
        severity:
          fallback.severity === "danger"
            ? "danger"
            : actionRiskToSeverity(action),
      };
    };
    const pushCommand = (command: AgiQuickCommand) => {
      const key = `${command.label}:${command.command}`;
      if (seen.has(key)) return;
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

    pushCommand(
      catalogCommand("refresh_evidence_interfaces", {
        label: "刷新证据",
        command: "/刷新证据",
        detail: "重读事实源",
        icon: "evidence",
        severity: evidenceGateStatus === "fail" ? "danger" : "idle",
      }),
    );

    if (residentAgiLlmRiskVisible) {
      pushCommand({
        label: "检查 AGI 模型",
        command: "请检查 Resident AGI 的模型绑定、参与开关和当前阻塞。",
        detail: "先修复绑定",
        icon: "model",
        severity: "warn",
      });
    } else if (residentAgiParticipationEnabled) {
      pushCommand(
        catalogCommand("request_resident_agi_judgement", {
          label: "请求 AGI 判断",
          command: "请让 AGI 基于当前证据判断下一步怎么办。",
          detail: "角色回合",
          icon: "judgement",
          severity: "ok",
        }),
      );
    }

    if (agiBlockers.length > 0 && residentAgiParticipationEnabled) {
      pushCommand(
        catalogCommand("request_director_controlled_repair", {
          label: "交给修复链",
          command: "交给 Director 受控修复这个阻塞。",
          detail: "治理目标",
          icon: "repair",
          severity: "danger",
        }),
      );
    } else {
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

  const toggleAgiParticipationScope = (scope: string) => {
    const scopeKey = normalizeAgiParticipationScope(scope);
    setAgiParticipationScopes((current) =>
      current.some((item) => normalizeAgiParticipationScope(item) === scopeKey)
        ? current.filter(
            (item) => normalizeAgiParticipationScope(item) !== scopeKey,
          )
        : [...current, scope],
    );
  };
  const setAgiRepairAdvisoryParticipation = (enabled: boolean) => {
    setAgiParticipationScopes((current) => {
      const selectedKeys = new Set(
        current.map((item) => normalizeAgiParticipationScope(item)),
      );
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
      return current.filter(
        (scope) =>
          !AGI_REPAIR_ADVISORY_SCOPE_IDS.some(
            (repairScope) =>
              normalizeAgiParticipationScope(repairScope) ===
              normalizeAgiParticipationScope(scope),
          ),
      );
    });
  };
  const handleSaveAgiParticipation = async () => {
    await resident.saveIdentity({
      resident_agi_participation: {
        enabled: agiParticipationEnabled,
        scopes: agiParticipationScopes,
        participation: buildAgiParticipationFlags(
          agiParticipationScopes,
          agiParticipationOptions.map((option) => option.scope),
        ),
        custom_scopes_allowed:
          resident.residentIdentity?.resident_agi_participation
            ?.custom_scopes_allowed ?? true,
      },
    });
  };

  const handleCreateGoal = async () => {
    if (!newGoalTitle.trim()) return;
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
    if (!objective) return;
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
        resident_agi_available: Boolean(
          agiAuditPack?.role_registry?.resident_agi_available,
        ),
        resident_agi_hard_rule_gate_status:
          agiAuditPack?.hard_rule_gate?.status || "",
        resident_agi_evidence_gate_status:
          agiAuditPack?.evidence_gate?.status || "",
        resident_agi_evidence_gate_recommended_verdict:
          agiAuditPack?.evidence_gate?.recommended_verdict || "",
        resident_agi_authority_matrix_schema:
          agiAuthorityMatrix?.schema_version || "",
        resident_agi_chain_required: Boolean(
          agiAuthorityMatrix?.chain_required,
        ),
        resident_agi_decision_profile_schema:
          agiDecisionProfile?.schema_version || "",
        resident_agi_decision_profile_recommended_verdict:
          agiDecisionProfile?.recommended_verdict || "",
        resident_agi_decision_profile_next_action:
          agiDecisionProfile?.recommended_next_action || "",
        resident_agi_role_turn_allowed: Boolean(
          agiDecisionProfile?.role_turn_allowed,
        ),
        resident_agi_downstream_precheck:
          agiDecisionProfile?.downstream_precheck || "",
        selected_decision_capability_id:
          selectedAgiDecisionCapability?.decision_id || agiDecisionType,
        selected_decision_capability_name:
          selectedAgiDecisionCapability?.name || "",
        selected_decision_capability_owner:
          selectedAgiDecisionCapability?.owner || "",
        selected_decision_capability_risk:
          selectedAgiDecisionCapability?.risk_level || "",
        selected_decision_required_evidence_interfaces:
          selectedAgiDecisionCapability?.required_evidence_interfaces || [],
        selected_decision_optional_evidence_interfaces:
          selectedAgiDecisionCapability?.optional_evidence_interfaces || [],
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

  const appendConsoleMessage = (message: Omit<AgiConsoleMessage, "id">) => {
    setConsoleMessages((current) => [
      ...current,
      { ...message, id: buildConsoleId(message.role) },
    ]);
  };

  const toConsoleReceipt = (
    response: ResidentAgiTacticalChatResponse,
  ): AgiConsoleReceipt | undefined => {
    const receipt = response.receipt;
    if (!receipt) return undefined;
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

  const toConsoleMissionBrief = (
    brief: ResidentAgiTacticalMissionBriefPayload | null | undefined,
  ): AgiConsoleMissionBrief | undefined => {
    if (!brief) return undefined;
    const severityRaw = String(brief.severity || "").trim();
    const severity: AgiSeverity =
      severityRaw === "ok" ||
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
      progressPercent: Math.max(
        0,
        Math.min(100, Number(brief.progress_percent || 0)),
      ),
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

  const toConsoleToolTrace = (
    trace: ResidentAgiTacticalToolTracePayload | null | undefined,
  ): AgiConsoleToolTrace | undefined => {
    if (!trace) return undefined;
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
    if (items.length === 0) return undefined;
    return {
      schemaVersion: String(
        trace.schema_version || "resident.agi_tactical_tool_trace.v1",
      ),
      items,
    };
  };

  const toConsoleDecisionRoute = (
    route: ResidentAgiTacticalDecisionRoutePayload | null | undefined,
  ): AgiConsoleDecisionRoute | undefined => {
    if (!route) return undefined;
    const status = String(route.route_status || "").trim();
    if (!status) return undefined;
    return {
      schemaVersion: String(
        route.schema_version || "resident.agi_tactical_decision_route.v1",
      ),
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

  const toConsoleParticipationGate = (
    gate: ResidentAgiTacticalParticipationGatePayload | null | undefined,
  ): AgiConsoleParticipationGate | undefined => {
    if (!gate) return undefined;
    const status = String(gate.status || "").trim();
    if (!status) return undefined;
    return {
      schemaVersion: String(
        gate.schema_version || "resident.agi_tactical_participation_gate.v1",
      ),
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
      directPermissionChangeAllowed: Boolean(
        gate.agi_direct_permission_change_allowed,
      ),
    };
  };

  const toConsoleActions = (
    actions: ResidentAgiTacticalChatActionPayload[] | undefined,
    sourceMessage: string,
  ): AgiConsoleAction[] =>
    (actions || [])
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

  const consoleStatusAnswer = (): string => {
    const blockerText = agiBlockers.length
      ? `当前我看到 ${agiBlockers.length} 个需要注意的问题：${agiBlockers
          .slice(0, 2)
          .join("；")}。`
      : "当前没有发现需要人工处理的阻断项。";
    return `我已读取当前 Polaris 元项目投影。${cockpitStatusLabel}：${cockpitMission}。目标 ${totalGoals} 个，决策 ${decisionStats.total} 条，证据覆盖 ${decisionStats.evidenceBacked}/${decisionStats.total}。${blockerText}`;
  };

  const handleConsoleCommand = async (rawCommand?: string) => {
    const command = String(rawCommand ?? consoleInput).trim();
    if (!command) return;
    setConsoleInput("");
    setPendingConsoleAction(null);
    appendConsoleMessage({ role: "user", text: command });

    const normalized = command.toLowerCase();
    const flow = [
      "[授权] 校验当前 workspace 绑定... 通过",
      "[事实源] 读取 Resident runtime / audit pack / evidence projection",
      "[边界] 高风险写入与门禁放行仍交由角色链处理",
    ];

    if (
      normalized.includes("刷新证据") ||
      normalized.includes("evidence") ||
      normalized.includes("证据接口")
    ) {
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
      participationGate: toConsoleParticipationGate(
        chatResponse.participation_gate,
      ),
      decisionRoute: toConsoleDecisionRoute(chatResponse.decision_route),
      receipt: toConsoleReceipt(chatResponse),
      actions: toConsoleActions(chatResponse.suggested_actions, command),
    });
  };

  const handleConsoleAction = async (action: AgiConsoleAction) => {
    setPendingConsoleAction(null);
    const executesThroughBackend =
      action.uiHandler === "execute_governed_action" ||
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
      const blockedBoundary =
        action.actionId === "request_resident_agi_judgement"
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
        actions: toConsoleActions(
          result?.follow_up_actions,
          action.sourceMessage || action.reason,
        ),
        receipt: result?.receipt
          ? {
              title: result.receipt.title || "受控动作阻断凭证",
              summary:
                result.receipt.summary || result.reason || "受控动作未执行。",
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
            label:
              action.actionId === "request_resident_agi_judgement"
                ? "决策"
                : "目标",
            value:
              action.actionId === "request_resident_agi_judgement"
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
        actions: toConsoleActions(
          result.follow_up_actions,
          action.sourceMessage || action.reason,
        ),
        receipt: {
          title: result.receipt?.title || "AGI 判断凭证",
          summary:
            result.receipt?.summary ||
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
      actions: toConsoleActions(
        result.follow_up_actions,
        action.sourceMessage || action.reason,
      ),
      receipt: {
        title: result.receipt?.title || "受控动作执行凭证",
        summary:
          result.receipt?.summary ||
          "已通过 Resident public contract 创建目标并写入 decision trace。",
        status: result.receipt?.status || "EXECUTED",
        rows: receiptRows,
      },
    });
  };

  return (
    <div
      data-testid="resident-workspace"
      className="flex h-full flex-col bg-slate-950 text-slate-100"
    >
      {/* Simplified Header */}
      <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBackToMain}
            className="text-slate-400 hover:text-white"
          >
            <ArrowLeft className="size-4" />
          </Button>
          <div className="flex items-center gap-2">
            <Bot className="size-5 text-slate-300" />
            <span className="font-medium">AGI 工作区</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Badge
            variant="outline"
            className={cn(
              "border-slate-700 bg-slate-950/40",
              isActive ? "text-slate-100" : "text-slate-500",
            )}
          >
            {isActive ? "运行中" : "已停止"}
          </Badge>
          {isActive ? (
            <Button
              size="sm"
              variant="destructive"
              onClick={() => void resident.stop()}
            >
              <Square className="mr-1 size-3" />
              停止
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() => void resident.start(mode)}
              className="bg-slate-100 text-slate-950 hover:bg-white"
            >
              <Play className="mr-1 size-3" />
              启动
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            data-testid="resident-tick"
            title="立即运行一轮反思 (Tick)：元认知 / 技能 / 反事实 / 自改 / 目标生成"
            onClick={() => void resident.tick()}
            disabled={resident.isActing("tick")}
            className="border-slate-700 text-slate-200 hover:bg-slate-900"
          >
            <Brain
              className={cn(
                "mr-1 size-3",
                resident.isActing("tick") && "animate-pulse",
              )}
            />
            反思一轮
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void resident.refresh()}
            disabled={resident.loading}
          >
            <RefreshCw
              className={cn("size-4", resident.loading && "animate-spin")}
            />
          </Button>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 overflow-auto p-4">
        {/* Tabs */}
        <div className="mb-4 flex gap-1 border-b border-slate-800">
          {[
            { key: "overview", label: "概览" },
            { key: "goals", label: "目标" },
            { key: "decisions", label: "决策" },
            { key: "evolution", label: "进化" },
          ].map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key as AgiTab)}
              data-testid={`resident-tab-${tab.key}`}
              className={cn(
                "px-4 py-2 text-sm font-medium transition-colors",
                activeTab === tab.key
                  ? "border-b-2 border-slate-200 text-slate-100"
                  : "text-slate-400 hover:text-slate-200",
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {activeTab === "overview" && (
          <div className="space-y-3">
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(380px,0.85fr)]">
              <AgiCockpitOverview
                statusLabel={cockpitStatusLabel}
                statusDetail={cockpitStatusDetail}
                severity={cockpitSeverity}
                mission={cockpitMission}
                nextAction={cockpitNextAction}
                blockers={agiBlockers}
                trustSignals={trustSignals}
                roleTrackItems={roleTrackItems}
                goalsCount={totalGoals}
                decisionsCount={decisionStats.total}
                evidenceCoverage={cockpitEvidenceCoverage}
                lastUpdated={formatTime(resident.residentRuntime?.last_tick_at)}
                onOpenAdvanced={() => setAdvancedAuditOpen(true)}
                onExplainBlocker={() => void handleConsoleCommand("/解释卡住")}
                onRunTick={() => void handleConsoleCommand("/反思一轮")}
              />
              <AgiTacticalConsole
                messages={consoleMessages}
                value={consoleInput}
                disabled={Boolean(resident.actionKey)}
                quickCommands={agiTacticalQuickCommands}
                pendingAction={pendingConsoleAction}
                onChange={setConsoleInput}
                onSubmit={() => void handleConsoleCommand()}
                onQuickCommand={(command) => void handleConsoleCommand(command)}
                onAction={setPendingConsoleAction}
                onConfirmAction={() => {
                  if (pendingConsoleAction) {
                    void handleConsoleAction(pendingConsoleAction);
                  }
                }}
                onCancelAction={() => setPendingConsoleAction(null)}
                onOpenAdvanced={() => setAdvancedAuditOpen(true)}
                onOpenOperatorSettings={() => {
                  setOperatorSettingsOpen(true);
                  setEditingIdentity(false);
                }}
                onOpenGoals={() => {
                  setActiveTab("goals");
                  setShowNewGoal(false);
                }}
              />
            </div>

            <AgiActionTimeline entries={agiActionTimelineEntries} />

            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
              <Card
                className="border-slate-800 bg-slate-950/55"
                data-testid="agi-operator-briefing"
              >
                <CardContent className="p-4">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-500">
                        <Bot className="size-4 text-slate-400" />
                        值守机器人
                      </div>
                      <div className="mt-2 text-base font-semibold text-slate-50">
                        {resident.residentIdentity?.name || "常驻 AGI 监督员"}
                      </div>
                      <div className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">
                        {resident.residentIdentity?.mission ||
                          "尚未设定任务宣言"}
                      </div>
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        <Badge
                          className={cn(
                            "border text-xs",
                            residentAgiParticipationEnabled
                              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-200"
                              : "border-slate-700 bg-slate-950 text-slate-500",
                          )}
                        >
                          {residentAgiParticipationEnabled
                            ? "AGI 可参与"
                            : "AGI 仅观察"}
                        </Badge>
                        <Badge className="border-slate-700 bg-slate-950 text-xs text-slate-300">
                          受控执行
                        </Badge>
                        <Badge className="border-slate-700 bg-slate-950 text-xs text-slate-300">
                          直接写入已阻断
                        </Badge>
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-wrap gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        className="border-slate-700 text-slate-200 hover:bg-slate-900"
                        data-testid="resident-edit-identity"
                        onClick={() => {
                          setIdentityName(resident.residentIdentity?.name || "");
                          setIdentityMission(
                            resident.residentIdentity?.mission || "",
                          );
                          setOperatorSettingsOpen(true);
                          setEditingIdentity(true);
                        }}
                      >
                        <Pencil className="mr-1 size-3.5" />
                        编辑身份
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="border-slate-700 text-slate-200 hover:bg-slate-900"
                        data-testid="agi-open-operator-settings"
                        onClick={() =>
                          setOperatorSettingsOpen((open) => !open)
                        }
                      >
                        <Settings className="mr-1 size-3.5" />
                        {operatorSettingsOpen ? "收起设定" : "值守设定"}
                      </Button>
                    </div>
                  </div>
                  <div
                    className={cn(
                      "mt-4 rounded-md border px-3 py-2 text-xs",
                      residentAgiLlmReady
                        ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-100"
                        : residentAgiLlmRiskVisible
                          ? "border-amber-500/25 bg-amber-500/10 text-amber-200"
                          : "border-slate-800 bg-slate-950/70 text-slate-400",
                    )}
                    data-testid="resident-agi-llm-binding-status"
                  >
                    <div className="flex items-center gap-2 font-medium">
                      {residentAgiLlmReady ? (
                        <CheckCircle2 className="size-3.5" />
                      ) : residentAgiLlmRiskVisible ? (
                        <Ban className="size-3.5" />
                      ) : (
                        <Bot className="size-3.5" />
                      )}
                      <span>
                        {residentAgiLlmReady
                          ? "常驻 AGI 模型已绑定"
                          : residentAgiLlmRiskVisible
                            ? "常驻 AGI 参与已开启但模型不可用"
                            : "常驻 AGI 模型绑定状态未确认"}
                      </span>
                    </div>
                    <div className="mt-1 text-[11px] opacity-80">
                      {residentAgiLlmBound
                        ? `${residentAgiLlmProvider}/${residentAgiLlmModel}${
                            residentAgiLlmStatus?.grade
                              ? ` · ${residentAgiLlmStatus.grade}`
                              : ""
                          }`
                        : "请在 LLM 视觉配置编辑器中为常驻 AGI 绑定模型。"}
                      {residentAgiLlmStatus?.readinessIssue
                        ? ` ${residentAgiLlmStatus.readinessIssue}`
                        : ""}
                      {residentAgiLlmStatus?.runtimeIssue
                        ? ` ${residentAgiLlmStatus.runtimeIssue}`
                        : ""}
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card className="border-slate-800 bg-slate-950/55">
                <CardContent className="space-y-2 p-4">
                  <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-500">
                    <Target className="size-4 text-slate-400" />
                    快速入口
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="w-full justify-start border-slate-700 text-slate-200 hover:bg-slate-900"
                    onClick={() => setActiveTab("goals")}
                  >
                    <FileText className="mr-2 size-3.5" />
                    查看目标队列
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="w-full justify-start border-slate-700 text-slate-200 hover:bg-slate-900"
                    onClick={() => setActiveTab("decisions")}
                  >
                    <Brain className="mr-2 size-3.5" />
                    打开决策回合
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="w-full justify-start border-slate-700 text-slate-200 hover:bg-slate-900"
                    onClick={() => setAdvancedAuditOpen(true)}
                  >
                    <Eye className="mr-2 size-3.5" />
                    查看证据黑匣子
                  </Button>
                </CardContent>
              </Card>
            </div>

            {operatorSettingsOpen && (
              <div
                className="grid gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]"
                data-testid="agi-operator-settings"
              >
                <Card className="border-slate-800/80 bg-slate-950/45">
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm text-slate-300">
                    <Bot className="size-4 text-slate-400" />
                    AGI 身份设定
                  </CardTitle>
                  {!editingIdentity && (
                    <Button
                      size="sm"
                      variant="ghost"
                      data-testid="resident-edit-identity-inline"
                      onClick={() => {
                        setIdentityName(resident.residentIdentity?.name || "");
                        setIdentityMission(
                          resident.residentIdentity?.mission || "",
                        );
                        setAgiParticipationEnabled(
                          Boolean(
                            resident.residentIdentity
                              ?.resident_agi_participation?.enabled,
                          ),
                        );
                        setAgiParticipationScopes(
                          selectedAgiParticipationScopes(
                            resident.residentIdentity
                              ?.resident_agi_participation,
                          ),
                        );
                        setEditingIdentity(true);
                      }}
                    >
                      <Pencil className="size-3" />
                    </Button>
                  )}
                </CardHeader>
                <CardContent>
                  {editingIdentity ? (
                    <div className="space-y-2">
                      <Input
                        aria-label="AGI 名称"
                        value={identityName}
                        onChange={(e) => setIdentityName(e.target.value)}
                        placeholder="名称"
                        className="bg-slate-950"
                      />
                      <Textarea
                        aria-label="AGI 任务宣言"
                        value={identityMission}
                        onChange={(e) => setIdentityMission(e.target.value)}
                        placeholder="任务宣言"
                        className="bg-slate-950"
                      />
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          data-testid="resident-save-identity"
                          disabled={resident.isActing("save-identity")}
                          onClick={async () => {
                            await resident.saveIdentity({
                              name: identityName.trim(),
                              mission: identityMission.trim(),
                            });
                            setEditingIdentity(false);
                          }}
                          className="bg-slate-100 text-slate-950 hover:bg-white"
                        >
                          保存
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setEditingIdentity(false)}
                        >
                          取消
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="text-base font-medium text-white">
                        {resident.residentIdentity?.name || "常驻 AGI 监督员"}
                      </div>
                      <div className="mt-1 text-sm text-slate-400">
                        {resident.residentIdentity?.mission ||
                          "尚未设定任务宣言"}
                      </div>
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        <Badge
                          className={cn(
                            "border text-xs",
                            residentAgiParticipationEnabled
                              ? "border-slate-700 bg-slate-950 text-slate-200"
                              : "border-slate-700 bg-slate-950 text-slate-500",
                          )}
                        >
                          {residentAgiParticipationEnabled
                            ? "AGI 参与已开启"
                            : "AGI 参与未开启"}
                        </Badge>
                        {identityParticipationScopes
                          .slice(0, 4)
                          .map((scope) => (
                            <Badge
                              key={scope}
                              className="border-slate-700 bg-slate-950 text-xs text-slate-300"
                            >
                              {AGI_PARTICIPATION_LABELS[scope] || scope}
                            </Badge>
                          ))}
                      </div>
                      <div
                        className={cn(
                          "mt-3 rounded border px-3 py-2 text-xs",
                          residentAgiLlmReady
                            ? "border-slate-700 bg-slate-950/70 text-slate-200"
                            : residentAgiLlmRiskVisible
                              ? "border-amber-500/25 bg-amber-500/10 text-amber-200"
                              : "border-slate-800 bg-slate-950/70 text-slate-400",
                        )}
                        data-testid="resident-agi-llm-binding-status-inline"
                      >
                        <div className="flex items-center gap-2 font-medium">
                          {residentAgiLlmReady ? (
                            <CheckCircle2 className="size-3.5" />
                          ) : residentAgiLlmRiskVisible ? (
                            <Ban className="size-3.5" />
                          ) : (
                            <Bot className="size-3.5" />
                          )}
                          <span>
                            {residentAgiLlmReady
                              ? "常驻 AGI 模型已绑定"
                              : residentAgiLlmRiskVisible
                                ? "常驻 AGI 参与已开启但模型不可用"
                                : "常驻 AGI 模型绑定状态未确认"}
                          </span>
                        </div>
                        <div className="mt-1 text-[11px] opacity-80">
                          {residentAgiLlmBound
                            ? `${residentAgiLlmProvider}/${residentAgiLlmModel}${
                                residentAgiLlmStatus?.grade
                                  ? ` · ${residentAgiLlmStatus.grade}`
                                  : ""
                              }`
                            : "请在 LLM 视觉配置编辑器中为常驻 AGI 绑定模型。"}
                          {residentAgiLlmStatus?.readinessIssue
                            ? ` ${residentAgiLlmStatus.readinessIssue}`
                            : ""}
                          {residentAgiLlmStatus?.runtimeIssue
                            ? ` ${residentAgiLlmStatus.runtimeIssue}`
                            : ""}
                        </div>
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>

              <AgiParticipationDock
                enabled={agiParticipationEnabled}
                options={agiParticipationOptions}
                selectedScopes={agiParticipationScopes}
                repairAdvisoryEnabled={agiRepairAdvisoryParticipationEnabled}
                llmReady={residentAgiLlmReady}
                llmIssue={
                  residentAgiLlmRiskVisible
                    ? residentAgiLlmStatus?.readinessIssue ||
                      residentAgiLlmStatus?.runtimeIssue ||
                      "常驻 AGI 模型绑定不可用。"
                    : ""
                }
                isSaving={resident.isActing("save-identity")}
                onEnabledChange={setAgiParticipationEnabled}
                onToggleScope={toggleAgiParticipationScope}
                onToggleRepairAdvisory={setAgiRepairAdvisoryParticipation}
                onSave={() => void handleSaveAgiParticipation()}
                onOpenAdvanced={() => setAdvancedAuditOpen(true)}
              />
            </div>
            )}

            {advancedAuditOpen && latestInsight && (
              <Card className="border-slate-800 bg-slate-900/50">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm text-slate-300">
                    <FileSearch className="size-4 text-slate-400" />
                    最新元认知
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-1">
                    <div className="text-sm font-medium text-white">
                      {latestInsight.summary}
                    </div>
                    <div className="text-xs text-slate-500">
                      {latestInsight.strategy_tag ||
                        latestInsight.insight_type ||
                        "未分类"}{" "}
                      · 置信度{" "}
                      {Math.round((latestInsight.confidence ?? 0) * 100)}%
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {advancedAuditOpen && capabilities.length > 0 && (
              <Card className="border-slate-800 bg-slate-900/50">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm text-slate-300">
                    能力图谱
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {capabilities.slice(0, 4).map((capability) => (
                      <div
                        key={capability.capability_id}
                        className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2"
                      >
                        <div className="text-sm font-medium text-slate-200">
                          {capability.name}
                        </div>
                        <div className="mt-1 text-xs text-slate-500">
                          成功率{" "}
                          {Math.round((capability.success_rate ?? 0) * 100)}% ·
                          证据 {capability.evidence_count ?? 0}
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            <div
              className="rounded-lg border border-slate-800 bg-slate-950/70 p-3"
              data-testid="agi-advanced-audit-dock"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
                    <FileSearch className="size-4 text-slate-400" />
                    证据黑匣子
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    保存 Polaris 元项目审计细节：运行投影、能力边界、修复策略、
                    证据接口与审计包。默认收起，避免干扰驾驶舱。
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="border-slate-700 text-slate-200 hover:bg-slate-900"
                  onClick={() => setAdvancedAuditOpen((open) => !open)}
                  data-testid="agi-toggle-advanced-audit"
                >
                  {advancedAuditOpen ? (
                    <ChevronDown className="mr-1 size-3.5" />
                  ) : (
                    <ChevronRight className="mr-1 size-3.5" />
                  )}
                  {advancedAuditOpen ? "收起黑匣子" : "打开黑匣子"}
                </Button>
              </div>
            </div>

            {advancedAuditOpen && (
              <>
                <div
                  className="rounded-md border border-slate-800 bg-slate-950/45 px-3 py-2 font-mono text-[10px] text-slate-400"
                  data-testid="resident-runtime-evidence"
                >
                  <span>
                    {runtimeEvidence?.schema_version ||
                      "resident.runtime_projection_evidence.v1"}
                  </span>
                  <span>
                    {" "}
                    ·{" "}
                    {runtimeEvidence?.realtime_channel ||
                      "runtime.v2.status.resident"}
                  </span>
                  <span>
                    {" "}
                    ·{" "}
                    {runtimeEvidence?.snapshot_channel ||
                      "runtime.v2.status.snapshot"}
                  </span>
                  <span>
                    {" "}
                    · {runtimeEvidence?.projection_field || "snapshot.resident"}
                  </span>
                  <span>
                    {" "}
                    · 来源：
                    {runtimeEvidence?.source || formatAgiUiToken("unavailable")}
                  </span>
                </div>
                {tickAutonomyBoundary && (
                  <div
                    className="rounded-md border border-slate-800 bg-slate-950/45 px-3 py-2 font-mono text-[10px] text-slate-400"
                    data-testid="resident-tick-autonomy-boundary"
                  >
                    <span>
                      {tickAutonomyBoundary.schema_version ||
                        "resident.tick_autonomy_boundary.v1"}
                    </span>
                    <span>
                      {" "}
                      · 轮次角色：
                      {tickAutonomyBoundary.tick_role || "evidence_only"}
                    </span>
                    <span>
                      {" "}
                      · 判断入口：
                      {tickAutonomyBoundary.agi_judgement_entrypoint ||
                        "resident_agi_decision_turn"}
                    </span>
                    <span>
                      {" "}
                      · 旁路模型：
                      {tickAutonomyBoundary.sidecar_llm_allowed
                        ? formatAgiUiToken("allowed")
                        : formatAgiUiToken("blocked")}
                    </span>
                  </div>
                )}

                <Card className="border-slate-800 bg-slate-900/50">
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-2 text-sm text-slate-300">
                      <Brain className="size-4 text-slate-400" />
                      AGI 角色能力面
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="grid gap-2 sm:grid-cols-3">
                      <CapabilityMetric
                        label="角色"
                        value={agiCapabilitySurface?.role_id || "resident_agi"}
                      />
                      <CapabilityMetric
                        label="运行底座"
                        value={
                          agiCapabilitySurface?.runtime_foundation ||
                          "RoleRuntime / ContextOS / TurnEngine"
                        }
                      />
                      <CapabilityMetric
                        label="能力数"
                        value={String(
                          agiCapabilitySurface?.count ?? agiCapabilities.length,
                        )}
                      />
                    </div>
                    <div
                      className="mt-3 rounded-md border border-slate-800 bg-slate-950/45 px-3 py-2 text-xs text-slate-300"
                      data-testid="resident-agi-role-foundation"
                    >
                      <span className="font-mono text-slate-100">
                        resident_agi
                      </span>{" "}
                      运行在同一 RoleRuntime / ContextOS / TurnEngine
                      底座上；平台级证据访问更宽，但执行必须服从硬规则、能力目录、
                      权威契约和
                      {formatAgiRoleChain("PM → Chief Engineer → Director")}。
                    </div>
                    <CapabilityGovernanceMatrix
                      stats={capabilityGovernance}
                      authorityMatrix={agiAuthorityMatrix}
                      accessRegistry={agiCapabilityAccessRegistry}
                      runtimeFoundation={
                        agiCapabilitySurface?.runtime_foundation ||
                        "roles.runtime + ContextOS + TurnEngine"
                      }
                    />
                    <AgiRepairStrategyCatalogPanel
                      catalog={hardcodedRepairCatalog}
                    />
                    <AgiRepairAdvisoryPolicyPanel
                      policy={repairAdvisoryPolicy}
                    />
                    <AgiRepairAdvisoryOverlayPanel
                      overlay={activeRepairAdvisoryOverlay}
                      source={activeRepairAdvisoryOverlaySource}
                    />
                    <AgiDecisionCapabilityRegistry
                      schema={agiCapabilitySurface?.decision_capability_schema}
                      registry={agiDecisionCapabilityRegistry}
                      decisions={agiDecisionCapabilities}
                    />
                    <AgiEvidenceInterfaceMatrix
                      capabilities={agiCapabilities}
                      contract={agiEvidenceInterfaceContract}
                    />
                    <AgiEvidenceInterfaceReadiness
                      payload={agiEvidenceInterfaces}
                    />
                    <DecisionBoundaryMatrix
                      schema={agiCapabilitySurface?.decision_boundary_schema}
                      boundaries={agiDecisionBoundaries}
                    />
                    <DecisionBoundaryPolicyPanel
                      policy={agiDecisionBoundaryPolicy}
                    />
                    <AgiAuditPackPanel pack={agiAuditPack} />
                    <div className="mt-3 grid gap-2 lg:grid-cols-2">
                      {agiCapabilities.map((capability) => (
                        <div
                          key={capability.capability_id || capability.name}
                          className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-sm font-medium text-slate-200">
                              {capability.name || "未命名能力"}
                            </span>
                            <span className="shrink-0 text-[10px] uppercase text-slate-500">
                              {formatAgiUiToken(
                                capability.access || "read_only",
                              )}
                            </span>
                          </div>
                          <div className="mt-1 text-xs text-slate-500">
                            {[capability.category, capability.contract_ref]
                              .filter(Boolean)
                              .join(" · ")}
                          </div>
                          <div className="mt-1 flex flex-wrap gap-1">
                            {capability.risk_level && (
                              <span
                                className={cn(
                                  "rounded border px-1.5 py-0.5 text-[10px]",
                                  capability.risk_level === "high"
                                    ? "border-amber-500/20 bg-amber-500/10 text-amber-300"
                                    : "border-slate-700 bg-slate-900 text-slate-400",
                                )}
                              >
                                风险 {formatAgiUiToken(capability.risk_level)}
                              </span>
                            )}
                            {(capability.guardrails || [])
                              .slice(0, 1)
                              .map((guardrail) => (
                                <span
                                  key={guardrail}
                                  className="truncate rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400"
                                  title={guardrail}
                                >
                                  {guardrail}
                                </span>
                              ))}
                            {(capability.evidence_refs || [])
                              .slice(0, 1)
                              .map((evidenceRef) => (
                                <span
                                  key={evidenceRef}
                                  className="truncate rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
                                  title={evidenceRef}
                                >
                                  {evidenceRef}
                                </span>
                              ))}
                          </div>
                        </div>
                      ))}
                      {agiCapabilities.length === 0 && (
                        <div className="rounded-lg border border-dashed border-slate-700 p-4 text-sm text-slate-500">
                          暂无能力面投影
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </>
            )}

          </div>
        )}

        {activeTab === "goals" && (
          <div className="space-y-3">
            {/* New Goal Button */}
            {!showNewGoal ? (
              <Button
                variant="outline"
                className="w-full border-dashed border-slate-700 text-slate-400 hover:text-white"
                onClick={() => setShowNewGoal(true)}
              >
                <Plus className="mr-1 size-4" />
                新建目标
              </Button>
            ) : (
              <Card className="border-slate-800 bg-slate-900/50">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center justify-between text-sm">
                    <span>目标生成台</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setShowNewGoal(false)}
                    >
                      <X className="size-4" />
                    </Button>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <Input
                    aria-label="目标标题"
                    placeholder="目标标题"
                    value={newGoalTitle}
                    onChange={(e) => setNewGoalTitle(e.target.value)}
                    className="border-slate-700 bg-slate-950"
                  />
                  <Textarea
                    aria-label="目标描述"
                    placeholder="目标描述（可选）"
                    value={newGoalDesc}
                    onChange={(e) => setNewGoalDesc(e.target.value)}
                    className="border-slate-700 bg-slate-950"
                    rows={2}
                  />
                  <div className="flex gap-2">
                    <Button
                      onClick={handleCreateGoal}
                      disabled={
                        !newGoalTitle.trim() || resident.isActing("create-goal")
                      }
                      className="bg-slate-100 text-slate-950 hover:bg-white"
                    >
                      创建 AGI 目标
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => setShowNewGoal(false)}
                    >
                      取消
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Goals List */}
            <div className="space-y-2">
              {resident.goals.map((goal) => (
                <GoalItem
                  key={goal.goal_id}
                  goal={goal}
                  execution={
                    goal.goal_id
                      ? resident.getGoalExecution?.(goal.goal_id)
                      : undefined
                  }
                  expanded={expandedGoal === goal.goal_id}
                  onToggle={() =>
                    setExpandedGoal(
                      expandedGoal === goal.goal_id
                        ? null
                        : goal.goal_id || null,
                    )
                  }
                  onApprove={() =>
                    void resident.approveGoal(String(goal.goal_id))
                  }
                  onReject={() =>
                    void resident.rejectGoal(String(goal.goal_id))
                  }
                  onMaterialize={() =>
                    void resident.materializeGoal(String(goal.goal_id))
                  }
                  onStage={() =>
                    void resident.stageGoal(String(goal.goal_id), false)
                  }
                  onPromoteToPm={() =>
                    void resident.stageGoal(String(goal.goal_id), true)
                  }
                  onRun={() =>
                    void resident.runGoal(String(goal.goal_id), false, 1)
                  }
                  disabled={Boolean(resident.actionKey)}
                />
              ))}
              {resident.goals.length === 0 && !showNewGoal && (
                <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-500">
                  暂无目标，点击上方按钮创建
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "decisions" && (
          <div className="space-y-3">
            <Card
              className="border-slate-800 bg-slate-900/50"
              data-testid="resident-agi-decision-turn"
            >
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center justify-between gap-2 text-sm text-slate-300">
                  <span className="flex items-center gap-2">
                    <Brain className="size-4 text-slate-400" />
                    AGI 决策回合
                  </span>
                  <Badge className="border-slate-700 bg-slate-950 text-slate-300">
                    resident_agi
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
                  <label className="block min-w-0">
                    <span className="mb-1 block text-xs text-slate-500">
                      决策类型
                    </span>
                    <select
                      aria-label="AGI 决策类型"
                      data-testid="resident-agi-decision-type"
                      value={agiDecisionType}
                      onChange={(event) =>
                        setAgiDecisionType(event.target.value)
                      }
                      className="h-9 w-full rounded border border-slate-700 bg-slate-950 px-2 text-xs text-slate-200 outline-none focus:border-slate-500"
                    >
                      {agiDecisionTypeOptions.map((option) => (
                        <option
                          key={option.decisionId}
                          value={option.decisionId}
                        >
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div
                    className="flex flex-wrap gap-1"
                    data-testid="resident-agi-selected-decision-meta"
                  >
                    <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                      {selectedAgiDecisionCapability?.decision_id ||
                        agiDecisionType}
                    </span>
                    {selectedAgiDecisionCapability?.owner && (
                      <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                        {selectedAgiDecisionCapability.owner}
                      </span>
                    )}
                    {selectedAgiDecisionCapability?.risk_level && (
                      <span className="rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[10px] text-amber-200">
                        风险：
                        {formatAgiUiToken(
                          selectedAgiDecisionCapability.risk_level,
                        )}
                      </span>
                    )}
                  </div>
                </div>
                <Textarea
                  aria-label="AGI 决策目标"
                  value={agiDecisionObjective}
                  onChange={(event) =>
                    setAgiDecisionObjective(event.target.value)
                  }
                  className="min-h-20 border-slate-700 bg-slate-950"
                />
                <AgiDecisionProfilePanel
                  profile={agiDecisionProfile}
                  testId="resident-agi-decision-turn-profile"
                />
                <AgiSelectedDecisionEvidencePanel
                  decision={selectedAgiDecisionCapability}
                  evidencePayload={agiEvidenceInterfaces}
                  contract={agiEvidenceInterfaceContract}
                  refreshing={resident.isActing("agi-evidence-interfaces")}
                  onRefresh={() =>
                    void resident.refreshAgiEvidenceInterfaces(agiDecisionType)
                  }
                />
                <AgiDecisionHandoffPanel handoff={lastAgiDecisionHandoff} />
                <AgiHandoffInboxPanel inbox={agiHandoffs} />
                <div className="flex items-center justify-end">
                  <Button
                    size="sm"
                    data-testid="resident-run-agi-decision"
                    disabled={
                      !agiDecisionObjective.trim() ||
                      resident.isActing("agi-decide")
                    }
                    onClick={() => void handleRunAgiDecision()}
                    className="bg-slate-100 text-slate-950 hover:bg-white"
                  >
                    <Brain
                      className={cn(
                        "mr-1 size-3",
                        resident.isActing("agi-decide") && "animate-pulse",
                      )}
                    />
                    运行决策
                  </Button>
                </div>
              </CardContent>
            </Card>
            <DecisionAuditSummary stats={decisionStats} />
            <div className="space-y-2">
              {resident.decisions.map((decision) => (
                <DecisionItem
                  key={decision.decision_id || decision.timestamp}
                  decision={decision}
                  workspace={workspace}
                />
              ))}
            </div>
            {resident.decisions.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-500">
                暂无决策记录
              </div>
            )}
          </div>
        )}

        {activeTab === "evolution" && (
          <div className="space-y-4">
            {/* Skill Foundry */}
            <EvolutionSection
              icon={<Sparkles className="size-4 text-slate-400" />}
              title="技能工坊"
              count={resident.residentSkills.length}
              actionLabel="提炼技能"
              actionTestId="resident-extract-skills"
              onAction={() => void resident.extractSkills()}
              acting={resident.isActing("extract-skills")}
              emptyHint="尚无技能（运行一轮反思后生成）"
            >
              {resident.residentSkills.map((skill, idx) => (
                <div
                  key={skill.skill_id || idx}
                  className="rounded border border-slate-800 bg-slate-950/50 p-2"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-200">
                      {skill.name || "未命名技能"}
                    </span>
                    <span className="text-xs text-slate-500">
                      v{skill.version ?? 1} ·{" "}
                      {Math.round((skill.confidence ?? 0) * 100)}%
                    </span>
                  </div>
                  {skill.trigger && (
                    <div className="mt-1 text-xs text-slate-400">
                      触发: {skill.trigger}
                    </div>
                  )}
                </div>
              ))}
            </EvolutionSection>

            {/* Counterfactual Lab */}
            <EvolutionSection
              icon={<FlaskConical className="size-4 text-slate-400" />}
              title="反事实实验"
              count={resident.residentExperiments.length}
              actionLabel="运行实验"
              actionTestId="resident-run-experiments"
              onAction={() => void resident.runExperiments()}
              acting={resident.isActing("run-experiments")}
              emptyHint="尚无实验（需有失败决策作为输入）"
            >
              {resident.residentExperiments.map((exp, idx) => (
                <div
                  key={exp.experiment_id || idx}
                  className="rounded border border-slate-800 bg-slate-950/50 p-2"
                >
                  <div className="text-sm text-slate-200">
                    {(exp.baseline_strategy || "基线") +
                      " → " +
                      (exp.counterfactual_strategy || "反事实")}
                  </div>
                  {exp.recommendation && (
                    <div className="mt-1 text-xs text-slate-400">
                      建议: {exp.recommendation}
                    </div>
                  )}
                  {exp.status && (
                    <div className="mt-1 text-xs text-slate-500">
                      状态: {exp.status}
                    </div>
                  )}
                </div>
              ))}
            </EvolutionSection>

            {/* Self-Improvement Lab */}
            <EvolutionSection
              icon={<Wrench className="size-4 text-slate-400" />}
              title="自改提案"
              count={resident.residentImprovements.length}
              actionLabel="生成提案"
              actionTestId="resident-run-improvements"
              onAction={() => void resident.runImprovements()}
              acting={resident.isActing("run-improvements")}
              emptyHint="尚无提案（需有高分实验作为输入）"
            >
              {resident.residentImprovements.map((imp, idx) => (
                <div
                  key={imp.improvement_id || idx}
                  className="rounded border border-slate-800 bg-slate-950/50 p-2"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-200">
                      {imp.title || "未命名提案"}
                    </span>
                    {imp.status && (
                      <span className="text-xs text-slate-500">
                        {imp.status}
                      </span>
                    )}
                  </div>
                  {(imp.category || imp.target_surface) && (
                    <div className="mt-1 text-xs text-slate-400">
                      {[imp.category, imp.target_surface]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                  )}
                </div>
              ))}
            </EvolutionSection>
          </div>
        )}
      </div>
    </div>
  );
}

// Evolution section: skill / experiment / improvement list with a run action
function EvolutionSection({
  icon,
  title,
  count,
  actionLabel,
  actionTestId,
  onAction,
  acting,
  emptyHint,
  children,
}: {
  icon: ReactNode;
  title: string;
  count: number;
  actionLabel: string;
  actionTestId: string;
  onAction: () => void;
  acting: boolean;
  emptyHint: string;
  children: ReactNode;
}) {
  return (
    <Card className="border-slate-800 bg-slate-900/50">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="flex items-center gap-2 text-sm text-slate-300">
          {icon}
          {title} ({count})
        </CardTitle>
        <Button
          size="sm"
          variant="outline"
          data-testid={actionTestId}
          onClick={onAction}
          disabled={acting}
        >
          {actionLabel}
        </Button>
      </CardHeader>
      <CardContent className="space-y-2">
        {count === 0 ? (
          <div className="text-xs text-slate-500">{emptyHint}</div>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

function CapabilityMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">
        {label}
      </div>
      <div
        className="mt-1 truncate text-xs font-medium text-slate-200"
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

function buildCapabilityGovernanceStats(
  capabilities: ResidentAgiCapabilityPayload[],
): CapabilityGovernanceStats {
  const categories = new Set<string>();
  const contractRefs = new Set<string>();
  let readOnly = 0;
  let governedMutation = 0;
  let highRisk = 0;
  let chainRequired = false;

  for (const capability of capabilities) {
    const access = String(capability.access || "").toLowerCase();
    const risk = String(capability.risk_level || "").toLowerCase();
    const category = String(capability.category || "").trim();
    const contractRef = String(capability.contract_ref || "").trim();

    if (category) categories.add(category);
    if (contractRef) contractRefs.add(contractRef);
    if (access === "read_only") readOnly += 1;
    if (access.includes("write") || access.includes("execute"))
      governedMutation += 1;
    if (risk === "high") highRisk += 1;
    if (
      access.includes("pm_ce_director") ||
      contractRef.includes("goal_bridge")
    )
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

function CapabilityGovernanceMatrix({
  stats,
  authorityMatrix,
  accessRegistry,
  runtimeFoundation,
}: {
  stats: CapabilityGovernanceStats;
  authorityMatrix?: ResidentAgiAuthorityMatrixPayload;
  accessRegistry?: ResidentAgiCapabilityAccessRegistryPayload | null;
  runtimeFoundation: string;
}) {
  const chainLabel = authorityMatrix?.chain_required
    ? formatAgiRoleChain(
        authorityMatrix.chain || "PM → Chief Engineer → Director",
      )
    : stats.chainRequired
      ? formatAgiRoleChain("PM → Chief Engineer → Director")
      : "只读/观察优先";
  const counts = authorityMatrix?.counts || {};
  const accessCounts = accessRegistry?.counts || {};
  const accessGovernedOps =
    (accessCounts.governed_execution || 0) + (accessCounts.governed_write || 0);
  const readOnly =
    counts.read_only_capabilities ?? accessCounts.read_only ?? stats.readOnly;
  const governedOps =
    counts.governed_operation_capabilities ??
    (accessGovernedOps || stats.governedMutation);
  const highRisk =
    counts.high_risk_capabilities ?? accessCounts.high_risk ?? stats.highRisk;
  const contracts =
    counts.canonical_contracts ??
    accessCounts.canonical_contracts ??
    stats.contractRefs.length;
  const contractRefs =
    authorityMatrix?.canonical_contracts ||
    accessRegistry?.canonical_contracts ||
    stats.contractRefs;
  const policy = authorityMatrix?.decision_policy || {};
  const directToolAllowed = Boolean(
    accessRegistry?.execution_policy?.agi_direct_tool_execution_allowed,
  );
  const directWriteAllowed = Boolean(
    accessRegistry?.execution_policy?.agi_direct_writes_allowed,
  );
  const domains = accessRegistry?.interface_domains || [];
  return (
    <div
      className="mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2"
      data-testid="resident-agi-governance-matrix"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">能力治理矩阵</div>
          <div className="mt-0.5 text-[10px] text-slate-500">
            底座: {authorityMatrix?.runtime_foundation || runtimeFoundation}
          </div>
        </div>
        <Badge className="border-slate-700 bg-slate-950/50 text-slate-300">
          {chainLabel}
        </Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric label="只读" value={String(readOnly)} />
        <CapabilityMetric label="受控操作" value={String(governedOps)} />
        <CapabilityMetric label="高风险" value={String(highRisk)} />
        <CapabilityMetric label="契约" value={String(contracts)} />
      </div>
      <div className="mt-3 rounded border border-slate-800 bg-slate-950/35 px-2.5 py-2">
        <SegmentedMeter
          segments={[
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
          ]}
        />
      </div>
      {authorityMatrix && (
        <div
          className="mt-2 rounded border border-slate-800 bg-slate-950/35 px-2 py-1 font-mono text-[10px] text-slate-400"
          data-testid="resident-agi-authority-matrix"
        >
          {authorityMatrix.schema_version || "resident.agi_authority_matrix.v1"}{" "}
          · 硬规则 {counts.platform_hard_rules ?? 0} · AGI 判断{" "}
          {counts.agi_recommendations ?? 0} · 受控执行{" "}
          {counts.governed_execution_boundaries ?? 0}
        </div>
      )}
      {accessRegistry && (
        <div
          className="mt-2 rounded border border-slate-800 bg-slate-950/35 px-2 py-1 font-mono text-[10px] text-slate-400"
          data-testid="resident-agi-capability-access-registry"
        >
          {accessRegistry.schema_version ||
            "resident.agi_capability_access_registry.v1"}{" "}
          · 直接工具 {formatAgiAllowed(directToolAllowed)} · 直接写入{" "}
          {formatAgiAllowed(directWriteAllowed)} · 仅建议{" "}
          {accessCounts.advisory_only ?? 0}
        </div>
      )}
      <div
        className="mt-2 flex flex-wrap gap-1"
        data-testid="resident-agi-governance-tags"
      >
        {domains.slice(0, 6).map((domain) => (
          <span
            key={domain.domain_id}
            className="rounded border border-slate-800 bg-slate-950/45 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
          >
            {domain.domain_id}:r{domain.read_only ?? 0}/g
            {domain.governed_execution ?? 0}
          </span>
        ))}
        {stats.categories.slice(0, 8).map((category) => (
          <span
            key={category}
            className="rounded bg-slate-950/45 px-1.5 py-0.5 font-mono text-[10px] text-slate-500"
          >
            {category}
          </span>
        ))}
        {contractRefs.slice(0, 6).map((contractRef) => (
          <span
            key={contractRef}
            className="rounded border border-slate-800 bg-slate-950/35 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
          >
            {contractRef}
          </span>
        ))}
        {Object.values(policy)
          .slice(0, 3)
          .map((policyValue) => (
            <span
              key={policyValue}
              className="rounded border border-slate-800 bg-slate-950/35 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
            >
              {policyValue}
            </span>
          ))}
      </div>
    </div>
  );
}

function catalogSummaryEntries(
  values?: Record<string, number>,
  limit = 5,
): Array<[string, number]> {
  return Object.entries(values || {})
    .sort(
      (left, right) => right[1] - left[1] || left[0].localeCompare(right[0]),
    )
    .slice(0, limit);
}

function AgiRepairStrategyCatalogPanel({
  catalog,
}: {
  catalog?: ResidentAgiHardcodedRepairStrategyCatalogPayload | null;
}) {
  if (!catalog) return null;
  const summary = catalog.summary || {};
  const items = catalog.items || [];
  const total = summary.total ?? items.length;
  const executionBoundary =
    catalog.execution_boundary || "director_authorized_tools_only";
  const chain = formatAgiRoleChain(
    catalog.chain || "PM → Chief Engineer → Director",
  );
  const agiExecutionAuthority = Boolean(catalog.agi_execution_authority);

  return (
    <div
      className="mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2"
      data-testid="resident-agi-repair-strategy-catalog"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">
            Director 确定性修复策略目录
          </div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {catalog.schema_version ||
              "director.deterministic_repair_strategy_catalog.v1"}{" "}
            · {catalog.source || "director.runtime.repair_kernel"}
          </div>
        </div>
        <Badge className="border-slate-700 bg-slate-950/50 text-slate-300">
          {total} 条策略
        </Badge>
      </div>
      <div
        className="mt-2 flex flex-wrap gap-1"
        data-testid="resident-agi-repair-strategy-catalog-summary"
      >
        <span className="rounded border border-slate-700 bg-slate-950/50 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
          {executionBoundary}
        </span>
        <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-[10px] text-slate-300">
          {chain}
        </span>
        <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
          AGI 执行：{formatAgiAllowed(agiExecutionAuthority)}
        </span>
        <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
          {catalog.unknown_source_tool_policy || "fail_closed_high_risk"}
        </span>
        {catalog.director_tool_execution_required && (
          <span className="rounded border border-slate-700 bg-slate-950/50 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
            Director 工具必需
          </span>
        )}
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <div className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5">
          <div className="text-[10px] text-slate-500">语言</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {catalogSummaryEntries(summary.by_language).map(([key, count]) => (
              <span
                key={key}
                className="rounded bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
              >
                {key}:{count}
              </span>
            ))}
          </div>
        </div>
        <div className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5">
          <div className="text-[10px] text-slate-500">阶段</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {catalogSummaryEntries(summary.by_phase).map(([key, count]) => (
              <span
                key={key}
                className="rounded bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
              >
                {key}:{count}
              </span>
            ))}
          </div>
        </div>
        <div className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5">
          <div className="text-[10px] text-slate-500">风险</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {catalogSummaryEntries(summary.by_risk).map(([key, count]) => (
              <span
                key={key}
                className={cn(
                  "rounded border px-1.5 py-0.5 font-mono text-[10px]",
                  key === "high"
                    ? "border-rose-500/20 bg-rose-500/10 text-rose-200"
                    : "border-slate-700 bg-slate-950 text-slate-300",
                )}
              >
                {formatAgiUiToken(key)}:{count}
              </span>
            ))}
          </div>
        </div>
      </div>
      {items.length > 0 && (
        <div className="mt-2 grid gap-2 lg:grid-cols-2">
          {items.slice(0, 5).map((item) => (
            <div
              key={item.source_tool}
              className="rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2"
              data-testid="resident-agi-repair-strategy-catalog-item"
            >
              <div
                className="truncate font-mono text-[10px] text-slate-200"
                title={item.source_tool || ""}
              >
                {item.source_tool || "unknown_source_tool"}
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {[item.language, item.phase, item.concern, item.risk_level]
                  .filter(Boolean)
                  .map((token) => (
                    <span
                      key={token}
                      className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
                    >
                      {token}
                    </span>
                  ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AgiRepairAdvisoryPolicyPanel({
  policy,
}: {
  policy?: ResidentAgiRepairAdvisoryPolicyPayload | null;
}) {
  if (!policy) return null;
  const summary = policy.summary || {};
  const allowedFields = policy.allowed_suggested_rule_fields || [];
  const forbiddenFields = policy.forbidden_suggested_rule_fields || [];
  const suggestedRulesAllowed = Boolean(summary.suggested_rules_allowed);

  return (
    <div
      className="mt-2 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2"
      data-testid="resident-agi-repair-advisory-policy"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">
            AGI 修复建议边界
          </div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {policy.schema_version || "director.repair_advisory_policy.v1"} ·{" "}
            {policy.source || "director.runtime.repair_kernel.advisory_policy"}
          </div>
        </div>
        <Badge className="border-slate-700 bg-slate-950/50 text-slate-300">
          建议规则 {formatAgiAllowed(suggestedRulesAllowed)}
        </Badge>
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric
          label="AGI 执行"
          value={formatAgiAllowed(policy.agi_execution_authority)}
        />
        <CapabilityMetric
          label="写入"
          value={formatAgiAllowed(policy.writes_allowed)}
        />
        <CapabilityMetric
          label="注册"
          value={formatAgiAllowed(policy.registration_allowed)}
        />
        <CapabilityMetric
          label="权威回执"
          value={formatAgiAllowed(policy.authoritative_receipts_allowed)}
        />
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {allowedFields.slice(0, 8).map((field) => (
          <span
            key={field}
            className="rounded border border-slate-800 bg-slate-950/35 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
          >
            允许字段：{field}
          </span>
        ))}
        {forbiddenFields.slice(0, 8).map((field) => (
          <span
            key={field}
            className="rounded border border-slate-800 bg-slate-950/35 px-1.5 py-0.5 font-mono text-[10px] text-slate-500"
          >
            禁止字段：{field}
          </span>
        ))}
      </div>
      <div className="mt-2 font-mono text-[10px] text-slate-500">
        {policy.execution_boundary ||
          "read_only_advisory_no_writes_no_registration"}{" "}
        · Director 运行时保持权威
      </div>
    </div>
  );
}

function AgiRepairAdvisoryOverlayPanel({
  overlay,
  source,
}: {
  overlay?: ResidentAgiRepairAdvisoryOverlayPayload | null;
  source?: string;
}) {
  if (!overlay) return null;
  const advisorNotes = overlay.advisor_notes || [];
  const suggestedRuleCount = advisorNotes.reduce(
    (count, note) => count + (note.suggested_rules?.length || 0),
    0,
  );

  return (
    <div
      className="mt-2 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2"
      data-testid="resident-agi-repair-advisory-overlay"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">
            AGI 修复建议覆盖层
          </div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {overlay.schema_version ||
              "resident.agi_repair_advisory_overlay.v1"}{" "}
            ·{" "}
            {overlay.director_runtime_contract ||
              "director.repair_advisory_policy.v1"}
          </div>
          {source && (
            <div
              className="mt-0.5 font-mono text-[10px] text-slate-500"
              data-testid="resident-agi-repair-advisory-overlay-source"
            >
              来源：{source}
            </div>
          )}
        </div>
        <Badge
          className={cn(
            "border-slate-700 bg-slate-950/50 text-slate-300",
            overlay.status === "ready" &&
              "border-slate-600 bg-slate-900 text-slate-100",
            overlay.status?.startsWith("invalid") &&
              "border-slate-600 bg-slate-900 text-slate-100",
          )}
        >
          {formatAgiUiToken(overlay.status || "unknown")}
        </Badge>
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric
          label="注入"
          value={
            overlay.eligible_for_director_injection
              ? formatAgiUiToken("eligible")
              : formatAgiUiToken("blocked")
          }
        />
        <CapabilityMetric
          label="参与"
          value={
            overlay.participation_enabled
              ? formatAgiUiToken("enabled")
              : formatAgiUiToken("disabled")
          }
        />
        <CapabilityMetric label="建议" value={String(advisorNotes.length)} />
        <CapabilityMetric label="规则" value={String(suggestedRuleCount)} />
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
          仅建议：{formatAgiBoolean(overlay.advisory_only)}
        </span>
        <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
          权威：{formatAgiBoolean(overlay.authoritative)}
        </span>
        <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
          AGI 执行：{formatAgiAllowed(overlay.agi_execution_authority)}
        </span>
      </div>
      {(overlay.reason || overlay.error) && (
        <div className="mt-2 text-[11px] text-slate-500">
          {overlay.reason || overlay.error}
        </div>
      )}
    </div>
  );
}

function AgiDecisionCapabilityRegistry({
  schema,
  registry,
  decisions,
}: {
  schema?: string;
  registry?: ResidentAgiDecisionCapabilityRegistryPayload;
  decisions: ResidentAgiDecisionCapabilityPayload[];
}) {
  if (!registry && decisions.length === 0) return null;
  const counts = registry?.counts || {};
  const platformOwned = counts.platform_owned ?? 0;
  const agiOwned = counts.agi_owned ?? 0;
  const governedExecution = counts.governed_execution ?? 0;
  const evidenceInterfaces =
    counts.evidence_interfaces ?? registry?.evidence_interface_ids?.length ?? 0;
  const policy = registry?.decision_policy || {};

  return (
    <div
      className="mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2"
      data-testid="resident-agi-decision-capability-registry"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">
            AGI 决策能力注册表
          </div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {schema ||
              registry?.schema_version ||
              "resident.agi_decision_capability.v1"}
          </div>
        </div>
        <Badge className="border-slate-700 bg-slate-950/50 text-slate-300">
          {counts.decisions ?? decisions.length} 个决策
        </Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric label="平台" value={String(platformOwned)} />
        <CapabilityMetric label="AGI" value={String(agiOwned)} />
        <CapabilityMetric label="受控" value={String(governedExecution)} />
        <CapabilityMetric label="证据" value={String(evidenceInterfaces)} />
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {Object.values(policy).map((policyValue) => (
          <span
            key={policyValue}
            className="rounded border border-slate-800 bg-slate-950/50 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
          >
            {policyValue}
          </span>
        ))}
        {(registry?.candidate_actions || []).map((action) => (
          <span
            key={action}
            className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
          >
            动作：{action}
          </span>
        ))}
      </div>
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        {decisions.map((decision) => (
          <div
            key={decision.decision_id || decision.name}
            className="rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-slate-200">
                {decision.name || decision.decision_id}
              </span>
              <span
                className={cn(
                  "shrink-0 rounded border px-1.5 py-0.5 text-[10px]",
                  decision.platform_enforced
                    ? "border-rose-500/20 bg-rose-500/10 text-rose-200"
                    : "border-slate-700 bg-slate-950 text-slate-300",
                )}
              >
                {decision.platform_enforced
                  ? formatAgiUiToken("platform")
                  : decision.owner}
              </span>
            </div>
            <div className="mt-1 line-clamp-2 text-[10px] text-slate-400">
              {decision.decision_scope}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {(decision.required_evidence_interfaces || [])
                .slice(0, 4)
                .map((interfaceId) => (
                  <span
                    key={interfaceId}
                    className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
                  >
                    {interfaceId}
                  </span>
                ))}
              {decision.risk_level && (
                <span className="rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200">
                  风险 {formatAgiUiToken(decision.risk_level)}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function evidenceInterfaceStatusClass(status?: string): string {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "available") {
    return "border-slate-700 bg-slate-950 text-slate-300";
  }
  if (normalized === "metadata_only") {
    return "border-slate-800 bg-slate-950 text-slate-400";
  }
  if (
    normalized === "needs_public_facade" ||
    normalized === "governed_execute_only"
  ) {
    return "border-amber-500/20 bg-amber-500/10 text-amber-300";
  }
  return "border-rose-500/20 bg-rose-500/10 text-rose-300";
}

function AgiSelectedDecisionEvidencePanel({
  decision,
  evidencePayload,
  contract,
  refreshing = false,
  onRefresh,
}: {
  decision?: ResidentAgiDecisionCapabilityPayload | null;
  evidencePayload?: ResidentAgiEvidenceInterfacesPayload | null;
  contract?: ResidentAgiEvidenceInterfaceContractPayload;
  refreshing?: boolean;
  onRefresh?: () => void;
}) {
  if (!decision?.decision_id) return null;
  const payloadDecisionType = String(
    evidencePayload?.decision_type || "",
  ).trim();
  const runtimePayloadMatchesDecision =
    Boolean(payloadDecisionType) &&
    payloadDecisionType === decision.decision_id;
  const runtimeInterfaces = evidencePayload?.interfaces || [];
  const contractInterfaces = contract?.interfaces || [];
  const interfaceById = new Map<
    string,
    {
      interface_id?: string;
      status?: string;
      source?: string;
      contract_ref?: string;
    }
  >();
  contractInterfaces.forEach((item) => {
    if (item.interface_id) interfaceById.set(item.interface_id, item);
  });
  if (runtimePayloadMatchesDecision) {
    runtimeInterfaces.forEach((item) => {
      if (item.interface_id) interfaceById.set(item.interface_id, item);
    });
  }

  const required = decision.required_evidence_interfaces || [];
  const optional = decision.optional_evidence_interfaces || [];
  const rows = [
    ...required.map((interfaceId) => ({ interfaceId, required: true })),
    ...optional.map((interfaceId) => ({ interfaceId, required: false })),
  ];
  if (rows.length === 0) return null;
  const availableRequired = required.filter((interfaceId) => {
    const status = String(interfaceById.get(interfaceId)?.status || "");
    return status === "available";
  }).length;

  return (
    <div
      className="rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2"
      data-testid="resident-agi-selected-decision-evidence"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">
            当前决策证据预检
          </div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {decision.decision_id}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <Badge
            className={cn(
              "border-amber-500/20 bg-amber-500/10 text-amber-200",
              runtimePayloadMatchesDecision &&
                "border-slate-700 bg-slate-950 text-slate-300",
            )}
          >
            {runtimePayloadMatchesDecision ? "运行态已刷新" : "契约兜底"}
          </Badge>
          <Badge className="border-slate-700 bg-slate-950 text-slate-300">
            必需证据 {availableRequired}/{required.length}
          </Badge>
          {onRefresh && (
            <Button
              size="sm"
              variant="ghost"
              data-testid="resident-refresh-agi-evidence-interfaces"
              disabled={refreshing}
              onClick={onRefresh}
              className="h-6 px-2 text-[10px] text-slate-300"
            >
              <RefreshCw
                className={cn("mr-1 size-3", refreshing && "animate-spin")}
              />
              刷新
            </Button>
          )}
        </div>
      </div>
      {!runtimePayloadMatchesDecision && payloadDecisionType && (
        <div className="mt-2 rounded border border-amber-500/20 bg-amber-500/10 px-2 py-1 font-mono text-[10px] text-amber-200">
          运行态证据已过期：{payloadDecisionType}
        </div>
      )}
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        {rows.map(({ interfaceId, required }) => {
          const item = interfaceById.get(interfaceId) || {};
          const status = String(item.status || "unknown");
          const source = String(item.source || item.contract_ref || "");
          return (
            <div
              key={`${required ? "required" : "optional"}:${interfaceId}`}
              className="rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-[10px] text-slate-200">
                  {interfaceId}
                </span>
                <span
                  className={cn(
                    "shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px]",
                    evidenceInterfaceStatusClass(status),
                  )}
                >
                  {formatAgiUiToken(status)}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                  {required ? "必需" : "可选"}
                </span>
                {source && (
                  <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                    {source}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AgiEvidenceInterfaceReadiness({
  payload,
}: {
  payload?: ResidentAgiEvidenceInterfacesPayload | null;
}) {
  if (!payload) return null;
  const summary = payload.summary || {};
  const interfaces = payload.interfaces || [];
  const matrix = payload.capability_matrix || null;
  const matrixSummary = matrix?.summary || {};
  const matrixGroups = matrix?.groups || [];
  if (interfaces.length === 0) return null;

  return (
    <div
      className="mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2"
      data-testid="resident-agi-evidence-interface-readiness"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">
            AGI 证据接口可用性
          </div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {payload.schema_version || "resident.agi_evidence_interfaces.v1"} ·{" "}
            {payload.decision_type || "platform_supervision"}
          </div>
        </div>
        <Badge className="border-slate-700 bg-slate-950 text-slate-300">
          可用 {summary.available ?? 0}/{summary.total ?? interfaces.length}
        </Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric
          label="Metadata"
          value={String(summary.metadata_only ?? 0)}
        />
        <CapabilityMetric
          label="Facade gaps"
          value={String(summary.needs_public_facade ?? 0)}
        />
        <CapabilityMetric
          label="Governed"
          value={String(summary.governed_execute_only ?? 0)}
        />
        <CapabilityMetric
          label="Unavailable"
          value={String(summary.unavailable ?? 0)}
        />
      </div>
      {matrix && (
        <div
          className="mt-2 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2"
          data-testid="resident-agi-evidence-runtime-matrix"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-[10px] text-slate-400">
              {matrix.schema_version ||
                "resident.agi_evidence_capability_matrix.v1"}
            </span>
            <span className="text-[10px] text-slate-500">
              必需 {matrixSummary.required_available ?? 0}/
              {matrixSummary.required ?? 0} · 推荐{" "}
              {matrixSummary.recommended_now ?? 0}
            </span>
          </div>
          <div className="mt-2 grid gap-1.5 sm:grid-cols-3">
            {matrixGroups.slice(0, 6).map((group) => (
              <div
                key={group.group_id || group.name}
                className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[11px] text-slate-200">
                    {group.name || group.group_id || "group"}
                  </span>
                  <span className="font-mono text-[10px] text-slate-500">
                    {group.available ?? 0}/{group.total ?? 0}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {(group.required ?? 0) > 0 && (
                    <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                      必需：{group.required}
                    </span>
                  )}
                  {(group.missing_required ?? 0) > 0 && (
                    <span className="rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[10px] text-amber-200">
                      缺失：{group.missing_required}
                    </span>
                  )}
                  {(group.governed_execute ?? 0) > 0 && (
                    <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                      受控：{group.governed_execute}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
              仅建议：{formatAgiBoolean(matrixSummary.advisory_only ?? true)}
            </span>
            <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
              权威：{formatAgiBoolean(matrixSummary.authoritative ?? false)}
            </span>
            <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
              AGI 执行：
              {formatAgiAllowed(matrixSummary.agi_execution_authority)}
            </span>
          </div>
        </div>
      )}
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        {interfaces.map((item) => (
          <div
            key={item.interface_id || item.name}
            className="rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-slate-200">
                {item.name || item.interface_id || "未命名接口"}
              </span>
              <span
                className={cn(
                  "shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px]",
                  evidenceInterfaceStatusClass(item.status),
                )}
              >
                {formatAgiUiToken(item.status || "unknown")}
              </span>
            </div>
            <div className="mt-1 truncate font-mono text-[10px] text-slate-500">
              {item.interface_id || ""} · {item.source || "unknown_source"}
            </div>
            {item.recommended_next_action && (
              <div className="mt-1 truncate text-[10px] text-slate-400">
                {item.recommended_next_action}
              </div>
            )}
            {(item.gaps || []).length > 0 && (
              <div className="mt-1 truncate text-[10px] text-amber-200/80">
                {(item.gaps || [])[0]}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function isAgiEvidenceInterface(
  capability: ResidentAgiCapabilityPayload,
): boolean {
  const category = String(capability.category || "").trim();
  const contractRef = String(capability.contract_ref || "").trim();
  return (
    AGI_EVIDENCE_INTERFACE_CATEGORIES.has(category) ||
    contractRef.startsWith("audit.") ||
    contractRef.startsWith("context.") ||
    contractRef.startsWith("control_plane.verifier") ||
    contractRef === "control_plane.run_ledger" ||
    contractRef === "roles.final_request_context_audit"
  );
}

function AgiEvidenceInterfaceMatrix({
  capabilities,
  contract,
}: {
  capabilities: ResidentAgiCapabilityPayload[];
  contract?: ResidentAgiEvidenceInterfaceContractPayload;
}) {
  const interfaces = capabilities.filter(isAgiEvidenceInterface);
  if (interfaces.length === 0) return null;

  const readOnly = interfaces.filter(
    (capability) =>
      String(capability.access || "").toLowerCase() === "read_only",
  ).length;
  const governedRequests = interfaces.filter((capability) =>
    String(capability.access || "")
      .toLowerCase()
      .includes("execute"),
  ).length;
  const highRisk = interfaces.filter(
    (capability) =>
      String(capability.risk_level || "").toLowerCase() === "high",
  ).length;
  const contracts = uniqueStrings(
    interfaces.map((capability) => capability.contract_ref || ""),
  );
  const declaredCount = contract?.declared_interface_ids?.length;
  const requiredCount = contract?.required_interface_ids?.length;
  const optionalCount = contract?.optional_interface_ids?.length;
  const missingIds = contract?.missing_interface_ids || [];
  const coverageComplete = contract?.coverage_complete;

  return (
    <div
      className="mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2"
      data-testid="resident-agi-evidence-interface-matrix"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">
            AGI 证据接口矩阵
          </div>
          <div className="mt-0.5 text-[10px] text-slate-500">
            仅使用公开 Cell 契约
          </div>
        </div>
        <Badge
          className={cn(
            coverageComplete === false
              ? "border-amber-500/20 bg-amber-500/10 text-amber-300"
              : "border-slate-700 bg-slate-950 text-slate-300",
          )}
        >
          {coverageComplete === false ? "契约有缺口" : "契约已覆盖"}
        </Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric label="只读" value={String(readOnly)} />
        <CapabilityMetric label="受控请求" value={String(governedRequests)} />
        <CapabilityMetric label="高风险" value={String(highRisk)} />
        <CapabilityMetric
          label="已声明"
          value={String(declaredCount ?? interfaces.length)}
        />
      </div>
      {contract && (
        <div
          className="mt-2 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2"
          data-testid="resident-agi-evidence-interface-contract"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-[10px] text-slate-400">
              {contract.schema_version ||
                "resident.agi_evidence_interface_contract.v1"}
            </span>
            <span className="text-[10px] text-slate-500">
              必需 {requiredCount ?? 0} · 可选 {optionalCount ?? 0} · 缺失{" "}
              {missingIds.length}
            </span>
          </div>
          {missingIds.length > 0 && (
            <div className="mt-1 truncate font-mono text-[10px] text-amber-300">
              缺失：{missingIds.slice(0, 4).join(", ")}
            </div>
          )}
        </div>
      )}
      {contracts.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {contracts.slice(0, 6).map((contractRef) => (
            <span
              key={contractRef}
              className="rounded border border-slate-700 bg-slate-950/50 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
            >
              {contractRef}
            </span>
          ))}
        </div>
      )}
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        {interfaces.map((capability) => (
          <div
            key={capability.capability_id || capability.name}
            className="rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-slate-200">
                {capability.name || capability.capability_id || "未命名接口"}
              </span>
              <span
                className={cn(
                  "shrink-0 rounded border px-1.5 py-0.5 text-[10px]",
                  capability.risk_level === "high"
                    ? "border-amber-500/20 bg-amber-500/10 text-amber-300"
                    : "border-slate-700 bg-slate-950 text-slate-300",
                )}
              >
                {formatAgiUiToken(capability.access || "read_only")}
              </span>
            </div>
            <div className="mt-1 truncate font-mono text-[10px] text-slate-500">
              {capability.capability_id || ""} ·{" "}
              {capability.contract_ref || "unknown_contract"}
            </div>
            {(capability.evidence_refs || []).length > 0 && (
              <div className="mt-1 truncate font-mono text-[10px] text-slate-400">
                证据： {(capability.evidence_refs || []).slice(0, 2).join(", ")}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function boundaryAuthorityLabel(authority?: string): string {
  const normalized = String(authority || "").toLowerCase();
  if (normalized === "platform_hard_rule") return "平台硬规则";
  if (normalized === "agi_governed_execution") return "AGI 受控执行";
  if (normalized === "agi_recommendation") return "AGI 智能判断";
  return authority || "未分类";
}

function boundaryAuthorityClass(authority?: string): string {
  const normalized = String(authority || "").toLowerCase();
  if (normalized === "platform_hard_rule")
    return "border-rose-500/20 bg-rose-500/10 text-rose-300";
  if (normalized === "agi_governed_execution")
    return "border-amber-500/20 bg-amber-500/10 text-amber-300";
  if (normalized === "agi_recommendation")
    return "border-cyan-500/20 bg-cyan-500/10 text-cyan-200";
  return "border-slate-700 bg-slate-900 text-slate-300";
}

function countBoundariesByAuthority(
  boundaries: ResidentAgiDecisionBoundaryPayload[],
  authority: string,
): number {
  return boundaries.filter((boundary) => boundary.authority === authority)
    .length;
}

function DecisionBoundaryMatrix({
  schema,
  boundaries,
}: {
  schema?: string;
  boundaries: ResidentAgiDecisionBoundaryPayload[];
}) {
  if (boundaries.length === 0) return null;
  return (
    <div
      className="mt-3 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2"
      data-testid="resident-agi-decision-boundaries"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-100">AGI 决策边界</div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {schema || "resident.agi_decision_boundary.v1"}
          </div>
        </div>
        <div className="flex flex-wrap gap-1">
          <Badge className="border-rose-500/20 bg-rose-500/10 text-rose-300">
            硬规则{" "}
            {countBoundariesByAuthority(boundaries, "platform_hard_rule")}
          </Badge>
          <Badge className="border-cyan-500/20 bg-cyan-500/10 text-cyan-200">
            智能判断{" "}
            {countBoundariesByAuthority(boundaries, "agi_recommendation")}
          </Badge>
          <Badge className="border-amber-500/20 bg-amber-500/10 text-amber-300">
            受控执行{" "}
            {countBoundariesByAuthority(boundaries, "agi_governed_execution")}
          </Badge>
        </div>
      </div>
      <div className="mt-3 grid gap-2 lg:grid-cols-2">
        {boundaries.map((boundary) => (
          <div
            key={boundary.boundary_id || boundary.name}
            className="rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-slate-200">
                {boundary.name || "未命名边界"}
              </span>
              <span
                className={cn(
                  "shrink-0 rounded border px-1.5 py-0.5 text-[10px]",
                  boundaryAuthorityClass(boundary.authority),
                )}
              >
                {boundaryAuthorityLabel(boundary.authority)}
              </span>
            </div>
            <div
              className="mt-1 line-clamp-2 text-[11px] text-slate-500"
              title={boundary.platform_hard_rule || ""}
            >
              硬约束: {boundary.platform_hard_rule || "未声明"}
            </div>
            <div
              className="mt-1 line-clamp-2 text-[11px] text-slate-400"
              title={boundary.agi_decision_scope || ""}
            >
              AGI: {boundary.agi_decision_scope || "未声明"}
            </div>
            {(boundary.evidence_required || []).length > 0 && (
              <div className="mt-1 truncate font-mono text-[10px] text-slate-500">
                证据：{" "}
                {(boundary.evidence_required || []).slice(0, 3).join(", ")}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function DecisionBoundaryPolicyPanel({
  policy,
}: {
  policy?: ResidentAgiDecisionBoundaryPolicyPayload | null;
}) {
  if (!policy) return null;
  const counts = policy.counts || {};
  const executionPolicy = policy.capability_execution_policy || {};
  const modes = Object.entries(policy.decision_modes || {});
  const boundaryPolicies = policy.boundary_policies || [];
  const nonOverridable = policy.non_overridable_rules || [];
  const agiJudgement = policy.agi_judgement_boundaries || [];
  const governedExecution = policy.governed_execution_boundaries || [];

  return (
    <div
      className="mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2"
      data-testid="resident-agi-decision-boundary-policy"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">
            AGI 决策边界策略
          </div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {policy.schema_version ||
              "resident.agi_decision_boundary_policy.v1"}{" "}
            · {policy.source || "resident.autonomy.capability_surface"}
          </div>
        </div>
        <Badge className="border-slate-700 bg-slate-950 text-slate-300">
          {policy.chain || "PM → Chief Engineer → Director"}
        </Badge>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric
          label="Hard rules"
          value={String(counts.platform_hard_rules ?? nonOverridable.length)}
        />
        <CapabilityMetric
          label="AGI judgement"
          value={String(counts.agi_judgement ?? agiJudgement.length)}
        />
        <CapabilityMetric
          label="Governed"
          value={String(counts.governed_execution ?? governedExecution.length)}
        />
        <CapabilityMetric
          label="High risk"
          value={String(counts.high_risk_capabilities ?? 0)}
        />
      </div>

      <div className="mt-2 grid gap-2 lg:grid-cols-3">
        {modes.map(([modeId, mode]) => (
          <div
            key={modeId}
            className="rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-mono text-[10px] text-slate-200">
                {modeId}
              </span>
              <span
                className={cn(
                  "shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px]",
                  mode.llm_decision_allowed
                    ? "border-slate-700 bg-slate-950 text-slate-300"
                    : "border-rose-500/20 bg-rose-500/10 text-rose-200",
                )}
              >
                LLM：{formatAgiAllowed(mode.llm_decision_allowed)}
              </span>
            </div>
            <div className="mt-1 truncate text-[10px] text-slate-500">
              责任方：{mode.owner || formatAgiUiToken("unknown")}
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                执行：{formatAgiUiToken(mode.execution_authority || "none")}
              </span>
              <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                覆盖：{formatAgiBoolean(mode.override_allowed ?? false)}
              </span>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap gap-1">
        <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
          AGI 直接写入：
          {formatAgiAllowed(executionPolicy.agi_direct_writes_allowed)}
        </span>
        <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
          AGI 直接工具：
          {formatAgiAllowed(executionPolicy.agi_direct_tool_execution_allowed)}
        </span>
        <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
          Director 权威：
          {executionPolicy.director_runtime_remains_authoritative
            ? "保留"
            : formatAgiUiToken("unknown")}
        </span>
      </div>

      {boundaryPolicies.length > 0 && (
        <div className="mt-2 grid gap-2 lg:grid-cols-2">
          {boundaryPolicies.slice(0, 4).map((item) => (
            <div
              key={item.boundary_id || item.name}
              className="rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[11px] font-medium text-slate-200">
                  {item.name || item.boundary_id || "boundary"}
                </span>
                <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                  {item.execution_authority || "none"}
                </span>
              </div>
              <div className="mt-1 truncate font-mono text-[10px] text-slate-500">
                责任方：{item.decision_owner || formatAgiUiToken("unknown")} ·
                默认动作：
                {formatAgiUiToken(item.default_action || "request_evidence")}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AgiAuditPackPanel({
  pack,
}: {
  pack?: ResidentAgiAuditPackPayload | null;
}) {
  if (!pack) {
    return (
      <div
        className="mt-3 rounded-lg border border-dashed border-slate-700 bg-slate-950/40 px-3 py-2 text-xs text-slate-500"
        data-testid="resident-agi-audit-pack"
      >
        AGI 审计包尚未加载
      </div>
    );
  }

  const roleRegistry = pack.role_registry;
  const missingRoles = roleRegistry?.missing_required_roles || [];
  const evidenceRefs = pack.evidence_refs || [];
  const recentDecisions = pack.recent_decisions || [];
  const constraints = pack.execution_constraints || [];
  const boundaryIds = pack.boundary_summary?.boundary_ids || [];
  const hardRuleGate = pack.hard_rule_gate;
  const hardRuleStatus = String(
    hardRuleGate?.status || "unknown",
  ).toLowerCase();
  const hardRuleFailedChecks = hardRuleGate?.failed_check_ids || [];
  const evidenceGate = pack.evidence_gate;
  const evidenceGateStatus = String(
    evidenceGate?.status || "unknown",
  ).toLowerCase();
  const decisionProfile = pack.decision_profile;
  const runLedgerSummary = pack.run_ledger_summary;
  const authorityMatrix = pack.authority_matrix;
  const authorityCounts = authorityMatrix?.counts || {};
  const directorRepairContract = pack.director_repair_contract;
  const directorRepairAdvisory = directorRepairContract?.agi_advisory || {};
  const capabilityIds = (pack.capability_surface?.items || [])
    .map((capability) => capability.capability_id || "")
    .filter(Boolean);

  return (
    <div
      className="mt-3 rounded-md border border-slate-800 bg-slate-950/35 px-3 py-2"
      data-testid="resident-agi-audit-pack"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">AGI 审计包</div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {pack.schema_version || "resident.agi_audit_pack.v1"}
          </div>
        </div>
        <Badge
          className={cn(
            hardRuleStatus === "pass"
              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300"
              : "border-rose-500/20 bg-rose-500/10 text-rose-300",
          )}
        >
          硬规则门禁 {formatAgiUiToken(hardRuleStatus)}
        </Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric
          label="对话角色"
          value={String(roleRegistry?.dialogue_roles?.length ?? 0)}
        />
        <CapabilityMetric
          label="适配器角色"
          value={String(roleRegistry?.adapter_roles?.length ?? 0)}
        />
        <CapabilityMetric
          label="证据门禁"
          value={formatAgiUiToken(evidenceGateStatus)}
        />
        <CapabilityMetric
          label="硬检查"
          value={String(hardRuleGate?.checks?.length ?? 0)}
        />
      </div>
      {authorityMatrix && (
        <div
          className="mt-2 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2"
          data-testid="resident-agi-audit-authority-matrix"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-[10px] uppercase text-slate-500">权威矩阵</div>
            <Badge className="border-slate-700 bg-slate-950 text-slate-300">
              {authorityMatrix.chain_required
                ? formatAgiRoleChain(
                    authorityMatrix.chain || "PM → Chief Engineer → Director",
                  )
                : formatAgiUiToken("read_only")}
            </Badge>
          </div>
          <div className="mt-1 font-mono text-[10px] text-slate-400">
            {authorityMatrix.schema_version ||
              "resident.agi_authority_matrix.v1"}{" "}
            · 硬规则 {authorityCounts.platform_hard_rules ?? 0} · AGI 判断{" "}
            {authorityCounts.agi_recommendations ?? 0} · 受控操作{" "}
            {authorityCounts.governed_operation_capabilities ?? 0}
          </div>
        </div>
      )}
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        <div className="rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2">
          <div className="text-[10px] uppercase text-slate-500">执行约束</div>
          <div className="mt-1 space-y-1">
            {constraints.slice(0, 4).map((constraint) => (
              <div key={constraint} className="text-[11px] text-slate-300">
                {constraint}
              </div>
            ))}
          </div>
        </div>
        <div className="rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2">
          <div className="text-[10px] uppercase text-slate-500">审计来源</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {(pack.truth_sources || []).slice(0, 6).map((source) => (
              <span
                key={source}
                className="rounded bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
              >
                {source}
              </span>
            ))}
            {boundaryIds.slice(0, 4).map((boundaryId) => (
              <span
                key={boundaryId}
                className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
              >
                {boundaryId}
              </span>
            ))}
            {capabilityIds.slice(0, 4).map((capabilityId) => (
              <span
                key={capabilityId}
                className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
              >
                {capabilityId}
              </span>
            ))}
          </div>
        </div>
      </div>
      {directorRepairContract && (
        <div
          className="mt-2 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2"
          data-testid="resident-agi-director-repair-contract"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-[10px] uppercase text-slate-500">
                Director 修复契约
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-slate-500">
                {directorRepairContract.schema_version ||
                  "resident.agi_director_repair_contract.v1"}
              </div>
            </div>
            <Badge className="border-slate-700 bg-slate-950 text-slate-300">
              {directorRepairContract.owner_cell || "director.runtime"}
            </Badge>
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            <span className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              {directorRepairContract.execution_boundary ||
                "director_authorized_tools_only"}
            </span>
            <span className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 text-[10px] text-slate-300">
              {formatAgiRoleChain(
                directorRepairContract.chain ||
                  "PM → Chief Engineer → Director",
              )}
            </span>
            <span className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              {directorRepairContract.unknown_source_tool_policy ||
                "fail_closed_high_risk"}
            </span>
            <span className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              AGI 执行：
              {formatAgiAllowed(directorRepairContract.agi_execution_authority)}
            </span>
            <span className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              写入：{formatAgiAllowed(directorRepairAdvisory.writes_allowed)}
            </span>
            <span className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              建议：{formatAgiActive(directorRepairAdvisory.active)}
            </span>
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            <CapabilityMetric
              label="策略数"
              value={String(directorRepairContract.strategy_count ?? 0)}
            />
            <CapabilityMetric
              label="目录"
              value={
                directorRepairContract.catalog_schema ||
                "director.deterministic_repair_strategy_catalog.v1"
              }
            />
            <CapabilityMetric
              label="画像"
              value={
                directorRepairContract.profile_summary_schema ||
                "director.deterministic_repair_profile_summary.v1"
              }
            />
          </div>
        </div>
      )}
      <div className="mt-2 rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[10px] uppercase text-slate-500">证据门禁</div>
          <Badge
            className={cn(
              evidenceGateStatus === "pass"
                ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300"
                : evidenceGateStatus === "fail"
                  ? "border-rose-500/20 bg-rose-500/10 text-rose-300"
                  : "border-amber-500/20 bg-amber-500/10 text-amber-300",
            )}
          >
            {formatAgiUiToken(evidenceGateStatus)} →{" "}
            {formatAgiUiToken(
              evidenceGate?.recommended_verdict || "request_evidence",
            )}
          </Badge>
        </div>
        <div className="mt-1 text-[11px] text-slate-400">
          {evidenceGate?.reason || "暂无证据门说明"}
        </div>
        <div className="mt-1 font-mono text-[10px] text-slate-500">
          运行账本 {formatAgiUiToken(runLedgerSummary?.status || "unknown")} ·
          已投影 {runLedgerSummary?.projected ?? 0}/
          {runLedgerSummary?.total ?? 0} · 失败 {runLedgerSummary?.failed ?? 0}{" "}
          · 上下文引用{" "}
          {evidenceGate?.context_snapshot_ref_count ?? evidenceRefs.length}
        </div>
      </div>
      <AgiDecisionProfilePanel
        profile={decisionProfile}
        testId="resident-agi-audit-decision-profile"
      />
      {missingRoles.length > 0 && (
        <div className="mt-2 rounded border border-rose-500/20 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">
          缺失角色：{missingRoles.join(", ")}
        </div>
      )}
      {hardRuleFailedChecks.length > 0 && (
        <div className="mt-2 rounded border border-rose-500/20 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">
          失败硬规则：{hardRuleFailedChecks.join(", ")}
        </div>
      )}
      <div className="mt-2 text-[10px] text-slate-500">
        最近决策：{recentDecisions.length} · LLM 覆盖：
        {formatAgiAllowed(hardRuleGate?.llm_override_allowed)}
      </div>
      {pack.decision_endpoint && (
        <div className="mt-2 font-mono text-[10px] text-slate-500">
          决策入口：{pack.decision_endpoint}
        </div>
      )}
    </div>
  );
}

function AgiDecisionProfilePanel({
  profile,
  testId,
}: {
  profile?: ResidentAgiDecisionProfilePayload | null;
  testId: string;
}) {
  if (!profile) return null;
  const roleTurnAllowed = profile.role_turn_allowed !== false;
  const downstreamPrecheck = profile.downstream_precheck || "unknown";
  const recommendedVerdict = profile.recommended_verdict || "request_evidence";
  const nextAction =
    profile.recommended_next_action || "request_missing_evidence";
  const candidateActions = profile.candidate_actions || [];
  const requiredConstraints = profile.required_constraints || [];
  const requiredEvidence = profile.required_evidence || [];
  const evidenceInterfaceRecommendations =
    profile.evidence_interface_recommendations || [];
  const contractRefs = profile.contract_refs || [];

  return (
    <div
      className="mt-2 rounded border border-slate-800 bg-slate-950/35 px-2.5 py-2"
      data-testid={testId}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">AGI 执行画像</div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {profile.schema_version || "resident.agi_decision_profile.v1"}
          </div>
        </div>
        <Badge
          className={cn(
            roleTurnAllowed
              ? "border-slate-700 bg-slate-950 text-slate-300"
              : "border-rose-500/20 bg-rose-500/10 text-rose-300",
          )}
        >
          角色回合 {formatAgiAllowed(roleTurnAllowed)}
        </Badge>
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric
          label="底座"
          value={
            profile.runtime_foundation || "RoleRuntime / ContextOS / TurnEngine"
          }
        />
        <CapabilityMetric
          label="预检"
          value={formatAgiUiToken(downstreamPrecheck)}
        />
        <CapabilityMetric
          label="裁决"
          value={formatAgiUiToken(recommendedVerdict)}
        />
        <CapabilityMetric label="下一步" value={formatAgiUiToken(nextAction)} />
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {candidateActions.map((action) => (
          <span
            key={action}
            className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
          >
            动作：{formatAgiUiToken(action)}
          </span>
        ))}
        {requiredConstraints.map((constraint) => (
          <span
            key={constraint}
            className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
          >
            {constraint}
          </span>
        ))}
        {requiredEvidence.slice(0, 4).map((evidence) => (
          <span
            key={evidence}
            className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
          >
            证据：{evidence}
          </span>
        ))}
        {contractRefs.slice(0, 4).map((contractRef) => (
          <span
            key={contractRef}
            className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
          >
            {contractRef}
          </span>
        ))}
      </div>
      {evidenceInterfaceRecommendations.length > 0 && (
        <div className="mt-2 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-[10px] uppercase text-slate-500">证据接口</div>
            <Badge className="border-slate-700 bg-slate-950 text-slate-300">
              {
                evidenceInterfaceRecommendations.filter(
                  (recommendation) => recommendation.recommended_now,
                ).length
              }{" "}
              个推荐
            </Badge>
          </div>
          <div className="mt-2 grid gap-1.5 lg:grid-cols-2">
            {evidenceInterfaceRecommendations
              .slice(0, 6)
              .map((recommendation) => (
                <div
                  key={
                    recommendation.capability_id ||
                    recommendation.contract_ref ||
                    recommendation.name
                  }
                  className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[11px] font-medium text-slate-200">
                      {recommendation.name ||
                        recommendation.capability_id ||
                        "证据接口"}
                    </span>
                    <span
                      className={cn(
                        "shrink-0 rounded border px-1.5 py-0.5 text-[10px]",
                        recommendation.recommended_now
                          ? "border-slate-700 bg-slate-950 text-slate-300"
                          : "border-slate-700 bg-slate-950 text-slate-400",
                      )}
                    >
                      {recommendation.recommended_now ? "现在" : "稍后"}
                    </span>
                  </div>
                  <div className="mt-1 truncate font-mono text-[10px] text-slate-500">
                    {recommendation.capability_id || ""} ·{" "}
                    {recommendation.contract_ref || ""}
                  </div>
                  {recommendation.reason && (
                    <div className="mt-1 line-clamp-2 text-[10px] text-slate-400">
                      {recommendation.reason}
                    </div>
                  )}
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AgiDecisionHandoffPanel({
  handoff,
}: {
  handoff?: ResidentAgiDecisionHandoffPayload | null;
}) {
  if (!handoff) return null;
  const status = String(handoff.handoff_status || "hold").toLowerCase();
  const targetRoles = handoff.target_roles || [];
  const blockedActions = handoff.blocked_actions || [];
  return (
    <div
      className="mt-2 rounded border border-slate-800 bg-slate-950/35 px-2.5 py-2"
      data-testid="resident-agi-decision-handoff"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-200">AGI 决策交接</div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {handoff.schema_version || "resident.agi_decision_handoff.v1"}
          </div>
        </div>
        <Badge
          className={cn(
            status === "ready"
              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300"
              : status === "blocked"
                ? "border-rose-500/20 bg-rose-500/10 text-rose-300"
                : "border-amber-500/20 bg-amber-500/10 text-amber-300",
          )}
        >
          {formatAgiUiToken(status)}
        </Badge>
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <CapabilityMetric
          label="目标角色"
          value={targetRoles.join(" → ") || formatAgiUiToken("hold")}
        />
        <CapabilityMetric
          label="下游"
          value={formatAgiAllowed(handoff.downstream_allowed)}
        />
        <CapabilityMetric
          label="AGI 执行"
          value={formatAgiAllowed(handoff.agi_execution_authority)}
        />
      </div>
      <div className="mt-2 line-clamp-2 text-[11px] text-slate-400">
        {handoff.reason || "等待 AGI 决策交接说明"}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {(handoff.allowed_actions || []).slice(0, 5).map((action) => (
          <span
            key={action}
            className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
          >
            {action}
          </span>
        ))}
        {blockedActions.slice(0, 5).map((action) => (
          <span
            key={action}
            className="rounded border border-rose-700/30 bg-rose-950/20 px-1.5 py-0.5 font-mono text-[10px] text-rose-200/80"
          >
            已阻断：{action}
          </span>
        ))}
      </div>
      <div className="mt-2 font-mono text-[10px] text-slate-500">
        链路：
        {formatAgiRoleChain(
          handoff.required_chain || "PM → Chief Engineer → Director",
        )}{" "}
        · 仅建议：{formatAgiBoolean(handoff.advisory_only !== false)}
      </div>
    </div>
  );
}

function AgiHandoffInboxPanel({
  inbox,
}: {
  inbox?: ResidentAgiHandoffInboxPayload | null;
}) {
  if (!inbox || (inbox.items || []).length === 0) return null;
  const items = inbox.items || [];
  const summary = inbox.summary || {};
  const byStatus = summary.by_status || {};
  return (
    <div
      className="mt-2 rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2"
      data-testid="resident-agi-handoff-inbox"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-100">AGI 交接队列</div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {inbox.schema_version || "resident.agi_handoff_inbox.v1"}
          </div>
        </div>
        <Badge className="border-slate-700 bg-slate-900 text-slate-300">
          {items.length} 个交接
        </Badge>
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric label="就绪" value={String(byStatus.ready ?? 0)} />
        <CapabilityMetric label="暂缓" value={String(byStatus.hold ?? 0)} />
        <CapabilityMetric label="阻断" value={String(byStatus.blocked ?? 0)} />
        <CapabilityMetric
          label="AGI 执行"
          value={formatAgiAllowed(summary.agi_execution_authority)}
        />
      </div>
      <div className="mt-2 space-y-1.5">
        {items.slice(0, 4).map((item) => {
          const handoff = item.handoff || {};
          const targetRoles = handoff.target_roles || [];
          return (
            <div
              key={item.decision_id || item.timestamp}
              className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="truncate text-[11px] font-medium text-slate-200">
                  {item.summary || handoff.reason || item.decision_id}
                </span>
                <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                  {formatAgiUiToken(handoff.handoff_status || "hold")}
                </span>
              </div>
              <div className="mt-1 truncate font-mono text-[10px] text-slate-500">
                {targetRoles.join(" → ") || "resident_agi"} ·{" "}
                {formatAgiRoleChain(
                  handoff.required_chain || "PM → Chief Engineer → Director",
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Simplified Goal Item
function GoalItem({
  goal,
  execution,
  expanded,
  onToggle,
  onApprove,
  onReject,
  onMaterialize,
  onStage,
  onPromoteToPm,
  onRun,
  disabled,
}: {
  goal: ResidentGoalPayload;
  execution?: import("@/app/types/appContracts").GoalExecutionView;
  expanded: boolean;
  onToggle: () => void;
  onApprove: () => void;
  onReject: () => void;
  onMaterialize: () => void;
  onStage: () => void;
  onPromoteToPm: () => void;
  onRun: () => void;
  disabled: boolean;
}) {
  const status = goal.status || "pending";
  const isPending = status === "pending";
  const isApproved = status === "approved" || status === "materialized";

  return (
    <Card
      className={cn(
        "border-slate-800 bg-slate-900/50",
        expanded && "border-slate-700",
      )}
    >
      <div
        className="flex cursor-pointer items-center justify-between p-3"
        onClick={onToggle}
      >
        <div className="flex items-center gap-3">
          {expanded ? (
            <ChevronDown className="size-4 text-slate-400" />
          ) : (
            <ChevronRight className="size-4 text-slate-400" />
          )}
          <div className="flex-1">
            <div className="font-medium text-slate-200">
              {goal.title || "未命名目标"}
            </div>
            {/* Phase 1.2: Execution Progress */}
            {execution ? (
              <div className="mt-1">
                <ExecutionProgressBar execution={execution} compact />
              </div>
            ) : (
              <div className="text-xs text-slate-500">
                {formatTime(goal.updated_at)}
              </div>
            )}
          </div>
        </div>
        <GoalStatusBadge status={status} />
      </div>

      {expanded && (
        <div className="border-t border-slate-800 px-3 pb-3">
          <div className="pt-3 text-sm text-slate-400">
            {goal.motivation || "暂无描述"}
          </div>
          {/* Phase 1.2: Full Execution Progress */}
          {execution && (
            <div className="mt-3 rounded bg-slate-950 p-3">
              <ExecutionProgressBar execution={execution} />
            </div>
          )}
          <div className="mt-3 flex gap-2">
            {isPending && (
              <>
                <Button
                  size="sm"
                  onClick={onApprove}
                  disabled={disabled}
                  className="bg-slate-100 text-slate-950 hover:bg-white"
                >
                  <CheckCircle2 className="mr-1 size-3" />
                  批准
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  data-testid="resident-reject-goal"
                  onClick={onReject}
                  disabled={disabled}
                  className="border-rose-500/30 text-rose-300 hover:bg-rose-500/10"
                >
                  <Ban className="mr-1 size-3" />
                  拒绝
                </Button>
              </>
            )}
            {isApproved && (
              <>
                {status === "approved" && (
                  <Button
                    size="sm"
                    variant="outline"
                    data-testid="resident-materialize-goal"
                    onClick={onMaterialize}
                    disabled={disabled}
                  >
                    <Package className="mr-1 size-3" />
                    固化
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onStage}
                  disabled={disabled}
                >
                  暂存
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onPromoteToPm}
                  disabled={disabled}
                >
                  写入 PM
                </Button>
                <Button
                  size="sm"
                  onClick={onRun}
                  disabled={disabled}
                  className="bg-slate-100 text-slate-950 hover:bg-white"
                >
                  <Play className="mr-1 size-3" />
                  交给 PM
                </Button>
              </>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function decisionString(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  return "";
}

function decisionNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function decisionStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(decisionString).filter(Boolean);
}

function decisionObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function toRepairAdvisoryOverlay(
  value: unknown,
): ResidentAgiRepairAdvisoryOverlayPayload | null {
  const overlay = decisionObject(value);
  if (!Object.keys(overlay).length) return null;
  const schema = decisionString(overlay.schema_version);
  const status = decisionString(overlay.status);
  const notes = overlay.advisor_notes;
  if (
    schema !== "resident.agi_repair_advisory_overlay.v1" &&
    !status &&
    !Array.isArray(notes)
  ) {
    return null;
  }
  return overlay as ResidentAgiRepairAdvisoryOverlayPayload;
}

function latestDecisionRepairAdvisoryOverlay(
  decisions: ResidentDecisionPayload[],
): { overlay: ResidentAgiRepairAdvisoryOverlayPayload; source: string } | null {
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
    const overlay =
      toRepairAdvisoryOverlay(actual.resident_agi_repair_advisory_overlay) ||
      toRepairAdvisoryOverlay(actual.repair_advisory_overlay);
    if (!overlay) continue;
    const sourceId = shortDecisionId(item.decision.decision_id);
    return {
      overlay,
      source: sourceId ? `decision_trace:${sourceId}` : "decision_trace",
    };
  }
  return null;
}

function shortDecisionId(value?: string): string {
  const token = String(value || "").trim();
  if (!token) return "";
  if (token.length <= 14) return token;
  return `${token.slice(0, 10)}...${token.slice(-4)}`;
}

function formatConfidence(value?: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "暂无";
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function decisionHasEvidence(decision: ResidentDecisionPayload): boolean {
  return Boolean(
    decision.evidence_bundle_id ||
    (decision.evidence_refs || []).length > 0 ||
    (decision.context_refs || []).length > 0 ||
    (decision.affected_files || []).length > 0 ||
    (decision.affected_symbols || []).length > 0,
  );
}

function decisionHasHandoffImpact(decision: ResidentDecisionPayload): boolean {
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

function decisionRuntimeContractGate(
  decision: ResidentDecisionPayload,
): Record<string, unknown> {
  return decisionObject(
    decision.actual_outcome?.resident_agi_runtime_contract_gate,
  );
}

function decisionHasRuntimeContractReceipt(
  decision: ResidentDecisionPayload,
): boolean {
  const gate = decisionRuntimeContractGate(decision);
  return gate.passed === true || decisionString(gate.status) === "pass";
}

function decisionHasRuntimeContractFailure(
  decision: ResidentDecisionPayload,
): boolean {
  const gate = decisionRuntimeContractGate(decision);
  const status = decisionString(gate.status);
  return (
    status === "fail" ||
    decisionStringList(gate.failed_check_ids).length > 0 ||
    (gate.passed === false && Boolean(gate.required))
  );
}

function buildDecisionStats(decisions: ResidentDecisionPayload[]) {
  const total = decisions.length;
  const evidenceBacked = decisions.filter(decisionHasEvidence).length;
  const handoffImpact = decisions.filter(decisionHasHandoffImpact).length;
  const runtimeReceipts = decisions.filter(
    decisionHasRuntimeContractReceipt,
  ).length;
  const runtimeContractFailures = decisions.filter(
    decisionHasRuntimeContractFailure,
  ).length;
  const blockedOrFailed = decisions.filter((decision) => {
    const verdict = String(decision.verdict || "").toLowerCase();
    const actual = decision.actual_outcome || {};
    const blockers = decisionStringList(actual.hard_rule_blockers);
    return (
      verdict === "failure" || verdict === "blocked" || blockers.length > 0
    );
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

function DecisionAuditSummary({
  stats,
}: {
  stats: ReturnType<typeof buildDecisionStats>;
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
            <FileSearch className="size-4 text-slate-400" />
            决策审计面
          </div>
          <div className="mt-1 text-xs text-slate-500">
            唯一事实源：decision_trace.jsonl
          </div>
        </div>
        <Badge className="border-slate-700 bg-slate-950/50 text-slate-300">
          resident.decision_event.v1
        </Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-5">
        <DecisionMetric
          label="决策"
          value={String(stats.total)}
          tone="neutral"
        />
        <DecisionMetric
          label="证据"
          value={String(stats.evidenceBacked)}
          tone="cyan"
        />
        <DecisionMetric
          label="交接"
          value={String(stats.handoffImpact)}
          tone="emerald"
        />
        <DecisionMetric
          label="运行时"
          value={String(stats.runtimeReceipts)}
          tone={stats.runtimeContractFailures ? "amber" : "cyan"}
        />
        <DecisionMetric
          label="阻断"
          value={String(stats.blockedOrFailed)}
          tone={stats.blockedOrFailed ? "amber" : "neutral"}
        />
      </div>
      <div className="mt-3 rounded border border-slate-800 bg-slate-950/45 px-2.5 py-2">
        <SegmentedMeter
          segments={[
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
          ]}
        />
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          <div className="space-y-1">
            <div className="flex justify-between text-[10px] text-slate-500">
              <span>证据覆盖</span>
              <span>
                {formatPercent(ratioPercent(stats.evidenceBacked, stats.total))}
              </span>
            </div>
            <ProgressTrack
              value={ratioPercent(stats.evidenceBacked, stats.total)}
            />
          </div>
          <div className="space-y-1">
            <div className="flex justify-between text-[10px] text-slate-500">
              <span>运行时回执</span>
              <span>
                {formatPercent(
                  ratioPercent(stats.runtimeReceipts, stats.total),
                )}
              </span>
            </div>
            <ProgressTrack
              value={ratioPercent(stats.runtimeReceipts, stats.total)}
            />
          </div>
          <div className="space-y-1">
            <div className="flex justify-between text-[10px] text-slate-500">
              <span>阻塞/失败</span>
              <span>
                {formatPercent(
                  ratioPercent(stats.blockedOrFailed, stats.total),
                )}
              </span>
            </div>
            <ProgressTrack
              value={ratioPercent(stats.blockedOrFailed, stats.total)}
              tone={stats.blockedOrFailed > 0 ? "warning" : "neutral"}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function DecisionMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "neutral" | "cyan" | "emerald" | "amber";
}) {
  return (
    <div
      className={cn(
        "rounded border bg-slate-950/70 px-3 py-2",
        tone === "neutral" && "border-slate-800",
        tone === "cyan" && "border-slate-700",
        tone === "emerald" && "border-slate-700",
        tone === "amber" && "border-amber-500/20",
      )}
    >
      <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">
        {label}
      </div>
      <div
        className={cn(
          "mt-1 text-lg font-semibold",
          tone === "neutral" && "text-slate-200",
          tone === "cyan" && "text-slate-200",
          tone === "emerald" && "text-slate-200",
          tone === "amber" && "text-amber-300",
        )}
      >
        {value}
      </div>
    </div>
  );
}

// Decision Item with Evidence support
function DecisionItem({
  decision,
  workspace,
}: {
  decision: ResidentDecisionPayload;
  workspace: string;
}) {
  const verdict = decision.verdict || "unknown";
  const isSuccess = verdict === "success";
  const isFailure = verdict === "failure";
  const hasEvidence = Boolean(decision.evidence_bundle_id);
  const [showEvidence, setShowEvidence] = useState(false);
  const actual = decision.actual_outcome || {};
  const decisionSource =
    decisionString(actual.decision_source) || decision.actor || "";
  const evidenceSchema = decisionString(actual.evidence_schema);
  const profileSchema =
    decisionString(actual.execution_profile_schema) ||
    decisionString(actual.profile_schema);
  const validatorResult =
    decisionString(actual.validator_result) ||
    decisionString(actual.validation_status);
  const selectedOption = (decision.options || []).find(
    (option) => option.option_id === decision.selected_option_id,
  );
  const taskCount = decisionNumber(actual.task_count);
  const confidence = formatConfidence(decision.confidence);
  const runtimeContractGate = decisionRuntimeContractGate(decision);
  const runtimeContractStatus =
    decisionString(runtimeContractGate.status) || "unknown";
  const runtimeContractPassed = decisionHasRuntimeContractReceipt(decision);
  const runtimeContractFailed = decisionHasRuntimeContractFailure(decision);
  const runtimeContractSchema = decisionString(
    runtimeContractGate.schema_version,
  );
  const runtimeFailedChecks = decisionStringList(
    runtimeContractGate.failed_check_ids,
  );
  const runtimeEntrypoint = decisionString(actual.role_runtime_entrypoint);
  const agiDecisionProfile = decisionObject(
    actual.resident_agi_decision_profile,
  );
  const agiDecisionProfileSchema = decisionString(
    agiDecisionProfile.schema_version,
  );
  const agiDecisionCapability = decisionObject(
    actual.resident_agi_decision_capability,
  );
  const agiDecisionCapabilityId = decisionString(
    agiDecisionCapability.decision_id,
  );
  const agiRequiredEvidenceInterfaces = decisionStringList(
    actual.resident_agi_required_evidence_interfaces,
  );
  const evidenceRefs = (decision.evidence_refs || []).filter(Boolean);
  const affectedFiles = (decision.affected_files || []).filter(Boolean);
  const affectedSymbols = (decision.affected_symbols || []).filter(Boolean);
  const strategyTags = (decision.strategy_tags || []).filter(Boolean);
  const hardRuleBlockers = Array.isArray(actual.hard_rule_blockers)
    ? actual.hard_rule_blockers.map(decisionString).filter(Boolean)
    : [];
  const handoffImpact = decisionHasHandoffImpact(decision);

  return (
    <Card
      className={cn(
        "border-slate-800 bg-slate-900/50",
        handoffImpact && "border-slate-700",
      )}
    >
      <div className="p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <FileText className="size-4 shrink-0 text-slate-500" />
              <span
                className="truncate text-sm text-slate-300"
                title={decision.summary || "未命名决策"}
              >
                {decision.summary || "未命名决策"}
              </span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              {decision.actor && <span>{decision.actor}</span>}
              {decision.stage && <span>{decision.stage}</span>}
              {decision.decision_id && (
                <span title={decision.decision_id}>
                  #{shortDecisionId(decision.decision_id)}
                </span>
              )}
              <span>{formatTime(decision.timestamp)}</span>
            </div>
          </div>
          <Badge
            className={cn(
              isSuccess && "bg-emerald-500/10 text-emerald-400",
              isFailure && "bg-red-500/10 text-red-400",
              !isSuccess && !isFailure && "bg-slate-500/10 text-slate-400",
            )}
          >
            {formatAgiUiToken(verdict)}
          </Badge>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-4">
          <div className="rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5">
            <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">
              置信度
            </div>
            <div className="mt-1 text-xs font-medium text-slate-200">
              {confidence}
            </div>
          </div>
          <div className="rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5">
            <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">
              校验
            </div>
            <div
              className="mt-1 truncate text-xs font-medium text-slate-200"
              title={validatorResult || "unknown"}
            >
              {formatAgiUiToken(validatorResult || "unknown")}
            </div>
          </div>
          <div className="rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5">
            <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">
              交接
            </div>
            <div
              className={cn(
                "mt-1 text-xs font-medium",
                handoffImpact ? "text-slate-200" : "text-slate-500",
              )}
            >
              {handoffImpact
                ? formatAgiRoleChain("PM → Chief Engineer → Director")
                : formatAgiUiToken("none")}
            </div>
          </div>
          <div
            className={cn(
              "rounded border bg-slate-950/70 px-2 py-1.5",
              runtimeContractPassed && "border-slate-700",
              runtimeContractFailed && "border-rose-500/20",
              !runtimeContractPassed &&
                !runtimeContractFailed &&
                "border-slate-800",
            )}
          >
            <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">
              运行时
            </div>
            <div
              className={cn(
                "mt-1 truncate text-xs font-medium",
                runtimeContractPassed && "text-slate-200",
                runtimeContractFailed && "text-rose-300",
                !runtimeContractPassed &&
                  !runtimeContractFailed &&
                  "text-slate-500",
              )}
              title={runtimeContractSchema || runtimeContractStatus}
            >
              {formatAgiUiToken(runtimeContractStatus)}
            </div>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-end">
          {hasEvidence && (
            <button
              onClick={() => setShowEvidence(!showEvidence)}
              className={cn(
                "flex cursor-pointer items-center gap-1 text-xs transition-colors",
                showEvidence
                  ? "text-slate-100"
                  : "text-slate-400 hover:text-slate-200",
              )}
            >
              <FileSearch className="size-3" />
              {showEvidence ? "隐藏证据" : "查看证据"}
            </button>
          )}
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
          {decision.stage && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              阶段：{decision.stage}
            </span>
          )}
          {decisionSource && (
            <span className="rounded border border-cyan-500/20 bg-cyan-500/10 px-2 py-1 text-cyan-200">
              来源：{decisionSource}
            </span>
          )}
          {evidenceSchema && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              证据：{evidenceSchema}
            </span>
          )}
          {profileSchema && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              画像：{profileSchema}
            </span>
          )}
          {agiDecisionProfileSchema && (
            <span className="rounded border border-cyan-700/40 bg-slate-950 px-2 py-1 text-cyan-200">
              AGI 画像：{agiDecisionProfileSchema}
            </span>
          )}
          {agiDecisionCapabilityId && (
            <span className="rounded border border-cyan-700/40 bg-cyan-950/20 px-2 py-1 text-cyan-200">
              AGI 决策：{agiDecisionCapabilityId}
            </span>
          )}
          {agiRequiredEvidenceInterfaces.slice(0, 3).map((interfaceId) => (
            <span
              key={interfaceId}
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300"
            >
              证据接口：{interfaceId}
            </span>
          ))}
          {runtimeContractSchema && (
            <span
              className={cn(
                "rounded border px-2 py-1",
                runtimeContractPassed &&
                  "border-cyan-500/20 bg-cyan-500/10 text-cyan-200",
                runtimeContractFailed &&
                  "border-rose-500/20 bg-rose-500/10 text-rose-200",
                !runtimeContractPassed &&
                  !runtimeContractFailed &&
                  "border-slate-700 bg-slate-950 text-slate-300",
              )}
            >
              运行时契约：{formatAgiUiToken(runtimeContractStatus)}
            </span>
          )}
          {runtimeEntrypoint && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              运行时：{runtimeEntrypoint}
            </span>
          )}
          {taskCount !== null && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              任务：{taskCount}
            </span>
          )}
          {decision.run_id && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              运行：{shortDecisionId(decision.run_id)}
            </span>
          )}
          {decision.task_id && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              任务：{decision.task_id}
            </span>
          )}
          {decision.goal_id && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              目标：{shortDecisionId(decision.goal_id)}
            </span>
          )}
          {strategyTags.slice(0, 4).map((tag) => (
            <span
              key={tag}
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300"
            >
              标签：{tag}
            </span>
          ))}
          {hardRuleBlockers.map((blocker) => (
            <span
              key={blocker}
              className="rounded border border-amber-500/20 bg-amber-500/10 px-2 py-1 text-amber-200"
            >
              阻断：{blocker}
            </span>
          ))}
          {runtimeFailedChecks.map((failedCheck) => (
            <span
              key={failedCheck}
              className="rounded border border-rose-500/20 bg-rose-500/10 px-2 py-1 text-rose-200"
            >
              运行时阻断：{failedCheck}
            </span>
          ))}
        </div>
        {selectedOption && (
          <div className="mt-3 rounded border border-slate-800 bg-slate-950/70 p-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="font-medium text-slate-200">
                {selectedOption.label ||
                  selectedOption.option_id ||
                  "selected option"}
              </span>
              {typeof selectedOption.estimated_score === "number" && (
                <span className="text-slate-500">
                  分数 {Math.round(selectedOption.estimated_score * 100)}%
                </span>
              )}
            </div>
            {selectedOption.rationale && (
              <div className="mt-1 text-xs text-slate-500">
                {selectedOption.rationale}
              </div>
            )}
          </div>
        )}
        {(decision.context_refs || []).length > 0 && (
          <div
            className="mt-2 truncate text-xs text-slate-500"
            title={(decision.context_refs || []).join(" · ")}
          >
            上下文：{(decision.context_refs || []).slice(0, 3).join(" · ")}
          </div>
        )}
        {evidenceRefs.length > 0 && (
          <div
            className="mt-2 truncate text-xs text-slate-500"
            title={evidenceRefs.join(" · ")}
          >
            证据引用：{evidenceRefs.slice(0, 3).join(" · ")}
          </div>
        )}
        {affectedFiles.length > 0 && (
          <div
            className="mt-2 truncate text-xs text-slate-500"
            title={affectedFiles.join(" · ")}
          >
            文件：{affectedFiles.slice(0, 3).join(" · ")}
          </div>
        )}
        {affectedSymbols.length > 0 && (
          <div
            className="mt-2 truncate text-xs text-slate-500"
            title={affectedSymbols.join(" · ")}
          >
            符号：{affectedSymbols.slice(0, 4).join(" · ")}
          </div>
        )}
      </div>

      {showEvidence && decision.decision_id && (
        <div className="border-t border-slate-800 p-3">
          <EvidenceViewer
            decisionId={decision.decision_id}
            workspace={workspace}
            onClose={() => setShowEvidence(false)}
          />
        </div>
      )}
    </Card>
  );
}
