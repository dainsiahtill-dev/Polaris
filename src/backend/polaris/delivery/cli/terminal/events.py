"""Event handling and streaming turn execution logic."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.host.public import RoleHostKind
from polaris.delivery.cli.terminal._base import (
    _EMPTY_TOOL_BLOCK_RE,
    _as_mapping,
    _safe_text,
    _tool_error,
)
from polaris.delivery.cli.terminal.layout import (
    _create_turn_spinner,
    _TurnExecutionResult,
)
from polaris.delivery.cli.terminal.renderers import (
    _build_structured_json_event,
    _extract_token_usage,
    _print_debug_event,
    _print_error_event,
    _print_stream_json_event,
    _print_structured_json_event,
    _print_token_stats,
)

if TYPE_CHECKING:
    from polaris.delivery.cli.director.console_host import RoleConsoleHost

logger = logging.getLogger(__name__)


async def _stream_turn(
    host: RoleConsoleHost,
    *,
    role: str,
    session_id: str,
    message: str,
    json_render: str,
    debug: bool,
    spinner_label: str,
    dry_run: bool = False,
    output_format: str = "text",
    enable_cognitive: bool | None = None,
) -> _TurnExecutionResult:
    # Determine if we're in structured JSON output mode
    use_json_output = output_format in ("json", "json-pretty")
    json_output_pretty = output_format == "json-pretty"

    spinner = _create_turn_spinner(label=spinner_label)
    if not use_json_output:
        spinner.start()
        # Show minimal context status indicator while waiting
        try:
            from rich.console import Console

            Console()
            status_line = "[dim]🤖 LLM request started...[/dim]"
            print(status_line, end="\r", flush=True)
        except (RuntimeError, ValueError):
            logger.warning("Failed to create rich console for spinner")
    turn_start_time = time.monotonic()
    first_token_time: float | None = None
    content_open = False
    thinking_open = False
    saw_content_chunk = False
    saw_thinking_chunk = False
    thinking_tail_newline = True
    # Spinner stops when user-visible content arrives or error occurs
    stop_spinner_event_types = {
        "content_chunk",
        "thinking_chunk",
        "complete",
        "error",
    }
    # Dry-run state
    dry_run_count = 0
    dry_run_done = False
    saw_error = False
    content_parts: list[str] = []
    final_content = ""

    def _dry_run_banner() -> None:
        print("\x1b[33m=== DRY-RUN MODE: No tools will be executed ===\x1b[0m")

    def _dry_run_tool_line(tool_name: str, args: dict[str, Any]) -> None:
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        print(f"  \x1b[33m[DRY-RUN] Would execute: {tool_name}({args_str})\x1b[0m")
        print("  \x1b[33m[DRY-RUN] Skipping actual execution\x1b[0m")

    def _dry_run_summary(count: int) -> None:
        print(f"\x1b[33mDry-run complete: {count} tool call(s) would be executed\x1b[0m")

    def _show_ttft_if_first_token() -> None:
        nonlocal first_token_time
        if first_token_time is not None:
            return
        first_token_time = time.monotonic()
        ttft_ms = (first_token_time - turn_start_time) * 1000
        # Brief TTFT display that gets overwritten by first content
        print(f"\r[dim]TTFT: {ttft_ms:.0f}ms[/dim]  ", end="", flush=True)

    def _close_content_stream() -> None:
        nonlocal content_open
        if content_open:
            print()
            content_open = False

    def _open_thinking_stream() -> None:
        nonlocal thinking_open, thinking_tail_newline
        if thinking_open:
            return
        _close_content_stream()
        print("<thinking>")
        thinking_open = True
        thinking_tail_newline = True

    def _write_thinking_chunk(chunk: str) -> None:
        nonlocal saw_thinking_chunk, thinking_tail_newline
        if not chunk:
            return
        _open_thinking_stream()
        print(chunk, end="", flush=True)
        saw_thinking_chunk = True
        thinking_tail_newline = chunk.endswith("\n")

    def _close_thinking_stream() -> None:
        nonlocal thinking_open, thinking_tail_newline
        if not thinking_open:
            return
        if not thinking_tail_newline:
            print()
        print("</thinking>")
        thinking_open = False
        thinking_tail_newline = True

    try:
        logger.debug("stream_turn started: session_id=%s role=%s", session_id, role)
        async for event in host.stream_turn(
            session_id,
            message,
            context={
                "role": role,
                "host_kind": _safe_text(getattr(host.config, "host_kind", RoleHostKind.CLI.value))
                or RoleHostKind.CLI.value,
            },
            role=role,
            debug=debug,
            enable_cognitive=enable_cognitive,
        ):
            event_type = _safe_text(event.get("type"))
            logger.debug("stream_turn event: type=%s", event_type)
            if event_type in stop_spinner_event_types:
                await spinner.stop()

            # In dry-run mode, stop after all tool calls are processed
            if dry_run and dry_run_done:
                break

            payload = _as_mapping(event.get("data"))
            if event_type == "error" and not payload:
                fallback_error = _safe_text(event.get("error"))
                if fallback_error:
                    payload = {"error": fallback_error}

            if event_type == "content_chunk":
                _show_ttft_if_first_token()
                chunk = str(payload.get("content") or "")
                if chunk and not _EMPTY_TOOL_BLOCK_RE.match(chunk):
                    content_parts.append(chunk)
                    if use_json_output:
                        _print_structured_json_event(
                            _build_structured_json_event("content_chunk", {"content": chunk}),
                            pretty=json_output_pretty,
                        )
                    else:
                        _close_thinking_stream()
                        print(chunk, end="", flush=True)
                    content_open = True
                    saw_content_chunk = True
                continue

            if event_type == "thinking_chunk":
                _show_ttft_if_first_token()
                chunk = str(payload.get("content") or "")
                _write_thinking_chunk(chunk)
                continue

            if event_type == "tool_call":
                if use_json_output:
                    tool_name = _safe_text(payload.get("tool"))
                    tool_args = _as_mapping(payload.get("args"))
                    _print_structured_json_event(
                        _build_structured_json_event(
                            "tool_call",
                            {"tool": tool_name, "args": tool_args},
                        ),
                        pretty=json_output_pretty,
                    )
                else:
                    _close_thinking_stream()
                    _close_content_stream()
                    _print_stream_json_event(
                        event_type="tool_call",
                        payload=payload,
                        json_render=json_render,
                    )
                if dry_run:
                    tool_name = _safe_text(payload.get("tool"))
                    tool_args = _as_mapping(payload.get("args"))
                    _dry_run_tool_line(tool_name, tool_args)
                    dry_run_count += 1
                continue

            if event_type == "tool_result":
                if use_json_output:
                    tool_name = _safe_text(payload.get("tool"))
                    duration_ms = payload.get("duration_ms")
                    error_msg = _tool_error(payload)
                    success = error_msg is None
                    event_payload = {
                        "tool": tool_name,
                        "success": success,
                    }
                    if duration_ms is not None:
                        event_payload["duration_ms"] = duration_ms
                    _print_structured_json_event(
                        _build_structured_json_event("tool_result", event_payload),
                        pretty=json_output_pretty,
                    )
                else:
                    _close_thinking_stream()
                    _close_content_stream()
                    _print_stream_json_event(
                        event_type="tool_result",
                        payload=payload,
                        json_render=json_render,
                    )
                # In dry-run mode, tool_result signals end of tool call sequence
                if dry_run:
                    dry_run_done = True
                # Restart spinner to show LLM is still processing after tool execution
                if not use_json_output:
                    spinner.restart()
                continue

            if event_type == "debug":
                _close_thinking_stream()
                _close_content_stream()
                _print_debug_event(payload, json_render=json_render)
                continue

            if event_type == "error":
                saw_error = True
                if use_json_output:
                    error_text = _tool_error(payload) or "unknown streaming error"
                    _print_error_event({"error": error_text})
                else:
                    _close_thinking_stream()
                    _close_content_stream()
                    error_text = _tool_error(payload) or "unknown streaming error"
                    print(f"[error] {error_text}", file=sys.stderr)
                continue

            if event_type == "complete":
                thinking = str(payload.get("thinking") or "")
                if thinking and not saw_thinking_chunk:
                    _write_thinking_chunk(thinking)
                _close_thinking_stream()
                content = str(payload.get("content") or "")
                if content:
                    final_content = content
                if content and not saw_content_chunk:
                    if use_json_output:
                        _print_structured_json_event(
                            _build_structured_json_event("content_chunk", {"content": content}),
                            pretty=json_output_pretty,
                        )
                    else:
                        print(content, end="", flush=True)
                    content_open = True
                    saw_content_chunk = True
                if not use_json_output and content_open:
                    print()
                    content_open = False
                if not dry_run:
                    elapsed = time.monotonic() - turn_start_time
                    if use_json_output:
                        token_usage = _extract_token_usage(payload)
                        if token_usage:
                            tokens_dict = {
                                "prompt": token_usage["prompt_tokens"],
                                "completion": token_usage["completion_tokens"],
                                "total": token_usage["total_tokens"],
                            }
                        else:
                            tokens_dict = {}
                        # Include context_budget for context window display if available
                        context_budget = payload.get("context_budget")
                        complete_payload: dict[str, Any] = {"tokens": tokens_dict}
                        if isinstance(context_budget, Mapping):
                            complete_payload["context_budget"] = dict(context_budget)
                        if not saw_content_chunk:
                            complete_payload["content"] = content
                        _print_structured_json_event(
                            _build_structured_json_event("complete", complete_payload),
                            pretty=json_output_pretty,
                        )
                    else:
                        _print_token_stats(payload, elapsed)
                # In dry-run mode, complete signals end
                if dry_run:
                    dry_run_done = True

        _close_thinking_stream()
        if content_open:
            print()
        logger.debug("stream_turn event loop exhausted normally")
    finally:
        # Gracefully stop spinner even if the event loop is shutting down.
        # Suppress CancelledError: during asyncio.run() teardown, pending tasks
        # (including the spinner) are cancelled; awaiting them would re-raise.
        if not use_json_output:
            with contextlib.suppress(asyncio.CancelledError):
                await spinner.stop()

    if dry_run:
        _dry_run_summary(dry_run_count)
    # FIX-20250422-v4: Always aggregate content_parts into final_content.
    # The old guard "if not final_content and content_parts" only fired when
    # final_content was exactly falsy (""). If complete.content was a whitespace
    # string, final_content would be non-empty but content_parts might have more
    # content. We now always prefer the union of both sources.
    aggregated = "".join(content_parts)
    _has_aggregated = bool(aggregated)
    _has_final = bool(final_content.strip())
    if _has_aggregated and not _has_final:
        final_content = aggregated
    elif _has_aggregated and _has_final and aggregated.strip() != final_content.strip():
        logger.debug(
            "stream_content_divergence: complete_len=%d chunks_len=%d",
            len(final_content),
            len(aggregated),
        )
    return _TurnExecutionResult(
        role=role,
        session_id=session_id,
        final_content=final_content,
        saw_error=saw_error,
    )


def _run_streaming_turn(
    host: RoleConsoleHost,
    *,
    role: str,
    session_id: str,
    message: str,
    json_render: str,
    debug: bool,
    spinner_label: str,
    dry_run: bool = False,
    output_format: str = "text",
    enable_cognitive: bool | None = None,
) -> _TurnExecutionResult:
    try:
        return asyncio.run(
            _stream_turn(
                host,
                role=role,
                session_id=session_id,
                message=message,
                json_render=json_render,
                debug=debug,
                spinner_label=spinner_label,
                dry_run=dry_run,
                output_format=output_format,
                enable_cognitive=enable_cognitive,
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        # User interrupted or task cancelled - graceful shutdown
        return _TurnExecutionResult(role=role, session_id=session_id, saw_error=True)
    except Exception as exc:
        # Surface unexpected errors so users know *why* the turn aborted.
        logger.exception("Streaming turn aborted unexpectedly: %s", exc)
        raise
