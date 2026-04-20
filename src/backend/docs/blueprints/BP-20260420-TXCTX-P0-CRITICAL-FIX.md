# Blueprint: Transaction Kernel ↔ ContextOS P0 紧急修复

- **蓝图编号**: BP-20260420-TXCTX-P0-CRITICAL-FIX
- **生效日期**: 2026-04-20
- **适用范围**: `polaris/cells/roles/kernel/` + `polaris/kernelone/context/context_os/`
- **强制级别**: MUST（P0 — 本周内完成）
- **关联 ADR**: ADR-0071, ADR-0067, ADR-0080

---

## 1. 问题陈述

### 1.1 致命缺陷：Snapshot 更新链路断裂

当前 Transaction Kernel 执行完一个 Turn 后，`_execute_transaction_kernel_turn()` 仅通过 `_build_turn_history_and_events()` 构建 `turn_history` 和 `turn_events_metadata`，并将它们放入 `RoleTurnResult` 返回给调用方（Orchestrator）。**但 `request.context_override["context_os_snapshot"]` 从未被更新。**

这意味着：
- 下一个 Turn 的 `RoleTurnRequest` 仍携带**旧的** `ContextOSSnapshot`
- `TruthLog` 丢失当前 Turn 的事件，导致多 Turn 对话"失忆"
- 严重违反 AGENTS.md §4.7 "Single State Owner" 和 §17.3 "TruthLog append-only"

### 1.2 生产环境风险：硬编码测试指令

`stream_orchestrator.py:116` 存在硬编码指令，强制 LLM 写入 `requirements.txt`。这在生产环境中会导致不可预期的文件写入。

### 1.3 基础设施阻断：单例锁初始化时机

`CognitiveGateway` 和 `SLMCoprocessor` 在类定义时创建 `asyncio.Lock()`。如果模块导入时事件循环未启动（如某些测试框架或预 fork 场景），会导致 `RuntimeError`。

### 1.4 数据一致性：序列号冲突与竞态

- `pipeline/stages.py:191-194`: 使用浮点 `sub_index` 生成工具调用序列号，但 `int(call_sequence)` 导致所有子调用共享同一整数序列号
- `content_store.py:392-397`: `RefTracker.acquire()` 检查 `ref.hash not in self._store._refs` 后无锁保护，高并发下可能产生引用计数错误

---

## 2. 目标架构

### 2.1 修复后数据流（Turn 生命周期闭环）

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│  Phase 0: Bootstrap (SSOT)                                                  │
│  RoleTurnRequest._post_init()                                               │
│    └─> context_override["context_os_snapshot"] = EMPTY_SNAPSHOT             │
└─────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Phase 1: Context Building                                                  │
│  RoleContextGateway.build_context()                                         │
│    └─> StateFirstContextOS.project(existing_snapshot=snapshot)              │
└─────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Phase 2: Transaction Execution                                             │
│  TurnTransactionController.execute() → ToolBatchExecutor → Finalization     │
└─────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Phase 3: Snapshot Commit  ←── 【新增：本次修复的核心】                      │
│  RoleExecutionKernel._commit_turn_to_snapshot()                             │
│    ├─> merge turn_history into snapshot.transcript_log                     │
│    ├─> merge turn_events_metadata into snapshot.working_state               │
│    ├─> update snapshot.version                                              │
│    └─> write back to request.context_override["context_os_snapshot"]       │
└─────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Phase 4: Result Return                                                     │
│  RoleTurnResult (包含更新的 snapshot 引用)                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Phase 5: Orchestrator Persistence                                          │
│  SessionContinuityEngine 将 snapshot 持久化到 SQLite                        │
│    └─> 下一 Turn 从持久化存储加载最新 snapshot                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责矩阵

| 模块 | 修复职责 | 不变量 |
|------|---------|--------|
| `core.py` | 新增 `_commit_turn_to_snapshot()` 方法；在 `_execute_transaction_kernel_turn()` 末尾调用 | snapshot 必须在 return 前完成更新 |
| `stream_orchestrator.py` | 移除硬编码测试指令；重构为配置驱动 | 生产代码不得包含测试硬编码 |
| `cognitive_gateway.py` | 延迟初始化单例锁 | 锁必须在事件循环启动后创建 |
| `slm_coprocessor.py` | 延迟初始化单例锁 | 同上 |
| `pipeline/stages.py` | 修复序列号生成逻辑 | 每个 tool_call 必须有唯一整数序列号 |
| `content_store.py` | 为 `RefTracker.acquire()` 添加线程锁 | 引用计数必须线程安全 |
| `cells.yaml` | 更新 `roles.kernel` 的 `current_modules` 和 `verification.tests` | Graph 必须反映代码事实 |

---

## 3. 核心数据流修复方案

### 3.1 Snapshot Commit 机制

**设计决策**: 在 `RoleExecutionKernel` 中新增 `_commit_turn_to_snapshot()` 方法，负责将 Turn 结果合并回 `ContextOSSnapshot`。

**理由**:
- `RoleExecutionKernel` 是 Transaction Kernel 的 facade，已经掌握 Turn 的完整输入输出
- 避免在 `TurnTransactionController` 中引入对 `ContextOSSnapshot` 的依赖（保持层间解耦）
- 与现有 `_build_turn_history_and_events()` 形成"构建 + 提交"的完整闭环

**实现要点**:
```python
async def _commit_turn_to_snapshot(
    self,
    request: RoleTurnRequest,
    turn_history: list[tuple[str, str]],
    turn_events_metadata: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> None:
    """Merge turn outcome back into ContextOSSnapshot.

    This is the critical fix for the broken snapshot update chain.
    Without this, the next turn will carry a stale snapshot.
    """
    snapshot_raw = request.context_override.get("context_os_snapshot") if request.context_override else None
    if not isinstance(snapshot_raw, dict):
        return  # Nothing to commit to

    # Build immutable transcript events from turn_history + metadata
    new_events = []
    for (role, content), meta in zip(turn_history, turn_events_metadata):
        event = TranscriptEvent(
            event_id=meta.get("event_id", ""),
            sequence=meta.get("sequence", 0),
            role=role,
            kind=meta.get("kind", "unknown"),
            route=meta.get("route", ""),
            content=content,
            source_turns=(f"t{meta.get('turn_id', '0')}",),
            artifact_id=meta.get("artifact_id"),
            created_at=meta.get("created_at", _utc_now_iso()),
        )
        new_events.append(event)

    # Merge into existing snapshot (append-only)
    existing_events = snapshot_raw.get("transcript_log", [])
    snapshot_raw["transcript_log"] = list(existing_events) + [e.to_dict() for e in new_events]
    snapshot_raw["version"] = snapshot_raw.get("version", 0) + 1
    snapshot_raw["last_updated_at"] = _utc_now_iso()

    # Update working_state with tool_results
    working_state = snapshot_raw.get("working_state", {})
    working_state["last_tool_results"] = tool_results
    snapshot_raw["working_state"] = working_state
```

### 3.2 序列号修复

**问题**: `sub_index = 0.01 * (idx + 1)` + `int(call_sequence)` 导致所有子调用共享同一整数序列号。

**修复**: 使用独立的递增整数计数器，而非浮点 hack。

```python
# Before (buggy)
sub_index = 0.01 * (idx + 1)
call_sequence = seq + sub_index
next_sequence = max(next_sequence, int(call_sequence) + 1)

# After (fixed)
call_sequence = next_sequence
next_sequence += 1
```

### 3.3 ContentStore 竞态修复

**问题**: `RefTracker.acquire()` 中 `self._store._refs` 的读写无锁保护。

**修复**: 在 `ContentStore` 中添加 `threading.RLock`，在 `acquire()` 和 `release()` 中使用。

```python
class ContentStore:
    def __init__(...):
        self._lock = threading.RLock()

    def _intern_locked(self, text: str) -> ContentRef:
        ...

class RefTracker:
    def acquire(self, ref: ContentRef) -> ContentRef:
        with self._store._lock:
            self._active.add(ref.hash)
            if ref.hash not in self._store._refs:
                self._store.intern(self._store._store.get(ref.hash, ""))
        return ref
```

---

## 4. 技术选型理由

### 4.1 为什么在 `RoleExecutionKernel` 中提交 Snapshot？

| 备选方案 | 否决理由 |
|---------|---------|
| 在 `TurnTransactionController` 中提交 | 违反层间边界。Controller 不应感知 `ContextOSSnapshot` 的内部结构 |
| 在 Orchestrator 中提交 | 太远。Orchestrator 不应负责 snapshot 的语义合并 |
| 新增独立的 `SnapshotCommitService` | 过度设计。当前仅需简单的 append-only 合并，无需独立服务 |

### 4.2 为什么使用 `threading.RLock` 而非 `asyncio.Lock`？

`ContentStore` 当前在同步上下文中被调用（`pipeline/stages.py` 的同步方法），且可能被多线程访问（`SLMSummarizer` 的线程池）。`threading.RLock` 是最小侵入的选择。

### 4.3 为什么延迟初始化单例锁？

`asyncio.Lock()` 在 Python 3.10+ 虽然允许在模块导入时创建，但在某些测试框架（如 pytest-asyncio 的 event_loop fixture）或预 fork 服务器（如 uvicorn + gunicorn）中，事件循环可能在 import 后重新创建，导致锁绑定到旧的循环。

---

## 5. 实施计划

### 5.1 任务分解

| 工程师 | 任务 | 文件 | 预计工时 |
|--------|------|------|---------|
| **工程师 A** | Snapshot Commit + 硬编码清理 | `core.py`, `stream_orchestrator.py` | 4h |
| **工程师 B** | 基础设施修复 | `cognitive_gateway.py`, `slm_coprocessor.py`, `pipeline/stages.py`, `content_store.py` | 3h |
| **工程师 C** | 治理更新 + 测试 + 验证 | `cells.yaml`, `test_snapshot_commit.py`, `test_sequence_fix.py`, `test_content_store_threadsafe.py` | 4h |

### 5.2 验证门禁

所有修改必须通过：
1. `ruff check <paths> --fix`
2. `ruff format <paths>`
3. `mypy <paths>`
4. `pytest <tests> -q`

### 5.3 回滚策略

- 所有修改保持向后兼容（新增方法、不改变现有公开接口签名）
- `stream_orchestrator.py` 的硬编码删除是破坏性变更，但属于移除测试代码
- 若发现问题，可通过 revert 单个 commit 回滚

---

## 6. 风险与边界

| 风险 | 缓解措施 |
|------|---------|
| Snapshot 合并逻辑可能与其他代码路径冲突 | 保持 `transcript_log` 的 append-only 语义，不修改历史事件 |
| `cells.yaml` 更新可能触发 catalog governance gate 失败 | 先以 `audit-only` 模式运行 gate，确认无新增 blocker 后再提交 |
| 序列号修复可能影响下游日志解析 | 使用递增整数序列号更自然，下游应已支持；需回归测试验证 |
| ContentStore RLock 可能引入死锁 | RLock 是可重入锁，同一线程多次获取不会死锁 |

---

## 7. 自检清单

- [ ] `core.py` 的 `_commit_turn_to_snapshot()` 是否防御了 `context_os_snapshot` 缺失的情况？
- [ ] `stream_orchestrator.py` 的硬编码是否已完全移除？
- [ ] 单例锁是否使用了延迟初始化？
- [ ] 序列号是否为严格递增整数？
- [ ] ContentStore 的 RLock 是否覆盖了所有 `_refs` 和 `_store` 的读写？
- [ ] `cells.yaml` 的 `current_modules` 是否补充了 `internal/transaction/*`？
- [ ] 新增测试是否覆盖了 Happy Path、Edge Cases 和 Exceptions？
- [ ] 所有修改是否通过了 ruff / mypy / pytest 门禁？

---

*本蓝图由 Principal Architect 于 2026-04-20 签发，所有工程师必须严格按此执行。*
