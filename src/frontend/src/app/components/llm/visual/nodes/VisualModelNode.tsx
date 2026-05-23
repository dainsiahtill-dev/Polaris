import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { getRoleDisplayLabel } from '@/app/constants/roleLabels';
import type { VisualModelNodeData } from '../types/visual';

export function VisualModelNode({ data }: NodeProps<Node<VisualModelNodeData>>) {
  return (
    <div className="min-w-[200px] rounded-xl border border-cyan-300/30 bg-black/60 px-3 py-2 text-text-main shadow-[0_0_10px_rgba(34,211,238,0.12)]">
      <Handle type="target" position={Position.Left} className="!bg-cyan-200 !border-cyan-100" />
      <Handle type="source" position={Position.Right} className="!bg-emerald-200 !border-emerald-100" />
      <div className="text-xs font-semibold">{data.label}</div>
      <div className="mt-1 text-[10px] text-text-dim">提供商: {data.providerId}</div>
      {data.assignedRoles && data.assignedRoles.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {data.assignedRoles.map((role) => (
            <span key={role} className="rounded bg-emerald-500/20 px-2 py-0.5 text-[9px] text-emerald-200">
              {getRoleDisplayLabel(role)}
            </span>
          ))}
        </div>
      ) : (
        <div className="mt-2 text-[9px] text-text-dim">未连接角色</div>
      )}
    </div>
  );
}
