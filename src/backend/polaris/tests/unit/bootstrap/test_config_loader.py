"""Tests for polaris.bootstrap.config_loader module.

This module tests the ConfigLoader class and its configuration loading,
merging, and validation logic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.bootstrap.config_loader import (
    ConfigLoader,
    ConfigLoadError,
    load_config,
)


class TestConfigLoaderInit:
    """Test ConfigLoader initialization."""

    def test_default_initialization(self) -> None:
        """Should initialize with built-in defaults."""
        loader = ConfigLoader()
        assert loader._defaults is not None
        assert "server.port" in loader._defaults
        assert "pm.backend" in loader._defaults

    def test_custom_defaults_override(self) -> None:
        """Should allow custom defaults to override built-ins."""
        custom_defaults = {"server.port": 8888}
        loader = ConfigLoader(defaults=custom_defaults)
        assert loader._defaults["server.port"] == 8888


class TestConfigLoaderDefaults:
    """Test default configuration values."""

    def test_server_defaults(self) -> None:
        """Should have correct server defaults."""
        loader = ConfigLoader()
        assert loader.get_default("server.host") == "127.0.0.1"
        assert loader.get_default("server.port") == 49977

    def test_pm_defaults(self) -> None:
        """Should have correct PM defaults."""
        loader = ConfigLoader()
        assert loader.get_default("pm.backend") == "auto"
        assert loader.get_default("pm.show_output") is True
        assert loader.get_default("pm.runs_director") is True

    def test_director_defaults(self) -> None:
        """Should have correct Director defaults."""
        loader = ConfigLoader()
        assert loader.get_default("director.iterations") == 1
        assert loader.get_default("director.execution_mode") == "parallel"

    def test_llm_defaults(self) -> None:
        """Should have correct LLM defaults."""
        loader = ConfigLoader()
        assert loader.get_default("llm.provider") == "ollama"

    def test_get_all_defaults(self) -> None:
        """Should return all defaults as dict."""
        loader = ConfigLoader()
        defaults = loader.get_all_defaults()
        assert isinstance(defaults, dict)
        assert len(defaults) > 10


class TestConfigLoaderLoad:
    """Test configuration loading."""

    def test_load_with_no_args(self) -> None:
        """Should load with defaults when no args provided."""
        loader = ConfigLoader()
        snapshot = loader.load()
        assert snapshot is not None
        assert snapshot.get("server.port") == 49977

    def test_load_with_workspace_none(self) -> None:
        """Should handle None workspace gracefully."""
        loader = ConfigLoader()
        snapshot = loader.load(workspace=None)
        assert snapshot.get("server.port") == 49977

    def test_load_with_cli_overrides(self) -> None:
        """Should apply CLI overrides."""
        loader = ConfigLoader()
        snapshot = loader.load(cli_overrides={"server.port": 8888, "pm.backend": "openai"})
        assert snapshot.get("server.port") == 8888
        assert snapshot.get("pm.backend") == "openai"

    def test_load_with_workspace_and_persisted_config(self, tmp_path: Path) -> None:
        """Should load persisted config from workspace."""
        # Create a mock metadata directory with config
        metadata_dir = tmp_path / ".polaris"
        metadata_dir.mkdir()
        config_file = metadata_dir / "config.json"
        config_file.write_text(
            json.dumps({"server": {"port": 8080}}),
            encoding="utf-8",
        )

        loader = ConfigLoader()
        snapshot = loader.load(workspace=tmp_path)
        assert snapshot.get("server.port") == 8080

    def test_load_with_legacy_config(self, tmp_path: Path) -> None:
        """Should load legacy .polaris.json config."""
        config_file = tmp_path / ".polaris.json"
        config_file.write_text(
            json.dumps({"server": {"port": 9000}}),
            encoding="utf-8",
        )

        loader = ConfigLoader()
        snapshot = loader.load(workspace=tmp_path)
        assert snapshot.get("server.port") == 9000

    def test_load_with_corrupted_json(self, tmp_path: Path) -> None:
        """Should handle corrupted JSON gracefully."""
        metadata_dir = tmp_path / ".polaris"
        metadata_dir.mkdir()
        config_file = metadata_dir / "config.json"
        config_file.write_text("{ invalid json }", encoding="utf-8")

        loader = ConfigLoader()
        # Should not raise, should fall back to defaults
        snapshot = loader.load(workspace=tmp_path)
        assert snapshot.get("server.port") == 49977  # default


class TestConfigLoaderLoadEnv:
    """Test environment variable loading."""

    def test_load_env_with_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should load environment variables with prefix."""
        monkeypatch.setenv("KERNELONE_BACKEND_PORT", "8888")
        monkeypatch.setenv("KERNELONE_PM_BACKEND", "openai")

        loader = ConfigLoader()
        env_config = loader._load_env("KERNELONE_")

        assert env_config.get("server.port") == 8888
        assert env_config.get("pm.backend") == "openai"

    def test_load_env_cors_origins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should parse CORS origins from comma-separated env var."""
        monkeypatch.setenv(
            "KERNELONE_CORS_ORIGINS",
            "http://example.com, https://test.com",
        )

        loader = ConfigLoader()
        env_config = loader._load_env("KERNELONE_")

        assert "server.cors_origins" in env_config
        assert "http://example.com" in env_config["server.cors_origins"]

    def test_load_env_ramdisk_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should load ramdisk root from env var."""
        monkeypatch.setenv("KERNELONE_RAMDISK_ROOT", "/mnt/ramdisk")

        loader = ConfigLoader()
        env_config = loader._load_env("KERNELONE_")

        assert env_config.get("runtime.ramdisk_root") == "/mnt/ramdisk"


class TestConfigLoaderFlattenDict:
    """Test nested dictionary flattening."""

    def test_flatten_simple(self) -> None:
        """Should flatten simple nested dict."""
        loader = ConfigLoader()
        result = loader._flatten_dict({"server": {"port": 8080}})
        assert result.get("server.port") == 8080

    def test_flatten_deep(self) -> None:
        """Should flatten deeply nested dict."""
        loader = ConfigLoader()
        result = loader._flatten_dict(
            {"a": {"b": {"c": {"d": 42}}}},
            parent_key="root",
        )
        assert result.get("root.a.b.c.d") == 42

    def test_flatten_mixed(self) -> None:
        """Should handle mixed nested and flat values."""
        loader = ConfigLoader()
        result = loader._flatten_dict({"server": {"port": 8080}, "host": "localhost"})
        assert result.get("server.port") == 8080
        assert result.get("host") == "localhost"


class TestConfigLoaderSettingsToFlatDict:
    """Test Settings object to flat dict conversion."""

    def test_settings_to_flat_dict(self) -> None:
        """Should convert Settings object to flat dict."""
        from polaris.bootstrap.config import Settings

        settings = Settings()
        loader = ConfigLoader()

        flat = loader._settings_to_flat_dict(settings)
        assert isinstance(flat, dict)


class TestLoadConfig:
    """Test load_config convenience function."""

    def test_load_config_returns_snapshot(self) -> None:
        """Should return ConfigSnapshot."""
        result = load_config()
        assert result is not None

    def test_load_config_with_overrides(self) -> None:
        """Should apply CLI overrides."""
        loader = ConfigLoader()
        result = loader.load(cli_overrides={"server.port": 9999})
        assert result.get("server.port") == 9999


class TestConfigLoadError:
    """Test ConfigLoadError exception."""

    def test_error_with_message(self) -> None:
        """Should store message."""
        error = ConfigLoadError("Test error")
        assert error.message == "Test error"
        assert str(error) == "Test error"

    def test_error_with_source(self) -> None:
        """Should store source information."""
        error = ConfigLoadError("Test error", source="test_source")
        assert error.source == "test_source"

    def test_error_with_details(self) -> None:
        """Should store details dictionary."""
        details = {"key": "value", "count": 42}
        error = ConfigLoadError("Test error", details=details)
        assert error.details == details
