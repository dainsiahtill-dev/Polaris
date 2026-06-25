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

import json
import warnings
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn, cast

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile
    from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest

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
    resolve_max_tokens,
    resolve_platform_retry_max,
    resolve_timeout_seconds,
    resolve_tool_call_provider,
)
from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import build_native_tool_schemas
from polaris.cells.roles.profile.public.service import load_core_roles
from polaris.kernelone.context.contracts import TurnEngineContextResult


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


def _turn_context_result(content: str, token_estimate: int = 12) -> TurnEngineContextResult:
    return TurnEngineContextResult(
        messages=({"role": "user", "content": content},),
        token_estimate=token_estimate,
    )


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

    def test_director_context_timeout_cannot_reduce_role_default(self) -> None:
        profile = MockProfile(role_id="director")
        timeout = resolve_timeout_seconds(
            cast("RoleProfile", profile),
            {"llm_call_timeout_seconds": 45},
        )
        assert timeout == 660

    def test_non_director_context_timeout_override_wins(self) -> None:
        profile = MockProfile(role_id="pm")
        timeout = resolve_timeout_seconds(
            cast("RoleProfile", profile),
            {"llm_call_timeout_seconds": 45},
        )
        assert timeout == 45

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

    def test_timeout_clamped_to_default_max_1800(self) -> None:
        import os

        # Clear LRU cache so the new env var is picked up
        from polaris.cells.roles.kernel.internal.llm_caller.helpers import _get_cached_director_timeout

        _get_cached_director_timeout.cache_clear()

        os.environ["KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS"] = "9999"
        try:
            profile = MockProfile(role_id="director")
            timeout = resolve_timeout_seconds(cast("RoleProfile", profile))
            assert timeout == 1800
        finally:
            os.environ.pop("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", None)
            _get_cached_director_timeout.cache_clear()

    def test_timeout_max_can_be_configured_for_slow_director_models(self) -> None:
        import os

        from polaris.cells.roles.kernel.internal.llm_caller.helpers import _get_cached_director_timeout

        _get_cached_director_timeout.cache_clear()

        os.environ["KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS"] = "9999"
        os.environ["KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS"] = "2400"
        try:
            profile = MockProfile(role_id="director")
            timeout = resolve_timeout_seconds(cast("RoleProfile", profile))
            assert timeout == 2400
        finally:
            os.environ.pop("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", None)
            os.environ.pop("KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS", None)
            _get_cached_director_timeout.cache_clear()

    def test_context_timeout_override_clamped_to_configurable_max(self) -> None:
        import os

        os.environ["KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS"] = "2400"
        try:
            profile = MockProfile(role_id="director")
            timeout = resolve_timeout_seconds(
                cast("RoleProfile", profile),
                {"llm_call_timeout_seconds": 9999},
            )
            assert timeout == 2400
        finally:
            os.environ.pop("KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS", None)


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


class TestResolveMaxTokens:
    """resolve_max_tokens allows trusted context to override output budget."""

    def test_context_max_tokens_override_wins_over_requested(self) -> None:
        assert resolve_max_tokens(4000, {"llm_max_tokens": 16000}) == 16000

    def test_accepts_max_output_tokens_alias(self) -> None:
        assert resolve_max_tokens(4000, {"max_output_tokens": "12000"}) == 12000

    def test_invalid_context_override_falls_back_to_requested(self) -> None:
        assert resolve_max_tokens(4096, {"max_tokens": "bad"}) == 4096

    def test_clamps_context_override_to_hard_limit(self) -> None:
        assert resolve_max_tokens(4000, {"llm_max_tokens": 999_999}) == 128_000


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
        assert "path" in properties
        assert "target_file" in properties
        assert "limit" in properties

    def test_context_retrieve_offering_is_flag_gated(self, monkeypatch) -> None:
        """T1-A: context_retrieve is offered to the model ONLY when KERNELONE_CCR_RETRIEVE is on
        AND the role's tool policy has ccr_retrieve_opt_in=True.

        Default-off keeps the emitted native tool set byte-identical (floor-inert);
        the flag-on path closes the CCR consumer loop by offering the retrieve tool.
        This is the "tool reaches the wire" guard the prior audit passes lacked — a
        green component test never caught that the tool was never offered.

        Policy-respect: the env flag MUST NOT silently override a role's tool policy.
        A role that did not opt in will not see context_retrieve even with the flag on.
        """
        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"], ccr_retrieve_opt_in=True)

        # Flag OFF (default): context_retrieve must NOT be offered.
        monkeypatch.delenv("KERNELONE_CCR_RETRIEVE", raising=False)
        off = build_native_tool_schemas(cast("RoleProfile", profile))
        off_names = {str((it.get("function") or {}).get("name") or "") for it in off if isinstance(it, dict)}
        assert "context_retrieve" not in off_names

        # Flag ON + opt-in: context_retrieve IS offered (consumer loop closed).
        monkeypatch.setenv("KERNELONE_CCR_RETRIEVE", "1")
        on = build_native_tool_schemas(cast("RoleProfile", profile))
        on_names = {str((it.get("function") or {}).get("name") or "") for it in on if isinstance(it, dict)}
        assert "context_retrieve" in on_names

        # Floor-inertness: enabling adds EXACTLY the one tool, perturbing nothing else.
        assert on_names - off_names == {"context_retrieve"}

    def test_resident_agi_profile_can_offer_context_retrieve_when_flag_enabled(self, monkeypatch) -> None:
        """Resident AGI may read pointerized ContextOS evidence only through the gated CCR tool."""

        registry = load_core_roles()
        profile = registry.get_profile_or_raise("resident_agi")

        monkeypatch.delenv("KERNELONE_CCR_RETRIEVE", raising=False)
        off_schemas = build_native_tool_schemas(profile)
        off_names = {
            str((item.get("function") or {}).get("name") or "") for item in off_schemas if isinstance(item, dict)
        }
        assert "context_retrieve" not in off_names

        monkeypatch.setenv("KERNELONE_CCR_RETRIEVE", "1")
        on_schemas = build_native_tool_schemas(profile)
        on_names = {
            str((item.get("function") or {}).get("name") or "") for item in on_schemas if isinstance(item, dict)
        }
        assert "context_retrieve" in on_names

    def test_flag_on_without_opt_in_does_not_offer(self, monkeypatch) -> None:
        """Adversarial: env flag ON but role did NOT opt in → context_retrieve is
        NOT offered. This is the policy-respect guard for the §6.6/§8 audit
        finding (silent policy override). The flag is not a back-door.
        """
        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"], ccr_retrieve_opt_in=False)
        monkeypatch.setenv("KERNELONE_CCR_RETRIEVE", "1")
        schemas = build_native_tool_schemas(cast("RoleProfile", profile))
        names = {str((it.get("function") or {}).get("name") or "") for it in schemas if isinstance(it, dict)}
        assert "context_retrieve" not in names, (
            "KERNELONE_CCR_RETRIEVE must NOT override per-role tool policy. "
            "A role that did not opt in must not see context_retrieve."
        )

    # ----- Slice #2 adversarial tightening: env-flag gate semantics -----

    def test_flag_off_empty_whitelist_returns_empty(self, monkeypatch) -> None:
        """Defensive: empty whitelist + flag-OFF must yield [] (no offering path runs)."""
        monkeypatch.delenv("KERNELONE_CCR_RETRIEVE", raising=False)
        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=[])
        assert build_native_tool_schemas(cast("RoleProfile", profile)) == []

    def test_flag_off_byte_identical_to_historical(self, monkeypatch) -> None:
        """Floor: with flag OFF (default), the emitted schema list is byte-identical
        to historical behaviour — no offering path, no spec lookup, no side effects.
        This is the L2-floor bench invariant: enabling is opt-in.
        """
        monkeypatch.delenv("KERNELONE_CCR_RETRIEVE", raising=False)
        profile = MockProfile(role_id="director")
        # Mirror a realistic Director whitelist.
        profile.tool_policy = SimpleNamespace(
            whitelist=["read_file", "write_file", "edit_blocks", "execute_command", "repo_rg"]
        )
        schemas = build_native_tool_schemas(cast("RoleProfile", profile))
        names = {str((it.get("function") or {}).get("name") or "") for it in schemas if isinstance(it, dict)}
        # The CCR tool is not in the whitelist → must NOT appear in the emitted set.
        assert "context_retrieve" not in names

    def test_flag_on_with_weird_casing_normalizes_to_lower(self, monkeypatch) -> None:
        """Robustness: KERNELONE_CCR_RETRIEVE='Yes'/'TRUE'/'On' all enable (when opt-in)."""
        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"], ccr_retrieve_opt_in=True)
        for token in ("Yes", "TRUE", "On", "1", "true", "yes", "on"):
            monkeypatch.setenv("KERNELONE_CCR_RETRIEVE", token)
            schemas = build_native_tool_schemas(cast("RoleProfile", profile))
            names = {str((it.get("function") or {}).get("name") or "") for it in schemas if isinstance(it, dict)}
            assert "context_retrieve" in names, f"token={token!r} should enable CCR offering"

    def test_flag_on_with_garbage_value_does_not_enable(self, monkeypatch) -> None:
        """Floor: garbage values must NOT enable the offering (default-off discipline)."""
        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"], ccr_retrieve_opt_in=True)
        for token in ("maybe", "enable", " ", "", "2", "0", "off", "false", "disabled", "none"):
            monkeypatch.setenv("KERNELONE_CCR_RETRIEVE", token)
            schemas = build_native_tool_schemas(cast("RoleProfile", profile))
            names = {str((it.get("function") or {}).get("name") or "") for it in schemas if isinstance(it, dict)}
            assert "context_retrieve" not in names, f"token={token!r} should NOT enable CCR offering"

    def test_flag_on_but_spec_registration_raises_returns_original_tools(self, monkeypatch) -> None:
        """Defensive: when spec registration raises ImportError/RuntimeError/ValueError,
        the function degrades gracefully — it must still return the ORIGINAL tools
        (read_file, write_file, etc.), not crash the turn.
        """
        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"], ccr_retrieve_opt_in=True)
        monkeypatch.setenv("KERNELONE_CCR_RETRIEVE", "1")

        # Force the spec registration to raise (simulates a broken import path /
        # future registry signature change / removed module).
        import builtins as _builtins

        real_import = _builtins.__import__

        def _boom(name, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "polaris.kernelone.llm.toolkit.executor.handlers.context_retrieve":
                raise ImportError("simulated: handler module relocated")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(_builtins, "__import__", _boom)

        # Should NOT raise; the offering path's try/except must absorb the failure.
        schemas = build_native_tool_schemas(cast("RoleProfile", profile))
        names = {str((it.get("function") or {}).get("name") or "") for it in schemas if isinstance(it, dict)}
        # Original whitelist tool still present.
        assert "read_file" in names
        # context_retrieve was NOT appended (registration failed before whitelist.append).
        assert "context_retrieve" not in names

    def test_flag_on_does_not_duplicate_when_already_in_whitelist(self, monkeypatch) -> None:
        """Floor: if context_retrieve is already in the whitelist, enabling the flag
        must NOT duplicate it. At most one schema entry, regardless of source.

        Note: when the whitelist ALREADY contains context_retrieve, the policy is
        implicitly opting in (it listed the tool by name), so the flag-on path is
        not even reached — the dedup check runs against the existing list.
        """
        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file", "context_retrieve"])
        monkeypatch.setenv("KERNELONE_CCR_RETRIEVE", "1")

        schemas = build_native_tool_schemas(cast("RoleProfile", profile))
        names = [str((it.get("function") or {}).get("name") or "") for it in schemas if isinstance(it, dict)]

        # At most one entry for context_retrieve — no duplicate.
        assert names.count("context_retrieve") <= 1
        # And the original read_file is still there.
        assert "read_file" in names

    def test_flag_on_for_opted_in_role_offered(self, monkeypatch) -> None:
        """Floor: a role that explicitly opted in via ccr_retrieve_opt_in=True
        receives the CCR tool when the flag is on, regardless of role_id
        (spec registration is idempotent, so multiple roles calling concurrently
        cannot race).
        """
        for role_id in ("director", "qa", "pm", "architect", "chief_engineer"):
            profile = MockProfile(role_id=role_id)
            profile.tool_policy = SimpleNamespace(whitelist=["read_file"], ccr_retrieve_opt_in=True)
            monkeypatch.setenv("KERNELONE_CCR_RETRIEVE", "1")
            schemas = build_native_tool_schemas(cast("RoleProfile", profile))
            names = {str((it.get("function") or {}).get("name") or "") for it in schemas if isinstance(it, dict)}
            assert "context_retrieve" in names, (
                f"role={role_id} should receive the CCR tool when flag is on and opted in"
            )
            monkeypatch.delenv("KERNELONE_CCR_RETRIEVE", raising=False)


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
        assert (
            classify_error("500 Server Error: Internal Server Error url: http://localhost:8189/v1/chat/completions")
            == "network"
        )

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

    def test_direct_compatibility_facade_warns_on_construction(self) -> None:
        with pytest.warns(DeprecationWarning, match="LLMCaller is deprecated"):
            LLMCaller()

    def test_internal_adapter_can_suppress_deprecation_warning(self) -> None:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            caller = LLMCaller(emit_deprecation_warning=False)

        assert caller._emit_deprecation_warning is False
        assert not any("LLMCaller is deprecated" in str(item.message) for item in captured)

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

    @pytest.mark.asyncio
    async def test_internal_adapter_call_suppresses_deprecated_method_warning(self, monkeypatch) -> None:
        caller = LLMCaller(emit_deprecation_warning=False)

        class _FakeInvoker:
            async def call(self, **_kwargs: Any) -> LLMResponse:
                return LLMResponse(content="ok")

        monkeypatch.setattr(LLMCaller, "_get_invoker", lambda _self: _FakeInvoker())

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            response = await caller.call(
                profile=cast("RoleProfile", MockProfile()),
                system_prompt="system",
                context=cast("ContextRequest", SimpleNamespace(task_id=None)),
            )

        assert response.content == "ok"
        assert not any("LLMCaller.call() is deprecated" in str(item.message) for item in captured)


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

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("hello")

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
    async def test_prepare_llm_request_carries_resident_agi_participation_context(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="resident_agi", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=[])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("decide")

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )

        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast(
                "ContextRequest",
                SimpleNamespace(
                    task_id="task-agi-1",
                    message="decide",
                    context_override={
                        "resident_agi_enabled": True,
                        "resident_agi_participation": {
                            "enabled": True,
                            "role_turn_enabled": True,
                            "manual_role_turn_requested": True,
                            "automatic_participation_enabled": False,
                            "configured_enabled": False,
                            "configured_scopes": ["quality_gate_response"],
                            "required_role_turn_scopes": ["final_request_audit", "decision_trace"],
                            "scopes": ["final_request_audit", "quality_gate_response", "evidence.interface.selection"],
                            "configured_participation": {"architecture_option_selection": False},
                            "automatic_participation": {
                                "final_request_audit": False,
                                "quality_gate_response": False,
                            },
                            "participation": {
                                "final_request_audit": True,
                                "architecture_option_selection": False,
                                "evidence_interface_selection": True,
                            },
                        },
                        "resident_agi_audit_pack": {
                            "schema_version": "resident.agi_audit_pack.v1",
                            "capability_surface": {
                                "schema_version": "resident.agi_capability_surface.v1",
                                "decision_boundary_schema": "resident.agi_decision_boundary.v1",
                                "decision_boundaries": [{"boundary_id": "role.runtime.foundation"}],
                                "decision_capability_registry": {
                                    "schema_version": "resident.agi_decision_capability_registry.v1",
                                },
                            },
                        },
                        "resident_agi_decision_contract": {
                            "schema_version": "resident.agi_decision_contract.v1",
                            "decision_capability_id": "quality.gate.response",
                        },
                        "metadata": {"resident_agi_role_runtime_required": True},
                    },
                ),
            ),
            temperature=0.2,
            max_tokens=256,
            stream=False,
        )

        agi_context = prepared.ai_request.context["resident_agi_audit_context"]
        assert agi_context["enabled"] is True
        assert agi_context["role_turn_enabled"] is True
        assert agi_context["manual_role_turn_requested"] is True
        assert agi_context["automatic_participation_enabled"] is False
        assert agi_context["configured_enabled"] is False
        assert agi_context["configured_scopes"] == ["quality_gate_response"]
        assert agi_context["required_role_turn_scopes"] == ["final_request_audit", "decision_trace"]
        assert agi_context["participation_scopes"] == [
            "final_request_audit",
            "quality_gate_response",
            "evidence.interface.selection",
        ]
        assert agi_context["participation"]["final_request_audit"] is True
        assert agi_context["participation"]["architecture_option_selection"] is False
        assert agi_context["participation"]["evidence_interface_selection"] is True
        assert agi_context["audit_pack_schema_version"] == "resident.agi_audit_pack.v1"
        assert agi_context["capability_surface_schema_version"] == "resident.agi_capability_surface.v1"
        assert agi_context["decision_boundary_schema"] == "resident.agi_decision_boundary.v1"
        assert agi_context["decision_boundary_count"] == 1
        assert agi_context["decision_capability_id"] == "quality.gate.response"

    @pytest.mark.asyncio
    async def test_prepare_llm_request_honors_context_timeout_override(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=[])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("hello")

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )

        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast(
                "ContextRequest",
                SimpleNamespace(
                    task_id=None,
                    context_override={"llm_call_timeout_seconds": 45},
                ),
            ),
            temperature=0.2,
            max_tokens=256,
            stream=False,
        )

        assert prepared.request_options["timeout"] == 660

    @pytest.mark.asyncio
    async def test_prepare_llm_request_honors_context_max_tokens_override(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="pm", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=[])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("hello")

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )

        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast(
                "ContextRequest",
                SimpleNamespace(
                    task_id=None,
                    context_override={"llm_max_tokens": 16000},
                ),
            ),
            temperature=0.2,
            max_tokens=256,
            stream=False,
        )

        assert prepared.request_options["max_tokens"] == 16000
        assert prepared.ai_request.options["max_tokens"] == 16000

    @pytest.mark.asyncio
    async def test_prepare_llm_request_stream_enables_native_tools(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file"])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("hello")

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

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("hello")

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
    async def test_prepare_llm_request_honors_dict_forced_tool_choice_override(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file", "write_file"])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("create index.html")

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )
        monkeypatch.setattr(
            LLMCaller,
            "_build_native_tool_schemas",
            staticmethod(lambda _profile: [{"type": "function", "function": {"name": "read_file"}}]),
        )

        forced_tools = [{"type": "function", "function": {"name": "write_file"}}]
        forced_choice = {"type": "function", "function": {"name": "write_file"}}
        context = SimpleNamespace(
            task_id=None,
            context_override={
                "_transaction_kernel_forced_tool_definitions": forced_tools,
                "_transaction_kernel_forced_tool_choice": forced_choice,
            },
        )
        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast("ContextRequest", context),
            temperature=0.2,
            max_tokens=256,
            stream=False,
        )

        assert prepared.native_tool_mode == "native_tools"
        assert prepared.request_options["tools"] == forced_tools
        assert prepared.request_options["tool_choice"] == forced_choice

    @pytest.mark.asyncio
    async def test_prepare_llm_request_quality_repair_forced_tools_keep_read_locate_context(
        self,
        monkeypatch,
    ) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file", "repo_tree", "write_file"])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("repair missing moon model")

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )
        monkeypatch.setattr(
            LLMCaller,
            "_build_native_tool_schemas",
            staticmethod(
                lambda _profile: [
                    {"type": "function", "function": {"name": "write_file"}},
                    {"type": "function", "function": {"name": "read_file"}},
                    {"type": "function", "function": {"name": "repo_tree"}},
                    {"type": "function", "function": {"name": "scout_probe"}},
                ]
            ),
        )

        forced_tools = [{"type": "function", "function": {"name": "write_file"}}]
        forced_choice = {"type": "function", "function": {"name": "write_file"}}
        context = SimpleNamespace(
            task_id=None,
            context_override={
                "_transaction_kernel_forced_tool_definitions": forced_tools,
                "_transaction_kernel_forced_tool_choice": forced_choice,
                "director_quality_repair": {
                    "missing_target_files": ["src/models/moon.ts"],
                    "repair_target_files": ["src/models/moon.ts"],
                    "write_only_single_target": {
                        "tool": "write_file",
                        "target_file": "src/models/moon.ts",
                    },
                },
            },
        )
        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast("ContextRequest", context),
            temperature=0.2,
            max_tokens=256,
            stream=False,
        )

        tool_names = [
            str(item.get("function", {}).get("name") or "")
            for item in prepared.request_options["tools"]
            if isinstance(item.get("function"), dict)
        ]
        assert tool_names == ["write_file", "read_file", "repo_tree"]
        assert prepared.request_options["tool_choice"] == forced_choice

    @pytest.mark.asyncio
    async def test_prepare_llm_request_honors_explicit_empty_forced_tools(self, monkeypatch) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        profile.tool_policy = SimpleNamespace(whitelist=["read_file", "edit_file"])

        class _FakeGateway:
            def __init__(self, _profile, _workspace) -> None:
                pass

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("return fenced file blocks")

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway",
            _FakeGateway,
        )
        monkeypatch.setattr(
            LLMCaller,
            "_build_native_tool_schemas",
            staticmethod(lambda _profile: [{"type": "function", "function": {"name": "read_file"}}]),
        )

        context = SimpleNamespace(
            task_id=None,
            context_override={
                "_transaction_kernel_forced_tool_definitions": [],
                "_transaction_kernel_forced_tool_choice": "none",
            },
        )
        prepared = await caller._prepare_llm_request(
            profile=cast("RoleProfile", profile),
            system_prompt="system",
            context=cast("ContextRequest", context),
            temperature=0.2,
            max_tokens=256,
            stream=False,
        )

        assert prepared.native_tool_mode == "disabled"
        assert "tools" not in prepared.request_options
        assert "tool_choice" not in prepared.request_options
        assert prepared.ai_request.context["native_tool_mode"] == "disabled"

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

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("hello")

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

            async def build_context(self, _context, *, system_prompt=None):
                return _turn_context_result("hello")

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

            async def build_context(self, _context, *, system_prompt=None) -> NoReturn:
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
                    "chat_messages": [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "hello"},
                    ],
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
        assert "运行时结构化输出回退" in request.context["chat_messages"][-1]["content"]

    def test_native_tool_fallback_request_updates_provider_bound_chat_messages(self) -> None:
        caller = LLMCaller(workspace="C:/workspace")
        prepared = SimpleNamespace(
            request_options={
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout": 120,
                "tools": [{"type": "function", "function": {"name": "read_file"}}],
                "tool_choice": "auto",
            },
            input_text="read README",
            ai_request=SimpleNamespace(
                context={
                    "workspace": "C:/workspace",
                    "mode": "chat",
                    "native_tool_mode": "native_tools",
                    "chat_messages": [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "read README"},
                    ],
                }
            ),
        )

        request = caller._build_native_tool_fallback_request(
            prepared=cast("PreparedLLMRequest", prepared),
            profile=cast("RoleProfile", MockProfile(role_id="director", model="qwen", provider_id="local")),
        )

        assert "tools" not in request.options
        assert "tool_choice" not in request.options
        assert request.context["native_tool_mode"] == "native_tools_text_fallback"
        assert "运行时工具回退" in request.input
        assert "运行时工具回退" in request.context["chat_messages"][-1]["content"]

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

            def build_context(self, _context, *, system_prompt=None) -> NoReturn:
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

            def build_context(self, _context, *, system_prompt=None) -> NoReturn:
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
    async def test_invoker_stream_call_end_prefers_provider_usage(self, monkeypatch) -> None:
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
                yield {
                    "type": "complete",
                    "content": "",
                    "metadata": {
                        "usage": {
                            "input_tokens": 321,
                            "output_tokens": 45,
                            "total_tokens": 366,
                        }
                    },
                }

        def _normalize_stream_chunk(chunk, **_kwargs):
            return SimpleNamespace(
                event_type=chunk["type"],
                content=chunk.get("content", ""),
                metadata=dict(chunk.get("metadata", {})),
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
            "polaris.cells.roles.kernel.internal.llm_caller.stream_engine.normalize_stream_chunk",
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

        context_metadata = next(event for event in events if event["type"] == "context_metadata")
        assert context_metadata["usage_source"] == "provider"
        assert context_metadata["usage"]["prompt_tokens"] == 321
        assert context_metadata["usage"]["completion_tokens"] == 45
        assert context_metadata["usage"]["total_tokens"] == 366
        assert captured["prompt_tokens"] == 321
        assert captured["completion_tokens"] == 45
        assert captured["metadata"]["usage_source"] == "provider"
        assert captured["metadata"]["usage"]["total_tokens"] == 366

    @pytest.mark.asyncio
    async def test_structured_fallback_parse_error_includes_final_request_audit(self, monkeypatch) -> None:
        invoker = LLMInvoker(workspace="C:/workspace")
        profile = MockProfile(role_id="director", model="gpt-5", provider_id="openai")
        captured_error: dict[str, Any] = {}
        fallback_request = SimpleNamespace(
            context={
                "chat_messages": [
                    {
                        "role": "user",
                        "content": "TASK-1 target_files src/app.ts Chief Engineer blueprint",
                    }
                ],
                "response_format_mode": "text_json_fallback",
            },
            options={},
            input="",
        )
        prepared = PreparedLLMRequest(
            messages=[{"role": "user", "content": "TASK-1 target_files src/app.ts"}],
            input_text="TASK-1 target_files src/app.ts",
            context_result=SimpleNamespace(
                token_estimate=42,
                compression_strategy="none",
                compression_applied=False,
            ),
            context_summary="summary",
            request_options={"response_format": {"type": "json_schema"}},
            ai_request=SimpleNamespace(context={}, options={"response_format": {"type": "json_schema"}}, input=""),
            native_response_format={"type": "json_schema"},
        )

        class _FakeCaller:
            def _build_structured_fallback_request(self, **_kwargs: Any) -> Any:
                return fallback_request

        class _FakeExecutor:
            async def invoke(self, _request: Any) -> Any:
                return SimpleNamespace(ok=True, output="not json", raw={}, error=None)

        monkeypatch.setattr(LLMInvoker, "_get_executor", lambda _self: _FakeExecutor())
        monkeypatch.setattr(
            LLMInvoker,
            "_emit_call_error_event",
            lambda _self, **kwargs: captured_error.update(kwargs),
        )

        result = await invoker._run_structured_fallback(
            caller=_FakeCaller(),
            prepared=prepared,
            profile=cast("RoleProfile", profile),
            response_model=dict,
            model="gpt-5",
            prompt_tokens=42,
            turn_round=0,
            role_id="director",
            run_id="run_structured",
            task_id="task_structured",
            attempt=0,
            call_id="call_structured",
            event_emitter=None,
            start_time=0.0,
        )

        assert result.error_category == "validation_fail"
        assert result.metadata["final_request_context_audit"]["response_format_token_estimate"] == 0
        assert (
            result.metadata["contextTokens"]
            == result.metadata["final_request_context_audit"]["final_request_token_estimate"]
        )
        assert captured_error["metadata"]["final_request_context_audit"]["response_format_token_estimate"] == 0
        assert (
            captured_error["metadata"]["contextTokens"]
            == captured_error["metadata"]["final_request_context_audit"]["final_request_token_estimate"]
        )

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
                messages=[
                    {"role": "system", "content": "SECRET SYSTEM CONTENT"},
                    {"role": "user", "content": "SECRET USER CONTENT"},
                ],
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
        assert payload["message_roles"] == ["system", "user"]
        assert payload["message_content_sha256"]
        assert "messages" not in payload
        serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        assert "SECRET SYSTEM CONTENT" not in serialized_payload
        assert "SECRET USER CONTENT" not in serialized_payload
