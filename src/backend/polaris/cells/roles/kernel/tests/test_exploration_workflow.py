"""
Tests for Exploration Workflow

验证：
1. 接收HANDOFF_WORKFLOW决策
2. 执行初始工具批次
3. 探索策略
4. 超时处理
5. 结果综合
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.exploration_workflow import (
    ExplorationStatus,
    ExplorationWorkflow,
    ExplorationWorkflowRuntime,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.domain.cognitive_runtime.models import ContextHandoffPack

# ============ Fixtures ============


@pytest.fixture
def mock_executor():
    """Mock tool executor"""
    executor = AsyncMock()
    return executor


@pytest.fixture
def mock_synthesis_llm():
    """Mock synthesis LLM"""
    llm = AsyncMock()
    llm.return_value = "Synthesis: Found key patterns in codebase."
    return llm


@pytest.fixture
def workflow(mock_executor, mock_synthesis_llm):
    """Create workflow with mocks"""
    return ExplorationWorkflow(
        tool_executor=mock_executor, synthesis_llm=mock_synthesis_llm, max_steps=5, timeout_ms=10000
    )


@pytest.fixture
def handoff_decision():
    """Sample HANDOFF_WORKFLOW decision"""
    return TurnDecision(
        turn_id=TurnId("turn_1"),
        kind=TurnDecisionKind.HANDOFF_WORKFLOW,
        visible_message="Exploring codebase...",
        reasoning_summary="Complex exploration needed",
        tool_batch=ToolBatch(
            batch_id=BatchId("handoff_batch"),
            invocations=[
                ToolInvocation(
                    call_id=ToolCallId("call_1"),
                    tool_name="read_file",
                    arguments={"path": "main.py"},
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                ),
                ToolInvocation(
                    call_id=ToolCallId("call_2"),
                    tool_name="list_directory",
                    arguments={"path": "."},
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                ),
            ],
            parallel_readonly=[],
            serial_writes=[],
            async_receipts=[],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="document",
        metadata={
            "handoff_reason": "complex_exploration",
            "tool_count": 2,
            "initial_tools": ["read_file", "list_directory"],
        },
    )


# ============ Test Basic Execution ============


class TestBasicExecution:
    """测试基本执行"""

    @pytest.mark.asyncio
    async def test_receives_handoff_decision(self, workflow, mock_executor, handoff_decision) -> None:
        """接收HANDOFF_WORKFLOW决策"""
        mock_executor.return_value = {"success": True, "result": "file content"}

        result = await workflow.execute(handoff_decision, TurnId("turn_explore"))

        assert result.status == ExplorationStatus.COMPLETED
        assert result.turn_id == "turn_explore"

    @pytest.mark.asyncio
    async def test_executes_initial_tools(self, workflow, mock_executor, handoff_decision) -> None:
        """执行初始工具批次"""
        mock_executor.return_value = {"success": True, "result": "content"}

        result = await workflow.execute(handoff_decision, TurnId("turn_1"))

        # 初始2个工具被执行
        assert mock_executor.call_count == 2
        assert result.steps_completed == 2

    @pytest.mark.asyncio
    async def test_caches_discoveries(self, workflow, mock_executor, handoff_decision) -> None:
        """缓存探索发现"""
        mock_executor.return_value = {"success": True, "result": "def main(): pass"}

        await workflow.execute(handoff_decision, TurnId("turn_cache"))

        assert "main.py" in workflow._discovery_cache
        assert "def main(): pass" in workflow._discovery_cache["main.py"]


# ============ Test Async Handoff ============


class TestAsyncHandoff:
    """测试异步操作移交"""

    @pytest.mark.asyncio
    async def test_async_operation_single_batch(self, workflow, mock_executor) -> None:
        """异步操作使用单批次策略"""
        decision = TurnDecision(
            turn_id=TurnId("turn_async"),
            kind=TurnDecisionKind.HANDOFF_WORKFLOW,
            visible_message="Creating PR...",
            reasoning_summary="",
            tool_batch=ToolBatch(
                batch_id=BatchId("async_batch"),
                invocations=[
                    ToolInvocation(
                        call_id=ToolCallId("async_1"),
                        tool_name="create_pull_request",
                        arguments={"title": "PR"},
                        effect_type=ToolEffectType.ASYNC,
                        execution_mode=ToolExecutionMode.ASYNC_RECEIPT,
                    )
                ],
                parallel_readonly=[],
                serial_writes=[],
                async_receipts=[],
            ),
            finalize_mode=FinalizeMode.NONE,
            domain="document",
            metadata={"handoff_reason": "async_operation", "tool_count": 1},
        )

        mock_executor.return_value = {"success": True, "result": "PR created"}

        result = await workflow.execute(decision, TurnId("turn_async"))

        assert result.status == ExplorationStatus.COMPLETED
        assert result.steps_completed == 1
        assert mock_executor.call_count == 1


# ============ Test Strategy ============


class TestStrategies:
    """测试探索策略"""

    @pytest.mark.asyncio
    async def test_breadth_first_strategy(self, mock_executor, mock_synthesis_llm) -> None:
        """广度优先策略"""
        workflow = ExplorationWorkflow(tool_executor=mock_executor, synthesis_llm=mock_synthesis_llm, max_steps=10)

        decision = TurnDecision(
            turn_id=TurnId("turn_bfs"),
            kind=TurnDecisionKind.HANDOFF_WORKFLOW,
            visible_message="",
            reasoning_summary="",
            tool_batch=ToolBatch(
                batch_id=BatchId("bfs_batch"),
                invocations=[
                    ToolInvocation(
                        call_id=ToolCallId("bfs_1"),
                        tool_name="read_file",
                        arguments={"path": "a.py"},
                        effect_type=ToolEffectType.READ,
                        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                    )
                ],
                parallel_readonly=[],
                serial_writes=[],
                async_receipts=[],
            ),
            finalize_mode=FinalizeMode.NONE,
            domain="document",
            metadata={"handoff_reason": "complex_exploration"},
        )

        mock_executor.return_value = {"success": True, "result": "import b"}

        result = await workflow.execute(decision, TurnId("turn_bfs"))

        assert result.status == ExplorationStatus.COMPLETED


# ============ Test Error Handling ============


class TestErrorHandling:
    """测试错误处理"""

    @pytest.mark.asyncio
    async def test_tool_failure_continues(self, workflow, mock_executor, handoff_decision) -> None:
        """工具失败后继续执行"""
        # 第一个成功，第二个失败
        mock_executor.side_effect = [{"success": True, "result": "content 1"}, Exception("File not found")]

        result = await workflow.execute(handoff_decision, TurnId("turn_fail"))

        # 仍然完成探索
        assert result.status == ExplorationStatus.COMPLETED
        assert result.steps_completed == 2

    @pytest.mark.asyncio
    async def test_timeout_handling(self, mock_executor, mock_synthesis_llm) -> None:
        """超时处理 - 单工具超时"""
        workflow = ExplorationWorkflow(
            tool_executor=mock_executor,
            synthesis_llm=mock_synthesis_llm,
            timeout_ms=10000,  # workflow超时较长
        )

        # 模拟工具超时
        mock_executor.side_effect = asyncio.TimeoutError("Tool timed out")

        decision = TurnDecision(
            turn_id=TurnId("turn_timeout"),
            kind=TurnDecisionKind.HANDOFF_WORKFLOW,
            visible_message="",
            reasoning_summary="",
            tool_batch=ToolBatch(
                batch_id=BatchId("timeout_batch"),
                invocations=[
                    ToolInvocation(
                        call_id=ToolCallId("slow_1"),
                        tool_name="grep",
                        arguments={"pattern": "test"},
                        effect_type=ToolEffectType.READ,
                        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                    )
                ],
                parallel_readonly=[],
                serial_writes=[],
                async_receipts=[],
            ),
            finalize_mode=FinalizeMode.NONE,
            domain="document",
            metadata={"handoff_reason": "complex_exploration"},
        )

        result = await workflow.execute(decision, TurnId("turn_timeout"))

        # 工具超时但探索完成（有容错）
        assert result.status in [ExplorationStatus.COMPLETED, ExplorationStatus.FAILED]


# ============ Test Synthesis ============


class TestSynthesis:
    """测试结果综合"""

    @pytest.mark.asyncio
    async def test_synthesis_called(self, workflow, mock_executor, mock_synthesis_llm, handoff_decision) -> None:
        """综合LLM被调用"""
        mock_executor.return_value = {"success": True, "result": "content"}

        result = await workflow.execute(handoff_decision, TurnId("turn_synth"))

        # 综合LLM被调用
        assert mock_synthesis_llm.call_count == 1
        assert result.synthesis is not None

    @pytest.mark.asyncio
    async def test_fallback_synthesis_without_llm(self, mock_executor, handoff_decision) -> None:
        """没有综合LLM时生成兜底摘要"""
        workflow = ExplorationWorkflow(
            tool_executor=mock_executor,
            synthesis_llm=None,  # 没有综合LLM
        )

        mock_executor.return_value = {"success": True, "result": "content"}

        result = await workflow.execute(handoff_decision, TurnId("turn_no_llm"))

        assert result.synthesis is not None
        assert "完成" in result.synthesis
        assert "read_file" in result.synthesis or "列出目录" in result.synthesis


# ============ Test Discovery Cache ============


class TestDiscoveryCache:
    """测试发现缓存"""

    @pytest.mark.asyncio
    async def test_import_extraction(self, workflow, mock_executor, handoff_decision) -> None:
        """提取imports"""
        mock_executor.return_value = {"success": True, "result": "import os\nimport sys\nfrom typing import List"}

        await workflow.execute(handoff_decision, TurnId("turn_imports"))

        # 应该提取到imports
        imports = workflow._extract_imports()
        assert "os" in imports
        assert "sys" in imports


# ============ Test Plan Creation ============


class TestPlanCreation:
    """测试计划创建"""

    def test_async_strategy_detection(self, workflow) -> None:
        """异步操作检测"""
        decision = TurnDecision(
            turn_id=TurnId("turn"),
            kind=TurnDecisionKind.HANDOFF_WORKFLOW,
            visible_message="",
            reasoning_summary="",
            tool_batch=ToolBatch(
                batch_id=BatchId("batch"), invocations=[], parallel_readonly=[], serial_writes=[], async_receipts=[]
            ),
            finalize_mode=FinalizeMode.NONE,
            domain="document",
            metadata={"handoff_reason": "async_operation"},
        )

        plan = workflow._create_plan(decision)
        assert plan.strategy == "single_batch"

    def test_many_tools_strategy_detection(self, workflow) -> None:
        """大量工具检测"""
        decision = TurnDecision(
            turn_id=TurnId("turn"),
            kind=TurnDecisionKind.HANDOFF_WORKFLOW,
            visible_message="",
            reasoning_summary="",
            tool_batch=ToolBatch(
                batch_id=BatchId("batch"), invocations=[], parallel_readonly=[], serial_writes=[], async_receipts=[]
            ),
            finalize_mode=FinalizeMode.NONE,
            domain="document",
            metadata={"handoff_reason": "too_many_tools"},
        )

        plan = workflow._create_plan(decision)
        assert plan.strategy == "adaptive"


# ============ Test ExplorationWorkflowRuntime Features ============


class TestCheckpointResume:
    """测试 checkpoint / resume"""

    def test_checkpoint_captures_state(self, mock_executor) -> None:
        runtime = ExplorationWorkflowRuntime(tool_executor=mock_executor)
        runtime._explored_paths = {"a.py", "b.py"}
        runtime._discovery_cache = {"a.py": "content"}
        runtime._ledger.append({"phase": "test"})

        cp = runtime.checkpoint()
        assert cp["ledger"] == [{"phase": "test"}]
        assert set(cp["explored_paths"]) == {"a.py", "b.py"}
        assert cp["discovery_cache_keys"] == ["a.py"]

    def test_resume_restores_state(self, mock_executor) -> None:
        runtime = ExplorationWorkflowRuntime(tool_executor=mock_executor)
        cp = {
            "ledger": [{"phase": "execute"}],
            "explored_paths": ["x.py"],
            "discovery_cache_keys": ["x.py"],
            "handoff_context": {"reason": "test"},
        }
        runtime.resume(cp)

        assert runtime._ledger == [{"phase": "execute"}, {"phase": "resume", "checkpoint_size": 1}]
        assert runtime._explored_paths == {"x.py"}
        assert list(runtime._discovery_cache.keys()) == ["x.py"]
        assert runtime._handoff_context == {"reason": "test"}


class TestHandoffEntry:
    """测试从 ContextHandoffPack 进入"""

    def test_enter_from_handoff_constructs_decision(self, mock_executor) -> None:
        runtime = ExplorationWorkflowRuntime(tool_executor=mock_executor)
        pack = ContextHandoffPack(
            handoff_id="handoff_123",
            workspace="/workspace",
            created_at="2026-04-16T00:00:00Z",
            session_id="session_1",
            reason="complex_exploration",
            current_goal="Refactor kernel",
            run_card={"priority": "high"},
            receipt_refs=("receipt_1", "receipt_2"),
        )

        decision = runtime.enter_from_handoff(pack)

        assert decision["kind"] == TurnDecisionKind.HANDOFF_WORKFLOW
        assert decision["visible_message"] == "Refactor kernel"
        assert decision["metadata"]["handoff_reason"] == "complex_exploration"
        assert decision["metadata"]["current_goal"] == "Refactor kernel"
        assert decision["metadata"]["receipt_refs"] == ["receipt_1", "receipt_2"]
        assert runtime._handoff_context["current_goal"] == "Refactor kernel"

    @pytest.mark.asyncio
    async def test_handoff_entry_then_execute(self, mock_executor) -> None:
        runtime = ExplorationWorkflowRuntime(tool_executor=mock_executor)
        pack = ContextHandoffPack(
            handoff_id="handoff_123",
            workspace="/workspace",
            created_at="2026-04-16T00:00:00Z",
            session_id="session_1",
            reason="async_operation",
            current_goal="Create PR",
        )

        decision = runtime.enter_from_handoff(pack)
        mock_executor.return_value = {"success": True, "result": "done"}

        result = await runtime.execute(decision, TurnId("turn_after_handoff"))
        assert result.status == ExplorationStatus.COMPLETED
