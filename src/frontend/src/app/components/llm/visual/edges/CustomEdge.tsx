import { BaseEdge, getBezierPath, type EdgeProps, type Edge, type Node } from '@xyflow/react';
import type { VisualEdgeData } from '../types/visual';

const EDGE_COLORS: Record<string, string> = {
  'provider-to-model': '#22d3ee',
  'model-to-role': '#f472b6',
};

export function CustomEdge(props: EdgeProps<Edge<VisualEdgeData>>) {
  const [edgePath] = getBezierPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    targetX: props.targetX,
    targetY: props.targetY,
    sourcePosition: props.sourcePosition,
    targetPosition: props.targetPosition,
  });
  const stroke = props.data?.kind ? EDGE_COLORS[props.data.kind] || '#94a3b8' : '#94a3b8';
  return <BaseEdge path={edgePath} style={{ stroke, strokeWidth: 2 }} markerEnd={props.markerEnd} />;
}
