# 统一审计框架审计报告

**日期**: 2026-03-29
**问题级别**: P0 - 关键缺陷
**影响**: benchmark 运行时所有工具调用事件零落盘，无法回放分析
**状态**: 已定位，修复中

---

## 1. 问题现象

`audit_quick.py` 对 benchmark 运行目录返回 **0 条事件**，即使 LLM 产生了大量工具调用：

```
$ python -m polaris.delivery.cli.audit.audit_quick --root C:/Temp/BenchmarkTest events
Events: 0 (mode: offline)

[诊断] 未找到审计事件
可能原因: 审计事件存储在 C:\TempBenchmarkTest/audit/audit-*.jsonl
         但该目录可能为空或不存在
```

但实际 benchmark 运行目录中，`strategy_runs/*.json` 有数据，`journal.*.jsonl` 有数据——只是 `audit_quick.py` 查不到。

---

## 2. 根因分析

### 2.1 运行时产物路径现状

benchmark 运行时产生四套并行的产物：

| 产物 | 路径 | 写入组件 | benchmark 有数据？ |
|------|------|---------|----------------|
| AuditStore | `runtime/audit/audit-*.jsonl` | `AuditStore.append()` + HMAC 链 | ❌ 空 |
| LLM journal | `runtime/runs/{id}/logs/journal.*.jsonl` | `LogPipelineLLMRealtimeBridge` | ✅ 有 |
| Role 事件 | `runtime/events/{role}.llm.events.jsonl` | `_emit_llm_event_to_disk()` | ❌ 目录不存在（静默失败） |
| Strategy Receipt | `runtime/strategy_runs/*.json` | `StrategyReceiptEmitter` | ✅ 有 |

**根本原因**: `audit_quick.py` 硬编码只查 `runtime/audit/audit-*.jsonl`（AuditStore），对 benchmark 场景永远为空。

### 2.2 写入链路断点

benchmark 调用链（无 audit）:

```
tool_calling_matrix.py
  → stream_role_session_command()
  → RoleRuntimeService.stream_chat_turn()     ← 没有 @audit_stream_turn 装饰
  → kernel.run_stream()
  → _emit_stream_log_event()                 ← 写 journal.*.jsonl（✅ 有数据）
  → _emit_runtime_llm_event()                ← 写 role 事件（❌ 静默失败）
```

CLI 调用链（有 audit）:

```
RoleConsoleHost.stream_turn()
  → @audit_stream_turn 装饰器                 ← 有 bus publish + archival
  → kernel.run_stream()                       ← 同样写 journal
```

关键发现：`@audit_stream_turn` 装饰器只应用在 CLI 层（`console_host.py`），不在 runtime service 层。benchmark 调用的是 `stream_role_session_command()`，该路径完全绕过了 `@audit_stream_turn`。

### 2.3 `_emit_llm_event_to_disk` 静默失败

`kernel.py:_emit_llm_event_to_disk()` 调用 `kernel_io_events.emit_llm_event()` 写往：

```
{workspace}/.polaris/runtime/events/{role}.llm.events.jsonl
```

benchmark sandbox workspace 是 `llm_evaluations/{run_id}/sandboxes/{case}/`，其 runtime 目录结构不完整，导致路径解析失败，但异常被 `except Exception: pass` 吞掉，事件静默丢失。

---

## 3. 修复方案

### Phase 1: 在 `stream_chat_turn()` 内联 audit 逻辑（根本修复）

**文件**: `polaris/cells/roles/runtime/public/service.py`

在 `stream_chat_turn()` 的 try 块内，为每个 yielded event 做两件事：

1. **发布到 MessageBus**（`audit.stream.turn` topic）——与 `@audit_stream_turn` 等价
2. **归档到 StreamArchiver**——写入 `.polaris/history/runs/{turn_id}/stream_events.jsonl.gz`

这是 `stream_chat_turn()` 缺失的核心 audit 行为，与 CLI 路径的 `@audit_stream_turn` 装饰器功能对等。

```python
# 内部：收集事件用于归档
from polaris.cells.archive.run_archive.internal.stream_archiver import create_stream_archiver

turn_id = uuid.uuid4().hex
events_archived: list[dict[str, Any]] = []

async for event in kernel.run_stream(...):
    # 1. 发布到 bus（等效于 @audit_stream_turn）
    bus = get_global_bus()
    if bus is not None:
        from polaris.kernelone.events.message_bus import Message
        await bus.publish(Message(
            type=MessageType.USER,
            sender="roles.runtime",
            payload={...}
        ))

    # 2. 归档
    events_archived.append({...})

    yield event

# finally 块：归档
if events_archived:
    archiver = create_stream_archiver(command.workspace)
    await archiver.archive_turn(session_id=..., turn_id=turn_id, events=events_archived)
```

### Phase 2: `audit_quick.py` 支持 journal 和 strategy_runs 读取

**文件**:
- `polaris/cells/audit/diagnosis/internal/toolkit/service.py`
- `polaris/delivery/cli/audit/audit_quick.py`

在 `events` 命令中增加 `--journal` 选项，自动发现并解析：
- `runtime/runs/*/logs/journal.norm.jsonl`（journal 事件）
- `runtime/strategy_runs/*.json`（receipt 元数据）

同时增加 `journal` 数据源到 `_collect_runtime_event_inventory()` 的 source 分类中。

### Phase 3: `_emit_llm_event_to_disk` 路由到 journal 系统（可选）

当前该函数写往 `runtime/events/` 路径，在 benchmark sandbox 中路径不存在导致静默失败。

可改为直接走 `LogPipelineLLMRealtimeBridge`（已在 `ensure_minimal_kernelone_bindings` 中初始化），统一写到 `runtime/runs/{run_id}/logs/journal.*.jsonl`。

---

## 4. 关键文件清单

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `polaris/cells/roles/runtime/public/service.py` | `stream_chat_turn()` 增加 bus publish + archival | P0 |
| `polaris/cells/audit/diagnosis/internal/toolkit/service.py` | 增加 journal 路径解析 | P1 |
| `polaris/delivery/cli/audit/audit_quick.py` | `--journal` 选项，journal 源发现 | P1 |
| `polaris/cells/roles/kernel/internal/kernel.py` | `_emit_llm_event_to_disk` 路由修复（可选） | P2 |

---

## 5. 验证方法

1. 运行 benchmark:
   ```bash
   python -m polaris.delivery.cli agentic-eval \
     --workspace C:/Temp/BenchmarkTest \
     --suite tool_calling_matrix \
     --level l7 --role director --observable
   ```

2. 验证 archive 事件存在:
   ```bash
   find C:/Temp/BenchmarkTest -name "stream_events.jsonl.gz"
   ```

3. 验证 journal 事件存在:
   ```bash
   find C:/Temp/BenchmarkTest -name "journal.norm.jsonl"
   ```

4. 验证 audit_quick 能查到 journal:
   ```bash
   python -m polaris.delivery.cli.audit.audit_quick \
     events --root C:/Temp/BenchmarkTest --journal
   ```

---

## 6. 架构决策记录

### ADR-UNIFORM-AUDIT-001: 统一审计事件存储路径

**状态**: 提议中
**问题**: benchmark 和 CLI 使用不同的 audit 路径，导致审计数据碎片化
**决策**: 所有 audit 事件统一走 `journal.*.jsonl`，AuditStore 仅用于 hash chain 验证
**影响**: 需要协调写入层（`stream_chat_turn` + `@audit_stream_turn`）统一到 journal 路径
