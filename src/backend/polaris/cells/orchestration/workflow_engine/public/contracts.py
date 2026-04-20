"""Public contracts for orchestration.workflow_engine Cell.

These types form the stable public interface between the WorkflowEngine
(in kernelone/workflow/engine.py) and the Cells that provide concrete
registry implementations.

ACGA 2.0 rule: only these types may cross the Cell boundary.
All internal registry logic stays in internal/.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CellHandlerRegistry",
]


# ---------------------------------------------------------------------------
# Concrete HandlerRegistry implementation (Cell-owned, kernelone-agnostic)
# ---------------------------------------------------------------------------


class CellWorkflowRegistryOps:
    """Per-Cell workflow registry read-only ops.

    Each Cell that wants to register workflows implements this protocol
    and wraps it in a CellHandlerRegistry.
    """

    __slots__ = ()

    def list_workflows(self) -> list[str]:
        raise NotImplementedError

    def get(self, name: str) -> Any | None:
        raise NotImplementedError


class CellActivityRegistryOps:
    """Per-Cell activity registry read-only ops."""

    __slots__ = ()

    def list_activities(self) -> list[str]:
        raise NotImplementedError

    def get(self, name: str) -> Any | None:
        raise NotImplementedError


@dataclass
class CellHandlerRegistry:
    """Concrete HandlerRegistry backed by per-Cell registries.

    Usage (from the Cell factory, e.g. workflow_runtime internal/factory.py):

        from polaris.cells.orchestration.workflow_engine.internal import (
            CellHandlerRegistry,
        )
        from .activity_registry import get_activity_registry
        from .workflow_registry import get_workflow_registry

        registry = CellHandlerRegistry(
            workflows=get_workflow_registry(),
            activities=get_activity_registry(),
        )
        engine = WorkflowEngine(store, timer_wheel, task_queue, activity_runner)
        engine.set_handler_registry(registry)
        await engine.start()

    The CellHandlerRegistry is owned by this Cell; other Cells must not
    import or instantiate it directly.
    """

    workflows: CellWorkflowRegistryOps
    activities: CellActivityRegistryOps

    __slots__ = ("activities", "workflows")
