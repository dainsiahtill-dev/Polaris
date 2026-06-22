"""Production factory stage executor backed by ``OrchestrationCommandService``.

Holds the standalone ``OrchestrationStageExecutor`` god-class extracted from
``factory_run_service``. Behavior is preserved verbatim: this module imports
the shared data-contracts and tuning constants from ``factory_run_models`` and
keeps all cross-cell edges lazy (in-function) exactly as before.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.orchestration.orchestration_engine.public.service import OrchestrationCommandService

from polaris.cells.chief_engineer.blueprint.public import GenerateTaskBlueprintCommandV1, generate_task_blueprint
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.roles.kernel.public.service import QualityChecker
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

_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS = 3
_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_ENV = "KERNELONE_FACTORY_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT"
_DEFAULT_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT = 4

_CE_BLUEPRINT_OUTPUT_CONTRACT = """

Chief Engineer output contract:
- Return exactly one JSON object, with no Markdown fence and no surrounding prose.
- Required top-level keys: construction_plan, scope_for_apply, risk_flags.
- construction_plan must be an object that describes concrete implementation phases.
- scope_for_apply must be an array of repository-relative paths or modules.
- risk_flags must be an array, even when empty.
- Do not emit tool calls, code patches, <SESSION_PATCH>, or file edit instructions.
"""


class OrchestrationStageExecutor:
    """Production executor backed by OrchestrationCommandService."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self._fs = KernelFileSystem(str(workspace), get_default_adapter())
        self._artifact_store = ArtifactStore(self.workspace, self._fs)
        self._workspace_quality = WorkspaceQualityRunner(self.workspace)
        self._run_completion_waiter = RunCompletionWaiter(self.workspace)
        self._binding_timeout_counts: dict[str, int] = {}
        self._quarantined_bindings: set[str] = set()
        self._last_director_binding_skips: list[dict[str, Any]] = []
        self._binding_status_probe_seconds = 2.0

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
    def _compact_workspace_quality_evidence_for_qa(text: str) -> str:
        """Build a short, parseable workspace-quality JSON payload for QA."""

        try:
            payload = json.loads(str(text or ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            return helpers.compact_text_for_prompt(str(text or ""), max_chars=6000)
        if not isinstance(payload, dict):
            return helpers.compact_text_for_prompt(str(text or ""), max_chars=6000)

        commands: list[dict[str, Any]] = []
        for item in list(payload.get("commands") or []):
            if not isinstance(item, dict):
                continue
            command = item.get("command")
            if isinstance(command, list):
                command_value: list[str] | str = [str(part) for part in command]
            else:
                command_value = str(command or "")
            row: dict[str, Any] = {
                "command": command_value,
                "phase": str(item.get("phase") or ""),
                "passed": bool(item.get("passed")),
                "exit_code": item.get("exit_code"),
            }
            stdout_tail = str(item.get("stdout_tail") or "").strip()
            stderr_tail = str(item.get("stderr_tail") or "").strip()
            if stdout_tail:
                row["stdout_tail"] = helpers.compact_text_for_prompt(stdout_tail, max_chars=700)
            if stderr_tail:
                row["stderr_tail"] = helpers.compact_text_for_prompt(stderr_tail, max_chars=700)
            commands.append(row)

        repair = payload.get("repair") if isinstance(payload.get("repair"), dict) else {}
        compact_payload: dict[str, Any] = {
            "schema_version": payload.get("schema_version"),
            "source": payload.get("source"),
            "factory_run_id": payload.get("factory_run_id"),
            "workspace": payload.get("workspace"),
            "passed": bool(payload.get("passed")),
            "commands": commands,
        }
        if isinstance(repair, dict) and repair:
            compact_payload["repair"] = {
                "attempted": bool(repair.get("attempted")),
                "success": bool(repair.get("success")),
                "source_tools": [str(item) for item in list(repair.get("source_tools") or [])[:6]],
                "evidence": [
                    helpers.compact_text_for_prompt(str(item or ""), max_chars=220)
                    for item in list(repair.get("evidence") or [])[:6]
                    if str(item or "").strip()
                ],
            }
        return json.dumps(compact_payload, ensure_ascii=False, indent=2)

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

    def _director_task_ids_from_pm_tasks(self, tasks: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for index, task in enumerate(tasks, start=1):
            task_id = self._task_id(task, index)
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            ids.append(task_id)
        return ids

    def _director_requested_task_ids(self, context: dict[str, Any], pm_tasks: list[dict[str, Any]]) -> list[str] | None:
        explicit_tasks = context.get("tasks")
        if isinstance(explicit_tasks, list):
            ids: list[str] = []
            seen: set[str] = set()
            for index, item in enumerate(explicit_tasks, start=1):
                task_id = self._task_id(item, index) if isinstance(item, dict) else str(item or "").strip()
                if not task_id or task_id in seen:
                    continue
                seen.add(task_id)
                ids.append(task_id)
            return ids
        return self._director_task_ids_from_pm_tasks(pm_tasks) or None

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
            "in_design": 0,
            "in_execution": 0,
            "in_qa": 0,
            "waiting_human": 0,
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
    def _terminal_status_from_task_counts(counts: Any) -> str:
        if not isinstance(counts, dict):
            return ""

        def _count(key: str) -> int:
            try:
                return int(counts.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        active = (
            _count("pending")
            + _count("ready")
            + _count("in_progress")
            + _count("in_design")
            + _count("in_execution")
            + _count("in_qa")
            + _count("running")
            + _count("processing")
            + _count("executing")
            + _count("waiting_human")
        )
        if active > 0:
            return ""
        failed = _count("failed") + _count("blocked") + _count("cancelled") + _count("timeout")
        if failed > 0:
            return "failed"
        completed = _count("completed") + _count("success")
        total = _count("total") or sum(_count(key) for key in counts)
        if total > 0 and completed >= total:
            return "completed"
        return ""

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

    @staticmethod
    def _director_dispatch_timeout_seconds(context: dict[str, Any], *, task_count: int) -> int:
        del task_count
        raw_override = context.get("director_dispatch_timeout_seconds")
        if raw_override is not None:
            try:
                return max(1, int(raw_override))
            except (TypeError, ValueError):
                pass
        try:
            return max(1, int(context.get("timeout") or 600))
        except (TypeError, ValueError):
            return 600

    @staticmethod
    def _director_binding_timeout_quarantine_count() -> int:
        raw = os.environ.get(_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_ENV, "")
        try:
            value = int(str(raw).strip()) if str(raw).strip() else _DEFAULT_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT
        except (TypeError, ValueError):
            value = _DEFAULT_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT
        return max(2, value)

    # ── Director binding fanout ────────────────────────────────────────────

    @staticmethod
    def _director_binding_identity(provider_id: str, model: str, binding_id: str = "") -> str:
        return f"{str(provider_id or '').strip()}|{str(model or '').strip()}|{str(binding_id or '').strip()}"

    def _record_director_binding_skip(
        self,
        *,
        provider_id: str,
        model: str,
        binding_id: str,
        reason: str,
    ) -> None:
        skip = {
            "provider_id": str(provider_id or "").strip(),
            "model": str(model or "").strip(),
            "binding_id": str(binding_id or "").strip(),
            "reason": str(reason or "").strip() or "binding_unavailable",
        }
        if not skip["provider_id"] or not skip["model"]:
            return
        skips = getattr(self, "_last_director_binding_skips", [])
        identity = self._director_binding_identity(skip["provider_id"], skip["model"], skip["binding_id"])
        if any(
            self._director_binding_identity(
                str(item.get("provider_id") or ""),
                str(item.get("model") or ""),
                str(item.get("binding_id") or ""),
            )
            == identity
            for item in skips
            if isinstance(item, dict)
        ):
            return
        skips.append(skip)
        self._last_director_binding_skips = skips

    def _director_readiness_skip_reasons(self, context: dict[str, Any] | None = None) -> dict[str, str]:
        if context is None:
            context = {}
        try:
            from polaris.bootstrap.config import Settings
            from polaris.cells.runtime.projection.internal.llm_status import build_llm_status
        except ImportError as exc:
            logger.debug("Director readiness skip resolution unavailable: %s", exc)
            return {}
        try:
            settings = context.get("settings") or Settings(workspace=Path(self.workspace))
            status = build_llm_status(settings)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Director readiness status unavailable: %s", exc)
            return {}
        roles = status.get("roles") if isinstance(status, dict) else {}
        director = roles.get("director") if isinstance(roles, dict) else {}
        skipped = director.get("skipped_bindings") if isinstance(director, dict) else None
        if not isinstance(skipped, list):
            return {}
        reasons: dict[str, str] = {}
        for item in skipped:
            if not isinstance(item, dict):
                continue
            provider_id = str(item.get("provider_id") or "").strip()
            model = str(item.get("model") or "").strip()
            binding_id = str(item.get("binding_id") or "").strip()
            reason = str(item.get("reason") or "readiness_skipped").strip()
            if not provider_id or not model:
                continue
            reasons[self._director_binding_identity(provider_id, model, binding_id)] = reason
            reasons.setdefault(self._director_binding_identity(provider_id, model, ""), reason)
        return reasons

    def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        self._last_director_binding_skips = []
        try:
            from polaris.kernelone.llm.runtime_config import get_role_binding_slots, is_role_binding_healthy
        except (ImportError, RuntimeError) as exc:
            logger.debug("Director binding fanout resolution unavailable: %s", exc)
            return []
        try:
            slots = get_role_binding_slots("director")
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.debug("Director binding slots unavailable: %s", exc)
            return []
        if len(slots) <= 1:
            return []
        readiness_skip_reasons = self._director_readiness_skip_reasons(context)
        try:
            from polaris.cells.orchestration.pm_dispatch.public.service import reachable_provider_pool

            provider_ids = tuple(dict.fromkeys(str(slot.provider_id) for slot in slots if slot.provider_id))
            live_providers = set(reachable_provider_pool(provider_ids))
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Director provider reachability probe failed: %s", exc)
            live_providers = {str(slot.provider_id) for slot in slots if slot.provider_id}
        bindings: list[dict[str, str]] = []
        cooldown_candidates: list[dict[str, str]] = []
        seen_keys: set[str] = set()

        def _append_binding(binding: dict[str, str]) -> None:
            key = f"{binding['provider_id']}|{binding['model']}"
            if key in seen_keys:
                return
            seen_keys.add(key)
            bindings.append(binding)

        for slot in slots:
            pid = str(slot.provider_id or "").strip()
            model = str(slot.model or "").strip()
            binding_id = str(slot.binding_id or "").strip()
            if not pid or pid not in live_providers:
                if pid and model:
                    self._record_director_binding_skip(
                        provider_id=pid,
                        model=model,
                        binding_id=binding_id,
                        reason="provider_unreachable",
                    )
                continue
            readiness_reason = readiness_skip_reasons.get(
                self._director_binding_identity(pid, model, binding_id)
            ) or readiness_skip_reasons.get(self._director_binding_identity(pid, model, ""))
            if readiness_reason:
                if readiness_reason == "role_binding_cooldown":
                    cooldown_candidates.append(
                        {
                            "provider_id": pid,
                            "model": model,
                            "binding_id": binding_id,
                        }
                    )
                    continue
                self._record_director_binding_skip(
                    provider_id=pid,
                    model=model,
                    binding_id=binding_id,
                    reason=readiness_reason,
                )
                continue
            if not is_role_binding_healthy(
                "director",
                provider_id=pid,
                model=model,
                binding_id=binding_id or None,
            ):
                cooldown_candidates.append(
                    {
                        "provider_id": pid,
                        "model": model,
                        "binding_id": binding_id,
                    }
                )
                continue
            _append_binding(
                {
                    "provider_id": pid,
                    "model": model,
                    "binding_id": binding_id,
                }
            )
        if not bindings and cooldown_candidates:
            logger.warning(
                "Director binding cooldown would starve dispatch; allowing %d cooled binding(s)",
                len(cooldown_candidates),
            )
            for binding in cooldown_candidates:
                _append_binding(binding)
        else:
            for binding in cooldown_candidates:
                self._record_director_binding_skip(
                    provider_id=binding["provider_id"],
                    model=binding["model"],
                    binding_id=binding.get("binding_id", ""),
                    reason="role_binding_cooldown",
                )
        if len(bindings) <= 1 and not getattr(self, "_last_director_binding_skips", []):
            return []
        logger.info("Director binding fanout: %d reachable binding(s)", len(bindings))
        return bindings

    async def _execute_director_binding_fanout(
        self,
        *,
        service: Any,
        workspace: str,
        tasks: list[str] | None,
        base_options: dict[str, Any],
        bindings: list[dict[str, str]],
        timeout_seconds: int = 600,
        cancel_event: asyncio.Event | None = None,
        abort_checker: Any = None,
        skipped_bindings: list[dict[str, Any]] | None = None,
    ) -> CommandResult:
        terminal_statuses = {"completed", "success", "failed", "cancelled", "timeout", "partial"}
        submitted: list[tuple[dict[str, str], CommandResult]] = []
        readiness_skipped = [dict(item) for item in list(skipped_bindings or []) if isinstance(item, dict)]

        def _binding_key(binding: dict[str, str]) -> str:
            return f"{binding['provider_id']}:{binding['model']}:{binding.get('binding_id', '')}"

        def _backend_failure_reason(result: CommandResult) -> str:
            status = str(result.status or "").strip().lower()
            if status == "timeout":
                return "timeout"
            text = " ".join(
                str(item or "")
                for item in (
                    result.reason_code,
                    result.message,
                    (result.metadata or {}).get("error") if isinstance(result.metadata, dict) else "",
                )
            ).lower()
            backend_markers = (
                "provider_connectivity_unavailable",
                "connection refused",
                "cannot connect",
                "connect timeout",
                "read timeout",
                "timed out",
                "timeout",
                "circuit_open",
                "llm call error",
                "binding_fanout_error",
            )
            if any(marker in text for marker in backend_markers):
                return "provider_backend_failure"
            return ""

        active_bindings = []
        quarantined_skipped = []
        for binding in bindings:
            key = _binding_key(binding)
            if key in self._quarantined_bindings:
                quarantined_skipped.append(binding)
                logger.info("Skipping quarantined binding: %s", key)
            else:
                active_bindings.append(binding)

        async def _run_binding(binding: dict[str, str]) -> CommandResult:
            binding_opts = dict(base_options)
            binding_opts.setdefault("llm_call_timeout_seconds", int(timeout_seconds))
            binding_opts.setdefault("director_llm_timeout_seconds", int(timeout_seconds))
            raw_binding_metadata = base_options.get("metadata")
            binding_metadata: dict[str, Any] = (
                dict(raw_binding_metadata) if isinstance(raw_binding_metadata, dict) else {}
            )
            binding_opts["metadata"] = {
                **binding_metadata,
                "binding_override": {
                    "provider_id": binding["provider_id"],
                    "model": binding["model"],
                    "binding_id": binding.get("binding_id", ""),
                },
            }
            return await service.execute_director_run(workspace=workspace, tasks=tasks, options=binding_opts)

        gathered = await asyncio.gather(*[_run_binding(b) for b in active_bindings], return_exceptions=True)
        for idx, item in enumerate(gathered):
            if isinstance(item, Exception):
                logger.warning("Director binding fanout[%d] raised: %s", idx, item)
                submitted.append(
                    (
                        active_bindings[idx],
                        CommandResult(
                            run_id="", status="failed", message=str(item), reason_code="BINDING_FANOUT_ERROR"
                        ),
                    )
                )
            elif isinstance(item, CommandResult):
                submitted.append((active_bindings[idx], item))

        async def _wait_submitted_binding(
            binding: dict[str, str],
            sub_result: CommandResult,
        ) -> tuple[dict[str, str], CommandResult]:
            if sub_result.status in terminal_statuses or not str(sub_result.run_id or "").strip():
                return binding, sub_result
            run_id = str(sub_result.run_id or "").strip()
            wait_task = asyncio.create_task(
                self._wait_run_completion(
                    service,
                    sub_result,
                    timeout_seconds=timeout_seconds,
                    cancel_event=cancel_event,
                    abort_checker=abort_checker,
                )
            )
            try:
                while True:
                    probe_seconds = max(0.01, float(getattr(self, "_binding_status_probe_seconds", 2.0)))
                    done, _ = await asyncio.wait(
                        {wait_task},
                        timeout=probe_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if wait_task in done:
                        return binding, wait_task.result()

                    if cancel_event is not None and cancel_event.is_set():
                        wait_task.cancel()
                        return binding, CommandResult(
                            run_id=run_id,
                            status="cancelled",
                            message="Run cancelled: factory_cancelled",
                        )

                    status_probe: CommandResult | None = None
                    with contextlib.suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
                        status_probe = await service.query_run_status(run_id)
                    if status_probe is None:
                        continue

                    probed_status = str(status_probe.status or "").strip().lower()
                    if probed_status in terminal_statuses:
                        wait_task.cancel()
                        return binding, status_probe

                    metadata = status_probe.metadata if isinstance(status_probe.metadata, dict) else {}
                    count_status = self._terminal_status_from_task_counts(metadata.get("task_status_counts"))
                    if count_status:
                        continue
            except (RuntimeError, OSError, ValueError, TypeError) as exc:
                logger.warning("Director binding fanout wait failed for run %s: %s", sub_result.run_id, exc)
                return binding, CommandResult(run_id=sub_result.run_id, status="failed", message=f"Wait failed: {exc}")
            finally:
                if not wait_task.done():
                    wait_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await wait_task

        final_results: list[tuple[dict[str, str], CommandResult]] = list(
            await asyncio.gather(*[_wait_submitted_binding(binding, sub_result) for binding, sub_result in submitted])
        )

        quarantine_threshold = self._director_binding_timeout_quarantine_count()
        for binding, result in final_results:
            key = _binding_key(binding)
            if str(result.status or "").strip().lower() == "timeout":
                self._binding_timeout_counts[key] = self._binding_timeout_counts.get(key, 0) + 1
                if self._binding_timeout_counts[key] >= quarantine_threshold:
                    self._quarantined_bindings.add(key)
                    logger.warning(
                        "Quarantining binding %s after %d consecutive timeouts",
                        key,
                        self._binding_timeout_counts[key],
                    )
            else:
                self._binding_timeout_counts[key] = 0
            backend_failure_reason = _backend_failure_reason(result)
            if backend_failure_reason:
                with contextlib.suppress(ImportError, RuntimeError, TypeError, ValueError):
                    from polaris.kernelone.llm.runtime_config import mark_role_binding_unhealthy

                    mark_role_binding_unhealthy(
                        "director",
                        provider_id=binding["provider_id"],
                        model=binding["model"],
                        binding_id=binding.get("binding_id") or None,
                    )

        per_binding: list[dict[str, Any]] = []
        success_count = 0
        fail_count = 0
        first_run_id = ""
        for binding, result in final_results:
            if not first_run_id and result.run_id:
                first_run_id = result.run_id
            status = str(result.status or "").strip().lower()
            if status in {"completed", "success"}:
                success_count += 1
            else:
                fail_count += 1
            key = _binding_key(binding)
            entry: dict[str, Any] = {
                "provider_id": binding["provider_id"],
                "model": binding["model"],
                "binding_id": binding.get("binding_id", ""),
                "run_id": result.run_id or "",
                "status": result.status or "unknown",
                "message": result.message or "",
            }
            result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
            for evidence_key in (
                "cancel_signal_sent",
                "terminal_source",
                "queried_status",
                "task_status_counts",
            ):
                if evidence_key in result_metadata:
                    entry[evidence_key] = result_metadata[evidence_key]
            if status == "timeout":
                entry["timeout_count"] = self._binding_timeout_counts.get(key, 0)
                if key in self._quarantined_bindings:
                    entry["quarantined"] = True
                    entry["quarantine_reason"] = "consecutive_timeout"
            backend_failure_reason = _backend_failure_reason(result)
            if backend_failure_reason:
                entry["backend_failure_reason"] = backend_failure_reason
            per_binding.append(entry)

        for binding in quarantined_skipped:
            key = _binding_key(binding)
            per_binding.append(
                {
                    "provider_id": binding["provider_id"],
                    "model": binding["model"],
                    "binding_id": binding.get("binding_id", ""),
                    "run_id": "",
                    "status": "quarantined",
                    "message": "Skipped due to consecutive timeouts",
                    "quarantined": True,
                    "quarantine_reason": "consecutive_timeout",
                    "timeout_count": self._binding_timeout_counts.get(key, 0),
                }
            )

        for binding in readiness_skipped:
            provider_id = str(binding.get("provider_id") or "").strip()
            model = str(binding.get("model") or "").strip()
            binding_id = str(binding.get("binding_id") or "").strip()
            if not provider_id or not model:
                continue
            per_binding.append(
                {
                    "provider_id": provider_id,
                    "model": model,
                    "binding_id": binding_id,
                    "run_id": "",
                    "status": "skipped",
                    "message": "Skipped by Director binding readiness filter",
                    "skipped": True,
                    "skip_reason": str(binding.get("reason") or "binding_unavailable").strip(),
                }
            )

        quarantined_count = sum(1 for entry in per_binding if entry.get("quarantined"))
        skipped_count = len(quarantined_skipped)
        readiness_skipped_count = sum(
            1 for entry in per_binding if entry.get("skipped") and not entry.get("quarantined")
        )
        merged_status = "completed" if success_count > 0 and fail_count == 0 else "failed"
        total_binding_count = len(bindings) + readiness_skipped_count
        return CommandResult(
            run_id=first_run_id,
            status=merged_status,
            message=(
                f"Director binding fanout: {total_binding_count} bindings, {success_count} succeeded, "
                f"{fail_count} failed, {quarantined_count} quarantined, "
                f"{readiness_skipped_count} readiness-skipped"
            ),
            metadata={
                "binding_fanout": True,
                "binding_count": total_binding_count,
                "active_binding_count": len(active_bindings),
                "quarantined_binding_count": quarantined_count,
                "quarantined_skipped_count": skipped_count,
                "timeout_quarantine_threshold": quarantine_threshold,
                "readiness_skipped_count": readiness_skipped_count,
                "per_binding": per_binding,
                "execution_mode": str(base_options.get("execution_mode", "")).strip(),
                "max_workers": int(base_options.get("max_workers", 0)),
            },
        )

    @staticmethod
    def _build_per_binding_route_events(per_binding: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        events: list[dict[str, Any]] = []
        for entry in per_binding:
            if not isinstance(entry, dict):
                continue
            provider_id = str(entry.get("provider_id") or "").strip()
            model = str(entry.get("model") or "").strip()
            binding_id = str(entry.get("binding_id") or "").strip()
            run_id = str(entry.get("run_id") or "").strip()
            status = str(entry.get("status") or "").strip().lower()
            if not provider_id or not model:
                continue
            event: dict[str, Any] = {
                "event": "llm_route_terminal",
                "role": "director",
                "provider_id": provider_id,
                "model": model,
                "binding_id": binding_id,
                "run_id": run_id,
                "status": status,
                "source": "llm",
                "cache_hit": False,
                "invocation": True,
                "terminal": True,
                "fail_closed": False,
                "timestamp": now_iso,
            }
            if status == "timeout" or entry.get("quarantined"):
                event["timeout_count"] = entry.get("timeout_count", 0)
            if entry.get("quarantined"):
                event["quarantined"] = True
                event["quarantine_reason"] = entry.get("quarantine_reason", "")
            if entry.get("skipped"):
                event["skipped"] = True
                event["skip_reason"] = entry.get("skip_reason", "")
                event["invocation"] = False
                event["fail_closed"] = True
            events.append(event)
        return events

    @staticmethod
    def _build_fail_closed_director_route_events(
        *,
        attempts: list[dict[str, Any]],
        stage_signals: list[dict[str, Any]],
        per_binding_route_events: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            from polaris.cells.factory.pipeline.internal.bench_gates import _norm_text, resolve_expected_llm_bindings
        except (ImportError, RuntimeError):
            return []
        expected = resolve_expected_llm_bindings(("director",))
        configured = expected.get("director") or []
        if not configured:
            return []
        observed_providers: set[str] = set()
        for event in per_binding_route_events or []:
            if not isinstance(event, dict):
                continue
            provider = _norm_text(event.get("provider_id") or event.get("provider"))
            model = _norm_text(event.get("model"))
            if provider and model:
                observed_providers.add(f"{provider}|{model}")
        for attempt in attempts:
            metadata = attempt.get("metadata") if isinstance(attempt, dict) else {}
            if not isinstance(metadata, dict):
                continue
            provider = _norm_text(metadata.get("provider_id") or metadata.get("provider"))
            model = _norm_text(metadata.get("model"))
            if provider and model:
                observed_providers.add(f"{provider}|{model}")
        for signal in stage_signals:
            if not isinstance(signal, dict):
                continue
            detail = str(signal.get("detail") or "")
            for binding in configured:
                provider = _norm_text(binding.get("provider_id") or binding.get("provider"))
                model = _norm_text(binding.get("model"))
                if provider and model and provider in detail and model in detail:
                    observed_providers.add(f"{provider}|{model}")
        now_iso = datetime.now(timezone.utc).isoformat()
        fail_closed_events: list[dict[str, Any]] = []
        for binding in configured:
            provider = _norm_text(binding.get("provider_id") or binding.get("provider"))
            model = _norm_text(binding.get("model"))
            binding_id = _norm_text(binding.get("binding_id"))
            key = f"{provider}|{model}"
            if not provider or not model or key in observed_providers:
                continue
            fail_closed_events.append(
                {
                    "event": "llm_route_fail_closed",
                    "role": "director",
                    "provider_id": provider,
                    "model": model,
                    "binding_id": binding_id,
                    "source": "diagnostic",
                    "cache_hit": False,
                    "invocation": True,
                    "terminal": False,
                    "fail_closed": True,
                    "fail_closed_reason": "no_dispatch_evidence_for_binding",
                    "timestamp": now_iso,
                }
            )
        return fail_closed_events

    @staticmethod
    def _reclassify_binding_coverage_signals(
        stage_signals: list[dict[str, Any]],
        per_binding_route_events: list[dict[str, Any]],
    ) -> None:
        if not per_binding_route_events:
            return
        try:
            from polaris.cells.factory.pipeline.internal.bench_gates import _norm_text, resolve_expected_llm_bindings
        except (ImportError, RuntimeError):
            return
        expected = resolve_expected_llm_bindings(("director",))
        configured = expected.get("director") or []
        if not configured:
            return
        observed_loose: set[str] = set()
        for event in per_binding_route_events:
            if not isinstance(event, dict):
                continue
            provider = _norm_text(event.get("provider_id") or event.get("provider"))
            model = _norm_text(event.get("model"))
            if provider and model:
                observed_loose.add(f"{provider}|{model}")
        configured_loose: set[str] = set()
        for binding in configured:
            provider = _norm_text(binding.get("provider_id") or binding.get("provider"))
            model = _norm_text(binding.get("model"))
            if provider and model:
                configured_loose.add(f"{provider}|{model}")
        if not configured_loose or configured_loose != observed_loose:
            return
        has_timeout = any(
            str(ev.get("status") or "").strip().lower() == "timeout"
            for ev in per_binding_route_events
            if isinstance(ev, dict)
        )
        if not has_timeout:
            return
        for i, signal in enumerate(stage_signals):
            if not isinstance(signal, dict):
                continue
            if signal.get("code") != "director.binding_coverage_incomplete":
                continue
            timeout_bindings = [
                str(ev.get("binding_id") or f"{ev.get('provider_id')}|{ev.get('model')}")
                for ev in per_binding_route_events
                if isinstance(ev, dict) and str(ev.get("status") or "").strip().lower() == "timeout"
            ]
            stage_signals[i] = {
                "code": "director.binding_timeout",
                "severity": "error",
                "detail": f"All director bindings have terminal evidence but {len(timeout_bindings)} timed out: {', '.join(timeout_bindings[:8])}",
                "timeout_bindings": timeout_bindings,
                "observed_count": len(per_binding_route_events),
                "multi_route_required": True,
            }
            break

    def _validate_director_binding_coverage(
        self,
        additional_events: list[dict[str, Any]] | None = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        try:
            from polaris.cells.factory.pipeline.internal.bench_gates import (
                build_llm_route_audit,
                collect_llm_events,
                resolve_expected_llm_bindings,
            )
        except (ImportError, RuntimeError) as exc:
            return False, [
                {
                    "code": "director.binding_coverage_audit_unavailable",
                    "severity": "error",
                    "detail": f"Director binding coverage audit is unavailable: {exc}",
                }
            ]
        expected = resolve_expected_llm_bindings(("director",))
        configured = expected.get("director") or []
        if not configured:
            return True, []
        try:
            events = collect_llm_events(self.workspace, None)
        except (RuntimeError, OSError, ValueError, TypeError):
            events = []
        if additional_events:
            seen_keys: set[tuple[str, ...]] = set()
            for ev in events:
                key = (
                    str(ev.get("event") or ""),
                    str(ev.get("provider_id") or ""),
                    str(ev.get("model") or ""),
                    str(ev.get("binding_id") or ""),
                    str(ev.get("run_id") or ""),
                )
                seen_keys.add(key)
            for ev in additional_events:
                if not isinstance(ev, dict):
                    continue
                key = (
                    str(ev.get("event") or ""),
                    str(ev.get("provider_id") or ""),
                    str(ev.get("model") or ""),
                    str(ev.get("binding_id") or ""),
                    str(ev.get("run_id") or ""),
                )
                if key not in seen_keys:
                    events.append(ev)
                    seen_keys.add(key)
        audit = build_llm_route_audit(
            events, expected_bindings=expected, required_roles=("director",), require_all_director_routes=True
        )
        if audit.get("ok"):
            return True, []
        director_result = audit.get("roles", {}).get("director", {})
        missing = list(director_result.get("missing_bindings") or [])
        observed_count = int(director_result.get("observed_count") or 0)
        fail_closed_count = int(director_result.get("fail_closed_count") or 0)
        signals: list[dict[str, Any]] = []
        if missing:
            signals.append(
                {
                    "code": "director.binding_coverage_incomplete",
                    "severity": "error",
                    "detail": f"Not all configured director bindings produced real LLM evidence. Observed={observed_count}, missing={len(missing)}, fail_closed(diagnostic)={fail_closed_count}. Missing: {', '.join(missing[:8])}",
                    "missing_bindings": missing,
                    "observed_count": observed_count,
                    "fail_closed_count": fail_closed_count,
                    "multi_route_required": True,
                }
            )
        elif observed_count == 0:
            signals.append(
                {
                    "code": "director.no_real_llm_evidence",
                    "severity": "error",
                    "detail": "No real LLM terminal evidence found for any configured director binding.",
                    "observed_count": 0,
                    "fail_closed_count": fail_closed_count,
                }
            )
        else:
            signals.append(
                {
                    "code": "director.binding_coverage_failed",
                    "severity": "error",
                    "detail": str(audit.get("summary") or "Director binding coverage audit failed"),
                    "observed_count": observed_count,
                    "fail_closed_count": fail_closed_count,
                    "multi_route_required": True,
                }
            )
        return False, signals

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

    @staticmethod
    def _ce_extract_llm_evidence(ce_result: Any, *, task_id: str, run_id: str) -> dict[str, Any]:
        def _walk_values(root: Any, keys: set[str]) -> Any:
            stack: list[Any] = [root]
            seen_ids: set[int] = set()
            while stack:
                item = stack.pop()
                item_id = id(item)
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                if isinstance(item, dict):
                    for key, value in item.items():
                        normalized_key = str(key or "").strip().lower()
                        if normalized_key in keys and str(value or "").strip():
                            return value
                    stack.extend(item.values())
                elif isinstance(item, (list, tuple)):
                    stack.extend(item)
            return None

        metadata = dict(getattr(ce_result, "metadata", {}) or {})
        usage = dict(getattr(ce_result, "usage", {}) or {})
        roots: list[Any] = [metadata, usage, ce_result]
        provider = ""
        model = ""
        cache_hit = False
        for root in roots:
            if not provider:
                provider = str(_walk_values(root, {"provider_id", "provider", "providerid"}) or "").strip()
            if not model:
                model = str(_walk_values(root, {"model", "model_id", "modelid"}) or "").strip()
            cache_value = _walk_values(root, {"cache_hit", "cached", "cachehit"})
            if cache_value is not None:
                cache_hit = bool(cache_value)
        if not provider:
            provider = "unknown"
        if not model:
            model = "unknown"

        evidence: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "cache_hit": cache_hit,
            "role": "chief_engineer",
            "task_id": task_id,
            "run_id": run_id,
        }
        if provider == "unknown" or model == "unknown":
            missing_parts: list[str] = []
            if provider == "unknown":
                missing_parts.append("provider_id/provider")
            if model == "unknown":
                missing_parts.append("model/model_id")
            evidence["provider_model_unknown"] = True
            evidence["provider_model_unknown_reason"] = (
                "Runtime result did not contain "
                + " and ".join(missing_parts)
                + "; check RoleExecutionKernel and RoleRuntimeService metadata propagation"
            )
        final_context_audit = _walk_values(roots, {"final_request_context_audit", "finalrequestcontextaudit"})
        if isinstance(final_context_audit, dict):
            evidence["final_request_context_audit"] = dict(final_context_audit)
        context_os_audit = _walk_values(roots, {"context_os_audit", "contextosaudit"})
        if isinstance(context_os_audit, dict):
            evidence["context_os_audit"] = dict(context_os_audit)
        context_snapshot_ref = str(_walk_values(roots, {"context_snapshot_ref", "contextsnapshotref"}) or "").strip()
        if context_snapshot_ref:
            evidence["context_snapshot_ref"] = context_snapshot_ref
        kernel_repair_reasons = _walk_values(roots, {"kernel_repair_reasons", "kernelrepairreasons"})
        if isinstance(kernel_repair_reasons, list):
            evidence["kernel_repair_reasons"] = [str(item) for item in kernel_repair_reasons]
        return evidence

    @staticmethod
    def _ce_review_schema_failure_is_recoverable(ce_result: Any, *, raw_output: str) -> bool:
        if not raw_output.strip():
            return False
        if "<SESSION_PATCH" in raw_output or "</SESSION_PATCH>" in raw_output:
            return False
        failure_text = " ".join(
            str(value or "")
            for value in (
                getattr(ce_result, "error_code", None),
                getattr(ce_result, "error_message", None),
            )
        ).lower()
        return any(
            token in failure_text
            for token in (
                "验证失败",
                "validation_failed",
                "no json object matched chief_engineer blueprint keys",
                "json解析错误",
            )
        )

    @staticmethod
    def _attach_ce_llm_evidence(signal: dict[str, Any], evidence: dict[str, Any]) -> None:
        for key in (
            "final_request_context_audit",
            "context_os_audit",
            "context_snapshot_ref",
            "kernel_repair_reasons",
        ):
            if key in evidence:
                signal[key] = evidence[key]

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
                task_context = self._task_blueprint_context(task, run_id=run.id, index=index)
                task_context.update(
                    {
                        "cognitive_runtime_mode": "off",
                        "cognitive_runtime_enabled": False,
                        "cognitive_runtime_required": False,
                        "suppress_working_memory_contract": True,
                        "suppress_tool_policy_prompt": True,
                        "disable_internal_tool_rounds": True,
                        "_transaction_kernel_forced_tool_definitions": [],
                        "_transaction_kernel_forced_tool_choice": "none",
                    }
                )
                ce_objective = f"{objective.strip()}{_CE_BLUEPRINT_OUTPUT_CONTRACT}"

                # Build command for RoleRuntimeService
                command = ExecuteRoleTaskCommandV1(
                    role="chief_engineer",
                    task_id=task_id,
                    workspace=str(self.workspace),
                    objective=ce_objective,
                    run_id=run.id,
                    context=task_context,
                    metadata={
                        "constraints": self._task_blueprint_constraints(task),
                        "source": "factory_stage_executor.chief_engineer_review",
                        "cognitive_runtime_mode": "off",
                        "cognitive_runtime_enabled": False,
                        "cognitive_runtime_required": False,
                        "validate_output": True,
                        "max_retries": 1,
                    },
                )

                # Execute via RoleRuntimeService (real LLM call)
                ce_result = await ce_service.execute_role_task(command)
                ce_evidence = self._ce_extract_llm_evidence(ce_result, task_id=task_id, run_id=run.id)
                ce_provider = str(ce_evidence.get("provider") or "unknown")
                ce_model = str(ce_evidence.get("model") or "unknown")
                raw_output = str(getattr(ce_result, "output", "") or "")

                # Check if CE LLM call succeeded (fail-closed)
                recovered_review_schema_failure = False
                if not ce_result.ok:
                    recovered_review_schema_failure = self._ce_review_schema_failure_is_recoverable(
                        ce_result,
                        raw_output=raw_output,
                    )
                    error_signal: dict[str, Any] = {
                        "code": "chief_engineer.llm_review_failed",
                        "severity": "warning" if recovered_review_schema_failure else "error",
                        "detail": ce_result.error_message or ce_result.error_code or "CE LLM call failed",
                        "task_id": task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "recoverable": recovered_review_schema_failure,
                    }
                    if ce_evidence.get("provider_model_unknown"):
                        error_signal["provider_model_unknown"] = True
                        error_signal["provider_model_unknown_reason"] = str(
                            ce_evidence.get("provider_model_unknown_reason") or ""
                        )
                    self._attach_ce_llm_evidence(error_signal, ce_evidence)
                    stage_signals.append(error_signal)
                    if not recovered_review_schema_failure:
                        continue

                task_error_count_before = len(stage_signals)
                if ce_evidence.get("provider_model_unknown"):
                    stage_signals.append(
                        {
                            "code": "chief_engineer.llm_evidence_missing",
                            "severity": "error",
                            "detail": str(ce_evidence.get("provider_model_unknown_reason") or ""),
                            "task_id": task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                            "provider_model_unknown": True,
                        }
                    )
                else:
                    # Emit audit event for LLM call once real provider/model evidence exists.
                    self._emit_audit_event(
                        "chief_engineer.llm_call",
                        provider=ce_provider,
                        model=ce_model,
                        cache_hit=bool(ce_evidence.get("cache_hit")),
                        task_id=task_id,
                        run_id=run.id,
                    )

                if not recovered_review_schema_failure and (
                    "<SESSION_PATCH" in raw_output or "</SESSION_PATCH>" in raw_output
                ):
                    stage_signals.append(
                        {
                            "code": "chief_engineer.session_patch_output_rejected",
                            "severity": "error",
                            "detail": "CE returned SESSION_PATCH content instead of the required blueprint JSON object",
                            "task_id": task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                        }
                    )
                if not recovered_review_schema_failure:
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
                                or "CE output failed schema validation",
                                "task_id": task_id,
                                "provider": ce_provider,
                                "model": ce_model,
                                "quality_score": float(quality_result.quality_score),
                                "suggestions": list(quality_result.suggestions),
                            }
                        )

                if len(stage_signals) > task_error_count_before:
                    continue

                # Convert to blueprint result format (deterministic structure generator)
                result = generate_task_blueprint(
                    GenerateTaskBlueprintCommandV1(
                        task_id=task_id,
                        workspace=str(self.workspace),
                        objective=objective,
                        run_id=run.id,
                        constraints=self._task_blueprint_constraints(task),
                        context=task_context,
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
                    "llm_evidence": ce_evidence,
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
        requested_task_ids = self._director_requested_task_ids(context, pm_tasks)

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
            director_binding_fanout = self._resolve_director_binding_fanout(context)
            director_binding_skips = list(getattr(self, "_last_director_binding_skips", []))

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

                raw_context_metadata = context.get("metadata")
                context_metadata: dict[str, Any] = (
                    dict(raw_context_metadata) if isinstance(raw_context_metadata, dict) else {}
                )
                base_options: dict[str, Any] = {
                    "task_filter": effective_task_filter,
                    "max_workers": max_workers,
                    "execution_mode": execution_mode,
                    "dispatch_mode": "mainline-full",
                    "metadata": {
                        **context_metadata,
                        "factory_run_id": str(context.get("factory_run_id") or run.id or "").strip(),
                        "factory_stage": "director_dispatch",
                        "director_binding_skips": director_binding_skips,
                    },
                }
                director_timeout_seconds = self._director_dispatch_timeout_seconds(
                    context,
                    task_count=len(pm_tasks),
                )
                base_options["llm_call_timeout_seconds"] = int(
                    context.get("llm_call_timeout_seconds") or director_timeout_seconds
                )
                base_options["director_llm_timeout_seconds"] = int(
                    context.get("director_llm_timeout_seconds")
                    or context.get("llm_call_timeout_seconds")
                    or director_timeout_seconds
                )
                if director_binding_fanout:
                    command_result = await self._execute_director_binding_fanout(
                        service=service,
                        workspace=str(self.workspace),
                        tasks=requested_task_ids,
                        base_options=base_options,
                        bindings=director_binding_fanout,
                        timeout_seconds=director_timeout_seconds,
                        cancel_event=self._resolve_cancel_event(context),
                        abort_checker=abort_checker,
                        skipped_bindings=director_binding_skips,
                    )
                    last_command_result = command_result
                    director_result = command_result
                elif director_binding_skips:
                    per_binding = [
                        {
                            "provider_id": str(binding.get("provider_id") or "").strip(),
                            "model": str(binding.get("model") or "").strip(),
                            "binding_id": str(binding.get("binding_id") or "").strip(),
                            "run_id": "",
                            "status": "skipped",
                            "message": "Skipped by Director binding readiness filter",
                            "skipped": True,
                            "skip_reason": str(binding.get("reason") or "binding_unavailable").strip(),
                        }
                        for binding in director_binding_skips
                        if isinstance(binding, dict)
                    ]
                    command_result = CommandResult(
                        run_id="",
                        status="failed",
                        message="No available Director binding after readiness filtering",
                        reason_code="DIRECTOR_BINDINGS_UNAVAILABLE",
                        metadata={
                            "binding_fanout": True,
                            "binding_count": len(per_binding),
                            "active_binding_count": 0,
                            "readiness_skipped_count": len(per_binding),
                            "per_binding": per_binding,
                            "execution_mode": execution_mode,
                            "max_workers": max_workers,
                        },
                    )
                    last_command_result = command_result
                    director_result = command_result
                else:
                    command_result = await service.execute_director_run(
                        workspace=str(self.workspace),
                        tasks=requested_task_ids,
                        options=base_options,
                    )
                    last_command_result = command_result
                    director_timeout_seconds = self._director_dispatch_timeout_seconds(
                        context,
                        task_count=len(pm_tasks),
                    )
                    director_result = await self._wait_run_completion(
                        service,
                        command_result,
                        timeout_seconds=director_timeout_seconds,
                        cancel_event=self._resolve_cancel_event(context),
                        abort_checker=abort_checker,
                    )
                final_result = director_result
                if str(director_result.status or "").strip().lower() == "cancelled":
                    break

                after_stats = self._read_taskboard_stats()
                metadata_payload = director_result.metadata if isinstance(director_result.metadata, dict) else {}
                metadata_progress = self._metadata_indicates_execution(metadata_payload)
                # When upstream is non-success, only count metadata progress if there
                # are completed tasks (forward movement), not just failed-only evidence.
                # Failed-only metadata should not suppress specific error handling.
                director_status_early = str(director_result.status or "").strip().lower()
                if director_status_early not in {"completed", "success"} and metadata_progress:
                    counts = metadata_payload.get("task_status_counts")
                    has_completed = isinstance(counts, dict) and int(counts.get("completed") or 0) > 0
                    metadata_progress = has_completed
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

                director_status = str(director_result.status or "").strip().lower()
                if director_status not in {"completed", "success"}:
                    if progress_made:
                        idle_rounds = 0
                        stage_signals.append(
                            {
                                "code": "director.partial_failure_progress_continued",
                                "severity": "warning",
                                "detail": (
                                    "Director run returned a non-success status after material progress; "
                                    "continuing remaining dispatch rounds until TaskBoard convergence"
                                ),
                                "upstream_status": director_status,
                                "round": round_index,
                            }
                        )
                        if self._is_taskboard_converged(after_stats):
                            stage_signals.append(
                                {
                                    "code": "director.dispatch_converged_after_partial_failure",
                                    "severity": "info",
                                    "detail": f"Director dispatch converged after partial failure in round {round_index}",
                                    "round": round_index,
                                    "upstream_status": director_status,
                                }
                            )
                            break
                        continue
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
                    if director_status == "timeout":
                        stage_signals.append(
                            {
                                "code": "director.dispatch_timeout",
                                "severity": "error",
                                "detail": (
                                    "Director dispatch timed out after "
                                    f"{self._director_dispatch_timeout_seconds(context, task_count=len(pm_tasks))} "
                                    "seconds; "
                                    "no further progress possible"
                                ),
                                "upstream_status": director_status,
                                "round": round_index,
                                "timeout_seconds": self._director_dispatch_timeout_seconds(
                                    context,
                                    task_count=len(pm_tasks),
                                ),
                            }
                        )
                    else:
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
        final_metadata = final_result.metadata if (final_result and isinstance(final_result.metadata, dict)) else {}
        fanout_all_failed = self._fanout_all_active_bindings_failed(final_metadata)
        fanout_quality_handoff = self._fanout_quality_failure_can_enter_quality_gate(
            metadata=final_metadata,
            final_stats=final_stats,
            pm_tasks=pm_tasks,
        )
        if fanout_all_failed and not any(
            str(item.get("code") or "") == "director.binding_fanout_all_failed"
            for item in stage_signals
            if isinstance(item, dict)
        ):
            active_count = int(final_metadata.get("active_binding_count") or 0)
            if fanout_quality_handoff:
                stage_signals.append(
                    {
                        "code": "director.materialization_quality_handoff",
                        "severity": "warning",
                        "detail": (
                            "All active Director bindings ended with materialization quality failure after "
                            "writing workspace artifacts; continuing to quality_gate repair/QA harness"
                        ),
                        "active_binding_count": active_count,
                        "upstream_status": str((final_result.status if final_result else "") or "").strip(),
                    }
                )
            else:
                stage_signals.append(
                    {
                        "code": "director.binding_fanout_all_failed",
                        "severity": "error",
                        "detail": (
                            "All active Director bindings ended with non-success status; "
                            "quality gate cannot promote a failed Director materialization"
                        ),
                        "active_binding_count": active_count,
                        "upstream_status": str((final_result.status if final_result else "") or "").strip(),
                    }
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

        # Generate per-binding terminal route events from fanout results
        per_binding_route_events: list[dict[str, Any]] = []
        for attempt in attempts:
            metadata = attempt.get("metadata") if isinstance(attempt, dict) else {}
            if not isinstance(metadata, dict):
                continue
            per_binding_raw = metadata.get("per_binding")
            if isinstance(per_binding_raw, list):
                per_binding_items = [item for item in per_binding_raw if isinstance(item, dict)]
                per_binding_route_events.extend(
                    self._build_per_binding_route_events(cast(list[dict[str, Any]], per_binding_items))
                )

        if stage_status != "cancelled":
            binding_ok, binding_signals = self._validate_director_binding_coverage(
                additional_events=per_binding_route_events,
            )
            stage_signals.extend(binding_signals)
            if not binding_ok:
                stage_status = "failed"

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

        if per_binding_route_events:
            self._reclassify_binding_coverage_signals(
                stage_signals,
                per_binding_route_events,
            )

        for signal in stage_signals:
            if not isinstance(signal, dict):
                continue
            if str(signal.get("severity") or "").strip().lower() != "error":
                continue
            error_code = str(signal.get("code") or "").strip()
            root_cause_hint = str(signal.get("detail") or "").strip()
            if error_code:
                break

        fail_closed_events = self._build_fail_closed_director_route_events(
            attempts=attempts,
            stage_signals=stage_signals,
            per_binding_route_events=per_binding_route_events,
        )
        if fail_closed_events:
            stage_signals.append(
                {
                    "code": "director.fail_closed_route_evidence",
                    "severity": "info",
                    "detail": f"Recorded fail-closed diagnostics for {len(fail_closed_events)} missing director route(s)",
                    "count": len(fail_closed_events),
                }
            )

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
            "fail_closed_route_events": fail_closed_events,
            "per_binding_route_events": per_binding_route_events,
            "quality_gate_handoff": fanout_quality_handoff,
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
    def _fanout_all_active_bindings_failed(metadata: dict[str, Any]) -> bool:
        if not bool(metadata.get("binding_fanout")):
            return False
        per_binding = metadata.get("per_binding")
        if not isinstance(per_binding, list):
            return False

        active_entries = [
            item
            for item in per_binding
            if isinstance(item, dict)
            and not bool(item.get("quarantined"))
            and not bool(item.get("skipped"))
            and str(item.get("status") or "").strip().lower() not in {"quarantined", "skipped"}
        ]
        if not active_entries:
            return False

        success_statuses = {"completed", "success"}
        if any(str(item.get("status") or "").strip().lower() in success_statuses for item in active_entries):
            return False

        active_count = int(metadata.get("active_binding_count") or len(active_entries))
        return active_count > 0 and len(active_entries) >= active_count

    @staticmethod
    def _fanout_failure_mentions_materialization_quality(metadata: dict[str, Any]) -> bool:
        per_binding = metadata.get("per_binding")
        if not isinstance(per_binding, list):
            return False
        markers = (
            "director_materialization_quality_failed",
            "director_materialization_semantic_quality_failed",
        )
        for item in per_binding:
            if not isinstance(item, dict):
                continue
            if bool(item.get("skipped")) or bool(item.get("quarantined")):
                continue
            status = str(item.get("status") or "").strip().lower()
            if status in {"completed", "success", "skipped", "quarantined"}:
                continue
            text = json.dumps(item, ensure_ascii=False, default=str).lower()
            if any(marker in text for marker in markers):
                return True
        return False

    def _workspace_has_materialized_delivery_evidence(self, tasks: list[dict[str, Any]]) -> bool:
        workspace_root = self.workspace.resolve()
        declared_targets = self._collect_declared_delivery_targets(tasks)
        for target in declared_targets:
            path = (workspace_root / target).resolve()
            try:
                path.relative_to(workspace_root)
            except ValueError:
                continue
            if path.is_file():
                try:
                    if path.stat().st_size > 0:
                        return True
                except OSError:
                    continue
            if path.is_dir():
                try:
                    if any(child.is_file() and child.stat().st_size > 0 for child in path.rglob("*")):
                        return True
                except OSError:
                    continue

        for pattern in (
            "src/**/*.ts",
            "src/**/*.tsx",
            "src/**/*.js",
            "src/**/*.jsx",
            "src/**/*.py",
            "tests/**/*.*",
            "package.json",
            "index.html",
        ):
            for candidate in workspace_root.glob(pattern):
                if not candidate.is_file():
                    continue
                parts = set(candidate.relative_to(workspace_root).parts)
                if parts.intersection({".git", ".polaris", "node_modules"}):
                    continue
                try:
                    if candidate.stat().st_size > 0:
                        return True
                except OSError:
                    continue
        return False

    def _fanout_quality_failure_can_enter_quality_gate(
        self,
        *,
        metadata: dict[str, Any],
        final_stats: dict[str, int],
        pm_tasks: list[dict[str, Any]],
    ) -> bool:
        if not self._fanout_all_active_bindings_failed(metadata):
            return False
        if not self._is_taskboard_converged(final_stats):
            return False
        if not self._fanout_failure_mentions_materialization_quality(metadata):
            return False
        return self._workspace_has_materialized_delivery_evidence(pm_tasks)

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

    def _workspace_quality_repair_errors(self, results: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        for result in results:
            if bool(result.get("passed")):
                continue
            output_parts = [
                str(result.get(key) or "").strip()
                for key in ("error", "stdout_tail", "stderr_tail")
                if str(result.get(key) or "").strip()
            ]
            if not output_parts:
                continue
            command = result.get("command")
            command_text = " ".join(str(part) for part in command) if isinstance(command, list) else str(command or "")
            output = self._trim_command_output("\n".join(output_parts))
            errors.append(
                "Artifact quality scan failed: workspace validation command failed"
                f" ({command_text or 'unknown command'}): {output}"
            )

        try:
            from polaris.kernelone.quality.artifact_quality import scan_workspace_artifact_quality

            errors.extend(scan_workspace_artifact_quality(str(self.workspace)))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(f"Artifact quality scan failed: workspace quality repair scan failed: {exc}")

        deduped: list[str] = []
        seen: set[str] = set()
        for error in errors:
            normalized = str(error or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _apply_workspace_quality_repairs(
        self,
        *,
        run_id: str,
        artifact_quality_errors: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from polaris.cells.roles.adapters.public.service import (
            apply_deterministic_materialization_quality_repairs as _apply_deterministic_materialization_quality_repairs,
        )

        class _QualityRepairAdapter:
            def __init__(self, workspace: Path) -> None:
                self.workspace = str(workspace)
                self._execution = SimpleNamespace(_message_bus=None)

            def _update_task_progress(
                self,
                task_id: str,
                phase: str,
                current_file: str | None = None,
                event_code: str | None = None,
                event_status: str | None = None,
                event_reason: str | None = None,
                event_detail: str | None = None,
                event_refs: dict[str, Any] | None = None,
            ) -> None:
                del task_id, phase, current_file, event_code, event_status, event_reason, event_detail, event_refs

        target_files = self._collect_declared_delivery_targets(self._load_pm_plan_tasks("tasks/plan.json"))
        return _apply_deterministic_materialization_quality_repairs(
            _QualityRepairAdapter(self.workspace),
            task={"target_files": target_files, "metadata": {"target_files": target_files}},
            task_id=f"factory-quality-gate:{run_id}",
            artifact_quality_errors=artifact_quality_errors,
        )

    @staticmethod
    def _workspace_quality_repair_evidence(repair_results: list[dict[str, Any]]) -> list[str]:
        evidence: list[str] = []
        for item in repair_results:
            if not isinstance(item, dict) or not bool(item.get("success")):
                continue
            raw_result = item.get("result")
            result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
            source_tool = str(result.get("source_tool") or item.get("source_tool") or "").strip()
            file_name = str(result.get("file") or result.get("path") or "").strip()
            operation = str(result.get("operation") or "").strip()
            if source_tool or file_name:
                evidence.append(
                    "repair_write:"
                    f"tool={source_tool or str(item.get('tool') or item.get('tool_name') or 'unknown')};"
                    f"file={file_name or 'unknown'};"
                    f"operation={operation or 'unknown'}"
                )
            before_hash = str(result.get("before_sha256") or "").strip()
            after_hash = str(result.get("after_sha256") or "").strip()
            if before_hash or after_hash:
                evidence.append(
                    f"repair_hash:file={file_name or 'unknown'};before={before_hash[:16]};after={after_hash[:16]}"
                )
            diff_excerpt = str(result.get("diff_excerpt") or "").strip()
            if diff_excerpt:
                compact_diff = " ".join(diff_excerpt.split())
                evidence.append(f"repair_diff:file={file_name or 'unknown'};excerpt={compact_diff[:360]}")
            if len(evidence) >= 12:
                break
        return evidence

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

        repair_errors: list[str] = []
        repair_results: list[dict[str, Any]] = []
        repair_summary: dict[str, Any] = {
            "attempted": False,
            "success": False,
            "source_tools": [],
            "tool_results": 0,
            "rounds": [],
        }
        rerun_results: list[dict[str, Any]] = []
        if run_commands and not prepare_failed and not all(bool(item.get("passed")) for item in results):
            max_rounds = int(context.get("workspace_quality_repair_max_rounds") or _WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS)
            max_rounds = max(1, min(max_rounds, _WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS))
            latest_check_results = [item for item in results if str(item.get("phase") or "") == "check"]
            repair_rounds: list[dict[str, Any]] = []
            source_tools: list[str] = []
            evidence: list[str] = []
            write_tool_evidence = False
            for round_index in range(max_rounds):
                if latest_check_results and all(bool(item.get("passed")) for item in latest_check_results):
                    break
                repair_errors = self._workspace_quality_repair_errors(latest_check_results or results)
                if not repair_errors:
                    break
                round_repair_results, round_summary = await asyncio.to_thread(
                    self._apply_workspace_quality_repairs,
                    run_id=run.id,
                    artifact_quality_errors=repair_errors,
                )
                repair_results.extend(round_repair_results)
                normalized_round_summary = dict(round_summary)
                round_source_tools = [
                    str(item) for item in normalized_round_summary.get("source_tools", []) if str(item or "").strip()
                ]
                round_evidence = self._workspace_quality_repair_evidence(round_repair_results)
                round_write_tool_evidence = any(
                    bool(item.get("success")) and str(item.get("tool") or item.get("tool_name") or "") == "write_file"
                    for item in round_repair_results
                )
                source_tools.extend(round_source_tools)
                evidence.extend(round_evidence)
                write_tool_evidence = write_tool_evidence or round_write_tool_evidence
                repair_rounds.append(
                    {
                        "round": round_index + 1,
                        "attempted": True,
                        "artifact_quality_errors": repair_errors[:10],
                        "tool_results": len(round_repair_results),
                        "source_tools": round_source_tools,
                        "write_tool_evidence": round_write_tool_evidence,
                        "evidence": round_evidence,
                    }
                )
                if not round_repair_results:
                    break
                latest_check_results = []
                rerun_results = []
                round_prepare_failed = False
                prepare_phase = (
                    "prepare_after_repair" if round_index == 0 else f"prepare_after_repair_{round_index + 1}"
                )
                for command in prepare_commands:
                    result = await asyncio.to_thread(self._run_workspace_quality_command, command, timeout_seconds)
                    result["phase"] = prepare_phase
                    results.append(result)
                    if not bool(result.get("passed")):
                        round_prepare_failed = True
                phase = "check_after_repair" if round_index == 0 else f"check_after_repair_{round_index + 1}"
                if round_prepare_failed:
                    for command in run_commands:
                        result = {
                            "command": command,
                            "phase": phase,
                            "exit_code": None,
                            "passed": False,
                            "error": "skipped because workspace validation preparation failed after repair",
                            "stdout_tail": "",
                            "stderr_tail": "",
                        }
                        results.append(result)
                        latest_check_results.append(result)
                        rerun_results.append(result)
                    break
                else:
                    for command in run_commands:
                        result = await asyncio.to_thread(self._run_workspace_quality_command, command, timeout_seconds)
                        result["phase"] = phase
                        results.append(result)
                        latest_check_results.append(result)
                        rerun_results.append(result)
            residual_failures = [item for item in latest_check_results if not bool(item.get("passed"))]
            repair_revalidated = bool(rerun_results)
            repair_summary = {
                "attempted": bool(repair_rounds),
                "success": repair_revalidated and not residual_failures,
                "revalidated": repair_revalidated,
                "residual_error_count": len(residual_failures),
                "residual_errors": self._workspace_quality_repair_errors(residual_failures)[:10]
                if residual_failures
                else [],
                "source_tools": list(dict.fromkeys(source_tools)),
                "tool_results": len(repair_results),
                "write_tool_evidence": write_tool_evidence,
                "artifact_quality_errors": repair_errors[:10],
                "evidence": evidence[:12],
                "max_rounds": max_rounds,
                "rounds": repair_rounds,
            }

        effective_results = rerun_results if rerun_results else results
        if rerun_results:
            effective_results = [item for item in results if str(item.get("phase") or "") == "prepare"] + rerun_results

        payload = {
            "schema_version": "factory.workspace_quality_checks.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "factory_run_id": run.id,
            "workspace": str(self.workspace),
            "passed": all(bool(item.get("passed")) for item in effective_results),
            "commands": results,
            "repair": repair_summary,
        }
        artifact = "runtime/qa/workspace-validation.json"
        self._write_json_artifact(artifact, payload)
        return bool(payload["passed"]), artifact

    @staticmethod
    def _qa_report_has_warning(payload: dict[str, Any], warning: str) -> bool:
        return helpers.qa_report_has_warning(payload, warning)

    def _build_qa_input_with_workspace_quality_evidence(
        self,
        qa_input: object,
        workspace_checks_artifact: str,
        *,
        run_id: str = "",
    ) -> str:
        base_input = str(qa_input or "").strip()
        sections = [base_input] if base_input else []

        if workspace_checks_artifact:
            evidence_text = self._read_text_artifact(workspace_checks_artifact, min_chars=2)
            if evidence_text:
                compact_evidence = self._compact_workspace_quality_evidence_for_qa(evidence_text)
                sections.append(
                    "\n".join(
                        [
                            "Workspace quality evidence collected before QA judgement:",
                            f"- artifact: {workspace_checks_artifact}",
                            "- content:",
                            compact_evidence,
                        ]
                    )
                )

        ce_review_artifact = ""
        ce_review_text = ""
        if run_id:
            for candidate in (
                f"runtime/state/blueprints/{run_id}.review.json",
                f"runtime/blueprints/{run_id}.review.json",
                f"workspace/.polaris/blueprints/{run_id}.review.json",
                "workspace/.polaris/blueprints/latest.review.json",
            ):
                with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                    ce_review_text = self._read_text_artifact(candidate, min_chars=2)
                if ce_review_text:
                    ce_review_artifact = candidate
                    break
        if ce_review_text:
            sections.append(
                "\n".join(
                    [
                        "Chief Engineer blueprint evidence collected before QA judgement:",
                        f"- artifact: {ce_review_artifact}",
                        "- content:",
                        self._compact_text_for_prompt(ce_review_text, max_chars=6000),
                    ]
                )
            )
        return "\n\n".join(sections)

    async def _execute_quality_gate(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing quality gate for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)

        workspace_checks_passed, workspace_checks_artifact = await self._run_workspace_quality_checks(run, context)
        qa_input = self._build_qa_input_with_workspace_quality_evidence(
            context.get("qa_input"),
            workspace_checks_artifact,
            run_id=run.id,
        )

        service = self._build_orchestration_service(context)
        command_result = await service.execute_qa_run(
            workspace=str(self.workspace),
            target=context.get("qa_target", "Quality gate"),
            options={
                "input": qa_input,
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
