"""Internal surface for director.planning cell.

Migrated implementation modules:
- director_agent: DirectorAgent, ExecutionRecord, RiskRegistry, QualityTracker
- director_logic_rules: parse_json_payload, parse_acceptance, extract_defect_ticket,
  validate_defect_ticket, compact_pm_payload, validate_files_to_edit,
  write_gate_check, extract_required_evidence
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
