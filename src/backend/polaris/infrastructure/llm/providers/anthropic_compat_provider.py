from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.provider_contract import AdapterProviderContract
from polaris.kernelone.llm.providers import (
    THINKING_PREFIX,
    BaseProvider,
    ProviderInfo,
    ValidationResult,
)
from polaris.kernelone.llm.providers.stream_thinking_parser import StreamThinkingParser
from polaris.kernelone.llm.response_parser import LLMResponseParser
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult, Usage, estimate_usage
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds

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
            for parsed_kind, parsed_text in think_parser.feed_sync(text):
                if not parsed_text:
                    continue
                if parsed_kind in ("content", "answer"):
                    out.append(parsed_text)
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
    return parsed if parsed > 0 else default


def _convert_tools_to_anthropic(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []

    converted: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue

        # 已经是 Anthropic 格式
        if isinstance(item.get("name"), str) and isinstance(item.get("input_schema"), dict):
            converted.append(item)
            continue

        # OpenAI function calling 格式 -> Anthropic 格式
        if str(item.get("type") or "").strip().lower() == "function":
            function_block = item.get("function")
            if not isinstance(function_block, dict):
                continue
            name = str(function_block.get("name") or "").strip()
            if not name:
                continue
            parameters = function_block.get("parameters")
            input_schema = parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}}
            converted.append(
                {
                    "name": name,
                    "description": str(function_block.get("description") or ""),
                    "input_schema": input_schema,
                }
            )
            continue

        # 宽松兜底：支持 {name, description, parameters}
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        parameters = item.get("parameters")
        input_schema = parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}}
        converted.append(
            {
                "name": name,
                "description": str(item.get("description") or ""),
                "input_schema": input_schema,
            }
        )

    return converted


def _convert_tool_choice_to_anthropic(tool_choice: Any) -> dict[str, Any] | None:
    if isinstance(tool_choice, dict):
        # 兼容 OpenAI 样式: {"type": "function", "function": {"name": "..."}}
        if str(tool_choice.get("type") or "").strip().lower() == "function":
            function_block = tool_choice.get("function")
            if isinstance(function_block, dict):
                name = str(function_block.get("name") or "").strip()
                if name:
                    return {"type": "tool", "name": name}
        if isinstance(tool_choice.get("type"), str):
            return dict(tool_choice)
        return None

    token = str(tool_choice or "").strip().lower()
    if not token or token == "none":
        return None
    if token == "auto":
        return {"type": "auto"}
    if token == "required":
        return {"type": "any"}
    return {"type": "tool", "name": str(tool_choice)}


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
    if api_key:
        header_name = str(config.get("api_key_header") or "x-api-key")
        headers[header_name] = str(api_key)
    return headers


class AnthropicCompatProvider(BaseProvider):
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
    def validate_config(cls, config: dict[str, Any]) -> ValidationResult:
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
        if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2:
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
            if max_tokens <= 0:
                warnings.append("Invalid max_tokens, using default 256")
                normalized["max_tokens"] = 256
            else:
                normalized["max_tokens"] = max_tokens

        headers = config.get("headers")
        if headers is not None and not isinstance(headers, dict):
            warnings.append("Headers should be a dictionary")
            normalized["headers"] = {}

        return ValidationResult(
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

        # FIXED: Use adapter-built messages from config if available.
        # This is critical for proper transcript handling in multi-turn conversations.
        # Adapter.build_request() builds proper messages from ConversationState.transcript.
        adapter_messages = _CONTRACT.extract_messages({"config": config})
        messages = adapter_messages or [{"role": "user", "content": [{"type": "text", "text": prompt}]}]

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": _resolve_max_tokens(config, 256),
            "messages": messages,
            "temperature": float(config.get("temperature") or 0.2),
        }
        anthropic_tools = _convert_tools_to_anthropic(config.get("tools"))
        if anthropic_tools:
            payload["tools"] = anthropic_tools
            tool_choice = _convert_tool_choice_to_anthropic(config.get("tool_choice"))
            if isinstance(tool_choice, dict) and tool_choice:
                payload["tool_choice"] = tool_choice
        # FIXED: Prefer adapter-provided system prompt from config['system']
        system_prompt = config.get("system") or config.get("system_prompt")
        if system_prompt:
            payload["system"] = str(system_prompt)
        overrides = config.get("request_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)
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

        Anthropic API is similar to OpenAI for streaming, using SSE format:
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
        """Yield raw structured SSE events for KernelOne stream decoding."""

        base = normalize_base_url(str(config.get("base_url") or ""))
        timeout_val = _timeout_seconds(config, 60)
        api_path = str(config.get("api_path") or DEFAULT_MESSAGES_PATH).strip()
        url = join_url(base, api_path, strip_prefixes=["/v1"])

        api_key = config.get("api_key")
        if not api_key:
            raise RuntimeError("API key is required for Anthropic provider")

        # FIXED: Use adapter-built messages from config if available.
        # This is critical for proper transcript handling in multi-turn conversations.
        adapter_messages = _CONTRACT.extract_messages({"config": config})
        messages = adapter_messages or [{"role": "user", "content": [{"type": "text", "text": prompt}]}]

        # FIXED: Prefer adapter-provided system prompt from config['system']
        system_prompt = config.get("system") or config.get("system_prompt")
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": _resolve_max_tokens(config, 256),
            "messages": messages,
            "temperature": float(config.get("temperature") or 0.2),
            "stream": True,
        }
        anthropic_tools = _convert_tools_to_anthropic(config.get("tools"))
        if anthropic_tools:
            payload["tools"] = anthropic_tools
            tool_choice = _convert_tool_choice_to_anthropic(config.get("tool_choice"))
            if isinstance(tool_choice, dict) and tool_choice:
                payload["tool_choice"] = tool_choice
        if system_prompt:
            payload["system"] = str(system_prompt)

        overrides = config.get("request_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

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


_provider = AnthropicCompatProvider()


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
        logger.debug("DEBUG: anthropic_compat_provider.py:{592} {exc} (swallowed)")
    return estimate_usage(prompt, output)
