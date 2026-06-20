"""``qa_pytest_verification`` capability handler.

Identity tuple::

    ("invoke_container_pytest", "factory.verification_guard", "VerifyCompletionCommandV1")

This is a VERBATIM re-shaping of the legacy ``is_qa_pytest_verification`` arm of
``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface:

* :meth:`validate` reproduces the five pre-invoke rejection paths — the four
  payload-shape guards (``invalid_verification_commands``,
  ``invalid_verification_evidence_paths``, ``invalid_verification_allowed_commands``,
  ``invalid_verification_metadata``) and the :class:`VerifyCompletionCommandV1`
  construction guard (``invalid_verification_command``) — raising
  :class:`CapabilityInvocationError` instead of returning a failure result.
* :meth:`invoke` performs the verification exactly as the legacy branch:
  ``deps.verification_guard_service.verify_completion`` when the port is set, else
  the ``factory.verification_guard`` module-level public function when the port is
  ``None``; it raises ``verification_guard_failed`` on any downstream exception.
* :meth:`map_result` builds the success / ``verification_failed`` not-ok
  :class:`RoleCapabilityInvocationResultV1` verbatim, including the plain metadata
  bundle (this branch does NOT wrap metadata in ``_capability_available_metadata``).

The verification command is a pure function of ``command.payload`` (helper
:func:`_build_verification_command`), so :meth:`validate`, :meth:`invoke`, and
:meth:`map_result` rebuild it identically without sharing mutable state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import (
    _payload_mapping,
    _payload_string,
    _payload_string_tuple,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.factory.verification_guard.public.contracts import (
        VerifyCompletionCommandV1,
        VerifyCompletionResultV1,
    )
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


def _build_verification_command(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> VerifyCompletionCommandV1:
    """Construct the ``VerifyCompletionCommandV1`` from ``command``.

    Mirrors the legacy branch's payload-shape validation + metadata-mutation +
    command construction statements byte-for-byte. Raises
    :class:`CapabilityInvocationError` with the legacy ``error_code`` literals on
    the five pre-invoke rejection paths.
    """
    runtime_object = command.runtime_object
    invocation = command.invocation

    verification_commands = _payload_string_tuple(command.payload, "verification_commands")
    if verification_commands is None or not verification_commands:
        raise CapabilityInvocationError(
            "payload.verification_commands must be a non-empty sequence of strings",
            code="invalid_verification_commands",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    evidence_paths = _payload_string_tuple(command.payload, "evidence_paths")
    if evidence_paths is None:
        raise CapabilityInvocationError(
            "payload.evidence_paths must be a sequence of strings when provided",
            code="invalid_verification_evidence_paths",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    allowed_commands = _payload_string_tuple(command.payload, "allowed_commands")
    if allowed_commands is None:
        raise CapabilityInvocationError(
            "payload.allowed_commands must be a sequence of strings when provided",
            code="invalid_verification_allowed_commands",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    claim_metadata = _payload_mapping(command.payload, "metadata")
    if claim_metadata is None:
        raise CapabilityInvocationError(
            "payload.metadata must be a mapping when provided",
            code="invalid_verification_metadata",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    claim_metadata.update(
        {
            "role_invocation_id": invocation.invocation_id,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
            "role_capability_id": capability.capability_id,
        }
    )
    try:
        from polaris.cells.factory.verification_guard.public.contracts import (
            VerificationClaim,
            VerifyCompletionCommandV1,
        )

        verification_command = VerifyCompletionCommandV1(
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            claim=VerificationClaim(
                claim_id=_payload_string(command.payload, "claim_id", invocation.invocation_id),
                claimed_outcome=_payload_string(command.payload, "claimed_outcome", "pytest verification"),
                verification_commands=verification_commands,
                evidence_paths=evidence_paths,
                timeout_seconds=int(command.payload.get("timeout_seconds", 120)),
                metadata=claim_metadata,
            ),
            strict_mode=bool(command.payload.get("strict_mode", True)),
            allowed_commands=allowed_commands or None,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_verification_command",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return verification_command


class QaPytestVerificationHandler:
    """:class:`CapabilityHandler` for ``invoke_container_pytest``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _build_verification_command(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        verification_command = _build_verification_command(command, capability)

        verification_guard_service = deps.verification_guard_service
        try:
            if verification_guard_service is None:
                from polaris.cells.factory.verification_guard.public.service import verify_completion

                verification_result: VerifyCompletionResultV1 = verify_completion(verification_command)
            else:
                verification_result = cast(
                    "VerifyCompletionResultV1",
                    verification_guard_service.verify_completion(verification_command),
                )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="verification_guard_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return verification_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        from polaris.cells.factory.verification_guard.public.contracts import VerificationStatus

        verification_result = cast("VerifyCompletionResultV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)
        verification_command = _build_verification_command(command, capability)

        report = verification_result.report
        status = report.status.name if report is not None else "ERROR"
        result_ref = f"factory.verification_guard:report:{verification_command.claim.claim_id}"
        metadata: dict[str, Any] = {
            "verification_ok": verification_result.ok,
            "verification_status": status,
            "execution_summary": report.execution_summary if report is not None else "",
            "command_count": len(report.command_results) if report is not None else 0,
        }
        if report is not None:
            metadata["evidence_collected"] = tuple(report.evidence_collected)
            metadata["evidence_missing"] = tuple(report.evidence_missing)
            metadata["mismatch_details"] = tuple(report.mismatch_details)
        if not verification_result.ok or report is None or report.status != VerificationStatus.PASS:
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
                task_id=runtime_object.identity.task_id or "",
                status=status,
                metadata=metadata,
                error_code="verification_failed",
                error_message=verification_result.error_message
                or (report.execution_summary if report is not None else "verification failed"),
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
            task_id=runtime_object.identity.task_id or "",
            status=status,
            metadata=metadata,
        )
