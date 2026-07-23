"""One-round deferred Director repair follow-up planning."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.director.runtime.public.directed_effect_contracts import hash_directed_effect_arguments
from polaris.cells.roles.kernel.internal.deferred_repair_effects import (
    DeferredRepairEffectSynthesizer,
    DeferredRequestReplayFence,
)
from polaris.cells.roles.kernel.public import (
    DeferredDirectorCommandRequestV1,
    DeferredDirectorRepairEffectBindingV1,
    DeferredDirectorRepairRequestV1,
)
from polaris.cells.roles.kernel.public.turn_contracts import BatchId, ToolBatch, ToolCallId, ToolInvocation
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1

_MAX_EXTRACTED_REQUESTS = 256
_MAX_REQUEST_NESTING_DEPTH = 32
_MAX_REQUEST_SCAN_NODES = 4096


@dataclass(slots=True)
class _RequestScanBudget:
    remaining: int = _MAX_REQUEST_SCAN_NODES

    def consume(self) -> None:
        if self.remaining <= 0:
            raise RuntimeError("deo_deferred_request_scan_capacity_exceeded")
        self.remaining -= 1


def _collect_request(
    value: DeferredDirectorRepairRequestV1 | DeferredDirectorCommandRequestV1,
    *,
    collect: bool,
    requests: list[DeferredDirectorRepairRequestV1],
    command_requests: list[DeferredDirectorCommandRequestV1],
) -> None:
    if not collect:
        return
    if len(requests) + len(command_requests) >= _MAX_EXTRACTED_REQUESTS:
        raise RuntimeError("deo_deferred_request_capacity_exceeded")
    if type(value) is DeferredDirectorRepairRequestV1:
        requests.append(value)
    else:
        command_requests.append(value)


@dataclass(frozen=True, slots=True)
class DeferredRepairFollowupV1:
    """Exact inventory and dispatch partition for one visible follow-up batch."""

    batch_id: str
    inventory_invocations: tuple[ToolInvocation, ...]
    dispatch_batch: ToolBatch
    forward_call_ids: tuple[str, ...]
    rollback_call_ids: tuple[str, ...]
    rollback_activation_by_call_id: tuple[tuple[str, str], ...]
    effect_bindings_by_call_id: tuple[tuple[str, DeferredDirectorRepairEffectBindingV1], ...]
    request_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, str) or not self.batch_id.strip():
            raise ValueError("batch_id must be non-empty")
        if not all(type(item) is ToolInvocation for item in self.inventory_invocations):
            raise TypeError("inventory_invocations must contain exact ToolInvocation values")
        inventory_ids = tuple(str(item.call_id) for item in self.inventory_invocations)
        if len(inventory_ids) != len(set(inventory_ids)):
            raise ValueError("inventory invocation call ids must be unique")
        if tuple(str(item.call_id) for item in self.dispatch_batch.serial_writes) != self.forward_call_ids:
            raise ValueError("dispatch batch must contain the exact forward partition")
        if {*self.forward_call_ids, *self.rollback_call_ids} != set(inventory_ids):
            raise ValueError("forward and rollback call ids must partition inventory")
        activation = dict(self.rollback_activation_by_call_id)
        if len(activation) != len(self.rollback_activation_by_call_id):
            raise ValueError("rollback activation call ids must be unique")
        if set(activation) != set(self.rollback_call_ids) or any(
            call_id not in set(self.forward_call_ids) for call_id in activation.values()
        ):
            raise ValueError("rollback activation must bind the exact forward/rollback partition")
        bindings = dict(self.effect_bindings_by_call_id)
        if len(bindings) != len(self.effect_bindings_by_call_id):
            raise ValueError("effect binding call ids must be unique")
        if not set(bindings).issubset(inventory_ids) or any(
            type(binding) is not DeferredDirectorRepairEffectBindingV1 or binding.tool_call_id != call_id
            for call_id, binding in bindings.items()
        ):
            raise ValueError("repair effect bindings must be an exact inventory subset")
        if len(self.request_ids) != len(set(self.request_ids)) or not all(self.request_ids):
            raise ValueError("request_ids must be non-empty and unique")


def _sanitize_and_collect(
    value: object,
    *,
    collect: bool,
    requests: list[DeferredDirectorRepairRequestV1],
    command_requests: list[DeferredDirectorCommandRequestV1],
    depth: int,
    budget: _RequestScanBudget,
) -> object:
    if depth > _MAX_REQUEST_NESTING_DEPTH:
        raise RuntimeError("deo_deferred_request_nesting_depth_exceeded")
    budget.consume()
    if type(value) is DeferredDirectorRepairRequestV1:
        _collect_request(
            value,
            collect=collect,
            requests=requests,
            command_requests=command_requests,
        )
        return None
    if type(value) is DeferredDirectorCommandRequestV1:
        _collect_request(
            value,
            collect=collect,
            requests=requests,
            command_requests=command_requests,
        )
        return None
    if isinstance(value, MutableMapping):
        for key in tuple(value):
            item = value[key]
            sanitized = _sanitize_and_collect(
                item,
                collect=collect,
                requests=requests,
                command_requests=command_requests,
                depth=depth + 1,
                budget=budget,
            )
            if type(item) in {DeferredDirectorRepairRequestV1, DeferredDirectorCommandRequestV1}:
                del value[key]
                continue
            value[key] = sanitized
        return value
    if isinstance(value, list):
        sanitized_items: list[object] = []
        for item in value:
            sanitized = _sanitize_and_collect(
                item,
                collect=collect,
                requests=requests,
                command_requests=command_requests,
                depth=depth + 1,
                budget=budget,
            )
            if type(item) not in {DeferredDirectorRepairRequestV1, DeferredDirectorCommandRequestV1}:
                sanitized_items.append(sanitized)
        value[:] = sanitized_items
        return value
    if isinstance(value, tuple):
        sanitized_items = []
        for item in value:
            sanitized = _sanitize_and_collect(
                item,
                collect=collect,
                requests=requests,
                command_requests=command_requests,
                depth=depth + 1,
                budget=budget,
            )
            if type(item) not in {DeferredDirectorRepairRequestV1, DeferredDirectorCommandRequestV1}:
                sanitized_items.append(sanitized)
        return tuple(sanitized_items)
    return value


def _extract_authoritative_requests(
    receipts: Sequence[MutableMapping[str, Any]],
) -> tuple[tuple[DeferredDirectorRepairRequestV1, ...], tuple[DeferredDirectorCommandRequestV1, ...]]:
    requests: list[DeferredDirectorRepairRequestV1] = []
    command_requests: list[DeferredDirectorCommandRequestV1] = []
    budget = _RequestScanBudget()
    for receipt in receipts:
        raw_rows = receipt.get("raw_results")
        if isinstance(raw_rows, list):
            _sanitize_and_collect(
                raw_rows,
                collect=False,
                requests=requests,
                command_requests=command_requests,
                depth=0,
                budget=budget,
            )
        rows = receipt.get("results")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, MutableMapping):
                continue
            collect = str(row.get("status") or "").strip().lower() == "success"
            if "result" in row:
                row["result"] = _sanitize_and_collect(
                    row["result"],
                    collect=collect,
                    requests=requests,
                    command_requests=command_requests,
                    depth=0,
                    budget=budget,
                )
    by_request_id: dict[str, DeferredDirectorRepairRequestV1] = {}
    for request in requests:
        previous = by_request_id.get(request.request_id)
        if previous is not None and previous != request:
            raise RuntimeError("deo_deferred_repair_request_identity_conflict")
        by_request_id[request.request_id] = request
    by_command_request_id: dict[str, DeferredDirectorCommandRequestV1] = {}
    for request in command_requests:
        previous = by_command_request_id.get(request.request_id)
        if previous is not None and previous != request:
            raise RuntimeError("deo_deferred_command_request_identity_conflict")
        by_command_request_id[request.request_id] = request
    return (
        tuple(by_request_id[key] for key in sorted(by_request_id)),
        tuple(sorted(by_command_request_id.values(), key=lambda item: (item.purpose, item.request_id))),
    )


@dataclass(slots=True)
class DeferredCommandEffectSynthesizer:
    """One-shot validator/synthesizer for adapter-discovered command effects."""

    _replay_fence: DeferredRequestReplayFence = field(default_factory=DeferredRequestReplayFence, repr=False)

    def synthesize_batch(
        self,
        requests: tuple[DeferredDirectorCommandRequestV1, ...],
        *,
        expected_workspace: str,
        expected_task_id: str,
        expected_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> tuple[ToolInvocation, ...]:
        if not requests:
            return ()
        if not all(type(request) is DeferredDirectorCommandRequestV1 for request in requests):
            raise TypeError("command requests must contain exact DeferredDirectorCommandRequestV1 values")
        request_ids = tuple(request.request_id for request in requests)
        if len(request_ids) != len(set(request_ids)):
            raise RuntimeError("deo_deferred_command_request_identity_conflict")
        canonical: list[DeferredDirectorCommandRequestV1] = []
        for request in requests:
            if request.workspace != expected_workspace:
                raise RuntimeError("deo_deferred_command_workspace_mismatch")
            if request.task_id != expected_task_id:
                raise RuntimeError("deo_deferred_command_task_mismatch")
            if request.execution_attempt != expected_execution_attempt:
                raise RuntimeError("deo_deferred_command_attempt_mismatch")
            try:
                rebuilt = DeferredDirectorCommandRequestV1(
                    request_id=request.request_id,
                    workspace=request.workspace,
                    task_id=request.task_id,
                    execution_attempt=request.execution_attempt,
                    command=request.command,
                    cwd=request.cwd,
                    timeout_seconds=request.timeout_seconds,
                    purpose=request.purpose,
                    schema_version=request.schema_version,
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError("deo_deferred_command_request_invalid") from exc
            if rebuilt != request or rebuilt.request_hash != request.request_hash:
                raise RuntimeError("deo_deferred_command_request_hash_mismatch")
            if request.cwd != ".":
                raise RuntimeError("deo_deferred_command_subdirectory_not_supported")
            canonical.append(rebuilt)
        fence_status = self._replay_fence.consume(request_ids)
        if fence_status == "replayed":
            raise RuntimeError("deo_deferred_command_request_replayed")
        if fence_status == "capacity":
            raise RuntimeError("deo_deferred_command_fence_capacity")
        return tuple(
            ToolInvocation(
                call_id=ToolCallId(f"deferred-command-{request.request_hash[:24]}"),
                tool_name="execute_command",
                raw_tool_name="execute_command",
                arguments={
                    "command": request.command,
                    "timeout": request.timeout_seconds,
                    "shell": False,
                },
            )
            for request in canonical
        )


def build_deferred_repair_followup(
    receipts: Sequence[MutableMapping[str, Any]],
    *,
    primary_batch_id: str,
    turn_id: str,
    expected_workspace: str,
    expected_task_id: str,
    expected_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None,
    synthesizer: DeferredRepairEffectSynthesizer,
    command_synthesizer: DeferredCommandEffectSynthesizer | None = None,
) -> DeferredRepairFollowupV1 | None:
    """Extract, sanitize, revalidate and synthesize at most one repair round."""

    requests, command_requests = _extract_authoritative_requests(receipts)
    if not requests and not command_requests:
        return None
    if type(expected_execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
        raise RuntimeError("deo_deferred_repair_attempt_required")
    forward: list[ToolInvocation] = []
    rollback: list[ToolInvocation] = []
    rollback_activation: list[tuple[str, str]] = []
    effect_bindings: list[tuple[str, DeferredDirectorRepairEffectBindingV1]] = []
    syntheses = (
        synthesizer.synthesize_batch(
            requests,
            expected_workspace=expected_workspace,
            expected_task_id=expected_task_id,
            expected_execution_attempt=expected_execution_attempt,
        )
        if requests
        else ()
    )
    for synthesis in syntheses:
        if not synthesis.ok:
            raise RuntimeError(str(synthesis.error_code or "deo_deferred_repair_synthesis_failed"))
        forward.extend(synthesis.forward_invocations)
        rollback.extend(synthesis.rollback_invocations)
        rollback_activation.extend(synthesis.rollback_activation_by_call_id)
        effect_bindings.extend(synthesis.effect_bindings_by_call_id)
    if command_requests and command_synthesizer is None:
        raise RuntimeError("deo_deferred_command_synthesizer_required")
    command_invocations = (
        command_synthesizer.synthesize_batch(
            command_requests,
            expected_workspace=expected_workspace,
            expected_task_id=expected_task_id,
            expected_execution_attempt=expected_execution_attempt,
        )
        if command_synthesizer is not None
        else ()
    )
    forward.extend(command_invocations)
    all_invocations = (*forward, *rollback)
    call_ids = tuple(str(item.call_id) for item in all_invocations)
    if len(call_ids) != len(set(call_ids)):
        raise RuntimeError("deo_deferred_repair_tool_call_id_conflict")
    batch_digest = hash_directed_effect_arguments(
        (
            ("domain", "roles_kernel_deferred_repair_followup_batch_v1"),
            ("primary_batch_id", primary_batch_id),
            (
                "request_hashes",
                tuple(request.request_hash for request in requests)
                + tuple(request.request_hash for request in command_requests),
            ),
            ("turn_id", turn_id),
        )
    )
    batch_id = f"{primary_batch_id}:deferred-repair:{batch_digest[:16]}"
    forward_invocations = tuple(forward)
    return DeferredRepairFollowupV1(
        batch_id=batch_id,
        inventory_invocations=all_invocations,
        dispatch_batch=ToolBatch(
            batch_id=BatchId(batch_id),
            invocations=list(forward_invocations),
            parallel_readonly=[],
            readonly_serial=[],
            serial_writes=list(forward_invocations),
            async_receipts=[],
        ),
        forward_call_ids=tuple(str(item.call_id) for item in forward_invocations),
        rollback_call_ids=tuple(str(item.call_id) for item in rollback),
        rollback_activation_by_call_id=tuple(rollback_activation),
        effect_bindings_by_call_id=tuple(effect_bindings),
        request_ids=tuple(request.request_id for request in requests)
        + tuple(request.request_id for request in command_requests),
    )


__all__ = ["DeferredCommandEffectSynthesizer", "DeferredRepairFollowupV1", "build_deferred_repair_followup"]
