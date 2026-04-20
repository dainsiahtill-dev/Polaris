# Cognitive Life Form & ContextOS/TurnEngine 审计报告

**日期**: 2026-04-09
**执行**: 10 并行 Expert Agent 审计
**范围**: `polaris/kernelone/cognitive/`, `polaris/kernelone/context/`, `polaris/cells/roles/kernel/`

---

## CRITICAL BUGS

### BUG-C01: Stream/Non-Stream Transcript 顺序差异 [CRITICAL]

**严重性**: CRITICAL
**模块**: TurnEngine
**位置**: `polaris/cells/roles/kernel/internal/turn_engine/engine.py:1071` vs `1693`

`run()` 和 `run_stream()` 对同一输入产生不同的 transcript 顺序：
- `run()`: `[user, assistant, tool_result_1, tool_result_2]`
- `run_stream()`: `[user, tool_result_1, tool_result_2, assistant]`

**根因**: `run()` 在工具循环前调用 `append_transcript_cycle`，而 `run_stream()` 在之后调用。

**影响**: 第2轮 LLM 调用的上下文不同，导致不同工具调用序列。测试用 `sorted()` 比对绕过了此问题。

**状态**: 待修复

---

### BUG-C02: Value Alignment 拒绝消息不记入 Session [CRITICAL]

**严重性**: CRITICAL
**模块**: CognitiveOrchestrator
**位置**: `polaris/kernelone/cognitive/orchestrator.py:171-188,232`

当 Value Alignment 拒绝消息时，函数 early return（line 188），跳过了 `update_session()`（line 232）。被阻止的消息**永不记入 conversation_history**。

**影响**: 被阻止的消息对 session 连续性不可见，无法用于审计。

**状态**: 待修复

---

### BUG-C03: `_parse_action()` 返回无效工具名 [CRITICAL]

**严重性**: CRITICAL
**模块**: ActingHandler
**位置**: `polaris/kernelone/cognitive/execution/acting_handler.py:194,200`

`_parse_action()` 返回以下不存在于 `_TOOL_SPECS` 的工具名：
- `repo_read` — 不存在，正确应为 `read_file`
- `delete_file` — 不存在

**影响**: `executor.execute()` 返回 `{"ok": False, "error": "Unknown tool: repo_read"}`

**状态**: 待修复

---

## HIGH SEVERITY BUGS

### BUG-H01: `max_turns` 在内容响应路径被绕过

**严重性**: HIGH
**模块**: TurnEngine
**位置**: `polaris/cells/roles/kernel/internal/turn_engine/engine.py:1051`

当 LLM 返回纯内容（无工具调用）时，early return 在 `round_index` 递增之前触发。`max_turns` 配置的硬限制检查（line 1193）变成 dead check。

**状态**: 待修复

---

### BUG-H02: `BudgetPolicy.evaluate()` 未强制执行 `turn_count`

**严重性**: HIGH
**模块**: BudgetPolicy
**位置**: `polaris/cells/roles/kernel/internal/policy/layer/budget.py:207-333`

`evaluate()` 方法接收 `turn_count` 参数但**从未在方法体内使用**。所有预算强制只检查 `tool_call_count`、`wall_time_seconds`、`total_tokens` 等。

**状态**: 待修复

---

### BUG-H03: 非原子磁盘写入 + 新 Session 不持久化

**严重性**: HIGH
**模块**: CognitiveContext
**位置**: `polaris/kernelone/cognitive/context.py:137,138-140,189`

1. `path.write_text()` 非原子，进程崩溃产生损坏文件
2. 所有磁盘读写异常被静默吞噬（`except Exception: pass`）
3. 新 session 创建后从未立即持久化，进程崩溃则丢失

**状态**: 待修复

---

### BUG-H04: RollbackManager 无验证 + 部分失败报 SUCCESS

**严重性**: HIGH
**模块**: RollbackManager
**位置**: `polaris/kernelone/cognitive/execution/rollback_manager.py:136-156`

1. 回滚后无 ETag 再验证
2. `write_text` 失败时仍报告 `SUCCESS`
3. 部分失败（A 文件成功，B 文件失败）也报告 `SUCCESS`

**状态**: 待修复

---

### BUG-H05: `max_turns=0` 允许 1 次 LLM 调用

**严重性**: HIGH
**模块**: ConversationState
**位置**: `polaris/cells/roles/kernel/internal/conversation_state.py:405`

```python
if b.max_turns > 0 and b.turn_count >= b.max_turns:
```

当 `max_turns=0` 时，条件 `b.max_turns > 0` 为 False，`should_stop()` 返回 False，允许 1 次 LLM 调用。

**状态**: 待修复

---

## MEDIUM SEVERITY BUGS

### BUG-M01: Content-Hash 去重丢失 metadata

**严重性**: MEDIUM
**模块**: ContextGateway
**位置**: `polaris/cells/roles/kernel/internal/context_gateway.py:722,726`

去重使用 SHA256(`role:content`) 哈希，相同内容的不同事件后者替代前者，`transcript_log` 的 metadata（event_id, kind, route）丢失。

**状态**: 待修复

---

### BUG-M02: `_track_successful_calls` 存储重复 tuple

**严重性**: MEDIUM
**模块**: ToolLoopController
**位置**: `polaris/cells/roles/kernel/internal/tool_loop_controller.py:408-416`

```python
self._recent_successful_calls.append((tool_name, args_hash))  # 重复！
```

相同 `(tool_name, args_hash)` 被追加多次，而非维护计数器。trim 时也保留重复项。

**状态**: 待修复

---

### BUG-M03: `to_tuple()` 丢失所有 ContextEvent metadata

**严重性**: MEDIUM
**模块**: ToolLoopController
**位置**: `polaris/cells/roles/kernel/internal/tool_loop_controller.py:245`

`e.to_tuple()` 只返回 `(role, content)`，丢失 event_id, kind, route, dialog_act, source_turns, artifact_id, created_at。

**状态**: 待修复

---

### BUG-M04: `conversation_history` 无上限增长

**严重性**: MEDIUM
**模块**: CognitiveContext
**位置**: `polaris/kernelone/cognitive/context.py:36`

```python
conversation_history: tuple[ConversationTurn, ...]
```

无大小限制或 eviction 策略，长会话内存无限增长。

**状态**: 待修复

---

### BUG-M05: RollbackManager 静默跳过无法读取的文件

**严重性**: MEDIUM
**模块**: RollbackManager
**位置**: `polaris/kernelone/cognitive/execution/rollback_manager.py:76-91,117-124`

准备阶段无法读取的文件被静默排除，但快照从未保存。执行时也跳过，导致虚假 SUCCESS。

**状态**: 待修复

---

### BUG-M06: 解析失败报告 `status="success"`

**严重性**: MEDIUM
**模块**: ActingHandler
**位置**: `polaris/kernelone/cognitive/execution/acting_handler.py:154-161`

`_parse_action()` 返回 None 时，报告 `status="success"` 而非 `"failed"`。

**状态**: 待修复

---

### BUG-M07: 验证对解析失败也设 `verification_passed=True`

**严重性**: MEDIUM
**模块**: ActingHandler
**位置**: `polaris/kernelone/cognitive/execution/acting_handler.py:241,249`

因 BUG-M06，解析失败时 `result.status == "success"`，导致 `verification_passed=True`。

**状态**: 待修复

---

### BUG-M08: CJK 文本截断 token 估算不匹配

**严重性**: MEDIUM
**模块**: ContextOS
**位置**: `polaris/kernelone/context/context_os/runtime.py:1118-1131`

截断使用 `chars = tokens * 3`，但 `_estimate_tokens` 使用 `cjk_chars*1.5`。对于 CJK 文本，截断后 token 数仍超过预算。

**状态**: 待修复

---

## 低优先级 BUGS

| ID | 模块 | 问题 | 位置 |
|----|------|------|------|
| BUG-L01 | ToolLoopController | `_history` 从未被显式清理，无 commit 方法 | `tool_loop_controller.py` |
| BUG-L02 | ConversationState | `max_turns` 和 `max_tool_calls` 共用同一 env var | `conversation_state.py:168` |
| BUG-L03 | ToolLoopController | `context_os_snapshot=None` 被静默接受 | `tool_loop_controller.py:116-118` |
| BUG-L04 | ActingHandler | `_execute_direct` 声明 `async` 但无任何 `await` | `acting_handler.py:138` |

---

## 修复分配

| Agent | 负责 BUGs |
|-------|----------|
| Agent-1 | C01 (Stream/Non-Stream Transcript 顺序) |
| Agent-2 | C02 (Value Alignment session 数据丢失) |
| Agent-3 | C03 (无效工具名 repo_read/delete_file) |
| Agent-4 | H01, H02, H05 (TurnEngine max_turns 相关) |
| Agent-5 | H03, H04 (磁盘持久化 + RollbackManager) |
| Agent-6 | M01, M02, M03 (ContextGateway 去重 + ToolLoopController scratchpad) |
| Agent-7 | M04 (conversation_history 内存泄漏) |
| Agent-8 | M05, M06, M07 (ActingHandler 错误处理链) |
| Agent-9 | M08 (CJK token 截断不匹配) |
| Agent-10 | L01-L04 (低优先级清理) |

---

## 验证

修复后必须通过：
```bash
python -m pytest polaris/kernelone/cognitive/ -q
python -m pytest polaris/kernelone/context/ -q
python -m pytest polaris/cells/roles/kernel/tests/ -q
python -m ruff check polaris/ --fix
```
