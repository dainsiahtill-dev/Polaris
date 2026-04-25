"""Unit tests for polaris.kernelone.audit.invariant_sentinel."""

from __future__ import annotations

import json
from pathlib import Path

from polaris.kernelone.audit.invariant_sentinel import (
    _check_contract_immutable,
    _check_events_append_only,
    _check_failure_hops_ready,
    _check_memory_refs,
    _hash_payload,
    compute_contract_fingerprint,
    load_contract_fingerprint,
    run_invariant_sentinel,
)


class TestHashPayload:
    def test_deterministic(self) -> None:
        assert _hash_payload({"a": 1}) == _hash_payload({"a": 1})

    def test_different_inputs(self) -> None:
        assert _hash_payload({"a": 1}) != _hash_payload({"a": 2})


class TestComputeContractFingerprint:
    def test_empty_dict(self) -> None:
        assert compute_contract_fingerprint({}) == ""

    def test_none_input(self) -> None:
        assert compute_contract_fingerprint(None) == ""

    def test_basic_contract(self) -> None:
        payload = {
            "overall_goal": "test",
            "focus": "area",
            "tasks": [
                {"id": "1", "goal": "g1", "acceptance": "a1"},
            ],
        }
        fp1 = compute_contract_fingerprint(payload)
        fp2 = compute_contract_fingerprint(payload)
        assert fp1 == fp2
        assert len(fp1) == 64

    def test_ignores_extra_fields(self) -> None:
        payload = {
            "overall_goal": "test",
            "focus": "area",
            "tasks": [{"id": "1", "goal": "g1"}],
            "extra": "ignored",
        }
        fp = compute_contract_fingerprint(payload)
        assert len(fp) == 64


class TestLoadContractFingerprint:
    def test_empty_path(self) -> None:
        assert load_contract_fingerprint("") == ""

    def test_missing_file(self) -> None:
        assert load_contract_fingerprint("/nonexistent/file.json") == ""

    def test_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.json"
        payload = {"overall_goal": "g", "focus": "f", "tasks": []}
        path.write_text(json.dumps(payload), encoding="utf-8")
        fp = load_contract_fingerprint(str(path))
        assert fp == compute_contract_fingerprint(payload)

    def test_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert load_contract_fingerprint(str(path)) == ""


class TestCheckEventsAppendOnly:
    def test_missing_path(self) -> None:
        assert _check_events_append_only("", run_id="r1", event_seq_start=0, event_seq_end=0) is None

    def test_missing_file(self) -> None:
        assert (
            _check_events_append_only("/nonexistent/events.jsonl", run_id="r1", event_seq_start=0, event_seq_end=0)
            is None
        )

    def test_no_violation(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text('{"seq":1}\n{"seq":2}\n', encoding="utf-8")
        assert _check_events_append_only(str(path), run_id="r1", event_seq_start=0, event_seq_end=0) is None

    def test_size_shrink_violation(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text('{"seq":1}\n', encoding="utf-8")
        result = _check_events_append_only(str(path), run_id="r1", event_seq_start=0, event_seq_end=0, start_size=9999)
        assert result is not None
        assert result["code"] == "EVENTS_APPEND_ONLY"


class TestCheckContractImmutable:
    def test_no_initial_hash(self) -> None:
        assert _check_contract_immutable(initial_hash="", pm_task_path="/some/path") is None

    def test_no_pm_task_path(self) -> None:
        assert _check_contract_immutable(initial_hash="abc", pm_task_path="") is None

    def test_unchanged_contract(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.json"
        payload = {"overall_goal": "g", "focus": "f", "tasks": []}
        path.write_text(json.dumps(payload), encoding="utf-8")
        initial = compute_contract_fingerprint(payload)
        assert _check_contract_immutable(initial_hash=initial, pm_task_path=str(path)) is None

    def test_changed_contract(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.json"
        payload = {"overall_goal": "g", "focus": "f", "tasks": []}
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert _check_contract_immutable(initial_hash="different", pm_task_path=str(path)) is not None


class TestCheckMemoryRefs:
    def test_missing_path(self) -> None:
        assert _check_memory_refs("", "r1") is None

    def test_missing_file(self) -> None:
        assert _check_memory_refs("/nonexistent/memory.jsonl", "r1") is None

    def test_all_have_refs(self, tmp_path: Path) -> None:
        path = tmp_path / "memory.jsonl"
        lines = [
            json.dumps({"id": "m1", "context": {"run_id": "r1", "refs": {"evidence": "e1"}}}),
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        assert _check_memory_refs(str(path), "r1") is None

    def test_missing_refs(self, tmp_path: Path) -> None:
        path = tmp_path / "memory.jsonl"
        lines = [
            json.dumps({"id": "m1", "context": {"run_id": "r1"}}),
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        result = _check_memory_refs(str(path), "r1")
        assert result is not None
        assert result["code"] == "MEMORY_REFS"
        assert result["details"]["missing_count"] == 1


class TestCheckFailureHopsReady:
    def test_missing_path(self) -> None:
        assert _check_failure_hops_ready("") is None

    def test_non_failure_status(self, tmp_path: Path) -> None:
        path = tmp_path / "result.json"
        path.write_text(json.dumps({"status": "success"}), encoding="utf-8")
        assert _check_failure_hops_ready(str(path)) is None

    def test_failure_with_ready(self, tmp_path: Path) -> None:
        path = tmp_path / "result.json"
        path.write_text(json.dumps({"status": "fail", "failure_hops_ready": True}), encoding="utf-8")
        assert _check_failure_hops_ready(str(path)) is None

    def test_failure_without_ready(self, tmp_path: Path) -> None:
        path = tmp_path / "result.json"
        path.write_text(json.dumps({"status": "fail", "failure_hops_ready": False}), encoding="utf-8")
        result = _check_failure_hops_ready(str(path))
        assert result is not None
        assert result["code"] == "FAILURE_3HOPS_MISSING"

    def test_acceptance_false_triggers(self, tmp_path: Path) -> None:
        path = tmp_path / "result.json"
        path.write_text(json.dumps({"status": "ok", "acceptance": False}), encoding="utf-8")
        result = _check_failure_hops_ready(str(path))
        assert result is not None
        assert result["code"] == "FAILURE_3HOPS_MISSING"


class TestRunInvariantSentinel:
    def test_no_violations(self, tmp_path: Path) -> None:
        events_path = tmp_path / "events.jsonl"
        events_path.write_text('{"seq":1}\n', encoding="utf-8")
        result = run_invariant_sentinel(
            events_path=str(events_path),
            run_id="r1",
            step=1,
        )
        assert result["ok"] is True
        assert result["violations"] == []

    def test_with_contract_violation(self, tmp_path: Path) -> None:
        events_path = tmp_path / "events.jsonl"
        events_path.write_text('{"seq":1}\n', encoding="utf-8")
        tasks_path = tmp_path / "tasks.json"
        tasks_path.write_text(json.dumps({"overall_goal": "g", "focus": "f", "tasks": []}), encoding="utf-8")
        result = run_invariant_sentinel(
            events_path=str(events_path),
            run_id="r1",
            step=1,
            pm_task_path=str(tasks_path),
            contract_fingerprint="different_hash",
        )
        assert result["ok"] is False
        assert any(v["code"] == "CONTRACT_IMMUTABLE" for v in result["violations"])
