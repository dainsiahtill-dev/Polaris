"""Shared mixin base for static analysis of TaskRuntimeService composition."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.runtime.task_runtime.internal.task_board import TaskBoard
    from polaris.kernelone.fs import KernelFileSystem

    from ..execution_session import TaskExecutionSessionWriteReceipt

    class _ServiceMixinBase:
        """TYPE_CHECKING-only base so peer-mixin attributes type-check.

        Runtime bases are empty; all real attributes and methods live on the
        composed ``TaskRuntimeService`` MRO. ``__getattr__`` is never hit for
        real methods (they exist on the class) and only exists to tell mypy that
        peer-mixin symbols are available via composition.
        """

        _workspace: str
        _board: TaskBoard
        _kernel_fs: KernelFileSystem
        _session_locks: dict[int, threading.RLock]
        _session_locks_meta: threading.Lock
        _settlement_projection_locks: dict[int, threading.RLock]
        _settlement_projection_locks_meta: threading.Lock
        _last_session_write_receipt: TaskExecutionSessionWriteReceipt | None
        _session_write_receipts_by_identity: dict[tuple[int, str], TaskExecutionSessionWriteReceipt]
        _session_write_receipt_lock: threading.Lock
        _execution_fact_append_lock: threading.Lock

        def __getattr__(self, name: str) -> Any:  # pragma: no cover
            raise AttributeError(name)

else:

    class _ServiceMixinBase:
        """Runtime-empty base for TaskRuntimeService method mixins."""

        pass
