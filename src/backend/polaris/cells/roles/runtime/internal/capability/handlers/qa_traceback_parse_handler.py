"""``qa_traceback_parse`` capability handler.

Identity tuple::

    ("parse_traceback_frames", "qa.audit_verdict", "ParseTracebackFramesCommandV1")

This is a VERBATIM re-shaping of the legacy ``is_qa_traceback_parse`` arm of
``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface:

* :meth:`validate` reproduces the three pre-invoke rejection paths — the
  ``metadata`` mapping check (``invalid_traceback_metadata``), the non-empty
  ``traceback_text`` check (``invalid_traceback_text``), and the
  :class:`ParseTracebackFramesCommandV1` construction guard
  (``invalid_traceback_parse_command``) — raising
  :class:`CapabilityInvocationError` instead of returning a failure result.
* :meth:`invoke` performs the traceback parse exactly as the legacy branch:
  ``deps.qa_audit_service`` when set, else the ``qa.audit_verdict`` module-level
  public function when the port is ``None``; it raises ``traceback_parse_failed``
  on any downstream exception.
* :meth:`map_result` builds the success (``PARSED``) / not-ok (``REJECTED``)
  :class:`RoleCapabilityInvocationResultV1` verbatim, surfacing
  ``traceback_parse_rejected`` on the not-ok path.

The command construction is a pure function of ``command`` (helper
:func:`_build_parse_command`), so :meth:`validate` and :meth:`invoke` rebuild it
identically without sharing mutable state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import (
    _capability_available_metadata,
    _payload_mapping,
    _payload_string,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.qa.audit_verdict.public.contracts import (
        ParseTracebackFramesCommandV1,
        ParseTracebackFramesResultV1,
    )
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


def _build_parse_command(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> ParseTracebackFramesCommandV1:
    """Construct the ``ParseTracebackFramesCommandV1`` from ``command``.

    Mirrors the legacy branch's metadata/text guards + metadata-mutation +
    command construction statements byte-for-byte. Raises
    :class:`CapabilityInvocationError` with the legacy ``error_code`` literals on
    the three pre-invoke rejection paths.
    """
    runtime_object = command.runtime_object
    invocation = command.invocation

    traceback_metadata = _payload_mapping(command.payload, "metadata")
    if traceback_metadata is None:
        raise CapabilityInvocationError(
            "payload.metadata must be a mapping when provided",
            code="invalid_traceback_metadata",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    traceback_text = _payload_string(command.payload, "traceback_text")
    if not traceback_text:
        raise CapabilityInvocationError(
            "payload.traceback_text must be a non-empty string",
            code="invalid_traceback_text",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    traceback_metadata.update(
        {
            "role_invocation_id": invocation.invocation_id,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
            "role_capability_id": capability.capability_id,
        }
    )
    try:
        from polaris.cells.qa.audit_verdict.public.contracts import ParseTracebackFramesCommandV1

        parse_command = ParseTracebackFramesCommandV1(
            task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            traceback_text=traceback_text,
            run_id=_payload_string(command.payload, "run_id", runtime_object.identity.run_id or "") or None,
            metadata=traceback_metadata,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_traceback_parse_command",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return parse_command


class QaTracebackParseHandler:
    """:class:`CapabilityHandler` for ``parse_traceback_frames``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _build_parse_command(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        parse_command = _build_parse_command(command, capability)

        qa_audit_service = deps.qa_audit_service
        try:
            if qa_audit_service is None:
                from polaris.cells.qa.audit_verdict.public.service import parse_traceback_frames

                parse_result: ParseTracebackFramesResultV1 = parse_traceback_frames(parse_command)
            else:
                parse_result = cast(
                    "ParseTracebackFramesResultV1",
                    qa_audit_service.parse_traceback_frames(parse_command),
                )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="traceback_parse_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return parse_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        parse_result = cast("ParseTracebackFramesResultV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)

        signal = parse_result.signal
        result_ref = f"qa.audit_verdict:failure-signal:{signal.signal_id}"
        metadata: dict[str, Any] = _capability_available_metadata(
            capability.capability_id,
            {
                "signal_id": signal.signal_id,
                "signal_type": signal.signal_type,
                "summary": signal.summary,
                "severity": signal.severity,
                "source": signal.source,
                "frame_count": parse_result.frame_count,
                "frames": tuple(
                    {
                        "path": frame.path,
                        "line": frame.line,
                        "function": frame.function,
                        "code": frame.code,
                    }
                    for frame in signal.frames
                ),
            },
        )
        if not parse_result.ok:
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
                task_id=parse_result.task_id,
                status="REJECTED",
                metadata=metadata,
                error_code="traceback_parse_rejected",
                error_message=signal.summary,
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
            task_id=parse_result.task_id,
            status="PARSED",
            metadata=metadata,
        )
