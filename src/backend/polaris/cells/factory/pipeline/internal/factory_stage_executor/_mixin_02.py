"""Private mixin _Mixin02 for OrchestrationStageExecutor."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    pass

from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerBlueprintPortfolioV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    build_chief_engineer_blueprint_portfolio,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    validate_director_handoff_from_payload,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    _issue_chief_engineer_portfolio_authority_carrier,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleEvidenceAuthorityBindingV1,
)
from polaris.cells.roles.kernel.public.service import QualityChecker
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleTaskCommandV1,
    RoleExecutionResultV1,
)
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
)
from polaris.kernelone.constants import (
    MAX_LLM_PROVIDER_TIMEOUT_SECONDS,  # noqa: F401 — re-exported for characterization-test surface
)
from polaris.kernelone.llm.budget_policy import (
    chief_engineer_portfolio_output_tokens,
)

from .. import (
    factory_director_dispatch_impl as director_dispatch_impl,
    factory_materialization_impl as materialization_impl,
    factory_stage_helpers as helpers,
    factory_workspace_quality_impl as workspace_quality_impl,
)
from ..factory_deadline_calculations import (  # noqa: F401 — re-exported for characterization-test surface
    _CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS,
    _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS,
    _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS,
    ChiefEngineerExecutionAttemptLeaseBudget as _ChiefEngineerExecutionAttemptLeaseBudget,
)
from ..factory_deadline_policy import (
    FactoryDeadlineAdmissionV1,
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
)
from ..factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from ..factory_run_models import (
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    FactoryRun,
    StageResult,
)
from ..factory_stage_artifact_bindings import (
    PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY,
    RevalidatedPMStageArtifactBindingV1,
)
from ..run_ledger import load_run_ledger_projection
from ._helpers import (
    _CHIEF_ENGINEER_PORTFOLIO_REASONING_BUDGET_TOKENS,
    _CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS,
    _CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
    _CHIEF_ENGINEER_SCHEMA_REPAIR_REASONING_BUDGET_TOKENS,
    _ChiefEngineerExecutionAttemptLeaseKeeper,
    _ChiefEngineerExecutionAttemptLeaseScope,
    _ChiefEngineerPortfolioAuthorityError,
    _ChiefEngineerPortfolioAuthorityV1,
    _is_workspace_quality_repair_path,
)
from ._pkg_proxy import pkg

logger = logging.getLogger("polaris.cells.factory.pipeline.internal.factory_stage_executor")


class _Mixin02:
    """Method group extracted from OrchestrationStageExecutor (lossless)."""

    async def _run_chief_engineer_schema_repair(
        self,
        *,
        run: FactoryRun,
        authority_port: FactoryRoleEvidenceAuthorityPort,
        authority_binding: FactoryRoleEvidenceAuthorityBindingV1,
        prior_result: RoleExecutionResultV1,
        portfolio_context: Mapping[str, Any],
        portfolio_task_ids: tuple[str, ...],
        deadline_decision: FactoryDeadlineAdmissionV1,
    ) -> RoleExecutionResultV1:
        """Run exactly one separately claimed, deadline-admitted CE schema repair."""

        repair_scope = _ChiefEngineerExecutionAttemptLeaseScope()
        repair_task_id = f"CE-PORTFOLIO-{run.id}-SCHEMA-REPAIR"
        repair_timeout_seconds = int(deadline_decision.timeout_seconds)
        repair_lease_budget = self._chief_engineer_execution_attempt_lease_budget(repair_timeout_seconds)
        repair_objective = self._chief_engineer_schema_repair_objective(
            prior_result=prior_result,
            portfolio_task_ids=portfolio_task_ids,
        )
        prior_error = str(prior_result.error_message or prior_result.error_code or "output validation failed").strip()[
            :_CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS
        ]
        prior_output = str(prior_result.output or "")
        repair_failure_feedback = {
            "schema_version": "factory.chief_engineer_schema_repair.failure_evidence.v1",
            "failure_class": self._ce_schema_repair_failure_class(prior_result),
            "failure_stage": "chief_engineer_review",
            "detail": prior_error,
            "prior_output_sha256": hashlib.sha256(prior_output.encode("utf-8")).hexdigest(),
            "prior_output_chars": len(prior_output),
            "evidence_refs": [],
        }
        try:
            runtime_task_id, execution_attempt = self._claim_chief_engineer_execution_attempt(
                run_id=run.id,
                portfolio_task_id=repair_task_id,
                objective=repair_objective,
                lease_budget=repair_lease_budget,
            )
            repair_scope.bind_claim(task_id=runtime_task_id, execution_attempt=execution_attempt)
            repair_scope.start_keeper(
                _ChiefEngineerExecutionAttemptLeaseKeeper(
                    workspace=str(self.workspace),
                    task_id=runtime_task_id,
                    execution_attempt=execution_attempt,
                    budget=repair_lease_budget,
                )
            )
            repair_context = deepcopy(dict(portfolio_context))
            repair_context.update(
                {
                    "chief_engineer_schema_repair": True,
                    "chief_engineer_schema_repair_of_task_id": f"CE-PORTFOLIO-{run.id}",
                    "chief_engineer_prior_error_code": str(prior_result.error_code or ""),
                    "chief_engineer_prior_error_message": prior_error,
                    "failure_feedback": repair_failure_feedback,
                    "chief_engineer_deadline_decision": deadline_decision.to_dict(),
                    "chief_engineer_llm_timeout_seconds": repair_timeout_seconds,
                    "llm_call_timeout_seconds": repair_timeout_seconds,
                    "request_timeout_seconds": repair_timeout_seconds,
                    "temperature": 0.0,
                    "llm_max_tokens": _CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
                    "reasoning_budget_tokens": _CHIEF_ENGINEER_SCHEMA_REPAIR_REASONING_BUDGET_TOKENS,
                    "response_format_mode": "json",
                    "chief_engineer_json_contract_required": True,
                    "chief_engineer_portfolio_required": True,
                }
            )
            command = ExecuteRoleTaskCommandV1(
                role="chief_engineer",
                task_id=repair_task_id,
                workspace=str(self.workspace),
                objective=repair_objective,
                run_id=run.id,
                stream=True,
                context=repair_context,
                timeout_seconds=repair_timeout_seconds,
                execution_attempt=execution_attempt,
                structured_output_contract=self._chief_engineer_structured_output_contract(portfolio_task_ids),
                metadata={
                    "pm_task_contract": dict(repair_context["pm_task_contract"]),
                    "pm_task_contracts": list(repair_context["pm_task_contracts"]),
                    "target_files": list(repair_context["target_files"]),
                    "scope_paths": list(repair_context["scope_paths"]),
                    "source": "factory_stage_executor.chief_engineer_schema_repair",
                    "schema_repair_of_task_id": f"CE-PORTFOLIO-{run.id}",
                    "cognitive_runtime_mode": "off",
                    "cognitive_runtime_enabled": False,
                    "cognitive_runtime_required": False,
                    "llm_call_timeout_seconds": repair_timeout_seconds,
                    "validate_output": True,
                    "max_retries": 0,
                    "temperature": 0.0,
                    "llm_max_tokens": _CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
                    "reasoning_budget_tokens": _CHIEF_ENGINEER_SCHEMA_REPAIR_REASONING_BUDGET_TOKENS,
                    "response_format_mode": "json",
                    "chief_engineer_json_contract_required": True,
                    "chief_engineer_portfolio_required": True,
                },
            )
            result = cast(
                RoleExecutionResultV1,
                await self._call_with_factory_role_evidence_authority(
                    authority_port,
                    "chief_engineer",
                    lambda: pkg().RoleRuntimeService().execute_role_task(command),
                    authority_binding=authority_binding,
                ),
            )
            should_settle, heartbeat_failure = repair_scope.begin_settlement()
            if not should_settle or repair_scope.execution_attempt is None:
                reason = heartbeat_failure.error_message if heartbeat_failure is not None else "settlement_not_started"
                raise RuntimeError(f"chief_engineer_schema_repair_settlement_blocked:{reason}")
            self._settle_chief_engineer_execution_attempt(
                task_id=runtime_task_id,
                execution_attempt=repair_scope.execution_attempt,
                stage_status="success" if result.ok else "failed",
                summary=(
                    "chief_engineer_schema_repair_completed"
                    if result.ok
                    else str(result.error_code or "chief_engineer_schema_repair_failed")
                ),
            )
            if result.ok:
                self._complete_chief_engineer_attempt_after_schema_repair(
                    run_id=run.id,
                    objective=repair_objective,
                    lease_budget=repair_lease_budget,
                )
            return result
        except asyncio.CancelledError as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=repair_scope,
                stage_status="cancelled",
                summary="chief_engineer_schema_repair_cancelled",
                preserved_error=exc,
            )
            raise
        except BaseException as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=repair_scope,
                stage_status="failed",
                summary=f"chief_engineer_schema_repair_exception:{type(exc).__name__}",
                preserved_error=exc,
            )
            raise
        finally:
            repair_scope.stop_keeper()

    async def _execute_chief_engineer_review(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        """Run CE review under one claim-bound heartbeat and settlement scope."""

        lease_scope = _ChiefEngineerExecutionAttemptLeaseScope()
        try:
            return await self._execute_chief_engineer_review_with_attempt_lease(
                run,
                context,
                lease_scope,
            )
        except asyncio.CancelledError as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=lease_scope,
                stage_status="cancelled",
                summary="chief_engineer_portfolio_review_cancelled",
                preserved_error=exc,
            )
            raise
        except BaseException as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=lease_scope,
                stage_status="failed",
                summary=f"chief_engineer_portfolio_review_exception:{type(exc).__name__}",
                preserved_error=exc,
            )
            raise
        finally:
            lease_scope.stop_keeper()

    async def _execute_chief_engineer_review_with_attempt_lease(
        self,
        run: FactoryRun,
        context: dict[str, Any],
        lease_scope: _ChiefEngineerExecutionAttemptLeaseScope,
    ) -> StageResult:
        """Create one CE project portfolio and project task-level handoffs."""

        logger.info("Executing Chief Engineer project review for run %s", run.id)
        authority_port = self._factory_role_evidence_cutoff_port(context)
        synced_plan_source = self._ensure_pm_plan_contract_available()
        self._enrich_pm_plan_contract_artifact("tasks/plan.json")
        stage_signals: list[dict[str, Any]] = []
        blueprint_rows: list[dict[str, Any]] = []
        portfolio: ChiefEngineerBlueprintPortfolioV1 | None = None
        ce_evidence: dict[str, Any] = {}
        llm_call_count = 0
        cancelled_by_factory = False
        ce_runtime_task_id: int | None = None
        ce_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None

        if synced_plan_source:
            stage_signals.append(
                {
                    "code": "chief_engineer.plan_contract_synced_from_workspace_mirror",
                    "severity": "info",
                    "detail": "Copied PM workspace plan mirror into runtime tasks/plan.json before blueprint review.",
                    "source_path": synced_plan_source,
                }
            )

        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        if not pm_tasks:
            stage_signals.append(
                {
                    "code": "chief_engineer.plan_missing",
                    "severity": "error",
                    "detail": "tasks/plan.json missing or empty tasks array",
                }
            )

        cancel_event = self._resolve_cancel_event(context)
        abort_checker = self._resolve_abort_checker(context)
        if pm_tasks and cancel_event is not None and cancel_event.is_set():
            cancelled_by_factory = True
            stage_signals.append(
                {
                    "code": "chief_engineer.cancelled_before_portfolio",
                    "severity": "warning",
                    "detail": "Factory cancel event was set before the CE portfolio request.",
                }
            )
        if pm_tasks and not cancelled_by_factory and abort_checker is not None:
            abort_reason = ""
            with contextlib.suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
                abort_reason = str(await abort_checker() or "").strip()
            if abort_reason:
                cancelled_by_factory = True
                stage_signals.append(
                    {
                        "code": "chief_engineer.cancelled_before_portfolio",
                        "severity": "warning",
                        "detail": f"Factory abort was requested before CE portfolio review: {abort_reason}",
                        "abort_reason": abort_reason,
                    }
                )

        portfolio_tasks: tuple[ChiefEngineerPortfolioTaskV1, ...] = ()
        portfolio_context: dict[str, Any] = {}
        portfolio_authority: _ChiefEngineerPortfolioAuthorityV1 | None = None
        deadline_decision: FactoryDeadlineAdmissionV1 | None = None
        if pm_tasks and not cancelled_by_factory:
            try:
                start_metadata_raw = context.get("metadata")
                start_metadata = start_metadata_raw if isinstance(start_metadata_raw, Mapping) else {}
                local_rework_raw = start_metadata.get("chief_engineer_local_rework_evidence")
                local_rework_evidence = local_rework_raw if isinstance(local_rework_raw, Mapping) else None
                portfolio_tasks = self._chief_engineer_portfolio_tasks(pm_tasks)
                portfolio_context = self._chief_engineer_portfolio_context(
                    pm_tasks,
                    run_id=run.id,
                    failure_feedback=local_rework_evidence,
                )
            except (TypeError, ValueError) as exc:
                stage_signals.append(
                    {
                        "code": "chief_engineer.portfolio_contract_invalid",
                        "severity": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )

        if portfolio_tasks:
            try:
                portfolio_authority = await self._load_chief_engineer_portfolio_authority(
                    run=run,
                    pm_tasks=pm_tasks,
                    portfolio_tasks=portfolio_tasks,
                )
                portfolio_context["project_completion_authority"] = {
                    "project_id": portfolio_authority.project_id,
                    "run_id": run.id,
                    "pm_contract_hash": portfolio_authority.pm_contract_hash,
                    "covered_task_ids": list(portfolio_authority.pm_task_ids),
                    "project_kind_authority": portfolio_authority.project_kind_authority.to_dict(),
                    "completion_predicate_version": "polaris.project_completion_predicate.v1",
                    "verifier_policy_hash": portfolio_authority.verifier_policy_hash,
                    "verifier_policy": dict(portfolio_authority.verifier_policy),
                    "verifier_policy_snapshot_hash": portfolio_authority.verifier_policy_snapshot_hash,
                    "verification_command_authority": [
                        item.to_dict() for item in portfolio_authority.verification_command_authority
                    ],
                    "authority": "factory_committed_pm_and_verifier_policy",
                    "llm_may_override": False,
                }
            except _ChiefEngineerPortfolioAuthorityError as exc:
                stage_signals.append(
                    {
                        "code": exc.code,
                        "severity": "error",
                        "detail": str(exc),
                    }
                )
                portfolio_tasks = ()
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                stage_signals.append(
                    {
                        "code": "chief_engineer.project_completion_authority_invalid",
                        "severity": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
                portfolio_tasks = ()

        ce_result: RoleExecutionResultV1 | None = None
        if portfolio_tasks:
            dependency_schedule = build_task_dependency_schedule(pm_tasks)
            requested_timeout_seconds = self._chief_engineer_llm_timeout_seconds(context)
            deadline_decision = self._chief_engineer_deadline_projection_decision(
                context,
                requested_timeout_seconds=requested_timeout_seconds,
                dependency_schedule=dependency_schedule,
            )
            deadline_payload = deadline_decision.to_dict()
            if deadline_decision.disposition is FactoryDeadlineDispositionV1.BLOCK:
                stage_signals.append(
                    {
                        "code": "chief_engineer.deadline_admission_blocked",
                        "severity": "error",
                        "detail": (
                            "The CE portfolio request was not admitted because the remaining Factory lease "
                            "cannot preserve all mandatory Director, QA, finalization, and safety budgets."
                        ),
                        "deadline_decision": deadline_payload,
                        "reason": deadline_decision.reason,
                    }
                )
            else:
                ce_timeout_seconds = int(deadline_decision.timeout_seconds)
                ce_lease_budget = self._chief_engineer_execution_attempt_lease_budget(ce_timeout_seconds)
                portfolio_context.update(
                    {
                        "cognitive_runtime_mode": "off",
                        "cognitive_runtime_enabled": False,
                        "cognitive_runtime_required": False,
                        "suppress_working_memory_contract": True,
                        "suppress_tool_policy_prompt": True,
                        "disable_internal_tool_rounds": True,
                        "delivery_mode": "analyze_only",
                        "temperature": 0.2,
                        "response_format_mode": "json",
                        "chief_engineer_json_contract_required": True,
                        "chief_engineer_portfolio_required": True,
                        "llm_max_tokens": chief_engineer_portfolio_output_tokens(len(portfolio_tasks)),
                        "reasoning_budget_tokens": _CHIEF_ENGINEER_PORTFOLIO_REASONING_BUDGET_TOKENS,
                        "chief_engineer_llm_timeout_seconds": ce_timeout_seconds,
                        "llm_call_timeout_seconds": ce_timeout_seconds,
                        "request_timeout_seconds": ce_timeout_seconds,
                        "chief_engineer_deadline_decision": deadline_payload,
                    }
                )
                portfolio_task_id = f"CE-PORTFOLIO-{run.id}"
                try:
                    objective = self._chief_engineer_portfolio_objective(pm_tasks)
                    ce_runtime_task_id, ce_execution_attempt = self._claim_chief_engineer_execution_attempt(
                        run_id=run.id,
                        portfolio_task_id=portfolio_task_id,
                        objective=objective,
                        lease_budget=ce_lease_budget,
                    )
                    lease_scope.bind_claim(
                        task_id=ce_runtime_task_id,
                        execution_attempt=ce_execution_attempt,
                    )
                    lease_scope.start_keeper(
                        _ChiefEngineerExecutionAttemptLeaseKeeper(
                            workspace=str(self.workspace),
                            task_id=ce_runtime_task_id,
                            execution_attempt=ce_execution_attempt,
                            budget=ce_lease_budget,
                        )
                    )
                    command = ExecuteRoleTaskCommandV1(
                        role="chief_engineer",
                        task_id=portfolio_task_id,
                        workspace=str(self.workspace),
                        objective=objective,
                        run_id=run.id,
                        stream=True,
                        context=portfolio_context,
                        timeout_seconds=ce_timeout_seconds,
                        execution_attempt=ce_execution_attempt,
                        structured_output_contract=self._chief_engineer_structured_output_contract(
                            tuple(task.task_id for task in portfolio_tasks)
                        ),
                        metadata={
                            "pm_task_contract": dict(portfolio_context["pm_task_contract"]),
                            "pm_task_contracts": list(portfolio_context["pm_task_contracts"]),
                            "target_files": list(portfolio_context["target_files"]),
                            "scope_paths": list(portfolio_context["scope_paths"]),
                            "source": "factory_stage_executor.chief_engineer_portfolio_review",
                            "cognitive_runtime_mode": "off",
                            "cognitive_runtime_enabled": False,
                            "cognitive_runtime_required": False,
                            "llm_call_timeout_seconds": ce_timeout_seconds,
                            "validate_output": True,
                            "max_retries": 0,
                            "temperature": 0.2,
                            "reasoning_budget_tokens": _CHIEF_ENGINEER_PORTFOLIO_REASONING_BUDGET_TOKENS,
                            "response_format_mode": "json",
                            "chief_engineer_json_contract_required": True,
                            "chief_engineer_portfolio_required": True,
                            "project_completion_authority": dict(portfolio_context["project_completion_authority"]),
                        },
                    )
                    llm_call_count = 1
                    authority_binding = authority_port.mint_authority_binding("chief_engineer")
                    ce_result = cast(
                        RoleExecutionResultV1,
                        await self._call_with_factory_role_evidence_authority(
                            authority_port,
                            "chief_engineer",
                            lambda: pkg().RoleRuntimeService().execute_role_task(command),
                            authority_binding=authority_binding,
                        ),
                    )
                    if self._ce_portfolio_result_allows_schema_repair(ce_result):
                        initial_evidence = self._ce_extract_llm_evidence(
                            ce_result,
                            task_id=portfolio_task_id,
                            run_id=run.id,
                        )
                        repair_signal: dict[str, Any] = {
                            "code": "chief_engineer.output_schema_repair_started",
                            "severity": "warning",
                            "detail": str(
                                ce_result.error_message
                                or "CE stream output failed validation; one bounded schema repair was requested."
                            ),
                            "task_id": portfolio_task_id,
                            "repair_task_id": f"{portfolio_task_id}-SCHEMA-REPAIR",
                            "prior_error_code": ce_result.error_code,
                            "prior_failure_class": self._ce_schema_repair_failure_class(ce_result),
                        }
                        self._attach_ce_llm_evidence(repair_signal, initial_evidence)
                        stage_signals.append(repair_signal)
                        try:
                            self._settle_chief_engineer_attempt_before_schema_repair(lease_scope=lease_scope)
                        except (OSError, RuntimeError, TypeError, ValueError) as exc:
                            stage_signals.append(
                                {
                                    "code": "chief_engineer.output_schema_repair_settlement_failed",
                                    "severity": "error",
                                    "detail": f"{type(exc).__name__}: {exc}",
                                    "task_id": portfolio_task_id,
                                }
                            )
                            ce_result = None
                        else:
                            deadline_decision = self._chief_engineer_deadline_projection_decision(
                                context,
                                requested_timeout_seconds=requested_timeout_seconds,
                                dependency_schedule=dependency_schedule,
                                output_tokens=_CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
                            )
                            if deadline_decision.disposition is FactoryDeadlineDispositionV1.BLOCK:
                                stage_signals.append(
                                    {
                                        "code": "chief_engineer.output_schema_repair_deadline_blocked",
                                        "severity": "error",
                                        "detail": (
                                            "The CE schema repair was not admitted because the remaining Factory "
                                            "lease cannot preserve mandatory downstream budgets."
                                        ),
                                        "task_id": portfolio_task_id,
                                        "deadline_decision": deadline_decision.to_dict(),
                                        "reason": deadline_decision.reason,
                                    }
                                )
                                ce_result = None
                            else:
                                ce_result = await self._run_chief_engineer_schema_repair(
                                    run=run,
                                    authority_port=authority_port,
                                    authority_binding=authority_binding,
                                    prior_result=ce_result,
                                    portfolio_context=portfolio_context,
                                    portfolio_task_ids=tuple(task.task_id for task in portfolio_tasks),
                                    deadline_decision=deadline_decision,
                                )
                                llm_call_count = 2
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — contain provider/http failures as stage signals
                    # Provider/network failures (e.g. aiohttp.ClientResponseError on
                    # HTTP 403 quota) must become stage signals, not uncaught escapes
                    # that strand the Factory run before execute_stage can finish.
                    stage_signals.append(
                        {
                            "code": "chief_engineer.llm_review_failed",
                            "severity": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                            "task_id": portfolio_task_id,
                            "exception_type": type(exc).__name__,
                        }
                    )
                    ce_result = None

        ce_llm_blueprint: dict[str, Any] = {}
        if ce_result is not None:
            portfolio_task_id = f"CE-PORTFOLIO-{run.id}"
            ce_evidence = self._ce_extract_llm_evidence(
                ce_result,
                task_id=portfolio_task_id,
                run_id=run.id,
            )
            ce_provider = str(ce_evidence.get("provider") or "unknown")
            ce_model = str(ce_evidence.get("model") or "unknown")
            raw_output = str(ce_result.output or "")
            ce_result_metadata = dict(ce_result.metadata or {})

            if not ce_result.ok:
                error_signal: dict[str, Any] = {
                    "code": "chief_engineer.llm_review_failed",
                    "severity": "error",
                    "detail": ce_result.error_message or ce_result.error_code or "CE portfolio LLM call failed",
                    "task_id": portfolio_task_id,
                    "provider": ce_provider,
                    "model": ce_model,
                    "recoverable": False,
                }
                self._attach_ce_llm_evidence(error_signal, ce_evidence)
                stage_signals.append(error_signal)
            elif ce_evidence.get("provider_model_unknown"):
                stage_signals.append(
                    {
                        "code": "chief_engineer.llm_evidence_missing",
                        "severity": "error",
                        "detail": str(ce_evidence.get("provider_model_unknown_reason") or ""),
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "provider_model_unknown": True,
                    }
                )
            else:
                audit_payload: dict[str, Any] = {
                    "provider": ce_provider,
                    "model": ce_model,
                    "cache_hit": bool(ce_evidence.get("cache_hit")),
                    "task_id": portfolio_task_id,
                    "run_id": run.id,
                    "portfolio_task_ids": [task.task_id for task in portfolio_tasks],
                }
                self._attach_ce_llm_evidence(audit_payload, ce_evidence)
                self._emit_audit_event("chief_engineer.llm_call", **audit_payload)
                missing_final_request_evidence = self._ce_missing_final_request_evidence(ce_evidence)
                if missing_final_request_evidence:
                    missing_signal: dict[str, Any] = {
                        "code": "chief_engineer.final_request_audit_missing",
                        "severity": "error",
                        "detail": (
                            "CE LLM result did not expose required final provider-request evidence: "
                            + ", ".join(missing_final_request_evidence)
                        ),
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "missing": missing_final_request_evidence,
                    }
                    self._attach_ce_llm_evidence(missing_signal, ce_evidence)
                    stage_signals.append(missing_signal)

            call_error_count = sum(
                1 for signal in stage_signals if str(signal.get("severity") or "").strip().lower() == "error"
            )
            structured_output = ce_result_metadata.get("structured_output")
            if isinstance(structured_output, Mapping):
                ce_llm_blueprint = dict(structured_output)
            elif "<SESSION_PATCH" in raw_output or "</SESSION_PATCH>" in raw_output:
                stage_signals.append(
                    {
                        "code": "chief_engineer.session_patch_output_rejected",
                        "severity": "error",
                        "detail": "CE returned SESSION_PATCH content instead of the required portfolio JSON object",
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                    }
                )
            elif call_error_count == 0:
                quality_result = QualityChecker(str(self.workspace)).validate_output(
                    raw_output,
                    cast(Any, SimpleNamespace(role_id="chief_engineer")),
                )
                if not quality_result.success:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.output_schema_invalid",
                            "severity": "error",
                            "detail": "; ".join(str(item) for item in quality_result.errors)
                            or "CE portfolio output failed schema validation",
                            "task_id": portfolio_task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                            "quality_score": float(quality_result.quality_score),
                            "suggestions": list(quality_result.suggestions),
                        }
                    )
                elif isinstance(quality_result.data, Mapping):
                    ce_llm_blueprint = dict(quality_result.data)

            if ce_llm_blueprint and call_error_count == 0:
                if "scope_for_apply" not in ce_llm_blueprint:
                    omission_signal: dict[str, Any] = {
                        "code": "chief_engineer.scope_advisory_omitted",
                        "severity": "warning",
                        "detail": (
                            "CE omitted non-authoritative scope_for_apply advice; "
                            "PM target_files and scope_paths remain the apply authority."
                        ),
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "pm_authority_preserved": True,
                        "scope_expansion_allowed": False,
                    }
                    self._attach_ce_llm_evidence(omission_signal, ce_evidence)
                    stage_signals.append(omission_signal)
                output_errors = self._chief_engineer_portfolio_output_errors(
                    ce_llm_blueprint,
                    task_ids=tuple(task.task_id for task in portfolio_tasks),
                )
                if output_errors:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.portfolio_output_invalid",
                            "severity": "error",
                            "detail": "; ".join(output_errors),
                            "task_id": portfolio_task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                            "errors": output_errors,
                        }
                    )
            elif not ce_llm_blueprint and call_error_count == 0:
                stage_signals.append(
                    {
                        "code": "chief_engineer.output_schema_invalid",
                        "severity": "error",
                        "detail": "CE portfolio output did not contain a JSON object",
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                    }
                )

        has_pre_projection_errors = any(
            str(signal.get("severity") or "").strip().lower() == "error" for signal in stage_signals
        )
        if portfolio_tasks and portfolio_authority is not None and ce_llm_blueprint and not has_pre_projection_errors:
            try:
                portfolio = build_chief_engineer_blueprint_portfolio(
                    BuildChiefEngineerBlueprintPortfolioCommandV1(
                        workspace=str(self.workspace),
                        run_id=run.id,
                        tasks=portfolio_tasks,
                        authority_carrier=_issue_chief_engineer_portfolio_authority_carrier(
                            workspace=str(self.workspace),
                            run_id=run.id,
                            project_id=portfolio_authority.project_id,
                            pm_stage_event_id=portfolio_authority.pm_stage_event_id,
                            pm_contract_hash=portfolio_authority.pm_contract_hash,
                            tasks=portfolio_tasks,
                            catalog_snapshot=portfolio_authority.catalog_snapshot,
                            catalog_snapshot_hash=portfolio_authority.catalog_snapshot_hash,
                            verifier_policy_hash=portfolio_authority.verifier_policy_hash,
                            verifier_policy_snapshot=portfolio_authority.verifier_policy,
                            verifier_policy_snapshot_hash=portfolio_authority.verifier_policy_snapshot_hash,
                            verification_command_authority=(portfolio_authority.verification_command_authority),
                        ),
                        llm_blueprint=ce_llm_blueprint,
                    )
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                stage_signals.append(
                    {
                        "code": "chief_engineer.portfolio_generation_failed",
                        "severity": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )

        if portfolio is not None:
            portfolio_reference = portfolio.to_reference()
            portfolio_context_evidence = portfolio.to_task_blueprint_context()
            for index, task in enumerate(pm_tasks, start=1):
                task_id = self._task_id(task, index)
                objective = self._task_objective(task)
                task_constraints = self._task_blueprint_constraints(task)
                task_context = self._task_blueprint_context(task, run_id=run.id, index=index)
                task_context.update(portfolio_context_evidence)
                task_context["chief_engineer_blueprint_portfolio"] = dict(portfolio_reference)
                if deadline_decision is not None:
                    task_context["chief_engineer_deadline_decision"] = deadline_decision.to_dict()
                try:
                    task_llm_blueprint = project_chief_engineer_task_blueprint(portfolio, task_id)
                    result = generate_task_blueprint(
                        GenerateTaskBlueprintCommandV1(
                            task_id=task_id,
                            workspace=str(self.workspace),
                            objective=objective,
                            run_id=run.id,
                            constraints=task_constraints,
                            context=task_context,
                            llm_blueprint=task_llm_blueprint,
                        )
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.blueprint_generation_failed",
                            "severity": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                            "task_id": task_id,
                        }
                    )
                    continue

                if not result.ok or not result.blueprint_id or not result.blueprint_path:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.blueprint_result_invalid",
                            "severity": "error",
                            "detail": result.summary or result.status,
                            "task_id": task_id,
                        }
                    )
                    continue

                repaired_missing_artifact = self._ensure_chief_engineer_blueprint_artifact_present(
                    result=result,
                    task=task,
                    task_context=task_context,
                    constraints=task_constraints,
                    run_id=run.id,
                )
                if repaired_missing_artifact:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.blueprint_artifact_rewritten_from_result",
                            "severity": "warning",
                            "detail": (
                                "CE returned a valid blueprint result but the physical blueprint artifact was "
                                "missing; rewrote the handoff artifact from structured result fields."
                            ),
                            "task_id": task_id,
                            "blueprint_id": result.blueprint_id,
                            "blueprint_path": result.blueprint_path,
                        }
                    )

                handoff_validation = validate_director_handoff_from_payload(
                    str(self.workspace),
                    {"task_id": task_id, "blueprint_id": result.blueprint_id},
                    require_strict=True,
                )
                handoff_payload_raw = handoff_validation.get("decision_payload")
                handoff_payload: dict[str, Any] = handoff_payload_raw if isinstance(handoff_payload_raw, dict) else {}
                if not handoff_validation.get("allowed") and not handoff_payload:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.handoff_decision_unreadable",
                            "severity": "error",
                            "detail": str(
                                handoff_validation.get("reason")
                                or "Generated CE blueprint could not be loaded for handoff validation."
                            ),
                            "task_id": task_id,
                            "blueprint_id": result.blueprint_id,
                            "handoff_validation": handoff_validation,
                        }
                    )
                elif not handoff_validation.get("allowed"):
                    stage_signals.append(
                        {
                            "code": "chief_engineer.handoff_blocked",
                            "severity": "error",
                            "detail": str(handoff_validation.get("reason") or "Chief Engineer handoff blocked."),
                            "task_id": task_id,
                            "blueprint_id": result.blueprint_id,
                            "blockers": list(handoff_payload.get("blockers") or []),
                            "handoff_decision": handoff_payload,
                            "handoff_validation": handoff_validation,
                        }
                    )

                row_evidence = {
                    **ce_evidence,
                    "portfolio_id": portfolio.portfolio_id,
                    "portfolio_path": portfolio.portfolio_path,
                    "portfolio_hash": portfolio.portfolio_hash,
                    "project_interface_contract_ref": portfolio.project_interface_contract_ref,
                    "project_interface_contract_hash": portfolio.project_interface_contract_hash,
                }
                blueprint_rows.append(
                    {
                        "task_id": result.task_id,
                        "status": result.status,
                        "blueprint_id": result.blueprint_id,
                        "blueprint_path": result.blueprint_path,
                        "summary": result.summary,
                        "recommendations": list(result.recommendations),
                        "risks": list(result.risks),
                        "handoff_ready": bool(handoff_validation.get("allowed")),
                        "handoff_decision": handoff_payload,
                        "llm_evidence": row_evidence,
                        "llm_blueprint_consumed": True,
                        "llm_blueprint_keys": sorted(task_llm_blueprint),
                        "portfolio_reference": dict(portfolio_reference),
                    }
                )

        review_artifact = ""
        if blueprint_rows or stage_signals or portfolio is not None:
            review_artifact = f"runtime/state/blueprints/{run.id}.review.json"
            self._write_json_artifact(
                review_artifact,
                {
                    "schema_version": "factory.chief_engineer_review.v2",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source": "factory_stage_executor.chief_engineer_portfolio_review",
                    "factory_run_id": run.id,
                    "task_plan": "tasks/plan.json",
                    "total_tasks": len(pm_tasks),
                    "generated_blueprints": len(blueprint_rows),
                    "llm_call_count": llm_call_count,
                    "portfolio": portfolio.to_reference() if portfolio is not None else {},
                    "project_interface_contract": (
                        portfolio.project_interface_contract.to_reference() if portfolio is not None else {}
                    ),
                    "blueprints": blueprint_rows,
                    "signals": stage_signals,
                },
            )

        keeper_stop = lease_scope.stop_keeper()
        heartbeat_failure = keeper_stop.failure
        if not keeper_stop.thread_exited:
            stage_signals.append(
                {
                    "code": "chief_engineer.execution_attempt_keeper_stop_failed",
                    "severity": "error",
                    "detail": (
                        f"{heartbeat_failure.error_type}: {heartbeat_failure.error_message}"
                        if heartbeat_failure is not None
                        else "lease keeper did not confirm thread exit"
                    ),
                    "reason": heartbeat_failure.reason if heartbeat_failure is not None else "unknown",
                }
            )
        elif heartbeat_failure is not None:
            stage_signals.append(
                {
                    "code": "chief_engineer.execution_attempt_heartbeat_failed",
                    "severity": "error",
                    "detail": (f"{heartbeat_failure.error_type}: {heartbeat_failure.error_message}"),
                    "reason": heartbeat_failure.reason,
                    "task_id": (
                        lease_scope.execution_attempt.external_task_id
                        if lease_scope.execution_attempt is not None
                        else ""
                    ),
                    "session_id": (
                        lease_scope.execution_attempt.session_id if lease_scope.execution_attempt is not None else ""
                    ),
                }
            )

        has_errors = any(
            str(item.get("severity") or "").strip().lower() == "error"
            for item in stage_signals
            if isinstance(item, dict)
        )
        stage_status = "cancelled" if cancelled_by_factory else "failed" if has_errors else "success"
        error_code = ""
        root_cause_hint = ""
        failure_recoverable = True
        if has_errors:
            for signal in stage_signals:
                if str(signal.get("severity") or "").strip().lower() != "error":
                    continue
                error_code = str(signal.get("code") or "").strip()
                root_cause_hint = str(signal.get("detail") or "").strip()
                if isinstance(signal.get("recoverable"), bool):
                    failure_recoverable = bool(signal["recoverable"])
                if error_code:
                    break

        if ce_runtime_task_id is not None and ce_execution_attempt is not None:
            should_settle, heartbeat_failure = lease_scope.begin_settlement()
            if should_settle:
                settlement_attempt = lease_scope.execution_attempt
                if settlement_attempt is None:
                    raise RuntimeError("chief_engineer_execution_attempt_settlement_identity_missing")
                try:
                    self._settle_chief_engineer_execution_attempt(
                        task_id=ce_runtime_task_id,
                        execution_attempt=settlement_attempt,
                        stage_status=stage_status,
                        summary=error_code or "chief_engineer_portfolio_review_completed",
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.execution_attempt_settlement_failed",
                            "severity": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    stage_status = "failed"
                    error_code = "chief_engineer.execution_attempt_settlement_failed"
                    root_cause_hint = str(exc)
            elif heartbeat_failure is not None and not any(
                str(signal.get("code") or "") == "chief_engineer.execution_attempt_keeper_stop_failed"
                for signal in stage_signals
                if isinstance(signal, dict)
            ):
                stage_signals.append(
                    {
                        "code": "chief_engineer.execution_attempt_settlement_blocked",
                        "severity": "error",
                        "detail": f"{heartbeat_failure.error_type}: {heartbeat_failure.error_message}",
                        "reason": heartbeat_failure.reason,
                    }
                )
                stage_status = "failed"
                error_code = "chief_engineer.execution_attempt_settlement_blocked"
                root_cause_hint = heartbeat_failure.error_message

        stage_signal_path = ""
        if stage_signals:
            stage_signal_path = self._write_stage_signal_artifact(
                stage="chief_engineer_review",
                run_id=run.id,
                signals=stage_signals,
            )

        artifacts = [row["blueprint_path"] for row in blueprint_rows if row.get("blueprint_path")]
        if portfolio is not None:
            artifacts.append(portfolio.portfolio_path)
        if review_artifact:
            artifacts.append(review_artifact)
        self._mirror_chief_engineer_artifacts(run.id, blueprint_rows, review_artifact, artifacts)
        if stage_signal_path:
            artifacts.append(stage_signal_path)

        return StageResult(
            stage="chief_engineer_review",
            status=stage_status,
            output=(
                f"Chief Engineer portfolio review generated {len(blueprint_rows)}/{len(pm_tasks)} blueprints; "
                f"llm_calls={llm_call_count}; signals={len(stage_signals)}; "
                f"error_code={error_code or 'none'}; root_cause_hint={root_cause_hint or 'none'}"
            ),
            artifacts=artifacts,
            metadata={
                "error_code": error_code,
                "failure_class": (
                    "ROLE_LLM_REVIEW_FAILED"
                    if error_code == "chief_engineer.llm_review_failed"
                    else "CHIEF_ENGINEER_REVIEW_FAILED"
                    if stage_status == "failed"
                    else ""
                ),
                "responsible_layer": "chief_engineer" if stage_status == "failed" else "",
                "root_cause_hint": root_cause_hint,
                "recoverable": failure_recoverable if stage_status == "failed" else False,
            },
        )

    async def _execute_director_dispatch(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        return await director_dispatch_impl._execute_director_dispatch(self, run, context)

    @staticmethod
    def _bool_from_context_or_env(
        context: dict[str, Any],
        *keys: str,
        env_var: str = "",
        default: bool = True,
    ) -> bool:
        return helpers.bool_from_context_or_env(context, *keys, env_var=env_var, default=default)

    def _load_package_scripts(self) -> dict[str, str]:
        return self._workspace_quality.load_package_scripts()

    def _workspace_quality_commands(self, context: dict[str, Any]) -> list[list[str]]:
        return self._workspace_quality.workspace_quality_commands(context)

    @staticmethod
    def _canonical_project_id(context: dict[str, Any]) -> str:
        return str(
            context.get("project_id")
            or context.get("requested_project_id")
            or context.get("factory_bench_project_id")
            or ""
        ).strip()

    def _canonical_factory_projection(
        self,
        run: FactoryRun,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Load the canonical Factory run-tree projection.

        The same-cell adapter owns workspace/factory/project scoping. Missing
        or malformed facts return an empty projection so all callers fail
        closed through the pure authority evaluator.
        """

        try:
            projection = load_run_ledger_projection(
                self.workspace,
                run_id=run.id,
                factory_run_id=run.id,
                project_id=self._canonical_project_id(context),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Canonical Factory projection unavailable for run %s: %s", run.id, exc)
            return {}
        # Factory portfolios also create internal settlement / verification
        # TaskRuntime rows under the same factory_run_id. Director completion
        # authority owns only the immutable, committed PM contract tasks.
        # Mutable workspace mirrors are never completion authority.
        proof = context.get(PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY)
        contract_task_ids: list[str] = []
        if (
            isinstance(proof, RevalidatedPMStageArtifactBindingV1)
            and proof.binding.factory_run_id == run.id
            and proof.binding.stage == "pm_planning"
        ):
            contract_task_ids = [
                helpers._canonical_task_id_token(task_id)
                for task_id in proof.task_ids
                if helpers._canonical_task_id_token(task_id)
            ]
        expected_task_ids = set(contract_task_ids)

        def _latest_contract_rows(authority: Mapping[str, Any]) -> list[dict[str, Any]]:
            authority_rows = authority.get("rows")
            scoped_candidates = (
                [
                    dict(row)
                    for row in authority_rows
                    if isinstance(row, Mapping) and helpers._runtime_row_contract_task_id(row) in expected_task_ids
                ]
                if isinstance(authority_rows, list)
                else []
            )
            # Stage-local retries create fresh numeric TaskRuntime rows for the
            # same immutable PM task. Keep only the newest fact for each external
            # contract identity; old failed/removed attempts remain audit facts but
            # cannot collide with or override current completion authority.
            latest_scoped_rows: dict[str, dict[str, Any]] = {}
            for row in scoped_candidates:
                contract_task_id = helpers._runtime_row_contract_task_id(row)
                previous = latest_scoped_rows.get(contract_task_id)
                current_seq = row.get("fact_event_seq")
                previous_seq = previous.get("fact_event_seq") if previous is not None else None
                if previous is None or (
                    isinstance(current_seq, int) and (not isinstance(previous_seq, int) or current_seq > previous_seq)
                ):
                    latest_scoped_rows[contract_task_id] = row
            return [latest_scoped_rows[task_id] for task_id in contract_task_ids if task_id in latest_scoped_rows]

        try:
            task_runtime_projection = (
                pkg().TaskRuntimeService(str(self.workspace)).query_observable_task_rows_projection()
            )
            task_runtime_authority = task_runtime_projection.to_authority_dict(
                factory_run_id=run.id,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Canonical live TaskRuntime projection unavailable for run %s: %s",
                run.id,
                exc,
            )
            task_runtime_authority = {}
        scoped_rows = _latest_contract_rows(task_runtime_authority)

        # Terminal Factory settlement deliberately drains TaskRuntime files and
        # leaves ``removed`` tombstones. QA retries still need the exact frozen
        # authority captured immediately before that destructive reset. This is
        # a read model of TaskRuntime's facts, not a second state owner. A
        # Director-stage retry invalidates the old epoch snapshot in
        # ``FactoryRunService.retry_run_from_stage`` before new execution.
        live_scope_is_current = (
            bool(expected_task_ids)
            and {helpers._runtime_row_contract_task_id(row) for row in scoped_rows} == expected_task_ids
            and all(
                str(row.get("execution_state") or row.get("status") or "").strip().lower() != "removed"
                for row in scoped_rows
            )
        )
        if not live_scope_is_current:
            from polaris.cells.factory.pipeline.public.contracts import (
                FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
                FactoryTerminalTaskRuntimeProjectionV1,
            )

            frozen_payload = run.metadata.get(
                FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
            )
            if isinstance(frozen_payload, Mapping):
                try:
                    frozen = FactoryTerminalTaskRuntimeProjectionV1.from_dict(
                        frozen_payload,
                    )
                except (OSError, TypeError, ValueError) as exc:
                    logger.warning(
                        "Canonical frozen TaskRuntime projection invalid for run %s: %s",
                        run.id,
                        exc,
                    )
                else:
                    if frozen.factory_run_id == run.id:
                        frozen_authority = deepcopy(dict(frozen.projection))
                        frozen_scoped_rows = _latest_contract_rows(frozen_authority)
                        frozen_scope_exact = (
                            bool(expected_task_ids)
                            and {helpers._runtime_row_contract_task_id(row) for row in frozen_scoped_rows}
                            == expected_task_ids
                            and all(
                                str(row.get("execution_state") or row.get("status") or "").strip().lower() != "removed"
                                for row in frozen_scoped_rows
                            )
                        )
                        if frozen_scope_exact:
                            task_runtime_authority = frozen_authority
                            scoped_rows = frozen_scoped_rows
                            task_runtime_authority["authority_epoch_source"] = (
                                "factory_terminal_task_runtime_projection"
                            )
        task_runtime_authority["rows"] = scoped_rows
        task_runtime_authority["row_count"] = len(scoped_rows)
        task_runtime_authority["owner_scope"] = (
            "pm_contract_tasks" if contract_task_ids else "pm_contract_binding_invalid"
        )
        # Keep duplicates so the pure evaluator can reject PM aliases such as
        # ``1`` plus ``TASK-1`` instead of silently collapsing obligations.
        task_runtime_authority["owned_task_ids"] = sorted(contract_task_ids)
        projection["task_runtime_projection"] = task_runtime_authority
        return projection

    def _workspace_quality_task_boundary_blocker(
        self,
        run: FactoryRun,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Block workspace validation until canonical task boundaries settle."""

        authority = helpers.evaluate_canonical_factory_authority(self._canonical_factory_projection(run, context))
        if authority.director_stage_authorized:
            return None
        failure_class = authority.failure_class or (
            FailureClassV1.DEPENDENCY_NOT_UNLOCKED.value
            if not authority.task_boundary_present
            else FailureClassV1.INCOMPLETE_MATERIALIZATION.value
        )
        return {
            "schema_version": "factory.workspace_quality.task_boundary_blocker.v2",
            "reason_code": authority.reason_code,
            "failure_class": failure_class,
            "responsible_layer": authority.responsible_layer or "task_boundary",
            "task_count": authority.task_count,
            "incomplete_task_ids": list(authority.incomplete_task_ids),
            "detail": authority.detail,
            "authority_source": "run_ledger_projection",
        }

    @staticmethod
    def _trim_command_output(text: str, limit: int = _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS) -> str:
        return helpers.trim_command_output(text, limit)

    def _run_workspace_quality_command(self, command: list[str], timeout_seconds: float) -> dict[str, Any]:
        return self._workspace_quality.run_command(command, timeout_seconds)

    @staticmethod
    def _resolve_workspace_quality_command(command: list[str]) -> list[str]:
        return helpers.resolve_workspace_quality_command(command)

    def _workspace_quality_repair_errors(self, results: list[dict[str, Any]]) -> list[str]:
        return workspace_quality_impl._workspace_quality_repair_errors(self, results)

    @staticmethod
    def _workspace_quality_diagnostic_signature(errors: Iterable[str]) -> tuple[str, ...]:
        """Return a stable verifier-diagnostic signature for convergence checks.

        Repair success is owned by the post-repair verifier, not by a write-tool
        receipt.  Normalize whitespace/case so formatting jitter does not buy
        another Provider attempt, while preserving paths/codes/symbols needed to
        distinguish a real diagnostic change.
        """

        normalized = {" ".join(str(error or "").split()).casefold() for error in errors if str(error or "").strip()}
        return tuple(sorted(normalized))

    @staticmethod
    def _workspace_quality_repair_effect(
        *,
        before_signature: tuple[str, ...],
        after_signature: tuple[str, ...],
        verifier_passed: bool,
        write_tool_evidence: bool,
    ) -> str:
        """Classify one local repair by verifier effect, never by model claim."""

        if verifier_passed:
            return "resolved"
        if not write_tool_evidence:
            return "no_op"
        if len(after_signature) < len(before_signature):
            return "progress"
        if len(after_signature) > len(before_signature):
            return "regression"
        if after_signature == before_signature:
            return "stagnant"
        # An equal-count diagnostic swap is not demonstrated progress.  Treat it
        # as stagnation so a model cannot burn the full budget by trading one
        # compiler error for another indefinitely.
        return "equal_count_swap"

    def _workspace_quality_repair_issue_payloads(
        self,
        artifact_quality_errors: list[str],
    ) -> tuple[dict[str, Any], ...]:
        if not artifact_quality_errors:
            return ()
        try:
            from polaris.kernelone.quality import (
                artifact_quality_issues_for_errors,
                scan_workspace_artifact_quality_evidence,
            )

            evidence = scan_workspace_artifact_quality_evidence(str(self.workspace))
            return artifact_quality_issues_for_errors(artifact_quality_errors, evidence.issues)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            pass
        try:
            from polaris.kernelone.quality import artifact_quality_issues_from_errors

            return artifact_quality_issues_from_errors(str(item) for item in artifact_quality_errors or [])
        except (ImportError, RuntimeError, TypeError, ValueError):
            return ()

    def _workspace_quality_repair_coverage_report(self, artifact_quality_errors: list[str]) -> dict[str, Any]:
        if not artifact_quality_errors:
            return {}
        try:
            from polaris.cells.director.runtime.public import (
                QueryDirectorRepairCoverageV1,
                query_director_repair_coverage,
            )

            return query_director_repair_coverage(
                QueryDirectorRepairCoverageV1(
                    artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
                    artifact_quality_issues=self._workspace_quality_repair_issue_payloads(artifact_quality_errors),
                )
            ).to_dict()
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "schema_version": "factory.workspace_quality_repair_coverage_query_error.v1",
                "source": "factory_stage_executor",
                "access": "read_only",
                "coverage_query_failed": True,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "total_diagnostics": len(artifact_quality_errors),
                "coverage_gap_count": 0,
                "coverage_gaps": [],
            }

    def _workspace_quality_repair_plan_probe_report(self, artifact_quality_errors: list[str]) -> dict[str, Any]:
        if not artifact_quality_errors:
            return {}
        try:
            from polaris.cells.director.runtime.public import (
                QueryDirectorRepairPlanProbeV1,
                query_director_repair_plan_probe,
            )

            return query_director_repair_plan_probe(
                QueryDirectorRepairPlanProbeV1(
                    artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
                    artifact_quality_issues=self._workspace_quality_repair_issue_payloads(artifact_quality_errors),
                    base_files=self._workspace_quality_repair_plan_probe_base_files(artifact_quality_errors),
                    metadata={
                        "source": "factory_stage_executor.workspace_quality",
                        "coverage_is_not_planning": True,
                    },
                )
            ).to_dict()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "schema_version": "factory.workspace_quality_repair_plan_probe_query_error.v1",
                "source": "factory_stage_executor",
                "access": "read_only",
                "plan_probe_query_failed": True,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "total_diagnostics": len(artifact_quality_errors),
                "status": "plan_probe_unavailable",
                "coverage_is_not_planning": True,
            }

    def _workspace_quality_repair_plan_probe_base_files(self, artifact_quality_errors: list[str]) -> dict[str, str]:
        workspace_root = self.workspace.resolve()
        candidates: list[str] = []
        candidates.extend(self._workspace_quality_repair_diagnostic_target_files(artifact_quality_errors))
        candidates.extend(self._workspace_quality_repair_target_files())
        base_files: dict[str, str] = {}
        for raw_candidate in candidates:
            normalized = os.path.normpath(str(raw_candidate or "").strip().replace("\\", "/")).replace("\\", "/")
            if not normalized or normalized in base_files or not _is_workspace_quality_repair_path(normalized):
                continue
            path = (workspace_root / normalized).resolve()
            try:
                if not path.is_relative_to(workspace_root) or not path.is_file():
                    continue
                if path.stat().st_size > 256_000:
                    continue
                base_files[normalized] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if len(base_files) >= 64:
                break
        return base_files

    def _director_stage_should_run_materialization_quality_settle(
        self,
        *,
        stage_status: str,
        error_code: str,
    ) -> bool:
        return materialization_impl._director_stage_should_run_materialization_quality_settle(
            self, stage_status=stage_status, error_code=error_code
        )

    def _workspace_has_delivery_surface(self) -> bool:
        return materialization_impl._workspace_has_delivery_surface(self)

    def _recover_director_stage_authority_after_delivery_settle(
        self,
        *,
        run: FactoryRun,
        context: dict[str, Any],
        prior_authority: helpers.CanonicalFactoryAuthority,
    ) -> helpers.CanonicalFactoryAuthority | None:
        return materialization_impl._recover_director_stage_authority_after_delivery_settle(
            self, run=run, context=context, prior_authority=prior_authority
        )

    def _seal_director_stage_missing_tool_lifecycles(
        self,
        *,
        run: FactoryRun,
        incomplete_task_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return materialization_impl._seal_director_stage_missing_tool_lifecycles(
            self, run=run, incomplete_task_ids=incomplete_task_ids
        )

    def _collect_director_stage_materialization_diagnostics(self) -> list[str]:
        return materialization_impl._collect_director_stage_materialization_diagnostics(self)

    def _ensure_director_stage_materialization_typescript_toolchain(self) -> None:
        """Best-effort npm install so settle can collect tsc diagnostics (R167)."""

        package_json = self.workspace / "package.json"
        if not package_json.is_file():
            return
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return
        if not isinstance(payload, Mapping):
            return
        deps: dict[str, Any] = {}
        for key in ("dependencies", "devDependencies"):
            raw = payload.get(key)
            if isinstance(raw, Mapping):
                deps.update(raw)
        has_typescript = any(str(name).lower() == "typescript" for name in deps)
        if not has_typescript:
            return
        try:
            pkg().subprocess.run(
                ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, TimeoutError, ValueError) as exc:
            logger.warning(
                "Director stage materialization settle npm install skipped: %s",
                exc,
            )

    def _claim_director_stage_materialization_settle_attempt(
        self,
        *,
        run_id: str,
    ) -> tuple[str, int, TaskRuntimeExecutionAttemptIdentityV1]:
        return materialization_impl._claim_director_stage_materialization_settle_attempt(self, run_id=run_id)

    @staticmethod
    def _workspace_quality_repair_owner_score(
        candidate: Mapping[str, Any],
        *,
        run_id: str,
        normalized_targets: set[str],
    ) -> tuple[int, int]:
        """Score only task-owned paths when selecting a verifier-repair owner.

        ``project_declared_target_files`` is a project-wide inventory copied to
        every Director task.  Treating it as ownership makes unrelated tasks tie
        for every project file; the failed/rework priority then selects the wrong
        task and the Director correctly refuses the out-of-scope edit.  Ownership
        is established only by the task-local ``target_files``/``scope_paths``.
        """

        metadata = candidate.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        candidate_factory_run_id = str(metadata.get("factory_run_id") or "").strip()
        external_id = str(metadata.get("external_task_id") or candidate.get("external_task_id") or "").strip()
        if candidate_factory_run_id != run_id or not external_id or external_id.startswith("factory-"):
            return (-1, -1)
        raw_paths: list[Any] = []
        for key in ("target_files", "scope_paths"):
            value = metadata.get(key)
            if isinstance(value, str):
                raw_paths.append(value)
            elif isinstance(value, list | tuple | set):
                raw_paths.extend(value)
        candidate_paths = {str(path or "").strip().replace("\\", "/") for path in raw_paths if str(path or "").strip()}
        overlaps = normalized_targets.intersection(candidate_paths)
        source_overlap = sum(
            1
            for path in overlaps
            if not path.startswith(("tests/", "test/", "__tests__/"))
            and "/__tests__/" not in path
            and not path.endswith((".test.js", ".test.ts", ".test.tsx", ".spec.js", ".spec.ts", ".spec.tsx"))
        )
        # A verifier diagnostic often yields both the failing test path and
        # its imported implementation source. Prefer the implementation owner
        # when each task overlaps one target; otherwise TaskRuntime row order
        # can select the test task and make the real source edit out of scope.
        overlap = len(overlaps) + source_overlap
        status = str(candidate.get("status") or candidate.get("raw_status") or "").strip().lower()
        rework_priority = 1 if status in {"pending", "ready", "blocked", "failed"} else 0
        return (overlap, rework_priority)

    def _claim_workspace_quality_repair_attempt(
        self,
        *,
        run_id: str,
        repair_attempt: int,
        target_files: list[str],
    ) -> tuple[str, int, TaskRuntimeExecutionAttemptIdentityV1, dict[str, Any]]:
        return workspace_quality_impl._claim_workspace_quality_repair_attempt(
            self, run_id=run_id, repair_attempt=repair_attempt, target_files=target_files
        )

    @staticmethod
    def _materialization_settle_attempt_outcome(stage_status: str) -> TaskRuntimeExecutionAttemptSettlementOutcomeV1:
        """Map settle procedure stage_status to a terminal TaskRuntime outcome.

        R184/M06: never return suspended for factory-owned settle helper claims.
        """

        normalized = str(stage_status or "").strip().lower()
        if normalized in {"success", "completed", "ok", "passed"}:
            return "completed"
        return "failed"

    def _settle_director_stage_materialization_attempt(
        self,
        *,
        task_row_id: int,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        stage_status: str,
        summary: str,
    ) -> dict[str, Any]:
        return materialization_impl._settle_director_stage_materialization_attempt(
            self,
            task_row_id=task_row_id,
            execution_attempt=execution_attempt,
            stage_status=stage_status,
            summary=summary,
        )
