# 统一审计框架修复执行计划

**版本**: v1.0
**日期**: 2026-03-29
**执行团队**: 6 名高级 Python 工程师

---

## 执行摘要

| Phase | 描述 | 工程师 | 文件 | 预估行数 |
|-------|------|--------|------|---------|
| 1A | import + bus publish in `stream_chat_turn` | Engineer 1 | `service.py` | ~30行 |
| 1B | archival logic in `stream_chat_turn` | Engineer 2 | `service.py` | ~40行 |
| 1C | 单元测试 | Engineer 3 | `service.py` test | ~80行 |
| 2A | journal 发现函数 | Engineer 4 | `toolkit/service.py` | ~60行 |
| 2B | `audit_quick.py` journal 集成 | Engineer 5 | `audit_quick.py` | ~30行 |
| 3 | `_emit_llm_event_to_disk` 路由 | Engineer 6 | `kernel.py` | ~25行 |

---

## Phase 1: `stream_chat_turn()` 内联 audit 逻辑

### Engineer 1: bus publish 逻辑

**文件**: `polaris/cells/roles/runtime/public/service.py`

**步骤 1.1**: 在 `stream_chat_turn` 方法（line ~1257）开头，找到这行：
```python
kernel = self._get_kernel(command.workspace)
```

在它之前插入归档初始化和 bus 获取：
```python
# Audit: 初始化流事件归档
turn_uuid = uuid.uuid4().hex
events_for_archive: list[dict[str, Any]] = []
bus = get_global_bus()

# 收集 fingerprint event（第一个 yield 事件）
fingerprint_payload = {
    "type": "fingerprint",
    "profile_id": run_ctx.profile_id,
    "profile_hash": run_ctx.profile_hash,
    "bundle_id": run_ctx.bundle_id,
    "bundle_version": run_ctx.bundle_version,
    "run_id": run_ctx.run_id,
    "turn_index": run_ctx.turn_index,
}
events_for_archive.append({"type": "fingerprint", "data": fingerprint_payload})

# 通过 bus 发布 fingerprint（等效于 @audit_stream_turn）
if bus is not None:
    from polaris.kernelone.events.message_bus import Message as BusMessage
    bus_msg = BusMessage(
        type=MessageType.USER,
        sender="roles.runtime",
        recipient=None,
        payload={
            "turn_id": turn_uuid,
            "session_id": command.session_id or "",
            "event_type": "fingerprint",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": fingerprint_payload,
        },
    )
    await bus.publish(bus_msg)
```

**步骤 1.2**: 找到 `yield dict(event)`（在 `kernel.run_stream()` 的 async for 循环中，line ~1341），
在它之前插入：

```python
                # Audit: 通过 bus 发布事件
                safe_event = dict(event)
                if bus is not None:
                    from polaris.kernelone.events.message_bus import Message as BusMessage
                    bus_msg = BusMessage(
                        type=MessageType.USER,
                        sender="roles.runtime",
                        recipient=None,
                        payload={
                            "turn_id": turn_uuid,
                            "session_id": command.session_id or "",
                            "event_type": event_type,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "data": safe_event,
                        },
                    )
                    await bus.publish(bus_msg)

                # Audit: 收集事件用于归档
                events_for_archive.append({"type": event_type, "data": safe_event})
```

**步骤 1.3**: 在文件顶部 import 区，增加：
```python
from datetime import timezone
```

（确认 `datetime` 已有，增加 `timezone`）

---

### Engineer 2: archival 逻辑

**文件**: `polaris/cells/roles/runtime/public/service.py`

**步骤 2.1**: 在 `finally:` 块（line ~1340）的末尾，`# WS2: Mark run ended...` 注释之前，插入：

```python
            # Audit: 归档流事件（fire-and-forget，永远不阻止主流程）
            if events_for_archive and command.workspace:
                try:
                    from polaris.cells.archive.run_archive.internal.stream_archiver import (
                        create_stream_archiver,
                    )
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
                    # Architecture constraint: archival failure never breaks main flow
                    logger.warning(
                        "stream_chat_turn archival failed (events on bus are safe): "
                        "turn_id=%s error=%s",
                        turn_uuid, exc,
                    )
```

---

### Engineer 3: 单元测试

**文件**: `polaris/cells/roles/runtime/public/tests/test_stream_chat_turn_audit.py`（新建）

测试用例：
1. `test_stream_chat_turn_collects_events_for_archive` - 验证 events_for_archive 收集
2. `test_stream_chat_turn_publishes_to_bus_when_available` - 验证 bus publish
3. `test_stream_chat_turn_skips_bus_when_unavailable` - 验证 bus=None 降级
4. `test_stream_chat_turn_archival_called_on_completion` - 验证 archival 调用
5. `test_stream_chat_turn_archival_failure_does_not_break_stream` - 验证 archival 异常不外溢

---

## Phase 2: `audit_quick.py` journal 支持

### Engineer 4: journal 发现函数

**文件**: `polaris/cells/audit/diagnosis/internal/toolkit/service.py`

**步骤 4.1**: 在 `_resolve_failure_hops_events_path()` 函数（line ~240）之后，增加：

```python
def _discover_journal_run_dirs(runtime_root: Path) -> list[Path]:
    """Discover all journal run directories under runtime_root.

    Returns run directories sorted by mtime descending (newest first).
    """
    runs_root = runtime_root / "runs"
    if not runs_root.is_dir():
        return []
    dirs: list[tuple[float, Path]] = []
    for d in runs_root.iterdir():
        if not d.is_dir():
            continue
        try:
            mtime = d.stat().st_mtime
        except OSError:
            mtime = 0.0
        dirs.append((mtime, d))
    dirs.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in dirs]


def _resolve_journal_events_path(run_dir: Path) -> Path | None:
    """Resolve the normalized journal events file for a run directory.

    Checks preference order: norm > enriched > raw.
    """
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
    """Load the most recent *limit* events from a journal file.

    Returns events in ascending time order.
    """
    journal_path = _resolve_journal_events_path(run_dir)
    if journal_path is None or not journal_path.exists():
        return []
    all_events: list[dict[str, Any]] = []
    try:
        with open(journal_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    all_events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    # Return the most recent *limit* events
    start = max(0, len(all_events) - limit)
    return all_events[start:]


def discover_strategy_receipts(runtime_root: Path) -> list[Path]:
    """Discover all strategy receipt JSON files under runtime_root."""
    receipts_root = runtime_root / "strategy_runs"
    if not receipts_root.is_dir():
        return []
    return sorted(receipts_root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
```

**步骤 4.2**: 在 `_collect_runtime_event_inventory()`（line ~612）函数中，
在 `source_files` 字典初始化处增加 journal 和 strategy_receipts：

```python
    source_files: dict[str, list[Path]] = {
        "audit": [],
        "runtime": [],
        "role": [],
        "journal": [],       # 新增
        "strategy_receipts": [],  # 新增
    }
```

在 `roles_dir` 扫描块之后增加：

```python
    # journal 源: runtime/runs/*/logs/journal.norm.jsonl
    journal_dirs = _discover_journal_run_dirs(resolved_root)
    for run_dir in journal_dirs:
        journal_path = _resolve_journal_events_path(run_dir)
        if journal_path is not None and journal_path.exists():
            source_files["journal"].append(journal_path)

    # strategy_receipts 源: runtime/strategy_runs/*.json
    receipts = discover_strategy_receipts(resolved_root)
    source_files["strategy_receipts"].extend(receipts)
```

---

### Engineer 5: `audit_quick.py` journal 集成

**文件**: `polaris/delivery/cli/audit/audit_quick.py`

**步骤 5.1**: 找到 `_collect_runtime_event_inventory()` 的返回值处理（大约 line ~674），
在 `by_source` 构建处，确保新增的 `"journal"` 和 `"strategy_receipts"` 也被处理（沿用现有的按 source 循环统计逻辑，无需特殊处理）。

**步骤 5.2**: 在 `events` 命令的处理函数中（大约 line ~600-610 `_tail_jsonl_events` 调用处），
当 `audit_store` 查询返回空时，增加 journal 回退：

在 `tail` handler 函数中（大约 line ~580 `def _tail_jsonl_events` 附近），
创建一个新的 `_load_journal_tail()` 函数专门处理 journal 格式，
并在 `run_audit_command` 的 `tail` handler 中，在 AuditStore 返回空时调用它。

（具体改动取决于 `run_audit_command` 的结构，建议 Engineer 5 先读 `run_audit_command` 的 tail handler）

---

## Phase 3: `_emit_llm_event_to_disk` 路由修复

### Engineer 6: 路由到 journal

**文件**: `polaris/cells/roles/kernel/internal/kernel.py`

**步骤 6.1**: 找到 `_emit_llm_event_to_disk()`（line ~231），读取当前实现。

**步骤 6.2**: 替换方法体中的路径解析逻辑。
当前写往 `{workspace}/.polaris/runtime/events/{role}.llm.events.jsonl`，
改为通过 `LogPipelineLLMRealtimeBridge` 写到 `runtime/runs/{run_id}/logs/`。

关键改动：
1. 用 `get_llm_realtime_bridge()` 获取 bridge
2. 调用 `bridge.publish(LLMRealtimeEvent(...))` 而不是 `kernel_io_events.emit_llm_event()`

注意：需要保留旧路径作为 fallback，以防 bridge 不可用。

---

## 验证步骤（所有工程师）

```bash
# 1. 语法检查
cd src/backend
ruff check polaris/cells/roles/runtime/public/service.py
ruff check polaris/cells/audit/diagnosis/internal/toolkit/service.py
ruff check polaris/delivery/cli/audit/audit_quick.py

# 2. 类型检查
mypy polaris/cells/roles/runtime/public/service.py --no-error-summary
mypy polaris/cells/audit/diagnosis/internal/toolkit/service.py --no-error-summary

# 3. 运行 benchmark
python -m polaris.delivery.cli agentic-eval \
  --workspace C:/Temp/BenchmarkTest \
  --suite tool_calling_matrix \
  --level l7 --role director --observable

# 4. 验证 journal 存在
find C:/Temp/BenchmarkTest -name "journal.norm.jsonl" -type f

# 5. 验证 archive 存在
find C:/Temp/BenchmarkTest -name "stream_events.jsonl.gz" -type f

# 6. 验证 audit_quick 能查到
python -m polaris.delivery.cli.audit.audit_quick events \
  --root C:/Temp/BenchmarkTest --journal
```

---

## 约束与注意事项

1. **所有 import 必须 lazy**（在函数内部 import）以避免循环依赖
2. **archival 异常必须被吞掉**，永远不能阻止 stream yield
3. **bus publish 失败必须被吞掉**，降级到仅归档
4. **不改变** `stream_chat_turn()` 的方法签名和返回类型
5. **不改变** `audit_quick.py` 对非 journal 模式的已有行为
