# Polaris 核心系统地图

本文从仓库根 `CLAUDE.md` 原样迁出，保留完整约束。适用任务须在修改或派工前读取；与更细粒度架构规范冲突时遵循原有优先级。

## 7) 核心系统地图（防重复造轮子）

以下模块已实现，禁止重复创建：

### 7.1) LLM 工具系统
**唯一实现**: `src/backend/polaris/kernelone/llm/toolkit/`

```python
# ✅ 正确用法
from polaris.kernelone.llm.toolkit import (
    AgentAccelToolExecutor,      # 统一工具执行器
    parse_tool_calls,            # 工具调用解析
)

# 获取角色工具集成（注册表 ROLE_TOOL_INTEGRATIONS 现位于 tool_runtime cell）
from polaris.cells.llm.tool_runtime.internal.role_integrations import ROLE_TOOL_INTEGRATIONS

integration = ROLE_TOOL_INTEGRATIONS["pm"](workspace=".")
prompt = integration.get_system_prompt()
```

**禁止行为**:
- ✗ 在 `polaris/cells/llm/` 下新建 `*ToolIntegration` 类
- ✗ 自定义 `TOOL_CALL:...ARGS:...` 格式
- ✗ 直接调用底层 `tools.py`

**相关文件**:
- `polaris/kernelone/llm/toolkit/definitions.py` - 工具定义（单一事实来源）
- `polaris/kernelone/llm/toolkit/executor/` - 工具执行（目录）
- `polaris/cells/llm/tool_runtime/internal/role_integrations.py` - 5个角色的工具集成
- `polaris/kernelone/llm/toolkit/parsers/` - 工具调用解析（目录）

### 7.2) 角色对话系统
**唯一实现**: `src/backend/polaris/cells/llm/dialogue/internal/role_dialogue.py`

```python
# ✅ 正确用法
from polaris.cells.llm.dialogue.internal.role_dialogue import generate_role_response

result = await generate_role_response(
    workspace=workspace,
    settings=settings,
    role="pm",  # 或 architect, chief_engineer, director, qa
    message=message,
)
```

**角色提示词注册表**: `ROLE_PROMPT_TEMPLATES`
- `pm` - 尚书令 (项目管理)
- `architect` - 中书令 (架构设计)
- `chief_engineer` - 工部尚书 (技术分析)
- `director` - 工部侍郎 (代码执行)
- `qa` - 门下侍中 (质量审查)
- `scout` - 探子 (只读代码探索，sub-agent，即将由 PM/Director 调用)

**禁止行为**:
- ✗ 在 `polaris/cells/llm/dialogue/` 下新建独立角色对话文件（已统一到 `role_dialogue.py`）
- ✗ 在角色 CLI/internal 模块下内嵌角色提示词
- ✗ 创建新的 `generate_xxx_response()` 函数

### 7.3) Provider 系统
- ✗ 直接操作 `base_provider.provider_registry`
- ✗ 绕过 `ProviderManager` 创建 Provider 实例

### 7.4) 任务管理系统
**唯一实现**: `src/backend/polaris/cells/runtime/task_runtime/internal/task_board.py`

```python
# ✅ 正确用法
from polaris.cells.runtime.task_runtime.internal.task_board import TaskBoard

board = TaskBoard(workspace=".")
board.create(subject="实现登录功能", priority="high")
```

### 7.5) 已删除模块（历史记录）

| 模块 | 替代方案 | 状态 |
|------|----------|------|
| `pm_dialogue.py` | `polaris.cells.llm.dialogue.internal.role_dialogue.generate_role_response(role="pm", ...)` | 已删除 |
| `pm_tools.py` | `polaris.kernelone.llm.toolkit.AgentAccelToolExecutor` | 已删除 |
| `api/routers/pm.py` | `polaris/delivery/http/routers/pm_chat.py` + `pm_management.py`（`/v2/pm`） | 已删除 |
| `workflow_nodes_compat.py` | `polaris/cells/roles/adapters/internal/workflow_adapter.py` | 已删除 |

### 7.6) 新增能力检查清单

在实现新功能前，检查：

1. **工具能力?** → 先看 `polaris/kernelone/llm/toolkit/` 是否已存在
2. **角色对话?** → 先看 `role_dialogue.ROLE_PROMPT_TEMPLATES` 是否已有
3. **Provider?** → 先看 `providers/provider_registry.py` 是否已支持
4. **任务管理?** → 先看 `task_board.py` 是否满足需求

如果不确定，查看对应模块的 `__init__.py` 中的 **"防重复造轮子提示"** 区域。

