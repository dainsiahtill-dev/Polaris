"""Public service exports for `roles.adapters` cell."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.orchestration.workflow_runtime.public.service import (
    configure_orchestration_role_adapter_factory,
)
from polaris.cells.roles.adapters.public.contracts import (
    DirectorMaterializationQualityRepairScheduleResultV1,
    RunDirectorMaterializationQualityRepairScheduleCommandV1,
)

# ---------------------------------------------------------------------------
# NOTE on import order
# ---------------------------------------------------------------------------
# All imports from ``..internal.*`` below use RELATIVE paths (``from ..internal.X``).
# This is intentional: relative imports cause Python to execute ``internal/__init__.py``
# BEFORE any sub-module is imported, which guarantees that ``__init__.py``'s re-export
# statements (``X = X``) are fully evaluated before the sub-module is accessed.
#
# Absolute imports (``from polaris.cells.roles.adapters.internal.X``) bypass
# ``__init__.py`` entirely because Python registers the sub-module in ``sys.modules``
# directly, skipping the package-initialisation step.  This breaks re-export
# guarantees and causes ``ImportError`` when code tries to import adapter classes
# through the ``internal`` namespace.
# ---------------------------------------------------------------------------
from ..internal.architect_adapter import ArchitectAdapter
from ..internal.base import BaseRoleAdapter
from ..internal.chief_engineer_adapter import ChiefEngineerAdapter
from ..internal.director.deferred_repair_commit_bridge import (
    commit_materialization_deferred_repairs,
)
from ..internal.pm_adapter import PMAdapter
from ..internal.qa_adapter import (
    QAAdapter,
    _extract_workspace_quality_summary as extract_workspace_quality_summary,
)
from ..internal.resident_agi_adapter import ResidentAgiAdapter
from ..internal.schemas import (
    ROLE_OUTPUT_SCHEMAS,
    BaseToolEnabledOutput,
    BlueprintOutput,
    ConstructionPlan,
    DirectorOutput,
    PatchOperation,
    QAFinding,
    QAReportOutput,
    Task,
    TaskListOutput,
    ToolCall,
    get_schema_for_role,
)
from ..internal.workflow_adapter import (
    WorkflowRoleAdapter,
    WorkflowRoleResult,
    execute_workflow_role,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _build_registry() -> dict[str, Callable[[str], BaseRoleAdapter]]:
    registry: dict[str, Callable[[str], BaseRoleAdapter]] = {
        "pm": PMAdapter,
        "architect": ArchitectAdapter,
        "qa": QAAdapter,
        "chief_engineer": ChiefEngineerAdapter,
        "resident_agi": ResidentAgiAdapter,
    }
    director_adapter_factory: Callable[[str], BaseRoleAdapter] | None = None
    try:
        from ..internal.director_adapter import DirectorAdapter

        director_adapter_factory = cast("Callable[[str], BaseRoleAdapter]", DirectorAdapter)
    except (RuntimeError, ValueError):
        pass
    if director_adapter_factory is not None:
        registry["director"] = director_adapter_factory
    return registry


_ADAPTERS = _build_registry()


def create_role_adapter(role_id: str, workspace: str) -> BaseRoleAdapter:
    role_token = str(role_id or "").strip().lower()
    workspace_token = str(workspace or "").strip()
    if not role_token:
        raise ValueError("role_id must be a non-empty string")
    if not workspace_token:
        raise ValueError("workspace must be a non-empty string")
    adapter_class = _ADAPTERS.get(role_token)
    if adapter_class is None:
        raise ValueError(f"Unknown role: {role_token}, supported: {list(_ADAPTERS.keys())}")
    return adapter_class(workspace_token)


def run_director_materialization_quality_repair_schedule(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    artifact_quality_issues: tuple[dict[str, Any], ...] = (),
    advisor_notes: tuple[Any, ...] = (),
    convergence_verifier: Any | None = None,
    execution_attempt: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run Director materialization-quality repair schedule as a tuple projection."""

    result = run_director_materialization_quality_repair_schedule_result(
        RunDirectorMaterializationQualityRepairScheduleCommandV1(
            adapter_port=adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=tuple(artifact_quality_errors),
            artifact_quality_issues=tuple(dict(item) for item in artifact_quality_issues),
            advisor_notes=tuple(advisor_notes or ()),
            convergence_verifier=convergence_verifier,
            execution_attempt=execution_attempt,
        )
    )
    return [dict(item) for item in result.tool_results], dict(result.summary)


def _split_materialization_effect_results(
    tool_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split runtime schedule evidence from actual tool/effect results.

    Director Runtime may return ``missing_evidence`` rows to preserve schedule
    observability. Those rows are useful diagnostics, but they are not tool
    effects and must not drive adapter execution counters, changed-file checks,
    or write-evidence decisions.
    """

    effect_results: list[dict[str, Any]] = []
    diagnostic_results: list[dict[str, Any]] = []
    for item in tool_results:
        copied = dict(item)
        if str(
            copied.get("evidence_status") or ""
        ).strip() == "missing_evidence" and not _materialization_result_has_effect_payload(copied):
            diagnostic_results.append(copied)
            continue
        effect_results.append(copied)
    return effect_results, diagnostic_results


def _materialization_result_has_effect_payload(item: dict[str, Any]) -> bool:
    """Return whether a projected runtime row carries a dispatchable or physical effect.

    Planning/source-tool metadata and a repair-kernel denial are evidence about
    a callback, not an effect.  Treating those rows as tool results made the
    Factory quality gate believe that an authority-free repair had executed and
    caused it to rerun validation against an unchanged workspace.
    """

    result = item.get("result")
    if not isinstance(result, Mapping):
        return False
    if bool(item.get("success")) and str(item.get("tool_name") or item.get("tool") or "").strip() in {
        "write_file",
        "edit_file",
        "delete_file",
        "execute_command",
        "run_command",
    }:
        return True
    if result.get("deferred_request") is not None:
        return True
    if str(result.get("status") or "").strip() in {
        "deferred_repair_effects_pending",
        "deferred_command_effect_pending",
    }:
        return True
    if (
        str(result.get("file") or result.get("path") or "").strip()
        and str(result.get("operation") or result.get("tool") or result.get("tool_name") or "").strip()
    ):
        return True
    if str(result.get("before_sha256") or result.get("after_sha256") or "").strip():
        return True
    if isinstance(result.get("effect_receipt"), Mapping):
        return True
    repair_kernel = result.get("repair_kernel")
    if isinstance(repair_kernel, Mapping):
        receipts = repair_kernel.get("receipts")
        if isinstance(receipts, Sequence) and not isinstance(receipts, str | bytes):
            return any(isinstance(receipt, Mapping) for receipt in receipts)
        if str(repair_kernel.get("receipt_id") or repair_kernel.get("plan_id") or "").strip():
            return True
    return False


def _call_materialization_quality_repair_facade(
    facade: Any,
    *,
    artifact_quality_errors: tuple[str, ...],
    artifact_quality_issues: tuple[dict[str, Any], ...],
    runner_step_ids: tuple[str, ...],
    runner: Any,
    plan_probe_preaudit: dict[str, Any],
    convergence_verifier_present: bool,
) -> Any:
    """Call runtime facade while preserving old mock/facade signatures."""
    kwargs: dict[str, Any] = {
        "artifact_quality_errors": artifact_quality_errors,
        "artifact_quality_issues": artifact_quality_issues,
        "runner_step_ids": runner_step_ids,
        "runner": runner,
        "plan_probe_preaudit": plan_probe_preaudit,
        "convergence_verifier_present": convergence_verifier_present,
    }
    try:
        signature = inspect.signature(facade)
    except (TypeError, ValueError):
        return facade(**kwargs)

    parameters = signature.parameters
    accepts_var_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    if not accepts_var_kwargs:
        kwargs = {key: value for key, value in kwargs.items() if key in parameters}
    return facade(**kwargs)


def run_director_materialization_quality_repair_schedule_result(
    command: RunDirectorMaterializationQualityRepairScheduleCommandV1,
) -> DirectorMaterializationQualityRepairScheduleResultV1:
    """Run Director materialization-quality repair schedule through the typed public boundary."""

    from polaris.cells.director.runtime.public import (
        QueryDirectorRepairMaterializationQualityScheduleV1,
        query_director_repair_materialization_quality_schedule,
        run_director_materialization_quality_repair_facade,
    )

    from ..internal.director.materialization_quality_runtime_ports import (
        build_materialization_quality_step_runner,
        project_materialization_quality_facade_summary,
        project_materialization_quality_plan_probe_preaudit,
    )

    task = dict(command.task)
    artifact_quality_errors = tuple(command.artifact_quality_errors)
    artifact_quality_issues = tuple(dict(item) for item in command.artifact_quality_issues)
    schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
    )
    plan_probe_preaudit = project_materialization_quality_plan_probe_preaudit(
        task=dict(command.task),
        adapter=command.adapter_port,
        artifact_quality_errors=artifact_quality_errors,
        artifact_quality_issues=artifact_quality_issues,
    )
    facade_result = _call_materialization_quality_repair_facade(
        run_director_materialization_quality_repair_facade,
        artifact_quality_errors=artifact_quality_errors,
        artifact_quality_issues=artifact_quality_issues,
        runner_step_ids=tuple(step.step_id for step in schedule.items),
        runner=build_materialization_quality_step_runner(
            command.adapter_port,
            task=task,
            task_id=command.task_id,
            artifact_quality_errors=artifact_quality_errors,
            artifact_quality_issues=artifact_quality_issues,
            advisor_notes=command.advisor_notes,
            convergence_verifier=command.convergence_verifier,
            execution_attempt=command.execution_attempt,
        ),
        plan_probe_preaudit=plan_probe_preaudit,
        convergence_verifier_present=command.convergence_verifier is not None,
    )
    all_results = [dict(item) for item in facade_result.tool_results]
    results, diagnostic_results = _split_materialization_effect_results(all_results)
    public_summary = project_materialization_quality_facade_summary(
        ordered_steps=facade_result.ordered_steps,
        tool_results=all_results,
        artifact_quality_errors=artifact_quality_errors,
        coverage_preaudit=dict(facade_result.coverage_preaudit),
        plan_probe_preaudit=dict(facade_result.plan_probe_preaudit),
        schedule_summary=dict(facade_result.schedule_summary),
        receipt_projections=facade_result.receipt_projections,
        schedule_reconciliation=dict(facade_result.schedule_reconciliation),
        convergence_verifier_present=command.convergence_verifier is not None,
    )
    runtime_ports_diagnostics = {
        key: dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value
        for key, value in (
            ("materialization_quality_runtime_ports", public_summary.get("materialization_quality_runtime_ports")),
            ("repair_kernel_migration_debt", public_summary.get("repair_kernel_migration_debt")),
            ("adapter_projection_debt", public_summary.get("adapter_projection_debt")),
        )
        if value is not None
    }
    for internal_key in (
        "materialization_quality_bridge",
        "materialization_quality_runtime_ports",
        "repair_kernel_migration_debt",
        "adapter_projection_debt",
    ):
        public_summary.pop(internal_key, None)
    if runtime_ports_diagnostics:
        public_summary["runtime_ports_diagnostics"] = runtime_ports_diagnostics
    if diagnostic_results:
        public_summary["non_effect_evidence_result_count"] = len(diagnostic_results)
        public_summary["non_effect_evidence_results"] = diagnostic_results
    public_summary["runtime_facade_summary"] = dict(facade_result.summary)
    public_summary["coverage_preaudit"] = dict(facade_result.coverage_preaudit)
    public_summary["plan_probe_preaudit"] = dict(facade_result.plan_probe_preaudit)
    public_summary["schedule_reconciliation"] = dict(facade_result.schedule_reconciliation)
    public_summary["dark_launch_comparison"] = {
        "schema_version": "director.repair_shadow_comparison.v1",
        "comparison_mode": "receipt_projection_self_check",
        "cutover_ready": False,
        "cutover_blockers": ["independent_shadow_required"],
        "independent_shadow_required": True,
        "independent_shadow_satisfied": False,
        "runtime_facade_entrypoint": "run_director_materialization_quality_repair_facade",
    }
    public_summary["runtime_materialization_facade"] = {
        "schema_version": facade_result.schema_version,
        "source": facade_result.source,
        "owner_cell": facade_result.owner_cell,
        "execution_boundary": facade_result.execution_boundary,
    }
    public_summary["public_boundary"] = {
        "schema_version": "roles.adapters.materialization_quality_repair_boundary.v1",
        "mode": "runtime_owned_schedule_public_boundary",
        "internal_function_exported": False,
        "repair_kernel_owner": "director.runtime",
        "director_runtime_public_summary_required": True,
        "runtime_facade_entrypoint": "run_director_materialization_quality_repair_facade",
        "typed_contract": "RunDirectorMaterializationQualityRepairScheduleCommandV1",
        "typed_result": "DirectorMaterializationQualityRepairScheduleResultV1",
    }
    return DirectorMaterializationQualityRepairScheduleResultV1(
        tool_results=tuple(dict(item) for item in results),
        summary=public_summary,
    )


def _project_public_materialization_repair_kernel_summary(
    tool_results: list[dict[str, Any]],
    *,
    coverage_preaudit: dict[str, Any],
) -> dict[str, Any]:
    receipt_count = 0
    for item in tool_results:
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        repair_kernel = result.get("repair_kernel")
        if not isinstance(repair_kernel, dict):
            continue
        receipts = repair_kernel.get("receipts")
        if isinstance(receipts, list | tuple):
            receipt_count += sum(1 for receipt in receipts if isinstance(receipt, dict))
        elif repair_kernel.get("receipt_id") or repair_kernel.get("plan_id"):
            receipt_count += 1
    return {
        "schema_version": "director.repair_kernel_summary_projection.v1",
        "stage": "materialization_quality_repairs",
        "receipt_count": receipt_count,
        "coverage_report": dict(coverage_preaudit),
        "source": "director.runtime.repair_kernel.materialization_quality_facade",
    }


class _PublicPostExecutionRepairAdapter:
    """Minimal adapter surface for public post-execution schedule callers."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        artifact_quality_errors: tuple[str, ...] = (),
    ) -> None:
        self.workspace = str(Path(workspace))
        # Post-execution planners consume verifier diagnostics from the adapter
        # boundary.  Dropping them made coverage report an executable repair
        # while the actual runner received no diagnostics and returned
        # ``repair_not_planned``.
        self.artifact_quality_errors = artifact_quality_errors
        self._execution = None

    def _update_task_progress(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def run_director_post_execution_repair_schedule(
    workspace: str | Path,
    *,
    task_id: str = "roles-adapters-post-execution-repair",
    artifact_quality_errors: list[str] | tuple[str, ...] = (),
    execution_attempt: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Run the runtime-owned Director post-execution repair schedule.

    Public callers may request post-execution repair convergence, but must not
    call language-specific helpers. Language dispatch stays inside the
    runtime-owned schedule and the adapter bridge.
    """

    from ..internal.director.post_execution_repair_bridge import (
        run_post_execution_language_repairs,
    )

    return run_post_execution_language_repairs(
        _PublicPostExecutionRepairAdapter(
            workspace,
            artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
        ),
        task_id=task_id,
        execution_attempt=execution_attempt,
    )


def resolve_director_semantic_quality_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str,
) -> list[str]:
    """Return semantic quality-repair target files through the public boundary."""

    from ..internal.director.quality_gate import _semantic_quality_repair_target_files

    return _semantic_quality_repair_target_files(
        artifact_quality_errors=list(artifact_quality_errors),
        changed_files=list(changed_files),
        workspace_full=workspace_full,
    )


def resolve_director_causal_quality_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str,
) -> list[str]:
    """Resolve causal implementation targets before a Factory owner claim.

    Factory must select the canonical TaskRuntime owner before invoking the
    Director adapter.  Language-aware target discovery used to run only after
    that claim, so verifier paths such as ``engine/engine_test.go`` leased the
    test task even when the same diagnostic identified ``engine/engine.go`` as
    the implementation home.  Expose the adapter's existing read-only target
    discovery through its public Cell boundary; callers receive paths only and
    still rely on CE/JobToken authority for ownership and mutation scope.
    """

    from ..internal.director.quality_gate import (
        _explicit_artifact_quality_repair_target_files,
        _go_runtime_smoke_repair_target_files,
        _is_test_like_python_path,
        _javascript_runtime_smoke_repair_target_files,
        _python_runtime_smoke_repair_target_files,
        _rust_test_behavior_repair_target_files,
        _semantic_quality_repair_target_files,
    )

    errors = list(artifact_quality_errors)
    changed = list(changed_files)
    python_runtime_candidates = _python_runtime_smoke_repair_target_files(
        artifact_quality_errors=errors,
        changed_files=changed,
        workspace_full=workspace_full,
    )
    candidates = [
        *_explicit_artifact_quality_repair_target_files(
            artifact_quality_errors=errors,
            changed_files=changed,
            workspace_full=workspace_full,
        ),
        *python_runtime_candidates,
        *_javascript_runtime_smoke_repair_target_files(
            artifact_quality_errors=errors,
            changed_files=changed,
            workspace_full=workspace_full,
        ),
        *_go_runtime_smoke_repair_target_files(
            artifact_quality_errors=errors,
            changed_files=changed,
            workspace_full=workspace_full,
        ),
        *_rust_test_behavior_repair_target_files(
            artifact_quality_errors=errors,
            changed_files=changed,
            workspace_full=workspace_full,
        ),
        *_semantic_quality_repair_target_files(
            artifact_quality_errors=errors,
            changed_files=changed,
            workspace_full=workspace_full,
        ),
    ]
    if any(not candidate.endswith(".py") for candidate in python_runtime_candidates):
        # Once the Python observer has resolved a native production frontier,
        # its test wrapper remains evidence but is not a causal mutation
        # target.  Keep the raw extractor complete; narrow only this public
        # execution-authority projection so QA assertions cannot consume a
        # bounded Director repair turn or hide the product defect.
        candidates = [candidate for candidate in candidates if not _is_test_like_python_path(candidate)]
    return list(
        dict.fromkeys(str(path or "").strip().replace("\\", "/") for path in candidates if str(path or "").strip())
    )


def build_director_materialization_quality_repair_message(
    *,
    original_message: str,
    artifact_quality_errors: list[str],
    directive_artifact_quality_errors: list[str] | None = None,
    changed_files: list[str],
    missing_target_files: list[str] | None = None,
    repair_target_files: list[str] | None = None,
    workspace_full: str = "",
    interface_discrepancy_evidence: dict[str, Any] | None = None,
) -> str:
    """Build Director materialization-quality repair prompt text."""

    from ..internal.director.quality_gate import _build_materialization_quality_repair_message

    return _build_materialization_quality_repair_message(
        original_message=original_message,
        artifact_quality_errors=list(artifact_quality_errors),
        directive_artifact_quality_errors=(
            list(directive_artifact_quality_errors) if directive_artifact_quality_errors is not None else None
        ),
        changed_files=list(changed_files),
        missing_target_files=list(missing_target_files) if missing_target_files is not None else None,
        repair_target_files=list(repair_target_files) if repair_target_files is not None else None,
        workspace_full=workspace_full,
        interface_discrepancy_evidence=(
            dict(interface_discrepancy_evidence) if interface_discrepancy_evidence is not None else None
        ),
    )


def register_all_adapters(service: object) -> None:
    """Register role adapter factory to orchestration service if supported."""
    if hasattr(service, "set_role_adapter_factory"):
        return


def get_supported_roles() -> list[str]:
    return list(_ADAPTERS.keys())


async def run_director_materialization_quality_repair(
    workspace: str,
    *,
    task: dict[str, object],
    target_task_id: str,
    run_id: str,
    context: dict[str, object],
    original_message: str,
    llm_call_timeout: float,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    repair_target_files: list[str],
    repair_attempt: int = 1,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Run Director's materialization-quality repair through the roles public boundary."""

    from ..internal.director.quality_gate import _run_materialization_quality_repair_retry
    from ..internal.director.quality_gate._candidate_guard import (
        DirectorQualityRepairCandidateGuard,
    )
    from ..internal.director_adapter import DirectorAdapter

    # Factory already resolved the causal implementation paths and claimed a
    # JobToken over them.  Preserve that explicit cross-Cell authority.  Do
    # not re-derive the rollback scope from a mutable context or the broader
    # owner task row: live L3-23 edited src/patience.rs while a second
    # inference snapshotted src/lib.rs, then reported a rollback that never
    # restored the physical candidate.
    target_files: list[str] = []
    seen_target_files: set[str] = set()
    raw_owner_targets = task.get("target_files") or ()
    owner_targets = (
        [raw_owner_targets]
        if isinstance(raw_owner_targets, str)
        else list(raw_owner_targets)
        if isinstance(raw_owner_targets, (list, tuple))
        else []
    )
    # The Provider's narrow intent is not a transactional rollback boundary:
    # native tools can legally choose another already-authorized materialized
    # implementation path after reading the failing tests. Snapshot the bounded
    # authorized/materialized universe, then the guard seals and restores only
    # paths whose hashes actually changed.
    for item in [*repair_target_files, *owner_targets]:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen_target_files:
            continue
        seen_target_files.add(normalized)
        target_files.append(normalized)
    try:
        candidate_guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=workspace,
            candidate_id=f"{run_id}:{target_task_id}:quality-repair-{repair_attempt}",
            target_files=target_files,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return [], {
            "attempted": True,
            "success": False,
            "write_tool_evidence": False,
            "error_code": "quality_repair_candidate_snapshot_failed",
            "error": f"quality_repair_candidate_snapshot_failed:{type(exc).__name__}:{exc}",
        }

    adapter = DirectorAdapter(str(workspace))
    try:
        results, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task=dict(task),
            target_task_id=target_task_id,
            run_id=run_id,
            context=dict(context),
            original_message=original_message,
            llm_call_timeout=llm_call_timeout,
            artifact_quality_errors=list(artifact_quality_errors),
            changed_files=list(changed_files),
            repair_attempt=repair_attempt,
        )
    except BaseException:
        await candidate_guard.seal_effect()
        await candidate_guard.rollback(reason="quality_repair_provider_boundary_failed")
        raise

    normalized_summary = dict(summary)
    mutation_committed = bool(normalized_summary.get("write_tool_evidence")) or any(
        str(item.get("tool") or "").strip() in {"write_file", "edit_file", "delete_file"} and bool(item.get("success"))
        for item in results
        if isinstance(item, dict)
    )
    if mutation_committed:
        normalized_summary["candidate_guard_seal"] = await candidate_guard.seal_effect()
        normalized_summary["_candidate_guard"] = candidate_guard
    else:
        normalized_summary["candidate_guard_seal"] = candidate_guard.accept(
            reason="quality_repair_produced_no_mutation"
        )
    return [dict(item) for item in results], normalized_summary


_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level factory registration — the authoritative one for this cell.
# Guard against import-order races where workflow_runtime internal modules are
# not yet fully initialised when this module is imported first.
# ---------------------------------------------------------------------------
try:
    configure_orchestration_role_adapter_factory(create_role_adapter)
except (RuntimeError, ValueError) as exc:
    _logger.debug(
        "workflow_runtime not yet fully initialised at import time "
        "(%s); factory will be configured lazily on first orchestration "
        "service access.  Import will still succeed.",
        exc,
    )


__all__ = [
    "ROLE_OUTPUT_SCHEMAS",
    "ArchitectAdapter",
    "BaseRoleAdapter",
    "BaseToolEnabledOutput",
    "BlueprintOutput",
    "ChiefEngineerAdapter",
    "ConstructionPlan",
    "DirectorOutput",
    "PMAdapter",
    "PatchOperation",
    "QAAdapter",
    "QAFinding",
    "QAReportOutput",
    "ResidentAgiAdapter",
    "Task",
    "TaskListOutput",
    "ToolCall",
    "WorkflowRoleAdapter",
    "WorkflowRoleResult",
    "build_director_materialization_quality_repair_message",
    "commit_materialization_deferred_repairs",
    "create_role_adapter",
    "execute_workflow_role",
    "extract_workspace_quality_summary",
    "get_schema_for_role",
    "get_supported_roles",
    "register_all_adapters",
    "resolve_director_semantic_quality_repair_target_files",
    "run_director_materialization_quality_repair",
    "run_director_materialization_quality_repair_schedule",
    "run_director_materialization_quality_repair_schedule_result",
    "run_director_post_execution_repair_schedule",
]
