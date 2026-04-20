"""防御性测试：验证 executor.py 和 resilience.py 的修复效果

测试覆盖：
1. 全局信号量线程安全初始化
2. WorkspaceExecutorManager 混合锁问题修复
3. CancelledError 正确传播
4. CircuitBreaker TOCTOU 漏洞修复
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
import pytest

from polaris.kernelone.llm.engine.executor import (
    _get_global_semaphore,
    WorkspaceExecutorManager,
    AIExecutor,
    reset_executor_manager,
)
from polaris.kernelone.llm.engine.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    CircuitBreakerOpenError,
    retry_with_jitter,
)


class TestGlobalSemaphoreThreadSafety:
    """测试全局信号量的线程安全初始化"""

    def test_concurrent_initialization(self) -> None:
        """多个线程同时初始化信号量，应只创建一个实例"""
        results: list[asyncio.Semaphore] = []
        errors: list[Exception] = []

        async def get_semaphore() -> asyncio.Semaphore:
            return await _get_global_semaphore()

        def run_in_thread() -> None:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    sem = loop.run_until_complete(get_semaphore())
                    results.append(sem)
                finally:
                    loop.close()
            except Exception as e:
                errors.append(e)

        # 并发启动 10 个线程
        threads = [threading.Thread(target=run_in_thread) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # 验证：无错误，所有结果指向同一实例
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 10
        # 所有信号量应该是同一个实例
        first = results[0]
        for sem in results[1:]:
            assert sem is first, "Multiple semaphore instances created"

    @pytest.mark.asyncio
    async def test_semaphore_value_preserved(self) -> None:
        """信号量值应正确保留"""
        sem = await _get_global_semaphore()
        # 默认值是 100
        assert sem._value == 100, f"Expected 100, got {sem._value}"


class TestWorkspaceExecutorManagerLockConsistency:
    """测试 WorkspaceExecutorManager 使用统一锁"""

    def test_sync_and_async_paths_use_same_lock(self) -> None:
        """同步和异步路径应使用同一个 threading.Lock"""
        manager = WorkspaceExecutorManager()
        # 验证只有一个锁
        assert hasattr(manager, "_lock")
        assert isinstance(manager._lock, threading.Lock)
        # 不应存在 _sync_lock 或 asyncio.Lock
        assert not hasattr(manager, "_sync_lock")

    def test_concurrent_get_executor_sync(self) -> None:
        """多线程并发获取 executor 应安全"""
        manager = WorkspaceExecutorManager()
        executors: list[AIExecutor] = []
        errors: list[Exception] = []

        def get_executor() -> None:
            try:
                executor = manager.get_executor_sync("test_workspace")
                executors.append(executor)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_executor) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(executors) == 20
        # 所有 executor 应是同一实例
        first = executors[0]
        for ex in executors[1:]:
            assert ex is first

    @pytest.mark.asyncio
    async def test_async_path_calls_sync(self) -> None:
        """异步路径应调用同步方法"""
        manager = WorkspaceExecutorManager()
        executor1 = manager.get_executor_sync("async_test")
        executor2 = await manager.get_executor("async_test")
        # 应返回同一实例
        assert executor1 is executor2


class TestCancelledErrorPropagation:
    """测试 CancelledError 正确传播"""

    @pytest.mark.asyncio
    async def test_executor_invoke_propagates_cancelled(self) -> None:
        """AIExecutor.invoke 应传播 CancelledError"""
        # 重置 executor manager
        reset_executor_manager()
        
        from polaris.kernelone.llm.shared_contracts import AIRequest, TaskType
        
        executor = AIExecutor(workspace=".")
        request = AIRequest(
            task_type=TaskType.GENERATION,
            role="test_role",
            input="test prompt",
        )

        # 创建一个会被取消的任务
        async def invoke_and_cancel() -> Any:
            task = asyncio.create_task(executor.invoke(request))
            # 立即取消
            task.cancel()
            return await task

        with pytest.raises(asyncio.CancelledError):
            await invoke_and_cancel()

    @pytest.mark.asyncio
    async def test_circuit_breaker_propagates_cancelled(self) -> None:
        """CircuitBreaker.call 应传播 CancelledError"""
        breaker = CircuitBreaker(name="test_cancel")

        async def slow_func() -> str:
            await asyncio.sleep(10.0)
            return "done"

        async def call_and_cancel() -> Any:
            task = asyncio.create_task(breaker.call(slow_func))
            task.cancel()
            return await task

        with pytest.raises(asyncio.CancelledError):
            await call_and_cancel()

    @pytest.mark.asyncio
    async def test_retry_with_jitter_propagates_cancelled(self) -> None:
        """retry_with_jitter 应传播 CancelledError"""
        call_count = 0

        async def failing_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("temporary error")
            return "success"

        async def retry_and_cancel() -> Any:
            task = asyncio.create_task(
                retry_with_jitter(failing_func, max_retries=5, base_delay=0.1)
            )
            # 等待第一次失败后取消
            await asyncio.sleep(0.2)
            task.cancel()
            return await task

        with pytest.raises(asyncio.CancelledError):
            await retry_and_cancel()


class TestCircuitBreakerTOCTOUFix:
    """测试 CircuitBreaker TOCTOU 漏洞修复"""

    @pytest.mark.asyncio
    async def test_half_open_state_consistency(self) -> None:
        """HALF_OPEN 状态下并发调用应正确限制"""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=0.5,  # 增加恢复超时
            half_open_max_calls=3,
            success_threshold=3,  # 需要 3 次成功才能关闭
        )
        breaker = CircuitBreaker(name="toctou_test", config=config)

        # 先触发 OPEN 状态
        async def failing_func() -> str:
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                await breaker.call(failing_func)

        assert breaker.state == CircuitState.OPEN

        # 等待进入 HALF_OPEN（需要等待 recovery_timeout）
        await asyncio.sleep(0.6)
        
        # 检查状态是否已转换
        # 注意：状态转换发生在下一次 call() 时
        # 所以我们需要先触发一次调用来检查状态
        
        # 并发调用：只有 half_open_max_calls 个应该通过
        results: list[str] = []
        rejected: list[CircuitBreakerOpenError] = []

        async def success_func() -> str:
            await asyncio.sleep(0.1)  # 模拟延迟，让其他调用排队
            results.append("success")
            return "ok"

        async def try_call() -> None:
            try:
                await breaker.call(success_func)
            except CircuitBreakerOpenError as e:
                rejected.append(e)

        # 10 个并发调用，只有 half_open_max_calls (3) 个应该通过
        tasks = [asyncio.create_task(try_call()) for _ in range(10)]
        await asyncio.gather(*tasks, return_exceptions=True)

        # 只有 3 个调用应该成功（half_open_max_calls）
        assert len(results) <= 3, f"Expected <= 3 successes, got {len(results)}"
        # 至少 7 个应该被拒绝
        assert len(rejected) >= 7, f"Expected >= 7 rejections, got {len(rejected)}"

    @pytest.mark.asyncio
    async def test_state_transition_atomicity(self) -> None:
        """状态转换应原子执行"""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.05,
            half_open_max_calls=1,
            success_threshold=1,
        )
        breaker = CircuitBreaker(name="atomic_test", config=config)

        # 触发 OPEN
        async def fail() -> None:
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await breaker.call(fail)

        assert breaker.state == CircuitState.OPEN

        # 等待进入 HALF_OPEN
        await asyncio.sleep(0.1)

        # 并发调用：一个成功，一个失败
        async def success() -> str:
            return "ok"

        async def fail_again() -> None:
            raise ValueError("fail again")

        # 先让一个成功
        result = await breaker.call(success)
        assert result == "ok"

        # 状态应变为 CLOSED
        assert breaker.state == CircuitState.CLOSED


class TestExceptionHandlingSpecificity:
    """测试异常处理的具体性"""

    @pytest.mark.asyncio
    async def test_circuit_breaker_does_not_swallow_specific_errors(self) -> None:
        """CircuitBreaker 应正确传播特定异常"""
        breaker = CircuitBreaker(name="specific_test")

        class SpecificError(Exception):
            pass

        async def raise_specific() -> None:
            raise SpecificError("specific error")

        with pytest.raises(SpecificError):
            await breaker.call(raise_specific)

    @pytest.mark.asyncio
    async def test_retry_does_not_swallow_non_retryable(self) -> None:
        """retry_with_jitter 应立即传播非可重试错误"""
        call_count = 0

        async def raise_401() -> None:
            nonlocal call_count
            call_count += 1
            err = Exception("401 Unauthorized")
            err.status_code = 401  # type: ignore[attr-defined]
            raise err

        # 401 不应重试
        with pytest.raises(Exception):
            await retry_with_jitter(raise_401, max_retries=5)

        # 应只调用一次
        assert call_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])