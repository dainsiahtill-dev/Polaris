# 蓝图：修复 Benchmark 流程中 Journal Sink 未注册导致事件丢失

**状态**: 草稿
**日期**: 2026-04-01
**优先级**: P0
**执行团队**: Python 架构与代码治理实验室

---

## 1. 问题摘要

`l7_context_switch_with_memory` 等 benchmark case 执行后，`audit_quick --journal` 只能读到 2 条 LLM 生命周期事件（`llm_call_start/end`），而 Benchmark 框架内部采集到了完整的 10 条工具调用链事件（`fingerprint`/`tool_call`/`tool_result`/`complete`）。

**核心现象**：Journal 文件中缺失所有 `runtime.event.stream` 类型事件（`tool_call`/`tool_result`/`content_chunk`/`complete`）。

---

## 2. 根因分析

### 2.1 调用链路对比

```
Benchmark 执行时（in-process）
  kernel.run_stream()
    └── UEPEventPublisher(bus=None)           ← 新实例，无 bus
          └── _get_adapter() → None            ← TypedEventBusAdapter 未初始化
                └── _get_bus() → None         ← 全局 MessageBus 未设置
                     └── publish_stream_event() → ❌ 事件被静默丢弃（无日志！）

Benchmark 内部订阅
  stream_role_session_command()
    └── 内部 event collector                  ← 直接接收 TurnEngine yields
          └── captured_events (10条)          ✅ 完整

离线审计
  audit_quick --journal
    └── 读 journal.*.jsonl                  → 只有 2 条 llm 事件
```

### 2.2 根本原因

**`agentic_eval.py` 只调用了 `ensure_minimal_kernelone_bindings()`，从未调用 `assemble()` 或 `assemble_core_services()`。**

| bootstrap 函数 | agentic_eval 是否调用 | 影响 |
|---|---|---|
| `ensure_minimal_kernelone_bindings()` | ✅ 是 | Storage adapter, workspace metadata |
| `set_global_bus(MessageBus())` | ❌ 否 | MessageBus 全局实例未创建 |
| `_ensure_typed_event_bridge()` | ❌ 否 | TypedEventBusAdapter 未初始化 |
| `_register_uep_sinks()` | ❌ 否 | JournalSink 未订阅 MessageBus |

### 2.3 事件丢弃路径

1. `kernel.run_stream()` 创建 `UEPEventPublisher(bus=None)` — 无 MessageBus
2. `publish_stream_event()` 调用 `_get_adapter()` → `get_default_adapter()` → `None`
3. 进入 fallback `_publish_stream_to_bus()` → `_get_bus()` → `None`（因为全局 bus 未设置）
4. **事件被静默丢弃**（`_publish_stream_to_bus` 只打 `logger.debug`，且只有 2 行日志）：
   ```python
   if bus is None:
       logger.debug("UEP stream event dropped (no bus): ...")  # ← benchmark 不开 debug
       return False
   ```
5. Benchmark 框架仍然能工作，因为它的 event collector 直接订阅 `stream_role_session_command` 的 yields，与 MessageBus 完全解耦。

---

## 3. 修复方案

### 3.1 方案 A：让 agentic_eval 调用完整的 bootstrap（推荐）

在 `agentic_eval.py` 中，将 `ensure_minimal_kernelone_bindings()` 替换为 `assemble_core_services()`。

**优点**：
- 复用现有 bootstrap 链路，最小改动
- TypedEventBusAdapter + UEP Sinks 全部正确初始化
- 一次性修复所有事件链路问题

**缺点**：
- agentic_eval 会拉起完整的 DI 容器，可能影响冷启动速度
- 需要确认 `assemble_core_services` 在 benchmark 场景下无副作用

### 3.2 方案 B：在 UEPEventPublisher 中添加安全总线兜底

当 `_get_bus()` 返回 `None` 时，不静默丢弃，而是落盘到临时文件或打印警告。

**优点**：
- 不依赖 bootstrap 完整性
- 对所有 UEPEventPublisher 调用路径生效

**缺点**：
- 只是兜底，不能替代 Journal Sink 的规范化落盘
- 引入新的落盘机制（额外的复杂度）

### 3.3 方案 C：在 benchmark 流程中显式注册 Journal Sink

在 benchmark suite 启动前，显式创建一个 MessageBus 并注册 JournalSink。

**优点**：
- 不需要拉起完整 bootstrap
- 精准修复 benchmark 场景

**缺点**：
- 每次 benchmark 都新建 sink，架构不一致
- 其他调用路径仍然有问题

### 3.4 推荐方案：A（最小化改动，一次修复）

---

## 4. 实施细节

### 4.1 修改文件

**`polaris/delivery/cli/agentic_eval.py`**

```python
# 修改前
from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings
# ...
ensure_minimal_kernelone_bindings()

# 修改后
from polaris.bootstrap.assembly import assemble_core_services
# ...
assemble_core_services(container=None, settings=None)
```

同时需要：
1. 导入 `DIContainer` 和 `Settings`
2. 确保 `assemble_core_services` 内部能容忍 `container=None`（当前代码已支持：`container.resolve(MessageBus) if container.has_registration(MessageBus) else MessageBus()`）

### 4.2 验证方法

```bash
python -m polaris.delivery.cli agentic-eval \
  --workspace C:/Temp/BenchmarkTest-stream/ \
  --suite tool_calling_matrix \
  --role director \
  --observable \
  --matrix-transport stream

# 然后检查 journal 文件
python -m polaris.delivery.cli.audit.audit_quick \
  --root "X:/.polaris/projects/l7-context-switch-with-memory-700ab4dac4ac/runtime" \
  events --journal -f json
```

**预期结果**：
- Journal 文件中包含完整的 `tool_call`/`tool_result`/`content_chunk`/`complete` 事件
- `audit_quick --journal` 能读到 >= 10 条事件（而非现在的 2 条）

---

## 5. 风险评估

| 风险 | 级别 | 缓解措施 |
|---|---|---|
| `assemble_core_services` 在 benchmark 场景下有副作用 | 低 | 先在不传参数的情况下测试，确认无破坏 |
| bootstrap 拉起时间影响 benchmark 冷启动 | 低 | benchmark 本身启动慢（LLM 调用），bootstrap 占比可忽略 |
| DI 容器与现有 benchmark 逻辑冲突 | 低 | `container=None` 时走默认值路径，已有测试覆盖 |

---

## 6. 测试策略

1. **集成测试**：运行 `agentic_eval` 并检查 journal 文件事件数量
2. **回归测试**：确保现有 benchmark 评分不变（事件数量增加不应改变评分逻辑）
3. **边界测试**：在无 LLM 环境下验证 graceful degradation
