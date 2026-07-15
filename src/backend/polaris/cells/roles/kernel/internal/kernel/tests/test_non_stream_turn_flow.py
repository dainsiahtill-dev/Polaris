"""Tests for the non-streaming role-turn flow owner."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.kernel.internal.kernel import non_stream_turn_flow as flow
from polaris.cells.roles.kernel.internal.kernel.turn_prompt_setup import RoleTurnSetupError
from polaris.cells.roles.profile.public.service import (
    PromptFingerprint,
    RoleProfile,
    RoleTurnRequest,
    RoleTurnResult,
)


class _PromptBuilder:
    def build_retry_prompt(
        self,
        base_system_prompt: str,
        quality_result: dict[str, Any],
        attempt: int,
    ) -> str:
        return f"retry:{base_system_prompt}:{quality_result}:{attempt}"


class _EventEmitter:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def resolve_observer_run_id(self, role: str, run_id: str | None) -> str:
        return run_id or f"run:{role}"

    def emit_runtime_llm_event(self, **payload: Any) -> None:
        self.events.append(dict(payload))


class _Metrics:
    def __init__(self) -> None:
        self.llm_latencies: list[float] = []
        self.quality_scores: list[float] = []
        self.retries: list[tuple[str, str]] = []
        self.executions: list[tuple[str, str]] = []

    def record_llm_latency(self, latency_seconds: float) -> None:
        self.llm_latencies.append(latency_seconds)

    def record_quality_score(self, quality_score: float) -> None:
        self.quality_scores.append(quality_score)

    def record_retry(self, role: str, reason: str) -> None:
        self.retries.append((role, reason))

    def record_execution(self, role: str, status: str) -> None:
        self.executions.append((role, status))


class _Span:
    def __init__(self) -> None:
        self.tags: dict[str, Any] = {}

    def __enter__(self) -> _Span:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def set_tag(self, key: str, value: Any) -> None:
        self.tags[key] = value


class _Tracer:
    def __init__(self) -> None:
        self.spans: list[_Span] = []

    def span(self, *_: Any, **__: Any) -> _Span:
        span = _Span()
        self.spans.append(span)
        return span


def _profile() -> RoleProfile:
    return RoleProfile(
        role_id="director",
        display_name="Director",
        description="Executes governed implementation turns.",
        model="gpt-test",
    )


def _kernel() -> Any:
    return SimpleNamespace(
        workspace="/tmp/workspace",
        config=SimpleNamespace(max_retries=1),
        _cached_tool_gateway="previous",
        _cached_gateway_profile="previous-profile",
    )


def test_execute_non_stream_role_turn_returns_setup_error_without_transaction(monkeypatch: Any) -> None:
    class _FailingTransactionTurnExecutor:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def execute_turn(self, **_: Any) -> RoleTurnResult:
            raise AssertionError("TransactionKernel should not run after setup failure")

    def fail_setup(*_: Any, **__: Any) -> Any:
        raise RoleTurnSetupError("profile", "missing profile")

    monkeypatch.setattr(flow, "build_role_turn_prompt_setup", fail_setup)
    monkeypatch.setattr(flow, "TransactionTurnExecutor", _FailingTransactionTurnExecutor)

    result = asyncio.run(
        flow.execute_non_stream_role_turn(
            kernel=_kernel(),
            role="director",
            request=RoleTurnRequest(message="hello", validate_output=False),
        )
    )

    assert result.error == "角色加载失败: missing profile"
    assert result.is_complete is True


def test_execute_non_stream_role_turn_calls_transaction_and_projects_success(monkeypatch: Any) -> None:
    profile = _profile()
    fingerprint = PromptFingerprint(core_hash="core", profile_fingerprint=profile.profile_fingerprint)
    prompt_builder = _PromptBuilder()
    event_emitter = _EventEmitter()
    metrics = _Metrics()
    tracer = _Tracer()
    captured: dict[str, Any] = {}

    def setup(*_: Any, **__: Any) -> Any:
        return SimpleNamespace(
            profile=profile,
            prompt_builder=prompt_builder,
            fingerprint=fingerprint,
            system_prompt="base",
        )

    class _CapturingTransactionTurnExecutor:
        def __init__(self, kernel: Any) -> None:
            captured["kernel"] = kernel

        async def execute_turn(self, **kwargs: Any) -> RoleTurnResult:
            captured.update(kwargs)
            return RoleTurnResult(
                content="done",
                thinking="",
                profile_version=profile.version,
                prompt_fingerprint=fingerprint,
                is_complete=True,
            )

    monkeypatch.setattr(flow, "build_role_turn_prompt_setup", setup)
    monkeypatch.setattr(flow, "build_context_request", lambda request: {"request": request})
    monkeypatch.setattr(flow, "get_kernel_event_emitter", lambda kernel: event_emitter)
    monkeypatch.setattr(flow, "get_metrics_collector", lambda: metrics)
    monkeypatch.setattr(flow, "get_tracer", lambda: tracer)
    monkeypatch.setattr(flow, "TransactionTurnExecutor", _CapturingTransactionTurnExecutor)

    kernel = _kernel()
    request = RoleTurnRequest(
        message="hello",
        validate_output=False,
        metadata={"turn_request_id": "non-stream-success"},
    )
    result = asyncio.run(
        flow.execute_non_stream_role_turn(
            kernel=kernel,
            role="director",
            request=request,
        )
    )

    assert result.content == "done"
    assert result.error is None
    assert result.is_complete is True
    assert request.run_id == "run:director"
    assert kernel._cached_tool_gateway is None
    assert kernel._cached_gateway_profile is None
    assert captured["kernel"] is kernel
    assert captured["role"] == "director"
    assert captured["profile"] is profile
    assert captured["fingerprint"] is fingerprint
    assert captured["observer_run_id"] == "run:director"
    assert captured["system_prompt"] == "retry:base:None:0"
    assert metrics.llm_latencies
    assert metrics.executions == [("director", "success")]
    assert tracer.spans[0].tags["has_content"] is True


def test_validation_retry_uses_distinct_transaction_attempt_identity(monkeypatch: Any) -> None:
    profile = _profile()
    fingerprint = PromptFingerprint(core_hash="core", profile_fingerprint=profile.profile_fingerprint)
    event_emitter = _EventEmitter()
    captured_attempts: list[dict[str, Any]] = []

    def setup(*_: Any, **__: Any) -> Any:
        return SimpleNamespace(
            profile=profile,
            prompt_builder=_PromptBuilder(),
            fingerprint=fingerprint,
            system_prompt="base",
        )

    class _RetryingTransactionTurnExecutor:
        def __init__(self, _: Any) -> None:
            pass

        async def execute_turn(self, **kwargs: Any) -> RoleTurnResult:
            request = kwargs["request"]
            captured_attempts.append(dict(request.metadata))
            return RoleTurnResult(
                content=f"attempt-{len(captured_attempts)}",
                thinking="",
                profile_version=profile.version,
                prompt_fingerprint=fingerprint,
                is_complete=True,
            )

    def validate_turn_output(**kwargs: Any) -> tuple[SimpleNamespace, str | None]:
        attempt = int(kwargs["attempt"])
        if attempt == 0:
            return (
                SimpleNamespace(
                    success=False,
                    errors=["truncated structured output"],
                    suggestions=[],
                    data=None,
                    quality_score=0.0,
                    quality_passed=False,
                ),
                "truncated structured output",
            )
        return (
            SimpleNamespace(
                success=True,
                errors=[],
                suggestions=[],
                data={"ok": True},
                quality_score=1.0,
                quality_passed=True,
            ),
            None,
        )

    monkeypatch.setattr(flow, "build_role_turn_prompt_setup", setup)
    monkeypatch.setattr(flow, "build_context_request", lambda request: {"request": request})
    monkeypatch.setattr(flow, "get_kernel_event_emitter", lambda kernel: event_emitter)
    monkeypatch.setattr(flow, "get_metrics_collector", lambda: _Metrics())
    monkeypatch.setattr(flow, "get_tracer", lambda: _Tracer())
    monkeypatch.setattr(flow, "TransactionTurnExecutor", _RetryingTransactionTurnExecutor)
    monkeypatch.setattr(flow, "_validate_turn_output", validate_turn_output)

    request = RoleTurnRequest(
        message="return structured output",
        validate_output=True,
        max_retries=1,
        run_id="run-retry",
        task_id="TASK-1",
        metadata={"turn_request_id": "non-stream-retry"},
    )
    result = asyncio.run(
        flow.execute_non_stream_role_turn(
            kernel=_kernel(),
            role="director",
            request=request,
        )
    )

    assert result.error is None
    assert len(captured_attempts) == 2
    assert captured_attempts[0]["transaction_attempt"] == 0
    assert captured_attempts[1]["transaction_attempt"] == 1
    assert captured_attempts[0]["transaction_invocation_id"] == captured_attempts[1]["transaction_invocation_id"]
    assert captured_attempts[0]["transaction_attempt_id"] != captured_attempts[1]["transaction_attempt_id"]

    captured_attempts.clear()
    no_retry_result = asyncio.run(
        flow.execute_non_stream_role_turn(
            kernel=_kernel(),
            role="director",
            request=RoleTurnRequest(
                message="return structured output once",
                validate_output=True,
                max_retries=0,
                run_id="run-no-retry",
                task_id="TASK-2",
                metadata={"turn_request_id": "non-stream-no-retry"},
            ),
        )
    )

    assert no_retry_result.error is not None
    assert len(captured_attempts) == 1
    assert captured_attempts[0]["transaction_attempt"] == 0
