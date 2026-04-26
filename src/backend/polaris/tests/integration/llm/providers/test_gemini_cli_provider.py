"""Integration tests for GeminiCLIProvider.

Covers:
- Happy path: invoke(), health(), list_models()
- Edge cases: empty response, command not found, missing API key
- Exception paths: process failure, timeout, CLI errors
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from polaris.infrastructure.llm.providers.gemini_cli_provider import GeminiCLIProvider
from polaris.kernelone.llm.types import InvokeResult


class TestGeminiCLIProviderHappyPath:
    """Tests for the normal successful execution paths."""

    def test_get_provider_info(self) -> None:
        info = GeminiCLIProvider.get_provider_info()
        assert info.type == "gemini_cli"
        assert "thinking_extraction" in info.supported_features
        assert info.provider_category == "AGENT"

    def test_get_default_config(self) -> None:
        defaults = GeminiCLIProvider.get_default_config()
        assert defaults["command"] == "gemini"
        assert defaults["timeout"] == 60
        assert "GOOGLE_API_KEY" in defaults["env"]

    def test_validate_config_valid(self, gemini_cli_config: dict[str, Any]) -> None:
        result = GeminiCLIProvider.validate_config(gemini_cli_config)
        assert result.valid is True

    def test_invoke_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 0, "Hello! How can I help you today?", "", 42

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        result = provider.invoke("Say hello", "gemini-1.5-pro", gemini_cli_config)

        assert isinstance(result, InvokeResult)
        assert result.ok is True
        assert "Hello!" in result.output
        assert result.error is None
        assert result.latency_ms == 42

    def test_health_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 0, "gemini version 1.0.0", "", 15

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        result = provider.health(gemini_cli_config)

        assert result.ok is True
        assert result.error is None
        assert result.latency_ms == 15

    def test_list_models_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            stdout = "gemini-1.5-pro\ngemini-1.5-flash\ngemini-1.0-pro\n"
            return 0, stdout, "", 20

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        result = provider.list_models(gemini_cli_config)

        assert result.ok is True
        assert len(result.models) == 3
        assert result.models[0].id == "gemini-1.5-pro"

    def test_list_models_json_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            stdout = '[{"id":"gemini-1.5-pro"},{"id":"gemini-1.5-flash"}]'
            return 0, stdout, "", 20

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        result = provider.list_models(gemini_cli_config)

        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "gemini-1.5-pro"


class TestGeminiCLIProviderEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_invoke_command_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: None),
        )

        provider = GeminiCLIProvider()
        result = provider.invoke("Say hello", "gemini-1.5-pro", gemini_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_invoke_empty_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
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
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        result = provider.invoke("Say nothing", "gemini-1.5-pro", gemini_cli_config)

        assert result.ok is True
        assert result.output == ""

    def test_validate_config_missing_api_key(self) -> None:
        config: dict[str, Any] = {
            "type": "gemini_cli",
            "command": "gemini",
            "env": {},
        }
        result = GeminiCLIProvider.validate_config(config)
        assert result.valid is False
        assert any("api key" in e.lower() for e in result.errors)

    def test_validate_config_invalid_args(self) -> None:
        config: dict[str, Any] = {
            "type": "gemini_cli",
            "command": "gemini",
            "args": "not_a_list",
            "env": {"GOOGLE_API_KEY": "test"},
        }
        result = GeminiCLIProvider.validate_config(config)
        assert result.valid is False
        assert any("args" in e.lower() for e in result.errors)

    def test_validate_config_missing_placeholders(self) -> None:
        config: dict[str, Any] = {
            "type": "gemini_cli",
            "command": "gemini",
            "args": ["chat"],
            "env": {"GOOGLE_API_KEY": "test"},
        }
        result = GeminiCLIProvider.validate_config(config)
        assert result.valid is True
        assert any("{model}" in w for w in result.warnings)
        assert any("{prompt}" in w for w in result.warnings)

    def test_validate_config_negative_timeout(self) -> None:
        config: dict[str, Any] = {
            "type": "gemini_cli",
            "command": "gemini",
            "timeout": -10,
            "env": {"GOOGLE_API_KEY": "test"},
        }
        result = GeminiCLIProvider.validate_config(config)
        assert result.valid is True
        assert any("timeout" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["timeout"] == 60

    def test_validate_config_invalid_cli_mode(self) -> None:
        config: dict[str, Any] = {
            "type": "gemini_cli",
            "command": "gemini",
            "cli_mode": "invalid",
            "env": {"GOOGLE_API_KEY": "test"},
        }
        result = GeminiCLIProvider.validate_config(config)
        assert result.valid is True
        assert any("cli_mode" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["cli_mode"] == "headless"

    def test_health_command_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: None),
        )

        provider = GeminiCLIProvider()
        result = provider.health(gemini_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_list_models_command_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: None),
        )

        provider = GeminiCLIProvider()
        result = provider.list_models(gemini_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_clean_output_removes_artifacts(self) -> None:
        raw = "Generated by Gemini CLI\nModel: gemini-1.5-pro\n\nActual content here."
        cleaned = GeminiCLIProvider._clean_output(raw)
        assert "Generated by Gemini" not in cleaned
        assert "Model:" not in cleaned
        assert "Actual content here." in cleaned


class TestGeminiCLIProviderExceptions:
    """Tests for error and exception handling paths."""

    def test_invoke_process_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 1, "", "gemini CLI error", 5

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        result = provider.invoke("Say hello", "gemini-1.5-pro", gemini_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "gemini CLI error" in result.error

    def test_invoke_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            raise subprocess.TimeoutExpired(cmd="gemini", timeout=30)

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        result = provider.invoke("Say hello", "gemini-1.5-pro", gemini_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "timeout" in result.error.lower()

    def test_invoke_runtime_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
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
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        result = provider.invoke("Say hello", "gemini-1.5-pro", gemini_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "Unexpected runtime error" in result.error

    def test_health_process_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
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
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        result = provider.health(gemini_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "health check failed" in result.error

    def test_list_models_process_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 1, "", "model listing failed", 5

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        result = provider.list_models(gemini_cli_config)

        assert result.ok is False
        assert result.error is not None
        assert "model listing failed" in result.error

    def test_extract_thinking_support_detected(self) -> None:
        response = {
            "output": "<thinking>I need to analyze this.</thinking>\n\nResult here.",
            "config": {
                "thinking_extraction": {
                    "enabled": True,
                    "patterns": [r"<thinking>(.*?)</thinking>"],
                    "confidence_threshold": 0.1,
                }
            },
        }
        info = GeminiCLIProvider.extract_thinking_support(response)
        assert info.supports_thinking is True
        assert info.format == "xml"
        assert info.extraction_method == "gemini_pattern"

    def test_extract_thinking_support_disabled(self) -> None:
        response = {
            "output": "Some text",
            "config": {
                "thinking_extraction": {
                    "enabled": False,
                }
            },
        }
        info = GeminiCLIProvider.extract_thinking_support(response)
        assert info.supports_thinking is False
        assert info.extraction_method == "disabled"

    def test_extract_thinking_support_keyword(self) -> None:
        response = {
            "output": "Let me analyze this step by step. First, I should consider the context.",
            "config": {
                "thinking_extraction": {
                    "enabled": True,
                    "patterns": [],
                    "confidence_threshold": 0.6,
                }
            },
        }
        info = GeminiCLIProvider.extract_thinking_support(response)
        assert info.supports_thinking is True
        assert info.extraction_method == "gemini_keyword"

    def test_extract_thinking_support_no_output(self) -> None:
        response: dict[str, Any] = {"config": {}}
        info = GeminiCLIProvider.extract_thinking_support(response)
        assert info.supports_thinking is False
        assert info.extraction_method == "gemini_default"

    @pytest.mark.asyncio
    async def test_invoke_stream_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        def _mock_run_cli(
            _command: str,
            _args: list[str],
            _cwd: str,
            _env: dict[str, str] | None,
            _timeout: int,
            _input_text: str | None,
        ) -> tuple[int, str, str, int]:
            return 0, "Stream fallback output", "", 10

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._run_cli",
            staticmethod(_mock_run_cli),
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: "/usr/bin/gemini"),
        )

        provider = GeminiCLIProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "gemini-1.5-pro", gemini_cli_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0] == "Stream fallback output"

    @pytest.mark.asyncio
    async def test_invoke_stream_error_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_cli_config: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.gemini_cli_provider.GeminiCLIProvider._resolve_command",
            staticmethod(lambda _cmd: None),
        )

        provider = GeminiCLIProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "gemini-1.5-pro", gemini_cli_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].startswith("Error:")
