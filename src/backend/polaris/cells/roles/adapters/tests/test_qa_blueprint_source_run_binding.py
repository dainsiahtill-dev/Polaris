"""QA child identity must not rebind authenticated CE semantic reads.

Historical PM source is explicit synthetic input from its Factory-owned fixture.
PM artifact persistence, CE authority, portfolio, persistence and provenance are real.
Only QA provider output and unrelated progress/TaskBoard transport are canned.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    GenerateTaskBlueprintCommandV1,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
)
from polaris.cells.chief_engineer.blueprint.public.tests.portfolio_payload_fixture import (
    synthetic_task_intent_portfolio_payload,
)
from polaris.cells.factory.pipeline.tests.legacy_pm_owner_scenarios import (
    LEGACY_PROJECT_ID as PROJECT,
    LEGACY_TASK_ID as TASK,
    produce_synthetic_legacy_ce_portfolio,
)
from polaris.cells.roles.adapters.internal.qa_adapter import QAAdapter, _bind_qa_transaction_execution_identity
from polaris.cells.roles.kernel.tests.adapters_kernel_boundary_scenarios import observe_review_source_request
from polaris.kernelone.storage import resolve_runtime_path

QA_RUN = "qa-private-source-binding"
DIRECTOR_RUN = "director-private-source-binding"


async def _source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[QAAdapter, dict[str, Any]]:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "private-home"))
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "0")
    candidate = synthetic_task_intent_portfolio_payload()
    candidate["construction_plan"]["task_plans"][TASK] = candidate["construction_plan"]["task_plans"].pop("TASK-A")
    for rows in candidate["project_completion_contract"]["obligations"].values():
        for row in rows:
            row["owner_task_id"] = TASK
    source = await produce_synthetic_legacy_ce_portfolio(
        tmp_path,
        monkeypatch,
        structured_output=candidate,
        project_id=PROJECT,
    )
    factory_run_id = source["factory_run_id"]
    tasks = source["pm_tasks"]
    portfolio = source["portfolio"]
    generated = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=TASK,
            workspace=str(tmp_path),
            run_id=factory_run_id,
            objective=tasks[0]["goal"],
            context={**portfolio.to_task_blueprint_context(), "pm_task_contract": tasks[0]},
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, TASK),
        )
    )
    contract = portfolio.project_completion_contract
    assert contract is not None
    targets = [item.path for item in contract.obligations.artifacts if item.applicability == "required"]
    for target in targets:
        path = tmp_path / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 7\n" if path.suffix == ".py" else "[project]\nname = 'private-qa'\n", encoding="utf-8")
    parent = {
        "run_id": DIRECTOR_RUN,
        "metadata": {"factory_run_id": factory_run_id},
        "target_files": targets,
        "pm_task_contracts": tasks,
        "chief_engineer_blueprint": {
            "factory_run_id": factory_run_id,
            "portfolio": {"portfolio_hash": portfolio.portfolio_hash},
            "blueprints": [
                {
                    "task_id": TASK,
                    "blueprint_id": generated.blueprint_id,
                    "blueprint_path": generated.blueprint_path,
                }
            ],
        },
    }
    return QAAdapter(workspace=str(tmp_path)), parent


def _bound(parent: dict[str, Any], *, run_id: str = QA_RUN) -> dict[str, Any]:
    return _bind_qa_transaction_execution_identity(
        task_id="qa-private", run_id=run_id, review_type="quality_gate", parent_context=parent, stage_label="judgement"
    )


@pytest.mark.asyncio
async def test_execute_keeps_child_identity_and_real_factory_evidence_through_json_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, parent = await _source(tmp_path, monkeypatch)
    parent["run_id"] = QA_RUN
    calls: list[dict[str, Any]] = []

    async def canned_provider(**kwargs: Any) -> dict[str, Any]:
        context = kwargs["context"]
        calls.append(deepcopy(kwargs))
        assert context["run_id"] == QA_RUN
        assert "factory_run_id" not in context and "factory_run_id" not in context["metadata"]
        ce = context["context_evidence_slots"]["ce_blueprint"]
        assert ce["blueprints"][0]["task_id"] == TASK
        assert ce["source_observations"][0]["provenance"]["factory_run_id"] == parent["metadata"]["factory_run_id"]
        # Exercise the real final request evidence-message assembly locally.
        messages = observe_review_source_request(
            [{"role": "system", "content": "QA independent reviewer"}, {"role": "user", "content": kwargs["message"]}],
            context,
        )["messages"]
        rendered = json.dumps(messages, ensure_ascii=False)
        assert ce["source_observations"][0]["content_sha256"] in rendered
        assert "source_context" not in rendered
        return {"response": "invalid" if len(calls) == 1 else '{"verdict":"FAIL","critical_issues":["private QA"]}'}

    monkeypatch.setattr(adapter, "_call_role_llm", canned_provider)
    monkeypatch.setattr(adapter, "_update_task_progress", lambda *_args: None)
    monkeypatch.setattr(adapter, "_apply_taskboard_qa_verdict", lambda **_kwargs: {})
    before = deepcopy(parent)
    result = await adapter.execute("qa-private", {"review_target": "Original required behavior"}, parent)
    assert len(calls) == 2, result
    assert result["passed"] is False
    assert (
        calls[0]["context"]["context_evidence_slots"]["ce_blueprint"]
        == calls[1]["context"]["context_evidence_slots"]["ce_blueprint"]
    )
    assert parent == before


def _files(workspace: Path) -> dict[str, str]:
    return {
        str(path.relative_to(workspace)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in workspace.rglob("*")
        if path.is_file()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["direct", "metadata", "both", "legacy", "legacy_none_metadata"])
async def test_factory_owner_queries_and_cold_input_keep_all_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    from polaris.cells.chief_engineer.blueprint import public as blueprint_owner

    adapter, parent = await _source(tmp_path, monkeypatch)
    factory = parent["metadata"].pop("factory_run_id")
    if location in {"direct", "both"}:
        parent["factory_run_id"] = factory
    if location in {"metadata", "both"}:
        parent["metadata"]["factory_run_id"] = factory
    if location.startswith("legacy"):
        parent["run_id"] = factory
    if location == "legacy_none_metadata":
        parent["metadata"] = None
    calls: list[tuple[str, str]] = []
    for name, field in (
        ("get_blueprint_status", "run_id"),
        ("query_blueprint_provenance", "expected_factory_run_id"),
        ("query_project_completion_contract", "run_id"),
    ):
        original = getattr(blueprint_owner, name)

        def observe(query: Any, *, original: Any = original, name: str = name, field: str = field) -> Any:
            calls.append((name, getattr(query, field)))
            return original(query)

        monkeypatch.setattr(blueprint_owner, name, observe)
    bound = _bound(parent)
    before_parent, before_bound, before_files = deepcopy(parent), deepcopy(bound), _files(tmp_path)
    evidence = adapter._build_qa_blueprint_evidence(bound, source_context=parent)
    assert len(evidence["blueprints"]) == 1
    assert len(calls) == 3 and {value for _, value in calls} == {factory}
    assert len({factory, QA_RUN, DIRECTOR_RUN}) == 3
    assert bound["run_id"] == QA_RUN
    assert bound == before_bound and parent == before_parent
    cold = QAAdapter(workspace=str(tmp_path))
    assert (
        cold._build_qa_blueprint_evidence(
            _bound(json.loads(json.dumps(parent))), source_context=json.loads(json.dumps(parent))
        )
        == evidence
    )
    assert len(calls) == 6 and {value for _, value in calls} == {factory}
    assert _files(tmp_path) == before_files
    if location.startswith("legacy"):
        # Existing direct root callers require no new argument or authority.
        assert adapter._build_qa_blueprint_evidence(parent) == evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "empty",
        "none",
        "number",
        "mapping",
        "whitespace",
        "inner_whitespace",
        "conflict",
        "foreign",
        "empty_source",
    ],
)
async def test_invalid_parent_factory_binding_never_uses_review_as_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    from polaris.cells.chief_engineer.blueprint import public as blueprint_owner

    adapter, parent = await _source(tmp_path, monkeypatch)
    factory = parent["metadata"]["factory_run_id"]
    values: dict[str, Any] = {
        "empty": "",
        "none": None,
        "number": 7,
        "mapping": {},
        "whitespace": f" {factory} ",
        "inner_whitespace": "foreign\nowner",
        "foreign": "foreign-factory",
    }
    if invalid == "missing":
        parent["metadata"].pop("factory_run_id")
    elif invalid == "conflict":
        parent["factory_run_id"] = "foreign-factory"
    elif invalid != "empty_source":
        parent["metadata"]["factory_run_id"] = values[invalid]
    bound = _bound(parent)
    calls = []
    original = blueprint_owner.get_blueprint_status

    def observe(query: Any) -> Any:
        calls.append(query)
        return original(query)

    monkeypatch.setattr(blueprint_owner, "get_blueprint_status", observe)
    before = _files(tmp_path)
    with pytest.raises(ValueError, match="qa_blueprint_run_binding_invalid"):
        adapter._build_qa_blueprint_evidence(bound, source_context={} if invalid == "empty_source" else parent)
    assert not calls and _files(tmp_path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["pm", "path", "hash", "portfolio", "review_factory", "pm_owner"])
async def test_factory_selection_keeps_real_provenance_and_artifact_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    adapter, parent = await _source(tmp_path, monkeypatch)
    bound = _bound(parent)
    review = bound["chief_engineer_blueprint"]
    if tamper == "pm":
        bound["pm_task_contracts"][0]["goal"] = "foreign goal"
    elif tamper == "pm_owner":
        bound["pm_task_contracts"][0]["id"] = "foreign-task"
        bound["pm_task_contracts"][0]["task_id"] = "foreign-task"
    elif tamper == "path":
        review["blueprints"][0]["blueprint_path"] = "runtime/blueprints/foreign.json"
    elif tamper == "hash":
        path = Path(resolve_runtime_path(str(tmp_path), review["blueprints"][0]["blueprint_path"], read_only=True))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["blueprint_hash"] = "0" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif tamper == "portfolio":
        review["portfolio"]["portfolio_hash"] = "0" * 64
    else:
        review["factory_run_id"] = "foreign-factory"
    before = _files(tmp_path)
    with pytest.raises((ValueError, RuntimeError)):
        adapter._build_qa_blueprint_evidence(bound, source_context=parent)
    assert _files(tmp_path) == before
