"""Prompt / context / event / parse leaf helpers for RoleExecutionKernel.

Holds the small leaf helper bodies extracted verbatim (behavior-preserving)
into free functions. The class methods become thin delegating shims.

FROZEN behavior notes (do NOT change):
- ``parse_content_and_thinking_tool_calls`` remains reachable as the bound
  method ``kernel._parse_content_and_thinking_tool_calls`` (it is a monkeypatch
  target in tests and a back-reference from ``turn_materializer``); the shim
  preserves that call-time indirection by routing collaborator access through
  ``kernel._get_output_parser()``.
- All collaborator calls (prompt builder, output parser, event emitter) go
  through ``kernel._<method>`` so the monkeypatch / dependency-injection surface
  is unchanged.
- ``resolve_stream_run_id`` preserves its function-local ``import uuid`` and the
  explicit UTF-8 read of ``latest_run.json``.
- ``build_system_prompt_for_request`` preserves the explicit-kwargs vs
  ``TypeError``-fallback compatibility branches verbatim.
"""

from __future__ import annotations

import json
import logging
import os
import warnings
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.cells.roles.kernel.internal.kernel.request_tool_gating import (
    tool_contract_requires_single_batch,
)
from polaris.cells.roles.kernel.internal.output_parser import ToolCallResult
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest
from polaris.kernelone.storage import resolve_storage_roots

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
    from polaris.infrastructure.log_pipeline.writer import LogEventWriter

logger = logging.getLogger(__name__)


def emit_event(
    kernel: RoleExecutionKernel,
    *,
    event_type: str,
    role: str,
    run_id: str,
    task_id: str | None,
    attempt: int = 0,
    publish_realtime: bool = True,
    **kwargs: Any,
) -> None:
    """发射 LLM 事件（委托到 KernelEventEmitter）"""
    kernel._get_event_emitter().emit_runtime_llm_event(
        event_type=event_type,
        role=role,
        run_id=run_id,
        task_id=task_id,
        attempt=attempt,
        publish_realtime=publish_realtime,
        workspace=kernel.workspace,
        **kwargs,
    )


def emit_stream_log_event(
    kernel: RoleExecutionKernel,
    *,
    writer: LogEventWriter | None,
    role: str,
    run_id: str,
    task_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """发射流日志事件（委托到 KernelEventEmitter）"""
    kernel._get_event_emitter().emit_stream_log_event(
        writer=writer,
        role=role,
        run_id=run_id,
        task_id=task_id,
        event_type=event_type,
        payload=payload,
    )


def resolve_stream_run_id(kernel: RoleExecutionKernel, request_run_id: str | None) -> str:
    """Resolve stream run_id from request or workspace runtime metadata."""
    requested = str(request_run_id or "").strip()
    if requested:
        return requested

    workspace = str(kernel.workspace or "").strip() or os.getcwd()
    try:
        roots = resolve_storage_roots(workspace)
        latest_run_file = os.path.join(roots.runtime_root, "latest_run.json")
        if os.path.isfile(latest_run_file):
            with open(latest_run_file, encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict) and payload.get("run_id"):
                return str(payload.get("run_id", "").strip())
    except (RuntimeError, ValueError):
        logger.warning("Failed to resolve stream run_id from latest_run.json", exc_info=True)
    # Fallback: generate a new run_id so tool events can be journaled
    import uuid

    return f"auto_{uuid.uuid4().hex[:12]}"


def build_stream_log_writer(kernel: RoleExecutionKernel, run_id: str) -> LogEventWriter | None:
    """Create a log writer for streaming events."""
    if not run_id:
        return None
    workspace = str(kernel.workspace or "").strip() or os.getcwd()
    try:
        from polaris.infrastructure.log_pipeline.writer import get_writer

        return get_writer(workspace=workspace, run_id=run_id)
    except (RuntimeError, ValueError):
        logger.warning("Failed to create stream log writer for run_id=%s", run_id, exc_info=True)
        return None


def process_deprecated_params(request: RoleTurnRequest) -> str:
    """处理废弃参数"""
    appendix_parts: list[str] = []
    seen: set[str] = set()

    if request.prompt_appendix:
        token = str(request.prompt_appendix).strip()
        if token and token not in seen:
            seen.add(token)
            appendix_parts.append(token)

    if request.system_prompt:
        token = str(request.system_prompt).strip()
        if token:
            warnings.warn(
                "RoleTurnRequest.system_prompt is deprecated; use prompt_appendix instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            if token not in seen:
                seen.add(token)
                appendix_parts.append(token)

    extra_context = getattr(request, "extra_context", None)
    if extra_context:
        token = f"【额外上下文】\n{extra_context}"
        if token not in seen:
            seen.add(token)
            appendix_parts.append(token)

    return "\n\n".join(appendix_parts)


def build_context(profile: RoleProfile, request: RoleTurnRequest) -> ContextRequest:
    """构建上下文请求"""
    context_os_snapshot = None
    context_override = dict(request.context_override) if isinstance(request.context_override, dict) else {}
    if isinstance(request.context_override, dict):
        context_os_snapshot = request.context_override.get("context_os_snapshot")
    return ContextRequest(
        message=request.message,
        history=tuple(request.history) if request.history else (),
        task_id=request.task_id,
        context_os_snapshot=context_os_snapshot,
        context_override=context_override or None,
    )


def build_system_prompt_for_request(
    kernel: RoleExecutionKernel,
    profile: RoleProfile,
    request: RoleTurnRequest,
    prompt_appendix: str,
) -> str:
    """Build system prompt with domain-aware fallback compatibility."""
    domain = str(getattr(request, "domain", "") or "").strip().lower() or "code"
    context_override = getattr(request, "context_override", None)
    request_message = str(getattr(request, "message", "") or "")
    prompt_layer_options = kernel._resolve_prompt_layer_options(context_override, message=request_message)
    try:
        if prompt_layer_options:
            # Explicit kwargs (not **options) so a stray key can never bind
            # to a positional parameter; options only ever carries these two.
            return kernel._get_prompt_builder().build_system_prompt(
                profile,
                prompt_appendix,
                domain=domain,
                message=request_message,
                include_working_memory_contract=prompt_layer_options.get("include_working_memory_contract", True),
                include_tool_policy=prompt_layer_options.get("include_tool_policy", True),
            )
        return kernel._get_prompt_builder().build_system_prompt(
            profile,
            prompt_appendix,
            domain=domain,
            message=request_message,
        )
    except TypeError:
        return kernel._get_prompt_builder().build_system_prompt(profile, prompt_appendix)


def resolve_prompt_layer_options(context_override: Any, *, message: str | None = None) -> dict[str, bool]:
    """Resolve per-turn prompt layer switches from explicit runtime context."""
    if not isinstance(context_override, dict):
        return {}

    def _forced_tool_choice_name(raw_choice: Any) -> str:
        if isinstance(raw_choice, dict):
            function_payload = raw_choice.get("function")
            if isinstance(function_payload, dict):
                return str(function_payload.get("name") or "").strip().lower()
            return str(raw_choice.get("name") or "").strip().lower()
        return str(raw_choice or "").strip().lower()

    delivery_mode = str(context_override.get("delivery_mode") or "").strip().lower()
    codegen_mode = str(context_override.get("director_runtime_codegen_mode") or "").strip().lower()
    forced_tool_name = _forced_tool_choice_name(context_override.get("_transaction_kernel_forced_tool_choice"))
    is_forced_write_turn = forced_tool_name in {
        "append_to_file",
        "edit_blocks",
        "edit_file",
        "precision_edit",
        "repo_apply_diff",
        "write_file",
    }
    message_text = str(message or "")
    message_lower = message_text.lower()
    is_director_codegen_bridge = bool(context_override.get("director_runtime_codegen")) and (
        delivery_mode == "propose_patch" or codegen_mode == "proposal_then_apply"
    )
    is_single_batch_execution = (
        delivery_mode in {"materialize_changes", "propose_patch"}
        or tool_contract_requires_single_batch(context_override)
        or "materialization quality repair mode" in message_lower
        or "[director_quality_repair:" in message_lower
        or ("artifact quality scan failed" in message_lower and "do not read files first" in message_lower)
    )
    suppress_working_memory = bool(
        context_override.get("suppress_working_memory_contract")
        or context_override.get("_transaction_kernel_suppress_session_patch")
        or is_director_codegen_bridge
        or is_single_batch_execution
        or is_forced_write_turn
    )
    suppress_tool_policy = bool(context_override.get("suppress_tool_policy_prompt") or is_director_codegen_bridge)

    options: dict[str, bool] = {}
    if suppress_working_memory:
        options["include_working_memory_contract"] = False
    if suppress_tool_policy:
        options["include_tool_policy"] = False
    return options


def parse_content_and_thinking_tool_calls(
    kernel: RoleExecutionKernel,
    content: str,
    thinking: str | None,
    profile: Any,
    native_tool_calls: list[dict[str, Any]] | None,
    native_tool_provider: str,
) -> list[Any]:
    """Parse tool calls from content and thinking, filtering out thinking-only calls.

    Args:
        content: Raw text content from LLM
        thinking: Thinking content (may contain [TOOL_CALL]...[/TOOL_CALL] markers)
        profile: Role profile for allowed tool names
        native_tool_calls: Native tool calls from provider
        native_tool_provider: Provider hint for parsing

    Returns:
        List of parsed and filtered ToolCallResult objects
    """

    # Filter out tool calls that are only in thinking (not in main content)
    # by parsing only the main content (not thinking)
    result: list[ToolCallResult] = []
    seen: set[tuple[str, str]] = set()

    # Parse tool calls from main content and/or native_tool_calls
    # Note: native_tool_calls must be parsed even if content is empty
    # because LLM may emit tools via native protocol without content
    valid_parsed = kernel._get_output_parser().parse_tool_calls(
        content or "",  # Ensure content is never None
        native_tool_calls=native_tool_calls,
        native_provider=native_tool_provider,
    )
    for call in valid_parsed:
        key = (call.tool, str(call.args.get("path", "") or call.args.get("file", "")))
        if key not in seen:
            seen.add(key)
            result.append(call)

    return result
