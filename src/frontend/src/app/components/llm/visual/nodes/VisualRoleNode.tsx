import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import type { VisualRoleNodeData } from '../types/visual';

export function VisualRoleNode({ data }: NodeProps<Node<VisualRoleNodeData>>) {
  const readiness = data.readiness;
  const runtimeStatus = data.runtimeStatus;
  
  // Determine status color based on readiness and runtime status
  let statusColor = 'bg-amber-400';
  let statusLabel = '待命';
  
  if (runtimeStatus?.running) {
    statusColor = 'bg-blue-400 animate-pulse';
    statusLabel = '运行中';
  } else if (readiness?.ready) {
    statusColor = 'bg-emerald-400';
    statusLabel = '就绪';
  } else if (readiness?.grade) {
    statusColor = 'bg-rose-400';
    statusLabel = readiness.grade;
  } else if (runtimeStatus?.lastRun) {
    statusColor = runtimeStatus.lastStatus === 'success' ? 'bg-emerald-400' : 'bg-rose-400';
    statusLabel = runtimeStatus.lastStatus === 'success' ? '就绪' : '失败';
  }

  // Format last run time
  const formatLastRun = (timestamp?: string): string => {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    
    if (minutes < 1) return '刚刚';
    if (minutes < 60) return `${minutes}分钟前`;
    if (hours < 24) return `${hours}小时前`;
    return `${days}天前`;
  };

  return (
    <div className="min-w-[200px] rounded-xl border border-cyan-400/40 bg-black/80 px-3 py-2 text-text-main shadow-[0_0_12px_rgba(34,211,238,0.2)]">
      <Handle type="target" position={Position.Left} className="!bg-cyan-300 !border-cyan-200" />
      
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold tracking-wide">{data.label}</span>
        <span className={`inline-flex items-center gap-1 rounded-full border border-white/10 px-2 py-0.5 text-[9px] uppercase ${statusColor} text-black font-bold`}>
          {runtimeStatus?.running && (
            <span className="inline-block w-1.5 h-1.5 bg-white rounded-full animate-pulse" />
          )}
          {statusLabel}
        </span>
      </div>
      
      {/* Description */}
      {data.description ? (
        <div className="mt-1 text-[10px] text-text-dim">{data.description}</div>
      ) : null}
      
      {/* Model Configuration */}
      {runtimeStatus?.config?.model && (
        <div className="mt-2 text-[9px] text-cyan-200/80 border-t border-white/10 pt-1">
          <div className="flex items-center gap-1">
            <span className="text-text-dim">模型:</span>
            <span className="font-medium text-cyan-200">{runtimeStatus.config.model}</span>
          </div>
        </div>
      )}
      
      {/* Last Run Info */}
      {runtimeStatus?.lastRun && !runtimeStatus.running && (
        <div className="mt-1 text-[8px] text-text-dim">
          上次运行: {formatLastRun(runtimeStatus.lastRun)}
        </div>
      )}
      
      {/* Capabilities */}
      <div className="mt-2 flex flex-wrap gap-1 text-[9px]">
        {data.requiresThinking ? (
          <span className="rounded bg-purple-500/20 px-2 py-0.5 text-purple-200">思考要求</span>
        ) : (
          <span className="rounded bg-emerald-500/20 px-2 py-0.5 text-emerald-200">基础能力</span>
        )}
        {typeof data.minConfidence === 'number' ? (
          <span className="rounded bg-black/40 px-2 py-0.5 text-text-dim">最低置信 {data.minConfidence}</span>
        ) : null}
      </div>
    </div>
  );
}
