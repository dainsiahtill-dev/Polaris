"""Async Ollama provider for local LLM inference.

Replaces sync ``requests`` calls with native ``httpx.AsyncClient`` I/O.
Maintains backward compatibility with the sync API through module-level functions.

Key improvements:
    - ``health()`` and ``list_models()`` use ``httpx.AsyncClient`` instead of ``requests``
    - ``invoke()`` uses ``async_invoke_with_retry`` for proper retry/circuit-breaker
    - ``invoke_stream()`` uses native httpx streaming instead of aiohttp
    - All HTTP I/O is non-blocking in async contexts
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import httpx
from polaris.kernelone.llm.provider_contract import AdapterProviderContract
from polaris.kernelone.llm.providers import (
    ProviderConfigValidationResult,
    ProviderInfo,
)
from polaris.kernelone.llm.types import (
    HealthResult,
    InvokeResult,
    ModelInfo,
    ModelListResult,
    Usage,
    estimate_usage,
)
from polaris.kernelone.shared.text_utils import (
    normalize_timeout_seconds,
)

from .async_base_provider import AsyncBaseProvider
from .async_http_client import AsyncProviderHttpClient
from .async_provider_helpers import async_invoke_with_retry
from .http_utils import join_url, normalize_base_url
from .provider_helpers import build_chat_messages_payload

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://120.24.117.59:11434"
DEFAULT_TAGS_PATH = "/api/tags"
DEFAULT_CHAT_PATH = "/api/chat"
DEFAULT_GENERATE_PATH = "/api/generate"
DEFAULT_OPENAI_CHAT_PATH = "/v1/chat/completions"
DEFAULT_OPENAI_MODELS_PATH = "/v1/models"
_CONTRACT = AdapterProviderContract()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _timeout_seconds(config: dict[str, Any], default: int) -> int:
    return normalize_timeout_seconds(config.get("timeout"), default=default)


def _is_openai_compat_mode(config: dict[str, Any]) -> bool:
    api_path = str(config.get("api_path") or "").strip()
    return api_path.startswith("/v1/")


def _build_headers(config: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    if _is_openai_compat_mode(config):
        api_key = str(config.get("api_key") or "ollama")
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


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


def _extract_messages(prompt: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    chat_messages = config.get("chat_messages")
    if isinstance(chat_messages, list) and chat_messages:
        system_prompt = str(config.get("system_prompt") or config.get("system") or "").strip()
        return list(build_chat_messages_payload(chat_messages, prompt, system_prompt=system_prompt or None))

    adapter_messages = _CONTRACT.extract_messages({"config": config})
    if adapter_messages:
        return adapter_messages

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    system_prompt = str(config.get("system_prompt") or config.get("system") or "").strip()
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})
    return messages


def _usage_from_response(prompt: str, output: str, data: dict[str, Any], is_openai_compat: bool = False) -> Usage:
    try:
        if is_openai_compat:
            usage = data.get("usage") if isinstance(data, dict) else None
            if isinstance(usage, dict):
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
                if total_tokens > 0:
                    return Usage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        estimated=False,
                        prompt_chars=len(prompt or ""),
                        completion_chars=len(output or ""),
                    )
        else:
            prompt_tokens = int(data.get("prompt_eval_count") or 0)
            completion_tokens = int(data.get("eval_count") or 0)
            total_tokens = prompt_tokens + completion_tokens
            if total_tokens > 0:
                return Usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    estimated=False,
                    prompt_chars=len(prompt or ""),
                    completion_chars=len(output or ""),
                )
    except (RuntimeError, ValueError) as e:
        logger.debug("Failed to estimate Ollama usage: %s", e)
    return estimate_usage(prompt, output)


def _extract_output(data: dict[str, Any], is_compat: bool) -> str:
    """Extract output text from Ollama response."""
    if not isinstance(data, dict):
        return ""
    if is_compat:
        choices = data.get("choices", [])
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            return str(message.get("content") or "")
    elif "response" in data:
        return str(data.get("response") or "")
    elif "message" in data and isinstance(data.get("message"), dict):
        return str(data["message"].get("content") or "")
    return ""


# ---------------------------------------------------------------------------
# Async OllamaProvider
# ---------------------------------------------------------------------------


class AsyncOllamaProvider(AsyncBaseProvider):
    """Async Ollama local provider using httpx."""

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Ollama Provider",
            type="ollama",
            description="Local Ollama provider (async)",
            version="2.0.0",
            author="Polaris Team",
            documentation_url="https://github.com/ollama/ollama/blob/master/docs/api.md",
            supported_features=[
                "health_check",
                "model_listing",
                "local_inference",
                "chat",
                "generate",
                "tool_calling",
                "streaming",
            ],
            cost_class="LOCAL",
            provider_category="LLM",
            autonomous_file_access=False,
            requires_file_interfaces=True,
            model_listing_method="API",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {
            "base_url": DEFAULT_BASE_URL,
            "timeout": 60,
            "api_path": "",
            "use_chat": False,
            "api_key": "ollama",
        }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ProviderConfigValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        normalized = dict(config)

        base_url = normalize_base_url(str(config.get("base_url") or DEFAULT_BASE_URL))
        if not base_url:
            errors.append("base_url is required")
        else:
            normalized["base_url"] = base_url

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

        api_path = str(config.get("api_path") or "").strip()
        if api_path.startswith("/v1/"):
            api_key = config.get("api_key")
            if not api_key:
                warnings.append("OpenAI compatibility mode (/v1/*) requires api_key; using placeholder 'ollama'")
                normalized["api_key"] = "ollama"
            normalized["api_path"] = api_path
        else:
            normalized["api_path"] = api_path if api_path else DEFAULT_CHAT_PATH

        return ProviderConfigValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            normalized_config=normalized,
        )

    def _base_url(self, config: dict[str, Any]) -> str:
        return normalize_base_url(str(config.get("base_url") or DEFAULT_BASE_URL))

    async def health(self, config: dict[str, Any]) -> HealthResult:
        base = self._base_url(config)
        timeout = _timeout_seconds(config, 10)

        if _is_openai_compat_mode(config):
            url = join_url(base, DEFAULT_OPENAI_MODELS_PATH)
            headers = _build_headers(config)
        else:
            url = join_url(base, DEFAULT_TAGS_PATH)
            headers = {}

        async with AsyncProviderHttpClient(timeout=float(timeout)) as client:
            result = await client.get_json(url, headers)

        if result.status_code >= 400:
            return HealthResult(
                ok=False,
                latency_ms=result.elapsed_ms,
                error=f"HTTP {result.status_code}: {result.text[:200]}",
            )
        return HealthResult(ok=True, latency_ms=result.elapsed_ms)

    async def list_models(self, config: dict[str, Any]) -> ModelListResult:
        base = self._base_url(config)
        timeout = _timeout_seconds(config, 10)

        if _is_openai_compat_mode(config):
            url = join_url(base, DEFAULT_OPENAI_MODELS_PATH)
            headers = _build_headers(config)
        else:
            url = join_url(base, DEFAULT_TAGS_PATH)
            headers = {}

        try:
            async with AsyncProviderHttpClient(timeout=float(timeout)) as client:
                result = await client.get_json(url, headers)

            if result.status_code >= 400:
                return ModelListResult(
                    ok=False,
                    supported=True,
                    models=[],
                    error=f"HTTP {result.status_code}",
                )

            payload = json.loads(result.text)
            models: list[ModelInfo] = []

            if _is_openai_compat_mode(config):
                for item in payload.get("data") or []:
                    if not isinstance(item, dict):
                        continue
                    model_id = str(item.get("id") or "")
                    if model_id:
                        models.append(ModelInfo(id=model_id, raw=item))
            else:
                for item in payload.get("models") or []:
                    if not isinstance(item, dict):
                        continue
                    model_id = str(item.get("name") or item.get("model") or "").strip()
                    if model_id:
                        models.append(ModelInfo(id=model_id, raw=item))

            return ModelListResult(ok=True, supported=True, models=models)
        except (RuntimeError, ValueError) as exc:
            return ModelListResult(ok=False, supported=True, models=[], error=str(exc))

    async def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        base = self._base_url(config)
        timeout = _timeout_seconds(config, 60)
        api_path = str(config.get("api_path") or "").strip()
        is_compat = _is_openai_compat_mode(config)

        if not api_path:
            api_path = DEFAULT_OPENAI_CHAT_PATH if is_compat else DEFAULT_CHAT_PATH
        url = join_url(base, api_path)
        headers = _build_headers(config) if is_compat else {}

        payload = self._build_payload(prompt, model, config, is_compat, api_path)

        def extract_output(data: dict[str, Any]) -> str:
            return _extract_output(data, is_compat)

        def usage_from_response(prompt: str, output: str, data: dict[str, Any]) -> Usage:
            return _usage_from_response(prompt, output, data, is_compat)

        return await async_invoke_with_retry(
            url=url,
            headers=headers,
            payload=payload,
            timeout=timeout,
            retries=config.get("retries", 3),
            prompt=prompt,
            extract_output=extract_output,
            usage_from_response=usage_from_response,
        )

    def _build_payload(
        self,
        prompt: str,
        model: str,
        config: dict[str, Any],
        is_compat: bool,
        api_path: str,
    ) -> dict[str, Any]:
        messages = _extract_messages(prompt, config)
        payload: dict[str, Any]

        if is_compat:
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
            }
            temperature = config.get("temperature")
            if temperature is not None:
                payload["temperature"] = float(temperature)
            max_tokens = _resolve_max_tokens(config)
            if max_tokens is not None:
                payload["max_tokens"] = int(max_tokens)
            tools = config.get("tools")
            if isinstance(tools, list) and tools:
                payload["tools"] = tools
                tool_choice = config.get("tool_choice")
                if tool_choice not in (None, ""):
                    payload["tool_choice"] = tool_choice
                parallel_tool_calls = config.get("parallel_tool_calls")
                if isinstance(parallel_tool_calls, bool):
                    payload["parallel_tool_calls"] = parallel_tool_calls
        elif DEFAULT_CHAT_PATH in api_path:
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
            }
            tools = config.get("tools")
            if isinstance(tools, list) and tools:
                payload["tools"] = tools
        else:
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False,
            }
            system_prompt = config.get("system_prompt") or config.get("system")
            if system_prompt:
                payload["system"] = str(system_prompt)

        if config.get("options") is not None:
            payload["options"] = config.get("options")
        if config.get("keep_alive") is not None:
            payload["keep_alive"] = config.get("keep_alive")
        if config.get("format") is not None:
            payload["format"] = config.get("format")
        if config.get("think") is not None:
            payload["think"] = config.get("think")
        if config.get("logprobs") is not None:
            payload["logprobs"] = config.get("logprobs")
        if config.get("top_logprobs") is not None:
            payload["top_logprobs"] = config.get("top_logprobs")

        overrides = config.get("request_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        return payload

    async def invoke_stream(
        self, prompt: str, model: str, config: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        base = self._base_url(config)
        timeout = _timeout_seconds(config, 60)
        api_path = str(config.get("api_path") or "").strip()
        is_compat = _is_openai_compat_mode(config)

        if not api_path:
            api_path = DEFAULT_OPENAI_CHAT_PATH if is_compat else DEFAULT_CHAT_PATH
        url = join_url(base, api_path)
        headers = _build_headers(config) if is_compat else {}
        messages = _extract_messages(prompt, config)

        payload = self._build_stream_payload(prompt, model, config, is_compat, api_path, messages)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(float(timeout))) as http_client:
                response = await http_client.send(
                    http_client.build_request("POST", url, headers=headers, json=payload),
                    stream=True,
                )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if is_compat and line.strip() == "data: [DONE]":
                        break
                    if line.startswith("data: "):
                        line = line[6:]
                    try:
                        data = json.loads(line)
                        if isinstance(data, dict):
                            yield data
                    except json.JSONDecodeError:
                        continue
        except httpx.HTTPError as exc:
            yield {"error": True, "code": None, "message": f"Connection error: {exc}"}
        except asyncio.TimeoutError:
            yield {"error": True, "code": None, "message": "Request timeout"}

    def _build_stream_payload(
        self,
        prompt: str,
        model: str,
        config: dict[str, Any],
        is_compat: bool,
        api_path: str,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if is_compat:
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": True,
            }
            temperature = config.get("temperature")
            if temperature is not None:
                payload["temperature"] = float(temperature)
            max_tokens = _resolve_max_tokens(config)
            if max_tokens is not None:
                payload["max_tokens"] = int(max_tokens)
            tools = config.get("tools")
            if isinstance(tools, list) and tools:
                payload["tools"] = tools
                tool_choice = config.get("tool_choice")
                if tool_choice not in (None, ""):
                    payload["tool_choice"] = tool_choice
                parallel_tool_calls = config.get("parallel_tool_calls")
                if isinstance(parallel_tool_calls, bool):
                    payload["parallel_tool_calls"] = parallel_tool_calls
        elif DEFAULT_CHAT_PATH in api_path:
            payload = {
                "model": model,
                "messages": messages,
                "stream": True,
            }
            tools = config.get("tools")
            if isinstance(tools, list) and tools:
                payload["tools"] = tools
        else:
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": True,
            }
            system_prompt = config.get("system_prompt") or config.get("system")
            if system_prompt:
                payload["system"] = str(system_prompt)

        if config.get("options") is not None:
            payload["options"] = config.get("options")
        if config.get("keep_alive") is not None:
            payload["keep_alive"] = config.get("keep_alive")
        if config.get("format") is not None:
            payload["format"] = config.get("format")
        if config.get("think") is not None:
            payload["think"] = config.get("think")
        if config.get("logprobs") is not None:
            payload["logprobs"] = config.get("logprobs")
        if config.get("top_logprobs") is not None:
            payload["top_logprobs"] = config.get("top_logprobs")

        overrides = config.get("request_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        return payload


# ---------------------------------------------------------------------------
# Module-level async API
# ---------------------------------------------------------------------------

_async_provider = AsyncOllamaProvider()


async def async_health(config: dict[str, Any]) -> HealthResult:
    return await _async_provider.health(config)


async def async_list_models(config: dict[str, Any]) -> ModelListResult:
    return await _async_provider.list_models(config)


async def async_invoke(prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
    return await _async_provider.invoke(prompt, model, config)


# ---------------------------------------------------------------------------
# Backward-compatible sync API (wraps async via asyncio.run)
# ---------------------------------------------------------------------------


def health(config: dict[str, Any]) -> HealthResult:
    """Sync wrapper for backward compatibility."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _async_provider.health(config)).result()
    return asyncio.run(_async_provider.health(config))


def list_models(config: dict[str, Any]) -> ModelListResult:
    """Sync wrapper for backward compatibility."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _async_provider.list_models(config)).result()
    return asyncio.run(_async_provider.list_models(config))


def invoke(prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
    """Sync wrapper for backward compatibility."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _async_provider.invoke(prompt, model, config)).result()
    return asyncio.run(_async_provider.invoke(prompt, model, config))


__all__ = [
    "AsyncOllamaProvider",
    "async_health",
    "async_invoke",
    "async_list_models",
    "health",
    "invoke",
    "list_models",
]
