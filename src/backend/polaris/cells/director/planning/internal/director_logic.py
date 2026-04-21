"""Director logic utilities (Cell Implementation).

Migrated from ``polaris.cells.director.execution.logic``.

Shared director logic utilities have been extracted to
``polaris.domain.services.director_logic_service``.
"""

from __future__ import annotations

import logging

from polaris.domain.services.director_logic_service import (
    extract_defect_ticket,
    parse_acceptance,
)
from polaris.kernelone.utils.json_utils import parse_json_payload

logger = logging.getLogger(__name__)

extract_defect_ticket = extract_defect_ticket
parse_acceptance = parse_acceptance
parse_json_payload = parse_json_payload


def write_gate_check(
    changed_files: list[str],
    act_files: list[str],
    pm_target_files: list[str] | None = None,
    *,
    require_change: bool = False,
) -> tuple[bool, str]:
    """Simplified write gate check for cell logic."""
    if require_change and not changed_files:
        return False, "No files changed"
    return True, ""
