from __future__ import annotations

import importlib
import inspect
from typing import TYPE_CHECKING, Any

from .base_sdk import BaseLLMSDK, SDKConfig, SDKMessage, SDKResponse, SDKUnavailableError

if TYPE_CHECKING:
    from collections.abc import Iterable


def _load_client_class(module_name: str, class_name: str) -> type | None:
    try:
        module = importlib.import_module(module_name)
    except (RuntimeError, ValueError):
        return None
    return getattr(module, class_name, None)


def _instantiate_client(client_cls: type, **kwargs: Any) -> Any:
    try:
        signature = inspect.signature(client_cls)
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            return client_cls(**kwargs)
        filtered = {key: value for key, value in kwargs.items() if key in signature.parameters}
        return client_cls(**filtered)
    except (RuntimeError, ValueError):
        return client_cls(**kwargs)


def _coerce_usage(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump()
        except (RuntimeError, ValueError):
            return None
    if hasattr(usage, "to_dict"):
        try:
            return usage.to_dict()
        except (RuntimeError, ValueError):
            return None
    if hasattr(usage, "__dict__"):
        return dict(usage.__dict__)
    return None


def _extract_first_choice(response: Any) -> Any | None:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        return choices[0]
    if isinstance(response, dict):
        items = response.get("choices")
        if isinstance(items, list) and items:
            return items[0]
    return None


def _extract_message_content(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, dict):
        return str(message.get("content") or "")
    content = getattr(message, "content", None)
    return str(content or "")


def _extract_message_thinking(message: Any) -> str | None:
    if message is None:
        return None
    if isinstance(message, dict):
        for key in ("reasoning_content", "reasoning", "thinking", "analysis"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None
    for key in ("reasoning_content", "reasoning", "thinking", "analysis"):
        value = getattr(message, key, None)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _extract_response_payload(response: Any) -> SDKResponse:
    content = ""
    thinking = None
    metadata: dict[str, Any] = {}

    if response is None:
        return SDKResponse(content="")

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        content = output_text

    choice = _extract_first_choice(response)
    if choice is not None:
        message = getattr(choice, "message", None) if not isinstance(choice, dict) else choice.get("message")
        if message is not None:
            content = _extract_message_content(message) or content
            thinking = _extract_message_thinking(message)
        elif isinstance(choice, dict):
            content = str(choice.get("text") or content)
        else:
            content = str(getattr(choice, "text", None) or content)
        finish_reason = (
            getattr(choice, "finish_reason", None) if not isinstance(choice, dict) else choice.get("finish_reason")
        )
        if finish_reason:
            metadata["finish_reason"] = finish_reason

    usage = _coerce_usage(getattr(response, "usage", None) if not isinstance(response, dict) else response.get("usage"))

    if not content and isinstance(response, dict):
        content = str(response.get("content") or "")

    # Extract truncation information
    truncated = finish_reason == "length"
    truncation_reason = "length" if truncated else None

    return SDKResponse(
        content=content or "",
        thinking=thinking,
        usage=usage,
        metadata=metadata,
        truncated=truncated,
        truncation_reason=truncation_reason,
        finish_reason=finish_reason,
    )


class OpenAISDK(BaseLLMSDK):
    """OpenAI Python SDK wrapper."""

    def __init__(self, config: SDKConfig) -> None:
        super().__init__(config)
        self._client = self._build_client()

    def _build_client(self) -> Any:
        client_cls = _load_client_class("openai", "OpenAI")
        if client_cls is None:
            raise SDKUnavailableError("OpenAI SDK not available. Install the openai package.")

        kwargs: dict[str, Any] = {
            "api_key": self.config.api_key,
            "base_url": self.config.base_url,
            "timeout": (self.config.timeout if (self.config.timeout or 0) > 0 else None),
            "max_retries": self.config.max_retries,
        }
        if self.config.headers:
            kwargs["default_headers"] = dict(self.config.headers)

        extra = self.config.additional_params or {}
        kwargs.update(extra)
        return _instantiate_client(client_cls, **kwargs)

    def health_check(self) -> bool:
        try:
            models = self.list_models()
            return bool(models) or True
        except (RuntimeError, ValueError):
            return False

    def list_models(self) -> list[str]:
        models_api = getattr(self._client, "models", None)
        if models_api is None or not hasattr(models_api, "list"):
            return []
        response = models_api.list()
        data = getattr(response, "data", None)
        if isinstance(response, dict):
            data = response.get("data")
        models: list[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    model_id = str(item.get("id") or item.get("name") or "").strip()
                else:
                    model_id = str(getattr(item, "id", "") or getattr(item, "name", "")).strip()
                if model_id:
                    models.append(model_id)
        return models

    def invoke(self, messages: list[SDKMessage], model: str, **kwargs: Any) -> SDKResponse:
        payload_messages = [{"role": msg.role, "content": msg.content} for msg in messages]
        chat_api = getattr(self._client, "chat", None)
        if chat_api and hasattr(chat_api, "completions"):
            create = getattr(chat_api.completions, "create", None)
            if callable(create):
                response = create(model=model, messages=payload_messages, **kwargs)
                return _extract_response_payload(response)

        responses_api = getattr(self._client, "responses", None)
        if responses_api and hasattr(responses_api, "create"):
            response = responses_api.create(model=model, input=payload_messages, **kwargs)
            return _extract_response_payload(response)

        raise RuntimeError("OpenAI SDK client does not expose chat completions or responses API.")

    def invoke_stream(self, messages: list[SDKMessage], model: str, **kwargs: Any) -> Iterable[str]:
        payload_messages = [{"role": msg.role, "content": msg.content} for msg in messages]
        chat_api = getattr(self._client, "chat", None)
        if not chat_api or not hasattr(chat_api, "completions"):
            raise RuntimeError("OpenAI SDK streaming is not available.")
        create = getattr(chat_api.completions, "create", None)
        if not callable(create):
            raise RuntimeError("OpenAI SDK streaming is not available.")

        stream = create(model=model, messages=payload_messages, stream=True, **kwargs)
        for chunk in stream:
            choice = _extract_first_choice(chunk)
            if choice is None:
                continue
            delta = getattr(choice, "delta", None) if not isinstance(choice, dict) else choice.get("delta")
            if delta is None:
                continue
            content = delta.get("content") if isinstance(delta, dict) else getattr(delta, "content", None)
            if content:
                yield str(content)

    def supports_feature(self, feature: str) -> bool:
        return feature in {
            "streaming",
            "json_mode",
            "function_calling",
            "tool_use",
            "reasoning",
        }
