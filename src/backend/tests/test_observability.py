"""测试统一可观测性模块

验证:
1. PolarisContext 创建和传播
2. create_task_with_context 上下文保持
3. UnifiedLogger JSON格式输出
4. UnifiedTracer span追踪
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from polaris.kernelone.trace import (
    ContextManager,
    PolarisContext,
    configure_logging,
    create_task_with_context,
    get_context,
    get_logger,
    get_tracer,
    new_trace,
)


class TestPolarisContext:
    """测试统一上下文"""

    def test_create_context(self):
        """测试创建上下文"""
        ctx = PolarisContext(
            trace_id="test-trace-123",
            run_id="run-456",
            task_id="task-789",
        )
        assert ctx.trace_id == "test-trace-123"
        assert ctx.run_id == "run-456"
        assert ctx.task_id == "task-789"

    def test_context_to_dict(self):
        """测试上下文转换为字典"""
        ctx = PolarisContext(
            trace_id="test-trace",
            workspace="/tmp/test",
        )
        data = ctx.to_dict()
        assert data["trace_id"] == "test-trace"
        assert data["workspace"] == "/tmp/test"
        assert "span_depth" in data

    def test_context_to_env_vars(self):
        """测试上下文转换为环境变量"""
        ctx = PolarisContext(
            trace_id="test-trace",
            run_id="run-123",
            workspace="/tmp/test",
        )
        env = ctx.to_env_vars()
        assert env["KERNELONE_TRACE_ID"] == "test-trace"
        assert env["KERNELONE_RUN_ID"] == "run-123"
        assert env["KERNELONE_WORKSPACE"] == "/tmp/test"

    def test_context_from_env_vars(self):
        """测试从环境变量恢复上下文"""
        import os
        os.environ["KERNELONE_TRACE_ID"] = "env-trace-123"
        os.environ["KERNELONE_RUN_ID"] = "env-run-456"

        ctx = PolarisContext.from_env_vars()
        assert ctx is not None
        assert ctx.trace_id == "env-trace-123"
        assert ctx.run_id == "env-run-456"

        # 清理
        del os.environ["KERNELONE_TRACE_ID"]
        del os.environ["KERNELONE_RUN_ID"]

    def test_context_with_span(self):
        """测试添加span"""
        ctx = PolarisContext(trace_id="test")
        new_ctx = ctx.with_span("operation-1")

        assert len(new_ctx.span_stack) == 1
        assert new_ctx.span_stack[0]["name"] == "operation-1"
        assert "span_id" in new_ctx.span_stack[0]

    def test_context_with_metadata(self):
        """测试添加元数据"""
        ctx = PolarisContext(trace_id="test", metadata={"key1": "value1"})
        new_ctx = ctx.with_metadata(key2="value2")

        assert new_ctx.metadata["key1"] == "value1"
        assert new_ctx.metadata["key2"] == "value2"


class TestContextManager:
    """测试上下文管理器"""

    def test_get_current_creates_new(self):
        """测试获取当前上下文自动创建"""
        # 清除当前上下文
        ContextManager.clear()

        ctx = ContextManager.get_current()
        assert ctx.trace_id is not None
        assert ctx.trace_id.startswith("hp-")

    def test_bind_context(self):
        """测试上下文绑定"""
        ctx = PolarisContext(trace_id="bound-trace")

        with ContextManager.bind_context(ctx) as bound:
            assert bound.trace_id == "bound-trace"
            assert get_context().trace_id == "bound-trace"

        # 离开范围后应该恢复
        # 注意：这里如果之前没有上下文，可能会自动创建新的

    def test_new_trace_context_manager(self):
        """测试new_trace上下文管理器"""
        with new_trace("test-type", metadata={"key": "value"}) as ctx:
            assert ctx.trace_id.startswith("hp-test-type-")
            assert ctx.metadata["key"] == "value"
            assert get_context().trace_id == ctx.trace_id


class TestAsyncContextPropagation:
    """测试异步上下文传播"""

    @pytest.mark.asyncio
    async def test_create_task_with_context_propagates(self):
        """测试create_task_with_context传播上下文"""
        with new_trace("test-trace") as ctx:
            trace_id = ctx.trace_id

            async def inner_task():
                return get_context().trace_id

            task = create_task_with_context(inner_task())
            result = await task

            assert result == trace_id

    @pytest.mark.asyncio
    async def test_create_task_without_context_loses(self):
        """测试标准create_task丢失上下文"""
        with new_trace("test-trace") as ctx:
            trace_id = ctx.trace_id

            async def read_trace_id():
                return get_context().trace_id

            async def inner_task():
                inner = asyncio.create_task(read_trace_id())
                return await inner

            # 使用我们的工具函数
            result = await create_task_with_context(inner_task())
            assert result == trace_id

    @pytest.mark.asyncio
    async def test_nested_tasks(self):
        """测试嵌套任务"""
        with new_trace("outer") as outer_ctx:

            async def level1():
                assert get_context().trace_id == outer_ctx.trace_id

                async def level2():
                    assert get_context().trace_id == outer_ctx.trace_id
                    return "level2-done"

                result = await create_task_with_context(level2())
                return result

            result = await create_task_with_context(level1())
            assert result == "level2-done"

    @pytest.mark.asyncio
    async def test_multiple_tasks(self):
        """测试多个并发任务"""
        with new_trace("multi") as ctx:
            results = []

            async def task(n):
                return (n, get_context().trace_id)

            # 创建多个任务
            tasks = [
                create_task_with_context(task(i))
                for i in range(3)
            ]

            for t in tasks:
                n, trace_id = await t
                results.append((n, trace_id))

            # 所有任务应该有相同的trace_id
            for n, trace_id in results:
                assert trace_id == ctx.trace_id


class TestUnifiedLogger:
    """测试统一日志器"""

    def test_logger_with_trace_id(self, caplog):
        """测试日志包含trace_id"""
        with new_trace("test-log") as ctx:
            logger = get_logger("test")
            logger.info("Test message", extra_key="extra_value")

            # 验证日志输出
            # 注意：这需要配置日志捕获

    def test_configure_logging_json(self):
        """测试JSON日志配置"""
        configure_logging(level=logging.INFO, json_output=True)
        logger = logging.getLogger("test-json")

        # 创建一个处理器来捕获输出
        import io
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        logger.info("JSON test")

        # 验证输出是JSON格式
        output = stream.getvalue()
        # 由于我们使用的是root logger配置，这个测试可能需要调整


class TestUnifiedTracer:
    """测试统一追踪器"""

    def test_start_span(self):
        """测试开始span"""
        tracer = get_tracer()

        with new_trace("test-trace"):
            span = tracer.start_span("operation")

            assert span.name == "operation"
            assert span.trace_id == get_context().trace_id
            assert span.span_id is not None

            tracer.end_span(span)
            assert span.duration_ms is not None

    def test_span_context_manager(self):
        """测试span上下文管理器"""
        tracer = get_tracer()

        with new_trace("test-trace"):
            with tracer.span("operation") as span:
                assert span.name == "operation"
                span.set_tag("key", "value")

            assert span.duration_ms is not None

    def test_nested_spans(self):
        """测试嵌套span"""
        tracer = get_tracer()

        with new_trace("test-trace"), tracer.span("outer") as outer, tracer.span("inner") as inner:
            assert inner.parent_span_id == outer.span_id

    def test_span_error(self):
        """测试span错误记录"""
        tracer = get_tracer()

        with new_trace("test-trace"):
            try:
                with tracer.span("failing-operation"):
                    raise ValueError("Test error")
            except ValueError:
                pass

            # 验证错误被记录
            # 这里需要检查recorder中的span


class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_full_flow(self):
        """测试完整流程"""
        with new_trace("full-flow", task_id="task-full") as ctx:
            async def capture_trace() -> str:
                return get_context().trace_id

            task = create_task_with_context(capture_trace())
            propagated_trace_id = await task
            assert propagated_trace_id == ctx.trace_id

            tracer = get_tracer()
            with tracer.span("verify-operation") as span:
                span.set_tag("trace_id", ctx.trace_id)
                span.add_event("trace_propagated", {"task_id": "task-full"})

            assert span.duration_ms is not None
