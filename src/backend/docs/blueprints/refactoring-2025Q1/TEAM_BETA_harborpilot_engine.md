# Team Beta: polaris_engine.py 重构蓝图

## 目标文件
`polaris/delivery/cli/pm/polaris_engine.py` (3411行)

## 架构分析

### 当前问题
1. **巨型Engine类**: PolarisEngine 承担过多职责
2. **辅助函数泛滥**: 60+ 个`_`前缀辅助函数散落文件中
3. **调度逻辑耦合**: 调度器、任务板、Tri-Council混在一起

### 职责拆分矩阵

| 职责 | 行数 | 目标模块 |
|------|------|---------|
| 调度器 | ~400 | `scheduler.py` |
| 任务板集成 | ~500 | `taskboard.py` |
| Tri-Council协调 | ~400 | `tri_council.py` |
| 交付验证 | ~400 | `delivery_floor.py` |
| 完成锁状态 | ~300 | `completion_lock.py` |
| 辅助函数 | ~600 | `helpers.py` |
| 核心引擎 | ~400 | `polaris_engine.py` (保留) |

## 拆分方案

### 目标结构
```
polaris/delivery/cli/pm/
├── polaris_engine.py        # Facade (50行)
├── engine/
│   ├── __init__.py
│   ├── core.py                  # PolarisEngine核心 (400行)
│   ├── scheduler.py             # 调度器 (350行)
│   ├── taskboard.py             # 任务板集成 (400行)
│   ├── tri_council.py           # Tri-Council协调 (350行)
│   ├── delivery_floor.py        # 交付验证 (350行)
│   ├── completion_lock.py       # 完成锁状态 (250行)
│   └── helpers.py               # 辅助函数 (500行)
```

### 模块契约

#### `scheduler.py`
```python
"""任务调度器模块。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol, Iterator

@dataclass(frozen=True, slots=True)
class SchedulingPolicy:
    """调度策略配置。"""
    mode: str  # "fifo" | "priority" | "dag"
    max_batch_size: int
    priority_levels: frozenset[str]

class TaskScheduler(Protocol):
    """任务调度器协议。"""

    def select_batch(
        self,
        tasks: list[dict[str, any]]
    ) -> Iterator[list[dict[str, any]]]:
        """选择下一批待执行任务。"""
        ...

class FIFOScheduler:
    """FIFO 调度器实现。"""

    __slots__ = ('_policy',)

    def __init__(self, policy: SchedulingPolicy) -> None:
        self._policy = policy

    def select_batch(
        self,
        tasks: list[dict[str, any]]
    ) -> Iterator[list[dict[str, any]]]:
        """按FIFO顺序选择任务批次。"""
        ...

class PriorityScheduler:
    """优先级调度器实现。"""

    __slots__ = ('_policy', '_priority_enum')

    def select_batch(
        self,
        tasks: list[dict[str, any]]
    ) -> Iterator[list[dict[str, any]]]:
        """按优先级选择任务批次。"""
        ...

class DAGScheduler:
    """DAG 依赖调度器实现。"""

    __slots__ = ('_policy', '_dependency_graph')

    def select_batch(
        self,
        tasks: list[dict[str, any]]
    ) -> Iterator[list[dict[str, any]]]:
        """按依赖拓扑序选择任务批次。"""
        ...
```

#### `tri_council.py`
```python
"""Tri-Council 协调模块。"""

from dataclasses import dataclass
from enum import Enum

class CouncilRole(str, Enum):
    """Tri-Council角色。"""
    CHIEF_ENGINEER = "ChiefEngineer"
    PM = "PM"
    ARCHITECT = "Architect"
    HUMAN = "Human"

@dataclass(frozen=True, slots=True)
class CouncilPolicy:
    """Tri-Council策略配置。"""
    max_rounds: int
    escalation_chain: tuple[CouncilRole, ...]
    retry_budget: int

@dataclass(slots=True)
class CouncilVerdict:
    """Council裁决结果。"""
    role: CouncilRole
    action: str  # "continue" | "abort" | "escalate"
    reasoning: str
    confidence: float

class TriCouncilCoordinator:
    """Tri-Council协调器。"""

    __slots__ = ('_policy', '_rounds', '_verdicts')

    def __init__(self, policy: CouncilPolicy) -> None:
        self._policy = policy
        self._rounds: list[CouncilVerdict] = []

    def should_escalate(self, failure: str) -> bool:
        """判断是否需要升级。"""
        ...

    def get_next_role(self) -> CouncilRole:
        """获取下一个应介入的角色。"""
        ...

    def record_verdict(self, verdict: CouncilVerdict) -> None:
        """记录裁决结果。"""
        ...

    def is_terminal(self) -> bool:
        """判断是否已终止。"""
        ...
```

#### `delivery_floor.py`
```python
"""交付验证模块。"""

from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class DeliveryThreshold:
    """交付阈值配置。"""
    code_files: int
    code_lines: int
    test_files: int

@dataclass(slots=True)
class DeliveryMetrics:
    """交付度量。"""
    code_file_count: int
    code_line_count: int
    test_file_count: int

    @classmethod
    def from_workspace(cls, workspace: str) -> "DeliveryMetrics":
        """从工作区计算交付度量。"""
        ...

@dataclass(slots=True)
class DeliveryVerdict:
    """交付裁决。"""
    passed: bool
    metrics: DeliveryMetrics
    threshold: DeliveryThreshold
    gaps: tuple[str, ...]

class DeliveryFloorValidator:
    """交付验证器。"""

    __slots__ = ('_thresholds',)

    def __init__(
        self,
        thresholds: dict[str, DeliveryThreshold]
    ) -> None:
        self._thresholds = thresholds

    def validate(
        self,
        workspace: str,
        scale: str
    ) -> DeliveryVerdict:
        """验证交付物是否满足阈值。"""
        ...

    def detect_scale(self, workspace: str) -> str:
        """检测项目规模。"""
        ...
```

## 实现步骤

### Step 1: 创建目录结构
```bash
mkdir -p polaris/delivery/cli/pm/engine
touch polaris/delivery/cli/pm/engine/__init__.py
```

### Step 2: 提取 Scheduler 模块
```python
# 1. 迁移 SchedulerProtocol, SingleWorkerScheduler
# 2. 添加 PriorityScheduler, DAGScheduler
# 3. 定义 SchedulingPolicy 配置类
```

### Step 3: 提取 Tri-Council 模块
```python
# 1. 迁移 _tri_council_* 函数
# 2. 创建 TriCouncilCoordinator 类
# 3. 定义 CouncilPolicy, CouncilVerdict
```

### Step 4: 提取 Delivery Floor 模块
```python
# 1. 迁移 _delivery_floor_* 函数
# 2. 创建 DeliveryFloorValidator 类
# 3. 定义 DeliveryThreshold, DeliveryMetrics
```

### Step 5: 创建 Facade
```python
# polaris/delivery/cli/pm/polaris_engine.py

"""Polaris PM引擎 (Facade)。

此文件保留向后兼容性，实际实现已迁移到 engine/ 子模块。
"""

from .engine.core import PolarisEngine
from .engine.scheduler import (
    FIFOScheduler,
    PriorityScheduler,
    DAGScheduler,
    SchedulingPolicy,
)
from .engine.tri_council import (
    TriCouncilCoordinator,
    CouncilPolicy,
    CouncilVerdict,
)
from .engine.delivery_floor import (
    DeliveryFloorValidator,
    DeliveryThreshold,
    DeliveryMetrics,
)

__all__ = [
    "PolarisEngine",
    "FIFOScheduler",
    "PriorityScheduler",
    "DAGScheduler",
    "SchedulingPolicy",
    "TriCouncilCoordinator",
    "CouncilPolicy",
    "CouncilVerdict",
    "DeliveryFloorValidator",
    "DeliveryThreshold",
    "DeliveryMetrics",
]
```

## 测试策略

### 单元测试结构
```
polaris/delivery/cli/pm/engine/tests/
├── test_scheduler.py           # 调度器测试
├── test_tri_council.py         # Tri-Council测试
├── test_delivery_floor.py      # 交付验证测试
├── test_completion_lock.py     # 完成锁测试
└── test_core.py                # 集成测试
```

### 关键测试用例
```python
# test_scheduler.py
class TestDAGScheduler:
    def test_respects_dependencies(self) -> None:
        """验证依赖顺序正确。"""
        tasks = [
            {"id": "a", "depends_on": []},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["a"]},
            {"id": "d", "depends_on": ["b", "c"]},
        ]
        scheduler = DAGScheduler(policy)
        batches = list(scheduler.select_batch(tasks))
        # a 必须在 b, c 之前
        # b, c 必须在 d 之前
        ...

# test_tri_council.py
class TestTriCouncilCoordinator:
    def test_escalation_chain(self) -> None:
        """验证升级链正确。"""
        ...

    def test_max_rounds_enforcement(self) -> None:
        """验证最大轮数限制。"""
        ...
```

## 验收标准

- [ ] 所有模块 < 400行
- [ ] mypy --strict 通过
- [ ] pytest覆盖率 > 80%
- [ ] ruff check/format 通过
- [ ] 原CLI功能正常
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