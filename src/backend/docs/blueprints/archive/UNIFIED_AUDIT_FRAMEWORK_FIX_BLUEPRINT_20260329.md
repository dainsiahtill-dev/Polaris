# 统一审计框架修复蓝图

**版本**: v1.0
**日期**: 2026-03-29
**目标**: 让 benchmark / CLI / 其他模式共用同一套 audit 事件写入和查询路径

---

## 1. 架构现状图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        benchmark 调用链                              │
│  tool_calling_matrix.py                                            │
│    → execute_role_session_command()                                  │
│      → RoleRuntimeService.execute_role_session()                    │
│        → kernel.run()                    [写 journal ✅, 写 role 事件 ❌]│
│                                                                  │
│  tool_calling_matrix.py                                            │
│    → stream_role_session_command()                                │
│      → RoleRuntimeService.stream_chat_turn()                       │
│        → kernel.run_stream()            [写 journal ✅, 无 bus ❌]   │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                          CLI 调用链                                  │
│  RoleConsoleHost.stream_turn()                                     │
│    → @audit_stream_turn()              [写 bus ✅, archival ✅]     │
│      → kernel.run_stream()              [写 journal ✅]              │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                       audit_quick.py                                │
│  只查: runtime/audit/audit-*.jsonl (AuditStore)                   │
│  查不到: journal.*.jsonl / strategy_runs/*.json                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phase 1: 在 `stream_chat_turn()` 内联 audit 逻辑

### 2.1 目标
让 `RoleRuntimeService.stream_chat_turn()` 与 CLI 的 `@audit_stream_turn` 装饰器有相同的 audit 能力：
- 通过 MessageBus 发布 `audit.stream.turn` 事件
- 通过 StreamArchiver 归档原始流事件

### 2.2 改动文件

**文件 A**: `polaris/cells/roles/runtime/public/service.py`

**改动 1**: 增加 import（`stream_chat_turn` 方法上方）

```python
from polaris.kernelone.events.message_bus import Message, MessageType
from polaris.kernelone.events.registry import get_global_bus
from polaris.cells.archive.run_archive.internal.stream_archiver import create_stream_archiver
```

**改动 2**: `stream_chat_turn()` 方法

在 `kernel = self._get_kernel(command.workspace)` 之前插入：

```python
# Audit: 初始化流事件归档
turn_uuid = uuid.uuid4().hex
events_for_archive: list[dict[str, Any]] = []
bus = get_global_bus()

# 收集第一个 fingerprint event
fingerprint_event = {
    "type": "fingerprint",
    "profile_id": run_ctx.profile_id,
    "profile_hash": run_ctx.profile_hash,
    "bundle_id": run_ctx.bundle_id,
    "bundle_version": run_ctx.bundle_version,
    "run_id": run_ctx.run_id,
    "turn_index": run_ctx.turn_index,
}
events_for_archive.append(fingerprint_event)

# 通过 bus 发布 fingerprint
if bus is not None:
    bus_msg = Message(
        type=MessageType.USER,
        sender="roles.runtime",
        recipient=None,
        payload={
            "turn_id": turn_uuid,
            "session_id": command.session_id,
            "event_type": "fingerprint",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": fingerprint_event,
        },
    )
    await bus.publish(bus_msg)
```

在 `yield dict(event)` 之前插入：

```python
# Audit: 通过 bus 发布事件
if bus is not None:
    bus_msg = Message(
        type=MessageType.USER,
        sender="roles.runtime",
        recipient=None,
        payload={
            "turn_id": turn_uuid,
            "session_id": command.session_id,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": dict(event),
        },
    )
    await bus.publish(bus_msg)

# Audit: 收集事件用于归档
events_for_archive.append({"type": event_type, "data": dict(event)})
```

在 `finally` 块末尾插入归档逻辑：

```python
# Audit: 归档流事件
if events_for_archive and command.workspace:
    try:
        archiver = create_stream_archiver(command.workspace)
        archive_id = await archiver.archive_turn(
            session_id=command.session_id or "",
            turn_id=turn_uuid,
            events=events_for_archive,
        )
        logger.info(
            "stream_chat_turn archived: turn_id=%s archive_id=%s events=%d",
            turn_uuid, archive_id, len(events_for_archive),
        )
    except Exception as exc:
        logger.warning(
            "stream_chat_turn archival failed (events on bus are safe): "
            "turn_id=%s error=%s",
            turn_uuid, exc,
        )
```

### 2.3 验收标准

- benchmark 运行后，`.polaris/history/runs/*/stream_events.jsonl.gz` 有数据
- `audit_quick.py events` 能查到 bus 事件（如果 bus 可用）

---

## 3. Phase 2: `audit_quick.py` 支持 journal 和 strategy_runs

### 3.1 目标

`audit_quick.py events --journal` 能自动发现并解析：
- `runtime/runs/*/logs/journal.norm.jsonl`（journal 事件）
- `runtime/strategy_runs/*.json`（strategy receipt 元数据）

### 3.2 改动文件

**文件 B**: `polaris/cells/audit/diagnosis/internal/toolkit/service.py`

增加函数：

```python
def _discover_journal_run_dirs(runtime_root: Path) -> list[Path]:
    """Discover all journal run directories under runtime_root."""
    runs_root = runtime_root / "runs"
    if not runs_root.is_dir():
        return []
    return sorted([d for d in runs_root.iterdir() if d.is_dir()], key=lambda d: d.stat().st_mtime, reverse=True)


def _resolve_journal_events_path(run_dir: Path) -> Path | None:
    """Resolve the normalized journal events file for a run directory."""
    logs_dir = run_dir / "logs"
    if not logs_dir.is_dir():
        return None
    candidates = [
        logs_dir / "journal.norm.jsonl",
        logs_dir / "journal.enriched.jsonl",
        logs_dir / "journal.raw.jsonl",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def load_journal_events(run_dir: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Load the most recent events from a journal file."""
    journal_path = _resolve_journal_events_path(run_dir)
    if journal_path is None or not journal_path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with open(journal_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return events[-limit:] if len(events) > limit else events
```

**改动 `_collect_runtime_event_inventory()`**:
在 `source_files` 字典中增加 `"journal": []` 和 `"strategy_receipts": []` 条目，自动扫描并统计。

**文件 C**: `polaris/delivery/cli/audit/audit_quick.py`

在 `_collect_runtime_event_inventory()` 调用后，`events` 命令处理逻辑中，
增加 journal 数据的聚合输出到结果中。

---

## 4. Phase 3: `_emit_llm_event_to_disk` 路由修复（可选）

### 4.1 目标

`_emit_llm_event_to_disk()` 当前写往 `runtime/events/{role}.llm.events.jsonl`，
在 benchmark sandbox 中该路径不存在导致静默失败。

改为直接使用 `LogPipelineLLMRealtimeBridge`，统一写到 `runtime/runs/{run_id}/logs/`。

### 4.2 改动文件

**文件 D**: `polaris/cells/roles/kernel/internal/kernel.py`

修改 `_emit_llm_event_to_disk()` 方法体，将：
1. 替换 `kernel_io_events.emit_llm_event()` 调用
2. 改用 `get_llm_realtime_bridge()` 直接 emit

该改动影响范围广，建议单独 PR 处理。

---

## 5. 工程师分工建议

| 工程师 | 负责 | 文件 |
|--------|------|------|
| Engineer 1 | Phase 1A: import + bus publish | `service.py` |
| Engineer 2 | Phase 1B: archival logic in `stream_chat_turn` | `service.py` |
| Engineer 3 | Phase 1C: 验证 + 单元测试 | `service.py` + tests |
| Engineer 4 | Phase 2A: journal 发现函数 | `toolkit/service.py` |
| Engineer 5 | Phase 2B: `audit_quick.py` journal 输出集成 | `audit_quick.py` |
| Engineer 6 | Phase 3: `_emit_llm_event_to_disk` 路由 | `kernel.py` |

---

## 6. 测试计划

### 6.1 单元测试
- `test_stream_chat_turn_audit_events_collected`: 验证 `stream_chat_turn` 收集事件
- `test_stream_chat_turn_audit_bus_publish`: 验证 bus publish 被调用
- `test_stream_chat_turn_audit_archival`: 验证 archival 触发

### 6.2 集成测试
- 运行 `python -m polaris.delivery.cli agentic-eval --suite tool_calling_matrix --level l7 --role director`
- 检查 `workspace/.polaris/history/runs/*/stream_events.jsonl.gz` 存在且非空
- 检查 `workspace/.polaris/runtime/runs/*/logs/journal.norm.jsonl` 存在且包含 tool_call 事件
- 运行 `audit_quick.py events --journal` 验证数据可查

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| `stream_chat_turn` 中 import `create_stream_archiver` 导致循环依赖 | 使用 lazy import 在函数内部 import |
| bus publish 在 benchmark 中不可用（无全局 bus） | 检查 `bus is not None`，降级到仅归档 |
| archival 失败导致 benchmark 流程中断 | archival 必须在 try/except 中，永远不阻止 yield |
