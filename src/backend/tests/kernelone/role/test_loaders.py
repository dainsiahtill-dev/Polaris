"""Unit tests for Role Loaders."""

from __future__ import annotations

import pytest

from polaris.kernelone.role.loaders import (
    AnchorConfig,
    AnchorLoader,
    PersonaConfig,
    PersonaLoader,
    ProfessionConfig,
    ProfessionLoader,
    RecipeConfig,
    RecipeLoader,
    get_anchor_loader,
    get_persona_loader,
    get_profession_loader,
    get_recipe_loader,
)


class TestAnchorLoader:
    """Tests for AnchorLoader."""

    def setup_method(self) -> None:
        """Setup fresh loader for each test."""
        self.loader = AnchorLoader()

    def test_load_polaris_director(self) -> None:
        """Test loading director anchor."""
        anchor = self.loader.load("polaris_director")

        assert anchor is not None
        assert anchor.id == "polaris_director"
        assert anchor.name == "工部侍郎"
        assert "workflow_orchestration" in anchor.capabilities
        assert anchor.macro_workflow.get("type") == "blueprint_then_execute"

    def test_load_polaris_pm(self) -> None:
        """Test loading pm anchor."""
        anchor = self.loader.load("polaris_pm")

        assert anchor is not None
        assert anchor.id == "polaris_pm"
        assert "requirement_analysis" in anchor.capabilities

    def test_load_polaris_qa(self) -> None:
        """Test loading qa anchor."""
        anchor = self.loader.load("polaris_qa")

        assert anchor is not None
        assert anchor.id == "polaris_qa"
        assert "quality_review" in anchor.capabilities

    def test_load_nonexistent_returns_none(self) -> None:
        """Test loading non-existent anchor returns None."""
        anchor = self.loader.load("nonexistent_anchor")
        assert anchor is None

    def test_caching(self) -> None:
        """Test that loaded anchors are cached."""
        anchor1 = self.loader.load("polaris_director")
        anchor2 = self.loader.load("polaris_director")

        assert anchor1 is anchor2  # Same object reference

    def test_get_workflow(self) -> None:
        """Test getting workflow definition."""
        workflow = self.loader.get_workflow("polaris_director")

        assert workflow is not None
        assert workflow.get("type") == "blueprint_then_execute"
        assert len(workflow.get("stages", {})) > 0


class TestPersonaLoader:
    """Tests for PersonaLoader."""

    def setup_method(self) -> None:
        """Setup fresh loader for each test."""
        self.loader = PersonaLoader()

    def test_load_gongbu_shilang(self) -> None:
        """Test loading gongbu_shilang persona."""
        persona = self.loader.load("gongbu_shilang")

        assert persona is not None
        assert persona.id == "gongbu_shilang"
        assert persona.name == "工部侍郎"
        assert len(persona.vocabulary) >= 3
        assert "臣已核实" in persona.vocabulary

    def test_load_all_polaris_personas(self) -> None:
        """Test loading all Polaris personas."""
        personas = ["gongbu_shilang", "shangshuling", "zhongshuling", "mentu_xiaozhong"]

        for pid in personas:
            persona = self.loader.load(pid)
            assert persona is not None, f"Failed to load {pid}"
            assert persona.name  # Has a name

    def test_load_nonexistent_returns_none(self) -> None:
        """Test loading non-existent persona returns None."""
        persona = self.loader.load("nonexistent_persona")
        assert persona is None

    def test_expression_fields(self) -> None:
        """Test persona expression fields."""
        persona = self.loader.load("gongbu_shilang")

        assert persona is not None
        assert persona.expression.get("greeting") == "臣听令。"
        assert persona.expression.get("thinking_prefix") == "<thinking>"
        assert persona.expression.get("thinking_suffix") == "</thinking>"


class TestProfessionLoader:
    """Tests for ProfessionLoader."""

    def setup_method(self) -> None:
        """Setup fresh loader for each test."""
        self.loader = ProfessionLoader()

    def test_load_python_principal_architect(self) -> None:
        """Test loading python_principal_architect profession."""
        profession = self.loader.load("python_principal_architect")

        assert profession is not None
        assert profession.id == "python_principal_architect"
        assert profession.name == "Python 首席架构师"
        assert len(profession.expertise) >= 3
        assert "系统架构设计" in profession.expertise

    def test_load_security_auditor(self) -> None:
        """Test loading security_auditor profession."""
        profession = self.loader.load("security_auditor")

        assert profession is not None
        assert profession.id == "security_auditor"
        # Check that expertise contains threat modeling related content
        assert any("威胁" in exp or "threat" in exp.lower() for exp in profession.expertise)

    def test_load_software_engineer(self) -> None:
        """Test loading software_engineer profession."""
        profession = self.loader.load("software_engineer")

        assert profession is not None
        assert profession.id == "software_engineer"

    def test_load_nonexistent_returns_none(self) -> None:
        """Test loading non-existent profession returns None."""
        profession = self.loader.load("nonexistent_profession")
        assert profession is None

    def test_skip_base_template(self) -> None:
        """Test that base template (_base) returns None."""
        profession = self.loader.load("_base")
        assert profession is None

    def test_parent_inheritance(self) -> None:
        """Test that child profession inherits from parent."""
        profession = self.loader.load("python_principal_architect")

        assert profession is not None
        # Should have red_lines from _base or overridden
        standards = profession.engineering_standards
        assert standards.get("coverage_mode") == "strict"

    def test_workflow_stages_as_dict(self) -> None:
        """Test that workflow stages are properly loaded as dict."""
        profession = self.loader.load("python_principal_architect")

        assert profession is not None
        workflow = profession.workflow
        assert isinstance(workflow.get("stages"), dict)
        assert "blueprint" in workflow.get("stages", {})
        assert "execution" in workflow.get("stages", {})


class TestRecipeLoader:
    """Tests for RecipeLoader."""

    def setup_method(self) -> None:
        """Setup fresh loader for each test."""
        self.loader = RecipeLoader()

    def test_load_builtin_recipes(self) -> None:
        """Test loading all builtin recipes."""
        recipes = ["pm", "director", "qa", "architect", "chief_engineer"]

        for recipe_id in recipes:
            recipe = self.loader.load(recipe_id)
            assert recipe is not None, f"Failed to load recipe {recipe_id}"
            assert recipe.anchor
            assert recipe.persona
            assert recipe.profession

    def test_load_senior_python_architect(self) -> None:
        """Test loading senior_python_architect recipe."""
        recipe = self.loader.load("senior_python_architect")

        assert recipe is not None
        assert recipe.anchor == "polaris_director"
        assert recipe.persona == "gongbu_shilang"
        assert recipe.profession == "python_principal_architect"

    def test_load_by_legacy_id(self) -> None:
        """Test loading recipe by legacy ID."""
        recipe = self.loader.load_by_legacy_id("director")

        assert recipe is not None
        assert recipe.backward_compatible is True

    def test_load_nonexistent_returns_none(self) -> None:
        """Test loading non-existent recipe returns None."""
        recipe = self.loader.load("nonexistent_recipe")
        assert recipe is None


class TestGlobalLoaders:
    """Tests for global loader singletons."""

    def test_get_anchor_loader_singleton(self) -> None:
        """Test that get_anchor_loader returns singleton."""
        loader1 = get_anchor_loader()
        loader2 = get_anchor_loader()
        assert loader1 is loader2

    def test_get_persona_loader_singleton(self) -> None:
        """Test that get_persona_loader returns singleton."""
        loader1 = get_persona_loader()
        loader2 = get_persona_loader()
        assert loader1 is loader2

    def test_get_profession_loader_singleton(self) -> None:
        """Test that get_profession_loader returns singleton."""
        loader1 = get_profession_loader()
        loader2 = get_profession_loader()
        assert loader1 is loader2

    def test_get_recipe_loader_singleton(self) -> None:
        """Test that get_recipe_loader returns singleton."""
        loader1 = get_recipe_loader()
        loader2 = get_recipe_loader()
        assert loader1 is loader2
