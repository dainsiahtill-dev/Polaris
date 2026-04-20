# KernelOne Runtime 重构蓝图

> **文档版本**: 1.0.0  
> **创建日期**: 2026-03-27  
> **目标目录**: `polaris/kernelone/runtime/`  
> **状态**: 待实施

---

## 1. 背景与目标

### 1.1 审计结论

经过 10 人高级 Python 团队对 `polaris/kernelone/runtime/` 的全面审计，发现以下核心问题：

| 类别 | 问题数 | 严重程度 |
|------|--------|----------|
| 业务语义入侵 | 3 | P0 |
| 资源泄漏 (信号量/内存) | 4 | P0-P1 |
| 消息可靠性 | 3 | P0-P1 |
| 进程终止 | 2 | P1 |
| 代码质量 | 8 | P2-P3 |

**综合评分**: 6.5/10

### 1.2 重构目标

1. **清除业务语义入侵**: KernelOne 层不得包含 Polaris 业务路径和语义
2. **消除资源泄漏**: 修复信号量、内存、进程的泄漏风险
3. **增强可靠性**: 消息队列死信机制、进程超时强制终止
4. **提升可维护性**: 拆分职责混乱模块、标准化异常处理
5. **完善可观测性**: 添加 span、审计日志、健康检查

---

## 2. 问题清单与修复计划

### 2.1 P0 问题 (立即修复)

#### P0-1: 执行引擎信号量泄漏

| 属性 | 值 |
|------|-----|
| **文件** | `execution_runtime.py` |
| **位置** | `submit_process()` 方法 |
| **根因** | 异常路径中 `_mark_failed()` 可能抛异常，导致信号量不释放 |
| **修复类型** | Bug Fix |
| **影响范围** | 所有子进程执行路径 |

**修复方案**:
```python
# 使用 try-finally 确保信号量释放
async def submit_process(
    self,
    command: str,
    *,
    timeout: float | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    name: str = "",
) -> ExecutionHandle:
    state = self._create_state(execution_id, ExecutionLane.SUBPROCESS, name)
    await self._process_semaphore.acquire()
    try:
        self._register_state(state)
        handle = await self._spawn_process(
            state, command, timeout=timeout, cwd=cwd, env=env
        )
        state.process_handle = handle
        state.status = ExecutionStatus.RUNNING
        state.started_at = _utc_now()
        return ExecutionHandle(...)
    except Exception as exc:
        self._mark_failed(state, exc)
        raise
    finally:
        # 只有终态才释放信号量
        if state.status.terminal:
            self._process_semaphore.release()
```

#### P0-2: 消息队列丢消息无告警

| 属性 | 值 |
|------|-----|
| **文件** | `message_bus.py` |
| **位置** | `publish()` 方法 `QueueFull` 处理 |
| **根因** | 队列满时静默丢弃消息 |
| **修复类型** | Bug Fix + Enhancement |
| **影响范围** | 所有异步消息传递 |

**修复方案**:
```python
from collections import deque
from dataclasses import dataclass, field
from typing import Any
import asyncio
import time

@dataclass
class DeadLetterMessage:
    message: Message
    reason: str
    timestamp: float = field(default_factory=time.time)

class MessageBus:
    def __init__(self, max_history: int = 1000):
        # ... existing init ...
        self._dead_letters: deque[DeadLetterMessage] = deque(maxlen=1000)
        self._dropped_messages_total: int = 0
    
    async def publish(self, message: Message) -> None:
        """发布消息到总线。
        
        Args:
            message: 要发布的消息
            
        Raises:
            MessageBusError: 当消息格式无效时
        """
        if not message.recipient and not message.message_type.broadcast:
            raise MessageBusError(f"Invalid message: no recipient for {message.message_type}")
        
        self._history.append(message)
        
        direct_queue = self._direct_queues.get(message.recipient)
        if direct_queue:
            try:
                direct_queue.put_nowait(message)
            except asyncio.QueueFull:
                self._dropped_messages_total += 1
                self._dead_letters.append(
                    DeadLetterMessage(
                        message=message,
                        reason=f"queue_full:{direct_queue.maxsize}"
                    )
                )
                _logger.warning(
                    "Message dropped: recipient=%s type=%s queue_size=%d "
                    "total_dropped=%d",
                    message.recipient,
                    message.message_type,
                    direct_queue.maxsize,
                    self._dropped_messages_total,
                )
            return
        
        # ... broadcast handling ...
```

#### P0-3: 环境变量危险变量未过滤

| 属性 | 值 |
|------|-----|
| **文件** | `command_executor.py` |
| **位置** | `_build_env()` 方法 |
| **根因** | `env_policy="inherit"` 继承所有环境变量包括危险变量 |
| **修复类型** | Security Fix |
| **影响范围** | 所有子进程执行 |

**修复方案**:
```python
# 危险环境变量白名单
DANGEROUS_ENV_VARS: frozenset[str] = frozenset({
    # 链接器/加载器
    "LD_PRELOAD", "LD_AUDIT", "LD_DEBUG", "LD_LIBRARY_PATH",
    "LD_ORIGIN", "LD_PROFILE", "LD_SHOW_AUXV",
    # Python 环境
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
    "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONCOERCECLOCALE",
    # 其他解释器
    "RUST_BACKTRACE", "RUST_LOG", "RUST_MIN_STACK",
    "NODE_OPTIONS", "NODE_PATH", "NODE_TLS_REJECT_UNAUTHORIZED",
    "JAVA_HOME", "CLASSPATH",
    # Shell 内建
    "BASH_FUNC_*", "PS1", "PS2",
})

class CommandExecutionService:
    def _build_env(
        self,
        env_policy: str,
        env_overrides: dict[str, str] | None,
    ) -> dict[str, str]:
        """构建进程环境变量。
        
        Args:
            env_policy: 环境策略 ("clean", "inherit", "minimal")
            env_overrides: 环境变量覆盖
            
        Returns:
            安全的环境变量字典
            
        Note:
            "inherit" 模式会自动过滤危险变量
        """
        policy = str(env_policy or "minimal").strip().lower()
        
        if policy == "clean":
            base: dict[str, str] = {}
        elif policy == "inherit":
            base = dict(os.environ)
            # 过滤危险变量
            for var in DANGEROUS_ENV_VARS:
                base.pop(var, None)
                # 处理通配符模式
                if var.endswith("_*"):
                    prefix = var[:-1]
                    base = {k: v for k, v in base.items() if not k.startswith(prefix)}
        else:  # minimal
            base = {}
        
        # 添加安全默认值
        base.setdefault("PYTHONUTF8", "1")
        base.setdefault("PYTHONIOENCODING", "utf-8")
        
        # 应用覆盖
        if env_overrides:
            base.update(env_overrides)
        
        return base
```

---

### 2.2 P1 问题 (高优先级)

#### P1-1: 超时后进程不强制终止

| 属性 | 值 |
|------|-----|
| **文件** | `execution_runtime.py` |
| **位置** | `_await_process_completion()` |
| **根因** | `terminate()` 超时不触发 `kill()` |
| **修复类型** | Bug Fix |

**修复方案**:
```python
async def _await_process_completion(
    self,
    state: _ExecutionState,
    handle: PopenAsyncHandle | _AsyncioProcessHandle,
) -> None:
    """等待进程完成。
    
    处理进程正常退出、超时、取消等场景。
    """
    deadline = state.deadline
    
    while not state.status.terminal:
        try:
            if deadline is not None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError("Deadline exceeded")
                await asyncio.wait_for(
                    handle.wait(),
                    timeout=min(remaining, 1.0)
                )
            else:
                await handle.wait()
            break
        except asyncio.TimeoutError:
            if deadline is not None and \
               asyncio.get_running_loop().time() >= deadline:
                state.status = ExecutionStatus.TIMED_OUT
                # 强制 kill，不只是 terminate
                killed = await handle.kill()
                if not killed:
                    _logger.error(
                        "Failed to kill timed-out process: "
                        "execution_id=%s pid=%s",
                        state.execution_id,
                        getattr(state, 'pid', 'unknown'),
                    )
                await handle.wait()  # 确保进程已终止
                break
        except asyncio.CancelledError:
            if not state.status.terminal:
                state.status = ExecutionStatus.CANCELLED
            raise
        except Exception as exc:
            self._mark_failed(state, exc)
            break
    else:
        # 循环正常退出，检查最终状态
        pass
    
    self._process_semaphore.release()
```

#### P1-2: 执行状态无限增长

| 属性 | 值 |
|------|-----|
| **文件** | `execution_runtime.py` |
| **位置** | `_states` 字典管理 |
| **根因** | 无自动清理机制 |
| **修复类型** | Enhancement |

**修复方案**:
```python
class ExecutionRuntime:
    # 配置常量
    MAX_RETAINED_STATES: int = 1000
    MAX_TERMINAL_STATES: int = 500
    CLEANUP_THRESHOLD: float = 0.8  # 达到 80% 时触发清理
    
    def __init__(
        self,
        max_async_concurrency: int = DEFAULT_ASYNC_CONCURRENCY,
        max_blocking_concurrency: int = DEFAULT_BLOCKING_CONCURRENCY,
        max_process_concurrency: int = DEFAULT_PROCESS_CONCURRENCY,
        max_retained_states: int | None = None,
    ):
        # ... existing init ...
        self._max_retained_states = max_retained_states or self.MAX_RETAINED_STATES
        self._cleanup_triggered = False
    
    def _register_state(self, state: _ExecutionState) -> None:
        """注册执行状态。
        
        当状态数量超过阈值时自动触发清理。
        """
        self._states[state.execution_id] = state
        
        # 检查是否需要清理
        if not self._cleanup_triggered and \
           len(self._states) >= self._max_retained_states * self.CLEANUP_THRESHOLD:
            self._cleanup_triggered = True
            # 延迟清理，避免阻塞主流程
            asyncio.get_running_loop().call_later(
                0.1,
                lambda: asyncio.create_task(self._compact_states())
            )
    
    async def _compact_states(self) -> None:
        """压缩状态字典，保留最近的终态。
        
        清理策略:
        1. 保留所有非终态
        2. 终态按时间排序，保留最近的 MAX_TERMINAL_STATES 个
        """
        terminal_states = {
            eid: s for eid, s in self._states.items()
            if s.status.terminal
        }
        non_terminal_states = {
            eid: s for eid, s in self._states.items()
            if not s.status.terminal
        }
        
        if not terminal_states:
            self._cleanup_triggered = False
            return
        
        # 按完成时间排序
        sorted_terminal = sorted(
            terminal_states.items(),
            key=lambda x: x[1].finished_at or x[1].started_at or 0,
            reverse=True
        )
        
        # 保留最近的终态
        kept_terminal = dict(sorted_terminal[:self.MAX_TERMINAL_STATES])
        
        self._states = {**kept_terminal, **non_terminal_states}
        self._cleanup_triggered = False
        
        _logger.debug(
            "States compacted: kept=%d terminal=%d non_terminal=%d",
            len(self._states),
            len(kept_terminal),
            len(non_terminal_states),
        )
```

#### P1-3: 生命周期文件非原子操作

| 属性 | 值 |
|------|-----|
| **文件** | `lifecycle.py` |
| **位置** | `update_director_lifecycle()` |
| **根因** | 读-改-写非原子，并发时可能损坏 |
| **修复类型** | Bug Fix |

**修复方案**:
```python
def update_director_lifecycle(
    path: str,
    *,
    phase: str,
    run_id: str = "",
    task_id: str = "",
    status: str = "unknown",
    details: str | None = None,
    error: str | None = None,
    workspace: str = "",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """更新 Director 生命周期状态。
    
    Args:
        path: 生命周期文件路径
        phase: 当前阶段
        run_id: 运行 ID
        task_id: 任务 ID
        status: 状态
        details: 详情
        error: 错误信息
        workspace: 工作区
        timestamp: 时间戳
        
    Returns:
        更新后的生命周期数据
        
    Note:
        此函数是线程/协程安全的，使用文件锁保护并发写入。
    """
    if not path:
        return {}
    
    lock_path = f"{path}.lock"
    fd = acquire_lock_fd(lock_path, timeout_sec=5.0)
    try:
        payload = read_director_lifecycle(path)
        
        if "lifecycle" not in payload:
            payload["lifecycle"] = {}
        
        lc = payload["lifecycle"]
        lc["phase"] = phase
        lc["status"] = status
        if run_id:
            lc["run_id"] = run_id
        if task_id:
            lc["task_id"] = task_id
        if workspace:
            lc["workspace"] = workspace
        if details is not None:
            lc["details"] = details
        if error is not None:
            lc["error"] = error
        if timestamp is not None:
            lc["timestamp"] = timestamp
        else:
            lc["timestamp"] = _utc_now()
        
        # 保持事件历史不超过 50 条
        if "events" not in payload:
            payload["events"] = []
        payload["events"].append({
            "phase": phase,
            "status": status,
            "timestamp": lc["timestamp"],
        })
        if len(payload["events"]) > 50:
            payload["events"] = payload["events"][-50:]
        
        # 原子写入
        write_json_atomic(path, payload)
        return payload
        
    except Exception as exc:
        _logger.error("Failed to update director lifecycle: %s", exc)
        raise
    finally:
        release_lock_fd(fd, lock_path)
```

---

### 2.3 P2 问题 (中优先级)

#### P2-1: shared_types.py 职责混乱

| 属性 | 值 |
|------|-----|
| **当前文件** | `shared_types.py` (257 行) |
| **问题** | 混合 ANSI 颜色、日志工具、路径规范化、正则表达式 |
| **修复类型** | 重构 (职责分离) |

**拆分方案**:
```
polaris/kernelone/shared/
    ├── __init__.py
    ├── terminal.py      # ANSI 颜色常量、终端检测
    ├── text_utils.py    # safe_truncate、strip_ansi、正则
    └── path_utils.py    # normalize_path、Path 类扩展
```

#### P2-2: 异常处理标准化

| 属性 | 值 |
|------|-----|
| **问题** | 5 处宽泛 `except Exception`，1 处静默吞噬 |
| **修复类型** | 代码质量改进 |

**改进方案**:
```python
# 标准化异常处理装饰器
from functools import wraps
from typing import TypeVar, ParamSpec
import logging

P = ParamSpec('P')
T = TypeVar('T')

def log_and_reraise(
    logger: logging.Logger,
    level: int = logging.ERROR,
):
    """记录异常后重新抛出。
    
    Args:
        logger: 日志记录器
        level: 日志级别
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                logger.log(level, 
                    f"{func.__name__} failed: {exc}",
                    exc_info=True
                )
                raise
        
        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                logger.log(level,
                    f"{func.__name__} failed: {exc}",
                    exc_info=True
                )
                raise
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator
```

---

## 3. 架构边界清理

### 3.1 业务语义迁移

| 当前路径 | 目标路径 | 说明 |
|----------|----------|------|
| `runtime/constants.py` | `application/director/constants.py` | Polaris 业务路径 |
| `runtime/lifecycle.py` | `domain/director/lifecycle.py` | Director 生命周期 |

### 3.2 重构后结构

```
polaris/kernelone/
    ├── runtime/
    │   ├── __init__.py           # 公共导出
    │   ├── constants.py          # 纯技术常量 (已清理)
    │   ├── defaults.py           # 默认配置
    │   ├── execution_runtime.py  # 核心运行时
    │   ├── execution_facade.py   # 门面层
    │   ├── run_id.py            # ID 验证
    │   └── usage_metrics.py     # 指标追踪
    │
    ├── shared/                  # 新建: 共享工具
    │   ├── __init__.py
    │   ├── terminal.py          # ANSI 颜色、终端
    │   ├── text_utils.py        # 文本处理
    │   └── path_utils.py        # 路径工具
    │
    ├── process/                 # 进程管理
    │   ├── async_contracts.py
    │   └── background_manager.py
    │
    └── events/                  # 事件总线
        ├── message_bus.py       # 增强: 死信机制
        └── io_events.py

polaris/domain/director/          # 新建: 业务领域
    ├── __init__.py
    ├── constants.py             # 业务路径常量
    └── lifecycle.py            # Director 生命周期
```

---

## 4. 测试策略

### 4.1 测试覆盖要求

| 测试类别 | 覆盖率要求 | 场景数 |
|----------|-----------|--------|
| 正常路径 | 100% | ≥ 10 |
| 边界条件 | 100% | ≥ 5 |
| 异常处理 | 100% | ≥ 5 |
| 并发安全 | 100% | ≥ 3 |
| 资源清理 | 100% | ≥ 3 |

### 4.2 关键测试场景

```python
# tests/test_execution_runtime.py

class TestExecutionRuntime:
    async def test_process_semaphore_release_on_exception(self):
        """验证异常时信号量正确释放。"""
        runtime = ExecutionRuntime(max_process_concurrency=1)
        
        # 第一次执行会失败
        with pytest.raises(RuntimeError):
            await runtime.submit_process(
                "nonexistent_command_xyz",
                timeout=1.0
            )
        
        # 信号量应该已释放，第二次执行应该成功
        handle = await runtime.submit_process(
            "echo test",
            timeout=1.0
        )
        result = await handle.wait()
        
        assert result.status == ExecutionStatus.SUCCESS
        await runtime.close()
    
    async def test_timeout_forces_kill(self):
        """验证超时后进程被强制终止。"""
        runtime = ExecutionRuntime()
        
        handle = await runtime.submit_process(
            "sleep 10",
            timeout=0.5  # 500ms 超时
        )
        result = await handle.wait()
        
        assert result.status == ExecutionStatus.TIMED_OUT
        # 进程应该已被 kill，不会继续运行
        await asyncio.sleep(0.5)
        # ...
        
        await runtime.close()
    
    async def test_state_compaction(self):
        """验证状态自动压缩。"""
        runtime = ExecutionRuntime(max_retained_states=10)
        
        # 创建大量执行
        handles = []
        for i in range(20):
            h = await runtime.submit_process(
                "echo ok",
                timeout=1.0
            )
            handles.append(h)
        
        # 等待所有完成
        for h in handles:
            await h.wait()
        
        # 触发清理
        await asyncio.sleep(0.2)
        
        # 状态数量应该被限制
        assert len(runtime._states) <= 10
        await runtime.close()

# tests/test_message_bus.py

class TestMessageBus:
    async def test_dead_letter_on_queue_full(self):
        """验证队列满时消息进入死信。"""
        bus = MessageBus()
        queue = asyncio.Queue(maxsize=1)
        bus._direct_queues["test_recipient"] = queue
        
        # 填满队列
        await queue.put(Message(...))
        
        # 再次发布应进入死信
        msg = Message(recipient="test_recipient", ...)
        await bus.publish(msg)
        
        assert bus._dropped_messages_total == 1
        assert len(bus._dead_letters) == 1
        assert bus._dead_letters[0].reason.startswith("queue_full")
    
    async def test_dead_letter_retrieval(self):
        """验证死信可检索。"""
        bus = MessageBus()
        # ... 填充死信 ...
        
        dead_letters = await bus.get_dead_letters(limit=10)
        assert len(dead_letters) <= 10
```

---

## 5. 实施计划

### Phase 1: P0 修复 (1-2 天)

| 任务 | 负责人 | 验收标准 |
|------|--------|----------|
| P0-1 信号量泄漏修复 | Agent-1 | 单元测试通过 |
| P0-2 死信机制实现 | Agent-2 | 队列满测试通过 |
| P0-3 环境变量过滤 | Agent-3 | 安全测试通过 |

### Phase 2: P1 修复 (2-3 天)

| 任务 | 负责人 | 验收标准 |
|------|--------|----------|
| P1-1 进程超时强制终止 | Agent-4 | 超时测试通过 |
| P1-2 状态自动清理 | Agent-5 | 内存不增长验证 |
| P1-3 生命周期原子性 | Agent-6 | 并发测试通过 |

### Phase 3: P2 改进 (3-5 天)

| 任务 | 负责人 | 验收标准 |
|------|--------|----------|
| P2-1 shared_types 拆分 | Agent-7 | 所有导入更新 |
| P2-2 异常处理标准化 | Agent-8 | 无宽泛 except |
| P2-3 可观测性增强 | Agent-9 | Span 覆盖完整 |

### Phase 4: 架构重构 (5-7 天)

| 任务 | 负责人 | 验收标准 |
|------|--------|----------|
| 业务语义迁移 | Agent-10 | Graph 同步更新 |
| 集成测试 | 全部 | E2E 测试通过 |
| 文档更新 | 全部 | API 文档完整 |

---

## 6. 验收标准

### 6.1 代码质量

- [ ] 无 P0-P1 问题
- [ ] 类型注解覆盖率 ≥ 95%
- [ ] 单元测试覆盖率 ≥ 80%
- [ ] 无宽泛 `except:` 或 `except Exception: pass`

### 6.2 安全合规

- [ ] 环境变量危险变量已过滤
- [ ] 无命令注入风险
- [ ] 无路径遍历漏洞

### 6.3 架构合规

- [ ] KernelOne 层无 Polaris 业务语义
- [ ] 跨 Cell 只走 Public Contract
- [ ] 单一状态拥有原则遵守

---

## 7. 风险与边界

### 7.1 已知风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 重构引入回归 | 高 | 完整单元测试 + E2E 测试 |
| 并发测试不稳定 | 中 | 使用 `pytest-asyncio` 确定性模式 |
| Windows 兼容性问题 | 低 | CI 覆盖 Windows 环境 |

### 7.2 边界条件

- 空命令/超时值处理
- 进程在 `kill()` 前已退出
- 队列在 `put_nowait()` 后立即被消费
- 文件锁超时/死锁

---

## 8. 后续优化

### 8.1 可选改进 (不在本次范围)

| 改进项 | 优先级 | 说明 |
|--------|--------|------|
| OpenTelemetry 集成 | P2 | 分布式追踪 |
| 消息持久化 | P2 | WAL 支持 |
| 自适应超时 | P3 | 基于历史调整 |

### 8.2 监控指标

```python
# 建议添加的 Prometheus 指标
kernelone_execution_active_total{lane}
kernelone_execution_completed_total{status, lane}
kernelone_execution_duration_seconds{lane}
kernelone_message_bus_dropped_total
kernelone_process_killed_total{reason}
kernelone_states_retained_current
```

---

## 9. 变更记录

| 版本 | 日期 | 修改内容 |
|------|------|----------|
| 1.0.0 | 2026-03-27 | 初始版本 |
