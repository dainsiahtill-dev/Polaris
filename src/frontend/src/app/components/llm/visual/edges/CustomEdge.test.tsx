import { Position, type Edge, type EdgeProps } from "@xyflow/react";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { VisualEdgeData } from "../types/visual";
import { CustomEdge } from "./CustomEdge";

describe("CustomEdge", () => {
  it("keeps a wide invisible hit target for edge context menus", () => {
    const { container } = render(
      <svg>
        <CustomEdge
          {...({
            id: "edge:model:director:0",
            source: "model:qwen",
            target: "role:director",
            sourceX: 10,
            sourceY: 20,
            targetX: 180,
            targetY: 80,
            sourcePosition: Position.Right,
            targetPosition: Position.Left,
            markerEnd: "url(#arrow)",
            data: { kind: "model-to-role" },
          } as EdgeProps<Edge<VisualEdgeData>>)}
        />
      </svg>,
    );

    const interactionPath = container.querySelector(
      ".llm-visual-edge-hit-target",
    );
    expect(interactionPath).not.toBeNull();
    expect(interactionPath).toHaveAttribute("stroke", "transparent");
    expect(interactionPath).toHaveAttribute("stroke-width", "40");
    expect(interactionPath).toHaveAttribute("pointer-events", "stroke");
  });
});
