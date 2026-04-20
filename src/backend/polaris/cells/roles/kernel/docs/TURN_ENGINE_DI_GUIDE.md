# TurnEngine 依赖注入指南

## 概述

TurnEngine 现在采用依赖注入模式，所有外部服务在初始化时注入，避免了运行时的 `NoneType` 错误。

## 改进亮点

### 之前（容易出错）
```python
class TurnEngine:
    def __init__(self, kernel):
        self._kernel = kernel
        # 运行时访问，可能为 None

    def run(self):
        result = self._kernel._get_output_parser().parse(...)  # 可能 AttributeError
```

### 之后（类型安全）
```python
class TurnEngine:
    def __init__(self, kernel, llm_caller=None, output_parser=None, ...):
        # 初始化时固化依赖，永远不会为 None
        self._output_parser = output_parser or kernel._get_output_parser()

    def run(self):
        result = self._output_parser.parse(...)  # 保证存在
```

## 使用方式

### 1. 生产代码（标准方式）

```python
from polaris.cells.roles.kernel.internal.turn_engine import TurnEngine

# 自动从 kernel 获取依赖
engine = TurnEngine(kernel=self)
result = await engine.run(request=request, role="pm")
```

### 2. 测试代码（依赖注入）

```python
from unittest.mock import MagicMock, AsyncMock
from polaris.cells.roles.kernel.internal.turn_engine import TurnEngine

# 创建 mock 服务
mock_llm_caller = MagicMock()
mock_llm_caller.call = AsyncMock(return_value=mock_response)

mock_output_parser = MagicMock()
mock_output_parser.parse_thinking.return_value = MagicMock(
    thinking=None,
    clean_content="Test",
)

mock_prompt_builder = MagicMock()
mock_prompt_builder.build_system_prompt.return_value = "System prompt"
mock_prompt_builder.build_fingerprint.return_value = MagicMock(full_hash="abc123")

# 注入 mock 服务
engine = TurnEngine(
    kernel=kernel,
    llm_caller=mock_llm_caller,
    output_parser=mock_output_parser,
    prompt_builder=mock_prompt_builder,
)

# 运行测试
result = await engine.run(request=request, role="pm")

# 验证 mock 被调用
assert mock_llm_caller.call.called
assert mock_output_parser.parse_thinking.called
```

### 3. 部分依赖注入

```python
# 只注入部分服务，其他从 kernel 获取
engine = TurnEngine(
    kernel=kernel,
    llm_caller=mock_llm_caller,  # 使用 mock
    # output_parser 和 prompt_builder 从 kernel 获取
)
```

## 架构优势

| 优势 | 说明 |
|------|------|
| **编译时安全** | 所有依赖在 `__init__` 后保证存在 |
| **可测试性** | 无需 patch kernel，直接注入 mock |
| **灵活性** | 可以替换单个服务，不影响其他 |
| **清晰依赖** | 从构造函数即可看出依赖关系 |

## 迁移指南

### 测试代码迁移

**旧代码：**
```python
kernel = MagicMock()
kernel._llm_caller.call = AsyncMock(return_value=response)
kernel._output_parser.parse_thinking.return_value = result

engine = TurnEngine(kernel=kernel)
```

**新代码：**
```python
kernel = MagicMock()

mock_caller = MagicMock()
mock_caller.call = AsyncMock(return_value=response)

mock_parser = MagicMock()
mock_parser.parse_thinking.return_value = result

engine = TurnEngine(
    kernel=kernel,
    llm_caller=mock_caller,
    output_parser=mock_parser,
)
```

## API 参考

### TurnEngine.__init__

```python
def __init__(
    self,
    kernel: Any,                    # RoleExecutionKernel 实例
    config: TurnEngineConfig | None = None,
    llm_caller: Any | None = None,   # LLM 调用器
    output_parser: Any | None = None, # 输出解析器
    prompt_builder: Any | None = None, # 提示词构建器
    policy_layer: Any | None = None,  # 策略层
) -> None
```

**参数说明：**
- `kernel`: 提供 workspace、registry 等基础属性
- `config`: TurnEngine 配置，默认从环境变量加载
- `llm_caller`: 如果提供，直接使用；否则从 `kernel._get_llm_caller()` 获取
- `output_parser`: 如果提供，直接使用；否则从 `kernel._get_output_parser()` 获取
- `prompt_builder`: 如果提供，直接使用；否则从 `kernel._get_prompt_builder()` 获取
- `policy_layer`: 如果提供，直接使用；否则延迟初始化

## 最佳实践

1. **生产代码**：使用默认方式，让 TurnEngine 自动从 kernel 获取依赖
2. **单元测试**：注入 mock 服务，隔离被测逻辑
3. **集成测试**：可以使用真实服务或部分 mock
4. **避免**：直接访问 `kernel._xxx` 内部属性

## 测试示例

### 完整示例：测试 TurnEngine 错误处理

```python
@pytest.mark.asyncio
async def test_turn_engine_handles_llm_error():
    """验证 TurnEngine 正确处理 LLM 错误."""
    # Arrange
    kernel = MagicMock()
    kernel.workspace = "."
    kernel.registry = MagicMock()
    kernel.registry.get_profile_or_raise.return_value = MagicMock(
        role_id="pm",
        model="gpt-4",
        version="1.0.0",
        tool_policy=MagicMock(policy_id="pm-policy", whitelist=[]),
    )

    # 注入会报错的 LLM caller
    mock_caller = MagicMock()
    mock_caller.call = AsyncMock(side_effect=TimeoutError("LLM timeout"))

    engine = TurnEngine(
        kernel=kernel,
        llm_caller=mock_caller,
    )

    request = MagicMock()
    request.message = "Test"
    request.workspace = "."
    request.history = []

    # Act
    result = await engine.run(request=request, role="pm")

    # Assert
    assert "timeout" in result.error.lower()
    assert mock_caller.call.called
```
