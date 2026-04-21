"""Motion Preset Library — P1-C: 动画预设库。

由 motion 轴驱动的动画规范生成。
提供 spring physics 预设、duration/stagger 常量、以及 GSAP/Framer Motion 互斥检查。

核心常量:
    SPRING_PREMIUM — 高端 spring 物理
    SPRING_GENTLE  — 温和 spring 物理
    STAGGER_CASCADE_MS — 级联 stagger 延迟
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from polaris.kernelone.cognitive.design_quality import DesignQualityDials, MotionPresetKey

# ---------------------------------------------------------------------------
# 核心预设常量
# ---------------------------------------------------------------------------

SPRING_PREMIUM: dict[str, Any] = {
    "type": "spring",
    "stiffness": 100,
    "damping": 20,
    "mass": 1,
}

SPRING_GENTLE: dict[str, Any] = {
    "type": "spring",
    "stiffness": 200,
    "damping": 25,
    "mass": 1,
}

SPRING_HOVER: dict[str, Any] = {
    "type": "spring",
    "stiffness": 300,
    "damping": 30,
    "mass": 1,
}

# 标准 cubic-bezier 缓动（非 spring 场景）
EASE_OUT_EXPO: tuple[float, float, float, float] = (0.16, 1, 0.3, 1)
EASE_OUT_QUART: tuple[float, float, float, float] = (0.25, 1, 0.5, 1)
EASE_IN_OUT_CUBIC: tuple[float, float, float, float] = (0.65, 0, 0.35, 1)

# Stagger 延迟
STAGGER_CASCADE_MS: int = 80
STAGGER_RAPID_MS: int = 40

# GSAP / Framer Motion 互斥表
ANIMATION_MUTUAL_EXCLUSION: dict[str, frozenset[str]] = {
    "gsap": frozenset({"framer_motion", "framer-motion", "motion"}),
    "framer_motion": frozenset({"gsap"}),
}


# ---------------------------------------------------------------------------
# 令牌类型
# ---------------------------------------------------------------------------


class EasingType(str, Enum):
    """缓动函数类型。"""

    SPRING = "spring"
    CUBIC_BEZIER = "cubic-bezier"
    LINEAR = "linear"


@dataclass(frozen=True)
class MotionToken:
    """单个动画令牌 — 描述一个交互状态的动画规范。"""

    name: str  # e.g., "hover", "enter", "exit", "stagger"
    duration_ms: int
    easing: str  # CSS easing string or "spring(...)"
    delay_ms: int = 0
    properties: frozenset[str] = frozenset()  # 动画属性: transform, opacity, etc.

    def __post_init__(self) -> None:
        if self.duration_ms < 0 or self.duration_ms > 5000:
            raise ValueError(f"duration_ms must be in [0, 5000], got {self.duration_ms}")

    def to_css_transition(self) -> str:
        """生成 CSS transition 字符串。"""
        props = ", ".join(sorted(self.properties)) if self.properties else "all"
        return f"{props} {self.duration_ms}ms {self.easing}"

    def to_framer_motion(self) -> dict[str, Any]:
        """生成 Framer Motion transition 对象。"""
        if self.easing.startswith("spring") or "stiffness" in self.easing:
            import re

            match = re.search(r"spring\((\d+),\s*(\d+)\)", self.easing)
            if match:
                stiffness = int(match.group(1))
                damping = int(match.group(2))
            else:
                stiffness = 100
                damping = 20
            return {
                "type": "spring",
                "stiffness": stiffness,
                "damping": damping,
            }
        return {
            "duration": self.duration_ms / 1000,
            "ease": self.easing,
        }

    def to_gsap_config(self) -> dict[str, Any]:
        """生成 GSAP tween config。"""
        config: dict[str, Any] = {
            "duration": self.duration_ms / 1000,
            "ease": self.easing,
        }
        if self.delay_ms > 0:
            config["delay"] = self.delay_ms / 1000
        return config


# ---------------------------------------------------------------------------
# 预设库
# ---------------------------------------------------------------------------


class MotionPresetLibrary:
    """动画预设库 — motion 轴驱动的动画规范生成器。

    使用方式:
        >>> from polaris.kernelone.cognitive.design_quality import DesignQualityDials
        >>> dials = DesignQualityDials.experimental()  # motion=8
        >>> lib = MotionPresetLibrary(dials)
        >>> hover = lib.get_hover_token()
        >>> hover.duration_ms
        250
        >>> lib.get_enter_tokens()
        [MotionToken(...), ...]
    """

    def __init__(self, dials: DesignQualityDials) -> None:
        """初始化动画预设库。

        Args:
            dials: 三轴质量参数，motion 轴驱动动画强度决策
        """
        self._dials = dials
        self._preset_key = dials.motion_preset_key

    # -----------------------------------------------------------------------
    # 预设查询
    # -----------------------------------------------------------------------

    def get_hover_token(self) -> MotionToken:
        """Hover 状态动画令牌。"""
        if self._preset_key is MotionPresetKey.HOVER_ONLY:
            return MotionToken(
                name="hover",
                duration_ms=150,
                easing="cubic-bezier(0.4, 0, 0.2, 1)",
                properties=frozenset({"opacity", "transform"}),
            )
        if self._preset_key is MotionPresetKey.SPRING_GENTLE:
            return MotionToken(
                name="hover",
                duration_ms=200,
                easing="spring(200, 25)",
                properties=frozenset({"opacity", "transform", "background-color"}),
            )
        # SPRING_PREMIUM
        return MotionToken(
            name="hover",
            duration_ms=250,
            easing="spring(100, 20)",
            properties=frozenset({"opacity", "transform", "background-color", "box-shadow"}),
        )

    def get_enter_tokens(self) -> list[MotionToken]:
        """入场动画令牌列表（级联）。"""
        if self._preset_key is MotionPresetKey.HOVER_ONLY:
            return [
                MotionToken(
                    name="enter",
                    duration_ms=200,
                    easing="cubic-bezier(0.4, 0, 0.2, 1)",
                    delay_ms=0,
                    properties=frozenset({"opacity", "transform"}),
                ),
            ]
        if self._preset_key is MotionPresetKey.SPRING_GENTLE:
            return [
                MotionToken(
                    name="enter-fade",
                    duration_ms=300,
                    easing="spring(200, 25)",
                    delay_ms=0,
                    properties=frozenset({"opacity"}),
                ),
                MotionToken(
                    name="enter-slide",
                    duration_ms=400,
                    easing="spring(200, 25)",
                    delay_ms=STAGGER_RAPID_MS,
                    properties=frozenset({"transform"}),
                ),
            ]
        # SPRING_PREMIUM
        return [
            MotionToken(
                name="enter-fade",
                duration_ms=400,
                easing="spring(100, 20)",
                delay_ms=0,
                properties=frozenset({"opacity"}),
            ),
            MotionToken(
                name="enter-scale",
                duration_ms=500,
                easing="spring(100, 20)",
                delay_ms=STAGGER_CASCADE_MS,
                properties=frozenset({"transform"}),
            ),
            MotionToken(
                name="enter-blur",
                duration_ms=500,
                easing="spring(100, 20)",
                delay_ms=STAGGER_CASCADE_MS * 2,
                properties=frozenset({"filter"}),
            ),
        ]

    def get_exit_token(self) -> MotionToken:
        """退场动画令牌。"""
        if self._preset_key is MotionPresetKey.HOVER_ONLY:
            return MotionToken(
                name="exit",
                duration_ms=150,
                easing="cubic-bezier(0.4, 0, 0.2, 1)",
                properties=frozenset({"opacity"}),
            )
        if self._preset_key is MotionPresetKey.SPRING_GENTLE:
            return MotionToken(
                name="exit",
                duration_ms=200,
                easing="spring(200, 25)",
                properties=frozenset({"opacity", "transform"}),
            )
        # SPRING_PREMIUM
        return MotionToken(
            name="exit",
            duration_ms=300,
            easing="spring(100, 20)",
            properties=frozenset({"opacity", "transform", "filter"}),
        )

    def get_stagger_delay_ms(self) -> int:
        """获取当前预设的 stagger 延迟。"""
        if self._preset_key is MotionPresetKey.HOVER_ONLY:
            return 0
        if self._preset_key is MotionPresetKey.SPRING_GENTLE:
            return STAGGER_RAPID_MS
        return STAGGER_CASCADE_MS

    # -----------------------------------------------------------------------
    # 静态工具
    # -----------------------------------------------------------------------

    @staticmethod
    def check_animation_library_conflict(used_libraries: list[str]) -> list[str]:
        """检查动画库互斥冲突。

        Args:
            used_libraries: 已使用的动画库名称列表（如 ["gsap", "framer-motion"]）

        Returns:
            冲突描述列表。空列表表示无冲突。
        """
        conflicts: list[str] = []
        normalized = [lib.lower().replace("-", "_") for lib in used_libraries]

        for lib in normalized:
            if lib in ANIMATION_MUTUAL_EXCLUSION:
                banned = ANIMATION_MUTUAL_EXCLUSION[lib]
                for other in normalized:
                    if other != lib and other in banned:
                        conflicts.append(f"Animation library conflict: '{lib}' and '{other}' are mutually exclusive.")

        # 去重
        return list(dict.fromkeys(conflicts))
