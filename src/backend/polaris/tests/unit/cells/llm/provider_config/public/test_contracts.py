"""Tests for polaris.cells.llm.provider_config.public.contracts."""

from __future__ import annotations

import pytest

from polaris.cells.llm.provider_config.public.contracts import (
    LlmProviderConfigError,
    ProviderConfigResultV1,
    ProviderConfigValidationError,
    ProviderNotFoundError,
    ResolveLlmTestExecutionContextCommandV1,
    ResolveProviderContextCommandV1,
    RoleNotConfiguredError,
    SyncSettingsFromLlmCommandV1,
)


class TestResolveProviderContextCommandV1:
    def test_valid(self) -> None:
        cmd = ResolveProviderContextCommandV1(workspace="ws", provider_id="openai")
        assert cmd.workspace == "ws"
        assert cmd.provider_id == "openai"
        assert cmd.api_key is None

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            ResolveProviderContextCommandV1(workspace="", provider_id="openai")

    def test_empty_provider_id_raises(self) -> None:
        with pytest.raises(ValueError, match="provider_id"):
            ResolveProviderContextCommandV1(workspace="ws", provider_id="")

    def test_api_key_normalized(self) -> None:
        cmd = ResolveProviderContextCommandV1(workspace="ws", provider_id="openai", api_key="sk-123")
        assert cmd.api_key == "sk-123"


class TestResolveLlmTestExecutionContextCommandV1:
    def test_valid(self) -> None:
        cmd = ResolveLlmTestExecutionContextCommandV1(workspace="ws", payload={"key": "value"})
        assert cmd.workspace == "ws"
        assert cmd.payload == {"key": "value"}

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            ResolveLlmTestExecutionContextCommandV1(workspace="", payload={})


class TestSyncSettingsFromLlmCommandV1:
    def test_valid(self) -> None:
        cmd = SyncSettingsFromLlmCommandV1(workspace="ws", llm_config={"model": "gpt-4"})
        assert cmd.workspace == "ws"
        assert cmd.llm_config == {"model": "gpt-4"}

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            SyncSettingsFromLlmCommandV1(workspace="", llm_config={})


class TestProviderConfigResultV1:
    def test_success(self) -> None:
        result = ProviderConfigResultV1(
            ok=True, workspace="ws", provider_id="openai", provider_type="openai"
        )
        assert result.ok is True

    def test_failure_requires_error(self) -> None:
        with pytest.raises(ValueError, match="failed result"):
            ProviderConfigResultV1(
                ok=False, workspace="ws", provider_id="openai", provider_type="openai"
            )

    def test_failure_with_error(self) -> None:
        result = ProviderConfigResultV1(
            ok=False,
            workspace="ws",
            provider_id="openai",
            provider_type="openai",
            error_code="not_found",
            error_message="missing",
        )
        assert result.ok is False
        assert result.error_code == "not_found"


class TestLlmProviderConfigError:
    def test_defaults(self) -> None:
        exc = LlmProviderConfigError("something failed")
        assert str(exc) == "something failed"
        assert exc.code == "llm_provider_config_error"
        assert exc.details == {}

    def test_custom_code(self) -> None:
        exc = LlmProviderConfigError("fail", code="custom_code")
        assert exc.code == "custom_code"

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            LlmProviderConfigError("")


class TestProviderNotFoundError:
    def test_fields(self) -> None:
        exc = ProviderNotFoundError("unknown_provider")
        assert exc.code == "provider_not_found"
        assert "unknown_provider" in str(exc)
        assert exc.details == {"provider_id": "unknown_provider"}


class TestProviderConfigValidationError:
    def test_fields(self) -> None:
        exc = ProviderConfigValidationError("invalid field")
        assert exc.code == "provider_config_validation_error"
        assert "invalid field" in str(exc)


class TestRoleNotConfiguredError:
    def test_fields(self) -> None:
        exc = RoleNotConfiguredError("director")
        assert exc.code == "role_not_configured"
        assert "director" in str(exc)
        assert exc.details == {"role": "director"}
