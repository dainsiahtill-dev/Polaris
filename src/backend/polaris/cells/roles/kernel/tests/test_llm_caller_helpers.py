"""Tests for roles.kernel LLM helper modules.

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
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    native_tool_call_count_from_metadata,
    native_tool_call_facts_from_sources,
    tool_call_lifecycle_receipts_from_metadata,
)
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    build_final_request_context_audit_for_request,
)
from polaris.cells.roles.kernel.internal.llm_caller.error_handling import (
    classify_error,
    is_native_tool_calling_unsupported,
    is_retryable_error,
)
from polaris.cells.roles.kernel.internal.llm_caller.helpers import (
    build_native_tool_call_envelope_payloads,
    extract_json_from_text,
    extract_native_tool_calls,
    messages_to_input,
    resolve_max_tokens,
    resolve_platform_retry_max,
    resolve_temperature,
    resolve_timeout_seconds,
    resolve_tool_call_provider,
)
from polaris.cells.roles.kernel.internal.llm_caller.invoker import (
    _recover_text_tool_calls_from_response_text,
    _required_tool_not_called_error,
    _store_active_request_context_snapshot,
)
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import (
    LLMRequestPreparer,
    _tool_contract_context_fields,
)
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    build_native_tool_call_from_stream_event,
    build_native_tool_schemas,
    native_tool_call_envelopes_from_metadata,
    native_tool_call_envelopes_from_response,
    native_tool_call_name,
    native_tool_call_provider_from_metadata,
    native_tool_calls_from_response,
    provider_response_hash,
    stream_tool_call_signature,
    supersede_partial_tool_calls,
    upsert_stream_native_tool_call,
)
from polaris.cells.roles.profile.public.service import load_core_roles
from polaris.kernelone.context.contracts import TurnEngineContextResult
from polaris.kernelone.storage.io_paths import resolve_storage_roots


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


def test_stream_event_native_tool_call_projection_uses_decoder_shape() -> None:
    call = build_native_tool_call_from_stream_event(
        tool_name="write_file",
        tool_args={"file": "src/index.ts", "content": "ok"},
        call_id="call-1",
        ordinal=1,
    )

    assert call == {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": {"file": "src/index.ts", "content": "ok"},
        },
    }


def test_native_tool_call_name_uses_run_ledger_name_projection() -> None:
    assert native_tool_call_name({"functionName": " write_file ", "arguments": {"path": "x.py"}}) == "write_file"
    assert native_tool_call_name({"function": {"name": "execute_command", "arguments": "{}"}}) == "execute_command"


def test_stream_tool_call_signature_is_stable() -> None:
    first = stream_tool_call_signature("write_file", {"b": 2, "a": 1}, "call-1")
    second = stream_tool_call_signature("write_file", {"a": 1, "b": 2}, "call-1")

    assert first == second


def test_upsert_stream_native_tool_call_refines_same_call_id() -> None:
    calls: list[dict[str, object]] = []
    index: dict[str, int] = {}

    upsert_stream_native_tool_call(calls, index, tool_name="edit_file", tool_args={}, call_id="call-1")
    upsert_stream_native_tool_call(
        calls,
        index,
        tool_name="edit_file",
        tool_args={"file": "src/app.ts", "old": "a", "new": "b"},
        call_id="call-1",
    )

    assert len(calls) == 1
    assert calls[0]["function"] == {
        "name": "edit_file",
        "arguments": {"file": "src/app.ts", "old": "a", "new": "b"},
    }


def test_supersede_partial_tool_calls_keeps_distinct_same_tool_calls() -> None:
    partial = build_native_tool_call_from_stream_event(
        tool_name="edit_file",
        tool_args={"file": "src/app.ts"},
        call_id="partial",
        ordinal=1,
    )
    complete = build_native_tool_call_from_stream_event(
        tool_name="edit_file",
        tool_args={"file": "src/app.ts", "old": "a", "new": "b"},
        call_id="complete",
        ordinal=2,
    )
    distinct = build_native_tool_call_from_stream_event(
        tool_name="edit_file",
        tool_args={"file": "src/other.ts"},
        call_id="distinct",
        ordinal=3,
    )

    assert supersede_partial_tool_calls([partial, complete, distinct]) == [complete, distinct]


def test_tool_contract_context_fields_project_materialization_write_requirement() -> None:
    fields = _tool_contract_context_fields(
        {
            "director_first_call_materialization_scope": {
                "schema_version": "director.first_call_materialization_scope.v1",
                "injected": True,
                "tool": "write_file",
            }
        }
    )

    assert fields["required_tools"] == ["write_file"]
    assert fields["tool_contract"]["required_tools"] == ["write_file"]


def test_tool_contract_context_fields_skip_projection_when_tool_surface_disabled() -> None:
    """A finalization-style call (explicit tool disable) must not inherit
    required-tool semantics from the shared turn context, even when the turn
    carries a stale forced-write projection."""

    fields = _tool_contract_context_fields(
        {
            "required_tools": ["write_file"],
            "tool_contract": {"required_tools": ["write_file"]},
            "director_first_call_materialization_scope": {
                "schema_version": "director.first_call_materialization_scope.v1",
                "injected": True,
                "tool": "write_file",
            },
            "_transaction_kernel_forced_tool_definitions": [],
            "_transaction_kernel_forced_tool_choice": "none",
        }
    )

    assert fields == {}


def test_required_tool_retry_request_handles_non_numeric_temperature_and_tool_contract() -> None:
    messages = [{"role": "user", "content": "TASK-1 target_files package.json"}]
    ai_request = SimpleNamespace(
        task_type="dialogue",
        context={
            "workspace": "/tmp/example",
            "tool_contract": {"required_tools": ["write_file"]},
            "chat_messages": messages,
        },
    )
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="TASK-1 target_files package.json",
        context_result=_turn_context_result("TASK-1 target_files package.json"),
        context_summary="summary",
        request_options={
            "temperature": "not-a-float",
            "max_tokens": 128000,
            "timeout": 660,
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "tool_choice": "auto",
        },
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    retry_request = LLMRequestPreparer(workspace="/tmp/example")._build_required_tool_retry_request(
        prepared=prepared,
        profile=cast("RoleProfile", MockProfile(role_id="director")),
        error_message="required_tool_not_called: required_tools=write_file",
    )

    assert retry_request.options["temperature"] == 0.2
    assert retry_request.options["max_tokens"] == 7000
    assert retry_request.options["timeout"] == 120.0
    assert retry_request.options["tool_choice"] == "auto"
    assert retry_request.context["required_tool_retry"] is True
    assert retry_request.context["required_tool_retry_budget"]["max_tokens"] == 7000
    assert retry_request.context["required_tool_retry_budget"]["timeout_seconds"] == 120.0
    assert "write_file" in retry_request.input
    assert "必须立即发出真实工具调用" in retry_request.input


def test_required_tool_text_fallback_request_disables_native_tools_and_requests_json() -> None:
    messages = [{"role": "user", "content": "TASK-1 target_files package.json"}]
    ai_request = SimpleNamespace(
        task_type="dialogue",
        context={
            "workspace": "/tmp/example",
            "tool_contract": {"required_tools": ["write_file"]},
            "chat_messages": messages,
        },
    )
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="TASK-1 target_files package.json",
        context_result=_turn_context_result("TASK-1 target_files package.json"),
        context_summary="summary",
        request_options={
            "temperature": 0.8,
            "max_tokens": 128000,
            "timeout": 660,
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "tool_choice": {"type": "function", "function": {"name": "write_file"}},
        },
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    fallback_request = LLMRequestPreparer(workspace="/tmp/example")._build_required_tool_text_fallback_request(
        prepared=prepared,
        profile=cast("RoleProfile", MockProfile(role_id="director", provider_id="kimi")),
        error_message="required_tool_not_called: required_tools=write_file",
    )

    assert "tools" not in fallback_request.options
    assert fallback_request.options["tool_choice"] == "none"
    assert fallback_request.options["temperature"] == 0.0
    assert fallback_request.options["max_tokens"] == 7000
    assert fallback_request.options["timeout"] == 120.0
    assert fallback_request.context["required_tool_text_fallback"] is True
    assert fallback_request.context["required_tool_text_fallback_budget"]["required_tools"] == ["write_file"]
    assert "UTF-8 JSON 数组" in fallback_request.input
    assert '"name":"write_file"' in fallback_request.input
    audit = build_final_request_context_audit_for_request(
        ai_request=fallback_request,
        prepared=prepared,
        profile=cast("RoleProfile", MockProfile(role_id="director", provider_id="kimi")),
    )
    surface = audit["tool_execution_surface"]
    assert audit["native_tool_surface_absent_because_text_fallback"] is True
    assert surface["compatibility_mode"] == "required_tool_text_fallback"
    assert surface["convergence_status"] == "pending_text_parser_dispatch"
    assert surface["convergence_proven"] is False


def test_recover_text_tool_calls_from_response_text_uses_allowed_tools() -> None:
    messages = [{"role": "user", "content": "TASK-1 target_files package.json"}]
    ai_request = SimpleNamespace(task_type="dialogue", context={"workspace": "/tmp/example"})
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="TASK-1 target_files package.json",
        context_result=_turn_context_result("TASK-1 target_files package.json"),
        context_summary="summary",
        request_options={},
        ai_request=ai_request,
        native_tool_schemas=[{"type": "function", "function": {"name": "write_file"}}],
    )

    recovered = _recover_text_tool_calls_from_response_text(
        response_text='[{"name":"write_file","arguments":{"path":"package.json","content":"{\\"scripts\\":{}}"}}]',
        raw_payload={},
        prepared=prepared,
        provider_hint="auto",
    )

    assert recovered.parser_attempted is True
    assert recovered.error == ""
    assert len(recovered.calls) == 1
    assert recovered.calls[0]["type"] == "function"
    assert recovered.calls[0]["function"]["name"] == "write_file"
    arguments = json.loads(str(recovered.calls[0]["function"]["arguments"]))
    assert arguments["file"] == "package.json"
    assert arguments["content"] == '{"scripts":{}}'


def test_required_tool_not_called_error_accepts_text_tool_envelope() -> None:
    messages = [{"role": "user", "content": "TASK-1 target_files package.json"}]
    ai_request = SimpleNamespace(
        task_type="dialogue",
        context={
            "workspace": "/tmp/example",
            "required_tools": ["write_file"],
            "tool_contract": {"required_tools": ["write_file"]},
        },
    )
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="TASK-1 target_files package.json",
        context_result=_turn_context_result("TASK-1 target_files package.json"),
        context_summary="summary",
        request_options={
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "tool_choice": {"type": "function", "function": {"name": "write_file"}},
        },
        ai_request=ai_request,
        native_tool_schemas=[{"type": "function", "function": {"name": "write_file"}}],
    )
    response = SimpleNamespace(
        output='[{"name":"write_file","arguments":{"path":"package.json","content":"{\\"scripts\\":{}}"}}]',
        raw={},
        model="kimi-for-coding",
        provider_id="anthropic_compat-kimi",
    )

    error = _required_tool_not_called_error(
        prepared=prepared,
        active_request=ai_request,
        response=response,
        profile=MockProfile(role_id="director", provider_id="kimi", model="kimi-for-coding"),
    )

    assert error == ""


@pytest.mark.asyncio
async def test_store_active_request_context_snapshot_uses_fallback_request(tmp_path) -> None:
    messages = [{"role": "user", "content": "TASK-1 target_files package.json"}]
    ai_request = SimpleNamespace(
        task_type="dialogue",
        context={
            "workspace": str(tmp_path),
            "context_snapshot_ref": "stale-original-ref",
            "tool_contract": {"required_tools": ["write_file"]},
            "chat_messages": messages,
        },
    )
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="TASK-1 target_files package.json",
        context_result=_turn_context_result("TASK-1 target_files package.json"),
        context_summary="summary",
        request_options={
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "tool_choice": {"type": "function", "function": {"name": "write_file"}},
        },
        ai_request=ai_request,
        native_tool_schemas=[],
    )
    fallback_request = LLMRequestPreparer(workspace=str(tmp_path))._build_required_tool_text_fallback_request(
        prepared=prepared,
        profile=cast("RoleProfile", MockProfile(role_id="director", provider_id="kimi")),
        error_message="required_tool_not_called: required_tools=write_file",
    )

    context_ref = await _store_active_request_context_snapshot(
        workspace=str(tmp_path),
        active_request=fallback_request,
        prepared=prepared,
        profile=cast("RoleProfile", MockProfile(role_id="director", provider_id="kimi")),
        run_id="run-1",
        call_id="call-1-required-tool-text-fallback",
    )

    assert context_ref
    assert fallback_request.context["context_snapshot_ref"] == context_ref
    assert context_ref != "stale-original-ref"
    roots = resolve_storage_roots(str(tmp_path))
    snapshot_path = Path(roots.runtime_root) / "contexts" / context_ref[:2] / context_ref
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    provider_request = snapshot["provider_request"]
    assert provider_request["tool_schema_count"] == 0
    assert provider_request["tool_choice"] == "none"
    assert provider_request["tools"] == []
    assert "UTF-8 JSON 数组" in json.dumps(snapshot["messages"], ensure_ascii=False)


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

    def test_director_context_timeout_ceiling_can_reduce_role_default(self) -> None:
        profile = MockProfile(role_id="director")
        timeout = resolve_timeout_seconds(
            cast("RoleProfile", profile),
            {"llm_call_timeout_ceiling_seconds": 45},
        )
        assert timeout == 45

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

    def test_execution_strategy_output_budget_wins_over_requested(self) -> None:
        assert (
            resolve_max_tokens(
                4000,
                {
                    "task_execution_strategy": {
                        "schema_version": "task.execution_strategy.v1",
                        "output_budget_tokens": 96_000,
                    }
                },
            )
            == 96_000
        )

    def test_execution_contract_output_budget_wins_over_requested(self) -> None:
        assert (
            resolve_max_tokens(
                4000,
                {
                    "task_execution_contract": {
                        "schema_version": "task.execution_contract.v1",
                        "context_budget": {"output_budget_tokens": 112_000},
                    }
                },
            )
            == 112_000
        )


class TestResolveTemperature:
    """resolve_temperature consumes execution profile and strategy controls."""

    def test_execution_strategy_temperature_wins_over_requested(self) -> None:
        assert (
            resolve_temperature(
                0.7,
                {
                    "task_execution_strategy": {
                        "schema_version": "task.execution_strategy.v1",
                        "temperature": 0.05,
                    }
                },
            )
            == 0.05
        )

    def test_execution_contract_temperature_wins_over_requested(self) -> None:
        assert (
            resolve_temperature(
                0.7,
                {
                    "task_execution_contract": {
                        "schema_version": "task.execution_contract.v1",
                        "sampling": {"temperature": 0.03},
                    }
                },
            )
            == 0.03
        )

    def test_execution_profile_temperature_is_backstop(self) -> None:
        assert (
            resolve_temperature(
                0.7,
                {
                    "director_execution_profile": {
                        "schema_version": "task.execution_profile.v1",
                        "temperature": 0.15,
                    }
                },
            )
            == 0.15
        )


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
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=["repo_read_head", "repo_rg"])
        monkeypatch.setattr(
            ToolSpecRegistry,
            "get_llm_schema",
            classmethod(lambda cls, _name, **_kwargs: None),
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
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        profile = MockProfile(role_id="director")
        profile.tool_policy = SimpleNamespace(whitelist=["repo_read_head"])
        monkeypatch.setattr(
            ToolSpecRegistry,
            "get_llm_schema",
            classmethod(lambda cls, _name, **_kwargs: None),
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
        envelopes = build_native_tool_call_envelope_payloads(calls, provider=provider)
        assert envelopes[0]["schema_version"] == "native_tool_call_envelope.v1"
        assert envelopes[0]["provider"] == "openai"
        assert envelopes[0]["tool_name"] == "read_file"
        assert envelopes[0]["call_id"] == "call_abc"
        assert "path" not in envelopes[0]
        assert len(envelopes[0]["arguments_hash"]) == 64
        assert len(envelopes[0]["raw_call_hash"]) == 64

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
        envelopes = build_native_tool_call_envelope_payloads(calls, provider=provider)
        assert envelopes[0]["provider"] == "anthropic"
        assert envelopes[0]["tool_name"] == "read_file"
        assert envelopes[0]["call_id"].startswith("native_tool_call_")

    def test_native_tool_call_count_and_names_prefer_envelopes(self) -> None:
        metadata = {
            "native_tool_call_envelopes": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "repo_rg"},
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "read_file"},
            ]
        }
        raw_calls = [{"function": {"name": "write_file"}}]

        assert native_tool_call_facts_from_sources(metadata, raw_calls) == {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["repo_rg", "read_file"],
        }

    def test_native_tool_call_facts_prefer_envelopes(self) -> None:
        metadata = {
            "native_tool_call_envelopes": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "repo_rg"},
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "read_file"},
            ],
            "native_tool_calls_count": 9,
            "native_tool_call_names": ["stale_tool"],
        }
        raw_calls = [{"function": {"name": "write_file"}}]

        assert native_tool_call_facts_from_sources(metadata, raw_calls) == {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["repo_rg", "read_file"],
        }

    def test_native_tool_call_sources_accept_response_object_and_mapping(self) -> None:
        object_response = SimpleNamespace(native_tool_calls=[{"function": {"name": "write_file"}}])
        mapping_response = {"tool_calls": [{"function": {"name": "read_file"}}]}

        assert native_tool_calls_from_response(object_response) == [
            {"function": {"name": "write_file"}},
        ]
        assert native_tool_call_facts_from_sources({}, native_tool_calls_from_response(mapping_response)) == {
            "native_tool_calls_count": 1,
            "native_tool_call_names": ["read_file"],
        }

    def test_provider_response_hash_binds_tool_call_payload_and_metadata(self) -> None:
        response = SimpleNamespace(
            content="ok",
            model="model-a",
            native_tool_calls=[{"function": {"name": "write_file"}}],
            thinking=None,
        )
        metadata = {
            "native_tool_call_envelopes": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"}
            ]
        }

        first_hash = provider_response_hash(response, metadata)
        second_hash = provider_response_hash(response, metadata)
        changed_hash = provider_response_hash(response, {"native_tool_call_envelopes": []})

        assert first_hash == second_hash
        assert first_hash != changed_hash

    def test_native_tool_call_envelopes_from_response_prefers_metadata_then_raw_calls(self) -> None:
        response = SimpleNamespace(
            native_tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": {"file": "src/main.py"}},
                }
            ]
        )
        metadata = {
            "tool_call_provider": "OpenAI",
            "native_tool_call_envelopes": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "read_file"}
            ],
        }

        assert native_tool_call_provider_from_metadata(metadata) == "openai"
        assert native_tool_call_envelopes_from_response(response, metadata) == [
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "read_file"}
        ]

        raw_envelopes = native_tool_call_envelopes_from_response(response, {"provider_id": "anthropic"})
        assert raw_envelopes[0]["provider"] == "anthropic"
        assert raw_envelopes[0]["tool_name"] == "write_file"

    def test_native_tool_call_envelopes_deduplicate_by_envelope_identity(self) -> None:
        metadata = {
            "native_tool_call_envelopes": [
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
                    "tool_name": "write_file",
                },
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
                    "tool_name": "write_file",
                },
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:1:call-2:abcdef",
                    "tool_name": "execute_command",
                },
            ]
        }
        raw_calls = [{"function": {"name": "read_file"}}]

        envelopes = native_tool_call_envelopes_from_metadata(metadata)

        assert [envelope["tool_name"] for envelope in envelopes] == ["write_file", "execute_command"]
        assert native_tool_call_facts_from_sources(metadata, raw_calls) == {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["write_file", "execute_command"],
        }

    def test_native_tool_call_count_from_metadata_keeps_legacy_numeric_as_fallback(self) -> None:
        metadata = {
            "native_tool_call_envelope_refs": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "execute_command"},
            ],
            "native_tool_calls_count": 1,
        }

        assert native_tool_call_count_from_metadata(metadata, fallback=0) == 2
        assert native_tool_call_count_from_metadata({"native_tool_calls_count": 3}, fallback=1) == 3
        assert native_tool_call_count_from_metadata({}, fallback=2) == 2

    def test_native_tool_call_count_derives_from_count_only_lifecycle_receipt(self) -> None:
        metadata = {
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_calls_count": 5,
                "decoded_tool_calls_count": 0,
                "dispatched_tool_calls_count": 0,
                "dispatch_status": "",
                "failure_class": "",
            }
        }

        assert native_tool_call_facts_from_sources(metadata, ()) == {
            "native_tool_calls_count": 5,
            "native_tool_call_names": [],
        }
        assert native_tool_call_count_from_metadata(metadata, fallback=1) == 5

    def test_native_tool_call_count_treats_zero_lifecycle_receipt_as_authoritative(self) -> None:
        metadata = {
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_calls_count": 0,
                "decoded_tool_calls_count": 0,
                "dispatched_tool_calls_count": 0,
                "dispatch_status": "blocked",
                "failure_class": "MISSING_TOOL_RESULT",
            },
            "native_tool_calls_count": 9,
        }
        raw_calls = [{"function": {"name": "write_file"}}]

        assert native_tool_call_facts_from_sources(metadata, raw_calls) == {
            "native_tool_calls_count": 0,
            "native_tool_call_names": [],
        }
        assert native_tool_call_count_from_metadata(metadata, fallback=3) == 0

    def test_native_tool_call_names_treat_lifecycle_receipt_without_names_as_authoritative(self) -> None:
        metadata = {
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_calls_count": 0,
                "decoded_tool_calls_count": 0,
                "dispatched_tool_calls_count": 0,
                "dispatch_status": "blocked",
                "failure_class": "MISSING_TOOL_RESULT",
            }
        }
        raw_calls = [{"function": {"name": "write_file"}}]

        assert native_tool_call_facts_from_sources(metadata, raw_calls) == {
            "native_tool_calls_count": 0,
            "native_tool_call_names": [],
        }

    def test_native_tool_call_count_accepts_canonical_lifecycle_alias(self) -> None:
        metadata = {
            "tool_call_lifecycle": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_calls_count": 2,
                "decoded_tool_calls_count": 2,
                "dispatched_tool_calls_count": 0,
                "dropped_tool_calls": [
                    {"tool_name": "write_file", "reason": "tool_dispatch_dropped"},
                    {"tool_name": "execute_command", "reason": "tool_dispatch_dropped"},
                ],
            }
        }

        assert native_tool_call_facts_from_sources(metadata, ()) == {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["write_file", "execute_command"],
        }
        assert native_tool_call_count_from_metadata(metadata, fallback=1) == 2

    def test_native_tool_call_names_derive_from_lifecycle_dropped_refs(self) -> None:
        metadata = {
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_calls_count": 2,
                "decoded_tool_calls_count": 2,
                "dispatched_tool_calls_count": 0,
                "dropped_tool_calls": [
                    {"tool_name": "write_file", "reason": "tool_dispatch_dropped"},
                    {"tool_name": "execute_command", "reason": "tool_dispatch_dropped"},
                ],
            }
        }

        assert native_tool_call_facts_from_sources(metadata, ()) == {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["write_file", "execute_command"],
        }

    def test_native_tool_call_count_and_names_accept_lifecycle_envelope_refs(self) -> None:
        metadata = {
            "native_tool_call_envelope_refs": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "execute_command"},
            ]
        }
        raw_calls = [{"function": {"name": "read_file"}}]

        assert native_tool_call_facts_from_sources(metadata, raw_calls) == {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["write_file", "execute_command"],
        }

    def test_native_tool_call_envelope_refs_survive_invalid_legacy_alias(self) -> None:
        metadata = {
            "native_tool_call_envelopes": ["bad legacy projection"],
            "native_tool_call_envelope_refs": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
            ],
        }
        raw_calls = [{"function": {"name": "read_file"}}]

        envelopes = native_tool_call_envelopes_from_metadata(metadata)

        assert len(envelopes) == 1
        assert envelopes[0]["tool_name"] == "write_file"
        assert native_tool_call_facts_from_sources(metadata, raw_calls) == {
            "native_tool_calls_count": 1,
            "native_tool_call_names": ["write_file"],
        }

    def test_native_tool_call_count_and_names_derive_from_lifecycle_receipt(self) -> None:
        metadata = {
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_call_envelope_refs": [
                    {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
                    {"schema_version": "native_tool_call_envelope.v1", "tool_name": "execute_command"},
                ],
            }
        }
        raw_calls = [{"function": {"name": "read_file"}}]

        envelopes = native_tool_call_envelopes_from_metadata(metadata)

        assert len(envelopes) == 2
        assert native_tool_call_facts_from_sources(metadata, raw_calls) == {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["write_file", "execute_command"],
        }

    def test_lifecycle_receipts_deduplicate_canonical_and_legacy_aliases(self) -> None:
        receipt = {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
            ],
        }
        metadata = {
            "tool_call_lifecycle_receipt": receipt,
            "tool_call_lifecycle": dict(receipt),
            "tool_call_lifecycle_receipts": [dict(receipt)],
        }

        receipts = tool_call_lifecycle_receipts_from_metadata(metadata)

        assert len(receipts) == 1
        assert receipts[0]["native_tool_calls_count"] == 1
        assert receipts[0]["native_tool_call_envelope_refs"] == [
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
        ]

    def test_native_tool_call_envelopes_use_normalized_lifecycle_receipt_aliases(self) -> None:
        metadata = {
            "native_tool_call_envelopes": ["bad legacy projection"],
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_call_envelopes": [
                    {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
                    {"schema_version": "native_tool_call_envelope.v1", "tool_name": "execute_command"},
                ],
            },
        }
        raw_calls = [{"function": {"name": "read_file"}}]

        envelopes = native_tool_call_envelopes_from_metadata(metadata)

        assert [envelope["tool_name"] for envelope in envelopes] == ["write_file", "execute_command"]
        assert native_tool_call_facts_from_sources(metadata, raw_calls) == {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["write_file", "execute_command"],
        }

    def test_native_tool_call_envelopes_deduplicate_lifecycle_receipt_refs(self) -> None:
        metadata = {
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_call_envelope_refs": [
                    {
                        "schema_version": "native_tool_call_envelope.v1",
                        "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
                        "tool_name": "write_file",
                    },
                    {
                        "schema_version": "native_tool_call_envelope.v1",
                        "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
                        "tool_name": "write_file",
                    },
                    {
                        "schema_version": "native_tool_call_envelope.v1",
                        "envelope_id": "native_tool_call:openai:1:call-2:abcdef",
                        "tool_name": "execute_command",
                    },
                ],
            },
        }

        envelopes = native_tool_call_envelopes_from_metadata(metadata)

        assert [envelope["tool_name"] for envelope in envelopes] == ["write_file", "execute_command"]
        assert native_tool_call_facts_from_sources(metadata, ()) == {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["write_file", "execute_command"],
        }

    def test_native_tool_call_envelope_refs_fall_back_to_plural_lifecycle_receipts(self) -> None:
        metadata = {
            "native_tool_call_envelopes": ["bad legacy projection"],
            "tool_call_lifecycle_receipts": [
                {"schema_version": "tool_call_lifecycle_receipt.v1"},
                {
                    "schema_version": "tool_call_lifecycle_receipt.v1",
                    "native_tool_call_envelope_refs": [
                        {"schema_version": "native_tool_call_envelope.v1", "tool_name": "repo_tree"},
                    ],
                },
            ],
        }
        raw_calls = [{"function": {"name": "read_file"}}]

        assert native_tool_call_facts_from_sources(metadata, raw_calls) == {
            "native_tool_calls_count": 1,
            "native_tool_call_names": ["repo_tree"],
        }

    def test_native_tool_call_names_fallback_uses_shared_aliases(self) -> None:
        raw_calls = [
            {"functionName": "write_file", "arguments": {"path": "x.py"}},
            {"function_name": "execute_command", "arguments": {"cmd": "pytest"}},
        ]

        assert native_tool_call_facts_from_sources({}, raw_calls) == {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["write_file", "execute_command"],
        }

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

    def test_required_tool_not_called_is_retryable_control_plane_error(self) -> None:
        category = classify_error("required_tool_not_called: required_tools=write_file")
        assert category == "tool_required"
        assert is_retryable_error(category) is True


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
