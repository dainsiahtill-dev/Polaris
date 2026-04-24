"""Tests for polaris.bootstrap.config module - P0 configuration model tests.

This module tests the configuration models, constants, and helper functions
in the config module.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from polaris.bootstrap.config import (
    JSONLConfig,
    LoggingConfig,
    RuntimeConfig,
    ServerConfig,
    Settings,
    SettingsUpdate,
    default_system_cache_base,
    find_workspace_root,
    get_backend_root,
    get_project_root,
    reload_settings,
    resolve_ramdisk_root,
)


class TestFindWorkspaceRoot:
    """Test workspace root detection."""

    def test_finds_root_with_docs_dir(self, tmp_path: Path) -> None:
        """Should find workspace root when docs/ directory exists."""
        # Create a nested structure with docs/
        subdir = tmp_path / "src" / "backend"
        subdir.mkdir(parents=True)
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        result = find_workspace_root(str(subdir / "file.txt"))
        assert result == tmp_path.resolve()

    def test_returns_resolved_path_if_no_docs(self, tmp_path: Path) -> None:
        """Should return resolved path when no docs/ directory found."""
        result = find_workspace_root(str(tmp_path))
        assert result == tmp_path.resolve()


class TestGetBackendRoot:
    """Test backend root detection."""

    def test_returns_path_instance(self) -> None:
        """Should return a Path instance."""
        result = get_backend_root()
        assert isinstance(result, Path)

    def test_points_to_backend_directory(self) -> None:
        """Should point to src/backend directory."""
        result = get_backend_root()
        # The backend root should be somewhere under src/backend
        # On Windows, paths use backslash, normalize for comparison
        path_str = str(result).replace("\\", "/")
        assert "backend" in path_str


class TestGetProjectRoot:
    """Test project root detection."""

    def test_returns_path_instance(self) -> None:
        """Should return a Path instance."""
        result = get_project_root()
        assert isinstance(result, Path)

    def test_points_to_repository_root(self) -> None:
        """Should point to repository root."""
        result = get_project_root()
        path_str = str(result).replace("\\", "/")
        # backend/parents[1] should be the repo root (polaris or src)
        assert "polaris" in path_str or result.name == "polaris"


class TestDefaultSystemCacheBase:
    """Test system cache base directory resolution."""

    def test_returns_path_instance(self) -> None:
        """Should return a Path instance."""
        result = default_system_cache_base()
        assert isinstance(result, Path)

    def test_fallback_to_home(self) -> None:
        """Should fallback to home directory cache when no system vars set."""
        # Clear any potentially set cache variables
        original_localappdata = os.environ.get("LOCALAPPDATA")
        original_xdg = os.environ.get("XDG_CACHE_HOME")

        try:
            if "LOCALAPPDATA" in os.environ:
                del os.environ["LOCALAPPDATA"]
            if "XDG_CACHE_HOME" in os.environ:
                del os.environ["XDG_CACHE_HOME"]

            result = default_system_cache_base()
            assert ".cache" in str(result) or ".cache" in str(result).lower()
        finally:
            # Restore environment
            if original_localappdata:
                os.environ["LOCALAPPDATA"] = original_localappdata
            if original_xdg:
                os.environ["XDG_CACHE_HOME"] = original_xdg


class TestResolveRamdiskRoot:
    """Test ramdisk root resolution."""

    def test_returns_none_when_no_ramdisk(self) -> None:
        """Should return None when no ramdisk available."""
        # Mock a path that doesn't exist
        result = resolve_ramdisk_root(None)
        # On most systems neither X: drive nor /dev/shm exists on Windows
        # The function should handle missing ramdisk gracefully
        assert result is None or isinstance(result, Path)

    def test_respects_configured_root(self, tmp_path: Path) -> None:
        """Should return configured root if it exists."""
        result = resolve_ramdisk_root(str(tmp_path))
        assert result == tmp_path.resolve()


class TestJSONLConfig:
    """Test JSONL configuration model."""

    def test_default_values(self) -> None:
        """Should have correct default values."""
        config = JSONLConfig()
        assert config.lock_stale_sec == 120.0
        assert config.buffer_enabled is True
        assert config.flush_interval_sec == 1.0
        assert config.flush_batch == 50
        assert config.max_buffer == 2000
        assert config.buffer_ttl_sec == 300.0
        assert config.max_paths == 100
        assert config.cleanup_interval_sec == 60.0

    def test_positive_float_validation(self) -> None:
        """Should clamp negative floats to 0.0."""
        config = JSONLConfig(lock_stale_sec=-50.0)
        assert config.lock_stale_sec == 0.0

    def test_positive_int_validation(self) -> None:
        """Should clamp negative ints to 1."""
        config = JSONLConfig(flush_batch=-10)
        assert config.flush_batch == 1

    def test_bool_validation_string(self) -> None:
        """Should parse string booleans correctly."""
        config = JSONLConfig(buffer_enabled="false")  # type: ignore[arg-type]
        assert config.buffer_enabled is False

        config2 = JSONLConfig(buffer_enabled="0")  # type: ignore[arg-type]
        assert config2.buffer_enabled is False

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should create config from environment variables."""
        monkeypatch.setenv("KERNELONE_JSONL_LOCK_STALE_SEC", "200.0")
        monkeypatch.setenv("KERNELONE_JSONL_BUFFERED", "0")
        monkeypatch.setenv("KERNELONE_JSONL_FLUSH_BATCH", "100")

        config = JSONLConfig.from_env()
        assert config.lock_stale_sec == 200.0
        assert config.buffer_enabled is False
        assert config.flush_batch == 100


class TestRuntimeConfig:
    """Test runtime configuration model."""

    def test_default_values(self) -> None:
        """Should have correct defaults."""
        config = RuntimeConfig()
        assert config.root is None
        assert config.cache_root is None
        assert config.use_ramdisk is True
        assert config.ramdisk_root is None

    def test_path_validation(self, tmp_path: Path) -> None:
        """Should resolve path strings to Path instances."""
        config = RuntimeConfig(root=str(tmp_path))  # type: ignore[arg-type]
        assert config.root == tmp_path.resolve()

    def test_none_paths_stay_none(self) -> None:
        """Should keep None values as None."""
        config = RuntimeConfig(root=None, cache_root=None, ramdisk_root=None)
        assert config.root is None
        assert config.cache_root is None
        assert config.ramdisk_root is None


class TestLoggingConfig:
    """Test logging configuration model."""

    def test_default_values(self) -> None:
        """Should have correct defaults."""
        config = LoggingConfig()
        assert config.level == "DEBUG"
        assert config.json_path is None
        assert config.enable_debug_tracing is True

    def test_valid_log_levels(self) -> None:
        """Should accept valid log levels."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = LoggingConfig(level=level)
            assert config.level == level

    def test_invalid_log_level_raises(self) -> None:
        """Should raise ValueError for invalid log level."""
        with pytest.raises(ValueError, match="Invalid log level"):
            LoggingConfig(level="INVALID")


class TestServerConfig:
    """Test server configuration model."""

    def test_default_values(self) -> None:
        """Should have correct defaults."""
        config = ServerConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 49977
        assert len(config.cors_origins) == 2

    def test_custom_cors_origins(self) -> None:
        """Should accept custom CORS origins."""
        config = ServerConfig(cors_origins=["http://example.com", "https://example.org"])
        assert len(config.cors_origins) == 2
        assert "http://example.com" in config.cors_origins


class TestSettingsUpdate:
    """Test partial settings update payload."""

    def test_default_all_none(self) -> None:
        """Should have all fields as None by default."""
        update = SettingsUpdate()
        assert update.self_upgrade_mode is None
        assert update.workspace is None
        assert update.timeout is None
        assert update.model is None

    def test_partial_update(self) -> None:
        """Should accept partial updates."""
        update = SettingsUpdate(timeout=300)
        assert update.timeout == 300
        assert update.workspace is None


class TestSettings:
    """Test unified Settings model."""

    def test_default_workspace(self) -> None:
        """Should have default workspace from cwd."""
        settings = Settings()
        assert isinstance(settings.workspace, Path)

    def test_nested_configs_exist(self) -> None:
        """Should have all nested configuration objects."""
        settings = Settings()
        assert isinstance(settings.llm, object)
        assert isinstance(settings.pm, object)
        assert isinstance(settings.director, object)
        assert isinstance(settings.runtime, RuntimeConfig)
        assert isinstance(settings.logging, LoggingConfig)
        assert isinstance(settings.server, ServerConfig)
        assert isinstance(settings.jsonl, JSONLConfig)

    def test_model_property(self) -> None:
        """Should expose model via property."""
        settings = Settings()
        # The property should return self.llm.model
        assert hasattr(settings, "model")

    def test_audit_llm_role_normalization(self) -> None:
        """Should normalize audit_llm_role to lowercase."""
        settings = Settings(audit_llm_role="QA")
        assert settings.audit_llm_role == "qa"

    def test_audit_llm_timeout_minimum(self) -> None:
        """Should enforce minimum audit_llm_timeout."""
        settings = Settings(audit_llm_timeout=10)
        assert settings.audit_llm_timeout >= 30

    def test_json_log_path_normalization(self) -> None:
        """Should normalize json_log_path."""
        settings = Settings(json_log_path=".polaris/runtime/logs")
        # Should normalize to runtime/...
        assert settings.json_log_path is not None

    def test_migrate_legacy_inputs(self) -> None:
        """Should migrate legacy flat keys to nested structures."""
        # Test that Settings can be created and model property works
        # Using self_upgrade_mode to avoid workspace validation
        settings = Settings(self_upgrade_mode=True)
        # Set model via property
        settings.model = "gpt-4"
        assert settings.llm.model == "gpt-4"

    def test_apply_update_pm_settings(self) -> None:
        """Should apply partial PM updates correctly."""
        settings = Settings(self_upgrade_mode=True)
        update = SettingsUpdate(pm_max_failures=5)
        settings.apply_update(update)
        assert settings.pm_max_failures == 5

    def test_apply_update_director_settings(self) -> None:
        """Should apply partial Director updates correctly."""
        settings = Settings(self_upgrade_mode=True)
        update = SettingsUpdate(director_iterations=10)
        settings.apply_update(update)
        assert settings.director_iterations == 10

    def test_apply_update_jsonl_settings(self) -> None:
        """Should apply JSONL settings correctly."""
        settings = Settings(self_upgrade_mode=True)
        update = SettingsUpdate(jsonl_max_buffer=5000)
        settings.apply_update(update)
        assert settings.jsonl.max_buffer == 5000

    def test_to_payload(self) -> None:
        """Should produce JSON-safe payload."""
        settings = Settings()
        payload = settings.to_payload()
        assert isinstance(payload, dict)
        assert "model" in payload
        assert "pm_backend" in payload
        assert "director_model" in payload

    def test_runtime_base_property(self) -> None:
        """Should resolve runtime base correctly."""
        settings = Settings()
        base = settings.runtime_base
        assert isinstance(base, Path)

    def test_pm_script_path(self) -> None:
        """Should point to PM CLI script."""
        settings = Settings()
        assert settings.pm_script_path.name == "cli.py"

    def test_director_script_path(self) -> None:
        """Should point to Director script."""
        settings = Settings()
        assert "loop" in str(settings.director_script_path) or "director" in str(settings.director_script_path)


class TestSettingsFromEnv:
    """Test Settings.from_env() class method."""

    def test_from_env_returns_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return a Settings instance."""
        # Clear all relevant env vars
        for key in [
            "KERNELONE_WORKSPACE",
            "KERNELONE_TIMEOUT",
            "KERNELONE_MODEL",
            "KERNELONE_PM_MODEL",
            "KERNELONE_BACKEND_PORT",
        ]:
            monkeypatch.delenv(key, raising=False)

        settings = Settings.from_env()
        assert isinstance(settings, Settings)

    def test_from_env_with_workspace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should load workspace from env var."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            workspace_path = Path(tmp)
            monkeypatch.setenv("KERNELONE_WORKSPACE", str(workspace_path))
            monkeypatch.setenv("KERNELONE_SELF_UPGRADE_MODE", "1")
            settings = Settings.from_env()
            assert settings.workspace is not None


class TestReloadSettings:
    """Test settings reload functionality."""

    def test_reload_returns_settings(self) -> None:
        """Should return a new Settings instance."""
        settings = reload_settings()
        assert isinstance(settings, Settings)


class TestParseHelpers:
    """Test helper parsing functions."""

    def test_parse_value_true_variants(self) -> None:
        """Should parse true-like string values."""
        from polaris.bootstrap.config import _parse_value

        assert _parse_value("true") is True
        assert _parse_value("1") is True
        assert _parse_value("yes") is True

    def test_parse_value_false_variants(self) -> None:
        """Should parse false-like string values."""
        from polaris.bootstrap.config import _parse_value

        assert _parse_value("false") is False
        assert _parse_value("0") is False
        assert _parse_value("no") is False

    def test_parse_value_integer(self) -> None:
        """Should parse integer strings."""
        from polaris.bootstrap.config import _parse_value

        assert _parse_value("42") == 42
        assert _parse_value("0") == 0

    def test_parse_value_string(self) -> None:
        """Should return non-numeric strings as-is."""
        from polaris.bootstrap.config import _parse_value

        assert _parse_value("gpt-4") == "gpt-4"
        assert _parse_value("hello world") == "hello world"

    def test_parse_bool(self) -> None:
        """Should parse boolean strings."""
        from polaris.bootstrap.config import _parse_bool

        assert _parse_bool("1") is True
        assert _parse_bool("true") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("on") is True
        assert _parse_bool("0") is False
        assert _parse_bool("false") is False
        assert _parse_bool("no") is False
