"""Role-binding fallback coverage for provider quota failures."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.cells.roles.profile.public.service import RoleProfile
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse, TaskType
from polaris.kernelone.llm.runtime_config import (
    RoleBindingSlot,
    get_role_binding_override,
    get_role_model,
    reset_runtime_config_manager,
    set_runtime_config_manager,
)


class _RuntimeConfig:
    def __init__(self, slots: tuple[RoleBindingSlot, ...]) -> None:
        self._slots = slots

    def get_role_binding_slots(self, role_id: str) -> tuple[RoleBindingSlot, ...]:
        if self._slots and role_id == self._slots[0].role_id:
            return self._slots
        return ()

    def get_role_binding_candidates(self, role_id: str) -> tuple[RoleBindingSlot, ...]:
        return self.get_role_binding_slots(role_id)

    def get_role_config(self, _role_id: str) -> None:
        return None

    def get_role_model(self, role_id: str) -> tuple[str, str]:
        override = get_role_binding_override(role_id)
        if override is not None:
            return str(override["provider_id"]), str(override["model"])
        if self._slots and role_id == self._slots[0].role_id:
            first = self._slots[0]
            return first.provider_id, first.model
        return "", ""


class _Executor:
    def __init__(self, first_error: str) -> None:
        self.first_error = first_error
        self.calls: list[tuple[str, str]] = []

    async def invoke(self, request: AIRequest, *, physical_dispatch_port: object | None = None) -> AIResponse:
        assert physical_dispatch_port is None
        provider_id, model = get_role_model(request.role)
        self.calls.append((provider_id, model))
        if len(self.calls) == 1:
            return AIResponse(
                ok=False,
                error=self.first_error,
                provider_id=provider_id,
                model=model,
                raw={"provider_id": provider_id, "model": model},
            )
        return AIResponse(
            ok=True,
            output="fallback ok",
            provider_id=provider_id,
            model=model,
            raw={"provider_id": provider_id, "model": model, "output": "fallback ok"},
        )


class _ProviderFailingExecutor:
    def __init__(self, failing_provider_id: str, error: str) -> None:
        self.failing_provider_id = failing_provider_id
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def invoke(self, request: AIRequest, *, physical_dispatch_port: object | None = None) -> AIResponse:
        assert physical_dispatch_port is None
        provider_id, model = get_role_model(request.role)
        self.calls.append((provider_id, model))
        if provider_id == self.failing_provider_id:
            return AIResponse(
                ok=False,
                error=self.error,
                provider_id=provider_id,
                model=model,
                raw={"provider_id": provider_id, "model": model},
            )
        return AIResponse(
            ok=True,
            output=f"ok from {provider_id}",
            provider_id=provider_id,
            model=model,
            raw={"provider_id": provider_id, "model": model, "output": f"ok from {provider_id}"},
        )


class _RequestProviderRecordingExecutor:
    def __init__(self, first_error: str) -> None:
        self.first_error = first_error
        self.calls: list[tuple[str | None, str | None]] = []

    async def invoke(self, request: AIRequest, *, physical_dispatch_port: object | None = None) -> AIResponse:
        assert physical_dispatch_port is None
        self.calls.append((request.provider_id, request.model))
        if len(self.calls) == 1:
            return AIResponse(
                ok=False,
                error=self.first_error,
                provider_id=request.provider_id,
                model=request.model,
                raw={"provider_id": request.provider_id, "model": request.model},
            )
        return AIResponse(
            ok=True,
            output="fallback ok",
            provider_id=request.provider_id,
            model=request.model,
            raw={"provider_id": request.provider_id, "model": request.model, "output": "fallback ok"},
        )


class _RaisingThenOkExecutor:
    def __init__(self, first_error: str) -> None:
        self.first_error = first_error
        self.calls: list[tuple[str, str]] = []

    async def invoke(self, request: AIRequest, *, physical_dispatch_port: object | None = None) -> AIResponse:
        assert physical_dispatch_port is None
        provider_id, model = get_role_model(request.role)
        self.calls.append((provider_id, model))
        if len(self.calls) == 1:
            raise RuntimeError(self.first_error)
        return AIResponse(
            ok=True,
            output="fallback ok after exception",
            provider_id=provider_id,
            model=model,
            raw={"provider_id": provider_id, "model": model, "output": "fallback ok after exception"},
        )


class _ProviderFallbackThenRequiredToolExecutor:
    def __init__(self, first_error: str, *, raise_first: bool) -> None:
        self.first_error = first_error
        self.raise_first = raise_first
        self.calls: list[tuple[str, str, str]] = []

    async def invoke(self, request: AIRequest, *, physical_dispatch_port: object | None = None) -> AIResponse:
        assert physical_dispatch_port is None
        provider_id, model = get_role_model(request.role)
        options = request.options if isinstance(request.options, dict) else {}
        tool_choice = options.get("tool_choice")
        if isinstance(tool_choice, dict):
            function_block = tool_choice.get("function")
            tool_choice_label = (
                str(function_block.get("name") or "") if isinstance(function_block, dict) else str(tool_choice)
            )
        else:
            tool_choice_label = str(tool_choice or "")
        self.calls.append((provider_id, model, tool_choice_label))
        if len(self.calls) == 1:
            if self.raise_first:
                raise RuntimeError(self.first_error)
            return AIResponse(
                ok=False,
                error=self.first_error,
                provider_id=provider_id,
                model=model,
                raw={"provider_id": provider_id, "model": model},
            )
        if tool_choice_label != "none":
            return AIResponse(
                ok=True,
                output="I will write the requested file now.",
                provider_id=provider_id,
                model=model,
                raw={
                    "provider_id": provider_id,
                    "model": model,
                    "output": "I will write the requested file now.",
                },
            )
        output = '[{"name":"write_file","arguments":{"path":"package.json","content":"{\\"scripts\\":{}}"}}]'
        return AIResponse(
            ok=True,
            output=output,
            provider_id=provider_id,
            model=model,
            raw={"provider_id": provider_id, "model": model, "output": output},
        )


class _ProviderFallbackThenFailureExecutor:
    def __init__(self, first_error: str, fallback_error: str) -> None:
        self.first_error = first_error
        self.fallback_error = fallback_error
        self.calls: list[tuple[str, str, str]] = []

    async def invoke(self, request: AIRequest, *, physical_dispatch_port: object | None = None) -> AIResponse:
        assert physical_dispatch_port is None
        provider_id, model = get_role_model(request.role)
        options = request.options if isinstance(request.options, dict) else {}
        tool_choice = options.get("tool_choice")
        if isinstance(tool_choice, dict):
            function_block = tool_choice.get("function")
            tool_choice_label = (
                str(function_block.get("name") or "") if isinstance(function_block, dict) else str(tool_choice)
            )
        else:
            tool_choice_label = str(tool_choice or "")
        self.calls.append((provider_id, model, tool_choice_label))
        if len(self.calls) == 1:
            raise RuntimeError(self.first_error)
        raise RuntimeError(self.fallback_error)


class _PrimaryFailureThenTwoFallbackFailuresExecutor:
    def __init__(self, primary_error: str, text_fallback_error: str, native_fallback_error: str) -> None:
        self.primary_error = primary_error
        self.text_fallback_error = text_fallback_error
        self.native_fallback_error = native_fallback_error
        self.calls: list[tuple[str | None, str | None, str]] = []

    async def invoke(self, request: AIRequest, *, physical_dispatch_port: object | None = None) -> AIResponse:
        assert physical_dispatch_port is None
        options = request.options if isinstance(request.options, dict) else {}
        tool_choice = options.get("tool_choice")
        if isinstance(tool_choice, dict):
            function_block = tool_choice.get("function")
            tool_choice_label = (
                str(function_block.get("name") or "") if isinstance(function_block, dict) else str(tool_choice)
            )
        else:
            tool_choice_label = str(tool_choice or "")
        self.calls.append((request.provider_id, request.model, tool_choice_label))
        if len(self.calls) == 1:
            return AIResponse(
                ok=False,
                error=self.primary_error,
                provider_id=request.provider_id,
                model=request.model,
                raw={"provider_id": request.provider_id, "model": request.model},
            )
        if len(self.calls) == 2:
            return AIResponse(
                ok=False,
                error=self.text_fallback_error,
                provider_id=request.provider_id,
                model=request.model,
                raw={"provider_id": request.provider_id, "model": request.model},
            )
        return AIResponse(
            ok=False,
            error=self.native_fallback_error,
            provider_id=request.provider_id,
            model=request.model,
            raw={"provider_id": request.provider_id, "model": request.model},
        )


def _profile(
    provider_id: str = "minimax-primary",
    model: str = "MiniMax-M3",
    role_id: str = "pm",
) -> RoleProfile:
    return RoleProfile(
        role_id=role_id,
        display_name="PM",
        description="Project manager",
        provider_id=provider_id,
        model=model,
    )


def _prepared(profile: RoleProfile) -> PreparedLLMRequest:
    return PreparedLLMRequest(
        messages=[{"role": "user", "content": "build"}],
        input_text="build",
        context_result=SimpleNamespace(
            token_estimate=8,
            compression_strategy="none",
            compression_applied=False,
        ),
        context_summary="summary",
        request_options={"max_retries": 0},
        ai_request=AIRequest(
            task_type=TaskType.DIALOGUE,
            role=profile.role_id,
            input="build",
            options={"max_retries": 0},
            context={"workspace": ".", "mode": "chat"},
        ),
        native_tool_schemas=[],
        native_tool_mode="disabled",
        response_format_mode="plain_text",
    )


def _write_file_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    }


def _prepared_required_write(profile: RoleProfile) -> PreparedLLMRequest:
    tool_schema = _write_file_tool_schema()
    messages = [{"role": "user", "content": "TASK-1 target_files package.json Chief Engineer blueprint"}]
    options = {
        "max_retries": 0,
        "tools": [tool_schema],
        "tool_choice": {"type": "function", "function": {"name": "write_file"}},
    }
    context = {
        "workspace": ".",
        "mode": "chat",
        "chat_messages": messages,
        "required_tools": ["write_file"],
        "tool_contract": {"required_tools": ["write_file"]},
    }
    return PreparedLLMRequest(
        messages=messages,
        input_text="TASK-1 target_files package.json Chief Engineer blueprint",
        context_result=SimpleNamespace(
            token_estimate=32,
            compression_strategy="none",
            compression_applied=False,
        ),
        context_summary="summary",
        request_options=options,
        ai_request=AIRequest(
            task_type=TaskType.DIALOGUE,
            role=profile.role_id,
            input="TASK-1 target_files package.json Chief Engineer blueprint",
            options=options,
            context=context,
        ),
        native_tool_schemas=[tool_schema],
        native_tool_mode="native_tools",
        response_format_mode="plain_text",
    )


def test_profile_bound_request_for_evidence_pins_provider_without_mutating_original() -> None:
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        provider_id="minimax-primary",
        model="MiniMax-M3",
        input="build",
        context={"context_snapshot_ref": "ctx-primary"},
    )

    bound = LLMInvoker._profile_bound_request_for_evidence(
        request,
        _profile(
            provider_id="gemma-backup",
            model="gemma-4-12B-it-Q8_0",
            role_id="director",
        ),
    )

    assert bound is not request
    assert bound.provider_id == "gemma-backup"
    assert bound.model == "gemma-4-12B-it-Q8_0"
    assert bound.context == {"context_snapshot_ref": "ctx-primary"}
    assert bound.context is not request.context
    assert request.provider_id == "minimax-primary"
    assert request.model == "MiniMax-M3"


@pytest.fixture
def role_slots(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    reset_runtime_config_manager()
    set_runtime_config_manager(
        cast(
            Any,
            _RuntimeConfig(
                (
                    RoleBindingSlot(
                        role_id="pm",
                        provider_id="minimax-primary",
                        model="MiniMax-M3",
                        binding_id="pm:0:minimax-primary:MiniMax-M3",
                    ),
                    RoleBindingSlot(
                        role_id="pm",
                        provider_id="deepseek-backup",
                        model="DeepSeekV4-Pro",
                        binding_id="pm:1:deepseek-backup:DeepSeekV4-Pro",
                    ),
                )
            ),
        )
    )
    monkeypatch.setattr(LLMInvoker, "_emit_call_start_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_end_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_error_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_retry_event", lambda *_args, **_kwargs: None)
    yield
    reset_runtime_config_manager()


@pytest.mark.asyncio
async def test_pm_rate_limit_falls_back_to_next_same_role_binding(
    monkeypatch: pytest.MonkeyPatch, role_slots: None
) -> None:
    prepare_profiles: list[tuple[str, str]] = []

    async def _prepare(self: LLMRequestPreparer, **kwargs: Any) -> PreparedLLMRequest:
        profile = cast(RoleProfile, kwargs["profile"])
        prepare_profiles.append((profile.provider_id, profile.model))
        return _prepared(profile)

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", _prepare)
    executor = _Executor(
        "MiniMax API Error 2056: 已达到 Token Plan 用量上限：请升级 Token Plan 套餐或购买积分补充用量。"
    )
    invoker = LLMInvoker(workspace=".", enable_cache=False, executor=executor)

    response = await invoker.call(
        profile=_profile(),
        system_prompt="system",
        context=cast(Any, SimpleNamespace(task_id=None, context_override=None)),
        run_id="run_pm_fallback",
    )

    assert response.error is None
    assert response.content == "fallback ok"
    assert executor.calls == [
        ("minimax-primary", "MiniMax-M3"),
        ("deepseek-backup", "DeepSeekV4-Pro"),
    ]
    assert prepare_profiles == [
        ("minimax-primary", "MiniMax-M3"),
        ("deepseek-backup", "DeepSeekV4-Pro"),
    ]
    assert get_role_binding_override("pm") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_error",
    [
        "500 Server Error: Internal Server Error url: http://localhost:8189/v1/chat/completions",
        "circuit_open:57s_remaining",
    ],
)
async def test_director_retryable_provider_error_falls_back_to_next_binding(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: str,
) -> None:
    reset_runtime_config_manager()
    set_runtime_config_manager(
        cast(
            Any,
            _RuntimeConfig(
                (
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="openai_compat-local",
                        model="qwen3.6-27b-int4",
                        binding_id="director:0:openai_compat-local:qwen3.6-27b-int4",
                    ),
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="openai_compat-gpu0",
                        model="qwen3.6-27b-code-gpu0",
                        binding_id="director:1:openai_compat-gpu0:qwen3.6-27b-code-gpu0",
                    ),
                )
            ),
        )
    )
    prepare_profiles: list[tuple[str, str]] = []

    async def _prepare(self: LLMRequestPreparer, **kwargs: Any) -> PreparedLLMRequest:
        profile = cast(RoleProfile, kwargs["profile"])
        prepare_profiles.append((profile.provider_id, profile.model))
        return _prepared(profile)

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", _prepare)
    monkeypatch.setattr(LLMInvoker, "_emit_call_start_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_end_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_error_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_retry_event", lambda *_args, **_kwargs: None)

    executor = _Executor(provider_error)
    invoker = LLMInvoker(workspace=".", enable_cache=False, executor=executor)

    response = await invoker.call(
        profile=_profile(
            provider_id="openai_compat-local",
            model="qwen3.6-27b-int4",
            role_id="director",
        ),
        system_prompt="system",
        context=cast(Any, SimpleNamespace(task_id=None, context_override=None)),
        run_id="run_director_fallback",
    )

    assert response.error is None
    assert response.content == "fallback ok"
    assert executor.calls == [
        ("openai_compat-local", "qwen3.6-27b-int4"),
        ("openai_compat-gpu0", "qwen3.6-27b-code-gpu0"),
    ]
    assert prepare_profiles == [
        ("openai_compat-local", "qwen3.6-27b-int4"),
        ("openai_compat-gpu0", "qwen3.6-27b-code-gpu0"),
    ]
    assert get_role_binding_override("director") is None
    reset_runtime_config_manager()


@pytest.mark.asyncio
async def test_director_retryable_provider_exception_falls_back_to_next_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_runtime_config_manager()
    set_runtime_config_manager(
        cast(
            Any,
            _RuntimeConfig(
                (
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="minimax-primary",
                        model="MiniMax-M3",
                        binding_id="director:0:minimax-primary:MiniMax-M3",
                    ),
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="deepseek-backup",
                        model="DeepSeekV4-Pro",
                        binding_id="director:1:deepseek-backup:DeepSeekV4-Pro",
                    ),
                )
            ),
        )
    )
    prepare_profiles: list[tuple[str, str]] = []
    retry_decisions: list[str] = []

    async def _prepare(self: LLMRequestPreparer, **kwargs: Any) -> PreparedLLMRequest:
        profile = cast(RoleProfile, kwargs["profile"])
        prepare_profiles.append((profile.provider_id, profile.model))
        return _prepared(profile)

    def _record_retry(*_args: Any, **kwargs: Any) -> None:
        retry_decisions.append(str(kwargs.get("retry_decision") or ""))

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", _prepare)
    monkeypatch.setattr(LLMInvoker, "_emit_call_start_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_end_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_error_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_retry_event", _record_retry)

    executor = _RaisingThenOkExecutor(
        "429 Rate limited by https://api.minimaxi.com/anthropic/v1/messages after 4 retries: "
        '{"type":"error","error":{"type":"rate_limit_error","message":"Token Plan quota exhausted"}}'
    )
    invoker = LLMInvoker(workspace=".", enable_cache=False, executor=executor)

    response = await invoker.call(
        profile=_profile(
            provider_id="minimax-primary",
            model="MiniMax-M3",
            role_id="director",
        ),
        system_prompt="system",
        context=cast(Any, SimpleNamespace(task_id=None, context_override=None)),
        run_id="run_director_exception_fallback",
    )

    assert response.error is None
    assert response.content == "fallback ok after exception"
    assert executor.calls == [
        ("minimax-primary", "MiniMax-M3"),
        ("deepseek-backup", "DeepSeekV4-Pro"),
    ]
    assert prepare_profiles == [
        ("minimax-primary", "MiniMax-M3"),
        ("deepseek-backup", "DeepSeekV4-Pro"),
    ]
    assert retry_decisions == ["role_binding_fallback", "role_binding_fallback_request"]
    assert get_role_binding_override("director") is None
    reset_runtime_config_manager()


@pytest.mark.asyncio
async def test_director_fallback_invoke_overrides_stale_request_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_runtime_config_manager()
    set_runtime_config_manager(
        cast(
            Any,
            _RuntimeConfig(
                (
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="minimax-primary",
                        model="MiniMax-M3",
                        binding_id="director:0:minimax-primary:MiniMax-M3",
                    ),
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="gemma-backup",
                        model="gemma-4-12B-it-Q8_0",
                        binding_id="director:backup:_director_backup:0:gemma-backup:gemma-4-12B-it-Q8_0",
                    ),
                )
            ),
        )
    )

    async def _prepare(self: LLMRequestPreparer, **kwargs: Any) -> PreparedLLMRequest:
        profile = cast(RoleProfile, kwargs["profile"])
        prepared = _prepared(profile)
        prepared.ai_request.provider_id = "minimax-primary"
        prepared.ai_request.model = "MiniMax-M3"
        return prepared

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", _prepare)
    monkeypatch.setattr(LLMInvoker, "_emit_call_start_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_end_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_error_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_retry_event", lambda *_args, **_kwargs: None)

    executor = _RequestProviderRecordingExecutor(
        "429 Rate limited by https://api.minimaxi.com/anthropic/v1/messages after 4 retries"
    )
    invoker = LLMInvoker(workspace=".", enable_cache=False, executor=executor)

    response = await invoker.call(
        profile=_profile(
            provider_id="minimax-primary",
            model="MiniMax-M3",
            role_id="director",
        ),
        system_prompt="system",
        context=cast(Any, SimpleNamespace(task_id=None, context_override=None)),
        run_id="run_director_stale_request_provider_fallback",
    )

    assert response.error is None
    assert executor.calls == [
        ("minimax-primary", "MiniMax-M3"),
        ("gemma-backup", "gemma-4-12B-it-Q8_0"),
    ]
    assert get_role_binding_override("director") is None
    reset_runtime_config_manager()


@pytest.mark.asyncio
async def test_director_exception_fallback_reports_last_fallback_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_runtime_config_manager()
    set_runtime_config_manager(
        cast(
            Any,
            _RuntimeConfig(
                (
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="minimax-primary",
                        model="MiniMax-M3",
                        binding_id="director:0:minimax-primary:MiniMax-M3",
                    ),
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="kimi-backup",
                        model="kimi-for-coding",
                        binding_id="director:1:kimi-backup:kimi-for-coding",
                    ),
                )
            ),
        )
    )
    prepare_profiles: list[tuple[str, str]] = []
    retry_decisions: list[str] = []
    error_events: list[dict[str, Any]] = []

    async def _prepare(self: LLMRequestPreparer, **kwargs: Any) -> PreparedLLMRequest:
        profile = cast(RoleProfile, kwargs["profile"])
        prepare_profiles.append((profile.provider_id, profile.model))
        prepared = _prepared_required_write(profile)
        prepared.ai_request.provider_id = "minimax-primary"
        prepared.ai_request.model = "MiniMax-M3"
        return prepared

    def _record_retry(*_args: Any, **kwargs: Any) -> None:
        retry_decisions.append(str(kwargs.get("retry_decision") or ""))

    def _record_error(*_args: Any, **kwargs: Any) -> None:
        error_events.append(dict(kwargs))

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", _prepare)
    monkeypatch.setattr(LLMInvoker, "_emit_call_start_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_end_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_error_event", _record_error)
    monkeypatch.setattr(LLMInvoker, "_emit_call_retry_event", _record_retry)

    executor = _ProviderFallbackThenFailureExecutor(
        "429 Rate limited by https://api.minimaxi.com/anthropic/v1/messages after 4 retries",
        "Timeout contacting kimi-backup during required_tool_text_fallback",
    )
    invoker = LLMInvoker(workspace=".", enable_cache=False, executor=executor)

    response = await invoker.call(
        profile=_profile(
            provider_id="minimax-primary",
            model="MiniMax-M3",
            role_id="director",
        ),
        system_prompt="system",
        context=cast(Any, SimpleNamespace(task_id=None, context_override=None)),
        run_id="run_director_fallback_failure",
    )

    assert "kimi-backup" in str(response.error)
    assert "minimaxi" not in str(response.error).lower()
    assert response.error_category == "timeout"
    assert executor.calls == [
        ("minimax-primary", "MiniMax-M3", "write_file"),
        ("kimi-backup", "kimi-for-coding", "none"),
    ]
    assert prepare_profiles == [
        ("minimax-primary", "MiniMax-M3"),
        ("kimi-backup", "kimi-for-coding"),
    ]
    assert retry_decisions == [
        "role_binding_fallback",
        "required_tool_text_fallback",
    ]
    assert error_events
    error_metadata = error_events[-1]["metadata"]
    assert error_metadata["provider"] == "kimi-backup"
    assert error_metadata["provider_id"] == "kimi-backup"
    assert error_metadata["model"] == "kimi-for-coding"
    assert get_role_binding_override("director") is None
    reset_runtime_config_manager()


@pytest.mark.asyncio
async def test_director_response_fallback_reports_last_native_fallback_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_runtime_config_manager()
    set_runtime_config_manager(
        cast(
            Any,
            _RuntimeConfig(
                (
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="minimax-primary",
                        model="MiniMax-M3",
                        binding_id="director:0:minimax-primary:MiniMax-M3",
                    ),
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="kimi-backup",
                        model="kimi-for-coding",
                        binding_id="director:1:kimi-backup:kimi-for-coding",
                    ),
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="gemma-backup",
                        model="gemma-4-12B-it-Q8_0",
                        binding_id="director:backup:_director_backup:0:gemma-backup:gemma-4-12B-it-Q8_0",
                    ),
                )
            ),
        )
    )
    prepare_profiles: list[tuple[str, str]] = []
    retry_decisions: list[str] = []
    error_events: list[dict[str, Any]] = []

    async def _prepare(self: LLMRequestPreparer, **kwargs: Any) -> PreparedLLMRequest:
        profile = cast(RoleProfile, kwargs["profile"])
        prepare_profiles.append((profile.provider_id, profile.model))
        prepared = _prepared_required_write(profile)
        prepared.ai_request.provider_id = "minimax-primary"
        prepared.ai_request.model = "MiniMax-M3"
        return prepared

    def _record_retry(*_args: Any, **kwargs: Any) -> None:
        retry_decisions.append(str(kwargs.get("retry_decision") or ""))

    def _record_error(*_args: Any, **kwargs: Any) -> None:
        error_events.append(dict(kwargs))

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", _prepare)
    monkeypatch.setattr(LLMInvoker, "_emit_call_start_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_end_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_error_event", _record_error)
    monkeypatch.setattr(LLMInvoker, "_emit_call_retry_event", _record_retry)

    executor = _PrimaryFailureThenTwoFallbackFailuresExecutor(
        "429 Rate limited by https://api.minimaxi.com/anthropic/v1/messages after 4 retries",
        "Timeout contacting kimi-backup during required_tool_text_fallback",
        "502 Server Error from http://127.0.0.1:8000/v1/chat/completions: (empty)",
    )
    invoker = LLMInvoker(workspace=".", enable_cache=False, executor=executor)

    response = await invoker.call(
        profile=_profile(
            provider_id="minimax-primary",
            model="MiniMax-M3",
            role_id="director",
        ),
        system_prompt="system",
        context=cast(Any, SimpleNamespace(task_id=None, context_override=None)),
        run_id="run_director_two_fallback_failures",
    )

    assert "127.0.0.1:8000" in str(response.error)
    assert "minimaxi" not in str(response.error).lower()
    assert "kimi-backup" not in str(response.error)
    assert response.error_category == "network"
    assert executor.calls == [
        ("minimax-primary", "MiniMax-M3", "write_file"),
        ("kimi-backup", "kimi-for-coding", "none"),
        ("gemma-backup", "gemma-4-12B-it-Q8_0", "write_file"),
    ]
    assert prepare_profiles == [
        ("minimax-primary", "MiniMax-M3"),
        ("kimi-backup", "kimi-for-coding"),
        ("gemma-backup", "gemma-4-12B-it-Q8_0"),
    ]
    assert retry_decisions == [
        "role_binding_fallback",
        "required_tool_text_fallback",
        "role_binding_fallback",
        "role_binding_fallback_request",
    ]
    assert error_events
    error_metadata = error_events[-1]["metadata"]
    assert error_events[-1]["error_message"] == response.error
    assert error_metadata["provider"] == "gemma-backup"
    assert error_metadata["provider_id"] == "gemma-backup"
    assert error_metadata["model"] == "gemma-4-12B-it-Q8_0"
    assert get_role_binding_override("director") is None
    reset_runtime_config_manager()


@pytest.mark.asyncio
@pytest.mark.parametrize("raise_first", [False, True])
async def test_director_role_binding_fallback_continues_required_tool_retry_on_selected_binding(
    monkeypatch: pytest.MonkeyPatch,
    raise_first: bool,
) -> None:
    reset_runtime_config_manager()
    set_runtime_config_manager(
        cast(
            Any,
            _RuntimeConfig(
                (
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="minimax-primary",
                        model="MiniMax-M3",
                        binding_id="director:0:minimax-primary:MiniMax-M3",
                    ),
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="deepseek-backup",
                        model="DeepSeekV4-Pro",
                        binding_id="director:1:deepseek-backup:DeepSeekV4-Pro",
                    ),
                )
            ),
        )
    )
    prepare_profiles: list[tuple[str, str]] = []
    retry_decisions: list[str] = []

    async def _prepare(self: LLMRequestPreparer, **kwargs: Any) -> PreparedLLMRequest:
        profile = cast(RoleProfile, kwargs["profile"])
        prepare_profiles.append((profile.provider_id, profile.model))
        return _prepared_required_write(profile)

    def _record_retry(*_args: Any, **kwargs: Any) -> None:
        retry_decisions.append(str(kwargs.get("retry_decision") or ""))

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", _prepare)
    monkeypatch.setattr(LLMInvoker, "_emit_call_start_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_end_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_error_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_retry_event", _record_retry)

    executor = _ProviderFallbackThenRequiredToolExecutor(
        "429 Rate limited by https://api.minimaxi.com/anthropic/v1/messages after 4 retries",
        raise_first=raise_first,
    )
    invoker = LLMInvoker(workspace=".", enable_cache=False, executor=executor)

    response = await invoker.call(
        profile=_profile(
            provider_id="minimax-primary",
            model="MiniMax-M3",
            role_id="director",
        ),
        system_prompt="system",
        context=cast(Any, SimpleNamespace(task_id=None, context_override=None)),
        run_id=f"run_director_required_tool_fallback_{raise_first}",
    )

    assert response.error is None
    assert "write_file" in str(response.tool_calls)
    assert executor.calls == [
        ("minimax-primary", "MiniMax-M3", "write_file"),
        ("deepseek-backup", "DeepSeekV4-Pro", "none"),
    ]
    assert prepare_profiles == [
        ("minimax-primary", "MiniMax-M3"),
        ("deepseek-backup", "DeepSeekV4-Pro"),
    ]
    assert retry_decisions == [
        "role_binding_fallback",
        "required_tool_text_fallback",
    ]
    assert get_role_binding_override("director") is None
    reset_runtime_config_manager()


@pytest.mark.asyncio
async def test_director_retryable_provider_error_cools_binding_for_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_runtime_config_manager()
    set_runtime_config_manager(
        cast(
            Any,
            _RuntimeConfig(
                (
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="openai_compat-local",
                        model="qwen3.6-27b-int4",
                        binding_id="director:0:openai_compat-local:qwen3.6-27b-int4",
                    ),
                    RoleBindingSlot(
                        role_id="director",
                        provider_id="openai_compat-gpu0",
                        model="qwen3.6-27b-code-gpu0",
                        binding_id="director:1:openai_compat-gpu0:qwen3.6-27b-code-gpu0",
                    ),
                )
            ),
        )
    )

    async def _prepare(self: LLMRequestPreparer, **kwargs: Any) -> PreparedLLMRequest:
        profile = cast(RoleProfile, kwargs["profile"])
        return _prepared(profile)

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", _prepare)
    monkeypatch.setattr(LLMInvoker, "_emit_call_start_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_end_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_error_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LLMInvoker, "_emit_call_retry_event", lambda *_args, **_kwargs: None)

    executor = _ProviderFailingExecutor(
        "openai_compat-local",
        "500 Server Error: Internal Server Error url: http://localhost:8189/v1/chat/completions",
    )
    invoker = LLMInvoker(workspace=".", enable_cache=False, executor=executor)

    for run_id in ("run_director_cooldown_1", "run_director_cooldown_2"):
        response = await invoker.call(
            profile=_profile(
                provider_id="openai_compat-local",
                model="qwen3.6-27b-int4",
                role_id="director",
            ),
            system_prompt="system",
            context=cast(Any, SimpleNamespace(task_id=None, context_override=None)),
            run_id=run_id,
        )
        assert response.error is None

    assert executor.calls == [
        ("openai_compat-local", "qwen3.6-27b-int4"),
        ("openai_compat-gpu0", "qwen3.6-27b-code-gpu0"),
        ("openai_compat-gpu0", "qwen3.6-27b-code-gpu0"),
    ]
    reset_runtime_config_manager()


@pytest.mark.asyncio
async def test_pm_auth_error_does_not_fallback(monkeypatch: pytest.MonkeyPatch, role_slots: None) -> None:
    async def _prepare(self: LLMRequestPreparer, **kwargs: Any) -> PreparedLLMRequest:
        return _prepared(cast(RoleProfile, kwargs["profile"]))

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", _prepare)
    executor = _Executor("unauthorized: invalid api key")
    invoker = LLMInvoker(workspace=".", enable_cache=False, executor=executor)

    response = await invoker.call(
        profile=_profile(),
        system_prompt="system",
        context=cast(Any, SimpleNamespace(task_id=None, context_override=None)),
        run_id="run_pm_no_fallback",
    )

    assert response.error is not None
    assert response.error_category == "auth"
    assert executor.calls == [("minimax-primary", "MiniMax-M3")]
    assert get_role_binding_override("pm") is None
