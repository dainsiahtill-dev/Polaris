"""Resolved actor capability read model for LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_SOURCES = (
    "roles.profile",
    "kernelone.llm.model_catalog",
    "roles.kernel.llm_caller.request_options",
)

_PROVIDER_POLICY_KEYS = (
    "allowed_provider_types",
    "allow_provider_types",
    "blocked_provider_types",
    "provider_type_policy",
)


@dataclass(frozen=True)
class ResolvedActorCapabilityProfile:
    """Provider-bound role/model capability snapshot.

    This is a read model, not state. It records the capability facts already
    resolved for a single LLM turn so downstream ContextOS and receipts can use
    one stable contract instead of re-deriving them from scattered inputs.
    """

    role_id: str
    provider_id: str
    provider_type: str
    model: str
    model_window_tokens: int
    model_output_limit_tokens: int
    tokenizer: str
    supports_native_tools: bool
    supports_json_schema: bool
    supports_stream_native_tools: bool
    tool_whitelist: tuple[str, ...]
    tool_count: int
    native_tool_mode: str
    response_format_mode: str
    requested_max_tokens: int
    request_timeout_seconds: float | None
    provider_policy: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    sources: tuple[str, ...] = _SOURCES
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role_id": self.role_id,
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "model": self.model,
            "model_window_tokens": self.model_window_tokens,
            "model_output_limit_tokens": self.model_output_limit_tokens,
            "tokenizer": self.tokenizer,
            "supports_native_tools": self.supports_native_tools,
            "supports_json_schema": self.supports_json_schema,
            "supports_stream_native_tools": self.supports_stream_native_tools,
            "tool_whitelist": self.tool_whitelist,
            "tool_count": self.tool_count,
            "native_tool_mode": self.native_tool_mode,
            "response_format_mode": self.response_format_mode,
            "requested_max_tokens": self.requested_max_tokens,
            "request_timeout_seconds": self.request_timeout_seconds,
            "provider_policy": dict(self.provider_policy),
            "warnings": self.warnings,
            "sources": self.sources,
        }


def resolve_actor_capability_profile(
    *,
    profile: Any,
    model_catalog: Any,
    provider_capabilities: Any,
    request_options: dict[str, Any],
    native_tool_mode: str,
    response_format_mode: str,
) -> ResolvedActorCapabilityProfile:
    provider_id = str(getattr(profile, "provider_id", "") or "").strip()
    model = str(getattr(profile, "model", "") or "").strip()
    warnings: list[str] = []
    spec = None
    try:
        spec = model_catalog.resolve(provider_id, model)
    except (RuntimeError, ValueError, TypeError) as exc:
        warnings.append(f"model_catalog_resolve_failed:{type(exc).__name__}")

    tools = request_options.get("tools")
    tool_whitelist = tuple(
        str(item).strip()
        for item in list(getattr(getattr(profile, "tool_policy", None), "whitelist", ()) or ())
        if str(item).strip()
    )
    provider_policy = {key: request_options[key] for key in _PROVIDER_POLICY_KEYS if key in request_options}

    return ResolvedActorCapabilityProfile(
        role_id=str(getattr(profile, "role_id", "") or "").strip(),
        provider_id=provider_id,
        provider_type=str(getattr(spec, "provider_type", "") or "").strip(),
        model=str(getattr(spec, "model", model) or "").strip(),
        model_window_tokens=_as_int(getattr(spec, "max_context_tokens", 0)),
        model_output_limit_tokens=_as_int(getattr(spec, "max_output_tokens", 0)),
        tokenizer=str(getattr(spec, "tokenizer", "") or "").strip(),
        supports_native_tools=bool(
            getattr(provider_capabilities, "supports_native_tools", False) or getattr(spec, "supports_tools", False)
        ),
        supports_json_schema=bool(
            getattr(provider_capabilities, "supports_json_schema", False)
            or getattr(spec, "supports_json_schema", False)
        ),
        supports_stream_native_tools=bool(
            getattr(provider_capabilities, "supports_stream_native_tools", False)
            or getattr(spec, "supports_tools", False)
        ),
        tool_whitelist=tool_whitelist,
        tool_count=len(tools) if isinstance(tools, list) else 0,
        native_tool_mode=str(native_tool_mode or "").strip(),
        response_format_mode=str(response_format_mode or "").strip(),
        requested_max_tokens=_as_int(request_options.get("max_tokens")),
        request_timeout_seconds=_as_float_or_none(request_options.get("timeout")),
        provider_policy=provider_policy,
        warnings=tuple(warnings),
    )


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
