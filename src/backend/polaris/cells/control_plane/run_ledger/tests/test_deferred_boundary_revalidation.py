"""Private synthetic module fixtures; no native project/model/acceptance command.

Real TaskRuntime claim/settlement and RunLedger producers persist every fact.
The normal verdict and receipt locators stand for Factory's already validated
owner observations; the Factory integration tests own real Broker validation.
The carrier is data, never an execution capability or a new evidence owner.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    ControlPlaneRunLedgerV1Error,
    ReadRunLedgerProjectionQueryV1,
    RunLedger,
    evaluate_task_boundary_verdict,
    read_run_ledger_projection,
    stable_hash,
)
from polaris.cells.control_plane.run_ledger.public.task_boundary import (
    ReprojectTaskBoundaryVerdictCommandV1,
    TaskBoundaryVerdictV1,
    reproject_task_boundary_verdict,
)
from polaris.cells.control_plane.run_ledger.tests.test_canonical_task_identity import _bootstrap
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)
from polaris.cells.roles.kernel.internal.kernel.task_boundary import (
    append_deferred_followup_task_boundary_verdict,
    append_director_task_boundary_verdict,
)
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
)

FACTORY = "factory-synthetic-revalidation"
CHILD = "director-synthetic-revalidation"
TASK = "SYNTHETIC-UNIT"
PROJECT = "synthetic-project"


def _facts(workspace: Path, stream: str) -> list[dict[str, Any]]:
    return list(
        query_fact_events(
            QueryFactEventsV1(workspace=str(workspace), stream=stream, limit=1000, strict_integrity=True)
        ).events
    )


def _fixture(
    workspace: Path,
    *,
    bad_projection_hash: bool = False,
    optional_verifier: bool = False,
    declared_modalities: bool = False,
) -> dict[str, Any]:
    _bootstrap(workspace)
    artifacts = []
    for index in range(13):
        relative = f"artifact-{index}.txt"
        (workspace / relative).write_text(f"private fixture {index}\n", encoding="utf-8")
        artifacts.append(
            {"obligation_id": f"A-{index}", "owner_task_id": TASK, "path": relative, "applicability": "required"}
        )
    verification = [
        {
            "obligation_id": f"V-{index}",
            "owner_task_id": TASK,
            "command": f"synthetic-verify-{index}",
            "modality": "test",
            "applicability": "required",
        }
        for index in range(5)
    ]
    projection: dict[str, Any] = {
        "schema_version": "polaris.task_completion_projection.v1",
        "project_id": PROJECT,
        "run_id": FACTORY,
        "task_id": TASK,
        "project_contract_hash": "a" * 64,
        "owned_artifacts": artifacts,
        "owned_entrypoints": [],
        "owned_verification": verification,
        "verification_execution_authority": [dict(row) for row in verification],
    }
    if optional_verifier:
        optional_row = {
            **verification[-1],
            "obligation_id": "V-optional",
            "command": "synthetic-optional",
            "applicability": "optional",
        }
        projection["owned_verification"] = [*verification, optional_row]
        projection["verification_execution_authority"].append(dict(optional_row))
    projection["projection_hash"] = "f" * 64 if bad_projection_hash else stable_hash(projection)
    service = TaskRuntimeService(str(workspace))
    row = service.create_task_row(
        subject="Private revalidation fixture",
        metadata={
            "external_task_id": TASK,
            "factory_run_id": FACTORY,
            "project_id": PROJECT,
            "factory_bench_project_id": PROJECT,
            "task_completion_projection": projection,
            "job_token": {
                "run_id": FACTORY,
                "project_id": PROJECT,
                "gate_policy": {
                    "project_completion_contract_hash": "a" * 64,
                    **({"required_evidence_modalities": ["code", "command", "qa"]} if declared_modalities else {}),
                },
            },
        },
    )
    claim = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id=CHILD, external_task_id=TASK
    )
    assert claim["success"] is True
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    settled = service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(workspace),
            identity=identity,
            outcome="completed",
            summary="Private fixture execution ended, not delivery qualification",
        )
    )
    assert settled["success"] is True
    native = next(row for row in _facts(workspace, "task_runtime.execution") if row["event_type"] == "claimed")
    append_deferred_followup_task_boundary_verdict(
        workspace=str(workspace), task_id=TASK, run_id=CHILD, reason="needs_followup_workflow"
    )
    old = _projection(workspace)["latest_by_task"][TASK]
    bindings = tuple(
        (
            row["obligation_id"],
            f"synthetic-receipt-{index}",
            stable_hash({"receipt": index}),
            stable_hash({"inputs": index}),
        )
        for index, row in enumerate(verification)
    )
    normal = evaluate_task_boundary_verdict(
        workspace=str(workspace),
        task_id=TASK,
        run_id=CHILD,
        target_files=[row["path"] for row in artifacts],
        completed_artifacts=[row["path"] for row in artifacts],
        required_verifiers=[row["command"] for row in verification],
        completed_verifiers=[row["command"] for row in verification],
        evidence_refs=[binding[1] for binding in bindings],
    )
    assert normal.ok is True
    return {"claim": native, "projection": projection, "before": old, "normal": normal, "bindings": bindings}


def _projection(workspace: Path) -> dict[str, Any]:
    return read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(workspace), run_id=CHILD, max_runs=1)
    ).projection["task_boundary"]


def _command(workspace: Path, fixture: dict[str, Any], **changes: Any) -> ReprojectTaskBoundaryVerdictCommandV1:
    values = {
        "workspace": str(workspace),
        "run_id": CHILD,
        "task_id": TASK,
        "expected_content_id": fixture["before"]["content_id"],
        "reason": "director_post_settle_workspace_revalidation",
        "revalidated_verdict": fixture["normal"],
        "source_claim_fact_event_id": fixture["claim"]["event_id"],
        "current_receipt_bindings": fixture["bindings"],
    }
    values.update(changes)
    return ReprojectTaskBoundaryVerdictCommandV1(**values)


def test_deferred_placeholder_cannot_promote_itself_without_revalidation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    before = _facts(tmp_path, "execution.control_plane")
    with pytest.raises(ControlPlaneRunLedgerV1Error, match="task_boundary_revalidation_required"):
        reproject_task_boundary_verdict(
            ReprojectTaskBoundaryVerdictCommandV1(
                workspace=str(tmp_path),
                run_id=CHILD,
                task_id=TASK,
                expected_content_id=fixture["before"]["content_id"],
                reason="director_post_settle_workspace_revalidation",
            )
        )
    assert _facts(tmp_path, "execution.control_plane") == before


def test_optional_claimed_verifier_requires_no_completion_binding(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, optional_verifier=True)
    command = _command(tmp_path, fixture)
    assert len(command.current_receipt_bindings) == 5
    result = reproject_task_boundary_verdict(command)
    assert result.status == "reprojected"
    current = _projection(tmp_path)["latest_by_task"][TASK]
    assert current["ok"] is True
    assert "synthetic-optional" not in current["required_verifiers"]
    assert len(current["reprojection"]["current_receipt_bindings"]) == 5


def test_normal_cannot_omit_claimed_director_modalities_or_claim_qa_pass(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, declared_modalities=True)
    with pytest.raises(ControlPlaneRunLedgerV1Error, match="normal_scope_incomplete"):
        reproject_task_boundary_verdict(_command(tmp_path, fixture))
    normal = replace(
        fixture["normal"],
        required_evidence_modalities=("code", "command"),
        present_evidence_modalities=("code", "command"),
    )
    assert (
        reproject_task_boundary_verdict(_command(tmp_path, fixture, revalidated_verdict=normal)).status == "reprojected"
    )
    assert "qa" not in _projection(tmp_path)["latest_by_task"][TASK]["present_evidence_modalities"]


def test_full_claim_bound_normal_verdict_persists_and_cold_retry_is_idempotent(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    command = _command(tmp_path, fixture)
    before = _facts(tmp_path, "execution.control_plane")
    result = reproject_task_boundary_verdict(command)
    cold = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id=CHILD, max_runs=1)
    ).projection
    current = cold["task_boundary"]["latest_by_task"][TASK]
    assert result.status == "reprojected" and current["ok"] is True
    assert len(current["target_files"]) == 13 and len(current["required_verifiers"]) == 5
    assert cold["task_boundary"]["historical_failed_count"] == 1
    assert current["reprojection"]["source_claim_fact_event_id"] == fixture["claim"]["event_id"]
    assert current["reprojection"]["completion_contract_hash"] == "a" * 64
    assert current["reprojection"]["task_projection_hash"] == fixture["projection"]["projection_hash"]
    assert current["reprojection"]["current_receipt_bindings"] == [list(value) for value in fixture["bindings"]]
    after = _facts(tmp_path, "execution.control_plane")
    assert after[: len(before)] == before and len(after) == len(before) + 1
    assert after[-1]["payload"]["event"]["job_token"] == before[-1]["payload"]["event"]["job_token"]
    again = reproject_task_boundary_verdict(command)
    assert again.status == "idempotent" and again.content_id == result.content_id
    assert _facts(tmp_path, "execution.control_plane") == after


@pytest.mark.parametrize(
    "defect",
    [
        "foreign_task",
        "foreign_run",
        "missing_target",
        "missing_verifier",
        "failed_verifier",
        "missing_binding",
        "foreign_binding",
        "duplicate_binding",
        "wrong_claim",
        "not_claimed",
    ],
)
def test_revalidation_rejects_incomplete_or_foreign_basis_without_append(tmp_path: Path, defect: str) -> None:
    fixture = _fixture(tmp_path)
    normal: TaskBoundaryVerdictV1 = fixture["normal"]
    changes: dict[str, Any] = {}
    if defect == "foreign_task":
        changes["revalidated_verdict"] = replace(normal, task_id="FOREIGN")
    if defect == "foreign_run":
        changes["revalidated_verdict"] = replace(normal, run_id="foreign-child")
    if defect == "missing_target":
        changes["revalidated_verdict"] = replace(normal, target_files=normal.target_files[:-1])
    if defect == "missing_verifier":
        changes["revalidated_verdict"] = replace(normal, required_verifiers=normal.required_verifiers[:-1])
    if defect == "failed_verifier":
        changes["revalidated_verdict"] = replace(
            normal,
            completed_verifiers=normal.completed_verifiers[:-1],
            failed_required_verifiers=(normal.required_verifiers[-1],),
        )
    if defect == "missing_binding":
        changes["current_receipt_bindings"] = fixture["bindings"][:-1]
    if defect == "foreign_binding":
        changes["current_receipt_bindings"] = (*fixture["bindings"][:-1], ("V-FOREIGN", *fixture["bindings"][-1][1:]))
    if defect == "duplicate_binding":
        changes["current_receipt_bindings"] = (*fixture["bindings"], fixture["bindings"][0])
    if defect == "wrong_claim":
        changes["source_claim_fact_event_id"] = "f" * 32
    if defect == "not_claimed":
        changes["source_claim_fact_event_id"] = next(
            row["event_id"] for row in _facts(tmp_path, "task_runtime.execution") if row["event_type"] == "completed"
        )
    before = _facts(tmp_path, "execution.control_plane")
    with pytest.raises((ControlPlaneRunLedgerV1Error, TypeError, ValueError), match="revalidation"):
        reproject_task_boundary_verdict(_command(tmp_path, fixture, **changes))
    assert _facts(tmp_path, "execution.control_plane") == before


def test_revalidation_rejects_tampered_captured_projection(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, bad_projection_hash=True)
    before = _facts(tmp_path, "execution.control_plane")
    with pytest.raises(ControlPlaneRunLedgerV1Error, match="revalidation"):
        reproject_task_boundary_verdict(_command(tmp_path, fixture))
    assert _facts(tmp_path, "execution.control_plane") == before


def test_revalidation_cas_and_basis_changes_never_reuse_a_success(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(ControlPlaneRunLedgerV1Error, match="stale_predecessor"):
        reproject_task_boundary_verdict(_command(tmp_path, fixture, expected_content_id="f" * 64))
    command = _command(tmp_path, fixture)
    reproject_task_boundary_verdict(command)
    before = _facts(tmp_path, "execution.control_plane")
    changed = (*fixture["bindings"][:-1], (*fixture["bindings"][-1][:2], "f" * 64, fixture["bindings"][-1][3]))
    with pytest.raises(ControlPlaneRunLedgerV1Error, match=r"revalidation|stale_predecessor"):
        reproject_task_boundary_verdict(_command(tmp_path, fixture, current_receipt_bindings=changed))
    assert _facts(tmp_path, "execution.control_plane") == before


@pytest.mark.parametrize("foreign", ["factory", "project"])
def test_revalidation_reuses_strict_child_owner_constraints(tmp_path: Path, foreign: str) -> None:
    fixture = _fixture(tmp_path)
    service = TaskRuntimeService(str(tmp_path))
    row = service.create_task_row(
        subject="Explicit foreign owner fixture",
        metadata={
            "external_task_id": "FOREIGN-UNIT",
            "factory_run_id": "foreign-factory" if foreign == "factory" else FACTORY,
            "factory_bench_project_id": "foreign-project" if foreign == "project" else PROJECT,
        },
    )
    claim = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id=CHILD, external_task_id="FOREIGN-UNIT"
    )
    assert claim["success"] is True
    before = _facts(tmp_path, "execution.control_plane")
    with pytest.raises(ControlPlaneRunLedgerV1Error, match="revalidation"):
        reproject_task_boundary_verdict(_command(tmp_path, fixture))
    assert _facts(tmp_path, "execution.control_plane") == before


def _append_newer_failure(workspace: Path, *, task_id: str = TASK) -> None:
    append_director_task_boundary_verdict(
        role="director",
        workspace=str(workspace),
        task_id=task_id,
        run_id=CHILD,
        context_override={
            "required_write_targets": ["artifact-0.txt"],
            "required_verifiers": ["newer-unexecuted-verifier"],
        },
        tool_results=[],
    )


def test_same_task_interleaving_cannot_commit_old_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.cells.control_plane.run_ledger.public import service as owner

    fixture = _fixture(tmp_path)
    before = _facts(tmp_path, "execution.control_plane")
    original = owner.append_run_ledger_event
    injected = False

    def interleave(command: AppendRunLedgerEventCommandV1) -> Any:
        nonlocal injected
        if not injected and command.event.get("stage") == "task_boundary_reprojection":
            injected = True
            _append_newer_failure(tmp_path)
        return original(command)

    monkeypatch.setattr(owner, "append_run_ledger_event", interleave)
    with pytest.raises(ControlPlaneRunLedgerV1Error, match="stale_predecessor"):
        reproject_task_boundary_verdict(_command(tmp_path, fixture))
    after = _facts(tmp_path, "execution.control_plane")
    assert injected and after[: len(before)] == before and len(after) == len(before) + 1
    current = _projection(tmp_path)["latest_by_task"][TASK]
    assert current["status"] == "required_verifier_missing" and current["ok"] is False


@pytest.mark.parametrize("interleaving", ["other_task", "ordinary_event"])
def test_unrelated_run_events_do_not_block_valid_task_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interleaving: str
) -> None:
    from polaris.cells.control_plane.run_ledger.public import service as owner

    fixture = _fixture(tmp_path)
    if interleaving == "other_task":
        service = TaskRuntimeService(str(tmp_path))
        row = service.create_task_row(
            subject="Private other task",
            metadata={
                "external_task_id": "SYNTHETIC-OTHER",
                "factory_run_id": FACTORY,
                "factory_bench_project_id": PROJECT,
            },
        )
        claim = service.claim_execution(
            row["id"], worker_id="director", role_id="director", run_id=CHILD, external_task_id="SYNTHETIC-OTHER"
        )
        assert claim["success"]
        assert service.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(tmp_path),
                identity=TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"]),
                outcome="completed",
                summary="Private independent task ended",
            )
        )["success"]
    original = owner.append_run_ledger_event
    injected = False

    def interleave(command: AppendRunLedgerEventCommandV1) -> Any:
        nonlocal injected
        if not injected and command.event.get("stage") == "task_boundary_reprojection":
            injected = True
            if interleaving == "other_task":
                _append_newer_failure(tmp_path, task_id="SYNTHETIC-OTHER")
            else:
                original(
                    AppendRunLedgerEventCommandV1(
                        workspace=str(tmp_path),
                        run_id=CHILD,
                        event={
                            "event_type": "private_observation",
                            "run_id": CHILD,
                            "detail": "Ordinary immutable observation, not a gate or receipt",
                        },
                    )
                )
        return original(command)

    monkeypatch.setattr(owner, "append_run_ledger_event", interleave)
    result = reproject_task_boundary_verdict(_command(tmp_path, fixture))
    assert injected and result.verdict["ok"] is True
    assert _projection(tmp_path)["latest_by_task"][TASK]["status"] == "completed_verified"


@pytest.mark.parametrize("failure", ["projection_loss", "partial_tail"])
def test_fact_committed_projection_failure_cold_retry_preserves_fact_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from polaris.cells.control_plane.run_ledger.public import service as owner

    fixture = _fixture(tmp_path)
    captured: dict[str, Any] = {}
    original_append = owner.append_run_ledger_event

    def capture(command: AppendRunLedgerEventCommandV1) -> Any:
        if command.event.get("stage") == "task_boundary_reprojection":
            captured["command"] = command
        return original_append(command)

    monkeypatch.setattr(owner, "append_run_ledger_event", capture)
    original_write = RunLedger._append_serialized_row_locked

    def fail_after_fact(self: RunLedger, handle: Any, serialized_row: bytes) -> None:
        if b'"stage":"task_boundary_reprojection"' in serialized_row:
            if failure == "partial_tail":
                handle.write(serialized_row[:19])
                handle.flush()
            raise OSError("Explicit private crash after durableFact before projection completion")
        original_write(self, handle, serialized_row)

    monkeypatch.setattr(RunLedger, "_append_serialized_row_locked", fail_after_fact)
    with pytest.raises(OSError, match="Explicit private crash"):
        reproject_task_boundary_verdict(_command(tmp_path, fixture))
    committed = _facts(tmp_path, "execution.control_plane")
    assert len(committed) == 2
    assert committed[-1]["payload"]["event"]["task_boundary_verdict"]["ok"] is True
    monkeypatch.setattr(RunLedger, "_append_serialized_row_locked", original_write)
    restored = original_append(captured["command"])
    assert restored.receipt["event"]["content_id"] == committed[-1]["payload"]["event"]["content_id"]
    assert _facts(tmp_path, "execution.control_plane") == committed
    assert len(RunLedger(tmp_path, run_id=CHILD).read_events()) == 2
    _append_newer_failure(tmp_path)
    newer = _facts(tmp_path, "execution.control_plane")
    original_append(captured["command"])
    assert _facts(tmp_path, "execution.control_plane") == newer
    assert len(RunLedger(tmp_path, run_id=CHILD).read_events()) == 3
    assert _projection(tmp_path)["latest_by_task"][TASK]["status"] == "required_verifier_missing"


def test_locked_reprojection_reuses_strict_canonical_fact_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.control_plane.run_ledger.public import service as owner

    fixture = _fixture(tmp_path)
    before = _facts(tmp_path, "execution.control_plane")
    original = owner.append_run_ledger_event
    injected = False

    def interleave(command: AppendRunLedgerEventCommandV1) -> Any:
        nonlocal injected
        if not injected and command.event.get("stage") == "task_boundary_reprojection":
            injected = True
            # Explicit synthetic malformed envelope, never a native owner fact.
            append_fact_event(
                AppendFactEventCommandV1(
                    workspace=str(tmp_path),
                    stream="execution.control_plane",
                    source="control_plane.run_ledger",
                    event_type="synthetic_wrong_outer_event",
                    run_id=CHILD,
                    task_id=TASK,
                    payload={
                        "schema_version": "execution.control_plane.fact.v1",
                        "run_id": CHILD,
                        "event": before[0]["payload"]["event"],
                    },
                    idempotency_key="explicit-synthetic-invalid-canonical-envelope",
                    strict_integrity=True,
                )
            )
        return original(command)

    monkeypatch.setattr(owner, "append_run_ledger_event", interleave)
    with pytest.raises(ValueError, match="fact_noncanonical"):
        reproject_task_boundary_verdict(_command(tmp_path, fixture))
    assert len(_facts(tmp_path, "execution.control_plane")) == len(before) + 1
