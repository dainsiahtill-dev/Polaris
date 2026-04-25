"""Unit tests for polaris.cells.factory.pipeline.internal.models."""

from __future__ import annotations

import pytest

from polaris.cells.factory.pipeline.internal.models import (
    CommandSpec,
    EntitySpec,
    FieldSpec,
    ProjectionEntry,
    TargetCellSpec,
    TargetProjectManifest,
)


class TestFieldSpec:
    """Tests for FieldSpec dataclass."""

    def test_valid_field(self) -> None:
        field = FieldSpec(name="title", kind="str", description="The title")
        assert field.name == "title"
        assert field.kind == "str"
        assert field.required is True
        assert field.searchable is False

    def test_bool_required_false(self) -> None:
        field = FieldSpec(name="active", kind="bool", description="Is active")
        assert field.required is False

    def test_invalid_kind(self) -> None:
        with pytest.raises(ValueError, match="Unsupported field kind"):
            FieldSpec(name="x", kind="float", description="A float")

    def test_empty_name(self) -> None:
        with pytest.raises(ValueError, match="name must be a non-empty string"):
            FieldSpec(name="", kind="str", description="desc")

    def test_empty_description(self) -> None:
        with pytest.raises(ValueError, match="description must be a non-empty string"):
            FieldSpec(name="x", kind="str", description="")

    def test_to_dict(self) -> None:
        field = FieldSpec(name="title", kind="str", description="The title", searchable=True)
        d = field.to_dict()
        assert d == {"name": "title", "kind": "str", "description": "The title", "required": True, "searchable": True}


class TestCommandSpec:
    """Tests for CommandSpec dataclass."""

    def test_valid_command(self) -> None:
        cmd = CommandSpec(name="create", description="Create a thing")
        assert cmd.name == "create"
        assert cmd.description == "Create a thing"

    def test_empty_name(self) -> None:
        with pytest.raises(ValueError, match="name must be a non-empty string"):
            CommandSpec(name="", description="desc")

    def test_to_dict(self) -> None:
        cmd = CommandSpec(name="create", description="Create a thing")
        assert cmd.to_dict() == {"name": "create", "description": "Create a thing"}


class TestEntitySpec:
    """Tests for EntitySpec dataclass."""

    def test_valid_entity(self) -> None:
        field = FieldSpec(name="title", kind="str", description="The title")
        entity = EntitySpec(singular="task", plural="tasks", class_name="Task", fields=(field,))
        assert entity.singular == "task"
        assert entity.plural == "tasks"
        assert entity.class_name == "Task"
        assert entity.record_id_field == "task_id"
        assert entity.searchable_fields == ()

    def test_empty_fields(self) -> None:
        with pytest.raises(ValueError, match="fields must not be empty"):
            EntitySpec(singular="task", plural="tasks", class_name="Task", fields=())

    def test_duplicate_field_names(self) -> None:
        f1 = FieldSpec(name="title", kind="str", description="Title")
        f2 = FieldSpec(name="title", kind="str", description="Title again")
        with pytest.raises(ValueError, match="field names must be unique"):
            EntitySpec(singular="task", plural="tasks", class_name="Task", fields=(f1, f2))

    def test_invalid_archive_field(self) -> None:
        f1 = FieldSpec(name="title", kind="str", description="Title")
        with pytest.raises(ValueError, match="archive_field must reference an existing field"):
            EntitySpec(singular="task", plural="tasks", class_name="Task", fields=(f1,), archive_field="missing")

    def test_searchable_fields(self) -> None:
        f1 = FieldSpec(name="title", kind="str", description="Title", searchable=True)
        f2 = FieldSpec(name="body", kind="str", description="Body")
        entity = EntitySpec(singular="task", plural="tasks", class_name="Task", fields=(f1, f2))
        assert entity.searchable_fields == (f1,)

    def test_to_dict(self) -> None:
        field = FieldSpec(name="title", kind="str", description="The title")
        entity = EntitySpec(singular="task", plural="tasks", class_name="Task", fields=(field,))
        d = entity.to_dict()
        assert d["singular"] == "task"
        assert d["record_id_field"] == "task_id"
        assert len(d["fields"]) == 1


class TestTargetProjectManifest:
    """Tests for TargetProjectManifest dataclass."""

    def test_valid_manifest(self) -> None:
        field = FieldSpec(name="title", kind="str", description="The title")
        entity = EntitySpec(singular="task", plural="tasks", class_name="Task", fields=(field,))
        cmd = CommandSpec(name="create", description="Create")
        manifest = TargetProjectManifest(
            scenario_id="s1",
            requirement="Build a task app",
            project_slug="task_app",
            project_title="Task App",
            package_name="taskapp",
            summary="A task app",
            entity=entity,
            commands=(cmd,),
        )
        assert manifest.scenario_id == "s1"
        assert manifest.project_root == "experiments/task_app"
        assert manifest.verification_commands == ()

    def test_empty_commands(self) -> None:
        field = FieldSpec(name="title", kind="str", description="The title")
        entity = EntitySpec(singular="task", plural="tasks", class_name="Task", fields=(field,))
        with pytest.raises(ValueError, match="commands must not be empty"):
            TargetProjectManifest(
                scenario_id="s1",
                requirement="Build",
                project_slug="app",
                project_title="App",
                package_name="app",
                summary="An app",
                entity=entity,
                commands=(),
            )

    def test_verification_commands_normalization(self) -> None:
        field = FieldSpec(name="title", kind="str", description="The title")
        entity = EntitySpec(singular="task", plural="tasks", class_name="Task", fields=(field,))
        cmd = CommandSpec(name="create", description="Create")
        manifest = TargetProjectManifest(
            scenario_id="s1",
            requirement="Build",
            project_slug="app",
            project_title="App",
            package_name="app",
            summary="An app",
            entity=entity,
            commands=(cmd,),
            verification_commands=(["pytest", "-q"], ["ruff", "check", "."]),
        )
        assert manifest.verification_commands == (("pytest", "-q"), ("ruff", "check", "."))

    def test_to_dict(self) -> None:
        field = FieldSpec(name="title", kind="str", description="The title")
        entity = EntitySpec(singular="task", plural="tasks", class_name="Task", fields=(field,))
        cmd = CommandSpec(name="create", description="Create")
        manifest = TargetProjectManifest(
            scenario_id="s1",
            requirement="Build",
            project_slug="app",
            project_title="App",
            package_name="app",
            summary="An app",
            entity=entity,
            commands=(cmd,),
        )
        d = manifest.to_dict()
        assert d["scenario_id"] == "s1"
        assert d["project_root"] == "experiments/app"
        assert "entity" in d
        assert "commands" in d


class TestTargetCellSpec:
    """Tests for TargetCellSpec dataclass."""

    def test_valid_cell(self) -> None:
        cell = TargetCellSpec(cell_id="c1", purpose="Do something")
        assert cell.cell_id == "c1"
        assert cell.purpose == "Do something"
        assert cell.depends_on == ()
        assert cell.state_owners == ()

    def test_empty_cell_id(self) -> None:
        with pytest.raises(ValueError, match="cell_id must be a non-empty string"):
            TargetCellSpec(cell_id="", purpose="Do something")

    def test_tuple_normalization(self) -> None:
        cell = TargetCellSpec(
            cell_id="c1",
            purpose="Do something",
            depends_on=("c2", "", "c3"),
            state_owners=("",),
        )
        assert cell.depends_on == ("c2", "c3")
        assert cell.state_owners == ()

    def test_to_dict(self) -> None:
        cell = TargetCellSpec(cell_id="c1", purpose="Do something", depends_on=("c2",))
        d = cell.to_dict()
        assert d["cell_id"] == "c1"
        assert d["depends_on"] == ["c2"]


class TestProjectionEntry:
    """Tests for ProjectionEntry dataclass."""

    def test_valid_entry(self) -> None:
        entry = ProjectionEntry(path="src/app.py", cell_ids=("c1",), description="Main app")
        assert entry.path == "src/app.py"
        assert entry.cell_ids == ("c1",)

    def test_empty_cell_ids(self) -> None:
        with pytest.raises(ValueError, match="cell_ids must not be empty"):
            ProjectionEntry(path="src/app.py", cell_ids=(), description="Main app")

    def test_empty_path(self) -> None:
        with pytest.raises(ValueError, match="path must be a non-empty string"):
            ProjectionEntry(path="", cell_ids=("c1",), description="Main app")

    def test_to_dict(self) -> None:
        entry = ProjectionEntry(path="src/app.py", cell_ids=("c1", "c2"), description="Main app")
        assert entry.to_dict() == {"path": "src/app.py", "cell_ids": ["c1", "c2"], "description": "Main app"}
