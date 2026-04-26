# Polaris 角色工具系统集成报告

## 概述

本次改进为Polaris的5个LLM角色（PM/Architect/CE/Director/QA）添加了完整的工具使用能力，解决了之前PM只能使用命令行的问题。

## 核心改进

### 1. 差异化工具分配（Token优化）

| 角色 | 中文名 | 工具数量 | 权限范围 | 设计原则 |
|-----|-------|---------|---------|---------|
| PM | 尚书令 | 21 | 只读 + 任务管理 | 不写代码，专注规划 |
| Architect | 中书令 | **11** | 纯只读 | 仅架构分析，无执行权限 |
| ChiefEngineer | 工部尚书 | 21 | 只读 + 执行 | 技术分析，不写代码 |
| Director | 工部侍郎 | **26** | 全权限 | 唯一代码编辑权限 |
| QA | 门下侍中 | 21 | 只读 + 测试 | 审查但不修改 |

**Token优化策略**:
- Architect仅11个工具（最少），纯只读分析
- Director独享5个代码编辑工具
- PM/CE/QA无代码编辑权限（避免越权操作）

### 2. 新增文件

#### 核心模块
- `src/backend/app/llm/usecases/role_tools.py` - 统一角色工具系统
  - 角色工具配置（ROLE_TOOLS_CONFIG）
  - 工具权限控制（get_role_available_tools）
  - 工具执行器（RoleToolExecutor）
  - 工具调用解析（parse_tool_calls）

#### 保留文件
- `src/backend/app/llm/usecases/pm_tools.py` - PM专用工具（向后兼容）

### 3. 修改文件

#### `src/backend/app/llm/usecases/role_dialogue.py`
**主要变更**:
1. 为所有角色添加工具使用指南（各角色差异化）
2. 更新`generate_role_response`函数，支持所有角色的工具调用
3. 添加工具调用解析和执行逻辑
4. 保留原有输出验证和质量检查

**角色提示词增强**:
- PM: 项目探索 + 任务管理工具
- Architect: 架构分析专用工具
- CE: 技术分析 + 静态检查工具
- Director: 完整工具（包含代码编辑）
- QA: 代码审查 + 测试执行工具

### 4. 工具分类

#### 项目探索（所有角色）
- `repo_tree` - 目录结构
- `repo_rg` - 代码搜索
- `repo_map` - 代码地图
- `repo_symbols_index` - 符号索引

#### 文件读取（所有角色）
- `repo_read_slice` - 读取行范围
- `repo_read_around` - 读取上下文
- `repo_read_head/tail` - 读取头尾
- `repo_diff` - 代码变更

#### 技能系统（所有角色）
- `skill_manifest` - 列出技能
- `load_skill` - 加载技能

#### 任务管理（PM/CE/QA/Director）
- `task_create/update/ready`
- `todo_read/write`

#### 后台任务（PM/CE/QA/Director）
- `background_run/check/list/cancel/wait`

#### 代码编辑（仅Director）
- `precision_edit` - SEARCH/REPLACE编辑
- `repo_apply_diff` - 应用补丁
- `treesitter_replace_node` - AST节点替换
- `treesitter_insert_method` - 插入方法
- `treesitter_rename_symbol` - 重命名符号

#### 上下文管理（可选）
- `compact_context` - 压缩上下文

### 5. 工具调用格式

```
<thinking>
我需要分析项目结构。

TOOL_CALL: repo_tree
ARGS:
{"path": "src", "max_entries": 30}
END TOOL_CALL

TOOL_CALL: repo_rg
ARGS:
{"pattern": "def.*login", "glob": "*.py"}
END TOOL_CALL
</thinking>
```

工具结果会自动注入到prompt，LLM基于结果生成最终回复。

### 6. 权限控制机制

```python
# RoleToolExecutor 自动检查权限
def can_execute(self, tool_name: str) -> bool:
    if tool_name not in self.available_tools:
        return False
    # 返回详细错误信息
```

### 7. 向后兼容

- 原`pm_tools.py`保留，向后兼容
- `generate_role_response`函数参数不变
- 工具调用是可选功能，不启用时不影响原有流程

## 测试验证

```python
# 验证各角色工具数量
assert len(get_role_available_tools('pm')) == 21
assert len(get_role_available_tools('architect')) == 11  # 最少
assert len(get_role_available_tools('director')) == 26   # 最多

# 验证工具调用解析
calls = parse_tool_calls(text)
assert len(calls) > 0

# 验证权限控制
executor = RoleToolExecutor(workspace, 'pm')
assert not executor.can_execute('precision_edit')  # PM不能编辑代码

executor = RoleToolExecutor(workspace, 'director')
assert executor.can_execute('precision_edit')  # Director可以
```

## 使用示例

### PM 使用工具
```python
from app.llm.usecases.role_dialogue import generate_role_response

result = await generate_role_response(
    workspace="/path/to/project",
    settings=settings,
    role="pm",
    message="分析项目结构并创建任务",
)
# result 包含 tool_calls 字段，记录执行的工具
```

### 手动执行工具
```python
from app.llm.usecases.role_tools import execute_role_tool_call

result = execute_role_tool_call(
    workspace=".",
    role="pm",
    tool_name="repo_tree",
    args={"path": "src"}
)
```

## 后续建议

### 短期
1. 在真实场景下测试工具调用效果
2. 根据使用频率调整工具提示词权重
3. 添加工具调用超时和重试机制

### 中期
1. 工具调用结果缓存
2. 工具调用链路追踪（observability）
3. 自动工具选择建议（Agent能力）

### 长期
1. 工具调用策略学习（RL优化）
2. 工具组合推荐系统
3. 跨角色工具协作协议

## 总结

本次改进使Polaris的LLM角色从"只能对话"升级为"能使用工具行动"：

1. **差异化设计**: 根据角色职责分配工具，避免token浪费
2. **权限隔离**: 只有Director能编辑代码，其他角色各司其职
3. **统一架构**: 所有角色使用同一套工具系统，便于维护
4. **向后兼容**: 不影响现有功能，渐进式启用

**状态**: ✅ 已完成，可立即投入使用。
