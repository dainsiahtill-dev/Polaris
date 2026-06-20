"""Phase-0 oracle: frozen characterization snapshot of the capability dispatcher.

This module pins, from git HEAD, the two invariants the behavioral decomposition
of ``execute_role_capability_invocation`` must preserve byte-for-byte:

* :data:`CAPABILITY_IDENTITY_TUPLES` — the exact set of
  ``(capability_id, owner_cell, command_contract)`` identity triples the
  thirteen ``is_*`` boolean flags (capability_commands.py ~lines 1537-1674)
  reconstruct by hand. The ``chief_engineer.blueprint`` blueprint-generation
  branch admits two capability ids (``generate_diff_specification`` and
  ``record_arch_memo``) for the same owner + contract, so it expands to two
  triples — giving fourteen triples across thirteen capability families.
* :data:`CAPABILITY_ERROR_CODES` — every ``error_code`` string literal the
  dispatcher emits, grouped into the shared cross-cutting prelude guards and the
  per-capability failure paths, plus :data:`ALL_ERROR_CODES` for whole-function
  diffing.

These are *frozen constants*, not behavior. Later phases assert each migrated
handler emits codes drawn from this set and that every registry key is a member
of :data:`CAPABILITY_IDENTITY_TUPLES`. Do NOT edit these to match new code —
edit code to match the oracle.
"""

from __future__ import annotations

from typing import Final

CapabilityIdentity = tuple[str, str, str]
"""``(capability_id, owner_cell, command_contract)``."""

# --- (a) Identity tuples -----------------------------------------------------
# One entry per concrete identity triple the dispatcher's is_* flags match.
CAPABILITY_IDENTITY_TUPLES: Final[frozenset[CapabilityIdentity]] = frozenset(
    {
        # 1. qa_pytest_verification
        ("invoke_container_pytest", "factory.verification_guard", "VerifyCompletionCommandV1"),
        # 2. qa_visual_audit_verdict
        ("issue_visual_audit_verdict", "qa.audit_verdict", "RunVisualQaAuditCommandV1"),
        # 3. task_market_dispatch
        ("dispatch_task_to_market", "runtime.task_market", "PublishTaskWorkItemCommandV1"),
        # 4. pm_critical_path
        ("evaluate_critical_path", "runtime.task_market", "QueryTaskMarketStatusV1"),
        # 5. pm_runtime_projection
        ("project_runtime_status", "runtime.projection", "RuntimeProjectionQueryV1"),
        # 6. blueprint_generation (dual capability_id, single owner + contract)
        ("generate_diff_specification", "chief_engineer.blueprint", "GenerateTaskBlueprintCommandV1"),
        ("record_arch_memo", "chief_engineer.blueprint", "GenerateTaskBlueprintCommandV1"),
        # 7. ce_ast_dependency
        ("verify_ast_dependency", "code_intelligence.engine", "VerifyAstDependencyQueryV1"),
        # 8. qa_audit_verdict
        ("issue_audit_verdict", "qa.audit_verdict", "RunQaAuditCommandV1"),
        # 9. qa_traceback_parse
        ("parse_traceback_frames", "qa.audit_verdict", "ParseTracebackFramesCommandV1"),
        # 10. budget_reservation
        ("allocate_context_token_budget", "finops.budget_guard", "ReserveBudgetCommandV1"),
        # 11. workspace_guard
        ("intercept_illegal_mutations", "policy.workspace_guard", "WorkspaceWriteGuardQueryV1"),
        # 12. boundary_validation
        ("validate_cell_boundary_change", "architect.design", "GenerateArchitectureDesignCommandV1"),
        # 13. director_task_execution
        ("execute_director_task", "director.execution", "ExecuteDirectorTaskCommandV1"),
    }
)

CAPABILITY_FAMILY_COUNT: Final[int] = 13
"""Distinct capability *families* (branches); identity triples may exceed this."""

# --- (b) Error-code literals -------------------------------------------------
# Cross-cutting prelude guards (capability_commands.py ~1496-1618). These stay
# VERBATIM in the dispatcher and are NOT owned by any single handler.
PRELUDE_ERROR_CODES: Final[tuple[str, ...]] = (
    "role_mismatch",
    "capability_not_mounted",
    "capability_role_denied",
    "capability_contract_mismatch",
    "qa_capability_role_denied",
    "qa_visual_capability_role_denied",
    "capability_fingerprint_mismatch",
    "payload_ref_outside_turn_context",
)

# Per-capability failure codes, keyed by the canonical family name. Each tuple is
# the exact ordered set of error_code literals emitted inside that branch body.
CAPABILITY_ERROR_CODES: Final[dict[str, tuple[str, ...]]] = {
    "budget_reservation": (
        "invalid_budget_metadata",
        "invalid_budget_command",
        "budget_guard_failed",
        "budget_denied",
    ),
    "workspace_guard": (
        "invalid_workspace_guard_path",
        "invalid_workspace_guard_query",
        "workspace_guard_failed",
        "workspace_guard_denied",
    ),
    "boundary_validation": (
        "invalid_architect_boundary_context",
        "invalid_architect_boundary_constraints",
        "invalid_architect_boundary_changed_paths",
        "invalid_architect_boundary_target_cell",
        "invalid_permission_command",
        "permission_evaluation_failed",
        "permission_denied",
        "workspace_guard_failed",
        "workspace_guard_denied",
        "invalid_architect_design_command",
        "architect_design_timeout",
        "architect_design_failed",
        "architect_design_rejected",
    ),
    "pm_critical_path": (
        "invalid_task_market_status_query",
        "task_market_status_query_failed",
    ),
    "pm_runtime_projection": (
        "invalid_runtime_projection_query",
        "runtime_projection_service_unavailable",
        "runtime_projection_query_failed",
    ),
    "ce_ast_dependency": (
        "invalid_ast_dependency_metadata",
        "invalid_ast_dependency_query",
        "ast_dependency_verification_failed",
    ),
    "qa_traceback_parse": (
        "invalid_traceback_metadata",
        "invalid_traceback_text",
        "invalid_traceback_parse_command",
        "traceback_parse_failed",
        "traceback_parse_rejected",
    ),
    "qa_audit_verdict": (
        "invalid_qa_audit_criteria",
        "invalid_qa_audit_evidence_paths",
        "invalid_qa_audit_command",
        "qa_audit_failed",
        "qa_audit_rejected",
    ),
    "qa_visual_audit_verdict": (
        "invalid_visual_audit_image_refs",
        "invalid_visual_audit_criteria",
        "invalid_visual_audit_evidence_paths",
        "visual_model_capability_override_denied",
        "invalid_visual_model_capability_query",
        "visual_model_capability_check_failed",
        "visual_model_capability_missing",
        "invalid_visual_qa_audit_command",
        "visual_qa_audit_failed",
        "visual_qa_audit_missing_evidence_ref",
        "visual_qa_audit_rejected",
    ),
    "qa_pytest_verification": (
        "invalid_verification_commands",
        "invalid_verification_evidence_paths",
        "invalid_verification_allowed_commands",
        "invalid_verification_metadata",
        "invalid_verification_command",
        "verification_guard_failed",
        "verification_failed",
    ),
    "blueprint_generation": (
        "invalid_blueprint_context",
        "invalid_blueprint_constraints",
        "invalid_blueprint_command",
        "blueprint_generation_failed",
        "blueprint_generation_rejected",
    ),
    "director_task_execution": (
        "invalid_director_execution_metadata",
        "invalid_director_execution_command",
        "director_execution_failed",
    ),
    "task_market_dispatch": (
        "unsupported_capability_contract",
        "invalid_task_market_payload",
        "invalid_task_market_metadata",
        "invalid_task_market_command",
        "task_market_publish_failed",
        "task_market_publish_rejected",
    ),
}

ALL_ERROR_CODES: Final[frozenset[str]] = frozenset(PRELUDE_ERROR_CODES).union(
    *(frozenset(codes) for codes in CAPABILITY_ERROR_CODES.values())
)
"""Every distinct ``error_code`` literal the dispatcher emits (77 codes)."""
