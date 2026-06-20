"""The default, process-wide :class:`CapabilityHandlerRegistry`.

``default_capability_registry`` assembles every migrated capability handler into
one immutable, identity-keyed registry and caches it for the process. The
dispatcher falls back to this registry when no explicit ``handlers`` registry is
injected, so each migrated family short-circuits the legacy ``if/elif`` ladder.

All thirteen capability families are migrated. The fourteen identity tuples below
must equal :data:`..._oracle.CAPABILITY_IDENTITY_TUPLES` (the blueprint family
answers two capability ids for one owner + contract); the Phase-5 fitness test
asserts that equality so the table can never silently drift from the dispatcher's
historical branch set.
"""

from __future__ import annotations

from functools import lru_cache

from polaris.cells.roles.runtime.internal.capability.handlers import (
    BlueprintGenerationHandler,
    BoundaryValidationHandler,
    BudgetReservationHandler,
    CeAstDependencyHandler,
    DirectorExecutionHandler,
    PmCriticalPathHandler,
    PmRuntimeProjectionHandler,
    QaAuditVerdictHandler,
    QaPytestVerificationHandler,
    QaTracebackParseHandler,
    QaVisualAuditVerdictHandler,
    TaskMarketDispatchHandler,
    WorkspaceGuardHandler,
)
from polaris.cells.roles.runtime.internal.capability.protocol import CapabilityHandler
from polaris.cells.roles.runtime.internal.capability.registry import (
    CapabilityHandlerRegistry,
    CapabilityIdentity,
)


def _handler_bindings() -> dict[CapabilityIdentity, CapabilityHandler]:
    """Return the identity-tuple -> handler bindings for all migrated families.

    Each migrated handler is instantiated once and bound to every identity triple
    it answers. The ``chief_engineer.blueprint`` family answers two capability ids
    (``generate_diff_specification`` and ``record_arch_memo``) for the same owner
    cell and command contract, so its single handler is bound to two triples.
    """
    blueprint_generation_handler: CapabilityHandler = BlueprintGenerationHandler()
    return {
        # director_task_execution
        ("execute_director_task", "director.execution", "ExecuteDirectorTaskCommandV1"): (DirectorExecutionHandler()),
        # qa_pytest_verification
        ("invoke_container_pytest", "factory.verification_guard", "VerifyCompletionCommandV1"): (
            QaPytestVerificationHandler()
        ),
        # qa_visual_audit_verdict
        ("issue_visual_audit_verdict", "qa.audit_verdict", "RunVisualQaAuditCommandV1"): (
            QaVisualAuditVerdictHandler()
        ),
        # task_market_dispatch
        ("dispatch_task_to_market", "runtime.task_market", "PublishTaskWorkItemCommandV1"): (
            TaskMarketDispatchHandler()
        ),
        # pm_critical_path
        ("evaluate_critical_path", "runtime.task_market", "QueryTaskMarketStatusV1"): (PmCriticalPathHandler()),
        # pm_runtime_projection
        ("project_runtime_status", "runtime.projection", "RuntimeProjectionQueryV1"): (PmRuntimeProjectionHandler()),
        # blueprint_generation (dual capability_id, single owner + contract)
        ("generate_diff_specification", "chief_engineer.blueprint", "GenerateTaskBlueprintCommandV1"): (
            blueprint_generation_handler
        ),
        ("record_arch_memo", "chief_engineer.blueprint", "GenerateTaskBlueprintCommandV1"): (
            blueprint_generation_handler
        ),
        # ce_ast_dependency
        ("verify_ast_dependency", "code_intelligence.engine", "VerifyAstDependencyQueryV1"): (CeAstDependencyHandler()),
        # qa_audit_verdict
        ("issue_audit_verdict", "qa.audit_verdict", "RunQaAuditCommandV1"): (QaAuditVerdictHandler()),
        # qa_traceback_parse
        ("parse_traceback_frames", "qa.audit_verdict", "ParseTracebackFramesCommandV1"): (QaTracebackParseHandler()),
        # budget_reservation
        ("allocate_context_token_budget", "finops.budget_guard", "ReserveBudgetCommandV1"): (
            BudgetReservationHandler()
        ),
        # workspace_guard
        ("intercept_illegal_mutations", "policy.workspace_guard", "WorkspaceWriteGuardQueryV1"): (
            WorkspaceGuardHandler()
        ),
        # boundary_validation
        ("validate_cell_boundary_change", "architect.design", "GenerateArchitectureDesignCommandV1"): (
            BoundaryValidationHandler()
        ),
    }


@lru_cache(maxsize=1)
def default_capability_registry() -> CapabilityHandlerRegistry:
    """Return the cached, process-wide registry of all migrated handlers."""
    return CapabilityHandlerRegistry.from_handlers(_handler_bindings())
