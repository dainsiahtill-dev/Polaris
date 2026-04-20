# Team Delta: verify/orchestrator.py 重构蓝图

## 目标文件
`polaris/infrastructure/accel/verify/orchestrator.py` (2679行)

## 架构分析

### 当前问题
1. **验证流程混杂**: 编排、报告生成、门禁检查混在一起
2. **CLI入口耦合**: CLI参数解析与业务逻辑耦合
3. **报告格式化内嵌**: 多种报告格式在同一文件

### 拆分方案

```
polaris/infrastructure/accel/verify/
├── orchestrator.py              # Facade (50行)
├── verify/
│   ├── __init__.py
│   ├── core.py                  # VerifyOrchestrator核心 (400行)
│   ├── report_generator.py      # 报告生成 (400行)
│   ├── gate_checker.py          # 门禁检查 (350行)
│   ├── formatters.py            # 格式化器 (300行)
│   └── cli.py                   # CLI入口 (200行)
```

### 核心契约

```python
# core.py
@dataclass(frozen=True, slots=True)
class VerifyConfig:
    """验证配置。"""
    workspace: str
    output_format: str  # "json" | "markdown" | "html"
    fail_fast: bool
    parallel_jobs: int

class VerifyOrchestrator:
    """验证编排器。"""

    __slots__ = ('_config', '_checkers', '_reporter')

    async def run_verification(
        self,
        targets: list[str],
    ) -> VerifyResult:
        """执行验证流程。"""
        ...

# gate_checker.py
class GateChecker:
    """门禁检查器。"""

    def check(self, result: VerifyResult) -> GateDecision:
        """检查是否通过门禁。"""
        ...
```

---

**Team Lead**: _________________
**Date**: 2025-03-31