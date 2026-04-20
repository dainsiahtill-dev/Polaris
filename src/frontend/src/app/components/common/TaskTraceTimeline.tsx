import React, { useState, memo } from 'react';
import type { TaskTraceEvent } from '@/app/types/taskTrace';
import { ChevronDown, ChevronUp } from 'lucide-react';

interface TaskTraceTimelineProps {
  traces: TaskTraceEvent[];
  maxTraces?: number;
  expanded?: boolean;
  className?: string;
}

function TaskTraceTimelineComponent({ traces, maxTraces = 20, expanded = false, className }: TaskTraceTimelineProps) {
  const [isExpanded, setIsExpanded] = useState(expanded);
  const displayTraces = isExpanded ? traces.slice(-maxTraces) : traces.slice(-1);

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-green-400';
      case 'failed': return 'bg-red-400';
      case 'running': return 'bg-blue-400';
      case 'started': return 'bg-yellow-400';
      case 'retry': return 'bg-orange-400';
      default: return 'bg-gray-400';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed': return '✓';
      case 'failed': return '✗';
      case 'running': return '⋯';
      case 'retry': return '↻';
      default: return '○';
    }
  };

  if (traces.length === 0) return null;

  return (
    <div className={`${className}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500">执行步骤 ({traces.length})</span>
        {traces.length > 1 && (
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1"
          >
            {isExpanded ? '收起' : '展开'}
            {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        )}
      </div>
      <div className="space-y-1">
        {displayTraces.map((trace, idx) => (
          <div key={trace.event_id || idx} className="flex items-start gap-2 text-sm">
            <span className={`inline-flex items-center justify-center w-5 h-5 rounded-full text-xs ${getStatusColor(trace.status)} text-black font-bold shrink-0`}>
              {getStatusIcon(trace.status)}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-gray-300 truncate">{trace.step_title}</div>
              {isExpanded && trace.step_detail && (
                <div className="text-gray-500 text-xs truncate">{trace.step_detail}</div>
              )}
            </div>
            <span className="text-gray-600 text-xs shrink-0">
              {trace.ts && new Date(trace.ts).toLocaleTimeString()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export const TaskTraceTimeline = memo(TaskTraceTimelineComponent);
