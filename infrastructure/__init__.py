"""Infrastructure package for Polaris tools and core functionality.

This top-level package also exposes backend infrastructure modules so imports
like ``infrastructure.persistence`` remain stable when running from repo root.
"""

from __future__ import annotations

import os
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_backend_infra = os.path.join(_repo_root, "src", "backend", "infrastructure")
if os.path.isdir(_backend_infra) and _backend_infra not in __path__:
    __path__.append(_backend_infra)

__all__ = []
