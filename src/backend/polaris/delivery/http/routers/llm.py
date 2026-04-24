"""Canonical LLM delivery router in Polaris."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.cells.llm.evaluation.public.service import reconcile_llm_test_index
from polaris.cells.llm.provider_config.public.service import sync_settings_from_llm
from polaris.cells.llm.provider_runtime.public.service import get_provider_manager
from polaris.cells.runtime.projection.public.service import build_llm_status
from polaris.cells.storage.layout.public.service import save_persisted_settings
from polaris.delivery.http.routers._shared import get_state, require_auth
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.llm.runtime_config import load_role_config
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

router = APIRouter()
logger = logging.getLogger(__name__)

# Resolve provider_manager from the Cell layer (which delegates to kernelone)
_provider_manager = get_provider_manager()


def _normalize_runtime_role_id(role_id: str) -> str:
    normalized = str(role_id or "").strip().lower()
    if normalized == "docs":
        return "architect"
    return normalized


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


@router.get("/llm/config", dependencies=[Depends(require_auth)])
def get_llm_config(request: Request) -> dict[str, Any]:
    state = get_state(request)
    cache_root = build_cache_root(state.settings.ramdisk_root or "", str(state.settings.workspace))
    config = llm_config.load_llm_config(str(state.settings.workspace), cache_root, settings=state.settings)
    return llm_config.redact_llm_config(config)


@router.post("/llm/config", dependencies=[Depends(require_auth)])
def save_llm_config(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    state = get_state(request)
    cache_root = build_cache_root(state.settings.ramdisk_root or "", str(state.settings.workspace))
    config_payload = payload.get("config") if isinstance(payload, dict) and "config" in payload else payload
    if not isinstance(config_payload, dict):
        raise HTTPException(status_code=400, detail="invalid config payload")

    config = llm_config.save_llm_config(
        str(state.settings.workspace),
        cache_root,
        config_payload,
        settings=state.settings,
    )
    reconcile_llm_test_index(state.settings, config)
    sync_settings_from_llm(state.settings, config)
    save_persisted_settings(state.settings)
    return llm_config.redact_llm_config(config)


@router.post("/llm/config/migrate", dependencies=[Depends(require_auth)])
def migrate_config(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return _provider_manager.migrate_legacy_config(payload)
    except (RuntimeError, ValueError) as exc:  # pragma: no cover - defensive runtime path
        logger.error("migrate_config failed: %s", exc)
        raise HTTPException(status_code=500, detail="internal error") from exc


@router.get("/llm/status", dependencies=[Depends(require_auth)])
def llm_status(request: Request) -> dict[str, Any]:
    state = get_state(request)
    return build_llm_status(state.settings)


@router.get("/llm/runtime-status", dependencies=[Depends(require_auth)])
def get_runtime_status(request: Request) -> dict[str, Any]:
    state = get_state(request)
    cache_root = build_cache_root(state.settings.ramdisk_root or "", str(state.settings.workspace))
    runtime_dir = resolve_artifact_path(str(state.settings.workspace), cache_root, "runtime")

    roles_status: dict[str, dict[str, Any]] = {}
    for role_id in ("pm", "director", "qa", "architect"):
        roles_status[role_id] = _build_role_runtime_status(runtime_dir, role_id)

    return {
        "roles": roles_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/llm/runtime-status/{role_id}", dependencies=[Depends(require_auth)])
def get_role_runtime_status(request: Request, role_id: str) -> dict[str, Any]:
    normalized_role_id = _normalize_runtime_role_id(role_id)
    if normalized_role_id not in ("pm", "director", "qa", "architect"):
        raise HTTPException(status_code=400, detail="invalid role_id")

    state = get_state(request)
    cache_root = build_cache_root(state.settings.ramdisk_root or "", str(state.settings.workspace))
    runtime_dir = resolve_artifact_path(str(state.settings.workspace), cache_root, "runtime")

    role_status = _build_role_runtime_status(runtime_dir, normalized_role_id)
    role_status["roleId"] = normalized_role_id
    return role_status


__all__ = [
    "get_llm_config",
    "get_role_runtime_status",
    "get_runtime_status",
    "llm_status",
    "migrate_config",
    "router",
    "save_llm_config",
]
