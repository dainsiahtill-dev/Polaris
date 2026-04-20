# Tool Calling JSON Text Parsing - Root Cause Fix Blueprint

## 1. 问题概述

### 1.1 现象描述
- **Stream 模式**: LLM 输出工具调用为裸 JSON 文本（如 `{"name": "repo_rg", "arguments": {...}}`），未被识别为可执行工具调用
- **Non-Stream 模式**: 同上，但更严重的是完全没有任何工具调用执行
- **Benchmark 结果**: 20 个测试用例中 17 个失败，主要原因是 `tool_calls: []`（空数组）

### 1.2 根因分析

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ LLM Response Format                                                       │
│ LLM 返回: {"name": "repo_rg", "arguments": {...}}                        │
│ 期望: tool_calls: [{function: {name: "repo_rg", arguments: {...}}}]      │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ StreamThinkingParser (stream_thinking_parser.py)                          │
│ 问题: 只识别 XML 标签格式，不识别 JSON 格式                               │
│ 识别: <tool_call>...</tool_call>                                        │
│ 不识别: {"name": "...", "arguments": {...}}                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ output_parser.parse_execution_tool_calls() (output_parser.py:80-116)      │
│ 问题: 明确不解析文本中的工具调用                                          │
│ 注释: "Native tool calling is the only executable protocol"              │
│ 文本协议: "deprecated for execution"                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 结果                                                                    │
│ tool_calls: [] (空数组)                                                  │
│ output: "{\"name\": \"repo_rg\", ...}" (原始 JSON 文本作为普通内容)       │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2. 解决方案架构

### 2.1 核心策略
采用**分层后备解析架构**：

```
Layer 1: Native Tool Calls (原生格式)
    ↓ (失败时)
Layer 2: XML Tool Calls (XML 标签格式)
    ↓ (失败时)
Layer 3: JSON Tool Calls (JSON 文本格式) [新增]
    ↓ (失败时)
Layer 4: 记录错误并返回空结果
```

### 2.2 组件设计

| 组件 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `JSONToolParser` | 解析 JSON 格式工具调用 | 原始文本 | `list[ParsedToolCall]` |
| `HybridToolParser` | 统一入口，自动选择最佳解析器 | 文本 + 原生调用 | `list[ParsedToolCall]` |
| `StreamThinkingParser` | 流式解析时识别 JSON 格式 | Token 流 | `(kind, text)` 元组 |

## 3. 详细设计

### 3.1 新增组件: JSONToolParser

**文件**: `polaris/kernelone/llm/toolkit/parsers/json_based.py`

```python
class JSONToolParser:
    """JSON 格式工具调用解析器

    支持的格式:
    - {"name": "tool_name", "arguments": {...}}
    - {"name": "tool_name", "args": {...}}
    - {"tool": "tool_name", "arguments": {...}}
    """
    # ... 实现细节见代码
```

### 3.2 修改组件: output_parser.py

**文件**: `polaris/cells/roles/kernel/internal/output_parser.py`

**修改点**:
1. 添加 `JSONToolParser` 导入
2. 在 `parse_execution_tool_calls()` 中添加 JSON 解析后备
3. 添加日志记录解析失败情况

### 3.3 修改组件: stream_thinking_parser.py

**文件**: `polaris/kernelone/llm/providers/stream_thinking_parser.py`

**修改点**:
1. 添加 JSON 工具调用状态检测
2. 修改 `_process_content_state()` 以识别 JSON 格式
3. 修改 `_process_tool_state()` 以处理 JSON 格式

## 4. 实施计划

### Phase 1: 紧急修复 (P0)
| 任务 | 时间 | 负责人 | 验收标准 |
|------|------|--------|----------|
| 创建 JSONToolParser | 1h | - | 单元测试通过 |
| 集成到 output_parser | 30m | - | 解析正常/边界/异常 |
| 修改 StreamThinkingParser | 2h | - | 流式解析 JSON |

### Phase 2: 完善验证 (P1)
| 任务 | 时间 | 负责人 | 验收标准 |
|------|------|--------|----------|
| 添加回归测试 | 1h | - | 覆盖正常/边界/异常 |
| 运行 Benchmark | 1h | - | 通过率 >= 80% |
| 检查 Provider 配置 | 1h | - | 无配置问题 |

### Phase 3: 优化 (P2)
| 任务 | 时间 | 负责人 | 验收标准 |
|------|------|--------|----------|
| 性能优化 | 1h | - | 解析时间 < 10ms |
| 文档完善 | 30m | - | Docstring 完整 |

## 5. 风险评估

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| JSON 格式与正常文本冲突 | 高 | 中 | 使用严格的正则匹配 |
| 解析性能下降 | 中 | 低 | 添加缓存机制 |
| 破坏现有功能 | 高 | 低 | 保留原有解析优先级 |

## 6. 测试策略

### 6.1 单元测试

```python
# test_json_tool_parser.py
def test_parse_simple_json_call():
    """正常: 简单 JSON 工具调用"""

def test_parse_json_with_nested_args():
    """边界: 嵌套参数的 JSON"""

def test_parse_invalid_json_returns_empty():
    """异常: 无效 JSON 返回空列表"""

def test_parse_json_mixed_with_text():
    """边界: JSON 与普通文本混合"""
```

### 6.2 集成测试

```python
# test_output_parser_integration.py
def test_parser_falls_back_to_json():
    """验证原生解析失败时使用 JSON 解析"""

def test_parser_prefers_native_over_json():
    """验证原生解析优先于 JSON 解析"""
```

## 7. 验收标准

- [ ] `JSONToolParser` 单元测试覆盖率 > 90%
- [ ] Benchmark 通过率从 15% 提升到 >= 80%
- [ ] Stream 和 Non-Stream 模式行为一致
- [ ] 无新增回归问题
- [ ] 性能影响 < 10ms

## 8. 回滚计划

如出现问题，可通过以下方式回滚：
1. 设置环境变量 `DISABLE_JSON_TOOL_PARSER=1`
2. 禁用 JSON 解析后备，恢复原有行为

---

**文档版本**: 1.0.0
**创建日期**: 2026-03-28
**最后更新**: 2026-03-28
**状态**: 待实施
