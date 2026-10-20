"""Private real FactoryStore/compose evidence; canned provider and synthetic PM fixtures."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public.tests.test_recipe_diagnosis_scoped_projection import _recipe_case
from polaris.cells.factory.pipeline.internal import factory_stage_executor as stage_executor_module
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.internal.factory_store import FactoryArtifactSnapshotError, FactoryStore
from polaris.cells.factory.pipeline.tests._characterization_helpers import (
    _bootstrap_fact_stream_workspace,
    _executor,
    _factory_stage_context,
    _single_task_chief_engineer_result,
    _write_minimal_chief_engineer_plan,
)
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1
from polaris.kernelone.storage import resolve_storage_roots

KEY = "chief_engineer_semantic_patch_proposal"
STAGE_KEY = "chief_engineer_semantic_patch_proposals"


def _compose_case(tmp_path: Path, *, denied: bool = False) -> tuple[Any, Any, Any, Any, Any]:
    source, candidate, diagnosis = _recipe_case(tmp_path)
    rows = candidate.candidate["project_completion_contract"]["obligations"]["entrypoints"]
    row = deepcopy(rows[0] if denied else rows[1])
    row["command"] = "python -m src.main"
    # Provider echoes deliberately differ from active CAS; existing binder owns rebinding.
    proposal = {"base_candidate_hash": "c" * 64, "diagnosis_hash": "d" * 64, "entrypoint_upserts": [row]}
    result = RoleExecutionResultV1(
        ok=True,
        status="completed",
        role="chief_engineer",
        workspace=str(tmp_path),
        run_id=candidate.run_id,
        task_id="CE-REPAIR",
        metadata={
            "structured_output": proposal,
            "context_snapshot_ref": "f" * 24,
            "final_request_context_audit": {"request_identity": {"call_id": "local-call", "turn_id": "local-turn"}},
            "job_token": "NEVER_COPY_METADATA_TOKEN",
        },
    )
    executor = OrchestrationStageExecutor(tmp_path)
    return executor, source, candidate, diagnosis, result


def _compose(executor: Any, source: Any, candidate: Any, diagnosis: Any, result: Any) -> Any:
    return executor._compose_chief_engineer_semantic_repair_result(
        result=result,
        candidate=candidate,
        diagnosis=diagnosis,
        tasks=source.tasks,
        authority_carrier=source.authority_carrier,
    )


def _store(tmp_path: Path, *, cold: bool = False) -> FactoryStore:
    return FactoryStore(Path(resolve_storage_roots(str(tmp_path)).runtime_root) / "factory", create_root=not cold)


def _read(store: FactoryStore, run_id: str, evidence: dict[str, Any]) -> Any:
    return store.read_stage_artifact_snapshot(
        run_id,
        evidence["snapshot_ref"],
        evidence["snapshot_sha256"],
        evidence["byte_count"],
    )


@pytest.mark.parametrize("denied", [False, True])
def test_exact_decoded_proposal_survives_bind_or_compose_rejection(tmp_path: Path, denied: bool) -> None:
    executor, source, candidate, diagnosis, original = _compose_case(tmp_path, denied=denied)
    before = deepcopy(original.metadata["structured_output"])
    result = _compose(executor, source, candidate, diagnosis, original)
    assert result.ok is not denied
    evidence = result.metadata[KEY]
    assert evidence["capture_status"] == "PERSISTED"
    snapshot = _read(_store(tmp_path, cold=True), candidate.run_id, evidence)
    envelope = json.loads(snapshot.content.decode("utf-8"))
    assert envelope["decoded_proposal"] == before
    assert original.metadata["structured_output"] == before
    expected = hashlib.sha256(
        json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    assert evidence["proposal_canonical_sha256"] == expected
    assert envelope["candidate_hash"] == candidate.candidate_hash != before["base_candidate_hash"]
    assert envelope["diagnosis_hash"] == diagnosis.diagnosis_hash != before["diagnosis_hash"]
    assert evidence["context_snapshot_ref"] == "f" * 24
    assert evidence["native_raw_call_hash"] is None
    assert evidence["native_arguments_hash"] is None
    assert b"NEVER_COPY_METADATA_TOKEN" not in snapshot.content


@pytest.mark.parametrize("defect", ["run", "ref", "hash", "count"])
def test_cold_snapshot_reader_refuses_locator_or_digest_drift(tmp_path: Path, defect: str) -> None:
    args = _compose_case(tmp_path)
    result = _compose(*args)
    evidence = dict(result.metadata[KEY])
    run_id = args[2].run_id
    if defect == "run":
        run_id = "foreign-run"
    elif defect == "ref":
        evidence["snapshot_ref"] += "x"
    elif defect == "hash":
        evidence["snapshot_sha256"] = "0" * 64
    else:
        evidence["byte_count"] += 1
    with pytest.raises(FactoryArtifactSnapshotError):
        _read(_store(tmp_path, cold=True), run_id, evidence)


@pytest.mark.parametrize("denied", [False, True])
def test_capture_storage_failure_is_unknown_and_does_not_change_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    denied: bool,
) -> None:
    args = _compose_case(tmp_path, denied=denied)
    baseline = _compose(*args)

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("injected private snapshot failure")

    monkeypatch.setattr(FactoryStore, "persist_stage_artifact_snapshot", fail)
    observed = _compose(*args)
    assert (observed.ok, observed.status, observed.error_code, observed.error_message, observed.output) == (
        baseline.ok,
        baseline.status,
        baseline.error_code,
        baseline.error_message,
        baseline.output,
    )
    assert observed.metadata["structured_output"] == baseline.metadata["structured_output"]
    evidence = observed.metadata[KEY]
    assert evidence["capture_status"] == "UNKNOWN"
    assert evidence["snapshot_ref"] is None
    assert evidence["snapshot_sha256"] is None
    assert evidence["byte_count"] is None


def test_two_rounds_reach_stage_result_and_cold_existing_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Actual StageResult collector; PM proof/cutoff and provider are declared test ports."""
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "0")
    _bootstrap_fact_stream_workspace(tmp_path)
    executor = _executor(tmp_path)  # Existing explicit synthetic PM issuer, not original PM provenance.
    _write_minimal_chief_engineer_plan(executor)
    initial = _single_task_chief_engineer_result()
    payload = initial.metadata["structured_output"]
    payload["construction_plan"]["task_plans"]["TASK-CANCEL"]["behavior_invariant_refs"] = ["INV-MISSING"]
    initial.output = json.dumps(payload)
    calls = []

    class RecordingRolePort:
        async def execute_role_task(self, command: Any) -> Any:
            calls.append(command)
            if len(calls) == 1:
                return initial
            patch = {
                "base_candidate_hash": command.context["chief_engineer_semantic_repair_base_candidate_hash"],
                "diagnosis_hash": command.context["chief_engineer_semantic_repair_diagnosis_hash"],
                "task_behavior_ref_replacements": {"TASK-CANCEL": ["STILL-MISSING"] if len(calls) == 2 else []},
            }
            return RoleExecutionResultV1(
                ok=True,
                status="completed",
                role="chief_engineer",
                workspace=str(tmp_path),
                task_id=command.task_id,
                run_id=command.run_id,
                metadata={**_single_task_chief_engineer_result().metadata, "structured_output": patch},
            )

    monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", RecordingRolePort)
    run = FactoryRun(
        id="snapshot-rounds",
        config=FactoryConfig(name="private-proposal"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-20T00:00:00+00:00",
    )
    stage = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))
    assert stage.status == "success", stage.output
    assert len(calls) == 3
    evidence = stage.metadata[STAGE_KEY]
    assert len(evidence) == 2
    assert [row["repair_round"] for row in evidence] == [1, 2]
    assert all(row["capture_status"] == "PERSISTED" for row in evidence)
    assert evidence[0]["snapshot_ref"] != evidence[1]["snapshot_ref"]
    # Same StageResult.to_dict mapping as existing _service_stage; real save/get is tested cold.
    run.metadata["stage_results"] = {stage.stage: stage.to_dict()}
    store = _store(tmp_path)
    asyncio.run(store.save_run(run))
    cold = _store(tmp_path, cold=True)
    restored = asyncio.run(cold.get_run(run.id))
    assert restored is not None
    links = restored.metadata["stage_results"][stage.stage]["metadata"][STAGE_KEY]
    assert links == evidence
    proposals = [json.loads(_read(cold, run.id, row).content.decode("utf-8"))["decoded_proposal"] for row in links]
    assert proposals[0]["task_behavior_ref_replacements"]["TASK-CANCEL"] == ["STILL-MISSING"]
    assert proposals[1]["task_behavior_ref_replacements"]["TASK-CANCEL"] == []
