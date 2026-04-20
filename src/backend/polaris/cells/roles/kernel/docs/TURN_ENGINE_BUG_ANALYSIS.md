# TurnEngine BUG 分析与架构改进方案

## 1. BUG 根本原因

### 直接原因
TurnEngine 直接访问 `kernel._xxx` 内部属性，而这些属性初始值为 `None`，需要通过 `_get_xxx()` 方法惰性初始化。

**问题代码模式：**
```python
# TurnEngine 中的危险代码
thinking_result = kernel._output_parser.parse_thinking(...)  # _output_parser 可能为 None
llm_resp = await kernel._llm_caller.call(...)               # _llm_caller 可能为 None
```

**应该使用：**
```python
thinking_result = kernel._get_output_parser().parse_thinking(...)
llm_resp = await kernel._get_llm_caller().call(...)
```

### 为什么测试没有发现？

**测试中的 Mock 方式：**
```python
# test_service_integration.py
kernel = MagicMock()  # 创建纯Mock，不是真实的RoleExecutionKernel
kernel._llm_caller = MagicMock()  # 直接赋值，永远不会是None
kernel._output_parser = MagicMock()  # 直接赋值，永远不会是None
```

**真实场景：**
```python
# 生产代码
kernel = RoleExecutionKernel(workspace=".")  # _llm_caller = None (初始值)
# 如果没有调用 _get_llm_caller()，直接访问 _llm_caller 就是 None
```

**测试盲点：**
| 测试类型 | 覆盖情况 | 问题 |
|---------|---------|------|
| `test_turn_engine.py` | 只测试配置和工具类 | 不测试 `run()` 和 `run_stream()` |
| `test_service_integration.py` | 使用 MagicMock 替代真实 Kernel | Mock 直接赋值属性，不触发 None 问题 |
| 真实集成测试 | 缺失 | 没有使用真实 Kernel 实例的测试 |

## 2. 立即修复方案

### 修复 1: TurnEngine 使用正确的访问方法

**文件:** `polaris/cells/roles/kernel/internal/turn_engine/engine.py`

```python
# Line 281: _materialize_assistant_turn
thinking_result = kernel._get_output_parser().parse_thinking(...)

# Line 311: _parse_tool_calls_from_turn
return kernel._get_output_parser().parse_tool_calls(...)

# Line 472, 481: _get_prompt_builder 替代 _prompt_builder
system_prompt = kernel._get_prompt_builder().build_system_prompt(...)
fingerprint = kernel._get_prompt_builder().build_fingerprint(...)

# Line 854, 863: run_stream 中的相同问题
```

### 修复 2: 添加防御性检查

在访问任何 kernel 内部属性前检查：

```python
def _get_kernel_attr(kernel, attr_name, getter_name):
    """安全获取 kernel 属性，如果为 None 使用 getter 初始化"""
    attr = getattr(kernel, attr_name, None)
    if attr is not None:
        return attr
    getter = getattr(kernel, getter_name, None)
    if getter is None:
        raise AttributeError(f"Kernel missing {attr_name} and {getter_name}")
    return getter()
```

## 3. 架构设计改进（杜绝类似BUG）

### 方案 A: 依赖注入模式（推荐）

**核心思想:** TurnEngine 不直接访问 kernel 内部属性，而是通过构造函数接收所需服务。

**改造后代码：**

```python
class TurnEngine:
    def __init__(
        self,
        kernel: Any,
        llm_caller: Optional[LLMCaller] = None,
        output_parser: Optional[OutputParser] = None,
        prompt_builder: Optional[PromptBuilder] = None,
    ):
        self._kernel = kernel
        # 如果外部注入则使用，否则从 kernel 获取
        self._llm_caller = llm_caller or kernel._get_llm_caller()
        self._output_parser = output_parser or kernel._get_output_parser()
        self._prompt_builder = prompt_builder or kernel._get_prompt_builder()
```

**优点：**
- 依赖明确，易于测试
- 编译时即可发现缺失依赖
- 支持 mock 注入，无需 patch kernel

### 方案 B: Facade 接口模式

**核心思想:** Kernel 实现一个 Facade 接口，TurnEngine 只通过接口访问。

```python
from typing import Protocol

class KernelFacade(Protocol):
    """Kernel Facade 接口 - TurnEngine 只依赖此接口"""

    def get_llm_caller(self) -> LLMCaller: ...
    def get_output_parser(self) -> OutputParser: ...
    def get_prompt_builder(self) -> PromptBuilder: ...

class RoleExecutionKernel:
    """实现 Facade 接口"""
    def get_llm_caller(self) -> LLMCaller:
        return self._get_llm_caller()

    def get_output_parser(self) -> OutputParser:
        return self._get_output_parser()
```

**优点：**
- 解耦 TurnEngine 和 Kernel 内部实现
- 可以创建 FakeKernel 用于测试

### 方案 C: 初始化时固化依赖

**核心思想:** Kernel 在初始化时创建所有依赖，不再惰性初始化。

```python
class RoleExecutionKernel:
    def __init__(self, ...):
        # 立即初始化，不是 None
        self._prompt_builder = PromptBuilder(self.workspace)
        self._output_parser = OutputParser()
        self._quality_checker = QualityChecker(self.workspace)
        self._llm_caller = LLMCaller(self.workspace)
        self._event_emitter = KernelEventEmitter()
```

**优点：**
- 简单直接，无 None 风险
- 启动时即可发现配置问题

**缺点：**
- 增加启动时间
- 无法延迟加载不用的组件

## 4. 测试改进方案

### 测试类型 1: Mock 测试（现有）
- 用于快速验证逻辑分支
- 但需要正确使用 Mock

### 测试类型 2: 真实 Kernel 集成测试（缺失）
```python
@pytest.mark.asyncio
async def test_turn_engine_with_real_kernel():
    """使用真实 Kernel 实例测试，不是 MagicMock"""
    kernel = RoleExecutionKernel.create_default(workspace=".")

    # 这会测试真实的属性初始化
    engine = TurnEngine(kernel=kernel)

    # 应该失败，因为 _llm_caller 初始为 None
    # 直到调用 _get_llm_caller()
```

### 测试类型 3: 契约测试
```python
def test_kernel_provides_required_services():
    """验证 Kernel 提供 TurnEngine 需要的所有服务"""
    kernel = RoleExecutionKernel.create_default(workspace=".")

    assert kernel._get_llm_caller() is not None
    assert kernel._get_output_parser() is not None
    assert kernel._get_prompt_builder() is not None
```

## 5. 实施建议

### 立即执行（修复现有BUG）
1. 修复所有 `kernel._xxx` 直接访问为 `kernel._get_xxx()`
2. 添加单元测试验证修复

### 短期（架构改进）
1. 实施方案 A（依赖注入）或方案 C（初始化时固化）
2. 添加真实 Kernel 集成测试

### 长期（测试体系）
1. 建立测试金字塔
   - 大量单元测试（Mock）
   - 适量集成测试（真实服务）
   - 少量端到端测试
2. 添加静态检查规则，禁止直接访问 `_` 前缀属性

## 6. 检查清单

- [ ] 修复 `kernel._output_parser` → `kernel._get_output_parser()`
- [ ] 修复 `kernel._prompt_builder` → `kernel._get_prompt_builder()`
- [ ] 修复 `kernel._llm_caller` → `kernel._get_llm_caller()`
- [ ] 添加使用真实 Kernel 的集成测试
- [ ] 实施架构改进方案（A 或 C）
