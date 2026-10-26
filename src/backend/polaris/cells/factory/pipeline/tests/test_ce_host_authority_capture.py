"""Synthetic PM input through original Factory stage persistence and CE capture.

No retired start/execute entry is reopened. The actual FactoryStore, artifact
binding, stage transaction, durable loader and CE seal/persistence stay real.
Only PM computation input and non-authoritative NATS fanout are controlled.
This is component evidence, not Native/provider/Bench delivery.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint import public as ce
from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import (
    _payload,
)
from polaris.cells.factory.pipeline.internal.factory_event_chain import (
    FactoryRunAdmissionV1,
    build_factory_run_admitted_event,
)
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus,
    StageResult,
)
from polaris.cells.factory.pipeline.internal.factory_stage_executor import (
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.internal.factory_stage_persistence import (
    reduce_factory_stage_persistence,
)
from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_provenance import (
    _pm_document,
)
from polaris.cells.orchestration.pm_planning.public.task_intent import (
    normalize_pm_task_intent,
)


def _synthetic_pm_tasks() -> list[dict[str, Any]]:
    return normalize_pm_task_intent(
        {
            "schema_version": "pm.task_intent.v1",
            "product_goal": "Produce a deterministic synthetic CLI fixture",
            "required_requirements": ["CLI prints deterministic output"],
            "optional_requirements": [],
            "acceptance": ["CLI prints deterministic output"],
            "explicit_constraints": ["Keep data inside the workspace"],
            "tasks": [
                {
                    "id": "TASK-A",
                    "title": "Synthetic CLI",
                    "goal": "Produce deterministic output",
                    "acceptance": ["CLI prints deterministic output"],
                    "depends_on": [],
                }
            ],
        }
    )


async def _committed_pm_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FactoryRun, OrchestrationStageExecutor]:
    executor = OrchestrationStageExecutor(tmp_path)
    owner = FactoryRunService(tmp_path)

    async def no_transport(run_id: str, event: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(owner, "_publish_factory_event", no_transport)
    run = FactoryRun(
        id="synthetic-ce-capture",
        config=FactoryConfig(name="synthetic-project", stages=["pm_planning"]),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-26T00:00:00+00:00",
        metadata={
            "project_id": "synthetic-project",
            "factory_start_request": {
                "workspace": str(tmp_path),
                "metadata": {"project_id": "synthetic-project"},
            },
        },
    )
    admission = FactoryRunAdmissionV1(
        factory_run_id=run.id,
        created_at=run.created_at,
        name=run.config.name,
        description=run.config.description,
    )
    await owner._append_event(run.id, build_factory_run_admitted_event(admission), publish=False)
    await owner.store.save_run(run)
    await owner._mark_stage_started(run, "pm_planning", "2026-10-26T00:00:01+00:00")
    executor._write_json_artifact("tasks/plan.json", _pm_document(_synthetic_pm_tasks()))
    result = StageResult(
        stage="pm_planning",
        status="success",
        output="Synthetic PM computation input persisted",
        started_at="2026-10-26T00:00:01+00:00",
        completed_at="2026-10-26T00:00:02+00:00",
    )
    await owner._mark_stage_finished(run, result)
    assert result.status == "success", result.output
    events = await owner.store.get_authoritative_events(run.id)
    state = reduce_factory_stage_persistence(events, factory_run_id=run.id)
    assert len(state.commits) == 1 and state.commits[0].stage == "pm_planning"
    cold = await FactoryRunService(tmp_path, read_only=True).get_run(run.id)
    assert cold is not None
    return cold, executor


async def _capture_real_owner(
    workspace: Path,
    run: FactoryRun,
    executor: OrchestrationStageExecutor,
    *,
    pm_tasks: list[dict[str, Any]] | None = None,
) -> object:
    tasks = _synthetic_pm_tasks() if pm_tasks is None else pm_tasks
    portfolio_tasks = executor._chief_engineer_portfolio_tasks(tasks)
    query = ce.CaptureChiefEngineerPortfolioAuthorityQueryV1(str(workspace), run.id, portfolio_tasks)

    async def real_factory_source(request: Any) -> dict[str, Any]:
        assert request == query
        authority = await executor._load_chief_engineer_portfolio_authority(
            run=run, pm_tasks=tasks, portfolio_tasks=portfolio_tasks
        )
        return {
            "workspace": str(workspace),
            "run_id": run.id,
            "tasks": portfolio_tasks,
            "project_id": authority.project_id,
            "pm_stage_event_id": authority.pm_stage_event_id,
            "pm_contract_hash": authority.pm_contract_hash,
            "catalog_snapshot": authority.catalog_snapshot,
            "catalog_snapshot_hash": authority.catalog_snapshot_hash,
            "verifier_policy_hash": authority.verifier_policy_hash,
            "verifier_policy_snapshot": authority.verifier_policy,
            "verifier_policy_snapshot_hash": authority.verifier_policy_snapshot_hash,
            "verification_command_authority": authority.verification_command_authority,
        }

    return await ce.capture_chief_engineer_portfolio_authority(query, source_port=real_factory_source)


@pytest.mark.asyncio
async def test_candidate_build_reuses_carrier_from_real_committed_pm_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, executor = await _committed_pm_owner(tmp_path, monkeypatch)
    carrier = await _capture_real_owner(tmp_path, run, executor)
    portfolio = executor._build_chief_engineer_portfolio_from_candidate(
        run_id=run.id,
        tasks=executor._chief_engineer_portfolio_tasks(_synthetic_pm_tasks()),
        authority_carrier=carrier,
        structured_output=_payload(),
    )
    assert portfolio.project_completion_contract is not None
    assert portfolio.project_completion_contract.project_id == "synthetic-project"
    assert portfolio.project_completion_contract.run_id == run.id
    assert portfolio.task_ids == ("TASK-A",)


@pytest.mark.asyncio
async def test_real_source_refuses_uncommitted_pm_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, executor = await _committed_pm_owner(tmp_path, monkeypatch)
    changed = _synthetic_pm_tasks()
    changed[0]["goal"] = "Uncommitted caller replacement"
    with pytest.raises(RuntimeError, match="pm_document_mismatch"):
        await _capture_real_owner(tmp_path, run, executor, pm_tasks=changed)


@pytest.mark.asyncio
async def test_factory_host_capture_uses_real_pm_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, executor = await _committed_pm_owner(tmp_path, monkeypatch)
    assert hasattr(executor, "_capture_chief_engineer_portfolio_authority"), "Factory host capture boundary is missing"
    authority, carrier = await executor._capture_chief_engineer_portfolio_authority(
        run=run,
        pm_tasks=_synthetic_pm_tasks(),
        portfolio_tasks=executor._chief_engineer_portfolio_tasks(_synthetic_pm_tasks()),
    )
    assert authority.project_id == "synthetic-project"
    assert getattr(carrier, "project_id", None) == "synthetic-project"
    assert getattr(carrier, "run_id", None) == run.id
    assert getattr(carrier, "pm_contract_hash", None) == authority.pm_contract_hash
    assert getattr(carrier, "pm_stage_event_id", None) == authority.pm_stage_event_id
    assert authority.pm_task_ids == ("TASK-A",)


@pytest.mark.asyncio
async def test_new_interpreter_recaptures_original_store_and_rejects_old_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, executor = await _committed_pm_owner(tmp_path, monkeypatch)
    tasks = executor._chief_engineer_portfolio_tasks(_synthetic_pm_tasks())
    _, carrier = await executor._capture_chief_engineer_portfolio_authority(
        run=run,
        pm_tasks=_synthetic_pm_tasks(),
        portfolio_tasks=tasks,
    )
    portfolio = executor._build_chief_engineer_portfolio_from_candidate(
        run_id=run.id,
        tasks=tasks,
        authority_carrier=carrier,
        structured_output=_payload(),
    )
    assert "authority_signature" not in json.dumps(portfolio.to_dict())
    child = r"""import asyncio,json,sys
from pathlib import Path
from polaris.cells.chief_engineer.blueprint import public as ce
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryRunService
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests.test_ce_host_authority_capture import _synthetic_pm_tasks
from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import _payload
async def main():
    workspace=Path(sys.argv[1]);run=await FactoryRunService(workspace,read_only=True).get_run(sys.argv[2]);assert run is not None
    owner=OrchestrationStageExecutor(workspace);tasks=owner._chief_engineer_portfolio_tasks(_synthetic_pm_tasks())
    authority,carrier=await owner._capture_chief_engineer_portfolio_authority(run=run,pm_tasks=_synthetic_pm_tasks(),portfolio_tasks=tasks)
    fresh=getattr(carrier, "authority_signature", None);assert fresh!=sys.argv[3]
    object.__setattr__(carrier,"authority_signature",sys.argv[3])
    try:
        owner._build_chief_engineer_portfolio_from_candidate(run_id=run.id,tasks=tasks,authority_carrier=carrier,structured_output=_payload(),revalidate_existing=True)
        raise AssertionError("old process signature accepted")
    except ce.ChiefEngineerBlueprintErrorV1 as error:
        assert error.code=="invalid_project_completion_authority_carrier"
    object.__setattr__(carrier,"authority_signature",fresh)
    recovered=owner._build_chief_engineer_portfolio_from_candidate(run_id=run.id,tasks=tasks,authority_carrier=carrier,structured_output=_payload(),revalidate_existing=True)
    print(json.dumps({"project_id":recovered.project_completion_contract.project_id,"pm_contract_hash":authority.pm_contract_hash,"portfolio_hash":recovered.portfolio_hash,"old_signature_rejected":True}))
asyncio.run(main())
"""
    signature = getattr(carrier, "authority_signature", None)
    assert isinstance(signature, str)
    process = subprocess.run(
        [
            "rtk",
            "proxy",
            sys.executable,
            "-B",
            "-c",
            child,
            str(tmp_path),
            run.id,
            signature,
        ],
        cwd=Path.cwd(),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    recovered = json.loads(process.stdout.splitlines()[-1])
    assert recovered == {
        "project_id": "synthetic-project",
        "pm_contract_hash": getattr(carrier, "pm_contract_hash", None),
        "portfolio_hash": portfolio.portfolio_hash,
        "old_signature_rejected": True,
    }
    assert all(not (tmp_path / path).exists() for path in ("src/main.py", "tests/test_main.py", "pyproject.toml"))
