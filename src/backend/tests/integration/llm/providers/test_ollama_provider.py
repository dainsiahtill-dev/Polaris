"""Integration tests for OllamaProvider.

Covers:
- Happy path: invoke(), health(), list_models() in native and OpenAI-compat modes
- Edge cases: empty response, missing base_url, tool calling
- Exception paths: HTTP 4xx/5xx, network errors
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.llm.providers.ollama_provider import OllamaProvider
from polaris.kernelone.llm.types import InvokeResult


class TestOllamaProviderHappyPath:
    """Tests for the normal successful execution paths."""

    def test_get_provider_info(self) -> None:
        info = OllamaProvider.get_provider_info()
        assert info.type == "ollama"
        assert "local_inference" in info.supported_features

    def test_get_default_config(self) -> None:
        defaults = OllamaProvider.get_default_config()
        assert defaults["base_url"] == "http://120.24.117.59:11434"
        assert defaults["api_key"] == "ollama"

    def test_validate_config_valid(self, ollama_config: dict[str, Any]) -> None:
        result = OllamaProvider.validate_config(ollama_config)
        assert result.valid is True
        assert not result.errors

    def test_invoke_native_chat_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
        sample_ollama_response: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_ollama_response
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.invoke("Say hello", "llama2", ollama_config)

        assert isinstance(result, InvokeResult)
        assert result.ok is True
        assert result.output == "Hello! How can I help you today?"
        assert result.error is None
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 8

    def test_invoke_openai_compat_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_openai_response: dict[str, Any],
    ) -> None:
        config = {
            "base_url": "http://localhost:11434",
            "api_path": "/v1/chat/completions",
            "api_key": "ollama",
            "timeout": 30,
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_openai_response
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.invoke("Say hello", "llama2", config)

        assert result.ok is True
        assert result.output == "Hello! How can I help you today?"

    def test_health_native_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.health(ollama_config)

        assert result.ok is True
        assert result.error is None
        assert result.latency_ms >= 0

    def test_health_openai_compat_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = {
            "base_url": "http://localhost:11434",
            "api_path": "/v1/chat/completions",
            "api_key": "ollama",
            "timeout": 30,
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.health(config)

        assert result.ok is True

    def test_list_models_native_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
    ) -> None:
        payload = {
            "models": [
                {"name": "llama2", "size": 1000000000},
                {"name": "mistral", "size": 2000000000},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.list_models(ollama_config)

        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "llama2"

    def test_list_models_openai_compat_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = {
            "base_url": "http://localhost:11434",
            "api_path": "/v1/chat/completions",
            "api_key": "ollama",
            "timeout": 30,
        }
        payload = {
            "data": [
                {"id": "llama2", "object": "model"},
                {"id": "mistral", "object": "model"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.list_models(config)

        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "llama2"


class TestOllamaProviderEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_invoke_empty_response_native(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "model": "llama2",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 0,
        }
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.invoke("Say nothing", "llama2", ollama_config)

        assert result.ok is True
        assert result.output == ""

    def test_invoke_generate_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = {
            "base_url": "http://localhost:11434",
            "api_path": "/api/generate",
            "timeout": 30,
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "model": "llama2",
            "response": "Generated text here",
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 3,
        }
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.invoke("Generate", "llama2", config)

        assert result.ok is True
        assert result.output == "Generated text here"

    def test_validate_config_missing_base_url(self) -> None:
        config: dict[str, Any] = {}
        result = OllamaProvider.validate_config(config)
        # Ollama has a default base_url, so missing it is still valid
        assert result.valid is True
        assert result.normalized_config is not None
        assert result.normalized_config.get("base_url") == "http://120.24.117.59:11434"

    def test_validate_config_negative_timeout(self) -> None:
        config = {"base_url": "http://localhost:11434", "timeout": -10}
        result = OllamaProvider.validate_config(config)
        assert result.valid is True
        assert any("timeout" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["timeout"] == 60

    def test_validate_config_openai_compat_missing_api_key(self) -> None:
        config = {"base_url": "http://localhost:11434", "api_path": "/v1/chat/completions"}
        result = OllamaProvider.validate_config(config)
        assert result.valid is True
        assert any("api_key" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config.get("api_key") == "ollama"

    def test_list_models_empty_native(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.list_models(ollama_config)

        assert result.ok is True
        assert result.models == []


class TestOllamaProviderExceptions:
    """Tests for error and exception handling paths."""

    def test_invoke_http_500(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
    ) -> None:
        import requests

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 500
        mock_resp.text = '{"error": "Internal Server Error"}'
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.invoke("Hello", "llama2", ollama_config)

        assert result.ok is False
        assert result.error is not None
        assert "500" in result.error or "Server Error" in result.error

    def test_invoke_requests_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
    ) -> None:
        """requests.ConnectionError must be caught and returned gracefully.

        Regression test for: only RuntimeError/ValueError were caught, letting
        requests exceptions propagate uncaught.
        """
        import requests

        def _mock_post(*_args: Any, **_kwargs: Any) -> Any:
            raise requests.ConnectionError("Connection refused")

        monkeypatch.setattr(
            "requests.post",
            _mock_post,
        )

        provider = OllamaProvider()
        result = provider.invoke("Hello", "llama2", ollama_config)

        assert result.ok is False
        assert result.error is not None
        assert "Connection" in result.error

    def test_health_http_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
    ) -> None:
        import requests

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_resp.raise_for_status.side_effect = requests.HTTPError("404 Client Error: Not Found")

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.health(ollama_config)

        assert result.ok is False
        assert result.error is not None
        assert "404" in result.error or "Not Found" in result.error

    def test_list_models_http_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
    ) -> None:
        import requests

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 503
        mock_resp.text = "Service Unavailable"
        mock_resp.raise_for_status.side_effect = requests.HTTPError("503 Server Error")

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        result = provider.list_models(ollama_config)

        assert result.ok is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invoke_stream_success_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
        sample_ollama_response: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_ollama_response
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "llama2", ollama_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0] == "Hello! How can I help you today?"

    @pytest.mark.asyncio
    async def test_invoke_stream_error_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ollama_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 429
        mock_resp.text = "Rate limited"
        mock_resp.raise_for_status.side_effect = RuntimeError("429 Rate Limited")

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = OllamaProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "llama2", ollama_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].startswith("Error:")
