from __future__ import annotations

import pytest
from polaris.infrastructure.llm.providers.openai_provider import _build_openai_payload
from polaris.kernelone.llm.engine.provider_native_request import (
    FactoryProviderNativeRequestProjectionError,
    project_factory_provider_native_request,
    supports_factory_provider_native_projection,
)


def _final_payload(*, stream: bool = False) -> dict[str, object]:
    return {
        "model": "model-1",
        "messages": [
            {"role": "system", "content": "You are the Chief Engineer."},
            {"role": "user", "content": "Inspect the target files."},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read one file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            }
        ],
        "tool_choice": "auto",
        "response_format": None,
        "temperature": 0.0,
        "max_tokens": 512,
        "stream": stream,
    }


@pytest.mark.parametrize("mode", ["invoke", "stream"])
def test_only_closed_native_provider_routes_are_projectable(mode: str) -> None:
    assert supports_factory_provider_native_projection("anthropic_compat", mode=mode) is True  # type: ignore[arg-type]
    assert supports_factory_provider_native_projection("openai_compat", mode=mode) is True  # type: ignore[arg-type]
    for provider_type in ("codex_sdk", "gemini_api", "ollama", "plugin"):
        assert supports_factory_provider_native_projection(provider_type, mode=mode) is False  # type: ignore[arg-type]
        assert (
            project_factory_provider_native_request(
                provider_type=provider_type,
                mode=mode,  # type: ignore[arg-type]
                final_payload=_final_payload(stream=mode == "stream"),
                provider_config={"base_url": "https://provider.test/v1"},
            )
            is None
        )


def test_openai_chat_projection_is_endpoint_transport_and_body_exact() -> None:
    projection = project_factory_provider_native_request(
        provider_type="openai_compat",
        mode="invoke",
        final_payload=_final_payload(),
        provider_config={
            "base_url": "https://openai.test/v1/",
            "api_path": "/v1/chat/completions",
            "request_overrides": {"unfrozen": "must-not-enter-authority"},
        },
    )

    assert projection is not None
    assert projection.native_protocol == "openai_chat_completions"
    assert projection.exact_endpoint == "https://openai.test/v1/chat/completions"
    assert projection.exact_transport_kind == "requests.post"
    assert projection.expected_body() == {
        "model": "model-1",
        "messages": [
            {"role": "system", "content": "You are the Chief Engineer."},
            {"role": "user", "content": "Inspect the target files."},
        ],
        "temperature": 0.0,
        "max_tokens": 512,
        "tools": _final_payload()["tools"],
        "tool_choice": "auto",
    }
    assert "unfrozen" not in projection.expected_body()
    assert projection.authority() == {
        "schema_version": "llm.factory_provider_native_request.v1",
        "provider_type": "openai_compat",
        "mode": "invoke",
        "native_protocol": "openai_chat_completions",
        "exact_endpoint": "https://openai.test/v1/chat/completions",
        "exact_transport_kind": "requests.post",
        "expected_body": projection.expected_body(),
    }


def test_openai_responses_stream_projection_uses_native_input_and_transport() -> None:
    projection = project_factory_provider_native_request(
        provider_type="openai_compat",
        mode="stream",
        final_payload=_final_payload(stream=True),
        provider_config={"base_url": "https://openai.test/v1", "api_path": "/v1/responses"},
    )

    assert projection is not None
    assert projection.native_protocol == "openai_responses"
    assert projection.exact_endpoint == "https://openai.test/v1/responses"
    assert projection.exact_transport_kind == "aiohttp.ClientSession.post"
    assert projection.expected_body()["input"] == _final_payload()["messages"]
    assert projection.expected_body()["max_output_tokens"] == 512
    assert projection.expected_body()["stream"] is True


@pytest.mark.parametrize(
    ("api_path", "mode"),
    [("/v1/chat/completions", "invoke"), ("/v1/responses", "stream")],
)
def test_openai_projection_matches_provider_native_body(api_path: str, mode: str) -> None:
    semantic = _final_payload(stream=mode == "stream")
    config = {
        "base_url": "https://openai.test/v1",
        "api_path": api_path,
        "chat_messages": semantic["messages"],
        "temperature": semantic["temperature"],
        "max_tokens": semantic["max_tokens"],
        "tools": semantic["tools"],
        "tool_choice": semantic["tool_choice"],
    }
    projection = project_factory_provider_native_request(
        provider_type="openai_compat",
        mode=mode,  # type: ignore[arg-type]
        final_payload=semantic,
        provider_config=config,
    )
    provider_body = _build_openai_payload(
        prompt="flattened prompt",
        model="model-1",
        config=config,
        api_path=api_path,
        stream=mode == "stream",
    )

    assert projection is not None
    assert projection.expected_body() == provider_body


def test_anthropic_projection_splits_system_and_converts_tools_exactly() -> None:
    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload=_final_payload(),
        provider_config={"base_url": "https://anthropic.test/v1", "api_path": "/v1/messages"},
    )

    assert projection is not None
    assert projection.native_protocol == "anthropic_messages"
    assert projection.exact_endpoint == "https://anthropic.test/v1/messages"
    assert projection.expected_body() == {
        "model": "model-1",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "Inspect the target files."}],
        "system": "You are the Chief Engineer.",
        "temperature": 0.0,
        "tools": [
            {
                "name": "read_file",
                "description": "Read one file",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            }
        ],
        "tool_choice": {"type": "auto"},
    }


def test_kimi_anthropic_projection_reserves_visible_output_from_reasoning() -> None:
    payload = _final_payload(stream=True)
    payload["max_tokens"] = 8_192
    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="stream",
        final_payload=payload,
        provider_config={
            "base_url": "https://api.kimi.com/coding/v1",
            "provider_id": "kimi",
            "max_tokens": 8_192,
            "reasoning_budget_tokens": 2_048,
        },
    )

    assert projection is not None
    assert projection.expected_body()["thinking"] == {"type": "enabled", "budget_tokens": 2_048}


def test_reasoning_budget_does_not_enable_thinking_on_standard_anthropic_route() -> None:
    payload = _final_payload(stream=True)
    payload["max_tokens"] = 8_192
    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="stream",
        final_payload=payload,
        provider_config={
            "base_url": "https://anthropic.test/v1",
            "max_tokens": 8_192,
            "reasoning_budget_tokens": 2_048,
        },
    )

    assert projection is not None
    assert "thinking" not in projection.expected_body()


@pytest.mark.parametrize(
    ("provider_type", "api_path", "mode", "body_key"),
    [
        ("anthropic_compat", "/v1/messages", "invoke", "max_tokens"),
        ("openai_compat", "/v1/chat/completions", "invoke", "max_tokens"),
        ("openai_compat", "/v1/responses", "stream", "max_output_tokens"),
    ],
)
def test_native_projection_uses_engine_clamped_effective_max_tokens(
    provider_type: str,
    api_path: str,
    mode: str,
    body_key: str,
) -> None:
    """The physical authority must project the Engine's final output budget.

    The frozen semantic request records the requested budget.  Before the
    provider route is bound, the Engine clamps that request to the model/window
    limit in its final invoke config.  The provider consumes that effective
    value, so the exact native projection must consume the same authority.
    """

    semantic = _final_payload(stream=mode == "stream")
    semantic["max_tokens"] = 128_000
    projection = project_factory_provider_native_request(
        provider_type=provider_type,
        mode=mode,  # type: ignore[arg-type]
        final_payload=semantic,
        provider_config={
            "base_url": "https://provider.test/v1",
            "api_path": api_path,
            "max_tokens": 16_384,
        },
    )

    assert projection is not None
    assert projection.expected_body()[body_key] == 16_384


def test_native_projection_rejects_effective_max_tokens_expansion() -> None:
    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_effective_max_tokens_expansion_forbidden",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=_final_payload(),
            provider_config={
                "base_url": "https://provider.test/v1",
                "api_path": "/v1/messages",
                "max_tokens": 513,
            },
        )


@pytest.mark.parametrize("provider_type", ["anthropic_compat", "openai_compat"])
def test_native_projection_rejects_stream_mode_drift(provider_type: str) -> None:
    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_stream_mode_drift",
    ):
        project_factory_provider_native_request(
            provider_type=provider_type,
            mode="stream",
            final_payload=_final_payload(stream=False),
            provider_config={"base_url": "https://provider.test/v1"},
        )


def test_anthropic_projection_rejects_unrepresentable_response_format() -> None:
    payload = _final_payload()
    payload["response_format"] = {"type": "json_object"}
    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_response_format_unsupported:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=payload,
            provider_config={"base_url": "https://anthropic.test/v1"},
        )
