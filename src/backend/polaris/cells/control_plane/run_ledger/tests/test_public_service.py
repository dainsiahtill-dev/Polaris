from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    ReadRunLedgerProjectionQueryV1,
    RunLedger,
    append_run_ledger_event,
    build_run_ledger_projection,
    read_run_ledger_projection,
    service as run_ledger_service,
    summarize_run_ledger_projection,
)


def _write_ledger_event(workspace: Path, *, run_id: str = "run-1") -> None:
    ledger_path = workspace / "runtime" / "factory" / "ledger" / f"{run_id}.ndjson"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event_type": "gate_evaluated",
        "event_id": "evt-1",
        "content_id": "cid-1",
        "append_id": "append-1",
        "stage": "qa_verifier",
        "gate": {"name": "qa_verifier", "ok": True, "summary": "gate passed"},
        "job_token": {
            "token_id": "token-1",
            "run_id": run_id,
            "project_id": "P1",
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {
                "enabled_evidence_modalities": ["browser"],
                "required_evidence_modalities": [],
            },
        },
        "physical_evidence": {
            "modalities": {
                "browser": {
                    "present": True,
                    "ok": True,
                    "detail": "browser verifier passed",
                }
            }
        },
    }
    ledger_path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")


def test_run_ledger_writer_uses_platform_control_plane_namespace(tmp_path: Path) -> None:
    persisted = RunLedger(tmp_path, run_id="run-1").append_event(
        {
            "event_type": "gate_evaluated",
            "gate": {"name": "qa_verifier", "ok": True, "summary": "ok"},
            "job_token": {
                "token_id": "token-1",
                "project_id": "P1",
                "capability_audit": {"ok": True, "issues": []},
                "gate_policy": {},
            },
            "physical_evidence": {},
        }
    )

    ledger_path = Path(str(persisted["ledger_path"]))
    assert ledger_path.parent == tmp_path / "runtime" / "control_plane" / "ledger"
    assert RunLedger(tmp_path, run_id="run-1").read_events()[0]["event_type"] == "gate_evaluated"


def test_append_run_ledger_event_public_service_projects_event(tmp_path: Path) -> None:
    result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "director_mutation",
                "gate": {"name": "director_mutation", "ok": True, "summary": "mutation verified"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["tool_receipt"],
                        "required_evidence_modalities": [],
                    },
                },
                "physical_evidence": {
                    "tool_receipts": [
                        {
                            "operation": "write_file",
                            "file": "src/app.ts",
                            "capability_token": {"token_id": "token-1"},
                        }
                    ]
                },
            },
        )
    )

    ledger_path = Path(str(result.receipt["ledger_path"]))
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection

    assert ledger_path.parent == tmp_path / "runtime" / "control_plane" / "ledger"
    assert result.receipt["event"]["append_id"]
    assert projection["ok"] is True
    assert projection["projects"][0]["project_id"] == "P1"
    assert projection["evidence_modalities"]["tool_receipt"]["present"] == 1


def test_required_evidence_distinguishes_missing_from_failed() -> None:
    base_event = {
        "event_type": "gate_evaluated",
        "stage": "real_run",
        "gate": {"name": "real_run_gate", "ok": False, "summary": "command failed"},
        "job_token": {
            "token_id": "token-1",
            "project_id": "P1",
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {"required_evidence_modalities": ["code", "command"]},
        },
        "physical_evidence": {
            "modalities": {
                "code": {"present": True, "ok": True, "detail": "files landed"},
                "command": {"present": True, "ok": False, "detail": "go test failed"},
            }
        },
    }

    projection = build_run_ledger_projection([base_event])
    summary = summarize_run_ledger_projection(projection)

    assert projection["integrity_ok"] is True
    assert projection["outcome_ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["command"]
    assert projection["missing"] == []
    assert summary["missing"] == []
    assert summary["failed_required_modalities"] == ["command"]
    assert summary["detail"] == "run ledger projection required evidence failed: command"

    missing_projection = build_run_ledger_projection(
        [
            {
                **base_event,
                "physical_evidence": {
                    "modalities": {
                        "code": {"present": True, "ok": True, "detail": "files landed"},
                    }
                },
            }
        ]
    )

    assert missing_projection["integrity_ok"] is False
    assert missing_projection["evidence_policy"]["missing_required_modalities"] == ["command"]
    assert missing_projection["evidence_policy"]["failed_required_modalities"] == []


def test_append_run_ledger_event_publishes_control_plane_projection_event(tmp_path: Path, monkeypatch) -> None:
    class FakePublisher:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            self.calls.append((subject, payload))
            return True

    publisher = FakePublisher()
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")
    monkeypatch.setattr(run_ledger_service, "get_log_jetstream_publisher", lambda: publisher)
    monkeypatch.setattr(
        run_ledger_service,
        "resolve_storage_roots",
        lambda workspace: SimpleNamespace(workspace_key="workspace-key"),
    )

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "qa_verifier",
                "gate": {"name": "qa_verifier", "ok": True, "summary": "qa verified"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )

    assert len(publisher.calls) == 1
    subject, payload = publisher.calls[0]
    event_payload = payload["payload"]
    assert subject == "hp.runtime.workspace-key.status.control_plane"
    assert payload["schema_version"] == "runtime.v2"
    assert payload["channel"] == "status.control_plane"
    assert payload["kind"] == "control_plane_ledger_projection_update"
    assert isinstance(event_payload, dict)
    projection = event_payload["projection"]
    assert isinstance(projection, dict)
    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is True
    assert projection["projects"][0]["project_id"] == "P1"


def test_read_run_ledger_projection_ignores_compat_ledgers_by_default(tmp_path: Path) -> None:
    _write_ledger_event(tmp_path)

    result = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1"))
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is False
    assert projection["ok"] is False
    assert projection["compat_ledgers_included"] is False
    assert projection["projects"] == []


def test_read_run_ledger_projection_can_include_compat_ledgers_explicitly_for_migration(
    tmp_path: Path,
) -> None:
    _write_ledger_event(tmp_path)

    result = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="run-1",
            include_compat_ledgers=True,
        )
    )
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is True
    assert projection["ok"] is True
    assert projection["compat_ledgers_included"] is True
    assert projection["projects"][0]["project_id"] == "P1"
    assert projection["evidence_policy"]["enabled_modalities"] == ["browser"]
    assert projection["evidence_policy"]["required_modalities"] == []


def test_read_run_ledger_projection_returns_empty_when_no_ledger_exists(tmp_path: Path) -> None:
    result = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path)))
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is False
    assert projection["status"] == "pending"
    assert projection["compat_ledgers_included"] is False
    assert projection["projects"] == []
