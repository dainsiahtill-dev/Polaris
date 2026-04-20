/**
 * TaskNode - 自定义任务节点组件
 *
 * 基于 @xyflow/react 的自定义节点，用于任务依赖图可视化
 */

import { memo } from 'react';
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { cn } from '@/app/components/ui/utils';
import type { TaskStatus } from '@/types/task';
import { TaskStatus as TaskStatusEnum } from '@/types/task';

// 扩展 Node 类型以支持 TaskNodeData
export type TaskNode = Node<TaskNodeData, 'taskNode'>;

export interface TaskNodeData extends Record<string, unknown> {
  /** 任务标题 */
  label: string;
  /** 任务状态 */
  status: TaskStatus;
  /** 任务描述（可选） */
  description?: string;
  /** 是否有依赖 */
  hasDependencies: boolean;
  /** 是否被依赖 */
  isDependedUpon: boolean;
  /** 是否在循环依赖中 */
  inCycle?: boolean;
  /** 点击回调 */
  onClick?: (taskId: string) => void;
}

/** 任务状态到颜色的映射 */
const STATUS_COLORS: Record<TaskStatus, { bg: string; border: string; text: string; badge: string }> = {
  [TaskStatusEnum.PENDING]: {
    bg: 'bg-slate-800/80',
    border: 'border-slate-600',
    text: 'text-slate-200',
    badge: 'bg-slate-500/20 text-slate-300 border-slate-500/30',
  },
  [TaskStatusEnum.IN_PROGRESS]: {
    bg: 'bg-amber-900/60',
    border: 'border-amber-500/60',
    text: 'text-amber-100',
    badge: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
  },
  [TaskStatusEnum.COMPLETED]: {
    bg: 'bg-emerald-900/60',
    border: 'border-emerald-500/60',
    text: 'text-emerald-100',
    badge: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  },
  [TaskStatusEnum.SUCCESS]: {
    bg: 'bg-emerald-900/60',
    border: 'border-emerald-500/60',
    text: 'text-emerald-100',
    badge: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  },
  [TaskStatusEnum.FAILED]: {
    bg: 'bg-red-900/60',
    border: 'border-red-500/60',
    text: 'text-red-100',
    badge: 'bg-red-500/20 text-red-300 border-red-500/30',
  },
  [TaskStatusEnum.BLOCKED]: {
    bg: 'bg-violet-900/60',
    border: 'border-violet-500/60',
    text: 'text-violet-100',
    badge: 'bg-violet-500/20 text-violet-300 border-violet-500/30',
  },
};

/** 状态中文标签 */
const STATUS_LABELS: Record<TaskStatus, string> = {
  [TaskStatusEnum.PENDING]: '待处理',
  [TaskStatusEnum.IN_PROGRESS]: '进行中',
  [TaskStatusEnum.COMPLETED]: '已完成',
  [TaskStatusEnum.SUCCESS]: '成功',
  [TaskStatusEnum.FAILED]: '失败',
  [TaskStatusEnum.BLOCKED]: '阻塞',
};

/**
 * TaskNode 组件
 * 展示单个任务节点及其状态
 */
export const TaskNode = memo(function TaskNode({ data, id }: NodeProps<TaskNode>) {
  const { label, status, description, hasDependencies, isDependedUpon, inCycle } = data;

  const colors = STATUS_COLORS[status] || STATUS_COLORS[TaskStatusEnum.PENDING];
  const statusLabel = STATUS_LABELS[status] || '未知';

  const handleClick = () => {
    data.onClick?.(id);
  };

  return (
    <div
      className={cn(
        'min-w-[180px] max-w-[240px] rounded-lg border p-3 cursor-pointer transition-all duration-200',
        'hover:shadow-lg hover:scale-[1.02]',
        colors.bg,
        colors.border,
        inCycle && 'ring-2 ring-red-500 ring-offset-2 ring-offset-slate-900'
      )}
      onClick={handleClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          handleClick();
        }
      }}
    >
      {/* 左侧连接点（目标） */}
      {hasDependencies && (
        <Handle
          type="target"
          position={Position.Left}
          className="!w-2.5 !h-2.5 !bg-slate-400 !border-slate-300"
        />
      )}

      {/* 右侧连接点（源） */}
      {isDependedUpon && (
        <Handle
          type="source"
          position={Position.Right}
          className="!w-2.5 !h-2.5 !bg-slate-400 !border-slate-300"
        />
      )}

      {/* 节点内容 */}
      <div className="space-y-1.5">
        {/* 标题行 */}
        <div className="flex items-start justify-between gap-2">
          <span
            className={cn(
              'text-xs font-medium leading-tight line-clamp-2',
              colors.text
            )}
            title={label}
          >
            {label}
          </span>
        </div>

        {/* 描述行（可选） */}
        {description && (
          <p
            className="text-[10px] text-slate-400 line-clamp-1"
            title={description}
          >
            {description}
          </p>
        )}

        {/* 底部状态和标识 */}
        <div className="flex items-center justify-between pt-1 border-t border-white/10">
          {/* 状态标签 */}
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-0.5 text-[9px] font-medium border',
              colors.badge
            )}
          >
            {status === TaskStatusEnum.IN_PROGRESS && (
              <span className="inline-block w-1.5 h-1.5 bg-amber-400 rounded-full mr-1 animate-pulse" />
            )}
            {statusLabel}
          </span>

          {/* 依赖标识 */}
          <div className="flex items-center gap-1">
            {inCycle && (
              <span
                className="inline-flex items-center gap-0.5 rounded bg-red-500/20 px-1.5 py-0.5 text-[8px] text-red-300 font-medium"
                title="循环依赖"
              >
                <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 2v4m0 12v4M2 12h4m12 0h4" />
                  <path d="M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83" />
                </svg>
                循环
              </span>
            )}
            {hasDependencies && (
              <span className="text-[8px] text-slate-500" title="有前置依赖">
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                </svg>
              </span>
            )}
            {isDependedUpon && (
              <span className="text-[8px] text-slate-500" title="被其他任务依赖">
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v4m0 12v4M2 12h4m12 0h4" />
                </svg>
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
});

export default TaskNode;
