"""
Tests for LLM Configuration Store Security Hardening

Tests cover:
1. Sensitive value masking/restoration
2. Strict Pydantic validation
3. Configuration backup mechanism
4. Audit logging
5. Schema migration framework
6. Codex CLI security defaults
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestSensitiveValueRestoration:
    """Tests for _restore_masked_sensitive_values function."""

    def test_restore_exact_masked_secret(self) -> None:
        """Restore when value exactly equals MASKED_SECRET."""
        from polaris.kernelone.llm.config_store import MASKED_SECRET, _restore_masked_sensitive_values

        previous = "my-secret-api-key-12345"
        result = _restore_masked_sensitive_values(MASKED_SECRET, previous, key_hint="api_key")
        assert result == previous

    def test_restore_mostly_masked_value(self) -> None:
        """Restore when value is >50% asterisks."""
        from polaris.kernelone.llm.config_store import _restore_masked_sensitive_values

        previous = "super-secret-token-xyz"
        # Value with >50% asterisks (7 out of 13 = 54%)
        mostly_masked = "*******secret"
        result = _restore_masked_sensitive_values(mostly_masked, previous, key_hint="token")
        assert result == previous

    def test_keep_partially_edited_value(self) -> None:
        """Keep value when only partially masked (<50%)."""
        from polaris.kernelone.llm.config_store import _restore_masked_sensitive_values

        previous = "old-key"
        new_value = "new-partially-edited-key"
        result = _restore_masked_sensitive_values(new_value, previous, key_hint="api_key")
        # Should keep the new value since it's not mostly masked
        assert result == new_value

    def test_restore_nested_sensitive_values(self) -> None:
        """Restore nested sensitive values in dict."""
        from polaris.kernelone.llm.config_store import MASKED_SECRET, _restore_masked_sensitive_values

        previous = {"providers": {"test": {"api_key": "secret-previous-key", "timeout": 60}}}
        new_value = {"providers": {"test": {"api_key": MASKED_SECRET, "timeout": 120}}}
        result = _restore_masked_sensitive_values(new_value, previous, key_hint="")

        assert result["providers"]["test"]["api_key"] == "secret-previous-key"
        assert result["providers"]["test"]["timeout"] == 120

    def test_non_sensitive_key_unchanged(self) -> None:
        """Non-sensitive keys should not trigger restoration."""
        from polaris.kernelone.llm.config_store import MASKED_SECRET, _restore_masked_sensitive_values

        previous = {"model": "gpt-4", "timeout": 60}
        new_value = {"model": MASKED_SECRET, "timeout": 120}
        result = _restore_masked_sensitive_values(new_value, previous, key_hint="")

        # Model is not a sensitive key, so MASKED_SECRET stays as-is
        assert result["model"] == MASKED_SECRET
        assert result["timeout"] == 120


class TestPydanticValidation:
    """Tests for strict Pydantic validation models."""

    def test_valid_provider_config(self) -> None:
        """Valid provider config passes validation."""
        from polaris.kernelone.llm.config_store import ProviderConfig

        config = ProviderConfig(type="ollama", name="Ollama Local", timeout=60.0)
        assert config.type == "ollama"
        assert config.timeout == 60.0

    def test_invalid_timeout_raises(self) -> None:
        """Invalid timeout (out of range) raises validation error."""
        from polaris.kernelone.llm.config_store import ProviderConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProviderConfig(
                type="ollama",
                timeout=500.0,  # Exceeds max of 300
            )

    def test_invalid_temperature_raises(self) -> None:
        """Invalid temperature (out of range) raises validation error."""
        from polaris.kernelone.llm.config_store import ProviderConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProviderConfig(
                type="ollama",
                temperature=5.0,  # Exceeds max of 2
            )

    def test_invalid_max_tokens_raises(self) -> None:
        """Invalid max_tokens (out of range) raises validation error."""
        from polaris.kernelone.llm.config_store import ProviderConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProviderConfig(
                type="ollama",
                max_tokens=200000,  # Exceeds max of 100000
            )

    def test_valid_llm_config_schema(self) -> None:
        """Valid full LLM config passes validation."""
        from polaris.kernelone.llm.config_store import LLMConfigSchema

        # Pydantic will coerce dicts to ProviderConfig/RoleConfig models
        config = LLMConfigSchema(
            schema_version=2,
            providers={
                "ollama": {  # type: ignore[dict-item]
                    "type": "ollama",
                    "timeout": 60.0,
                }
            },
            roles={
                "pm": {  # type: ignore[dict-item]
                    "provider_id": "ollama",
                    "model": "llama2",
                }
            },
        )
        assert config.schema_version == 2
        assert "ollama" in config.providers

    def test_invalid_schema_version_raises(self) -> None:
        """Schema version > 10 raises validation error."""
        from polaris.kernelone.llm.config_store import LLMConfigSchema
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LLMConfigSchema(
                schema_version=99  # Exceeds sanity max
            )


class TestSchemaMigration:
    """Tests for schema migration framework."""

    def test_migrate_v1_to_v2_normalizes_providers(self) -> None:
        """Migration v1->v2 normalizes single provider to providers dict."""
        from polaris.kernelone.llm.config_store import migrate_config

        # v1 config with single 'provider' field
        v1_config = {"schema_version": 1, "provider": {"type": "ollama", "base_url": "http://192.168.1.2:11434"}}

        migrated = migrate_config(v1_config, target_version=2)

        assert migrated["schema_version"] == 2
        assert "providers" in migrated
        assert "default" in migrated["providers"]

    def test_migrate_v1_to_v2_fixes_dangerous_defaults(self) -> None:
        """Migration v1->v2 fixes dangerous Codex CLI defaults."""
        from polaris.kernelone.llm.config_store import migrate_config

        v1_config = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {
                    "type": "codex_cli",
                    "codex_exec": {"sandbox": "danger-full-access", "skip_git_repo_check": True},
                }
            },
        }

        migrated = migrate_config(v1_config, target_version=2)

        codex_exec = migrated["providers"]["codex_cli"]["codex_exec"]
        assert codex_exec["sandbox"] == "safe"
        assert codex_exec["skip_git_repo_check"] is False

    def test_migrate_no_op_when_current_version(self) -> None:
        """No migration needed when already at target version."""
        from polaris.kernelone.llm.config_store import migrate_config

        config = {"schema_version": 2, "providers": {}}

        migrated = migrate_config(config, target_version=2)
        assert migrated["schema_version"] == 2

    def test_migrate_raises_on_missing_path(self) -> None:
        """Raises error when migration path is missing."""
        from polaris.kernelone.llm.config_store import ConfigMigrationError, migrate_config

        # No migrator registered for v5 -> v6
        config = {"schema_version": 5}

        with pytest.raises(ConfigMigrationError, match="No migrator found"):
            migrate_config(config, target_version=6)

    def test_migrate_raises_on_downgrade(self) -> None:
        """Raises error when trying to downgrade."""
        from polaris.kernelone.llm.config_store import ConfigMigrationError, migrate_config

        config = {"schema_version": 3}

        with pytest.raises(ConfigMigrationError, match="lower version"):
            migrate_config(config, target_version=1)


class TestBackupMechanism:
    """Tests for configuration backup mechanism."""

    def test_backup_creates_timestamped_file(self, tmp_path: Path) -> None:
        """Backup creates file with timestamp suffix."""
        from polaris.kernelone.llm.config_store import _create_config_backup

        config_file = tmp_path / "test_config.json"
        config_file.write_text('{"test": true}', encoding="utf-8")

        backup_path = _create_config_backup(str(config_file))

        assert backup_path is not None
        assert os.path.exists(backup_path)
        assert ".backup." in backup_path

    def test_backup_copies_content_exactly(self, tmp_path: Path) -> None:
        """Backup copies file content exactly."""
        from polaris.kernelone.llm.config_store import _create_config_backup

        config_file = tmp_path / "test_config.json"
        original_content = '{"providers": {"test": {}}, "schema_version": 2}'
        config_file.write_text(original_content, encoding="utf-8")

        backup_path = _create_config_backup(str(config_file))

        assert backup_path is not None
        backup_content = Path(backup_path).read_text(encoding="utf-8")
        assert backup_content == original_content

    def test_backup_no_file_returns_none(self, tmp_path: Path) -> None:
        """Returns None when config file doesn't exist."""
        from polaris.kernelone.llm.config_store import _create_config_backup

        non_existent = tmp_path / "non_existent.json"
        result = _create_config_backup(str(non_existent))

        assert result is None

    def test_cleanup_removes_old_backups(self, tmp_path: Path) -> None:
        """Cleanup removes backups beyond max_backups limit."""
        from polaris.kernelone.llm.config_store import _cleanup_old_backups

        config_file = tmp_path / "test_config.json"
        config_file.write_text("{}", encoding="utf-8")

        # Create 7 backup files
        for i in range(7):
            backup = tmp_path / f"test_config.json.backup.{1000 + i}"
            backup.write_text(f'{{"version": {i}}}', encoding="utf-8")

        _cleanup_old_backups(str(config_file), max_backups=5)

        # Should only have 5 backups remaining
        remaining = list(tmp_path.glob("test_config.json.backup.*"))
        assert len(remaining) == 5


class TestAuditLogging:
    """Tests for configuration change audit logging."""

    def test_detect_changes_finds_modifications(self) -> None:
        """Detects modified values."""
        from polaris.kernelone.llm.config_store import _detect_config_changes

        old = {"timeout": 60, "providers": {"test": {}}}
        new = {"timeout": 120, "providers": {"test": {}}}

        changes = _detect_config_changes(old, new)

        assert len(changes) == 1
        assert changes[0]["path"] == "timeout"
        assert changes[0]["change_type"] == "value_change"
        assert changes[0]["old_value"] == 60
        assert changes[0]["new_value"] == 120

    def test_detect_changes_finds_additions(self) -> None:
        """Detects added fields."""
        from polaris.kernelone.llm.config_store import _detect_config_changes

        old = {"timeout": 60}
        new = {"timeout": 60, "temperature": 0.7}

        changes = _detect_config_changes(old, new)

        assert len(changes) == 1
        assert changes[0]["path"] == "temperature"

    def test_detect_changes_redacts_sensitive(self) -> None:
        """Redacts sensitive values in change log."""
        from polaris.kernelone.llm.config_store import _detect_config_changes

        old = {"api_key": "secret-old"}
        new = {"api_key": "secret-new"}

        changes = _detect_config_changes(old, new)

        assert len(changes) == 1
        assert changes[0]["is_sensitive"] is True
        assert changes[0]["old_value"] == "[REDACTED]"
        assert changes[0]["new_value"] == "[REDACTED]"

    def test_detect_changes_nested_paths(self) -> None:
        """Detects changes in nested structures."""
        from polaris.kernelone.llm.config_store import _detect_config_changes

        old = {"providers": {"ollama": {"timeout": 60}}}
        new = {"providers": {"ollama": {"timeout": 120}}}

        changes = _detect_config_changes(old, new)

        assert len(changes) == 1
        assert changes[0]["path"] == "providers.ollama.timeout"


class TestCodexSecurityDefaults:
    """Tests for Codex CLI security default fixes."""

    def test_default_config_has_safe_sandbox(self) -> None:
        """Default config uses safe sandbox mode."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()

        codex_exec = config["providers"]["codex_cli"]["codex_exec"]
        assert codex_exec["sandbox"] == "safe"

    def test_default_config_has_git_check_enabled(self) -> None:
        """Default config has git repo check enabled."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()

        codex_exec = config["providers"]["codex_cli"]["codex_exec"]
        assert codex_exec["skip_git_repo_check"] is False

    def test_default_schema_version_is_2(self) -> None:
        """Default config uses schema version 2."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        assert config["schema_version"] == 2


class TestSaveWithSecurityFeatures:
    """Integration tests for save_llm_config with security features."""

    def test_save_creates_backup(self, tmp_path: Path) -> None:
        """Save creates backup before writing."""
        from polaris.kernelone.llm.config_store import save_llm_config

        # Mock the config path to tmp directory
        config_path = str(tmp_path / "llm_config.json")

        def mock_config_path(workspace: str, cache_root: str) -> str:
            return config_path

        # Create initial config with valid providers and roles
        initial_config = {
            "schema_version": 2,
            "providers": {
                "test": {"type": "test", "timeout": 60},
                "ollama": {"type": "ollama", "timeout": 60},
            },
            "roles": {
                "pm": {"provider_id": "ollama", "model": "test"},
                "director": {"provider_id": "ollama", "model": "test"},
                "qa": {"provider_id": "ollama", "model": "test"},
                "architect": {"provider_id": "ollama", "model": "test"},
            },
            "policies": {},
        }

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(initial_config, f)

        # Save updated config
        updated = {
            "schema_version": 2,
            "providers": {
                "test": {"type": "test", "timeout": 120},
                "ollama": {"type": "ollama", "timeout": 60},
            },
            "roles": {
                "pm": {"provider_id": "ollama", "model": "test"},
                "director": {"provider_id": "ollama", "model": "test"},
                "qa": {"provider_id": "ollama", "model": "test"},
                "architect": {"provider_id": "ollama", "model": "test"},
            },
            "policies": {},
        }

        with patch("polaris.kernelone.llm.config_store.llm_config_path", mock_config_path):
            save_llm_config(".", ".", updated)

        # Check backup was created
        backups = list(tmp_path.glob("llm_config.json.backup.*"))
        assert len(backups) >= 1

    def test_save_validates_before_write(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Save validates config before writing."""
        from polaris.kernelone.llm.config_store import save_llm_config

        config_path = str(tmp_path / "llm_config.json")

        def mock_config_path(workspace: str, cache_root: str) -> str:
            return config_path

        monkeypatch.setattr("polaris.kernelone.llm.config_store.llm_config_path", mock_config_path)

        # Create initial valid config
        initial_config = {
            "schema_version": 2,
            "providers": {
                "test": {"type": "test", "timeout": 60},
                "ollama": {"type": "ollama", "timeout": 60},
            },
            "roles": {
                "pm": {"provider_id": "ollama", "model": "test"},
                "director": {"provider_id": "ollama", "model": "test"},
                "qa": {"provider_id": "ollama", "model": "test"},
                "architect": {"provider_id": "ollama", "model": "test"},
            },
            "policies": {},
        }

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(initial_config, f)

        # Try to save invalid config (missing 'type')
        invalid = {
            "schema_version": 2,
            "providers": {"bad": {"timeout": 60}},  # Missing 'type'
            "roles": {},
            "policies": {},
        }

        with pytest.raises(ValueError, match="Invalid LLM configuration"):
            save_llm_config(".", ".", invalid)

    def test_save_restore_masked_sensitive_values(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Save restores masked sensitive values from existing config."""
        from polaris.kernelone.llm.config_store import (
            MASKED_SECRET,
            save_llm_config,
        )

        config_path = str(tmp_path / "llm_config.json")

        def mock_config_path(workspace: str, cache_root: str) -> str:
            return config_path

        monkeypatch.setattr("polaris.kernelone.llm.config_store.llm_config_path", mock_config_path)

        # Create initial config with secret
        initial_config = {
            "schema_version": 2,
            "providers": {
                "test": {
                    "type": "test",
                    "api_key": "my-secret-key-12345",
                    "timeout": 60,
                },
                "ollama": {"type": "ollama", "timeout": 60},
            },
            "roles": {
                "pm": {"provider_id": "ollama", "model": "test"},
                "director": {"provider_id": "ollama", "model": "test"},
                "qa": {"provider_id": "ollama", "model": "test"},
                "architect": {"provider_id": "ollama", "model": "test"},
            },
            "policies": {},
        }

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(initial_config, f)

        # Save with masked value - should restore original
        masked_update = {
            "schema_version": 2,
            "providers": {
                "test": {
                    "type": "test",
                    "api_key": MASKED_SECRET,  # Should be restored
                    "timeout": 120,  # This should be updated
                },
                "ollama": {"type": "ollama", "timeout": 60},
            },
            "roles": {
                "pm": {"provider_id": "ollama", "model": "test"},
                "director": {"provider_id": "ollama", "model": "test"},
                "qa": {"provider_id": "ollama", "model": "test"},
                "architect": {"provider_id": "ollama", "model": "test"},
            },
            "policies": {},
        }

        result = save_llm_config(".", ".", masked_update)

        # Secret should be restored, timeout should be updated
        assert result["providers"]["test"]["api_key"] == "my-secret-key-12345"
        assert result["providers"]["test"]["timeout"] == 120


class TestValidateLlmConfig:
    """Tests for validate_llm_config function."""

    def test_valid_config_returns_true(self) -> None:
        """Valid config passes validation."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        config = {
            "schema_version": 2,
            "providers": {
                "ollama": {
                    "type": "ollama",
                    "timeout": 60,
                }
            },
            "roles": {
                "pm": {
                    "provider_id": "ollama",
                }
            },
            "policies": {},
        }

        is_valid, errors, _warnings = validate_llm_config(config)
        assert is_valid is True
        assert len(errors) == 0

    def test_missing_provider_type_returns_error(self) -> None:
        """Missing provider type returns error."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        config = {
            "schema_version": 2,
            "providers": {
                "bad": {
                    "timeout": 60,
                    # Missing 'type'
                }
            },
            "roles": {},
            "policies": {},
        }

        is_valid, errors, _warnings = validate_llm_config(config)
        assert is_valid is False
        # Pydantic validation error for missing required field
        assert any("Field required" in e or "missing 'type'" in e for e in errors)

    def test_dangerous_sandbox_returns_warning(self) -> None:
        """Dangerous sandbox mode returns warning."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        config = {
            "schema_version": 2,
            "providers": {
                "codex_cli": {
                    "type": "codex_cli",
                    "codex_exec": {
                        "sandbox": "danger-full-access",
                    },
                }
            },
            "roles": {},
            "policies": {},
        }

        is_valid, _errors, warnings = validate_llm_config(config)
        assert is_valid is True  # Still valid, just a warning
        assert any("dangerous sandbox" in w for w in warnings)


class TestMigrationIntegration:
    """Integration tests for migration during save."""

    def test_save_migrates_v1_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Save migrates v1 config to v2 automatically."""
        from polaris.kernelone.llm.config_store import (
            save_llm_config,
        )

        config_path = str(tmp_path / "llm_config.json")

        def mock_config_path(workspace: str, cache_root: str) -> str:
            return config_path

        monkeypatch.setattr("polaris.kernelone.llm.config_store.llm_config_path", mock_config_path)

        # Create v1 config with provider field (v1 schema)
        v1_config = {
            "schema_version": 1,
            "provider": {"type": "ollama", "base_url": "http://192.168.1.2:11434"},
            "roles": {
                "pm": {"provider_id": "ollama", "model": "test"},
                "director": {"provider_id": "ollama", "model": "test"},
                "qa": {"provider_id": "ollama", "model": "test"},
                "architect": {"provider_id": "ollama", "model": "test"},
            },
            "policies": {},
        }

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(v1_config, f)

        # Save with minimal update - include roles to pass validation
        # NOTE: After v1->v2 migration, old provider.type becomes providers["default"].
        # Role provider_ids must reference the new provider key ("default"), not the old type ("ollama").
        update = {
            "schema_version": 1,
            "provider": {"type": "ollama", "base_url": "http://192.168.1.2:11434"},
            "roles": {
                "pm": {"provider_id": "default", "model": "test"},
                "director": {"provider_id": "default", "model": "test"},
                "qa": {"provider_id": "default", "model": "test"},
                "architect": {"provider_id": "default", "model": "test"},
            },
            "policies": {},
        }

        result = save_llm_config(".", ".", update)

        # Should be migrated to v2
        assert result["schema_version"] == 2
        assert "providers" in result
        assert "default" in result["providers"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
