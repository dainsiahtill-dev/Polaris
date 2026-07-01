# Verification Card: LLM工具调用收敛 Phase 4 - 角色策略收敛

**验证卡片**: VC-20260328-004
**Phase**: Phase 4
**负责人**: 工程师丁 (Policy-Warden)
**技术总监**: Dains
**创建时间**: 2026-03-28
**目标完成日期**: 2026-05-28

---

## 验证目标

角色工具策略收敛到 `roles.kernel` policy layer；危险命令模式由
`kernelone.security` 统一持有。`polaris.kernelone.policy` 包已退休，禁止
作为 RolePolicyEngine 或兼容 facade 重建。

---

## 验证条件

### 条件1: roles.kernel policy layer 正确实现

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| Tool/approval/syntax/budget policy layer | 代码审查 | 角色运行策略在 `roles.kernel` 内聚 | ⏳ |
| whitelist检查 | 单元测试 | 通配符支持正确 | ⏳ |
| blacklist检查 | 单元测试 | 优先级正确 | ⏳ |
| category权限检查 | 单元测试 | code_write/command_execution/file_delete正确 | ⏳ |
| 路径遍历检测 | 单元测试 | 阻止恶意路径 | ⏳ |

### 条件2: 危险命令Patterns统一

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| 统一Patterns定义 | 代码审查 | `kernelone.security.dangerous_patterns` 一处定义 | ⏳ |
| 角色工具策略使用 | 代码审查 | 通过 policy layer / command auditor 消费统一定义 | ⏳ |
| BudgetPolicy使用 | 代码审查 | 引用统一定义 | ⏳ |
| 无重复定义 | grep搜索 | 0个重复 | ⏳ |

### 条件3: retired KernelOne policy package 不可回归

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| `polaris.kernelone.policy` | import 测试 | 不可导入 | ⏳ |
| `polaris/kernelone/policy/*.py` | 文件检查 | 源文件不存在 | ⏳ |
| reverse-dep baseline | 架构测试 | 不保留 retired package budget | ⏳ |
| 原有功能不变 | 集成测试 | 行为一致 | ⏳ |
| 单元测试通过 | pytest | 100%通过 | ⏳ |

### 条件4: YAML配置删除

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| core_roles.yaml已删除 | 文件检查 | 文件不存在 | ⏳ |
| builtin_profiles.py唯一源 | 代码审查 | 所有角色配置在此 | ⏳ |
| 角色加载正常 | 集成测试 | 6个角色加载正常 | ⏳ |

### 条件5: TOOL_NAME_ALIASES正确使用

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| 授权时使用别名 | 代码审查 | whitelist匹配支持别名 | ⏳ |
| 单元测试通过 | pytest | 100%通过 | ⏳ |

---

## 验证执行记录

### 2026-05-28 验证

```
执行者: Dains (技术总监)
验证结果: □ 通过  □ 未通过  □ 有条件通过
```

| 条件 | 结果 | 备注 |
|------|------|------|
| roles.kernel policy layer 正确实现 | ☐ | |
| 危险命令Patterns统一 | ☐ | |
| retired KernelOne policy package 不可回归 | ☐ | |
| YAML配置删除 | ☐ | |
| TOOL_NAME_ALIASES正确使用 | ☐ | |

**验证签字**: _________________

---

*卡片状态*: 待验证
*最后更新*: 2026-03-28
