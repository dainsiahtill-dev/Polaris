# Team Alpha: director_adapter.py 重构蓝图

## 目标文件
`polaris/cells/roles/adapters/internal/director_adapter.py` (3533行)

## 架构分析

### 当前问题
1. **职责混乱**: 工具安全检查、命令注入防护、执行逻辑、对话管理、状态追踪混在一起
2. **测试困难**: 3500+行单文件难以单元测试
3. **循环依赖风险**: 多个内部函数相互调用

### 职责拆分矩阵

| 职责 | 行数 | 目标模块 |
|------|------|---------|
| 命令注入防护 | ~300 | `security.py` |
| 工具安全检查 | ~200 | `security.py` |
| 执行逻辑 | ~800 | `execution.py` |
| 对话管理 | ~500 | `dialogue.py` |
| 状态追踪 | ~400 | `state_tracking.py` |
| 辅助函数 | ~300 | `helpers.py` |
| 核心适配器 | ~500 | `director_adapter.py` (保留) |

## 拆分方案

### 目标结构
```
polaris/cells/roles/adapters/internal/
├── director_adapter.py          # Facade (50行)
├── director/
│   ├── __init__.py              # 导出聚合
│   ├── adapter.py               # 核心DirectorAdapter类 (500行)
│   ├── security.py              # 安全检查 (350行)
│   ├── execution.py             # 执行逻辑 (500行)
│   ├── dialogue.py              # 对话管理 (400行)
│   ├── state_tracking.py        # 状态追踪 (350行)
│   └── helpers.py               # 辅助函数 (300行)
```

### 模块契约

#### `security.py`
```python
"""命令注入防护与工具安全检查模块。"""

from dataclasses import dataclass
from typing import Protocol, Set, Callable

@dataclass(frozen=True, slots=True)
class SecurityPolicy:
    """不可变安全策略配置。"""
    allowed_commands: frozenset[str]
    shell_blocked: bool
    injection_patterns: tuple[str, ...]

class CommandValidator:
    """命令验证器 - 无状态，线程安全。"""

    __slots__ = ('_policy',)

    def __init__(self, policy: SecurityPolicy) -> None:
        self._policy = policy

    def validate(self, command: str) -> ValidationResult:
        """验证命令是否安全可执行。

        Args:
            command: 待验证的命令字符串

        Returns:
            ValidationResult 包含 is_safe, reason, sanitized_command

        Raises:
            CommandInjectionBlocked: 检测到注入攻击时抛出
        """
        ...

    def is_allowed(self, command: str) -> bool:
        """检查命令是否在白名单中。"""
        ...

class CommandInjectionBlocked(Exception):
    """命令注入被阻止时抛出。"""

    __slots__ = ('command', 'reason')

    def __init__(self, command: str, reason: str) -> None:
        self.command = command
        self.reason = reason
        super().__init__(f"Command injection blocked: {reason}")
```

#### `execution.py`
```python
"""Director 执行逻辑模块。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator

@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """执行上下文 - 不可变。"""
    workspace: str
    session_id: str
    timeout_seconds: float
    max_retries: int

@dataclass(slots=True)
class ExecutionResult:
    """执行结果。"""
    success: bool
    output: str
    error: str | None
    duration_ms: int
    artifacts: tuple[str, ...]

class ExecutionBackend(ABC):
    """执行后端抽象基类。"""

    @abstractmethod
    async def execute(
        self,
        command: str,
        context: ExecutionContext
    ) -> ExecutionResult:
        """执行命令并返回结果。"""
        ...

    @abstractmethod
    async def execute_stream(
        self,
        command: str,
        context: ExecutionContext
    ) -> AsyncIterator[str]:
        """流式执行命令。"""
        ...

class DirectorExecutor:
    """Director 执行器 - 组合模式。"""

    __slots__ = ('_backend', '_validator', '_context')

    def __init__(
        self,
        backend: ExecutionBackend,
        validator: CommandValidator,
        context: ExecutionContext,
    ) -> None:
        self._backend = backend
        self._validator = validator
        self._context = context

    async def run_tool(
        self,
        tool_name: str,
        args: dict[str, Any]
    ) -> ExecutionResult:
        """运行工具并返回结果。"""
        ...
```

#### `dialogue.py`
```python
"""Director 对话管理模块。"""

import re
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class DialoguePattern:
    """对话模式配置。"""
    followup_patterns: tuple[re.Pattern[str], ...]
    negative_patterns: tuple[re.Pattern[str], ...]
    constraint_patterns: tuple[re.Pattern[str], ...]

class DialogueClassifier:
    """对话分类器。"""

    __slots__ = ('_patterns',)

    def __init__(self, patterns: DialoguePattern) -> None:
        self._patterns = patterns

    def extract_followup_action(self, text: str) -> str:
        """从assistant消息提取follow-up动作。"""
        ...

    def is_negative_response(self, text: str) -> bool:
        """检测否定响应。"""
        ...

    def classify_intent(self, text: str) -> str:
        """分类用户意图。"""
        ...
```

## 实现步骤

### Step 1: 创建目录结构
```bash
mkdir -p polaris/cells/roles/adapters/internal/director
touch polaris/cells/roles/adapters/internal/director/__init__.py
```

### Step 2: 提取 Security 模块
```python
# 1. 创建 security.py
# 2. 迁移 CommandInjectionBlocked, _load_tooling_security()
# 3. 创建 CommandValidator 类
# 4. 添加类型注解和文档字符串
```

### Step 3: 提取 Execution 模块
```python
# 1. 创建 execution.py
# 2. 迁移执行相关方法
# 3. 定义ExecutionContext, ExecutionResult
# 4. 实现 ExecutionBackend 抽象类
```

### Step 4: 提取 Dialogue 模块
```python
# 1. 创建 dialogue.py
# 2. 迁移 _extract_assistant_followup_action()
# 3. 迁移 _is_negative_response()
# 4. 创建 DialogueClassifier 类
```

### Step 5: 创建 Facade
```python
# polaris/cells/roles/adapters/internal/director_adapter.py

"""Director 角色适配器 (Facade)。

此文件保留向后兼容性，实际实现已迁移到 director/ 子模块。
"""

from .director.adapter import DirectorAdapter
from .director.security import (
    CommandInjectionBlocked,
    CommandValidator,
    SecurityPolicy,
)
from .director.execution import (
    ExecutionContext,
    ExecutionResult,
    DirectorExecutor,
)

__all__ = [
    # 核心类
    "DirectorAdapter",
    # 安全模块
    "CommandInjectionBlocked",
    "CommandValidator",
    "SecurityPolicy",
    # 执行模块
    "ExecutionContext",
    "ExecutionResult",
    "DirectorExecutor",
]
```

## 测试策略

### 单元测试结构
```
polaris/cells/roles/adapters/internal/director/tests/
├── test_security.py          # CommandValidator 测试
├── test_execution.py         # DirectorExecutor 测试
├── test_dialogue.py          # DialogueClassifier 测试
└── test_adapter.py           # 集成测试
```

### 关键测试用例
```python
# test_security.py
class TestCommandValidator:
    def test_blocks_shell_injection(self) -> None:
        """验证 shell注入被阻止。"""
        ...

    def test_allows_whitelisted_commands(self) -> None:
        """验证白名单命令通过。"""
        ...

    @pytest.mark.parametrize("malicious", [
        "rm -rf /",
        "$(cat /etc/passwd)",
        "; drop table users; --",
    ])
    def test_blocks_malicious_patterns(self, malicious: str) -> None:
        """参数化测试恶意模式。"""
        ...
```

## 验收标准

- [ ] 所有模块 < 500行
- [ ] mypy --strict 通过
- [ ] pytest覆盖率 > 80%
- [ ] ruff check/format 通过
- [ ] 原测试全部通过
- [ ] Facade导入向后兼容

## 时间表

| 阶段 | 时间 | 交付物 |
|------|------|--------|
| 设计 | Day 1-2 | 详细设计文档 |
| 实现 | Day 3-7 | 拆分后模块代码 |
| 测试 | Day 8-10 | 单元测试 + 集成测试 |
| 验收 | Day 11-12 | Code Review + 合并 |

---

**Team Lead**: _________________
**Reviewer**: _________________
**Date**: 2025-03-31