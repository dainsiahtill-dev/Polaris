"""Incomplete owned closure must not hide a later real committed repair receipt."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.bootstrap.project_completion_diagnostics_owner import (
    PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER,
    configure_project_completion_diagnostics_owner,
)
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    build_chief_engineer_blueprint_portfolio,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import (
    _library_completion_requirements,
    _portfolio_command_authority,
)
from polaris.cells.factory.pipeline.internal import (
    factory_materialization_impl as materialization,
    factory_workspace_quality_impl as quality,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection, _real_effect
from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import _setup_attempt
from polaris.cells.runtime.execution_broker.public import (
    ProjectArtifactExecutionAuthorityV1,
    QueryProjectArtifactReceiptV1,
    RecordProjectArtifactCommandV1,
    query_project_artifact_receipt,
    record_project_artifact,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

_MISSING = "src/missing/cli.py"
_REPAIRED = "src/a.py"


@pytest.fixture
def partial_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Only fixture authority composition is replaced, never physical effects/receipts."""
    paths = (_MISSING, _REPAIRED, "tests/test_a.py")
    tasks = (ChiefEngineerPortfolioTaskV1(task_id="DEO-LIFECYCLE", objective="Author library", target_files=paths),)
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run",
            tasks=tasks,
            **_portfolio_command_authority(tasks=tasks, project_kind="library", workspace=tmp_path, run_id="run"),
            llm_blueprint={
                "construction_plan": {"project_interface_contract": {}},
                "project_completion_contract": _library_completion_requirements(
                    _MISSING,
                    _REPAIRED,
                    owner_task_ids=("DEO-LIFECYCLE", "DEO-LIFECYCLE"),
                    test_path=paths[2],
                    test_owner_task_id="DEO-LIFECYCLE",
                ),
                "risk_flags": [],
            },
        )
    )
    contract = portfolio.project_completion_contract
    assert contract is not None

    def authority(query: Any) -> ProjectArtifactExecutionAuthorityV1:
        artifact = next(item for item in contract.obligations.artifacts if item.obligation_id == query.obligation_id)
        assert artifact.owner_task_id is not None
        return ProjectArtifactExecutionAuthorityV1(
            workspace=query.workspace,
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
            obligation_id=artifact.obligation_id,
            owner_task_id=artifact.owner_task_id,
            path=artifact.path,
            job_token_id="fixture-artifact-owner",
            job_token_set_hash="c" * 64,
            execution_policy_hash="d" * 64,
            authority_revision="e" * 64,
        )

    configure_project_completion_diagnostics_owner()
    monkeypatch.setattr(PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER, "resolve_project_artifact_authority", authority)
    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    identities: dict[str, dict[str, str]] = {}
    for artifact in contract.obligations.artifacts:
        assert artifact.owner_task_id is not None
        identity: dict[str, str] = {
            "workspace": str(tmp_path),
            "project_id": contract.project_id,
            "run_id": contract.run_id,
            "completion_contract_hash": contract.contract_hash,
            "obligation_id": artifact.obligation_id,
            "owner_task_id": artifact.owner_task_id,
            "path": artifact.path,
        }
        identities[artifact.path] = identity
        if artifact.path != _MISSING:
            fs.workspace_write_text(artifact.path, "before\n", encoding="utf-8")
            record_project_artifact(RecordProjectArtifactCommandV1(**identity))
    projection = _projection(portfolio, contract)
    assert projection["owned_artifacts"][0]["path"] == _MISSING
    return SimpleNamespace(root=tmp_path, projection=projection, identities=identities, fs=fs)


def _stored_artifacts(root: Path) -> list[dict[str, Any]]:
    database = root / ".polaris/runtime/evidence/project_verification_receipts.sqlite3"
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT event_json FROM project_verification_receipt_events ORDER BY sequence")
        return [payload for (row,) in rows if isinstance(payload := json.loads(row).get("receipt_payload"), dict)]
    finally:
        connection.close()


def _pending(owner: SimpleNamespace, attempt: Any) -> dict[str, Any]:
    return {
        "task_id": "DEO-LIFECYCLE",
        "task_row_id": attempt.task_id,
        "execution_attempt": attempt,
        "task_completion_projection": owner.projection,
    }


@pytest.mark.asyncio
async def test_missing_earlier_obligation_still_refreshes_later_real_effect(partial_owner: SimpleNamespace) -> None:
    # Break caught: fail-fast registration leaves committed repair bytes with the old receipt.
    owner = partial_owner
    old = query_project_artifact_receipt(QueryProjectArtifactReceiptV1(**owner.identities[_REPAIRED]))
    assert old is not None and old.artifact_hash == hashlib.sha256(b"before\n").hexdigest()
    attempt, effects = await _real_effect(owner.root)
    assert effects[0]["effect_receipt"]["receipt_outcome"] == "succeeded"
    assert query_project_artifact_receipt(QueryProjectArtifactReceiptV1(**owner.identities[_REPAIRED])) is None
    with pytest.raises((OSError, RuntimeError)) as caught:
        quality._record_workspace_quality_repair_artifact_receipts(_pending(owner, attempt))
    current = query_project_artifact_receipt(QueryProjectArtifactReceiptV1(**owner.identities[_REPAIRED]))
    assert current is not None, "later committed effect was not registered after missing owned artifact"
    assert current.artifact_hash == hashlib.sha256(b"after\n").hexdigest()
    assert current.receipt_hash != old.receipt_hash
    assert not (owner.root / _MISSING).exists()
    assert query_project_artifact_receipt(QueryProjectArtifactReceiptV1(**owner.identities[_MISSING])) is None
    recorded = getattr(caught.value, "registered_receipts", ())
    missing = getattr(caught.value, "missing_obligations", ())
    assert {item["path"] for item in recorded} == {_REPAIRED, "tests/test_a.py"}
    assert [item["path"] for item in missing] == [_MISSING]
    stored = _stored_artifacts(owner.root)
    assert any(row["path"] == _REPAIRED and row["artifact_hash"] == current.artifact_hash for row in stored)
    assert all(row["path"] != _MISSING for row in stored)


@pytest.mark.asyncio
async def test_materialization_partial_closure_summary_and_real_task_remain_failed(
    partial_owner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Planner/selection doubles isolate this boundary; DEO, broker and failed TaskRuntime close are real.
    from polaris.cells.roles import adapters

    owner = partial_owner
    attempt = _setup_attempt(str(owner.root))
    candidate = {"result": {"status": "deferred_repair_effects_pending"}}
    executor = SimpleNamespace(
        workspace=owner.root,
        _director_stage_should_run_materialization_quality_settle=lambda **_kwargs: True,
        _collect_director_stage_materialization_diagnostics=lambda: ["missing required CLI"],
        _workspace_quality_repair_diagnostic_target_files=lambda _errors: [_REPAIRED],
        _claim_workspace_quality_repair_attempt=lambda **_kwargs: (
            "DEO-LIFECYCLE",
            int(attempt.task_id),
            attempt,
            {"task_completion_projection": owner.projection},
        ),
        _director_stage_materialization_settle_commit_context=lambda **_kwargs: {},
        _apply_workspace_quality_repairs=lambda **_kwargs: ([candidate], {}),
        _director_stage_materialization_receipt_succeeded=lambda row: (
            row.get("effect_receipt", {}).get("receipt_outcome") == "succeeded"
        ),
        _materialization_settle_attempt_outcome=lambda status: "completed" if status == "success" else "failed",
    )
    executor._settle_director_stage_materialization_attempt = lambda **kwargs: (
        materialization._settle_director_stage_materialization_attempt(executor, **kwargs)
    )

    async def commit(**_kwargs: Any) -> list[dict[str, Any]]:
        _same_attempt, effects = await _real_effect(owner.root, attempt=attempt)
        return effects

    monkeypatch.setattr(adapters.public, "commit_materialization_deferred_repairs", commit)
    monkeypatch.setattr(quality, "_workspace_quality_causal_repair_target_files", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(materialization, "_director_stage_deferred_repair_owner_targets", lambda *_args, **_kwargs: {})
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor,
        run=FactoryRun(
            id="run",
            config=FactoryConfig(name="partial-artifact-receipt-refresh"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-10-04T00:00:00+00:00",
        ),
        stage_status="failed",
        error_code="director_missing_write_receipt",
    )
    assert result["ok"] is False
    assert result["reason"] == "artifact_closure_incomplete"
    assert result["project_artifact_receipt_count"] == 2
    assert {row["path"] for row in result["project_artifact_receipts"]} == {_REPAIRED, "tests/test_a.py"}
    assert [row["path"] for row in result["missing_required_artifacts"]] == [_MISSING]
    task = next(
        row
        for row in TaskRuntimeService(str(owner.root)).list_task_rows(include_terminal=True)
        if str(row["id"]) == str(attempt.task_id)
    )
    assert task["status"] == "failed"
    assert not (owner.root / _MISSING).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["permission", "foreign_storage", "anonymous_missing", "hash_drift"])
async def test_unrelated_registration_errors_are_never_missing_artifact_continuations(
    partial_owner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, rejection: str
) -> None:
    owner = partial_owner
    attempt, _effects = await _real_effect(owner.root)
    before = _stored_artifacts(owner.root)
    errors: dict[str, Exception] = {
        "permission": PermissionError(13, "denied", str(owner.root / _MISSING)),
        "foreign_storage": FileNotFoundError(2, "missing authority store", str(owner.root / ".polaris/missing.db")),
        "anonymous_missing": FileNotFoundError("authority namespace missing"),
        "hash_drift": ValueError("current committed artifact hash drift"),
    }
    error = errors[rejection]

    def reject(_command: Any) -> Any:
        raise error

    monkeypatch.setattr(quality, "record_project_artifact", reject)
    with pytest.raises(type(error)) as caught:
        quality._record_workspace_quality_repair_artifact_receipts(_pending(owner, attempt))
    assert caught.value is error
    assert _stored_artifacts(owner.root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["foreign_owner", "foreign_path", "symlink"])
async def test_partial_registration_never_admits_unowned_or_symlink_artifacts(
    partial_owner: SimpleNamespace, attack: str
) -> None:
    owner = partial_owner
    attempt, _effects = await _real_effect(owner.root)
    pending = _pending(owner, attempt)
    projection = deepcopy(owner.projection)
    first = projection["owned_artifacts"][0]
    if attack == "foreign_owner":
        first["owner_task_id"] = "FOREIGN"
    elif attack == "foreign_path":
        first["path"] = "../outside.py"
    else:
        (owner.root / "src/missing").symlink_to(owner.root / "src")
    pending["task_completion_projection"] = projection
    before = _stored_artifacts(owner.root)
    with pytest.raises((OSError, ValueError)):
        quality._record_workspace_quality_repair_artifact_receipts(pending)
    assert _stored_artifacts(owner.root) == before
