"""Director logic rules (Cell Implementation).

.. deprecated::
    Implementation migrated to ``polaris.cells.director.planning.internal.director_logic_rules``
    (Phase 4, director.planning sub-Cell).

    This module is kept as a backward-compatibility stub.
    Update imports to use ``polaris.cells.director.planning.internal``.

# TODO: remove after 2026-06-30
"""

from __future__ import annotations

import warnings

# TODO: Cross-cell internal import — director_logic_rules symbols are not
# yet exposed in director.planning.public. Add to public contract when stabilised.
from polaris.cells.director.planning.public import (
    compact_pm_payload,
    extract_defect_ticket,
    extract_required_evidence,
    parse_acceptance,
    validate_defect_ticket,
    validate_files_to_edit,
    write_gate_check,
)
from polaris.kernelone.utils.json_utils import parse_json_payload

warnings.warn(
    "polaris.cells.director.execution.internal.director_logic_rules is deprecated. "
    "Implementation migrated to polaris.cells.director.planning.internal. "
    "Update imports accordingly.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "compact_pm_payload",
    "extract_defect_ticket",
    "extract_required_evidence",
    "parse_acceptance",
    "parse_json_payload",
    "validate_defect_ticket",
    "validate_files_to_edit",
    "write_gate_check",
]
