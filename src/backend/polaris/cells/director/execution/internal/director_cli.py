"""Director CLI - Command line interface for Director.

.. deprecated::
    Implementation migrated to ``polaris.cells.director.tasking.internal.director_cli``
    (Phase 4, director.tasking sub-Cell).

    This module is kept as a backward-compatibility stub.
    Update imports to use ``polaris.cells.director.tasking.internal``.
"""

from __future__ import annotations

import warnings

# Import from tasking - DirectorCLI may not exist yet, handle gracefully
try:
    from polaris.cells.director.tasking.internal.director_cli import (
        DirectorCLI,
    )

    __all__ = ["DirectorCLI"]
except ImportError:
    # DirectorCLI not yet migrated, keep original implementation
    DirectorCLI = None
    __all__ = []

warnings.warn(
    "polaris.cells.director.execution.internal.director_cli is deprecated. "
    "Implementation migrated to polaris.cells.director.tasking.internal. "
    "Update imports accordingly.",
    DeprecationWarning,
    stacklevel=2,
)
