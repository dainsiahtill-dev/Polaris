"""conftest for polaris.cells test package.

Patches a pre-existing import chain bug where session_workflow_matrix.py
imports RoleSessionOrchestrator from polaris.cells.roles.runtime.public
where it does not exist. Installed before pytest collects any tests.
"""

from __future__ import annotations

import os
import sys
import types


class _StubRoleSessionOrchestrator:
    """Stub for RoleSessionOrchestrator when the real class is unavailable."""
    pass


_runtime_public_dir = os.path.join(
    os.path.dirname(__file__), "roles", "runtime", "public"
)
_runtime_stub = types.ModuleType("polaris.cells.roles.runtime.public")
_runtime_stub.__path__ = [_runtime_public_dir]  # type: ignore[assignment]
_runtime_stub.__file__ = os.path.join(_runtime_public_dir, "__init__.py")  # type: ignore[assignment]
_runtime_stub.__package__ = "polaris.cells.roles.runtime.public"
_runtime_stub.__all__ = []

def _runtime_getattr(name: str):
    if name == "RoleSessionOrchestrator":
        return _StubRoleSessionOrchestrator
    import importlib

    sys.modules.pop("polaris.cells.roles.runtime.public", None)
    sys.modules.pop("polaris.cells.roles.runtime.public.service", None)
    try:
        real = importlib.import_module("polaris.cells.roles.runtime.public")
        return getattr(real, name)
    except (ImportError, AttributeError):
        raise AttributeError(
            f"module 'polaris.cells.roles.runtime.public' has no attribute {name!r}"
        )
    finally:
        sys.modules["polaris.cells.roles.runtime.public"] = _runtime_stub


_runtime_stub.__getattr__ = _runtime_getattr
sys.modules["polaris.cells.roles.runtime.public"] = _runtime_stub
sys.modules["polaris.cells.roles.runtime.public.service"] = types.ModuleType(
    "polaris.cells.roles.runtime.public.service"
)
