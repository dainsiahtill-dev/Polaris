# AGI骨架进化 Phase 0: 紧急修复计划

**版本**: v1.0.0
**日期**: 2026-04-06
**状态**: 待执行
**工期**: 2周
**人力**: 2人
**目标评分**: 60/100 (从56/100提升)

---

## 一、任务总览

| 任务 | 优先级 | 工作量 | 当前状态 |
|------|--------|--------|----------|
| 140+处`except:pass`全局异常logging | P0 | 12h | TOP6 Fix3未完成 |
| 7个Cell空契约迁移 | P0 | 16h | director.*迁移未完成 |
| execute_command白名单 | P0 | 8h | 无过滤 |
| 文本协议提示词修复 | P0 | 4h | 已识别待修复 |

---

## 二、任务详情

### 2.1 任务T0-1: 全局异常Logging修复

**问题**: 代码库中存在140+处`except:pass`或`except Exception: pass`，导致错误静默丢失。

**目标文件**:
```
polaris/infrastructure/storage/adapter.py
polaris/kernelone/events/message_bus.py
polaris/kernelone/workflow/activity_runner.py
polaris/cells/director/runtime/internal/worker_pool_service.py
... (共140+文件)
```

**修复模式**:
```python
# ❌ 错误模式
except Exception:
    pass

# ✅ 正确模式
except Exception as e:
    logger.exception("Failed to process item %s: %s", item_id, e)
```

**执行步骤**:
1. 使用grep定位所有`except:`和`except Exception:`模式
2. 按文件分组，每组作为一个PR
3. 每处修复需保留原有异常类型信息
4. 添加上下文信息(logging with extra context)

**验收标准**:
- [ ] 0处裸`except:`
- [ ] 0处`except Exception: pass`
- [ ] 所有异常路径有`logger.exception()`或`logger.error()`
- [ ] ruff check --select=E,F 零警告

---

### 2.2 任务T0-2: Cell空契约迁移

**问题**: 7个Cell的公开契约为空，无法作为稳定边界。

**空契约Cell列表**:

| Cell | 契约路径 | 当前状态 |
|------|----------|----------|
| director.planning | `polaris/cells/director/planning/public/contracts.py` | 全部为空 |
| director.tasking | `polaris/cells/director/tasking/public/contracts.py` | 全部为空 |
| director.runtime | `polaris/cells/director/runtime/public/contracts.py` | 全部为空 |
| director.delivery | `polaris/cells/director/delivery/public/contracts.py` | 全部为空 |
| roles.host | `polaris/cells/roles/host/public/contracts.py` | cell.yaml为空但实现存在 |
| orchestration.workflow_engine | `polaris/cells/orchestration/workflow_engine/public/contracts.py` | 全部为空 |
| orchestration.workflow_activity | `polaris/cells/orchestration/workflow_activity/public/contracts.py` | 全部为空 |

**迁移规范**:

```python
# ✅ 标准契约格式
from dataclasses import dataclass
from typing import FrozenSet

@dataclass(frozen=True)
class SomeCommandV1:
    """命令描述"""
    required_field: str
    optional_field: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_field", _require_non_empty("required_field", self.required_field))

@dataclass(frozen=True)
class SomeQueryV1:
    """查询描述"""
    query_params: tuple[str, ...]

@dataclass(frozen=True)
class SomeEventV1:
    """事件描述"""
    event_type: str
    payload: dict[str, object]
```

**执行步骤**:
1. 读取每个Cell的内部实现
2. 识别公开API(commands/queries/events)
3. 创建对应的contracts.py
4. 更新cell.yaml的public_contracts
5. 添加__init__.py导出
6. 编写迁移测试

**验收标准**:
- [ ] 7个Cell契约文件创建完成
- [ ] 每个契约有完整的__post_init__验证
- [ ] cell.yaml更新public_contracts
- [ ] pytest tests/director/planning tests/director/tasking tests/director/runtime tests/director/delivery tests/roles/host tests/orchestration/workflow_engine tests/orchestration/workflow_activity 全部通过

---

### 2.3 任务T0-3: execute_command白名单细化

**问题**: `execute_command`工具无命令白名单，可执行任意shell命令。

**当前问题代码**:
```python
# polaris/kernelone/tools/contracts.py
"execute_command": {
    "aliases": ["run_command", "shell", "cmd"],
    "description": "Execute a shell command",
    # 无白名单!
}
```

**修复方案**:

```python
# polaris/kernelone/tool_execution/constants.py

# 开发工具白名单
ALLOWED_EXECUTION_COMMANDS: FrozenSet[str] = frozenset({
    # Git commands
    "git", "git clone", "git pull", "git push", "git fetch",
    "git checkout", "git branch", "git status", "git log",
    "git diff", "git merge", "git rebase", "git stash",

    # Package managers
    "npm", "npm install", "npm run", "npm test", "npm build",
    "pip", "pip install", "pip freeze", "pip list",
    "poetry", "poetry install", "poetry run",

    # Code quality
    "ruff", "ruff check", "ruff format",
    "mypy", "pytest", "python -m pytest",
    "tsc", "typescript", "eslint",

    # File operations (restricted)
    "ls", "pwd", "cd",  # 仅内嵌使用，不直接暴露
})

# 危险命令黑名单
BLOCKED_COMMAND_PATTERNS: tuple[str, ...] = (
    r":\(\)\s*\{.*:.*\|.*:.*&.*\}",  # Fork bomb
    r"rm\s+-rf\s+/",                    # 递归删除根目录
    r"dd\s+if=.*of=/dev/",              # 磁盘写入
    r"mkfs",                            # 文件系统格式化
    r":(){ :|:& };:",                   # Fork bomb变体
)

class CommandWhitelistValidator:
    """命令白名单验证器"""

    @classmethod
    def validate(cls, command: str) -> CommandValidationResult:
        # 检查黑名单模式
        for pattern in BLOCKED_COMMAND_PATTERNS:
            if re.search(pattern, command):
                return CommandValidationResult(
                    allowed=False,
                    reason=f"Command matches blocked pattern: {pattern}"
                )

        # 解析命令(处理 python -m pytest 格式)
        parts = command.split()
        base_cmd = parts[0] if parts else ""

        # 检查白名单
        if base_cmd not in ALLOWED_EXECUTION_COMMANDS:
            # 允许的参数变体
            if command not in ALLOWED_EXECUTION_COMMANDS:
                return CommandValidationResult(
                    allowed=False,
                    reason=f"Command '{base_cmd}' not in whitelist"
                )

        return CommandValidationResult(allowed=True)
```

**执行步骤**:
1. 创建`CommandWhitelistValidator`类
2. 更新`execute_command`工具定义
3. 在`ToolExecutorPort`添加验证调用
4. 添加集成测试
5. 更新文档

**验收标准**:
- [ ] `execute_command`使用白名单验证
- [ ] 危险命令被阻止并记录
- [ ] 常用开发命令(npm, pytest, ruff等)正常工作
- [ ] 集成测试覆盖白名单场景

---

### 2.4 任务T0-4: 文本协议提示词修复

**问题**: LLM提示词描述XML工具格式但实际使用禁用状态。

**问题代码**:
```python
# polaris/cells/llm/tool_runtime/internal/role_integrations.py
async def process_llm_response(self, response, ...):
    return _disabled_text_tool_protocol_result(response, role=...)
```

**修复方案**:

```python
# 方案: 更新提示词移除XML格式描述

CHIEF_ENGINEER_TOOL_PROMPT = """\
## 工具使用

你可以通过调用工具来完成任务。每次调用返回结果后，
系统会自动将结果注入上下文，你可以继续调用下一个工具。

可用工具:
{tool_descriptions}

## 响应格式

当需要调用工具时，使用以下JSON格式:
{{
    "tool_calls": [
        {{
            "name": "tool_name",
            "arguments": {{ "arg1": "value1", "arg2": "value2" }}
        }}
    ]
}}

不需要工具时，直接返回你的分析和建议。
"""
```

**执行步骤**:
1. 检查所有`ROLE_PROMPT_TEMPLATES`
2. 移除XML工具格式描述
3. 更新为JSON格式说明
4. 验证`process_llm_response`正确处理

**验收标准**:
- [ ] 提示词中无`[TOOL_NAME]` XML标签描述
- [ ] 提示词正确描述JSON工具格式
- [ ] LLM响应正确路由到工具执行

---

## 三、执行计划

### Week 1

| Day | 任务 | 负责人 |
|-----|------|--------|
| Mon | T0-1: 定位except:pass文件 | Engineer-1 |
| Tue | T0-1: 修复前50处 | Engineer-1 |
| Wed | T0-1: 修复剩余90处 | Engineer-1 |
| Thu | T0-2: director.planning契约 | Engineer-2 |
| Fri | T0-2: director.tasking契约 | Engineer-2 |

### Week 2

| Day | 任务 | 负责人 |
|-----|------|--------|
| Mon | T0-2: director.runtime契约 | Engineer-1 |
| Tue | T0-2: director.delivery契约 | Engineer-2 |
| Wed | T0-3: 白名单验证器 | Engineer-1 |
| Thu | T0-4: 提示词修复 | Engineer-2 |
| Fri | 验收与测试 | Both |

---

## 四、风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| 修复引入新bug | 高 | 中 | 完整测试套件覆盖 |
| Cell契约迁移破坏现有功能 | 高 | 低 | 向后兼容契约 |
| 白名单过于严格 | 中 | 中 | 提供配置开关 |
| 提示词修改影响LLM行为 | 中 | 低 | A/B测试验证 |

---

## 五、验收清单

```markdown
## Phase 0 验收检查单

### 代码质量
- [ ] 0处裸`except:`
- [ ] 0处`except Exception: pass`
- [ ] ruff check --select=E,F 零警告
- [ ] ruff format 已格式化
- [ ] mypy --strict 零警告

### Cell契约迁移
- [ ] director.planning/public/contracts.py 完整
- [ ] director.tasking/public/contracts.py 完整
- [ ] director.runtime/public/contracts.py 完整
- [ ] director.delivery/public/contracts.py 完整
- [ ] roles.host/public/contracts.py 完整
- [ ] orchestration.workflow_engine/public/contracts.py 完整
- [ ] orchestration.workflow_activity/public/contracts.py 完整
- [ ] cell.yaml 已更新

### 安全
- [ ] execute_command 白名单验证启用
- [ ] 危险命令被阻止
- [ ] 常用开发命令正常工作

### 提示词
- [ ] 无XML工具格式描述
- [ ] JSON格式正确描述

### 测试
- [ ] pytest 100% 通过
- [ ] 新增契约测试覆盖
```

---

## 六、关键文件索引

```
polaris/kernelone/
├── errors.py                                    # 异常层级定义
├── tool_execution/
│   ├── constants.py                            # 命令白名单(待修改)
│   └── validators.py                           # 验证器
└── events/
    └── message_bus.py                          # 异常日志(待修改)

polaris/cells/director/
├── planning/public/contracts.py               # 待创建
├── tasking/public/contracts.py                # 待创建
├── runtime/public/contracts.py                # 待创建
└── delivery/public/contracts.py              # 待创建

polaris/cells/roles/host/public/contracts.py   # 待创建

polaris/cells/orchestration/
├── workflow_engine/public/contracts.py        # 待创建
└── workflow_activity/public/contracts.py     # 待创建
```
