# Post-Mortem: ContextOS `transcript_events: (empty)` 根本原因分析

**日期**: 2026-03-31
**严重程度**: P0 — 功能完全失效
**影响范围**: 所有通过 streaming path 执行的 benchmark session
**状态**: 已修复

---

## 1. 问题现象

benchmark 日志中，所有 iteration 都显示：

```
【Context OS State】 (Phase 5 direct path)
transcript_events: (empty)
```

即使在同一个 `run_id` 下连续多个 iteration，ContextOS 的 `transcript_log` 始终为空。

---

## 2. 根因分析（5层递进）

### 第一层：Streaming Path 完全没有创建 Session

**发现时间**: 本次调试

**现象**:
- `stream_chat_turn` 调用 `_persist_session_turn_state`
- `_persist_session_turn_state` 调用 `svc.get_session(session_id)` → 返回 `None`
- 直接 `return`，**所有事件未持久化**

**根因**: Benchmark adapter 每次用 fresh UUID 作为 `session_id`（如 `agentic-bench-{case_id}-{uuid}`），但从未调用 `svc.create_session()`。Streaming path 假设 session 已存在，但实际上 DB 中没有这条记录。

```python
# Benchmark adapter — 每次新 session_id
session_id = f"agentic-bench-{case.case_id}-{uuid.uuid4().hex[:8]}"

# Streaming path — 假设 session 已存在
session = svc.get_session(session_id)
if session is None:
    return  # ← 什么都没存！
```

---

### 第二层：`turn_events_metadata` 从未从 Stream Path 传递

**发现时间**: 本次调试

**现象**:
- 非流式 `run()` 在结束时会构建 `turn_events_metadata`（line 857-866）
- 流式 `run_stream()` 的 `_build_stream_complete_result()` **从未接收** `turn_events_metadata` 参数

**根因**: `_build_stream_complete_result()` 的函数签名中本来就没有 `turn_events_metadata` 参数。所有 3 个 stream completion 分支（early return / tool blocked / approval required）都调用这个函数，但都传不了 `turn_events_metadata`。

```python
# 非流式 run() — 正确构建
turn_events_metadata = [{...} for e in _controller._history]  # ✓

# 流式 _build_stream_complete_result — 缺失参数
def _build_stream_complete_result(..., turn_events_metadata=None):
    return RoleTurnResult(
        ...
        turn_events_metadata=list(turn_events_metadata) if turn_events_metadata else [],  # 永远空
    )
```

---

### 第三层：`RoleTurnRequest` 的 Bootstrap 设计导致测试无法发现 Session 创建缺失

**发现时间**: 本次调试

**现象**:
- `RoleTurnRequest.__init__` 的 `_post_init()` 会自动注入空的 `context_os_snapshot`
- 所以 `_build_session_request` 即使在 session 不存在时也能返回一个"看起来正常"的 `RoleTurnRequest`
- 早期测试 `test_requires_context_os_snapshot` 验证 `ValueError` 应该被抛出，但**永远不会触发**，因为 `RoleTurnRequest._post_init()` 已经保证了 snapshot 存在

**根因**: 设计决策 —— "所有 RoleTurnRequest 都有 context_os_snapshot"。这本身是好的设计（SSOT bootstrap），但它掩盖了一个问题：**有了 snapshot 不等于 session 真正存在**。

---

### 第四层：两层"假性正常"导致问题被深埋

| 层次 | 现象 | 为什么"看起来正常" |
|------|------|---------------------|
| `_build_session_request` | 返回有效 `RoleTurnRequest`，带 bootstrap snapshot | `RoleTurnRequest._post_init()` 自动注入 |
| `_persist_session_turn_state` | 无异常抛出，silent return | `except: pass` + `if session is None: return` |

**根因**: 两处设计都"fail silently" —— 都没有报错，但都没有真正工作。

---

### 第五层：系统性问题 — 缺少跨模块集成测试

**现象**:
- `turn_engine` 测试、`context_gateway` 测试、`session_continuity` 测试都**单独通过**
- 但 **streaming path 全链路测试** 不存在
- `RoleSessionService` 的 `get/create_session` 契约与 streaming path 的使用方式**从未一起验证**

**根因**: 测试覆盖存在结构性盲区 —— 单元测试通过不等于集成正确。

---

## 3. 为什么之前没有发现？

### 3.1 代码审查盲区

在之前的代码审查中，审查者可能关注了：
- `turn_events_metadata` 是否正确保留（局部正确）
- `ContextOS` 投影逻辑是否正确（局部正确）
- `transcript_log` 是否在持久化 payload 中（正确）

但**没有人追踪 streaming path 的 `_persist_session_turn_state` 是否真正被调用、调用时 session 是否存在**。

### 3.2 测试设计盲区

- 现有测试用 mock 绕过真实 session 服务
- `RoleSessionService` 的测试不涉及 streaming path 的 `_persist_session_turn_state`
- Benchmark 测试存在，但 `transcript_events` 的验证从未作为 benchmark 的检查项

### 3.3 Silent Failure 掩盖问题

```python
# 位置 1: service.py _persist_session_turn_state
if session is None:
    return  # 无日志，无报警

# 位置 2: service.py _build_session_request
except Exception:
    pass  # 静默吞掉所有异常
```

两处 silent failure 导致问题完全不产生可见错误，只有日志中 `transcript_events: (empty)` 才能发现。

---

## 4. 研讨会讨论问题

### 问题 1：Fail-Silent 原则是否应该被重新审视？

**背景**: 代码中有大量 `except: pass` 和 `if x is None: return`

**讨论点**:
- 在哪些层次上 silent failure 是合理的？
- 在 SSOT 核心路径（ContextOS persistence）上，silent failure 是否应该被上报？
- 如何区分"优雅降级"和"静默丢失数据"？

### 问题 2：Bootstrap 设计如何避免"保证存在但实际无效"？

**背景**: `RoleTurnRequest._post_init()` 保证 `context_os_snapshot` 存在，但 session 可能不存在

**讨论点**:
- 应该在哪个层次验证 session 存在性？
- 应该在 `execute_role_session` 入口统一创建/获取 session？
- Session 的"创建"和"第一个 turn 的执行"能否原子化？

### 问题 3：如何建立跨模块集成测试？

**背景**: 单独测试都通过，但全链路失败

**讨论点**:
- Streaming path 全链路测试应该包含哪些组件？
- 如何在不依赖真实 LLM 的情况下测试 persistence 层？
- Benchmark 是否应该包含 ContextOS 状态的验证？

### 问题 4：代码审查如何发现这类问题？

**讨论点**:
- `_persist_session_turn_state` 的 early return 是否需要 code review checklist？
- 审查者看到 `if session is None: return` 时，应该追问什么？
- "修复了测试"和"修复了 bug"的区别是什么？

---

## 5. 修复清单

| # | 修复 | 文件 | 优先级 | 状态 |
|---|------|------|--------|------|
| 1 | `_persist_session_turn_state` 中 session 不存在时创建 session | `service.py` | P0 | 已修复 |
| 2 | `_build_stream_complete_result` 增加 `turn_events_metadata` 参数 | `turn_engine.py` | P0 | 已修复 |
| 3 | 3 个 stream completion 分支都传入序列化后的 `_controller._history` | `turn_engine.py` | P0 | 已修复 |
| 4 | 更新 `test_requires_context_os_snapshot` 以反映实际设计 | `test_context_os_ssot_constraint.py` | P1 | 已修复 |
| 5 | `stream_chat_turn` 累积 `turn_events_metadata` 并在 final_result 为空时使用 | `service.py` | P0 | 已修复 |

---

## 6. 行动项

| 行动 | 负责人 | 截止日期 |
|------|--------|----------|
| A. 建立 streaming path 全链路集成测试（mock LLM + 真实 DB） | 待定 | 2026-04-07 |
| B. 审查所有 `except: pass` 和 silent return，标记 SSOT 路径 | 待定 | 2026-04-07 |
| C. `execute_role_session` 入口统一 session 创建逻辑 | 待定 | 2026-04-14 |
| D. Benchmark 增加 `transcript_events` 非空验证 | 待定 | 2026-04-14 |

---

## 7. 关键教训

1. **SSOT 路径上的 Silent Failure 是 P0 级风险** — `transcript_events: (empty)` 不是警告，是数据丢失
2. **Bootstrap 设计必须验证"保证存在"的语义真正成立** — `context_os_snapshot` 存在 ≠ session 存在
3. **局部测试通过 ≠ 集成正确** — 必须有跨模块的全链路测试
4. **流式路径和非流式路径必须用同一套 persistence 契约测试** — 否则会像这次一样，流式路径 bug 隐藏在非流式测试的阴影里
5. **修复测试 ≠ 修复 Bug** — `test_requires_context_os_snapshot` 失败是设计冲突，不是 bug

---

*Prepared for team review — 2026-03-31*
