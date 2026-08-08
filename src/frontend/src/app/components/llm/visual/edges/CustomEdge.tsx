import {
  BaseEdge,
  getBezierPath,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import type { VisualEdgeData } from "../types/visual";

const EDGE_COLORS: Record<string, string> = {
  "provider-to-model": "#94a3b8",
  "model-to-role": "#64748b",
};

/**
 * CustomEdge — simplified for robust right-click interaction.
 *
 * Uses ReactFlow's built-in interactionWidth (40px wide invisible hit area)
 * instead of a custom hit-target path. This ensures onEdgeContextMenu fires
 * reliably across all browsers without CSS pointer-events conflicts.
 */
export function CustomEdge(props: EdgeProps<Edge<VisualEdgeData>>) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    targetX: props.targetX,
    targetY: props.targetY,
    sourcePosition: props.sourcePosition,
    targetPosition: props.targetPosition,
  });
  const stroke = props.data?.kind
    ? EDGE_COLORS[props.data.kind] || "#94a3b8"
    : "#94a3b8";

  return (
    <BaseEdge
      id={props.id}
      path={edgePath}
      labelX={labelX}
      labelY={labelY}
      label={props.label}
      labelStyle={props.labelStyle}
      labelShowBg={props.labelShowBg}
      labelBgStyle={props.labelBgStyle}
      labelBgPadding={props.labelBgPadding}
      labelBgBorderRadius={props.labelBgBorderRadius}
      style={{ ...props.style, stroke, strokeWidth: 2 }}
      markerStart={props.markerStart}
      markerEnd={props.markerEnd}
      interactionWidth={40}
    />
  );
}
