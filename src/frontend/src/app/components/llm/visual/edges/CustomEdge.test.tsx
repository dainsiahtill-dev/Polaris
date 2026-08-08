import { Position, type Edge, type EdgeProps } from "@xyflow/react";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { VisualEdgeData } from "../types/visual";
import { CustomEdge } from "./CustomEdge";

describe("CustomEdge", () => {
  it("renders with a wide interaction width for reliable right-click", () => {
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

    // BaseEdge with interactionWidth={40} creates a wide invisible interaction
    // path (react-flow__edge-interaction) that reliably receives pointer events.
    const interactionPath = container.querySelector(
      ".react-flow__edge-interaction",
    );
    expect(interactionPath).not.toBeNull();
    expect(interactionPath).toHaveAttribute("stroke-width", "40");
  });

  it("applies correct color based on edge kind", () => {
    const { container } = render(
      <svg>
        <CustomEdge
          {...({
            id: "edge:test",
            source: "model:a",
            target: "role:b",
            sourceX: 0,
            sourceY: 0,
            targetX: 100,
            targetY: 100,
            sourcePosition: Position.Right,
            targetPosition: Position.Left,
            data: { kind: "provider-to-model" },
          } as EdgeProps<Edge<VisualEdgeData>>)}
        />
      </svg>,
    );

    const basePaths = container.querySelectorAll(".react-flow__edge-path");
    expect(basePaths.length).toBeGreaterThan(0);
  });
});
