import os
import sys

MODULE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core", "polaris_loop"))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from polaris.cells.context.engine.public.precision_mode import (  # noqa: E402
    resolve_cost_class,
    route_by_cost_model,
)


def test_resolve_cost_class_env(monkeypatch):
    monkeypatch.setenv("POLARIS_COST_MODEL", "fixed")
    assert resolve_cost_class() == "FIXED"
    monkeypatch.setenv("POLARIS_COST_MODEL", "metered")
    assert resolve_cost_class() == "METERED"


def test_route_by_cost_model_metered():
    local = route_by_cost_model("LOCAL", "director")
    metered = route_by_cost_model("METERED", "director")
    assert metered.cost_class == "METERED"
    assert metered.budget["max_tokens"] < local.budget["max_tokens"]
    assert "repo_map" not in metered.sources_enabled
    assert metered.policy.get("memory_refs_required") is True
