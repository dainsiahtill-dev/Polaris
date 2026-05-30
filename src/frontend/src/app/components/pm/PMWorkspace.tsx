import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import {
  Crown,
  ScrollText,
  CheckCircle2,
  MessageSquare,
  Settings,
  ChevronLeft,
  FileText,
  ListTodo,
  History,
  Sparkles,
  BarChart3,
  Loader2,
  Stethoscope,
  Activity,
  Zap,
  Brain,
  FileCode,
  Clock,
  AlertCircle,
  RefreshCw,
  GitBranch,
  Database,
  Coins,
  Trash2,
  Wrench,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { PMTaskPanel } from './PMTaskPanel';
import { PMDocumentPanel } from './PMDocumentPanel';
import { PMAIDialoguePanel } from './PMAIDialoguePanel';
import { PMStatusBar } from './PMStatusBar';
import { PMDiagnosticsPanel } from './PMDiagnosticsPanel';
import { PMWorkbenchPanel } from './PMWorkbenchPanel';
import { QualityGateCard, type QualityGateData } from './QualityGateCard';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import { TaskStatus, type PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';
import type { TaskTraceMap } from '@/app/types/taskTrace';
import {
  getPmStatus,
  getPmStartupDiagnostics,
  listPmTasks,
  getPmRequirement,
  listPmDirectorTaskHistory,
  listPmRequirements,
  listPmTaskHistory,
  clearRoleKernelCache,
  getRoleKernelCacheStats,
  getRoleKernelLLMEvents,
  getRoleKernelTokenBudgetStats,
  type PmStatus,
  type PmStartupDiagnosticsResponse,
  type PmDirectorHistoryIteration,
  type PmRequirementEntry,
  type PmTaskHistoryEntry,
  type PmTaskSearchResult,
  type RoleKernelCacheStats,
  type RoleKernelLLMEvent,
  type RoleKernelLLMEventsResponse,
  type RoleKernelTokenBudgetStats,
} from '@/services/pmService';
import { getRoleCapabilities, resolveRoleCapabilities } from '@/services/roleSessionService';

// 阶段到视图的映射
const PHASE_TO_VIEW: Record<string, { view: 'tasks' | 'activity' | 'documents'; icon: React.ReactNode; label: string; color: string }> = {
  'idle': { view: 'tasks', icon: <ListTodo className="w-4 h-4" />, label: '任务', color: 'text-slate-400' },
  'planning': { view: 'tasks', icon: <Brain className="w-4 h-4" />, label: '规划', color: 'text-blue-400' },
  'analyzing': { view: 'activity', icon: <Activity className="w-4 h-4" />, label: '分析', color: 'text-purple-400' },
  'executing': { view: 'activity', icon: <Zap className="w-4 h-4" />, label: '执行', color: 'text-amber-400' },
  'llm_calling': { view: 'activity', icon: <Brain className="w-4 h-4" />, label: '思考', color: 'text-cyan-400' },
  'tool_running': { view: 'activity', icon: <FileCode className="w-4 h-4" />, label: '工具', color: 'text-emerald-400' },
  'verification': { view: 'activity', icon: <CheckCircle2 className="w-4 h-4" />, label: '验证', color: 'text-teal-400' },
  'completed': { view: 'tasks', icon: <CheckCircle2 className="w-4 h-4" />, label: '完成', color: 'text-green-400' },
  'error': { view: 'activity', icon: <Activity className="w-4 h-4" />, label: '错误', color: 'text-red-400' },
};

interface PMWorkspaceProps {
  tasks: PmTask[];
  pmState: Record<string, unknown> | null;
  pmRunning: boolean;
  pmTerminalStatus?: PMTerminalStatus | null;
  pmStartBlockedReason?: string;
  runtimeIssue?: PMRuntimeIssue | null;
  isStarting?: boolean;
  isStopping?: boolean;
  onBackToMain: () => void;
  onTogglePm: () => void | boolean | Promise<void | boolean>;
  onRunPmOnce: () => void | boolean | Promise<void | boolean>;
  workspace: string;
  executionLogs?: LogEntry[];
  llmStreamEvents?: LogEntry[];
  processStreamEvents?: LogEntry[];
  currentPhase?: string;
  factoryMode?: boolean;
  qualityGate?: QualityGateData | null;
  taskTraceMap?: TaskTraceMap;
  onOpenSettings?: () => void;
}

interface PMRuntimeIssue {
  code: string;
  title: string;
  detail: string;
}

interface PMTerminalStatus {
  status?: unknown;
  terminal?: boolean;
  ok?: boolean | null;
  exit_code?: number | null;
  error?: string | null;
  log_path?: string | null;
  contract_path?: string | null;
}

interface PMRuntimeBanner {
  title: string;
  detail: string;
  severity: 'error' | 'warning';
  refs: string[];
  code?: string;
  rootCause?: PMRuntimeRoleLine | null;
  cascades?: PMRuntimeRoleLine[];
  startBlocker?: string;
}

interface PMRuntimeRoleLine {
  role: string;
  detail: string;
}

interface PMRunOnceStatusEvidence {
  triggered: boolean;
  loading: boolean;
  data: PmStatus | null;
  error: string | null;
}

interface PMToggleStatusEvidence {
  triggered: boolean;
  loading: boolean;
  data: PmStatus | null;
  error: string | null;
}

interface PMBackendEvidenceState {
  loading: boolean;
  capabilities: string[];
  capabilitiesError: string;
  diagnostics: PmStartupDiagnosticsResponse | null;
  diagnosticsError: string;
  cacheStats: RoleKernelCacheStats | null;
  cacheError: string;
  llmEvents: RoleKernelLLMEventsResponse | null;
  llmEventsError: string;
  tokenBudgetStats: RoleKernelTokenBudgetStats | null;
  tokenBudgetError: string;
}

const EMPTY_PM_BACKEND_EVIDENCE: PMBackendEvidenceState = {
  loading: false,
  capabilities: [],
  capabilitiesError: '',
  diagnostics: null,
  diagnosticsError: '',
  cacheStats: null,
  cacheError: '',
  llmEvents: null,
  llmEventsError: '',
  tokenBudgetStats: null,
  tokenBudgetError: '',
};

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readRecordNumber(record: Record<string, unknown> | null | undefined, keys: string[]): number | undefined {
  if (!record) return undefined;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return undefined;
}

function readKernelEventString(event: RoleKernelLLMEvent | null | undefined, keys: string[]): string {
  if (!event) return '';
  for (const key of keys) {
    const value = event[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return '';
}

function formatPmKernelEvent(event: RoleKernelLLMEvent | null | undefined): string {
  if (!event) return 'none';
  const eventType = readKernelEventString(event, ['event_type', 'type', 'status']) || 'event';
  const model = readKernelEventString(event, ['model', 'model_name', 'provider']);
  const tokens = readRecordNumber(event, ['tokens', 'token_count', 'total_tokens']);
  return [
    eventType.replace(/_/g, ' '),
    model,
    typeof tokens === 'number' ? `${tokens} tokens` : '',
  ].filter(Boolean).join(' · ');
}

function formatPmCacheStats(stats: RoleKernelCacheStats | null): string {
  if (!stats) return 'unavailable';
  const hits = readRecordNumber(stats, ['hits']) ?? 0;
  const misses = readRecordNumber(stats, ['misses']) ?? 0;
  const size = readRecordNumber(stats, ['size']) ?? 0;
  const maxSize = readRecordNumber(stats, ['max_size', 'maxSize']);
  const hitRate = readRecordNumber(stats, ['hit_rate', 'hitRate']);
  return [
    `hits=${hits}`,
    `misses=${misses}`,
    `size=${size}${typeof maxSize === 'number' ? `/${maxSize}` : ''}`,
    typeof hitRate === 'number' ? `hit=${hitRate}%` : '',
  ].filter(Boolean).join(' · ');
}

function formatPmTokenBudget(stats: RoleKernelTokenBudgetStats | null): string {
  if (!stats) return 'unavailable';
  const total = readRecordNumber(stats, ['total', 'total_budget']);
  const available = readRecordNumber(stats, ['available_conversation', 'remaining']);
  const safety = readRecordNumber(stats, ['safety_margin']);
  return [
    typeof total === 'number' ? `total=${total}` : '',
    typeof available === 'number' ? `available=${available}` : '',
    typeof safety === 'number' ? `margin=${safety}` : '',
  ].filter(Boolean).join(' · ') || 'stats ready';
}

function evidenceEndpoint(endpoint: string, workspace = ''): string {
  const value = String(workspace || '').trim();
  if (!value) return endpoint;
  const separator = endpoint.includes('?') ? '&' : '?';
  return `${endpoint}${separator}workspace=${encodeURIComponent(value)}`;
}

function EvidenceEndpointBadge({
  endpoint,
  testId,
}: {
  endpoint: string;
  testId?: string;
}) {
  return (
    <span
      className="shrink-0 rounded border border-white/10 bg-slate-950/70 px-1.5 py-0.5 text-[9px] font-medium text-slate-500"
      title={endpoint}
      data-endpoint={endpoint}
      data-testid={testId}
    >
      API
    </span>
  );
}

function formatPmDiagnostics(diagnostics: PmStartupDiagnosticsResponse | null): string {
  if (!diagnostics) return 'unavailable';
  const llmState = diagnostics.llm?.state || (diagnostics.llm?.ok ? 'ready' : 'blocked');
  const workspaceState = diagnostics.workspace?.status || (diagnostics.workspace?.ok ? 'ready' : 'blocked');
  const planningInputState = diagnostics.planning_input?.status || (diagnostics.planning_input?.ok ? 'ready' : 'missing');
  const blockedRoles = Array.isArray(diagnostics.llm?.blocked_roles) ? diagnostics.llm.blocked_roles : [];
  const issues = Array.isArray(diagnostics.issues) ? diagnostics.issues.length : 0;
  const startupBlockers = pmStartupBlockers(diagnostics).length;
  return [
    startupBlockers > 0 ? 'start=blocked' : diagnostics.ok ? 'ready' : 'degraded',
    `llm=${llmState}`,
    blockedRoles.length > 0 ? `blocked=${blockedRoles.join(',')}` : '',
    `workspace=${workspaceState}`,
    `input=${planningInputState}`,
    issues > 0 ? `issues=${issues}` : '',
  ].filter(Boolean).join(' · ');
}

const PM_STARTUP_BLOCKER_LABELS: Record<string, string> = {
  lancedb_unavailable: 'LanceDB 不可用',
  llm_not_ready: 'PM LLM 未通过就绪检查',
  workspace_unavailable: '工作区不可用',
  workspace_docs_missing: 'docs/ 初始化未完成',
  planning_input_missing: '缺少需求/计划输入',
  planning_input_empty: '需求/计划输入为空',
  planning_input_unreadable: '需求/计划输入无法读取',
};

const PM_HARD_BLOCKER_ISSUES = new Set(Object.keys(PM_STARTUP_BLOCKER_LABELS));

function pmStartupBlockers(diagnostics: PmStartupDiagnosticsResponse | null): string[] {
  if (!diagnostics) {
    return [];
  }
  if (Array.isArray(diagnostics.startup_blockers) && diagnostics.startup_blockers.length > 0) {
    return diagnostics.startup_blockers
      .map((issue) => String(issue || '').trim())
      .filter((issue) => issue.length > 0);
  }
  const hasExplicitStartupSignal = typeof diagnostics.can_start === 'boolean' || Array.isArray(diagnostics.startup_blockers);
  if (hasExplicitStartupSignal && diagnostics.can_start !== false) {
    return [];
  }
  return (diagnostics.issues || []).filter((issue) => PM_HARD_BLOCKER_ISSUES.has(issue));
}

function formatPmStartupBlockReason(diagnostics: PmStartupDiagnosticsResponse | null): string {
  const blockers = pmStartupBlockers(diagnostics);
  if (blockers.length === 0) {
    return '';
  }
  const primary = PM_STARTUP_BLOCKER_LABELS[blockers[0]] || blockers[0].replace(/_/g, ' ');
  const extraCount = blockers.length - 1;
  return `PM 启动诊断未通过：${primary}${extraCount > 0 ? `，另有 ${extraCount} 项阻断` : ''}`;
}

function taskListRecord(task: PmTaskSearchResult): Record<string, unknown> {
  return task as Record<string, unknown>;
}

function taskListMetadata(task: PmTaskSearchResult): Record<string, unknown> {
  const metadata = taskListRecord(task).metadata;
  return metadata && typeof metadata === 'object' && !Array.isArray(metadata)
    ? metadata as Record<string, unknown>
    : {};
}

function readTaskListValue(task: PmTaskSearchResult, keys: string[]): unknown {
  const record = taskListRecord(task);
  const metadata = taskListMetadata(task);
  for (const key of keys) {
    const directValue = record[key];
    if (directValue !== undefined && directValue !== null) return directValue;
    const metadataValue = metadata[key];
    if (metadataValue !== undefined && metadataValue !== null) return metadataValue;
  }
  return undefined;
}

function readTaskListString(task: PmTaskSearchResult, keys: string[]): string {
  const value = readTaskListValue(task, keys);
  return typeof value === 'string' ? value.trim() : '';
}

function toTaskListStrings(value: unknown): string[] {
  if (!Array.isArray(value)) {
    const token = typeof value === 'string' ? value.trim() : '';
    return token ? [token] : [];
  }

  return value
    .map((item) => {
      if (typeof item === 'string') return item.trim();
      if (item && typeof item === 'object') {
        const record = item as Record<string, unknown>;
        return String(record.description || record.title || record.name || record.path || record.id || '').trim();
      }
      return String(item || '').trim();
    })
    .filter(Boolean);
}

function normalizePmTaskListStatus(task: PmTaskSearchResult): TaskStatus {
  const status = readTaskListString(task, ['status', 'state']).toLowerCase();
  if (status === 'completed' || status === 'done' || status === 'success') return TaskStatus.COMPLETED;
  if (status === 'running' || status === 'in_progress' || status === 'active') return TaskStatus.IN_PROGRESS;
  if (status === 'blocked') return TaskStatus.BLOCKED;
  if (status === 'failed' || status === 'failure') return TaskStatus.FAILED;
  return TaskStatus.PENDING;
}

function normalizePmTaskListPriority(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    const parsed = Number.parseInt(normalized.replace(/^p/, ''), 10);
    if (Number.isFinite(parsed)) return parsed;
    if (normalized === 'critical') return 0;
    if (normalized === 'high') return 1;
    if (normalized === 'medium') return 2;
    if (normalized === 'low') return 3;
  }
  return 99;
}

function normalizePmTaskListRow(task: PmTaskSearchResult): PmTask | null {
  const id = readTaskListString(task, ['id', 'task_id']);
  if (!id) return null;

  const status = normalizePmTaskListStatus(task);
  const acceptance = [
    ...toTaskListStrings(readTaskListValue(task, ['acceptance'])),
    ...toTaskListStrings(readTaskListValue(task, ['acceptance_criteria', 'acceptanceCriteria'])),
  ].map((description) => ({ description }));
  const qaContract = readTaskListValue(task, ['qa_contract']);
  const done = task.done === true || task.completed === true || status === TaskStatus.COMPLETED;

  return {
    id,
    title: readTaskListString(task, ['title', 'subject', 'name']) || id,
    subject: readTaskListString(task, ['subject']),
    goal: readTaskListString(task, ['goal']),
    summary: readTaskListString(task, ['summary', 'snippet', 'description']),
    description: readTaskListString(task, ['description']),
    status,
    done,
    completed: done,
    priority: normalizePmTaskListPriority(readTaskListValue(task, ['priority'])),
    acceptance,
    acceptance_criteria: toTaskListStrings(readTaskListValue(task, ['acceptance_criteria', 'acceptanceCriteria'])),
    execution_checklist: toTaskListStrings(readTaskListValue(task, ['execution_checklist', 'execution_steps', 'steps'])),
    target_files: toTaskListStrings(readTaskListValue(task, ['target_files', 'scope_paths', 'files'])),
    dependencies: toTaskListStrings(readTaskListValue(task, ['dependencies', 'blocked_by'])),
    qa_contract: qaContract && typeof qaContract === 'object' ? qaContract as Record<string, unknown> : undefined,
    blueprint_id: readTaskListString(task, ['blueprint_id', 'blueprintId']) || null,
    blueprint_path: readTaskListString(task, ['blueprint_path', 'blueprintPath']) || null,
    runtime_blueprint_path: readTaskListString(task, ['runtime_blueprint_path', 'runtimeBlueprintPath']) || null,
    assignee: readTaskListString(task, ['assignee', 'assigned_to', 'assignedTo']) || undefined,
    assigned_to: readTaskListString(task, ['assigned_to', 'assignedTo']) || undefined,
    created_at: readTaskListString(task, ['created_at', 'createdAt']) || undefined,
    started_at: readTaskListString(task, ['started_at', 'startedAt']) || undefined,
    completed_at: readTaskListString(task, ['completed_at', 'completedAt']) || undefined,
    metadata: {
      ...taskListRecord(task),
      ...taskListMetadata(task),
      source: readTaskListString(task, ['source']) || 'pm_task_list',
    },
  };
}

function mergePmTaskEvidenceRows(runtimeTasks: PmTask[], backendTasks: PmTask[]): PmTask[] {
  const rows = new Map<string, PmTask>();
  for (const task of backendTasks) {
    if (task.id) rows.set(task.id, task);
  }
  for (const task of runtimeTasks) {
    if (task.id) rows.set(task.id, task);
  }
  return Array.from(rows.values());
}

const PM_RUNTIME_ROLE_LABELS: Record<string, string> = {
  pm: 'PM',
  chiefengineer: 'Chief Engineer',
  director: 'Director',
  qa: 'QA',
};

function normalizeRuntimeRoleLabel(value: string): string {
  return value.replace(/[^a-z0-9]/gi, '').toLowerCase();
}

function parseRuntimeRoleLine(line: string): PMRuntimeRoleLine | null {
  const match = /^([A-Za-z][A-Za-z _-]{1,32}):\s*(.+)$/.exec(line.trim());
  if (!match) return null;
  const roleToken = normalizeRuntimeRoleLabel(match[1]);
  const role = PM_RUNTIME_ROLE_LABELS[roleToken];
  const detail = match[2].trim();
  if (!role || !detail) return null;
  return { role, detail };
}

function runtimeIssueLines(issue: PMRuntimeIssue | null | undefined): string[] {
  return String(issue?.detail || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function splitPMRuntimeIssue(issue: PMRuntimeIssue | null | undefined): {
  rootCause: PMRuntimeRoleLine | null;
  cascades: PMRuntimeRoleLine[];
  detail: string;
} {
  const lines = runtimeIssueLines(issue);
  const roleLines = lines.map(parseRuntimeRoleLine).filter((line): line is PMRuntimeRoleLine => Boolean(line));
  const rootCause = roleLines.find((line) => line.role === 'PM') ?? null;
  const cascades = roleLines.filter((line) => line.role !== rootCause?.role);
  const nonRoleLines = lines.filter((line) => !parseRuntimeRoleLine(line));
  const detail = rootCause?.detail || nonRoleLines.join('\n') || String(issue?.detail || issue?.code || '').trim();
  return { rootCause, cascades, detail };
}

function resolvePMRuntimeBanner({
  pmRunning,
  pmStartBlockedReason,
  runtimeIssue,
  pmTerminalStatus,
}: {
  pmRunning: boolean;
  pmStartBlockedReason?: string;
  runtimeIssue?: PMRuntimeIssue | null;
  pmTerminalStatus?: PMTerminalStatus | null;
}): PMRuntimeBanner | null {
  if (runtimeIssue && !pmRunning) {
    const breakdown = splitPMRuntimeIssue(runtimeIssue);
    return {
      title: runtimeIssue.title || 'PM 运行已终止',
      detail: breakdown.detail || runtimeIssue.code || 'PM 运行失败，请查看运行日志。',
      severity: 'error',
      refs: [],
      code: runtimeIssue.code,
      rootCause: breakdown.rootCause,
      cascades: breakdown.cascades,
      startBlocker: pmStartBlockedReason || '',
    };
  }

  if (!pmRunning && pmStartBlockedReason) {
    return {
      title: 'PM 启动被阻止',
      detail: pmStartBlockedReason,
      severity: 'warning',
      refs: [],
    };
  }

  if (!pmTerminalStatus || pmRunning) return null;

  const status = stringValue(pmTerminalStatus.status).toLowerCase();
  const exitCode = typeof pmTerminalStatus.exit_code === 'number' ? pmTerminalStatus.exit_code : null;
  const error = stringValue(pmTerminalStatus.error);
  const failed = (
    (exitCode !== null && exitCode !== 0)
    || pmTerminalStatus.ok === false
    || (pmTerminalStatus.terminal === true && status === 'failed')
    || Boolean(error)
  );
  if (!failed) return null;

  const detailParts = [
    exitCode !== null ? `退出码: ${exitCode}` : '',
    error || '',
  ].filter(Boolean);

  return {
    title: 'PM 运行已终止',
    detail: detailParts.join('\n') || 'PM 进程已进入失败终态，请查看运行日志和任务合同。',
    severity: 'error',
    refs: [
      stringValue(pmTerminalStatus.contract_path),
      stringValue(pmTerminalStatus.log_path),
    ].filter(Boolean),
  };
}

function PMBackendEvidenceStrip({
  evidence,
  cacheClearing,
  cacheClearStatus,
  onRefresh,
  onClearCache,
  workspace,
}: {
  evidence: PMBackendEvidenceState;
  cacheClearing: boolean;
  cacheClearStatus: string;
  onRefresh: () => void;
  onClearCache: () => void;
  workspace: string;
}) {
  const llmEventCount = evidence.llmEvents?.count
    ?? (Array.isArray(evidence.llmEvents?.events) ? evidence.llmEvents.events.length : 0);
  const latestLLMEvent = evidence.llmEvents?.events?.[0] ?? null;
  const diagnosticsLabel = formatPmDiagnostics(evidence.diagnostics);
  const cacheLabel = formatPmCacheStats(evidence.cacheStats);
  const tokenBudgetLabel = formatPmTokenBudget(evidence.tokenBudgetStats);
  const hasError = Boolean(
    evidence.capabilitiesError
    || evidence.diagnosticsError
    || evidence.llmEventsError
    || evidence.cacheError
    || evidence.tokenBudgetError,
  );
  const canStart = evidence.diagnostics?.can_start;
  const summaryTone = evidence.loading
    ? 'border-slate-500/20 bg-slate-500/10 text-slate-300'
    : hasError
      ? 'border-rose-400/25 bg-rose-500/10 text-rose-200'
      : canStart === false
        ? 'border-amber-400/25 bg-amber-500/10 text-amber-200'
        : 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200';
  const summaryLabel = evidence.loading
    ? '检查中'
    : hasError
      ? '需要查看'
      : canStart === false
        ? '门禁阻断'
        : '就绪';
  const capabilityLabel = evidence.loading
    ? '能力 ...'
    : evidence.capabilitiesError
      ? '能力 ?'
      : `能力 ${evidence.capabilities.length}`;

  return (
    <section
      className="border-b border-white/10 bg-slate-950/60 px-4 py-2 text-xs text-slate-300"
      data-testid="pm-backend-evidence-strip"
      aria-label="PM backend evidence"
    >
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-2 outline-none">
          <Wrench className="h-3.5 w-3.5 shrink-0 text-amber-300" />
          <span className="shrink-0 font-medium text-slate-100">PM 后端状态</span>
          <span className={cn('shrink-0 rounded-full border px-2 py-0.5 text-[10px]', summaryTone)}>
            {summaryLabel}
          </span>
          <span className="shrink-0 rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
            {capabilityLabel}
          </span>
          <span className="min-w-0 truncate rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-slate-400" title={diagnosticsLabel}>
            {diagnosticsLabel}
          </span>
          <span className="hidden shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-slate-400 xl:inline-flex">
            events={llmEventCount}
          </span>
          <span className="ml-auto shrink-0 text-[10px] text-slate-500 group-open:hidden">详情</span>
          <span className="ml-auto hidden shrink-0 text-[10px] text-slate-500 group-open:inline">收起</span>
          <Button
            variant="ghost"
            size="icon"
            onClick={(event) => {
              event.preventDefault();
              onRefresh();
            }}
            disabled={evidence.loading || cacheClearing}
            title="刷新 PM 后端状态"
            className="h-7 w-7 shrink-0 text-slate-400 hover:bg-amber-500/10 hover:text-amber-300"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', evidence.loading && 'animate-spin')} />
          </Button>
        </summary>

        <div className="mt-2 grid gap-2 rounded-lg border border-white/10 bg-slate-950/50 p-2 md:grid-cols-2 xl:grid-cols-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2 rounded-md bg-white/[0.025] px-2 py-1.5">
          <Wrench className="h-3.5 w-3.5 shrink-0 text-amber-300" />
          <span className="shrink-0 font-medium text-amber-100">Capabilities</span>
          <EvidenceEndpointBadge
            endpoint="/v2/roles/capabilities/pm?host_kind=electron_workbench"
            testId="pm-capabilities-endpoint"
          />
          {evidence.loading ? (
            <span className="text-slate-400">读取中...</span>
          ) : evidence.capabilitiesError ? (
            <span className="text-rose-300">{evidence.capabilitiesError}</span>
          ) : (
            <span className="max-w-[260px] truncate text-emerald-300" title={evidence.capabilities.join(', ')}>
              {evidence.capabilities.length > 0 ? evidence.capabilities.slice(0, 5).join(', ') : 'none'}
            </span>
          )}
        </div>

          <div className="flex min-w-0 flex-wrap items-center gap-2 rounded-md bg-white/[0.025] px-2 py-1.5">
          <Activity className="h-3.5 w-3.5 shrink-0 text-amber-300" />
          <span className="shrink-0 font-medium text-amber-100">Diagnostics</span>
          <EvidenceEndpointBadge
            endpoint={evidenceEndpoint('/v2/pm/diagnostics', workspace)}
            testId="pm-diagnostics-endpoint"
          />
          {evidence.loading ? (
            <span className="text-slate-400">读取中...</span>
          ) : evidence.diagnosticsError ? (
            <span className="text-rose-300">{evidence.diagnosticsError}</span>
          ) : (
            <span className="max-w-[320px] truncate text-emerald-300" title={diagnosticsLabel}>
              {diagnosticsLabel}
            </span>
          )}
        </div>

          <div className="flex min-w-0 flex-wrap items-center gap-2 rounded-md bg-white/[0.025] px-2 py-1.5">
          <Brain className="h-3.5 w-3.5 shrink-0 text-amber-300" />
          <span className="shrink-0 font-medium text-amber-100">LLM events</span>
          <EvidenceEndpointBadge
            endpoint={evidenceEndpoint('/v2/pm/llm-events?role=pm&limit=5', workspace)}
            testId="pm-llm-events-endpoint"
          />
          {evidence.loading ? (
            <span className="text-slate-400">读取中...</span>
          ) : evidence.llmEventsError ? (
            <span className="text-rose-300">{evidence.llmEventsError}</span>
          ) : (
            <span className="max-w-[260px] truncate text-emerald-300">
              events={llmEventCount}{latestLLMEvent ? ` · ${formatPmKernelEvent(latestLLMEvent)}` : ''}
            </span>
          )}
        </div>

          <div className="flex min-w-0 flex-wrap items-center gap-2 rounded-md bg-white/[0.025] px-2 py-1.5">
          <Database className="h-3.5 w-3.5 shrink-0 text-amber-300" />
          <span className="shrink-0 font-medium text-amber-100">Kernel cache</span>
          <EvidenceEndpointBadge endpoint="/v2/pm/cache-stats" testId="pm-cache-endpoint" />
          {evidence.loading ? (
            <span className="text-slate-400">读取中...</span>
          ) : evidence.cacheError ? (
            <span className="text-rose-300">{evidence.cacheError}</span>
          ) : (
            <span className="max-w-[260px] truncate text-emerald-300">{cacheLabel}</span>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={onClearCache}
            disabled={evidence.loading || cacheClearing}
            data-testid="pm-kernel-cache-clear"
            title="清空 PM LLM 缓存"
            className="h-6 w-6 text-slate-400 hover:bg-red-500/10 hover:text-red-300"
          >
            {cacheClearing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
          </Button>
          {cacheClearStatus ? (
            <span
              data-testid="pm-kernel-cache-clear-result"
              data-endpoint="/v2/pm/cache-clear"
              title="/v2/pm/cache-clear"
              className="truncate text-amber-200"
            >
              {cacheClearStatus}
            </span>
          ) : null}
        </div>

          <div className="flex min-w-0 flex-wrap items-center gap-2 rounded-md bg-white/[0.025] px-2 py-1.5">
          <Coins className="h-3.5 w-3.5 shrink-0 text-amber-300" />
          <span className="shrink-0 font-medium text-amber-100">Token budget</span>
          <EvidenceEndpointBadge endpoint="/v2/pm/token-budget-stats" testId="pm-token-budget-endpoint" />
          {evidence.loading ? (
            <span className="text-slate-400">读取中...</span>
          ) : evidence.tokenBudgetError ? (
            <span className="text-rose-300">{evidence.tokenBudgetError}</span>
          ) : (
            <span className="max-w-[260px] truncate text-emerald-300">{tokenBudgetLabel}</span>
          )}
        </div>
        </div>
      </details>
    </section>
  );
}

type PMActiveView = 'tasks' | 'activity' | 'documents' | 'requirements' | 'history' | 'analytics' | 'workbench';

export function PMWorkspace({
  tasks,
  pmState,
  pmRunning,
  pmTerminalStatus = null,
  pmStartBlockedReason = '',
  runtimeIssue = null,
  isStarting,
  isStopping = false,
  onBackToMain,
  onTogglePm,
  onRunPmOnce,
  workspace,
  executionLogs = [],
  llmStreamEvents = [],
  processStreamEvents = [],
  currentPhase = 'idle',
  factoryMode = false,
  qualityGate = null,
  taskTraceMap,
  onOpenSettings,
}: PMWorkspaceProps) {
  const [activeView, setActiveView] = useState<PMActiveView>('tasks');
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedDocumentPath, setSelectedDocumentPath] = useState<string | null>(null);
  const [showAIDialogue, setShowAIDialogue] = useState(true);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [runOnceStatusEvidence, setRunOnceStatusEvidence] = useState<PMRunOnceStatusEvidence>({
    triggered: false,
    loading: false,
    data: null,
    error: null,
  });
  const [toggleStatusEvidence, setToggleStatusEvidence] = useState<PMToggleStatusEvidence>({
    triggered: false,
    loading: false,
    data: null,
    error: null,
  });
  const [backendPmTasks, setBackendPmTasks] = useState<PmTask[]>([]);
  const [backendPmTaskError, setBackendPmTaskError] = useState('');
  const [isLoadingBackendPmTasks, setIsLoadingBackendPmTasks] = useState(false);
  const [pmBackendEvidence, setPmBackendEvidence] = useState<PMBackendEvidenceState>(EMPTY_PM_BACKEND_EVIDENCE);
  const [pmKernelCacheClearing, setPmKernelCacheClearing] = useState(false);
  const [pmKernelCacheClearStatus, setPmKernelCacheClearStatus] = useState('');

  const loadBackendPmTasks = useCallback(async () => {
    if (!workspace) {
      setBackendPmTasks([]);
      setBackendPmTaskError('');
      setIsLoadingBackendPmTasks(false);
      return;
    }

    setIsLoadingBackendPmTasks(true);
    setBackendPmTaskError('');
    try {
      const result = await listPmTasks({ limit: 100, offset: 0, workspace });
      if (!result.ok || !result.data) {
        throw new Error(result.error || 'PM task list unavailable');
      }
      const rows = Array.isArray(result.data.tasks)
        ? result.data.tasks
        : Array.isArray(result.data.items)
          ? result.data.items
          : [];
      setBackendPmTasks(rows.map(normalizePmTaskListRow).filter((task): task is PmTask => Boolean(task)));
    } catch (error) {
      setBackendPmTasks([]);
      setBackendPmTaskError(error instanceof Error ? error.message : 'PM task list unavailable');
    } finally {
      setIsLoadingBackendPmTasks(false);
    }
  }, [workspace]);

  useEffect(() => {
    void loadBackendPmTasks();
  }, [loadBackendPmTasks]);

  const loadPmBackendEvidence = useCallback(async () => {
    if (!workspace || factoryMode) {
      setPmBackendEvidence(EMPTY_PM_BACKEND_EVIDENCE);
      setPmKernelCacheClearStatus('');
      return;
    }

    setPmBackendEvidence((current) => ({
      ...current,
      loading: true,
    }));

    try {
      const [capabilityResult, diagnosticsResult, cacheResult, tokenBudgetResult, llmResult] = await Promise.all([
        getRoleCapabilities('pm', 'electron_workbench'),
        getPmStartupDiagnostics(workspace),
        getRoleKernelCacheStats('pm'),
        getRoleKernelTokenBudgetStats('pm'),
        getRoleKernelLLMEvents('pm', { role: 'pm', limit: 5, workspace }),
      ]);

      setPmBackendEvidence({
        loading: false,
        capabilities: capabilityResult.ok && capabilityResult.data
          ? resolveRoleCapabilities(capabilityResult.data, 'electron_workbench').sort()
          : [],
        capabilitiesError: capabilityResult.ok ? '' : capabilityResult.error || 'PM capabilities unavailable',
        diagnostics: diagnosticsResult.ok && diagnosticsResult.data ? diagnosticsResult.data : null,
        diagnosticsError: diagnosticsResult.ok ? '' : diagnosticsResult.error || 'PM diagnostics unavailable',
        cacheStats: cacheResult.ok && cacheResult.data ? cacheResult.data : null,
        cacheError: cacheResult.ok ? '' : cacheResult.error || 'PM cache stats unavailable',
        llmEvents: llmResult.ok && llmResult.data
          ? { ...llmResult.data, events: Array.isArray(llmResult.data.events) ? llmResult.data.events : [] }
          : null,
        llmEventsError: llmResult.ok ? '' : llmResult.error || 'PM LLM events unavailable',
        tokenBudgetStats: tokenBudgetResult.ok && tokenBudgetResult.data ? tokenBudgetResult.data : null,
        tokenBudgetError: tokenBudgetResult.ok ? '' : tokenBudgetResult.error || 'PM token budget unavailable',
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'PM backend evidence unavailable';
      setPmBackendEvidence({
        ...EMPTY_PM_BACKEND_EVIDENCE,
        capabilitiesError: message,
        diagnosticsError: message,
        cacheError: message,
        llmEventsError: message,
        tokenBudgetError: message,
      });
    }
  }, [factoryMode, workspace]);

  useEffect(() => {
    void loadPmBackendEvidence();
  }, [loadPmBackendEvidence]);

  const handleClearPmKernelCache = useCallback(async () => {
    setPmKernelCacheClearing(true);
    setPmKernelCacheClearStatus('');
    try {
      const result = await clearRoleKernelCache('pm');
      if (result.ok) {
        setPmKernelCacheClearStatus(result.data?.message || 'cleared');
        await loadPmBackendEvidence();
      } else {
        setPmKernelCacheClearStatus(result.error || 'clear failed');
      }
    } catch (error) {
      setPmKernelCacheClearStatus(error instanceof Error ? error.message : 'clear failed');
    } finally {
      setPmKernelCacheClearing(false);
    }
  }, [loadPmBackendEvidence]);

  const pmTaskEvidenceRows = useMemo(
    () => mergePmTaskEvidenceRows(tasks, backendPmTasks),
    [backendPmTasks, tasks],
  );
  
  // 用户手动切换视图的标记（避免自动切换覆盖用户选择）
  const userSwitchedViewRef = useRef(false);
  const lastPhaseRef = useRef<string>('');
  
  // 自动切换视图基于当前阶段
  useEffect(() => {
    if (!pmRunning || userSwitchedViewRef.current) return;
    
    const phaseConfig = PHASE_TO_VIEW[currentPhase] || PHASE_TO_VIEW['idle'];
    
    // 只有当阶段真正改变时才切换
    if (currentPhase !== lastPhaseRef.current) {
      lastPhaseRef.current = currentPhase;
      
      // 如果当前视图不是推荐的视图，则自动切换
      if (phaseConfig.view !== activeView) {
        setActiveView(phaseConfig.view);
      }
    }
  }, [currentPhase, pmRunning, activeView]);
  
  // 当用户手动点击导航时，记录用户偏好
  const handleViewChange = useCallback((view: PMActiveView) => {
    userSwitchedViewRef.current = true;
    setActiveView(view);
  }, []);

  const handleTaskSelect = useCallback((taskId: string | null) => {
    userSwitchedViewRef.current = true;
    setSelectedTaskId(taskId);
    setActiveView('tasks');
  }, []);

  const handleBackendPmTaskCreated = useCallback((task: PmTask) => {
    setBackendPmTasks((current) => {
      if (!task.id) return current;
      return mergePmTaskEvidenceRows([], [task, ...current.filter((item) => item.id !== task.id)]);
    });
  }, []);

  const pmDiagnosticStartReason = useMemo(
    () => formatPmStartupBlockReason(pmBackendEvidence.diagnostics),
    [pmBackendEvidence.diagnostics],
  );
  const pmStartBlockReason = !pmRunning ? pmStartBlockedReason || pmDiagnosticStartReason : '';
  const pmStarting = Boolean(isStarting);
  const pmStopping = Boolean(isStopping);
  const pmToggleBusyReason = pmStarting
    ? 'PM 正在启动，请等待状态回传。'
    : pmStopping
      ? 'PM 正在停止，请等待状态回传。'
      : toggleStatusEvidence.loading
        ? 'PM 状态确认中，请等待后端回传。'
        : '';
  const pmRunOnceBusyReason = pmStarting
    ? 'PM 正在启动，请等待状态回传。'
    : pmStopping
      ? 'PM 正在停止，请等待状态回传。'
      : runOnceStatusEvidence.loading
        ? 'PM 单次督办状态确认中，请等待后端回传。'
        : '';
  const pmRunOnceDisabledReason = factoryMode
    ? '工厂模式下无法使用此功能'
    : pmRunOnceBusyReason
      || (pmRunning ? 'PM 正在运行，不能同时触发单次督办。' : '')
      || pmStartBlockReason;
  const pmToggleDisabledReason = factoryMode
    ? '工厂模式下无法使用此功能'
    : pmToggleBusyReason || pmStartBlockReason;

  const handleRunPmOnce = useCallback(async () => {
    if (pmRunOnceBusyReason) {
      return;
    }

    if (pmStartBlockReason) {
      setRunOnceStatusEvidence({
        triggered: true,
        loading: false,
        data: null,
        error: pmStartBlockReason,
      });
      return;
    }
    setRunOnceStatusEvidence({
      triggered: true,
      loading: true,
      data: null,
      error: null,
    });
    try {
      await Promise.resolve(onRunPmOnce());
      const statusResult = await getPmStatus(workspace);
      if (statusResult.ok && statusResult.data) {
        setRunOnceStatusEvidence({
          triggered: true,
          loading: false,
          data: statusResult.data,
          error: null,
        });
        return;
      }
      setRunOnceStatusEvidence({
        triggered: true,
        loading: false,
        data: null,
        error: statusResult.error || 'PM status unavailable',
      });
    } catch (error) {
      setRunOnceStatusEvidence({
        triggered: true,
        loading: false,
        data: null,
        error: error instanceof Error ? error.message : 'PM status unavailable',
      });
    }
  }, [onRunPmOnce, pmRunOnceBusyReason, pmStartBlockReason, workspace]);

  const handleTogglePm = useCallback(async () => {
    if (pmToggleBusyReason) {
      return;
    }

    if (!pmRunning && pmStartBlockReason) {
      setToggleStatusEvidence({
        triggered: true,
        loading: false,
        data: null,
        error: pmStartBlockReason,
      });
      return;
    }
    setToggleStatusEvidence({
      triggered: true,
      loading: true,
      data: null,
      error: null,
    });
    try {
      await Promise.resolve(onTogglePm());
      const statusResult = await getPmStatus(workspace);
      if (statusResult.ok && statusResult.data) {
        setToggleStatusEvidence({
          triggered: true,
          loading: false,
          data: statusResult.data,
          error: null,
        });
        return;
      }
      setToggleStatusEvidence({
        triggered: true,
        loading: false,
        data: null,
        error: statusResult.error || 'PM status unavailable',
      });
    } catch (error) {
      setToggleStatusEvidence({
        triggered: true,
        loading: false,
        data: null,
        error: error instanceof Error ? error.message : 'PM status unavailable',
      });
    }
  }, [onTogglePm, pmRunning, pmStartBlockReason, pmToggleBusyReason, workspace]);

  const handleDocumentSelect = useCallback((path: string) => {
    userSwitchedViewRef.current = true;
    setSelectedDocumentPath(path);
    setActiveView('documents');
  }, []);

  const completedTasks = pmTaskEvidenceRows.filter(t => t.status === 'completed' || t.done).length;
  const totalTasks = pmTaskEvidenceRows.length;
  const progress = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
  
  // 实时任务统计
  const taskStats = {
    pending: pmTaskEvidenceRows.filter(t => !t.status || t.status === 'pending').length,
    running: pmTaskEvidenceRows.filter(t => String(t.status) === 'running' || t.status === 'in_progress').length,
    completed: completedTasks,
    blocked: pmTaskEvidenceRows.filter(t => t.status === 'blocked' || t.status === 'failed').length,
  };
  
  // 获取当前阶段信息
  const currentPhaseConfig = PHASE_TO_VIEW[currentPhase] || PHASE_TO_VIEW['idle'];
  
  // 获取当前正在执行的任务
  const currentTask = pmTaskEvidenceRows.find((task) => task.status === 'in_progress' || String(task.status) === 'running') ?? null;
  const pmStartBlocked = Boolean(pmStartBlockReason && !pmRunning);
  const pmRunOnceDisabled = Boolean(pmRunOnceDisabledReason);
  const pmToggleDisabled = Boolean(pmToggleDisabledReason);
  const pmRuntimeBanner = resolvePMRuntimeBanner({
    pmRunning,
    pmStartBlockedReason: pmStartBlockReason,
    runtimeIssue,
    pmTerminalStatus,
  });
  const shouldShowSideAIDialogue = showAIDialogue && activeView !== 'workbench';

  return (
    <div data-testid="pm-workspace" className="flex flex-col h-full bg-gradient-to-br from-[var(--ink-indigo)] via-[rgba(28,18,48,0.8)] to-[rgba(14,20,40,0.95)] text-slate-100 overflow-hidden">
      {/* PM Header - PM 主题 */}
      {!factoryMode && (
      <header className="h-14 flex items-center justify-between px-4 border-b border-amber-500/20 bg-gradient-to-r from-slate-900 via-slate-900 to-amber-950/20">
        <div className="flex items-center gap-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBackToMain}
            data-testid="pm-workspace-back"
            aria-label="返回主界面"
            className="text-slate-400 hover:text-slate-100 hover:bg-white/5"
          >
            <ChevronLeft className="w-4 h-4 mr-1" />
            返回
          </Button>

          <div className="flex items-center gap-3">
            <div className="relative">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-amber-500 to-amber-700 flex items-center justify-center shadow-lg shadow-amber-500/20">
                <Crown className="w-4 h-4 text-amber-100" />
              </div>
              <div className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
            </div>
            <div>
              <h1 className="text-sm font-semibold text-amber-100">PM</h1>
              <p className="text-[10px] text-amber-500/70 uppercase tracking-wider">PM Console</p>
            </div>
          </div>
        </div>

        {/* 中央进度指示器 + 当前状态 */}
        <div className="flex items-center gap-4">
          {/* 实时任务统计 - 动画数字 */}
          <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-white/5 border border-white/10">
            <Clock className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs text-slate-400">待办:</span>
            <span className="text-xs font-mono text-slate-300 min-w-[20px] text-center">
              {taskStats.pending}
            </span>
            <span className="text-slate-600">|</span>
            <Zap className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-xs text-amber-400 font-medium min-w-[20px] text-center">
              {taskStats.running}
            </span>
            <span className="text-slate-600">|</span>
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-xs text-emerald-400 font-medium min-w-[20px] text-center">
              {taskStats.completed}
            </span>
            {taskStats.blocked > 0 && (
              <>
                <span className="text-slate-600">|</span>
                <AlertCircle className="w-3.5 h-3.5 text-red-400" />
                <span className="text-xs text-red-400 font-medium min-w-[20px] text-center">
                  {taskStats.blocked}
                </span>
              </>
            )}
          </div>

          {/* 当前阶段状态指示 */}
          {pmRunning && (
            <div className={cn(
              "flex items-center gap-2 px-3 py-1.5 rounded-lg border transition-all duration-300",
              currentPhaseConfig.color.replace('text-', 'bg-').replace('400', '500/20'),
              currentPhaseConfig.color
            )}>
              {currentPhaseConfig.icon}
              <span className="text-xs font-medium">{currentPhaseConfig.label}</span>
            </div>
          )}
          
          {/* 任务进度条 */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10">
            <ScrollText className="w-4 h-4 text-amber-500/70" />
            <span className="text-xs text-slate-400">进度</span>
            <span className="text-xs font-mono text-amber-400">
              {completedTasks}/{totalTasks}
            </span>
            <div className="w-20 h-1.5 rounded-full bg-slate-800 overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-amber-500 to-amber-400 transition-all duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="text-xs font-mono text-slate-500">{progress}%</span>
          </div>
          
          {/* 当前任务指示 - 带脉冲动画 */}
          {currentTask && pmRunning && (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/20 max-w-[250px] animate-pulse">
              <Zap className="w-3.5 h-3.5 text-amber-400 flex-shrink-0 animate-pulse" />
              <span className="text-xs text-amber-300 truncate" title={currentTask.title}>
                正在执行: {currentTask.title}
              </span>
            </div>
          )}
        </div>

        {/* 右侧控制 */}
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setShowDiagnostics(true)}
            className="text-slate-400 hover:text-amber-400 hover:bg-amber-500/10"
            title="运行诊断"
          >
            <Stethoscope className="w-4 h-4" />
          </Button>

          <div className="w-px h-6 bg-white/10" />

          <Button
            variant="ghost"
            size="sm"
            onClick={() => { void handleRunPmOnce(); }}
            data-testid="pm-workspace-run-once"
            disabled={pmRunOnceDisabled}
            title={pmRunOnceDisabledReason || undefined}
            className="text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 border border-amber-500/20"
          >
            {pmStarting || pmStopping || runOnceStatusEvidence.loading ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5 mr-1.5" />}
            单次 Run
          </Button>

          <Button
            variant={pmRunning ? 'default' : 'outline'}
            size="sm"
            onClick={() => { void handleTogglePm(); }}
            data-testid="pm-workspace-toggle"
            disabled={pmToggleDisabled}
            title={pmToggleDisabledReason || undefined}
            className={cn(
              pmRunning
                ? 'bg-amber-600 hover:bg-amber-700 text-white'
                : 'border-amber-500/30 text-amber-400 hover:bg-amber-500/10'
              )}
          >
            {pmStarting || pmStopping || toggleStatusEvidence.loading ? (
              <>
                <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                {pmStopping ? '停止中' : pmStarting ? '启动中' : '确认中'}
              </>
            ) : pmRunning ? (
              <>
                <div className="w-1.5 h-1.5 rounded-full bg-white animate-pulse mr-2" />
                运行中
              </>
            ) : (
              <>
                <CheckCircle2 className="w-3.5 h-3.5 mr-1.5" />
                启动
              </>
            )}
          </Button>

          <div className="w-px h-6 bg-white/10 mx-2" />

          <Button
            variant="ghost"
            size="icon"
            onClick={() => setShowAIDialogue(!showAIDialogue)}
            className={cn(
              'text-slate-400 hover:text-slate-100',
              showAIDialogue && 'text-amber-400 bg-amber-500/10'
            )}
          >
            <MessageSquare className="w-4 h-4" />
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={onOpenSettings}
            disabled={!onOpenSettings}
            className="text-slate-400 hover:text-slate-100"
            title="系统配置"
          >
            <Settings className="w-4 h-4" />
          </Button>
        </div>
      </header>
      )}

      {!factoryMode && (
        <PMBackendEvidenceStrip
          evidence={pmBackendEvidence}
          cacheClearing={pmKernelCacheClearing}
          cacheClearStatus={pmKernelCacheClearStatus}
          onRefresh={() => { void loadPmBackendEvidence(); }}
          onClearCache={() => { void handleClearPmKernelCache(); }}
          workspace={workspace}
        />
      )}

      {runOnceStatusEvidence.triggered && (
        <section
          className="border-b border-white/10 bg-slate-950/45 px-4 py-1.5 text-xs text-slate-300"
          data-testid="pm-run-once-status-evidence"
        >
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center gap-2 outline-none">
              <Clock className="h-3.5 w-3.5 shrink-0 text-slate-500" />
              <span className="font-medium text-slate-200">PM 运行快照</span>
              {runOnceStatusEvidence.data ? (
                <span className={cn(
                  'rounded-full border px-2 py-0.5 text-[10px]',
                  runOnceStatusEvidence.data.running
                    ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
                    : 'border-slate-500/25 bg-slate-500/10 text-slate-300',
                )}>
                  {runOnceStatusEvidence.data.running ? 'running' : 'idle'}
                </span>
              ) : null}
              <span className="min-w-0 truncate text-slate-500">
                {runOnceStatusEvidence.loading
                  ? '正在同步后端状态...'
                  : runOnceStatusEvidence.error || runOnceStatusEvidence.data?.workspace || '等待状态'}
              </span>
              <span className="ml-auto text-[10px] text-slate-500 group-open:hidden">详情</span>
              <span className="ml-auto hidden text-[10px] text-slate-500 group-open:inline">收起</span>
            </summary>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-white/10 bg-slate-950/55 px-2 py-1.5">
              <span className="font-medium text-amber-100">PM run_once status</span>
              <EvidenceEndpointBadge endpoint="/v2/pm/status" testId="pm-run-once-status-endpoint" />
            {runOnceStatusEvidence.loading ? (
              <span className="text-slate-400">正在读取状态快照...</span>
            ) : runOnceStatusEvidence.error ? (
              <span className="text-rose-300">{runOnceStatusEvidence.error}</span>
            ) : runOnceStatusEvidence.data ? (
              <>
                <span className={cn(
                  'rounded border px-1.5 py-0.5 text-[10px]',
                  runOnceStatusEvidence.data.running
                    ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
                    : 'border-slate-500/25 bg-slate-500/10 text-slate-300',
                )}>
                  {runOnceStatusEvidence.data.running ? 'running' : 'idle'}
                </span>
                <span className="text-slate-400">
                  pid={runOnceStatusEvidence.data.pid ?? 'none'}
                </span>
                {runOnceStatusEvidence.data.mode ? (
                  <span className="text-slate-400">mode={runOnceStatusEvidence.data.mode}</span>
                ) : null}
                {runOnceStatusEvidence.data.source ? (
                  <span className="text-slate-400">source={runOnceStatusEvidence.data.source}</span>
                ) : null}
                {runOnceStatusEvidence.data.workspace ? (
                  <span className="max-w-[260px] truncate text-slate-400" title={runOnceStatusEvidence.data.workspace}>
                    workspace={runOnceStatusEvidence.data.workspace}
                  </span>
                ) : null}
              </>
            ) : null}
            </div>
          </details>
        </section>
      )}

      {toggleStatusEvidence.triggered && (
        <section
          className="border-b border-white/10 bg-slate-950/45 px-4 py-1.5 text-xs text-slate-300"
          data-testid="pm-toggle-status-evidence"
        >
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center gap-2 outline-none">
              <Activity className="h-3.5 w-3.5 shrink-0 text-slate-500" />
              <span className="font-medium text-slate-200">PM 启停快照</span>
              {toggleStatusEvidence.data ? (
                <span className={cn(
                  'rounded-full border px-2 py-0.5 text-[10px]',
                  toggleStatusEvidence.data.running
                    ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
                    : 'border-slate-500/25 bg-slate-500/10 text-slate-300',
                )}>
                  {toggleStatusEvidence.data.running ? 'running' : 'idle'}
                </span>
              ) : null}
              <span className="min-w-0 truncate text-slate-500">
                {toggleStatusEvidence.loading
                  ? '正在同步后端状态...'
                  : toggleStatusEvidence.error || toggleStatusEvidence.data?.workspace || '等待状态'}
              </span>
              <span className="ml-auto text-[10px] text-slate-500 group-open:hidden">详情</span>
              <span className="ml-auto hidden text-[10px] text-slate-500 group-open:inline">收起</span>
            </summary>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-white/10 bg-slate-950/55 px-2 py-1.5">
              <span className="font-medium text-amber-100">PM toggle status</span>
              <EvidenceEndpointBadge endpoint="/v2/pm/status" testId="pm-toggle-status-endpoint" />
            {toggleStatusEvidence.loading ? (
              <span className="text-slate-400">正在读取状态快照...</span>
            ) : toggleStatusEvidence.error ? (
              <span className="text-rose-300">{toggleStatusEvidence.error}</span>
            ) : toggleStatusEvidence.data ? (
              <>
                <span className={cn(
                  'rounded border px-1.5 py-0.5 text-[10px]',
                  toggleStatusEvidence.data.running
                    ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
                    : 'border-slate-500/25 bg-slate-500/10 text-slate-300',
                )}>
                  {toggleStatusEvidence.data.running ? 'running' : 'idle'}
                </span>
                <span className="text-slate-400">
                  pid={toggleStatusEvidence.data.pid ?? 'none'}
                </span>
                {toggleStatusEvidence.data.mode ? (
                  <span className="text-slate-400">mode={toggleStatusEvidence.data.mode}</span>
                ) : null}
                {toggleStatusEvidence.data.source ? (
                  <span className="text-slate-400">source={toggleStatusEvidence.data.source}</span>
                ) : null}
                {toggleStatusEvidence.data.workspace ? (
                  <span className="max-w-[260px] truncate text-slate-400" title={toggleStatusEvidence.data.workspace}>
                    workspace={toggleStatusEvidence.data.workspace}
                  </span>
                ) : null}
              </>
            ) : null}
            </div>
          </details>
        </section>
      )}

      {(backendPmTaskError || backendPmTasks.length > 0) && (
        <section
          className="border-b border-white/10 bg-slate-950/45 px-4 py-1.5 text-xs text-slate-300"
          data-testid="pm-task-backend-evidence"
        >
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center gap-2 outline-none">
              <ListTodo className="h-3.5 w-3.5 shrink-0 text-slate-500" />
              <span className="font-medium text-slate-200">任务合同来源</span>
              <span className={cn(
                'rounded-full border px-2 py-0.5 text-[10px]',
                backendPmTaskError
                  ? 'border-rose-500/25 bg-rose-500/10 text-rose-200'
                  : 'border-slate-500/25 bg-slate-500/10 text-slate-300',
              )}>
                {isLoadingBackendPmTasks ? '读取中' : backendPmTaskError ? '读取失败' : `backend ${backendPmTasks.length}`}
              </span>
              <span className="min-w-0 truncate text-slate-500">
                runtime {tasks.length} · merged {pmTaskEvidenceRows.length}
              </span>
              <span className="ml-auto text-[10px] text-slate-500 group-open:hidden">详情</span>
              <span className="ml-auto hidden text-[10px] text-slate-500 group-open:inline">收起</span>
            </summary>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-white/10 bg-slate-950/55 px-2 py-1.5">
              <span className="font-medium text-amber-100">PM task list evidence</span>
              <EvidenceEndpointBadge endpoint="/v2/pm/tasks" testId="pm-task-list-evidence-endpoint" />
            {isLoadingBackendPmTasks ? (
              <span className="text-slate-400">正在读取任务合同...</span>
            ) : backendPmTaskError ? (
              <span className="text-rose-300">{backendPmTaskError}</span>
            ) : (
              <>
                <span className="text-slate-400">backend={backendPmTasks.length}</span>
                <span className="text-slate-400">runtime={tasks.length}</span>
                <span className="text-slate-400">merged={pmTaskEvidenceRows.length}</span>
              </>
            )}
            </div>
          </details>
        </section>
      )}

      {pmRuntimeBanner && (
        <div
          data-testid="pm-runtime-terminal-banner"
          className={cn(
            "mx-4 mt-3 rounded-lg border px-3 py-2.5 text-sm shadow-lg",
            pmRuntimeBanner.severity === 'error'
              ? "border-red-500/30 bg-red-950/40 text-red-100"
              : "border-amber-500/30 bg-amber-950/35 text-amber-100",
          )}
        >
          <div className="flex items-start gap-2">
            <AlertCircle className={cn(
              "mt-0.5 size-4 shrink-0",
              pmRuntimeBanner.severity === 'error' ? "text-red-300" : "text-amber-300",
            )} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{pmRuntimeBanner.title}</span>
                {pmRuntimeBanner.code ? (
                  <span
                    data-testid="pm-runtime-error-code"
                    className="rounded border border-red-400/25 bg-red-500/10 px-1.5 py-0.5 font-mono text-[10px] text-red-100/85"
                  >
                    {pmRuntimeBanner.code}
                  </span>
                ) : null}
              </div>
              {pmRuntimeBanner.rootCause ? (
                <div
                  data-testid="pm-runtime-root-cause"
                  className="mt-2 rounded-md border border-red-400/20 bg-red-500/10 px-2.5 py-2 text-xs"
                >
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-red-200/75">
                    根因 · {pmRuntimeBanner.rootCause.role}
                  </div>
                  <div className="whitespace-pre-line text-red-50/90">{pmRuntimeBanner.rootCause.detail}</div>
                </div>
              ) : (
                <div className="mt-1 whitespace-pre-line text-xs opacity-85">{pmRuntimeBanner.detail}</div>
              )}
              {pmRuntimeBanner.cascades && pmRuntimeBanner.cascades.length > 0 ? (
                <div
                  data-testid="pm-runtime-cascade"
                  className="mt-2 rounded-md border border-amber-300/15 bg-amber-500/10 px-2.5 py-2 text-xs text-amber-50/85"
                >
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-amber-200/75">
                    级联阻断
                  </div>
                  <div className="space-y-1">
                    {pmRuntimeBanner.cascades.map((item) => (
                      <div key={`${item.role}-${item.detail}`} className="grid gap-2 sm:grid-cols-[112px_minmax(0,1fr)]">
                        <span className="font-medium text-amber-100">{item.role}</span>
                        <span className="min-w-0 break-words">{item.detail}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              {pmRuntimeBanner.startBlocker ? (
                <div
                  data-testid="pm-runtime-start-blocker"
                  className="mt-2 rounded-md border border-white/10 bg-slate-950/35 px-2 py-1.5 text-[11px] text-slate-300"
                >
                  当前启动门禁: {pmRuntimeBanner.startBlocker}
                </div>
              ) : null}
              {pmRuntimeBanner.refs.length > 0 && (
                <div className="mt-1.5 space-y-0.5 font-mono text-[10px] opacity-65">
                  {pmRuntimeBanner.refs.map((ref) => (
                    <div key={ref} className="truncate" title={ref}>{ref}</div>
                  ))}
                </div>
              )}
            </div>
            {pmStartBlocked && onOpenSettings && (
              <Button
                variant="outline"
                size="sm"
                onClick={onOpenSettings}
                className="shrink-0 border-amber-400/30 text-amber-100 hover:bg-amber-500/10"
              >
                <Settings className="mr-1.5 size-3.5" />
                LLM 设置
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Sidebar - Navigation */}
        <nav className="w-14 flex flex-col items-center py-4 gap-2 border-r border-white/5 bg-slate-950/50">
          <NavButton
            icon={<ListTodo className="w-4 h-4" />}
            label="任务"
            active={activeView === 'tasks'}
            onClick={() => handleViewChange('tasks')}
          />
          <NavButton
            icon={<Activity className="w-4 h-4" />}
            label="实时"
            active={activeView === 'activity'}
            onClick={() => handleViewChange('activity')}
          />
          <NavButton
            icon={<FileText className="w-4 h-4" />}
            label="文档"
            active={activeView === 'documents'}
            onClick={() => handleViewChange('documents')}
          />
          <NavButton
            icon={<ScrollText className="w-4 h-4" />}
            label="需求"
            active={activeView === 'requirements'}
            onClick={() => handleViewChange('requirements')}
          />
          <NavButton
            icon={<History className="w-4 h-4" />}
            label="历史"
            active={activeView === 'history'}
            onClick={() => handleViewChange('history')}
          />
          <NavButton
            icon={<BarChart3 className="w-4 h-4" />}
            label="统计"
            active={activeView === 'analytics'}
            onClick={() => handleViewChange('analytics')}
          />
          <NavButton
            icon={<GitBranch className="w-4 h-4" />}
            label="编排"
            active={activeView === 'workbench'}
            onClick={() => handleViewChange('workbench')}
          />
        </nav>

        {/* Main Panel */}
        <PanelGroup direction="horizontal" className="flex-1">
          <Panel defaultSize={shouldShowSideAIDialogue ? 65 : 85} minSize={40}>
            <div className="h-full overflow-hidden">
              {activeView === 'tasks' && (
                <div className="flex h-full min-h-0 flex-col">
                  {qualityGate ? (
                    <div className="shrink-0 border-b border-white/10 bg-slate-950/35 p-3">
                      <QualityGateCard data={qualityGate} className="rounded-lg" />
                    </div>
                  ) : null}
                  <div className="min-h-0 flex-1">
                    <PMTaskPanel
                      tasks={pmTaskEvidenceRows}
                      selectedTaskId={selectedTaskId}
                      onTaskSelect={handleTaskSelect}
                      onTaskCreated={handleBackendPmTaskCreated}
                      pmRunning={pmRunning}
                      taskTraceMap={taskTraceMap}
                      workspace={workspace}
                    />
                  </div>
                </div>
              )}
              {activeView === 'activity' && (
                <RealtimeActivityPanel
                  executionLogs={executionLogs}
                  llmStreamEvents={llmStreamEvents}
                  processStreamEvents={processStreamEvents}
                  currentPhase={currentPhase}
                  isRunning={pmRunning}
                  role="pm"
                />
              )}
              {activeView === 'documents' && (
                <PMDocumentPanel
                  workspace={workspace}
                  selectedPath={selectedDocumentPath}
                  onDocumentSelect={handleDocumentSelect}
                />
              )}
              {activeView === 'requirements' && (
                <PMRequirementsPanel workspace={workspace} />
              )}
              {activeView === 'history' && (
                <PMHistoryPanel pmState={pmState} workspace={workspace} />
              )}
              {activeView === 'analytics' && (
                <PMAnalyticsPanel tasks={pmTaskEvidenceRows} />
              )}
              {activeView === 'workbench' && (
                <PMWorkbenchPanel
                  pmRunning={pmRunning}
                  workspace={workspace}
                  taskCount={totalTasks}
                  hostKind="electron_workbench"
                  attachmentMode="isolated"
                />
              )}
            </div>
          </Panel>

          {shouldShowSideAIDialogue && (
            <>
              <PanelResizeHandle className="w-1 bg-white/5 hover:bg-amber-500/30 transition-colors" />
              <Panel defaultSize={35} minSize={25} maxSize={50}>
                <PMAIDialoguePanel
                  pmRunning={pmRunning}
                  workspace={workspace}
                  taskCount={totalTasks}
                  selectedTaskId={selectedTaskId}
                  interactionBlockedReason={pmRuntimeBanner?.detail || pmStartBlockedReason}
                />
              </Panel>
            </>
          )}
        </PanelGroup>
      </div>

      {/* Status Bar */}
      <PMStatusBar
        pmRunning={pmRunning}
        taskCount={totalTasks}
        completedCount={completedTasks}
        iteration={pmState?.pm_iteration as number | undefined}
      />

      {/* Diagnostics Panel */}
      <PMDiagnosticsPanel
        isOpen={showDiagnostics}
        onClose={() => setShowDiagnostics(false)}
        workspace={workspace}
      />
    </div>
  );
}

// Navigation Button Component
interface NavButtonProps {
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  onClick: () => void;
}

function NavButton({ icon, label, active, onClick }: NavButtonProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-10 h-10 rounded-xl flex flex-col items-center justify-center gap-0.5 transition-all duration-200',
        active
          ? 'bg-amber-500/15 text-amber-400 shadow-lg shadow-amber-500/10'
          : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
      )}
      title={label}
    >
      {icon}
      <span className="text-[8px] font-medium">{label}</span>
    </button>
  );
}

function requirementRecord(requirement: PmRequirementEntry): Record<string, unknown> {
  return requirement as Record<string, unknown>;
}

function requirementMetadata(requirement: PmRequirementEntry): Record<string, unknown> {
  const metadata = requirementRecord(requirement).metadata;
  return metadata && typeof metadata === 'object' && !Array.isArray(metadata)
    ? metadata as Record<string, unknown>
    : {};
}

function readRequirementValue(requirement: PmRequirementEntry, keys: string[]): unknown {
  const record = requirementRecord(requirement);
  const metadata = requirementMetadata(requirement);
  for (const key of keys) {
    const directValue = record[key];
    if (directValue !== undefined && directValue !== null) return directValue;
    const metadataValue = metadata[key];
    if (metadataValue !== undefined && metadataValue !== null) return metadataValue;
  }
  return undefined;
}

function readRequirementString(requirement: PmRequirementEntry, keys: string[]): string {
  const value = readRequirementValue(requirement, keys);
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'boolean') return String(value);
  return '';
}

function requirementStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    const token = typeof value === 'string' ? value.trim() : '';
    return token ? [token] : [];
  }

  return value
    .map((item) => {
      if (typeof item === 'string') return item.trim();
      if (item && typeof item === 'object') {
        const record = item as Record<string, unknown>;
        return String(record.description || record.title || record.name || record.path || record.id || '').trim();
      }
      return String(item || '').trim();
    })
    .filter(Boolean);
}

function requirementApiId(requirement: PmRequirementEntry): string {
  return readRequirementString(requirement, ['id', 'req_id', 'requirement_id']);
}

function requirementRowKey(requirement: PmRequirementEntry, index: number): string {
  return requirementApiId(requirement) || readRequirementString(requirement, ['title', 'subject', 'name']) || `requirement-${index}`;
}

function requirementTitle(requirement: PmRequirementEntry): string {
  return readRequirementString(requirement, ['title', 'subject', 'name']) || requirementApiId(requirement) || 'Untitled requirement';
}

function requirementStatus(requirement: PmRequirementEntry): string {
  return readRequirementString(requirement, ['status', 'state']) || 'unknown';
}

function requirementPriority(requirement: PmRequirementEntry): string {
  return readRequirementString(requirement, ['priority']) || 'unset';
}

function requirementSource(requirement: PmRequirementEntry): string {
  return readRequirementString(requirement, ['source_doc', 'sourceDoc', 'source', 'path']);
}

function PMRequirementsPanel({ workspace }: { workspace: string }) {
  const [requirements, setRequirements] = useState<PmRequirementEntry[]>([]);
  const [selectedRequirementId, setSelectedRequirementId] = useState<string | null>(null);
  const [selectedRequirement, setSelectedRequirement] = useState<PmRequirementEntry | null>(null);
  const [isLoadingRequirements, setIsLoadingRequirements] = useState(false);
  const [requirementsError, setRequirementsError] = useState('');
  const [isLoadingRequirementDetail, setIsLoadingRequirementDetail] = useState(false);
  const [requirementDetailError, setRequirementDetailError] = useState('');

  const loadRequirements = useCallback(async () => {
    setIsLoadingRequirements(true);
    setRequirementsError('');
    try {
      const result = await listPmRequirements({ limit: 100, offset: 0, workspace });
      if (!result.ok || !result.data) {
        throw new Error(result.error || 'PM requirements unavailable');
      }

      const rows = Array.isArray(result.data.requirements)
        ? result.data.requirements
        : Array.isArray(result.data.items)
          ? result.data.items
          : [];
      setRequirements(rows);

      const firstRequirementId = rows.map(requirementApiId).find(Boolean) || null;
      setSelectedRequirementId((currentId) => {
        if (currentId && rows.some((requirement) => requirementApiId(requirement) === currentId)) {
          return currentId;
        }
        return firstRequirementId;
      });
      if (!firstRequirementId) {
        setSelectedRequirement(null);
      }
    } catch (error) {
      setRequirements([]);
      setSelectedRequirement(null);
      setSelectedRequirementId(null);
      setRequirementsError(error instanceof Error ? error.message : 'PM requirements unavailable');
    } finally {
      setIsLoadingRequirements(false);
    }
  }, [workspace]);

  useEffect(() => {
    void loadRequirements();
  }, [loadRequirements]);

  useEffect(() => {
    if (!selectedRequirementId) {
      setSelectedRequirement(null);
      setRequirementDetailError('');
      setIsLoadingRequirementDetail(false);
      return;
    }

    let cancelled = false;
    setIsLoadingRequirementDetail(true);
    setRequirementDetailError('');
    void getPmRequirement(selectedRequirementId, workspace)
      .then((result) => {
        if (cancelled) return;
        if (!result.ok || !result.data) {
          throw new Error(result.error || 'PM requirement detail unavailable');
        }
        setSelectedRequirement(result.data);
      })
      .catch((error) => {
        if (cancelled) return;
        setSelectedRequirement(null);
        setRequirementDetailError(error instanceof Error ? error.message : 'PM requirement detail unavailable');
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingRequirementDetail(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [selectedRequirementId, workspace]);

  const selectedListRequirement = useMemo(
    () => requirements.find((requirement) => requirementApiId(requirement) === selectedRequirementId) || null,
    [requirements, selectedRequirementId],
  );
  const detailRequirement = selectedRequirement || selectedListRequirement;
  const acceptanceCriteria = detailRequirement
    ? requirementStringList(readRequirementValue(detailRequirement, ['acceptance_criteria', 'acceptanceCriteria', 'criteria']))
    : [];
  const relatedTasks = detailRequirement
    ? requirementStringList(readRequirementValue(detailRequirement, ['related_task_ids', 'relatedTaskIds', 'task_ids', 'tasks']))
    : [];
  const source = detailRequirement ? requirementSource(detailRequirement) : '';
  const detailEndpoint = selectedRequirementId ? `/v2/pm/requirements/${selectedRequirementId}` : '/v2/pm/requirements/{id}';

  return (
    <div data-testid="pm-requirements-panel" className="flex h-full min-h-0 flex-col p-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">需求追踪</h2>
          <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
            <span>来自 PM 需求合同接口</span>
            <EvidenceEndpointBadge endpoint="/v2/pm/requirements" testId="pm-requirements-endpoint" />
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => { void loadRequirements(); }}
          disabled={isLoadingRequirements}
          data-testid="pm-requirements-refresh"
          className="h-8 px-2 text-xs text-slate-400 hover:bg-white/5 hover:text-slate-100"
        >
          <RefreshCw className={cn('mr-1.5 h-3.5 w-3.5', isLoadingRequirements && 'animate-spin')} />
          刷新
        </Button>
      </div>

      {requirementsError ? (
        <div data-testid="pm-requirements-error" className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-200">
          {requirementsError}
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-3 overflow-hidden xl:grid-cols-[minmax(260px,0.42fr)_minmax(0,0.58fr)]">
        <section className="min-h-0 rounded-lg border border-white/10 bg-white/5">
          <div className="flex h-10 items-center justify-between border-b border-white/10 px-3 text-xs text-slate-400">
            <EvidenceEndpointBadge endpoint="/v2/pm/requirements" testId="pm-requirements-list-endpoint" />
            <span data-testid="pm-requirements-count" className="rounded border border-white/10 bg-slate-950/40 px-1.5 py-0.5 text-[10px] text-slate-300">
              {requirements.length}
            </span>
          </div>
          <div data-testid="pm-requirements-list" className="max-h-full space-y-1 overflow-auto p-2">
            {isLoadingRequirements && requirements.length === 0 ? (
              <div className="flex items-center gap-2 px-2 py-4 text-xs text-slate-500">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                加载需求...
              </div>
            ) : requirements.length === 0 ? (
              <div className="px-2 py-4 text-xs text-slate-500">暂无需求合同</div>
            ) : (
              requirements.slice(0, 100).map((requirement, index) => {
                const apiId = requirementApiId(requirement);
                const sourcePath = requirementSource(requirement);
                return (
                  <button
                    key={requirementRowKey(requirement, index)}
                    type="button"
                    data-testid="pm-requirement-row"
                    onClick={() => apiId && setSelectedRequirementId(apiId)}
                    disabled={!apiId}
                    className={cn(
                      'w-full rounded-md border px-2 py-2 text-left text-xs transition-colors',
                      apiId && apiId === selectedRequirementId
                        ? 'border-amber-400/30 bg-amber-500/10 text-slate-100'
                        : 'border-white/5 bg-slate-950/35 text-slate-300 hover:border-white/15 hover:bg-white/5',
                      !apiId && 'cursor-not-allowed opacity-60',
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate font-medium">{requirementTitle(requirement)}</span>
                      <span className="shrink-0 rounded bg-slate-900/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                        {apiId || 'no-id'}
                      </span>
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-[10px] text-slate-500">
                      <span className="rounded bg-cyan-500/10 px-1.5 py-0.5 text-cyan-200">{requirementStatus(requirement)}</span>
                      <span className="rounded bg-purple-500/10 px-1.5 py-0.5 text-purple-200">P:{requirementPriority(requirement)}</span>
                    </div>
                    {sourcePath ? <div className="mt-1 truncate font-mono text-[10px] text-slate-500">{sourcePath}</div> : null}
                  </button>
                );
              })
            )}
          </div>
        </section>

        <section className="min-h-0 overflow-hidden rounded-lg border border-white/10 bg-white/5">
          <div className="flex h-10 items-center justify-between border-b border-white/10 px-3 text-xs text-slate-400">
            <EvidenceEndpointBadge endpoint={detailEndpoint} testId="pm-requirement-detail-endpoint" />
            {isLoadingRequirementDetail ? <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-500" /> : null}
          </div>
          {requirementDetailError ? (
            <div data-testid="pm-requirement-detail-error" className="m-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              {requirementDetailError}
            </div>
          ) : null}
          {detailRequirement ? (
            <div data-testid="pm-requirement-detail" className="max-h-full space-y-3 overflow-auto p-3 text-xs text-slate-300">
              <div className="rounded-md border border-white/5 bg-slate-950/35 px-2 py-1.5">
                <EvidenceEndpointBadge endpoint={detailEndpoint} testId="pm-requirement-detail-body-endpoint" />
              </div>
              <div>
                <div className="text-[10px] uppercase text-slate-500">Title</div>
                <div className="mt-1 break-words text-base font-semibold text-slate-100">{requirementTitle(detailRequirement)}</div>
              </div>
              <div className="grid gap-2 sm:grid-cols-3">
                <div className="rounded-md border border-white/5 bg-slate-950/35 p-2">
                  <div className="text-[10px] uppercase text-slate-500">Status</div>
                  <div className="mt-1 text-slate-100">{requirementStatus(detailRequirement)}</div>
                </div>
                <div className="rounded-md border border-white/5 bg-slate-950/35 p-2">
                  <div className="text-[10px] uppercase text-slate-500">Priority</div>
                  <div className="mt-1 text-slate-100">{requirementPriority(detailRequirement)}</div>
                </div>
                <div className="rounded-md border border-white/5 bg-slate-950/35 p-2">
                  <div className="text-[10px] uppercase text-slate-500">Source</div>
                  <div className="mt-1 break-words font-mono text-[11px] text-slate-300">{source || 'unlinked'}</div>
                </div>
              </div>
              {readRequirementString(detailRequirement, ['description', 'summary']) ? (
                <div>
                  <div className="text-[10px] uppercase text-slate-500">Description</div>
                  <p className="mt-1 whitespace-pre-wrap break-words leading-relaxed text-slate-300">
                    {readRequirementString(detailRequirement, ['description', 'summary'])}
                  </p>
                </div>
              ) : null}
              <div className="grid gap-3 lg:grid-cols-2">
                <div className="rounded-md border border-white/5 bg-slate-950/35 p-2">
                  <div className="mb-1 text-[10px] uppercase text-slate-500">Acceptance Criteria</div>
                  {acceptanceCriteria.length > 0 ? (
                    <ul className="space-y-1">
                      {acceptanceCriteria.map((criterion, index) => (
                        <li key={`${criterion}-${index}`} className="break-words rounded bg-white/5 px-2 py-1 text-slate-300">
                          {criterion}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div className="text-slate-500">未记录验收条件</div>
                  )}
                </div>
                <div className="rounded-md border border-white/5 bg-slate-950/35 p-2">
                  <div className="mb-1 text-[10px] uppercase text-slate-500">Related Tasks</div>
                  {relatedTasks.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {relatedTasks.map((taskId, index) => (
                        <span key={`${taskId}-${index}`} className="rounded bg-amber-500/10 px-1.5 py-0.5 font-mono text-[11px] text-amber-200">
                          {taskId}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="text-slate-500">未关联 PM 任务</div>
                  )}
                </div>
              </div>
              <details className="rounded-md border border-white/5 bg-slate-950/35">
                <summary className="cursor-pointer px-2 py-1.5 text-[11px] text-slate-400">Raw requirement payload</summary>
                <pre className="max-h-52 overflow-auto border-t border-white/5 p-2 font-mono text-[11px] text-slate-400">
                  {JSON.stringify(detailRequirement, null, 2)}
                </pre>
              </details>
            </div>
          ) : (
            <div data-testid="pm-requirement-detail" className="flex h-full items-center justify-center px-3 text-sm text-slate-500">
              选择需求后查看详情
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function historyValue(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value.trim() : '';
}

function historyEntryId(entry: PmTaskHistoryEntry): string {
  return historyValue(entry.task_id) || historyValue(entry.id) || historyValue(entry.title) || 'history';
}

function historyEntryAction(entry: PmTaskHistoryEntry): string {
  return historyValue(entry.action) || historyValue(entry.status) || historyValue(entry.type) || 'event';
}

function historyEntryTime(entry: PmTaskHistoryEntry): string {
  return historyValue(entry.updated_at) || historyValue(entry.created_at) || historyValue(entry.timestamp);
}

function directorIterationTaskCount(iteration: PmDirectorHistoryIteration): number {
  return Array.isArray(iteration.tasks) ? iteration.tasks.length : 0;
}

function PMHistoryPanel({ pmState, workspace }: { pmState: Record<string, unknown> | null; workspace: string }) {
  const [taskHistory, setTaskHistory] = useState<PmTaskHistoryEntry[]>([]);
  const [directorIterations, setDirectorIterations] = useState<PmDirectorHistoryIteration[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState('');

  const loadHistory = useCallback(async () => {
    setIsLoadingHistory(true);
    setHistoryError('');
    try {
      const [taskResult, directorResult] = await Promise.all([
        listPmTaskHistory({ limit: 50, offset: 0, workspace }),
        listPmDirectorTaskHistory({ limit: 25, offset: 0, workspace }),
      ]);
      if (!taskResult.ok || !taskResult.data) {
        throw new Error(taskResult.error || 'PM 任务历史加载失败');
      }
      if (!directorResult.ok || !directorResult.data) {
        throw new Error(directorResult.error || 'Director 分发历史加载失败');
      }

      setTaskHistory(Array.isArray(taskResult.data.history) ? taskResult.data.history : []);
      setDirectorIterations(Array.isArray(directorResult.data.iterations) ? directorResult.data.iterations : []);
    } catch (error) {
      setTaskHistory([]);
      setDirectorIterations([]);
      setHistoryError(error instanceof Error ? error.message : 'PM 历史加载失败');
    } finally {
      setIsLoadingHistory(false);
    }
  }, [workspace]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  return (
    <div className="h-full flex flex-col p-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">执行历史</h2>
          <p className="text-xs text-slate-500">来自 PM 任务历史与 Director 分发历史接口</p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => { void loadHistory(); }}
          disabled={isLoadingHistory}
          data-testid="pm-history-refresh"
          className="h-8 px-2 text-xs text-slate-400 hover:bg-white/5 hover:text-slate-100"
        >
          <RefreshCw className={cn('mr-1.5 h-3.5 w-3.5', isLoadingHistory && 'animate-spin')} />
          刷新
        </Button>
      </div>

      {historyError ? (
        <div data-testid="pm-history-error" className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-200">
          {historyError}
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-3 overflow-hidden lg:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)]">
        <section className="min-h-0 rounded-lg border border-white/10 bg-white/5">
          <div className="flex h-10 items-center justify-between border-b border-white/10 px-3 text-xs text-slate-400">
            <span>PM Task History</span>
            <span data-testid="pm-history-task-count" className="rounded border border-white/10 bg-slate-950/40 px-1.5 py-0.5 text-[10px] text-slate-300">
              {taskHistory.length}
            </span>
          </div>
          <div data-testid="pm-history-task-list" className="max-h-full space-y-1 overflow-auto p-2">
            {isLoadingHistory && taskHistory.length === 0 ? (
              <div className="flex items-center gap-2 px-2 py-4 text-xs text-slate-500">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                加载任务历史...
              </div>
            ) : taskHistory.length === 0 ? (
              <div className="px-2 py-4 text-xs text-slate-500">暂无任务历史</div>
            ) : (
              taskHistory.slice(0, 50).map((entry, index) => {
                const key = historyValue(entry.id) || `${historyEntryId(entry)}-${index}`;
                const time = historyEntryTime(entry);
                return (
                  <div key={key} data-testid="pm-history-task-row" className="rounded-md border border-white/5 bg-slate-950/35 px-2 py-1.5 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate font-mono text-slate-200">{historyEntryId(entry)}</span>
                      <span className="shrink-0 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200">
                        {historyEntryAction(entry)}
                      </span>
                    </div>
                    {time ? <div className="mt-1 truncate text-[10px] text-slate-500">{time}</div> : null}
                  </div>
                );
              })
            )}
          </div>
        </section>

        <section className="min-h-0 rounded-lg border border-white/10 bg-white/5">
          <div className="flex h-10 items-center justify-between border-b border-white/10 px-3 text-xs text-slate-400">
            <span>Director Dispatch</span>
            <span data-testid="pm-history-director-count" className="rounded border border-white/10 bg-slate-950/40 px-1.5 py-0.5 text-[10px] text-slate-300">
              {directorIterations.length}
            </span>
          </div>
          <div data-testid="pm-history-director-list" className="max-h-full space-y-1 overflow-auto p-2">
            {isLoadingHistory && directorIterations.length === 0 ? (
              <div className="flex items-center gap-2 px-2 py-4 text-xs text-slate-500">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                加载分发历史...
              </div>
            ) : directorIterations.length === 0 ? (
              <div className="px-2 py-4 text-xs text-slate-500">暂无 Director 分发历史</div>
            ) : (
              directorIterations.slice(0, 25).map((iteration, index) => {
                const iterationId = typeof iteration.iteration === 'number' ? iteration.iteration : index + 1;
                return (
                  <div key={`${iterationId}-${index}`} data-testid="pm-history-director-row" className="rounded-md border border-white/5 bg-slate-950/35 px-2 py-1.5 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-slate-200">Iteration {iterationId}</span>
                      <span className="rounded bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-200">
                        {directorIterationTaskCount(iteration)} tasks
                      </span>
                    </div>
                    {historyValue(iteration.updated_at) || historyValue(iteration.created_at) ? (
                      <div className="mt-1 truncate text-[10px] text-slate-500">
                        {historyValue(iteration.updated_at) || historyValue(iteration.created_at)}
                      </div>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>
        </section>
      </div>

      <details className="mt-3 shrink-0 rounded-lg border border-white/10 bg-slate-950/35">
        <summary className="cursor-pointer px-3 py-2 text-xs text-slate-400">PM 状态快照</summary>
        {pmState ? (
          <pre data-testid="pm-history-state-snapshot" className="max-h-40 overflow-auto border-t border-white/10 p-3 font-mono text-xs text-slate-400">
            {JSON.stringify(pmState, null, 2)}
          </pre>
        ) : (
          <div className="border-t border-white/10 px-3 py-4 text-xs text-slate-500">
            暂无 PM 状态快照
          </div>
        )}
      </details>
    </div>
  );
}

function PMAnalyticsPanel({ tasks }: { tasks: PmTask[] }) {
  const statusCounts = tasks.reduce((acc, task) => {
    const status = task.status || 'unknown';
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  return (
    <div className="h-full flex flex-col p-6">
      <h2 className="text-lg font-semibold text-slate-100 mb-4">任务统计</h2>
      {tasks.length > 0 ? (
        <div className="grid grid-cols-2 gap-4">
          {Object.entries(statusCounts).map(([status, count]) => (
            <div
              key={status}
              className="rounded-xl border border-white/10 bg-white/5 p-4"
            >
              <p className="text-xs uppercase text-slate-500">{status}</p>
              <p className="text-2xl font-bold text-amber-400">{count}</p>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center rounded-xl border border-white/10 bg-white/5 text-sm text-slate-500">
          暂无任务数据，统计面板不会使用示例数据。
        </div>
      )}
    </div>
  );
}
