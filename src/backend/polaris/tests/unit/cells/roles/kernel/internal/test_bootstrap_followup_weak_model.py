"""Phase-2 wave-2: bootstrap follow-up must be transcribable, durable, and non-destructive.

Covers the django-11630 live findings (2026-06-10):
- bootstrap read content was truncated to a 1200-char JSON fragment, making correct
  SEARCH/REPLACE transcription impossible by construction;
- bootstrap read results never reached the session event stream, so later turns had
  no trace the file was ever read (models then hallucinate SEARCH text from memory);
- the deterministic write_file fallback planted scaffolding stubs (main.py) for
  targets the user never named, reinforcing weak-model task drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    RetryOrchestrator,
    build_deterministic_bootstrap_followup_write_decision,
    build_retry_write_after_bootstrap_context,
    merge_bootstrap_receipt_into_result,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    _recent_edit_failure_in_context,
)

# ---------------------------------------------------------------------------
# build_retry_write_after_bootstrap_context — real content must be transcribable
# ---------------------------------------------------------------------------


def _receipt_with_content(content: str) -> dict[str, Any]:
    return {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": "django/core/checks/model_checks.py", "content": content},
                "arguments": {"file": "django/core/checks/model_checks.py"},
            }
        ]
    }


def test_bootstrap_context_carries_real_file_content_beyond_old_fragment_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Transcription fidelity is tested with a generous budget (a large-window
    # provider); the I3-r22 window-sized default is exercised separately below.
    monkeypatch.setenv("KERNELONE_BOOTSTRAP_READ_TOTAL_CHARS", "16000")
    monkeypatch.setenv("KERNELONE_BOOTSTRAP_READ_MAX_CHARS", "9000")
    content = "\n".join(f"line {i}: db_table_models[model._meta.db_table].append(x)" for i in range(120))
    assert len(content) > 1200  # would have been truncated to a fragment before
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "fix models.E028 in the checks module"}],
        bootstrap_receipt=_receipt_with_content(content),
        forced_write_tool_name="edit_blocks",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "line 119:" in rendered  # full content present, not the 1200-char fragment
    assert "exact file content follows" in rendered


def test_bootstrap_content_window_sized_default_caps_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    """I3-r22 (F10): the default read-content budget is small (sized for a 16k
    local-Director window) so a large transcription payload cannot collapse the
    output budget. 120 lines (~6k chars) is truncated under the default."""
    monkeypatch.delenv("KERNELONE_BOOTSTRAP_READ_TOTAL_CHARS", raising=False)
    monkeypatch.delenv("KERNELONE_BOOTSTRAP_READ_MAX_CHARS", raising=False)
    content = "\n".join(f"line {i}: db_table_models[model._meta.db_table].append(x)" for i in range(120))
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "fix it"}],
        bootstrap_receipt=_receipt_with_content(content),
        forced_write_tool_name="edit_blocks",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "[content truncated]" in rendered  # capped under the small default
    assert "line 0:" in rendered  # the head is still transcribable
    assert "line 119:" not in rendered  # tail dropped to protect the output budget


def test_bootstrap_context_unwraps_executor_envelope() -> None:
    content = "def real_function():\n    return 42\n"
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                # executor envelope shape: {"ok": ..., "result": {...}}
                "result": {"ok": True, "result": {"file": "a.py", "content": content}},
                "arguments": {"file": "a.py"},
            }
        ]
    }
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "fix a.py"}],
        bootstrap_receipt=receipt,
        forced_write_tool_name="edit_blocks",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "def real_function():" in rendered


def test_bootstrap_context_caps_giant_content() -> None:
    content = "x" * 50_000
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "fix it"}],
        bootstrap_receipt=_receipt_with_content(content),
        forced_write_tool_name="edit_blocks",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "[content truncated]" in rendered
    assert len(rendered) < 30_000


def test_bootstrap_context_non_content_results_keep_fragment_form() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "repo_rg",
                "status": "success",
                "result": {"returned_count": 3, "results": ["a", "b", "c"]},
                "arguments": {"pattern": "E028"},
            }
        ]
    }
    context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "fix it"}],
        bootstrap_receipt=receipt,
        forced_write_tool_name="edit_blocks",
    )
    rendered = "\n".join(str(m.get("content") or "") for m in context)
    assert "returned_count" in rendered
    assert "exact file content follows" not in rendered


# ---------------------------------------------------------------------------
# deterministic write_file fallback — never stomp, never invent
# ---------------------------------------------------------------------------


def _failed_read_receipt(path: str) -> dict[str, Any]:
    return {
        "results": [
            {
                "tool_name": "read_file",
                "status": "error",
                "result": {"error": f"File not found: {path}"},
                "arguments": {"file": path},
            }
        ]
    }


def test_deterministic_fallback_skips_target_not_named_by_user(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-1",
        original_context=[{"role": "user", "content": "fix the db_table checks bug"}],
        bootstrap_receipt=_failed_read_receipt("main.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is None


def test_deterministic_fallback_skips_existing_file(tmp_path: Path) -> None:
    (tmp_path / "demo_app.py").write_text("real_source = True\n", encoding="utf-8")
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-2",
        original_context=[{"role": "user", "content": "please create demo_app.py with an entry point"}],
        bootstrap_receipt=_failed_read_receipt("demo_app.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is None


def test_deterministic_fallback_fires_for_user_named_new_file(tmp_path: Path) -> None:
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-3",
        original_context=[{"role": "user", "content": "please create demo_app.py with an entry point"}],
        bootstrap_receipt=_failed_read_receipt("demo_app.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is not None
    assert decision.metadata.get("target_file") == "demo_app.py"


# ---------------------------------------------------------------------------
# I3-r21 root fix: leaf-construction suppression (rank 2) + refuse-to-guess (rank 1)
# ---------------------------------------------------------------------------


def _leaf_step_context(target_file: str, *, verify: str, named_files: str) -> list[dict[str, Any]]:
    """A turn context carrying a CE construction-step card (a leaf step execution)."""
    return [
        {"role": "user", "content": f"build the game across {named_files}"},
        {
            "role": "user",
            "content": "execute the construction step",
            "context_override": {
                "construction_step": {
                    "step_id": "PM-0001-1-S4",
                    "target_file": target_file,
                    "verify": verify,
                }
            },
        },
    ]


def test_leaf_construction_step_suppresses_write_fallback(tmp_path: Path) -> None:
    """A leaf step naming many files must NOT plant a stub for ANY of them — the
    placeholder can never satisfy the real verify and only poisons the owner.
    This is the live r21 main.js clobber: S4(readme.md) executing wrote main.js.
    """
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="pm-00001",
        original_context=_leaf_step_context(
            "readme.md",
            verify="test -f main.js && node --check main.js && grep -q 'class Paddle' main.js",
            named_files="index.html style.css main.js readme.md",
        ),
        bootstrap_receipt=_failed_read_receipt("index.html"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is None


def test_non_leaf_refuses_to_guess_among_multiple_targets(tmp_path: Path) -> None:
    """Without a declared single target, >1 user-named new file is ambiguous;
    picking viable_targets[0] is the wrong-file bug. Refuse rather than guess."""
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-multi",
        original_context=[
            {"role": "user", "content": "please create index.html and main.js and style.css for me"}
        ],
        bootstrap_receipt=_failed_read_receipt("index.html"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is None


def test_non_leaf_single_target_still_fires(tmp_path: Path) -> None:
    """The legitimate non-leaf scaffold case (exactly one user-named new file)
    is unchanged — the rank-1 guard only refuses when the choice is ambiguous."""
    decision = build_deterministic_bootstrap_followup_write_decision(
        turn_id="t-single",
        original_context=[{"role": "user", "content": "please create config_app.py with an entry point"}],
        bootstrap_receipt=_failed_read_receipt("config_app.py"),
        allowed_tool_names={"write_file"},
        workspace=str(tmp_path),
    )
    assert decision is not None
    assert decision.metadata.get("target_file") == "config_app.py"


# ---------------------------------------------------------------------------
# verification-read exemption helper
# ---------------------------------------------------------------------------


def test_recent_edit_failure_detected_in_context_tail() -> None:
    context = [
        {"role": "user", "content": "fix the bug"},
        {"role": "assistant", "content": "**edit_blocks**: Error - Validation failed for 1 block(s)."},
        {"role": "user", "content": "continue"},
    ]
    assert _recent_edit_failure_in_context(context) is True


def test_recent_edit_failure_respects_lookback() -> None:
    context = [{"role": "assistant", "content": "No valid edit blocks found in input"}] + [
        {"role": "user", "content": f"turn {i}"} for i in range(10)
    ]
    assert _recent_edit_failure_in_context(context, lookback=8) is False


def test_recent_edit_failure_handles_non_list_context() -> None:
    assert _recent_edit_failure_in_context(None) is False
    assert _recent_edit_failure_in_context(123) is False
    assert _recent_edit_failure_in_context([{"role": "user", "content": "all good"}]) is False


# ---------------------------------------------------------------------------
# execute_read_bootstrap_batch — receipts must reach the event stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_reads_emit_tool_result_events() -> None:
    emitted: list[Any] = []
    orchestrator = RetryOrchestrator(
        tool_runtime=MagicMock(),
        config=MagicMock(max_tool_execution_time_ms=60000, max_retry_attempts=4),
        decoder=MagicMock(),
        call_llm_for_decision=AsyncMock(),
        call_llm_for_decision_stream=None,
        execute_tool_batch=AsyncMock(),
        guard_assert_single_tool_batch=MagicMock(),
        emit_event=emitted.append,
    )

    receipt = {
        "batch_id": "b-1",
        "turn_id": "t-boot",
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": "a.py", "content": "x = 1\n"},
                "arguments": {"file": "a.py"},
            }
        ],
        "success_count": 1,
        "failure_count": 0,
    }
    runtime = MagicMock()
    runtime.execute_batch = AsyncMock(return_value=[receipt])
    orchestrator._build_tool_batch_runtime = MagicMock(return_value=runtime)  # type: ignore[method-assign]

    merged = await orchestrator.execute_read_bootstrap_batch(
        turn_id="t-boot",
        workspace=".",
        tool_batch={
            "invocations": [
                {
                    "call_id": "c1",
                    "tool_name": "read_file",
                    "arguments": {"file": "a.py"},
                }
            ]
        },
        ledger=TurnLedger(turn_id="t-boot"),
    )

    assert merged is not None
    tool_result_events = [e for e in emitted if isinstance(e, dict) and e.get("type") == "tool_result"]
    assert len(tool_result_events) == 1
    data = tool_result_events[0]["data"]
    assert data["tool"] == "read_file"
    assert data["bootstrap_read"] is True
    assert data["result"]["content"] == "x = 1\n"


# ---------------------------------------------------------------------------
# merge_bootstrap_receipt_into_result — bootstrap reads must reach the envelope
# ---------------------------------------------------------------------------


def _bootstrap_receipt() -> dict[str, Any]:
    return {
        "batch_id": "b-boot",
        "turn_id": "t-merge",
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": "a.py", "content": "x = 1\n"},
                "arguments": {"file": "a.py"},
            }
        ],
        "success_count": 1,
        "failure_count": 0,
    }


def test_merge_prepends_bootstrap_reads_into_existing_receipt() -> None:
    result = {
        "kind": "tool_batch_with_receipt",
        "batch_receipt": {
            "results": [{"tool_name": "write_file", "status": "success", "result": {"file": "a.py"}}],
            "success_count": 1,
        },
    }
    merged = merge_bootstrap_receipt_into_result(result, _bootstrap_receipt())
    tools = [item["tool_name"] for item in merged["batch_receipt"]["results"]]
    assert tools == ["read_file", "write_file"]  # reads first, then the write
    assert merged["batch_receipt"]["success_count"] == 2


def test_merge_sets_receipt_when_result_has_none() -> None:
    result = {"kind": "tool_batch_with_receipt", "batch_receipt": None}
    merged = merge_bootstrap_receipt_into_result(result, _bootstrap_receipt())
    assert merged["batch_receipt"]["results"][0]["tool_name"] == "read_file"


def test_merge_leaves_non_dict_result_untouched() -> None:
    sentinel = object()
    assert merge_bootstrap_receipt_into_result(sentinel, _bootstrap_receipt()) is sentinel
    plain = {"kind": "x"}
    assert merge_bootstrap_receipt_into_result(plain, None) is plain
