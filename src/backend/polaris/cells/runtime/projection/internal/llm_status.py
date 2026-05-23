from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

from polaris.cells.llm.evaluation.public.service import load_llm_test_index
from polaris.cells.llm.provider_runtime.public.service import is_role_runtime_supported
from polaris.cells.runtime.projection.internal.io_helpers import build_cache_root
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.llm.model_identity import model_identity_equal

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


def _workspace_text(value: Any) -> str:
    if isinstance(value, Mock):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, os.PathLike):
        path = os.fspath(value)
        return path.strip() if isinstance(path, str) else ""
    return ""


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


def _provider_role_compatible(role: str, provider_test_info: dict[str, Any] | None) -> bool:
    if not isinstance(provider_test_info, dict):
        return False
    tested_role = _role_key(provider_test_info.get("role"))
    return not tested_role or tested_role == _role_key(role)


def _active_workspace(settings: Settings) -> str:
    for attr in ("workspace_path", "workspace"):
        text = _workspace_text(getattr(settings, attr, ""))
        if text:
            return text
    return ""


def build_llm_status(settings: Settings) -> dict[str, Any]:
    workspace = _active_workspace(settings)
    cache_root = build_cache_root(str(settings.ramdisk_root or ""), workspace)
    config = llm_config.load_llm_config(workspace, cache_root, settings=settings)
    index = load_llm_test_index(workspace)

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
        role_key = _role_key(role)
        test_info = _lookup_role_status(index, role_key) if isinstance(index, dict) else None
        provider_test_info = provider_index.get(provider_id) if isinstance(provider_index, dict) else None
        runtime_supported = _runtime_supported(role, provider_id, provider_cfg)
        binding_readiness = _binding_readiness(
            role=role_key,
            provider_id=provider_id,
            model=model,
            test_info=test_info if isinstance(test_info, dict) else None,
            provider_test_info=provider_test_info if isinstance(provider_test_info, dict) else None,
        )
        roles_status[role_key] = {
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
            "readiness_source": binding_readiness["source"],
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

    config_path = llm_config.llm_config_path(workspace, cache_root)
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
    role: str,
    provider_id: str,
    model: str,
    test_info: dict[str, Any] | None,
    provider_test_info: dict[str, Any] | None,
) -> dict[str, Any]:
    candidates: list[tuple[str, str, str]] = []
    if isinstance(test_info, dict) and bool(test_info.get("ready")):
        candidates.append(
            (
                "role_index",
                str(test_info.get("provider_id") or "").strip(),
                str(test_info.get("model") or "").strip(),
            )
        )

    provider_ready = bool(provider_test_info.get("ready")) if isinstance(provider_test_info, dict) else False
    if provider_ready and _provider_role_compatible(role, provider_test_info):
        candidates.append(
            (
                "provider_index",
                provider_id,
                str((provider_test_info or {}).get("model") or "").strip(),
            )
        )

    fallback_issue = "role_readiness_missing"
    fallback_provider_id = ""
    fallback_model = ""
    fallback_source = ""
    for source, tested_provider_id, tested_model in candidates:
        issue = _readiness_candidate_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=tested_provider_id,
            tested_model=tested_model,
        )
        if issue == "":
            return {
                "ready": True,
                "issue": "",
                "source": source,
                "tested_provider_id": tested_provider_id,
                "tested_model": tested_model,
            }
        if not fallback_source:
            fallback_issue = issue
            fallback_provider_id = tested_provider_id
            fallback_model = tested_model
            fallback_source = source

    if provider_ready and not _provider_role_compatible(role, provider_test_info):
        fallback_issue = "provider_role_mismatch"
        fallback_source = "provider_index"
        fallback_provider_id = provider_id
        fallback_model = str((provider_test_info or {}).get("model") or "").strip()

    return {
        "ready": False,
        "issue": fallback_issue,
        "source": fallback_source,
        "tested_provider_id": fallback_provider_id,
        "tested_model": fallback_model,
    }


def _readiness_candidate_issue(
    *,
    provider_id: str,
    model: str,
    tested_provider_id: str,
    tested_model: str,
) -> str:
    if not provider_id or not model:
        return "role_binding_missing"
    if tested_provider_id and tested_provider_id != provider_id:
        return "provider_mismatch"
    if not tested_model:
        return "tested_model_missing"
    if not model_identity_equal(tested_model, model):
        return "model_mismatch"
    return ""
