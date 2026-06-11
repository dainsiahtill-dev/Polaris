"""Tests for the deterministic SWE-bench failure-mode labeler."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.swebench_failure_taxonomy import (
    TAXONOMY_SCHEMA_VERSION,
    aggregate_labels,
    label_session,
    read_events,
)


def _diff(path: str, added: int, removed: int, new_file: bool = False) -> str:
    body = "".join("+x\n" for _ in range(added)) + "".join("-y\n" for _ in range(removed))
    mode = "new file mode 100644\n" if new_file else ""
    return f"diff --git a/{path} b/{path}\n{mode}--- a/{path}\n+++ b/{path}\n@@ -1,{max(removed, 1)} +1,{max(added, 1)} @@\n{body}"


def _tool_result(tool: str, *, error: str = "", ok: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": ok}
    if error:
        result["error"] = error
        result["ok"] = False
    return {"type": "tool_result", "data": {"tool": tool, "result": result}}


def _tool_call(tool: str, **args: Any) -> dict[str, Any]:
    return {"type": "tool_call", "data": {"tool": tool, "args": args}}


def _complete_with_receipt(items: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    return {
        "type": "complete",
        "data": {
            "content": "done",
            "batch_receipt": {
                "batch_id": "b1",
                "results": [
                    {"call_id": f"c{i}", "tool_name": tool, "status": "success", "result": result}
                    for i, (tool, result) in enumerate(items)
                ],
            },
        },
    }


GOLD = _diff("pkg/real_target.py", 3, 1)


class TestPhantomAndSuggestions:
    def test_phantom_paths_and_hallucination_label(self) -> None:
        events = [
            _tool_result("read_file", error="File not found: src/main.py. Did you mean: pkg/other/main.py?"),
            _tool_result("read_file", error="File not found: README.md"),
            _tool_result("read_file", error="File not found: app.py"),
        ]
        record = label_session(events, model_patch="", gold_patch=GOLD)
        assert record["counters"]["phantom_paths"] == ["README.md", "app.py", "src/main.py"]
        assert record["counters"]["did_you_mean_suggestions"] == ["pkg/other/main.py"]
        assert "path_hallucination" in record["labels"]

    def test_below_threshold_not_labeled(self) -> None:
        events = [_tool_result("read_file", error="File not found: README.md")]
        record = label_session(events, model_patch="", gold_patch=GOLD)
        assert "path_hallucination" not in record["labels"]

    def test_suggestion_induced_misedit(self) -> None:
        # The run20-15213 shape: harness suggests an unrelated real file, the
        # model edits exactly that file, and it is not a gold file.
        events = [
            _tool_result("read_file", error="File not found: src/main.py. Did you mean: pkg/other/main.py?"),
        ]
        record = label_session(events, model_patch=_diff("pkg/other/main.py", 23, 530), gold_patch=GOLD)
        assert "suggestion_induced_misedit" in record["labels"]
        assert record["counters"]["suggestion_misedit_files"] == ["pkg/other/main.py"]
        assert "destructive_overwrite" in record["labels"]
        assert "localization_miss" in record["labels"]

    def test_suggested_file_that_is_gold_not_misedit(self) -> None:
        events = [
            _tool_result("read_file", error="File not found: target.py. Did you mean: pkg/real_target.py?"),
        ]
        record = label_session(events, model_patch=_diff("pkg/real_target.py", 3, 1), gold_patch=GOLD)
        assert "suggestion_induced_misedit" not in record["labels"]
        assert "localization_miss" not in record["labels"]


class TestVerificationLabels:
    def test_no_verification_attempt(self) -> None:
        record = label_session([_tool_call("read_file", file="a.py")], model_patch="", gold_patch=GOLD)
        assert "no_verification_attempt" in record["labels"]
        assert "verification_suppressed" not in record["labels"]

    def test_verification_suppressed_requested_but_never_executed(self) -> None:
        # Forensic trap shape: the model ASKED for execute_command but the
        # batch was replaced — no execution-side payload ever appears.
        events = [_tool_call("execute_command", command="pytest -x")]
        record = label_session(events, model_patch="", gold_patch=GOLD)
        assert "verification_suppressed" in record["labels"]
        assert "no_verification_attempt" not in record["labels"]

    def test_executed_verification_clears_both(self) -> None:
        events = [
            _tool_call("execute_command", command="pytest -x"),
            _tool_result("execute_command"),
        ]
        record = label_session(events, model_patch="", gold_patch=GOLD)
        assert "verification_suppressed" not in record["labels"]
        assert "no_verification_attempt" not in record["labels"]


class TestExecutionTruthSources:
    def test_receipt_items_counted_without_double_counting(self) -> None:
        shared = {"ok": False, "error": "File not found: app.py"}
        events = [
            _tool_result("read_file", error="File not found: app.py"),
            _complete_with_receipt(
                [
                    ("read_file", {"ok": False, "error": "File not found: app.py"}),  # dup of event
                    ("read_file", {"ok": False, "error": "File not found: main.py"}),  # receipt-only
                ]
            ),
        ]
        record = label_session(events, model_patch="", gold_patch=GOLD)
        assert record["counters"]["phantom_probe_count"] == 2
        assert record["counters"]["phantom_paths"] == ["app.py", "main.py"]
        assert shared["ok"] is False  # fixture sanity

    def test_tool_call_never_paired_with_results(self) -> None:
        # A requested read of the GOLD file must not count as executed.
        events = [
            _tool_call("repo_read_slice", file="pkg/real_target.py"),
            _tool_result("read_file", error="File not found: main.py"),
        ]
        record = label_session(events, model_patch="", gold_patch=GOLD)
        assert record["counters"]["executed_results"] == 1
        assert record["counters"]["phantom_paths"] == ["main.py"]


class TestPatchDerivedLabels:
    def test_destructive_overwrite_threshold(self) -> None:
        record = label_session([], model_patch=_diff("big.py", 32, 539), gold_patch=GOLD)
        assert "destructive_overwrite" in record["labels"]
        ok = label_session([], model_patch=_diff("big.py", 400, 539), gold_patch=GOLD)
        assert "destructive_overwrite" not in ok["labels"]

    def test_nonexistent_target_created(self) -> None:
        record = label_session([], model_patch=_diff("package.json", 7, 0, new_file=True), gold_patch=GOLD)
        assert "nonexistent_target_created" in record["labels"]
        assert record["counters"]["new_files_created"] == ["package.json"]

    def test_new_file_in_gold_is_fine(self) -> None:
        gold = _diff("pkg/new_module.py", 10, 0, new_file=True)
        record = label_session([], model_patch=_diff("pkg/new_module.py", 10, 0, new_file=True), gold_patch=gold)
        assert "nonexistent_target_created" not in record["labels"]

    def test_empty_patch(self) -> None:
        record = label_session([], model_patch="", gold_patch=GOLD)
        assert "empty_patch" in record["labels"]
        assert "localization_miss" not in record["labels"]


class TestErrorEventCounters:
    def test_mutation_pressure(self) -> None:
        events = [
            {"type": "error", "error": "MutationTargetGuardViolation(violation_type='UNSUPPORTED_NEW_FILE')"}
        ] * 2 + [{"type": "error", "error": "single_batch_contract_violation_retry_failed: ..."}]
        record = label_session(events, model_patch="", gold_patch=GOLD)
        assert record["counters"]["mutation_guard_violations"] == 2
        assert record["counters"]["contract_violation_errors"] == 1
        assert "mutation_contract_pressure" in record["labels"]

    def test_malformed_tool_args(self) -> None:
        events = [
            _tool_result("edit_blocks", error=f"Parameter validation failed: edit_blocks: missing ({i})")
            for i in range(5)
        ]
        record = label_session(events, model_patch="", gold_patch=GOLD)
        assert record["counters"]["param_validation_failures"] == 5
        assert "malformed_tool_args" in record["labels"]


class TestIO:
    def test_read_events_skips_malformed_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text(
            json.dumps({"type": "tool_call", "data": {}}) + "\nnot json\n[1,2]\n\n",
            encoding="utf-8",
        )
        events = read_events(path)
        assert len(events) == 1

    def test_aggregate(self) -> None:
        a = label_session([], model_patch="", gold_patch=GOLD)
        b = label_session([], model_patch=_diff("wrong.py", 2, 1), gold_patch=GOLD)
        agg = aggregate_labels([a, b])
        assert agg["schema_version"] == TAXONOMY_SCHEMA_VERSION
        assert agg["total"] == 2
        assert agg["label_frequency"]["empty_patch"] == 1
        assert agg["label_frequency"]["localization_miss"] == 1
