"""Weak-model assist in the mutation-contract retry (Blueprint A).

When a low-precision model narrates a fix but emits no write tool, the retry context
must (1) offer the easiest line-range edit path and (2) feed the model's own prior
analysis back with a transcribe-don't-re-explain instruction.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    _extract_latest_assistant_message,
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
