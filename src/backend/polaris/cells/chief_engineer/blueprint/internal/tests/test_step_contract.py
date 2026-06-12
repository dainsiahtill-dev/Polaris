"""Unit tests for the construction-step contract and CE-stage quality gate."""

from __future__ import annotations

from polaris.cells.chief_engineer.blueprint.internal.step_contract import (
    CE_BLUEPRINT_TASKS_SCHEMA_VERSION,
    build_blueprint_tasks_contract,
    normalize_construction_step,
    validate_construction_steps,
)


def _step(**overrides):
    base = {
        "step_id": "PM-1-S1",
        "target_file": "app.js",
        "est_lines": 80,
        "signatures": ["function render(state)"],
        "interface_names": ["#editor"],
        "verify": "node --check app.js",
        "depends_on": [],
    }
    base.update(overrides)
    return base


class TestNormalize:
    def test_defaults_and_coercion(self) -> None:
        step = normalize_construction_step(
            {"file": "./src/app.js", "est_lines": "90", "verify": "node --check src/app.js"},
            parent_pm_task="PM-1",
            index=0,
        )
        assert step["step_id"] == "PM-1-S1"
        assert step["target_file"] == "src/app.js"
        assert step["est_lines"] == 90
        assert step["parent_pm_task"] == "PM-1"

    def test_non_dict_input_yields_empty_shape(self) -> None:
        step = normalize_construction_step("prose", parent_pm_task="PM-1", index=2)
        assert step["step_id"] == "PM-1-S3"
        assert step["target_file"] == ""


class TestGate:
    def test_valid_steps_pass(self) -> None:
        steps = [_step(), _step(step_id="PM-1-S2", target_file="style.css", depends_on=["PM-1-S1"])]
        assert validate_construction_steps(steps, parent_pm_task="PM-1") == []

    def test_empty_fission_blocked(self) -> None:
        errors = validate_construction_steps([], parent_pm_task="PM-1")
        assert errors and "no construction steps" in errors[0]

    def test_oversized_step_blocked(self) -> None:
        errors = validate_construction_steps([_step(est_lines=121)], parent_pm_task="PM-1")
        assert any("exceeds the convergence ceiling" in e for e in errors)

    def test_missing_verify_blocked(self) -> None:
        errors = validate_construction_steps([_step(verify="")], parent_pm_task="PM-1")
        assert any("machine-executable verify" in e for e in errors)

    def test_missing_signatures_blocked(self) -> None:
        errors = validate_construction_steps([_step(signatures=[])], parent_pm_task="PM-1")
        assert any("signatures skeleton" in e for e in errors)

    def test_unknown_dependency_blocked(self) -> None:
        errors = validate_construction_steps([_step(depends_on=["PM-1-S9"])], parent_pm_task="PM-1")
        assert any("unknown step" in e for e in errors)

    def test_self_dependency_blocked(self) -> None:
        errors = validate_construction_steps([_step(depends_on=["PM-1-S1"])], parent_pm_task="PM-1")
        assert any("depends on itself" in e for e in errors)

    def test_duplicate_step_id_blocked(self) -> None:
        errors = validate_construction_steps([_step(), _step()], parent_pm_task="PM-1")
        assert any("duplicate step_id" in e for e in errors)


def test_contract_assembly() -> None:
    steps = [_step()]
    contract = build_blueprint_tasks_contract(
        parent_pm_task="PM-1",
        blueprint_id="bp-1",
        blueprint_path="runtime/blueprints/bp-1.json",
        steps=steps,
    )
    assert contract["schema_version"] == CE_BLUEPRINT_TASKS_SCHEMA_VERSION
    assert contract["parent_task_id"] == "PM-1"
    assert contract["step_count"] == 1
