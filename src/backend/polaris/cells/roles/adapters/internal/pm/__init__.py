"""PM 适配器内部组件包.

``pm_adapter.py`` 仍是唯一的装配点（assembly point）与 canonical 导入路径。
本包仅承载被组合的叶子工具与职责 mixin，便于维护与按职责定位：

- :mod:`pm_text_utils` — 冻结常量、正则与纯函数（叶子，无兄弟依赖）。
- :mod:`parsing` — :class:`PMContractParsingMixin`
- :mod:`normalization` — :class:`PMContractNormalizationMixin`
- :mod:`synthesis` — :class:`PMContractSynthesisMixin`（确定性、无 LLM 合成）
- :mod:`prompting` — :class:`PMPromptBuildingMixin`
- :mod:`board_tasks` — :class:`PMBoardTaskMixin`
- :mod:`plan_artifact` — :class:`PMPlanArtifactMixin`（写 runtime/tasks/plan.json）

防重复造轮子：不要在此包外重建 PM 合同解析/归一化/合成逻辑。
"""

from __future__ import annotations

from .board_tasks import PMBoardTaskMixin
from .normalization import PMContractNormalizationMixin
from .parsing import PMContractParsingMixin
from .plan_artifact import PMPlanArtifactMixin
from .prompting import PMPromptBuildingMixin
from .synthesis import PMContractSynthesisMixin

__all__ = [
    "PMBoardTaskMixin",
    "PMContractNormalizationMixin",
    "PMContractParsingMixin",
    "PMContractSynthesisMixin",
    "PMPlanArtifactMixin",
    "PMPromptBuildingMixin",
]
