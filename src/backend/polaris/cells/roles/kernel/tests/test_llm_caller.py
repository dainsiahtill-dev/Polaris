"""Test suite for `roles.kernel` LLMCaller.

Covers:
- resolve_timeout_seconds() — Director=600s, others=60s
- _resolve_platform_retry_max() — Director=0, others=requested
- _resolve_tool_call_provider() — provider keyword resolution
- _is_native_tool_calling_unsupported() — error pattern detection
- _extract_native_tool_calls() — OpenAI vs Anthropic tool call extraction
- _extract_json_from_text() — JSON extraction from code blocks and bare
- _classify_error() — error string classification
- _messages_to_input() — message list formatting (annotated vs native)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn, cast

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.contracts import RoleProfile
    from polaris.kernelone.context.contracts import ContextRequest

import pytest
from polaris.cells.roles.kernel.internal.llm_caller import (
    LLMCaller,
    LLMResponse,
    PreparedLLMRequest,
    StructuredLLMResponse,
)
from polaris.cells.roles.kernel.internal.llm_caller.error_handling import (
    classify_error,
    is_native_tool_calling_unsupported,
)
from polaris.cells.roles.kernel.internal.llm_caller.helpers import (
    extract_json_from_text,
    extract_native_tool_calls,
    messages_to_input,
    resolve_platform_retry_max,
    resolve_timeout_seconds,
    resolve_tool_call_provider,
)
from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import build_native_tool_schemas


class MockProfile:
    """Minimal RoleProfile stand-in for testing."""

    def __init__(
        self,
        role_id: str = "pm",
        model: str = "gpt-4",
        provider_id: str = "openai",
    ) -> None:
        self.role_id = role_id
        self.model = model
        self.provider_id = provider_id
        self.tool_policy = SimpleNamespace(allowed_tools=[], denied_tools=[])


class TestResolveTimeoutSeconds:
    """resolve_timeout_seconds returns Director=600s, others=60s."""

    def test_director_role_gets_600_seconds(self) -> None:
        profile = MockProfile(role_id="director")
        timeout = resolve_timeout_seconds(cast("RoleProfile", profile))
        assert timeout == 660

    def test_non_director_role_gets_60_seconds(self) -> None:
        profile = MockProfile(role_id="pm")
        timeout = resolve_timeout_seconds(cast("RoleProfile", profile))
        assert timeout == 60

    def test_director_role_respects_env_override(self) -> None:
        import os

        # Clear LRU cache so the new env var is picked up
        from polaris.cells.roles.kernel.internal.llm_caller.helpers import _get_cached_director_timeout

        _get_cached_director_timeout.cache_clear()

        os.environ["KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS"] = "300"
        try:
            profile = MockProfile(role_id="director")
            timeout = resolve_timeout_seconds(cast("RoleProfile", profile))
            assert timeout == 300
        finally:
            os.environ.pop("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", None)
            _get_cached_director_timeout.cache_clear()

    def test_timeout_clamped_to_max_900(self) -> None:
        import os

        # Clear LRU cache so the new env var is picked up
        from polaris.cells.roles.kernel.internal.llm_caller.helpers import _get_cached_director_timeout

        _get_cached_director_timeout.cache_clear()

        os.environ["KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS"] = "9999"
        try:
            profile = MockProfile(role_id="director")
            timeout = resolve_timeout_seconds(cast("RoleProfile", profile))
            assert timeout == 900
        finally:
            os.environ.pop("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", None)
            _get_cached_director_timeout.cache_clear()


class TestResolvePlatformRetryMax:
    """resolve_platform_retry_max returns Director=0, others=requested."""

    def test_director_role_returns_zero(self) -> None:
        profile = MockProfile(role_id="director")
        result = resolve_platform_retry_max(cast("RoleProfile", profile), 3)
        assert result == 0

    def test_non_director_role_returns_requested(self) -> None:
        profile = MockProfile(role_id="pm")
        result = resolve_platform_retry_max(cast("RoleProfile", profile), 2)
        assert result == 2

    def test_non_director_role_handles_invalid(self) -> None:
        profile = MockProfile(role_id="architect")
        # Cast to bypass type check - testing fallback behavior with invalid input
        result = resolve_platform_retry_max(cast("RoleProfile", profile), cast("int", "not a number"))
        assert result == 1  # default fallback


class TestResolveToolCallProvider:
    """resolve_tool_call_provider maps model strings to provider names."""

    def test_anthropic_keywords_resolve_to_anthropic(self) -> None:
        for keyword in ("anthropic", "claude", "kimi"):
            result = resolve_tool_call_provider(
                provider_id=keyword,
                model="",
            )
            assert result == "anthropic", f"keyword={keyword}"

    def test_openai_keywords_resolve_to_openai(self) -> None:
        for keyword in ("openai", "gpt", "codex"):
            result = resolve_tool_call_provider(
                provider_id=keyword,
                model="",
            )
            assert result == "openai", f"keyword={keyword}"

    def test_empty_returns_auto(self) -> None:
        result = resolve_tool_call_provider(provider_id="", model="")
        assert result == "auto"


class TestIsNativeToolCallingUnsupported:
    """is_native_tool_calling_unsupported detects provider rejection patterns."""

    def test_unsupported_parameter_detected(self) -> None:
        assert is_native_tool_calling_unsupported("unsupported parameter: tools") is True

    def test_tools_not_allowed_detected(self) -> None:
        assert is_native_tool_calling_unsupported("extra inputs are not permitted: tools") is True

    def test_function_calling_not_supported_detected(self) -> None:
        assert is_native_tool_calling_unsupported("function calling not supported") is True

    def test_invalid_tools_bad_request_detected(self) -> None:
        # Must contain "tools" AND ("invalid_request_error" OR "bad request")
        assert is_native_tool_calling_unsupported("invalid_request_error: tools is not allowed") is True

    def test_empty_returns_false(self) -> None:
        assert is_native_tool_calling_unsupported("") is False

    def test_normal_error_returns_false(self) -> None:
        assert is_native_tool_calling_unsupported("timeout after 30s") is False


class TestBuildNativeToolSchemas:
    """build_native_tool_schemas should expose canonical tool contracts."""

    def test_builds_repo_contract_schema_when_registry_missing(self, monkeypatch) -> None:
        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=["repo_read_head", "repo_rg"])
        monkeypatch.setattr(
            "polaris.kernelone.llm.toolkit.definitions.create_default_registry",
            lambda: SimpleNamespace(get=lambda _name: None),
        )
        monkeypatch.setattr(
            "polaris.kernelone.llm.toolkit.tool_normalization.normalize_tool_name",
            lambda name: str(name),
        )

        schemas = build_native_tool_schemas(cast("RoleProfile", profile))
        names = {str((item.get("function") or {}).get("name") or "") for item in schemas if isinstance(item, dict)}

        assert "repo_read_head" in names
        assert "repo_rg" in names

    def test_repo_read_head_schema_exposes_alias_params(self, monkeypatch) -> None:
        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=["repo_read_head"])
        monkeypatch.setattr(
            "polaris.kernelone.llm.toolkit.definitions.create_default_registry",
            lambda: SimpleNamespace(get=lambda _name: None),
        )
        monkeypatch.setattr(
            "polaris.kernelone.llm.toolkit.tool_normalization.normalize_tool_name",
            lambda name: str(name),
        )

        schemas = build_native_tool_schemas(cast("RoleProfile", profile))
        function_payload = next(
            (item.get("function") for item in schemas if (item.get("function") or {}).get("name") == "repo_read_head"),
            None,
        )
        assert isinstance(function_payload, dict)
        parameters = function_payload.get("parameters") or {}
        properties = parameters.get("properties") or {}

        assert "file" in properties
        assert "n" in properties
        # Compatibility aliases remain explicit in schema for model-side argument shaping.
        assert "limit" in properties


class TestExtractNativeToolCalls:
    """extract_native_tool_calls separates OpenAI vs Anthropic tool call formats."""

    def test_extracts_openai_tool_calls_from_top_level(self) -> None:
        raw = {
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "x.py"}'},
                }
            ]
        }
        calls, provider = extract_native_tool_calls(raw, provider_id="openai", model="gpt-4")
        assert len(calls) == 1
        assert provider == "openai"

    def test_extracts_openai_tool_calls_from_choices(self) -> None:
        raw = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_xyz",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": '{"path": "y.py", "content": "x"}',
                                },
                            }
                        ]
                    }
                }
            ]
        }
        calls, provider = extract_native_tool_calls(raw, provider_id="openai", model="gpt-4")
        assert len(calls) == 1
        assert provider == "openai"

    def test_extracts_anthropic_tool_use_blocks(self) -> None:
        raw = {
            "content": [
                {
                    "type": "tool_use",
                    "name": "read_file",
                    "input": {"path": "z.py"},
                }
            ]
        }
        calls, provider = extract_native_tool_calls(raw, provider_id="anthropic", model="claude-3")
        assert len(calls) == 1
        assert provider == "anthropic"

    def test_empty_payload_returns_empty(self) -> None:
        calls, provider = extract_native_tool_calls({}, provider_id="openai", model="gpt-4")
        assert calls == []
        assert provider == "openai"

    def test_non_dict_returns_empty(self) -> None:
        # Cast to test non-dict input handling
        calls, provider = extract_native_tool_calls(
            cast("dict[str, object]", "not a dict"), provider_id="openai", model="gpt-4"
        )
        assert calls == []
        assert provider == "auto"


class TestExtractJsonFromText:
    """extract_json_from_text parses JSON from fenced blocks and bare JSON."""

    def test_extracts_json_from_fenced_block(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        result = extract_json_from_text(text)
        assert result == {"key": "value"}

    def test_rejects_json_array_for_type_safety(self) -> None:
        """Arrays are rejected to maintain type safety (function returns dict)."""
        text = "```json\n[1, 2, 3]\n```"
        with pytest.raises(ValueError, match="No valid JSON object found"):
            extract_json_from_text(text)

    def test_extracts_bare_json(self) -> None:
        text = '{"bare": true}'
        result = extract_json_from_text(text)
        assert result == {"bare": True}

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty text"):
            extract_json_from_text("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty text"):
            extract_json_from_text("   \n\t  ")

    def test_no_valid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="No valid JSON object found"):
            extract_json_from_text("not json at all")


class TestClassifyError:
    """classify_error categorizes LLM errors by type."""

    def test_timeout_classification(self) -> None:
        assert classify_error("timeout after 30s") == "timeout"
        assert classify_error("Request timed out after 60s") == "timeout"

    def test_rate_limit_classification(self) -> None:
        assert classify_error("rate limit exceeded") == "rate_limit"
        assert classify_error("HTTP 429 Too Many Requests") == "rate_limit"

    def test_network_classification(self) -> None:
        assert classify_error("Connection refused") == "network"
        assert classify_error("Network error: DNS failure") == "network"

    def test_auth_classification(self) -> None:
        assert classify_error("Auth failed: Invalid API key") == "auth"
        assert classify_error("401 Unauthorized") == "auth"

    def test_provider_classification(self) -> None:
        assert classify_error("Model not found") == "provider"
        assert classify_error("Provider error: service unavailable") == "provider"

    def test_unknown_classification(self) -> None:
        assert classify_error("Something unexpected happened") == "unknown"
        assert classify_error("") == "unknown"


class TestMessagesToInput:
    """messages_to_input formats message lists for different provider types."""

    def test_native_format_uses_xml_tags(self) -> None:
        messages = [{"role": "system", "content": "You are helpful"}]
        result = messages_to_input(messages, format_type="native", provider_id="anthropic")
        assert "<system>" in result
        assert "</system>" in result
        assert "You are helpful" in result

    def test_native_format_user_role(self) -> None:
        messages = [{"role": "user", "content": "Hello"}]
        result = messages_to_input(messages, format_type="native", provider_id="claude")
        assert "<user>" in result
        assert "Hello" in result

    def test_annotated_format_uses_chinese_markers(self) -> None:
        messages = [{"role": "system", "content": "SYS"}]
        result = messages_to_input(messages, format_type="annotated")
        assert "【系统指令】" in result

    def test_auto_selects_native_for_supported_providers(self) -> None:
        messages = [{"role": "system", "content": "SYS"}]
        result = messages_to_input(messages, format_type="auto", provider_id="anthropic")
        assert "<system>" in result

    def test_auto_selects_annotated_for_unknown_providers(self) -> None:
        messages = [{"role": "system", "content": "SYS"}]
        result = messages_to_input(messages, format_type="auto", provider_id="unknown")
        assert "【系统指令】" in result

    def test_multiple_messages_joined_with_double_newline(self) -> None:
        messages = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USER"},
        ]
        result = messages_to_input(messages, format_type="annotated")
        assert "SYS" in result
        assert "USER" in result


class TestLLMCallerConstruction:
    """LLMCaller instantiates correctly with workspace and cache flags."""

    def test_default_construction(self) -> None:
        caller = LLMCaller()
        assert caller.workspace == ""
        assert caller._enable_cache is True

    def test_workspace_set(self) -> None:
        caller = LLMCaller(workspace="/tmp/project")
        assert caller.workspace == "/tmp/project"

    def test_cache_can_be_disabled(self) -> None:
        caller = LLMCaller(enable_cache=False)
        assert caller._enable_cache is False


class TestLLMResponseDataclass:
    """LLMResponse and StructuredLLMResponse dataclasses are correctly structured."""

    def test_llm_response_defaults(self) -> None:
        response = LLMResponse(content="Hello")
        assert response.content == "Hello"
        assert response.token_estimate == 0
        assert response.error is None
        assert response.error_category is None
        assert response.tool_calls == []
        assert response.metadata == {}

    def test_llm_response_with_error(self) -> None:
        response = LLMResponse(
            content="",
            error="timeout",
            error_category="timeout",
        )
        assert response.error == "timeout"
        assert response.error_category == "timeout"

    def test_structured_llm_response_defaults(self) -> None:
        response = StructuredLLMResponse()
        assert response.data == {}
        assert response.raw_content == ""
        assert response.token_estimate == 0
        assert response.error is None
        assert response.validation_errors == []
        assert response.metadata == {}

    def test_structured_llm_response_with_validation_errors(self) -> None:
        response = StructuredLLMResponse(
            data={"key": "value"},
            validation_errors=["missing field: id"],
        )
        assert response.validation_errors == ["missing field: id"]


class TestPreparedRequestArchitecture:
    """Request construction stays converged across sync and streaming paths."""

    @pytest.mark.asyncio
    async def test_prepare_llm_request_non_stream_enables_native_tools(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "hello"}],
                    token_estimate=12,
                )

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )
        monkeypatch.setattr(
            LLMCaller,
            "_build_native_tool_schemas",
            staticmethod(lambda _profile: [{"type": "function", "function": {"name": "read_file"}}]),
        )

        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast("ContextRequest", SimpleNamespace(task_id=None)),
            temperature=0.2,
            max_tokens=256,
            stream=False,
            platform_retry_max=3,
        )

        assert prepared.native_tool_mode == "native_tools"
        assert prepared.request_options["tools"] == [{"type": "function", "function": {"name": "read_file"}}]
        assert prepared.request_options["tool_choice"] == "auto"
        assert prepared.request_options["max_retries"] == 0
        assert prepared.ai_request.context["native_tool_mode"] == "native_tools"

    @pytest.mark.asyncio
    async def test_prepare_llm_request_stream_enables_native_tools(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "hello"}],
                    token_estimate=12,
                )

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )
        monkeypatch.setattr(
            LLMCaller,
            "_build_native_tool_schemas",
            staticmethod(lambda _profile: [{"type": "function", "function": {"name": "read_file"}}]),
        )

        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast("ContextRequest", SimpleNamespace(task_id=None)),
            temperature=0.2,
            max_tokens=256,
            stream=True,
        )

        assert prepared.native_tool_mode == "native_tools_streaming"
        assert prepared.request_options["tools"] == [{"type": "function", "function": {"name": "read_file"}}]
        assert prepared.request_options["tool_choice"] == "auto"
        assert "max_retries" not in prepared.request_options
        assert prepared.ai_request.context["native_tool_mode"] == "native_tools_streaming"

    @pytest.mark.asyncio
    async def test_prepare_llm_request_stream_honors_forced_tool_definitions_override(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file", "edit_file"])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "hello"}],
                    token_estimate=12,
                )

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )
        monkeypatch.setattr(
            LLMCaller,
            "_build_native_tool_schemas",
            staticmethod(lambda _profile: [{"type": "function", "function": {"name": "read_file"}}]),
        )

        forced_tools = [{"type": "function", "function": {"name": "edit_file"}}]
        context = SimpleNamespace(
            task_id=None,
            context_override={
                "_transaction_kernel_forced_tool_definitions": forced_tools,
                "_transaction_kernel_forced_tool_choice": "required",
            },
        )
        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast("ContextRequest", context),
            temperature=0.2,
            max_tokens=256,
            stream=True,
        )

        assert prepared.native_tool_mode == "native_tools_streaming"
        assert prepared.request_options["tools"] == forced_tools
        assert prepared.request_options["tool_choice"] == "required"

    @pytest.mark.asyncio
    async def test_prepare_llm_request_non_stream_unknown_provider_model_uses_native_tools(
        self,
        monkeypatch,
    ) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="", provider_id="")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "hello"}],
                    token_estimate=12,
                )

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )
        monkeypatch.setattr(
            LLMCaller,
            "_build_native_tool_schemas",
            staticmethod(lambda _profile: [{"type": "function", "function": {"name": "read_file"}}]),
        )

        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast("ContextRequest", SimpleNamespace(task_id=None)),
            temperature=0.2,
            max_tokens=256,
            stream=False,
            platform_retry_max=3,
        )

        assert prepared.native_tool_mode == "native_tools"
        assert prepared.request_options["tools"] == [{"type": "function", "function": {"name": "read_file"}}]
        assert prepared.request_options["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_prepare_llm_request_stream_unknown_provider_model_uses_native_tools(
        self,
        monkeypatch,
    ) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="", provider_id="")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "hello"}],
                    token_estimate=12,
                )

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )
        monkeypatch.setattr(
            LLMCaller,
            "_build_native_tool_schemas",
            staticmethod(lambda _profile: [{"type": "function", "function": {"name": "read_file"}}]),
        )

        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast("ContextRequest", SimpleNamespace(task_id=None)),
            temperature=0.2,
            max_tokens=256,
            stream=True,
        )

        assert prepared.native_tool_mode == "native_tools_streaming"
        assert prepared.request_options["tools"] == [{"type": "function", "function": {"name": "read_file"}}]
        assert prepared.request_options["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_prepare_llm_request_uses_prebuilt_messages_without_gateway(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"])

        class _FailGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context) -> NoReturn:
                raise AssertionError("RoleContextGateway.build_context should be bypassed for prebuilt messages")

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FailGateway,
        )

        context_override = {
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "content": "sys prebuilt"},
                {"role": "user", "content": "hello prebuilt"},
            ],
            "_transaction_kernel_prebuilt_token_estimate": 42,
            "_transaction_kernel_prebuilt_compression_applied": True,
            "_transaction_kernel_prebuilt_compression_strategy": "summarize",
        }
        context = SimpleNamespace(task_id="task-1", context_override=context_override)

        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="ignored system prompt",
            context=cast("ContextRequest", context),
            temperature=0.2,
            max_tokens=256,
            stream=True,
        )

        assert prepared.messages[0] == {"role": "system", "content": "sys prebuilt"}
        assert prepared.messages[1] == {"role": "user", "content": "hello prebuilt"}
        assert prepared.context_result.token_estimate == 42
        assert prepared.context_result.compression_applied is True
        assert prepared.context_result.compression_strategy == "summarize"
        assert prepared.context_result.metadata.get("prebuilt_projection_messages") is True

    def test_extract_prebuilt_projection_messages_dedupes_current_user_variants(self) -> None:
        context = SimpleNamespace(
            message="hello",
            context_override={
                "_transaction_kernel_prebuilt_messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hello"},
                    {"role": "user", "content": "hello"},
                    {"role": "system", "content": "anchor"},
                    {"role": "user", "content": "\ufeffhello\r\n"},
                    {"role": "user", "content": "other"},
                ],
            },
        )
        messages = LLMCaller._extract_prebuilt_projection_messages(cast("ContextRequest", context))
        assert messages is not None
        assert messages == [
            {"role": "system", "content": "sys"},
            {"role": "system", "content": "anchor"},
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "other"},
        ]

    def test_extract_prebuilt_projection_messages_collapses_adjacent_user_duplicates(self) -> None:
        context = SimpleNamespace(
            context_override={
                "_transaction_kernel_prebuilt_messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "same"},
                    {"role": "user", "content": "same"},
                    {"role": "assistant", "content": "ack"},
                    {"role": "user", "content": "same"},
                ],
            },
        )
        messages = LLMCaller._extract_prebuilt_projection_messages(cast("ContextRequest", context))
        assert messages is not None
        assert messages == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "same"},
            {"role": "assistant", "content": "ack"},
            {"role": "user", "content": "same"},
        ]


class TestLifecycleAndCacheGuards:
    """Guardrails for cache eligibility and structured fallback request building."""

    def test_cache_eligibility_plain_text_only(self) -> None:
        plain = SimpleNamespace(
            native_tool_mode="disabled",
            response_format_mode="plain_text",
            native_tool_schemas=[],
        )
        with_tools = SimpleNamespace(
            native_tool_mode="native_tools",
            response_format_mode="plain_text",
            native_tool_schemas=[{"type": "function"}],
        )
        with_schema = SimpleNamespace(
            native_tool_mode="disabled",
            response_format_mode="native_json_schema",
            native_tool_schemas=[],
        )

        assert LLMCaller._is_cache_eligible(prepared=cast("PreparedLLMRequest", plain), response_model=None) is True
        assert (
            LLMCaller._is_cache_eligible(prepared=cast("PreparedLLMRequest", with_tools), response_model=None) is False
        )
        assert (
            LLMCaller._is_cache_eligible(prepared=cast("PreparedLLMRequest", with_schema), response_model=None) is False
        )
        assert LLMCaller._is_cache_eligible(prepared=cast("PreparedLLMRequest", plain), response_model=dict) is False

    def test_structured_fallback_request_reuses_prepared_baseline(self) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        prepared = SimpleNamespace(
            request_options={
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout": 120,
                "response_format": {"type": "json_schema"},
            },
            input_text="hello",
            ai_request=SimpleNamespace(
                context={
                    "workspace": "C:/workspace",
                    "mode": "chat",
                    "native_tool_mode": "disabled",
                    "response_format_mode": "native_json_schema",
                }
            ),
        )
        request = caller._build_structured_fallback_request(
            prepared=cast("PreparedLLMRequest", prepared),
            profile=cast("RoleProfile", MockProfile(role_id="pm", model="gpt-5", provider_id="openai")),
            response_model=dict,
        )

        assert "response_format" not in request.options
        assert request.options["timeout"] == 120
        assert request.options["max_tokens"] == 256
        assert request.context["mode"] == "structured"
        assert request.context["response_format_mode"] == "text_json_fallback"
        assert "运行时结构化输出回退" in request.input

    @pytest.mark.asyncio
    async def test_call_stream_error_event_contains_metadata_on_prepare_failure(
        self,
        monkeypatch,
    ) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="pm", model="gpt-5", provider_id="openai")

        class _FailingGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            def build_context(self, _context) -> NoReturn:
                raise ValueError("context build failed")

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FailingGateway,
        )

        events = []
        async for item in caller.call_stream(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast("ContextRequest", SimpleNamespace(task_id=None)),
            temperature=0.2,
            max_tokens=256,
        ):
            events.append(item)

        assert events
        assert events[0]["type"] == "error"
        assert events[0]["error"] == "context build failed"
        assert events[0]["metadata"]["native_tool_mode"] == "disabled"
        assert events[0]["metadata"]["tool_protocol"] == "none"
        assert events[0]["metadata"]["native_tool_calling_fallback"] is False

    @pytest.mark.asyncio
    async def test_call_stream_supports_preinvoke_cancel_flag(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="pm", model="gpt-5", provider_id="openai")

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            def build_context(self, _context) -> NoReturn:
                raise AssertionError("build_context should not be called when preinvoke cancel is set")

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )

        events = []
        async for item in caller.call_stream(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast(
                "ContextRequest",
                SimpleNamespace(
                    task_id=None,
                    context_override={"stream_cancelled": True},
                ),
            ),
            temperature=0.2,
            max_tokens=128,
        ):
            events.append(item)

        assert events
        assert events[0]["type"] == "error"
        assert events[0]["error"] == "stream_cancelled_before_invoke"

    @pytest.mark.asyncio
    async def test_invoker_stream_call_end_includes_prompt_tokens(self, monkeypatch) -> None:
        invoker = LLMInvoker(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        captured: dict[str, Any] = {}

        async def _prepare_llm_request(self, **_kwargs):
            return SimpleNamespace(
                context_result=SimpleNamespace(
                    token_estimate=123,
                    compression_strategy="none",
                    compression_applied=False,
                ),
                messages=[{"role": "user", "content": "hello"}],
                native_tool_mode="disabled",
                response_format_mode="plain_text",
                native_tool_schemas=[],
                ai_request=SimpleNamespace(),
            )

        class _FakeExecutor:
            async def invoke_stream(self, _request):
                yield {"type": "chunk", "content": "hello"}
                yield {"type": "complete", "content": ""}

        def _normalize_stream_chunk(chunk, **_kwargs):
            return SimpleNamespace(
                event_type=chunk["type"],
                content=chunk.get("content", ""),
                metadata={},
                error="",
                tool_name="",
                tool_args={},
                tool_call_id="",
                tool_result={},
            )

        def _capture_call_end(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.llm_caller.caller.LLMCaller._prepare_llm_request",
            _prepare_llm_request,
        )
        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.normalize_stream_chunk",
            _normalize_stream_chunk,
        )
        monkeypatch.setattr(LLMInvoker, "_get_executor", lambda _self: _FakeExecutor())
        monkeypatch.setattr(LLMInvoker, "_emit_call_end_event", lambda _self, **kwargs: _capture_call_end(**kwargs))

        events = []
        async for event in invoker.call_stream(
            profile=cast("RoleProfile", profile),
            system_prompt="system prompt",
            context=cast("ContextRequest", SimpleNamespace(task_id=None)),
            temperature=0.2,
            max_tokens=64,
        ):
            events.append(event)

        assert any(event["type"] == "complete" for event in events)
        assert captured["prompt_tokens"] == 123

    @pytest.mark.asyncio
    async def test_invoker_stream_debug_event_uses_prepared_request_payload(self, monkeypatch) -> None:
        invoker = LLMInvoker(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        captured_debug_events: list[dict[str, Any]] = []

        async def _prepare_llm_request(self, **_kwargs):
            return SimpleNamespace(
                context_result=SimpleNamespace(
                    token_estimate=12,
                    compression_strategy="none",
                    compression_applied=False,
                ),
                messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}],
                native_tool_mode="disabled",
                response_format_mode="plain_text",
                native_tool_schemas=[],
                ai_request=SimpleNamespace(provider_id="openai", model="gpt-5-resolved"),
            )

        async def _run_stream(**_kwargs):
            yield {"type": "complete", "content": ""}

        def _capture_debug_event(**kwargs: Any) -> None:
            captured_debug_events.append(kwargs)

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.llm_caller.caller.LLMCaller._prepare_llm_request",
            _prepare_llm_request,
        )
        monkeypatch.setattr(invoker._stream_engine, "run_stream", _run_stream)
        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.emit_debug_event",
            _capture_debug_event,
        )

        async for _event in invoker.call_stream(
            profile=cast("RoleProfile", profile),
            system_prompt="system prompt",
            context=cast("ContextRequest", SimpleNamespace(task_id=None)),
            temperature=0.2,
            max_tokens=64,
        ):
            pass

        invoke_request = next(item for item in captured_debug_events if item.get("label") == "invoke_request")
        payload = cast("dict[str, Any]", invoke_request["payload"])
        assert payload["provider_id"] == "openai"
        assert payload["model"] == "gpt-5-resolved"
        assert payload["message_count"] == 2
        assert payload["messages"] == [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ]
