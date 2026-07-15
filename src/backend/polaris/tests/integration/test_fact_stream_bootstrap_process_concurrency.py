"""Independent-process bootstrap evidence for the FactStream public contract."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path

from polaris.cells.events.fact_stream.public import (
    QueryFactEventsV1,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.kernelone.fs.locked_regular_file import default_platform_lock_root

_BOOTSTRAP_CONCURRENCY = 64
_PROCESS_BARRIER_TIMEOUT_SECONDS = 30
_PROCESS_CLEANUP_TIMEOUT_SECONDS = 5

_PROCESS_BOOTSTRAP_PROGRAM = """
import fcntl
import json
import os
import sys
from pathlib import Path

from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
)


def emit(record):
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)


payload = json.loads(os.environ["POLARIS_BOOTSTRAP_PROCESS_PAYLOAD"])
worker_id = os.environ["POLARIS_BOOTSTRAP_PROCESS_WORKER_ID"]
ready_marker = Path(payload["ready_directory"]) / f"{worker_id}.ready"
passed_marker = Path(payload["passed_directory"]) / f"{worker_id}.passed"
with ready_marker.open("x", encoding="utf-8") as marker:
    marker.write("armed\\n")
    marker.flush()
    os.fsync(marker.fileno())

try:
    with Path(payload["gate_file"]).open("r+", encoding="utf-8") as gate:
        fcntl.flock(gate.fileno(), fcntl.LOCK_SH)
        try:
            with passed_marker.open("x", encoding="utf-8") as marker:
                marker.write("shared_lock_acquired\\n")
                marker.flush()
                os.fsync(marker.fileno())
            receipt = bootstrap_fact_stream_workspace(
                BootstrapFactStreamWorkspaceCommandV1(
                    workspace=payload["workspace"],
                    streams=tuple(payload["streams"]),
                    maintenance_reason=payload["maintenance_reason"],
                    platform_lock_root=payload["platform_lock_root"],
                )
            )
            appended = append_fact_event(
                AppendFactEventCommandV1(
                    workspace=payload["workspace"],
                    stream=payload["contested_stream"],
                    event_type="process_concurrency_proof",
                    payload={"worker_id": worker_id},
                    source="integration.fact_stream_process_concurrency",
                    idempotency_key=f"process-concurrency:{worker_id}",
                    durability="fsync",
                    strict_integrity=True,
                )
            )
        finally:
            fcntl.flock(gate.fileno(), fcntl.LOCK_UN)
except Exception as exc:
    emit(
        {
            "phase": "error",
            "worker_id": worker_id,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    )
    raise SystemExit(1)

emit(
    {
        "phase": "result",
        "worker_id": worker_id,
        "receipt": {
            "workspace": receipt.workspace,
            "storage_identity_token": receipt.storage_identity_token,
            "operation": receipt.operation,
            "streams": list(receipt.streams),
            "proofs": [
                {
                    "operation": proof.operation,
                    "verdict": proof.verdict,
                    "storage_identity_token": proof.storage_identity_token,
                    "runtime_root": proof.runtime_root,
                    "format_revision": proof.format_revision,
                    "final_validation": proof.final_validation,
                    "root_identity": [proof.root_identity.device, proof.root_identity.inode],
                    "anchor_identity": [proof.anchor_identity.device, proof.anchor_identity.inode],
                    "realm_identity": [proof.realm_identity.device, proof.realm_identity.inode],
                    "lock_keys": [
                        {
                            "logical_path": item.logical_path,
                            "lock_key": item.lock_key,
                            "verdict": item.verdict,
                            "identity": [item.identity.device, item.identity.inode],
                        }
                        for item in proof.lock_keys
                    ],
                }
                for proof in receipt.proofs
            ],
        },
        "append": {
            "event_id": appended.event_id,
            "stream": appended.stream,
            "appended_seq": appended.appended_seq,
        },
        "gate": {
            "ready_marker": ready_marker.name,
            "passed_marker": passed_marker.name,
            "shared_lock_held_through_operation": True,
        },
    }
)
"""


def _terminate_processes(processes: list[subprocess.Popen[str]]) -> None:
    """Terminate every child created by this test, escalating only when required."""

    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=_PROCESS_CLEANUP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
    for process in processes:
        if process.poll() is None:
            process.wait(timeout=_PROCESS_CLEANUP_TIMEOUT_SECONDS)


def _wait_for_ready_markers(ready_directory: Path, expected_markers: set[str]) -> None:
    deadline = time.monotonic() + _PROCESS_BARRIER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        actual_markers = {marker.name for marker in ready_directory.glob("*.ready")}
        if actual_markers == expected_markers:
            return
        time.sleep(0.01)
    actual_markers = {marker.name for marker in ready_directory.glob("*.ready")}
    raise AssertionError(
        "bootstrap process ready barrier timed out: "
        f"missing={sorted(expected_markers - actual_markers)} extra={sorted(actual_markers - expected_markers)}"
    )


def _collect_process_outputs(processes: list[subprocess.Popen[str]]) -> list[tuple[str, str]]:
    executor = ThreadPoolExecutor(max_workers=len(processes))
    futures: dict[Future[tuple[str, str]], subprocess.Popen[str]] = {
        executor.submit(process.communicate): process for process in processes
    }
    _done, pending = wait(futures, timeout=_PROCESS_BARRIER_TIMEOUT_SECONDS)
    if pending:
        executor.shutdown(wait=False, cancel_futures=True)
        pending_pids = sorted(futures[future].pid for future in pending)
        raise AssertionError(f"bootstrap process result collection timed out: pids={pending_pids}")
    try:
        outputs_by_process = {process: future.result() for future, process in futures.items()}
        return [outputs_by_process[process] for process in processes]
    finally:
        executor.shutdown(wait=True)


def _parse_result(stdout: str) -> dict[str, object]:
    lines = [line for line in stdout.splitlines() if line]
    assert len(lines) == 1
    try:
        result = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise AssertionError(f"bootstrap child emitted invalid UTF-8 JSON: {lines[0]!r}") from exc
    assert isinstance(result, dict)
    return result


def _proof_for_operation(receipt: dict[str, object], operation: str) -> dict[str, object]:
    raw_proofs = receipt.get("proofs")
    assert isinstance(raw_proofs, list)
    proofs = [proof for proof in raw_proofs if isinstance(proof, dict) and proof.get("operation") == operation]
    assert len(proofs) == 1
    return proofs[0]


def _identity_tuple(proof: dict[str, object]) -> tuple[object, ...]:
    identities = tuple(proof.get(field) for field in ("root_identity", "anchor_identity", "realm_identity"))
    assert all(
        isinstance(identity, list)
        and len(identity) == 2
        and all(isinstance(value, int) and value >= 0 for value in identity)
        and identity[1] > 0
        for identity in identities
    )
    return (
        proof.get("storage_identity_token"),
        proof.get("runtime_root"),
        proof.get("format_revision"),
        *(tuple(identity) for identity in identities if isinstance(identity, list)),
    )


def _lock_key_evidence(proof: dict[str, object]) -> tuple[tuple[object, ...], ...]:
    raw_lock_keys = proof.get("lock_keys")
    assert isinstance(raw_lock_keys, list)
    evidence: list[tuple[object, ...]] = []
    for item in raw_lock_keys:
        assert isinstance(item, dict)
        identity = item.get("identity")
        assert isinstance(identity, list)
        assert len(identity) == 2
        assert all(isinstance(value, int) and value >= 0 for value in identity)
        assert identity[1] > 0
        evidence.append((item.get("logical_path"), item.get("lock_key"), *identity))
    return tuple(evidence)


def _assert_exact_process_bootstrap_evidence(
    records: list[dict[str, object]],
    *,
    workspace: Path,
    contested_stream: str,
) -> None:
    assert len(records) == _BOOTSTRAP_CONCURRENCY
    expected_worker_ids = {f"worker-{index:02d}" for index in range(_BOOTSTRAP_CONCURRENCY)}
    assert {record.get("worker_id") for record in records} == expected_worker_ids

    receipts: list[dict[str, object]] = []
    for record in records:
        assert record.get("phase") == "result"
        worker_id = record.get("worker_id")
        assert isinstance(worker_id, str)
        gate = record.get("gate")
        assert gate == {
            "ready_marker": f"{worker_id}.ready",
            "passed_marker": f"{worker_id}.passed",
            "shared_lock_held_through_operation": True,
        }
        receipt = record.get("receipt")
        assert isinstance(receipt, dict)
        assert receipt.get("workspace") == str(workspace.resolve())
        assert receipt.get("operation") == "bootstrap_workspace"
        assert receipt.get("streams") == list(fact_stream_bootstrap_streams())
        assert isinstance(receipt.get("storage_identity_token"), str)
        receipts.append(receipt)

    append_records = [record.get("append") for record in records]
    assert all(isinstance(append, dict) for append in append_records)
    typed_appends = [append for append in append_records if isinstance(append, dict)]
    assert {append.get("stream") for append in typed_appends} == {contested_stream}
    assert {append.get("appended_seq") for append in typed_appends} == set(range(1, _BOOTSTRAP_CONCURRENCY + 1))
    assert len({append.get("event_id") for append in typed_appends}) == _BOOTSTRAP_CONCURRENCY

    assert len({receipt["storage_identity_token"] for receipt in receipts}) == 1
    proofs_by_operation = {
        operation: tuple(_proof_for_operation(receipt, operation) for receipt in receipts)
        for operation in ("provision_authority", "enroll_stream_lock_keys")
    }
    for operation, proofs in proofs_by_operation.items():
        verdicts = tuple(proof.get("verdict") for proof in proofs)
        assert verdicts.count("created") == 1, operation
        assert verdicts.count("already_present") == _BOOTSTRAP_CONCURRENCY - 1, operation
        assert all(proof.get("final_validation") is True for proof in proofs)
        assert len({_identity_tuple(proof) for proof in proofs}) == 1

    enrollment_proofs = proofs_by_operation["enroll_stream_lock_keys"]
    expected_keys = _lock_key_evidence(enrollment_proofs[0])
    assert len(expected_keys) == len(fact_stream_bootstrap_streams())
    assert all(_lock_key_evidence(proof) == expected_keys for proof in enrollment_proofs)
    for key_index, key_evidence in enumerate(expected_keys):
        key_verdicts = tuple(
            proof["lock_keys"][key_index]["verdict"]
            for proof in enrollment_proofs
            if isinstance(proof["lock_keys"], list) and isinstance(proof["lock_keys"][key_index], dict)
        )
        assert len(key_verdicts) == _BOOTSTRAP_CONCURRENCY
        assert key_verdicts.count("created") == 1, key_evidence
        assert key_verdicts.count("already_present") == _BOOTSTRAP_CONCURRENCY - 1, key_evidence


def test_bootstrap_is_idempotent_across_exactly_64_independent_processes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    ready_directory = tmp_path / "ready"
    passed_directory = tmp_path / "passed"
    gate_file = tmp_path / "bootstrap.gate"
    workspace.mkdir()
    ready_directory.mkdir()
    passed_directory.mkdir()
    backend_root = Path(__file__).resolve().parents[3]
    assert backend_root.is_absolute()
    contested_stream = fact_stream_bootstrap_streams()[0]
    payload = {
        "workspace": str(workspace.resolve()),
        "platform_lock_root": str(default_platform_lock_root()),
        "streams": list(fact_stream_bootstrap_streams()),
        "maintenance_reason": "fact_stream_workspace_process_bootstrap_test",
        "ready_directory": str(ready_directory.resolve()),
        "passed_directory": str(passed_directory.resolve()),
        "gate_file": str(gate_file.resolve()),
        "contested_stream": contested_stream,
    }
    base_environment = os.environ.copy()
    base_environment.update(
        {
            "POLARIS_BOOTSTRAP_PROCESS_PAYLOAD": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(backend_root),
            "PYTHONUTF8": "1",
        }
    )
    processes: list[subprocess.Popen[str]] = []
    expected_markers = {f"worker-{index:02d}.ready" for index in range(_BOOTSTRAP_CONCURRENCY)}
    expected_passed_markers = {f"worker-{index:02d}.passed" for index in range(_BOOTSTRAP_CONCURRENCY)}
    release_count = 0

    try:
        with gate_file.open("w+", encoding="utf-8") as gate:
            fcntl.flock(gate.fileno(), fcntl.LOCK_EX)
            for index in range(_BOOTSTRAP_CONCURRENCY):
                child_environment = base_environment.copy()
                child_environment["POLARIS_BOOTSTRAP_PROCESS_WORKER_ID"] = f"worker-{index:02d}"
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-c", _PROCESS_BOOTSTRAP_PROGRAM],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        encoding="utf-8",
                        errors="strict",
                        env=child_environment,
                        text=True,
                    )
                )

            assert len(processes) == _BOOTSTRAP_CONCURRENCY
            _wait_for_ready_markers(ready_directory, expected_markers)
            assert {marker.name for marker in passed_directory.glob("*.passed")} == set()
            assert release_count == 0
            fcntl.flock(gate.fileno(), fcntl.LOCK_UN)
            release_count += 1
            assert release_count == 1

        outputs = _collect_process_outputs(processes)
        records: list[dict[str, object]] = []
        for process, (stdout, stderr) in zip(processes, outputs, strict=True):
            assert process.returncode == 0, f"child pid={process.pid} stderr={stderr!r} stdout={stdout!r}"
            records.append(_parse_result(stdout))
        assert {marker.name for marker in ready_directory.glob("*.ready")} == expected_markers
        assert {marker.name for marker in passed_directory.glob("*.passed")} == expected_passed_markers
        _assert_exact_process_bootstrap_evidence(records, workspace=workspace, contested_stream=contested_stream)

        strict_result = query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream=contested_stream,
                limit=_BOOTSTRAP_CONCURRENCY,
                strict_integrity=True,
            )
        )
        assert strict_result.total == _BOOTSTRAP_CONCURRENCY
        assert len(strict_result.events) == _BOOTSTRAP_CONCURRENCY
        assert {event["seq"] for event in strict_result.events} == set(range(1, _BOOTSTRAP_CONCURRENCY + 1))
        assert {event["event_id"] for event in strict_result.events} == {
            record["append"]["event_id"] for record in records if isinstance(record["append"], dict)
        }
        assert {event["payload"]["worker_id"] for event in strict_result.events} == {
            f"worker-{index:02d}" for index in range(_BOOTSTRAP_CONCURRENCY)
        }
    finally:
        _terminate_processes(processes)
