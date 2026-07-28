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
    assert "tool_choice" not in projection.expected_body()


def test_deepseek_official_anthropic_projection_preserves_forced_tool_choice() -> None:
    payload = _final_payload(stream=True)
    payload["model"] = "deepseek-v4-pro"
    payload["tool_choice"] = {
        "type": "function",
        "function": {"name": "read_file"},
    }

    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="stream",
        final_payload=payload,
        provider_config={
            "base_url": "https://api.deepseek.com/anthropic",
            "provider_id": "deepseek",
        },
    )

    assert projection is not None
    assert projection.expected_body()["tool_choice"] == {
        "type": "tool",
        "name": "read_file",
    }
    assert projection.expected_body()["thinking"] == {"type": "disabled"}


def test_deepseek_official_anthropic_projection_keeps_default_thinking_without_tool_choice() -> None:
    payload = _final_payload(stream=True)
    payload["model"] = "deepseek-v4-pro"
    payload["tool_choice"] = None

    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="stream",
        final_payload=payload,
        provider_config={
            "base_url": "https://api.deepseek.com/anthropic",
            "provider_id": "deepseek",
        },
    )

    assert projection is not None
    assert "tool_choice" not in projection.expected_body()
    assert "thinking" not in projection.expected_body()


def test_deepseek_official_anthropic_projection_rejects_explicit_thinking_with_tool_choice() -> None:
    payload = _final_payload(stream=True)
    payload["model"] = "deepseek-v4-pro"

    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_thinking_tool_choice_conflict:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="stream",
            final_payload=payload,
            provider_config={
                "base_url": "https://api.deepseek.com/anthropic",
                "provider_id": "deepseek",
                "thinking": {"type": "enabled"},
            },
        )


@pytest.mark.parametrize(
    "provider_config",
    [
        {
            "base_url": "https://api.deepseek.com/anthropicology",
            "api_path": "/v1/messages",
        },
        {
            "base_url": "https://proxy.example.test",
            "api_path": "/https://api.deepseek.com/anthropic/v1/messages",
        },
    ],
)
def test_non_deepseek_routes_cannot_inherit_official_thinking_semantics_from_substrings(
    provider_config: dict[str, object],
) -> None:
    payload = _final_payload(stream=True)
    payload["model"] = "deepseek-v4-pro"
    payload["tool_choice"] = {
        "type": "function",
        "function": {"name": "read_file"},
    }

    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="stream",
        final_payload=payload,
        provider_config=provider_config,
    )

    assert projection is not None
    assert "thinking" not in projection.expected_body()


def test_factory_anthropic_projection_rejects_unfrozen_request_overrides() -> None:
    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_overrides_forbidden:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="stream",
            final_payload=_final_payload(stream=True),
            provider_config={
                "base_url": "https://api.deepseek.com/anthropic",
                "request_overrides": {"thinking": {"type": "disabled"}},
            },
        )


@pytest.mark.parametrize(
    ("provider_config", "model", "tool_choice"),
    [
        (
            {
                "base_url": "https://api.kimi.com/coding/v1",
                "provider_id": "kimi",
            },
            "kimi-for-coding",
            {"type": "function", "function": {"name": "read_file"}},
        ),
        (
            {
                "base_url": "https://api.kimi.com/coding/v1",
                "provider_id": "kimi",
            },
            "kimi-for-coding",
            "required",
        ),
        (
            {
                "base_url": "https://api.kimi.com/coding/v1",
                "provider_id": "kimi",
            },
            "kimi-for-coding",
            "none",
        ),
        (
            {
                "base_url": "https://api.kimi.com/coding/v1",
                "provider_id": "kimi",
                "disable_parallel_tool_use": True,
            },
            "kimi-for-coding",
            "auto",
        ),
        (
            {
                "base_url": "https://anthropic-compatible.test/v1",
                "disable_tool_choice": True,
            },
            "model-1",
            {"type": "function", "function": {"name": "read_file"}},
        ),
    ],
)
def test_anthropic_projection_rejects_forced_tool_choice_when_route_cannot_represent_it(
    provider_config: dict[str, object],
    model: str,
    tool_choice: object,
) -> None:
    payload = _final_payload(stream=True)
    payload["model"] = model
    payload["tool_choice"] = tool_choice

    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_tool_choice_unsupported:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="stream",
            final_payload=payload,
            provider_config=provider_config,
        )


@pytest.mark.parametrize(
    "tool_choice",
    [
        "required",
        {"type": "function", "function": {"name": "read_file"}},
    ],
)
def test_anthropic_projection_rejects_forced_tool_choice_without_tools(
    tool_choice: object,
) -> None:
    payload = _final_payload()
    payload["tools"] = []
    payload["tool_choice"] = tool_choice

    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_tool_choice_without_tools:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=payload,
            provider_config={"base_url": "https://anthropic.test/v1"},
        )


@pytest.mark.parametrize(
    "tool_choice",
    [
        "",
        {},
        {
            "type": "function",
            "function": {"name": "read_file"},
            "provider_semantic_hint": "must-preserve",
        },
        {
            "type": "function",
            "function": {"name": "read_file"},
            "disable_parallel_tool_use": "true",
        },
        {"type": "auto", "name": "read_file"},
        " read_file ",
        {"type": "tool", "name": " read_file"},
    ],
)
def test_anthropic_projection_rejects_malformed_tool_choice(tool_choice: object) -> None:
    payload = _final_payload()
    payload["tool_choice"] = tool_choice

    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_tool_choice_invalid",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=payload,
            provider_config={"base_url": "https://anthropic.test/v1"},
        )


def test_anthropic_projection_maps_any_to_native_any() -> None:
    payload = _final_payload()
    payload["tool_choice"] = "any"

    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload=payload,
        provider_config={"base_url": "https://anthropic.test/v1"},
    )

    assert projection is not None
    assert projection.expected_body()["tool_choice"] == {"type": "any"}


def test_anthropic_projection_preserves_openai_choice_parallel_constraint() -> None:
    payload = _final_payload()
    payload["tool_choice"] = {
        "type": "function",
        "function": {"name": "read_file"},
        "disable_parallel_tool_use": True,
    }

    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload=payload,
        provider_config={"base_url": "https://anthropic.test/v1"},
    )

    assert projection is not None
    assert projection.expected_body()["tool_choice"] == {
        "type": "tool",
        "name": "read_file",
        "disable_parallel_tool_use": True,
    }


def test_anthropic_projection_rejects_forced_choice_not_in_tools() -> None:
    payload = _final_payload()
    payload["tool_choice"] = {"type": "tool", "name": "write_file"}

    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_tool_choice_unknown_tool:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=payload,
            provider_config={"base_url": "https://anthropic.test/v1"},
        )


def test_deepseek_projection_rejects_unsupported_parallel_constraint() -> None:
    payload = _final_payload()

    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_parallel_tool_choice_unsupported:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=payload,
            provider_config={
                "base_url": "https://api.deepseek.com/anthropic",
                "disable_parallel_tool_use": True,
            },
        )


def test_deepseek_projection_omits_default_false_parallel_constraint() -> None:
    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload=_final_payload(),
        provider_config={
            "base_url": "https://api.deepseek.com/anthropic",
            "disable_parallel_tool_use": False,
        },
    )

    assert projection is not None
    assert projection.expected_body()["tool_choice"] == {"type": "auto"}


def test_non_deepseek_route_does_not_infer_capabilities_from_model_name() -> None:
    payload = _final_payload()
    payload["model"] = "deepseek-v4-pro"

    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload=payload,
        provider_config={
            "base_url": "https://anthropic-proxy.test/v1",
            "disable_parallel_tool_use": True,
        },
    )

    assert projection is not None
    assert projection.expected_body()["tool_choice"] == {
        "type": "auto",
        "disable_parallel_tool_use": True,
    }


def test_standard_anthropic_projection_preserves_implicit_auto_parallel_constraint() -> None:
    payload = _final_payload()
    payload["tool_choice"] = None

    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload=payload,
        provider_config={
            "base_url": "https://anthropic.test/v1",
            "disable_parallel_tool_use": True,
        },
    )

    assert projection is not None
    assert projection.expected_body()["tool_choice"] == {
        "type": "auto",
        "disable_parallel_tool_use": True,
    }


@pytest.mark.parametrize(
    "provider_config",
    [
        {
            "base_url": "https://api.kimi.com/coding/v1",
            "disable_parallel_tool_use": True,
        },
        {
            "base_url": "https://anthropic-compatible.test/v1",
            "disable_tool_choice": True,
            "disable_parallel_tool_use": True,
        },
    ],
)
def test_unsupported_route_rejects_implicit_auto_parallel_constraint(
    provider_config: dict[str, object],
) -> None:
    payload = _final_payload()
    payload["tool_choice"] = None

    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_tool_choice_unsupported:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=payload,
            provider_config=provider_config,
        )


def test_explicit_false_does_not_override_kimi_tool_choice_capability() -> None:
    payload = _final_payload()
    payload["tool_choice"] = "required"

    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_tool_choice_unsupported:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=payload,
            provider_config={
                "base_url": "https://api.kimi.com/coding/v1",
                "disable_tool_choice": False,
            },
        )


@pytest.mark.parametrize(
    "provider_config",
    [
        {
            "base_url": "https://api.kimi.com/coding/v1",
            "disable_parallel_tool_use": False,
        },
        {
            "base_url": "https://anthropic-compatible.test/v1",
            "disable_tool_choice": True,
            "disable_parallel_tool_use": False,
        },
    ],
)
def test_unsupported_route_omits_default_auto_false_parallel_constraint(
    provider_config: dict[str, object],
) -> None:
    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload=_final_payload(),
        provider_config=provider_config,
    )

    assert projection is not None
    assert "tool_choice" not in projection.expected_body()


def test_deepseek_explicit_identity_rejects_parallel_constraint() -> None:
    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_parallel_tool_choice_unsupported:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=_final_payload(),
            provider_config={
                "base_url": "https://anthropic-proxy.test/v1",
                "name": "DeepSeek Official",
                "provider_id": "deepseek",
                "disable_parallel_tool_use": True,
            },
        )


@pytest.mark.parametrize(
    "tools",
    [
        [{"name": "read_file", "parameters": "not-a-schema"}],
        [{"name": "read_file", "input_schema": "not-a-schema"}],
        [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": "not-a-schema",
                },
            }
        ],
        [
            {
                "name": "read_file",
                "input_schema": {"type": "object"},
                "provider_semantic_hint": "must-preserve",
            }
        ],
        [
            {
                "name": "read_file",
                "parameters": {"type": "object"},
                "description": None,
            }
        ],
        [{"name": " read_file", "parameters": {"type": "object"}}],
    ],
)
def test_anthropic_projection_rejects_lossy_tool_schema_conversion(
    tools: list[object],
) -> None:
    payload = _final_payload()
    payload["tools"] = tools

    with pytest.raises(
        FactoryProviderNativeRequestProjectionError,
        match="factory_provider_native_request_tools_unrepresentable:anthropic_messages",
    ):
        project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=payload,
            provider_config={"base_url": "https://anthropic.test/v1"},
        )


def test_anthropic_projection_does_not_invent_missing_tool_description() -> None:
    payload = _final_payload()
    payload["tools"] = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "parameters": {"type": "object"},
            },
        }
    ]

    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload=payload,
        provider_config={"base_url": "https://anthropic.test/v1"},
    )

    assert projection is not None
    assert projection.expected_body()["tools"] == [
        {
            "name": "read_file",
            "input_schema": {"type": "object"},
        }
    ]


def test_anthropic_projection_allows_default_none_without_tools() -> None:
    payload = _final_payload()
    payload["tools"] = []
    payload["tool_choice"] = "none"

    projection = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload=payload,
        provider_config={"base_url": "https://anthropic.test/v1"},
    )

    assert projection is not None
    assert "tools" not in projection.expected_body()
    assert "tool_choice" not in projection.expected_body()


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
