# LLM 工具调用协议指南

**版本**: 1.0
**更新时间**: 2026-03-26
**状态**: 草稿

---

## 1. 概述

本文档描述 Polaris LLM 工具调用系统的协议架构、设计决策和迁移路径。

### 1.1 协议类型

当前系统支持三种协议格式：

| 协议 | 状态 | 用途 |
|------|------|------|
| **Native Function Calling** | 主协议 | OpenAI/Anthropic 原生工具调用格式 |
| **Tool Chain** | 兼容协议 | 向后兼容的 `<tool_chain>` 格式 |
| **Legacy Text Protocol** | **已禁用** | `[TOOL_NAME]` 格式，完全禁用 |

### 1.2 架构分层

```
┌─────────────────────────────────────────────────────────────┐
│  Cell 层: role_integrations.py (6个角色集成)                 │
├─────────────────────────────────────────────────────────────┤
│  KernelOne 平台层: toolkit/* + engine/*                    │
│  ├── definitions.py: 工具定义注册表                          │
│  ├── executor.py: 工具执行器                                │
│  ├── parsers.py: 协议解析器                                │
│  ├── protocol_kernel.py: 统一协议内核                       │
│  ├── streaming_patch_buffer.py: 流式 PATCH 缓冲             │
│  └── tool_normalization.py: 参数归一化                       │
├─────────────────────────────────────────────────────────────┤
│  Provider 适配层: provider_adapters/*                       │
│  ├── base.py: 公共辅助函数 (serialize_transcript_for_prompt) │
│  ├── anthropic_messages_adapter.py: Anthropic 适配器        │
│  └── openai_responses_adapter.py: OpenAI 适配器            │
├─────────────────────────────────────────────────────────────┤
│  外部 Provider: 各种 LLM Provider 实现                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Native Function Calling (主协议)

### 2.1 概述

Native Function Calling 是当前唯一推荐使用的协议格式，由 LLM Provider 原生支持。

### 2.2 支持的 Provider

| Provider | 格式 | 状态 |
|----------|------|------|
| OpenAI | `tool_calls` | ✓ 支持 |
| Anthropic | `content.blocks` + `tool_use` | ✓ 支持 |
| 其他兼容 Provider | 需通过适配器支持 | 待验证 |

### 2.3 响应格式

**OpenAI 格式**:
```json
{
  "choices": [{
    "delta": {
      "content": "...",
      "tool_calls": [{
        "id": "call_123",
        "type": "function",
        "function": {
          "name": "read_file",
          "arguments": "{\"path\": \"src/main.py\"}"
        }
      }]
    }
  }]
}
```

**Anthropic 格式**:
```json
{
  "content": [{
    "type": "text",
    "text": "..."
  }, {
    "type": "tool_use",
    "id": "tool_123",
    "name": "read_file",
    "input": {"path": "src/main.py"}
  }]
}
```

---

## 3. Tool Chain 协议 (兼容协议)

### 3.1 概述

Tool Chain 协议提供向后兼容性，支持 `<tool_chain>` 格式。

### 3.2 格式示例

```xml
<tool_chain>
  <invoke name="read_file">
    <parameter name="path">src/main.py</parameter>
  </invoke>
</tool_chain>
```

### 3.3 实现位置

- 解析器: `polaris/kernelone/llm/toolkit/tool_chain_adapter.py`
- 状态: 仅向后兼容，不推荐新功能使用

---

## 4. Legacy Text Protocol (已禁用)

### 4.1 禁用状态

**2026-03-26**: Legacy Text Protocol 已完全禁用。

### 4.2 历史格式

以下格式已被移除：

- `[TOOL_NAME]...[/TOOL_NAME]`
- `TOOL_CALLS:...`
- 自定义工具调用格式

### 4.3 迁移路径

如果遇到 Legacy 格式响应，系统会：

1. 检测到 `[TOOL_NAME]` 模式
2. 返回协议违规错误
3. 不执行任何工具

---

## 5. Provider 适配器开发指南

### 5.1 适配器职责

Provider 适配器负责：

1. **请求构建**: `build_request()` - 将 ConversationState 转换为 Provider 格式
2. **响应解码**: `decode_response()` - 将 Provider 响应转换为 DecodedProviderOutput
3. **流式事件解码**: `decode_stream_event()` - 解码 SSE 事件
4. **工具结果构建**: `build_tool_result_payload()` - 构建工具结果 payload
5. **Usage 提取**: `extract_usage()` - 提取 token 使用统计

### 5.2 公共辅助函数

位于 `polaris/kernelone/llm/provider_adapters/base.py`:

```python
# 序列化 transcript 为纯文本
from polaris.kernelone.llm.provider_adapters.base import serialize_transcript_for_prompt

# 解析 JSON 参数
from polaris.kernelone.llm.provider_adapters.base import serialize_input_payload
```

### 5.3 新增 Provider 适配器

1. 创建新的适配器类，继承 `ProviderAdapter`
2. 实现所有抽象方法
3. 在 `factory.py` 中注册适配器
4. 更新本文档

---

## 6. 流式处理架构

### 6.1 双路径设计

```
LLM 流式输出
    │
    ▼
┌────────────────────────────────────────────┐
│  Provider 是否支持结构化流式?                │
└────────────────────────────────────────────┘
    │
    ├── 是 ──► _invoke_structured_stream()   │
    │              (SSE 事件直接解码)         │
    │                                       │
    └── 否 ──► _invoke_text_stream()         │
                   (文本流 + 标签解析)         │
```

### 6.2 流式标签解析

支持的思考/回答标签：

```regex
<(think|thinking|thought|answer|output|思考|thoughts|回答|结果)(\s[^>]*)?>
```

### 6.3 StreamingPatchBuffer

用于在流式输出过程中检测、缓冲和执行 PATCH 块：

```python
from polaris.kernelone.llm.toolkit import StreamingPatchBuffer

buffer = StreamingPatchBuffer(workspace=".")
visible_text, blocks = buffer.feed(chunk)
```

---

## 7. 安全考虑

### 7.1 路径安全校验

工具路径参数必须通过安全校验：

```python
from polaris.kernelone.llm.toolkit.tool_normalization import validate_tool_path_argument

safe, msg = validate_tool_path_argument(
    tool_name="read_file",
    path="src/main.py",
    workspace="/path/to/workspace"
)
```

### 7.2 检测的威胁

- 路径遍历攻击 (`../`)
- URL 编码路径遍历 (`%2e%2e%2f`)
- 绝对路径逃逸

### 7.3 命令白名单

`execute_command` 工具使用命令白名单限制可执行命令。

---

## 8. 测试覆盖

### 8.1 测试文件

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_protocol_kernel.py` | ProtocolParser, OperationValidator |
| `tests/test_director_patch_protocol_guard.py` | PATCH 协议守卫 |
| `polaris/delivery/cli/director/tests/test_stream_protocol.py` | 流式协议 |

### 8.2 测试覆盖率目标

- ProtocolParser: > 90%
- StreamingPatchBuffer: > 85%
- Provider Adapters: > 80%

---

## 9. 已知问题

### 9.1 协议解析重复匹配

**问题**: `ProtocolParser.parse()` 可能对同一 PATCH 块返回多个解析结果。

**原因**: `_parse_routed_editing_formats` 和 `_parse_standalone_search_replace` 同时匹配。

**状态**: 已知问题，待优化。

---

## 10. 变更历史

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2026-03-26 | 1.0 | 初始版本，禁用 Legacy Text Protocol |

---

*文档维护者: Polaris Team*
