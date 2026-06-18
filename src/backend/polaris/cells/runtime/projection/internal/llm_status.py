from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

from polaris.cells.llm.evaluation.public.service import (
    load_llm_test_index,
    load_llm_test_index_candidates,
    readiness_freshness_issue,
)
from polaris.cells.llm.provider_runtime.public.service import role_runtime_support_issue
from polaris.cells.runtime.projection.internal.io_helpers import build_cache_root
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.llm.model_identity import model_identity_equal
from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings

FACTORY_REQUIRED_ROLE_ORDER = ("architect", "pm", "director", "qa")


def load_interview_history_summary(settings: Settings) -> dict[str, Any]:
    """加载交互面试历史摘要。"""

    summary: dict[str, Any] = {
        "lastUpdated": None,
        "latest_by_provider": {},
        "latest_by_role_provider_model": {},
    }
    workspace = _active_workspace(settings)
    if not workspace:
        return summary

    interviews_dir = Path(resolve_runtime_path(workspace, "runtime/llm_tests/interviews"))
    try:
        files = sorted(
            (path for path in interviews_dir.glob("*.json") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return summary

    latest_updated = 0.0
    for path in files[:50]:
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        target_raw = payload.get("target")
        target: dict[str, Any] = target_raw if isinstance(target_raw, dict) else {}
        final_raw = payload.get("final")
        final: dict[str, Any] = final_raw if isinstance(final_raw, dict) else {}
        role = _role_key(target.get("role") or payload.get("role"))
        provider_id = str(target.get("provider_id") or payload.get("provider_id") or "").strip()
        model = str(target.get("model") or payload.get("model") or "").strip()
        if not provider_id:
            continue

        ready = bool(final.get("ready"))
        item = {
            "status": "passed" if ready else "failed",
            "timestamp": str(payload.get("timestamp") or ""),
            "role": role,
            "model": model,
            "report_path": str(path),
        }

        if provider_id not in summary["latest_by_provider"]:
            summary["latest_by_provider"][provider_id] = item
        role_model_key = f"{role}:{provider_id}:{model}".lower()
        if role and model and role_model_key not in summary["latest_by_role_provider_model"]:
            summary["latest_by_role_provider_model"][role_model_key] = item

        try:
            latest_updated = max(latest_updated, path.stat().st_mtime)
        except OSError:
            continue

    if latest_updated:
        summary["lastUpdated"] = datetime.fromtimestamp(latest_updated, tz=timezone.utc).isoformat()
    return summary


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


def _normalize_required_roles(value: Any, *, qa_enabled: bool) -> list[str]:
    if not isinstance(value, list):
        return []

    roles: list[str] = []
    for item in value:
        role = _role_key(item)
        if not role or role == "docs":
            continue
        if role == "qa" and not qa_enabled:
            continue
        if role not in roles:
            roles.append(role)
    return roles


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
    return not tested_role or tested_role == "connectivity" or tested_role == _role_key(role)


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
    tested_timestamp = role_status.get("timestamp")
    return (
        _readiness_candidate_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=tested_provider_id,
            tested_model=tested_model,
            tested_timestamp=tested_timestamp,
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
    tested_timestamp = provider_status.get("timestamp")
    return (
        _readiness_candidate_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=provider_id,
            tested_model=tested_model,
            tested_timestamp=tested_timestamp,
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


def _active_workspace(settings: Settings) -> str:
    for attr in ("workspace_path", "workspace"):
        text = _workspace_text(getattr(settings, attr, ""))
        if text:
            return text
    return ""


def _qa_enabled(settings: Settings) -> bool:
    return bool(getattr(settings, "qa_enabled", True))


def _parse_status_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iter_index_timestamps(index: Any) -> list[datetime]:
    if not isinstance(index, dict):
        return []

    timestamps: list[datetime] = []
    for key in ("last_update", "last_reconcile", "reset_at"):
        parsed = _parse_status_timestamp(index.get(key))
        if parsed is not None:
            timestamps.append(parsed)

    for section_name in ("roles", "providers"):
        section = index.get(section_name)
        if not isinstance(section, dict):
            continue
        for item in section.values():
            if not isinstance(item, dict):
                continue
            parsed = _parse_status_timestamp(item.get("timestamp"))
            if parsed is not None:
                timestamps.append(parsed)

    return timestamps


def _latest_status_update(
    *,
    config_path: str,
    index: Any,
    interview_summary: dict[str, Any],
) -> str | None:
    candidates = _iter_index_timestamps(index)

    interview_updated = _parse_status_timestamp(interview_summary.get("lastUpdated"))
    if interview_updated is not None:
        candidates.append(interview_updated)

    if os.path.isfile(config_path):
        try:
            config_updated = datetime.fromtimestamp(os.path.getmtime(config_path), tz=timezone.utc)
            candidates.append(config_updated)
        except (OSError, RuntimeError, ValueError) as e:
            logger.debug(f"Failed to get config mtime: {e}")

    if not candidates:
        return None
    return max(candidates).isoformat()


def build_llm_status(settings: Settings) -> dict[str, Any]:
    workspace = _active_workspace(settings)
    cache_root = build_cache_root(str(getattr(settings, "ramdisk_root", "") or ""), workspace)
    config = llm_config.load_llm_config(workspace, cache_root, settings=settings)
    index = load_llm_test_index(workspace)
    index_candidates = _dedupe_index_candidates([index, *load_llm_test_index_candidates(workspace)])

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
        test_info, provider_test_info = _select_binding_status(
            indexes=index_candidates,
            role=role_key,
            provider_id=provider_id,
            model=model,
        )
        runtime_issue = _runtime_issue(role, provider_id, provider_cfg)
        runtime_supported = not runtime_issue
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
            "runtime_issue": runtime_issue,
            "readiness_issue": binding_readiness["issue"],
            "readiness_source": binding_readiness["source"],
            "tested_provider_id": binding_readiness["tested_provider_id"],
            "tested_model": binding_readiness["tested_model"],
            "tested_timestamp": binding_readiness["tested_timestamp"],
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

    policies = config.get("policies", {}) if isinstance(config.get("policies"), dict) else {}
    required = _normalize_required_roles(
        policies.get("required_ready_roles") if isinstance(policies, dict) else None,
        qa_enabled=_qa_enabled(settings),
    )

    blocked = [r for r in required if not roles_status.get(r, {}).get("ready")]
    unsupported = [r for r in required if not roles_status.get(r, {}).get("runtime_supported")]
    factory_required = [role for role in FACTORY_REQUIRED_ROLE_ORDER if role != "qa" or _qa_enabled(settings)]
    factory_blocked = [r for r in factory_required if not roles_status.get(r, {}).get("ready")]
    factory_unsupported = [r for r in factory_required if not roles_status.get(r, {}).get("runtime_supported")]

    global_state = "READY"
    if blocked or unsupported:
        global_state = "BLOCKED"
    factory_state = "READY"
    if factory_blocked or factory_unsupported:
        factory_state = "BLOCKED"

    interview_summary = load_interview_history_summary(settings)

    config_path = llm_config.llm_config_path(workspace, cache_root)
    last_updated = _latest_status_update(
        config_path=config_path,
        index=index,
        interview_summary=interview_summary,
    )

    return {
        "roles": roles_status,
        "providers": providers_status,
        "required_ready_roles": required,
        "blocked_roles": blocked,
        "unsupported_roles": unsupported,
        "factory_required_roles": factory_required,
        "factory_blocked_roles": factory_blocked,
        "factory_unsupported_roles": factory_unsupported,
        "factory_state": factory_state,
        "state": global_state,
        "interviews": interview_summary,
        "last_updated": last_updated,
    }


def _runtime_issue(role: str, provider_id: str | None, provider_cfg: dict[str, Any]) -> str:
    return role_runtime_support_issue(role, provider_id, provider_cfg)


def _binding_readiness(
    *,
    role: str,
    provider_id: str,
    model: str,
    test_info: dict[str, Any] | None,
    provider_test_info: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(test_info, dict):
        tested_provider_id = str(test_info.get("provider_id") or "").strip()
        tested_model = str(test_info.get("model") or "").strip()
        tested_timestamp = test_info.get("timestamp")
        issue = _readiness_candidate_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=tested_provider_id,
            tested_model=tested_model,
            tested_timestamp=tested_timestamp,
        )
        if bool(test_info.get("ready")) and issue == "":
            return {
                "ready": True,
                "issue": "",
                "source": "role_index",
                "tested_provider_id": tested_provider_id,
                "tested_model": tested_model,
                "tested_timestamp": tested_timestamp,
            }
        if not bool(test_info.get("ready")) and issue == "":
            issue = "readiness_failed"
        return {
            "ready": False,
            "issue": issue,
            "source": "role_index",
            "tested_provider_id": tested_provider_id,
            "tested_model": tested_model,
            "tested_timestamp": tested_timestamp,
        }

    provider_ready = bool(provider_test_info.get("ready")) if isinstance(provider_test_info, dict) else False
    if provider_ready and _provider_role_compatible(role, provider_test_info):
        tested_model = str((provider_test_info or {}).get("model") or "").strip()
        tested_timestamp = (provider_test_info or {}).get("timestamp")
        issue = _readiness_candidate_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=provider_id,
            tested_model=tested_model,
            tested_timestamp=tested_timestamp,
        )
        if issue == "":
            return {
                "ready": True,
                "issue": "",
                "source": "provider_index",
                "tested_provider_id": provider_id,
                "tested_model": tested_model,
                "tested_timestamp": tested_timestamp,
            }
        return {
            "ready": False,
            "issue": issue,
            "source": "provider_index",
            "tested_provider_id": provider_id,
            "tested_model": tested_model,
            "tested_timestamp": tested_timestamp,
        }

    if isinstance(provider_test_info, dict) and _provider_role_compatible(role, provider_test_info):
        tested_model = str(provider_test_info.get("model") or "").strip()
        tested_timestamp = provider_test_info.get("timestamp")
        issue = _readiness_candidate_issue(
            provider_id=provider_id,
            model=model,
            tested_provider_id=provider_id,
            tested_model=tested_model,
            tested_timestamp=tested_timestamp,
        )
        if issue == "":
            issue = "readiness_failed"
        return {
            "ready": False,
            "issue": issue,
            "source": "provider_index",
            "tested_provider_id": provider_id,
            "tested_model": tested_model,
            "tested_timestamp": tested_timestamp,
        }

    if isinstance(provider_test_info, dict):
        return {
            "ready": False,
            "issue": "provider_role_mismatch",
            "source": "provider_index",
            "tested_provider_id": provider_id,
            "tested_model": str(provider_test_info.get("model") or "").strip(),
            "tested_timestamp": provider_test_info.get("timestamp"),
        }

    return {
        "ready": False,
        "issue": "role_readiness_missing",
        "source": "",
        "tested_provider_id": "",
        "tested_model": "",
        "tested_timestamp": None,
    }


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
    if freshness_issue:
        return freshness_issue
    return ""


def _readiness_identity_issue(
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
