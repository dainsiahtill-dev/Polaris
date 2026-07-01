"""Output parser provider for Role Kernel execution flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from polaris.cells.roles.kernel.internal.output_parser import OutputParser

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel


def get_output_parser(kernel: RoleExecutionKernel) -> OutputParser:
    """Return the injected or lazily-created output parser for a kernel turn."""
    injected = getattr(kernel, "_injected_output_parser", None)
    if injected is not None:
        return cast(OutputParser, injected)

    parser = getattr(kernel, "_output_parser", None)
    if parser is None:
        parser = OutputParser()
        kernel._output_parser = parser
    return cast(OutputParser, parser)


__all__ = ["get_output_parser"]
