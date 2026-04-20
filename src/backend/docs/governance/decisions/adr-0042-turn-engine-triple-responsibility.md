---
status: 已实施
context: "CLI polarix/delivery/cli/toad 在 C:/Temp/FileServer 运行时出现两个并发 bug"
decision: "raw_clean → 解析 → clean_content → 输出/存储三路径分离"
consequences: run() 和 run_stream() 各自调用_parse_content_and_thinking_tool_calls 时传正确的 raw 输入
---

# ADR-0042: turn_engine.py 三重职责显式分离

## 上下文

### 触发事件

2026-03-25，CLI `polaris/delivery/cli/toad` 在 `C:\Temp\FileServer` 运行时出现两个并发 bug：

1. **`StreamEventType` 未导入**：每次 CLI 启动即崩溃，`NameError`
2. **`[TOOL_CALL]` 无限循环**：LLM 反复调用 `read_file`，导致上下文管理进入死循环

两个 bug 的根因都指向同一个架构问题：`turn_engine.py` 的 `run()` 和 `run_stream()` 方法承担了三重职责，且没有显式分离。

---

## 问题陈述

### 职责矩阵（修复前）

| 步骤 | 方法 | 职责 | 输入语义 | 输出 |
|------|------|------|----------|------|
| 1 | `_parse_content_and_thinking_tool_calls` | 工具调用解析 | 传入 `clean_content` | 工具调用列表 |
| 2 | `final_content` 赋值 | 用户可见输出 | `clean_content` | 发送给 UI |
| 3 | `append_tool_cycle()` | Session history 存储 | `clean_content` | 写入 transcript |

问题在于：`clean_content` 的语义没有显式定义。它是否包含 `[TOOL_CALL]` wrapper？各步骤对它有什么假设？

**实际状态**：
- `run()` 中，`clean_content` 被传给了 `_parse_content_and_thinking_tool_calls` 作为第一个参数
- 但 `clean_content` 的值来自 `parse_thinking().clean_content`
- `parse_thinking()` 的 `clean_content` 是否剥离了 `[TOOL_CALL]` wrapper，需要查代码才能知道
- 传入 `_parse_content_and_thinking_tool_calls` 时，各调用方假设不一

### 隐式假设链

```
LLM 输出文本
    ↓
parse_thinking(raw_text)
    ↓ .clean_content
clean_content（语义：已剥离 thinking 的文本，但 [TOOL_CALL] wrapper 是否剥离？）
    ↓
    ├─→ _parse_content_and_thinking_tool_calls(clean_content, ...)
    │   → 问题：parser 需要 [TOOL_CALL] wrapper 来提取工具调用
    │   → 如果 clean_content 已剥离，工具调用解析会失败
    │
    ├─→ final_content → 用户可见输出
    │
    └─→ append_tool_cycle(assistant_message=final_content)
        → 问题：如果 wrapper 未剥离，[TOOL_CALL] 文本进入 session history
        → 下一轮 LLM 看到自己输出的 [TOOL_CALL] wrapper → 再次输出 → 无限循环
```

---

## 决策

### 核心原则

```
raw_clean   → 工具调用解析（需要 [TOOL_CALL] wrapper）
    ↓
clean_content = sanitize(raw_clean)  → 用户可见输出 + session history 存储
```

两步必须严格分离：**先解析，后清洗**。顺序不能颠倒。

### 具体改动

#### run()（非流式）

```python
# Before（错误）：
thinking_result = kernel._output_parser.parse_thinking(llm_resp.content or "")
clean_content = str(thinking_result.clean_content or "")
# ...
parsed_tool_calls = kernel._parse_content_and_thinking_tool_calls(
    clean_content,  # ❌ 假设已剥离，但解析需要 wrapper
    ...
)

# After（正确）：
thinking_result = kernel._output_parser.parse_thinking(llm_resp.content or "")
raw_clean = str(thinking_result.clean_content or "")
if not raw_clean.strip() and str(thinking_result.thinking or "").strip():
    raw_clean = str(thinking_result.thinking or "")

# 职责分离：先解析（用 raw_clean），再清洗（得到 clean_content）
allowed_names = list(getattr(profile.tool_policy, "whitelist", []) or [])
clean_content = self._sanitize_assistant_transcript_message(
    raw_clean,
    allowed_tool_names=allowed_names,
)
final_content = clean_content  # 用于输出和存储

parsed_tool_calls = kernel._parse_content_and_thinking_tool_calls(
    raw_clean,  # ✅ 用 raw_clean：parser 需要 [TOOL_CALL] wrapper
    thinking_result.thinking,
    profile,
    native_tool_calls=None,
    native_tool_provider="auto",
)
```

#### run_stream()（流式）

```python
# Before（错误）：
full_text = "".join(full_content)
thinking_result = kernel._output_parser.parse_thinking(full_text)
clean_content = str(thinking_result.clean_content or "")
# ...
parsed_tool_calls = kernel._parse_content_and_thinking_tool_calls(
    clean_content,  # ❌ 同上
    ...
)
# ...
append_tool_cycle(assistant_message=self._sanitize_assistant_transcript_message(
    clean_content,  # ❌ 双重 sanitize，但 clean_content 未 sanitize
    ...
))

# After（正确）：
full_text = "".join(full_content)
thinking_result = kernel._output_parser.parse_thinking(full_text)
raw_clean = str(thinking_result.clean_content or "")
if not raw_clean.strip() and str(thinking_result.thinking or "").strip():
    raw_clean = str(thinking_result.thinking or "")

allowed_names = list(getattr(profile.tool_policy, "whitelist", []) or [])
clean_content = self._sanitize_assistant_transcript_message(
    raw_clean,
    allowed_tool_names=allowed_names,
)
final_content = clean_content

# native_tool_calls 来自 stream 事件，是主要解析路径
# raw_clean 仅作 fallback（当 native_tool_calls 为空时）
parsed_tool_calls = kernel._parse_content_and_thinking_tool_calls(
    raw_clean,
    thinking_result.thinking,
    profile,
    native_tool_calls=native_tool_calls or None,
    native_tool_provider=native_tool_provider,
)
# ...
append_tool_cycle(
    assistant_message=clean_content,  # ✅ 已 sanitize
    tool_results=round_tool_results,
)
```

---

## 后果

### 收益

- **无限循环消除**：`[TOOL_CALL]` wrapper 不再进入 session history
- **工具调用解析可靠**：`raw_clean` 包含完整 wrapper，fallback 路径可用
- **代码意图显式化**：阅读者能清楚区分"解析用"和"输出用"数据

### 残余技术债

1. **Double-sanitize 在 `append_tool_cycle`**：`run()` 中 `append_tool_cycle` 传入了已经 sanitize 过的 `final_content`，又在内部 `_sanitize_assistant_transcript_message` 再处理一次。这是安全带，不影响正确性，但可以优化。
2. **Parser 输入契约不显式**：`_parse_content_and_thinking_tool_calls` 的 `text` 参数语义依赖文档和调用方约定，类型系统无法强制。建议后续引入中间类型（见下方"未来方向"）。

---

## 验证方法

### Bug 1 验证（StreamEventType）

```bash
python -c "from polaris.kernelone.llm.engine.executor import AIExecutor; print('ok')"
# 输出：ok
```

### Bug 2 验证（无限循环）

需要端到端测试：mock stream 返回 `[TOOL_CALL]` 文本但不返回 `native_tool_calls`，验证：
1. 工具调用仍被解析并执行
2. transcript 历史中不包含 `[TOOL_CALL]` wrapper

现有测试覆盖：
- `test_run_stream_stops_after_second_identical_failed_cycle`：通过（验证 tool call 解析）
- `test_kernel_stream_tool_loop.py`：27 个测试全部通过

---

## 未来方向

### 类型级隔离（长期）

```python
# 新类型定义
RawStreamContent = NewType('RawStreamContent', str)   # 含 [TOOL_CALL] wrapper
SanitizedContent = NewType('SanitizedContent', str)  # 剥离后

class LLMOutput:
    raw: RawStreamContent
    sanitized: SanitizedContent
    thinking: str | None

def _parse_tool_calls(
    self,
    raw: RawStreamContent,  # 编译期强制：传对了类型才能编译
    ...
)
```

### Parser 契约显式化

`_parse_content_and_thinking_tool_calls` 的 `text` 参数需要文档化或类型化：

```python
def _parse_content_and_thinking_tool_calls(
    self,
    content: str,  # 文档：应包含 [TOOL_CALL] wrapper 用于文本解析
    ...
)
```

---

## 参考

- Bug 触发：CLI `polaris/delivery/cli/toad` 工具调用循环
- 相关文件：
  - `polaris/cells/roles/kernel/internal/turn_engine.py`
  - `polaris/kernelone/llm/engine/executor.py`
- 相关测试：
  - `polaris/cells/roles/kernel/tests/test_turn_engine_policy_convergence.py`
  - `polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py`
