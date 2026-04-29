"""Tests for preset_templates module."""

from __future__ import annotations

import pytest

from polaris.kernelone.roles.templates.preset_templates import (
    ARCHITECT_TEMPLATE,
    CHIEF_ENGINEER_TEMPLATE,
    DIRECTOR_TEMPLATE,
    PM_TEMPLATE,
    PRESET_TEMPLATES,
    QA_TEMPLATE,
    SCOUT_TEMPLATE,
    get_preset_template,
    list_preset_template_names,
    register_preset_templates,
)
from polaris.kernelone.roles.dynamic_role import DynamicRoleManager


class TestPresetTemplates:
    """Tests for preset role templates."""

    def test_pm_template_attributes(self) -> None:
        assert PM_TEMPLATE.name == "pm"
        assert "Project Manager" in PM_TEMPLATE.description
        assert "task_create" in PM_TEMPLATE.tools
        assert "system" in PM_TEMPLATE.prompts
        assert "Must maintain task audit trail" in PM_TEMPLATE.constraints
        assert "task_management" in PM_TEMPLATE.capabilities

    def test_architect_template_attributes(self) -> None:
        assert ARCHITECT_TEMPLATE.name == "architect"
        assert "Architecture Designer" in ARCHITECT_TEMPLATE.description
        assert "codebase_search" in ARCHITECT_TEMPLATE.tools
        assert "Must follow ACGA 2.0 principles" in ARCHITECT_TEMPLATE.constraints

    def test_chief_engineer_template_attributes(self) -> None:
        assert CHIEF_ENGINEER_TEMPLATE.name == "chief_engineer"
        assert "Chief Engineer" in CHIEF_ENGINEER_TEMPLATE.description
        assert "performance_analyze" in CHIEF_ENGINEER_TEMPLATE.tools

    def test_director_template_attributes(self) -> None:
        assert DIRECTOR_TEMPLATE.name == "director"
        assert "Code Director" in DIRECTOR_TEMPLATE.description
        assert "file_write" in DIRECTOR_TEMPLATE.tools

    def test_qa_template_attributes(self) -> None:
        assert QA_TEMPLATE.name == "qa"
        assert "Quality Assurance" in QA_TEMPLATE.description
        assert "test_run" in QA_TEMPLATE.tools

    def test_scout_template_attributes(self) -> None:
        assert SCOUT_TEMPLATE.name == "scout"
        assert "Scout" in SCOUT_TEMPLATE.description
        assert "codebase_search" in SCOUT_TEMPLATE.tools
        assert "Read-only operations only" in SCOUT_TEMPLATE.constraints


class TestPresetRegistry:
    """Tests for PRESET_TEMPLATES registry."""

    def test_registry_has_all_six_roles(self) -> None:
        assert len(PRESET_TEMPLATES) == 6
        assert set(PRESET_TEMPLATES.keys()) == {
            "pm", "architect", "chief_engineer", "director", "qa", "scout"
        }

    def test_registry_values_are_templates(self) -> None:
        for template in PRESET_TEMPLATES.values():
            assert template.name
            assert template.description
            assert template.tools
            assert template.prompts
            assert template.constraints
            assert template.capabilities


class TestGetPresetTemplate:
    """Tests for get_preset_template function."""

    def test_get_existing_template(self) -> None:
        template = get_preset_template("pm")
        assert template is not None
        assert template.name == "pm"

    def test_get_architect_template(self) -> None:
        template = get_preset_template("architect")
        assert template is not None
        assert template.name == "architect"

    def test_get_nonexistent_returns_none(self) -> None:
        assert get_preset_template("nonexistent") is None

    def test_get_empty_string_returns_none(self) -> None:
        assert get_preset_template("") is None


class TestListPresetTemplateNames:
    """Tests for list_preset_template_names function."""

    def test_returns_sorted_list(self) -> None:
        names = list_preset_template_names()
        assert isinstance(names, list)
        assert names == sorted(names)
        assert len(names) == 6

    def test_contains_all_roles(self) -> None:
        names = list_preset_template_names()
        assert "pm" in names
        assert "architect" in names
        assert "chief_engineer" in names
        assert "director" in names
        assert "qa" in names
        assert "scout" in names


class TestRegisterPresetTemplates:
    """Tests for register_preset_templates function."""

    def test_registers_all_templates(self) -> None:
        manager = DynamicRoleManager()
        register_preset_templates(manager)
        # Verify manager has registered templates by checking internal state
        assert len(manager._templates) == 6

    def test_duplicate_registration_raises(self) -> None:
        manager = DynamicRoleManager()
        register_preset_templates(manager)
        # Duplicate registration should raise RoleAlreadyExistsError
        from polaris.kernelone.roles.dynamic_role import RoleAlreadyExistsError
        with pytest.raises(RoleAlreadyExistsError):
            register_preset_templates(manager)


class TestTemplatePrompts:
    """Tests for template prompt content."""

    def test_pm_system_prompt(self) -> None:
        assert "Project Manager" in PM_TEMPLATE.prompts["system"]

    def test_architect_system_prompt(self) -> None:
        assert "Architecture Designer" in ARCHITECT_TEMPLATE.prompts["system"]

    def test_director_system_prompt(self) -> None:
        assert "Code Director" in DIRECTOR_TEMPLATE.prompts["system"]

    def test_all_templates_have_system_prompt(self) -> None:
        for template in PRESET_TEMPLATES.values():
            assert "system" in template.prompts
            assert template.prompts["system"]


class TestTemplateTools:
    """Tests for template tool assignments."""

    def test_pm_has_task_tools(self) -> None:
        assert "task_create" in PM_TEMPLATE.tools
        assert "task_update" in PM_TEMPLATE.tools
        assert "task_list" in PM_TEMPLATE.tools

    def test_director_has_file_tools(self) -> None:
        assert "file_write" in DIRECTOR_TEMPLATE.tools
        assert "file_edit" in DIRECTOR_TEMPLATE.tools
        assert "file_delete" in DIRECTOR_TEMPLATE.tools

    def test_scout_has_read_only_tools(self) -> None:
        assert "file_read" in SCOUT_TEMPLATE.tools
        assert "codebase_search" in SCOUT_TEMPLATE.tools
        assert "grep" in SCOUT_TEMPLATE.tools

    def test_no_template_has_empty_tools(self) -> None:
        for template in PRESET_TEMPLATES.values():
            assert len(template.tools) > 0


class TestTemplateConstraints:
    """Tests for template constraints."""

    def test_all_templates_have_constraints(self) -> None:
        for template in PRESET_TEMPLATES.values():
            assert len(template.constraints) > 0

    def test_qa_has_coverage_constraint(self) -> None:
        assert any("coverage" in c.lower() for c in QA_TEMPLATE.constraints)

    def test_director_has_quality_constraint(self) -> None:
        assert any("quality" in c.lower() for c in DIRECTOR_TEMPLATE.constraints)


class TestTemplateCapabilities:
    """Tests for template capabilities."""

    def test_all_templates_have_capabilities(self) -> None:
        for template in PRESET_TEMPLATES.values():
            assert len(template.capabilities) > 0

    def test_pm_has_project_planning(self) -> None:
        assert "project_planning" in PM_TEMPLATE.capabilities

    def test_architect_has_design_review(self) -> None:
        assert "design_review" in ARCHITECT_TEMPLATE.capabilities
