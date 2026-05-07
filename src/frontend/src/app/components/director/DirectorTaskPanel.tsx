/**
 * DirectorTaskPanel - task board and drill-down details for Director execution.
 */
import { useMemo, useState } from 'react';
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock,
  Code2,
  FileCode,
  Filter,
  GitBranch,
  Layers,
  ListChecks,
  ListTodo,
  Loader2,
  Pause,
  RotateCcw,
  ShieldCheck,
  Target,
  User,
  Zap,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntime';
import type { TaskTraceMap } from '@/app/types/taskTrace';
import { TaskTraceTimeline } from '../common/TaskTraceTimeline';
import type { ExecutionTask } from './hooks/useDirectorWorkspace';

export type TaskBoardFilter = 'all' | 'unclaimed' | 'claimed' | 'attention' | 'completed';

export interface TaskBoardGroup {
  id: Exclude<TaskBoardFilter, 'all'>;
  label: string;
  description: string;
  tasks: ExecutionTask[];
}

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

const FILTERS: Array<{ id: TaskBoardFilter; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'unclaimed', label: '未领取' },
  { id: 'claimed', label: '已领取/运行中' },
  { id: 'attention', label: '阻塞/报错' },
  { id: 'completed', label: '完成' },
];

export function buildTaskBoardGroups(tasks: ExecutionTask[], filter: TaskBoardFilter = 'all'): TaskBoardGroup[] {
  const groups: TaskBoardGroup[] = [
    {
      id: 'unclaimed',
      label: '未领取',
      description: '等待 Director 或 worker 领取',
      tasks: tasks.filter((task) => task.status === 'pending'),
    },
    {
      id: 'claimed',
      label: '已领取 / 运行中',
      description: '已分配 worker 或正在执行',
      tasks: tasks.filter((task) => task.status === 'running'),
    },
    {
      id: 'attention',
      label: '阻塞 / 报错',
      description: '需要排障、重试或回流 PM',
      tasks: tasks.filter((task) => task.status === 'blocked' || task.status === 'failed'),
    },
    {
      id: 'completed',
      label: '完成',
      description: '已完成并可进入 QA 观察',
      tasks: tasks.filter((task) => task.status === 'completed'),
    },
  ];

  if (filter === 'all') {
    return groups;
  }
  return groups.filter((group) => group.id === filter);
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
    <div className={cn('flex min-h-16 flex-col items-center justify-center rounded-lg border p-2', colorClasses[color])}>
      {icon}
      <span className="mt-1 text-lg font-bold">{value}</span>
      <span className="text-[9px] opacity-70">{label}</span>
    </div>
  );
}

function getStatusIcon(status: ExecutionTask['status']) {
  switch (status) {
    case 'completed': return <CheckCircle2 className="h-4 w-4 text-emerald-400" />;
    case 'running': return <Loader2 className="h-4 w-4 animate-spin text-blue-400" />;
    case 'failed': return <AlertTriangle className="h-4 w-4 text-red-400" />;
    case 'blocked': return <Pause className="h-4 w-4 text-yellow-400" />;
    default: return <Clock className="h-4 w-4 text-slate-500" />;
  }
}

function getStatusLabel(status: ExecutionTask['status']) {
  switch (status) {
    case 'running': return '运行中';
    case 'pending': return '未领取';
    case 'completed': return '完成';
    case 'failed': return '报错';
    case 'blocked': return '阻塞';
  }
}

function getStatusColor(status: ExecutionTask['status']) {
  switch (status) {
    case 'running': return 'text-blue-300 border-blue-500/25 bg-blue-500/10';
    case 'pending': return 'text-slate-300 border-slate-500/25 bg-white/5';
    case 'completed': return 'text-emerald-300 border-emerald-500/25 bg-emerald-500/10';
    case 'failed': return 'text-red-300 border-red-500/25 bg-red-500/10';
    case 'blocked': return 'text-yellow-300 border-yellow-500/25 bg-yellow-500/10';
  }
}

function getTypeIcon(type: ExecutionTask['type']) {
  switch (type) {
    case 'test': return <ShieldCheck className="h-3.5 w-3.5 text-purple-400" />;
    case 'debug': return <AlertTriangle className="h-3.5 w-3.5 text-red-400" />;
    case 'review': return <ListChecks className="h-3.5 w-3.5 text-amber-400" />;
    default: return <Code2 className="h-3.5 w-3.5 text-blue-400" />;
  }
}

function formatDuration(ms?: number) {
  if (!ms || ms <= 0) return '-';
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  if (hours > 0) return `${hours}h ${minutes % 60}m`;
  if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
  return `${seconds}s`;
}

function formatListValue(items?: string[]) {
  return Array.isArray(items) ? items.filter((item) => String(item || '').trim().length > 0) : [];
}

function DetailSection({ icon, title, items, emptyText }: {
  icon: React.ReactNode;
  title: string;
  items?: string[];
  emptyText: string;
}) {
  const rows = formatListValue(items);
  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.03] p-3" data-testid={`director-task-detail-${title}`}>
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-200">
        {icon}
        <span>{title}</span>
      </div>
      {rows.length === 0 ? (
        <p className="text-[11px] text-slate-500">{emptyText}</p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((item, index) => (
            <li key={`${title}-${index}`} className="flex gap-2 text-[11px] leading-5 text-slate-300">
              <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
              <span className="break-words">{item}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function DirectorTaskPanel({
  tasks,
  workers,
  taskMap,
  selectedTaskId,
  onTaskSelect,
  onExecute,
  isExecuting,
  taskTraceMap,
}: DirectorTaskPanelProps) {
  const [activeFilter, setActiveFilter] = useState<TaskBoardFilter>('all');
  const groups = useMemo(() => buildTaskBoardGroups(tasks, activeFilter), [tasks, activeFilter]);
  const selectedTask = selectedTaskId ? taskMap.get(selectedTaskId) || null : null;
  const selectedExecuteLabel = selectedTask?.status === 'failed' || selectedTask?.status === 'blocked'
    ? '重试选中任务'
    : selectedTask?.status === 'running' || isExecuting
      ? '停止 Director'
      : '执行选中任务';

  const runningCount = tasks.filter((task) => task.status === 'running').length;
  const pendingCount = tasks.filter((task) => task.status === 'pending').length;
  const completedCount = tasks.filter((task) => task.status === 'completed').length;
  const blockedCount = tasks.filter((task) => task.status === 'blocked').length;
  const failedCount = tasks.filter((task) => task.status === 'failed').length;
  const progress = tasks.length > 0 ? Math.round((completedCount / tasks.length) * 100) : 0;

  const workerRows = useMemo(() =>
    workers
      .filter((worker) => worker && typeof worker === 'object')
      .map((worker) => {
        const taskId = String(worker.currentTaskId || '').trim();
        return {
          id: worker.id,
          name: worker.name || worker.id,
          status: worker.status,
          taskId,
          taskName: taskId ? taskMap.get(taskId)?.name || taskId : '',
          healthy: worker.healthy,
          tasksCompleted: worker.tasksCompleted || 0,
          tasksFailed: worker.tasksFailed || 0,
        };
      }),
    [workers, taskMap],
  );

  const selectedWorker = selectedTask
    ? workerRows.find((worker) => worker.taskId === selectedTask.id || worker.id === selectedTask.assignedWorker)
    : null;

  const TaskCard = ({ task }: { task: ExecutionTask }) => {
    const isSelected = selectedTaskId === task.id;
    const traces = taskTraceMap?.get(task.id) || [];
    const currentFile = task.currentFilePath || task.currentFile;
    const hasFileActivity = Boolean(
      currentFile
      || task.activityUpdatedAt
      || task.lineStats?.added
      || task.lineStats?.deleted
      || task.lineStats?.modified
      || task.operationStats?.create
      || task.operationStats?.modify
      || task.operationStats?.delete,
    );

    return (
      <button
        type="button"
        data-testid="director-task-item"
        onClick={() => onTaskSelect(task.id)}
        className={cn(
          'w-full rounded-lg border p-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400/60',
          isSelected ? 'border-indigo-400/50 bg-indigo-500/12' : 'border-white/10 bg-white/[0.04] hover:border-white/20 hover:bg-white/[0.07]',
        )}
      >
        <div className="flex items-start gap-3">
          <div className="mt-0.5">{getStatusIcon(task.status)}</div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="truncate text-sm font-medium text-slate-100">{task.name}</span>
              <span className={cn('rounded-md border px-1.5 py-0.5 text-[10px]', getStatusColor(task.status))}>
                {getStatusLabel(task.status)}
              </span>
              {task.assignedWorker && (
                <span className="rounded-md border border-cyan-400/20 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-200">
                  {task.assignedWorker}
                </span>
              )}
            </div>
            <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-slate-400">
              {task.description || task.goal || '暂无任务描述'}
            </p>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-3 gap-2 text-[10px] text-slate-400">
          <span className="flex items-center gap-1.5">{getTypeIcon(task.type)}{task.type}</span>
          <span className="flex items-center gap-1.5"><Clock className="h-3 w-3" />{formatDuration(task.actualTime)}</span>
          <span className="flex items-center gap-1.5"><FileCode className="h-3 w-3" />{task.filesModified || 0} 文件</span>
        </div>

        {task.status === 'running' && (
          <div className="mt-3">
            <div className="mb-1 flex justify-between text-[10px] text-slate-400">
              <span>{task.currentPhase || '执行中'}</span>
              <span>{task.progress || 0}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
              <div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${task.progress || 0}%` }} />
            </div>
          </div>
        )}

        {hasFileActivity && (
          <div className="mt-3 rounded-lg border border-cyan-400/15 bg-cyan-500/5 p-2 text-[10px] text-cyan-100">
            {currentFile && <div className="truncate" title={currentFile}>文件: {currentFile}</div>}
            {task.lineStats && (
              <div className="mt-1 flex gap-2 font-mono">
                <span className="text-emerald-300">+{task.lineStats.added}</span>
                <span className="text-red-300">-{task.lineStats.deleted}</span>
                <span className="text-amber-300">~{task.lineStats.modified}</span>
              </div>
            )}
          </div>
        )}

        {traces.length > 0 && (
          <div className="mt-3 border-t border-white/5 pt-2">
            <TaskTraceTimeline traces={traces} maxTraces={task.status === 'running' ? 4 : 1} expanded={task.status === 'running'} />
          </div>
        )}

        {task.error && (
          <div className="mt-3 rounded-md border border-red-500/20 bg-red-500/10 p-2 text-[10px] text-red-300">
            {task.error}
          </div>
        )}
      </button>
    );
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-white/5">
        <div className="flex h-12 items-center justify-between px-4">
          <div>
            <h2 className="text-sm font-medium text-slate-200">任务队列</h2>
            <p className="text-[10px] text-slate-500">按领取状态分区，点击任务查看完整执行合同</p>
          </div>
          <Button
            size="sm"
            onClick={onExecute}
            data-testid="director-workspace-bulk-execute"
            className={cn(isExecuting ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700', 'text-white')}
          >
            {isExecuting ? <><Pause className="mr-1.5 h-3.5 w-3.5" />停止执行</> : <><Zap className="mr-1.5 h-3.5 w-3.5" />全部执行</>}
          </Button>
        </div>

        <div className="grid grid-cols-5 gap-2 px-4 pb-3">
          <StatCard icon={<Loader2 className="h-3.5 w-3.5 text-blue-400" />} label="运行" value={runningCount} color="blue" />
          <StatCard icon={<Clock className="h-3.5 w-3.5 text-slate-400" />} label="未领取" value={pendingCount} color="slate" />
          <StatCard icon={<Pause className="h-3.5 w-3.5 text-yellow-400" />} label="阻塞" value={blockedCount} color="yellow" />
          <StatCard icon={<AlertTriangle className="h-3.5 w-3.5 text-red-400" />} label="报错" value={failedCount} color="red" />
          <StatCard icon={<CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />} label="完成" value={completedCount} color="emerald" />
        </div>

        <div className="px-4 pb-3">
          <div className="mb-1 flex items-center justify-between text-[10px] text-slate-400">
            <span className="flex items-center gap-1.5"><BarChart3 className="h-3 w-3" />总体进度 {completedCount}/{tasks.length}</span>
            <span className="font-medium text-indigo-300">{progress}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
            <div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${progress}%` }} />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-4 pb-3">
          <span className="flex items-center gap-1 text-[10px] text-slate-500"><Filter className="h-3 w-3" />筛选</span>
          {FILTERS.map((filter) => (
            <button
              key={filter.id}
              type="button"
              data-testid={`director-task-filter-${filter.id}`}
              onClick={() => setActiveFilter(filter.id)}
              className={cn(
                'rounded-md border px-2 py-1 text-[10px] transition-colors',
                activeFilter === filter.id
                  ? 'border-indigo-400/50 bg-indigo-500/15 text-indigo-200'
                  : 'border-white/10 bg-white/[0.03] text-slate-400 hover:text-slate-200',
              )}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(320px,0.95fr)_minmax(360px,1.05fr)] overflow-hidden">
        <div className="overflow-auto border-r border-white/5 p-4" data-testid="director-task-board">
          {tasks.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-slate-500">
              <ListTodo className="mb-4 h-12 w-12 text-indigo-500/30" />
              <p>当前没有可执行任务</p>
            </div>
          ) : (
            <div className="space-y-4">
              {groups.map((group) => (
                <section key={group.id} data-testid={`director-task-group-${group.id}`}>
                  <div className="mb-2 flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
                    <div>
                      <div className="text-xs font-medium text-slate-200">{group.label}</div>
                      <div className="text-[10px] text-slate-500">{group.description}</div>
                    </div>
                    <span className="rounded-md bg-white/10 px-2 py-0.5 text-xs font-mono text-slate-300">{group.tasks.length}</span>
                  </div>
                  {group.tasks.length === 0 ? (
                    <div className="rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-[11px] text-slate-500">
                      暂无任务
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {group.tasks.map((task) => <TaskCard key={task.id} task={task} />)}
                    </div>
                  )}
                </section>
              ))}
            </div>
          )}
        </div>

        <aside className="min-w-0 overflow-auto p-4" data-testid="director-task-detail">
          {!selectedTask ? (
            <div className="flex h-full flex-col items-center justify-center rounded-lg border border-dashed border-white/10 text-slate-500">
              <Target className="mb-3 h-10 w-10 text-indigo-500/30" />
              <p className="text-sm">选择左侧任务查看详情</p>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="rounded-lg border border-white/10 bg-white/[0.04] p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      {getStatusIcon(selectedTask.status)}
                      <h3 className="truncate text-base font-semibold text-slate-100">{selectedTask.name}</h3>
                      <span className={cn('rounded-md border px-1.5 py-0.5 text-[10px]', getStatusColor(selectedTask.status))}>
                        {getStatusLabel(selectedTask.status)}
                      </span>
                    </div>
                    <p className="text-xs leading-5 text-slate-400">{selectedTask.description || selectedTask.goal || '暂无描述'}</p>
                  </div>
                  <Button
                    size="sm"
                    onClick={onExecute}
                    data-testid="director-task-execute-selected"
                    className={cn(
                      isExecuting ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700',
                      'shrink-0 text-white',
                    )}
                  >
                    {isExecuting ? <Pause className="mr-1.5 h-3.5 w-3.5" /> : <Zap className="mr-1.5 h-3.5 w-3.5" />}
                    {selectedExecuteLabel}
                  </Button>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-slate-400">
                  <span className="flex items-center gap-1.5"><User className="h-3.5 w-3.5" />Worker: {selectedWorker?.name || selectedTask.assignedWorker || '未分配'}</span>
                  <span className="flex items-center gap-1.5"><Clock className="h-3.5 w-3.5" />耗时: {formatDuration(selectedTask.actualTime)}</span>
                  <span className="flex items-center gap-1.5"><FileCode className="h-3.5 w-3.5" />文件活动: {selectedTask.filesModified || 0}</span>
                  <span className="flex items-center gap-1.5"><RotateCcw className="h-3.5 w-3.5" />重试: {selectedTask.retries || 0}{selectedTask.maxRetries ? `/${selectedTask.maxRetries}` : ''}</span>
                </div>
                <div
                  className="mt-3 flex flex-wrap gap-1.5 text-[10px]"
                  data-testid="director-task-provenance"
                  aria-label="Director task provenance"
                >
                  <ProvenanceChip label="PM" value={selectedTask.pmTaskId || selectedTask.id} />
                  <ProvenanceChip label="BP" value={selectedTask.blueprintId || selectedTask.blueprintPath || '未绑定'} />
                  <ProvenanceChip label="Owner" value={selectedTask.claimedBy || selectedTask.assignedWorker || selectedWorker?.name || '未分配'} />
                  <ProvenanceChip label="Source" value={selectedTask.source || 'runtime'} />
                </div>
              </div>

              <DetailSection icon={<Target className="h-3.5 w-3.5 text-indigo-300" />} title="PM目标" items={selectedTask.goal ? [selectedTask.goal] : []} emptyText="暂无 PM 目标" />
              <DetailSection icon={<ListChecks className="h-3.5 w-3.5 text-blue-300" />} title="执行步骤" items={selectedTask.executionSteps} emptyText="暂无执行步骤" />
              <DetailSection icon={<ShieldCheck className="h-3.5 w-3.5 text-emerald-300" />} title="验收标准" items={selectedTask.acceptanceCriteria} emptyText="暂无验收标准" />
              <DetailSection icon={<FileCode className="h-3.5 w-3.5 text-cyan-300" />} title="目标文件" items={selectedTask.targetFiles} emptyText="暂无目标文件" />
              <DetailSection icon={<GitBranch className="h-3.5 w-3.5 text-amber-300" />} title="依赖" items={[...(selectedTask.dependencies || []), ...(selectedTask.blockedBy || [])]} emptyText="暂无依赖" />

              <section className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
                <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-200">
                  <Layers className="h-3.5 w-3.5 text-cyan-300" />
                  <span>实时文件活动</span>
                </div>
                <div className="space-y-1.5 text-[11px] text-slate-300">
                  <div>当前/最近文件: {selectedTask.currentFilePath || selectedTask.currentFile || '暂无'}</div>
                  {selectedTask.lineStats ? (
                    <div className="flex gap-3 font-mono">
                      <span className="text-emerald-300">+{selectedTask.lineStats.added}</span>
                      <span className="text-red-300">-{selectedTask.lineStats.deleted}</span>
                      <span className="text-amber-300">~{selectedTask.lineStats.modified}</span>
                    </div>
                  ) : null}
                  {selectedTask.operationStats ? (
                    <div>操作: 创建 {selectedTask.operationStats.create} / 修改 {selectedTask.operationStats.modify} / 删除 {selectedTask.operationStats.delete}</div>
                  ) : null}
                  <div>更新时间: {selectedTask.activityUpdatedAt || '暂无'}</div>
                </div>
              </section>

              {(selectedTask.error || selectedTask.output) && (
                <section className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
                  <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-200">
                    <AlertTriangle className="h-3.5 w-3.5 text-red-300" />
                    <span>错误 / 输出</span>
                  </div>
                  {selectedTask.error && <pre className="whitespace-pre-wrap rounded-md bg-red-500/10 p-2 text-[11px] text-red-200">{selectedTask.error}</pre>}
                  {selectedTask.output && <pre className="mt-2 whitespace-pre-wrap rounded-md bg-slate-900/60 p-2 text-[11px] text-slate-300">{selectedTask.output}</pre>}
                </section>
              )}

              {taskTraceMap?.get(selectedTask.id)?.length ? (
                <section className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
                  <TaskTraceTimeline traces={taskTraceMap.get(selectedTask.id) || []} maxTraces={12} expanded />
                </section>
              ) : null}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

function ProvenanceChip({ label, value }: { label: string; value: string }) {
  return (
    <span className="max-w-full truncate rounded-md border border-white/10 bg-slate-950/55 px-2 py-1 text-slate-300" title={`${label}: ${value}`}>
      <span className="text-slate-500">{label}</span>
      <span className="mx-1 text-slate-600">·</span>
      <span>{value}</span>
    </span>
  );
}
