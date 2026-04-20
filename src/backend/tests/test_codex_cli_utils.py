"""Tests for Codex CLI Provider utility functions"""

from polaris.infrastructure.llm.providers.codex_cli_provider import CodexCLIProvider
from polaris.infrastructure.llm.providers.codex_cli_args import (
    _build_codex_exec_args,
    _supports_reasoning_effort,
)
from polaris.infrastructure.llm.providers.codex_command_utils import (
    _normalize_command,
    _truncate,
)


class TestNormalizeCommand:
    def test_bat_file(self):
        result = _normalize_command("test.cmd")
        assert result == ["cmd.exe", "/c", "test.cmd"]

    def test_ps1_file(self):
        result = _normalize_command("script.ps1")
        assert "powershell" in result[0].lower()

class TestTruncate:
    def test_short_text(self):
        result = _truncate("short", 10)
        assert result == "short"
    def test_long_text(self):
        result = _truncate("very long text here", 10)
        assert len(result) <= 10
        assert result.endswith("...")

    def test_empty_text(self):
        result = _truncate("", 10)
        assert result == ""

class TestSupportsReasoningEffort:
    def test_gpt_models(self):
        assert _supports_reasoning_effort("gpt-4") is True
    def test_o_series(self):
        assert _supports_reasoning_effort("o1") is True
    def test_codex_models(self):
        assert _supports_reasoning_effort("codex") is True
    def test_empty_model(self):
        assert _supports_reasoning_effort("") is False
    def test_other_models(self):
        assert _supports_reasoning_effort("llama-2") is False

class TestBuildCodexExecArgs:
    def test_default_args(self):
        result = _build_codex_exec_args("gpt-4", {})
        assert "exec" in result
        assert "--json" in result

    def test_custom_sandbox(self):
        config = {"codex_exec": {"sandbox": "workspace-write"}}
        result = _build_codex_exec_args("model", config)
        assert "workspace-write" in result

class TestCodexCLIProviderConfig:
    def test_provider_capabilities(self):
        info = CodexCLIProvider.get_provider_info()
        assert info.autonomous_file_access is True
        assert info.provider_category == "AGENT"

    def test_supported_features(self):
        info = CodexCLIProvider.get_provider_info()
        assert "thinking_extraction" in info.supported_features

    def test_cli_mode_default(self):
        config = CodexCLIProvider.get_default_config()
        assert config["cli_mode"] == "headless"

    def test_validate_config(self):
        valid_config = {"command": "codex", "timeout": 300}
        result = CodexCLIProvider.validate_config(valid_config)
        assert result.valid is True
