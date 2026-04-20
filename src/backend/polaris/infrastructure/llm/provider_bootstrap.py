"""Explicit bootstrap wiring for infrastructure LLM providers.

This module owns the startup-time bridge that connects infrastructure provider
implementations to KernelOne's provider registry and ServiceLocator runtime.
The goal is to make provider availability depend on explicit bootstrap, not on
accidental import order.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any, cast

from polaris.infrastructure.llm.providers.provider_registry import (
    ProviderManager as InfrastructureProviderManager,
    provider_manager as infrastructure_provider_manager,
)
from polaris.kernelone.llm.providers import THINKING_PREFIX, BaseProvider

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.contracts import AIRequest, AIResponse, ProviderPort

logger = logging.getLogger(__name__)
_provider_bootstrap_lock = threading.Lock()


class ProviderAdapter:
    """Provider runtime adapter exposed to KernelOne ServiceLocator."""

    def __init__(self, manager: InfrastructureProviderManager) -> None:
        self._manager = manager

    @property
    def manager(self) -> InfrastructureProviderManager:
        """Return the infrastructure manager backing this adapter."""
        return self._manager

    async def generate(self, request: AIRequest) -> AIResponse:
        """Generate a non-streamed LLM response."""
        from polaris.kernelone.llm.toolkit.contracts import AIResponse, Usage

        provider, provider_id, model, invoke_cfg = self._resolve_request(
            request,
            stream=False,
        )
        result = await asyncio.to_thread(provider.invoke, request.input, model, invoke_cfg)
        if not getattr(result, "ok", False):
            raise RuntimeError(str(getattr(result, "error", "") or "provider_invoke_failed"))

        return AIResponse.success(
            output=str(getattr(result, "output", "") or ""),
            model=model,
            provider_id=provider_id,
            usage=Usage(
                prompt_tokens=int(getattr(getattr(result, "usage", None), "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(getattr(result, "usage", None), "completion_tokens", 0) or 0),
                total_tokens=int(getattr(getattr(result, "usage", None), "total_tokens", 0) or 0),
            ),
            latency_ms=int(float(getattr(result, "latency_ms", 0) or 0)),  # type: ignore[arg-type]
            metadata=self._build_metadata(result),
        )

    async def generate_stream(self, request: AIRequest):
        """Generate a streamed LLM response."""
        from polaris.kernelone.llm.toolkit.contracts import StreamChunk, StreamEventType

        provider, provider_id, model, invoke_cfg = self._resolve_request(
            request,
            stream=True,
        )

        try:
            async for token in provider.invoke_stream(request.input, model, invoke_cfg):
                text = str(token or "")
                if text.startswith("Error:"):
                    error_text = text[6:].strip() or "provider_stream_failed"
                    yield StreamChunk(
                        content=error_text,
                        event_type=StreamEventType.ERROR,
                        metadata={"error": error_text, "provider_id": provider_id, "model": model},
                        is_final=True,
                    )
                    return
                if text.startswith(THINKING_PREFIX):
                    yield StreamChunk(
                        content=text[len(THINKING_PREFIX) :],
                        event_type=StreamEventType.REASONING_CHUNK,
                        metadata={"provider_id": provider_id, "model": model},
                    )
                    continue
                if text:
                    yield StreamChunk(
                        content=text,
                        event_type=StreamEventType.CHUNK,
                        metadata={"provider_id": provider_id, "model": model},
                    )
        except (RuntimeError, ValueError) as exc:
            error_text = str(exc) or "provider_stream_failed"
            yield StreamChunk(
                content=error_text,
                event_type=StreamEventType.ERROR,
                metadata={"error": error_text, "provider_id": provider_id, "model": model},
                is_final=True,
            )
            return

        yield StreamChunk(
            content="",
            event_type=StreamEventType.COMPLETE,
            metadata={"provider_id": provider_id, "model": model},
            is_final=True,
        )

    def _resolve_request(
        self,
        request: AIRequest,
        *,
        stream: bool,
    ) -> tuple[BaseProvider, str, str, dict[str, Any]]:
        from polaris.kernelone.llm import config_store as llm_config
        from polaris.kernelone.llm.runtime import (
            normalize_provider_type,
            resolve_provider_api_key,
        )
        from polaris.kernelone.llm.runtime_config import get_role_model

        workspace = str(request.context.get("workspace") or ".").strip() or "."
        provider_id = str(request.provider_id or "").strip()
        model = str(request.model or "").strip()
        if not provider_id or not model:
            resolved_provider_id, resolved_model = get_role_model(request.role)
            provider_id = provider_id or str(resolved_provider_id or "").strip()
            model = model or str(resolved_model or "").strip()
        if not provider_id or not model:
            raise RuntimeError(f"role_provider_binding_missing:{request.role}")

        cache_root = llm_config.resolve_workspace_cache_root_for_workspace(workspace)
        llm_payload = llm_config.load_llm_config(workspace, cache_root, settings=None)
        providers_raw = llm_payload.get("providers")
        providers: dict[str, Any] = providers_raw if isinstance(providers_raw, dict) else {}
        raw_provider_cfg = providers.get(provider_id)
        provider_cfg: dict[str, Any] = raw_provider_cfg if isinstance(raw_provider_cfg, dict) else {}
        provider_type = (
            normalize_provider_type(str(provider_cfg.get("type") or "").strip().lower())
            if isinstance(provider_cfg, dict)
            else ""
        )
        if not provider_type:
            provider_type = self._infer_provider_type(provider_id)
        if not provider_type:
            raise RuntimeError(f"provider_type_missing:{provider_id}")

        provider = self._manager.get_provider_instance(provider_type)
        if provider is None:
            raise RuntimeError(f"provider_not_found:{provider_type}")

        invoke_cfg: dict[str, Any] = dict(provider_cfg)
        invoke_cfg["type"] = provider_type
        for key in (
            "temperature",
            "max_tokens",
            "timeout",
            "system_prompt",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
        ):
            if key in request.options:
                invoke_cfg[key] = request.options[key]
        invoke_cfg["stream"] = bool(stream)
        invoke_cfg["streaming"] = bool(stream)
        if int(invoke_cfg.get("timeout") or 0) <= 0:
            invoke_cfg["timeout"] = 300
        invoke_cfg = resolve_provider_api_key(provider_id, provider_type, invoke_cfg)
        return provider, provider_id, model, invoke_cfg

    @staticmethod
    def _build_metadata(result: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        raw = getattr(result, "raw", None)
        if isinstance(raw, dict):
            metadata["raw"] = raw
        thinking = str(getattr(result, "thinking", "") or "").strip()
        if thinking:
            metadata["thinking"] = thinking
        return metadata

    @staticmethod
    def _infer_provider_type(provider_id: str) -> str:
        token = str(provider_id or "").strip().lower()
        if "ollama" in token:
            return "ollama"
        if "codex" in token:
            return "codex_cli"
        if "openai" in token:
            return "openai_compat"
        if "anthropic" in token:
            return "anthropic_compat"
        if "gemini" in token:
            return "gemini_api"
        if "minimax" in token:
            return "minimax"
        if "kimi" in token:
            return "kimi"
        return ""


def inject_kernelone_provider_runtime(
    manager: InfrastructureProviderManager | None = None,
) -> None:
    """Explicitly inject provider runtime into KernelOne.

    The function is safe to call multiple times. After Phase 3 convergence,
    get_provider_manager() returns the infrastructure ProviderManager singleton.
    If a custom manager is passed (e.g., for testing), it is used directly.

    Args:
        manager: Optional manager override (for testing). Defaults to the
            infrastructure provider_manager singleton.
    """
    from polaris.kernelone.llm.toolkit.contracts import ServiceLocator

    effective_manager = manager or infrastructure_provider_manager

    with _provider_bootstrap_lock:
        current_provider = ServiceLocator.get_provider()
        if not isinstance(current_provider, ProviderAdapter) or current_provider.manager is not effective_manager:
            ServiceLocator.register_provider(cast("ProviderPort", ProviderAdapter(effective_manager)))  # type: ignore[arg-type]

    logger.debug(
        "KernelOne provider runtime injected using %s",
        type(effective_manager).__name__,
    )


__all__ = [
    "ProviderAdapter",
    "inject_kernelone_provider_runtime",
]
