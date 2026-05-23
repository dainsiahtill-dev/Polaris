"""Shared helpers used by multiple routers (director, pm, etc.)."""

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from polaris.cells.llm.evaluation.public.service import load_llm_test_index
from polaris.cells.llm.provider_runtime.public.service import is_role_runtime_supported
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.delivery.http.dependencies import require_auth as _canonical_require_auth
from polaris.delivery.http.middleware.rbac import require_role as _require_role
from polaris.delivery.http.workspace import active_workspace_value
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.llm.model_identity import model_identity_equal
from polaris.kernelone.storage.io_paths import build_cache_root
from starlette.responses import JSONResponse

require_auth = _canonical_require_auth
require_role = _require_role


def get_state(request: Request) -> AppState:
    return request.app.state.app_state


def _workspace_value(settings: Any) -> str:
    return active_workspace_value(settings)


def _role_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _lookup_role_status(index: dict[str, Any], role: str) -> dict[str, Any] | None:
    roles = index.get("roles") if isinstance(index.get("roles"), dict) else {}
    if not isinstance(roles, dict):
        return None

    direct = roles.get(role)
    if isinstance(direct, dict):
        return direct

    target = _role_key(role)
    for key, value in roles.items():
        if _role_key(key) == target and isinstance(value, dict):
            return value
    return None


def _provider_role_compatible(role: str, provider_status: dict[str, Any] | None) -> bool:
    if not isinstance(provider_status, dict):
        return False
    tested_role = _role_key(provider_status.get("role"))
    return not tested_role or tested_role == _role_key(role)


def _readiness_candidate_issue(
    *,
    provider_id: str,
    model: str,
    tested_provider_id: str,
    tested_model: str,
) -> str:
    if not provider_id or not model:
        return "LLM binding is incomplete"
    if tested_provider_id and tested_provider_id != provider_id:
        return f"LLM readiness was tested for provider {tested_provider_id}, not {provider_id}"
    if not tested_model:
        return "LLM readiness was not tested for the current model"
    if not model_identity_equal(tested_model, model):
        return f"LLM readiness was tested for model {tested_model}, not {model}"
    return ""


def _ensure_llm_ready(state: AppState, role: str) -> None:
    role_key = _role_key(role)
    workspace = _workspace_value(state.settings)
    cache_root = build_cache_root(str(state.settings.ramdisk_root or ""), workspace)
    config = llm_config.load_llm_config(workspace, cache_root, settings=state.settings)
    index = load_llm_test_index(workspace)
    role_status = _lookup_role_status(index, role_key) if isinstance(index, dict) else None
    roles_cfg = config.get("roles") if isinstance(config.get("roles"), dict) else {}
    role_cfg = roles_cfg.get(role_key, {}) if isinstance(roles_cfg, dict) else {}
    if not isinstance(role_cfg, dict) or not role_cfg:
        for key, value in roles_cfg.items():
            if _role_key(key) == role_key and isinstance(value, dict):
                role_cfg = value
                break
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    provider_id = str(role_cfg.get("provider_id") or "").strip() if isinstance(role_cfg, dict) else ""
    model = str(role_cfg.get("model") or "").strip() if isinstance(role_cfg, dict) else ""

    provider_index = index.get("providers") if isinstance(index.get("providers"), dict) else {}
    provider_status = provider_index.get(provider_id) if isinstance(provider_index, dict) else None
    candidates: list[tuple[str, str]] = []
    if isinstance(role_status, dict) and bool(role_status.get("ready")):
        candidates.append(
            (
                str(role_status.get("provider_id") or "").strip(),
                str(role_status.get("model") or "").strip(),
            )
        )
    if (
        isinstance(provider_status, dict)
        and bool(provider_status.get("ready"))
        and _provider_role_compatible(role_key, provider_status)
    ):
        candidates.append(
            (
                provider_id,
                str(provider_status.get("model") or "").strip(),
            )
        )

    first_issue = f"{role_key} LLM not ready; run tests first"
    for tested_provider_id, tested_model in candidates:
        issue = _readiness_candidate_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=tested_provider_id,
            tested_model=tested_model,
        )
        if not issue:
            break
        first_issue = f"{role_key} {issue}"
    else:
        raise HTTPException(status_code=409, detail=first_issue)

    provider_cfg = providers.get(provider_id, {}) if isinstance(providers, dict) else {}
    if not is_role_runtime_supported(role_key, provider_id, provider_cfg):
        raise HTTPException(
            status_code=409,
            detail=f"{role_key} provider not supported for runtime",
        )


def required_ready_roles(
    state: AppState,
    default_roles: list[str] | None = None,
    force_first: str | None = None,
) -> list[str]:
    """Return the list of roles that must pass LLM-readiness checks.

    *default_roles* – fallback when the workspace config has no
    ``required_ready_roles`` policy (e.g. ``["director", "qa"]``).

    *force_first* – if given and the role is absent from the resolved
    list it is inserted at position 0 (used by the director router to
    guarantee "director" is always checked).
    """
    workspace = _workspace_value(state.settings)
    cache_root = build_cache_root(str(state.settings.ramdisk_root or ""), workspace)
    config = llm_config.load_llm_config(workspace, cache_root, settings=state.settings)
    policies = config.get("policies") if isinstance(config.get("policies"), dict) else {}
    configured = policies.get("required_ready_roles") if isinstance(policies, dict) else None
    roles: list[str] = []
    if isinstance(configured, list):
        for value in configured:
            role = str(value or "").strip().lower()
            if not role or role == "docs" or role in roles:
                continue
            roles.append(role)
    if not roles:
        roles = list(default_roles or ["director", "qa"])
    if not state.settings.qa_enabled:
        roles = [role for role in roles if role != "qa"]
    if force_first and force_first not in roles:
        roles.insert(0, force_first)
    return roles


def ensure_required_roles_ready(
    state: AppState,
    default_roles: list[str] | None = None,
    force_first: str | None = None,
) -> None:
    """Raise 409 if any of the required roles fail the LLM-readiness check.

    Returns a structured error response via JSONResponse to properly format
    the error details (instead of using HTTPException.detail which expects a string).
    """
    roles = required_ready_roles(state, default_roles=default_roles, force_first=force_first)
    missing_roles: list[str] = []
    for role in roles:
        try:
            _ensure_llm_ready(state, role)
        except HTTPException:
            missing_roles.append(role)
    if missing_roles:
        # Use structured_error_response for proper JSON formatting
        # HTTPException.detail expects a string, so we use JSONResponse instead
        raise StructuredHTTPException(
            status_code=409,
            code="RUNTIME_ROLES_NOT_READY",
            message="One or more required runtime roles are not ready",
            details={
                "required_roles": roles,
                "missing_roles": missing_roles,
            },
        )


class StructuredHTTPException(HTTPException):
    """HTTPException that carries structured {code, message, details} data.

    Registered via FastAPI exception handlers so all API error responses
    follow the unified format defined in ADR-003.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.structured_message = message
        self.structured_details: dict[str, Any] = dict(details) if details else {}
        super().__init__(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "details": dict(details) if details else {},
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the ADR-003 structured error dict."""
        return {
            "code": self.code,
            "message": self.structured_message,
            "details": self.structured_details,
        }


def structured_error_response(
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> JSONResponse:
    """Return a JSONResponse with unified {code, message, details} format."""
    body = {
        "code": code,
        "message": message,
        "details": dict(details) if details else {},
    }
    return JSONResponse(status_code=status_code, content=body)
