# 工具执行智能建议模块蓝图

**日期**: 2026-04-10
**状态**: Phase 1 已完成（框架 + 2 个 Builder）
**目标**: 为 LLM 工具调用错误提供结构化的智能建议，防止 LLM 反复重试同样的错误

---

## 1. 背景与问题

### 1.1 问题描述

当 LLM 使用工具（如 `search_replace`、`edit_file`）失败时，工具返回的错误信息过于简略，导致 LLM 无法理解失败原因并修正下一次尝试。

**案例**:
```json
{
  "error": "No matches found"
}
```

LLM 完全不知道搜索字符串哪里不对、实际文件内容是什么、应该怎么修正。

### 1.2 当前修复（Phase 1）

| 文件 | 改动 |
|------|------|
| `polaris/kernelone/tool_execution/suggestions/__init__.py` | 模块入口，公共 API 导出 |
| `polaris/kernelone/tool_execution/suggestions/protocols.py` | `SuggestionBuilder` 协议定义 |
| `polaris/kernelone/tool_execution/suggestions/registry.py` | Builder 注册表 + `build_suggestion()` 工厂函数 |
| `polaris/kernelone/tool_execution/suggestions/fuzzy.py` | `FuzzyMatchBuilder` — 搜索未命中，提供最相似行 + diff |
| `polaris/kernelone/tool_execution/suggestions/exploration.py` | `ExplorationBuilder` — 文件不存在，提供文件名相似度建议 |
| `polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py` | 移除内联逻辑，改用 `_build_no_match_suggestion()` |
| `polaris/cells/roles/kernel/internal/tool_gateway.py` | append `suggestion` 字段到 `error_message` |
| `polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py` | 修复 `ok: True` 但 `replacements == 0` 的语义错误 |

---

## 2. 架构设计

### 2.1 核心组件

```
polaris/kernelone/tool_execution/suggestions/
    __init__.py           # 公共导出: build_suggestion, register_builder, Builder 类
    protocols.py          # SuggestionBuilder 协议
    registry.py           # 注册表 + 工厂函数
    fuzzy.py              # FuzzyMatchBuilder
    exploration.py         # ExplorationBuilder
    context.py             # (待实现) ContextBuilder — 文件过大建议
    validation.py          # (待实现) ValidationBuilder — 参数校验建议
    command.py             # (待实现) CommandBuilder — 命令执行错误建议
```

### 2.2 SuggestionBuilder 协议

```python
class SuggestionBuilder(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def priority(self) -> int: ...   # 数字越小越先检查

    def should_apply(self, error_result: dict[str, Any]) -> bool: ...

    def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None: ...
```

### 2.3 工厂函数

```python
def build_suggestion(
    error_result: dict[str, Any],
    **kwargs: Any,
) -> str | None:
    """按 priority 顺序遍历所有 Builder，返回第一个产生的建议。"""
```

### 2.4 数据流

```
ToolExecutor 执行工具
    │
    ├── 成功 → 返回 result dict（含可选 suggestion 字段）
    │
    └── 失败 → error_result dict（含 error、suggestion 字段）
                    │
                    ▼
            tool_gateway.normalize_result()
                    │
                    ├── error_message = result.error + " | " + suggestion
                    │
                    └── _emit_tool_event_to_journal(...)
                        │
                        ▼
                _schedule_uep_event(...)
                        │
                        ▼
                logs/journal.*.jsonl
```

---

## 3. 已实现的 Builder

### 3.1 FuzzyMatchBuilder (priority=10)

**适用错误**: `"No matches found"`

**策略**:
1. 用 `difflib.SequenceMatcher` 找到文件中与搜索字符串最相似的行
2. 计算相似度百分比
3. 生成 unified diff 显示差异
4. 建议 LLM 先用 `read_file()` 验证内容

**示例输出**:
```
Search='def fo():' not found. Most similar line 1: 'def foo():' (similarity=95%).
-def fo():; +def foo(): Use read_file() to verify exact content before editing.
```

### 3.2 ExplorationBuilder (priority=20)

**适用错误**: `"not found"`, `"does not exist"`, `"no such file"`

**策略**:
1. 用 `difflib.SequenceMatcher` 找到最接近的文件名
2. 如果 workspace 文件列表 ≤ 30 个，全部列出
3. 建议使用 `repo_tree()` 或 `glob()` 探索

**示例输出**:
```
File not found: foo.py. Did you mean: 'foo_bar.py'? Available files: [bar.py, baz.py] Use repo_tree() to explore.
```

---

## 4. 待实现的 Builder（Roadmap）

| Builder | 优先级 | 适用错误类型 | 策略 |
|---------|--------|-------------|------|
| `ContextBuilder` | 30 | 文件过大、行数超限 | 提供分片读取建议 |
| `ValidationBuilder` | 40 | 参数类型错误、缺少必填参数 | 给出参数规范 + 示例 |
| `CommandBuilder` | 50 | 危险命令、超时、退出码非零 | 说明原因 + 替代方案 |
| `RegexBuilder` | 15 | regex 语法错误 | 指出具体语法错误位置 |
| `ScopeBuilder` | 25 | scope 越界 | 列出当前 scope 允许的操作 |

---

## 5. 使用规范

### 5.1 工具 Handler 的责任

工具 Handler（如 `filesystem.py` 中的 handlers）在返回错误时：

```python
if replacements == 0:
    from polaris.kernelone.tool_execution.suggestions.fuzzy import (
        _build_no_match_suggestion,
    )
    suggestion = _build_no_match_suggestion(content, search_text)
    return {
        "ok": False,
        "file": file,
        "error": "No matches found",
        "suggestion": suggestion,  # 关键：提供 suggestion
    }
```

### 5.2 Gateway 的责任

Gateway 在标准化错误时，**必须** append `suggestion` 到 `error_message`：

```python
if not normalized_success:
    error_message = str(result.get("error") or "").strip()
    suggestion = result.get("suggestion")
    if suggestion and str(suggestion).strip():
        error_message = f"{error_message} | {suggestion}"
```

### 5.3 新增 Builder 的流程

1. 在 `suggestions/` 下创建 `xxx.py`
2. 实现 `SuggestionBuilder` 协议
3. 在 `registry.py` 的 `_register_default_builders()` 中注册
4. 在 `__init__.py` 中导出

---

## 6. 测试策略

```python
# tests/test_suggestions.py
def test_fuzzy_match_builder_exact_line():
    content = "def foo():\n    return 1\n"
    result = build_suggestion({"error": "No matches found", "content": content, "search": "def foo()"})
    assert result is None or "similarity=100%" in result  # 完全匹配不需要建议

def test_fuzzy_match_builder_partial_match():
    content = "def foo():\n    return 1\n"
    result = build_suggestion({"error": "No matches found", "content": content, "search": "def fo():"})
    assert result is not None
    assert "similarity=" in result
    assert "def foo():" in result

def test_exploration_builder_file_not_found():
    result = build_suggestion(
        {"error": "File not found: foo.py", "file": "foo.py"},
        workspace_files=["bar.py", "foo_bar.py"],
    )
    assert result is not None
    assert "foo_bar.py" in result
```

---

## 7. 风险与边界

1. **性能**: `difflib.SequenceMatcher` 对大文件（>10万行）有 O(n²) 复杂度。需要对大文件做采样或行数限制。
2. **空结果**: 当 `content` 为空时，Builder 返回简短建议，不报错。
3. **无相似**: 当 `best_ratio < 0.3` 时，不强行给建议，只给出通用探索建议。
4. **循环依赖**: Registry 延迟导入 Builder，避免启动时循环依赖。
