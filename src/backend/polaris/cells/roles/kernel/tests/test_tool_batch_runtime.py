"""
Tests for Tool Batch Runtime

验证：
1. 并行执行只读工具
2. 串行执行写工具
3. 异步工具返回pending
4. 超时处理
5. 错误处理
6. 工具分类
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.tool_batch_runtime import (
    ToolBatchRuntime,
    ToolExecutionContext,
    ToolExecutionMode,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    ToolBatch,
    ToolCallId,
    ToolInvocation,
    TurnId,
)

# ============ Fixtures ============


@pytest.fixture
def mock_executor():
    """Mock tool executor"""
    executor = AsyncMock()
    return executor


@pytest.fixture
def runtime(mock_executor):
    """Create runtime with mock executor"""
    context = ToolExecutionContext(workspace="/test", timeout_ms=5000)
    return ToolBatchRuntime(executor=mock_executor, context=context)


@pytest.fixture
def sample_batch():
    """Sample tool batch"""
    read1 = ToolInvocation(
        call_id=ToolCallId("call_1"),
        tool_name="read_file",
        arguments={"path": "a.txt"},
        effect_type="read",
        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
    )
    read2 = ToolInvocation(
        call_id=ToolCallId("call_2"),
        tool_name="read_file",
        arguments={"path": "b.txt"},
        effect_type="read",
        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
    )
    write1 = ToolInvocation(
        call_id=ToolCallId("call_3"),
        tool_name="write_file",
        arguments={"path": "out.txt", "content": "data"},
        effect_type="write",
        execution_mode=ToolExecutionMode.WRITE_SERIAL,
    )
    return ToolBatch(
        batch_id=BatchId("test_batch"),
        invocations=[read1, read2, write1],
        parallel_readonly=[read1, read2],
        serial_writes=[write1],
        async_receipts=[],
    )


# ============ Test Parallel Execution ============


class TestParallelExecution:
    """测试并行执行"""

    @pytest.mark.asyncio
    async def test_parallel_readonly_tools_execute_concurrently(self, runtime, mock_executor) -> None:
        """只读工具并行执行"""

        # 模拟慢速工具
        async def slow_executor(tool_name, arguments):
            await asyncio.sleep(0.1)
            return {"success": True, "result": f"content of {arguments.get('path')}"}

        runtime.executor = slow_executor

        p1 = ToolInvocation(
            call_id=ToolCallId("p1"),
            tool_name="read_file",
            arguments={"path": "a.txt"},
            effect_type="read",
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        p2 = ToolInvocation(
            call_id=ToolCallId("p2"),
            tool_name="read_file",
            arguments={"path": "b.txt"},
            effect_type="read",
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        p3 = ToolInvocation(
            call_id=ToolCallId("p3"),
            tool_name="read_file",
            arguments={"path": "c.txt"},
            effect_type="read",
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        batch = ToolBatch(
            batch_id=BatchId("parallel_batch"),
            invocations=[p1, p2, p3],
            parallel_readonly=[p1, p2, p3],
            serial_writes=[],
            async_receipts=[],
        )

        import time

        start = time.time()
        receipts = await runtime.execute_batch(batch, TurnId("turn_1"))
        elapsed = time.time() - start

        # 3个100ms工具并行应该只需要~100ms，不是300ms
        assert elapsed < 0.2, f"Parallel execution took {elapsed}s, expected < 0.2s"
        assert len(receipts) == 3

        # 所有只读工具成功
        for receipt in receipts:
            assert receipt["success_count"] == 1
            assert receipt["failure_count"] == 0


# ============ Test Serial Execution ============


class TestSerialExecution:
    """测试串行执行"""

    @pytest.mark.asyncio
    async def test_write_tools_execute_serially(self, runtime, mock_executor) -> None:
        """写工具串行执行"""
        execution_order = []

        async def tracking_executor(tool_name, arguments):
            execution_order.append(tool_name)
            await asyncio.sleep(0.05)
            return {"success": True, "result": "done", "effect_receipt": {"tool": tool_name}}

        runtime.executor = tracking_executor

        w1 = ToolInvocation(
            call_id=ToolCallId("w1"),
            tool_name="write_file",
            arguments={"path": "a.txt", "content": "a"},
            effect_type="write",
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        w2 = ToolInvocation(
            call_id=ToolCallId("w2"),
            tool_name="write_file",
            arguments={"path": "b.txt", "content": "b"},
            effect_type="write",
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        w3 = ToolInvocation(
            call_id=ToolCallId("w3"),
            tool_name="write_file",
            arguments={"path": "c.txt", "content": "c"},
            effect_type="write",
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        batch = ToolBatch(
            batch_id=BatchId("serial_batch"),
            invocations=[w1, w2, w3],
            parallel_readonly=[],
            serial_writes=[w1, w2, w3],
            async_receipts=[],
        )

        await runtime.execute_batch(batch, TurnId("turn_2"))

        # 验证顺序执行
        assert execution_order == ["write_file", "write_file", "write_file"]


# ============ Test Async Tools ============


class TestAsyncTools:
    """测试异步工具"""

    @pytest.mark.asyncio
    async def test_async_tool_returns_pending_receipt(self, runtime, mock_executor) -> None:
        """异步工具返回pending receipt"""
        async_inv = ToolInvocation(
            call_id=ToolCallId("async_1"),
            tool_name="create_pull_request",
            arguments={"title": "PR"},
            effect_type="async",
            execution_mode=ToolExecutionMode.ASYNC_RECEIPT,
        )
        batch = ToolBatch(
            batch_id=BatchId("async_batch"),
            invocations=[async_inv],
            parallel_readonly=[],
            serial_writes=[],
            async_receipts=[async_inv],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_async"))

        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt["pending_async_count"] == 1
        assert receipt["has_pending_async"] is True
        assert receipt["results"][0]["status"] == "pending"


# ============ Test Error Handling ============


class TestErrorHandling:
    """测试错误处理"""

    @pytest.mark.asyncio
    async def test_tool_error_returns_error_receipt(self, runtime, mock_executor) -> None:
        """工具错误返回错误receipt"""
        mock_executor.side_effect = Exception("File not found")

        err_inv = ToolInvocation(
            call_id=ToolCallId("err_1"),
            tool_name="read_file",
            arguments={"path": "missing.txt"},
            effect_type="read",
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        batch = ToolBatch(
            batch_id=BatchId("error_batch"),
            invocations=[err_inv],
            parallel_readonly=[err_inv],
            serial_writes=[],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_error"))

        assert len(receipts) == 1
        assert receipts[0]["failure_count"] == 1
        assert receipts[0]["success_count"] == 0

    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_status(self, runtime, mock_executor) -> None:
        """超时返回timeout状态"""

        # 模拟超时
        async def slow_tool(tool_name, arguments):
            await asyncio.sleep(10)  # 超过5秒超时
            return {"success": True, "result": "done"}

        runtime.executor = slow_tool
        runtime.context.timeout_ms = 100  # 100ms超时

        slow_inv = ToolInvocation(
            call_id=ToolCallId("slow_1"),
            tool_name="grep",
            arguments={"pattern": "test"},
            effect_type="read",
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        batch = ToolBatch(
            batch_id=BatchId("timeout_batch"),
            invocations=[slow_inv],
            parallel_readonly=[slow_inv],
            serial_writes=[],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_timeout"))

        assert receipts[0]["results"][0]["status"] == "timeout"


# ============ Test Tool Classification ============


class TestToolClassification:
    """测试工具分类"""

    def test_readonly_tools_classified_correctly(self) -> None:
        """只读工具正确分类"""
        readonly_tools = ["read_file", "list_directory", "grep", "search_code"]
        for tool in readonly_tools:
            mode = ToolBatchRuntime.classify_tool(tool)
            assert mode == ToolExecutionMode.READONLY_PARALLEL, f"{tool} should be READONLY_PARALLEL"

    def test_write_tools_classified_correctly(self) -> None:
        """写工具正确分类"""
        write_tools = ["write_file", "edit_file", "delete_file", "bash"]
        for tool in write_tools:
            mode = ToolBatchRuntime.classify_tool(tool)
            assert mode == ToolExecutionMode.WRITE_SERIAL, f"{tool} should be WRITE_SERIAL"

    def test_async_tools_classified_correctly(self) -> None:
        """异步工具正确分类"""
        async_tools = ["create_pull_request", "deploy", "trigger_ci"]
        for tool in async_tools:
            mode = ToolBatchRuntime.classify_tool(tool)
            assert mode == ToolExecutionMode.ASYNC_RECEIPT, f"{tool} should be ASYNC_RECEIPT"

    def test_unknown_tools_default_to_write_serial(self) -> None:
        """未知工具默认WRITE_SERIAL（安全优先）"""
        mode = ToolBatchRuntime.classify_tool("unknown_custom_tool")
        assert mode == ToolExecutionMode.WRITE_SERIAL

    def test_classify_batch_groups_correctly(self) -> None:
        """批次分类正确分组"""
        invocations = [
            ToolInvocation(
                call_id=ToolCallId("c1"),
                tool_name="read_file",
                arguments={},
                effect_type="read",
                execution_mode=ToolExecutionMode.READONLY_PARALLEL,
            ),
            ToolInvocation(
                call_id=ToolCallId("c2"),
                tool_name="write_file",
                arguments={},
                effect_type="write",
                execution_mode=ToolExecutionMode.WRITE_SERIAL,
            ),
            ToolInvocation(
                call_id=ToolCallId("c3"),
                tool_name="create_pull_request",
                arguments={},
                effect_type="async",
                execution_mode=ToolExecutionMode.ASYNC_RECEIPT,
            ),
            ToolInvocation(
                call_id=ToolCallId("c4"),
                tool_name="grep",
                arguments={},
                effect_type="read",
                execution_mode=ToolExecutionMode.READONLY_PARALLEL,
            ),
        ]

        classified = ToolBatchRuntime.classify_batch(invocations)

        assert len(classified["parallel_readonly"]) == 2  # read_file, grep
        assert len(classified["serial_writes"]) == 1  # write_file
        assert len(classified["async_receipts"]) == 1  # create_pull_request


# ============ Test Mixed Batch ============


class TestMixedBatch:
    """测试混合批次"""

    @pytest.mark.asyncio
    async def test_mixed_batch_executes_correctly(self, runtime, mock_executor) -> None:
        """混合批次正确执行"""

        async def mixed_executor(tool_name, arguments):
            if tool_name == "write_file":
                return {"success": True, "result": "done", "effect_receipt": {"bytes_written": 1}}
            return {"success": True, "result": "done"}

        mock_executor.side_effect = mixed_executor

        r1 = ToolInvocation(
            call_id=ToolCallId("r1"),
            tool_name="read_file",
            arguments={"path": "a.txt"},
            effect_type="read",
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        w1 = ToolInvocation(
            call_id=ToolCallId("w1"),
            tool_name="write_file",
            arguments={"path": "out.txt", "content": "x"},
            effect_type="write",
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        batch = ToolBatch(
            batch_id=BatchId("mixed_batch"),
            invocations=[r1, w1],
            parallel_readonly=[r1],
            serial_writes=[w1],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_mixed"))

        # 2个工具，2个receipts
        assert len(receipts) == 2

        # 只读成功
        assert receipts[0]["success_count"] == 1
        # 写成功
        assert receipts[1]["success_count"] == 1

        # 总调用次数
        assert mock_executor.call_count == 2

    @pytest.mark.asyncio
    async def test_nested_effect_receipt_is_promoted(self, runtime) -> None:
        """嵌套在 result 内的 effect_receipt 也应被识别。"""

        async def nested_receipt_executor(tool_name, arguments):
            return {
                "ok": True,
                "result": {
                    "message": "done",
                    "effect_receipt": {"operation": "modify", "file": arguments.get("path", "")},
                },
            }

        runtime.executor = nested_receipt_executor

        write_inv = ToolInvocation(
            call_id=ToolCallId("w_nested"),
            tool_name="write_file",
            arguments={"path": "nested.txt", "content": "x"},
            effect_type="write",
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        batch = ToolBatch(
            batch_id=BatchId("nested_receipt_batch"),
            invocations=[write_inv],
            parallel_readonly=[],
            serial_writes=[write_inv],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_nested_receipt"))
        assert receipts[0]["success_count"] == 1
        assert receipts[0]["results"][0]["effect_receipt"] == {"operation": "modify", "file": "nested.txt"}

    @pytest.mark.asyncio
    async def test_ok_false_result_maps_to_error_status(self, runtime) -> None:
        """仅返回 ok=false 的结果应被记为 error，而不是 success。"""

        async def failing_executor(tool_name, arguments):
            return {"ok": False, "error": "command failed", "result": {"detail": "boom"}}

        runtime.executor = failing_executor

        read_inv = ToolInvocation(
            call_id=ToolCallId("r_fail"),
            tool_name="read_file",
            arguments={"path": "missing.txt"},
            effect_type="read",
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        batch = ToolBatch(
            batch_id=BatchId("ok_false_batch"),
            invocations=[read_inv],
            parallel_readonly=[read_inv],
            serial_writes=[],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_ok_false"))
        assert receipts[0]["failure_count"] == 1
        assert receipts[0]["results"][0]["status"] == "error"
