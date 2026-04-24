from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import aiohttp
import requests
from polaris.kernelone.llm.provider_contract import AdapterProviderContract
from polaris.kernelone.llm.providers import (
    BaseProvider,
    ProviderInfo,
    ValidationResult,
)
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelInfo, ModelListResult, Usage, estimate_usage
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds, timeout_seconds_or_none

from .http_utils import join_url, normalize_base_url
from .provider_helpers import get_stream_session, iter_sse_data_payloads

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


def _timeout_seconds(config: dict[str, Any], default: int) -> int:
    return normalize_timeout_seconds(config.get("timeout"), default=default)


def _is_openai_compat_mode(config: dict[str, Any]) -> bool:
    """Check if the config is using OpenAI compatibility mode"""
    api_path = str(config.get("api_path") or "").strip()
    return api_path.startswith("/v1/")


def _build_headers(config: dict[str, Any]) -> dict[str, str]:
    """Build headers for requests, including API key for OpenAI compatibility mode"""
    headers: dict[str, str] = {}
    if _is_openai_compat_mode(config):
        # OpenAI compatibility mode requires Authorization header
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
    adapter_messages = _CONTRACT.extract_messages({"config": config})
    if adapter_messages:
        return adapter_messages

    messages = [{"role": "user", "content": prompt}]
    system_prompt = str(config.get("system_prompt") or config.get("system") or "").strip()
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})
    return messages


class OllamaProvider(BaseProvider):
    """Ollama local provider"""

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Ollama Provider",
            type="ollama",
            description="Local Ollama provider",
            version="1.0.0",
            author="Polaris Team",
            documentation_url="https://github.com/ollama/ollama/blob/main/docs/api.md",
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
    def validate_config(cls, config: dict[str, Any]) -> ValidationResult:
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

        # Validate api_key for OpenAI compatibility mode
        api_path = str(config.get("api_path") or "").strip()
        if api_path.startswith("/v1/"):
            # OpenAI compatibility mode requires api_key (can be placeholder "ollama")
            api_key = config.get("api_key")
            if not api_key:
                warnings.append("OpenAI compatibility mode (/v1/*) requires api_key; using placeholder 'ollama'")
                normalized["api_key"] = "ollama"
            normalized["api_path"] = api_path
        else:
            # Native Ollama API doesn't require api_key
            normalized["api_path"] = api_path if api_path else DEFAULT_CHAT_PATH

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            normalized_config=normalized,
        )

    def _base_url(self, config: dict[str, Any]) -> str:
        return normalize_base_url(str(config.get("base_url") or DEFAULT_BASE_URL))

    def health(self, config: dict[str, Any]) -> HealthResult:
        base = self._base_url(config)
        timeout = _timeout_seconds(config, 10)

        # Use OpenAI compatibility endpoints if in compat mode
        if _is_openai_compat_mode(config):
            url = join_url(base, DEFAULT_OPENAI_MODELS_PATH)
            headers = _build_headers(config)
        else:
            url = join_url(base, DEFAULT_TAGS_PATH)
            headers = {}

        start = time.time()
        try:
            response = requests.get(url, headers=headers, timeout=timeout if timeout > 0 else None)
            response.raise_for_status()
            latency_ms = int((time.time() - start) * 1000)
            return HealthResult(ok=True, latency_ms=latency_ms)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            latency_ms = int((time.time() - start) * 1000)
            return HealthResult(ok=False, latency_ms=latency_ms, error=str(exc))

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        base = self._base_url(config)
        timeout = _timeout_seconds(config, 10)

        # Use OpenAI compatibility endpoints if in compat mode
        if _is_openai_compat_mode(config):
            url = join_url(base, DEFAULT_OPENAI_MODELS_PATH)
            headers = _build_headers(config)
        else:
            url = join_url(base, DEFAULT_TAGS_PATH)
            headers = {}

        try:
            response = requests.get(url, headers=headers, timeout=timeout if timeout > 0 else None)
            response.raise_for_status()
            payload = response.json()
            models: list[ModelInfo] = []

            if _is_openai_compat_mode(config):
                # OpenAI format: { "data": [{ "id": "model-id", ... }] }
                for item in payload.get("data") or []:
                    if not isinstance(item, dict):
                        continue
                    model_id = str(item.get("id") or "")
                    if model_id:
                        models.append(ModelInfo(id=model_id, raw=item))
            else:
                # Native Ollama format: { "models": [{ "name": "model-name", ... }] }
                for item in payload.get("models") or []:
                    if not isinstance(item, dict):
                        continue
                    model_id = str(item.get("name") or item.get("model") or "").strip()
                    if model_id:
                        models.append(ModelInfo(id=model_id, raw=item))

            return ModelListResult(ok=True, supported=True, models=models)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            return ModelListResult(ok=False, supported=True, models=[], error=str(exc))

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        base = self._base_url(config)
        timeout = _timeout_seconds(config, 60)
        api_path = str(config.get("api_path") or "").strip()

        is_compat = _is_openai_compat_mode(config)
        if not api_path:
            api_path = DEFAULT_OPENAI_CHAT_PATH if is_compat else DEFAULT_CHAT_PATH
        url = join_url(base, api_path)

        headers = _build_headers(config) if is_compat else {}

        start = time.time()
        try:
            payload: dict[str, Any]
            messages = _extract_messages(prompt, config)
            if is_compat:
                # OpenAI compatibility format
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
            # Native Ollama format
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

            response = requests.post(url, json=payload, headers=headers, timeout=timeout if timeout > 0 else None)
            response.raise_for_status()
            data = response.json()
            latency_ms = int((time.time() - start) * 1000)

            output = ""
            if isinstance(data, dict):
                if is_compat:
                    # OpenAI format: { "choices": [{ "message": { "content": "..." } }] }
                    choices = data.get("choices", [])
                    if choices and isinstance(choices[0], dict):
                        message = choices[0].get("message", {})
                        output = str(message.get("content") or "")
                # Native Ollama format
                elif "response" in data:
                    output = str(data.get("response") or "")
                elif "message" in data and isinstance(data.get("message"), dict):
                    output = str(data["message"].get("content") or "")

            usage = _usage_from_response(prompt, output, data, is_compat)
            return InvokeResult(ok=True, output=output.strip(), latency_ms=latency_ms, usage=usage, raw=data)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            latency_ms = int((time.time() - start) * 1000)
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=latency_ms, usage=usage, error=str(exc))

    async def invoke_stream_events(
        self,
        prompt: str,
        model: str,
        config: dict[str, Any],
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
        else:
            if DEFAULT_CHAT_PATH in api_path:
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

        session = await get_stream_session(
            "ollama",
            timeout_seconds=timeout,
        )
        async with session.post(
            url,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds_or_none(timeout, default=60)),
        ) as response:
            response.raise_for_status()

            if is_compat:
                async for data in iter_sse_data_payloads(response.content):
                    if data == "[DONE]":
                        break
                    try:
                        payload_obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload_obj, dict):
                        yield payload_obj
                return

            buffer = ""
            async for chunk in response.content:
                text = chunk.decode("utf-8", errors="ignore")
                if not text:
                    continue
                buffer += text
                lines = buffer.split("\n")
                buffer = lines.pop() if lines else ""
                for raw_line in lines:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        payload_obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload_obj, dict):
                        yield payload_obj

            trailing = buffer.strip()
            if trailing:
                try:
                    payload_obj = json.loads(trailing)
                except json.JSONDecodeError:
                    payload_obj = None
                if isinstance(payload_obj, dict):
                    yield payload_obj

    async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]) -> AsyncGenerator[str, None]:
        """Stream invoke via thread executor to avoid blocking the event loop.

        Offloads the synchronous self.invoke() call (which uses blocking requests.post)
        to a thread pool so the asyncio event loop is never stalled.
        """
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: self.invoke(prompt, model, config))
        if result.ok and result.output:
            # Honest fallback for non-native streaming path.
            yield result.output
        elif result.error:
            yield f"Error: {result.error}"


_provider = OllamaProvider()


def health(config: dict[str, Any]) -> HealthResult:
    return _provider.health(config)


def list_models(config: dict[str, Any]) -> ModelListResult:
    return _provider.list_models(config)


def invoke(prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
    return _provider.invoke(prompt, model, config)


def _usage_from_response(prompt: str, output: str, data: dict[str, Any], is_openai_compat: bool = False) -> Usage:
    try:
        if is_openai_compat:
            # OpenAI format: { "usage": { "prompt_tokens": X, "completion_tokens": Y, "total_tokens": Z } }
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
            # Native Ollama format: { "prompt_eval_count": X, "eval_count": Y }
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
        logger.debug(f"Failed to estimate Ollama usage: {e}")
    return estimate_usage(prompt, output)
