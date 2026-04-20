# 大文件重构总蓝图 2025Q1

## 概述

本蓝图指导10个专家团队并行重构 Polaris 后端超过1000行的巨型文件。

## 目标

- 将所有超过1000行的文件拆分为300-500行的模块
- 保持向后兼容性（Facade模式）
- 提升代码可维护性和测试覆盖率
- 遵循 SOLID 原则和 Pythonic 最佳实践

## 团队分配

| 团队 | 目标文件 | 行数 | 优先级 |
|------|----------|------|--------|
| Team Alpha | `director_adapter.py` | 3533 | P0 |
| Team Beta | `polaris_engine.py` | 3411 | P0 |
| Team Gamma | `llm_caller.py` | 2932 | P0 |
| Team Delta | `accel/verify/orchestrator.py` | 2679 | P0 |
| Team Epsilon | `audit_quick.py` | 2236 | P0 |
| Team Zeta | `orchestration_core.py` | 2043 | P1 |
| Team Eta | `runtime_endpoint.py` | 1812 | P1 |
| Team Theta | `kernel.py` | 1761 | P1 |
| Team Iota | `stream_executor.py` | 1724 | P1 |
| Team Kappa | `policy/layer.py` | 1697 | P1 |

## 执行时间表

```
Week 1: 深度分析 + 设计评审
Week 2-3: 核心拆分实现
Week 4: 测试验证 + 文档更新
```

## 通用原则

### 拆分模式

```
原文件 (2000+ 行)
    │
    ├─→ 核心模块 (300-500行) - 保留主类/核心逻辑
    │
    ├─→ 功能模块 (200-400行) - 按职责拆分
    │
    ├─→ 辅助模块 (100-200行) - 工具函数
    │
    └─→ Facade文件 (50-100行) - 导入重定向，向后兼容
```

### 质量门禁

```bash
# 每个团队必须通过以下检查
ruff check . --fix && ruff format .
mypy <module> --strict
pytest <module>/tests -v --cov=<module>
```

### 向后兼容策略

```python
# 原文件变为 facade
from .submodule.core import MainClass
from .submodule.helpers import helper_function

__all__ = ["MainClass", "helper_function"]

# 弃用警告（可选）
import warnings
warnings.warn(
    "直接从 <原模块> 导入已弃用，请使用 <新模块>",
    DeprecationWarning,
    stacklevel=2,
)
```

## 依赖关系图

```
┌─────────────────────────────────────────────────────────────┐
│                    P0 文件（无相互依赖）                      │
├─────────────┬─────────────┬─────────────┬─────────────┬─────┤
│ Team Alpha  │ Team Beta   │ Team Gamma  │ Team Delta  │Team │
│ director_   │ polaris │ llm_caller  │ verify/     │Epsi-│
│ adapter     │ _engine     │             │ orchestrator│lon  │
└─────────────┴─────────────┴─────────────┴─────────────┴─────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│                    P1 文件（可能依赖P0）                      │
├─────────────┬─────────────┬─────────────┬─────────────┬─────┤
│ Team Zeta   │ Team Eta    │ Team Theta  │ Team Iota   │Team │
│ orchestration│ runtime_   │ kernel      │ stream_     │Kappa│
│ _core       │ endpoint    │             │ executor    │     │
└─────────────┴─────────────┴─────────────┴─────────────┴─────┘
```

## 交付物清单

每个团队必须交付：

1. **重构蓝图** (`blueprint.md`) - 详细拆分方案
2. **模块代码** - 拆分后的模块文件
3. **Facade文件** - 向后兼容的导入重定向
4. **单元测试** - 覆盖率 > 80%
5. **迁移指南** (`migration.md`) - 上层代码迁移说明

## 风险控制

| 风险 | 缓解措施 | 责任人 |
|------|----------|--------|
| 循环依赖 | 拆分前绘制依赖图 | 各团队Lead |
| 测试失败 | 保持测试覆盖，增量提交 | QA负责人 |
| API破坏 | Facade模式 + 弃用警告 | 架构师 |
| 合并冲突 | 独立分支 + 定期rebase | 各团队 |

---

**批准签名**: _________________
**日期**: 2025-03-31