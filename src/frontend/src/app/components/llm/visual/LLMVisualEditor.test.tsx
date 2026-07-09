import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { VisualGraphConfig } from "./types/visual";

vi.mock("@/api", () => ({
  apiFetch: vi.fn(async () => ({
    ok: true,
    json: async () => ({ roles: {}, timestamp: "2026-07-09T00:00:00Z" }),
  })),
}));

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>(
    "@xyflow/react",
  );
  return {
    ...actual,
    ReactFlow: ({ children }: { children?: ReactNode }) => (
      <div data-testid="llm-visual-flow-canvas">{children}</div>
    ),
    MiniMap: () => <div data-testid="llm-visual-minimap" />,
    Controls: () => <div data-testid="llm-visual-controls" />,
    Background: () => <div data-testid="llm-visual-background" />,
  };
});

import { LLMVisualEditor } from "./LLMVisualEditor";

const visualConfig: VisualGraphConfig = {
  providers: {
    "anthropic-compat": {
      name: "Anthropic Compat",
      default_model: "kimi-for-coding",
    },
  },
  roles: {
    pm: {
      provider_id: "anthropic-compat",
      model: "kimi-for-coding",
      max_concurrency: 3,
    },
    director: {
      bindings: [
        {
          provider_id: "anthropic-compat",
          model: "kimi-for-coding",
        },
      ],
    },
  },
};

describe("LLMVisualEditor layout", () => {
  it("keeps concurrency settings collapsed by default so the flow canvas remains primary", () => {
    render(<LLMVisualEditor config={visualConfig} onConfigChange={vi.fn()} />);

    expect(screen.getByTestId("llm-visual-flow-canvas")).toBeInTheDocument();
    expect(screen.getByText("Provider 1")).toBeInTheDocument();
    expect(screen.queryByText("anthropic-compat")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("llm-visual-toggle-concurrency"));

    expect(screen.getByText("anthropic-compat")).toBeInTheDocument();
  });
});
