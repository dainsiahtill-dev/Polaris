from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from typing import TYPE_CHECKING, Any

import aiohttp
import requests
from polaris.kernelone.llm.providers import (
    THINKING_PREFIX,
    BaseProvider,
    ProviderInfo,
    ValidationResult,
)
from polaris.kernelone.llm.providers.stream_thinking_parser import StreamThinkingParser
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelInfo, ModelListResult, Usage, estimate_usage
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds, timeout_seconds_or_none

from .http_utils import join_url, normalize_base_url, validate_base_url_for_ssrf
from .provider_helpers import (
    CircuitOpenError,
    _blocking_http_post,
    _blocking_sleep,
    get_circuit_breaker,
    get_stream_session,
    iter_sse_data_payloads,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

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


def _debug_enabled(config: dict[str, Any]) -> bool:
    local_flag = str(config.get("debug") or "").strip().lower()
    env_flag = str(os.environ.get("KERNELONE_MINIMAX_DEBUG") or "").strip().lower()
    return local_flag in {"1", "true", "yes", "on"} or env_flag in {"1", "true", "yes", "on"}


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = dict(headers or {})
    for key in list(redacted.keys()):
        if str(key or "").strip().lower() == "authorization":
            redacted[key] = "Bearer ***REDACTED***"
    return redacted


def _redact_for_debug(value: Any, key_hint: str = "") -> Any:
    lowered_key = str(key_hint or "").strip().lower()
    sensitive_markers = ("token", "key", "secret", "authorization", "password", "api_key")
    if any(marker in lowered_key for marker in sensitive_markers):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(k): _redact_for_debug(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_for_debug(item, key_hint) for item in value]
    if isinstance(value, str):
        trimmed = value.strip()
        if len(trimmed) > 120:
            return f"[REDACTED_TEXT len={len(trimmed)}]"
        return trimmed
    return value


logger = logging.getLogger(__name__)


class MiniMaxProvider(BaseProvider):
    """MiniMax API provider with thinking extraction"""

    @staticmethod
    def _build_request_payload(
        prompt: str,
        model: str,
        config: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(config.get("temperature") or 0.7),
            "max_tokens": _resolve_max_tokens(config, 2048),
            "stream": bool(stream),
        }

        tools = config.get("tools")
        if isinstance(tools, list) and tools:
            payload["tools"] = tools

        tool_choice = config.get("tool_choice")
        if tool_choice not in (None, ""):
            payload["tool_choice"] = tool_choice

        if "parallel_tool_calls" in config:
            payload["parallel_tool_calls"] = bool(config.get("parallel_tool_calls"))

        response_format = config.get("response_format")
        if isinstance(response_format, dict) and response_format:
            payload["response_format"] = response_format

        return payload

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="MiniMax Provider",
            type="minimax",
            description="MiniMax API provider for M2 model",
            version="1.0.0",
            author="Polaris Team",
            documentation_url="https://platform.minimaxi.com/docs/api-reference/text-chat",
            supported_features=[
                "thinking_extraction",
                "model_listing",
                "health_check",
                "chinese_support",
                "context_window",
                "streaming",
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
            "type": "minimax",
            "name": "MiniMax",
            "base_url": "https://api.minimaxi.com/v1",
            "api_path": "/text/chatcompletion_v2",
            "timeout": 60,
            "retries": 3,
            "temperature": 0.7,
            "max_tokens": 2048,
        }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        normalized = dict(config)

        base_url = str(config.get("base_url") or "").strip()
        if not base_url:
            errors.append("Base URL is required")
        else:
            normalized["base_url"] = base_url.rstrip("/")
            is_safe, reason = validate_base_url_for_ssrf(base_url)
            if not is_safe:
                errors.append(f"SSRF check failed: {reason}")

        api_key = config.get("api_key", "")
        if not api_key:
            errors.append("API key is required")

        api_path = str(config.get("api_path") or "/text/chatcompletion_v2").strip()
        if not api_path:
            errors.append("API path is required")
        else:
            normalized["api_path"] = api_path

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

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            normalized_config=normalized,
        )

    def _base_url(self, config: dict[str, Any]) -> str:
        return normalize_base_url(str(config.get("base_url") or ""))

    def _build_url(
        self, config: dict[str, Any], path_key: str = "api_path", default_path: str = "/text/chatcompletion_v2"
    ) -> str:
        base = self._base_url(config)
        path = str(config.get(path_key) or default_path).strip()
        return join_url(base, path, strip_prefixes=["/v1"])

    def _headers(self, config: dict[str, Any], api_key: str | None, streaming: bool = False) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        # 根据是否流式设置不同的 Accept 头
        if streaming:
            headers["Accept"] = "text/event-stream"  # 流式响应
        else:
            headers["Accept"] = "application/json"  # 普通响应
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    _STRIP_TAG_PATTERNS = [
        re.compile(r"<think[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE),
        re.compile(r"<minimax:tool_call>.*?</minimax:tool_call>", re.DOTALL | re.IGNORECASE),
        re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL | re.IGNORECASE),
        re.compile(r"<function_calls?>.*?</function_calls?>", re.DOTALL | re.IGNORECASE),
        re.compile(r"<invoke\b[^>]*>.*?</invoke>", re.DOTALL | re.IGNORECASE),
    ]

    def _extract_thinking(self, content: str) -> str | None:
        pattern = r"<think[^>]*>(.*?)</think>"
        matches = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)
        return "\n".join(matches) if matches else None

    def _clean_content(self, content: str) -> str:
        result = content
        for pat in self._STRIP_TAG_PATTERNS:
            result = pat.sub("", result)
        return result.strip()

    def health(self, config: dict[str, Any]) -> HealthResult:
        url = self._build_url(config)
        timeout = _timeout_seconds(config, 60)

        api_key = config.get("api_key")
        if not api_key:
            return HealthResult(ok=False, latency_ms=0, error="API key is required")

        start = time.time()
        try:
            test_payload = {
                "model": config.get("model", "MiniMax-M2.1"),
                "messages": [{"role": "user", "content": "1+1="}],
                "max_tokens": 50,
            }

            # _blocking_http_post safely offloads requests.post to a thread when an
            # event loop is running, preventing the call from freezing the event loop.
            response = _blocking_http_post(
                url,
                self._headers(config, api_key),
                test_payload,
                timeout,
            )
            latency_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                return HealthResult(
                    ok=False, latency_ms=latency_ms, error=f"HTTP {response.status_code}: {response.text[:100]}"
                )

            return HealthResult(ok=True, latency_ms=latency_ms)

        except requests.exceptions.ConnectionError:
            latency_ms = int((time.time() - start) * 1000)
            return HealthResult(ok=False, latency_ms=latency_ms, error="Cannot connect to MiniMax API")
        except (RuntimeError, ValueError) as exc:
            latency_ms = int((time.time() - start) * 1000)
            return HealthResult(ok=False, latency_ms=latency_ms, error=str(exc))

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        return ModelListResult(ok=True, supported=True, models=self._get_fallback_models())

    def _get_fallback_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="MiniMax-M2.1", label="MiniMax-M2.1"),
            ModelInfo(id="MiniMax-M2.1-lightning", label="MiniMax-M2.1-lightning"),
            ModelInfo(id="MiniMax-M2", label="MiniMax-M2"),
        ]

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        url = self._build_url(config)
        timeout = _timeout_seconds(config, 60)
        retries = max(0, int(config.get("retries") or 0))
        backoff_base = float(config.get("retry_base_delay") or 0.5)
        backoff_max = float(config.get("retry_max_delay") or 30.0)
        circuit_breaker = get_circuit_breaker(
            f"minimax:{url}",
            failure_threshold=int(config.get("circuit_failure_threshold") or 5),
            recovery_timeout_seconds=float(config.get("circuit_recovery_timeout") or 60.0),
        )

        # 如果传入的model为空，尝试从配置获取或使用默认值
        if not model:
            model = str(config.get("model") or "MiniMax-M2.1").strip()

        debug_mode = _debug_enabled(config)
        if debug_mode:
            logger.debug(
                "MiniMax invoke: request config - base_url=%s, api_path=%s, final_url=%s, model=%s, streaming=%s",
                config.get("base_url", "NOT SET"),
                config.get("api_path", "NOT SET"),
                url,
                model,
                config.get("streaming", "NOT SET"),
            )

        api_key = config.get("api_key")
        if not api_key:
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=0, usage=usage, error="API key is required")

        payload = self._build_request_payload(
            prompt,
            model,
            config,
            stream=bool(config.get("streaming", False)),
        )

        attempt = 0
        start = time.time()

        while True:
            try:
                circuit_breaker.before_call()
                is_streaming = bool(config.get("streaming", False))
                headers = self._headers(config, api_key, streaming=is_streaming)

                # ===== 调试日志：实际请求 =====
                if debug_mode:
                    logger.debug(
                        "MiniMax invoke: actual request - url=%s, headers=%s, payload=%s",
                        url,
                        _redact_headers(headers),
                        json.dumps(_redact_for_debug(payload), ensure_ascii=False),
                    )

                # _blocking_http_post safely offloads requests.post to a thread when an
                # event loop is running, preventing the call from freezing the event loop.
                response = _blocking_http_post(url, headers, payload, timeout)

                # ===== 调试日志：响应状态 =====
                if debug_mode:
                    logger.debug(
                        "MiniMax invoke: response status - status_code=%s, content_type=%s",
                        response.status_code,
                        response.headers.get("Content-Type", "N/A"),
                    )
                    if response.status_code != 200:
                        logger.debug(
                            "MiniMax invoke: error response text - %s",
                            _redact_for_debug(response.text[:500], "response_text"),
                        )
                latency_ms = int((time.time() - start) * 1000)

                if response.status_code != 200:
                    circuit_breaker.on_failure()
                    if response.status_code >= 500 and attempt < retries:
                        attempt += 1
                        delay = min(backoff_max, backoff_base * (2 ** max(0, attempt - 1)))
                        delay += random.uniform(0.0, min(1.0, delay * 0.2))
                        # _blocking_sleep prevents blocking the asyncio event loop.
                        _blocking_sleep(delay)
                        continue
                    return InvokeResult(
                        ok=False,
                        output="",
                        latency_ms=latency_ms,
                        usage=estimate_usage(prompt, ""),
                        error=f"HTTP {response.status_code}: {response.text[:500]}",
                    )

                is_streaming = payload.get("stream", False)

                if is_streaming:
                    output_parts: list[str] = []
                    thinking_parts: list[str] = []
                    full_response = []
                    line_count = 0
                    is_json_response = False
                    output_text = ""
                    thinking_text = ""
                    output_is_cumulative = False
                    thinking_is_cumulative = False

                    def _merge_fragment(
                        current: str,
                        fragment: str,
                        is_cumulative: bool,
                    ) -> tuple[str, bool]:
                        frag = str(fragment or "")
                        if not frag:
                            return current, is_cumulative
                        if not current:
                            return frag, is_cumulative
                        if is_cumulative:
                            if len(frag) >= len(current) and frag.startswith(current):
                                return frag, True
                            if current.startswith(frag) or frag in current:
                                return current, True
                            return current + frag, True
                        # Auto-detect cumulative mode when chunk is full-prefix growth.
                        if len(frag) > len(current) and frag.startswith(current):
                            return frag, True
                        # Duplicate or stale prefix chunks.
                        if current.endswith(frag) or current.startswith(frag):
                            return current, is_cumulative
                        return current + frag, is_cumulative

                    # ===== 调试日志：开始流式解析 =====
                    if debug_mode:
                        logger.debug("MiniMax invoke: starting streaming parse")

                    # 检查响应类型 - MiniMax可能返回JSON而不是SSE
                    content_type = response.headers.get("Content-Type", "")
                    if debug_mode:
                        logger.debug("MiniMax invoke: response content-type=%s", content_type)

                    # 如果返回的是JSON，直接解析
                    if "application/json" in content_type:
                        if debug_mode:
                            logger.debug("MiniMax invoke: detected JSON response, switching to JSON parse mode")
                        is_json_response = True
                        try:
                            json_data = response.json()
                            if debug_mode:
                                logger.debug(
                                    "MiniMax invoke: JSON data=%s",
                                    json.dumps(_redact_for_debug(json_data), ensure_ascii=False)[:500],
                                )

                            choices = json_data.get("choices", [])
                            if choices:
                                message = choices[0].get("message", {})
                                content = message.get("content", "") or ""
                                reasoning = message.get("reasoning_content", "") or ""

                                if content:
                                    output_parts.append(content)
                                    output_text, output_is_cumulative = _merge_fragment(
                                        output_text, content, output_is_cumulative
                                    )
                                if reasoning:
                                    thinking_parts.append(reasoning)
                                    thinking_text, thinking_is_cumulative = _merge_fragment(
                                        thinking_text, reasoning, thinking_is_cumulative
                                    )

                                if debug_mode:
                                    logger.debug(
                                        "MiniMax invoke: JSON parse success - content_len=%s, reasoning_len=%s",
                                        len(content) if content else 0,
                                        len(reasoning) if reasoning else 0,
                                    )

                                # 将JSON数据也存入full_response用于raw字段
                                full_response.append(json_data)
                        except (RuntimeError, ValueError) as e:
                            if debug_mode:
                                logger.debug("MiniMax invoke: JSON parse failed: %s", str(e))
                    else:
                        # SSE流式解析
                        for line in response.iter_lines():
                            line_count += 1
                            if not line:
                                continue
                            line_str = line.decode("utf-8")
                            if line_str.startswith("data: "):
                                data_str = line_str[6:]
                                if data_str.strip() == "[DONE]":
                                    break
                                try:
                                    chunk_data = json.loads(data_str)
                                    full_response.append(chunk_data)

                                    choices = chunk_data.get("choices", [])
                                    if choices:
                                        choice = choices[0]
                                        matched = False

                                        delta = choice.get("delta", {})
                                        if delta:
                                            content = delta.get("content", "")
                                            if content:
                                                output_parts.append(content)
                                                output_text, output_is_cumulative = _merge_fragment(
                                                    output_text, content, output_is_cumulative
                                                )
                                            reasoning = delta.get("reasoning_content", "")
                                            if reasoning:
                                                thinking_parts.append(reasoning)
                                                thinking_text, thinking_is_cumulative = _merge_fragment(
                                                    thinking_text, reasoning, thinking_is_cumulative
                                                )
                                            matched = True

                                        if not matched:
                                            message = choice.get("message", {})
                                            if message:
                                                content = message.get("content", "")
                                                if content:
                                                    output_parts.append(content)
                                                    output_text, output_is_cumulative = _merge_fragment(
                                                        output_text, content, output_is_cumulative
                                                    )
                                                reasoning = message.get("reasoning_content", "")
                                                if reasoning:
                                                    thinking_parts.append(reasoning)
                                                    thinking_text, thinking_is_cumulative = _merge_fragment(
                                                        thinking_text, reasoning, thinking_is_cumulative
                                                    )
                                                matched = True

                                        if not matched:
                                            text = choice.get("text", "")
                                            if text:
                                                output_parts.append(text)
                                                output_text, output_is_cumulative = _merge_fragment(
                                                    output_text, text, output_is_cumulative
                                                )
                                except json.JSONDecodeError:
                                    continue
                                except (RuntimeError, ValueError):
                                    continue

                    output = self._clean_content(output_text)
                    thinking = self._clean_content(thinking_text) if thinking_text else None

                    # ===== 调试日志：流式解析完成 =====
                    if debug_mode:
                        resp_type = "JSON (non-streaming)" if is_json_response else "SSE streaming"
                        logger.debug(
                            "MiniMax invoke: streaming parse complete - type=%s, line_count=%s, "
                            "output_parts=%s, thinking_parts=%s, output_len=%s",
                            resp_type,
                            line_count,
                            len(output_parts),
                            len(thinking_parts),
                            len(output),
                        )

                    if output:
                        circuit_breaker.on_success()
                        return InvokeResult(
                            ok=True,
                            output=output,
                            latency_ms=latency_ms,
                            usage=estimate_usage(prompt, output),
                            raw={"chunks": full_response},
                            streaming=True,
                            thinking=thinking,
                        )
                    else:
                        # If streaming failed but we have raw response, try to extract from it
                        if full_response:
                            if debug_mode:
                                logger.debug(
                                    "MiniMax invoke: streaming recovery attempt - attempting to extract from %s chunks",
                                    len(full_response),
                                )
                            # Try to extract from the last chunk's message
                            last_chunk = full_response[-1]
                            if isinstance(last_chunk, dict):
                                choices = last_chunk.get("choices", [])
                                if choices and len(choices) > 0:
                                    msg = choices[0].get("message", {})
                                    if msg:
                                        content = msg.get("content", "")
                                        if content:
                                            circuit_breaker.on_success()
                                            return InvokeResult(
                                                ok=True,
                                                output=self._clean_content(content),
                                                latency_ms=latency_ms,
                                                usage=estimate_usage(prompt, content),
                                                raw={"chunks": full_response},
                                                streaming=True,
                                                thinking=None,
                                            )

                        return InvokeResult(
                            ok=False,
                            output="",
                            latency_ms=latency_ms,
                            usage=estimate_usage(prompt, ""),
                            error="Empty streaming response from MiniMax API",
                        )

                try:
                    data = response.json()
                except (RuntimeError, ValueError) as json_err:
                    return InvokeResult(
                        ok=False,
                        output="",
                        latency_ms=latency_ms,
                        usage=estimate_usage(prompt, ""),
                        error=f"JSON parse error: {json_err!s}, raw: {response.text[:500]}",
                    )

                if debug_mode:
                    logger.debug("MiniMax invoke: response data=%s", json.dumps(data, ensure_ascii=False)[:500])

                base_resp = data.get("base_resp")
                if isinstance(base_resp, dict) and base_resp.get("status_code") != 0:
                    return InvokeResult(
                        ok=False,
                        output="",
                        latency_ms=latency_ms,
                        usage=estimate_usage(prompt, ""),
                        error=f"MiniMax API Error {base_resp.get('status_code')}: {base_resp.get('status_msg', 'Unknown error')}",
                    )

                output = ""
                thinking = None
                if isinstance(data, dict):
                    choices = data.get("choices")
                    if choices:
                        first_choice = choices[0]
                        if isinstance(first_choice, dict):
                            message = first_choice.get("message", {})
                            content = message.get("content", "")
                            output = self._clean_content(content)
                            reasoning = message.get("reasoning_content", "")
                            thinking = self._clean_content(reasoning) if reasoning else None

                if not output:
                    circuit_breaker.on_failure()
                    return InvokeResult(
                        ok=False,
                        output="",
                        latency_ms=latency_ms,
                        usage=estimate_usage(prompt, ""),
                        error="Empty response from MiniMax API",
                    )

                usage = self._usage_from_response(prompt, output, data)

                circuit_breaker.on_success()
                return InvokeResult(
                    ok=True, output=output.strip(), latency_ms=latency_ms, usage=usage, raw=data, thinking=thinking
                )

            except CircuitOpenError as exc:
                latency_ms = int((time.time() - start) * 1000)
                usage = estimate_usage(prompt, "")
                return InvokeResult(
                    ok=False,
                    output="",
                    latency_ms=latency_ms,
                    usage=usage,
                    error=str(exc),
                )
            except (RuntimeError, ValueError) as exc:
                circuit_breaker.on_failure()
                attempt += 1
                if attempt > retries:
                    latency_ms = int((time.time() - start) * 1000)
                    usage = estimate_usage(prompt, "")
                    return InvokeResult(ok=False, output="", latency_ms=latency_ms, usage=usage, error=str(exc))
                delay = min(backoff_max, backoff_base * (2 ** max(0, attempt - 1)))
                delay += random.uniform(0.0, min(1.0, delay * 0.2))
                _blocking_sleep(delay)

    def _usage_from_response(self, prompt: str, output: str, response: dict[str, Any]) -> Usage:
        usage_data = response.get("usage", {}) if isinstance(response, dict) else {}

        if usage_data:
            return Usage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )

        return estimate_usage(prompt, output)

    async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        Stream invoke the MiniMax LLM with true async streaming.

        Uses aiohttp for async HTTP requests and yields tokens as they arrive
        from the MiniMax SSE stream.

        Args:
            prompt: The prompt to send
            model: The model name (e.g., "MiniMax-M2.1")
            config: Provider configuration including api_key, base_url, etc.

        Yields:
            Text tokens/chunks from the LLM response as they arrive
        """
        url = self._build_url(config)
        timeout = _timeout_seconds(config, 60)

        # If model not provided, use config default
        if not model:
            model = str(config.get("model") or "MiniMax-M2.1").strip()

        api_key = config.get("api_key")
        if not api_key:
            yield "Error: API key is required"
            return

        payload = self._build_request_payload(
            prompt,
            model,
            config,
            stream=True,
        )

        headers = self._headers(config, api_key, streaming=True)

        try:
            session = await get_stream_session(
                "minimax",
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
                    yield f"Error: HTTP {response.status}: {error_text[:500]}"
                    return

                # Process SSE stream
                content_type = response.headers.get("Content-Type", "")

                # MiniMax may return JSON instead of SSE in some cases
                if "application/json" in content_type:
                    # Non-streaming JSON response - yield all at once
                    json_data = await response.json()
                    choices = json_data.get("choices", [])
                    if choices and len(choices) > 0:
                        message = choices[0].get("message", {})
                        content = message.get("content", "")
                        if content:
                            yield content
                    return

                # Process SSE stream line by line
                buffer = ""
                # Instantiate parser for <think> tag fallback
                think_parser = StreamThinkingParser()
                # Track if we've seen native reasoning across all deltas
                has_seen_native_reasoning = False

                async for line in response.content:
                    line_str = line.decode("utf-8").strip()
                    if not line_str:
                        continue

                    if line_str.startswith("data: "):
                        data_str = line_str[6:]

                        if data_str.strip() == "[DONE]":
                            break

                        try:
                            chunk_data = json.loads(data_str)
                            choices = chunk_data.get("choices", [])

                            if choices:
                                choice = choices[0]

                                # Try delta format (OpenAI compatible)
                                delta = choice.get("delta", {})
                                if delta:
                                    # 1) Dedicated reasoning field (优先使用原生 reasoning)
                                    reasoning = (
                                        delta.get("reasoning_content")
                                        or delta.get("reasoning")
                                        or delta.get("thinking")
                                    )
                                    if reasoning and str(reasoning).strip():
                                        yield f"{THINKING_PREFIX}{reasoning}"
                                        has_seen_native_reasoning = True

                                    # 2) Content — parse for <think> tags
                                    content = delta.get("content", "")
                                    if content:
                                        if has_seen_native_reasoning:
                                            # 已检测到过原生 reasoning，使用 think parser 处理标签
                                            # 丢弃 thinking 部分（已有原生 reasoning）
                                            # 保留 content 和 answer 部分
                                            for parsed_kind, parsed_text in think_parser.feed_sync(content):
                                                if not parsed_text:
                                                    continue
                                                if parsed_kind in ("content", "answer"):
                                                    yield parsed_text
                                                elif parsed_kind == "tool_call":
                                                    yield f"<tool_call>{parsed_text}</tool_call>"
                                                # parsed_kind == "thinking" 被丢弃
                                        else:
                                            # 没有原生 reasoning 时，使用 think parser 解析
                                            for kind, text in think_parser.feed_sync(content):
                                                if kind == "thinking":
                                                    yield f"{THINKING_PREFIX}{text}"
                                                elif kind == "tool_call":
                                                    yield f"<tool_call>{text}</tool_call>"
                                                else:
                                                    yield text
                                    continue

                                # Try message format (MiniMax specific)
                                message = choice.get("message", {})
                                if message:
                                    content = message.get("content", "")
                                    # 检查 message 中是否有原生 reasoning
                                    msg_reasoning = (
                                        message.get("reasoning_content")
                                        or message.get("reasoning")
                                        or message.get("thinking")
                                    )
                                    if msg_reasoning and str(msg_reasoning).strip():
                                        has_seen_native_reasoning = True

                                    if content and content != buffer:
                                        # Only yield new content
                                        new_content = content[len(buffer) :]
                                        if new_content:
                                            if has_seen_native_reasoning:
                                                # 已检测到过原生 reasoning，使用 think parser 处理标签
                                                # 丢弃 thinking 部分（已有原生 reasoning）
                                                # 保留 content 和 answer 部分
                                                for parsed_kind, parsed_text in think_parser.feed_sync(new_content):
                                                    if not parsed_text:
                                                        continue
                                                    if parsed_kind in ("content", "answer"):
                                                        yield parsed_text
                                                    elif parsed_kind == "tool_call":
                                                        yield f"<tool_call>{parsed_text}</tool_call>"
                                                    # parsed_kind == "thinking" 被丢弃
                                            else:
                                                # 没有原生 reasoning 时，使用 think parser 解析
                                                for kind, text in think_parser.feed_sync(new_content):
                                                    if kind == "thinking":
                                                        yield f"{THINKING_PREFIX}{text}"
                                                    elif kind == "tool_call":
                                                        yield f"<tool_call>{text}</tool_call>"
                                                    else:
                                                        yield text
                                        buffer = content
                                    continue

                                # Try direct text format
                                text = choice.get("text", "")
                                if text:
                                    yield text

                        except json.JSONDecodeError:
                            continue
                        except (RuntimeError, ValueError):
                            continue

                # Flush any remaining buffered content
                for kind, text in think_parser.flush():
                    if kind == "thinking":
                        # 如果已检测到过原生 reasoning，跳过 flush 中的 thinking
                        if not has_seen_native_reasoning:
                            yield f"{THINKING_PREFIX}{text}"
                    elif kind == "answer":
                        # answer 内容作为 content 输出
                        yield text
                    elif kind == "tool_call":
                        yield f"<tool_call>{text}</tool_call>"
                    else:
                        # kind == "content" - 总是输出
                        yield text

        except asyncio.TimeoutError:
            yield "Error: Request timeout"
        except (RuntimeError, ValueError) as exc:
            yield f"Error: {exc!s}"

    async def invoke_stream_events(
        self,
        prompt: str,
        model: str,
        config: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream raw MiniMax events for structured stream decoding."""
        url = self._build_url(config)
        timeout = _timeout_seconds(config, 60)

        if not model:
            model = str(config.get("model") or "MiniMax-M2.1").strip()

        api_key = config.get("api_key")
        if not api_key:
            raise RuntimeError("API key is required")

        payload = self._build_request_payload(
            prompt,
            model,
            config,
            stream=True,
        )
        headers = self._headers(config, api_key, streaming=True)

        session = await get_stream_session(
            "minimax",
            timeout_seconds=timeout,
        )
        async with session.post(
            url,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds_or_none(timeout, default=60)),
        ) as response:
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                payload_obj = await response.json()
                if isinstance(payload_obj, dict):
                    yield payload_obj
                return

            async for data_str in iter_sse_data_payloads(response.content):
                if data_str == "[DONE]":
                    break
                try:
                    payload_obj = json.loads(data_str)
                except (RuntimeError, ValueError):
                    continue
                if isinstance(payload_obj, dict):
                    yield payload_obj
