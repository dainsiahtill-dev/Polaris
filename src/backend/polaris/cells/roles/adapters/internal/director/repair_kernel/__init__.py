"""Compatibility shim for Director Repair Kernel imports.

The canonical implementation lives in
``polaris.cells.director.runtime.internal.repair_kernel``. This package exists
only so migration-era roles.adapters imports remain stable.
"""

from __future__ import annotations

from polaris.cells.director.runtime.internal.repair_kernel import *  # noqa: F403
