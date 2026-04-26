import os
import sys

MODULE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core", "polaris_loop"))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from polaris.kernelone.context.engine import (  # noqa: E402
    ContextBudget,
    ContextEngine,
    ContextRequest,
)


def test_repo_map_provider_basic(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "class Foo:\n    def bar(self):\n        return 1\n\ndef baz():\n    return 2\n",
        encoding="utf-8",
    )

    engine = ContextEngine(project_root=str(tmp_path))
    request = ContextRequest(
        run_id="run_test",
        step=1,
        role="director",
        mode="test",
        query="",
        budget=ContextBudget(max_tokens=0, max_chars=0, cost_class="LOCAL"),
        sources_enabled=["repo_map"],
        policy={
            "repo_map_languages": ["python"],
            "repo_map_max_files": 10,
            "repo_map_max_lines": 50,
        },
        events_path="",
    )
    pack = engine.build_context(request)
    assert any(item.provider == "repo_map" for item in pack.items)
    repo_item = next(item for item in pack.items if item.provider == "repo_map")
    assert "class Foo" in repo_item.content_or_pointer
    assert "function baz" in repo_item.content_or_pointer
