/**
 * ExecutionProgressBar - 目标执行进度条组件
 *
 * Phase 1.2: Goal Execution Projection
 */

import { Clock, Code, FileSearch, Lightbulb, TestTube, CheckCircle2 } from 'lucide-react';

import { Badge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import type { GoalExecutionView } from '@/app/types/appContracts';

interface ExecutionProgressBarProps {
  execution: GoalExecutionView;
  compact?: boolean;
}

const stageConfig: Record<
  string,
  {
    label: string;
    color: string;
    bgColor: string;
    icon: React.ReactNode;
  }
> = {
  planning: {
    label: '规划',
    color: 'text-amber-400',
    bgColor: 'bg-amber-400',
    icon: <Lightbulb className="size-3" />,
  },
  coding: {
    label: '编码',
    color: 'text-cyan-400',
    bgColor: 'bg-cyan-400',
    icon: <Code className="size-3" />,
  },
  testing: {
    label: '测试',
    color: 'text-violet-400',
    bgColor: 'bg-violet-400',
    icon: <TestTube className="size-3" />,
  },
  review: {
    label: '审查',
    color: 'text-blue-400',
    bgColor: 'bg-blue-400',
    icon: <FileSearch className="size-3" />,
  },
  completed: {
    label: '完成',
    color: 'text-emerald-400',
    bgColor: 'bg-emerald-400',
    icon: <CheckCircle2 className="size-3" />,
  },
  unknown: {
    label: '未知',
    color: 'text-slate-400',
    bgColor: 'bg-slate-400',
    icon: <Clock className="size-3" />,
  },
};

export function ExecutionProgressBar({ execution, compact = false }: ExecutionProgressBarProps) {
  const stage = execution.stage || 'unknown';
  const config = stageConfig[stage] || stageConfig.unknown;
  const percent = Math.round((execution.percent || 0) * 100);
  const filledBlocks = Math.floor(percent / 10);
  const emptyBlocks = 10 - filledBlocks;

  if (compact) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <Badge
          variant="outline"
          className={cn('gap-1 border-transparent px-1.5 py-0 text-xs', config.color)}
        >
          {config.icon}
          {config.label}
        </Badge>
        <div className="flex">
          {Array.from({ length: filledBlocks }).map((_, i) => (
            <div key={i} className={cn('mr-0.5 h-1.5 w-1.5 rounded-sm', config.bgColor)} />
          ))}
          {Array.from({ length: emptyBlocks }).map((_, i) => (
            <div key={i} className="mr-0.5 h-1.5 w-1.5 rounded-sm bg-slate-700" />
          ))}
        </div>
        <span className="text-slate-500">{percent}%</span>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Stage and Progress */}
      <div className="flex items-center justify-between">
        <Badge
          variant="outline"
          className={cn('gap-1.5 border-transparent', config.color)}
        >
          {config.icon}
          {config.label}
        </Badge>
        <span className={cn('text-sm font-medium', config.color)}>{percent}%</span>
      </div>

      {/* Progress Bar */}
      <div className="flex">
        {Array.from({ length: filledBlocks }).map((_, i) => (
          <div key={i} className={cn('mr-1 h-2 w-2 rounded-sm', config.bgColor)} />
        ))}
        {Array.from({ length: emptyBlocks }).map((_, i) => (
          <div key={i} className="mr-1 h-2 w-2 rounded-sm bg-slate-700" />
        ))}
      </div>

      {/* Current Task */}
      {execution.current_task && (
        <div className="text-xs text-slate-400 truncate">
          {execution.current_task}
        </div>
      )}

      {/* ETA */}
      {execution.eta_minutes !== undefined && execution.eta_minutes > 0 && (
        <div className="flex items-center gap-1 text-xs text-slate-500">
          <Clock className="size-3" />
          预计 {execution.eta_minutes} 分钟
        </div>
      )}

      {/* Stats */}
      <div className="flex items-center gap-3 text-xs text-slate-500">
        <span>任务: {execution.completed_tasks}/{execution.total_tasks}</span>
        {execution.failed_tasks > 0 && (
          <span className="text-red-400">失败: {execution.failed_tasks}</span>
        )}
      </div>
    </div>
  );
}

export default ExecutionProgressBar;
