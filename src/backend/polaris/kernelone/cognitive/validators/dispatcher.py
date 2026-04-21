"""Domain Isolation Layer — P0-Pre: 生成域路由与验证调度。

确保 taste-skill 验证器只影响前端设计代码，绝不干扰 Python/SQL/后端逻辑等非设计代码生成。
核心机制：
1. 文件扩展名 → GenerationDomain 路由
2. Domain-gated validator chain — 只有 UI_COMPONENT / DESIGN_SPEC 进入 taste-skill
3. Escape Hatch — bypass_taste 标志允许显式跳过
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple, Sequence

# ---------------------------------------------------------------------------
# 基础类型
# ---------------------------------------------------------------------------


class GenerationDomain(str, Enum):
    """生成域 — 区分代码的生成意图与所属技术域。

    只有 UI_COMPONENT 和 DESIGN_SPEC 会进入 taste-skill 验证链路。
    CORE_LOGIC / DATA_PROCESSING / DOCUMENTATION 直接 bypass。
    """

    UI_COMPONENT = "ui_component"  # 前端组件、HTML/CSS/JSX/Vue/Svelte
    CORE_LOGIC = "core_logic"  # Python/Go/Rust/Java 等业务逻辑
    DATA_PROCESSING = "data_processing"  # SQL/ETL/数据处理脚本
    DOCUMENTATION = "documentation"  # Markdown/YAML/JSON 配置文档
    DESIGN_SPEC = "design_spec"  # DESIGN.md/Figma-token/设计规范文件
    UNKNOWN = "unknown"  # 无法识别的文件类型


class ValidationSeverity(str, Enum):
    """验证违规严重程度。"""

    ERROR = "error"  # 必须修复 — 触发 LLM rewrite
    WARNING = "warning"  # 静默修复 — PhantomStateHydrator 重写
    INFO = "info"  # 仅记录 — 不阻断


class ValidationViolation(NamedTuple):
    """单个验证违规记录 — 所有 validator 的统一输出格式。"""

    rule: str
    severity: ValidationSeverity
    message: str
    location: str | None = None
    domain: GenerationDomain | None = None
    fix_hint: str | None = None


# ---------------------------------------------------------------------------
# 验证配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationConfig:
    """单次验证运行的配置。

    Args:
        bypass_taste: 显式跳过 taste-skill 验证（Escape Hatch）
        allow_partial: 允许部分输出（不强制完整性检查）
        design_dials: 可选的三轴质量参数，用于上下文感知验证
        target_domain: 显式指定目标域（覆盖文件扩展名推断）
    """

    bypass_taste: bool = False
    allow_partial: bool = False
    design_dials: Any | None = None
    target_domain: GenerationDomain | None = None


# ---------------------------------------------------------------------------
# 文件扩展名 → GenerationDomain 路由表
# ---------------------------------------------------------------------------

# 前端/UI 文件扩展名 — 这些会进入 taste-skill 验证
_UI_EXTENSIONS: frozenset[str] = frozenset(
    {
        # HTML / Template
        ".html",
        ".htm",
        ".jinja",
        ".j2",
        ".njk",
        ".hbs",
        ".ejs",
        # CSS / Style
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".styl",
        ".stylus",
        # JS / TS / Framework
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".svelte",
        ".astro",
        # Asset / Config
        ".svg",
        ".json",  # 部分 JSON（design token）
    }
)

# 核心业务逻辑 — 绝不进入 taste-skill
_LOGIC_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".pyx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".scala",
        ".clj",
        ".c",
        ".cpp",
        ".cc",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".cs",
        ".fs",
        ".swift",
    }
)

# 数据处理 — 绝不进入 taste-skill
_DATA_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".sql",
        ".etl",
        ".spark",
        ".dbt",
    }
)

# 文档/配置 — 绝不进入 taste-skill
_DOC_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".mdx",
        ".rst",
        ".txt",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
    }
)

# 设计规范文件 — 进入 taste-skill
_DESIGN_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".design",
        ".tokens",
        ".theme",
    }
)


def _infer_domain_from_extension(file_path: str | Path) -> GenerationDomain:
    """根据文件扩展名推断生成域。

    这是域路由的唯一权威入口。业务模块不应自行实现分类逻辑。
    """
    ext = Path(file_path).suffix.lower()
    if ext in _UI_EXTENSIONS:
        return GenerationDomain.UI_COMPONENT
    if ext in _LOGIC_EXTENSIONS:
        return GenerationDomain.CORE_LOGIC
    if ext in _DATA_EXTENSIONS:
        return GenerationDomain.DATA_PROCESSING
    if ext in _DOC_EXTENSIONS:
        return GenerationDomain.DOCUMENTATION
    if ext in _DESIGN_EXTENSIONS:
        return GenerationDomain.DESIGN_SPEC
    return GenerationDomain.UNKNOWN


# ---------------------------------------------------------------------------
# 内容启发式路由 — 当文件扩展名不明确时
# ---------------------------------------------------------------------------

# CSS/样式相关标记 — 内容中大量出现则判定为 UI_COMPONENT
_UI_CONTENT_MARKERS: tuple[str, ...] = (
    "@tailwind",
    "@layer",
    "@apply",
    "display: flex",
    "display: grid",
    "margin:",
    "padding:",
    "border-radius:",
    "className=",
    "class=",
    "style=",
    "import React",
    "import Vue",
    "import {",
    "<template>",
    "<div",
    "<span",
    "<Component",
    "framer-motion",
    "gsap",
    "tailwindcss",
)

# Python/后端标记 — 判定为 CORE_LOGIC
_LOGIC_CONTENT_MARKERS: tuple[str, ...] = (
    "def ",
    "class ",
    "if __name__ ==",
    "async def",
    "await ",
    "typing.",
    "pydantic",
    "fastapi",
    "django",
    "@dataclass",
    "@app.route",
    "@pytest",
    "self.",
    "raise ",
    "except ",
    "try:",
    "with open(",
    "lambda ",
)

# SQL 标记 — 判定为 DATA_PROCESSING
_DATA_CONTENT_MARKERS: tuple[str, ...] = (
    "SELECT ",
    "FROM ",
    "WHERE ",
    "JOIN ",
    "INSERT INTO",
    "UPDATE ",
    "DELETE ",
    "CREATE TABLE",
    "ALTER TABLE",
    "DROP ",
)


def _infer_domain_from_content(content: str) -> GenerationDomain:
    """当扩展名不明确时，根据内容启发式推断域。"""
    if not content:
        return GenerationDomain.UNKNOWN

    content_lower = content[:2000].lower()  # 只扫描前 2KB 提高效率

    # 检查各类标记
    ui_score = sum(1 for m in _UI_CONTENT_MARKERS if m.lower() in content_lower)
    logic_score = sum(1 for m in _LOGIC_CONTENT_MARKERS if m.lower() in content_lower)
    data_score = sum(1 for m in _DATA_CONTENT_MARKERS if m.lower() in content_lower)

    scores = {
        GenerationDomain.UI_COMPONENT: ui_score,
        GenerationDomain.CORE_LOGIC: logic_score,
        GenerationDomain.DATA_PROCESSING: data_score,
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] >= 2:
        return best
    return GenerationDomain.UNKNOWN


# ---------------------------------------------------------------------------
# 主调度器
# ---------------------------------------------------------------------------


class CognitiveValidatorDispatcher:
    """认知验证调度器 — 根据生成域路由到对应的验证链路。

    使用方式：
        dispatcher = CognitiveValidatorDispatcher()
        violations = dispatcher.validate(
            file_path="src/components/Button.tsx",
            content=generated_code,
            config=ValidationConfig(),
        )

    域隔离保证：
    - UI_COMPONENT / DESIGN_SPEC → 进入 taste-skill + 完整性检查
    - CORE_LOGIC / DATA_PROCESSING / DOCUMENTATION → 仅完整性检查，跳过 taste-skill
    - UNKNOWN → 默认 bypass，记录日志
    """

    def __init__(self) -> None:
        self._validators: dict[str, Any] = {}  # 懒加载的 validator 实例缓存
        self._stats: dict[str, int] = {"total_calls": 0, "bypassed": 0, "checked": 0}

    # -----------------------------------------------------------------------
    # 核心路由接口
    # -----------------------------------------------------------------------

    def resolve_domain(
        self,
        file_path: str | Path | None = None,
        content: str | None = None,
        config: ValidationConfig | None = None,
    ) -> GenerationDomain:
        """解析目标生成域 — 三层覆盖逻辑。

        优先级：
        1. config.target_domain（显式覆盖）
        2. file_path 扩展名推断
        3. content 启发式推断
        4. UNKNOWN
        """
        if config and config.target_domain is not None:
            return config.target_domain

        if file_path is not None:
            domain = _infer_domain_from_extension(file_path)
            if domain != GenerationDomain.UNKNOWN:
                return domain

        if content is not None:
            domain = _infer_domain_from_content(content)
            if domain != GenerationDomain.UNKNOWN:
                return domain

        return GenerationDomain.UNKNOWN

    def validate(
        self,
        file_path: str | Path | None = None,
        content: str = "",
        config: ValidationConfig | None = None,
    ) -> list[ValidationViolation]:
        """执行域感知的验证。

        Args:
            file_path: 目标文件路径（用于扩展名路由）
            content: 待验证的生成内容
            config: 验证配置

        Returns:
            违规列表。Error 级别违规导致验证失败；Warning 级别触发静默修复。
        """
        cfg = config or ValidationConfig()
        self._stats["total_calls"] += 1

        # Escape Hatch: 显式 bypass
        if cfg.bypass_taste:
            self._stats["bypassed"] += 1
            return []

        domain = self.resolve_domain(file_path, content, cfg)

        # 域隔离核心逻辑：只有 UI / DESIGN 域进入 taste-skill
        if domain not in {GenerationDomain.UI_COMPONENT, GenerationDomain.DESIGN_SPEC}:
            self._stats["bypassed"] += 1
            return []

        self._stats["checked"] += 1
        violations: list[ValidationViolation] = []

        # P0-B: Anti-Slop 验证（懒加载）
        violations.extend(self._run_antislop(content, domain, cfg))

        # P0-C: 完整性检查（除非 allow_partial）
        if not cfg.allow_partial:
            violations.extend(self._run_completeness(content, domain, cfg))

        return violations

    def validate_batch(
        self,
        items: Sequence[tuple[str | Path | None, str]],
        config: ValidationConfig | None = None,
    ) -> dict[str | Path, list[ValidationViolation]]:
        """批量验证多个文件。"""
        results: dict[str | Path, list[ValidationViolation]] = {}
        for file_path, content in items:
            key = file_path or "__inline__"
            results[key] = self.validate(file_path, content, config)
        return results

    # -----------------------------------------------------------------------
    # 内部 validator 调用（懒加载占位 — 由 P0-B / P0-C 填充实现）
    # -----------------------------------------------------------------------

    def _run_antislop(
        self,
        content: str,
        domain: GenerationDomain,
        config: ValidationConfig,
    ) -> list[ValidationViolation]:
        """Anti-Slop 验证 — 字体、颜色、内容、布局、动画五维检测。"""
        from polaris.kernelone.cognitive.validators.output_antislop import OutputAntiSlopValidator

        validator = self._validators.get("antislop")
        if validator is None:
            validator = OutputAntiSlopValidator()
            self._validators["antislop"] = validator
        return validator.validate(content, {"domain": domain.value})

    def _run_completeness(
        self,
        content: str,
        domain: GenerationDomain,
        config: ValidationConfig,
    ) -> list[ValidationViolation]:
        """完整性验证 — 检测 AI 截断、骨架输出与占位符。"""
        from polaris.kernelone.cognitive.validators.completeness_enforcer import OutputCompletenessEnforcer

        validator = self._validators.get("completeness")
        if validator is None:
            validator = OutputCompletenessEnforcer()
            self._validators["completeness"] = validator
        return validator.validate(content, {"domain": domain.value})

    # -----------------------------------------------------------------------
    # 统计与观测
    # -----------------------------------------------------------------------

    def get_stats(self) -> dict[str, int]:
        """返回验证统计信息。"""
        return dict(self._stats)

    def reset_stats(self) -> None:
        """重置统计计数器。"""
        self._stats = {"total_calls": 0, "bypassed": 0, "checked": 0}


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------

_DEFAULT_DISPATCHER: CognitiveValidatorDispatcher | None = None


def get_validator_dispatcher() -> CognitiveValidatorDispatcher:
    """获取全局默认验证调度器实例。"""
    global _DEFAULT_DISPATCHER
    if _DEFAULT_DISPATCHER is None:
        _DEFAULT_DISPATCHER = CognitiveValidatorDispatcher()
    return _DEFAULT_DISPATCHER


def reset_validator_dispatcher() -> None:
    """重置全局默认验证调度器实例。"""
    global _DEFAULT_DISPATCHER
    _DEFAULT_DISPATCHER = None
