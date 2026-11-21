"""Unified phase and action policy for the evidence-driven engineering loop."""

from __future__ import annotations

from enum import StrEnum


class EngineeringPhase(StrEnum):
    BUILD = "build"
    VERIFY = "verify"
    PREPARE = "prepare"
    REPAIR = "repair"
    QA_PROBE = "qa_probe"
    REPORT = "report"


_PHASE_TOOLS: dict[EngineeringPhase, frozenset[str]] = {
    EngineeringPhase.VERIFY: frozenset({"verify_project", "execute_command", "read_file", "browser_observe"}),
    EngineeringPhase.PREPARE: frozenset({"execute_command", "read_file"}),
    EngineeringPhase.REPORT: frozenset({"report_review"}),
}


def phase_for(
    role: str,
    *,
    verification: str,
    first_verification_due: bool,
    preparation_pending: bool,
    qa_report_required: bool,
) -> EngineeringPhase:
    """Derive the current phase from journal-backed facts, not model intent."""

    if role == "qa":
        return EngineeringPhase.REPORT if qa_report_required else EngineeringPhase.QA_PROBE
    if first_verification_due:
        return EngineeringPhase.VERIFY
    if preparation_pending:
        return EngineeringPhase.PREPARE
    if verification == "failed":
        return EngineeringPhase.REPAIR
    return EngineeringPhase.BUILD


def allowed_tools(phase: EngineeringPhase) -> frozenset[str] | None:
    """None means no phase restriction; restrictions are always narrowing."""

    return _PHASE_TOOLS.get(phase)
