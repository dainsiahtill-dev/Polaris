"""Tests for polaris.kernelone.audit.invariant_sentinel."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from polaris.kernelone.audit.invariant_sentinel import (
    _check_contract_immutable,
    _check_events_append_only,
    _check_failure_hops_ready,
    _check_memory_refs,
    compute_contract_fingerprint,
    load_contract_fingerprint,
    run_invariant_sentinel,
)


class TestComputeContractFingerprint:
    def test_empty_payload(self) -> None:
        assert compute_contract_fingerprint(None) == ""
        assert compute_contract_fingerprint({}) == ""

    def test_deterministic(self) -> None:
        payload = {
            "overall_goal": "g1",
            "focus": "f1",
            "tasks": [
                {"id": "t1", "goal": "g", "acceptance": "a"},
                {"id": "t2", "goal": "g2", "acceptance_criteria": "ac"},
            ],
        }
        fp1 = compute_contract_fingerprint(payload)
        fp2 = compute_contract_fingerprint(payload)
        assert fp1 != ""
        assert fp1 == fp2

    def test_ignores_extra_fields(self) -> None:
        payload1 = {"overall_goal": "g", "focus": "f", "tasks": [], "extra": 1}
        payload2 = {"overall_goal": "g", "focus": "f", "tasks": []}
        assert compute_contract_fingerprint(payload1) == compute_contract_fingerprint(payload2)

    def test_skips_non_dict_tasks(self) -> None:
        payload = {"overall_goal": "g", "focus": "f", "tasks": ["not_a_dict"]}
        fp = compute_contract_fingerprint(payload)
        assert fp != ""


class TestLoadContractFingerprint:
    def test_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.json"
        path.write_text(json.dumps({"overall_goal": "g", "focus": "f", "tasks": []}), encoding="utf-8")
        assert load_contract_fingerprint(str(path)) != ""

    def test_empty_path(self) -> None:
        assert load_contract_fingerprint("") == ""

    def test_missing_file(self, tmp_path: Path) -> None:
        assert load_contract_fingerprint(str(tmp_path / "missing.json")) == ""

    def test_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert load_contract_fingerprint(str(path)) == ""


class TestCheckEventsAppendOnly:
    def test_missing_file(self) -> None:
        assert _check_events_append_only("/nonexistent") is None

    def test_no_change(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text("line1\n", encoding="utf-8")
        assert _check_events_append_only(str(path), start_seq=0, start_size=0) is None

    def test_size_shrink_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text("line1\nline2\n", encoding="utf-8")
        result = _check_events_append_only(str(path), start_seq=0, start_size=100)
        assert result is not None
        assert result["code"] == "EVENTS_APPEND_ONLY"


class TestCheckContractImmutable:
    def test_no_initial_hash(self) -> None:
        assert _check_contract_immutable(initial_hash="", pm_task_path="x") is None

    def test_no_pm_task_path(self) -> None:
        assert _check_contract_immutable(initial_hash="abc", pm_task_path="") is None

    def test_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.json"
        path.write_text(json.dumps({"overall_goal": "g", "focus": "f", "tasks": []}), encoding="utf-8")
        fp = load_contract_fingerprint(str(path))
        assert _check_contract_immutable(initial_hash=fp, pm_task_path=str(path)) is None

    def test_changed(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.json"
        path.write_text(json.dumps({"overall_goal": "g", "focus": "f", "tasks": []}), encoding="utf-8")
        result = _check_contract_immutable(initial_hash="different", pm_task_path=str(path))
        assert result is not None
        assert result["code"] == "CONTRACT_IMMUTABLE"


class TestCheckMemoryRefs:
    def test_missing_path(self) -> None:
        assert _check_memory_refs("/nonexistent", "r1") is None

    def test_empty_run_id(self, tmp_path: Path) -> None:
        path = tmp_path / "memory.jsonl"
        path.write_text("\n", encoding="utf-8")
        assert _check_memory_refs(str(path), "") is None

    def test_all_refs_present(self, tmp_path: Path) -> None:
        path = tmp_path / "memory.jsonl"
        lines = [
            json.dumps({"id": "m1", "context": {"run_id": "r1", "refs": ["ref1"]}}),
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
    def test_missing_file(self) -> None:
        assert _check_failure_hops_ready("/nonexistent") is None

    def test_not_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "result.json"
        path.write_text(json.dumps({"status": "success"}), encoding="utf-8")
        assert _check_failure_hops_ready(str(path)) is None

    def test_failure_but_ready(self, tmp_path: Path) -> None:
        path = tmp_path / "result.json"
        path.write_text(json.dumps({"status": "fail", "failure_hops_ready": True}), encoding="utf-8")
        assert _check_failure_hops_ready(str(path)) is None

    def test_failure_not_ready(self, tmp_path: Path) -> None:
        path = tmp_path / "result.json"
        path.write_text(json.dumps({"status": "fail", "failure_hops_ready": False}), encoding="utf-8")
        result = _check_failure_hops_ready(str(path))
        assert result is not None
        assert result["code"] == "FAILURE_3HOPS_MISSING"

    def test_blocked_status(self, tmp_path: Path) -> None:
        path = tmp_path / "result.json"
        path.write_text(json.dumps({"status": "blocked"}), encoding="utf-8")
        result = _check_failure_hops_ready(str(path))
        assert result is not None
        assert result["code"] == "FAILURE_3HOPS_MISSING"

    def test_acceptance_false(self, tmp_path: Path) -> None:
        path = tmp_path / "result.json"
        path.write_text(json.dumps({"status": "ok", "acceptance": False}), encoding="utf-8")
        result = _check_failure_hops_ready(str(path))
        assert result is not None
        assert result["code"] == "FAILURE_3HOPS_MISSING"


class TestRunInvariantSentinel:
    def test_pass(self, tmp_path: Path) -> None:
        events_path = tmp_path / "events.jsonl"
        events_path.write_text("\n", encoding="utf-8")
        result = run_invariant_sentinel(
            events_path=str(events_path),
            run_id="r1",
            step=1,
            pm_task_path="",
            contract_fingerprint="",
        )
        assert result["ok"] is True
        assert result["violations"] == []

    def test_fail_contract(self, tmp_path: Path) -> None:
        pm_path = tmp_path / "tasks.json"
        pm_path.write_text(json.dumps({"overall_goal": "g", "focus": "f", "tasks": []}), encoding="utf-8")
        events_path = tmp_path / "events.jsonl"
        events_path.write_text("\n", encoding="utf-8")
        result = run_invariant_sentinel(
            events_path=str(events_path),
            run_id="r1",
            step=1,
            pm_task_path=str(pm_path),
            contract_fingerprint="wrong",
        )
        assert result["ok"] is False
        assert any(v["code"] == "CONTRACT_IMMUTABLE" for v in result["violations"])

    def test_fail_events_shrink(self, tmp_path: Path) -> None:
        events_path = tmp_path / "events.jsonl"
        events_path.write_text("line\n", encoding="utf-8")
        result = run_invariant_sentinel(
            events_path=str(events_path),
            run_id="r1",
            step=1,
            events_size_start=100,
        )
        assert result["ok"] is False
        assert any(v["code"] == "EVENTS_APPEND_ONLY" for v in result["violations"])

    def test_fail_memory_refs(self, tmp_path: Path) -> None:
        memory_path = tmp_path / "memory.jsonl"
        memory_path.write_text(
            json.dumps({"id": "m1", "context": {"run_id": "r1"}}) + "\n",
            encoding="utf-8",
        )
        events_path = tmp_path / "events.jsonl"
        events_path.write_text("\n", encoding="utf-8")
        result = run_invariant_sentinel(
            events_path=str(events_path),
            run_id="r1",
            step=1,
            memory_path=str(memory_path),
        )
        assert result["ok"] is False
        assert any(v["code"] == "MEMORY_REFS" for v in result["violations"])

    def test_fail_failure_hops(self, tmp_path: Path) -> None:
        result_path = tmp_path / "result.json"
        result_path.write_text(json.dumps({"status": "fail"}), encoding="utf-8")
        events_path = tmp_path / "events.jsonl"
        events_path.write_text("\n", encoding="utf-8")
        result = run_invariant_sentinel(
            events_path=str(events_path),
            run_id="r1",
            step=1,
            director_result_path=str(result_path),
        )
        assert result["ok"] is False
        assert any(v["code"] == "FAILURE_3HOPS_MISSING" for v in result["violations"])
