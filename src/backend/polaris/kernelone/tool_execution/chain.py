"""Tool chain execution functions for KernelOne tool execution."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.constants import DEFAULT_MAX_RETRIES
from polaris.kernelone.tool_execution.constants import (
    DEFAULT_READ_RADIUS,
    MAX_TOOL_READ_LINES,
    READ_ONLY_TOOLS,
)
from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name, normalize_tool_args
from polaris.kernelone.tool_execution.models import ToolChainStep
from polaris.kernelone.tool_execution.utils import safe_int


def parse_tool_chain_step(step: dict[str, Any]) -> ToolChainStep:
    """Parse a tool plan step into a ToolChainStep with chain metadata."""
    raw_tool = str(step.get("tool") or "").strip()
    tool = canonicalize_tool_name(raw_tool)
    args = step.get("args") if isinstance(step.get("args"), dict) else {}
    args = normalize_tool_args(tool, args)

    on_error = step.get("on_error", "stop")
    if on_error not in ("stop", "retry", "continue"):
        on_error = "stop"

    # Read-only tools default to retry on error
    if tool in READ_ONLY_TOOLS and on_error == "stop":
        on_error = "retry"

    return ToolChainStep(
        tool=tool,
        args=args,
        step_id=step.get("step_id"),
        save_as=step.get("save_as"),
        input_from=step.get("input_from"),
        on_error=on_error,
        max_retries=safe_int(step.get("max_retries"), DEFAULT_MAX_RETRIES),
    )


def normalize_tool_plan(
    tool_plan: list[dict[str, Any]],
    around_history: dict[tuple[str, int], dict[str, Any]],
    need_more_context_count: int,
) -> list[dict[str, Any]]:
    """Normalize a tool plan, handling around history suggestions."""
    normalized: list[dict[str, Any]] = []
    for step in tool_plan:
        if not isinstance(step, dict):
            continue
        raw_tool = str(step.get("tool") or "").strip()
        tool = canonicalize_tool_name(raw_tool)
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        args = normalize_tool_args(tool, args)
        step = dict(step)
        step["tool"] = tool
        step["args"] = args
        if tool == "repo_read_around":
            file_arg = args.get("file") or args.get("path") or args.get("file_path")
            line_no = args.get("line") or args.get("around_line") or args.get("around") or args.get("center_line")
            radius = safe_int(args.get("radius"), DEFAULT_READ_RADIUS)
            if file_arg and line_no is not None:
                key = (str(file_arg), int(line_no))
                suggestion = around_history.get(key, {}).get("suggest_radius")
                if suggestion and suggestion > radius:
                    args = dict(args)
                    args["radius"] = suggestion
                    step = dict(step)
                    step["args"] = args
                if need_more_context_count >= 2 and key in around_history:
                    start_line = around_history[key].get("start_line")
                    end_line = around_history[key].get("end_line")
                    if isinstance(start_line, int) and isinstance(end_line, int):
                        width = end_line - start_line + 1
                        width = max(width, DEFAULT_READ_RADIUS * 2)
                        width = min(width + 80, MAX_TOOL_READ_LINES)
                        half = width // 2
                        new_start = max(1, int(line_no) - half)
                        new_end = new_start + width - 1
                        step = {
                            "tool": "repo_read_slice",
                            "args": {
                                "file": file_arg,
                                "start": new_start,
                                "end": new_end,
                            },
                        }
        normalized.append(step)
    return normalized
