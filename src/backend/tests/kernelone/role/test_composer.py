"""Unit tests for RoleComposer."""

from __future__ import annotations

import pytest

from polaris.kernelone.role.composer import (
    ComposedPrompt,
    PromptMetadata,
    RoleComposer,
    get_role_composer,
)


class TestRoleComposer:
    """Tests for RoleComposer."""

    def setup_method(self) -> None:
        """Setup fresh composer for each test."""
        self.composer = RoleComposer()

    def test_compose_polaris_director(self) -> None:
        """Test composing director with python_principal_architect."""
        composed = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
            task_type="new_code",
        )

        assert composed is not None
        assert isinstance(composed, ComposedPrompt)
        assert len(composed.system_prompt) > 500
        assert "<role_definition>" in composed.system_prompt
        assert "<workflow>" in composed.system_prompt
        assert "<engineering_standards>" in composed.system_prompt

    def test_compose_returns_none_for_missing_anchor(self) -> None:
        """Test compose returns None when anchor is missing."""
        composed = self.composer.compose(
            anchor_id="nonexistent_anchor",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
        )

        assert composed is None

    def test_compose_returns_none_for_missing_profession(self) -> None:
        """Test compose returns None when profession is missing."""
        composed = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="nonexistent_profession",
            persona_id="gongbu_shilang",
        )

        assert composed is None

    def test_compose_returns_none_for_missing_persona(self) -> None:
        """Test compose returns None when persona is missing."""
        composed = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="nonexistent_persona",
        )

        assert composed is None

    def test_compose_by_recipe_builtin(self) -> None:
        """Test compose_by_recipe with builtin recipes."""
        for recipe_id in ["pm", "director", "qa"]:
            composed = self.composer.compose_by_recipe(recipe_id)
            assert composed is not None, f"Failed for recipe {recipe_id}"
            assert len(composed.system_prompt) > 0

    def test_compose_by_recipe_professional(self) -> None:
        """Test compose_by_recipe with professional recipes."""
        composed = self.composer.compose_by_recipe("senior_python_architect")
        assert composed is not None
        assert "Python 首席架构师" in composed.system_prompt

    def test_compose_by_recipe_security_architect(self) -> None:
        """Test compose_by_recipe with security_architect."""
        composed = self.composer.compose_by_recipe("security_architect")
        assert composed is not None
        assert "安全" in composed.system_prompt

    def test_compose_by_recipe_legacy_id(self) -> None:
        """Test compose_by_recipe with legacy ID."""
        composed = self.composer.compose_by_recipe("director")
        assert composed is not None

    def test_compose_by_recipe_returns_none_for_unknown(self) -> None:
        """Test compose_by_recipe returns None for unknown recipe."""
        composed = self.composer.compose_by_recipe("nonexistent_recipe")
        assert composed is None

    def test_metadata_populated(self) -> None:
        """Test that metadata is properly populated."""
        composed = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
            task_type="new_code",
            domain="python_backend",
        )

        assert composed is not None
        metadata = composed.metadata
        assert metadata.anchor_id == "polaris_director"
        assert metadata.persona_id == "gongbu_shilang"
        assert metadata.profession_id == "python_principal_architect"
        assert metadata.task_type == "new_code"
        assert metadata.domain == "python_backend"
        assert metadata.version == "1.0"
        assert metadata.cache_key

    def test_cache_key_consistency(self) -> None:
        """Test that same inputs produce same cache key."""
        composed1 = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
        )

        composed2 = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
        )

        assert composed1 is not None
        assert composed2 is not None
        assert composed1.metadata.cache_key == composed2.metadata.cache_key

    def test_different_inputs_different_cache_keys(self) -> None:
        """Test that different inputs produce different cache keys."""
        composed1 = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
        )

        composed2 = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="security_auditor",
            persona_id="gongbu_shilang",
        )

        assert composed1 is not None
        assert composed2 is not None
        assert composed1.metadata.cache_key != composed2.metadata.cache_key

    def test_skip_cache_parameter(self) -> None:
        """Test skip_cache parameter."""
        composed1 = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
        )

        composed2 = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
            skip_cache=True,
        )

        assert composed1 is not None
        assert composed2 is not None
        # Both should have same cache key, but skip_cache forces regeneration
        assert composed1.metadata.cache_key == composed2.metadata.cache_key

    def test_workflow_in_composed(self) -> None:
        """Test that workflow is included in composed prompt."""
        composed = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
            task_type="new_code",
        )

        assert composed is not None
        assert composed.workflow
        assert "blueprint_then_execute" in str(composed.workflow)

    def test_engineering_standards_in_composed(self) -> None:
        """Test that engineering_standards is included in composed prompt."""
        composed = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
        )

        assert composed is not None
        assert composed.engineering_standards
        standards = composed.engineering_standards
        assert standards.get("coverage_mode") == "strict"

    def test_invalidate_cache_by_anchor(self) -> None:
        """Test cache invalidation by anchor."""
        # First composition
        self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
        )

        # Invalidate
        self.composer.invalidate_cache(anchor_id="polaris_director")

        # Second composition should regenerate
        composed = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
        )
        assert composed is not None

    def test_invalidate_all_cache(self) -> None:
        """Test clearing all cache."""
        # Create some cached entries
        self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
        )

        # Clear all
        self.composer.invalidate_cache()

        # Should still work but rebuild cache
        composed = self.composer.compose(
            anchor_id="polaris_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
        )
        assert composed is not None


class TestGetRoleComposer:
    """Tests for get_role_composer singleton."""

    def test_returns_singleton(self) -> None:
        """Test that get_role_composer returns singleton."""
        composer1 = get_role_composer()
        composer2 = get_role_composer()
        assert composer1 is composer2


class TestComposedPrompt:
    """Tests for ComposedPrompt dataclass."""

    def test_prompt_metadata_dataclass(self) -> None:
        """Test PromptMetadata dataclass."""
        metadata = PromptMetadata(
            anchor_id="polaris_director",
            persona_id="gongbu_shilang",
            profession_id="python_principal_architect",
            task_type="new_code",
            version="1.0",
            cache_key="abc123",
        )

        assert metadata.anchor_id == "polaris_director"
        assert metadata.cache_key == "abc123"

    def test_composed_prompt_dataclass(self) -> None:
        """Test ComposedPrompt dataclass."""
        composed = ComposedPrompt(
            system_prompt="<role_definition>test</role_definition>",
            metadata=PromptMetadata(
                anchor_id="test",
                persona_id="test",
                profession_id="test",
            ),
        )

        assert composed.system_prompt == "<role_definition>test</role_definition>"
        assert composed.metadata.anchor_id == "test"
