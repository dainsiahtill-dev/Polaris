import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ContextMenu } from "./ContextMenu";

describe("LLM visual ContextMenu", () => {
  it("renders delete actions in a top-level fixed portal", () => {
    const action = vi.fn();
    const onClose = vi.fn();

    render(
      <div data-testid="editor-root">
        <ContextMenu
          x={160}
          y={120}
          title="连接操作"
          onClose={onClose}
          items={[
            {
              label: "删除连接",
              variant: "danger",
              action,
            },
          ]}
        />
      </div>,
    );

    const menu = screen.getByTestId("llm-visual-context-menu");
    expect(menu.parentElement).toBe(document.body);
    expect(menu).toHaveClass("fixed");
    expect(menu).toHaveStyle({ left: "160px", top: "120px" });

    fireEvent.click(screen.getByRole("button", { name: "删除连接" }));

    expect(action).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
