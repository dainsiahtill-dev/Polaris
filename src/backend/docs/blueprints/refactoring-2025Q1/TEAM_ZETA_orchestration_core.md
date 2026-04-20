# Team Zeta: orchestration_core.py 重构蓝图

## 目标文件
`polaris/delivery/cli/pm/orchestration_core.py` (2043行)

## 架构分析

### 当前问题
1. **文档生成流水线内嵌**: 大量文档生成逻辑
2. **蓝图分析混杂**: 模块演进、架构分析混在一起
3. **辅助函数过多**: 40+ 个辅助函数

### 拆分方案

```
polaris/delivery/cli/pm/
├── orchestration_core.py        # Facade (50行)
├── orchestration/
│   ├── __init__.py
│   ├── core.py                  # OrchestrationCore核心 (300行)
│   ├── docs_pipeline.py         # 文档生成流水线 (400行)
│   ├── blueprint_analysis.py    # 蓝图分析 (400行)
│   ├── module_evolution.py      # 模块演进 (350行)
│   └── helpers.py               # 辅助函数 (300行)
```

### 核心契约

```python
# docs_pipeline.py
class DocsPipeline:
    """文档生成流水线。"""

    __slots__ = ('_workspace', '_config')

    def generate_architect_docs(
        self,
        directive: str,
    ) -> list[str]:
        """生成架构师文档。"""
        ...

# blueprint_analysis.py
class BlueprintAnalyzer:
    """蓝图分析器。"""

    def analyze(
        self,
        files: dict[str, dict[str, Any]],
    ) -> ProjectBlueprint:
        """分析项目蓝图。"""
        ...
```

---

**Team Lead**: _________________
**Date**: 2025-03-31