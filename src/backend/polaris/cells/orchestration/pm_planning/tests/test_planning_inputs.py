"""Pure product projection does not grant execution authority."""

import pytest
from polaris.cells.orchestration.pm_planning.public import contracts
from polaris.cells.roles.adapters.tests.test_pm_planning_context import product


def test_public_roundtrip_preserves_scalar_focus_and_exact_binding():
    directive = "花园控制示例 src/engine/renderer.ts"
    item = contracts.parse_pm_product_planning_input(product(directive), directive=directive)
    assert item is not None
    assert isinstance(item, contracts.PmProductPlanningInputV1)
    assert item.test_focus == "验证核心领域规则和边界行为"
    assert item.feature_keywords == ("firefly", "flower", "moon", "humidity")
    assert contracts.parse_pm_product_planning_input(item.to_dict(), directive=directive) == item
    assert contracts.parse_pm_product_planning_input(item.to_dict(), directive=directive + "x") is None
    assert "renderer.ts" not in item.semantic_text()


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema_version", "bad"),
        ("source", "caller"),
        ("directive_sha256", "x"),
        ("test_focus", []),
        ("feature_keywords", ["firefly", 1]),
        ("creative_hook", ""),
        ("primary_language", 1),
    ],
)
def test_reject_malformed_fields(key, value):
    payload = product("directive")
    payload[key] = value
    assert contracts.parse_pm_product_planning_input(payload, directive="directive") is None


@pytest.mark.parametrize("payload", [None, {}, [], "ordinary"])
def test_non_catalog_unavailable(payload):
    assert contracts.parse_pm_product_planning_input(payload, directive="ordinary") is None
