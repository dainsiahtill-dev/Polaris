"""Bootstrap template catalog for WorkerExecutor.

.. deprecated::
    Implementation migrated to ``polaris.cells.director.tasking.internal.bootstrap_template_catalog``
    (Phase 3, director.tasking sub-Cell).

    This module is kept as a backward-compatibility stub.
    Update imports to use ``polaris.cells.director.tasking.internal``.

# TODO: remove after 2026-06-30
"""

from __future__ import annotations

import warnings

# TODO: Cross-cell internal import — bootstrap_template_catalog symbols are not
# yet exposed in director.tasking.public. Add to public contract when stabilised.
from polaris.cells.director.tasking import (
    get_generic_bootstrap_files,
    get_intelligent_bootstrap_files,
    get_python_bootstrap_files,
    get_typescript_bootstrap_files,
)

warnings.warn(
    "polaris.cells.director.execution.internal.bootstrap_template_catalog is deprecated. "
    "Implementation migrated to polaris.cells.director.tasking.internal. "
    "Update imports accordingly.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "get_generic_bootstrap_files",
    "get_intelligent_bootstrap_files",
    "get_python_bootstrap_files",
    "get_typescript_bootstrap_files",
]
