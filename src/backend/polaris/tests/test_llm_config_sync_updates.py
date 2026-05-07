"""Tests for llm_config_sync utilities.

Verifies that compute_llm_config_sync_updates returns correct delta dicts
and that the deprecated sync_settings_from_llm still works with a mock object.
"""

from __future__ import annotations

from polaris.cells.llm.provider_config.internal.settings_sync import (
    compute_llm_config_sync_updates,
    sync_settings_from_llm,
)


class TestComputeLlMConfigSyncUpdates:
    def test_returns_empty_dict_for_empty_config(self):
        updates = compute_llm_config_sync_updates({})
        assert updates == {}

    def test_maps_pm_model(self):
        config = {
            "roles": {
                "pm": {"model": "gpt-4"},
            },
        }
        updates = compute_llm_config_sync_updates(config)
        assert updates.get("pm_backend") == "auto"
        assert updates.get("pm_model") == "gpt-4"
        assert updates.get("model") == "gpt-4"

    def test_maps_legacy_model_from_pm_role_to_prevent_stale_fallback(self):
        config = {
            "roles": {
                "pm": {"model": "MiniMax-M2.7-highspeed"},
            },
        }

        updates = compute_llm_config_sync_updates(config)

        assert updates["model"] == "MiniMax-M2.7-highspeed"

    def test_maps_director_model(self):
        config = {
            "roles": {
                "director": {"model": "claude-3-5"},
            },
        }
        updates = compute_llm_config_sync_updates(config)
        assert updates.get("director_model") == "claude-3-5"

    def test_maps_openai_compat_provider_with_base_url(self):
        config = {
            "roles": {
                "architect": {
                    "provider_id": "my_custom",
                    "model": "gpt-4o",
                },
            },
            "providers": {
                "my_custom": {
                    "type": "openai_compat",
                    "base_url": "https://api.example.com",
                    "api_path": "/chat/completions",
                },
            },
        }
        updates = compute_llm_config_sync_updates(config)
        assert updates.get("architect_spec_provider") == "custom"
        assert updates.get("docs_init_provider") == "custom"
        assert updates.get("architect_spec_base_url") == "https://api.example.com"
        assert updates.get("docs_init_base_url") == "https://api.example.com"
        assert updates.get("architect_spec_api_path") == "/chat/completions"
        assert updates.get("docs_init_api_path") == "/chat/completions"
        assert updates.get("architect_spec_model") == "gpt-4o"
        assert updates.get("docs_init_model") == "gpt-4o"

    def test_maps_ollama_provider(self):
        config = {
            "roles": {
                "architect": {
                    "provider_id": "ollama_local",
                    "model": "llama3",
                },
            },
            "providers": {
                "ollama_local": {
                    "type": "ollama",
                    "base_url": "http://120.24.117.59:11434",
                },
            },
        }
        updates = compute_llm_config_sync_updates(config)
        assert updates.get("architect_spec_provider") == "ollama"
        assert updates.get("docs_init_provider") == "ollama"

    def test_maps_codex_cli_provider(self):
        config = {
            "roles": {
                "architect": {
                    "provider_id": "codex_cli",
                    "model": "codex",
                },
            },
            "providers": {
                "codex_cli": {
                    "type": "cli",
                    "command": "codex",
                },
            },
        }
        updates = compute_llm_config_sync_updates(config)
        assert updates.get("architect_spec_provider") == "codex"
        assert updates.get("docs_init_provider") == "codex"

    def test_handles_docs_alias_for_architect_role(self):
        config = {
            "roles": {
                "docs": {
                    "provider_id": "ollama_local",
                    "model": "llama3",
                },
            },
            "providers": {
                "ollama_local": {
                    "type": "ollama",
                },
            },
        }
        updates = compute_llm_config_sync_updates(config)
        assert updates.get("architect_spec_provider") == "ollama"
        assert updates.get("docs_init_provider") == "ollama"

    def test_handles_non_dict_roles_gracefully(self):
        config = {
            "roles": "not_a_dict",
            "providers": "not_a_dict",
        }
        updates = compute_llm_config_sync_updates(config)
        assert updates == {}


class TestSyncSettingsFromLlM:
    """Tests for the backward-compatible sync_settings_from_llm function."""

    def test_sets_attributes_on_mutable_object(self):
        class MockSettings:
            pass

        settings = MockSettings()
        config = {
            "roles": {
                "pm": {"model": "gpt-5"},
            },
        }
        sync_settings_from_llm(settings, config)
        assert settings.pm_backend == "auto"
        assert settings.pm_model == "gpt-5"
        assert settings.model == "gpt-5"

    def test_does_not_raise_on_non_dict_roles(self):
        class MockSettings:
            pass

        settings = MockSettings()
        config = {"roles": "not_a_dict"}
        # Should not raise
        sync_settings_from_llm(settings, config)
