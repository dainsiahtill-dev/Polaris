# 全知之眼（Omniscient Audit System）实现计划

> 本文档详细记录 Omniscient Audit System 的架构设计、实现细节和使用指南。
> 适用于后期维护人员理解系统并基于此进行二次开发。
>
> **版本**: v1.0
> **日期**: 2026-04-04
> **状态**: 已完成全量落地

---

## 目录

1. [系统概述](#1-系统概述)
2. [架构蓝图](#2-架构蓝图)
3. [核心组件](#3-核心组件)
4. [文件结构](#4-文件结构)
5. [实现详情](#5-实现详情)
6. [集成指南](#6-集成指南)
7. [使用示例](#7-使用示例)
8. [质量验收](#8-质量验收)
9. [下一步计划](#9-下一步计划)

---

## 1. 系统概述

### 1.1 目标

全知之眼（Omniscient Audit System）为 Polaris 后端提供**全链路过程追踪**审计能力，覆盖：

- **LLM 交互审计**：token 使用量、latency、strategy (primary/fallback)、provider switching
- **工具/函数调用审计**：输入/输出、状态码、异常栈
- **任务编排与角色交流审计**：Cognitive Runtime 状态变更
- **上下文管理审计**：Prompt 模板渲染、上下文窗口占用
- **数据脱敏与安全**：PII 剔除、API Keys 掩码

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **绝对非侵入式** | 使用 `contextvars` 穿透，不能在 domain 函数签名中层层传递 `trace_id` |
| **向下兼容** | 复用 `KernelOne` 及 JSONL 机制，使用 Pydantic v2 定义事件 |
| **异步与隔离** | 审计写入通过后台队列/异步任务完成，主业务链路不阻塞 |
| **文件分区** | `workspace_id/YYYY-MM-DD/role_session.jsonl` 分区策略 |
| **可运维性** | 支持动态采样率、异常告警、降级策略 |

### 1.3 与旧系统对比

| 维度 | 旧系统（纯 Logging） | 新系统（Omniscient Audit） |
|------|---------------------|---------------------------|
| Trace 关联 | 无，跨系统 trace_id 丢失 | 统一 contextvars，async 自动传播 |
| Schema | dict 混用，无类型安全 | Pydantic v2 + CloudEvents 对齐 |
| 事件追溯 | 各模块写各的 JSONL | 全链路端到端追踪 |
| 脱敏 | Redactor 存在但未集成 | emit 时实时 redact |
| 批量写入 | 每事件 sync write | BatchQueue + 异步 flush |
| 文件分区 | 单 JSONL 文件 | workspace/date/channel 分区 |

---

## 2. 架构蓝图

### 2.1 contextvars 在各层间的流转

```
FastAPI Request
    ↓
[AuditContextMiddleware] — 从 header 提取 X-Trace-ID / X-Run-ID / X-Task-ID
    ↓ 1. set_audit_context(context) → contextvars.set()
    ↓
KernelOne Runtime
    ↓ get_current_audit_context() 读取 contextvars
    ↓ → 自动注入到 emit_event() / emit_llm_event()
    ↓
Provider Runtime (LLM 调用)
    ↓ 1. AuditContext 从 contextvars 自动获取
    ↓ 2. LLMCallTracker / LLMProviderAuditInterceptor 自动携带 trace_id
    ↓ 3. Prompt/Response 自动 redact 后落盘
    ↓
OmniscientAuditBus
    ↓ emit() → 事件入 PriorityQueue
    ↓ dispatch_loop() → 批量写到 KernelRuntimeAdapter
    ↓
KernelRuntimeAdapter → 异步批量落盘 + 文件分区
```

### 2.2 事件流图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        OmniscientAuditBus                            │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │PriorityQueue│  │StormDetector │  │  InterceptSubscribers     │  │
│  │(async)     │  │(backpressure)│  │  LLMCallInterceptor      │  │
│  └──────┬──────┘  └──────────────┘  │  ToolAuditInterceptor    │  │
│         │                            │  TaskOrchestrationInterceptor│  │
│         ↓                            └──────────────────────────┘  │
│  ┌─────────────┐                                                   │
│  │DispatchLoop│ → _fallback_persist()                            │
│  └──────┬──────┘                                                   │
└─────────┼─────────────────────────────────────────────────────────┘
          ↓
┌─────────────────────────────────────────────────────────────────┐
│                   KernelRuntimeAdapter                            │
│  ┌───────────────┐  ┌─────────────────┐  ┌──────────────────┐   │
│  │SanitizationHook│  │MemoryBoundedBatch│  │CircuitBreaker    │   │
│  │(PII redaction)│  │(async flush)    │  │(fault tolerance)│   │
│  └───────────────┘  └────────┬────────┘  └──────────────────┘   │
└──────────────────────────────┼───────────────────────────────────┘
                               ↓
                    ┌──────────────────────┐
                    │ Partitioned JSONL    │
                    │ {workspace}/{date}/  │
                    │ {prefix}.{type}.jsonl│
                    └──────────────────────┘
```

---

## 3. 核心组件

### 3.1 Schema 层 (`polaris/kernelone/audit/omniscient/schemas/`)

| 文件 | 类 | 用途 |
|------|-----|------|
| `base.py` | `AuditEvent`, `AuditPriority`, `EventDomain` | 事件基类，CloudEvents 对齐 |
| `llm_event.py` | `LLMEvent`, `LLMStrategy`, `LLMFinishReason` | LLM 交互审计 |
| `tool_event.py` | `ToolEvent`, `ToolCategory` | 工具调用审计 |
| `dialogue_event.py` | `DialogueEvent`, `MessageDirection`, `MessageType` | 角色通信审计 |
| `context_event.py` | `ContextEvent`, `ContextOperation` | 上下文管理审计 |
| `task_event.py` | `TaskEvent`, `TaskState` | 任务编排审计 |

**设计要点**：
- 所有事件为 `frozen=True`（不可变）
- `version` + `schema_uri` 支持 Schema Versioning
- `trace_id`/`run_id`/`span_id` 字段支持分布式追踪

### 3.2 Context 管理 (`polaris/kernelone/audit/omniscient/context_manager.py`)

| 类 | 用途 |
|-----|------|
| `UnifiedAuditContext` | 统一审计上下文，桥接 PolarisContext 和 AuditContext |
| `UnifiedContextFactory` | 工厂类，从 env vars / PolarisContext / 继承创建 |
| `_AuditContextScope` | async context manager，自动 lifecycle 管理 |
| `ThreadAuditContextScope` | sync 代码路径支持 |

**关键函数**：
- `get_current_audit_context()` — 从 contextvars 获取当前上下文
- `set_audit_context()` — 设置上下文
- `audit_context_scope()` — async with 自动管理

### 3.3 事件总线 (`polaris/kernelone/audit/omniscient/bus.py`)

**OmniscientAuditBus** — 核心事件总线

| 方法 | 说明 |
|------|------|
| `emit(event, priority)` | 非阻塞事件发射 |
| `subscribe(interceptor)` | 订阅拦截器 |
| `track_llm_interaction()` | LLM 调用追踪 context manager |
| `track_tool_execution()` | 工具执行追踪 context manager |
| `open_circuit()` / `close_circuit()` | 熔断控制 |
| `get_stats()` | 获取统计信息 |

### 3.4 适配器层 (`polaris/kernelone/audit/omniscient/adapters/`)

**SanitizationHook** — PII 脱敏
- 默认敏感字段模式：password, token, secret, api_key, credential, etc.
- JWT / Hex / Base64 token 检测
- 可配置旁路类型（如 `security_violation` 事件不脱敏）

**KernelRuntimeAdapter** — 异步批量写入
- 内存缓冲 + 定时 flush
- 文件分区：`{workspace}/{YYYY-MM-DD}/{prefix}.{event_type}.jsonl`
- CircuitBreaker 故障隔离

### 3.5 拦截器 (`polaris/kernelone/audit/omniscient/interceptors/`)

| 拦截器 | 文件 | 用途 |
|--------|------|------|
| `LLMCallInterceptor` | `llm_interceptor.py` | Bus 订阅者，聚合 LLM 指标 |
| `LLMCallTracker` | `llm_interceptor.py` | context manager，追踪 in-flight LLM 调用 |
| `LLMProviderAuditInterceptor` | `llm_provider_integration.py` | 集成到 provider runtime |
| `@llm_audit` | `llm_interceptor.py` | 非侵入式装饰器 |

### 3.6 高可用防御 (`polaris/kernelone/audit/omniscient/high_availability.py`)

| 组件 | 说明 |
|------|------|
| `AuditStormDetector` | Sliding window 风暴检测 |
| `PriorityBasedStormDetector` | 基于优先级的风暴处理 |
| `AuditSampler` | 动态采样率 |
| `MemoryBoundedBatcher` | OOM 防护，内存有界缓冲 |
| `AuditCircuitBreaker` | CLOSED → OPEN → HALF_OPEN 状态机 |
| `AuditFallbackManager` | 多级降级策略 (memory → disk → drop) |

### 3.7 查询引擎 (`polaris/kernelone/audit/omniscient/query_engine.py`)

**AuditQueryEngine** — 高效查询接口
- `discover_partitions()` — O(directory) 分区发现
- `query_by_time_range()` — 时间范围查询 + 分区裁剪
- `query_by_trace_id()` / `query_by_run_id()` / `query_by_task_id()` — O(1) 索引查询
- 分页支持：`QueryResult` with `offset/limit/has_more`

### 3.8 指标与注册表

**AuditMetricsCollector** (`metrics.py`)
- Prometheus 兼容指标：`audit_events_total`, `audit_events_latency_seconds`, `audit_buffer_size`, etc.
- 线程安全，RLock 保护

**SchemaRegistry** (`schema_registry.py`)
- CloudEvents 对齐的 schema 管理
- 自动注册 LLMEvent, ToolEvent, etc.
- 版本追踪和 schema URI 管理

---

## 4. 文件结构

```
polaris/kernelone/audit/omniscient/
├── __init__.py
├── bus.py                          # OmniscientAuditBus 核心事件总线
├── context.py                      # [旧] AuditContext (已废弃，使用 context_manager.py)
├── context_manager.py               # [新建] UnifiedAuditContext + 工厂
├── high_availability.py            # [新建] HA 防御组件
├── metrics.py                      # [新建] AuditMetricsCollector
├── query_engine.py                 # [新建] AuditQueryEngine
├── schema_registry.py               # [新建] SchemaRegistry
├── redaction.py                    # [已有] SensitiveFieldRedactor
│
├── schemas/                        # [新建]
│   ├── __init__.py
│   ├── base.py                     # AuditEvent 基类
│   ├── llm_event.py                # LLMEvent
│   ├── tool_event.py               # ToolEvent
│   ├── dialogue_event.py           # DialogueEvent
│   ├── context_event.py            # ContextEvent
│   └── task_event.py               # TaskEvent
│
├── adapters/                       # [新建]
│   ├── __init__.py
│   ├── sanitization_hook.py        # SanitizationHook
│   └── kernel_runtime_adapter.py   # KernelRuntimeAdapter
│
└── interceptors/                    # [增强]
    ├── __init__.py
    ├── base.py                     # BaseAuditInterceptor
    ├── llm.py                     # [旧] LLMAuditInterceptor (stub)
    ├── llm_interceptor.py         # [新建] 完整 LLM 审计拦截器
    └── llm_provider_integration.py # [新建] Provider runtime 集成

polaris/delivery/http/middleware/
└── audit_context.py                # [新建] AuditContextMiddleware

polaris/cells/llm/provider_runtime/internal/
└── runtime_invoke.py               # [修改] 集成 LLM 审计
```

---

## 5. 实现详情

### 5.1 AuditEvent Schema 设计

```python
# polaris/kernelone/audit/omniscient/schemas/base.py

class AuditEvent(BaseModel, frozen=True):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    version: str = "3.0"
    schema_uri: str = "https://polaris.dev/schemas/audit/v3.0"
    domain: EventDomain = EventDomain.SYSTEM
    event_type: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str = ""          # 16-char hex, 分布式追踪 ID
    run_id: str = ""           # 执行会话 ID
    span_id: str = ""           # 当前操作 span ID
    parent_span_id: str = ""   # 父 span ID
    priority: AuditPriority = AuditPriority.INFO
    workspace: str = ""         # 多租户隔离
    role: str = ""              # 角色 attribution
    data: dict[str, Any] = {}  # 事件特定 payload
    correlation_context: dict[str, Any] = {}  # 扩展关联上下文
```

### 5.2 UnifiedAuditContext 桥接

```python
# UnifiedAuditContext 继承 PolarisContext 字段
# 同时兼容 AuditContext 字段

PolarisContext     →  UnifiedAuditContext
─────────────────────────────────────────────
trace_id               →  trace_id
run_id                 →  run_id
task_id                →  task_id
workspace              →  workspace
span_stack[-1].span_id →  span_id
metadata               →  metadata
```

### 5.3 文件分区策略

```
{runtime_root}/audit/
├── {workspace}/                  # workspace 隔离
│   ├── 2026-04-04/             # 日期分区
│   │   ├── audit.llm_call.jsonl
│   │   ├── audit.tool_execution.jsonl
│   │   └── audit.dialogue.jsonl
│   └── 2026-04-05/
│       └── ...
└── default/                     # 无 workspace 的默认分区
```

### 5.4 脱敏规则

```python
# 默认敏感字段模式（case-insensitive）
DEFAULT_SENSITIVE_PATTERNS = [
    "password", "token", "secret", "api_key", "apikey",
    "authorization", "auth", "credential", "key", "private_key",
    "access_token", "refresh_token", "bearer", "session",
    "session_id", "cookie", "x-api-key",
]

# Token 检测
TOKEN_PATTERNS = [
    r"^[a-f0-9]{32,}$",           # Hex 字符串
    r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",  # JWT
    r"^[A-Za-z0-9+/]+=*$",        # Base64
]
```

### 5.5 高可用策略

| 场景 | 处理策略 |
|------|---------|
| 正常负载 | 全量记录，batch_size=100, flush_interval=1s |
|  Elevated (>500 events/window) | 采样率 50% |
|  WARNING (>2000) | 采样率 10%，跳过 body |
|  CRITICAL (>5000) | 采样率 5%，只保留 metadata |
|  EMERGENCY (>10000) | 采样率 1%，CRITICAL 级别事件优先 |
|  写入失败 | CircuitBreaker OPEN，30s 后 HALF_OPEN 测试 |
|  内存超限 | MemoryBoundedBatcher 丢弃最老事件 |

---

## 6. 集成指南

### 6.1 FastAPI Middleware 集成

```python
# polaris/delivery/http/middleware/audit_context.py

class AuditContextMiddleware:
    async def __call__(self, request, call_next):
        # 1. 提取或生成 trace IDs
        trace_id = request.headers.get("X-Trace-ID") or uuid.uuid4().hex[:16]
        run_id = request.headers.get("X-Run-ID") or str(uuid.uuid4())
        task_id = request.headers.get("X-Task-ID") or str(uuid.uuid4())

        # 2. 设置 audit context
        async with audit_context_scope(
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
            workspace=str(request.base_url),
        ):
            response = await call_next(request)

        # 3. 响应头添加 trace IDs
        response.headers["X-Trace-ID"] = trace_id
        return response
```

### 6.2 LLM Provider 集成

```python
# runtime_invoke.py 修改

async def invoke_role_runtime_provider(*, role, workspace, prompt, ...):
    tracker = LLMCallTracker(
        model=model,
        provider=provider,
        role=role,
        strategy=LLMStrategy.PRIMARY,
    )

    async with tracker:
        result = await KernelLLM(...).invoke_role_provider(...)
        tracker.add_response(
            completion=result.text,
            finish_reason=result.finish_reason,
            completion_tokens=result.usage.get("completion_tokens", 0),
        )

    return result
```

### 6.3 LLM Call Decorator

```python
@llm_audit(model="claude-3-sonnet", provider="anthropic", role="director")
async def generate_response(prompt: str) -> str:
    return await llm.generate(prompt)
```

---

## 7. 使用示例

### 7.1 发射审计事件

```python
from polaris.kernelone.audit.omniscient import emit_llm_event

await emit_llm_event(
    model="claude-3-sonnet",
    provider="anthropic",
    prompt_tokens=500,
    completion_tokens=200,
    latency_ms=1500.0,
    strategy=LLMStrategy.PRIMARY,
    role="director",
    workspace="/path/to/workspace",
)
```

### 7.2 LLM 调用追踪

```python
from polaris.kernelone.audit.omniscient.interceptors import LLMCallTracker

async with LLMCallTracker.track(
    model="claude-3",
    provider="anthropic",
    role="director",
) as tracker:
    result = await llm.generate(prompt)
    tracker.add_response(result.text, finish_reason="stop")
```

### 7.3 查询审计日志

```python
from polaris.kernelone.audit.omniscient import AuditQueryEngine

engine = AuditQueryEngine(runtime_root=Path("/path/to/runtime"))

# 按 trace_id 查询
events = await engine.query_by_trace_id("abc123")

# 按时间范围查询
events = await engine.query_by_time_range(
    start_date=datetime(2026, 4, 1),
    end_date=datetime(2026, 4, 4),
)

# 分页
result = await engine.query_by_trace_id("abc123", offset=0, limit=100)
```

### 7.4 获取指标

```python
from polaris.kernelone.audit.omniscient.metrics import get_metrics_collector

collector = get_metrics_collector()
stats = collector.get_stats()

# Prometheus 格式输出
metrics_output = collector.format_prometheus()
```

---

## 8. 质量验收

### 8.1 代码规范

```bash
# Ruff check
ruff check polaris/kernelone/audit/omniscient/ --fix

# Ruff format
ruff format polaris/kernelone/audit/omniscient/

# Mypy strict
mypy polaris/kernelone/audit/omniscient/ --strict
```

### 8.2 验收标准

| 检查项 | 标准 |
|--------|------|
| `ruff check` | 0 errors, 0 warnings |
| `ruff format` | All files formatted |
| `mypy --strict` | Success: no issues found |
| Schema 兼容性 | Pydantic validation 通过 |
| Context 传播 | trace_id 在 async 边界不丢失 |
| 脱敏 | 敏感字段被 `[REDACTED]` 替换 |
| 分区 | 文件写入正确路径 |
| CircuitBreaker | 故障时正确 OPEN/HALF_OPEN |

---

## 9. 下一步计划

### 9.1 已完成 (2026-04-04)

- [x] 架构蓝图设计
- [x] Pydantic Schema 实现（LLMEvent, ToolEvent, etc.）
- [x] UnifiedAuditContext 统一 Context 管理
- [x] OmniscientAuditBus 事件总线
- [x] KernelRuntimeAdapter 异步批量写入 + 文件分区
- [x] SanitizationHook PII 脱敏
- [x] LLMCallTracker / @llm_audit 非侵入式拦截器
- [x] LLMProviderAuditInterceptor provider runtime 集成
- [x] AuditContextMiddleware FastAPI 集成
- [x] AuditQueryEngine 高效查询接口
- [x] AuditMetricsCollector Prometheus 指标
- [x] SchemaRegistry schema 版本管理
- [x] HA 防御战术（StormDetector, MemoryBoundedBatcher, CircuitBreaker）
- [x] 全量代码规范验证

### 9.2 后续优化方向

| 方向 | 说明 | 优先级 | 状态 |
|------|------|--------|------|
| 端到端测试 | 验证 trace_id 从 request 到 LLM 调用到落盘全链路 | P0 | ✅ 已完成 (2026-04-04) |
| Benchmark 集成 | 将审计指标接入已有 Benchmark 框架 | P1 | ✅ 已完成 (2026-04-04) |
| 告警集成 | 基于 storm level 的动态告警 | P1 | ✅ 已完成 (2026-04-04) |
| 长期存储 | 冷热分层，离谱事件归档到对象存储 | P2 | ✅ 已完成 (2026-04-04) |
| 分布式追踪 | 接入 OpenTelemetry / Jaeger | P2 | ✅ 已完成 (2026-04-04) |

---

## 附录 A: Commit Messages

本次实现的 commit messages：

```
# Expert A - FastAPI Middleware
feat(middleware): add AuditContextMiddleware for trace context propagation

# Expert B - LLM Provider Integration
feat(audit): integrate LLM provider runtime with Omniscient Audit System

# Expert C - Query Engine
feat(audit): Add AuditQueryEngine for partitioned audit log queries

# Expert D - Metrics & Schema Registry
feat(omniscient): Add AuditMetricsCollector and SchemaRegistry
```

## 附录 B: 相关文档

| 文档 | 路径 |
|------|------|
| 架构标准 | `docs/AGENT_ARCHITECTURE_STANDARD.md` |
| KernelOne 规范 | `docs/KERNELONE_ARCHITECTURE_SPEC.md` |
| Cells YAML | `docs/graph/catalog/cells.yaml` |
| 已有审计报告 | `docs/audit/OMNISCENT_AUDIT_ARCHITECTURE_20260403.md` |

---

**本文档由 Claude Code 基于全知之眼重构计划生成**
**最后更新: 2026-04-04**
