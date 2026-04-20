# Polaris 角色统一内核重构总结

## 重构完成清单

### Phase 1: 建立角色统一内核基础架构 ✅

**核心文件** (`src/backend/app/roles/`):

| 文件 | 职责 |
|------|------|
| `__init__.py` | 统一导出所有新API |
| `schema.py` | 类型定义（Profile、Policy、Request、Result） |
| `registry.py` | RoleProfileRegistry（SSOT配置中心） |
| `kernel.py` | RoleExecutionKernel（统一执行内核） |
| `data_store.py` | RoleDataStore（UTF-8 + 原子写） |
| `builtin_profiles.py` | 5核心角色内置配置 |
| `workflow_adapter.py` | 工作流适配器 |
| `workflow_node.py` | 工作流节点基类 |
| `compat.py` | 向后兼容层 |
| `gateways/tool_gateway.py` | RoleToolGateway（严格白名单） |
| `gateways/context_gateway.py` | RoleContextGateway（差异化上下文） |

**配置文件**:
- `src/backend/app/config/core_roles.yaml` - 5核心角色完整配置

### Phase 2: 创建5核心角色Profile配置 ✅

| 角色 | 核心能力 | 工具白名单 | 代码写入 | 命令执行 |
|------|----------|------------|----------|----------|
| **PM** | 任务拆解、依赖分析 | smart_search, analyze_code_changes... | ❌ | ❌ |
| **Architect** | 架构设计、ADR | read_file, smart_search... | ❌ | ❌ |
| **ChiefEngineer** | 蓝图生成、影响分析 | analyze_code_changes, get_impact_analysis... | ❌ | ❌ |
| **Director** | 代码执行、补丁应用 | apply_patch, write_file, execute_command... | ✅ | ✅ |
| **QA** | 审查、测试执行 | run_tests, read_file, analyze_code_changes... | ❌ | ✅(仅测试) |

### Phase 3: 迁移聊天入口到统一内核 ✅

**修改文件**: `src/backend/app/llm/usecases/role_dialogue.py`

- `generate_role_response()` - 优先使用 `RoleExecutionKernel`，保留旧实现作为回退
- `generate_role_response_streaming()` - **完全使用内核流式执行**，移除旧实现
- 新增 `prompt_appendix` 参数（仅追加）
- `system_prompt` 参数标记为废弃（转为 appendix，禁止覆盖核心提示词）
- 响应新增：`profile_version`, `prompt_fingerprint`, `tool_policy_id`

### Phase 4: 迁移工作流入口到统一内核 ✅

**工作流适配器**: `src/backend/app/roles/workflow_adapter.py`
- `WorkflowRoleAdapter` - 为工作流节点提供内核接口
- `execute_role()` - 执行单轮角色对话
- `execute_role_with_tools()` - 自动处理多轮工具调用
- `validate_role_permission()` - 验证工具权限

**工作流节点基类**: `src/backend/app/roles/workflow_node.py`
- `WorkflowRoleNode` - 统一工作流节点基类
- `execute_kernel()` - 通过内核执行角色
- 自动处理工具调用和多轮对话

### Phase 5: 清理分叉实现与下线旧代码 ✅

**向后兼容层**: `src/backend/app/roles/compat.py`
- `StandaloneRoleAgentCompat` - 适配旧 StandaloneRoleAgent
- `RoleKernelLLMClient` - 适配旧 LLMClient
- `create_standalone_agent()` - 兼容函数（带废弃警告）
- `create_llm_client()` - 兼容函数（带废弃警告）

所有旧接口现在：
1. 内部使用 `RoleExecutionKernel`
2. 发出 `DeprecationWarning`
3. 保持向后兼容的API签名

### Phase 6: 全功能联调与门禁测试 ✅

**测试文件**: `src/backend/tests/test_roles_kernel.py`

测试覆盖：
- `TestRoleProfileRegistry` - Profile加载、指纹一致性
- `TestRoleToolGateway` - 白名单/黑名单、路径穿越防护、危险命令检测
- `TestPromptFingerprint` - 提示词指纹一致性
- `TestRoleExecutionKernel` - 基本执行、流式执行
- `TestWorkflowAdapter` - 工作流适配
- `TestDataStore` - UTF-8读写、原子写、路径安全
- `TestChatWorkflowConsistency` - 聊天/工作流指纹一致性
- `TestMigrationCompat` - 废弃参数处理

## 关键变更汇总

### API变更

| 旧用法 | 新用法 | 状态 |
|--------|--------|------|
| `generate_role_response(..., system_prompt=...)` | `generate_role_response(..., prompt_appendix=...)` | 废弃警告 |
| 直接调用 `role_dialogue.py` | 内部走 `RoleExecutionKernel` | 自动迁移 |
| 直接使用 `StandaloneRoleAgent` | `StandaloneRoleAgentCompat` | 兼容层 |
| 直接使用 `LLMClient` | `RoleKernelLLMClient` | 兼容层 |

### 新增响应字段

```python
{
    "response": "...",
    "thinking": "...",
    "role": "pm",
    "model": "...",
    "provider": "...",
    # 新增：
    "profile_version": "1.0.0",
    "prompt_fingerprint": "a1b2c3d4...",  # 一致性追踪
    "tool_policy_id": "e5f6g7h8...",      # 权限策略追踪
}
```

## 验证命令

```bash
# 1. 验证语法
python -c "import ast; ast.parse(open('src/backend/app/roles/kernel.py').read())"

# 2. 验证导入
python -c "from app.roles import RoleExecutionKernel, RoleProfileRegistry; print('OK')"

# 3. 运行测试
pytest src/backend/tests/test_roles_kernel.py -v

# 4. 验证配置文件
python -c "from app.roles import load_core_roles; r = load_core_roles(); print(f'Loaded {len(r.list_roles())} roles')"
```

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        统一角色内核架构                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │  应用层 (Application Layer)                                │ │
│  │  ├── role_dialogue.py (聊天入口)                          │ │
│  │  ├── role_chat.py (API路由)                               │ │
│  │  └── scripts/pm/nodes/*.py (工作流节点)                   │ │
│  └───────────────────────┬───────────────────────────────────┘ │
│                          │                                       │
│                          ▼                                       │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │  执行层 (Execution Layer)                                  │ │
│  │  ┌─────────────────────────────────────────────────────┐  │ │
│  │  │ RoleExecutionKernel                                  │  │ │
│  │  │ ├── run(mode="chat")      # 聊天模式                │  │ │
│  │  │ ├── run(mode="workflow")  # 工作流模式              │  │ │
│  │  │ └── run_stream()          # 流式执行                │  │ │
│  │  └─────────────────────────────────────────────────────┘  │ │
│  └───────────────────────┬───────────────────────────────────┘ │
│                          │                                       │
│                          ▼                                       │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │  策略层 (Policy Layer)                                     │ │
│  │  ├── RoleProfileRegistry    # 配置SSOT                   │ │
│  │  ├── RoleToolGateway        # 工具权限控制               │ │
│  │  ├── RoleContextGateway     # 上下文构建                 │ │
│  │  └── RoleDataStore          # 数据存储                   │ │
│  └───────────────────────────────────────────────────────────┘ │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 后续建议

1. **验证真实LLM调用** - 在配置了LLM provider的环境中运行完整测试
2. **前端协议对齐** - 确保前端能处理新的响应字段（`profile_version`, `prompt_fingerprint`）
3. **工作流节点迁移** - 逐步将 `scripts/pm/nodes/*.py` 改为继承 `WorkflowRoleNode`
4. **清理旧代码** - 在验证稳定后，物理删除 `_legacy_generate_role_response`
5. **性能优化** - 根据实际使用情况优化上下文压缩策略

## 硬约束保证

✅ **system_prompt 仅追加，禁止覆盖核心提示词**
✅ **UTF-8 编码，原子写入**
✅ **路径穿越防护**
✅ **危险命令检测**
✅ **角色工具白名单强制**
✅ **聊天/工作流指纹一致性**
