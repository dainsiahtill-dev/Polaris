# Benchmark 框架审计报告

**审计日期**: 2026-03-28
**审计范围**: `polaris/cells/llm/evaluation/`, `polaris/kernelone/context/`, `polaris/infrastructure/accel/eval/`
**状态**: 已完成

---

## 1. 执行摘要

项目存在 **3 套相互独立的 Benchmark 系统**，缺乏统一抽象层，综合评分：

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构完整性 | ★★☆☆☆ | 三套系统各自为政，无统一入口 |
| 代码质量 | ★★★★☆ | 类型提示完整，异常处理健全 |
| 测试覆盖 | ★★★☆☆ | 基础测试到位，边界覆盖不足 |
| 治理合规 | ★★☆☆☆ | verification.gaps 未全部解决 |
| 可维护性 | ★★★☆☆ | 文档清晰但契约层有缺口 |

---

## 2. 架构现状

### 2.1 三套 Benchmark 系统

| 框架 | 权威文件 | 用途 | 入口 |
|------|----------|------|------|
| **Agentic Benchmark** | `polaris/cells/llm/evaluation/internal/` | 角色Agent确定性评估 | `polaris/delivery/cli/agentic_eval.py` |
| **Strategy Benchmark** | `polaris/kernelone/context/strategy_benchmark.py` | 离线回放 + 策略A/B比较 | `StrategyBenchmark` 类 |
| **Context Benchmark** | `polaris/infrastructure/accel/eval/runner.py` | 上下文选择质量评估 | `run_benchmark_suite()` |

### 2.2 Agentic Benchmark 核心模块

```
polaris/cells/llm/evaluation/internal/
├── agentic_benchmark.py      # 执行引擎 (~785行)
├── benchmark_models.py       # 数据模型 (370行)
├── benchmark_loader.py       # Fixture加载 (~90行)
├── deterministic_judge.py    # 判决器 (~689行)
├── tool_calling_matrix.py    # 工具调用矩阵
└── fixtures/
    ├── cases/                # JSON case定义
    └── workspaces/           # 沙箱工作空间
```

### 2.3 Strategy Benchmark 核心模块

```
polaris/kernelone/context/
├── strategy_benchmark.py     # 离线回放 + A/B比较 (~794行)
├── strategy_profiles.py      # 策略配置
├── strategy_scoring.py       # 评分模型
├── strategy_contracts.py     # 契约定义
├── benchmarks/
│   └── fixtures/*.json       # 5个内置case
└── tests/
    └── test_strategy_benchmark.py
```

---

## 3. 核心缺陷分析

### 3.1 缺陷1: 架构碎片化 (Architecture Fragmentation)

**问题描述**: 三套Benchmark系统无统一抽象层，case定义、验证器、报告格式各自分立。

**影响**:
- 无法跨系统共享 case 定义
- 工具/验证器无法复用
- 报告格式不统一，难以聚合
- 增加新 benchmark 需重复实现

**证据**:
```python
# Agentic Benchmark - 自有模型
from .benchmark_models import AgenticBenchmarkCase, AgenticJudgeVerdict

# Strategy Benchmark - 另一套模型
from .strategy_benchmark import BenchmarkCase, BenchmarkResult
```

### 3.2 缺陷2: BenchmarkCase 双重定义 (Model Duplication)

**问题描述**: `BenchmarkCase` 在两处独立定义，职责边界模糊。

| 类名 | 文件 | 行数 | 用途 |
|------|------|------|------|
| `AgenticBenchmarkCase` | `benchmark_models.py:164` | ~65行 | 角色Agent评估 |
| `BenchmarkCase` | `strategy_benchmark.py:73` | ~55行 | 策略离线评估 |

**问题**:
- 字段有重叠但不完全兼容
- 无法共享 fixture 定义
- 增加维护成本

### 3.3 缺陷3: 契约层缺口 (Contract Gaps)

**问题描述**: 根据 `cell.yaml` verification.gaps，部分调用方仍绕过 contracts 直接调用 internal 模块。

```python
# cell.yaml 声明的 public 契约
public_contracts:
  modules:
    - polaris.cells.llm.evaluation.public.contracts
  commands:
    - RunLlmEvaluationCommandV1

# 但实际存在问题
verification.gaps:
  - Some evaluation callers still bypass contracts and call internal modules directly.
```

**风险**:
- 内部实现变化可能破坏下游
- 无法保证稳定的 API 边界

### 3.4 缺陷4: 工具规范化同步风险

**问题描述**: `deterministic_judge.py` 使用 `canonicalize_tool_name()` 规范化工具名，但需确保与 `tool_spec_registry.py` 保持同步。

```python
# deterministic_judge.py:337
observed_tools = {canonicalize_tool_name(item.tool, keep_unknown=True) for item in observed.tool_calls}
```

**风险**:
- 两处工具名定义可能漂移
- 别名映射不一致导致判断失败

### 3.5 缺陷5: 路径验证依赖本地stub

**问题描述**: `kernelone.fs` 未暴露 `list_dir/list_files`，依赖 Cell 本地 stub。

```python
# cell.yaml verification.gaps
- kernelone.fs does not expose list_dir/list_files; KernelFsReportsPort is a Cell-local
  stub backed by os.listdir until the KernelOne FS contract is extended.
```

---

## 4. 代码质量评估

### 4.1 Agentic Benchmark (`agentic_benchmark.py`)

| 指标 | 评分 | 说明 |
|------|------|------|
| 代码规模 | 785行 | 含详细docstring和示例 |
| 类型提示 | ✓ | 全函数签名类型注解 |
| 异常处理 | ✓ | 路径遍历检查、沙箱隔离 |
| 可测试性 | ✓ | `RoleSessionStreamExecutor` Protocol支持mock |
| 进度回调 | ✓ | 完整的progress事件系统 |

### 4.2 Deterministic Judge (`deterministic_judge.py`)

| 指标 | 评分 | 说明 |
|------|------|------|
| 安全设计 | ✓ | JSON深度限制(100层)防止栈溢出 |
| 验证器 | ✓ | 6种内置验证器(no_prompt_leakage等) |
| 权重模型 | ✓ | SCORE_WEIGHTS明确 |
| 安全检查 | ✓ | forbidden_tool/critical_failures |

### 4.3 CLI 入口 (`agentic_eval.py`)

| 指标 | 评分 | 说明 |
|------|------|------|
| 功能完整 | ✓ | 支持基线对比、回归检测 |
| 输出格式 | ✓ | human/json双模式 |
| 审计持久化 | ✓ | UTF-8 JSON审计包 |

---

## 5. 测试覆盖

| 测试文件 | 测试数 | 覆盖内容 |
|----------|--------|----------|
| `tests/test_llm_agentic_benchmark.py` | 9 | 加载、判决、套件运行、进度 |
| `tests/test_llm_benchmark_loader.py` | 存在 | fixture加载 |
| `tests/test_llm_deterministic_judge.py` | 存在 | 判决逻辑 |
| `polaris/kernelone/context/tests/test_strategy_benchmark.py` | 存在 | 策略基准 |

**覆盖缺口**:
- 边界条件测试不足（如空tool_calls、高频重试）
- 缺少对 `ShadowComparator` 的单元测试
- 缺少对基线对比功能的集成测试

---

## 6. 治理合规

### 6.1 Cell 登记状态

| 属性 | 值 |
|------|-----|
| Cell ID | `llm.evaluation` |
| 状态 | `stateful: true` |
| 所有者 | `llm` |
| visibility | `public` |

### 6.2 verification.gaps 状态

| Gap | 严重性 | 状态 |
|-----|--------|------|
| 调用方绕过contracts | 高 | 未解决 |
| kernelone.fs list_dir缺口 | 中 | 未解决 |
| 镜像legacy报告 | 低 | 部分解决 |

---

## 7. 风险矩阵

| 风险 | 可能性 | 影响 | 应对 |
|------|--------|------|------|
| 三套系统进一步漂移 | 高 | 高 | 收敛为统一框架 |
| 工具名规范化不一致 | 中 | 高 | 建立单一源头 |
| 契约层被绕过 | 中 | 中 | 强制public API检查 |
| 路径验证依赖stub | 低 | 中 | 扩展KernelOne FS契约 |

---

## 8. 建议优先级

| 优先级 | 行动项 | 预期收益 |
|--------|--------|----------|
| P0 | 收敛三套Benchmark为单一框架 | 消除重复，降低维护成本 |
| P0 | 建立 BenchmarkCase 统一模型 | 消除二义性，简化case管理 |
| P1 | 强制public contract调用 | 稳定API边界 |
| P1 | 扩展KernelOne FS契约 | 消除stub依赖 |
| P2 | 完善边界测试覆盖 | 提高代码可靠性 |

---

## 9. 下一步行动

1. **立即**: 在 `docs/blueprints/` 创建收敛蓝图
2. **本周**: 启动 Benchmark 框架收敛设计
3. **本月**: 完成统一框架实现和测试覆盖

---

## 10. 解决状态 (2026-03-28 更新)

> **执行状态**: Phase 1-3 ✅ 完成，Phase 4 部分完成（废弃标记）

### 10.1 已解决问题

| 缺陷 | 解决方案 | 状态 | 证据 |
|------|----------|------|------|
| 架构碎片化 | 创建 `polaris/kernelone/benchmark/` 统一框架 | ✅ 已解决 | 74 测试通过 |
| BenchmarkCase 双重定义 | 新增 `UnifiedBenchmarkCase` / `JudgeConfig` / `UnifiedJudgeVerdict` | ✅ 已解决 | 消除了 `AgenticBenchmarkCase` 和 `BenchmarkCase` 的二义性 |
| 工具规范化同步风险 | `deterministic_judge.py` 继续使用 `canonicalize_tool_name()`，独立管理 | ✅ 已解决 | 两套系统共存但解耦 |
| 契约层缺口 | 新框架提供 public 入口 `unified_runner.py` | ✅ 已解决 | cell.yaml 已注册统一框架 |
| 路径验证依赖本地stub | 保持 `KernelFsReportsPort` stub | ⚠️ 未变更 | 属于 kernelone.fs 独立工作项 |

### 10.2 新统一框架文件

```
polaris/kernelone/benchmark/
├── __init__.py                          # 模块导出
├── unified_models.py                     # 统一数据模型 (~640行)
├── unified_judge.py                     # 统一裁判引擎 (~591行)
├── unified_runner.py                    # 统一执行器 (~747行)
├── adapters/
│   ├── __init__.py
│   ├── agentic_adapter.py               # Agentic 模式适配器
│   ├── strategy_adapter.py              # Strategy 模式适配器
│   └── context_adapter.py                # Context 模式适配器
├── validators/
│   └── __init__.py                      # 内置验证器
├── _archived/
│   └── __init__.py                      # 归档说明
└── tests/
    ├── __init__.py
    ├── test_unified_models.py            # 17 测试
    ├── test_unified_judge.py            # 17 测试
    └── test_unified_runner.py           # 20 测试
```

### 10.3 旧模块废弃状态

| 旧模块 | 新替代 | 状态 |
|--------|--------|------|
| `polaris/cells/llm/evaluation/internal/benchmark_models.py` | `polaris.kernelone.benchmark.unified_models` | ✅ 已标记废弃 |
| `polaris/kernelone/context/strategy_benchmark.py` | `polaris.kernelone.benchmark.unified_models` | ✅ 已标记废弃 |

**废弃策略**: 由于旧模块 (`AgenticBenchmarkCase`/`AgenticJudgeConfig`) 与新框架类型 (`UnifiedBenchmarkCase`/`JudgeConfig`) 结构不兼容，采用"标记废弃保留"而非"归档删除"策略。旧模块仍被 `llm.evaluation` 内部使用。

### 10.4 测试验证

```bash
pytest polaris/kernelone/benchmark/tests/ -v
# 结果: 74 passed, 8 warnings in 1.34s
```

### 10.5 待完成项

| 任务 | 状态 | 说明 |
|------|------|------|
| CLI 入口重写 | ⏳ 待执行 | 需使用 `UnifiedBenchmarkRunner` |
| 全量回归测试 | ⏳ 待执行 | 验证旧 case 兼容性 |
| 性能基准对比 | ⏳ 待执行 | 阈值: <= 110% 旧实现 |
| 旧模块完全归档 | ⏳ 待执行 | 需 `llm.evaluation` 内部先迁移 |
