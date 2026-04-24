"""Shared fixtures for E2E integration tests.

Provides reusable mocks, workspaces, and helpers for testing
roles/kernel, roles/runtime, and context/ modules.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Workspace Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace(prefix: str = "e2e-") -> str:
    """Create a temporary workspace directory for tests.

    Automatically cleaned up after test completes.
    """
    root = Path(tempfile.gettempdir()) / "polaris_e2e_tests"
    root.mkdir(parents=True, exist_ok=True)
    workspace = tempfile.mkdtemp(prefix=prefix, dir=str(root))
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture
def polaris_workspace(tmp_workspace: str) -> Path:
    """Create a workspace with .polaris/ checkpoint directory structure."""
    workspace = Path(tmp_workspace)
    checkpoint_dir = workspace / ".polaris" / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    return workspace


# ---------------------------------------------------------------------------
# Mock LLM Provider
# ---------------------------------------------------------------------------


class MockLLMProvider:
    """Mock LLM provider that returns configurable responses."""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self._response = response or {
            "choices": [
                {
                    "message": {
                        "content": "This is a mock LLM response.",
                        "role": "assistant",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        self.call_count = 0
        self.last_request: dict[str, Any] | None = None

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.call_count += 1
        self.last_request = kwargs
        return self._response


class MockLLMStreamProvider:
    """Mock streaming LLM provider."""

    def __init__(self, chunks: list[str] | None = None) -> None:
        self._chunks = chunks or ["This ", "is ", "a ", "mock ", "response.", ""]
        self.call_count = 0

    async def __call__(self, **kwargs: Any) -> Any:
        self.call_count += 1
        return self._stream_chunks()

    async def _stream_chunks(self) -> Any:
        """Yield chunks as an async generator."""
        for i, chunk_text in enumerate(self._chunks):
            yield {
                "choices": [
                    {
                        "delta": {"content": chunk_text},
                        "finish_reason": None if i < len(self._chunks) - 1 else "stop",
                    }
                ]
            }


@pytest.fixture
def mock_llm_provider() -> MockLLMProvider:
    """Return a MockLLMProvider with default response."""
    return MockLLMProvider()


@pytest.fixture
def mock_llm_stream_provider() -> MockLLMStreamProvider:
    """Return a MockLLMStreamProvider."""
    return MockLLMStreamProvider()


# ---------------------------------------------------------------------------
# Mock Tool Runtime
# ---------------------------------------------------------------------------


def make_success_tool_result(tool_name: str, result: dict[str, Any] | str) -> dict[str, Any]:
    """Create a successful tool result dict."""
    return {
        "tool_call_id": f"call_{tool_name}_001",
        "tool_name": tool_name,
        "status": "success",
        "result": result if isinstance(result, dict) else {"content": str(result)},
        "arguments": {},
    }


def make_error_tool_result(tool_name: str, error: str) -> dict[str, Any]:
    """Create an error tool result dict."""
    return {
        "tool_call_id": f"call_{tool_name}_001",
        "tool_name": tool_name,
        "status": "error",
        "error": error,
        "arguments": {},
    }


class MockToolRuntime:
    """Mock tool runtime that returns configurable results."""

    def __init__(
        self,
        results: list[dict[str, Any]] | None = None,
        raise_on_call: type[Exception] | None = None,
    ) -> None:
        self._results = results or []
        self._raise_on_call = raise_on_call
        self.call_count = 0
        self.last_context: list[dict[str, Any]] | None = None
        self.last_tool_definitions: list[dict[str, Any]] | None = None

    async def __call__(
        self,
        context: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.call_count += 1
        self.last_context = context
        self.last_tool_definitions = tool_definitions

        if self._raise_on_call:
            raise self._raise_on_call("Mock tool runtime error")

        if self._results:
            return {"results": self._results}
        return {"results": []}


@pytest.fixture
def mock_tool_runtime() -> MockToolRuntime:
    """Return a MockToolRuntime with default empty results."""
    return MockToolRuntime()


@pytest.fixture
def mock_tool_runtime_with_read() -> MockToolRuntime:
    """Return a MockToolRuntime that simulates a successful read_file."""
    return MockToolRuntime(
        results=[
            make_success_tool_result(
                "read_file",
                {"content": "class Example:\n    pass\n", "file": "example.py"},
            )
        ]
    )


@pytest.fixture
def mock_tool_runtime_with_write() -> MockToolRuntime:
    """Return a MockToolRuntime that simulates a successful write_file."""
    return MockToolRuntime(
        results=[
            make_success_tool_result(
                "write_file",
                {"file": "output.py", "effect_receipt": {"file": "output.py"}},
            )
        ]
    )


@pytest.fixture
def mock_tool_runtime_read_then_write() -> MockToolRuntime:
    """Return a MockToolRuntime that simulates read followed by write."""
    return MockToolRuntime(
        results=[
            make_success_tool_result(
                "read_file",
                {"content": "original content", "file": "example.py"},
            ),
            make_success_tool_result(
                "write_file",
                {"file": "example.py", "effect_receipt": {"file": "example.py"}},
            ),
        ]
    )


@pytest.fixture
def mock_tool_runtime_error() -> MockToolRuntime:
    """Return a MockToolRuntime that raises an error."""
    return MockToolRuntime(raise_on_call=RuntimeError)


# ---------------------------------------------------------------------------
# Mock Tool Definitions
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_tool_definitions() -> list[dict[str, Any]]:
    """Return minimal tool definitions for testing."""
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Execute a shell command",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
    ]


@pytest.fixture
def mock_context() -> list[dict[str, Any]]:
    """Return a minimal conversation context."""
    return [{"role": "user", "content": "Hello, assistant."}]


# ---------------------------------------------------------------------------
# Session State Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def make_turn_outcome_envelope():
    """Factory for creating TurnOutcomeEnvelope mocks."""
    from polaris.cells.roles.kernel.public.turn_contracts import (
        TurnContinuationMode,
        TurnOutcomeEnvelope,
        TurnResult,
    )

    def _make(
        turn_id: str = "turn-0",
        kind: str = "final_answer",
        visible_content: str = "Done.",
        continuation_mode: str = "END_SESSION",
        batch_receipt: dict[str, Any] | None = None,
        session_patch: dict[str, Any] | None = None,
        next_intent: str | None = None,
        turn_kind: str = "final_answer",
    ) -> TurnOutcomeEnvelope:
        result = TurnResult(
            turn_id=turn_id,  # type: ignore[arg-type]
            kind=kind,  # type: ignore[arg-type]
            visible_content=visible_content,
            decision={},
            batch_receipt=batch_receipt or {},
        )
        mode = TurnContinuationMode(continuation_mode)
        return TurnOutcomeEnvelope(
            turn_result=result,
            continuation_mode=mode,
            next_intent=next_intent,
            session_patch=session_patch or {},
            artifacts_to_persist=[],
            speculative_hints={},
        )

    return _make


# ---------------------------------------------------------------------------
# Content Store Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def content_store_workspace(tmp_workspace: str) -> str:
    """Return a workspace path for ContentStore tests."""
    return tmp_workspace


# ---------------------------------------------------------------------------
# Async Event Loop Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def run_async():
    """Helper to run async code in sync tests."""
    import asyncio

    def _run(coro):
        return asyncio.run(coro)

    return _run
