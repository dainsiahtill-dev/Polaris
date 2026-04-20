# PR-05+PR-06: 旁路组装代码扫描与修复方案

> 生成时间: 2026/04/13
> 来源: M0.1 Context主链测绘

## 1. 概述

### 1.1 问题定义

**旁路组装 (Bypass Assembly)**: 代码直接构建 `messages` 列表，绕过了 `RoleContextGateway.build_context()` 主链路。

**风险**:
- 跳过 Context OS 智能投影
- 跳过 Budget-aware 消息选择
- 跳过注入检测和清洗
- 跳过压缩策略
- 导致上下文不一致

### 1.2 修复原则

旁路代码应该通过 `RoleContextGateway` 获取 context，而非直接构建 messages。

---

## 2. 高风险旁路点 (P0 - 必须修复)

### 2.1 openai_compat_provider.py:445-446

**文件**: `polaris\infrastructure\llm\providers\openai_compat_provider.py`

**当前代码 (行445-446)**:
```python
messages.append({"role": "system", "content": system_prompt})
messages.append({"role": "user", "content": prompt})
```

**位置**: `_invoke()` 方法

**修复方案**:
```python
# 1. 添加导入
from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest

# 2. 在 _invoke() 方法中替换
# BEFORE:
messages = []
system_prompt = config.get("system_prompt")
if system_prompt:
    messages.append({"role": "system", "content": system_prompt})
messages.append({"role": "user", "content": prompt})

# AFTER:
request = ContextRequest(
    message=prompt,
    history=[],
    system_hint=system_prompt,
)
context_result = await self.role_context_gateway.build_context(request)
messages = list(context_result.messages)
```

**风险评估**: 高 - Provider层直接调用LLM，跳过所有上下文策略

**实施优先级**: P0

---

### 2.2 kimi_provider.py:363-364, 439-440

**文件**: `polaris\infrastructure\llm\providers\kimi_provider.py`

**当前代码**:
```python
# _invoke() 行363-364
messages.append({"role": "system", "content": system_prompt})
messages.append({"role": "user", "content": prompt})

# _invoke_streaming() 行439-440
messages.append({"role": "system", "content": system_prompt})
messages.append({"role": "user", "content": prompt})
```

**修复方案**: 同 2.1，需要在两个方法中都应用

**风险评估**: 高 - 同 openai_compat_provider

**实施优先级**: P0

---

### 2.3 director_llm_tools.py:98-102, 131-135

**文件**: `polaris\delivery\cli\director\director_llm_tools.py`

**当前代码 (行98-102)**:
```python
messages.append({"role": "system", "content": full_system})
messages.append({"role": "user", "content": prompt})
```

**当前代码 (行131-135)**:
```python
messages.append({"role": "assistant", "content": content})
tool_results = self.tool_integration.build_tool_results_prompt(result["tools_executed"])
messages.append({"role": "user", "content": tool_results})
```

**修复方案**:
CLI 层需要增加 `RoleContextGateway` 的异步支持。问题在于:
- `_invoke_with_tools()` 是同步方法
- `RoleContextGateway.build_context()` 是 `async` 方法

**修复后代码**:
```python
# 需要将 _invoke_with_tools 改为 async 方法
async def _invoke_with_tools(self, prompt: str, **kwargs):
    # ... 工具调用逻辑 ...

    # 替换消息构建部分
    from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest

    request = ContextRequest(
        message=prompt,
        history=[],  # 从对话历史填充
        system_hint=full_system,
    )
    context_result = await self.role_context_gateway.build_context(request)
    messages = list(context_result.messages)

    # ... 后续工具循环逻辑保持不变，但使用 messages 列表 ...
```

**风险评估**: 高 - CLI层直接构建消息，跳过所有策略

**特殊考虑**: 需要将 `_invoke_with_tools()` 改为 `async` 方法，这可能影响调用方

**实施优先级**: P0

---

### 2.4 chief_engineer_llm_tools.py:75-79, 109-113

**文件**: `polaris\delivery\cli\pm\chief_engineer_llm_tools.py`

**当前代码**: 同 director_llm_tools.py 的模式

**修复方案**: 同 2.3

**风险评估**: 高 - 同 director_llm_tools

**实施优先级**: P0

---

## 3. 中风险旁路点

### 3.1 subagent_runtime.py:231, 274

**文件**: `polaris\kernelone\single_agent\subagent_runtime.py`

**当前代码**:
```python
# 行231 - 添加工具结果到历史
messages.append({"role": "assistant", "content": content_blocks})

# 行274 - 添加最终结果
messages.append({"role": "user", "content": results})
```

**修复方案**:
subagent 的消息构建是在对话循环内部，需要保留消息构建逻辑，但应确保通过 RoleContextGateway 进行初始请求的上下文构建

**风险评估**: 中 - Subagent 通常有独立的上下文策略

**实施优先级**: P1

---

### 3.2 role_integrations.py:336-340, 987-990

**文件**: `polaris\cells\llm\tool_runtime\internal\role_integrations.py`

**当前代码**:
```python
# 行336-340
messages.append({"role": "assistant", "content": content})
messages.append({"role": "user", "content": tool_results})

# 行987-990
messages.append({"role": "assistant", "content": content})
messages.append({"role": "user", "content": tool_results})
```

**修复方案**:
这是工具调用循环中的消息追加，应该在循环开始前通过 Gateway 构建初始上下文，循环内部的消息追加是正常的工具调用模式

**风险评估**: 中 - 工具调用循环中的消息追加可能是合理模式

**实施优先级**: P1

---

### 3.3 openai_responses_adapter.py:133, 181-185

**文件**: `polaris\kernelone\llm\provider_adapters\openai_responses_adapter.py`

**当前代码**:
```python
# 行133
messages.append({"role": "user", "content": item.content or ""})

# 行181-185
messages.append({"role": "assistant", "content": f"<thinking>\n{item.content}\n</thinking>"})
messages.append({"role": "system", "content": f"[Event: {item.event_type}] {item.reason}"])
```

**修复方案**:
这个适配器是将 OpenAI Responses API 格式转换为 messages，需要评估是否应该通过 Gateway

**风险评估**: 中 - 适配器层可能有特殊的转换需求

**实施优先级**: P2

---

### 3.4 context_assembler.py:450-555

**文件**: `polaris\cells\roles\kernel\internal\services\context_assembler.py`

**问题**: `_build_standard_context` 方法直接使用 `PromptChunkAssembler`，而不是通过 `RoleContextGateway`

**修复方案**:
检查 ContextAssembler 和 RoleContextGateway 的关系，确定正确的调用层次

**风险评估**: 中 - ContextAssembler 可能是 Gateway 的下游组件

**实施优先级**: P2

---

## 4. 修复检查清单

- [ ] openai_compat_provider.py - Provider 层改造 (P0)
- [ ] kimi_provider.py - Provider 层改造 (P0)
- [ ] director_llm_tools.py - CLI 层异步改造 (P0)
- [ ] chief_engineer_llm_tools.py - CLI 层异步改造 (P0)
- [ ] subagent_runtime.py - Subagent 上下文改造 (P1)
- [ ] role_integrations.py - 评估工具循环模式 (P1)
- [ ] openai_responses_adapter.py - 评估适配器需求 (P2)
- [ ] context_assembler.py - 评估组件关系 (P2)

---

## 5. 关键依赖

- `RoleContextGateway` 必须是异步方法 (`async build_context()`)
- 调用方需要提供 `RoleProfile`
- 需要处理 `ContextResult` 的 `messages` tuple 转换为 list

---

## 6. 风险

1. **同步转异步的破坏性变更**: CLI 层的 `_invoke_with_tools()` 改为 async 可能影响调用方
2. **测试覆盖**: 需要确保修改后的路径有足够的测试覆盖
3. **性能**: 异步调用 Gateway 可能引入延迟

---

## 7. 建议实施顺序

1. **Phase 1**: Provider 层 (openai_compat_provider, kimi_provider) - 最简单，无异步问题
2. **Phase 2**: CLI 层 (director_llm_tools, chief_engineer_llm_tools) - 需要异步改造
3. **Phase 3**: 评估中风险点 (subagent_runtime, role_integrations, context_assembler)
