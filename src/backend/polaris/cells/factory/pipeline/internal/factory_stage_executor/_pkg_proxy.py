"""Late-bound package namespace for monkeypatch-compatible symbol lookup.

Characterization tests and unit tests patch attributes on the package module
(``factory_stage_executor.RoleRuntimeService``, ``.subprocess``, …). Submodule
method bodies must resolve those names through this proxy so patches take effect.
"""

from __future__ import annotations

import sys
from types import ModuleType

_PKG = "polaris.cells.factory.pipeline.internal.factory_stage_executor"


def pkg() -> ModuleType:
    """Return the live package module (monkeypatch-aware)."""

    return sys.modules[_PKG]
