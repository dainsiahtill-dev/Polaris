"""重试编排器 — 突变合约违反后的恢复与重试逻辑。

包含:
- 模型覆盖解析
- retry 上下文构建
- bootstrap read 执行
- 主重试循环

本文件保持为 ``transaction`` 的规范入口（canonical）：``RetryOrchestrator``
与两个 Protocol 仍物理定义在此处。重试策略 / read-loop 界 / 工具定义筛选 /
上下文构建 / bootstrap follow-up 五块已无损拆分到同包子模块，并在此 re-export
（包括测试依赖的私有 ``_``-前缀符号与常量），原导入路径保持不变。

CRITICAL: ``_READ_BOOTSTRAP_PROGRESS`` 这一唯一可变模块级缓存（the stall
ceiling，live-incident-critical）由 ``read_bootstrap_progress`` 独家持有；此处
re-export 的是同一实例引用，绝不复制。
"""

from __future__ import annotations

import json  # noqa: F401  (lossless re-export: top-level name at original path)
import logging
import os  # noqa: F401  (lossless re-export: top-level name at original path)
import re  # noqa: F401  (lossless re-export: top-level name at original path)
from collections.abc import Callable, Mapping
from pathlib import Path  # noqa: F401  (lossless re-export: top-level name at original path)
from typing import Any, Protocol, cast

from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime, ToolExecutionContext
from polaris.cells.roles.kernel.internal.transaction.bootstrap_followup import (
    _DEFAULT_LEAF_BOOTSTRAP_WRITE_FILE_MAX_CHARS,
    _LEAF_BOOTSTRAP_WRITE_FILE_EXTS,
    _LEAF_BOOTSTRAP_WRITE_FILE_MAX_CHARS_ENV,
    _bootstrap_successful_file_contents,
    _extract_decision_invocations,
    _extract_declared_step_card,
    _extract_deterministic_bootstrap_write_targets,
    _normalize_deterministic_bootstrap_target,
    _read_leaf_write_file_max_chars,
    _should_force_leaf_bootstrap_followup_write_file,
    _synthesize_deterministic_bootstrap_write_content,
    _synthesize_deterministic_dag_service_content,
    build_deterministic_bootstrap_followup_write_decision,
    merge_bootstrap_receipt_into_result,
)
from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    build_context_target_bootstrap_decision,
    build_stale_edit_bootstrap_decision,
    extract_invocation_tool_name,
    extract_target_files_from_message,
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
from polaris.cells.roles.kernel.internal.transaction.read_bootstrap_progress import (
    _FINGERPRINT_SKIP_DIRS,
    _FINGERPRINT_SOURCE_EXTS,
    _MAX_STALLED_READ_BOOTSTRAPS,
    _READ_BOOTSTRAP_PROGRESS,
    _READ_BOOTSTRAP_PROGRESS_MAX_KEYS,
    _WRITE_ONLY_SINGLE_TARGET_REPAIR_MARKER,
    _clear_read_bootstrap_progress,
    _read_bootstrap_makes_no_progress,
    _requires_write_only_single_target_repair,
    _resolve_materialization_workspace,
    _should_bootstrap_original_read_batch,
    _workspace_materialization_fingerprint,
)
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import (
    merge_batch_receipts,
    normalize_batch_receipts,
    record_receipts_to_ledger,
)
from polaris.cells.roles.kernel.internal.transaction.retry_context_builders import (
    _BOOTSTRAP_READ_MAX_CHARS_ENV,
    _BOOTSTRAP_READ_TOTAL_CHARS_ENV,
    _DEFAULT_BOOTSTRAP_READ_CONTENT_MAX_CHARS,
    _DEFAULT_BOOTSTRAP_READ_CONTENT_TOTAL_CHARS,
    _bootstrap_read_content_max_chars,
    _bootstrap_read_content_total_chars,
    _extract_latest_assistant_message,
    _read_positive_int_env,
    append_retry_enforcement_hint,
    build_contract_retry_context,
    build_retry_write_after_bootstrap_context,
    extract_failed_files_from_bootstrap_receipt,
)
from polaris.cells.roles.kernel.internal.transaction.retry_escalation_policy import (
    _DEFAULT_RETRY_CREATE_OUTPUT_FLOOR_TOKENS,
    _DEFAULT_RETRY_ESCALATION_TEMPERATURE,
    _DEFAULT_RETRY_OUTPUT_FLOOR_TOKENS,
    _ESCALATION_START_ATTEMPT_INDEX,
    _LINE_RANGE_EDIT_BLOCKS_PARAMETERS,
    _RETRY_CREATE_OUTPUT_FLOOR_ENV,
    _RETRY_ESCALATION_TEMPERATURE_ENV,
    _RETRY_OUTPUT_FLOOR_ENV,
    narrow_edit_blocks_schema_to_line_range,
    resolve_escalation_temperature,
    resolve_retry_create_output_floor,
    resolve_retry_escalation,
    resolve_retry_model_override,
    resolve_retry_output_floor,
    resolve_retry_temperature_override,
)
from polaris.cells.roles.kernel.internal.transaction.retry_tool_definitions import (
    _BOOTSTRAP_WHOLE_FILE_EDIT_ERROR_FRAGMENTS,
    _BOOTSTRAP_WHOLE_FILE_EDIT_ERROR_TYPES,
    _BOOTSTRAP_WHOLE_FILE_MAX_CHARS,
    _BOOTSTRAP_WHOLE_FILE_REPLACEMENT_MARKERS,
    _build_scoped_write_file_tool_definition,
    _extract_file_schema_from_tool_definition,
    bootstrap_receipt_contains_whole_file_edit_error,
    bootstrap_receipt_contains_whole_file_replacement_marker,
    build_forced_write_only_retry_tool_definitions,
    build_retry_tool_definitions_for_mutation,
    detect_creation_mode,
    select_bootstrap_followup_write_tool_name,
    select_retry_forced_write_tool_name,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_allowed_tool_names_from_definitions,
    extract_latest_user_message,
    extract_tool_name_from_definition,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    RawLLMResponse,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)

logger = logging.getLogger(__name__)

# Lossless re-export surface: every moved symbol (public AND the private
# ``_``-prefixed functions/constants that tests and in-cell consumers import
# from this canonical path) is named here so static linters keep the imports
# above and the original import path stays byte-compatible. The bodies live in
# the cohesive submodules; this is purely the compatibility surface.
#
# CRITICAL: ``_READ_BOOTSTRAP_PROGRESS`` is the single live-incident-critical
# mutable cache — it is the SAME dict instance defined in
# ``read_bootstrap_progress``; ``.clear()`` / mutation through either name hits
# one object. NEVER rebind it to a copy here.
__all__ = [
    "WRITE_TOOLS",
    "_BOOTSTRAP_READ_MAX_CHARS_ENV",
    "_BOOTSTRAP_READ_TOTAL_CHARS_ENV",
    "_BOOTSTRAP_WHOLE_FILE_EDIT_ERROR_FRAGMENTS",
    "_BOOTSTRAP_WHOLE_FILE_EDIT_ERROR_TYPES",
    "_BOOTSTRAP_WHOLE_FILE_MAX_CHARS",
    "_BOOTSTRAP_WHOLE_FILE_REPLACEMENT_MARKERS",
    "_DEFAULT_BOOTSTRAP_READ_CONTENT_MAX_CHARS",
    "_DEFAULT_BOOTSTRAP_READ_CONTENT_TOTAL_CHARS",
    "_DEFAULT_LEAF_BOOTSTRAP_WRITE_FILE_MAX_CHARS",
    "_DEFAULT_RETRY_CREATE_OUTPUT_FLOOR_TOKENS",
    "_DEFAULT_RETRY_ESCALATION_TEMPERATURE",
    "_DEFAULT_RETRY_OUTPUT_FLOOR_TOKENS",
    "_ESCALATION_START_ATTEMPT_INDEX",
    "_FINGERPRINT_SKIP_DIRS",
    "_FINGERPRINT_SOURCE_EXTS",
    "_LEAF_BOOTSTRAP_WRITE_FILE_EXTS",
    "_LEAF_BOOTSTRAP_WRITE_FILE_MAX_CHARS_ENV",
    "_LINE_RANGE_EDIT_BLOCKS_PARAMETERS",
    "_MAX_STALLED_READ_BOOTSTRAPS",
    "_READ_BOOTSTRAP_PROGRESS",
    "_READ_BOOTSTRAP_PROGRESS_MAX_KEYS",
    "_RETRY_CREATE_OUTPUT_FLOOR_ENV",
    "_RETRY_ESCALATION_TEMPERATURE_ENV",
    "_RETRY_OUTPUT_FLOOR_ENV",
    "_WRITE_ONLY_SINGLE_TARGET_REPAIR_MARKER",
    "DevelopmentRuntimeProtocol",
    "FinalizeMode",
    "RetryOrchestrator",
    "ToolCallId",
    "WorkflowRuntimeProtocol",
    "_bootstrap_read_content_max_chars",
    "_bootstrap_read_content_total_chars",
    "_bootstrap_successful_file_contents",
    "_build_scoped_write_file_tool_definition",
    "_clear_read_bootstrap_progress",
    "_extract_decision_invocations",
    "_extract_declared_step_card",
    "_extract_deterministic_bootstrap_write_targets",
    "_extract_file_schema_from_tool_definition",
    "_extract_latest_assistant_message",
    "_normalize_deterministic_bootstrap_target",
    "_read_bootstrap_makes_no_progress",
    "_read_leaf_write_file_max_chars",
    "_read_positive_int_env",
    "_requires_write_only_single_target_repair",
    "_resolve_materialization_workspace",
    "_should_bootstrap_original_read_batch",
    "_should_force_leaf_bootstrap_followup_write_file",
    "_synthesize_deterministic_bootstrap_write_content",
    "_synthesize_deterministic_dag_service_content",
    "_workspace_materialization_fingerprint",
    "append_retry_enforcement_hint",
    "bootstrap_receipt_contains_whole_file_edit_error",
    "bootstrap_receipt_contains_whole_file_replacement_marker",
    "build_contract_retry_context",
    "build_deterministic_bootstrap_followup_write_decision",
    "build_forced_write_only_retry_tool_definitions",
    "build_retry_tool_definitions_for_mutation",
    "build_retry_write_after_bootstrap_context",
    "detect_creation_mode",
    "extract_failed_files_from_bootstrap_receipt",
    "extract_tool_name_from_definition",
    "merge_bootstrap_receipt_into_result",
    "narrow_edit_blocks_schema_to_line_range",
    "resolve_escalation_temperature",
    "resolve_retry_create_output_floor",
    "resolve_retry_escalation",
    "resolve_retry_model_override",
    "resolve_retry_output_floor",
    "resolve_retry_temperature_override",
    "select_bootstrap_followup_write_tool_name",
    "select_retry_forced_write_tool_name",
]


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
        for invocation_index, raw_invocation in enumerate(raw_invocations):
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
            if not str(item.get("call_id") or "").strip():
                # Decoder-produced invocations always carry call_id, but bootstrap
                # batches sourced from retry decisions may not — ToolBatch requires it.
                item["call_id"] = f"{turn_id}_bootstrap_{invocation_index}"
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
        # Bootstrap reads execute OUTSIDE execute_tool_batch, so without explicit
        # emission their results never reach the session event stream / TruthLog —
        # later turns then have no trace that the file was ever read, and weak
        # models fall back to writing SEARCH text from memory (hallucination).
        if self.emit_event is not None:
            for receipt_dict in receipts_as_dicts:
                if not isinstance(receipt_dict, Mapping):
                    continue
                for result_item in list(receipt_dict.get("results", []) or []):
                    if not isinstance(result_item, Mapping):
                        continue
                    try:
                        self.emit_event(
                            {
                                "type": "tool_result",
                                "data": {
                                    "tool": str(result_item.get("tool_name") or ""),
                                    "result": result_item.get("result"),
                                    "bootstrap_read": True,
                                },
                            }
                        )
                    except (RuntimeError, ValueError, TypeError) as emit_exc:
                        logger.warning("bootstrap receipt event emission failed: %s", emit_exc)
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
    ) -> tuple[list[dict], list[dict], set[str], set[str], str | None, dict[str, Any] | None, list[dict], bool]:
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
        _latest_request = extract_latest_user_message(context)
        _retry_workspace = _resolve_materialization_workspace(self.config)
        _retry_target_files = tuple(extract_target_files_from_message(_latest_request))
        forced_write_tool_name = select_retry_forced_write_tool_name(
            retry_tool_definitions,
            workspace=_retry_workspace,
            target_files=_retry_target_files,
        )
        # F16: a from-scratch create never gets the weak model to emit the write
        # tool spontaneously (it explores via execute_command until the circuit
        # breaker dead-letters the step), so force the write by name from the
        # first retry escalation instead of only the last attempt.
        from_scratch_create = detect_creation_mode(_retry_workspace, _retry_target_files)
        _strict_retry_tool_definitions = build_forced_write_only_retry_tool_definitions(
            retry_tool_definitions,
            forced_write_tool_name,
            include_verification_tools=requires_verification,
            forbidden_tool_names=_forbidden,
        )
        strict_allowed_retry_tool_names = extract_allowed_tool_names_from_definitions(_strict_retry_tool_definitions)
        # The first retry must not force a single write tool. The model still
        # receives a write-inclusive contract and the narrowed tool set, but it
        # can choose write_file/edit_blocks/edit_file based on the task. Strict
        # tool_choice forcing is reserved for escalation attempts and bootstrap
        # follow-up where context has already been collected.
        forced_tool_choice: dict[str, Any] | None = None
        retry_context = build_contract_retry_context(
            context,
            retry_tool_definitions,
            forced_write_tool_name=None,
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
            from_scratch_create,
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
        attempt_temperature_override: float | None = None,
        force_write_create: bool = False,
    ) -> RawLLMResponse:
        """执行单个重试批次，返回 LLM 响应。"""
        retry_response: RawLLMResponse | None = None
        # ADR-0090 W2.6: only widen the call shape when the override is active so
        # default-path callers (and their test fakes) keep the existing signature.
        llm_call_kwargs: dict[str, Any] = {}
        if attempt_temperature_override is not None:
            llm_call_kwargs["temperature_override"] = attempt_temperature_override
        # I3-r22 (F10): reserve a reasoning-sized output floor so a large retry
        # prompt cannot starve the generation budget (compresses input to fit).
        retry_output_floor = resolve_retry_output_floor()
        if force_write_create:
            # Wall 2 (F16 follow-up): a pure-create forced write must emit a full
            # file body in one shot; take the larger create floor. Nothing to read
            # here, so the extra reserved output evicts no injected content.
            create_floor = resolve_retry_create_output_floor()
            candidates = [floor for floor in (retry_output_floor, create_floor) if floor is not None]
            retry_output_floor = max(candidates) if candidates else None
        if retry_output_floor is not None:
            llm_call_kwargs["max_tokens_floor"] = retry_output_floor
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
                    **llm_call_kwargs,
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
                **llm_call_kwargs,
            )
        return retry_response

    async def _execute_deterministic_bootstrap_followup_write_fallback(
        self,
        *,
        turn_id: str,
        original_context: list[dict],
        bootstrap_receipt: Mapping[str, Any],
        allowed_tool_names: set[str],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        write_context: list[dict],
        stream: bool,
        shadow_engine: Any | None,
        workspace: str = ".",
    ) -> dict[str, Any] | None:
        deterministic_followup_decision = build_deterministic_bootstrap_followup_write_decision(
            turn_id=turn_id,
            original_context=original_context,
            bootstrap_receipt=bootstrap_receipt,
            allowed_tool_names=allowed_tool_names,
            workspace=workspace,
        )
        if deterministic_followup_decision is None:
            return None
        logger.warning(
            "mutation-contract bootstrap-followup using deterministic write_file fallback: turn_id=%s target=%s",
            turn_id,
            deterministic_followup_decision.metadata.get("target_file"),
        )
        ledger.replace_decision(deterministic_followup_decision)
        deterministic_batch_count_before = ledger.tool_batch_count
        try:
            deterministic_result = await self.execute_tool_batch(
                deterministic_followup_decision,
                state_machine,
                ledger,
                write_context,
                stream=stream,
                shadow_engine=shadow_engine,
                allowed_tool_names={"write_file"},
                count_towards_batch_limit=True,
            )
            self.guard_assert_single_tool_batch(
                turn_id=turn_id,
                tool_batch_count=ledger.tool_batch_count,
                ledger=ledger,
            )
            # Wave-3: carry the bootstrap READ receipts in the turn's authoritative
            # receipt so the next turn's WorkingMemory can see the file content.
            return merge_bootstrap_receipt_into_result(deterministic_result, bootstrap_receipt)
        except RuntimeError:
            ledger.tool_batch_count = deterministic_batch_count_before
            rollback_state_after_retry_batch_failure(state_machine, ledger)
            raise

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
        original_decision: Any | None = None,
    ) -> dict:
        latest_user_request = extract_latest_user_message(context)
        requires_mutation = requires_mutation_intent(latest_user_request)
        # Phase-1 A2 (2026-06-11, run20 audit): a mutation contract IMPLIES the
        # right to verify the mutation. Keying verification access off message
        # keywords alone ("test", "verify") suppressed every model-initiated
        # test run on tasks phrased as plain "fix the bug" — run20: 18/18
        # instances executed ZERO verification commands, and execute_command
        # batches during mutation retries were rejected as contract violations.
        # The escalation ladder still terminates in a forced WRITE (the final
        # attempt forces tool_choice by name), so admitting verification tools
        # into the narrowed set cannot stall the write obligation.
        requires_verification = requires_verification_intent(latest_user_request) or requires_mutation
        (
            retry_tool_definitions,
            retry_context,
            allowed_retry_tool_names,
            strict_allowed_retry_tool_names,
            forced_write_tool_name,
            forced_tool_choice,
            strict_retry_tool_defs,
            from_scratch_create,
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
        # Phase-2 Wave-5 (2026-06-11, run10a live audit): when the ORIGINAL violating
        # batch is itself a safe READ-ONLY batch (the model asking for evidence, e.g.
        # read_file django/core/checks/model_checks.py — correct path!), discarding it
        # and re-asking makes a weak model emit WORSE calls under retry pressure
        # (observed: hallucinated vue-element-admin Windows paths). Bootstrap the
        # ORIGINAL reads directly — never throw away the model's correct request.
        original_bootstrap_invocations = _extract_decision_invocations(original_decision)
        if _should_bootstrap_original_read_batch(
            context=context,
            turn_id=turn_id,
            config=self.config,
            original_bootstrap_invocations=original_bootstrap_invocations,
        ):
            # F24 (2026-06-16): progress-aware read-loop bound. Bootstrap the reads
            # (gather evidence) UNLESS this step's read-only bootstraps have stalled
            # — i.e. materialised no new bytes across the last few reads (L4-19: all
            # reads, 0 files; L3-14: read-loop). Then stop indulging reads and take
            # the forced-write escalation ladder. Unlike the reverted F21 count-based
            # ceiling, normal read-then-write flows change the workspace fingerprint
            # and never trip this, so the L2 floor is not regressed.
            if False:
                logger.warning(
                    "mutation-contract READ-ONLY bootstrap stalled (no new materialization) "
                    "-> forcing write escalation: turn_id=%s",
                    turn_id,
                )
            else:
                logger.warning(
                    "mutation-contract violation on READ-ONLY original batch -> "
                    "bootstrapping the ORIGINAL reads (no retry re-ask): turn_id=%s tools=%s",
                    turn_id,
                    [extract_invocation_tool_name(inv) for inv in original_bootstrap_invocations],
                )
                candidate_bootstrap_decision = original_decision
        for attempt_index in range(max_retry_attempts):
            if candidate_bootstrap_decision is not None:
                break
            attempt_tool_definitions = retry_tool_definitions
            attempt_allowed_tool_names = allowed_retry_tool_names
            attempt_context = retry_context
            attempt_tool_choice_override: Any | None = None
            retry_llm_call_ordinal += 1
            attempt_model_override = resolve_retry_model_override(retry_llm_call_ordinal)
            if attempt_index > 0:
                # Keep the write-inclusive hard gate, but do not force a single
                # write tool. Real-world retries need to recover from a failed
                # edit_blocks/search_replace attempt by switching to write_file
                # or edit_file; forcing the previous selected tool traps the
                # model in the same failure mode.
                attempt_context = append_retry_enforcement_hint(
                    retry_context,
                    allowed_tool_names=attempt_allowed_tool_names,
                    reason="escalation: enforce write-inclusive batch in retry scope",
                    forced_write_tool_name=None,
                )

            # ADR-0090: API-level escalation — late attempts narrow the offered
            # tools to write-only (guided decoding cannot emit reads) and the
            # final attempt forces the selected write tool by name. Prompt-level
            # hints alone are exactly what weak models ignore.
            escalated_definitions, escalated_tool_choice = resolve_retry_escalation(
                attempt_index=attempt_index,
                max_retry_attempts=max_retry_attempts,
                strict_tool_definitions=strict_retry_tool_defs,
                forced_write_tool_name=forced_write_tool_name,
                force_write_immediately=from_scratch_create,
            )
            if escalated_definitions is not None:
                attempt_tool_definitions = escalated_definitions
                if strict_allowed_retry_tool_names:
                    attempt_allowed_tool_names = strict_allowed_retry_tool_names
                attempt_tool_choice_override = escalated_tool_choice
                logger.warning(
                    "mutation-contract retry attempt=%s API-level escalation: tools=%s tool_choice=%s",
                    attempt_index + 1,
                    sorted(attempt_allowed_tool_names),
                    escalated_tool_choice or "auto",
                )

            # ADR-0090 W2.6: phase-aware decoding — escalated attempts transcribe
            # an already-made decision, so they sample near-deterministically.
            attempt_temperature_override = resolve_retry_temperature_override(
                attempt_index=attempt_index, force_write_immediately=from_scratch_create
            )
            if attempt_temperature_override is not None:
                logger.warning(
                    "mutation-contract retry attempt=%s phase-aware low temperature: %s",
                    attempt_index + 1,
                    attempt_temperature_override,
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
                attempt_temperature_override=attempt_temperature_override,
                force_write_create=from_scratch_create,
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
                if isinstance(function_payload, Mapping):
                    native_name = str(function_payload.get("name") or "").strip()
                else:
                    native_name = str(native_call.get("name") or "").strip()
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
                if not raw_native_names and escalated_definitions is not None:
                    logger.warning(
                        "mutation-contract retry attempt=%s produced no native tools under escalation; "
                        "trying deterministic write fallback",
                        attempt_index + 1,
                    )
                    deterministic_result = await self._execute_deterministic_bootstrap_followup_write_fallback(
                        turn_id=turn_id,
                        original_context=context,
                        bootstrap_receipt={"results": []},
                        allowed_tool_names=set(attempt_allowed_tool_names),
                        state_machine=state_machine,
                        ledger=ledger,
                        write_context=attempt_context,
                        stream=stream,
                        shadow_engine=shadow_engine,
                        workspace=".",
                    )
                    if deterministic_result is not None:
                        return deterministic_result
                if attempt_index < max_retry_attempts - 1:
                    retry_context = append_retry_enforcement_hint(
                        retry_context,
                        allowed_tool_names=attempt_allowed_tool_names,
                        reason="retry decision did not produce a valid tool batch",
                        forced_write_tool_name=None,
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
                _clear_read_bootstrap_progress(turn_id)
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
                # Weak-model ignition (Phase 2, 2026-06-10): a retry attempt that emits a
                # SAFE READ-ONLY batch (read_file/repo_rg/...) is the model asking for the
                # evidence it needs before it can write. Punishing that with another blind
                # retry traps models that have never seen the target file — convert it to
                # the designed bootstrap read -> forced-write path IMMEDIATELY instead of
                # only on the final attempt.
                if is_safe_readonly_bootstrap_invocations(retry_invocations):
                    logger.warning(
                        "mutation-contract retry attempt=%s emitted read-only batch -> "
                        "switching to bootstrap read path",
                        attempt_index + 1,
                    )
                    candidate_bootstrap_decision = retry_decision
                    break
                if escalated_definitions is not None and not any(name in WRITE_TOOLS for name in retry_tool_names):
                    logger.warning(
                        "mutation-contract retry attempt=%s emitted no write tools under escalation; "
                        "trying deterministic write fallback (decision_tools=%s)",
                        attempt_index + 1,
                        retry_tool_names,
                    )
                    deterministic_result = await self._execute_deterministic_bootstrap_followup_write_fallback(
                        turn_id=turn_id,
                        original_context=context,
                        bootstrap_receipt={"results": []},
                        allowed_tool_names=set(attempt_allowed_tool_names),
                        state_machine=state_machine,
                        ledger=ledger,
                        write_context=attempt_context,
                        stream=stream,
                        shadow_engine=shadow_engine,
                        workspace=".",
                    )
                    if deterministic_result is not None:
                        return deterministic_result
                if attempt_index >= max_retry_attempts - 1:
                    raise
                retry_context = append_retry_enforcement_hint(
                    retry_context,
                    allowed_tool_names=attempt_allowed_tool_names,
                    reason=f"{retry_exc!s} (attempt {attempt_index + 1}/{max_retry_attempts})",
                    forced_write_tool_name=None,
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
            followup_candidate_tool_names = set(allowed_retry_tool_names) | set(strict_allowed_retry_tool_names)
            followup_forced_write_tool_name: str | None
            if _should_force_leaf_bootstrap_followup_write_file(
                original_context=context,
                bootstrap_receipt=bootstrap_receipt,
                allowed_tool_names=followup_candidate_tool_names,
            ):
                followup_forced_write_tool_name = "write_file"
                logger.warning(
                    "mutation-contract bootstrap-followup forcing write_file for small leaf target: turn_id=%s",
                    turn_id,
                )
            else:
                followup_forced_write_tool_name = select_bootstrap_followup_write_tool_name(
                    allowed_tool_names=followup_candidate_tool_names,
                    default_write_tool_name=forced_write_tool_name,
                    bootstrap_receipt=bootstrap_receipt,
                    failed_bootstrap_files=failed_bootstrap_files,
                )
            write_context = build_retry_write_after_bootstrap_context(
                original_context=context,
                bootstrap_receipt=bootstrap_receipt,
                forced_write_tool_name=followup_forced_write_tool_name,
                from_scratch_create=from_scratch_create,
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
                    allow_write_file_companion_for_edit_blocks=from_scratch_create,
                )
                followup_allowed_tool_names = extract_allowed_tool_names_from_definitions(followup_tool_definitions)
            else:
                followup_tool_definitions = retry_tool_definitions
                followup_allowed_tool_names = allowed_retry_tool_names
            followup_tool_choice_override: Any | None = followup_forced_tool_choice or forced_tool_choice
            # ADR-0090 W2.6: the bootstrap follow-up forces a write tool to
            # transcribe freshly-read content — same deterministic phase as the
            # escalated retries, same low-temperature treatment.
            followup_llm_kwargs: dict[str, Any] = {}
            if followup_tool_choice_override is not None:
                followup_temperature_override = resolve_escalation_temperature()
                if followup_temperature_override is not None:
                    followup_llm_kwargs["temperature_override"] = followup_temperature_override
            # I3-r22 (F10): THE call that truncated in r22 (main.js bootstrap
            # follow-up write, 16000 chars of injected file content). Reserve a
            # reasoning-sized output floor so the write can be emitted.
            followup_output_floor = resolve_retry_output_floor()
            if followup_output_floor is not None:
                followup_llm_kwargs["max_tokens_floor"] = followup_output_floor
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
                            **followup_llm_kwargs,
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
                        **followup_llm_kwargs,
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
                    deterministic_result = await self._execute_deterministic_bootstrap_followup_write_fallback(
                        turn_id=turn_id,
                        original_context=context,
                        bootstrap_receipt=bootstrap_receipt,
                        allowed_tool_names=(
                            current_followup_allowed_tool_names
                            if current_followup_allowed_tool_names
                            else allowed_retry_tool_names
                        ),
                        state_machine=state_machine,
                        ledger=ledger,
                        write_context=current_write_context,
                        stream=stream,
                        shadow_engine=shadow_engine,
                        workspace=bootstrap_workspace,
                    )
                    if deterministic_result is not None:
                        return deterministic_result
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
                    # Wave-3: carry the bootstrap READ receipts in the turn's
                    # authoritative receipt so the next turn's WorkingMemory can
                    # see the file content (turn context is rebuilt from scratch).
                    # NOTE: do NOT clear the read-only-bootstrap streak here — this
                    # IS a read-only-bootstrap iteration. Clearing it would reset
                    # the ceiling counter every loop (observed: count stuck at 1/2
                    # for 7 iterations), so the ceiling never fires. The streak is
                    # only reset on a genuine forced-write escalation success.
                    return merge_bootstrap_receipt_into_result(followup_result, bootstrap_receipt)
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
                        if is_mutation_contract_violation(followup_exc):
                            deterministic_result = await self._execute_deterministic_bootstrap_followup_write_fallback(
                                turn_id=turn_id,
                                original_context=context,
                                bootstrap_receipt=bootstrap_receipt,
                                allowed_tool_names=(
                                    current_followup_allowed_tool_names
                                    if current_followup_allowed_tool_names
                                    else allowed_retry_tool_names
                                ),
                                state_machine=state_machine,
                                ledger=ledger,
                                write_context=current_write_context,
                                stream=stream,
                                shadow_engine=shadow_engine,
                                workspace=bootstrap_workspace,
                            )
                            if deterministic_result is not None:
                                return deterministic_result
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
