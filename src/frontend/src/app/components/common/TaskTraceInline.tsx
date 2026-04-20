import React, { memo } from 'react';
import type { TaskTraceEvent } from '@/app/types/taskTrace';

interface TaskTraceInlineProps {
  traces: TaskTraceEvent[];
  maxLines?: number;
  className?: string;
}

function TaskTraceInlineComponent({ traces, maxLines = 1, className }: TaskTraceInlineProps) {
  const latestTrace = traces[traces.length - 1];
  if (!latestTrace) return null;

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed': return 'text-green-400';
      case 'failed': return 'text-red-400';
      case 'running': return 'text-blue-400';
      case 'started': return 'text-yellow-400';
      default: return 'text-gray-400';
    }
  };

  const getStatusDotColor = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-green-400';
      case 'failed': return 'bg-red-400';
      case 'running': return 'bg-blue-400';
      case 'started': return 'bg-yellow-400';
      case 'retry': return 'bg-orange-400';
      default: return 'bg-gray-400';
    }
  };

  return (
    <div className={`text-sm ${className}`}>
      <div className="flex items-center gap-2">
        <span className={`inline-block w-2 h-2 rounded-full ${getStatusDotColor(latestTrace.status)}`} />
        <span className="text-gray-300">{latestTrace.step_title}</span>
        <span className="text-gray-500 text-xs">{latestTrace.ts && new Date(latestTrace.ts).toLocaleTimeString()}</span>
      </div>
      {maxLines > 1 && latestTrace.step_detail && (
        <p className="text-gray-400 mt-1 truncate">{latestTrace.step_detail}</p>
      )}
    </div>
  );
}

export const TaskTraceInline = memo(TaskTraceInlineComponent);
