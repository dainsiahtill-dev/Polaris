"""Tests for async provider helpers (AAA pattern).

Verifies:
    - async_invoke_with_retry with successful responses
    - async_invoke_with_retry with retryable errors
    - async_invoke_with_retry with circuit breaker
    - async_health_check_post with various status codes
    - AsyncStreamSession lifecycle
    - Context overflow self-heal
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_gate import (
    FinalProviderAttemptGate,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_inflight import (
    ProviderAttemptInFlightCoordinator,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle import (
    StrictProviderAttemptLifecycleStore,
)
from polaris.infrastructure.llm.providers import async_provider_helpers
from polaris.infrastructure.llm.providers.async_http_client import (
    AsyncCircuitBreaker,
    HttpResult,
)
from polaris.infrastructure.llm.providers.async_provider_adapter import _run_async
from polaris.infrastructure.llm.providers.async_provider_helpers import (
    AsyncStreamSession,
    _build_backoff_seconds,
    _shrink_max_tokens_for_context_overflow,
    async_health_check_post,
    async_invoke_with_retry,
)
from polaris.infrastructure.llm.providers.provider_helpers import (
    invoke_stream_with_retry,
    invoke_stream_with_retry_and_handler,
)
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository
from polaris.kernelone.llm.engine.contracts import (
    bind_physical_provider_dispatch_port,
    get_physical_provider_dispatch_port,
)
from polaris.kernelone.llm.types import HealthResult, Usage


class ClientResponseError(RuntimeError):
    """Retry-shaped aiohttp error without constructing a real response."""


class _StreamContent:
    def __init__(self, chunks: tuple[bytes | str, ...]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[bytes | str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes | str]:
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        chunks: tuple[bytes | str, ...] = (),
        json_body: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.ok = status < 400
        self.headers = {"Content-Type": "application/json" if json_body is not None else "text/event-stream"}
        self.content = _StreamContent(chunks)
        self._json_body = json_body

    async def text(self) -> str:
        return f"HTTP {self.status}"

    async def json(self) -> dict[str, Any]:
        assert self._json_body is not None
        return copy.deepcopy(self._json_body)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise ClientResponseError(str(self.status))


class _PostContext:
    def __init__(self, response: _FakeResponse, events: list[str]) -> None:
        self._response = response
        self._events = events

    async def __aenter__(self) -> _FakeResponse:
        self._events.append("post_enter")
        return self._response

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: object,
    ) -> None:
        self._events.append("response_exit")


class _FakeSession:
    def __init__(self, response: _FakeResponse, events: list[str]) -> None:
        self._response = response
        self._events = events
        self.closed = False
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _PostContext:
        self.posts.append({"url": url, **copy.deepcopy(kwargs)})
        return _PostContext(self._response, self._events)

    async def close(self) -> None:
        self.closed = True
        self._events.append("session_close")


class _PassthroughPhysicalStreamPort:
    """Test port that preserves the real session.post context boundary."""

    def __init__(self) -> None:
        self.wire_requests: list[dict[str, Any]] = []

    async def dispatch_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Awaitable[Any]],
    ) -> Any:
        return await send(copy.deepcopy(dict(wire_request)))

    async def dispatch_blocking_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Any],
    ) -> Any:
        return send(copy.deepcopy(dict(wire_request)))

    def dispatch_stream_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        open_stream: Callable[[Mapping[str, Any]], Any],
        consume: Callable[[Any], AsyncIterator[Any]],
    ) -> AsyncIterator[Any]:
        frozen = copy.deepcopy(dict(wire_request))
        self.wire_requests.append(frozen)

        async def _dispatch() -> AsyncIterator[Any]:
            async with open_stream(frozen) as response:
                async for item in consume(response):
                    yield item

        return _dispatch()


class _RecordingAsyncDispatchPort:
    def __init__(self) -> None:
        self.wire_requests: list[dict[str, Any]] = []

    async def dispatch_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Awaitable[Any]],
    ) -> Any:
        self.wire_requests.append(dict(wire_request))
        return await send(wire_request)

    async def dispatch_blocking_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Any],
    ) -> Any:
        self.wire_requests.append(dict(wire_request))
        return send(wire_request)

    def dispatch_stream_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        open_stream: Callable[[Mapping[str, Any]], Any],
        consume: Callable[[Any], AsyncIterator[Any]],
    ) -> AsyncIterator[Any]:
        async def _dispatch() -> AsyncIterator[Any]:
            async with open_stream(wire_request) as response:
                async for item in consume(response):
                    yield item

        return _dispatch()


class _SyncOnlyDispatchPort:
    def dispatch_sync(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Any],
    ) -> Any:
        return send(wire_request)


def _bootstrap_provider_attempt_facts(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("task_runtime.execution",),
            maintenance_reason="async_provider_helper_test",
        )
    )


def _governed_async_port(
    workspace: Path,
) -> tuple[FinalProviderAttemptGate, StrictProviderAttemptLifecycleStore]:
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(workspace),
        factory_run_id="factory-run-async-1",
    )
    port = FinalProviderAttemptGate.for_factory_run(
        workspace=str(workspace),
        factory_run_id="factory-run-async-1",
        run_id="run-async-1",
        role="director",
        turn_id="turn-async-1",
        call_id="call-async-1",
        request_freeze_id="freeze-async-1",
        provider="openai",
        model="model-async-1",
        semantic_request={
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "response_format": None,
            "semantic_options": {"temperature": 0.1},
        },
        lifecycle=lifecycle,
        drain_coordinator=ProviderAttemptInFlightCoordinator.for_factory_run("factory-run-async-1"),
    )
    return port, lifecycle


def _terminal_statuses(lifecycle: StrictProviderAttemptLifecycleStore) -> list[str]:
    return [
        str(item["payload"]["status"])
        for item in lifecycle.query_strict()
        if item["event_type"] == "provider_attempt.terminal"
    ]


# =============================================================================
# Utility function tests
# =============================================================================


class TestBuildBackoffSeconds:
    """Tests for the backoff calculation utility."""

    def test_first_attempt_returns_base_delay(self) -> None:
        # Arrange & Act
        delay = _build_backoff_seconds(attempt=1, base_delay_seconds=0.5, max_delay_seconds=30.0)

        # Assert
        assert 0.5 <= delay <= 0.55  # base + small jitter

    def test_exponential_growth(self) -> None:
        # Arrange & Act
        delay1 = _build_backoff_seconds(attempt=1, base_delay_seconds=1.0, max_delay_seconds=30.0)
        delay2 = _build_backoff_seconds(attempt=2, base_delay_seconds=1.0, max_delay_seconds=30.0)
        delay3 = _build_backoff_seconds(attempt=3, base_delay_seconds=1.0, max_delay_seconds=30.0)

        # Assert
        assert delay1 < delay2 < delay3

    def test_respects_max_delay(self) -> None:
        # Arrange & Act
        delay = _build_backoff_seconds(attempt=100, base_delay_seconds=1.0, max_delay_seconds=5.0)

        # Assert
        assert delay <= 5.5  # max + jitter


class TestShrinkMaxTokens:
    """Tests for the context overflow self-heal utility."""

    def test_shrinks_on_matching_error(self) -> None:
        # Arrange
        payload: dict[str, object] = {"max_tokens": 8000}
        error = "This model's maximum context length is 8192 tokens. However, your messages resulted in 8000 tokens. Please reduce the length of the messages by 1000."

        # Act
        result = _shrink_max_tokens_for_context_overflow(payload, error)  # type: ignore[arg-type]

        # Assert
        assert result is True
        assert isinstance(payload["max_tokens"], int)
        assert payload["max_tokens"] < 8000

    def test_no_shrink_on_unmatched_error(self) -> None:
        # Arrange
        payload: dict[str, object] = {"max_tokens": 4000}
        error = "Some other error"

        # Act
        result = _shrink_max_tokens_for_context_overflow(payload, error)  # type: ignore[arg-type]

        # Assert
        assert result is False
        assert payload["max_tokens"] == 4000

    def test_no_shrink_when_already_small(self) -> None:
        # Arrange
        payload: dict[str, object] = {"max_tokens": 50}
        error = "maximum context length is 8192. messages resulted in 8000. reduce by 1000."

        # Act
        result = _shrink_max_tokens_for_context_overflow(payload, error)  # type: ignore[arg-type]

        # Assert
        assert result is False


# =============================================================================
# async_invoke_with_retry tests
# =============================================================================


class TestAsyncInvokeWithRetry:
    """Tests for the async invoke with retry function."""

    @pytest.mark.asyncio
    async def test_explicit_async_dispatch_port_wins_over_context_binding(self) -> None:
        bound = _RecordingAsyncDispatchPort()
        explicit = _RecordingAsyncDispatchPort()
        response = HttpResult(
            status_code=200,
            headers={},
            text='{"choices": [{"message": {"content": "ok"}}]}',
            elapsed_ms=1,
        )

        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(return_value=response)
            mock_client_cls.return_value = mock_client

            with bind_physical_provider_dispatch_port(bound):
                result = await async_invoke_with_retry(
                    url="https://example.test/invoke",
                    headers={},
                    payload={"messages": []},
                    timeout=5,
                    retries=0,
                    prompt="prompt",
                    extract_output=lambda data: str(data["choices"][0]["message"]["content"]),
                    usage_from_response=lambda _prompt, _output, _data: Usage(),
                    physical_dispatch_port=explicit,
                )

        assert result.ok is True
        assert len(explicit.wire_requests) == 1
        assert bound.wire_requests == []

    @pytest.mark.asyncio
    async def test_bound_async_dispatch_port_wraps_each_physical_retry(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        port = _RecordingAsyncDispatchPort()
        responses = (
            HttpResult(status_code=200, headers={}, text="not-json", elapsed_ms=1),
            HttpResult(
                status_code=200,
                headers={},
                text='{"choices": [{"message": {"content": "recovered"}}]}',
                elapsed_ms=1,
            ),
        )

        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(side_effect=responses)
            mock_client_cls.return_value = mock_client
            monkeypatch.setattr(async_provider_helpers.asyncio, "sleep", AsyncMock())

            with bind_physical_provider_dispatch_port(port):
                result = await async_invoke_with_retry(
                    url="https://example.test/invoke",
                    headers={},
                    payload={"messages": []},
                    timeout=5,
                    retries=1,
                    prompt="prompt",
                    extract_output=lambda data: str(data["choices"][0]["message"]["content"]),
                    usage_from_response=lambda _prompt, _output, _data: Usage(),
                )

        assert result.ok is True
        assert len(port.wire_requests) == 2

    @pytest.mark.asyncio
    async def test_bound_sync_only_port_fails_closed_before_raw_async_post(self) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client._post_json_impl = AsyncMock()

        with (
            patch(
                "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient",
                return_value=mock_client,
            ),
            bind_physical_provider_dispatch_port(_SyncOnlyDispatchPort()),
            pytest.raises(RuntimeError, match="dispatch_async"),
        ):
            await async_invoke_with_retry(
                url="https://example.test/invoke",
                headers={},
                payload={"messages": []},
                timeout=5,
                retries=0,
                prompt="prompt",
                extract_output=lambda data: str(data),
                usage_from_response=lambda _prompt, _output, _data: Usage(),
            )

        mock_client._post_json_impl.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_governed_success_uses_real_gate_and_records_completed_terminal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _bootstrap_provider_attempt_facts(tmp_path)
        port, lifecycle = _governed_async_port(tmp_path)
        counts = {"post": 0, "parse": 0, "extract": 0, "finalize": 0, "usage": 0}
        mock_result = HttpResult(
            status_code=200,
            headers={},
            text='{"choices": [{"message": {"content": "Hello"}}]}',
            elapsed_ms=10,
        )
        original_parse = async_provider_helpers._parse_json
        original_finalize = async_provider_helpers.LLMResponseParser.finalize_response

        class _Clock:
            current = 0.0

            def time(self) -> float:
                return self.current

            def sleep(self, seconds: float) -> None:
                self.current += seconds

            def advance(self, seconds: float) -> None:
                self.current += seconds

        clock = _Clock()

        def _parse(text: str) -> dict[str, Any]:
            counts["parse"] += 1
            clock.advance(1.0)
            return original_parse(text)

        def _extract(data: dict[str, Any]) -> str:
            counts["extract"] += 1
            clock.advance(10.0)
            return str(data["choices"][0]["message"]["content"])

        def _finalize(
            _cls: type[Any],
            body: Any,
            *,
            visible_text: str | None = None,
        ) -> Any:
            counts["finalize"] += 1
            clock.advance(100.0)
            return original_finalize(body, visible_text=visible_text)

        def _usage(prompt: str, output: str, data: dict[str, Any]) -> Usage:
            del prompt, output, data
            counts["usage"] += 1
            clock.advance(1000.0)
            return Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2)

        async def _post(*_args: object, **_kwargs: object) -> HttpResult:
            counts["post"] += 1
            return mock_result

        monkeypatch.setattr(async_provider_helpers, "_parse_json", _parse)
        monkeypatch.setattr(
            async_provider_helpers.LLMResponseParser,
            "finalize_response",
            classmethod(_finalize),
        )

        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(side_effect=_post)
            mock_client_cls.return_value = mock_client

            result = await async_invoke_with_retry(
                url="https://example.test/v1/chat/completions",
                headers={"Authorization": "Bearer secret"},
                payload={
                    "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                    "tools": [],
                    "tool_choice": "auto",
                    "temperature": 0.1,
                },
                timeout=30,
                retries=0,
                prompt="Hello",
                extract_output=_extract,
                usage_from_response=_usage,
                clock=clock,
                physical_dispatch_port=port,
            )

        assert result.ok is True
        assert result.output == "Hello"
        assert result.latency_ms == 1000
        assert mock_client._post_json_impl.await_count == 1
        assert counts == {"post": 1, "parse": 1, "extract": 1, "finalize": 1, "usage": 1}
        dispatched = mock_client._post_json_impl.await_args
        assert dispatched.args == (
            "https://example.test/v1/chat/completions",
            {"Authorization": "Bearer secret"},
            {
                "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                "tools": [],
                "tool_choice": "auto",
                "temperature": 0.1,
            },
        )
        assert dispatched.kwargs == {"timeout": 30.0}
        facts = lifecycle.query_strict()
        assert _terminal_statuses(lifecycle) == ["completed"]
        start_fact = facts[0]
        assert start_fact["event_type"] == "provider_attempt.started"
        context_ref = str(start_fact["payload"]["context_snapshot_ref"])
        assert len(context_ref) == 24
        pins = ContextSnapshotAuditPinRepository(workspace=str(tmp_path)).query_snapshot_pins(context_ref)
        assert len(pins) == 1

    @pytest.mark.asyncio
    async def test_governed_json_parse_retry_records_failed_then_completed_unique_attempts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _bootstrap_provider_attempt_facts(tmp_path)
        port, lifecycle = _governed_async_port(tmp_path)
        responses = (
            HttpResult(status_code=200, headers={}, text="not-json", elapsed_ms=5),
            HttpResult(
                status_code=200,
                headers={},
                text='{"choices": [{"message": {"content": "recovered"}}]}',
                elapsed_ms=5,
            ),
        )

        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(side_effect=responses)
            mock_client_cls.return_value = mock_client
            monkeypatch.setattr(async_provider_helpers.asyncio, "sleep", AsyncMock())

            result = await async_invoke_with_retry(
                url="https://example.test/v1/chat/completions",
                headers={},
                payload={
                    "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                    "tools": [],
                    "tool_choice": "auto",
                    "temperature": 0.1,
                    "max_tokens": 128,
                },
                timeout=30,
                retries=1,
                prompt="prompt",
                extract_output=lambda data: str(data["choices"][0]["message"]["content"]),
                usage_from_response=lambda prompt, output, data: Usage(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                ),
                physical_dispatch_port=port,
            )

        assert result.ok is True
        assert result.output == "recovered"
        assert mock_client._post_json_impl.await_count == 2
        facts = lifecycle.query_strict()
        starts = tuple(item for item in facts if item["event_type"] == "provider_attempt.started")
        terminals = tuple(item for item in facts if item["event_type"] == "provider_attempt.terminal")
        assert _terminal_statuses(lifecycle) == ["failed", "completed"]
        assert [item["payload"]["attempt_number"] for item in starts] == [1, 2]
        request_ids = [item["payload"]["provider_request_id"] for item in starts]
        assert len(set(request_ids)) == 2
        assert request_ids == [item["payload"]["provider_request_id"] for item in terminals]
        repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
        assert all(repository.query_snapshot_pins(str(item["payload"]["context_snapshot_ref"])) for item in starts)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure_stage", "error_type"),
        (("extract", KeyError), ("finalize", ValueError), ("usage", TypeError)),
    )
    async def test_governed_callback_failure_records_failed_terminal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        failure_stage: str,
        error_type: type[Exception],
    ) -> None:
        _bootstrap_provider_attempt_facts(tmp_path)
        port, lifecycle = _governed_async_port(tmp_path)
        response = HttpResult(
            status_code=200,
            headers={},
            text='{"choices": [{"message": {"content": "ok"}}]}',
            elapsed_ms=5,
        )

        def _extract(data: dict[str, Any]) -> str:
            if failure_stage == "extract":
                raise error_type("extract failed")
            return str(data["choices"][0]["message"]["content"])

        def _usage(prompt: str, output: str, data: dict[str, Any]) -> Usage:
            del prompt, output, data
            if failure_stage == "usage":
                raise error_type("usage failed")
            return Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2)

        if failure_stage == "finalize":

            def _fail_finalize(
                _cls: type[Any],
                _data: Any,
                *,
                visible_text: str | None = None,
            ) -> Any:
                del visible_text
                raise error_type("finalize failed")

            monkeypatch.setattr(
                async_provider_helpers.LLMResponseParser,
                "finalize_response",
                classmethod(_fail_finalize),
            )

        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(return_value=response)
            mock_client_cls.return_value = mock_client

            result = await async_invoke_with_retry(
                url="https://example.test/v1/chat/completions",
                headers={},
                payload={
                    "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                    "tools": [],
                    "tool_choice": "auto",
                    "temperature": 0.1,
                },
                timeout=30,
                retries=0,
                prompt="prompt",
                extract_output=_extract,
                usage_from_response=_usage,
                physical_dispatch_port=port,
            )

        assert result.ok is False
        assert failure_stage in str(result.error)
        assert mock_client._post_json_impl.await_count == 1
        assert _terminal_statuses(lifecycle) == ["failed"]

    @pytest.mark.asyncio
    async def test_governed_semantic_failure_is_completed_without_retry_and_preserves_evidence(
        self,
        tmp_path: Path,
    ) -> None:
        _bootstrap_provider_attempt_facts(tmp_path)
        port, lifecycle = _governed_async_port(tmp_path)
        raw = {
            "choices": [
                {
                    "message": {"content": None, "reasoning_content": "unfinished reasoning"},
                    "finish_reason": "length",
                }
            ]
        }
        response = HttpResult(status_code=200, headers={}, text=json.dumps(raw), elapsed_ms=5)

        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(return_value=response)
            mock_client_cls.return_value = mock_client

            result = await async_invoke_with_retry(
                url="https://example.test/v1/chat/completions",
                headers={},
                payload={
                    "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                    "tools": [],
                    "tool_choice": "auto",
                    "temperature": 0.1,
                },
                timeout=30,
                retries=3,
                prompt="prompt",
                extract_output=lambda data: "",
                usage_from_response=lambda prompt, output, data: Usage(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                ),
                physical_dispatch_port=port,
            )

        assert result.ok is False
        assert "reasoning truncated" in str(result.error)
        assert result.thinking == "unfinished reasoning"
        assert result.raw == raw
        assert mock_client._post_json_impl.await_count == 1
        assert _terminal_statuses(lifecycle) == ["completed"]

    @pytest.mark.asyncio
    async def test_governed_non_success_reuses_identical_http_result_outside_gate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _bootstrap_provider_attempt_facts(tmp_path)
        port, lifecycle = _governed_async_port(tmp_path)
        sentinel = HttpResult(status_code=401, headers={}, text="unauthorized", elapsed_ms=5)
        seen: list[HttpResult] = []
        original_raise_for_status = HttpResult.raise_for_status

        def _raise_for_status(self: HttpResult) -> None:
            assert self is sentinel
            seen.append(self)
            original_raise_for_status(self)

        monkeypatch.setattr(HttpResult, "raise_for_status", _raise_for_status)
        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(return_value=sentinel)
            mock_client_cls.return_value = mock_client

            result = await async_invoke_with_retry(
                url="https://example.test/v1/chat/completions",
                headers={},
                payload={
                    "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                    "tools": [],
                    "tool_choice": "auto",
                    "temperature": 0.1,
                },
                timeout=30,
                retries=0,
                prompt="prompt",
                extract_output=lambda data: "",
                usage_from_response=lambda prompt, output, data: Usage(),
                physical_dispatch_port=port,
            )

        assert result.ok is False
        assert seen == [sentinel]
        assert mock_client._post_json_impl.await_count == 1
        assert _terminal_statuses(lifecycle) == ["failed"]

    @pytest.mark.asyncio
    async def test_governed_pin_failure_blocks_async_http_post(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _bootstrap_provider_attempt_facts(tmp_path)
        port, lifecycle = _governed_async_port(tmp_path)
        original_verify = ContextSnapshotAuditPinRepository._fsync_and_verify

        def _fail_pin_verify(self: ContextSnapshotAuditPinRepository, path: str, expected: bytes) -> None:
            if "/pins/" in path.replace("\\", "/"):
                raise OSError("pin fsync failed")
            original_verify(self, path, expected)

        monkeypatch.setattr(ContextSnapshotAuditPinRepository, "_fsync_and_verify", _fail_pin_verify)
        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock()
            mock_client_cls.return_value = mock_client

            with pytest.raises(OSError, match="pin fsync failed"):
                await async_invoke_with_retry(
                    url="https://example.test/v1/chat/completions",
                    headers={},
                    payload={
                        "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                        "tools": [],
                        "tool_choice": "auto",
                        "temperature": 0.1,
                    },
                    timeout=30,
                    retries=0,
                    prompt="prompt",
                    extract_output=lambda data: "",
                    usage_from_response=lambda prompt, output, data: Usage(),
                    physical_dispatch_port=port,
                )

        assert mock_client._post_json_impl.await_count == 0
        assert lifecycle.query_strict() == ()

    @pytest.mark.asyncio
    async def test_successful_invoke(self) -> None:
        # Arrange
        mock_result = HttpResult(
            status_code=200,
            headers={},
            text='{"choices": [{"message": {"content": "Hello"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}',
            elapsed_ms=100,
        )

        def extract_output(data: dict[str, object]) -> str:
            choices = data.get("choices", [])
            if choices and isinstance(choices, list):
                msg = choices[0].get("message", {})
                if isinstance(msg, dict):
                    return str(msg.get("content", ""))
            return ""

        def usage_from_response(prompt: str, output: str, data: dict[str, object]) -> Usage:
            return Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(return_value=mock_result)
            mock_client_cls.return_value = mock_client

            result = await async_invoke_with_retry(
                url="http://test/api",
                headers={"Content-Type": "application/json"},
                payload={"prompt": "Hello", "max_tokens": 100},
                timeout=30,
                retries=3,
                prompt="Hello",
                extract_output=extract_output,
                usage_from_response=usage_from_response,
            )

        # Assert
        assert result.ok is True
        assert result.output == "Hello"
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_circuit_open_returns_error(self) -> None:
        # Arrange
        cb = AsyncCircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60.0)
        await cb.on_failure()  # Open the circuit

        def extract_output(data: dict[str, object]) -> str:
            return ""

        def usage_from_response(prompt: str, output: str, data: dict[str, object]) -> Usage:
            return Usage()

        # Act
        result = await async_invoke_with_retry(
            url="http://test/api",
            headers={},
            payload={},
            timeout=30,
            retries=3,
            prompt="test",
            extract_output=extract_output,
            usage_from_response=usage_from_response,
            circuit_breaker=cb,
        )

        # Assert
        assert result.ok is False
        assert result.error is not None
        assert "circuit_open" in result.error

    @pytest.mark.asyncio
    async def test_retry_on_server_error(self) -> None:
        # Arrange
        error_result = HttpResult(status_code=500, headers={}, text="Internal Server Error", elapsed_ms=50)

        def extract_output(data: dict[str, object]) -> str:
            return ""

        def usage_from_response(prompt: str, output: str, data: dict[str, object]) -> Usage:
            return Usage()

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(return_value=error_result)
            mock_client_cls.return_value = mock_client

            result = await async_invoke_with_retry(
                url="http://test/api",
                headers={},
                payload={"prompt": "test"},
                timeout=30,
                retries=0,  # No retries for this test
                prompt="test",
                extract_output=extract_output,
                usage_from_response=usage_from_response,
            )

        # Assert
        assert result.ok is False
        assert result.error is not None
        assert "500" in result.error


# =============================================================================
# async_health_check_post tests
# =============================================================================


class TestAsyncHealthCheckPost:
    """Tests for the async health check function."""

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        # Arrange

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.health_check = AsyncMock(return_value=HealthResult(ok=True, latency_ms=42))
            mock_client_cls.return_value = mock_client

            result = await async_health_check_post(
                url="http://test/api",
                headers={},
                payload={"prompt": "test"},
                timeout=30,
            )

        # Assert
        assert result.ok is True
        assert result.latency_ms == 42


# =============================================================================
# AsyncStreamSession tests
# =============================================================================


def test_async_provider_adapter_direct_run_preserves_dispatch_context() -> None:
    port = _RecordingAsyncDispatchPort()

    async def _observe() -> object | None:
        return get_physical_provider_dispatch_port()

    with bind_physical_provider_dispatch_port(port):
        observed = _run_async(_observe())

    assert observed is port


@pytest.mark.asyncio
async def test_async_provider_adapter_bridge_pool_preserves_dispatch_context() -> None:
    port = _RecordingAsyncDispatchPort()

    async def _observe() -> object | None:
        return get_physical_provider_dispatch_port()

    with bind_physical_provider_dispatch_port(port):
        observed = _run_async(_observe())

    assert observed is port


class TestAsyncStreamSession:
    """Tests for the async stream session."""

    @pytest.mark.asyncio
    async def test_context_manager_lifecycle(self) -> None:
        # Arrange
        session = AsyncStreamSession()

        # Act & Assert
        async with session:
            assert session._client is not None

        assert session._client is None

    @pytest.mark.asyncio
    async def test_iter_lines_before_start_raises(self) -> None:
        # Arrange
        session = AsyncStreamSession()

        # Act & Assert
        async with session:
            with pytest.raises(RuntimeError, match="Stream not started"):
                async for _ in session.aiter_lines():
                    pass


@pytest.mark.asyncio
async def test_governed_stream_success_crosses_post_context_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    session = _FakeSession(
        _FakeResponse(chunks=(b'data: {"kind":"delta"}\n\n', b"data: [DONE]\n\n")),
        events,
    )
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        AsyncMock(return_value=session),
    )
    port = _PassthroughPhysicalStreamPort()

    result = [
        item
        async for item in invoke_stream_with_retry(
            "https://example.test/stream",
            {"Authorization": "Bearer secret"},
            {"messages": [], "stream": True},
            5,
            max_attempts=1,
            governance_mode="governed_required",
            physical_dispatch_port=port,
        )
    ]

    assert result == [{"kind": "delta"}]
    assert len(port.wire_requests) == 1
    assert len(session.posts) == 1
    assert events == ["post_enter", "response_exit", "session_close"]


@pytest.mark.asyncio
async def test_governed_stream_retries_each_failed_post_through_separate_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    sessions = [
        _FakeSession(_FakeResponse(status=500), events),
        _FakeSession(_FakeResponse(status=503), events),
        _FakeSession(_FakeResponse(json_body={"ok": True}), events),
    ]
    create_session = AsyncMock(side_effect=sessions)
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        create_session,
    )
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers.asyncio.sleep",
        AsyncMock(),
    )
    port = _PassthroughPhysicalStreamPort()

    result = [
        item
        async for item in invoke_stream_with_retry(
            "https://example.test/stream",
            {},
            {"messages": [], "max_tokens": 300},
            5,
            max_attempts=3,
            retry_delay_seconds=0,
            governance_mode="governed_required",
            physical_dispatch_port=port,
        )
    ]

    assert result == [{"ok": True}]
    assert len(port.wire_requests) == 3
    assert [request["body"]["max_tokens"] for request in port.wire_requests] == [300, 300, 300]
    assert all(session.closed for session in sessions)
    assert events == [
        "post_enter",
        "response_exit",
        "session_close",
        "post_enter",
        "response_exit",
        "session_close",
        "post_enter",
        "response_exit",
        "session_close",
    ]


@pytest.mark.asyncio
async def test_governed_handler_stream_uses_same_physical_post_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    session = _FakeSession(_FakeResponse(chunks=(b"handler",)), events)
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        AsyncMock(return_value=session),
    )
    port = _PassthroughPhysicalStreamPort()

    async def _handler(response: _FakeResponse) -> AsyncGenerator[str, None]:
        async for chunk in response.content:
            yield str(chunk, "utf-8") if isinstance(chunk, bytes) else chunk

    result = [
        item
        async for item in invoke_stream_with_retry_and_handler(
            "https://example.test/custom-stream",
            {},
            {"messages": []},
            5,
            _handler,  # type: ignore[arg-type]
            max_attempts=1,
            governance_mode="governed_required",
            physical_dispatch_port=port,
        )
    ]

    assert result == ["handler"]
    assert len(port.wire_requests) == len(session.posts) == 1
    assert events == ["post_enter", "response_exit", "session_close"]


@pytest.mark.asyncio
@pytest.mark.parametrize("use_handler", [False, True])
async def test_governed_stream_missing_physical_port_fails_before_session_or_post(
    monkeypatch: pytest.MonkeyPatch,
    use_handler: bool,
) -> None:
    create_session = AsyncMock(side_effect=AssertionError("session must not be created"))
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        create_session,
    )

    if use_handler:

        async def _handler(_response: object) -> AsyncGenerator[object, None]:
            if False:
                yield None

        stream = invoke_stream_with_retry_and_handler(
            "https://example.test/custom-stream",
            {},
            {"messages": []},
            5,
            _handler,  # type: ignore[arg-type]
            governance_mode="governed_required",
            physical_dispatch_port=None,
        )
    else:
        stream = invoke_stream_with_retry(
            "https://example.test/stream",
            {},
            {"messages": []},
            5,
            governance_mode="governed_required",
            physical_dispatch_port=None,
        )

    with pytest.raises(RuntimeError, match="governed async stream requires a physical dispatch port"):
        await anext(stream)
    create_session.assert_not_awaited()
