"""Infrastructure LLM provider exports and transitional bootstrap bridge."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "THINKING_PREFIX",
    "AnthropicCompatProvider",
    "BaseProvider",
    "CodexCLIProvider",
    "CodexSDKProvider",
    "GeminiAPIProvider",
    "GeminiCLIProvider",
    "MiniMaxProvider",
    "OllamaProvider",
    "OpenAICompatProvider",
    "ProviderAdapter",
    "ProviderInfo",
    "ProviderManager",
    "ThinkingInfo",
    "ValidationResult",
    "WorkingDirConfig",
    "anthropic_health",
    "anthropic_invoke",
    "anthropic_list_models",
    "inject_kernelone_provider_runtime",
    "ollama_health",
    "ollama_invoke",
    "ollama_list_models",
    "openai_health",
    "openai_invoke",
    "openai_list_models",
    "provider_manager",
]


def inject_kernelone_provider_runtime() -> None:
    """Explicitly wire infrastructure providers into KernelOne."""
    from polaris.infrastructure.llm.provider_bootstrap import inject_kernelone_provider_runtime as _inject

    _inject()


def __getattr__(name: str) -> Any:
    if name in {
        "THINKING_PREFIX",
        "BaseProvider",
        "ProviderInfo",
        "ThinkingInfo",
        "ValidationResult",
        "WorkingDirConfig",
    }:
        module = import_module("polaris.kernelone.llm.providers")
        return {
            "THINKING_PREFIX": module.THINKING_PREFIX,
            "BaseProvider": module.BaseProvider,
            "ProviderInfo": module.ProviderInfo,
            "ThinkingInfo": module.ThinkingInfo,
            "ValidationResult": module.ValidationResult,
            "WorkingDirConfig": module.WorkingDirConfig,
        }[name]
    if name in {"AnthropicCompatProvider", "anthropic_health", "anthropic_invoke", "anthropic_list_models"}:
        module = import_module("polaris.infrastructure.llm.providers.anthropic_compat_provider")
        return {
            "AnthropicCompatProvider": module.AnthropicCompatProvider,
            "anthropic_health": module.health,
            "anthropic_invoke": module.invoke,
            "anthropic_list_models": module.list_models,
        }[name]
    if name == "CodexCLIProvider":
        module = import_module("polaris.infrastructure.llm.providers.codex_cli_provider")
        return module.CodexCLIProvider
    if name == "CodexSDKProvider":
        module = import_module("polaris.infrastructure.llm.providers.codex_sdk_provider")
        return module.CodexSDKProvider
    if name == "GeminiAPIProvider":
        module = import_module("polaris.infrastructure.llm.providers.gemini_api_provider")
        return module.GeminiAPIProvider
    if name == "GeminiCLIProvider":
        module = import_module("polaris.infrastructure.llm.providers.gemini_cli_provider")
        return module.GeminiCLIProvider
    if name == "MiniMaxProvider":
        module = import_module("polaris.infrastructure.llm.providers.minimax_provider")
        return module.MiniMaxProvider
    if name in {"OllamaProvider", "ollama_health", "ollama_invoke", "ollama_list_models"}:
        module = import_module("polaris.infrastructure.llm.providers.ollama_provider")
        return {
            "OllamaProvider": module.OllamaProvider,
            "ollama_health": module.health,
            "ollama_invoke": module.invoke,
            "ollama_list_models": module.list_models,
        }[name]
    if name in {"OpenAICompatProvider", "openai_health", "openai_invoke", "openai_list_models"}:
        module = import_module("polaris.infrastructure.llm.providers.openai_compat_provider")
        return {
            "OpenAICompatProvider": module.OpenAICompatProvider,
            "openai_health": module.health,
            "openai_invoke": module.invoke,
            "openai_list_models": module.list_models,
        }[name]
    if name in {"ProviderManager", "provider_manager"}:
        module = import_module("polaris.infrastructure.llm.providers.provider_registry")
        return {
            "ProviderManager": module.ProviderManager,
            "provider_manager": module.provider_manager,
        }[name]
    if name == "ProviderAdapter":
        module = import_module("polaris.infrastructure.llm.provider_bootstrap")
        return module.ProviderAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
