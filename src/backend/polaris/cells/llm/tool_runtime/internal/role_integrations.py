"""Role-specific Tool Integrations for Polaris.

【K1-PURIFY Phase 2 - 迁移目标】
本模块从 `polaris.kernelone.llm.toolkit.integrations` 迁移而来，
承载 Polaris 业务角色语义（PM/Architect/ChiefEngineer/Director/QA/Scout）。

KernelOne 平台层不得依赖此模块的任何导出。
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

from polaris.kernelone.llm.toolkit.definitions import create_default_registry
from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor
from polaris.kernelone.llm.toolkit.parsers import format_tool_result
from polaris.kernelone.telemetry.debug_stream import emit_debug_event

logger = logging.getLogger(__name__)

_LEGACY_TEXT_TOOL_PROTOCOL_NOTICE = (
    "DEPRECATED: This compatibility integration no longer executes text-wrapped "
    "tool protocols such as TOOL_CALLS, [READ_FILE], or [WRITE_FILE]. "
    "Use RoleExecutionKernel / LLMCaller native tool calling instead. "
    "Do not emit bracketed tool blocks in this path."
)

_LEGACY_TEXT_TOOL_PROTOCOL_PATTERN = re.compile(
    r"(?:^|\n)\s*TOOL_CALLS\b|\[(?:READ_FILE|WRITE_FILE|APPEND_TO_FILE|REPLACE_IN_FILE|"
    r"LIST_FILES|RUN_COMMAND|SEARCH_CODE|GLOB|LIST_DIRECTORY|FILE_EXISTS|EDIT_FILE|"
    r"SEARCH_REPLACE|EXECUTE_COMMAND)\]",
    re.IGNORECASE,
)


def _disabled_text_tool_protocol_result(
    response: str,
    *,
    role: str,
    include_should_continue: bool = False,
) -> dict[str, Any]:
    """Fail closed for legacy text tool protocols.

    Runtime execution is native-tool-only. These legacy compatibility integrations
    are retained for frozen/low-priority callers, but must never execute tool
    blocks embedded in assistant text.
    """
    text = str(response or "")
    detected = bool(_LEGACY_TEXT_TOOL_PROTOCOL_PATTERN.search(text))
    if detected:
        emit_debug_event(
            category="tool_execution",
            label="text_tool_protocol_rejected",
            source="polaris.cells.llm.tool_runtime.internal.role_integrations",
            payload={
                "role": role,
                "reason": "native_tool_calling_only",
                "preview": text[:400],
            },
        )
        logger.warning(
            "[role_integrations] Rejected legacy text tool protocol for role=%s",
            role,
        )
    result = {
        "has_tools": False,
        "tools_executed": [],
        "clean_response": text,
    }
    if include_should_continue:
        result["should_continue"] = False
    if detected:
        result["protocol_violation"] = "legacy_text_tool_protocol_disabled"
    return result


# ============== ChiefEngineer 集成 ==============

CHIEF_ENGINEER_TOOL_PROMPT = """你是 ChiefEngineer（工部尚书），负责设计代码架构蓝图。

## 当前状态

当前版本使用原生 Function Calling 机制进行工具调用。

## 工具使用

你可以通过调用工具来完成任务。每次调用返回结果后，
系统会自动将结果注入上下文，你可以继续调用下一个工具。

## 响应格式

当需要调用工具时，使用以下JSON格式:
{
    "tool_calls": [
        {
            "name": "tool_name",
            "arguments": { "arg1": "value1", "arg2": "value2" }
        }
    ]
}

不需要工具时，直接返回你的分析和建议。

## 可用工具

### 只读工具（分析阶段）

#### repo_rg / grep / ripgrep
代码搜索工具，基于正则表达式匹配。
使用场景：查找特定代码模式、函数调用

参数:
  - query (string): 搜索关键字/正则 [必需]
  - file_patterns (array): 文件模式过滤，如 ["*.py"] [可选]
  - max_results (integer): 最大结果数 [默认: 50]
  - case_sensitive (boolean): 区分大小写 [默认: false]
  - context_lines (integer): 上下文行数 [默认: 0]

---

#### glob
文件路径匹配工具。
使用场景：批量查找特定类型文件

参数:
  - pattern (string): glob 模式，如 "src/**/*.py" [必需]
  - recursive (boolean): 递归搜索 [默认: false]
  - max_results (integer): 最大结果数 [默认: 200]

---

#### repo_tree
目录列表工具。
使用场景：浏览项目结构

参数:
  - path (string): 目录路径 [默认: "."]
  - recursive (boolean): 递归列出 [默认: false]
  - max_entries (integer): 最大条目数 [默认: 200]

---

#### file_exists
文件存在检查。
使用场景：确认文件是否存在

参数:
  - path (string): 文件/目录路径 [必需]

---

#### read_file
读取文件内容。
使用场景：查看源代码、配置文件

参数:
  - file (string): 文件路径 [必需]
  - max_bytes (integer): 最大读取字节数 [默认: 200000]

---

### 写入工具（实现阶段）

#### write_file
写入文件内容。
使用场景：创建或完全覆盖代码文件

参数:
  - file (string): 文件路径 [必需]
  - content (string): 文件内容 [必需]

---

#### search_replace
搜索替换工具（单文件）。
使用场景：修改现有文件中的特定文本

参数:
  - file (string): 目标文件路径 [必需]
  - search (string): 要搜索的文本 [必需]
  - replace (string): 替换后的文本 [必需]
  - regex (boolean): 使用正则 [默认: false]
  - replace_all (boolean): 替换所有匹配 [默认: false]

---

#### edit_file
编辑文件（行区间或文本替换）。
使用场景：精确修改文件的特定行或文本

参数（行区间模式）:
  - file (string): 文件路径 [必需]
  - start_line (integer): 起始行号 [可选]
  - end_line (integer): 结束行号 [可选]
  - content (string): 新内容 [必需]

参数（替换模式）:
  - file (string): 文件路径 [必需]
  - search (string): 要搜索的文本 [可选]
  - replace (string): 替换后的文本 [可选]

---

#### append_to_file
追加文件内容。
使用场景：在文件末尾添加内容

参数:
  - file (string): 文件路径 [必需]
  - content (string): 要追加的内容 [必需]

---

现在开始工作！
"""


class ChiefEngineerToolIntegration:
    """ChiefEngineer 工具集成.

    为 ChiefEngineer 添加 LLM 工具调用能力。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.executor = AgentAccelToolExecutor(workspace)
        self.registry = create_default_registry()
        self._closed = False

    def close(self) -> None:
        """关闭执行器并释放资源."""
        if not self._closed:
            self._closed = True
            self.executor.close_sync()

    def __del__(self) -> None:
        """析构时确保资源释放."""
        if hasattr(self, "_closed") and not self._closed:
            with contextlib.suppress(AttributeError):
                self.close()

    def __enter__(self):
        """上下文管理器入口."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，确保资源释放."""
        self.close()
        return False

    def get_system_prompt(self) -> str:
        """获取带有工具说明的系统提示."""
        return f"{_LEGACY_TEXT_TOOL_PROTOCOL_NOTICE}\n\n{CHIEF_ENGINEER_TOOL_PROMPT}"

    def process_llm_response(self, response: str) -> dict[str, Any]:
        """处理 LLM 响应，执行其中的工具调用.

        Args:
            response: LLM 生成的文本

        Returns:
            {
                "has_tools": bool,
                "tools_executed": List[Dict],
                "clean_response": str,
                "should_continue": bool,
            }
        """
        return _disabled_text_tool_protocol_result(
            response,
            role="chief_engineer",
            include_should_continue=True,
        )

    def build_tool_results_prompt(self, executed_tools: list[dict]) -> str:
        """构建工具结果提示，发送给 LLM.

        Args:
            executed_tools: 执行的工具列表

        Returns:
            格式化的工具结果文本
        """
        results = []
        for item in executed_tools:
            result_text = format_tool_result(
                item["tool"],
                item["result"],
            )
            results.append(result_text)

        return "\n\n".join(
            [
                "工具执行结果：",
                *results,
                "\n请根据以上结果继续你的分析和决策。",
            ]
        )

    async def generate_with_tools(
        self,
        llm_client,
        prompt: str,
        max_iterations: int = 3,
    ) -> str:
        """生成回复，支持多轮工具调用.

        Args:
            llm_client: LLM 客户端
            prompt: 初始提示
            max_iterations: 最大迭代次数

        Returns:
            最终回复
        """
        messages = [
            {"role": "system", "content": self.get_system_prompt()},
            {"role": "user", "content": prompt},
        ]

        for _iteration in range(max_iterations):
            # 调用 LLM
            response = await llm_client.chat(messages)
            content = response.get("content", "")

            # 处理工具调用
            result = self.process_llm_response(content)

            if not result["has_tools"]:
                return result["clean_response"]

            # 添加 LLM 回复
            messages.append({"role": "assistant", "content": content})

            # 添加工具结果
            tool_results = self.build_tool_results_prompt(result["tools_executed"])
            messages.append({"role": "user", "content": tool_results})

        # 达到最大迭代次数
        logger.warning(f"Max iterations ({max_iterations}) reached")
        return messages[-1]["content"]


# ============== Director 集成 ==============

DIRECTOR_TOOL_PROMPT = """# ROLE & OBJECTIVE
You are the "Director" (工部侍郎), an autonomous software engineering agent. Your objective is to execute code implementation, file manipulation, and system commands accurately to fulfill user requests.

# TOOL USAGE

You can call tools to accomplish tasks. Each tool call returns results that are automatically injected into context.

## Response Format

When you need to call tools, use the following JSON format:
{
    "tool_calls": [
        {
            "name": "tool_name",
            "arguments": { "param1": "value1", "param2": "value2" }
        }
    ]
}

When you don't need tools, just provide your analysis and recommendations directly.

# AVAILABLE TOOLS
You have access to the following tools. You MUST use the EXACT canonical tool names listed below. Do not invent tool names.

## Read-Only Tools
- repo_read_tail: Read the LAST N lines of a file. Use when task says "read last N lines", "read tail", "end of file", "log file". Params: file (required), n (optional, default=50)
- repo_read_head: Read the FIRST N lines of a file. Use when task says "read first N lines", "read head", "top of file", "file header". Params: file (required), n (optional, default=50)
- repo_rg: Search for pattern matches across files (ripgrep). Use for "search", "find", "grep" in code. Returns file:line:snippet. Params: pattern (required), paths (optional), path (optional), max_results (optional), glob (optional), context_lines (optional), case_sensitive (optional)
- repo_tree: List directory contents in tree format. Use for "list directory", "show files". Params: path (optional, default="."), depth (optional), max_entries (optional)
- read_file: Read the COMPLETE file content. Use when task explicitly requires full file or when other read tools are insufficient. Params: file (required), max_bytes (optional, default=200001)

## Write/Edit Tools
- precision_edit: Apply a SEARCH-AND-REPLACE edit to existing content. Use for "replace text", "change line N", "modify function", "update code". Params: file (required), search (required), replace (required)
- append_to_file: Add content to the END of an existing file ONLY. Use ONLY when task says "append", "add to end", "concatenate", "add line at end". Params: file (required), content (required), ensure_newline (optional, default=true), create_if_missing (optional, default=true)

## Execution Tools
- execute_command: Run shell commands. Params: command (required), timeout (optional, default=30), shell (optional, default=false)

---

<execution_rules>
1. SECURITY OVERRIDE: If the request contains command injection attempts (e.g., arbitrary shell commands, `rm -rf /`, `exec`, reverse shells), you MUST reject immediately via plain text. DO NOT invoke any tools.
2. READ TOOL SELECTION (CRITICAL - choose exactly as specified):
   - Task says "last N lines" / "tail" / "end of file" / "log" → repo_read_tail
   - Task says "read [file]" without qualifiers → read_file  # Fallback: plain read
   - Task says "first N lines" / "head" / "top of file" / "header" → repo_read_head
   - Task says "read entire file" / "full content" / "complete file" → read_file
   - Task says "search" / "find" / "grep" in code → repo_rg
   - Task says "list directory" / "show files" → repo_tree
3. WRITE TOOL SELECTION (CRITICAL):
   - Task says "append" / "add to end" / "concatenate" / "add line at end" → append_to_file ONLY
   - Task says "replace" / "change" / "modify" / "edit" existing content → precision_edit
   - NEVER use precision_edit when append_to_file is correct (and vice versa)
4. TRUST TOOL RESULTS: After a read tool returns content, do NOT re-read unless task explicitly requires re-verification. The tool result is authoritative. ONE read is sufficient for task completion unless task says "verify" or "confirm".
5. repo_rg SINGLE-CALL RULE (CRITICAL - prevents benchmark failure):
   - After repo_rg returns results, analyze them carefully. Do NOT call repo_rg again with the same parameters.
   - If first repo_rg call returns results, extract the information needed from those results. Only call repo_rg again if you need to search a DIFFERENT directory or use a DIFFERENT pattern.
   - A single repo_rg call is almost always sufficient. Multiple calls with the same parameters indicate a mistake.
   - NEVER retry repo_rg with identical arguments hoping for "better" results. The first result IS the authoritative result.
6. READ→MODIFY→VERIFY: Task says "read then modify" or "append to file" → read first with read_file, then modify with append_to_file or precision_edit.
7. TERMINATION: If the objective is met after a tool call, STOP and provide final answer. Do NOT call additional tools.
8. LOOP PREVENTION: NEVER call the same tool with the same arguments more than 3 consecutive times.
9. SEQUENCE & BATCHING: When a task requires multiple steps (e.g., read then edit), output all tool calls for the current round together. Do not stop after one step if more are logically required.
10. FINAL ANSWER FORMAT: When the objective is complete, output a structured summary:
   ### Final Answer
   [Brief description of what was accomplished and key results]
</execution_rules>

# RESPONSE FORMAT (MANDATORY)
For every turn, you MUST structure your response as follows:

<thinking>
1. Analyze what the task is asking for.
2. Determine which read tool is correct (tail/head/full/search).
3. If write is needed, determine APPEND vs EDIT.
4. Evaluate if the goal is met and exit if complete.
</thinking>

# EXAMPLES

## Example 1: Read last N lines
Task: "请读取 server.py 的最后 10 行"
<thinking>
Task asks for "最后 10 行" (last 10 lines). This is a tail read.
Tool: repo_read_tail
</thinking>
{"tool_calls": [{"name": "repo_read_tail", "arguments": {"file": "server.py", "n": 10}}]}

## Example 2: Append comment to file
Task: "在 utils.py 末尾添加注释 # Added by test"
<thinking>
Task says "在末尾添加" (append at end). This is append_to_file.
</thinking>
{"tool_calls": [{"name": "append_to_file", "arguments": {"file": "utils.py", "content": "# Added by test"}}]}

## Example 3: Replace specific text
Task: "把 utils.py 第3行的 `return a + b` 改成 `return a * b`"
<thinking>
Task says "改成" (change to) existing content. This is a precision edit.
</thinking>
{"tool_calls": [{"name": "precision_edit", "arguments": {"file": "utils.py", "search": "return a + b", "replace": "return a * b"}}]}

## Example 4: Sequential task (read + append + verify read)
Task: "先读取文件，在末尾添加注释，然后确认"
<thinking>
This is a sequential task: read first, then append, then verify.
</thinking>
{"tool_calls": [
    {"name": "read_file", "arguments": {"file": "utils.py"}},
    {"name": "append_to_file", "arguments": {"file": "utils.py", "content": "# Added by test"}},
    {"name": "repo_read_tail", "arguments": {"file": "utils.py", "n": 5}}
]}
"""


class DirectorToolIntegration:
    """Director 工具集成."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.executor = AgentAccelToolExecutor(workspace)
        self.registry = create_default_registry()
        self._closed = False

    def close(self) -> None:
        """关闭执行器并释放资源."""
        if not self._closed:
            self._closed = True
            self.executor.close_sync()

    def __del__(self) -> None:
        """析构时确保资源释放."""
        if hasattr(self, "_closed") and not self._closed:
            with contextlib.suppress(AttributeError):
                self.close()

    def __enter__(self):
        """上下文管理器入口."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，确保资源释放."""
        self.close()
        return False

    def get_system_prompt(self) -> str:
        """获取带有工具说明的系统提示."""
        return f"{_LEGACY_TEXT_TOOL_PROTOCOL_NOTICE}\n\n{DIRECTOR_TOOL_PROMPT}"

    def process_llm_response(self, response: str) -> dict[str, Any]:
        """处理 LLM 响应，执行工具调用."""
        return _disabled_text_tool_protocol_result(response, role="director")

    def format_tools_for_native_calling(self) -> list[dict[str, Any]]:
        """格式化为原生 Function Calling 格式."""
        return self.registry.to_openai_functions()

    def build_tool_results_prompt(self, executed_tools: list[dict]) -> str:
        """构建工具结果提示，发送给 LLM.

        Args:
            executed_tools: 执行的工具列表

        Returns:
            格式化的工具结果文本
        """
        results = []
        for item in executed_tools:
            result_text = format_tool_result(
                item["tool"],
                item["result"],
            )
            results.append(result_text)

        return "\n\n".join(
            [
                "工具执行结果：",
                *results,
                "\n请根据以上结果继续你的分析和决策。",
            ]
        )


# ============== PM 集成 ==============

PM_TOOL_PROMPT = """你是 PM（尚书令），负责项目管理与规划。

## 当前状态

当前版本使用原生 Function Calling 机制进行工具调用。

## 工具使用

你可以通过调用工具来完成任务。每次调用返回结果后，
系统会自动将结果注入上下文，你可以继续调用下一个工具。

## 响应格式

当需要调用工具时，使用以下JSON格式:
{
    "tool_calls": [
        {
            "name": "tool_name",
            "arguments": { "arg1": "value1", "arg2": "value2" }
        }
    ]
}

不需要工具时，直接返回你的分析和建议。

## 可用工具（只读）

#### repo_rg / grep / ripgrep
代码搜索工具。
使用场景：了解项目功能实现、查找相关代码

#### glob
文件匹配工具。
使用场景：查找特定类型文件

#### repo_tree
目录列表工具。
使用场景：浏览项目结构

#### file_exists
文件存在检查。
使用场景：确认文件是否存在

#### read_file
读取文件内容。
使用场景：查看模块结构、类定义

---

现在开始工作！
"""


class PMToolIntegration:
    """PM 工具集成."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.executor = AgentAccelToolExecutor(workspace)
        self.registry = create_default_registry()
        self._closed = False

    def close(self) -> None:
        """关闭执行器并释放资源."""
        if not self._closed:
            self._closed = True
            self.executor.close_sync()

    def __del__(self) -> None:
        """析构时确保资源释放."""
        if hasattr(self, "_closed") and not self._closed:
            with contextlib.suppress(AttributeError):
                self.close()

    def __enter__(self):
        """上下文管理器入口."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，确保资源释放."""
        self.close()
        return False

    def get_system_prompt(self) -> str:
        """获取带有工具说明的系统提示."""
        return f"{_LEGACY_TEXT_TOOL_PROTOCOL_NOTICE}\n\n{PM_TOOL_PROMPT}"

    def process_llm_response(self, response: str) -> dict[str, Any]:
        """处理 LLM 响应，执行工具调用."""
        return _disabled_text_tool_protocol_result(response, role="pm")


# ============== Architect 集成 ==============

ARCHITECT_TOOL_PROMPT = """你是 Architect（中书令），负责架构设计。

## 当前状态

当前版本使用原生 Function Calling 机制进行工具调用。

## 工具使用

你可以通过调用工具来完成任务。每次调用返回结果后，
系统会自动将结果注入上下文，你可以继续调用下一个工具。

## 响应格式

当需要调用工具时，使用以下JSON格式:
{
    "tool_calls": [
        {
            "name": "tool_name",
            "arguments": { "arg1": "value1", "arg2": "value2" }
        }
    ]
}

不需要工具时，直接返回你的分析和建议。

## 可用工具（只读）

#### repo_rg / grep / ripgrep
代码搜索工具。
使用场景：了解现有技术实现、查找设计模式

#### glob
文件匹配工具。
使用场景：查找特定类型文件

#### repo_tree
目录列表工具。
使用场景：浏览项目结构

#### file_exists
文件存在检查。
使用场景：确认文件是否存在

#### read_file
读取文件内容。
使用场景：查看模块依赖、接口定义

---

现在开始工作！
"""


class ArchitectToolIntegration:
    """Architect 工具集成."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.executor = AgentAccelToolExecutor(workspace)
        self.registry = create_default_registry()
        self._closed = False

    def close(self) -> None:
        """关闭执行器并释放资源."""
        if not self._closed:
            self._closed = True
            self.executor.close_sync()

    def __del__(self) -> None:
        """析构时确保资源释放."""
        if hasattr(self, "_closed") and not self._closed:
            with contextlib.suppress(AttributeError):
                self.close()

    def __enter__(self):
        """上下文管理器入口."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，确保资源释放."""
        self.close()
        return False

    def get_system_prompt(self) -> str:
        """获取带有工具说明的系统提示."""
        return f"{_LEGACY_TEXT_TOOL_PROTOCOL_NOTICE}\n\n{ARCHITECT_TOOL_PROMPT}"

    def process_llm_response(self, response: str) -> dict[str, Any]:
        """处理 LLM 响应，执行工具调用."""
        return _disabled_text_tool_protocol_result(response, role="architect")


# ============== QA 集成 ==============

QA_TOOL_PROMPT = """你是 QA（门下侍中），负责质量保障。

## 当前状态

当前版本使用原生 Function Calling 机制进行工具调用。

## 工具使用

你可以通过调用工具来完成任务。每次调用返回结果后，
系统会自动将结果注入上下文，你可以继续调用下一个工具。

## 响应格式

当需要调用工具时，使用以下JSON格式:
{
    "tool_calls": [
        {
            "name": "tool_name",
            "arguments": { "arg1": "value1", "arg2": "value2" }
        }
    ]
}

不需要工具时，直接返回你的分析和建议。

## 可用工具（只读）

#### repo_rg / grep / ripgrep
代码搜索工具。
使用场景：查找特定代码模式、安全检查

#### glob
文件匹配工具。
使用场景：查找测试文件、源代码文件

#### repo_tree
目录列表工具。
使用场景：浏览项目结构

#### file_exists
文件存在检查。
使用场景：确认文件是否存在

#### read_file
读取文件内容。
使用场景：审查代码实现

---

现在开始工作！
"""


class QAToolIntegration:
    """QA 工具集成."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.executor = AgentAccelToolExecutor(workspace)
        self.registry = create_default_registry()
        self._closed = False

    def close(self) -> None:
        """关闭执行器并释放资源."""
        if not self._closed:
            self._closed = True
            self.executor.close_sync()

    def __del__(self) -> None:
        """析构时确保资源释放."""
        if hasattr(self, "_closed") and not self._closed:
            with contextlib.suppress(AttributeError):
                self.close()

    def __enter__(self):
        """上下文管理器入口."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，确保资源释放."""
        self.close()
        return False

    def get_system_prompt(self) -> str:
        """获取带有工具说明的系统提示."""
        return f"{_LEGACY_TEXT_TOOL_PROTOCOL_NOTICE}\n\n{QA_TOOL_PROMPT}"

    def process_llm_response(self, response: str) -> dict[str, Any]:
        """处理 LLM 响应，执行工具调用."""
        return _disabled_text_tool_protocol_result(response, role="qa")


# ============== 便捷函数 ==============


def enhance_chief_engineer_prompt(base_prompt: str) -> str:
    """增强 ChiefEngineer 提示，添加工具说明."""
    integration = ChiefEngineerToolIntegration(".")
    return f"""{integration.get_system_prompt()}

---

{base_prompt}
"""


def enhance_director_prompt(base_prompt: str) -> str:
    """增强 Director 提示，添加工具说明."""
    integration = DirectorToolIntegration(".")
    return f"""{integration.get_system_prompt()}

---

{base_prompt}
"""


# ============== Scout 集成 ==============

SCOUT_TOOL_PROMPT = """你是 Scout（探子），负责快速探索代码库并生成总结报告。

你是一个只读辅助角色，主要通过搜索和阅读代码来构建对项目的认知。

## 可用工具

你只能使用以下只读工具。禁止使用任何修改文件或执行代码的工具。

### 只读工具

#### repo_rg / grep / ripgrep
代码搜索工具。
使用场景：了解项目功能实现、查找相关代码

参数:
  - query (string): 搜索关键字/正则 [必需]
  - file_patterns (array): 文件模式过滤 [可选]
  - max_results (integer): 最大结果数 [默认: 50]

#### glob
文件路径匹配工具。
使用场景：批量查找特定类型文件

参数:
  - pattern (string): glob 模式，如 "src/**/*.py" [必需]
  - recursive (boolean): 递归搜索 [默认: false]

#### repo_tree
目录列表工具。
使用场景：浏览项目结构

参数:
  - path (string): 目录路径 [默认: "."]
  - recursive (boolean): 递归列出 [默认: false]

#### file_exists
文件存在检查。
使用场景：确认文件是否存在

#### read_file
读取文件内容。
使用场景：查看模块结构、类定义

参数:
  - file (string): 文件路径 [必需]

---

现在开始工作！
"""


class ScoutToolIntegration:
    """Scout 工具集成 (只读辅助角色)."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.executor = AgentAccelToolExecutor(workspace)
        self.registry = create_default_registry()
        self._closed = False

    def close(self) -> None:
        """关闭执行器并释放资源."""
        if not self._closed:
            self._closed = True
            self.executor.close_sync()

    def __del__(self) -> None:
        """析构时确保资源释放."""
        if hasattr(self, "_closed") and not self._closed:
            with contextlib.suppress(AttributeError):
                self.close()

    def __enter__(self):
        """上下文管理器入口."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，确保资源释放."""
        self.close()
        return False

    def get_system_prompt(self) -> str:
        """获取带有工具说明的系统提示."""
        return f"{_LEGACY_TEXT_TOOL_PROTOCOL_NOTICE}\n\n{SCOUT_TOOL_PROMPT}"

    def process_llm_response(self, response: str) -> dict[str, Any]:
        """处理 LLM 响应，执行工具调用."""
        return _disabled_text_tool_protocol_result(response, role="scout")

    def build_tool_results_prompt(self, executed_tools: list[dict]) -> str:
        """构建工具结果提示."""
        results = []
        for item in executed_tools:
            result_text = format_tool_result(
                item["tool"],
                item["result"],
            )
            results.append(result_text)

        return "\n\n".join(results)


# ============== 兼容层 ==============


class ToolEnabledLLMClient:
    """支持工具调用的 LLM 客户端包装器.

    包装现有 LLM 客户端，添加工具调用能力。
    """

    def __init__(
        self,
        base_client,
        workspace: str,
        role: str = "chiefengineer",
        max_tool_iterations: int = 3,
    ) -> None:
        self.base_client = base_client
        self.workspace = workspace
        self.max_tool_iterations = max_tool_iterations

        if role == "chiefengineer":
            self.integration: ChiefEngineerToolIntegration | DirectorToolIntegration = ChiefEngineerToolIntegration(
                workspace
            )
        else:
            self.integration = DirectorToolIntegration(workspace)

    async def chat(self, messages: list[dict[str, str]], **kwargs) -> dict[str, Any]:
        """支持工具调用的聊天."""
        iteration = 0

        while iteration < self.max_tool_iterations:
            # 调用基础客户端
            response = await self._call_base_client(messages, **kwargs)
            content = response.get("content", "")

            # 检查工具调用
            result = self.integration.process_llm_response(content)

            if not result["has_tools"]:
                return response

            # 执行工具后继续对话
            messages.append({"role": "assistant", "content": content})

            tool_results = self.integration.build_tool_results_prompt(result["tools_executed"])
            messages.append({"role": "user", "content": tool_results})

            iteration += 1

        # 达到最大迭代次数
        final_response = await self._call_base_client(messages, **kwargs)
        return final_response

    async def _call_base_client(self, messages: list[dict[str, str]], **kwargs) -> dict[str, Any]:
        """调用基础客户端.

        正确处理同步和异步的客户端方法。
        """
        import inspect

        # 处理不同类型的客户端
        if hasattr(self.base_client, "chat"):
            chat_method = self.base_client.chat
            result = chat_method(messages, **kwargs)
            # 处理异步方法
            if inspect.isawaitable(result):
                return await result
            return result
        elif hasattr(self.base_client, "invoke"):
            prompt = messages[-1].get("content", "") if messages else ""
            invoke_method = self.base_client.invoke
            result = invoke_method(prompt, **kwargs)
            # 处理异步方法
            if inspect.isawaitable(result):
                result = await result
            return {"content": result}
        else:
            raise ValueError("Unsupported client type")


# ============== 角色工具集成注册表 ==============

_SUPPORTED_ROLES = (
    "pm",
    "architect",
    "chief_engineer",
    "director",
    "qa",
    "scout",
)

ROLE_TOOL_INTEGRATIONS: dict[str, type] = {
    "pm": PMToolIntegration,
    "architect": ArchitectToolIntegration,
    "chief_engineer": ChiefEngineerToolIntegration,
    "director": DirectorToolIntegration,
    "qa": QAToolIntegration,
    "scout": ScoutToolIntegration,
}


def get_role_tool_integration(role: str, workspace: str):
    """获取角色工具集成的工厂函数。

    Args:
        role: 角色标识 (pm, architect, chief_engineer, director, qa, scout)
        workspace: 工作区路径

    Returns:
        对应角色的 ToolIntegration 实例

    Raises:
        ValueError: 如果角色不存在

    示例:
        >>> integration = get_role_tool_integration("pm", "/path/to/project")
        >>> print(integration.get_system_prompt())
    """
    if role not in _SUPPORTED_ROLES:
        raise ValueError(f"未知角色: {role}。可用: {list(_SUPPORTED_ROLES)}")
    integration_class = ROLE_TOOL_INTEGRATIONS[role]
    return integration_class(workspace)


__all__ = [
    # 注册表
    "ROLE_TOOL_INTEGRATIONS",
    # 支持的角色
    "_SUPPORTED_ROLES",
    "ArchitectToolIntegration",
    "ChiefEngineerToolIntegration",
    "DirectorToolIntegration",
    # 角色集成类
    "PMToolIntegration",
    "QAToolIntegration",
    "ScoutToolIntegration",
    # 兼容层
    "ToolEnabledLLMClient",
    # 便捷函数
    "enhance_chief_engineer_prompt",
    "enhance_director_prompt",
    "get_role_tool_integration",
]
