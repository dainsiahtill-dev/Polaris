import { useCallback, useMemo } from 'react';
import { DragDropContext, Droppable, Draggable, DropResult } from '@hello-pangea/dnd';
import { motion } from 'framer-motion';
import { LayoutGrid, List, Loader2 } from 'lucide-react';
import { KanbanCard } from './KanbanCard';
import { KanbanColumn } from './KanbanColumn';
import { PriorityBadge } from './PriorityBadge';
import {
  COLUMN_ORDER,
  COLUMN_CONFIG,
  type KanbanTask,
  type KanbanMoveEvent,
  type TaskStatus,
} from './types';

interface KanbanBoardProps {
  tasks: KanbanTask[];
  completedIds?: Set<string>;
  currentTaskId?: string;
  onTaskMove: (event: KanbanMoveEvent) => void;
  onTaskClick?: (task: KanbanTask) => void;
  onAddTask?: (status: TaskStatus) => void;
  isLoading?: boolean;
  className?: string;
}

/** 将任务数组按状态分组到 Kanban 列 */
function groupTasksByStatus(tasks: KanbanTask[]): Map<TaskStatus, KanbanTask[]> {
  const groups = new Map<TaskStatus, KanbanTask[]>();

  for (const status of COLUMN_ORDER) {
    groups.set(status, []);
  }

  for (const task of tasks) {
    const existing = groups.get(task.status);
    if (existing) {
      existing.push(task);
    } else {
      // 未知状态的任务放入 backlog
      const backlog = groups.get('backlog');
      if (backlog) {
        backlog.push({ ...task, status: 'backlog' });
      }
    }
  }

  return groups;
}

export function KanbanBoard({
  tasks,
  completedIds = new Set(),
  currentTaskId,
  onTaskMove,
  onTaskClick,
  onAddTask,
  isLoading = false,
  className = '',
}: KanbanBoardProps) {
  /** 按状态分组后的列数据 */
  const columns = useMemo(() => {
    const grouped = groupTasksByStatus(tasks);
    return COLUMN_ORDER.map((status) => ({
      id: status,
      title: COLUMN_CONFIG[status].title,
      titleZh: COLUMN_CONFIG[status].titleZh,
      tasks: grouped.get(status) ?? [],
      color: COLUMN_CONFIG[status].color,
    }));
  }, [tasks]);

  /** 处理拖拽结束事件 */
  const handleDragEnd = useCallback(
    (result: DropResult) => {
      const { draggableId, source, destination } = result;

      // 未放置到有效目标
      if (!destination) return;

      // 源位置和目标位置相同
      if (
        source.droppableId === destination.droppableId &&
        source.index === destination.index
      ) {
        return;
      }

      const event: KanbanMoveEvent = {
        taskId: draggableId,
        from: source.droppableId as TaskStatus,
        to: destination.droppableId as TaskStatus,
        fromIndex: source.index,
        toIndex: destination.index,
      };

      onTaskMove(event);
    },
    [onTaskMove]
  );

  if (isLoading) {
    return (
      <div className={`flex items-center justify-center h-64 ${className}`}>
        <Loader2 className="size-6 text-accent animate-spin" />
        <span className="ml-2 text-sm text-text-muted">Loading tasks...</span>
      </div>
    );
  }

  return (
    <DragDropContext onDragEnd={handleDragEnd}>
      <div className={`kanban-board flex gap-4 overflow-x-auto p-4 ${className}`}>
        {columns.map((column) => (
          <KanbanColumn
            key={column.id}
            column={column}
            completedIds={completedIds}
            currentTaskId={currentTaskId}
            onTaskClick={onTaskClick}
            onAddTask={onAddTask}
          />
        ))}
      </div>
    </DragDropContext>
  );
}

/** 从 PmTask 转换为 KanbanTask */
export function convertToKanbanTask(
  pmTask: {
    id: string;
    title?: string;
    goal?: string;
    summary?: string;
    status?: string;
    state?: string;
    done?: boolean;
    completed?: boolean;
    priority?: number;
  },
  defaultStatus: TaskStatus = 'todo'
): KanbanTask {
  // 解析状态字符串
  const rawStatus = pmTask.status || pmTask.state || defaultStatus;
  const statusLower = rawStatus.toLowerCase();

  let status: TaskStatus = defaultStatus;
  if (statusLower.includes('backlog')) {
    status = 'backlog';
  } else if (statusLower.includes('todo') || statusLower.includes('pending')) {
    status = 'todo';
  } else if (statusLower.includes('progress') || statusLower.includes('running')) {
    status = 'in_progress';
  } else if (statusLower.includes('done') || statusLower.includes('complete') || statusLower.includes('success')) {
    status = 'done';
  }

  // 解析优先级数字到标签
  let priority: KanbanTask['priority'] = 'medium';
  const p = pmTask.priority;
  if (typeof p === 'number') {
    if (p >= 4) priority = 'urgent';
    else if (p >= 3) priority = 'high';
    else if (p >= 2) priority = 'medium';
    else priority = 'low';
  }

  return {
    id: pmTask.id,
    title: pmTask.title || pmTask.goal || pmTask.summary || 'Untitled Task',
    goal: pmTask.goal,
    summary: pmTask.summary,
    priority,
    status,
    done: pmTask.done || pmTask.completed || false,
    completed: pmTask.completed,
  };
}
