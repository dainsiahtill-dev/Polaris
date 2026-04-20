"""Runtime configuration for role -> provider/model bindings.

This module intentionally avoids import-time singleton side effects and any
reverse dependency on application-level Settings.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.kernelone.storage import resolve_global_path

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_ROLE_BINDING_MODE_ENV_KEYS = (
    "KERNELONE_ROLE_MODEL_BINDING_MODE",
    "POLARIS_ROLE_MODEL_BINDING_MODE",
)
_ROLE_BINDING_MODES = {"strict", "warn"}
_DEFAULT_ROLE_BINDING_MODE = "strict"
MASKED_SECRET = "********"


@dataclass(frozen=True)
class RoleModelConfig:
    """Resolved model configuration for a role."""

    role_id: str
    provider_id: str
    model: str
    profile: str | None = None


def _normalize_runtime_role_id(role_id: str) -> str:
    normalized = str(role_id or "").strip().lower()
    if normalized == "docs":
        return "architect"
    return normalized


def _resolve_role_binding_mode() -> str:
    raw = _DEFAULT_ROLE_BINDING_MODE
    for key in _ROLE_BINDING_MODE_ENV_KEYS:
        candidate = str(os.environ.get(key, "") or "").strip().lower()
        if candidate:
            raw = candidate
            break
    if raw not in _ROLE_BINDING_MODES:
        return _DEFAULT_ROLE_BINDING_MODE
    return raw


_default_model_resolver: Callable[[], str] | None = None
_default_model_resolver_lock = threading.RLock()
_runtime_config_manager: RuntimeConfigManager | None = None
_runtime_config_lock = threading.RLock()


def set_default_model_resolver(resolver: Callable[[], str] | None) -> None:
    """Inject a bootstrap-owned resolver for default model selection."""

    global _default_model_resolver
    with _default_model_resolver_lock:
        _default_model_resolver = resolver


class RuntimeConfigManager:
    """Role model configuration manager with explicit lazy lifecycle."""

    def __init__(
        self,
        *,
        config_path_resolver: Callable[[], str] | None = None,
    ) -> None:
        self._config_path_resolver = config_path_resolver
        self._config_cache: dict[str, Any] | None = None
        self._config_mtime: float = 0.0
        self._lock = threading.RLock()

    def _get_config_path(self) -> str:
        if self._config_path_resolver is not None:
            path = str(self._config_path_resolver() or "").strip()
            if path:
                return path
        env_path = os.environ.get("KERNELONE_LLM_CONFIG") or os.environ.get("POLARIS_LLM_CONFIG")
        if env_path:
            return str(env_path)
        return resolve_global_path("config/llm/llm_config.json")

    def _load_config(self) -> dict[str, Any]:
        config_path = self._get_config_path()
        if not os.path.exists(config_path):
            logger.debug("[RuntimeConfig] Config file not found: %s", config_path)
            return {}

        with self._lock:
            try:
                mtime = os.path.getmtime(config_path)
            except OSError as exc:
                logger.warning("[RuntimeConfig] Failed to stat config: %s", exc)
                return {}

            if self._config_cache is not None and mtime <= self._config_mtime:
                return dict(self._config_cache)

            try:
                with open(config_path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("[RuntimeConfig] Failed to load config: %s", exc)
                return {}

            config = loaded if isinstance(loaded, dict) else {}
            self._config_cache = dict(config)
            self._config_mtime = mtime
            logger.debug("[RuntimeConfig] Loaded config from: %s", config_path)
            return dict(config)

    def get_role_config(self, role_id: str) -> RoleModelConfig | None:
        config = self._load_config()
        normalized_role_id = _normalize_runtime_role_id(role_id)

        assignments = config.get("roleAssignments", [])
        if isinstance(assignments, list):
            for assignment in assignments:
                if not isinstance(assignment, dict):
                    continue
                assignment_role = _normalize_runtime_role_id(str(assignment.get("roleId") or ""))
                if assignment_role != normalized_role_id:
                    continue
                provider_id = str(assignment.get("providerId") or "").strip()
                model = str(assignment.get("model") or "").strip()
                if not provider_id or not model:
                    continue
                return RoleModelConfig(
                    role_id=normalized_role_id,
                    provider_id=provider_id,
                    model=model,
                    profile=str(assignment.get("profile") or "").strip() or None,
                )

        roles = config.get("roles", {})
        if not isinstance(roles, dict):
            return None

        role_cfg = roles.get(normalized_role_id, {})
        if not role_cfg and normalized_role_id == "architect":
            role_cfg = roles.get("docs", {})
        if not isinstance(role_cfg, dict):
            return None

        provider_id = str(role_cfg.get("provider_id") or "").strip()
        model = str(role_cfg.get("model") or "").strip()
        if not provider_id or not model:
            return None

        return RoleModelConfig(
            role_id=normalized_role_id,
            provider_id=provider_id,
            model=model,
            profile=str(role_cfg.get("profile") or "").strip() or None,
        )

    def get_role_model(self, role_id: str) -> tuple[str, str]:
        normalized_role_id = _normalize_runtime_role_id(role_id)
        resolved = self.get_role_config(normalized_role_id)
        if resolved is not None:
            logger.debug(
                "[RuntimeConfig] %s: using %s/%s",
                normalized_role_id,
                resolved.provider_id,
                resolved.model,
            )
            return resolved.provider_id, resolved.model

        binding_mode = _resolve_role_binding_mode()
        logger.warning(
            "[RuntimeConfig] %s: no explicit role-model binding found (binding_mode=%s)",
            normalized_role_id,
            binding_mode,
        )
        return "", ""

    def get_all_role_configs(self) -> dict[str, RoleModelConfig]:
        configs: dict[str, RoleModelConfig] = {}
        for role_id in ("pm", "director", "qa", "architect"):
            role_config = self.get_role_config(role_id)
            if role_config is not None:
                configs[role_id] = role_config
        return configs

    def clear_cache(self) -> None:
        with self._lock:
            self._config_cache = None
            self._config_mtime = 0.0


def get_runtime_config_manager() -> RuntimeConfigManager:
    global _runtime_config_manager
    with _runtime_config_lock:
        if _runtime_config_manager is None:
            _runtime_config_manager = RuntimeConfigManager()
        return _runtime_config_manager


def set_runtime_config_manager(manager: RuntimeConfigManager) -> None:
    global _runtime_config_manager
    if manager is None:
        raise ValueError("manager is required")
    with _runtime_config_lock:
        _runtime_config_manager = manager


def reset_runtime_config_manager() -> None:
    global _runtime_config_manager
    with _runtime_config_lock:
        _runtime_config_manager = None


def get_role_model(role_id: str) -> tuple[str, str]:
    """Get role provider/model tuple using lazy runtime config manager."""

    return get_runtime_config_manager().get_role_model(role_id)


def load_role_config(role_id: str) -> RoleModelConfig | None:
    """Load complete role config from runtime settings."""

    return get_runtime_config_manager().get_role_config(role_id)


__all__ = [
    "RoleModelConfig",
    "RuntimeConfigManager",
    "get_role_model",
    "get_runtime_config_manager",
    "load_role_config",
    "reset_runtime_config_manager",
    "set_default_model_resolver",
    "set_runtime_config_manager",
]
