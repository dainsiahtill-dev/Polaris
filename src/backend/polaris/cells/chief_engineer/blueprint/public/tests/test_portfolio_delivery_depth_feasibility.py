"""Contract-feasibility tests for CE delivery-depth authority."""

from __future__ import annotations

from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerPortfolioTaskV1,
    project_chief_engineer_delivery_depth_feasibility_from_pm_tasks,
    project_chief_engineer_portfolio_delivery_depth_feasibility,
)
from polaris.cells.chief_engineer.blueprint.public.service._portfolio import (
    _task_authorizes_completion_path,
)


def _task() -> ChiefEngineerPortfolioTaskV1:
    return ChiefEngineerPortfolioTaskV1(
        task_id="TASK-1",
        objective="Deliver one real Go application",
        target_files=("go.mod",),
        scope_paths=("go.mod",),
        topology_authority="chief_engineer",
        required_source_kinds=("domain_modules", "entrypoint", "tests"),
        primary_language="go",
        allowed_source_suffixes=(".go",),
        delivery_depth_contract={
            "schema_version": "polaris.delivery_depth_contract.v1",
            "minimums": {"min_prod_files": 7, "min_test_files": 2},
        },
    )


def _payload(artifacts: list[dict[str, object]]) -> dict[str, object]:
    return {
        "project_completion_contract": {
            "obligations": {"artifacts": artifacts},
        }
    }


def _artifact(path: str, role: str) -> dict[str, object]:
    return {
        "obligation_id": "artifact-" + path.replace("/", "-"),
        "path": path,
        "semantic_role": role,
        "applicability": "required",
        "owner_task_id": "TASK-1",
    }


def test_depth_feasibility_rejects_authority_that_cannot_meet_qa_counts() -> None:
    result = project_chief_engineer_portfolio_delivery_depth_feasibility(
        _payload(
            [
                _artifact("main.go", "entrypoint"),
                _artifact("models/model.go", "source"),
                _artifact("engine/engine.go", "source"),
                _artifact("main_test.go", "test"),
                _artifact("go.mod", "manifest"),
            ]
        ),
        tasks=(_task(),),
    )

    assert result["ok"] is False
    assert result["actual"] == {"prod_files": 3, "test_files": 1}
    assert result["deficits"] == [
        {"metric": "prod_files", "actual": 3, "required": 7, "deficit": 4},
        {"metric": "test_files", "actual": 1, "required": 2, "deficit": 1},
    ]


def test_depth_feasibility_accepts_distinct_authorized_source_topology() -> None:
    artifacts = [
        _artifact("main.go", "entrypoint"),
        *[_artifact(f"internal/domain/module_{index}.go", "source") for index in range(1, 7)],
        _artifact("main_test.go", "test"),
        _artifact("behavior_test.go", "test"),
        _artifact("go.mod", "manifest"),
    ]

    result = project_chief_engineer_portfolio_delivery_depth_feasibility(
        _payload(artifacts),
        tasks=(_task(),),
    )

    assert result["ok"] is True
    assert result["actual"] == {"prod_files": 7, "test_files": 2}
    assert result["deficits"] == []


def test_depth_feasibility_does_not_count_source_path_mislabeled_as_test() -> None:
    result = project_chief_engineer_portfolio_delivery_depth_feasibility(
        _payload([_artifact("src/fakecase.go", "test")]),
        tasks=(_task(),),
    )

    assert result["actual"] == {"prod_files": 0, "test_files": 0}
    assert result["ok"] is False


def test_depth_feasibility_does_not_count_docs_or_fixture_under_tests() -> None:
    result = project_chief_engineer_portfolio_delivery_depth_feasibility(
        _payload(
            [
                _artifact("tests/README.md", "docs"),
                _artifact("tests/fixture.json", "test"),
            ]
        ),
        tasks=(_task(),),
    )

    assert result["actual"] == {"prod_files": 0, "test_files": 0}
    assert result["ok"] is False


def test_depth_feasibility_rejects_foreign_language_source_from_delegated_topology() -> None:
    result = project_chief_engineer_portfolio_delivery_depth_feasibility(
        _payload([_artifact("src/foreign.py", "source")]),
        tasks=(_task(),),
    )

    assert result["actual"] == {"prod_files": 0, "test_files": 0}
    assert result["ok"] is False


def test_portfolio_task_serializes_delivery_depth_authority() -> None:
    payload = _task().to_dict()

    assert payload["delivery_depth_contract"]["minimums"] == {
        "min_prod_files": 7,
        "min_test_files": 2,
    }


def test_persisted_pm_task_projection_uses_same_ce_authority_rules() -> None:
    result = project_chief_engineer_delivery_depth_feasibility_from_pm_tasks(
        _payload(
            [
                _artifact("main.go", "entrypoint"),
                _artifact("models/model.go", "source"),
                _artifact("engine/engine.go", "source"),
                _artifact("main_test.go", "test"),
            ]
        ),
        pm_tasks=[
            {
                "id": "TASK-1",
                "goal": "Deliver Go project",
                "language": "go",
                "target_files": ["main.go", "models/model.go", "engine/engine.go", "main_test.go"],
                "project_declared_entrypoint_targets": ["main.go"],
                "metadata": {
                    "topology_authority": "chief_engineer",
                    "required_source_kinds": ["domain_modules", "entrypoint"],
                },
                "delivery_depth_contract": {"minimums": {"min_prod_files": 7, "min_test_files": 2}},
            }
        ],
    )

    assert result["ok"] is False
    assert result["actual"] == {"prod_files": 3, "test_files": 1}


def test_file_like_scope_is_exact_even_when_not_repeated_in_targets() -> None:
    task = ChiefEngineerPortfolioTaskV1(
        task_id="TASK-1",
        objective="Write documentation",
        target_files=("go.mod",),
        scope_paths=("README.md",),
    )

    assert _task_authorizes_completion_path(task=task, path="README.md") is True
    assert _task_authorizes_completion_path(task=task, path="README.md/child.go") is False
