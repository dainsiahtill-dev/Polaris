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

    async def invoke(self, request: AIRequest) -> AIResponse:
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

    async def invoke(self, request: AIRequest) -> AIResponse:
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
