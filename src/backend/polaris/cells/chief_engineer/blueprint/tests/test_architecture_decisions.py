"""Tests for Chief Engineer architecture-decision inference."""

from __future__ import annotations

from polaris.cells.chief_engineer.blueprint.internal.architecture_decisions import (
    infer_architecture_decisions,
    merge_architecture_decisions,
    selected_libraries_from_decisions,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ArchitectureDecisionV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    generate_task_blueprint,
    get_blueprint_status,
)


def _decision_by_concern(decisions: tuple, concern: str):
    return next(item for item in decisions if item.concern == concern)


def test_infers_realtime_database_and_application_architecture() -> None:
    decisions = infer_architecture_decisions(
        objective=(
            "Build a complex realtime analytics service with WebSocket live updates, "
            "database persistence, and dependency injection for a medium project."
        ),
        context={"task_type": "service", "language": "go"},
        constraints={},
        target_files=["src/server/main.go"],
        scope_paths=["src/server"],
        dependencies=[],
    )

    concerns = {decision.concern for decision in decisions}
    assert "application_architecture" in concerns
    assert "realtime" in concerns
    assert "database" in concerns

    app = _decision_by_concern(decisions, "application_architecture")
    assert app.decision_status == "guidance"
    assert app.source == "platform_signal_guidance"
    assert app.selected_libraries == ()
    assert any("layered architecture" in item.lower() for item in app.options_considered)
    assert any("dependency injection" in item.lower() for item in app.options_considered)

    realtime = _decision_by_concern(decisions, "realtime")
    assert realtime.decision_status == "guidance"
    assert realtime.selected_libraries == ()
    assert any("server-to-client" in item.lower() for item in realtime.options_considered)
    assert any("durable event streaming" in item.lower() for item in realtime.options_considered)

    database = _decision_by_concern(decisions, "database")
    assert database.decision_status == "guidance"
    assert database.selected_libraries == ()
    assert any("relational oltp" in item.lower() for item in database.options_considered)
    assert any("embedded/local" in item.lower() for item in database.options_considered)
    assert any("search" in item.lower() for item in database.options_considered)
    assert any("analytical/columnar" in item.lower() for item in database.options_considered)
    assert any("vector" in item.lower() for item in database.options_considered)
    assert any("managed cloud" in item.lower() for item in database.options_considered)


def test_infers_mvvm_for_mobile_ui_work() -> None:
    decisions = infer_architecture_decisions(
        objective="Build an iOS SwiftUI screen with MVVM and dependency injection.",
        context={},
        constraints={},
    )

    app = _decision_by_concern(decisions, "application_architecture")
    assert app.decision_status == "guidance"
    assert app.selected_libraries == ()
    assert any("mvvm" in item.lower() for item in app.options_considered)
    assert any("dependency injection" in item.lower() for item in app.options_considered)


def test_react_frontend_uses_component_architecture_not_mvc_mvvm() -> None:
    decisions = infer_architecture_decisions(
        objective="Build a React frontend page with shared API client injection and state management.",
        context={"project_type": "frontend", "language": "typescript", "framework": "react"},
        constraints={},
        target_files=["src/features/dashboard/Dashboard.tsx"],
    )

    app = _decision_by_concern(decisions, "application_architecture")
    assert app.decision_status == "guidance"
    assert app.selected_libraries == ()
    assert any("component architecture" in item.lower() for item in app.options_considered)
    assert any("feature/module" in item.lower() for item in app.options_considered)
    assert not any("mvc" in item.lower() for item in app.options_considered)
    assert not any("mvvm" in item.lower() for item in app.options_considered)


def test_backend_only_work_does_not_force_mvc_or_mvvm() -> None:
    decisions = infer_architecture_decisions(
        objective=(
            "Build a complex backend CLI worker with repository boundaries "
            "and dependency injection for batch processing. It is headless with no UI."
        ),
        context={"project_type": "cli", "language": "python"},
        constraints={},
        target_files=["src/worker/main.py"],
    )

    app = _decision_by_concern(decisions, "application_architecture")
    assert app.decision_status == "guidance"
    assert app.selected_libraries == ()
    assert any("dependency injection" in item.lower() for item in app.options_considered)
    assert not any("mvc" in item.lower() for item in app.options_considered)
    assert not any("mvvm" in item.lower() for item in app.options_considered)
    assert any("layered architecture" in item.lower() for item in app.options_considered)


def test_explicit_ce_decision_keeps_final_selection() -> None:
    explicit = (
        ArchitectureDecisionV1(
            concern="database",
            decision="Use the existing project datastore because the project documents standardize on it.",
            selected_libraries=("existing_project_datastore", "existing_project_data_mapper"),
            options_considered=(
                "Existing project database standard",
                "Relational OLTP database",
                "Managed cloud database",
                "Search / analytical / vector store only if query requirements need it",
            ),
        ),
    )
    inferred = infer_architecture_decisions(
        objective="Add database persistence for user records.",
        context={},
        constraints={},
    )

    merged = merge_architecture_decisions(explicit, inferred)
    database = _decision_by_concern(merged, "database")

    assert database.decision.startswith("Use the existing project datastore")
    assert database.decision_status == "decision"
    assert database.selected_libraries == ("existing_project_datastore", "existing_project_data_mapper")


def test_generate_task_blueprint_persists_architecture_decisions(tmp_path) -> None:
    result = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id="task-arch-1",
            workspace=str(tmp_path),
            objective=(
                "Create a complex realtime dashboard with durable event streaming, "
                "WebSocket client updates, relational persistence, and dependency injection."
            ),
            context={
                "target_files": ["src/backend/app.ts"],
                "acceptance_criteria": ["Dashboard streams live updates"],
                "execution_checklist": ["Create service boundary", "Add persistence adapter"],
            },
        )
    )

    assert result.ok is True
    assert result.architecture_decisions
    assert result.selected_libraries == ()

    status = get_blueprint_status(
        GetBlueprintStatusQueryV1(
            task_id="task-arch-1",
            workspace=str(tmp_path),
        )
    )
    assert status.ok is True
    assert {item.concern for item in status.architecture_decisions} >= {
        "application_architecture",
        "realtime",
        "database",
    }
    assert all(not item.selected_libraries for item in status.architecture_decisions)
    assert selected_libraries_from_decisions(status.architecture_decisions) == status.selected_libraries
