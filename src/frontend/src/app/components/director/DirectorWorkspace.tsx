/** DirectorWorkspace - Director 执行工作区
 *
 * 角色特性：
 * - 任务执行与代码实现
 * - 调试与问题解决
 * - 测试用例执行
 * - 执行状态汇报
 * - 阻塞问题上报
 */
import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { openPath } from '@/api';
import {
  Hammer,
  Code2,
  Play,
  Bug,
  Terminal,
  CheckCircle2,
  MessageSquare,
  Settings,
  ChevronLeft,
  FileCode,
  ListTodo,
  History,
  Activity,
  Loader2,
  AlertTriangle,
  Zap,
  Pause,
  RotateCcw,
  RefreshCw,
  Send,
  FilePlus,
  FileEdit,
  FileX,
  Clock,
  Coins,
  BarChart3,
  Layers,
  TrendingUp,
  ChevronDown,
  ChevronRight,
  Filter,
  ArrowRight,
  Hash,
  Brain,
  Wrench,
  Database,
  Trash2,
  SlidersHorizontal,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { AIDialoguePanel } from '@/app/components/ai-dialogue';
import { RealTimeFileDiff } from './RealTimeFileDiff';
import { resolveDirectorOpenTarget } from './directorFileActions';
import { TaskTraceTimeline } from '../common/TaskTraceTimeline';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import { RoleRunEvidenceStrip } from '@/app/components/common/RoleRunEvidenceStrip';
import {
  DirectorTaskPanel as DirectorTaskPanelView,
  type DirectorTaskBackendDetailState,
  type DirectorTaskCreateDraft,
  type DirectorTaskLLMEventsState,
  type DirectorWorkerDetailState,
} from './DirectorTaskPanel';
import { DirectorWorkbenchPanel } from './DirectorWorkbenchPanel';
import { DirectorStrategyPanel } from './DirectorStrategyPanel';
import {
  cancelDirectorRun,
  cancelDirectorTask,
  createDirectorTask,
  getDirectorCapabilities,
  getDirectorDiagnostics,
  getDirectorRun,
  getDirectorStatus,
  getDirectorTask,
  getDirectorWorker,
  clearRoleKernelCache,
  getDirectorTaskKernelLLMEvents,
  getRoleKernelCacheStats,
  getRoleKernelLLMEvents,
  getRoleKernelTokenBudgetStats,
  listDirectorTaskFallbackRows,
  listDirectorWorkers,
  runDirector,
  type DirectorCapabilitiesResponse,
  type DirectorOrchestrationRunResponse,
  type DirectorStatus,
  type DirectorWorker,
  type CreateDirectorTaskPayload,
  type DirectorDiagnosticsResponse,
  type RunDirectorPayload,
  type RoleKernelCacheStats,
  type RoleKernelLLMEvent,
  type RoleKernelLLMEventsResponse,
  type RoleKernelTokenBudgetStats,
} from '@/services';
import { TaskStatus, type PmTask } from '@/types/task';
import type { FileEditEvent } from '@/app/hooks/useRuntime';
import type { LogEntry } from '@/types/log';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntime';
import type { TaskTraceMap } from '@/types/taskTrace';

interface DirectorWorkspaceProps {
  workspace: string;
  onBackToMain: () => void;
  tasks: PmTask[];
  workers?: RuntimeWorkerState[];
  directorRunning: boolean;
  isStarting?: boolean;
  isStopping?: boolean;
  startBlockedReason?: string;
  onToggleDirector: () => void | boolean | Promise<void | boolean>;
  onOpenSettings?: () => void;
  currentTaskId?: string | null;
  currentTaskTitle?: string | null;
  currentTaskStatus?: string | null;
  fileEditEvents?: FileEditEvent[];
  executionLogs?: LogEntry[];
  llmStreamEvents?: LogEntry[];
  processStreamEvents?: LogEntry[];
  currentPhase?: string;
  factoryMode?: boolean;
  taskProgressMap?: Map<string, {
    phase?: string;
    phaseIndex?: number;
    phaseTotal?: number;
    retryCount?: number;
    maxRetries?: number;
    currentFile?: string;
  }>;
  taskTraceMap?: TaskTraceMap;
}

function evidenceEndpoint(endpoint: string, workspace: string): string {
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

interface DirectorTaskCancelState {
  taskId: string | null;
  loading: boolean;
  message: string | null;
  error: string | null;
}

interface DirectorTaskCreateState {
  loading: boolean;
  message: string | null;
  error: string | null;
  taskId: string | null;
}

interface DirectorRunEvidenceState {
  runId: string | null;
  loading: boolean;
  data: DirectorOrchestrationRunResponse | null;
  error: string | null;
}

interface DirectorRunCancelState {
  runId: string | null;
  loading: boolean;
  message: string | null;
  error: string | null;
}

interface DirectorToggleStatusEvidenceState {
  triggered: boolean;
  loading: boolean;
  data: DirectorStatus | null;
  error: string | null;
}

interface DirectorDiagnosticsState {
  loading: boolean;
  data: DirectorDiagnosticsResponse | null;
  error: string | null;
}

const DIRECTOR_TERMINAL_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled', 'canceled', 'blocked', 'timeout']);
const DIRECTOR_RUN_EVIDENCE_REFRESH_INTERVAL_MS = 3000;

interface LoadDirectorRunEvidenceOptions {
  preserveData?: boolean;
  preserveCancel?: boolean;
}

const isDirectorRunTerminal = (status?: string | null): boolean => {
  const token = String(status || '').trim().toLowerCase();
  return DIRECTOR_TERMINAL_RUN_STATUSES.has(token);
};

interface ExecutionTask {
  id: string;
  name: string;
  rawStatus?: string;
  goal?: string;
  description?: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'blocked';
  type: 'code' | 'test' | 'debug' | 'review';
  progress?: number;
  output?: string;
  error?: string;
  priority?: 'low' | 'medium' | 'high' | 'critical';
  budget?: {
    used: number;
    total: number;
    unit: 'tokens' | 'requests' | 'time';
  };
  estimatedTime?: number;
  actualTime?: number;
  dependencies?: string[];
  blockedBy?: string[];
  tags?: string[];
  createdAt?: string;
  startedAt?: string;
  completedAt?: string;
  assignedWorker?: string;
  claimedBy?: string;
  pmTaskId?: string;
  blueprintId?: string;
  blueprintPath?: string;
  source?: string;
  filesModified?: number;
  retries?: number;
  maxRetries?: number;
  executionSteps?: string[];
  acceptanceCriteria?: string[];
  targetFiles?: string[];
  currentFilePath?: string;
  activityUpdatedAt?: string;
  lineStats?: TaskLineStats;
  operationStats?: TaskOperationStats;
  // Progress tracking
  currentPhase?: string;
  phaseIndex?: number;
  phaseTotal?: number;
  taskScopedFileEvents?: FileEditEvent[];
}

interface ExecutionSession {
  id: string;
  status: 'idle' | 'running' | 'paused' | 'completed';
  currentTask?: ExecutionTask;
  logs: string[];
}

type DirectorActiveView = 'tasks' | 'code' | 'activity' | 'terminal' | 'debug' | 'strategy' | 'workbench';

type TaskExecutionStatus = ExecutionTask['status'];

interface ResolveTaskExecutionStatusParams {
  rawStatus: string;
  done: boolean;
  completed: boolean;
  directorRunning: boolean;
  isCurrent: boolean;
}

interface TaskLineStats {
  added: number;
  deleted: number;
  modified: number;
}

interface TaskOperationStats {
  create: number;
  modify: number;
  delete: number;
}

interface TaskRealtimeTelemetry {
  currentFilePath?: string;
  activityUpdatedAt?: string;
  filesTouchedCount: number;
  lineStats: TaskLineStats;
  operationStats: TaskOperationStats;
  // Progress tracking from backend
  retryCount?: number;
  maxRetries?: number;
  currentPhase?: string;
  phaseIndex?: number;
  phaseTotal?: number;
}

interface TaskRealtimeTelemetryAccumulator {
  currentFilePath?: string;
  activityUpdatedAt?: string;
  filesTouched: Set<string>;
  lineStats: TaskLineStats;
  operationStats: TaskOperationStats;
  // Progress tracking from backend
  retryCount?: number;
  maxRetries?: number;
  currentPhase?: string;
  phaseIndex?: number;
  phaseTotal?: number;
}

interface DirectorCapabilityHost {
  hostKind: string;
  capabilities: string[];
}

export function resolveTaskExecutionStatus(params: ResolveTaskExecutionStatusParams): TaskExecutionStatus {
  const normalized = String(params.rawStatus || '').trim().toLowerCase();
  const completed = params.done || params.completed || ['completed', 'done', 'success'].includes(normalized);
  if (completed) {
    return 'completed';
  }
  if (['failed', 'error'].includes(normalized)) {
    return 'failed';
  }
  if (['blocked', 'cancelled', 'canceled'].includes(normalized)) {
    return 'blocked';
  }
  if (['running', 'in_progress', 'claimed'].includes(normalized)) {
    return 'running';
  }
  if (params.directorRunning && params.isCurrent) {
    return 'running';
  }
  return 'pending';
}

function readTaskMetadata(task: PmTask): Record<string, unknown> {
  return task.metadata && typeof task.metadata === 'object'
    ? task.metadata
    : {};
}

function readTaskString(task: PmTask, keys: string[]): string {
  for (const key of keys) {
    const directValue = (task as unknown as Record<string, unknown>)[key];
    if (typeof directValue === 'string' && directValue.trim()) {
      return directValue.trim();
    }
    const metadataValue = readTaskMetadata(task)[key];
    if (typeof metadataValue === 'string' && metadataValue.trim()) {
      return metadataValue.trim();
    }
  }
  return '';
}

function readStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      if (typeof item === 'string') {
        return item.trim();
      }
      if (item && typeof item === 'object') {
        const record = item as Record<string, unknown>;
        return String(record.description || record.title || record.name || record.path || record.id || '').trim();
      }
      return String(item || '').trim();
    })
    .filter((item) => item.length > 0);
}

function readTaskStringList(task: PmTask, keys: string[]): string[] {
  const metadata = readTaskMetadata(task);
  for (const key of keys) {
    const directList = readStringList((task as unknown as Record<string, unknown>)[key]);
    if (directList.length > 0) {
      return directList;
    }
    const metadataList = readStringList(metadata[key]);
    if (metadataList.length > 0) {
      return metadataList;
    }
  }
  return [];
}

function hasUsableTaskValue(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (value && typeof value === 'object') {
    return Object.keys(value as Record<string, unknown>).length > 0;
  }
  if (typeof value === 'string') {
    return value.trim().length > 0;
  }
  return value !== undefined && value !== null;
}

function mergeTaskRows(detailRow: PmTask, liveRow: PmTask): PmTask {
  const detailRecord = detailRow as unknown as Record<string, unknown>;
  const liveRecord = liveRow as unknown as Record<string, unknown>;
  const merged: Record<string, unknown> = { ...detailRecord, ...liveRecord };
  const detailMetadata = readTaskMetadata(detailRow);
  const liveMetadata = readTaskMetadata(liveRow);
  const mergedMetadata = { ...detailMetadata, ...liveMetadata };
  if (hasUsableTaskValue(mergedMetadata)) {
    merged.metadata = mergedMetadata;
  }

  for (const key of [
    'goal',
    'description',
    'acceptance',
    'acceptance_criteria',
    'execution_steps',
    'target_files',
    'current_file',
    'current_file_path',
    'dependencies',
    'pm_task_id',
  ]) {
    if (!hasUsableTaskValue(liveRecord[key]) && hasUsableTaskValue(detailRecord[key])) {
      merged[key] = detailRecord[key];
    }
  }

  if (
    typeof detailRecord.description === 'string'
    && detailRecord.description.trim()
    && typeof liveRecord.description === 'string'
    && [liveRecord.subject, liveRecord.title, liveRecord.id].some((value) => liveRecord.description === value)
  ) {
    merged.description = detailRecord.description;
  }

  return merged as unknown as PmTask;
}

function normalizeDirectorCreatedTaskRow(
  value: unknown,
  payload: CreateDirectorTaskPayload,
  fallbackTaskId: string,
): PmTask | null {
  const taskId = String(fallbackTaskId || '').trim();
  if (!taskId) {
    return null;
  }

  const record = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  const metadata = record.metadata && typeof record.metadata === 'object'
    ? record.metadata as Record<string, unknown>
    : {};
  const subject = String(record.subject || record.title || payload.subject || taskId).trim();
  const description = String(record.description || payload.description || subject).trim();
  const status = String(record.status || 'PENDING').trim().toLowerCase();
  const priority = String(record.priority || payload.priority || 'MEDIUM').trim().toUpperCase();
  const acceptance = readStringList(record.acceptance).length > 0
    ? readStringList(record.acceptance)
    : payload.metadata.acceptance;

  return {
    id: taskId,
    title: subject,
    subject,
    goal: String(record.goal || payload.metadata.pm_task_title || subject).trim(),
    description,
    status: status === 'completed'
      ? TaskStatus.COMPLETED
      : status === 'failed'
        ? TaskStatus.FAILED
        : status === 'blocked'
          ? TaskStatus.BLOCKED
          : status === 'running' || status === 'claimed' || status === 'in_progress'
            ? TaskStatus.IN_PROGRESS
            : TaskStatus.PENDING,
    state: String(record.state || record.status || 'PENDING'),
    done: false,
    completed: false,
    priority: priority === 'CRITICAL' ? 4 : priority === 'HIGH' ? 3 : priority === 'LOW' ? 1 : 2,
    acceptance: acceptance.map((descriptionText) => ({ description: descriptionText })),
    acceptance_criteria: acceptance,
    command: typeof record.command === 'string' ? record.command : undefined,
    execution_checklist: readStringList(record.execution_steps || record.execution_checklist || record.steps),
    target_files: readStringList(record.target_files || record.files),
    dependencies: readStringList(record.dependencies),
    blueprint_id: String(record.blueprint_id || payload.metadata.blueprint_id || '').trim() || null,
    blueprint_path: String(record.blueprint_path || payload.metadata.blueprint_path || '').trim() || null,
    runtime_blueprint_path: String(record.runtime_blueprint_path || payload.metadata.runtime_blueprint_path || '').trim() || null,
    pm_task_id: String(record.pm_task_id || metadata.pm_task_id || payload.metadata.pm_task_id || '').trim(),
    metadata: {
      ...payload.metadata,
      ...metadata,
      director_task_source: metadata.director_task_source || 'local',
      priority,
      subject,
    },
    created_at: typeof record.created_at === 'string' ? record.created_at : new Date().toISOString(),
  } as PmTask;
}

function upsertDirectorFallbackTaskRow(current: PmTask[], task: PmTask): PmTask[] {
  const taskId = String(task.id || '').trim();
  if (!taskId) {
    return current;
  }

  const existing = current.find((item) => String(item.id || '').trim() === taskId);
  const nextTask = existing ? mergeTaskRows(existing, task) : task;
  return [
    nextTask,
    ...current.filter((item) => String(item.id || '').trim() !== taskId),
  ];
}

function toTaskToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function toNonNegativeInt(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0, Math.round(numeric)) : 0;
}

function resolveTaskIdentityCandidates(task: PmTask): string[] {
  const metadata = readTaskMetadata(task);
  const rawTask = task as unknown as Record<string, unknown>;
  const candidates = [
    task.id,
    task.title,
    rawTask.subject,
    rawTask.pm_task_id,
    task.goal,
    metadata.pm_task_id,
    metadata.task_id,
    metadata.subject,
    metadata.id,
  ];
  const normalized: string[] = [];
  const seen = new Set<string>();
  for (const candidate of candidates) {
    const token = toTaskToken(candidate);
    if (!token || seen.has(token)) {
      continue;
    }
    seen.add(token);
    normalized.push(token);
  }
  return normalized;
}

export function computePatchLineStats(
  patch: string | undefined,
  operation: FileEditEvent['operation'],
): TaskLineStats {
  const text = String(patch || '');
  if (!text) {
    return { added: 0, deleted: 0, modified: 0 };
  }
  const lines = text.split('\n');
  const hasDiffMarkers = lines.some((line) => line.startsWith('@@') || line.startsWith('+++ ') || line.startsWith('--- '));
  if (!hasDiffMarkers) {
    const rawLineCount = lines.filter((line) => line.trim().length > 0).length;
    if (operation === 'delete') {
      return { added: 0, deleted: rawLineCount, modified: 0 };
    }
    return { added: rawLineCount, deleted: 0, modified: 0 };
  }

  let plus = 0;
  let minus = 0;
  for (const line of lines) {
    if (!line) continue;
    if (line.startsWith('+++ ') || line.startsWith('--- ') || line.startsWith('@@')) {
      continue;
    }
    if (line.startsWith('+')) {
      plus += 1;
      continue;
    }
    if (line.startsWith('-')) {
      minus += 1;
    }
  }
  const modified = Math.min(plus, minus);
  return {
    added: Math.max(0, plus - modified),
    deleted: Math.max(0, minus - modified),
    modified,
  };
}

function resolveEventLineStats(event: FileEditEvent): TaskLineStats {
  const backendStats = {
    added: toNonNegativeInt(event.addedLines),
    deleted: toNonNegativeInt(event.deletedLines),
    modified: toNonNegativeInt(event.modifiedLines),
  };
  if (backendStats.added > 0 || backendStats.deleted > 0 || backendStats.modified > 0) {
    return backendStats;
  }
  return computePatchLineStats(event.patch, event.operation);
}

export function buildTaskRealtimeTelemetry(
  tasks: PmTask[],
  fileEditEvents: FileEditEvent[],
  taskProgressMap?: Map<string, {
    phase?: string;
    phaseIndex?: number;
    phaseTotal?: number;
    retryCount?: number;
    maxRetries?: number;
    currentFile?: string;
  }>,
): Map<string, TaskRealtimeTelemetry> {
  const tokenToTaskId = new Map<string, string>();
  const taskIdSet = new Set<string>();
  for (const task of tasks) {
    const taskId = String(task.id || '').trim();
    if (!taskId) {
      continue;
    }
    taskIdSet.add(taskId);
    const candidates = resolveTaskIdentityCandidates(task);
    for (const token of candidates) {
      tokenToTaskId.set(token, taskId);
    }
    const rawTask = task as unknown as Record<string, unknown>;
    for (const aliasKey of ['subject', 'pm_task_id', 'task_id', 'backlog_ref']) {
      const aliasToken = toTaskToken(rawTask[aliasKey] ?? readTaskMetadata(task)[aliasKey]);
      if (aliasToken) {
        tokenToTaskId.set(aliasToken, taskId);
      }
    }
  }

  const accumulators = new Map<string, TaskRealtimeTelemetryAccumulator>();

  // Process file edit events
  for (const event of fileEditEvents) {
    const rawTaskId = String(event.taskId || '').trim();
    if (!rawTaskId) {
      continue;
    }
    const rawTaskToken = toTaskToken(rawTaskId);
    let mappedTaskId = tokenToTaskId.get(rawTaskToken) || "";
    if (!mappedTaskId) {
      for (const task of tasks) {
        const taskId = String(task.id || '').trim();
        if (!taskId) {
          continue;
        }
        const aliases = [
          ...resolveTaskIdentityCandidates(task),
          ...readTaskStringList(task, ['target_task_ids', 'related_task_ids']),
        ];
        if (aliases.some((alias) => toTaskToken(alias) === rawTaskToken)) {
          mappedTaskId = taskId;
          break;
        }
      }
    }
    mappedTaskId = mappedTaskId || rawTaskId;
    if (!taskIdSet.has(mappedTaskId)) {
      continue;
    }
    const accumulator = accumulators.get(mappedTaskId) || {
      filesTouched: new Set<string>(),
      lineStats: { added: 0, deleted: 0, modified: 0 },
      operationStats: { create: 0, modify: 0, delete: 0 },
    };
    const lineStats = resolveEventLineStats(event);
    accumulator.lineStats.added += lineStats.added;
    accumulator.lineStats.deleted += lineStats.deleted;
    accumulator.lineStats.modified += lineStats.modified;
    accumulator.operationStats[event.operation] += 1;
    if (event.filePath) {
      accumulator.filesTouched.add(event.filePath);
    }

    const previousEpoch = Date.parse(String(accumulator.activityUpdatedAt || ''));
    const nextEpoch = Date.parse(String(event.timestamp || ''));
    const shouldReplaceCurrentFile = !Number.isFinite(previousEpoch)
      || (Number.isFinite(nextEpoch) && nextEpoch >= previousEpoch);
    if (shouldReplaceCurrentFile) {
      accumulator.currentFilePath = event.filePath || accumulator.currentFilePath;
      accumulator.activityUpdatedAt = event.timestamp || accumulator.activityUpdatedAt;
    }

    accumulators.set(mappedTaskId, accumulator);
  }

  // Merge in task progress data (retry count, phase info, current file from backend)
  if (taskProgressMap) {
    for (const [taskId, progress] of taskProgressMap.entries()) {
      if (!taskIdSet.has(taskId)) {
        continue;
      }
      const accumulator = accumulators.get(taskId) || {
        filesTouched: new Set<string>(),
        lineStats: { added: 0, deleted: 0, modified: 0 },
        operationStats: { create: 0, modify: 0, delete: 0 },
      };

      // Update retry count from progress
      if (progress.retryCount !== undefined) {
        accumulator.retryCount = progress.retryCount;
      }
      if (progress.maxRetries !== undefined) {
        accumulator.maxRetries = progress.maxRetries;
      }

      // Update phase info
      if (progress.phase) {
        accumulator.currentPhase = progress.phase;
      }
      if (progress.phaseIndex !== undefined) {
        accumulator.phaseIndex = progress.phaseIndex;
      }
      if (progress.phaseTotal !== undefined) {
        accumulator.phaseTotal = progress.phaseTotal;
      }

      // Update current file from progress (takes precedence over file edit events)
      if (progress.currentFile) {
        accumulator.currentFilePath = progress.currentFile;
      }

      accumulators.set(taskId, accumulator);
    }
  }

  const telemetry = new Map<string, TaskRealtimeTelemetry>();
  for (const [taskId, accumulator] of accumulators.entries()) {
    telemetry.set(taskId, {
      currentFilePath: accumulator.currentFilePath,
      activityUpdatedAt: accumulator.activityUpdatedAt,
      filesTouchedCount: accumulator.filesTouched.size,
      lineStats: { ...accumulator.lineStats },
      operationStats: { ...accumulator.operationStats },
      retryCount: accumulator.retryCount,
      maxRetries: accumulator.maxRetries,
      currentPhase: accumulator.currentPhase,
      phaseIndex: accumulator.phaseIndex,
      phaseTotal: accumulator.phaseTotal,
    });
  }
  return telemetry;
}

function formatTelemetryTime(value: string | undefined): string {
  if (!value) {
    return '';
  }
  const epoch = Date.parse(value);
  if (!Number.isFinite(epoch)) {
    return '';
  }
  return new Date(epoch).toLocaleTimeString();
}

function resolveSessionStatus(
  directorRunning: boolean,
  isStarting: boolean,
  tasks: ExecutionTask[],
): ExecutionSession['status'] {
  if (directorRunning || isStarting) {
    return 'running';
  }

  if (tasks.length > 0 && tasks.every((task) => task.status === 'completed')) {
    return 'completed';
  }

  if (tasks.some((task) => task.status === 'blocked')) {
    return 'paused';
  }

  return 'idle';
}

export function normalizeDirectorCapabilityHosts(
  payload: DirectorCapabilitiesResponse | null | undefined,
): DirectorCapabilityHost[] {
  const capabilities = payload?.capabilities;
  if (Array.isArray(capabilities)) {
    return [{ hostKind: payload?.role || 'default', capabilities: capabilities.filter(Boolean).map(String).sort() }];
  }

  if (!capabilities || typeof capabilities !== 'object') {
    return [];
  }

  return Object.entries(capabilities)
    .map(([hostKind, values]) => ({
      hostKind,
      capabilities: Array.isArray(values) ? values.filter(Boolean).map(String).sort() : [],
    }))
    .filter((entry) => entry.capabilities.length > 0)
    .sort((left, right) => left.hostKind.localeCompare(right.hostKind));
}

function formatCapabilityLabel(value: string): string {
  return value.replace(/_/g, ' ');
}

function formatKernelNumber(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '-';
}

function formatKernelPercent(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}%` : '-';
}

function readKernelEventText(event: RoleKernelLLMEvent | null | undefined, keys: string[]): string {
  if (!event) {
    return '';
  }
  for (const key of keys) {
    const value = event[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
    if (typeof value === 'number' && Number.isFinite(value)) {
      return String(value);
    }
  }
  return '';
}

function readKernelStatNumber(stats: Record<string, unknown> | null | undefined, keys: string[]): number | undefined {
  if (!stats) {
    return undefined;
  }
  for (const key of keys) {
    const value = stats[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
  }
  return undefined;
}

function formatKernelEventType(event: RoleKernelLLMEvent | null | undefined): string {
  return readKernelEventText(event, ['event_type', 'type', 'status']).replace(/_/g, ' ') || '-';
}

function formatKernelEventModel(event: RoleKernelLLMEvent | null | undefined): string {
  return readKernelEventText(event, ['model', 'model_name', 'provider']) || '-';
}

function readWorkerText(row: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
    if (typeof value === 'number' && Number.isFinite(value)) {
      return String(value);
    }
  }
  return '';
}

function readWorkerNumber(row: Record<string, unknown>, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
      return Number(value);
    }
  }
  return undefined;
}

function readWorkerBoolean(row: Record<string, unknown>, keys: string[]): boolean | undefined {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === 'boolean') {
      return value;
    }
    if (typeof value === 'string' && value.trim()) {
      const normalized = value.trim().toLowerCase();
      if (['true', 'healthy', 'ok', 'ready'].includes(normalized)) {
        return true;
      }
      if (['false', 'unhealthy', 'failed', 'error'].includes(normalized)) {
        return false;
      }
    }
  }
  return undefined;
}

export function normalizeDirectorWorkerRows(rows: DirectorWorker[] | null | undefined): RuntimeWorkerState[] {
  if (!Array.isArray(rows)) {
    return [];
  }

  return rows
    .map((row) => {
      if (!row || typeof row !== 'object') {
        return null;
      }
      const record = row as Record<string, unknown>;
      const id = readWorkerText(record, ['id', 'worker_id', 'name']);
      if (!id) {
        return null;
      }
      const worker: RuntimeWorkerState = {
        id,
        name: readWorkerText(record, ['name', 'display_name', 'worker_name']) || id,
        status: readWorkerText(record, ['status', 'state']) || 'idle',
        currentTaskId: readWorkerText(record, ['currentTaskId', 'current_task_id', 'task_id', 'current_task']) || undefined,
        healthy: readWorkerBoolean(record, ['healthy', 'is_healthy']),
        tasksCompleted: readWorkerNumber(record, ['tasksCompleted', 'tasks_completed', 'completed_tasks']),
        tasksFailed: readWorkerNumber(record, ['tasksFailed', 'tasks_failed', 'failed_tasks']),
      };
      return worker;
    })
    .filter((row): row is RuntimeWorkerState => Boolean(row));
}

export function mergeDirectorWorkers(
  realtimeWorkers: RuntimeWorkerState[],
  backendWorkers: RuntimeWorkerState[],
): RuntimeWorkerState[] {
  const merged = new Map<string, RuntimeWorkerState>();
  for (const worker of backendWorkers) {
    if (worker?.id) {
      merged.set(worker.id, worker);
    }
  }
  for (const worker of realtimeWorkers) {
    if (worker?.id) {
      merged.set(worker.id, {
        ...merged.get(worker.id),
        ...worker,
      });
    }
  }
  return Array.from(merged.values()).sort((left, right) => left.id.localeCompare(right.id));
}

function DirectorCapabilityStrip({
  hosts,
  isLoading,
  error,
  compact = false,
}: {
  hosts: DirectorCapabilityHost[];
  isLoading: boolean;
  error: string | null;
  compact?: boolean;
}) {
  const allCapabilities = new Set(hosts.flatMap((host) => host.capabilities));
  const deleteAllowed = allCapabilities.has('delete_files');
  const capabilityCount = allCapabilities.size;

  return (
    <section
      className={cn(
        compact ? 'min-w-0' : 'border-b border-white/10 bg-slate-950/55 px-4 py-2',
      )}
      data-testid="director-capability-strip"
      aria-label="Director capability matrix"
    >
      <details className="group h-full rounded-lg border border-indigo-500/15 bg-slate-900/35 px-3 py-2">
        <summary className="flex min-w-0 cursor-pointer list-none flex-wrap items-center gap-2 [&::-webkit-details-marker]:hidden">
          <div className="flex shrink-0 items-center gap-2 text-xs font-medium text-indigo-100">
            <Wrench className="h-3.5 w-3.5 text-indigo-300" />
            能力
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
              {isLoading ? '读取中' : error ? '能力异常' : `${hosts.length} host`}
            </span>
            {!isLoading && !error ? (
              <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
                {capabilityCount} capabilities
              </span>
            ) : null}
            {!isLoading && !error ? (
              <div
                className={cn(
                  'flex shrink-0 items-center gap-1.5 rounded border px-2 py-0.5 text-[10px]',
                  deleteAllowed
                    ? 'border-red-500/25 bg-red-500/10 text-red-200'
                    : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200',
                )}
                data-testid="director-delete-capability"
              >
                {deleteAllowed ? <AlertTriangle className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
                delete_files {deleteAllowed ? 'allowed' : 'blocked'}
              </div>
            ) : null}
          </div>
          <span className="ml-auto shrink-0 text-[10px] text-slate-500 group-open:hidden">详情</span>
          <span className="ml-auto hidden shrink-0 text-[10px] text-indigo-300 group-open:inline">收起</span>
        </summary>
        <div className="mt-2 flex min-w-0 items-center gap-3 border-t border-white/10 pt-2">
          <EvidenceEndpointBadge endpoint="/v2/director/capabilities" testId="director-capability-endpoint" />

          {isLoading ? (
            <div className="flex items-center gap-2 text-[11px] text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-300" />
              正在读取 Director 能力
            </div>
          ) : error ? (
            <div
              className="flex items-center gap-2 rounded border border-red-500/25 bg-red-500/10 px-2 py-1 text-[11px] text-red-200"
              data-testid="director-capability-error"
            >
              <AlertTriangle className="h-3.5 w-3.5" />
              {error}
            </div>
          ) : hosts.length > 0 ? (
            <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto" data-testid="director-capability-hosts">
              {hosts.map((host) => (
                <div
                  key={host.hostKind}
                  className="flex shrink-0 items-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-2 py-1"
                  data-testid="director-capability-host"
                >
                  <Brain className="h-3.5 w-3.5 text-cyan-300" />
                  <span className="text-[10px] font-medium text-slate-200">{host.hostKind}</span>
                  <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[9px] text-indigo-200">
                    {host.capabilities.length}
                  </span>
                  <div className="flex items-center gap-1">
                    {host.capabilities.slice(0, 4).map((capability) => (
                      <span
                        key={`${host.hostKind}-${capability}`}
                        className="rounded border border-white/10 bg-slate-950/70 px-1.5 py-0.5 text-[9px] text-slate-300"
                        title={capability}
                      >
                        {formatCapabilityLabel(capability)}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[11px] text-slate-500" data-testid="director-capability-empty">
              后端未返回能力矩阵
            </div>
          )}
        </div>
      </details>
    </section>
  );
}

function DirectorKernelDiagnosticsStrip({
  cacheStats,
  llmEvents,
  tokenBudgetStats,
  isLoading,
  isClearing,
  error,
  onRefresh,
  onClearCache,
  workspace,
  compact = false,
}: {
  cacheStats: RoleKernelCacheStats | null;
  llmEvents: RoleKernelLLMEventsResponse | null;
  tokenBudgetStats: RoleKernelTokenBudgetStats | null;
  isLoading: boolean;
  isClearing: boolean;
  error: string | null;
  onRefresh: () => void;
  onClearCache: () => void;
  workspace: string;
  compact?: boolean;
}) {
  const eventCount = llmEvents?.count ?? llmEvents?.events?.length;

  return (
    <section
      className={cn(
        compact ? 'min-w-0' : 'border-b border-white/10 bg-slate-950/45 px-4 py-2',
      )}
      data-testid="director-kernel-diagnostics-strip"
      aria-label="Director Kernel diagnostics"
    >
      <details className="group h-full rounded-lg border border-indigo-500/15 bg-slate-900/30 px-3 py-2">
        <summary className="flex min-w-0 cursor-pointer list-none flex-wrap items-center gap-2 [&::-webkit-details-marker]:hidden">
          <div className="flex shrink-0 items-center gap-2 text-xs font-medium text-indigo-100">
            <BarChart3 className="h-3.5 w-3.5 text-indigo-300" />
            Kernel
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            {isLoading ? (
              <span className="flex items-center gap-1 rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
                <Loader2 className="h-3 w-3 animate-spin text-indigo-300" />
                读取中
              </span>
            ) : error ? (
              <span className="rounded border border-red-500/25 bg-red-500/10 px-2 py-0.5 text-[10px] text-red-200">
                统计异常
              </span>
            ) : (
              <>
                <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
                  cache hit {formatKernelPercent(cacheStats?.hit_rate)}
                </span>
                <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
                  tokens {formatKernelNumber(tokenBudgetStats?.total)}
                </span>
                <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
                  LLM events {formatKernelNumber(eventCount)}
                </span>
              </>
            )}
          </div>
          <span className="ml-auto shrink-0 text-[10px] text-slate-500 group-open:hidden">详情</span>
          <span className="ml-auto hidden shrink-0 text-[10px] text-indigo-300 group-open:inline">收起</span>
        </summary>
        <div className="mt-2 flex min-w-0 items-center gap-3 border-t border-white/10 pt-2">
          {isLoading ? (
            <div className="flex items-center gap-2 text-[11px] text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-300" />
              正在读取缓存、预算与 LLM 事件
            </div>
          ) : error ? (
            <div
              className="flex min-w-0 items-center gap-2 rounded border border-red-500/25 bg-red-500/10 px-2 py-1 text-[11px] text-red-200"
              data-testid="director-kernel-diagnostics-error"
            >
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{error}</span>
            </div>
          ) : (
            <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
              <KernelStripMetric
                icon={<Database className="h-3.5 w-3.5 text-cyan-300" />}
                label="缓存"
                endpoint="/v2/director/cache-stats"
                values={[
                  `hit ${formatKernelPercent(cacheStats?.hit_rate)}`,
                  `${formatKernelNumber(cacheStats?.size)} / ${formatKernelNumber(cacheStats?.max_size)}`,
                  cacheStats?.enabled === false ? 'disabled' : 'enabled',
                ]}
              />
              <KernelStripMetric
                icon={<Coins className="h-3.5 w-3.5 text-emerald-300" />}
                label="预算"
                endpoint="/v2/director/token-budget-stats"
                values={[
                  `total ${formatKernelNumber(tokenBudgetStats?.total)}`,
                  `dialogue ${formatKernelNumber(tokenBudgetStats?.available_conversation)}`,
                  `margin ${formatKernelNumber(tokenBudgetStats?.safety_margin)}`,
                ]}
              />
              <KernelStripMetric
                icon={<Brain className="h-3.5 w-3.5 text-indigo-300" />}
                label="LLM"
                endpoint={evidenceEndpoint('/v2/director/llm-events?role=director&limit=5', workspace)}
                values={[
                  `events ${formatKernelNumber(eventCount)}`,
                  `last ${formatKernelEventType(llmEvents?.events?.[0])}`,
                  `model ${formatKernelEventModel(llmEvents?.events?.[0])}`,
                  `err/retry ${formatKernelNumber(readKernelStatNumber(llmEvents?.stats, ['call_error', 'llm_error', 'errors']))}/${formatKernelNumber(readKernelStatNumber(llmEvents?.stats, ['call_retry', 'llm_retry', 'retries']))}`,
                ]}
              />
            </div>
          )}

          <div className="ml-auto flex shrink-0 items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={onRefresh}
              disabled={isLoading || isClearing}
              title="刷新 Kernel 统计"
              className="h-7 w-7 text-slate-400 hover:bg-indigo-500/10 hover:text-indigo-300"
            >
              <RefreshCw className={cn('h-3.5 w-3.5', isLoading && 'animate-spin')} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={onClearCache}
              disabled={isLoading || isClearing}
              title="清空 Director LLM 缓存"
              data-testid="director-kernel-cache-clear"
              className="h-7 w-7 text-slate-400 hover:bg-red-500/10 hover:text-red-300"
            >
              {isClearing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            </Button>
          </div>
        </div>
      </details>
    </section>
  );
}

function formatDirectorDiagnosticIssue(issue: string): string {
  return String(issue || '')
    .replace(/^director_/, '')
    .replace(/_/g, ' ')
    .trim() || 'unknown';
}

const DIRECTOR_EXECUTION_BLOCKER_LABELS: Record<string, string> = {
  director_llm_not_ready: 'Director LLM 角色未通过运行前测试',
  director_status_unavailable: 'Director 状态投影不可用',
  director_tasks_unavailable: 'Director 任务队列不可用',
  director_no_tasks: '没有可执行的 Director 任务',
  director_no_ready_tasks: '没有 ready 任务，需先完成 PM/Chief Engineer 交接',
  director_ready_tasks_missing_blueprints: 'workflow 任务缺少 Chief Engineer 蓝图证据',
  director_ready_tasks_invalid_blueprints: 'workflow 任务引用的 Chief Engineer 蓝图不可审计',
  director_workers_unavailable: 'Director worker 池不可用',
  director_no_workers: '没有可用 worker',
  director_no_idle_workers: '有 ready 任务但没有空闲 worker',
};

const DIRECTOR_HARD_BLOCKER_ISSUES = new Set(Object.keys(DIRECTOR_EXECUTION_BLOCKER_LABELS));

function directorExecutionBlockers(diagnostics: DirectorDiagnosticsResponse | null): string[] {
  if (!diagnostics) {
    return [];
  }
  if (Array.isArray(diagnostics.execution_blockers) && diagnostics.execution_blockers.length > 0) {
    return diagnostics.execution_blockers
      .map((issue) => String(issue || '').trim())
      .filter((issue) => issue.length > 0);
  }
  if (diagnostics.status?.running) {
    return [];
  }
  const hasExplicitExecutionSignal = typeof diagnostics.can_execute === 'boolean' || Array.isArray(diagnostics.execution_blockers);
  if (hasExplicitExecutionSignal && diagnostics.can_execute !== false) {
    return [];
  }
  return (diagnostics.issues || []).filter((issue) => DIRECTOR_HARD_BLOCKER_ISSUES.has(issue));
}

function formatDirectorExecutionBlockReason(diagnostics: DirectorDiagnosticsResponse | null): string {
  const blockers = directorExecutionBlockers(diagnostics);
  if (blockers.length === 0) {
    return '';
  }
  const primary = DIRECTOR_EXECUTION_BLOCKER_LABELS[blockers[0]] || formatDirectorDiagnosticIssue(blockers[0]);
  const extraCount = blockers.length - 1;
  return `Director 交接诊断未通过：${primary}${extraCount > 0 ? `，另有 ${extraCount} 项阻断` : ''}`;
}

function DirectorReadinessDiagnosticsStrip({
  diagnostics,
  isLoading,
  error,
  onRefresh,
  compact = false,
  workspace,
}: {
  diagnostics: DirectorDiagnosticsResponse | null;
  isLoading: boolean;
  error: string | null;
  onRefresh: () => void;
  compact?: boolean;
  workspace: string;
}) {
  const issues = diagnostics?.issues || [];
  const executionBlockers = directorExecutionBlockers(diagnostics);
  const visibleIssues = [...new Set([...executionBlockers, ...issues])].slice(0, compact ? 1 : 3);
  const blocked = executionBlockers.length > 0;
  const llmValues = diagnostics?.llm
    ? [
        diagnostics.llm.state || (diagnostics.llm.ok ? 'ready' : 'blocked'),
        diagnostics.llm.model || diagnostics.llm.provider_id || 'model n/a',
        ...(diagnostics.llm.blocked_roles?.length ? [`blocked ${diagnostics.llm.blocked_roles.join(',')}`] : []),
      ]
    : ['checking'];

  return (
    <section
      className={cn(
        compact ? 'min-w-0' : 'border-b border-white/10 bg-slate-950/50 px-4 py-2',
      )}
      data-testid="director-readiness-diagnostics"
      aria-label="Director readiness diagnostics"
    >
      <details className="group h-full rounded-lg border border-indigo-500/15 bg-slate-900/35 px-3 py-2">
        <summary className="flex min-w-0 cursor-pointer list-none flex-wrap items-center gap-2 [&::-webkit-details-marker]:hidden">
          <div className="flex shrink-0 items-center gap-2 text-xs font-medium text-indigo-100">
            {blocked ? (
              <AlertTriangle className="h-3.5 w-3.5 text-amber-300" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" />
            )}
            交接
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            {isLoading ? (
              <span className="flex items-center gap-1 rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
                <Loader2 className="h-3 w-3 animate-spin text-indigo-300" />
                读取中
              </span>
            ) : error ? (
              <span className="rounded border border-red-500/25 bg-red-500/10 px-2 py-0.5 text-[10px] text-red-200">
                诊断异常
              </span>
            ) : diagnostics ? (
              <>
                <div
                  className={cn(
                    'flex shrink-0 items-center gap-1.5 rounded border px-2 py-0.5 text-[10px]',
                    blocked
                      ? 'border-amber-500/25 bg-amber-500/10 text-amber-200'
                      : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200',
                  )}
                  data-testid="director-readiness-state"
                >
                  {blocked ? <AlertTriangle className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
                  {blocked ? 'blocked' : 'ready'}
                </div>
                <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
                  ready {diagnostics.tasks.ready_to_execute}/{diagnostics.tasks.total}
                </span>
                <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
                  worker {diagnostics.workers.idle}/{diagnostics.workers.total} idle
                </span>
                <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
                  LLM {llmValues[0]}
                </span>
              </>
            ) : (
              <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-400">
                等待诊断快照
              </span>
            )}
          </div>
          <span className="ml-auto shrink-0 text-[10px] text-slate-500 group-open:hidden">详情</span>
          <span className="ml-auto hidden shrink-0 text-[10px] text-indigo-300 group-open:inline">收起</span>
        </summary>
        <div className="mt-2 flex min-w-0 items-center gap-3 border-t border-white/10 pt-2">
          <EvidenceEndpointBadge
            endpoint={evidenceEndpoint('/v2/director/diagnostics', workspace)}
            testId="director-readiness-endpoint"
          />

          {isLoading ? (
            <div className="flex items-center gap-2 text-[11px] text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-300" />
              正在读取任务队列与 worker 状态
            </div>
          ) : error ? (
            <div
              className="flex min-w-0 items-center gap-2 rounded border border-red-500/25 bg-red-500/10 px-2 py-1 text-[11px] text-red-200"
              data-testid="director-readiness-error"
            >
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{error}</span>
            </div>
          ) : diagnostics ? (
            <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
              <KernelStripMetric
                icon={<ListTodo className="h-3.5 w-3.5 text-cyan-300" />}
                label="任务"
                endpoint={diagnostics.tasks.source}
                values={[
                  `ready ${diagnostics.tasks.ready_to_execute}/${diagnostics.tasks.total}`,
                  ...(diagnostics.tasks.missing_blueprint_task_ids?.length
                    ? [`missing BP ${diagnostics.tasks.missing_blueprint_task_ids.length}`]
                    : []),
                  ...(diagnostics.tasks.invalid_blueprint_task_ids?.length
                    ? [`invalid BP ${diagnostics.tasks.invalid_blueprint_task_ids.length}`]
                    : []),
                  `blocked ${diagnostics.tasks.blocked}`,
                  `running ${diagnostics.tasks.running}`,
                ]}
              />
              <KernelStripMetric
                icon={<Layers className="h-3.5 w-3.5 text-emerald-300" />}
                label="Worker"
                endpoint="pool"
                values={[
                  `idle ${diagnostics.workers.idle}/${diagnostics.workers.total}`,
                  `busy ${diagnostics.workers.busy}`,
                  `bad ${diagnostics.workers.unhealthy}`,
                ]}
              />
              <KernelStripMetric
                icon={<Activity className="h-3.5 w-3.5 text-indigo-300" />}
                label="状态"
                endpoint={diagnostics.status.projection_source || 'projection'}
                values={[
                  diagnostics.status.running ? 'running' : diagnostics.status.state.toLowerCase(),
                  `src ${diagnostics.status.source || 'none'}`,
                ]}
              />
              <KernelStripMetric
                icon={<Zap className="h-3.5 w-3.5 text-amber-300" />}
                label="LLM"
                endpoint="/v2/llm/status"
                values={llmValues}
              />
              {visibleIssues.length > 0 ? (
                <div className="flex shrink-0 items-center gap-1" data-testid="director-readiness-issues">
                  {visibleIssues.map((issue) => (
                    <span
                      key={issue}
                      className="rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-200"
                      title={issue}
                    >
                      {formatDirectorDiagnosticIssue(issue)}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="text-[11px] text-slate-500">等待 Director 诊断快照</div>
          )}

          <Button
            variant="ghost"
            size="icon"
            onClick={onRefresh}
            disabled={isLoading}
            title="刷新 Director 交接诊断"
            className="ml-auto h-7 w-7 shrink-0 text-slate-400 hover:bg-indigo-500/10 hover:text-indigo-300"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', isLoading && 'animate-spin')} />
          </Button>
        </div>
      </details>
    </section>
  );
}

function KernelStripMetric({
  icon,
  label,
  endpoint,
  values,
}: {
  icon: React.ReactNode;
  label: string;
  endpoint: string;
  values: string[];
}) {
  return (
    <div className="flex min-w-[12rem] shrink-0 flex-wrap items-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-2 py-1">
      {icon}
      <span className="text-[10px] font-medium text-slate-200">{label}</span>
      <EvidenceEndpointBadge endpoint={endpoint} testId={`director-kernel-${label}-endpoint`} />
      <div className="flex items-center gap-1">
        {values.map((value) => (
          <span
            key={`${label}-${value}`}
            className="rounded border border-white/10 bg-slate-950/70 px-1.5 py-0.5 text-[9px] text-slate-300"
          >
            {value}
          </span>
        ))}
      </div>
    </div>
  );
}

export function DirectorWorkspace({
  workspace,
  onBackToMain,
  tasks,
  workers = [],
  directorRunning,
  isStarting,
  isStopping = false,
  startBlockedReason = '',
  onToggleDirector,
  onOpenSettings,
  currentTaskId,
  currentTaskTitle,
  currentTaskStatus,
  fileEditEvents = [],
  executionLogs = [],
  llmStreamEvents = [],
  processStreamEvents = [],
  currentPhase = 'idle',
  factoryMode = false,
  taskProgressMap = new Map(),
  taskTraceMap,
}: DirectorWorkspaceProps) {
  const [activeView, setActiveView] = useState<DirectorActiveView>('tasks');
  const [showAIDialogue, setShowAIDialogue] = useState(true);
  const [session] = useState<ExecutionSession>({
    id: `dir-${Date.now()}`,
    status: 'idle',
    logs: [],
  });
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [terminalOutput, setTerminalOutput] = useState<string>('');
  const [fallbackTasks, setFallbackTasks] = useState<PmTask[]>([]);
  const [backendWorkers, setBackendWorkers] = useState<RuntimeWorkerState[]>([]);
  const [workerFallbackError, setWorkerFallbackError] = useState<string | null>(null);
  const [workerBackendDetail, setWorkerBackendDetail] = useState<DirectorWorkerDetailState>({
    workerId: null,
    data: null,
    loading: false,
    error: null,
  });
  const [taskLLMEvents, setTaskLLMEvents] = useState<DirectorTaskLLMEventsState>({
    taskId: null,
    events: [],
    stats: null,
    loading: false,
    error: null,
  });
  const [taskCancelState, setTaskCancelState] = useState<DirectorTaskCancelState>({
    taskId: null,
    loading: false,
    message: null,
    error: null,
  });
  const [taskCreateState, setTaskCreateState] = useState<DirectorTaskCreateState>({
    loading: false,
    message: null,
    error: null,
    taskId: null,
  });
  const [directorRunEvidence, setDirectorRunEvidence] = useState<DirectorRunEvidenceState>({
    runId: null,
    loading: false,
    data: null,
    error: null,
  });
  const [directorRunCancelState, setDirectorRunCancelState] = useState<DirectorRunCancelState>({
    runId: null,
    loading: false,
    message: null,
    error: null,
  });
  const [directorToggleStatusEvidence, setDirectorToggleStatusEvidence] = useState<DirectorToggleStatusEvidenceState>({
    triggered: false,
    loading: false,
    data: null,
    error: null,
  });
  const [directorDiagnostics, setDirectorDiagnostics] = useState<DirectorDiagnosticsState>({
    loading: false,
    data: null,
    error: null,
  });
  const [taskBackendDetail, setTaskBackendDetail] = useState<DirectorTaskBackendDetailState>({
    taskId: null,
    data: null,
    loading: false,
    error: null,
  });
  const [capabilityHosts, setCapabilityHosts] = useState<DirectorCapabilityHost[]>([]);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [isCapabilityLoading, setIsCapabilityLoading] = useState(false);
  const [kernelCacheStats, setKernelCacheStats] = useState<RoleKernelCacheStats | null>(null);
  const [kernelLLMEvents, setKernelLLMEvents] = useState<RoleKernelLLMEventsResponse | null>(null);
  const [kernelTokenBudgetStats, setKernelTokenBudgetStats] = useState<RoleKernelTokenBudgetStats | null>(null);
  const [kernelDiagnosticsError, setKernelDiagnosticsError] = useState<string | null>(null);
  const [isKernelDiagnosticsLoading, setIsKernelDiagnosticsLoading] = useState(false);
  const [isKernelCacheClearing, setIsKernelCacheClearing] = useState(false);

  // 用户手动切换视图的标记
  const userSwitchedViewRef = useRef(false);
  const lastPhaseRef = useRef<string>('');

  // 阶段到视图的映射
  const PHASE_TO_VIEW: Record<string, { view: DirectorActiveView; label: string }> = {
    'idle': { view: 'tasks', label: '等待' },
    'planning': { view: 'tasks', label: '规划' },
    'analyzing': { view: 'activity', label: '分析' },
    'executing': { view: 'code', label: '代码' },
    'llm_calling': { view: 'activity', label: '思考' },
    'tool_running': { view: 'terminal', label: '执行' },
    'verification': { view: 'activity', label: '验证' },
    'completed': { view: 'tasks', label: '完成' },
    'error': { view: 'activity', label: '错误' },
  };

  // 自动切换视图基于当前阶段
  useEffect(() => {
    if (!directorRunning || userSwitchedViewRef.current) return;

    const phaseConfig = PHASE_TO_VIEW[currentPhase] || PHASE_TO_VIEW['idle'];

    if (currentPhase !== lastPhaseRef.current) {
      lastPhaseRef.current = currentPhase;

      if (phaseConfig.view !== activeView) {
        setActiveView(phaseConfig.view);
      }
    }
  }, [currentPhase, directorRunning, activeView]);

  // 用户手动点击导航时记录偏好
  const handleViewChange = useCallback((view: DirectorActiveView) => {
    userSwitchedViewRef.current = true;
    setActiveView(view);
  }, []);

  useEffect(() => {
    if (!workspace || factoryMode) {
      setCapabilityHosts([]);
      setCapabilityError(null);
      return;
    }

    let cancelled = false;

    const loadCapabilities = async () => {
      setIsCapabilityLoading(true);
      setCapabilityError(null);

      const result = await getDirectorCapabilities();
      if (cancelled) return;

      if (result.ok && result.data) {
        setCapabilityHosts(normalizeDirectorCapabilityHosts(result.data));
      } else {
        setCapabilityHosts([]);
        setCapabilityError(result.error || 'Director capability matrix unavailable');
      }

      setIsCapabilityLoading(false);
    };

    void loadCapabilities();

    return () => {
      cancelled = true;
    };
  }, [factoryMode, workspace]);

  const loadKernelDiagnostics = useCallback(async () => {
    if (!workspace || factoryMode) {
      setKernelCacheStats(null);
      setKernelLLMEvents(null);
      setKernelTokenBudgetStats(null);
      setKernelDiagnosticsError(null);
      return;
    }

    setIsKernelDiagnosticsLoading(true);
    setKernelDiagnosticsError(null);

    try {
      const [cacheResult, tokenResult, llmResult] = await Promise.all([
        getRoleKernelCacheStats('director'),
        getRoleKernelTokenBudgetStats('director'),
        getRoleKernelLLMEvents('director', { role: 'director', limit: 5, workspace }),
      ]);
      const errors: string[] = [];

      if (cacheResult.ok && cacheResult.data) {
        setKernelCacheStats(cacheResult.data);
      } else {
        setKernelCacheStats(null);
        errors.push(cacheResult.error || 'Director LLM cache stats unavailable');
      }

      if (tokenResult.ok && tokenResult.data) {
        setKernelTokenBudgetStats(tokenResult.data);
      } else {
        setKernelTokenBudgetStats(null);
        errors.push(tokenResult.error || 'Director token budget stats unavailable');
      }

      if (llmResult.ok && llmResult.data) {
        setKernelLLMEvents(llmResult.data);
      } else {
        setKernelLLMEvents(null);
        errors.push(llmResult.error || 'Director LLM events unavailable');
      }

      setKernelDiagnosticsError(errors.length > 0 ? errors.join('；') : null);
    } catch (err) {
      setKernelCacheStats(null);
      setKernelLLMEvents(null);
      setKernelTokenBudgetStats(null);
      setKernelDiagnosticsError(err instanceof Error ? err.message : 'Director Kernel diagnostics unavailable');
    } finally {
      setIsKernelDiagnosticsLoading(false);
    }
  }, [factoryMode, workspace]);

  useEffect(() => {
    void loadKernelDiagnostics();
  }, [loadKernelDiagnostics]);

  const loadDirectorDiagnostics = useCallback(async () => {
    if (!workspace) {
      setDirectorDiagnostics({
        loading: false,
        data: null,
        error: null,
      });
      return;
    }

    setDirectorDiagnostics((prev) => ({
      ...prev,
      loading: true,
      error: null,
    }));

    try {
      const result = await getDirectorDiagnostics(workspace);
      if (result.ok && result.data) {
        setDirectorDiagnostics({
          loading: false,
          data: result.data,
          error: null,
        });
      } else {
        setDirectorDiagnostics({
          loading: false,
          data: null,
          error: result.error || 'Director diagnostics unavailable',
        });
      }
    } catch (err) {
      setDirectorDiagnostics({
        loading: false,
        data: null,
        error: err instanceof Error ? err.message : 'Director diagnostics unavailable',
      });
    }
  }, [workspace]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const syncDiagnostics = async () => {
      if (cancelled) {
        return;
      }
      await loadDirectorDiagnostics();
    };

    void syncDiagnostics();
    if (workspace) {
      timer = setInterval(() => {
        void syncDiagnostics();
      }, directorRunning ? 2500 : 7000);
    }

    return () => {
      cancelled = true;
      if (timer) {
        clearInterval(timer);
      }
    };
  }, [directorRunning, loadDirectorDiagnostics, workspace]);

  const handleClearKernelCache = useCallback(async () => {
    setIsKernelCacheClearing(true);
    setKernelDiagnosticsError(null);
    try {
      const result = await clearRoleKernelCache('director');
      if (result.ok) {
        await loadKernelDiagnostics();
      } else {
        setKernelDiagnosticsError(result.error || 'Director LLM cache clear failed');
      }
    } catch (err) {
      setKernelDiagnosticsError(err instanceof Error ? err.message : 'Director LLM cache clear failed');
    } finally {
      setIsKernelCacheClearing(false);
    }
  }, [loadKernelDiagnostics]);

  useEffect(() => {
    if (!workspace) {
      setFallbackTasks([]);
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const syncTasks = async () => {
      try {
        const result = await listDirectorTaskFallbackRows(directorRunning, workspace);
        if (cancelled) {
          return;
        }
        if (result.ok && Array.isArray(result.data)) {
          setFallbackTasks(result.data as unknown as PmTask[]);
        }
      } catch {
        // Ignore polling errors and keep using live push data.
      }
    };

    void syncTasks();
    timer = setInterval(() => {
      void syncTasks();
    }, directorRunning ? 1500 : 4000);

    return () => {
      cancelled = true;
      if (timer) {
        clearInterval(timer);
      }
    };
  }, [workspace, directorRunning]);

  useEffect(() => {
    if (!workspace) {
      setBackendWorkers([]);
      setWorkerFallbackError(null);
      setWorkerBackendDetail({
        workerId: null,
        data: null,
        loading: false,
        error: null,
      });
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const syncWorkers = async () => {
      try {
        const result = await listDirectorWorkers(workspace);
        if (cancelled) {
          return;
        }
        if (result.ok && Array.isArray(result.data)) {
          setBackendWorkers(normalizeDirectorWorkerRows(result.data));
          setWorkerFallbackError(null);
        } else {
          setWorkerFallbackError(result.error || 'Director worker backend unavailable');
        }
      } catch (err) {
        if (!cancelled) {
          setWorkerFallbackError(err instanceof Error ? err.message : 'Director worker backend unavailable');
        }
      }
    };

    void syncWorkers();
    timer = setInterval(() => {
      void syncWorkers();
    }, directorRunning ? 2500 : 6000);

    return () => {
      cancelled = true;
      if (timer) {
        clearInterval(timer);
      }
    };
  }, [workspace, directorRunning]);

  useEffect(() => {
    const taskId = String(selectedTaskId || '').trim();
    if (!taskId) {
      setTaskBackendDetail({
        taskId: null,
        data: null,
        loading: false,
        error: null,
      });
      setTaskLLMEvents({
        taskId: null,
        events: [],
        stats: null,
        loading: false,
        error: null,
      });
      return;
    }

    let detailCancelled = false;
    setTaskBackendDetail((current) => ({
      taskId,
      data: current.taskId === taskId ? current.data : null,
      loading: true,
      error: null,
    }));

    const loadTaskBackendDetail = async () => {
      const result = await getDirectorTask(taskId, workspace);
      if (detailCancelled) {
        return;
      }
      if (result.ok && result.data) {
        setTaskBackendDetail({
          taskId,
          data: result.data,
          loading: false,
          error: null,
        });
      } else {
        setTaskBackendDetail({
          taskId,
          data: null,
          loading: false,
          error: result.error || 'Director task detail unavailable',
        });
      }
    };

    void loadTaskBackendDetail();

    let cancelled = false;
    setTaskLLMEvents((current) => ({
      taskId,
      events: current.taskId === taskId ? current.events : [],
      stats: current.taskId === taskId ? current.stats : null,
      loading: true,
      error: null,
    }));

    const loadTaskLLMEvents = async () => {
      const result = await getDirectorTaskKernelLLMEvents(taskId, { limit: 25, workspace });
      if (cancelled) {
        return;
      }
      if (result.ok && result.data) {
        setTaskLLMEvents({
          taskId,
          events: Array.isArray(result.data.events) ? result.data.events : [],
          stats: result.data.stats || null,
          loading: false,
          error: null,
        });
      } else {
        setTaskLLMEvents({
          taskId,
          events: [],
          stats: null,
          loading: false,
          error: result.error || 'Director task LLM events unavailable',
        });
      }
    };

    void loadTaskLLMEvents();

    return () => {
      detailCancelled = true;
      cancelled = true;
    };
  }, [selectedTaskId, workspace]);

  const visibleTasks = useMemo(() => {
    const toTaskId = (task: PmTask): string => String(task.id || '').trim();
    const merged = new Map<string, PmTask>();

    // Live realtime rows own volatile state; fallback rows fill the task contract details
    // that runtime projection may omit.
    for (const task of fallbackTasks) {
      const taskId = toTaskId(task);
      if (taskId) {
        merged.set(taskId, task);
      }
    }

    for (const task of tasks) {
      const taskId = toTaskId(task);
      if (taskId) {
        const existing = merged.get(taskId);
        merged.set(taskId, existing ? mergeTaskRows(existing, task) : task);
      }
    }

    const orderedIds: string[] = [];
    for (const task of fallbackTasks) {
      const taskId = toTaskId(task);
      if (taskId && !orderedIds.includes(taskId)) {
        orderedIds.push(taskId);
      }
    }
    for (const task of tasks) {
      const taskId = toTaskId(task);
      if (taskId && !orderedIds.includes(taskId)) {
        orderedIds.push(taskId);
      }
    }

    return orderedIds
      .map((taskId) => merged.get(taskId))
      .filter((task): task is PmTask => Boolean(task));
  }, [tasks, fallbackTasks]);

  const visibleWorkers = useMemo(
    () => mergeDirectorWorkers(workers, backendWorkers),
    [workers, backendWorkers],
  );

  const taskRealtimeTelemetry = useMemo(
    () => buildTaskRealtimeTelemetry(visibleTasks, fileEditEvents, taskProgressMap),
    [visibleTasks, fileEditEvents, taskProgressMap],
  );

  const executionTasks: ExecutionTask[] = visibleTasks.map((task) => {
    const metadata = readTaskMetadata(task);
    const adapterResult = (metadata.adapter_result && typeof metadata.adapter_result === 'object')
      ? metadata.adapter_result as Record<string, unknown>
      : {};
    const adapterChangedFiles = [
      ...readStringList(adapterResult.new_files),
      ...readStringList(adapterResult.modified_files),
      ...readStringList(adapterResult.deleted_files),
      ...readStringList(adapterResult.changed_files),
    ].filter((item, index, all) => Boolean(item) && all.indexOf(item) === index);
    const taskId = String(task.id || '').trim();
    const rawStatus = String(task.status || task.state || '').trim().toLowerCase();
    const isCurrent = currentTaskId
      ? task.id === currentTaskId
      : currentTaskTitle
        ? (task.title || task.subject || task.goal || '').trim() === String(currentTaskTitle || '').trim()
        : false;
    const status = resolveTaskExecutionStatus({
      rawStatus,
      done: Boolean(task.done),
      completed: Boolean(task.completed),
      directorRunning,
      isCurrent,
    });

    const title = readTaskString(task, ['title', 'subject', 'goal', 'id']) || '未命名任务';
    const goal = readTaskString(task, ['goal', 'pm_task_goal', 'summary']);
    const description = readTaskString(task, ['description', 'goal', 'summary']);
    const lowered = `${title} ${goal}`.toLowerCase();
    const type: ExecutionTask['type'] = lowered.includes('test')
      ? 'test'
      : lowered.includes('debug') || lowered.includes('fix')
        ? 'debug'
        : lowered.includes('review') || lowered.includes('audit')
          ? 'review'
          : 'code';

    const budgetRaw = (metadata.budget && typeof metadata.budget === 'object')
      ? metadata.budget
      : task.budget;
    const budgetInfo = budgetRaw && typeof budgetRaw === 'object'
      ? {
        used: Number((budgetRaw as Record<string, number>).used) || 0,
        total: Number((budgetRaw as Record<string, number>).total) || 100,
        unit: ((budgetRaw as Record<string, string>).unit || 'tokens') as 'tokens' | 'requests' | 'time',
      }
      : undefined;

    const createdAt = task.created_at || task.createdAt;
    const startedAt = task.started_at || task.startedAt;
    const completedAt = task.completed_at || task.completedAt;

    let actualTime: number | undefined;
    if (completedAt && startedAt) {
      actualTime = new Date(completedAt).getTime() - new Date(startedAt).getTime();
    } else if (startedAt && status === 'running') {
      actualTime = Date.now() - new Date(startedAt).getTime();
    }

    const priorityValue = readTaskString(task, ['priority']) || 'medium';
    const dependencies = task.dependencies
      || task.blocked_by
      || (Array.isArray(metadata.dependencies) ? metadata.dependencies : undefined);
    const blockedBy = readTaskStringList(task, ['blocked_by', 'blockedBy']);
    const tags = task.tags || (Array.isArray(metadata.tags) ? metadata.tags : []);
    const telemetry = taskRealtimeTelemetry.get(taskId);
    const filesModified = Math.max(
      Number(task.files_modified || metadata.files_modified || 0) || 0,
      adapterChangedFiles.length,
      telemetry?.filesTouchedCount || 0,
    );
    const retries = Number(
      task.retries
      || task.retry_count
      || metadata.retry_count
      || metadata.retries
      || 0,
    ) || 0;
    const assignedWorker = readTaskString(task, [
      'assigned_worker',
      'worker_id',
      'claimed_by',
      'assignedTo',
      'assignee',
    ]);
    const claimedBy = readTaskString(task, ['claimed_by', 'claimedBy', 'worker_id']);
    const identityTokens = new Set(resolveTaskIdentityCandidates(task));
    const taskScopedFileEvents = fileEditEvents.filter((event) => {
      const token = toTaskToken(event.taskId);
      return Boolean(token && identityTokens.has(token));
    });

    const progressFromTelemetry =
      telemetry?.phaseIndex !== undefined
        && telemetry?.phaseTotal !== undefined
        && telemetry.phaseTotal > 0
        ? Math.min(99, Math.max(1, Math.round((telemetry.phaseIndex / telemetry.phaseTotal) * 100)))
        : undefined;

    return {
      id: String(task.id || title),
      name: title,
      rawStatus,
      goal,
      description,
      status,
      type,
      priority: String(priorityValue).toLowerCase() as ExecutionTask['priority'],
      progress: status === 'running' ? (progressFromTelemetry ?? 50) : status === 'completed' ? 100 : status === 'failed' ? 0 : undefined,
      output: readTaskString(task, ['summary', 'output', 'result_summary']),
      error: status === 'failed' || status === 'blocked'
        ? readTaskString(task, ['error', 'error_detail', 'state', 'status'])
        : '',
      budget: budgetInfo,
      estimatedTime: task.estimated_time || task.estimatedTime,
      actualTime,
      dependencies: Array.isArray(dependencies) ? dependencies.map((item) => String(item)) : undefined,
      blockedBy,
      tags: Array.isArray(tags) ? tags.map((tag) => String(tag)) : [],
      createdAt,
      startedAt,
      completedAt,
      assignedWorker: assignedWorker || undefined,
      claimedBy: claimedBy || undefined,
      pmTaskId: readTaskString(task, ['pm_task_id', 'task_id']) || taskId || undefined,
      blueprintId: readTaskString(task, ['blueprint_id', 'blueprintId']) || undefined,
      blueprintPath: readTaskString(task, ['blueprint_path', 'runtime_blueprint_path']) || undefined,
      source: readTaskString(task, ['director_task_source', 'source']) || undefined,
      filesModified,
      executionSteps: readTaskStringList(task, ['execution_steps', 'executionSteps', 'execution_checklist', 'steps', 'checklist']),
      acceptanceCriteria: [
        ...readStringList(task.acceptance),
        ...readTaskStringList(task, ['acceptance_criteria', 'acceptanceCriteria', 'acceptance']),
      ].filter((item, index, all) => all.indexOf(item) === index),
      targetFiles: [
        ...readTaskStringList(task, ['target_files', 'scope_paths', 'files', 'targetFiles']),
        ...adapterChangedFiles,
      ].filter((item, index, all) => all.indexOf(item) === index),
      // Progress tracking from telemetry (merged from taskProgressMap and fileEditEvents)
      retries: telemetry?.retryCount ?? retries,
      maxRetries: telemetry?.maxRetries,
      currentFilePath: telemetry?.currentFilePath || readTaskString(task, ['current_file', 'current_file_path']) || adapterChangedFiles.at(-1),
      activityUpdatedAt: telemetry?.activityUpdatedAt,
      lineStats: telemetry?.lineStats || (metadata.line_stats as TaskLineStats | undefined),
      operationStats: telemetry?.operationStats || (metadata.operation_stats as TaskOperationStats | undefined),
      currentPhase: telemetry?.currentPhase,
      phaseIndex: telemetry?.phaseIndex,
      phaseTotal: telemetry?.phaseTotal,
      taskScopedFileEvents,
    };
  });
  const executionTaskMap = useMemo(() => {
    const mapping = new Map<string, ExecutionTask>();
    executionTasks.forEach((task) => mapping.set(task.id, task));
    return mapping;
  }, [executionTasks]);
  const directorStarting = Boolean(isStarting);
  const directorStopping = Boolean(isStopping);
  const isExecuting = directorRunning || directorStarting || directorStopping;
  const sessionStatus = resolveSessionStatus(directorRunning || directorStopping, directorStarting, executionTasks);

  const handleTaskSelect = useCallback((taskId: string) => {
    setSelectedTaskId(taskId);
    setTaskCancelState({
      taskId,
      loading: false,
      message: null,
      error: null,
    });
    const task = executionTasks.find(t => t.id === taskId);
    if (task) {
      setTerminalOutput(`选中任务: ${task.name}\n状态: ${task.status}\n类型: ${task.type}\n`);
    }
  }, [executionTasks]);

  const handleWorkerSelect = useCallback(async (workerId: string) => {
    const normalizedWorkerId = String(workerId || '').trim();
    if (!normalizedWorkerId) {
      return;
    }

    setWorkerBackendDetail({
      workerId: normalizedWorkerId,
      data: null,
      loading: true,
      error: null,
    });
    setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] 读取 Director worker: ${normalizedWorkerId}\n`);

    try {
      const result = await getDirectorWorker(normalizedWorkerId, workspace);
      if (!result.ok || !result.data) {
        setWorkerBackendDetail({
          workerId: normalizedWorkerId,
          data: null,
          loading: false,
          error: result.error || 'Director worker detail unavailable',
        });
        return;
      }
      setWorkerBackendDetail({
        workerId: normalizedWorkerId,
        data: result.data,
        loading: false,
        error: null,
      });
    } catch (error) {
      setWorkerBackendDetail({
        workerId: normalizedWorkerId,
        data: null,
        loading: false,
        error: error instanceof Error ? error.message : 'Director worker detail unavailable',
      });
    }
  }, [workspace]);

  const handleTaskCreate = useCallback(async (draft: DirectorTaskCreateDraft) => {
    const subject = String(draft.subject || '').trim();
    if (!subject) {
      return;
    }
    const selectedTask = selectedTaskId ? executionTaskMap.get(selectedTaskId) || null : null;
    const selectedTaskIdForMetadata = selectedTask?.pmTaskId || selectedTask?.id || `director-desktop-${Date.now()}`;
    const acceptance = selectedTask?.acceptanceCriteria?.length
      ? selectedTask.acceptanceCriteria
      : [`Desktop-created Director task: ${subject}`];
    const payload: CreateDirectorTaskPayload = {
      subject,
      description: String(draft.description || subject).trim() || subject,
      command: null,
      priority: draft.priority,
      timeout_seconds: Math.max(30, Math.round(Number(draft.timeoutSeconds) || 300)),
      metadata: {
        pm_task_id: selectedTaskIdForMetadata,
        pm_task_title: selectedTask?.name || subject,
        pm_task_status: selectedTask?.status || 'desktop_created',
        acceptance,
        blueprint_id: selectedTask?.blueprintId || null,
        blueprint_path: selectedTask?.blueprintPath || null,
        runtime_blueprint_path: selectedTask?.blueprintPath || null,
        guardrails: {
          source: 'director_desktop_task_create',
        },
        context_snapshot_ref: null,
      },
    };

    setTaskCreateState({
      loading: true,
      message: null,
      error: null,
      taskId: null,
    });
    setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] 创建 Director 任务: ${subject}\n`);

    try {
      const result = await createDirectorTask(payload, workspace);
      if (!result.ok || !result.data) {
        setTaskCreateState({
          loading: false,
          message: null,
          error: result.error || 'Director task create failed',
          taskId: null,
        });
        return;
      }

      const createdTaskId = String(result.data.id || result.data.task_id || subject).trim();
      const createdTask = normalizeDirectorCreatedTaskRow(result.data, payload, createdTaskId);
      if (createdTask) {
        setFallbackTasks((current) => upsertDirectorFallbackTaskRow(current, createdTask));
      }
      setTaskCreateState({
        loading: false,
        message: `已创建 Director 任务: ${createdTaskId}`,
        error: null,
        taskId: createdTaskId,
      });
      setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director 任务已创建: ${createdTaskId}\n`);
      if (createdTaskId) {
        setSelectedTaskId(createdTaskId);
      }

      try {
        const refreshed = await listDirectorTaskFallbackRows(directorRunning, workspace);
        if (refreshed.ok && Array.isArray(refreshed.data)) {
          const refreshedTasks = refreshed.data as unknown as PmTask[];
          setFallbackTasks(createdTask ? upsertDirectorFallbackTaskRow(refreshedTasks, createdTask) : refreshedTasks);
        }
      } catch {
        // The create evidence is still valid if the best-effort list refresh fails.
      }
    } catch (error) {
      setTaskCreateState({
        loading: false,
        message: null,
        error: error instanceof Error ? error.message : 'Director task create failed',
        taskId: null,
      });
    }
  }, [directorRunning, executionTaskMap, selectedTaskId, workspace]);

  const handleTaskCancel = useCallback(async (taskId: string) => {
    const normalizedTaskId = String(taskId || '').trim();
    if (!normalizedTaskId) {
      return;
    }

    const startedAt = new Date().toLocaleTimeString();
    setTaskCancelState({
      taskId: normalizedTaskId,
      loading: true,
      message: null,
      error: null,
    });
    setTerminalOutput((prev) => `${prev}[${startedAt}] 请求取消 Director 任务: ${normalizedTaskId}\n`);

    try {
      const result = await cancelDirectorTask(normalizedTaskId, workspace);
      if (!result.ok || !result.data) {
        const error = result.error || 'Director task cancel failed';
        setTaskCancelState({
          taskId: normalizedTaskId,
          loading: false,
          message: null,
          error,
        });
        setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director 任务取消失败: ${error}\n`);
        return;
      }

      const responseTaskId = String(result.data.task_id || result.data.id || normalizedTaskId).trim();
      const status = String(result.data.status || '').trim();
      const message = status
        ? `取消请求已提交: ${responseTaskId} (${status})`
        : `取消请求已提交: ${responseTaskId}`;
      setTaskCancelState({
        taskId: normalizedTaskId,
        loading: false,
        message,
        error: null,
      });
      setTerminalOutput((prev) =>
        `${prev}[${new Date().toLocaleTimeString()}] Director 任务取消请求已提交: ${responseTaskId}${status ? ` status=${status}` : ''}\n`,
      );

      try {
        const refreshed = await listDirectorTaskFallbackRows(directorRunning, workspace);
        if (refreshed.ok && Array.isArray(refreshed.data)) {
          setFallbackTasks(refreshed.data as unknown as PmTask[]);
        }
      } catch {
        // Keep the submitted cancellation evidence visible even if the best-effort refresh fails.
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Director task cancel failed');
      setTaskCancelState({
        taskId: normalizedTaskId,
        loading: false,
        message: null,
        error: message,
      });
      setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director 任务取消失败: ${message}\n`);
    }
  }, [directorRunning, workspace]);

  const loadDirectorRunEvidence = useCallback(async (
    runId: string,
    options: LoadDirectorRunEvidenceOptions = {},
  ) => {
    const normalizedRunId = String(runId || '').trim();
    if (!normalizedRunId) {
      return;
    }

    setDirectorRunEvidence((current) => ({
      runId: normalizedRunId,
      loading: true,
      data: options.preserveData && current.runId === normalizedRunId ? current.data : null,
      error: null,
    }));
    if (!options.preserveCancel) {
      setDirectorRunCancelState({
        runId: normalizedRunId,
        loading: false,
        message: null,
        error: null,
      });
    }

    try {
      const result = await getDirectorRun(normalizedRunId, workspace);
      if (!result.ok || !result.data) {
        setDirectorRunEvidence({
          runId: normalizedRunId,
          loading: false,
          data: null,
          error: result.error || 'Director run evidence unavailable',
        });
        return;
      }

      setDirectorRunEvidence({
        runId: normalizedRunId,
        loading: false,
        data: result.data,
        error: null,
      });
    } catch (error) {
      setDirectorRunEvidence({
        runId: normalizedRunId,
        loading: false,
        data: null,
        error: error instanceof Error ? error.message : 'Director run evidence unavailable',
      });
    }
  }, [workspace]);

  const handleCancelDirectorRun = useCallback(async () => {
    const normalizedRunId = String(directorRunEvidence.runId || '').trim();
    if (!normalizedRunId) {
      return;
    }

    setDirectorRunCancelState({
      runId: normalizedRunId,
      loading: true,
      message: null,
      error: null,
    });
    setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] 请求取消 Director run: ${normalizedRunId}\n`);

    try {
      const result = await cancelDirectorRun(normalizedRunId, workspace);
      if (!result.ok || !result.data) {
        const error = result.error || 'Director run cancel failed';
        setDirectorRunCancelState({
          runId: normalizedRunId,
          loading: false,
          message: null,
          error,
        });
        setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director run 取消失败: ${error}\n`);
        return;
      }

      const statusText = String(result.data.status || 'unknown').trim() || 'unknown';
      setDirectorRunEvidence({
        runId: normalizedRunId,
        loading: false,
        data: result.data,
        error: null,
      });
      setDirectorRunCancelState({
        runId: normalizedRunId,
        loading: false,
        message: `取消运行已提交: ${statusText}`,
        error: null,
      });
      setTerminalOutput((prev) =>
        `${prev}[${new Date().toLocaleTimeString()}] Director run 取消请求已提交: ${normalizedRunId} status=${statusText}\n`,
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Director run cancel failed');
      setDirectorRunCancelState({
        runId: normalizedRunId,
        loading: false,
        message: null,
        error: message,
      });
      setTerminalOutput((prev) => `${prev}[${new Date().toLocaleTimeString()}] Director run 取消失败: ${message}\n`);
    }
  }, [directorRunEvidence.runId, workspace]);

  const toggleDirectorWithStatusEvidence = useCallback(async () => {
    setDirectorToggleStatusEvidence({
      triggered: true,
      loading: true,
      data: null,
      error: null,
    });
    try {
      await Promise.resolve(onToggleDirector());
      const statusResult = await getDirectorStatus(workspace);
      if (statusResult.ok && statusResult.data) {
        setDirectorToggleStatusEvidence({
          triggered: true,
          loading: false,
          data: statusResult.data,
          error: null,
        });
        return;
      }
      setDirectorToggleStatusEvidence({
        triggered: true,
        loading: false,
        data: null,
        error: statusResult.error || 'Director status unavailable',
      });
    } catch (error) {
      setDirectorToggleStatusEvidence({
        triggered: true,
        loading: false,
        data: null,
        error: error instanceof Error ? error.message : 'Director status unavailable',
      });
    }
  }, [onToggleDirector, workspace]);

  const directorDiagnosticExecutionReason = useMemo(
    () => formatDirectorExecutionBlockReason(directorDiagnostics.data),
    [directorDiagnostics.data],
  );
  const executionBlockReasonForStart = factoryMode
    ? '工厂模式下由 Factory 编排 Director，不能在嵌入层直接启动。'
    : !directorRunning
      ? startBlockedReason || directorDiagnosticExecutionReason
      : '';
  const directorToggleBusy = directorToggleStatusEvidence.loading;
  const directorControlBusyReason = directorStarting
    ? 'Director 正在启动，请等待状态回传。'
    : directorStopping
      ? 'Director 正在停止，请等待状态回传。'
      : directorToggleBusy
        ? 'Director 状态确认中，请等待后端回传。'
        : '';
  const executionDisabledReason = executionBlockReasonForStart || directorControlBusyReason;
  const directorPrimaryActionLabel = directorStarting
    ? '启动中'
    : directorStopping
      ? '停止中'
      : directorRunning
        ? '停止'
        : '执行';

  const handleExecute = useCallback(async () => {
    if (directorControlBusyReason) {
      setTerminalOutput(prev => `${prev}[${new Date().toLocaleTimeString()}] Director 控制请求等待中: ${directorControlBusyReason}\n`);
      return;
    }

    if (!directorRunning && executionBlockReasonForStart) {
      setTerminalOutput(prev => `${prev}[${new Date().toLocaleTimeString()}] Director 启动被阻断: ${executionBlockReasonForStart}\n`);
      return;
    }

    const nextAction = directorRunning ? '停止' : '启动';
    const targetName = selectedTaskId
      ? executionTasks.find((task) => task.id === selectedTaskId)?.name || selectedTaskId
      : currentTaskTitle || '当前任务队列';
    const newLog = `[${new Date().toLocaleTimeString()}] ${nextAction} Director 执行: ${targetName}`;
    setTerminalOutput(prev => prev + newLog + '\n');

    if (directorRunning) {
      await toggleDirectorWithStatusEvidence();
      return;
    }

    const payload: RunDirectorPayload = {
      workspace,
      execution_mode: 'parallel',
    };
    if (selectedTaskId) {
      payload.task_id = selectedTaskId;
      payload.task_filter = selectedTaskId;
    }

    const result = await runDirector(payload);
    if (!result.ok || !result.data) {
      setTerminalOutput(prev => `${prev}[${new Date().toLocaleTimeString()}] Director 任务启动失败: ${result.error || 'unknown error'}\n`);
      return;
    }
    const data = result.data;
    setTerminalOutput(prev => `${prev}[${new Date().toLocaleTimeString()}] Director run 已创建: ${data.run_id} queued=${data.tasks_queued}\n`);
    if (data.run_id) {
      void loadDirectorRunEvidence(data.run_id);
    }
  }, [
    currentTaskTitle,
    directorControlBusyReason,
    directorRunning,
    executionTasks,
    executionBlockReasonForStart,
    loadDirectorRunEvidence,
    selectedTaskId,
    toggleDirectorWithStatusEvidence,
    workspace,
  ]);

  const handlePause = useCallback(async () => {
    if (!directorRunning || directorControlBusyReason) {
      return;
    }
    setTerminalOutput(prev => prev + `[${new Date().toLocaleTimeString()}] 停止 Director 执行\n`);
    await toggleDirectorWithStatusEvidence();
  }, [directorControlBusyReason, directorRunning, toggleDirectorWithStatusEvidence]);

  const handleReset = useCallback(() => {
    setSelectedTaskId(null);
    setTerminalOutput('');
  }, []);

  const handleClearTerminal = useCallback(() => {
    setTerminalOutput('');
  }, []);

  useEffect(() => {
    const statusText = String(currentTaskStatus || '').trim();
    if (directorRunning) {
      const currentLabel = String(currentTaskTitle || currentTaskId || '等待任务').trim();
      setTerminalOutput((prev) => {
        const nextLine = `[${new Date().toLocaleTimeString()}] Director 运行中: ${currentLabel}${statusText ? ` (${statusText})` : ''}\n`;
        if (prev.includes(nextLine)) {
          return prev;
        }
        return prev + nextLine;
      });
      return;
    }
    if (statusText) {
      setTerminalOutput((prev) => {
        const nextLine = `[${new Date().toLocaleTimeString()}] Director 状态: ${statusText}\n`;
        if (prev.includes(nextLine)) {
          return prev;
        }
        return prev + nextLine;
      });
    }
  }, [currentTaskId, currentTaskStatus, currentTaskTitle, directorRunning]);

  const runningTasks = executionTasks.filter(t => t.status === 'running').length;
  const completedTasks = executionTasks.filter(t => t.status === 'completed').length;
  const failedTasks = executionTasks.filter(t => t.status === 'failed').length;
  const pendingTasks = executionTasks.filter(t => t.status === 'pending').length;
  const totalTasks = executionTasks.length;
  const progress = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
  const handleRefreshDirectorRun = useCallback(() => {
    const normalizedRunId = String(directorRunEvidence.runId || '').trim();
    if (!normalizedRunId) return;
    void loadDirectorRunEvidence(normalizedRunId, {
      preserveData: true,
      preserveCancel: true,
    });
  }, [directorRunEvidence.runId, loadDirectorRunEvidence]);

  const directorRunCancelDisabled =
    !directorRunEvidence.runId ||
    directorRunEvidence.loading ||
    directorRunCancelState.loading ||
    isDirectorRunTerminal(directorRunEvidence.data?.status);
  const directorRunAutoRefreshActive = Boolean(directorRunEvidence.runId)
    && !directorRunEvidence.loading
    && !directorRunCancelState.loading
    && !directorRunEvidence.error
    && !isDirectorRunTerminal(directorRunEvidence.data?.status);
  const shouldShowSideAIDialogue = showAIDialogue && activeView !== 'workbench' && activeView !== 'strategy';

  useEffect(() => {
    const normalizedRunId = String(directorRunEvidence.runId || '').trim();
    if (!normalizedRunId || !directorRunAutoRefreshActive) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void loadDirectorRunEvidence(normalizedRunId, {
        preserveData: true,
        preserveCancel: true,
      });
    }, DIRECTOR_RUN_EVIDENCE_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [directorRunAutoRefreshActive, directorRunEvidence.runId, loadDirectorRunEvidence]);

  return (
    <div data-testid="director-workspace" className="flex flex-col h-full bg-gradient-to-br from-[var(--ink-indigo)] via-[rgba(28,18,48,0.8)] to-[rgba(14,20,40,0.95)] text-slate-100 overflow-hidden">
      {/* Director Header - Director 主题 */}
      {!factoryMode && (
      <header className="h-14 flex items-center justify-between px-4 border-b border-indigo-500/20 bg-gradient-to-r from-slate-900 via-slate-900 to-indigo-950/20">
        <div className="flex items-center gap-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBackToMain}
            data-testid="director-workspace-back"
            className="text-slate-400 hover:text-slate-100 hover:bg-white/5"
          >
            <ChevronLeft className="w-4 h-4 mr-1" />
            返回
          </Button>

          <div className="flex items-center gap-3">
            <div className="relative">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-indigo-700 flex items-center justify-center shadow-lg shadow-indigo-500/20">
                <Hammer className="w-4 h-4 text-indigo-100" />
              </div>
              {sessionStatus === 'running' && (
                <div className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-indigo-500 animate-pulse" />
              )}
            </div>
            <div>
              <h1 className="text-sm font-semibold text-indigo-100">Director</h1>
              <p className="text-[10px] text-indigo-500/70 uppercase tracking-wider">Director Console</p>
            </div>
          </div>
        </div>

        {/* 中央执行状态 */}
        <div className="flex items-center gap-4">
          {/* 实时任务统计 */}
          <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-white/5 border border-white/10">
            <Clock className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs text-slate-400">未领取:</span>
            <span className="text-xs font-mono text-slate-300 min-w-[20px] text-center">
              {pendingTasks}
            </span>
            <span className="text-slate-600">|</span>
            <Loader2 className="w-3.5 h-3.5 text-blue-400 animate-spin" />
            <span className="text-xs text-blue-400 font-medium min-w-[20px] text-center">
              {runningTasks}
            </span>
            <span className="text-slate-600">|</span>
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-xs text-emerald-400 font-medium min-w-[20px] text-center">
              {completedTasks}
            </span>
            {failedTasks > 0 && (
              <>
                <span className="text-slate-600">|</span>
                <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
                <span className="text-xs text-red-400 font-medium min-w-[20px] text-center">
                  {failedTasks}
                </span>
              </>
            )}
          </div>

          {/* 进度条 */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10">
            <Activity className="w-4 h-4 text-indigo-500/70" />
            <span className="text-xs text-slate-400">进度</span>
            <span className="text-xs font-mono text-indigo-400">
              {completedTasks}/{totalTasks}
            </span>
            <div className="w-px h-3 bg-white/10 mx-1" />
            <div className="w-20 h-1.5 rounded-full bg-slate-800 overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-purple-400 transition-all duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="text-xs font-mono text-slate-500">{progress}%</span>
          </div>

          {/* 当前执行任务 - 实时显示 */}
          {currentTaskTitle && directorRunning && (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-indigo-500/10 border border-indigo-500/20 max-w-[250px] animate-pulse">
              <Loader2 className="w-3.5 h-3.5 text-indigo-400 animate-spin flex-shrink-0" />
              <span className="text-xs text-indigo-300 truncate" title={currentTaskTitle || ''}>
                正在执行: {currentTaskTitle}
              </span>
            </div>
          )}

          {failedTasks > 0 && (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-red-500/10 border border-red-500/20">
              <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
              <span className="text-xs text-red-400">{failedTasks} 失败</span>
            </div>
          )}
        </div>

        {/* 右侧控制 */}
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleExecute}
            data-testid="director-workspace-execute"
            disabled={Boolean(executionDisabledReason) || directorToggleBusy}
            title={executionDisabledReason || undefined}
            className="border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/10"
          >
            {directorStarting || directorStopping || directorToggleBusy ? (
              <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5 mr-1.5" />
            )}
            {directorPrimaryActionLabel}
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={() => { void handlePause(); }}
            data-testid="director-workspace-pause"
            disabled={!directorRunning || Boolean(directorControlBusyReason)}
            className="text-slate-400 hover:text-indigo-400 hover:bg-indigo-500/10"
          >
            <Pause className="w-4 h-4" />
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={handleReset}
            data-testid="director-workspace-reset"
            className="text-slate-400 hover:text-slate-100"
          >
            <RotateCcw className="w-4 h-4" />
          </Button>

          <div className="w-px h-6 bg-white/10 mx-2" />

          <Button
            variant="ghost"
            size="icon"
            onClick={() => setShowAIDialogue(!showAIDialogue)}
            className={cn(
              'text-slate-400 hover:text-slate-100',
              showAIDialogue && 'text-indigo-400 bg-indigo-500/10'
            )}
          >
            <MessageSquare className="w-4 h-4" />
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={onOpenSettings}
            disabled={!onOpenSettings}
            data-testid="director-workspace-open-settings"
            title={onOpenSettings ? '系统配置' : '系统配置需由主界面打开'}
            className="text-slate-400 hover:text-slate-100"
          >
            <Settings className="w-4 h-4" />
          </Button>
        </div>
      </header>
      )}

      {!factoryMode ? (
        <section
          className="grid gap-2 border-b border-white/10 bg-slate-950/45 px-4 py-2 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)_minmax(0,1.2fr)]"
          data-testid="director-operational-evidence-grid"
          aria-label="Director operational evidence"
        >
          <DirectorCapabilityStrip
            hosts={capabilityHosts}
            isLoading={isCapabilityLoading}
            error={capabilityError}
            compact
          />
          <DirectorKernelDiagnosticsStrip
            cacheStats={kernelCacheStats}
            llmEvents={kernelLLMEvents}
            tokenBudgetStats={kernelTokenBudgetStats}
            isLoading={isKernelDiagnosticsLoading}
            isClearing={isKernelCacheClearing}
            error={kernelDiagnosticsError}
            onRefresh={() => void loadKernelDiagnostics()}
            onClearCache={() => void handleClearKernelCache()}
            workspace={workspace}
            compact
          />
          <DirectorReadinessDiagnosticsStrip
            diagnostics={directorDiagnostics.data}
            isLoading={directorDiagnostics.loading}
            error={directorDiagnostics.error}
            onRefresh={() => void loadDirectorDiagnostics()}
            compact
            workspace={workspace}
          />
        </section>
      ) : (
        <DirectorReadinessDiagnosticsStrip
          diagnostics={directorDiagnostics.data}
          isLoading={directorDiagnostics.loading}
          error={directorDiagnostics.error}
          onRefresh={() => void loadDirectorDiagnostics()}
          compact
          workspace={workspace}
        />
      )}
      {directorRunEvidence.runId && (
        <RoleRunEvidenceStrip
          tone="cyan"
          testId="director-run-evidence"
          endpoint={`/v2/director/runs/${directorRunEvidence.runId}`}
          workspace={workspace}
          loading={directorRunEvidence.loading}
          error={directorRunEvidence.error}
          status={directorRunEvidence.data?.status}
          details={directorRunEvidence.data ? [`queued=${directorRunEvidence.data.tasks_queued ?? 0}`] : []}
          message={directorRunEvidence.data?.message}
          refreshTestId="director-run-refresh"
          refreshDisabled={!directorRunEvidence.runId || directorRunEvidence.loading}
          refreshLoading={directorRunEvidence.loading}
          autoRefreshActive={directorRunAutoRefreshActive}
          onRefresh={handleRefreshDirectorRun}
          cancelTestId="director-run-cancel"
          cancelDisabled={directorRunCancelDisabled}
          cancelLoading={directorRunCancelState.loading}
          onCancel={() => { void handleCancelDirectorRun(); }}
          cancelResultTestId="director-run-cancel-result"
          cancelResultEndpoint={`/v2/director/runs/${directorRunEvidence.runId}/cancel`}
          cancelResultVisible={
            directorRunCancelState.runId === directorRunEvidence.runId
            && (directorRunCancelState.loading || Boolean(directorRunCancelState.message) || Boolean(directorRunCancelState.error))
          }
          cancelResultLoading={directorRunCancelState.loading}
          cancelResultMessage={directorRunCancelState.message}
          cancelResultError={directorRunCancelState.error}
        />
      )}
      {directorToggleStatusEvidence.triggered && (
        <div
          className="border-b border-white/10 bg-slate-950/70 px-4 py-2 text-xs text-slate-300"
          data-testid="director-toggle-status-evidence"
        >
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-medium text-slate-100">Director status evidence</span>
            <EvidenceEndpointBadge
              endpoint={evidenceEndpoint('/v2/director/status?source=auto', workspace)}
              testId="director-toggle-status-endpoint"
            />
            {directorToggleStatusEvidence.loading ? (
              <span className="text-slate-400">正在读取进程状态...</span>
            ) : directorToggleStatusEvidence.error ? (
              <span className="text-rose-300">{directorToggleStatusEvidence.error}</span>
            ) : directorToggleStatusEvidence.data ? (
              <span className={cn(
                directorToggleStatusEvidence.data.running ? 'text-emerald-300' : 'text-slate-300',
              )}>
                {directorToggleStatusEvidence.data.running ? 'running' : 'idle'}
                {' · '}
                pid={directorToggleStatusEvidence.data.pid ?? 'none'}
                {directorToggleStatusEvidence.data.mode ? ` · mode=${directorToggleStatusEvidence.data.mode}` : ''}
                {directorToggleStatusEvidence.data.source ? ` · source=${directorToggleStatusEvidence.data.source}` : ''}
              </span>
            ) : null}
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
            icon={<FileCode className="w-4 h-4" />}
            label="代码"
            active={activeView === 'code'}
            onClick={() => handleViewChange('code')}
          />
          <NavButton
            icon={<Terminal className="w-4 h-4" />}
            label="终端"
            active={activeView === 'terminal'}
            onClick={() => handleViewChange('terminal')}
          />
          <NavButton
            icon={<Bug className="w-4 h-4" />}
            label="调试"
            active={activeView === 'debug'}
            onClick={() => handleViewChange('debug')}
          />
          <NavButton
            icon={<SlidersHorizontal className="w-4 h-4" />}
            label="策略"
            active={activeView === 'strategy'}
            onClick={() => handleViewChange('strategy')}
          />
          <NavButton
            icon={<Wrench className="w-4 h-4" />}
            label="工作台"
            active={activeView === 'workbench'}
            onClick={() => handleViewChange('workbench')}
          />
        </nav>

        {/* Main Panel */}
        <PanelGroup direction="horizontal" className="flex-1">
          <Panel defaultSize={shouldShowSideAIDialogue ? 60 : 85} minSize={40}>
            <div className="h-full overflow-hidden">
              {activeView === 'tasks' && (
                <DirectorTaskPanelView
                  tasks={executionTasks}
                  workers={visibleWorkers}
                  taskMap={executionTaskMap}
                  selectedTaskId={selectedTaskId}
                  onTaskSelect={handleTaskSelect}
                  onExecute={handleExecute}
                  onTaskCancel={handleTaskCancel}
                  onTaskCreate={handleTaskCreate}
                  isExecuting={isExecuting}
                  isTaskCreating={taskCreateState.loading}
                  taskCreateMessage={taskCreateState.message}
                  taskCreateError={taskCreateState.error}
                  isTaskCancelling={taskCancelState.taskId === selectedTaskId && taskCancelState.loading}
                  taskCancelMessage={taskCancelState.taskId === selectedTaskId ? taskCancelState.message : null}
                  taskCancelError={taskCancelState.taskId === selectedTaskId ? taskCancelState.error : null}
                  taskTraceMap={taskTraceMap}
                  workerFallbackError={workerFallbackError}
                  workerBackendDetail={workerBackendDetail}
                  onWorkerSelect={handleWorkerSelect}
                  taskBackendDetail={taskBackendDetail}
                  taskLLMEvents={taskLLMEvents}
                  executionDisabledReason={executionDisabledReason}
                  workspace={workspace}
                />
              )}
              {activeView === 'activity' && (
                <RealtimeActivityPanel
                  executionLogs={executionLogs}
                  llmStreamEvents={llmStreamEvents}
                  processStreamEvents={processStreamEvents}
                  currentPhase={currentPhase}
                  isRunning={directorRunning}
                  role="director"
                />
              )}
              {activeView === 'code' && (
                <DirectorCodePanel workspace={workspace} fileEditEvents={fileEditEvents} tasks={executionTasks} />
              )}
              {activeView === 'terminal' && (
                <DirectorTerminalPanel output={terminalOutput} onClear={handleClearTerminal} />
              )}
              {activeView === 'debug' && (
                <DirectorDebugPanel
                  tasks={executionTasks.filter((task) => task.status === 'failed' || task.status === 'blocked')}
                  cancellingTaskId={taskCancelState.loading ? taskCancelState.taskId : null}
                  onInspectTask={(taskId) => {
                    handleTaskSelect(taskId);
                    setActiveView('tasks');
                  }}
                  onCancelTask={(taskId) => { void handleTaskCancel(taskId); }}
                />
              )}
              {activeView === 'strategy' && (
                <DirectorStrategyPanel
                  workspace={workspace}
                  tasksCount={totalTasks}
                  runningTasks={runningTasks}
                />
              )}
              {activeView === 'workbench' && (
                <DirectorWorkbenchPanel
                  workspace={workspace}
                  hostKind="electron_workbench"
                  attachmentMode={(selectedTaskId || currentTaskId) ? 'attached_readonly' : 'isolated'}
                  attachedTaskId={selectedTaskId || currentTaskId || undefined}
                  tasksCount={totalTasks}
                  runningTasks={runningTasks}
                />
              )}
            </div>
          </Panel>

          {shouldShowSideAIDialogue && (
            <>
              <PanelResizeHandle className="w-1 bg-white/5 hover:bg-indigo-500/30 transition-colors" />
              <Panel defaultSize={40} minSize={25} maxSize={50}>
                <AIDialoguePanel
                  dialogueRole="director"
                  roleDisplayName="Director"
                  roleTheme={{
                    primary: 'indigo',
                    secondary: 'indigo-400',
                    gradient: 'from-indigo-500 to-indigo-700',
                  }}
                  welcomeMessage="Director 执行系统已就绪。我可以帮您执行代码、调试问题、运行测试。"
                  context={{
                    workspace,
                    session_id: session.id,
                    tasks_count: executionTasks.length,
                    running_tasks: runningTasks,
                    workers_count: visibleWorkers.length,
                    selected_task_id: selectedTaskId || null,
                    current_task_id: currentTaskId || null,
                  }}
                  workspace={workspace}
                  hostKind="electron_workbench"
                  attachmentMode={(selectedTaskId || currentTaskId) ? 'attached_readonly' : 'isolated'}
                  attachedTaskId={selectedTaskId || currentTaskId || undefined}
                  workflowExportTarget="director"
                  workflowExportLabel="导出执行"
                />
              </Panel>
            </>
          )}
        </PanelGroup>
      </div>

      {/* Status Bar */}
      <footer className="h-8 flex items-center justify-between px-4 border-t border-white/5 bg-slate-950/80 text-[11px] text-slate-500">
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5">
            <div className={cn(
              "w-1.5 h-1.5 rounded-full",
              sessionStatus === 'running' ? 'bg-indigo-500 animate-pulse' :
              sessionStatus === 'paused' ? 'bg-yellow-500' :
              sessionStatus === 'completed' ? 'bg-blue-500' : 'bg-slate-500'
            )} />
            {sessionStatus === 'idle' ? '就绪' :
             sessionStatus === 'running' ? '执行中' :
             sessionStatus === 'paused' ? '已暂停' : '已完成'}
          </span>
          <span>会话: {session.id.slice(0, 8)}</span>
        </div>
        <div className="flex items-center gap-4">
          <span>工作区: {workspace}</span>
          <span className="text-indigo-500/70">Director Console v1.0</span>
        </div>
      </footer>
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
      aria-label={`切换到${label}`}
      data-testid={`director-nav-${label}`}
      className={cn(
        'w-10 h-10 cursor-pointer rounded-xl flex flex-col items-center justify-center gap-0.5 transition-all duration-200',
        active
          ? 'bg-indigo-500/15 text-indigo-400 shadow-lg shadow-indigo-500/10'
          : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
      )}
      title={label}
    >
      {icon}
      <span className="text-[8px] font-medium">{label}</span>
    </button>
  );
}

// Task Panel
interface DirectorTaskPanelProps {
  tasks: ExecutionTask[];
  workers: RuntimeWorkerState[];
  taskMap: Map<string, ExecutionTask>;
  selectedTaskId: string | null;
  onTaskSelect: (taskId: string) => void;
  onExecute: () => void;
  isExecuting: boolean;
  taskTraceMap?: TaskTraceMap;
}

function DirectorTaskPanel({
  tasks,
  workers,
  taskMap,
  selectedTaskId,
  onTaskSelect,
  onExecute,
  isExecuting,
  taskTraceMap,
}: DirectorTaskPanelProps) {
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({
    running: true,
    pending: true,
    completed: true,
    failed: true,
    blocked: true,
  });

  const toggleGroup = (group: string) => {
    setExpandedGroups(prev => ({ ...prev, [group]: !prev[group] }));
  };

  // 按状态分组任务
  const groupedTasks = {
    running: tasks.filter(t => t.status === 'running'),
    pending: tasks.filter(t => t.status === 'pending'),
    blocked: tasks.filter(t => t.status === 'blocked'),
    failed: tasks.filter(t => t.status === 'failed'),
    completed: tasks.filter(t => t.status === 'completed'),
  };

  const getStatusIcon = (status: ExecutionTask['status']) => {
    switch (status) {
      case 'completed': return <CheckCircle2 className="w-4 h-4 text-emerald-400" />;
      case 'running': return <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />;
      case 'failed': return <AlertTriangle className="w-4 h-4 text-red-400" />;
      case 'blocked': return <Pause className="w-4 h-4 text-yellow-400" />;
      default: return <div className="w-4 h-4 rounded-full border-2 border-slate-600" />;
    }
  };

  const getStatusLabel = (status: string) => {
    switch (status) {
      case 'running': return '正在进行';
      case 'pending': return '未领取';
      case 'completed': return '已完成';
      case 'failed': return '失败';
      case 'blocked': return '阻塞';
      default: return status;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'running': return 'text-blue-400 bg-blue-500/10 border-blue-500/20';
      case 'pending': return 'text-slate-400 bg-slate-500/10 border-slate-500/20';
      case 'completed': return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20';
      case 'failed': return 'text-red-400 bg-red-500/10 border-red-500/20';
      case 'blocked': return 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20';
      default: return 'text-slate-400';
    }
  };

  const getTypeIcon = (type: ExecutionTask['type']) => {
    switch (type) {
      case 'code': return <Code2 className="w-3.5 h-3.5 text-blue-400" />;
      case 'test': return <CheckCircle2 className="w-3.5 h-3.5 text-purple-400" />;
      case 'debug': return <Bug className="w-3.5 h-3.5 text-red-400" />;
      case 'review': return <FileCode className="w-3.5 h-3.5 text-amber-400" />;
    }
  };

  const getTypeLabel = (type: ExecutionTask['type']) => {
    switch (type) {
      case 'code': return '编码';
      case 'test': return '测试';
      case 'debug': return '调试';
      case 'review': return '审查';
    }
  };

  const getPriorityColor = (priority?: string) => {
    switch (priority) {
      case 'critical': return 'text-red-400 bg-red-500/20';
      case 'high': return 'text-orange-400 bg-orange-500/20';
      case 'medium': return 'text-yellow-400 bg-yellow-500/20';
      case 'low': return 'text-slate-400 bg-slate-500/20';
      default: return 'text-slate-400 bg-slate-500/20';
    }
  };

  const formatDuration = (ms?: number) => {
    if (!ms || ms <= 0) return '-';
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    if (hours > 0) return `${hours}h ${minutes % 60}m`;
    if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
    return `${seconds}s`;
  };

  const formatBytes = (bytes?: number) => {
    if (!bytes || bytes <= 0) return '-';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  // 计算总体统计
  const totalTasks = tasks.length;
  const completedCount = groupedTasks.completed.length;
  const runningCount = groupedTasks.running.length;
  const failedCount = groupedTasks.failed.length;
  const pendingCount = groupedTasks.pending.length;
  const blockedCount = groupedTasks.blocked.length;
  const progress = totalTasks > 0 ? Math.round((completedCount / totalTasks) * 100) : 0;

  // 计算总预算消耗
  const totalBudget = tasks.reduce((acc, t) => acc + (t.budget?.total || 0), 0);
  const usedBudget = tasks.reduce((acc, t) => acc + (t.budget?.used || 0), 0);
  const budgetProgress = totalBudget > 0 ? Math.round((usedBudget / totalBudget) * 100) : 0;

  const workerRows = workers
    .filter((worker) => worker && typeof worker === 'object')
    .map((worker) => {
      const taskId = String(worker.currentTaskId || '').trim();
      const taskName = taskId ? taskMap.get(taskId)?.name || taskId : '';
      return {
        id: worker.id,
        name: worker.name || worker.id,
        status: worker.status,
        taskId,
        taskName,
        healthy: worker.healthy,
        tasksCompleted: worker.tasksCompleted,
        tasksFailed: worker.tasksFailed,
      };
    });
  const workerBusyCount = workerRows.filter((worker) => worker.status === 'busy').length;
  const workerIdleCount = workerRows.filter((worker) => worker.status === 'idle').length;
  const workerFailedCount = workerRows.filter((worker) => worker.status === 'failed').length;
  const selectedTask = selectedTaskId ? taskMap.get(selectedTaskId) || null : null;

  const getWorkerStatusLabel = (status: RuntimeWorkerState['status']) => {
    if (status === 'busy') return '执行中';
    if (status === 'idle') return '空闲';
    if (status === 'stopping') return '停止中';
    if (status === 'stopped') return '已停止';
    if (status === 'failed') return '异常';
    return '未知';
  };

  const getWorkerStatusColor = (status: RuntimeWorkerState['status']) => {
    if (status === 'busy') return 'text-blue-300 border-blue-500/30 bg-blue-500/10';
    if (status === 'idle') return 'text-emerald-300 border-emerald-500/30 bg-emerald-500/10';
    if (status === 'stopping') return 'text-amber-300 border-amber-500/30 bg-amber-500/10';
    if (status === 'stopped') return 'text-slate-300 border-slate-500/30 bg-slate-500/10';
    if (status === 'failed') return 'text-red-300 border-red-500/30 bg-red-500/10';
    return 'text-slate-300 border-slate-500/30 bg-slate-500/10';
  };

  const renderCompactList = (items: string[] | undefined, empty: string) => {
    if (!items || items.length === 0) {
      return <span className="text-slate-500">{empty}</span>;
    }
    return (
      <div className="flex flex-wrap gap-1">
        {items.map((item) => (
          <span key={item} className="rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-300">
            {item}
          </span>
        ))}
      </div>
    );
  };

  const TaskGroup = ({ status, tasks: groupTasks }: { status: string; tasks: ExecutionTask[] }) => {
    if (groupTasks.length === 0) return null;
    const isExpanded = expandedGroups[status];

    return (
      <div className="mb-4">
        <button
          onClick={() => toggleGroup(status)}
          className={cn(
            'w-full flex items-center justify-between px-3 py-2 rounded-lg border text-xs font-medium transition-all',
            getStatusColor(status)
          )}
        >
          <div className="flex items-center gap-2">
            {status === 'running' && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {status === 'pending' && <Clock className="w-3.5 h-3.5" />}
            {status === 'completed' && <CheckCircle2 className="w-3.5 h-3.5" />}
            {status === 'failed' && <AlertTriangle className="w-3.5 h-3.5" />}
            {status === 'blocked' && <Pause className="w-3.5 h-3.5" />}
            <span>{getStatusLabel(status)}</span>
            <span className="opacity-70">({groupTasks.length})</span>
          </div>
          {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>

        {isExpanded && (
          <div className="mt-2 space-y-2">
            {groupTasks.map((task) => (
              <TaskCard key={task.id} task={task} />
            ))}
          </div>
        )}
      </div>
    );
  };

  const TaskCard = ({ task }: { task: ExecutionTask }) => {
    const isSelected = selectedTaskId === task.id;
    const budgetPercent = task.budget && task.budget.total > 0
      ? Math.round((task.budget.used / task.budget.total) * 100)
      : 0;
    const hasLineStats = Boolean(
      task.lineStats
      && (task.lineStats.added > 0 || task.lineStats.deleted > 0 || task.lineStats.modified > 0),
    );
    const hasOperationStats = Boolean(
      task.operationStats
      && (task.operationStats.create > 0 || task.operationStats.modify > 0 || task.operationStats.delete > 0),
    );
    const traces = taskTraceMap?.get(task.id) || [];
    const failedTrace = traces.find((t: { status: string }) => t.status === 'failed');

    return (
      <button
        data-testid="director-task-item"
        onClick={() => onTaskSelect(task.id)}
        className={cn(
          'w-full p-3 rounded-xl text-left transition-all border',
          isSelected
            ? 'bg-indigo-500/10 border-indigo-500/30'
            : 'bg-white/5 border-white/5 hover:border-white/10 hover:bg-white/[0.07]'
        )}
      >
        {/* 头部：名称和状态 */}
        <div className="flex items-start gap-3">
          {getStatusIcon(task.status)}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm text-slate-200 font-medium truncate">{task.name}</span>
              {task.priority && (
                <span className={cn('text-[9px] px-1.5 py-0.5 rounded', getPriorityColor(task.priority))}>
                  {task.priority === 'critical' ? '紧急' : task.priority === 'high' ? '高' : task.priority === 'medium' ? '中' : '低'}
                </span>
              )}
            </div>
            {task.description && (
              <p className="mt-1 text-[11px] text-slate-500 line-clamp-2">{task.description}</p>
            )}
          </div>
        </div>

        {/* 进度条（仅运行中） */}
        {task.status === 'running' && (
          <div className="mt-3">
            <div className="flex items-center justify-between text-[10px] text-slate-400 mb-1">
              <span>进度</span>
              <span>{task.progress || 0}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all"
                style={{ width: `${task.progress || 0}%` }}
              />
            </div>
          </div>
        )}

        {/* 详细信息网格 */}
        <div className="mt-3 grid grid-cols-3 gap-2 text-[10px]">
          {/* 类型 */}
          <div className="flex items-center gap-1.5 text-slate-400">
            {getTypeIcon(task.type)}
            <span>{getTypeLabel(task.type)}</span>
          </div>

          {/* 耗时 */}
          <div className="flex items-center gap-1.5 text-slate-400">
            <Clock className="w-3 h-3" />
            <span>{formatDuration(task.actualTime)}</span>
          </div>

          {/* 文件修改 */}
          <div className="flex items-center gap-1.5 text-slate-400">
            <FileCode className="w-3 h-3" />
            <span>{task.filesModified || 0} 文件</span>
          </div>
        </div>

        {(task.currentFilePath || hasLineStats || hasOperationStats || (task.retries || 0) > 0) && (
          <div className="mt-2 pt-2 border-t border-white/5">
            <div className="flex flex-wrap items-center gap-1.5 text-[9px]">
              {task.currentFilePath && (
                <span
                  className="inline-flex max-w-full items-center gap-1 rounded-md border border-cyan-400/30 bg-cyan-500/10 px-1.5 py-0.5 text-cyan-200"
                  title={task.currentFilePath}
                >
                  <FileCode className="h-2.5 w-2.5 shrink-0" />
                  <span className="truncate max-w-[220px]">
                    {task.status === 'running' ? '当前文件' : '最近文件'}: {task.currentFilePath}
                  </span>
                </span>
              )}
              {hasLineStats && task.lineStats && (
                <>
                  <span className="inline-flex items-center rounded-md border border-emerald-400/30 bg-emerald-500/10 px-1.5 py-0.5 text-emerald-200">
                    +{task.lineStats.added}
                  </span>
                  <span className="inline-flex items-center rounded-md border border-rose-400/30 bg-rose-500/10 px-1.5 py-0.5 text-rose-200">
                    -{task.lineStats.deleted}
                  </span>
                  <span className="inline-flex items-center rounded-md border border-amber-400/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-200">
                    ~{task.lineStats.modified}
                  </span>
                </>
              )}
              {hasOperationStats && task.operationStats && (
                <span className="inline-flex items-center gap-1 rounded-md border border-slate-400/20 bg-white/5 px-1.5 py-0.5 text-slate-300">
                  C:{task.operationStats.create} M:{task.operationStats.modify} D:{task.operationStats.delete}
                </span>
              )}
              {(task.retries || 0) > 0 && (
                <span className="inline-flex items-center gap-1 rounded-md border border-orange-400/30 bg-orange-500/10 px-1.5 py-0.5 text-orange-200">
                  <RotateCcw className="h-2.5 w-2.5" />
                  重试 {task.retries} 次
                </span>
              )}
              {task.activityUpdatedAt && (
                <span className="inline-flex items-center gap-1 rounded-md border border-indigo-400/20 bg-indigo-500/10 px-1.5 py-0.5 text-indigo-200">
                  <Clock className="h-2.5 w-2.5" />
                  {formatTelemetryTime(task.activityUpdatedAt)}
                </span>
              )}
            </div>
          </div>
        )}

        {/* 预算消耗 */}
        {task.budget && (
          <div className="mt-2 pt-2 border-t border-white/5">
            <div className="flex items-center justify-between text-[10px]">
              <div className="flex items-center gap-1.5 text-slate-400">
                <Coins className="w-3 h-3" />
                <span>Budget</span>
              </div>
              <span className={cn(
                budgetPercent > 90 ? 'text-red-400' : budgetPercent > 70 ? 'text-yellow-400' : 'text-emerald-400'
              )}>
                {formatBytes(task.budget.used)} / {formatBytes(task.budget.total)}
              </span>
            </div>
            <div className="mt-1 h-1 rounded-full bg-slate-800 overflow-hidden">
              <div
                className={cn(
                  'h-full rounded-full transition-all',
                  budgetPercent > 90 ? 'bg-red-500' : budgetPercent > 70 ? 'bg-yellow-500' : 'bg-emerald-500'
                )}
                style={{ width: `${Math.min(budgetPercent, 100)}%` }}
              />
            </div>
          </div>
        )}

        {/* 标签 */}
        {task.tags && task.tags.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {task.tags.slice(0, 3).map((tag, idx) => (
              <span key={idx} className="text-[9px] px-1.5 py-0.5 rounded bg-white/10 text-slate-400">
                {tag}
              </span>
            ))}
            {task.tags.length > 3 && (
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-white/10 text-slate-400">
                +{task.tags.length - 3}
              </span>
            )}
          </div>
        )}

        {/* 任务追踪时间线 */}
        {traces.length > 0 && (
          <div className="mt-2 pt-2 border-t border-white/5">
            <TaskTraceTimeline
              traces={traces}
              maxTraces={task.status === 'running' ? 5 : 1}
              expanded={task.status === 'running'}
            />
          </div>
        )}

        {/* 失败卡片优先显示失败步骤 */}
        {task.status === 'failed' && failedTrace?.step_detail && (
          <div className="text-red-400 text-sm mt-2">
            {failedTrace.step_detail}
          </div>
        )}

        {/* 错误信息 */}
        {task.error && (
          <div className="mt-2 p-2 rounded bg-red-500/10 border border-red-500/20">
            <p className="text-[10px] text-red-400 line-clamp-2">{task.error}</p>
          </div>
        )}
      </button>
    );
  };

  return (
    <div className="h-full flex flex-col">
      {/* 头部统计 */}
      <div className="h-auto border-b border-white/5">
        {/* 主要控制栏 */}
        <div className="h-12 flex items-center justify-between px-4">
          <h2 className="text-sm font-medium text-slate-200">任务队列</h2>
          <Button
            size="sm"
            onClick={onExecute}
            data-testid="director-workspace-bulk-execute"
            className={cn(
              isExecuting
                ? 'bg-red-600 hover:bg-red-700'
                : 'bg-emerald-600 hover:bg-emerald-700',
              'text-white'
            )}
          >
            {isExecuting ? (
              <><Pause className="w-3.5 h-3.5 mr-1.5" /> 停止执行</>
            ) : (
              <><Zap className="w-3.5 h-3.5 mr-1.5" /> 全部执行</>
            )}
          </Button>
        </div>

        {/* 统计卡片 */}
        <div className="px-4 pb-3 grid grid-cols-5 gap-2">
          <StatCard
            icon={<Loader2 className="w-3.5 h-3.5 text-blue-400" />}
            label="进行中"
            value={runningCount}
            color="blue"
          />
          <StatCard
            icon={<Clock className="w-3.5 h-3.5 text-slate-400" />}
            label="未领取"
            value={pendingCount}
            color="slate"
          />
          <StatCard
            icon={<CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />}
            label="已完成"
            value={completedCount}
            color="emerald"
          />
          <StatCard
            icon={<AlertTriangle className="w-3.5 h-3.5 text-red-400" />}
            label="失败"
            value={failedCount}
            color="red"
          />
          <StatCard
            icon={<Pause className="w-3.5 h-3.5 text-yellow-400" />}
            label="阻塞"
            value={blockedCount}
            color="yellow"
          />
        </div>

        {/* 总体进度 */}
        <div className="px-4 pb-3">
          <div className="flex items-center justify-between text-[10px] text-slate-400 mb-1">
            <span className="flex items-center gap-1.5">
              <BarChart3 className="w-3 h-3" />
              总体进度 {completedCount}/{totalTasks}
            </span>
            <span className="text-indigo-400 font-medium">{progress}%</span>
          </div>
          <div className="h-2 rounded-full bg-slate-800 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-500 transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>

        {/* 预算消耗 */}
        {totalBudget > 0 && (
          <div className="px-4 pb-3">
            <div className="flex items-center justify-between text-[10px] text-slate-400 mb-1">
              <span className="flex items-center gap-1.5">
                <Coins className="w-3 h-3" />
                预算消耗
              </span>
              <span className={cn(
                budgetProgress > 90 ? 'text-red-400' : 'text-emerald-400',
                'font-medium'
              )}>
                {formatBytes(usedBudget)} / {formatBytes(totalBudget)} ({budgetProgress}%)
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
              <div
                className={cn(
                  'h-full rounded-full transition-all',
                  budgetProgress > 90 ? 'bg-red-500' : 'bg-emerald-500'
                )}
                style={{ width: `${Math.min(budgetProgress, 100)}%` }}
              />
            </div>
          </div>
        )}

        {/* Selected task detail */}
        <div data-testid="director-task-detail" className="mx-4 mb-3 rounded-xl border border-white/10 bg-slate-950/45 p-3">
          {selectedTask ? (
            <div>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                    {getStatusIcon(selectedTask.status)}
                    <span className="truncate">{selectedTask.name}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                    <span className={cn('rounded border px-1.5 py-0.5', getStatusColor(selectedTask.status))}>
                      {getStatusLabel(selectedTask.status)}
                    </span>
                    {selectedTask.rawStatus ? (
                      <span className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-slate-400">
                        raw: {selectedTask.rawStatus}
                      </span>
                    ) : null}
                    {selectedTask.pmTaskId ? (
                      <span className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-slate-400">
                        PM: {selectedTask.pmTaskId}
                      </span>
                    ) : null}
                    {selectedTask.claimedBy || selectedTask.assignedWorker ? (
                      <span className="rounded border border-indigo-400/25 bg-indigo-500/10 px-1.5 py-0.5 text-indigo-200">
                        owner: {selectedTask.claimedBy || selectedTask.assignedWorker}
                      </span>
                    ) : (
                      <span className="rounded border border-slate-500/25 bg-slate-500/10 px-1.5 py-0.5 text-slate-400">
                        未领取
                      </span>
                    )}
                  </div>
                </div>
                {selectedTask.blueprintId || selectedTask.blueprintPath ? (
                  <span className="shrink-0 rounded border border-cyan-400/25 bg-cyan-500/10 px-2 py-1 text-[10px] text-cyan-200">
                    {selectedTask.blueprintId || 'blueprint'}
                  </span>
                ) : null}
              </div>

              {(selectedTask.goal || selectedTask.description) && (
                <div className="mt-3 text-xs leading-5 text-slate-300">
                  {selectedTask.goal || selectedTask.description}
                </div>
              )}

              <div className="mt-3 grid grid-cols-2 gap-3 text-[11px]">
                <DetailBlock title="执行步骤">
                  {renderCompactList(selectedTask.executionSteps, '无步骤字段')}
                </DetailBlock>
                <DetailBlock title="验收标准">
                  {renderCompactList(selectedTask.acceptanceCriteria, '无验收字段')}
                </DetailBlock>
                <DetailBlock title="目标文件">
                  {renderCompactList(selectedTask.targetFiles, '无目标文件')}
                </DetailBlock>
                <DetailBlock title="依赖/阻塞">
                  {renderCompactList([...(selectedTask.dependencies || []), ...(selectedTask.blockedBy || [])], '无依赖或阻塞')}
                </DetailBlock>
              </div>

              {selectedTask.blueprintPath ? (
                <div className="mt-3 truncate rounded-md border border-cyan-400/20 bg-cyan-500/5 px-2 py-1 text-[10px] text-cyan-100" title={selectedTask.blueprintPath}>
                  蓝图路径: {selectedTask.blueprintPath}
                </div>
              ) : null}

              {selectedTask.error ? (
                <div className="mt-3 rounded-md border border-red-500/25 bg-red-500/10 p-2 text-[11px] leading-5 text-red-200">
                  {selectedTask.error}
                </div>
              ) : null}

              <div className="mt-3 rounded-md border border-white/10 bg-white/[0.035] p-2">
                <div className="mb-2 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-400">
                  <span>任务级实时文件变更</span>
                  <span>{selectedTask.taskScopedFileEvents?.length || 0} events</span>
                </div>
                {selectedTask.taskScopedFileEvents && selectedTask.taskScopedFileEvents.length > 0 ? (
                  <div className="space-y-1">
                    {selectedTask.taskScopedFileEvents.slice(-4).reverse().map((event) => (
                      <div key={event.id} className="flex items-center justify-between gap-2 rounded border border-white/5 bg-slate-950/50 px-2 py-1 text-[10px]">
                        <span className="truncate text-slate-300">{event.filePath}</span>
                        <span className={cn(
                          'shrink-0 rounded px-1.5 py-0.5',
                          event.operation === 'create' ? 'bg-emerald-500/15 text-emerald-200' :
                            event.operation === 'delete' ? 'bg-red-500/15 text-red-200' :
                              'bg-blue-500/15 text-blue-200',
                        )}>
                          {event.operation}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-[11px] text-slate-500">该任务暂未收到文件增删改事件。</div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <Hash className="h-3.5 w-3.5" />
              点击左侧任务卡查看完整任务合同、领取状态、验收标准和实时文件变更。
            </div>
          )}
        </div>

        {/* Worker 实时状态 */}
        <div className="px-4 pb-3">
          <div className="flex items-center justify-between text-[10px] text-slate-400 mb-2">
            <span className="flex items-center gap-1.5">
              <Layers className="w-3 h-3" />
              Worker 运行看板
            </span>
            <span>
              总计 {workerRows.length} / 空闲 {workerIdleCount} / 执行中 {workerBusyCount}
              {workerFailedCount > 0 ? ` / 异常 ${workerFailedCount}` : ''}
            </span>
          </div>
          {workerRows.length === 0 ? (
            <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-[11px] text-slate-400">
              暂无 worker 实时数据，等待 Director 推送...
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-2">
              {workerRows.map((worker) => (
                <div
                  key={worker.id}
                  className={cn(
                    'rounded-lg border px-3 py-2 text-[11px] transition-colors',
                    getWorkerStatusColor(worker.status),
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium truncate">{worker.name}</span>
                    <span className="text-[10px]">{getWorkerStatusLabel(worker.status)}</span>
                  </div>
                  <div className="mt-1 text-[10px] text-slate-300/90">
                    {worker.taskName
                      ? `当前任务: ${worker.taskName}`
                      : '当前任务: 空闲'}
                  </div>
                  <div className="mt-1 text-[10px] text-slate-400">
                    完成 {worker.tasksCompleted} / 失败 {worker.tasksFailed}
                    {worker.healthy === false ? ' / 健康检查失败' : ''}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 任务列表 */}
      <div className="flex-1 overflow-auto p-4">
        {tasks.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-500">
            <ListTodo className="w-12 h-12 mb-4 text-indigo-500/30" />
            <p>当前没有可执行任务</p>
          </div>
        ) : (
          <div>
            <TaskGroup status="running" tasks={groupedTasks.running} />
            <TaskGroup status="pending" tasks={groupedTasks.pending} />
            <TaskGroup status="blocked" tasks={groupedTasks.blocked} />
            <TaskGroup status="failed" tasks={groupedTasks.failed} />
            <TaskGroup status="completed" tasks={groupedTasks.completed} />
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: number; color: string }) {
  const colorClasses: Record<string, string> = {
    blue: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
    slate: 'text-slate-400 bg-slate-500/10 border-slate-500/20',
    emerald: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
    red: 'text-red-400 bg-red-500/10 border-red-500/20',
    yellow: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
  };

  return (
    <div className={cn('flex flex-col items-center p-2 rounded-lg border', colorClasses[color])}>
      {icon}
      <span className="text-lg font-bold mt-1">{value}</span>
      <span className="text-[9px] opacity-70">{label}</span>
    </div>
  );
}

function DetailBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-md border border-white/10 bg-white/[0.025] p-2">
      <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">{title}</div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

// Code Panel
interface DirectorCodePanelProps {
  workspace: string;
  fileEditEvents: FileEditEvent[];
  tasks: ExecutionTask[];
}

function buildTaskSnapshotFileEditEvents(tasks: ExecutionTask[]): FileEditEvent[] {
  const fallbackEvents: FileEditEvent[] = [];

  for (const task of tasks) {
    if ((task.taskScopedFileEvents?.length || 0) > 0) {
      continue;
    }
    const files = [
      task.currentFilePath,
      ...(task.targetFiles || []),
    ].filter((item, index, all): item is string => {
      const value = String(item || "").trim();
      return Boolean(value) && all.indexOf(item) === index;
    });
    if (files.length === 0 || (task.filesModified || 0) <= 0) {
      continue;
    }
    const timestamp = task.completedAt || task.activityUpdatedAt || task.startedAt || task.createdAt || new Date(0).toISOString();
    const taskId = String(task.id || "");
    files.slice(0, 20).forEach((filePath, index) => {
      fallbackEvents.push({
        id: `task-snapshot-${taskId}-${index}-${filePath}`,
        filePath,
        operation: "modify",
        contentSize: 0,
        taskId,
        timestamp,
        addedLines: index === 0 ? task.lineStats?.added : undefined,
        deletedLines: index === 0 ? task.lineStats?.deleted : undefined,
        modifiedLines: index === 0 ? task.lineStats?.modified : undefined,
        sourceChannel: "task-runtime",
        eventKind: "task_snapshot_file_change",
        provenance: "task-runtime-snapshot",
      });
    });
  }

  return fallbackEvents;
}

function mergeCodePanelEvents(fileEditEvents: FileEditEvent[], tasks: ExecutionTask[]): FileEditEvent[] {
  const merged = [...fileEditEvents];
  const seen = new Set(merged.map((event) => `${event.taskId || ""}:${event.filePath}`));
  for (const event of buildTaskSnapshotFileEditEvents(tasks)) {
    const key = `${event.taskId || ""}:${event.filePath}`;
    if (!seen.has(key)) {
      seen.add(key);
      merged.push(event);
    }
  }
  return merged;
}

function DirectorCodePanel({ workspace, fileEditEvents, tasks }: DirectorCodePanelProps) {
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);
  const [openFileStatus, setOpenFileStatus] = useState<{
    kind: 'idle' | 'loading' | 'success' | 'error';
    message: string | null;
  }>({ kind: 'idle', message: null });

  const getOperationIcon = (operation: string) => {
    switch (operation) {
      case 'create':
        return <FilePlus className="w-3.5 h-3.5 text-emerald-400" />;
      case 'delete':
        return <FileX className="w-3.5 h-3.5 text-red-400" />;
      case 'modify':
      default:
        return <FileEdit className="w-3.5 h-3.5 text-blue-400" />;
    }
  };

  const getOperationLabel = (operation: string) => {
    switch (operation) {
      case 'create':
        return '创建';
      case 'delete':
        return '删除';
      case 'modify':
        return '修改';
      default:
        return operation;
    }
  };

  const getOperationColor = (operation: string) => {
    switch (operation) {
      case 'create':
        return 'text-emerald-400';
      case 'delete':
        return 'text-red-400';
      case 'modify':
        return 'text-blue-400';
      default:
        return 'text-slate-400';
    }
  };

  const codePanelEvents = useMemo(() => mergeCodePanelEvents(fileEditEvents, tasks), [fileEditEvents, tasks]);
  // 只显示最近的 20 个事件，按时间倒序
  const recentEvents = useMemo(() => [...codePanelEvents].reverse().slice(0, 20), [codePanelEvents]);
  const selectedOpenEvent = useMemo(
    () => recentEvents.find((event) => event.id === expandedEventId) ?? recentEvents[0] ?? null,
    [expandedEventId, recentEvents],
  );

  const toggleExpand = (eventId: string) => {
    setExpandedEventId(prev => prev === eventId ? null : eventId);
  };

  const renderLineStats = (event: FileEditEvent) => {
    const stats = resolveEventLineStats(event);
    const hasStats = stats.added > 0 || stats.deleted > 0 || stats.modified > 0;
    return { stats, hasStats };
  };

  const handleOpenFile = useCallback(async () => {
    const target = resolveDirectorOpenTarget(workspace, selectedOpenEvent?.filePath);
    if (!target) {
      setOpenFileStatus({ kind: 'error', message: '没有可打开的工作区文件' });
      return;
    }

    setOpenFileStatus({ kind: 'loading', message: `正在打开 ${selectedOpenEvent?.filePath || target}` });
    try {
      const result = await openPath(target);
      if (!result.ok) {
        setOpenFileStatus({ kind: 'error', message: result.error || '打开文件失败' });
        return;
      }
      setOpenFileStatus({ kind: 'success', message: `已请求打开 ${selectedOpenEvent?.filePath || target}` });
    } catch (error) {
      setOpenFileStatus({
        kind: 'error',
        message: error instanceof Error ? error.message : '打开文件失败',
      });
    }
  }, [selectedOpenEvent, workspace]);

  return (
    <div data-testid="director-code-panel" className="h-full flex flex-col">
      <div className="h-12 flex items-center justify-between px-4 border-b border-white/5">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-medium text-slate-200">实时代码变更</h2>
          {codePanelEvents.length > 0 && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-400">
              {codePanelEvents.length} 个文件
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { void handleOpenFile(); }}
            disabled={!selectedOpenEvent || openFileStatus.kind === 'loading'}
            data-testid="director-code-open-file"
            title={selectedOpenEvent?.filePath ? `打开 ${selectedOpenEvent.filePath}` : '没有可打开的文件'}
            className="text-slate-400"
          >
            <FileCode className="w-4 h-4 mr-1.5" />
            {openFileStatus.kind === 'loading' ? '打开中' : '打开文件'}
          </Button>
        </div>
      </div>
      {openFileStatus.message ? (
        <div
          className={cn(
            'border-b px-4 py-1.5 text-[11px]',
            openFileStatus.kind === 'error'
              ? 'border-amber-500/20 bg-amber-500/10 text-amber-100'
              : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-100',
          )}
          data-testid="director-code-open-file-evidence"
        >
          {openFileStatus.message}
        </div>
      ) : null}
      <div className="flex-1 overflow-hidden flex">
        {/* 文件变更列表 + Diff 详情 */}
        <div className="flex-1 overflow-auto p-4">
          {recentEvents.length === 0 ? (
            <div data-testid="director-code-empty" className="h-full flex flex-col items-center justify-center text-slate-500">
              <FileCode className="w-12 h-12 mb-4 text-indigo-500/30" />
              <p>等待代码变更...</p>
              <p className="text-xs mt-2 opacity-70">Director 执行时将实时显示文件修改</p>
            </div>
          ) : (
            <div data-testid="director-code-event-list" className="space-y-2">
              {recentEvents.map((event, index) => {
                const hasPatch = Boolean(event.patch);
                const { stats, hasStats } = renderLineStats(event);
                const sourceLabel = event.provenance || event.sourceChannel || event.eventKind || 'runtime';
                return (
                <div key={event.id}>
                  <div
                    className={cn(
                      'p-3 rounded-xl border transition-all cursor-pointer',
                      index === 0 ? 'bg-indigo-500/10 border-indigo-500/30' : 'bg-white/5 border-white/5 hover:border-white/10',
                      expandedEventId === event.id && 'ring-1 ring-indigo-500/30'
                    )}
                    onClick={() => toggleExpand(event.id)}
                  >
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5">{getOperationIcon(event.operation)}</div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs font-mono text-slate-300 truncate flex-1" title={event.filePath}>
                            {event.filePath}
                          </span>
                          <span
                            className={cn(
                              'text-[10px] px-1.5 py-0.5 rounded bg-white/10',
                              getOperationColor(event.operation)
                            )}
                          >
                            {getOperationLabel(event.operation)}
                          </span>
                          <span
                            className={cn(
                              'text-[10px] px-1.5 py-0.5 rounded',
                              hasPatch ? 'bg-cyan-500/20 text-cyan-400' : 'bg-amber-500/15 text-amber-300',
                            )}
                          >
                            {hasPatch ? 'Diff' : '统计'}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center gap-3 text-[10px] text-slate-500">
                          <span>{event.contentSize} bytes</span>
                          {event.taskId && <span className="text-slate-600">任务: {String(event.taskId).slice(0, 8)}</span>}
                          {hasStats && (
                            <span className="flex items-center gap-1.5 font-mono">
                              {stats.added > 0 && <span className="text-emerald-400">+{stats.added}</span>}
                              {stats.deleted > 0 && <span className="text-red-400">-{stats.deleted}</span>}
                              {stats.modified > 0 && <span className="text-blue-400">~{stats.modified}</span>}
                            </span>
                          )}
                          <span className="text-slate-600">
                            {new Date(event.timestamp).toLocaleTimeString()}
                          </span>
                          <span className={hasPatch ? 'text-cyan-400' : 'text-amber-300'}>
                            {expandedEventId === event.id ? '▼ 收起' : hasPatch ? '▶ 展开 Diff' : '▶ 展开统计'}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 展开的 Diff 详情 */}
                  {expandedEventId === event.id && (
                    <div className="mt-2">
                      {hasPatch ? (
                        <RealTimeFileDiff
                          filePath={event.filePath}
                          operation={event.operation}
                          patch={event.patch}
                          compact
                        />
                      ) : (
                        <div
                          className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-100"
                          data-testid="director-file-edit-summary"
                        >
                          <div className="font-medium">未收到 diff patch，已显示文件变更统计。</div>
                          <div className="mt-2 flex flex-wrap gap-2 font-mono">
                            <span className="rounded bg-white/5 px-2 py-1 text-emerald-300">+{stats.added}</span>
                            <span className="rounded bg-white/5 px-2 py-1 text-red-300">-{stats.deleted}</span>
                            <span className="rounded bg-white/5 px-2 py-1 text-blue-300">~{stats.modified}</span>
                            <span className="rounded bg-white/5 px-2 py-1 text-slate-300">{event.contentSize} bytes</span>
                          </div>
                          <div className="mt-2 text-[11px] text-amber-200/70">
                            来源: {sourceLabel}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
              })}
            </div>
          )}
        </div>

        {/* 右侧统计 */}
        <div className="w-48 border-l border-white/5 p-4 bg-slate-950/30">
          <h3 className="text-[10px] uppercase tracking-wider text-slate-500 mb-3">变更统计</h3>
          <div className="space-y-2">
            <div className="flex items-center justify-between p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
              <span className="text-xs text-emerald-400 flex items-center gap-1.5">
                <FilePlus className="w-3 h-3" />
                创建
              </span>
              <span className="text-xs font-mono text-emerald-300">
                {fileEditEvents.filter(e => e.operation === 'create').length}
              </span>
            </div>
            <div className="flex items-center justify-between p-2 rounded-lg bg-blue-500/10 border border-blue-500/20">
              <span className="text-xs text-blue-400 flex items-center gap-1.5">
                <FileEdit className="w-3 h-3" />
                修改
              </span>
              <span className="text-xs font-mono text-blue-300">
                {fileEditEvents.filter(e => e.operation === 'modify').length}
              </span>
            </div>
            <div className="flex items-center justify-between p-2 rounded-lg bg-red-500/10 border border-red-500/20">
              <span className="text-xs text-red-400 flex items-center gap-1.5">
                <FileX className="w-3 h-3" />
                删除
              </span>
              <span className="text-xs font-mono text-red-300">
                {fileEditEvents.filter(e => e.operation === 'delete').length}
              </span>
            </div>
          </div>

          <div className="mt-6 pt-4 border-t border-white/5">
            <h3 className="text-[10px] uppercase tracking-wider text-slate-500 mb-2">工作区</h3>
            <p className="text-xs text-slate-400 truncate" title={workspace}>
              {workspace}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// Terminal Panel
function DirectorTerminalPanel({ output, onClear }: { output: string; onClear: () => void }) {
  return (
    <div className="h-full flex flex-col">
      <div className="h-12 flex items-center justify-between px-4 border-b border-white/5">
        <h2 className="text-sm font-medium text-slate-200">执行终端</h2>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClear}
          disabled={!output}
          data-testid="director-terminal-clear"
          className="text-slate-400"
        >
          <RotateCcw className="w-4 h-4 mr-1.5" />
          清空
        </Button>
      </div>
      <div className="flex-1 p-4">
        <div className="h-full rounded-xl border border-white/10 bg-slate-950 p-4 font-mono text-xs overflow-auto">
          {output ? (
            <pre className="text-slate-300 whitespace-pre-wrap">{output}</pre>
          ) : (
            <div className="text-slate-600">等待执行...</div>
          )}
        </div>
      </div>
    </div>
  );
}

// Debug Panel
function DirectorDebugPanel({
  tasks,
  cancellingTaskId,
  onInspectTask,
  onCancelTask,
}: {
  tasks: ExecutionTask[];
  cancellingTaskId?: string | null;
  onInspectTask: (taskId: string) => void;
  onCancelTask: (taskId: string) => void;
}) {
  return (
    <div className="h-full flex flex-col">
      <div className="h-12 flex items-center px-4 border-b border-white/5">
        <h2 className="text-sm font-medium text-slate-200">调试中心</h2>
      </div>
      <div className="flex-1 overflow-auto p-4">
        {tasks.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-500">
            <CheckCircle2 className="w-12 h-12 mb-4 text-blue-500/30" />
            <p>没有需要调试的问题</p>
          </div>
        ) : (
          <div className="space-y-2">
            {tasks.map((task) => (
              <div
                key={task.id}
                className="p-4 rounded-xl border border-red-500/20 bg-red-500/5"
              >
                <div className="flex items-center gap-2 mb-2">
                  <Bug className="w-4 h-4 text-red-400" />
                  <span className="text-sm text-slate-200 font-medium">{task.name}</span>
                </div>
                {task.error && (
                  <pre className="text-xs text-red-400 font-mono bg-red-950/30 p-2 rounded">
                    {task.error}
                  </pre>
                )}
                <div className="mt-3 flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onInspectTask(task.id)}
                    data-testid={`director-debug-inspect-${task.id}`}
                    className="border-red-500/30 text-red-400"
                  >
                    定位
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onCancelTask(task.id)}
                    disabled={cancellingTaskId === task.id}
                    data-testid={`director-debug-cancel-${task.id}`}
                    className="text-slate-400"
                  >
                    {cancellingTaskId === task.id ? '取消中' : '取消'}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
