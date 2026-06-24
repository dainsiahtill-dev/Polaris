from __future__ import annotations

import json
from pathlib import Path

from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    append_run_ledger_event,
)
from polaris.cells.runtime.projection.internal.workflow_status import (
    _derive_terminal_failure_status,
    _derive_terminal_success_status,
)


def _write_success_artifacts(cache_root: Path) -> None:
    results_dir = cache_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "director.result.json").write_text(
        json.dumps(
            {
                "status": "success",
                "total": 1,
                "failures": 0,
                "blocked": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (results_dir / "integration_qa.result.json").write_text(
        json.dumps(
            {
                "passed": True,
                "reason": "integration_qa_passed",
                "summary": "Integration verification passed: pytest -q",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _append_success_ledger(workspace: Path, run_id: str) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(workspace),
            run_id=run_id,
            event={
                "schema_version": 1,
                "event_type": "gate_evaluated",
                "run_id": run_id,
                "stage": "integration_qa",
                "actor": "QA",
                "job_token": {
                    "token_id": "jt-ledger-ok",
                    "run_id": run_id,
                    "project_id": run_id,
                    "contract_hash": "contract-hash",
                    "blueprint_hash": "blueprint-hash",
                    "target_files": [],
                    "permissions": {},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["verifier"],
                        "required_evidence_modalities": ["verifier"],
                    },
                    "capability_audit": {"ok": True, "issues": []},
                },
                "gate": {
                    "name": "integration_qa",
                    "ok": True,
                    "summary": "Integration verification passed: pytest -q",
                },
                "physical_evidence": {
                    "command_count": 1,
                    "qa_verifiers": [
                        {
                            "id": "integration_qa",
                            "name": "Integration QA",
                            "modality": "verifier",
                            "ok": True,
                            "detail": "Integration verification passed: pytest -q",
                        }
                    ],
                },
            },
        )
    )


def test_success_artifacts_without_run_ledger_fail_closed(tmp_path: Path) -> None:
    cache_root = tmp_path / "runtime"
    _write_success_artifacts(cache_root)

    assert (
        _derive_terminal_failure_status(
            workspace=str(tmp_path),
            cache_root=str(cache_root),
            run_id="run-without-ledger",
            statuses=("", ""),
            payloads=(),
        )
        == "failed"
    )
    assert (
        _derive_terminal_success_status(
            workspace=str(tmp_path),
            cache_root=str(cache_root),
            run_id="run-without-ledger",
        )
        == ""
    )


def test_run_ledger_projection_can_prove_terminal_success(tmp_path: Path) -> None:
    cache_root = tmp_path / "runtime"
    _write_success_artifacts(cache_root)
    _append_success_ledger(tmp_path, "run-ledger-ok")

    assert (
        _derive_terminal_failure_status(
            workspace=str(tmp_path),
            cache_root=str(cache_root),
            run_id="run-ledger-ok",
            statuses=("", ""),
            payloads=(),
        )
        == ""
    )
    assert (
        _derive_terminal_success_status(
            workspace=str(tmp_path),
            cache_root=str(cache_root),
            run_id="run-ledger-ok",
        )
        == "completed"
    )
