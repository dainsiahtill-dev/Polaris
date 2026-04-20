# Polaris Kernel 测试修复蓝图

**项目代号**: Kernel-Stabilization-2026Q2
**目标**: 修复剩余143个测试失败，达到100%测试通过率
**团队规模**: 10人精英工程师团队
**截止日期**: 2026-04-15

---

## 1. 系统架构现状分析

### 1.1 当前测试状态快照

```
Total: 822 tests
- Passed: 678 (82.4%)
- Failed: 143 (17.4%)
- Errors: 2 (0.2%)
- Skipped: 1
```

### 1.2 失败测试分类（根因分析）

| 类别 | 测试文件 | 失败数 | 根因 | 修复复杂度 |
|------|---------|-------|------|----------|
| **A. API契约变更** | test_llm_caller_text_fallback.py | 16 | LLMCaller接口重构，文本回退逻辑变更 | 中 |
| **B. 指标系统迁移** | test_metrics.py | 7 | MetricsCollector迁移到kernelone | 低 |
| **C. 流式/非流式一致性** | test_run_stream_parity.py, test_stream_parity.py | 18 | run()与run_stream()输出格式差异 | 高 |
| **D. 输出解析器重构** | test_pydantic_output_parser.py | 3 | OutputParser依赖注入变更 | 中 |
| **E. 事务控制器** | test_transaction_controller.py | 3 | 返回数据结构变更 | 中 |
| **F. 策略层集成** | test_turn_engine_policy_convergence.py | 2 | PolicyLayer集成方式变更 | 高 |
| **G. 杂项** | 其他20+文件 | 94 | 各种mock、导入、断言问题 | 低-中 |

### 1.3 架构变更影响地图

```
TurnEngine Facade模式重构
├── 依赖注入架构
│   ├── LLMInvoker (新) ← test_llm_caller_*.py 失败
│   ├── ToolExecutor (新) ← test_*_tool_*.py 部分失败
│   └── PromptBuilder DI ← _patch_prompt_builder 失效
├── Stream/Non-stream 统一
│   ├── run() → run_stream() 包装
│   └── 输出格式标准化 ← test_*_parity.py 失败
└── PolicyLayer 集成
    ├── 预算检查前置
    └── 工具调用拦截 ← test_*_policy_*.py 失败
```

---

## 2. 修复策略设计

### 2.1 核心原则

1. **向后兼容优先**: 不修改生产代码行为，只调整测试以匹配实际行为
2. **测试即文档**: 测试代码必须反映组件的真实使用方式
3. **分阶段收敛**: 按依赖关系排序，先修基础设施，后修业务逻辑

### 2.2 技术选型

- **测试框架**: pytest 8.x + pytest-asyncio
- **Mock框架**: unittest.mock + pytest-monkeypatch
- **类型检查**: mypy --strict
- **代码规范**: ruff check + ruff format

### 2.3 修复模式库

| 模式 | 问题描述 | 修复方案 | 适用场景 |
|-----|---------|---------|---------|
| **DI适配** | `kernel._prompt_builder` 为 None | 使用 `kernel._injected_prompt_builder = mock` | _build_kernel() 辅助函数 |
| **Whitelist对齐** | 工具调用未被识别 | 确保工具在profile.whitelist中 | 工具相关测试 |
| **流式输出标准化** | 增量chunk vs 完整内容 | 使用 `rendered = "".join(chunks)` 比较 | 流式测试 |
| **响应结构更新** | `result['metrics']['state_trajectory']` KeyError | 更新为新的 `RoleTurnResult` 结构 | 结果验证测试 |
| **Async Mock** | 同步mock用于异步函数 | 使用 `AsyncMock` 或 `async def` | LLM caller mock |

---

## 3. 模块职责划分（10人团队）

### 3.1 团队组织架构

```
Principal Architect (You)
├── Team Alpha: 基础设施层 (2人)
├── Team Beta: LLM调用层 (2人)
├── Team Gamma: 流式一致性 (2人)
├── Team Delta: 工具执行层 (2人)
├── Team Epsilon: 策略与指标 (2人)
```

### 3.2 详细分工

#### Team Alpha: 基础设施层修复
**负责人**: 资深平台工程师 x 2
**范围**:
- `test_metrics.py` (7 tests)
- `test_kernel_config.py` (如有失败)
- `test_conversation_state.py`
- `test_context_compressor.py`

**关键任务**:
1. 更新 `MetricsCollector` mock 以匹配 `kernelone` 新位置
2. 修复配置类测试中的冻结状态检查
3. 更新 `ConversationState` 的测试数据构造

---

#### Team Beta: LLM调用层修复
**负责人**: LLM集成专家 x 2
**范围**:
- `test_llm_caller_text_fallback.py` (16 tests)
- `test_llm_caller.py` (2 errors)
- `test_pydantic_output_parser.py` (3 tests)

**关键任务**:
1. 重构 `_MockLLMCaller` 以支持新的 `ILLMInvoker` 接口
2. 更新文本回退测试，使用 `call_stream` 而非 `call`
3. 修复 Pydantic 解析器的依赖注入方式

---

#### Team Gamma: 流式/非流式一致性
**负责人**: 并发/流式专家 x 2
**范围**:
- `test_run_stream_parity.py` (9 tests)
- `test_stream_parity.py` (9 tests)
- `test_stream_visible_output_contract.py` (已修复，保持)

**关键任务**:
1. 标准化 `run()` 和 `run_stream()` 的输出格式
2. 修复 `transcript` 积累逻辑差异
3. 确保工具调用事件在两种模式下一致

---

#### Team Delta: 工具执行层
**负责人**: 工具系统专家 x 2
**范围**:
- `test_transaction_controller.py` (3 remaining tests)
- `test_decision_decoder.py` (如有失败)
- `test_exploration_workflow.py` (如有失败)

**关键任务**:
1. 更新 `TransactionController` 测试以使用新的返回结构
2. 修复工具批处理测试中的异步mock
3. 更新决策解码器的测试数据

---

#### Team Epsilon: 策略层与指标
**负责人**: 策略引擎专家 x 2
**范围**:
- `test_turn_engine_policy_convergence.py` (2 tests)
- 杂项测试清理 (94 tests across 20+ files)

**关键任务**:
1. 更新 `PolicyLayer` 集成测试
2. 批量修复简单mock/导入问题
3. 最终回归测试和验证

---

## 4. 核心数据流

### 4.1 测试修复工作流

```
Analyze Failure
      ↓
Identify Root Cause (使用 debug 脚本)
      ↓
Select Fix Pattern (从模式库)
      ↓
Implement Fix
      ↓
Run pytest -v (单文件)
      ↓
Run mypy --strict (类型检查)
      ↓
Run ruff check (代码规范)
      ↓
Commit with message: "fix(test): [component] [test_name]"
      ↓
PR Review (self-check)
```

### 4.2 依赖关系图

```
Team Alpha (基础设施)
       ↓
Team Beta (LLM层) ← Team Alpha done
       ↓
Team Gamma (流式) ← Team Beta done
       ↓
Team Delta (工具层) ← Team Gamma done
       ↓
Team Epsilon (策略+清理) ← All above done
```

---

## 5. 交付标准

### 5.1 测试通过率
- **目标**: 100% (822/822 tests passing)
- **可接受**: ≥99% (允许1-2个 flaky tests)

### 5.2 代码质量标准
- mypy --strict: 0 errors
- ruff check: 0 errors
- ruff format: 已应用
- 测试覆盖率: ≥85%

### 5.3 文档要求
- 每个修复文件头添加修复注释
- 复杂修复在代码中添加 inline comment

---

## 6. 风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|-----|-------|------|---------|
| 测试设计缺陷需重构 | 中 | 高 | 区分"修复测试"vs"修复bug" |
| 发现真实bug | 低 | 高 | 立即停止，创建bug修复任务 |
| 团队间冲突 | 低 | 中 | 严格的文件级分工 |
| 性能退化 | 低 | 中 | 每轮修复后运行性能基准 |

---

## 7. 验收清单

- [ ] 所有822个测试通过
- [ ] mypy --strict 无错误
- [ ] ruff check 无错误
- [ ] CI/CD 流水线绿色
- [ ] 代码审查完成
- [ ] 文档更新完成

---

**Blueprint Version**: 1.0
**Created**: 2026-04-01
**Author**: Principal Architect
**Reviewers**: Team Leads (Alpha through Epsilon)
