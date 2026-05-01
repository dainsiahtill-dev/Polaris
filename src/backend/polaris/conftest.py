"""Root conftest for polaris package tests.

Patches a pre-existing import chain bug where session_workflow_matrix.py
imports RoleSessionOrchestrator from polaris.cells.roles.runtime.public
where it does not exist. Installed before pytest collects any tests.
"""

from __future__ import annotations

import os
import sys
import types


# Stub RoleSessionOrchestrator class (does not exist in the real module)
class _StubRoleSessionOrchestrator:
    """Stub for RoleSessionOrchestrator when the real class is unavailable."""
    pass


# Create stub package for polaris.cells.roles.runtime.public
_runtime_public_dir = os.path.join(
    os.path.dirname(__file__), "cells", "roles", "runtime", "public"
)
_runtime_stub = types.ModuleType("polaris.cells.roles.runtime.public")
_runtime_stub.__path__ = [_runtime_public_dir]  # type: ignore[assignment]
_runtime_stub.__file__ = os.path.join(_runtime_public_dir, "__init__.py")  # type: ignore[assignment]
_runtime_stub.__package__ = "polaris.cells.roles.runtime.public"
_runtime_stub.__all__ = []

# Provide RoleSessionOrchestrator stub via __getattr__
def _runtime_getattr(name: str):
    if name == "RoleSessionOrchestrator":
        return _StubRoleSessionOrchestrator
    # For all other names, try to load from real module
    import importlib

    # Temporarily remove stub to allow real import chain
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
        # Restore stub
        sys.modules["polaris.cells.roles.runtime.public"] = _runtime_stub


_runtime_stub.__getattr__ = _runtime_getattr

# Pre-populate sys.modules with stub + needed submodules
sys.modules["polaris.cells.roles.runtime.public"] = _runtime_stub
sys.modules["polaris.cells.roles.runtime.public.service"] = types.ModuleType(
    "polaris.cells.roles.runtime.public.service"
)


