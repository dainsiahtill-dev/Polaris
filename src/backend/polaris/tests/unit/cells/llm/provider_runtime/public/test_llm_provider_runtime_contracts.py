"""Tests for polaris.cells.llm.provider_runtime.public.contracts.

Covers dataclass construction, validation, serialization, and error contracts.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from polaris.cells.llm.provider_runtime.public.contracts import (
    ILlmProviderRuntimeService,
    InvokeProviderActionCommandV1,
    InvokeRoleProviderCommandV1,
    LlmProviderRuntimeError,
    ProviderInvocationCompletedEventV1,
    ProviderInvocationResultV1,
    QueryRoleRuntimeProviderSupportV1,
    UnsupportedProviderTypeError,
)


class TestInvokeProviderActionCommandV1:
    def test_minimal_construction(self) -> None:
        cmd = InvokeProviderActionCommandV1(
            action="complete",
            provider_type="openai",
        )
        assert cmd.action == "complete"
        assert cmd.provider_type == "openai"
        assert cmd.provider_cfg == {}
        assert cmd.api_key is None

    def test_with_optional_fields(self) -> None:
        cmd = InvokeProviderActionCommandV1(
            action="chat",
            provider_type="anthropic",
            provider_cfg={"model": "claude-3"},
            api_key="sk-123",
        )
        assert cmd.provider_cfg == {"model": "claude-3"}
        assert cmd.api_key == "sk-123"

    def test_empty_action_raises(self) -> None:
        with pytest.raises(ValueError, match="action must be a non-empty string"):
            InvokeProviderActionCommandV1(action="", provider_type="openai")

    def test_empty_provider_type_raises(self) -> None:
        with pytest.raises(ValueError, match="provider_type must be a non-empty string"):
            InvokeProviderActionCommandV1(action="complete", provider_type="")

    def test_empty_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key must be a non-empty string"):
            InvokeProviderActionCommandV1(
                action="complete",
                provider_type="openai",
                api_key="",
            )

    def test_none_api_key_allowed(self) -> None:
        cmd = InvokeProviderActionCommandV1(
            action="complete",
            provider_type="openai",
            api_key=None,
        )
        assert cmd.api_key is None

    def test_provider_cfg_is_copied(self) -> None:
        original = {"temperature": 0.5}
        cmd = InvokeProviderActionCommandV1(
            action="complete",
            provider_type="openai",
            provider_cfg=original,
        )
        original["temperature"] = 1.0
        assert cmd.provider_cfg == {"temperature": 0.5}

    def test_frozen_dataclass(self) -> None:
        cmd = InvokeProviderActionCommandV1(action="complete", provider_type="openai")
        with pytest.raises(FrozenInstanceError):
            cmd.action = "chat"  # type: ignore[misc]


class TestInvokeRoleProviderCommandV1:
    def test_minimal_construction(self) -> None:
        cmd = InvokeRoleProviderCommandV1(
            workspace="/tmp/ws",
            role="pm",
            prompt="hello",
            fallback_model="gpt-4",
        )
        assert cmd.workspace == "/tmp/ws"
        assert cmd.role == "pm"
        assert cmd.prompt == "hello"
        assert cmd.fallback_model == "gpt-4"
        assert cmd.timeout == 30
        assert cmd.blocked_provider_types == ()

    def test_custom_timeout_and_blocked(self) -> None:
        cmd = InvokeRoleProviderCommandV1(
            workspace="ws",
            role="architect",
            prompt="design",
            fallback_model="claude-3",
            timeout=60,
            blocked_provider_types=("ollama", "local"),
        )
        assert cmd.timeout == 60
        assert cmd.blocked_provider_types == ("ollama", "local")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            InvokeRoleProviderCommandV1(
                workspace="",
                role="pm",
                prompt="hello",
                fallback_model="gpt-4",
            )

    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role must be a non-empty string"):
            InvokeRoleProviderCommandV1(
                workspace="ws",
                role="",
                prompt="hello",
                fallback_model="gpt-4",
            )

    def test_empty_prompt_raises(self) -> None:
        with pytest.raises(ValueError, match="prompt must be a non-empty string"):
            InvokeRoleProviderCommandV1(
                workspace="ws",
                role="pm",
                prompt="",
                fallback_model="gpt-4",
            )

    def test_empty_fallback_model_raises(self) -> None:
        with pytest.raises(ValueError, match="fallback_model must be a non-empty string"):
            InvokeRoleProviderCommandV1(
                workspace="ws",
                role="pm",
                prompt="hello",
                fallback_model="",
            )

    def test_timeout_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout must be >= 1"):
            InvokeRoleProviderCommandV1(
                workspace="ws",
                role="pm",
                prompt="hello",
                fallback_model="gpt-4",
                timeout=0,
            )

    def test_timeout_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout must be >= 1"):
            InvokeRoleProviderCommandV1(
                workspace="ws",
                role="pm",
                prompt="hello",
                fallback_model="gpt-4",
                timeout=-1,
            )

    def test_blocked_provider_types_filtered(self) -> None:
        cmd = InvokeRoleProviderCommandV1(
            workspace="ws",
            role="pm",
            prompt="hello",
            fallback_model="gpt-4",
            blocked_provider_types=("", "  ", "openai", ""),
        )
        assert cmd.blocked_provider_types == ("openai",)

    def test_blocked_provider_types_stripped(self) -> None:
        cmd = InvokeRoleProviderCommandV1(
            workspace="ws",
            role="pm",
            prompt="hello",
            fallback_model="gpt-4",
            blocked_provider_types=(" openai ", "anthropic"),
        )
        assert cmd.blocked_provider_types == ("openai", "anthropic")


class TestQueryRoleRuntimeProviderSupportV1:
    def test_construction(self) -> None:
        q = QueryRoleRuntimeProviderSupportV1(workspace="/tmp/ws", role="pm")
        assert q.workspace == "/tmp/ws"
        assert q.role == "pm"

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            QueryRoleRuntimeProviderSupportV1(workspace="", role="pm")

    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role must be a non-empty string"):
            QueryRoleRuntimeProviderSupportV1(workspace="ws", role="")


class TestProviderInvocationCompletedEventV1:
    def test_minimal_construction(self) -> None:
        ev = ProviderInvocationCompletedEventV1(
            event_id="e1",
            workspace="ws",
            role="pm",
            provider_kind="openai",
            status="ok",
            completed_at="2024-01-01T00:00:00Z",
        )
        assert ev.event_id == "e1"
        assert ev.workspace == "ws"
        assert ev.role == "pm"
        assert ev.provider_kind == "openai"
        assert ev.status == "ok"
        assert ev.completed_at == "2024-01-01T00:00:00Z"
        assert ev.request_id is None

    def test_with_request_id(self) -> None:
        ev = ProviderInvocationCompletedEventV1(
            event_id="e1",
            workspace="ws",
            role="pm",
            provider_kind="openai",
            status="ok",
            completed_at="2024-01-01T00:00:00Z",
            request_id="req-1",
        )
        assert ev.request_id == "req-1"

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            ProviderInvocationCompletedEventV1(
                event_id="",
                workspace="ws",
                role="pm",
                provider_kind="openai",
                status="ok",
                completed_at="2024-01-01T00:00:00Z",
            )

    def test_empty_request_id_raises(self) -> None:
        with pytest.raises(ValueError, match="request_id must be a non-empty string"):
            ProviderInvocationCompletedEventV1(
                event_id="e1",
                workspace="ws",
                role="pm",
                provider_kind="openai",
                status="ok",
                completed_at="2024-01-01T00:00:00Z",
                request_id="",
            )

    def test_none_request_id_allowed(self) -> None:
        ev = ProviderInvocationCompletedEventV1(
            event_id="e1",
            workspace="ws",
            role="pm",
            provider_kind="openai",
            status="ok",
            completed_at="2024-01-01T00:00:00Z",
            request_id=None,
        )
        assert ev.request_id is None


class TestProviderInvocationResultV1:
    def test_success_result(self) -> None:
        result = ProviderInvocationResultV1(
            ok=True,
            status="ok",
            provider_kind="openai",
        )
        assert result.ok is True
        assert result.status == "ok"
        assert result.provider_kind == "openai"
        assert result.payload == {}
        assert result.error_code is None
        assert result.error_message is None

    def test_failed_result_with_error(self) -> None:
        result = ProviderInvocationResultV1(
            ok=False,
            status="failed",
            provider_kind="openai",
            error_code="E001",
            error_message="rate limited",
        )
        assert result.ok is False
        assert result.error_code == "E001"
        assert result.error_message == "rate limited"

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            ProviderInvocationResultV1(
                ok=False,
                status="failed",
                provider_kind="openai",
            )

    def test_payload_is_copied(self) -> None:
        original = {"tokens": 100}
        result = ProviderInvocationResultV1(
            ok=True,
            status="ok",
            provider_kind="openai",
            payload=original,
        )
        original["tokens"] = 200
        assert result.payload == {"tokens": 100}

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status must be a non-empty string"):
            ProviderInvocationResultV1(
                ok=True,
                status="",
                provider_kind="openai",
            )

    def test_empty_provider_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="provider_kind must be a non-empty string"):
            ProviderInvocationResultV1(
                ok=True,
                status="ok",
                provider_kind="",
            )


class TestLlmProviderRuntimeError:
    def test_default_code(self) -> None:
        err = LlmProviderRuntimeError("something bad")
        assert str(err) == "something bad"
        assert err.code == "llm_provider_runtime_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = LlmProviderRuntimeError(
            "bad request",
            code="provider_timeout",
            details={"provider": "openai"},
        )
        assert err.code == "provider_timeout"
        assert err.details == {"provider": "openai"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            LlmProviderRuntimeError("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            LlmProviderRuntimeError("msg", code="")

    def test_details_are_copied(self) -> None:
        original = {"a": 1}
        err = LlmProviderRuntimeError("msg", details=original)
        original["a"] = 2
        assert err.details == {"a": 1}

    def test_is_runtime_error(self) -> None:
        assert issubclass(LlmProviderRuntimeError, RuntimeError)


class TestUnsupportedProviderTypeError:
    def test_construction(self) -> None:
        err = UnsupportedProviderTypeError("unknown_provider")
        assert "unknown_provider" in str(err)
        assert err.code == "unsupported_provider_type"
        assert err.details == {"provider_type": "unknown_provider"}

    def test_is_llm_provider_runtime_error(self) -> None:
        assert issubclass(UnsupportedProviderTypeError, LlmProviderRuntimeError)

    def test_is_runtime_error(self) -> None:
        assert issubclass(UnsupportedProviderTypeError, RuntimeError)


class TestILlmProviderRuntimeService:
    def test_is_protocol(self) -> None:
        assert hasattr(ILlmProviderRuntimeService, "invoke_provider_action")
        assert hasattr(ILlmProviderRuntimeService, "invoke_role_provider")

    def test_runtime_checkable(self) -> None:
        class FakeService:
            async def invoke_provider_action(self, command): ...
            async def invoke_role_provider(self, command): ...

        assert isinstance(FakeService(), ILlmProviderRuntimeService)

    def test_missing_method_fails_check(self) -> None:
        class BadService:
            async def invoke_provider_action(self, command): ...

        assert not isinstance(BadService(), ILlmProviderRuntimeService)
