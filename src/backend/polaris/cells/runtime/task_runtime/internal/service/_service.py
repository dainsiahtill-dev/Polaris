"""TaskRuntimeService core composition (mixins hold method groups)."""

from __future__ import annotations

import threading

from polaris.cells.runtime.task_runtime.internal.task_board import (
    TaskBoard,
)
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter

from ..execution_session import (
    TaskExecutionSessionWriteReceipt,
)
from ._mixin_dependency_session import _DependencySessionMixin
from ._mixin_directed_effect import _DirectedEffectMixin
from ._mixin_execution import _ExecutionMixin
from ._mixin_facts_events import _FactsEventsMixin
from ._mixin_recovery_reexec import _RecoveryReexecMixin
from ._mixin_task_rows import _TaskRowsMixin


class TaskRuntimeService(
    _DirectedEffectMixin,
    _RecoveryReexecMixin,
    _TaskRowsMixin,
    _ExecutionMixin,
    _DependencySessionMixin,
    _FactsEventsMixin,
):
    """Runtime task lifecycle service for the ``runtime.task_runtime`` cell.

    Responsibilities:
    - Keep the canonical runtime taskboard rows under ``runtime/tasks/*``
    - Materialize legacy orchestration tasks into canonical task rows
    - Persist execution lease/session facts under ``runtime/tasks/*``
    - Expose a stable, resumable read model for snapshot/observer consumers"""

    def __init__(self, workspace: str, board: TaskBoard | None = None) -> None:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required for TaskRuntimeService")
        self._workspace = workspace_token
        self._board = board or TaskBoard(workspace=workspace_token)
        self._kernel_fs = KernelFileSystem(workspace_token, get_default_adapter())
        # Per-task-id locks guard only the read-modify-write cycle on session
        # files. The only FactStream work permitted under this lock is the
        # narrow DEO registry admission/pre-barrier chain; projection uses a
        # distinct lock and never acquires a session lock.
        self._session_locks: dict[int, threading.RLock] = {}
        self._session_locks_meta = threading.Lock()
        self._settlement_projection_locks: dict[int, threading.RLock] = {}
        self._settlement_projection_locks_meta = threading.Lock()
        self._last_session_write_receipt: TaskExecutionSessionWriteReceipt | None = None
        self._session_write_receipts_by_identity: dict[tuple[int, str], TaskExecutionSessionWriteReceipt] = {}
        self._session_write_receipt_lock = threading.Lock()
        self._execution_fact_append_lock = threading.Lock()

    @property
    def workspace(self) -> str:
        return self._workspace
