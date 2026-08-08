"""Composition adapter for workflow-runtime model-ceiling owner evidence.

Only direct owner queries are admissible.  roles.kernel currently exposes the
provider-attempt lifecycle replay needed to verify physical calls.  The other
owners do not yet expose a round-identity lookup joining material effects,
execution_broker verifier receipts/diagnostics, and director.runtime coverage.
Until that API exists this adapter deliberately fails closed; generic receipt
records are never accepted as substitute authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.context.engine.public.contracts import (
    FinalProviderRequestAuditResultV1,
    QueryFinalProviderRequestAuditV1,
)
from polaris.cells.context.engine.public.service import query_final_provider_request_audit
from polaris.cells.orchestration.workflow_runtime.public.model_ceiling import (
    ModelCeilingCandidateV1,
    ModelCeilingOwnerObservationError,
    ModelCeilingOwnerObservationV1,
)
from polaris.cells.orchestration.workflow_runtime.public.model_ceiling_bootstrap import (
    bind_model_ceiling_owner_observation_port,
)
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
    FactoryProviderAttemptLifecycleReplayFactV1,
    FactoryProviderAttemptLifecycleReplaySnapshotV1,
    QueryFactoryProviderAttemptLifecycleReplayV1,
    query_factory_provider_attempt_lifecycle_replay,
)

MODEL_CEILING_REQUIRED_OWNER_API_GAPS: tuple[str, ...] = (
    "runtime.execution_broker.query_verification_round_by_factory_call",
    "runtime.execution_broker.query_material_effect_round_by_factory_call",
    "director.runtime.query_repair_coverage_by_owner_verifier_diagnostic",
)
_LOWER_HEX = frozenset("0123456789abcdef")


def _fail(code: str, message: str) -> ModelCeilingOwnerObservationError:
    return ModelCeilingOwnerObservationError(code, message)


def _mapping(value: object, *, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code, "Expected an evidence mapping")
    return value


def _text(value: object, *, code: str) -> str:
    if type(value) is not str or not value.strip():
        raise _fail(code, "Expected a non-empty exact string")
    return value.strip()


def _string_tuple(value: object, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _fail(code, "Expected a string sequence")
    result = tuple(_text(item, code=code) for item in value)
    if len(set(result)) != len(result):
        raise _fail(code, "Evidence sequence contains duplicates")
    return result


def _sha256(value: object, *, code: str) -> str:
    token = _text(value, code=code)
    if len(token) != 64 or any(character not in _LOWER_HEX for character in token):
        raise _fail(code, "Expected a 64-character lowercase sha256 value")
    return token


def _tool_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _fail("final_request_tools_invalid", "Final provider tools must be a sequence")
    names: list[str] = []
    for item in value:
        row = _mapping(item, code="final_request_tools_invalid")
        function = row.get("function")
        raw_name = function.get("name") if isinstance(function, Mapping) else row.get("name")
        name = _text(raw_name, code="final_request_tools_invalid")
        if name not in names:
            names.append(name)
    return tuple(names)


class WorkflowRuntimeModelCeilingOwnerAdapter:
    """Read direct owner facts and park until a complete round join exists."""

    @staticmethod
    def _read_final_request(candidate: ModelCeilingCandidateV1) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        try:
            result = query_final_provider_request_audit(
                QueryFinalProviderRequestAuditV1(
                    workspace=candidate.workspace,
                    context_snapshot_ref=candidate.final_request_snapshot_ref,
                )
            )
        except Exception as exc:
            raise _fail("context_snapshot_read_failed", f"ContextOS final request read failed: {exc}") from exc
        if type(result) is not FinalProviderRequestAuditResultV1:
            raise _fail("context_snapshot_wrong_type", "ContextOS returned a non-canonical result")
        if (
            not result.ok
            or result.status != "available"
            or result.workspace != candidate.workspace
            or result.context_snapshot_ref != candidate.final_request_snapshot_ref
        ):
            raise _fail("context_snapshot_identity_mismatch", "ContextOS snapshot is unavailable or mismatched")
        payload = _mapping(result.payload, code="final_request_payload_invalid")
        if payload.get("context_hash") != candidate.final_request_snapshot_ref:
            raise _fail("context_snapshot_identity_mismatch", "Payload context hash differs from candidate")
        if payload.get("call_id") != candidate.provider_call_id:
            raise _fail("context_snapshot_call_identity_mismatch", "ContextOS call id differs from candidate")
        final_audit = _mapping(payload.get("final_request_context_audit"), code="final_request_audit_missing")
        coverage = _mapping(
            final_audit.get("final_request_evidence_coverage"),
            code="final_request_coverage_missing",
        )
        role_id = _text(payload.get("role"), code="final_request_role_missing")
        if (
            coverage.get("role_id") != role_id
            or coverage.get("expected_role_id") != role_id
            or coverage.get("role_identity_ok") is not True
            or coverage.get("pass") is not True
        ):
            raise _fail("final_request_role_or_coverage_invalid", "Final request role or coverage did not pass")
        _sha256(coverage.get("request_hash"), code="final_request_hash_invalid")
        required_refs = _string_tuple(coverage.get("required_refs"), code="final_request_refs_invalid")
        included_refs = _string_tuple(coverage.get("included_refs"), code="final_request_refs_invalid")
        missing_refs = _string_tuple(coverage.get("missing_required_refs"), code="final_request_refs_invalid")
        if missing_refs or not set(required_refs).issubset(included_refs):
            raise _fail("final_request_refs_incomplete", "Required final request evidence refs are missing")
        required_tools = _string_tuple(coverage.get("required_tools"), code="final_request_tools_invalid")
        available_tools = _string_tuple(coverage.get("available_tools"), code="final_request_tools_invalid")
        missing_tools = _string_tuple(coverage.get("missing_required_tools"), code="final_request_tools_invalid")
        provider_tools = _tool_names(payload.get("tools"))
        if (
            missing_tools
            or not set(required_tools).issubset(available_tools)
            or not set(required_tools).issubset(provider_tools)
        ):
            raise _fail("final_request_tools_incomplete", "Required final request tools are missing")
        return payload, coverage

    @staticmethod
    def _read_provider_lifecycle(
        candidate: ModelCeilingCandidateV1,
    ) -> tuple[FactoryProviderAttemptLifecycleReplayFactV1, ...]:
        try:
            snapshot = query_factory_provider_attempt_lifecycle_replay(
                QueryFactoryProviderAttemptLifecycleReplayV1(
                    schema_version=QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
                    workspace=candidate.workspace,
                    factory_run_id=candidate.factory_run_id,
                )
            )
        except Exception as exc:
            raise _fail("provider_attempt_lifecycle_query_failed", f"roles.kernel replay failed: {exc}") from exc
        if type(snapshot) is not FactoryProviderAttemptLifecycleReplaySnapshotV1:
            raise _fail("provider_attempt_lifecycle_wrong_type", "roles.kernel returned a non-canonical replay")
        if snapshot.workspace != candidate.workspace or snapshot.factory_run_id != candidate.factory_run_id:
            raise _fail("provider_attempt_lifecycle_identity_mismatch", "roles.kernel replay identity differs")
        facts = tuple(fact for fact in snapshot.facts if fact.call_id == candidate.provider_call_id)
        if not facts:
            raise _fail("provider_attempt_lifecycle_call_missing", "No lifecycle facts exist for provider call")
        terminals = tuple(fact for fact in facts if fact.phase == "terminal")
        if not terminals:
            raise _fail("provider_attempt_terminal_missing", "Provider call has no terminal lifecycle fact")
        if any(fact.run_id != candidate.run_id for fact in facts):
            raise _fail("provider_attempt_lifecycle_run_mismatch", "Provider lifecycle run identity differs")
        if any(fact.authority_attempt_ordinal > fact.attempt_budget for fact in terminals):
            raise _fail("provider_attempt_budget_invalid", "Provider attempt ordinal exceeds its authority budget")
        return terminals

    def observe_model_ceiling(self, candidate: ModelCeilingCandidateV1) -> ModelCeilingOwnerObservationV1:
        if type(candidate) is not ModelCeilingCandidateV1:
            raise TypeError("candidate must be an exact ModelCeilingCandidateV1")

        payload, coverage = self._read_final_request(candidate)
        role_id = _text(payload.get("role"), code="final_request_role_missing")
        provider_id = _text(payload.get("provider_id"), code="final_request_provider_missing")
        model = _text(payload.get("model"), code="final_request_model_missing")
        terminals = self._read_provider_lifecycle(candidate)
        last_terminal = max(terminals, key=lambda fact: fact.logical_sequence)
        if (
            last_terminal.role != role_id
            or last_terminal.provider != provider_id
            or last_terminal.model != model
            or last_terminal.context_snapshot_ref != candidate.final_request_snapshot_ref
        ):
            raise _fail(
                "provider_attempt_final_request_identity_mismatch",
                "roles.kernel terminal event is not bound to the final provider request",
            )
        if last_terminal.terminal_status != "completed":
            raise _fail("provider_attempt_not_completed", "Final provider attempt did not complete")
        _sha256(coverage.get("request_hash"), code="final_request_hash_invalid")

        # The remaining join cannot be reconstructed safely from hashes.  In
        # particular, query_project_verification_receipt requires the complete
        # command authority and input-artifact snapshot, while no owner API can
        # resolve those values from factory_run/call/round.  director.runtime
        # coverage likewise needs the owner verifier diagnostic, not caller
        # text.  Parking here prevents generic receipt forgery from becoming a
        # project stop condition.
        gap_list = ", ".join(MODEL_CEILING_REQUIRED_OWNER_API_GAPS)
        raise _fail("model_ceiling_round_owner_query_unavailable", f"Missing owner query APIs: {gap_list}")


_OWNER_ADAPTER = WorkflowRuntimeModelCeilingOwnerAdapter()


def configure_workflow_runtime_model_ceiling_owner() -> None:
    """Bind the singleton adapter during application composition."""

    bind_model_ceiling_owner_observation_port(_OWNER_ADAPTER)


__all__ = [
    "MODEL_CEILING_REQUIRED_OWNER_API_GAPS",
    "WorkflowRuntimeModelCeilingOwnerAdapter",
    "configure_workflow_runtime_model_ceiling_owner",
]
