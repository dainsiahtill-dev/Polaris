# Omniscient Audit（全知之眼）架构文档

**版本**: 2026-04-03
**状态**: Phase 0-6 全部完成

---

## 1. 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│              Cell Layer (Business Logic)                     │  ← 零侵入
├──────────────────────────────────────────────────────────────┤
│  OmniscientAuditBus (PriorityQueue + Back-pressure)          │
│  ┌────────────────────────────────────────────────────────┐ │
│  │                    Interceptors                        │ │
│  │  LLMAuditInterceptor   │ ToolAuditInterceptor          │ │
│  │  TaskOrchInterceptor   │ AgentCommInterceptor          │ │
│  │  ContextAuditInterceptor│ AlertInterceptor              │ │
│  │  TracingAuditInterceptor│                              │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────┐ │
│  │                    StormDetector                        │ │
│  │  AuditStormDetector (sliding window, 5-level thresholds) │ │
│  └────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────┤
│  AuditContext (frozen dataclass + contextvars)               │  ← run_id/turn_id/task_id
├──────────────────────────────────────────────────────────────┤
│  TypedEvent Schema Layer (CloudEvents-v1.3 inspired)         │
│  LLMInteractionEvent │ ToolExecutionEvent │ TaskOrchestration │
│  AgentCommunication │ ContextManagement │ BudgetAudit         │
├──────────────────────────────────────────────────────────────┤
│  KernelAuditRuntime (HMAC Chain) │ TypedEventRegistry (UEP)  │
│  EvidenceStore (attachments)       │ JournalSink (JSONL)     │
│  AlertingEngine (rule evaluation) │ UnifiedTracer (spans)    │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. 核心组件

### 2.1 OmniscientAuditBus

**文件**: `polaris/kernelone/audit/omniscient/bus.py`

异步事件总线，PriorityQueue + fire-and-forget emit。

| 方法 | 说明 |
|------|------|
| `emit(event, priority)` | 非阻塞发射事件，返回 envelope_id |
| `subscribe(callback)` | 订阅事件 |
| `start()` / `stop()` | 生命周期管理 |
| `track_llm_interaction(name)` | LLM 调用上下文管理器 |
| `track_tool_execution(name)` | 工具执行上下文管理器 |

**Priority 级别**: CRITICAL(0) > ERROR(1) > WARNING(2) > INFO(3) > DEBUG(4)

### 2.2 AuditContext

**文件**: `polaris/kernelone/audit/omniscient/context.py`

frozen dataclass，存储 Correlation ID，通过 `contextvars` 传播到异步子任务。

```python
ctx = AuditContext(run_id="run_123", turn_id="turn_1", workspace="/project")
with audit_context(ctx):
    await bus.emit({"type": "llm_interaction", ...})
```

### 2.3 拦截器（Interceptors）

| 拦截器 | 文件 | 职责 |
|--------|------|------|
| `LLMAuditInterceptor` | `interceptors/llm.py` | Token 计数、latency、错误率、熔断 |
| `ToolAuditInterceptor` | `interceptors/tool.py` | 工具执行时间、写操作检测、熔断 |
| `TaskOrchestrationInterceptor` | `interceptors/task.py` | DAG 状态、死锁检测、超时告警 |
| `AgentCommInterceptor` | `interceptors/agent.py` | Director 生命周期、消息拓扑图 |
| `ContextAuditInterceptor` | `interceptors/context_mgmt.py` | Context Window 占用率、压缩事件 |
| `AuditAlertInterceptor` | `interceptors/alert.py` | 审计事件 → AlertingEngine 规则评估 |
| `TracingAuditInterceptor` | `interceptors/tracing.py` | 审计事件 → UnifiedTracer span |

**基类**: `BaseAuditInterceptor` — 所有拦截器继承
- 熔断器状态 (`circuit_open`)
- 事件计数器 (`events_processed`)
- 异常安全 (`intercept` 方法用 `contextlib.suppress`)

### 2.4 AuditStormDetector

**文件**: `polaris/kernelone/audit/omniscient/storm_detector.py`

滑动窗口风暴检测，5 级降级策略：

| 压力等级 | 阈值 (events/s) | 策略 |
|---------|----------------|------|
| NORMAL | < 500 | 全量处理 |
| ELEVATED | 500-2000 | 跳过 body content |
| WARNING | 2000-3000 | 暂停非关键拦截器 |
| CRITICAL | 3000-5000 | 仅保留 ERROR/CRITICAL |
| EMERGENCY | > 5000 | 全量丢弃 + alert |

### 2.5 SensitiveFieldRedactor

**文件**: `polaris/kernelone/audit/omniscient/redaction.py`

递归脱敏，检测模式：password、token、api_key、JWT、hex(32+)、base64(40+)。

### 2.6 AlertingEngine 集成

**文件**: `polaris/kernelone/audit/omniscient/interceptors/alert.py`

`AuditAlertInterceptor` 将审计事件转换为 `KernelAuditEvent` 格式，触发 AlertingEngine 规则：

- `high_failure_rate`: 5分钟内 3+ 次失败 → WARNING
- `security_violation`: 任何安全违规 → CRITICAL
- `audit_chain_broken`: HMAC 链验证失败 → ERROR
- `interceptor_circuit_open`: 拦截器熔断器打开 → CRITICAL

### 2.7 Tracing 集成

**文件**: `polaris/kernelone/audit/omniscient/interceptors/tracing.py`

`TracingAuditInterceptor` 将审计事件接入 Polaris `UnifiedTracer`：

- Span 名称: `audit.{event_type}`
- trace_id 来源: `AuditContext.run_id`
- Span tags: event_type、model、tool_name、priority 等
- Error events → `SpanStatus.ERROR`

---

## 3. 事件类型参考表

| type 字符串 | 拦截器 | TypedEvent 类型 |
|------------|--------|----------------|
| `llm_interaction` | LLMAuditInterceptor | LLMInteractionEvent |
| `llm_interaction_complete` | LLMAuditInterceptor | LLMInteractionEvent |
| `llm_interaction_error` | LLMAuditInterceptor | LLMInteractionEvent |
| `tool_execution` | ToolAuditInterceptor | ToolExecutionEvent |
| `tool_execution_start` | ToolAuditInterceptor | ToolExecutionEvent |
| `tool_execution_complete` | ToolAuditInterceptor | ToolExecutionEvent |
| `tool_execution_error` | ToolAuditInterceptor | ToolExecutionEvent |
| `task_submitted` | TaskOrchestrationInterceptor | TaskOrchestrationEvent |
| `task_started` | TaskOrchestrationInterceptor | TaskOrchestrationEvent |
| `task_completed` | TaskOrchestrationInterceptor | TaskOrchestrationEvent |
| `task_failed` | TaskOrchestrationInterceptor | TaskOrchestrationEvent |
| `director_started` | AgentCommInterceptor | AgentCommunicationEvent |
| `director_completed` | AgentCommInterceptor | AgentCommunicationEvent |
| `context_window_status` | ContextAuditInterceptor | ContextManagementEvent |

---

## 4. 非侵入接入指南

### 4.1 快速接入

```python
from polaris.kernelone.audit.omniscient import (
    OmniscientAuditBus,
    AuditPriority,
)
from polaris.kernelone.audit.omniscient.interceptors import (
    LLMAuditInterceptor,
    ToolAuditInterceptor,
    TracingAuditInterceptor,
    AuditAlertInterceptor,
)

# 初始化
bus = OmniscientAuditBus.get_default()
await bus.start()

# 订阅拦截器
bus.subscribe(LLMAuditInterceptor(bus))
bus.subscribe(ToolAuditInterceptor(bus))
bus.subscribe(TracingAuditInterceptor(bus))
bus.subscribe(AuditAlertInterceptor(bus))

# 发射事件
await bus.emit(
    {"type": "llm_interaction", "model": "gpt-4", "prompt_tokens": 100},
    priority=AuditPriority.INFO,
)
```

### 4.2 带 Correlation Context

```python
from polaris.kernelone.audit.omniscient import AuditContext, audit_context

ctx = AuditContext(run_id="run_abc", turn_id="turn_1", task_id="task_xyz")
with audit_context(ctx):
    await bus.emit({"type": "tool_execution", "tool_name": "read_file"})
```

---

## 5. 测试覆盖

| 模块 | 文件 | 测试数 |
|------|------|--------|
| Context | `test_context.py` | 15 |
| Bus + Integration | `test_integration.py` | 10 |
| Interceptors | `test_interceptors.py` | 31 |
| Redaction | `test_redaction.py` | 18 |
| Storm Detector | `test_storm_detector.py` | 24 |
| **合计** | | **98** |

---

## 6. 验收命令

```bash
# 质量网关
ruff check polaris/kernelone/audit/omniscient/ --fix
ruff format polaris/kernelone/audit/omniscient/
mypy polaris/kernelone/audit/omniscient/ --no-error-summary
pytest polaris/kernelone/audit/omniscient/ -v

# 预期：ruff 0 errors, mypy 0 errors, pytest 98/98 passed
```

---

## 7. 扩展路线

- **TypedEvent Schema 落地**: `schemas/*.py` 实现类（CloudEvents-v1.3 格式）
- **Evidence Package 集成**: 附件与 HMAC chain 加密链接
- **KernelAuditRuntime dual-write**: TypedEventBusAdapter 支持新事件类型
- **持久化 span 到 JournalSink**: TracingAuditInterceptor span 输出到 JSONL
