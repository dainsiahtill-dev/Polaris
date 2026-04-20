# Team Epsilon: audit_quick.py 重构蓝图

## 目标文件
`polaris/delivery/cli/audit/audit_quick.py` (2236行)

## 架构分析

### 当前问题
1. **审计逻辑与CLI耦合**: 业务逻辑与参数解析混杂
2. **报告格式化内嵌**: 多种输出格式在同一文件
3. **文件操作分散**: 文件读写逻辑散落各处

### 拆分方案

```
polaris/delivery/cli/audit/
├── audit_quick.py               # Facade (50行)
├── audit/
│   ├── __init__.py
│   ├── auditor.py               # QuickAuditor核心 (400行)
│   ├── reporters.py             # 报告生成器 (400行)
│   ├── formatters.py            # 格式化器 (300行)
│   ├── file_ops.py              # 文件操作 (200行)
│   └── cli.py                   # CLI入口 (200行)
```

### 核心契约

```python
# auditor.py
@dataclass(frozen=True, slots=True)
class AuditConfig:
    """审计配置。"""
    workspace: str
    depth: int
    include_tests: bool
    output_format: str

@dataclass(slots=True)
class AuditResult:
    """审计结果。"""
    files_scanned: int
    issues_found: int
    critical_count: int
    warnings: tuple[str, ...]

class QuickAuditor:
    """快速审计器。"""

    __slots__ = ('_config', '_scanners')

    def audit(self, targets: list[str]) -> AuditResult:
        """执行审计。"""
        ...
```

---

**Team Lead**: _________________
**Date**: 2025-03-31