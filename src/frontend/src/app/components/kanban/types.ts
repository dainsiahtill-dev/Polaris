/**
 * Kanban Board Types
 * Linear/Jira 风格看板视图的核心类型定义
 */

/** 任务优先级 */
export type Priority = 'low' | 'medium' | 'high' | 'urgent';

/** 任务状态 (Kanban 列) */
export type TaskStatus = 'backlog' | 'todo' | 'in_progress' | 'done';

/** Kanban 看板任务 */
export interface KanbanTask {
  id: string;
  title: string;
  goal?: string;
  summary?: string;
  description?: string;
  priority: Priority;
  status: TaskStatus;
  done: boolean;
  completed?: boolean;
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  dependencies?: string[];
  blocked_by?: string[];
  tags?: string[];
}

/** Kanban 列定义 */
export interface KanbanColumn {
  id: TaskStatus;
  title: string;
  titleZh: string;
  tasks: KanbanTask[];
  color?: string;
}

/** 列配置 */
export const COLUMN_CONFIG: Record<TaskStatus, { title: string; titleZh: string; color: string }> = {
  backlog: {
    title: 'Backlog',
    titleZh: '待办事项',
    color: 'border-slate-500/50',
  },
  todo: {
    title: 'To Do',
    titleZh: '计划中',
    color: 'border-blue-500/50',
  },
  in_progress: {
    title: 'In Progress',
    titleZh: '进行中',
    color: 'border-amber-500/50',
  },
  done: {
    title: 'Done',
    titleZh: '已完成',
    color: 'border-emerald-500/50',
  },
};

/** 所有列的默认顺序 */
export const COLUMN_ORDER: TaskStatus[] = ['backlog', 'todo', 'in_progress', 'done'];

/** 看板移动事件 */
export interface KanbanMoveEvent {
  taskId: string;
  from: TaskStatus;
  to: TaskStatus;
  fromIndex: number;
  toIndex: number;
}

/** 看板 Props */
export interface KanbanBoardProps {
  tasks: KanbanTask[];
  completedIds?: Set<string>;
  currentTaskId?: string;
  onTaskMove: (event: KanbanMoveEvent) => void;
  onTaskClick?: (task: KanbanTask) => void;
  isLoading?: boolean;
  className?: string;
}
