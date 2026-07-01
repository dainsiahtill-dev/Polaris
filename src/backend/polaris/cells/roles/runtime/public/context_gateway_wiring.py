"""Context-gateway asset-reader wiring for `roles.runtime` cell.

Lossless split: this module holds the §8 helpers that mount role-owned asset
reader ports (CE blueprint status, QA verdict history, and Resident AGI
capability/decision evidence) for kernel-owned context signal assembly. The
cross-cell reads go through the owner cells' PUBLIC contracts
(ACGA-compliant), or through evidence already carried by the current role turn,
and the imports stay lazy/in-body to avoid import cycles.

Monkeypatch contract (load-bearing): existing tests patch the reader functions
on the ``service`` module namespace (``service._read_blueprint_status_for_context``
/ ``service._read_qa_verdict_for_context``) and expect
``_build_context_gateway_config_for_role`` to mount the *patched* versions. To
preserve that, the config factory resolves the reader callables through the
``service`` module object at call time rather than binding them from this
module's namespace.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from typing import Any, cast

from polaris.cells.roles.kernel.public.service import ContextGatewayConfig
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest

_SERVICE_MODULE = "polaris.cells.roles.runtime.public.service"


def _read_blueprint_status_for_context(task_id: str, workspace: str) -> Any | None:
    """Read CE blueprint status through the owner Cell public contract."""
    task_token = str(task_id or "").strip()
    if not task_token:
        return None
    workspace_token = str(workspace or "").strip() or "."
    from polaris.cells.chief_engineer.blueprint.public import GetBlueprintStatusQueryV1, get_blueprint_status

    return get_blueprint_status(GetBlueprintStatusQueryV1(task_id=task_token, workspace=workspace_token))


def _read_qa_verdict_for_context(task_id: str, workspace: str) -> Any | None:
    """Read QA verdict history through the owner Cell public contract."""
    task_token = str(task_id or "").strip()
    if not task_token:
        return None
    workspace_token = str(workspace or "").strip() or "."
    from polaris.cells.qa.audit_verdict.public import GetQaVerdictQueryV1, get_qa_verdict

    return get_qa_verdict(GetQaVerdictQueryV1(task_id=task_token, workspace=workspace_token))


def _resolve_reader(name: str) -> Callable[[str, str], Any]:
    """Resolve a reader callable from the ``service`` module namespace.

    Resolved at call time so test monkeypatches applied to the ``service``
    module (the historical patch target) take effect.
    """
    service_module = import_module(_SERVICE_MODULE)
    return cast(Callable[[str, str], Any], getattr(service_module, name))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _resident_agi_capability_surface_from_request(request: RoleTurnRequest) -> Mapping[str, Any] | None:
    """Return Resident AGI capability evidence already carried by this role turn.

    Resident-owned data must arrive through the role-turn context assembled by
    `resident.autonomy`. Keeping the reader request-scoped avoids a reverse
    dependency from `roles.kernel` or `roles.runtime` back into the Resident
    Cell while still letting the kernel render its generic signal.
    """

    context_override = _mapping(getattr(request, "context_override", None))
    direct_payload = _mapping(context_override.get("resident_agi_capability_surface"))
    if direct_payload:
        return direct_payload

    audit_pack = _mapping(context_override.get("resident_agi_audit_pack"))
    capability_surface = _mapping(audit_pack.get("capability_surface"))
    return capability_surface or None


def _resident_agi_decision_trace_from_request(request: RoleTurnRequest) -> list[Any] | Mapping[str, Any] | str | None:
    """Return Resident AGI decision trace evidence carried by this role turn."""

    context_override = _mapping(getattr(request, "context_override", None))
    direct_payload = context_override.get("resident_agi_decision_trace")
    if isinstance(direct_payload, (str, list, Mapping)) and direct_payload:
        return direct_payload

    audit_pack = _mapping(context_override.get("resident_agi_audit_pack"))
    recent_decisions = audit_pack.get("recent_decisions")
    if isinstance(recent_decisions, list) and recent_decisions:
        return recent_decisions
    return None


def _build_context_gateway_config_for_role(
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
) -> ContextGatewayConfig:
    """Mount role asset reader ports for kernel-owned context signal assembly."""
    del role, profile
    return ContextGatewayConfig(
        blueprint_overview_provider=_resolve_reader("_read_blueprint_status_for_context"),
        verdict_history_provider=_resolve_reader("_read_qa_verdict_for_context"),
        resident_agi_capability_provider=lambda _workspace: _resident_agi_capability_surface_from_request(request),
        resident_agi_decision_trace_provider=lambda _workspace: _resident_agi_decision_trace_from_request(request),
    )
