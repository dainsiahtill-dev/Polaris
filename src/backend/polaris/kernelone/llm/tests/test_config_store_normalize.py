"""Tests for normalize_llm_config function."""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.kernelone.constants import MAX_LLM_PROVIDER_TIMEOUT_SECONDS
from polaris.kernelone.llm.config_store import (
    normalize_llm_config,
    validate_llm_config,
)


class TestNormalizeLLMConfig:
    """Tests for normalize_llm_config function."""

    def test_normalize_empty_payload(self) -> None:
        """Test normalize handles empty payload."""
        result = normalize_llm_config({})
        assert "schema_version" in result
        assert result["schema_version"] == 1

    def test_normalize_with_providers(self) -> None:
        """Test normalize preserves user providers."""
        payload = {
            "providers": {"custom_provider": {"type": "openai_compat", "base_url": "https://custom.example.com"}}
        }
        result = normalize_llm_config(payload)
        assert "custom_provider" in result["providers"]

    def test_normalize_roles_architect_alias(self) -> None:
        """Test normalize converts 'docs' role to 'architect'."""
        payload = {"roles": {"docs": {"provider_id": "ollama", "model": "test"}}}
        result = normalize_llm_config(payload)
        assert "architect" in result["roles"]
        assert "docs" not in result["roles"]

    def test_normalize_preserves_existing_architect(self) -> None:
        """Test normalize preserves existing architect role."""
        payload = {"roles": {"architect": {"provider_id": "codex_cli", "model": "claude-3"}}}
        result = normalize_llm_config(payload)
        assert result["roles"]["architect"]["provider_id"] == "codex_cli"

    def test_normalize_policies_role_requirements_merge(self) -> None:
        """Test normalize merges role_requirements from base and payload."""
        payload = {"policies": {"role_requirements": {"pm": {"min_score": 80}}}}
        result = normalize_llm_config(payload)
        assert "role_requirements" in result["policies"]

    def test_normalize_required_ready_roles_normalized(self) -> None:
        """Test normalize filters out architect/docs from required_ready_roles."""
        payload = {"policies": {"required_ready_roles": ["pm", "director", "architect", "docs"]}}
        result = normalize_llm_config(payload)
        assert "architect" not in result["policies"].get("required_ready_roles", [])
        assert "docs" not in result["policies"].get("required_ready_roles", [])
        assert "pm" in result["policies"].get("required_ready_roles", [])
        assert "director" in result["policies"].get("required_ready_roles", [])

    def test_normalize_passthrough_unknown_keys(self) -> None:
        """Test normalize passes through unknown keys."""
        payload = {"custom_field": "custom_value", "another_field": 123}
        result = normalize_llm_config(payload)
        assert result.get("custom_field") == "custom_value"
        assert result.get("another_field") == 123

    def test_normalize_schema_version_defaults_to_1(self) -> None:
        """Test normalize defaults schema_version to 1."""
        result = normalize_llm_config({})
        assert result["schema_version"] == 1

    def test_normalize_with_settings(self) -> None:
        """Test normalize uses settings for defaults."""
        mock_settings = MagicMock()
        mock_settings.pm_backend = "codex"
        mock_settings.pm_model = "claude-3"
        mock_settings.director_model = "gpt-4"

        result = normalize_llm_config({}, settings=mock_settings)
        assert result["roles"]["pm"]["provider_id"] == "codex_cli"

    def test_normalize_visual_layout_defaults_to_empty_dict(self) -> None:
        """Test normalize defaults visual_layout to empty dict."""
        result = normalize_llm_config({})
        assert result["visual_layout"] == {}

    def test_normalize_visual_node_states_defaults_to_empty_dict(self) -> None:
        """Test normalize defaults visual_node_states to empty dict."""
        result = normalize_llm_config({})
        assert result["visual_node_states"] == {}


class TestValidateLLMConfig:
    """Tests for validate_llm_config function."""

    def test_validate_valid_config(self) -> None:
        """Test validate accepts valid configuration."""
        config = {
            "schema_version": 2,
            "providers": {"test": {"type": "ollama"}},
            "roles": {"pm": {"provider_id": "test"}},
        }
        is_valid, errors, _ = validate_llm_config(config)
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_accepts_long_provider_timeout(self) -> None:
        """Provider saves must allow long LLM calls without rejecting valid config."""
        config = {
            "schema_version": 2,
            "providers": {
                "slow-provider": {
                    "type": "minimax",
                    "timeout": 360,
                }
            },
            "roles": {"pm": {"provider_id": "slow-provider"}},
        }
        is_valid, errors, _ = validate_llm_config(config)
        assert is_valid is True
        assert errors == []

    def test_validate_rejects_unbounded_provider_timeout(self) -> None:
        """Provider timeout still has a hard safety bound."""
        config = {
            "schema_version": 2,
            "providers": {
                "unbounded-provider": {
                    "type": "minimax",
                    "timeout": MAX_LLM_PROVIDER_TIMEOUT_SECONDS + 1,
                }
            },
            "roles": {"pm": {"provider_id": "unbounded-provider"}},
        }
        is_valid, errors, _ = validate_llm_config(config)
        assert is_valid is False
        assert any("timeout" in error.lower() for error in errors)

    def test_validate_missing_provider_type(self) -> None:
        """Test validate detects missing provider type."""
        config = {"schema_version": 2, "providers": {"test": {"name": "Test Provider"}}}
        is_valid, errors, _ = validate_llm_config(config)
        assert is_valid is False
        assert any("type" in e.lower() for e in errors)

    def test_validate_missing_role_provider(self) -> None:
        """Test validate detects missing role provider."""
        config = {
            "schema_version": 2,
            "providers": {"test": {"type": "ollama"}},
            "roles": {"pm": {"provider_id": "nonexistent"}},
        }
        is_valid, errors, _ = validate_llm_config(config)
        assert is_valid is False
        assert any("non-existent" in e.lower() for e in errors)

    def test_validate_rejects_required_role_without_provider_id(self) -> None:
        """Required role rows must be present with provider_id before saving."""
        config = {
            "schema_version": 2,
            "providers": {"test": {"type": "ollama"}},
            "roles": {"pm": {"model": "llama3"}},
            "policies": {"required_ready_roles": ["pm"]},
        }
        is_valid, errors, _ = validate_llm_config(config)
        assert is_valid is False
        assert any("missing 'provider_id'" in e for e in errors)

    def test_validate_warns_dangerous_sandbox(self) -> None:
        """Test validate warns about dangerous sandbox mode."""
        config = {
            "schema_version": 2,
            "providers": {"codex": {"type": "codex_cli", "codex_exec": {"sandbox": "danger-full-access"}}},
        }
        _, _, warnings = validate_llm_config(config)
        assert any("danger" in w.lower() for w in warnings)

    def test_validate_rejects_non_dict_config(self) -> None:
        """Test validate rejects non-dictionary config."""
        is_valid, errors, _ = validate_llm_config("not a dict")
        assert is_valid is False
        assert "dictionary" in errors[0].lower()

    def test_validate_invalid_schema_version_type(self) -> None:
        """Test validate handles invalid schema_version type."""
        config = {"schema_version": "invalid", "providers": {}, "roles": {}}
        is_valid, errors, _ = validate_llm_config(config)
        assert is_valid is False
        assert len(errors) > 0
