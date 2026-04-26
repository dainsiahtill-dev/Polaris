import json
import os
import sys
from pathlib import Path

MODULE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core", "polaris_loop"))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from polaris.kernelone.audit.invariant_sentinel import (  # noqa: E402
    compute_contract_fingerprint,
    run_invariant_sentinel,
)


def _write_events(path: str, seqs):
    with open(path, "w", encoding="utf-8") as handle:
        for seq in seqs:
            handle.write(json.dumps({"seq": seq}) + "\n")


def _runtime_events_path(tmp_path) -> str:
    return str(Path(tmp_path) / "runtime" / "events" / "runtime.events.jsonl")


def test_invariant_sentinel_contract_violation(monkeypatch, tmp_path):
    monkeypatch.setattr("polaris.kernelone.audit.invariant_sentinel.emit_event", lambda *args, **kwargs: None)
    events_path = Path(_runtime_events_path(tmp_path))
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    pm_task_path = tmp_path / "workspace" / "contracts" / "pm_tasks.json"
    pm_task_path.parent.mkdir(parents=True, exist_ok=True)
    payload_a = {"overall_goal": "A", "tasks": [{"id": "t1", "goal": "G1", "acceptance": ["x"]}]}
    pm_task_path.write_text(json.dumps(payload_a), encoding="utf-8")
    fingerprint = compute_contract_fingerprint(payload_a)
    payload_b = {"overall_goal": "B", "tasks": [{"id": "t1", "goal": "G1", "acceptance": ["x"]}]}
    pm_task_path.write_text(json.dumps(payload_b), encoding="utf-8")

    result = run_invariant_sentinel(
        events_path=str(events_path),
        run_id="run-1",
        step=1,
        pm_task_path=str(pm_task_path),
        contract_fingerprint=fingerprint,
        events_seq_start=0,
        events_size_start=0,
        memory_path="",
        director_result_path="",
    )
    codes = [v.get("code") for v in result.get("violations", [])]
    assert "CONTRACT_IMMUTABLE" in codes


def test_invariant_sentinel_events_violation(monkeypatch, tmp_path):
    monkeypatch.setattr("polaris.kernelone.audit.invariant_sentinel.emit_event", lambda *args, **kwargs: None)
    events_path = Path(_runtime_events_path(tmp_path))
    events_path.parent.mkdir(parents=True, exist_ok=True)
    _write_events(str(events_path), [1, 2])
    result = run_invariant_sentinel(
        events_path=str(events_path),
        run_id="run-2",
        step=1,
        pm_task_path="",
        contract_fingerprint="",
        events_seq_start=5,
        events_size_start=999,
        memory_path="",
        director_result_path="",
    )
    codes = [v.get("code") for v in result.get("violations", [])]
    assert "EVENTS_APPEND_ONLY" in codes


def test_invariant_sentinel_memory_refs(monkeypatch, tmp_path):
    monkeypatch.setattr("polaris.kernelone.audit.invariant_sentinel.emit_event", lambda *args, **kwargs: None)
    events_path = Path(_runtime_events_path(tmp_path))
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    memory_path = tmp_path / "MEMORY.jsonl"
    memory_path.write_text(
        json.dumps({"id": "mem-1", "context": {"run_id": "run-3"}}) + "\n",
        encoding="utf-8",
    )
    result = run_invariant_sentinel(
        events_path=str(events_path),
        run_id="run-3",
        step=1,
        pm_task_path="",
        contract_fingerprint="",
        events_seq_start=0,
        events_size_start=0,
        memory_path=str(memory_path),
        director_result_path="",
    )
    codes = [v.get("code") for v in result.get("violations", [])]
    assert "MEMORY_REFS" in codes


def test_invariant_sentinel_failure_hops_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("polaris.kernelone.audit.invariant_sentinel.emit_event", lambda *args, **kwargs: None)
    events_path = Path(_runtime_events_path(tmp_path))
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    director_result_path = tmp_path / "DIRECTOR_RESULT.json"
    director_result_path.write_text(
        json.dumps({"status": "fail", "acceptance": False, "run_id": "run-4"}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = run_invariant_sentinel(
        events_path=str(events_path),
        run_id="run-4",
        step=1,
        pm_task_path="",
        contract_fingerprint="",
        events_seq_start=0,
        events_size_start=0,
        memory_path="",
        director_result_path=str(director_result_path),
    )
    codes = [v.get("code") for v in result.get("violations", [])]
    assert "FAILURE_3HOPS_MISSING" in codes
