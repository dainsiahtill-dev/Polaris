"""Private mixin _Mixin01 for OrchestrationStageExecutor."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shlex
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerPortfolioTaskV1,
    ProjectKindAuthorityV1,
    VerificationCommandAuthorityV1,
    chief_engineer_source_suffixes_for_language,
    classify_chief_engineer_pm_entrypoint_kind,
    derive_project_kind_authority_from_catalog_snapshot,
    project_chief_engineer_completion_contract_semantic_errors,
    project_chief_engineer_portfolio_delivery_depth_feasibility,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.control_plane.verifier_policy.public import (
    CompileEvidencePolicyCommandV1,
)
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    RoleStructuredOutputContractV1,
)
from polaris.cells.roles.runtime.public.contracts import (
    RoleExecutionResultV1,
)
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    bind_runtime_task_to_factory_run,
)
from polaris.kernelone.constants import (
    MAX_LLM_PROVIDER_TIMEOUT_SECONDS,  # noqa: F401 — re-exported for characterization-test surface
)
from polaris.kernelone.storage import resolve_storage_roots

from .. import (
    factory_ce_evidence as ce_evidence,
    factory_deadline_calculations as deadline_calc,
    factory_director_dispatch_impl as director_dispatch_impl,
    factory_director_route_audit as route_audit,
)
from ..factory_deadline_calculations import (  # noqa: F401 — re-exported for characterization-test surface
    _CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS,
    _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS,
    _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS,
    ChiefEngineerExecutionAttemptLeaseBudget as _ChiefEngineerExecutionAttemptLeaseBudget,
)
from ..factory_deadline_policy import (
    FactoryDeadlineAdmissionV1,
    TaskDependencyScheduleV1,
)
from ..factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from ..factory_run_models import (
    FactoryRun,
    StageResult,
)
from ._helpers import (
    _CE_BLUEPRINT_OUTPUT_CONTRACT,
    _CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS,
    _ChiefEngineerExecutionAttemptLeaseScope,
    _ChiefEngineerPortfolioAuthorityError,
    _ChiefEngineerPortfolioAuthorityV1,
)
from ._pkg_proxy import pkg

logger = logging.getLogger("polaris.cells.factory.pipeline.internal.factory_stage_executor")


class _Mixin01:
    """Method group extracted from OrchestrationStageExecutor (lossless)."""

    @staticmethod
    def _director_admission_failure_projection(
        admission_decision: FactoryDeadlineAdmissionV1,
    ) -> tuple[str, str, str, str]:
        """Project one admission rejection without misreporting its cause."""

        return route_audit.director_admission_failure_projection(admission_decision)

    @staticmethod
    def _director_dispatch_deadline_admission_decision(
        context: dict[str, Any],
        *,
        requested_timeout_seconds: int,
        first_materialization_pending: bool,
        materialization_pending: bool,
        dependency_schedule: TaskDependencyScheduleV1,
    ) -> FactoryDeadlineAdmissionV1:
        """Return the canonical typed admission for one Director dispatch."""

        return deadline_calc.director_dispatch_deadline_admission_decision(
            context,
            requested_timeout_seconds=requested_timeout_seconds,
            first_materialization_pending=first_materialization_pending,
            materialization_pending=materialization_pending,
            dependency_schedule=dependency_schedule,
        )

    @staticmethod
    def _director_first_materialization_min_budget_seconds(context: dict[str, Any]) -> float:
        return deadline_calc.director_first_materialization_min_budget_seconds(context)

    @staticmethod
    def _quality_gate_reserved_budget_seconds(context: dict[str, Any]) -> float:
        return deadline_calc.quality_gate_reserved_budget_seconds(context)

    @staticmethod
    def _director_downstream_reserved_budget_seconds(
        context: dict[str, Any],
        *,
        materialization_pending: bool,
        remaining_task_count: int,
    ) -> float:
        """Reserve only executable downstream work at the Director boundary."""

        return deadline_calc.director_downstream_reserved_budget_seconds(
            context,
            materialization_pending=materialization_pending,
            remaining_task_count=remaining_task_count,
        )

    @staticmethod
    def _director_dispatch_timeout_settle_grace_seconds(context: dict[str, Any]) -> int:
        return deadline_calc.director_dispatch_timeout_settle_grace_seconds(context)

    @staticmethod
    def _chief_engineer_llm_timeout_seconds(context: dict[str, Any]) -> int:
        return deadline_calc.chief_engineer_llm_timeout_seconds(context)

    @staticmethod
    def _chief_engineer_execution_attempt_lease_budget(
        execution_timeout_seconds: int,
    ) -> _ChiefEngineerExecutionAttemptLeaseBudget:
        """Derive one bounded TaskRuntime TTL and heartbeat cadence."""

        return deadline_calc.chief_engineer_execution_attempt_lease_budget(execution_timeout_seconds)

    @staticmethod
    def _chief_engineer_deadline_projection_decision(
        context: dict[str, Any],
        *,
        requested_timeout_seconds: int,
        dependency_schedule: TaskDependencyScheduleV1,
        output_tokens: int | None = None,
    ) -> FactoryDeadlineAdmissionV1:
        """Return admission for one project-level Chief Engineer LLM call."""

        return deadline_calc.chief_engineer_deadline_projection_decision(
            context,
            requested_timeout_seconds=requested_timeout_seconds,
            dependency_schedule=dependency_schedule,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _chief_engineer_projection_semantic_terms(task_context: dict[str, Any]) -> list[str]:
        return deadline_calc.chief_engineer_projection_semantic_terms(task_context)

    @staticmethod
    def _enrich_chief_engineer_projection_context(task_context: dict[str, Any]) -> None:
        deadline_calc.enrich_chief_engineer_projection_context(task_context)

    @staticmethod
    def _director_binding_timeout_quarantine_count() -> int:
        return deadline_calc.director_binding_timeout_quarantine_count()

    # ── Director binding fanout ────────────────────────────────────────────

    @staticmethod
    def _director_binding_identity(provider_id: str, model: str, binding_id: str = "") -> str:
        return deadline_calc.director_binding_identity(provider_id, model, binding_id)

    def _record_director_binding_skip(
        self,
        *,
        provider_id: str,
        model: str,
        binding_id: str,
        reason: str,
    ) -> None:
        return director_dispatch_impl._record_director_binding_skip(
            self, provider_id=provider_id, model=model, binding_id=binding_id, reason=reason
        )

    def _director_readiness_skip_reasons(self, context: dict[str, Any] | None = None) -> dict[str, str]:
        return director_dispatch_impl._director_readiness_skip_reasons(self, context)

    def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        return director_dispatch_impl._resolve_director_binding_fanout(self, context)

    async def _execute_director_binding_fanout(
        self,
        *,
        service: Any,
        workspace: str,
        tasks: list[str] | None,
        base_options: dict[str, Any],
        bindings: list[dict[str, str]],
        timeout_seconds: int = 600,
        deadline_monotonic: float | None = None,
        cancel_event: asyncio.Event | None = None,
        abort_checker: Any = None,
        skipped_bindings: list[dict[str, Any]] | None = None,
        authority_port: FactoryRoleEvidenceAuthorityPort,
    ) -> CommandResult:
        return await director_dispatch_impl._execute_director_binding_fanout(
            self,
            service=service,
            workspace=workspace,
            tasks=tasks,
            base_options=base_options,
            bindings=bindings,
            timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline_monotonic,
            cancel_event=cancel_event,
            abort_checker=abort_checker,
            skipped_bindings=skipped_bindings,
            authority_port=authority_port,
        )

    @staticmethod
    def _build_per_binding_route_events(per_binding: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return route_audit.build_per_binding_route_events(per_binding)

    @staticmethod
    def _build_fail_closed_director_route_events(
        *,
        attempts: list[dict[str, Any]],
        stage_signals: list[dict[str, Any]],
        per_binding_route_events: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return route_audit.build_fail_closed_director_route_events(
            attempts=attempts,
            stage_signals=stage_signals,
            per_binding_route_events=per_binding_route_events,
        )

    @staticmethod
    def _reclassify_binding_coverage_signals(
        stage_signals: list[dict[str, Any]],
        per_binding_route_events: list[dict[str, Any]],
    ) -> None:
        route_audit.reclassify_binding_coverage_signals(stage_signals, per_binding_route_events)

    def _validate_director_binding_coverage(
        self,
        additional_events: list[dict[str, Any]] | None = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        return route_audit.validate_director_binding_coverage(
            self.workspace,
            additional_events=additional_events,
        )

    def _director_provider_health_failure_signal(self) -> dict[str, Any] | None:
        return route_audit.director_provider_health_failure_signal(self.workspace)

    @staticmethod
    def _director_provider_health_failure_signal_from_events(
        events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        return route_audit.director_provider_health_failure_signal_from_events(events)

    @staticmethod
    def _llm_event_error_text(event: dict[str, Any]) -> str:
        return ce_evidence.llm_event_error_text(event)

    async def _execute_docs_generation(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing docs generation for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)
        authority_port = self._factory_role_evidence_cutoff_port(context)

        service = self._build_orchestration_service(context)
        command_result = cast(
            CommandResult,
            await self._call_with_factory_role_evidence_authority(
                authority_port,
                "architect",
                lambda: service.execute_pm_run(
                    workspace=str(self.workspace),
                    run_type="architect",
                    options={
                        "directive": context.get("directive", "Generate project documentation"),
                        "run_director": False,
                    },
                ),
            ),
        )
        final_result = await self._wait_run_completion(
            service,
            command_result,
            timeout_seconds=int(context.get("timeout", 600)),
            cancel_event=self._resolve_cancel_event(context),
            abort_checker=abort_checker,
        )
        if str(final_result.status or "").strip().lower() == "cancelled":
            return StageResult(
                stage="docs_generation",
                status="cancelled",
                output=f"Docs generation cancelled: {final_result.message or 'N/A'}",
                artifacts=[],
            )

        upstream_success = final_result.status in {"completed", "success"}
        stage_signals: list[dict[str, Any]] = []
        if not upstream_success:
            stage_signals.append(
                {
                    "code": "docs.run_status_non_success",
                    "severity": "error",
                    "detail": str(final_result.message or "").strip() or str(final_result.status or "unknown"),
                    "upstream_status": str(final_result.status or "").strip(),
                }
            )
        missing_artifacts: list[str] = []
        if upstream_success:
            missing_artifacts = self._ensure_docs_artifacts(
                directive=str(context.get("directive") or ""),
                summary=str(final_result.message or ""),
            )
            if missing_artifacts:
                stage_signals.append(
                    {
                        "code": "docs.required_artifacts_missing",
                        "severity": "error",
                        "detail": f"Missing docs artifacts: {missing_artifacts}",
                    }
                )
        artifacts: list[str] = []
        for candidate in ("docs/plan.md", "docs/architecture.md"):
            if self._artifact_exists(candidate, min_chars=1):
                artifacts.append(candidate)
        self._mirror_docs_artifacts(run.id, artifacts)
        if stage_signals:
            artifacts.append(
                self._write_stage_signal_artifact(
                    stage="docs_generation",
                    run_id=run.id,
                    signals=stage_signals,
                )
            )
        stage_status = "success" if (upstream_success and not missing_artifacts) else "failed"
        status_label = "completed" if stage_status == "success" else "failed"
        return StageResult(
            stage="docs_generation",
            status=stage_status,
            output=(f"Docs generation {status_label}: {final_result.message or 'N/A'}; signals={len(stage_signals)}"),
            artifacts=artifacts,
        )

    async def _execute_pm_planning(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing PM planning for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)
        authority_port = self._factory_role_evidence_cutoff_port(context)
        planning_directive = self._build_pm_planning_directive(
            context.get("directive", "Plan implementation tasks"),
        )
        reset_summary = (
            pkg()
            .TaskRuntimeService(str(self.workspace))
            .reset_records(
                keep_plan=True,
                factory_run_id=run.id,
            )
        )
        if reset_summary.get("ok") is not True:
            return StageResult(
                stage="pm_planning",
                status="failed",
                output=(
                    "PM planning blocked by TaskRuntime reset authority: "
                    f"code={reset_summary.get('code') or 'task_runtime_reset_failed'}; "
                    f"conflicts={reset_summary.get('conflict_count') or 0}"
                ),
                artifacts=[],
            )

        service = self._build_orchestration_service(context)
        pm_run_metadata = self._pm_deterministic_contract_metadata_for_context(run, context)
        pm_run_options: dict[str, Any] = {
            "directive": planning_directive,
            "run_director": False,
        }
        if pm_run_metadata:
            pm_run_options["metadata"] = pm_run_metadata
        command_result = cast(
            CommandResult,
            await self._call_with_factory_role_evidence_authority(
                authority_port,
                "pm",
                lambda: service.execute_pm_run(
                    workspace=str(self.workspace),
                    run_type="pm",
                    options=pm_run_options,
                ),
            ),
        )
        final_result = await self._wait_run_completion(
            service,
            command_result,
            timeout_seconds=int(context.get("timeout", 600)),
            cancel_event=self._resolve_cancel_event(context),
            abort_checker=abort_checker,
        )
        if str(final_result.status or "").strip().lower() == "cancelled":
            return StageResult(
                stage="pm_planning",
                status="cancelled",
                output=f"PM planning cancelled: {final_result.message or 'N/A'}",
                artifacts=[],
            )

        stage_signals: list[dict[str, Any]] = [
            {
                "code": "pm.task_runtime_reset",
                "severity": "info",
                "detail": "Cleared stale executable task records before materializing the current PM plan.",
                "cleared_count": int(cast("int | str", reset_summary.get("cleared_count")) or 0),
                "failed_count": int(cast("int | str", reset_summary.get("failed_count")) or 0),
            }
        ]
        if pm_run_metadata:
            stage_signals.append(
                {
                    "code": "pm.deterministic_contracts_enabled",
                    "severity": "info",
                    "detail": "PM planning was started with deterministic contract metadata.",
                    "factory_recovery": str(pm_run_metadata.get("factory_recovery") or ""),
                    "factory_bench_project_id": str(pm_run_metadata.get("factory_bench_project_id") or ""),
                }
            )
        if str(final_result.status or "").strip().lower() == "timeout" and not self._artifact_exists(
            "tasks/plan.json", min_chars=1
        ):
            recovery_result = await self._run_pm_planning_deterministic_recovery(
                service=service,
                planning_directive=planning_directive,
                context=context,
                abort_checker=abort_checker,
                authority_port=authority_port,
            )
            if recovery_result.status in {"completed", "success"} or self._artifact_exists(
                "tasks/plan.json", min_chars=1
            ):
                stage_signals.append(
                    {
                        "code": "pm.timeout_recovered_by_deterministic_contracts",
                        "severity": "warning",
                        "detail": str(final_result.message or "").strip() or "PM LLM planning timed out",
                        "recovery_status": str(recovery_result.status or "").strip(),
                    }
                )
                final_result = recovery_result

        if final_result.status not in {"completed", "success"}:
            stage_signals.append(
                {
                    "code": "pm.run_status_non_success",
                    "severity": "error",
                    "detail": str(final_result.message or "").strip() or str(final_result.status or "unknown"),
                    "upstream_status": str(final_result.status or "").strip(),
                }
            )
        synced_plan_source = self._ensure_pm_plan_contract_available()
        if synced_plan_source:
            stage_signals.append(
                {
                    "code": "pm.plan_contract_synced_from_workspace_mirror",
                    "severity": "info",
                    "detail": "Copied PM workspace plan mirror into runtime tasks/plan.json for downstream stages.",
                    "source_path": synced_plan_source,
                }
            )
        enrichment_summary = self._enrich_pm_plan_contract_artifact("tasks/plan.json")
        if int(enrichment_summary.get("task_count") or 0) > 0:
            stage_signals.append(
                {
                    "code": "pm.plan_contract_enriched_with_catalog_depth_and_declared_targets",
                    "severity": "info",
                    "detail": (
                        "Merged catalog delivery depth contract and project declared target union into PM task contracts."
                    ),
                    **enrichment_summary,
                }
            )
        normalization_summary = self._persist_normalized_pm_plan_validation_contracts("tasks/plan.json")
        if int(normalization_summary.get("task_count") or 0) > 0:
            stage_signals.append(
                {
                    "code": "pm.plan_validation_contracts_persisted",
                    "severity": "info",
                    "detail": ("Persisted the exact PM validation contracts consumed by Chief Engineer provenance."),
                    **normalization_summary,
                }
            )
        contract_issue = self._validate_pm_plan_contract("tasks/plan.json")
        if contract_issue:
            stage_signals.append(
                {
                    "code": "pm.contract_issue_detected",
                    "severity": "error",
                    "detail": contract_issue,
                }
            )
        if not contract_issue:
            language_issue = self._validate_pm_plan_language_consistency("tasks/plan.json")
            if language_issue:
                contract_issue = language_issue
                stage_signals.append(
                    {
                        "code": "pm.language_mismatch_detected",
                        "severity": "error",
                        "detail": language_issue,
                    }
                )
        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        if not contract_issue and pm_tasks:
            materialize_summary = self._materialize_pm_plan_taskboard(
                pm_tasks,
                run_id=run.id,
                source_stage="pm_planning",
                run_metadata=run.metadata,
            )
            binding_failures = list(materialize_summary.get("binding_failures") or [])
            if binding_failures:
                contract_issue = "TaskRuntime rejected one or more Factory run bindings"
            stage_signals.append(
                {
                    "code": (
                        "pm.task_runtime_factory_binding_failed"
                        if binding_failures
                        else "pm.taskboard_materialized_from_plan"
                    ),
                    "severity": "error" if binding_failures else "info",
                    "detail": (
                        contract_issue
                        if binding_failures
                        else "Materialized PM plan tasks into canonical TaskBoard for Director claim enforcement."
                    ),
                    **materialize_summary,
                }
            )
        artifacts: list[str] = []
        if self._artifact_exists("tasks/plan.json", min_chars=1):
            artifacts.append("tasks/plan.json")
            self._mirror_pm_plan_artifacts(run.id, artifacts)
        if stage_signals:
            artifacts.append(
                self._write_stage_signal_artifact(
                    stage="pm_planning",
                    run_id=run.id,
                    signals=stage_signals,
                )
            )
        stage_status = "success"
        if final_result.status not in {"completed", "success"} or bool(contract_issue):
            stage_status = "failed"
        error_code = ""
        root_cause_hint = ""
        if stage_status == "failed":
            for signal in stage_signals:
                if not isinstance(signal, dict):
                    continue
                if str(signal.get("severity") or "").strip().lower() != "error":
                    continue
                error_code = str(signal.get("code") or "").strip()
                root_cause_hint = str(signal.get("detail") or "").strip()
                if error_code:
                    break
        return StageResult(
            stage="pm_planning",
            status=stage_status,
            output=(
                f"PM planning {final_result.status}: {final_result.message or 'N/A'}; "
                f"signals={len(stage_signals)}; "
                f"error_code={error_code or 'none'}; root_cause_hint={root_cause_hint or 'none'}"
            ),
            artifacts=artifacts,
        )

    async def _run_pm_planning_deterministic_recovery(
        self,
        *,
        service: Any,
        planning_directive: str,
        context: dict[str, Any],
        abort_checker: Callable[[], Awaitable[str | None]] | None,
        authority_port: FactoryRoleEvidenceAuthorityPort,
    ) -> CommandResult:
        recovery_timeout = int(context.get("pm_recovery_timeout", 120))
        command_result = cast(
            CommandResult,
            await self._call_with_factory_role_evidence_authority(
                authority_port,
                "pm",
                lambda: service.execute_pm_run(
                    workspace=str(self.workspace),
                    run_type="pm",
                    options={
                        "directive": planning_directive,
                        "run_director": False,
                        "metadata": {
                            "deterministic_pm_contracts": True,
                            "factory_recovery": "pm_timeout_without_plan",
                            "timeout_seconds": recovery_timeout,
                        },
                    },
                ),
            ),
        )
        return await self._wait_run_completion(
            service,
            command_result,
            timeout_seconds=recovery_timeout,
            cancel_event=self._resolve_cancel_event(context),
            abort_checker=abort_checker,
        )

    @staticmethod
    def _ce_extract_llm_evidence(ce_result: Any, *, task_id: str, run_id: str) -> dict[str, Any]:
        return ce_evidence.ce_extract_llm_evidence(ce_result, task_id=task_id, run_id=run_id)

    @staticmethod
    def _ce_prompt_profile_identity(ce_result: Any) -> dict[str, str]:
        return ce_evidence.ce_prompt_profile_identity(ce_result)

    @staticmethod
    def _ce_review_schema_failure_is_recoverable(ce_result: Any, *, raw_output: str) -> bool:
        return ce_evidence.ce_review_schema_failure_is_recoverable(ce_result, raw_output=raw_output)

    @staticmethod
    def _ce_portfolio_result_allows_schema_repair(ce_result: Any) -> bool:
        """Whether one failed CE portfolio result may consume the single repair."""

        return ce_evidence.ce_portfolio_result_allows_schema_repair(ce_result)

    @staticmethod
    def _ce_schema_repair_failure_class(ce_result: Any) -> str:
        return ce_evidence.ce_schema_repair_failure_class(ce_result)

    @staticmethod
    def _chief_engineer_authoritative_pm_projection_candidate() -> dict[str, Any]:
        return ce_evidence.chief_engineer_authoritative_pm_projection_candidate()

    @staticmethod
    def _attach_ce_llm_evidence(signal: dict[str, Any], evidence: dict[str, Any]) -> None:
        ce_evidence.attach_ce_llm_evidence(signal, evidence)

    @staticmethod
    def _ce_missing_final_request_evidence(evidence: dict[str, Any]) -> list[str]:
        return ce_evidence.ce_missing_final_request_evidence(evidence)

    @staticmethod
    def _architecture_decision_payloads(values: Any) -> list[dict[str, Any]]:
        return ce_evidence.architecture_decision_payloads(values)

    def _ensure_chief_engineer_blueprint_artifact_present(
        self,
        *,
        result: Any,
        task: dict[str, Any],
        task_context: dict[str, Any],
        constraints: dict[str, Any],
        run_id: str,
    ) -> bool:
        blueprint_path = str(getattr(result, "blueprint_path", "") or "").strip()
        if not blueprint_path or self._artifact_exists(blueprint_path, min_chars=2):
            return False

        now = datetime.now(timezone.utc).isoformat()
        blueprint_id = str(getattr(result, "blueprint_id", "") or Path(blueprint_path).stem).strip()
        payload = {
            "schema_version": "chief_engineer.blueprint.v1",
            "role": "chief_engineer",
            "blueprint_id": blueprint_id,
            "task_id": str(getattr(result, "task_id", "") or self._task_id(task, 0)).strip(),
            "run_id": str(run_id or "").strip(),
            "title": self._task_string(task, "title", "subject", "goal"),
            "objective": str(getattr(result, "objective", "") or "").strip() or self._task_objective(task),
            "summary": str(getattr(result, "summary", "") or "").strip(),
            "status": str(getattr(result, "status", "") or "generated").strip(),
            "source": "factory_stage_executor.ce_result_artifact_repair",
            "target_files": list(getattr(result, "target_files", ()) or []),
            "scope_paths": list(getattr(result, "scope_paths", ()) or []),
            "acceptance_criteria": list(getattr(result, "acceptance_criteria", ()) or []),
            "execution_checklist": list(getattr(result, "execution_checklist", ()) or []),
            "dependencies": list(getattr(result, "dependencies", ()) or []),
            "architecture_decisions": self._architecture_decision_payloads(
                getattr(result, "architecture_decisions", ())
            ),
            "selected_libraries": list(getattr(result, "selected_libraries", ()) or []),
            "constraints": dict(constraints),
            "context": dict(task_context),
            "pm_task": dict(task),
            "contract_completeness": {
                "reconstructed_from_result": True,
                "physical_artifact_missing_before_repair": True,
            },
            "handoff_ready": True,
            "recommendations": list(getattr(result, "recommendations", ()) or []),
            "risks": list(getattr(result, "risks", ()) or []),
            "created_at": now,
            "updated_at": now,
            "blueprint_hash": str(getattr(result, "blueprint_hash", "") or "").strip(),
        }
        self._write_json_artifact(blueprint_path, payload)
        return True

    def _chief_engineer_portfolio_tasks(
        self,
        pm_tasks: list[dict[str, Any]],
    ) -> tuple[ChiefEngineerPortfolioTaskV1, ...]:
        """Project validated PM facts into the CE portfolio contract."""

        portfolio_tasks: list[ChiefEngineerPortfolioTaskV1] = []
        for index, task in enumerate(pm_tasks, start=1):
            target_files = tuple(self._task_string_list(task, "target_files"))
            target_file_set = set(target_files)
            scope_paths = tuple(self._task_string_list(task, "scope_paths")) or target_files
            entrypoint_targets = tuple(
                path
                for path in self._task_string_list(task, "project_declared_entrypoint_targets")
                if path in target_file_set
            )
            raw_metadata = task.get("metadata")
            metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
            raw_topology_authority = str(metadata.get("topology_authority") or "pm").strip()
            if raw_topology_authority not in {"pm", "chief_engineer"}:
                raise _ChiefEngineerPortfolioAuthorityError(
                    "chief_engineer.topology_authority_invalid",
                    f"committed PM task {self._task_id(task, index)!r} has invalid topology authority",
                )
            topology_authority: Literal["pm", "chief_engineer"] = (
                "chief_engineer" if raw_topology_authority == "chief_engineer" else "pm"
            )
            required_source_kinds = (
                tuple(str(value).strip() for value in metadata.get("required_source_kinds", ()) if str(value).strip())
                if isinstance(metadata.get("required_source_kinds"), (list, tuple))
                else ()
            )
            raw_depth_contract = task.get("delivery_depth_contract") or metadata.get("delivery_depth_contract")
            delivery_depth_contract = dict(raw_depth_contract) if isinstance(raw_depth_contract, Mapping) else {}
            primary_language = (
                str(task.get("language") or delivery_depth_contract.get("language") or "").strip().lower()
            )
            allowed_source_suffixes = chief_engineer_source_suffixes_for_language(primary_language)
            if topology_authority == "chief_engineer" and not allowed_source_suffixes:
                raise _ChiefEngineerPortfolioAuthorityError(
                    "chief_engineer.topology_language_authority_missing",
                    f"committed PM task {self._task_id(task, index)!r} has no supported source suffix authority",
                )
            entrypoint_command = ""
            raw_verification_commands = task.get("verification_commands")
            if isinstance(raw_verification_commands, list):
                for raw_command in raw_verification_commands:
                    if not isinstance(raw_command, Mapping):
                        continue
                    if str(raw_command.get("modality") or "").strip() != "entrypoint":
                        continue
                    argv = raw_command.get("argv")
                    if isinstance(argv, list) and argv and all(str(item).strip() for item in argv):
                        entrypoint_command = shlex.join(str(item) for item in argv)
                        break
            entrypoint_kind_authority = ""
            if entrypoint_command:
                project_type = str(delivery_depth_contract.get("project_type") or "").strip().lower()
                project_kind = (
                    "library"
                    if project_type in {"library", "package", "sdk", "crate"}
                    or project_type.endswith(("_library", "_package", "_sdk", "_crate"))
                    else "application"
                )
                classification_path = entrypoint_targets[0] if entrypoint_targets else target_files[0]
                entrypoint_kind_authority = classify_chief_engineer_pm_entrypoint_kind(
                    path=classification_path,
                    command=entrypoint_command,
                    project_kind=project_kind,
                    catalog_snapshot={"project_type": project_type},
                )
            portfolio_tasks.append(
                ChiefEngineerPortfolioTaskV1(
                    task_id=self._task_id(task, index),
                    objective=self._task_objective(task),
                    target_files=target_files,
                    scope_paths=scope_paths,
                    dependencies=tuple(self._task_string_list(task, "depends_on", "dependencies")),
                    entrypoint_targets=entrypoint_targets,
                    topology_authority=topology_authority,
                    required_source_kinds=required_source_kinds,
                    primary_language=primary_language,
                    allowed_source_suffixes=allowed_source_suffixes,
                    entrypoint_kind_authority=entrypoint_kind_authority,
                    delivery_depth_contract=delivery_depth_contract,
                )
            )
        return tuple(portfolio_tasks)

    def _chief_engineer_portfolio_context(
        self,
        pm_tasks: list[dict[str, Any]],
        *,
        run_id: str,
        failure_feedback: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build one structured final-request evidence payload for all PM tasks.

        The PM contracts remain authoritative.  This projection only gives one
        CE call enough product intent, task topology, and file ownership context
        to design interfaces consistently across task boundaries.

        Complexity:
            O(T + F) time and space for ``T`` tasks and ``F`` declared paths.
        """

        task_rows: list[dict[str, Any]] = []
        target_files: list[str] = []
        scope_paths: list[str] = []
        seen_targets: set[str] = set()
        seen_scope: set[str] = set()
        for index, task in enumerate(pm_tasks, start=1):
            task_context = self._task_blueprint_context(task, run_id=run_id, index=index)
            task_targets = self._task_string_list(task, "target_files")
            task_scope = self._task_string_list(task, "scope_paths") or task_targets
            for path in task_targets:
                if path not in seen_targets:
                    seen_targets.add(path)
                    target_files.append(path)
            for path in task_scope:
                if path not in seen_scope:
                    seen_scope.add(path)
                    scope_paths.append(path)
            task_rows.append(
                {
                    "task_id": self._task_id(task, index),
                    "title": self._task_string(task, "title", "subject", "goal"),
                    "objective": self._task_objective(task),
                    "target_files": task_targets,
                    "scope_paths": task_scope,
                    "depends_on": self._task_string_list(task, "depends_on", "dependencies"),
                    "project_declared_entrypoint_targets": self._task_string_list(
                        task,
                        "project_declared_entrypoint_targets",
                    ),
                    "acceptance_criteria": self._task_string_list(
                        task,
                        "acceptance",
                        "acceptance_criteria",
                    ),
                    "execution_checklist": self._task_string_list(task, "steps", "execution_checklist"),
                    "delivery_plan_document": task_context.get("delivery_plan_document", {}),
                    "delivery_depth_contract": task_context.get("delivery_depth_contract", {}),
                    "behavior_contract": task_context.get("behavior_contract", {}),
                    "existing_target_files": task_context.get("existing_target_files", []),
                }
            )

        pm_contract_set = {
            "schema_version": "polaris.validated_pm_contract_set.v1",
            "source_artifact": "tasks/plan.json",
            "tasks": [dict(task) for task in pm_tasks],
        }
        context = {
            "factory_run_id": run_id,
            "source_artifact": "tasks/plan.json",
            "pm_task_contract": pm_contract_set,
            "pm_task_contracts": [dict(task) for task in pm_tasks],
            "portfolio_tasks": task_rows,
            "project_task_graph": [
                {
                    "task_id": row["task_id"],
                    "depends_on": list(row["depends_on"]),
                    "target_files": list(row["target_files"]),
                }
                for row in task_rows
            ],
            "target_files": target_files,
            "scope_paths": scope_paths,
            "task_count": len(task_rows),
        }
        if failure_feedback:
            context["failure_feedback"] = deepcopy(dict(failure_feedback))
            context["chief_engineer_local_rework"] = True
        return context

    def _chief_engineer_portfolio_objective(self, pm_tasks: list[dict[str, Any]]) -> str:
        """Return a natural-language project design objective for one CE call."""

        task_lines = [
            f"- {self._task_id(task, index)}: {self._task_objective(task)}"
            for index, task in enumerate(pm_tasks, start=1)
        ]
        depth_minimums: dict[str, int] = {}
        for task in pm_tasks:
            raw_metadata = task.get("metadata")
            metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
            raw_contract = task.get("delivery_depth_contract") or metadata.get("delivery_depth_contract")
            contract = raw_contract if isinstance(raw_contract, Mapping) else {}
            candidate_minimums = contract.get("minimums")
            minimums = candidate_minimums if isinstance(candidate_minimums, Mapping) else {}
            for key in ("min_prod_files", "min_test_files"):
                try:
                    value = max(0, int(minimums.get(key) or 0))
                except (TypeError, ValueError):
                    continue
                depth_minimums[key] = max(depth_minimums.get(key, 0), value)
        depth_clause = ""
        if depth_minimums:
            depth_clause = (
                "\n\nAuthoritative portfolio depth minimums: "
                + json.dumps(depth_minimums, ensure_ascii=False, sort_keys=True)
                + ". The artifact obligation set itself must satisfy these counts before Director dispatch."
            )
        return (
            "Produce one coherent Chief Engineer project blueprint portfolio for the validated PM task graph. "
            "Define shared module boundaries and cross-file interfaces before projecting concrete plans for every "
            "task. Preserve PM target/scope authority and make each task independently executable by Director.\n\n"
            "Validated PM tasks:\n" + "\n".join(task_lines) + depth_clause + _CE_BLUEPRINT_OUTPUT_CONTRACT
        )

    def _chief_engineer_project_kind_authority(
        self,
        *,
        project_id: str,
        run_id: str,
        pm_contract_hash: str,
        catalog_snapshot: Mapping[str, Any],
        catalog_snapshot_hash: str,
    ) -> ProjectKindAuthorityV1:
        """Mirror the CE owner derivation for provider context; CE revalidates it."""

        try:
            return derive_project_kind_authority_from_catalog_snapshot(
                project_id=project_id,
                run_id=run_id,
                pm_contract_hash=pm_contract_hash,
                catalog_snapshot=catalog_snapshot,
                catalog_snapshot_hash=catalog_snapshot_hash,
            )
        except (TypeError, ValueError) as exc:
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_project_kind_authority_invalid",
                str(exc),
            ) from exc

    def _chief_engineer_catalog_snapshot(self) -> dict[str, Any]:
        """Capture the exact platform catalog after PM artifact revalidation."""

        catalog_path = self.workspace / ".polaris" / "catalog_contract.json"
        if not catalog_path.exists():
            return {}
        try:
            payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_catalog_snapshot_invalid",
                "catalog_contract.json is unreadable or invalid JSON",
            ) from exc
        if type(payload) is not dict:
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_catalog_snapshot_invalid",
                "catalog_contract.json must be an exact JSON object",
            )
        return payload

    def _chief_engineer_verification_command_authority(
        self,
        committed_pm_tasks: list[dict[str, Any]],
    ) -> tuple[VerificationCommandAuthorityV1, ...]:
        """Read exact structured verifier argv/cwd authority from the committed PM document.

        Natural-language acceptance criteria and generic verifier-policy modalities are deliberately
        not command authority.  Missing or malformed structured rows fail before any CE provider call.
        """

        authorities: list[VerificationCommandAuthorityV1] = []
        seen_hashes: set[str] = set()
        for index, task in enumerate(committed_pm_tasks, start=1):
            task_id = self._task_id(task, index)
            if "verification_commands" not in task:
                raise _ChiefEngineerPortfolioAuthorityError(
                    "chief_engineer.project_completion_verification_command_authority_missing",
                    f"committed PM task {task_id!r} is missing verification_commands",
                )
            rows = task.get("verification_commands")
            if type(rows) is not list:
                raise _ChiefEngineerPortfolioAuthorityError(
                    "chief_engineer.project_completion_verification_command_authority_invalid",
                    f"committed PM task {task_id!r} verification_commands must be a JSON array",
                )
            for row_index, raw_row in enumerate(rows):
                if not isinstance(raw_row, Mapping) or set(raw_row) != {"modality", "argv", "cwd"}:
                    raise _ChiefEngineerPortfolioAuthorityError(
                        "chief_engineer.project_completion_verification_command_authority_invalid",
                        f"committed PM task {task_id!r} verification_commands[{row_index}] "
                        "must contain exactly modality, argv, cwd",
                    )
                try:
                    authority = VerificationCommandAuthorityV1(
                        task_id=task_id,
                        modality=raw_row["modality"],
                        argv=raw_row["argv"],
                        cwd=raw_row["cwd"],
                    )
                except (TypeError, ValueError) as exc:
                    raise _ChiefEngineerPortfolioAuthorityError(
                        "chief_engineer.project_completion_verification_command_authority_invalid",
                        f"committed PM task {task_id!r} verification_commands[{row_index}] is invalid: {exc}",
                    ) from exc
                if authority.authority_hash in seen_hashes:
                    continue
                seen_hashes.add(authority.authority_hash)
                authorities.append(authority)
        if not authorities:
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_verification_command_authority_missing",
                "committed PM task set contains no structured verification command authority",
            )
        if not any(authority.modality in {"build", "test", "lint"} for authority in authorities):
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_delivery_verifier_authority_missing",
                "committed PM task set requires at least one build/test/lint command authority",
            )
        return tuple(sorted(authorities, key=lambda item: item.authority_hash))

    async def _load_chief_engineer_portfolio_authority(
        self,
        *,
        run: FactoryRun,
        pm_tasks: list[dict[str, Any]],
        portfolio_tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
    ) -> _ChiefEngineerPortfolioAuthorityV1:
        """Bind CE completion authority to committed PM and verifier-policy evidence."""

        catalog_snapshot = self._chief_engineer_catalog_snapshot()
        catalog_project_id = str(catalog_snapshot.get("project_id") or "").strip()
        # ``FactoryConfig.name`` is a human-facing run label (the HTTP/bench
        # caller commonly sets it to ``Factory Run - pm``), not project
        # identity authority.  Prefer the catalog identity captured after PM
        # artifact revalidation; retain the display-name fallback only for
        # legacy/non-catalog workspaces.
        project_id = catalog_project_id or str(run.config.name)
        if not project_id or project_id != project_id.strip():
            raise RuntimeError("chief_engineer_project_completion_project_id_missing")
        if any(unicodedata.category(character).startswith("C") for character in project_id):
            raise RuntimeError("chief_engineer_project_completion_project_id_invalid")
        if len(project_id.encode("utf-8")) > 128:
            raise RuntimeError("chief_engineer_project_completion_project_id_invalid")
        expected_task_ids = tuple(sorted(task.task_id for task in portfolio_tasks))
        if not expected_task_ids:
            raise RuntimeError("chief_engineer_project_completion_task_set_missing")

        runtime_root = Path(resolve_storage_roots(str(self.workspace)).runtime_root)
        factory_store = pkg().FactoryStore(runtime_root / "factory", create_root=False)
        events = await factory_store.get_authoritative_events(run.id)
        persistence = pkg().reduce_factory_stage_persistence(events, factory_run_id=run.id)
        pm_commits = tuple(commit for commit in persistence.commits if commit.stage == "pm_planning")
        if not pm_commits:
            raise RuntimeError("chief_engineer_project_completion_pm_commit_missing")
        pm_commit = pm_commits[-1]
        pm_stage_event = next(
            (
                event
                for event in events
                if event.get("type") == "stage_completed"
                and event.get("event_id") == pm_commit.stage_completed_event_id
            ),
            None,
        )
        if pm_stage_event is None:
            raise RuntimeError("chief_engineer_project_completion_pm_stage_event_missing")
        proof = pkg().revalidate_pm_stage_artifact_binding(
            factory_store=factory_store,
            factory_run_id=run.id,
            stage_event=pm_stage_event,
        )
        committed_pm_tasks_raw = proof.document.get("tasks")
        if type(committed_pm_tasks_raw) is not list or committed_pm_tasks_raw != pm_tasks:
            raise RuntimeError("chief_engineer_project_completion_pm_document_mismatch")
        committed_pm_tasks = cast(list[dict[str, Any]], committed_pm_tasks_raw)
        committed_portfolio_tasks = self._chief_engineer_portfolio_tasks(committed_pm_tasks)
        if committed_portfolio_tasks != portfolio_tasks:
            raise RuntimeError("chief_engineer_project_completion_pm_path_authority_mismatch")
        if proof.task_ids != expected_task_ids:
            raise RuntimeError(
                "chief_engineer_project_completion_pm_task_set_mismatch:"
                f"expected={list(expected_task_ids)}:actual={list(proof.task_ids)}"
            )

        target_files = tuple(path for task in portfolio_tasks for path in task.target_files)
        acceptance_criteria = tuple(
            criterion
            for task in committed_pm_tasks
            for criterion in self._task_string_list(task, "acceptance", "acceptance_criteria")
        )
        verification_command_authority = self._chief_engineer_verification_command_authority(committed_pm_tasks)
        try:
            catalog_snapshot_hash = project_completion_catalog_snapshot_hash(catalog_snapshot)
        except (TypeError, ValueError) as exc:
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_catalog_snapshot_invalid",
                str(exc),
            ) from exc
        project_kind_authority = self._chief_engineer_project_kind_authority(
            project_id=project_id,
            run_id=run.id,
            pm_contract_hash=proof.item.canonical_json_sha256,
            catalog_snapshot=catalog_snapshot,
            catalog_snapshot_hash=catalog_snapshot_hash,
        )
        policy = dict(
            pkg()
            .compile_evidence_policy(
                CompileEvidencePolicyCommandV1(
                    workspace=str(self.workspace),
                    task_id=f"CE-PORTFOLIO-{run.id}",
                    run_id=run.id,
                    target_files=target_files,
                    acceptance_criteria=acceptance_criteria,
                    explicit_required_modalities=("command",),
                )
            )
            .policy
        )
        verifier_policy_hash = str(policy.get("policy_hash") or "")
        if re.fullmatch(r"[0-9a-f]{64}", verifier_policy_hash) is None:
            raise RuntimeError("chief_engineer_project_completion_verifier_policy_hash_invalid")
        verifier_policy_snapshot_hash = project_completion_verifier_policy_snapshot_hash(policy)
        return _ChiefEngineerPortfolioAuthorityV1(
            project_id=project_id,
            pm_stage_event_id=str(pm_commit.stage_completed_event_id),
            pm_contract_hash=proof.item.canonical_json_sha256,
            pm_task_ids=proof.task_ids,
            catalog_snapshot=catalog_snapshot,
            catalog_snapshot_hash=catalog_snapshot_hash,
            project_kind_authority=project_kind_authority,
            verifier_policy_hash=verifier_policy_hash,
            verifier_policy=policy,
            verifier_policy_snapshot_hash=verifier_policy_snapshot_hash,
            verification_command_authority=verification_command_authority,
        )

    @staticmethod
    def _chief_engineer_structured_output_contract(
        portfolio_task_ids: tuple[str, ...],
    ) -> RoleStructuredOutputContractV1:
        """Build the caller-owned provider schema for one CE portfolio."""

        task_plan_properties = {
            task_id: {
                "type": "object",
                "minProperties": 1,
                "properties": {
                    "behavior_invariant_refs": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "scope_for_apply": {"type": "array", "items": {}},
                    "risk_flags": {"type": "array", "items": {}},
                },
                "required": ["behavior_invariant_refs"],
                "additionalProperties": True,
            }
            for task_id in portfolio_task_ids
        }
        nullable_string = {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}
        completion_contract_schema = {
            "type": "object",
            "properties": {
                "obligations": {
                    "type": "object",
                    "properties": {
                        "artifacts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "obligation_id": {"type": "string", "minLength": 1},
                                    "path": {"type": "string", "minLength": 1},
                                    "semantic_role": {
                                        "type": "string",
                                        "enum": [
                                            "source",
                                            "test",
                                            "manifest",
                                            "config",
                                            "docs",
                                            "entrypoint",
                                            "assets",
                                        ],
                                    },
                                    "applicability": {
                                        "type": "string",
                                        "enum": ["required", "optional", "not_applicable"],
                                    },
                                    "owner_task_id": nullable_string,
                                },
                                "required": [
                                    "obligation_id",
                                    "path",
                                    "semantic_role",
                                    "applicability",
                                    "owner_task_id",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "entrypoints": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "obligation_id": {"type": "string", "minLength": 1},
                                    "kind": {
                                        "type": "string",
                                        "enum": ["cli", "web", "api", "library"],
                                    },
                                    "applicability": {
                                        "type": "string",
                                        "enum": ["required", "optional", "not_applicable"],
                                    },
                                    "owner_task_id": nullable_string,
                                    "source_path": nullable_string,
                                    "runtime_path": nullable_string,
                                    "command": nullable_string,
                                },
                                "required": [
                                    "obligation_id",
                                    "kind",
                                    "applicability",
                                    "owner_task_id",
                                    "source_path",
                                    "runtime_path",
                                    "command",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "verification": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "obligation_id": {"type": "string", "minLength": 1},
                                    "modality": {
                                        "type": "string",
                                        "enum": ["environment_prep", "build", "test", "lint", "entrypoint"],
                                    },
                                    "command_authority_hash": nullable_string,
                                    "applicability": {
                                        "type": "string",
                                        "enum": ["required", "optional", "not_applicable"],
                                    },
                                    "owner_task_id": nullable_string,
                                    "covers_obligation_ids": {
                                        "type": "array",
                                        "items": {"type": "string", "minLength": 1},
                                    },
                                },
                                "required": [
                                    "obligation_id",
                                    "modality",
                                    "command_authority_hash",
                                    "applicability",
                                    "owner_task_id",
                                    "covers_obligation_ids",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["artifacts", "entrypoints", "verification"],
                    "additionalProperties": False,
                },
            },
            "required": ["obligations"],
            "additionalProperties": False,
        }
        return RoleStructuredOutputContractV1(
            schema_name="chief_engineer_blueprint_portfolio",
            description=(
                "Submit the complete Chief Engineer portfolio for every validated PM task id, "
                "including shared interface and cross-task behavior contracts. When source and tests have "
                "different task owners, define concrete sign/unit/boundary/order/rounding semantics as needed, "
                "bind owner and consumer tasks, cover their completion obligations, and include given/when/then examples."
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "construction_plan": {
                        "type": "object",
                        "properties": {
                            "task_plans": {
                                "type": "object",
                                "properties": task_plan_properties,
                                "additionalProperties": False,
                            },
                            "project_interface_contract": {
                                "type": "object",
                                "properties": {
                                    "provider_declarations": {
                                        "type": "array",
                                        "items": {"type": "object"},
                                    },
                                    "consumer_declarations": {
                                        "type": "array",
                                        "items": {"type": "object"},
                                    },
                                },
                                "additionalProperties": False,
                            },
                            "shared_behavior_contract": {
                                "type": "object",
                                "properties": {
                                    "invariants": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "invariant_id": {"type": "string", "minLength": 1},
                                                "statement": {"type": "string", "minLength": 1},
                                                "owner_task_id": {"type": "string", "minLength": 1},
                                                "consumer_task_ids": {
                                                    "type": "array",
                                                    "items": {"type": "string", "minLength": 1},
                                                },
                                                "covered_obligation_ids": {
                                                    "type": "array",
                                                    "minItems": 1,
                                                    "items": {"type": "string", "minLength": 1},
                                                },
                                                "verification_examples": {
                                                    "type": "array",
                                                    "minItems": 1,
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "given": {"type": "string", "minLength": 1},
                                                            "when": {"type": "string", "minLength": 1},
                                                            "then": {"type": "string", "minLength": 1},
                                                        },
                                                        "required": ["given", "when", "then"],
                                                        "additionalProperties": False,
                                                    },
                                                },
                                            },
                                            "required": [
                                                "invariant_id",
                                                "statement",
                                                "owner_task_id",
                                                "consumer_task_ids",
                                                "covered_obligation_ids",
                                                "verification_examples",
                                            ],
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "required": ["invariants"],
                                "additionalProperties": False,
                            },
                        },
                        "required": [
                            "task_plans",
                            "project_interface_contract",
                            "shared_behavior_contract",
                        ],
                        "additionalProperties": True,
                    },
                    "scope_for_apply": {"type": "array", "items": {}},
                    "risk_flags": {"type": "array", "items": {}},
                    "project_completion_contract": completion_contract_schema,
                },
                "required": [
                    "construction_plan",
                    "project_completion_contract",
                    "risk_flags",
                ],
                "additionalProperties": False,
            },
        )

    def _claim_chief_engineer_execution_attempt(
        self,
        *,
        run_id: str,
        portfolio_task_id: str,
        objective: str,
        lease_budget: _ChiefEngineerExecutionAttemptLeaseBudget,
    ) -> tuple[int, TaskRuntimeExecutionAttemptIdentityV1]:
        """Claim TaskRuntime's durable owner for one CE portfolio execution.

        TaskRuntime is the sole source of execution-attempt identity. Replaying
        an active claim renews its persisted session, while a requeued claim
        receives a new session from TaskRuntime. Factory neither derives an
        identity from run/task identifiers nor generates UUIDs. The lease is
        derived only from the already-admitted CE execution timeout.
        """

        task_runtime = pkg().TaskRuntimeService(str(self.workspace))
        primary_portfolio_task_id = f"CE-PORTFOLIO-{run_id}"
        subject = (
            "Chief Engineer portfolio review"
            if portfolio_task_id == primary_portfolio_task_id
            else f"Chief Engineer portfolio repair [{portfolio_task_id}]"
        )
        row = task_runtime.ensure_task_row(
            external_task_id=portfolio_task_id,
            subject=subject,
            description=objective,
            metadata={
                "factory_run_id": run_id,
                "factory_stage": "chief_engineer_review",
                "role": "chief_engineer",
                "execution_identity_required": True,
            },
        )
        task_row_id = task_runtime.normalize_task_id(row.get("id"))
        if task_row_id is None:
            raise RuntimeError("chief_engineer_execution_attempt_task_id_invalid")

        binding = bind_runtime_task_to_factory_run(
            BindRuntimeTaskToFactoryRunCommandV1(
                workspace=str(self.workspace),
                task_id=portfolio_task_id,
                factory_run_id=run_id,
            )
        )
        if not binding.ok:
            raise RuntimeError(f"chief_engineer_execution_attempt_binding_failed:{binding.code}")

        claim = task_runtime.claim_execution(
            task_row_id,
            worker_id="chief_engineer",
            role_id="chief_engineer",
            run_id=run_id,
            lease_ttl_seconds=lease_budget.lease_ttl_seconds,
            selection_source="factory_stage_executor.chief_engineer_portfolio_review",
            external_task_id=portfolio_task_id,
            context_summary=objective,
            metadata={
                "factory_run_id": run_id,
                "factory_stage": "chief_engineer_review",
                "execution_identity_required": True,
            },
        )
        session = claim.get("session") if isinstance(claim, dict) else None
        attempt_record = claim.get("execution_attempt") if isinstance(claim, dict) else None
        if (
            not isinstance(session, Mapping)
            or not isinstance(attempt_record, Mapping)
            or not bool(claim.get("success"))
        ):
            reason = str(claim.get("reason") or "unknown") if isinstance(claim, dict) else "invalid_claim_result"
            raise RuntimeError(f"chief_engineer_execution_attempt_claim_failed:{reason}")
        try:
            execution_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"chief_engineer_execution_attempt_record_invalid:{type(exc).__name__}:{exc}") from exc
        if (
            execution_attempt.workspace != str(self.workspace)
            or execution_attempt.task_id != task_row_id
            or execution_attempt.external_task_id != portfolio_task_id
            or execution_attempt.role_id != "chief_engineer"
            or execution_attempt.run_id != run_id
            or execution_attempt.session_id != str(session.get("session_id") or "").strip()
            or execution_attempt.attempt != session.get("attempt")
        ):
            raise RuntimeError("chief_engineer_execution_attempt_session_mismatch")
        return task_row_id, execution_attempt

    def _settle_chief_engineer_execution_attempt(
        self,
        *,
        task_id: int,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        stage_status: str,
        summary: str,
    ) -> None:
        """Complete successful CE work or suspend it for a new TaskRuntime claim."""

        task_runtime = pkg().TaskRuntimeService(str(self.workspace))
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1 = (
            "completed" if stage_status == "success" else "suspended"
        )
        if task_id != execution_attempt.task_id:
            raise RuntimeError("chief_engineer_execution_attempt_task_id_mismatch")
        result = task_runtime.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=execution_attempt.workspace,
                identity=execution_attempt,
                outcome=outcome,
                summary=summary,
                lock_timeout_seconds=5.0,
                metadata={"factory_stage": "chief_engineer_review"},
            )
        )
        if not bool(result.get("success")):
            reason = str(result.get("reason") or "unknown")
            raise RuntimeError(f"chief_engineer_execution_attempt_settlement_failed:{reason}")

    @staticmethod
    def _chief_engineer_portfolio_output_errors(
        payload: Mapping[str, Any],
        *,
        task_ids: tuple[str, ...] | None = None,
        tasks: tuple[ChiefEngineerPortfolioTaskV1, ...] = (),
    ) -> list[str]:
        """Validate the nested project-level CE output contract."""

        feasibility = (
            project_chief_engineer_portfolio_delivery_depth_feasibility(
                payload,
                tasks=tasks,
            )
            if tasks
            else None
        )
        errors = ce_evidence.chief_engineer_portfolio_output_errors(
            payload,
            task_ids=tuple(task.task_id for task in tasks) or tuple(task_ids or ()),
            authorized_artifact_obligation_ids=(
                frozenset(feasibility["authorized_artifact_obligation_ids"])
                if feasibility is not None
                else None
            ),
        )
        errors.extend(project_chief_engineer_completion_contract_semantic_errors(payload))
        depth_compatible_errors = tuple(
            error
            for error in errors
            if (
                ".covered_obligation_ids reference unknown completion obligations:" in error
                or "production-and-test obligation coverage" in error
                or "entrypoint" in error
            )
        )
        if len(depth_compatible_errors) != len(errors) or feasibility is None:
            return errors
        for deficit in feasibility["deficits"]:
            errors.append(
                "project_completion_contract delivery depth infeasible: "
                f"{deficit['metric']}={deficit['actual']} < {deficit['required']}"
            )
        return errors

    def _settle_chief_engineer_execution_attempt_after_exception(
        self,
        *,
        lease_scope: _ChiefEngineerExecutionAttemptLeaseScope,
        stage_status: str,
        summary: str,
        preserved_error: BaseException,
    ) -> None:
        should_settle, heartbeat_failure = lease_scope.begin_settlement()
        if not should_settle or lease_scope.task_id is None or lease_scope.execution_attempt is None:
            if heartbeat_failure is not None:
                logger.error(
                    "Chief Engineer exceptional-path settlement blocked by lease keeper: "
                    "run_id=%s task_id=%s reason=%s error_type=%s preserved_error_type=%s",
                    lease_scope.execution_attempt.run_id if lease_scope.execution_attempt is not None else "",
                    lease_scope.task_id,
                    heartbeat_failure.reason,
                    heartbeat_failure.error_type,
                    type(preserved_error).__name__,
                )
            return
        try:
            self._settle_chief_engineer_execution_attempt(
                task_id=lease_scope.task_id,
                execution_attempt=lease_scope.execution_attempt,
                stage_status=stage_status,
                summary=summary,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            failure_kind = (
                "Chief Engineer cancellation settlement failed"
                if stage_status == "cancelled"
                else "Chief Engineer exceptional-path settlement failed"
            )
            logger.exception(
                "%s: run_id=%s task_id=%s session_id=%s preserved_error_type=%s",
                failure_kind,
                lease_scope.execution_attempt.run_id,
                lease_scope.task_id,
                lease_scope.execution_attempt.session_id,
                type(preserved_error).__name__,
            )

    @staticmethod
    def _chief_engineer_schema_repair_objective(
        *,
        prior_result: RoleExecutionResultV1,
        portfolio_task_ids: tuple[str, ...],
    ) -> str:
        """Build one bounded CE reconstruction objective without replaying corrupt bytes."""

        prior_error = str(prior_result.error_message or prior_result.error_code or "output validation failed").strip()[
            :_CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS
        ]
        prior_output = str(prior_result.output or "")
        prior_output_sha256 = hashlib.sha256(prior_output.encode("utf-8")).hexdigest()
        return (
            "Reconstruct a fresh, concise Chief Engineer portfolio as exactly one valid JSON object from the "
            "authoritative validated PM contracts, target_files, and scope_paths already attached to this request. "
            "Do not copy, quote, continue, or textually repair the previous malformed output; its bytes are "
            "intentionally excluded so corrupt placeholders and duplicated stream fragments cannot be replayed. "
            "Preserve every validated PM task id and remain inside PM-authoritative scope. Call the required "
            "submit_structured_role_output result-submission tool exactly once, with the complete portfolio object "
            "as its arguments. Emit no assistant prose or raw JSON outside that tool call: no markdown, "
            "SESSION_PATCH wrapper, placeholder syntax, angle-bracket metavariables, comments, or trailing "
            "fragments. Keep the tool arguments under 8,000 output tokens.\n\n"
            "Required shape:\n"
            "- required top-level keys: construction_plan, project_completion_contract, risk_flags\n"
            "- construction_plan.task_plans: an object; it may be empty because exact PM task authority is "
            "projected deterministically after schema validation\n"
            "- construction_plan.project_interface_contract: object containing provider_declarations and "
            "consumer_declarations arrays; either array may be empty when no cross-task interface is required\n"
            "- construction_plan.shared_behavior_contract: object containing invariants. Every invariant must bind "
            "an owner task, consumer tasks, covered completion obligation ids, and concrete given/when/then examples. "
            "Each linked task plan must list the invariant id in behavior_invariant_refs. Use an empty invariants array "
            "only when no required source/test behavior crosses task ownership\n"
            "- task-plan overlays are advisory only; do not invent alternate task ids, target files, scope, "
            "dependencies, or entrypoints\n"
            "- project_completion_contract.obligations: object containing artifacts, entrypoints, and verification "
            "arrays that follow the active provider tool schema and PM authority; include every required PM target "
            "and, when delivery_depth_contract is present, enough distinct task-owned production/test source "
            "artifacts to satisfy min_prod_files and min_test_files. Arrays may be empty only when the authoritative "
            "PM/depth/application contract has no obligation of that kind\n"
            "- every verification modality must be exactly one of: environment_prep, build, test, lint, entrypoint. "
            "Use test for QA/domain/behavior verification; never emit qa, domain, verify, check, smoke, or other "
            "semantic aliases as modality values\n"
            "- risk_flags: array; optional scope_for_apply, when present: array\n\n"
            f"Validated PM task ids: {json.dumps(list(portfolio_task_ids), ensure_ascii=False)}\n"
            f"Prior validation failure: {prior_error}\n"
            f"Excluded prior output SHA-256: {prior_output_sha256}\n"
            f"Excluded prior output UTF-8 character count: {len(prior_output)}"
        )

    def _chief_engineer_post_validation_repair_result(
        self,
        *,
        prior_result: RoleExecutionResultV1,
        output_errors: list[str],
    ) -> RoleExecutionResultV1:
        """Project a schema-valid CE contract deficit into the bounded repair path.

        The provider call itself succeeded, but its candidate failed an
        authoritative post-transport contract check.  The existing repair
        runner consumes a failed ``RoleExecutionResultV1``; project only that
        verdict while preserving original provider/final-request evidence.
        """

        normalized_errors = tuple(str(item).strip() for item in output_errors if str(item).strip())
        if not normalized_errors:
            raise ValueError("chief_engineer_post_validation_repair_errors_required")
        metadata = dict(getattr(prior_result, "metadata", {}) or {})
        metadata["chief_engineer_post_validation_errors"] = list(normalized_errors)
        return RoleExecutionResultV1(
            ok=False,
            status="failed",
            role=str(getattr(prior_result, "role", "") or "chief_engineer"),
            workspace=str(getattr(prior_result, "workspace", "") or self.workspace),
            task_id=getattr(prior_result, "task_id", None),
            session_id=getattr(prior_result, "session_id", None),
            run_id=getattr(prior_result, "run_id", None),
            output=str(getattr(prior_result, "output", "") or ""),
            thinking=getattr(prior_result, "thinking", None),
            tool_calls=tuple(getattr(prior_result, "tool_calls", ()) or ()),
            artifacts=tuple(getattr(prior_result, "artifacts", ()) or ()),
            usage=dict(getattr(prior_result, "usage", {}) or {}),
            metadata=metadata,
            error_code="output_validation_failed",
            error_message="; ".join(normalized_errors),
            turn_history=list(getattr(prior_result, "turn_history", ()) or ()),
        )

    def _settle_chief_engineer_attempt_before_schema_repair(
        self,
        *,
        lease_scope: _ChiefEngineerExecutionAttemptLeaseScope,
    ) -> None:
        """Suspend the invalid primary CE attempt before claiming its repair task."""

        should_settle, heartbeat_failure = lease_scope.begin_settlement()
        if not should_settle or lease_scope.task_id is None or lease_scope.execution_attempt is None:
            reason = heartbeat_failure.error_message if heartbeat_failure is not None else "settlement_not_started"
            raise RuntimeError(f"chief_engineer_schema_repair_primary_settlement_blocked:{reason}")
        self._settle_chief_engineer_execution_attempt(
            task_id=lease_scope.task_id,
            execution_attempt=lease_scope.execution_attempt,
            stage_status="failed",
            summary="chief_engineer_output_validation_failed_before_schema_repair",
        )

    def _complete_chief_engineer_attempt_after_schema_repair(
        self,
        *,
        run_id: str,
        objective: str,
        lease_budget: _ChiefEngineerExecutionAttemptLeaseBudget,
    ) -> None:
        """Close the original CE helper after its bounded repair succeeds.

        The invalid primary response is suspended before the separately
        claimed schema-repair attempt.  A successful repair supersedes that
        response, so the original helper must be re-claimed and terminally
        completed; otherwise its pending row survives forever and makes an
        otherwise verified project fail ``task_runtime_not_completed``.
        """

        portfolio_task_id = f"CE-PORTFOLIO-{run_id}"
        task_id, execution_attempt = self._claim_chief_engineer_execution_attempt(
            run_id=run_id,
            portfolio_task_id=portfolio_task_id,
            objective=objective,
            lease_budget=lease_budget,
        )
        self._settle_chief_engineer_execution_attempt(
            task_id=task_id,
            execution_attempt=execution_attempt,
            stage_status="success",
            summary="chief_engineer_primary_attempt_superseded_by_schema_repair",
        )
