"""Polaris HTTP delivery package.

NOTE: This package name ``http`` shadows the stdlib ``http`` module.
Eager imports here cause a circular import when third-party libs
(starlette/fastapi) do ``from http import cookies``.  Use lazy
imports via ``__getattr__`` to break the cycle.
"""

from __future__ import annotations

from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "audit_router": ("polaris.delivery.http.audit_router", "router"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        module_path, attr = _LAZY_EXPORTS[name]
        import importlib

        mod = importlib.import_module(module_path)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_LAZY_EXPORTS)
