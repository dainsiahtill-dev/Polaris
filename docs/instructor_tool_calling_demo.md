# Instructor 工具调用支持演示

## 概述

所有角色现在都支持在结构化输出中包含工具调用。

## 使用模式

### 1. PM 角色 - 带工具调用的任务规划

```python
from app.roles.schemas import TaskListOutput, ToolCall

# PM 在分析项目时需要先使用工具收集信息
output = TaskListOutput(
    tasks=[],  # 任务列表为空，因为需要先执行工具
    analysis={
        "total_tasks": 0,
        "risk_level": "low",
        "recommended_sequence": []
    },
    tool_calls=[
        ToolCall(
            tool="search_code",
            arguments={"query": "authentication", "file_patterns": ["*.py"]},
            reasoning="Need to understand existing auth implementation"
        ),
        ToolCall(
            tool="read_file",
            arguments={"file": "src/config.py"},
            reasoning="Check current configuration structure"
        )
    ],
    next_action="call_tools",  # 指示需要执行工具
    is_complete=False
)
```

### 2. Architect 角色 - 带工具调用的架构设计

```python
from app.roles.schemas import ArchitectOutput, ToolCall

output = ArchitectOutput(
    system_overview="",
    architecture_diagram="",
    key_decisions=[],
    technology_stack=[],
    modules=[],
    data_flow="",
    tool_calls=[
        ToolCall(
            tool="glob",
            arguments={"pattern": "src/**/*.py"},
            reasoning="Understand project structure"
        ),
        ToolCall(
            tool="list_directory",
            arguments={"path": "src", "recursive": False},
            reasoning="Check top-level organization"
        )
    ],
    next_action="call_tools",
    is_complete=False
)
```

### 3. Chief Engineer 角色 - 带工具调用的蓝图设计

```python
from app.roles.schemas import BlueprintOutput, ToolCall

output = BlueprintOutput(
    analysis=None,  # 需要先分析代码
    construction_plan=None,
    scope_for_apply=[],
    tool_calls=[
        ToolCall(
            tool="read_file",
            arguments={"file": "src/models/user.py"},
            reasoning="Understand current user model"
        ),
        ToolCall(
            tool="search_code",
            arguments={"query": "def login", "file_patterns": ["*.py"]},
            reasoning="Find existing login implementation"
        )
    ],
    next_action="call_tools",
    is_complete=False
)
```

### 4. QA 角色 - 带工具调用的代码审查

```python
from app.roles.schemas import QAReportOutput, ToolCall

output = QAReportOutput(
    verdict="",  # 空 verdict 表示需要更多信息
    summary="",
    findings=[],
    tool_calls=[
        ToolCall(
            tool="search_code",
            arguments={"query": "eval\\(|exec\\("},
            reasoning="Check for security vulnerabilities"
        ),
        ToolCall(
            tool="glob",
            arguments={"pattern": "tests/**/*.py"},
            reasoning="Find test files for coverage analysis"
        )
    ],
    next_action="call_tools",
    is_complete=False
)
```

### 5. Director 角色 - 完整的工具调用和补丁

```python
from app.roles.schemas import DirectorOutput, ToolCall, PatchOperation

# Director 可以使用工具调用或补丁
output = DirectorOutput(
    mode="mixed",
    summary="Implement login functionality",
    patches=[
        PatchOperation(
            file="src/auth.py",
            search="",
            replace="def login(): ...",
            description="Add login function"
        )
    ],
    tool_calls=[
        ToolCall(
            tool="execute_command",
            arguments={"command": "pytest tests/auth/"},
            reasoning="Run tests to verify implementation"
        )
    ],
    next_action="call_tools",
    is_complete=False
)
```

## 工作流程

### 1. 首次调用

LLM 返回包含 `tool_calls` 但 `is_complete=False` 的输出：

```json
{
  "tasks": [],
  "analysis": {"total_tasks": 0, "risk_level": "low"},
  "tool_calls": [
    {"tool": "search_code", "arguments": {...}, "reasoning": "..."}
  ],
  "next_action": "call_tools",
  "is_complete": false
}
```

### 2. 执行工具

Kernel 检测到 `next_action="call_tools"`，执行工具并将结果追加到上下文。

### 3. 最终调用

LLM 返回完整输出，`is_complete=True`：

```json
{
  "tasks": [...],
  "analysis": {"total_tasks": 5, "risk_level": "medium"},
  "tool_calls": [],
  "next_action": "respond",
  "is_complete": true
}
```

## 内核集成

```python
# kernel.py
pre_validated_data = structured_response.data

# 检查是否需要工具调用
if pre_validated_data.get("next_action") == "call_tools":
    tool_calls = pre_validated_data.get("tool_calls", [])
    # 执行工具并继续循环
    for tc in tool_calls:
        result = await execute_tool(tc["tool"], tc["arguments"])
        # 追加结果到上下文
```

## 优势

1. **类型安全**: 工具调用参数通过 Pydantic 验证
2. **清晰意图**: `reasoning` 字段说明为什么需要这个工具
3. **可中断**: `is_complete` 和 `next_action` 控制流程
4. **统一格式**: 所有角色使用相同的工具调用格式
