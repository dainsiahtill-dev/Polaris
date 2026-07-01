"""LLM invoker provider for Role Kernel execution flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel


def get_llm_invoker(kernel: RoleExecutionKernel) -> Any:
    """Return the injected or lazily-created canonical LLM invoker."""
    injected = getattr(kernel, "_injected_llm_invoker", None)
    if injected is not None:
        return injected

    invoker = getattr(kernel, "_llm_invoker", None)
    if invoker is None:
        invoker = LLMInvoker(kernel.workspace)
        kernel._llm_invoker = invoker
    return invoker


__all__ = ["get_llm_invoker"]
