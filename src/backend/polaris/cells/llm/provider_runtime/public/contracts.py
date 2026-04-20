"""Public contracts for `llm.provider_runtime` cell."""

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
class InvokeProviderActionCommandV1:
    action: str
    provider_type: str
    provider_cfg: Mapping[str, Any] = field(default_factory=dict)
    api_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "provider_type", _require_non_empty("provider_type", self.provider_type))
        object.__setattr__(self, "provider_cfg", _to_dict_copy(self.provider_cfg))
        if self.api_key is not None:
            object.__setattr__(self, "api_key", _require_non_empty("api_key", self.api_key))


@dataclass(frozen=True)
class InvokeRoleProviderCommandV1:
    workspace: str
    role: str
    prompt: str
    fallback_model: str
    timeout: int = 30
    blocked_provider_types: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "prompt", _require_non_empty("prompt", self.prompt))
        object.__setattr__(self, "fallback_model", _require_non_empty("fallback_model", self.fallback_model))
        object.__setattr__(
            self,
            "blocked_provider_types",
            tuple(str(item).strip() for item in self.blocked_provider_types if str(item).strip()),
        )
        if self.timeout < 1:
            raise ValueError("timeout must be >= 1")


@dataclass(frozen=True)
class QueryRoleRuntimeProviderSupportV1:
    workspace: str
    role: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))


@dataclass(frozen=True)
class ProviderInvocationCompletedEventV1:
    event_id: str
    workspace: str
    role: str
    provider_kind: str
    status: str
    completed_at: str
    request_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "provider_kind", _require_non_empty("provider_kind", self.provider_kind))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))
        if self.request_id is not None:
            object.__setattr__(self, "request_id", _require_non_empty("request_id", self.request_id))


@dataclass(frozen=True)
class ProviderInvocationResultV1:
    ok: bool
    status: str
    provider_kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "provider_kind", _require_non_empty("provider_kind", self.provider_kind))
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class LlmProviderRuntimeError(RuntimeError):
    """Structured contract error for `llm.provider_runtime`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "llm_provider_runtime_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


class UnsupportedProviderTypeError(LlmProviderRuntimeError):
    """Raised when a provider type is not recognised by the runtime."""

    def __init__(self, provider_type: str) -> None:
        super().__init__(
            f"unsupported provider type: {provider_type!r}",
            code="unsupported_provider_type",
            details={"provider_type": provider_type},
        )


@runtime_checkable
class ILlmProviderRuntimeService(Protocol):
    async def invoke_provider_action(
        self,
        command: InvokeProviderActionCommandV1,
    ) -> ProviderInvocationResultV1:
        """Run provider action."""

    async def invoke_role_provider(
        self,
        command: InvokeRoleProviderCommandV1,
    ) -> ProviderInvocationResultV1:
        """Invoke runtime provider for role."""


__all__ = [
    "ILlmProviderRuntimeService",
    "InvokeProviderActionCommandV1",
    "InvokeRoleProviderCommandV1",
    "LlmProviderRuntimeError",
    "ProviderInvocationCompletedEventV1",
    "ProviderInvocationResultV1",
    "QueryRoleRuntimeProviderSupportV1",
    "UnsupportedProviderTypeError",
]
