# 加速执行计划：全仓代码治理与 ACGA 2.0 落地

**版本**: 2.0  
**日期**: 2026-03-28  
**状态**: 🔴 **启动中**  
**负责人**: 10人 Python 架构与代码治理实验室  
**优先级**: P0  

---

## 📋 执行摘要

基于全仓代码审计报告（959 个 Mypy 错误、206 个异常处理问题、5/6 CLI 入口点老化），本计划组织**多支高级 Python 团队并行作战**，加速所有治理计划落地。

---

## 🎯 团队组织架构

### 主力团队 (Team Alpha) - 核心治理

| 角色 | 专家 | 职责 | 交付物 |
|------|------|------|--------|
| CTO - 战略决策者 | @CTO | 技术选型，确保超前扩展性 | 技术路线图 |
| Principal Engineer | @PE | SOLID/DRY，解耦复杂模块 | 架构设计文档 |
| Security Lead | @Security | 审计注入、敏感信息 | 安全审计报告 |
| Refactoring Guru | @Refactor | Pythonic 重构 | 重构代码 |
| Typing Specialist | @Typing | mypy 严格检查 | 类型注解 |
| QA Automation | @QA | Pytest 架构，边缘案例 | 测试套件 |
| Documentation Lead | @Docs | Docstrings，可读性 | 文档 |
| Framework SME | @Framework | FastAPI/SQLAlchemy 底层 | 框架适配 |
| Code Auditor | @Auditor | PEP 8 扫描 | 代码审查报告 |
| DevOps Integrator | @DevOps | CI/CD，依赖管理 | 自动化门禁 |

### 扩展团队 (Team Beta) - 专项攻坚

| 角色 | 专家 | 职责 | 交付物 |
|------|------|------|--------|
| Typing Specialist (x2) | @Typing-2 | 类型注解攻坚 | Mypy 错误减少 500+ |
| QA Automation (x2) | @QA-2 | 测试覆盖率提升 | 80%+ 覆盖率 |
| Code Auditor (x2) | @Auditor-2 | PEP 8 修复 | 0 Ruff 错误 |
| DevOps Integrator (x2) | @DevOps-2 | CI/CD 门禁 | 自动化流水线 |

### 协同机制

- **每日站会**：15:00（北京时间）
- **周报同步**：每周五 18:00
- **问题升级**：阻塞问题 2 小时内升级至 CTO

---

## 🚀 加速执行计划

### Phase 1：紧急修复（1 周）

#### 任务 1.1：CLI 入口点统一（P1）

**负责人**: @DevOps + @Docs  
**目标**: 6/6 CLI 入口点统一到 `polaris.delivery.cli`

**执行步骤**:

```bash
# 1. 更新文档
docs/governance/decisions/adr-0068-cli-entrypoint-unification.md

# 2. 创建统一入口垫片
polaris/delivery/cli/__main__.py

# 3. 添加向后兼容层（可选）
polaris/delivery/cli/compat.py  # 临时垫片，标注弃用
```

**验收标准**:

| 指标 | 目标 | 验证方式 |
|------|------|----------|
| CLI 入口一致性 | 6/6 | `grep -r "scripts/pm/cli.py" polaris/` 应无输出 |
| 文档更新 | 100% | `docs/governance/decisions/` 包含 ADR-0068 |
| 向后兼容 | 可选 | `compat.py` 标注 `@deprecated` |

---

#### 任务 1.2：关键文件类型注解补充（P0）

**负责人**: @Typing + @QA  
**目标**: Mypy 错误减少 200（从 959 → 759）

**高风险文件清单**:

| 文件 | 错误数 | 优先级 | 负责人 |
|------|--------|--------|--------|
| `polaris/delivery/http/routers/role_session.py` | 23 | P0 | @Typing |
| `polaris/delivery/http/routers/role_chat.py` | 18 | P0 | @Typing |
| `polaris/delivery/http/routers/system.py` | 15 | P0 | @QA |
| `docs/governance/ci/scripts/run_catalog_governance_gate.py` | 14 | P0 | @QA |
| `polaris/delivery/cli/terminal_console.py` | 12 | P1 | @QA |
| `polaris/kernelone/tools/validators.py` | 11 | P1 | @QA |
| `polaris/delivery/http/routers/role_session.py` | 8 | P1 | @QA |
| `polaris/delivery/http/routers/role_chat.py` | 7 | P1 | @QA |

**执行策略**:

```python
# 策略 1：Optional 类型处理
# ❌ 修复前
def get_workspace_status(workspace: str) -> dict[str, Any] | None:
    ...
    return None

# ✅ 修复后
from typing import assert_type

def get_workspace_status(workspace: str) -> Result[dict[str, Any], Error]:
    """返回 Result 类型，强制调用方处理成功/失败"""
    try:
        status = _load_status(workspace)
        return Ok(status)
    except FileNotFoundError as e:
        return Err(Error(code="NOT_FOUND", message=f"Workspace not found: {workspace}"))
    except Exception as e:
        return Err(Error(code="INTERNAL", message=str(e)))
```

**验收标准**:

| 指标 | 目标 | 验证方式 |
|------|------|----------|
| Mypy 错误减少 | 200 | `mypy polaris/ --no-error-summary | grep -c 'error:'` |
| 关键文件 100% 类型覆盖 | 100% | `mypy polaris/delivery/http/routers/ --strict` |

---

#### 任务 1.3：异常处理标准化（P1）

**负责人**: @Security + @QA  
**目标**: 新增统一异常处理契约，减少异常吞噬

**执行步骤**:

```python
# 新增：polaris/kernelone/exceptions/contracts.py
"""统一异常处理契约 - 遵循 ACGA 2.0 Effect 模型"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, runtime_checkable


class ErrorSeverity(Enum):
    """错误严重等级 - 用于治理审计"""
    DEBUG = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    CRITICAL = auto()


@dataclass(frozen=True)
class Error:
    """统一错误类型 - 符合 KernelOne Effect 合约"""
    code: str
    message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    details: dict[str, Any] | None = None
    trace_id: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典 - 用于日志与传输"""
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.name,
            "details": self.details or {},
            "trace_id": self.trace_id,
            "timestamp": self.timestamp.isoformat(),
        }


@runtime_checkable
class ErrorHandler(Protocol):
    """错误处理器契约 - 用于依赖注入"""
    def handle(self, error: Error) -> None:
        ...


class LoggingErrorHandler:
    """日志错误处理器 - 基础设施层实现"""
    def __init__(self, logger: "Logger") -> None:
        self._logger = logger

    def handle(self, error: Error) -> None:
        """处理错误 - 记录日志"""
        self._logger.log(error.severity.name.lower(), error.to_dict())


class ErrorClassifier:
    """错误分类器 - 核心业务逻辑"""
    def __init__(self, handlers: list[ErrorHandler]) -> None:
        self._handlers = handlers

    def classify_and_handle(self, exception: Exception) -> Error:
        """分类并处理错误 - 统一入口"""
        error = self._classify(exception)
        for handler in self._handlers:
            handler.handle(error)
        return error

    def _classify(self, exception: Exception) -> Error:
        """分类错误 - 根据类型映射到标准错误码"""
        if isinstance(exception, FileNotFoundError):
            return Error(code="NOT_FOUND", message=str(exception), severity=ErrorSeverity.DEBUG)
        elif isinstance(exception, TimeoutError):
            return Error(code="TIMEOUT", message=str(exception), severity=ErrorSeverity.WARNING)
        elif isinstance(exception, PermissionError):
            return Error(code="PERMISSION_DENIED", message=str(exception), severity=ErrorSeverity.ERROR)
        else:
            return Error(code="INTERNAL", message=str(exception), severity=ErrorSeverity.CRITICAL)
```

**验收标准**:

| 指标 | 目标 | 验证方式 |
|------|------|----------|
| 新增 ErrorClassifier | 1 个 | `grep -r "class ErrorClassifier" polaris/` |
| 异常处理测试 | 20+ | `pytest tests/kernelone/exceptions/ -v` |

---

### Phase 2：架构增强（3 周）

#### 任务 2.1：Result Pattern 全面推广（P0）

**负责人**: @Refactor + @QA  
**目标**: 函数式错误处理，减少运行时错误

**推广策略**:

```python
# 核心模式
from typing import TypeVar, Generic

T = TypeVar("T")
E = TypeVar("E", bound=Error)


class Result(Generic[T, E]):
    """Result 类型 - 函数式错误处理"""

    def is_ok(self) -> bool:
        ...

    def is_err(self) -> bool:
        ...

    def unwrap(self) -> T:
        ...

    def unwrap_err(self) -> E:
        ...

    def map(self, f: Callable[[T], U]) -> "Result[U, E]":
        ...

    def and_then(self, f: Callable[[T], "Result[U, E]"]) -> "Result[U, E]":
        ...

    def to_dict(self) -> dict[str, Any]:
        ...


def Ok(value: T) -> Result[T, E]:
    """成功结果"""
    return OkResult(value)


def Err(error: E) -> Result[T, E]:
    """错误结果"""
    return ErrResult(error)
```

**验收标准**:

| 指标 | 目标 | 验证方式 |
|------|------|----------|
| Result Pattern 覆盖 | 50% 核心模块 | `grep -r "class Result" polaris/` |
| 测试覆盖率 | 90%+ | `pytest tests/kernelone/exceptions/ --cov` |

---

#### 任务 2.2：统一异常处理契约落地（P1）

**负责人**: @Security + @DevOps  
**目标**: 符合 ACGA 2.0 Effect 模型

**执行步骤**:

1. **新增契约层**：`polaris/kernelone/exceptions/contracts.py`
2. **基础设施适配**：`polaris/infrastructure/exceptions/handlers.py`
3. **业务层集成**：各 Cell 迁移到统一异常处理

**验收标准**:

| 指标 | 目标 | 验证方式 |
|------|------|----------|
| 统一异常处理契约 | 100% Cell 覆盖 | `grep -r "from.*exceptions.contracts import Error" polaris/` |
| Effect 模型符合 | 100% | `docs/governance/decisions/adr-0069-unified-error-handling.md` |

---

#### 任务 2.3：Mypy 错误减少至 ≤300（P0）

**负责人**: @Typing (x2)  
**目标**: Mypy 错误从 759 → 300

**执行策略**:

| 策略 | 执行 | 预期效果 |
|------|------|---------|
| 类型别名统一 | `PathStr = str \| Path` | 减少 100 错误 |
| Optional 处理 | `Result[T, Error]` 替代 `T \| None` | 减少 150 错误 |
| 抽象类修复 | 实现抽象方法 | 减少 50 错误 |

**验收标准**:

| 指标 | 目标 | 验证方式 |
|------|------|----------|
| Mypy 错误 | ≤300 | `mypy polaris/ --no-error-summary | grep -c 'error:'` |

---

### Phase 3：质量收敛（6 周）

#### 任务 3.1：Mypy 错误 ≤50（P0）

**负责人**: @Typing + @QA  
**目标**: 类型安全收敛

**执行策略**:

| 策略 | 执行 | 预期效果 |
|------|------|---------|
| 严格模式 | `mypy --strict` | 减少 200 错误 |
| 类型别名 | `TypeAlias` | 减少 50 错误 |
| 单元测试 | `pytest --mypy` | 减少 50 错误 |

**验收标准**:

| 指标 | 目标 | 验证方式 |
|------|------|----------|
| Mypy 错误 | ≤50 | `mypy polaris/ --strict | grep -c 'error:'` |
| 类型覆盖 | 95%+ | `mypy polaris/ --strict --show-error-codes` |

---

#### 任务 3.2：100% 类型覆盖核心模块（P0）

**负责人**: @Typing  
**目标**: 核心模块 100% 类型覆盖

**核心模块清单**:

| 模块 | 文件数 | 目标 |
|------|--------|------|
| `kernelone/llm/` | 50 | 100% |
| `kernelone/exceptions/` | 10 | 100% |
| `delivery/http/v2/` | 30 | 100% |
| `delivery/cli/` | 20 | 100% |

**验收标准**:

| 指标 | 目标 | 验证方式 |
|------|------|----------|
| 核心模块类型覆盖 | 100% | `mypy polaris/kernelone/llm/ --strict` |

---

#### 任务 3.3：0 个异常吞噬（P1）

**负责人**: @Security + @QA  
**目标**: 异常处理标准化

**执行策略**:

| 策略 | 执行 | 预期效果 |
|------|------|---------|
| Ruff 门禁 | `ruff check . --select B902` | 减少 50 个 `pass except` |
| 代码审查 | 每周 2 次 | 减少 30 个 `pass except` |
| 自动化测试 | `pytest tests/` | 减少 20 个 `pass except` |

**验收标准**:

| 指标 | 目标 | 验证方式 |
|------|------|----------|
| 独立 pass 行 | 0 | `grep -r "except.*:" polaris/ \| grep "pass" \| wc -l` |

---

## 📊 执行时间线

```
Week 1 (Phase 1 - 紧急修复):
├── Day 1-2: CLI 入口点统一
├── Day 3-5: 关键文件类型注解补充 (Mypy -200)
└── Day 6-7: 异常处理标准化 (ErrorClassifier)

Week 2-4 (Phase 2 - 架构增强):
├── Week 2: Result Pattern 推广 (Mypy -459 → 300)
├── Week 3: 统一异常处理契约落地
└── Week 4: Mypy -300 → 150

Week 5-10 (Phase 3 - 质量收敛):
├── Week 5-6: Mypy -150 → 50
├── Week 7-8: 100% 类型覆盖核心模块
└── Week 9-10: 0 个异常吞噬

Week 11 (最终验收):
├── Day 1-2: 全仓 Mypy 检查
├── Day 3-4: 全仓测试覆盖
└── Day 5: 最终报告
```

---

## 🎯 验收标准

| 指标 | 当前 | 目标（1 个月） | 目标（3 个月） |
|------|------|---------------|---------------|
| Mypy 错误 | 959 | ≤300 | ≤50 |
| 异常吞噬 | 53 | ≤10 | 0 |
| CLI 一致性 | 5/6 | 6/6 | 6/6 |
| 测试通过率 | 99.9% | 100% | 100% |
| 类型覆盖 | 60% | 80% | 95% |

---

## 📈 质量趋势监控

### 每日监控

| 指标 | 监控命令 | 阈值 |
|------|---------|------|
| Mypy 错误 | `mypy polaris/ --no-error-summary \| grep -c 'error:'` | ≤300 |
| 异常吞噬 | `grep -r "except.*:" polaris/ \| grep "pass" \| wc -l` | ≤10 |
| CLI 一致性 | `grep -r "scripts/pm/cli.py" polaris/ \| wc -l` | 0 |

### 每周报告

| 报告 | 生成命令 | 预期输出 |
|------|---------|---------|
| Mypy 进度 | `python docs/scripts/mypy_progress.py` | 错误减少趋势图 |
| 异常处理 | `python docs/scripts/exception_handling_audit.py` | `pass except` 减少趋势图 |
| CLI 入口 | `python docs/scripts/cli_entrypoint_audit.py` | 一致性报告 |

---

## 🛠️ 自动化门禁

### CI/CD 门禁

```yaml
# docs/governance/ci/fitness-rules.yaml
# 新增类型安全门禁（2026-03-28）

- id: type-safety-mypy
  description: "Mypy 错误数必须 ≤ 50"
  type: static_analysis
  command: "python -m mypy polaris/ --no-error-summary | grep -c 'error:' || echo 0"
  threshold: 50
  severity: blocker
  status: draft

- id: exception-handling-no-pass
  description: "禁止异常吞噬（pass in except）"
  type: static_analysis
  command: "grep -r 'except.*:' polaris/ | grep -v 'raise' | grep 'pass' || echo 0"
  threshold: 0
  severity: blocker
  status: draft

- id: cli-entrypoint-unified
  description: "CLI 入口点必须统一到 polaris.delivery.cli"
  type: structural
  command: "grep -r 'scripts/pm/cli.py' polaris/ || echo 0"
  threshold: 0
  severity: blocker
  status: draft
```

---

## 📋 交付物清单

| 交付物 | 位置 | 负责人 | 截止日期 |
|--------|------|--------|---------|
| CLI 入口点统一 ADR | `docs/governance/decisions/adr-0068-cli-entrypoint-unification.md` | @DevOps | Week 1 |
| 统一异常处理契约 | `polaris/kernelone/exceptions/contracts.py` | @Security | Week 1 |
| ErrorClassifier | `polaris/kernelone/exceptions/error_classifier.py` | @QA | Week 1 |
| Result Pattern | `polaris/kernelone/exceptions/result.py` | @Refactor | Week 2 |
| Context Adapter | `polaris/kernelone/benchmark/adapters/context_adapter.py` | @QA | Week 1 |
| Fixture Mapper | `polaris/kernelone/benchmark/adapters/context_fixture_mapper.py` | @QA | Week 1 |
| 测试套件 | `polaris/kernelone/benchmark/tests/test_context_adapter.py` | @QA | Week 2 |
| CI/CD 门禁 | `docs/governance/ci/fitness-rules.yaml` | @DevOps | Week 2 |

---

## 🎉 总结

**优势**:
- ✅ 多支高级 Python 团队并行作战
- ✅ 10 位顶级专家分工明确
- ✅ 清晰的执行时间线与验收标准

**风险**:
- 🔴 任务密集，需严格每日站会
- 🔴 依赖项多，需及时升级阻塞问题

**建议**:
1. 立即启动 Phase 1（1 周）紧急修复
2. 建立类型安全门禁（Mypy ≤50）
3. 统一异常处理契约（符合 ACGA 2.0 Effect 模型）

---

**报告生成时间**: 2026-03-28  
**报告版本**: v2.0  
**审计团队**: 10 位顶级 Python 架构与代码治理实验室专家  
**扩展团队**: 6 位高级 Python 专家（@Typing-2, @QA-2, @Auditor-2, @DevOps-2）
