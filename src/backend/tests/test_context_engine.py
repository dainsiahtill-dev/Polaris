import os
import sys

MODULE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core", "polaris_loop"))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from polaris.kernelone.context.engine import (  # noqa: E402
    ContextBudget,
    ContextEngine,
    ContextItem,
)


def test_budget_ladder_pointerize(tmp_path):
    engine = ContextEngine(project_root=str(tmp_path))
    items = [
        ContextItem(
            kind="docs",
            content_or_pointer="A" * 200,
            refs={"path": "docs/a.md"},
            priority=5,
            provider="docs",
        ),
        ContextItem(
            kind="docs",
            content_or_pointer="B" * 200,
            refs={"path": "docs/a.md"},
            priority=1,
            provider="docs",
        ),
        ContextItem(
            kind="contract",
            content_or_pointer="C" * 200,
            refs={"path": "contract.json"},
            priority=9,
            provider="contract",
        ),
    ]
    budget = ContextBudget(max_tokens=0, max_chars=100, cost_class="LOCAL")
    final_items, log = engine._apply_budget_ladder(items, budget)

    actions = [entry.get("action") for entry in log]
    assert "deduplicate" in actions
    assert "pointerize" in actions
    assert any(item.kind == "pointer" for item in final_items)
    total_chars = sum(len(item.content_or_pointer or "") for item in final_items)
    assert total_chars <= budget.max_chars
