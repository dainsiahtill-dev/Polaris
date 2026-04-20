"""ContextRequest 统一测试

测试目标: P1-2 ContextRequest 统一到 contracts.py

验证:
1. ContextRequest 从 polaris.kernelone.context.contracts 导入
2. TurnEngineContextRequest 包含所有必需字段
3. history 字段接受 tuple of tuples 格式
"""

from __future__ import annotations

from dataclasses import fields

import pytest
from polaris.kernelone.context.contracts import (
    CompactSnapshot,
    ContextBudget,
    ContextPack,
    ContextRequest,
    ContextSource,
    TurnEngineContextRequest,
    TurnEngineContextResult,
)


class TestContextRequestImport:
    """P1-2-1: 验证 TurnEngineContextRequest 从 contracts 导入"""

    def test_turn_engine_context_request_imported_from_contracts(self) -> None:
        """TurnEngineContextRequest 应该从 polaris.kernelone.context.contracts 导入"""
        # Verify TurnEngineContextRequest exists and is imported correctly
        # context_gateway.py imports it as: from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest
        assert TurnEngineContextRequest is not None
        assert hasattr(TurnEngineContextRequest, "__dataclass_fields__")

    def test_context_request_exists_in_contracts(self) -> None:
        """ContextRequest (legacy) 也存在于 contracts.py"""
        # Both ContextRequest (legacy) and TurnEngineContextRequest (unified) exist in contracts
        assert ContextRequest is not None
        # They should be different classes
        assert ContextRequest != TurnEngineContextRequest

    def test_turn_engine_context_request_fields_complete(self) -> None:
        """P1-2-2: TurnEngineContextRequest 包含所有必需字段"""
        expected_fields = {
            "message",
            "history",
            "task_id",
            "strategy_receipt",
            "context_os_snapshot",
        }

        actual_fields = {f.name for f in fields(TurnEngineContextRequest)}

        # All expected fields must be present
        assert expected_fields.issubset(actual_fields), f"Missing fields: {expected_fields - actual_fields}"

    def test_turn_engine_context_request_field_types(self) -> None:
        """P1-2-2b: TurnEngineContextRequest 字段类型正确"""
        request = TurnEngineContextRequest(
            message="test message",
            history=(("user", "hello"), ("assistant", "hi there")),
            task_id="task-123",
            strategy_receipt=None,
            context_os_snapshot={"transcript_log": []},
        )

        assert request.message == "test message"
        assert request.history == (("user", "hello"), ("assistant", "hi there"))
        assert request.task_id == "task-123"
        assert request.strategy_receipt is None
        assert request.context_os_snapshot == {"transcript_log": []}

    def test_history_accepts_tuple_of_tuples(self) -> None:
        """P1-2-3: history 字段接受 tuple of tuples 格式"""
        # Valid tuple of tuples
        valid_history = (
            ("system", "You are a helpful assistant"),
            ("user", "Hello"),
            ("assistant", "Hi there!"),
            ("tool", "Tool result here"),
        )

        request = TurnEngineContextRequest(
            message="test",
            history=valid_history,
            task_id=None,
            strategy_receipt=None,
            context_os_snapshot=None,
        )

        assert request.history == valid_history
        assert len(request.history) == 4

    def test_history_accepts_empty_tuple(self) -> None:
        """P1-2-3b: history 字段接受空 tuple"""
        request = TurnEngineContextRequest(
            message="test",
            history=(),
            task_id=None,
            strategy_receipt=None,
            context_os_snapshot=None,
        )

        assert request.history == ()

    def test_context_os_snapshot_accepts_dict(self) -> None:
        """P1-2-3c: context_os_snapshot 字段接受 dict"""
        snapshot = {
            "transcript_log": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            "working_state": {"current_task": "test-task"},
            "artifact_store": [],
        }

        request = TurnEngineContextRequest(
            message="test",
            history=(),
            task_id=None,
            strategy_receipt=None,
            context_os_snapshot=snapshot,
        )

        assert request.context_os_snapshot == snapshot
        assert "transcript_log" in request.context_os_snapshot

    def test_context_os_snapshot_accepts_none(self) -> None:
        """P1-2-3d: context_os_snapshot 字段接受 None"""
        request = TurnEngineContextRequest(
            message="test",
            history=(),
            task_id=None,
            strategy_receipt=None,
            context_os_snapshot=None,
        )

        assert request.context_os_snapshot is None


class TestContextBudget:
    """Test ContextBudget dataclass"""

    def test_context_budget_creation(self) -> None:
        """ContextBudget 应该正确创建"""
        budget = ContextBudget(max_tokens=8000, max_chars=32000, cost_class="medium")

        assert budget.max_tokens == 8000
        assert budget.max_chars == 32000
        assert budget.cost_class == "medium"

    def test_context_budget_frozen(self) -> None:
        """ContextBudget 应该是不可变的"""
        from dataclasses import FrozenInstanceError

        budget = ContextBudget(max_tokens=8000, max_chars=32000)

        with pytest.raises(FrozenInstanceError):
            budget.max_tokens = 10000  # type: ignore[misc]


class TestContextSource:
    """Test ContextSource dataclass"""

    def test_context_source_creation(self) -> None:
        """ContextSource 应该正确创建"""

        source = ContextSource(
            source_type="memory",
            source_id="mem-1",
            role="director",
            text="Some content",
            tokens=100,
            importance=0.8,
        )

        assert source.source_type == "memory"
        assert source.source_id == "mem-1"
        assert source.role == "director"
        assert source.text == "Some content"
        assert source.tokens == 100
        assert source.importance == 0.8


class TestCompactSnapshot:
    """Test CompactSnapshot dataclass"""

    def test_compact_snapshot_creation(self) -> None:
        """CompactSnapshot 应该正确创建"""
        budget = ContextBudget(max_tokens=8000, max_chars=32000)
        sources = [
            ContextSource(
                source_type="memory",
                source_id="mem-1",
                role="director",
                text="content",
                tokens=100,
                importance=0.8,
            )
        ]

        snapshot = CompactSnapshot(
            role="director",
            mode="chat",
            run_id="run-1",
            step=1,
            budget=budget,
            sources=sources,
            total_tokens=100,
            total_chars=400,
        )

        assert snapshot.role == "director"
        assert snapshot.total_tokens == 100
        assert snapshot.is_within_budget() is True

    def test_compact_snapshot_within_budget(self) -> None:
        """CompactSnapshot.is_within_budget() 正确判断"""
        budget = ContextBudget(max_tokens=1000, max_chars=4000)
        snapshot = CompactSnapshot(
            role="director",
            mode="chat",
            run_id="run-1",
            step=1,
            budget=budget,
            sources=[],
            total_tokens=500,
            total_chars=2000,
        )

        assert snapshot.is_within_budget() is True

    def test_compact_snapshot_exceeds_budget(self) -> None:
        """CompactSnapshot.is_within_budget() 正确判断超出限制"""
        budget = ContextBudget(max_tokens=1000, max_chars=4000)
        snapshot = CompactSnapshot(
            role="director",
            mode="chat",
            run_id="run-1",
            step=1,
            budget=budget,
            sources=[],
            total_tokens=1500,
            total_chars=6000,
        )

        assert snapshot.is_within_budget() is False


class TestTurnEngineContextResult:
    """Test TurnEngineContextResult dataclass"""

    def test_turn_engine_context_result_creation(self) -> None:
        """TurnEngineContextResult 应该正确创建"""
        result = TurnEngineContextResult(
            messages=(
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ),
            token_estimate=100,
            context_sources=("memory", "task"),
        )

        assert len(result.messages) == 2
        assert result.token_estimate == 100
        assert result.context_sources == ("memory", "task")

    def test_turn_engine_context_result_defaults(self) -> None:
        """TurnEngineContextResult 应该有正确的默认值"""
        result = TurnEngineContextResult()

        assert result.messages == ()
        assert result.token_estimate == 0
        assert result.context_sources == ()


class TestContextPack:
    """Test ContextPack dataclass"""

    def test_context_pack_creation(self) -> None:
        """ContextPack 应该正确创建"""
        budget = ContextBudget(max_tokens=8000, max_chars=32000)
        sources = [
            ContextSource(
                source_type="memory",
                source_id="mem-1",
                role="director",
                text="content",
                tokens=100,
                importance=0.8,
            )
        ]

        pack = ContextPack(
            role="director",
            mode="chat",
            run_id="run-1",
            step=1,
            content="assembled content",
            sources=sources,
            total_tokens=100,
            total_chars=400,
            budget=budget,
        )

        assert pack.role == "director"
        assert pack.content == "assembled content"
        assert pack.total_tokens == 100
