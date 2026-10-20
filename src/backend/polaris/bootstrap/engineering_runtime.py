"""Host composition shared by HTTP and CLI engineering entry adapters.

The service owns runs. Settings owns current policy. This module only binds the
two and maps ordinary entry arguments; it has no task store or execution loop.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from polaris.bootstrap.config import Settings
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import (
    EngineeringRunService,
    EngineeringSessionConflictError,
)


def _roots(workspace: Path, configured: tuple[str, ...], *, future_outputs: bool = False) -> tuple[Path, ...]:
    roots: list[Path] = []
    for raw in configured:
        if not raw.strip():
            raise ValueError("engineering profile roots must be non-empty directory paths")
        path = Path(raw).expanduser()
        if future_outputs:
            # The public service validates these exact host declarations. It
            # never creates them; Director must materialize them with tools.
            roots.append((workspace / path).absolute() if not path.is_absolute() else path.absolute())
            continue
        path = (workspace / path).resolve(strict=True) if not path.is_absolute() else path.resolve(strict=True)
        if not path.is_dir():
            raise ValueError(f"engineering profile root is not a directory: {path}")
        roots.append(path)
    return tuple(roots)


def resolve_engineering_profile(settings: Settings, workspace: Path, profile_id: str) -> PermissionScope:
    """Resolve only an exact currently configured host profile; preserve empties."""
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    profile = settings.server.engineering_permission_profiles.get(profile_id)
    if profile is None:
        raise PermissionError(f"engineering permission profile is unavailable: {profile_id}")
    supported = {
        "read_file",
        "repo_tree",
        "repo_rg",
        "write_file",
        "edit_file",
        "execute_command",
        "consult_role",
        "report_review",
        "verify_project",
    }
    tools = frozenset(ToolSpecRegistry.get_canonical(name) for name in profile.enabled_tools)
    if tools - supported:
        raise ValueError("engineering profile contains unsupported tools: " + ", ".join(sorted(tools - supported)))
    return PermissionScope(
        _roots(workspace, profile.read_roots), _roots(workspace, profile.write_roots), tools, profile.network_allowed
    )


def engineering_limits(settings: Settings) -> RunLimits:
    """Convert host duration once at admission; models cannot select limits."""
    server = settings.server
    deadline = time.time() + server.engineering_run_timeout_seconds if server.engineering_run_timeout_seconds else None
    return RunLimits(deadline, server.engineering_max_output_bytes, server.engineering_max_tool_output_bytes)


def _prepare_model_runtime(settings: Settings, workspace: Path) -> None:
    """Reuse existing global K1 binding/config owners without full legacy DI."""
    from polaris.infrastructure.llm.provider_bootstrap import ProviderAdapter, inject_kernelone_provider_runtime
    from polaris.infrastructure.llm.providers.provider_registry import provider_manager
    from polaris.kernelone.llm.config_store import load_llm_config
    from polaris.kernelone.llm.providers.provider_injection import set_provider_manager_port

    inject_kernelone_provider_runtime()
    set_provider_manager_port(ProviderAdapter(provider_manager))  # type: ignore[arg-type]
    # Existing config_store/runtime_config choose KERNELONE_LLM_CONFIG or the
    # same global config. No workspace model map or role-readiness fanout.
    load_llm_config(str(workspace), str(workspace / ".polaris" / "runtime"), settings=settings)


def create_engineering_service(
    settings: Settings, *, current_settings: Callable[[], Settings] | None = None
) -> EngineeringRunService:
    """Create one process owner bound to the trusted workspace at construction."""
    workspace = Path(settings.workspace).expanduser().resolve(strict=True)
    _prepare_model_runtime(settings, workspace)
    from polaris.delivery.ws.engineering_events import publish_engineering_event

    def current_profile(row: dict[str, Any]) -> PermissionScope:
        profile_id = row.get("permission_profile_id")
        if not isinstance(profile_id, str) or not profile_id:
            raise PermissionError("saved engineering run has no selected host permission profile")
        policy = current_settings() if current_settings is not None else settings
        return resolve_engineering_profile(policy, workspace, profile_id)

    return EngineeringRunService(
        workspace,
        permission_resolver=current_profile,
        writable_output_roots=_roots(
            workspace, settings.server.engineering_verification_output_roots, future_outputs=True
        ),
        require_qa=settings.server.engineering_require_qa,
        event_publisher=publish_engineering_event if settings.nats.enabled else None,
    )


def get_engineering_service(owner: Any, *, settings: Settings | None = None) -> EngineeringRunService:
    """Return the process/app owner; subsequent settings changes cannot rebind it."""
    service = getattr(owner, "engineering_run_service", None)
    if service is None:
        configured = settings if settings is not None else getattr(owner, "settings", None)
        if not isinstance(configured, Settings):
            raise RuntimeError("engineering host settings are unavailable")

        def current_settings() -> Settings:
            state = getattr(owner, "app_state", None)
            current = getattr(state, "settings", None) or getattr(owner, "settings", None)
            if not isinstance(current, Settings):
                raise PermissionError("current engineering host policy is unavailable")
            return current

        service = create_engineering_service(configured, current_settings=current_settings)
        owner.engineering_run_service = service
    if not isinstance(service, EngineeringRunService):
        raise RuntimeError("invalid engineering service owner")
    return service


def observe_engineering_run(service: EngineeringRunService, session_id: str, workspace: Path) -> dict[str, Any]:
    """Project saved facts and fresh checks; untouched roles stay not_called."""
    from polaris.delivery.ws.engineering_events import compact_engineering_payload

    row = service.snapshot(session_id, workspace=workspace)
    roles = dict.fromkeys(("pm", "chief_engineer", "director", "qa"), "not_called")
    active_roles: list[str] = []
    for event in row["role_events"]:
        role = event["role"]
        if role not in roles:
            continue
        if event["kind"] == "role_started":
            roles[role] = "running"
            if role not in active_roles:
                active_roles.append(role)
        elif event["kind"] == "role_finished":
            roles[role] = str(event["payload"].get("status", "unknown"))
            if role in active_roles:
                active_roles.remove(role)
    if row["status"] != "running":
        active_roles = []
        for role, status in roles.items():
            if status == "running":
                roles[role] = "unknown" if row["status"] == "completed" else str(row["status"])
    return {
        "session_id": row["session_id"],
        "run_id": row["session_id"],
        "engine_version": row["engine_version"],
        "workspace": row["workspace"],
        "workflow": row["workflow"],
        "role": row["role"],
        "status": row["status"],
        "active_roles": active_roles,
        "roles": roles,
        "text": row["text"],
        "verification": row["verification"],
        "require_qa": row["require_qa"],
        "qa_verdict": row["qa_verdict"],
        "project_outcome": row["project_outcome"],
        "acceptance_basis": row["acceptance_basis"],
        "checks": row["checks"],
        "required_checks": row["required_checks"],
        "verification_commands": row["verification_commands"],
        "permission_profile_id": row.get("permission_profile_id"),
        "resource_url": f"/v2/engineering/runs/{session_id}",
        "last_seq": row["last_seq"],
        "events": [
            {**event, "payload": compact_engineering_payload(event["kind"], event["payload"])}
            for event in row["events"]
        ],
        "event_delivery_error": row["event_delivery_error"],
        "native_observation_error": row["native_observation_error"],
    }


async def start_engineering_run(
    service: EngineeringRunService,
    settings: Settings,
    *,
    instruction: str,
    workspace: str | Path,
    workflow: Literal["direct", "collaborative"] = "direct",
    role: Literal["pm", "chief_engineer", "director", "qa"] = "director",
    permission_profile_id: str | None = None,
    required_checks: tuple[str, ...] = (),
    request_key: str | None = None,
) -> dict[str, Any]:
    """Map explicit user intent and host policy into the same service command."""
    bound = Path(workspace).expanduser().resolve()
    if bound != service.workspace:
        raise PermissionError("engineering service workspace mismatch")
    if role not in {"pm", "chief_engineer", "director", "qa"} or workflow not in {"direct", "collaborative"}:
        raise ValueError("unsupported engineering role or workflow")
    if workflow == "collaborative" and role != "director":
        raise ValueError("collaborative execution requires the director owner role")
    if not instruction.strip() or any(not check.strip() for check in required_checks):
        raise ValueError("instruction and supplied required checks must be non-empty")
    selected = (
        permission_profile_id
        if permission_profile_id is not None
        else settings.server.engineering_default_permission_profile_id
    )
    scope = resolve_engineering_profile(settings, service.workspace, selected)
    limits = engineering_limits(settings)
    if request_key is not None:
        existing = service.find_request(request_key, workspace=bound)
        if existing is not None:
            # Absolute admission time is not a new user command. Preserve the
            # original deadline while still comparing current scope and limits.
            limits = RunLimits(existing["limits"]["deadline"], limits.max_output_bytes, limits.max_tool_output_bytes)
    command = RoleRunCommand(uuid4().hex, role, instruction, bound, workflow, tuple(required_checks))
    session_id = await service.start(
        command, scope=scope, limits=limits, request_key=request_key, permission_profile_id=selected
    )
    return observe_engineering_run(service, session_id, bound)


def engineering_error_status(error: Exception) -> int:
    """Shared explicit error mapping for thin HTTP/chat entry adapters."""
    if isinstance(error, PermissionError):
        return 403
    if isinstance(error, KeyError):
        return 404
    if isinstance(error, (EngineeringSessionConflictError, BlockingIOError, RuntimeError)):
        return 409
    return 422
