"""Prompt builder provider for Role Kernel execution flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder
from polaris.cells.roles.kernel.services.contracts import IPromptBuilder

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel


def get_prompt_builder(kernel: RoleExecutionKernel) -> IPromptBuilder:
    """Return the injected or lazily-created prompt builder for a kernel turn."""
    injected = getattr(kernel, "_injected_prompt_builder", None)
    if injected is not None:
        return cast(IPromptBuilder, injected)

    builder = getattr(kernel, "_prompt_builder", None)
    if builder is None:
        builder = PromptBuilder(kernel.workspace)
        kernel._prompt_builder = builder
    return cast(IPromptBuilder, builder)


__all__ = ["get_prompt_builder"]
