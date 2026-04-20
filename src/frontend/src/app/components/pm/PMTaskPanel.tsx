import { useState, useMemo } from 'react';
import {
  Search,
  Filter,
  MoreHorizontal,
  Play,
  Pause,
  CheckCircle2,
  Circle,
  Clock,
  AlertCircle,
  ArrowUpDown,
  Plus,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { Badge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import type { PmTask } from '@/types/task';
import type { TaskTraceMap } from '@/app/types/taskTrace';
import { TaskTraceInline } from '../common/TaskTraceInline';
import { TaskTraceTimeline } from '../common/TaskTraceTimeline';

interface PMTaskPanelProps {
  tasks: PmTask[];
  selectedTaskId: string | null;
  onTaskSelect: (taskId: string | null) => void;
  pmRunning: boolean;
  taskTraceMap?: TaskTraceMap;
}

type TaskFilter = 'all' | 'pending' | 'running' | 'completed' | 'blocked';
type TaskSort = 'priority' | 'status' | 'created' | 'name';

export function PMTaskPanel({
  tasks,
  selectedTaskId,
  onTaskSelect,
  pmRunning,
  taskTraceMap,
}: PMTaskPanelProps) {
  const [filter, setFilter] = useState<TaskFilter>('all');
  const [sort, setSort] = useState<TaskSort>('priority');
  const [searchQuery, setSearchQuery] = useState('');
  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskId) ?? null,
    [tasks, selectedTaskId],
  );

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
    onTaskSelect(task.id);
  };

  return (
    <div className="h-full flex"
    >
      {/* Task List */}
      <div className="flex-1 flex flex-col min-w-0 border-r border-white/10"
      >
        {/* Toolbar */}
        <div className="h-14 flex items-center gap-3 px-4 border-b border-white/10 bg-white/[0.02]"
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
              variant="outline"
              size="sm"
              className="border-amber-500/30 text-amber-400 hover:bg-amber-500/10"
            >
              <Plus className="w-3.5 h-3.5 mr-1.5" />
              新建
            </Button>
          </div>
        </div>

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
        />
      )}
    </div>
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

      {/* Actions */}
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity"
      >
        <Button variant="ghost" size="icon" className="h-7 w-7 text-slate-400 hover:text-slate-200"
        >
          <MoreHorizontal className="w-4 h-4" />
        </Button>
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
}

function TaskDetailPanel({ task, onClose, taskTraceMap }: TaskDetailPanelProps) {
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
