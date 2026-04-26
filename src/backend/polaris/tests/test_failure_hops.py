import json
import os
import sys

MODULE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core", "polaris_loop"))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from polaris.kernelone.audit.failure_hops import build_failure_hops  # noqa: E402


def _write_events(path: str, records) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_build_failure_hops_with_raw_artifacts(tmp_path):
    events_path = tmp_path / "events.jsonl"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    stdout_path.write_text("ok", encoding="utf-8")
    stderr_path.write_text("boom", encoding="utf-8")

    _write_events(
        str(events_path),
        [
            {
                "seq": 1,
                "kind": "action",
                "actor": "Tooling",
                "name": "repo_rg",
                "refs": {"run_id": "dir-00001", "phase": "tool_exec", "task_id": "t1"},
            },
            {
                "seq": 2,
                "kind": "observation",
                "actor": "Tooling",
                "name": "repo_rg",
                "ok": False,
                "refs": {
                    "run_id": "dir-00001",
                    "phase": "tool_exec",
                    "task_id": "t1",
                    "task_fingerprint": "fp1",
                    "files": ["backend/x.py"],
                },
                "output": {
                    "ok": False,
                    "tool": "repo_rg",
                    "error": "timeout",
                    "tool_stdout_path": str(stdout_path),
                    "tool_stderr_path": str(stderr_path),
                },
            },
        ],
    )

    payload = build_failure_hops(
        str(events_path),
        run_id="dir-00001",
        event_seq_start=1,
        event_seq_end=2,
        fallback_failure_code="",
    )

    assert payload["ready"] is True
    assert payload["has_failure"] is True
    assert payload["failure_event_seq"] == 2
    assert payload["hop1_phase"]["phase"] == "tool_exec"
    assert payload["hop2_evidence"]["related_action_seq"] == 1
    assert payload["hop3_tool_output"]["source"] == "artifact_paths"
    assert payload["hop3_tool_output"]["paths"]["tool_stdout_path"] == str(stdout_path)


def test_build_failure_hops_fallback_to_event_output(tmp_path):
    events_path = tmp_path / "events.jsonl"
    _write_events(
        str(events_path),
        [
            {
                "seq": 3,
                "kind": "observation",
                "actor": "Tooling",
                "name": "repo_read_head",
                "ok": False,
                "refs": {"run_id": "dir-00002", "phase": "tool_exec"},
                "output": {"ok": False, "tool": "repo_read_head", "error": "invalid args"},
            }
        ],
    )

    payload = build_failure_hops(
        str(events_path),
        run_id="dir-00002",
        event_seq_start=1,
        event_seq_end=10,
        fallback_failure_code="QA_FAIL",
    )

    assert payload["has_failure"] is True
    assert payload["failure_code"] == "QA_FAIL"
    assert payload["hop3_tool_output"]["source"] == "event_output"
    assert payload["hop3_tool_output"]["error"] == "invalid args"


def test_build_failure_hops_success_run(tmp_path):
    events_path = tmp_path / "events.jsonl"
    _write_events(
        str(events_path),
        [
            {
                "seq": 1,
                "kind": "observation",
                "actor": "Tooling",
                "name": "repo_tree",
                "ok": True,
                "refs": {"run_id": "dir-00003", "phase": "tool_exec"},
                "output": {"ok": True, "tool": "repo_tree"},
            }
        ],
    )

    payload = build_failure_hops(
        str(events_path),
        run_id="dir-00003",
        event_seq_start=1,
        event_seq_end=10,
        fallback_failure_code="",
    )

    assert payload["ready"] is True
    assert payload["has_failure"] is False
    assert payload["hop1_phase"] is None
