"""Public contracts for `llm.provider_config` cell."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class ResolveProviderContextCommandV1:
    workspace: str
    provider_id: str
    api_key: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "provider_id", _require_non_empty("provider_id", self.provider_id))
        object.__setattr__(self, "headers", dict(self.headers))
        if self.api_key is not None:
            object.__setattr__(self, "api_key", _require_non_empty("api_key", self.api_key))


@dataclass(frozen=True)
class ResolveLlmTestExecutionContextCommandV1:
    workspace: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))


@dataclass(frozen=True)
class SyncSettingsFromLlmCommandV1:
    workspace: str
    llm_config: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "llm_config", _to_dict_copy(self.llm_config))


@dataclass(frozen=True)
class ProviderConfigResolvedEventV1:
    event_id: str
    workspace: str
    provider_id: str
    provider_type: str
    resolved_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "provider_id", _require_non_empty("provider_id", self.provider_id))
        object.__setattr__(self, "provider_type", _require_non_empty("provider_type", self.provider_type))
        object.__setattr__(self, "resolved_at", _require_non_empty("resolved_at", self.resolved_at))


@dataclass(frozen=True)
class ProviderConfigResultV1:
    ok: bool
    workspace: str
    provider_id: str
    provider_type: str
    provider_cfg: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "provider_id", _require_non_empty("provider_id", self.provider_id))
        object.__setattr__(self, "provider_type", _require_non_empty("provider_type", self.provider_type))
        object.__setattr__(self, "provider_cfg", _to_dict_copy(self.provider_cfg))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class LlmProviderConfigError(RuntimeError):
    """Structured contract error for `llm.provider_config`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "llm_provider_config_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


class ProviderNotFoundError(LlmProviderConfigError):
    """Raised when a requested provider_id does not exist in config."""

    def __init__(self, provider_id: str) -> None:
        super().__init__(
            f"provider not found: {provider_id!r}",
            code="provider_not_found",
            details={"provider_id": provider_id},
        )


class ProviderConfigValidationError(LlmProviderConfigError):
    """Raised when required config fields are missing or invalid."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="provider_config_validation_error", details=details)


class RoleNotConfiguredError(LlmProviderConfigError):
    """Raised when a requested role is not present in the LLM config."""

    def __init__(self, role: str) -> None:
        super().__init__(
            f"role not configured: {role!r}",
            code="role_not_configured",
            details={"role": role},
        )


@runtime_checkable
class IProviderConfigService(Protocol):
    async def resolve_provider_context(
        self,
        command: ResolveProviderContextCommandV1,
    ) -> ProviderConfigResultV1:
        """Resolve provider config context."""

    async def resolve_test_context(
        self,
        command: ResolveLlmTestExecutionContextCommandV1,
    ) -> Mapping[str, Any]:
        """Resolve LLM test execution context."""

    def sync_settings(self, command: SyncSettingsFromLlmCommandV1) -> None:
        """Sync runtime settings from LLM config."""


__all__ = [
    "IProviderConfigService",
    "LlmProviderConfigError",
    "ProviderConfigResolvedEventV1",
    "ProviderConfigResultV1",
    "ProviderConfigValidationError",
    "ProviderNotFoundError",
    "ResolveLlmTestExecutionContextCommandV1",
    "ResolveProviderContextCommandV1",
    "RoleNotConfiguredError",
    "SyncSettingsFromLlmCommandV1",
]
