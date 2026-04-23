# Polaris Unified Event Pipeline (UEP) v2.0 架构蓝图

**版本**: v2.0
**日期**: 2026-03-31
**状态**: Stage 1 Blueprint / Ready for Execution
**执行团队**: KernelOne 基础设施组 + Roles Runtime 组

---

## 1. 执行摘要

Polaris 当前存在 **4 套并行的审计/事件落盘系统**，彼此没有统一Schema，导致benchmark、CLI、API三条调用链产生的事件不一致，甚至出现“断头路”——某些路径有日志但另一些路径查不到。

本蓝图目标是：
- **建立单一真相来源**: Polaris Event Bus 作为所有事件的中枢
- **引入 Sink 模式**: Journal、Archive、AuditHash 作为独立 Consumer，互不干扰
- **消除路径特权**: 移除 `@audit_stream_turn` 这种仅 CLI 享有的装饰器逻辑，所有入口统一走 UEP
- **显式失败处理**: 任何 Sink 写入失败必须被记录（metrics + 结构化日志），禁止 `except: pass` 静默吞错

---

## 2. 架构现状与问题诊断

### 2.1 四套并行系统

| 系统 | 落盘位置 | 生产者 | 覆盖路径 | 问题 |
|------|----------|--------|----------|------|
| **Legacy LLM Events** | `{runtime_root}/events/{role}.llm.events.jsonl` | `emit_llm_event()` → `_emit_llm_event_to_disk()` | 所有调用 `LLMInvoker` / `LLMCaller` 的路径 | 仅记录 CALL_START/END/ERROR/RETRY；无 stream chunk；`_emit_llm_event_to_disk()` 中 `except Exception: pass` 导致静默丢失 |
| **Stream Journal** | `{runtime_root}/runs/{run_id}/logs/journal.{raw|norm|enriched}.jsonl` | `LogEventWriter` (由 `RoleExecutionKernel.run_stream()` 驱动) | 所有调用 `kernel.run_stream()` 的路径 | 不记录非流式调用；外部难以发现 run_id |
| **Stream Archive** | `{history_root}/runs/{turn_id}/stream_events.jsonl.gz` | `StreamArchiver` | **仅 CLI** (`@audit_stream_turn`) | benchmark/API 路径完全被排除 |
| **AuditStore HMAC Chain** | `{runtime_root}/audit/audit-{YYYY-MM}.jsonl` | `AuditStoreAdapter` | 仅 CLI audit 工具触发 | benchmark 场景下几乎为空 |

### 2.2 路径分歧（Path Divergence）

```
benchmark path:
  stream_chat_turn() -> kernel.run_stream() -> journal ✅ | archive ❌ | bus ❌ | audit ❌

CLI path:
  RoleConsoleHost.stream_turn() -> @audit_stream_turn -> bus ✅ | archive ✅ | journal ✅

API path:
  HTTP router -> execute_role_session(stream=True) -> journal ✅ | archive ❌ | bus ❌

non-stream path:
  kernel.run() -> legacy events ✅ | journal ❌ | archive ❌ | bus ❌
```

### 2.3 根本原因

1. **无统一总线**: `MessageBus` 已经存在，但只有 CLI 路径（`audit_stream_turn`）和 `stream_chat_turn()` 手动 publish；大部分路径绕过它。
2. **Sink 与 Producer 耦合**: `LogEventWriter` 在 `kernel/core.py` 内部直接实例化；`StreamArchiver` 在装饰器里直接调用。
3. **Schema 碎片化**: legacy events 使用 `LLMCallEvent`，journal 使用 `CanonicalLogEventV2`，archive 使用自定义 dict，audit store 使用 `AuditEvent`。
4. **静默失败成习惯**: `_emit_llm_event_to_disk()` 的 `except Exception: pass` 导致 benchmark sandbox 中事件被吞掉而不为人知。

---

## 3. 设计原则

| 原则 | 说明 |
|------|------|
| **Bus-Centric** | 所有可审计事件必须先经过 `MessageBus`。任何直接写文件的”旁路”都被视为技术债。 |
| **Sink Decoupling** | Producer 不关心谁消费。Journal、Archive、Audit、Realtime Fanout 都是独立的 Bus Consumer / Actor。 |
| **Schema Convergence** | 统一使用 `CanonicalLogEventV2` 作为 on-wire 格式，legacy 事件通过 `write_from_legacy()` 转换。 |
| **Fail-Explicit** | 任何 Sink 处理异常必须记录结构化错误（`logger.error` + metrics），不能静默 pass。主流程不可中断。 |
| **Entry Parity** | benchmark、CLI、API、单元测试在事件落盘上行为一致；不允许任何入口享有特权装饰器。 |
| **Dual-Write** | UEP 事件通过 `TypedEventBusAdapter` 双重写入：EventRegistry（实时订阅）+ MessageBus（持久化 Sink）。 |

---

## 4. 目标架构：UEP v2.0

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PRODUCERS                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ TurnEngine   │  │ LLMInvoker   │  │ RoleRuntime  │  │ ToolExecutor     │ │
│  │ (stream)     │  │ (lifecycle)  │  │ (fingerprint │  │ (tool_execute)   │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘ │
│         │                 │                 │                   │           │
│         └─────────────────┴─────────────────┴───────────────────┘           │
│                                   │                                         │
│                          UEPEventPublisher                                  │
│                                   │                                         │
│                                   ▼                                         │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │          TypedEventBusAdapter.emit_to_both() (双重写入)               │  │
│  │                                                                       │  │
│  │   ┌─────────────────────┐     ┌─────────────────────────────────┐   │  │
│  │   │   EventRegistry      │     │           MessageBus              │   │  │
│  │   │  (Typed Events)     │     │     (底层消息总线)               │   │  │
│  │   │                     │     │                                  │   │  │
│  │   │  • 实时订阅        │     │  • JournalSink ─► journal.jsonl  │   │  │
│  │   │  • 前端 WebSocket   │     │  • ArchiveSink ─► stream.gz    │   │  │
│  │   │  • UI 更新          │     │  • AuditHashSink ─► HMAC chain │   │  │
│  │   └─────────────────────┘     └─────────────────────────────────┘   │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```
┌───────────────────────────────────▼─────────────────────────────────────────┐
│                          Polaris Event Bus                                   │
│                   (polaris/kernelone/events/message_bus.py)                  │
│                                                                              │
│   Topics:                                                                    │
│   - runtime.event.all        (所有运行时事件)                                 │
│   - runtime.event.llm        (LLM 生命周期事件)                               │
│   - runtime.event.stream     (流式 chunk / tool_call / complete)              │
│   - runtime.event.audit      (需要 HMAC 签名的审计事件)                        │
│   - runtime.event.fingerprint (策略指纹)                                      │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────────┐
│                             SINKS (Actors)                                   │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │ JournalSink     │  │ ArchiveSink     │  │ AuditHashSink   │              │
│  │ (Actor)         │  │ (Actor)         │  │ (Actor)         │              │
│  │                 │  │                 │  │                 │              │
│  │ Writes:         │  │ Writes:         │  │ Writes:         │              │
│  │ journal.raw/    │  │ history/runs/   │  │ runtime/audit/  │              │
│  │ norm/enriched   │  │ stream_events.  │  │ audit-*.jsonl   │              │
│  │ .jsonl          │  │ jsonl.gz        │  │ (HMAC chain)    │              │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘              │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐                                    │
│  │ RealtimeFanout  │  │ MetricsSink     │  (可选)                           │
│  │ (已有)          │  │ (新增)          │                                    │
│  └─────────────────┘  └─────────────────┘                                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. 组件规格

### 5.1 Producer 边界 refactoring

#### 5.1.1 `RoleExecutionKernel.run_stream()` (polaris/cells/roles/kernel/internal/kernel/core.py)

**当前问题**: 内部直接实例化 `LogEventWriter`，并通过 `_emit_stream_log_event()` 写 journal。

**目标行为**: 不再内部写 journal。改为将每个 stream event publish 到 `MessageBus` topic `runtime.event.stream`。

- 删除 `_build_stream_log_writer()` 调用
- 删除 `_emit_stream_log_event()` 调用
- 改为: `await _publish_stream_event_to_bus(event)`
- 保留 `yield event` 不变

#### 5.1.2 `LLMInvoker` (polaris/cells/roles/kernel/internal/llm_caller/invoker.py)

**当前问题**: `_emit_call_start_event` / `_emit_call_end_event` 等方法默认 fallback 到 `emit_llm_event()`，后者又调用 `_emit_llm_event_to_disk()`。

**目标行为**: 所有 lifecycle 事件统一 publish 到 `MessageBus` topic `runtime.event.llm`。

- 修改 `_emit_call_*_event` 方法：优先 publish 到 bus；如果 bus 不可用，再 fallback 到 legacy `emit_llm_event()`（保留过渡期兼容）
- 新增 `_publish_llm_lifecycle_event(...)` 辅助方法

#### 5.1.3 `RoleRuntimeService.stream_chat_turn()` (polaris/cells/roles/runtime/public/service.py)

**当前问题**: 手动 publish fingerprint 和 stream events 到 bus，但 schema 不统一（自定义 dict）。缺少 archive。

**目标行为**: 所有事件以 `CanonicalLogEventV2`  envelope 形式 publish 到 `runtime.event.stream` 和 `runtime.event.fingerprint`。

- 移除内嵌的 `BusMessage(type=MessageType.EVENT, ...)` 手动构造（交给 Producer helper）
- 使用新的 `UEPEventPublisher` 统一接口
- 在 `finally` 块中不再需要手动调用 `StreamArchiver`（由 ArchiveSink 消费 bus 事件完成）

#### 5.1.4 `emit_llm_event()` / `_emit_llm_event_to_disk()` (polaris/cells/roles/kernel/internal/events.py)

**目标行为**: 标记为 **deprecated**。

- `emit_llm_event()` 改为统一包装 legacy 事件并 publish 到 bus topic `runtime.event.llm`，同时调用 `_emit_llm_event_to_disk()` 作为短期 fallback
- `_emit_llm_event_to_disk()` 的 `except Exception: pass` 必须改为显式错误记录：`logger.error(...)`，不能是 `warning`

### 5.2 Polaris Event Bus 集成

#### 5.2.1 新增 Topic 常量

在 `polaris/kernelone/events/message_bus.py` 的 `MessageType` 中新增：

```python
class MessageType:
    ...
    AUDIT = "audit"
    RUNTIME_EVENT = "runtime_event"
```

或者（如果不修改 MessageType），使用 `MessageType.EVENT` 并在 payload 中增加 `topic` 字段做子路由。推荐使用 payload 子路由，避免修改公共枚举引发连锁变更。

#### 5.2.2 新增 `UEPEventPublisher`

新建文件: `polaris/kernelone/events/uep_publisher.py`

```python
class UEPEventPublisher:
    def __init__(self, bus: MessageBus | None = None):
        self._bus = bus or get_global_bus()

    async def publish_stream_event(
        self,
        *,
        workspace: str,
        run_id: str,
        role: str,
        event_type: str,
        payload: dict[str, Any],
        turn_id: str | None = None,
    ) -> None:
        """Publish a stream chunk/tool_call/complete/error event."""

    async def publish_llm_lifecycle_event(
        self,
        *,
        workspace: str,
        run_id: str,
        role: str,
        event_type: str,  # call_start, call_end, call_error, call_retry
        metadata: dict[str, Any],
    ) -> None:
        """Publish an LLM lifecycle event."""

    async def publish_fingerprint_event(
        self,
        *,
        workspace: str,
        run_id: str,
        role: str,
        fingerprint: dict[str, Any],
    ) -> None:
        """Publish strategy fingerprint."""
```

所有 publish 方法内部统一构造 `Message(type=MessageType.EVENT, sender="uep", payload=...)`。

### 5.3 Sink 定义

#### 5.3.1 `JournalSink` (polaris/infrastructure/log_pipeline/journal_sink.py)

继承 `Actor` 基类。

```python
class JournalSink(Actor):
    """Consumes runtime.event.stream / runtime.event.llm / runtime.event.fingerprint
    and writes to journal.raw|norm|enriched.jsonl via LogEventWriter."""

    async def handle_message(self, message: Message) -> None:
        if message.payload.get("topic") not in ("runtime.event.stream", "runtime.event.llm", "runtime.event.fingerprint"):
            return
        # normalize to CanonicalLogEventV2
        # write via get_writer(workspace, run_id)
```

#### 5.3.2 `ArchiveSink` (polaris/cells/archive/run_archive/internal/archive_sink.py)

继承 `Actor` 基类。

```python
class ArchiveSink(Actor):
    """Consumes runtime.event.stream and archives per turn via StreamArchiver."""

    def __init__(self, ...):
        self._buffers: dict[str, list[dict]] = {}  # turn_id -> events
        self._lock = asyncio.Lock()

    async def handle_message(self, message: Message) -> None:
        if message.payload.get("topic") != "runtime.event.stream":
            return
        turn_id = message.payload.get("turn_id")
        # buffer events; when receiving a "complete" or "error", flush to StreamArchiver
```

**关键**: ArchiveSink 取代 `@audit_stream_turn` 的 archive 职责。所有路径产生的 stream 事件都会被自动归档。

#### 5.3.3 `AuditHashSink` (polaris/infrastructure/audit/sinks/audit_hash_sink.py)

继承 `Actor` 基类。

```python
class AuditHashSink(Actor):
    """Consumes runtime.event.audit and appends to AuditStore HMAC chain."""

    async def handle_message(self, message: Message) -> None:
        if message.payload.get("topic") != "runtime.event.audit":
            return
        # append to AuditStore
```

### 5.4 `@audit_stream_turn` 废弃计划

**决策**: `@audit_stream_turn` 和 `apply_audit_decorator` 在 UEP v2.0 中标记为 **deprecated**，并在 2 个小版本后删除。

**原因**: 它是一个 CLI 特权装饰器，导致路径不一致。UEP 的目标是所有路径统一，不需要装饰器来“补漏”。

**迁移步骤**:
1. 在 `audit_decorator.py` 顶部添加 `warnings.warn("@audit_stream_turn is deprecated; use UEP sinks instead.", DeprecationWarning)`
2. `RoleConsoleHost.stream_turn()` 中继续调用 `apply_audit_decorator` 但改为 no-op（直接 yield 原始 stream，不执行 archive/bus publish）
3. 由 ArchiveSink / JournalSink 接管其职能

---

## 6. 数据流对比

### 6.1 Stream 模式（统一后）

```
TurnEngine.run_stream() 生成事件
  -> RoleExecutionKernel.run_stream() 既不写 journal 也不直接 yield
       改为: 每个事件 -> UEPEventPublisher.publish_stream_event() -> MessageBus

MessageBus
  -> JournalSink 写入 journal.norm.jsonl
  -> ArchiveSink 按 turn_id buffer 后 gzip 归档
  -> RealtimeFanout 推送到前端/CLI

RoleRuntimeService.stream_chat_turn()
  不再手动构造 BusMessage
  不再手动调用 StreamArchiver
```

### 6.2 Non-Stream 模式（统一后）

```
LLMInvoker.call() 生成 lifecycle 事件
  -> UEPEventPublisher.publish_llm_lifecycle_event() -> MessageBus

MessageBus
  -> JournalSink 写入 journal.{raw,norm,enriched}.jsonl
  -> AuditHashSink 写入 HMAC chain (可选，仅标记为 audit 的事件)
```

这意味着 **非流式调用首次也能产生 journal 记录**，解决了当前非流式路径事件黑盒的问题。

---

## 7. 需要修改的文件清单

### 7.1 新增文件

| 文件 | 用途 |
|------|------|
| `polaris/kernelone/events/uep_publisher.py` | 统一事件发布器 (集成 TypedEventBusAdapter) |
| `polaris/kernelone/events/uep_contracts.py` | UEP payload schema/dataclasses |
| `polaris/kernelone/events/uep_typed_converter.py` | UEP → TypedEvent 转换器 (双重写入桥梁) |
| `polaris/infrastructure/log_pipeline/journal_sink.py` | JournalSink Actor |
| `polaris/cells/archive/run_archive/internal/archive_sink.py` | ArchiveSink Actor |
| `polaris/infrastructure/audit/sinks/audit_hash_sink.py` | AuditHashSink Actor |
| `polaris/kernelone/events/tests/test_uep_publisher.py` | UEP 发布器单元测试 (含 TypedEventBusAdapter 集成测试) |
| `polaris/kernelone/events/tests/test_uep_sinks.py` | Sink 单元测试 |
| `polaris/kernelone/events/tests/test_uep_typed_converter.py` | UEP → TypedEvent 转换器单元测试 |
| `polaris/cells/roles/runtime/public/tests/test_uep_stream_parity.py` | benchmark/CLI/API 落盘一致性测试 |
| `polaris/delivery/cli/audit/tests/test_journal_integration.py` | audit_quick.py --journal 集成测试 |

### 7.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `polaris/cells/roles/kernel/internal/events.py` | `emit_llm_event()` 标记 deprecated；`_emit_llm_event_to_disk()` 改为显式 error 日志；增加 fallback bus publish；修复未闭合 docstring 语法错误 |
| `polaris/cells/roles/kernel/internal/llm_caller/invoker.py` | `_emit_call_*_event` 优先 publish 到 bus；fallback 到 legacy |
| `polaris/cells/roles/kernel/internal/kernel/core.py` | `run_stream()` 移除 `LogEventWriter` 内部实例化；改为 publish 到 bus |
| `polaris/cells/roles/kernel/internal/kernel/error_handler.py` | `KernelEventEmitter.emit_stream_log_event()` 标记 deprecated；内部改为调用 UEP publisher |
| `polaris/cells/roles/runtime/public/service.py` | `stream_chat_turn()` 移除手动 BusMessage 构造和 StreamArchiver 调用；使用 UEP publisher |
| `polaris/delivery/cli/director/audit_decorator.py` | `@audit_stream_turn` 和 `apply_audit_decorator` 标记 deprecated；内部 archive 逻辑改为 no-op |
| `polaris/delivery/cli/director/console_host.py` | 继续调用 `apply_audit_decorator` 但不再依赖它（由全局 Sink 接管） |
| `polaris/bootstrap/assembly.py` | 在 bootstrap 阶段注册 JournalSink、ArchiveSink、AuditHashSink 到 MessageBus |
| `polaris/delivery/cli/audit/audit/cli.py` | 新增 `--journal` 参数支持 UEP v2.0 Journal 查询 |
| `polaris/delivery/cli/audit/audit/handlers.py` | 实现 `_handle_journal_events()` 函数，支持直接查询 journal 文件 |
| `polaris/kernelone/events/uep_publisher.py` | 集成 `TypedEventBusAdapter` 实现双重写入（EventRegistry + MessageBus） |

---

## 8. 迁移路径（Migration Path）

### Phase 1: 基础设施（Week 1）
1. 实现 `UEPEventPublisher` + `uep_contracts.py`
2. 实现 `JournalSink`、`ArchiveSink`、`AuditHashSink`
3. 在 `bootstrap/assembly.py` 中注册 Sink Actor
4. 编写 Sink 单元测试

### Phase 2: Producer 接入（Week 2）
1. 修改 `LLMInvoker` lifecycle 事件 publish 到 bus
2. 修改 `RoleExecutionKernel.run_stream()` 不再内部写 journal
3. 修改 `RoleRuntimeService.stream_chat_turn()` 使用 UEP publisher
4. 修改 `emit_llm_event()` 标记 deprecated + fallback

### Phase 3: 废弃与清理（Week 3）✅ 已完成
1. `@audit_stream_turn` 标记 deprecated，archive 逻辑 no-op ✅
2. `audit_quick.py` 新增 `--journal` 参数，支持直接查询 journal 文件 ✅
   - 新增 `events --journal --discover` 命令
   - 新增 `events --journal --root <path>` 命令
   - 新增 JSON 格式输出支持 (`-f json`)
   - 新增 `--limit` 限制参数
3. 验证 benchmark / CLI / API 事件一致性 ✅ (test_uep_stream_parity.py)

### Phase 4: 全量验证（Week 4）✅ 已完成
1. `pytest polaris/kernelone/events/tests/ -v`
2. `pytest polaris/cells/roles/runtime/public/tests/test_uep_stream_parity.py -v`
3. 跑 `agentic-eval --suite tool_calling_matrix --observable` 验证 journal + archive 双输出

---

## 9. 验证策略

### 9.1 单元测试

- `test_uep_publisher_publish_stream_event`: 验证 publisher 正确构造 MessageBus Message
- `test_uep_publisher_publish_llm_lifecycle`: 验证 lifecycle 事件 topic 正确
- `test_journal_sink_writes_to_log_event_writer`: 验证 JournalSink 消费后写入 journal.norm.jsonl
- `test_archive_sink_buffers_and_flushes_on_complete`: 验证 ArchiveSink 在 complete 事件后调用 StreamArchiver
- `test_audit_hash_sink_appends_hmac_chain`: 验证 AuditHashSink 写入 AuditStore

### 9.2 集成测试（路径一致性为核心）

新建 `test_uep_stream_parity.py`：

```python
@pytest.mark.parametrize("entrypoint", [
    "benchmark_stream",   # 模拟 benchmark 调用 stream_chat_turn
    "cli_stream",         # 模拟 CLI 调用 RoleConsoleHost.stream_turn
    "api_stream",         # 模拟 HTTP 调用 execute_role_session(stream=True)
])
def test_all_stream_entrypoints_produce_journal_and_archive(entrypoint, tmp_workspace):
    ...
```

验收标准：
- 三种入口均产生 `journal.norm.jsonl` 且包含 `tool_call` 事件
- 三种入口均产生 `history/runs/{turn_id}/stream_events.jsonl.gz`
- 非流式入口 (`kernel.run()`) 也产生 `journal.norm.jsonl`

### 9.3 回归测试

- `ruff check . --fix && ruff format .` 零报错
- `mypy` 对新增文件输出 `Success: no issues found`
- 已有 benchmark 不因为事件系统重构而变慢（< 5% 性能 regression）

---

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| MessageBus 在单测/沙箱中未初始化 | 中 | 高 | `UEPEventPublisher` 内部检查 `bus is not None`；无 bus 时 fallback 到 legacy `emit_llm_event()`，保证不崩溃 |
| `ArchiveSink` buffer 在异常流中未 flush | 低 | 中 | 增加 `atexit` / asyncio shutdown handler 强制 flush；complete/error 事件必须触发 flush |
| JournalSink 引入 Actor 后写入延迟 | 低 | 低 | JournalSink 直接同步写 `LogEventWriter`（在 `handle_message` 中 await），不引入异步队列延迟 |
| 循环依赖（kernel -> uep_publisher -> bus -> kernel） | 中 | 高 | `uep_publisher.py` 只依赖 `message_bus.py` 和 `registry.py`，不依赖 kernel 内部模块 |
| 现有 HTTP endpoint 监听 LLMEventEmitter 失效 | 低 | 高 | 保留 `LLMEventEmitter` 作为辅助内存历史；bus publish 后可选同步 emit 到 `LLMEventEmitter` 供 listener 使用 |

---

## 11. 结论

UEP v2.0 不是对现有系统的“打补丁”，而是一次架构收敛：
- **从 4 套系统收敛到 1 套 Bus + N 个 Sink**
- **从路径特权收敛到入口平等**
- **从静默失败收敛到显式可观测**

执行本蓝图后，Polaris 的审计与事件系统将具备单一真相来源，任何调用链（benchmark、CLI、API、测试）产生的事件都可被一致地记录、查询和归档。

---

*Blueprint authored by: Principal Architect*
*Next step: Stage 2 Execution (代码实现与测试落地)*
