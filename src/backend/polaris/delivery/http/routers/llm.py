"""Canonical LLM delivery router in Polaris."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.llm.evaluation.public.service import reconcile_llm_test_index
from polaris.cells.llm.provider_config.public.service import sync_settings_from_llm
from polaris.cells.llm.provider_runtime.public.service import get_provider_manager
from polaris.cells.runtime.projection.public.service import build_llm_status
from polaris.cells.storage.layout.public.service import save_persisted_settings
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    active_workspace_value,
    get_state,
    require_auth,
)
from polaris.delivery.http.schemas.common import (
    LLMConfigResponse,
    LLMHealthResponse,
    LLMMetricsResponse,
    LLMMigrateConfigResponse,
    LLMRoleRuntimeStatusResponse,
    LLMRuntimeStatusResponse,
    LLMStatusResponse,
)
from polaris.delivery.http.workspace import settings_with_workspace_override
from polaris.infrastructure.llm.providers.provider_registry import ProviderManager
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.llm.runtime_config import load_role_config
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

router = APIRouter()
logger = logging.getLogger(__name__)

# Resolve provider_manager from the Cell layer (which delegates to kernelone)
_provider_manager: ProviderManager = get_provider_manager()
_RUNTIME_ROLE_IDS = ("pm", "chief_engineer", "director", "qa", "architect")


def _workspace_and_cache_root(settings: Any) -> tuple[str, str]:
    workspace = active_workspace_value(settings)
    cache_root = build_cache_root(str(getattr(settings, "ramdisk_root", "") or ""), workspace)
    return workspace, cache_root


def _normalize_runtime_role_id(role_id: str) -> str:
    normalized = str(role_id or "").strip().lower()
    if normalized == "docs":
        return "architect"
    return normalized


def _provider_health_result(provider_health_results: dict[str, dict[str, Any]], provider_id: str) -> dict[str, Any]:
    if not provider_id:
        return {}
    result = provider_health_results.get(provider_id)
    return result if isinstance(result, dict) else {}


def _role_health_entry(
    *,
    provider_health_results: dict[str, dict[str, Any]],
    provider_id: str,
    model: str,
) -> dict[str, Any]:
    provider_health = _provider_health_result(provider_health_results, provider_id)
    return {
        "provider_id": provider_id,
        "model": model,
        "provider_ok": bool(provider_health.get("ok", False)),
        "provider_latency_ms": int(provider_health.get("latency_ms") or 0),
        "provider_error": provider_health.get("error"),
    }


def _read_json_file(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _build_role_runtime_status(runtime_dir: str, role_id: str) -> dict[str, Any]:
    status: dict[str, Any] = {
        "running": False,
        "lastRun": None,
        "config": {
            "provider_id": None,
            "model": None,
        },
    }

    lock_file = os.path.join(runtime_dir, f"{role_id}.lock")
    if os.path.exists(lock_file):
        status["running"] = True
        lock_data = _read_json_file(lock_file)
        if lock_data:
            status["startedAt"] = lock_data.get("startedAt")
            status["pid"] = lock_data.get("pid")

    status_file = os.path.join(runtime_dir, f"{role_id}_status.json")
    if os.path.exists(status_file):
        status_data = _read_json_file(status_file)
        if status_data:
            status["lastRun"] = status_data.get("lastRun")
            status["lastStatus"] = status_data.get("status")
            status["lastError"] = status_data.get("error")

    try:
        role_config = load_role_config(role_id)
        if role_config:
            status["config"] = {
                "provider_id": role_config.provider_id,
                "model": role_config.model,
                "profile": role_config.profile,
            }
    except (RuntimeError, ValueError) as exc:  # pragma: no cover - defensive runtime path
        logger.warning("RuntimeStatus failed to get config for %s: %s", role_id, exc)

    return status


def _get_llm_config_payload(request: Request) -> dict[str, Any]:
    state = get_state(request)
    workspace, cache_root = _workspace_and_cache_root(state.settings)
    config = llm_config.load_llm_config(workspace, cache_root, settings=state.settings)
    return llm_config.redact_llm_config(config)


def _save_llm_config_payload(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    state = get_state(request)
    workspace, cache_root = _workspace_and_cache_root(state.settings)
    config_payload = payload.get("config") if isinstance(payload, dict) and "config" in payload else payload
    if not isinstance(config_payload, dict):
        raise StructuredHTTPException(status_code=400, code="INVALID_CONFIG", message="invalid config payload")

    try:
        config = llm_config.save_llm_config(
            workspace,
            cache_root,
            config_payload,
            settings=state.settings,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_LLM_CONFIG",
            message=str(exc),
        ) from exc
    reconcile_llm_test_index(state.settings, config)
    sync_settings_from_llm(state.settings, config)
    save_persisted_settings(state.settings)
    return llm_config.redact_llm_config(config)


def _migrate_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return _provider_manager.migrate_legacy_config(payload)
    except (RuntimeError, ValueError) as exc:  # pragma: no cover - defensive runtime path
        logger.error("migrate_config failed: %s", exc)
        raise StructuredHTTPException(status_code=500, code="INTERNAL_ERROR", message="internal error") from exc


def _llm_status_payload(request: Request, workspace: str = "") -> dict[str, Any]:
    state = get_state(request)
    return build_llm_status(settings_with_workspace_override(state.settings, workspace))


def _runtime_status_payload(request: Request) -> dict[str, Any]:
    state = get_state(request)
    workspace, cache_root = _workspace_and_cache_root(state.settings)
    runtime_dir = resolve_artifact_path(workspace, cache_root, "runtime")

    roles_status: dict[str, dict[str, Any]] = {}
    for role_id in _RUNTIME_ROLE_IDS:
        roles_status[role_id] = _build_role_runtime_status(runtime_dir, role_id)

    return {
        "roles": roles_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _role_runtime_status_payload(request: Request, role_id: str) -> dict[str, Any]:
    normalized_role_id = _normalize_runtime_role_id(role_id)
    if normalized_role_id not in _RUNTIME_ROLE_IDS:
        raise StructuredHTTPException(status_code=400, code="INVALID_ROLE_ID", message="invalid role_id")

    state = get_state(request)
    workspace, cache_root = _workspace_and_cache_root(state.settings)
    runtime_dir = resolve_artifact_path(workspace, cache_root, "runtime")

    role_status = _build_role_runtime_status(runtime_dir, normalized_role_id)
    role_status["roleId"] = normalized_role_id
    return role_status


@router.get("/v2/llm/config", dependencies=[Depends(require_auth)], response_model=LLMConfigResponse)
def get_llm_config_v2(request: Request) -> dict[str, Any]:
    """Get the current LLM configuration (redacted)."""
    return _get_llm_config_payload(request)


@router.post("/v2/llm/config", dependencies=[Depends(require_auth)], response_model=LLMConfigResponse)
def save_llm_config_v2(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Save and reconcile LLM configuration."""
    return _save_llm_config_payload(request, payload)


@router.post("/v2/llm/config/migrate", dependencies=[Depends(require_auth)], response_model=LLMMigrateConfigResponse)
def migrate_config_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy LLM configuration to the current schema."""
    return _migrate_config_payload(payload)


@router.get("/v2/llm/status", dependencies=[Depends(require_auth)], response_model=LLMStatusResponse)
def llm_status_v2(request: Request, workspace: str = "") -> dict[str, Any]:
    """Get overall LLM system status."""
    return _llm_status_payload(request, workspace=workspace)


@router.get("/v2/llm/runtime-status", dependencies=[Depends(require_auth)], response_model=LLMRuntimeStatusResponse)
def get_runtime_status_v2(request: Request) -> dict[str, Any]:
    """Get runtime status for all LLM roles."""
    return _runtime_status_payload(request)


@router.get(
    "/v2/llm/runtime-status/{role_id}",
    dependencies=[Depends(require_auth)],
    response_model=LLMRoleRuntimeStatusResponse,
)
def get_role_runtime_status_v2(request: Request, role_id: str) -> dict[str, Any]:
    """Get runtime status for a single LLM role."""
    return _role_runtime_status_payload(request, role_id)


@router.get("/v2/llm/health", dependencies=[Depends(require_auth)], response_model=LLMHealthResponse)
def llm_health_v2(request: Request, workspace: str = "") -> dict[str, Any]:
    """Perform health checks on all configured LLM providers."""
    import time

    start_time = time.time()

    state = get_state(request)
    settings = settings_with_workspace_override(state.settings, workspace)
    workspace_val, cache_root = _workspace_and_cache_root(settings)

    # Load LLM config
    config = llm_config.load_llm_config(workspace_val, cache_root, settings=settings)
    providers_cfg = config.get("providers", {}) if isinstance(config.get("providers"), dict) else {}

    # Perform health checks on all providers
    provider_health_results = _provider_manager.health_check_all(providers_cfg)

    # Build roles health status
    roles_cfg = config.get("roles", {}) if isinstance(config.get("roles"), dict) else {}
    roles_health: dict[str, Any] = {}

    for role, role_cfg in roles_cfg.items():
        if not isinstance(role_cfg, dict):
            continue
        provider_id = str(role_cfg.get("provider_id") or "").strip()
        model = str(role_cfg.get("model") or "").strip()
        bindings_raw = role_cfg.get("bindings")
        bindings: list[dict[str, Any]] = []
        if isinstance(bindings_raw, list):
            for binding in bindings_raw:
                if not isinstance(binding, dict):
                    continue
                binding_provider_id = str(binding.get("provider_id") or "").strip()
                binding_model = str(binding.get("model") or "").strip()
                if not binding_provider_id or not binding_model:
                    continue
                entry = _role_health_entry(
                    provider_health_results=provider_health_results,
                    provider_id=binding_provider_id,
                    model=binding_model,
                )
                entry["binding_id"] = str(binding.get("binding_id") or "").strip()
                bindings.append(entry)

        if bindings:
            roles_health[role] = {
                "provider_id": provider_id,
                "model": model,
                "provider_ok": all(bool(binding.get("provider_ok")) for binding in bindings),
                "provider_latency_ms": max(int(binding.get("provider_latency_ms") or 0) for binding in bindings),
                "provider_error": next(
                    (binding.get("provider_error") for binding in bindings if binding.get("provider_error")),
                    None,
                ),
                "bindings": bindings,
            }
        else:
            roles_health[role] = _role_health_entry(
                provider_health_results=provider_health_results,
                provider_id=provider_id,
                model=model,
            )

    latency_ms = int((time.time() - start_time) * 1000)

    # Determine overall health
    all_providers_ok = all(result.get("ok", False) for result in provider_health_results.values())
    overall_ok = all_providers_ok and len(provider_health_results) > 0

    error_message = None
    if not overall_ok:
        failed_providers = [pid for pid, result in provider_health_results.items() if not result.get("ok", False)]
        error_message = f"Failed providers: {', '.join(failed_providers)}"

    return {
        "ok": overall_ok,
        "latency_ms": latency_ms,
        "providers": provider_health_results,
        "roles": roles_health,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": error_message,
    }


@router.get("/v2/llm/metrics", dependencies=[Depends(require_auth)], response_model=LLMMetricsResponse)
def llm_metrics_v2(request: Request, window_seconds: int = 300) -> dict[str, Any]:
    """Get aggregated LLM call metrics within a sliding time window.

    Aggregates recent LLM call events by role / provider / model
    dimensions, reporting call counts, error rates, latency percentiles
    and token usage.  Data is sourced from the in-process
    ``LLMMetricsStore`` which is fed by the existing audit interceptor
    event path — no new polling or real-time link is introduced.

    Query parameters:
        window_seconds: Size of the sliding window (default 300 s).
    """
    from polaris.kernelone.audit.omniscient.interceptors.llm_metrics import (
        get_llm_metrics_store,
    )

    store = get_llm_metrics_store()
    return store.query(window_seconds=window_seconds)


__all__ = [
    "get_llm_config_v2",
    "get_role_runtime_status_v2",
    "get_runtime_status_v2",
    "llm_health_v2",
    "llm_metrics_v2",
    "llm_status_v2",
    "migrate_config_v2",
    "router",
    "save_llm_config_v2",
]
