"""Internal surface for director.planning cell.

Migrated implementation modules:
- director_agent: DirectorAgent, ExecutionRecord, RiskRegistry, QualityTracker
- director_logic_rules: parse_json_payload, parse_acceptance, extract_defect_ticket,
  validate_defect_ticket, compact_pm_payload, validate_files_to_edit,
  write_gate_check, extract_required_evidence
- director_logic: parse_json_payload, parse_acceptance, extract_defect_ticket,
  write_gate_check (legacy aliases from cell root logic.py)
- context_gatherer: GatheredContext, gather
"""

from __future__ import annotations

from polaris.cells.director.planning.internal.context_gatherer import (
    GatheredContext,
    gather,
)
from polaris.cells.director.planning.internal.director_agent import (
    DirectorAgent,
    ExecutionRecord,
    QualityTracker,
    RiskRegistry,
)
from polaris.cells.director.planning.internal.director_logic import (
    extract_defect_ticket as _dl_extract_defect_ticket,
    parse_acceptance as _dl_parse_acceptance,
    parse_json_payload as _dl_parse_json_payload,
    write_gate_check as _dl_write_gate_check,
)
from polaris.cells.director.planning.internal.director_logic_rules import (
    DEFAULT_DEFECT_TICKET_FIELDS,
    compact_pm_payload,
    extract_defect_ticket,
    extract_required_evidence,
    parse_acceptance,
    parse_json_payload,
    validate_defect_ticket,
    validate_files_to_edit,
    write_gate_check,
)

# ---------------------------------------------------------------------------
# Aliases from director_logic (legacy cell-root utilities)
# ---------------------------------------------------------------------------
# Re-export the same symbols under both names so that existing call sites
# that import from director.planning.internal.logic (the old path) still work.
parse_json_payload = parse_json_payload
parse_acceptance = parse_acceptance
extract_defect_ticket = extract_defect_ticket
write_gate_check = write_gate_check

__all__ = [
    # Rules (canonical)
    "DEFAULT_DEFECT_TICKET_FIELDS",
    # Agent
    "DirectorAgent",
    "ExecutionRecord",
    # Context
    "GatheredContext",
    "QualityTracker",
    "RiskRegistry",
    # Legacy aliases from director_logic
    "_dl_extract_defect_ticket",
    "_dl_parse_acceptance",
    "_dl_parse_json_payload",
    "_dl_write_gate_check",
    "compact_pm_payload",
    "extract_defect_ticket",
    "extract_required_evidence",
    "gather",
    "parse_acceptance",
    "parse_json_payload",
    "validate_defect_ticket",
    "validate_files_to_edit",
    "write_gate_check",
]
