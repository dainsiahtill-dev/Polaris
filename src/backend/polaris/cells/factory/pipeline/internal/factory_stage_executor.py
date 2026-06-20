"""Production factory stage executor backed by ``OrchestrationCommandService``.

Holds the standalone ``OrchestrationStageExecutor`` god-class extracted from
``factory_run_service``. Behavior is preserved verbatim: this module imports
the shared data-contracts and tuning constants from ``factory_run_models`` and
keeps all cross-cell edges lazy (in-function) exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.orchestration.orchestration_engine.public.service import OrchestrationCommandService

from polaris.cells.chief_engineer.blueprint.public import GenerateTaskBlueprintCommandV1, generate_task_blueprint
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleTaskCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

from . import factory_stage_helpers as helpers
from .factory_artifact_store import ArtifactStore
from .factory_run_completion import RunCompletionWaiter
from .factory_run_models import (
    _PM_ARCHITECT_DOC_MAX_CHARS,
    _PM_DIRECTIVE_MAX_CHARS,
    _PM_ORIGINAL_DIRECTIVE_MAX_CHARS,
    _QA_LLM_JUDGEMENT_UNAVAILABLE_WARNING,
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    _WORKSPACE_VALIDATION_TIMEOUT_SECONDS,
    FactoryRun,
    StageResult,
)
from .factory_workspace_quality import WorkspaceQualityRunner

logger = logging.getLogger(__name__)


class OrchestrationStageExecutor:
    """Production executor backed by OrchestrationCommandService."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self._fs = KernelFileSystem(str(workspace), get_default_adapter())
        self._artifact_store = ArtifactStore(self.workspace, self._fs)
        self._workspace_quality = WorkspaceQualityRunner(self.workspace)
        self._run_completion_waiter = RunCompletionWaiter(self.workspace)

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        handlers = {
            "docs_generation": self._execute_docs_generation,
            "pm_planning": self._execute_pm_planning,
            "chief_engineer_review": self._execute_chief_engineer_review,
            "director_dispatch": self._execute_director_dispatch,
            "quality_gate": self._execute_quality_gate,
        }
        handler = handlers.get(stage)
        if handler is None:
            return StageResult(stage=stage, status="skipped", output="No handler for this stage")
        return await handler(run, context)

    def _artifact_path(self, relative_path: str) -> Path:
        return self._artifact_store.artifact_path(relative_path)

    def _write_json_artifact(self, relative_path: str, payload: dict[str, Any]) -> Path:
        return self._artifact_store.write_json_artifact(relative_path, payload)

    def _write_text_artifact(self, relative_path: str, content: str) -> Path:
        return self._artifact_store.write_text_artifact(relative_path, content)

    def _write_stage_signal_artifact(
        self,
        *,
        stage: str,
        run_id: str,
        signals: list[dict[str, Any]],
    ) -> str:
        return self._artifact_store.write_stage_signal_artifact(stage=stage, run_id=run_id, signals=signals)

    def _copy_text_artifact(self, source_relative_path: str, target_relative_path: str) -> str:
        return self._artifact_store.copy_text_artifact(source_relative_path, target_relative_path)

    def _copy_text_artifact_if_present(
        self,
        source_relative_path: str,
        target_relative_path: str,
        *,
        min_chars: int = 1,
    ) -> str:
        return self._artifact_store.copy_text_artifact_if_present(
            source_relative_path, target_relative_path, min_chars=min_chars
        )

    def _read_text_artifact(self, relative_path: str, *, min_chars: int = 1) -> str:
        return self._artifact_store.read_text_artifact(relative_path, min_chars=min_chars)

    def _emit_audit_event(self, event_type: str, **kwargs: Any) -> None:
        """Emit an audit event for tracking purposes."""
        self._artifact_store.emit_audit_event(event_type, **kwargs)

    @staticmethod
    def _extend_artifacts(artifacts: list[str], *paths: str) -> None:
        helpers.extend_artifacts(artifacts, *paths)

    @staticmethod
    def _normalize_declared_delivery_target(value: Any) -> str:
        return helpers.normalize_declared_delivery_target(value)

    @classmethod
    def _collect_declared_delivery_targets(cls, tasks: list[dict[str, Any]]) -> list[str]:
        return helpers.collect_declared_delivery_targets(tasks)

    def _missing_declared_delivery_targets(self, tasks: list[dict[str, Any]]) -> list[str]:
        workspace_root = self.workspace.resolve()
        missing: list[str] = []
        for target in self._collect_declared_delivery_targets(tasks):
            path = (workspace_root / target).resolve()
            try:
                path.relative_to(workspace_root)
            except ValueError:
                missing.append(target)
                continue
            if not path.exists():
                missing.append(target)
                continue
            if path.is_file():
                try:
                    if path.stat().st_size <= 0:
                        missing.append(target)
                except OSError:
                    missing.append(target)
        return missing

    def _mirror_docs_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        self._artifact_store.mirror_docs_artifacts(run_id, artifacts)

    def _mirror_pm_plan_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        self._artifact_store.mirror_pm_plan_artifacts(run_id, artifacts)

    def _mirror_chief_engineer_artifacts(
        self,
        run_id: str,
        blueprint_rows: list[dict[str, Any]],
        review_artifact: str,
        artifacts: list[str],
    ) -> None:
        self._artifact_store.mirror_chief_engineer_artifacts(run_id, blueprint_rows, review_artifact, artifacts)

    def _mirror_director_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        self._artifact_store.mirror_director_artifacts(run_id, artifacts)

    def _mirror_quality_gate_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        self._artifact_store.mirror_quality_gate_artifacts(run_id, artifacts)

    def _workspace_package_has_external_dependencies(self) -> bool:
        return self._workspace_quality.workspace_package_has_external_dependencies()

    def _workspace_quality_prepare_commands(
        self,
        commands: list[list[str]],
        context: dict[str, Any],
    ) -> list[list[str]]:
        return self._workspace_quality.workspace_quality_prepare_commands(commands, context)

    @staticmethod
    def _artifact_file_ready(target: Path) -> bool:
        """Return whether an expected stage artifact is present after upstream completion."""
        return helpers.artifact_file_ready(target)

    def _artifact_exists(self, relative_path: str, *, min_chars: int = 1) -> bool:
        target = self._artifact_path(relative_path)
        if not target.exists() or not target.is_file():
            return False
        if min_chars <= 0:
            return True
        try:
            return len(target.read_text(encoding="utf-8").strip()) >= min_chars
        except OSError:
            return False

    def _missing_artifacts(self, artifacts: list[str], *, min_chars: int = 1) -> list[str]:
        return [item for item in artifacts if not self._artifact_exists(item, min_chars=min_chars)]

    @staticmethod
    def _is_substantive_doc_text(text: str, *, min_chars: int = 200) -> bool:
        return helpers.is_substantive_doc_text(text, min_chars=min_chars)

    def _ensure_docs_artifacts(
        self,
        *,
        directive: str,
        summary: str,
    ) -> list[str]:
        expected = ["docs/plan.md", "docs/architecture.md"]
        missing = self._missing_artifacts(expected, min_chars=120)
        if not missing:
            return []

        design_path = self._artifact_path("docs/design.md")
        design_text = ""
        if design_path.exists() and design_path.is_file():
            try:
                design_text = design_path.read_text(encoding="utf-8").strip()
            except OSError:
                design_text = ""
        if design_text and not self._is_substantive_doc_text(design_text):
            design_text = ""

        for rel in list(missing):
            if self._artifact_exists(rel, min_chars=120):
                continue
            if design_text:
                header = "# 项目计划\n" if rel.endswith("plan.md") else "# 架构设计\n"
                self._write_text_artifact(
                    rel,
                    "\n".join(
                        [
                            header,
                            "",
                            f"来源: docs/design.md ({datetime.now(timezone.utc).isoformat()})",
                            "",
                            design_text,
                            "",
                        ]
                    ),
                )
        return self._missing_artifacts(expected, min_chars=120)

    def _validate_pm_plan_contract(self, relative_path: str = "tasks/plan.json") -> str:
        target = self._artifact_path(relative_path)
        if not target.exists():
            return "missing_tasks_plan"
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return "tasks_plan_invalid_json"
        if not isinstance(payload, dict):
            return "tasks_plan_invalid_type"
        tasks = payload.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return "tasks_plan_empty_tasks"
        invalid = 0
        meta_diagnostic = 0
        for item in tasks:
            if not isinstance(item, dict):
                invalid += 1
                continue
            goal = str(item.get("goal") or item.get("title") or "").strip()
            scope = str(item.get("scope") or "").strip()
            steps = item.get("steps")
            acceptance = item.get("acceptance") or item.get("acceptance_criteria")
            has_steps = isinstance(steps, list) and len([s for s in steps if str(s).strip()]) > 0
            has_acceptance = isinstance(acceptance, list) and len([s for s in acceptance if str(s).strip()]) > 0
            if not (goal and scope and has_steps and has_acceptance):
                invalid += 1
            if self._is_pm_meta_diagnostic_task(item):
                meta_diagnostic += 1
        if invalid > 0:
            return f"tasks_plan_invalid_contract:{invalid}"
        if meta_diagnostic > 0:
            return f"tasks_plan_meta_diagnostic_tasks:{meta_diagnostic}"
        return ""

    @staticmethod
    def _is_pm_meta_diagnostic_task(task: dict[str, Any]) -> bool:
        return helpers.is_pm_meta_diagnostic_task(task)

    def _load_pm_plan_tasks(self, relative_path: str = "tasks/plan.json") -> list[dict[str, Any]]:
        target = self._artifact_path(relative_path)
        if not target.exists():
            return []
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            return []
        return [item for item in tasks if isinstance(item, dict)]

    @staticmethod
    def _compact_text_for_prompt(text: str, *, max_chars: int) -> str:
        return helpers.compact_text_for_prompt(text, max_chars=max_chars)

    @staticmethod
    def _strip_prompt_meta_lines(text: str) -> str:
        return helpers.strip_prompt_meta_lines(text)

    def _build_pm_planning_directive(self, raw_directive: Any) -> str:
        user_directive = self._strip_prompt_meta_lines(str(raw_directive or "").strip())
        sections = [
            "请基于 Architect 阶段产物生成 PM 执行任务合同。任务必须覆盖需求、实现、验证、QA 闭环；"
            "每个任务必须包含 goal、scope、steps、acceptance、depends_on，并能交给 Director 直接执行。"
        ]
        for rel_path, label in (
            ("docs/plan.md", "Architect Plan"),
            ("docs/architecture.md", "Architect Architecture"),
            ("docs/design.md", "Architect Design"),
        ):
            doc_text = self._read_text_artifact(rel_path, min_chars=120)
            if not doc_text:
                continue
            sections.extend(
                [
                    "",
                    f"## {label}",
                    self._compact_text_for_prompt(doc_text, max_chars=_PM_ARCHITECT_DOC_MAX_CHARS),
                ]
            )
        if user_directive:
            sections.extend(
                [
                    "",
                    "## Original Requirement Excerpt",
                    self._compact_text_for_prompt(user_directive, max_chars=_PM_ORIGINAL_DIRECTIVE_MAX_CHARS),
                ]
            )
        compacted = "\n".join(sections).strip()
        return self._compact_text_for_prompt(compacted, max_chars=_PM_DIRECTIVE_MAX_CHARS)

    def _build_director_task_filter(self, tasks: list[dict[str, Any]]) -> str:
        return helpers.build_director_task_filter(tasks)

    @staticmethod
    def _task_string(task: dict[str, Any], *keys: str) -> str:
        return helpers.task_string(task, *keys)

    @staticmethod
    def _task_string_list(task: dict[str, Any], *keys: str) -> list[str]:
        return helpers.task_string_list(task, *keys)

    def _task_id(self, task: dict[str, Any], index: int) -> str:
        return self._task_string(task, "id", "task_id", "uid") or f"task-{index}"

    def _task_objective(self, task: dict[str, Any]) -> str:
        return (
            self._task_string(task, "goal", "objective", "title", "subject", "description")
            or "Prepare Director implementation blueprint"
        )

    def _task_blueprint_context(self, task: dict[str, Any], *, run_id: str, index: int) -> dict[str, Any]:
        context = dict(task)
        context["source_artifact"] = "tasks/plan.json"
        context["factory_run_id"] = run_id
        context["task_index"] = index
        title = self._task_string(task, "title", "subject", "goal")
        if title:
            context["task_title"] = title
        scope = self._task_string(task, "scope")
        if scope:
            context.setdefault("scope_paths", [scope])
        return context

    def _task_blueprint_constraints(self, task: dict[str, Any]) -> dict[str, Any]:
        constraints: dict[str, Any] = {}
        acceptance = self._task_string_list(task, "acceptance", "acceptance_criteria")
        steps = self._task_string_list(task, "steps")
        scope = self._task_string(task, "scope")
        if acceptance:
            constraints["acceptance"] = acceptance
        if steps:
            constraints["steps"] = steps
        if scope:
            constraints["scope"] = scope
        return constraints

    def _read_taskboard_stats(self) -> dict[str, int]:
        baseline = {
            "total": 0,
            "pending": 0,
            "ready": 0,
            "in_progress": 0,
            "completed": 0,
            "failed": 0,
            "blocked": 0,
        }
        try:
            payload = TaskRuntimeService(str(self.workspace)).get_stats()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return baseline
        if not isinstance(payload, dict):
            return baseline
        for key in tuple(baseline.keys()):
            try:
                baseline[key] = int(payload.get(key) or 0)
            except (TypeError, ValueError):
                baseline[key] = 0
        return baseline

    @staticmethod
    def _is_taskboard_converged(stats: dict[str, int]) -> bool:
        return helpers.is_taskboard_converged(stats)

    @staticmethod
    def _has_director_progress(before: dict[str, int], after: dict[str, int]) -> bool:
        return helpers.has_director_progress(before, after)

    @staticmethod
    def _has_director_execution_evidence(
        *,
        attempts: list[dict[str, Any]],
        initial_stats: dict[str, int],
        final_stats: dict[str, int],
        converged: bool,
    ) -> bool:
        return helpers.has_director_execution_evidence(
            attempts=attempts,
            initial_stats=initial_stats,
            final_stats=final_stats,
            converged=converged,
        )

    @staticmethod
    def _metadata_indicates_execution(metadata: dict[str, Any]) -> bool:
        return helpers.metadata_indicates_execution(metadata)

    async def _execute_docs_generation(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing docs generation for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)

        service = self._build_orchestration_service(context)
        command_result = await service.execute_pm_run(
            workspace=str(self.workspace),
            run_type="architect",
            options={
                "directive": context.get("directive", "Generate project documentation"),
                "run_director": False,
            },
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
        planning_directive = self._build_pm_planning_directive(
            context.get("directive", "Plan implementation tasks"),
        )
        reset_summary = TaskRuntimeService(str(self.workspace)).reset_records(keep_plan=True)

        service = self._build_orchestration_service(context)
        command_result = await service.execute_pm_run(
            workspace=str(self.workspace),
            run_type="pm",
            options={
                "directive": planning_directive,
                "run_director": False,
            },
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
        if str(final_result.status or "").strip().lower() == "timeout" and not self._artifact_exists(
            "tasks/plan.json", min_chars=1
        ):
            recovery_result = await self._run_pm_planning_deterministic_recovery(
                service=service,
                planning_directive=planning_directive,
                context=context,
                abort_checker=abort_checker,
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
        contract_issue = self._validate_pm_plan_contract("tasks/plan.json")
        if contract_issue:
            stage_signals.append(
                {
                    "code": "pm.contract_issue_detected",
                    "severity": "error",
                    "detail": contract_issue,
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
    ) -> CommandResult:
        recovery_timeout = int(context.get("pm_recovery_timeout", 120))
        command_result = await service.execute_pm_run(
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
        )
        return await self._wait_run_completion(
            service,
            command_result,
            timeout_seconds=recovery_timeout,
            cancel_event=self._resolve_cancel_event(context),
            abort_checker=abort_checker,
        )

    async def _execute_chief_engineer_review(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing Chief Engineer review for run %s", run.id)
        del context

        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        stage_signals: list[dict[str, Any]] = []
        blueprint_rows: list[dict[str, Any]] = []

        if not pm_tasks:
            stage_signals.append(
                {
                    "code": "chief_engineer.plan_missing",
                    "severity": "error",
                    "detail": "tasks/plan.json missing or empty tasks array",
                }
            )

        # Use RoleRuntimeService for real LLM invocation
        ce_service = RoleRuntimeService()

        for index, task in enumerate(pm_tasks, start=1):
            task_id = self._task_id(task, index)
            objective = self._task_objective(task)
            try:
                # Build command for RoleRuntimeService
                command = ExecuteRoleTaskCommandV1(
                    role="chief_engineer",
                    task_id=task_id,
                    workspace=str(self.workspace),
                    objective=objective,
                    run_id=run.id,
                    context=self._task_blueprint_context(task, run_id=run.id, index=index),
                    metadata={
                        "constraints": self._task_blueprint_constraints(task),
                        "source": "factory_stage_executor.chief_engineer_review",
                    },
                )

                # Execute via RoleRuntimeService (real LLM call)
                ce_result = await ce_service.execute_role_task(command)

                # Check if CE LLM call succeeded (fail-closed)
                if not ce_result.ok:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.llm_review_failed",
                            "severity": "error",
                            "detail": ce_result.error_message or ce_result.error_code or "CE LLM call failed",
                            "task_id": task_id,
                            "provider": getattr(ce_result, "provider", "unknown"),
                            "model": getattr(ce_result, "model", "unknown"),
                        }
                    )
                    continue

                # Extract LLM evidence from ce_result
                ce_provider = getattr(ce_result, "provider", "unknown")
                ce_model = getattr(ce_result, "model", "unknown")
                ce_cache_hit = getattr(ce_result, "cache_hit", False)

                # Emit audit event for LLM call
                self._emit_audit_event(
                    "chief_engineer.llm_call",
                    provider=ce_provider,
                    model=ce_model,
                    cache_hit=ce_cache_hit,
                    task_id=task_id,
                    run_id=run.id,
                )

                # Convert to blueprint result format (deterministic structure generator)
                result = generate_task_blueprint(
                    GenerateTaskBlueprintCommandV1(
                        task_id=task_id,
                        workspace=str(self.workspace),
                        objective=objective,
                        run_id=run.id,
                        constraints=self._task_blueprint_constraints(task),
                        context=self._task_blueprint_context(task, run_id=run.id, index=index),
                    )
                )

            except (RuntimeError, TypeError, ValueError) as exc:
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

            blueprint_rows.append(
                {
                    "task_id": result.task_id,
                    "status": result.status,
                    "blueprint_id": result.blueprint_id,
                    "blueprint_path": result.blueprint_path,
                    "summary": result.summary,
                    "recommendations": list(result.recommendations),
                    "risks": list(result.risks),
                    "llm_evidence": {
                        "provider": ce_provider,
                        "model": ce_model,
                        "cache_hit": ce_cache_hit,
                        "role": "chief_engineer",
                        "task_id": task_id,
                        "run_id": run.id,
                    },
                }
            )

        review_artifact = ""
        if blueprint_rows or stage_signals:
            review_artifact = f"runtime/state/blueprints/{run.id}.review.json"
            self._write_json_artifact(
                review_artifact,
                {
                    "schema_version": "factory.chief_engineer_review.v1",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source": "factory_stage_executor",
                    "factory_run_id": run.id,
                    "task_plan": "tasks/plan.json",
                    "total_tasks": len(pm_tasks),
                    "generated_blueprints": len(blueprint_rows),
                    "blueprints": blueprint_rows,
                    "signals": stage_signals,
                },
            )

        stage_signal_path = ""
        if stage_signals:
            stage_signal_path = self._write_stage_signal_artifact(
                stage="chief_engineer_review",
                run_id=run.id,
                signals=stage_signals,
            )

        has_errors = any(
            str(item.get("severity") or "").strip().lower() == "error"
            for item in stage_signals
            if isinstance(item, dict)
        )
        stage_status = "failed" if has_errors else "success"
        artifacts = [row["blueprint_path"] for row in blueprint_rows if row.get("blueprint_path")]
        if review_artifact:
            artifacts.append(review_artifact)
        self._mirror_chief_engineer_artifacts(run.id, blueprint_rows, review_artifact, artifacts)
        if stage_signal_path:
            artifacts.append(stage_signal_path)

        error_code = ""
        root_cause_hint = ""
        if has_errors:
            for signal in stage_signals:
                if str(signal.get("severity") or "").strip().lower() != "error":
                    continue
                error_code = str(signal.get("code") or "").strip()
                root_cause_hint = str(signal.get("detail") or "").strip()
                if error_code:
                    break

        return StageResult(
            stage="chief_engineer_review",
            status=stage_status,
            output=(
                f"Chief Engineer review generated {len(blueprint_rows)}/{len(pm_tasks)} blueprints; "
                f"signals={len(stage_signals)}; "
                f"error_code={error_code or 'none'}; root_cause_hint={root_cause_hint or 'none'}"
            ),
            artifacts=artifacts,
        )

    async def _execute_director_dispatch(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing Director dispatch for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)

        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        plan_task_filter = self._build_director_task_filter(pm_tasks)
        configured_task_filter = str(context.get("task_filter") or "").strip()
        effective_task_filter = configured_task_filter or plan_task_filter

        service = self._build_orchestration_service(context)
        stage_signals: list[dict[str, Any]] = []
        initial_stats = self._read_taskboard_stats()
        attempts: list[dict[str, Any]] = []
        last_command_result: CommandResult | None = None
        final_result: CommandResult | None = None
        max_rounds = int(context.get("director_max_rounds") or 0)
        if max_rounds <= 0:
            dynamic_rounds = (
                int(initial_stats.get("pending") or 0)
                + int(initial_stats.get("ready") or 0)
                + int(initial_stats.get("in_progress") or 0)
                + 2
            )
            max_rounds = max(2, min(dynamic_rounds, 12))
        idle_budget = max(1, int(context.get("director_idle_budget") or 2))
        idle_rounds = 0
        requires_taskboard_convergence = True

        # Enforce mainline-full: no silent single-worker fallback
        execution_mode = str(context.get("execution_mode", "parallel")).strip().lower()
        if execution_mode not in ("parallel", "serial"):
            stage_signals.append(
                {
                    "code": "director.invalid_execution_mode",
                    "severity": "error",
                    "detail": f"Invalid execution_mode: {execution_mode}; must be 'parallel' or 'serial'",
                }
            )
            execution_mode = "parallel"

        # Enforce worker count matches configured bindings
        max_workers = int(context.get("max_workers", DEFAULT_DIRECTOR_MAX_PARALLELISM))
        if max_workers < 1:
            stage_signals.append(
                {
                    "code": "director.invalid_worker_count",
                    "severity": "error",
                    "detail": f"Invalid max_workers: {max_workers}; must be >= 1",
                }
            )
            max_workers = DEFAULT_DIRECTOR_MAX_PARALLELISM

        if not pm_tasks:
            stage_signals.append(
                {
                    "code": "director.task_lineage_missing",
                    "severity": "error",
                    "detail": "tasks/plan.json missing or empty tasks array",
                }
            )
        if int(initial_stats.get("total") or 0) <= 0:
            stage_signals.append(
                {
                    "code": "director.taskboard_empty",
                    "severity": "error",
                    "detail": "TaskBoard has no executable task records",
                }
            )

        if not stage_signals:
            for round_index in range(1, max_rounds + 1):
                before_stats = self._read_taskboard_stats()
                if self._is_taskboard_converged(before_stats):
                    stage_signals.append(
                        {
                            "code": "director.already_converged",
                            "severity": "info",
                            "detail": "TaskBoard already converged before dispatch round",
                            "round": round_index,
                        }
                    )
                    final_result = CommandResult(
                        run_id="",
                        status="completed",
                        message="TaskBoard already converged",
                        metadata={"task_status_counts": dict(before_stats)},
                    )
                    break

                command_result = await service.execute_director_run(
                    workspace=str(self.workspace),
                    tasks=context.get("tasks"),
                    options={
                        "task_filter": effective_task_filter,
                        "max_workers": max_workers,
                        "execution_mode": execution_mode,
                        "dispatch_mode": "mainline-full",  # Enforce mainline-full, no fallback
                    },
                )
                last_command_result = command_result
                director_result = await self._wait_run_completion(
                    service,
                    command_result,
                    timeout_seconds=int(context.get("timeout", 600)),
                    cancel_event=self._resolve_cancel_event(context),
                    abort_checker=abort_checker,
                )
                final_result = director_result
                if str(director_result.status or "").strip().lower() == "cancelled":
                    break

                after_stats = self._read_taskboard_stats()
                metadata_payload = director_result.metadata if isinstance(director_result.metadata, dict) else {}
                metadata_progress = self._metadata_indicates_execution(metadata_payload)
                progress_made = self._has_director_progress(before_stats, after_stats) or metadata_progress
                attempt_entry = {
                    "round": round_index,
                    "run_id": str(command_result.run_id or "").strip(),
                    "status": str(director_result.status or "").strip(),
                    "message": str(director_result.message or "").strip(),
                    "metadata": metadata_payload,
                    "taskboard_before": before_stats,
                    "taskboard_after": after_stats,
                    "progress_made": progress_made,
                    "metadata_progress": metadata_progress,
                }
                attempts.append(attempt_entry)

                if director_result.status not in {"completed", "success"}:
                    prior_successful_progress = any(
                        str(item.get("status") or "").strip().lower() in {"completed", "success"}
                        and bool(item.get("progress_made"))
                        for item in attempts[:-1]
                        if isinstance(item, dict)
                    )
                    if self._is_director_no_materialized_changes(director_result) and (prior_successful_progress):
                        missing_delivery_targets = self._missing_declared_delivery_targets(pm_tasks)
                        if missing_delivery_targets:
                            stage_signals.append(
                                {
                                    "code": "director.no_materialized_changes_missing_targets",
                                    "severity": "error",
                                    "detail": (
                                        "Director reported no materialized changes while declared delivery targets "
                                        f"are still missing: {', '.join(missing_delivery_targets[:8])}"
                                    ),
                                    "missing_targets": missing_delivery_targets,
                                    "declared_target_count": len(self._collect_declared_delivery_targets(pm_tasks)),
                                    "upstream_status": str(director_result.status or "").strip(),
                                    "round": round_index,
                                }
                            )
                            break
                        requires_taskboard_convergence = False
                        stage_signals.append(
                            {
                                "code": "director.idempotent_no_materialized_changes",
                                "severity": "info",
                                "detail": (
                                    "Director reported no materialized changes after prior execution evidence; "
                                    "treating dispatch as idempotent and allowing QA to decide final quality"
                                ),
                                "requires_taskboard_convergence": False,
                                "upstream_status": str(director_result.status or "").strip(),
                                "round": round_index,
                            }
                        )
                        final_result = CommandResult(
                            run_id=str(director_result.run_id or command_result.run_id or ""),
                            status="completed",
                            message=(
                                "Director made no further materialized changes after prior evidence; "
                                "dispatch treated as idempotent"
                            ),
                            metadata=metadata_payload,
                        )
                        break
                    stage_signals.append(
                        {
                            "code": "director.run_status_non_success",
                            "severity": "error",
                            "detail": str(director_result.message or "").strip()
                            or str(director_result.status or "unknown"),
                            "upstream_status": str(director_result.status or "").strip(),
                            "round": round_index,
                        }
                    )
                    break

                if progress_made:
                    idle_rounds = 0
                else:
                    idle_rounds += 1
                    stage_signals.append(
                        {
                            "code": "director.no_progress_round",
                            "severity": "warning",
                            "detail": f"No TaskBoard progress in dispatch round {round_index}",
                            "round": round_index,
                            "idle_rounds": idle_rounds,
                        }
                    )

                if self._is_taskboard_converged(after_stats):
                    stage_signals.append(
                        {
                            "code": "director.dispatch_converged",
                            "severity": "info",
                            "detail": f"Director dispatch converged in {round_index} rounds",
                            "round": round_index,
                        }
                    )
                    break

                if metadata_progress:
                    stage_signals.append(
                        {
                            "code": "director.dispatch_evidence_confirmed",
                            "severity": "info",
                            "detail": f"Director execution evidence confirmed in round {round_index}",
                            "round": round_index,
                        }
                    )

                if idle_rounds > idle_budget:
                    stage_signals.append(
                        {
                            "code": "director.dispatch_stalled",
                            "severity": "error",
                            "detail": (
                                "Director dispatch exceeded idle progress budget; "
                                f"idle_rounds={idle_rounds}, idle_budget={idle_budget}"
                            ),
                            "round": round_index,
                        }
                    )
                    break

        final_stats = self._read_taskboard_stats()
        converged = self._is_taskboard_converged(final_stats)
        execution_evidence_ok = self._has_director_execution_evidence(
            attempts=attempts,
            initial_stats=initial_stats,
            final_stats=final_stats,
            converged=converged,
        )

        stage_status = "success"
        if (
            str((final_result or CommandResult(run_id="", status="", message="")).status or "").strip().lower()
            == "cancelled"
        ):
            stage_status = "cancelled"
        elif any(
            str(item.get("severity") or "").strip().lower() == "error"
            for item in stage_signals
            if isinstance(item, dict)
        ):
            stage_status = "failed"
        elif not attempts and not converged:
            stage_status = "failed"
            stage_signals.append(
                {
                    "code": "director.no_dispatch_attempt",
                    "severity": "error",
                    "detail": "No director dispatch attempt executed before stage termination",
                }
            )
        elif not execution_evidence_ok:
            stage_status = "failed"
            stage_signals.append(
                {
                    "code": "director.execution_evidence_missing",
                    "severity": "error",
                    "detail": "No valid director execution evidence found from taskboard or run metadata",
                }
            )
        elif requires_taskboard_convergence and not converged:
            stage_status = "failed"
            stage_signals.append(
                {
                    "code": "director.taskboard_not_converged",
                    "severity": "error",
                    "detail": f"TaskBoard not converged after dispatch rounds; final_stats={final_stats}",
                }
            )

        error_code = ""
        root_cause_hint = ""
        for signal in stage_signals:
            if not isinstance(signal, dict):
                continue
            if str(signal.get("severity") or "").strip().lower() != "error":
                continue
            error_code = str(signal.get("code") or "").strip()
            root_cause_hint = str(signal.get("detail") or "").strip()
            if error_code:
                break

        stage_signal_path = ""
        if stage_signals:
            stage_signal_path = self._write_stage_signal_artifact(
                stage="director_dispatch",
                run_id=run.id,
                signals=stage_signals,
            )

        dispatch_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "factory_run_id": run.id,
            "orchestration_run_id": str((last_command_result.run_id if last_command_result else "") or "").strip(),
            "status": str((final_result.status if final_result else stage_status) or "").strip(),
            "message": str((final_result.message if final_result else "") or "").strip(),
            "metadata": final_result.metadata if (final_result and isinstance(final_result.metadata, dict)) else {},
            "taskboard": {
                "initial": initial_stats,
                "final": final_stats,
                "converged": converged,
                "requires_convergence": requires_taskboard_convergence,
            },
            "attempts": attempts,
            "signals": stage_signals,
            "failure_stage": "director_dispatch" if stage_status == "failed" else "",
            "error_code": error_code or None,
            "root_cause_hint": root_cause_hint or None,
            "evidence_paths": {
                "plan": "tasks/plan.json" if self._artifact_exists("tasks/plan.json", min_chars=1) else "",
                "dispatch_log": "dispatch/log.json",
                "stage_signals": stage_signal_path,
            },
        }
        self._write_json_artifact("dispatch/log.json", dispatch_payload)
        artifacts = ["dispatch/log.json"]
        self._mirror_director_artifacts(run.id, artifacts)
        if stage_signal_path:
            artifacts.append(stage_signal_path)
        if stage_status == "cancelled":
            return StageResult(
                stage="director_dispatch",
                status="cancelled",
                output=f"Director dispatch cancelled: {(final_result.message if final_result else 'N/A')}",
                artifacts=artifacts,
            )
        return StageResult(
            stage="director_dispatch",
            status=stage_status,
            output=(
                f"Director dispatch {(final_result.status if final_result else 'unknown')}: "
                f"{(final_result.message if final_result else 'N/A')}; "
                f"signals={len(stage_signals)}; "
                f"error_code={error_code or 'none'}; root_cause_hint={root_cause_hint or 'none'}"
            ),
            artifacts=artifacts,
        )

    @staticmethod
    def _is_director_no_materialized_changes(result: CommandResult) -> bool:
        return helpers.is_director_no_materialized_changes(result)

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
    def _trim_command_output(text: str, limit: int = _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS) -> str:
        return helpers.trim_command_output(text, limit)

    def _run_workspace_quality_command(self, command: list[str], timeout_seconds: float) -> dict[str, Any]:
        return self._workspace_quality.run_command(command, timeout_seconds)

    @staticmethod
    def _resolve_workspace_quality_command(command: list[str]) -> list[str]:
        return helpers.resolve_workspace_quality_command(command)

    async def _run_workspace_quality_checks(self, run: FactoryRun, context: dict[str, Any]) -> tuple[bool, str]:
        commands = self._workspace_quality_commands(context)
        if not commands:
            return True, ""

        timeout_seconds = float(
            context.get("workspace_validation_timeout_seconds") or _WORKSPACE_VALIDATION_TIMEOUT_SECONDS
        )
        results: list[dict[str, Any]] = []
        prepare_commands = self._workspace_quality_prepare_commands(commands, context)
        prepare_failed = False
        for command in prepare_commands:
            result = await asyncio.to_thread(self._run_workspace_quality_command, command, timeout_seconds)
            result["phase"] = "prepare"
            results.append(result)
            if not bool(result.get("passed")):
                prepare_failed = True

        run_commands = [] if prepare_failed else commands
        for command in run_commands:
            result = await asyncio.to_thread(self._run_workspace_quality_command, command, timeout_seconds)
            result["phase"] = "check"
            results.append(result)
        if prepare_failed:
            for command in commands:
                results.append(
                    {
                        "command": command,
                        "phase": "check",
                        "exit_code": None,
                        "passed": False,
                        "error": "skipped because workspace validation preparation failed",
                        "stdout_tail": "",
                        "stderr_tail": "",
                    }
                )

        payload = {
            "schema_version": "factory.workspace_quality_checks.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "factory_run_id": run.id,
            "workspace": str(self.workspace),
            "passed": all(bool(item.get("passed")) for item in results),
            "commands": results,
        }
        artifact = "runtime/qa/workspace-validation.json"
        self._write_json_artifact(artifact, payload)
        return bool(payload["passed"]), artifact

    @staticmethod
    def _qa_report_has_warning(payload: dict[str, Any], warning: str) -> bool:
        return helpers.qa_report_has_warning(payload, warning)

    async def _execute_quality_gate(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing quality gate for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)

        service = self._build_orchestration_service(context)
        command_result = await service.execute_qa_run(
            workspace=str(self.workspace),
            target=context.get("qa_target", "Quality gate"),
            options={
                "input": context.get("qa_input"),
            },
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
                stage="quality_gate",
                status="cancelled",
                output=f"Quality gate cancelled: {final_result.message or 'N/A'}",
                artifacts=[],
            )

        qa_report_path = self._artifact_path("runtime/qa/report.json")
        if not self._artifact_file_ready(qa_report_path):
            raise RuntimeError(f"Quality gate report missing: {qa_report_path}")
        loaded: dict[str, Any] | Any = {}
        parse_error: Exception | None = None
        for _attempt in range(5):
            try:
                report_text = await asyncio.to_thread(qa_report_path.read_text, encoding="utf-8")
                loaded = json.loads(report_text)
                parse_error = None
                break
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                parse_error = exc
                await asyncio.sleep(0.2)
        if parse_error is not None:
            raise RuntimeError(f"Quality gate report parse failed: {qa_report_path}") from parse_error
        if not isinstance(loaded, dict):
            raise RuntimeError(f"Quality gate report payload must be JSON object: {qa_report_path}")
        qa_payload: dict[str, Any] = loaded

        qa_passed = bool(qa_payload.get("passed"))
        qa_score = int(qa_payload.get("score") or 0)
        qa_critical = int(qa_payload.get("critical_issue_count") or 0)
        qa_llm_required = self._bool_from_context_or_env(
            context,
            "qa_require_llm_judgement",
            "require_qa_llm_judgement",
            "factory_require_qa_llm_judgement",
            env_var="POLARIS_FACTORY_QA_REQUIRE_LLM_JUDGEMENT",
            default=True,
        )
        qa_llm_judgement_ready = not self._qa_report_has_warning(qa_payload, _QA_LLM_JUDGEMENT_UNAVAILABLE_WARNING)
        workspace_checks_passed, workspace_checks_artifact = await self._run_workspace_quality_checks(run, context)
        is_success = (
            final_result.status in {"completed", "success"}
            and qa_passed
            and workspace_checks_passed
            and (qa_llm_judgement_ready or not qa_llm_required)
        )
        output_suffix = (
            f"qa_passed={qa_passed}; qa_score={qa_score}; qa_critical={qa_critical}; "
            f"workspace_checks_passed={workspace_checks_passed}; "
            f"qa_llm_required={qa_llm_required}; qa_llm_judgement_ready={qa_llm_judgement_ready}"
        )
        if qa_llm_required and not qa_llm_judgement_ready:
            output_suffix = f"{output_suffix}; qa_gate_blocker={_QA_LLM_JUDGEMENT_UNAVAILABLE_WARNING}"
        artifacts = ["runtime/qa/report.json"]
        if workspace_checks_artifact:
            artifacts.append(workspace_checks_artifact)
        self._mirror_quality_gate_artifacts(run.id, artifacts)
        return StageResult(
            stage="quality_gate",
            status="success" if is_success else "failed",
            output=(f"Quality gate {final_result.status}: {final_result.message or 'N/A'}; {output_suffix}"),
            artifacts=artifacts,
        )

    def _build_orchestration_service(self, context: dict[str, Any]) -> Any:
        return self._run_completion_waiter.build_orchestration_service(context)

    async def _wait_run_completion(
        self,
        service: OrchestrationCommandService,
        initial_result: CommandResult,
        timeout_seconds: int = 300,
        *,
        cancel_event: asyncio.Event | None = None,
        abort_checker: Callable[[], Awaitable[str | None]] | None = None,
    ) -> CommandResult:
        return await self._run_completion_waiter.wait(
            service,
            initial_result,
            timeout_seconds,
            cancel_event=cancel_event,
            abort_checker=abort_checker,
        )

    @staticmethod
    def _resolve_cancel_event(context: dict[str, Any]) -> asyncio.Event | None:
        return RunCompletionWaiter.resolve_cancel_event(context)

    @staticmethod
    def _resolve_abort_checker(
        context: dict[str, Any],
    ) -> Callable[[], Awaitable[str | None]] | None:
        return RunCompletionWaiter.resolve_abort_checker(context)
