/**
 * useDirectorWorkspace - DirectorWorkspace的状态管理Hook
 * 将业务逻辑从组件中抽离，实现容器/展示分离
 */
import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { apiFetchFresh } from '@/api';
import type { PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';
import type { FileEditEvent, RuntimeWorkerState } from '@/app/hooks/useRuntime';

export type DirectorActiveView = 'tasks' | 'code' | 'activity' | 'terminal' | 'debug';

export interface FileWriteEvent {
  filePath: string;
  operation: 'create' | 'modify' | 'delete';
  addedLines: number;
  deletedLines: number;
  modifiedLines: number;
  timestamp: string;
}

export interface ExecutionTask {
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
  // Real-time file write tracking
  currentFile?: string;
  currentFilePath?: string;
  activityUpdatedAt?: string;
  fileWriteEvents?: FileWriteEvent[];
  totalAddedLines?: number;
  totalDeletedLines?: number;
  totalModifiedLines?: number;
  lineStats?: {
    added: number;
    deleted: number;
    modified: number;
  };
  operationStats?: {
    create: number;
    modify: number;
    delete: number;
  };
  // Phase information
  currentPhase?: string;
  phaseIndex?: number;
  phaseTotal?: number;
}

export interface ExecutionSession {
  id: string;
  status: 'idle' | 'running' | 'paused' | 'completed';
  currentTask?: ExecutionTask;
  logs: string[];
}

interface ResolveTaskExecutionStatusParams {
  rawStatus: string;
  done: boolean;
  completed: boolean;
  directorRunning: boolean;
  isCurrent: boolean;
}

export function resolveTaskExecutionStatus(
  params: ResolveTaskExecutionStatusParams
): ExecutionTask['status'] {
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

// 阶段到视图的映射
const PHASE_TO_VIEW: Record<string, { view: DirectorActiveView; label: string }> = {
  idle: { view: 'tasks', label: '等待' },
  planning: { view: 'tasks', label: '规划' },
  analyzing: { view: 'activity', label: '分析' },
  executing: { view: 'code', label: '代码' },
  llm_calling: { view: 'activity', label: '思考' },
  tool_running: { view: 'terminal', label: '执行' },
  verification: { view: 'activity', label: '验证' },
  completed: { view: 'tasks', label: '完成' },
  error: { view: 'activity', label: '错误' },
};

interface UseDirectorWorkspaceParams {
  workspace: string;
  tasks: PmTask[];
  workers: RuntimeWorkerState[];
  directorRunning: boolean;
  isStarting: boolean;
  currentTaskId?: string | null;
  currentTaskTitle?: string | null;
  currentTaskStatus?: string | null;
  currentPhase: string;
  onToggleDirector: () => void;
}

interface UseDirectorWorkspaceReturn {
  // 状态
  activeView: DirectorActiveView;
  showAIDialogue: boolean;
  session: ExecutionSession;
  selectedTaskId: string | null;
  terminalOutput: string;
  fallbackTasks: PmTask[];
  userSwitchedViewRef: React.MutableRefObject<boolean>;

  // 计算值
  visibleTasks: PmTask[];
  executionTasks: ExecutionTask[];
  executionTaskMap: Map<string, ExecutionTask>;
  isExecuting: boolean;
  sessionStatus: ExecutionSession['status'];
  runningTasks: number;
  completedTasks: number;
  failedTasks: number;
  pendingTasks: number;
  totalTasks: number;
  progress: number;

  // 操作
  setActiveView: (view: DirectorActiveView) => void;
  setShowAIDialogue: (show: boolean) => void;
  setSelectedTaskId: (id: string | null) => void;
  handleViewChange: (view: DirectorActiveView) => void;
  handleTaskSelect: (taskId: string) => void;
  handleExecute: () => void;
  handlePause: () => void;
  handleReset: () => void;

  // 文件写入和进度事件处理
  handleFileWriteEvent: (event: {
    task_id?: string;
    file_path?: string;
    operation?: string;
    added_lines?: number;
    deleted_lines?: number;
    modified_lines?: number;
    timestamp?: string;
  }) => void;
  handleTaskProgressEvent: (event: {
    task_id?: string;
    phase?: string;
    phase_index?: number;
    phase_total?: number;
    retry_count?: number;
    max_retries?: number;
  }) => void;
}

export function useDirectorWorkspace({
  workspace,
  tasks,
  workers,
  directorRunning,
  isStarting,
  currentTaskId,
  currentTaskTitle,
  currentTaskStatus,
  currentPhase,
  onToggleDirector,
}: UseDirectorWorkspaceParams): UseDirectorWorkspaceReturn {
  const [activeView, setActiveViewState] = useState<DirectorActiveView>('tasks');
  const [showAIDialogue, setShowAIDialogue] = useState(true);
  const [session] = useState<ExecutionSession>({
    id: `dir-${Date.now()}`,
    status: 'idle',
    logs: [],
  });
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [terminalOutput, setTerminalOutput] = useState<string>('');
  const [fallbackTasks, setFallbackTasks] = useState<PmTask[]>([]);

  // 文件写入事件和任务进度跟踪
  const [taskFileEvents, setTaskFileEvents] = useState<Map<string, FileWriteEvent[]>>(new Map());
  const [taskProgressMap, setTaskProgressMap] = useState<Map<string, {
    currentFile?: string;
    currentPhase?: string;
    phaseIndex?: number;
    phaseTotal?: number;
    retries?: number;
    maxRetries?: number;
  }>>(new Map());

  // 用户手动切换视图的标记
  const userSwitchedViewRef = useRef(false);
  const lastPhaseRef = useRef<string>('');

  // 处理文件写入事件
  const handleFileWriteEvent = useCallback((event: {
    task_id?: string;
    file_path?: string;
    operation?: string;
    added_lines?: number;
    deleted_lines?: number;
    modified_lines?: number;
    timestamp?: string;
  }) => {
    const taskId = event.task_id;
    if (!taskId) return;

    const fileEvent: FileWriteEvent = {
      filePath: event.file_path || '',
      operation: (event.operation as 'create' | 'modify' | 'delete') || 'modify',
      addedLines: event.added_lines || 0,
      deletedLines: event.deleted_lines || 0,
      modifiedLines: event.modified_lines || 0,
      timestamp: event.timestamp || new Date().toISOString(),
    };

    setTaskFileEvents((prev) => {
      const newMap = new Map(prev);
      const existing = newMap.get(taskId) || [];
      newMap.set(taskId, [...existing, fileEvent]);
      return newMap;
    });

    // Update current file in progress map
    setTaskProgressMap((prev) => {
      const newMap = new Map(prev);
      const existing = newMap.get(taskId) || {};
      newMap.set(taskId, { ...existing, currentFile: fileEvent.filePath });
      return newMap;
    });
  }, []);

  // 处理任务进度事件
  const handleTaskProgressEvent = useCallback((event: {
    task_id?: string;
    phase?: string;
    phase_index?: number;
    phase_total?: number;
    retry_count?: number;
    max_retries?: number;
  }) => {
    const taskId = event.task_id;
    if (!taskId) return;

    setTaskProgressMap((prev) => {
      const newMap = new Map(prev);
      const existing = newMap.get(taskId) || {};
      newMap.set(taskId, {
        ...existing,
        currentPhase: event.phase || existing.currentPhase,
        phaseIndex: event.phase_index ?? existing.phaseIndex,
        phaseTotal: event.phase_total ?? existing.phaseTotal,
        retries: event.retry_count ?? existing.retries,
        maxRetries: event.max_retries ?? existing.maxRetries,
      });
      return newMap;
    });
  }, []);

  // 自动切换视图基于当前阶段
  useEffect(() => {
    if (!directorRunning || userSwitchedViewRef.current) return;

    const phaseConfig = PHASE_TO_VIEW[currentPhase] || PHASE_TO_VIEW.idle;

    if (currentPhase !== lastPhaseRef.current) {
      lastPhaseRef.current = currentPhase;

      if (phaseConfig.view !== activeView) {
        setActiveViewState(phaseConfig.view);
      }
    }
  }, [currentPhase, directorRunning, activeView]);

  // 用户手动点击导航时记录偏好
  const handleViewChange = useCallback((view: DirectorActiveView) => {
    userSwitchedViewRef.current = true;
    setActiveViewState(view);
  }, []);

  // 设置activeView的包装器
  const setActiveView = useCallback((view: DirectorActiveView) => {
    setActiveViewState(view);
  }, []);

  // 同步任务
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
        const payload = await apiFetchFresh(`/v2/director/tasks?source=${source}`);
        if (!Array.isArray(payload) || cancelled) {
          return;
        }
        const normalized = payload
          .filter((item): item is PmTask => {
            return Boolean(item && typeof item === 'object' && String((item as { id?: unknown }).id || '').trim());
          })
          .map((item) => ({
            ...item,
            metadata: {
              ...readTaskMetadata(item),
              director_task_source: source,
            },
          }));
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

  // 合并任务
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

  // 转换执行任务
  const executionTasks: ExecutionTask[] = useMemo(() => {
    return visibleTasks.map((task) => {
      const metadata = readTaskMetadata(task);
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
      const filesModified = Number(task.files_modified || metadata.files_modified || 0) || 0;
      const retries = Number(task.retries || task.retry_count || metadata.retry_count || 0) || 0;
      const maxRetries = Number(metadata.max_retries || metadata.maxRetries || 3) || 3;
      const assignedWorker = readTaskString(task, [
        'assigned_worker',
        'worker_id',
        'claimed_by',
        'assignedTo',
        'assignee',
      ]);

      // Get real-time progress data
      const fileEvents = taskFileEvents.get(task.id) || [];
      const progressData = taskProgressMap.get(task.id) || {};

      // Calculate line stats from file events
      const totalAddedLines = fileEvents.reduce((sum, e) => sum + (e.addedLines || 0), 0);
      const totalDeletedLines = fileEvents.reduce((sum, e) => sum + (e.deletedLines || 0), 0);
      const totalModifiedLines = fileEvents.reduce((sum, e) => sum + (e.modifiedLines || 0), 0);

      // Calculate progress based on phases if available
      let progress: number | undefined;
      if (progressData.phaseTotal && progressData.phaseIndex) {
        progress = Math.round((progressData.phaseIndex / progressData.phaseTotal) * 100);
      } else if (status === 'running') {
        progress = 50;
      } else if (status === 'completed') {
        progress = 100;
      } else if (status === 'failed') {
        progress = 0;
      }

      return {
        id: task.id || title,
        name: title,
        rawStatus,
        description: String(task.description || task.goal || '').trim(),
        status,
        type,
        priority: String(priorityValue).toLowerCase() as ExecutionTask['priority'],
        progress,
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
        claimedBy: readTaskString(task, ['claimed_by', 'claimedBy', 'worker_id']) || undefined,
        pmTaskId: readTaskString(task, ['pm_task_id', 'task_id']) || task.id || undefined,
        blueprintId: readTaskString(task, ['blueprint_id', 'blueprintId']) || undefined,
        blueprintPath: readTaskString(task, ['blueprint_path', 'runtime_blueprint_path']) || undefined,
        source: readTaskString(task, ['director_task_source', 'source']) || undefined,
        filesModified: filesModified || fileEvents.length,
        retries,
        maxRetries,
        // Real-time tracking data
        currentFile: progressData.currentFile,
        fileWriteEvents: fileEvents,
        totalAddedLines,
        totalDeletedLines,
        totalModifiedLines,
        currentPhase: progressData.currentPhase,
        phaseIndex: progressData.phaseIndex,
        phaseTotal: progressData.phaseTotal,
      };
    });
  }, [visibleTasks, currentTaskId, currentTaskTitle, directorRunning, taskFileEvents, taskProgressMap]);

  const executionTaskMap = useMemo(() => {
    const mapping = new Map<string, ExecutionTask>();
    executionTasks.forEach((task) => mapping.set(task.id, task));
    return mapping;
  }, [executionTasks]);

  const isExecuting = directorRunning || Boolean(isStarting);
  const sessionStatus = resolveSessionStatus(directorRunning, Boolean(isStarting), executionTasks);

  // 终端输出更新
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

  // 任务统计
  const runningTasks = executionTasks.filter((t) => t.status === 'running').length;
  const completedTasks = executionTasks.filter((t) => t.status === 'completed').length;
  const failedTasks = executionTasks.filter((t) => t.status === 'failed').length;
  const pendingTasks = executionTasks.filter((t) => t.status === 'pending').length;
  const totalTasks = executionTasks.length;
  const progress = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;

  // 操作回调
  const handleTaskSelect = useCallback((taskId: string) => {
    setSelectedTaskId(taskId);
    const task = executionTasks.find((t) => t.id === taskId);
    if (task) {
      setTerminalOutput((prev) => prev + `选中任务: ${task.name}\n状态: ${task.status}\n类型: ${task.type}\n`);
    }
  }, [executionTasks]);

  const handleExecute = useCallback(() => {
    const nextAction = directorRunning ? '停止' : '启动';
    const targetName = selectedTaskId
      ? executionTasks.find((task) => task.id === selectedTaskId)?.name || selectedTaskId
      : currentTaskTitle || '当前任务队列';
    const newLog = `[${new Date().toLocaleTimeString()}] ${nextAction} Director 执行: ${targetName}`;
    setTerminalOutput((prev) => prev + newLog + '\n');
    onToggleDirector();
  }, [currentTaskTitle, directorRunning, executionTasks, onToggleDirector, selectedTaskId]);

  const handlePause = useCallback(() => {
    if (!directorRunning) return;
    setTerminalOutput((prev) => prev + `[${new Date().toLocaleTimeString()}] 停止 Director 执行\n`);
    onToggleDirector();
  }, [directorRunning, onToggleDirector]);

  const handleReset = useCallback(() => {
    setSelectedTaskId(null);
    setTerminalOutput('');
  }, []);

  return {
    // 状态
    activeView,
    showAIDialogue,
    session,
    selectedTaskId,
    terminalOutput,
    fallbackTasks,
    userSwitchedViewRef,

    // 计算值
    visibleTasks,
    executionTasks,
    executionTaskMap,
    isExecuting,
    sessionStatus,
    runningTasks,
    completedTasks,
    failedTasks,
    pendingTasks,
    totalTasks,
    progress,

    // 操作
    setActiveView,
    setShowAIDialogue,
    setSelectedTaskId,
    handleViewChange,
    handleTaskSelect,
    handleExecute,
    handlePause,
    handleReset,

    // 文件写入和进度事件处理
    handleFileWriteEvent,
    handleTaskProgressEvent,
  };
}
