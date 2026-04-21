# Superpowers 设计精华 → Polaris 原生架构转化蓝图

## 版本: v1.0
## 日期: 2026-04-21
## 状态: 规划中

---

## 1. 设计原则

**核心信条**: 不是"引入skill"，而是"提取模式，工程化落地"。

转化原则:
1. **概念抽象**: 提取Superpowers的行为模式，转化为Polaris的Cell/契约/事件
2. **原生实现**: 用Polaris的工程规范（PEP 8/Ruff/MyPy/pytest）编写生产级Python代码
3. **架构适配**: 适配Polaris的TurnTransaction单决策法则、Cell边界、Context Plane
4. **零侵入**: 不修改现有核心契约，通过新增Cell或扩展现有Cell实现

---

## 2. 精华提取与转化映射

### 2.1 Verification Before Completion → VerificationGuard Cell

**Superpowers精华**:
- "没有新鲜验证证据就不能声称完成"
- 门控函数: 识别→运行→读取→验证→声明

**Polaris转化**:
- 新增 `polaris.cells.factory.verification_guard` Cell
- 职责: 在Turn完成前强制执行验证检查点
- 契约: `command: VerifyCompletion` → `result: VerificationReport`
- 集成点: `TurnTransactionController.commit()` 之前调用

### 2.2 Subagent-driven Development → SubagentTaskDispatcher Cell

**Superpowers精华**:
- 每任务一个子代理 + 两阶段审查
- 上下文隔离: 主代理精选上下文派发给子代理

**Polaris转化**:
- 新增 `polaris.cells.factory.subagent_dispatcher` Cell
- 职责: 任务级子代理调度、上下文裁剪、结果聚合
- 契约: `command: DispatchSubagentTask` → `event: SubagentCompleted`
- 约束: 遵守 `len(ToolBatches) <= 1` 单决策法则

### 2.3 Systematic Debugging → DebugStrategyEngine

**Superpowers精华**:
- 四阶段调试: 根因调查→模式分析→假设测试→实施
- 防御性编程四层验证

**Polaris转化**:
- 增强现有 `polaris.cells.roles.kernel.internal.error_classifier`
- 新增 `DebugStrategyEngine` 策略选择器
- 职责: 根据错误类型自动选择调试策略
- 集成点: `ErrorClassifier` → `DebugStrategyEngine` → `RetryOrchestrator`

### 2.4 Dispatching Parallel Agents → ParallelTaskExecutor

**Superpowers精华**:
- 多故障域并行调查
- 3x效率提升

**Polaris转化**:
- 新增 `ParallelTaskExecutor` 组件（位于 `polaris.kernelone.parallel`）
- 职责: 安全地并行执行独立任务
- 约束: 文件冲突检测、状态竞争预防
- 集成点: `StreamShadowEngine` 的 speculative 分支扩展

### 2.5 Writing Skills Methodology → SkillValidationFramework

**Superpowers精华**:
- TDD原则应用于技能文档: 压力场景→编写→验证
- 合理化预防表、红旗列表

**Polaris转化**:
- 新增 `polaris.cells.context.catalog.internal.skill_validator`
- 职责: 技能文件的自动化测试和验证
- 输出: `verification.pack.json` 的自动生成
- 集成点: CI/CD 管道中的 `structural_bug_governance_gate`

---

## 3. 实施优先级

```
Phase 1 (最高优先级):
  ├── VerificationGuard Cell
  │   └── 解决"虚假完成声明"问题
  │   └── 集成点明确，风险低
  │
  └── DebugStrategyEngine
      └── 增强现有ErrorClassifier
      └── 解决"头痛医头"问题

Phase 2 (高优先级):
  ├── SubagentTaskDispatcher Cell
  │   └── 解决Director单点瓶颈
  │   └── 需适配TurnTransaction约束
  │
  └── ParallelTaskExecutor
      └── 扩展StreamShadowEngine
      └── 解决复杂场景效率问题

Phase 3 (中优先级):
  └── SkillValidationFramework
      └── 治理门禁增强
      └── 长期工程收益
```

---

## 4. 架构设计

### 4.1 VerificationGuard Cell

```
┌─────────────────────────────────────────┐
│         VerificationGuard Cell          │
├─────────────────────────────────────────┤
│                                         │
│  Input: CompletionClaim                 │
│    ├── claimed_outcome: str            │
│    ├── verification_commands: list[str]│
│    └── evidence_paths: list[str]       │
│                                         │
│  Process:                               │
│    1. Validate commands are safe       │
│    2. Execute commands                 │
│    3. Parse output                     │
│    4. Match against claimed_outcome    │
│                                         │
│  Output: VerificationReport             │
│    ├── status: PASS / FAIL / BLOCKED   │
│    ├── evidence: dict                  │
│    └── recommendations: list[str]      │
│                                         │
└─────────────────────────────────────────┘
```

### 4.2 SubagentTaskDispatcher Cell

```
┌─────────────────────────────────────────┐
│      SubagentTaskDispatcher Cell        │
├─────────────────────────────────────────┤
│                                         │
│  Input: TaskPackage                     │
│    ├── task_spec: TaskSpec             │
│    ├── context_budget: int             │
│    └── review_required: bool           │
│                                         │
│  Process:                               │
│    1. ContextPruner: 裁剪上下文        │
│    2. ModelSelector: 选择模型          │
│    3. SubagentExecutor: 执行子代理     │
│    4. SpecReviewer: 规范审查           │
│    5. QualityReviewer: 质量审查        │
│    6. ResultAggregator: 聚合结果       │
│                                         │
│  Output: SubagentResult                 │
│    ├── code_changes: list[Change]      │
│    ├── review_notes: list[Note]        │
│    └── confidence: float               │
│                                         │
└─────────────────────────────────────────┘
```

### 4.3 DebugStrategyEngine

```
┌─────────────────────────────────────────┐
│        DebugStrategyEngine              │
├─────────────────────────────────────────┤
│                                         │
│  Input: ErrorContext                    │
│    ├── error_type: str                 │
│    ├── stack_trace: str                │
│    ├── recent_changes: list[Change]    │
│    └── environment: dict               │
│                                         │
│  Process:                               │
│    1. ErrorClassifier: 分类错误        │
│    2. StrategySelector: 选择策略       │
│    3. HypothesisGenerator: 生成假设    │
│    4. EvidenceCollector: 收集证据      │
│    5. FixValidator: 验证修复           │
│                                         │
│  Output: DebugStrategy                  │
│    ├── strategy_type: enum             │
│    ├── steps: list[Step]               │
│    └── rollback_plan: Plan             │
│                                         │
└─────────────────────────────────────────┘
```

---

## 5. 实施状态

### Phase 1 (已完成 ✅)

| 组件 | 位置 | 测试 | 状态 |
|------|------|------|------|
| **VerificationGuard Cell** | `polaris/cells/factory/verification_guard/` | 46 passed | ✅ 完成 |
| **DebugStrategyEngine** | `polaris/cells/roles/kernel/internal/debug_strategy/` | 81 passed | ✅ 完成 |

**质量门禁**:
- ✅ Ruff: All checks passed
- ✅ MyPy --strict: Success, no issues found
- ✅ pytest: 127 passed (46 + 81)

### Phase 2 (待实施)

| 组件 | 预计工时 | 依赖 |
|------|----------|------|
| SubagentTaskDispatcher Cell | 3天 | VerificationGuard |
| ParallelTaskExecutor | 2天 | StreamShadowEngine扩展 |

### Phase 3 (待实施)

| 组件 | 预计工时 | 依赖 |
|------|----------|------|
| SkillValidationFramework | 2天 | CI/CD管道 |

---

## 6. 技术选型

| 组件 | 技术栈 | 理由 |
|------|--------|------|
| VerificationGuard | Pure Python + subprocess | 安全执行命令，零依赖 |
| SubagentDispatcher | asyncio + Cell契约 | 遵守Polaris异步模型 |
| DebugStrategyEngine | 状态机 + 策略模式 | 可扩展的调试策略 |
| ParallelTaskExecutor | asyncio.TaskGroup | Python原生并发，安全取消 |
| SkillValidator | pytest + AST解析 | 利用现有测试基础设施 |

---

## 7. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 子代理绕过Single State Owner | 子代理只读，修改必须通过主代理提交 |
| 并行执行文件冲突 | 文件锁 + 冲突检测 + 自动回退 |
| 验证命令安全风险 | 命令白名单 + 沙箱执行 |
| 上下文裁剪过度 | 保留关键契约 + 可配置预算 |

---

## 7. 验证计划

每个组件必须通过:
1. **Ruff**: 零错误
2. **MyPy --strict**: 零类型错误
3. **pytest**: 100%测试覆盖（Happy Path + Edge Cases + Exceptions）
4. **集成测试**: 与现有Cell的契约兼容性

---

*本蓝图为 Polaris Backend 架构文档，遵循 AGENTS.md 规范*
