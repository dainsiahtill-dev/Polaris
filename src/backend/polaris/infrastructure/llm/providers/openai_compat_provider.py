from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from polaris.kernelone.context.contracts import (
    ContextBuilderPort,
    TurnEngineContextRequest as ContextRequest,
)
from polaris.kernelone.llm.model_resolver import resolve_model_name, validate_model_name
from polaris.kernelone.llm.providers import (
    THINKING_PREFIX,
    BaseProvider,
    ProviderInfo,
    ValidationResult,
)
from polaris.kernelone.llm.providers.stream_thinking_parser import StreamThinkingParser
from polaris.kernelone.llm.response_parser import LLMResponseParser
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult, Usage, estimate_usage
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds, timeout_seconds_or_none

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile

from .http_utils import join_url, merge_headers, normalize_base_url, validate_base_url_for_ssrf
from .provider_helpers import (
    get_stream_session,
    health_check_post,
    invoke_with_retry,
    iter_sse_data_payloads,
    list_models_from_api,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# Regex to strip structured tags like <think>, <thinking>, <answer>, etc.
_STRUCTURAL_TAGS_RE = re.compile(
    r"<(think|thinking|thought|answer)(\s[^>]*)?>.*?</\1>|<(think|thinking|thought|answer)(\s[^>]*)?>|</(think|thinking|thought|answer)>",
    re.IGNORECASE | re.DOTALL,
)

logger = logging.getLogger(__name__)

DEFAULT_MODELS_PATH = "/v1/models"
DEFAULT_CHAT_PATH = "/v1/chat/completions"


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


def _timeout_seconds(config: dict[str, Any], default: int) -> int:
    return normalize_timeout_seconds(config.get("timeout"), default=default)


def _resolve_max_tokens(config: dict[str, Any]) -> int | None:
    value = config.get("max_tokens")
    if value is None:
        value = config.get("max_output_tokens")
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
        out_dict: list[str] = []  # renamed to avoid redef
        for key in ("text", "content", "value"):
            text_value = value.get(key)
            if isinstance(text_value, str) and text_value:
                out_dict.append(text_value)
            elif isinstance(text_value, (list, dict)):
                out_dict.extend(_flatten_text(text_value))
        for key in ("reasoning_content", "reasoning", "thinking"):
            nested = value.get(key)
            if nested is not None:
                out_dict.extend(_flatten_text(nested))
        return out_dict
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


def _extract_tokens_from_openai_stream_event(
    raw_event: dict[str, Any],
    *,
    think_parser: StreamThinkingParser,
    has_seen_native_reasoning: bool,
) -> tuple[list[str], bool]:
    out: list[str] = []
    if not isinstance(raw_event, dict):
        return out, has_seen_native_reasoning

    choices = raw_event.get("choices", [])
    if not choices or not isinstance(choices, list):
        return out, has_seen_native_reasoning
    delta = choices[0].get("delta", {}) if isinstance(choices[0], dict) else {}
    if not isinstance(delta, dict):
        return out, has_seen_native_reasoning

    for key in ("reasoning_content", "reasoning", "thinking"):
        for text in _flatten_text(delta.get(key)):
            if text and text.strip():
                out.append(f"{THINKING_PREFIX}{text}")
                has_seen_native_reasoning = True

    for part_kind, text in _extract_delta_content_parts(delta.get("content")):
        if not text:
            continue
        if part_kind == "reasoning":
            if not has_seen_native_reasoning:
                out.append(f"{THINKING_PREFIX}{text}")
            continue

        if has_seen_native_reasoning:
            for parsed_kind, parsed_text in think_parser.feed_sync(text):
                if not parsed_text:
                    continue
                if parsed_kind in ("content", "answer"):
                    out.append(parsed_text)
            continue

        for think_kind, parsed in think_parser.feed_sync(text):
            if not parsed:
                continue
            if think_kind == "thinking":
                out.append(f"{THINKING_PREFIX}{parsed}")
            else:
                out.append(parsed)

    return out, has_seen_native_reasoning


def _inject_api_key(config: dict[str, Any], api_key: str | None) -> dict[str, Any]:
    if not api_key:
        return config
    merged = dict(config)
    merged["api_key"] = api_key
    return merged


def _headers(config: dict[str, Any], api_key: str | None) -> dict[str, str]:
    headers = merge_headers({"Content-Type": "application/json"}, config.get("headers"))
    header_name = str(config.get("api_key_header") or "").strip()
    if api_key:
        if header_name:
            headers[header_name] = str(api_key)
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    return headers


class OpenAICompatProvider(BaseProvider):
    """OpenAI-compatible API provider"""

    def __init__(
        self,
        profile: RoleProfile | None = None,
        workspace: Path | str | None = None,
    ) -> None:
        """Initialize the provider with optional profile and workspace.

        Args:
            profile: Optional role profile for context gateway.
            workspace: Optional workspace path for context gateway.
        """
        self._profile = profile
        self._workspace = Path(workspace) if workspace else Path.cwd()
        self._context_builder: ContextBuilderPort | None = None
        if profile is not None:
            # TODO(ADR): Inject ContextBuilderPort via DI instead of concrete class.
            from polaris.cells.roles.kernel.public.service import RoleContextGateway

            self._context_builder = RoleContextGateway(
                profile=profile,
                workspace=self._workspace,
            )

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="OpenAI Compatible Provider",
            type="openai_compat",
            description="OpenAI-compatible REST API provider",
            version="1.0.0",
            author="Polaris Team",
            documentation_url="https://platform.openai.com/docs/api-reference",
            supported_features=[
                "health_check",
                "model_listing",
                "chat_completions",
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
            "base_url": "https://api.example.com/v1",
            "api_path": DEFAULT_CHAT_PATH,
            "timeout": 60,
            "retries": 0,
            "temperature": 0.2,
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
            is_safe, reason = validate_base_url_for_ssrf(base_url)
            if not is_safe:
                errors.append(f"SSRF check failed: {reason}")

        api_path = str(config.get("api_path") or "").strip()
        if not api_path:
            errors.append("api_path is required")
        else:
            normalized["api_path"] = api_path
            if not base_url and not api_path.startswith(("http://", "https://")):
                warnings.append("base_url is empty; api_path should be absolute")

        models_path = str(config.get("models_path") or DEFAULT_MODELS_PATH).strip()
        if not models_path:
            warnings.append("models_path is empty; model listing may fail")
        else:
            normalized["models_path"] = models_path

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
        api_path = str(config.get("api_path") or DEFAULT_CHAT_PATH).strip()
        url = join_url(base, api_path, strip_prefixes=["/v1"])
        timeout = _timeout_seconds(config, 30)
        api_key = config.get("api_key")
        test_payload = {
            "model": config.get("model") or "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 50,
            "stream": False,
        }
        return health_check_post(url, _headers(config, api_key), test_payload, timeout)

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        base = normalize_base_url(str(config.get("base_url") or ""))
        models_path = str(config.get("models_path") or DEFAULT_MODELS_PATH).strip()
        if "/v1/" not in base and models_path.startswith("/models"):
            models_path = DEFAULT_MODELS_PATH
        url = join_url(base, models_path, strip_prefixes=["/v1"])
        timeout = _timeout_seconds(config, 10)
        api_key = config.get("api_key")
        return list_models_from_api(url, _headers(config, api_key), timeout)

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        base = normalize_base_url(str(config.get("base_url") or ""))
        timeout = _timeout_seconds(config, 60)
        retries = int(config.get("retries") or 0)
        api_path = str(config.get("api_path") or DEFAULT_CHAT_PATH).strip()
        url = join_url(base, api_path, strip_prefixes=["/v1"])

        resolved = resolve_model_name(
            model=model,
            default_model=config.get("default_model"),
            provider_type="openai_compat",
            role_model=config.get("role_model"),
        )

        if not resolved.is_valid:
            return InvokeResult(
                ok=False,
                output="",
                latency_ms=0,
                usage=estimate_usage(prompt, ""),
                error=f"Invalid model resolution: {resolved.warning}",
            )

        validation = validate_model_name(resolved.model, "openai_compat")
        if not validation.is_valid:
            return InvokeResult(
                ok=False,
                output="",
                latency_ms=0,
                usage=estimate_usage(prompt, ""),
                error=f"Invalid model name: {validation.error}",
            )

        # NOTE: For sync invoke(), we use direct message construction as RoleContextGateway
        # requires async context. The async invoke_stream() and invoke_stream_events()
        # methods properly use RoleContextGateway for context building.
        payload: dict[str, Any] = {
            "model": resolved.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(config.get("temperature") or 0.2),
        }
        tools = config.get("tools")
        if isinstance(tools, list) and tools:
            payload["tools"] = tools
            tool_choice = config.get("tool_choice")
            if tool_choice not in (None, ""):
                payload["tool_choice"] = tool_choice
            parallel_tool_calls = config.get("parallel_tool_calls")
            if isinstance(parallel_tool_calls, bool):
                payload["parallel_tool_calls"] = parallel_tool_calls
        response_format = config.get("response_format")
        if isinstance(response_format, dict) and response_format:
            payload["response_format"] = response_format
        max_tokens = _resolve_max_tokens(config)
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
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
        True streaming invoke for OpenAI-compatible API.

        Sends request with stream=True and yields tokens as they arrive.
        """
        think_parser = StreamThinkingParser()
        has_seen_native_reasoning = False

        try:
            async for raw_event in self.invoke_stream_events(prompt, model, config):
                tokens, has_seen_native_reasoning = _extract_tokens_from_openai_stream_event(
                    raw_event,
                    think_parser=think_parser,
                    has_seen_native_reasoning=has_seen_native_reasoning,
                )
                for token in tokens:
                    yield token

            for kind, text in think_parser.flush():  # type: ignore[attr-defined]
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
        timeout = _timeout_seconds(config, 60)
        api_path = str(config.get("api_path") or DEFAULT_CHAT_PATH).strip()
        url = join_url(base, api_path, strip_prefixes=["/v1"])

        resolved = resolve_model_name(
            model=model,
            default_model=config.get("default_model"),
            provider_type="openai_compat",
            role_model=config.get("role_model"),
        )

        if not resolved.is_valid:
            raise RuntimeError(f"Invalid model resolution: {resolved.warning}")

        validation = validate_model_name(resolved.model, "openai_compat")
        if not validation.is_valid:
            raise RuntimeError(f"Invalid model name: {validation.error}")

        # Use RoleContextGateway to build context properly
        system_prompt = config.get("system_prompt")
        if self._context_builder is not None:
            request = ContextRequest(
                message=prompt,
                history=(),
                context_override={"system_hint": system_prompt} if system_prompt else None,
            )
            context_result = await self._context_builder.build_context(request)
            messages = list(context_result.messages)
        else:
            messages = [{"role": "user", "content": prompt}]
            if system_prompt:
                messages.insert(0, {"role": "system", "content": system_prompt})

        payload: dict[str, Any] = {
            "model": resolved.model,
            "messages": messages,
            "temperature": float(config.get("temperature") or 0.2),
            "stream": True,
        }
        tools = config.get("tools")
        if isinstance(tools, list) and tools:
            payload["tools"] = tools
            tool_choice = config.get("tool_choice")
            if tool_choice not in (None, ""):
                payload["tool_choice"] = tool_choice
            parallel_tool_calls = config.get("parallel_tool_calls")
            if isinstance(parallel_tool_calls, bool):
                payload["parallel_tool_calls"] = parallel_tool_calls

        max_tokens = _resolve_max_tokens(config)
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)

        api_key = config.get("api_key")
        headers = _headers(config, api_key)

        session = await get_stream_session(
            "openai_compat",
            timeout_seconds=timeout,
        )
        async with session.post(
            url,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds_or_none(timeout, default=60)),
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"HTTP {response.status} - {error_text}")

            async for data in iter_sse_data_payloads(response.content):
                if data == "[DONE]":
                    break
                try:
                    payload_obj = json.loads(data)
                except (RuntimeError, ValueError):
                    continue
                if isinstance(payload_obj, dict):
                    yield payload_obj


_provider = OpenAICompatProvider()


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
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
            return Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated=False,
                prompt_chars=len(prompt or ""),
                completion_chars=len(output or ""),
            )
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to estimate usage: {e}")
    return estimate_usage(prompt, output)
