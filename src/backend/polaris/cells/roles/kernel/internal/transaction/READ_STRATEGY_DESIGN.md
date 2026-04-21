# Read Strategy 自动切换机制设计说明

## 概述

实现了 Read Strategy 自动切换机制，当 `read_file` 遇到大文件或截断内容时，自动切换到 `repo_read_slice` 分段读取模式。

## 核心组件

### 1. `read_strategy.py` - 策略决策引擎

**位置**: `polaris/cells/roles/kernel/internal/transaction/read_strategy.py`

**主要功能**:
- `_should_use_slice_mode()`: 基于文件大小/行数判断是否应使用分段读取
- `is_content_truncated()`: 检测内容是否被截断（支持多种启发式规则）
- `calculate_slice_ranges()`: 计算最优分段范围
- `determine_optimal_strategy()`: 综合决策入口

**阈值配置**:
- 默认大小阈值: 100KB
- 默认行数阈值: 1000行
- 默认分段大小: 200行

**截断检测规则**:
1. 检查 `result_metadata` 中的 `truncated` 标记
2. 检查内容是否以 `"..."`、`"[truncated]"`、`"[截断]"` 结尾
3. 检查内容最后200字符是否包含截断警告
4. 检查声明行数与实际行数是否匹配

### 2. `stream_orchestrator.py` - 集成层

**位置**: `polaris/cells/roles/kernel/internal/transaction/stream_orchestrator.py`

**新增组件**:
- `_should_use_slice_mode()`: 便捷函数，包装策略决策
- `_detect_truncation_heuristics()`: 截断检测便捷函数
- `ReadStrategyAdapter`: 适配器类，分析工具结果并决策

**向后兼容**:
- 现有调用方式不变
- 内部自动决策，不修改工具调用接口
- 优化在 tool result 返回后进行处理

## 使用方式

### 基本使用

```python
from polaris.cells.roles.kernel.internal.transaction.read_strategy import determine_optimal_strategy

# 分析工具结果
strategy = determine_optimal_strategy(
    file_path="large_file.py",
    content=tool_result.get("content"),
    result_metadata=tool_result,
)

if strategy.use_slice_mode:
    # 切换到 repo_read_slice 分段读取
    pass
```

### 在 StreamOrchestrator 中使用

```python
adapter = ReadStrategyAdapter()

# 分析 read_file 结果
strategy = adapter.analyze_tool_result("read_file", tool_result)
if strategy and strategy.use_slice_mode:
    # 构建分段读取替换调用
    replacements = adapter.build_slice_replacements(
        file_path=tool_result["file"],
        total_lines=tool_result.get("line_count", 0),
    )
```

## 测试覆盖

### 单元测试

**read_strategy 测试** (`test_read_strategy.py`):
- 47 个测试用例
- 覆盖文件大小判断、截断检测、分段计算、策略决策
- 边界情况：空文件、阈值边界、Unicode 内容

**stream_orchestrator 测试** (`test_stream_orchestrator.py`):
- 26 个测试用例（新增 15 个）
- 覆盖 ReadStrategyAdapter、截断检测、策略切换

### 运行测试

```bash
# 运行 read_strategy 测试
python -m pytest polaris/cells/roles/kernel/internal/transaction/tests/test_read_strategy.py -v

# 运行 stream_orchestrator 测试
python -m pytest polaris/cells/roles/kernel/internal/transaction/tests/test_stream_orchestrator.py -v

# 运行所有 transaction 测试
python -m pytest polaris/cells/roles/kernel/internal/transaction/tests/ -v
```

## 质量门禁

- **Ruff**: 代码规范检查通过
- **MyPy**: 类型检查通过
- **Pytest**: 261 个测试全部通过

## 设计决策

1. **保持向后兼容**: 不改变现有工具调用接口，内部自动决策
2. **分层架构**: 策略决策与集成层分离，便于测试和维护
3. **可配置阈值**: 支持自定义大小/行数阈值
4. **多种检测方式**: 支持元数据标记、内容启发式、行数匹配等多种截断检测
5. **并发优化**: 分段读取可并行执行，提高大文件读取效率

## 后续优化方向

1. 集成到 `ToolBatchExecutor` 中自动触发分段读取
2. 添加缓存机制避免重复读取
3. 支持基于文件类型的自定义阈值
4. 添加性能监控指标
