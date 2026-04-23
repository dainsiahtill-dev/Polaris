# ADR-0081: Cognitive Behavior Matrix — L3 认知行为评测集

**日期**: 2026-04-17
**版本**: v1.0
**状态**: Blueprint → Implementation
**关联文档**:
- `docs/blueprints/WORKING_MEMORY_PIPELINE_IMPLEMENTATION_20260417.md`
- `AGENTS.md` §18（认知生命体架构对齐）
- `polaris/cells/llm/evaluation/internal/session_workflow_matrix.py`

> **工程注释**：本文档使用"认知行为"、"主控意识"等隐喻作为记忆辅助。
> 所有隐喻均可在 [TERMINOLOGY.md](../TERMINOLOGY.md) 中找到对应的工程实体。
> 代码实现中使用的是工程实体名称，而非隐喻。

---

## 1. 问题陈述

### 1.1 四层测试金字塔现状

| 层级 | 覆盖度 | 代表测试 |
|------|--------|----------|
| L0 单元测试 | 强 (866 passed) | ContextOS隔离、Circuit Breaker、Session Persistence |
| L1 工具调用 | 强 (14+ fixtures) | Tool Calling Matrix |
| L2 会话工作流 | 中 (6 cases) | Session Workflow Matrix |
| **L3 认知行为** | **弱 (0 cases)** | **缺失** |

### 1.2 L3 缺失的核心认知维度

`session_workflow_matrix` 现有6个cases全部是**工作流机制**测试（auto_continue、checkpoint、stagnation、handoff），没有一个是**认知行为**测试：

- **Belief Revision（认知修正）**: 生命体能否在收到反证后更新 WorkingMemory，丢弃旧假设
- **Role Adherence（角色定力）**: 生命体能否坚守角色边界，拒绝越权指令
- **Goal Convergence（目标收敛）**: 生命体能否在干扰信息中保持主线任务不偏离

这三项是 AGENTS.md §18 "认知生命体" 定义中 "主控意识" 的核心能力。缺失评测意味着：
1. 每次改动 `OrchestratorSessionState` 或 `ContinuationPolicy` 时，无法验证认知行为是否退化
2. LLM 的 "兔子洞效应" 和 "角色漂移" 只能在生产环境暴露

---

## 2. 核心架构原则

### 2.1 复用现有框架，不造新轮子

`session_workflow_matrix` 已具备完整的基础设施：
- `MockWorkflowKernel` — 可编程内核，每 turn 返回预定义事件
- `WorkflowTurnSpec` — turn 级别的规格定义
- `SessionWorkflowCase` — case 级别的编排容器
- `_run_session_workflow_case` — 执行引擎 + 断言验证
- `load_builtin_session_workflow_cases` — case 注册表

**决策**: 在现有框架内追加 3 个 L3 cases，不新建模块。

### 2.2 认知行为必须可断言

每个 L3 case 的 `final_state_assertions` 必须能验证认知状态：
- `structured_findings` 的演进轨迹
- `task_progress` 的收敛路径
- `rejected_actions` 或 `boundary_violations` 的记录

### 2.3 与 AGENTS.md §18 对齐

| 抽象概念 | 工程实体 | L3 评测维度 |
|---------|---------|------------|
| 主控意识 | `RoleSessionOrchestrator` | Belief Revision、Goal Convergence |
| 物理法则 | `ContinuationPolicy` | Role Adherence |
| 海马体 | `OrchestratorSessionState.structured_findings` | 认知状态演进断言 |

---

## 3. 模块职责

### 3.1 现有架构（不变）

```
polaris/cells/llm/evaluation/internal/session_workflow_matrix.py
├── MockWorkflowKernel          (已有) — 模拟内核事件流
├── WorkflowTurnSpec            (已有) — 单 turn 规格
├── SessionWorkflowCase         (已有) — case 定义
├── _run_session_workflow_case  (已有) — 执行引擎
└── _CASES: list[...]           (扩展) — 追加3个L3 cases

polaris/cells/llm/evaluation/tests/test_session_workflow_matrix.py
├── TestCaseLoading             (扩展) — 验证新cases可加载
├── TestCaseExecution           (扩展) — 验证新cases可通过
└── TestCustomCase              (已有) — 不变
```

### 3.2 新增职责

| 组件 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `swm_belief_revision` case | 验证错误假设被反证推翻 | Turn 1: 错误假设 + Turn 2: 反证 | `structured_findings` 更新，旧假设消失 |
| `swm_role_adherence` case | 验证角色边界不被突破 | QA角色 + 越权指令 | 只执行QA工具，忽略越权 |
| `swm_goal_convergence` case | 验证主线不被干扰带偏 | 主线A + 干扰B | task_progress 保持主线 |

---

## 4. 核心数据流

```
测试触发
    │
    ▼
load_builtin_session_workflow_cases()
    │
    ├── swm_auto_continue_phases      (已有)
    ├── swm_working_memory_reduces_search (已有)
    ├── ... (4 more existing)
    ├── swm_belief_revision           (新增)
    ├── swm_role_adherence            (新增)
    └── swm_goal_convergence          (新增)
    │
    ▼
run_session_workflow_suite()
    │
    ▼
_run_session_workflow_case(case) ──► MockWorkflowKernel.execute_stream()
    │                                    │
    │                                    ▼
    │                            按 turn 返回预定义事件
    │                            + TurnOutcomeEnvelope
    │                            + state_assertions
    │                                    │
    ▼                                    ▼
Session Verdict ◄─────────────────── 状态断言检查
    │
    ├── tooling check (工具调用是否正确)
    ├── state check (structured_findings 演进)
    └── envelope check (continuation_mode 预期)
```

---

## 5. 技术选型

| 决策 | 选型 | 理由 |
|------|------|------|
| 框架复用 | `session_workflow_matrix` | 已有 MockKernel + 断言引擎，零额外依赖 |
| Case 定义方式 | `SessionWorkflowCase` dataclass | 与现有6个cases完全一致，统一注册表 |
| 状态断言 | `final_state_assertions: dict` | 可验证 `structured_findings`、`turn_count`、`task_progress` |
| 标签分类 | `tags=("swm", "cognitive", "belief-revision")` | 便于过滤运行特定类别 |

---

## 6. 验证门禁

每个新增 case 必须通过：
1. `ruff check <paths> --fix && ruff format <paths>` — 静默
2. `mypy <paths>` — "Success: no issues found"
3. `pytest polaris/cells/llm/evaluation/tests/test_session_workflow_matrix.py -v` — 100% PASS
4. `pytest polaris/cells/llm/evaluation/tests/test_session_workflow_matrix.py::TestCaseExecution -v` — 新增3个case执行通过

---

## 7. 下一步行动

1. **Phase A**: 在 `session_workflow_matrix.py` 中追加3个L3 cases
2. **Phase B**: 更新 `test_session_workflow_matrix.py` 覆盖新增cases
3. **Phase C**: 运行验证门禁，确保100%通过
