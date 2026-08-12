"""Private mixin _Mixin00 for OrchestrationStageExecutor."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


from polaris.cells.chief_engineer.blueprint.public import (
    validate_director_handoff_from_payload,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleEvidenceAuthorityBindingV1,
    bind_factory_role_evidence_authority,
)
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    bind_runtime_task_to_factory_run,
)
from polaris.kernelone.constants import (
    MAX_LLM_PROVIDER_TIMEOUT_SECONDS,  # noqa: F401 — re-exported for characterization-test surface
)
from polaris.kernelone.events.final_request_evidence import canonical_role_final_request_json
from polaris.kernelone.fs import (
    GuardedRegularFileSnapshotError,
    KernelFileSystem,
    get_default_adapter,
    guarded_compare_and_replace_regular_file,
    read_guarded_regular_file_snapshot,
)
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage import resolve_storage_roots

from .. import (
    factory_deadline_calculations as deadline_calc,
    factory_pm_contract_normalization as pm_contract_norm,
    factory_prompt_compaction as prompt_compaction,
    factory_stage_helpers as helpers,
    factory_target_file_summaries as target_summaries,
)
from ..factory_artifact_store import ArtifactStore
from ..factory_deadline_calculations import (  # noqa: F401 — re-exported for characterization-test surface
    _CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS,
    _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS,
    _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS,
    ChiefEngineerExecutionAttemptLeaseBudget as _ChiefEngineerExecutionAttemptLeaseBudget,
)
from ..factory_deadline_policy import (
    FactoryDeadlineBudgetPolicyV1,
    TaskDependencyScheduleV1,
    build_task_dependency_schedule,
)
from ..factory_role_evidence_authority import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY,
    FactoryRoleEvidenceAuthorityPort,
)
from ..factory_run_completion import RunCompletionWaiter
from ..factory_run_models import (
    _PM_ARCHITECT_DOC_MAX_CHARS,
    _PM_DIRECTIVE_MAX_CHARS,
    _PM_ORIGINAL_DIRECTIVE_MAX_CHARS,
    FactoryRun,
    StageResult,
)
from ..factory_stage_artifact_bindings import (
    FactoryStageArtifactBindingError,
    parse_factory_stage_artifact_json,
)
from ..factory_workspace_quality import WorkspaceQualityRunner
from ._helpers import (
    _FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY,
    _LANGUAGE_NEUTRAL_EXTENSIONS,
    _LANGUAGE_NEUTRAL_FILENAMES,
    _LANGUAGE_SOURCE_EXTENSIONS,
    _PM_PLAN_ARTIFACT_MAX_BYTES,
    _PRE_DIRECTOR_PLATFORM_PREFIXES,
    _PRE_DIRECTOR_SNAPSHOT_KIND,
    _PRE_DIRECTOR_SNAPSHOT_RELATIVE_DIR,
    _call_accepts_keyword,
    _empty_taskboard_stats,
    _safe_taskboard_stat,
)
from ._pkg_proxy import pkg

logger = logging.getLogger("polaris.cells.factory.pipeline.internal.factory_stage_executor")


class _Mixin00:
    """Method group extracted from OrchestrationStageExecutor (lossless)."""

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

    @staticmethod
    def _factory_role_evidence_cutoff_port(context: Mapping[str, Any]) -> FactoryRoleEvidenceAuthorityPort:
        port = context.get(FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY)
        if type(port) is not FactoryRoleEvidenceAuthorityPort:
            raise RuntimeError("factory_role_evidence_live_cutoff_port_required")
        return port

    @staticmethod
    async def _call_with_factory_role_evidence_authority(
        authority_port: FactoryRoleEvidenceAuthorityPort,
        role: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        authority_binding: FactoryRoleEvidenceAuthorityBindingV1 | None = None,
    ) -> Any:
        """Bind one role-task grant and revoke it if task creation raises.

        A bounded retry for the same controlled child run may reuse the
        caller-owned binding.  This preserves the fixed per-stage grant
        cardinality while every physical request still consumes the grant's
        aggregate attempt budget under a distinct request freeze.
        """

        binding = authority_binding or authority_port.mint_authority_binding(role)
        if binding.role != role or binding.cutoff_port is not authority_port:
            raise RuntimeError("factory_role_evidence_authority_binding_scope_mismatch")
        try:
            with bind_factory_role_evidence_authority(binding):
                return await operation()
        except BaseException:
            authority_port.revoke_authority_binding(binding)
            raise

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
            try:
                path = (workspace_root / target).resolve()
                path.relative_to(workspace_root)
                target_exists = path.exists()
            except (OSError, RuntimeError, ValueError):
                missing.append(target)
                continue
            if not target_exists:
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
        parts = normalized.split("/")
        if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"} for part in parts):
            return False
        if normalized.endswith((".pyc", ".pyo")):
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
        write_json_atomic(str(snapshot_dir / "manifest.json"), manifest)
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

    def _capture_workspace_delivery_state(self) -> dict[str, tuple[int, int]]:
        state: dict[str, tuple[int, int]] = {}
        for path in self._iter_pre_director_snapshot_files():
            try:
                relative = path.relative_to(self.workspace).as_posix()
                stat_result = path.stat()
            except OSError:
                continue
            state[relative] = (int(stat_result.st_size), int(stat_result.st_mtime_ns))
        return state

    @staticmethod
    def _workspace_delivery_delta(
        before: dict[str, tuple[int, int]],
        after: dict[str, tuple[int, int]],
        *,
        max_samples: int = 12,
    ) -> dict[str, Any]:
        before_paths = set(before)
        after_paths = set(after)
        added = sorted(after_paths - before_paths)
        deleted = sorted(before_paths - after_paths)
        changed = sorted(path for path in before_paths & after_paths if before[path] != after[path])
        return {
            "added_count": len(added),
            "changed_count": len(changed),
            "deleted_count": len(deleted),
            "delta_file_count": len(added) + len(changed),
            "added_sample": added[:max_samples],
            "changed_sample": changed[:max_samples],
            "deleted_sample": deleted[:max_samples],
        }

    @staticmethod
    def _workspace_delta_indicates_materialization_progress(delta: dict[str, Any]) -> bool:
        try:
            added = int(delta.get("added_count") or 0)
            changed = int(delta.get("changed_count") or 0)
        except (TypeError, ValueError):
            return False
        return (added + changed) > 0

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

    def _read_catalog_contract(self) -> dict[str, Any]:
        return pm_contract_norm.read_catalog_contract(self.workspace)

    @staticmethod
    def _catalog_delivery_depth_contract(catalog: dict[str, Any]) -> dict[str, Any]:
        return pm_contract_norm.catalog_delivery_depth_contract(catalog)

    @staticmethod
    def _merge_string_list(*values: Any) -> list[str]:
        return pm_contract_norm.merge_string_list(*values)

    @staticmethod
    def _merge_catalog_delivery_depth_contract(
        existing: dict[str, Any],
        catalog_contract: dict[str, Any],
    ) -> dict[str, Any]:
        return pm_contract_norm.merge_catalog_delivery_depth_contract(existing, catalog_contract)

    def _inject_catalog_delivery_depth_contract(self, context: dict[str, Any]) -> None:
        pm_contract_norm.inject_catalog_delivery_depth_contract(
            context,
            self._read_catalog_contract(),
        )

    @staticmethod
    def _normalize_contract_path(value: Any) -> str:
        return pm_contract_norm.normalize_contract_path(value)

    @classmethod
    def _source_target_suffixes(cls) -> frozenset[str]:
        return pm_contract_norm.source_target_suffixes()

    @classmethod
    def _collect_pm_project_declared_target_files(cls, tasks: list[dict[str, Any]]) -> list[str]:
        """Collect write targets from PM task contracts.

        ``target_files`` is the write/materialization surface. ``context_files``
        remains read-only evidence and must not be promoted into this union.
        """

        return pm_contract_norm.collect_pm_project_declared_target_files(tasks)

    @classmethod
    def _filter_source_target_files(cls, paths: list[str]) -> list[str]:
        return pm_contract_norm.filter_source_target_files(paths)

    @staticmethod
    def _filter_entrypoint_like_targets(paths: list[str]) -> list[str]:
        return pm_contract_norm.filter_entrypoint_like_targets(paths)

    def _inject_project_declared_target_contract(
        self,
        context: dict[str, Any],
        *,
        project_declared_target_files: list[str],
    ) -> None:
        pm_contract_norm.inject_project_declared_target_contract(
            context,
            project_declared_target_files=project_declared_target_files,
        )

    def _enrich_pm_plan_contract_artifact(self, relative_path: str = "tasks/plan.json") -> dict[str, Any]:
        target = self._artifact_path(relative_path)
        if not target.exists() or not target.is_file():
            return {"changed": False, "task_count": 0, "declared_target_count": 0}
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
            return {"changed": False, "task_count": 0, "declared_target_count": 0}
        if not isinstance(payload, dict):
            return {"changed": False, "task_count": 0, "declared_target_count": 0}
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list):
            return {"changed": False, "task_count": 0, "declared_target_count": 0}

        task_rows = [dict(item) for item in raw_tasks if isinstance(item, dict)]
        project_declared_targets = self._collect_pm_project_declared_target_files(task_rows)
        changed = False
        enriched_tasks: list[Any] = []
        dict_index = 0
        for item in raw_tasks:
            if not isinstance(item, dict):
                enriched_tasks.append(item)
                continue
            task = dict(task_rows[dict_index])
            dict_index += 1
            before = json.dumps(task, sort_keys=True, ensure_ascii=False)
            self._inject_catalog_delivery_depth_contract(task)
            self._inject_project_declared_target_contract(
                task,
                project_declared_target_files=project_declared_targets,
            )
            after = json.dumps(task, sort_keys=True, ensure_ascii=False)
            if before != after:
                changed = True
            enriched_tasks.append(task)

        if changed:
            updated_payload = dict(payload)
            updated_payload["tasks"] = enriched_tasks
            self._write_json_artifact(relative_path, updated_payload)

        return {
            "changed": changed,
            "task_count": len(task_rows),
            "declared_target_count": len(project_declared_targets),
            "source_target_count": len(self._filter_source_target_files(project_declared_targets)),
        }

    def _load_pm_plan_tasks(
        self,
        relative_path: str = "tasks/plan.json",
        *,
        include_mirrors: bool = True,
    ) -> list[dict[str, Any]]:
        candidates = [self._artifact_path(relative_path)]
        if include_mirrors and relative_path == "tasks/plan.json":
            candidates.extend(self._iter_pm_plan_contract_candidates())

        seen: set[str] = set()
        for target in candidates:
            key = target.resolve().as_posix()
            if key in seen:
                continue
            seen.add(key)
            if not target.exists():
                continue
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
                continue
            tasks = self._pm_plan_tasks_from_payload(payload)
            if tasks:
                return tasks
        return []

    def _persist_normalized_pm_plan_validation_contracts(
        self,
        relative_path: str = "tasks/plan.json",
    ) -> dict[str, Any]:
        """Persist the exact normalized PM tasks consumed by CE provenance.

        Normalization was historically applied only by the in-memory loader,
        so the CE context could differ from the immutable ``tasks/plan.json``
        later bound by Factory.  Persisting first makes repeated loads
        idempotent and gives PM binding/CE ``pm_task_contract`` one exact fact.
        """

        if relative_path != "tasks/plan.json":
            raise FactoryStageArtifactBindingError(
                "factory_stage_artifact_pm_plan_path_invalid",
                "PM validation-contract normalization only accepts tasks/plan.json",
            )

        runtime_root = resolve_storage_roots(str(self.workspace)).runtime_root
        try:
            source_snapshot = read_guarded_regular_file_snapshot(
                str(runtime_root),
                relative_path,
                _PM_PLAN_ARTIFACT_MAX_BYTES,
            )
        except GuardedRegularFileSnapshotError as exc:
            if exc.code == "guarded_snapshot_missing":
                return {"changed": False, "task_count": 0}
            raise

        payload = parse_factory_stage_artifact_json(source_snapshot.content)
        raw_tasks = payload.get("tasks")
        if type(raw_tasks) is not list:
            raise FactoryStageArtifactBindingError(
                "factory_stage_artifact_pm_tasks_invalid",
                "PM plan tasks must be an exact JSON list before normalization",
            )
        if any(type(item) is not dict for item in raw_tasks):
            raise FactoryStageArtifactBindingError(
                "factory_stage_artifact_pm_task_invalid",
                "Every PM plan task must be an exact JSON object before normalization",
            )

        task_rows = [deepcopy(item) for item in raw_tasks]
        normalized = self._normalize_pm_plan_validation_contracts(task_rows)
        changed = normalized != raw_tasks
        if changed:
            updated = deepcopy(payload)
            updated["tasks"] = normalized
            replacement = (canonical_role_final_request_json(updated) + "\n").encode("utf-8")
            committed_snapshot = guarded_compare_and_replace_regular_file(
                str(runtime_root),
                source_snapshot,
                replacement,
                max_bytes=_PM_PLAN_ARTIFACT_MAX_BYTES,
            )
            reread_snapshot = read_guarded_regular_file_snapshot(
                str(runtime_root),
                relative_path,
                _PM_PLAN_ARTIFACT_MAX_BYTES,
            )
            if (
                reread_snapshot.content != replacement
                or reread_snapshot.content != committed_snapshot.content
                or reread_snapshot.size != committed_snapshot.size
                or reread_snapshot.device != committed_snapshot.device
                or reread_snapshot.inode != committed_snapshot.inode
            ):
                raise FactoryStageArtifactBindingError(
                    "factory_stage_artifact_pm_plan_postread_mismatch",
                    "PM plan changed after guarded normalization commit",
                )
            reread_payload = parse_factory_stage_artifact_json(reread_snapshot.content)
            if reread_payload.get("tasks") != normalized:
                raise FactoryStageArtifactBindingError(
                    "factory_stage_artifact_pm_plan_postread_mismatch",
                    "Strict PM plan reread does not contain the normalized task vector",
                )
        return {"changed": changed, "task_count": len(normalized)}

    @staticmethod
    def _pm_plan_tasks_from_payload(payload: Any) -> list[dict[str, Any]]:
        return pm_contract_norm.pm_plan_tasks_from_payload(payload)

    _PM_TEST_COMMAND_RE = pm_contract_norm._PM_TEST_COMMAND_RE
    _PM_NON_TEST_COMMAND_RE = pm_contract_norm._PM_NON_TEST_COMMAND_RE

    @staticmethod
    def _normalize_pm_plan_validation_contracts(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep per-task test acceptance aligned with the task that owns test targets."""

        return pm_contract_norm.normalize_pm_plan_validation_contracts(tasks)

    @staticmethod
    def _is_pm_validation_target_path(path: str) -> bool:
        return pm_contract_norm.is_pm_validation_target_path(path)

    @staticmethod
    def _acceptance_without_test_commands(acceptance: list[str]) -> tuple[list[str], list[str]]:
        return pm_contract_norm.acceptance_without_test_commands(acceptance)

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

        if self._load_pm_plan_tasks("tasks/plan.json", include_mirrors=False):
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
        run_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not tasks:
            return {"ensured_count": 0, "created_count": 0, "task_ids": []}

        service = pkg().TaskRuntimeService(str(self.workspace))
        task_ids: list[str] = []
        created_count = 0
        bound_count = 0
        binding_failures: list[dict[str, str]] = []
        for index, task in enumerate(tasks, start=1):
            task_id = self._task_id(task, index)
            if not task_id:
                continue
            existing = service.get_task(task_id)
            metadata_raw = task.get("metadata")
            metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
            metadata.pop(_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY, None)
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
            lease_task_metadata = self._factory_workspace_run_lease_task_metadata(run_metadata)
            metadata.update(lease_task_metadata)
            for key in ("scope", "target_files", "acceptance", "acceptance_criteria", "steps", "depends_on"):
                if key in task:
                    metadata.setdefault(key, task.get(key))
            description_parts = [
                self._task_string(task, "description"),
                "\n".join(self._task_string_list(task, "steps")),
                "\n".join(self._task_string_list(task, "acceptance", "acceptance_criteria")),
            ]
            description = "\n\n".join(part for part in description_parts if part.strip())
            ensured_row = service.ensure_task_row(
                external_task_id=task_id,
                subject=self._task_objective(task),
                description=description,
                metadata=metadata,
                priority=task.get("priority", index),
            )
            binding_result = bind_runtime_task_to_factory_run(
                BindRuntimeTaskToFactoryRunCommandV1(
                    workspace=str(self.workspace),
                    task_id=task_id,
                    factory_run_id=str(run_id or "").strip(),
                )
            )
            if binding_result.ok:
                bound_count += 1
                if existing is not None and lease_task_metadata:
                    existing_metadata_raw = existing.get("metadata")
                    existing_metadata = existing_metadata_raw if isinstance(existing_metadata_raw, Mapping) else {}
                    projected_lease = lease_task_metadata[_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY]
                    if existing_metadata.get(_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY) != projected_lease:
                        refreshed_row = service.update_task_row(
                            ensured_row.get("id"),
                            metadata=lease_task_metadata,
                        )
                        if refreshed_row is None:
                            binding_failures.append(
                                {
                                    "task_id": task_id,
                                    "code": "factory_workspace_run_lease_projection_failed",
                                    "reason": "TaskRuntime could not refresh Factory lease provenance",
                                    "existing_factory_run_id": str(run_id or "").strip(),
                                }
                            )
            else:
                binding_failures.append(
                    {
                        "task_id": task_id,
                        "code": binding_result.code,
                        "reason": binding_result.reason,
                        "existing_factory_run_id": binding_result.existing_factory_run_id,
                    }
                )
            if existing is None:
                created_count += 1
            task_ids.append(task_id)

        return {
            "ensured_count": len(task_ids),
            "created_count": created_count,
            "bound_count": bound_count,
            "binding_failures": binding_failures,
            "task_ids": task_ids,
        }

    @staticmethod
    def _factory_workspace_run_lease_task_metadata(
        run_metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Detach Factory-owned workspace authority for TaskRuntime facts.

        The lease remains owned by the Factory run/admission ledger. Task
        metadata carries an immutable-at-materialization projection so a later
        TaskRuntime terminal fact can identify the fencing authority used by
        its Factory run. PM task metadata with the same key is deliberately
        discarded by the caller and can never mint this projection.

        Complexity:
            O(n) time and memory over the lease metadata payload.
        """

        if not isinstance(run_metadata, Mapping):
            return {}
        lease_raw = run_metadata.get(_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY)
        if not isinstance(lease_raw, Mapping):
            return {}
        lease_projection: dict[str, Any] = {str(key): deepcopy(value) for key, value in lease_raw.items()}
        if not lease_projection:
            return {}
        return {_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY: lease_projection}

    @staticmethod
    def _compact_text_for_prompt(text: str, *, max_chars: int) -> str:
        return helpers.compact_text_for_prompt(text, max_chars=max_chars)

    @staticmethod
    def _compact_workspace_quality_evidence_for_qa(text: str) -> str:
        """Build a short, parseable workspace-quality JSON payload for QA."""

        return prompt_compaction.compact_workspace_quality_evidence_for_qa(text)

    @staticmethod
    def _compact_blueprint_evidence_for_repair(text: str) -> str:
        return prompt_compaction.compact_blueprint_evidence_for_repair(text)

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

    def _read_json_artifact_payload(self, relative_path: str) -> dict[str, Any]:
        target = self._artifact_path(relative_path)
        if not target.exists() or not target.is_file():
            return {}
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def _load_chief_engineer_review_payload(self, *, run_id: str = "") -> dict[str, Any]:
        resolved_run_id = str(run_id or "").strip()
        if not resolved_run_id:
            return {}
        payload = self._read_json_artifact_payload(f"runtime/state/blueprints/{resolved_run_id}.review.json")
        if not payload:
            return {}
        payload_run_id = str(payload.get("factory_run_id") or "").strip()
        if payload_run_id and payload_run_id != resolved_run_id:
            return {}
        return payload

    def _chief_engineer_handoff_signals_for_director(
        self,
        pm_tasks: list[dict[str, Any]],
        *,
        run_id: str = "",
    ) -> list[dict[str, Any]]:
        """Validate PM task contracts have handoff-ready CE blueprints."""

        expected_task_ids = [self._task_id(task, index) for index, task in enumerate(pm_tasks, start=1)]
        expected_task_ids = [task_id for task_id in expected_task_ids if task_id]
        if not expected_task_ids:
            return []

        review_payload = self._load_chief_engineer_review_payload(run_id=run_id)
        raw_rows = review_payload.get("blueprints") if isinstance(review_payload, dict) else None
        rows = [dict(item) for item in raw_rows if isinstance(item, dict)] if isinstance(raw_rows, list) else []

        rows_by_task: dict[str, dict[str, Any]] = {}
        for row in rows:
            task_id = str(row.get("task_id") or "").strip()
            if task_id:
                rows_by_task[task_id] = row

        signals: list[dict[str, Any]] = []
        if not rows_by_task:
            return [
                {
                    "code": "director.chief_engineer_handoff_missing",
                    "severity": "error",
                    "detail": "Director dispatch requires Chief Engineer review evidence before execution.",
                    "expected_task_ids": expected_task_ids,
                    "review_artifact_found": bool(review_payload),
                }
            ]

        for task_id in expected_task_ids:
            blueprint_row = rows_by_task.get(task_id)
            if blueprint_row is None:
                signals.append(
                    {
                        "code": "director.chief_engineer_blueprint_missing_for_task",
                        "severity": "error",
                        "detail": "No Chief Engineer blueprint row was found for PM task before Director dispatch.",
                        "task_id": task_id,
                    }
                )
                continue

            blueprint_id = str(blueprint_row.get("blueprint_id") or "").strip()
            if not blueprint_id:
                signals.append(
                    {
                        "code": "director.chief_engineer_blueprint_id_missing",
                        "severity": "error",
                        "detail": "Chief Engineer blueprint row is missing blueprint_id.",
                        "task_id": task_id,
                    }
                )
                continue

            validation = validate_director_handoff_from_payload(
                str(self.workspace),
                {"task_id": task_id, "blueprint_id": blueprint_id},
                require_strict=True,
            )
            handoff_payload_raw = validation.get("decision_payload")
            handoff_payload: dict[str, Any] = handoff_payload_raw if isinstance(handoff_payload_raw, dict) else {}
            if not validation.get("allowed") and not handoff_payload:
                signals.append(
                    {
                        "code": "director.chief_engineer_blueprint_unreadable",
                        "severity": "error",
                        "detail": str(
                            validation.get("reason")
                            or "Chief Engineer blueprint could not be loaded for handoff validation."
                        ),
                        "task_id": task_id,
                        "blueprint_id": blueprint_id,
                        "handoff_validation": validation,
                    }
                )
                continue
            if not validation.get("allowed"):
                signals.append(
                    {
                        "code": "director.chief_engineer_handoff_blocked",
                        "severity": "error",
                        "detail": str(validation.get("reason") or "Chief Engineer handoff blocked Director dispatch."),
                        "task_id": task_id,
                        "blueprint_id": blueprint_id,
                        "blockers": list(handoff_payload.get("blockers") or []),
                        "handoff_decision": handoff_payload,
                        "handoff_validation": validation,
                    }
                )
        return signals

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
        context = deepcopy(task)
        # Preserve the validated PM task as a named evidence slot. Flattened
        # task fields are useful prompt material, but they are not a provenance
        # reference and cannot satisfy final-request contract coverage.
        context["pm_task_contract"] = deepcopy(task)
        context["source_artifact"] = "tasks/plan.json"
        context["factory_run_id"] = run_id
        context["task_index"] = index
        title = self._task_string(task, "title", "subject", "goal")
        if title:
            context["task_title"] = title
        scope = self._task_string(task, "scope")
        if scope:
            context.setdefault("scope_paths", [scope])
        self._inject_catalog_delivery_depth_contract(context)
        # Inject existing target file contents so the CE blueprint (and Director)
        # can see the actual API of files created by earlier tasks. Without this,
        # test-generation tasks guess at class/function names and produce broken tests.
        existing_file_context = self._read_existing_target_file_summaries(task)
        if existing_file_context:
            context["existing_target_files"] = existing_file_context
        return context

    _EXISTING_SUMMARY_SOURCE_SUFFIXES = target_summaries._EXISTING_SUMMARY_SOURCE_SUFFIXES
    _EXISTING_SUMMARY_MAX_FILES = target_summaries._EXISTING_SUMMARY_MAX_FILES

    def _read_existing_target_file_summaries(
        self, task: dict[str, Any], *, max_chars_per_file: int = 1500
    ) -> list[dict[str, str]]:
        """Summarize the export API of files this task depends on but does NOT own."""

        return target_summaries.read_existing_target_file_summaries(
            self.workspace, task, max_chars_per_file=max_chars_per_file
        )

    @staticmethod
    def _extract_js_export_summary(content: str) -> str:
        """Extract JS/TS export signatures so dependent files reference real symbols."""

        return target_summaries.extract_js_export_summary(content)

    @staticmethod
    def _extract_py_export_summary(content: str) -> str:
        """Extract Python export signatures for cross-file coherence."""

        return target_summaries.extract_py_export_summary(content)

    @staticmethod
    def _extract_py_export_summary_fallback(content: str) -> str:
        """Line-scan fallback when the dependency source does not parse as Python."""

        return target_summaries.extract_py_export_summary_fallback(content)

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
        try:
            payload = pkg().TaskRuntimeService(str(self.workspace)).get_observable_task_row_stats()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug("Failed to read observable task stats for factory taskboard projection", exc_info=True)
            return _empty_taskboard_stats()
        if not isinstance(payload, dict):
            return _empty_taskboard_stats()
        stats = _empty_taskboard_stats()
        for key, value in payload.items():
            stats[str(key)] = _safe_taskboard_stat(value)
        return stats

    def _query_observable_task_rows(
        self,
        *,
        factory_run_id: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Return authoritative rows or one typed fail-closed diagnostic.

        Degraded transitional rows remain available to UI/diagnostic consumers
        through TaskRuntime, but Factory stage decisions fail closed instead of
        allowing file fallback to authorize execution or verification.
        """

        try:
            projection = pkg().TaskRuntimeService(str(self.workspace)).query_observable_task_rows_projection()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return [], {
                "code": "director.task_runtime_fact_projection_unavailable",
                "severity": "error",
                "detail": (f"TaskRuntime fact-only observable projection is unavailable: {type(exc).__name__}: {exc}"),
                "failure_class": FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
                "responsible_layer": "task_runtime",
                "repairable_by_director": False,
            }
        if (
            projection.authoritative is not True
            or projection.degraded
            or projection.source != "task_runtime.execution_fact"
            or projection.readiness.get("ready") is not True
        ):
            readiness = dict(projection.readiness)
            return [], {
                "code": "director.task_runtime_fact_projection_not_ready",
                "severity": "error",
                "detail": (
                    "TaskRuntime fact-only observable projection is not ready: "
                    f"source={projection.source}; readiness={readiness}"
                ),
                "failure_class": FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
                "responsible_layer": "task_runtime",
                "repairable_by_director": False,
                "projection_source": projection.source,
                "projection_readiness": readiness,
            }
        rows = projection.rows_for_factory_run(factory_run_id) if str(factory_run_id or "").strip() else projection.rows
        return [dict(row) for row in rows if isinstance(row, Mapping)], None

    def _read_observable_task_rows(self, *, factory_run_id: str = "") -> list[dict[str, Any]]:
        """Return only authoritative TaskRuntime fact-projected rows."""

        rows, failure = self._query_observable_task_rows(factory_run_id=factory_run_id)
        if failure is not None:
            logger.warning("Factory rejected TaskRuntime projection: %s", failure)
        return rows

    def _read_claimable_director_task_ids(
        self,
        *,
        limit: int,
        factory_run_id: str = "",
        allowed_task_ids: Iterable[str] | None = None,
    ) -> list[str]:
        """Return claimable PM ids confined to the admitted dependency wave.

        TaskRuntime readiness is the execution-state authority, while the
        immutable PM contract owns the dependency DAG.  ``blocked_by`` on
        legacy task rows is not a substitute for that contract: older rows may
        only carry ``depends_on`` in metadata.  The caller therefore supplies
        the currently admitted wave and this projection intersects both facts
        before any Director provider request can start.
        """
        if limit <= 0:
            return []
        allowed: set[str] | None = None
        if allowed_task_ids is not None:
            allowed = {str(item or "").strip() for item in allowed_task_ids if str(item or "").strip()}
            if not allowed:
                return []
        rows = self._read_observable_task_rows(factory_run_id=factory_run_id)

        ids: list[str] = []
        seen: set[str] = set()
        for row in rows:
            if self._is_internal_chief_engineer_task_row(
                row,
                factory_run_id=factory_run_id,
            ):
                continue
            status = str(row.get("status") or "").strip().lower()
            if status not in {"pending", "ready"}:
                continue
            blocked_by = row.get("blocked_by") if isinstance(row.get("blocked_by"), list) else row.get("blockedBy")
            if blocked_by:
                continue
            task_id = self._task_projection_external_id(row)
            if not task_id or task_id in seen:
                continue
            if allowed is not None and task_id not in allowed:
                continue
            seen.add(task_id)
            ids.append(task_id)
            if len(ids) >= limit:
                break
        return ids

    @staticmethod
    def _task_projection_external_id(row: Mapping[str, Any]) -> str:
        """Return the PM identity represented by one TaskRuntime projection.

        TaskRuntime owns a numeric storage identity while PM dependency graphs
        use stable external task ids.  Every Factory consumer must resolve the
        same identity precedence or its projections can disagree about which
        task is claimable, unresolved, or admitted by the deadline policy.
        """

        metadata_raw = row.get("metadata")
        metadata: Mapping[str, Any] = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        return str(
            metadata.get("external_task_id")
            or metadata.get("pm_task_id")
            or metadata.get("source_task_id")
            or metadata.get("task_id")
            or row.get("task_id")
            or row.get("id")
            or ""
        ).strip()

    @staticmethod
    def _director_dependency_settle_grace_seconds(
        context: dict[str, Any],
    ) -> float:
        """Return the bounded grace for dependency-unblock fact propagation."""

        raw_value = context.get("director_dependency_settle_grace_seconds")
        try:
            parsed = float(raw_value) if raw_value is not None else 2.0
        except (TypeError, ValueError):
            parsed = 2.0
        return max(0.0, min(parsed, 10.0))

    async def _wait_for_claimable_director_tasks(
        self,
        *,
        limit: int,
        grace_seconds: float,
        factory_run_id: str = "",
        dependency_tasks: list[dict[str, Any]] | None = None,
    ) -> tuple[list[str], dict[str, int]]:
        """Wait briefly for completion-triggered dependency facts to settle.

        The wait is read-only and bounded. A newly claimable task causes the
        caller to start a fresh dispatch round so deadline admission is
        recalculated. No task state is inferred or mutated here.

        Complexity:
            O(p * r) time and O(r) memory for ``p`` projection polls over ``r``
            observable task rows.
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, grace_seconds)
        latest_stats = self._read_taskboard_stats()
        while True:
            allowed_task_ids: Iterable[str] | None = None
            if dependency_tasks is not None:
                schedule = self._director_dependency_schedule(
                    dependency_tasks,
                    factory_run_id=factory_run_id,
                )
                allowed_task_ids = schedule.waves[0] if schedule.valid and schedule.waves else ()
            claim_kwargs: dict[str, Any] = {
                "limit": limit,
                "factory_run_id": factory_run_id,
            }
            if _call_accepts_keyword(self._read_claimable_director_task_ids, "allowed_task_ids"):
                claim_kwargs["allowed_task_ids"] = allowed_task_ids
            task_ids = self._read_claimable_director_task_ids(**claim_kwargs)
            latest_stats = self._read_taskboard_stats()
            if task_ids or self._is_taskboard_converged(latest_stats):
                return task_ids, latest_stats
            remaining = deadline - loop.time()
            if remaining <= 0:
                return [], latest_stats
            await asyncio.sleep(min(0.1, remaining))

    @staticmethod
    def _remaining_director_task_count(stats: dict[str, int], *, fallback: int) -> int:
        """Return unresolved PM task owners from the observable projection."""

        total = max(0, _safe_taskboard_stat(stats.get("total")))
        terminal = sum(_safe_taskboard_stat(stats.get(key)) for key in ("completed", "failed", "cancelled"))
        if total > 0:
            return max(1, total - terminal)
        return max(1, int(fallback))

    @staticmethod
    def _is_taskboard_converged(stats: dict[str, int]) -> bool:
        return helpers.is_taskboard_converged(stats)

    @staticmethod
    def _taskboard_has_active_execution(stats: Mapping[str, Any]) -> bool:
        """Whether authoritative TaskRuntime facts prove a child is still active.

        The orchestration lifecycle may publish a non-success terminal result
        before the TaskRuntime-owned execution row reaches its terminal fact.
        In that interval the lifecycle progress marker can be absent even though
        the child is physically executing.  TaskRuntime is the canonical task
        authority, so any active row must preserve the already-admitted Director
        execution lease instead of collapsing the wait to the 5s settlement
        reserve.
        """

        return any(
            _safe_taskboard_stat(stats.get(key)) > 0
            for key in (
                "in_progress",
                "in_design",
                "in_execution",
                "in_qa",
                "running",
                "processing",
                "executing",
            )
        )

    @staticmethod
    def _has_director_progress(before: dict[str, int], after: dict[str, int]) -> bool:
        return helpers.has_director_progress(before, after)

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
    def _director_dispatch_timeout_seconds(
        context: dict[str, Any],
        *,
        task_count: int,
        materialization_pending: bool = False,
    ) -> int:
        return deadline_calc.director_dispatch_timeout_seconds(
            context,
            task_count=task_count,
            materialization_pending=materialization_pending,
        )

    @staticmethod
    def _factory_deadline_budget_policy(
        context: dict[str, Any],
        *,
        chief_engineer_generation_floor_seconds: float = 0.0,
    ) -> FactoryDeadlineBudgetPolicyV1:
        """Resolve infrastructure configuration into the pure deadline policy."""

        return deadline_calc.factory_deadline_budget_policy(
            context,
            chief_engineer_generation_floor_seconds=chief_engineer_generation_floor_seconds,
            director_first_task_min_seconds=(
                pkg().OrchestrationStageExecutor._director_first_materialization_min_budget_seconds(context)
            ),
            quality_gate_reserved_seconds=pkg().OrchestrationStageExecutor._quality_gate_reserved_budget_seconds(
                context
            ),
            director_settlement_barrier_seconds=(
                pkg().OrchestrationStageExecutor._director_dispatch_timeout_settle_grace_seconds(context)
            ),
        )

    @staticmethod
    def _unresolved_task_ids_from_rows(rows: list[dict[str, Any]]) -> tuple[str, ...]:
        """Return non-terminal task identifiers from authoritative projections."""

        terminal_statuses = {"completed", "completed_verified", "failed", "cancelled"}
        unresolved: list[str] = []
        for row in rows:
            task_id = pkg().OrchestrationStageExecutor._task_projection_external_id(row)
            status = str(row.get("status") or row.get("state") or "").strip().lower()
            if task_id and status not in terminal_statuses and task_id not in unresolved:
                unresolved.append(task_id)
        return tuple(unresolved)

    @staticmethod
    def _is_internal_chief_engineer_task_row(
        row: Mapping[str, Any],
        *,
        factory_run_id: str,
    ) -> bool:
        """Identify a trusted Factory-owned CE execution row.

        Director dependency admission is defined over PM task identities.  CE
        portfolio and schema-repair attempts are separate TaskRuntime facts and
        must remain observable, but they are not vertices in the PM dependency
        graph.  The exclusion is provenance-bound so an arbitrary unknown task
        id still invalidates the schedule fail-closed.
        """

        resolved_run_id = str(factory_run_id or "").strip()
        if not resolved_run_id:
            return False
        metadata_raw = row.get("metadata")
        metadata: Mapping[str, Any] = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        external_task_id = pkg().OrchestrationStageExecutor._task_projection_external_id(row)
        return bool(
            external_task_id
            and str(metadata.get("factory_run_id") or "").strip() == resolved_run_id
            and str(metadata.get("factory_stage") or "").strip() == "chief_engineer_review"
            and str(metadata.get("role") or "").strip() == "chief_engineer"
            and str(metadata.get("external_task_id") or "").strip() == external_task_id
            and str(metadata.get("source_task_id") or "").strip() == external_task_id
            and str(metadata.get("materialized_by") or "").strip() == "runtime.task_runtime"
        )

    def _director_dependency_schedule(
        self,
        pm_tasks: list[dict[str, Any]],
        *,
        factory_run_id: str = "",
    ) -> TaskDependencyScheduleV1:
        """Project the remaining Director critical path from TaskRuntime facts."""

        observable_rows = self._read_observable_task_rows(factory_run_id=factory_run_id)
        dependency_rows = [
            row
            for row in observable_rows
            if not self._is_internal_chief_engineer_task_row(
                row,
                factory_run_id=factory_run_id,
            )
        ]
        active_task_ids = self._unresolved_task_ids_from_rows(dependency_rows)
        return build_task_dependency_schedule(
            pm_tasks,
            active_task_ids=active_task_ids if observable_rows else None,
        )
