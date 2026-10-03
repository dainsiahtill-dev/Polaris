"""Settle plans cannot promote proposed paths into write authority."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.chief_engineer.blueprint import public as ce_public
from polaris.cells.factory.pipeline.internal.factory_materialization_impl import (
    _director_stage_materialization_settle_commit_context,
)
from polaris.cells.factory.pipeline.internal.run_ledger import build_job_token_from_record


def _candidate(path: str) -> dict:
    return {
        "result": {
            "deferred_request": SimpleNamespace(
                plan=SimpleNamespace(effects=(SimpleNamespace(contingency_kind="forward", target_path=path),)),
                allowed_paths=(path,),
            )
        }
    }


def _executor(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(
        workspace=workspace,
        _director_stage_materialization_settle_target_files=lambda **_kwargs: ["package.json"],
        _workspace_quality_repair_blueprint_evidence=lambda **_kwargs: ("runtime/blueprints/ce-1.json", "present"),
    )


def test_missing_owner_cannot_mint_settle_capability(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="repair_owner_authority_required"):
        _director_stage_materialization_settle_commit_context(
            _executor(tmp_path),
            run=SimpleNamespace(config=SimpleNamespace(name="project-1")),
            run_id="factory-1",
            diagnostics=[],
            deferred_tool_results=[_candidate("tests/unowned.mjs")],
        )


def _validated_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, run_id: str = "factory-1", paths: list[str] | None = None
) -> tuple[dict, dict]:
    owned = paths if paths is not None else ["package.json"]
    token = build_job_token_from_record(
        {
            "target_files": ["package.json"],
            "allowed_paths": ["package.json"],
            "allowed_write_paths": ["package.json"],
            "allowed_read_paths": ["package.json"],
            "blueprint_id": "ce-1",
            "blueprints": [{"id": "ce-1"}],
            "chief_engineer": {"blueprint_id": "ce-1"},
            "contract_goal": "Implement project manifest",
        },
        run_id=run_id,
        project_id="project-1",
        stage="director_dispatch",
    ).to_dict()
    # The strict CE boundary is the input double here, not the token builder.
    # Represent its already validated original scope literally.
    token["allowed_write_paths"] = list(owned)
    token["allowed_read_paths"] = list(owned)
    projection = {
        "schema_version": "polaris.task_completion_projection.v1",
        "task_id": "TASK-1",
        "run_id": run_id,
        "project_id": "project-1",
        "project_contract_hash": "a" * 64,
        "projection_hash": "b" * 64,
        "owned_artifacts": [
            {"path": p, "owner_task_id": "TASK-1", "obligation_id": f"artifact-{i}"} for i, p in enumerate(owned)
        ],
    }
    owner = {
        "id": "TASK-1",
        "task_id": "TASK-1",
        "subject": "Implement manifest",
        "target_files": list(owned),
        "metadata": {"factory_run_id": run_id, "task_completion_projection": projection},
    }

    def strict_handoff(workspace: str, task: dict, *, require_strict: bool = False) -> dict:
        assert workspace == str(tmp_path)
        assert task["id"] == "TASK-1" and require_strict
        return {
            "allowed": True,
            "task_id": "TASK-1",
            "task_completion_projection": deepcopy(projection),
            "job_token": deepcopy(token),
            "capability_token": deepcopy(token),
        }

    monkeypatch.setattr(ce_public, "validate_director_handoff_from_payload", strict_handoff)
    return owner, token


def test_foreign_candidate_does_not_expand_original_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    owner, _ = _validated_owner(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="repair_candidate_outside_owner_scope"):
        _director_stage_materialization_settle_commit_context(
            _executor(tmp_path),
            run=SimpleNamespace(config=SimpleNamespace(name="project-1")),
            run_id="factory-1",
            diagnostics=[],
            repair_task=owner,
            deferred_tool_results=[_candidate("tests/unowned.mjs")],
        )


def test_valid_repair_preserves_original_token_and_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    owner, token = _validated_owner(tmp_path, monkeypatch)
    original = deepcopy(owner)
    context = _director_stage_materialization_settle_commit_context(
        _executor(tmp_path),
        run=SimpleNamespace(config=SimpleNamespace(name="project-1")),
        run_id="factory-1",
        diagnostics=[],
        repair_task=owner,
        deferred_tool_results=[_candidate("package.json")],
    )
    assert context["job_token"] == token
    assert context["capability_token"] == token
    assert context["allowed_write_paths"] == ["package.json"]
    assert context["task_completion_projection"]["task_id"] == "TASK-1"
    assert context["task_completion_projection"]["run_id"] == "factory-1"
    assert owner == original


@pytest.mark.parametrize("path", ["../outside.py", "/etc/passwd", ".polaris/runtime/ledger.json", "bad\npath.py"])
def test_unsafe_plan_path_is_rejected_not_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    owner, _ = _validated_owner(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="repair_candidate_path_invalid"):
        _director_stage_materialization_settle_commit_context(
            _executor(tmp_path),
            run=SimpleNamespace(config=SimpleNamespace(name="project-1")),
            run_id="factory-1",
            diagnostics=[],
            repair_task=owner,
            deferred_tool_results=[_candidate(path)],
        )


@pytest.mark.parametrize("denial", ["paths", "envelope", "root_paths", "root_envelope"])
def test_original_explicit_denial_is_not_replaced_by_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denial: str
) -> None:
    owner, _ = _validated_owner(tmp_path, monkeypatch)
    source = owner if denial.startswith("root_") else owner["metadata"]
    if denial.endswith("paths"):
        source["allowed_write_paths"] = []
    else:
        source["director_execution_envelope"] = {
            "authorization": {"allowed_write_paths": [], "allowed_read_paths": ["package.json"], "allowed_commands": []}
        }
    with pytest.raises(ValueError, match="repair_owner_scope_binding_conflict"):
        _director_stage_materialization_settle_commit_context(
            _executor(tmp_path),
            run=SimpleNamespace(config=SimpleNamespace(name="project-1")),
            run_id="factory-1",
            diagnostics=[],
            repair_task=owner,
            deferred_tool_results=[_candidate("package.json")],
        )


@pytest.mark.parametrize("mismatch", ["run", "task", "rejected"])
def test_wrong_handoff_rejects_before_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str) -> None:
    owner, token = _validated_owner(tmp_path, monkeypatch)
    projection = deepcopy(owner["metadata"]["task_completion_projection"])
    projection["run_id" if mismatch == "run" else "task_id"] = "foreign"
    monkeypatch.setattr(
        ce_public,
        "validate_director_handoff_from_payload",
        lambda *_a, **_kw: {
            "allowed": mismatch != "rejected",
            "task_completion_projection": projection,
            "job_token": token,
            "capability_token": token,
        },
    )
    with pytest.raises(ValueError, match="repair_owner_authority_invalid"):
        _director_stage_materialization_settle_commit_context(
            _executor(tmp_path),
            run=SimpleNamespace(config=SimpleNamespace(name="project-1")),
            run_id="factory-1",
            diagnostics=[],
            repair_task=owner,
            deferred_tool_results=[_candidate("package.json")],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("record_fails", [False, True])
@pytest.mark.parametrize("lease_rejected", [False, True])
@pytest.mark.parametrize("late_scope_failure", [False, True])
async def test_stage_settle_reuses_owner_and_records_assets_before_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_fails: bool, lease_rejected: bool, late_scope_failure: bool
) -> None:
    from polaris.cells.factory.pipeline.internal import (
        factory_materialization_impl as stage,
        factory_workspace_quality_impl as quality,
    )
    from polaris.cells.roles.adapters import public as adapters
    from polaris.cells.runtime.task_runtime import public as runtime

    owner, _ = _validated_owner(tmp_path, monkeypatch)
    (tmp_path / "package.json").write_text('{"name":"test"}\n', encoding="utf-8")
    attempt = SimpleNamespace(workspace=str(tmp_path), task_id=1, external_task_id="TASK-1")
    order: list[str] = []
    scans = iter(
        [
            ["package.json: existing owner repair needed"],
            ["package.json: residual after first repair"] if late_scope_failure else [],
        ]
    )
    executor = _executor(tmp_path)
    executor._director_stage_should_run_materialization_quality_settle = lambda **_k: True
    executor._collect_director_stage_materialization_diagnostics = lambda: next(scans)
    executor._workspace_quality_repair_diagnostic_target_files = lambda _d: ["package.json"]
    executor._workspace_quality_repair_target_files = lambda: ["package.json"]

    def claim(**_kwargs: object) -> tuple:
        order.append("original_owner_claim")
        return "TASK-1", 1, attempt, owner

    executor._claim_workspace_quality_repair_attempt = claim

    def forbidden_helper(**_kwargs: object) -> tuple:
        raise AssertionError("synthetic_settle_owner_forbidden")

    executor._claim_director_stage_materialization_settle_attempt = forbidden_helper

    def plan(**kwargs: object) -> tuple:
        assert kwargs["repair_task"] == owner and kwargs["task_id"] == "TASK-1"
        order.append("owner_plan")
        if lease_rejected:
            import time

            time.sleep(0.03)  # Slow synchronous planner; renewal runs independently.
        candidates = [_candidate("package.json")]
        if late_scope_failure:
            candidates.append(_candidate("tests/unowned.mjs"))
        return candidates, {"ok": True}

    executor._apply_workspace_quality_repairs = plan
    executor._director_stage_materialization_settle_commit_context = lambda **k: (
        stage._director_stage_materialization_settle_commit_context(executor, **k)
    )
    executor._director_stage_materialization_receipt_succeeded = lambda r: r.get("success") is True
    executor._workspace_quality_repair_result_has_mutation = lambda r: False
    executor._ensure_director_stage_materialization_typescript_toolchain = lambda: None

    def close(**kwargs: object) -> dict:
        order.append("close:" + str(kwargs["stage_status"]))
        return {"success": True}

    executor._settle_director_stage_materialization_attempt = close

    async def commit(**kwargs: object) -> list:
        assert kwargs["context"]["task_completion_projection"]["task_id"] == "TASK-1"
        order.append("physical_effect")
        return [{"tool": "write_file", "success": True, "result": {"file": "package.json"}}]

    def record(pending: dict) -> tuple:
        assert pending["task_id"] == "TASK-1" and pending["execution_attempt"] is attempt
        order.append("owner_asset_record")
        if record_fails:
            raise RuntimeError("asset_owner_unavailable")
        return ({"path": "package.json"},)

    monkeypatch.setattr(adapters, "commit_materialization_deferred_repairs", commit)
    authority = SimpleNamespace(heartbeat=lambda **_k: SimpleNamespace(success=not lease_rejected, code="lease_lost"))
    monkeypatch.setattr(runtime, "create_task_runtime_execution_attempt_authority", lambda _a: authority)
    if lease_rejected:
        monkeypatch.setattr(quality, "_WORKSPACE_QUALITY_REPAIR_HEARTBEAT_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(quality, "_workspace_quality_causal_repair_target_files", lambda *_a, **_k: ["package.json"])
    monkeypatch.setattr(quality, "_record_workspace_quality_repair_artifact_receipts", record)
    result = await stage._run_director_stage_materialization_quality_settle(
        executor,
        run=SimpleNamespace(id="factory-1", config=SimpleNamespace(name="project-1")),
        stage_status="failed",
        error_code="director.canonical_task_boundary_missing",
    )
    if lease_rejected:
        assert result["ok"] is False
        assert order == ["original_owner_claim", "owner_plan", "close:failed"]
        assert "heartbeat" in result["detail"]
        return
    if late_scope_failure and not record_fails:
        assert result["ok"] is False
        assert order == ["original_owner_claim", "owner_plan", "physical_effect", "owner_asset_record", "close:failed"]
        assert result["committed_receipt_count"] == 1
        assert result["project_artifact_receipt_count"] == 1
        return
    assert result["ok"] is not record_fails
    assert order == [
        "original_owner_claim",
        "owner_plan",
        "physical_effect",
        "owner_asset_record",
        "close:failed" if record_fails else "close:success",
    ]


@pytest.mark.asyncio
async def test_cancelled_settle_does_not_leave_owner_heartbeat_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from polaris.cells.factory.pipeline.internal import (
        factory_materialization_impl as stage,
        factory_workspace_quality_impl as quality,
    )
    from polaris.cells.runtime.task_runtime import public as runtime

    owner, _ = _validated_owner(tmp_path, monkeypatch)
    executor = _executor(tmp_path)
    executor._director_stage_should_run_materialization_quality_settle = lambda **_k: True
    executor._collect_director_stage_materialization_diagnostics = lambda: ["package.json: repair"]
    executor._workspace_quality_repair_diagnostic_target_files = lambda _d: ["package.json"]
    executor._claim_workspace_quality_repair_attempt = lambda **_k: (
        "TASK-1",
        1,
        SimpleNamespace(workspace=str(tmp_path)),
        owner,
    )
    executor._director_stage_materialization_settle_commit_context = lambda **k: (
        stage._director_stage_materialization_settle_commit_context(executor, **k)
    )

    def cancelled_plan(**_k: object) -> tuple:
        raise asyncio.CancelledError

    executor._apply_workspace_quality_repairs = cancelled_plan
    monkeypatch.setattr(quality, "_workspace_quality_causal_repair_target_files", lambda *_a, **_k: ["package.json"])
    monkeypatch.setattr(runtime, "create_task_runtime_execution_attempt_authority", lambda _a: object())
    with pytest.raises(asyncio.CancelledError):
        await stage._run_director_stage_materialization_quality_settle(
            executor,
            run=SimpleNamespace(id="factory-1"),
            stage_status="failed",
            error_code="director.dispatch_timeout",
        )
    keepers = [t for t in asyncio.all_tasks() if t.get_coro().__name__ == "_run_workspace_quality_repair_heartbeat"]
    try:
        assert keepers == []
    finally:
        for task in keepers:
            task.cancel()
        await asyncio.gather(*keepers, return_exceptions=True)
