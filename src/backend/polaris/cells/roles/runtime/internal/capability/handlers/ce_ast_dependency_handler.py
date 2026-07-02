"""``ce_ast_dependency`` capability handler.

Identity tuple::

    ("verify_ast_dependency", "code_intelligence.engine", "VerifyAstDependencyQueryV1")

This is a verbatim extraction of the ``is_ce_ast_dependency`` dispatcher arm of
``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface:

* :meth:`validate` reproduces the two pre-invoke rejection paths — the
  ``metadata`` mapping check (``invalid_ast_dependency_metadata``) and the
  :class:`VerifyAstDependencyQueryV1` construction guard
  (``invalid_ast_dependency_query``) — raising :class:`CapabilityInvocationError`
  instead of returning a failure result.
* :meth:`invoke` performs the AST dependency verification exactly as the extracted
  branch: ``deps.code_intelligence_service.verify_ast_dependency`` when the port
  is set, else the ``code_intelligence.engine`` module-level public function when
  the port is ``None``; it raises ``ast_dependency_verification_failed`` on any
  downstream exception.
* :meth:`map_result` builds the success / not-ok :class:`RoleCapabilityInvocationResultV1`
  verbatim, surfacing the engine's ``error`` on the not-ok path.

The query construction is a pure function of ``command`` (helper
:func:`_build_ast_query`), so :meth:`validate` and :meth:`invoke` rebuild it
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
    from polaris.cells.code_intelligence.engine.public.contracts import (
        AstDependencyVerificationResultV1,
        VerifyAstDependencyQueryV1,
    )
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


def _build_ast_query(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> VerifyAstDependencyQueryV1:
    """Construct the ``VerifyAstDependencyQueryV1`` from ``command``.

    Mirrors the extracted branch's metadata-mutation + query construction statements
    byte-for-byte. Raises :class:`CapabilityInvocationError` with the stable
    ``error_code`` literals on the two pre-invoke rejection paths.
    """
    runtime_object = command.runtime_object
    invocation = command.invocation

    ast_metadata = _payload_mapping(command.payload, "metadata")
    if ast_metadata is None:
        raise CapabilityInvocationError(
            "payload.metadata must be a mapping when provided",
            code="invalid_ast_dependency_metadata",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    ast_metadata.update(
        {
            "role_invocation_id": invocation.invocation_id,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
            "role_capability_id": capability.capability_id,
        }
    )
    try:
        from polaris.cells.code_intelligence.engine.public.contracts import VerifyAstDependencyQueryV1

        ast_query = VerifyAstDependencyQueryV1(
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            path=_payload_string(command.payload, "path") or _payload_string(command.payload, "file"),
            language=_payload_string(command.payload, "language"),
            symbol=_payload_string(command.payload, "symbol") or _payload_string(command.payload, "name"),
            kind=_payload_string(command.payload, "kind") or None,
            max_results=int(command.payload.get("max_results", 10)),
            context_radius=int(command.payload.get("context_radius", 5)),
            fuzzy=bool(command.payload.get("fuzzy", True)),
            metadata=ast_metadata,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_ast_dependency_query",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return ast_query


class CeAstDependencyHandler:
    """:class:`CapabilityHandler` for ``verify_ast_dependency``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _build_ast_query(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        ast_query = _build_ast_query(command, capability)

        code_intelligence_service = deps.code_intelligence_service
        try:
            if code_intelligence_service is None:
                from polaris.cells.code_intelligence.engine.public.service import verify_ast_dependency

                ast_result: AstDependencyVerificationResultV1 = verify_ast_dependency(ast_query)
            else:
                ast_result = cast(
                    "AstDependencyVerificationResultV1",
                    code_intelligence_service.verify_ast_dependency(ast_query),
                )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="ast_dependency_verification_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return ast_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        ast_result = cast("AstDependencyVerificationResultV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)

        result_ref = f"code_intelligence.engine:ast-dependency:{invocation.invocation_id}"
        metadata: dict[str, Any] = _capability_available_metadata(
            capability.capability_id,
            {
                "workspace": ast_result.workspace,
                "path": ast_result.path,
                "language": ast_result.language,
                "symbol": ast_result.symbol,
                "engine": ast_result.engine,
                "result_count": ast_result.result_count,
                "results": tuple(dict(item) for item in ast_result.results),
                "warnings": tuple(ast_result.warnings),
            },
        )
        if not ast_result.ok:
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
                status="FAILED",
                metadata=metadata,
                error_code="ast_dependency_verification_failed",
                error_message=ast_result.error or "AST dependency verification failed",
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
            status="VERIFIED" if ast_result.result_count else "NO_MATCH",
            metadata=metadata,
        )
