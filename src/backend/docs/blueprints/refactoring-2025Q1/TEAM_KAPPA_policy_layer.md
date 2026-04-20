# Team Kappa: policy/layer.py 重构蓝图

## 目标文件
`polaris/cells/roles/kernel/internal/policy/layer.py` (1697行)

## 架构分析

### 当前问题
1. **6个Policy类在同一文件**: Budget/Tool/Approval/Sandbox/Exploration/Redaction
2. **PolicyLayer 过大**: 主类包含大量评估逻辑
3. **辅助函数散落**: 多个 `_` 前缀函数

### 拆分方案

```
polaris/cells/roles/kernel/internal/policy/
├── layer.py                     # Facade (50行)
├── layer/
│   ├── __init__.py              # 导出聚合
│   ├── core.py                  # PolicyLayer核心 (300行)
│   ├── budget.py                # BudgetPolicy (250行)
│   ├── tool.py                  # ToolPolicy (350行)
│   ├── approval.py              # ApprovalPolicy (150行)
│   ├── sandbox.py               # SandboxPolicy (200行)
│   ├── exploration.py           # ExplorationToolPolicy (250行)
│   ├── redaction.py             # RedactionPolicy (150行)
│   └── helpers.py               # 辅助函数 (150行)
```

### 核心契约

```python
# core.py
@dataclass(slots=True)
class PolicyResult:
    """策略评估结果。"""
    allowed: bool
    stop_reason: str | None
    requires_approval: bool
    violations: tuple[PolicyViolation, ...]

class PolicyLayer:
    """策略层 - 组合多个Policy。"""

    __slots__ = (
        '_budget',
        '_tool',
        '_approval',
        '_sandbox',
        '_exploration',
        '_redaction',
    )

    def evaluate(
        self,
        tool_calls: list[CanonicalToolCall],
        budget_state: dict[str, Any],
    ) -> PolicyResult:
        """评估工具调用。"""
        results = [
            self._budget.evaluate(budget_state),
            self._tool.evaluate(tool_calls),
            self._sandbox.evaluate(tool_calls),
            ...
        ]
        return self._aggregate(results)

# budget.py
class BudgetPolicy:
    """预算策略。"""

    __slots__ = ('_config', '_spent')

    def evaluate(
        self,
        budget_state: dict[str, Any],
    ) -> PolicyResult:
        """评估预算是否超限。"""
        ...
```

---

**Team Lead**: _________________
**Date**: 2025-03-31