from __future__ import annotations

from polaris.kernelone.llm.toolkit import KernelToolCallingRuntime

from .executor_adapter import LLMToolkitExecutorAdapter
from .parser_adapter import LLMToolkitParserAdapter


def create_kernel_tool_runtime() -> KernelToolCallingRuntime:
    return KernelToolCallingRuntime(
        parser=LLMToolkitParserAdapter(),
        executor=LLMToolkitExecutorAdapter(),
    )
