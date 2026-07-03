"""Async Gemini API provider for Google Gemini models.

Replaces sync ``requests`` calls with native ``httpx.AsyncClient`` I/O.
Maintains backward compatibility with the sync API through module-level functions.

Key improvements:
    - ``health()`` and ``list_models()`` use ``httpx.AsyncClient`` instead of ``requests``
    - ``invoke()`` uses ``async_invoke_with_retry`` for proper retry/circuit-breaker
    - All HTTP I/O is non-blocking in async contexts
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.providers import (
    ProviderConfigValidationResult,
    ProviderInfo,
    ThinkingInfo,
    WorkingDirConfig,
)
from polaris.kernelone.llm.types import (
    HealthResult,
    InvokeResult,
    ModelInfo,
    ModelListResult,
    Usage,
    estimate_usage,
)
from polaris.kernelone.shared.text_utils import normalize_timeout_seconds

from .async_base_provider import AsyncBaseProvider
from .async_http_client import AsyncProviderHttpClient
from .async_provider_helpers import async_invoke_with_retry
from .http_utils import join_url, normalize_base_url, validate_base_url_for_ssrf

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Async GeminiAPIProvider
# ---------------------------------------------------------------------------


class AsyncGeminiAPIProvider(AsyncBaseProvider):
    """Async Google Gemini API provider using httpx."""

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Gemini API Provider",
            type="gemini_api",
            description="Google Gemini API provider (async)",
            version="2.0.0",
            author="Polaris Team",
            documentation_url="https://ai.google.dev/",
            supported_features=[
                "thinking_extraction",
                "large_context",
                "model_listing",
                "health_check",
                "file_operations_via_interface",
                "multimodal_support",
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
            "base_url": "https://generativelanguage.googleapis.com",
            "api_key": "",
            "api_key_ref": "keychain:gemini",
            "api_path": "/v1beta/models/{model}:generateContent",
            "models_path": "/v1beta/models",
            "timeout": 60,
            "retries": 3,
            "temperature": 0.7,
            "max_tokens": 8192,
            "thinking_extraction": {
                "enabled": True,
                "patterns": [
                    r"<thinking>(.*?)</thinking>",
                    r"```thinking(.*?)```",
                    r"Let me think(.*?)(?:\n\n|\n[A-Z])",
                    r"I need to consider(.*?)(?:\n\n|\n[A-Z])",
                    r"Looking at this(.*?)(?:\n\n|\n[A-Z])",
                    r"Step by step(.*?)(?:\n\n|\n[A-Z])",
                ],
                "confidence_threshold": 0.6,
            },
            "model_specific": {
                "gemini-1.5-pro": {"max_tokens": 2097152, "supports_thinking": True, "context_window": 2000000},
                "gemini-1.5-flash": {"max_tokens": 1048576, "supports_thinking": True, "context_window": 1000000},
                "gemini-1.0-pro": {"max_tokens": 32768, "supports_thinking": False, "context_window": 32768},
            },
        }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ProviderConfigValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        normalized = dict(config)

        base_url = str(config.get("base_url", "https://generativelanguage.googleapis.com")).strip()
        if not base_url:
            errors.append("Base URL is required")
        else:
            normalized["base_url"] = base_url.rstrip("/")
            is_safe, reason = validate_base_url_for_ssrf(base_url)
            if not is_safe:
                errors.append(f"SSRF check failed: {reason}")

        api_key = config.get("api_key", "")
        api_key_ref = config.get("api_key_ref", "")
        if not api_key and not api_key_ref:
            errors.append("API key or API key reference is required")

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

        temperature = config.get("temperature", 0.7)
        if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2:
            warnings.append("Invalid temperature, using default 0.7")
            normalized["temperature"] = 0.7

        return ProviderConfigValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            normalized_config=normalized,
        )

    def _base_url(self, config: dict[str, Any]) -> str:
        return normalize_base_url(str(config.get("base_url") or ""))

    def _build_url(self, config: dict[str, Any], path: str) -> str:
        base = self._base_url(config)
        return join_url(base, path, strip_prefixes=["/v1beta", "/v1"])

    def _headers(self, config: dict[str, Any], api_key: str | None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key or "",
        }
        extra = config.get("headers") or {}
        if isinstance(extra, dict):
            for key, value in extra.items():
                if value is not None:
                    headers[str(key)] = str(value)
        return headers

    async def health(self, config: dict[str, Any]) -> HealthResult:
        models_path = str(config.get("models_path", "/v1beta/models")).strip()
        url = self._build_url(config, models_path)
        timeout = _timeout_seconds(config, 10)

        api_key = config.get("api_key")
        if not api_key:
            return HealthResult(ok=False, latency_ms=0, error="API key is required")

        headers = self._headers(config, api_key)

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
        models_path = str(config.get("models_path", "/v1beta/models")).strip()
        url = self._build_url(config, models_path)
        timeout = _timeout_seconds(config, 10)

        api_key = config.get("api_key")
        if not api_key:
            return ModelListResult(ok=False, supported=True, models=[], error="API key is required")

        headers = self._headers(config, api_key)

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

            import json

            payload = json.loads(result.text)
            models: list[ModelInfo] = []

            if isinstance(payload, dict):
                model_list = payload.get("models") or []
                if isinstance(model_list, list):
                    for item in model_list:
                        if isinstance(item, dict):
                            model_id = str(item.get("name") or "").strip()
                            if "/" in model_id:
                                model_id = model_id.split("/")[-1]
                            if model_id:
                                display_name = str(item.get("displayName") or model_id)
                                description = str(item.get("description") or "")
                                label = f"{display_name} - {description}" if description else display_name
                                models.append(ModelInfo(id=model_id, label=label, raw=item))

            if not models:
                known_models = [
                    ("gemini-1.5-pro", "Gemini 1.5 Pro - Advanced multimodal model"),
                    ("gemini-1.5-flash", "Gemini 1.5 Flash - Fast multimodal model"),
                    ("gemini-1.0-pro", "Gemini 1.0 Pro - Legacy text model"),
                ]
                for model_id, label in known_models:
                    models.append(ModelInfo(id=model_id, label=label))

            return ModelListResult(ok=True, supported=True, models=models)
        except (RuntimeError, ValueError) as exc:
            return ModelListResult(ok=False, supported=True, models=[], error=str(exc))

    async def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        timeout = _timeout_seconds(config, 60)
        retries = int(config.get("retries") or 0)

        api_key = config.get("api_key")
        if not api_key:
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=0, usage=usage, error="API key is required")

        api_path = str(config.get("api_path", "/v1beta/models/{model}:generateContent")).strip()
        url = self._build_url(config, api_path.replace("{model}", model))
        headers = self._headers(config, api_key)

        payload = self._build_payload(prompt, config, model)

        def extract_output(data: dict[str, Any]) -> str:
            return self._extract_output(data)

        def usage_from_response(prompt: str, output: str, data: dict[str, Any]) -> Usage:
            return self._usage_from_response(prompt, output, data)

        return await async_invoke_with_retry(
            url=url,
            headers=headers,
            payload=payload,
            timeout=timeout,
            retries=retries,
            prompt=prompt,
            extract_output=extract_output,
            usage_from_response=usage_from_response,
        )

    def _build_payload(self, prompt: str, config: dict[str, Any], model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": float(config.get("temperature") or 0.7),
                "maxOutputTokens": _resolve_max_tokens(config, 8192),
                "candidateCount": 1,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            ],
        }

        model_specific = config.get("model_specific", {})
        model_config = model_specific.get(model, {}) if isinstance(model_specific, dict) else {}
        if model_config and "max_tokens" in model_config:
            gen_config = payload.get("generationConfig")
            if isinstance(gen_config, dict):
                gen_config["maxOutputTokens"] = model_config["max_tokens"]

        return payload

    async def invoke_stream(
        self, prompt: str, model: str, config: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream invoke - Gemini API returns a single result in this adapter."""
        result = await self.invoke(prompt, model, config)
        if result.ok and result.output:
            yield {"text": result.output}
        elif result.error:
            yield {"error": True, "message": result.error}

    @staticmethod
    def _extract_output(data: dict[str, Any]) -> str:
        if not isinstance(data, dict):
            return ""
        candidates = data.get("candidates", [])
        if isinstance(candidates, list) and candidates:
            first_candidate = candidates[0]
            if isinstance(first_candidate, dict):
                content = first_candidate.get("content", {})
                if isinstance(content, dict):
                    parts = content.get("parts", [])
                    if isinstance(parts, list) and parts:
                        first_part = parts[0]
                        if isinstance(first_part, dict):
                            return str(first_part.get("text") or "")
        return ""

    @staticmethod
    def _usage_from_response(prompt: str, output: str, data: dict[str, Any]) -> Usage:
        try:
            usage_metadata = data.get("usageMetadata") if isinstance(data, dict) else None
            if isinstance(usage_metadata, dict):
                prompt_tokens = int(usage_metadata.get("promptTokenCount") or 0)
                candidates_tokens = int(usage_metadata.get("candidatesTokenCount") or 0)
                total_tokens = int(usage_metadata.get("totalTokenCount") or (prompt_tokens + candidates_tokens))
                if total_tokens > 0:
                    return Usage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=candidates_tokens,
                        total_tokens=total_tokens,
                        estimated=False,
                        prompt_chars=len(prompt or ""),
                        completion_chars=len(output or ""),
                    )
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to extract usage from response: %s", e)
        return estimate_usage(prompt, output)

    @classmethod
    def extract_thinking_support(cls, response: dict[str, Any]) -> ThinkingInfo:
        if not isinstance(response, dict) or "output" not in response:
            return ThinkingInfo(
                supports_thinking=False,
                confidence=0.0,
                format=None,
                thinking_text=None,
                extraction_method="gemini_api_default",
            )
        output = response.get("output", "")
        config = response.get("config", {})
        thinking_config = config.get("thinking_extraction", {})
        if not thinking_config.get("enabled", True):
            return ThinkingInfo(
                supports_thinking=False,
                confidence=0.0,
                format=None,
                thinking_text=None,
                extraction_method="disabled",
            )
        patterns = thinking_config.get(
            "patterns",
            [
                r"<thinking>(.*?)</thinking>",
                r"```thinking(.*?)```",
                r"Let me think(.*?)(?:\n\n|\n[A-Z])",
                r"I need to consider(.*?)(?:\n\n|\n[A-Z])",
                r"Looking at this(.*?)(?:\n\n|\n[A-Z])",
                r"Step by step(.*?)(?:\n\n|\n[A-Z])",
                r"My reasoning(.*?)(?:\n\n|\n[A-Z])",
            ],
        )
        confidence_threshold = thinking_config.get("confidence_threshold", 0.6)
        for pattern in patterns:
            try:
                match = re.search(pattern, output, re.DOTALL | re.IGNORECASE)
                if match:
                    thinking_text = match.group(1).strip()
                    confidence = cls._calculate_thinking_confidence(thinking_text)
                    if confidence >= confidence_threshold:
                        return ThinkingInfo(
                            supports_thinking=True,
                            confidence=confidence,
                            format="xml" if "<thinking>" in pattern else "markdown",
                            thinking_text=thinking_text,
                            extraction_method="gemini_api_pattern",
                        )
            except re.error:
                continue
        reasoning_indicators = [
            "let me analyze",
            "i should consider",
            "looking at the context",
            "to approach this",
            "my reasoning",
            "step by step",
            "first",
            "next",
            "finally",
            "therefore",
            "however",
        ]
        output_lower = output.lower()
        if any(indicator in output_lower for indicator in reasoning_indicators):
            return ThinkingInfo(
                supports_thinking=True,
                confidence=0.4,
                format="text",
                thinking_text=None,
                extraction_method="gemini_api_keyword",
            )
        return ThinkingInfo(
            supports_thinking=False,
            confidence=0.0,
            format=None,
            thinking_text=None,
            extraction_method="no_thinking",
        )

    @classmethod
    def get_working_directory_config(cls, config: dict[str, Any]) -> WorkingDirConfig:
        return WorkingDirConfig(
            target_directory=None,
            auto_create=False,
            cleanup_after=False,
            environment_vars={},
        )

    @staticmethod
    def _calculate_thinking_confidence(thinking_text: str) -> float:
        if not thinking_text:
            return 0.0
        length_score = min(len(thinking_text) / 400, 1.0)
        reasoning_words = [
            "because",
            "therefore",
            "however",
            "although",
            "consider",
            "analyze",
            "evaluate",
            "examine",
            "first",
            "next",
            "finally",
            "step",
        ]
        reasoning_score = sum(0.08 for word in reasoning_words if word in thinking_text.lower())
        structure_score = 0.2 if any(punct in thinking_text for punct in [".", "!", "?", ";", ":"]) else 0.0
        flow_indicators = ["first", "second", "third", "next", "then", "finally"]
        flow_score = 0.15 if any(indicator in thinking_text.lower() for indicator in flow_indicators) else 0.0
        return min(length_score + reasoning_score + structure_score + flow_score, 1.0)


# ---------------------------------------------------------------------------
# Module-level async API
# ---------------------------------------------------------------------------

_async_provider = AsyncGeminiAPIProvider()


async def async_health(config: dict[str, Any]) -> HealthResult:
    return await _async_provider.health(config)


async def async_list_models(config: dict[str, Any]) -> ModelListResult:
    return await _async_provider.list_models(config)


async def async_invoke(prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
    return await _async_provider.invoke(prompt, model, config)


# ---------------------------------------------------------------------------
# Backward-compatible sync API
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
    "AsyncGeminiAPIProvider",
    "async_health",
    "async_invoke",
    "async_list_models",
    "health",
    "invoke",
    "list_models",
]
