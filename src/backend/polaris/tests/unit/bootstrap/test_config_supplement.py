"""Supplementary tests for polaris.bootstrap.config uncovered branches."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polaris.bootstrap.config import (
    Settings,
    SettingsUpdate,
)


class TestSettingsFromEnvBranches:
    """Test Settings.from_env() for previously uncovered branches."""

    def test_from_env_llm_config(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_LLM_PROVIDER", "openai")
        monkeypatch.setenv("KERNELONE_LLM_BASE_URL", "http://localhost:11434")
        monkeypatch.setenv("KERNELONE_LLM_API_KEY", "test-key")
        monkeypatch.setenv("KERNELONE_LLM_API_PATH", "/v1/chat")
        monkeypatch.setenv("KERNELONE_LLM_TIMEOUT", "30")
        settings = Settings.from_env()
        assert settings.llm.provider == "openai"
        assert settings.llm.base_url == "http://localhost:11434"
        assert settings.llm.api_key == "test-key"

    def test_from_env_pm_config(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_PM_BACKEND", "codex")
        monkeypatch.setenv("KERNELONE_PM_MODEL", "gpt-4")
        monkeypatch.setenv("KERNELONE_PM_DIRECTOR_TIMEOUT", "60")
        monkeypatch.setenv("KERNELONE_PM_DIRECTOR_ITERATIONS", "3")
        monkeypatch.setenv("KERNELONE_PM_SHOW_OUTPUT", "1")
        monkeypatch.setenv("KERNELONE_PM_RUNS_DIRECTOR", "0")
        monkeypatch.setenv("KERNELONE_PM_DIRECTOR_SHOW_OUTPUT", "1")
        settings = Settings.from_env()
        assert settings.pm.backend == "codex"
        assert settings.pm.model == "gpt-4"
        assert settings.pm.show_output is True
        assert settings.pm.runs_director is False

    def test_from_env_director_config(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_DIRECTOR_MODEL", "gpt-4-director")
        monkeypatch.setenv("KERNELONE_DIRECTOR_ITERATIONS", "5")
        monkeypatch.setenv("KERNELONE_DIRECTOR_WORKFLOW_EXECUTION_MODE", "serial")
        monkeypatch.setenv("KERNELONE_DIRECTOR_MAX_PARALLEL_TASKS", "4")
        monkeypatch.setenv("KERNELONE_DIRECTOR_READY_TIMEOUT_SECONDS", "10")
        monkeypatch.setenv("KERNELONE_DIRECTOR_FOREVER", "1")
        monkeypatch.setenv("KERNELONE_DIRECTOR_SHOW_OUTPUT", "0")
        settings = Settings.from_env()
        assert settings.director.model == "gpt-4-director"
        assert settings.director.iterations == 5
        assert settings.director.execution_mode == "serial"
        assert settings.director.forever is False  # parsed via _parse_bool -> False for "0"

    def test_from_env_runtime_config(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", "/tmp/runtime")
        monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", "/tmp/cache")
        monkeypatch.setenv("KERNELONE_RAMDISK_ROOT", "/tmp/ramdisk")
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "1")
        settings = Settings.from_env()
        assert settings.runtime.root == Path("/tmp/runtime").resolve()
        assert settings.runtime.cache_root == Path("/tmp/cache").resolve()

    def test_from_env_logging_config(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_LOG_LEVEL", "ERROR")
        monkeypatch.setenv("KERNELONE_DEBUG_TRACING", "0")
        monkeypatch.setenv("KERNELONE_JSON_LOG_PATH", "/tmp/logs.json")
        settings = Settings.from_env()
        assert settings.logging.level == "ERROR"
        assert settings.logging.enable_debug_tracing is False
        assert settings.json_log_path == "/tmp/logs.json"

    def test_from_env_server_config(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_BACKEND_PORT", "8080")
        monkeypatch.setenv("KERNELONE_CORS_ORIGINS", "http://localhost:3000,https://example.com")
        settings = Settings.from_env()
        assert settings.server.port == 8080
        assert "http://localhost:3000" in settings.server.cors_origins
        assert "https://example.com" in settings.server.cors_origins

    def test_from_env_nats_config(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_NATS_ENABLED", "1")
        monkeypatch.setenv("KERNELONE_NATS_URL", "nats://localhost:4222")
        monkeypatch.setenv("KERNELONE_NATS_USER", "user")
        monkeypatch.setenv("KERNELONE_NATS_PASSWORD", "pass")
        monkeypatch.setenv("KERNELONE_NATS_STREAM_NAME", "test-stream")
        settings = Settings.from_env()
        assert settings.nats.enabled is True
        assert settings.nats.url == "nats://localhost:4222"
        assert settings.nats.user == "user"

    def test_from_env_audit_config(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_AUDIT_LLM_ENABLED", "0")
        monkeypatch.setenv("KERNELONE_AUDIT_LLM_ROLE", "REVIEWER")
        monkeypatch.setenv("KERNELONE_AUDIT_LLM_TIMEOUT", "120")
        monkeypatch.setenv("KERNELONE_AUDIT_LLM_PREFER_LOCAL_OLLAMA", "0")
        monkeypatch.setenv("KERNELONE_AUDIT_LLM_ALLOW_REMOTE_FALLBACK", "0")
        settings = Settings.from_env()
        assert settings.audit_llm_enabled is False
        assert settings.audit_llm_role == "reviewer"
        assert settings.audit_llm_timeout == 120
        assert settings.audit_llm_prefer_local_ollama is False

    def test_from_env_json_log_path_becomes_logging_json_path(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_JSON_LOG_PATH", "/tmp/test.jsonl")
        settings = Settings.from_env()
        assert settings.logging.json_path == Path("/tmp/test.jsonl").resolve()

    def test_from_env_timeout_parsed(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_TIMEOUT", "300")
        settings = Settings.from_env()
        assert settings.timeout == 300

    def test_from_env_slm_and_qa_flags(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_SLM_ENABLED", "1")
        monkeypatch.setenv("KERNELONE_QA_ENABLED", "0")
        settings = Settings.from_env()
        assert settings.slm_enabled is True
        assert settings.qa_enabled is False


class TestSettingsApplyUpdateBranches:
    """Test Settings.apply_update() for uncovered branches."""

    def test_apply_update_nested_llm(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(llm={"model": "gpt-4-turbo", "provider": "openai"})
        settings.apply_update(update)
        assert settings.llm.model == "gpt-4-turbo"
        assert settings.llm.provider == "openai"

    def test_apply_update_nested_director(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(director={"iterations": 7, "execution_mode": "serial"})
        settings.apply_update(update)
        assert settings.director.iterations == 7
        assert settings.director.execution_mode == "serial"

    def test_apply_update_nested_runtime(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(runtime={"use_ramdisk": False, "root": "/tmp/rt"})
        settings.apply_update(update)
        assert settings.runtime.use_ramdisk is False
        assert settings.runtime.root == Path("/tmp/rt").resolve()

    def test_apply_update_nested_logging(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(logging={"level": "WARNING", "enable_debug_tracing": False})
        settings.apply_update(update)
        assert settings.logging.level == "WARNING"
        assert settings.logging.enable_debug_tracing is False

    def test_apply_update_nested_server(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(server={"host": "0.0.0.0", "port": 8080})
        settings.apply_update(update)
        assert settings.server.host == "0.0.0.0"
        assert settings.server.port == 8080

    def test_apply_update_nested_jsonl(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(jsonl={"lock_stale_sec": 60.0, "max_paths": 50})
        settings.apply_update(update)
        assert settings.jsonl.lock_stale_sec == 60.0
        assert settings.jsonl.max_paths == 50

    def test_apply_update_nested_nats(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(nats={"enabled": True, "url": "nats://remote:4222"})
        settings.apply_update(update)
        assert settings.nats.enabled is True
        assert settings.nats.url == "nats://remote:4222"

    def test_apply_update_timeout(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(timeout=600)
        settings.apply_update(update)
        assert settings.timeout == 600

    def test_apply_update_json_log_path(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(json_log_path="/tmp/logs.json")
        settings.apply_update(update)
        assert settings.json_log_path == "/tmp/logs.json"

    def test_apply_update_model(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(model="claude-3")
        settings.apply_update(update)
        assert settings.model == "claude-3"

    def test_apply_update_ramdisk_root(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(ramdisk_root="/tmp/ramdisk")
        settings.apply_update(update)
        assert settings.ramdisk_root == str(Path("/tmp/ramdisk").resolve())

    def test_apply_update_audit_fields_edge_cases(self):
        settings = Settings(workspace="/tmp/test_ws")
        update = SettingsUpdate(audit_llm_role="", audit_llm_timeout="invalid")
        settings.apply_update(update)
        assert settings.audit_llm_role == "qa"
        assert settings.audit_llm_timeout == 180

    def test_apply_update_unknown_field_ignored(self):
        settings = Settings(workspace="/tmp/test_ws")
        original_workspace = settings.workspace
        update = SettingsUpdate(unknown_field="value")
        settings.apply_update(update)
        assert settings.workspace == original_workspace


class TestSettingsRuntimeBaseBranches:
    """Test Settings.runtime_base edge cases."""

    def test_runtime_base_prefers_explicit_root(self):
        settings = Settings(workspace="/tmp/test_ws", runtime={"root": "/tmp/explicit"})
        assert settings.runtime_base == Path("/tmp/explicit").resolve()

    def test_runtime_base_uses_cache_root_when_no_ramdisk(self):
        settings = Settings(workspace="/tmp/test_ws", runtime={"use_ramdisk": False, "cache_root": "/tmp/cache"})
        assert settings.runtime_base == Path("/tmp/cache").resolve()

    def test_runtime_base_falls_back_to_system_cache(self):
        settings = Settings(workspace="/tmp/test_ws", runtime={"use_ramdisk": False})
        base = settings.runtime_base
        assert isinstance(base, Path)
        assert "polaris" in str(base).lower() or "cache" in str(base).lower()


class TestSettingsPayloadCompleteness:
    """Test Settings.to_payload() covers all compatibility keys."""

    def test_payload_contains_all_director_keys(self):
        settings = Settings(workspace="/tmp/test_ws")
        payload = settings.to_payload()
        director_keys = [
            "director_iterations", "director_execution_mode", "director_max_parallel_tasks",
            "director_ready_timeout_seconds", "director_claim_timeout_seconds",
            "director_phase_timeout_seconds", "director_complete_timeout_seconds",
            "director_task_timeout_seconds", "director_forever", "director_show_output",
        ]
        for key in director_keys:
            assert key in payload, f"Missing key: {key}"

    def test_payload_contains_all_pm_keys(self):
        settings = Settings(workspace="/tmp/test_ws")
        payload = settings.to_payload()
        pm_keys = [
            "pm_backend", "pm_model", "pm_show_output", "pm_runs_director",
            "pm_director_show_output", "pm_director_timeout", "pm_director_iterations",
            "pm_director_match_mode", "pm_agents_approval_mode", "pm_agents_approval_timeout",
            "pm_max_failures", "pm_max_blocked", "pm_max_same", "pm_blocked_strategy",
            "pm_blocked_degrade_max_retries",
        ]
        for key in pm_keys:
            assert key in payload, f"Missing key: {key}"

    def test_payload_contains_nats_keys(self):
        settings = Settings(workspace="/tmp/test_ws")
        payload = settings.to_payload()
        assert "nats_enabled" in payload
        assert "nats_required" in payload
        assert "nats_url" in payload
        assert "nats_stream_name" in payload

    def test_payload_debug_tracing(self):
        settings = Settings(workspace="/tmp/test_ws")
        payload = settings.to_payload()
        assert "debug_tracing" in payload
        assert payload["debug_tracing"] == settings.logging.enable_debug_tracing
