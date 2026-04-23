"""Runtime feature switches for Context OS and Cognitive Runtime.

This module centralizes toggle parsing so callers across cells can apply
the same precedence and defaults.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Canonical env var names (KERNELONE_* primary, KERNELONE_* fallback handled by resolver)
CONTEXT_OS_ENABLED_ENV = "KERNELONE_CONTEXT_OS_ENABLED"
COGNITIVE_RUNTIME_MODE_ENV = "KERNELONE_COGNITIVE_RUNTIME_MODE"
# KERNELONE_* fallbacks
CONTEXT_OS_ENABLED_ENV_FALLBACK = "KERNELONE_CONTEXT_OS_ENABLED"
COGNITIVE_RUNTIME_MODE_ENV_FALLBACK = "KERNELONE_COGNITIVE_RUNTIME_MODE"

_TRUE_TOKENS = {"1", "true", "yes", "on", "enabled"}
_FALSE_TOKENS = {"0", "false", "no", "off", "disabled"}


class CognitiveRuntimeMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    MAINLINE = "mainline"


def _coerce_bool(
    value: Any,
    default: bool | None = None,
    *,
    env_var_name: str | None = None,
) -> bool | None:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if not token:
        return default
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    if env_var_name is not None:
        logger.warning(
            "Invalid boolean value %r for env var %s; falling back to default %r.",
            value,
            env_var_name,
            default,
        )
    return default


def _coerce_mode(
    value: Any,
    default: CognitiveRuntimeMode,
    *,
    env_var_name: str | None = None,
) -> CognitiveRuntimeMode:
    token = str(value or "").strip().lower()
    if not token:
        return default
    for item in CognitiveRuntimeMode:
        if item.value == token:
            return item
    if env_var_name is not None:
        logger.warning(
            "Invalid CognitiveRuntimeMode value %r for env var %s; falling back to %r.",
            value,
            env_var_name,
            default,
        )
    return default


def _iter_mappings(
    first: Mapping[str, Any] | None,
    second: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    items: list[Mapping[str, Any]] = []
    if isinstance(first, Mapping):
        items.append(first)
    if isinstance(second, Mapping):
        items.append(second)
    return tuple(items)


def resolve_context_os_enabled(
    *,
    incoming_context: Mapping[str, Any] | None = None,
    session_context_config: Mapping[str, Any] | None = None,
    default: bool = True,
) -> bool:
    """Resolve Context OS enable state with deterministic precedence.

    Priority:
    1. incoming/session payload explicit flags
    2. environment variable
    3. default argument (True)
    """

    for payload in _iter_mappings(incoming_context, session_context_config):
        direct = _coerce_bool(
            payload.get("state_first_context_os_enabled", payload.get("context_os_enabled")),
            default=None,
        )
        if direct is not None:
            return direct

        strategy_override = payload.get("strategy_override")
        if isinstance(strategy_override, Mapping):
            continuity = strategy_override.get("session_continuity")
            if isinstance(continuity, Mapping):
                nested = _coerce_bool(
                    continuity.get(
                        "state_first_context_os_enabled",
                        continuity.get("context_os_enabled"),
                    ),
                    default=None,
                )
                if nested is not None:
                    return nested

        state_first_payload = payload.get("state_first_context_os")
        if isinstance(state_first_payload, Mapping):
            nested = _coerce_bool(state_first_payload.get("enabled"), default=None)
            if nested is not None:
                return nested

    env_value = _coerce_bool(
        os.getenv(CONTEXT_OS_ENABLED_ENV, ""),
        default=None,
        env_var_name=CONTEXT_OS_ENABLED_ENV,
    )
    if env_value is not None:
        return env_value
    # KERNELONE_* fallback
    env_value = _coerce_bool(
        os.getenv(CONTEXT_OS_ENABLED_ENV_FALLBACK, ""),
        default=None,
        env_var_name=CONTEXT_OS_ENABLED_ENV_FALLBACK,
    )
    if env_value is not None:
        return env_value
    return bool(default)


def resolve_cognitive_runtime_mode(
    *,
    context: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    default: CognitiveRuntimeMode = CognitiveRuntimeMode.SHADOW,
) -> CognitiveRuntimeMode:
    """Resolve Cognitive Runtime mode.

    Priority:
    1. context/metadata explicit mode
    2. context/metadata boolean enabled flag
    3. environment variable (KERNELONE_COGNITIVE_RUNTIME_MODE or KERNELONE_COGNITIVE_RUNTIME_MODE)
    4. default mode (shadow)

    Mode behaviors:
    - OFF: Cognitive Runtime is disabled. No runtime enhancement is applied.
    - SHADOW: Cognitive Runtime runs in shadow mode. It observes and logs runtime
      decisions but does not influence outcomes. Useful for benchmarking and
      validation before enabling full runtime control.
    - MAINLINE: Cognitive Runtime is fully active. It influences runtime decisions,
      applies optimizations, and drives the execution strategy.
    """

    for payload in _iter_mappings(context, metadata):
        mode = _coerce_mode(payload.get("cognitive_runtime_mode"), default=default)
        if payload.get("cognitive_runtime_mode") is not None:
            return mode

        enabled = _coerce_bool(payload.get("cognitive_runtime_enabled"), default=None)
        if enabled is not None:
            return CognitiveRuntimeMode.SHADOW if enabled else CognitiveRuntimeMode.OFF

    env_mode = os.getenv(COGNITIVE_RUNTIME_MODE_ENV, "")
    if str(env_mode or "").strip():
        return _coerce_mode(env_mode, default=default, env_var_name=COGNITIVE_RUNTIME_MODE_ENV)
    # KERNELONE_* fallback
    env_mode = os.getenv(COGNITIVE_RUNTIME_MODE_ENV_FALLBACK, "")
    if str(env_mode or "").strip():
        return _coerce_mode(env_mode, default=default, env_var_name=COGNITIVE_RUNTIME_MODE_ENV_FALLBACK)

    return default


def cognitive_runtime_is_enabled(mode: CognitiveRuntimeMode | str) -> bool:
    token = mode if isinstance(mode, CognitiveRuntimeMode) else _coerce_mode(mode, default=CognitiveRuntimeMode.OFF)
    return token is not CognitiveRuntimeMode.OFF


__all__ = [
    "COGNITIVE_RUNTIME_MODE_ENV",
    "COGNITIVE_RUNTIME_MODE_ENV_FALLBACK",
    "CONTEXT_OS_ENABLED_ENV",
    "CONTEXT_OS_ENABLED_ENV_FALLBACK",
    "CognitiveRuntimeMode",
    "cognitive_runtime_is_enabled",
    "resolve_cognitive_runtime_mode",
    "resolve_context_os_enabled",
]
