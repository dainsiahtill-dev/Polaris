"""Design System Spec — 7-section 设计规范语义格式。

来源: stitch-skill DESIGN.md format — 将设计系统的所有决策压缩为
可序列化、可验证、可导出的结构化数据。

核心能力:
1. DesignSystemSpec: frozen dataclass 表示 7-section 设计规范
2. DesignSystemExporter: 渲染为 stitch-skill 兼容的 markdown
3. 与 DesignQualityDials 无缝衔接 — atmosphere 直接映射三轴参数
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.kernelone.cognitive.design_quality import DesignQualityDials

# ---------------------------------------------------------------------------
# 子规范类型
# ---------------------------------------------------------------------------


class ColorRole(str, Enum):
    """颜色令牌的角色分类。"""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    ACCENT = "accent"
    BACKGROUND = "background"
    SURFACE = "surface"
    TEXT = "text"
    MUTED = "muted"
    BORDER = "border"


@dataclass(frozen=True)
class ColorToken:
    """单个颜色令牌 — 命名 + hex 值 + 语义角色。"""

    name: str
    hex_value: str
    role: ColorRole

    def __post_init__(self) -> None:
        # 基础 hex 格式校验
        hex_norm = self.hex_value.strip().lstrip("#")
        if len(hex_norm) == 3:
            hex_norm = "".join(c * 2 for c in hex_norm)
        if len(hex_norm) != 6:
            raise ValueError(f"ColorToken.hex_value must be 3 or 6 digit hex, got '{self.hex_value}'")
        try:
            int(hex_norm, 16)
        except ValueError as exc:
            raise ValueError(f"ColorToken.hex_value invalid hex: '{self.hex_value}'") from exc


@dataclass(frozen=True)
class TypographySpec:
    """字体层级规范。"""

    display_font: str
    body_font: str
    mono_font: str
    base_size_px: int = 16
    scale_ratio: float = 1.25
    max_line_length_ch: int = 65

    def __post_init__(self) -> None:
        if self.base_size_px < 8 or self.base_size_px > 32:
            raise ValueError(f"base_size_px must be in [8, 32], got {self.base_size_px}")
        if self.scale_ratio < 1.0 or self.scale_ratio > 2.0:
            raise ValueError(f"scale_ratio must be in [1.0, 2.0], got {self.scale_ratio}")


@dataclass(frozen=True)
class ComponentSpec:
    """组件层视觉规范。"""

    border_radius_px: int = 8
    shadow_depth: str = "md"  # none | sm | md | lg | xl
    button_style: str = "filled"  # filled | outlined | ghost
    input_style: str = "underlined"  # underlined | outlined | filled
    card_style: str = "elevated"  # flat | elevated | outlined

    def __post_init__(self) -> None:
        valid_shadows = {"none", "sm", "md", "lg", "xl"}
        if self.shadow_depth not in valid_shadows:
            raise ValueError(f"shadow_depth must be one of {valid_shadows}, got '{self.shadow_depth}'")


@dataclass(frozen=True)
class LayoutSpec:
    """布局网格规范。"""

    max_width: str = "1280px"
    grid_columns: int = 12
    gutter: str = "24px"
    breakpoint_sm: str = "640px"
    breakpoint_md: str = "768px"
    breakpoint_lg: str = "1024px"
    breakpoint_xl: str = "1280px"

    def __post_init__(self) -> None:
        if self.grid_columns < 1 or self.grid_columns > 24:
            raise ValueError(f"grid_columns must be in [1, 24], got {self.grid_columns}")


@dataclass(frozen=True)
class MotionSpec:
    """动画与交互规范。"""

    easing: str = "cubic-bezier(0.4, 0, 0.2, 1)"
    duration_base_ms: int = 200
    duration_long_ms: int = 400
    stagger_delay_ms: int = 80
    prefers_reduced_motion: bool = True

    def __post_init__(self) -> None:
        if self.duration_base_ms < 0 or self.duration_base_ms > 2000:
            raise ValueError(f"duration_base_ms must be in [0, 2000], got {self.duration_base_ms}")


@dataclass(frozen=True)
class AntiPatternsSpec:
    """设计反模式清单 — 明确禁止的模式。"""

    banned_fonts: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "Inter",
                "Roboto",
                "Open Sans",
                "Lato",
                "Times New Roman",
                "Georgia",
                "Garamond",
                "Palatino",
            }
        ),
    )
    banned_colors: frozenset[str] = field(
        default_factory=lambda: frozenset({"#000000"}),
    )
    banned_words: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "lorem ipsum",
                "filler text",
                "placeholder text",
            }
        ),
    )
    layout_anti_patterns: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "h-screen",
                "height: 100vh",
                "calc(%) in flex",
            }
        ),
    )
    motion_anti_patterns: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "linear easing",
                "animate top/left/width/height",
            }
        ),
    )


# ---------------------------------------------------------------------------
# 主规范
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesignSystemSpec:
    """7-section 设计系统规范 — stitch-skill 语义格式的结构化表示。

    7 sections:
    1. Atmosphere — 氛围参数 (variance / motion / density)
    2. Colors — 颜色令牌系统
    3. Typography — 字体层级
    4. Components — 组件视觉规范
    5. Layout — 网格与断点
    6. Motion — 动画与交互
    7. Anti-patterns — 明确禁止的模式

    构造方式:
        >>> from polaris.kernelone.cognitive.design_quality import DesignQualityDials
        >>> dials = DesignQualityDials.minimalist()
        >>> spec = DesignSystemSpec.from_dials(dials)
    """

    atmosphere: dict[str, int]
    colors: tuple[ColorToken, ...]
    typography: TypographySpec
    components: ComponentSpec
    layout: LayoutSpec
    motion: MotionSpec
    anti_patterns: AntiPatternsSpec

    def __post_init__(self) -> None:
        # 校验 atmosphere 包含必需键
        required = {"variance", "motion", "density"}
        missing = required - set(self.atmosphere.keys())
        if missing:
            raise ValueError(f"DesignSystemSpec.atmosphere missing required keys: {missing}")
        for key, value in self.atmosphere.items():
            if not (1 <= value <= 10):
                raise ValueError(f"atmosphere.{key} must be in [1, 10], got {value}")

    # -----------------------------------------------------------------------
    # 工厂方法
    # -----------------------------------------------------------------------

    @classmethod
    def from_dials(cls, dials: DesignQualityDials) -> DesignSystemSpec:
        """从 DesignQualityDials 自动生成完整的 DesignSystemSpec。

        根据三轴参数自动推导颜色、字体、布局、动画等所有子规范。
        这是 taste-skill → Polaris 的核心映射函数。
        """
        atmosphere = dials.to_dict()

        colors = cls._derive_colors(dials)
        typography = cls._derive_typography(dials)
        components = cls._derive_components(dials)
        layout = cls._derive_layout(dials)
        motion = cls._derive_motion(dials)
        anti_patterns = AntiPatternsSpec()

        return cls(
            atmosphere=atmosphere,
            colors=colors,
            typography=typography,
            components=components,
            layout=layout,
            motion=motion,
            anti_patterns=anti_patterns,
        )

    # -----------------------------------------------------------------------
    # 内部推导逻辑
    # -----------------------------------------------------------------------

    @staticmethod
    def _derive_colors(dials: DesignQualityDials) -> tuple[ColorToken, ...]:
        """根据 density 轴推导颜色系统。"""
        # density 低 → 高对比度、更克制
        # density 高 → 功能性颜色、更中性
        if dials.density <= 3:
            # Gallery / 极简: 高对比，黑白为主，单一强调色
            return (
                ColorToken("background", "#FAFAFA", ColorRole.BACKGROUND),
                ColorToken("surface", "#FFFFFF", ColorRole.SURFACE),
                ColorToken("text", "#18181B", ColorRole.TEXT),
                ColorToken("muted", "#71717A", ColorRole.MUTED),
                ColorToken("primary", "#18181B", ColorRole.PRIMARY),
                ColorToken("accent", "#DC2626", ColorRole.ACCENT),
                ColorToken("border", "#E4E4E7", ColorRole.BORDER),
            )
        if dials.density <= 7:
            # Daily app: 中等对比，双色调
            return (
                ColorToken("background", "#F8FAFC", ColorRole.BACKGROUND),
                ColorToken("surface", "#FFFFFF", ColorRole.SURFACE),
                ColorToken("text", "#0F172A", ColorRole.TEXT),
                ColorToken("muted", "#64748B", ColorRole.MUTED),
                ColorToken("primary", "#3B82F6", ColorRole.PRIMARY),
                ColorToken("secondary", "#8B5CF6", ColorRole.SECONDARY),
                ColorToken("accent", "#F59E0B", ColorRole.ACCENT),
                ColorToken("border", "#E2E8F0", ColorRole.BORDER),
            )
        # Cockpit: 高密度，深色友好，功能性色彩
        return (
            ColorToken("background", "#0F172A", ColorRole.BACKGROUND),
            ColorToken("surface", "#1E293B", ColorRole.SURFACE),
            ColorToken("text", "#F1F5F9", ColorRole.TEXT),
            ColorToken("muted", "#94A3B8", ColorRole.MUTED),
            ColorToken("primary", "#38BDF8", ColorRole.PRIMARY),
            ColorToken("secondary", "#818CF8", ColorRole.SECONDARY),
            ColorToken("accent", "#34D399", ColorRole.ACCENT),
            ColorToken("border", "#334155", ColorRole.BORDER),
        )

    @staticmethod
    def _derive_typography(dials: DesignQualityDials) -> TypographySpec:
        """根据 density 轴推导字体规范。"""
        if dials.density <= 3:
            return TypographySpec(
                display_font="Cabinet Grotesk",
                body_font="Geist",
                mono_font="JetBrains Mono",
                base_size_px=16,
                scale_ratio=1.333,
                max_line_length_ch=65,
            )
        if dials.density <= 7:
            return TypographySpec(
                display_font="Outfit",
                body_font="Geist",
                mono_font="JetBrains Mono",
                base_size_px=15,
                scale_ratio=1.25,
                max_line_length_ch=70,
            )
        return TypographySpec(
            display_font="Satoshi",
            body_font="Geist",
            mono_font="JetBrains Mono",
            base_size_px=13,
            scale_ratio=1.2,
            max_line_length_ch=80,
        )

    @staticmethod
    def _derive_components(dials: DesignQualityDials) -> ComponentSpec:
        """根据 density 轴推导组件规范。"""
        if dials.density <= 3:
            return ComponentSpec(
                border_radius_px=12,
                shadow_depth="lg",
                button_style="ghost",
                input_style="underlined",
                card_style="elevated",
            )
        if dials.density <= 7:
            return ComponentSpec(
                border_radius_px=8,
                shadow_depth="md",
                button_style="filled",
                input_style="outlined",
                card_style="elevated",
            )
        return ComponentSpec(
            border_radius_px=4,
            shadow_depth="none",
            button_style="filled",
            input_style="outlined",
            card_style="flat",
        )

    @staticmethod
    def _derive_layout(dials: DesignQualityDials) -> LayoutSpec:
        """根据 variance 轴推导布局规范。"""
        if dials.variance <= 3:
            # 对称居中
            return LayoutSpec(
                max_width="1024px",
                grid_columns=12,
                gutter="32px",
            )
        if dials.variance <= 7:
            # 偏移不对称
            return LayoutSpec(
                max_width="1280px",
                grid_columns=12,
                gutter="24px",
            )
        # masonry / 分数网格
        return LayoutSpec(
            max_width="1440px",
            grid_columns=16,
            gutter="16px",
        )

    @staticmethod
    def _derive_motion(dials: DesignQualityDials) -> MotionSpec:
        """根据 motion 轴推导动画规范。"""
        if dials.motion <= 3:
            return MotionSpec(
                easing="cubic-bezier(0.4, 0, 0.2, 1)",
                duration_base_ms=150,
                duration_long_ms=300,
                stagger_delay_ms=0,
                prefers_reduced_motion=True,
            )
        if dials.motion <= 7:
            return MotionSpec(
                easing="cubic-bezier(0.34, 1.56, 0.64, 1)",  # gentle spring
                duration_base_ms=200,
                duration_long_ms=400,
                stagger_delay_ms=60,
                prefers_reduced_motion=True,
            )
        return MotionSpec(
            easing="cubic-bezier(0.16, 1, 0.3, 1)",  # premium spring
            duration_base_ms=250,
            duration_long_ms=500,
            stagger_delay_ms=80,
            prefers_reduced_motion=True,
        )

    # -----------------------------------------------------------------------
    # 序列化
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict，供 JSON / YAML 持久化。"""
        return {
            "atmosphere": dict(self.atmosphere),
            "colors": [{"name": c.name, "hex_value": c.hex_value, "role": c.role.value} for c in self.colors],
            "typography": {
                "display_font": self.typography.display_font,
                "body_font": self.typography.body_font,
                "mono_font": self.typography.mono_font,
                "base_size_px": self.typography.base_size_px,
                "scale_ratio": self.typography.scale_ratio,
                "max_line_length_ch": self.typography.max_line_length_ch,
            },
            "components": {
                "border_radius_px": self.components.border_radius_px,
                "shadow_depth": self.components.shadow_depth,
                "button_style": self.components.button_style,
                "input_style": self.components.input_style,
                "card_style": self.components.card_style,
            },
            "layout": {
                "max_width": self.layout.max_width,
                "grid_columns": self.layout.grid_columns,
                "gutter": self.layout.gutter,
                "breakpoint_sm": self.layout.breakpoint_sm,
                "breakpoint_md": self.layout.breakpoint_md,
                "breakpoint_lg": self.layout.breakpoint_lg,
                "breakpoint_xl": self.layout.breakpoint_xl,
            },
            "motion": {
                "easing": self.motion.easing,
                "duration_base_ms": self.motion.duration_base_ms,
                "duration_long_ms": self.motion.duration_long_ms,
                "stagger_delay_ms": self.motion.stagger_delay_ms,
                "prefers_reduced_motion": self.motion.prefers_reduced_motion,
            },
            "anti_patterns": {
                "banned_fonts": sorted(self.anti_patterns.banned_fonts),
                "banned_colors": sorted(self.anti_patterns.banned_colors),
                "banned_words": sorted(self.anti_patterns.banned_words),
                "layout_anti_patterns": sorted(self.anti_patterns.layout_anti_patterns),
                "motion_anti_patterns": sorted(self.anti_patterns.motion_anti_patterns),
            },
        }


# ---------------------------------------------------------------------------
# 导出器
# ---------------------------------------------------------------------------


class DesignSystemExporter:
    """DesignSystemSpec → stitch-skill 兼容的 7-section DESIGN.md。"""

    def render(self, spec: DesignSystemSpec) -> str:
        """渲染为 markdown 字符串。

        输出严格遵循 stitch-skill 7-section 结构:
        1. Atmosphere
        2. Colors
        3. Typography
        4. Components
        5. Layout
        6. Motion
        7. Anti-patterns
        """
        sections: list[str] = []
        sections.append(self._render_atmosphere(spec))
        sections.append(self._render_colors(spec))
        sections.append(self._render_typography(spec))
        sections.append(self._render_components(spec))
        sections.append(self._render_layout(spec))
        sections.append(self._render_motion(spec))
        sections.append(self._render_anti_patterns(spec))
        return "\n\n".join(sections)

    # -----------------------------------------------------------------------
    # 各 section 渲染
    # -----------------------------------------------------------------------

    @staticmethod
    def _render_atmosphere(spec: DesignSystemSpec) -> str:
        a = spec.atmosphere
        variance_label = (
            "Symmetric / Centered"
            if a["variance"] <= 3
            else "Offset / Asymmetric"
            if a["variance"] <= 7
            else "Masonry / Fractional"
        )
        motion_label = "Hover Only" if a["motion"] <= 3 else "Gentle Spring" if a["motion"] <= 7 else "Premium Spring"
        density_label = (
            "Gallery (Generous White Space)"
            if a["density"] <= 3
            else "Daily App"
            if a["density"] <= 7
            else "Cockpit (Information Dense)"
        )

        return (
            "# 1. Atmosphere\n\n"
            f"- **Variance**: {a['variance']}/10 — {variance_label}\n"
            f"- **Motion**: {a['motion']}/10 — {motion_label}\n"
            f"- **Density**: {a['density']}/10 — {density_label}\n"
        )

    @staticmethod
    def _render_colors(spec: DesignSystemSpec) -> str:
        lines = ["# 2. Colors\n"]
        # Group by role
        by_role: dict[str, list[ColorToken]] = {}
        for c in spec.colors:
            by_role.setdefault(c.role.value, []).append(c)

        for role in ["primary", "secondary", "accent", "background", "surface", "text", "muted", "border"]:
            if role in by_role:
                tokens = by_role[role]
                for t in tokens:
                    lines.append(f"- **{t.name}** (`{t.hex_value}`) — {role}")
        return "\n".join(lines)

    @staticmethod
    def _render_typography(spec: DesignSystemSpec) -> str:
        t = spec.typography
        return (
            "# 3. Typography\n\n"
            f"- **Display**: {t.display_font}\n"
            f"- **Body**: {t.body_font}\n"
            f"- **Mono**: {t.mono_font}\n"
            f"- **Base size**: {t.base_size_px}px\n"
            f"- **Scale ratio**: {t.scale_ratio}\n"
            f"- **Max line length**: {t.max_line_length_ch}ch\n"
        )

    @staticmethod
    def _render_components(spec: DesignSystemSpec) -> str:
        c = spec.components
        return (
            "# 4. Components\n\n"
            f"- **Border radius**: {c.border_radius_px}px\n"
            f"- **Shadow depth**: {c.shadow_depth}\n"
            f"- **Button style**: {c.button_style}\n"
            f"- **Input style**: {c.input_style}\n"
            f"- **Card style**: {c.card_style}\n"
        )

    @staticmethod
    def _render_layout(spec: DesignSystemSpec) -> str:
        layout = spec.layout
        return (
            "# 5. Layout\n\n"
            f"- **Max width**: {layout.max_width}\n"
            f"- **Grid columns**: {layout.grid_columns}\n"
            f"- **Gutter**: {layout.gutter}\n"
            "\n"
            "## Breakpoints\n\n"
            f"- **sm**: {layout.breakpoint_sm}\n"
            f"- **md**: {layout.breakpoint_md}\n"
            f"- **lg**: {layout.breakpoint_lg}\n"
            f"- **xl**: {layout.breakpoint_xl}\n"
        )

    @staticmethod
    def _render_motion(spec: DesignSystemSpec) -> str:
        m = spec.motion
        return (
            "# 6. Motion\n\n"
            f"- **Easing**: `{m.easing}`\n"
            f"- **Base duration**: {m.duration_base_ms}ms\n"
            f"- **Long duration**: {m.duration_long_ms}ms\n"
            f"- **Stagger delay**: {m.stagger_delay_ms}ms\n"
            f"- **Prefers reduced motion**: {'Yes' if m.prefers_reduced_motion else 'No'}\n"
        )

    @staticmethod
    def _render_anti_patterns(spec: DesignSystemSpec) -> str:
        ap = spec.anti_patterns
        lines = ["# 7. Anti-patterns\n"]
        if ap.banned_fonts:
            lines.append("\n## Banned Fonts\n")
            for f in sorted(ap.banned_fonts):
                lines.append(f"- {f}")
        if ap.banned_colors:
            lines.append("\n## Banned Colors\n")
            for c in sorted(ap.banned_colors):
                lines.append(f"- `{c}`")
        if ap.banned_words:
            lines.append("\n## Banned Words\n")
            for w in sorted(ap.banned_words):
                lines.append(f"- {w}")
        if ap.layout_anti_patterns:
            lines.append("\n## Layout Anti-patterns\n")
            for p in sorted(ap.layout_anti_patterns):
                lines.append(f"- {p}")
        if ap.motion_anti_patterns:
            lines.append("\n## Motion Anti-patterns\n")
            for p in sorted(ap.motion_anti_patterns):
                lines.append(f"- {p}")
        return "\n".join(lines)
