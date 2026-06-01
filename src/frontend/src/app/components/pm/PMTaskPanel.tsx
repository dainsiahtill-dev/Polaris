import { useEffect, useState, useMemo } from 'react';
import {
  Search,
  Filter,
  Play,
  Pause,
  CheckCircle2,
  Circle,
  Clock,
  AlertCircle,
  ArrowUpDown,
  FileCode,
  GitBranch,
  ListChecks,
  ShieldCheck,
  Target,
  Plus,
  Loader2,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { Badge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import {
  getPmTask,
  listPmTaskAssignments,
  searchPmTasks,
  type PmTaskAssignmentEntry,
  type PmTaskSearchResult,
} from '@/services/pmService';
import { pmTaskService } from '@/services/api';
import { TaskStatus, type PmTask } from '@/types/task';
import type { TaskTraceMap } from '@/app/types/taskTrace';
import { TaskTraceInline } from '../common/TaskTraceInline';
import { TaskTraceTimeline } from '../common/TaskTraceTimeline';

interface PMTaskPanelProps {
  tasks: PmTask[];
  selectedTaskId: string | null;
  onTaskSelect: (taskId: string | null) => void;
  onTaskCreated?: (task: PmTask) => void;
  pmRunning: boolean;
  taskTraceMap?: TaskTraceMap;
  workspace?: string;
  createDisabledReason?: string;
}

type TaskFilter = 'all' | 'pending' | 'running' | 'completed' | 'blocked';
type TaskSort = 'priority' | 'status' | 'created' | 'name';

interface PmTaskDetailEvidence {
  taskId: string;
  loading: boolean;
  error: string | null;
  task: PmTask | null;
}

interface PmTaskAssignmentEvidence {
  taskId: string;
  loading: boolean;
  error: string | null;
  assignments: PmTaskAssignmentEntry[];
  count: number;
}

interface PmTaskCreateEvidence {
  loading: boolean;
  error: string | null;
  task: PmTask | null;
}

function EndpointBadge({
  endpoint,
  method,
  testId,
}: {
  endpoint: string;
  method?: string;
  testId?: string;
}) {
  return (
    <span
      className="shrink-0 rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] font-medium text-slate-500"
      title={endpoint}
      data-endpoint={endpoint}
      data-testid={testId}
    >
      {method ? `${method} API` : 'API'}
    </span>
  );
}

function taskRecord(task: PmTask): PmTask & Record<string, unknown> {
  return task as PmTask & Record<string, unknown>;
}

function metadataOf(task: PmTask): Record<string, unknown> {
  const metadata = taskRecord(task).metadata;
  return metadata && typeof metadata === 'object' ? metadata as Record<string, unknown> : {};
}

function readTaskValue(task: PmTask, keys: string[]): unknown {
  const direct = taskRecord(task);
  const metadata = metadataOf(task);
  for (const key of keys) {
    const directValue = direct[key];
    if (directValue !== undefined && directValue !== null) return directValue;
    const metadataValue = metadata[key];
    if (metadataValue !== undefined && metadataValue !== null) return metadataValue;
  }
  return undefined;
}

function readTaskString(task: PmTask, keys: string[]): string {
  const value = readTaskValue(task, keys);
  return typeof value === 'string' ? value.trim() : '';
}

function toStringList(value: unknown): string[] {
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

function readTaskStringList(task: PmTask, keys: string[]): string[] {
  const values: string[] = [];
  for (const key of keys) {
    values.push(...toStringList(taskRecord(task)[key]));
    values.push(...toStringList(metadataOf(task)[key]));
  }
  return values.filter((item, index, all) => item.length > 0 && all.indexOf(item) === index);
}

function readAcceptanceCriteria(task: PmTask): string[] {
  const qaContract = readTaskValue(task, ['qa_contract']);
  const qaCriteria = qaContract && typeof qaContract === 'object'
    ? toStringList((qaContract as Record<string, unknown>).acceptance_criteria)
    : [];
  return [
    ...toStringList(task.acceptance),
    ...readTaskStringList(task, ['acceptance_criteria', 'acceptanceCriteria', 'acceptance']),
    ...qaCriteria,
  ].filter((item, index, all) => item.length > 0 && all.indexOf(item) === index);
}

function readTaskSearchId(result: PmTaskSearchResult): string {
  const id = result.id ?? result.task_id;
  return typeof id === 'string' ? id.trim() : '';
}

function readTaskSearchMetadata(result: PmTaskSearchResult): Record<string, unknown> {
  const metadata = result.metadata;
  return metadata && typeof metadata === 'object' && !Array.isArray(metadata)
    ? metadata as Record<string, unknown>
    : {};
}

function readTaskSearchString(result: PmTaskSearchResult, keys: string[]): string {
  const metadata = readTaskSearchMetadata(result);
  for (const key of keys) {
    const value = result[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
    const metadataValue = metadata[key];
    if (typeof metadataValue === 'string' && metadataValue.trim()) {
      return metadataValue.trim();
    }
  }
  return '';
}

function normalizeTaskSearchStatus(result: PmTaskSearchResult): TaskStatus {
  const status = readTaskSearchString(result, ['status', 'state']).toLowerCase();
  if (status === 'completed' || status === 'done' || status === 'success') return TaskStatus.COMPLETED;
  if (status === 'running' || status === 'in_progress') return TaskStatus.IN_PROGRESS;
  if (status === 'blocked') return TaskStatus.BLOCKED;
  if (status === 'failed' || status === 'failure') return TaskStatus.FAILED;
  return TaskStatus.PENDING;
}

function readTaskSearchPriority(result: PmTaskSearchResult): number {
  const value = result.priority;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number.parseInt(value, 10);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 99;
}

function normalizeTaskSearchResult(
  result: PmTaskSearchResult,
  sourceFallback = 'pm_task_search',
): PmTask | null {
  const id = readTaskSearchId(result);
  if (!id) return null;

  const status = normalizeTaskSearchStatus(result);
  const resultMetadata = readTaskSearchMetadata(result);
  const acceptance = [
    ...toStringList(result.acceptance),
    ...toStringList(result.acceptance_criteria),
  ].map((description) => ({ description }));
  const qaContract = result.qa_contract;

  return {
    id,
    title: readTaskSearchString(result, ['title', 'subject', 'name']) || id,
    goal: readTaskSearchString(result, ['goal']),
    summary: readTaskSearchString(result, ['summary', 'snippet', 'description']),
    description: readTaskSearchString(result, ['description']),
    status,
    done: result.done === true || result.completed === true || status === TaskStatus.COMPLETED,
    priority: readTaskSearchPriority(result),
    acceptance,
    execution_checklist: toStringList(result.execution_checklist ?? result.steps),
    target_files: toStringList(result.target_files ?? result.files ?? result.scope_paths),
    dependencies: toStringList(result.dependencies ?? result.blocked_by),
    qa_contract: qaContract && typeof qaContract === 'object' ? qaContract as Record<string, unknown> : undefined,
    blueprint_id: readTaskSearchString(result, ['blueprint_id', 'blueprintId']) || null,
    blueprint_path: readTaskSearchString(result, ['blueprint_path', 'blueprintPath']) || null,
    runtime_blueprint_path: readTaskSearchString(result, ['runtime_blueprint_path', 'runtimeBlueprintPath']) || null,
    metadata: {
      ...result,
      ...resultMetadata,
      source: readTaskSearchString(result, ['source']) || sourceFallback,
    },
  };
}

function nonEmptyTaskStrings(values: string[] | undefined): string[] {
  return Array.isArray(values) ? values.filter((item) => item.trim().length > 0) : [];
}

function mergePmTaskDetailProjection(base: PmTask, detail: PmTask): PmTask {
  const detailAcceptance = Array.isArray(detail.acceptance) ? detail.acceptance : [];
  const baseAcceptance = Array.isArray(base.acceptance) ? base.acceptance : [];
  const detailAcceptanceCriteria = nonEmptyTaskStrings(detail.acceptance_criteria);
  const baseAcceptanceCriteria = nonEmptyTaskStrings(base.acceptance_criteria);
  const detailSteps = nonEmptyTaskStrings(detail.execution_checklist ?? detail.steps);
  const baseSteps = nonEmptyTaskStrings(base.execution_checklist ?? base.steps);
  const detailTargetFiles = nonEmptyTaskStrings(detail.target_files ?? detail.files ?? detail.scope_paths);
  const baseTargetFiles = nonEmptyTaskStrings(base.target_files ?? base.files ?? base.scope_paths);
  const detailDependencies = nonEmptyTaskStrings(detail.dependencies ?? detail.blocked_by);
  const baseDependencies = nonEmptyTaskStrings(base.dependencies ?? base.blocked_by);
  const baseMetadata = metadataOf(base);
  const detailMetadata = metadataOf(detail);

  return {
    ...base,
    ...detail,
    title: detail.title || base.title,
    subject: detail.subject || base.subject,
    goal: detail.goal || base.goal,
    summary: detail.summary || base.summary,
    description: detail.description || base.description,
    acceptance: detailAcceptance.length > 0 ? detailAcceptance : baseAcceptance,
    acceptance_criteria: detailAcceptanceCriteria.length > 0 ? detailAcceptanceCriteria : baseAcceptanceCriteria,
    execution_checklist: detailSteps.length > 0 ? detailSteps : baseSteps,
    target_files: detailTargetFiles.length > 0 ? detailTargetFiles : baseTargetFiles,
    dependencies: detailDependencies.length > 0 ? detailDependencies : baseDependencies,
    qa_contract: detail.qa_contract || base.qa_contract,
    metadata: {
      ...baseMetadata,
      ...detailMetadata,
      source: readTaskString(detail, ['source']) || 'pm_task_detail',
    },
  };
}

function assignmentRecord(assignment: PmTaskAssignmentEntry): Record<string, unknown> {
  return assignment as Record<string, unknown>;
}

function readAssignmentString(assignment: PmTaskAssignmentEntry, keys: string[]): string {
  const record = assignmentRecord(assignment);
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return '';
}

function assignmentIdentity(assignment: PmTaskAssignmentEntry): string {
  return readAssignmentString(assignment, [
    'assignee',
    'assigned_to',
    'director_id',
    'worker_id',
    'owner',
    'role',
    'id',
  ]) || 'unassigned';
}

function assignmentState(assignment: PmTaskAssignmentEntry): string {
  return readAssignmentString(assignment, ['status', 'action', 'state', 'event']) || 'recorded';
}

function assignmentTime(assignment: PmTaskAssignmentEntry): string {
  return readAssignmentString(assignment, ['assigned_at', 'updated_at', 'created_at', 'timestamp']);
}

function formatTaskSearchMeta(result: PmTaskSearchResult): string {
  const parts = ['PM search API'];
  const id = readTaskSearchId(result);
  if (id) parts.push(id);
  if (typeof result.score === 'number') parts.push(`score ${result.score.toFixed(2)}`);
  return parts.join(' · ');
}

export function PMTaskPanel({
  tasks,
  selectedTaskId,
  onTaskSelect,
  onTaskCreated,
  pmRunning,
  taskTraceMap,
  workspace = '',
  createDisabledReason = '',
}: PMTaskPanelProps) {
  const [filter, setFilter] = useState<TaskFilter>('all');
  const [sort, setSort] = useState<TaskSort>('priority');
  const [searchQuery, setSearchQuery] = useState('');
  const [showCreatePanel, setShowCreatePanel] = useState(false);
  const [createSubject, setCreateSubject] = useState('');
  const [createDescription, setCreateDescription] = useState('');
  const [createPriority, setCreatePriority] = useState('medium');
  const [createAcceptanceText, setCreateAcceptanceText] = useState('');
  const [taskSearchResults, setTaskSearchResults] = useState<PmTaskSearchResult[]>([]);
  const [taskSearchError, setTaskSearchError] = useState<string | null>(null);
  const [isTaskSearchLoading, setIsTaskSearchLoading] = useState(false);
  const [backendSelectedTask, setBackendSelectedTask] = useState<PmTask | null>(null);
  const [taskDetailEvidence, setTaskDetailEvidence] = useState<PmTaskDetailEvidence>({
    taskId: '',
    loading: false,
    error: null,
    task: null,
  });
  const [assignmentEvidence, setAssignmentEvidence] = useState<PmTaskAssignmentEvidence>({
    taskId: '',
    loading: false,
    error: null,
    assignments: [],
    count: 0,
  });
  const [createEvidence, setCreateEvidence] = useState<PmTaskCreateEvidence>({
    loading: false,
    error: null,
    task: null,
  });
  const selectedTaskProjection = useMemo(
    () => tasks.find((task) => task.id === selectedTaskId) ??
      (backendSelectedTask?.id === selectedTaskId ? backendSelectedTask : null),
    [backendSelectedTask, tasks, selectedTaskId],
  );
  const selectedTask = useMemo(
    () => {
      const backendDetail = taskDetailEvidence.taskId === selectedTaskId ? taskDetailEvidence.task : null;
      if (selectedTaskProjection && backendDetail) {
        return mergePmTaskDetailProjection(selectedTaskProjection, backendDetail);
      }
      return selectedTaskProjection ?? backendDetail;
    },
    [selectedTaskId, selectedTaskProjection, taskDetailEvidence],
  );
  const normalizedCreateDisabledReason = createDisabledReason.trim();
  const createTaskDisabled = normalizedCreateDisabledReason.length > 0;

  useEffect(() => {
    if (createTaskDisabled && showCreatePanel) {
      setShowCreatePanel(false);
    }
  }, [createTaskDisabled, showCreatePanel]);

  useEffect(() => {
    const query = searchQuery.trim();
    if (query.length < 2) {
      setTaskSearchResults([]);
      setTaskSearchError(null);
      setIsTaskSearchLoading(false);
      return undefined;
    }

    let isCurrent = true;
    setIsTaskSearchLoading(true);
    setTaskSearchError(null);

    const timeoutId = window.setTimeout(async () => {
      const result = await searchPmTasks(query, 20, workspace);
      if (!isCurrent) return;

      if (result.ok && result.data) {
        setTaskSearchResults(result.data.results || []);
      } else {
        setTaskSearchResults([]);
        setTaskSearchError(result.error || 'PM 任务搜索不可用');
      }

      setIsTaskSearchLoading(false);
    }, 250);

    return () => {
      isCurrent = false;
      window.clearTimeout(timeoutId);
    };
  }, [searchQuery, workspace]);

  useEffect(() => {
    const taskId = selectedTaskId?.trim();
    if (!taskId) {
      setTaskDetailEvidence({
        taskId: '',
        loading: false,
        error: null,
        task: null,
      });
      return undefined;
    }

    let isCurrent = true;
    setTaskDetailEvidence({
      taskId,
      loading: true,
      error: null,
      task: null,
    });

    void getPmTask(taskId, workspace).then((result) => {
      if (!isCurrent) return;

      if (result.ok && result.data) {
        const task = normalizeTaskSearchResult(result.data, 'pm_task_detail');
        setTaskDetailEvidence({
          taskId,
          loading: false,
          error: task ? null : 'PM 任务详情缺少任务 ID',
          task,
        });
        return;
      }

      setTaskDetailEvidence({
        taskId,
        loading: false,
        error: result.error || 'PM 任务详情不可用',
        task: null,
      });
    }).catch((error: unknown) => {
      if (!isCurrent) return;
      setTaskDetailEvidence({
        taskId,
        loading: false,
        error: error instanceof Error ? error.message : 'PM 任务详情不可用',
        task: null,
      });
    });

    return () => {
      isCurrent = false;
    };
  }, [selectedTaskId, workspace]);

  useEffect(() => {
    const taskId = selectedTaskId?.trim();
    if (!taskId) {
      setAssignmentEvidence({
        taskId: '',
        loading: false,
        error: null,
        assignments: [],
        count: 0,
      });
      return undefined;
    }

    let isCurrent = true;
    setAssignmentEvidence({
      taskId,
      loading: true,
      error: null,
      assignments: [],
      count: 0,
    });

    void listPmTaskAssignments(taskId, 100, workspace).then((result) => {
      if (!isCurrent) return;

      if (result.ok && result.data) {
        const assignments = Array.isArray(result.data.assignments) ? result.data.assignments : [];
        setAssignmentEvidence({
          taskId,
          loading: false,
          error: null,
          assignments,
          count: typeof result.data.count === 'number' ? result.data.count : assignments.length,
        });
        return;
      }

      setAssignmentEvidence({
        taskId,
        loading: false,
        error: result.error || 'PM 任务分配历史不可用',
        assignments: [],
        count: 0,
      });
    }).catch((error: unknown) => {
      if (!isCurrent) return;
      setAssignmentEvidence({
        taskId,
        loading: false,
        error: error instanceof Error ? error.message : 'PM 任务分配历史不可用',
        assignments: [],
        count: 0,
      });
    });

    return () => {
      isCurrent = false;
    };
  }, [selectedTaskId, workspace]);

  const filteredTasks = useMemo(() => {
    let result = [...tasks];

    // Apply filter
    if (filter !== 'all') {
      result = result.filter((task) => {
        const status = task.status?.toLowerCase() || '';
        if (filter === 'pending') return status === 'pending' || !status;
        if (filter === 'running') return status === 'running' || status === 'in_progress';
        if (filter === 'completed') return status === 'completed' || task.done;
        if (filter === 'blocked') return status === 'blocked' || status === 'failed';
        return true;
      });
    }

    // Apply search
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (task) =>
          task.title?.toLowerCase().includes(query) ||
          task.id?.toLowerCase().includes(query) ||
          task.summary?.toLowerCase().includes(query)
      );
    }

    // Apply sort
    result.sort((a, b) => {
      if (sort === 'priority') {
        // priority is number, lower is higher priority
        const aPriority = typeof a.priority === 'number' ? a.priority : 99;
        const bPriority = typeof b.priority === 'number' ? b.priority : 99;
        return aPriority - bPriority;
      }
      if (sort === 'status') {
        const statusOrder = { running: 0, pending: 1, blocked: 2, completed: 3 };
        const aStatus = (a.status as keyof typeof statusOrder) || 'pending';
        const bStatus = (b.status as keyof typeof statusOrder) || 'pending';
        return statusOrder[aStatus] - statusOrder[bStatus];
      }
      if (sort === 'name') {
        return (a.title || '').localeCompare(b.title || '');
      }
      return 0;
    });

    return result;
  }, [tasks, filter, sort, searchQuery]);

  const taskStats = useMemo(() => {
    return {
      all: tasks.length,
      pending: tasks.filter((t) => !t.status || t.status === 'pending').length,
      running: tasks.filter((t) => String(t.status) === 'running' || t.status === 'in_progress').length,
      completed: tasks.filter((t) => t.status === 'completed' || t.done).length,
      blocked: tasks.filter((t) => t.status === 'blocked' || t.status === 'failed').length,
    };
  }, [tasks]);

  const handleTaskClick = (task: PmTask) => {
    setBackendSelectedTask(null);
    onTaskSelect(task.id);
  };

  const handleTaskSearchResultClick = (result: PmTaskSearchResult) => {
    const task = normalizeTaskSearchResult(result);
    if (!task) return;

    setBackendSelectedTask(task);
    onTaskSelect(task.id);
  };

  const handleCreateTask = async () => {
    if (createTaskDisabled) {
      setCreateEvidence({
        loading: false,
        error: normalizedCreateDisabledReason,
        task: null,
      });
      return;
    }

    const subject = createSubject.trim();
    if (!subject) {
      setCreateEvidence({
        loading: false,
        error: '任务标题不能为空',
        task: null,
      });
      return;
    }

    const acceptance = createAcceptanceText
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);

    setCreateEvidence({
      loading: true,
      error: null,
      task: null,
    });

    const result = await pmTaskService.create(
      {
        subject,
        description: createDescription.trim(),
        priority: createPriority,
        status: 'pending',
        acceptance,
      },
      workspace,
    );

    if (!result.ok || !result.data) {
      setCreateEvidence({
        loading: false,
        error: result.error || 'PM 任务创建失败',
        task: null,
      });
      return;
    }

    const createResponseRecord = result.data as unknown as Record<string, unknown>;
    const responseTitle = createResponseRecord.title;
    const createdTitle =
      typeof responseTitle === 'string' && responseTitle.trim().length > 0
        ? responseTitle.trim()
        : result.data.subject;

    const createdTask = normalizeTaskSearchResult({
      ...(result.data as unknown as PmTaskSearchResult),
      title: createdTitle,
      summary: result.data.description || subject,
      acceptance: result.data.acceptance || acceptance,
      metadata: {
        ...(result.data.metadata || {}),
        source: 'pm_task_create',
      },
    }, 'pm_task_create');

    if (!createdTask) {
      setCreateEvidence({
        loading: false,
        error: 'PM 任务创建成功，但响应缺少任务 ID',
        task: null,
      });
      return;
    }

    onTaskCreated?.(createdTask);
    setBackendSelectedTask(createdTask);
    onTaskSelect(createdTask.id);
    setCreateEvidence({
      loading: false,
      error: null,
      task: createdTask,
    });
    setCreateSubject('');
    setCreateDescription('');
    setCreateAcceptanceText('');
    setShowCreatePanel(false);
  };

  const searchTerm = searchQuery.trim();
  const showBackendSearch = searchTerm.length >= 2;
  const validTaskSearchResults = taskSearchResults.filter((result) => Boolean(readTaskSearchId(result)));

  return (
    <div data-testid="pm-task-panel" className="h-full flex"
    >
      {/* Task List */}
      <div className="flex-1 flex flex-col min-w-0 border-r border-white/10"
      >
        {/* Toolbar */}
        <div data-testid="pm-task-toolbar" className="h-14 flex items-center gap-3 px-4 border-b border-white/10 bg-white/[0.02]"
        >
          <div className="relative flex-1 max-w-sm"
          >
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <Input
              placeholder="搜索任务..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9 h-9 bg-white/5 border-white/10 text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50"
            />
          </div>

          <div className="flex items-center gap-1 p-1 rounded-lg bg-white/5 border border-white/10"
          >
            <FilterButton active={filter === 'all'} count={taskStats.all} onClick={() => setFilter('all')}>
              全部
            </FilterButton>
            <FilterButton active={filter === 'pending'} count={taskStats.pending} onClick={() => setFilter('pending')}>
              待办
            </FilterButton>
            <FilterButton active={filter === 'running'} count={taskStats.running} onClick={() => setFilter('running')}>
              进行中
            </FilterButton>
            <FilterButton active={filter === 'blocked'} count={taskStats.blocked} onClick={() => setFilter('blocked')}>
              阻塞
            </FilterButton>
            <FilterButton active={filter === 'completed'} count={taskStats.completed} onClick={() => setFilter('completed')}>
              完成
            </FilterButton>
          </div>

          <div className="flex items-center gap-2"
          >
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSort(sort === 'priority' ? 'status' : 'priority')}
              className="text-slate-400 hover:text-slate-200"
            >
              <ArrowUpDown className="w-3.5 h-3.5 mr-1.5" />
              {sort === 'priority' ? '优先级' : sort === 'status' ? '状态' : '名称'}
            </Button>

            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                if (createTaskDisabled) {
                  setCreateEvidence({
                    loading: false,
                    error: normalizedCreateDisabledReason,
                    task: null,
                  });
                  return;
                }
                setShowCreatePanel((current) => !current);
              }}
              disabled={createTaskDisabled}
              data-testid="pm-task-create-toggle"
              title={normalizedCreateDisabledReason || '创建 PM 任务'}
              className={cn(
                'text-slate-400 hover:bg-amber-500/10 hover:text-amber-200',
                showCreatePanel && 'bg-amber-500/10 text-amber-200',
              )}
            >
              <Plus className="mr-1.5 h-3.5 w-3.5" />
              创建任务
            </Button>

            <span className="rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200">
              来源: PM 合同
            </span>
          </div>
        </div>

        {showCreatePanel && (
          <div className="border-b border-amber-500/15 bg-slate-950/35 px-4 py-3" data-testid="pm-task-create-panel">
            <div className="mb-2 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-amber-200">
                <Plus className="h-3.5 w-3.5" />
                PM Task Create
              </div>
              <EndpointBadge endpoint="/v2/pm/tasks" method="POST" testId="pm-task-create-endpoint" />
            </div>
            <div className="grid gap-2 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_130px_auto]">
              <Input
                value={createSubject}
                onChange={(event) => setCreateSubject(event.target.value)}
                placeholder="任务标题"
                data-testid="pm-task-create-subject"
                className="h-9 border-white/10 bg-white/5 text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50"
              />
              <Input
                value={createDescription}
                onChange={(event) => setCreateDescription(event.target.value)}
                placeholder="目标 / 描述"
                data-testid="pm-task-create-description"
                className="h-9 border-white/10 bg-white/5 text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50"
              />
              <select
                value={createPriority}
                onChange={(event) => setCreatePriority(event.target.value)}
                data-testid="pm-task-create-priority"
                className="h-9 rounded-md border border-white/10 bg-slate-950 px-2 text-xs text-slate-200 focus:border-amber-500/50 focus:outline-none"
              >
                <option value="high">high</option>
                <option value="medium">medium</option>
                <option value="low">low</option>
              </select>
              <Button
                variant="outline"
                size="sm"
                onClick={() => { void handleCreateTask(); }}
                disabled={createEvidence.loading || createTaskDisabled}
                data-testid="pm-task-create-submit"
                title={normalizedCreateDisabledReason || '提交 PM 任务'}
                className="border-amber-500/30 text-amber-200 hover:bg-amber-500/10"
              >
                {createEvidence.loading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Plus className="mr-1.5 h-3.5 w-3.5" />}
                提交
              </Button>
            </div>
            <textarea
              value={createAcceptanceText}
              onChange={(event) => setCreateAcceptanceText(event.target.value)}
              placeholder="验收标准，每行一条"
              data-testid="pm-task-create-acceptance"
              className="mt-2 min-h-16 w-full resize-y rounded-md border border-white/10 bg-white/5 px-3 py-2 text-xs leading-5 text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50 focus:outline-none"
            />
          </div>
        )}

        {(createEvidence.loading || createEvidence.error || createEvidence.task) && (
          <div
            className={cn(
              'border-b px-4 py-2 text-xs',
              createEvidence.error
                ? 'border-red-500/20 bg-red-500/10 text-red-100'
                : 'border-amber-500/15 bg-slate-950/45 text-slate-300',
            )}
            data-testid="pm-task-create-evidence"
            data-endpoint="/v2/pm/tasks"
          >
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="font-semibold text-amber-100">PM task create</span>
              <EndpointBadge endpoint="/v2/pm/tasks" method="POST" testId="pm-task-create-evidence-endpoint" />
              {createEvidence.loading ? (
                <span className="text-slate-400">正在创建...</span>
              ) : createEvidence.error ? (
                <span className="text-red-200">{createEvidence.error}</span>
              ) : createEvidence.task ? (
                <span className="text-emerald-300">
                  created · {createEvidence.task.id} · {createEvidence.task.title}
                </span>
              ) : null}
            </div>
          </div>
        )}

        {showBackendSearch && (
          <div className="border-b border-white/10 bg-slate-950/20 px-4 py-2" data-testid="pm-task-search-panel">
            <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500">
              <span>后端任务搜索</span>
              <span data-testid="pm-task-search-count">
                {isTaskSearchLoading ? 'searching' : `${validTaskSearchResults.length} matches`}
              </span>
            </div>
            {isTaskSearchLoading ? (
              <div className="flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-400">
                <Clock className="h-3.5 w-3.5 animate-pulse text-amber-400" />
                正在调用 /v2/pm/search/tasks
              </div>
            ) : taskSearchError ? (
              <div
                className="rounded-md border border-red-500/20 bg-red-500/10 px-2 py-2 text-[11px] leading-relaxed text-red-200"
                data-testid="pm-task-search-error"
              >
                {taskSearchError}
              </div>
            ) : validTaskSearchResults.length > 0 ? (
              <div className="grid gap-1 md:grid-cols-2" data-testid="pm-task-search-results">
                {validTaskSearchResults.map((result, index) => (
                  <TaskSearchResultRow
                    key={`${readTaskSearchId(result)}-${index}`}
                    result={result}
                    onSelect={() => handleTaskSearchResultClick(result)}
                  />
                ))}
              </div>
            ) : (
              <div
                className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-500"
                data-testid="pm-task-search-empty"
              >
                后端未返回匹配任务
              </div>
            )}
          </div>
        )}

        {/* Task List Content */}
        <div className="flex-1 overflow-auto"
        >
          {filteredTasks.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-slate-500"
            >
              <Filter className="w-12 h-12 mb-4 opacity-20" />
              <p className="text-sm">暂无任务</p>
              <p className="text-xs text-slate-600 mt-1">任务将显示在这里</p>
            </div>
          ) : (
            <div className="divide-y divide-white/5"
            >
              {filteredTasks.map((task) => (
                <TaskListItem
                  key={task.id}
                  task={task}
                  selected={selectedTaskId === task.id}
                  onClick={() => handleTaskClick(task)}
                  pmRunning={pmRunning}
                  taskTraceMap={taskTraceMap}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Task Detail */}
      {selectedTask && (
        <TaskDetailPanel
          task={selectedTask}
          onClose={() => onTaskSelect(null)}
          taskTraceMap={taskTraceMap}
          detailEvidence={taskDetailEvidence.taskId === selectedTask.id ? taskDetailEvidence : null}
          assignmentEvidence={assignmentEvidence.taskId === selectedTask.id ? assignmentEvidence : null}
        />
      )}
    </div>
  );
}

function TaskSearchResultRow({
  result,
  onSelect,
}: {
  result: PmTaskSearchResult;
  onSelect: () => void;
}) {
  const task = normalizeTaskSearchResult(result);
  if (!task) return null;

  const summary = task.summary || task.description || task.goal || 'PM backend returned a task match';

  return (
    <button
      type="button"
      onClick={onSelect}
      className="cursor-pointer rounded-md border border-white/10 bg-white/[0.035] px-3 py-2 text-left transition-colors hover:border-amber-400/30 hover:bg-amber-500/10"
      data-testid="pm-task-search-result"
    >
      <div className="flex min-w-0 items-center gap-2">
        <p className="min-w-0 flex-1 truncate text-xs font-medium text-slate-200">{task.title}</p>
        <StatusBadge status={task.status} done={task.done} />
      </div>
      <p className="mt-1 truncate text-[11px] text-slate-400">{summary}</p>
      <p className="mt-1 truncate text-[10px] text-slate-500">{formatTaskSearchMeta(result)}</p>
    </button>
  );
}

// Task List Item Component
interface TaskListItemProps {
  task: PmTask;
  selected: boolean;
  onClick: () => void;
  pmRunning: boolean;
  taskTraceMap?: TaskTraceMap;
}

function TaskListItem({ task, selected, onClick, pmRunning, taskTraceMap }: TaskListItemProps) {
  const status = task.status?.toLowerCase() || 'pending';
  const isRunning = status === 'running' || status === 'in_progress';
  const isCompleted = status === 'completed' || task.done;
  const isBlocked = status === 'blocked' || status === 'failed';

  return (
    <div
      onClick={onClick}
      className={cn(
        'group flex items-center gap-3 px-4 py-3 cursor-pointer transition-all duration-200',
        // Running state: pulse animation + amber border highlight
        isRunning && pmRunning && 'animate-pulse border-l-4 border-amber-500 bg-amber-500/10',
        // Completed state: subtle styling
        isCompleted && 'opacity-70',
        // Blocked/Failed state: red border highlight
        isBlocked && 'border-l-4 border-red-500 bg-red-500/10',
        // Selected state (when not running)
        selected && !isRunning && 'bg-amber-500/10 border-l-2 border-amber-500',
        // Default hover state
        !selected && !isRunning && !isBlocked && 'hover:bg-white/5 border-l-2 border-transparent'
      )}
    >
      {/* Status Icon */}
      <div className="flex-shrink-0"
      >
        {isCompleted ? (
          <div className="w-5 h-5 rounded-full bg-emerald-500/20 flex items-center justify-center"
          >
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
          </div>
        ) : isRunning ? (
          <div className="w-5 h-5 rounded-full bg-amber-500/20 flex items-center justify-center animate-pulse"
          >
            <Play className="w-3 h-3 text-amber-400" />
          </div>
        ) : isBlocked ? (
          <div className="w-5 h-5 rounded-full bg-red-500/20 flex items-center justify-center"
          >
            <AlertCircle className="w-3.5 h-3.5 text-red-400" />
          </div>
        ) : (
          <div className="w-5 h-5 rounded-full border-2 border-slate-600 group-hover:border-slate-500"
          />
        )}
      </div>

      {/* Task Info */}
      <div className="flex-1 min-w-0"
      >
        <div className="flex items-center gap-2"
        >
          <p className={cn(
            'text-sm font-medium truncate',
            isCompleted ? 'text-slate-500 line-through' : 'text-slate-200'
          )}>
            {task.title || task.id}
          </p>
          {task.priority !== undefined && (
            <PriorityBadge priority={task.priority} />
          )}
        </div>
        {task.summary && (
          <p className="text-xs text-slate-500 truncate mt-0.5">{task.summary}</p>
        )}
        {/* 最近步骤 (仅显示 1 条) */}
        {taskTraceMap?.has(task.id) && (
          <TaskTraceInline
            traces={taskTraceMap.get(task.id) || []}
            maxLines={1}
            className="mt-2"
          />
        )}
      </div>

    </div>
  );
}

// Priority Badge Component
function PriorityBadge({ priority }: { priority: number | string }) {
  // priority is number, lower = higher priority
  const configs = {
    0: { color: 'text-red-400 bg-red-500/10 border-red-500/20', label: 'P0' },
    1: { color: 'text-red-400 bg-red-500/10 border-red-500/20', label: 'P1' },
    2: { color: 'text-amber-400 bg-amber-500/10 border-amber-500/20', label: 'P2' },
    3: { color: 'text-amber-400 bg-amber-500/10 border-amber-500/20', label: 'P3' },
    4: { color: 'text-slate-400 bg-slate-500/10 border-slate-500/20', label: 'P4' },
    5: { color: 'text-slate-400 bg-slate-500/10 border-slate-500/20', label: 'P5' },
  };
  const numPriority = typeof priority === 'number' ? priority : parseInt(String(priority), 10) || 99;
  const config = configs[numPriority as keyof typeof configs] || { color: 'text-slate-400 bg-slate-500/10 border-slate-500/20', label: `P${numPriority}` };

  return (
    <Badge variant="outline" className={cn('text-[10px] px-1.5 py-0 h-4', config.color)}>
      {config.label}
    </Badge>
  );
}

// Filter Button Component
interface FilterButtonProps {
  children: React.ReactNode;
  active: boolean;
  count: number;
  onClick: () => void;
}

function FilterButton({ children, active, count, onClick }: FilterButtonProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'px-2.5 py-1 rounded-md text-xs font-medium transition-all duration-200 flex items-center gap-1',
        active
          ? 'bg-amber-500/20 text-amber-400'
          : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
      )}
    >
      {children}
      <span className={cn('text-[10px]', active ? 'text-amber-400/70' : 'text-slate-600')}>
        {count}
      </span>
    </button>
  );
}

// Task Detail Panel Component
interface TaskDetailPanelProps {
  task: PmTask;
  onClose: () => void;
  taskTraceMap?: TaskTraceMap;
  detailEvidence?: PmTaskDetailEvidence | null;
  assignmentEvidence?: PmTaskAssignmentEvidence | null;
}

function TaskDetailPanel({
  task,
  onClose,
  taskTraceMap,
  detailEvidence = null,
  assignmentEvidence = null,
}: TaskDetailPanelProps) {
  const blueprintId = readTaskString(task, ['blueprint_id', 'blueprintId']);
  const blueprintPath = readTaskString(task, ['blueprint_path', 'blueprintPath', 'runtime_blueprint_path']);
  const owner = readTaskString(task, ['assignee', 'assigned_to', 'assignedTo', 'assigned_worker', 'worker_id']);
  const source = readTaskString(task, ['source', 'director_task_source']) || 'pm_contract';
  const executionSteps = readTaskStringList(task, ['execution_checklist', 'execution_steps', 'executionSteps', 'steps', 'checklist']);
  const acceptanceCriteria = readAcceptanceCriteria(task);
  const targetFiles = readTaskStringList(task, ['target_files', 'targetFiles', 'scope_paths', 'files']);
  const dependencies = readTaskStringList(task, ['dependencies', 'blocked_by', 'blockedBy']);
  const qaContract = readTaskValue(task, ['qa_contract']);
  const backendDetailSource = detailEvidence?.task
    ? readTaskString(detailEvidence.task, ['source']) || 'pm_task_detail'
    : '';

  return (
    <div className="w-96 flex flex-col border-l border-white/10 bg-slate-950/30"
    >
      {/* Header */}
      <div className="h-14 flex items-center justify-between px-4 border-b border-white/10"
      >
        <h3 className="text-sm font-semibold text-slate-200">任务详情</h3>
        <Button variant="ghost" size="sm" onClick={onClose} className="text-slate-400 hover:text-slate-200"
        >
          关闭
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4 space-y-4"
      >
        {/* Title */}
        <div>
          <label className="text-xs text-slate-500 uppercase tracking-wider">标题</label>
          <p className="text-sm text-slate-200 mt-1">{task.title || task.id}</p>
        </div>

        {/* Status */}
        <div>
          <label className="text-xs text-slate-500 uppercase tracking-wider">状态</label>
          <div className="flex items-center gap-2 mt-1">
            <StatusBadge status={task.status || 'pending'} done={task.done} />
          </div>
        </div>

        {/* Priority */}
        {task.priority !== undefined && (
          <div>
            <label className="text-xs text-slate-500 uppercase tracking-wider">优先级</label>
            <div className="mt-1">
              <PriorityBadge priority={task.priority} />
            </div>
          </div>
        )}

        {/* Goal */}
        {task.goal && (
          <div>
            <label className="text-xs text-slate-500 uppercase tracking-wider">目标</label>
            <p className="text-sm text-slate-300 mt-1 whitespace-pre-wrap">{task.goal}</p>
          </div>
        )}

        {/* Summary */}
        {task.summary && (
          <div>
            <label className="text-xs text-slate-500 uppercase tracking-wider">摘要</label>
            <p className="text-sm text-slate-300 mt-1 whitespace-pre-wrap">{task.summary}</p>
          </div>
        )}

        <div
          className="rounded-lg border border-white/10 bg-white/[0.035] p-3"
          data-testid="pm-task-detail-provenance"
        >
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-200">
            <Target className="h-3.5 w-3.5 text-amber-300" />
            合同来源
          </div>
          <div className="flex flex-wrap gap-1.5 text-[10px] text-slate-300">
            <DetailChip label="PM" value={task.id} />
            <DetailChip label="Source" value={source} />
            <DetailChip label="Owner" value={owner || '未分配'} />
            <DetailChip label="Blueprint" value={blueprintId || blueprintPath || '未绑定'} />
          </div>
          {blueprintPath ? (
            <div className="mt-2 truncate rounded-md border border-cyan-400/20 bg-cyan-500/5 px-2 py-1 text-[11px] text-cyan-100" title={blueprintPath}>
          {blueprintPath}
        </div>
      ) : null}
    </div>

        <div
          className="rounded-lg border border-cyan-400/20 bg-cyan-500/5 p-3"
          data-testid="pm-task-backend-detail"
        >
          <div className="mb-2 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-xs font-medium text-slate-200">
              <FileCode className="h-3.5 w-3.5 text-cyan-300" />
              <span>后端任务详情</span>
            </div>
            <EndpointBadge endpoint={`/v2/pm/tasks/${task.id}`} testId="pm-task-backend-detail-endpoint" />
          </div>
          {detailEvidence?.loading ? (
            <div className="flex items-center gap-2 rounded-md border border-cyan-500/20 bg-cyan-500/10 px-2 py-2 text-[11px] text-cyan-100">
              <Clock className="h-3.5 w-3.5 animate-pulse" />
              正在读取后端 PM 任务详情...
            </div>
          ) : detailEvidence?.error ? (
            <div className="flex items-center gap-2 rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-2 text-[11px] text-amber-100">
              <AlertCircle className="h-3.5 w-3.5" />
              <span>{detailEvidence.error}</span>
            </div>
          ) : detailEvidence?.task ? (
            <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-300">
              <DetailChip label="Hydrated" value={detailEvidence.task.id} />
              <DetailChip label="Source" value={backendDetailSource || 'pm_task_detail'} />
              <DetailChip label="Status" value={String(detailEvidence.task.status || 'pending')} />
              <DetailChip label="Priority" value={String(detailEvidence.task.priority ?? 'unknown')} />
            </div>
          ) : (
            <div className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-500">
              选择任务后读取后端详情合同
            </div>
          )}
        </div>

        <div
          className="rounded-lg border border-purple-400/20 bg-purple-500/5 p-3"
          data-testid="pm-task-assignments-panel"
        >
          <div className="mb-2 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-xs font-medium text-slate-200">
              <GitBranch className="h-3.5 w-3.5 text-purple-300" />
              <span>分配历史</span>
            </div>
            <EndpointBadge endpoint={`/v2/pm/tasks/${task.id}/assignments`} testId="pm-task-assignments-endpoint" />
          </div>
          {assignmentEvidence?.loading ? (
            <div className="flex items-center gap-2 rounded-md border border-purple-500/20 bg-purple-500/10 px-2 py-2 text-[11px] text-purple-100">
              <Clock className="h-3.5 w-3.5 animate-pulse" />
              正在读取 PM 任务分配历史...
            </div>
          ) : assignmentEvidence?.error ? (
            <div className="flex items-center gap-2 rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-2 text-[11px] text-amber-100">
              <AlertCircle className="h-3.5 w-3.5" />
              <span>{assignmentEvidence.error}</span>
            </div>
          ) : assignmentEvidence && assignmentEvidence.assignments.length > 0 ? (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500">
                <span>Assignment Evidence</span>
                <span data-testid="pm-task-assignment-count">{assignmentEvidence.count} records</span>
              </div>
              {assignmentEvidence.assignments.slice(0, 5).map((assignment, index) => {
                const identity = assignmentIdentity(assignment);
                const state = assignmentState(assignment);
                const time = assignmentTime(assignment);
                const key = readAssignmentString(assignment, ['id']) || `${identity}-${state}-${index}`;
                return (
                  <div
                    key={key}
                    className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-1.5 text-[11px]"
                    data-testid="pm-task-assignment-row"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate text-slate-200">{identity}</span>
                      <span className="shrink-0 rounded bg-purple-500/10 px-1.5 py-0.5 text-[10px] text-purple-100">
                        {state}
                      </span>
                    </div>
                    {time ? <div className="mt-1 truncate text-[10px] text-slate-500">{time}</div> : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-500">
              后端未返回任务分配历史
            </div>
          )}
        </div>

        <TaskContractSection icon={<ListChecks className="h-3.5 w-3.5 text-blue-300" />} title="执行步骤" items={executionSteps} emptyText="PM 合同未提供执行步骤" />
        <TaskContractSection icon={<ShieldCheck className="h-3.5 w-3.5 text-emerald-300" />} title="验收标准" items={acceptanceCriteria} emptyText="PM 合同未提供验收标准" />
        <TaskContractSection icon={<FileCode className="h-3.5 w-3.5 text-cyan-300" />} title="目标文件/作用域" items={targetFiles} emptyText="PM 合同未声明目标文件" />
        <TaskContractSection icon={<GitBranch className="h-3.5 w-3.5 text-amber-300" />} title="依赖/阻塞" items={dependencies} emptyText="无依赖或阻塞声明" />

        {qaContract && typeof qaContract === 'object' && Object.keys(qaContract).length > 0 ? (
          <div className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-200">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-300" />
              QA 合同
            </div>
            <pre className="max-h-40 overflow-auto rounded-md border border-white/10 bg-slate-950/70 p-2 text-[10px] text-slate-400">
              {JSON.stringify(qaContract, null, 2)}
            </pre>
          </div>
        ) : null}

        {/* 执行步骤追踪 */}
        {taskTraceMap?.has(task.id) && (
          <div className="pt-4 border-t border-white/10">
            <TaskTraceTimeline
              traces={taskTraceMap.get(task.id) || []}
              maxTraces={20}
              expanded={true}
            />
          </div>
        )}

        {/* Raw Data */}
        <div className="pt-4 border-t border-white/10"
        >
          <label className="text-xs text-slate-500 uppercase tracking-wider">原始数据</label>
          <pre className="mt-2 p-3 rounded-lg bg-slate-950 border border-white/10 text-[10px] text-slate-500 font-mono overflow-auto">
            {JSON.stringify(task, null, 2)}
          </pre>
        </div>
      </div>
    </div>
  );
}

function TaskContractSection({
  icon,
  title,
  items,
  emptyText,
}: {
  icon: React.ReactNode;
  title: string;
  items: string[];
  emptyText: string;
}) {
  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-200">
        {icon}
        {title}
      </div>
      {items.length === 0 ? (
        <div className="text-[11px] text-slate-500">{emptyText}</div>
      ) : (
        <ul className="space-y-1.5">
          {items.map((item, index) => (
            <li key={`${title}-${index}`} className="flex gap-2 text-[11px] leading-5 text-slate-300">
              <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-amber-400" />
              <span className="break-words">{item}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DetailChip({ label, value }: { label: string; value: string }) {
  return (
    <span className="max-w-full truncate rounded-md border border-white/10 bg-slate-950/55 px-2 py-1" title={`${label}: ${value}`}>
      <span className="text-slate-500">{label}</span>
      <span className="mx-1 text-slate-600">·</span>
      <span>{value}</span>
    </span>
  );
}

// Status Badge Component
function StatusBadge({ status, done }: { status: string; done?: boolean }) {
  const configs = {
    pending: { icon: Circle, color: 'text-slate-400 bg-slate-500/10 border-slate-500/20', label: '待办' },
    running: { icon: Play, color: 'text-amber-400 bg-amber-500/10 border-amber-500/20', label: '进行中' },
    in_progress: { icon: Play, color: 'text-amber-400 bg-amber-500/10 border-amber-500/20', label: '进行中' },
    completed: { icon: CheckCircle2, color: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20', label: '已完成' },
    blocked: { icon: AlertCircle, color: 'text-red-400 bg-red-500/10 border-red-500/20', label: '阻塞' },
    failed: { icon: AlertCircle, color: 'text-red-400 bg-red-500/10 border-red-500/20', label: '失败' },
  };

  const config = configs[status as keyof typeof configs] || configs.pending;
  const Icon = config.icon;

  if (done) {
    return (
      <Badge variant="outline" className="text-emerald-400 bg-emerald-500/10 border-emerald-500/20"
      >
        <CheckCircle2 className="w-3 h-3 mr-1" />
        已完成
      </Badge>
    );
  }

  return (
    <Badge variant="outline" className={config.color}
    >
      <Icon className="w-3 h-3 mr-1" />
      {config.label}
    </Badge>
  );
}
