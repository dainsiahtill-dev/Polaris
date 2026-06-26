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
import shutil
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

# Language-to-extension mapping for PM plan language consistency validation.
# Used to detect when the PM model plans files in the wrong language
# (e.g. Java files for a JavaScript project — context bleed from other projects).
_LANGUAGE_SOURCE_EXTENSIONS: dict[str, frozenset[str]] = {
    "javascript": frozenset({".js", ".mjs", ".cjs", ".jsx"}),
    "typescript": frozenset({".ts", ".tsx", ".mts", ".cts"}),
    "python": frozenset({".py"}),
    "rust": frozenset({".rs"}),
    "go": frozenset({".go"}),
    "java": frozenset({".java"}),
    "cpp": frozenset({".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"}),
    "csharp": frozenset({".cs"}),
    "ruby": frozenset({".rb"}),
    "swift": frozenset({".swift"}),
    "kotlin": frozenset({".kt", ".kts"}),
    "scala": frozenset({".scala"}),
}
# Extensions that are language-agnostic and should not trigger a mismatch.
_LANGUAGE_NEUTRAL_EXTENSIONS: frozenset[str] = frozenset(
    {".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".html", ".css", ".xml", ".csv", ".lock"}
)
_LANGUAGE_NEUTRAL_FILENAMES: frozenset[str] = frozenset(
    {
        "go.mod",
        "go.sum",
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "cmakelists.txt",
    }
)

_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS = 3
_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_ENV = "KERNELONE_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS"
_DEFAULT_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS = 90.0
_WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES = frozenset(
    {
        ".css",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".json",
        ".md",
        ".py",
        ".rs",
        ".ts",
        ".tsx",
    }
)
_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_ENV = "KERNELONE_FACTORY_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT"
_DEFAULT_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT = 4
_DIRECTOR_DISPATCH_TIMEOUT_GRACE_SECONDS = 60
_PRE_DIRECTOR_SNAPSHOT_RELATIVE_DIR = ".polaris/factory_snapshots/pre_director"
_PRE_DIRECTOR_SNAPSHOT_KIND = "pre_director_workspace"
_PRE_DIRECTOR_PLATFORM_PREFIXES = (
    ".git/",
    ".polaris/",
    ".polaris.kernelone.tags.cache.v1/",
    "runtime/",
    "node_modules/",
)
_DIRECTOR_TIMEOUT_ENV_KEYS = (
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS",
)
_DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS = 240
_CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS = (
    "KERNELONE_FACTORY_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS",
    "KERNELONE_FACTORY_CE_LLM_TIMEOUT_SECONDS",
    "KERNELONE_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS",
)

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

    @staticmethod
    def _pre_director_snapshot_candidate(relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/").strip("/")
        if not normalized:
            return False
        if normalized in {".git", ".polaris", "runtime", "node_modules"}:
            return False
        return not any(normalized.startswith(prefix) for prefix in _PRE_DIRECTOR_PLATFORM_PREFIXES)

    def _pre_director_snapshot_dir(self) -> Path:
        return self.workspace / _PRE_DIRECTOR_SNAPSHOT_RELATIVE_DIR

    def _iter_pre_director_snapshot_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.workspace.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                relative = path.relative_to(self.workspace).as_posix()
            except ValueError:
                continue
            if self._pre_director_snapshot_candidate(relative):
                files.append(path)
        return sorted(files)

    def _create_pre_director_snapshot(self, *, run_id: str) -> dict[str, Any]:
        snapshot_dir = self._pre_director_snapshot_dir()
        files_dir = snapshot_dir / "files"
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        files_dir.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        for source in self._iter_pre_director_snapshot_files():
            relative = source.relative_to(self.workspace).as_posix()
            target = files_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entries.append({"path": relative, "size": source.stat().st_size})
        manifest = {
            "snapshot_kind": _PRE_DIRECTOR_SNAPSHOT_KIND,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "factory_run_id": str(run_id or "").strip(),
            "file_count": len(entries),
            "files": entries,
            "platform_excluded_prefixes": list(_PRE_DIRECTOR_PLATFORM_PREFIXES),
        }
        (snapshot_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest

    def _restore_pre_director_snapshot(self) -> dict[str, Any]:
        snapshot_dir = self._pre_director_snapshot_dir()
        manifest_path = snapshot_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("pre-Director workspace snapshot is missing or invalid") from exc
        if not isinstance(manifest, dict) or manifest.get("snapshot_kind") != _PRE_DIRECTOR_SNAPSHOT_KIND:
            raise RuntimeError("pre-Director workspace snapshot manifest has invalid kind")
        entries_raw = manifest.get("files")
        entries = [item for item in entries_raw if isinstance(item, dict)] if isinstance(entries_raw, list) else []
        expected_paths = {
            str(item.get("path") or "").replace("\\", "/").strip("/")
            for item in entries
            if str(item.get("path") or "").strip()
        }

        removed: list[str] = []
        restored: list[str] = []
        for current in self._iter_pre_director_snapshot_files():
            relative = current.relative_to(self.workspace).as_posix()
            if relative not in expected_paths:
                current.unlink(missing_ok=True)
                removed.append(relative)

        files_dir = snapshot_dir / "files"
        for relative in sorted(expected_paths):
            source = files_dir / relative
            if not source.is_file():
                raise RuntimeError(f"pre-Director snapshot content missing for {relative}")
            target = self.workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored.append(relative)

        for directory in sorted(
            [path for path in self.workspace.rglob("*") if path.is_dir()],
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                relative = directory.relative_to(self.workspace).as_posix().strip("/")
            except ValueError:
                continue
            if not relative or not self._pre_director_snapshot_candidate(f"{relative}/placeholder"):
                continue
            with contextlib.suppress(OSError):
                directory.rmdir()

        return {
            "snapshot_kind": _PRE_DIRECTOR_SNAPSHOT_KIND,
            "removed_files": removed,
            "restored_files": restored,
            "file_count": len(restored),
            "snapshot_created_at": manifest.get("created_at"),
        }

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

    def _validate_pm_plan_language_consistency(self, relative_path: str = "tasks/plan.json") -> str:
        """Check that PM plan target_files match the catalog primary_language.

        Detects context bleed where the PM model plans files in the wrong
        language (e.g. ``.java`` files for a ``javascript`` project).
        Returns an empty string when consistent, or a diagnostic message.
        """
        catalog_path = self.workspace / ".polaris" / "catalog_contract.json"
        if not catalog_path.exists():
            return ""
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return ""
        primary_language = str(catalog.get("primary_language") or "").strip().lower()
        if not primary_language:
            return ""
        expected_extensions = _LANGUAGE_SOURCE_EXTENSIONS.get(primary_language)
        if not expected_extensions:
            return ""
        tasks = self._load_pm_plan_tasks(relative_path)
        if not tasks:
            return ""
        wrong_lang_files: list[str] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            target_files = task.get("target_files")
            if not isinstance(target_files, list):
                continue
            for file_path in target_files:
                if not isinstance(file_path, str):
                    continue
                normalized = file_path.replace("\\", "/")
                filename = Path(normalized).name.lower()
                if filename in _LANGUAGE_NEUTRAL_FILENAMES:
                    continue
                ext = Path(normalized).suffix.lower()
                if ext in _LANGUAGE_NEUTRAL_EXTENSIONS or not ext:
                    continue
                # Bench injects tests/test_product.py as a validation script;
                # it is not project source code, so skip test directories.
                if normalized.startswith("tests/") or "/tests/" in normalized:
                    continue
                if ext not in expected_extensions:
                    wrong_lang_files.append(file_path)
        if not wrong_lang_files:
            return ""
        sample = wrong_lang_files[:5]
        return (
            f"pm_plan_language_mismatch: catalog primary_language={primary_language!r} "
            f"but {len(wrong_lang_files)} target_files use wrong extensions "
            f"(e.g. {sample}). "
            f"PM likely confused this project with a different language project."
        )

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

    def _iter_pm_plan_contract_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        latest_plan = self.workspace / ".polaris" / "plans" / "latest.plan.json"
        candidates.append(latest_plan)

        for pattern in (".polaris/plans/*.plan.json", ".polaris/roles/pm/*/plan.json"):
            candidates.extend(self.workspace.glob(pattern))

        deduped: dict[str, Path] = {}
        for candidate in candidates:
            deduped[candidate.resolve().as_posix()] = candidate

        def _mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        return sorted(deduped.values(), key=_mtime, reverse=True)

    def _ensure_pm_plan_contract_available(self) -> str:
        """Copy PM's workspace mirror into the runtime artifact path consumed downstream."""

        if self._load_pm_plan_tasks("tasks/plan.json"):
            return ""

        for candidate in self._iter_pm_plan_contract_candidates():
            if not candidate.exists() or not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            tasks = payload.get("tasks")
            if not isinstance(tasks, list) or not any(isinstance(item, dict) for item in tasks):
                continue
            self._write_json_artifact("tasks/plan.json", payload)
            try:
                return candidate.relative_to(self.workspace).as_posix()
            except ValueError:
                return candidate.as_posix()
        return ""

    def _materialize_pm_plan_taskboard(
        self,
        tasks: list[dict[str, Any]],
        *,
        run_id: str,
        source_stage: str,
    ) -> dict[str, Any]:
        if not tasks:
            return {"ensured_count": 0, "created_count": 0, "task_ids": []}

        service = TaskRuntimeService(str(self.workspace))
        task_ids: list[str] = []
        created_count = 0
        for index, task in enumerate(tasks, start=1):
            task_id = self._task_id(task, index)
            if not task_id:
                continue
            existing = service.get_task(task_id)
            metadata_raw = task.get("metadata")
            metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
            metadata.update(
                {
                    "external_task_id": task_id,
                    "pm_task_id": task_id,
                    "source_task_id": task_id,
                    "task_index": index,
                    "factory_run_id": str(run_id or "").strip(),
                    "factory_stage": str(source_stage or "").strip(),
                    "source_artifact": "tasks/plan.json",
                    "task_contract": dict(task),
                }
            )
            for key in ("scope", "target_files", "acceptance", "acceptance_criteria", "steps", "depends_on"):
                if key in task:
                    metadata.setdefault(key, task.get(key))
            description_parts = [
                self._task_string(task, "description"),
                "\n".join(self._task_string_list(task, "steps")),
                "\n".join(self._task_string_list(task, "acceptance", "acceptance_criteria")),
            ]
            description = "\n\n".join(part for part in description_parts if part.strip())
            service.ensure_task_row(
                external_task_id=task_id,
                subject=self._task_objective(task),
                description=description,
                metadata=metadata,
                priority=task.get("priority", index),
            )
            if existing is None:
                created_count += 1
            task_ids.append(task_id)

        return {
            "ensured_count": len(task_ids),
            "created_count": created_count,
            "task_ids": task_ids,
        }

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
    def _compact_blueprint_evidence_for_repair(text: str) -> str:
        try:
            payload = json.loads(str(text or ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            return helpers.compact_text_for_prompt(str(text or ""), max_chars=6000)
        if not isinstance(payload, dict):
            return helpers.compact_text_for_prompt(str(text or ""), max_chars=6000)

        blueprints: list[dict[str, Any]] = []
        for item in list(payload.get("blueprints") or [])[:12]:
            if not isinstance(item, dict):
                continue
            compact_item: dict[str, Any] = {}
            for key in ("task_id", "status", "blueprint_id", "blueprint_path", "summary", "recommendations", "risks"):
                value = item.get(key)
                if value not in (None, "", [], {}):
                    compact_item[key] = value
            if compact_item:
                blueprints.append(compact_item)

        compact_payload: dict[str, Any] = {
            "schema_version": "factory.chief_engineer_review.evidence.v1",
            "generated_blueprints": int(payload.get("generated_blueprints") or len(blueprints)),
            "total_tasks": int(payload.get("total_tasks") or len(blueprints)),
            "blueprints": blueprints,
        }
        signals = [
            {
                key: item.get(key)
                for key in ("code", "severity", "detail", "task_id")
                if isinstance(item, dict) and item.get(key) not in (None, "", [], {})
            }
            for item in list(payload.get("signals") or [])[:8]
            if isinstance(item, dict)
        ]
        if signals:
            compact_payload["signals"] = signals
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
        # Inject existing target file contents so the CE blueprint (and Director)
        # can see the actual API of files created by earlier tasks. Without this,
        # test-generation tasks guess at class/function names and produce broken tests.
        existing_file_context = self._read_existing_target_file_summaries(task)
        if existing_file_context:
            context["existing_target_files"] = existing_file_context
        return context

    _EXISTING_SUMMARY_SOURCE_SUFFIXES = (".py", ".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx")
    _EXISTING_SUMMARY_MAX_FILES = 24

    def _read_existing_target_file_summaries(
        self, task: dict[str, Any], *, max_chars_per_file: int = 1500
    ) -> list[dict[str, str]]:
        """Summarize the export API of files this task depends on but does NOT own.

        A later task (e.g. the one writing ``main.py``) imports symbols from files
        an earlier task already created (e.g. ``src/models/mood.py``). Those
        dependency files are NOT in this task's own ``target_files``, so the
        Director would otherwise have to guess their API — and guessing wrong is
        exactly how ``main.py`` ended up calling ``Mood(mood=..., intensity=...)``
        on an ``enum`` (live L1-03: cross-file coherence break, entrypoint smoke
        TypeError). We therefore scan the workspace for already-existing source
        files OUTSIDE this task's targets and inject their compact export
        signatures so the Director's imports stay coherent with reality.

        The task's own existing targets are also summarized (harmless re-edit
        context); both sets are returned, de-duplicated, capped, and path-sorted
        for deterministic context.
        """
        own_targets: set[str] = set()
        raw_targets = task.get("target_files")
        if isinstance(raw_targets, list):
            for item in raw_targets:
                if isinstance(item, str) and item.strip():
                    own_targets.add(item.strip().replace("\\", "/").lstrip("./"))

        # Collect candidate relative paths: existing own targets first, then any
        # other existing workspace source file (the dependency surface).
        candidates: list[str] = []
        seen: set[str] = set()

        def _add(rel: str) -> None:
            norm = rel.replace("\\", "/")
            if norm and norm not in seen:
                seen.add(norm)
                candidates.append(norm)

        for rel in sorted(own_targets):
            if (self.workspace / rel).is_file():
                _add(rel)

        workspace_root = self.workspace.resolve()
        if workspace_root.is_dir():
            for suffix in self._EXISTING_SUMMARY_SOURCE_SUFFIXES:
                for full_path in sorted(workspace_root.rglob(f"*{suffix}")):
                    if not full_path.is_file():
                        continue
                    parts = set(full_path.relative_to(workspace_root).parts)
                    if parts & {".polaris", "runtime", "node_modules", "__pycache__", ".git", "dist", "build"}:
                        continue
                    try:
                        rel = str(full_path.relative_to(workspace_root))
                    except ValueError:
                        continue
                    norm = rel.replace("\\", "/")
                    if norm in own_targets:
                        continue  # the task is (re)writing this; not a frozen dependency
                    _add(rel)
                    if len(candidates) >= self._EXISTING_SUMMARY_MAX_FILES:
                        break
                if len(candidates) >= self._EXISTING_SUMMARY_MAX_FILES:
                    break

        summaries: list[dict[str, str]] = []
        for rel_path in candidates[: self._EXISTING_SUMMARY_MAX_FILES]:
            full_path = self.workspace / rel_path
            if not full_path.is_file():
                continue
            try:
                content = full_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if not content.strip():
                continue
            suffix = full_path.suffix.lower()
            if suffix in (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"):
                summary = self._extract_js_export_summary(content)
            elif suffix == ".py":
                summary = self._extract_py_export_summary(content)
            else:
                summary = content[:max_chars_per_file]
            summaries.append({"path": rel_path, "exports": summary})
        return summaries

    @staticmethod
    def _extract_js_export_summary(content: str) -> str:
        """Extract JS/TS export signatures so dependent files reference real symbols.

        Captures classes, functions, const/let/var, TS enums (with members),
        interfaces, types, ``export { ... }`` lists, and CommonJS exports. Mirrors
        the Python extractor's enum-member coverage: a dependent TS file's Director
        must see enum members (e.g. ``SkyCondition.CALM``), not just the enum name,
        or it invents non-existent members — the cross-file coherence wall L4-L8
        React/Express projects hit.
        """
        import re as _re

        lines: list[str] = []

        # TS enums (incl. ``const enum``) with their members — the JS analog of the
        # Python enum-member gap. ``[^{}]`` spans newlines, so multi-line bodies match.
        for match in _re.finditer(r"(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)\s*\{([^{}]*)\}", content):
            name = match.group(1)
            members: list[str] = []
            seen_member: set[str] = set()
            for member in _re.findall(r"([A-Za-z_$][\w$]*)\s*(?==|,|\Z)", match.group(2)):
                if member not in seen_member:
                    seen_member.add(member)
                    members.append(member)
            lines.append(f"enum {name} {{ {', '.join(members[:40])} }}" if members else f"enum {name}")

        for raw_line in content.split("\n"):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            if (
                _re.match(r"module\.exports\s*=", stripped)
                or _re.match(r"exports\.[A-Za-z_$]", stripped)
                or _re.match(r"(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+[A-Za-z_$]", stripped)
                or _re.match(r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*[A-Za-z_$]", stripped)
                or _re.match(r"(?:export\s+)?(?:const|let|var)\s+(?!enum\b)[A-Za-z_$]", stripped)
                or _re.match(r"(?:export\s+)?interface\s+[A-Za-z_$]", stripped)
                or _re.match(r"(?:export\s+)?type\s+[A-Za-z_$][\w$]*\s*=", stripped)
                or _re.match(r"export\s+\{", stripped)
                or _re.match(r"export\s+default\s+", stripped)
            ):
                lines.append(stripped[:200])

        # Dedupe preserving order (an enum's declaration line can also appear above).
        deduped: list[str] = []
        seen_line: set[str] = set()
        for line in lines:
            if line not in seen_line:
                seen_line.add(line)
                deduped.append(line)
        if not deduped:
            for raw_line in content.split("\n"):
                if raw_line.strip():
                    deduped.append(raw_line.strip()[:200])
                if len(deduped) >= 30:
                    break
        return "\n".join(deduped[:60])

    @staticmethod
    def _extract_py_export_summary(content: str) -> str:
        """Extract Python export signatures so a dependent file's Director sees the
        *valid* cross-file symbols, not just declaration names.

        Includes enum members and class attributes alongside class/function
        signatures. Without enum members, the Director receives only
        ``class SkyCondition(Enum):`` and guesses non-existent members like
        ``SkyCondition.CLEAR`` — the factory-bench L1-03 entrypoint crash
        (``AttributeError: type object 'SkyCondition' has no attribute 'CLEAR'``).
        Falls back to a line scan when the source does not parse.
        """
        import ast as _ast

        try:
            tree = _ast.parse(content)
        except (SyntaxError, ValueError):
            return OrchestrationStageExecutor._extract_py_export_summary_fallback(content)

        enum_bases = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"}
        lines: list[str] = []

        def _base_names(class_node: _ast.ClassDef) -> list[str]:
            names: list[str] = []
            for base in class_node.bases:
                if isinstance(base, _ast.Name):
                    names.append(base.id)
                elif isinstance(base, _ast.Attribute):
                    names.append(base.attr)
            return names

        def _func_signature(fn: _ast.FunctionDef | _ast.AsyncFunctionDef) -> str:
            params: list[str] = [a.arg for a in fn.args.posonlyargs] + [a.arg for a in fn.args.args]
            if fn.args.vararg is not None:
                params.append("*" + fn.args.vararg.arg)
            params.extend(a.arg for a in fn.args.kwonlyargs)
            if fn.args.kwarg is not None:
                params.append("**" + fn.args.kwarg.arg)
            keyword = "async def" if isinstance(fn, _ast.AsyncFunctionDef) else "def"
            return f"{keyword} {fn.name}({', '.join(params)})"

        for node in tree.body:
            if isinstance(node, _ast.ClassDef):
                bases = _base_names(node)
                header = f"class {node.name}({', '.join(bases)}):" if bases else f"class {node.name}:"
                is_enum = any(base in enum_bases for base in bases)
                members: list[str] = []
                methods: list[str] = []
                for item in node.body:
                    if isinstance(item, _ast.Assign):
                        members.extend(tgt.id for tgt in item.targets if isinstance(tgt, _ast.Name))
                    elif isinstance(item, _ast.AnnAssign) and isinstance(item.target, _ast.Name):
                        members.append(item.target.id)
                    elif isinstance(item, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                        methods.append(item.name)
                if is_enum and members:
                    lines.append(f"{header} members: {', '.join(members[:40])}")
                else:
                    detail: list[str] = []
                    if members:
                        detail.append("attrs: " + ", ".join(members[:24]))
                    if methods:
                        detail.append("methods: " + ", ".join(methods[:24]))
                    lines.append(f"{header} {' | '.join(detail)}" if detail else header)
            elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                lines.append(_func_signature(node))
            elif isinstance(node, _ast.Assign):
                lines.extend(
                    f"{tgt.id} = ..." for tgt in node.targets if isinstance(tgt, _ast.Name) and tgt.id.isupper()
                )

        if not lines:
            return OrchestrationStageExecutor._extract_py_export_summary_fallback(content)
        return "\n".join(lines[:60])

    @staticmethod
    def _extract_py_export_summary_fallback(content: str) -> str:
        """Line-scan fallback when the dependency source does not parse as Python."""
        import re as _re

        lines: list[str] = []
        for raw_line in content.split("\n"):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _re.match(r"(?:class|def|async def)\s+\w+", stripped):
                lines.append(stripped[:200])
        if not lines:
            for raw_line in content.split("\n"):
                if raw_line.strip():
                    lines.append(raw_line.strip()[:200])
                if len(lines) >= 30:
                    break
        return "\n".join(lines[:50])

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

    def _read_claimable_director_task_ids(self, *, limit: int) -> list[str]:
        """Return TaskBoard PM/external ids that can be claimed in this round."""
        if limit <= 0:
            return []
        try:
            rows = TaskRuntimeService(str(self.workspace)).list_task_rows(include_terminal=False)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return []

        ids: list[str] = []
        seen: set[str] = set()
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status not in {"pending", "ready"}:
                continue
            blocked_by = row.get("blocked_by") if isinstance(row.get("blocked_by"), list) else row.get("blockedBy")
            if blocked_by:
                continue
            metadata_raw = row.get("metadata")
            metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
            task_id = str(
                metadata.get("external_task_id")
                or metadata.get("pm_task_id")
                or metadata.get("source_task_id")
                or metadata.get("task_id")
                or row.get("id")
                or ""
            ).strip()
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            ids.append(task_id)
            if len(ids) >= limit:
                break
        return ids

    @staticmethod
    def _terminal_status_from_task_counts(counts: Any) -> str:
        if not isinstance(counts, dict):
            return ""

        def _count(key: str) -> int:
            try:
                return int(counts.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        unresolved = _count("pending") + _count("ready")
        running = (
            _count("in_progress")
            + _count("in_design")
            + _count("in_execution")
            + _count("in_qa")
            + _count("running")
            + _count("processing")
            + _count("executing")
            + _count("waiting_human")
        )
        if running > 0:
            return ""
        failed = _count("failed") + _count("blocked") + _count("cancelled") + _count("timeout")
        if failed > 0:
            return "failed"
        if unresolved > 0:
            return ""
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
    def _pm_deterministic_contract_metadata_for_context(
        run: FactoryRun,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Build PM run metadata for explicit/internal deterministic contract mode."""
        metadata_sources: list[dict[str, Any]] = []
        if isinstance(context, dict):
            context_metadata = context.get("metadata")
            if isinstance(context_metadata, dict):
                metadata_sources.append(context_metadata)
            metadata_sources.append(context)
        run_metadata = run.metadata if isinstance(run.metadata, dict) else {}
        start_request = run_metadata.get("factory_start_request")
        if isinstance(start_request, dict):
            start_metadata = start_request.get("metadata")
            if isinstance(start_metadata, dict):
                metadata_sources.append(start_metadata)

        explicit_deterministic = any(
            str(source.get("deterministic_pm_contracts") or "").strip().lower() in {"1", "true", "yes", "on"}
            for source in metadata_sources
        )
        bench_metadata = next(
            (source for source in metadata_sources if str(source.get("factory_bench_project_id") or "").strip()),
            {},
        )
        if not explicit_deterministic and not bench_metadata:
            return {}

        result: dict[str, Any] = {"deterministic_pm_contracts": True}
        if bench_metadata:
            result.update(
                {
                    "factory_bench_project_id": str(bench_metadata.get("factory_bench_project_id") or "").strip(),
                    "factory_bench_level": bench_metadata.get("factory_bench_level"),
                    "factory_bench_deterministic_pm": True,
                    "pm_route_audit_probe": True,
                    "factory_recovery": "bench_preemptive_deterministic_contracts",
                }
            )
        else:
            result["factory_recovery"] = "explicit_deterministic_contracts"
        return result

    @staticmethod
    def _director_dispatch_timeout_seconds(context: dict[str, Any], *, task_count: int) -> int:
        del task_count
        raw_override = context.get("director_dispatch_timeout_seconds")
        if raw_override is not None:
            try:
                return max(1, int(raw_override))
            except (TypeError, ValueError):
                pass

        def _parse_timeout(raw: Any) -> int | None:
            if raw is None:
                return None
            try:
                value = int(float(str(raw).strip()))
            except (TypeError, ValueError):
                return None
            if value <= 0:
                return None
            return value

        stage_timeout = _parse_timeout(context.get("timeout"))
        llm_timeout_candidates: list[int] = []
        for key in ("director_llm_timeout_seconds", "llm_call_timeout_seconds"):
            value = _parse_timeout(context.get(key))
            if value is not None:
                llm_timeout_candidates.append(value)
        for env_key in _DIRECTOR_TIMEOUT_ENV_KEYS:
            value = _parse_timeout(os.getenv(env_key))
            if value is not None:
                llm_timeout_candidates.append(value)
        if llm_timeout_candidates:
            return max(llm_timeout_candidates) + _DIRECTOR_DISPATCH_TIMEOUT_GRACE_SECONDS

        return stage_timeout or 600

    @staticmethod
    def _chief_engineer_llm_timeout_seconds(context: dict[str, Any]) -> int:
        def _parse_timeout(raw: Any) -> int | None:
            if raw is None:
                return None
            try:
                value = int(float(str(raw).strip()))
            except (TypeError, ValueError):
                return None
            if value <= 0:
                return None
            return value

        for key in (
            "chief_engineer_llm_timeout_seconds",
            "ce_llm_timeout_seconds",
            "llm_call_timeout_seconds",
            "request_timeout_seconds",
            "timeout_seconds",
        ):
            value = _parse_timeout(context.get(key))
            if value is not None:
                return value

        for env_key in _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS:
            value = _parse_timeout(os.getenv(env_key))
            if value is not None:
                return value

        return _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS

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
            from polaris.cells.runtime.projection.public import build_llm_status
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
            readiness_source = str(item.get("readiness_source") or item.get("source") or "").strip()
            if readiness_source == "runtime_dispatch":
                continue
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
        external_readiness_skipped_count = len(readiness_skipped)

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

        requested_tasks = [str(item or "").strip() for item in list(tasks or []) if str(item or "").strip()]
        partition_tasks = bool(requested_tasks) and len(active_bindings) > 1
        assigned_tasks_by_key: dict[str, list[str] | None] = {}
        submission_bindings: list[dict[str, str]] = []
        if partition_tasks:
            for idx, binding in enumerate(active_bindings):
                assigned_tasks = requested_tasks[idx :: len(active_bindings)]
                if not assigned_tasks:
                    readiness_skipped.append({**binding, "reason": "no_assigned_tasks"})
                    continue
                assigned_tasks_by_key[_binding_key(binding)] = assigned_tasks
                submission_bindings.append(binding)
        else:
            for binding in active_bindings:
                assigned_tasks_by_key[_binding_key(binding)] = tasks
                submission_bindings.append(binding)
        active_bindings = submission_bindings

        async def _run_binding(binding: dict[str, str]) -> CommandResult:
            binding_tasks = assigned_tasks_by_key.get(_binding_key(binding))
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
                "fanout_assigned_tasks": list(binding_tasks or []),
                "fanout_assigned_task_count": len(binding_tasks or []),
            }
            return await service.execute_director_run(workspace=workspace, tasks=binding_tasks, options=binding_opts)

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

            def _workspace_taskboard_terminal_result(*, queried_status: str = "") -> CommandResult | None:
                """Fail-closed when the shared taskboard is already terminal.

                A slow Director binding can keep its own run row as ``running``
                after another binding has already completed or failed the
                claimed task set.  When the taskboard contains no active tasks
                and at least one failure, continuing to wait only hides the
                real failure behind a long fanout timeout.
                """
                with contextlib.suppress(RuntimeError, OSError, TypeError, ValueError):
                    taskboard_stats = self._read_taskboard_stats()
                    count_status = self._terminal_status_from_task_counts(taskboard_stats)
                    if count_status and count_status != "completed":
                        return CommandResult(
                            run_id=run_id,
                            status=count_status,
                            message=(
                                "Director binding stopped because workspace taskboard reached "
                                f"terminal counts before binding run status converged: {taskboard_stats}"
                            ),
                            metadata={
                                "terminal_source": "workspace_taskboard_counts",
                                "queried_status": queried_status,
                                "task_status_counts": dict(taskboard_stats),
                            },
                        )
                return None

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
                        taskboard_terminal = _workspace_taskboard_terminal_result(queried_status="unavailable")
                        if taskboard_terminal is not None:
                            wait_task.cancel()
                            return binding, taskboard_terminal
                        continue

                    probed_status = str(status_probe.status or "").strip().lower()
                    if probed_status in terminal_statuses:
                        wait_task.cancel()
                        return binding, status_probe

                    metadata = status_probe.metadata if isinstance(status_probe.metadata, dict) else {}
                    count_status = self._terminal_status_from_task_counts(metadata.get("task_status_counts"))
                    if count_status:
                        wait_task.cancel()
                        return binding, CommandResult(
                            run_id=run_id,
                            status=count_status,
                            message=(
                                "Director binding reached terminal task counts "
                                f"before run status converged: {metadata.get('task_status_counts')}"
                            ),
                            metadata={
                                **metadata,
                                "terminal_source": "task_status_counts",
                                "queried_status": probed_status,
                            },
                        )

                    taskboard_terminal = _workspace_taskboard_terminal_result(queried_status=probed_status)
                    if taskboard_terminal is not None:
                        wait_task.cancel()
                        return binding, taskboard_terminal
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
            entry_assigned_tasks = assigned_tasks_by_key.get(key)
            if entry_assigned_tasks is not None:
                entry["assigned_tasks"] = list(entry_assigned_tasks)
                entry["assigned_task_count"] = len(entry_assigned_tasks)
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
                    "assigned_tasks": [],
                    "assigned_task_count": 0,
                }
            )

        quarantined_count = sum(1 for entry in per_binding if entry.get("quarantined"))
        skipped_count = len(quarantined_skipped)
        readiness_skipped_count = sum(
            1 for entry in per_binding if entry.get("skipped") and not entry.get("quarantined")
        )
        merged_status = "completed" if success_count > 0 and fail_count == 0 else "failed"
        total_binding_count = len(bindings) + external_readiness_skipped_count
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
                "task_assignment_mode": "partitioned" if partition_tasks else "shared",
                "requested_task_ids": requested_tasks,
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
        pm_run_metadata = self._pm_deterministic_contract_metadata_for_context(run, context)
        pm_run_options: dict[str, Any] = {
            "directive": planning_directive,
            "run_director": False,
        }
        if pm_run_metadata:
            pm_run_options["metadata"] = pm_run_metadata
        command_result = await service.execute_pm_run(
            workspace=str(self.workspace),
            run_type="pm",
            options=pm_run_options,
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
            )
            stage_signals.append(
                {
                    "code": "pm.taskboard_materialized_from_plan",
                    "severity": "info",
                    "detail": "Materialized PM plan tasks into canonical TaskBoard for Director claim enforcement.",
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

    @staticmethod
    def _ce_missing_final_request_evidence(evidence: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        if not isinstance(evidence.get("final_request_context_audit"), dict):
            missing.append("final_request_context_audit")
        if not str(evidence.get("context_snapshot_ref") or "").strip():
            missing.append("context_snapshot_ref")
        return missing

    @staticmethod
    def _architecture_decision_payloads(values: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        source_values = values if isinstance(values, (list, tuple)) else []
        for item in source_values:
            if isinstance(item, dict):
                rows.append(dict(item))
                continue
            to_dict = getattr(item, "to_dict", None)
            if not callable(to_dict):
                continue
            try:
                payload = to_dict()
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

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

    async def _execute_chief_engineer_review(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing Chief Engineer review for run %s", run.id)

        synced_plan_source = self._ensure_pm_plan_contract_available()
        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        stage_signals: list[dict[str, Any]] = []
        blueprint_rows: list[dict[str, Any]] = []
        if synced_plan_source:
            stage_signals.append(
                {
                    "code": "chief_engineer.plan_contract_synced_from_workspace_mirror",
                    "severity": "info",
                    "detail": "Copied PM workspace plan mirror into runtime tasks/plan.json before blueprint review.",
                    "source_path": synced_plan_source,
                }
            )

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
        ce_timeout_seconds = self._chief_engineer_llm_timeout_seconds(context)

        for index, task in enumerate(pm_tasks, start=1):
            task_id = self._task_id(task, index)
            objective = self._task_objective(task)
            task_constraints = self._task_blueprint_constraints(task)
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
                        "chief_engineer_llm_timeout_seconds": ce_timeout_seconds,
                        "llm_call_timeout_seconds": ce_timeout_seconds,
                        "request_timeout_seconds": ce_timeout_seconds,
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
                    timeout_seconds=ce_timeout_seconds,
                    metadata={
                        "constraints": task_constraints,
                        "source": "factory_stage_executor.chief_engineer_review",
                        "cognitive_runtime_mode": "off",
                        "cognitive_runtime_enabled": False,
                        "cognitive_runtime_required": False,
                        "llm_call_timeout_seconds": ce_timeout_seconds,
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
                    audit_payload: dict[str, Any] = {
                        "provider": ce_provider,
                        "model": ce_model,
                        "cache_hit": bool(ce_evidence.get("cache_hit")),
                        "task_id": task_id,
                        "run_id": run.id,
                    }
                    self._attach_ce_llm_evidence(audit_payload, ce_evidence)
                    self._emit_audit_event(
                        "chief_engineer.llm_call",
                        **audit_payload,
                    )
                    missing_final_request_evidence = self._ce_missing_final_request_evidence(ce_evidence)
                    if missing_final_request_evidence:
                        missing_signal = {
                            "code": "chief_engineer.final_request_audit_missing",
                            "severity": "error",
                            "detail": (
                                "CE LLM result did not expose required final provider-request evidence: "
                                + ", ".join(missing_final_request_evidence)
                            ),
                            "task_id": task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                            "missing": missing_final_request_evidence,
                        }
                        self._attach_ce_llm_evidence(missing_signal, ce_evidence)
                        stage_signals.append(missing_signal)

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
                        constraints=task_constraints,
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
                            "CE returned a valid blueprint result but the physical blueprint artifact was missing; "
                            "rewrote the handoff artifact from structured result fields."
                        ),
                        "task_id": task_id,
                        "blueprint_id": result.blueprint_id,
                        "blueprint_path": result.blueprint_path,
                    }
                )

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

        synced_plan_source = self._ensure_pm_plan_contract_available()
        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        plan_task_filter = self._build_director_task_filter(pm_tasks)
        configured_task_filter = str(context.get("task_filter") or "").strip()
        effective_task_filter = configured_task_filter or plan_task_filter
        requested_task_ids = self._director_requested_task_ids(context, pm_tasks)

        service = self._build_orchestration_service(context)
        stage_signals: list[dict[str, Any]] = []
        if synced_plan_source:
            stage_signals.append(
                {
                    "code": "director.plan_contract_synced_from_workspace_mirror",
                    "severity": "info",
                    "detail": "Copied PM workspace plan mirror into runtime tasks/plan.json before Director dispatch.",
                    "source_path": synced_plan_source,
                }
            )
        if pm_tasks:
            materialize_summary = self._materialize_pm_plan_taskboard(
                pm_tasks,
                run_id=run.id,
                source_stage="director_dispatch",
            )
            if int(materialize_summary.get("created_count") or 0) > 0:
                stage_signals.append(
                    {
                        "code": "director.taskboard_materialized_from_plan",
                        "severity": "info",
                        "detail": "Materialized missing PM plan tasks into TaskBoard before Director dispatch.",
                        **materialize_summary,
                    }
                )
        snapshot_signals: list[dict[str, Any]] = []
        raw_start_metadata = context.get("metadata")
        start_metadata: dict[str, Any] = dict(raw_start_metadata) if isinstance(raw_start_metadata, dict) else {}
        start_from_hint = str(context.get("factory_start_from") or start_metadata.get("factory_start_from") or "")
        director_only_resume = start_from_hint.strip().lower() == "director" or str(run.config.name or "") == (
            "Factory Run - director"
        )
        if director_only_resume:
            try:
                restore_payload = self._restore_pre_director_snapshot()
                snapshot_signals.append(
                    {
                        "code": "director.pre_director_snapshot_restored",
                        "severity": "info",
                        "detail": "Restored workspace delivery files from pre-Director snapshot before resume",
                        **restore_payload,
                    }
                )
            except RuntimeError as exc:
                stage_signals.append(
                    {
                        "code": "director.pre_director_snapshot_restore_failed",
                        "severity": "error",
                        "detail": str(exc),
                    }
                )
        else:
            try:
                snapshot_payload = self._create_pre_director_snapshot(run_id=run.id)
                snapshot_signals.append(
                    {
                        "code": "director.pre_director_snapshot_created",
                        "severity": "info",
                        "detail": "Captured workspace delivery-file snapshot before Director dispatch",
                        "file_count": snapshot_payload.get("file_count"),
                        "snapshot_path": _PRE_DIRECTOR_SNAPSHOT_RELATIVE_DIR,
                    }
                )
            except (OSError, RuntimeError, ValueError) as exc:
                stage_signals.append(
                    {
                        "code": "director.pre_director_snapshot_create_failed",
                        "severity": "error",
                        "detail": str(exc),
                    }
                )
        initial_stats = self._read_taskboard_stats()
        attempts: list[dict[str, Any]] = []
        last_command_result: CommandResult | None = None
        final_result: CommandResult | None = None
        max_rounds = int(context.get("director_max_rounds") or 0)
        if max_rounds <= 0:
            active_rounds = (
                int(initial_stats.get("pending") or 0)
                + int(initial_stats.get("ready") or 0)
                + int(initial_stats.get("in_progress") or 0)
                + 2
            )
            total_rounds = int(initial_stats.get("total") or 0) + 2
            dynamic_rounds = max(active_rounds, total_rounds)
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

        if not any(str(item.get("severity") or "").strip().lower() == "error" for item in stage_signals):
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
                round_requested_task_ids = self._read_claimable_director_task_ids(limit=max_workers)
                if not round_requested_task_ids:
                    round_requested_task_ids = list(requested_task_ids or [])
                base_options["metadata"]["director_claimable_task_ids"] = list(round_requested_task_ids)
                if director_binding_fanout:
                    command_result = await self._execute_director_binding_fanout(
                        service=service,
                        workspace=str(self.workspace),
                        tasks=round_requested_task_ids,
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
                        tasks=round_requested_task_ids,
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
                        if self._fanout_quality_failure_can_enter_quality_gate(
                            metadata=metadata_payload,
                            final_stats=after_stats,
                            pm_tasks=pm_tasks,
                        ):
                            stage_signals.append(
                                {
                                    "code": "director.materialization_quality_handoff_ready",
                                    "severity": "warning",
                                    "detail": (
                                        "Director wrote materialized workspace artifacts but failed materialization "
                                        "quality; stopping dispatch before a no-claim retry so quality_gate can "
                                        "run on the physical workspace state"
                                    ),
                                    "upstream_status": director_status,
                                    "round": round_index,
                                }
                            )
                            break
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
        elif fanout_quality_handoff and not any(
            str(item.get("code") or "") == "director.materialization_quality_handoff"
            for item in stage_signals
            if isinstance(item, dict)
        ):
            stage_signals.append(
                {
                    "code": "director.materialization_quality_handoff",
                    "severity": "warning",
                    "detail": (
                        "Director materialization quality failed after writing workspace artifacts; "
                        "continuing to quality_gate repair/QA harness"
                    ),
                    "upstream_status": str((final_result.status if final_result else "") or "").strip(),
                }
            )

        if fanout_quality_handoff:
            self._downgrade_quality_handoff_blocking_signals(stage_signals)
        stage_signals.extend(snapshot_signals)

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
            if fanout_quality_handoff:
                stage_signals.append(
                    {
                        "code": "director.taskboard_unresolved_quality_handoff",
                        "severity": "warning",
                        "detail": (
                            "TaskBoard retained unresolved work after Director materialization failure, "
                            f"but workspace artifacts exist and will enter quality gate; final_stats={final_stats}"
                        ),
                    }
                )
            else:
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

    def _failed_task_records_indicate_quality_handoff(self) -> bool:
        """Return true when failed task records show artifacts that should enter QA.

        Director weak-model fanout can write files but fail the strict write-tool
        receipt contract. That is still a real, inspectable workspace state, so
        Factory should let workspace quality and QA decide whether it is runnable
        instead of stopping before gates run.
        """
        tasks_dir = self._artifact_path("tasks/plan.json").parent
        if not tasks_dir.exists():
            return False
        markers = (
            "director_missing_write_receipt",
            "director_no_materialized_changes",
            "single_batch_contract_violation",
            "director_materialization_quality_failed",
            "director_materialization_semantic_quality_failed",
        )
        modes = {
            "workspace_diff_without_write_tool",
            "no_materialized_changes",
        }
        for task_path in sorted(tasks_dir.glob("task_*.json")):
            try:
                payload = json.loads(task_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            status = str(payload.get("status") or "").strip().lower()
            if status not in {"failed", "blocked"}:
                continue
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            adapter_result = metadata.get("adapter_result")
            if not isinstance(adapter_result, dict):
                adapter_result = {}
            runtime_execution = metadata.get("runtime_execution")
            if not isinstance(runtime_execution, dict):
                runtime_execution = {}
            texts = [
                payload.get("last_execution_error"),
                metadata.get("last_execution_error"),
                metadata.get("error"),
                metadata.get("error_code"),
                runtime_execution.get("last_error"),
                runtime_execution.get("error"),
                adapter_result.get("materialization_error"),
                adapter_result.get("last_execution_error"),
                adapter_result.get("error"),
                adapter_result.get("error_code"),
                adapter_result.get("direct_fallback", {}).get("skipped_reason")
                if isinstance(adapter_result.get("direct_fallback"), dict)
                else "",
            ]
            haystack = "\n".join(str(item or "") for item in texts).lower()
            mode = str(adapter_result.get("materialization_mode") or "").strip().lower()
            if mode in modes or any(marker in haystack for marker in markers):
                return True
        return False

    @staticmethod
    def _taskboard_idle_with_unresolved_work(stats: dict[str, int]) -> bool:
        """Return true when no work is active and only blocked residue remains."""
        active_keys = (
            "in_progress",
            "in_design",
            "in_execution",
            "in_qa",
            "running",
            "processing",
            "executing",
            "waiting_human",
        )
        if any(int(stats.get(key) or 0) > 0 for key in active_keys):
            return False
        claimable = int(stats.get("pending") or 0) + int(stats.get("ready") or 0)
        blocked = int(stats.get("blocked") or 0)
        terminal = int(stats.get("completed") or 0) + int(stats.get("failed") or 0)
        return claimable == 0 and blocked > 0 and terminal > 0

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
        idle_with_unresolved = self._taskboard_idle_with_unresolved_work(final_stats)
        if idle_with_unresolved and self._missing_declared_delivery_targets(pm_tasks):
            return False
        taskboard_terminal_enough = self._is_taskboard_converged(final_stats) or idle_with_unresolved
        if not taskboard_terminal_enough:
            return False
        if not self._workspace_has_materialized_delivery_evidence(pm_tasks):
            return False
        if not self._fanout_all_active_bindings_failed(metadata):
            return self._failed_task_records_indicate_quality_handoff()
        return (
            self._fanout_failure_mentions_materialization_quality(metadata)
            or self._failed_task_records_indicate_quality_handoff()
        )

    @staticmethod
    def _downgrade_quality_handoff_blocking_signals(stage_signals: list[dict[str, Any]]) -> None:
        handoff_codes = {
            "director.dispatch_timeout",
            "director.run_status_non_success",
            "director.taskboard_not_converged",
            "director.binding_fanout_all_failed",
        }
        for signal in stage_signals:
            if not isinstance(signal, dict):
                continue
            code = str(signal.get("code") or "").strip()
            severity = str(signal.get("severity") or "").strip().lower()
            if code in handoff_codes and severity == "error":
                signal["severity"] = "warning"
                signal["handoff_suppressed_error"] = True
                signal["handoff_reason"] = "materialized_artifacts_enter_quality_gate"

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
            run_director_materialization_quality_repair_schedule,
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

        target_files = self._workspace_quality_repair_target_files()
        return run_director_materialization_quality_repair_schedule(
            _QualityRepairAdapter(self.workspace),
            task={"target_files": target_files, "metadata": {"target_files": target_files}},
            task_id=f"factory-quality-gate:{run_id}",
            artifact_quality_errors=artifact_quality_errors,
        )

    def _apply_workspace_quality_cpp_post_repairs(self) -> list[dict[str, Any]]:
        has_cpp_project = any(self.workspace.rglob("*.cpp")) or (self.workspace / "CMakeLists.txt").is_file()
        if not has_cpp_project:
            return []
        try:
            from polaris.cells.roles.adapters.public.service import (
                run_director_cpp_post_execution_repairs,
            )

            return run_director_cpp_post_execution_repairs(self.workspace)
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
        return max(30.0, min(value, 3600.0))

    async def _apply_workspace_quality_llm_repairs(
        self,
        *,
        run_id: str,
        context: dict[str, Any],
        artifact_quality_errors: list[str],
        repair_attempt: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        changed_files = self._workspace_quality_repair_changed_files()
        if not changed_files:
            return [], {
                "attempted": False,
                "repair_mode": "director_llm",
                "reason": "no_workspace_source_files_for_repair",
                "source_tools": [],
                "tool_results": 0,
            }
        target_files = self._workspace_quality_repair_target_files()
        task: dict[str, Any] = {"target_files": target_files or changed_files}
        repair_context = {
            "delivery_mode": "materialize_changes",
            "target_files": (target_files or changed_files)[:80],
            "changed_files": changed_files[:80],
            "factory_workspace_quality_repair": {
                "changed_files": changed_files[:80],
                "target_files": target_files[:80],
            },
        }
        for key in (
            "language",
            "prompt_language",
            "programming_language",
            "artifact",
            "artifact_type",
            "project_kind",
            "prompt_profile_ids",
            "prompt_profiles",
            "prompt_profile",
            "prompt_profile_id",
        ):
            if key in context:
                repair_context[key] = context[key]
        try:
            from polaris.cells.roles.adapters.public.service import run_director_materialization_quality_repair

            results, summary = await run_director_materialization_quality_repair(
                str(self.workspace),
                task=task,
                target_task_id=f"factory-quality-gate:{run_id}:llm-repair",
                run_id=run_id,
                context=repair_context,
                original_message=self._workspace_quality_repair_original_message(
                    run_id=run_id,
                    target_files=target_files,
                ),
                llm_call_timeout=self._workspace_quality_llm_repair_timeout_seconds(context),
                artifact_quality_errors=artifact_quality_errors,
                changed_files=changed_files,
                repair_attempt=repair_attempt,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed around external LLM repair boundary.
            return [], {
                "attempted": True,
                "repair_mode": "director_llm",
                "success": False,
                "error": str(exc),
                "source_tools": ["director_materialization_quality_repair_error"],
                "tool_results": 0,
            }
        normalized_summary = dict(summary)
        normalized_summary["repair_mode"] = "director_llm"
        raw_source_tools = normalized_summary.get("source_tools")
        source_tool_items = raw_source_tools if isinstance(raw_source_tools, list | tuple | set) else []
        source_tools = [str(item) for item in source_tool_items if str(item or "").strip()]
        if results and "director_materialization_quality_repair" not in source_tools:
            source_tools.append("director_materialization_quality_repair")
        normalized_summary["source_tools"] = source_tools
        normalized_summary.setdefault("tool_results", len(results))
        normalized_summary.setdefault("attempted", True)
        return [dict(item) for item in results], normalized_summary

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
                # If npm install failed due to hallucinated dependencies, repair and retry.
                is_npm_install = (
                    isinstance(command, list)
                    and command
                    and str(command[0]).strip().lower() == "npm"
                    and any(str(part).strip().lower() == "install" for part in command)
                )
                if is_npm_install:
                    stderr_text = str(result.get("stderr_tail") or "")
                    removed = self._workspace_quality.repair_hallucinated_npm_dependencies(stderr_text)
                    if removed:
                        result["repair"] = {"action": "remove_hallucinated_deps", "removed": removed}
                        retry_result = await asyncio.to_thread(
                            self._run_workspace_quality_command, command, timeout_seconds
                        )
                        retry_result["phase"] = "prepare"
                        retry_result["repair_retry"] = True
                        results.append(retry_result)
                        if bool(retry_result.get("passed")):
                            result["passed"] = True  # Mark original as repaired
                        else:
                            prepare_failed = True
                    else:
                        prepare_failed = True
                else:
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
            # Deterministic repairs before LLM repair loop.
            # 1) CJS export/import mismatch: module.exports = X vs const { X } = require("./x")
            # 2) Test trim mismatch: assertEqual fails due to whitespace-only difference
            cjs_repairs = self._workspace_quality.repair_cjs_export_import_mismatch()
            # Collect test stderr for trim repair
            test_stderr_parts: list[str] = []
            for item in results:
                if str(item.get("phase") or "") == "check" and not bool(item.get("passed")):
                    test_stderr_parts.append(str(item.get("stderr_tail") or ""))
                    test_stderr_parts.append(str(item.get("stdout_tail") or ""))
            trim_repairs = self._workspace_quality.repair_test_trim_mismatch("\n".join(test_stderr_parts))
            deterministic_repairs = {
                "cjs_export_import": cjs_repairs,
                "test_trim": trim_repairs,
            }
            has_deterministic_repairs = bool(cjs_repairs or trim_repairs)
            if has_deterministic_repairs:
                repair_summary["deterministic_repairs"] = deterministic_repairs
                # Re-run check commands after deterministic repairs
                det_rerun: list[dict[str, Any]] = []
                for command in run_commands:
                    rerun_result = await asyncio.to_thread(
                        self._run_workspace_quality_command, command, timeout_seconds
                    )
                    rerun_result["phase"] = "check_after_deterministic_repair"
                    det_rerun.append(rerun_result)
                results.extend(det_rerun)
                # If all checks pass after deterministic repairs — skip LLM repair loop
                if all(bool(item.get("passed")) for item in det_rerun):
                    effective_results = [
                        item for item in results if str(item.get("phase") or "") == "prepare"
                    ] + det_rerun
                    payload = {
                        "schema_version": "factory.workspace_quality_checks.v1",
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "source": "factory_stage_executor",
                        "factory_run_id": run.id,
                        "workspace": str(self.workspace),
                        "passed": True,
                        "commands": results,
                        "repair": repair_summary,
                    }
                    artifact = "runtime/qa/workspace-validation.json"
                    self._write_json_artifact(artifact, payload)
                    return True, artifact

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
                round_repair_evidence = self._workspace_quality_repair_evidence(round_repair_results)
                round_write_tool_evidence = any(
                    bool(item.get("success")) and str(item.get("tool") or item.get("tool_name") or "") == "write_file"
                    for item in round_repair_results
                )
                if round_repair_results and not round_write_tool_evidence and not round_repair_evidence:
                    deterministic_noop_summary = dict(round_summary)
                    round_repair_results, round_summary = await self._apply_workspace_quality_llm_repairs(
                        run_id=run.id,
                        context=context,
                        artifact_quality_errors=repair_errors,
                        repair_attempt=round_index + 1,
                    )
                    if not round_repair_results:
                        round_summary = dict(round_summary)
                        round_summary["deterministic_no_materialized_evidence"] = deterministic_noop_summary
                elif not round_repair_results:
                    round_repair_results, round_summary = await self._apply_workspace_quality_llm_repairs(
                        run_id=run.id,
                        context=context,
                        artifact_quality_errors=repair_errors,
                        repair_attempt=round_index + 1,
                    )
                cpp_post_repair_results = await asyncio.to_thread(self._apply_workspace_quality_cpp_post_repairs)
                if cpp_post_repair_results:
                    round_repair_results.extend(cpp_post_repair_results)
                    round_summary = dict(round_summary)
                    round_summary_tools = [
                        str(item) for item in round_summary.get("source_tools", []) if str(item or "").strip()
                    ]
                    if "deterministic_cpp_post_repair" not in round_summary_tools:
                        round_summary_tools.append("deterministic_cpp_post_repair")
                    round_summary["source_tools"] = round_summary_tools
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
