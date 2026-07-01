from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.post_execution_repair_bridge import (
    run_cpp_post_repairs_as_tool_results,
)


def test_cpp_post_repair_without_director_adapter_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    target = workspace / "src" / "engine" / "generator.cpp"
    target.parent.mkdir(parents=True)
    original = '#include "src/models/postcard.hpp"\n#include <string>\n'
    target.write_text(original, encoding="utf-8")

    results = run_cpp_post_repairs_as_tool_results(
        workspace,
        adapter=None,
        task_id="task-without-adapter",
    )

    assert target.read_text(encoding="utf-8") == original
    assert len(results) == 1
    assert results[0]["success"] is False
    payload = results[0]["result"]
    assert payload["ok"] is False
    assert payload["source_tool"] == "deterministic_cpp_post_repair"
    assert payload["error_code"] == "director_adapter_required_for_policy_gated_repair"
    assert payload["repair_kernel"]["owner_cell"] == "director.runtime"
    assert payload["repair_kernel"]["direct_write_allowed"] is False
    assert payload["repair_kernel"]["writer_boundary"] == "director_tool_executor_required"
