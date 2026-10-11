from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events
from polaris.cells.runtime.task_runtime.tests.test_service_sub2 import _create_bootstrapped_task_runtime_service


@pytest.fixture(autouse=True)
def project_local_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)


def test_real_task_runtime_producer_writes_strictly_readable_facts(tmp_path: Path) -> None:
    service = _create_bootstrapped_task_runtime_service(tmp_path)
    row = service.create_task_row(subject="Integrity fixture")
    claim = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id="run-child", selection_source="unit"
    )
    assert claim["success"] is True
    facts = query_fact_events(
        QueryFactEventsV1(workspace=str(tmp_path), stream="task_runtime.execution", strict_integrity=True)
    )
    assert len(facts.events) >= 2


def test_legacy_history_remains_resumable_but_never_strictly_qualified(tmp_path: Path) -> None:
    service = _create_bootstrapped_task_runtime_service(tmp_path)
    row = service.create_task_row(subject="Legacy fixture")
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    old = json.loads(path.read_text(encoding="utf-8"))
    old.pop("integrity_digest")
    original = json.dumps(old).encode("utf-8") + b"\n"
    path.write_bytes(original)
    claim = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id="run-child", selection_source="unit"
    )
    assert claim["success"] is True
    assert path.read_bytes().startswith(original)
    assert "integrity_digest" in json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    with pytest.raises(RuntimeError):
        query_fact_events(
            QueryFactEventsV1(workspace=str(tmp_path), stream="task_runtime.execution", strict_integrity=True)
        )


@pytest.mark.parametrize("corruption", ["malformed", "digest_drift"])
def test_task_runtime_append_rejects_corruption_without_rewriting_history(tmp_path: Path, corruption: str) -> None:
    service = _create_bootstrapped_task_runtime_service(tmp_path)
    service.create_task_row(subject="Existing fixture")
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    if corruption == "malformed":
        path.write_text("{invalid}\n", encoding="utf-8")
    else:
        record = json.loads(path.read_text(encoding="utf-8"))
        record["payload"]["subject"] = "tampered"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    original = path.read_bytes()
    row = service.create_task_row(subject="New fixture")
    assert row["execution_event"]["ok"] is False
    assert path.read_bytes() == original
