"""Phase 0 Regression Tests for LLM Configuration Unification

Tests for:
1. Default config unique provider keys (minimax fix)
2. LLMStatus contains last_updated field
3. LLMConfig atomic write and UTF-8 roundtrip
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from polaris.kernelone.storage import resolve_runtime_path

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


@pytest.fixture(autouse=True)
def isolate_polaris_root(tmp_path, monkeypatch):
    app_root = tmp_path / "polaris_root"
    app_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POLARIS_ROOT", str(app_root))
    # llm_config uses POLARIS_HOME via storage_layout.resolve_global_path.
    # Isolate it to avoid touching the real user config during tests.
    monkeypatch.setenv("POLARIS_HOME", str(app_root))
    return app_root


class TestLLMDefaultConfigUniqueProviderKeys:
    """Test that default LLM config has unique provider keys."""

    def test_default_config_no_duplicate_provider_keys(self):
        """Ensure no duplicate provider IDs exist in default config."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        providers = config.get("providers", {})

        provider_ids = list(providers.keys())
        duplicate_ids = [pid for pid in provider_ids if provider_ids.count(pid) > 1]

        assert len(duplicate_ids) == 0, f"Found duplicate provider IDs in default config: {duplicate_ids}"

    def test_minimax_provider_appears_only_once(self):
        """Ensure minimax provider is defined exactly once with correct type."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        providers = config.get("providers", {})

        minimax_count = (
            providers.count("minimax")
            if hasattr(providers, "count")
            else sum(1 for k in providers.keys() if k == "minimax")
        )

        assert minimax_count == 1, f"Expected exactly one 'minimax' provider, found {minimax_count}"

        if "minimax" in providers:
            minimax_config = providers["minimax"]
            assert minimax_config.get("type") == "minimax", (
                f"Expected minimax type 'minimax', got '{minimax_config.get('type')}'"
            )


class TestLLMStatusLastUpdated:
    """Test that LLMStatus response includes last_updated field."""

    def test_status_response_has_last_updated_field(self):
        """Verify /llm/status endpoint returns last_updated."""
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.pm_backend = "openai"
        mock_settings.pm_model = "gpt-4"
        mock_settings.director_model = None
        mock_settings.docs_model = None
        mock_settings.qa_model = None
        mock_settings.qa_enabled = True

        with patch(
            "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
            return_value={"schema_version": 1, "providers": {}, "roles": {}},
        ):
            with patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value={"providers": {}, "roles": {}},
            ):
                with patch(
                    "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                    return_value={},
                ):
                    with patch(
                        "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                        return_value="/tmp/test_cache",
                    ):
                        response = build_llm_status(mock_settings)

                        assert "last_updated" in response, "Response missing 'last_updated' field"
                        assert response["last_updated"] is None or isinstance(response["last_updated"], str), (
                            f"last_updated should be None or ISO string, got {type(response['last_updated'])}"
                        )

                        if response["last_updated"] is not None:
                            try:
                                datetime.fromisoformat(response["last_updated"])
                            except (TypeError, ValueError) as e:
                                pytest.fail(f"last_updated is not valid ISO format: {e}")


class TestRoleRuntimeSupportConsistency:
    """Keep llm/status and director runtime gate aligned on provider support."""

    def test_llm_status_marks_director_codex_as_supported(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli"},
            },
            "roles": {
                "director": {"provider_id": "codex_cli", "model": "gpt-5.2-codex"},
            },
            "policies": {
                "required_ready_roles": ["director"],
            },
        }

        with patch(
            "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
            return_value=config_payload,
        ):
            with patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value={"providers": {}, "roles": {}},
            ):
                with patch(
                    "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                    return_value={},
                ):
                    with patch(
                        "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                        return_value="/tmp/test_cache",
                    ):
                        response = build_llm_status(mock_settings)

        assert response["roles"]["director"]["runtime_supported"] is True
        assert "director" not in response["unsupported_roles"]

    def test_director_gate_allows_codex_and_generic_provider(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        base_index = {
            "roles": {
                "director": {"ready": True},
            }
        }

        codex_cfg = {
            "providers": {"codex_cli": {"type": "codex_cli"}},
            "roles": {"director": {"provider_id": "codex_cli", "model": "gpt-5.2-codex"}},
        }
        generic_cfg = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"director": {"provider_id": "openai_compat", "model": "gpt-4.1"}},
        }

        with patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"):
            with patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=base_index):
                with patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=codex_cfg):
                    _ensure_llm_ready(mock_state, "director")

                with patch(
                    "polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=generic_cfg
                ):
                    _ensure_llm_ready(mock_state, "director")

    def test_pm_gate_allows_ready_role_without_provider_type_restriction(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        base_index = {
            "roles": {
                "pm": {"ready": True},
            }
        }

        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "gpt-4.1"}},
        }

        with patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"):
            with patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=base_index):
                with patch(
                    "polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload
                ):
                    _ensure_llm_ready(mock_state, "pm")

    def test_director_start_requires_all_required_roles(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import ensure_required_roles_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True
        mock_state = AppState(settings=mock_settings)

        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {
                "pm": {"provider_id": "openai_compat", "model": "gpt-4.1"},
                "director": {"provider_id": "openai_compat", "model": "gpt-4.1"},
                "qa": {"provider_id": "openai_compat", "model": "gpt-4.1"},
            },
            "policies": {"required_ready_roles": ["pm", "director", "qa"]},
        }
        index_payload = {"roles": {"director": {"ready": True}, "qa": {"ready": True}}}

        with patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"):
            with patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload):
                with patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload):
                    with pytest.raises(HTTPException) as exc:
                        ensure_required_roles_ready(
                            mock_state, default_roles=["director", "qa"], force_first="director"
                        )

        assert exc.value.status_code == 409
        assert "pm" in exc.value.detail["missing_roles"]

    def test_pm_start_requires_all_required_roles(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import ensure_required_roles_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True
        mock_state = AppState(settings=mock_settings)

        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {
                "pm": {"provider_id": "openai_compat", "model": "gpt-4.1"},
                "director": {"provider_id": "openai_compat", "model": "gpt-4.1"},
                "qa": {"provider_id": "openai_compat", "model": "gpt-4.1"},
            },
            "policies": {"required_ready_roles": ["pm", "director", "qa"]},
        }
        index_payload = {"roles": {"pm": {"ready": True}, "director": {"ready": True}}}

        with patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"):
            with patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload):
                with patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload):
                    with pytest.raises(HTTPException) as exc:
                        ensure_required_roles_ready(mock_state, default_roles=["pm", "director", "qa"])

        assert exc.value.status_code == 409
        assert "qa" in exc.value.detail["missing_roles"]


class TestLLMConfigAtomicWrite:
    """Test LLMConfig atomic write and UTF-8 roundtrip."""

    def test_config_save_and_load_utf8_roundtrip(self, mock_workspace):
        """Verify config can be saved and loaded with UTF-8 characters preserved."""
        from polaris.kernelone.llm.config_store import load_llm_config, save_llm_config

        test_config = {
            "schema_version": 1,
            "providers": {
                "test_provider": {
                    "type": "openai_compat",
                    "name": "测试提供商",
                    "base_url": "https://api.test.com",
                    "api_key": "test_key_123",
                    "model": "test-model",
                    "description": "包含中文描述的配置",
                },
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "test_provider", "model": "test-model", "profile": "测试角色配置"},
                "director": {"provider_id": "ollama", "model": "test-model"},
                "qa": {"provider_id": "ollama", "model": "test-model"},
                "docs": {"provider_id": "openai_compat", "model": "test-model"},
            },
        }

        save_llm_config(mock_workspace, mock_workspace, test_config)

        loaded_config = load_llm_config(mock_workspace, mock_workspace)

        assert loaded_config.get("providers", {}).get("test_provider", {}).get("name") == "测试提供商", (
            "UTF-8 Chinese characters not preserved in provider name"
        )
        assert loaded_config.get("roles", {}).get("pm", {}).get("profile") == "测试角色配置", (
            "UTF-8 Chinese characters not preserved in role profile"
        )

    def test_config_atomic_write_pattern(self, mock_workspace):
        """Verify config uses atomic write pattern (tmp -> fsync -> rename)."""
        from polaris.kernelone.llm.config_store import llm_config_path, save_llm_config

        test_config = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "codex_cli", "model": "test-model"},
                "director": {"provider_id": "ollama", "model": "test-model"},
                "qa": {"provider_id": "ollama", "model": "test-model"},
                "docs": {"provider_id": "openai_compat", "model": "test-model"},
            },
        }

        config_path = llm_config_path(mock_workspace, mock_workspace)

        save_llm_config(mock_workspace, mock_workspace, test_config)

        assert os.path.isfile(config_path), "Config file was not created"

        assert not os.path.exists(config_path + ".tmp"), f"Found temporary file after write: {config_path}.tmp"

        with open(config_path, encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["schema_version"] == 2


class TestLLMVisualLayoutPersistence:
    """Test visual layout fields are preserved in config normalization and persistence."""

    def test_save_and_load_preserves_visual_fields(self, mock_workspace):
        """visual_layout / visual_node_states / visual_viewport should survive save+load."""
        from polaris.kernelone.llm.config_store import load_llm_config, save_llm_config

        payload = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "codex_cli", "model": "gpt-4.1"},
                "director": {"provider_id": "ollama", "model": "glm-4.7-flash:latest"},
                "qa": {"provider_id": "ollama", "model": "glm-4.7-flash:latest"},
                "docs": {"provider_id": "openai_compat", "model": "gpt-4.1-mini"},
            },
            "visual_layout": {
                "role:pm": {"x": 410.5, "y": 122.25},
                "provider:codex_cli": {"x": 84, "y": 48},
            },
            "visual_node_states": {
                "role:pm": {
                    "position": {"x": 410.5, "y": 122.25},
                    "selected": True,
                    "hidden": False,
                }
            },
            "visual_viewport": {"x": -20, "y": 16, "zoom": 1.15},
        }

        save_llm_config(mock_workspace, mock_workspace, payload)
        loaded = load_llm_config(mock_workspace, mock_workspace)

        assert loaded.get("visual_layout", {}).get("role:pm", {}).get("x") == 410.5
        assert loaded.get("visual_layout", {}).get("role:pm", {}).get("y") == 122.25
        assert loaded.get("visual_node_states", {}).get("role:pm", {}).get("position", {}).get("x") == 410.5
        assert loaded.get("visual_viewport", {}).get("zoom") == 1.15


class TestLLMConfigLoadPreservesUserFields:
    """Ensure loading config never rewrites or drops user-managed fields."""

    def test_load_llm_config_preserves_role_assignments_and_file_content(self, mock_workspace):
        from polaris.kernelone.llm.config_store import llm_config_path, load_llm_config

        path = llm_config_path(mock_workspace, mock_workspace)
        payload = {
            "schema_version": 1,
            "providers": {
                "minimax": {"type": "minimax"},
            },
            "roleAssignments": [
                {"roleId": "pm", "providerId": "minimax", "model": "MiniMax-M2.5"},
            ],
        }

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        with open(path, encoding="utf-8") as handle:
            before = handle.read()

        loaded = load_llm_config(mock_workspace, mock_workspace)

        with open(path, encoding="utf-8") as handle:
            after = handle.read()

        assert after == before, "load_llm_config must not rewrite user config file"
        assert isinstance(loaded.get("roleAssignments"), list)
        assert loaded["roleAssignments"][0]["providerId"] == "minimax"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestLLMConfigValidation:
    """Test validate_llm_config function for Phase 1 SSOT."""

    def test_valid_config_returns_no_errors(self):
        """Valid config should pass validation."""
        from polaris.kernelone.llm.config_store import build_default_config, validate_llm_config

        config = build_default_config()
        is_valid, errors, warnings = validate_llm_config(config)

        assert is_valid, f"Valid config failed validation with errors: {errors}"
        assert len(errors) == 0, f"Expected no errors, got: {errors}"

    def test_missing_provider_type_returns_error(self):
        """Config with missing provider type should fail validation."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        config = {"schema_version": 1, "providers": {"bad_provider": {"name": "Bad Provider"}}, "roles": {}}

        is_valid, errors, warnings = validate_llm_config(config)

        assert not is_valid, "Config with missing provider type should fail validation"
        assert any("Field required" in str(e) or "missing 'type'" in str(e) for e in errors), (
            f"Expected error about missing type field, got: {errors}"
        )

    def test_role_references_nonexistent_provider_returns_error(self):
        """Role referencing non-existent provider should fail validation."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        config = {
            "schema_version": 1,
            "providers": {"existing_provider": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "nonexistent_provider", "model": "test-model"}},
        }

        is_valid, errors, warnings = validate_llm_config(config)

        assert not is_valid, "Config with invalid provider reference should fail"
        assert any("non-existent provider" in e for e in errors), (
            f"Expected error about non-existent provider, got: {errors}"
        )

    def test_required_role_not_defined_returns_error(self):
        """Required role not in roles should fail validation."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        config = {
            "schema_version": 1,
            "providers": {"test_provider": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "test_provider", "model": "test"}},
            "policies": {"required_ready_roles": ["director", "qa"]},
        }

        is_valid, errors, warnings = validate_llm_config(config)

        assert not is_valid, "Config with missing required roles should fail"
        assert any("not defined in roles" in e for e in errors), (
            f"Expected error about missing required role, got: {errors}"
        )

    def test_non_dict_config_returns_error(self):
        """Non-dict config should fail validation."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        is_valid, errors, warnings = validate_llm_config("not a dict")

        assert not is_valid, "Non-dict config should fail validation"
        assert any("must be a dictionary" in e for e in errors), f"Expected error about dict type, got: {errors}"

    def test_provider_id_matches_type_field(self):
        """Provider ID should match the provider's type field."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()

        mismatched_providers = []
        for provider_id, provider_cfg in config.get("providers", {}).items():
            provider_type = provider_cfg.get("type")
            if provider_type and provider_type != provider_id:
                mismatched_providers.append((provider_id, provider_type))

        assert len(mismatched_providers) == 0, (
            f"Found providers where ID doesn't match type field: {mismatched_providers}"
        )

    def test_all_roles_have_valid_provider_reference(self):
        """All roles should reference valid providers."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()

        roles = config.get("roles", {})
        providers = config.get("providers", {})

        invalid_role_refs = []
        for role_id, role_cfg in roles.items():
            if isinstance(role_cfg, dict):
                provider_id = role_cfg.get("provider_id")
                if provider_id and provider_id not in providers:
                    invalid_role_refs.append((role_id, provider_id))

        assert len(invalid_role_refs) == 0, f"Roles with invalid provider references: {invalid_role_refs}"


class TestLLMConfigStandardProviders:
    """Test that standard providers are properly configured."""

    def test_all_required_providers_present(self):
        """Ensure all required providers are defined."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        providers = config.get("providers", {})

        required_providers = ["ollama", "openai_compat"]
        for required in required_providers:
            assert required in providers, f"Required provider '{required}' not found"

    def test_standard_openai_compat_config(self):
        """Verify openai_compat provider has correct base structure."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        provider = config.get("providers", {}).get("openai_compat")

        assert provider is not None, "openai_compat provider not found"
        assert provider.get("type") == "openai_compat", f"Expected type 'openai_compat', got '{provider.get('type')}'"
        assert "api_path" in provider, "openai_compat missing api_path"
        # models_path is deprecated and removed from default config

    def test_no_duplicate_minimax_entries(self):
        """Ensure no duplicate minimax-related entries exist."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        providers = config.get("providers", {})

        minimax_entries = [k for k in providers.keys() if "minimax" in k.lower()]
        minimax_types = [v.get("type") for v in providers.values() if "minimax" in str(v.get("type", "")).lower()]

        assert len(minimax_entries) <= 1, f"Found multiple minimax entries: {minimax_entries}"
        assert len(minimax_types) <= 1, f"Found multiple minimax types: {minimax_types}"


class TestLLMSaveConfigValidation:
    """Test that save_llm_config validates config before saving."""

    def test_save_invalid_config_raises_error(self, mock_workspace):
        """Invalid config should raise ValueError during save."""
        from polaris.kernelone.llm.config_store import save_llm_config

        invalid_config = {"schema_version": 1, "providers": {"bad_provider": {"name": "No type"}}, "roles": {}}

        with pytest.raises(ValueError) as exc_info:
            save_llm_config(mock_workspace, mock_workspace, invalid_config)

        assert "Invalid LLM configuration" in str(exc_info.value)
        assert "missing 'type' field" in str(exc_info.value) or "Field required" in str(exc_info.value)

    def test_save_config_with_invalid_role_reference_raises_error(self, mock_workspace):
        """Config with invalid provider reference should raise ValueError."""
        from polaris.kernelone.llm.config_store import save_llm_config

        invalid_config = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {"pm": {"provider_id": "nonexistent", "model": "test"}},
        }

        with pytest.raises(ValueError) as exc_info:
            save_llm_config(mock_workspace, mock_workspace, invalid_config)

        assert "non-existent provider" in str(exc_info.value)

    def test_save_valid_config_succeeds(self, mock_workspace):
        """Valid config should be saved without errors."""
        from polaris.kernelone.llm.config_store import load_llm_config, save_llm_config

        valid_config = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "codex_cli", "model": "test"},
                "director": {"provider_id": "ollama", "model": "test"},
                "qa": {"provider_id": "ollama", "model": "test"},
                "docs": {"provider_id": "openai_compat", "model": "test"},
            },
        }

        result = save_llm_config(mock_workspace, mock_workspace, valid_config)

        assert result is not None
        loaded = load_llm_config(mock_workspace, mock_workspace)
        assert loaded.get("schema_version") == 2


class TestSettingsPersistence:
    """Regression tests for global settings persistence."""

    @staticmethod
    def _write_json(path: str, payload: dict) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def _seed_legacy_llm_payload() -> dict:
        return {
            "schema_version": 1,
            "providers": {
                "custom": {"type": "openai_compat", "name": "Custom"},
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "custom", "model": "custom-model"},
                "director": {"provider_id": "ollama", "model": "director-model"},
                "qa": {"provider_id": "ollama", "model": "qa-model"},
                "docs": {"provider_id": "openai_compat", "model": "docs-model"},
            },
        }

    def test_save_settings_into_global_config(self, tmp_path, monkeypatch):
        from polaris.bootstrap.config import Settings
        from polaris.cells.storage.layout.internal.settings_utils import (
            get_legacy_settings_path,
            get_settings_path,
            save_persisted_settings,
        )

        appdata = tmp_path / "appdata"
        monkeypatch.setenv("APPDATA", str(appdata))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        settings = Settings(
            workspace=str(workspace),
            pm_backend="ollama",
            pm_model="test-pm",
            director_model="test-director",
            model="test-model",
        )

        save_persisted_settings(settings)

        global_settings_path = get_settings_path(str(workspace))
        assert os.path.isfile(global_settings_path)
        with open(global_settings_path, encoding="utf-8") as handle:
            global_payload = json.load(handle)
        assert global_payload.get("workspace") == os.path.abspath(str(workspace))
        assert global_payload.get("pm_backend") == "ollama"

        legacy_path = get_legacy_settings_path()
        assert os.path.isfile(legacy_path)
        with open(legacy_path, encoding="utf-8") as handle:
            legacy_payload = json.load(handle)
        assert legacy_payload == {"workspace": os.path.abspath(str(workspace))}

    def test_load_settings_migrates_legacy_to_global(self, tmp_path, monkeypatch):
        from polaris.cells.storage.layout.internal.settings_utils import (
            get_legacy_settings_path,
            get_settings_path,
            load_persisted_settings,
        )

        appdata = tmp_path / "appdata"
        monkeypatch.setenv("APPDATA", str(appdata))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        legacy_payload = {
            "workspace": os.path.abspath(str(workspace)),
            "pm_backend": "ollama",
            "pm_model": "legacy-model",
            "auto_refresh": False,
        }
        self._write_json(get_legacy_settings_path(), legacy_payload)

        loaded = load_persisted_settings()
        assert loaded.get("workspace") == os.path.abspath(str(workspace))
        assert loaded.get("pm_backend") == "ollama"

        global_settings_path = get_settings_path(str(workspace))
        assert os.path.isfile(global_settings_path)
        with open(global_settings_path, encoding="utf-8") as handle:
            migrated_payload = json.load(handle)
        assert migrated_payload.get("workspace") == os.path.abspath(str(workspace))
        assert migrated_payload.get("pm_model") == "legacy-model"

    def test_load_settings_migrates_workspace_scoped_settings_to_global(self, tmp_path):
        from polaris.cells.storage.layout.internal.settings_utils import (
            get_settings_path,
            get_workspace_settings_path,
            load_persisted_settings,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        legacy_workspace_payload = {
            "workspace": os.path.abspath(str(workspace)),
            "pm_backend": "ollama",
            "pm_model": "workspace-legacy-model",
        }
        self._write_json(get_workspace_settings_path(str(workspace)), legacy_workspace_payload)

        loaded = load_persisted_settings(str(workspace))
        assert loaded.get("pm_model") == "workspace-legacy-model"

        global_settings_path = get_settings_path()
        with open(global_settings_path, encoding="utf-8") as handle:
            global_payload = json.load(handle)
        assert global_payload.get("pm_model") == "workspace-legacy-model"

    def test_load_llm_config_ignores_runtime_legacy_config(self, tmp_path):
        from polaris.kernelone.llm.config_store import llm_config_path, load_llm_config

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        legacy_path = Path(resolve_runtime_path(str(workspace), "runtime/config/llm_config.json"))
        self._write_json(str(legacy_path), self._seed_legacy_llm_payload())

        loaded = load_llm_config(str(workspace), "")
        target_path = llm_config_path(str(workspace), "")
        assert os.path.isfile(target_path)

        with open(target_path, encoding="utf-8") as handle:
            persisted_payload = json.load(handle)
        assert "custom" not in persisted_payload.get("providers", {})
        assert loaded.get("roles", {}).get("pm", {}).get("provider_id") != "custom"


class TestPmBackendRuntimeResolution:
    def test_sync_settings_sets_pm_backend_auto_for_generic_provider(self):
        from polaris.cells.llm.provider_config.internal.settings_sync import sync_settings_from_llm

        settings = MagicMock()
        settings.pm_backend = "codex"
        settings.pm_model = ""

        payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "gpt-4.1"}},
        }

        sync_settings_from_llm(settings, payload)

        assert settings.pm_backend == "auto"
        assert settings.pm_model == "gpt-4.1"

    def test_check_backend_available_ignores_stale_codex_when_runtime_is_generic(self):
        from polaris.bootstrap.runtime_health import check_backend_available

        settings = MagicMock()
        settings.pm_backend = "codex"
        settings.workspace = "/tmp/workspace"
        settings.ramdisk_root = ""

        llm_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "gpt-4.1"}},
        }

        with patch("polaris.kernelone.storage.io_paths.build_cache_root", return_value=""):
            with patch("polaris.kernelone.llm.config_store.load_llm_config", return_value=llm_payload):
                with patch("shutil.which", return_value=None):
                    error = check_backend_available(settings)

        assert error is None

    def test_check_backend_available_requires_pm_role_mapping(self, tmp_path, monkeypatch):
        from polaris.bootstrap.runtime_health import check_backend_available

        settings = MagicMock()
        settings.pm_backend = "auto"
        settings.workspace = "/tmp/workspace"
        settings.ramdisk_root = ""

        monkeypatch.setenv("POLARIS_HOME", str(tmp_path / "polaris-home"))
        with patch("polaris.kernelone.storage.io_paths.build_cache_root", return_value=""):
            error = check_backend_available(settings)

        assert isinstance(error, str)
        assert "PM role mapping is missing or incomplete" in error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
