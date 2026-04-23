"""Shared fixtures for LLM provider integration tests."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def sample_messages() -> list[dict[str, str]]:
    """Standard test messages."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, world!"},
    ]


@pytest.fixture
def sample_openai_response() -> dict[str, Any]:
    """Standard OpenAI-compatible chat completion response."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "gpt-4",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello! How can I help you today?"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }


@pytest.fixture
def sample_anthropic_response() -> dict[str, Any]:
    """Standard Anthropic-compatible messages response."""
    return {
        "id": "msg_01Test",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-haiku-20240307",
        "content": [{"type": "text", "text": "Hello! How can I help you today?"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 8},
    }


@pytest.fixture
def sample_gemini_response() -> dict[str, Any]:
    """Standard Gemini API generateContent response."""
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": "Hello! How can I help you today?"}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 8, "totalTokenCount": 18},
    }


@pytest.fixture
def sample_ollama_response() -> dict[str, Any]:
    """Standard Ollama native chat response."""
    return {
        "model": "llama2",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": "Hello! How can I help you today?"},
        "done": True,
        "prompt_eval_count": 10,
        "eval_count": 8,
    }


@pytest.fixture
def sample_kimi_response() -> dict[str, Any]:
    """Standard Kimi/OpenAI-compatible chat completion response."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "kimi-k2-turbo",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello! How can I help you today?"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }


@pytest.fixture
def mock_http_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Return a mock that replaces requests.post/get in provider helpers.

    Usage:
        mock_post = MagicMock(return_value=mock_response)
        monkeypatch.setattr("requests.post", mock_post)
    """
    return MagicMock()


@pytest.fixture
def mock_response_ok() -> MagicMock:
    """A successful requests.Response mock."""
    mock = MagicMock()
    mock.ok = True
    mock.status_code = 200
    mock.text = json.dumps({"ok": True})
    mock.json.return_value = {"ok": True}
    return mock


@pytest.fixture
def mock_response_401() -> MagicMock:
    """A 401 Unauthorized requests.Response mock."""
    mock = MagicMock()
    mock.ok = False
    mock.status_code = 401
    mock.text = '{"error": "Unauthorized"}'
    mock.json.return_value = {"error": "Unauthorized"}
    mock.raise_for_status.side_effect = Exception("401 Client Error: Unauthorized")
    return mock


@pytest.fixture
def mock_response_500() -> MagicMock:
    """A 500 Internal Server Error requests.Response mock."""
    mock = MagicMock()
    mock.ok = False
    mock.status_code = 500
    mock.text = '{"error": "Internal Server Error"}'
    mock.json.return_value = {"error": "Internal Server Error"}
    mock.raise_for_status.side_effect = Exception("500 Server Error: Internal Server Error")
    return mock


@pytest.fixture
def mock_response_timeout() -> MagicMock:
    """A timeout-side-effect mock for requests."""
    mock = MagicMock()
    mock.raise_for_status.side_effect = Exception("Request timeout")
    return mock


def _make_mock_stream_session(
    events: list[dict[str, Any]],
) -> Any:
    """Create a mock aiohttp-like session for SSE streaming tests.

    Yields JSON-encoded SSE data lines for each event dict.
    """
    import json

    class _MockResponse:
        def __init__(self) -> None:
            self.status = 200
            self.ok = True
            self.headers = {"Content-Type": "text/event-stream"}

        async def text(self) -> str:
            return ""

        async def json(self) -> dict[str, Any]:
            return {}

        def raise_for_status(self) -> None:
            pass

        async def __aenter__(self) -> _MockResponse:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        @property
        def content(self) -> Any:
            lines: list[bytes] = []
            for ev in events:
                lines.append(f"data: {json.dumps(ev)}\n\n".encode())
            lines.append(b"data: [DONE]\n\n")

            class _AsyncIter:
                def __init__(self, data: list[bytes]) -> None:
                    self._data = data
                    self._idx = 0

                def __aiter__(self) -> _AsyncIter:
                    return self

                async def __anext__(self) -> bytes:
                    if self._idx >= len(self._data):
                        raise StopAsyncIteration
                    item = self._data[self._idx]
                    self._idx += 1
                    return item

            return _AsyncIter(lines)

    class _MockSession:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

        def post(self, *args: Any, **kwargs: Any) -> _MockResponse:
            return _MockResponse()

    return _MockSession()


@pytest.fixture
def openai_compat_config() -> dict[str, Any]:
    """Valid OpenAI-compatible provider config."""
    return {
        "base_url": "https://api.example.com",
        "api_key": "sk-test-key",
        "api_path": "/v1/chat/completions",
        "timeout": 30,
        "retries": 1,
        "temperature": 0.2,
    }


@pytest.fixture
def anthropic_compat_config() -> dict[str, Any]:
    """Valid Anthropic-compatible provider config."""
    return {
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-ant-test",
        "api_path": "/v1/messages",
        "timeout": 30,
        "retries": 1,
        "temperature": 0.2,
        "max_tokens": 256,
    }


@pytest.fixture
def gemini_api_config() -> dict[str, Any]:
    """Valid Gemini API provider config."""
    return {
        "base_url": "https://generativelanguage.googleapis.com",
        "api_key": "gemini-test-key",
        "api_path": "/v1beta/models/{model}:generateContent",
        "timeout": 30,
        "retries": 1,
        "temperature": 0.7,
    }


@pytest.fixture
def ollama_config() -> dict[str, Any]:
    """Valid Ollama provider config."""
    return {
        "base_url": "http://localhost:11434",
        "api_path": "/api/chat",
        "timeout": 30,
    }


@pytest.fixture
def kimi_config() -> dict[str, Any]:
    """Valid Kimi provider config."""
    return {
        "base_url": "https://api.moonshot.cn/v1",
        "api_key": "kimi-test-key",
        "api_path": "/v1/chat/completions",
        "timeout": 30,
        "retries": 1,
        "temperature": 0.7,
        "max_tokens": 2048,
    }


@pytest.fixture
def codex_cli_config() -> dict[str, Any]:
    """Valid Codex CLI provider config."""
    return {
        "type": "codex_cli",
        "command": "codex",
        "cli_mode": "headless",
        "timeout": 60,
        "codex_exec": {
            "skip_git_repo_check": True,
            "json": True,
            "sandbox": "read-only",
        },
    }


@pytest.fixture
def codex_sdk_config() -> dict[str, Any]:
    """Valid Codex SDK provider config."""
    return {
        "type": "codex_sdk",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-codex-test-key",
        "timeout": 60,
        "max_retries": 3,
        "temperature": 0.2,
        "thinking_mode": True,
        "streaming": False,
        "sdk_params": {},
    }


@pytest.fixture
def gemini_cli_config() -> dict[str, Any]:
    """Valid Gemini CLI provider config."""
    return {
        "type": "gemini_cli",
        "command": "gemini",
        "args": ["chat", "--model", "{model}", "--prompt", "{prompt}"],
        "cli_mode": "headless",
        "env": {
            "GOOGLE_API_KEY": "gemini-test-key",
            "GOOGLE_GENAI_USE_VERTEXAI": "false",
        },
        "timeout": 60,
        "health_args": ["version"],
        "list_args": ["models", "list"],
    }


@pytest.fixture
def minimax_config() -> dict[str, Any]:
    """Valid MiniMax provider config."""
    return {
        "type": "minimax",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "minimax-test-key",
        "api_path": "/text/chatcompletion_v2",
        "timeout": 60,
        "retries": 3,
        "temperature": 0.7,
        "max_tokens": 2048,
    }
