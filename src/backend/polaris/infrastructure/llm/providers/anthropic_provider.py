from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.engine.provider_native_request import (
    build_anthropic_native_messages,
    convert_tool_choice_to_anthropic,
    convert_tools_to_anthropic,
)
from polaris.kernelone.llm.provider_contract import AdapterProviderContract
from polaris.kernelone.llm.providers import (
    THINKING_PREFIX,
    BaseProvider,
    ProviderConfigValidationResult,
    ProviderInfo,
)
from polaris.kernelone.llm.providers.stream_thinking_parser import StreamThinkingParser
from polaris.kernelone.llm.response_parser import LLMResponseParser
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult, Usage
from polaris.kernelone.shared.text_utils import normalize_timeout_seconds

from .http_utils import join_url, merge_headers, normalize_base_url
from .provider_helpers import (
    health_check_post,
    invoke_stream_with_retry,
    invoke_with_retry,
    list_models_from_api,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

# Contract utility instance for extracting adapter-built messages
_CONTRACT = AdapterProviderContract()

# Regex to strip structured tags like <think>, <thinking>, <answer>, etc.
_STRUCTURAL_TAGS_RE = re.compile(
    r"<(think|thinking|thought|answer)(\s[^>]*)?>.*?</\1>|<(think|thinking|thought|answer)(\s[^>]*)?>|</(think|thinking|thought|answer)>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_structured_tags(text: str) -> str:
    """Remove structured tags from text when native reasoning is available.

    When a model provides native reasoning_content, any structured tags
    (<think>, <thinking>, <answer>, etc.) in the content are duplicates
    and should be stripped completely.
    """
    if not text:
        return ""
    # Remove all structural tags and their content
    cleaned = _STRUCTURAL_TAGS_RE.sub("", text)
    return cleaned.strip()


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_flatten_text(item))
        return out
    if isinstance(value, dict):
        dict_out: list[str] = []
        for key in ("text", "content", "value"):
            text_value = value.get(key)
            if isinstance(text_value, str) and text_value:
                dict_out.append(text_value)
            elif isinstance(text_value, (list, dict)):
                dict_out.extend(_flatten_text(text_value))
        for key in ("reasoning_content", "reasoning", "thinking"):
            nested = value.get(key)
            if nested is not None:
                dict_out.extend(_flatten_text(nested))
        return dict_out
    return [str(value)]


def _extract_delta_content_parts(content: Any) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    if isinstance(content, str):
        if content:
            parts.append(("content", content))
        return parts
    if isinstance(content, list):
        for item in content:
            parts.extend(_extract_delta_content_parts(item))
        return parts
    if isinstance(content, dict):
        item_type = str(content.get("type") or "").strip().lower()
        payloads = _flatten_text(content)
        for text in payloads:
            if not text:
                continue
            if "reason" in item_type or "think" in item_type:
                parts.append(("reasoning", text))
            else:
                parts.append(("content", text))
        return parts
    text = str(content or "")
    if text:
        parts.append(("content", text))
    return parts


def _extract_tokens_from_anthropic_stream_event(
    raw_event: dict[str, Any],
    *,
    think_parser: StreamThinkingParser,
    has_seen_native_reasoning: bool,
) -> tuple[list[str], bool]:
    out: list[str] = []
    if not isinstance(raw_event, dict):
        return out, has_seen_native_reasoning

    event_type = str(raw_event.get("type") or "").strip()
    if event_type == "error":
        error_obj = raw_event.get("error")
        if isinstance(error_obj, dict):
            message = error_obj.get("message") or error_obj.get("type") or "stream_error"
        else:
            message = raw_event.get("message") or "stream_error"
        out.append(f"Error: {message}")
        return out, has_seen_native_reasoning

    delta = raw_event.get("delta", {})
    if not isinstance(delta, dict):
        delta = {}

    thinking = delta.get("thinking") or raw_event.get("thinking")
    if thinking and str(thinking).strip():
        out.append(f"{THINKING_PREFIX}{thinking}")
        has_seen_native_reasoning = True

    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if reasoning and str(reasoning).strip():
        out.append(f"{THINKING_PREFIX}{reasoning}")
        has_seen_native_reasoning = True

    text = delta.get("text", "")
    if not text:
        content_block = raw_event.get("content_block", {})
        if isinstance(content_block, dict):
            text = content_block.get("text", "")

    if text:
        if has_seen_native_reasoning:
            cleaned = _strip_structured_tags(str(text))
            if cleaned:
                out.append(cleaned)
        else:
            for parsed_kind, parsed_text in think_parser.feed_sync(text):
                if not parsed_text:
                    continue
                if parsed_kind == "thinking":
                    out.append(f"{THINKING_PREFIX}{parsed_text}")
                else:
                    out.append(parsed_text)

    return out, has_seen_native_reasoning


DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODELS_PATH = "/v1/models"
DEFAULT_MESSAGES_PATH = "/v1/messages"
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


def _timeout_seconds(config: dict[str, Any], default: int) -> int:
    return normalize_timeout_seconds(config.get("timeout"), default=default)


def _resolve_max_tokens(config: dict[str, Any], default: int) -> int:
    value = config.get("max_tokens")
    if value is None:
        value = config.get("max_output_tokens")
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _copy_present(payload: dict[str, Any], config: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        value = config.get(key)
        if value is not None:
            payload[key] = value


def _requires_enabled_thinking(config: dict[str, Any], model: str) -> bool:
    token = " ".join(
        [
            str(config.get("base_url") or ""),
            str(config.get("api_path") or ""),
            str(config.get("name") or ""),
            str(config.get("provider_id") or ""),
            str(model or ""),
        ]
    ).lower()
    return "api.kimi.com/coding" in token or "kimi-for-coding" in token


_MIN_ANTHROPIC_REASONING_BUDGET_TOKENS = 1_024


def _apply_reasoning_budget_to_thinking(
    thinking: dict[str, Any] | None,
    *,
    config: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any] | None:
    """Bind a semantic reasoning cap only when manual thinking is active."""

    if thinking is None:
        return None
    value = config.get("reasoning_budget_tokens")
    if value is None:
        return thinking
    if type(value) is not int or value < _MIN_ANTHROPIC_REASONING_BUDGET_TOKENS:
        raise ValueError("reasoning_budget_tokens_invalid")
    if value >= max_tokens:
        raise ValueError("reasoning_budget_tokens_must_be_less_than_max_tokens")
    bounded = dict(thinking)
    bounded["budget_tokens"] = value
    return bounded


def _normalize_anthropic_thinking(value: Any, *, require_enabled: bool = False) -> dict[str, Any] | None:
    """Return a provider-safe Anthropic thinking config.

    Anthropic-compatible endpoints diverge on the optional ``thinking`` field.
    Kimi's Anthropic-compatible coding endpoint rejects a missing/disabled
    thinking mode with HTTP 400 and requires ``{"type": "enabled"}``.  Other
    endpoints should not receive disabled, falsey, string, or otherwise unknown
    values.
    """

    if not isinstance(value, Mapping):
        return {"type": "enabled"} if require_enabled else None
    normalized = {str(key): item for key, item in value.items() if str(key).strip()}
    thinking_type = str(normalized.get("type") or "").strip().lower()
    if thinking_type != "enabled":
        return {"type": "enabled"} if require_enabled else None
    normalized["type"] = "enabled"
    return normalized


def _sanitize_anthropic_payload_tool_choice(payload: dict[str, Any], config: dict[str, Any], model: str) -> None:
    if "tool_choice" not in payload:
        return
    if not payload.get("tools"):
        payload.pop("tool_choice", None)
        return
    tool_choice = _convert_tool_choice_to_anthropic(
        payload.get("tool_choice"),
        disable_parallel_tool_use=_coerce_disable_parallel_tool_use(config),
    )
    if not isinstance(tool_choice, dict) or not tool_choice:
        payload.pop("tool_choice", None)
        return
    if not _supports_tool_choice(config, model):
        payload.pop("tool_choice", None)
        return
    payload["tool_choice"] = tool_choice


def _sanitize_anthropic_payload_options(payload: dict[str, Any], config: dict[str, Any], model: str) -> None:
    require_enabled = _requires_enabled_thinking(config, model)
    normalized = _normalize_anthropic_thinking(payload.get("thinking"), require_enabled=require_enabled)
    normalized = _apply_reasoning_budget_to_thinking(
        normalized,
        config=config,
        max_tokens=int(payload.get("max_tokens") or 0),
    )
    if normalized is None:
        payload.pop("thinking", None)
    else:
        payload["thinking"] = normalized
    _sanitize_anthropic_payload_tool_choice(payload, config, model)


def _coerce_disable_parallel_tool_use(config: dict[str, Any]) -> bool | None:
    value = config.get("disable_parallel_tool_use")
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return None


def _convert_tools_to_anthropic(tools: Any) -> list[dict[str, Any]]:
    return convert_tools_to_anthropic(tools)


def _convert_tool_choice_to_anthropic(
    tool_choice: Any,
    *,
    disable_parallel_tool_use: bool | None = None,
) -> dict[str, Any] | None:
    return convert_tool_choice_to_anthropic(
        tool_choice,
        disable_parallel_tool_use=disable_parallel_tool_use,
    )


def _supports_tool_choice(config: dict[str, Any], model: str) -> bool:
    """Return whether this Anthropic-compatible endpoint accepts tool_choice.

    Some Anthropic-compatible endpoints expose native tools but reject the
    `tool_choice` field for reasoning/thinking models. DeepSeek's Anthropic
    endpoint currently returns HTTP 400 ("Thinking mode does not support this
    tool_choice") in that case. Kimi's coding endpoint requires thinking but
    rejects forced tool choice while thinking is enabled. Omitting the field
    preserves tool availability while letting the provider use its default
    native tool selection behavior.
    """

    raw_flag = config.get("disable_tool_choice")
    if isinstance(raw_flag, bool):
        return not raw_flag
    if raw_flag is not None:
        flag = str(raw_flag).strip().lower()
        if flag in {"1", "true", "yes", "on", "disabled", "disable"}:
            return False

    token = " ".join(
        [
            str(config.get("base_url") or ""),
            str(config.get("api_path") or ""),
            str(config.get("name") or ""),
            str(config.get("provider_id") or ""),
            str(model or ""),
        ]
    ).lower()
    if "deepseek" in token:
        return False
    return not ("api.kimi.com/coding" in token or "kimi-for-coding" in token)


def _inject_api_key(config: dict[str, Any], api_key: str | None) -> dict[str, Any]:
    if not api_key:
        return config
    merged = dict(config)
    merged["api_key"] = api_key
    return merged


def _headers(config: dict[str, Any], api_key: str | None) -> dict[str, str]:
    headers = merge_headers({"Content-Type": "application/json"}, config.get("headers"))
    version = config.get("anthropic_version") or headers.get("anthropic-version") or DEFAULT_ANTHROPIC_VERSION
    if version and "anthropic-version" not in headers:
        headers["anthropic-version"] = str(version)
    beta = config.get("anthropic_beta") or config.get("anthropic-beta")
    if beta and "anthropic-beta" not in headers:
        headers["anthropic-beta"] = str(beta)
    if api_key:
        header_name = str(config.get("api_key_header") or "x-api-key")
        headers[header_name] = str(api_key)
    return headers


def _apply_anthropic_options(payload: dict[str, Any], config: dict[str, Any], model: str) -> None:
    _copy_present(payload, config, _ANTHROPIC_OPTION_KEYS)
    thinking = _normalize_anthropic_thinking(
        config.get("thinking"),
        require_enabled=_requires_enabled_thinking(config, model),
    )
    thinking = _apply_reasoning_budget_to_thinking(
        thinking,
        config=config,
        max_tokens=int(payload.get("max_tokens") or 0),
    )
    if thinking is not None:
        payload["thinking"] = thinking
    system_prompt = config.get("system")
    if system_prompt is None:
        system_prompt = config.get("system_prompt")
    if system_prompt:
        payload["system"] = system_prompt


def _apply_anthropic_tools(payload: dict[str, Any], config: dict[str, Any], model: str) -> None:
    anthropic_tools = _convert_tools_to_anthropic(config.get("tools"))
    if not anthropic_tools:
        return
    payload["tools"] = anthropic_tools
    disable_parallel_tool_use = _coerce_disable_parallel_tool_use(config)
    tool_choice = _convert_tool_choice_to_anthropic(
        config.get("tool_choice"),
        disable_parallel_tool_use=disable_parallel_tool_use,
    )
    if not isinstance(tool_choice, dict) or not tool_choice:
        return
    if _supports_tool_choice(config, model):
        payload["tool_choice"] = tool_choice
        return


def _provider_native_messages(
    prompt: str,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Return exact Anthropic messages/system for the provider wire.

    Factory carries its final role-aware transcript as ``chat_messages``.  It
    must not be flattened into one user prompt or sent with an invalid
    ``system`` message role.  Legacy adapter-native ``messages`` remain
    pass-through for ordinary non-Factory calls.
    """

    chat_messages = config.get("chat_messages")
    if isinstance(chat_messages, list) and chat_messages:
        return build_anthropic_native_messages(chat_messages, fallback_prompt=prompt)
    adapter_messages = _CONTRACT.extract_messages({"config": config})
    if adapter_messages:
        return adapter_messages, None
    return [{"role": "user", "content": [{"type": "text", "text": prompt}]}], None


def _temperature(config: dict[str, Any]) -> float:
    value = config.get("temperature")
    return float(value) if value is not None else 0.2


class AnthropicProvider(BaseProvider):
    """Anthropic-compatible API provider"""

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Anthropic Compatible Provider",
            type="anthropic_compat",
            description="Anthropic-compatible REST API provider",
            version="1.0.0",
            author="Polaris Team",
            documentation_url="https://docs.anthropic.com/claude/reference",
            supported_features=[
                "health_check",
                "model_listing",
                "messages_api",
                "custom_headers",
                "anthropic_beta_headers",
                "structured_outputs",
                "native_tools",
                "extended_thinking",
                "prompt_caching",
                "service_tiers",
                "retries",
            ],
            cost_class="METERED",
            provider_category="LLM",
            autonomous_file_access=False,
            requires_file_interfaces=True,
            model_listing_method="API",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {
            "base_url": "",
            "api_path": DEFAULT_MESSAGES_PATH,
            "anthropic_version": DEFAULT_ANTHROPIC_VERSION,
            "timeout": 120,
            "retries": 0,
            "temperature": 0.2,
            "max_tokens": 256,
            "headers": {},
        }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ProviderConfigValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        normalized = dict(config)

        base_url = normalize_base_url(str(config.get("base_url") or ""))
        if base_url:
            normalized["base_url"] = base_url

        api_path = str(config.get("api_path") or "").strip()
        if not api_path:
            errors.append("api_path is required")
        else:
            normalized["api_path"] = api_path
            if not base_url and not api_path.startswith(("http://", "https://")):
                warnings.append("base_url is empty; api_path should be absolute")

        timeout = config.get("timeout", 60)
        if not isinstance(timeout, (int, float)):
            warnings.append("Invalid timeout, using default 60")
            normalized["timeout"] = 60
        else:
            timeout_num = int(timeout)
            if timeout_num < 0:
                warnings.append("Timeout cannot be negative, using default 60")
                normalized["timeout"] = 60
            else:
                normalized["timeout"] = timeout_num

        retries = config.get("retries", 0)
        if not isinstance(retries, int) or retries < 0:
            warnings.append("Invalid retries, using default 0")
            normalized["retries"] = 0

        temperature = config.get("temperature", 0.2)
        if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 1:
            warnings.append("Invalid temperature, using default 0.2")
            normalized["temperature"] = 0.2

        max_tokens_raw = config.get("max_tokens")
        if max_tokens_raw is None:
            max_tokens_raw = config.get("max_output_tokens")
        if max_tokens_raw is None:
            normalized["max_tokens"] = 256
        else:
            try:
                max_tokens = int(max_tokens_raw)
            except (TypeError, ValueError):
                max_tokens = 0
            if max_tokens < 0:
                warnings.append("Invalid max_tokens, using default 256")
                normalized["max_tokens"] = 256
            else:
                normalized["max_tokens"] = max_tokens

        headers = config.get("headers")
        if headers is not None and not isinstance(headers, dict):
            warnings.append("Headers should be a dictionary")
            normalized["headers"] = {}

        return ProviderConfigValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            normalized_config=normalized,
        )

    def health(self, config: dict[str, Any]) -> HealthResult:
        base = normalize_base_url(str(config.get("base_url") or ""))
        api_path = str(config.get("api_path") or DEFAULT_MESSAGES_PATH).strip()
        url = join_url(base, api_path, strip_prefixes=["/v1"])
        timeout = _timeout_seconds(config, 30)
        api_key = config.get("api_key")
        test_payload = {
            "model": config.get("model") or "claude-3-haiku-20240307",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        }
        return health_check_post(url, _headers(config, api_key), test_payload, timeout)

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        base = normalize_base_url(str(config.get("base_url") or ""))
        models_path = str(config.get("models_path") or DEFAULT_MODELS_PATH).strip()
        url = join_url(base, models_path, strip_prefixes=["/v1"])
        timeout = _timeout_seconds(config, 10)
        api_key = config.get("api_key")
        return list_models_from_api(url, _headers(config, api_key), timeout)

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        base = normalize_base_url(str(config.get("base_url") or ""))
        timeout = _timeout_seconds(config, 60)
        retries = int(config.get("retries") or 0)
        api_path = str(config.get("api_path") or DEFAULT_MESSAGES_PATH).strip()
        url = join_url(base, api_path, strip_prefixes=["/v1"])

        messages, message_system = _provider_native_messages(prompt, config)

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": _resolve_max_tokens(config, 256),
            "messages": messages,
            "temperature": _temperature(config),
        }
        _apply_anthropic_options(payload, config, model)
        if message_system is not None:
            payload["system"] = message_system
        _apply_anthropic_tools(payload, config, model)
        overrides = config.get("request_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)
        _sanitize_anthropic_payload_options(payload, config, model)
        api_key = config.get("api_key")
        return invoke_with_retry(
            url,
            _headers(config, api_key),
            payload,
            timeout,
            retries,
            prompt,
            _extract_output,
            _usage_from_response,
        )

    async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        True streaming invoke for Anthropic-compatible API using aiohttp.

        Anthropic API is similar to OpenAI for streaming, using provider data-line chunks:
        data: {"type":"content_block_delta","delta":{"text":"hello"}}

        Args:
            prompt: The prompt to send
            model: The model name (e.g., "claude-3-haiku-20240307")
            config: Provider configuration

        Yields:
            Text tokens/chunks from the LLM response
        """
        think_parser = StreamThinkingParser()
        has_seen_native_reasoning = False

        try:
            async for raw_event in self.invoke_stream_events(prompt, model, config):
                tokens, has_seen_native_reasoning = _extract_tokens_from_anthropic_stream_event(
                    raw_event,
                    think_parser=think_parser,
                    has_seen_native_reasoning=has_seen_native_reasoning,
                )
                for token in tokens:
                    yield token
                    if token.startswith("Error:"):
                        return

            for kind, text in think_parser.flush():
                if not text:
                    continue
                if kind == "thinking":
                    if not has_seen_native_reasoning:
                        yield f"{THINKING_PREFIX}{text}"
                elif kind == "answer":
                    yield text
                else:
                    yield text
        except (RuntimeError, ValueError) as exc:
            yield f"Error: {exc!s}"

    async def invoke_stream_events(
        self, prompt: str, model: str, config: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield raw structured provider stream events for KernelOne stream decoding."""

        base = normalize_base_url(str(config.get("base_url") or ""))
        timeout_val = _timeout_seconds(config, 60)
        api_path = str(config.get("api_path") or DEFAULT_MESSAGES_PATH).strip()
        url = join_url(base, api_path, strip_prefixes=["/v1"])

        api_key = config.get("api_key")
        if not api_key:
            raise RuntimeError("API key is required for Anthropic provider")

        messages, message_system = _provider_native_messages(prompt, config)

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": _resolve_max_tokens(config, 256),
            "messages": messages,
            "temperature": _temperature(config),
            "stream": True,
        }
        _apply_anthropic_options(payload, config, model)
        if message_system is not None:
            payload["system"] = message_system
        _apply_anthropic_tools(payload, config, model)

        overrides = config.get("request_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)
        _sanitize_anthropic_payload_options(payload, config, model)

        headers = _headers(config, api_key)
        headers["Accept"] = "text/event-stream"

        # Use invoke_stream_with_retry for automatic network jitter handling
        async for payload_obj in invoke_stream_with_retry(
            url,
            headers,
            payload,
            timeout_seconds=timeout_val,
        ):
            yield payload_obj


_provider = AnthropicProvider()


def health(config: dict[str, Any], api_key: str | None) -> HealthResult:
    return _provider.health(_inject_api_key(config, api_key))


def list_models(config: dict[str, Any], api_key: str | None) -> ModelListResult:
    return _provider.list_models(_inject_api_key(config, api_key))


def invoke(prompt: str, model: str, config: dict[str, Any], api_key: str | None) -> InvokeResult:
    return _provider.invoke(prompt, model, _inject_api_key(config, api_key))


def _extract_output(data: dict[str, Any]) -> str:
    return LLMResponseParser.extract_text(data)


def _usage_from_response(prompt: str, output: str, data: dict[str, Any]) -> Usage:
    try:
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
            return Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated=False,
                prompt_chars=len(prompt or ""),
                completion_chars=len(output or ""),
            )
    except (RuntimeError, ValueError):
        logger.debug("DEBUG: anthropic_provider.py:{592} {exc} (swallowed)")
    return Usage.estimate(prompt, output)
