"""Bootstrap adapter tests for non-Factory ProjectOutcome owner facts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import RunLedgerProjectionResultV1
from polaris.cells.runtime.projection.public import (
    DeliveryAxisV1,
    ProjectOutcomeNonFactoryOwnerObservationV1,
    ProjectOutcomeOwnerObservationV1Error,
    QaAxisV1,
    RunLedgerAxisV1,
    TaskBoundaryAxisV1,
    TaskRuntimeAxisV1,
)
from polaris.cells.runtime.task_runtime.public import ObservableTaskRowsProjectionV1

_CONTRACT_HASH = "a" * 64
_RUN_ID = "factory-run-1"
_PROJECT_ID = "project-1"
_TASK_ID = "TASK-1"
_CHILD_RUN_ID = "director-run-1"


def _task_runtime_projection(
    workspace: Path,
    *,
    authoritative: bool = True,
    degraded: bool = False,
    factory_run_id: str = _RUN_ID,
    execution_state: str = "completed",
) -> ObservableTaskRowsProjectionV1:
    return ObservableTaskRowsProjectionV1(
        workspace=str(workspace.resolve()),
        source=("task_runtime.execution_fact" if authoritative else "task_runtime.transitional_file_fallback"),
        authoritative=authoritative,
        degraded=degraded,
        rows=(
            {
                "task_id": _TASK_ID,
                "factory_run_id": factory_run_id,
                "workflow_run_id": _CHILD_RUN_ID,
                "status": execution_state,
                "execution_state": execution_state,
                "fact_event_seq": 7,
                "metadata": {
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                },
            },
        ),
        readiness={"ready": authoritative},
    )


def _boundary_verdict(*, ok: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "polaris.task_boundary_verdict.v1",
        "task_id": _TASK_ID,
        "run_id": _CHILD_RUN_ID,
        "status": "completed_verified" if ok else "dependency_not_unlocked",
        "ok": ok,
        "failure_class": "passed" if ok else "dependency_not_unlocked",
        "responsible_layer": "execution_control_plane",
        "reason": "complete" if ok else "dependency blocked",
        "missing_target_files": [],
        "missing_entrypoint_targets": [],
        "unresolved_local_imports": [],
        "artifact_semantic_mismatches": [],
        "downstream_pending_artifacts": [],
        "blocked_dependencies": [] if ok else ["TASK-0"],
        "required_evidence_modalities": ["command", "verifier"],
        "present_evidence_modalities": ["command", "verifier"] if ok else [],
        "missing_required_evidence_modalities": [] if ok else ["command"],
        "failed_required_evidence_modalities": [],
        "required_verifiers": ["tests"],
        "completed_verifiers": ["tests"] if ok else [],
        "missing_required_verifiers": [] if ok else ["tests"],
        "failed_required_verifiers": [],
        "evidence_refs": ["task-boundary:event-1"],
    }


def _run_ledger_projection(
    workspace: Path,
    *,
    contract_hash: str = _CONTRACT_HASH,
    missing_modalities: tuple[str, ...] = (),
    failed_modalities: tuple[str, ...] = (),
    include_entrypoint: bool = True,
    include_environment: bool = True,
    include_verifier: bool = True,
    derived_failed_modalities: tuple[str, ...] = (),
    entrypoint_ok: bool = True,
    qa_gate_present: bool = True,
    qa_gate_ok: bool = True,
    boundary_ok: bool = True,
) -> RunLedgerProjectionResultV1:
    gate_modalities: dict[str, dict[str, Any]] = {
        "code": {"present": True, "ok": True, "detail": "source committed"},
        "command": {
            "present": True,
            "ok": True,
            "detail": "build passed",
            "metadata": {"entrypoint_kind": "cli" if include_entrypoint and entrypoint_ok else ""},
        },
    }
    if include_entrypoint and not entrypoint_ok:
        gate_modalities["browser"] = {
            "present": True,
            "ok": False,
            "detail": "entrypoint smoke failed",
        }
    if include_environment:
        gate_modalities["environment_prep"] = {
            "present": True,
            "ok": True,
            "detail": "dependencies prepared",
        }
    if include_verifier:
        gate_modalities["verifier"] = {
            "present": True,
            "ok": True,
            "detail": "tests passed",
        }
    for modality in failed_modalities:
        gate_modalities.setdefault(modality, {"present": True})["ok"] = False
    for modality in derived_failed_modalities:
        gate_modalities.setdefault(modality, {"present": True})["ok"] = False

    evidence_modalities = {
        name: {
            "total": 1,
            "present": 1 if summary.get("present") else 0,
            "ok": 1 if summary.get("ok") else 0,
            "failed": 0 if summary.get("ok") else 1,
            "latest_detail": str(summary.get("detail") or ""),
        }
        for name, summary in gate_modalities.items()
    }
    boundary = _boundary_verdict(ok=boundary_ok)
    task_boundary = {
        "ok": boundary_ok,
        "verdict_count": 1,
        "historical_failed_count": 0 if boundary_ok else 1,
        "latest": boundary,
        "latest_by_task": {_TASK_ID: boundary},
        "failed": [] if boundary_ok else [boundary],
    }
    evidence_policy = {
        "ok": not missing_modalities and not failed_modalities,
        "integrity_ok": not missing_modalities,
        "outcome_ok": not failed_modalities,
        "enabled_modalities": ["code", "environment_prep", "command", "verifier"],
        "required_modalities": ["code", "environment_prep", "command", "verifier"],
        "missing_required_modalities": list(missing_modalities),
        "failed_required_modalities": list(failed_modalities),
    }
    qa_gate = {
        "name": "qa" if qa_gate_present else "delivery",
        "stage": "qa" if qa_gate_present else "delivery",
        "ok": qa_gate_ok and not failed_modalities,
        "content_id": "qa-gate-event-1",
        "append_id": "qa-gate-append-1",
        "job_token_id": "job-token-1",
        "capability_ok": True,
        "capability_issues": [],
        "evidence_modalities": gate_modalities,
        "enabled_evidence_modalities": evidence_policy["enabled_modalities"],
        "required_evidence_modalities": evidence_policy["required_modalities"],
        "missing_required_evidence_modalities": list(missing_modalities),
        "failed_required_evidence_modalities": list(failed_modalities),
    }
    run_projection = {
        "schema_version": 1,
        "source": "run_ledger",
        "ok": evidence_policy["ok"] and boundary_ok,
        "integrity_ok": evidence_policy["integrity_ok"],
        "outcome_ok": evidence_policy["outcome_ok"] and boundary_ok,
        "event_count": 3,
        "gate_count": 1,
        "missing": list(missing_modalities),
        "gates": [qa_gate],
        "failed_gates": [] if qa_gate["ok"] else [qa_gate],
        "capability": {
            "ok": True,
            "issues": [],
            "latest_token_id": "job-token-1",
            "latest_contract_hash": contract_hash,
            "latest_blueprint_hash": "b" * 64,
            "job_token_ids": ["job-token-1"],
        },
        "evidence_modalities": evidence_modalities,
        "evidence_policy": evidence_policy,
        "task_boundary": task_boundary,
    }
    project = {
        "project_id": _PROJECT_ID,
        "ok": run_projection["ok"],
        "integrity_ok": run_projection["integrity_ok"],
        "outcome_ok": run_projection["outcome_ok"],
        "gate_count": 1,
        "failed_gate_count": 0 if qa_gate["ok"] else 1,
        "latest_token_id": "job-token-1",
        "detail": "projected",
        "missing": list(missing_modalities),
        "missing_required_modalities": list(missing_modalities),
        "failed_required_modalities": list(failed_modalities),
        "failed_control_plane_events": [],
        "evidence_policy": evidence_policy,
        "evidence_modalities": evidence_modalities,
        "task_boundary": task_boundary,
        "tool_lifecycle": {"ok": True},
    }
    return RunLedgerProjectionResultV1(
        projection={
            "schema_version": 1,
            "source": "run_ledger_projection",
            "available": True,
            "ok": run_projection["ok"],
            "status": "ready" if run_projection["ok"] else "failed",
            "migration_ledgers_included": False,
            "query_scope": {
                "run_id": _RUN_ID,
                "factory_run_id": _RUN_ID,
                "project_id": _PROJECT_ID,
            },
            "consumed_run_ids": [_RUN_ID, _CHILD_RUN_ID],
            "total": 1,
            "projected": 1,
            "missing": 0,
            "failed": 0 if project["ok"] else 1,
            "missing_required_modalities": list(missing_modalities),
            "failed_required_modalities": list(failed_modalities),
            "failed_control_plane_events": [],
            "projects": [project],
            "run_projection": run_projection,
            "evidence_policy": evidence_policy,
            "evidence_modalities": evidence_modalities,
            "task_boundary": task_boundary,
            "tool_lifecycle": {"ok": True},
        }
    )


def _install_owner_queries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    task_runtime: ObservableTaskRowsProjectionV1,
    run_ledger: RunLedgerProjectionResultV1,
) -> tuple[list[str], list[object]]:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    task_queries: list[str] = []
    ledger_queries: list[object] = []

    def query_tasks(workspace: str) -> ObservableTaskRowsProjectionV1:
        task_queries.append(workspace)
        return task_runtime

    def query_ledger(query: object) -> RunLedgerProjectionResultV1:
        ledger_queries.append(query)
        return run_ledger

    monkeypatch.setattr(adapter_module, "query_observable_task_rows", query_tasks)
    monkeypatch.setattr(adapter_module, "read_run_ledger_projection", query_ledger)
    return task_queries, ledger_queries


@pytest.mark.asyncio
async def test_adapter_returns_six_owner_bound_green_axes_for_exact_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    task_queries, ledger_queries = _install_owner_queries(
        monkeypatch,
        task_runtime=_task_runtime_projection(tmp_path),
        run_ledger=_run_ledger_projection(tmp_path),
    )

    observation = (
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )
    )

    assert type(observation) is ProjectOutcomeNonFactoryOwnerObservationV1
    assert observation.workspace == str(tmp_path.resolve())
    assert observation.project_id == _PROJECT_ID
    assert observation.run_id == _RUN_ID
    assert observation.completion_contract_hash == _CONTRACT_HASH
    assert observation.delivery is DeliveryAxisV1.VERIFIED
    assert observation.qa is QaAxisV1.PASSED
    assert observation.task_boundary is TaskBoundaryAxisV1.PASSED
    assert observation.task_runtime is TaskRuntimeAxisV1.CONVERGED
    assert observation.run_ledger is RunLedgerAxisV1.CLOSED
    assert observation.task_count == 1
    assert observation.completed_task_count == 1
    assert observation.missing_required_modalities == ()
    assert observation.failed_required_modalities == ()
    assert observation.evidence_refs.empty_axes() == ()
    assert observation.projection_hashes.empty_axes() == ()
    for axis in ("delivery", "qa", "task_boundary", "task_runtime", "run_ledger"):
        assert getattr(observation.projection_hashes, axis) in getattr(observation.evidence_refs, axis)
    assert task_queries == [str(tmp_path.resolve())]
    assert len(ledger_queries) == 1
    ledger_query = ledger_queries[0]
    assert ledger_query.workspace == str(tmp_path.resolve())
    assert ledger_query.run_id == _RUN_ID
    assert ledger_query.factory_run_id == _RUN_ID
    assert ledger_query.project_id == _PROJECT_ID
    assert ledger_query.include_migration_ledgers is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("projection", "error_code"),
    [
        ("degraded", "project_outcome_task_runtime_projection_degraded"),
        ("empty", "project_outcome_task_runtime_rows_empty"),
    ],
)
async def test_adapter_rejects_degraded_or_empty_exact_task_runtime_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    projection: str,
    error_code: str,
) -> None:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    task_runtime = (
        _task_runtime_projection(tmp_path, authoritative=False, degraded=True)
        if projection == "degraded"
        else _task_runtime_projection(tmp_path, factory_run_id="another-run")
    )
    _install_owner_queries(
        monkeypatch,
        task_runtime=task_runtime,
        run_ledger=_run_ledger_projection(tmp_path),
    )

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )

    assert exc_info.value.error_code == error_code


@pytest.mark.asyncio
async def test_adapter_rejects_run_ledger_scope_or_contract_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    run_ledger = _run_ledger_projection(tmp_path, contract_hash="c" * 64)
    run_ledger.projection["query_scope"]["project_id"] = "other-project"
    _install_owner_queries(
        monkeypatch,
        task_runtime=_task_runtime_projection(tmp_path),
        run_ledger=run_ledger,
    )

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )

    assert exc_info.value.error_code == "project_outcome_run_ledger_scope_mismatch"

    run_ledger.projection["query_scope"]["project_id"] = _PROJECT_ID
    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as contract_exc:
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )

    assert contract_exc.value.error_code == "project_outcome_completion_contract_hash_mismatch"


@pytest.mark.asyncio
async def test_adapter_rejects_duplicate_task_runtime_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    task_runtime = _task_runtime_projection(tmp_path)
    object.__setattr__(task_runtime, "rows", (*task_runtime.rows, dict(task_runtime.rows[0])))
    _install_owner_queries(
        monkeypatch,
        task_runtime=task_runtime,
        run_ledger=_run_ledger_projection(tmp_path),
    )

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )

    assert exc_info.value.error_code == "project_outcome_task_runtime_task_identity_invalid"


@pytest.mark.asyncio
async def test_adapter_rejects_uncommitted_gate_or_modality_state_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    run_ledger = _run_ledger_projection(tmp_path)
    gate = run_ledger.projection["run_projection"]["gates"][0]
    gate["content_id"] = ""
    gate["append_id"] = ""
    _install_owner_queries(
        monkeypatch,
        task_runtime=_task_runtime_projection(tmp_path),
        run_ledger=run_ledger,
    )

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as gate_exc:
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )

    assert gate_exc.value.error_code == "project_outcome_run_ledger_gate_evidence_uncommitted"

    gate["content_id"] = "qa-gate-event-1"
    run_ledger.projection["missing_required_modalities"] = ["command"]
    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as modality_exc:
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )

    assert modality_exc.value.error_code == "project_outcome_required_modality_projection_mismatch"


@pytest.mark.asyncio
async def test_adapter_preserves_missing_and_failed_modalities_as_distinct_owner_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    _install_owner_queries(
        monkeypatch,
        task_runtime=_task_runtime_projection(tmp_path),
        run_ledger=_run_ledger_projection(
            tmp_path,
            missing_modalities=("environment_prep",),
            failed_modalities=("verifier",),
            include_environment=False,
        ),
    )

    observation = (
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )
    )

    assert observation.delivery is DeliveryAxisV1.PRESENT_UNVERIFIED
    assert observation.qa is QaAxisV1.FAILED
    assert observation.run_ledger is RunLedgerAxisV1.NOT_CLOSED
    assert observation.missing_required_modalities == ("environment_prep",)
    assert observation.failed_required_modalities == ("qa", "verifier")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ledger_options", "reason_token", "derived_modality", "expected_delivery"),
    [
        (
            {"include_entrypoint": False},
            "entrypoint_evidence_missing",
            "entrypoint",
            DeliveryAxisV1.PRESENT_UNVERIFIED,
        ),
        (
            {"include_environment": False},
            "environment_prep_evidence_missing",
            "environment_prep",
            DeliveryAxisV1.PRESENT_UNVERIFIED,
        ),
        (
            {"include_verifier": False},
            "verifier_evidence_missing",
            "verifier",
            DeliveryAxisV1.PRESENT_UNVERIFIED,
        ),
        (
            {"qa_gate_present": False},
            "qa_evidence_missing",
            "qa",
            DeliveryAxisV1.VERIFIED,
        ),
    ],
)
async def test_adapter_does_not_verify_delivery_without_required_physical_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ledger_options: dict[str, bool],
    reason_token: str,
    derived_modality: str,
    expected_delivery: DeliveryAxisV1,
) -> None:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    _install_owner_queries(
        monkeypatch,
        task_runtime=_task_runtime_projection(tmp_path),
        run_ledger=_run_ledger_projection(tmp_path, **ledger_options),
    )

    observation = (
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )
    )

    assert observation.delivery is expected_delivery
    assert observation.run_ledger is RunLedgerAxisV1.NOT_CLOSED
    assert reason_token in observation.reasons
    assert derived_modality in observation.missing_required_modalities
    assert derived_modality not in observation.failed_required_modalities


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ledger_options", "reason_token", "derived_modality"),
    [
        ({"entrypoint_ok": False}, "entrypoint_evidence_failed", "entrypoint"),
        (
            {"derived_failed_modalities": ("environment_prep",)},
            "environment_prep_evidence_failed",
            "environment_prep",
        ),
        (
            {"derived_failed_modalities": ("verifier",)},
            "verifier_evidence_failed",
            "verifier",
        ),
        ({"qa_gate_ok": False}, "qa_evidence_failed", "qa"),
    ],
)
async def test_adapter_projects_derived_failed_modalities_as_owner_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ledger_options: dict[str, object],
    reason_token: str,
    derived_modality: str,
) -> None:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    _install_owner_queries(
        monkeypatch,
        task_runtime=_task_runtime_projection(tmp_path),
        run_ledger=_run_ledger_projection(tmp_path, **ledger_options),
    )

    observation = (
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )
    )

    assert observation.run_ledger is RunLedgerAxisV1.NOT_CLOSED
    assert reason_token in observation.reasons
    assert derived_modality in observation.failed_required_modalities
    assert derived_modality not in observation.missing_required_modalities


@pytest.mark.asyncio
async def test_adapter_keeps_failed_task_runtime_and_boundary_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    _install_owner_queries(
        monkeypatch,
        task_runtime=_task_runtime_projection(tmp_path, execution_state="failed"),
        run_ledger=_run_ledger_projection(tmp_path, boundary_ok=False),
    )

    observation = (
        await adapter_module.ProjectOutcomeNonFactoryOwnerObservationAdapter().observe_project_outcome_non_factory(
            workspace=str(tmp_path),
            project_id=_PROJECT_ID,
            run_id=_RUN_ID,
            completion_contract_hash=_CONTRACT_HASH,
        )
    )

    assert observation.task_runtime is TaskRuntimeAxisV1.NOT_CONVERGED
    assert observation.task_boundary is TaskBoundaryAxisV1.FAILED
    assert observation.delivery is DeliveryAxisV1.VERIFIED
    assert observation.qa is QaAxisV1.PASSED
    assert observation.completed_task_count == 0


def test_bootstrap_configure_binds_project_outcome_owner_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_project_outcome_owner as adapter_module

    bound: list[object] = []
    monkeypatch.setattr(
        adapter_module,
        "bind_project_outcome_non_factory_owner_observation_port",
        bound.append,
    )

    adapter_module.configure_runtime_projection_project_outcome_owner()

    assert bound == [adapter_module.PROJECT_OUTCOME_NON_FACTORY_OWNER_OBSERVATION_ADAPTER]


def test_http_app_factory_invokes_project_outcome_owner_wiring() -> None:
    app_factory_path = Path(__file__).resolve().parents[3] / "delivery" / "http" / "app_factory.py"
    source = app_factory_path.read_text(encoding="utf-8")

    assert "configure_runtime_projection_project_outcome_owner" in source
