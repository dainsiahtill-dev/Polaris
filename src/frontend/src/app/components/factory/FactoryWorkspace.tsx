/** FactoryWorkspace - 无人值守开发工厂工作区 */
import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertCircle,
  BadgeCheck,
  Brain,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  FileCode,
  FileText,
  Hammer,
  Layers,
  Loader2,
  PackageCheck,
  Pause,
  Play,
  RotateCcw,
  Route,
  ShieldCheck,
  Square,
  Terminal,
  XCircle,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import type { FileEditEvent } from '@/app/hooks/useRuntime';
import type { FactoryAuditEvent, FactoryRunArtifact, FactoryRunStatus } from '@/hooks/useFactory';
import type { LogEntry } from '@/types/log';
import type { PmTask } from '@/types/task';

interface FactoryWorkspaceProps {
  workspace: string;
  onBackToMain: () => void;
  tasks: PmTask[];
  pmTasks?: PmTask[];
  directorTasks?: PmTask[];
  executionLogs?: LogEntry[];
  llmStreamEvents?: LogEntry[];
  processStreamEvents?: LogEntry[];
  fileEditEvents?: FileEditEvent[];
  currentRun?: FactoryRunStatus | null;
  events?: FactoryAuditEvent[];
  artifacts?: FactoryRunArtifact[];
  summaryMd?: string | null;
  summaryJson?: Record<string, unknown> | null;
  artifactsError?: string | null;
  isArtifactsLoading?: boolean;
  onStart?: () => void;
  onCancel?: () => void;
  onPause?: () => void;
  onResume?: () => void;
  onRetryCheckpoint?: () => void;
  isLoading?: boolean;
}

type FactoryPhase = 'idle' | 'planning' | 'executing' | 'verifying' | 'completed' | 'failed' | 'cancelled';
type FactoryRoleLayer = 'pm' | 'chief_engineer' | 'director';
type RunRoleStatus = FactoryRunStatus['roles'][string];
const CANCELLED_RUN_STATUSES = new Set(['cancelled', 'canceled']);
const FAILED_RUN_STATUSES = new Set(['failed', 'error', 'blocked', 'timeout']);
const TERMINAL_RUN_STATUSES = new Set(['completed', ...CANCELLED_RUN_STATUSES, ...FAILED_RUN_STATUSES]);

interface RoleLayerView {
  id: FactoryRoleLayer;
  order: string;
  title: string;
  subtitle: string;
  status: string;
  progress: number;
  metric: string;
  detail: string;
  icon: React.ReactNode;
  tone: {
    idle: string;
    active: string;
    text: string;
    progress: string;
  };
}

interface BlueprintEvidenceView {
  id: string;
  taskId: string;
  title: string;
  path: string;
  summary: string;
  source: 'task' | 'artifact';
}

interface ChiefEngineerReviewArtifactView {
  id: string;
  title: string;
  path: string;
  summary: string;
  source: 'artifact';
}

interface BlueprintCoverageSummary {
  required: number;
  covered: number;
  completed: number;
  missing: PmTask[];
}

interface FactoryContractStats {
  total: number;
  pending: number;
  running: number;
  blocked: number;
  completed: number;
  withGoal: number;
  withScope: number;
  withSteps: number;
  withAcceptance: number;
}

interface FactoryDeliveryStats {
  total: number;
  ready: number;
  running: number;
  blocked: number;
  completed: number;
  claimed: number;
}

interface FactorySourceEvidenceRow {
  label: string;
  value: string;
  tone: string;
}

interface FactoryFailureBrief {
  rootRole: string;
  headline: string;
  detail: string;
  cascades: string[];
  code: string;
  recoverable: boolean;
}

const PHASE_CONFIG: Record<FactoryPhase, { label: string; color: string; icon: React.ReactNode }> = {
  idle: { label: '等待启动', color: 'text-slate-400', icon: <Hammer className="h-4 w-4" /> },
  planning: { label: '规划中', color: 'text-amber-300', icon: <ClipboardList className="h-4 w-4" /> },
  executing: { label: '执行中', color: 'text-indigo-300', icon: <Terminal className="h-4 w-4" /> },
  verifying: { label: '验证中', color: 'text-cyan-300', icon: <CheckCircle2 className="h-4 w-4" /> },
  completed: { label: '已完成', color: 'text-emerald-300', icon: <CheckCircle2 className="h-4 w-4" /> },
  failed: { label: '失败', color: 'text-red-300', icon: <AlertCircle className="h-4 w-4" /> },
  cancelled: { label: '已取消', color: 'text-orange-300', icon: <XCircle className="h-4 w-4" /> },
};

const ROLE_LAYER_LABELS: Record<FactoryRoleLayer, { short: string; route: string }> = {
  pm: { short: 'PM', route: '任务合同' },
  chief_engineer: { short: 'CE', route: '技术蓝图' },
  director: { short: 'DIR', route: '执行交付' },
};

function normalizeToken(value: string | null | undefined): string {
  return String(value || '').trim().toLowerCase();
}

function mapRunToFactoryPhase(run?: FactoryRunStatus | null): FactoryPhase {
  const status = normalizeToken(run?.status);
  const phase = normalizeToken(run?.phase);
  const stage = normalizeToken(run?.current_stage);

  if (CANCELLED_RUN_STATUSES.has(status) || CANCELLED_RUN_STATUSES.has(phase)) return 'cancelled';
  if (FAILED_RUN_STATUSES.has(status) || FAILED_RUN_STATUSES.has(phase)) return 'failed';
  if (phase === 'completed' || status === 'completed') return 'completed';
  if (['verification', 'qa_gate', 'handover', 'quality_gate'].includes(phase) || stage.includes('quality')) {
    return 'verifying';
  }
  if (phase === 'implementation' || stage.includes('director')) return 'executing';
  if (stage.includes('chief') || stage.includes('blueprint')) return 'planning';
  if (['architect', 'planning', 'pending', 'intake', 'docs_check'].includes(phase) || stage.includes('pm')) {
    return 'planning';
  }
  return 'idle';
}

function mapRunToWorkspacePhase(run?: FactoryRunStatus | null): string {
  const phase = mapRunToFactoryPhase(run);
  if (phase === 'planning') return 'planning';
  if (phase === 'executing') return 'executing';
  if (phase === 'verifying') return 'verification';
  if (phase === 'completed') return 'completed';
  if (phase === 'failed' || phase === 'cancelled') return 'error';
  return 'idle';
}

function preferredRoleLayer(run?: FactoryRunStatus | null): FactoryRoleLayer {
  const stage = normalizeToken(run?.current_stage);
  const phase = mapRunToFactoryPhase(run);
  if (stage.includes('director') || phase === 'executing' || phase === 'verifying') return 'director';
  if (stage.includes('chief') || stage.includes('blueprint') || stage.includes('architect')) return 'chief_engineer';
  return 'pm';
}

function toEventLevel(event: FactoryAuditEvent): LogEntry['level'] {
  const type = normalizeToken(event.type);
  const resultStatus = normalizeToken(String((event.result as Record<string, unknown> | undefined)?.status || ''));

  if (CANCELLED_RUN_STATUSES.has(type)) return 'warning';
  if (FAILED_RUN_STATUSES.has(type)) return 'error';
  if (type === 'stage_started') return 'exec';
  if (type === 'stage_completed' && resultStatus === 'failed') return 'error';
  if (type === 'stage_completed' && resultStatus === 'success') return 'success';
  if (type === 'completed') return 'success';
  return 'info';
}

function toActivityLogs(events: FactoryAuditEvent[]): LogEntry[] {
  return events.map((event, index) => {
    const message = String(event.message || event.type || 'Factory event').trim();
    const tags = [event.stage, event.type].filter((value): value is string => Boolean(value));
    return {
      id: String(event.event_id || `${event.type}-${index}`),
      timestamp: String(event.timestamp || new Date().toISOString()),
      level: toEventLevel(event),
      source: 'FACTORY',
      title: event.stage ? `阶段: ${event.stage}` : 'Factory 事件',
      message,
      details: event.result ? JSON.stringify(event.result, null, 2) : undefined,
      tags,
    };
  });
}

function formatBytes(size?: number): string {
  if (typeof size !== 'number' || Number.isNaN(size) || size < 0) {
    return 'size n/a';
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function formatSummaryValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return 'n/a';
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return JSON.stringify(value);
}

function toSummaryRows(summaryJson?: Record<string, unknown> | null): Array<[string, string]> {
  if (!summaryJson) {
    return [];
  }
  return Object.entries(summaryJson)
    .slice(0, 5)
    .map(([key, value]) => [key, formatSummaryValue(value)]);
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function runMetadata(run?: FactoryRunStatus | null): Record<string, unknown> {
  return recordValue(run?.metadata);
}

function metadataString(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  }
  return '';
}

function buildRunSourceEvidence(run?: FactoryRunStatus | null): FactorySourceEvidenceRow[] {
  const metadata = runMetadata(run);
  const startRequest = recordValue(metadata.factory_start_request);
  const rows: FactorySourceEvidenceRow[] = [];
  const sessionId = metadataString(metadata, ['export_session_id', 'session_id']);
  const bundlePath = metadataString(metadata, ['export_bundle_path', 'bundle_path']);
  const directive = metadataString(metadata, ['directive']) || metadataString(startRequest, ['directive']);
  const inputSource = metadataString(startRequest, ['input_source']) || metadataString(metadata, ['input_source']);
  const startFrom = metadataString(startRequest, ['start_from']);

  if (sessionId) {
    rows.push({ label: '会话', value: sessionId, tone: 'text-cyan-200' });
  }
  if (bundlePath) {
    rows.push({ label: '证据包', value: bundlePath, tone: 'text-emerald-200' });
  }
  if (directive) {
    rows.push({ label: '指令', value: directive, tone: 'text-slate-200' });
  }
  if (inputSource || startFrom) {
    rows.push({
      label: '入口',
      value: [inputSource, startFrom].filter(Boolean).join(' / '),
      tone: 'text-amber-200',
    });
  }
  return rows;
}

function gateTone(gate: FactoryRunStatus['gates'][number]): string {
  const status = normalizeToken(gate.status);
  if (gate.passed || status === 'passed' || status === 'success') {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300';
  }
  if (status === 'failed' || status === 'error') {
    return 'border-red-500/30 bg-red-500/10 text-red-300';
  }
  if (status === 'running' || status === 'pending') {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
  }
  return 'border-slate-500/30 bg-slate-500/10 text-slate-300';
}

function roleStatusTone(status: string): string {
  const token = normalizeToken(status);
  if (['completed', 'ready', 'success', 'passed'].includes(token)) {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
  }
  if (['running', 'active', 'in_progress'].includes(token)) {
    return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200';
  }
  if (['failed', 'error', 'blocked'].includes(token)) {
    return 'border-red-500/30 bg-red-500/10 text-red-200';
  }
  if (['pending', 'waiting'].includes(token)) {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-200';
  }
  return 'border-slate-500/30 bg-slate-500/10 text-slate-300';
}

function roleStatusLabel(status: string): string {
  const token = normalizeToken(status);
  if (['completed', 'complete', 'ready', 'success', 'passed'].includes(token)) return '已就绪';
  if (['running', 'active', 'in_progress'].includes(token)) return '运行中';
  if (['failed', 'error'].includes(token)) return '失败';
  if (token === 'blocked') return '阻塞';
  if (token === 'waiting') return '等待';
  if (token === 'pending') return '待处理';
  if (token === 'cancelled' || token === 'canceled') return '已取消';
  return token || '空闲';
}

function taskStatusToken(task: PmTask): string {
  return normalizeToken(String(task.status || task.state || ''));
}

function isTaskDone(task: PmTask): boolean {
  const status = taskStatusToken(task);
  return task.done || task.completed === true || status === 'completed' || status === 'success' || status === 'done';
}

function isTaskRunning(task: PmTask): boolean {
  const status = taskStatusToken(task);
  return status === 'running' || status === 'in_progress' || status === 'active';
}

function taskRecord(task: PmTask): Record<string, unknown> {
  return task as unknown as Record<string, unknown>;
}

function taskMetadata(task: PmTask): Record<string, unknown> {
  return task.metadata && typeof task.metadata === 'object' ? task.metadata : {};
}

function readTaskString(task: PmTask, keys: string[]): string {
  const direct = taskRecord(task);
  const metadata = taskMetadata(task);
  for (const key of keys) {
    const directValue = direct[key];
    if (typeof directValue === 'string' && directValue.trim()) return directValue.trim();
    const metadataValue = metadata[key];
    if (typeof metadataValue === 'string' && metadataValue.trim()) return metadataValue.trim();
  }
  return '';
}

function stringifyListItem(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    for (const key of ['description', 'title', 'goal', 'path', 'file', 'name']) {
      const item = record[key];
      if (typeof item === 'string' && item.trim()) return item.trim();
    }
  }
  return '';
}

function readTaskStringList(task: PmTask, keys: string[]): string[] {
  const direct = taskRecord(task);
  const metadata = taskMetadata(task);
  const rows: string[] = [];
  const seen = new Set<string>();

  for (const key of keys) {
    const candidates = [direct[key], metadata[key]];
    for (const candidate of candidates) {
      const values = Array.isArray(candidate)
        ? candidate
        : typeof candidate === 'string' && candidate.trim()
          ? candidate.split(/\r?\n|;/)
          : [];
      for (const value of values) {
        const text = stringifyListItem(value);
        const token = normalizeToken(text);
        if (!text || seen.has(token)) continue;
        seen.add(token);
        rows.push(text);
      }
    }
  }

  return rows;
}

function taskTitle(task: PmTask): string {
  return task.title || task.subject || readTaskString(task, ['title', 'subject']) || task.id || '未命名任务';
}

function taskGoal(task: PmTask): string {
  return readTaskString(task, ['goal', 'summary', 'description']) || '等待 PM 补齐目标与验收上下文';
}

function taskScopeItems(task: PmTask): string[] {
  return readTaskStringList(task, ['scope_paths', 'target_files', 'files', 'file_paths']);
}

function taskStepItems(task: PmTask): string[] {
  return readTaskStringList(task, ['execution_checklist', 'execution_steps', 'steps', 'checklist']);
}

function taskAcceptanceItems(task: PmTask): string[] {
  return readTaskStringList(task, ['acceptance', 'acceptance_criteria', 'acceptanceCriteria', 'qa_contract']);
}

function isTaskBlocked(task: PmTask): boolean {
  const status = taskStatusToken(task);
  return status === 'blocked' || status === 'failed' || Boolean(task.error);
}

function isTaskPending(task: PmTask): boolean {
  const status = taskStatusToken(task);
  return !isTaskDone(task) && !isTaskRunning(task) && !isTaskBlocked(task)
    && (status === '' || status === 'pending' || status === 'idle' || status === 'todo');
}

function taskDisplayStatus(task: PmTask): string {
  if (isTaskDone(task)) return '完成';
  if (isTaskRunning(task)) return '执行中';
  if (isTaskBlocked(task)) return '阻塞';
  return '待办';
}

function taskStatusTone(task: PmTask): string {
  if (isTaskDone(task)) return 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200';
  if (isTaskRunning(task)) return 'border-cyan-500/25 bg-cyan-500/10 text-cyan-200';
  if (isTaskBlocked(task)) return 'border-red-500/25 bg-red-500/10 text-red-200';
  return 'border-amber-500/25 bg-amber-500/10 text-amber-200';
}

function taskPriorityLabel(task: PmTask): string {
  const metadata = taskMetadata(task);
  const value = metadata.priority ?? task.priority;
  if (typeof value === 'number' && Number.isFinite(value)) return `P${value}`;
  const text = String(value || '').trim();
  return text || 'P-';
}

function buildContractStats(tasks: PmTask[]): FactoryContractStats {
  return tasks.reduce<FactoryContractStats>((stats, task) => {
    stats.total += 1;
    if (isTaskDone(task)) stats.completed += 1;
    else if (isTaskRunning(task)) stats.running += 1;
    else if (isTaskBlocked(task)) stats.blocked += 1;
    else stats.pending += 1;

    if (readTaskString(task, ['goal', 'summary', 'description'])) stats.withGoal += 1;
    if (taskScopeItems(task).length > 0) stats.withScope += 1;
    if (taskStepItems(task).length > 0) stats.withSteps += 1;
    if (taskAcceptanceItems(task).length > 0) stats.withAcceptance += 1;
    return stats;
  }, {
    total: 0,
    pending: 0,
    running: 0,
    blocked: 0,
    completed: 0,
    withGoal: 0,
    withScope: 0,
    withSteps: 0,
    withAcceptance: 0,
  });
}

function contractCompleteness(stats: FactoryContractStats): number {
  if (stats.total === 0) return 0;
  return percent(((stats.withGoal + stats.withScope + stats.withSteps + stats.withAcceptance) / (stats.total * 4)) * 100);
}

function buildDeliveryStats(tasks: PmTask[]): FactoryDeliveryStats {
  return tasks.reduce<FactoryDeliveryStats>((stats, task) => {
    stats.total += 1;
    if (isTaskDone(task)) stats.completed += 1;
    else if (isTaskRunning(task)) stats.running += 1;
    else if (isTaskBlocked(task)) stats.blocked += 1;
    else stats.ready += 1;

    const claimed = Boolean(
      task.assigned_to || task.assignedTo || task.assignee || task.assigned_worker || task.worker_id
        || readTaskString(task, ['assigned_to', 'assigned_worker', 'worker_id'])
    );
    if (claimed || isTaskRunning(task) || isTaskDone(task)) stats.claimed += 1;
    return stats;
  }, {
    total: 0,
    ready: 0,
    running: 0,
    blocked: 0,
    completed: 0,
    claimed: 0,
  });
}

function latestLogRows(logs: LogEntry[], limit: number): LogEntry[] {
  const toTimestamp = (value: unknown): number => {
    const timestamp = Date.parse(String(value || ''));
    return Number.isFinite(timestamp) ? timestamp : 0;
  };
  return [...logs]
    .filter((entry) => Boolean(String(entry.message || entry.title || '').trim()))
    .sort((a, b) => toTimestamp(b.timestamp) - toTimestamp(a.timestamp))
    .slice(0, limit);
}

function hasBlueprintEvidence(task: PmTask): boolean {
  return Boolean(readTaskString(task, ['blueprint_id', 'blueprint_path', 'runtime_blueprint_path']));
}

function taskDisplayText(task: PmTask): string {
  return String(taskTitle(task) || task.goal || task.summary || task.description || task.id || '').trim();
}

function taskIdentityTokens(task: PmTask): string[] {
  const metadata = taskMetadata(task);
  const direct = taskRecord(task);
  const values = [
    task.id,
    direct.task_id,
    direct.pm_task_id,
    direct.taskId,
    direct.subject,
    metadata.task_id,
    metadata.pm_task_id,
    metadata.taskId,
    metadata.id,
    metadata.subject,
  ];
  const seen = new Set<string>();
  const tokens: string[] = [];
  for (const value of values) {
    const token = normalizeToken(String(value || ''));
    if (!token || seen.has(token)) continue;
    seen.add(token);
    tokens.push(token);
  }
  return tokens;
}

function mergeFactoryTaskPools(...pools: PmTask[][]): PmTask[] {
  const byKey = new Map<string, PmTask>();
  for (const pool of pools) {
    for (const task of pool) {
      const tokens = taskIdentityTokens(task).map(canonicalFactoryTaskId).filter(Boolean);
      const key = tokens[0] || canonicalFactoryTaskId(task.id);
      if (!key) continue;
      const current = byKey.get(key);
      if (!current || (!taskDisplayText(current) && taskDisplayText(task))) {
        byKey.set(key, task);
      }
      for (const token of tokens) {
        if (!byKey.has(token)) {
          byKey.set(token, byKey.get(key) || task);
        }
      }
    }
  }
  return Array.from(new Set(byKey.values()));
}

function canonicalFactoryTaskId(value: unknown): string {
  const text = String(value || '').trim();
  if (!text) return '';
  const normalized = normalizeToken(text);
  const numericAlias = normalized.match(/^(?:task|pm-task|pm)[-_]?(\d+)$/);
  return numericAlias ? numericAlias[1] : normalized;
}

function isChiefEngineerArtifact(artifact: FactoryRunArtifact): boolean {
  const path = normalizeToken(artifact.path);
  const name = normalizeToken(artifact.name);
  return (
    path.includes('runtime/blueprints/')
    || (name.includes('blueprint') && !name.includes('review'))
  );
}

function isChiefEngineerReviewArtifact(artifact: FactoryRunArtifact): boolean {
  const path = normalizeToken(artifact.path);
  const name = normalizeToken(artifact.name);
  return path.includes('runtime/state/blueprints/') || name.includes('.review') || name.includes('review');
}

function isLatestReviewArtifact(artifact: FactoryRunArtifact): boolean {
  return normalizeToken(artifact.name || artifact.path).includes('latest.review');
}

function reviewArtifactGroupKey(artifact: FactoryRunArtifact): string {
  const path = String(artifact.path || '').trim();
  const name = String(artifact.name || basename(path)).trim();
  const token = `${path}/${name}`;
  const runMatch = token.match(/factory[_-][a-z0-9_-]+/i);
  return normalizeToken(runMatch?.[0] || path || name);
}

function reviewArtifactRank(artifact: FactoryRunArtifact): number {
  const path = normalizeToken(artifact.path);
  if (path.includes('runtime/state/blueprints/')) return 0;
  if (path.includes('workspace/blueprints/') && !path.includes('latest.review')) return 1;
  if (path.includes('workspace/roles/chief_engineer/')) return 2;
  if (path.includes('latest.review')) return 9;
  return 5;
}

function basename(path: string): string {
  const normalized = String(path || '').replace(/\\/g, '/').trim();
  return normalized.split('/').filter(Boolean).pop() || normalized || 'blueprint';
}

function workspaceLabel(workspace: string): string {
  const normalized = String(workspace || '').replace(/\\/g, '/').trim();
  if (!normalized) return '未设置工作区';
  return normalized.split('/').filter(Boolean).pop() || normalized;
}

function artifactTaskIdFromName(value: string): string {
  const base = basename(value).replace(/\.[^.]+$/, '').trim();
  if (!base) return '';
  const normalized = base.toLowerCase();
  for (const prefix of ['ce_', 'ce-', 'blueprint_', 'blueprint-', 'chief_engineer_', 'chief-engineer-']) {
    if (normalized.startsWith(prefix)) {
      return base.slice(prefix.length).trim();
    }
  }
  return '';
}

function artifactTaskId(artifact: FactoryRunArtifact): string {
  const record = artifact as unknown as Record<string, unknown>;
  for (const key of ['task_id', 'pm_task_id', 'taskId']) {
    const value = String(record[key] || '').trim();
    if (value) return value;
  }
  return artifactTaskIdFromName(artifact.name) || artifactTaskIdFromName(artifact.path);
}

function buildBlueprintEvidence(tasks: PmTask[], artifacts: FactoryRunArtifact[]): BlueprintEvidenceView[] {
  const rows: BlueprintEvidenceView[] = [];
  const seen = new Set<string>();
  const tasksByCanonicalId = new Map<string, PmTask>();

  for (const task of tasks) {
    for (const token of taskIdentityTokens(task)) {
      const canonical = canonicalFactoryTaskId(token);
      if (canonical && !tasksByCanonicalId.has(canonical)) {
        tasksByCanonicalId.set(canonical, task);
      }
    }
  }

  for (const task of tasks) {
    if (!hasBlueprintEvidence(task)) continue;
    const blueprintId = readTaskString(task, ['blueprint_id']) || task.id;
    const path = readTaskString(task, ['blueprint_path', 'runtime_blueprint_path']) || blueprintId;
    const key = path || blueprintId;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      id: blueprintId,
      taskId: String(task.id || '').trim(),
      title: taskDisplayText(task) || blueprintId,
      path,
      summary: task.summary || task.goal || task.description || '任务合同携带的 Chief Engineer 蓝图字段',
      source: 'task',
    });
  }

  for (const artifact of artifacts.filter(isChiefEngineerArtifact)) {
    const path = String(artifact.path || '').trim();
    const name = String(artifact.name || basename(path)).trim();
    const key = path || name;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const taskId = artifactTaskId(artifact);
    const matchedTask = tasksByCanonicalId.get(canonicalFactoryTaskId(taskId));
    rows.push({
      id: name,
      taskId,
      title: matchedTask ? taskDisplayText(matchedTask) || taskId || name : taskId || name,
      path: path || name,
      summary: matchedTask?.summary || matchedTask?.goal || matchedTask?.description || '',
      source: 'artifact',
    });
  }

  return rows;
}

function buildChiefEngineerReviewArtifacts(artifacts: FactoryRunArtifact[]): ChiefEngineerReviewArtifactView[] {
  const byKey = new Map<string, { rank: number; row: ChiefEngineerReviewArtifactView }>();
  const reviewArtifacts = artifacts.filter(isChiefEngineerReviewArtifact);
  const hasSpecificReview = reviewArtifacts.some((artifact) => !isLatestReviewArtifact(artifact));

  for (const artifact of reviewArtifacts) {
    if (hasSpecificReview && isLatestReviewArtifact(artifact)) continue;
    const path = String(artifact.path || '').trim();
    const name = String(artifact.name || basename(path)).trim();
    const key = reviewArtifactGroupKey(artifact);
    if (!key) continue;
    const rank = reviewArtifactRank(artifact);
    const current = byKey.get(key);
    if (current && current.rank <= rank) continue;
    byKey.set(key, {
      rank,
      row: {
        id: name,
        title: name,
        path: path || name,
        summary: 'Factory Chief Engineer review summary artifact',
        source: 'artifact',
      },
    });
  }

  return Array.from(byKey.values()).map((entry) => entry.row);
}

function buildBlueprintCoverage(tasks: PmTask[], blueprintEvidence: BlueprintEvidenceView[]): BlueprintCoverageSummary {
  const evidenceTaskIds = new Set(blueprintEvidence.map((item) => canonicalFactoryTaskId(item.taskId)).filter(Boolean));
  const byTaskKey = new Map<string, { task: PmTask; covered: boolean; completed: boolean }>();

  for (const task of tasks) {
    const tokens = taskIdentityTokens(task);
    const key = tokens[0] || normalizeToken(task.id);
    if (!key) continue;

    const covered = hasBlueprintEvidence(task) || tokens.some((token) => evidenceTaskIds.has(canonicalFactoryTaskId(token)));
    const completed = isTaskDone(task);
    const existing = byTaskKey.get(key);
    if (!existing) {
      byTaskKey.set(key, { task, covered, completed });
      continue;
    }

    existing.covered = existing.covered || covered;
    existing.completed = existing.completed && completed;
    if (!existing.covered && covered) {
      existing.task = task;
    }
  }

  const rows = Array.from(byTaskKey.values());
  const activeRows = rows.filter((row) => !row.completed);
  const missing = activeRows.filter((row) => !row.covered).map((row) => row.task);

  return {
    required: activeRows.length,
    covered: activeRows.length - missing.length,
    completed: rows.length - activeRows.length,
    missing,
  };
}

function getRunRole(roles: FactoryRunStatus['roles'] | undefined, keys: string[]): RunRoleStatus | null {
  if (!roles) return null;
  const normalizedKeys = keys.map(normalizeToken);
  for (const key of keys) {
    const direct = roles[key];
    if (direct) return direct;
  }
  for (const [key, role] of Object.entries(roles)) {
    const normalizedKey = normalizeToken(key);
    const normalizedRoleName = normalizeToken(role.role);
    if (normalizedKeys.includes(normalizedKey) || normalizedKeys.includes(normalizedRoleName)) {
      return role;
    }
  }
  return null;
}

function roleDetail(role: RunRoleStatus | null): string {
  return String(role?.detail || role?.current_task || '').trim();
}

function isFailedRole(role: RunRoleStatus | null): boolean {
  return ['failed', 'error', 'blocked', 'timeout'].includes(normalizeToken(role?.status));
}

function buildFactoryFailureBrief(run?: FactoryRunStatus | null): FactoryFailureBrief | null {
  const status = normalizeToken(run?.status);
  if (!run?.failure && !FAILED_RUN_STATUSES.has(status)) {
    return null;
  }

  const pmRole = getRunRole(run?.roles, ['pm']);
  const chiefRole = getRunRole(run?.roles, ['chief_engineer', 'chiefengineer', 'architect']);
  const directorRole = getRunRole(run?.roles, ['director']);
  const qaRole = getRunRole(run?.roles, ['qa']);
  const failureDetail = String(run?.failure?.detail || '').trim();
  const pmDetail = roleDetail(pmRole);
  const chiefDetail = roleDetail(chiefRole);
  const directorDetail = roleDetail(directorRole);
  const qaDetail = roleDetail(qaRole);
  const combined = [failureDetail, pmDetail, chiefDetail, directorDetail, qaDetail].join(' ').toLowerCase();

  if (isFailedRole(pmRole) || combined.includes('pm iteration failed')) {
    return {
      rootRole: 'PM',
      headline: 'PM 阶段失败',
      detail: pmDetail || failureDetail || 'PM iteration failed',
      cascades: [chiefDetail, directorDetail, qaDetail].filter((item) => item && item !== pmDetail),
      code: run?.failure?.code || 'PM_ITERATION_FAILED',
      recoverable: Boolean(run?.failure?.recoverable),
    };
  }

  if (isFailedRole(chiefRole) || combined.includes('chief')) {
    return {
      rootRole: 'Chief Engineer',
      headline: 'Chief Engineer 蓝图层阻塞',
      detail: chiefDetail || failureDetail || 'Chief Engineer handoff blocked',
      cascades: [directorDetail, qaDetail].filter(Boolean),
      code: run?.failure?.code || 'CHIEF_ENGINEER_BLOCKED',
      recoverable: Boolean(run?.failure?.recoverable),
    };
  }

  if (isFailedRole(directorRole) || combined.includes('director')) {
    return {
      rootRole: 'Director',
      headline: 'Director 执行层失败',
      detail: directorDetail || failureDetail || 'Director execution failed',
      cascades: [qaDetail].filter(Boolean),
      code: run?.failure?.code || 'DIRECTOR_FAILED',
      recoverable: Boolean(run?.failure?.recoverable),
    };
  }

  return {
    rootRole: 'Factory',
    headline: 'Factory 运行失败',
    detail: failureDetail || '运行已进入失败态',
    cascades: [pmDetail, chiefDetail, directorDetail, qaDetail].filter(Boolean),
    code: run?.failure?.code || status || 'FACTORY_FAILED',
    recoverable: Boolean(run?.failure?.recoverable),
  };
}

function percent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function buildRoleLayers({
  currentRun,
  pmTasks,
  directorTasks,
  blueprintEvidenceCount,
  blueprintCoverage,
}: {
  currentRun: FactoryRunStatus | null;
  pmTasks: PmTask[];
  directorTasks: PmTask[];
  blueprintEvidenceCount: number;
  blueprintCoverage: BlueprintCoverageSummary;
}): RoleLayerView[] {
  const pmRole = getRunRole(currentRun?.roles, ['pm']);
  const chiefRole = getRunRole(currentRun?.roles, ['chief_engineer', 'chiefengineer', 'architect']);
  const directorRole = getRunRole(currentRun?.roles, ['director']);

  const completedPmTasks = pmTasks.filter(isTaskDone).length;
  const runningDirectorTasks = directorTasks.filter(isTaskRunning).length;
  const completedDirectorTasks = directorTasks.filter(isTaskDone).length;
  const pmProgress = pmRole ? percent(pmRole.progress) : pmTasks.length > 0 ? percent((completedPmTasks / pmTasks.length) * 100) : 0;
  const chiefProgress = chiefRole
    ? percent(chiefRole.progress)
    : blueprintCoverage.required > 0
      ? percent((blueprintCoverage.covered / blueprintCoverage.required) * 100)
      : blueprintEvidenceCount > 0
        ? 100
        : 0;
  const chiefFallbackStatus = blueprintCoverage.missing.length > 0
    ? 'waiting'
    : blueprintCoverage.required > 0 || blueprintEvidenceCount > 0
      ? 'ready'
      : 'waiting';
  const directorProgress = directorRole
    ? percent(directorRole.progress)
    : directorTasks.length > 0
      ? percent((completedDirectorTasks / directorTasks.length) * 100)
      : 0;

  return [
    {
      id: 'pm',
      order: '01',
      title: 'PM',
      subtitle: '任务合同层',
      status: pmRole?.status || (pmTasks.length > 0 ? 'ready' : 'idle'),
      progress: pmProgress,
      metric: `${completedPmTasks}/${pmTasks.length}`,
      detail: pmRole?.detail || pmRole?.current_task || '规划目标、范围、验收与任务拆分',
      icon: <ClipboardList className="h-4 w-4" />,
      tone: {
        idle: 'border-amber-500/20 bg-amber-500/5 hover:border-amber-400/40',
        active: 'border-amber-400/50 bg-amber-500/12 shadow-[0_0_24px_rgba(245,158,11,0.12)]',
        text: 'text-amber-100',
        progress: 'from-amber-500 to-yellow-300',
      },
    },
    {
      id: 'chief_engineer',
      order: '02',
      title: 'Chief Engineer',
      subtitle: '蓝图交接层',
      status: chiefRole?.status || chiefFallbackStatus,
      progress: chiefProgress,
      metric: blueprintCoverage.required > 0
        ? `${blueprintCoverage.covered}/${blueprintCoverage.required} 蓝图`
        : `${blueprintEvidenceCount} 条蓝图`,
      detail: chiefRole?.detail
        || chiefRole?.current_task
        || (blueprintCoverage.missing.length > 0
          ? `还有 ${blueprintCoverage.missing.length} 个任务缺少蓝图证据`
          : '审阅任务，沉淀施工蓝图与 Director 交接条件'),
      icon: <Brain className="h-4 w-4" />,
      tone: {
        idle: 'border-cyan-500/20 bg-cyan-500/5 hover:border-cyan-400/40',
        active: 'border-cyan-400/50 bg-cyan-500/12 shadow-[0_0_24px_rgba(34,211,238,0.12)]',
        text: 'text-cyan-100',
        progress: 'from-cyan-500 to-sky-300',
      },
    },
    {
      id: 'director',
      order: '03',
      title: 'Director',
      subtitle: '执行交付层',
      status: directorRole?.status || (runningDirectorTasks > 0 ? 'running' : directorTasks.length > 0 ? 'ready' : 'idle'),
      progress: directorProgress,
      metric: `${runningDirectorTasks} running`,
      detail: directorRole?.detail || directorRole?.current_task || '领取任务，执行文件变更、命令与验证',
      icon: <Hammer className="h-4 w-4" />,
      tone: {
        idle: 'border-indigo-500/20 bg-indigo-500/5 hover:border-indigo-400/40',
        active: 'border-indigo-400/50 bg-indigo-500/12 shadow-[0_0_24px_rgba(99,102,241,0.14)]',
        text: 'text-indigo-100',
        progress: 'from-indigo-500 to-violet-300',
      },
    },
  ];
}

export function FactoryWorkspace({
  workspace,
  onBackToMain,
  tasks,
  pmTasks,
  directorTasks,
  executionLogs = [],
  llmStreamEvents = [],
  processStreamEvents = [],
  fileEditEvents = [],
  currentRun = null,
  events = [],
  artifacts,
  summaryMd,
  summaryJson,
  artifactsError,
  isArtifactsLoading = false,
  onStart,
  onCancel,
  onPause,
  onResume,
  onRetryCheckpoint,
  isLoading = false,
}: FactoryWorkspaceProps) {
  const factoryPhase = mapRunToFactoryPhase(currentRun);
  const workspacePhase = mapRunToWorkspacePhase(currentRun);
  const phaseConfig = PHASE_CONFIG[factoryPhase];
  const runStatus = normalizeToken(currentRun?.status);
  const isRunActive = runStatus === 'running' || runStatus === 'recovering';
  const isRunPaused = runStatus === 'paused';
  const canStart = !currentRun || TERMINAL_RUN_STATUSES.has(runStatus);
  const canCancel = runStatus === 'running' || runStatus === 'recovering' || isRunPaused;
  const canPause = runStatus === 'running' || runStatus === 'recovering';
  const canResume = isRunPaused;
  const canRetryCheckpoint = runStatus === 'failed' || runStatus === 'blocked' || runStatus === 'timeout';

  const pmWorkflowTasks = pmTasks ?? tasks;
  const directorWorkflowTasks = directorTasks ?? tasks;
  const blueprintTaskPool = useMemo(
    () => mergeFactoryTaskPools(pmWorkflowTasks, directorWorkflowTasks),
    [directorWorkflowTasks, pmWorkflowTasks]
  );
  const activityLogs = useMemo(() => toActivityLogs(events), [events]);
  const operationsActivityLogs = useMemo(
    () => [...activityLogs, ...executionLogs],
    [activityLogs, executionLogs]
  );
  const gateResults = currentRun?.gates || [];
  const deliveryArtifacts = artifacts || currentRun?.artifacts || [];
  const blueprintEvidence = useMemo(
    () => buildBlueprintEvidence(blueprintTaskPool, deliveryArtifacts),
    [blueprintTaskPool, deliveryArtifacts]
  );
  const chiefReviewArtifacts = useMemo(
    () => buildChiefEngineerReviewArtifacts(deliveryArtifacts),
    [deliveryArtifacts]
  );
  const blueprintCoverage = useMemo(
    () => buildBlueprintCoverage(blueprintTaskPool, blueprintEvidence),
    [blueprintEvidence, blueprintTaskPool]
  );
  const summaryMarkdown = String(summaryMd ?? currentRun?.summary_md ?? '').trim();
  const summaryRows = toSummaryRows(summaryJson ?? currentRun?.summary_json ?? null);
  const artifactErrorMessage = String(artifactsError || currentRun?.artifacts_error || '').trim();
  const suggestedLayer = preferredRoleLayer(currentRun);
  const [activeLayer, setActiveLayer] = useState<FactoryRoleLayer>(() => suggestedLayer);

  useEffect(() => {
    setActiveLayer(suggestedLayer);
  }, [suggestedLayer]);

  const roleLayers = useMemo(
    () => buildRoleLayers({
      currentRun,
      pmTasks: pmWorkflowTasks,
      directorTasks: directorWorkflowTasks,
      blueprintEvidenceCount: blueprintEvidence.length,
      blueprintCoverage,
    }),
    [blueprintCoverage, blueprintEvidence.length, currentRun, directorWorkflowTasks, pmWorkflowTasks]
  );
  const activeLayerView = roleLayers.find((layer) => layer.id === activeLayer) || roleLayers[0];
  const workspaceDisplay = workspaceLabel(workspace);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#070b14] text-slate-100">
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-white/10 bg-slate-950/95 px-4">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onBackToMain}
            className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-white/10 hover:text-slate-100"
            aria-label="返回主界面"
          >
            <RotateCcw className="h-4 w-4" />
          </button>
          <div className="h-6 w-px bg-white/10" />
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-emerald-400/25 bg-emerald-500/10 text-emerald-200">
            <Hammer className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-sm font-semibold text-slate-100">Factory 模式</h1>
              <span className="rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-slate-400">
                分层视图
              </span>
            </div>
            <p data-testid="factory-workspace-label" className="truncate text-[11px] text-slate-500" title={workspace || workspaceDisplay}>
              {workspaceDisplay}
            </p>
          </div>
        </div>

        <div className="flex min-w-0 items-center justify-end gap-2">
          <div
            className={cn(
              'flex shrink-0 items-center gap-2 rounded-lg border px-3 py-1.5',
              factoryPhase === 'planning' && 'border-amber-500/30 bg-amber-500/10',
              factoryPhase === 'executing' && 'border-indigo-500/30 bg-indigo-500/10',
              factoryPhase === 'verifying' && 'border-cyan-500/30 bg-cyan-500/10',
              factoryPhase === 'completed' && 'border-emerald-500/30 bg-emerald-500/10',
              factoryPhase === 'failed' && 'border-red-500/30 bg-red-500/10',
              factoryPhase === 'cancelled' && 'border-orange-500/30 bg-orange-500/10',
              factoryPhase === 'idle' && 'border-slate-500/30 bg-slate-500/10'
            )}
          >
            {(isRunActive || isLoading) ? (
              <Loader2 className={cn('h-4 w-4 animate-spin', phaseConfig.color)} />
            ) : (
              phaseConfig.icon
            )}
            <span className={cn('text-sm font-medium', phaseConfig.color)}>{phaseConfig.label}</span>
          </div>

          <div className="hidden min-w-0 items-center gap-2 lg:flex">
            <StatusChip label="阶段" value={currentRun?.phase || 'pending'} />
            <StatusChip label="状态" value={currentRun?.status || 'idle'} />
            <StatusChip label="步骤" value={currentRun?.current_stage || 'n/a'} />
            <StatusChip label="进度" value={`${Math.round(currentRun?.progress || 0)}%`} />
          </div>

          <div className="ml-1 flex items-center gap-2">
            {canStart && onStart && (
              <Button
                size="sm"
                onClick={onStart}
                disabled={isLoading}
                className="bg-emerald-600 hover:bg-emerald-700"
              >
                {isLoading ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Play className="mr-1 h-4 w-4" />
                )}
                {isLoading ? '启动中...' : '启动'}
              </Button>
            )}
            {canPause && onPause && (
              <Button
                size="sm"
                variant="outline"
                onClick={onPause}
                disabled={isLoading}
                className="border-amber-500/25 bg-amber-500/10 text-amber-100 hover:bg-amber-500/20"
                data-testid="factory-run-pause"
              >
                <Pause className="mr-1 h-4 w-4" />
                暂停
              </Button>
            )}
            {canResume && onResume && (
              <Button
                size="sm"
                variant="outline"
                onClick={onResume}
                disabled={isLoading}
                className="border-emerald-500/25 bg-emerald-500/10 text-emerald-100 hover:bg-emerald-500/20"
                data-testid="factory-run-resume"
              >
                <Play className="mr-1 h-4 w-4" />
                恢复
              </Button>
            )}
            {canRetryCheckpoint && onRetryCheckpoint && (
              <Button
                size="sm"
                variant="outline"
                onClick={onRetryCheckpoint}
                disabled={isLoading}
                className="border-cyan-500/25 bg-cyan-500/10 text-cyan-100 hover:bg-cyan-500/20"
                data-testid="factory-run-retry-checkpoint"
              >
                <RotateCcw className="mr-1 h-4 w-4" />
                重试
              </Button>
            )}
            {canCancel && onCancel && (
              <Button size="sm" variant="destructive" onClick={onCancel} disabled={isLoading} data-testid="factory-run-cancel">
                <Square className="mr-1 h-4 w-4" />
                取消
              </Button>
            )}
          </div>
        </div>
      </header>

      <BenchStatusStrip />

      <main
        data-testid="factory-layered-layout"
        className="flex min-h-0 flex-1 flex-col overflow-hidden"
      >
        <section
          data-testid="factory-role-flow-rail"
          className="shrink-0 border-b border-white/10 bg-slate-950/80 px-4 py-3"
        >
          <RoleLayerRail
            layers={roleLayers}
            activeLayer={activeLayerView.id}
            suggestedLayer={suggestedLayer}
            onSelect={setActiveLayer}
          />
        </section>

        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)_minmax(260px,34vh)] overflow-hidden xl:grid-cols-[minmax(0,1fr)_360px] xl:grid-rows-1 2xl:grid-cols-[minmax(0,1fr)_400px]">
          <section className="h-full min-w-0 overflow-hidden" data-testid="factory-focused-layer">
            {activeLayerView.id === 'pm' && (
              <FactoryPmLayer
                tasks={pmWorkflowTasks}
                workspace={workspace}
                executionLogs={executionLogs}
                roleStatus={getRunRole(currentRun?.roles, ['pm'])}
                currentRun={currentRun}
                blueprintCoverage={blueprintCoverage}
              />
            )}
            {activeLayerView.id === 'chief_engineer' && (
              <FactoryChiefEngineerLayer
                workspace={workspace}
                blueprintEvidence={blueprintEvidence}
                reviewArtifacts={chiefReviewArtifacts}
                blueprintCoverage={blueprintCoverage}
                roleStatus={getRunRole(currentRun?.roles, ['chief_engineer', 'chiefengineer', 'architect'])}
                currentRun={currentRun}
              />
            )}
            {activeLayerView.id === 'director' && (
              <FactoryDirectorLayer
                workspace={workspace}
                tasks={directorWorkflowTasks}
                fileEditEvents={fileEditEvents}
                executionLogs={executionLogs}
                roleStatus={getRunRole(currentRun?.roles, ['director'])}
                currentRun={currentRun}
                blueprintCoverage={blueprintCoverage}
              />
            )}
          </section>

          <FactoryOperationsRail
            currentRun={currentRun}
            factoryPhase={factoryPhase}
            workspacePhase={workspacePhase}
            activeLayer={activeLayerView.id}
            activityLogs={operationsActivityLogs}
            llmStreamEvents={llmStreamEvents}
            processStreamEvents={processStreamEvents}
            gateResults={gateResults}
            deliveryArtifacts={deliveryArtifacts}
            summaryMarkdown={summaryMarkdown}
            summaryRows={summaryRows}
            artifactErrorMessage={artifactErrorMessage}
            isArtifactsLoading={isArtifactsLoading}
            isRunning={isRunActive || isLoading}
          />
        </div>
      </main>
    </div>
  );
}

function RoleLayerRail({
  layers,
  activeLayer,
  suggestedLayer,
  onSelect,
}: {
  layers: RoleLayerView[];
  activeLayer: FactoryRoleLayer;
  suggestedLayer: FactoryRoleLayer;
  onSelect: (layer: FactoryRoleLayer) => void;
}) {
  return (
    <div className="grid gap-3 xl:grid-cols-[220px_minmax(0,1fr)] xl:items-stretch">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3 xl:block">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
          <Layers className="h-3.5 w-3.5 text-emerald-300" />
          <span>角色分层</span>
        </div>
        <div className="hidden items-center gap-1.5 text-[11px] text-slate-500 md:flex xl:mt-3">
          <Route className="h-3.5 w-3.5" />
          <span>PM 任务合同</span>
          <ChevronRight className="h-3 w-3" />
          <span>CE 技术蓝图</span>
          <ChevronRight className="h-3 w-3" />
          <span>Director 执行交付</span>
        </div>
      </div>
      <div className="grid min-w-0 grid-cols-1 gap-2 md:grid-cols-3">
        {layers.map((layer, index) => {
          const label = ROLE_LAYER_LABELS[layer.id];
          const isActive = activeLayer === layer.id;
          const isSuggested = suggestedLayer === layer.id;
          return (
            <div key={layer.id} className="min-w-0">
              <button
                type="button"
                onClick={() => onSelect(layer.id)}
                data-testid={`factory-role-layer-${layer.id}`}
                aria-pressed={isActive}
                className={cn(
                  'group flex h-full min-h-[86px] w-full cursor-pointer flex-col rounded-lg border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/70',
                  isActive ? layer.tone.active : layer.tone.idle
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <div className={cn('rounded-md border border-white/10 bg-white/10 p-1.5', layer.tone.text)}>
                      {layer.icon}
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[10px] text-slate-500">{layer.order}</span>
                        <span className={cn('truncate text-sm font-semibold', layer.tone.text)}>{layer.title}</span>
                      </div>
                      <div className="truncate text-[11px] text-slate-500">{layer.subtitle}</div>
                    </div>
                  </div>
                  <span className={cn('shrink-0 rounded-md border px-1.5 py-0.5 text-[10px]', roleStatusTone(layer.status))}>
                    {roleStatusLabel(layer.status)}
                  </span>
                </div>

                <div className="mt-3 flex flex-1 items-end justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-[11px] text-slate-400">{layer.detail}</div>
                    <div className="mt-1 flex items-center gap-2 font-mono text-[11px] text-slate-500">
                      <span>{label.short}</span>
                      <span className="h-1 w-1 rounded-full bg-slate-700" />
                      <span className="truncate">{layer.metric}</span>
                    </div>
                    <div className="mt-1 truncate text-[10px] text-slate-600">
                      {label.route}
                    </div>
                  </div>
                  {isSuggested ? (
                    <span className="shrink-0 rounded-md border border-emerald-500/25 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-200">
                      当前阶段
                    </span>
                  ) : null}
                </div>

                <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-900">
                  <div
                    className={cn('h-full rounded-full bg-gradient-to-r transition-all duration-500', layer.tone.progress)}
                    style={{ width: `${layer.progress}%` }}
                  />
                </div>
              </button>
              {index < layers.length - 1 ? (
                <ChevronRight className="mx-auto my-1 h-4 w-4 rotate-90 text-slate-600 md:hidden" aria-hidden="true" />
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FactoryPmLayer({
  workspace,
  tasks,
  executionLogs,
  roleStatus,
  currentRun,
  blueprintCoverage,
}: {
  workspace: string;
  tasks: PmTask[];
  executionLogs: LogEntry[];
  roleStatus: RunRoleStatus | null;
  currentRun: FactoryRunStatus | null;
  blueprintCoverage: BlueprintCoverageSummary;
}) {
  const stats = buildContractStats(tasks);
  const completeness = contractCompleteness(stats);
  const visibleTasks = tasks.slice(0, 12);
  const recentLogs = latestLogRows(executionLogs, 5);
  const handoffReady = stats.total > 0 && stats.blocked === 0 && stats.withGoal === stats.total
    && stats.withScope === stats.total && stats.withSteps === stats.total && stats.withAcceptance === stats.total;
  const status = roleStatus?.status || (stats.total > 0 ? 'ready' : 'waiting');
  const contractGaps = [
    { label: '目标', value: stats.withGoal },
    { label: '范围', value: stats.withScope },
    { label: '步骤', value: stats.withSteps },
    { label: '验收', value: stats.withAcceptance },
  ];
  const workspaceDisplay = workspaceLabel(workspace);

  return (
    <div data-testid="factory-pm-layer" className="flex h-full flex-col overflow-hidden bg-[#070b14]">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-amber-500/20 bg-slate-950/80 px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-amber-400/30 bg-amber-500/10 text-amber-100">
            <ClipboardList className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-amber-100">PM 任务合同层</h2>
            <p className="truncate text-[10px] uppercase tracking-wider text-amber-400/70">Contract Planning Layer</p>
          </div>
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn('rounded-md border px-2 py-1 text-[10px] tracking-wider', roleStatusTone(status))}>
            {roleStatusLabel(status)}
          </span>
          <span data-testid="factory-pm-workspace-label" className="max-w-[180px] truncate rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-slate-400" title={workspace || workspaceDisplay}>
            {workspaceDisplay}
          </span>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden p-4 2xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-amber-500/15 bg-white/[0.03]">
          <div className="shrink-0 border-b border-white/10 px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold text-slate-100">PM task list evidence</h3>
                <p className="mt-1 text-xs text-slate-500">只展示 Factory 需要交接的合同字段，避免嵌入完整 PM 控制台。</p>
              </div>
              <span className="shrink-0 rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-100">
                {stats.total} 个任务
              </span>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-auto p-3">
            {visibleTasks.length > 0 ? (
              <div className="space-y-2">
                {visibleTasks.map((task) => {
                  const goal = readTaskString(task, ['goal', 'summary', 'description']);
                  const scopeItems = taskScopeItems(task);
                  const stepItems = taskStepItems(task);
                  const acceptanceItems = taskAcceptanceItems(task);
                  const checks = [
                    { label: '目标', ok: Boolean(goal) },
                    { label: '范围', ok: scopeItems.length > 0 },
                    { label: '步骤', ok: stepItems.length > 0 },
                    { label: '验收', ok: acceptanceItems.length > 0 },
                  ];

                  return (
                    <article
                      key={task.id || taskTitle(task)}
                      data-testid="factory-pm-task-item"
                      className="rounded-lg border border-white/10 bg-slate-950/45 px-3 py-2.5"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex min-w-0 items-center gap-2">
                            <span className="font-mono text-[10px] text-slate-500">{task.id || 'task'}</span>
                            <span className="truncate text-sm font-medium text-slate-100">{taskTitle(task)}</span>
                          </div>
                          <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">
                            {goal || taskGoal(task)}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-1.5">
                          <span className="rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                            {taskPriorityLabel(task)}
                          </span>
                          <span className={cn('rounded-md border px-1.5 py-0.5 text-[10px]', taskStatusTone(task))}>
                            {taskDisplayStatus(task)}
                          </span>
                        </div>
                      </div>

                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {checks.map((check) => (
                          <span
                            key={check.label}
                            className={cn(
                              'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px]',
                              check.ok
                                ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
                                : 'border-slate-700 bg-slate-900/70 text-slate-500'
                            )}
                          >
                            <CheckCircle2 className="h-3 w-3" />
                            {check.label}
                          </span>
                        ))}
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="flex h-full min-h-[260px] items-center justify-center rounded-lg border border-dashed border-white/10 bg-slate-950/35 text-center">
                <div>
                  <ClipboardList className="mx-auto h-8 w-8 text-slate-600" />
                  <p className="mt-3 text-sm text-slate-400">暂无 PM 合同任务</p>
                  <p className="mt-1 text-xs text-slate-600">等待 PM 生成可交接任务合同。</p>
                </div>
              </div>
            )}
          </div>
        </section>

        <aside className="grid min-h-0 grid-cols-1 gap-3 overflow-auto lg:grid-cols-3 2xl:flex 2xl:flex-col">
          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <Route className="h-3.5 w-3.5 text-amber-300" />
              交接门禁
            </div>
            <div className="space-y-2">
              <MetricRow label="合同完整度" value={`${completeness}%`} tone={completeness >= 80 ? 'text-emerald-200' : 'text-amber-200'} />
              <MetricRow label="待办任务" value={String(stats.pending)} tone="text-amber-200" />
              <MetricRow label="阻塞任务" value={String(stats.blocked)} tone={stats.blocked > 0 ? 'text-red-200' : 'text-emerald-200'} />
              <MetricRow label="CE 待蓝图" value={String(blueprintCoverage.missing.length)} tone={blueprintCoverage.missing.length > 0 ? 'text-amber-200' : 'text-emerald-200'} />
              <MetricRow label="Factory 阶段" value={PHASE_CONFIG[mapRunToFactoryPhase(currentRun)].label} tone="text-slate-300" />
              <MetricRow label="可交接" value={handoffReady ? '就绪' : '待补齐'} tone={handoffReady ? 'text-emerald-200' : 'text-amber-200'} />
            </div>
          </section>

          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <BadgeCheck className="h-3.5 w-3.5 text-emerald-300" />
              合同字段覆盖
            </div>
            <div className="space-y-2">
              {contractGaps.map((item) => (
                <div key={item.label}>
                  <div className="mb-1 flex justify-between text-[11px] text-slate-400">
                    <span>{item.label}</span>
                    <span>{item.value}/{stats.total}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-900">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-amber-500 to-emerald-300"
                      style={{ width: `${stats.total > 0 ? percent((item.value / stats.total) * 100) : 0}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="min-h-0 rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <Activity className="h-3.5 w-3.5 text-cyan-300" />
              最近 PM 证据
            </div>
            <div className="space-y-2">
              {recentLogs.length > 0 ? (
                recentLogs.map((log, index) => (
                  <div key={`pm-log-${log.id || 'no-id'}-${index}`} className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2">
                    <div className="truncate text-xs font-medium text-slate-200">{log.title || log.source || 'PM 事件'}</div>
                    <div className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500">{log.message}</div>
                  </div>
                ))
              ) : (
                <div className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs text-slate-500">
                  暂无 PM 运行证据。
                </div>
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

function FactoryDirectorLayer({
  workspace,
  tasks,
  fileEditEvents,
  executionLogs,
  roleStatus,
  currentRun,
  blueprintCoverage,
}: {
  workspace: string;
  tasks: PmTask[];
  fileEditEvents: FileEditEvent[];
  executionLogs: LogEntry[];
  roleStatus: RunRoleStatus | null;
  currentRun: FactoryRunStatus | null;
  blueprintCoverage: BlueprintCoverageSummary;
}) {
  const stats = buildDeliveryStats(tasks);
  const [selectedTaskId, setSelectedTaskId] = useState<string>(() => tasks[0]?.id || '');
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) || tasks[0] || null;
  const recentLogs = latestLogRows(executionLogs, 4);
  const eventTimestamp = (value: unknown): number => {
    const timestamp = Date.parse(String(value || ''));
    return Number.isFinite(timestamp) ? timestamp : 0;
  };
  const recentFileEvents = [...fileEditEvents]
    .sort((a, b) => eventTimestamp(b.timestamp) - eventTimestamp(a.timestamp))
    .slice(0, 6);
  const status = roleStatus?.status || (stats.running > 0 ? 'running' : stats.total > 0 ? 'ready' : 'waiting');
  const deliveryReady = blueprintCoverage.required === 0 || blueprintCoverage.missing.length === 0;
  const workspaceDisplay = workspaceLabel(workspace);

  useEffect(() => {
    if (!tasks.some((task) => task.id === selectedTaskId)) {
      setSelectedTaskId(tasks[0]?.id || '');
    }
  }, [selectedTaskId, tasks]);

  return (
    <div data-testid="director-workspace" className="flex h-full flex-col overflow-hidden bg-[#070b14]">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-indigo-500/20 bg-slate-950/80 px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-indigo-400/30 bg-indigo-500/10 text-indigo-100">
            <Hammer className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-indigo-100">Director 执行交付层</h2>
            <p className="truncate text-[10px] uppercase tracking-wider text-indigo-400/70">Delivery Execution Layer</p>
          </div>
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn('rounded-md border px-2 py-1 text-[10px] tracking-wider', roleStatusTone(status))}>
            {roleStatusLabel(status)}
          </span>
          <span data-testid="factory-director-workspace-label" className="max-w-[180px] truncate rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-slate-400" title={workspace || workspaceDisplay}>
            {workspaceDisplay}
          </span>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden p-4 2xl:grid-cols-[300px_minmax(0,1fr)_300px]">
        <aside className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-indigo-500/15 bg-white/[0.03]">
          <div className="shrink-0 border-b border-white/10 px-3 py-3">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold text-slate-100">Director 任务队列</h3>
                <p className="mt-1 text-xs text-slate-500">只读查看 Factory 分派队列。</p>
              </div>
              <button
                type="button"
                data-testid="director-workspace-bulk-execute"
                disabled
                title="工厂模式下由 Factory 编排 Director"
                className="shrink-0 rounded-md border border-slate-700 bg-slate-900/80 px-2 py-1 text-[10px] text-slate-500"
              >
                全部执行
              </button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-auto p-3">
            {tasks.length > 0 ? (
              <div className="space-y-2">
                {tasks.slice(0, 18).map((task) => {
                  const isSelected = selectedTask?.id === task.id;
                  return (
                    <button
                      key={task.id || taskTitle(task)}
                      type="button"
                      data-testid="director-task-item"
                      onClick={() => setSelectedTaskId(task.id)}
                      className={cn(
                        'w-full rounded-lg border px-3 py-2.5 text-left transition-colors',
                        isSelected
                          ? 'border-indigo-400/45 bg-indigo-500/10'
                          : 'border-white/10 bg-slate-950/45 hover:border-indigo-400/30'
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-xs font-medium text-slate-100">{taskTitle(task)}</span>
                        <span className={cn('shrink-0 rounded-md border px-1.5 py-0.5 text-[10px]', taskStatusTone(task))}>
                          {taskDisplayStatus(task)}
                        </span>
                      </div>
                      <div className="mt-1 truncate font-mono text-[10px] text-slate-500">{task.id || 'task-id n/a'}</div>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="flex h-full min-h-[260px] items-center justify-center rounded-lg border border-dashed border-white/10 bg-slate-950/35 text-center">
                <div>
                  <Hammer className="mx-auto h-8 w-8 text-slate-600" />
                  <p className="mt-3 text-sm text-slate-400">暂无 Director 队列</p>
                  <p className="mt-1 text-xs text-slate-600">等待蓝图交接后生成执行任务。</p>
                </div>
              </div>
            )}
          </div>
        </aside>

        <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-indigo-500/15 bg-white/[0.03]">
          <div className="shrink-0 border-b border-white/10 px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold text-slate-100">执行交付详情</h3>
                <p className="mt-1 text-xs text-slate-500">文件变更、命令和验证由 Factory 统一调度。</p>
              </div>
              <span className={cn(
                'shrink-0 rounded-md border px-2 py-1 text-[10px]',
                deliveryReady
                  ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
                  : 'border-amber-500/25 bg-amber-500/10 text-amber-200'
              )}>
                {deliveryReady ? '可接收' : '待蓝图'}
              </span>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-auto p-4">
            <div
              data-testid="director-execution-guard"
              className="mb-4 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-100"
            >
              工厂模式下由 Factory 编排 Director，不能在嵌入层直接启动。
            </div>

            {selectedTask ? (
              <article data-testid="director-task-detail" className="space-y-4">
                <section className="rounded-lg border border-white/10 bg-slate-950/45 p-3">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <h4 className="truncate text-sm font-semibold text-slate-100">{taskTitle(selectedTask)}</h4>
                    <span className={cn('rounded-md border px-1.5 py-0.5 text-[10px]', taskStatusTone(selectedTask))}>
                      {taskDisplayStatus(selectedTask)}
                    </span>
                  </div>
                  <p className="text-xs leading-5 text-slate-400">{taskGoal(selectedTask)}</p>
                </section>

                <section className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                  <DetailList title="执行步骤" items={taskStepItems(selectedTask)} empty="等待 PM 合同补充步骤" />
                  <DetailList title="验收标准" items={taskAcceptanceItems(selectedTask)} empty="等待 PM 合同补充验收" />
                </section>

                <section className="rounded-lg border border-white/10 bg-slate-950/45 p-3">
                  <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
                    <FileText className="h-3.5 w-3.5 text-indigo-300" />
                    目标文件
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {taskScopeItems(selectedTask).length > 0 ? (
                      taskScopeItems(selectedTask).slice(0, 10).map((item) => (
                        <span key={item} className="rounded-md border border-white/10 bg-white/5 px-2 py-1 text-[10px] text-slate-300">
                          {item}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-slate-500">暂无目标文件字段</span>
                    )}
                  </div>
                </section>
              </article>
            ) : (
              <div className="flex h-full min-h-[320px] items-center justify-center text-center text-slate-500">
                <div>
                  <FileText className="mx-auto h-8 w-8 text-slate-600" />
                  <p className="mt-3 text-sm">选择任务查看交付详情</p>
                </div>
              </div>
            )}
          </div>
        </section>

        <aside className="grid min-h-0 grid-cols-1 gap-3 overflow-auto lg:grid-cols-3 2xl:flex 2xl:flex-col">
          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <Route className="h-3.5 w-3.5 text-indigo-300" />
              交付状态
            </div>
            <div className="space-y-2">
              <MetricRow label="队列任务" value={String(stats.total)} tone="text-indigo-200" />
              <MetricRow label="已领取" value={String(stats.claimed)} tone="text-slate-300" />
              <MetricRow label="执行中" value={String(stats.running)} tone="text-cyan-200" />
              <MetricRow label="阻塞" value={String(stats.blocked)} tone={stats.blocked > 0 ? 'text-red-200' : 'text-emerald-200'} />
              <MetricRow label="完成" value={String(stats.completed)} tone="text-emerald-200" />
              <MetricRow label="Factory 阶段" value={PHASE_CONFIG[mapRunToFactoryPhase(currentRun)].label} tone="text-slate-300" />
            </div>
          </section>

          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <FileCode className="h-3.5 w-3.5 text-emerald-300" />
              实时文件活动
            </div>
            <div className="space-y-2">
              {recentFileEvents.length > 0 ? (
                recentFileEvents.map((event, index) => (
                  <div key={`file-${event.id || event.filePath || 'no-id'}-${index}`} className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2">
                    <div className="flex items-center justify-between gap-2 text-[10px]">
                      <span className="uppercase text-emerald-300">{event.operation}</span>
                      <span className="text-slate-600">{event.addedLines || 0}+ / {event.deletedLines || 0}-</span>
                    </div>
                    <div className="mt-1 truncate text-xs text-slate-300">{event.filePath}</div>
                  </div>
                ))
              ) : (
                <div className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs text-slate-500">
                  暂无文件变更事件。
                </div>
              )}
            </div>
          </section>

          <section className="min-h-0 rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <Activity className="h-3.5 w-3.5 text-cyan-300" />
              最近执行证据
            </div>
            <div className="space-y-2">
              {recentLogs.length > 0 ? (
                recentLogs.map((log, index) => (
                  <div key={`director-log-${log.id || 'no-id'}-${index}`} className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2">
                    <div className="truncate text-xs font-medium text-slate-200">{log.title || log.source || 'Director 事件'}</div>
                    <div className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500">{log.message}</div>
                  </div>
                ))
              ) : (
                <div className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs text-slate-500">
                  暂无执行证据。
                </div>
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

function DetailList({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <section className="rounded-lg border border-white/10 bg-slate-950/45 p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
        <BadgeCheck className="h-3.5 w-3.5 text-cyan-300" />
        {title}
      </div>
      {items.length > 0 ? (
        <ul className="space-y-1.5">
          {items.slice(0, 6).map((item) => (
            <li key={item} className="flex gap-2 text-xs leading-5 text-slate-400">
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300" />
              <span className="min-w-0 break-words">{item}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-slate-500">{empty}</p>
      )}
    </section>
  );
}

function roleLayerDisplayName(layer: FactoryRoleLayer): string {
  if (layer === 'chief_engineer') return 'Chief Engineer';
  if (layer === 'director') return 'Director';
  return 'PM';
}

function FactoryChiefEngineerLayer({
  workspace,
  blueprintEvidence,
  reviewArtifacts,
  blueprintCoverage,
  roleStatus,
  currentRun,
}: {
  workspace: string;
  blueprintEvidence: BlueprintEvidenceView[];
  reviewArtifacts: ChiefEngineerReviewArtifactView[];
  blueprintCoverage: BlueprintCoverageSummary;
  roleStatus: RunRoleStatus | null;
  currentRun: FactoryRunStatus | null;
}) {
  const candidateTasks = blueprintCoverage.missing.slice(0, 5);
  const status = roleStatus?.status || (blueprintCoverage.missing.length > 0 ? 'waiting' : blueprintEvidence.length > 0 ? 'ready' : 'waiting');
  const directorStageActive = normalizeToken(currentRun?.current_stage).includes('director');
  const handoffReady = blueprintCoverage.required > 0 && blueprintCoverage.missing.length === 0;
  const handoffLabel = blueprintCoverage.missing.length > 0
    ? '缺证据'
    : handoffReady
      ? '就绪'
      : directorStageActive
        ? '已进入 Director'
        : '等待';
  const handoffTone = blueprintCoverage.missing.length > 0
    ? 'text-red-200'
    : handoffReady || directorStageActive
      ? 'text-emerald-200'
      : 'text-amber-200';
  const workspaceDisplay = workspaceLabel(workspace);

  return (
    <div data-testid="factory-chief-layer" className="flex h-full flex-col overflow-hidden bg-gradient-to-br from-slate-950 via-slate-900 to-cyan-950/25">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-cyan-500/20 bg-slate-950/80 px-4">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-cyan-400/30 bg-cyan-500/10 text-cyan-100">
            <Brain className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-cyan-100">Chief Engineer</h2>
            <p className="text-[10px] uppercase tracking-wider text-cyan-400/70">Blueprint Handoff Layer</p>
          </div>
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn('rounded-md border px-2 py-1 text-[10px] tracking-wider', roleStatusTone(status))}>
            {roleStatusLabel(status)}
          </span>
          <span data-testid="factory-chief-workspace-label" className="max-w-[180px] truncate rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-slate-400" title={workspace || workspaceDisplay}>
            {workspaceDisplay}
          </span>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden p-4 2xl:grid-cols-[minmax(0,1fr)_340px]">
        <section className="min-h-0 overflow-auto rounded-lg border border-cyan-500/15 bg-white/[0.035]">
          <div className="border-b border-white/10 px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-100">施工蓝图证据</h3>
                <p className="mt-1 text-xs text-slate-500">仅展示任务合同字段和 Factory 运行时蓝图产物。</p>
              </div>
              <span className="rounded-md border border-cyan-500/25 bg-cyan-500/10 px-2 py-1 text-[10px] text-cyan-100">
                {blueprintEvidence.length} 条蓝图
              </span>
            </div>
          </div>

          <div className="space-y-3 p-4">
            {blueprintEvidence.length > 0 ? (
              blueprintEvidence.map((evidence) => {
                return (
                  <article
                    key={`${evidence.source}-${evidence.id}-${evidence.path}`}
                    className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-3"
                    title={evidence.path || evidence.id}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 text-sm font-medium text-cyan-100">
                          <FileText className="h-4 w-4 shrink-0" />
                          <span className="truncate">{evidence.title}</span>
                        </div>
                        {evidence.summary ? (
                          <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">
                            {evidence.summary}
                          </p>
                        ) : null}
                      </div>
                    </div>
                  </article>
                );
              })
            ) : (
              <div className="rounded-lg border border-amber-500/25 bg-amber-500/10 p-4 text-sm text-amber-100">
                <div className="flex items-center gap-2 font-medium">
                  <AlertCircle className="h-4 w-4" />
                  暂无 Chief Engineer 蓝图证据
                </div>
                <p className="mt-2 text-xs leading-5 text-amber-100/75">
                  等待 PM/CE 链路写入 `blueprint_id`、`blueprint_path` 或 `runtime_blueprint_path` 后，再开放 Director 交接判断。
                </p>
              </div>
            )}
          </div>
        </section>

        <aside className="grid min-h-0 grid-cols-1 gap-3 overflow-auto lg:grid-cols-2 2xl:flex 2xl:flex-col">
          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <Route className="h-3.5 w-3.5 text-cyan-300" />
              交接状态
            </div>
            <div className="space-y-2">
              <MetricRow label="任务覆盖" value={`${blueprintCoverage.covered}/${blueprintCoverage.required}`} tone="text-cyan-200" />
              <MetricRow label="蓝图证据" value={String(blueprintEvidence.length)} tone="text-cyan-200" />
              <MetricRow label="审查回执" value={String(reviewArtifacts.length)} tone="text-slate-300" />
              <MetricRow label="待蓝图任务" value={String(blueprintCoverage.missing.length)} tone={blueprintCoverage.missing.length > 0 ? 'text-red-200' : 'text-emerald-200'} />
              <MetricRow label="已完成任务" value={String(blueprintCoverage.completed)} tone="text-slate-300" />
              <MetricRow label="Director 交接" value={handoffLabel} tone={handoffTone} />
              <MetricRow label="Factory 阶段" value={currentRun?.current_stage || 'n/a'} tone="text-slate-300" />
            </div>
          </section>

          <section className="min-h-0 rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <BadgeCheck className="h-3.5 w-3.5 text-emerald-300" />
              审查回执
            </div>
            <div className="space-y-2">
              {reviewArtifacts.length > 0 ? (
                reviewArtifacts.map((artifact) => (
                  <div key={`${artifact.source}-${artifact.id}-${artifact.path}`} className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2">
                    <div className="truncate text-xs font-medium text-slate-200">{artifact.title}</div>
                    <div className="mt-1 truncate text-[10px] text-slate-500" title={artifact.path}>
                      {artifact.path}
                    </div>
                  </div>
                ))
              ) : (
                <div className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs text-slate-400">
                  暂无 Factory Chief Engineer 审查回执。
                </div>
              )}
            </div>
          </section>

          <section className="min-h-0 rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <ClipboardList className="h-3.5 w-3.5 text-amber-300" />
              待蓝图任务
            </div>
            <div className="space-y-2">
              {candidateTasks.length > 0 ? (
                candidateTasks.map((task, index) => (
                  <div key={`candidate-${task.id || taskTitle(task) || 'task'}-${index}`} className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2">
                    <div className="truncate text-xs font-medium text-slate-200">{task.title || task.id}</div>
                    <div className="mt-1 truncate text-[10px] text-slate-500">{task.goal || task.summary || task.description || '等待蓝图输入'}</div>
                  </div>
                ))
              ) : (
                <div className="rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-2 text-xs text-emerald-100">
                  当前任务均已具备蓝图字段或暂无 PM 任务。
                </div>
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

function FactoryOperationsRail({
  currentRun,
  factoryPhase,
  workspacePhase,
  activeLayer,
  activityLogs,
  llmStreamEvents,
  processStreamEvents,
  gateResults,
  deliveryArtifacts,
  summaryMarkdown,
  summaryRows,
  artifactErrorMessage,
  isArtifactsLoading,
  isRunning,
}: {
  currentRun: FactoryRunStatus | null;
  factoryPhase: FactoryPhase;
  workspacePhase: string;
  activeLayer: FactoryRoleLayer;
  activityLogs: LogEntry[];
  llmStreamEvents: LogEntry[];
  processStreamEvents: LogEntry[];
  gateResults: FactoryRunStatus['gates'];
  deliveryArtifacts: FactoryRunArtifact[];
  summaryMarkdown: string;
  summaryRows: Array<[string, string]>;
  artifactErrorMessage: string;
  isArtifactsLoading: boolean;
  isRunning: boolean;
}) {
  const sourceEvidence = buildRunSourceEvidence(currentRun);
  const failureBrief = buildFactoryFailureBrief(currentRun);

  return (
    <aside
      className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden border-t border-white/10 bg-slate-950/80 xl:border-l xl:border-t-0"
      data-testid="factory-operations-rail"
    >
      <section className="shrink-0 border-b border-white/10 p-3">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
            <Activity className="h-3.5 w-3.5 text-emerald-300" />
            运行观测
          </div>
          <span className={cn('rounded-md border px-1.5 py-0.5 text-[10px] uppercase', roleStatusTone(currentRun?.status || 'idle'))}>
            {roleStatusLabel(currentRun?.status || 'idle')}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <MiniMetric label="角色层" value={roleLayerDisplayName(activeLayer)} />
          <MiniMetric label="阶段" value={PHASE_CONFIG[factoryPhase].label} />
          <MiniMetric label="运行ID" value={currentRun?.run_id || 'n/a'} />
          <MiniMetric label="进度" value={`${Math.round(currentRun?.progress || 0)}%`} />
        </div>
        {failureBrief ? <FactoryFailureBriefPanel brief={failureBrief} /> : null}
        {sourceEvidence.length > 0 ? (
          <div data-testid="factory-source-evidence" className="mt-3 rounded-lg border border-emerald-500/15 bg-emerald-500/[0.04] p-2">
            <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-emerald-300">
              <Route className="h-3.5 w-3.5" />
              来源证据
            </div>
            <div className="space-y-1.5">
              {sourceEvidence.map((row) => (
                <div key={`${row.label}-${row.value}`} className="grid grid-cols-[48px_minmax(0,1fr)] gap-2 text-[11px]">
                  <span className="text-slate-500">{row.label}</span>
                  <span
                    className={cn(
                      'min-w-0 break-words font-medium leading-4',
                      row.label === '指令' ? 'line-clamp-3' : 'truncate',
                      row.tone
                    )}
                    title={row.value}
                  >
                    {row.value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="min-h-[260px] flex-[1.05] overflow-hidden border-b border-white/10">
        <RealtimeActivityPanel
          executionLogs={activityLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          currentPhase={workspacePhase}
          isRunning={isRunning}
          role={activeLayer}
        />
      </section>

      <section className="min-h-0 flex-1 overflow-y-auto p-3">
        <FactoryAuditEvidencePanel
          gateResults={gateResults}
          deliveryArtifacts={deliveryArtifacts}
          summaryMarkdown={summaryMarkdown}
          summaryRows={summaryRows}
          artifactErrorMessage={artifactErrorMessage}
          isArtifactsLoading={isArtifactsLoading}
          failure={currentRun?.failure}
        />
      </section>
    </aside>
  );
}

function FactoryFailureBriefPanel({ brief }: { brief: FactoryFailureBrief }) {
  return (
    <section
      data-testid="factory-failure-brief"
      className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 p-2.5"
      aria-label="Factory failure root cause"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-semibold text-red-200">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />
            <span>{brief.headline}</span>
          </div>
          <p className="mt-1 line-clamp-3 text-[11px] leading-4 text-red-100/80">{brief.detail}</p>
        </div>
        <span className="shrink-0 rounded-md border border-red-400/25 bg-red-950/45 px-1.5 py-0.5 text-[10px] text-red-200">
          根因 {brief.rootRole}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <span className="rounded-md border border-white/10 bg-black/20 px-1.5 py-0.5 font-mono text-[10px] text-red-200">
          {brief.code}
        </span>
        <span className={cn(
          'rounded-md border px-1.5 py-0.5 text-[10px]',
          brief.recoverable
            ? 'border-amber-500/25 bg-amber-500/10 text-amber-200'
            : 'border-slate-700 bg-slate-900/70 text-slate-400'
        )}>
          {brief.recoverable ? '可重试' : '需先修复根因'}
        </span>
        {brief.cascades.length > 0 ? (
          <span className="rounded-md border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200">
            {brief.cascades.length} 个级联阻塞
          </span>
        ) : null}
      </div>
      {brief.cascades.length > 0 ? (
        <div className="mt-2 space-y-1">
          {brief.cascades.slice(0, 3).map((cascade) => (
            <p key={cascade} className="truncate text-[10px] text-red-100/65" title={cascade}>
              {cascade}
            </p>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function FactoryAuditEvidencePanel({
  gateResults,
  deliveryArtifacts,
  summaryMarkdown,
  summaryRows,
  artifactErrorMessage,
  isArtifactsLoading,
  failure,
}: {
  gateResults: FactoryRunStatus['gates'];
  deliveryArtifacts: FactoryRunArtifact[];
  summaryMarkdown: string;
  summaryRows: Array<[string, string]>;
  artifactErrorMessage: string;
  isArtifactsLoading: boolean;
  failure?: FactoryRunStatus['failure'];
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-emerald-300">
        <ShieldCheck className="h-4 w-4" />
        <h3 className="text-xs font-semibold uppercase tracking-wider">总监审计 / 交付证据</h3>
      </div>

      <section>
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-300">
          <BadgeCheck className="h-3.5 w-3.5 text-cyan-300" />
          <span>质量门</span>
        </div>
        <div className="space-y-2">
          {gateResults.length > 0 ? (
            gateResults.map((gate) => (
              <div key={gate.gate_name} className={cn('rounded-lg border px-3 py-2 text-xs', gateTone(gate))}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{gate.gate_name}</span>
                  <span className="shrink-0 uppercase">{gate.status || 'n/a'}</span>
                </div>
                <div className="mt-1 flex items-center justify-between gap-2 text-[10px] opacity-80">
                  <span>{gate.passed ? '通过' : '阻塞'}</span>
                  {typeof gate.score === 'number' && <span>score {gate.score}</span>}
                </div>
                {gate.message && (
                  <p className="mt-1 text-[10px] leading-relaxed opacity-80">{gate.message}</p>
                )}
              </div>
            ))
          ) : (
            <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
              <p className="text-xs text-slate-400">暂无质量门结果</p>
              <p className="mt-1 text-[10px] text-slate-600">等待可审计门禁记录</p>
            </div>
          )}
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-300">
          <PackageCheck className="h-3.5 w-3.5 text-emerald-300" />
          <span>交付产物</span>
        </div>
        <div className="space-y-2">
          {isArtifactsLoading && (
            <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-300" />
              <span>同步证据中</span>
            </div>
          )}
          {artifactErrorMessage && (
            <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              产物同步失败: {artifactErrorMessage}
            </div>
          )}
          {deliveryArtifacts.length > 0 ? (
            deliveryArtifacts.map((artifact) => (
              <div key={`${artifact.path}-${artifact.name}`} className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <FileText className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                    <span className="truncate text-xs text-slate-200">{artifact.name}</span>
                  </div>
                  <span className="shrink-0 text-[10px] text-slate-500">{formatBytes(artifact.size)}</span>
                </div>
                <p className="mt-1 break-all text-[10px] leading-relaxed text-slate-500">{artifact.path}</p>
              </div>
            ))
          ) : (
            <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
              <p className="text-xs text-slate-400">暂无交付产物</p>
              <p className="mt-1 text-[10px] text-slate-600">等待 Director 证据文件</p>
            </div>
          )}
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-300">
          <FileCode className="h-3.5 w-3.5 text-purple-300" />
          <span>交付摘要</span>
        </div>
        {summaryMarkdown ? (
          <div className="max-h-28 overflow-y-auto rounded-lg border border-white/10 bg-white/5 px-3 py-2">
            <p className="whitespace-pre-line text-xs leading-relaxed text-slate-300">{summaryMarkdown}</p>
          </div>
        ) : summaryRows.length > 0 ? (
          <div className="space-y-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs">
            {summaryRows.map(([key, value]) => (
              <div key={key} className="flex justify-between gap-2">
                <span className="text-slate-500">{key}</span>
                <span className="min-w-0 truncate text-right text-slate-300">{value}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
            <p className="text-xs text-slate-400">暂无交付摘要</p>
            <p className="mt-1 text-[10px] text-slate-600">等待终态摘要</p>
          </div>
        )}
      </section>

      {failure && (
        <section role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 p-3">
          <div className="flex items-center gap-2 text-red-300">
            <AlertCircle className="h-4 w-4" />
            <span className="text-sm font-medium">失败信息</span>
          </div>
          <p className="mt-2 text-xs text-red-200">{failure.detail}</p>
          {failure.suggested_action && (
            <p className="mt-2 text-xs text-red-300/80">建议: {failure.suggested_action}</p>
          )}
        </section>
      )}
    </div>
  );
}

function StatusChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="max-w-24 truncate text-xs text-slate-200">{value}</div>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="truncate text-xs font-medium text-slate-200">{value}</div>
    </div>
  );
}

function MetricRow({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className={cn('min-w-0 truncate text-right font-medium', tone)}>{value}</span>
    </div>
  );
}
