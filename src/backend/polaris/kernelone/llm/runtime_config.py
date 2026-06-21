"""Runtime configuration for role -> provider/model bindings.

This module intentionally avoids import-time singleton side effects and any
reverse dependency on application-level Settings.
"""

from __future__ import annotations

import contextvars
import ipaddress
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

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
class ResolvedRoleBinding:
    """One explicit role -> provider/model binding from runtime config."""

    role_id: str
    provider_id: str
    model: str
    profile: str | None = None
    max_concurrency: int | None = None
    binding_id: str = ""
    binding_index: int = 0


@dataclass(frozen=True)
class RoleBindingSlot:
    """One schedulable request slot for a role binding."""

    role_id: str
    provider_id: str
    model: str
    profile: str | None = None
    binding_id: str = ""
    binding_index: int = 0
    slot_index: int = 0
    max_concurrency: int = 1

    def __str__(self) -> str:
        return self.provider_id

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.provider_id == other
        if not isinstance(other, RoleBindingSlot):
            return False
        return (
            self.role_id,
            self.provider_id,
            self.model,
            self.binding_id,
            self.binding_index,
            self.slot_index,
        ) == (
            other.role_id,
            other.provider_id,
            other.model,
            other.binding_id,
            other.binding_index,
            other.slot_index,
        )

    def __hash__(self) -> int:
        return hash((self.role_id, self.provider_id, self.model, self.binding_id, self.binding_index, self.slot_index))


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
    bindings: tuple[ResolvedRoleBinding, ...] = ()

    def resolved_pool(self) -> tuple[str, ...]:
        """The effective endpoint pool — the explicit pool, else the primary id."""
        if self.bindings:
            pool: list[str] = []
            seen: set[str] = set()
            for binding in self.bindings:
                if binding.provider_id and binding.provider_id not in seen:
                    seen.add(binding.provider_id)
                    pool.append(binding.provider_id)
            return tuple(pool)
        provider_pool = tuple(pid for pid in self.provider_pool if pid)
        return provider_pool or ((self.provider_id,) if self.provider_id else ())


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
_role_binding_override: contextvars.ContextVar[dict[str, dict[str, str]] | None] = contextvars.ContextVar(
    "role_binding_override", default=None
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
    binding_overrides = dict(_role_binding_override.get() or {})
    binding_overrides.pop(role, None)
    _role_binding_override.set(binding_overrides or None)


def set_role_binding_override(
    role_id: str,
    *,
    provider_id: str,
    model: str,
    binding_id: str | None = None,
    fanout_locked: bool = False,
) -> None:
    """Bind ``role_id`` to a specific provider/model for this execution context."""
    role = _normalize_runtime_role_id(role_id)
    pid = str(provider_id or "").strip()
    model_name = str(model or "").strip()
    updated = dict(_role_binding_override.get() or {})
    if pid and model_name:
        payload = {"provider_id": pid, "model": model_name}
        bid = str(binding_id or "").strip()
        if bid:
            payload["binding_id"] = bid
        if fanout_locked:
            payload["_fanout_locked"] = "true"
        updated[role] = payload
    else:
        updated.pop(role, None)
    _role_binding_override.set(updated or None)

    provider_overrides = dict(_role_provider_override.get() or {})
    if pid:
        provider_overrides[role] = pid
    else:
        provider_overrides.pop(role, None)
    _role_provider_override.set(provider_overrides or None)


def clear_role_provider_override(role_id: str | None = None) -> None:
    """Clear the current context's override for ``role_id`` (or all roles)."""
    current = _role_provider_override.get()
    if role_id is None:
        _role_provider_override.set(None)
        _role_binding_override.set(None)
        return
    normalized_role_id = _normalize_runtime_role_id(role_id)
    if current:
        updated = dict(current)
        updated.pop(normalized_role_id, None)
        _role_provider_override.set(updated or None)
    binding_current = _role_binding_override.get()
    if binding_current:
        binding_updated = dict(binding_current)
        binding_updated.pop(normalized_role_id, None)
        _role_binding_override.set(binding_updated or None)


def _get_role_provider_override(role_id: str) -> str | None:
    overrides = _role_provider_override.get()
    if not overrides:
        return None
    return overrides.get(_normalize_runtime_role_id(role_id))


def _get_role_binding_override(role_id: str) -> dict[str, str] | None:
    overrides = _role_binding_override.get()
    if not overrides:
        return None
    payload = overrides.get(_normalize_runtime_role_id(role_id))
    return dict(payload) if isinstance(payload, dict) else None


def get_role_provider_override(role_id: str) -> str | None:
    """Public accessor for the current context's provider override for ``role_id``.

    Returns the bound ``provider_id`` if a context-scoped override is active (e.g. a
    Director worker pinned to a specific backend), else ``None``. Provider/model
    resolution consults this so a worker's binding wins over any provider baked into
    a cached role profile."""
    return _get_role_provider_override(role_id)


def get_role_binding_override(role_id: str) -> dict[str, str] | None:
    """Return the current context's provider/model binding override, if any."""
    return _get_role_binding_override(role_id)


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


def _parse_optional_positive_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_local_base_url(raw_url: Any) -> bool:
    value = str(raw_url or "").strip()
    if not value:
        return False
    try:
        host = urlparse(value).hostname or ""
    except ValueError:
        return False
    lowered = host.strip().lower()
    if lowered in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        return lowered.endswith(".local")
    return ip.is_loopback or ip.is_private or ip.is_link_local


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
_role_binding_cooldowns: dict[tuple[str, str, str, str], float] = {}
_role_binding_health_lock = threading.RLock()


def _role_binding_health_key(
    role_id: str,
    provider_id: str,
    model: str,
    binding_id: str | None = None,
) -> tuple[str, str, str, str]:
    return (
        _normalize_runtime_role_id(role_id),
        str(provider_id or "").strip(),
        str(model or "").strip(),
        str(binding_id or "").strip(),
    )


def _role_binding_cooldown_seconds() -> float:
    raw = str(os.environ.get("KERNELONE_ROLE_BINDING_COOLDOWN_SECONDS", "") or "").strip()
    if not raw:
        return 120.0
    try:
        value = float(raw)
    except ValueError:
        return 120.0
    return max(1.0, min(3600.0, value))


def clear_role_binding_health() -> None:
    """Clear transient role-binding health state."""
    with _role_binding_health_lock:
        _role_binding_cooldowns.clear()


def mark_role_binding_unhealthy(
    role_id: str,
    *,
    provider_id: str,
    model: str,
    binding_id: str | None = None,
    cooldown_seconds: float | None = None,
) -> None:
    """Temporarily cool a retry-failing role binding.

    Fail-closed authorization remains unchanged; this only influences routing
    among already configured bindings for the same role.
    """
    provider = str(provider_id or "").strip()
    model_name = str(model or "").strip()
    if not provider or not model_name:
        return
    duration = _role_binding_cooldown_seconds() if cooldown_seconds is None else float(cooldown_seconds)
    until = time.monotonic() + max(1.0, min(3600.0, duration))
    with _role_binding_health_lock:
        _role_binding_cooldowns[_role_binding_health_key(role_id, provider, model_name, binding_id)] = until
        if binding_id:
            _role_binding_cooldowns[_role_binding_health_key(role_id, provider, model_name, None)] = until
    logger.warning(
        "[RuntimeConfig] cooled role binding: role=%s provider=%s model=%s binding_id=%s ttl=%.0fs",
        _normalize_runtime_role_id(role_id),
        provider,
        model_name,
        str(binding_id or ""),
        duration,
    )


def is_role_binding_healthy(
    role_id: str,
    *,
    provider_id: str,
    model: str,
    binding_id: str | None = None,
) -> bool:
    provider = str(provider_id or "").strip()
    model_name = str(model or "").strip()
    if not provider or not model_name:
        return True
    now = time.monotonic()
    keys = (
        _role_binding_health_key(role_id, provider, model_name, binding_id),
        _role_binding_health_key(role_id, provider, model_name, None),
    )
    with _role_binding_health_lock:
        for key in keys:
            until = _role_binding_cooldowns.get(key)
            if until is None:
                continue
            if until <= now:
                _role_binding_cooldowns.pop(key, None)
                continue
            return False
    return True


def _filter_healthy_slots(slots: tuple[RoleBindingSlot, ...]) -> tuple[RoleBindingSlot, ...]:
    healthy = tuple(
        slot
        for slot in slots
        if is_role_binding_healthy(
            slot.role_id,
            provider_id=slot.provider_id,
            model=slot.model,
            binding_id=slot.binding_id,
        )
    )
    return healthy or slots


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

    def _provider_entry(self, provider_id: str) -> dict[str, Any]:
        providers = self._load_config().get("providers")
        entry: Any = None
        if isinstance(providers, dict):
            entry = providers.get(provider_id)
        elif isinstance(providers, list):
            for item in providers:
                if isinstance(item, dict) and str(item.get("id") or item.get("provider_id") or "") == provider_id:
                    entry = item
                    break
        return dict(entry) if isinstance(entry, dict) else {}

    def _parse_role_bindings(
        self, normalized_role_id: str, role_cfg: dict[str, Any]
    ) -> tuple[ResolvedRoleBinding, ...]:
        raw_bindings = role_cfg.get("bindings")
        if not isinstance(raw_bindings, list):
            return ()

        bindings: list[ResolvedRoleBinding] = []
        role_profile = str(role_cfg.get("profile") or "").strip() or None
        for index, raw in enumerate(raw_bindings):
            if not isinstance(raw, dict):
                continue
            provider_id = str(raw.get("provider_id") or raw.get("providerId") or "").strip()
            model = str(raw.get("model") or "").strip()
            if not provider_id or not model:
                continue
            profile = str(raw.get("profile") or role_profile or "").strip() or None
            max_concurrency = _parse_optional_positive_int(raw.get("max_concurrency"))
            if max_concurrency is None:
                max_concurrency = _parse_optional_positive_int(raw.get("concurrency"))
            bindings.append(
                ResolvedRoleBinding(
                    role_id=normalized_role_id,
                    provider_id=provider_id,
                    model=model,
                    profile=profile,
                    max_concurrency=max_concurrency,
                    binding_id=f"{normalized_role_id}:{index}:{provider_id}:{model}",
                    binding_index=index,
                )
            )
        return tuple(bindings)

    def _role_config_from_assignments(self, normalized_role_id: str, assignments: list[Any]) -> RoleModelConfig | None:
        bindings: list[ResolvedRoleBinding] = []
        role_cap: int | None = None
        for index, assignment in enumerate(assignments):
            if not isinstance(assignment, dict):
                continue
            assignment_role = _normalize_runtime_role_id(str(assignment.get("roleId") or assignment.get("role") or ""))
            if assignment_role != normalized_role_id:
                continue
            provider_id = str(assignment.get("providerId") or assignment.get("provider_id") or "").strip()
            model = str(assignment.get("model") or "").strip()
            if not provider_id or not model:
                continue
            binding_cap = _parse_optional_positive_int(assignment.get("maxConcurrency"))
            if binding_cap is None:
                binding_cap = _parse_optional_positive_int(assignment.get("max_concurrency"))
            role_cap = role_cap or _parse_optional_positive_int(assignment.get("roleMaxConcurrency"))
            profile = str(assignment.get("profile") or "").strip() or None
            bindings.append(
                ResolvedRoleBinding(
                    role_id=normalized_role_id,
                    provider_id=provider_id,
                    model=model,
                    profile=profile,
                    max_concurrency=binding_cap,
                    binding_id=f"{normalized_role_id}:assignment:{index}:{provider_id}:{model}",
                    binding_index=index,
                )
            )
        if not bindings:
            return None
        primary = bindings[0]
        return RoleModelConfig(
            role_id=normalized_role_id,
            provider_id=primary.provider_id,
            model=primary.model,
            profile=primary.profile,
            provider_pool=tuple(dict.fromkeys(binding.provider_id for binding in bindings)),
            concurrency=max(1, role_cap or 1),
            bindings=tuple(bindings),
        )

    def get_provider_max_concurrency(self, provider_id: str) -> int:
        """Effective provider/deployment concurrency cap.

        Local/CLI providers default to 1 because most local installs are single
        worker by default; explicit ``max_concurrency`` always wins, including
        local deployments that are configured for parallel serving.
        """
        entry = self._provider_entry(provider_id)
        explicit = _parse_optional_positive_int(entry.get("max_concurrency"))
        if explicit is not None:
            return explicit

        provider_type = str(entry.get("type") or "").strip().lower()
        if provider_type in {"ollama", "codex_cli", "gemini_cli", "cli", "local", "local_http"}:
            return 1
        if _is_local_base_url(entry.get("base_url") or entry.get("endpoint") or entry.get("api_base")):
            return 1
        return 5

    def get_role_config(self, role_id: str) -> RoleModelConfig | None:
        config = self._load_config()
        normalized_role_id = _normalize_runtime_role_id(role_id)

        assignments = config.get("roleAssignments", [])
        if isinstance(assignments, list):
            assignment_config = self._role_config_from_assignments(normalized_role_id, assignments)
            if assignment_config is not None:
                return assignment_config

        roles = config.get("roles", {})
        if not isinstance(roles, dict):
            return None

        role_cfg = roles.get(normalized_role_id, {})
        if not role_cfg and normalized_role_id == "architect":
            role_cfg = roles.get("docs", {})
        if not isinstance(role_cfg, dict):
            return None

        bindings = self._parse_role_bindings(normalized_role_id, role_cfg)
        if bindings:
            provider_id = bindings[0].provider_id
            model = bindings[0].model
            profile = bindings[0].profile
        else:
            provider_id = str(role_cfg.get("provider_id") or "").strip()
            model = str(role_cfg.get("model") or "").strip()
            profile = str(role_cfg.get("profile") or "").strip() or None
        if not provider_id or not model:
            return None

        concurrency = _parse_optional_positive_int(role_cfg.get("max_concurrency"))
        if concurrency is None:
            concurrency = _parse_concurrency(role_cfg.get("concurrency"))

        return RoleModelConfig(
            role_id=normalized_role_id,
            provider_id=provider_id,
            model=model,
            profile=profile,
            provider_pool=_parse_provider_pool(role_cfg.get("provider_pool"), provider_id),
            concurrency=concurrency,
            bindings=bindings,
        )

    def _provider_model(self, provider_id: str) -> str:
        """Best-effort read of a provider's own configured model name.

        Used when a worker binds a specific backend in a heterogeneous pool whose
        endpoints serve distinctly-named models. Returns "" when absent.
        """
        try:
            entry = self._provider_entry(provider_id)
        except (RuntimeError, ValueError, OSError):
            return ""
        if isinstance(entry, dict):
            return str(entry.get("model") or "").strip()
        return ""

    def get_role_binding_slots(self, role_id: str) -> tuple[RoleBindingSlot, ...]:
        normalized_role_id = _normalize_runtime_role_id(role_id)
        resolved = self.get_role_config(normalized_role_id)
        if resolved is None:
            return ()

        if not resolved.bindings:
            pool = resolved.resolved_pool()
            if not pool:
                return ()
            legacy_slots: list[RoleBindingSlot] = []
            for index in range(resolved.concurrency):
                provider_id = pool[index % len(pool)]
                legacy_slots.append(
                    RoleBindingSlot(
                        role_id=normalized_role_id,
                        provider_id=provider_id,
                        model=self._provider_model(provider_id) or resolved.model,
                        profile=resolved.profile,
                        binding_id=f"{normalized_role_id}:legacy:{index}:{provider_id}",
                        binding_index=index % len(pool),
                        slot_index=index,
                        max_concurrency=resolved.concurrency,
                    )
                )
            return tuple(legacy_slots)

        role_remaining = max(1, resolved.concurrency)
        provider_remaining = {
            provider_id: self.get_provider_max_concurrency(provider_id) for provider_id in resolved.resolved_pool()
        }
        binding_slots: list[RoleBindingSlot] = []
        multiple_bindings = len(resolved.bindings) > 1
        for binding in resolved.bindings:
            if role_remaining <= 0:
                break
            provider_cap_remaining = provider_remaining.get(binding.provider_id, 0)
            if provider_cap_remaining <= 0:
                continue
            binding_cap = (
                binding.max_concurrency
                if binding.max_concurrency is not None
                else 1
                if multiple_bindings
                else self.get_provider_max_concurrency(binding.provider_id)
            )
            allocatable = min(role_remaining, provider_cap_remaining, max(1, binding_cap))
            for slot_index in range(allocatable):
                binding_slots.append(
                    RoleBindingSlot(
                        role_id=normalized_role_id,
                        provider_id=binding.provider_id,
                        model=binding.model,
                        profile=binding.profile,
                        binding_id=binding.binding_id,
                        binding_index=binding.binding_index,
                        slot_index=slot_index,
                        max_concurrency=allocatable,
                    )
                )
            provider_remaining[binding.provider_id] = provider_cap_remaining - allocatable
            role_remaining -= allocatable
        return tuple(binding_slots)

    def get_role_model(self, role_id: str) -> tuple[str, str]:
        normalized_role_id = _normalize_runtime_role_id(role_id)
        resolved = self.get_role_config(normalized_role_id)
        if resolved is not None:
            binding_override = _get_role_binding_override(normalized_role_id)
            if binding_override:
                override_pid = str(binding_override.get("provider_id") or "").strip()
                override_model = str(binding_override.get("model") or "").strip()
                override_bid = str(binding_override.get("binding_id") or "").strip()
                for binding in resolved.bindings:
                    same_provider_model = binding.provider_id == override_pid and binding.model == override_model
                    same_binding_id = bool(override_bid and binding.binding_id == override_bid)
                    if same_provider_model or same_binding_id:
                        logger.debug(
                            "[RuntimeConfig] %s: binding override %s/%s",
                            normalized_role_id,
                            override_pid,
                            override_model,
                        )
                        return override_pid, override_model
            # A worker thread bound to a specific backend overrides the provider
            # id (keeping the role's configured model). Only honoured when the
            # override names a provider actually in the role's pool, so a stale
            # override can never route to an unconfigured endpoint.
            provider_override_pid = _get_role_provider_override(normalized_role_id)
            if provider_override_pid and provider_override_pid in resolved.resolved_pool():
                # Heterogeneous pools: each backend may serve a DIFFERENTLY-NAMED
                # model (e.g. one endpoint serves 'qwen3.6-27b-gpu0', not the role's
                # 'int4'). Prefer the bound provider's own configured model so the
                # request's model name matches the endpoint (a mismatch is a hard
                # 404); fall back to the role model for homogeneous pools.
                provider_override_model = self._provider_model(provider_override_pid) or resolved.model
                logger.debug(
                    "[RuntimeConfig] %s: thread override %s/%s",
                    normalized_role_id,
                    provider_override_pid,
                    provider_override_model,
                )
                return provider_override_pid, provider_override_model
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
    clear_role_binding_health()


def get_role_model(role_id: str) -> tuple[str, str]:
    """Get role provider/model tuple using lazy runtime config manager."""
    manager = get_runtime_config_manager()
    provider_id, model = manager.get_role_model(role_id)
    if is_role_binding_healthy(role_id, provider_id=provider_id, model=model):
        return provider_id, model
    try:
        slots = manager.get_role_binding_slots(role_id)
    except (RuntimeError, ValueError, TypeError):
        return provider_id, model
    for slot in slots:
        if slot.provider_id == provider_id and slot.model == model:
            continue
        if is_role_binding_healthy(
            role_id,
            provider_id=slot.provider_id,
            model=slot.model,
            binding_id=slot.binding_id,
        ):
            logger.warning(
                "[RuntimeConfig] skipping cooled role binding: role=%s from=%s/%s to=%s/%s",
                _normalize_runtime_role_id(role_id),
                provider_id,
                model,
                slot.provider_id,
                slot.model,
            )
            return slot.provider_id, slot.model
    return provider_id, model


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


def get_provider_max_concurrency(provider_id: str) -> int:
    """Effective provider max concurrency from the runtime LLM config."""
    return get_runtime_config_manager().get_provider_max_concurrency(provider_id)


def get_role_binding_slots(role_id: str) -> tuple[RoleBindingSlot, ...]:
    """Concrete role binding slots after role/provider/binding caps are applied."""
    return _filter_healthy_slots(get_runtime_config_manager().get_role_binding_slots(role_id))


def resolve_role_worker_plan(role_id: str) -> list[RoleBindingSlot]:
    """Canonical per-role worker plan: ONE slot per worker, ``len(plan) == configured
    concurrency``, decoupled from provider count.

    This is the single source of truth for "how many market workers a role runs and
    which provider each binds to". A role may run N workers over ONE provider OR N
    providers — concurrency is read from config, never from the provider count.

    Built on :func:`get_role_binding_slots`, which already yields ``concurrency`` slots
    for the no-binding pool case (it round-robins the pool). The ONLY gap it repairs is
    the binding-path remainder drop: when distinct bindings each default to
    ``max_concurrency=1`` the slot count equals the binding count and any concurrency
    beyond it is silently lost (live: pm asks 3, gets 2). Here we round-robin EXTRA
    duplicate slots over the existing providers until the count reaches the configured
    concurrency, capping each provider at its ``max_concurrency`` so a single shared
    cloud endpoint is never oversubscribed. Never reduces, never exceeds concurrency.
    Pure + fail-open (returns the base slots / empty on error) so callers fall back to
    the single consumer unchanged.
    """
    try:
        slots = list(get_role_binding_slots(role_id))
        target = max(1, int(get_role_concurrency(role_id)))
    except (RuntimeError, ValueError, TypeError):
        return []
    if not slots or len(slots) >= target:
        return slots

    def _cap(provider_id: str) -> int:
        try:
            return max(1, int(get_provider_max_concurrency(provider_id)))
        except (RuntimeError, ValueError, TypeError):
            return 1

    base = list(slots)
    caps = {slot.provider_id: _cap(slot.provider_id) for slot in base}
    used: dict[str, int] = {}
    for slot in base:
        used[slot.provider_id] = used.get(slot.provider_id, 0) + 1
    idx = 0
    while len(slots) < target:
        if all(used.get(provider_id, 0) >= cap for provider_id, cap in caps.items()):
            break  # every provider saturated at its max_concurrency — do not oversubscribe
        src = base[idx % len(base)]
        idx += 1
        if used.get(src.provider_id, 0) >= caps[src.provider_id]:
            continue
        slots.append(replace(src, slot_index=len(slots), binding_id=f"{src.binding_id}#dup{len(slots)}"))
        used[src.provider_id] = used.get(src.provider_id, 0) + 1
    return slots


def role_worker_count(role_id: str) -> int:
    """Number of worker consumers a role's market pool should build (config-driven)."""
    return len(resolve_role_worker_plan(role_id))


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
    "ResolvedRoleBinding",
    "RoleBindingSlot",
    "RoleModelConfig",
    "RuntimeConfigManager",
    "clear_role_binding_health",
    "get_provider_base_url",
    "get_provider_max_concurrency",
    "get_role_binding_override",
    "get_role_binding_slots",
    "get_role_concurrency",
    "get_role_model",
    "get_role_provider_override",
    "get_role_provider_pool",
    "get_runtime_config_manager",
    "is_role_binding_healthy",
    "load_role_config",
    "mark_role_binding_unhealthy",
    "reset_runtime_config_manager",
    "set_default_model_resolver",
    "set_role_binding_override",
    "set_role_provider_override",
    "set_runtime_config_manager",
]
