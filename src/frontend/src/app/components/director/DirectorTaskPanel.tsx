/**
 * DirectorTaskPanel - 任务面板展示组件
 */
import { useState, useMemo } from 'react';
import {
  Loader2,
  Clock,
  CheckCircle2,
  AlertTriangle,
  Pause,
  ChevronDown,
  ChevronRight,
  Code2,
  Bug,
  FileCode,
  Coins,
  RotateCcw,
  ListTodo,
  Zap,
  BarChart3,
  Layers,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import type { ExecutionTask } from './hooks/useDirectorWorkspace';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntime';
import type { TaskTraceMap } from '@/app/types/taskTrace';
import { TaskTraceTimeline } from '../common/TaskTraceTimeline';

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
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({
    running: true,
    pending: true,
    completed: true,
    failed: true,
    blocked: true,
  });

  const toggleGroup = (group: string) => {
    setExpandedGroups((prev) => ({ ...prev, [group]: !prev[group] }));
  };

  // 按状态分组任务
  const groupedTasks = useMemo(() => ({
    running: tasks.filter((t) => t.status === 'running'),
    pending: tasks.filter((t) => t.status === 'pending'),
    blocked: tasks.filter((t) => t.status === 'blocked'),
    failed: tasks.filter((t) => t.status === 'failed'),
    completed: tasks.filter((t) => t.status === 'completed'),
  }), [tasks]);

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

  const workerRows = useMemo(() =>
    workers
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
      }),
    [workers, taskMap]
  );

  const workerBusyCount = workerRows.filter((w) => w.status === 'busy').length;
  const workerIdleCount = workerRows.filter((w) => w.status === 'idle').length;
  const workerFailedCount = workerRows.filter((w) => w.status === 'failed').length;

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

  const TaskCard = ({ task }: { task: ExecutionTask }) => {
    const traces = taskTraceMap?.get(task.id) || [];
    const failedTrace = traces.find((t: { status: string }) => t.status === 'failed');
    const isSelected = selectedTaskId === task.id;
    const budgetPercent = task.budget && task.budget.total > 0
      ? Math.round((task.budget.used / task.budget.total) * 100)
      : 0;

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
          <div className="flex items-center gap-1.5 text-slate-400">
            {getTypeIcon(task.type)}
            <span>{getTypeLabel(task.type)}</span>
          </div>
          <div className="flex items-center gap-1.5 text-slate-400">
            <Clock className="w-3 h-3" />
            <span>{formatDuration(task.actualTime)}</span>
          </div>
          <div className="flex items-center gap-1.5 text-slate-400">
            <FileCode className="w-3 h-3" />
            <span>{task.filesModified || 0} 文件</span>
          </div>
        </div>

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

        {/* 实时文件写入信息 - 仅运行中任务显示 */}
        {task.status === 'running' && (task.currentFile || task.totalAddedLines || task.totalDeletedLines || task.totalModifiedLines) && (
          <div className="mt-2 p-2 rounded-lg bg-slate-900/50 border border-slate-700/50">
            {/* 当前写入的文件 */}
            {task.currentFile && (
              <div className="flex items-center gap-1.5 text-[10px] mb-1.5">
                <FileCode className="w-3 h-3 text-cyan-400" />
                <span className="text-slate-400 truncate flex-1" title={task.currentFile}>
                  {task.currentFile}
                </span>
              </div>
            )}

            {/* 代码行数统计 */}
            {(task.totalAddedLines || task.totalDeletedLines || task.totalModifiedLines) ? (
              <div className="flex items-center gap-2 text-[10px]">
                {task.totalAddedLines ? (
                  <span className="flex items-center gap-0.5 text-emerald-400">
                    <span className="font-mono">+{task.totalAddedLines}</span>
                    <span className="text-slate-500">行</span>
                  </span>
                ) : null}
                {task.totalDeletedLines ? (
                  <span className="flex items-center gap-0.5 text-red-400">
                    <span className="font-mono">-{task.totalDeletedLines}</span>
                    <span className="text-slate-500">行</span>
                  </span>
                ) : null}
                {task.totalModifiedLines ? (
                  <span className="flex items-center gap-0.5 text-amber-400">
                    <span className="font-mono">~{task.totalModifiedLines}</span>
                    <span className="text-slate-500">行</span>
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        )}

        {/* 重试次数 - 带badge样式 */}
        {task.retries !== undefined && task.retries > 0 && (
          <div className="mt-2 flex items-center gap-2">
            <span className={cn(
              'inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full font-medium',
              task.retries >= (task.maxRetries || 3)
                ? 'bg-red-500/15 text-red-400 border border-red-500/20'
                : 'bg-orange-500/15 text-orange-400 border border-orange-500/20'
            )}>
              <RotateCcw className={cn('w-3 h-3', task.retries >= (task.maxRetries || 3) ? 'text-red-400' : 'text-orange-400')} />
              重试 {task.retries}
              {task.maxRetries && task.maxRetries > 0 && (
                <span className="text-slate-500">/ {task.maxRetries}</span>
              )}
            </span>
            {task.retries >= (task.maxRetries || 3) && (
              <span className="text-[9px] text-red-400">即将失败</span>
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
                    {worker.taskName ? `当前任务: ${worker.taskName}` : '当前任务: 空闲'}
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
