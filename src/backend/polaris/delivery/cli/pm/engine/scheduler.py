"""Task scheduler module for Polaris engine.

This module defines the scheduler protocol and implementations
for different scheduling policies (FIFO, priority, DAG).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from polaris.delivery.cli.pm.engine.helpers import (
    _build_batches,
    _env_positive_int,
    _order_tasks,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

_DEFAULT_ROLE_CONTEXT_HISTORY_LIMIT = 24


@runtime_checkable
class SchedulerProtocol(Protocol):
    """Scheduling strategy protocol."""

    def schedule(
        self,
        tasks: Sequence[dict[str, Any]],
        workers: int,
        policy: str,
    ) -> list[list[dict[str, Any]]]:
        """Return execution batches. Each inner list is a dispatch wave."""
        ...


class SingleWorkerScheduler:
    """Default scheduler implementation.

    The scheduler is multi-worker aware (returns batches), but execution can
    still be serialized by the caller for compatibility.
    """

    def schedule(
        self,
        tasks: Sequence[dict[str, Any]],
        workers: int,
        policy: str,
    ) -> list[list[dict[str, Any]]]:
        """Schedule tasks into execution batches."""
        ordered = _order_tasks(tasks, policy)
        return _build_batches(ordered, max(1, workers), policy)


def _role_context_history_limit() -> int:
    """Get the role context history limit from environment."""
    limit = _env_positive_int(
        "KERNELONE_ROLE_CONTEXT_HISTORY_LIMIT",
        _DEFAULT_ROLE_CONTEXT_HISTORY_LIMIT,
    )
    return max(4, int(limit or _DEFAULT_ROLE_CONTEXT_HISTORY_LIMIT))


__all__ = [
    "SchedulerProtocol",
    "SingleWorkerScheduler",
    "_role_context_history_limit",
]
