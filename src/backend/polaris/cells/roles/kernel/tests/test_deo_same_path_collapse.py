"""Unit tests for DEO mutation path collapse (R192/M03)."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    _collapse_last_write_wins_mutations,
    _mutation_target_path_key,
)
from polaris.cells.roles.kernel.public.turn_contracts import ToolEffectType, ToolInvocation


def _write(call_id: str, path: str, content: str) -> ToolInvocation:
    return ToolInvocation.model_validate(
        {
            "call_id": call_id,
            "raw_tool_name": "write_file",
            "tool_name": "write_file",
            "arguments": {"path": path, "content": content},
            "effect_type": ToolEffectType.WRITE,
        }
    )


def test_mutation_target_path_key_normalizes_dot_slash() -> None:
    inv = _write("c1", "./src/main.ts", "a")
    assert _mutation_target_path_key(inv) == "src/main.ts"


def test_collapse_last_write_wins_keeps_final_same_path_write() -> None:
    mutations = [
        _write("c1", "src/main.ts", "v1"),
        _write("c2", "src/other.ts", "x"),
        _write("c3", "./src/main.ts", "v2"),
        _write("c4", "README.md", "doc"),
    ]
    collapsed, dropped = _collapse_last_write_wins_mutations(mutations)
    assert [m.call_id for m in collapsed] == ["c2", "c3", "c4"]
    assert dropped == [("c1", "write_file", "deo_same_path_superseded_by_later_write")]
    kept_main = next(m for m in collapsed if m.call_id == "c3")
    assert kept_main.arguments["content"] == "v2"
