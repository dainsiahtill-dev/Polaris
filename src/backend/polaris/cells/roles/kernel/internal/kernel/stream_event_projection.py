"""Stream TransactionKernel event projection.

This module owns the stateful translation from typed stream events to the
dictionary events consumed by role-stream callers. Transaction execution and
tool dispatch stay inside TransactionKernel; this owner only records projection
feedback, builds completion results, emits stream events, and appends the
task-boundary verdict for successful stream completion.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.control_plane.run_ledger.public import task_boundary_tool_dispatch_from_lifecycle_metadata
from polaris.cells.roles.kernel.internal.kernel.commit_protocol import _build_turn_history_and_events
from polaris.cells.roles.kernel.internal.kernel.role_result_projection import (
    project_completion_audit_evidence,
    project_task_boundary_failure_to_metadata,
    role_result_metadata_from_profile,
    role_turn_completion_result,
    tool_calls_from_batch_receipt,
    tool_results_from_batch_receipt,
)
from polaris.cells.roles.kernel.internal.kernel.task_boundary import (
    append_role_turn_task_boundary_verdict,
    task_boundary_evidence_refs_from_metadata,
)
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_completion import (
    MISSING_DISPATCH_COMPLETION_ERROR,
    build_task_boundary_ledger_append_failure_verdict,
    record_missing_dispatch_lifecycle_receipt,
    task_boundary_ledger_append_failure_error,
)
from polaris.cells.roles.kernel.internal.kernel.turn_output_validation import validate_turn_output
from polaris.cells.roles.kernel.internal.structured_output_transport import (
    resolve_structured_output_transport,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ErrorEvent,
    FinalizationEvent,
    ToolBatchEvent,
    TurnEvent,
    TurnPhaseEvent,
)
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest
from polaris.kernelone.events.uep_publisher import UEPEventPublisher

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamEventProjectionResult:
    """Projected stream event plus flow-control metadata."""

    event: dict[str, Any]
    should_stop: bool = False


@dataclass
class StreamEventProjector:
    """Translate typed stream events into public role-stream dictionaries."""

    kernel: Any
    role: str
    profile: RoleProfile
    request: RoleTurnRequest
    fingerprint: Any
    context_gateway: Any
    context_result: Any
    stream_run_id: str
    uep_publisher: UEPEventPublisher
    runtime_tool_policy_audit: dict[str, Any]
    tool_filter_audit: dict[str, Any] | None
    accumulated_content: list[str] = field(default_factory=list)
    accumulated_thinking: list[str] = field(default_factory=list)
    stream_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stream_tool_results: list[dict[str, Any]] = field(default_factory=list)

    async def project(self, event: TurnEvent) -> StreamEventProjectionResult | None:
        """Project one typed stream event.

        Returns ``None`` for events intentionally hidden from stream callers.
        """

        if isinstance(event, TurnPhaseEvent):
            return await self._publish_result(
                {
                    "type": event.phase,
                    "turn_id": event.turn_id,
                    "metadata": dict(event.metadata),
                }
            )
        if isinstance(event, ContentChunkEvent):
            return await self._project_content_chunk(event)
        if isinstance(event, ToolBatchEvent):
            return await self._project_tool_batch(event)
        if isinstance(event, FinalizationEvent):
            return None
        if isinstance(event, CompletionEvent):
            return await self._project_completion(event)
        if isinstance(event, ErrorEvent):
            self._record_projection_outcome(success=False, reason="stream error")
            return await self._publish_result(
                {
                    "type": "error",
                    "error": event.message,
                    "error_type": event.error_type,
                    "turn_id": event.turn_id,
                    "metadata": dict(event.metadata),
                }
            )
        return None

    async def _project_content_chunk(self, event: ContentChunkEvent) -> StreamEventProjectionResult:
        if event.is_thinking:
            self.accumulated_thinking.append(event.chunk)
            event_dict = {
                "type": "thinking_chunk",
                "content": event.chunk,
                "turn_id": event.turn_id,
            }
        else:
            if event.is_finalization:
                self.accumulated_content = [event.chunk]
            else:
                self.accumulated_content.append(event.chunk)
            event_dict = {
                "type": "content_chunk",
                "content": event.chunk,
                "turn_id": event.turn_id,
            }
        return await self._publish_result(event_dict)

    async def _project_tool_batch(self, event: ToolBatchEvent) -> StreamEventProjectionResult:
        arguments = dict(event.arguments) if isinstance(event.arguments, dict) else {}
        if event.status == "started":
            self.stream_tool_calls.append(
                {
                    "tool": event.tool_name,
                    "args": arguments,
                    "call_id": event.call_id,
                }
            )
        else:
            self.stream_tool_results.append(
                {
                    "tool": event.tool_name,
                    "result": event.result,
                    "success": event.status == "success",
                    "call_id": event.call_id,
                }
            )
        return await self._publish_result(
            {
                "type": "tool_result" if event.status in ("success", "error") else "tool_call",
                "tool": event.tool_name,
                "call_id": event.call_id,
                "status": event.status,
                "progress": event.progress,
                "turn_id": event.turn_id,
                "args": arguments,
                "result": event.result,
                "error": event.error,
            }
        )

    async def _project_completion(self, event: CompletionEvent) -> StreamEventProjectionResult:
        final_content = "".join(self.accumulated_content)
        final_thinking = "".join(self.accumulated_thinking) or None
        batch_receipt = dict(event.batch_receipt) if isinstance(event.batch_receipt, dict) else None
        tool_calls = tool_calls_from_batch_receipt(batch_receipt) or self.stream_tool_calls
        tool_results = tool_results_from_batch_receipt(batch_receipt) or self.stream_tool_results

        metadata = role_result_metadata_from_profile(
            profile=self.profile,
            tool_filter_audit=self.tool_filter_audit,
            monitoring=event.monitoring if isinstance(event.monitoring, dict) else None,
        )
        _lift_completion_audit_evidence(metadata, event.monitoring)
        if event.commit_receipt:
            metadata["turn_commit_receipt"] = dict(event.commit_receipt)
        if event.turn_outcome:
            metadata["turn_outcome"] = dict(event.turn_outcome)

        if event.status in ("failed", "suspended"):
            self._record_projection_outcome(success=False, reason="stream failure")
            return await self._publish_result(
                {
                    "type": "error",
                    "error": event.error or "execution_failed",
                    "error_type": "stream_execution_failed",
                    "turn_id": event.turn_id,
                    "metadata": dict(metadata),
                },
                should_stop=True,
            )

        event_dict: dict[str, Any] = {
            "type": "complete",
            "status": event.status,
            "content": final_content,
            "thinking": final_thinking,
            "duration_ms": event.duration_ms,
            "llm_calls": event.llm_calls,
            "tool_calls": event.tool_calls,
            "turn_id": event.turn_id,
        }
        if event.monitoring:
            event_dict["monitoring"] = dict(event.monitoring)

        lifecycle_receipt = record_missing_dispatch_lifecycle_receipt(
            role=self.role,
            request=self.request,
            kernel=self.kernel,
            turn_id=event.turn_id,
            metadata=metadata,
            ledger=None,
            tool_results=tool_results,
            batch_receipt=batch_receipt,
        )
        structured_output: dict[str, Any] | None = None
        if lifecycle_receipt is None and self.request.validate_output:
            quality_result, _ = validate_turn_output(
                kernel=self.kernel,
                profile=self.profile,
                content=final_content,
                response_schema=None,
                attempt=0,
                max_retries=max(0, int(self.request.max_retries)),
                last_error=None,
                has_tool_activity=bool(tool_calls or tool_results),
                structured_output_context=self.request.context_override,
            )
            metadata["output_validation"] = {
                "success": bool(quality_result.success),
                "errors": list(quality_result.errors),
                "suggestions": list(quality_result.suggestions),
                "quality_score": float(quality_result.quality_score),
            }
            structured_output_plan = resolve_structured_output_transport(self.request.context_override)
            if structured_output_plan is not None:
                metadata["output_validation"].update(
                    {
                        "schema_name": structured_output_plan.contract.schema_name,
                        "validation_source": "caller_structured_output_contract",
                    }
                )
            if not quality_result.success:
                self._record_projection_outcome(success=False, reason="stream output validation failed")
                validation_error = "; ".join(str(item) for item in quality_result.errors)
                return await self._publish_result(
                    {
                        "type": "error",
                        "error": f"Output validation failed: {validation_error or 'unknown validation error'}",
                        "error_type": "output_validation_failed",
                        "turn_id": event.turn_id,
                        "metadata": dict(metadata),
                    },
                    should_stop=True,
                )
            if isinstance(quality_result.data, dict):
                structured_output = dict(quality_result.data)
        task_boundary_verdict = self._append_task_boundary_verdict(
            event.turn_id,
            tool_results,
            metadata,
            tool_dispatch=task_boundary_tool_dispatch_from_lifecycle_metadata(metadata),
        )
        task_boundary_error = _task_boundary_error_message(task_boundary_verdict, metadata)
        control_plane_error = task_boundary_ledger_append_failure_error(task_boundary_verdict)
        if control_plane_error:
            self._record_projection_outcome(success=False, reason="stream control-plane failure")
            return await self._publish_result(
                {
                    "type": "error",
                    "error": control_plane_error,
                    "error_type": "control_plane_failure",
                    "turn_id": event.turn_id,
                    "metadata": dict(metadata),
                },
                should_stop=True,
            )
        if lifecycle_receipt is not None:
            # Dropped required-write dispatch outranks the task-boundary error:
            # the missing materialization is a symptom of the dropped dispatch.
            self._record_projection_outcome(success=False, reason="stream tool dispatch dropped")
            return await self._publish_result(
                {
                    "type": "error",
                    "error": MISSING_DISPATCH_COMPLETION_ERROR,
                    "error_type": "tool_dispatch_dropped",
                    "turn_id": event.turn_id,
                    "metadata": dict(metadata),
                },
                should_stop=True,
            )
        if task_boundary_error:
            self._record_projection_outcome(success=False, reason="stream task boundary failure")
            return await self._publish_result(
                {
                    "type": "error",
                    "error": task_boundary_error,
                    "error_type": "task_boundary_failed",
                    "turn_id": event.turn_id,
                    "metadata": dict(metadata),
                },
                should_stop=True,
            )
        if "context_os_audit" in metadata:
            event_dict["metadata"] = dict(metadata)

        projection_weights = self._record_projection_outcome(
            success=event.status == "success",
            reason="stream completion",
        )
        if projection_weights is not None:
            metadata["projection_adaptive_weights_after_turn"] = projection_weights
            event_dict["metadata"] = dict(metadata)

        turn_history, turn_events_metadata = _build_turn_history_and_events(
            turn_id=event.turn_id,
            request=self.request,
            visible_content=final_content,
            thinking=final_thinking,
            tool_results=tool_results,
        )
        event_dict["result"] = role_turn_completion_result(
            content=final_content,
            thinking=final_thinking,
            structured_output=structured_output,
            tool_calls=tool_calls,
            tool_results=tool_results,
            batch_receipt=batch_receipt,
            profile=self.profile,
            fingerprint=self.fingerprint,
            error=None,
            is_complete=True,
            execution_stats={
                "duration_ms": event.duration_ms,
                "llm_calls": event.llm_calls,
                "tool_calls": event.tool_calls,
                "transaction_kernel": True,
                **self.runtime_tool_policy_audit,
            },
            turn_history=turn_history,
            turn_events_metadata=turn_events_metadata,
            metadata=metadata,
        )
        return await self._publish_result(event_dict)

    def _record_projection_outcome(self, *, success: bool, reason: str) -> dict[str, float] | None:
        try:
            outcome = self.context_gateway.record_projection_outcome(
                success=success,
                tokens_used=int(getattr(self.context_result, "token_estimate", 0) or 0),
            )
            return dict(outcome) if isinstance(outcome, dict) else None
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.debug("Projection outcome feedback failed after %s", reason, exc_info=True)
            return None

    def _append_task_boundary_verdict(
        self,
        turn_id: str,
        tool_results: list[dict[str, Any]],
        metadata: dict[str, Any],
        *,
        tool_dispatch: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            return append_role_turn_task_boundary_verdict(
                role=self.role,
                workspace=str(self.request.workspace or self.kernel.workspace or "."),
                task_id=str(self.request.task_id or ""),
                run_id=str(self.request.run_id or turn_id),
                context_override=getattr(self.request, "context_override", None),
                tool_results=tool_results,
                tool_dispatch=tool_dispatch,
                needs_followup_workflow=False,
                evidence_refs=task_boundary_evidence_refs_from_metadata(metadata),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            run_id = str(self.request.run_id or turn_id)
            logger.error(
                "Stream TaskBoundary Run Ledger append failed: run_id=%s turn_id=%s error_type=%s",
                run_id,
                turn_id,
                type(exc).__name__,
                exc_info=True,
            )
            return build_task_boundary_ledger_append_failure_verdict(
                error=exc,
                run_id=run_id,
                turn_id=turn_id,
            )

    async def _publish_result(
        self,
        event_dict: dict[str, Any],
        *,
        should_stop: bool = False,
    ) -> StreamEventProjectionResult:
        await self.uep_publisher.publish_stream_event(
            workspace=self.kernel.workspace or os.getcwd(),
            run_id=self.stream_run_id,
            role=self.role,
            event_type=str(event_dict.get("type", "unknown")),
            payload=event_dict,
        )
        return StreamEventProjectionResult(event=event_dict, should_stop=should_stop)


def _lift_completion_audit_evidence(metadata: dict[str, Any], monitoring: dict[str, Any] | None) -> None:
    """Copy final-request dispatch-evidence keys from stream monitoring into metadata.

    The stream completion event carries the final-request audit inside its
    monitoring payload; the shared missing-dispatch lifecycle receipt reads
    the same metadata keys as the non-stream completion owner.
    """
    project_completion_audit_evidence(metadata, monitoring)


def _task_boundary_error_message(verdict: dict[str, Any] | None, metadata: dict[str, Any]) -> str | None:
    task_boundary_error = project_task_boundary_failure_to_metadata(metadata, verdict)
    if task_boundary_error is None:
        return None
    return task_boundary_ledger_append_failure_error(verdict) or task_boundary_error
