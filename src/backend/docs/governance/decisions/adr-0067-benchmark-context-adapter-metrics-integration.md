# ADR-0067: Benchmark Context Adapter 与孤 metrics.py 集成

**日期**: 2026-03-28
**状态**: 已接受
**决策者**: 10人 Python 架构与代码治理实验室

---

## 背景

`polaris/infrastructure/accel/eval/metrics.py` 包含完整的上下文选择评估指标实现（`recall_at_k`, `reciprocal_rank`, `symbol_hit_rate`），但该模块自创建以来一直是孤立代码，无任何文件导入使用。

同时，`polaris/kernelone/benchmark/adapters/context_adapter.py` 中的 `_evaluate_context()` 方法是 stub 实现，直接返回硬编码零值，无法进行真实的上下文选择评估。

---

## 决策

### 核心决策

**将 `infrastructure/accel/eval/metrics.py` 作为 Context adapter 的指标计算引擎**，通过 DI 注入方式调用，而非直接导入。

```python
# 方案选择：DI 注入（推荐）
class ContextBenchmarkAdapter:
    def __init__(
        self,
        metrics_calculator: MetricsCalculatorProtocol | None = None,
    ) -> None:
        self._metrics = metrics_calculator or DefaultMetricsCalculator()

# 拒绝方案：直接 import（耦合过高）
# from polaris.infrastructure.accel.eval.metrics import recall_at_k  # ❌
```

### 原因

| 方案 | 优点 | 缺点 |
|------|------|------|
| 直接 import | 简单 | 硬耦合，测试困难 |
| DI 注入 | 可测试，可mock，可降级 | 稍复杂 |
| 完全重写 | 完全控制 | 重复造轮子，浪费已有实现 |

---

## 架构图

```
legacy isolated code                 new unified framework
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

infrastructure/accel/eval/          benchmark/adapters/
├── metrics.py          ──────────→  context_adapter.py
│   ├── recall_at_k()              │   ├── DI 注入 metrics
│   ├── reciprocal_rank()          │   ├── 调用 compile_context()
│   └── symbol_hit_rate()          │   └── 返回 ObservedBenchmarkRun
└── runner.py (保留，无调用方)        └── context_fixture_mapper.py
                                          └── 格式转换
```

---

## 变更清单

| 文件 | 操作 | 理由 |
|------|------|------|
| `infrastructure/accel/eval/__init__.py` | 更新 | 添加显式 `__all__` 导出 metrics |
| `benchmark/adapters/context_adapter.py` | 重写 | 实现真实 `_evaluate_context_metrics()` |
| `benchmark/adapters/context_fixture_mapper.py` | 新增 | 格式映射 |
| `benchmark/tests/test_context_adapter.py` | 新增 | 覆盖测试 |

---

## 向后兼容

- `infrastructure/accel/eval/runner.py` **保留**，无调用方但不失为一种 reference implementation
- `metrics.py` 原有接口**不变**，仅新增调用方

---

## 验证

```bash
# 验证 metrics.py 被引用
grep -r "from.*infrastructure.accel.eval.metrics import" polaris/

# 预期输出：
# polaris/kernelone/benchmark/adapters/context_adapter.py
```

---

## 影响

| 影响范围 | 说明 |
|----------|------|
| `infrastructure/accel/eval/` | 从孤立变为被依赖 |
| `benchmark/adapters/` | Context adapter 有真实实现 |
| 测试 | 新增 20+ 测试用例 |

---

## 副作用

- `metrics.py` 中的函数假设 `expected_files` 和 `predicted_files` 为 `list[str]`，adapter 需确保类型正确
- `symbol_hit_rate` 暂未使用，保留接口以备将来 Context+Symbol 混合评估
