# 可观测性体系建设 Gap 分析报告

**日期**: 2026-04-13
**任务**: #32 - 建立完整可观测性体系
**审计范围**: `polaris/kernelone/` + `polaris/cells/`
**状态**: 分析完成

---

## 1. 执行摘要

当前 Polaris 可观测性体系存在**结构性碎片化**问题：

| 组件 | 实现状态 | 问题 |
|------|---------|------|
| Metrics | 部分实现，3套并行 | `MetricsCollector` / `MetricsRecorder` / `AuditMetricsCollector` |
| Tracing | 4套并行 | `DistributedTracer` / `UnifiedTracer` / `TraceCarrier` / `CognitiveTelemetry` |
| Logging | 2套并行 | `StructuredLogger` / `KernelLogger` |
| 覆盖完整性 | 严重不足 | 关键路径缺乏埋点 |

**核心发现**：
- `polaris/kernelone/observability/` 有基础实现，但未被广泛采用
- 52个 Cell 中，仅 `roles.kernel` 有较完整 metrics
- 关键路径（LLM executor、tool execution、context OS）埋点缺失
- 已有日志审计报告（`log-audit-performance-latency-20260413.md`）发现6大类性能问题

---

## 2. Metrics 体系 Gap 分析

### 2.1 现有 Metrics 实现

| 实现 | 位置 | 用途 | 状态 |
|------|------|------|------|
| `MetricsCollector` | `polaris/kernelone/observability/metrics.py` | 通用指标收集 | 基础实现，未被广泛使用 |
| `MetricsRecorder` | `polaris/kernelone/telemetry/metrics.py` | KernelOne 遥测 | 部分使用 |
| `AuditMetricsCollector` | `polaris/kernelone/audit/omniscient/metrics.py` | 审计事件指标 | 审计系统专用 |
| `MetricsCollector` | `polaris/cells/roles/kernel/internal/metrics.py` | Kernel Cell 指标 | 完整实现 |

### 2.2 Metrics 引用分布

```
polaris/cells/roles/kernel/internal/     ✅ 完整 (metrics.py, turn_runner.py, core.py)
polaris/cells/director/execution/       ✅ 部分 (director_agent.py)
polaris/cells/llm/tool_runtime/         ⚠️ 部分 (role_integrations.py)
polaris/kernelone/context/context_os/    ⚠️ 部分 (runtime.py, pipeline/stages.py, models.py, chunks/assembler.py)
polaris/kernelone/llm/engine/stream/    ⚠️ 部分 (executor.py)
polaris/delivery/http/middleware/       ⚠️ 部分 (metrics.py)
```

### 2.3 关键路径 Metrics 缺失

| 关键路径 | 文件 | 缺失指标 |
|---------|------|---------|
| LLM Provider 调用 | `polaris/kernelone/llm/engine/executor.py` | 调用次数、延迟、错误率、token 用量 |
| Tool Execution | `polaris/kernelone/tool_execution/*.py` | 执行次数、成功率、延迟 |
| Context OS Runtime | `polaris/kernelone/context/context_os/runtime.py` | 内存使用、transcript 处理延迟 |
| Cache Manager | `polaris/kernelone/context/cache_manager.py` | 命中率、内存占用 |
| Storage Operations | `polaris/kernelone/fs/*.py` | I/O 操作延迟、错误率 |
| Message Bus | `polaris/kernelone/events/message_bus.py` | 消息队列深度、处理延迟 |

### 2.4 Metrics 缺口量化

基于静态分析，关键 Cell 的 metrics 覆盖率：

| Cell | 已有指标 | 建议指标 | 覆盖率 |
|------|---------|---------|--------|
| roles.kernel | 15+ | 20+ | 75% |
| director.execution | 3+ | 15+ | 20% |
| llm.engine | 1+ | 10+ | 10% |
| context.engine | 0 | 8+ | 0% |
| storage.layout | 0 | 5+ | 0% |
| archive.* | 0 | 5+ | 0% |

---

## 3. Tracing 体系 Gap 分析

### 3.1 现有 Tracing 实现

| 实现 | 位置 | 用途 | 状态 |
|------|------|------|------|
| `DistributedTracer` | `polaris/kernelone/observability/tracer.py` | 分布式追踪 | 基础实现 |
| `UnifiedTracer` | `polaris/kernelone/trace/tracer.py` | 统一追踪 | 已集成 span 上下文 |
| `TraceCarrier` | `polaris/kernelone/telemetry/trace.py` | 追踪上下文传播 | 用于 cognitive 模块 |
| `CognitiveTelemetry` | `polaris/kernelone/cognitive/telemetry.py` | OpenTelemetry 封装 | 仅 cognitive 子系统 |
| `TracingAuditInterceptor` | `polaris/kernelone/audit/omniscient/interceptors/tracing.py` | 审计→追踪桥接 | 已集成 |

### 3.2 Tracing 引用分布

```
polaris/kernelone/trace/tracer.py          ✅ 自引用
polaris/kernelone/cognitive/               ✅ 完整 (telemetry.py, orchestrator.py)
polaris/kernelone/audit/omniscient/        ✅ 完整 (interceptors/tracing.py)
polaris/cells/roles/kernel/internal/       ✅ 完整 (turn_runner.py, core.py)
polaris/kernelone/multi_agent/             ✅ 部分 (neural_syndicate/trace_context.py)
```

### 3.3 关键路径 Tracing 缺失

| 关键路径 | 文件 | 缺失 Spans |
|---------|------|-----------|
| LLM Executor | `polaris/kernelone/llm/engine/executor.py` | `llm.call`, `llm.stream`, `provider.resolve` |
| Tool Execution | `polaris/kernelone/tool_execution/*.py` | `tool.execute`, `tool.validate`, `tool.plan` |
| Context OS | `polaris/kernelone/context/context_os/runtime.py` | `context.project`, `context.merge`, `context.patch` |
| Turn Engine | `polaris/cells/roles/kernel/internal/turn_engine/engine.py` | `turn.execute`, `turn.validate` |
| Director Agent | `polaris/cells/director/execution/internal/director_agent.py` | `director.execute`, `director.plan` |
| File Operations | `polaris/kernelone/fs/*.py` | `fs.read`, `fs.write`, `fs.list` |

### 3.4 Span 属性缺失

现有 spans 常见问题：

1. **缺少必要 attributes**：
   - `llm.model`, `llm.provider`, `llm.tokens`
   - `tool.name`, `tool.category`
   - `context.occupancy_pct`
   - `agent.role`, `agent.intent`

2. **缺少链路关联**：
   - `trace_id` 未传播到所有子模块
   - `parent_span_id` 在异步边界丢失

---

## 4. Logging 体系 Gap 分析

### 4.1 现有 Logging 实现

| 实现 | 位置 | 用途 | 状态 |
|------|------|------|------|
| `StructuredLogger` | `polaris/kernelone/observability/logger.py` | JSON 结构化日志 | 基础实现 |
| `KernelLogger` | `polaris/kernelone/telemetry/logging.py` | KernelOne 结构化日志 | 较完整 |
| `trace/logger.py` | `polaris/kernelone/trace/logger.py` | 追踪日志 | 自用 |

### 4.2 Logging 引用分布

```
polaris/kernelone/telemetry/logging.py      ✅ 完整 (get_logger 工厂方法)
polaris/kernelone/observability/logger.py    ⚠️ 基础实现
polaris/kernelone/trace/                    ✅ 自用
polaris/cells/                             ⚠️ 仅部分使用 logging.getLogger
```

### 4.3 关键问题

1. **日志级别滥用**：
   - 根据 `log-audit-performance-latency-20260413.md`，存在 `except Exception:` 206 处
   - 可能隐藏真实错误

2. **结构化日志缺失**：
   - 大部分 Cell 使用 `logging.getLogger(__name__)` 而非 `KernelLogger`
   - 缺少 trace_id、span_id 传播

3. **错误日志不完整**：
   - `logger.error()` 调用多未带 `exc_info=True`
   - 缺少堆栈上下文

---

## 5. 关键路径 Gap 详细分析

### 5.1 LLM Executor 路径

**文件**: `polaris/kernelone/llm/engine/executor.py`

```
当前状态:
- 仅使用 logging.getLogger(__name__)
- 无 metrics 埋点
- 无 tracing span

缺失:
- Counter: llm_requests_total{provider, model, status}
- Histogram: llm_latency_seconds{provider, model}
- Counter: llm_errors_total{provider, model, error_type}
- Counter: llm_tokens_total{provider, model, type}
- Span: "llm.call" with provider, model, tokens attributes
```

### 5.2 Tool Execution 路径

**文件**: `polaris/kernelone/tool_execution/*.py`

```
当前状态:
- 仅使用 logging.getLogger(__name__)
- 无 metrics 埋点
- 无 tracing span

缺失:
- Counter: tool_calls_total{tool_name, status}
- Histogram: tool_execution_seconds{tool_name}
- Counter: tool_errors_total{tool_name, error_type}
- Span: "tool.execute" with tool_name, args_hash attributes
```

### 5.3 Context OS 路径

**文件**: `polaris/kernelone/context/context_os/runtime.py`

```
当前状态:
- 使用 threading.RLock（阻塞事件循环）
- N+1 查询模式（多次遍历 transcript）
- 缓存命中时同步刷盘

缺失:
- Gauge: context_memory_bytes
- Counter: context_projections_total
- Histogram: context_merge_latency_seconds
- Histogram: context_patch_latency_seconds
- Span: "context.project", "context.merge", "context.patch"
```

### 5.4 Director Execution 路径

**文件**: `polaris/cells/director/execution/internal/director_agent.py`

```
当前状态:
- 使用 MetricsRecorder 和 Timer
- 但无 tracing span
- 日志级别不一致

缺失:
- Span: "director.execute", "director.plan", "director.apply"
- 完整的 attributes 集合
```

---

## 6. 可观测性体系架构问题

### 6.1 系统碎片化

```
┌─────────────────────────────────────────────────────────────┐
│                    Polaris 可观测性                          │
├─────────────────────────────────────────────────────────────┤
│  Metrics: 4 套实现                                          │
│  ├─ observability/metrics.py (MetricsCollector)           │
│  ├─ telemetry/metrics.py (MetricsRecorder)                │
│  ├─ audit/omniscient/metrics.py (AuditMetricsCollector)   │
│  └─ cells/roles/kernel/internal/metrics.py                  │
├─────────────────────────────────────────────────────────────┤
│  Tracing: 4 套实现                                          │
│  ├─ observability/tracer.py (DistributedTracer)           │
│  ├─ trace/tracer.py (UnifiedTracer)                       │
│  ├─ telemetry/trace.py (TraceCarrier)                      │
│  └─ cognitive/telemetry.py (CognitiveTelemetry)            │
├─────────────────────────────────────────────────────────────┤
│  Logging: 2 套实现                                          │
│  ├─ observability/logger.py (StructuredLogger)            │
│  └─ telemetry/logging.py (KernelLogger)                   │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 收敛建议

**Phase 1: 统一 Metrics**
```
推荐保留: polaris.kernelone.telemetry.metrics.MetricsRecorder
理由: 支持 Prometheus 格式，有 Timer/Counter/Gauge/Histogram
废弃: 
- observability/metrics.py (功能重复)
- roles/kernel/internal/metrics.py (Cell 级专用)
```

**Phase 2: 统一 Tracing**
```
推荐保留: polaris.kernelone.trace.tracer.UnifiedTracer
理由: 支持 span 上下文传播，有装饰器支持
废弃:
- observability/tracer.py (功能重复)
- telemetry/trace.py (仅用于 cognitive)
```

**Phase 3: 统一 Logging**
```
推荐保留: polaris.kernelone.telemetry.logging.KernelLogger
理由: 支持 trace context 传播，JSON 格式
废弃:
- observability/logger.py (功能重复)
```

---

## 7. Roadmap 建议

### 7.1 短期（1-2 周）- 覆盖核心路径

| 优先级 | 任务 | 负责模块 | 指标类型 |
|--------|------|---------|---------|
| P0 | LLM Executor 埋点 | llm/engine | metrics + tracing |
| P0 | Tool Execution 埋点 | tool_execution | metrics + tracing |
| P1 | Context OS 埋点 | context/context_os | metrics + tracing |
| P1 | Director Agent 埋点 | director/execution | tracing |

### 7.2 中期（3-4 周）- 完善 Cell 覆盖

| 优先级 | 任务 | 负责模块 | 指标类型 |
|--------|------|---------|---------|
| P1 | Archive Cell 埋点 | archive/* | metrics |
| P2 | Storage Cell 埋点 | storage/layout | metrics + tracing |
| P2 | Context Engine 埋点 | context/engine | metrics |
| P2 | LLM Provider Runtime 埋点 | llm/provider_runtime | metrics + tracing |

### 7.3 长期（5-8 周）- 体系收敛

| 优先级 | 任务 | 描述 |
|--------|------|------|
| P1 | 统一 metrics 导出 | 废弃重复实现，统一 Prometheus 端点 |
| P1 | 统一 tracing 传播 | 确保 trace_id 跨异步边界传递 |
| P2 | 日志级别标准化 | 消除 bare except，添加 exc_info |
| P2 | 可观测性 CI 门禁 | 添加 observability 质量规则到 fitness-rules.yaml |

---

## 8. 待补充埋点清单

### 8.1 高优先级文件

```
polaris/kernelone/llm/engine/executor.py
polaris/kernelone/llm/engine/runtime.py
polaris/kernelone/tool_execution/executor.py
polaris/kernelone/tool_execution/graph.py
polaris/kernelone/context/context_os/runtime.py
polaris/kernelone/context/cache_manager.py
polaris/kernelone/fs/io_ops.py
polaris/kernelone/events/message_bus.py
polaris/cells/director/execution/internal/director_agent.py
polaris/cells/context/engine/internal/precision_mode.py
polaris/cells/storage/layout/internal/*.py
polaris/cells/archive/*/internal/*.py
```

### 8.2 建议添加的指标

**Counter 类型**:
- `polaris_llm_requests_total{provider, model, status}`
- `polaris_tool_calls_total{tool_name, status}`
- `polaris_context_projections_total`
- `polaris_cache_operations_total{level, operation}`

**Histogram 类型**:
- `polaris_llm_latency_seconds{provider, model}`
- `polaris_tool_execution_seconds{tool_name}`
- `polaris_context_merge_latency_seconds`
- `polaris_context_patch_latency_seconds`

**Gauge 类型**:
- `polaris_context_memory_bytes`
- `polaris_cache_memory_bytes{level}`
- `polaris_message_queue_depth`

**Span 类型**:
- `llm.call` (provider, model, tokens)
- `tool.execute` (tool_name, category)
- `context.project` (transcript_size, occupancy)
- `director.execute` (task_id, role)

---

## 9. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| Metrics 系统收敛导致测试失败 | 高 | 中 | 保留兼容层，逐步迁移 |
| tracing span 过多影响性能 | 中 | 中 | 使用采样策略 |
| 日志级别收紧暴露旧 bug | 高 | 高 | 先添加 log review 门禁 |
| Cell 埋点引入循环依赖 | 中 | 高 | 通过 KernelOne public contract |

---

## 10. 参考文档

- `polaris/kernelone/observability/` - 基础可观测性实现
- `polaris/kernelone/telemetry/` - 遥测模块
- `polaris/kernelone/trace/` - 追踪模块
- `polaris/kernelone/audit/omniscient/` - 审计系统
- `docs/governance/audit/log-audit-performance-latency-20260413.md` - 日志性能审计
- `docs/governance/ci/fitness-rules.yaml` - 治理规则

---

**报告生成**: Claude Code
**审计任务**: #32
