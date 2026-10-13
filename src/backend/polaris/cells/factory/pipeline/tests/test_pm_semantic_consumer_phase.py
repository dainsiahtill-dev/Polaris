"""PM intent survives Factory admission; only CE handoff grants execution."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_stage_executor._mixin_00 import _Mixin00
from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_pm_artifact_contracts
from polaris.cells.orchestration.pm_planning.public.task_intent import normalize_pm_task_intent


def _tasks():
    return normalize_pm_task_intent(
        {
            "schema_version": "pm.task_intent.v1",
            "product_goal": "Deliver an executable local application",
            "required_requirements": ["Provide a runnable entry and automated core checks"],
            "optional_requirements": [],
            "acceptance": ["The application runs and core checks pass"],
            "explicit_constraints": [],
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Implement application",
                    "goal": "Deliver runnable behavior",
                    "acceptance": ["Core behavior is verified"],
                    "depends_on": [],
                }
            ],
        }
    )


class _Consumer(_Mixin00):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.workspace = root

    def _artifact_path(self, relative_path):
        return self.root / relative_path

    def _read_catalog_contract(self):
        return {"primary_language": "python"}

    def _write_json_artifact(self, relative_path, payload):
        path = self._artifact_path(relative_path)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path


def test_factory_revalidates_semantic_core_without_inventing_technical_fields(tmp_path):
    tasks = _tasks()
    plan = tmp_path / "tasks" / "plan.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    consumer = _Consumer(tmp_path)
    assert consumer._validate_pm_plan_contract() == ""
    assert consumer._validate_pm_plan_artifact_contract() == ""
    assert consumer._validate_pm_plan_language_consistency() == ""
    result = consumer._enrich_pm_plan_contract_artifact()
    assert "contract_diagnostics" not in result
    assert result["declared_target_count"] == 0
    retained = json.loads(plan.read_text(encoding="utf-8"))["tasks"][0]
    for field in ("scope_paths", "target_files", "context_files", "verification_commands"):
        assert retained[field] == []
    assert retained["acceptance"] == tasks[0]["acceptance"]


def test_semantic_planning_does_not_admit_empty_execution_ownership():
    tasks = _tasks()
    planned = validate_pm_artifact_contracts(tasks, validation_phase="pm_planning")
    assert planned.ok, planned.diagnostics
    assert planned.authored_targets == ()
    executed = validate_pm_artifact_contracts(tasks)
    assert not executed.ok
    assert any(row["code"] == "artifact_action_contract_invalid" for row in executed.diagnostics)


@pytest.mark.parametrize(
    "defect",
    [
        "metadata_only",
        "empty_core",
        "verification_authority",
        "explicit_fields",
        "execution_backend",
        "acceptance_alias",
        "mixed",
    ],
)
def test_forged_semantic_phase_markers_fail_closed(defect):
    tasks = _tasks()
    if defect == "metadata_only":
        tasks[0].pop("schema_version")
    elif defect == "empty_core":
        tasks[0]["required_requirements"] = []
    elif defect == "verification_authority":
        tasks[0]["metadata"]["verification_authority"] = "director"
    elif defect == "explicit_fields":
        tasks[0]["metadata"]["explicit_technical_fields"] = ["command"]
    elif defect == "execution_backend":
        tasks[0]["execution_backend"] = "execute_only"
    elif defect == "acceptance_alias":
        tasks[0]["observable_acceptance"] = ["An unrelated result"]
    elif defect == "mixed":
        tasks.append({"id": "legacy", "target_files": ["src/main.py"], "depends_on": []})
    result = validate_pm_artifact_contracts(tasks, validation_phase="pm_planning")
    assert not result.ok
    assert any(row["code"] == "pm_task_intent_invalid" for row in result.diagnostics)


def test_legacy_empty_targets_remain_rejected_during_planning():
    result = validate_pm_artifact_contracts(
        [{"id": "legacy", "target_files": [], "depends_on": []}], validation_phase="pm_planning"
    )
    assert not result.ok
    assert result.diagnostics[0]["code"] == "artifact_action_contract_invalid"


def test_explicit_unsafe_verifier_is_rejected_during_planning():
    tasks = _tasks()
    tasks[0]["verification_commands"] = [{"modality": "test", "argv": ["bash", "-lc", "true"], "cwd": "."}]
    result = validate_pm_artifact_contracts(tasks, validation_phase="pm_planning")
    assert not result.ok


def test_phase_does_not_rewrite_semantic_declarations():
    tasks = _tasks()
    original = deepcopy(tasks)
    assert validate_pm_artifact_contracts(tasks, validation_phase="pm_planning").ok
    assert tasks == original


def test_execution_delivery_check_requires_same_run_ce_handoff(tmp_path):
    consumer = _Consumer(tmp_path)
    result = consumer._missing_declared_delivery_targets(_tasks(), run_id="run-intent")
    assert result
    assert result[0].startswith("chief_engineer_handoff_invalid:")


@pytest.mark.parametrize("field,value", [("planning_advisory", "invalid"), ("explicit_technical_fields", 42)])
def test_malformed_semantic_metadata_returns_diagnostics(field, value):
    tasks = _tasks()
    tasks[0]["metadata"][field] = value
    result = validate_pm_artifact_contracts(tasks, validation_phase="pm_planning")
    assert not result.ok
    assert result.diagnostics[0]["code"] == "pm_task_intent_invalid"


def test_semantic_admission_to_ce_handoff_preserves_required_ownership(tmp_path):
    from polaris.cells.chief_engineer.blueprint.public import (
        GenerateTaskBlueprintCommandV1,
        build_chief_engineer_blueprint_portfolio,
        generate_task_blueprint,
        project_chief_engineer_task_blueprint,
        validate_director_handoff_from_payload,
    )
    from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import (
        _command,
        _payload,
        _task,
    )

    tasks = _tasks()
    tasks[0]["id"] = "TASK-A"
    assert validate_pm_artifact_contracts(tasks, validation_phase="pm_planning").ok
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, _payload(), task=_task()))
    result = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id="TASK-A",
            workspace=str(tmp_path),
            objective=tasks[0]["goal"],
            run_id="run-intent",
            context={**portfolio.to_task_blueprint_context(), "pm_task_contract": tasks[0]},
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, "TASK-A"),
        )
    )
    handoff = validate_director_handoff_from_payload(
        str(tmp_path),
        {"task_id": "TASK-A", "blueprint_id": result.blueprint_id},
        require_strict=True,
    )
    assert handoff["allowed"] is True, handoff
    assert set(handoff["job_token"]["allowed_write_paths"]) == {"src/main.py", "tests/test_main.py", "pyproject.toml"}
    consumer = _Consumer(tmp_path)
    assert set(consumer._missing_declared_delivery_targets(tasks, run_id="run-intent")) == {
        "src/main.py",
        "tests/test_main.py",
        "pyproject.toml",
    }
    assert consumer._missing_declared_delivery_targets(tasks, run_id="other-run")[0].startswith(
        "chief_engineer_handoff_invalid:"
    )
    from polaris.kernelone.llm.toolkit.write_policy import validate_tool_write_policy

    scope = handoff["job_token"]["allowed_write_paths"]
    assert validate_tool_write_policy(
        changed_files=["src/main.py"], allowed_scope=scope, agents_md="", operation="write_file", require_change=True
    ).allowed
    assert not validate_tool_write_policy(
        changed_files=["outside.py"], allowed_scope=scope, agents_md="", operation="write_file", require_change=True
    ).allowed
    assert not validate_director_handoff_from_payload(str(tmp_path), {"task_id": "TASK-A"}, require_strict=True)[
        "allowed"
    ]
    assert not (tmp_path / "src" / "main.py").exists()
