"""KernelOne task graph exports.

.. deprecated::
   This module previously re-exported Polaris business task classes from
   ``polaris.kernelone.task_graph.task_board`` (now deleted).
   The canonical sources are:

   - Enums/Task entity: ``polaris.domain.entities.task``
     (``Task``, ``TaskStatus``, ``TaskPriority``)

   - TaskBoard implementation: ``polaris.cells.runtime.task_runtime.public.task_board_contract``
     (``TaskBoard``, ``create_taskboard``, ``InvalidTaskStateTransitionError``,
     ``TaskBoardToolInterface``)

   Import directly from those modules.  No backward-compat shim is provided here.

Architecture note: ``kernelone`` is the bottom runtime layer and MUST NOT re-export
from ``domain`` or ``cells`` layers (import fence enforced by
``test_kernelone_release_gates.test_kernelone_import_fence_blocks_reverse_layer_imports``).
"""

__all__ = []
