"""Tests for ConfigSnapshot - immutable configuration with source tracking.

This module tests the core configuration management functionality
introduced in Phase 1 of the "Thin CLI + Core OO" refactoring.
"""

import sys
from pathlib import Path

# Add src/backend to path
sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "backend"))

import pytest
from domain.models.config_snapshot import (
    ConfigSnapshot,
    SourceType,
)


class TestConfigSnapshotBasics:
    """Test basic ConfigSnapshot functionality."""

    def test_empty_snapshot(self):
        """Test creating empty snapshot."""
        snapshot = ConfigSnapshot.empty()
        assert snapshot.get("any.key") is None
        assert snapshot.get("any.key", "default") == "default"
        assert not snapshot.has("any.key")

    def test_merge_priority_default(self):
        """Test DEFAULT source has lowest priority."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"key": "default_value"}
        )
        assert snapshot.get("key") == "default_value"
        assert snapshot.get_source("key") == SourceType.DEFAULT

    def test_merge_priority_persisted_overrides_default(self):
        """Test PERSISTED overrides DEFAULT."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"key": "default"},
            persisted={"key": "persisted"}
        )
        assert snapshot.get("key") == "persisted"
        assert snapshot.get_source("key") == SourceType.PERSISTED

    def test_merge_priority_env_overrides_persisted(self):
        """Test ENV overrides PERSISTED."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"key": "default"},
            persisted={"key": "persisted"},
            env={"key": "env"}
        )
        assert snapshot.get("key") == "env"
        assert snapshot.get_source("key") == SourceType.ENV

    def test_merge_priority_cli_overrides_env(self):
        """Test CLI overrides ENV (highest priority)."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"key": "default"},
            persisted={"key": "persisted"},
            env={"key": "env"},
            cli={"key": "cli"}
        )
        assert snapshot.get("key") == "cli"
        assert snapshot.get_source("key") == SourceType.CLI

    def test_merge_full_priority_chain(self):
        """Test complete priority chain: default < persisted < env < cli."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"key": "default", "only_default": "val1"},
            persisted={"key": "persisted", "only_persisted": "val2"},
            env={"key": "env", "only_env": "val3"},
            cli={"key": "cli", "only_cli": "val4"}
        )

        # CLI wins for 'key'
        assert snapshot.get("key") == "cli"
        assert snapshot.get_source("key") == SourceType.CLI

        # Other keys preserved
        assert snapshot.get("only_default") == "val1"
        assert snapshot.get("only_persisted") == "val2"
        assert snapshot.get("only_env") == "val3"
        assert snapshot.get("only_cli") == "val4"

        # Source tracking
        assert snapshot.get_source("only_default") == SourceType.DEFAULT
        assert snapshot.get_source("only_persisted") == SourceType.PERSISTED
        assert snapshot.get_source("only_env") == SourceType.ENV
        assert snapshot.get_source("only_cli") == SourceType.CLI


class TestConfigSnapshotDotNotation:
    """Test dot notation key access."""

    def test_nested_key_access(self):
        """Test accessing nested keys with dot notation."""
        snapshot = ConfigSnapshot.merge_sources(
            default={
                "server": {
                    "host": "127.0.0.1",
                    "port": 8080
                },
                "pm": {
                    "backend": "auto",
                    "timeout": 300
                }
            }
        )

        assert snapshot.get("server.host") == "127.0.0.1"
        assert snapshot.get("server.port") == 8080
        assert snapshot.get("pm.backend") == "auto"
        assert snapshot.get("pm.timeout") == 300

    def test_missing_nested_key(self):
        """Test default value for missing nested keys."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"server": {"host": "127.0.0.1"}}
        )

        assert snapshot.get("server.missing") is None
        assert snapshot.get("server.missing", "default") == "default"

    def test_has_nested_key(self):
        """Test checking existence of nested keys."""
        snapshot = ConfigSnapshot.merge_sources(
            default={
                "server": {"host": "127.0.0.1"},
                "flat_key": "value"
            }
        )

        assert snapshot.has("server.host")
        assert snapshot.has("flat_key")
        assert not snapshot.has("server.missing")
        assert not snapshot.has("missing.nested.key")


class TestConfigSnapshotImmutability:
    """Test immutability guarantees."""

    def test_mapping_proxy_prevents_modification(self):
        """Test that MappingProxyType prevents direct modification."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"key": "value"}
        )

        with pytest.raises(TypeError):
            snapshot._config["new_key"] = "new_value"

    def test_functional_update_creates_new_instance(self):
        """Test with_override creates new instance."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"key": "original"}
        )

        new_snapshot = snapshot.with_override(
            {"key": "updated"},
            SourceType.CLI
        )

        # Original unchanged
        assert snapshot.get("key") == "original"
        assert snapshot.get_source("key") == SourceType.DEFAULT

        # New has updated value
        assert new_snapshot.get("key") == "updated"
        assert new_snapshot.get_source("key") == SourceType.CLI

    def test_with_override_preserves_original_keys(self):
        """Test override only affects specified keys."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"key1": "val1", "key2": "val2"}
        )

        new_snapshot = snapshot.with_override(
            {"key1": "new_val1"},
            SourceType.CLI
        )

        assert new_snapshot.get("key1") == "new_val1"
        assert new_snapshot.get("key2") == "val2"  # Preserved

    def test_with_defaults_only_fills_missing(self):
        """Test with_defaults only applies to missing keys."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"existing": "value"}
        )

        new_snapshot = snapshot.with_defaults(
            {"existing": "new", "missing": "filled"}
        )

        assert new_snapshot.get("existing") == "value"  # Unchanged
        assert new_snapshot.get("missing") == "filled"  # Filled in


class TestConfigSnapshotHashAndEquality:
    """Test hashing and equality."""

    def test_same_config_same_hash(self):
        """Test identical configs have same hash."""
        snapshot1 = ConfigSnapshot.merge_sources(
            default={"key": "value"}
        )
        snapshot2 = ConfigSnapshot.merge_sources(
            default={"key": "value"}
        )

        assert hash(snapshot1) == hash(snapshot2)
        assert snapshot1 == snapshot2

    def test_different_config_different_hash(self):
        """Test different configs have different hashes."""
        snapshot1 = ConfigSnapshot.merge_sources(
            default={"key": "value1"}
        )
        snapshot2 = ConfigSnapshot.merge_sources(
            default={"key": "value2"}
        )

        assert hash(snapshot1) != hash(snapshot2)
        assert snapshot1 != snapshot2

    def test_can_use_in_dict(self):
        """Test snapshots can be used as dict keys."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"key": "value"}
        )

        d = {snapshot: "test"}
        assert d[snapshot] == "test"


class TestConfigSnapshotValidation:
    """Test configuration validation."""

    def test_valid_port(self):
        """Test valid port passes validation."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"server.port": 8080}
        )

        result = snapshot.validate()
        assert result.is_valid
        assert not result.errors

    def test_invalid_port_negative(self):
        """Test negative port fails validation."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"server.port": -1}
        )

        result = snapshot.validate()
        assert not result.is_valid
        assert any("port" in e.lower() for e in result.errors)

    def test_invalid_port_too_large(self):
        """Test port > 65535 fails validation."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"server.port": 70000}
        )

        result = snapshot.validate()
        assert not result.is_valid

    def test_warning_on_unusual_log_level(self):
        """Test unusual log level generates warning."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"logging.level": "strange"}
        )

        result = snapshot.validate()
        assert any("log level" in w.lower() for w in result.warnings)


class TestConfigSnapshotFromFlatDict:
    """Test from_flat_dict factory method."""

    def test_from_flat_dict(self):
        """Test creating snapshot from flat dict."""
        snapshot = ConfigSnapshot.from_flat_dict({
            "server.host": "127.0.0.1",
            "server.port": 8080,
            "pm.backend": "auto"
        })

        assert snapshot.get("server.host") == "127.0.0.1"
        assert snapshot.get("server.port") == 8080
        assert snapshot.get("pm.backend") == "auto"


class TestConfigSnapshotJSON:
    """Test JSON serialization."""

    def test_to_json(self):
        """Test JSON serialization."""
        snapshot = ConfigSnapshot.merge_sources(
            default={
                "server": {"host": "127.0.0.1", "port": 8080},
                "pm": {"backend": "auto"}
            }
        )

        json_str = snapshot.to_json()
        assert "127.0.0.1" in json_str
        assert "8080" in json_str
        assert "auto" in json_str


class TestConfigSnapshotTypedAccess:
    """Test typed configuration access."""

    def test_get_typed_int(self):
        """Test getting integer values."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"port": "8080"}
        )

        assert snapshot.get_typed("port", int) == 8080

    def test_get_typed_bool_from_string(self):
        """Test getting boolean from string."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"enabled": "true", "disabled": "false"}
        )

        assert snapshot.get_typed("enabled", bool) is True
        assert snapshot.get_typed("disabled", bool) is False

    def test_get_typed_returns_default_on_error(self):
        """Test default returned on type conversion error."""
        snapshot = ConfigSnapshot.merge_sources(
            default={"port": "not_a_number"}
        )

        assert snapshot.get_typed("port", int, 8080) == 8080


class TestSourceType:
    """Test SourceType enum."""

    def test_source_type_order(self):
        """Test source types are ordered correctly."""
        assert SourceType.DEFAULT < SourceType.PERSISTED
        assert SourceType.PERSISTED < SourceType.ENV
        assert SourceType.ENV < SourceType.CLI

    def test_source_type_str(self):
        """Test string representation."""
        assert str(SourceType.DEFAULT) == "default"
        assert str(SourceType.CLI) == "cli"


if __name__ == "__main__":
    # Run basic tests without pytest
    print("Running ConfigSnapshot tests...")

    test = TestConfigSnapshotBasics()
    test.test_empty_snapshot()
    test.test_merge_priority_default()
    test.test_merge_priority_persisted_overrides_default()
    test.test_merge_priority_env_overrides_persisted()
    test.test_merge_priority_cli_overrides_env()
    test.test_merge_full_priority_chain()
    print("  ✓ Basic tests passed")

    test = TestConfigSnapshotDotNotation()
    test.test_nested_key_access()
    test.test_missing_nested_key()
    test.test_has_nested_key()
    print("  ✓ Dot notation tests passed")

    test = TestConfigSnapshotImmutability()
    test.test_mapping_proxy_prevents_modification()
    test.test_functional_update_creates_new_instance()
    test.test_with_override_preserves_original_keys()
    test.test_with_defaults_only_fills_missing()
    print("  ✓ Immutability tests passed")

    test = TestConfigSnapshotHashAndEquality()
    test.test_same_config_same_hash()
    test.test_different_config_different_hash()
    test.test_can_use_in_dict()
    print("  ✓ Hash/equality tests passed")

    test = TestConfigSnapshotValidation()
    test.test_valid_port()
    test.test_invalid_port_negative()
    test.test_invalid_port_too_large()
    test.test_warning_on_unusual_log_level()
    print("  ✓ Validation tests passed")

    print("\n✅ All tests passed!")
