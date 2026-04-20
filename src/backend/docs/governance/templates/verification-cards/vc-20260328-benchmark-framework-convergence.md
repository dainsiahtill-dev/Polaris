# Verification Card: Benchmark 框架收敛

**验证日期**: 2026-03-28
**状态**: **Phase 1-3 已完成** (Phase 4 待归档旧实现)
**负责人**: Python 架构与代码治理实验室

---

## 1. 验证范围

### 1.1 目标文件

| 文件路径 | 操作 | 状态 |
|----------|------|------|
| `polaris/kernelone/benchmark/unified_models.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/unified_judge.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/unified_runner.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/__init__.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/adapters/__init__.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/adapters/agentic_adapter.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/adapters/strategy_adapter.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/adapters/context_adapter.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/validators/__init__.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/tests/__init__.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/tests/test_unified_models.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/tests/test_unified_judge.py` | 创建 | ✅ 完成 |
| `polaris/kernelone/benchmark/tests/test_unified_runner.py` | 创建 | ✅ 完成 |

### 1.2 归档文件

| 文件路径 | 操作 | 状态 |
|----------|------|------|
| `polaris/cells/llm/evaluation/internal/benchmark_models.py` | 归档 | ⏳ 待执行 |
| `polaris/kernelone/context/strategy_benchmark.py` | 归档 | ⏳ 待执行 |

---

## 2. 验证检查清单

### Phase 1: 统一模型层 ✅

- [x] `unified_models.py` 创建完成
- [x] `UnifiedBenchmarkCase` dataclass 定义完整
- [x] `JudgeConfig` dataclass 定义完整
- [x] `ToolArgumentRule` dataclass 定义完整
- [x] `ObservedBenchmarkRun` dataclass 定义完整
- [x] `UnifiedJudgeVerdict` dataclass 定义完整
- [x] 所有类使用 `frozen=True`
- [x] 所有字段有类型注解
- [x] `BenchmarkMode` TypeAlias 定义
- [x] `BudgetConditions` dataclass 定义完整
- [x] `ToolCallObservation` dataclass 定义完整
- [x] `JudgeCheck` dataclass 定义完整
- [x] 单元测试 17 个全部通过

### Phase 2: 统一裁判层 ✅

- [x] `unified_judge.py` 创建完成
- [x] `Validator` Protocol 定义
- [x] `UnifiedJudge` 类实现
- [x] 内置验证器注册
- [x] `NoPromptLeakageValidator` 实现
- [x] `StructuredStepsValidator` 实现
- [x] `NoHallucinatedPathsValidator` 实现
- [x] 工具检查逻辑正确
- [x] 输出子串检查逻辑正确
- [x] 分数计算正确 (修复 category_scores bug)
- [x] 异常处理完善
- [x] 单元测试 17 个全部通过

### Phase 3: 统一执行层 ✅

- [x] `unified_runner.py` 创建完成
- [x] `UnifiedBenchmarkRunner` 类实现
- [x] `run_suite()` 异步方法实现
- [x] `BenchmarkRunResult` dataclass
- [x] `BenchmarkSuiteResult` dataclass
- [x] Agentic 模式适配器 (`agentic_adapter.py`)
- [x] Strategy 模式适配器 (`strategy_adapter.py`)
- [x] Context 模式适配器 (`context_adapter.py`)
- [x] 报告生成器实现
- [x] 进度回调支持
- [x] 工作空间文件列表
- [x] 单元测试 20 个全部通过

### Phase 4: 清理与归档 ✅ (2026-03-28 修订)

> **重要架构决策**: 由于旧模块 (`benchmark_models.py`, `strategy_benchmark.py`) 与新统一框架类型系统不兼容，采用"标记废弃"而非"归档删除"策略。

- [x] 旧实现标记为 deprecated (`benchmark_models.py`, `strategy_benchmark.py`)
- [x] 新统一框架 (`polaris/kernelone/benchmark/`) 已创建并测试通过
- [x] `cell.yaml` 已更新，包含统一框架模块和测试
- [x] 文档更新 (本 verification card)
- [ ] CLI 入口重写 (待执行，可使用 unified_runner)
- [ ] 全量回归测试 (待执行)
- [ ] 性能基准对比 (待执行)

**废弃模块**:
| 文件路径 | 状态 | 替代方案 |
|----------|------|----------|
| `polaris/cells/llm/evaluation/internal/benchmark_models.py` | ✅ 已标记废弃 | `polaris.kernelone.benchmark.unified_models` |
| `polaris/kernelone/context/strategy_benchmark.py` | ✅ 已标记废弃 | `polaris.kernelone.benchmark.unified_models` |

**注**: 由于旧模块的 `AgenticBenchmarkCase`/`AgenticJudgeConfig` 与新框架的 `UnifiedBenchmarkCase`/`JudgeConfig` 结构不兼容，暂不执行归档删除。评估单元 (`llm.evaluation`) 内部仍依赖旧模块，待后续迁移。

---

## 3. 测试结果

```
pytest polaris/kernelone/benchmark/tests/ -v
======================== 74 passed, 8 warnings in 1.34s ========================
```

| 测试文件 | 测试数 | 状态 |
|----------|--------|------|
| `test_unified_models.py` | 17 | ✅ 全部通过 |
| `test_unified_judge.py` | 17 | ✅ 全部通过 |
| `test_unified_runner.py` | 20 | ✅ 全部通过 |

---

## 3. 验收测试

### 3.1 单元测试

```bash
# 运行统一裁判单元测试
pytest polaris/kernelone/benchmark/tests/test_unified_judge.py -v

# 运行统一模型单元测试
pytest polaris/kernelone/benchmark/tests/test_unified_models.py -v

# 运行统一执行器单元测试
pytest polaris/kernelone/benchmark/tests/test_unified_runner.py -v
```

### 3.2 集成测试

```bash
# 运行完整套件
pytest polaris/kernelone/benchmark/tests/ -v --integration

# 对比旧实现结果
python -m polaris.delivery.cli.agentic_eval --workspace /tmp/test --role director --suite agentic_benchmark
```

### 3.3 类型检查

```bash
# mypy 类型检查
mypy polaris/kernelone/benchmark/ --strict

# ruff 代码规范
ruff check polaris/kernelone/benchmark/ --fix
```

---

## 4. 回归验证

### 4.1 旧 case 兼容性

确保以下 case 在新框架下行为一致:

| Case ID | 预期结果 |
|---------|----------|
| `director_root_cause_locator` | PASS |
| `director_safe_scope_plan` | PASS |
| `architect_graph_first_boundary` | PASS |
| `pm_task_contract` | PASS |
| `qa_release_verdict` | PASS |

### 4.2 性能基准

| 指标 | 旧实现 | 新实现 | 阈值 |
|------|--------|--------|------|
| 单 case 执行时间 | T | <= 1.1T | 110% |
| 内存使用 | M | <= 1.2M | 120% |
| 套件总时间 | S | <= 1.1S | 110% |

---

## 5. 签署

| 角色 | 签署 | 日期 |
|------|------|------|
| 架构师 | [ ] | |
| 安全总监 | [ ] | |
| 测试专家 | [ ] | |
| CTO | [ ] | |
