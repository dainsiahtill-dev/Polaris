"""``qa_audit_verdict`` capability handler.

Identity tuple::

    ("issue_audit_verdict", "qa.audit_verdict", "RunQaAuditCommandV1")

This is a verbatim extraction of the ``is_qa_audit_verdict`` dispatcher arm of
``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface:

* :meth:`validate` reproduces the three pre-invoke rejection paths — the
  ``criteria`` mapping check (``invalid_qa_audit_criteria``), the
  ``evidence_paths`` sequence check (``invalid_qa_audit_evidence_paths``), and the
  :class:`RunQaAuditCommandV1` construction guard (``invalid_qa_audit_command``) —
  raising :class:`CapabilityInvocationError` instead of returning a failure
  result.
* :meth:`invoke` performs the QA audit exactly as the extracted branch:
  ``deps.qa_audit_service.run_qa_audit`` when the port is set, else the
  ``qa.audit_verdict`` module-level public function when the port is ``None``; it
  raises ``qa_audit_failed`` on any downstream exception.
* :meth:`map_result` builds the success / not-ok
  :class:`RoleCapabilityInvocationResultV1` verbatim, surfacing
  ``qa_audit_rejected`` on the not-ok path.

The command construction is a pure function of ``command`` (helper
:func:`_build_qa_audit_command`), so :meth:`validate` and :meth:`invoke` rebuild
it identically without sharing mutable state; :meth:`map_result` recomputes the
evidence refs deterministically from the payload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import (
    _audit_evidence_refs,
    _capability_available_metadata,
    _payload_mapping,
    _payload_string,
    _payload_string_tuple,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.qa.audit_verdict.public.contracts import (
        QaAuditResultV1,
        RunQaAuditCommandV1,
    )
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )


def _build_qa_audit_command(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> tuple[RunQaAuditCommandV1, tuple[str, ...]]:
    """Construct the ``RunQaAuditCommandV1`` + evidence paths from ``command``.

    Mirrors the extracted branch's payload validation, criteria-mutation and command
    construction statements byte-for-byte. Raises :class:`CapabilityInvocationError`
    with the stable ``error_code`` literals on the three pre-invoke rejection paths.
    """
    runtime_object = command.runtime_object
    invocation = command.invocation

    audit_criteria = _payload_mapping(command.payload, "criteria")
    if audit_criteria is None:
        raise CapabilityInvocationError(
            "payload.criteria must be a mapping when provided",
            code="invalid_qa_audit_criteria",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    evidence_paths = _payload_string_tuple(command.payload, "evidence_paths")
    if evidence_paths is None:
        raise CapabilityInvocationError(
            "payload.evidence_paths must be a sequence of strings when provided",
            code="invalid_qa_audit_evidence_paths",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    audit_criteria.update(
        {
            "role_invocation_id": invocation.invocation_id,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
            "role_capability_id": capability.capability_id,
        }
    )
    try:
        from polaris.cells.qa.audit_verdict.public.contracts import RunQaAuditCommandV1

        audit_command = RunQaAuditCommandV1(
            task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            run_id=_payload_string(command.payload, "run_id", runtime_object.identity.run_id or "") or None,
            criteria=audit_criteria,
            evidence_paths=evidence_paths,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_qa_audit_command",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return audit_command, evidence_paths


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


class QaAuditVerdictHandler:
    """:class:`CapabilityHandler` for ``issue_audit_verdict``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _build_qa_audit_command(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        audit_command, _ = _build_qa_audit_command(command, capability)

        qa_audit_service = deps.qa_audit_service
        try:
            if qa_audit_service is None:
                from polaris.cells.qa.audit_verdict.public.service import run_qa_audit

                audit_result: QaAuditResultV1 = run_qa_audit(audit_command)
            else:
                audit_result = cast("QaAuditResultV1", qa_audit_service.run_qa_audit(audit_command))
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="qa_audit_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return audit_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        audit_result = cast("QaAuditResultV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)
        evidence_paths = _payload_string_tuple(command.payload, "evidence_paths") or ()

        result_ref = f"qa.audit_verdict:verdict:{audit_result.task_id}"
        audit_evidence_refs = _audit_evidence_refs(evidence_paths)
        qa_metadata = dict(getattr(audit_result, "metadata", {}) or {})
        metadata: dict[str, Any] = _capability_available_metadata(
            capability.capability_id,
            {
                "verdict": audit_result.verdict,
                "score": audit_result.score,
                "findings": tuple(audit_result.findings),
                "suggestions": tuple(audit_result.suggestions),
                "evidence_paths": evidence_paths,
                "audit_evidence_refs": audit_evidence_refs,
                "failure_class": str(qa_metadata.get("failure_class") or ""),
                "responsible_layer": str(qa_metadata.get("responsible_layer") or ""),
                "repairable_by_director": bool(qa_metadata.get("repairable_by_director")),
                "qa_verdict_content_hash": str(qa_metadata.get("qa_verdict_content_hash") or ""),
            },
        )
        envelope = qa_metadata.get("qa_verdict_envelope")
        if isinstance(envelope, dict):
            metadata["qa_verdict_envelope"] = dict(envelope)
        if not audit_result.ok:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=audit_result.task_id,
                status=audit_result.verdict,
                evidence_refs=audit_evidence_refs,
                metadata=metadata,
                error_code="qa_audit_rejected",
                error_message="; ".join(audit_result.findings) or "QA audit rejected the task",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=audit_result.task_id,
            status=audit_result.verdict,
            evidence_refs=audit_evidence_refs,
            metadata=metadata,
        )
