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
RUNTIME_DISPATCH_SKIP_TTL_SECONDS = 3600
RUNTIME_DISPATCH_SKIP_REASONS = frozenset(
    {
        "provider_connectivity_unavailable",
        "provider_readiness_failed",
        "provider_unreachable",
    }
)


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


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _provider_name(provider_id: str, provider_cfg: dict[str, Any]) -> str:
    name = str(provider_cfg.get("name") or "").strip()
    return name or provider_id


def _provider_type(provider_cfg: dict[str, Any]) -> str:
    return str(provider_cfg.get("type") or "").strip()


def _binding_matches(candidate: dict[str, Any], provider_id: str, model: str) -> bool:
    return (
        str(candidate.get("provider_id") or "").strip() == provider_id
        and str(candidate.get("model") or "").strip() == model
    )


def _role_binding_payload(
    *,
    binding_cfg: dict[str, Any],
    role_cfg: dict[str, Any],
    providers_cfg: dict[str, Any],
) -> dict[str, Any]:
    provider_id = str(binding_cfg.get("provider_id") or role_cfg.get("provider_id") or "").strip()
    provider_cfg = providers_cfg.get(provider_id, {}) if isinstance(providers_cfg, dict) else {}
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}
    model = str(
        binding_cfg.get("model") or role_cfg.get("model") or provider_cfg.get("model") or "",
    ).strip()
    return {
        "provider_id": provider_id,
        "provider_name": _provider_name(provider_id, provider_cfg),
        "provider_type": _provider_type(provider_cfg),
        "model": model,
        "profile": binding_cfg.get("profile") or role_cfg.get("profile"),
        "max_context_tokens": (
            _positive_int_or_none(binding_cfg.get("max_context_tokens"))
            or _positive_int_or_none(role_cfg.get("max_context_tokens"))
            or _positive_int_or_none(provider_cfg.get("max_context_tokens"))
        ),
        "max_output_tokens": (
            _positive_int_or_none(binding_cfg.get("max_output_tokens"))
            or _positive_int_or_none(role_cfg.get("max_output_tokens"))
            or _positive_int_or_none(provider_cfg.get("max_output_tokens"))
            or _positive_int_or_none(provider_cfg.get("max_tokens"))
        ),
    }


def _role_bindings_payload(
    *,
    role_cfg: dict[str, Any],
    providers_cfg: dict[str, Any],
    provider_id: str,
    model: str,
) -> list[dict[str, Any]]:
    raw_bindings = role_cfg.get("bindings")
    candidates: list[dict[str, Any]] = []
    if isinstance(raw_bindings, list):
        for item in raw_bindings:
            if isinstance(item, dict):
                candidates.append(item)

    primary = {"provider_id": provider_id, "model": model, "profile": role_cfg.get("profile")}
    if provider_id and not any(_binding_matches(candidate, provider_id, model) for candidate in candidates):
        candidates.insert(0, primary)
    elif not candidates:
        candidates.append(primary)

    bindings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        payload = _role_binding_payload(
            binding_cfg=candidate,
            role_cfg=role_cfg,
            providers_cfg=providers_cfg,
        )
        identity = (str(payload.get("provider_id") or ""), str(payload.get("model") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        bindings.append(payload)
    return bindings


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


def _director_binding_skip_reason(
    *,
    role: str,
    provider_id: str,
    model: str,
    provider_test_info: dict[str, Any] | None,
) -> str:
    if _role_key(role) != "director":
        return ""
    if not isinstance(provider_test_info, dict) or bool(provider_test_info.get("ready")):
        return ""
    if not _provider_role_compatible(role, provider_test_info):
        return ""
    tested_model = str(provider_test_info.get("model") or "").strip()
    tested_timestamp = provider_test_info.get("timestamp")
    issue = _readiness_candidate_issue(
        provider_id=provider_id,
        model=model,
        tested_provider_id=provider_id,
        tested_model=tested_model,
        tested_timestamp=tested_timestamp,
    )
    if issue:
        return ""
    suites = provider_test_info.get("suites")
    connectivity = suites.get("connectivity") if isinstance(suites, dict) else None
    if isinstance(connectivity, dict) and connectivity.get("ok") is False:
        return "provider_connectivity_unavailable"
    grade = str(provider_test_info.get("grade") or "").strip().upper()
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


def _runtime_dispatch_skip_ttl_seconds() -> int:
    raw = str(os.getenv("KERNELONE_LLM_STATUS_RUNTIME_SKIP_TTL_SECONDS") or "").strip()
    if not raw:
        return RUNTIME_DISPATCH_SKIP_TTL_SECONDS
    try:
        parsed = int(raw)
    except ValueError:
        return RUNTIME_DISPATCH_SKIP_TTL_SECONDS
    return max(0, parsed)


def _load_runtime_dispatch_director_skips(*, workspace: str, ramdisk_root: str | None) -> list[dict[str, Any]]:
    if not workspace:
        return []
    try:
        path = Path(resolve_runtime_path(workspace, "runtime/dispatch/log.json", ramdisk_root=ramdisk_root))
        stat = path.stat()
    except (OSError, RuntimeError, ValueError):
        return []

    timestamp = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    ttl_seconds = _runtime_dispatch_skip_ttl_seconds()
    if ttl_seconds and (datetime.now(timezone.utc) - timestamp).total_seconds() > ttl_seconds:
        return []

    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    raw_per_binding = metadata.get("per_binding") or payload.get("per_binding")
    if not isinstance(raw_per_binding, list):
        return []

    evidence: list[dict[str, Any]] = []
    timestamp_text = timestamp.isoformat()
    for item in raw_per_binding:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider_id") or "").strip()
        model = str(item.get("model") or "").strip()
        skip_reason = str(item.get("skip_reason") or item.get("reason") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        skipped = bool(item.get("skipped")) or status == "skipped"
        if not provider_id or not model or not skipped or skip_reason not in RUNTIME_DISPATCH_SKIP_REASONS:
            continue
        evidence.append(
            {
                "provider_id": provider_id,
                "model": model,
                "binding_id": str(item.get("binding_id") or "").strip(),
                "ready": False,
                "issue": skip_reason,
                "source": "runtime_dispatch",
                "tested_provider_id": provider_id,
                "tested_model": model,
                "tested_timestamp": timestamp_text,
                "skip_allowed": True,
                "skip_reason": skip_reason,
                "evidence_path": str(path),
            }
        )
    return evidence


def _runtime_dispatch_skip_matches_binding(
    evidence: dict[str, Any],
    *,
    provider_id: str,
    model: str,
) -> bool:
    if str(evidence.get("provider_id") or "").strip() != provider_id:
        return False
    tested_model = str(evidence.get("model") or evidence.get("tested_model") or "").strip()
    return bool(tested_model and model and model_identity_equal(tested_model, model))


def _runtime_dispatch_skip_is_newer(
    evidence: dict[str, Any],
    readiness: dict[str, Any] | None,
) -> bool:
    evidence_time = _parse_status_timestamp(evidence.get("tested_timestamp"))
    if evidence_time is None:
        return True
    readiness_time = _parse_status_timestamp((readiness or {}).get("tested_timestamp"))
    return readiness_time is None or evidence_time > readiness_time


def _runtime_dispatch_readiness_for_binding(
    evidence_items: list[dict[str, Any]],
    *,
    provider_id: str,
    model: str,
    readiness: dict[str, Any],
) -> dict[str, Any] | None:
    for evidence in evidence_items:
        if not _runtime_dispatch_skip_matches_binding(evidence, provider_id=provider_id, model=model):
            continue
        if not _runtime_dispatch_skip_is_newer(evidence, readiness):
            continue
        return dict(evidence)
    return None


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


def _binding_readiness_payload(
    *,
    indexes: list[dict[str, Any]],
    role: str,
    provider_id: str,
    model: str,
) -> dict[str, Any]:
    test_info, provider_test_info = _select_binding_status(
        indexes=indexes,
        role=role,
        provider_id=provider_id,
        model=model,
    )
    return _binding_readiness(
        role=role,
        provider_id=provider_id,
        model=model,
        test_info=test_info if isinstance(test_info, dict) else None,
        provider_test_info=provider_test_info if isinstance(provider_test_info, dict) else None,
    )


def _binding_status_issue(readiness: dict[str, Any]) -> str:
    issue = str(readiness.get("issue") or "role_readiness_missing").strip() or "role_readiness_missing"
    return issue


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
    extra_timestamps: list[Any] | None = None,
) -> str | None:
    candidates = _iter_index_timestamps(index)

    interview_updated = _parse_status_timestamp(interview_summary.get("lastUpdated"))
    if interview_updated is not None:
        candidates.append(interview_updated)
    for value in extra_timestamps or []:
        parsed = _parse_status_timestamp(value)
        if parsed is not None:
            candidates.append(parsed)

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
    ramdisk_root = str(getattr(settings, "ramdisk_root", "") or "")
    cache_root = build_cache_root(ramdisk_root, workspace)
    config = llm_config.load_llm_config(workspace, cache_root, settings=settings)
    index = load_llm_test_index(workspace)
    index_candidates = _dedupe_index_candidates([index, *load_llm_test_index_candidates(workspace)])
    runtime_dispatch_skips = _load_runtime_dispatch_director_skips(
        workspace=workspace,
        ramdisk_root=ramdisk_root or None,
    )

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
        provider_cfg = provider_cfg if isinstance(provider_cfg, dict) else {}
        role_key = _role_key(role)
        bindings = _role_bindings_payload(
            role_cfg=role_cfg,
            providers_cfg=providers_cfg,
            provider_id=provider_id,
            model=model,
        )
        binding_readiness_items: list[dict[str, Any]] = []
        for binding in bindings:
            binding_provider_id = str(binding.get("provider_id") or "").strip()
            binding_model = str(binding.get("model") or "").strip()
            readiness = _binding_readiness_payload(
                indexes=index_candidates,
                role=role_key,
                provider_id=binding_provider_id,
                model=binding_model,
            )
            runtime_readiness = _runtime_dispatch_readiness_for_binding(
                runtime_dispatch_skips if role_key == "director" else [],
                provider_id=binding_provider_id,
                model=binding_model,
                readiness=readiness,
            )
            if runtime_readiness is not None:
                readiness = runtime_readiness
            readiness_binding_id = str(readiness.get("binding_id") or "").strip()
            binding.update(
                {
                    "binding_id": str(binding.get("binding_id") or readiness_binding_id),
                    "ready": readiness["ready"],
                    "readiness_issue": readiness["issue"],
                    "readiness_source": readiness["source"],
                    "tested_provider_id": readiness["tested_provider_id"],
                    "tested_model": readiness["tested_model"],
                    "tested_timestamp": readiness["tested_timestamp"],
                    "skip_allowed": bool(readiness.get("skip_allowed")),
                    "skip_reason": str(readiness.get("skip_reason") or ""),
                },
            )
            binding_readiness_items.append(readiness)
        primary_binding = bindings[0] if bindings else {}
        primary_test_info, _primary_provider_test_info = _select_binding_status(
            indexes=index_candidates,
            role=role_key,
            provider_id=str(primary_binding.get("provider_id") or provider_id or "").strip(),
            model=str(primary_binding.get("model") or model or "").strip(),
        )
        primary_readiness = (
            binding_readiness_items[0]
            if binding_readiness_items
            else _binding_readiness_payload(
                indexes=index_candidates,
                role=role_key,
                provider_id=provider_id,
                model=model,
            )
        )
        runtime_issue = _runtime_issue(role, provider_id, provider_cfg)
        runtime_supported = not runtime_issue
        failed_binding_issue = ""
        skipped_bindings: list[dict[str, Any]] = []
        blocking_binding_count = 0
        for binding, readiness in zip(bindings, binding_readiness_items, strict=False):
            if not bool(readiness.get("ready")):
                if bool(readiness.get("skip_allowed")):
                    skipped_bindings.append(
                        {
                            "provider_id": str(binding.get("provider_id") or "").strip(),
                            "model": str(binding.get("model") or "").strip(),
                            "binding_id": str(binding.get("binding_id") or readiness.get("binding_id") or "").strip(),
                            "reason": str(readiness.get("skip_reason") or readiness.get("issue") or "").strip(),
                            "readiness_source": str(readiness.get("source") or "").strip(),
                        }
                    )
                    continue
                blocking_binding_count += 1
                if not failed_binding_issue:
                    failed_binding_issue = _binding_status_issue(readiness)
        all_bindings_ready = all(bool(item.get("ready")) for item in binding_readiness_items)
        any_binding_ready = any(bool(item.get("ready")) for item in binding_readiness_items)
        role_degraded = bool(
            role_key == "director"
            and any_binding_ready
            and not all_bindings_ready
            and blocking_binding_count == 0
            and skipped_bindings
        )
        if role_degraded and not failed_binding_issue:
            failed_binding_issue = "degraded: skipped unavailable Director binding(s)"
        elif (
            role_key == "director"
            and skipped_bindings
            and not any_binding_ready
            and blocking_binding_count == 0
            and not failed_binding_issue
        ):
            failed_binding_issue = "all Director bindings unavailable after runtime dispatch readiness filtering"
        role_ready = bool((all_bindings_ready and binding_readiness_items) or role_degraded)
        roles_status[role_key] = {
            "provider_id": provider_id,
            "provider_name": primary_binding.get("provider_name") or _provider_name(provider_id, provider_cfg),
            "provider_type": primary_binding.get("provider_type") or _provider_type(provider_cfg),
            "model": model,
            "profile": role_cfg.get("profile"),
            "max_context_tokens": primary_binding.get("max_context_tokens"),
            "max_output_tokens": primary_binding.get("max_output_tokens"),
            "bindings": bindings,
            "ready": role_ready,
            "grade": primary_test_info.get("grade") if isinstance(primary_test_info, dict) else "UNKNOWN",
            "last_run_id": primary_test_info.get("last_run_id") if isinstance(primary_test_info, dict) else None,
            "timestamp": primary_readiness["tested_timestamp"],
            "suites": primary_test_info.get("suites") if isinstance(primary_test_info, dict) else None,
            "runtime_supported": runtime_supported,
            "runtime_issue": runtime_issue,
            "readiness_issue": failed_binding_issue,
            "readiness_source": primary_readiness["source"],
            "tested_provider_id": primary_readiness["tested_provider_id"],
            "tested_model": primary_readiness["tested_model"],
            "tested_timestamp": primary_readiness["tested_timestamp"],
            "degraded": role_degraded,
            "skipped_bindings": skipped_bindings,
        }

    for provider_id, provider_cfg in providers_cfg.items():
        if not isinstance(provider_cfg, dict):
            continue
        test_info = provider_index.get(provider_id) if isinstance(provider_index, dict) else None
        runtime_provider_skip = next(
            (
                evidence
                for evidence in runtime_dispatch_skips
                if str(evidence.get("provider_id") or "").strip() == str(provider_id)
                and _runtime_dispatch_skip_is_newer(
                    evidence,
                    {
                        "tested_timestamp": test_info.get("timestamp") if isinstance(test_info, dict) else None,
                    },
                )
            ),
            None,
        )
        providers_status[provider_id] = {
            "ready": False
            if runtime_provider_skip is not None
            else test_info.get("ready")
            if isinstance(test_info, dict)
            else None,
            "grade": "FAIL"
            if runtime_provider_skip is not None
            else test_info.get("grade")
            if isinstance(test_info, dict)
            else "UNKNOWN",
            "last_run_id": test_info.get("last_run_id") if isinstance(test_info, dict) else None,
            "timestamp": runtime_provider_skip.get("tested_timestamp")
            if runtime_provider_skip is not None
            else test_info.get("timestamp")
            if isinstance(test_info, dict)
            else None,
            "suites": {"connectivity": {"ok": False, "reason": runtime_provider_skip.get("skip_reason")}}
            if runtime_provider_skip is not None
            else test_info.get("suites")
            if isinstance(test_info, dict)
            else None,
            "name": _provider_name(str(provider_id), provider_cfg),
            "type": _provider_type(provider_cfg),
            "max_context_tokens": _positive_int_or_none(provider_cfg.get("max_context_tokens")),
            "max_output_tokens": _positive_int_or_none(provider_cfg.get("max_output_tokens"))
            or _positive_int_or_none(provider_cfg.get("max_tokens")),
            "model": runtime_provider_skip.get("tested_model")
            if runtime_provider_skip is not None
            else test_info.get("model")
            if isinstance(test_info, dict)
            else None,
            "role": "director"
            if runtime_provider_skip is not None
            else test_info.get("role")
            if isinstance(test_info, dict)
            else None,
            "readiness_source": runtime_provider_skip.get("source") if runtime_provider_skip is not None else None,
            "skip_reason": runtime_provider_skip.get("skip_reason") if runtime_provider_skip is not None else "",
        }

    policies = config.get("policies", {}) if isinstance(config.get("policies"), dict) else {}
    required = _normalize_required_roles(
        policies.get("required_ready_roles") if isinstance(policies, dict) else None,
        qa_enabled=_qa_enabled(settings),
    )

    blocked = [r for r in required if not roles_status.get(r, {}).get("ready")]
    unsupported = [r for r in required if not roles_status.get(r, {}).get("runtime_supported")]
    degraded = [r for r in required if roles_status.get(r, {}).get("degraded")]
    factory_required = [role for role in FACTORY_REQUIRED_ROLE_ORDER if role != "qa" or _qa_enabled(settings)]
    factory_blocked = [r for r in factory_required if not roles_status.get(r, {}).get("ready")]
    factory_unsupported = [r for r in factory_required if not roles_status.get(r, {}).get("runtime_supported")]
    factory_degraded = [r for r in factory_required if roles_status.get(r, {}).get("degraded")]

    global_state = "READY"
    if blocked or unsupported:
        global_state = "BLOCKED"
    elif degraded:
        global_state = "DEGRADED"
    factory_state = "READY"
    if factory_blocked or factory_unsupported:
        factory_state = "BLOCKED"
    elif factory_degraded:
        factory_state = "DEGRADED"

    interview_summary = load_interview_history_summary(settings)

    config_path = llm_config.llm_config_path(workspace, cache_root)
    last_updated = _latest_status_update(
        config_path=config_path,
        index=index,
        interview_summary=interview_summary,
        extra_timestamps=[item.get("tested_timestamp") for item in runtime_dispatch_skips],
    )

    return {
        "roles": roles_status,
        "providers": providers_status,
        "required_ready_roles": required,
        "blocked_roles": blocked,
        "unsupported_roles": unsupported,
        "degraded_roles": degraded,
        "factory_required_roles": factory_required,
        "factory_blocked_roles": factory_blocked,
        "factory_unsupported_roles": factory_unsupported,
        "factory_degraded_roles": factory_degraded,
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
        skip_reason = _director_binding_skip_reason(
            role=role,
            provider_id=provider_id,
            model=model,
            provider_test_info=provider_test_info,
        )
        return {
            "ready": False,
            "issue": issue,
            "source": "provider_index",
            "tested_provider_id": provider_id,
            "tested_model": tested_model,
            "tested_timestamp": tested_timestamp,
            "skip_allowed": bool(skip_reason),
            "skip_reason": skip_reason,
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
