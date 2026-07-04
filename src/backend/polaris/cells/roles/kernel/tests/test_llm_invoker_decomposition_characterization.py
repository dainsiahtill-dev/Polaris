"""Characterization tests for LLMInvoker.call / call_structured decomposition (G8c).

These tests pin the CURRENT behavior of the two mega-methods before the
behavior-preserving decomposition. They cover the coverage_gaps from the
blueprint: every fallback rung, cache hit/put, native-tools-unavailable
branches, reasoning-truncation re-ask, ResponseNormalizer recovery, the
structured strategy branches, and the exception arms.

Strategy: patch ``LLMRequestPreparer._prepare_llm_request`` to return a controlled
``PreparedLLMRequest``, inject a fake executor via ``LLMInvoker(executor=...)``,
and capture the verbatim metadata dicts the invoker passes into its
``_emit_call_*`` delegates (the byte-identical event contract surface).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.internal.llm_caller.response_types import (
    PreparedLLMRequest,
    StructuredLLMResponse,
)
from polaris.cells.roles.profile.public.service import RoleProfile
from polaris.kernelone.llm.engine import AIExecutor
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse, TaskType, Usage
from polaris.kernelone.llm.engine.executor import _coverage_flags

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _EventRecorder:
    """Captures verbatim metadata dicts passed to the invoker emit delegates."""

    def __init__(self) -> None:
        self.start: list[dict[str, Any]] = []
        self.end: list[dict[str, Any]] = []
        self.error: list[dict[str, Any]] = []
        self.retry: list[dict[str, Any]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = self

        def _start(_self: LLMInvoker, **kwargs: Any) -> None:
            recorder.start.append(dict(kwargs))

        def _end(_self: LLMInvoker, **kwargs: Any) -> None:
            recorder.end.append(dict(kwargs))

        def _error(_self: LLMInvoker, **kwargs: Any) -> None:
            recorder.error.append(dict(kwargs))

        def _retry(_self: LLMInvoker, **kwargs: Any) -> None:
            recorder.retry.append(dict(kwargs))

        monkeypatch.setattr(LLMInvoker, "_emit_call_start_event", _start)
        monkeypatch.setattr(LLMInvoker, "_emit_call_end_event", _end)
        monkeypatch.setattr(LLMInvoker, "_emit_call_error_event", _error)
        monkeypatch.setattr(LLMInvoker, "_emit_call_retry_event", _retry)


def _profile(
    provider_id: str = "prov-x",
    model: str = "model-x",
    role_id: str = "director",
) -> RoleProfile:
    return RoleProfile(
        role_id=role_id,
        display_name="Director",
        description="Code execution",
        provider_id=provider_id,
        model=model,
    )


def _prepared(
    profile: RoleProfile,
    *,
    native_tool_mode: str = "disabled",
    native_tool_schemas: list[dict[str, Any]] | None = None,
    native_response_format: dict[str, Any] | None = None,
    response_format_mode: str = "plain_text",
    response_model: type | None = None,
    context_os_audit: dict[str, Any] | None = None,
) -> PreparedLLMRequest:
    return PreparedLLMRequest(
        messages=[{"role": "user", "content": "build"}],
        input_text="build",
        context_result=SimpleNamespace(
            token_estimate=8,
            compression_strategy="none",
            compression_applied=False,
        ),
        context_summary="summary",
        request_options={"max_retries": 0, "max_tokens": 100},
        ai_request=AIRequest(
            task_type=TaskType.DIALOGUE,
            role=profile.role_id,
            input="build",
            options={"max_retries": 0},
            context={"workspace": ".", "mode": "chat", "context_snapshot_ref": "snap-prep"},
        ),
        native_tool_schemas=native_tool_schemas if native_tool_schemas is not None else [],
        native_tool_mode=native_tool_mode,
        native_response_format=native_response_format,
        response_format_mode=response_format_mode,
        response_model=response_model,
        context_os_audit=context_os_audit if context_os_audit is not None else {},
    )


class _ScriptedExecutor:
    """Returns scripted AIResponse objects in order; records invoked requests."""

    def __init__(self, responses: list[AIResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[AIRequest] = []

    async def invoke(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        if self._responses:
            return self._responses.pop(0)
        raise AssertionError("executor invoked more times than scripted")


def test_executor_context_snapshot_coverage_uses_structured_context_before_text_needles() -> None:
    coverage = _coverage_flags(
        "build",
        context={
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "target_files": ["src/index.ts"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "target_files": ["src/index.ts"],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "authorization": {
                    "allowed_write_paths": ["src/index.ts"],
                    "allowed_read_paths": ["src/index.ts"],
                },
            },
            "run_ledger_projection": {
                "failure_evidence": [
                    {
                        "schema_version": "polaris.failure_evidence.v1",
                        "failure_class": "tool_dispatch_dropped",
                        "responsible_layer": "platform",
                        "evidence_refs": ["tool_lifecycle:turn-1"],
                    }
                ],
                "workspace_quality_evidence": {
                    "schema_version": "polaris.workspace_quality_evidence.v1",
                    "quality_errors": [{"code": "MISSING_TARGET"}],
                    "deterministic_checks": ["source_target_coverage"],
                },
            },
        },
    )

    assert coverage == {
        "has_pm_contract": True,
        "has_chief_engineer_blueprint": True,
        "has_target_files": True,
        "has_failure_feedback": True,
        "has_workspace_quality_evidence": True,
    }


def test_executor_context_snapshot_coverage_does_not_count_failure_or_quality_prose() -> None:
    coverage = _coverage_flags(
        (
            "stderr exit_code failed retry error npm test real_run_gate "
            "factory_workspace_quality workspace quality"
        ),
        context={
            "target_files": ["src/index.ts"],
            "failure_summary": {"message": "failure_class: TOOL_DISPATCH_DROPPED"},
            "quality_summary": {"message": "quality_errors: ['missing README']"},
        },
    )

    assert coverage["has_target_files"] is True
    assert coverage["has_failure_feedback"] is False
    assert coverage["has_workspace_quality_evidence"] is False


def test_executor_context_snapshot_coverage_does_not_count_contract_or_scope_prose() -> None:
    coverage = _coverage_flags(
        (
            "PM task contract acceptance depends_on Chief Engineer blueprint "
            "target_files src/index.ts scope_paths tests/"
        ),
        context={
            "chat_messages": [
                {
                    "role": "user",
                    "content": "PM task contract target_files src/index.ts Chief Engineer blueprint",
                }
            ],
        },
    )

    assert coverage["has_pm_contract"] is False
    assert coverage["has_chief_engineer_blueprint"] is False
    assert coverage["has_target_files"] is False


def test_executor_context_snapshot_coverage_rejects_weak_contract_and_scope_mappings() -> None:
    coverage = _coverage_flags(
        "build",
        context={
            "pm_contract": {"note": "PM Task Contract"},
            "ce_blueprint": {"note": "Chief Engineer Blueprint"},
            "target_files": "src/index.ts",
            "authorization": {"allowed_write_paths": []},
        },
    )

    assert coverage["has_pm_contract"] is False
    assert coverage["has_chief_engineer_blueprint"] is False
    assert coverage["has_target_files"] is False


def _patch_prepare(
    monkeypatch: pytest.MonkeyPatch,
    prepared: PreparedLLMRequest,
) -> None:
    async def _prepare(_self: LLMRequestPreparer, **_kwargs: Any) -> PreparedLLMRequest:
        return prepared

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", _prepare)


def _ctx(override: dict[str, Any] | None = None) -> Any:
    return cast(Any, SimpleNamespace(task_id=None, context_override=override))


def _assert_final_request_audit(metadata: dict[str, Any]) -> None:
    audit = metadata["final_request_context_audit"]
    assert audit["schema_version"] == "llm.final_request_context_audit.v1"
    assert "coverage" in audit
    assert metadata["contextTokens"] == audit["final_request_token_estimate"]
    assert metadata["context_tokens_after"] == audit["final_request_token_estimate"]


# ---------------------------------------------------------------------------
# call: native_tools_unavailable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_tools_unavailable_text_fallback_is_retired(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(
        profile,
        native_tool_mode="native_tools_unavailable",
        native_tool_schemas=[{"name": "write_file"}],
    )
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor([])
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx({"allow_native_tool_text_fallback": True}),
        run_id="run1",
    )

    assert resp.error is not None
    assert resp.content == ""
    assert resp.metadata["native_tool_mode"] == "native_tools_unavailable"
    assert resp.metadata["run_id"] == "run1"
    assert resp.metadata["workspace"] == "ws"
    _assert_final_request_audit(resp.metadata)
    assert len(executor.requests) == 0
    assert len(rec.error) == 1
    assert rec.end == []
    _assert_final_request_audit(rec.error[0]["metadata"])
    # Only one start event.
    assert len(rec.start) == 1


@pytest.mark.asyncio
async def test_native_tools_unavailable_fallback_disallowed_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(
        profile,
        native_tool_mode="native_tools_unavailable",
        native_tool_schemas=[{"name": "write_file"}],
    )
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor([])  # must not be invoked
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),  # fallback disallowed
        run_id="run2",
    )

    assert resp.error is not None
    assert resp.error_category == "provider"
    assert resp.content == ""
    assert resp.metadata["native_tool_mode"] == "native_tools_unavailable"
    assert executor.requests == []
    # An error event was emitted.
    assert len(rec.error) == 1
    assert rec.error[0]["error_category"] == "provider"
    _assert_final_request_audit(rec.error[0]["metadata"])
    _assert_final_request_audit(resp.metadata)
    assert rec.end == []


# ---------------------------------------------------------------------------
# call: cache hit + cache put
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_early_return(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(profile)  # cache-eligible (disabled tools, plain text)
    _patch_prepare(monkeypatch, prepared)

    class _CacheHit:
        def get(self, **_kwargs: Any) -> str:
            return "cached-text"

        def put(self, **_kwargs: Any) -> None:
            raise AssertionError("put must not run on a cache hit path")

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.invoker.get_global_llm_cache",
        lambda: _CacheHit(),
    )
    executor = _ScriptedExecutor([])  # must not be invoked
    invoker = LLMInvoker(workspace="ws", enable_cache=True, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        prompt_fingerprint="fp-1",
        run_id="run3",
    )

    assert resp.error is None
    assert resp.content == "cached-text"
    assert resp.metadata["cached"] is True
    assert resp.token_estimate == len("cached-text") // 2
    assert executor.requests == []
    # end event emitted with cached source
    assert len(rec.end) == 1
    assert rec.end[0]["metadata"]["cached"] is True
    assert rec.end[0]["metadata"]["source"] == "cache"


@pytest.mark.asyncio
async def test_cache_put_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(profile)
    _patch_prepare(monkeypatch, prepared)

    put_calls: list[dict[str, Any]] = []

    class _CacheMiss:
        def get(self, **_kwargs: Any) -> None:
            return None

        def put(self, **kwargs: Any) -> None:
            put_calls.append(dict(kwargs))

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.invoker.get_global_llm_cache",
        lambda: _CacheMiss(),
    )
    executor = _ScriptedExecutor([AIResponse(ok=True, output="fresh-output", raw={"model": "m", "provider": "p"})])
    invoker = LLMInvoker(workspace="ws", enable_cache=True, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        prompt_fingerprint="fp-2",
        run_id="run4",
    )

    assert resp.error is None
    assert resp.content == "fresh-output"
    assert len(put_calls) == 1
    assert put_calls[0]["response_content"] == "fresh-output"
    assert len(rec.end) == 1
    assert rec.end[0]["metadata"]["cached"] is False
    assert rec.end[0]["metadata"]["source"] == "llm"


@pytest.mark.asyncio
async def test_call_end_event_prefers_provider_usage_over_text_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(profile)
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor(
        [
            AIResponse.success(
                output="short",
                usage=Usage(
                    prompt_tokens=123,
                    completion_tokens=45,
                    total_tokens=168,
                    estimated=False,
                ),
                model="provider-model",
                provider_id="provider-a",
                raw={"model": "provider-model", "provider": "provider-a"},
            )
        ]
    )
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run-provider-usage",
    )

    assert resp.error is None
    assert resp.token_estimate == 168
    assert resp.metadata["usage"] == {
        "cached_tokens": 0,
        "prompt_tokens": 123,
        "completion_tokens": 45,
        "total_tokens": 168,
        "estimated": False,
        "prompt_chars": 0,
        "completion_chars": 0,
    }
    assert len(rec.end) == 1
    assert rec.end[0]["prompt_tokens"] == 123
    assert rec.end[0]["completion_tokens"] == 45
    assert rec.end[0]["metadata"]["usage"]["estimated"] is False
    assert rec.end[0]["metadata"]["usage"]["total_tokens"] == 168


@pytest.mark.asyncio
async def test_call_end_tool_count_uses_run_ledger_envelope_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(provider_id="openai", model="gpt-test")
    prepared = _prepared(profile)
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor(
        [
            AIResponse.success(
                output="tool decision",
                model="gpt-test",
                provider_id="openai",
                raw={
                    "model": "gpt-test",
                    "provider": "openai",
                    # Legacy provider metadata can be stale; the Run Ledger
                    # envelope projection is the authoritative count source.
                    "native_tool_calls_count": 99,
                    "tool_calls": [
                        {
                            "id": "call_one",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        },
                        {
                            "id": "call_two",
                            "type": "function",
                            "function": {"name": "repo_rg", "arguments": "{}"},
                        },
                    ],
                },
            )
        ]
    )
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run-native-tool-count",
    )

    assert resp.error is None
    assert resp.metadata["native_tool_calls_count"] == 2
    assert len(resp.metadata["native_tool_call_envelopes"]) == 2
    assert len(rec.end) == 1
    assert rec.end[0]["tool_calls_count"] == 2
    assert rec.end[0]["metadata"]["native_tool_calls_count"] == 2


@pytest.mark.asyncio
async def test_call_start_event_persists_context_snapshot_before_emit(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(profile)
    captured_hash = "a1b2c3d4e5f6a7b8c9d0e1f2"
    store_calls: list[tuple[str | None, list[Any], str, str | None, dict[str, Any] | None]] = []

    async def _fake_store(
        workspace: str | None,
        messages: list[Any],
        trace_id: str,
        call_id: str | None = None,
        provider_request_snapshot: dict[str, Any] | None = None,
    ) -> str:
        store_calls.append((workspace, list(messages), trace_id, call_id, provider_request_snapshot))
        return captured_hash

    monkeypatch.setattr(AIExecutor, "_store_context_messages", _fake_store)
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor([AIResponse.success(output="fresh-output", raw={"model": "m", "provider": "p"})])
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run-context-start",
    )

    assert resp.error is None
    assert len(rec.start) == 1
    assert store_calls[0][:4] == ("ws", prepared.messages, "run-context-start", rec.start[0]["call_id"])
    assert store_calls[0][4]
    assert store_calls[0][4]["schema_version"] == "llm.provider_request_snapshot.v1"
    assert store_calls[0][4]["message_count"] == len(prepared.messages)
    assert store_calls[0][4]["final_request_evidence_coverage"]["included_refs"] == ["final_provider_request"]
    assert store_calls[0][4]["final_request_evidence_coverage"]["redaction_safety"]["message_content_embedded"] is False
    assert executor.requests[0].context["context_snapshot_ref"] == captured_hash
    assert rec.start[0]["metadata"]["context_snapshot_ref"] == captured_hash
    assert rec.end[0]["metadata"]["context_snapshot_ref"] == captured_hash


@pytest.mark.asyncio
async def test_call_start_context_snapshot_store_failure_clears_stale_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(profile)
    if isinstance(prepared.ai_request.context, dict):
        prepared.ai_request.context["context_snapshot_ref"] = "stale-ref"
        prepared.ai_request.context["context_snapshot_degraded"] = {"code": "OLD", "reason": "old"}

    async def _failing_store(
        _workspace: str | None,
        _messages: list[Any],
        _trace_id: str,
        _call_id: str | None = None,
        _provider_request_snapshot: dict[str, Any] | None = None,
    ) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(AIExecutor, "_store_context_messages", _failing_store)
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor([AIResponse.success(output="fresh-output", raw={"model": "m", "provider": "p"})])
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run-context-start-degraded",
    )

    assert resp.error is None
    assert len(rec.start) == 1
    start_metadata = rec.start[0]["metadata"]
    assert start_metadata.get("context_snapshot_ref") is None
    assert start_metadata["context_snapshot_degraded"]["code"] == "CONTEXT_STORE_WRITE_FAILED"
    assert start_metadata["context_snapshot_degraded"]["exception_type"] == "OSError"
    assert "OLD" not in str(start_metadata["context_snapshot_degraded"])
    assert executor.requests[0].context.get("context_snapshot_ref") is None
    assert resp.metadata["context_snapshot_degraded_reason"] == "context_snapshot_store_failure"


@pytest.mark.asyncio
async def test_call_end_event_surfaces_context_snapshot_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(profile)
    if isinstance(prepared.ai_request.context, dict):
        prepared.ai_request.context["context_snapshot_ref"] = "stale-ref"
        prepared.ai_request.context["context_snapshot_degraded"] = {"code": "OLD", "reason": "old"}

    async def _failing_store(
        _workspace: str | None,
        _messages: list[Any],
        _trace_id: str,
        _call_id: str | None = None,
        _provider_request_snapshot: dict[str, Any] | None = None,
    ) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(AIExecutor, "_store_context_messages", _failing_store)
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor([AIResponse.success(output="fresh-output", raw={"model": "m", "provider": "p"})])
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run-context-degraded",
    )

    assert resp.error is None
    assert resp.metadata["context_snapshot_degraded"]["code"] == "CONTEXT_STORE_WRITE_FAILED"
    assert resp.metadata["context_snapshot_degraded_reason"] == "context_snapshot_store_failure"
    assert len(rec.end) == 1
    metadata = rec.end[0]["metadata"]
    assert metadata["context_snapshot_degraded"]["exception_type"] == "OSError"
    assert metadata["context_snapshot_degraded_reason"] == "context_snapshot_store_failure"
    assert metadata.get("context_snapshot_ref") is None


# ---------------------------------------------------------------------------
# call: fallback ladder rungs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_tool_text_fallback_rung_is_retired(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(profile, native_tool_schemas=[{"name": "write_file"}])
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor(
        [
            AIResponse(ok=False, error="Unknown field: tools", raw={}),
            AIResponse(ok=True, output="text-fallback-ok", raw={"model": "m", "provider": "p"}),
        ]
    )
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx({"allow_native_tool_text_fallback": True}),
        run_id="run5",
    )

    assert resp.error is not None
    assert resp.content == ""
    assert len(executor.requests) == 1
    assert rec.end == []
    assert len(rec.error) == 1
    _assert_final_request_audit(resp.metadata)


@pytest.mark.asyncio
async def test_response_format_fallback_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(
        profile,
        native_response_format={"type": "json_schema"},
        response_format_mode="native_json_schema",
    )
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor(
        [
            AIResponse(ok=False, error="response_format is not allowed", raw={}),
            AIResponse(ok=True, output="rf-fallback-ok", raw={"model": "m", "provider": "p"}),
        ]
    )
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run6",
    )

    assert resp.error is None
    assert resp.content == "rf-fallback-ok"
    assert len(executor.requests) == 2
    assert len(rec.end) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_error",
    [
        "Empty visible output (reasoning truncated)",
        (
            "Empty visible output from MiniMax stream (thinking_chars=28642, chunks=146, "
            "finish_reason=length) — reasoning exhausted max_tokens even after budget doubling"
        ),
    ],
)
async def test_reasoning_truncation_reask_rung(
    monkeypatch: pytest.MonkeyPatch,
    response_error: str,
) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile()
    prepared = _prepared(profile)
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor(
        [
            AIResponse(ok=False, error=response_error, raw={}),
            AIResponse(ok=True, output="reask-ok", raw={"model": "m", "provider": "p"}),
        ]
    )
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run7",
    )

    assert resp.error is None
    assert resp.content == "reask-ok"
    assert len(executor.requests) == 2
    assert executor.requests[1].options["max_tokens"] == 8000
    assert executor.requests[1].context["reasoning_truncation_retry"] is True
    assert "最小化推理" in executor.requests[1].input
    assert len(rec.end) == 1


@pytest.mark.asyncio
async def test_failure_no_fallback_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="scout")  # role that does not allow binding fallback
    prepared = _prepared(profile)
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor([AIResponse(ok=False, error="unauthorized: invalid api key", raw={})])
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run8",
    )

    assert resp.error == "unauthorized: invalid api key"
    assert resp.error_category == "auth"
    assert len(rec.error) == 1
    assert rec.error[0]["metadata"]["native_tool_calling_fallback"] is False
    assert rec.error[0]["metadata"]["native_response_format_fallback"] is False
    assert rec.end == []


# ---------------------------------------------------------------------------
# call: success normalization / ok=True+error-string rule / exception arms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ok_true_with_error_string_counts_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="scout")
    prepared = _prepared(profile)
    _patch_prepare(monkeypatch, prepared)
    # ok=True but a non-empty error string -> treated as failure
    executor = _ScriptedExecutor([AIResponse(ok=True, output="", error="provider hiccup", raw={})])
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run9",
    )

    assert resp.error == "provider hiccup"
    assert resp.content == ""
    assert len(rec.error) == 1


@pytest.mark.asyncio
async def test_response_normalizer_recovery_on_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="scout")
    prepared = _prepared(profile)
    _patch_prepare(monkeypatch, prepared)
    # Empty output but raw payload present -> ResponseNormalizer.extract_text recovery
    executor = _ScriptedExecutor(
        [AIResponse(ok=True, output="", raw={"choices": [{"message": {"content": "recovered text"}}]})]
    )
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run10",
    )

    assert resp.error is None
    assert resp.content == "recovered text"


@pytest.mark.asyncio
async def test_call_cancelled_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="scout")
    prepared = _prepared(profile)
    _patch_prepare(monkeypatch, prepared)

    class _CancelExecutor:
        async def invoke(self, _request: AIRequest) -> AIResponse:
            raise asyncio.CancelledError

    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=cast(Any, _CancelExecutor()))

    with pytest.raises(asyncio.CancelledError):
        await invoker.call(
            profile=profile,
            system_prompt="sys",
            context=_ctx(None),
            run_id="run11",
        )

    # cancel error event emitted before re-raise
    assert len(rec.error) == 1
    assert rec.error[0]["error_message"] == "call_cancelled"
    assert rec.error[0]["metadata"]["error_type"] == "CancelledError"
    _assert_final_request_audit(rec.error[0]["metadata"])


@pytest.mark.asyncio
async def test_call_value_error_arm_returns_response(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="scout")
    prepared = _prepared(profile)
    _patch_prepare(monkeypatch, prepared)

    class _RaisingExecutor:
        async def invoke(self, _request: AIRequest) -> AIResponse:
            raise ValueError("boom")

    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=cast(Any, _RaisingExecutor()))

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run12",
    )

    assert resp.error == "LLM call failed: boom"
    assert len(rec.error) == 1
    assert rec.error[0]["metadata"]["error_type"] == "ValueError"
    _assert_final_request_audit(rec.error[0]["metadata"])
    _assert_final_request_audit(resp.metadata)


@pytest.mark.asyncio
async def test_call_runtime_error_arm_returns_response(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="scout")
    prepared = _prepared(profile)
    _patch_prepare(monkeypatch, prepared)

    class _RaisingExecutor:
        async def invoke(self, _request: AIRequest) -> AIResponse:
            raise RuntimeError("rt-boom")

    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=cast(Any, _RaisingExecutor()))

    resp = await invoker.call(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        run_id="run13",
    )

    assert resp.error == "LLM call failed: rt-boom"
    assert len(rec.error) == 1
    assert rec.error[0]["metadata"]["error_type"] == "RuntimeError"
    _assert_final_request_audit(rec.error[0]["metadata"])
    _assert_final_request_audit(resp.metadata)


# ---------------------------------------------------------------------------
# call_structured
# ---------------------------------------------------------------------------


class _Model:
    """Minimal pydantic-like model for structured tests."""

    def __init__(self, **data: Any) -> None:
        self._data = dict(data)

    def model_dump(self) -> dict[str, Any]:
        return dict(self._data)

    def model_dump_json(self) -> str:
        import json

        return json.dumps(self._data)


@pytest.mark.asyncio
async def test_structured_native_response_format_success(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="architect")
    prepared = _prepared(
        profile,
        native_response_format={"type": "json_schema"},
        response_format_mode="native_json_schema",
        response_model=_Model,
    )
    _patch_prepare(monkeypatch, prepared)
    executor = _ScriptedExecutor([AIResponse(ok=True, output='{"a": 1}', raw={"model": "m"})])
    # Force the instructor branch off so the native path is the success path.
    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.invoker.INSTRUCTOR_AVAILABLE",
        False,
    )
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call_structured(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        response_model=_Model,
        run_id="rs1",
    )

    assert isinstance(resp, StructuredLLMResponse)
    assert resp.error is None
    assert resp.data == {"a": 1}
    assert resp.metadata["native_response_format"] is True
    assert len(rec.end) == 1
    assert rec.end[0]["metadata"]["native_response_format"] is True


@pytest.mark.asyncio
async def test_structured_call_start_persists_context_snapshot_before_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="architect")
    prepared = _prepared(profile, response_model=_Model)
    captured_hash = "abcdef123456abcdef123456"
    store_calls: list[tuple[str | None, list[Any], str, str | None, dict[str, Any] | None]] = []

    async def _fake_store(
        workspace: str | None,
        messages: list[Any],
        trace_id: str,
        call_id: str | None = None,
        provider_request_snapshot: dict[str, Any] | None = None,
    ) -> str:
        store_calls.append((workspace, list(messages), trace_id, call_id, provider_request_snapshot))
        return captured_hash

    monkeypatch.setattr(AIExecutor, "_store_context_messages", _fake_store)
    _patch_prepare(monkeypatch, prepared)
    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.invoker.INSTRUCTOR_AVAILABLE",
        False,
    )
    executor = _ScriptedExecutor([AIResponse(ok=True, output='{"a": 1}', raw={"model": "m"})])
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call_structured(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        response_model=_Model,
        run_id="rs-structured-context",
    )

    assert resp.error is None
    assert store_calls[0][:4] == ("ws", prepared.messages, "rs-structured-context", rec.start[0]["call_id"])
    assert store_calls[0][4]
    assert store_calls[0][4]["schema_version"] == "llm.provider_request_snapshot.v1"
    assert store_calls[0][4]["message_count"] == len(prepared.messages)
    assert store_calls[0][4]["final_request_evidence_coverage"]["included_refs"] == ["final_provider_request"]
    assert store_calls[0][4]["final_request_evidence_coverage"]["redaction_safety"]["message_content_embedded"] is False
    assert prepared.ai_request.context["context_snapshot_ref"] == captured_hash
    assert rec.start[0]["metadata"]["context_snapshot_ref"] == captured_hash
    assert rec.end[0]["metadata"]["context_snapshot_ref"] == captured_hash


@pytest.mark.asyncio
async def test_structured_native_rf_unsupported_falls_through_to_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="architect")
    prepared = _prepared(
        profile,
        native_response_format={"type": "json_schema"},
        response_format_mode="native_json_schema",
        response_model=_Model,
    )
    _patch_prepare(monkeypatch, prepared)
    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.invoker.INSTRUCTOR_AVAILABLE",
        False,
    )
    # First (native rf) returns unsupported -> swallowed; second (fallback) succeeds.
    executor = _ScriptedExecutor(
        [
            AIResponse(ok=False, error="response_format is not supported", raw={}),
            AIResponse(ok=True, output='{"b": 2}', raw={"model": "m"}),
        ]
    )
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call_structured(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        response_model=_Model,
        run_id="rs2",
    )

    assert resp.error is None
    assert resp.data == {"b": 2}
    assert len(executor.requests) == 2
    assert rec.end[0]["metadata"]["instructor_used"] is False


@pytest.mark.asyncio
async def test_structured_fallback_not_ok_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="architect")
    prepared = _prepared(profile, response_model=_Model)
    _patch_prepare(monkeypatch, prepared)
    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.invoker.INSTRUCTOR_AVAILABLE",
        False,
    )
    executor = _ScriptedExecutor([AIResponse(ok=False, error="server exploded", raw={})])
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call_structured(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        response_model=_Model,
        run_id="rs3",
    )

    assert resp.error == "server exploded"
    assert resp.data == {}
    assert len(rec.error) == 1
    assert rec.error[0]["metadata"]["structured"] is True


@pytest.mark.asyncio
async def test_structured_fallback_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="architect")
    prepared = _prepared(profile, response_model=_Model)
    _patch_prepare(monkeypatch, prepared)
    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.invoker.INSTRUCTOR_AVAILABLE",
        False,
    )

    class _StrictModel:
        def __init__(self, **_data: Any) -> None:
            raise ValueError("validation failed")

    executor = _ScriptedExecutor([AIResponse(ok=True, output='{"a": 1}', raw={"model": "m"})])
    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=executor)

    resp = await invoker.call_structured(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        response_model=_StrictModel,
        run_id="rs4",
    )

    assert resp.error is not None
    assert resp.error.startswith("Failed to parse structured output")
    assert resp.error_category == "validation_fail"
    assert resp.validation_errors
    assert len(rec.error) == 1


@pytest.mark.asyncio
async def test_structured_cancelled_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="architect")
    prepared = _prepared(profile, response_model=_Model)
    _patch_prepare(monkeypatch, prepared)
    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.invoker.INSTRUCTOR_AVAILABLE",
        False,
    )

    class _CancelExecutor:
        async def invoke(self, _request: AIRequest) -> AIResponse:
            raise asyncio.CancelledError

    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=cast(Any, _CancelExecutor()))

    with pytest.raises(asyncio.CancelledError):
        await invoker.call_structured(
            profile=profile,
            system_prompt="sys",
            context=_ctx(None),
            response_model=_Model,
            run_id="rs5",
        )

    assert len(rec.error) == 1
    assert rec.error[0]["error_message"] == "structured_call_cancelled"
    assert rec.error[0]["metadata"]["structured"] is True
    _assert_final_request_audit(rec.error[0]["metadata"])


@pytest.mark.asyncio
async def test_structured_runtime_error_arm_returns_response(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _EventRecorder()
    rec.install(monkeypatch)
    profile = _profile(role_id="architect")
    prepared = _prepared(profile, response_model=_Model)
    _patch_prepare(monkeypatch, prepared)
    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.invoker.INSTRUCTOR_AVAILABLE",
        False,
    )

    class _RaisingExecutor:
        async def invoke(self, _request: AIRequest) -> AIResponse:
            raise RuntimeError("struct-boom")

    invoker = LLMInvoker(workspace="ws", enable_cache=False, executor=cast(Any, _RaisingExecutor()))

    resp = await invoker.call_structured(
        profile=profile,
        system_prompt="sys",
        context=_ctx(None),
        response_model=_Model,
        run_id="rs6",
    )

    assert resp.error == "Structured LLM call failed: struct-boom"
    assert len(rec.error) == 1
    assert rec.error[0]["metadata"]["structured"] is True
    _assert_final_request_audit(rec.error[0]["metadata"])
    _assert_final_request_audit(resp.metadata)
