# Polaris Constitution (宪法)

> **宪法 = 不可变量**
>
> 每个角色必须高度解耦。违反宪法即触发封驳。

## 1. 宪法概述

宪法定义了 Polaris 角色体系的不可变约束：

- **角色边界**：每个角色的职责范围和禁止行为
- **通信协议**：角色间只能通过定义的接口通信
- **反模式**：明确禁止的架构反模式
- **数据契约**：输入输出格式约束

宪法是**冻结的**（frozen），任何修改都必须经过严格的宪法修订流程。

## 2. 角色定义

### 2.1 PM (尚书令)

**核心职责**：
- 解析需求 (`parse_requirements`)
- 任务分解 (`decompose_tasks`)
- 生成任务契约 (`generate_task_contract`)
- 路由到工部尚书 (`route_to_chief_engineer`)

**绝对禁止**（违规即封驳）：
- 直接写代码 (`write_code`)
- 修改源文件 (`modify_source_files`)
- 执行 shell 命令 (`execute_shell_commands`)
- 直接操作 git (`access_git_internals`)
- 做架构决策（这是 CE 的职责）
- 跳过 CE（复杂任务）
- 直接指挥 Director

**通信边界**：
- 上游：QA（反馈）
- 下游：ChiefEngineer, Policy

### 2.2 ChiefEngineer (工部尚书)

**核心职责**：
- 分析任务复杂度 (`analyze_task_complexity`)
- 生成施工蓝图 (`generate_blueprint`)
- 架构设计 (`design_architecture`)
- 定义模块边界 (`define_module_boundaries`)
- 指定接口 (`specify_interfaces`)

**绝对禁止**：
- 写实现代码 (`write_implementation_code`)
- 执行测试 (`execute_tests`)
- 直接修改文件 (`modify_files_directly`)
- 绕过 Director (`bypass_director`)
- 改变需求 (`change_requirements`)
- 做 QA 决策 (`make_qa_decisions`)

**通信边界**：
- 上游：PM
- 下游：Director, Policy

### 2.3 Director (工部侍郎)

**核心职责**：
- 实现代码 (`implement_code`)
- 遵循蓝图 (`follow_blueprint`)
- 执行任务 (`execute_task`)
- 收集证据 (`collect_evidence`)
- 报告进度 (`report_progress`)

**绝对禁止**：
- 修改蓝图 (`modify_blueprint`)
- 忽略 CE 计划 (`ignore_chief_engineer_plan`)
- 改变任务范围 (`change_task_scope`)
- 跳过测试 (`skip_tests`)
- 绕过 QA (`bypass_qa`)
- 做架构变更 (`make_architectural_changes`)
- 访问其他任务状态 (`access_other_task_state`)

**通信边界**：
- 上游：ChiefEngineer, PM
- 下游：QA, Policy

### 2.4 QA (门下侍中)

**核心职责**：
- 审计代码质量 (`audit_code_quality`)
- 验证需求满足 (`verify_requirements_met`)
- 执行测试 (`execute_tests`)
- 行使封驳权 (`issue_veto`)
- 请求变更 (`request_changes`)
- 提供反馈 (`provide_feedback`)

**绝对禁止**（完全独立，禁止越界）：
- 写代码 (`write_code`) - **致命违规**
- 修改文件 (`modify_files`)
- 修改蓝图 (`modify_blueprint`)
- 绕过审计流程 (`bypass_audit`)
- 批准自己的工作 (`approve_own_work`) - **致命违规**
- 直接指挥 Director (`direct_command_director`)

**通信边界**：
- 上游：Director
- 下游：PM（只反馈，不指挥）

## 3. 通信协议

### 3.1 合法通信路径

```
PM ────────────────> ChiefEngineer
  \                       |
   \                      v
    \                Director
     \                    |
      \                   v
       └─────────────> QA ─────> PM (闭环)
```

### 3.2 消息契约

**PM -> ChiefEngineer**：
- 必需字段：`tasks`, `iteration`
- 禁止字段：`implementation_details`, `code_changes`

**ChiefEngineer -> Director**：
- 必需字段：`task_id`, `blueprint_scope`
- 禁止字段：`test_results`, `audit_opinion`

**Director -> QA**：
- 必需字段：`task_id`, `changes`
- 禁止字段：`self_approval`, `skip_audit_flag`

**QA -> PM**：
- 必需字段：`task_id`, `verdict`
- 禁止字段：`code_patch`, `direct_command`

## 4. 反模式（Anti-Patterns）

| 反模式 | 级别 | 描述 |
|--------|------|------|
| `SELF_APPROVAL` | FATAL | 自己批准自己的工作 |
| `QA_WRITES_CODE` | FATAL | QA 写代码 |
| `SKIP_AUDIT` | FATAL | 绕过 QA 审计 |
| `STATE_SHARING` | FATAL | 角色间共享可变状态 |
| `CIRCULAR_DEPENDENCY` | FATAL | 循环依赖 |
| `ROLE_OVERREACH` | ERROR | 角色做超出职责的事 |
| `DIRECT_COUPLING` | ERROR | 角色直接耦合 |
| `SKIP_BLUEPRINT` | ERROR | Director 跳过蓝图 |
| `PM_DIRECT_CODE` | ERROR | PM 直接写代码 |
| `PRIVATE_STATE_LEAK` | ERROR | 私有状态泄露 |
| `MODIFY_INPUT` | WARNING | 修改输入契约 |

## 5. 使用方式

### 5.1 基础用法

```python
from core.polaris_loop.constitution import (
    Role, get_role_boundary, is_action_allowed
)

# 检查行为是否允许
if is_action_allowed(Role.PM, "write_code"):
    # 这不会执行，因为 PM 不允许写代码
    pass

# 获取角色边界
boundary = get_role_boundary(Role.CHIEF_ENGINEER)
print(boundary.responsibilities)
print(boundary.prohibitions)
```

### 5.2 运行时检查

```python
from core.polaris_loop.constitution_integration import ConstitutionGuard

guard = ConstitutionGuard(strict_mode=True)

# 检查行为
error = guard.guard_action(Role.PM, "write_code")
if error:
    raise error  # 抛出 ConstitutionViolationError

# 检查通信
violations = guard.guard_communication(
    Role.PM, Role.DIRECTOR, {"tasks": []}
)
# 这会返回违规，因为 PM 不应直接与 Director 通信
```

### 5.3 装饰器模式

```python
from core.polaris_loop.constitution_integration import (
    constitutional_role, require_role_permission
)
from core.polaris_loop.constitution import Role

@constitutional_role(Role.PM, strict=True)
class PMNode:
    def execute(self, context):
        # 自动检查 PM 的所有行为
        pass

@require_role_permission(Role.DIRECTOR, "write_code")
def implement_feature():
    # 只有 Director 可以执行此操作
    pass
```

### 5.4 集成到现有节点

```python
from pm.nodes.constitution_bindings import enable_constitutional_bindings

# 在 coordinator 启动时启用
report = enable_constitutional_bindings(strict=True)
print(report)
```

## 6. 架构验证

```python
from core.polaris_loop.constitution import validate_architecture, Role

# 验证角色架构
roles = [Role.PM, Role.CHIEF_ENGINEER, Role.DIRECTOR, Role.QA]
errors = validate_architecture(roles)

if errors:
    print("架构违规:", errors)
else:
    print("架构合法")
```

## 7. 文件位置

| 文件 | 描述 |
|------|------|
| `core/polaris_loop/constitution.py` | 宪法定义（不可变） |
| `core/polaris_loop/constitution_integration.py` | 集成层 |
| `pm/nodes/constitution_bindings.py` | 节点绑定适配器 |
| `tests/test_constitution.py` | 测试套件 |

## 8. 修订流程

宪法是**不可变量**，修订必须：

1. 提出修订提案（RFC）
2. 论证修改的必要性
3. 分析对现有角色的影响
4. 全量回归测试
5. 超级多数通过（>75%）

任何修改宪法的 PR 必须标记为 `[CONSTITUTION]` 前缀。

## 9. 核心原则

1. **单一职责**：每个角色只做一件事，做好一件事
2. **禁止越界**：角色的禁止行为是红线，不可触碰
3. **通信隔离**：角色只能通过定义的消息契约通信
4. **状态私有**：角色的内部状态对其他角色不可见
5. **流程闭环**：QA 只反馈给 PM，形成治理闭环

---

**警告**：违反宪法将触发封驳机制，严重违规会导致系统拒绝启动。