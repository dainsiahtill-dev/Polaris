from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
import requests
from polaris.infrastructure.llm.providers.http_utils import (
    join_url,
    normalize_base_url,
    validate_base_url_for_ssrf,
)
from polaris.infrastructure.llm.providers.provider_helpers import (
    get_stream_session,
    health_check_post,
    invoke_with_retry,
)
from polaris.kernelone.constants import DEFAULT_MAX_RETRIES
from polaris.kernelone.context.contracts import (
    ContextBuilderPort,
    TurnEngineContextRequest as ContextRequest,
)
from polaris.kernelone.llm.providers import (
    THINKING_PREFIX,
    BaseProvider,
    ProviderInfo,
    ValidationResult,
)
from polaris.kernelone.llm.providers.stream_thinking_parser import StreamThinkingParser
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelInfo, ModelListResult, Usage, estimate_usage
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds, timeout_seconds_or_none

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile

DEFAULT_MODELS_PATH = "/v1/models"
DEFAULT_CHAT_PATH = "/v1/chat/completions"


def _timeout_seconds(config: dict[str, Any], default: int, key: str = "timeout") -> int:
    return normalize_timeout_seconds(config.get(key), default=default)


def _resolve_max_tokens(config: dict[str, Any], default: int) -> int:
    value = config.get("max_tokens")
    if value is None:
        value = config.get("max_output_tokens")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


import re

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# Regex to strip structured tags like <think>, <thinking>, <answer>, etc.
_STRUCTURAL_TAGS_RE = re.compile(
    r"<(think|thinking|thought|answer)(\s[^>]*)?>.*?</\1>|</?(think|thinking|thought|answer)(\s[^>]*)?>",
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
        result: list[str] = []
        for key in ("text", "content", "value"):
            text_value = value.get(key)
            if isinstance(text_value, str) and text_value:
                result.append(text_value)
            elif isinstance(text_value, (list, dict)):
                result.extend(_flatten_text(text_value))
        for key in ("reasoning_content", "reasoning", "thinking"):
            nested = value.get(key)
            if nested is not None:
                result.extend(_flatten_text(nested))
        return result
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


class KimiProvider(BaseProvider):
    """Moonshot AI (Kimi) API provider - OpenAI compatible"""

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
            # Lazy import: only the factory site knows about Cell internals.
            from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway

            self._context_builder = RoleContextGateway(
                profile=profile,
                workspace=self._workspace,
            )

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Kimi Provider",
            type="kimi",
            description="Moonshot AI Kimi API provider with OpenAI SDK compatibility",
            version="1.0.0",
            author="Polaris Team",
            documentation_url="https://platform.moonshot.ai/docs/api/chat",
            supported_features=[
                "health_check",
                "model_listing",
                "chat_completions",
                "streaming",
                "multimodal",
                "chinese_support",
                "context_window",
            ],
            cost_class="METERED",
            provider_category="LLM",
            autonomous_file_access=False,
            requires_file_interfaces=False,
            model_listing_method="API",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {
            "base_url": "https://api.moonshot.cn/v1",
            "api_key": "",
            "api_key_ref": "keychain:kimi",
            "api_path": DEFAULT_CHAT_PATH,
            "timeout": 60,
            "retries": 3,
            "model": "kimi-k2-thinking-turbo",
            "temperature": 0.7,
            "top_p": 1.0,
            "max_tokens": 2048,
            "streaming": False,
        }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        normalized = dict(config)

        # Validate base URL
        base_url = str(config.get("base_url") or "").strip()
        if not base_url:
            errors.append("Base URL is required")
        else:
            normalized["base_url"] = base_url.rstrip("/")
            is_safe, reason = validate_base_url_for_ssrf(base_url)
            if not is_safe:
                errors.append(f"SSRF check failed: {reason}")

        # Validate API key
        api_key = config.get("api_key", "")
        api_key_ref = config.get("api_key_ref", "")
        if not api_key and not api_key_ref:
            errors.append("API key or API key reference is required")

        # Validate API path
        api_path = str(config.get("api_path") or DEFAULT_CHAT_PATH).strip()
        if not api_path:
            errors.append("API path is required")
        else:
            normalized["api_path"] = api_path

        # Validate timeout
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

        # Validate retries
        retries = config.get("retries", DEFAULT_MAX_RETRIES)
        if not isinstance(retries, int) or retries < 0:
            warnings.append(f"Invalid retries, using default {DEFAULT_MAX_RETRIES}")
            normalized["retries"] = DEFAULT_MAX_RETRIES

        # Validate temperature (0-2 for Kimi)
        temperature = config.get("temperature", 0.7)
        if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2:
            warnings.append("Invalid temperature, using default 0.7")
            normalized["temperature"] = 0.7

        # Validate top_p (0-1 for Kimi)
        top_p = config.get("top_p", 1.0)
        if not isinstance(top_p, (int, float)) or top_p < 0 or top_p > 1:
            warnings.append("Invalid top_p, using default 1.0")
            normalized["top_p"] = 1.0

        # Validate max_tokens
        max_tokens_raw = config.get("max_tokens")
        if max_tokens_raw is None:
            max_tokens_raw = config.get("max_output_tokens")
        if max_tokens_raw is None:
            normalized["max_tokens"] = 2048
        else:
            try:
                max_tokens = int(max_tokens_raw)
            except (TypeError, ValueError):
                max_tokens = 0
            if max_tokens < 1:
                warnings.append("Invalid max_tokens, using default 2048")
                normalized["max_tokens"] = 2048
            else:
                normalized["max_tokens"] = max_tokens

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            normalized_config=normalized,
        )

    def _base_url(self, config: dict[str, Any]) -> str:
        return normalize_base_url(str(config.get("base_url") or ""))

    def _build_url(
        self, config: dict[str, Any], path_key: str = "api_path", default_path: str = DEFAULT_CHAT_PATH
    ) -> str:
        base = self._base_url(config)
        path = str(config.get(path_key) or default_path).strip()
        return join_url(base, path, strip_prefixes=["/v1"])

    def _headers(self, config: dict[str, Any], api_key: str | None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def health(self, config: dict[str, Any]) -> HealthResult:
        url = self._build_url(config)
        timeout = _timeout_seconds(config, 30)
        api_key = config.get("api_key")
        if not api_key:
            return HealthResult(ok=False, latency_ms=0, error="API key is required")
        test_payload = {
            "model": config.get("model") or "moonshot-v1-8k",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 50,
            "stream": False,
        }
        return health_check_post(url, self._headers(config, api_key), test_payload, timeout)

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        url = self._build_url(config, path_key="models_path", default_path=DEFAULT_MODELS_PATH)
        timeout = _timeout_seconds(config, 10)

        api_key = config.get("api_key")
        if not api_key:
            return ModelListResult(ok=False, supported=True, models=[], error="API key is required")

        try:
            response = requests.get(
                url,
                headers=self._headers(config, api_key),
                timeout=timeout if timeout > 0 else None,
            )
            response.raise_for_status()
            payload = response.json()

            models: list[ModelInfo] = []

            # OpenAI-compatible response format
            if isinstance(payload, dict):
                model_list = payload.get("data") or payload.get("model_list") or []
                if isinstance(model_list, list):
                    for item in model_list:
                        if isinstance(item, dict):
                            model_id = str(item.get("id") or item.get("model_name") or "").strip()
                            if model_id:
                                context_window = item.get("context_window")
                                label = f"{model_id}"
                                if context_window:
                                    label += f" ({context_window // 1000}K context)"
                                models.append(ModelInfo(id=model_id, label=label, raw=item))
                        elif isinstance(item, str):
                            models.append(ModelInfo(id=item.strip()))

            # Fallback to known Kimi models if API doesn't return list
            if not models:
                known_models = [
                    # K2.5 series (latest flagship)
                    ("kimi-k2.5", "256K context"),
                    ("kimi-k2-0905-preview", "256K context"),
                    ("kimi-k2-0711-preview", "128K context"),
                    # K2 Thinking series (reasoning models)
                    ("kimi-k2-thinking", "256K context"),
                    ("kimi-k2-thinking-turbo", "256K context"),
                    # K2 Turbo series (fast response)
                    ("kimi-k2-turbo-preview", "256K context"),
                    ("kimi-k2-turbo", "256K context"),
                    # Moonshot V1 series (classic)
                    ("moonshot-v1-8k", "8K context"),
                    ("moonshot-v1-32k", "32K context"),
                    ("moonshot-v1-128k", "128K context"),
                    # Vision models
                    ("moonshot-v1-8k-vision-preview", "8K context"),
                    ("moonshot-v1-32k-vision-preview", "32K context"),
                    ("moonshot-v1-128k-vision-preview", "128K context"),
                ]
                for model_id, context in known_models:
                    models.append(ModelInfo(id=model_id, label=f"{model_id} ({context})"))

            return ModelListResult(ok=True, supported=True, models=models)
        except (RuntimeError, ValueError) as exc:
            return ModelListResult(ok=False, supported=True, models=[], error=str(exc))

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        url = self._build_url(config)
        timeout = _timeout_seconds(config, 60)
        retries = int(config.get("retries") or 0)
        api_key = config.get("api_key")
        if not api_key:
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=0, usage=usage, error="API key is required")
        system_prompt = config.get("system_prompt")

        # NOTE: RoleContextGateway.build_context() is async.
        # For sync invoke(), we use direct message construction as fallback.
        # The async invoke_stream() method properly uses RoleContextGateway.
        messages = [{"role": "user", "content": prompt}]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(config.get("temperature") or 0.7),
            "top_p": float(config.get("top_p") or 1.0),
            "max_tokens": _resolve_max_tokens(config, 2048),
            "stream": False,
        }

        def _extract_output(data: dict[str, Any]) -> str:
            if isinstance(data, dict):
                choices = data.get("choices", [])
                if choices and len(choices) > 0:
                    first_choice = choices[0]
                    if isinstance(first_choice, dict):
                        message = first_choice.get("message", {})
                        return message.get("content", "")
            return ""

        return invoke_with_retry(
            url,
            self._headers(config, api_key),
            payload,
            timeout,
            retries,
            prompt,
            _extract_output,
            self._usage_from_response,
        )

    def _usage_from_response(self, prompt: str, output: str, response: dict[str, Any]) -> Usage:
        """Extract usage information from Kimi API response"""
        usage_data = response.get("usage", {}) if isinstance(response, dict) else {}

        if usage_data:
            return Usage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )

        # Fallback to estimation
        return estimate_usage(prompt, output)

    async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        True streaming invoke for Kimi API using aiohttp.

        Kimi API is OpenAI-compatible, so we use SSE format:
        data: {"choices":[{"delta":{"content":"hello"}}]}

        Args:
            prompt: The prompt to send
            model: The model name (e.g., "kimi-k2-turbo-preview")
            config: Provider configuration

        Yields:
            Text tokens/chunks from the LLM response
        """
        url = self._build_url(config)
        if "stream_timeout" in config:
            timeout = _timeout_seconds(config, 60, key="stream_timeout")
        else:
            timeout = _timeout_seconds(config, 60)

        api_key = config.get("api_key")
        if not api_key:
            yield "Error: API key is required for Kimi provider"
            return

        # Build streaming payload - use RoleContextGateway to build context properly
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
        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(config.get("temperature") or 0.7),
            "top_p": float(config.get("top_p") or 1.0),
            "max_tokens": _resolve_max_tokens(config, 2048),
            "stream": True,  # Enable streaming
        }

        headers = self._headers(config, api_key)
        # Override for streaming
        headers["Accept"] = "text/event-stream"

        try:
            session = await get_stream_session(
                "kimi",
                timeout_seconds=timeout,
            )
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds_or_none(timeout, default=60)),
            ) as response:
                response.raise_for_status()

                # Instantiate parser for <think> tag fallback
                think_parser = StreamThinkingParser()
                done = False
                buffer = ""
                # Track if we've seen native reasoning across all deltas
                has_seen_native_reasoning = False

                def _handle_sse_data(data: str) -> list[str]:
                    nonlocal has_seen_native_reasoning
                    out: list[str] = []
                    payload = str(data or "").strip()
                    if not payload or payload == "[DONE]":
                        return out
                    try:
                        parsed = json.loads(payload)
                    except (RuntimeError, ValueError):
                        return out
                    choices = parsed.get("choices", [])
                    if not choices or not isinstance(choices, list):
                        return out
                    delta = choices[0].get("delta", {}) if isinstance(choices[0], dict) else {}
                    if not isinstance(delta, dict):
                        return out

                    # 首先检查原生的 reasoning 字段（优先使用）
                    for key in ("reasoning_content", "reasoning", "thinking"):
                        for text in _flatten_text(delta.get(key)):
                            if text and text.strip():  # 确保不是空白字符
                                out.append(f"{THINKING_PREFIX}{text}")
                                has_seen_native_reasoning = True

                    # 处理 content 字段
                    for part_kind, text in _extract_delta_content_parts(delta.get("content")):
                        if not text:
                            continue
                        if part_kind == "reasoning":
                            # 只有当没有原生 reasoning 时才使用 content 中的 reasoning
                            if not has_seen_native_reasoning:
                                out.append(f"{THINKING_PREFIX}{text}")
                            continue

                        # 如果已检测到过原生 reasoning，跳过 content 中的所有结构化标签
                        if has_seen_native_reasoning:
                            # 原生 reasoning 存在时，使用 think parser 处理标签
                            # 丢弃 thinking 部分（已有原生 reasoning）
                            # 保留 content 和 answer 部分
                            for parsed_kind, parsed_text in think_parser.feed_sync(text):
                                if not parsed_text:
                                    continue
                                if parsed_kind in ("content", "answer"):
                                    out.append(parsed_text)
                                # parsed_kind == "thinking" 被丢弃
                            continue

                        # 没有原生 reasoning 时，使用 think parser 解析
                        for think_kind, parsed_text in think_parser.feed_sync(text):
                            if not parsed_text:
                                continue
                            if think_kind == "thinking":
                                out.append(f"{THINKING_PREFIX}{parsed_text}")
                            else:
                                out.append(parsed_text)
                    return out

                # Process SSE stream with newline buffering, so reasoning isn't lost on TCP chunk boundaries.
                async for chunk in response.content:
                    text = chunk.decode("utf-8", errors="ignore")
                    if not text:
                        continue
                    buffer += text
                    lines = buffer.split("\n")
                    buffer = lines.pop() if lines else ""

                    for raw_line in lines:
                        line = raw_line.rstrip("\r")
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].lstrip()
                        if data_str == "[DONE]":
                            done = True
                            break
                        for token in _handle_sse_data(data_str):
                            yield token
                    if done:
                        break

                if not done and buffer.strip().startswith("data:"):
                    data_str = buffer.strip()[5:].lstrip()
                    if data_str != "[DONE]":
                        for token in _handle_sse_data(data_str):
                            yield token

                # Flush any remaining buffered content
                for kind, text in think_parser.flush():
                    if kind == "thinking":
                        # 如果已检测到过原生 reasoning，跳过 flush 中的 thinking
                        if not has_seen_native_reasoning:
                            yield f"{THINKING_PREFIX}{text}"
                    elif kind == "answer":
                        # answer 内容作为 content 输出
                        yield text
                    else:
                        # kind == "content" - 总是输出
                        yield text

        except aiohttp.ClientError as e:
            yield f"Error: Network error - {e!s}"
        except asyncio.TimeoutError:
            yield "Error: Request timeout"
        except (RuntimeError, ValueError) as e:
            yield f"Error: {e!s}"
