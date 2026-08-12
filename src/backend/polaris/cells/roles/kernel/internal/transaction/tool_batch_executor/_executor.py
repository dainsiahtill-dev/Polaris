"""ToolBatchExecutor class composition.

Private implementation module of the tool_batch_executor package.
"""

from __future__ import annotations

from ._executor_core import _ToolBatchExecutorCore
from ._executor_execute import _ToolBatchExecuteMixin


class ToolBatchExecutor(_ToolBatchExecuteMixin, _ToolBatchExecutorCore):
    """工具批次执行器 — 负责权威工具批次执行与最终化路由。"""
