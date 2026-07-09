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
    <>
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
        interactionWidth={0}
      />
      <path
        data-edge-id={props.id}
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={props.interactionWidth ?? 40}
        pointerEvents="stroke"
        className="llm-visual-edge-hit-target"
        cursor="context-menu"
      />
    </>
  );
}
