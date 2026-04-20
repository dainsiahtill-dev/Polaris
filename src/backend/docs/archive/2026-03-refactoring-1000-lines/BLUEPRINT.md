# 超过1000行文件重构蓝图

**版本**: 2026-03-31
**状态**: Draft
**负责人**: Chief Architect
**团队规模**: 10人并行执行

---

## 1. 执行摘要

### 1.1 背景

当前 `polaris/` 目录下存在多个超过1000行的核心文件，这些文件：
- 承载过多职责，违反单一职责原则
- 难以维护和测试
- 阻碍代码复用和并行开发

### 1.2 目标

将6个超1000行文件重构为模块化结构：
1. 每个文件拆分为3-5个子模块
2. 保持向后兼容（通过 `__init__.py` 重导出）
3. 测试覆盖率不降低
4. 执行周期：3天（并行执行）

### 1.3 目标文件清单

| ID | 文件 | 行数 | 优先级 | 目标行数 |
|----|------|------|--------|----------|
| F1 | `llm_caller.py` | 2869 | P0 | 4×~720 |
| F2 | `runtime/service.py` | 2095 | P0 | 3×~700 |
| F3 | `turn_engine.py` | 2033 | P0 | 4×~510 |
| F4 | `context_os/runtime.py` | 2013 | P1 | 4×~500 |
| F5 | `kernel.py` | 1761 | P2 | 3×~590 |
| F6 | `tool_loop_controller.py` | ~800 | P0 | 2×~400 |

---

## 2. 团队分工

### 2.1 团队角色

| 专家ID | 角色 | 负责文件 | 主要职责 |
|--------|------|----------|----------|
| E1 | Core Engine Lead | F3 `turn_engine.py` | 循环引擎核心 |
| E2 | Context OS Lead | F4 `context_os/runtime.py` | 上下文操作系统 |
| E3 | LLM Caller Lead | F1 `llm_caller.py` | LLM调用抽象层 |
| E4 | Service Layer Lead | F2 `runtime/service.py` | 服务层架构 |
| E5 | Kernel Core Lead | F5 `kernel.py` | 角色执行内核 |
| E6 | Controller Lead | F6 `tool_loop_controller.py` | 工具循环控制器 |
| E7 | Integration Architect | Cross-cutting | 接口契约、依赖管理 |
| E8 | Test Engineer | All | 测试适配、回归验证 |
| E9 | Documentation Lead | All | 文档更新、变更日志 |
| E10 | Quality Gate | All | 代码审查、标准检查 |

### 2.2 依赖关系图

```
                    ┌─────────────────────────────────────────┐
                    │           Integration Architect (E7)    │
                    │         接口契约、依赖管理              │
                    └────────────────┬────────────────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
        ▼                            ▼                            ▼
┌───────────────┐          ┌───────────────┐          ┌───────────────┐
│ E1: turn_engine│          │ E2: context_os│          │ E3: llm_caller│
│    核心       │◄─────────│    上下文    │──────────►│    LLM调用   │
└───────┬───────┘          └───────────────┘          └───────┬───────┘
        │                                                   │
        │                                                   │
        ▼                                                   ▼
┌───────────────┐          ┌───────────────┐          ┌───────────────┐
│ E6: controller│          │ E4: service   │          │ E5: kernel    │
│    控制器    │◄─────────│    服务层    │──────────►│    内核     │
└───────────────┘          └───────────────┘          └───────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
                    ▼                                 ▼
          ┌───────────────┐                 ┌───────────────┐
          │ E8: Test Eng  │                 │ E9: Docs Lead │
          │    测试      │                 │    文档      │
          └───────────────┘                 └───────────────┘
                                     │
                                     ▼
                           ┌───────────────┐
                           │ E10: QA Gate  │
                           │    质量门禁   │
                           └───────────────┘
```

---

## 3. 拆分策略

### 3.1 通用原则

1. **单一职责**: 每个模块只做一件事
2. **接口隔离**: 模块间通过明确的接口通信
3. **依赖倒置**: 高层模块不依赖低层模块，都依赖抽象
4. **向后兼容**: 原有导入路径必须继续有效

### 3.2 目录结构模式

**模式A: 目录替换文件**
```
# 原始
polaris/cells/roles/kernel/internal/turn_engine.py  # 2033行

# 目标
polaris/cells/roles/kernel/internal/turn_engine/
├── __init__.py      # 重导出所有公共API
├── engine.py        # TurnEngine 核心类 (~600行)
├── config.py        # 配置类 (~80行)
├── artifacts.py     # 数据类 (~150行)
└── helpers.py       # 辅助函数 (~80行)
```

**模式B: 提取独立文件**
```
# 原始
polaris/kernelone/context/context_os/runtime.py  # 2013行

# 目标
polaris/kernelone/context/context_os/
├── runtime.py       # StateFirstContextOS 核心 (~800行)
├── classifier.py    # DialogActClassifier (~250行)
├── patterns.py      # 正则模式 (~200行)
└── helpers.py       # 辅助函数 (~300行)
```

---

## 4. 各文件详细拆分计划

### 4.1 F1: llm_caller.py (2869行)

**当前职责**:
- LLM Provider 调用抽象
- Stream/NonStream 执行路径
- Prompt 构建
- 响应解析
- 错误处理与重试

**拆分方案**:

```
llm_caller/
├── __init__.py           # 重导出 LLMCaller
├── caller.py             # LLMCaller 核心类 (~600行)
├── stream_executor.py    # StreamCallExecutor (~500行)
├── non_stream_executor.py# NonStreamCallExecutor (~400行)
├── prompt_builder.py     # PromptBuilder (~300行)
├── response_parser.py    # ResponseParser (~250行)
└── retry_policy.py       # RetryPolicy (~200行)
```

**专家**: E3 (LLM Caller Lead)

---

### 4.2 F2: runtime/service.py (2095行)

**当前职责**:
- RoleRuntimeService 入口
- Session 管理
- Turn 执行协调
- Context OS 集成
- 持久化

**拆分方案**:

```
runtime/service/
├── __init__.py           # 重导出 RoleRuntimeService
├── service.py            # RoleRuntimeService 协调器 (~500行)
├── session_manager.py    # SessionLifecycleManager (~400行)
├── turn_executor.py      # TurnExecutionCoordinator (~400行)
├── context_adapter.py    # ContextOSAdapter (~350行)
└── persistence.py        # SessionPersistence (~300行)
```

**专家**: E4 (Service Layer Lead)

---

### 4.3 F3: turn_engine.py (2033行)

**当前职责**:
- 统一角色执行循环
- Stream/NonStream 循环逻辑
- Policy Layer 集成
- Tool 执行协调
- Safety State 管理

**拆分方案**:

```
turn_engine/
├── __init__.py           # 重导出 TurnEngine
├── engine.py             # TurnEngine 核心循环 (~600行)
├── stream_loop.py        # StreamLoopExecutor (~450行)
├── non_stream_loop.py    # NonStreamLoopExecutor (~400行)
├── config.py             # TurnEngineConfig, SafetyState (~120行)
└── artifacts.py          # AssistantTurnArtifacts, 辅助类 (~200行)
```

**专家**: E1 (Core Engine Lead)

---

### 4.4 F4: context_os/runtime.py (2013行)

**当前职责**:
- State-First Context OS
- Dialog Act 分类
- Episode 管理
- Artifact 管理
- Budget 规划
- Memory 搜索

**拆分方案**:

```
context_os/
├── runtime.py            # StateFirstContextOS 核心 (~800行)
├── classifier.py         # DialogActClassifier (~250行)
├── episode_manager.py    # EpisodeManager (~300行)
├── artifact_manager.py   # ArtifactManager (~250行)
├── budget_planner.py     # BudgetPlanner (~200行)
└── helpers.py            # 辅助函数 (~300行)
```

**专家**: E2 (Context OS Lead)

---

### 4.5 F5: kernel.py (1761行)

**当前职责**:
- RoleExecutionKernel 入口
- Tool 执行
- Prompt 构建
- Retry 逻辑
- Facade 方法

**拆分方案**:

```
kernel/
├── __init__.py           # 重导出 RoleExecutionKernel
├── kernel.py             # RoleExecutionKernel 核心 (~600行)
├── tool_executor.py      # ToolExecutor (~400行)
├── prompt_adapter.py     # PromptAdapter (~350行)
└── retry_handler.py      # RetryHandler (~300行)
```

**专家**: E5 (Kernel Core Lead)

---

### 4.6 F6: tool_loop_controller.py (~800行)

**当前职责**:
- Transcript 历史
- Safety Policy
- Tool Result 格式化
- Budget 估算
- Success Loop 检测

**拆分方案**:

```
tool_loop_controller.py   # ToolLoopController 核心 (~500行)
context_event.py          # ContextEvent, SafetyPolicy (~200行)
tool_result_formatter.py  # ToolResultFormatter (~150行)
```

**专家**: E6 (Controller Lead)

---

## 5. 接口契约

### 5.1 公共接口保留

所有重构必须保持以下公共接口不变：

```python
# turn_engine.py
from polaris.cells.roles.kernel.internal.turn_engine import TurnEngine, TurnEngineConfig

# context_os/runtime.py
from polaris.kernelone.context.context_os.runtime import StateFirstContextOS, DialogActClassifier

# llm_caller.py
from polaris.cells.roles.kernel.internal.llm_caller import LLMCaller

# runtime/service.py
from polaris.cells.roles.runtime.public.service import RoleRuntimeService

# kernel.py
from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel

# tool_loop_controller.py
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
```

### 5.2 模块间依赖

```
┌──────────────────────────────────────────────────────────────┐
│                      Public API Layer                        │
│  RoleRuntimeService, RoleExecutionKernel, TurnEngine         │
└──────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│ LLMCaller     │     │ ContextOS     │     │ ToolLoopCtrl  │
│ (E3)         │     │ (E2)         │     │ (E6)         │
└───────────────┘     └───────────────┘     └───────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                              ▼
                    ┌───────────────┐
                    │ Shared Types  │
                    │ (E7)         │
                    └───────────────┘
```

---

## 6. 执行时间线

### Day 1: 基础拆分

| 阶段 | 时间 | 任务 | 负责人 |
|------|------|------|--------|
| 1.1 | 09:00-10:00 | 团队同步，确认接口契约 | E7 + All |
| 1.2 | 10:00-12:00 | 提取无依赖模块（config, patterns, helpers） | E1-E6 |
| 1.3 | 14:00-17:00 | 提取数据类和辅助类 | E1-E6 |
| 1.4 | 17:00-18:00 | 集成测试检查点 | E8 |

### Day 2: 核心拆分

| 阶段 | 时间 | 任务 | 负责人 |
|------|------|------|--------|
| 2.1 | 09:00-12:00 | 核心类精简 | E1-E6 |
| 2.2 | 14:00-16:00 | 模块间接口适配 | E7 |
| 2.3 | 16:00-17:00 | 回归测试 | E8 |
| 2.4 | 17:00-18:00 | 问题修复 | E1-E6 |

### Day 3: 收尾验证

| 阶段 | 时间 | 任务 | 负责人 |
|------|------|------|--------|
| 3.1 | 09:00-11:00 | 最终测试覆盖 | E8 |
| 3.2 | 11:00-12:00 | 文档更新 | E9 |
| 3.3 | 14:00-16:00 | 代码审查 | E10 |
| 3.4 | 16:00-17:00 | 合并准备 | E7 |

---

## 7. 质量门禁

### 7.1 代码质量

- [ ] Ruff check 通过
- [ ] Ruff format 通过
- [ ] Mypy 类型检查通过
- [ ] 无循环导入

### 7.2 测试质量

- [ ] 现有测试 100% 通过
- [ ] 测试覆盖率不低于重构前
- [ ] 新增模块有单元测试

### 7.3 文档质量

- [ ] 模块 docstring 完整
- [ ] 公共 API 有类型注解
- [ ] CHANGELOG 更新

---

## 8. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 循环导入 | 高 | 中 | E7 提前定义依赖图 |
| 测试失败 | 中 | 高 | E8 持续监控 |
| 接口变更 | 低 | 高 | 严格向后兼容 |
| 进度延迟 | 中 | 中 | 每日同步会议 |

---

## 9. 附录

### A. 文件清单

- `docs/blueprints/refactoring-1000-lines-20260331/BLUEPRINT.md` - 本文档
- `docs/blueprints/refactoring-1000-lines-20260331/EXECUTION_PLAN.md` - 执行计划
- `docs/blueprints/refactoring-1000-lines-20260331/INTERFACE_CONTRACT.md` - 接口契约
- `docs/blueprints/refactoring-1000-lines-20260331/TEAM_ASSIGNMENTS.md` - 团队分配

### B. 参考资料

- `src/backend/AGENTS.md` - 后端权威入口
- `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md` - 架构标准
- `docs/governance/ci/fitness-rules.yaml` - 质量门禁规则