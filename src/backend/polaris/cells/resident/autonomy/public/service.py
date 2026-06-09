"""Public service exports for `resident.autonomy` cell."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from polaris.cells.audit.evidence.public.service import (
    EvidenceBundleService,
    create_evidence_bundle_service,
)
from polaris.cells.resident.autonomy.internal.capability_graph import CapabilityGraph
from polaris.cells.resident.autonomy.internal.counterfactual_lab import CounterfactualLab
from polaris.cells.resident.autonomy.internal.decision_trace import DecisionTraceRecorder
from polaris.cells.resident.autonomy.internal.execution_projection import (
    ExecutionProjectionService,
    get_execution_projection_service,
)
from polaris.cells.resident.autonomy.internal.goal_governor import GoalGovernor
from polaris.cells.resident.autonomy.internal.meta_cognition import StrategyInsightEngine
from polaris.cells.resident.autonomy.internal.pm_bridge import ResidentPMBridge
from polaris.cells.resident.autonomy.internal.resident_runtime_service import (
    ResidentService,
    get_resident_service,
    record_resident_decision,
    reset_resident_services,
)
from polaris.cells.resident.autonomy.internal.resident_storage import ResidentPaths, ResidentStorage
from polaris.cells.resident.autonomy.internal.self_improvement_lab import SelfImprovementLab
from polaris.cells.resident.autonomy.internal.skill_foundry import SkillFoundry
from polaris.cells.resident.autonomy.public.contracts import (
    QueryResidentStatusV1,
    RecordResidentEvidenceCommandV1,
    ResidentAutonomyError,
    ResidentAutonomyResultV1,
    ResidentCycleCompletedEventV1,
    RunResidentCycleCommandV1,
)
from polaris.domain.entities.evidence_bundle import (
    EvidenceBundle,
    FileChange,
    PerfEvidence,
    SourceType,
    StaticAnalysisEvidence,
    TestRunEvidence,
)
from polaris.domain.models.resident import (
    DecisionRecord,
    GoalProposal,
    ResidentAgenda,
    ResidentIdentity,
    ResidentMode,
    ResidentRuntimeState,
    SkillProposal,
    SkillProposalStatus,
    utc_now_iso,
)

logger = logging.getLogger(__name__)


def get_evidence_service() -> EvidenceBundleService:
    """Return the canonical evidence bundle service."""
    return create_evidence_bundle_service()


# ---------------------------------------------------------------------------
# Public contract handlers
#
# Map the declared `resident.autonomy` CQRS contracts onto the runtime service
# so the public command/query/event surface is actually exercised (not inert).
# ---------------------------------------------------------------------------

# Labs advanced by a single autonomy tick when the resident is active.
_CYCLE_ACTIONS: tuple[str, ...] = (
    "meta_cognition",
    "skill_foundry",
    "capability_graph",
    "counterfactual_lab",
    "self_improvement_lab",
    "goal_generation",
)


def query_resident_status(query: QueryResidentStatusV1, *, include_details: bool = False) -> dict[str, Any]:
    """Handle :class:`QueryResidentStatusV1` → resident status snapshot."""
    return get_resident_service(query.workspace).get_status(include_details=include_details)


def _emit_cycle_completed_event(
    command: RunResidentCycleCommandV1, result: ResidentAutonomyResultV1
) -> ResidentCycleCompletedEventV1:
    """Construct and surface a :class:`ResidentCycleCompletedEventV1`."""
    event = ResidentCycleCompletedEventV1(
        event_id=f"resident-cycle-{uuid4().hex[:12]}",
        workspace=result.workspace,
        cycle_id=command.cycle_id,
        status=result.status,
        completed_at=utc_now_iso(),
    )
    logger.info(
        "[resident] cycle completed: workspace=%s cycle=%s status=%s actions=%s",
        event.workspace,
        event.cycle_id,
        event.status,
        result.actions,
    )
    return event


def run_resident_cycle(command: RunResidentCycleCommandV1) -> ResidentAutonomyResultV1:
    """Handle :class:`RunResidentCycleCommandV1` → advance the resident loop.

    ``force`` is read from ``command.context['force']`` (default ``False``), so
    unattended callers preserve the "only ticks when active" gate.  Emits a
    :class:`ResidentCycleCompletedEventV1`; normalizes any failure to
    :class:`ResidentAutonomyError`.
    """
    force = bool(command.context.get("force", False))
    try:
        status = get_resident_service(command.workspace).tick(force=force)
    except ResidentAutonomyError:
        raise
    except Exception as exc:
        raise ResidentAutonomyError(
            f"resident cycle execution failed: {exc}",
            code="cycle_execution_failed",
            details={"cycle_id": command.cycle_id, "workspace": command.workspace},
        ) from exc

    runtime = status.get("runtime", {}) if isinstance(status, dict) else {}
    summary = runtime.get("last_summary", {}) if isinstance(runtime, dict) else {}
    active = bool(runtime.get("active"))
    metrics = (
        {k: v for k, v in summary.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if isinstance(summary, dict)
        else {}
    )
    result = ResidentAutonomyResultV1(
        ok=True,
        workspace=command.workspace,
        cycle_id=command.cycle_id,
        status="completed" if active else "skipped_inactive",
        actions=_CYCLE_ACTIONS if active else (),
        evidence_refs=(),
        metrics=metrics,
    )
    _emit_cycle_completed_event(command, result)
    return result


def record_resident_evidence(command: RecordResidentEvidenceCommandV1) -> dict[str, Any]:
    """Handle :class:`RecordResidentEvidenceCommandV1` → append to the decision trace.

    Evidence is mapped onto the evidence-first decision trace as an
    ``evidence:{kind}`` stage entry, so it feeds the same meta-cognition loop.
    """
    return record_resident_decision(
        command.workspace,
        {
            "actor": "resident",
            "stage": f"evidence:{command.evidence_kind}",
            "summary": f"Recorded {command.evidence_kind} evidence for cycle {command.cycle_id}",
            "verdict": "success",
            "context_refs": [command.cycle_id],
            "actual_outcome": dict(command.payload),
        },
    )


__all__ = [
    "CapabilityGraph",
    "CounterfactualLab",
    "DecisionRecord",
    "DecisionTraceRecorder",
    "EvidenceBundle",
    "EvidenceBundleService",
    "ExecutionProjectionService",
    "FileChange",
    "GoalGovernor",
    "GoalProposal",
    "PerfEvidence",
    "QueryResidentStatusV1",
    "RecordResidentEvidenceCommandV1",
    "ResidentAgenda",
    "ResidentAutonomyError",
    "ResidentAutonomyResultV1",
    "ResidentCycleCompletedEventV1",
    "ResidentIdentity",
    "ResidentMode",
    "ResidentPMBridge",
    "ResidentPaths",
    "ResidentRuntimeState",
    "ResidentService",
    "ResidentStorage",
    "RunResidentCycleCommandV1",
    "SelfImprovementLab",
    "SkillFoundry",
    "SkillProposal",
    "SkillProposalStatus",
    "SourceType",
    "StaticAnalysisEvidence",
    "StrategyInsightEngine",
    "TestRunEvidence",
    "create_evidence_bundle_service",
    "get_evidence_service",
    "get_execution_projection_service",
    "get_resident_service",
    "query_resident_status",
    "record_resident_decision",
    "record_resident_evidence",
    "reset_resident_services",
    "run_resident_cycle",
]
