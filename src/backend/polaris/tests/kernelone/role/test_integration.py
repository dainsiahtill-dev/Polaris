"""Integration tests for Tri-Axis Role Composition Engine.

These tests verify the complete workflow from configuration loading
through prompt composition and provider binding.
"""

from __future__ import annotations

from polaris.kernelone.role import (
    FallbackChain,
    get_hot_swap_engine,
    get_provider_resolver,
    get_role_composer,
    init_provider_resolver_from_config,
    stage,
)


class TestEndToEndComposition:
    """End-to-end tests for role composition."""

    def test_compose_senior_python_architect_full_workflow(self) -> None:
        """Test complete composition for senior_python_architect."""
        composer = get_role_composer()

        # Compose with new_code task type
        composed = composer.compose_by_recipe("senior_python_architect", task_type="new_code")

        assert composed is not None
        assert len(composed.system_prompt) > 500
        assert "<role_definition>" in composed.system_prompt
        assert "<workflow>" in composed.system_prompt
        assert "<engineering_standards>" in composed.system_prompt
        assert "<task_protocols>" in composed.system_prompt

    def test_compose_security_architect_full_workflow(self) -> None:
        """Test complete composition for security_architect."""
        composer = get_role_composer()

        composed = composer.compose_by_recipe("security_architect", task_type="code_review")

        assert composed is not None
        assert "安全" in composed.system_prompt or "威胁" in composed.system_prompt

    def test_all_builtin_recipes_work(self) -> None:
        """Test that all builtin recipes can be composed."""
        composer = get_role_composer()
        recipes = ["pm", "director", "qa", "architect", "chief_engineer"]

        for recipe_id in recipes:
            composed = composer.compose_by_recipe(recipe_id)
            assert composed is not None, f"Recipe {recipe_id} failed to compose"
            assert len(composed.system_prompt) > 0


class TestProviderBindingIntegration:
    """Integration tests for provider binding with role composition."""

    def test_provider_resolver_initialized_from_config(self) -> None:
        """Test that provider resolver is initialized from profession configs."""
        resolver = init_provider_resolver_from_config()

        # Python principal architect should have premium provider
        binding = resolver.get_binding("python_principal_architect")
        assert binding is not None
        assert "claude" in binding.primary.lower() or "gpt-4" in binding.primary.lower()

    def test_security_auditor_premium_provider(self) -> None:
        """Test that security auditor uses premium provider."""
        resolver = get_provider_resolver()

        # Ensure it's initialized
        init_provider_resolver_from_config()

        binding = resolver.get_binding("security_auditor")
        assert binding is not None
        assert "claude" in binding.primary.lower()

    def test_fallback_chain_for_profession(self) -> None:
        """Test fallback chain registration and usage."""
        from polaris.kernelone.role.hotswap import SwapReason

        engine = get_hot_swap_engine()

        chain = FallbackChain(
            primary="python_principal_architect",
            fallbacks=["software_engineer", "default"],
        )
        engine.register_fallback_chain("python_principal_architect", chain)

        # Verify the fallback chain is registered - use actual SwapReason
        engine.swap_with_fallback(
            session_id="test_session",
            primary_profession="python_principal_architect",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={},
        )
        # Just verify no error is raised


class TestStageWorkflowIntegration:
    """Integration tests for stage workflow with role composition."""

    def test_workflow_from_composed_prompt(self) -> None:
        """Test that workflow is properly extracted from composed prompt."""
        composer = get_role_composer()

        composed = composer.compose_by_recipe("senior_python_architect", task_type="new_code")

        assert composed is not None
        assert composed.workflow is not None

        # Verify workflow structure
        workflow_type = composed.workflow.get("type")
        assert workflow_type in ["blueprint_then_execute", "sequential"]

    def test_workflow_stages_parsed_correctly(self) -> None:
        """Test that workflow stages are correctly parsed into Stage objects."""
        composer = get_role_composer()

        composed = composer.compose_by_recipe("senior_python_architect", task_type="new_code")

        assert composed is not None
        assert composed.workflow is not None

        # Create workflow definition from config
        workflow_def = stage.create_workflow_from_config(
            "test_workflow",
            composed.workflow,
        )

        assert workflow_def is not None
        assert len(workflow_def.stages) > 0

        # Verify task type mapping
        applicable_stages = workflow_def.get_stages_for_task("new_code")
        assert len(applicable_stages) > 0


class TestHotSwapIntegration:
    """Integration tests for hot-swap mechanism."""

    def test_hot_swap_within_session(self) -> None:
        """Test hot-swap operation within a session."""
        from polaris.kernelone.role.hotswap import SwapReason

        engine = get_hot_swap_engine()
        session_id = "integration_test_session"

        # Initial composition
        result1 = engine.swap(
            session_id=session_id,
            new_profession="python_principal_architect",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={"turn": 0},
        )
        assert result1 is True

        # Swap to security auditor
        result2 = engine.swap(
            session_id=session_id,
            new_profession="security_auditor",
            reason=SwapReason.TASK_COMPLEXITY,
            context_snapshot={"turn": 1},
        )
        assert result2 is True

        # Verify history
        history = engine.get_swap_history(session_id)
        assert len(history) == 2
        assert history[0].to_profession == "python_principal_architect"
        assert history[1].to_profession == "security_auditor"

    def test_modifier_application(self) -> None:
        """Test that prompt modifiers can be added and retrieved."""

        engine = get_hot_swap_engine()
        session_id = "modifier_test_session"

        # Add modifiers
        engine.add_modifier(
            session_id=session_id,
            modifier_type="format_override",
            content="Use structured output format",
            priority=10,
        )
        engine.add_modifier(
            session_id=session_id,
            modifier_type="standard_addition",
            content="Additionally follow these security guidelines...",
            priority=5,
        )

        # Retrieve modifiers
        modifiers = engine.get_modifiers(session_id)
        assert len(modifiers) == 2

        # Higher priority should come last (applied later)
        assert modifiers[0].modifier_type == "standard_addition"
        assert modifiers[1].modifier_type == "format_override"


class TestBackwardCompatibility:
    """Tests to verify backward compatibility with existing system."""

    def test_legacy_role_id_still_works(self) -> None:
        """Test that legacy role IDs (director, pm, qa) still work."""
        composer = get_role_composer()

        # These should work as legacy aliases
        for legacy_id in ["director", "pm", "qa"]:
            composed = composer.compose_by_recipe(legacy_id)
            assert composed is not None, f"Legacy role {legacy_id} failed"

    def test_persona_backward_compatible(self) -> None:
        """Test that existing persona loading still works."""
        from polaris.kernelone.role import get_persona_loader

        loader = get_persona_loader()

        # Load existing personas
        for persona_id in ["gongbu_shilang", "shangshuling", "zhongshuling", "mentu_xiaozhong"]:
            persona = loader.load(persona_id)
            assert persona is not None
            assert persona.name
            assert len(persona.vocabulary) > 0


class TestSchemaValidation:
    """Tests for schema validation of configurations."""

    def test_validate_all_configs(self) -> None:
        """Test that all configuration files pass validation."""
        from polaris.kernelone.role.schema_validator import validate_all_configs

        results = validate_all_configs()
        assert results == {}, f"Validation errors: {results}"

    def test_profession_configs_are_valid(self) -> None:
        """Test that all profession configs are valid."""
        from polaris.kernelone.role import get_profession_loader

        loader = get_profession_loader()

        professions = [
            "python_principal_architect",
            "security_auditor",
            "software_engineer",
            "project_manager",
            "quality_engineer",
        ]

        for prof_id in professions:
            profession = loader.load(prof_id)
            assert profession is not None, f"Failed to load {prof_id}"
            assert profession.identity
            assert len(profession.expertise) > 0
            assert profession.engineering_standards
            assert profession.task_protocols
