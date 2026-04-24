from __future__ import annotations

import logging
import time
from typing import Any

from polaris.infrastructure.llm.sdk import CodexSDK, SDKConfig, SDKMessage, SDKUnavailableError
from polaris.kernelone.constants import DEFAULT_MAX_RETRIES
from polaris.kernelone.llm.providers import (
    BaseProvider,
    ProviderInfo,
    ThinkingInfo,
    ValidationResult,
)
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelInfo, ModelListResult, Usage, estimate_usage
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds

logger = logging.getLogger(__name__)


def _normalize_timeout(value: Any, default: int = 60) -> int:
    return normalize_timeout_seconds(value, default=default)


def _normalize_retries(value: Any, default: int = DEFAULT_MAX_RETRIES) -> int:
    if value is None:
        return default
    try:
        value = int(value)
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


def _normalize_headers(headers: Any) -> dict[str, str] | None:
    if headers is None:
        return None
    if not isinstance(headers, dict):
        return None
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if key is None:
            continue
        normalized[str(key)] = "" if value is None else str(value)
    return normalized


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


def _usage_from_sdk(prompt: str, output: str, usage: dict[str, Any] | None) -> Usage:
    if isinstance(usage, dict):
        try:
            prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
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


def _build_sdk_config(config: dict[str, Any]) -> SDKConfig:
    extra_params = config.get("sdk_params")
    if not isinstance(extra_params, dict):
        extra_params = {}
    return SDKConfig(
        api_key=config.get("api_key"),
        base_url=config.get("base_url"),
        timeout=_normalize_timeout(config.get("timeout"), 60),
        max_retries=_normalize_retries(config.get("max_retries"), DEFAULT_MAX_RETRIES),
        headers=_normalize_headers(config.get("headers")),
        additional_params=extra_params,
    )


class CodexSDKProvider(BaseProvider):
    """Codex provider backed by the official SDK."""

    def __init__(self) -> None:
        self._sdk_client: CodexSDK | None = None
        self._sdk_key: tuple[Any, ...] | None = None

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Codex SDK Provider",
            type="codex_sdk",
            description="Codex SDK integration with streaming and thinking support",
            version="1.0.0",
            author="Polaris Team",
            documentation_url="https://docs.openai.com/codex/sdk",
            supported_features=[
                "thinking_extraction",
                "streaming",
                "file_operations",
                "function_calling",
                "json_mode",
                "health_check",
                "model_listing",
            ],
            cost_class="METERED",
            provider_category="AGENT",
            autonomous_file_access=True,
            requires_file_interfaces=False,
            model_listing_method="API",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {
            "type": "codex_sdk",
            "name": "Codex SDK",
            "base_url": "https://api.openai.com/v1",
            "timeout": 60,
            "max_retries": 3,
            "default_model": "gpt-4-codex",
            "temperature": 0.2,
            "thinking_mode": True,
            "streaming": False,
            "sdk_params": {},
        }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        normalized = dict(config)

        base_url = str(config.get("base_url") or "").strip()
        if base_url:
            normalized["base_url"] = base_url
        else:
            warnings.append("base_url is empty; default SDK endpoint will be used")

        timeout = _normalize_timeout(config.get("timeout"), 60)
        if timeout != config.get("timeout"):
            warnings.append("Invalid timeout, using default 60")
        normalized["timeout"] = timeout

        max_retries = _normalize_retries(config.get("max_retries"), DEFAULT_MAX_RETRIES)
        if max_retries != config.get("max_retries"):
            warnings.append(f"Invalid max_retries, using default {DEFAULT_MAX_RETRIES}")
        normalized["max_retries"] = max_retries

        headers = _normalize_headers(config.get("headers"))
        if headers is None and config.get("headers") is not None:
            warnings.append("Headers should be a dictionary")
        if headers is not None:
            normalized["headers"] = headers

        if not config.get("api_key") and not config.get("api_key_ref"):
            warnings.append("api_key is empty; provider will require a key at runtime")

        sdk_params = config.get("sdk_params")
        if sdk_params is not None and not isinstance(sdk_params, dict):
            warnings.append("sdk_params should be a dictionary")
            normalized["sdk_params"] = {}

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            normalized_config=normalized,
        )

    def _sdk_identity(self, config: dict[str, Any]) -> tuple[Any, ...]:
        raw_headers = config.get("headers")
        headers = _normalize_headers(raw_headers) if isinstance(raw_headers, dict) else {}
        sdk_params = config.get("sdk_params") if isinstance(config.get("sdk_params"), dict) else {}
        return (
            config.get("api_key"),
            config.get("base_url"),
            _normalize_timeout(config.get("timeout"), 60),
            _normalize_retries(config.get("max_retries"), 3),
            tuple(sorted(headers.items())) if isinstance(headers, dict) else (),
            tuple(sorted(sdk_params.items())) if isinstance(sdk_params, dict) else (),
        )

    def _get_sdk_client(self, config: dict[str, Any]) -> CodexSDK:
        sdk_key = self._sdk_identity(config)
        if self._sdk_client is None or sdk_key != self._sdk_key:
            sdk_config = _build_sdk_config(config)
            self._sdk_client = CodexSDK(sdk_config)
            self._sdk_key = sdk_key
        return self._sdk_client

    def health(self, config: dict[str, Any]) -> HealthResult:
        start = time.time()
        try:
            client = self._get_sdk_client(config)
            ok = client.health_check()
            latency_ms = int((time.time() - start) * 1000)
            return HealthResult(ok=ok, latency_ms=latency_ms, error=None if ok else "health check failed")
        except SDKUnavailableError as exc:
            latency_ms = int((time.time() - start) * 1000)
            return HealthResult(ok=False, latency_ms=latency_ms, error=str(exc))
        except (RuntimeError, ValueError) as exc:
            latency_ms = int((time.time() - start) * 1000)
            return HealthResult(ok=False, latency_ms=latency_ms, error=str(exc))

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        try:
            client = self._get_sdk_client(config)
            models = client.list_models()
            model_infos = [ModelInfo(id=model, label=model) for model in models]
            return ModelListResult(ok=True, supported=True, models=model_infos)
        except SDKUnavailableError as exc:
            return ModelListResult(ok=False, supported=False, models=[], error=str(exc))
        except (RuntimeError, ValueError) as exc:
            return ModelListResult(ok=False, supported=True, models=[], error=str(exc))

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        start = time.time()
        try:
            client = self._get_sdk_client(config)
            messages = [SDKMessage(role="user", content=prompt)]

            sdk_kwargs: dict[str, Any] = {}
            temp_val = config.get("temperature")
            if temp_val is not None:
                sdk_kwargs["temperature"] = float(temp_val)
            max_tokens = _resolve_max_tokens(config)
            if max_tokens is not None:
                sdk_kwargs["max_tokens"] = max_tokens

            overrides = config.get("request_overrides")
            if isinstance(overrides, dict):
                sdk_kwargs.update(overrides)

            response = client.invoke(messages=messages, model=model, **sdk_kwargs)
            output = response.content or ""
            thinking = response.thinking
            if thinking and config.get("thinking_mode", True):
                output = f"<thinking>{thinking}</thinking>\n\n{output}".strip()

            usage = _usage_from_sdk(prompt, output, response.usage)
            latency_ms = int((time.time() - start) * 1000)
            return InvokeResult(
                ok=True,
                output=output,
                latency_ms=latency_ms,
                usage=usage,
                raw=response.metadata or {},
                streaming=bool(config.get("streaming")),
            )
        except SDKUnavailableError as exc:
            latency_ms = int((time.time() - start) * 1000)
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=latency_ms, usage=usage, error=str(exc))
        except (RuntimeError, ValueError) as exc:
            latency_ms = int((time.time() - start) * 1000)
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=latency_ms, usage=usage, error=str(exc))

    @classmethod
    def extract_thinking_support(cls, response: dict[str, Any]) -> ThinkingInfo:
        output = response.get("output") if isinstance(response, dict) else ""
        output = output or ""
        if "<thinking>" in output:
            return ThinkingInfo(
                supports_thinking=True,
                confidence=0.8,
                format="xml",
                thinking_text=None,
                extraction_method="sdk_tagged",
            )
        return ThinkingInfo(
            supports_thinking=False,
            confidence=0.0,
            format=None,
            thinking_text=None,
            extraction_method="sdk_default",
        )
