"""重试编排器 — 突变合约违反后的恢复与重试逻辑。

包含:
- 模型覆盖解析
- retry 上下文构建
- bootstrap read 执行
- 主重试循环
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast

from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime, ToolExecutionContext
from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    build_context_target_bootstrap_decision,
    build_stale_edit_bootstrap_decision,
    extract_invocation_tool_name,
    is_mutation_contract_violation,
    is_safe_readonly_bootstrap_invocations,
    is_stale_edit_contract_violation,
    rollback_state_after_retry_batch_failure,
)
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    requires_mutation_intent,
    requires_verification_intent,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import (
    merge_batch_receipts,
    normalize_batch_receipts,
    record_receipts_to_ledger,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_allowed_tool_names_from_definitions,
    extract_latest_user_message,
    extract_tool_name_from_definition,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    RawLLMResponse,
    ToolBatch,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 模型覆盖与上下文构建
# ---------------------------------------------------------------------------


def resolve_retry_model_override(retry_llm_call_ordinal: int) -> str | None:
    """Resolve optional retry model override from environment.

    KERNELONE_TRANSACTION_KERNEL_RETRY_MODELS:
        Comma-separated model list used when retry LLM calls reach threshold.
    KERNELONE_TRANSACTION_KERNEL_RETRY_MODEL_START:
        1-based retry LLM call ordinal to start model override (default: 3).
    """
    if retry_llm_call_ordinal <= 0:
        return None
    raw_models = str(os.environ.get("KERNELONE_TRANSACTION_KERNEL_RETRY_MODELS", "") or "").strip()
    if not raw_models:
        return None
    candidates = [item.strip() for item in raw_models.split(",") if item and item.strip()]
    if not candidates:
        return None
    raw_start = str(os.environ.get("KERNELONE_TRANSACTION_KERNEL_RETRY_MODEL_START", "3") or "").strip()
    try:
        start_ordinal = max(1, int(raw_start))
    except ValueError:
        start_ordinal = 3
    if retry_llm_call_ordinal < start_ordinal:
        return None
    model_index = min(retry_llm_call_ordinal - start_ordinal, len(candidates) - 1)
    selected = candidates[model_index]
    return selected or None


def build_contract_retry_context(
    context: list[dict],
    tool_definitions: list[dict],
    *,
    forced_write_tool_name: str | None = None,
) -> list[dict]:
    """构建突变合约违反后的 retry 上下文。"""
    latest_user = extract_latest_user_message(context)
    target_file_tokens = [
        token.strip()
        for token in re.findall(
            r"\b[\w./\\-]+\.(?:py|md|txt|json|ya?ml|js|ts|tsx|jsx|css|html)\b",
            latest_user,
            flags=re.IGNORECASE,
        )
        if token.strip()
    ]
    write_candidates = set(WRITE_TOOLS)
    write_tools: list[str] = []
    for item in tool_definitions:
        if not isinstance(item, Mapping):
            continue
        function_payload = item.get("function")
        if isinstance(function_payload, Mapping):
            tool_name = str(function_payload.get("name") or "").strip()
        else:
            tool_name = str(item.get("name") or "").strip()
        if tool_name and tool_name in write_candidates and tool_name not in write_tools:
            write_tools.append(tool_name)

    retry_lines = [
        "RETRY CONTRACT: The previous tool batch was rejected because it did not include any write tool",
        "while the user explicitly requested code/file modification.",
        "You must replan now and emit ONE valid tool batch before finalization.",
        "HARD GATE: never return plain-text-only completion for a mutation request.",
    ]
    if write_tools:
        retry_lines.append("Allowed write tools in this turn: " + ", ".join(write_tools) + ".")
        retry_lines.append("Include at least one of the allowed write tools in the emitted batch.")
    if forced_write_tool_name:
        retry_lines.append(f"MANDATORY: your batch must include write tool `{forced_write_tool_name}`.")
        retry_lines.append("Do not output read/list-only batches before this mandatory write tool.")
    if target_file_tokens:
        retry_lines.append(
            "Mutation target files detected from user request: "
            + ", ".join(target_file_tokens[:6])
            + ". Ensure one write call touches at least one target file."
        )

    retry_mode_guard = (
        "RETRY MODE ACTIVE: discard any previous staged workflow (e.g., understand-first/read-first).\n"
        "Output a single valid TOOL_BATCH immediately under the constraints below.\n"
        "Do not emit plain-text-only response."
    )
    retry_context: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "\n".join([retry_mode_guard, *retry_lines]),
        }
    ]
    if latest_user:
        retry_context.append({"role": "user", "content": latest_user})
    else:
        for item in context:
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "")
            if role in {"system", "user"} and content:
                retry_context.append({"role": role, "content": content})
        if not retry_context:
            retry_context.append({"role": "user", "content": ""})
    return retry_context


def append_retry_enforcement_hint(
    retry_context: list[dict],
    *,
    allowed_tool_names: set[str],
    reason: str,
    forced_write_tool_name: str | None = None,
) -> list[dict]:
    """向 retry 上下文追加强制约束提示。"""
    rendered_allowed = ", ".join(sorted(allowed_tool_names)) if allowed_tool_names else "<none>"
    enforcement_hint = {
        "role": "system",
        "content": (
            "RETRY ENFORCEMENT: previous retry output is still invalid.\n"
            f"Reason: {reason}\n"
            f"Allowed tools for this retry scope: {rendered_allowed}\n"
            "You MUST emit one TOOL_BATCH that uses only allowed tools, and includes at least one write tool.\n"
            "INVALID retry outputs: read_file-only, list_directory-only, execute_command-only.\n"
            "VALID retry output must include a write tool call.\n"
            + (f"MANDATORY write tool for this retry: {forced_write_tool_name}." if forced_write_tool_name else "")
        ),
    }
    return [*retry_context, enforcement_hint]


# ---------------------------------------------------------------------------
# 工具定义筛选
# ---------------------------------------------------------------------------


def build_retry_tool_definitions_for_mutation(
    *,
    latest_user_request: str,
    tool_definitions: list[dict],
    requires_mutation: bool | None = None,
    forbidden_tool_names: set[str] | None = None,
) -> list[dict]:
    """Builds narrowed tool definitions for a mutation-contract retry.

    Critically, this function respects a ``forbidden_tool_names`` set so
    that benchmark-level or case-level forbidden tools (e.g. execute_command)
    are never smuggled back in during retry escalation.
    """
    if requires_mutation is None:
        requires_mutation = requires_mutation_intent(latest_user_request)
    if not requires_mutation:
        return list(tool_definitions)

    _forbidden: set[str] = forbidden_tool_names or set()

    write_candidates = set(WRITE_TOOLS)
    read_context_candidates = {
        "read_file",
        "list_directory",
        "repo_rg",
        "repo_glob",
    }
    # Only add verification tools that are NOT forbidden by the benchmark case.
    verification_candidates = {t for t in {"execute_command"} if t not in _forbidden}
    narrowed: list[dict] = []
    has_write = False
    selected_tool_names: set[str] = set()
    for raw_item in tool_definitions:
        if not isinstance(raw_item, Mapping):
            continue
        item = dict(raw_item)
        tool_name = extract_tool_name_from_definition(item)
        # Never include globally forbidden tools.
        if tool_name in _forbidden:
            continue
        if tool_name in write_candidates:
            narrowed.append(item)
            has_write = True
            selected_tool_names.add(tool_name)
    if has_write:
        for raw_item in tool_definitions:
            if not isinstance(raw_item, Mapping):
                continue
            item = dict(raw_item)
            tool_name = extract_tool_name_from_definition(item)
            if tool_name in _forbidden:
                continue
            if tool_name and tool_name in read_context_candidates and tool_name not in selected_tool_names:
                narrowed.append(item)
                selected_tool_names.add(tool_name)
        for raw_item in tool_definitions:
            if not isinstance(raw_item, Mapping):
                continue
            item = dict(raw_item)
            tool_name = extract_tool_name_from_definition(item)
            if tool_name in _forbidden:
                continue
            if tool_name and tool_name in verification_candidates and tool_name not in selected_tool_names:
                narrowed.append(item)
                selected_tool_names.add(tool_name)
    if has_write and narrowed:
        return narrowed
    return [item for item in tool_definitions if extract_tool_name_from_definition(item) not in _forbidden]


def select_retry_forced_write_tool_name(tool_definitions: list[dict]) -> str | None:
    # BUG-NEW-2 fix: write_file was ranked first, causing destructive full-file
    # overwrites when the task only requires appending or editing a few lines.
    # Reordered so that safe incremental tools are tried first:
    #   append_to_file  — appends without touching existing content (safest)
    #   precision_edit  — targeted line/block edits
    #   edit_file       — structured file editing
    #   search_replace  — targeted search-and-replace
    #   repo_apply_diff — diff-based patching
    #   edit_blocks     — block-level edits
    #   write_file      — OVERWRITES entire file (last resort, destructive!)
    #   create_file     — only for brand-new files
    priority_order = (
        "append_to_file",
        "precision_edit",
        "edit_file",
        "search_replace",
        "repo_apply_diff",
        "edit_blocks",
        "write_file",
        "create_file",
    )
    available = extract_allowed_tool_names_from_definitions(tool_definitions)
    for tool_name in priority_order:
        if tool_name in available:
            return tool_name
    return None


def build_forced_write_only_retry_tool_definitions(
    tool_definitions: list[dict],
    forced_write_tool_name: str | None,
    *,
    include_verification_tools: bool = False,
    forbidden_tool_names: set[str] | None = None,
) -> list[dict]:
    """Builds the strict forced-write tool definitions.

    Respects ``forbidden_tool_names`` so that benchmark-forbidden tools
    (e.g. execute_command) are never included even when verification is enabled.
    """
    _forbidden: set[str] = forbidden_tool_names or set()
    if not forced_write_tool_name:
        return [item for item in tool_definitions if extract_tool_name_from_definition(item) not in _forbidden]
    companion_tool_names: set[str] = {forced_write_tool_name}
    if forced_write_tool_name in {"repo_apply_diff"}:
        companion_tool_names.update({"read_file", "repo_read_head"})
    if include_verification_tools and "execute_command" not in _forbidden:
        companion_tool_names.add("execute_command")
    narrowed: list[dict] = []
    for raw_item in tool_definitions:
        if not isinstance(raw_item, Mapping):
            continue
        item = dict(raw_item)
        tool_name = extract_tool_name_from_definition(item)
        if tool_name in _forbidden:
            continue
        if tool_name in companion_tool_names:
            narrowed.append(item)
    if narrowed:
        return narrowed
    return [item for item in tool_definitions if extract_tool_name_from_definition(item) not in _forbidden]


# ---------------------------------------------------------------------------
# Bootstrap 上下文构建
# ---------------------------------------------------------------------------


def build_retry_write_after_bootstrap_context(
    *,
    original_context: list[dict],
    bootstrap_receipt: Mapping[str, Any],
    forced_write_tool_name: str | None,
) -> list[dict]:
    latest_user = extract_latest_user_message(original_context)
    summary_lines: list[str] = []
    successful_files: list[str] = []
    failed_files: list[str] = []
    for item in list(bootstrap_receipt.get("results", []) or []):
        if not isinstance(item, Mapping):
            continue
        tool_name = str(item.get("tool_name") or "unknown").strip()
        status = str(item.get("status") or "").strip().lower()
        payload = item.get("result")
        if isinstance(payload, Mapping):
            try:
                payload_text = json.dumps(dict(payload), ensure_ascii=False)
            except (TypeError, ValueError):
                payload_text = str(payload)
        else:
            payload_text = str(payload or "")
        payload_text = payload_text.strip()
        if len(payload_text) > 1200:
            payload_text = payload_text[:1200] + " ...[truncated]"
        resolved_file = ""
        if isinstance(payload, Mapping):
            resolved_file = str(payload.get("file") or payload.get("path") or "").strip()
        if not resolved_file:
            from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
                extract_target_file_from_invocation_args,
            )

            resolved_file = extract_target_file_from_invocation_args({"arguments": item.get("arguments")})
        if status == "success":
            summary_lines.append(f"- {tool_name}: {payload_text}")
            if resolved_file and resolved_file not in successful_files:
                successful_files.append(resolved_file)
        else:
            summary_lines.append(f"- {tool_name}: ERROR {payload_text}")
            if resolved_file and resolved_file not in failed_files:
                failed_files.append(resolved_file)

    forced_line = (
        f"Mandatory write tool: {forced_write_tool_name}."
        if forced_write_tool_name
        else "Mandatory: include at least one write tool."
    )
    summary_block = "\n".join(summary_lines) if summary_lines else "- (no readable bootstrap receipts)"
    retry_system = (
        "WRITE RETRY MODE: bootstrap read context has been collected.\n"
        "Now emit exactly one TOOL_BATCH for implementation (write stage), no extra read-only exploration.\n"
        f"{forced_line}\n"
        "If you cannot determine exact patch, still emit a write-tool call with best-effort scoped edit arguments.\n"
        "Bootstrap read summary:\n"
        f"{summary_block}"
    )
    if successful_files:
        retry_system += (
            "\nWrite targets must be selected from successfully-read files only: " + ", ".join(successful_files) + "."
        )
    if failed_files:
        retry_system += "\nDo NOT edit unresolved paths (read failed): " + ", ".join(failed_files) + "."
        retry_system += "\nFor unresolved files that must be newly created, use write_file/create_file/append_to_file instead of edit_file."
    return [
        {"role": "system", "content": retry_system},
        {"role": "user", "content": latest_user},
    ]


def extract_failed_files_from_bootstrap_receipt(bootstrap_receipt: Mapping[str, Any]) -> list[str]:
    failed_files: list[str] = []
    for item in list(bootstrap_receipt.get("results", []) or []):
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status == "success":
            continue
        payload = item.get("result")
        resolved_file = ""
        if isinstance(payload, Mapping):
            resolved_file = str(payload.get("file") or payload.get("path") or "").strip()
        if not resolved_file:
            from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
                extract_target_file_from_invocation_args,
            )

            resolved_file = extract_target_file_from_invocation_args({"arguments": item.get("arguments")})
        if not resolved_file:
            error_text = str(item.get("error") or payload or "").strip()
            match = re.search(r"file not found:\s*([^\s|]+)", error_text, flags=re.IGNORECASE)
            if match:
                resolved_file = str(match.group(1) or "").strip()
        if resolved_file and resolved_file not in failed_files:
            failed_files.append(resolved_file)
    return failed_files


# ---------------------------------------------------------------------------
# RetryOrchestrator
# ---------------------------------------------------------------------------


class WorkflowRuntimeProtocol(Protocol):
    """工作流运行时协议。"""

    async def execute(self, decision: TurnDecision, turn_id: TurnId) -> dict[str, Any]:
        """执行决策并返回结果。"""
        ...


class DevelopmentRuntimeProtocol(Protocol):
    """开发运行时协议。"""

    async def execute(self, decision: TurnDecision, turn_id: TurnId) -> dict[str, Any]:
        """执行决策并返回结果。"""
        ...


class RetryOrchestrator:
    """重试编排器 — 突变合约违反后的恢复与重试。"""

    def __init__(
        self,
        *,
        tool_runtime: Any,
        config: Any,
        decoder: Any,
        call_llm_for_decision: Callable[..., Any],
        call_llm_for_decision_stream: Callable[..., Any] | None,
        execute_tool_batch: Callable[..., Any],
        guard_assert_single_tool_batch: Callable[..., None],
        emit_event: Callable[[Any], None] | None = None,
    ) -> None:
        self.tool_runtime = tool_runtime
        self.config = config
        self.decoder = decoder
        self.call_llm_for_decision = call_llm_for_decision
        self.call_llm_for_decision_stream = call_llm_for_decision_stream
        self.execute_tool_batch = execute_tool_batch
        self.guard_assert_single_tool_batch = guard_assert_single_tool_batch
        self.emit_event = emit_event

    def _build_tool_batch_runtime(self, workspace: str = ".") -> ToolBatchRuntime:
        return ToolBatchRuntime(
            executor=self.tool_runtime,
            context=ToolExecutionContext(
                workspace=workspace or ".",
                timeout_ms=self.config.max_tool_execution_time_ms,
            ),
        )

    async def execute_read_bootstrap_batch(
        self,
        *,
        turn_id: str,
        workspace: str,
        tool_batch: Any,
        ledger: TurnLedger,
    ) -> dict[str, Any] | None:
        if isinstance(tool_batch, Mapping):
            raw_invocations = list(tool_batch.get("invocations", []) or [])
            batch_id = tool_batch.get("batch_id", BatchId(f"{turn_id}_bootstrap"))
        else:
            raw_invocations = list(getattr(tool_batch, "invocations", []) or [])
            batch_id = getattr(tool_batch, "batch_id", BatchId(f"{turn_id}_bootstrap"))
        normalized_invocations: list[ToolInvocation] = []
        for raw_invocation in raw_invocations:
            if isinstance(raw_invocation, Mapping):
                item = dict(raw_invocation)
            else:
                raw_args = getattr(raw_invocation, "arguments", None)
                args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
                item = {
                    "call_id": str(getattr(raw_invocation, "call_id", "") or ""),
                    "tool_name": str(getattr(raw_invocation, "tool_name", "") or ""),
                    "arguments": args,
                    "execution_mode": getattr(raw_invocation, "execution_mode", None),
                }
            if not str(item.get("tool_name") or "").strip():
                continue
            if not item.get("execution_mode"):
                item["execution_mode"] = ToolExecutionMode.READONLY_SERIAL
            if not item.get("effect_type"):
                item["effect_type"] = ToolEffectType.READ
            normalized_invocations.append(cast("ToolInvocation", item))
        if not normalized_invocations:
            return None

        bootstrap_batch = ToolBatch(
            batch_id=batch_id,
            parallel_readonly=[
                inv
                for inv in normalized_invocations
                if inv.get("execution_mode") == ToolExecutionMode.READONLY_PARALLEL
            ],
            readonly_serial=[
                inv for inv in normalized_invocations if inv.get("execution_mode") == ToolExecutionMode.READONLY_SERIAL
            ],
            serial_writes=[
                inv for inv in normalized_invocations if inv.get("execution_mode") == ToolExecutionMode.WRITE_SERIAL
            ],
            async_receipts=[
                inv for inv in normalized_invocations if inv.get("execution_mode") == ToolExecutionMode.ASYNC_RECEIPT
            ],
        )
        receipts = await self._build_tool_batch_runtime(workspace).execute_batch(
            bootstrap_batch,
            TurnId(turn_id),
        )
        receipts_as_dicts = normalize_batch_receipts(receipts)
        record_receipts_to_ledger(receipts_as_dicts, ledger)
        if not receipts_as_dicts:
            return None
        merged_receipt = merge_batch_receipts(receipts_as_dicts)
        if merged_receipt is None:
            return None
        merged_receipt["batch_id"] = str(batch_id)
        merged_receipt["turn_id"] = turn_id
        return merged_receipt

    def _build_retry_context(
        self,
        *,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        requires_verification: bool,
        requires_mutation: bool,
        forbidden_tool_names: set[str] | None = None,
    ) -> tuple[list[dict], list[dict], set[str], set[str], str | None, dict[str, Any] | None, list[dict]]:
        """构建重试上下文和工具定义。

        ``forbidden_tool_names`` is threaded through all narrowing helpers so
        that benchmark-level or case-level forbidden tools are never included
        in any retry tool set, even during write-escalation.
        """
        _forbidden = forbidden_tool_names or set()
        retry_tool_definitions = build_retry_tool_definitions_for_mutation(
            latest_user_request=extract_latest_user_message(context),
            tool_definitions=tool_definitions,
            requires_mutation=requires_mutation,
            forbidden_tool_names=_forbidden,
        )
        allowed_retry_tool_names = extract_allowed_tool_names_from_definitions(retry_tool_definitions)
        forced_write_tool_name = select_retry_forced_write_tool_name(retry_tool_definitions)
        _strict_retry_tool_definitions = build_forced_write_only_retry_tool_definitions(
            retry_tool_definitions,
            forced_write_tool_name,
            include_verification_tools=requires_verification,
            forbidden_tool_names=_forbidden,
        )
        strict_allowed_retry_tool_names = extract_allowed_tool_names_from_definitions(_strict_retry_tool_definitions)
        forced_tool_choice: dict[str, Any] | None = None
        if forced_write_tool_name:
            forced_tool_choice = {
                "type": "function",
                "function": {"name": forced_write_tool_name},
            }
        retry_context = build_contract_retry_context(
            context,
            retry_tool_definitions,
            forced_write_tool_name=forced_write_tool_name,
        )
        logger.warning(
            "mutation-contract retry scope: turn_id=%s allowed_tools=%s strict_allowed_tools=%s forced_tool=%s",
            turn_id,
            sorted(allowed_retry_tool_names),
            sorted(strict_allowed_retry_tool_names),
            forced_write_tool_name,
        )
        return (
            retry_tool_definitions,
            retry_context,
            allowed_retry_tool_names,
            strict_allowed_retry_tool_names,
            forced_write_tool_name,
            forced_tool_choice,
            _strict_retry_tool_definitions,
        )

    async def _execute_retry_batch(
        self,
        *,
        turn_id: str,
        attempt_context: list[dict],
        attempt_tool_definitions: list[dict],
        ledger: TurnLedger,
        attempt_tool_choice_override: dict[str, Any] | None,
        attempt_model_override: str | None,
        stream: bool,
        shadow_engine: Any | None,
    ) -> RawLLMResponse:
        """执行单个重试批次，返回 LLM 响应。"""
        retry_response: RawLLMResponse | None = None
        stream_callable = self.call_llm_for_decision_stream
        use_stream_retry = stream and stream_callable is not None
        if use_stream_retry and stream_callable is not None:
            try:
                async for retry_event in stream_callable(
                    attempt_context,
                    attempt_tool_definitions,
                    ledger,
                    shadow_engine=shadow_engine,
                    tool_choice_override=attempt_tool_choice_override,
                    model_override=attempt_model_override,
                ):
                    if not isinstance(retry_event, Mapping):
                        continue
                    event_type = str(retry_event.get("type") or "").strip()
                    if event_type == "_internal_materialize":
                        candidate_response = retry_event.get("response")
                        if isinstance(candidate_response, RawLLMResponse):
                            retry_response = candidate_response
                    elif self.emit_event is not None and event_type:
                        self.emit_event(retry_event)
            except Exception as stream_exc:
                logger.exception("retry stream failed: turn_id=%s", turn_id)
                raise RuntimeError(
                    f"single_batch_contract_violation_retry_failed: retry stream error: {stream_exc}"
                ) from stream_exc
            if retry_response is None:
                raise RuntimeError(
                    "single_batch_contract_violation_retry_failed: retry stream did not materialize response"
                )
        else:
            retry_response = await self.call_llm_for_decision(
                attempt_context,
                attempt_tool_definitions,
                ledger,
                tool_choice_override=attempt_tool_choice_override,
                model_override=attempt_model_override,
            )
        return retry_response

    async def retry_tool_batch_after_contract_violation(
        self,
        *,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        stream: bool,
        shadow_engine: Any | None = None,
    ) -> dict:
        latest_user_request = extract_latest_user_message(context)
        requires_verification = requires_verification_intent(latest_user_request)
        requires_mutation = requires_mutation_intent(latest_user_request)
        (
            retry_tool_definitions,
            retry_context,
            allowed_retry_tool_names,
            strict_allowed_retry_tool_names,
            forced_write_tool_name,
            forced_tool_choice,
            strict_retry_tool_defs,
        ) = self._build_retry_context(
            turn_id=turn_id,
            context=context,
            tool_definitions=tool_definitions,
            requires_verification=requires_verification,
            requires_mutation=requires_mutation,
        )
        max_retry_attempts = getattr(self.config, "max_retry_attempts", 4)
        retry_llm_call_ordinal = 0
        candidate_bootstrap_decision: TurnDecision | None = None
        for attempt_index in range(max_retry_attempts):
            attempt_tool_definitions = retry_tool_definitions
            attempt_allowed_tool_names = allowed_retry_tool_names
            attempt_context = retry_context
            attempt_tool_choice_override: Any | None = forced_tool_choice
            retry_llm_call_ordinal += 1
            attempt_model_override = resolve_retry_model_override(retry_llm_call_ordinal)
            if attempt_index > 0 and forced_write_tool_name and strict_retry_tool_defs:
                attempt_tool_definitions = strict_retry_tool_defs
                attempt_allowed_tool_names = strict_allowed_retry_tool_names
            if attempt_index > 0 and forced_write_tool_name:
                attempt_context = append_retry_enforcement_hint(
                    retry_context,
                    allowed_tool_names=attempt_allowed_tool_names,
                    reason="escalation: enforce write-inclusive batch in retry scope",
                    forced_write_tool_name=forced_write_tool_name,
                )

            retry_response = await self._execute_retry_batch(
                turn_id=turn_id,
                attempt_context=attempt_context,
                attempt_tool_definitions=attempt_tool_definitions,
                ledger=ledger,
                attempt_tool_choice_override=attempt_tool_choice_override,
                attempt_model_override=attempt_model_override,
                stream=stream,
                shadow_engine=shadow_engine,
            )
            if attempt_model_override:
                logger.warning(
                    "mutation-contract retry attempt=%s uses model override: %s",
                    attempt_index + 1,
                    attempt_model_override,
                )

            raw_native_names: list[str] = []
            for native_call in retry_response.native_tool_calls:
                if not isinstance(native_call, Mapping):
                    continue
                function_payload = native_call.get("function")
                if not isinstance(function_payload, Mapping):
                    continue
                native_name = str(function_payload.get("name") or "").strip()
                if native_name:
                    raw_native_names.append(native_name)
            logger.warning(
                "mutation-contract retry attempt=%s raw_native_tools=%s",
                attempt_index + 1,
                raw_native_names,
            )

            retry_decision = self.decoder.decode(retry_response, TurnId(turn_id))
            ledger.replace_decision(retry_decision)
            if retry_decision.get("kind") != TurnDecisionKind.TOOL_BATCH:
                if attempt_index < max_retry_attempts - 1:
                    retry_context = append_retry_enforcement_hint(
                        retry_context,
                        allowed_tool_names=attempt_allowed_tool_names,
                        reason="retry decision did not produce a valid tool batch",
                        forced_write_tool_name=forced_write_tool_name,
                    )
                    continue
                raise RuntimeError(
                    "single_batch_contract_violation_retry_failed: retry decision did not produce a valid tool batch"
                )
            _batch_count_before = ledger.tool_batch_count
            try:
                attempt_result = await self.execute_tool_batch(
                    retry_decision,
                    state_machine,
                    ledger,
                    attempt_context,
                    stream=stream,
                    shadow_engine=shadow_engine,
                    allowed_tool_names=attempt_allowed_tool_names if attempt_allowed_tool_names else None,
                    count_towards_batch_limit=True,
                )
                self.guard_assert_single_tool_batch(
                    turn_id=turn_id,
                    tool_batch_count=ledger.tool_batch_count,
                    ledger=ledger,
                )
                return attempt_result
            except RuntimeError as retry_exc:
                # FIX-20260504: rollback batch count so failed attempts don't
                # accumulate and cause assert_single_tool_batch to fail on retries.
                ledger.tool_batch_count = _batch_count_before
                retry_tool_batch = retry_decision.get("tool_batch")
                if isinstance(retry_tool_batch, Mapping):
                    retry_invocations = list(retry_tool_batch.get("invocations", []))
                elif hasattr(retry_tool_batch, "invocations"):
                    retry_invocations = list(getattr(retry_tool_batch, "invocations", []) or [])
                else:
                    retry_invocations = []
                retry_tool_names: list[str] = []
                for invocation in retry_invocations:
                    tool_name = extract_invocation_tool_name(invocation)
                    if tool_name:
                        retry_tool_names.append(tool_name)
                logger.warning(
                    "mutation-contract retry attempt=%s failed: %s (decision_tools=%s)",
                    attempt_index + 1,
                    str(retry_exc),
                    retry_tool_names,
                )
                rollback_state_after_retry_batch_failure(state_machine, ledger)
                if is_stale_edit_contract_violation(retry_exc):
                    bootstrap_from_write = build_stale_edit_bootstrap_decision(
                        turn_id=turn_id,
                        retry_invocations=retry_invocations,
                        decision_metadata=retry_decision.get("metadata"),
                    )
                    bootstrap_from_context = None
                    if bootstrap_from_write is None:
                        bootstrap_from_context = build_context_target_bootstrap_decision(
                            turn_id=turn_id,
                            latest_user_request=extract_latest_user_message(retry_context),
                            decision_metadata=retry_decision.get("metadata"),
                        )
                    bootstrap_decision = bootstrap_from_write or bootstrap_from_context
                    if bootstrap_decision is not None:
                        logger.warning(
                            "mutation-contract retry attempt=%s switching to bootstrap read path",
                            attempt_index + 1,
                        )
                        candidate_bootstrap_decision = bootstrap_decision
                        break
                if not is_mutation_contract_violation(retry_exc):
                    raise
                if attempt_index >= max_retry_attempts - 1:
                    if is_safe_readonly_bootstrap_invocations(retry_invocations):
                        candidate_bootstrap_decision = retry_decision
                        break
                    raise
                retry_context = append_retry_enforcement_hint(
                    retry_context,
                    allowed_tool_names=attempt_allowed_tool_names,
                    reason=f"{retry_exc!s} (attempt {attempt_index + 1}/{max_retry_attempts})",
                    forced_write_tool_name=forced_write_tool_name,
                )
                continue

        if candidate_bootstrap_decision is not None:
            bootstrap_tool_batch = candidate_bootstrap_decision.get("tool_batch")
            if bootstrap_tool_batch is None:
                raise RuntimeError("single_batch_contract_violation_retry_failed: bootstrap tool batch missing")
            bootstrap_metadata = candidate_bootstrap_decision.get("metadata")
            bootstrap_workspace = "."
            if isinstance(bootstrap_metadata, Mapping):
                bootstrap_workspace = str(bootstrap_metadata.get("workspace", ".")).strip() or "."
            bootstrap_receipt = await self.execute_read_bootstrap_batch(
                turn_id=turn_id,
                workspace=bootstrap_workspace,
                tool_batch=bootstrap_tool_batch,
                ledger=ledger,
            )
            if bootstrap_receipt is None:
                raise RuntimeError("single_batch_contract_violation_retry_failed: bootstrap read receipt missing")
            failed_bootstrap_files = extract_failed_files_from_bootstrap_receipt(bootstrap_receipt)
            followup_forced_write_tool_name = forced_write_tool_name
            if failed_bootstrap_files:
                for creation_candidate in ("write_file", "create_file", "append_to_file"):
                    if creation_candidate in allowed_retry_tool_names:
                        followup_forced_write_tool_name = creation_candidate
                        break
            write_context = build_retry_write_after_bootstrap_context(
                original_context=context,
                bootstrap_receipt=bootstrap_receipt,
                forced_write_tool_name=followup_forced_write_tool_name,
            )
            if followup_forced_write_tool_name != forced_write_tool_name:
                logger.warning(
                    "mutation-contract bootstrap-followup adjusted forced write tool: %s -> %s (failed_files=%s)",
                    forced_write_tool_name,
                    followup_forced_write_tool_name,
                    failed_bootstrap_files,
                )
            followup_forced_tool_choice: Any | None = (
                {
                    "type": "function",
                    "function": {"name": followup_forced_write_tool_name},
                }
                if followup_forced_write_tool_name
                else None
            )
            if followup_forced_write_tool_name:
                followup_tool_definitions = build_forced_write_only_retry_tool_definitions(
                    retry_tool_definitions,
                    followup_forced_write_tool_name,
                    include_verification_tools=requires_verification,
                )
                followup_allowed_tool_names = extract_allowed_tool_names_from_definitions(followup_tool_definitions)
            else:
                followup_tool_definitions = retry_tool_definitions
                followup_allowed_tool_names = allowed_retry_tool_names
            followup_tool_choice_override: Any | None = followup_forced_tool_choice or forced_tool_choice
            max_followup_attempts = 3
            current_write_context = write_context
            current_followup_allowed_tool_names = set(followup_allowed_tool_names)
            for followup_attempt in range(max_followup_attempts):
                followup_response: RawLLMResponse | None = None
                retry_llm_call_ordinal += 1
                followup_model_override = resolve_retry_model_override(retry_llm_call_ordinal)
                if stream and self.call_llm_for_decision_stream is not None:
                    try:
                        async for retry_event in self.call_llm_for_decision_stream(
                            current_write_context,
                            followup_tool_definitions,
                            ledger,
                            shadow_engine=shadow_engine,
                            tool_choice_override=followup_tool_choice_override,
                            model_override=followup_model_override,
                        ):
                            if not isinstance(retry_event, Mapping):
                                continue
                            event_type = str(retry_event.get("type") or "").strip()
                            if event_type == "_internal_materialize":
                                candidate_response = retry_event.get("response")
                                if isinstance(candidate_response, RawLLMResponse):
                                    followup_response = candidate_response
                            elif self.emit_event is not None and event_type:
                                self.emit_event(retry_event)
                    except Exception as stream_exc:
                        logger.exception("bootstrap follow-up stream failed: turn_id=%s", turn_id)
                        raise RuntimeError(
                            f"single_batch_contract_violation_retry_failed: bootstrap follow-up stream error: {stream_exc}"
                        ) from stream_exc
                else:
                    followup_response = await self.call_llm_for_decision(
                        current_write_context,
                        followup_tool_definitions,
                        ledger,
                        tool_choice_override=followup_tool_choice_override,
                        model_override=followup_model_override,
                    )
                if followup_model_override:
                    logger.warning(
                        "mutation-contract bootstrap-followup attempt=%s uses model override: %s",
                        followup_attempt + 1,
                        followup_model_override,
                    )
                if followup_response is None:
                    raise RuntimeError(
                        "single_batch_contract_violation_retry_failed: bootstrap follow-up did not materialize response"
                    )
                followup_decision = self.decoder.decode(followup_response, TurnId(turn_id))
                ledger.replace_decision(followup_decision)
                if followup_decision.get("kind") != TurnDecisionKind.TOOL_BATCH:
                    raise RuntimeError(
                        "single_batch_contract_violation_retry_failed: bootstrap follow-up did not produce tool batch"
                    )
                _batch_count_before = ledger.tool_batch_count
                try:
                    followup_result = await self.execute_tool_batch(
                        followup_decision,
                        state_machine,
                        ledger,
                        current_write_context,
                        stream=stream,
                        shadow_engine=shadow_engine,
                        allowed_tool_names=(
                            current_followup_allowed_tool_names if current_followup_allowed_tool_names else None
                        ),
                        count_towards_batch_limit=True,
                    )
                    self.guard_assert_single_tool_batch(
                        turn_id=turn_id,
                        tool_batch_count=ledger.tool_batch_count,
                        ledger=ledger,
                    )
                    return followup_result
                except RuntimeError as followup_exc:
                    # FIX-20260504: rollback batch count so failed attempts don't
                    # accumulate and cause assert_single_tool_batch to fail.
                    ledger.tool_batch_count = _batch_count_before
                    rollback_state_after_retry_batch_failure(state_machine, ledger)
                    if (not is_stale_edit_contract_violation(followup_exc)) or (
                        followup_attempt >= max_followup_attempts - 1
                    ):
                        if is_mutation_contract_violation(followup_exc) and followup_attempt < (
                            max_followup_attempts - 1
                        ):
                            followup_error_text = str(followup_exc).lower()
                            if "outside narrowed set" in followup_error_text and allowed_retry_tool_names:
                                current_followup_allowed_tool_names = set(allowed_retry_tool_names)
                            current_write_context = append_retry_enforcement_hint(
                                current_write_context,
                                allowed_tool_names=(
                                    current_followup_allowed_tool_names
                                    if current_followup_allowed_tool_names
                                    else allowed_retry_tool_names
                                ),
                                reason=f"{followup_exc!s} (bootstrap follow-up {followup_attempt + 1}/{max_followup_attempts})",
                                forced_write_tool_name=followup_forced_write_tool_name,
                            )
                            continue
                        raise
                    followup_tool_batch = followup_decision.get("tool_batch")
                    if isinstance(followup_tool_batch, Mapping):
                        followup_invocations = list(followup_tool_batch.get("invocations", []) or [])
                    elif hasattr(followup_tool_batch, "invocations"):
                        followup_invocations = list(getattr(followup_tool_batch, "invocations", []) or [])
                    else:
                        followup_invocations = []
                    next_bootstrap_decision = build_stale_edit_bootstrap_decision(
                        turn_id=turn_id,
                        retry_invocations=followup_invocations,
                        decision_metadata=followup_decision.get("metadata"),
                    )
                    if next_bootstrap_decision is None:
                        raise
                    next_bootstrap_metadata = next_bootstrap_decision.get("metadata")
                    next_bootstrap_workspace = "."
                    if isinstance(next_bootstrap_metadata, Mapping):
                        next_bootstrap_workspace = str(next_bootstrap_metadata.get("workspace", ".")).strip() or "."
                    next_bootstrap_receipt = await self.execute_read_bootstrap_batch(
                        turn_id=turn_id,
                        workspace=next_bootstrap_workspace,
                        tool_batch=next_bootstrap_decision.get("tool_batch"),
                        ledger=ledger,
                    )
                    if next_bootstrap_receipt is None:
                        raise RuntimeError(
                            "single_batch_contract_violation_retry_failed: follow-up stale bootstrap read receipt missing"
                        ) from followup_exc
                    current_write_context = build_retry_write_after_bootstrap_context(
                        original_context=context,
                        bootstrap_receipt=next_bootstrap_receipt,
                        forced_write_tool_name=followup_forced_write_tool_name,
                    )
                    continue

        raise RuntimeError("single_batch_contract_violation_retry_failed: retry attempts exhausted")
