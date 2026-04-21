"""Design Quality Dials — 将 taste-skill 三轴参数系统转化为 Polaris 工程类型。

来源: taste-skill (Leonxlnx/taste-skill) 三参数系统
    DESIGN_VARIANCE × MOTION_INTENSITY × VISUAL_DENSITY

核心思想: 将不可名状的"设计感"降维到三个 1-10 连续空间，
驱动下游所有生成决策（typography, layout, motion, color）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class LayoutMode(str, Enum):
    """布局模式 — 由 variance 轴派生。"""

    SYMMETRIC_CENTERED = "symmetric_centered"  # variance 1-3: 对称居中
    OFFSET_ASYMMETRIC = "offset_asymmetric"  # variance 4-7: 偏移不对称
    MASONRY_FRACTIONAL = "masonry_fractional"  # variance 8-10:  masonry/分数网格


class SpacingTier(str, Enum):
    """间距层级 — 由 density 轴派生。"""

    GALLERY = "gallery"  # density 1-3: 奢侈留白，py-24/py-32
    DAILY_APP = "daily_app"  # density 4-7: 正常应用间距
    COCKPIT = "cockpit"  # density 8-10: 驾驶舱级密集


class MotionPresetKey(str, Enum):
    """动画预设键 — 由 motion 轴派生。"""

    HOVER_ONLY = "hover_only"  # motion 1-3: 仅 hover 状态
    SPRING_GENTLE = "spring_gentle"  # motion 4-7: 温和 spring 物理
    SPRING_PREMIUM = "spring_premium"  # motion 8-10: 完整 spring 系统


@dataclass(frozen=True)
class DesignQualityDials:
    """三轴质量参数 — 驱动前端生成的所有设计决策。

    来源: taste-skill 三参数系统 (DESIGN_VARIANCE / MOTION_INTENSITY / VISUAL_DENSITY)

    三个维度各自独立，组合成一个 3D 设计意图空间。
    下游子系统（typography, layout, motion, color）各自消费自己关心的轴。

    Args:
        variance: 布局方差。1=对称居中，10=不对称/实验性/masonry。
        motion: 动画强度。1=简单 hover，10=spring 物理/滚动触发/GSAP。
        density: 视觉密度。1=奢侈留白（画廊），10=驾驶舱级密集。

    Examples:
        >>> dials = DesignQualityDials(variance=2, motion=2, density=2)
        >>> dials.layout_mode
        <LayoutMode.SYMMETRIC_CENTERED: 'symmetric_centered'>
        >>> dials.spacing_tier
        <SpacingTier.GALLERY: 'gallery'>
    """

    variance: int = field(default=5, metadata={"ge": 1, "le": 10})
    motion: int = field(default=5, metadata={"ge": 1, "le": 10})
    density: int = field(default=5, metadata={"ge": 1, "le": 10})

    def __post_init__(self) -> None:
        # 防御性边界检查 — frozen dataclass 中通过 object.__setattr__ 修正
        for axis_name, value in (("variance", self.variance), ("motion", self.motion), ("density", self.density)):
            if not (1 <= value <= 10):
                raise ValueError(f"DesignQualityDials.{axis_name} must be in [1, 10], got {value}")

    # -----------------------------------------------------------------------
    # 派生属性 — 各轴独立映射到下游枚举
    # -----------------------------------------------------------------------

    @property
    def layout_mode(self) -> LayoutMode:
        """由 variance 轴派生的布局模式。"""
        if self.variance <= 3:
            return LayoutMode.SYMMETRIC_CENTERED
        if self.variance <= 7:
            return LayoutMode.OFFSET_ASYMMETRIC
        return LayoutMode.MASONRY_FRACTIONAL

    @property
    def spacing_tier(self) -> SpacingTier:
        """由 density 轴派生的间距层级。"""
        if self.density <= 3:
            return SpacingTier.GALLERY
        if self.density <= 7:
            return SpacingTier.DAILY_APP
        return SpacingTier.COCKPIT

    @property
    def motion_preset_key(self) -> MotionPresetKey:
        """由 motion 轴派生的动画预设键。"""
        if self.motion <= 3:
            return MotionPresetKey.HOVER_ONLY
        if self.motion <= 7:
            return MotionPresetKey.SPRING_GENTLE
        return MotionPresetKey.SPRING_PREMIUM

    # -----------------------------------------------------------------------
    # 预设工厂 — 常见设计意图的快捷构造
    # -----------------------------------------------------------------------

    @classmethod
    def minimalist(cls) -> DesignQualityDials:
        """极简主义预设: 低方差、低动画、低密度。"""
        return cls(variance=3, motion=2, density=2)

    @classmethod
    def modern_premium(cls) -> DesignQualityDials:
        """现代高端预设: 中等方差、中等动画、中等密度。"""
        return cls(variance=6, motion=6, density=5)

    @classmethod
    def dashboard(cls) -> DesignQualityDials:
        """数据驾驶舱预设: 低方差、低动画、高密度。"""
        return cls(variance=4, motion=3, density=9)

    @classmethod
    def experimental(cls) -> DesignQualityDials:
        """实验性创意预设: 高方差、高动画、中低密度。"""
        return cls(variance=9, motion=8, density=4)

    @classmethod
    def soft(cls) -> DesignQualityDials:
        """柔和优雅预设: 中等方差、低动画、低密度。"""
        return cls(variance=5, motion=4, density=3)

    @classmethod
    def brutalist(cls) -> DesignQualityDials:
        """粗野主义预设: 高方差、低动画、中高密度。"""
        return cls(variance=8, motion=2, density=7)

    # -----------------------------------------------------------------------
    # 序列化
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, int]:
        """序列化为 dict，供 session metadata / YAML 持久化使用。"""
        return {"variance": self.variance, "motion": self.motion, "density": self.density}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DesignQualityDials:
        """从 dict 反序列化。"""
        return cls(
            variance=int(data.get("variance", 5)),
            motion=int(data.get("motion", 5)),
            density=int(data.get("density", 5)),
        )

    # -----------------------------------------------------------------------
    # 三层覆盖解析 — 项目默认值 (Layer 1)
    #
    # TODO(P1): 实现项目配置文件读取 (.polaris/design.yaml)
    # 当前返回 hard-coded 默认值，避免引入文件 IO 依赖。
    # -----------------------------------------------------------------------

    _PROJECT_DEFAULT: DesignQualityDials | None = None

    @classmethod
    def load_project_default(cls, workspace: str | Path | None = None) -> DesignQualityDials:
        """加载项目级默认 dials (Layer 1)。

        当前实现返回 hard-coded 默认值。
        P1 阶段将支持从 .polaris/design.yaml 读取。
        """
        if cls._PROJECT_DEFAULT is not None:
            return cls._PROJECT_DEFAULT
        return cls()  # 返回默认构造 (5, 5, 5)

    @classmethod
    def set_project_default(cls, dials: DesignQualityDials) -> None:
        """设置项目级默认值（供测试或运行时配置使用）。"""
        cls._PROJECT_DEFAULT = dials

    @classmethod
    def reset_project_default(cls) -> None:
        """重置项目级默认值为 None。"""
        cls._PROJECT_DEFAULT = None
