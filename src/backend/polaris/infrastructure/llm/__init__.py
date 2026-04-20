"""Infrastructure adapters for KernelOne LLM ports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AppLLMRuntimeAdapter",
    "LLMToolkitExecutorAdapter",
    "LLMToolkitParserAdapter",
    "create_kernel_tool_runtime",
    "inject_kernelone_provider_runtime",
]


def __getattr__(name: str) -> Any:
    if name == "inject_kernelone_provider_runtime":
        module = import_module("polaris.infrastructure.llm.provider_bootstrap")
        return module.inject_kernelone_provider_runtime
    if name == "AppLLMRuntimeAdapter":
        module = import_module("polaris.infrastructure.llm.provider_runtime_adapter")
        return module.AppLLMRuntimeAdapter
    if name in {"LLMToolkitExecutorAdapter", "LLMToolkitParserAdapter", "create_kernel_tool_runtime"}:
        module = import_module("polaris.infrastructure.llm.tools")
        return {
            "LLMToolkitExecutorAdapter": module.LLMToolkitExecutorAdapter,
            "LLMToolkitParserAdapter": module.LLMToolkitParserAdapter,
            "create_kernel_tool_runtime": module.create_kernel_tool_runtime,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
