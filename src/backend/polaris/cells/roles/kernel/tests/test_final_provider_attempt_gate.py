from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
)
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptLiveControlPort,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_gate import (
    DurableFinalProviderAttemptSnapshotStore,
    FinalProviderAttemptGate,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_inflight import (
    ProviderAttemptDrainError,
    ProviderAttemptInFlightCoordinator,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle import (
    StrictProviderAttemptLifecycleStore,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.infrastructure.llm.providers import provider_helpers
from polaris.infrastructure.llm.providers.provider_helpers import (
    invoke_stream_with_retry,
    invoke_stream_with_retry_and_handler,
    invoke_with_retry,
)
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository
from polaris.kernelone.llm.engine.contracts import FrozenFinalProviderAttemptV1
from polaris.kernelone.llm.types import Usage


class _Response:
    def __init__(self, *, status_code: int = 200, text: str = "", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "ok"}}]}


class ClientResponseError(RuntimeError):
    """Retry-shaped aiohttp response error for physical stream tests."""


class _AsyncStreamContent:
    def __init__(self, chunks: tuple[bytes | str, ...]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[bytes | str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes | str]:
        for chunk in self._chunks:
            yield chunk


class _AsyncResponse:
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
        self.content = _AsyncStreamContent(chunks)
        self._json_body = json_body

    async def text(self) -> str:
        return f"HTTP {self.status}"

    async def json(self) -> dict[str, Any]:
        assert self._json_body is not None
        return dict(self._json_body)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise ClientResponseError(str(self.status))


class _AsyncPostContext:
    def __init__(self, response: _AsyncResponse, events: list[str]) -> None:
        self._response = response
        self._events = events

    async def __aenter__(self) -> _AsyncResponse:
        self._events.append("post_enter")
        return self._response

    async def __aexit__(self, *_args: object) -> None:
        self._events.append("response_exit")


class _AsyncSession:
    def __init__(self, response: _AsyncResponse, events: list[str]) -> None:
        self._response = response
        self._events = events
        self.closed = False
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _AsyncPostContext:
        self.posts.append({"url": url, **kwargs})
        return _AsyncPostContext(self._response, self._events)

    async def close(self) -> None:
        self.closed = True
        self._events.append("session_close")


def _bootstrap(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("task_runtime.execution",),
            maintenance_reason="final_provider_attempt_test",
        )
    )


def _gate(
    workspace: Path,
    *,
    snapshot_store: object | None = None,
    semantic_options: dict[str, Any] | None = None,
) -> tuple[FinalProviderAttemptGate, StrictProviderAttemptLifecycleStore]:
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(workspace),
        factory_run_id="factory-run-1",
    )
    physical_attempt_control_port = FactoryPhysicalAttemptLiveControlPort(factory_run_id="factory-run-1")
    physical_attempt_control_port.register_grant(
        FactoryPhysicalAttemptGrantViewV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
            verification_scope="factory",
            factory_run_id="factory-run-1",
            role="director",
            stage="director_dispatch",
            workspace_fencing_token=1,
            stage_claim_attempt=1,
            stage_claim_nonce="stage-nonce-1",
            execution_authority_hash="f" * 64,
            attempt_budget=32,
        )
    )
    gate = FinalProviderAttemptGate(
        workspace=str(workspace),
        verification_scope="factory",
        factory_run_id="factory-run-1",
        run_id="run-1",
        role="director",
        turn_id="turn-1",
        call_id="call-1",
        request_freeze_id="freeze-1",
        provider="openai",
        model="model-1",
        semantic_request={
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "response_format": None,
            "semantic_options": semantic_options or {"temperature": 0.1},
        },
        physical_attempt_control_port=physical_attempt_control_port,
        execution_authority_hash="f" * 64,
        attempt_budget=32,
        lifecycle=lifecycle,
        snapshot_store=snapshot_store or DurableFinalProviderAttemptSnapshotStore(str(workspace)),
    )
    return gate, lifecycle


def test_factory_gate_conserves_reserve_start_send_terminal_under_one_authority(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, _lifecycle = _gate(tmp_path)
    sends: list[Mapping[str, Any]] = []

    result = gate.dispatch_sync(
        wire_request=_wire_request(),
        send=lambda frozen: sends.append(frozen) or "ok",
    )

    assert result == "ok"
    assert len(sends) == 1
    state = gate._physical_attempt_control_port.budget_state("f" * 64)
    assert state.reserved_count == 0
    assert state.committed_count == 1
    assert state.terminal_count == 1
    assert state.consumed_attempts == 1
    assert state.settled is True


def test_factory_gate_uses_injected_physical_control_port_as_only_drain_state(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, _lifecycle = _gate(tmp_path)

    assert gate.drain_coordinator is gate._physical_attempt_control_port


def _wire_request() -> dict[str, Any]:
    return {
        "body": {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "response_format": None,
            "temperature": 0.1,
        }
    }


def test_governed_physical_http_attempt_persists_snapshot_pin_start_and_terminal_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    physical_calls: list[dict[str, Any]] = []

    def _post(*_args: object, **kwargs: Any) -> _Response:
        physical_calls.append(dict(kwargs))
        return _Response()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {"Authorization": "Bearer secret"},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.1,
        },
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )
    assert result.ok is True
    assert len(physical_calls) == 1
    facts = lifecycle.query_strict()
    assert [item["event_type"] for item in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    assert facts[0]["payload"]["provider_request_id"] == facts[1]["payload"]["provider_request_id"]
    assert facts[1]["payload"]["status"] == "completed"
    assert len(facts[0]["payload"]["context_snapshot_ref"]) == 24
    assert facts[0]["payload"]["pin_hash"]
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    pins = repository.query_snapshot_pins(facts[0]["payload"]["context_snapshot_ref"])
    assert len(pins) == 1
    pin = pins[0]
    assert pin.workspace_abs == str(tmp_path.resolve())
    assert pin.runtime_root == repository.runtime_root
    assert pin.storage_identity_token == repository.storage_identity_token
    assert pin.snapshot_logical_path == f"runtime/contexts/{pin.context_snapshot_ref[:2]}/{pin.context_snapshot_ref}"
    assert pin.snapshot_absolute_path == repository.snapshot_path(pin.context_snapshot_ref)
    assert pin.snapshot_source == "roles.kernel.final_provider_attempt"
    assert pin.factory_run_id == "factory-run-1"
    assert pin.role == "director"
    assert pin.verification_scope == "factory"
    assert pin.request_freeze_id == "freeze-1"
    assert pin.provider_request_id == facts[0]["payload"]["provider_request_id"]
    assert pin.composite_request_hash == facts[0]["payload"]["composite_request_hash"]
    assert Path(repository.pin_path(pin.context_snapshot_ref, pin.provider_request_id)).is_file()
    assert gate.drain_coordinator.snapshot().settled is True


def test_governed_json_parse_failure_records_failed_physical_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    class _InvalidJsonResponse(_Response):
        def json(self) -> dict[str, Any]:
            raise ValueError("invalid provider JSON")

    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers.requests.post",
        lambda *_args, **_kwargs: _InvalidJsonResponse(),
    )
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.1,
        },
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    assert result.error == "invalid provider JSON"
    terminals = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminals) == 1
    assert terminals[0]["payload"]["status"] == "failed"


def test_governed_json_parse_failure_retries_as_new_failed_then_completed_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    physical_calls = 0

    class _InvalidJsonResponse(_Response):
        def json(self) -> dict[str, Any]:
            raise ValueError("invalid provider JSON")

    responses = iter((_InvalidJsonResponse(), _Response()))

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return next(responses)

    class _Clock:
        current = 1.0

        def time(self) -> float:
            return self.current

        def sleep(self, seconds: float) -> None:
            self.current += seconds

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.1,
        },
        1,
        1,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        clock=_Clock(),
        physical_dispatch_port=gate,
    )

    assert result.ok is True
    assert physical_calls == 2
    terminals = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert [item["payload"]["status"] for item in terminals] == ["failed", "completed"]
    assert [item["payload"]["attempt_number"] for item in terminals] == [1, 2]


@pytest.mark.parametrize(
    "extract_error",
    [KeyError("missing provider content"), TypeError("invalid provider content")],
    ids=["key-error", "type-error"],
)
def test_governed_extract_output_error_records_failed_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extract_error: Exception,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _extract_output(_body: dict[str, Any]) -> str:
        raise extract_error

    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers.requests.post",
        lambda *_args, **_kwargs: _Response(),
    )
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.1,
        },
        1,
        0,
        "prompt",
        _extract_output,
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    terminal = lifecycle.query_strict()[-1]
    assert terminal["event_type"] == "provider_attempt.terminal"
    assert terminal["payload"]["status"] == "failed"


def test_governed_usage_extraction_error_records_failed_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _usage_error(_prompt: str, _output: str, _body: dict[str, Any]) -> Usage:
        raise ValueError("invalid provider usage")

    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers.requests.post",
        lambda *_args, **_kwargs: _Response(),
    )
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.1,
        },
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        _usage_error,
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    assert result.error == "invalid provider usage"
    assert lifecycle.query_strict()[-1]["payload"]["status"] == "failed"


def test_governed_finalize_exception_records_failed_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _fail_finalize(
        _cls: type[Any],
        _payload: Any,
        *,
        visible_text: str | None = None,
    ) -> Any:
        del visible_text
        raise ValueError("provider finalization failed")

    monkeypatch.setattr(provider_helpers.LLMResponseParser, "finalize_response", classmethod(_fail_finalize))
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers.requests.post",
        lambda *_args, **_kwargs: _Response(),
    )
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.1,
        },
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    assert result.error == "provider finalization failed"
    assert lifecycle.query_strict()[-1]["payload"]["status"] == "failed"


def test_governed_finalize_semantic_failure_keeps_physical_terminal_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    physical_calls = 0

    class _TruncatedReasoningResponse(_Response):
        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {"content": None, "reasoning_content": "unfinished reasoning"},
                        "finish_reason": "length",
                    }
                ]
            }

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return _TruncatedReasoningResponse()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.1,
        },
        1,
        2,
        "prompt",
        lambda _body: "",
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    assert "reasoning truncated" in str(result.error)
    assert result.thinking == "unfinished reasoning"
    assert physical_calls == 1
    assert lifecycle.query_strict()[-1]["payload"]["status"] == "completed"


def test_governed_success_parses_extracts_and_projects_usage_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    calls = {"json": 0, "extract": 0, "usage": 0, "post": 0}

    class _Clock:
        current = 1.0

        def time(self) -> float:
            return self.current

        def sleep(self, seconds: float) -> None:
            self.current += seconds

        def advance(self, seconds: float) -> None:
            self.current += seconds

    clock = _Clock()

    class _CountingResponse(_Response):
        def json(self) -> dict[str, Any]:
            calls["json"] += 1
            clock.advance(1.0)
            return super().json()

    def _post(*_args: object, **_kwargs: object) -> _Response:
        calls["post"] += 1
        return _CountingResponse()

    def _extract(body: dict[str, Any]) -> str:
        calls["extract"] += 1
        clock.advance(10.0)
        return str(body["choices"][0]["message"]["content"])

    def _usage(_prompt: str, _output: str, _body: dict[str, Any]) -> Usage:
        calls["usage"] += 1
        clock.advance(100.0)
        return Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.1,
        },
        1,
        0,
        "prompt",
        _extract,
        _usage,
        clock=clock,
        physical_dispatch_port=gate,
    )

    assert result.ok is True
    assert result.output == "ok"
    assert result.latency_ms == 1000
    assert calls == {"json": 1, "extract": 1, "usage": 1, "post": 1}
    assert lifecycle.query_strict()[-1]["payload"]["status"] == "completed"


def test_governed_provider_retry_creates_one_unique_lifecycle_pair_per_physical_http_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    responses = iter(
        (
            (
                rate_limited_response := _Response(
                    status_code=429,
                    text="rate limited",
                    headers={"Retry-After": "0"},
                )
            ),
            _Response(),
        )
    )
    physical_calls = 0

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return next(responses)

    class _Clock:
        current = 1.0
        sleeps: list[float]

        def __init__(self) -> None:
            self.sleeps = []

        def time(self) -> float:
            return self.current

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.current += seconds

    clock = _Clock()
    original_retry_after_parser = provider_helpers._parse_retry_after_seconds

    def _parse_retry_after(response: Any) -> float | None:
        assert response is rate_limited_response
        return original_retry_after_parser(response)

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    monkeypatch.setattr(provider_helpers, "_parse_retry_after_seconds", _parse_retry_after)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.1,
        },
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        clock=clock,
        physical_dispatch_port=gate,
    )
    assert result.ok is True
    assert physical_calls == 2
    assert clock.sleeps == [0.0]
    facts = lifecycle.query_strict()
    starts = tuple(item for item in facts if item["event_type"] == "provider_attempt.started")
    terminals = tuple(item for item in facts if item["event_type"] == "provider_attempt.terminal")
    assert len(starts) == len(terminals) == 2
    request_ids = [item["payload"]["provider_request_id"] for item in starts]
    assert len(set(request_ids)) == 2
    assert request_ids == [item["payload"]["provider_request_id"] for item in terminals]
    assert [item["payload"]["attempt_number"] for item in starts] == [1, 2]
    assert [item["payload"]["status"] for item in terminals] == ["failed", "completed"]


def test_governed_context_overflow_self_heal_records_failed_then_completed_without_duplicate_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    responses = iter(
        (
            _Response(
                status_code=400,
                text=(
                    "maximum context length is 8192 tokens; this request asked for "
                    "4096 output tokens and at least 7000 input tokens"
                ),
            ),
            _Response(),
        )
    )
    sent_max_tokens: list[int] = []

    def _post(*_args: object, **kwargs: Any) -> _Response:
        sent_max_tokens.append(int(kwargs["json"]["max_tokens"]))
        return next(responses)

    payload = {
        "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
        "tools": [],
        "tool_choice": "auto",
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        payload,
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )

    assert result.ok is True
    assert sent_max_tokens == [4096, 1176]
    assert payload["max_tokens"] == 1176
    terminals = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert [item["payload"]["status"] for item in terminals] == ["failed", "completed"]
    assert [item["payload"]["attempt_number"] for item in terminals] == [1, 2]


@pytest.mark.parametrize("status_code", [401, 500])
def test_governed_http_failure_records_failed_terminal_and_preserves_response_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    physical_calls = 0
    response_body = f"provider failure {status_code}"

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return _Response(status_code=status_code, text=response_body)

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    result = invoke_with_retry(
        f"https://example.test/{status_code}/v1/chat/completions",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.1,
        },
        1,
        0,
        "prompt",
        lambda body: str(body),
        lambda _prompt, _output, _body: Usage.estimate("", ""),
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    assert response_body in str(result.error)
    assert physical_calls == 1
    facts = lifecycle.query_strict()
    assert [item["event_type"] for item in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    assert facts[-1]["payload"]["status"] == "failed"


def test_governed_snapshot_or_pin_failure_keeps_physical_http_count_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)

    class _FailingSnapshotStore:
        def persist_and_pin(self, _attempt: object) -> object:
            raise OSError("pin fsync failed")

    gate, lifecycle = _gate(tmp_path, snapshot_store=_FailingSnapshotStore())
    physical_calls = 0

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return _Response()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    with pytest.raises(OSError, match="pin fsync failed"):
        invoke_with_retry(
            "https://example.test/v1/chat/completions",
            {},
            {
                "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                "tools": [],
                "tool_choice": "auto",
                "temperature": 0.1,
            },
            1,
            0,
            "prompt",
            lambda body: str(body),
            lambda _prompt, _output, _body: Usage.estimate("", ""),
            physical_dispatch_port=gate,
        )
    assert physical_calls == 0
    assert lifecycle.query_strict() == ()


def test_frozen_wire_is_detached_from_original_and_callback_cannot_mutate_authoritative_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    payload = {
        "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
        "tools": [],
        "tool_choice": "auto",
        "temperature": 0.1,
        "max_tokens": 128,
    }

    class _MutatingSnapshotStore:
        frozen_attempt: FrozenFinalProviderAttemptV1 | None = None

        def persist_and_pin(self, attempt: FrozenFinalProviderAttemptV1) -> object:
            self.frozen_attempt = attempt
            payload["max_tokens"] = 999
            dispatch_view = attempt.dispatch_view
            with pytest.raises(TypeError):
                dispatch_view["body"]["max_tokens"] = 777
            return SimpleNamespace(context_snapshot_ref="d" * 24, pin_hash="e" * 64)

    snapshot_store = _MutatingSnapshotStore()
    gate, _lifecycle = _gate(
        tmp_path,
        snapshot_store=snapshot_store,
        semantic_options={"temperature": 0.1, "max_tokens": 128},
    )
    dispatched_bodies: list[dict[str, Any]] = []

    def _post(*_args: object, **kwargs: Any) -> _Response:
        dispatched_bodies.append(dict(kwargs["json"]))
        return _Response()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        payload,
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )
    assert result.ok is True
    assert payload["max_tokens"] == 999
    assert dispatched_bodies[0]["max_tokens"] == 128
    frozen_attempt = snapshot_store.frozen_attempt
    assert frozen_attempt is not None
    assert frozen_attempt.durable_copy()["physical_wire"]["body"]["max_tokens"] == 128


def test_sync_cancellation_records_terminal_before_it_escapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _post(*_args: object, **_kwargs: object) -> _Response:
        raise KeyboardInterrupt("cancelled by caller")

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    with pytest.raises(KeyboardInterrupt, match="cancelled by caller"):
        invoke_with_retry(
            "https://example.test/v1/chat/completions",
            {},
            {
                "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                "tools": [],
                "tool_choice": "auto",
                "temperature": 0.1,
            },
            1,
            0,
            "prompt",
            lambda body: str(body),
            lambda _prompt, _output, _body: Usage.estimate("", ""),
            physical_dispatch_port=gate,
        )
    facts = lifecycle.query_strict()
    assert [item["event_type"] for item in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    assert facts[-1]["payload"]["status"] == "cancelled"


def test_terminal_fsync_failure_blocks_successful_physical_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _fail_terminal(*_args: object, **_kwargs: object) -> None:
        raise OSError("terminal fsync failed")

    monkeypatch.setattr(lifecycle, "append_terminal", _fail_terminal)
    physical_calls = 0

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return _Response()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    with pytest.raises(OSError, match="terminal fsync failed"):
        invoke_with_retry(
            "https://example.test/v1/chat/completions",
            {},
            {
                "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                "tools": [],
                "tool_choice": "auto",
                "temperature": 0.1,
            },
            1,
            0,
            "prompt",
            lambda body: body["choices"][0]["message"]["content"],
            lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            physical_dispatch_port=gate,
        )
    assert physical_calls == 1
    sync_drain = gate.drain_coordinator.snapshot()
    assert sync_drain.settled is False
    assert sync_drain.inflight_request_ids
    assert sync_drain.terminal_failures[0].error_type == "OSError"


def test_actual_pin_fsync_or_reread_failure_keeps_physical_http_count_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    physical_calls = 0

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return _Response()

    original_verify = ContextSnapshotAuditPinRepository._fsync_and_verify

    def _fail_pin_verify(self: ContextSnapshotAuditPinRepository, path: str, expected: bytes) -> None:
        if "/pins/" in path.replace("\\", "/"):
            raise OSError("pin fsync reread failed")
        original_verify(self, path, expected)

    monkeypatch.setattr(ContextSnapshotAuditPinRepository, "_fsync_and_verify", _fail_pin_verify)
    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _post)
    gate, lifecycle = _gate(tmp_path)
    with pytest.raises(OSError, match="pin fsync reread failed"):
        invoke_with_retry(
            "https://example.test/v1/chat/completions",
            {},
            {
                "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
                "tools": [],
                "tool_choice": "auto",
                "temperature": 0.1,
            },
            1,
            0,
            "prompt",
            lambda body: str(body),
            lambda _prompt, _output, _body: Usage.estimate("", ""),
            physical_dispatch_port=gate,
        )
    assert physical_calls == 0
    assert lifecycle.query_strict() == ()


@pytest.mark.asyncio
async def test_async_success_and_failure_each_append_exactly_one_terminal(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    async def _success(_wire: object) -> str:
        return "ok"

    assert await gate.dispatch_async(wire_request=_wire_request(), send=_success) == "ok"

    async def _failure(_wire: object) -> str:
        raise ValueError("provider failed")

    with pytest.raises(ValueError, match="provider failed"):
        await gate.dispatch_async(wire_request=_wire_request(), send=_failure)

    facts = lifecycle.query_strict()
    starts = tuple(item for item in facts if item["event_type"] == "provider_attempt.started")
    terminals = tuple(item for item in facts if item["event_type"] == "provider_attempt.terminal")
    assert len(starts) == len(terminals) == 2
    assert [item["payload"]["status"] for item in terminals] == ["completed", "failed"]
    assert [item["payload"]["provider_request_id"] for item in starts] == [
        item["payload"]["provider_request_id"] for item in terminals
    ]
    drained = await gate.drain_coordinator.wait_settled(
        verification_scope="factory",
        scope_id="factory-run-1",
        timeout_seconds=0.1,
    )
    assert drained.settled is True


@pytest.mark.asyncio
async def test_async_cancel_waits_for_shielded_terminal_ack_even_after_second_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    terminal_entered = threading.Event()
    release_terminal = threading.Event()
    terminal_complete = threading.Event()
    terminal_calls = 0
    original_terminal = lifecycle.append_terminal

    def _blocking_terminal(*args: Any, **kwargs: Any) -> object:
        nonlocal terminal_calls
        terminal_calls += 1
        terminal_entered.set()
        assert release_terminal.wait(timeout=2)
        terminal_complete.set()
        return original_terminal(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "append_terminal", _blocking_terminal)
    never = asyncio.Event()

    async def _send(_wire: object) -> str:
        await never.wait()
        return "unreachable"

    task = asyncio.create_task(gate.dispatch_async(wire_request=_wire_request(), send=_send))
    await asyncio.sleep(0)
    task.cancel()
    assert await asyncio.to_thread(terminal_entered.wait, 1)
    assert gate.drain_coordinator.inflight_request_ids
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_terminal.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert terminal_complete.is_set()
    assert terminal_calls == 1
    assert gate.drain_coordinator.inflight_request_ids == ()


@pytest.mark.asyncio
async def test_blocking_worker_outlives_cancelled_waiter_and_owns_terminal(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    worker_entered = threading.Event()
    release_worker = threading.Event()

    def _worker(_wire: object) -> str:
        worker_entered.set()
        assert release_worker.wait(timeout=2)
        return "worker-result"

    task = asyncio.create_task(gate.dispatch_blocking_async(wire_request=_wire_request(), send=_worker))
    assert await asyncio.to_thread(worker_entered.wait, 1)
    task.cancel()
    await asyncio.sleep(0.02)
    assert gate.drain_coordinator.inflight_request_ids
    assert not tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    with pytest.raises(ProviderAttemptDrainError) as pending:
        await gate.drain_coordinator.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.01,
        )
    assert pending.value.code == "provider_attempt_drain_timeout"
    assert pending.value.result.inflight_request_ids

    release_worker.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    terminals = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminals) == 1
    assert terminals[0]["payload"]["status"] == "completed"
    assert gate.drain_coordinator.inflight_request_ids == ()
    assert (
        await gate.drain_coordinator.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.1,
        )
    ).settled


@pytest.mark.asyncio
async def test_async_terminal_persistence_failure_rejects_result_and_fails_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _fail_terminal(*_args: object, **_kwargs: object) -> None:
        raise OSError("terminal fsync failed")

    monkeypatch.setattr(lifecycle, "append_terminal", _fail_terminal)

    async def _send(_wire: object) -> str:
        return "must-not-escape"

    with pytest.raises(OSError, match="terminal fsync failed"):
        await gate.dispatch_async(wire_request=_wire_request(), send=_send)
    with pytest.raises(ProviderAttemptDrainError) as drain_failure:
        await gate.drain_coordinator.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.1,
        )
    assert drain_failure.value.result.inflight_request_ids


@pytest.mark.asyncio
async def test_role_session_gate_uses_separate_ledger_and_cannot_drain_as_factory(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    role_lifecycle = StrictProviderAttemptLifecycleStore.for_role_session(
        workspace=str(tmp_path),
        role_session_id="role-session-1",
    )
    gate = FinalProviderAttemptGate.for_role_session(
        workspace=str(tmp_path),
        role_session_id="role-session-1",
        run_id="run-1",
        role="director",
        turn_id="turn-1",
        call_id="call-1",
        request_freeze_id="freeze-1",
        provider="openai",
        model="model-1",
        semantic_request={
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "response_format": None,
            "semantic_options": {"temperature": 0.1},
        },
        lifecycle=role_lifecycle,
        snapshot_store=SimpleNamespace(
            persist_and_pin=lambda _attempt: SimpleNamespace(context_snapshot_ref="d" * 24, pin_hash="e" * 64)
        ),
        drain_coordinator=ProviderAttemptInFlightCoordinator.for_role_session("role-session-1"),
    )

    async def _send(_wire: object) -> str:
        return "role-result"

    assert await gate.dispatch_async(wire_request=_wire_request(), send=_send) == "role-result"
    role_facts = role_lifecycle.query_strict()
    assert [item["event_type"] for item in role_facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    assert all(item["payload"]["verification_scope"] == "role_session" for item in role_facts)
    assert all(item["payload"]["scope_id"] == "role-session-1" for item in role_facts)
    factory_lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="role-session-1",
    )
    assert factory_lifecycle.query_strict() == ()
    with pytest.raises(ProviderAttemptDrainError, match="scope mismatch"):
        await gate.drain_coordinator.wait_settled(
            verification_scope="factory",
            scope_id="role-session-1",
            timeout_seconds=0.1,
        )


@pytest.mark.asyncio
async def test_async_stream_terminal_waits_for_response_exit_and_full_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    events: list[str] = []
    original_start = lifecycle.append_start
    original_terminal = lifecycle.append_terminal

    def _append_start(*args: Any, **kwargs: Any) -> object:
        receipt = original_start(*args, **kwargs)
        events.append("start_ack")
        return receipt

    def _append_terminal(*args: Any, **kwargs: Any) -> object:
        receipt = original_terminal(*args, **kwargs)
        events.append("terminal_ack")
        return receipt

    monkeypatch.setattr(lifecycle, "append_start", _append_start)
    monkeypatch.setattr(lifecycle, "append_terminal", _append_terminal)

    class _ResponseContext:
        async def __aenter__(self) -> object:
            events.append("post_enter")
            return object()

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _tb: object,
        ) -> None:
            events.append("response_exit")

    def _open_stream(_wire: Mapping[str, Any]) -> _ResponseContext:
        return _ResponseContext()

    async def _consume(_response: object) -> AsyncIterator[str]:
        yield "first"
        yield "second"

    stream = gate.dispatch_stream_async(
        wire_request=_wire_request(),
        open_stream=_open_stream,
        consume=_consume,
    )
    assert await anext(stream) == "first"
    assert events == ["start_ack", "post_enter"]
    assert not tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert await anext(stream) == "second"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert events == ["start_ack", "post_enter", "response_exit", "terminal_ack"]
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_async_stream_consumer_aclose_exits_response_before_cancelled_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    events: list[str] = []
    original_terminal = lifecycle.append_terminal

    def _append_terminal(*args: Any, **kwargs: Any) -> object:
        receipt = original_terminal(*args, **kwargs)
        events.append("terminal_ack")
        return receipt

    monkeypatch.setattr(lifecycle, "append_terminal", _append_terminal)

    class _ResponseContext:
        async def __aenter__(self) -> object:
            events.append("post_enter")
            return object()

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _tb: object,
        ) -> None:
            events.append("response_exit")

    def _open_stream(_wire: Mapping[str, Any]) -> _ResponseContext:
        return _ResponseContext()

    async def _consume(_response: object) -> AsyncIterator[str]:
        yield "first"
        await asyncio.Event().wait()

    stream = gate.dispatch_stream_async(
        wire_request=_wire_request(),
        open_stream=_open_stream,
        consume=_consume,
    )
    assert isinstance(stream, AsyncGenerator)
    assert await anext(stream) == "first"
    await stream.aclose()

    assert events == ["post_enter", "response_exit", "terminal_ack"]
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "cancelled"
    assert gate.drain_coordinator.inflight_request_ids == ()


@pytest.mark.asyncio
async def test_async_stream_terminal_persistence_failure_rejects_normal_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _fail_terminal(*_args: object, **_kwargs: object) -> None:
        raise OSError("stream terminal fsync failed")

    monkeypatch.setattr(lifecycle, "append_terminal", _fail_terminal)

    class _ResponseContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def _consume(_response: object) -> AsyncIterator[str]:
        yield "must-not-produce-success-verdict"

    with pytest.raises(OSError, match="stream terminal fsync failed"):
        async for _item in gate.dispatch_stream_async(
            wire_request=_wire_request(),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            pass
    with pytest.raises(ProviderAttemptDrainError):
        await gate.drain_coordinator.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.1,
        )


@pytest.mark.asyncio
async def test_real_async_helper_retries_each_post_with_exact_frozen_mutation_and_lifecycle_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    events: list[str] = []
    payload = {
        "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
        "tools": [],
        "tool_choice": "auto",
        "response_format": None,
        "temperature": 0.1,
        "max_tokens": 128,
    }

    class _MutatingSnapshotStore:
        attempts: list[FrozenFinalProviderAttemptV1] = []

        def persist_and_pin(self, attempt: FrozenFinalProviderAttemptV1) -> object:
            self.attempts.append(attempt)
            if len(self.attempts) == 1:
                payload["max_tokens"] = 64
            return SimpleNamespace(context_snapshot_ref="d" * 24, pin_hash="e" * 64)

    snapshot_store = _MutatingSnapshotStore()
    gate, lifecycle = _gate(
        tmp_path,
        snapshot_store=snapshot_store,
        semantic_options={"temperature": 0.1, "max_tokens": 128},
    )
    original_start = lifecycle.append_start
    original_terminal = lifecycle.append_terminal

    def _append_start(*args: Any, **kwargs: Any) -> object:
        receipt = original_start(*args, **kwargs)
        events.append("start_ack")
        return receipt

    def _append_terminal(*args: Any, **kwargs: Any) -> object:
        receipt = original_terminal(*args, **kwargs)
        events.append("terminal_ack")
        return receipt

    monkeypatch.setattr(lifecycle, "append_start", _append_start)
    monkeypatch.setattr(lifecycle, "append_terminal", _append_terminal)
    sessions = (
        _AsyncSession(_AsyncResponse(status=500), events),
        _AsyncSession(_AsyncResponse(status=503), events),
        _AsyncSession(_AsyncResponse(json_body={"ok": True}), events),
    )
    pending_sessions = iter(sessions)

    async def _create_session(_old: object) -> _AsyncSession:
        return next(pending_sessions)

    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        _create_session,
    )
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers.asyncio.sleep",
        AsyncMock(),
    )

    result = [
        item
        async for item in invoke_stream_with_retry(
            "https://example.test/stream",
            {},
            payload,
            5,
            max_attempts=3,
            retry_delay_seconds=0,
            governance_mode="governed_required",
            physical_dispatch_port=gate,
        )
    ]

    assert result == [{"ok": True}]
    attempts = snapshot_store.attempts
    assert len(attempts) == 3
    assert len({attempt.provider_request_id for attempt in attempts}) == 3
    assert [attempt.dispatch_copy()["body"]["max_tokens"] for attempt in attempts] == [128, 64, 64]
    assert [attempt.durable_copy()["physical_wire"]["body"]["max_tokens"] for attempt in attempts] == [
        128,
        64,
        64,
    ]
    assert [session.posts[0]["json"]["max_tokens"] for session in sessions] == [128, 64, 64]
    assert events == [
        "start_ack",
        "post_enter",
        "response_exit",
        "session_close",
        "terminal_ack",
        "start_ack",
        "post_enter",
        "response_exit",
        "session_close",
        "terminal_ack",
        "start_ack",
        "post_enter",
        "response_exit",
        "session_close",
        "terminal_ack",
    ]
    facts = lifecycle.query_strict()
    starts = tuple(item for item in facts if item["event_type"] == "provider_attempt.started")
    terminals = tuple(item for item in facts if item["event_type"] == "provider_attempt.terminal")
    assert len(starts) == len(terminals) == 3
    assert [item["payload"]["status"] for item in terminals] == ["failed", "failed", "completed"]
    assert [item["payload"]["provider_request_id"] for item in starts] == [
        item["payload"]["provider_request_id"] for item in terminals
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["pin", "start"])
async def test_real_async_helper_pin_or_start_failure_keeps_post_count_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    _bootstrap(tmp_path)

    class _SnapshotStore:
        def persist_and_pin(self, _attempt: object) -> object:
            if failure_phase == "pin":
                raise OSError("pin fsync failed")
            return SimpleNamespace(context_snapshot_ref="d" * 24, pin_hash="e" * 64)

    gate, lifecycle = _gate(tmp_path, snapshot_store=_SnapshotStore())
    if failure_phase == "start":
        monkeypatch.setattr(
            lifecycle,
            "append_start",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("start fsync failed")),
        )
    create_session_calls = 0

    async def _create_session(_old: object) -> _AsyncSession:
        nonlocal create_session_calls
        create_session_calls += 1
        return _AsyncSession(_AsyncResponse(json_body={"ok": True}), [])

    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        _create_session,
    )
    stream = invoke_stream_with_retry(
        "https://example.test/stream",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "response_format": None,
            "temperature": 0.1,
        },
        5,
        max_attempts=1,
        governance_mode="governed_required",
        physical_dispatch_port=gate,
    )

    with pytest.raises(OSError, match=f"{failure_phase} fsync failed"):
        await anext(stream)
    assert create_session_calls == 0
    assert lifecycle.query_strict() == ()
    if failure_phase == "start":
        state = gate._physical_attempt_control_port.budget_state("f" * 64)
        assert state.aborted_count == 1
        assert state.ambiguous_count == 0
        assert gate.drain_coordinator.snapshot().settled is True


def test_start_append_with_durable_fact_but_lost_ack_is_ambiguous_and_never_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    original_start = lifecycle.append_start
    sends = 0

    def _persist_then_lose_ack(*args: Any, **kwargs: Any) -> object:
        original_start(*args, **kwargs)
        raise OSError("start durability ack lost")

    def _send(_wire: Mapping[str, Any]) -> str:
        nonlocal sends
        sends += 1
        return "forbidden"

    monkeypatch.setattr(lifecycle, "append_start", _persist_then_lose_ack)
    with pytest.raises(OSError, match="start durability ack lost"):
        gate.dispatch_sync(wire_request=_wire_request(), send=_send)

    state = gate._physical_attempt_control_port.budget_state("f" * 64)
    assert sends == 0
    assert state.aborted_count == 0
    assert state.ambiguous_count == 1
    assert gate.drain_coordinator.snapshot().settled is False
    assert [fact["event_type"] for fact in lifecycle.query_strict()] == ["provider_attempt.started"]


@pytest.mark.asyncio
async def test_real_async_helper_second_cancellation_closes_session_before_terminal_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    events: list[str] = []
    session = _AsyncSession(_AsyncResponse(), events)
    entered_handler = asyncio.Event()
    never = asyncio.Event()
    terminal_entered = threading.Event()
    release_terminal = threading.Event()
    terminal_calls = 0
    original_terminal = lifecycle.append_terminal

    async def _create_session(_old: object) -> _AsyncSession:
        return session

    def _blocking_terminal(*args: Any, **kwargs: Any) -> object:
        nonlocal terminal_calls
        terminal_calls += 1
        events.append("terminal_entered")
        terminal_entered.set()
        assert release_terminal.wait(timeout=2)
        events.append("terminal_ack")
        return original_terminal(*args, **kwargs)

    async def _handler(_response: object) -> AsyncGenerator[str, None]:
        entered_handler.set()
        await never.wait()
        yield "unreachable"

    monkeypatch.setattr(lifecycle, "append_terminal", _blocking_terminal)
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        _create_session,
    )
    stream = invoke_stream_with_retry_and_handler(
        "https://example.test/custom-stream",
        {},
        {
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "response_format": None,
            "temperature": 0.1,
        },
        5,
        _handler,  # type: ignore[arg-type]
        max_attempts=1,
        governance_mode="governed_required",
        physical_dispatch_port=gate,
    )
    task = asyncio.create_task(anext(stream))
    await entered_handler.wait()
    task.cancel()
    assert await asyncio.to_thread(terminal_entered.wait, 1)
    assert events[:3] == ["post_enter", "response_exit", "session_close"]
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_terminal.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session.closed is True
    assert events == ["post_enter", "response_exit", "session_close", "terminal_entered", "terminal_ack"]
    assert terminal_calls == 1
    assert gate.drain_coordinator.inflight_request_ids == ()


@pytest.mark.asyncio
async def test_async_stream_sync_open_failure_records_failed_terminal_before_escape(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    outbound_calls = 0

    def _open_stream(_wire: Mapping[str, Any]) -> _AsyncPostContext:
        nonlocal outbound_calls
        assert outbound_calls == 0
        raise RuntimeError("open stream construction failed")

    async def _consume(_response: object) -> AsyncIterator[str]:
        if False:
            yield "unreachable"

    with pytest.raises(RuntimeError, match="open stream construction failed"):
        async for _item in gate.dispatch_stream_async(
            wire_request=_wire_request(),
            open_stream=_open_stream,
            consume=_consume,
        ):
            pass

    assert outbound_calls == 0
    facts = lifecycle.query_strict()
    assert [item["event_type"] for item in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    assert facts[-1]["payload"]["status"] == "failed"
    assert "open stream construction failed" in facts[-1]["payload"]["error"]


@pytest.mark.asyncio
async def test_async_stream_consume_keyboard_interrupt_exits_response_and_records_cancelled(
    tmp_path: Path,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    events: list[str] = []

    class _ResponseContext:
        async def __aenter__(self) -> object:
            events.append("post_enter")
            return object()

        async def __aexit__(self, *_args: object) -> None:
            events.append("response_exit")

    async def _consume(_response: object) -> AsyncIterator[str]:
        if False:
            yield "unreachable"
        raise KeyboardInterrupt("consume interrupted")

    with pytest.raises(KeyboardInterrupt, match="consume interrupted"):
        async for _item in gate.dispatch_stream_async(
            wire_request=_wire_request(),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            pass

    assert events == ["post_enter", "response_exit"]
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "cancelled"
    assert "KeyboardInterrupt: consume interrupted" in terminal[0]["payload"]["error"]


@pytest.mark.asyncio
async def test_async_stream_cleanup_system_exit_is_cancelled_and_preserves_both_errors(
    tmp_path: Path,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    events: list[str] = []

    class _ResponseContext:
        async def __aenter__(self) -> object:
            events.append("post_enter")
            return object()

        async def __aexit__(self, *_args: object) -> None:
            events.append("response_exit")
            raise SystemExit("cleanup aborted")

    async def _consume(_response: object) -> AsyncIterator[str]:
        if False:
            yield "unreachable"
        raise ValueError("consume failed")

    with pytest.raises(ValueError, match="consume failed"):
        async for _item in gate.dispatch_stream_async(
            wire_request=_wire_request(),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            pass

    assert events == ["post_enter", "response_exit"]
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "cancelled"
    assert "ValueError: consume failed" in terminal[0]["payload"]["error"]
    assert "SystemExit: cleanup aborted" in terminal[0]["payload"]["error"]
