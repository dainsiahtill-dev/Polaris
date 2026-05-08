"""Runtime health checks for Polaris backend.

This module provides runtime health status checks.
No HTTP semantics — callers map domain exceptions to HTTP at the delivery boundary.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from typing import TYPE_CHECKING, Any

from polaris.domain.exceptions import ServiceUnavailableError
from polaris.kernelone.utils.time_utils import utc_now_str

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings


def get_lancedb_status() -> dict[str, Any]:
    try:
        import lancedb  # type: ignore
    except (RuntimeError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "error": str(exc),
            "python": sys.executable,
        }
    version = getattr(lancedb, "__version__", None)
    return {
        "ok": True,
        "error": None,
        "python": sys.executable,
        "version": version,
    }


def require_lancedb() -> None:
    """Check lancedb availability; raise ServiceUnavailableError if not available.

    Callers in the delivery layer should catch this and return 503.
    """
    status = get_lancedb_status()
    if not status.get("ok"):
        detail = f"lancedb not available (python={status.get('python')})"
        error = status.get("error")
        if error:
            detail = f"{detail}: {error}"
        raise ServiceUnavailableError(service="lancedb", message=detail)


def check_backend_available(settings: Settings) -> str | None:
    binding = _resolve_pm_runtime_binding(settings)
    if not binding.get("configured"):
        return "PM role mapping is missing or incomplete (provider_id/model). Configure PM role in LLM settings."

    runtime_kind = str(binding.get("kind") or "").strip().lower()
    if runtime_kind == "codex" and not shutil.which("codex"):
        return "codex command not found in PATH. PM role mapping points to codex provider."
    return None


def build_runtime_issues(settings: Settings, workspace: str) -> list[dict[str, str]]:
    del workspace
    issues: list[dict[str, str]] = []
    binding = _resolve_pm_runtime_binding(settings)
    if not binding.get("configured"):
        issues.append(
            {
                "code": "PM_ROLE_MAPPING_MISSING",
                "title": "PM 角色映射缺失",
                "detail": "PM 角色未绑定 provider_id/model。请在 LLM 角色映射中完成配置。",
            }
        )
        return issues

    runtime_kind = str(binding.get("kind") or "").strip().lower()
    if runtime_kind == "codex" and not shutil.which("codex"):
        issues.append(
            {
                "code": "CODEX_MISSING",
                "title": "Codex 未安装",
                "detail": "检测不到 codex 命令。请安装 Codex 或在设置里切换 PM backend/provider。",
            }
        )
    return issues


def _resolve_pm_runtime_provider_kind(settings: Settings) -> str:
    binding = _resolve_pm_runtime_binding(settings)
    return str(binding.get("kind") or "").strip().lower()


def _resolve_pm_runtime_binding(settings: Settings) -> dict[str, Any]:
    try:
        from polaris.cells.llm.provider_runtime.public import get_role_runtime_provider_kind
        from polaris.kernelone.llm import config_store as llm_config
        from polaris.kernelone.storage.io_paths import build_cache_root

        workspace = str(getattr(settings, "workspace", "") or "").strip() or os.getcwd()
        cache_root = build_cache_root(getattr(settings, "ramdisk_root", "") or "", workspace)
        config = llm_config.load_llm_config(workspace, cache_root, settings=settings)
        if not isinstance(config, dict):
            return {"configured": False, "kind": "", "provider_id": "", "model": ""}
        roles = config.get("roles") if isinstance(config.get("roles"), dict) else {}
        role_cfg = roles.get("pm") if isinstance(roles, dict) else None
        if not isinstance(role_cfg, dict):
            return {"configured": False, "kind": "", "provider_id": "", "model": ""}
        provider_id = str(role_cfg.get("provider_id") or "").strip()
        model = str(role_cfg.get("model") or "").strip()
        if not provider_id or not model:
            return {"configured": False, "kind": "", "provider_id": provider_id, "model": model}
        providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
        provider_cfg = providers.get(provider_id) if isinstance(providers, dict) else {}
        if not isinstance(provider_cfg, dict):
            return {"configured": False, "kind": "", "provider_id": provider_id, "model": model}
        kind = str(get_role_runtime_provider_kind("pm", provider_id, provider_cfg) or "").strip().lower()
        if kind in ("codex", "ollama", "generic"):
            return {
                "configured": True,
                "kind": kind,
                "provider_id": provider_id,
                "model": model,
            }
    except (RuntimeError, TypeError, ValueError) as exc:
        # BUG-006 fix: infrastructure / import errors were silently swallowed
        # at debug level, making the health endpoint always report "unconfigured"
        # in production where debug logging is off.  Promote to warning so the
        # real failure reason is visible, and include the exception type so
        # callers can distinguish infrastructure errors from missing config.
        logger.warning(
            "PM runtime binding resolution failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        return {"configured": False, "kind": "", "provider_id": "", "model": "", "error": str(exc)}
    return {"configured": False, "kind": "", "provider_id": "", "model": ""}


def log_backend_error(event: str, detail: str, **extra: Any) -> None:
    payload: dict[str, Any] = {
        "event": event,
        "detail": detail,
        "ts": utc_now_str(),
    }
    for key, value in extra.items():
        if value is None:
            continue
        if isinstance(value, str) and not value:
            continue
        payload[key] = value
    try:
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(message)
    except (RuntimeError, ValueError) as exc:
        logger.debug("json.dumps payload failed in log_backend_error: %s", exc)
        logger.info("%s: %s", event, detail)

    if detail and "\n" in detail:
        try:
            logger.info("[backend-error-detail]")
            logger.info(detail)
        except (RuntimeError, ValueError) as exc:
            logger.debug("Failed to print backend error detail: %s", exc)
