/**
 * Runtime DirectorWorkspace - DirectorWorkspace 状态选择器与 VM
 * 
 * 为 DirectorWorkspace 组件提供状态选择和 ViewModel
 */

import { useMemo, useState, useEffect, useCallback, useRef } from 'react';
import { useRuntime } from '@/app/hooks/useRuntime';
import type { PmTask, TaskStatus } from '@/types/task';
import type { LogEntry } from '@/types/log';
import type { FileEditEvent, RuntimeWorkerState } from '@/app/hooks/useRuntime';
import type { TaskTraceMap } from '@/types/taskTrace';
import { apiFetchFresh } from '@/api';

// ============================================================
// 类型定义
// ============================================================

export interface DirectorWorkspaceState {
  // 基本信息
  workspace: string;
  connected: boolean;
  
  // 任务状态
  tasks: PmTask[];
  currentTaskId: string | null;
  currentTaskTitle: string | null;
  currentTaskStatus: string | null;
  
  // Director 状态
  directorRunning: boolean;
  isStarting: boolean;
  
  // Worker 状态
  workers: RuntimeWorkerState[];
  
  // 日志
  executionLogs: LogEntry[];
  llmStreamEvents: LogEntry[];
  processStreamEvents: LogEntry[];
  fileEditEvents: FileEditEvent[];
  
  // 阶段
  currentPhase: string;
  
  // 进度跟踪
  taskProgressMap: Map<string, {
    phase?: string;
    phaseIndex?: number;
    phaseTotal?: number;
    retryCount?: number;
    maxRetries?: number;
    currentFile?: string;
  }>;
  taskTraceMap?: TaskTraceMap;
}

export interface DirectorTask {
  id: string;
  name: string;
  description?: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'blocked';
  type: 'code' | 'test' | 'debug' | 'review';
  progress?: number;
  output?: string;
  error?: string;
  priority?: 'low' | 'medium' | 'high' | 'critical';
  budget?: { used: number; total: number; unit: 'tokens' | 'requests' | 'time' };
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
  currentPhase?: string;
  phaseIndex?: number;
  phaseTotal?: number;
}

export interface TaskLineStats {
  added: number;
  deleted: number;
  modified: number;
}

export interface TaskOperationStats {
  create: number;
  modify: number;
  delete: number;
}

export interface TaskRealtimeTelemetry {
  currentFilePath?: string;
  activityUpdatedAt?: string;
  filesTouchedCount: number;
  lineStats: TaskLineStats;
  operationStats: TaskOperationStats;
  retryCount?: number;
  maxRetries?: number;
  currentPhase?: string;
  phaseIndex?: number;
  phaseTotal?: number;
}

export interface DirectorWorkspaceVM {
  // 状态
  state: DirectorWorkspaceState;
  
  // 计算属性
  executionTasks: DirectorTask[];
  taskMap: Map<string, DirectorTask>;
  visibleTasks: PmTask[];
  
  // 统计
  runningTasksCount: number;
  completedTasksCount: number;
  failedTasksCount: number;
  pendingTasksCount: number;
  totalTasksCount: number;
  progress: number;
  
  // Worker 统计
  workerBusyCount: number;
  workerIdleCount: number;
  workerFailedCount: number;
  
  // 会话状态
  sessionStatus: 'idle' | 'running' | 'paused' | 'completed';
  isExecuting: boolean;
  
  // Actions
  handleTaskSelect: (taskId: string) => void;
  handleExecute: () => void;
  handlePause: () => void;
  handleReset: () => void;
  handleRefresh: () => void;
}

// ============================================================
// 纯函数工具
// ============================================================

export function resolveTaskExecutionStatus(params: {
  rawStatus: string;
  done: boolean;
  completed: boolean;
  directorRunning: boolean;
  isCurrent: boolean;
}): DirectorTask['status'] {
  const normalized = String(params.rawStatus || '').trim().toLowerCase();
  const completed = params.done || params.completed || ['completed', 'done', 'success'].includes(normalized);
  
  if (completed) return 'completed';
  if (['failed', 'error'].includes(normalized)) return 'failed';
  if (['blocked', 'cancelled', 'canceled'].includes(normalized)) return 'blocked';
  if (['running', 'in_progress', 'claimed'].includes(normalized)) return 'running';
  if (params.directorRunning && params.isCurrent) return 'running';
  return 'pending';
}

export function resolveSessionStatus(
  directorRunning: boolean,
  isStarting: boolean,
  tasks: DirectorTask[],
): 'idle' | 'running' | 'paused' | 'completed' {
  if (directorRunning || isStarting) return 'running';
  if (tasks.length > 0 && tasks.every((task) => task.status === 'completed')) return 'completed';
  if (tasks.some((task) => task.status === 'blocked')) return 'paused';
  return 'idle';
}

function readTaskMetadata(task: PmTask): Record<string, unknown> {
  return task.metadata && typeof task.metadata === 'object' ? task.metadata : {};
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

export function computePatchLineStats(
  patch: string | undefined,
  operation: FileEditEvent['operation'],
): TaskLineStats {
  const text = String(patch || '');
  if (!text) return { added: 0, deleted: 0, modified: 0 };
  
  const lines = text.split('\n');
  const hasDiffMarkers = lines.some((line) => 
    line.startsWith('@@') || line.startsWith('+++ ') || line.startsWith('--- ')
  );
  
  if (!hasDiffMarkers) {
    const rawLineCount = lines.filter((line) => line.trim().length > 0).length;
    if (operation === 'delete') return { added: 0, deleted: rawLineCount, modified: 0 };
    return { added: rawLineCount, deleted: 0, modified: 0 };
  }
  
  let plus = 0, minus = 0;
  for (const line of lines) {
    if (!line) continue;
    if (line.startsWith('+++ ') || line.startsWith('--- ') || line.startsWith('@@')) continue;
    if (line.startsWith('+')) { plus += 1; continue; }
    if (line.startsWith('-')) minus += 1;
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
    added: Math.max(0, Number(event.addedLines) || 0),
    deleted: Math.max(0, Number(event.deletedLines) || 0),
    modified: Math.max(0, Number(event.modifiedLines) || 0),
  };
  if (backendStats.added > 0 || backendStats.deleted > 0 || backendStats.modified > 0) {
    return backendStats;
  }
  return computePatchLineStats(event.patch, event.operation);
}

function toTaskToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function resolveTaskIdentityCandidates(task: PmTask): string[] {
  const metadata = readTaskMetadata(task);
  const candidates = [task.id, task.title, task.goal, metadata.pm_task_id, metadata.task_id, metadata.id];
  const normalized: string[] = [];
  const seen = new Set<string>();
  
  for (const candidate of candidates) {
    const token = toTaskToken(candidate);
    if (!token || seen.has(token)) continue;
    seen.add(token);
    normalized.push(token);
  }
  return normalized;
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
    if (!taskId) continue;
    taskIdSet.add(taskId);
    const candidates = resolveTaskIdentityCandidates(task);
    for (const token of candidates) {
      tokenToTaskId.set(token, taskId);
    }
  }
  
  const accumulators = new Map<string, {
    currentFilePath?: string;
    activityUpdatedAt?: string;
    filesTouched: Set<string>;
    lineStats: TaskLineStats;
    operationStats: TaskOperationStats;
    retryCount?: number;
    maxRetries?: number;
    currentPhase?: string;
    phaseIndex?: number;
    phaseTotal?: number;
  }>();
  
  // Process file edit events
  for (const event of fileEditEvents) {
    const rawTaskId = String(event.taskId || '').trim();
    if (!rawTaskId) continue;
    
    const mappedTaskId = tokenToTaskId.get(toTaskToken(rawTaskId)) || rawTaskId;
    if (!taskIdSet.has(mappedTaskId)) continue;
    
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
    if (event.filePath) accumulator.filesTouched.add(event.filePath);
    
    const previousEpoch = Date.parse(String(accumulator.activityUpdatedAt || ''));
    const nextEpoch = Date.parse(String(event.timestamp || ''));
    const shouldReplace = !Number.isFinite(previousEpoch) || 
      (Number.isFinite(nextEpoch) && nextEpoch >= previousEpoch);
    
    if (shouldReplace) {
      accumulator.currentFilePath = event.filePath || accumulator.currentFilePath;
      accumulator.activityUpdatedAt = event.timestamp || accumulator.activityUpdatedAt;
    }
    
    accumulators.set(mappedTaskId, accumulator);
  }
  
  // Merge task progress data
  if (taskProgressMap) {
    for (const [taskId, progress] of taskProgressMap.entries()) {
      if (!taskIdSet.has(taskId)) continue;
      
      const accumulator = accumulators.get(taskId) || {
        filesTouched: new Set<string>(),
        lineStats: { added: 0, deleted: 0, modified: 0 },
        operationStats: { create: 0, modify: 0, delete: 0 },
      };
      
      if (progress.retryCount !== undefined) accumulator.retryCount = progress.retryCount;
      if (progress.maxRetries !== undefined) accumulator.maxRetries = progress.maxRetries;
      if (progress.phase) accumulator.currentPhase = progress.phase;
      if (progress.phaseIndex !== undefined) accumulator.phaseIndex = progress.phaseIndex;
      if (progress.phaseTotal !== undefined) accumulator.phaseTotal = progress.phaseTotal;
      if (progress.currentFile) accumulator.currentFilePath = progress.currentFile;
      
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

export function formatTelemetryTime(value: string | undefined): string {
  if (!value) return '';
  const epoch = Date.parse(value);
  if (!Number.isFinite(epoch)) return '';
  return new Date(epoch).toLocaleTimeString();
}

// ============================================================
// Hooks
// ============================================================

/**
 * DirectorWorkspace ViewModel Hook
 */
export function useDirectorWorkspaceVM(
  workspace: string,
  onToggleDirector: () => void,
): DirectorWorkspaceVM {
  const runtime = useRuntime({ roles: ['director', 'qa'] });
  
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [fallbackTasks, setFallbackTasks] = useState<PmTask[]>([]);
  const [terminalOutput, setTerminalOutput] = useState<string>('');
  
  // Polling for fallback tasks
  useEffect(() => {
    if (!workspace) {
      setFallbackTasks([]);
      return;
    }
    
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    
    const syncTasks = async () => {
      try {
        const source = runtime.directorStatus?.running ? 'workflow' : 'auto';
        const payload = await apiFetchFresh(`/v2/director/tasks?source=${source}`);
        if (!Array.isArray(payload) || cancelled) return;
        
        const normalized = payload.filter((item): item is PmTask => 
          Boolean(item && typeof item === 'object' && String((item as { id?: unknown }).id || '').trim())
        );
        setFallbackTasks(normalized);
      } catch {
        // Ignore polling errors
      }
    };
    
    void syncTasks();
    timer = setInterval(() => { void syncTasks(); }, runtime.directorStatus?.running ? 1500 : 4000);
    
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [workspace, runtime.directorStatus?.running]);
  
  // Merge tasks
  const visibleTasks = useMemo(() => {
    const toTaskId = (task: PmTask): string => String(task.id || '').trim();
    const merged = new Map<string, PmTask>();
    
    for (const task of fallbackTasks) {
      const taskId = toTaskId(task);
      if (taskId) merged.set(taskId, task);
    }
    
    for (const task of runtime.tasks) {
      const taskId = toTaskId(task);
      if (taskId) merged.set(taskId, task);
    }
    
    const orderedIds: string[] = [];
    for (const task of fallbackTasks) {
      const taskId = toTaskId(task);
      if (taskId && !orderedIds.includes(taskId)) orderedIds.push(taskId);
    }
    for (const task of runtime.tasks) {
      const taskId = toTaskId(task);
      if (taskId && !orderedIds.includes(taskId)) orderedIds.push(taskId);
    }
    
    return orderedIds.map((taskId) => merged.get(taskId)).filter((task): task is PmTask => Boolean(task));
  }, [runtime.tasks, fallbackTasks]);
  
  // Build telemetry
  const taskRealtimeTelemetry = useMemo(
    () => buildTaskRealtimeTelemetry(visibleTasks, runtime.fileEditEvents, runtime.taskProgressMap),
    [visibleTasks, runtime.fileEditEvents, runtime.taskProgressMap],
  );
  
  // Convert to execution tasks
  const executionTasks = useMemo((): DirectorTask[] => {
    return visibleTasks.map((task) => {
      const taskId = String(task.id || '').trim();
      const rawStatus = String(task.status || task.state || '').trim().toLowerCase();
      const isCurrent = runtime.currentPhase?.includes('executing');
      
      const status = resolveTaskExecutionStatus({
        rawStatus,
        done: Boolean(task.done),
        completed: Boolean(task.completed),
        directorRunning: runtime.directorStatus?.running === true,
        isCurrent,
      });
      
      const title = String(task.title || task.goal || task.id || '未命名任务').trim();
      const lowered = `${title} ${String(task.goal || '')}`.toLowerCase();
      const type: DirectorTask['type'] = lowered.includes('test') ? 'test'
        : lowered.includes('debug') || lowered.includes('fix') ? 'debug'
        : lowered.includes('review') || lowered.includes('audit') ? 'review'
        : 'code';
      
      const metadata = readTaskMetadata(task);
      const budgetRaw = metadata.budget && typeof metadata.budget === 'object' ? metadata.budget : task.budget;
      const budgetInfo = budgetRaw && typeof budgetRaw === 'object' ? {
        used: Number((budgetRaw as Record<string, number>).used) || 0,
        total: Number((budgetRaw as Record<string, number>).total) || 100,
        unit: ((budgetRaw as Record<string, string>).unit || 'tokens') as 'tokens' | 'requests' | 'time',
      } : undefined;
      
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
      const dependencies = task.dependencies || task.blocked_by || 
        (Array.isArray(metadata.dependencies) ? metadata.dependencies : undefined);
      const tags = task.tags || (Array.isArray(metadata.tags) ? metadata.tags : []);
      const telemetry = taskRealtimeTelemetry.get(taskId);
      
      const filesModified = Math.max(
        Number(task.files_modified || metadata.files_modified || 0) || 0,
        telemetry?.filesTouchedCount || 0,
      );
      
      const retries = Number(
        task.retries || task.retry_count || metadata.retry_count || metadata.retries || 0
      ) || 0;
      
      const assignedWorker = readTaskString(task, [
        'assigned_worker', 'worker_id', 'claimed_by', 'assignedTo', 'assignee',
      ]);
      
      return {
        id: task.id || title,
        name: title,
        description: String(task.description || task.goal || '').trim(),
        status,
        type,
        priority: String(priorityValue).toLowerCase() as DirectorTask['priority'],
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
  }, [visibleTasks, runtime.directorStatus?.running, runtime.currentPhase, taskRealtimeTelemetry]);
  
  const taskMap = useMemo(() => {
    const mapping = new Map<string, DirectorTask>();
    executionTasks.forEach((task) => mapping.set(task.id, task));
    return mapping;
  }, [executionTasks]);
  
  const isExecuting = runtime.directorStatus?.running === true;
  const sessionStatus = resolveSessionStatus(isExecuting, false, executionTasks);
  
  // Actions
  const handleTaskSelect = useCallback((taskId: string) => {
    setSelectedTaskId(taskId);
    const task = executionTasks.find(t => t.id === taskId);
    if (task) {
      setTerminalOutput(`选中任务: ${task.name}\n状态: ${task.status}\n类型: ${task.type}\n`);
    }
  }, [executionTasks]);
  
  const handleExecute = useCallback(() => {
    const nextAction = isExecuting ? '停止' : '启动';
    const targetName = selectedTaskId 
      ? executionTasks.find((task) => task.id === selectedTaskId)?.name || selectedTaskId
      : '当前任务队列';
    const newLog = `[${new Date().toLocaleTimeString()}] ${nextAction} Director 执行: ${targetName}`;
    setTerminalOutput(prev => prev + newLog + '\n');
    onToggleDirector();
  }, [isExecuting, selectedTaskId, executionTasks, onToggleDirector]);
  
  const handlePause = useCallback(() => {
    if (!isExecuting) return;
    setTerminalOutput(prev => prev + `[${new Date().toLocaleTimeString()}] 停止 Director 执行\n`);
    onToggleDirector();
  }, [isExecuting, onToggleDirector]);
  
  const handleReset = useCallback(() => {
    setSelectedTaskId(null);
    setTerminalOutput('');
  }, []);
  
  const handleRefresh = useCallback(() => {
    // Trigger a re-sync
    setFallbackTasks([]);
  }, []);
  
  // Compute statistics
  const runningTasksCount = executionTasks.filter(t => t.status === 'running').length;
  const completedTasksCount = executionTasks.filter(t => t.status === 'completed').length;
  const failedTasksCount = executionTasks.filter(t => t.status === 'failed').length;
  const pendingTasksCount = executionTasks.filter(t => t.status === 'pending').length;
  const totalTasksCount = executionTasks.length;
  const progress = totalTasksCount > 0 ? Math.round((completedTasksCount / totalTasksCount) * 100) : 0;
  
  const workerBusyCount = runtime.workers.filter(w => w.status === 'busy').length;
  const workerIdleCount = runtime.workers.filter(w => w.status === 'idle').length;
  const workerFailedCount = runtime.workers.filter(w => w.status === 'failed').length;
  
  // Get current task info from engineStatus
  const currentTaskId = runtime.engineStatus?.roles?.Director?.task_id ?? null;
  const currentTaskTitle = runtime.engineStatus?.roles?.Director?.task_title ?? null;
  const currentTaskStatus = runtime.engineStatus?.roles?.Director?.status ?? null;
  
  const state: DirectorWorkspaceState = {
    workspace,
    connected: runtime.live,
    tasks: runtime.tasks,
    currentTaskId,
    currentTaskTitle,
    currentTaskStatus,
    directorRunning: isExecuting,
    isStarting: false,
    workers: runtime.workers,
    executionLogs: runtime.executionLogs,
    llmStreamEvents: runtime.llmStreamEvents,
    processStreamEvents: runtime.processStreamEvents,
    fileEditEvents: runtime.fileEditEvents,
    currentPhase: runtime.currentPhase,
    taskProgressMap: runtime.taskProgressMap,
    taskTraceMap: runtime.taskTraceMap,
  };
  
  return {
    state,
    executionTasks,
    taskMap,
    visibleTasks,
    runningTasksCount,
    completedTasksCount,
    failedTasksCount,
    pendingTasksCount,
    totalTasksCount,
    progress,
    workerBusyCount,
    workerIdleCount,
    workerFailedCount,
    sessionStatus,
    isExecuting,
    handleTaskSelect,
    handleExecute,
    handlePause,
    handleReset,
    handleRefresh,
  };
}

// ============================================================
// 纯函数导出（供测试）
// ============================================================

export const selectors = {
  resolveTaskExecutionStatus,
  resolveSessionStatus,
  computePatchLineStats,
  buildTaskRealtimeTelemetry,
  formatTelemetryTime,
};
