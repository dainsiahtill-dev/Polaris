"""Shared helpers used by multiple routers (director, pm, etc.)."""

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from polaris.cells.llm.evaluation.public.service import load_llm_test_index
from polaris.cells.llm.provider_runtime.public.service import is_role_runtime_supported
from polaris.cells.runtime.state_owner.public.service import AppState, Auth
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.storage.io_paths import build_cache_root
from starlette.responses import JSONResponse


def get_state(request: Request) -> AppState:
    return request.app.state.app_state


def require_auth(request: Request) -> None:
    """Require bearer auth when backend token is configured.

    Security: Token MUST be provided via Authorization header only.
    Query parameter token is NOT supported to prevent leakage in:
    - Server access logs
    - Browser history
    - Referer headers
    """
    auth: Auth = request.app.state.auth
    auth_header = request.headers.get("authorization", "")
    if auth.check(auth_header):
        return
    raise HTTPException(status_code=401, detail="unauthorized")


def _ensure_llm_ready(state: AppState, role: str) -> None:
    cache_root = build_cache_root(str(state.settings.ramdisk_root or ""), str(state.settings.workspace))
    config = llm_config.load_llm_config(str(state.settings.workspace), cache_root, settings=state.settings)
    index = load_llm_test_index(state.settings)
    role_status = (index.get("roles") or {}).get(role) if isinstance(index, dict) else None
    if not isinstance(role_status, dict) or not role_status.get("ready"):
        raise HTTPException(status_code=409, detail=f"{role} LLM not ready; run tests first")
    role_cfg = (config.get("roles") or {}).get(role, {}) if isinstance(config.get("roles"), dict) else {}
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    provider_cfg = providers.get(role_cfg.get("provider_id"), {}) if isinstance(providers, dict) else {}
    provider_id = role_cfg.get("provider_id") if isinstance(role_cfg, dict) else None
    if not is_role_runtime_supported(role, provider_id, provider_cfg):
        raise HTTPException(
            status_code=409,
            detail=f"{role} provider not supported for runtime",
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
    cache_root = build_cache_root(str(state.settings.ramdisk_root or ""), str(state.settings.workspace))
    config = llm_config.load_llm_config(str(state.settings.workspace), cache_root, settings=state.settings)
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
