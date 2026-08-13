"""Characterization tests for PM meta diagnostics + task-field accessors + verified-delivery recovery."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import os
import shutil
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BlueprintPersistence,
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
    derive_project_kind_authority_from_catalog_snapshot,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    TaskBlueprintResultV1,
    _issue_chief_engineer_portfolio_authority_carrier,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.events.fact_stream.public.service import (
    QueryFactEventsV1,
    query_fact_events,
)
from polaris.cells.factory.pipeline.internal import (
    factory_stage_executor as stage_executor_module,
    factory_workspace_quality as workspace_quality_module,
)
from polaris.cells.factory.pipeline.internal.factory_deadline_policy import (
    FactoryDeadlineBudgetPolicyV1,
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_completion import RunCompletionWaiter
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.internal.factory_settlement_consumer import _fencing_token
from polaris.cells.factory.pipeline.internal.factory_stage_helpers import (
    evaluate_canonical_factory_authority,
)
from polaris.cells.factory.pipeline.internal.run_ledger import load_run_ledger_projection
from polaris.cells.roles.adapters.public import (
    build_director_materialization_quality_repair_message,
    extract_workspace_quality_summary,
    resolve_director_semantic_quality_repair_target_files,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    ObservableTaskRowsProjectionV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path


from polaris.cells.factory.pipeline.tests._characterization_helpers import (  # noqa: F401
    _executor,
    _verified_delivery_recovery_authority,
)


class TestPmMetaDiagnostic:
    def test_is_pm_meta_diagnostic_task_true(self) -> None:
        task = {"title": "x", "goal": "多个任务标题/goal 重复", "description": ""}
        assert OrchestrationStageExecutor._is_pm_meta_diagnostic_task(task) is True

    def test_is_pm_meta_diagnostic_task_false(self) -> None:
        task = {"title": "实现登录", "goal": "完成登录功能", "description": "登录"}
        assert OrchestrationStageExecutor._is_pm_meta_diagnostic_task(task) is False


class TestTaskFieldAccessors:
    def test_task_string_first_nonempty(self) -> None:
        executor = _executor(Path("."))
        assert executor._task_string({"a": "", "b": "val"}, "a", "b") == "val"

    def test_task_string_numeric_coercion(self) -> None:
        executor = _executor(Path("."))
        assert executor._task_string({"n": 5}, "n") == "5"

    def test_task_string_list_flattens(self) -> None:
        executor = _executor(Path("."))
        assert executor._task_string_list({"k": ["a", "", "b"], "j": "c"}, "k", "j") == ["a", "b", "c"]

    def test_task_id_fallback(self) -> None:
        executor = _executor(Path("."))
        assert executor._task_id({}, 3) == "task-3"
        assert executor._task_id({"id": "X"}, 3) == "X"

    def test_task_objective_fallback(self) -> None:
        executor = _executor(Path("."))
        assert executor._task_objective({}) == "Prepare Director implementation blueprint"
        assert executor._task_objective({"goal": "G"}) == "G"

    def test_build_director_task_filter(self) -> None:
        executor = _executor(Path("."))
        assert executor._build_director_task_filter([]) == "Execute ready tasks from PM contract"
        result = executor._build_director_task_filter([{"title": "T1", "scope": "src/"}])
        assert "Execute PM tasks strictly in order:" in result
        assert "- T1 [scope: src/]" in result


class TestExistingTargetFileSummaries:
    """Cross-file coherence: a later task must see the API of files it depends on.

    Regression (factory-bench L1-03): TASK-1 created src/models/mood.py defining
    ``Mood`` as an enum; TASK-2 wrote src/main.py and — without the dependency
    signature — guessed ``Mood(mood=..., intensity=...)``, crashing entrypoint
    smoke with ``EnumType.__call__() got an unexpected keyword argument 'mood'``.
    The injection must surface the dependency file's signature, NOT just the
    task's own (not-yet-written) targets.
    """

    def test_dependency_file_signature_is_injected_for_later_task(self, tmp_path: Path) -> None:
        # TASK-1 already wrote the model (a dependency of TASK-2).
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "models" / "mood.py").write_text(
            "from enum import Enum\n\n\nclass Mood(Enum):\n    SUNNY = 'sunny'\n    CALM = 'calm'\n\n\n"
            "def derive_mood(weather):\n    return Mood.CALM\n",
            encoding="utf-8",
        )
        executor = _executor(tmp_path)

        # TASK-2 owns main.py (which does NOT exist yet) and depends on mood.py.
        task2 = {"target_files": ["src/main.py"]}
        summaries = executor._read_existing_target_file_summaries(task2)

        by_path = {s["path"]: s["exports"] for s in summaries}
        # The dependency file's real signature must be present even though it is
        # not one of TASK-2's own target_files.
        assert "src/models/mood.py" in by_path
        assert "class Mood(Enum):" in by_path["src/models/mood.py"]
        assert "def derive_mood" in by_path["src/models/mood.py"]
        # main.py is the task's own target and does not exist yet → not summarized.
        assert "src/main.py" not in by_path

    def test_runtime_and_dotpolaris_paths_are_excluded(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "core.py").write_text("def core():\n    return 1\n", encoding="utf-8")
        # Noise that must never enter Director context.
        (tmp_path / ".polaris" / "history").mkdir(parents=True)
        (tmp_path / ".polaris" / "history" / "leak.py").write_text("def leak():\n    return 1\n", encoding="utf-8")
        (tmp_path / "runtime").mkdir()
        (tmp_path / "runtime" / "noise.py").write_text("def noise():\n    return 1\n", encoding="utf-8")
        executor = _executor(tmp_path)

        summaries = executor._read_existing_target_file_summaries({"target_files": ["src/main.py"]})
        paths = {s["path"] for s in summaries}
        assert "src/core.py" in paths
        assert not any(".polaris" in p or "runtime/" in p for p in paths)

    def test_no_existing_files_returns_empty(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        assert executor._read_existing_target_file_summaries({"target_files": ["src/main.py"]}) == []


# ---------------------------------------------------------------------------
# WS4 typed-quality-issue seam regression guards
# ---------------------------------------------------------------------------


def test_workspace_quality_repair_issue_payloads_preserves_scanner_typed_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = "typed scanner diagnostic for src/main.py"
    typed_issue = {
        "code": "syntax_error",
        "message": raw,
        "path": "src/main.py",
        "severity": "error",
        "source": "source_syntax_checker",
        "metadata": {
            "raw": raw,
            "diagnostic_kind": "syntax_error",
            "scanner_owned": True,
        },
    }

    def fake_scan_workspace_artifact_quality_evidence(workspace: str) -> SimpleNamespace:
        assert workspace == str(tmp_path)
        return SimpleNamespace(errors=(raw,), issues=(typed_issue,))

    monkeypatch.setattr(
        "polaris.kernelone.quality.scan_workspace_artifact_quality_evidence",
        fake_scan_workspace_artifact_quality_evidence,
    )

    payloads = _executor(tmp_path)._workspace_quality_repair_issue_payloads([raw])

    assert len(payloads) == 1
    assert payloads[0]["code"] == "syntax_error"
    assert payloads[0]["path"] == "src/main.py"
    assert payloads[0]["source"] == "source_syntax_checker"
    assert payloads[0]["metadata"]["diagnostic_kind"] == "syntax_error"
    assert payloads[0]["metadata"]["scanner_owned"] is True


def test_workspace_quality_repair_issue_payloads_falls_back_to_string_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = "Artifact quality scan failed: workspace path does not exist"

    def broken_scan_workspace_artifact_quality_evidence(workspace: str) -> SimpleNamespace:
        assert workspace == str(tmp_path)
        raise OSError("scanner unavailable")

    monkeypatch.setattr(
        "polaris.kernelone.quality.scan_workspace_artifact_quality_evidence",
        broken_scan_workspace_artifact_quality_evidence,
    )

    payloads = _executor(tmp_path)._workspace_quality_repair_issue_payloads([raw])

    assert len(payloads) == 1
    assert payloads[0]["message"] == raw.removeprefix("Artifact quality scan failed:").strip()
    assert payloads[0]["metadata"]["raw"] == raw


def test_chief_engineer_portfolio_context_includes_local_rework_failure_feedback(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    failure_feedback = {
        "schema_version": "factory.chief_engineer_local_rework.v1",
        "cycle": 1,
        "stage_output": "project_completion_contract.obligations is required",
        "preserved_pm_contract": True,
    }

    context = executor._chief_engineer_portfolio_context(
        [
            {
                "id": "TASK-1",
                "goal": "Build a runnable CLI",
                "target_files": ["src/main.py"],
                "scope_paths": ["src/main.py"],
                "acceptance_criteria": ["python src/main.py exits 0"],
                "steps": ["Implement the entrypoint"],
            }
        ],
        run_id="factory-local-ce-rework",
        failure_feedback=failure_feedback,
    )

    assert context["chief_engineer_local_rework"] is True
    assert context["failure_feedback"] == failure_feedback
    assert context["failure_feedback"] is not failure_feedback


def test_reconcile_verified_runtime_delivery_settles_exact_failed_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run = FactoryRun(
        id="factory-runtime-reconcile",
        config=FactoryConfig(name="bench-run"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-08-10T00:00:00+00:00",
    )
    identity = TaskRuntimeExecutionAttemptIdentityV1(
        workspace=str(tmp_path),
        task_id=3,
        external_task_id="TASK-3",
        session_id="verified-delivery-reconcile-session",
        attempt=2,
        role_id="qa",
        worker_id=f"factory-quality-gate:{run.id}",
        run_id=run.id,
        lease_expires_at="2026-08-10T00:02:00+00:00",
    )

    class _Runtime:
        def __init__(self) -> None:
            self.reopened: list[tuple[int, str, dict[str, Any]]] = []
            self.claimed: list[tuple[int, dict[str, Any]]] = []
            self.settled: list[SettleTaskRuntimeExecutionAttemptCommandV1] = []

        def list_observable_task_rows(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 3,
                    "external_task_id": "TASK-3",
                    "status": "failed",
                    "metadata": {
                        "factory_run_id": run.id,
                        "source_task_id": "TASK-3",
                    },
                }
            ]

        def reopen_task_row(self, task_id: int, *, reason: str, metadata: dict[str, Any]) -> dict[str, Any]:
            self.reopened.append((task_id, reason, metadata))
            return {"id": task_id, "status": "pending"}

        def claim_execution(self, task_id: int, **kwargs: Any) -> dict[str, Any]:
            self.claimed.append((task_id, dict(kwargs)))
            return {"success": True, "execution_attempt": identity.to_record()}

        def settle_execution_attempt(
            self,
            command: SettleTaskRuntimeExecutionAttemptCommandV1,
        ) -> dict[str, Any]:
            self.settled.append(command)
            return {"success": True, "code": "settled"}

    runtime = _Runtime()
    monkeypatch.setattr(stage_executor_module, "TaskRuntimeService", lambda workspace: runtime)

    result = _executor(tmp_path)._reconcile_verified_runtime_delivery(
        run=run,
        authority=_verified_delivery_recovery_authority(),
    )

    assert result == {"success": True, "reconciled_task_ids": ["TASK-3"]}
    assert runtime.reopened[0][0] == 3
    assert runtime.reopened[0][1] == "canonical_delivery_verified_after_terminal_director_attempt"
    assert runtime.claimed[0][1]["selection_source"] == "factory_verified_delivery_reconciliation"
    assert runtime.settled[0].identity == identity
    assert runtime.settled[0].outcome == "completed"
    evidence = dict(runtime.settled[0].metadata["verified_delivery_reconciliation"])
    assert evidence["task_boundary_completed_verified"] is True
    assert evidence["qa_verdict_passed"] is True
    assert evidence["sequence_barrier_satisfied"] is True
    assert evidence["evidence_policy_passed"] is True


def test_reconcile_verified_runtime_delivery_restores_frozen_owner_after_terminal_drain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from polaris.cells.factory.pipeline.public.contracts import (
        FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
        FactoryTerminalTaskRuntimeProjectionV1,
    )

    run = FactoryRun(
        id="factory-runtime-reconcile-drained",
        config=FactoryConfig(name="bench-run"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-08-10T00:00:00+00:00",
    )
    run.metadata[FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY] = FactoryTerminalTaskRuntimeProjectionV1(
        workspace=str(tmp_path),
        factory_run_id=run.id,
        captured_at="2026-08-10T00:00:00+00:00",
        projection={
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "workspace": str(tmp_path),
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "requested_factory_run_id": run.id,
            "row_count": 1,
            "total_row_count": 1,
            "rows": [
                {
                    "task_id": "10",
                    "external_task_id": "TASK-3",
                    "factory_run_id": run.id,
                    "execution_state": "failed",
                    "status": "failed",
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                    "fact_event_seq": 106,
                }
            ],
            "readiness": {},
        },
    ).to_dict()
    identity = TaskRuntimeExecutionAttemptIdentityV1(
        workspace=str(tmp_path),
        task_id=18,
        external_task_id="TASK-3",
        session_id="verified-delivery-reconcile-restored-session",
        attempt=1,
        role_id="qa",
        worker_id=f"factory-quality-gate:{run.id}",
        run_id=run.id,
        lease_expires_at="2026-08-10T00:02:00+00:00",
    )

    class _Runtime:
        def __init__(self) -> None:
            self.restored: dict[str, Any] | None = None
            self.reopened: list[int] = []
            self.settled: list[SettleTaskRuntimeExecutionAttemptCommandV1] = []

        def list_observable_task_rows(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": task_id,
                    "external_task_id": "TASK-3",
                    "status": "removed",
                    "metadata": {"factory_run_id": run.id, "source_task_id": "TASK-3"},
                }
                for task_id in (10, 15, 16, 17)
            ]

        def get_task(self, external_task_id: str) -> dict[str, Any] | None:
            assert external_task_id == "TASK-3"
            return self.restored

        def reopen_task_row(self, task_id: int, **kwargs: Any) -> dict[str, Any]:
            self.reopened.append(task_id)
            return {"id": task_id, "status": "pending"}

        def claim_execution(self, task_id: int, **kwargs: Any) -> dict[str, Any]:
            assert task_id == 18
            return {"success": True, "execution_attempt": identity.to_record()}

        def settle_execution_attempt(
            self,
            command: SettleTaskRuntimeExecutionAttemptCommandV1,
        ) -> dict[str, Any]:
            self.settled.append(command)
            return {"success": True, "code": "settled"}

    runtime = _Runtime()
    monkeypatch.setattr(stage_executor_module, "TaskRuntimeService", lambda workspace: runtime)
    executor = _executor(tmp_path)
    monkeypatch.setattr(
        executor,
        "_load_pm_plan_tasks",
        lambda path: [{"id": "TASK-3", "goal": "Add tests", "target_files": ["tests/test_product.py"]}],
    )

    materialized: list[tuple[list[dict[str, Any]], str, str]] = []

    def _materialize(
        tasks: list[dict[str, Any]],
        *,
        run_id: str,
        source_stage: str,
        run_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        materialized.append((tasks, run_id, source_stage))
        runtime.restored = {
            "id": 18,
            "external_task_id": "TASK-3",
            "status": "failed",
            "metadata": {"factory_run_id": run.id, "source_task_id": "TASK-3"},
        }
        return {"binding_failures": [], "task_ids": ["TASK-3"]}

    monkeypatch.setattr(executor, "_materialize_pm_plan_taskboard", _materialize)

    result = executor._reconcile_verified_runtime_delivery(
        run=run,
        authority=_verified_delivery_recovery_authority(),
    )

    assert result == {"success": True, "reconciled_task_ids": ["TASK-3"]}
    assert materialized == [
        ([{"id": "TASK-3", "goal": "Add tests", "target_files": ["tests/test_product.py"]}], run.id, "quality_gate")
    ]
    assert runtime.reopened == [18]
    assert runtime.settled[0].identity == identity


def test_reconcile_verified_runtime_delivery_fails_closed_without_quality_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _ForbiddenRuntime:
        def __init__(self, workspace: str) -> None:
            raise AssertionError(f"TaskRuntime must not mutate without quality authority: {workspace}")

    monkeypatch.setattr(stage_executor_module, "TaskRuntimeService", _ForbiddenRuntime)
    run = FactoryRun(
        id="factory-runtime-reconcile-denied",
        config=FactoryConfig(name="bench-run"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-08-10T00:00:00+00:00",
    )

    result = _executor(tmp_path)._reconcile_verified_runtime_delivery(
        run=run,
        authority=_verified_delivery_recovery_authority(quality_authorized=False),
    )

    assert result == {
        "success": False,
        "reason": "canonical_quality_authority_not_verified",
        "reconciled_task_ids": [],
    }
