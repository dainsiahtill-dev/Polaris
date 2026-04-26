"""Integration tests for CodexCLIProvider.

Covers:
- Happy path: invoke(), health(), list_models()
- Edge cases: empty response, command not found, JSON parsing
- Exception paths: process failure, timeout, CLI errors
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from polaris.infrastructure.llm.providers.codex_cli_provider import CodexCLIProvider
from polaris.kernelone.llm.types import InvokeResult


class TestCodexCLIProviderHappyPath:
    """Tests for the normal successful execution paths."""

    def test_get_provider_info(self) -> None:
        info = CodexCLIProvider.get_provider_info()
        assert info.type == "codex_cli"
        assert "thinking_extraction" in info.supported_features
        assert info.provider_category == "AGENT"

    def test_get_default_config(self) -> None:
        defaults = CodexCLIProvider.get_default_config()
        assert defaults["type"] == "codex_cli"
        assert defaults["command"] == "codex"
        assert defaults["codex_exec"]["json"] is True

    def test_validate_config_valid(self, codex_cli_config: dict[str, Any]) -> None:
        result = CodexCLIProvider.validate_config(codex_cli_config)
        assert result.valid is True

    def test_validate_config_missing_command(self) -> None:
        config: dict[str, Any] = {"type": "codex_cli"}
        result = CodexCLIProvider.validate_config(config)
        assert result.valid is True  # command defaults to "codex"
        assert result.normalized_config is not None
        # _resolve_command may find the actual codex binary; just verify it's set
        assert result.normalized_config.get("command") is not None

    def test_invoke_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        mock_resp_stdout = '{"type":"item.completed","item":{"type":"agent_message","text":"Hello!"}}'

        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 0, mock_resp_stdout, "", 42

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._run_cli",
            _mock_run_cli,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        result = provider.invoke("Say hello", "gpt-4-codex", codex_cli_config)

        assert isinstance(result, InvokeResult)
        assert result.ok is True
        assert "Hello!" in result.output
        assert result.error is None
        assert result.latency_ms == 42

    def test_health_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 0, "codex version 1.0.0", "", 15

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._run_cli",
            _mock_run_cli,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        result = provider.health(codex_cli_config)

        assert result.ok is True
        assert result.error is None
        assert result.latency_ms == 15
        assert result.details is not None
        assert "version" in result.details

    def test_list_models_with_manual_models(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        config = {**codex_cli_config, "manual_models": ["gpt-4-codex", "gpt-5-codex"]}
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        result = provider.list_models(config)

        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "gpt-4-codex"

    def test_list_models_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        result = provider.list_models(codex_cli_config)

        assert result.ok is True
        assert len(result.models) >= 4
        assert any(m.id == "gpt-4-codex" for m in result.models)


class TestCodexCLIProviderEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_invoke_command_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: None,
        )

        provider = CodexCLIProvider()
        result = provider.invoke("Say hello", "gpt-4-codex", codex_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "command" in result.error.lower() or "not found" in result.error.lower()

    def test_invoke_empty_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 0, "", "", 10

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._run_cli",
            _mock_run_cli,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        result = provider.invoke("Say nothing", "gpt-4-codex", codex_cli_config)

        assert result.ok is True
        assert result.output == ""

    def test_invoke_json_parse(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        """Test that JSON Lines output from codex exec --json is parsed correctly."""
        raw_output = '{"type":"item.completed","item":{"type":"agent_message","text":"Parsed output"}}\n'

        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 0, raw_output, "", 20

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._run_cli",
            _mock_run_cli,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        result = provider.invoke("Test", "gpt-4-codex", codex_cli_config)

        assert result.ok is True
        assert "Parsed output" in result.output

    def test_validate_config_invalid_cli_mode(self) -> None:
        config: dict[str, Any] = {
            "type": "codex_cli",
            "cli_mode": "invalid_mode",
        }
        result = CodexCLIProvider.validate_config(config)
        assert result.valid is True
        assert any("cli_mode" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["cli_mode"] == "headless"

    def test_validate_config_negative_timeout(self) -> None:
        config: dict[str, Any] = {
            "type": "codex_cli",
            "timeout": -10,
        }
        result = CodexCLIProvider.validate_config(config)
        assert result.valid is True
        assert any("timeout" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["timeout"] == 60

    def test_health_command_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: None,
        )

        provider = CodexCLIProvider()
        result = provider.health(codex_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "command" in result.error.lower() or "not found" in result.error.lower()

    def test_list_models_command_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: None,
        )

        provider = CodexCLIProvider()
        result = provider.list_models(codex_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "command" in result.error.lower() or "not found" in result.error.lower()


class TestCodexCLIProviderExceptions:
    """Tests for error and exception handling paths."""

    def test_invoke_process_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 1, "", "CLI error: invalid model", 5

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._run_cli",
            _mock_run_cli,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        result = provider.invoke("Say hello", "gpt-4-codex", codex_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "CLI error" in result.error

    def test_invoke_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            raise subprocess.TimeoutExpired(cmd="codex", timeout=30)

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._run_cli",
            _mock_run_cli,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        result = provider.invoke("Say hello", "gpt-4-codex", codex_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "timeout" in result.error.lower()

    def test_invoke_runtime_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            raise RuntimeError("Unexpected runtime error")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._run_cli",
            _mock_run_cli,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        result = provider.invoke("Say hello", "gpt-4-codex", codex_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "Unexpected runtime error" in result.error

    def test_health_process_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 1, "", "health check failed", 5

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._run_cli",
            _mock_run_cli,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        result = provider.health(codex_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "health check failed" in result.error

    def test_extract_thinking_support(self) -> None:
        response = {
            "output": "<thinking>I need to analyze this.</thinking>\n\nResult here.",
            "config": {
                "thinking_extraction": {
                    "enabled": True,
                    "patterns": [r"<thinking>(.*?)</thinking>"],
                    "confidence_threshold": 0.7,
                }
            },
        }
        info = CodexCLIProvider.extract_thinking_support(response)
        assert info.supports_thinking is True
        assert info.format == "xml"
        # Confidence for short text may be below threshold
        assert info.extraction_method in ("codex_pattern", "codex_pattern_low_confidence")

    def test_extract_thinking_support_disabled(self) -> None:
        response = {
            "output": "Some text",
            "config": {
                "thinking_extraction": {
                    "enabled": False,
                }
            },
        }
        info = CodexCLIProvider.extract_thinking_support(response)
        assert info.supports_thinking is False
        assert info.extraction_method == "disabled"

    def test_extract_thinking_support_no_output(self) -> None:
        response: dict[str, Any] = {"config": {}}
        info = CodexCLIProvider.extract_thinking_support(response)
        assert info.supports_thinking is False
        assert info.extraction_method == "codex_default"

    @pytest.mark.asyncio
    async def test_invoke_stream_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        mock_resp_stdout = '{"type":"item.completed","item":{"type":"agent_message","text":"Stream fallback"}}'

        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 0, mock_resp_stdout, "", 10

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._run_cli",
            _mock_run_cli,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: "/usr/bin/codex",
        )

        provider = CodexCLIProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "gpt-4-codex", codex_cli_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert "Stream fallback" in chunks[0]

    @pytest.mark.asyncio
    async def test_invoke_stream_error_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_cli_config: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.codex_cli_provider._resolve_command",
            lambda _cmd: None,
        )

        provider = CodexCLIProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "gpt-4-codex", codex_cli_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].startswith("Error:")
