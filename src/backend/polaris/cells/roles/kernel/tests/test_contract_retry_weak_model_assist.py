"""Weak-model assist in the mutation-contract retry (Blueprint A).

When a low-precision model narrates a fix but emits no write tool, the retry context
must (1) offer the easiest line-range edit path and (2) feed the model's own prior
analysis back with a transcribe-don't-re-explain instruction.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction import retry_orchestrator as _ro
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    _MAX_STALLED_READ_BOOTSTRAPS,
    _clear_read_bootstrap_progress,
    _extract_latest_assistant_message,
    _read_bootstrap_makes_no_progress,
    _workspace_materialization_fingerprint,
    build_contract_retry_context,
)

_EDIT_TOOL_DEFS = [
    {"name": "edit_blocks"},
    {"name": "read_file"},
]

_ANALYSIS = (
    "问题在 django/http/response.py 的 HttpResponse.__init__：未把 memoryview 转 bytes。"
    "应在 __init__ 中检测 memoryview 并调用 .tobytes()。"
)


def _context() -> list[dict]:
    return [
        {"role": "user", "content": "Fix the HttpResponse memoryview bug in django/http/response.py"},
        {"role": "assistant", "content": _ANALYSIS},
    ]


def test_extract_latest_assistant_message() -> None:
    assert _extract_latest_assistant_message(_context()) == _ANALYSIS
    assert _extract_latest_assistant_message([{"role": "user", "content": "x"}]) == ""


def test_retry_offers_line_range_easy_path() -> None:
    out = build_contract_retry_context(_context(), _EDIT_TOOL_DEFS, forced_write_tool_name="edit_blocks")
    system = next(m["content"] for m in out if m["role"] == "system")
    assert "start" in system and "end" in system
    assert "repo_read_slice" in system
    assert "edit_blocks" in system
    assert "MANDATORY" in system


def test_retry_feeds_back_prior_analysis_for_transcription() -> None:
    out = build_contract_retry_context(_context(), _EDIT_TOOL_DEFS)
    system = next(m["content"] for m in out if m["role"] == "system")
    assert "ALREADY analysed" in system
    assert "memoryview" in system  # the model's own plan is echoed back
    assert "Transcribe" in system


def test_retry_skips_easy_path_when_no_edit_blocks_tool() -> None:
    out = build_contract_retry_context(_context(), [{"name": "write_file"}, {"name": "read_file"}])
    system = next(m["content"] for m in out if m["role"] == "system")
    # line-range easy-path is edit_blocks-specific; should not appear here
    assert "EASIEST EDIT" not in system


def test_retry_no_analysis_when_assistant_absent() -> None:
    out = build_contract_retry_context([{"role": "user", "content": "Fix it in a.py"}], _EDIT_TOOL_DEFS)
    system = next(m["content"] for m in out if m["role"] == "system")
    assert "ALREADY analysed" not in system


# --- F24 progress-aware read-loop bound (2026-06-16) ------------------------


def test_unmeasurable_workspace_never_forces(tmp_path) -> None:
    # Safe default: "." / missing dir -> never force (== original behaviour).
    _ro._READ_BOOTSTRAP_PROGRESS.clear()
    for _ in range(5):
        assert _read_bootstrap_makes_no_progress("step-x", ".") is False
        assert _read_bootstrap_makes_no_progress("step-x", "/no/such/dir/zzz") is False


def test_stalled_reads_force_after_threshold(tmp_path) -> None:
    # Workspace never changes -> stall trips after _MAX_STALLED_READ_BOOTSTRAPS.
    _ro._READ_BOOTSTRAP_PROGRESS.clear()
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    ws = str(tmp_path)
    fired = [
        _read_bootstrap_makes_no_progress("step-stall", ws),  # 1: baseline, no stall yet
        _read_bootstrap_makes_no_progress("step-stall", ws),  # 2: stalled=1
        _read_bootstrap_makes_no_progress("step-stall", ws),  # 3: stalled=2 -> force
    ]
    assert fired == [False, False, True]
    assert _MAX_STALLED_READ_BOOTSTRAPS == 2


def test_new_materialization_resets_streak(tmp_path) -> None:
    # A write between reads (fingerprint changes) keeps it from ever forcing —
    # this is exactly why normal L2 read-then-write flows are not regressed.
    _ro._READ_BOOTSTRAP_PROGRESS.clear()
    ws = str(tmp_path)
    (tmp_path / "a.js").write_text("const a = 1;", encoding="utf-8")
    assert _read_bootstrap_makes_no_progress("step-prog", ws) is False  # baseline
    assert _read_bootstrap_makes_no_progress("step-prog", ws) is False  # stalled=1
    (tmp_path / "b.js").write_text("const b = 2;", encoding="utf-8")  # progress!
    assert _read_bootstrap_makes_no_progress("step-prog", ws) is False  # reset
    assert _read_bootstrap_makes_no_progress("step-prog", ws) is False  # stalled=1 again
    # only now, with no further writes, does it trip
    assert _read_bootstrap_makes_no_progress("step-prog", ws) is True


def test_clear_resets_progress(tmp_path) -> None:
    _ro._READ_BOOTSTRAP_PROGRESS.clear()
    ws = str(tmp_path)
    (tmp_path / "x.py").write_text("x=1", encoding="utf-8")
    _read_bootstrap_makes_no_progress("step-clr", ws)
    _read_bootstrap_makes_no_progress("step-clr", ws)
    _clear_read_bootstrap_progress("step-clr")
    assert _read_bootstrap_makes_no_progress("step-clr", ws) is False  # fresh baseline


def test_fingerprint_none_for_unmeasurable() -> None:
    assert _workspace_materialization_fingerprint(".") is None
    assert _workspace_materialization_fingerprint("") is None
    assert _workspace_materialization_fingerprint("/no/such/zzz") is None
