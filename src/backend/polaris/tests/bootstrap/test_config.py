"""Tests for polaris.bootstrap.config module."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.bootstrap.config import (
    DEFAULT_BACKEND_PORT,
    JSONLConfig,
    LoggingConfig,
    RuntimeConfig,
    ServerConfig,
    Settings,
    SettingsUpdate,
    _parse_bool,
    _parse_value,
    default_system_cache_base,
    find_workspace_root,
    get_settings,
    reload_settings,
    resolve_ramdisk_root,
)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestFindWorkspaceRoot:
    def test_finds_docs_parent(self):
        with tempfile.TemporaryDirectory() as td:
            docs_dir = Path(td) / "docs"
            docs_dir.mkdir()
            sub_dir = Path(td) / "sub" / "deep"
            sub_dir.mkdir(parents=True)
            result = find_workspace_root(sub_dir)
            assert result == Path(td).resolve()

    def test_fallback_to_start(self):
        with tempfile.TemporaryDirectory() as td:
            sub_dir = Path(td) / "sub"
            sub_dir.mkdir()
            result = find_workspace_root(sub_dir)
            assert result == sub_dir.resolve()


class TestDefaultSystemCacheBase:
    def test_returns_path(self):
        result = default_system_cache_base()
        assert isinstance(result, Path)
        assert "polaris" in str(result).lower() or "cache" in str(result).lower()


class TestResolveRamdiskRoot:
    def test_configured_root_exists(self):
        with tempfile.TemporaryDirectory() as td:
            result = resolve_ramdisk_root(td)
            assert result == Path(td).resolve()

    def test_configured_root_missing(self):
        result = resolve_ramdisk_root("/nonexistent/path/12345")
        assert result is None

    def test_none_input(self):
        result = resolve_ramdisk_root(None)
        # On Windows, may return X:/ if it exists; on Linux, /dev/shm if exists
        assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# JSONLConfig
# ---------------------------------------------------------------------------


class TestJSONLConfig:
    def test_defaults(self):
        cfg = JSONLConfig()
        assert cfg.lock_stale_sec == 120.0
        assert cfg.buffer_enabled is True
        assert cfg.flush_interval_sec == 1.0
        assert cfg.flush_batch == 50
        assert cfg.max_buffer == 2000
        assert cfg.buffer_ttl_sec == 300.0
        assert cfg.max_paths == 100
        assert cfg.cleanup_interval_sec == 60.0

    def test_positive_float_validation(self):
        cfg = JSONLConfig(lock_stale_sec=-5.0, flush_interval_sec="abc")
        assert cfg.lock_stale_sec == 0.0
        assert cfg.flush_interval_sec == 0.0

    def test_positive_int_validation(self):
        cfg = JSONLConfig(flush_batch=0, max_buffer=-10)
        assert cfg.flush_batch == 1
        assert cfg.max_buffer == 1

    def test_bool_validation(self):
        cfg = JSONLConfig(buffer_enabled="false")
        assert cfg.buffer_enabled is False
        cfg2 = JSONLConfig(buffer_enabled="1")
        assert cfg2.buffer_enabled is True
        cfg3 = JSONLConfig(buffer_enabled=0)
        assert cfg3.buffer_enabled is False

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_JSONL_LOCK_STALE_SEC", "60.0")
        monkeypatch.setenv("KERNELONE_JSONL_BUFFERED", "0")
        cfg = JSONLConfig.from_env()
        assert cfg.lock_stale_sec == 60.0
        assert cfg.buffer_enabled is False


# ---------------------------------------------------------------------------
# LoggingConfig
# ---------------------------------------------------------------------------


class TestLoggingConfig:
    def test_valid_level(self):
        cfg = LoggingConfig(level="debug")
        assert cfg.level == "DEBUG"

    def test_invalid_level_raises(self):
        with pytest.raises(ValueError) as exc_info:
            LoggingConfig(level="INVALID")
        assert "Invalid log level" in str(exc_info.value)


# ---------------------------------------------------------------------------
# RuntimeConfig / ServerConfig
# ---------------------------------------------------------------------------


class TestRuntimeConfig:
    def test_path_validation(self):
        cfg = RuntimeConfig(root="/tmp/test")
        assert isinstance(cfg.root, Path)

    def test_none_paths(self):
        cfg = RuntimeConfig()
        assert cfg.root is None
        assert cfg.cache_root is None


class TestServerConfig:
    def test_defaults(self):
        cfg = ServerConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == DEFAULT_BACKEND_PORT
        assert len(cfg.cors_origins) == 2


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettingsDefaults:
    def test_default_workspace(self):
        s = Settings(workspace="/tmp/test_workspace")
        assert isinstance(s.workspace, Path)

    def test_nested_configs_present(self):
        s = Settings(workspace="/tmp/test_workspace")
        assert s.llm is not None
        assert s.pm is not None
        assert s.director is not None
        assert s.runtime is not None
        assert s.logging is not None
        assert s.server is not None
        assert s.jsonl is not None
        assert s.nats is not None

    def test_default_flags(self):
        s = Settings(workspace="/tmp/test_workspace")
        assert s.self_upgrade_mode is False
        assert s.slm_enabled is False
        assert s.qa_enabled is True
        assert s.audit_llm_enabled is True


class TestSettingsLegacyMigration:
    def test_flat_model_key(self):
        s = Settings(workspace="/tmp/test_ws", model="gpt-4")
        assert s.model == "gpt-4"
        assert s.llm.model == "gpt-4"

    def test_pm_flat_keys(self):
        s = Settings(workspace="/tmp/test_ws", pm_backend="codex", pm_model="gpt-4-pm")
        assert s.pm_backend == "codex"
        assert s.pm_model == "gpt-4-pm"

    def test_director_flat_keys(self):
        s = Settings(workspace="/tmp/test_ws", director_model="gpt-4-dir", director_iterations=5)
        assert s.director_model == "gpt-4-dir"
        assert s.director.iterations == 5

    def test_ramdisk_root_migration(self):
        s = Settings(workspace="/tmp/test_ws", ramdisk_root="/tmp/ramdisk")
        assert s.ramdisk_root == str(Path("/tmp/ramdisk").resolve())
        assert s.runtime.ramdisk_root == Path("/tmp/ramdisk").resolve()

    def test_debug_tracing_migration(self):
        s = Settings(workspace="/tmp/test_ws", debug_tracing=False)
        assert s.debug_tracing is False
        assert s.logging.enable_debug_tracing is False


class TestSettingsProperties:
    def test_model_property(self):
        s = Settings(workspace="/tmp/test_ws")
        s.model = "gpt-4"
        assert s.llm.model == "gpt-4"

    def test_pm_backend_property(self):
        s = Settings(workspace="/tmp/test_ws")
        s.pm_backend = "codex"
        assert s.pm.backend == "codex"

    def test_director_properties(self):
        s = Settings(workspace="/tmp/test_ws")
        s.director_execution_mode = "serial"
        assert s.director.execution_mode == "serial"
        s.director_max_parallel_tasks = 0
        assert s.director.max_parallel_tasks == 1
        s.director_ready_timeout_seconds = -1
        assert s.director.ready_timeout_seconds == 1

    def test_blocked_strategy_property(self):
        s = Settings(workspace="/tmp/test_ws")
        s.pm_blocked_strategy = "SKIP"
        assert s.pm.blocked_strategy == "skip"

    def test_json_log_path_normalization(self):
        s = Settings(workspace="/tmp/test_ws", json_log_path=".polaris/runtime/logs.json")
        assert s.json_log_path == "runtime/logs.json"

    def test_audit_llm_role_normalization(self):
        s = Settings(workspace="/tmp/test_ws", audit_llm_role="  QA  ")
        assert s.audit_llm_role == "qa"

    def test_audit_llm_timeout_normalization(self):
        s = Settings(workspace="/tmp/test_ws", audit_llm_timeout=10)
        assert s.audit_llm_timeout == 30
        s2 = Settings(workspace="/tmp/test_ws", audit_llm_timeout="invalid")
        assert s2.audit_llm_timeout == 180


class TestSettingsApplyUpdate:
    def test_apply_pm_fields(self):
        s = Settings(workspace="/tmp/test_ws")
        u = SettingsUpdate(pm_backend="codex", pm_model="gpt-4")
        s.apply_update(u)
        assert s.pm_backend == "codex"
        assert s.pm_model == "gpt-4"

    def test_apply_director_fields(self):
        s = Settings(workspace="/tmp/test_ws")
        u = SettingsUpdate(director_iterations=5, director_show_output=False)
        s.apply_update(u)
        assert s.director_iterations == 5
        assert s.director_show_output is False

    def test_apply_jsonl_fields(self):
        s = Settings(workspace="/tmp/test_ws")
        u = SettingsUpdate(jsonl_lock_stale_sec=30.0, jsonl_buffer_enabled=False, jsonl_flush_batch=10)
        s.apply_update(u)
        assert s.jsonl.lock_stale_sec == 30.0
        assert s.jsonl.buffer_enabled is False
        assert s.jsonl.flush_batch == 10

    def test_apply_nats_fields(self):
        s = Settings(workspace="/tmp/test_ws")
        u = SettingsUpdate(nats_enabled=False, nats_url="nats://other:4222")
        s.apply_update(u)
        assert s.nats.enabled is False
        assert s.nats.url == "nats://other:4222"

    def test_apply_flag_fields(self):
        s = Settings(workspace="/tmp/test_ws")
        u = SettingsUpdate(slm_enabled=True, qa_enabled=False)
        s.apply_update(u)
        assert s.slm_enabled is True
        assert s.qa_enabled is False

    def test_apply_audit_fields(self):
        s = Settings(workspace="/tmp/test_ws")
        u = SettingsUpdate(audit_llm_role="reviewer", audit_llm_timeout=60)
        s.apply_update(u)
        assert s.audit_llm_role == "reviewer"
        assert s.audit_llm_timeout == 60


class TestSettingsPayload:
    def test_to_payload_has_compatibility_keys(self):
        s = Settings(workspace="/tmp/test_ws")
        payload = s.to_payload()
        assert "model" in payload
        assert "pm_backend" in payload
        assert "pm_model" in payload
        assert "director_model" in payload
        assert "debug_tracing" in payload
        assert "ramdisk_root" in payload


class TestSettingsRuntimeBase:
    def test_runtime_base_with_ramdisk(self):
        with tempfile.TemporaryDirectory() as td:
            s = Settings(workspace="/tmp/test_ws", ramdisk_root=td)
            base = s.runtime_base
            assert base == Path(td).resolve()

    def test_runtime_base_without_ramdisk(self):
        s = Settings(workspace="/tmp/test_ws", ramdisk_root="")
        base = s.runtime_base
        assert base is not None


class TestSettingsPathProperties:
    def test_script_paths(self):
        s = Settings(workspace="/tmp/test_ws")
        assert s.pm_script_path.name == "cli.py"
        assert s.director_script_path.name == "loop-director.py"
        assert s.loop_module_dir.name == "polaris_loop"


# ---------------------------------------------------------------------------
# Settings.from_env
# ---------------------------------------------------------------------------


class TestSettingsFromEnv:
    def test_from_env_loads_workspace(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_WORKSPACE", "/tmp/test_ws_env")
        monkeypatch.setenv("KERNELONE_MODEL", "gpt-4")
        with patch(
            "polaris.cells.policy.workspace_guard.public.service.ensure_workspace_target_allowed"
        ) as mock_ensure:
            mock_ensure.return_value = Path("/tmp/test_ws_env")
            s = Settings.from_env()
        assert Path(s.workspace).name == Path("/tmp/test_ws_env").name
        assert s.model == "gpt-4"

    def test_from_env_loads_flags(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_SLM_ENABLED", "1")
        monkeypatch.setenv("KERNELONE_QA_ENABLED", "0")
        s = Settings.from_env()
        assert s.slm_enabled is True
        assert s.qa_enabled is False


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


class TestParseValue:
    def test_true_values(self):
        assert _parse_value("true") is True
        assert _parse_value("1") is True
        assert _parse_value("yes") is True

    def test_false_values(self):
        assert _parse_value("false") is False
        assert _parse_value("0") is False
        assert _parse_value("no") is False

    def test_integer(self):
        assert _parse_value("42") == 42

    def test_string(self):
        assert _parse_value("hello") == "hello"


class TestParseBool:
    def test_truthy(self):
        assert _parse_bool("1") is True
        assert _parse_bool("true") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("on") is True

    def test_falsy(self):
        assert _parse_bool("0") is False
        assert _parse_bool("false") is False
        assert _parse_bool("no") is False
        assert _parse_bool("off") is False
        assert _parse_bool("") is False


# ---------------------------------------------------------------------------
# Cached settings
# ---------------------------------------------------------------------------


class TestGetSettings:
    def test_returns_cached_instance(self):
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2


class TestReloadSettings:
    def test_clears_cache_and_returns_new(self):
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = reload_settings()
        assert s1 is not s2
