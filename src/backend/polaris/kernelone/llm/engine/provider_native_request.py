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
from typing import Any, Literal, NoReturn
from urllib.parse import urlsplit

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
    tool_choice = payload["tool_choice"]
    if tool_choice is not None:
        if type(tool_choice) is str:
            valid_tool_choice = bool(tool_choice.strip())
        elif type(tool_choice) is dict:
            choice_type = str(tool_choice.get("type") or "").strip().lower()
            if choice_type == "function":
                function = tool_choice.get("function")
                valid_tool_choice = type(function) is dict and bool(str(function.get("name") or "").strip())
            elif choice_type == "tool":
                valid_tool_choice = bool(str(tool_choice.get("name") or "").strip())
            else:
                valid_tool_choice = choice_type in {"auto", "any", "none"}
        else:
            valid_tool_choice = False
        if not valid_tool_choice:
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
                "input_schema": parameters if type(parameters) is dict else {"type": "object", "properties": {}},
            }
            if "description" in function:
                tool["description"] = function["description"]
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
            "input_schema": parameters if type(parameters) is dict else {"type": "object", "properties": {}},
        }
        if "description" in item:
            tool["description"] = item["description"]
        for key in ("cache_control", "defer_loading", "strict"):
            if item.get(key) is not None:
                tool[key] = item[key]
        converted.append(tool)
    return converted


_ANTHROPIC_NATIVE_TOOL_KEYS = frozenset(
    {
        "name",
        "description",
        "input_schema",
        "cache_control",
        "defer_loading",
        "strict",
    }
)
_OPENAI_TOOL_WRAPPER_KEYS = frozenset(
    {
        "type",
        "function",
        "cache_control",
        "defer_loading",
        "strict",
    }
)
_OPENAI_FUNCTION_TOOL_KEYS = frozenset(
    {
        "name",
        "description",
        "parameters",
        "cache_control",
        "defer_loading",
        "strict",
    }
)
_PORTABLE_FUNCTION_TOOL_KEYS = frozenset(
    {
        "name",
        "description",
        "parameters",
        "cache_control",
        "defer_loading",
        "strict",
    }
)
_TOOL_OPTION_KEYS = ("cache_control", "defer_loading", "strict")
_ANTHROPIC_TOOL_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _is_canonical_anthropic_tool_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= 64
        and all(character in _ANTHROPIC_TOOL_NAME_CHARS for character in value)
    )


def _raise_unrepresentable_anthropic_tools() -> NoReturn:
    raise FactoryProviderNativeRequestProjectionError(
        "factory_provider_native_request_tools_unrepresentable:anthropic_messages"
    )


def _validate_lossless_anthropic_tool_definitions(raw_tools: list[object]) -> None:
    """Reject tool definitions whose semantics conversion would drop or invent."""

    for item in raw_tools:
        if type(item) is not dict:
            _raise_unrepresentable_anthropic_tools()

        item_keys = frozenset(item)
        name = item.get("name")
        description = item.get("description")

        if "input_schema" in item:
            if (
                not item_keys.issubset(_ANTHROPIC_NATIVE_TOOL_KEYS)
                or not _is_canonical_anthropic_tool_name(name)
                or type(item.get("input_schema")) is not dict
                or ("description" in item and not isinstance(description, str))
            ):
                _raise_unrepresentable_anthropic_tools()
            continue

        if str(item.get("type") or "").strip().lower() == "function":
            function = item.get("function")
            if type(function) is not dict:
                _raise_unrepresentable_anthropic_tools()
            function_keys = frozenset(function)
            function_name = function.get("name")
            function_description = function.get("description")
            if (
                not item_keys.issubset(_OPENAI_TOOL_WRAPPER_KEYS)
                or not function_keys.issubset(_OPENAI_FUNCTION_TOOL_KEYS)
                or not _is_canonical_anthropic_tool_name(function_name)
                or type(function.get("parameters")) is not dict
                or ("description" in function and not isinstance(function_description, str))
                or any(key in item and key in function for key in _TOOL_OPTION_KEYS)
            ):
                _raise_unrepresentable_anthropic_tools()
            continue

        if (
            not item_keys.issubset(_PORTABLE_FUNCTION_TOOL_KEYS)
            or not _is_canonical_anthropic_tool_name(name)
            or type(item.get("parameters")) is not dict
            or ("description" in item and not isinstance(description, str))
        ):
            _raise_unrepresentable_anthropic_tools()


def convert_tool_choice_to_anthropic(
    tool_choice: object,
    *,
    disable_parallel_tool_use: bool | None = None,
) -> dict[str, Any] | None:
    """Convert one OpenAI/portable tool choice to Anthropic-native shape."""

    normalized: dict[str, Any]
    choice_parallel: bool | None = None
    choice_parallel_present = False
    if type(tool_choice) is dict:
        choice_type_raw = tool_choice.get("type")
        if not isinstance(choice_type_raw, str):
            return None
        choice_type = choice_type_raw.strip().lower()
        if choice_type == "function":
            if not frozenset(tool_choice).issubset({"type", "function", "disable_parallel_tool_use"}):
                return None
            function = tool_choice.get("function")
            if type(function) is not dict or frozenset(function) != {"name"}:
                return None
            name = function.get("name")
            if not _is_canonical_anthropic_tool_name(name):
                return None
            normalized = {"type": "tool", "name": name}
        elif choice_type in {"auto", "any", "required"}:
            if not frozenset(tool_choice).issubset({"type", "disable_parallel_tool_use"}):
                return None
            normalized = {"type": "any" if choice_type == "required" else choice_type}
        elif choice_type == "none":
            if frozenset(tool_choice) != {"type"}:
                return None
            return {"type": "none"}
        elif choice_type == "tool":
            if not frozenset(tool_choice).issubset({"type", "name", "disable_parallel_tool_use"}):
                return None
            name = tool_choice.get("name")
            if not _is_canonical_anthropic_tool_name(name):
                return None
            normalized = {"type": "tool", "name": name}
        else:
            return None

        if "disable_parallel_tool_use" in tool_choice:
            raw_parallel = tool_choice["disable_parallel_tool_use"]
            if not isinstance(raw_parallel, bool):
                return None
            choice_parallel = raw_parallel
            choice_parallel_present = True
    elif isinstance(tool_choice, str):
        if tool_choice != tool_choice.strip():
            return None
        token = tool_choice.strip().lower()
        if not token:
            return None
        if token == "none":
            return {"type": "none"}
        if token == "auto":
            normalized = {"type": "auto"}
        elif token in {"any", "required"}:
            normalized = {"type": "any"}
        else:
            if not _is_canonical_anthropic_tool_name(tool_choice):
                return None
            normalized = {"type": "tool", "name": tool_choice}
    elif tool_choice is None:
        return None
    else:
        return None

    if (
        choice_parallel_present
        and disable_parallel_tool_use is not None
        and choice_parallel != disable_parallel_tool_use
    ):
        return None
    effective_parallel = choice_parallel if choice_parallel_present else disable_parallel_tool_use
    if effective_parallel is not None:
        normalized["disable_parallel_tool_use"] = effective_parallel
    return normalized


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


def _anthropic_route_identity(provider_config: Mapping[str, Any], model: str) -> str:
    return " ".join(
        (
            str(provider_config.get("base_url") or ""),
            str(provider_config.get("api_path") or ""),
            str(provider_config.get("name") or ""),
            str(provider_config.get("provider_id") or ""),
            model,
        )
    ).lower()


def _anthropic_supports_tool_choice(provider_config: Mapping[str, Any], model: str) -> bool:
    disabled = provider_config.get("disable_tool_choice")
    if disabled is True:
        return False
    if str(disabled or "").strip().lower() in {"1", "true", "yes", "on", "disabled", "disable"}:
        return False
    route = _anthropic_route_identity(provider_config, model)
    return "api.kimi.com/coding" not in route and "kimi-for-coding" not in route


def _is_deepseek_anthropic_route(provider_config: Mapping[str, Any], model: str) -> bool:
    _ = model
    base_url = str(provider_config.get("base_url") or "").strip().rstrip("/")
    raw_api_path = str(provider_config.get("api_path") or "").strip()
    api_path = raw_api_path.lstrip("/")
    parsed_api_path = urlsplit(raw_api_path)
    if parsed_api_path.scheme in {"http", "https"} and parsed_api_path.netloc:
        endpoint = raw_api_path
    else:
        endpoint = f"{base_url}/{api_path}"
    parsed_endpoint = urlsplit(endpoint)
    endpoint_host = str(parsed_endpoint.hostname or "").lower()
    endpoint_path = "/" + str(parsed_endpoint.path or "").strip("/")
    is_official_endpoint = endpoint_host == "api.deepseek.com" and (
        endpoint_path == "/anthropic" or endpoint_path.startswith("/anthropic/")
    )
    provider_identities = (
        str(provider_config.get("name") or ""),
        str(provider_config.get("provider_id") or ""),
    )
    declares_deepseek = any(
        "deepseek" in identity.strip().lower().replace("-", " ").replace("_", " ").split()
        for identity in provider_identities
    )
    return is_official_endpoint or declares_deepseek


def reconcile_anthropic_thinking_for_wire(
    *,
    thinking: Mapping[str, Any] | None,
    requested_thinking: object,
    tool_choice: Mapping[str, Any] | None,
    provider_config: Mapping[str, Any],
    model: str,
) -> dict[str, Any] | None:
    """Make DeepSeek thinking and explicit tool choice wire-compatible.

    DeepSeek's official Anthropic route enables thinking by default, while its
    thinking mode rejects the ``tool_choice`` field.  When Polaris needs an
    explicit choice (notably the singleton structured-result protocol), keep
    that semantic guarantee and explicitly disable thinking.  An explicitly
    enabled thinking request is a real capability conflict and therefore fails
    before transport instead of being silently weakened.
    """

    normalized = dict(thinking) if thinking is not None else None
    if not _is_deepseek_anthropic_route(provider_config, model) or tool_choice is None:
        return normalized

    requested_type = ""
    if isinstance(requested_thinking, Mapping):
        requested_type = str(requested_thinking.get("type") or "").strip().lower()
    if requested_type == "enabled":
        raise FactoryProviderNativeRequestProjectionError(
            "factory_provider_native_request_thinking_tool_choice_conflict:anthropic_messages"
        )
    return {"type": "disabled"}


def normalize_anthropic_tool_surface_for_wire(
    *,
    tools: object,
    tool_choice: object,
    provider_config: Mapping[str, Any],
    model: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Return one lossless Anthropic tool surface or fail before transport."""

    if tools is None:
        raw_tools: list[object] = []
    elif type(tools) is list:
        raw_tools = tools
    else:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_tools_invalid")

    _validate_lossless_anthropic_tool_definitions(raw_tools)
    converted_tools = convert_tools_to_anthropic(raw_tools)
    if len(converted_tools) != len(raw_tools):
        raise FactoryProviderNativeRequestProjectionError(
            "factory_provider_native_request_tools_unrepresentable:anthropic_messages"
        )

    disable_parallel = _disable_parallel_tool_use(provider_config)
    raw_disable_parallel = provider_config.get("disable_parallel_tool_use")
    if raw_disable_parallel is not None and disable_parallel is None:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_tool_choice_invalid")

    converted_choice = convert_tool_choice_to_anthropic(
        tool_choice,
        disable_parallel_tool_use=disable_parallel,
    )
    if tool_choice is not None and converted_choice is None:
        raise FactoryProviderNativeRequestProjectionError("factory_provider_native_request_tool_choice_invalid")
    if converted_tools and tool_choice is None and disable_parallel is True:
        converted_choice = {
            "type": "auto",
            "disable_parallel_tool_use": True,
        }
    if converted_choice is not None and converted_choice.get("disable_parallel_tool_use") is False:
        converted_choice = dict(converted_choice)
        converted_choice.pop("disable_parallel_tool_use", None)

    if not converted_tools:
        if converted_choice in (None, {"type": "none"}):
            return converted_tools, None
        raise FactoryProviderNativeRequestProjectionError(
            "factory_provider_native_request_tool_choice_without_tools:anthropic_messages"
        )

    if _is_deepseek_anthropic_route(provider_config, model):
        if disable_parallel is True or (
            converted_choice is not None and converted_choice.get("disable_parallel_tool_use") is True
        ):
            raise FactoryProviderNativeRequestProjectionError(
                "factory_provider_native_request_parallel_tool_choice_unsupported:anthropic_messages"
            )
        if converted_choice is not None and "disable_parallel_tool_use" in converted_choice:
            converted_choice = dict(converted_choice)
            converted_choice.pop("disable_parallel_tool_use", None)

    if converted_choice is None:
        return converted_tools, None
    if converted_choice.get("type") == "tool":
        forced_name = converted_choice.get("name")
        advertised_names = {tool.get("name") for tool in converted_tools}
        if forced_name not in advertised_names:
            raise FactoryProviderNativeRequestProjectionError(
                "factory_provider_native_request_tool_choice_unknown_tool:anthropic_messages"
            )
    if _anthropic_supports_tool_choice(provider_config, model):
        return converted_tools, converted_choice
    if converted_choice == {"type": "auto"}:
        return converted_tools, None
    raise FactoryProviderNativeRequestProjectionError(
        "factory_provider_native_request_tool_choice_unsupported:anthropic_messages"
    )


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
    if system:
        body["system"] = system
    tools, choice = normalize_anthropic_tool_surface_for_wire(
        tools=semantic["tools"],
        tool_choice=semantic["tool_choice"],
        provider_config=provider_config,
        model=str(semantic["model"]),
    )
    thinking = reconcile_anthropic_thinking_for_wire(
        thinking=_anthropic_thinking(
            provider_config,
            str(semantic["model"]),
            max_tokens=int(semantic["max_tokens"]),
        ),
        requested_thinking=provider_config.get("thinking"),
        tool_choice=choice,
        provider_config=provider_config,
        model=str(semantic["model"]),
    )
    if thinking is not None:
        body["thinking"] = thinking
    if not tools:
        if stream:
            body["stream"] = True
        return body
    body["tools"] = tools
    if choice is not None:
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
    "normalize_anthropic_tool_surface_for_wire",
    "project_factory_provider_native_request",
    "reconcile_anthropic_thinking_for_wire",
    "supports_factory_provider_native_projection",
]
