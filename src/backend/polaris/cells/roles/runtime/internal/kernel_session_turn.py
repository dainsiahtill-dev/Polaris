"""Adapt canonical role-kernel turns to the existing session orchestrator.

This module owns no execution facts: TransactionKernel commits each turn,
roles.session persists its conversation, and the returned receipts are projections.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import AsyncGenerator, Mapping
from dataclasses import replace
from typing import Any

from polaris.cells.roles.kernel.public.llm_caller_contracts import (
    clamp_role_execution_timeout_to_deadline,
    prepare_role_session_continuation_policy,
)
from polaris.cells.roles.kernel.public.turn_contracts import TurnContinuationMode
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ErrorEvent,
    SessionCompletedEvent,
    SessionWaitingHumanEvent,
    TurnEvent,
)
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1, RoleExecutionResultV1
from polaris.kernelone.context.prompt_safety import format_tool_failure_summary


def _transcript_data_plane(value: Any) -> Any:
    """Keep tool observations while excluding execution and receipt authority."""
    if isinstance(value, Mapping):
        excluded = {
            "effect_receipt",
            "effect_receipt_commit",
            "director_policy",
            "authorization",
            "execution_envelope",
            "execution_attempt_authority",
            "job_token",
            "job_token_id",
            "job_token_set",
            "job_token_set_hash",
            "claim_grant",
            "claim_grant_hash",
            "capability_token",
        }
        return {str(key): item for key, item in value.items() if str(key) not in excluded}
    return value


def _committed_transcript_messages(results: list[RoleTurnResult]) -> list[dict[str, Any]]:
    """Derive native history from the same session's committed kernel results."""
    messages: list[dict[str, Any]] = []
    for result in results:
        commit = result.metadata.get("turn_commit_receipt")
        if not isinstance(commit, Mapping) or not commit.get("turn_id"):
            continue
        calls = {str(item.get("call_id") or ""): item for item in result.tool_calls if isinstance(item, Mapping)}
        native_calls: list[dict[str, Any]] = []
        tool_messages: list[dict[str, Any]] = []
        for row in (result.batch_receipt or {}).get("results", []):
            if not isinstance(row, Mapping):
                continue
            call_id = str(row.get("call_id") or "")
            call = calls.get(call_id)
            name = str(row.get("tool_name") or "")
            if row.get("status") not in {"success", "error", "failed", "blocked", "timeout", "aborted", "cancelled"}:
                continue
            if not call_id:
                continue
            if call is None or call.get("tool") != name or not isinstance(call.get("args"), Mapping):
                raise RuntimeError("committed_tool_transcript_missing_native_arguments:" + call_id)
            native_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(dict(call["args"]), ensure_ascii=False),
                    },
                }
            )
            observation = {"status": row.get("status"), "result": _transcript_data_plane(row.get("result"))}
            if row.get("error"):
                observation["error"] = row["error"]
            tool_messages.append(
                {
                    "role": "tool",
                    "name": name,
                    "tool_call_id": call_id,
                    "content": json.dumps(observation, ensure_ascii=False),
                    "is_error": row.get("status") != "success",
                }
            )
        if native_calls:
            messages.append({"role": "assistant", "content": "", "tool_calls": native_calls})
            messages.extend(tool_messages)
    return messages


def _failed_batch_feedback_summary(batch_receipt: Mapping[str, Any] | None) -> str:
    """Project only failed tool/path/error observations, never receipt authority."""
    rows = (batch_receipt or {}).get("results")
    if not isinstance(rows, list | tuple):
        return ""
    summaries: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("status") not in {
            "error",
            "failed",
            "timeout",
            "aborted",
            "cancelled",
        }:
            continue
        result = row.get("result")
        result = result if isinstance(result, Mapping) else {}
        arguments = row.get("arguments")
        arguments = arguments if isinstance(arguments, Mapping) else {}
        tool = str(row.get("tool_name") or row.get("tool") or "unknown")[:100]
        path = str(arguments.get("file") or result.get("file") or "")[:250]
        error_type = str(
            result.get("physical_error_type")
            or result.get("error_type")
            or result.get("error_code")
            or "tool_result_failed"
        )[:100]
        reason_value = result.get("physical_error") or row.get("error") or result.get("error")
        reason = " ".join(reason_value.split())[:600] if isinstance(reason_value, str) else "tool execution failed"
        summaries.append(
            format_tool_failure_summary(
                {
                    "schema_version": "tool_failure_summary.v1",
                    "tool": tool,
                    "error_type": error_type,
                    "reason": f"{path}: {reason}" if path else reason,
                    "prompt_safe": True,
                    "observation_only": True,
                    "non_deliverable": True,
                }
            )
        )
        if len(summaries) >= 3:
            break
    return "\n".join(summaries)[:1800]


def kernel_session_continuation_enabled(command: ExecuteRoleSessionCommandV1) -> bool:
    policy = command.context.get("continuous_execution")
    if isinstance(policy, Mapping) and "enabled" in policy:
        return policy["enabled"] is True
    return (
        command.role == "director"
        and command.host_kind == "director_adapter"
        and bool(command.task_id)
        and str(command.domain or "code").lower() == "code"
    )


class KernelSessionTurnBridge:
    """One canonical kernel invocation per orchestrator turn; never dispatch tools."""

    canonical_role_kernel = True

    def __init__(self, runtime: Any, command: ExecuteRoleSessionCommandV1) -> None:
        self.runtime = runtime
        self.command = command
        self.context = dict(command.context)
        self.context["continuous_execution"] = {**dict(self.context.get("continuous_execution") or {}), "enabled": True}
        self.results: list[RoleTurnResult] = []
        self.first_error: str | None = None
        enclosing = [
            float(value)
            for key in ("request_timeout_seconds", "timeout_seconds")
            if isinstance(value := command.context.get(key), int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
        ]
        # The command timeout describes a physical provider call. Prefer the
        # existing TaskRuntime budget for the entire continuous session.
        # Match the Director owner's enclosing watchdog semantics; a narrower
        # physical projection in one legacy timeout field cannot truncate it.
        budget = max(enclosing) if enclosing else command.timeout_seconds
        if budget is not None:
            budget = min(
                budget,
                clamp_role_execution_timeout_to_deadline(
                    timeout_seconds=max(1, math.ceil(budget)), context=self.context
                ),
            )
        self.deadline = time.monotonic() + budget if budget is not None else None

    def _turn_command(self, turn_id: str, message: str) -> ExecuteRoleSessionCommandV1:
        metadata = dict(self.command.metadata)
        # Invocation locators are deterministic per turn. Execution scope and
        # TaskRuntime authority remain untouched and are revalidated below.
        for key in (
            "transaction_invocation_identity",
            "transaction_attempt_identity",
            "transaction_attempt",
            "transaction_attempt_id",
            "turn_transition_id",
        ):
            metadata.pop(key, None)
        seed = "\0".join(
            (
                str(self.command.turn_request_id),
                self.command.workspace,
                str(self.command.run_id),
                str(self.command.task_id),
                self.command.session_id,
                turn_id,
            )
        )
        metadata["transaction_invocation_id"] = "session-turn-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()
        metadata["continuous_execution_turn_id"] = turn_id
        metadata["role_session_id"] = self.command.session_id
        context, metadata = prepare_role_session_continuation_policy(
            dict(self.context),
            metadata,
            role=self.command.role,
            host_kind=self.command.host_kind,
            previous_result=self.results[-1] if self.results else None,
            prior_results=tuple(self.results),
        )
        # The kernel assembly reconstructs current authority messages. The
        # session's prior native observations have one producer below.
        context.pop("_transaction_kernel_prebuilt_messages", None)
        if context.get("_roles_kernel_committed_session_continuation") is TurnContinuationMode.AUTO_CONTINUE:
            transcript = _committed_transcript_messages(self.results)
            if transcript:
                context["_transaction_kernel_prebuilt_messages"] = transcript
        remaining_seconds = self.command.timeout_seconds
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("continuous_execution_deadline_exceeded")
            remaining_seconds = max(1, math.ceil(min(remaining, self.command.timeout_seconds or remaining)))
            for key in ("request_timeout_seconds", "timeout_seconds"):
                previous = context.get(key)
                context[key] = (
                    min(float(previous), remaining) if isinstance(previous, int | float) and previous > 0 else remaining
                )
            provider_budget = min(remaining, self.command.timeout_seconds or remaining)
            previous = context.get("llm_call_timeout_seconds")
            context["llm_call_timeout_seconds"] = (
                min(float(previous), provider_budget)
                if isinstance(previous, int | float) and previous > 0
                else provider_budget
            )
        return replace(
            self.command,
            user_message=message,
            context=context,
            metadata=metadata,
            stream=True,
            timeout_seconds=remaining_seconds,
        )

    async def execute_stream(
        self,
        turn_id: str,
        context: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        *,
        parent_span_id: str | None = None,
    ) -> AsyncGenerator[TurnEvent | dict[str, Any], None]:
        del tool_definitions
        message = next(
            (str(item.get("content") or "") for item in reversed(context) if item.get("role") == "user"),
            self.command.user_message,
        )
        try:
            command = self._turn_command(turn_id, message)
            self.runtime._require_directed_effect_session_attempt(command)
            remaining = self.deadline - time.monotonic() if self.deadline is not None else None
            async with asyncio.timeout(remaining):
                request = await self.runtime._prepare_session_request(command, include_session_snapshot=True)
                kernel = self.runtime._get_kernel(command.workspace)
                final_result = None
                error_result_received = False
                actual_calls: dict[str, dict[str, Any]] = {}
                async for event in kernel.run_stream(command.role, request):
                    event_type = str(event.get("type") or "")
                    if event_type in {"content_chunk", "thinking_chunk"}:
                        yield ContentChunkEvent(
                            turn_id=turn_id,
                            chunk=str(event.get("content") or ""),
                            is_thinking=event_type == "thinking_chunk",
                            parent_span_id=parent_span_id,
                        )
                    elif event_type == "error":
                        error = str(event.get("error") or "role_stream_error")
                        candidate = event.get("result")
                        if isinstance(candidate, RoleTurnResult):
                            # Failure projections can carry already committed
                            # writes. Preserve them even when the failure is fatal.
                            final_result = replace(candidate, error=candidate.error or error, is_complete=False)
                            error_result_received = True
                            candidate_metadata = candidate.metadata or {}
                            has_committed_receipt = (
                                bool((candidate.batch_receipt or {}).get("results"))
                                and isinstance(candidate_metadata.get("turn_commit_receipt"), Mapping)
                                and bool(candidate_metadata["turn_commit_receipt"])
                            )
                            recoverable = (
                                str(event.get("error_type")) in {"task_boundary_failed", "stream_execution_failed"}
                                and candidate_metadata.get("transaction_kind") == "continue_multi_turn"
                                and candidate_metadata.get("needs_followup_workflow") is True
                                and has_committed_receipt
                            )
                            if recoverable:
                                continue
                        if self.first_error is None:
                            self.first_error = error
                        yield ErrorEvent(
                            turn_id=turn_id,
                            error_type=str(event.get("error_type") or "role_stream_error"),
                            message=error,
                            parent_span_id=parent_span_id,
                        )
                    elif event_type == "complete" and isinstance(event.get("result"), RoleTurnResult):
                        if not error_result_received:
                            final_result = event["result"]
                    elif event_type in {"tool_call", "tool_result"}:
                        if (
                            event_type == "tool_call"
                            and event.get("status") == "started"
                            and isinstance(event.get("args"), Mapping)
                        ):
                            call_id = str(event.get("call_id") or "")
                            if call_id:
                                actual_calls[call_id] = {
                                    "tool": event.get("tool"),
                                    "args": dict(event["args"]),
                                    "call_id": call_id,
                                    "turn_id": event.get("turn_id"),
                                }
                        yield dict(event)
                if final_result is None:
                    raise RuntimeError("role_stream_incomplete")
                commit = final_result.metadata.get("turn_commit_receipt")
                if isinstance(commit, Mapping) and commit.get("turn_id") and actual_calls:
                    committed_calls = [
                        {key: call[key] for key in ("tool", "args", "call_id")}
                        for row in (final_result.batch_receipt or {}).get("results", [])
                        if isinstance(row, Mapping)
                        and (call := actual_calls.get(str(row.get("call_id") or ""))) is not None
                        and call["turn_id"] == commit["turn_id"]
                        and call["tool"] == row.get("tool_name")
                    ]
                    final_result = replace(final_result, tool_calls=committed_calls)
                self.results.append(final_result)
                await self.runtime._persist_session_turn_state(
                    command,
                    turn_history=list(final_result.turn_history),
                    turn_events_metadata=list(final_result.turn_events_metadata),
                )
                self.context.update(dict(command.context))
            if self.first_error:
                return
            metadata = final_result.metadata or {}
            kind = str(metadata.get("transaction_kind") or "final_answer")
            followup = kind == "continue_multi_turn" and bool(metadata.get("needs_followup_workflow"))
            # Failed verification is a real failed receipt. It can request a
            # local next turn; policy/authority/provider failures cannot.
            if final_result.error and not followup:
                self.first_error = final_result.error
                yield ErrorEvent(
                    turn_id=turn_id,
                    error_type="role_kernel_failure",
                    message=final_result.error,
                    parent_span_id=parent_span_id,
                )
                return
            if final_result.is_complete and not followup:
                # Completion is scoped to the kernel-owned task boundary. A
                # qualified task need not manufacture another final-answer call.
                kind = "final_answer"
            failure_summary = str(final_result.error or "")
            failed_batch_summary = _failed_batch_feedback_summary(final_result.batch_receipt)
            if failed_batch_summary:
                failure_summary = "\n".join(part for part in (failure_summary, failed_batch_summary) if part)
            verdict = metadata.get("task_boundary_verdict")
            if isinstance(verdict, Mapping):
                for key in (
                    "missing_target_files",
                    "missing_required_evidence_modalities",
                    "failed_required_evidence_modalities",
                    "missing_required_verifiers",
                    "failed_required_verifiers",
                ):
                    values = verdict.get(key)
                    if isinstance(values, list | tuple) and values:
                        details = ", ".join(str(value)[:200] for value in values[:8])
                        failure_summary += f"; {key}={details}"
            yield CompletionEvent(
                turn_id=turn_id,
                status="failed" if final_result.error else "success",
                visible_content=final_result.content,
                turn_kind=kind,
                batch_receipt=dict(final_result.batch_receipt or {}),
                error=final_result.error,
                session_patch={"error_summary": failure_summary} if failure_summary else {},
                turn_outcome=dict(metadata.get("turn_outcome") or {}),
                commit_receipt=dict(metadata.get("turn_commit_receipt") or {}),
                llm_calls=int(final_result.execution_stats.get("llm_calls", 0)),
                tool_calls=int(final_result.execution_stats.get("tool_calls", 0)),
                parent_span_id=parent_span_id,
            )
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            error = str(exc) or (
                "continuous_execution_deadline_exceeded" if isinstance(exc, TimeoutError) else type(exc).__name__
            )
            if self.first_error is None:
                self.first_error = error
            yield ErrorEvent(
                turn_id=turn_id, error_type=type(exc).__name__, message=error, parent_span_id=parent_span_id
            )


async def stream_kernel_session(
    runtime: Any, command: ExecuteRoleSessionCommandV1
) -> AsyncGenerator[dict[str, Any], None]:
    """Project the one existing orchestrator for both public session transports."""
    bridge = KernelSessionTurnBridge(runtime, command)
    policy = command.context.get("continuous_execution")
    # A budgeted canonical task uses its existing irreversible deadline.
    # Explicit caller turn limits remain enforceable; unbudgeted sessions
    # retain the legacy ceiling rather than becoming unbounded.
    max_turns = (
        int(policy["max_auto_turns"])
        if isinstance(policy, Mapping) and "max_auto_turns" in policy
        else None
        if bridge.deadline is not None
        else 10
    )
    orchestrator = RoleSessionOrchestrator(
        command.session_id, bridge, command.workspace, role=command.role, max_auto_turns=max_turns
    )
    stop_reason = None
    async for event in orchestrator.execute_stream(command.user_message, context=dict(command.context)):
        if isinstance(event, dict):
            yield dict(event)
        elif isinstance(event, ContentChunkEvent):
            yield {"type": "thinking_chunk" if event.is_thinking else "content_chunk", "content": event.chunk}
        if isinstance(event, ErrorEvent):
            stop_reason = stop_reason or event.message
            yield {"type": "error", "error": event.message, "error_type": event.error_type}
        elif isinstance(event, SessionWaitingHumanEvent) or (
            isinstance(event, SessionCompletedEvent) and event.reason not in {None, "end_session"}
        ):
            stop_reason = stop_reason or event.reason
    last = bridge.results[-1] if bridge.results else RoleTurnResult(content="", is_complete=False)
    if stop_reason or bridge.first_error:
        last = replace(last, error=stop_reason or bridge.first_error, is_complete=False)
    elif bool(last.metadata.get("needs_followup_workflow")) or not last.is_complete:
        last = replace(last, error="continuous_execution_incomplete", is_complete=False)
    receipts = [dict(result.batch_receipt) for result in bridge.results if result.batch_receipt]
    rows = [dict(row) for receipt in receipts for row in receipt.get("results", []) if isinstance(row, Mapping)]
    batch_projection = {
        "projection_only": True,
        "batch_receipts": receipts,
        "results": rows,
        "success_count": sum(int(receipt.get("success_count", 0)) for receipt in receipts),
        "failure_count": sum(int(receipt.get("failure_count", 0)) for receipt in receipts),
    }
    aggregate_stats = dict(last.execution_stats)
    for key in ("llm_calls", "tool_calls", "duration_ms"):
        aggregate_stats[key] = sum(turn.execution_stats.get(key, 0) for turn in bridge.results)
    last = replace(
        last,
        tool_calls=[item for turn in bridge.results for item in turn.tool_calls],
        tool_results=[item for turn in bridge.results for item in turn.tool_results],
        batch_receipt=batch_projection,
        execution_stats=aggregate_stats,
        turn_history=[item for turn in bridge.results for item in turn.turn_history],
        turn_events_metadata=[item for turn in bridge.results for item in turn.turn_events_metadata],
    )
    last = replace(
        last,
        metadata={
            **dict(last.metadata),
            "continuous_execution": {
                "enabled": True,
                "turns_executed": len(bridge.results),
                "stop_reason": stop_reason,
            },
            "batch_receipt": batch_projection,
            "tool_results": [item for turn in bridge.results for item in turn.tool_results],
            "session_turn_commit_receipts": [
                dict(turn.metadata["turn_commit_receipt"])
                for turn in bridge.results
                if isinstance(turn.metadata.get("turn_commit_receipt"), Mapping)
            ],
        },
    )
    yield {"type": "complete", "result": last, "content": last.content, "metadata": dict(last.metadata)}


async def execute_kernel_session(runtime: Any, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
    """Collect the same session projection used by the public streaming facade."""
    from polaris.cells.roles.runtime.public.service import _to_contract_result

    final_result = None
    async for event in stream_kernel_session(runtime, command):
        if event.get("type") == "complete" and isinstance(event.get("result"), RoleTurnResult):
            final_result = event["result"]
    if final_result is None:
        raise RuntimeError("continuous_execution_missing_terminal_projection")
    return _to_contract_result(
        role=command.role,
        workspace=command.workspace,
        task_id=command.task_id,
        session_id=command.session_id,
        run_id=command.run_id,
        result=final_result,
    )
