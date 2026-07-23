"""Closed native-request projections for governed Factory provider routes.

The projection is intentionally provider-native and transport-exact.  It is
built from the frozen semantic request plus the Engine-resolved provider
configuration, so the Factory dispatch sidecar can compare the concrete wire
attempt without trusting provider adapter self-declarations.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

FactoryProviderDispatchMode = Literal["invoke", "stream"]
FactoryProviderNativeProtocol = Literal[
    "anthropic_messages",
    "openai_chat_completions",
    "openai_responses",
]

FACTORY_PROVIDER_NATIVE_REQUEST_SCHEMA = "llm.factory_provider_native_request.v1"

_PROJECTION_PROVIDER_TYPES = frozenset({"anthropic_compat", "openai_compat"})
_TRANSPORT_BY_MODE: dict[FactoryProviderDispatchMode, str] = {
    "invoke": "requests.post",
    "stream": "aiohttp.ClientSession.post",
}
_REQUIRED_SEMANTIC_FIELDS = frozenset(
    {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "response_format",
        "temperature",
        "max_tokens",
        "stream",
    }
)
_ANTHROPIC_OPTION_KEYS = (
    "cache_control",
    "container",
    "inference_geo",
    "metadata",
    "output_config",
    "service_tier",
    "stop_sequences",
    "top_k",
    "top_p",
)
_MIN_ANTHROPIC_REASONING_BUDGET_TOKENS = 1_024
_BODY_KEYS_BY_PROTOCOL: dict[FactoryProviderNativeProtocol, frozenset[str]] = {
    "anthropic_messages": frozenset(
        {
            "model",
            "max_tokens",
            "messages",
            "temperature",
            "system",
            "tools",
            "tool_choice",
            "thinking",
            *_ANTHROPIC_OPTION_KEYS,
            "stream",
        }
    ),
    "openai_chat_completions": frozenset(
        {
            "model",
            "messages",
            "temperature",
            "max_tokens",
            "tools",
            "tool_choice",
            "response_format",
            "stream",
        }
    ),
    "openai_responses": frozenset(
        {
            "model",
            "input",
            "temperature",
            "max_output_tokens",
            "tools",
            "tool_choice",
            "stream",
        }
    ),
}
_REQUIRED_BODY_KEYS_BY_PROTOCOL: dict[FactoryProviderNativeProtocol, frozenset[str]] = {
    "anthropic_messages": frozenset({"model", "max_tokens", "messages", "temperature"}),
    "openai_chat_completions": frozenset({"model", "messages", "temperature", "max_tokens"}),
    "openai_responses": frozenset({"model", "input", "temperature", "max_output_tokens"}),
}


class FactoryProviderNativeRequestProjectionError(ValueError):
    """Stable fail-closed projection error for a known native route."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "factory_provider_native_request_projection_invalid")
        super().__init__(self.code)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_not_json") from exc


def _normalize_provider_type(provider_type: str) -> str:
    return str(provider_type or "").strip().lower()


def supports_factory_provider_native_projection(
    provider_type: str,
    *,
    mode: FactoryProviderDispatchMode,
) -> bool:
    """Return whether this exact provider type/mode has a closed projection."""

    if mode not in _TRANSPORT_BY_MODE:
        raise ValueError("factory provider dispatch mode is invalid")
    return _normalize_provider_type(provider_type) in _PROJECTION_PROVIDER_TYPES


def _join_provider_endpoint(base_url: object, api_path: object, *, default_path: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_base_url_required")
    path = str(api_path or default_path).strip()
    if path.startswith(("http://", "https://")):
        return path
    if not path.startswith("/"):
        path = f"/{path}"
    if base.endswith("/v1") and path.startswith("/v1/"):
        path = path.removeprefix("/v1")
    return f"{base}{path}"


def _semantic_payload(final_payload: Mapping[str, Any], *, mode: FactoryProviderDispatchMode) -> dict[str, Any]:
    if not isinstance(final_payload, Mapping):
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_semantic_payload_required")
    missing = sorted(_REQUIRED_SEMANTIC_FIELDS.difference(final_payload))
    if missing:
        raise FactoryProviderNativeRequestProjectionError(
            f"factory_provider_native_request_semantic_fields_missing:{','.join(missing)}"
        )
    payload = {key: final_payload[key] for key in _REQUIRED_SEMANTIC_FIELDS}
    if type(payload["model"]) is not str or not payload["model"].strip():
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_model_invalid")
    messages = payload["messages"]
    if type(messages) is not list or any(type(item) is not dict for item in messages):
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_messages_invalid")
    for message in messages:
        if type(message.get("role")) is not str or type(message.get("content")) is not str:
            raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_message_fields_invalid")
    if type(payload["tools"]) is not list or any(type(item) is not dict for item in payload["tools"]):
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_tools_invalid")
    if payload["tool_choice"] is not None and type(payload["tool_choice"]) not in {str, dict}:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_tool_choice_invalid")
    if payload["response_format"] is not None and type(payload["response_format"]) is not dict:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_response_format_invalid")
    if type(payload["temperature"]) not in {int, float}:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_temperature_invalid")
    if type(payload["max_tokens"]) is not int or payload["max_tokens"] <= 0:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_max_tokens_invalid")
    expected_stream = mode == "stream"
    if type(payload["stream"]) is not bool or payload["stream"] is not expected_stream:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_stream_mode_drift")
    return payload


def _bind_effective_max_tokens(
    semantic: Mapping[str, Any],
    provider_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the Engine-clamped output budget into the physical projection.

    ``semantic`` records the caller-requested budget.  The Engine then applies
    the resolved model/window ceiling and passes the resulting final invoke
    config to the route binder.  Provider implementations consume that final
    ``max_tokens`` value, so the native sidecar authority must project it too.

    Only a positive, non-expanding reduction is accepted.  This preserves the
    frozen request as the upper authority while making the exact physical
    request auditable; malformed values or an attempted budget expansion remain
    fail-closed.
    """

    effective = provider_config.get("max_tokens")
    if effective is None:
        return dict(semantic)
    if type(effective) is not int or effective <= 0:
        raise FactoryProviderNativeRequestProjectionError(
            "factory_provider_native_request_effective_max_tokens_invalid"
        )
    requested = semantic["max_tokens"]
    if type(requested) is not int or requested <= 0:  # guarded by _semantic_payload
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_max_tokens_invalid")
    if effective > requested:
        raise FactoryProviderNativeRequestProjectionError(
            "factory_provider_native_request_effective_max_tokens_expansion_forbidden"
        )
    normalized = dict(semantic)
    normalized["max_tokens"] = effective
    return normalized


def _merge_same_role_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "")
        if not content.strip():
            continue
        if role == "tool":
            role, content = "user", f"【工具结果】\n{content}"
        elif role not in {"system", "user", "assistant"}:
            role = "user"
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] = f"{merged[-1]['content']}\n\n{content}"
        else:
            merged.append({"role": role, "content": content})
    return merged


def build_openai_native_messages(messages: object) -> list[dict[str, str]]:
    """Project frozen role messages to the OpenAI chat message protocol."""

    if type(messages) is not list:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_messages_invalid")
    normalized: list[dict[str, str]] = []
    seen_non_system = False
    for raw in _merge_same_role_messages(messages):
        role = str(raw["role"])
        content = str(raw["content"])
        if role == "system" and seen_non_system:
            role, content = "user", f"【系统提示】\n{content}"
        if role != "system":
            seen_non_system = True
        if normalized and normalized[-1]["role"] == role:
            normalized[-1]["content"] = f"{normalized[-1]['content']}\n\n{content}"
        else:
            normalized.append({"role": role, "content": content})
    if not normalized:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_messages_empty")
    if not any(message["role"] == "user" for message in normalized):
        normalized.append({"role": "user", "content": "(continue)"})
    return normalized


def build_anthropic_native_messages(
    messages: object,
    *,
    fallback_prompt: str = "",
) -> tuple[list[dict[str, Any]], str | None]:
    """Split system turns from Anthropic-native user/assistant messages."""

    if type(messages) is not list:
        messages = []
    system_parts: list[str] = []
    native_messages: list[dict[str, Any]] = []
    for message in _merge_same_role_messages(messages):
        role = str(message["role"])
        content = str(message["content"])
        if role == "system":
            system_parts.append(content)
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        if native_messages and native_messages[-1]["role"] == role:
            native_messages[-1]["content"] = f"{native_messages[-1]['content']}\n\n{content}"
        else:
            native_messages.append({"role": role, "content": content})
    if not native_messages:
        prompt = str(fallback_prompt or "").strip()
        native_messages = [{"role": "user", "content": prompt or "(continue)"}]
    elif not any(message["role"] == "user" for message in native_messages):
        native_messages.append({"role": "user", "content": "(continue)"})
    system = "\n\n".join(system_parts) if system_parts else None
    return native_messages, system


def convert_tools_to_anthropic(tools: object) -> list[dict[str, Any]]:
    """Convert OpenAI-style function tools to Anthropic-native tool schemas."""

    if type(tools) is not list:
        return []
    converted: list[dict[str, Any]] = []
    for item in tools:
        if type(item) is not dict:
            continue
        if isinstance(item.get("name"), str) and isinstance(item.get("input_schema"), dict):
            converted.append(dict(item))
            continue
        if str(item.get("type") or "").strip().lower() == "function":
            function = item.get("function")
            if type(function) is not dict:
                continue
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            parameters = function.get("parameters")
            tool: dict[str, Any] = {
                "name": name,
                "description": str(function.get("description") or ""),
                "input_schema": parameters if type(parameters) is dict else {"type": "object", "properties": {}},
            }
            for key in ("cache_control", "defer_loading", "strict"):
                if function.get(key) is not None:
                    tool[key] = function[key]
                elif item.get(key) is not None:
                    tool[key] = item[key]
            converted.append(tool)
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        parameters = item.get("parameters")
        tool = {
            "name": name,
            "description": str(item.get("description") or ""),
            "input_schema": parameters if type(parameters) is dict else {"type": "object", "properties": {}},
        }
        for key in ("cache_control", "defer_loading", "strict"):
            if item.get(key) is not None:
                tool[key] = item[key]
        converted.append(tool)
    return converted


def convert_tool_choice_to_anthropic(
    tool_choice: object,
    *,
    disable_parallel_tool_use: bool | None = None,
) -> dict[str, Any] | None:
    """Convert one OpenAI/portable tool choice to Anthropic-native shape."""

    def with_parallel_flag(value: dict[str, Any]) -> dict[str, Any]:
        if disable_parallel_tool_use is not None and value.get("type") in {"auto", "any", "tool"}:
            value = dict(value)
            value["disable_parallel_tool_use"] = disable_parallel_tool_use
        return value

    if type(tool_choice) is dict:
        if str(tool_choice.get("type") or "").strip().lower() == "function":
            function = tool_choice.get("function")
            if type(function) is dict:
                name = str(function.get("name") or "").strip()
                if name:
                    return with_parallel_flag({"type": "tool", "name": name})
        choice_type = tool_choice.get("type")
        if isinstance(choice_type, str):
            converted = dict(tool_choice)
            if choice_type.strip().lower() == "function":
                converted["type"] = "tool"
            return with_parallel_flag(converted)
        return None
    token = str(tool_choice or "").strip().lower()
    if not token:
        return None
    if token == "none":
        return {"type": "none"}
    if token == "auto":
        return with_parallel_flag({"type": "auto"})
    if token == "required":
        return with_parallel_flag({"type": "any"})
    return with_parallel_flag({"type": "tool", "name": str(tool_choice)})


def _disable_parallel_tool_use(provider_config: Mapping[str, Any]) -> bool | None:
    value = provider_config.get("disable_parallel_tool_use")
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return None


def _anthropic_supports_tool_choice(provider_config: Mapping[str, Any], model: str) -> bool:
    disabled = provider_config.get("disable_tool_choice")
    if isinstance(disabled, bool):
        return not disabled
    if str(disabled or "").strip().lower() in {"1", "true", "yes", "on", "disabled", "disable"}:
        return False
    route = " ".join(
        (
            str(provider_config.get("base_url") or ""),
            str(provider_config.get("api_path") or ""),
            str(provider_config.get("name") or ""),
            str(provider_config.get("provider_id") or ""),
            model,
        )
    ).lower()
    return "deepseek" not in route and "api.kimi.com/coding" not in route and "kimi-for-coding" not in route


def _anthropic_reasoning_budget_tokens(
    provider_config: Mapping[str, Any],
    *,
    max_tokens: int,
) -> int | None:
    value = provider_config.get("reasoning_budget_tokens")
    if value is None:
        return None
    if type(value) is not int or value < _MIN_ANTHROPIC_REASONING_BUDGET_TOKENS:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_reasoning_budget_invalid")
    if value >= max_tokens:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_reasoning_budget_exhausts_output")
    return value


def _anthropic_thinking(
    provider_config: Mapping[str, Any],
    model: str,
    *,
    max_tokens: int,
) -> dict[str, Any] | None:
    route = " ".join(
        (
            str(provider_config.get("base_url") or ""),
            str(provider_config.get("api_path") or ""),
            str(provider_config.get("name") or ""),
            str(provider_config.get("provider_id") or ""),
            model,
        )
    ).lower()
    required = "api.kimi.com/coding" in route or "kimi-for-coding" in route
    value = provider_config.get("thinking")
    if not isinstance(value, Mapping):
        normalized: dict[str, Any] | None = {"type": "enabled"} if required else None
    else:
        normalized = {str(key): item for key, item in value.items() if str(key).strip()}
        if str(normalized.get("type") or "").strip().lower() != "enabled":
            normalized = {"type": "enabled"} if required else None
        elif normalized is not None:
            normalized["type"] = "enabled"
    if normalized is None:
        return None
    reasoning_budget = _anthropic_reasoning_budget_tokens(provider_config, max_tokens=max_tokens)
    if reasoning_budget is not None:
        normalized["budget_tokens"] = reasoning_budget
    return normalized


def _project_openai_body(
    semantic: Mapping[str, Any],
    *,
    protocol: FactoryProviderNativeProtocol,
    stream: bool,
) -> dict[str, Any]:
    messages = build_openai_native_messages(semantic["messages"])
    if protocol == "openai_responses":
        if semantic["response_format"] is not None:
            raise FactoryProviderNativeRequestProjectionError(
                "factory_provider_native_request_response_format_unsupported:openai_responses"
            )
        body: dict[str, Any] = {
            "model": semantic["model"],
            "input": messages,
            "temperature": semantic["temperature"],
            "max_output_tokens": semantic["max_tokens"],
        }
    else:
        body = {
            "model": semantic["model"],
            "messages": messages,
            "temperature": semantic["temperature"],
            "max_tokens": semantic["max_tokens"],
        }
        if semantic["response_format"] is not None:
            body["response_format"] = semantic["response_format"]
    if semantic["tools"]:
        body["tools"] = semantic["tools"]
    if semantic["tool_choice"] is not None:
        body["tool_choice"] = semantic["tool_choice"]
    if stream:
        body["stream"] = True
    return body


def _project_anthropic_body(
    semantic: Mapping[str, Any],
    provider_config: Mapping[str, Any],
    *,
    stream: bool,
) -> dict[str, Any]:
    if semantic["response_format"] is not None:
        raise FactoryProviderNativeRequestProjectionError(
            "factory_provider_native_request_response_format_unsupported:anthropic_messages"
        )
    messages, system = build_anthropic_native_messages(semantic["messages"])
    body: dict[str, Any] = {
        "model": semantic["model"],
        "max_tokens": semantic["max_tokens"],
        "messages": messages,
        "temperature": semantic["temperature"],
    }
    overrides = provider_config.get("request_overrides")
    if isinstance(overrides, Mapping) and overrides:
        raise FactoryProviderNativeRequestProjectionError(
            "factory_provider_native_request_overrides_forbidden:anthropic_messages"
        )
    for key in _ANTHROPIC_OPTION_KEYS:
        if provider_config.get(key) is not None:
            body[key] = provider_config[key]
    thinking = _anthropic_thinking(
        provider_config,
        str(semantic["model"]),
        max_tokens=int(semantic["max_tokens"]),
    )
    if thinking is not None:
        body["thinking"] = thinking
    if system:
        body["system"] = system
    tools = convert_tools_to_anthropic(semantic["tools"])
    if tools:
        body["tools"] = tools
        choice = convert_tool_choice_to_anthropic(
            semantic["tool_choice"],
            disable_parallel_tool_use=_disable_parallel_tool_use(provider_config),
        )
        if choice and _anthropic_supports_tool_choice(provider_config, str(semantic["model"])):
            body["tool_choice"] = choice
    if stream:
        body["stream"] = True
    return body


@dataclass(frozen=True, slots=True)
class FactoryProviderNativeRequestV1:
    """Exact physical request authority consumed by the Factory sidecar."""

    schema_version: str
    provider_type: str
    mode: FactoryProviderDispatchMode
    native_protocol: FactoryProviderNativeProtocol
    exact_endpoint: str
    exact_transport_kind: str
    expected_body_json: str

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_PROVIDER_NATIVE_REQUEST_SCHEMA:
            raise ValueError("factory_provider_native_request_schema_mismatch")
        if self.provider_type not in _PROJECTION_PROVIDER_TYPES:
            raise ValueError("factory_provider_native_request_provider_type_invalid")
        if self.mode not in _TRANSPORT_BY_MODE:
            raise ValueError("factory_provider_native_request_mode_invalid")
        if self.exact_transport_kind != _TRANSPORT_BY_MODE[self.mode]:
            raise ValueError("factory_provider_native_request_transport_kind_mismatch")
        if not self.exact_endpoint.startswith(("http://", "https://")):
            raise ValueError("factory_provider_native_request_endpoint_invalid")
        if self.provider_type == "anthropic_compat" and self.native_protocol != "anthropic_messages":
            raise ValueError("factory_provider_native_request_protocol_provider_mismatch")
        if self.provider_type == "openai_compat" and self.native_protocol == "anthropic_messages":
            raise ValueError("factory_provider_native_request_protocol_provider_mismatch")
        try:
            body = json.loads(self.expected_body_json)
        except (TypeError, ValueError) as exc:
            raise ValueError("factory_provider_native_request_body_json_invalid") from exc
        if type(body) is not dict:
            raise TypeError("factory_provider_native_request_body_object_required")
        if _canonical_json(body) != self.expected_body_json:
            raise ValueError("factory_provider_native_request_body_not_canonical")
        if not set(body).issubset(_BODY_KEYS_BY_PROTOCOL[self.native_protocol]):
            raise ValueError("factory_provider_native_request_body_not_closed_set")
        if not _REQUIRED_BODY_KEYS_BY_PROTOCOL[self.native_protocol].issubset(body):
            raise ValueError("factory_provider_native_request_body_required_keys_missing")
        if self.mode == "stream":
            if body.get("stream") is not True:
                raise ValueError("factory_provider_native_request_body_stream_mismatch")
        elif "stream" in body:
            raise ValueError("factory_provider_native_request_body_stream_mismatch")

    def expected_body(self) -> dict[str, Any]:
        """Return a fresh JSON object so callers cannot mutate authority state."""

        body = json.loads(self.expected_body_json)
        if type(body) is not dict:  # guarded by __post_init__; keeps the return type exact
            raise TypeError("factory_provider_native_request_body_object_required")
        return body

    def authority(self) -> dict[str, Any]:
        """Return the sidecar-ready serializable authority record."""

        return {
            "schema_version": self.schema_version,
            "provider_type": self.provider_type,
            "mode": self.mode,
            "native_protocol": self.native_protocol,
            "exact_endpoint": self.exact_endpoint,
            "exact_transport_kind": self.exact_transport_kind,
            "expected_body": self.expected_body(),
        }


def project_factory_provider_native_request(
    *,
    provider_type: str,
    mode: FactoryProviderDispatchMode,
    final_payload: Mapping[str, Any],
    provider_config: Mapping[str, Any],
) -> FactoryProviderNativeRequestV1 | None:
    """Project one frozen semantic request to its exact native wire request.

    Unknown/opaque provider types return ``None``.  Known native routes raise a
    stable projection error when semantic data cannot be represented without
    loss; they never silently drop a frozen field.
    """

    normalized_provider_type = _normalize_provider_type(provider_type)
    if not supports_factory_provider_native_projection(normalized_provider_type, mode=mode):
        return None
    semantic = _bind_effective_max_tokens(
        _semantic_payload(final_payload, mode=mode),
        provider_config,
    )
    stream = mode == "stream"
    if normalized_provider_type == "anthropic_compat":
        protocol: FactoryProviderNativeProtocol = "anthropic_messages"
        endpoint = _join_provider_endpoint(
            provider_config.get("base_url"),
            provider_config.get("api_path"),
            default_path="/v1/messages",
        )
        body = _project_anthropic_body(semantic, provider_config, stream=stream)
    else:
        raw_path = str(provider_config.get("api_path") or "/v1/chat/completions").strip()
        normalized_path = f"/{raw_path.lstrip('/')}".rstrip("/")
        protocol = "openai_responses" if normalized_path == "/v1/responses" else "openai_chat_completions"
        endpoint = _join_provider_endpoint(
            provider_config.get("base_url"),
            raw_path,
            default_path="/v1/chat/completions",
        )
        body = _project_openai_body(semantic, protocol=protocol, stream=stream)
    return FactoryProviderNativeRequestV1(
        schema_version=FACTORY_PROVIDER_NATIVE_REQUEST_SCHEMA,
        provider_type=normalized_provider_type,
        mode=mode,
        native_protocol=protocol,
        exact_endpoint=endpoint,
        exact_transport_kind=_TRANSPORT_BY_MODE[mode],
        expected_body_json=_canonical_json(body),
    )


__all__ = [
    "FACTORY_PROVIDER_NATIVE_REQUEST_SCHEMA",
    "FactoryProviderDispatchMode",
    "FactoryProviderNativeProtocol",
    "FactoryProviderNativeRequestProjectionError",
    "FactoryProviderNativeRequestV1",
    "build_anthropic_native_messages",
    "build_openai_native_messages",
    "convert_tool_choice_to_anthropic",
    "convert_tools_to_anthropic",
    "project_factory_provider_native_request",
    "supports_factory_provider_native_projection",
]
