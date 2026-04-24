"""Tests for polaris.cells.roles.adapters.internal.schemas module."""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.schemas import (
    ROLE_OUTPUT_SCHEMAS,
    get_schema_for_role,
)
from polaris.cells.roles.adapters.internal.schemas.architect_schema import ArchitectOutput
from polaris.cells.roles.adapters.internal.schemas.base import BaseToolEnabledOutput, ToolCall
from polaris.cells.roles.adapters.internal.schemas.ce_schema import BlueprintOutput, ConstructionPlan
from polaris.cells.roles.adapters.internal.schemas.director_schema import (
    DirectorOutput,
    PatchOperation,
)
from polaris.cells.roles.adapters.internal.schemas.pm_schema import Task, TaskListOutput
from polaris.cells.roles.adapters.internal.schemas.qa_schema import QAFinding, QAReportOutput


class TestSchemasExported:
    """Test that all expected schemas are exported."""

    def test_base_schemas_exported(self) -> None:
        assert BaseToolEnabledOutput is not None
        assert ToolCall is not None

    def test_architect_schema_exported(self) -> None:
        assert ArchitectOutput is not None

    def test_ce_schema_exported(self) -> None:
        assert BlueprintOutput is not None
        assert ConstructionPlan is not None

    def test_director_schema_exported(self) -> None:
        assert DirectorOutput is not None
        assert PatchOperation is not None

    def test_pm_schema_exported(self) -> None:
        assert Task is not None
        assert TaskListOutput is not None

    def test_qa_schema_exported(self) -> None:
        assert QAFinding is not None
        assert QAReportOutput is not None


class TestRoleOutputSchemas:
    """Test the ROLE_OUTPUT_SCHEMAS mapping."""

    def test_all_five_roles_mapped(self) -> None:
        assert set(ROLE_OUTPUT_SCHEMAS.keys()) == {
            "pm",
            "chief_engineer",
            "architect",
            "qa",
            "director",
        }

    def test_pm_maps_to_task_list_output(self) -> None:
        assert ROLE_OUTPUT_SCHEMAS["pm"] is TaskListOutput

    def test_chief_engineer_maps_to_blueprint_output(self) -> None:
        assert ROLE_OUTPUT_SCHEMAS["chief_engineer"] is BlueprintOutput

    def test_architect_maps_to_architect_output(self) -> None:
        assert ROLE_OUTPUT_SCHEMAS["architect"] is ArchitectOutput

    def test_qa_maps_to_qa_report_output(self) -> None:
        assert ROLE_OUTPUT_SCHEMAS["qa"] is QAReportOutput

    def test_director_maps_to_director_output(self) -> None:
        assert ROLE_OUTPUT_SCHEMAS["director"] is DirectorOutput


class TestGetSchemaForRole:
    """Test the get_schema_for_role function."""

    def test_get_pm_schema(self) -> None:
        schema = get_schema_for_role("pm")
        assert schema is TaskListOutput

    def test_get_chief_engineer_schema(self) -> None:
        schema = get_schema_for_role("chief_engineer")
        assert schema is BlueprintOutput

    def test_get_architect_schema(self) -> None:
        schema = get_schema_for_role("architect")
        assert schema is ArchitectOutput

    def test_get_qa_schema(self) -> None:
        schema = get_schema_for_role("qa")
        assert schema is QAReportOutput

    def test_get_director_schema(self) -> None:
        schema = get_schema_for_role("director")
        assert schema is DirectorOutput

    def test_get_unknown_role_returns_none(self) -> None:
        schema = get_schema_for_role("unknown_role")
        assert schema is None

    def test_get_schema_case_sensitive(self) -> None:
        """Role lookup is case-sensitive."""
        assert get_schema_for_role("PM") is None
        assert get_schema_for_role("Pm") is None
        assert get_schema_for_role("pm") is TaskListOutput
