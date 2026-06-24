"""Strict, fail-closed environment feature-flag resolution."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class EnvFlagDecision:
    """Auditable result of resolving one feature flag across aliases."""

    enabled: bool
    configured_names: tuple[str, ...]
    reason: str


def resolve_env_flag(
    names: Sequence[str],
    *,
    default: bool = False,
    environ: Mapping[str, str] | None = None,
) -> EnvFlagDecision:
    """Resolve aliases without allowing malformed or conflicting values.

    Unset flags use ``default``. Any malformed value or disagreement between
    aliases disables the feature so privileged/internal surfaces fail closed.
    Raw values are intentionally excluded from the decision to avoid leaking
    configuration secrets into audit payloads.
    """

    normalized_names = tuple(str(name).strip() for name in names if str(name).strip())
    if not normalized_names:
        raise ValueError("at least one environment variable name is required")

    source: Mapping[str, str] = os.environ if environ is None else environ
    configured = tuple((name, source[name]) for name in normalized_names if name in source)
    configured_names = tuple(name for name, _raw in configured)
    if not configured:
        return EnvFlagDecision(
            enabled=bool(default),
            configured_names=(),
            reason="default_true" if default else "default_false",
        )

    parsed_values: list[bool] = []
    for _name, raw in configured:
        token = str(raw).strip().lower()
        if token in _TRUE_VALUES:
            parsed_values.append(True)
            continue
        if token in _FALSE_VALUES:
            parsed_values.append(False)
            continue
        return EnvFlagDecision(
            enabled=False,
            configured_names=configured_names,
            reason="invalid_value",
        )

    if len(set(parsed_values)) != 1:
        return EnvFlagDecision(
            enabled=False,
            configured_names=configured_names,
            reason="conflicting_aliases",
        )

    enabled = parsed_values[0]
    return EnvFlagDecision(
        enabled=enabled,
        configured_names=configured_names,
        reason="explicit_true" if enabled else "explicit_false",
    )
