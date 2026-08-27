"""Unit tests for DEO mutation path collapse (R192/M03)."""

from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public import (
    build_tool_batch_lifecycle_receipt_from_sources,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    _collapse_last_write_wins_mutations,
    _mutation_target_path_key,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor._executor_execute import (
    _project_directed_effect_dropped_member_receipt,
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


def test_same_path_supersession_is_accounted_without_tool_failure() -> None:
    receipt = _project_directed_effect_dropped_member_receipt(
        dropped_member_rows=[
            ("c1", "edit_file", "deo_same_path_superseded_by_later_write"),
        ],
        batch_id="batch-1",
        turn_id="turn-1",
    )

    assert receipt["success_count"] == 1
    assert receipt["failure_count"] == 0
    assert receipt["results"] == [
        {
            "call_id": "c1",
            "tool_name": "edit_file",
            "status": "success",
            "result": {
                "ok": True,
                "no_op": True,
                "superseded": True,
                "reason": "deo_same_path_superseded_by_later_write",
            },
            "error": None,
            "effect_receipt": None,
            "directed_effect_claim_status": "not_claimed",
        }
    ]

    lifecycle = build_tool_batch_lifecycle_receipt_from_sources(
        run_id="run-1",
        task_id="task-1",
        turn_id="turn-1",
        role="director",
        decoded_tool_calls_count=1,
        receipts=[receipt],
    )
    assert lifecycle.ok is True
    assert not lifecycle.failure_class
    assert lifecycle.tool_result_count >= 1


def test_real_deo_soft_denial_remains_fail_closed_next_to_supersession() -> None:
    receipt = _project_directed_effect_dropped_member_receipt(
        dropped_member_rows=[
            ("c1", "edit_file", "deo_same_path_superseded_by_later_write"),
            ("c2", "write_file", "deo_director_policy_denied"),
        ],
        batch_id="batch-1",
        turn_id="turn-1",
    )

    assert receipt["success_count"] == 1
    assert receipt["failure_count"] == 1
    denied = receipt["results"][1]
    assert denied["status"] == "error"
    assert denied["result"]["error_type"] == "deo_member_soft_denied"
