# Polaris CLI 架构修复蓝图：Session State 硬化与上下文隔离

**版本**: 1.0  
**日期**: 2026-04-21  
**状态**: 设计完成，待实施  
**分类**: P0 架构修复  

---

## 1. 执行摘要

本蓝图针对 Polaris CLI 在 MATERIALIZE_CHANGES 模式下暴露的 5 个严重架构缺陷，提出系统性修复方案。核心策略：**硬化 Session State 边界、沙箱化 Turn State、强化工具层容错**。

---

## 2. 问题根因矩阵

| 问题 | 症状 | 根因 | 修复策略 |
|------|------|------|----------|
| P0-1 交付模式丢失 | Turn 1 从 MATERIALIZE_CHANGES 降级为 ANALYZE_ONLY | `stream_orchestrator.py` 的 continuation prompt 解析逻辑强制非 implementing 降级 | Session State 硬化：delivery_mode 一旦设定不可变更 |
| P0-2 目标污染 | 模型输出覆盖 original_goal | 模型文本输出被回灌为 User Message | Turn State 沙箱化：模型输出放入 Agent Scratchpad 角色 |
| P1-1 路径死循环 | 连续 8 次 read_file 失败 | 工具层无路径容错，模型使用短路径 | Tool Execution 层增加模糊匹配和动态错误反馈 |
| P1-2 合成消息污染 | 合成提示包含矛盾指令 | 系统构造的合成 User Message 覆盖原始目标 | 禁止合成消息覆盖核心任务块 |
| P2 阶段卡死 | 10 个 Turn 卡在 EXPLORING | PhaseManager 不跨 Turn 持久化，无超时熔断 | PhaseManager 提升到 Session 级别，增加超时熔断 |

---

## 3. 架构设计

### 3.1 系统架构图（文本描述）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SESSION LEVEL (Immutable)                          │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │   original_goal     │  │   delivery_mode     │  │  phase_manager      │  │
│  │   (str, frozen)     │  │   (enum, frozen)    │  │  (persisted)        │  │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘  │
│  ┌─────────────────────┐  ┌─────────────────────┐                           │
│  │   read_files        │  │   session_invariants│                           │
│  │   (list[str])       │  │   (validator)       │                           │
│  └─────────────────────┘  └─────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            TURN LEVEL (Mutable)                              │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │   TurnLedger        │  │   Agent Scratchpad  │  │   Tool Results      │  │
│  │   (per-turn state)  │  │   (model output)    │  │   (batch receipt)   │  │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘  │
│  ┌─────────────────────┐                                                    │
│  │   Delivery Contract │  ← 只读引用 Session delivery_mode                  │
│  │   (read-only view)  │                                                    │
│  └─────────────────────┘                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TOOL EXECUTION LAYER                                 │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │   Fuzzy Resolver    │  │   Failure Budget    │  │   Dynamic Feedback  │  │
│  │   (path resolution) │  │   (with context)    │  │   (escalating)      │  │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 核心数据流

```
User Input → [Session Invariants Guard] → Intent Detection (SLM)
                                                │
                                                ▼
                              ┌─────────────────────────────┐
                              │   Delivery Mode Resolution  │
                              │   (Turn 0 only, immutable)  │
                              └─────────────────────────────┘
                                                │
                              ┌─────────────────────────────┐
                              │   PhaseManager (Session)    │
                              │   - EXPLORING → CONTENT_    │
                              │     GATHERED → IMPLEMENTING │
                              │   - persisted across turns  │
                              └─────────────────────────────┘
                                                │
                              ┌─────────────────────────────┐
                              │   Turn Execution            │
                              │   - Agent Scratchpad        │
                              │   - Tool Batch Execution    │
                              │   - Phase Validation        │
                              └─────────────────────────────┘
                                                │
                              ┌─────────────────────────────┐
                              │   Checkpoint                │
                              │   (Session State persisted) │
                              └─────────────────────────────┘
```

---

## 4. 模块职责划分

### 4.1 `continuation_policy.py` — 状态不变式守卫

**新增**：`SessionInvariants` 类

- **职责**: 在每次 Turn 开始前验证 Session State 的不可变性
- **验证项**:
  - `delivery_mode` 未被修改
  - `original_goal` 未被修改
  - `phase` 未发生非法回退
- **违规处理**: 触发 `InvariantViolation` → `panic + handoff_workflow`

### 4.2 `session_orchestrator.py` — Session State 硬化

**修改**:

1. **`OrchestratorSessionState` 命名空间隔离**:
   - `session_invariants`: `delivery_mode`, `original_goal`, `phase`
   - `turn_mutable`: `goal`, `task_progress`, `artifacts`

2. **Checkpoint 完整化**:
   - 保存/恢复 `original_goal`, `read_files`, `phase_manager`

3. **_build_continuation_prompt 修正**:
   - 模型输出放入 `<agent_scratchpad>` 角色块
   - 禁止模型输出进入 `user` 角色

### 4.3 `stream_orchestrator.py` — 交付模式持久化

**修改**:

1. **`resolve_delivery_mode` 一次性解析**:
   - Turn 0 通过 SLM 解析 delivery_mode
   - 后续 Turn 从 Session State 读取，禁止重新解析

2. **`_build_continue_visible_content` 强化**:
   - `MATERIALIZE_CHANGES + EXPLORING + 无 tool_calls` → 强制 continue_multi_turn
   - 注入高权重错误指令

3. **Continuation Prompt 阶段解析修正**:
   - `verifying` 阶段不降级为 `ANALYZE_ONLY`
   - 保持原始 delivery_mode

### 4.4 `tool_batch_executor.py` — Phase 验证强化

**修改**:

1. **Phase 检测去字符串化**:
   - 从 Session State 读取当前 phase，而非字符串匹配

2. **文本输出拦截**:
   - `MATERIALIZE_CHANGES + EXPLORING + 无 tool_calls` → 拦截并返回错误

3. **Delivery Contract 只读**:
   - 禁止修改 delivery_contract，使用 Session 级别的只读引用

### 4.5 `phase_manager.py` — 跨 Turn 持久化

**修改**:

1. **状态可序列化**:
   - `PhaseManager` 支持 `to_dict()` / `from_dict()`

2. **提升到 Session 级别**:
   - 绑定到 `OrchestratorSessionState` 而非 `TurnLedger`

3. **超时熔断**:
   - 同一阶段停留超过 3 个 Turn → 强制注入破局提示

### 4.6 `tool_gateway.py` / `read_file` — 路径容错

**修改**:

1. **模糊匹配**:
   - 短路径 → 在最近 glob 结果中搜索
   - 唯一匹配 → 静默展开

2. **动态错误反馈**:
   - 第 1 次: "文件未找到"
   - 第 2 次: "请使用完整路径"
   - 第 3 次: "调用 glob 查找路径"

---

## 5. 技术选型理由

| 决策 | 选型 | 理由 |
|------|------|------|
| Session State 存储 | `OrchestratorSessionState` dataclass | 类型安全、可序列化、与现有架构兼容 |
| 不变式验证 | `SessionInvariants` 显式守卫 | 防御性编程，避免隐式状态污染 |
| Phase 持久化 | JSON 序列化到 checkpoint | 简单可靠，与现有 checkpoint 机制一致 |
| 路径模糊匹配 | 工具层而非 LLM 层 | LLM 不可靠，工具层更确定 |
| Agent Scratchpad | 独立角色块 | 语义隔离，防止上下文污染 |

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Session State 膨胀 | checkpoint 过大 | 只持久化必要字段，压缩历史 |
| PhaseManager 回退 | 阶段回退导致循环 | 增加阶段历史验证，禁止回退 |
| 模糊匹配歧义 | 错误展开路径 | 多匹配时返回歧义错误 |
| 向后兼容 | 修改影响已有测试 | 全量回归测试 L3-L9 |

---

## 7. 验收标准

1. **L3 测试矩阵**: 9/9 通过，score >= 90
2. **模式持久化**: MATERIALIZE_CHANGES 跨 10 个 Turn 不丢失
3. **目标保护**: original_goal 在 10 个 Turn 后仍然正确
4. **路径容错**: 短路径 read_file 自动展开，连续失败 3 次后动态提示
5. **阶段推进**: EXPLORING → CONTENT_GATHERED → IMPLEMENTING 正常流转

---

## 8. 实施顺序

### Phase 1: Session State 硬化（地基）
1. 修改 `continuation_policy.py` — 添加 `SessionInvariants`
2. 修改 `session_orchestrator.py` — 命名空间隔离、checkpoint 完整化

### Phase 2: Turn State 沙箱化（防污染）
3. 修改 `stream_orchestrator.py` — 交付模式持久化、Agent Scratchpad
4. 修改 `tool_batch_executor.py` — Phase 验证强化、文本拦截

### Phase 3: Tool Execution 容错（打破死循环）
5. 修改 `phase_manager.py` — 跨 Turn 持久化、超时熔断
6. 修改 `tool_gateway.py` / read_file — 模糊匹配、动态反馈

### Phase 4: 验证
7. 运行 L3-L5 测试矩阵
8. 手动验证 CLI 多轮对话

---

*本蓝图遵循 AGENTS.md §10.1 两阶段执行模型和 §10.2 工程标准。*
