"""Pure DEO-2C synthesis for one deferred Director repair round."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    RepairAdvisoryV1,
    RepairDiagnosticV1,
    plan_director_repair,
    validate_director_repair_effect_plan,
)
from polaris.cells.roles.kernel.public.directed_effect_contracts import (
    DeferredDirectorRepairEffectBindingV1,
    DeferredDirectorRepairRequestV1,
    DeferredDirectorRepairSynthesisResultV1,
)
from polaris.cells.roles.kernel.public.turn_contracts import ToolCallId, ToolInvocation
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1

_PLANNING_PAYLOAD_FIELDS = frozenset(
    {
        "advisor_notes",
        "artifact_quality_errors",
        "artifact_quality_issues",
        "base_files",
        "deterministic_only",
        "diagnostics",
        "mode",
        "source_tool",
    }
)
_MAX_CONSUMED_REQUESTS = 256


@dataclass(slots=True)
class DeferredRequestReplayFence:
    """Shared bounded replay fence for repair and command request identities."""

    capacity: int = _MAX_CONSUMED_REQUESTS
    _consumed_request_ids: set[str] = field(default_factory=set, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def check(self, request_ids: tuple[str, ...]) -> str | None:
        with self._lock:
            if any(request_id in self._consumed_request_ids for request_id in request_ids):
                return "replayed"
            if len(self._consumed_request_ids) + len(request_ids) > self.capacity:
                return "capacity"
        return None

    def consume(self, request_ids: tuple[str, ...]) -> str | None:
        with self._lock:
            if any(request_id in self._consumed_request_ids for request_id in request_ids):
                return "replayed"
            if len(self._consumed_request_ids) + len(request_ids) > self.capacity:
                return "capacity"
            self._consumed_request_ids.update(request_ids)
        return None


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def build_deferred_repair_planning_payload(command: PlanDirectorRepairCommandV1) -> str:
    """Freeze the exact public planning input into canonical UTF-8 JSON text."""

    if type(command) is not PlanDirectorRepairCommandV1:
        raise TypeError("command must be exactly PlanDirectorRepairCommandV1")
    payload = {
        "advisor_notes": [
            {
                "advisor_source": note.advisor_source,
                "confidence": note.confidence,
                "message": note.message,
                "metadata": dict(note.metadata),
                "suggested_rules": [dict(item) for item in note.suggested_rules],
            }
            for note in command.advisor_notes
        ],
        "artifact_quality_errors": list(command.artifact_quality_errors),
        "artifact_quality_issues": [dict(item) for item in command.artifact_quality_issues],
        "base_files": dict(command.base_files),
        "deterministic_only": command.deterministic_only,
        "diagnostics": [
            {
                "code": diagnostic.code,
                "message": diagnostic.message,
                "metadata": dict(diagnostic.metadata),
                "path": diagnostic.path,
                "severity": diagnostic.severity,
                "source": diagnostic.source,
            }
            for diagnostic in command.diagnostics
        ],
        "mode": command.mode,
        "source_tool": command.source_tool,
    }
    return _canonical_json(payload)


def _exact_mapping(name: str, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{name} keys must be strings")
    return dict(value)


def _exact_list(name: str, value: object) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be an array")
    return list(value)


def _repair_command_from_payload(payload_json: str) -> PlanDirectorRepairCommandV1:
    payload = _exact_mapping("planning payload", json.loads(payload_json))
    if set(payload) != _PLANNING_PAYLOAD_FIELDS:
        raise ValueError("planning payload fields must match canonical schema")
    base_files = _exact_mapping("base_files", payload["base_files"])
    if not all(isinstance(path, str) and isinstance(content, str) for path, content in base_files.items()):
        raise TypeError("base_files must map string paths to UTF-8 text")
    artifact_quality_errors = _exact_list("artifact_quality_errors", payload["artifact_quality_errors"])
    if not all(isinstance(item, str) for item in artifact_quality_errors):
        raise TypeError("artifact_quality_errors must contain strings")
    issues = tuple(
        _exact_mapping("artifact_quality_issue", item)
        for item in _exact_list("artifact_quality_issues", payload["artifact_quality_issues"])
    )
    diagnostics = tuple(
        RepairDiagnosticV1(
            source=str(row["source"]),
            code=str(row["code"]),
            message=str(row["message"]),
            path=str(row["path"]) if row.get("path") is not None else None,
            severity=str(row["severity"]),
            metadata=_exact_mapping("diagnostic metadata", row["metadata"]),
        )
        for row in (_exact_mapping("diagnostic", item) for item in _exact_list("diagnostics", payload["diagnostics"]))
    )
    advisor_notes = tuple(
        RepairAdvisoryV1(
            advisor_source=str(row["advisor_source"]),
            message=str(row["message"]),
            confidence=float(row["confidence"]),
            suggested_rules=tuple(
                _exact_mapping("suggested rule", item)
                for item in _exact_list("suggested_rules", row["suggested_rules"])
            ),
            metadata=_exact_mapping("advisor metadata", row["metadata"]),
        )
        for row in (
            _exact_mapping("advisor note", item) for item in _exact_list("advisor_notes", payload["advisor_notes"])
        )
    )
    deterministic_only = payload["deterministic_only"]
    if type(deterministic_only) is not bool or deterministic_only is not True:
        raise ValueError("deferred repair planning must remain deterministic_only")
    if not isinstance(payload["source_tool"], str) or not isinstance(payload["mode"], str):
        raise TypeError("source_tool and mode must be strings")
    return PlanDirectorRepairCommandV1(
        source_tool=payload["source_tool"],
        base_files=base_files,
        artifact_quality_errors=tuple(artifact_quality_errors),
        artifact_quality_issues=issues,
        diagnostics=diagnostics,
        mode=payload["mode"],
        deterministic_only=True,
        advisor_notes=advisor_notes,
    )


@dataclass(slots=True)
class DeferredRepairEffectSynthesizer:
    """One-shot pure synthesizer; it never owns a mutation port or physical executor."""

    _replay_fence: DeferredRequestReplayFence = field(default_factory=DeferredRequestReplayFence, repr=False)

    def _failure(
        self,
        request: DeferredDirectorRepairRequestV1,
        error_code: str,
    ) -> DeferredDirectorRepairSynthesisResultV1:
        return DeferredDirectorRepairSynthesisResultV1(
            ok=False,
            request_id=request.request_id,
            request_hash=request.request_hash,
            plan_hash=request.plan.plan_hash,
            error_code=error_code,
        )

    def _synthesize_candidate(
        self,
        request: DeferredDirectorRepairRequestV1,
        *,
        expected_workspace: str,
        expected_task_id: str,
        expected_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DeferredDirectorRepairSynthesisResultV1:
        """Re-plan one request without spending the batch replay fence."""

        if type(request) is not DeferredDirectorRepairRequestV1:
            raise TypeError("request must be exactly DeferredDirectorRepairRequestV1")
        if request.workspace != expected_workspace:
            return self._failure(request, "deo_deferred_repair_workspace_mismatch")
        if request.task_id != expected_task_id:
            return self._failure(request, "deo_deferred_repair_task_mismatch")
        if (
            type(expected_execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1
            or request.execution_attempt != expected_execution_attempt
        ):
            return self._failure(request, "deo_deferred_repair_attempt_mismatch")
        try:
            canonical_plan = validate_director_repair_effect_plan(request.plan)
        except (TypeError, ValueError):
            return self._failure(request, "deo_deferred_repair_plan_hash_mismatch")
        try:
            canonical_request = DeferredDirectorRepairRequestV1(
                request_id=request.request_id,
                workspace=request.workspace,
                task_id=request.task_id,
                execution_attempt=request.execution_attempt,
                plan=canonical_plan,
                planning_payload_json=request.planning_payload_json,
                allowed_paths=request.allowed_paths,
                schema_version=request.schema_version,
            )
        except (TypeError, ValueError):
            return self._failure(request, "deo_deferred_repair_request_invalid")
        if canonical_request.request_hash != request.request_hash:
            return self._failure(request, "deo_deferred_repair_request_hash_mismatch")
        try:
            replanned = plan_director_repair(_repair_command_from_payload(request.planning_payload_json))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._failure(request, "deo_deferred_repair_planning_payload_invalid")
        if replanned.effect_plan is None or replanned.effect_plan != canonical_plan:
            return self._failure(request, "deo_deferred_repair_replan_mismatch")

        call_id_by_source_effect: dict[str, str] = {}
        forward_invocations: list[ToolInvocation] = []
        rollback_invocations: list[ToolInvocation] = []
        effect_bindings_by_call_id: list[tuple[str, DeferredDirectorRepairEffectBindingV1]] = []
        for effect in canonical_plan.effects:
            binding = DeferredDirectorRepairEffectBindingV1(
                request_id=request.request_id,
                request_hash=request.request_hash,
                plan_hash=canonical_plan.plan_hash,
                effect=effect,
            )
            call_id = binding.tool_call_id
            call_id_by_source_effect[effect.call_id] = call_id
            effect_bindings_by_call_id.append((call_id, binding))
            invocation = ToolInvocation(
                call_id=ToolCallId(call_id),
                tool_name=effect.tool_name,
                raw_tool_name=effect.tool_name,
                arguments=dict(effect.arguments),
            )
            if effect.contingency_kind == "forward":
                forward_invocations.append(invocation)
            else:
                rollback_invocations.append(invocation)
        rollback_activation_by_call_id = tuple(
            (
                call_id_by_source_effect[effect.call_id],
                call_id_by_source_effect[str(effect.activates_after_call_id)],
            )
            for effect in canonical_plan.effects
            if effect.contingency_kind == "rollback"
        )
        return DeferredDirectorRepairSynthesisResultV1(
            ok=True,
            request_id=request.request_id,
            request_hash=request.request_hash,
            plan_hash=canonical_plan.plan_hash,
            forward_invocations=tuple(forward_invocations),
            rollback_invocations=tuple(rollback_invocations),
            rollback_activation_by_call_id=rollback_activation_by_call_id,
            effect_bindings_by_call_id=tuple(effect_bindings_by_call_id),
        )

    def synthesize_batch(
        self,
        requests: tuple[DeferredDirectorRepairRequestV1, ...],
        *,
        expected_workspace: str,
        expected_task_id: str,
        expected_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> tuple[DeferredDirectorRepairSynthesisResultV1, ...]:
        """Validate every request, then atomically spend all replay identities."""

        if not isinstance(requests, tuple) or not requests:
            raise TypeError("requests must be a non-empty immutable tuple")
        if not all(type(request) is DeferredDirectorRepairRequestV1 for request in requests):
            raise TypeError("requests must contain exact DeferredDirectorRepairRequestV1 values")
        request_ids = tuple(request.request_id for request in requests)
        if len(set(request_ids)) != len(request_ids):
            return tuple(
                self._failure(request, "deo_deferred_repair_request_identity_conflict") for request in requests
            )

        fence_status = self._replay_fence.check(request_ids)
        if fence_status is not None:
            error_code = (
                "deo_deferred_repair_request_replayed"
                if fence_status == "replayed"
                else "deo_deferred_repair_fence_capacity"
            )
            return tuple(self._failure(request, error_code) for request in requests)

        results = tuple(
            self._synthesize_candidate(
                request,
                expected_workspace=expected_workspace,
                expected_task_id=expected_task_id,
                expected_execution_attempt=expected_execution_attempt,
            )
            for request in requests
        )
        if any(not result.ok for result in results):
            return results

        owner_by_path: dict[str, str] = {}
        for request, result in zip(requests, results, strict=True):
            forward_paths = {
                binding.effect.target_path
                for _, binding in result.effect_bindings_by_call_id
                if binding.effect.contingency_kind == "forward"
            }
            for target_path in forward_paths:
                owner = owner_by_path.setdefault(target_path, request.request_id)
                if owner != request.request_id:
                    return tuple(self._failure(item, "deo_deferred_repair_target_conflict") for item in requests)

        fence_status = self._replay_fence.consume(request_ids)
        if fence_status is not None:
            error_code = (
                "deo_deferred_repair_request_replayed"
                if fence_status == "replayed"
                else "deo_deferred_repair_fence_capacity"
            )
            return tuple(self._failure(request, error_code) for request in requests)
        return results

    def synthesize(
        self,
        request: DeferredDirectorRepairRequestV1,
        *,
        expected_workspace: str,
        expected_task_id: str,
        expected_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DeferredDirectorRepairSynthesisResultV1:
        """Re-plan and atomically spend one exact request identity."""

        return self.synthesize_batch(
            (request,),
            expected_workspace=expected_workspace,
            expected_task_id=expected_task_id,
            expected_execution_attempt=expected_execution_attempt,
        )[0]
