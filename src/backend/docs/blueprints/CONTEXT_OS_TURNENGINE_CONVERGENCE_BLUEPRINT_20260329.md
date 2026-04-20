# Context OS 与 TurnEngine 架构收敛蓝图

**日期**: 2026-03-29
**状态**: Draft
**执行团队**: Python 架构与代码治理实验室
**目标**: 统一 Context 系统，消除 Stream/Non-Stream parity bug，建立清晰的上下文架构

---

## 1. 问题陈述

### 1.1 当前症状

| 问题 | 影响 | 根因 |
|------|------|------|
| Stream/Non-Stream parity failure | 8/14 benchmark 失败 | `_persist_session_turn_state` 缺失 `turn_history` 参数 |
| 两套 Context 系统并行 | 维护成本高 | `RoleContextGateway` 与 `Context OS` 职责重叠 |
| `turn_history=None` 回退逻辑 | 状态丢失风险 | Legacy 代码路径未清除 |
| `command.history` vs `_controller._history` | 混淆风险 | 两个历史来数据源不统一 |
| Context OS 投影间接影响 LLM | 不可靠 | `strategy_receipt` 注入路径非主流 |

### 1.2 Benchmark 失败根因追溯

```
L1-L5 失败 (11/14):
├── 8/14: Stream/Non-stream parity → turn_history 不传递
├── 6/14: 工具选择策略 → LLM 偏好通用工具
├── 4/14: 工具调用数量 → 不确定性导致过度操作
└── 2/14: 无害查询不下发 → 模型判断能力不足
```

---

## 2. 目标架构

### 2.1 核心原则

1. **单一数据来源**：TurnEngine 的 `_controller._history` 是 LLM 上下文的唯一历史来源
2. **Context OS 作为主存储**：所有会话状态通过 Context OS 管理，消除独立 session 存储
3. **消除 Legacy 路径**：删除 `turn_history=None` 的回退分支
4. **统一 Context 接口**：一个 `ContextRequest`，一个 `ContextResult`

### 2.2 目标数据流

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Context OS (Single Source of Truth)             │
│                                                                          │
│  ┌─────────────┐     ┌──────────────────┐     ┌─────────────────────┐  │
│  │ Transcript  │ ──▶ │ ContextOSProjection│ ──▶ │ session.context_config│  │
│  │    Log      │     │   (transcript_log)│     │   [state_first_ctx] │  │
│  └─────────────┘     └──────────────────┘     └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         TurnEngine Layer                                │
│                                                                          │
│  TurnEngine.run()                                                        │
│    │                                                                     │
│    ├─▶ build_context_request() ──▶ _controller._history ──────────────┐ │
│    │                                      │                           │ │
│    │                               ┌──────▼──────┐                     │ │
│    │                               │   Context   │                     │ │
│    │                               │   Request   │                     │ │
│    │                               └──────┬──────┘                     │ │
│    │                                      │                           │ │
│    │                               ┌──────▼──────┐                     │ │
│    │                               │    LLM      │                     │ │
│    │                               │   Caller    │                     │ │
│    │                               └─────────────┘                     │ │
│    │                                                                     │
│    └─▶ execute_tools() ──▶ append_tool_result() ──▶ _controller._history│
│                                                                          │
│  TurnEngine.run_stream()                                                 │
│    │                                                                     │
│    └─▶ (同 run() 但流式增量)                                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         RoleContextGateway (Simplified)                  │
│                                                                          │
│  build_context(request: ContextRequest) → ContextResult                  │
│    │                                                                     │
│    ├─▶ _process_history(request.history)  ← 唯一历史来源               │
│    │                                                                     │
│    └─▶ messages = [{"role": "system", ...},                             │
│                     {"role": "user", ...},                               │
│                     {"role": "assistant", ...},                          │
│                     {"role": "tool", ...}]                               │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.3 关键接口统一

#### 统一 ContextRequest (在 `kernelone/context/` 下定义)

```python
# polaris/kernelone/context/contracts.py 新增

@dataclass(frozen=True)
class UnifiedContextRequest:
    """统一的上下文请求接口 - TurnEngine 和 Context OS 共用"""

    message: str                           # 当前用户消息
    history: tuple[tuple[str, str], ...]  # turn_history 元组 (role, content)
    task_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    workspace: str = "."
    domain: str = "code"
    # Context OS 投影（可选，用于跨会话连续性）
    context_os_snapshot: ContextOSSnapshot | None = None
    # 压缩相关
    compression_policy: CompressionPolicy | None = None
```

#### 统一 ContextResult

```python
@dataclass(frozen=True)
class UnifiedContextResult:
    """统一的上下文构建结果"""

    messages: tuple[dict[str, str], ...]   # LLM 消息列表
    token_estimate: int = 0
    sources: tuple[str, ...] = ()
    context_os_projection: ContextOSProjection | None = None
    compression_applied: bool = False
```

---

## 3. 实施计划

### Phase 1: Stream/Non-Stream Parity 修复（P0）

**目标**: 消除 turn_history 丢失问题

**任务**:
- [x] P0-1: 修复 `stream_chat_turn` 中的 `_persist_session_turn_state` 调用（已修复）
- [x] P1-2: 删除 `_persist_session_turn_state` 中的 `turn_history=None` 回退分支（已实施）
- [x] P1-3: 确保 `run_stream` 和 `run` 使用相同的 persistence 路径（已统一，所有调用点传 `turn_history=[]` 或真实历史）
- [x] P1-4: 添加 parity integration test（`test_turn_history_persist_parity.py` 17 tests）

**验证**:
```bash
pytest polaris/cells/roles/kernel/tests/test_turn_history_persist_parity.py -v
# 17 passed
```

**验证**:
```bash
pytest tests/benchmark/parity/
```

### Phase 2: 统一 ContextRequest 定义（P1）

**目标**: 消除两套 ContextRequest 定义

**任务**:
- [x] P2-1: 在 `polaris/kernelone/context/contracts.py` 添加 `TurnEngineContextRequest` 和 `TurnEngineContextResult`（统一类型）
- [x] P2-2: `RoleContextGateway` 导入并使用新的统一接口（通过别名 `ContextRequest`/`ContextResult`）
- [x] P2-3: `kernel._build_context()` 返回 `TurnEngineContextRequest`（`history` 转换为 tuple）
- [x] P2-4: 删除 `context_gateway.py` 中本地的 `ContextRequest` 和 `ContextResult` 类定义

**文件变更**:
```
polaris/kernelone/context/contracts.py     # 新增 UnifiedContextRequest
polaris/cells/roles/kernel/internal/context_gateway.py  # 删除本地 ContextRequest
polaris/cells/roles/kernel/internal/tool_loop_controller.py  # 更新 build_context_request
```

### Phase 3: Context OS 直接集成（P1）

**目标**: Context OS 投影直接作为 LLM 上下文来源

**任务**:
- [ ] P3-1: 在 `RoleContextGateway` 中添加 `context_os_snapshot` 字段处理
- [ ] P3-2: 将 `SessionContinuityEngine.project()` 结果直接传给 `RoleContextGateway`
- [ ] P3-3: 修改 `_persist_session_turn_state` 直接构建 `ContextOSProjection` 而非间接通过 session
- [ ] P3-4: 消除 `strategy_receipt` 的间接注入路径

**新数据流**:
```
_persist_session_turn_state
    ↓
ContextOSProjection.from_turn_history(turn_history)
    ↓
session.context_config["state_first_context_os"] = projection
    ↓
下一轮 TurnEngine:
RoleTurnRequest.context_override["state_first_context_os"] = session.context_config[...]
    ↓
RoleContextGateway.build_context()
    ↓
直接使用 ContextOSProjection 而非重新从 history 构建
```

### Phase 4: 消除 Legacy 回退逻辑（P2）

**目标**: 删除 `turn_history=None` 分支，强制所有路径传递完整历史

**任务**:
- [x] P4-1: 删除 `_persist_session_turn_state` 中的 else 分支（已随 P1-2 完成）
- [ ] P4-2: 删除 `_build_post_turn_history`（被 `turn_history` 参数替代，但 ContextOS 投影构建仍依赖）
- [ ] P4-3: 清理 `RoleRuntimeService` 中不再使用的参数（`assistant_text`, `thinking`, `tool_calls`, `usage`）
- [x] P4-4: 更新所有调用点传递 `turn_history`（已随 P1-2 完成）

**删除的代码**:
```python
# _persist_session_turn_state 中的这段回退逻辑将被删除
else:
    # 回退到旧的逻辑（仅当没有完整历史时）
    if user_text:
        svc.add_message(...)
    if assistant_text:
        svc.add_message(...)
```

### Phase 5: 统一历史来源 — 方案 C（纯粹职责分离）

**目标**: Context OS 是 Single Source of Truth，`_controller._history` 是当前轮草稿本

**核心原则**:
- 新轮对话启动时，`_build_session_request` **不再依赖** `command.history` 数组
- 直接从 Context OS 获取 `ContextOSProjection`（过去完整状态上下文）
- `_controller._history` 严格限制为**当前轮（Current Turn）草稿本**：
  - 初始为空（只包含当前用户新 Query）
  - TurnEngine 执行期间累加工具调用记录
  - 对话结束（`_persist_session_turn_state）时，Commit 给 Context OS，随后**清空**

**新数据流**:
```
第一轮对话：
  TurnEngine.start_turn()
    → _controller._history.clear()  ← 清空草稿本
    → _controller._history.append(("user", query))  ← 写入用户消息

  TurnEngine.run() 中每次工具执行：
    → _controller.append_tool_result(result)  ← 草稿本追加

  _persist_session_turn_state()
    → ContextOS.commit(_controller._history)  ← 草稿本提交给 Context OS
    → _controller._history.clear()  ← 提交后清空

第二轮对话：
  _build_session_request()
    → ContextOSProjection = ContextOS.get_projection(session_id)  ← 从 Context OS 获取
    → request.context_override["context_os_snapshot"] = projection  ← 注入请求
    → TurnEngine.start_turn()  ← 重复上述流程
```

**任务**:
- [ ] P5-1: `RoleRuntimeService._build_session_request` 从 Context OS 获取 `ContextOSProjection`，不再传递 `command.history`
- [ ] P5-2: `_controller._history` 初始化时预载 Context OS 投影（作为只读种子），后续只追加
- [ ] P5-3: `_persist_session_turn_state` 执行 `ContextOS.commit(_controller._history)` 后清空草稿本
- [ ] P5-4: 更新 `RoleTurnRequest` 移除 `history` 字段（或标记为 deprecated）

**Phase 5 后 `_controller._history` 状态**:
```
Turn 开始: [_controller._history = projection.transcript_log[-N:]]  ← 从 Context OS 恢复
Turn 中:  [_controller._history += [(role, content), ...]]  ← 增量追加
Turn 结束: [ContextOS.commit(_controller._history)] → [_controller._history.clear()]
```

### Phase 6: Context OS 压缩协同 + Event Sourcing Safeguard

**目标**: 压缩策略基于 Context OS，保留 Event Sourcing 不可变性

**核心原则（Event Sourcing Safeguard）**:
- `transcript_log`（原始事件流）是**不可变的**，永远不被压缩
- 压缩只发生在**视图层**（给 LLM 看的 messages）或 `working_state`
- 系统可随时从 `transcript_log` 重放（Replay）整个状态

**视图层压缩**:
```
ContextOSProjection
    ├── transcript_log: (Immutable) 原始事件流 ← 只追加，不压缩
    ├── working_state:   (压缩目标) 长尾对话摘要
    └── messages_view:   (压缩结果) 给 LLM 的 prompt

compress() 操作:
    messages_view = Compressor.compress(transcript_log, budget)
    working_state = Summarizer.summarize_older(transcript_log, threshold)
```

**Phase 6 前置条件**: Phase 5 完成（Context OS 是唯一真相来源）

**任务**:
- [ ] P6-1: 实现 `ContextOSProjection.compress()` 方法，**视图层压缩**
- [ ] P6-2: `working_state` 存储长尾对话摘要，不压缩 `transcript_log`
- [ ] P6-3: 迁移 `RoleContextGateway` 中的压缩逻辑到 `ContextOSProjection.compress()`
- [ ] P6-4: 消除 `compression_strategy == "summarize"` legacy 分支
- [ ] P6-5: 添加 `test_transcript_log_immutability` 测试，确保重放一致性

---

## 4. 验收标准

| Phase | 验收条件 | 测试 |
|--------|----------|------|
| Phase 1 | Stream/Non-stream parity 通过 | `test_parity_stream_vs_nonstream` |
| Phase 2 | 只有一套 ContextRequest | 代码审查 + `grep -r "class ContextRequest"` |
| Phase 3 | Context OS 直接影响 LLM 上下文 | integration test |
| Phase 4 | 无 `turn_history=None` 回退代码 | 代码审查 |
| Phase 5 | 单一历史来源 | `grep "command.history"` 确认语义一致 |
| Phase 6 | 压缩基于 Context OS | `test_context_os_compression` |

---

## 5. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Phase 1-3 改动影响 benchmark 稳定性 | 高 | 逐阶段验证，每阶段 benchmark 运行 |
| Context OS 性能下降 | 中 | 增量投影，而非全量重建 |
| 团队对目标架构理解不一致 | 中 | 每周 architecture review meeting |
| 回退逻辑删除后出现漏报 | 高 | 完整测试覆盖 + canary 部署 |

---

## 6. 里程碑

| 里程碑 | 目标日期 | 交付物 |
|--------|----------|--------|
| M1: Phase 1-2 完成 | 2026-04-05 | Parity bug 修复 + 统一 ContextRequest |
| M2: Phase 3-4 完成 | 2026-04-12 | Context OS 直接集成 + 无 Legacy 回退 |
| M3: Phase 5-6 完成 | 2026-04-19 | 统一历史来源 + Context OS 压缩 |
| M4: 全量 benchmark 通过 | 2026-04-26 | 14/14 PASS |

---

## 7. 相关文档

- `docs/audit/llm_tool_calling/TOOL_CALLING_MATRIX_BENCHMARK_REPORT_20260329.md`
- `docs/blueprints/STREAM_NONSTREAM_PARITY_FIX_20260329.md`
- `polaris/kernelone/context/context_os/models.py` - Context OS 模型定义
- `polaris/kernelone/context/session_continuity.py` - SessionContinuityEngine
- `polaris/cells/roles/kernel/internal/turn_engine.py` - TurnEngine
- `polaris/cells/roles/kernel/internal/tool_loop_controller.py` - ToolLoopController
