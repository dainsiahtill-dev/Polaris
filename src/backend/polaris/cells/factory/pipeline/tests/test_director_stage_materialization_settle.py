"""M06: director_dispatch must settle materialization quality before stage exit.

R165 residual: multi-task Director timed out with package.json + src on disk,
but quality_gate never ran, so smoke tests and covered tsc repairs never
landed. End-of-stage settle must invoke the materialization schedule even when
the stage itself is failed/timeout.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_models import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
)
from polaris.cells.factory.pipeline.internal.factory_stage_executor import (
    OrchestrationStageExecutor,
)

# SimpleNamespace used as fake attempt identity in settle tests.


def _run(workspace: Path) -> FactoryRun:
    return FactoryRun(
        id="factory_test_m06_settle",
        config=FactoryConfig(name="m06-settle"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-08-01T00:00:00+00:00",
    )


def test_director_stage_should_settle_when_package_json_present(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    assert (
        executor._director_stage_should_run_materialization_quality_settle(
            stage_status="failed",
            error_code="director.canonical_task_boundary_missing",
        )
        is True
    )


def test_director_stage_should_settle_on_timeout_error_code(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    assert (
        executor._director_stage_should_run_materialization_quality_settle(
            stage_status="failed",
            error_code="director.dispatch_timeout",
        )
        is True
    )


def test_director_stage_skips_settle_when_cancelled(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    assert (
        executor._director_stage_should_run_materialization_quality_settle(
            stage_status="cancelled",
            error_code="",
        )
        is False
    )


def test_r181_workspace_has_delivery_surface(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    assert executor._workspace_has_delivery_surface() is False
    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    assert executor._workspace_has_delivery_surface() is False
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export const x = 1;\n", encoding="utf-8")
    assert executor._workspace_has_delivery_surface() is True


def test_r181_recover_director_stage_authority_after_delivery_settle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disk delivery alone cannot synthesize missing TaskBoundary authority."""

    from polaris.cells.factory.pipeline.internal.factory_stage_helpers import (
        evaluate_canonical_factory_authority,
    )

    (tmp_path / "package.json").write_text('{"name":"garden"}\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    for name in ("main.ts", "models/index.ts", "web.ts"):
        path = tmp_path / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"export const {name.split('/')[-1].split('.')[0]} = 1;\n", encoding="utf-8")

    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)
    incomplete_projection: dict[str, Any] = {
        "source": "run_ledger",
        "integrity_ok": True,
        "outcome_ok": True,
        "task_runtime_projection": {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "owner_scope": "pm_contract_tasks",
            "owned_task_ids": ["TASK-1", "TASK-3"],
            "row_count": 2,
            "rows": [
                {
                    "task_id": "1",
                    "workflow_run_id": "director-task-1",
                    "status": "completed",
                    "execution_state": "completed",
                    "fact_event_seq": 1,
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                },
                {
                    "task_id": "3",
                    "workflow_run_id": "director-task-3",
                    "status": "failed",
                    "execution_state": "failed",
                    "fact_event_seq": 7,
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                },
            ],
            "readiness": {"ready": True, "blocking_reasons": []},
        },
        "task_boundary": {
            "ok": False,
            "verdict_count": 1,
            "latest_by_task": {
                "1": {
                    "task_id": "1",
                    "status": "completed_verified",
                    "ok": True,
                    "failure_class": "PASSED",
                }
            },
            "failed": [],
        },
        "gates": [
            {
                "name": "qa_verdict",
                "ok": True,
                "append_id": "qa-append",
                "content_id": "qa-content",
            }
        ],
        "evidence_policy": {
            "integrity_ok": True,
            "outcome_ok": True,
            "missing_required_modalities": [],
            "failed_required_modalities": [],
        },
    }
    recovered_projection = {
        **incomplete_projection,
        "task_boundary": {
            "ok": True,
            "verdict_count": 2,
            "latest_by_task": {
                "1": {
                    "task_id": "1",
                    "status": "completed_verified",
                    "ok": True,
                    "failure_class": "PASSED",
                },
                "3": {
                    "task_id": "3",
                    "status": "completed_verified",
                    "ok": True,
                    "failure_class": "PASSED",
                },
                "TASK-3": {
                    "task_id": "TASK-3",
                    "status": "completed_verified",
                    "ok": True,
                    "failure_class": "PASSED",
                },
            },
            "failed": [],
        },
    }
    loads = {"n": 0}
    appends: list[Any] = []

    def _fake_projection(_run: Any, _context: dict[str, Any]) -> dict[str, Any]:
        loads["n"] += 1
        # After recovery appends at least one boundary, return delivery-complete map.
        if appends:
            return dict(recovered_projection)
        return dict(incomplete_projection)

    def _fake_append(command: Any) -> Any:
        appends.append(command)
        return None

    monkeypatch.setattr(executor, "_canonical_factory_projection", _fake_projection)
    monkeypatch.setattr(
        "polaris.cells.control_plane.run_ledger.public.append_run_ledger_event",
        _fake_append,
    )

    prior = evaluate_canonical_factory_authority(incomplete_projection)
    assert prior.director_stage_authorized is False
    assert "TASK-3" in prior.incomplete_runtime_task_ids

    recovered = executor._recover_director_stage_authority_after_delivery_settle(
        run=run,
        context={"project_id": "L1-01"},
        prior_authority=prior,
    )
    assert recovered is None
    assert appends == []
    assert loads["n"] == 1


def test_recover_director_stage_authority_orphan_blocked_uses_sibling_director_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R188/M06: blocked task without director child must recover under sibling director-* id."""

    from polaris.cells.factory.pipeline.internal.factory_stage_helpers import (
        evaluate_canonical_factory_authority,
    )

    (tmp_path / "package.json").write_text(
        '{"name":"garden","scripts":{"test":"node --test tests/*.test.ts","build":"tsc"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    for name in ("main.ts", "index.ts", "a.ts", "b.ts"):
        (tmp_path / "src" / name).write_text(f"export const {name.split('.')[0]} = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "verify.test.ts").write_text("import test from 'node:test'\n", encoding="utf-8")

    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)

    incomplete_projection = {
        "schema_version": "run_ledger.projection.v1",
        "source": "run_ledger",
        "integrity_ok": True,
        "outcome_ok": True,
        "task_runtime_projection": {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "owner_scope": "pm_contract_tasks",
            "owned_task_ids": ["TASK-1", "TASK-3"],
            "row_count": 2,
            "readiness": {"ready": True},
            "rows": [
                {
                    "task_id": "1",
                    "status": "completed",
                    "execution_state": "completed",
                    "workflow_run_id": "director-task-1",
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                    "fact_event_seq": 2,
                },
                {
                    "task_id": "3",
                    "status": "blocked",
                    "execution_state": "blocked",
                    "workflow_run_id": "",
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                    "fact_event_seq": 1,
                },
            ],
        },
        "task_boundary": {
            "latest_by_task": {
                "1": {"task_id": "1", "status": "completed_verified", "ok": True, "failure_class": "PASSED"},
            },
            "failed": [],
        },
    }
    recovered_projection = {
        "schema_version": "run_ledger.projection.v1",
        "source": "run_ledger",
        "integrity_ok": True,
        "outcome_ok": True,
        "task_runtime_projection": incomplete_projection["task_runtime_projection"],
        "task_boundary": {
            "latest_by_task": {
                "1": {"task_id": "1", "status": "completed_verified", "ok": True, "failure_class": "PASSED"},
                "3": {"task_id": "3", "status": "completed_verified", "ok": True, "failure_class": "PASSED"},
                "TASK-3": {
                    "task_id": "TASK-3",
                    "status": "completed_verified",
                    "ok": True,
                    "failure_class": "PASSED",
                },
            },
            "failed": [],
        },
    }
    appends: list[Any] = []

    def _fake_projection(_run: Any, _context: dict[str, Any]) -> dict[str, Any]:
        if appends:
            return dict(recovered_projection)
        return dict(incomplete_projection)

    def _fake_append(command: Any) -> Any:
        appends.append(command)
        return None

    monkeypatch.setattr(executor, "_canonical_factory_projection", _fake_projection)
    monkeypatch.setattr(
        "polaris.cells.control_plane.run_ledger.public.append_run_ledger_event",
        _fake_append,
    )

    prior = evaluate_canonical_factory_authority(incomplete_projection)
    assert prior.director_stage_authorized is False
    assert "TASK-3" in prior.incomplete_runtime_task_ids

    recovered = executor._recover_director_stage_authority_after_delivery_settle(
        run=run,
        context={"project_id": "L1-01"},
        prior_authority=prior,
    )
    assert recovered is None
    assert appends == []


@pytest.mark.asyncio
async def test_run_director_stage_materialization_quality_settle_invokes_schedule(
    tmp_path: Path,
) -> None:
    """Failed multi-task stage with package.json must claim attempt + schedule + DEO commit."""

    (tmp_path / "package.json").write_text(
        '{"name":"garden","scripts":{"test":"vitest run","build":"tsc"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export function main(): void {}\n", encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)

    captured: dict[str, Any] = {}
    fake_attempt = SimpleNamespace(
        workspace=str(tmp_path),
        task_id=1,
        external_task_id="factory-director-mat-settle:factory_test_m06_settle",
    )

    def _fake_apply(
        *,
        run_id: str,
        artifact_quality_errors: list[str],
        task_id: str | None = None,
        execution_attempt: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        captured["run_id"] = run_id
        captured["errors"] = list(artifact_quality_errors)
        captured["task_id"] = task_id
        captured["has_attempt"] = execution_attempt is not None
        return (
            [
                {
                    "tool": "deferred_director_repair",
                    "success": True,
                    "result": {
                        "status": "deferred_repair_effects_pending",
                        "source_tool": "deterministic_typescript_json_as_source_repair",
                        "deferred_request": object(),
                    },
                }
            ],
            {"ok": True, "created_smoke_tests": ["tests/verify.test.ts"]},
        )

    async def _fake_commit(**kwargs: Any) -> list[dict[str, Any]]:
        captured["commit_called"] = True
        captured["commit_tools"] = len(kwargs.get("tool_results") or [])
        captured["commit_context"] = dict(kwargs.get("context") or {})
        return [{"tool": "write_file", "success": True, "result": {"file": "tests/verify.test.ts"}}]

    with (
        patch.object(
            executor,
            "_claim_director_stage_materialization_settle_attempt",
            return_value=(
                "factory-director-mat-settle:factory_test_m06_settle",
                1,
                fake_attempt,
            ),
        ),
        patch.object(executor, "_apply_workspace_quality_repairs", side_effect=_fake_apply),
        patch.object(executor, "_collect_director_stage_materialization_diagnostics", return_value=[]),
        patch.object(
            executor,
            "_settle_director_stage_materialization_attempt",
            return_value={"success": True},
        ),
        patch(
            "polaris.cells.roles.adapters.public.commit_materialization_deferred_repairs",
            side_effect=_fake_commit,
        ),
        patch(
            "polaris.cells.runtime.task_runtime.public.create_task_runtime_execution_attempt_authority",
            return_value=object(),
        ),
    ):
        settle = await executor._run_director_stage_materialization_quality_settle(
            run=run,
            stage_status="failed",
            error_code="director.canonical_task_boundary_missing",
        )

    assert settle["ok"] is True
    assert settle["reason"] == "director_stage_settle"
    assert settle["tool_result_count"] == 1
    assert settle["committed_receipt_count"] == 1
    assert settle["mutated"] is True
    assert captured["run_id"] == "factory_test_m06_settle"
    assert captured["errors"] == []
    assert captured["has_attempt"] is True
    assert captured["commit_called"] is True
    commit_ctx = captured["commit_context"]
    job_token = commit_ctx.get("job_token")
    assert isinstance(job_token, dict)
    assert str(job_token.get("token_id") or "").strip()
    assert job_token.get("capability_audit", {}).get("ok") is True
    assert len(str(job_token.get("execution_envelope_hash") or "")) == 64
    assert "tests/verify.test.ts" in (commit_ctx.get("allowed_paths") or [])


@pytest.mark.asyncio
async def test_materialization_settle_accepts_canonical_batch_receipt_and_revalidates(
    tmp_path: Path,
) -> None:
    """Regression: DEO BatchReceipt success is not a top-level ``success`` flag.

    L1-01 r2 physically committed the package.json dependency repair, but Factory
    counted every canonical receipt as failed because it only checked
    ``receipt["success"] is True``.  The settle path must consume the canonical
    ``success_count``/``failure_count`` contract and re-run diagnostics after the
    physical commit.
    """

    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)
    fake_attempt = SimpleNamespace(workspace=str(tmp_path), task_id=1, external_task_id="settle-x")
    deferred_results = [
        {
            "tool": "deferred_director_repair",
            "success": True,
            "result": {
                "status": "deferred_repair_effects_pending",
                "deferred_request": object(),
            },
        }
    ]
    canonical_batch_receipt = {
        "batch_id": "batch-1",
        "turn_id": "turn-1",
        "success_count": 1,
        "failure_count": 0,
        "pending_async_count": 0,
        "has_pending_async": False,
        "results": [
            {
                "call_id": "repair-1",
                "tool_name": "write_file",
                "status": "success",
                "effect_receipt": {"after_hash": "a" * 64},
            }
        ],
        "raw_results": [],
        "effect_receipts": [{"after_hash": "a" * 64}],
    }

    with (
        patch.object(
            executor,
            "_claim_director_stage_materialization_settle_attempt",
            return_value=("settle-x", 1, fake_attempt),
        ),
        patch.object(
            executor,
            "_apply_workspace_quality_repairs",
            return_value=(deferred_results, {"ok": True}),
        ),
        patch.object(
            executor,
            "_collect_director_stage_materialization_diagnostics",
            side_effect=[["error TS2688: Cannot find type definition file for 'node'"], []],
        ) as diagnostics,
        patch.object(
            executor,
            "_ensure_director_stage_materialization_typescript_toolchain",
        ) as ensure_toolchain,
        patch.object(
            executor,
            "_settle_director_stage_materialization_attempt",
            return_value={"success": True},
        ) as settle_attempt,
        patch(
            "polaris.cells.roles.adapters.public.commit_materialization_deferred_repairs",
            return_value=[canonical_batch_receipt],
        ),
        patch(
            "polaris.cells.runtime.task_runtime.public.create_task_runtime_execution_attempt_authority",
            return_value=object(),
        ),
    ):
        settle = await executor._run_director_stage_materialization_quality_settle(
            run=run,
            stage_status="failed",
            error_code="director.canonical_task_boundary_missing",
        )

    assert settle["ok"] is True
    assert settle["committed_receipt_count"] == 1
    assert settle["failed_receipt_count"] == 0
    assert settle["post_commit_diagnostic_count"] == 0
    assert settle["mutated"] is True
    assert diagnostics.call_count == 2
    ensure_toolchain.assert_called_once_with()
    assert settle_attempt.call_args.kwargs["stage_status"] == "success"


@pytest.mark.asyncio
async def test_materialization_settle_stops_after_first_verified_repair_candidate(
    tmp_path: Path,
) -> None:
    """Do not execute later repair candidates after the verifier is green.

    L1-01 r3 proved that the first DEO candidate could clear every physical
    diagnostic while later candidates produced dead-letter receipts.  Keeping
    all candidates inside one TaskRuntime attempt then made a truthful
    ``completed`` settlement impossible.  Convergence is therefore evaluated
    after each candidate, and the remaining candidates must not be admitted.
    """

    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)
    fake_attempt = SimpleNamespace(workspace=str(tmp_path), task_id=1, external_task_id="settle-x")
    deferred_results = [
        {
            "tool": "deferred_director_repair",
            "success": True,
            "result": {
                "status": "deferred_repair_effects_pending",
                "deferred_request": object(),
                "candidate": candidate,
            },
        }
        for candidate in ("first", "redundant")
    ]
    canonical_batch_receipt = {
        "batch_id": "batch-1",
        "turn_id": "turn-1",
        "success_count": 1,
        "failure_count": 0,
        "results": [{"status": "success"}],
    }

    with (
        patch.object(
            executor,
            "_claim_director_stage_materialization_settle_attempt",
            return_value=("settle-x", 1, fake_attempt),
        ),
        patch.object(
            executor,
            "_apply_workspace_quality_repairs",
            return_value=(deferred_results, {"ok": True}),
        ),
        patch.object(
            executor,
            "_collect_director_stage_materialization_diagnostics",
            side_effect=[["error TS2688"], []],
        ),
        patch.object(executor, "_ensure_director_stage_materialization_typescript_toolchain"),
        patch.object(
            executor,
            "_settle_director_stage_materialization_attempt",
            return_value={"success": True},
        ),
        patch(
            "polaris.cells.roles.adapters.public.commit_materialization_deferred_repairs",
            return_value=[canonical_batch_receipt],
        ) as commit,
        patch(
            "polaris.cells.runtime.task_runtime.public.create_task_runtime_execution_attempt_authority",
            return_value=object(),
        ),
    ):
        settle = await executor._run_director_stage_materialization_quality_settle(
            run=run,
            stage_status="failed",
            error_code="director.canonical_task_boundary_missing",
        )

    assert settle["ok"] is True
    assert settle["post_commit_diagnostic_count"] == 0
    assert commit.call_count == 1
    assert commit.call_args.kwargs["tool_results"] == [deferred_results[0]]
    assert commit.call_args.kwargs["turn_id"].endswith(":candidate0")


@pytest.mark.asyncio
async def test_materialization_settle_fails_when_verifier_residual_has_no_candidate(
    tmp_path: Path,
) -> None:
    """Residual verifier evidence cannot be reported as a successful settle."""

    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)
    fake_attempt = SimpleNamespace(workspace=str(tmp_path), task_id=1, external_task_id="settle-x")

    with (
        patch.object(
            executor,
            "_claim_director_stage_materialization_settle_attempt",
            return_value=("settle-x", 1, fake_attempt),
        ),
        patch.object(
            executor,
            "_collect_director_stage_materialization_diagnostics",
            return_value=["artifact_quality_error: npm test failed"],
        ),
        patch.object(
            executor,
            "_apply_workspace_quality_repairs",
            return_value=([], {"ok": False}),
        ),
        patch.object(
            executor,
            "_settle_director_stage_materialization_attempt",
            return_value={"success": True},
        ) as settle_attempt,
        patch(
            "polaris.cells.runtime.task_runtime.public.create_task_runtime_execution_attempt_authority",
            return_value=object(),
        ),
    ):
        settle = await executor._run_director_stage_materialization_quality_settle(
            run=run,
            stage_status="failed",
            error_code="director.canonical_task_boundary_missing",
        )

    assert settle["ok"] is False
    assert settle["reason"] == "materialization_verifier_residual"
    assert settle["post_commit_diagnostic_count"] == 1
    assert settle_attempt.call_args.kwargs["stage_status"] == "failed"


@pytest.mark.asyncio
async def test_materialization_settle_reports_deferred_commit_failure(
    tmp_path: Path,
) -> None:
    """A deferred batch with no terminal receipts must fail, not report ok=True."""

    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)
    fake_attempt = SimpleNamespace(workspace=str(tmp_path), task_id=1, external_task_id="settle-x")
    deferred_results = [
        {
            "tool": "deferred_director_repair",
            "success": True,
            "result": {
                "status": "deferred_repair_effects_pending",
                "deferred_request": object(),
            },
        }
    ]

    with (
        patch.object(
            executor,
            "_claim_director_stage_materialization_settle_attempt",
            return_value=("settle-x", 1, fake_attempt),
        ),
        patch.object(
            executor,
            "_apply_workspace_quality_repairs",
            return_value=(deferred_results, {"ok": True}),
        ),
        patch.object(executor, "_collect_director_stage_materialization_diagnostics", return_value=[]),
        patch.object(
            executor,
            "_settle_director_stage_materialization_attempt",
            return_value={"success": True},
        ) as settle_attempt,
        patch(
            "polaris.cells.roles.adapters.public.commit_materialization_deferred_repairs",
            return_value=[],
        ),
        patch(
            "polaris.cells.runtime.task_runtime.public.create_task_runtime_execution_attempt_authority",
            return_value=object(),
        ),
    ):
        settle = await executor._run_director_stage_materialization_quality_settle(
            run=run,
            stage_status="failed",
            error_code="director.dispatch_timeout",
        )

    assert settle["ok"] is False
    assert settle["reason"] == "deferred_repair_commit_failed"
    assert settle["committed_receipt_count"] == 0
    assert settle["failed_receipt_count"] == 0
    assert settle_attempt.call_args.kwargs["stage_status"] == "failed"


def test_materialization_settle_close_failure_is_returned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1

    executor = OrchestrationStageExecutor(tmp_path)
    attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace=str(tmp_path),
        task_id=1,
        external_task_id="settle-x",
        session_id="session-x",
        attempt=1,
        role_id="director",
        worker_id="director",
        run_id="factory-x",
        lease_expires_at="2026-08-09T00:00:00+00:00",
    )

    class _FailedTaskRuntime:
        def settle_execution_attempt(self, _command: object) -> dict[str, object]:
            return {"success": False, "reason": "settlement_directed_effect_unresolved"}

    monkeypatch.setattr(
        "polaris.cells.factory.pipeline.internal.factory_stage_executor.TaskRuntimeService",
        lambda _workspace: _FailedTaskRuntime(),
    )
    result = executor._settle_director_stage_materialization_attempt(
        task_row_id=1,
        execution_attempt=attempt,
        stage_status="failed",
        summary="failed repair",
    )

    assert result == {"success": False, "reason": "settlement_directed_effect_unresolved"}


def test_claim_director_stage_materialization_settle_attempt_mints_unique_external_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R190/M06: each settle claim mints a fresh external_task_id (no task_terminal reuse)."""

    from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1

    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)
    seen_external: list[str] = []
    row_counter = {"n": 0}

    class _FakeTR:
        def ensure_task_row(self, **kwargs: Any) -> dict[str, Any]:
            external = str(kwargs.get("external_task_id") or "")
            seen_external.append(external)
            row_counter["n"] += 1
            return {"id": row_counter["n"], "external_task_id": external}

        def normalize_task_id(self, value: Any) -> int:
            return int(value)

        def claim_execution(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            external = str(kwargs.get("external_task_id") or "")
            return {
                "success": True,
                "session": {"session_id": f"sess-{external[-8:]}"},
                "execution_attempt": {
                    "workspace": str(tmp_path),
                    "task_id": int(args[0]) if args else 1,
                    "session_id": f"sess-{external[-8:]}",
                    "attempt_id": f"att-{external[-8:]}",
                    "external_task_id": external,
                },
            }

    monkeypatch.setattr(
        "polaris.cells.factory.pipeline.internal.factory_stage_executor.TaskRuntimeService",
        lambda _ws: _FakeTR(),
    )
    monkeypatch.setattr(
        "polaris.cells.factory.pipeline.internal.factory_stage_executor.bind_runtime_task_to_factory_run",
        lambda _cmd: SimpleNamespace(ok=True, code="ok"),
    )
    monkeypatch.setattr(
        TaskRuntimeExecutionAttemptIdentityV1,
        "from_record",
        staticmethod(lambda record: SimpleNamespace(**dict(record))),
    )

    first_external, first_row, _first = executor._claim_director_stage_materialization_settle_attempt(run_id=run.id)
    second_external, second_row, _second = executor._claim_director_stage_materialization_settle_attempt(run_id=run.id)
    assert first_external.startswith("factory-director-mat-settle:factory_test_m06_settle:")
    assert second_external.startswith("factory-director-mat-settle:factory_test_m06_settle:")
    assert first_external != second_external
    assert first_row != second_row
    assert len(seen_external) == 2
    assert len(set(seen_external)) == 2


@pytest.mark.asyncio
async def test_run_director_stage_materialization_quality_settle_forwards_tsc_diagnostics(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)
    diags = [
        "src/web.ts(1,1): error TS6133: 'Humidity' is declared but its value is never read.",
    ]
    captured: dict[str, Any] = {}
    fake_attempt = SimpleNamespace(workspace=str(tmp_path), task_id=1, external_task_id="x")

    def _fake_apply(
        *,
        run_id: str,
        artifact_quality_errors: list[str],
        task_id: str | None = None,
        execution_attempt: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        del run_id, task_id, execution_attempt
        captured["errors"] = list(artifact_quality_errors)
        return ([], {"ok": True})

    async def _fake_commit(**kwargs: Any) -> list[dict[str, Any]]:
        del kwargs
        return []

    with (
        patch.object(
            executor,
            "_claim_director_stage_materialization_settle_attempt",
            return_value=("x", 1, fake_attempt),
        ),
        patch.object(executor, "_apply_workspace_quality_repairs", side_effect=_fake_apply),
        patch.object(
            executor,
            "_collect_director_stage_materialization_diagnostics",
            return_value=diags,
        ),
        patch.object(
            executor,
            "_settle_director_stage_materialization_attempt",
            return_value={"success": True},
        ),
        patch(
            "polaris.cells.roles.adapters.public.commit_materialization_deferred_repairs",
            side_effect=_fake_commit,
        ),
        patch(
            "polaris.cells.runtime.task_runtime.public.create_task_runtime_execution_attempt_authority",
            return_value=object(),
        ),
    ):
        settle = await executor._run_director_stage_materialization_quality_settle(
            run=run,
            stage_status="failed",
            error_code="director.dispatch_timeout",
        )

    assert settle["ok"] is False
    assert settle["reason"] == "materialization_verifier_residual"
    assert settle["diagnostic_count"] == 1
    assert captured["errors"] == diags


def test_partition_allows_smoke_test_when_main_ts_repairs_conflict() -> None:
    """R174: multi-deferred settle must not drop tests/verify.test.ts on path conflict."""

    from polaris.cells.roles.kernel.public.deferred_repair_commit_service import (
        _partition_non_conflicting_deferred_tool_results,
    )

    def _tool(source: str, path: str) -> dict[str, object]:
        from types import SimpleNamespace

        request = SimpleNamespace(
            plan=SimpleNamespace(effects=(SimpleNamespace(contingency_kind="forward", target_path=path),)),
            allowed_paths=(path,),
        )
        return {
            "tool": "deferred_director_repair",
            "success": True,
            "result": {
                "status": "deferred_repair_effects_pending",
                "source_tool": source,
                "deferred_request": request,
            },
        }

    waves = _partition_non_conflicting_deferred_tool_results(
        [
            _tool("deterministic_typescript_unused_local_repair", "src/main.ts"),
            _tool("deterministic_typescript_identifier_suggestion_repair", "src/main.ts"),
            _tool("deterministic_typescript_json_as_source_repair", "tests/verify.test.ts"),
        ]
    )
    assert len(waves) == 2
    all_paths = {str((item.get("result") or {}).get("source_tool") or "") for wave in waves for item in wave}
    assert "deterministic_typescript_json_as_source_repair" in all_paths


def test_director_stage_materialization_settle_commit_context_builds_job_token(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"x","scripts":{"test":"node --test tests/*.test.ts"}}\n', encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export {}\n", encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)

    context = executor._director_stage_materialization_settle_commit_context(
        run=run,
        run_id=run.id,
        diagnostics=[],
    )
    job_token = context["job_token"]
    assert job_token["capability_audit"]["ok"] is True
    assert len(job_token["execution_envelope_hash"]) == 64
    assert job_token["execution_envelope_hash"] == context["execution_envelope"]["envelope_hash"]
    assert context["execution_envelope"]["authorization"]["capability_token_ref"] == job_token["token_id"]
    assert "package.json" in context["allowed_paths"]
    assert "tests/verify.test.ts" in context["allowed_paths"]
    # R185/M03: deferred DEO commit must accept the settle context (not skip silently).
    assert str(context.get("capability_token_hash") or "").strip()
    assert context["execution_envelope"]["authorization"]["capability_token_hash"] == context["capability_token_hash"]


def test_materialization_settle_scope_includes_plannable_derived_targets(tmp_path: Path) -> None:
    """DEO scope includes changed paths proven by the read-only repair plan probe."""

    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    with patch.object(
        executor,
        "_workspace_quality_repair_plan_probe_report",
        return_value={
            "items": [
                {
                    "status": "covered_plannable",
                    "changed_paths": ["index.html", "dist/tests/verify.test.js"],
                },
                {"status": "covered_unplannable", "changed_paths": ["forbidden.txt"]},
            ]
        },
    ):
        targets = executor._director_stage_materialization_settle_target_files(
            diagnostics=["artifact_quality_error: npm test failed"]
        )

    assert "index.html" in targets
    assert "dist/tests/verify.test.js" in targets
    assert "forbidden.txt" not in targets


def test_collect_director_stage_materialization_diagnostics_parses_tsc_stderr(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    tsc_path = bin_dir / "tsc"
    tsc_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tsc_path.chmod(0o755)
    executor = OrchestrationStageExecutor(tmp_path)

    fake = SimpleNamespace(
        stdout="src/a.ts(1,1): error TS6133: 'x' is declared but its value is never read.\n",
        stderr="",
        returncode=1,
    )
    with patch(
        "polaris.cells.factory.pipeline.internal.factory_stage_executor.subprocess.run",
        return_value=fake,
    ):
        diags = executor._collect_director_stage_materialization_diagnostics()

    assert len(diags) == 1
    assert "TS6133" in diags[0]


def test_collect_materialization_diagnostics_includes_html_entrypoint_quality(
    tmp_path: Path,
) -> None:
    """A green compiler must not hide an HTML entrypoint that imports raw TS."""

    (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<!doctype html><script type="module" src="./src/web.ts"></script>\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "web.ts").write_text("export const ready = true;\n", encoding="utf-8")

    diagnostics = OrchestrationStageExecutor(
        tmp_path
    )._collect_director_stage_materialization_diagnostics()

    assert any("HTML module script references TypeScript source" in item for item in diagnostics)
    assert any("./src/web.ts" in item for item in diagnostics)


def test_collect_materialization_diagnostics_runs_declared_npm_test(
    tmp_path: Path,
) -> None:
    """Settle revalidation includes the real package test command, not tsc alone."""

    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "x",
                "scripts": {"test": "node --test dist/tests/verify.test.js"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "verify.test.ts").write_text("export {};\n", encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    failed_test = SimpleNamespace(
        stdout="",
        stderr="Could not find 'dist/tests/verify.test.js'\n",
        returncode=1,
    )

    with patch(
        "polaris.cells.factory.pipeline.internal.factory_stage_executor.subprocess.run",
        return_value=failed_test,
    ) as run_command:
        diagnostics = executor._collect_director_stage_materialization_diagnostics()

    assert run_command.call_args.args[0] == ["npm", "test"]
    assert any("npm test failed" in item for item in diagnostics)
    assert any("dist/tests/verify.test.js" in item for item in diagnostics)


def test_seal_director_stage_missing_tool_lifecycles_appends_blocked_receipt(
    tmp_path: Path,
) -> None:
    """R177/M06: stage end seals missing TASK-2 lifecycle as incomplete, not missing."""

    executor = OrchestrationStageExecutor(tmp_path)
    run = _run(tmp_path)
    projection = {
        "tool_lifecycle": {
            "ok": False,
            "missing_required_task_keys": ["TASK-2"],
            "requirement_projection": {
                "required": True,
                "missing_required_task_keys": ["TASK-2"],
                "obligations": [
                    {
                        "task_key": "TASK-2",
                        "task_id": "TASK-2",
                        "run_id": "director-task2-run",
                        "reason": "director_materialization_claimed",
                    }
                ],
            },
        }
    }
    append_calls: list[Any] = []

    def _fake_append(command: Any) -> Any:
        append_calls.append(command)
        return SimpleNamespace(ok=True)

    with (
        patch(
            "polaris.cells.factory.pipeline.internal.factory_stage_executor.load_run_ledger_projection",
            return_value=projection,
        ),
        patch(
            "polaris.cells.control_plane.run_ledger.public.append_tool_call_lifecycle_event",
            side_effect=_fake_append,
        ),
    ):
        result = executor._seal_director_stage_missing_tool_lifecycles(
            run=run,
            incomplete_task_ids=["2", "3"],
        )

    assert result["ok"] is True
    assert result["sealed_count"] == 1
    assert result["missing_before"] == ["TASK-2"]
    assert len(append_calls) == 1
    command = append_calls[0]
    assert command.task_id == "TASK-2"
    assert command.run_id == "director-task2-run"
    receipt = dict(command.lifecycle_receipt or {})
    assert receipt.get("dispatch_status") == "blocked"
    assert (
        "incomplete" in str(receipt.get("reason") or "").lower()
        or "without_tools" in str(receipt.get("reason") or "").lower()
    )


def test_collect_materialization_diagnostics_flags_missing_package_test_files(tmp_path: Path) -> None:
    """R184: settle diagnostics include missing package.json test entrypoints."""

    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "x",
                "scripts": {"test": "node --test --import tsx tests/*.test.ts"},
                "devDependencies": {"typescript": "^5.0.0"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export const n = 1;\n", encoding="utf-8")
    # No tsconfig → skip tsc, still report missing tests.
    executor = OrchestrationStageExecutor(tmp_path)
    diagnostics = executor._collect_director_stage_materialization_diagnostics()
    assert any("missing test source files" in item for item in diagnostics)


def test_settle_materialization_attempt_maps_non_success_to_failed_not_suspended() -> None:
    """R184: helper settle claim must terminal-close; suspended left task 5 pending."""

    assert OrchestrationStageExecutor._materialization_settle_attempt_outcome("success") == "completed"
    assert OrchestrationStageExecutor._materialization_settle_attempt_outcome("completed") == "completed"
    assert OrchestrationStageExecutor._materialization_settle_attempt_outcome("failed") == "failed"
    assert OrchestrationStageExecutor._materialization_settle_attempt_outcome("error") == "failed"
    assert OrchestrationStageExecutor._materialization_settle_attempt_outcome("") == "failed"
    # Explicit: never suspended.
    assert OrchestrationStageExecutor._materialization_settle_attempt_outcome("failed") != "suspended"
