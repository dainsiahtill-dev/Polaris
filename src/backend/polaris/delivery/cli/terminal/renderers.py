"""Output rendering: diff, ANSI, structured JSON, token stats, tool highlighting."""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import os
import sys
from collections.abc import Mapping
from typing import Any

from polaris.delivery.cli.context_status import ContextStats, render_context_panel
from polaris.delivery.cli.terminal._base import (
    _MODEL_PRICING_PER_M,
    _as_mapping,
    _get_tool_style,
    _json_event_packet,
    _json_event_text,
    _normalize_json_render,
    _safe_text,
    _tool_error,
    _tool_name,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ANSI color constants
# ---------------------------------------------------------------------------

_ANSI_GREEN = "\x1b[32m"
_ANSI_RED = "\x1b[31m"
_ANSI_RESET = "\x1b[0m"
_ANSI_BOLD = "\x1b[1m"
_ANSI_DIM = "\x1b[2m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_CYAN = "\x1b[36m"

# ---------------------------------------------------------------------------
# Diff detection and ANSI-colored rendering
# ---------------------------------------------------------------------------


def _extract_diff_text(payload: Mapping[str, Any]) -> str:
    """从 payload 中提取 diff/patch 字段。"""
    for source in (payload, _as_mapping(payload.get("result")), _as_mapping(payload.get("raw_result"))):
        for key in ("patch", "diff", "diff_patch", "workspace_diff", "unified_diff"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _has_diff_content(payload: Mapping[str, Any]) -> bool:
    """检查 payload 是否包含 diff 内容。"""
    return bool(_extract_diff_text(payload))


def _render_diff_ansi(diff_text: str, *, operation: str = "modify", max_lines: int = 200) -> str:
    """使用 ANSI 转义序列渲染带颜色的 diff（不依赖 Rich）。

    绿色行表示新增，红色表示删除，上下文行为默认色。
    """
    lines = diff_text.splitlines()
    visible = lines[:max_lines]
    has_unified = any(line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@") for line in visible)
    output: list[str] = []

    for line in visible:
        if has_unified:
            if line.startswith("+++ ") or line.startswith("--- "):
                output.append(f"{_ANSI_BOLD}{line}{_ANSI_RESET}")
            elif line.startswith("@@"):
                output.append(f"{_ANSI_DIM}{line}{_ANSI_RESET}")
            elif line.startswith("+") and not line.startswith("+++ "):
                output.append(f"{_ANSI_GREEN}{line}{_ANSI_RESET}")
            elif line.startswith("-") and not line.startswith("--- "):
                output.append(f"{_ANSI_RED}{line}{_ANSI_RESET}")
            else:
                output.append(line)
        else:
            if operation == "delete":
                output.append(f"{_ANSI_RED}{line}{_ANSI_RESET}")
            else:
                output.append(f"{_ANSI_GREEN}{line}{_ANSI_RESET}")

    remaining = len(lines) - len(visible)
    if remaining > 0:
        output.append(f"{_ANSI_DIM}... ({remaining} more lines){_ANSI_RESET}")
    return "\n".join(output)


def _print_json_with_rich(packet: Mapping[str, Any]) -> bool:
    try:
        from rich.console import Console
        from rich.syntax import Syntax
    except (RuntimeError, ValueError):
        return False

    rendered = json.dumps(dict(packet), ensure_ascii=False, indent=2)
    Console().print(Syntax(rendered, "json", theme="ansi_dark"))
    return True


def _print_tool_call_rich(tool_name: str, args: dict[str, Any]) -> None:
    """Print a tool call with Rich colored output."""
    style = _get_tool_style(tool_name)
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    try:
        from rich.console import Console
        from rich.text import Text

        console = Console()
        text = Text.assemble(
            ("▸ ", "cyan"),
            (tool_name, style),
            (f"({args_str})", "dim"),
        )
        console.print(text)
    except (RuntimeError, ValueError):
        print(f"▸ {tool_name}({args_str})")


def _print_tool_result_rich(
    tool_name: str,
    success: bool | None,
    duration_ms: int | None,
    error: str | None = None,
) -> None:
    """Print a tool result with Rich colored status indicator."""
    if success is True:
        status = "✓"
        status_style = "green"
    elif success is False:
        status = "✗"
        status_style = "red"
    else:
        status = "?"
        status_style = "yellow"

    duration = f" ({duration_ms}ms)" if duration_ms is not None else ""
    try:
        from rich.console import Console
        from rich.text import Text

        console = Console()
        text = Text.assemble(
            (f"{status} ", status_style),
            (tool_name, "cyan"),
            (duration, "dim"),
        )
        console.print(text)
        if error:
            error_text = Text.assemble(
                ("  └─ ", "dim"),
                (error[:200], "red"),
            )
            console.print(error_text)
    except (RuntimeError, ValueError):
        print(f"{status} {tool_name}{duration}")
        if error:
            print(f"  └─ {error[:200]}")


def _print_stream_json_event(*, event_type: str, payload: Mapping[str, Any], json_render: str) -> None:
    packet = _json_event_packet(event_type, payload)
    mode = _normalize_json_render(json_render)

    # V4: Handle tool_call with rich colored output
    if event_type == "tool_call" and mode == "pretty-color":
        tool_name = _safe_text(payload.get("tool")) or _tool_name(payload)
        args = _as_mapping(payload.get("args"))
        _print_tool_call_rich(tool_name, args)
        return

    # V4: Handle tool_result with rich colored status
    if event_type == "tool_result" and mode == "pretty-color":
        result_map = _as_mapping(payload.get("result", {}))
        tool_name = _safe_text(payload.get("tool")) or _tool_name(payload)
        success_val = result_map.get("success")
        if success_val is None:
            success_val = result_map.get("ok")
        success = bool(success_val) if isinstance(success_val, bool) else None
        duration_ms: int | None = None
        duration = result_map.get("duration_ms") or result_map.get("duration")
        if duration is not None:
            with contextlib.suppress(ValueError, TypeError):
                duration_ms = int(duration)
        error = _tool_error(payload)
        _print_tool_result_rich(tool_name, success, duration_ms, error)
        return

    # 检测是否为工具结果且包含 diff 内容
    if event_type in ("tool_result", "tool_call") and _has_diff_content(payload):
        diff_text = _extract_diff_text(payload)
        operation = _safe_text(_as_mapping(payload.get("result", {})).get("operation", "modify"))
        # 打印摘要行（工具名 + 状态）
        result_map = _as_mapping(payload.get("result", {}))
        success = result_map.get("success")
        status = "ok" if success is True else ("fail" if success is False else "?")
        tool_name = _safe_text(payload.get("tool")) or _safe_text(result_map.get("tool")) or event_type
        if mode == "pretty-color":
            # pretty-color: 打印彩色 diff
            print(f"[tool] {tool_name}  →  {status}")
            print(_render_diff_ansi(diff_text, operation=operation))
        elif mode == "pretty":
            # pretty: 打印 diff（无颜色）
            print(f"[tool] {tool_name}  →  {status}")
            print(diff_text)
        else:
            print(_json_event_text(packet, mode=mode))
        return

    if mode == "pretty-color":
        if _print_json_with_rich(packet):
            return
        mode = "pretty"
    print(_json_event_text(packet, mode=mode))


# ---------------------------------------------------------------------------
# Debug printing
# ---------------------------------------------------------------------------


def _supports_dim_debug() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    stream = getattr(sys, "stdout", None)
    return bool(stream is not None and hasattr(stream, "isatty") and stream.isatty())


def _style_debug_line(text: str) -> str:
    if not _supports_dim_debug():
        return text
    return f"\x1b[90m\x1b[2m{text}\x1b[0m"


def _debug_body_text(payload: Mapping[str, Any], *, json_render: str) -> str:
    packet = (
        dict(payload.get("payload") or {}) if isinstance(payload.get("payload"), Mapping) else payload.get("payload")
    )
    if isinstance(packet, str):
        return packet
    mode = _normalize_json_render(json_render)
    if mode == "pretty-color":
        mode = "pretty"
    if mode == "raw":
        return json.dumps(packet, ensure_ascii=False)
    return json.dumps(packet, ensure_ascii=False, indent=2)


def _print_debug_event(payload: Mapping[str, Any], *, json_render: str) -> None:
    category = _safe_text(payload.get("category")) or "debug"
    label = _safe_text(payload.get("label")) or "event"
    source = _safe_text(payload.get("source"))
    tags = _as_mapping(payload.get("tags"))

    header = f"[debug][{category}][{label}]"
    if source:
        header += f"[source={source}]"
    if tags:
        tag_bits = " ".join(f"{key}={value}" for key, value in sorted(tags.items()))
        if tag_bits:
            header += f" {tag_bits}"
    print(_style_debug_line(header))

    body_text = _debug_body_text(payload, json_render=json_render)
    for line in str(body_text or "").splitlines():
        print(_style_debug_line(f"[debug] {line}"))


# ---------------------------------------------------------------------------
# Structured JSON event output
# ---------------------------------------------------------------------------


def _build_structured_json_event(
    event_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a structured JSON event according to the schema."""
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
    result_map = _as_mapping(payload.get("result", {}))

    if event_type == "content_chunk":
        return {
            "type": "content_chunk",
            "content": str(payload.get("content") or ""),
            "timestamp": timestamp,
        }

    if event_type == "tool_call":
        return {
            "type": "tool_call",
            "tool": _safe_text(payload.get("tool")) or _tool_name(payload),
            "args": dict(payload.get("args", {})),
            "timestamp": timestamp,
        }

    if event_type == "tool_result":
        success_val = result_map.get("success")
        if success_val is None:
            success_val = result_map.get("ok")
        success = bool(success_val) if isinstance(success_val, bool) else None

        duration_ms: int | None = None
        duration = result_map.get("duration_ms") or result_map.get("duration")
        if duration is not None:
            with contextlib.suppress(ValueError, TypeError):
                duration_ms = int(duration)

        event: dict[str, Any] = {
            "type": "tool_result",
            "tool": _safe_text(payload.get("tool")) or _tool_name(payload),
            "success": success,
            "timestamp": timestamp,
        }
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        return event

    if event_type == "complete":
        tokens = payload.get("tokens")
        if isinstance(tokens, Mapping):
            token_data: dict[str, int] = {}
            prompt_tokens = tokens.get("prompt") or tokens.get("prompt_tokens")
            completion_tokens = tokens.get("completion") or tokens.get("completion_tokens")
            total_tokens = tokens.get("total") or tokens.get("total_tokens")
            if prompt_tokens is not None:
                with contextlib.suppress(ValueError, TypeError):
                    token_data["prompt"] = int(prompt_tokens)
            if completion_tokens is not None:
                with contextlib.suppress(ValueError, TypeError):
                    token_data["completion"] = int(completion_tokens)
            if total_tokens is not None:
                with contextlib.suppress(ValueError, TypeError):
                    token_data["total"] = int(total_tokens)
            elif "prompt" in token_data and "completion" in token_data:
                token_data["total"] = token_data["prompt"] + token_data["completion"]
        else:
            token_data = {}

        event = {
            "type": "complete",
            "content": str(payload.get("content") or ""),
            "timestamp": timestamp,
        }
        if token_data:
            event["tokens"] = token_data
        return event

    return {
        "type": event_type,
        "data": dict(payload),
        "timestamp": timestamp,
    }


def _print_structured_json_event(
    event: dict[str, Any],
    *,
    pretty: bool = False,
) -> None:
    """Print a structured JSON event to stdout (no ANSI codes)."""
    line = json.dumps(event, ensure_ascii=False, indent=2) if pretty else json.dumps(event, ensure_ascii=False)
    print(line, flush=True)


def _print_error_event(payload: Mapping[str, Any]) -> None:
    """Print an error event (used in JSON output mode)."""
    error_text = _tool_error(payload) or "unknown streaming error"
    event = {
        "type": "error",
        "error": error_text,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }
    _print_structured_json_event(event)


# ---------------------------------------------------------------------------
# Token stats
# ---------------------------------------------------------------------------


def _extract_token_usage(payload: Mapping[str, Any]) -> dict[str, int] | None:
    """Extract token usage from payload, checking multiple possible fields."""
    # Try direct usage field first
    usage = _as_mapping(payload.get("usage"))
    if usage:
        return {
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
        }

    # Try token_usage field
    token_usage = _as_mapping(payload.get("token_usage"))
    if token_usage:
        return {
            "prompt_tokens": int(token_usage.get("prompt_tokens", 0)),
            "completion_tokens": int(token_usage.get("completion_tokens", 0)),
            "total_tokens": int(token_usage.get("total_tokens", 0)),
        }

    # Try llm_usage field
    llm_usage = _as_mapping(payload.get("llm_usage"))
    if llm_usage:
        return {
            "prompt_tokens": int(llm_usage.get("prompt_tokens", 0)),
            "completion_tokens": int(llm_usage.get("completion_tokens", 0)),
            "total_tokens": int(llm_usage.get("total_tokens", 0)),
        }

    return None


def _estimate_cost(prompt_tokens: int, completion_tokens: int, model: str) -> str:
    """Calculate estimated cost based on token counts and model."""
    pricing = _MODEL_PRICING_PER_M.get(model.lower())
    if pricing is None:
        return "n/a"
    prompt_cost_per_m, completion_cost_per_m = pricing
    prompt_cost = (prompt_tokens / 1_000_000) * prompt_cost_per_m
    completion_cost = (completion_tokens / 1_000_000) * completion_cost_per_m
    total_cost = prompt_cost + completion_cost
    return f"~${total_cost:.4f}"


def _print_token_stats(payload: Mapping[str, Any], elapsed_seconds: float) -> None:
    """Print token statistics after complete event if token info is available.

    Shows cost/throughput if model and token usage are available.
    Shows context panel only if context_budget is available.
    """
    token_usage = _extract_token_usage(payload)

    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    has_token_usage = False
    if token_usage is not None:
        prompt_tokens = token_usage["prompt_tokens"]
        completion_tokens = token_usage["completion_tokens"]
        total_tokens = token_usage["total_tokens"]
        has_token_usage = True

    # Get model from payload - SSOT requires this from ContextOS, no hardcoded fallback
    model = _safe_text(payload.get("model"))
    if not model:
        # Model not in payload - skip stats display
        return

    # Resolve context limit from context_budget in event (ContextOS resolved)
    # SSOT: Context window MUST come from ContextOS via ModelCatalog resolution
    context_budget = payload.get("context_budget")
    if not isinstance(context_budget, Mapping):
        # context_budget not in payload - skip panel display
        return
    model_context_window = context_budget.get("model_context_window")
    if not isinstance(model_context_window, (int, float)) or model_context_window <= 0:
        # model_context_window invalid - skip panel display
        return
    context_limit = int(model_context_window)

    # Best practice: distinguish ContextOS estimate vs LLM actual
    # estimated_input_tokens: ContextOS budget estimation (before LLM call)
    # current_input_tokens: actual tokens from LLM response
    estimated_input_tokens = int(context_budget.get("current_input_tokens", 0))
    current_input_tokens = prompt_tokens  # LLM actual prompt_tokens

    # Calculate optional metrics for unified display
    cost_per_1k = None
    throughput = None
    if has_token_usage and elapsed_seconds > 0:
        cost_str = _estimate_cost(prompt_tokens, completion_tokens, model)
        if cost_str and cost_str != "n/a":
            with contextlib.suppress(ValueError):
                cost_per_1k = float(cost_str.replace("$", ""))
        throughput = total_tokens / elapsed_seconds

    # Create context stats for unified panel display
    stats = ContextStats(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        context_limit=context_limit,
        current_input_tokens=current_input_tokens,
        estimated_input_tokens=estimated_input_tokens,
        cost_per_1k=cost_per_1k,
        throughput=throughput,
    )

    # Try rich panel first
    panel_str = render_context_panel(stats, compact=True)
    if panel_str:
        # Use Rich Console to render markup (print() outputs raw markup text)
        try:
            from rich.console import Console

            rich_console = Console(force_terminal=True)
            rich_console.print(panel_str)
        except (RuntimeError, ValueError):
            print(panel_str)
        return

    # Fallback: plain text format
    print("[Token Stats]")
    print(f"  prompt tokens:     {prompt_tokens:>8,}")
    print(f"  completion tokens: {completion_tokens:>8,}")
    print(f"  total tokens:      {total_tokens:>8,}")
    if elapsed_seconds > 0:
        throughput = total_tokens / elapsed_seconds
        print(f"  throughput:       {throughput:.1f} tok/s")
    cost_str = _estimate_cost(prompt_tokens, completion_tokens, model)
    if cost_str:
        print(f"  estimated cost:    {cost_str}")
