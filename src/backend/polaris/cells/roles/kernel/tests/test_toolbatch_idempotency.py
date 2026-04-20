"""Tests for ToolBatch idempotency (Phase 1 P0-3).

验证：
1. ToolExecutionContext 包含 idempotency 字段
2. batch_idempotency_key 格式正确
3. side_effect_class 可设置
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolExecutionContext


class TestToolExecutionContextIdempotency:
    """ToolExecutionContext idempotency 字段验证。"""

    def test_default_context(self) -> None:
        """默认 context 包含空 idempotency 字段。"""
        ctx = ToolExecutionContext()
        assert ctx.batch_idempotency_key == ""
        assert ctx.call_idempotency_key is None
        assert ctx.side_effect_class == "readonly"

    def test_context_with_idempotency(self) -> None:
        """设置 idempotency 字段。"""
        ctx = ToolExecutionContext(
            batch_idempotency_key="t1:0",
            call_idempotency_key="t1:0:read_file",
            side_effect_class="local_write",
        )
        assert ctx.batch_idempotency_key == "t1:0"
        assert ctx.call_idempotency_key == "t1:0:read_file"
        assert ctx.side_effect_class == "local_write"

    def test_idempotency_key_format(self) -> None:
        """Idempotency key 格式：turn_id:batch_seq。"""
        ctx = ToolExecutionContext(
            batch_idempotency_key="run_001:3",
        )
        parts = ctx.batch_idempotency_key.split(":")
        assert len(parts) == 2
        assert parts[0] == "run_001"
        assert parts[1] == "3"

    def test_side_effect_class_values(self) -> None:
        """side_effect_class 允许的值。"""
        for value in ("readonly", "local_write", "external_write"):
            ctx = ToolExecutionContext(side_effect_class=value)  # type: ignore[arg-type]
            assert ctx.side_effect_class == value
