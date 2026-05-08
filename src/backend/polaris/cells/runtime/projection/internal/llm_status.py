from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from polaris.cells.llm.evaluation.public.service import load_llm_test_index
from polaris.cells.llm.provider_runtime.public.service import is_role_runtime_supported
from polaris.cells.runtime.projection.internal.io_helpers import build_cache_root
from polaris.kernelone.llm import config_store as llm_config

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings


def load_interview_history_summary(settings: Settings) -> dict[str, Any]:
    """加载面试历史摘要（简化实现）"""
    return {
        "lastUpdated": None,
        "latest_by_provider": {},
        "latest_by_role_provider_model": {},
    }


def build_llm_status(settings: Settings) -> dict[str, Any]:
    cache_root = build_cache_root(str(settings.ramdisk_root or ""), str(settings.workspace))
    config = llm_config.load_llm_config(str(settings.workspace), cache_root, settings=settings)
    index = load_llm_test_index(settings)

    roles_cfg = config.get("roles", {}) if isinstance(config.get("roles"), dict) else {}
    providers_cfg = config.get("providers", {}) if isinstance(config.get("providers"), dict) else {}
    provider_index = index.get("providers", {}) if isinstance(index.get("providers"), dict) else {}

    roles_status: dict[str, Any] = {}
    providers_status: dict[str, Any] = {}

    for role, role_cfg in roles_cfg.items():
        if not isinstance(role_cfg, dict):
            continue
        provider_id = str(role_cfg.get("provider_id") or "").strip()
        model = str(role_cfg.get("model") or "").strip()
        provider_cfg = providers_cfg.get(provider_id, {}) if isinstance(providers_cfg, dict) else {}
        test_info = (index.get("roles") or {}).get(role) if isinstance(index, dict) else None
        provider_test_info = provider_index.get(provider_id) if isinstance(provider_index, dict) else None
        runtime_supported = _runtime_supported(role, provider_id, provider_cfg)
        binding_readiness = _binding_readiness(
            provider_id=provider_id,
            model=model,
            test_info=test_info if isinstance(test_info, dict) else None,
            provider_test_info=provider_test_info if isinstance(provider_test_info, dict) else None,
        )
        roles_status[role] = {
            "provider_id": provider_id,
            "model": model,
            "profile": role_cfg.get("profile"),
            "ready": binding_readiness["ready"],
            "grade": test_info.get("grade") if isinstance(test_info, dict) else "UNKNOWN",
            "last_run_id": test_info.get("last_run_id") if isinstance(test_info, dict) else None,
            "timestamp": test_info.get("timestamp") if isinstance(test_info, dict) else None,
            "suites": test_info.get("suites") if isinstance(test_info, dict) else None,
            "runtime_supported": runtime_supported,
            "readiness_issue": binding_readiness["issue"],
            "tested_provider_id": binding_readiness["tested_provider_id"],
            "tested_model": binding_readiness["tested_model"],
        }

    for provider_id, provider_cfg in providers_cfg.items():
        if not isinstance(provider_cfg, dict):
            continue
        test_info = provider_index.get(provider_id) if isinstance(provider_index, dict) else None
        providers_status[provider_id] = {
            "ready": test_info.get("ready") if isinstance(test_info, dict) else None,
            "grade": test_info.get("grade") if isinstance(test_info, dict) else "UNKNOWN",
            "last_run_id": test_info.get("last_run_id") if isinstance(test_info, dict) else None,
            "timestamp": test_info.get("timestamp") if isinstance(test_info, dict) else None,
            "suites": test_info.get("suites") if isinstance(test_info, dict) else None,
            "model": test_info.get("model") if isinstance(test_info, dict) else None,
            "role": test_info.get("role") if isinstance(test_info, dict) else None,
        }

    required = config.get("policies", {}).get("required_ready_roles") or []
    if not settings.qa_enabled:
        required = [role for role in required if str(role).strip().lower() != "qa"]

    blocked = [r for r in required if not roles_status.get(r, {}).get("ready")]
    unsupported = [r for r in required if not roles_status.get(r, {}).get("runtime_supported")]

    global_state = "READY"
    if blocked or unsupported:
        global_state = "BLOCKED"

    interview_summary = load_interview_history_summary(settings)

    config_path = llm_config.llm_config_path(str(settings.workspace), cache_root)
    last_updated: str | None = None
    if os.path.isfile(config_path):
        try:
            mtime = os.path.getmtime(config_path)
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            last_updated = dt.isoformat()
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to get config mtime: {e}")

    return {
        "roles": roles_status,
        "providers": providers_status,
        "required_ready_roles": required,
        "blocked_roles": blocked,
        "unsupported_roles": unsupported,
        "state": global_state,
        "interviews": interview_summary,
        "last_updated": last_updated,
    }


def _runtime_supported(role: str, provider_id: str | None, provider_cfg: dict[str, Any]) -> bool:
    return is_role_runtime_supported(role, provider_id, provider_cfg)


def _binding_readiness(
    *,
    provider_id: str,
    model: str,
    test_info: dict[str, Any] | None,
    provider_test_info: dict[str, Any] | None,
) -> dict[str, Any]:
    role_ready = bool(test_info.get("ready")) if isinstance(test_info, dict) else False
    tested_provider_id = str((test_info or {}).get("provider_id") or "").strip()
    tested_model = str((test_info or {}).get("model") or "").strip()

    if not tested_provider_id and isinstance(provider_test_info, dict):
        tested_provider_id = provider_id if provider_test_info else ""
    if not tested_model and isinstance(provider_test_info, dict):
        tested_model = str(provider_test_info.get("model") or "").strip()

    issue = ""
    if not role_ready:
        issue = "role_readiness_missing"
    elif not provider_id or not model:
        issue = "role_binding_missing"
    elif tested_provider_id and tested_provider_id != provider_id:
        issue = "provider_mismatch"
    elif not tested_model:
        issue = "tested_model_missing"
    elif tested_model != model:
        issue = "model_mismatch"

    return {
        "ready": role_ready and issue == "",
        "issue": issue,
        "tested_provider_id": tested_provider_id,
        "tested_model": tested_model,
    }
