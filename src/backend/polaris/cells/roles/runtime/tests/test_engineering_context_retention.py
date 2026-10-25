"""Synthetic capture/SQLite retention proof; no provider or delivery claim."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.kernelone.llm.engine import context_store_retention as retention
from polaris.kernelone.llm.engine.contracts import AIRequest, ModelSpec, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.native_request_capture import NativeRequestCapturePort
from polaris.kernelone.storage.io_paths import resolve_storage_roots


def _captured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str = "director"
) -> tuple[EngineeringRunService, str, AIRequest, NativeRequestCapturePort]:
    # Removing producer pin acquisition must fail this real byte/SQLite assertion.
    service = EngineeringRunService(tmp_path)
    session = "synthetic-retention"
    service.store.create_run(
        RoleRunCommand(session, role, "Inspect synthetic evidence.", tmp_path),
        scope=PermissionScope((tmp_path,), (), frozenset(), False),
        limits=RunLimits(None, 10000, 2000),
        engine_version=service.engine_version,
        request_key=None,
    )
    turn = service.store.append_event(session, kind="turn_started", role=role, payload={})
    request = AIRequest(
        task_type=TaskType.GENERATION,
        role=role,
        provider_id="synthetic",
        model="synthetic",
        input="Inspect synthetic evidence.",
        context={
            "workspace": str(tmp_path),
            "session_id": session,
            "engine_version": service.engine_version,
            "turn_seq": turn,
            "call_id": "host-call",
        },
    )
    service.store.append_event(
        session, kind="request_prepared", role=role, payload={"turn_seq": turn, "request": request.to_dict()}
    )
    pressure = retention.ContextStoreRetention(
        str(tmp_path), retention.ContextStoreRetentionConfig(max_total_bytes=65536, sweep_min_interval_seconds=0)
    )
    monkeypatch.setattr(retention, "get_retention", lambda _: pressure)
    options: dict[str, Any] = {}
    # The pre-fix run uses the existing API. The final run supplies host-only authority.
    if hasattr(service, "context_reference_owner_resolver"):
        options["context_reference_owner"] = service.context_reference_owner_resolver(session)
    capture = NativeRequestCapturePort(
        request=request,
        model_spec=ModelSpec("synthetic", "openai_compat", "synthetic", 65536, 512, True),
        trace_id="synthetic-trace",
        messages=[{"role": "user", "content": "x" * 74000}],
        provider_request={"role": role, "final_request_context_audit": {}},
        persist=AIExecutor._store_context_messages_sync,
        workspace=str(tmp_path),
        native_request_observer=service.record_native_request_observation,
        **options,
    )
    capture.dispatch_sync(
        wire_request={
            "body": {"model": "synthetic", "messages": [{"role": "user", "content": "Inspect synthetic evidence."}]},
            "transport": {"kind": "synthetic"},
        },
        send=lambda _: {"usage": {"prompt_tokens": 2, "completion_tokens": 1}},
    )
    events = service.store.load_events(session, kinds=("llm_request_observed",))
    assert len(events) == 2
    runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
    for event in events:
        payload = event["payload"]
        assert payload["snapshot_status"] == "available"
        ref = payload["context_snapshot_ref"]
        assert (runtime / "contexts" / ref[:2] / ref).is_file(), "committed available ref was evicted by producer GC"
        saved = json.loads((runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
        assert saved["provider_request"]["final_physical_request"]["body"] == {
            "model": "synthetic",
            "messages": [{"role": "user", "content": "Inspect synthetic evidence."}],
        }
        assert saved["provider_request"]["role"] == role
        assert saved["provider_request"]["request_identity"]["call_id"] == "host-call"
    return service, session, request, capture


def test_spoofed_call_id_rejected_before_pin_publication_or_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, session, request, capture = _captured(tmp_path, monkeypatch)
    before = service.store.load_events(session, kinds=("llm_request_observed",))
    request.context["call_id"] = "forged-call-not-host-turn"
    sent = []
    with pytest.raises(RuntimeError, match="native_request_capture_persist_failed"):
        capture.dispatch_sync(wire_request={"body": {"messages": []}}, send=lambda _: sent.append("sent"))
    assert sent == []
    assert service.store.load_events(session, kinds=("llm_request_observed",)) == before


@pytest.mark.parametrize("mismatch", ["event", "snapshot"])
def test_cold_reconciliation_rejects_call_id_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    service, session, _, _ = _captured(tmp_path, monkeypatch)
    payload = dict(service.store.load_events(session, kinds=("llm_request_observed",))[0]["payload"])
    if mismatch == "event":
        payload["call_id"] = "forged-call-not-host-turn"
    else:
        repository = retention.ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
        snapshot = json.loads(
            Path(repository.snapshot_path(payload["context_snapshot_ref"])).read_text(encoding="utf-8")
        )
        snapshot["call_id"] = "forged-call-not-host-turn"
        snapshot["provider_request"]["request_identity"]["call_id"] = "forged-call-not-host-turn"
        payload["context_snapshot_ref"] = repository.persist_snapshot_bytes(
            json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        )
    service.store.append_event(session, kind="llm_request_observed", role="director", payload=payload)
    original = service.store.load_events(session)
    with pytest.raises(PermissionError, match="call identity mismatch"):
        EngineeringRunService(tmp_path)
    assert service.store.load_events(session) == original


def test_consumer_rejects_changed_call_id_before_available_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, session, request, _ = _captured(tmp_path, monkeypatch)
    before = service.store.load_events(session, kinds=("llm_request_observed",))
    observation = dict(before[-1]["payload"])
    request.context["call_id"] = "forged-call-not-host-turn"
    with pytest.raises(PermissionError, match="call identity mismatch"):
        service.record_native_request_observation(request, observation)
    assert service.store.load_events(session, kinds=("llm_request_observed",)) == before


def test_valid_previous_owner_correlation_is_retained_during_cold_call_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    service, session, _, _ = _captured(tmp_path, monkeypatch)
    payload = service.store.load_events(session, kinds=("llm_request_observed",))[0]["payload"]
    ref = payload["context_snapshot_ref"]
    repository = retention.ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    original = Path(repository.snapshot_path(ref)).read_bytes()
    current = repository.query_reference_pins(ref)[0].owner
    previous = replace(current, reference_id=json.dumps(json.loads(current.reference_id)[:3], separators=(",", ":")))
    repository.persist_snapshot_bytes(original, reference_owner=previous)
    Path(repository.reference_pin_path(ref, current)).unlink()
    cold = EngineeringRunService(tmp_path)
    assert cold.context_reference_recovery["present_references"] == 2
    assert {pin.owner for pin in repository.query_reference_pins(ref)} == {previous, current}
    assert Path(repository.snapshot_path(ref)).read_bytes() == original


@pytest.mark.parametrize("role", ["pm", "chief_engineer", "director", "qa"])
def test_native_capture_available_sqlite_refs_survive_producer_gc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    _captured(tmp_path, monkeypatch, role)


@pytest.mark.parametrize("prepared", [False, True])
def test_missing_prepared_call_field_uses_canonical_host_identity(tmp_path: Path, prepared: bool) -> None:
    service = EngineeringRunService(tmp_path)
    session = "synthetic-canonical-host"
    service.store.create_run(
        RoleRunCommand(session, "director", "Synthetic identity only.", tmp_path),
        scope=PermissionScope((tmp_path,), (), frozenset(), False),
        limits=RunLimits(None, 10000, 2000),
        engine_version=service.engine_version,
        request_key=None,
    )
    turn = service.store.append_event(session, kind="turn_started", role="director", payload={})
    if prepared:
        service.store.append_event(
            session, kind="request_prepared", role="director", payload={"turn_seq": turn, "request": {"context": {}}}
        )
    call_id = f"{session}:director:{turn}"
    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        input="Synthetic identity only.",
        context={
            "session_id": session,
            "workspace": str(tmp_path),
            "engine_version": service.engine_version,
            "turn_seq": turn,
            "call_id": call_id,
        },
    )
    resolver = service.context_reference_owner_resolver(session)
    assert json.loads(resolver(request, "provider").reference_id)[-1] == call_id
    request.context["call_id"] = "context-must-not-grant-call-identity"
    with pytest.raises(PermissionError, match="call identity mismatch"):
        resolver(request, "provider")


def test_cold_call_binding_keeps_other_engine_history_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, _, _ = _captured(tmp_path, monkeypatch)
    session = "synthetic-historical-engine"
    service.store.create_run(
        RoleRunCommand(session, "director", "Historical read only.", tmp_path),
        scope=PermissionScope((tmp_path,), (), frozenset(), False),
        limits=RunLimits(None, 10000, 2000),
        engine_version="synthetic_frozen_old_engine",
        request_key=None,
    )
    turn = service.store.append_event(session, kind="turn_started", role="director", payload={})
    service.store.append_event(
        session,
        kind="llm_request_observed",
        role="director",
        payload={
            "turn_seq": turn,
            "call_id": "historical-call-with-own-format",
            "context_snapshot_ref": "f" * 24,
            "snapshot_status": "available",
            "provider_request_id": "historical-provider",
        },
    )
    before = service.store.load_events(session)
    cold = EngineeringRunService(tmp_path)
    assert cold.context_reference_recovery["present_references"] == 2
    assert cold.store.load_events(session) == before


def test_cold_process_migrates_present_refs_preserves_missing_and_both_read_apis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, session, _, _ = _captured(tmp_path, monkeypatch)
    events = service.store.load_events(session, kinds=("llm_request_observed",))
    repository = retention.ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    sealed = {}
    for event in events:
        ref = event["payload"]["context_snapshot_ref"]
        sealed[ref] = Path(repository.snapshot_path(ref)).read_bytes()
        # Emulate old, still-present unprotected refs. No historical target touched.
        directory = Path(repository.contexts_root) / "pins" / ref[:2] / ref
        for pin in directory.iterdir():
            pin.unlink()
        directory.rmdir()
    missing = "f" * 24
    service.store.append_event(
        session,
        kind="llm_request_observed",
        role="director",
        payload={**events[0]["payload"], "context_snapshot_ref": missing},
    )
    before = service.store.load_events(session)
    script = """import json,sys
from pathlib import Path
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.test_engineering_context_audit import _reader
from polaris.kernelone.llm.engine.context_store_retention import ContextStoreRetention,ContextStoreRetentionConfig
w=Path(sys.argv[1]); service=EngineeringRunService(w)
report=ContextStoreRetention(str(w),ContextStoreRetentionConfig(ttl_seconds=0,max_total_bytes=65536,max_files=0)).sweep()
assert report.inventory_complete and not report.budget_satisfied
def pins():
    return {str(path):path.read_bytes() for path in (w/'.polaris/runtime/contexts/pins').rglob('*.json')}
before=pins()
with _reader(w) as client:
    for ref in json.loads(sys.argv[2]):
        context=client.get('/v2/context/'+ref,params={'workspace':str(w)})
        final=client.get('/v2/context/'+ref+'/final-request',params={'workspace':str(w)})
        assert context.status_code==final.status_code==200,(context.text,final.text)
    assert client.get('/v2/context/'+'f'*24,params={'workspace':str(w)}).status_code==404
assert pins()==before,'read APIs mutated retention pins'
print(json.dumps(service.context_reference_recovery))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path), json.dumps(list(sealed))],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    recovery = json.loads(result.stdout)
    assert recovery["present_references"] == 2
    assert recovery["unavailable_references"] == [missing]
    assert recovery["replayed_operations"] == 0
    assert service.store.load_events(session) == before
    assert all(Path(repository.snapshot_path(ref)).read_bytes() == content for ref, content in sealed.items())


def test_host_resolver_rejects_spoofed_context_and_stale_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, session, request, _ = _captured(tmp_path, monkeypatch)
    resolver = service.context_reference_owner_resolver(session)
    request.context["session_id"] = "forged-run"
    with pytest.raises(PermissionError):
        resolver(request, "provider")
    request.context["session_id"] = session
    service.store.append_event(session, kind="turn_started", role="qa", payload={})
    with pytest.raises(PermissionError):
        resolver(request, "provider")


def test_unpinned_observer_reference_is_recorded_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, session, request, _ = _captured(tmp_path, monkeypatch)
    ref = retention.ContextSnapshotAuditPinRepository(workspace=str(tmp_path)).persist_snapshot_bytes(
        b"synthetic-unowned"
    )
    service.record_native_request_observation(
        request,
        {"context_snapshot_ref": ref, "snapshot_status": "available", "provider_request_id": "unowned-provider"},
    )
    saved = service.store.load_events(session, kinds=("llm_request_observed",))[-1]["payload"]
    assert saved["snapshot_status"] == "unavailable" and saved["context_snapshot_ref"] is None
    assert saved["snapshot_error"]["code"] == "context_reference_pin_unavailable"


def test_terminal_pin_failure_records_unavailable_without_replaying_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, session, request, capture = _captured(tmp_path, monkeypatch)
    original = retention.ContextSnapshotAuditPinRepository._write_immutable
    pins = 0

    def fail_second_pin(self: Any, path: str, content: bytes, *, kind: str) -> None:
        nonlocal pins
        if kind == "pin":
            pins += 1
            if pins == 2:
                raise OSError("synthetic terminal disk full")
        original(self, path, content, kind=kind)

    monkeypatch.setattr(retention.ContextSnapshotAuditPinRepository, "_write_immutable", fail_second_pin)
    sent = []
    assert (
        capture.dispatch_sync(wire_request={"body": {"messages": []}}, send=lambda _: sent.append("sent") or "done")
        == "done"
    )
    assert sent == ["sent"]
    saved = service.store.load_events(session, kinds=("llm_request_observed",))[-1]["payload"]
    assert saved["snapshot_status"] == "unavailable" and saved["context_snapshot_ref"] is None
    assert request.context["context_snapshot_degraded"]["code"] == "NATIVE_FINAL_CONTEXT_STORE_FAILED"


def test_request_context_cannot_self_authorize_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, session, request, capture = _captured(tmp_path, monkeypatch)
    capture._reference_owner = None
    request.context["context_reference_owner"] = {"namespace": "engineering", "owner_id": session}
    capture.dispatch_sync(wire_request={"body": {"messages": []}}, send=lambda _: "synthetic")
    assert (
        service.store.load_events(session, kinds=("llm_request_observed",))[-1]["payload"]["snapshot_status"]
        == "unavailable"
    )


def test_cold_owner_rejects_snapshot_identity_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, session, _, _ = _captured(tmp_path, monkeypatch)
    event = service.store.load_events(session, kinds=("llm_request_observed",))[0]["payload"]
    service.store.append_event(
        session,
        kind="llm_request_observed",
        role="director",
        payload={**event, "provider_request_id": "foreign-provider"},
    )
    with pytest.raises(PermissionError, match="host identity mismatch"):
        EngineeringRunService(tmp_path)


def test_process_death_after_sqlite_commit_keeps_reference_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, session, _, capture = _captured(tmp_path, monkeypatch)

    def record_then_die(request: AIRequest, observation: Any) -> None:
        service.record_native_request_observation(request, observation)
        os._exit(73)

    capture._observer = record_then_die

    def child_capture() -> None:
        capture.dispatch_sync(
            wire_request={"body": {"messages": []}},
            send=lambda _: (_ for _ in ()).throw(AssertionError("send must not execute")),
        )

    child = multiprocessing.get_context("fork").Process(target=child_capture)
    child.start()
    child.join(5)
    assert child.exitcode == 73
    before = service.store.load_events(session, kinds=("llm_request_observed",))
    assert len(before) == 3 and before[-1]["payload"]["phase"] == "prepared"
    cold = EngineeringRunService(tmp_path)
    assert cold.store.load_events(session, kinds=("llm_request_observed",)) == before
    assert cold.context_reference_recovery["present_references"] == 3
    assert cold.context_reference_recovery["replayed_operations"] == 0


def test_active_owner_defers_cold_migration_and_cannot_enable_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, session, request, _ = _captured(tmp_path, monkeypatch)
    with service.store.acquire_workspace(tmp_path):
        observer = EngineeringRunService(tmp_path)
        assert observer.context_reference_recovery["status"] == "deferred_active_owner"
        with pytest.raises(RuntimeError, match="cold reconciliation incomplete"):
            observer.context_reference_owner_resolver(session)(request, "provider")
