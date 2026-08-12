"""Package-namespace attribute lookup for lossless monkeypatch surface.

Call sites that must honor ``monkeypatch.setattr(quality_gate, name, ...)``
resolve names through the package module rather than submodule globals.
"""

from __future__ import annotations

import sys
from typing import Any


def package_attr(name: str) -> Any:
    """Return attribute ``name`` from the ``quality_gate`` package module."""

    pkg = sys.modules.get(__package__)
    if pkg is None:
        raise RuntimeError("quality_gate_package_not_loaded")
    return getattr(pkg, name)


__all__ = ["package_attr"]
