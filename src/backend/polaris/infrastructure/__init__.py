"""Infrastructure layer for Polaris."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["audit", "db", "llm", "storage"]


def __getattr__(name: str) -> Any:
    if name == "audit":
        return import_module("polaris.infrastructure.audit")
    if name == "db":
        return import_module("polaris.infrastructure.db")
    if name == "llm":
        return import_module("polaris.infrastructure.llm")
    if name == "storage":
        return import_module("polaris.infrastructure.storage")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
