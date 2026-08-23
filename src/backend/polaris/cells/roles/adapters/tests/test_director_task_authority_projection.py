"""Regression coverage for CE authority projected into Director requests."""

from pathlib import Path

from polaris.cells.chief_engineer.blueprint.public import BlueprintPersistence
from polaris.cells.roles.adapters.internal.director.adapter import (
    _build_director_blueprint_handoff_lines,
)


def test_director_blueprint_handoff_projects_task_local_interface_authority(tmp_path: Path) -> None:
    """Director must see CE package/interface authority before writing sibling files."""

    BlueprintPersistence(str(tmp_path)).save(
        "bp-project-interface",
        {
            "blueprint_id": "bp-project-interface",
            "task_id": "TASK-1",
            "target_files": ["bubble.go"],
            "project_interface_contract": {
                "schema_version": "chief_engineer.project_interface_contract.v1",
                "ownership_authority": "chief_engineer.project_completion_contract",
                "provider_declarations": [
                    {
                        "contract_id": "bubble_physics_v1",
                        "owner_task_id": "TASK-1",
                        "description": "Export the core API from package main.",
                    }
                ],
                "consumer_declarations": [
                    {"task_id": "TASK-2", "depends_on_contract": "bubble_physics_v1"}
                ],
            },
        },
    )

    text = "\n".join(_build_director_blueprint_handoff_lines(str(tmp_path), "bp-project-interface"))

    assert "project_interface_contract: authority=chief_engineer.project_completion_contract" in text
    assert "provides bubble_physics_v1: Export the core API from package main." in text
    assert "TASK-2 consumes bubble_physics_v1" not in text
