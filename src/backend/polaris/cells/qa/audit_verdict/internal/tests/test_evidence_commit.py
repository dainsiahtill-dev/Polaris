from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from polaris.cells.qa.audit_verdict.internal import evidence_commit

if TYPE_CHECKING:
    from pathlib import Path


def _append_receipt(*, append_id: str, content_id: str) -> SimpleNamespace:
    return SimpleNamespace(receipt={"event": {"append_id": append_id, "content_id": content_id}})


def _canonical_envelope(
    *,
    workspace: Path,
    verdict: str,
) -> dict[str, Any]:
    return {
        "schema_version": "qa.verdict_envelope.v1",
        "workspace": str(workspace),
        "run_id": "run-qa-1",
        "task_id": "task-qa-1",
        "verdict": verdict,
        "ok": verdict == "PASS",
        "next_stage": "" if verdict == "PASS" else "pending_qa",
        "terminal_status": "resolved" if verdict == "PASS" else "",
        "ledger": {"source": "run_ledger_projection", "available": True},
        "evidence": {
            "barrier": {
                "schema_version": "run_ledger.projection_barrier.v1",
                "barrier_satisfied": True,
                "consumed_append_ids": ["append-qa-evidence"],
                "consumed_event_hashes": ["hash-qa-evidence"],
            },
            "conflict_matrix": {"conflicts": []},
        },
        "classification": {
            "failure_class": (None if verdict == "PASS" else "IMPLEMENTATION_DEFECT"),
            "responsible_layer": "qa" if verdict == "PASS" else "director",
        },
        "evidence_refs": ["runtime/run-ledger.jsonl"],
        "content_hash": f"envelope-hash-{verdict.lower()}",
    }


def test_commit_qa_evidence_returns_barrier_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []

    def append(command: Any) -> Any:
        captured.append(command)
        return _append_receipt(append_id="append-qa-1", content_id="hash-qa-1")

    monkeypatch.setattr(evidence_commit, "append_run_ledger_event", append)

    receipt = evidence_commit.commit_qa_evidence(
        workspace=str(tmp_path),
        run_id="run-qa-1",
        task_id="task-qa-1",
        gate_name="qa_evidence",
        ok=False,
        summary="one finding",
        verdict="FAIL",
        audit_result={
            "audit_id": "audit-1",
            "findings": ["broken"],
            "metrics": {"issues": 1},
            "failure_class": "IMPLEMENTATION_DEFECT",
        },
        job_token={"token_id": "job-1"},
    )

    assert receipt.to_dict() == {
        "run_id": "run-qa-1",
        "append_id": "append-qa-1",
        "event_hash": "hash-qa-1",
    }
    command = captured[0]
    assert command.run_id == "run-qa-1"
    assert command.event["event_type"] == "gate_evaluated"
    assert command.event["stage"] == "qa"
    assert command.event["gate"] == {
        "name": "qa_evidence",
        "ok": False,
        "summary": "one finding",
    }
    assert command.event["job_token"]["token_id"] == "job-1"
    assert command.event["physical_evidence"]["authoritative"] is False
    assert command.event["physical_evidence"]["evidence_kind"] == "qa_evidence"
    assert command.event["physical_evidence"]["findings_count"] == 1


def test_commit_qa_evidence_rejects_final_verdict_gate_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reserved for canonical QA verdict"):
        evidence_commit.commit_qa_evidence(
            workspace=str(tmp_path),
            run_id="run-qa-1",
            task_id="task-qa-1",
            gate_name="qa_verdict",
            ok=True,
            summary="not canonical",
            verdict="PASS",
        )


def test_commit_qa_evidence_fails_closed_without_barrier_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evidence_commit,
        "append_run_ledger_event",
        lambda _command: SimpleNamespace(receipt={"event": {}}),
    )

    with pytest.raises(RuntimeError, match="projection-barrier coordinates"):
        evidence_commit.commit_qa_evidence(
            workspace=str(tmp_path),
            run_id="run-qa-2",
            task_id="task-qa-2",
            gate_name="qa_evidence",
            ok=True,
            summary="passed",
            verdict="PASS",
        )


@pytest.mark.parametrize("verdict", ["PASS", "FAIL", "BLOCKED"])
def test_commit_qa_verdict_preserves_canonical_mapping(
    verdict: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []

    def append(command: Any) -> Any:
        captured.append(command)
        return _append_receipt(
            append_id=f"append-final-{verdict.lower()}",
            content_id=f"hash-final-{verdict.lower()}",
        )

    monkeypatch.setattr(evidence_commit, "append_run_ledger_event", append)
    envelope = _canonical_envelope(workspace=tmp_path, verdict=verdict)
    receipt = evidence_commit.commit_qa_verdict(
        workspace=str(tmp_path),
        run_id="run-qa-1",
        task_id="task-qa-1",
        envelope=envelope,
        evidence_commit_receipt={
            "run_id": "run-qa-1",
            "append_id": "append-qa-evidence",
            "event_hash": "hash-qa-evidence",
        },
        job_token={"token_id": "job-1"},
    )

    assert receipt.verdict == verdict
    assert receipt.envelope_hash == envelope["content_hash"]
    event = captured[0].event
    assert event["gate"]["name"] == "qa_verdict"
    assert event["gate"]["ok"] is (verdict == "PASS")
    physical = event["physical_evidence"]
    assert physical["authoritative"] is True
    assert physical["verdict"] == verdict
    expected_failure_class = None if verdict == "PASS" else "IMPLEMENTATION_DEFECT"
    assert physical["failure_class"] == expected_failure_class
    assert physical["envelope_hash"] == envelope["content_hash"]
    assert physical["barrier_coordinates"]["append_id"] == "append-qa-evidence"
    assert physical["qa_verdict_envelope"] == envelope


def test_commit_qa_verdict_rejects_pass_with_canonical_conflicts(
    tmp_path: Path,
) -> None:
    envelope = _canonical_envelope(workspace=tmp_path, verdict="PASS")
    envelope["evidence"]["conflict_matrix"]["conflicts"] = ["task_boundary_missing"]

    with pytest.raises(ValueError, match="conflicts require a BLOCKED verdict"):
        evidence_commit.commit_qa_verdict(
            workspace=str(tmp_path),
            run_id="run-qa-1",
            task_id="task-qa-1",
            envelope=envelope,
            evidence_commit_receipt={
                "run_id": "run-qa-1",
                "append_id": "append-qa-evidence",
                "event_hash": "hash-qa-evidence",
            },
        )


@pytest.mark.parametrize("next_stage", ["pending_exec", "waiting_human"])
def test_commit_qa_verdict_preserves_blocked_non_terminal_routes(
    next_stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []

    def append(command: Any) -> Any:
        captured.append(command)
        return _append_receipt(
            append_id=f"append-blocked-{next_stage}",
            content_id=f"hash-blocked-{next_stage}",
        )

    monkeypatch.setattr(evidence_commit, "append_run_ledger_event", append)
    envelope = _canonical_envelope(workspace=tmp_path, verdict="BLOCKED")
    envelope["next_stage"] = next_stage
    envelope["classification"]["failure_class"] = (
        "TOOL_DISPATCH_DROPPED" if next_stage == "waiting_human" else "DEPENDENCY_NOT_UNLOCKED"
    )

    receipt = evidence_commit.commit_qa_verdict(
        workspace=str(tmp_path),
        run_id="run-qa-1",
        task_id="task-qa-1",
        envelope=envelope,
        evidence_commit_receipt={
            "run_id": "run-qa-1",
            "append_id": "append-qa-evidence",
            "event_hash": "hash-qa-evidence",
        },
    )

    assert receipt.verdict == "BLOCKED"
    physical = captured[0].event["physical_evidence"]
    assert physical["next_stage"] == next_stage
    assert physical["terminal_status"] == ""
