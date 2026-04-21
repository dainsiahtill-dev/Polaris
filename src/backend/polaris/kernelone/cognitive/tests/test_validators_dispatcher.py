"""Tests for Domain Isolation Layer — CognitiveValidatorDispatcher."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.validators.dispatcher import (
    CognitiveValidatorDispatcher,
    GenerationDomain,
    ValidationConfig,
    ValidationSeverity,
    ValidationViolation,
    _infer_domain_from_content,
    _infer_domain_from_extension,
    get_validator_dispatcher,
    reset_validator_dispatcher,
)

# -----------------------------------------------------------------------------
# Extension-based routing
# -----------------------------------------------------------------------------


class TestInferDomainFromExtension:
    """文件扩展名 → GenerationDomain 路由测试。"""

    @pytest.mark.parametrize(
        ("ext", "expected"),
        [
            # UI_COMPONENT
            ("Button.tsx", GenerationDomain.UI_COMPONENT),
            ("App.vue", GenerationDomain.UI_COMPONENT),
            ("styles.scss", GenerationDomain.UI_COMPONENT),
            ("index.html", GenerationDomain.UI_COMPONENT),
            ("page.svelte", GenerationDomain.UI_COMPONENT),
            ("template.jinja", GenerationDomain.UI_COMPONENT),
            ("component.jsx", GenerationDomain.UI_COMPONENT),
            ("theme.css", GenerationDomain.UI_COMPONENT),
            # CORE_LOGIC — 绝不进入 taste-skill
            ("main.py", GenerationDomain.CORE_LOGIC),
            ("server.go", GenerationDomain.CORE_LOGIC),
            ("lib.rs", GenerationDomain.CORE_LOGIC),
            ("App.java", GenerationDomain.CORE_LOGIC),
            ("main.c", GenerationDomain.CORE_LOGIC),
            ("handler.cpp", GenerationDomain.CORE_LOGIC),
            # DATA_PROCESSING
            ("query.sql", GenerationDomain.DATA_PROCESSING),
            # DOCUMENTATION
            ("README.md", GenerationDomain.DOCUMENTATION),
            ("config.yaml", GenerationDomain.DOCUMENTATION),
            # DESIGN_SPEC
            ("tokens.design", GenerationDomain.DESIGN_SPEC),
            # UNKNOWN
            ("data.bin", GenerationDomain.UNKNOWN),
            ("archive.tar.gz", GenerationDomain.UNKNOWN),
        ],
    )
    def test_routing(self, ext: str, expected: GenerationDomain) -> None:
        assert _infer_domain_from_extension(ext) is expected

    def test_path_object(self) -> None:
        from pathlib import Path

        assert _infer_domain_from_extension(Path("src/components/App.tsx")) is GenerationDomain.UI_COMPONENT
        assert _infer_domain_from_extension(Path("backend/main.py")) is GenerationDomain.CORE_LOGIC


# -----------------------------------------------------------------------------
# Content-based heuristic routing
# -----------------------------------------------------------------------------


class TestInferDomainFromContent:
    """内容启发式路由测试。"""

    def test_ui_component_heuristic(self) -> None:
        content = (
            "import React from 'react';\nexport default function Button() { return <div className='btn'>OK</div>; }"
        )
        assert _infer_domain_from_content(content) is GenerationDomain.UI_COMPONENT

    def test_tailwind_heuristic(self) -> None:
        content = "@tailwind base;\n@layer components { .btn { @apply px-4 py-2; } }"
        assert _infer_domain_from_content(content) is GenerationDomain.UI_COMPONENT

    def test_core_logic_heuristic(self) -> None:
        content = "def hello():\n    return 'world'\n\nif __name__ == '__main__':\n    print(hello())"
        assert _infer_domain_from_content(content) is GenerationDomain.CORE_LOGIC

    def test_data_processing_heuristic(self) -> None:
        content = "SELECT id, name FROM users WHERE active = 1;"
        assert _infer_domain_from_content(content) is GenerationDomain.DATA_PROCESSING

    def test_empty_content(self) -> None:
        assert _infer_domain_from_content("") is GenerationDomain.UNKNOWN

    def test_ambiguous_content(self) -> None:
        # 没有足够标记 → UNKNOWN
        content = "hello world"
        assert _infer_domain_from_content(content) is GenerationDomain.UNKNOWN

    def test_mixed_content_prefers_best_match(self) -> None:
        # 同时包含 React 和 Python 标记，但 React 标记更多
        content = "import React from 'react';\nimport { useState } from 'react';\n<div className='app'>\n def helper() {}\n</div>"
        result = _infer_domain_from_content(content)
        # React 标记有 4 个，Python 只有 1 个 → UI_COMPONENT
        assert result is GenerationDomain.UI_COMPONENT


# -----------------------------------------------------------------------------
# CognitiveValidatorDispatcher
# -----------------------------------------------------------------------------


class TestResolveDomain:
    """三层覆盖逻辑测试。"""

    def test_config_override_priority(self) -> None:
        dispatcher = CognitiveValidatorDispatcher()
        # 即使文件是 .py，config.target_domain 显式覆盖为 UI_COMPONENT
        config = ValidationConfig(target_domain=GenerationDomain.UI_COMPONENT)
        domain = dispatcher.resolve_domain("main.py", config=config)
        assert domain is GenerationDomain.UI_COMPONENT

    def test_extension_priority_over_content(self) -> None:
        dispatcher = CognitiveValidatorDispatcher()
        # .tsx 扩展名优先于 Python-like 内容
        content = "def hello(): pass"
        domain = dispatcher.resolve_domain("Button.tsx", content=content)
        assert domain is GenerationDomain.UI_COMPONENT

    def test_content_fallback(self) -> None:
        dispatcher = CognitiveValidatorDispatcher()
        # 无扩展名时，内容启发式生效
        content = "import React from 'react';\nimport { useState } from 'react';\nexport default function App() { return <div className='app'>Hello</div>; }"
        domain = dispatcher.resolve_domain(content=content)
        assert domain is GenerationDomain.UI_COMPONENT

    def test_unknown_when_no_info(self) -> None:
        dispatcher = CognitiveValidatorDispatcher()
        domain = dispatcher.resolve_domain()
        assert domain is GenerationDomain.UNKNOWN


class TestValidateDomainIsolation:
    """域隔离核心保证测试。"""

    def setup_method(self) -> None:
        self.dispatcher = CognitiveValidatorDispatcher()
        self.dispatcher.reset_stats()

    def test_bypass_taste_escape_hatch(self) -> None:
        config = ValidationConfig(bypass_taste=True)
        violations = self.dispatcher.validate(
            file_path="Button.tsx",
            content="const btn = <div>OK</div>;",
            config=config,
        )
        assert violations == []
        assert self.dispatcher.get_stats()["bypassed"] == 1

    def test_ui_component_goes_through(self) -> None:
        # UI_COMPONENT 应该进入验证链路（当前 P0-B/P0-C 占位返回空）
        violations = self.dispatcher.validate(
            file_path="Button.tsx",
            content="const btn = <div>OK</div>;",
        )
        assert violations == []
        assert self.dispatcher.get_stats()["checked"] == 1

    def test_design_spec_goes_through(self) -> None:
        violations = self.dispatcher.validate(
            file_path="tokens.design",
            content='{"primary": "#18181B"}',
        )
        assert violations == []
        assert self.dispatcher.get_stats()["checked"] == 1

    def test_core_logic_bypassed(self) -> None:
        violations = self.dispatcher.validate(
            file_path="main.py",
            content="def hello(): return 'world'",
        )
        assert violations == []
        assert self.dispatcher.get_stats()["bypassed"] == 1
        assert self.dispatcher.get_stats()["checked"] == 0

    def test_data_processing_bypassed(self) -> None:
        violations = self.dispatcher.validate(
            file_path="query.sql",
            content="SELECT * FROM users;",
        )
        assert violations == []
        assert self.dispatcher.get_stats()["bypassed"] == 1

    def test_documentation_bypassed(self) -> None:
        violations = self.dispatcher.validate(
            file_path="README.md",
            content="# Project\n\nThis is a test.",
        )
        assert violations == []
        assert self.dispatcher.get_stats()["bypassed"] == 1

    def test_unknown_domain_bypassed(self) -> None:
        violations = self.dispatcher.validate(
            file_path="data.bin",
            content="binary data",
        )
        assert violations == []
        assert self.dispatcher.get_stats()["bypassed"] == 1

    def test_inline_no_path_bypassed_when_ambiguous(self) -> None:
        # 没有 file_path，内容也不明确 → UNKNOWN → bypass
        violations = self.dispatcher.validate(content="hello world")
        assert violations == []
        assert self.dispatcher.get_stats()["bypassed"] == 1

    def test_allow_partial_skips_completeness(self) -> None:
        # allow_partial=True 时跳过完整性检查（当前占位无影响）
        config = ValidationConfig(allow_partial=True)
        violations = self.dispatcher.validate(
            file_path="Button.tsx",
            content="const btn = <div>OK</div>;",
            config=config,
        )
        assert violations == []


class TestValidateBatch:
    """批量验证测试。"""

    def test_batch_mixed_domains(self) -> None:
        dispatcher = CognitiveValidatorDispatcher()
        items = [
            ("Button.tsx", "const btn = <div>OK</div>;"),
            ("main.py", "def hello(): pass"),
            ("README.md", "# Hello"),
            (
                None,
                "import React from 'react';\nimport { useState } from 'react';\nexport default function App() { return <div className='app'>Hello</div>; }",
            ),  # inline, no path
        ]
        results = dispatcher.validate_batch(items)

        # Button.tsx → checked
        assert results["Button.tsx"] == []
        # main.py → bypassed
        assert results["main.py"] == []
        # README.md → bypassed
        assert results["README.md"] == []
        # inline → UI_COMPONENT via content heuristic → checked
        assert results["__inline__"] == []

        stats = dispatcher.get_stats()
        assert stats["total_calls"] == 4
        assert stats["checked"] == 2  # Button.tsx + inline
        assert stats["bypassed"] == 2  # main.py + README.md


class TestStats:
    """统计接口测试。"""

    def test_stats_tracking(self) -> None:
        dispatcher = CognitiveValidatorDispatcher()
        dispatcher.reset_stats()

        assert dispatcher.get_stats() == {"total_calls": 0, "bypassed": 0, "checked": 0}

        dispatcher.validate(file_path="Button.tsx", content="<div />")
        dispatcher.validate(file_path="main.py", content="def f(): pass")
        dispatcher.validate(file_path="query.sql", content="SELECT 1")

        stats = dispatcher.get_stats()
        assert stats["total_calls"] == 3
        assert stats["checked"] == 1
        assert stats["bypassed"] == 2

    def test_reset_stats(self) -> None:
        dispatcher = CognitiveValidatorDispatcher()
        dispatcher.validate(file_path="Button.tsx", content="<div />")
        dispatcher.reset_stats()
        assert dispatcher.get_stats()["total_calls"] == 0


# -----------------------------------------------------------------------------
# ValidationConfig / ValidationViolation
# -----------------------------------------------------------------------------


class TestValidationConfig:
    """ValidationConfig frozen dataclass 测试。"""

    def test_defaults(self) -> None:
        config = ValidationConfig()
        assert config.bypass_taste is False
        assert config.allow_partial is False
        assert config.design_dials is None
        assert config.target_domain is None

    def test_custom_values(self) -> None:
        config = ValidationConfig(
            bypass_taste=True,
            allow_partial=True,
            target_domain=GenerationDomain.UI_COMPONENT,
        )
        assert config.bypass_taste is True
        assert config.allow_partial is True
        assert config.target_domain is GenerationDomain.UI_COMPONENT

    def test_frozen(self) -> None:
        config = ValidationConfig()
        with pytest.raises(AttributeError):
            config.bypass_taste = True  # type: ignore[misc]


class TestValidationViolation:
    """ValidationViolation NamedTuple 测试。"""

    def test_creation(self) -> None:
        v = ValidationViolation(
            rule="banned_font",
            severity=ValidationSeverity.ERROR,
            message="Banned font 'Inter' detected.",
            location="styles.css:42",
            domain=GenerationDomain.UI_COMPONENT,
            fix_hint="Replace with 'Geist'.",
        )
        assert v.rule == "banned_font"
        assert v.severity is ValidationSeverity.ERROR
        assert v.location == "styles.css:42"
        assert v.domain is GenerationDomain.UI_COMPONENT
        assert v.fix_hint == "Replace with 'Geist'."

    def test_optional_fields(self) -> None:
        v = ValidationViolation(
            rule="placeholder",
            severity=ValidationSeverity.WARNING,
            message="Placeholder text detected.",
        )
        assert v.location is None
        assert v.domain is None
        assert v.fix_hint is None


# -----------------------------------------------------------------------------
# Global dispatcher singleton
# -----------------------------------------------------------------------------


class TestGlobalDispatcher:
    """全局调度器单例测试。"""

    def test_singleton(self) -> None:
        reset_validator_dispatcher()
        d1 = get_validator_dispatcher()
        d2 = get_validator_dispatcher()
        assert d1 is d2

    def test_reset_creates_new_instance(self) -> None:
        d1 = get_validator_dispatcher()
        reset_validator_dispatcher()
        d2 = get_validator_dispatcher()
        assert d1 is not d2
