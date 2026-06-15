"""Runtime configuration for role -> provider/model bindings.

This module intentionally avoids import-time singleton side effects and any
reverse dependency on application-level Settings.
"""

from __future__ import annotations

import contextvars
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
    "KERNELONE_ROLE_MODEL_BINDING_MODE",
)
_ROLE_BINDING_MODES = {"strict", "warn"}
_DEFAULT_ROLE_BINDING_MODE = "strict"
MASKED_SECRET = "********"


@dataclass(frozen=True)
class RoleModelConfig:
    """Resolved model configuration for a role.

    ``provider_pool`` lists the provider ids a role may be spread across for
    concurrent execution (each provider is one backend endpoint); it defaults to
    ``(provider_id,)`` so single-endpoint configs are unchanged. ``concurrency``
    is the number of parallel workers requested for the role (default 1).
    """

    role_id: str
    provider_id: str
    model: str
    profile: str | None = None
    provider_pool: tuple[str, ...] = ()
    concurrency: int = 1

    def resolved_pool(self) -> tuple[str, ...]:
        """The effective endpoint pool — the explicit pool, else the primary id."""
        pool = tuple(pid for pid in self.provider_pool if pid)
        return pool or ((self.provider_id,) if self.provider_id else ())


# Per-worker provider override: a Director worker binds its own backend endpoint
# by setting the override for its role, so every downstream resolver
# (get_role_model -> executor) routes that worker's LLM calls to the assigned
# provider without threading an explicit argument through the whole call stack.
#
# A ContextVar (not threading.local) is used deliberately: the Director adapter
# runs its LLM call through ``asyncio.run`` + ``asyncio.to_thread``, both of
# which propagate the contextvars.Context — so an override set in the worker
# thread reaches the actual HTTP-invoke thread. A freshly spawned worker thread
# still starts from the default empty context, preserving per-worker isolation.
# Default None (never a shared mutable) — read as an empty mapping.
_role_provider_override: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "role_provider_override", default=None
)


def set_role_provider_override(role_id: str, provider_id: str) -> None:
    """Bind ``role_id`` to ``provider_id`` for the current execution context."""
    role = _normalize_runtime_role_id(role_id)
    pid = str(provider_id or "").strip()
    # Copy-on-write so sibling contexts never observe each other's overrides.
    updated = dict(_role_provider_override.get() or {})
    if pid:
        updated[role] = pid
    else:
        updated.pop(role, None)
    _role_provider_override.set(updated)


def clear_role_provider_override(role_id: str | None = None) -> None:
    """Clear the current context's override for ``role_id`` (or all roles)."""
    current = _role_provider_override.get()
    if not current:
        return
    if role_id is None:
        _role_provider_override.set(None)
        return
    updated = dict(current)
    updated.pop(_normalize_runtime_role_id(role_id), None)
    _role_provider_override.set(updated or None)


def _get_role_provider_override(role_id: str) -> str | None:
    overrides = _role_provider_override.get()
    if not overrides:
        return None
    return overrides.get(_normalize_runtime_role_id(role_id))


def get_role_provider_override(role_id: str) -> str | None:
    """Public accessor for the current context's provider override for ``role_id``.

    Returns the bound ``provider_id`` if a context-scoped override is active (e.g. a
    Director worker pinned to a specific backend), else ``None``. Provider/model
    resolution consults this so a worker's binding wins over any provider baked into
    a cached role profile."""
    return _get_role_provider_override(role_id)


def _parse_provider_pool(raw: Any, primary_provider_id: str) -> tuple[str, ...]:
    """Coerce a role's ``provider_pool`` into a deduped ordered tuple.

    The primary ``provider_id`` is always included (first) so the pool is never
    empty and a misconfigured pool still resolves the primary endpoint.
    """
    pool: list[str] = []
    seen: set[str] = set()
    for candidate in (primary_provider_id, *(raw if isinstance(raw, (list, tuple)) else ())):
        pid = str(candidate or "").strip()
        if pid and pid not in seen:
            seen.add(pid)
            pool.append(pid)
    return tuple(pool)


def _parse_concurrency(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(1, value)


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
        env_path = os.environ.get("KERNELONE_LLM_CONFIG")
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
                with open(config_path, encoding="utf-8-sig") as handle:
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
            provider_pool=_parse_provider_pool(role_cfg.get("provider_pool"), provider_id),
            concurrency=_parse_concurrency(role_cfg.get("concurrency")),
        )

    def _provider_model(self, provider_id: str) -> str:
        """Best-effort read of a provider's own configured model name.

        Used when a worker binds a specific backend in a heterogeneous pool whose
        endpoints serve distinctly-named models. Returns "" when absent.
        """
        try:
            providers = self._load_config().get("providers")
        except (RuntimeError, ValueError, OSError):
            return ""
        entry: Any = None
        if isinstance(providers, dict):
            entry = providers.get(provider_id)
        elif isinstance(providers, list):
            for item in providers:
                if isinstance(item, dict) and str(item.get("id") or item.get("provider_id") or "") == provider_id:
                    entry = item
                    break
        if isinstance(entry, dict):
            return str(entry.get("model") or "").strip()
        return ""

    def get_role_model(self, role_id: str) -> tuple[str, str]:
        normalized_role_id = _normalize_runtime_role_id(role_id)
        resolved = self.get_role_config(normalized_role_id)
        if resolved is not None:
            # A worker thread bound to a specific backend overrides the provider
            # id (keeping the role's configured model). Only honoured when the
            # override names a provider actually in the role's pool, so a stale
            # override can never route to an unconfigured endpoint.
            override_pid = _get_role_provider_override(normalized_role_id)
            if override_pid and override_pid in resolved.resolved_pool():
                # Heterogeneous pools: each backend may serve a DIFFERENTLY-NAMED
                # model (e.g. one endpoint serves 'qwen3.6-27b-gpu0', not the role's
                # 'int4'). Prefer the bound provider's own configured model so the
                # request's model name matches the endpoint (a mismatch is a hard
                # 404); fall back to the role model for homogeneous pools.
                override_model = self._provider_model(override_pid) or resolved.model
                logger.debug(
                    "[RuntimeConfig] %s: thread override %s/%s",
                    normalized_role_id,
                    override_pid,
                    override_model,
                )
                return override_pid, override_model
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


def get_role_provider_pool(role_id: str) -> tuple[str, ...]:
    """Provider-id pool a role may be spread across (>=1 entry, or empty if unbound)."""
    resolved = get_runtime_config_manager().get_role_config(role_id)
    return resolved.resolved_pool() if resolved is not None else ()


def get_role_concurrency(role_id: str) -> int:
    """Number of parallel workers requested for a role (>=1)."""
    resolved = get_runtime_config_manager().get_role_config(role_id)
    return resolved.concurrency if resolved is not None else 1


def get_provider_base_url(provider_id: str) -> str:
    """Resolve a provider's base endpoint URL from the runtime LLM config.

    Best-effort: returns "" when the provider/config is absent. Used to health-check
    a multi-backend pool so an offline endpoint can be skipped.
    """
    try:
        config = get_runtime_config_manager()._load_config()
    except (RuntimeError, ValueError, OSError):
        return ""
    providers = config.get("providers")
    entry: Any = None
    if isinstance(providers, dict):
        entry = providers.get(provider_id)
    elif isinstance(providers, list):
        for item in providers:
            if isinstance(item, dict) and str(item.get("id") or item.get("provider_id") or "") == provider_id:
                entry = item
                break
    if isinstance(entry, dict):
        return str(entry.get("base_url") or entry.get("endpoint") or entry.get("api_base") or "").strip()
    return ""


__all__ = [
    "RoleModelConfig",
    "RuntimeConfigManager",
    "get_provider_base_url",
    "get_role_model",
    "get_role_provider_override",
    "get_runtime_config_manager",
    "load_role_config",
    "reset_runtime_config_manager",
    "set_default_model_resolver",
    "set_runtime_config_manager",
]
