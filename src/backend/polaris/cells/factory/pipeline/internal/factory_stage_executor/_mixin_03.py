"""Private mixin _Mixin03 for OrchestrationStageExecutor."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.orchestration.orchestration_engine.public.service import OrchestrationCommandService

from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.constants import (
    MAX_LLM_PROVIDER_TIMEOUT_SECONDS,  # noqa: F401 — re-exported for characterization-test surface
)

from .. import (
    factory_deadline_calculations as deadline_calc,
    factory_materialization_impl as materialization_impl,
    factory_stage_helpers as helpers,
    factory_workspace_quality_evidence as wq_evidence,
    factory_workspace_quality_impl as workspace_quality_impl,
)
from ..factory_deadline_calculations import (  # noqa: F401 — re-exported for characterization-test surface
    _CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS,
    _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS,
    _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS,
    ChiefEngineerExecutionAttemptLeaseBudget as _ChiefEngineerExecutionAttemptLeaseBudget,
)
from ..factory_run_completion import RunCompletionAuthority
from ..factory_run_models import (
    FactoryRun,
    StageResult,
)
from ._helpers import (
    _DEFAULT_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS,
    _LANGUAGE_NEUTRAL_REPAIR_FILENAMES,
    _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS,
    _QUALITY_GATE_MIN_START_BUDGET_SECONDS,
    _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS,
    _WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_ENV,
    _WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES,
    _dedupe_workspace_repair_paths,
    _is_workspace_quality_repair_path,
    resolve_workspace_quality_existing_file,
    workspace_quality_rust_plan_probe_companion_paths,
)
from ._pkg_proxy import pkg

logger = logging.getLogger("polaris.cells.factory.pipeline.internal.factory_stage_executor")


class _Mixin03:
    """Method group extracted from OrchestrationStageExecutor (lossless)."""

    def _director_stage_materialization_settle_target_files(
        self,
        *,
        diagnostics: list[str],
    ) -> list[str]:
        """Resolve DEO write scope from owner targets plus plannable repairs.

        Some legitimate deterministic repairs create a derived target that is
        absent from the CE target list (for example
        ``dist/tests/verify.test.js`` referenced by the package verifier).  The
        repair-kernel plan probe is read-only authority for those changed paths;
        include them before minting the JobToken instead of letting DEO reject a
        valid existing repair as out of scope.
        """

        target_files = self._workspace_quality_repair_target_files()
        if not target_files:
            target_files = self._workspace_quality_repair_diagnostic_target_files(diagnostics)
        if not target_files:
            target_files = self._workspace_quality_repair_changed_files()
        planned_paths: list[str] = []
        probe = self._workspace_quality_repair_plan_probe_report(diagnostics)
        probe_items = probe.get("items") if isinstance(probe, Mapping) else None
        if isinstance(probe_items, list):
            for item in probe_items:
                if not isinstance(item, Mapping) or str(item.get("status") or "") != "covered_plannable":
                    continue
                changed_paths = item.get("changed_paths")
                if not isinstance(changed_paths, list):
                    continue
                for raw_path in changed_paths:
                    normalized = os.path.normpath(str(raw_path or "").strip().replace("\\", "/")).replace("\\", "/")
                    if normalized and _is_workspace_quality_repair_path(normalized):
                        planned_paths.append(normalized)
        extras: list[str] = []
        for candidate in (
            "package.json",
            "tsconfig.json",
            "tests/verify.test.ts",
            "tests/smoke.test.ts",
            "tests/unit/smoke.test.ts",
        ):
            if candidate not in target_files:
                extras.append(candidate)
        return list(dict.fromkeys([*target_files, *planned_paths, *extras]))

    def _director_stage_materialization_settle_commit_context(
        self,
        *,
        run: FactoryRun,
        run_id: str,
        diagnostics: list[str],
        factory_stage: str = "director_dispatch",
    ) -> dict[str, Any]:
        return materialization_impl._director_stage_materialization_settle_commit_context(
            self, run=run, run_id=run_id, diagnostics=diagnostics, factory_stage=factory_stage
        )

    @staticmethod
    def _director_stage_materialization_receipt_succeeded(receipt: Mapping[str, Any]) -> bool:
        """Interpret both legacy tool rows and canonical ``BatchReceipt`` rows.

        The DEO bridge returns normalized batch receipts whose authoritative
        outcome is expressed by ``success_count`` / ``failure_count`` and the
        nested result statuses.  It does not add a top-level ``success`` flag.
        Treating those rows like legacy tool-result dictionaries turned real
        ``RECEIPT_COMMITTED(succeeded)`` effects into Factory failures.
        """

        if "success" in receipt:
            return receipt.get("success") is True
        try:
            success_count = int(receipt.get("success_count", 0) or 0)
            failure_count = int(receipt.get("failure_count", 0) or 0)
        except (TypeError, ValueError):
            return False
        if failure_count > 0:
            return False
        if success_count > 0:
            return True
        results = receipt.get("results")
        if not isinstance(results, list) or not results:
            return False
        statuses = [str(item.get("status") or "").strip().lower() for item in results if isinstance(item, Mapping)]
        return len(statuses) == len(results) and bool(statuses) and all(status == "success" for status in statuses)

    async def _run_director_stage_materialization_quality_settle(
        self,
        *,
        run: FactoryRun,
        stage_status: str,
        error_code: str,
    ) -> dict[str, Any]:
        return await materialization_impl._run_director_stage_materialization_quality_settle(
            self, run=run, stage_status=stage_status, error_code=error_code
        )

    def _apply_workspace_quality_repairs(
        self,
        *,
        run_id: str,
        artifact_quality_errors: list[str],
        task_id: str | None = None,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
        repair_task: Mapping[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return workspace_quality_impl._apply_workspace_quality_repairs(
            self,
            run_id=run_id,
            artifact_quality_errors=artifact_quality_errors,
            task_id=task_id,
            execution_attempt=execution_attempt,
            repair_task=repair_task,
        )

    async def _apply_workspace_quality_deterministic_repairs(
        self,
        *,
        run: FactoryRun,
        artifact_quality_errors: list[str],
        repair_attempt: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return await workspace_quality_impl._apply_workspace_quality_deterministic_repairs(
            self, run=run, artifact_quality_errors=artifact_quality_errors, repair_attempt=repair_attempt
        )

    def _apply_workspace_quality_cpp_post_repairs(self) -> list[dict[str, Any]]:
        has_cpp_project = any(self.workspace.rglob("*.cpp")) or (self.workspace / "CMakeLists.txt").is_file()
        if not has_cpp_project:
            return []
        try:
            from polaris.cells.roles.adapters.public.service import (
                run_director_post_execution_repair_schedule,
            )

            results, _summary = run_director_post_execution_repair_schedule(
                self.workspace,
                task_id="factory-workspace-quality-post-execution-repair",
            )
            return results
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return [
                {
                    "tool": "deterministic_cpp_post_repair",
                    "success": False,
                    "result": {
                        "source_tool": "deterministic_cpp_post_repair",
                        "error": str(exc),
                    },
                }
            ]

    def _workspace_quality_repair_target_files(self) -> list[str]:
        return self._collect_declared_delivery_targets(self._load_pm_plan_tasks("tasks/plan.json"))

    def _workspace_quality_repair_diagnostic_target_files(self, artifact_quality_errors: list[str]) -> list[str]:
        from polaris.cells.director.runtime.public import normalize_director_repair_diagnostics

        workspace_root = self.workspace.resolve()
        candidates: list[str] = []
        causal_traceback_candidates: list[str] = []

        def append_workspace_path(raw_path: str, *, target: list[str] | None = None) -> None:
            path = str(raw_path or "").strip().replace("\\", "/")
            diagnostic_path = Path(path.removeprefix("file://"))
            if path and diagnostic_path.is_absolute():
                try:
                    path = diagnostic_path.resolve().relative_to(workspace_root).as_posix()
                except (OSError, ValueError):
                    # Python tracebacks also name stdlib/site-package frames.
                    # Never turn those external files into Director write scope.
                    return
            if path and _is_workspace_quality_repair_path(path):
                (target if target is not None else candidates).append(path)

        diagnostics = normalize_director_repair_diagnostics([str(item) for item in artifact_quality_errors or []])
        for diagnostic in diagnostics:
            append_workspace_path(str(diagnostic.path or ""))

        # Python tracebacks are ordered outermost -> innermost.  Owner routing
        # must therefore rank the deepest workspace frame before the importing
        # test/package frames.  Live L3-21 exposed ``NameError: dataclass`` in
        # ``line_editor.py`` but the old append order leased TASK-1 through its
        # outer ``__init__.py`` frame; the final provider request then excluded
        # the failing file even though its exact body and traceback were present.
        # A parenthesized ImportError source (``(.../__init__.py)``) is equally
        # causal and ranks before the traceback frames.
        for raw_error in artifact_quality_errors or []:
            error_text = str(raw_error or "")
            for match in re.finditer(r"\((?P<path>[^()]+)\)", error_text):
                append_workspace_path(match.group("path"), target=causal_traceback_candidates)
            traceback_matches = list(
                re.finditer(r"\bFile\s+[\"'](?P<path>[^\"']+)[\"']\s*,\s*line\s+\d+", error_text)
            )
            for match in reversed(traceback_matches):
                append_workspace_path(match.group("path"), target=causal_traceback_candidates)
        candidates = [*causal_traceback_candidates, *candidates]
        joined_errors = "\n".join(str(item or "") for item in artifact_quality_errors).lower()
        for filename in _LANGUAGE_NEUTRAL_REPAIR_FILENAMES:
            if filename.lower() not in joined_errors:
                continue
            resolved = resolve_workspace_quality_existing_file(workspace_root, filename)
            if resolved is not None:
                candidates.append(filename)
        candidates.extend(
            workspace_quality_rust_plan_probe_companion_paths(
                workspace_root,
                artifact_quality_errors=artifact_quality_errors,
            )
        )
        if ("include 'dom'" in joined_errors or "compiler option" in joined_errors or "tsconfig" in joined_errors) and (
            workspace_root / "tsconfig.json"
        ).is_file():
            candidates.append("tsconfig.json")
        if "package.json" in joined_errors and (workspace_root / "package.json").is_file():
            candidates.append("package.json")
        for source_path in list(candidates):
            candidates.extend(self._workspace_quality_relative_import_targets(source_path))
        return _dedupe_workspace_repair_paths(candidates)

    def _workspace_quality_relative_import_targets(self, source_path: str) -> list[str]:
        workspace_root = self.workspace.resolve()
        normalized_source = str(source_path or "").strip().replace("\\", "/")
        source = (workspace_root / normalized_source).resolve()
        try:
            if not source.is_relative_to(workspace_root) or not source.is_file():
                return []
        except ValueError:
            return []
        if source.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
            return []
        with contextlib.suppress(OSError, UnicodeDecodeError):
            text = source.read_text(encoding="utf-8")
            targets: list[str] = []
            for match in re.finditer(
                r"(?:\bfrom\s+|\brequire\s*\(\s*|\bimport\s*\(\s*)['\"](?P<module>\.{1,2}/[^'\"]+)['\"]",
                text,
            ):
                targets.extend(
                    self._workspace_quality_resolve_relative_module(normalized_source, match.group("module"))
                )
            return targets
        return []

    def _workspace_quality_resolve_relative_module(self, importer: str, module_ref: str) -> list[str]:
        workspace_root = self.workspace.resolve()
        importer_dir = Path(importer).parent
        raw = (importer_dir / module_ref).as_posix()
        root, suffix = os.path.splitext(raw)
        candidates = [raw] if suffix else []
        candidates.extend(f"{root}{ext}" for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
        candidates.extend(f"{root}/index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
        resolved: list[str] = []
        for candidate in candidates:
            normalized = os.path.normpath(candidate).replace("\\", "/")
            path = (workspace_root / normalized).resolve()
            try:
                if path.is_relative_to(workspace_root) and path.is_file():
                    resolved.append(path.relative_to(workspace_root).as_posix())
            except ValueError:
                continue
        return resolved

    def _workspace_quality_repair_changed_files(self) -> list[str]:
        workspace_root = self.workspace.resolve()
        if not workspace_root.is_dir():
            return []
        ignored_parts = {".git", ".polaris", ".pytest_cache", "dist", "build", "coverage", "node_modules"}
        changed: list[str] = []
        for path in sorted(workspace_root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            try:
                rel_path = path.relative_to(workspace_root)
            except ValueError:
                continue
            if any(part in ignored_parts for part in rel_path.parts):
                continue
            if path.suffix.lower() not in _WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES:
                continue
            changed.append(rel_path.as_posix())
            if len(changed) >= 120:
                break
        return changed

    def _workspace_quality_repair_blueprint_evidence(self, *, run_id: str) -> tuple[str, str]:
        if not run_id:
            return "", ""
        for candidate in (
            f"runtime/state/blueprints/{run_id}.review.json",
            f"runtime/blueprints/{run_id}.review.json",
            f"workspace/.polaris/blueprints/{run_id}.review.json",
            "workspace/.polaris/blueprints/latest.review.json",
        ):
            with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                text = self._read_text_artifact(candidate, min_chars=2)
            if text:
                return candidate, self._compact_blueprint_evidence_for_repair(text)
        for candidate in (
            f".polaris/blueprints/{run_id}.review.json",
            ".polaris/blueprints/latest.review.json",
            f".polaris/roles/chief_engineer/{run_id}/review.json",
        ):
            text = ""
            with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError, UnicodeDecodeError):
                target = (self.workspace / candidate).resolve()
                if not target.is_relative_to(self.workspace.resolve()) or not target.is_file():
                    continue
                text = target.read_text(encoding="utf-8").strip()
            if len(text) >= 2:
                return f"workspace-local:{candidate}", self._compact_blueprint_evidence_for_repair(text)
        return "", ""

    def _workspace_quality_repair_original_message(self, *, run_id: str, target_files: list[str]) -> str:
        tasks = self._load_pm_plan_tasks("tasks/plan.json")
        lines: list[str] = [
            "Factory workspace quality repair contract:",
            "- Delivery mode: materialize changes into the workspace.",
        ]
        if target_files:
            lines.append("- Target files:")
            lines.extend(f"  - {item}" for item in target_files[:80])

        blueprint_artifact, blueprint_text = self._workspace_quality_repair_blueprint_evidence(run_id=run_id)
        if blueprint_text:
            lines.extend(
                [
                    "- Chief Engineer blueprint evidence:",
                    f"  artifact: {blueprint_artifact}",
                    blueprint_text,
                ]
            )
        else:
            lines.append("- Chief Engineer blueprint evidence: unavailable for this repair turn.")

        if tasks:
            lines.append("- PM task contract summary:")
        for index, task in enumerate(tasks[:20], start=1):
            title = str(task.get("title") or task.get("id") or f"TASK-{index}").strip()
            goal = str(task.get("goal") or task.get("description") or "").strip()
            scope = str(task.get("scope") or "").strip()
            task_targets = self._task_string_list(task, "target_files", "scope_paths")
            steps = self._task_string_list(task, "steps")
            acceptance = self._task_string_list(task, "acceptance", "acceptance_criteria")
            lines.append(f"  {index}. {title}")
            if goal:
                lines.append(f"     goal: {goal}")
            if scope:
                lines.append(f"     scope: {scope}")
            if task_targets:
                lines.append(f"     targets: {', '.join(task_targets[:16])}")
            if steps:
                lines.append(f"     steps: {'; '.join(steps[:4])}")
            if acceptance:
                lines.append(f"     acceptance: {'; '.join(acceptance[:4])}")
        return "\n".join(lines)[:12000]

    @staticmethod
    def _workspace_quality_llm_repair_timeout_seconds(context: dict[str, Any]) -> float:
        raw = context.get("workspace_quality_repair_llm_timeout_seconds")
        if raw is None:
            raw = os.environ.get(_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_ENV)
        try:
            value = float(str(raw))
        except (TypeError, ValueError):
            value = _DEFAULT_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS
        configured = max(30.0, min(value, 3600.0))
        remaining_seconds = pkg().OrchestrationStageExecutor._factory_deadline_remaining_seconds(context)
        if remaining_seconds is None:
            return configured
        capped = max(1.0, remaining_seconds - _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS)
        return max(1.0, min(configured, capped))

    async def _apply_workspace_quality_llm_repairs(
        self,
        *,
        run: FactoryRun,
        context: dict[str, Any],
        artifact_quality_errors: list[str],
        repair_attempt: int,
        interface_discrepancy_evidence: dict[str, Any] | None = None,
        owner_target_files: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return await workspace_quality_impl._apply_workspace_quality_llm_repairs(
            self,
            run=run,
            context=context,
            artifact_quality_errors=artifact_quality_errors,
            repair_attempt=repair_attempt,
            interface_discrepancy_evidence=interface_discrepancy_evidence,
            owner_target_files=owner_target_files,
        )

    @staticmethod
    def _workspace_quality_repair_result_has_mutation(item: dict[str, Any]) -> bool:
        return wq_evidence.workspace_quality_repair_result_has_mutation(item)

    @staticmethod
    def _workspace_quality_repair_evidence(repair_results: list[dict[str, Any]]) -> list[str]:
        return wq_evidence.workspace_quality_repair_evidence(repair_results)

    @staticmethod
    def _workspace_quality_summary_requires_task_boundary_triage(summary: dict[str, Any]) -> bool:
        return wq_evidence.workspace_quality_summary_requires_task_boundary_triage(summary)

    @staticmethod
    def _workspace_quality_deferred_owner_targets(summary: dict[str, Any]) -> list[str]:
        """Return precise targets deferred because the first repair task did not own them."""

        return wq_evidence.workspace_quality_deferred_owner_targets(summary)

    @staticmethod
    def _workspace_quality_interface_discrepancy_evidence(
        summary: dict[str, Any],
        artifact_quality_errors: list[str] | None = None,
    ) -> dict[str, Any]:
        return wq_evidence.workspace_quality_interface_discrepancy_evidence(summary, artifact_quality_errors)

    @staticmethod
    def _workspace_quality_interface_discrepancy_allows_director_retry(evidence: dict[str, Any]) -> bool:
        return wq_evidence.workspace_quality_interface_discrepancy_allows_director_retry(evidence)

    @staticmethod
    def _workspace_quality_claimed_owner_repair_targets(evidence: dict[str, Any]) -> list[str]:
        return wq_evidence.workspace_quality_claimed_owner_repair_targets(evidence)

    @staticmethod
    def _workspace_quality_repair_summary_projection(
        summary: dict[str, Any],
        artifact_quality_errors: list[str] | None = None,
    ) -> dict[str, Any]:
        return wq_evidence.workspace_quality_repair_summary_projection(summary, artifact_quality_errors)

    async def _run_workspace_quality_checks(self, run: FactoryRun, context: dict[str, Any]) -> tuple[bool, str]:
        return await workspace_quality_impl._run_workspace_quality_checks(self, run, context)

    def _write_workspace_validation_artifact(
        self,
        run: FactoryRun,
        context: dict[str, Any],
        payload: dict[str, Any],
    ) -> str:
        artifact = "runtime/qa/workspace-validation.json"
        self._write_json_artifact(artifact, payload)
        self._persist_workspace_validation_ledger(run, context, payload)
        return artifact

    def _persist_workspace_validation_ledger(
        self,
        run: FactoryRun,
        context: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        raw_effective_commands = payload.get("effective_commands")
        raw_commands = raw_effective_commands if isinstance(raw_effective_commands, list) else payload.get("commands")
        commands = (
            [dict(item) for item in raw_commands if isinstance(item, dict)] if isinstance(raw_commands, list) else []
        )
        for command in commands:
            if "ok" not in command and "passed" in command:
                command["ok"] = bool(command.get("passed"))
        passed = bool(payload.get("passed"))
        detail = str(
            payload.get("error") or ("workspace validation passed" if passed else "workspace validation failed")
        )
        structured_authority = self._build_qa_final_request_metadata(
            run_id=run.id,
            workspace_checks_artifact="",
        )
        target_files = self._merge_string_list(
            context.get("target_files")
            or context.get("declared_source_targets")
            or context.get("code_files")
            or context.get("scope_paths")
            or structured_authority.get("target_files")
        )
        scope_paths = self._merge_string_list(
            context.get("scope_paths") or structured_authority.get("scope_paths") or target_files
        )
        raw_repair = payload.get("repair")
        full_repair: dict[str, Any] = dict(raw_repair) if isinstance(raw_repair, dict) else {}
        repair_ledger_projection = wq_evidence.workspace_quality_repair_ledger_projection(
            full_repair,
            full_evidence_ref="runtime/qa/workspace-validation.json",
        )
        record = {
            "id": str(context.get("project_id") or context.get("requested_project_id") or run.id),
            "project_id": str(context.get("project_id") or context.get("requested_project_id") or run.id),
            "run_id": run.id,
            "target_files": target_files,
            "scope_paths": scope_paths,
            "pm_task_contract": structured_authority.get("pm_task_contract") or {},
            "pm_task_contracts": structured_authority.get("pm_task_contracts") or [],
            "chief_engineer_blueprint": structured_authority.get("chief_engineer_blueprint") or {},
            "acceptance_criteria": self._merge_string_list(
                context.get("acceptance_criteria") or context.get("acceptance") or context.get("qa_contract")
            ),
            "required_evidence_modalities": ["command"] if commands else [],
            "enabled_evidence_modalities": ["command"] if commands else [],
            "chain": {"run_id": run.id},
            "factory_workspace_quality_repair": repair_ledger_projection,
        }
        gate = {
            "ok": passed,
            "summary": detail,
            "gate_obligation_id": f"factory:{run.id}:workspace_validation",
            "gate_subject_kind": "factory_run",
            "gate_subject_id": run.id,
            "command_count_total": len(commands),
            "commands": commands,
            "requirements": {"workspace_validation": {"ok": passed, "detail": detail}},
            "repair_result": repair_ledger_projection,
        }
        try:
            from ..run_ledger import persist_real_run_gate_ledger

            persist_real_run_gate_ledger(
                self.workspace,
                record,
                gate,
                run_id=run.id,
                project_id=str(record["project_id"]),
                stage="workspace_validation",
                gate_name="workspace_validation",
            )
        except Exception as exc:  # noqa: BLE001 - ledger evidence must not mask the validation verdict.
            logger.debug("workspace validation ledger persistence failed for %s: %s", run.id, exc)

    @staticmethod
    def _qa_report_has_warning(payload: dict[str, Any], warning: str) -> bool:
        return helpers.qa_report_has_warning(payload, warning)

    @staticmethod
    def _factory_deadline_remaining_seconds(context: dict[str, Any]) -> float | None:
        return deadline_calc.factory_deadline_remaining_seconds(context)

    @staticmethod
    async def _quality_gate_abort_reason(
        abort_checker: Callable[[], Awaitable[str | None]] | None,
    ) -> str:
        if abort_checker is None:
            return ""
        try:
            return str(await abort_checker() or "").strip()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Factory quality gate abort checker failed: %s", exc)
            return ""

    def _build_quality_gate_failure_stage(
        self,
        run: FactoryRun,
        *,
        reason_code: str,
        detail: str,
        context: dict[str, Any],
        workspace_checks_artifact: str = "",
        workspace_checks_passed: bool | None = None,
        status: str = "failed",
        qa_invoked: bool = False,
    ) -> StageResult:
        target = str(context.get("qa_target") or "Quality gate")
        remaining_seconds = self._factory_deadline_remaining_seconds(context)
        warnings = [reason_code]
        payload: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "review_type": "quality_gate",
            "target": target,
            "runtime_hard_gate_passed": False,
            "verdict": "FAIL" if qa_invoked else "NOT_RUN",
            "qa_invoked": qa_invoked,
            "canonical_qa_verdict": False,
            "verdict_source": "qa_runtime" if qa_invoked else "deterministic_factory_gate",
            "passed": False,
            "score": 0,
            "critical_issue_count": 1,
            "critical_issues": [detail],
            "major_issues": [],
            "warnings": warnings,
            "evidence": [
                f"factory_run_id={run.id}",
                f"reason_code={reason_code}",
            ],
            "suggestions": [],
            "raw_excerpt": detail[:2000],
            "deadline": {
                "remaining_seconds": remaining_seconds,
                "deadline_epoch_seconds": context.get("factory_run_deadline_epoch_seconds"),
                "timeout_seconds": context.get("factory_run_timeout_seconds"),
                "source": context.get("factory_run_deadline_source"),
            },
        }
        if workspace_checks_passed is not None:
            payload["workspace_checks_passed"] = workspace_checks_passed
            payload["evidence"].append(f"workspace_checks_passed={workspace_checks_passed}")
        if workspace_checks_artifact:
            payload["workspace_checks_artifact"] = workspace_checks_artifact
            payload["evidence"].append(f"workspace_checks_artifact={workspace_checks_artifact}")
        self._write_json_artifact("runtime/qa/report.json", payload)
        artifacts = ["runtime/qa/report.json"]
        if workspace_checks_artifact:
            artifacts.append(workspace_checks_artifact)
        self._mirror_quality_gate_artifacts(run.id, artifacts)
        return StageResult(
            stage="quality_gate",
            status=status,
            output=f"Quality gate {status}: {reason_code}; {detail}",
            artifacts=artifacts,
        )

    def _workspace_quality_failure_detail(self, workspace_checks_artifact: str) -> str:
        detail = "Workspace validation failed"
        if not workspace_checks_artifact:
            return detail
        artifact_path = self._artifact_path(workspace_checks_artifact)
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return f"{detail}; see {workspace_checks_artifact}"
        if not isinstance(payload, dict):
            return f"{detail}; see {workspace_checks_artifact}"
        evidence: list[str] = []
        repair = payload.get("repair")
        if isinstance(repair, dict):
            for raw in repair.get("residual_errors") or ():
                text = str(raw or "").strip()
                if text:
                    evidence.append(text[:500])
                if len(evidence) >= 2:
                    break
        for item in payload.get("commands") or ():
            if len(evidence) >= 3:
                break
            if not isinstance(item, dict) or bool(item.get("passed")):
                continue
            command = item.get("command")
            command_text = " ".join(str(part) for part in command) if isinstance(command, list) else str(command or "")
            stderr_tail = str(item.get("stderr_tail") or item.get("error") or "").strip()
            if command_text or stderr_tail:
                evidence.append(f"{command_text}: {stderr_tail[:400]}".strip(": "))
        if not evidence:
            return f"{detail}; see {workspace_checks_artifact}"
        return f"{detail}: {'; '.join(evidence)}; see {workspace_checks_artifact}"

    def _quality_gate_qa_wait_timeout_seconds(self, context: dict[str, Any]) -> int:
        try:
            configured = int(context.get("timeout", 600))
        except (TypeError, ValueError):
            configured = 600
        configured = max(1, configured)
        remaining_seconds = self._factory_deadline_remaining_seconds(context)
        if remaining_seconds is None:
            return configured
        capped = max(1, int(remaining_seconds - _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS))
        return max(1, min(configured, capped))

    @staticmethod
    def _quality_gate_requires_llm_judgement(context: dict[str, Any]) -> bool:
        """Return whether QA needs non-physical semantic judgement.

        Build/test/lint/entrypoint receipts are authoritative physical
        evidence. Repeating them through a general QA LLM adds latency and
        failure surface without adding authority. LLM QA is opt-in for
        modalities that genuinely require semantic or visual judgement.
        """

        sources = [context]
        qa_input = context.get("qa_input")
        if isinstance(qa_input, Mapping):
            sources.append(dict(qa_input))

        explicit_keys = (
            "qa_llm_required",
            "qa_requires_llm_judgement",
            "qa_semantic_review_required",
            "qa_security_review_required",
            "qa_visual_required",
            "requires_visual_evidence",
        )
        if any(bool(source.get(key)) for source in sources for key in explicit_keys):
            return True

        modes = {
            str(source.get(key) or "").strip().lower()
            for source in sources
            for key in ("qa_mode", "qa_judgement_mode", "qa_review_mode")
        }
        if modes.intersection({"llm", "llm_required", "semantic", "visual", "security"}):
            return True

        required_modalities: set[str] = set()
        for source in sources:
            for key in ("required_evidence_modalities", "qa_required_modalities"):
                raw = source.get(key)
                if isinstance(raw, str):
                    required_modalities.update(item.strip().lower() for item in raw.split(",") if item.strip())
                elif isinstance(raw, (list, tuple, set)):
                    required_modalities.update(
                        str(item or "").strip().lower() for item in raw if str(item or "").strip()
                    )
        return bool(
            required_modalities.intersection(
                {"visual", "image", "semantic", "semantic_review", "security", "security_review"}
            )
        )

    def _write_physical_verifier_qa_report(
        self,
        *,
        run: FactoryRun,
        workspace_checks_artifact: str,
    ) -> dict[str, Any]:
        """Persist deterministic QA evidence after physical checks pass."""

        payload: dict[str, Any] = {
            "schema_version": "factory.qa_physical_verifier_report.v1",
            "source": "factory_physical_verifier",
            "factory_run_id": run.id,
            "passed": True,
            "verdict": "PASS",
            "score": 100.0,
            "critical_issue_count": 0,
            "critical_issues": [],
            "major_issues": [],
            "warnings": [],
            "workspace_checks_artifact": workspace_checks_artifact,
            "qa_invoked": False,
            "llm_invoked": False,
        }
        self._write_json_artifact("runtime/qa/report.json", payload)
        return payload

    def _build_qa_execution_metadata(
        self,
        *,
        run_id: str,
        workspace_checks_artifact: str,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        """Bind structured QA evidence and its deadline-derived LLM budget."""

        qa_wait_timeout_seconds = self._quality_gate_qa_wait_timeout_seconds(context)
        qa_request_timeout_seconds = max(
            1,
            qa_wait_timeout_seconds - int(_QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS),
        )
        metadata = self._build_qa_final_request_metadata(
            run_id=run_id,
            workspace_checks_artifact=workspace_checks_artifact,
        )
        metadata.update(
            {
                "request_timeout_seconds": qa_request_timeout_seconds,
                "timeout_seconds": qa_request_timeout_seconds,
            }
        )
        return metadata, qa_wait_timeout_seconds

    def _build_qa_input_with_workspace_quality_evidence(
        self,
        qa_input: object,
        workspace_checks_artifact: str,
        *,
        run_id: str = "",
    ) -> str:
        base_input = str(qa_input or "").strip()
        sections = [base_input] if base_input else []
        if workspace_checks_artifact or run_id:
            # The actual evidence is attached as structured final-request slots
            # by ``_build_qa_final_request_metadata``.  Do not duplicate raw
            # audit JSON in the user prompt: it wastes tokens and may leak
            # control-plane run identifiers into ContextOS prompt content.
            sections.append(
                "Evaluate the structured PM contract, Chief Engineer blueprint, "
                "target files, verifier receipts, and workspace-quality evidence attached to this QA request."
            )
        return "\n\n".join(sections)

    def _build_qa_final_request_metadata(
        self,
        *,
        run_id: str,
        workspace_checks_artifact: str,
    ) -> dict[str, Any]:
        """Build QA's five required structured final-request evidence slots."""

        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        ce_blueprint = self._load_chief_engineer_review_payload(run_id=run_id)
        if not ce_blueprint:
            for candidate in (
                f"runtime/blueprints/{run_id}.review.json",
                f"workspace/.polaris/blueprints/{run_id}.review.json",
                "workspace/.polaris/blueprints/latest.review.json",
            ):
                ce_blueprint = self._read_json_artifact_payload(candidate)
                if ce_blueprint:
                    break
        workspace_quality = (
            self._read_json_artifact_payload(workspace_checks_artifact)
            if str(workspace_checks_artifact or "").strip()
            else {}
        )
        target_files = self._collect_declared_delivery_targets(pm_tasks)
        raw_receipts = workspace_quality.get("commands") if isinstance(workspace_quality, dict) else None
        verifier_receipts = (
            [dict(item) for item in raw_receipts if isinstance(item, dict)] if isinstance(raw_receipts, list) else []
        )

        metadata: dict[str, Any] = {
            "source": "factory_stage_executor.quality_gate",
            "pm_task_contracts": deepcopy(pm_tasks),
            "target_files": list(target_files),
            "scope_paths": list(target_files),
            "chief_engineer_blueprint": deepcopy(ce_blueprint),
            "verifier_receipts": verifier_receipts,
            "workspace_quality_evidence": deepcopy(workspace_quality),
        }
        if pm_tasks:
            metadata["pm_task_contract"] = deepcopy(pm_tasks[0])
        return metadata

    async def _wait_for_canonical_quality_authority(
        self,
        run: FactoryRun,
        context: dict[str, Any],
    ) -> helpers.CanonicalFactoryAuthority:
        """Wait until the QA fact is visible behind the sequence barrier.

        TaskBoundary ``completed_verified`` proves Director settlement. The
        final ``qa_verdict`` gate's append/content coordinates prove the QA
        consumer barrier. Both facts must be visible in the same Run Ledger
        projection; a report file or orchestration status cannot substitute.
        """

        raw_timeout = context.get("canonical_projection_settlement_timeout_seconds", 2.0)
        try:
            timeout_seconds = float(raw_timeout)
        except (TypeError, ValueError):
            timeout_seconds = 2.0
        timeout_seconds = max(0.1, min(timeout_seconds, 10.0))
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        latest = helpers.evaluate_canonical_factory_authority({})
        while True:
            latest = helpers.evaluate_canonical_factory_authority(
                self._canonical_factory_projection(run, context),
            )
            if latest.quality_stage_authorized:
                return latest
            if latest.qa_verdict_present or latest.reason_code in {
                "canonical_sequence_barrier_unsatisfied",
                "qa_verdict_failed",
                "evidence_policy_failed",
                "run_ledger_projection_failed",
                "task_boundary_not_completed_verified",
            }:
                return latest
            if asyncio.get_running_loop().time() >= deadline:
                return latest
            await asyncio.sleep(0.05)

    def _canonical_qa_commit_identity(
        self,
        *,
        run: FactoryRun,
        context: dict[str, Any],
    ) -> tuple[str, str]:
        """Return the final owned task and its authoritative child run.

        QA verdict envelopes are task-boundary scoped.  A Factory portfolio is
        a tree of Director child runs, so committing the verdict against the
        Factory root run would never match a TaskBoundary and would correctly
        fail closed.  Bind the final PM contract task to its actual child run;
        the Factory aggregate projection then observes the resulting gate.
        """

        projection = self._canonical_factory_projection(run, context)
        task_boundary = projection.get("task_boundary")
        task_boundary_map = task_boundary if isinstance(task_boundary, Mapping) else {}
        latest_by_task = task_boundary_map.get("latest_by_task")
        latest_by_task_map = latest_by_task if isinstance(latest_by_task, Mapping) else {}
        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        ordered_task_ids = [
            helpers._canonical_task_id_token(task.get("id") or task.get("task_id"))
            for task in pm_tasks
            if isinstance(task, dict)
        ]
        for task_id in reversed([item for item in ordered_task_ids if item]):
            boundary_raw = latest_by_task_map.get(task_id)
            boundary = boundary_raw if isinstance(boundary_raw, Mapping) else {}
            boundary_run_id = str(boundary.get("run_id") or "").strip()
            if (
                boundary_run_id
                and boundary.get("ok") is True
                and str(boundary.get("status") or "").strip().lower() == "completed_verified"
            ):
                return task_id, boundary_run_id
        return "", ""

    async def _commit_qa_role_report_authority(
        self,
        *,
        run: FactoryRun,
        context: dict[str, Any],
        qa_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit the completed QA role report through ``qa.audit_verdict``."""

        from polaris.cells.qa.audit_verdict.public import (
            CommitQaRoleVerdictCommandV1,
            commit_qa_role_verdict,
        )

        task_id, qa_run_id = self._canonical_qa_commit_identity(run=run, context=context)
        if not task_id or not qa_run_id:
            return {"success": False, "reason": "canonical_qa_task_boundary_identity_missing"}
        verdict = str(qa_payload.get("verdict") or "").strip().upper()
        passed = bool(qa_payload.get("passed"))
        if not verdict:
            verdict = "PASS" if passed else "FAIL"
        if passed != (verdict == "PASS"):
            return {"success": False, "reason": "qa_role_report_verdict_inconsistent"}
        raw_findings = [
            *(qa_payload.get("critical_issues") or []),
            *(qa_payload.get("major_issues") or []),
            *(qa_payload.get("warnings") or []),
        ]
        findings = tuple(str(item).strip() for item in raw_findings if str(item).strip())
        target_files = tuple(self._collect_declared_delivery_targets(self._load_pm_plan_tasks("tasks/plan.json")))
        commit_context = self._director_stage_materialization_settle_commit_context(
            run=run,
            run_id=qa_run_id,
            diagnostics=[],
            factory_stage="quality_gate",
        )
        job_token_raw = commit_context.get("job_token")
        job_token = dict(job_token_raw) if isinstance(job_token_raw, Mapping) else {}
        report_content_hash = hashlib.sha256(
            json.dumps(qa_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        try:
            score = float(qa_payload.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            critical_issue_count = int(qa_payload.get("critical_issue_count") or 0)
        except (TypeError, ValueError):
            critical_issue_count = len(qa_payload.get("critical_issues") or [])
        try:
            result = await asyncio.to_thread(
                commit_qa_role_verdict,
                CommitQaRoleVerdictCommandV1(
                    task_id=task_id,
                    workspace=str(self.workspace),
                    run_id=qa_run_id,
                    verdict=verdict,
                    passed=passed,
                    score=score,
                    critical_issue_count=critical_issue_count,
                    findings=findings,
                    target_files=target_files,
                    report_ref="runtime/qa/report.json",
                    report_content_hash=report_content_hash,
                    job_token=job_token,
                    metadata={
                        "source": str(qa_payload.get("source") or "factory_stage_executor.quality_gate"),
                        "factory_run_id": run.id,
                    },
                ),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Canonical QA role verdict commit failed for %s: %s", run.id, exc)
            return {"success": False, "reason": f"qa_role_verdict_commit_failed:{type(exc).__name__}:{exc}"}
        metadata = dict(result.metadata)
        return {
            "success": bool(metadata.get("qa_verdict_committed")),
            "verdict": result.verdict,
            "ok": result.ok,
            "task_id": task_id,
            "run_id": qa_run_id,
            "receipt": dict(metadata.get("qa_verdict_commit_receipt") or {}),
            "reason": "" if metadata.get("qa_verdict_committed") else "qa_verdict_commit_receipt_missing",
        }

    def _reconcile_verified_runtime_delivery(
        self,
        *,
        run: FactoryRun,
        authority: helpers.CanonicalFactoryAuthority,
    ) -> dict[str, Any]:
        """Settle exact failed PM rows whose canonical delivery is verified.

        This is not a disk-only success override.  ``recovered_runtime_task_ids``
        is produced only when the canonical Run Ledger has a terminal runtime
        fact plus an owned ``TaskBoundary completed_verified`` fact.  Quality
        reconciliation runs only after the same projection also contains a
        passing QA verdict, sequence barrier, and evidence-policy result.
        """

        recovered_ids = tuple(authority.recovered_runtime_task_ids)
        if not recovered_ids:
            return {"success": True, "reconciled_task_ids": []}
        if not authority.quality_stage_authorized:
            return {
                "success": False,
                "reason": "canonical_quality_authority_not_verified",
                "reconciled_task_ids": [],
            }

        runtime = pkg().TaskRuntimeService(str(self.workspace))
        rows = runtime.list_observable_task_rows()
        reconciled: list[str] = []
        for external_task_id in recovered_ids:
            historical_matches: list[dict[str, Any]] = []
            for raw_row in rows:
                row = dict(raw_row) if isinstance(raw_row, dict) else {}
                raw_metadata = row.get("metadata")
                metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
                aliases = {
                    str(source.get(key) or "").strip()
                    for source in (row, metadata)
                    for key in ("external_task_id", "source_task_id", "pm_task_id")
                    if str(source.get(key) or "").strip()
                }
                factory_run_id = str(metadata.get("factory_run_id") or "").strip()
                if external_task_id in aliases and factory_run_id == run.id:
                    historical_matches.append(row)

            # ``list_observable_task_rows`` is an execution-fact projection.
            # It intentionally retains ``runtime_reset_removed`` tombstones for
            # audit.  Those historical rows are not concurrent delivery owners.
            # Counting them here made every QA-only retry fail after a terminal
            # drain even though its frozen TaskRuntime authority was exact.
            live_matches = [
                row
                for row in historical_matches
                if str(row.get("execution_state") or row.get("status") or "").strip().lower() != "removed"
            ]
            if len(live_matches) > 1:
                return {
                    "success": False,
                    "reason": "verified_delivery_runtime_owner_not_unique",
                    "external_task_id": external_task_id,
                    "match_count": len(live_matches),
                    "historical_match_count": len(historical_matches),
                    "reconciled_task_ids": reconciled,
                }

            if not live_matches:
                # Terminal settlement removes live TaskRuntime files after
                # persisting one authoritative projection on the Factory run.
                # A QA-only retry preserves that epoch.  Validate the frozen
                # exact owner first, then ask TaskRuntime to materialize a fresh
                # row from the canonical PM contract.  Never resurrect an
                # arbitrary tombstone or infer authority from the largest id.
                from polaris.cells.factory.pipeline.public.contracts import (
                    FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
                    FactoryTerminalTaskRuntimeProjectionV1,
                )

                frozen_payload = run.metadata.get(FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY)
                if not isinstance(frozen_payload, Mapping):
                    return {
                        "success": False,
                        "reason": "verified_delivery_runtime_frozen_authority_missing",
                        "external_task_id": external_task_id,
                        "historical_match_count": len(historical_matches),
                        "reconciled_task_ids": reconciled,
                    }
                try:
                    frozen = FactoryTerminalTaskRuntimeProjectionV1.from_dict(frozen_payload)
                except (OSError, TypeError, ValueError) as exc:
                    return {
                        "success": False,
                        "reason": f"verified_delivery_runtime_frozen_authority_invalid:{type(exc).__name__}",
                        "external_task_id": external_task_id,
                        "historical_match_count": len(historical_matches),
                        "reconciled_task_ids": reconciled,
                    }
                frozen_rows = frozen.projection.get("rows")
                frozen_matches = [
                    dict(row)
                    for row in (frozen_rows if isinstance(frozen_rows, list) else [])
                    if isinstance(row, Mapping)
                    and str(row.get("external_task_id") or "").strip() == external_task_id
                    and str(row.get("factory_run_id") or "").strip() == run.id
                    and str(row.get("execution_state") or row.get("status") or "").strip().lower() != "removed"
                ]
                if frozen.factory_run_id != run.id or len(frozen_matches) != 1:
                    return {
                        "success": False,
                        "reason": "verified_delivery_runtime_frozen_owner_not_unique",
                        "external_task_id": external_task_id,
                        "match_count": len(frozen_matches),
                        "historical_match_count": len(historical_matches),
                        "reconciled_task_ids": reconciled,
                    }

                canonical_matches = [
                    dict(task)
                    for index, task in enumerate(self._load_pm_plan_tasks("tasks/plan.json"), start=1)
                    if self._task_id(task, index) == external_task_id
                ]
                if len(canonical_matches) != 1:
                    return {
                        "success": False,
                        "reason": "verified_delivery_runtime_pm_contract_not_unique",
                        "external_task_id": external_task_id,
                        "match_count": len(canonical_matches),
                        "reconciled_task_ids": reconciled,
                    }
                materialized = self._materialize_pm_plan_taskboard(
                    canonical_matches,
                    run_id=run.id,
                    source_stage="quality_gate",
                    run_metadata=run.metadata,
                )
                if materialized.get("binding_failures"):
                    return {
                        "success": False,
                        "reason": "verified_delivery_runtime_owner_binding_failed",
                        "external_task_id": external_task_id,
                        "binding_failures": materialized.get("binding_failures"),
                        "reconciled_task_ids": reconciled,
                    }
                # Materialization owns another service instance.  Reopen the
                # projection so this claimant cannot retain an empty board.
                runtime = pkg().TaskRuntimeService(str(self.workspace))
                restored = runtime.get_task(external_task_id)
                if not isinstance(restored, Mapping):
                    return {
                        "success": False,
                        "reason": "verified_delivery_runtime_owner_restore_failed",
                        "external_task_id": external_task_id,
                        "reconciled_task_ids": reconciled,
                    }
                live_matches = [dict(restored)]

            task_id = int(live_matches[0]["id"])
            evidence = {
                "schema_version": "factory.verified_delivery_runtime_reconciliation.v1",
                "factory_run_id": run.id,
                "external_task_id": external_task_id,
                "source": "canonical_run_ledger",
                "task_boundary_completed_verified": authority.task_boundary_completed_verified,
                "qa_verdict_passed": authority.qa_verdict_passed,
                "sequence_barrier_satisfied": authority.sequence_barrier_satisfied,
                "evidence_policy_passed": authority.evidence_policy_passed,
            }
            reopened = runtime.reopen_task_row(
                task_id,
                reason="canonical_delivery_verified_after_terminal_director_attempt",
                metadata={"verified_delivery_reconciliation": evidence},
            )
            if not isinstance(reopened, dict):
                return {
                    "success": False,
                    "reason": "verified_delivery_runtime_reopen_failed",
                    "external_task_id": external_task_id,
                    "reconciled_task_ids": reconciled,
                }
            claim = runtime.claim_execution(
                task_id,
                worker_id=f"factory-quality-gate:{run.id}",
                role_id="qa",
                run_id=run.id,
                lease_ttl_seconds=120,
                selection_source="factory_verified_delivery_reconciliation",
                external_task_id=external_task_id,
                metadata={"verified_delivery_reconciliation": evidence},
            )
            attempt_record = claim.get("execution_attempt") if isinstance(claim, dict) else None
            if not bool(claim.get("success")) or not isinstance(attempt_record, dict):
                return {
                    "success": False,
                    "reason": str(claim.get("reason") or "verified_delivery_runtime_claim_failed"),
                    "external_task_id": external_task_id,
                    "reconciled_task_ids": reconciled,
                }
            execution_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
            settled = runtime.settle_execution_attempt(
                SettleTaskRuntimeExecutionAttemptCommandV1(
                    workspace=str(self.workspace),
                    identity=execution_attempt,
                    outcome="completed",
                    summary="canonical_delivery_completed_verified_and_qa_passed",
                    lock_timeout_seconds=5.0,
                    metadata={"verified_delivery_reconciliation": evidence},
                )
            )
            if not bool(settled.get("success")):
                return {
                    "success": False,
                    "reason": str(settled.get("reason") or "verified_delivery_runtime_settlement_failed"),
                    "external_task_id": external_task_id,
                    "reconciled_task_ids": reconciled,
                }
            reconciled.append(external_task_id)
        return {"success": True, "reconciled_task_ids": reconciled}

    async def _execute_quality_gate(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing quality gate for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)
        authority_port = self._factory_role_evidence_cutoff_port(context)

        abort_reason = await self._quality_gate_abort_reason(abort_checker)
        if abort_reason:
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_cancelled_before_checks",
                detail=f"Quality gate cancelled before workspace checks: {abort_reason}",
                context=context,
                status="cancelled",
            )

        remaining_seconds = self._factory_deadline_remaining_seconds(context)
        if remaining_seconds is not None and remaining_seconds < _QUALITY_GATE_MIN_START_BUDGET_SECONDS:
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_deadline_insufficient_before_checks",
                detail=(
                    "Quality gate skipped before workspace checks because the factory run deadline "
                    f"has only {remaining_seconds:.1f}s remaining"
                ),
                context=context,
            )

        workspace_checks_passed, workspace_checks_artifact = await self._run_workspace_quality_checks(run, context)
        if workspace_checks_passed is False:
            # Deterministic physical verification owns artifact/build/test/lint/
            # entrypoint failures. The workspace loop above already performs
            # bounded, same-Director-task repair and affected-command
            # revalidation. Sending the same hard failure to the advisory QA
            # LLM duplicates tokens, can overwrite the typed residual with a
            # subjective verdict, and cannot add authority. Preserve the
            # verifier artifact and stop this stage as a repairable delivery
            # failure; PM/CE remain out of the loop.
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_workspace_validation_failed",
                detail=(
                    self._workspace_quality_failure_detail(workspace_checks_artifact)
                    if workspace_checks_artifact
                    else "Workspace validation failed without an authoritative evidence artifact"
                ),
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=False,
            )
        qa_input = self._build_qa_input_with_workspace_quality_evidence(
            context.get("qa_input"),
            workspace_checks_artifact,
            run_id=run.id,
        )
        physical_qa_task_id, physical_qa_run_id = self._canonical_qa_commit_identity(run=run, context=context)
        physical_verifier_qa = bool(
            workspace_checks_artifact
            and physical_qa_task_id
            and physical_qa_run_id
            and not self._quality_gate_requires_llm_judgement(context)
        )

        abort_reason = await self._quality_gate_abort_reason(abort_checker)
        if abort_reason:
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_cancelled_before_qa",
                detail=f"Quality gate cancelled before QA judgement: {abort_reason}",
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
                status="cancelled",
            )

        remaining_seconds = self._factory_deadline_remaining_seconds(context)
        if (
            not physical_verifier_qa
            and remaining_seconds is not None
            and remaining_seconds < _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS
        ):
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_deadline_insufficient_before_qa",
                detail=(
                    "Quality gate did not start QA LLM judgement because the factory run deadline "
                    f"has only {remaining_seconds:.1f}s remaining"
                ),
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
            )

        qa_invoked = not physical_verifier_qa
        if physical_verifier_qa:
            self._write_physical_verifier_qa_report(
                run=run,
                workspace_checks_artifact=workspace_checks_artifact,
            )
            final_result = CommandResult(
                run_id=run.id,
                status="completed",
                message="physical verifier evidence passed; advisory QA LLM not required",
            )
        else:
            service = self._build_orchestration_service(context)
            qa_request_metadata, qa_wait_timeout_seconds = self._build_qa_execution_metadata(
                run_id=run.id,
                workspace_checks_artifact=workspace_checks_artifact,
                context=context,
            )
            command_result = cast(
                CommandResult,
                await self._call_with_factory_role_evidence_authority(
                    authority_port,
                    "qa",
                    lambda: service.execute_qa_run(
                        workspace=str(self.workspace),
                        target=context.get("qa_target", "Quality gate"),
                        options={
                            "input": qa_input,
                            "metadata": qa_request_metadata,
                        },
                    ),
                ),
            )
            final_result = await self._wait_run_completion(
                service,
                command_result,
                timeout_seconds=qa_wait_timeout_seconds,
                cancel_event=self._resolve_cancel_event(context),
                abort_checker=abort_checker,
            )
        final_status = str(final_result.status or "").strip().lower()
        if final_status == "cancelled":
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_qa_cancelled",
                detail=f"Quality gate QA run cancelled: {final_result.message or 'N/A'}",
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
                status="cancelled",
                qa_invoked=qa_invoked,
            )
        if final_status == "timeout":
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_qa_timeout",
                detail=f"Quality gate QA run timed out: {final_result.message or 'N/A'}",
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
                qa_invoked=qa_invoked,
            )

        qa_report_path = self._artifact_path("runtime/qa/report.json")
        loaded: dict[str, Any] | Any = {}
        parse_error: Exception | None = None
        report_ready = self._artifact_file_ready(qa_report_path)
        if report_ready:
            for _attempt in range(5):
                try:
                    report_text = await asyncio.to_thread(qa_report_path.read_text, encoding="utf-8")
                    loaded = json.loads(report_text)
                    parse_error = None
                    break
                except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    parse_error = exc
                    await asyncio.sleep(0.2)
        qa_payload: dict[str, Any] = loaded if isinstance(loaded, dict) else {}

        preexisting_authority = helpers.evaluate_canonical_factory_authority(
            self._canonical_factory_projection(run, context)
        )
        qa_commit: dict[str, Any] = {"success": False, "reason": "qa_role_report_unavailable"}
        qa_commit_attempted = False
        if preexisting_authority.qa_verdict_present:
            # Idempotent recovery/replay: the ledger verdict is authority.  A
            # stale or diagnostic report mirror must not append a competing
            # revision or overwrite an already-committed canonical decision.
            qa_commit = {
                "success": True,
                "verdict": "PASS" if preexisting_authority.qa_verdict_passed else "FAIL",
                "ok": preexisting_authority.qa_verdict_passed,
                "reason": "canonical_qa_verdict_already_present",
            }
        elif report_ready and parse_error is None and qa_payload:
            qa_commit_attempted = True
            qa_commit = await self._commit_qa_role_report_authority(
                run=run,
                context=context,
                qa_payload=qa_payload,
            )
        if qa_commit_attempted and not bool(qa_commit.get("success")):
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_qa_verdict_commit_failed",
                detail=(
                    "QA role execution completed but canonical qa.audit_verdict commit failed: "
                    f"{qa_commit.get('reason') or 'unknown'}"
                ),
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
                qa_invoked=True,
            )

        canonical_authority = await self._wait_for_canonical_quality_authority(
            run,
            context,
        )
        runtime_reconciliation = self._reconcile_verified_runtime_delivery(
            run=run,
            authority=canonical_authority,
        )
        if not bool(runtime_reconciliation.get("success")):
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_runtime_reconciliation_failed",
                detail=(
                    "Quality evidence passed but exact TaskRuntime delivery reconciliation failed: "
                    f"{runtime_reconciliation.get('reason') or 'unknown'}"
                ),
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
                qa_invoked=True,
            )
        if runtime_reconciliation.get("reconciled_task_ids"):
            canonical_authority = await self._wait_for_canonical_quality_authority(run, context)
        is_success = canonical_authority.quality_stage_authorized
        qa_report_passed = bool(qa_payload.get("passed")) if qa_payload else None
        report_consistent = qa_report_passed is None or qa_report_passed == canonical_authority.qa_verdict_passed
        output_suffix = (
            f"task_boundary_completed_verified={canonical_authority.task_boundary_completed_verified}; "
            f"qa_verdict_passed={canonical_authority.qa_verdict_passed}; "
            f"sequence_barrier_satisfied={canonical_authority.sequence_barrier_satisfied}; "
            f"evidence_policy_passed={canonical_authority.evidence_policy_passed}; "
            f"workspace_checks_diagnostic={workspace_checks_passed}; "
            f"report_ready={report_ready}; report_parse_error={parse_error or 'none'}; "
            f"report_consistent={report_consistent}; "
            f"canonical_authorized={is_success}; "
            f"canonical_reason={canonical_authority.reason_code}"
            f"; qa_commit_run={qa_commit.get('run_id') or ''}"
            f"; qa_commit_task={qa_commit.get('task_id') or ''}"
            f"; runtime_reconciled={runtime_reconciliation.get('reconciled_task_ids') or []}"
        )
        artifacts = ["runtime/qa/report.json"] if report_ready else []
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
        cancel_on_timeout: bool = True,
        authority: RunCompletionAuthority = RunCompletionAuthority.ROLE_LIFECYCLE,
    ) -> CommandResult:
        return await self._run_completion_waiter.wait(
            service,
            initial_result,
            timeout_seconds,
            cancel_event=cancel_event,
            abort_checker=abort_checker,
            cancel_on_timeout=cancel_on_timeout,
            authority=authority,
        )

    @staticmethod
    def _inflight_director_run_ids(result: CommandResult) -> tuple[str, ...]:
        """Return child run ids that explicitly crossed the soft-timeout barrier."""

        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        run_ids: list[str] = []
        if bool(metadata.get("inflight_run_continues")):
            run_id = str(result.run_id or "").strip()
            if run_id:
                run_ids.append(run_id)
        per_binding = metadata.get("per_binding")
        if isinstance(per_binding, list):
            for entry in per_binding:
                if not isinstance(entry, dict) or not bool(entry.get("inflight_run_continues")):
                    continue
                run_id = str(entry.get("run_id") or "").strip()
                if run_id:
                    run_ids.append(run_id)
        return tuple(dict.fromkeys(run_ids))

    async def _settle_inflight_director_result(
        self,
        service: OrchestrationCommandService,
        *,
        result: CommandResult,
        grace_seconds: int,
        cancel_event: asyncio.Event | None,
        abort_checker: Callable[[], Awaitable[str | None]] | None,
    ) -> tuple[CommandResult, bool]:
        """Settle every child run named by a soft-timeout result before reuse.

        The provider response and tool batch belong to one execution attempt.
        Once a wait result says that attempt is still in flight, starting a new
        Director turn would create two writers for the same task boundary. This
        method therefore acts as the parent-side commit barrier and returns only
        after each named child is terminal or the barrier itself times out.

        Complexity:
            O(b) time and memory over the number of active Director bindings;
            waits execute concurrently and are bounded by ``grace_seconds``.
        """

        run_ids = self._inflight_director_run_ids(result)
        if not run_ids:
            return result, False

        settled_results = await asyncio.gather(
            *(
                self._settle_inflight_director_run_after_timeout(
                    service,
                    run_id=run_id,
                    grace_seconds=grace_seconds,
                    cancel_event=cancel_event,
                    abort_checker=abort_checker,
                )
                for run_id in run_ids
            )
        )
        settlements: dict[str, CommandResult] = {}
        for run_id, settled in zip(run_ids, settled_results, strict=True):
            if settled is None:
                settled = CommandResult(
                    run_id=run_id,
                    status="timeout",
                    message="Director execution barrier produced no terminal result",
                    metadata={
                        "barrier_state": "timeout",
                        "barrier_timeout": True,
                        "inflight_run_continues": True,
                        "cancel_signal_sent": False,
                        "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                        "responsible_layer": "execution_control_plane",
                    },
                )
            settlements[run_id] = settled

        original_metadata = result.metadata if isinstance(result.metadata, dict) else {}
        per_binding = original_metadata.get("per_binding")
        if isinstance(per_binding, list):
            updated_bindings: list[dict[str, Any]] = []
            for raw_entry in per_binding:
                if not isinstance(raw_entry, dict):
                    continue
                entry = dict(raw_entry)
                run_id = str(entry.get("run_id") or "").strip()
                settled = settlements.get(run_id)
                if settled is not None:
                    settled_metadata = settled.metadata if isinstance(settled.metadata, dict) else {}
                    entry.update(
                        {
                            "status": str(settled.status or "").strip(),
                            "message": str(settled.message or "").strip(),
                            "settled_after_timeout": not bool(settled_metadata.get("inflight_run_continues")),
                            **settled_metadata,
                        }
                    )
                updated_bindings.append(entry)

            active = any(bool(item.get("inflight_run_continues")) for item in updated_bindings)
            failed = any(
                str(item.get("status") or "").strip().lower() in {"failed", "blocked", "cancelled", "timeout"}
                for item in updated_bindings
                if str(item.get("run_id") or "").strip()
            )
            merged_status = "timeout" if active else ("failed" if failed else "completed")
            merged_metadata = {
                **original_metadata,
                "per_binding": updated_bindings,
                "settlement_attempted": True,
                "settled_run_count": len(settlements),
                "inflight_run_continues": active,
                "barrier_state": "timeout" if active else "settled",
                "barrier_timeout": active,
            }
            if active:
                merged_metadata.update(
                    {
                        "cancel_signal_sent": False,
                        "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                        "responsible_layer": "execution_control_plane",
                    }
                )
            return (
                CommandResult(
                    run_id=str(result.run_id or run_ids[0]).strip(),
                    status=merged_status,
                    message=(
                        "Director binding execution barrier timed out"
                        if active
                        else "Director binding execution barrier settled"
                    ),
                    reason_code=result.reason_code,
                    stage_results=result.stage_results,
                    started_at=result.started_at,
                    completed_at=result.completed_at,
                    artifacts=result.artifacts,
                    metadata=merged_metadata,
                ),
                True,
            )

        settled = settlements[run_ids[0]]
        settled_metadata = settled.metadata if isinstance(settled.metadata, dict) else {}
        active = bool(settled_metadata.get("inflight_run_continues"))
        merged_metadata = {
            **original_metadata,
            **settled_metadata,
            "settlement_attempted": True,
            "settled_run_count": 1,
            "inflight_run_continues": active,
            "barrier_state": "timeout" if active else "settled",
            "barrier_timeout": active,
        }
        return (
            CommandResult(
                run_id=str(settled.run_id or result.run_id or "").strip(),
                status=str(settled.status or result.status or "").strip(),
                message=settled.message or result.message,
                reason_code=settled.reason_code or result.reason_code,
                stage_results=settled.stage_results or result.stage_results,
                started_at=settled.started_at or result.started_at,
                completed_at=settled.completed_at or result.completed_at,
                artifacts=settled.artifacts or result.artifacts,
                metadata=merged_metadata,
            ),
            True,
        )
