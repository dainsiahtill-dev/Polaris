"""Configuration cascade stack for layered config resolution.

Provides a ConfigStack that resolves configuration values through
a priority-ordered chain: env vars > explicit overrides > TransactionConfig defaults.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig

# ---------------------------------------------------------------------------
# Env var -> TransactionConfig field mapping
# ---------------------------------------------------------------------------

_ENV_VAR_MAP: dict[str, str] = {
    "KERNELONE_MUTATION_GUARD_MODE": "mutation_guard_mode",
    "KERNELONE_MAX_TOOL_EXECUTION_TIME_MS": "max_tool_execution_time_ms",
    "KERNELONE_MAX_RETRY_ATTEMPTS": "max_retry_attempts",
    "KERNELONE_SLM_ENABLED": "slm_enabled",
    "KERNELONE_SLM_MODEL_NAME": "slm_model_name",
    "KERNELONE_SLM_PROVIDER": "slm_provider",
    "KERNELONE_SLM_BASE_URL": "slm_base_url",
    "KERNELONE_EFFECT_POLICY_MODE": "effect_policy_mode",
    "KERNELONE_ENABLE_MODIFICATION_CONTRACT": "enable_modification_contract",
}

# Reverse lookup: field name -> env var name
_FIELD_TO_ENV: dict[str, str] = {v: k for k, v in _ENV_VAR_MAP.items()}

# Fields whose target type is bool
_BOOL_FIELDS: frozenset[str] = frozenset(
    {
        "slm_enabled",
        "enable_modification_contract",
        "enable_streaming",
        "llm_once_forces_tool_choice_none",
        "intent_embedding_enabled",
    }
)

# Fields whose target type is int
_INT_FIELDS: frozenset[str] = frozenset(
    {
        "max_tool_execution_time_ms",
        "max_retry_attempts",
        "max_per_tool_result_chars",
        "max_total_result_chars",
        "handoff_threshold_tools",
        "slm_timeout",
    }
)

# Fields whose target type is float
_FLOAT_FIELDS: frozenset[str] = frozenset(
    {
        "inline_patch_escape_threshold",
        "intent_embedding_threshold",
    }
)

_ENV_PREFIX: str = "KERNELONE_"

# ---------------------------------------------------------------------------
# Type coercion helpers
# ---------------------------------------------------------------------------

_TRUTHY: frozenset[str] = frozenset({"true", "1", "yes", "on"})
_FALSY: frozenset[str] = frozenset({"false", "0", "no", "off"})


def _coerce_bool(raw: str) -> bool:
    """Coerce a string to bool.

    Raises ``ValueError`` if the string is not a recognised boolean literal.
    """
    lowered = raw.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    raise ValueError(f"Cannot coerce {raw!r} to bool")


def _coerce_int(raw: str) -> int:
    """Coerce a string to int."""
    return int(raw.strip())


def _coerce_float(raw: str) -> float:
    """Coerce a string to float."""
    return float(raw.strip())


def _coerce_value(field_name: str, raw: str) -> Any:
    """Coerce *raw* to the expected type for *field_name*."""
    if field_name in _BOOL_FIELDS:
        return _coerce_bool(raw)
    if field_name in _INT_FIELDS:
        return _coerce_int(raw)
    if field_name in _FLOAT_FIELDS:
        return _coerce_float(raw)
    return raw


# ---------------------------------------------------------------------------
# ConfigLayer / ConfigStack
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigLayer:
    """A single layer in the config stack."""

    name: str
    values: Mapping[str, Any]
    priority: int  # Higher = takes precedence


class ConfigStack:
    """Layered configuration resolution with env var integration.

    Resolution order (highest to lowest priority):
      1. Environment variables (``KERNELONE_*`` prefix)
      2. Runtime overrides (set programmatically via :meth:`push_layer`)
      3. ``TransactionConfig`` defaults (base layer, priority 0)

    Thread-safety: this class is **not** thread-safe.  Create one instance
    per logical scope (e.g. per turn / per session).
    """

    _ENV_PRIORITY: int = 100  # reserved ceiling for env vars
    _BASE_PRIORITY: int = 0

    def __init__(self, base: TransactionConfig | None = None) -> None:
        self._layers: list[ConfigLayer] = []
        self._base: TransactionConfig = base or TransactionConfig()
        self._base_fields: dict[str, Any] = {}
        self._register_base_layer()

    # -- internal helpers ---------------------------------------------------

    def _register_base_layer(self) -> None:
        """Register ``TransactionConfig`` fields as the base layer."""
        self._base_fields = {f.name: getattr(self._base, f.name) for f in dataclasses.fields(self._base)}
        self._layers.append(
            ConfigLayer(
                name="base:TransactionConfig",
                values=self._base_fields,
                priority=self._BASE_PRIORITY,
            )
        )

    def _resolve_env(self, field_name: str) -> tuple[bool, Any]:
        """Try to resolve *field_name* from the environment.

        Returns ``(found, value)``; value is already type-coerced.
        """
        env_key = _FIELD_TO_ENV.get(field_name)
        if env_key is None:
            # Try generic prefix lookup: KERNELONE_<FIELD_UPPER>
            env_key = f"{_ENV_PREFIX}{field_name.upper()}"
        raw = os.environ.get(env_key)
        if raw is None:
            return False, None
        return True, _coerce_value(field_name, raw)

    def _layers_descending(self) -> list[ConfigLayer]:
        """Return layers sorted by descending priority (highest first)."""
        return sorted(self._layers, key=lambda layer: layer.priority, reverse=True)

    # -- public API ---------------------------------------------------------

    def push_layer(
        self,
        name: str,
        values: Mapping[str, Any],
        priority: int = 50,
    ) -> None:
        """Push a new config layer.  Higher priority overrides lower.

        Args:
            name: Human-readable layer label (for debugging / snapshots).
            values: Key-value pairs provided by this layer.
            priority: Override priority.  Must be in ``[1, 99]`` (0 is reserved
                for the base layer, 100 for env vars).

        Raises:
            ValueError: If *priority* collides with reserved bands.
        """
        if priority <= self._BASE_PRIORITY:
            raise ValueError(f"priority must be > {self._BASE_PRIORITY} (reserved for base layer), got {priority}")
        if priority >= self._ENV_PRIORITY:
            raise ValueError(f"priority must be < {self._ENV_PRIORITY} (reserved for env vars), got {priority}")
        self._layers.append(ConfigLayer(name=name, values=dict(values), priority=priority))

    def get(self, key: str, default: Any = None) -> Any:
        """Resolve a config value through the stack.

        Resolution order:
          1. Environment variables (``KERNELONE_{KEY_UPPER}``)
          2. Layers by descending priority
          3. Fall back to *default*
        """
        # 1. env vars (highest priority)
        found, value = self._resolve_env(key)
        if found:
            return value

        # 2. walk layers by descending priority
        for layer in self._layers_descending():
            if key in layer.values:
                return layer.values[key]

        # 3. explicit default
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        """Resolve and cast to ``int``."""
        raw = self.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Resolve and cast to ``float``."""
        raw = self.get(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Resolve and cast to ``bool``.

        Recognises ``true/false/1/0/yes/no/on/off`` (case-insensitive)
        when the resolved value is a string.
        """
        raw = self.get(key)
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            try:
                return _coerce_bool(raw)
            except ValueError:
                return default
        return bool(raw)

    def get_str(self, key: str, default: str = "") -> str:
        """Resolve and cast to ``str``."""
        raw = self.get(key)
        if raw is None:
            return default
        return str(raw)

    def snapshot(self) -> dict[str, Any]:
        """Return a fully-resolved snapshot of all known keys.

        Useful for debugging and audit logging.  The snapshot merges every
        layer (lowest priority first, so higher layers overwrite) and finally
        applies env var overrides on top.
        """
        merged: dict[str, Any] = {}

        # Walk layers from lowest to highest priority
        for layer in sorted(self._layers, key=lambda la: la.priority):
            merged.update(layer.values)

        # Apply env overrides last
        for env_key, field_name in _ENV_VAR_MAP.items():
            raw = os.environ.get(env_key)
            if raw is not None:
                merged[field_name] = _coerce_value(field_name, raw)

        return merged

    # -- factory classmethod ------------------------------------------------

    @classmethod
    def from_env(cls, base: TransactionConfig | None = None) -> ConfigStack:
        """Build a ``ConfigStack`` pre-loaded with env var integration.

        This is the canonical entry point for production use.  Env vars are
        not baked into a layer; they are resolved dynamically on every
        :meth:`get` call so that hot-reloads of env vars are picked up
        without reconstructing the stack.
        """
        return cls(base=base)

    # -- dunder helpers -----------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        layer_names = [f"{la.name}(p={la.priority})" for la in self._layers_descending()]
        return f"ConfigStack(layers={layer_names})"


__all__ = [
    "ConfigLayer",
    "ConfigStack",
]
