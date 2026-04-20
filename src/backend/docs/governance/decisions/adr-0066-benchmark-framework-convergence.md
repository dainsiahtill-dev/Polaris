# ADR-0066: Benchmark 框架收敛

**状态**: 已接受
**日期**: 2026-03-28
**决策者**: Python 架构与代码治理实验室

---

## 背景

项目存在 3 套相互独立的 Benchmark 系统:
- **Agentic Benchmark** (`polaris/cells/llm/evaluation/internal/`)
- **Strategy Benchmark** (`polaris/kernelone/context/strategy_benchmark.py`)
- **Context Benchmark** (`polaris/infrastructure/accel/eval/runner.py`)

这种碎片化导致:
1. `BenchmarkCase` 在两处独立定义
2. 工具规范化逻辑分散
3. 报告格式不统一
4. 契约层存在缺口

## 决策

**收敛为单一 Benchmark 框架**，统一入口，统一模型，统一报告。

### 统一模型: `UnifiedBenchmarkCase`

```python
@dataclass(frozen=True, kw_only=True)
class UnifiedBenchmarkCase:
    case_id: str
    role: str
    title: str
    prompt: str
    description: str = ""
    workspace_fixture: str = ""
    expected_evidence_path: tuple[FilePath, ...] = field(default_factory=tuple)
    expected_answer_shape: str = "answer"
    budget_conditions: BudgetConditions = field(default_factory=BudgetConditions)
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    # ... 统一字段
```

### 统一裁判: `UnifiedJudge`

单一裁判引擎，支持可插拔验证器策略。

### 统一执行: `UnifiedBenchmarkRunner`

单一执行器，通过 `mode` 参数适配 Agentic/Strategy/Context 三种场景。

## 后果

### 正面

- 消除 `BenchmarkCase` 双重定义
- 统一报告格式，便于聚合分析
- 简化新 benchmark 添加流程
- 提高代码可维护性

### 负面

- 需要迁移现有 case 定义
- 需要更新调用方代码
- 短期内部署复杂度增加

## 实施计划

| 阶段 | 时间 | 内容 |
|------|------|------|
| Phase 1 | Week 1-2 | 统一模型层 |
| Phase 2 | Week 3-4 | 统一裁判层 |
| Phase 3 | Week 5-6 | 统一执行层 |
| Phase 4 | Week 7-8 | 清理与归档 |

## 参考文档

- 审计报告: `docs/audit/benchmark-framework-audit-20260328.md`
- 蓝图: `docs/blueprints/benchmark-framework-convergence-blueprint-20260328.md`
- 验证卡片: `docs/governance/templates/verification-cards/vc-20260328-benchmark-framework-convergence.md`

---

## 实施状态 (2026-03-28 更新)

| 阶段 | 状态 | 完成日期 |
|------|------|----------|
| Phase 1: 统一模型层 | ✅ 完成 | 2026-03-28 |
| Phase 2: 统一裁判层 | ✅ 完成 | 2026-03-28 |
| Phase 3: 统一执行层 | ✅ 完成 | 2026-03-28 |
| Phase 4: 清理与归档 | ⚠️ 部分完成 | — |

**Phase 4 说明**: 由于旧模块类型系统与新框架不兼容，采用"标记废弃保留"策略。CLI 入口重写和性能基准对比待完成。

### 已交付文件

```
polaris/kernelone/benchmark/
├── unified_models.py          # UnifiedBenchmarkCase, JudgeConfig, UnifiedJudgeVerdict
├── unified_judge.py           # UnifiedJudge + 内置验证器
├── unified_runner.py          # UnifiedBenchmarkRunner + 报告生成
├── adapters/                  # Agentic/Strategy/Context 适配器
└── tests/                     # 74 测试 (17+17+20)
```

### 测试验证

```bash
pytest polaris/kernelone/benchmark/tests/ -v
# 74 passed, 8 warnings in 1.34s
```
