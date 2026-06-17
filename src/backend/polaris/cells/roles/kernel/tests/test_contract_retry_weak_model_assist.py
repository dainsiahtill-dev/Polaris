"""Weak-model assist in the mutation-contract retry (Blueprint A).

When a low-precision model narrates a fix but emits no write tool, the retry context
must (1) offer the easiest line-range edit path and (2) feed the model's own prior
analysis back with a transcribe-don't-re-explain instruction.
"""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.transaction import retry_orchestrator as _ro
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    expand_bootstrap_read_candidates,
    extract_target_files_from_message,
)
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    _MAX_STALLED_READ_BOOTSTRAPS,
    _clear_read_bootstrap_progress,
    _extract_latest_assistant_message,
    _read_bootstrap_makes_no_progress,
    _should_bootstrap_original_read_batch,
    _workspace_materialization_fingerprint,
    append_retry_enforcement_hint,
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


def test_forced_write_file_enforcement_mentions_non_empty_complete_content() -> None:
    out = append_retry_enforcement_hint(
        [{"role": "user", "content": "Create src/app.py"}],
        allowed_tool_names={"write_file"},
        reason="mutation batch failed on argument shape",
        forced_write_tool_name="write_file",
    )
    system = out[-1]["content"]
    assert "write_file" in system
    assert "content" in system
    assert "non-empty" in system
    assert "complete file body" in system
    assert "prose" in system


def test_retry_enforcement_restates_target_files_not_acceptance_artifacts() -> None:
    out = append_retry_enforcement_hint(
        [
            {
                "role": "user",
                "content": (
                    "[mode:materialize]\n"
                    "范围: services/__init__.py\n"
                    "目标文件: services/__init__.py\n"
                    "验收标准:\n"
                    "- pytest -k test_create_app 通过\n"
                ),
            }
        ],
        allowed_tool_names={"edit_blocks"},
        reason="mutation batch failed on argument shape",
        forced_write_tool_name="edit_blocks",
    )

    system = out[-1]["content"]
    assert "services/__init__.py" in system
    assert "Only write these target files" in system
    assert "Acceptance" in system
    assert "not authorization" in system


def test_forced_edit_blocks_enforcement_includes_minimal_line_range_schema() -> None:
    out = append_retry_enforcement_hint(
        [
            {
                "role": "user",
                "content": (
                    "[mode:materialize]\n目标文件: services/__init__.py\n验收标准:\n- pytest -k test_create_app 通过\n"
                ),
            }
        ],
        allowed_tool_names={"edit_blocks"},
        reason="mutation batch failed on argument shape",
        forced_write_tool_name="edit_blocks",
    )

    system = out[-1]["content"]
    assert "edit_blocks" in system
    assert "file" in system
    assert "start" in system
    assert "end" in system
    assert "replace" in system
    assert "empty" in system
    assert "services/__init__.py" in system


def test_edit_blocks_validation_failure_enforcement_forbids_search_retry() -> None:
    out = append_retry_enforcement_hint(
        [
            {
                "role": "user",
                "content": (
                    "[mode:materialize]\n目标文件: services/__init__.py\n验收标准:\n- pytest -k test_create_app 通过\n"
                ),
            }
        ],
        allowed_tool_names={"edit_blocks"},
        reason=(
            "Validation failed 1 block(s). No files modified. Check that SEARCH text exactly matches file content."
        ),
        forced_write_tool_name="edit_blocks",
    )

    system = out[-1]["content"]
    assert "SEARCH" in system
    assert "Do not retry SEARCH" in system
    assert "line-range" in system
    assert "file" in system
    assert "start" in system
    assert "end" in system
    assert "replace" in system


def test_contract_retry_context_restates_target_files_not_acceptance_artifacts() -> None:
    out = build_contract_retry_context(
        [
            {
                "role": "user",
                "content": (
                    "[mode:materialize]\n"
                    "范围: services/__init__.py\n"
                    "目标文件: services/__init__.py\n"
                    "验收标准:\n"
                    "- pytest -k test_create_app 通过\n"
                ),
            },
            {"role": "assistant", "content": "I should create tests/test_services.py."},
        ],
        _EDIT_TOOL_DEFS,
        forced_write_tool_name="edit_blocks",
    )

    system = next(m["content"] for m in out if m["role"] == "system")
    assert "services/__init__.py" in system
    assert "Only write these target files" in system
    assert "acceptance criteria" in system
    assert "not authorization" in system


def test_extract_target_files_includes_pyproject_toml() -> None:
    message = "Create pyproject.toml, README.md, and infrastructure/config.py."

    targets = extract_target_files_from_message(message)

    assert "pyproject.toml" in targets
    assert "README.md" in targets
    assert "infrastructure/config.py" in targets


def test_retry_context_lists_all_detected_target_files_for_large_repair() -> None:
    targets = [
        "pyproject.toml",
        "readme.md",
        "infrastructure/__init__.py",
        "infrastructure/service_registry.py",
        "infrastructure/message_queue.py",
        "infrastructure/tracing.py",
        "infrastructure/base_service.py",
        "infrastructure/config.py",
        "run_all.py",
    ]
    out = build_contract_retry_context(
        [{"role": "user", "content": "Create missing files: " + ", ".join(targets)}],
        [{"name": "write_file"}, {"name": "read_file"}],
    )

    system = next(m["content"] for m in out if m["role"] == "system")
    for target in targets:
        assert target in system


# --- F24 progress-aware read-loop bound (2026-06-16) ------------------------


def test_original_read_bootstrap_allowed_without_single_target_marker(tmp_path) -> None:
    _ro._READ_BOOTSTRAP_PROGRESS.clear()
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

    assert _should_bootstrap_original_read_batch(
        context=[{"role": "user", "content": "Create src/styles.css"}],
        turn_id="step-normal-bootstrap",
        config=SimpleNamespace(workspace=str(tmp_path)),
        original_bootstrap_invocations=[{"tool_name": "repo_tree"}],
    )


def test_single_target_repair_blocks_original_read_bootstrap(tmp_path) -> None:
    _ro._READ_BOOTSTRAP_PROGRESS.clear()
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

    assert not _should_bootstrap_original_read_batch(
        context=[
            {
                "role": "user",
                "content": (
                    "SINGLE MISSING TARGET REPAIR:\n"
                    "[director_quality_repair:write_only_single_target]\n"
                    "- Target path: services/product_service/app.py\n"
                ),
            }
        ],
        turn_id="step-single-target",
        config=SimpleNamespace(workspace=str(tmp_path)),
        original_bootstrap_invocations=[{"tool_name": "repo_tree"}],
    )


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


def test_bootstrap_read_candidates_preserve_qualified_target_only() -> None:
    assert expand_bootstrap_read_candidates("services/__init__.py") == ["services/__init__.py"]
    assert expand_bootstrap_read_candidates("README.md") == ["README.md"]
