"""Compatibility facade for anthropomorphic reflection module.

Canonical implementation is hosted in ``polaris.kernelone.memory.reflection``.
This facade keeps historical imports stable during migration.
"""

from __future__ import annotations

from polaris.kernelone.memory.reflection import (
    FALLBACK_REFLECTION_TEMPLATE,
    ReflectionGenerator,
    ReflectionScheduler,
    ReflectionStore,
    parse_json_garbage,
)

__all__ = [
    "FALLBACK_REFLECTION_TEMPLATE",
    "ReflectionGenerator",
    "ReflectionScheduler",
    "ReflectionStore",
    "parse_json_garbage",
]
