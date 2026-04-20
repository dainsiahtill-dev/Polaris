# 统一审计框架调用链调查笔记

**日期**: 2026-03-29
**性质**: 内部技术调查，供工程师参考

---

## A. benchmark 运行目录结构分析

```
C:/Temp/BenchmarkTest/
  .polaris/
    runtime/
      llm_evaluations/
        {run_id}/
          sandboxes/
            l7_context_switch/
              .polaris/
                runtime/
                  strategy_runs/
                    {receipt}.json    ← StrategyReceiptEmitter 写入 ✅
              backend/api.py
              config.py
              frontend/app.ts
              server.py
              src/utils/helpers.py
              tests/test_api_real.py
              utils.py
```

**不存在**:
- `runtime/events/` 目录
- `runtime/audit/` 目录

**存在**（在不同的 projects 路径下，非 benchmark sandbox 内）:
```
runtime/.polaris/projects/{slug}-{hash}/runtime/runs/{run_id}/logs/journal.*.jsonl
```

---

## B. journal 写入链路

`kernel.py:run_stream()` (line 653):
1. 调用 `self._build_stream_log_writer(stream_run_id)` → `get_writer(workspace, run_id)`
2. 对每个 event 调用 `self._emit_stream_log_event(writer, ...)`
3. `writer.write_event(...)` → `LogPipelineLLMRealtimeBridge.publish()`

所以 `journal.*.jsonl` 数据来自 `kernel.run_stream()` → `_emit_stream_log_event()`，
benchmark 有数据是因为走了这条路。

---

## C. `_emit_llm_event_to_disk` 静默失败链路

`kernel.py:_emit_runtime_llm_event()` (line 190):
- 调用 `self._emit_llm_event_to_disk(...)`（line 221）

`_emit_llm_event_to_disk()` (line 231):
- 调用 `resolve_runtime_path(self.workspace, "runtime/events/{role}.llm.events.jsonl")`
- `resolve_runtime_path` → `resolve_storage_roots` → `business_roots_resolver`

在 benchmark sandbox (`llm_evaluations/{id}/sandboxes/l7_context_switch`) 中:
- `workspace = sandbox_path`
- `resolve_storage_roots` 尝试通过 `business_roots_resolver` 解析
- 如果 resolver 返回 None → 用 generic 路径: `{sandbox_path}/.polaris/runtime/`
- `runtime_project_root = {sandbox_path}/.polaris/runtime/.polaris/projects/{slug}/{hash}/runtime`

最终路径: `{sandbox_path}/.polaris/runtime/events/{role}.llm.events.jsonl`

但 benchmark `materialize_case_workspace()` 只复制了 fixture 内容，
**没有创建 `.polaris/runtime/events/` 目录**，
所以 `llm_events_path` 解析出来的目录不存在。

`kernel_io_events.emit_llm_event()` 内部:
```python
def emit_llm_event(llm_events_path, ...):
    if not llm_events_path:
        return
    _append_jsonl_via_kernel(llm_events_path, payload)
```

`_append_jsonl_via_kernel()` 内部:
```python
fs = KernelFileSystem(_infer_workspace_for_path(path), ...)
fs.append_jsonl(path, payload)
```

`fs.append_jsonl()` 可能在目录不存在时创建目录，或者抛出异常。
如果是异常，被 `_emit_llm_event_to_disk` 的 `except Exception: pass` 吞掉。

**结论**: 事件确实因为目录不存在而静默丢失。

---

## D. `audit_quick.py` 只查 AuditStore

`audit_quick.py events` 命令 → `run_audit_command("tail", ...)` → `tail_handler()`:

```python
def tail_handler(params, *, runtime_root, workspace):
    facade = AuditUseCaseFacade(runtime_root=runtime_root)
    events = facade.query_logs(...)
    return {"status": "ok", "events": [...]}  # 只从 AuditStore 查
```

**AuditStore** 使用 `KernelAuditRuntime.get_instance()`:
```python
def get_instance(cls, runtime_root: Path) -> KernelAuditRuntime:
    return cls(normalized, create_audit_store(normalized))
```

`create_audit_store` → `AuditStoreAdapter(root)` → `AuditStore(runtime_root=...)`

AuditStore 的 `_audit_dir` = `{runtime_root}/audit/` → 寻找 `audit-*.jsonl`

benchmark sandbox 的 `runtime_root` = `llm_evaluations/{id}/sandboxes/l7_context_switch/.polaris/runtime/`

该目录下没有 `audit/` 子目录，所以查不到任何事件。

---

## E. `@audit_stream_turn` 装饰器分析

装饰器源码: `polaris/delivery/cli/director/audit_decorator.py`

**bus 发布**:
```python
bus.publish(Message(
    type=MessageType.USER,
    sender="audit_stream_turn",
    payload={...}
))
```

**归档**:
```python
archiver = create_stream_archiver(workspace)
archive_id = await archiver.archive_turn(
    session_id=session_id,
    turn_id=turn_id,
    events=events_recorded,
)
```

**应用到 CLI**:
```python
# console_host.py 或 PolarisLazyClaude 中
apply_audit_decorator(host, workspace=str(workspace))
```

`stream_chat_turn()` 没有 `@audit_stream_turn` 装饰，也没有任何等效逻辑。

---

## F. 修复后预期结果

修复后 benchmark 运行预期产物：

| 产物 | 路径 | 验证方法 |
|------|------|---------|
| journal 事件 | `runtime/runs/{id}/logs/journal.norm.jsonl` | ✅ 已有数据 |
| stream archive | `history/runs/{turn_id}/stream_events.jsonl.gz` | 🔧 待修复后验证 |
| strategy receipts | `runtime/strategy_runs/*.json` | ✅ 已有数据 |
| AuditStore | `runtime/audit/audit-*.jsonl` | ❌ 不写入（audit_quick 查到的是 journal） |
