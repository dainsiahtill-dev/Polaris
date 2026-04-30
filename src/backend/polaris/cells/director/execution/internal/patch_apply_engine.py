"""Unified patch/file operation parser and applier for Director responses.

.. deprecated::
    Implementation migrated to ``polaris.cells.director.tasking.internal.patch_apply_engine``
    (Phase 4, director.tasking sub-Cell).

    This module is kept as a backward-compatibility stub.
    Update imports to use ``polaris.cells.director.tasking.internal``.

# TODO: remove after 2026-06-30
"""

from __future__ import annotations

import warnings

# TODO: Cross-cell internal import — patch_apply_engine symbols are not
# yet exposed in director.tasking.public. Add to public contract when stabilised.
from polaris.cells.director.tasking.internal.patch_apply_engine import (
    ApplyIntegrity,
    ApplyResult,
    EditType,
    apply_all_operations,
    apply_operation,
    apply_operations_strict,
    parse_all_operations,
    parse_delete_operations,
    parse_full_file_blocks,
    parse_search_replace_blocks,
    validate_before_apply,
)

warnings.warn(
    "polaris.cells.director.execution.internal.patch_apply_engine is deprecated. "
    "Implementation migrated to polaris.cells.director.tasking.internal. "
    "Update imports accordingly.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "ApplyIntegrity",
    "ApplyResult",
    "EditType",
    "apply_all_operations",
    "apply_operation",
    "apply_operations_strict",
    "parse_all_operations",
    "parse_delete_operations",
    "parse_full_file_blocks",
    "parse_search_replace_blocks",
    "validate_before_apply",
]
