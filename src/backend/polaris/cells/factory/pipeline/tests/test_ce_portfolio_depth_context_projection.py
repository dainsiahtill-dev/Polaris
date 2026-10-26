"""CE objective, contract and prompt share current formal delivery-depth facts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor

MINIMUMS = {
    "min_prod_files": 3,
    "min_prod_lines": 120,
    "min_behavior_symbols": 6,
    "min_branch_count": 3,
    "min_test_files": 1,
    "min_test_assertions": 2,
    "min_behavior_rules": 3,
    "min_primary_entities": 3,
    "min_edge_cases": 2,
}


def _task() -> dict[str, Any]:
    return {
        "id": "TASK-1",
        "schema_version": "pm.task_intent.v1",
        "title": "Implement the requested application",
        "goal": "Deliver runnable behavior with normal, boundary and invalid input checks",
        "acceptance": ["All original observable acceptance remains required"],
        "product_goal": "Deliver the requested application",
        "required_requirements": ["Runnable behavior and core rule tests"],
        "product_acceptance": ["All original observable acceptance remains required"],
        "depends_on": [],
        "metadata": {"topology_authority": "chief_engineer", "planning_contract_version": "pm.task_intent.v1"},
    }


def _contract(**changes: Any) -> dict[str, Any]:
    return {
        "schema_version": "polaris.delivery_depth_contract.v1",
        "level": 1,
        "minimums": dict(MINIMUMS),
        "required_evidence": [
            "behavior rules",
            "module and API blueprint",
            "core and I/O separation",
            "business assertions",
            "implementation depth",
        ],
        "anti_hollow_delivery": ["No keyword stuffing", "No existence-only tests", "No overriding QA findings"],
        "product_intent": {"subject": "Requested application", "primary_entities": ["EntityA", "EntityB"]},
        **changes,
    }


def _context(contract: Any) -> dict[str, Any]:
    return {"factory_run_id": "factory-current", "metadata": {"delivery_depth_contract": contract}}


def _objective_minimums(objective: str) -> dict[str, int] | None:
    marker = "Authoritative portfolio depth minimums: "
    if marker not in objective:
        return None
    return json.JSONDecoder().raw_decode(objective.split(marker, 1)[1])[0]


def test_current_formal_contract_reaches_all_ce_projections_without_catalog(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    tasks = [_task()]
    context = _context(_contract())
    before = deepcopy((tasks, context))
    resolved = executor._workspace_quality._load_delivery_depth_level_contract(context)
    assert resolved is not None and resolved["minimums"] == MINIMUMS

    objective = executor._chief_engineer_portfolio_objective(tasks, request_context=context)
    prompt = executor._chief_engineer_portfolio_context(tasks, run_id="factory-current", request_context=context)
    portfolio = executor._chief_engineer_portfolio_tasks(tasks, request_context=context)

    assert _objective_minimums(objective) == MINIMUMS
    assert prompt["portfolio_tasks"][0]["delivery_depth_contract"]["minimums"] == MINIMUMS
    assert dict(portfolio[0].delivery_depth_contract)["minimums"] == MINIMUMS
    for key in ("required_evidence", "anti_hollow_delivery", "product_intent"):
        assert (
            prompt["portfolio_tasks"][0]["delivery_depth_contract"][key]
            == context["metadata"]["delivery_depth_contract"][key]
        )
        assert dict(portfolio[0].delivery_depth_contract)[key] == context["metadata"]["delivery_depth_contract"][key]
    assert prompt["pm_task_contract"]["tasks"] == tasks
    assert (tasks, context) == before
    assert not (tmp_path / ".polaris" / "catalog_contract.json").exists()


@pytest.mark.parametrize("minimums", [dict.fromkeys(MINIMUMS, 0), {"min_prod_files": 0}])
def test_explicit_zero_is_preserved_without_inventing_other_minimums(tmp_path: Path, minimums: dict[str, int]) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    context = _context(_contract(minimums=minimums))
    assert (
        _objective_minimums(executor._chief_engineer_portfolio_objective([_task()], request_context=context))
        == minimums
    )


def test_absent_contract_does_not_advertise_authoritative_zero_minimums(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    assert _objective_minimums(executor._chief_engineer_portfolio_objective([_task()])) is None


@pytest.mark.parametrize("location", ["context", "metadata"])
@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        [],
        "contract",
        {"minimums": {}},
        {"minimums": {"min_prod_files": True}},
        {"minimums": {"min_prod_files": -1}},
        {"minimums": {"min_prod_files": "3"}},
        {"minimums": {"min_typo": 3}},
    ],
)
def test_malformed_current_contract_fails_before_ce_prompt(tmp_path: Path, invalid: Any, location: str) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    context = {"delivery_depth_contract": invalid} if location == "context" else _context(invalid)
    with pytest.raises(ValueError):
        executor._chief_engineer_portfolio_objective([_task()], request_context=context)


def test_conflicting_current_projections_cannot_select_weaker_minimums(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    context = _context(_contract())
    context["delivery_depth_contract"] = _contract(minimums=dict.fromkeys(MINIMUMS, 0))
    with pytest.raises(ValueError, match="conflicting"):
        executor._chief_engineer_portfolio_objective([_task()], request_context=context)


def test_task_contract_cannot_conflict_with_current_run_minimums(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    task = _task()
    task["delivery_depth_contract"] = _contract(minimums={"min_prod_files": 0})
    with pytest.raises(ValueError, match="conflicting"):
        executor._chief_engineer_portfolio_tasks([task], request_context=_context(_contract()))


def test_legacy_catalog_remains_readable_only_when_current_contract_absent(tmp_path: Path) -> None:
    legacy = tmp_path / ".polaris" / "catalog_contract.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"level": 1, "level_contract": {"minimums": MINIMUMS}}), encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    assert _objective_minimums(executor._chief_engineer_portfolio_objective([_task()])) == MINIMUMS
    zeros = dict.fromkeys(MINIMUMS, 0)
    current = _context(_contract(minimums=zeros))
    assert (
        _objective_minimums(executor._chief_engineer_portfolio_objective([_task()], request_context=current)) == zeros
    )
    prompt = executor._chief_engineer_portfolio_context([_task()], run_id="factory-current", request_context=current)
    assert prompt["portfolio_tasks"][0]["delivery_depth_contract"]["minimums"] == zeros


def test_nested_current_minimums_reach_objective_without_prose_inference(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    context = _context(
        {
            "schema_version": "polaris.delivery_depth_contract.v1",
            "level_contract": {"level": 1, "minimums": dict(MINIMUMS)},
        }
    )
    assert (
        _objective_minimums(executor._chief_engineer_portfolio_objective([_task()], request_context=context))
        == MINIMUMS
    )


def test_ce_depth_projection_does_not_clone_live_runtime_ports(tmp_path: Path) -> None:
    class RuntimePort:
        def __deepcopy__(self, _memo: dict[int, Any]) -> Any:
            raise AssertionError("live runtime ports cannot be deep-copied")

    executor = OrchestrationStageExecutor(tmp_path)
    context = _context(_contract())
    context["runtime_port"] = RuntimePort()
    assert (
        _objective_minimums(executor._chief_engineer_portfolio_objective([_task()], request_context=context))
        == MINIMUMS
    )


@pytest.mark.asyncio
async def test_current_depth_reaches_runtime_boundary_with_isolated_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.factory.pipeline.tests import test_ce_deferred_command_authority as capture
    from polaris.cells.factory.pipeline.tests._characterization_helpers import _factory_stage_context
    from polaris.cells.factory.pipeline.tests.test_semantic_pm_ce_handoff import _intent

    # This fixture isolates authority and proves only the runtime boundary.
    # Real persisted PM authority is covered by the regression tests below.
    stage_context = _factory_stage_context()
    stage_context["metadata"] = {"delivery_depth_contract": _contract()}
    monkeypatch.setattr(capture, "_factory_stage_context", lambda: stage_context)
    command = await capture._capture_bootstrap(tmp_path, monkeypatch, _intent())
    assert _objective_minimums(command.objective) == MINIMUMS
    depth = command.context["portfolio_tasks"][0]["delivery_depth_contract"]
    assert depth["minimums"] == MINIMUMS
    for key in ("required_evidence", "anti_hollow_delivery", "product_intent"):
        assert depth[key] == stage_context["metadata"]["delivery_depth_contract"][key]


def test_descriptive_current_context_preserves_explicit_task_minimums(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    task = _task()
    task["delivery_depth_contract"] = _contract()
    current = _context(
        {"schema_version": "polaris.delivery_depth_contract.v1", "product_intent": {"subject": "Current product"}}
    )
    objective = executor._chief_engineer_portfolio_objective([task], request_context=current)
    prompt = executor._chief_engineer_portfolio_context([task], run_id="factory-current", request_context=current)
    assert _objective_minimums(objective) == MINIMUMS
    assert prompt["portfolio_tasks"][0]["delivery_depth_contract"]["product_intent"] == {"subject": "Current product"}


def _persisted_depth_context(run: Any) -> dict[str, Any]:
    return {
        "factory_run_id": run.id,
        "metadata": deepcopy(run.metadata["factory_start_request"]["metadata"]),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("cold_reload", [False, True])
async def test_legacy_pm_commit_and_cold_authority_rebuild_preserve_recorded_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cold_reload: bool
) -> None:
    from polaris.cells.factory.pipeline.internal import factory_stage_executor as stage_module
    from polaris.cells.factory.pipeline.internal.factory_stage_executor._mixin_01 import _Mixin01
    from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
    from polaris.cells.factory.pipeline.tests.test_formal_project_identity import PROJECT, TASK, _persisted_legacy_owner
    from polaris.kernelone.storage import resolve_storage_roots

    # Historical record inputs are explicitly synthetic; no current admission
    # or PM role executes. Existing FactoryStore commit, PM binding and this
    # actual readonly authority method stay real.
    run, executor, tasks = await _persisted_legacy_owner(
        tmp_path, monkeypatch, metadata={"project_id": PROJECT, "delivery_depth_contract": _contract()}
    )
    runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
    store = FactoryStore(runtime / "factory", create_root=False)
    if cold_reload:
        recovered = await store.get_run(run.id)
        assert recovered is not None
        run = recovered
        executor = OrchestrationStageExecutor(tmp_path)
    assert (
        executor._load_chief_engineer_portfolio_authority.__func__ is _Mixin01._load_chief_engineer_portfolio_authority
    )
    plan = runtime / "tasks/plan.json"
    before = plan.read_bytes()
    current = _persisted_depth_context(run)
    assert executor._workspace_quality._load_delivery_depth_level_contract(current)["minimums"] == MINIMUMS
    projected = executor._chief_engineer_portfolio_tasks(tasks, request_context=current)
    authority = await executor._load_chief_engineer_portfolio_authority(
        run=run, pm_tasks=tasks, portfolio_tasks=projected
    )
    events = await store.get_authoritative_events(run.id)
    persistence = stage_module.reduce_factory_stage_persistence(events, factory_run_id=run.id)
    commit = next(item for item in reversed(persistence.commits) if item.stage == "pm_planning")
    assert authority.pm_stage_event_id == commit.stage_completed_event_id
    assert authority.project_id == PROJECT and authority.pm_task_ids == (TASK,)
    assert authority.pm_contract_hash != "a" * 64
    assert dict(projected[0].delivery_depth_contract)["minimums"] == MINIMUMS
    assert plan.read_bytes() == before
    assert not (tmp_path / ".polaris/catalog_contract.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["pm_document", "incoming_depth", "caller_admitted_depth", "current_depth_conflict"])
async def test_legacy_authority_still_rejects_tampered_pm_or_depth_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    from polaris.cells.factory.pipeline.tests.test_formal_project_identity import PROJECT, _persisted_legacy_owner

    run, executor, tasks = await _persisted_legacy_owner(
        tmp_path, monkeypatch, metadata={"project_id": PROJECT, "delivery_depth_contract": _contract()}
    )
    current = _persisted_depth_context(run)
    error = "pm_path_authority_mismatch"
    expected: type[Exception] = RuntimeError
    if tamper == "pm_document":
        tasks = deepcopy(tasks)
        tasks[0]["goal"] = "Uncommitted changed product intent"
        error = "pm_document_mismatch"
    elif tamper == "incoming_depth":
        current["metadata"]["delivery_depth_contract"] = _contract(minimums=dict.fromkeys(MINIMUMS, 0))
    elif tamper == "caller_admitted_depth":
        run.metadata["factory_start_request"]["metadata"]["delivery_depth_contract"] = _contract(
            minimums=dict.fromkeys(MINIMUMS, 0)
        )
        current = _persisted_depth_context(run)
    elif tamper == "current_depth_conflict":
        run.metadata["delivery_depth_contract"] = _contract(minimums=dict.fromkeys(MINIMUMS, 0))
        expected = ValueError
        error = "conflicting"
    projected = executor._chief_engineer_portfolio_tasks(tasks, request_context=current)
    with pytest.raises(expected, match=error):
        await executor._load_chief_engineer_portfolio_authority(run=run, pm_tasks=tasks, portfolio_tasks=projected)
