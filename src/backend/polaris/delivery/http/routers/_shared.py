"""Shared helpers used by multiple routers (director, pm, etc.)."""

import os
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from polaris.cells.llm.evaluation.public.service import (
    load_llm_test_index,
    load_llm_test_index_candidates,
    readiness_freshness_issue,
    run_connectivity_suite_sync,
)
from polaris.cells.llm.provider_runtime.public.service import role_runtime_support_issue
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


def _settings_qa_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "qa_enabled", True))


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
    return not tested_role or tested_role == "connectivity" or tested_role == _role_key(role)


def _director_binding_skip_reason(
    *,
    role: str,
    provider_id: str,
    model: str,
    provider_status: dict[str, Any] | None,
) -> str:
    """Return a reason when a Director binding may be explicitly skipped."""
    if _role_key(role) != "director":
        return ""
    if not isinstance(provider_status, dict) or bool(provider_status.get("ready")):
        return ""
    if not _provider_role_compatible(role, provider_status):
        return ""
    tested_model = str(provider_status.get("model") or "").strip()
    tested_timestamp = provider_status.get("timestamp")
    issue = _readiness_candidate_issue(
        provider_id=provider_id,
        model=model,
        tested_provider_id=provider_id,
        tested_model=tested_model,
        tested_timestamp=tested_timestamp,
    )
    if issue:
        return ""
    suites = provider_status.get("suites")
    connectivity = suites.get("connectivity") if isinstance(suites, dict) else None
    if isinstance(connectivity, dict) and connectivity.get("ok") is False:
        return "provider_connectivity_unavailable"
    grade = str(provider_status.get("grade") or "").strip().upper()
    if grade == "FAIL":
        return "provider_readiness_failed"
    return ""


def _dedupe_index_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index in candidates:
        if not isinstance(index, dict):
            continue
        identity = id(index)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(index)
    return deduped or [{}]


def _provider_status_from_index(index: dict[str, Any], provider_id: str) -> dict[str, Any] | None:
    provider_index = index.get("providers") if isinstance(index.get("providers"), dict) else {}
    provider_status = provider_index.get(provider_id) if isinstance(provider_index, dict) else None
    return provider_status if isinstance(provider_status, dict) else None


def _exact_role_status_matches_binding(role_status: dict[str, Any], provider_id: str, model: str) -> bool:
    tested_provider_id = str(role_status.get("provider_id") or "").strip()
    tested_model = str(role_status.get("model") or "").strip()
    return (
        _readiness_candidate_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=tested_provider_id,
            tested_model=tested_model,
            tested_timestamp=role_status.get("timestamp"),
        )
        == ""
    )


def _role_status_identity_matches_binding(role_status: dict[str, Any], provider_id: str, model: str) -> bool:
    tested_provider_id = str(role_status.get("provider_id") or "").strip()
    tested_model = str(role_status.get("model") or "").strip()
    return (
        _readiness_identity_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=tested_provider_id,
            tested_model=tested_model,
        )
        == ""
    )


def _exact_provider_status_matches_binding(
    *,
    role: str,
    provider_id: str,
    model: str,
    provider_status: dict[str, Any],
) -> bool:
    if not _provider_role_compatible(role, provider_status):
        return False
    tested_model = str(provider_status.get("model") or "").strip()
    return (
        _readiness_candidate_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=provider_id,
            tested_model=tested_model,
            tested_timestamp=provider_status.get("timestamp"),
        )
        == ""
    )


def _provider_status_identity_matches_binding(
    *,
    role: str,
    provider_id: str,
    model: str,
    provider_status: dict[str, Any],
) -> bool:
    if not _provider_role_compatible(role, provider_status):
        return False
    tested_model = str(provider_status.get("model") or "").strip()
    return (
        _readiness_identity_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=provider_id,
            tested_model=tested_model,
        )
        == ""
    )


def _select_binding_status(
    *,
    indexes: list[dict[str, Any]],
    role: str,
    provider_id: str,
    model: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    first_role_status: dict[str, Any] | None = None
    first_provider_status: dict[str, Any] | None = None
    first_exact_failed_role: dict[str, Any] | None = None
    first_exact_failed_provider: dict[str, Any] | None = None
    first_identity_matched_role: dict[str, Any] | None = None
    first_identity_matched_provider: dict[str, Any] | None = None

    for index in indexes:
        role_status = _lookup_role_status(index, role)
        provider_status = _provider_status_from_index(index, provider_id)

        if first_role_status is None and isinstance(role_status, dict):
            first_role_status = role_status
        if first_provider_status is None and isinstance(provider_status, dict):
            first_provider_status = provider_status

        if (
            first_identity_matched_role is None
            and isinstance(role_status, dict)
            and _role_status_identity_matches_binding(role_status, provider_id, model)
        ):
            first_identity_matched_role = role_status
            first_identity_matched_provider = provider_status

        if (
            first_identity_matched_provider is None
            and isinstance(provider_status, dict)
            and _provider_status_identity_matches_binding(
                role=role,
                provider_id=provider_id,
                model=model,
                provider_status=provider_status,
            )
        ):
            first_identity_matched_provider = provider_status

        if isinstance(role_status, dict) and _exact_role_status_matches_binding(role_status, provider_id, model):
            if bool(role_status.get("ready")):
                return role_status, provider_status
            if first_exact_failed_role is None:
                first_exact_failed_role = role_status
                first_exact_failed_provider = provider_status

        if isinstance(provider_status, dict) and _exact_provider_status_matches_binding(
            role=role,
            provider_id=provider_id,
            model=model,
            provider_status=provider_status,
        ):
            if bool(provider_status.get("ready")):
                return None, provider_status
            if first_exact_failed_provider is None:
                first_exact_failed_provider = provider_status

    if first_exact_failed_role is not None or first_exact_failed_provider is not None:
        return first_exact_failed_role, first_exact_failed_provider
    if first_identity_matched_role is not None or first_identity_matched_provider is not None:
        return first_identity_matched_role, first_identity_matched_provider
    return first_role_status, first_provider_status


def _readiness_candidate_issue(
    *,
    provider_id: str,
    model: str,
    tested_provider_id: str,
    tested_model: str,
    tested_timestamp: Any = None,
) -> str:
    identity_issue = _readiness_identity_issue(
        provider_id=provider_id,
        model=model,
        tested_provider_id=tested_provider_id,
        tested_model=tested_model,
    )
    if identity_issue:
        return identity_issue
    freshness_issue = readiness_freshness_issue(tested_timestamp)
    if freshness_issue == "timestamp_invalid":
        return (
            f"LLM readiness for provider {provider_id} model {model} has an invalid"
            f" timestamp ({tested_timestamp}); rerun LLM tests"
        )
    if freshness_issue == "timestamp_missing":
        return f"LLM readiness for provider {provider_id} model {model} has no timestamp; rerun LLM tests"
    return ""


def _readiness_identity_issue(
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


def _readiness_failed_issue(
    *,
    provider_id: str,
    model: str,
    tested_provider_id: str,
    tested_model: str,
    tested_timestamp: Any = None,
) -> str:
    issue = _readiness_candidate_issue(
        provider_id=provider_id,
        model=model,
        tested_provider_id=tested_provider_id,
        tested_model=tested_model,
        tested_timestamp=tested_timestamp,
    )
    if issue:
        return issue
    tested = f"provider {tested_provider_id or provider_id} model {tested_model or model}"
    suffix = f" at {tested_timestamp}" if tested_timestamp else ""
    return f"LLM readiness failed for {tested}{suffix}; rerun LLM tests"


def _ensure_llm_ready(state: AppState, role: str) -> None:
    role_key = _role_key(role)
    workspace = _workspace_value(state.settings)
    cache_root = build_cache_root(str(getattr(state.settings, "ramdisk_root", "") or ""), workspace)
    config = llm_config.load_llm_config(workspace, cache_root, settings=state.settings)
    index = load_llm_test_index(workspace)
    index_candidates = _dedupe_index_candidates([index, *load_llm_test_index_candidates(workspace)])
    roles_cfg_raw = config.get("roles")
    roles_cfg: Mapping[str, Any] = roles_cfg_raw if isinstance(roles_cfg_raw, dict) else {}
    role_cfg = roles_cfg.get(role_key, {}) if isinstance(roles_cfg, dict) else {}
    if not isinstance(role_cfg, dict) or not role_cfg:
        for key, value in roles_cfg.items():
            if _role_key(key) == role_key and isinstance(value, dict):
                role_cfg = value
                break
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    provider_id = str(role_cfg.get("provider_id") or "").strip() if isinstance(role_cfg, dict) else ""
    model = str(role_cfg.get("model") or "").strip() if isinstance(role_cfg, dict) else ""

    # Check bindings for multi-bound roles (e.g., Director)
    bindings_raw = role_cfg.get("bindings") if isinstance(role_cfg, dict) else None
    bindings: list[dict[str, Any]] = []
    if isinstance(bindings_raw, list):
        for binding in bindings_raw:
            if isinstance(binding, dict):
                bindings.append(binding)

    # If bindings exist, check each binding's readiness
    if bindings:
        ready_binding_count = 0
        skipped_bindings: list[str] = []
        for binding in bindings:
            binding_provider_id = str(binding.get("provider_id") or "").strip()
            binding_model = str(binding.get("model") or "").strip()
            binding_id = str(binding.get("binding_id") or "").strip()

            if not binding_provider_id or not binding_model:
                continue

            role_status, provider_status = _select_binding_status(
                indexes=index_candidates,
                role=role_key,
                provider_id=binding_provider_id,
                model=binding_model,
            )
            if isinstance(role_status, dict) and not bool(role_status.get("ready")):
                issue = _readiness_failed_issue(
                    provider_id=binding_provider_id,
                    model=binding_model,
                    tested_provider_id=str(role_status.get("provider_id") or "").strip(),
                    tested_model=str(role_status.get("model") or "").strip(),
                    tested_timestamp=role_status.get("timestamp"),
                )
                raise HTTPException(
                    status_code=409,
                    detail=f"{role_key} binding {binding_id} ({binding_provider_id}/{binding_model}) {issue}",
                )

            binding_candidates: list[tuple[str, str, Any]] = []
            if isinstance(role_status, dict) and bool(role_status.get("ready")):
                binding_candidates.append(
                    (
                        str(role_status.get("provider_id") or "").strip(),
                        str(role_status.get("model") or "").strip(),
                        role_status.get("timestamp"),
                    )
                )
            if (
                isinstance(provider_status, dict)
                and bool(provider_status.get("ready"))
                and _provider_role_compatible(role_key, provider_status)
            ):
                binding_candidates.append(
                    (
                        binding_provider_id,
                        str(provider_status.get("model") or "").strip(),
                        provider_status.get("timestamp"),
                    )
                )

            binding_name = f" {binding_id}" if binding_id else ""
            binding_label = f"{role_key} binding{binding_name} ({binding_provider_id}/{binding_model})"
            first_issue = f"{binding_label} LLM not ready; run tests first"
            binding_ready = False
            for tested_provider_id, tested_model, tested_timestamp in binding_candidates:
                issue = _readiness_candidate_issue(
                    provider_id=binding_provider_id,
                    model=binding_model,
                    tested_provider_id=tested_provider_id,
                    tested_model=tested_model,
                    tested_timestamp=tested_timestamp,
                )
                if not issue:
                    binding_ready = True
                    break
                first_issue = f"{binding_label} {issue}"
            if binding_ready:
                ready_binding_count += 1
                continue
            skip_reason = _director_binding_skip_reason(
                role=role_key,
                provider_id=binding_provider_id,
                model=binding_model,
                provider_status=provider_status,
            )
            if skip_reason:
                skipped_bindings.append(f"{binding_provider_id}/{binding_model}:{skip_reason}")
                continue
            raise HTTPException(status_code=409, detail=first_issue)
        if ready_binding_count <= 0 and skipped_bindings:
            raise HTTPException(
                status_code=409,
                detail=f"{role_key} all bindings unavailable; skipped={', '.join(skipped_bindings)}",
            )
    else:
        # No bindings - check single provider/model
        role_status, provider_status = _select_binding_status(
            indexes=index_candidates,
            role=role_key,
            provider_id=provider_id,
            model=model,
        )
        if isinstance(role_status, dict) and not bool(role_status.get("ready")):
            issue = _readiness_failed_issue(
                provider_id=provider_id,
                model=model,
                tested_provider_id=str(role_status.get("provider_id") or "").strip(),
                tested_model=str(role_status.get("model") or "").strip(),
                tested_timestamp=role_status.get("timestamp"),
            )
            raise HTTPException(status_code=409, detail=f"{role_key} {issue}")

        candidates: list[tuple[str, str, Any]] = []
        if isinstance(role_status, dict) and bool(role_status.get("ready")):
            candidates.append(
                (
                    str(role_status.get("provider_id") or "").strip(),
                    str(role_status.get("model") or "").strip(),
                    role_status.get("timestamp"),
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
                    provider_status.get("timestamp"),
                )
            )

        first_issue = f"{role_key} LLM not ready; run tests first"
        for tested_provider_id, tested_model, tested_timestamp in candidates:
            issue = _readiness_candidate_issue(
                provider_id=provider_id,
                model=model,
                tested_provider_id=tested_provider_id,
                tested_model=tested_model,
                tested_timestamp=tested_timestamp,
            )
            if not issue:
                break
            first_issue = f"{role_key} {issue}"
        else:
            raise HTTPException(status_code=409, detail=first_issue)

    provider_cfg = providers.get(provider_id, {}) if isinstance(providers, dict) else {}
    runtime_issue = role_runtime_support_issue(role_key, provider_id, provider_cfg)
    if runtime_issue:
        raise HTTPException(
            status_code=409,
            detail=f"{role_key} provider not supported for runtime: {runtime_issue}",
        )


def _role_binding_for_live_check(state: AppState, role: str) -> tuple[str, str, dict[str, Any]]:
    role_key = _role_key(role)
    workspace = _workspace_value(state.settings)
    cache_root = build_cache_root(str(getattr(state.settings, "ramdisk_root", "") or ""), workspace)
    config = llm_config.load_llm_config(workspace, cache_root, settings=state.settings)
    roles_cfg_raw = config.get("roles")
    roles_cfg: Mapping[str, Any] = roles_cfg_raw if isinstance(roles_cfg_raw, dict) else {}
    role_cfg = roles_cfg.get(role_key, {}) if isinstance(roles_cfg, dict) else {}
    if not isinstance(role_cfg, dict) or not role_cfg:
        for key, value in roles_cfg.items():
            if _role_key(key) == role_key and isinstance(value, dict):
                role_cfg = value
                break
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    provider_id = str(role_cfg.get("provider_id") or "").strip() if isinstance(role_cfg, dict) else ""
    model = str(role_cfg.get("model") or "").strip() if isinstance(role_cfg, dict) else ""
    provider_cfg = providers.get(provider_id, {}) if isinstance(providers, dict) else {}
    provider_payload = dict(provider_cfg) if isinstance(provider_cfg, dict) else {}
    return provider_id, model, provider_payload


def _sanitize_live_check_error(value: Any) -> str:
    text = str(value or "").strip().replace("\r", " ").replace("\n", " ")
    return text[:500] or "unknown live connectivity failure"


def _ensure_llm_live_connectivity(state: AppState, role: str) -> None:
    role_key = _role_key(role)
    provider_id, model, provider_cfg = _role_binding_for_live_check(state, role_key)
    if not provider_id or not model:
        raise HTTPException(status_code=409, detail=f"{role_key} LLM binding is incomplete")

    result = run_connectivity_suite_sync(provider_cfg, model)
    if bool(result.get("ok")):
        return
    error = _sanitize_live_check_error(result.get("error"))
    raise HTTPException(
        status_code=409,
        detail=f"{role_key} live LLM connectivity failed for provider {provider_id} model {model}: {error}",
    )


def required_ready_roles(
    state: AppState,
    default_roles: list[str] | None = None,
    force_first: str | None = None,
    force_roles: list[str] | None = None,
) -> list[str]:
    """Return the list of roles that must pass LLM-readiness checks.

    *default_roles* – fallback when the workspace config has no
    ``required_ready_roles`` policy (e.g. ``["director", "qa"]``).

    *force_first* – if given and the role is absent from the resolved
    list it is inserted at position 0 (used by the director router to
    guarantee "director" is always checked).

    *force_roles* – if given, every listed role is included before any
    policy-derived roles. This is used by multi-stage desktop workflows whose
    execution graph already proves the required runtime roles.
    """
    workspace = _workspace_value(state.settings)
    cache_root = build_cache_root(str(getattr(state.settings, "ramdisk_root", "") or ""), workspace)
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
        roles = list(default_roles or ["director", "qa", "chief_engineer"])
    if not _settings_qa_enabled(state.settings):
        roles = [role for role in roles if role != "qa"]
    forced: list[str] = []
    for value in [force_first, *(force_roles or [])]:
        role = str(value or "").strip().lower()
        if role and role not in forced:
            forced.append(role)
    if forced:
        roles = [*forced, *[role for role in roles if role not in forced]]
    return roles


def ensure_required_roles_ready(
    state: AppState,
    default_roles: list[str] | None = None,
    force_first: str | None = None,
    force_roles: list[str] | None = None,
    live_check: bool = False,
) -> None:
    """Raise 409 if any of the required roles fail the LLM-readiness check.

    Returns a structured error response via JSONResponse to properly format
    the error details (instead of using HTTPException.detail which expects a string).
    """
    roles = required_ready_roles(
        state,
        default_roles=default_roles,
        force_first=force_first,
        force_roles=force_roles,
    )
    missing_roles: list[str] = []
    role_issues: dict[str, str] = {}
    for role in roles:
        try:
            _ensure_llm_ready(state, role)
            if live_check:
                _ensure_llm_live_connectivity(state, role)
        except HTTPException as exc:
            missing_roles.append(role)
            role_issues[role] = str(exc.detail)
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
                "role_issues": role_issues,
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


def _env_flag_enabled(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def internal_bench_surface_enabled() -> bool:
    """Return whether internal Factory/Bench HTTP surfaces may be exposed."""
    # Default enabled in dev; set POLARIS_INTERNAL_BENCH_ENABLED=false to disable
    for name in (
        "POLARIS_INTERNAL_BENCH_ENABLED",
        "POLARIS_FACTORY_BENCH_INTERNAL_ENABLED",
        "VITE_POLARIS_INTERNAL_BENCH",
    ):
        raw = os.environ.get(name)
        if raw is not None:
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return True


def require_internal_bench_surface() -> None:
    """Fail closed for Bench/Factory APIs in formal product mode."""

    if internal_bench_surface_enabled():
        return
    raise StructuredHTTPException(
        status_code=404,
        code="INTERNAL_BENCH_SURFACE_DISABLED",
        message=(
            "Factory/Bench surfaces are internal test-mode APIs. "
            "Use platform Control Plane projections for formal product state."
        ),
        details={
            "internal_test_only": True,
            "enable_env": "POLARIS_INTERNAL_BENCH_ENABLED",
            "formal_projection": "/v2/control-plane/ledger/projection",
        },
    )


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
