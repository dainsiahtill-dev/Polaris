"""Directive-bound advisory semantics; controls are never product modules."""

from hashlib import sha256

import pytest
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter


def analyzer():
    return PMAdapter.__new__(PMAdapter)


def product(directive):
    return {
        "schema_version": "pm.product_planning_input.v1",
        "source": "factory.catalog_contract",
        "directive_sha256": sha256(directive.encode("utf-8")).hexdigest(),
        "project_id": "L1-01",
        "title": "发光昆虫花园模拟器",
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "feature_keywords": ["firefly", "flower", "moon", "humidity"],
        "test_focus": "验证核心领域规则和边界行为",
    }


def test_product_analysis_invariant_under_control_wrapper_growth():
    instance = analyzer()
    directives = [
        "实现花园模拟器",
        "实现花园模拟器\nmanifest specific implement build test schema\n" + "src/engine/renderer.ts /a/b /c/d\n" * 30,
    ]
    results = [
        instance._analyze_directive_complexity(d, {"metadata": {"pm_product_planning_input": product(d)}})
        for d in directives
    ]
    assert results[0] == results[1]
    assert results[0]["analysis_source"] == "factory.catalog_contract"
    assert results[0]["target_files_hint"] == 0
    assert results[0]["estimated_task_count"] >= 3


@pytest.mark.parametrize(
    "text,conditional", [("manifest specific", False), ("if rain", True), ("when wet", True), ("如果下雨", True)]
)
def test_word_safe_conditions(text, conditional):
    analysis = analyzer()._analyze_directive_complexity(text, {})
    assert analysis["has_conditional"] is conditional


def test_stale_projection_is_advisory_unavailable():
    analysis = analyzer()._analyze_directive_complexity(
        "ordinary documentation request", {"pm_product_planning_input": product("other")}
    )
    assert analysis["analysis_source"] == "directive"


def test_product_change_changes_analysis_and_native_guidance():
    instance = analyzer()
    directive = "Ordinary independent documentation and implementation request"
    bound = product(directive)
    small = instance._analyze_directive_complexity(directive, {"pm_product_planning_input": bound})
    bound["creative_hook"] = (
        "Implement API database authentication frontend backend deployment schema. If wet iterate build define. " * 20
    )
    large = instance._analyze_directive_complexity(directive, {"pm_product_planning_input": bound})
    assert small["complexity"] != large["complexity"]
    factory_message = instance._build_pm_message([], directive, directive_analysis=large)
    ordinary_message = instance._build_pm_message(
        [], directive, directive_analysis=instance._analyze_directive_complexity(directive, {})
    )
    assert "原生 PM/Chief Engineer" in factory_message
    assert "原生 PM/Chief Engineer" not in ordinary_message
    assert directive in factory_message and directive in ordinary_message
