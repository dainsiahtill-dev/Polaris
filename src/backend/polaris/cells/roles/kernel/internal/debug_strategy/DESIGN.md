# DebugStrategyEngine 设计文档

## 概述

DebugStrategyEngine是基于Superpowers的"Systematic Debugging"设计精华，为Polaris内核提供的原生系统化调试策略引擎。

## 核心设计

### 四阶段调试流程

1. **根因调查 (ROOT_CAUSE_INVESTIGATION)**
   - 收集错误现场信息
   - 定位错误发生点
   - 查看相关变更历史

2. **模式分析 (PATTERN_ANALYSIS)**
   - 分析数据流和调用链
   - 对比已知模式
   - 识别时序依赖

3. **假设测试 (HYPOTHESIS_TESTING)**
   - 生成可能原因假设
   - 设计验证实验
   - 验证修复方案

4. **实施 (IMPLEMENTATION)**
   - 修复根因（不是症状）
   - 添加防御性检查
   - 验证修复效果

### 防御性编程四层验证

每个策略都支持四层防御检查点：

1. **输入验证层 (INPUT_VALIDATION)** - 验证所有输入
2. **前置条件层 (PRECONDITION_CHECK)** - 检查操作前提
3. **不变量断言层 (INVARIANT_ASSERTION)** - 维护关键不变量
4. **后置条件验证层 (POSTCONDITION_VERIFY)** - 验证操作结果

### 五种调试策略

| 策略 | 适用场景 | 核心方法 |
|------|---------|---------|
| **反向追溯 (TRACE_BACKWARD)** | 运行时/逻辑错误 | 从错误点回溯数据流 |
| **模式匹配 (PATTERN_MATCH)** | 语法/配置/API错误 | 对比工作示例找差异 |
| **二分定位 (BINARY_SEARCH)** | 回归错误 | git bisect快速定位 |
| **条件等待 (CONDITIONAL_WAIT)** | 时序/竞态问题 | 条件等待解决时序 |
| **防御深度 (DEFENSE_IN_DEPTH)** | 边界/验证/状态错误 | 四层验证法 |

## 架构

```
polaris/cells/roles/kernel/internal/debug_strategy/
├── __init__.py                    # 包导出
├── types.py                       # 枚举类型定义
├── models.py                      # 数据模型
├── strategy_engine.py             # 策略引擎主类
├── hypothesis_generator.py        # 假设生成器
├── evidence_collector.py          # 证据收集器
├── enhanced_error_classifier.py   # 增强版错误分类器
├── strategies/
│   ├── __init__.py               # 策略包导出
│   ├── base.py                   # 策略基类
│   ├── trace_backward.py         # 反向追溯策略
│   ├── pattern_match.py          # 模式匹配策略
│   ├── binary_search.py          # 二分定位策略
│   ├── conditional_wait.py       # 条件等待策略
│   └── defense_in_depth.py       # 防御深度策略
└── tests/
    ├── test_debug_strategy_engine.py  # 引擎测试
    ├── test_models.py                 # 模型测试
    ├── test_hypothesis_generator.py   # 假设生成器测试
    └── test_evidence_collector.py     # 证据收集器测试
```

## 与现有ErrorClassifier集成

```python
from polaris.cells.roles.kernel.internal.debug_strategy import EnhancedErrorClassifier

classifier = EnhancedErrorClassifier()
result = classifier.classify_with_strategy(
    error=exception,
    context={"file_path": "main.py", "line_number": 42}
)

# result包含：
# - basic_classification: 基本分类
# - category: 错误类别
# - severity: 严重程度
# - root_cause_likely: 可能的根因
# - debug_plan: 调试计划（包含步骤、回滚策略等）
# - suggested_strategies: 建议的策略列表
```

## 关键特性

1. **策略优先级**: ConditionalWait > PatternMatch > DefenseInDepth > BinarySearch > TraceBackward
2. **"先调查后修复"**: 任何策略的第一步都是信息收集，不立即修改代码
3. **回滚支持**: 每个计划都有完整的回滚策略
4. **成功/失败标准**: 明确定义验收标准
5. **超时控制**: 每个步骤都有超时时间

## 测试覆盖

- **81个测试用例**，全部通过
- 覆盖正常场景、边界场景、异常场景、回归场景
- 性能测试：100次策略选择 < 1秒

## 质量门禁

- ✅ Ruff零错误
- ✅ MyPy --strict通过
- ✅ pytest 100%通过
- ✅ Python 3.10+语法
- ✅ 100%类型注解

## 使用示例

```python
from polaris.cells.roles.kernel.internal.debug_strategy import (
    DebugStrategyEngine,
    ErrorContext,
)

engine = DebugStrategyEngine()

context = ErrorContext(
    error_type="timeout",
    error_message="Connection timeout after 30s",
    stack_trace="...",
    file_path="client.py",
    line_number=88,
)

# 选择最佳策略
plan = engine.select_strategy(context)
print(f"Selected strategy: {plan.strategy.value}")
print(f"Estimated time: {plan.estimated_time} minutes")

# 获取详细分类
classification = engine.classify_error(context)
print(f"Category: {classification.category.value}")
print(f"Severity: {classification.severity}")
```
