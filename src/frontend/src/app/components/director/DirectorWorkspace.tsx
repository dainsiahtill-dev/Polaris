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
} from 'lucide-react';
import { apiFetchFresh } from '@/api';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { AIDialoguePanel } from '@/app/components/ai-dialogue';
import { RealTimeFileDiff } from './RealTimeFileDiff';
import { TaskTraceTimeline } from '../common/TaskTraceTimeline';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import type { PmTask } from '@/types/task';
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
  onToggleDirector: () => void;
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

interface ExecutionTask {
  id: string;
  name: string;
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
  tags?: string[];
  createdAt?: string;
  startedAt?: string;
  completedAt?: string;
  assignedWorker?: string;
  filesModified?: number;
  retries?: number;
  maxRetries?: number;
  currentFilePath?: string;
  activityUpdatedAt?: string;
  lineStats?: TaskLineStats;
  operationStats?: TaskOperationStats;
  // Progress tracking
  currentPhase?: string;
  phaseIndex?: number;
  phaseTotal?: number;
}

interface ExecutionSession {
  id: string;
  status: 'idle' | 'running' | 'paused' | 'completed';
  currentTask?: ExecutionTask;
  logs: string[];
}

type DirectorActiveView = 'tasks' | 'code' | 'activity' | 'terminal' | 'debug';

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

function toTaskToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function toNonNegativeInt(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0, Math.round(numeric)) : 0;
}

function resolveTaskIdentityCandidates(task: PmTask): string[] {
  const metadata = readTaskMetadata(task);
  const candidates = [
    task.id,
    task.title,
    task.goal,
    metadata.pm_task_id,
    metadata.task_id,
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
  }

  const accumulators = new Map<string, TaskRealtimeTelemetryAccumulator>();

  // Process file edit events
  for (const event of fileEditEvents) {
    const rawTaskId = String(event.taskId || '').trim();
    if (!rawTaskId) {
      continue;
    }
    const mappedTaskId = tokenToTaskId.get(toTaskToken(rawTaskId)) || rawTaskId;
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

export function DirectorWorkspace({
  workspace,
  onBackToMain,
  tasks,
  workers = [],
  directorRunning,
  isStarting,
  onToggleDirector,
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
    if (!workspace) {
      setFallbackTasks([]);
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const syncTasks = async () => {
      try {
        const source = directorRunning ? 'workflow' : 'auto';
        const response = await apiFetchFresh(`/v2/director/tasks?source=${source}`);
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        if (!Array.isArray(payload) || cancelled) {
          return;
        }
        const normalized = payload.filter((item): item is PmTask => {
          return Boolean(item && typeof item === 'object' && String((item as { id?: unknown }).id || '').trim());
        });
        setFallbackTasks(normalized);
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

  const visibleTasks = useMemo(() => {
    const toTaskId = (task: PmTask): string => String(task.id || '').trim();
    const merged = new Map<string, PmTask>();

    // fallback tasks only fill gaps; live realtime tasks are source of truth.
    for (const task of fallbackTasks) {
      const taskId = toTaskId(task);
      if (taskId) {
        merged.set(taskId, task);
      }
    }

    for (const task of tasks) {
      const taskId = toTaskId(task);
      if (taskId) {
        merged.set(taskId, task);
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

  const taskRealtimeTelemetry = useMemo(
    () => buildTaskRealtimeTelemetry(visibleTasks, fileEditEvents, taskProgressMap),
    [visibleTasks, fileEditEvents, taskProgressMap],
  );

  const executionTasks: ExecutionTask[] = visibleTasks.map((task) => {
    const metadata = readTaskMetadata(task);
    const taskId = String(task.id || '').trim();
    const rawStatus = String(task.status || task.state || '').trim().toLowerCase();
    const isCurrent = currentTaskId
      ? task.id === currentTaskId
      : currentTaskTitle
        ? (task.title || task.goal || '').trim() === String(currentTaskTitle || '').trim()
        : false;
    const status = resolveTaskExecutionStatus({
      rawStatus,
      done: Boolean(task.done),
      completed: Boolean(task.completed),
      directorRunning,
      isCurrent,
    });

    const title = String(task.title || task.goal || task.id || '未命名任务').trim();
    const lowered = `${title} ${String(task.goal || '')}`.toLowerCase();
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
    const tags = task.tags || (Array.isArray(metadata.tags) ? metadata.tags : []);
    const telemetry = taskRealtimeTelemetry.get(taskId);
    const filesModified = Math.max(
      Number(task.files_modified || metadata.files_modified || 0) || 0,
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

    return {
      id: task.id || title,
      name: title,
      description: String(task.description || task.goal || '').trim(),
      status,
      type,
      priority: String(priorityValue).toLowerCase() as ExecutionTask['priority'],
      progress: status === 'running' ? 50 : status === 'completed' ? 100 : status === 'failed' ? 0 : undefined,
      output: String(task.summary || task.output || '').trim(),
      error: status === 'failed' || status === 'blocked' ? String(task.error || task.state || task.status || '').trim() : '',
      budget: budgetInfo,
      estimatedTime: task.estimated_time || task.estimatedTime,
      actualTime,
      dependencies: Array.isArray(dependencies) ? dependencies.map((item) => String(item)) : undefined,
      tags: Array.isArray(tags) ? tags.map((tag) => String(tag)) : [],
      createdAt,
      startedAt,
      completedAt,
      assignedWorker: assignedWorker || undefined,
      filesModified,
      // Progress tracking from telemetry (merged from taskProgressMap and fileEditEvents)
      retries: telemetry?.retryCount ?? retries,
      maxRetries: telemetry?.maxRetries,
      currentFilePath: telemetry?.currentFilePath || readTaskString(task, ['current_file', 'current_file_path']),
      activityUpdatedAt: telemetry?.activityUpdatedAt,
      lineStats: telemetry?.lineStats,
      operationStats: telemetry?.operationStats,
      currentPhase: telemetry?.currentPhase,
      phaseIndex: telemetry?.phaseIndex,
      phaseTotal: telemetry?.phaseTotal,
    };
  });
  const executionTaskMap = useMemo(() => {
    const mapping = new Map<string, ExecutionTask>();
    executionTasks.forEach((task) => mapping.set(task.id, task));
    return mapping;
  }, [executionTasks]);
  const isExecuting = directorRunning || Boolean(isStarting);
  const sessionStatus = resolveSessionStatus(directorRunning, Boolean(isStarting), executionTasks);

  const handleTaskSelect = useCallback((taskId: string) => {
    setSelectedTaskId(taskId);
    const task = executionTasks.find(t => t.id === taskId);
    if (task) {
      setTerminalOutput(`选中任务: ${task.name}\n状态: ${task.status}\n类型: ${task.type}\n`);
    }
  }, [executionTasks]);

  const handleExecute = useCallback(async () => {
    const nextAction = directorRunning ? '停止' : '启动';
    const targetName = selectedTaskId
      ? executionTasks.find((task) => task.id === selectedTaskId)?.name || selectedTaskId
      : currentTaskTitle || '当前任务队列';
    const newLog = `[${new Date().toLocaleTimeString()}] ${nextAction} Director 执行: ${targetName}`;
    setTerminalOutput(prev => prev + newLog + '\n');
    onToggleDirector();
  }, [currentTaskTitle, directorRunning, executionTasks, onToggleDirector, selectedTaskId]);

  const handlePause = useCallback(() => {
    if (!directorRunning) {
      return;
    }
    setTerminalOutput(prev => prev + `[${new Date().toLocaleTimeString()}] 停止 Director 执行\n`);
    onToggleDirector();
  }, [directorRunning, onToggleDirector]);

  const handleReset = useCallback(() => {
    setSelectedTaskId(null);
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

  return (
    <div data-testid="director-workspace" className="flex flex-col h-full bg-gradient-to-br from-[var(--ink-indigo)] via-[rgba(28,18,48,0.8)] to-[rgba(14,20,40,0.95)] text-slate-100 overflow-hidden">
      {/* Director Header - Director 主题 */}
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
            <span className="text-xs text-slate-400">待定:</span>
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
            disabled={factoryMode}
            title={factoryMode ? "工厂模式下无法使用此功能" : undefined}
            className="border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/10"
          >
            {isStarting ? (
              <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5 mr-1.5" />
            )}
            {directorRunning ? '停止' : '执行'}
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={handlePause}
            data-testid="director-workspace-pause"
            disabled={!directorRunning}
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
            className="text-slate-400 hover:text-slate-100"
          >
            <Settings className="w-4 h-4" />
          </Button>
        </div>
      </header>

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
        </nav>

        {/* Main Panel */}
        <PanelGroup direction="horizontal" className="flex-1">
          <Panel defaultSize={showAIDialogue ? 60 : 85} minSize={40}>
            <div className="h-full overflow-hidden">
              {activeView === 'tasks' && (
                <DirectorTaskPanel
                  tasks={executionTasks}
                  workers={workers}
                  taskMap={executionTaskMap}
                  selectedTaskId={selectedTaskId}
                  onTaskSelect={handleTaskSelect}
                  onExecute={handleExecute}
                  isExecuting={isExecuting}
                  taskTraceMap={taskTraceMap}
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
                <DirectorCodePanel workspace={workspace} fileEditEvents={fileEditEvents} />
              )}
              {activeView === 'terminal' && (
                <DirectorTerminalPanel output={terminalOutput} />
              )}
              {activeView === 'debug' && (
                <DirectorDebugPanel
                  tasks={executionTasks.filter((task) => task.status === 'failed' || task.status === 'blocked')}
                />
              )}
            </div>
          </Panel>

          {showAIDialogue && (
            <>
              <PanelResizeHandle className="w-1 bg-white/5 hover:bg-indigo-500/30 transition-colors" />
              <Panel defaultSize={40} minSize={25} maxSize={50}>
                <AIDialoguePanel
                  dialogueRole="director"
                  roleDisplayName="大将军"
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
                  }}
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
      className={cn(
        'w-10 h-10 rounded-xl flex flex-col items-center justify-center gap-0.5 transition-all duration-200',
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
      case 'pending': return '待定';
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
            label="待定"
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

// Code Panel
interface DirectorCodePanelProps {
  workspace: string;
  fileEditEvents: FileEditEvent[];
}

function DirectorCodePanel({ workspace, fileEditEvents }: DirectorCodePanelProps) {
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);

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

  // 只显示最近的 20 个事件，按时间倒序
  const recentEvents = [...fileEditEvents].reverse().slice(0, 20);

  const toggleExpand = (eventId: string) => {
    setExpandedEventId(prev => prev === eventId ? null : eventId);
  };

  return (
    <div className="h-full flex flex-col">
      <div className="h-12 flex items-center justify-between px-4 border-b border-white/5">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-medium text-slate-200">实时代码变更</h2>
          {fileEditEvents.length > 0 && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-400">
              {fileEditEvents.length} 个文件
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" className="text-slate-400">
            <FileCode className="w-4 h-4 mr-1.5" />
            打开文件
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-hidden flex">
        {/* 文件变更列表 + Diff 详情 */}
        <div className="flex-1 overflow-auto p-4">
          {recentEvents.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-slate-500">
              <FileCode className="w-12 h-12 mb-4 text-indigo-500/30" />
              <p>等待代码变更...</p>
              <p className="text-xs mt-2 opacity-70">Director 执行时将实时显示文件修改</p>
            </div>
          ) : (
            <div className="space-y-2">
              {recentEvents.map((event, index) => (
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
                          {event.patch && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400">
                              Diff
                            </span>
                          )}
                        </div>
                        <div className="mt-1 flex items-center gap-3 text-[10px] text-slate-500">
                          <span>{event.contentSize} bytes</span>
                          {event.taskId && <span className="text-slate-600">任务: {event.taskId.slice(0, 8)}</span>}
                          <span className="text-slate-600">
                            {new Date(event.timestamp).toLocaleTimeString()}
                          </span>
                          {event.patch && (
                            <span className="text-cyan-400">
                              {expandedEventId === event.id ? '▼ 收起' : '▶ 展开 Diff'}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 展开的 Diff 详情 */}
                  {expandedEventId === event.id && event.patch && (
                    <div className="mt-2">
                      <RealTimeFileDiff
                        filePath={event.filePath}
                        operation={event.operation}
                        patch={event.patch}
                        compact
                      />
                    </div>
                  )}
                </div>
              ))}
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
function DirectorTerminalPanel({ output }: { output: string }) {
  return (
    <div className="h-full flex flex-col">
      <div className="h-12 flex items-center justify-between px-4 border-b border-white/5">
        <h2 className="text-sm font-medium text-slate-200">执行终端</h2>
        <Button variant="ghost" size="sm" className="text-slate-400">
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
function DirectorDebugPanel({ tasks }: { tasks: ExecutionTask[] }) {
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
                  <Button size="sm" variant="outline" className="border-red-500/30 text-red-400">
                    调试
                  </Button>
                  <Button size="sm" variant="ghost" className="text-slate-400">
                    跳过
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
