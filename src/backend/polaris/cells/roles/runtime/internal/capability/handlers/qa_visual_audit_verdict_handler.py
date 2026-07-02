"""``qa_visual_audit_verdict`` capability handler.

Identity tuple::

    ("issue_visual_audit_verdict", "qa.audit_verdict", "RunVisualQaAuditCommandV1")

This is a verbatim extraction of the ``is_qa_visual_audit_verdict`` dispatcher arm of
``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface. The branch is NON-UNIFORM: before the owner-cell visual-audit call it
runs an ``llm.control_plane`` model-capability override/check sub-flow, so the
classic ``validate -> invoke -> map`` shape absorbs that divergence as follows:

* :meth:`validate` reproduces every pre-RPC payload rejection — the
  ``image_refs`` / ``criteria`` / ``evidence_paths`` payload guards
  (``invalid_visual_audit_image_refs``, ``invalid_visual_audit_criteria``,
  ``invalid_visual_audit_evidence_paths``), the model-capability override check
  (``visual_model_capability_override_denied``), and the
  :class:`CheckLlmModelCapabilityQueryV1` construction guard
  (``invalid_visual_model_capability_query``) — raising
  :class:`CapabilityInvocationError` (with the stable ``owner_cell`` /
  ``capability_available`` / ``metadata``) instead of returning a failure result.
* :meth:`invoke` runs the two RPC sub-flows exactly as the extracted branch: the
  ``llm.control_plane`` capability check (port
  ``deps.llm_control_plane_service.check_model_capability`` or the module-level
  ``check_llm_model_capability`` when the port is ``None``), the ``ok`` /
  ``supported`` gate (``visual_model_capability_missing``), then the
  ``qa.audit_verdict`` visual audit (port
  ``deps.qa_audit_service.run_visual_qa_audit`` or module-level
  ``run_visual_qa_audit``). It raises ``visual_model_capability_check_failed`` /
  ``invalid_visual_qa_audit_command`` / ``visual_qa_audit_failed`` on the matching
  downstream paths, and returns a :class:`_VisualAuditOutcome` carrying both the
  raw visual result and the ``model_metadata`` derived from the capability check
  (which :meth:`map_result` cannot recompute without re-issuing the RPC).
* :meth:`map_result` builds the success / missing-evidence / rejected
  :class:`RoleCapabilityInvocationResultV1` verbatim from that outcome.

The payload guards in :meth:`validate` are a pure function of ``command``, so
:meth:`invoke` re-derives the same validated values (``image_refs``,
``visual_criteria``, ``evidence_paths``, ``required_model_capability``) without
sharing mutable state; the ordering-sensitive model-capability check therefore
runs identically. ``map_result`` consumes only the :class:`_VisualAuditOutcome`
produced by :meth:`invoke`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import (
    _capability_available_metadata,
    _normalize_model_capability,
    _payload_mapping,
    _payload_string,
    _payload_string_tuple,
    _visual_audit_evidence_refs,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.llm.control_plane.public.contracts import (
        CheckLlmModelCapabilityQueryV1,
        LlmModelCapabilityResultV1,
    )
    from polaris.cells.qa.audit_verdict.public.contracts import VisualQaAuditResultV1
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )


@dataclass(frozen=True)
class _VisualAuditOutcome:
    """Carrier threading the invoke-time RPC state into :meth:`map_result`.

    ``model_metadata`` is computed from the (non-deterministic) model-capability
    check inside :meth:`invoke`, so it cannot be recomputed in :meth:`map_result`
    without re-issuing the RPC; it is carried alongside the raw visual result.
    """

    visual_result: object
    model_metadata: dict[str, Any]


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


def _validate_payload(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> tuple[tuple[str, ...], dict[str, Any], tuple[str, ...], str]:
    """Validate the payload + model-capability override (verbatim pre-RPC guards).

    Returns the validated ``(image_refs, visual_criteria, evidence_paths,
    required_model_capability)`` tuple, or raises
    :class:`CapabilityInvocationError` with the stable ``error_code`` literals.
    """
    image_refs = _payload_string_tuple(command.payload, "image_refs")
    if image_refs is None or not image_refs:
        raise CapabilityInvocationError(
            "payload.image_refs must be a non-empty sequence of image evidence refs",
            code="invalid_visual_audit_image_refs",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    visual_criteria = _payload_mapping(command.payload, "criteria")
    if visual_criteria is None:
        raise CapabilityInvocationError(
            "payload.criteria must be a mapping when provided",
            code="invalid_visual_audit_criteria",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    evidence_paths = _payload_string_tuple(command.payload, "evidence_paths")
    if evidence_paths is None:
        raise CapabilityInvocationError(
            "payload.evidence_paths must be a sequence of strings when provided",
            code="invalid_visual_audit_evidence_paths",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    required_model_capability = _normalize_model_capability(
        capability.metadata.get("required_model_capability"),
        "image_input",
    )
    requested_model_capability = _payload_string(command.payload, "required_model_capability")
    normalized_requested_model_capability = _normalize_model_capability(requested_model_capability, "")
    if normalized_requested_model_capability and normalized_requested_model_capability != required_model_capability:
        raise CapabilityInvocationError(
            (
                "visual QA audit requires "
                f"{required_model_capability!r}; payload requested "
                f"{normalized_requested_model_capability!r}"
            ),
            code="visual_model_capability_override_denied",
            owner_cell="llm.control_plane",
            capability_available=False,
            metadata=_capability_available_metadata(
                capability.capability_id,
                {
                    "required_capability": required_model_capability,
                    "requested_capability": normalized_requested_model_capability,
                },
            ),
        )
    return image_refs, visual_criteria, evidence_paths, required_model_capability


def _build_model_query(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
    required_model_capability: str,
) -> CheckLlmModelCapabilityQueryV1:
    """Construct the ``CheckLlmModelCapabilityQueryV1`` (verbatim).

    Raises :class:`CapabilityInvocationError` with ``invalid_visual_model_capability_query``
    on the extracted construction-guard path.
    """
    runtime_object = command.runtime_object
    invocation = command.invocation
    role_id = runtime_object.identity.role_id
    try:
        from polaris.cells.llm.control_plane.public.contracts import CheckLlmModelCapabilityQueryV1

        return CheckLlmModelCapabilityQueryV1(
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            role=role_id,
            capability=required_model_capability,
            model=_payload_string(command.payload, "model") or None,
            metadata={
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
                "payload_llm_role": _payload_string(command.payload, "llm_role"),
            },
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_visual_model_capability_query",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc


class QaVisualAuditVerdictHandler:
    """:class:`CapabilityHandler` for ``issue_visual_audit_verdict``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _validate_payload(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        runtime_object = command.runtime_object
        invocation = command.invocation
        capability = _resolve_capability(command)

        image_refs, visual_criteria, evidence_paths, required_model_capability = _validate_payload(command, capability)
        model_query = _build_model_query(command, capability, required_model_capability)

        llm_control_plane_service = deps.llm_control_plane_service
        try:
            if llm_control_plane_service is None:
                from polaris.cells.llm.control_plane.public.service import check_llm_model_capability

                model_capability: LlmModelCapabilityResultV1 = check_llm_model_capability(model_query)
            else:
                model_capability = cast(
                    "LlmModelCapabilityResultV1",
                    llm_control_plane_service.check_model_capability(model_query),
                )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="visual_model_capability_check_failed",
                owner_cell="llm.control_plane",
                capability_available=True,
            ) from exc

        # ``model_query.capability`` is, by construction, ``required_model_capability``;
        # the extracted branch reads ``model_query.capability`` here — using the typed
        # local is byte-identical and keeps ``model_query`` opaque (zero ``Any``).
        model_metadata: dict[str, Any] = {
            "model_capability_supported": bool(getattr(model_capability, "supported", False)),
            "required_capability": required_model_capability,
            "model_capability_ref": getattr(model_capability, "capability_ref", ""),
            "model_provider_id": getattr(model_capability, "provider_id", ""),
            "model": getattr(model_capability, "model", ""),
            "model_reason": getattr(model_capability, "reason", ""),
        }
        if not bool(getattr(model_capability, "ok", False)) or not bool(getattr(model_capability, "supported", False)):
            raise CapabilityInvocationError(
                getattr(model_capability, "reason", "") or "configured model does not support image_input",
                code="visual_model_capability_missing",
                owner_cell="llm.control_plane",
                capability_available=False,
                metadata=_capability_available_metadata(capability.capability_id, model_metadata),
            )

        visual_criteria.update(
            {
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
                "model_provider_id": getattr(model_capability, "provider_id", ""),
                "model": getattr(model_capability, "model", ""),
            }
        )
        try:
            from polaris.cells.qa.audit_verdict.public.contracts import RunVisualQaAuditCommandV1

            visual_command = RunVisualQaAuditCommandV1(
                task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                run_id=_payload_string(command.payload, "run_id", runtime_object.identity.run_id or "") or None,
                image_refs=image_refs,
                model_capability_ref=str(getattr(model_capability, "capability_ref", "")),
                criteria=visual_criteria,
                evidence_paths=evidence_paths,
            )
        except (TypeError, ValueError) as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="invalid_visual_qa_audit_command",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc

        qa_audit_service = deps.qa_audit_service
        try:
            if qa_audit_service is None:
                from polaris.cells.qa.audit_verdict.public.service import run_visual_qa_audit

                visual_result: VisualQaAuditResultV1 = run_visual_qa_audit(visual_command)
            else:
                visual_result = cast(
                    "VisualQaAuditResultV1",
                    qa_audit_service.run_visual_qa_audit(visual_command),
                )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="visual_qa_audit_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc

        return _VisualAuditOutcome(visual_result=visual_result, model_metadata=model_metadata)

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        outcome = cast("_VisualAuditOutcome", raw)
        visual_result = cast("VisualQaAuditResultV1", outcome.visual_result)
        model_metadata = outcome.model_metadata
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)

        result_ref = f"qa.audit_verdict:visual-verdict:{visual_result.task_id}"
        target_evidence_refs = tuple(getattr(visual_result, "evidence_refs", ()) or ())
        audit_evidence_refs = _visual_audit_evidence_refs(target_evidence_refs)
        metadata = _capability_available_metadata(
            capability.capability_id,
            {
                **model_metadata,
                "verdict": visual_result.verdict,
                "score": visual_result.score,
                "image_refs": tuple(visual_result.image_refs),
                "finding_count": len(visual_result.findings),
                "findings": tuple(finding.summary for finding in visual_result.findings),
                "evidence_refs": target_evidence_refs,
                "audit_evidence_refs": audit_evidence_refs,
            },
        )
        if visual_result.ok and not audit_evidence_refs:
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
                task_id=visual_result.task_id,
                status="EVIDENCE_MISSING",
                metadata={
                    **metadata,
                    "owner_cell": capability.owner_cell,
                    "evidence_owner_cell": "audit.evidence",
                },
                error_code="visual_qa_audit_missing_evidence_ref",
                error_message="visual QA audit success must include an audit.evidence evidence ref",
            )
        if not visual_result.ok:
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
                task_id=visual_result.task_id,
                status=visual_result.verdict,
                evidence_refs=audit_evidence_refs,
                metadata=metadata,
                error_code="visual_qa_audit_rejected",
                error_message="; ".join(finding.summary for finding in visual_result.findings)
                or "visual QA audit rejected the task",
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
            task_id=visual_result.task_id,
            status=visual_result.verdict,
            evidence_refs=audit_evidence_refs,
            metadata=metadata,
        )
