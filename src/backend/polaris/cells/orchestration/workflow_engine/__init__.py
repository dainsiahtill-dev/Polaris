"""orchestration.workflow_engine Cell.

Owns the KernelOne WorkflowEngine and the HandlerRegistry protocol.
"""

from __future__ import annotations

from polaris.kernelone.workflow.engine import (
    HandlerRegistryPort,
    TaskExecutionOutcome,
    TaskRuntimeState,
    WorkflowEngine,
    WorkflowRuntimeState,
)

from .public.contracts import CellHandlerRegistry

# Backward-compatible alias for older Cell imports.
HandlerRegistry = HandlerRegistryPort

__all__ = [
    "CellHandlerRegistry",
    "HandlerRegistry",
    "TaskExecutionOutcome",
    "TaskRuntimeState",
    "WorkflowEngine",
    "WorkflowRuntimeState",
]
