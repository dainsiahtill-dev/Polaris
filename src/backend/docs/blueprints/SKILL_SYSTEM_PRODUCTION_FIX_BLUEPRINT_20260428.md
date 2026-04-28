# Skill System Production Fix Blueprint

## 现状诊断

Skill系统存在**"有代码无运行时"**问题：
- `SkillLoader`/`SkillToolInterface` 代码完整且测试通过
- `load_skill`/`skill_manifest` 工具规范已注册
- **但**：工具执行器未绑定 Skill handler，Agent 调用会返回 "Handler not implemented"
- **但**：默认 Skill 模板未初始化到 workspace，系统空转
- **但**：Cells 层和 KernelOne 层实现重复，未统一

## 修复目标

使 Agent 能够通过工具调用实际加载和使用 Skill，系统达到生产可用状态。

## 架构决策

### D1: KernelOne 层为主实现
- `polaris/kernelone/single_agent/skill_system.py` 作为唯一主实现
- `polaris/cells/roles/runtime/internal/skill_loader.py` 标记废弃，保留兼容 re-export
- 原因：KernelOne 层实现更完整（含 `SkillToolInterface`、默认模板、大小限制）

### D2: Handler 显式注册
- 新建 `polaris/kernelone/llm/toolkit/executor/handlers/skills.py`
- 在 `ToolHandlerRegistry.load_all()` 中注册
- Handler 签名：`(executor, **kwargs) -> dict[str, Any]`
- SkillLoader 生命周期：每个 executor 实例持有一个（按 workspace 隔离）

### D3: 默认 Skill 初始化
- `AgentAccelToolExecutor.__init__` 中调用 `install_default_skills(workspace)`
- 写入 `.polaris/skills/*.md`（3 个默认模板）
- 幂等：已存在则跳过

### D4: 两层统一接口
- `SkillLoader` (Cells 层) 委托给 `KernelSkillLoader` (KernelOne 层)
- 保持 `Skill` dataclass 兼容（字段映射）
- 逐步迁移调用方，最终移除 Cells 层实现

## 实施计划

### Phase 1: 绑定工具执行器
1. 新建 `skills.py` handler 模块
   - `load_skill(name)` → `SkillToolInterface.load_skill(name)`
   - `skill_manifest(role)` → `SkillToolInterface.list_skills()`
2. 修改 `ToolHandlerRegistry.load_all()` 导入并注册 skills 模块
3. 在 `AgentAccelToolExecutor` 中初始化 `SkillLoader` 和 `SkillToolInterface`

### Phase 2: 初始化默认 Skill
1. 修改 `AgentAccelToolExecutor.__init__` 调用 `install_default_skills()`
2. 确保 `.polaris/skills/` 目录在 workspace 创建时自动生成

### Phase 3: 统一两层实现
1. 修改 Cells 层 `SkillLoader` 委托给 KernelOne 层
2. 添加 `DeprecationWarning`
3. 更新 `public/service.py` re-export 指向 KernelOne 实现

### Phase 4: 质量门禁
1. ruff check + ruff format
2. mypy
3. pytest（skill_loader 测试 + 新增 handler 测试）

### Phase 5: 验证
1. 单元测试：验证 handler 注册和执行
2. 集成测试：验证 Agent 能调用 load_skill 并获取内容
3. E2E 测试：验证 SUPER 模式下 Skill 可用

## 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `polaris/kernelone/llm/toolkit/executor/handlers/skills.py` | 新建 | Skill handler 模块 |
| `polaris/kernelone/llm/toolkit/executor/handlers/registry.py` | 修改 | 注册 skills 模块 |
| `polaris/kernelone/llm/toolkit/executor/core.py` | 修改 | 初始化 SkillLoader + SkillToolInterface |
| `polaris/kernelone/single_agent/skill_system.py` | 修改 | 暴露 `install_default_skills` 为公共 API |
| `polaris/cells/roles/runtime/internal/skill_loader.py` | 修改 | 委托给 KernelOne 层，添加废弃警告 |
| `polaris/cells/roles/runtime/public/service.py` | 修改 | re-export 指向 KernelOne SkillLoader |

## 风险评估

| 风险 | 缓解 |
|------|------|
| Handler 注册冲突 | 使用 `register_from_module`（last-wins） |
| Skill 文件覆盖 | `install_default_skills` 检查文件存在性 |
| 循环导入 | skills handler 延迟导入 SkillToolInterface |
| 性能影响 | SkillLoader 仅在首次工具调用时初始化 |

## 验收标准

- [ ] `pytest polaris/kernelone/llm/toolkit/executor/handlers/tests/test_skills.py` 通过
- [ ] `pytest polaris/cells/roles/runtime/internal/tests/test_skill_loader.py` 通过
- [ ] Agent 调用 `load_skill` 返回实际 Skill 内容（非 "Handler not implemented"）
- [ ] Agent 调用 `skill_manifest` 返回默认 3 个 Skill 列表
- [ ] ruff + mypy 零报错
