"""Tests for dynamic role extension.

These tests verify:
- Template registration and management
- Role creation from templates
- Tool/prompt/constraint merging
- Preset template functionality
"""

from __future__ import annotations

from polaris.kernelone.roles.dynamic_role import (
    DynamicRoleManager,
    RoleAlreadyExistsError,
    RoleProfile,
    RoleTemplate,
    TemplateNotFoundError,
)
from polaris.kernelone.roles.templates.preset_templates import (
    PRESET_TEMPLATES,
    get_preset_template,
    list_preset_template_names,
    register_preset_templates,
)


class TestRoleTemplate:
    """Tests for RoleTemplate dataclass."""

    def test_create_valid_template(self) -> None:
        """Test creating a valid role template."""
        template = RoleTemplate(
            name="test_role",
            description="A test role template",
            tools=("tool_a", "tool_b"),
            prompts={"system": "You are a test role."},
            constraints=("constraint_1", "constraint_2"),
            capabilities=("cap_1", "cap_2"),
        )
        assert template.name == "test_role"
        assert template.description == "A test role template"
        assert template.tools == ("tool_a", "tool_b")
        assert template.prompts == {"system": "You are a test role."}
        assert template.constraints == ("constraint_1", "constraint_2")
        assert template.capabilities == ("cap_1", "cap_2")

    def test_template_empty_name_raises(self) -> None:
        """Test that empty template name raises ValueError."""
        try:
            RoleTemplate(
                name="",
                description="A test role template",
                tools=(),
                prompts={},
                constraints=(),
                capabilities=(),
            )
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "name" in str(e).lower()

    def test_template_empty_description_raises(self) -> None:
        """Test that empty description raises ValueError."""
        try:
            RoleTemplate(
                name="test_role",
                description="",
                tools=(),
                prompts={},
                constraints=(),
                capabilities=(),
            )
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "description" in str(e).lower()

    def test_template_is_frozen(self) -> None:
        """Test that RoleTemplate is immutable (frozen)."""
        template = RoleTemplate(
            name="test_role",
            description="A test role template",
            tools=("tool_a",),
            prompts={},
            constraints=(),
            capabilities=(),
        )
        try:
            template.name = "changed"  # type: ignore
            raise AssertionError("Expected FrozenInstanceError")
        except (AttributeError, RuntimeError, ValueError):  # dataclasses.FrozenInstanceError
            pass


class TestRoleProfile:
    """Tests for RoleProfile dataclass."""

    def test_create_valid_profile(self) -> None:
        """Test creating a valid role profile."""
        profile = RoleProfile(
            name="custom_role",
            template_name="base_template",
            tools=("tool_a", "tool_b"),
            prompts={"system": "You are custom."},
            constraints=("constraint_1",),
            metadata={"key": "value"},
        )
        assert profile.name == "custom_role"
        assert profile.template_name == "base_template"
        assert profile.tools == ("tool_a", "tool_b")
        assert profile.metadata == {"key": "value"}

    def test_profile_empty_name_raises(self) -> None:
        """Test that empty profile name raises ValueError."""
        try:
            RoleProfile(
                name="",
                template_name="base",
                tools=(),
                prompts={},
                constraints=(),
            )
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "name" in str(e).lower()

    def test_profile_default_metadata(self) -> None:
        """Test that profile has empty dict as default metadata."""
        profile = RoleProfile(
            name="custom_role",
            template_name="base",
            tools=(),
            prompts={},
            constraints=(),
        )
        assert profile.metadata == {}


class TestDynamicRoleManager:
    """Tests for DynamicRoleManager."""

    def test_register_and_get_template(self) -> None:
        """Test registering and retrieving a template."""
        manager = DynamicRoleManager()
        template = RoleTemplate(
            name="test_template",
            description="A test template",
            tools=("tool_a",),
            prompts={},
            constraints=(),
            capabilities=(),
        )
        manager.register_role(template)

        retrieved = manager.get_template("test_template")
        assert retrieved is not None
        assert retrieved.name == "test_template"
        assert retrieved.tools == ("tool_a",)

    def test_register_duplicate_raises(self) -> None:
        """Test that registering duplicate template raises error."""
        manager = DynamicRoleManager()
        template = RoleTemplate(
            name="test_template",
            description="A test template",
            tools=(),
            prompts={},
            constraints=(),
            capabilities=(),
        )
        manager.register_role(template)

        try:
            manager.register_role(template)
            raise AssertionError("Expected RoleAlreadyExistsError")
        except RoleAlreadyExistsError as e:
            assert e.template_name == "test_template"

    def test_list_templates(self) -> None:
        """Test listing all registered templates."""
        manager = DynamicRoleManager()
        manager.register_role(
            RoleTemplate(
                name="template_a",
                description="Template A",
                tools=(),
                prompts={},
                constraints=(),
                capabilities=(),
            )
        )
        manager.register_role(
            RoleTemplate(
                name="template_b",
                description="Template B",
                tools=(),
                prompts={},
                constraints=(),
                capabilities=(),
            )
        )

        templates = manager.list_templates()
        assert templates == ["template_a", "template_b"]

    def test_create_role_from_template(self) -> None:
        """Test creating a role from a base template."""
        manager = DynamicRoleManager()
        manager.register_role(
            RoleTemplate(
                name="base_role",
                description="Base role template",
                tools=("tool_a", "tool_b"),
                prompts={"system": "Base system prompt"},
                constraints=("base_constraint",),
                capabilities=("base_cap",),
            )
        )

        profile = manager.create_role(
            name="custom_role",
            base_role="base_role",
            customizations={},
        )

        assert profile.name == "custom_role"
        assert profile.template_name == "base_role"
        assert profile.tools == ("tool_a", "tool_b")
        assert profile.prompts == {"system": "Base system prompt"}
        assert profile.constraints == ("base_constraint",)

    def test_create_role_with_tool_merge(self) -> None:
        """Test creating a role with additional tools merged."""
        manager = DynamicRoleManager()
        manager.register_role(
            RoleTemplate(
                name="base_role",
                description="Base role template",
                tools=("tool_a", "tool_b"),
                prompts={},
                constraints=(),
                capabilities=(),
            )
        )

        profile = manager.create_role(
            name="custom_role",
            base_role="base_role",
            customizations={"tools": ("tool_b", "tool_c")},
        )

        # tool_b should not be duplicated
        assert "tool_a" in profile.tools
        assert "tool_b" in profile.tools
        assert "tool_c" in profile.tools
        assert len(profile.tools) == 3

    def test_create_role_with_prompt_merge(self) -> None:
        """Test creating a role with merged prompts."""
        manager = DynamicRoleManager()
        manager.register_role(
            RoleTemplate(
                name="base_role",
                description="Base role template",
                tools=(),
                prompts={"system": "Base prompt", "task": "Base task prompt"},
                constraints=(),
                capabilities=(),
            )
        )

        profile = manager.create_role(
            name="custom_role",
            base_role="base_role",
            customizations={"prompts": {"task": "Custom task prompt", "review": "Custom review"}},
        )

        assert profile.prompts["system"] == "Base prompt"
        assert profile.prompts["task"] == "Custom task prompt"
        assert profile.prompts["review"] == "Custom review"

    def test_create_role_with_constraint_merge(self) -> None:
        """Test creating a role with merged constraints."""
        manager = DynamicRoleManager()
        manager.register_role(
            RoleTemplate(
                name="base_role",
                description="Base role template",
                tools=(),
                prompts={},
                constraints=("constraint_a", "constraint_b"),
                capabilities=(),
            )
        )

        profile = manager.create_role(
            name="custom_role",
            base_role="base_role",
            customizations={"constraints": ("constraint_b", "constraint_c")},
        )

        assert "constraint_a" in profile.constraints
        assert "constraint_b" in profile.constraints
        assert "constraint_c" in profile.constraints
        assert len(profile.constraints) == 3

    def test_create_role_with_capabilities(self) -> None:
        """Test creating a role with capabilities in metadata."""
        manager = DynamicRoleManager()
        manager.register_role(
            RoleTemplate(
                name="base_role",
                description="Base role template",
                tools=(),
                prompts={},
                constraints=(),
                capabilities=("base_cap",),
            )
        )

        profile = manager.create_role(
            name="custom_role",
            base_role="base_role",
            customizations={"capabilities": ("custom_cap",)},
        )

        assert profile.metadata["created_from"] == "base_role"
        assert "base_cap" in profile.metadata["capabilities"]
        assert "custom_cap" in profile.metadata["capabilities"]

    def test_create_role_missing_template_raises(self) -> None:
        """Test that creating role from non-existent template raises error."""
        manager = DynamicRoleManager()

        try:
            manager.create_role(
                name="custom_role",
                base_role="nonexistent",
                customizations={},
            )
            raise AssertionError("Expected TemplateNotFoundError")
        except TemplateNotFoundError as e:
            assert e.template_name == "nonexistent"

    def test_unregister_role(self) -> None:
        """Test unregistering a template."""
        manager = DynamicRoleManager()
        manager.register_role(
            RoleTemplate(
                name="to_remove",
                description="Template to remove",
                tools=(),
                prompts={},
                constraints=(),
                capabilities=(),
            )
        )

        result = manager.unregister_role("to_remove")
        assert result is True
        assert manager.get_template("to_remove") is None

    def test_unregister_nonexistent_returns_false(self) -> None:
        """Test that unregistering non-existent template returns False."""
        manager = DynamicRoleManager()
        result = manager.unregister_role("nonexistent")
        assert result is False


class TestPresetTemplates:
    """Tests for preset templates."""

    def test_all_six_presets_exist(self) -> None:
        """Test that all six preset templates are defined."""
        expected = {"pm", "architect", "chief_engineer", "director", "qa", "scout"}
        assert set(PRESET_TEMPLATES.keys()) == expected

    def test_get_preset_template(self) -> None:
        """Test retrieving a preset template."""
        template = get_preset_template("pm")
        assert template is not None
        assert template.name == "pm"

    def test_get_nonexistent_preset_returns_none(self) -> None:
        """Test that getting non-existent preset returns None."""
        template = get_preset_template("nonexistent")
        assert template is None

    def test_list_preset_template_names_sorted(self) -> None:
        """Test that preset names are sorted."""
        names = list_preset_template_names()
        assert names == sorted(names)

    def test_preset_templates_have_required_fields(self) -> None:
        """Test that all preset templates have required fields."""
        for name, template in PRESET_TEMPLATES.items():
            assert template.name == name
            assert template.description
            assert template.tools
            assert "system" in template.prompts
            assert template.constraints
            assert template.capabilities

    def test_register_preset_templates(self) -> None:
        """Test registering all preset templates with a manager."""
        manager = DynamicRoleManager()
        register_preset_templates(manager)

        for name in PRESET_TEMPLATES:
            template = manager.get_template(name)
            assert template is not None
            assert template.name == name

    def test_preset_roles_have_unique_tools(self) -> None:
        """Test that preset roles have non-empty unique tool sets."""
        for name, template in PRESET_TEMPLATES.items():
            assert len(template.tools) > 0, f"{name} should have tools"
            assert len(set(template.tools)) == len(template.tools), f"{name} should have unique tools"
