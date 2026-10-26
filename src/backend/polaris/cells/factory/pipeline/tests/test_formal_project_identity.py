"""Historical project identity survives decoding and explicit CE technical tools.

Synthetic old records use actual persistence and artifact proof producers;
no retired HTTP admission, execution controller or role is invoked. Decoding
must leave persisted bytes unchanged. CE artifact generation is explicit.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
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
from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_provenance import (
    _pm_document,
)
from polaris.cells.orchestration.pm_planning.public.task_intent import (
    normalize_pm_task_intent,
)
from polaris.kernelone.fs.text_ops import write_text_atomic

PROJECT = "opaque-project.q7"
TASK = "opaque-unit.4f"


def _task() -> dict[str, Any]:
    return normalize_pm_task_intent(
        {
            "schema_version": "pm.task_intent.v1",
            "product_goal": "Deliver deterministic CLI output",
            "required_requirements": ["CLI accepts a name and prints a deterministic greeting"],
            "optional_requirements": [],
            "acceptance": ["CLI accepts a name and prints a deterministic greeting"],
            "explicit_constraints": ["Keep data inside the workspace"],
            "tasks": [
                {
                    "id": TASK,
                    "title": "Implement CLI",
                    "goal": "Deliver deterministic CLI output",
                    "acceptance": ["CLI accepts a name and prints a deterministic greeting"],
                    "depends_on": [],
                }
            ],
        }
    )[0]


def _persisted_bytes(workspace: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(workspace)): path.read_bytes() if path.is_file() else None for path in workspace.rglob("*")
    }


@contextmanager
def _unchanged_workspace(workspace: Path) -> Iterator[None]:
    before = _persisted_bytes(workspace)
    try:
        yield
    finally:
        assert _persisted_bytes(workspace) == before


async def _persisted_legacy_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, metadata: dict[str, Any]
) -> tuple[Any, OrchestrationStageExecutor, list[dict[str, Any]]]:
    """Seed a labeled historical input, never exercise old run admission."""
    tasks = [_task()]
    executor = OrchestrationStageExecutor(tmp_path)
    from polaris.cells.factory.pipeline.internal.factory_stage_artifact_bindings import (
        build_pm_stage_artifact_bindings,
    )
    from polaris.cells.factory.pipeline.internal.factory_stage_persistence import (
        reduce_factory_stage_persistence,
    )
    from polaris.kernelone.storage import resolve_storage_roots

    service: FactoryRunService = FactoryRunService(
        tmp_path,
        stage_artifact_binding_builder=lambda run_id, _result: build_pm_stage_artifact_bindings(
            factory_store=service.store,
            source_root=Path(resolve_storage_roots(str(tmp_path)).runtime_root),
            factory_run_id=run_id,
        ),
    )

    async def no_publish(_run_id: str, _event: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(service, "_publish_factory_event", no_publish)
    run = FactoryRun(
        id="synthetic-historical-formal-identity",
        config=FactoryConfig(name="Factory Run - pm", stages=["pm_planning"]),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-26T00:00:00+00:00",
        metadata={
            **copy.deepcopy(metadata),
            "fixture_origin": "synthetic_historical_input_not_current_http_admission",
            "factory_start_request": {
                "workspace": str(tmp_path),
                "start_from": "pm",
                "directive": "Private exact original acceptance",
                "persist_workspace": False,
                "metadata": copy.deepcopy(metadata),
            },
        },
    )
    admission = FactoryRunAdmissionV1(
        factory_run_id=run.id,
        created_at=run.created_at,
        name=run.config.name,
        description=run.config.description,
    )
    await service._append_event(run.id, build_factory_run_admitted_event(admission), publish=False)
    await service.store.save_run(run)
    await service._mark_stage_started(run, "pm_planning", "2026-10-26T00:00:01+00:00")
    executor._write_json_artifact("tasks/plan.json", _pm_document(tasks))
    result = StageResult(
        stage="pm_planning",
        status="success",
        output="Explicit synthetic historical PM input; no role execution",
        started_at="2026-10-26T00:00:01+00:00",
        completed_at="2026-10-26T00:00:02+00:00",
    )
    await service._mark_stage_finished(run, result)
    assert result.status == "success", result.output
    events = await service.store.get_authoritative_events(run.id)
    persisted = reduce_factory_stage_persistence(events, factory_run_id=run.id)
    assert len(persisted.commits) == 1 and persisted.commits[0].stage == "pm_planning"
    with _unchanged_workspace(tmp_path):
        loaded = await FactoryRunService(tmp_path, read_only=True).get_run(run.id)
    assert loaded is not None
    assert loaded.metadata["factory_start_request"]["metadata"] == metadata
    assert not (tmp_path / ".polaris" / "catalog_contract.json").exists()
    return loaded, OrchestrationStageExecutor(tmp_path), tasks


@pytest.mark.asyncio
async def test_legacy_persisted_project_reaches_explicit_ce_tool_without_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, executor, tasks = await _persisted_legacy_owner(tmp_path, monkeypatch, metadata={"project_id": PROJECT})
    with _unchanged_workspace(tmp_path):
        authority, carrier = await executor._capture_chief_engineer_portfolio_authority(
            run=run,
            pm_tasks=tasks,
            portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks),
        )
    assert authority.project_id == PROJECT
    assert authority.pm_task_ids == (TASK,)
    from polaris.cells.chief_engineer.blueprint.public import (
        GenerateTaskBlueprintCommandV1,
        generate_task_blueprint,
        project_chief_engineer_task_blueprint,
    )
    from polaris.cells.chief_engineer.blueprint.public.service import (
        BlueprintPersistence,
    )
    from polaris.cells.chief_engineer.blueprint.public.tests.portfolio_payload_fixture import (
        synthetic_task_intent_portfolio_payload as _payload,
    )

    candidate = _payload()
    candidate["construction_plan"]["task_plans"][TASK] = candidate["construction_plan"]["task_plans"].pop("TASK-A")
    for rows in candidate["project_completion_contract"]["obligations"].values():
        for row in rows:
            row["owner_task_id"] = TASK
    portfolio = executor._build_chief_engineer_portfolio_from_candidate(
        run_id=run.id,
        tasks=executor._chief_engineer_portfolio_tasks(tasks),
        authority_carrier=carrier,
        structured_output=candidate,
    )
    contract = portfolio.project_completion_contract
    assert contract is not None and contract.project_id == PROJECT and contract.run_id == run.id
    # A newer mutable context hint cannot rebind this frozen completion authority.
    result = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=TASK,
            workspace=str(tmp_path),
            run_id=run.id,
            objective=tasks[0]["goal"],
            context={
                **portfolio.to_task_blueprint_context(),
                "pm_task_contract": tasks[0],
                "project_id": "foreign-context",
            },
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, TASK),
        )
    )
    assert result.blueprint_id is not None
    blueprint = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
    assert blueprint is not None
    assert blueprint["job_token"]["project_id"] == PROJECT
    assert set(blueprint["job_token"]["allowed_write_paths"]) == {
        "src/main.py",
        "tests/test_main.py",
        "pyproject.toml",
    }
    assert len(blueprint["job_token"]["allowed_write_paths"]) == 3
    assert authority.project_id == PROJECT
    assert not (tmp_path / ".polaris" / "catalog_contract.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, "", " bad ", 42, [], "bad\nproject", "x" * 129])
async def test_legacy_explicit_invalid_project_does_not_fall_back_to_display_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: Any
) -> None:
    run, executor, tasks = await _persisted_legacy_owner(tmp_path, monkeypatch, metadata={"project_id": invalid})
    with _unchanged_workspace(tmp_path), pytest.raises(RuntimeError, match=r"project_id_(invalid|missing)"):
        await executor._load_chief_engineer_portfolio_authority(
            run=run,
            pm_tasks=tasks,
            portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks),
        )


@pytest.mark.asyncio
async def test_legacy_current_snapshot_project_cannot_replace_frozen_request_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, executor, tasks = await _persisted_legacy_owner(tmp_path, monkeypatch, metadata={"project_id": PROJECT})
    run.metadata["project_id"] = "foreign-current"
    with _unchanged_workspace(tmp_path), pytest.raises(RuntimeError, match=r"project_id_.*(conflict|mismatch)"):
        await executor._load_chief_engineer_portfolio_authority(
            run=run,
            pm_tasks=tasks,
            portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks),
        )


@pytest.mark.asyncio
async def test_legacy_persisted_project_request_is_bound_to_same_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, executor, tasks = await _persisted_legacy_owner(tmp_path, monkeypatch, metadata={"project_id": PROJECT})
    run.metadata["factory_start_request"]["workspace"] = str(tmp_path / "foreign")
    with _unchanged_workspace(tmp_path), pytest.raises(RuntimeError, match=r"project.*workspace.*mismatch"):
        await executor._load_chief_engineer_portfolio_authority(
            run=run,
            pm_tasks=tasks,
            portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks),
        )


@pytest.mark.asyncio
async def test_legacy_foreign_catalog_cannot_rebind_frozen_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from polaris.cells.factory.pipeline.internal.factory_stage_executor._helpers import (
        _ChiefEngineerPortfolioAuthorityError,
    )

    run, executor, tasks = await _persisted_legacy_owner(tmp_path, monkeypatch, metadata={"project_id": PROJECT})
    catalog = tmp_path / ".polaris" / "catalog_contract.json"
    write_text_atomic(str(catalog), json.dumps({"project_id": "foreign-catalog"}), encoding="utf-8")
    original = executor._chief_engineer_project_kind_authority
    observed: list[str] = []

    def observe_real_kind_authority(**kwargs: Any) -> Any:
        observed.append(kwargs["project_id"])
        return original(**kwargs)

    monkeypatch.setattr(executor, "_chief_engineer_project_kind_authority", observe_real_kind_authority)
    with (
        _unchanged_workspace(tmp_path),
        pytest.raises(_ChiefEngineerPortfolioAuthorityError, match="catalog snapshot project_id") as failure,
    ):
        await executor._load_chief_engineer_portfolio_authority(
            run=run,
            pm_tasks=tasks,
            portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks),
        )
    assert failure.value.code == "chief_engineer.project_completion_project_kind_authority_invalid"
    assert observed == [PROJECT]
    assert run.metadata["factory_start_request"]["metadata"]["project_id"] == PROJECT


@pytest.mark.asyncio
async def test_legacy_direct_owner_project_is_used_when_request_key_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, executor, tasks = await _persisted_legacy_owner(tmp_path, monkeypatch, metadata={})
    run.metadata["project_id"] = PROJECT
    with _unchanged_workspace(tmp_path):
        authority = await executor._load_chief_engineer_portfolio_authority(
            run=run,
            pm_tasks=tasks,
            portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks),
        )
    assert authority.project_id == PROJECT


@pytest.mark.asyncio
async def test_legacy_missing_project_preserves_record_display_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, executor, tasks = await _persisted_legacy_owner(tmp_path, monkeypatch, metadata={})
    with _unchanged_workspace(tmp_path):
        authority = await executor._load_chief_engineer_portfolio_authority(
            run=run,
            pm_tasks=tasks,
            portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks),
        )
    assert authority.project_id == "Factory Run - pm"


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["foreign_run", "stale_pm"])
async def test_legacy_pm_proof_cannot_be_borrowed_for_foreign_run_or_changed_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    from dataclasses import replace

    run, executor, tasks = await _persisted_legacy_owner(tmp_path, monkeypatch, metadata={"project_id": PROJECT})
    if defect == "foreign_run":
        run = replace(run, id="foreign-run")
        error = "current regular run snapshot"
    else:
        tasks = copy.deepcopy(tasks)
        tasks[0]["goal"] = "uncommitted changed goal"
        error = "pm_document_mismatch"
    with _unchanged_workspace(tmp_path), pytest.raises(RuntimeError, match=error):
        await executor._load_chief_engineer_portfolio_authority(
            run=run,
            pm_tasks=tasks,
            portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks),
        )
