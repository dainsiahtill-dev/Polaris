"""Committed engineering events, atomic snapshots and disposable delivery ports."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
)
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)


def _create(store: EngineeringSessionStore) -> None:
    store.create_run(
        RoleRunCommand("events", "director", "goal", store.workspace),
        scope=PermissionScope((), (), frozenset(), False),
        limits=RunLimits(None, 1000000, 64000),
        engine_version="engineering_v2",
        request_key=None,
    )


def _oversize_fixture(workspace: Path, count: int, content: str) -> dict[str, Any]:
    from polaris.bootstrap.engineering_runtime import observe_engineering_run
    from polaris.delivery.ws.engineering_events import engineering_event_envelope

    service = EngineeringRunService(workspace)
    service.store.create_run(
        RoleRunCommand(
            "oversize",
            "director",
            "synthetic failed check records",
            workspace,
            required_checks=tuple(f"synthetic-check-{index}" for index in range(count)),
        ),
        scope=PermissionScope((), (), frozenset(), False),
        limits=RunLimits(None, 1000000, 64000),
        engine_version="engineering_v2",
        request_key=None,
    )
    before = observe_engineering_run(service, "oversize", workspace)
    checks = tuple(
        ToolObservation(f"check-{index}", "execute_command", False, content, 1, False) for index in range(count)
    )
    service.store.update_run("oversize", status="failed", verification="failed", checks=checks)
    event = service.store.load_events("oversize")[-1]
    _, envelope = engineering_event_envelope({"session_id": "oversize", "workspace": str(workspace), **event})
    return {
        "before": before,
        "event": envelope.to_dict(),
        "after": observe_engineering_run(service, "oversize", workspace),
    }


def _actual_typescript_fold(workspace: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """Bundle the real TS parser/reducer; do not mirror its acceptance rules."""
    root = Path(__file__).resolve().parents[7]
    fixture_path = workspace / "wire-fixture.json"
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    consumer = workspace / "consumer.cjs"
    consumer.write_text(
        "const fs=require('fs');const path=require('path');\n"
        f"const root={json.dumps(str(root))};const esbuild=require(path.join(root,'node_modules/esbuild'));\n"
        "const output=path.join(__dirname,'projection.cjs');\n"
        "esbuild.buildSync({entryPoints:[path.join(root,'src/frontend/src/runtime/engineeringRunProjection.ts')],bundle:true,platform:'node',format:'cjs',alias:{'@':path.join(root,'src/frontend/src')},outfile:output,logLevel:'silent'});\n"
        "const p=require(output);const f=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));\n"
        "const base=p.hydrateEngineeringRun(p.newEngineeringProjection(f.before.workspace,f.before.session_id),f.before);\n"
        "const event=p.engineeringEventFromMessage(f.event);const live=p.applyEngineeringEvent(base,event);\n"
        "const cold=p.hydrateEngineeringRun(p.newEngineeringProjection(f.after.workspace,f.after.session_id),f.after);\n"
        "const duplicate=p.applyEngineeringEvent(live,event);const recovered=p.hydrateEngineeringRun(live,f.after);\n"
        "console.log(JSON.stringify({eventParsed:!!event,liveError:live.error,liveStatus:live.snapshot.status,liveVerification:live.snapshot.verification,liveQa:live.snapshot.qa_verdict,liveOutcome:live.snapshot.project_outcome,liveBasis:live.snapshot.acceptance_basis,liveRequireQa:live.snapshot.require_qa,liveChecks:live.snapshot.checks.length,liveRequired:live.snapshot.required_checks.length,liveFailed:live.snapshot.checks.filter(c=>!c.ok&&c.exit_code===1).length,coldError:cold.error,coldStatus:cold.snapshot.status,coldChecks:cold.snapshot.checks.length,coldRequired:cold.snapshot.required_checks.length,snapshotRequiredSeq:live.snapshotRequiredSeq??null,duplicateSnapshotRequiredSeq:duplicate.snapshotRequiredSeq??null,recoveredStatus:recovered.snapshot.status,recoveredChecks:recovered.snapshot.checks.length,recoveredSnapshotRequiredSeq:recovered.snapshotRequiredSeq??null}));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(consumer), str(fixture_path)], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("count", "content"),
    [(20, "failure " + "x" * 5000), (51, "failure " + "x" * 300), (120, "失败 " + "界" * 2000)],
    ids=["twenty-large", "fifty-one-count", "unicode-many"],
)
def test_compact_failed_checks_remain_valid_for_actual_typescript_consumer(
    isolated_workspace: Path, count: int, content: str
) -> None:
    fixture = _oversize_fixture(isolated_workspace, count, content)
    projected = fixture["event"]["payload"]["payload"]
    result = _actual_typescript_fold(isolated_workspace, fixture)
    assert result["liveError"] is None, result
    assert result["liveStatus"] == result["coldStatus"] == "failed"
    assert result["liveVerification"] == "failed" and result["liveFailed"] == count
    assert result["liveChecks"] == result["coldChecks"] == count
    assert result["liveRequired"] == result["coldRequired"] == count
    assert all(
        isinstance(check["content"], str) and content.startswith(check["content"]) for check in projected["checks"]
    )
    if any(check["content"] != content for check in projected["checks"]):
        assert projected.get("truncated_fields")
    assert len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) <= 60000


def test_impossible_required_projection_requests_same_sql_snapshot_without_losing_count(
    isolated_workspace: Path,
) -> None:
    fixture = _oversize_fixture(isolated_workspace, 2000, "real failure content")
    projected = fixture["event"]["payload"]["payload"]
    assert projected.get("snapshot_required") is True
    assert projected["checks_count"] == 2000 and projected["required_checks_count"] == 2000
    assert "checks" not in projected
    assert fixture["after"]["status"] == "failed" and len(fixture["after"]["checks"]) == 2000
    assert len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) <= 60000
    result = _actual_typescript_fold(isolated_workspace, fixture)
    assert result["liveError"] is None, result
    assert result["snapshotRequiredSeq"] == result["duplicateSnapshotRequiredSeq"] == fixture["event"]["payload"]["seq"]
    assert result["liveStatus"] == fixture["before"]["status"] and result["liveChecks"] == 0
    assert result["recoveredStatus"] == "failed" and result["recoveredChecks"] == 2000
    assert result["recoveredSnapshotRequiredSeq"] is None


def test_event_bridge_fresh_import_does_not_reintroduce_legacy_run_authorities(isolated_workspace: Path) -> None:
    script = """
import importlib.abc
import sys
blocked = ('polaris.cells.roles.kernel','polaris.cells.runtime.task_runtime','polaris.cells.roles.session',
           'polaris.cells.runtime.run_ledger','polaris.cells.runtime.job_token','polaris.cells.director.execution',
           'polaris.cells.runtime.execution_broker','polaris.cells.roles.runtime.public.service')
class Sentinel(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in blocked):
   raise RuntimeError('legacy import blocked: ' + fullname)
sys.meta_path.insert(0, Sentinel())
from polaris.delivery.ws.engineering_events import engineering_event_envelope
from polaris.bootstrap.engineering_runtime import create_engineering_service
subject,envelope = engineering_event_envelope({'session_id':'cold','workspace':sys.argv[1],'seq':1,
    'role':'director','kind':'run_updated','payload':{'status':'running'},'created_at':1.0})
assert subject == 'hp.runtime.' + envelope.workspace_key + '.event.engineering'
print('engineering bridge import clean')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(isolated_workspace)],
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "engineering bridge import clean"


def test_subject_mapping_compatibility_reexports_one_lightweight_owner() -> None:
    from polaris.delivery.ws import runtime_subjects
    from polaris.delivery.ws.endpoints import models, protocol_utils

    assert models.V2_CHANNEL_TO_SUBJECT is runtime_subjects.V2_CHANNEL_TO_SUBJECT
    assert protocol_utils.V2_CHANNEL_TO_SUBJECT is runtime_subjects.V2_CHANNEL_TO_SUBJECT
    assert protocol_utils.resolve_v2_subject is runtime_subjects.resolve_v2_subject
    for channel in (
        "log.system",
        "log.process",
        "log.llm",
        "event.file_edit",
        "event.task_trace",
        "status.snapshot",
        "status.resident",
        "status.instances",
        "event.engineering",
    ):
        expected = f"hp.runtime.workspace-key.{channel}"
        assert runtime_subjects.resolve_v2_subject("workspace-key", channel) == expected
        assert protocol_utils.build_v2_subscription_subjects("workspace-key", [channel]) == [expected]
    assert set(
        protocol_utils.build_v2_subscription_subjects("workspace-key", ["event.factory", "event.bench", "chat"])
    ) == {"hp.runtime.workspace-key.event.factory.>", "hp.runtime.bench.>", "hp.runtime.chat.>"}


def test_post_commit_batch_has_closed_connection_and_rollback_publishes_nothing(isolated_workspace: Path) -> None:
    batches: list[tuple[dict[str, Any], ...]] = []
    connections: list[sqlite3.Connection] = []

    def committed(batch: tuple[dict[str, Any], ...]) -> None:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connections[-1].execute("SELECT 1")
        with sqlite3.connect(store.database_path) as reader:
            assert reader.execute("SELECT COUNT(*) FROM events").fetchone()[0] == len(batch)
        batches.append(batch)

    store = EngineeringSessionStore(isolated_workspace, on_committed_events=committed)
    _create(store)
    with pytest.raises(ValueError, match="rollback"), store._transaction() as connection:
        connections.append(connection)
        store._append_event(connection, "events", kind="first", role="director", payload={"value": 1})
        raise ValueError("rollback")
    assert not batches and store.load_events("events") == []
    with store._transaction() as connection:
        connections.append(connection)
        store._append_event(connection, "events", kind="first", role="director", payload={"value": 1})
        store._append_event(connection, "events", kind="second", role="director", payload={"value": 2})
    assert [event["seq"] for event in batches[0]] == [1, 2]
    assert all(
        event["workspace"] == str(isolated_workspace) and event["session_id"] == "events" for event in batches[0]
    )


def test_observer_failure_preserves_committed_observation(isolated_workspace: Path) -> None:
    def unavailable(_batch: tuple[dict[str, Any], ...]) -> None:
        raise RuntimeError("delivery unavailable")

    store = EngineeringSessionStore(isolated_workspace, on_committed_events=unavailable)
    _create(store)
    assert store.append_event("events", kind="fact", role="director", payload={"ok": True}) == 1
    assert store.load_events("events")[0]["payload"] == {"ok": True}
    store.record_tool_dispatch(
        "events", turn_seq=1, call_id="read", tool="read_file", arguments={"file": "a"}, role="director"
    )
    store.record_tool_observation(
        "events",
        turn_seq=1,
        observation=ToolObservation("read", "read_file", True, "actual", None, False),
        role="director",
    )
    assert store.pending_tool_calls("events") == []
    assert store.load_events("events")[-1]["payload"]["content"] == "actual"
    assert "delivery unavailable" in str(store.event_delivery_error)


def test_snapshot_has_durable_state_watermark_and_bounded_events(isolated_workspace: Path) -> None:
    store = EngineeringSessionStore(isolated_workspace)
    _create(store)
    store.append_event("events", kind="role_started", role="pm", payload={})
    store.append_event("events", kind="role_finished", role="pm", payload={"status": "completed"})
    for index in range(205):
        store.append_event("events", kind="fact", role="director", payload={"index": index})
    store.update_run("events", status="completed", text="actual terminal", verification="not_run")
    snapshot = store.load_snapshot("events", workspace=isolated_workspace, event_limit=200)
    assert snapshot["run"]["status"] == "completed"
    assert len(snapshot["events"]) == 200
    assert snapshot["last_seq"] == snapshot["events"][-1]["seq"]
    assert snapshot["events"][-1]["kind"] == "run_updated"
    assert snapshot["events"][-1]["payload"]["status"] == "completed"
    assert [event["role"] for event in snapshot["role_events"]] == ["pm", "pm"]


def test_snapshot_cannot_mix_earlier_row_with_later_event_watermark(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EngineeringSessionStore(isolated_workspace)
    _create(store)
    original = store._require_run
    writer_done = threading.Event()
    writers: list[threading.Thread] = []
    main_thread = threading.current_thread()

    def select_row(connection: sqlite3.Connection, session_id: str) -> Any:
        row = original(connection, session_id)
        if threading.current_thread() is main_thread and not writers:

            def write() -> None:
                store.update_run("events", status="completed")
                writer_done.set()

            thread = threading.Thread(target=write)
            writers.append(thread)
            thread.start()
            writer_done.wait(0.15)
        return row

    monkeypatch.setattr(store, "_require_run", select_row)
    snapshot = store.load_snapshot("events", workspace=isolated_workspace)
    writers[0].join(timeout=3)
    assert writer_done.is_set()
    assert snapshot["run"]["status"] == "pending" and snapshot["last_seq"] == 0
    assert snapshot["events"] == []
    assert store.load_snapshot("events", workspace=isolated_workspace)["run"]["status"] == "completed"


def test_revision_push_cannot_project_historical_verification_as_current_pass(isolated_workspace: Path) -> None:
    store = EngineeringSessionStore(isolated_workspace)
    _create(store)
    store.update_run("events", verification="passed")
    store.update_run("events", workspace_revision=1)
    assert store.load_events("events")[-1]["payload"]["verification"] == "unknown"


def test_terminal_snapshot_and_push_do_not_leave_unknown_role_running(isolated_workspace: Path) -> None:
    from polaris.bootstrap.engineering_runtime import observe_engineering_run

    service = EngineeringRunService(isolated_workspace)
    _create(service.store)
    service.store.append_event("events", kind="role_started", role="director", payload={})
    service.store.update_run("events", status="interrupted")
    assert service.store.load_events("events")[-1]["payload"]["roles"]["director"] == "interrupted"
    snapshot = observe_engineering_run(service, "events", isolated_workspace)
    assert snapshot["roles"]["director"] == "interrupted" and snapshot["active_roles"] == []


async def test_failed_publication_never_replays_native_tools_and_snapshot_recovers(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict[str, Any]] = []

    async def unavailable(event: dict[str, Any]) -> bool:
        published.append(event)
        return False

    scripted_provider.enqueue_json(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "once",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps({"file": "once.txt", "content": "actual effect"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    scripted_provider.enqueue_json(
        {"choices": [{"message": {"role": "assistant", "content": "actual reply"}, "finish_reason": "stop"}]}
    )
    service = EngineeringRunService(
        isolated_workspace,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        event_publisher=unavailable,
    )
    command = RoleRunCommand("active", "director", "respond", isolated_workspace)
    await service.start(
        command,
        scope=PermissionScope((isolated_workspace,), (isolated_workspace,), frozenset({"write_file"}), False),
        limits=RunLimits(None, 1000000, 64000),
    )
    await service.wait("active", workspace=isolated_workspace)
    await service.flush_events()
    snapshot = service.snapshot("active", workspace=isolated_workspace)
    assert snapshot["status"] == "completed" and snapshot["last_seq"] > 0
    assert "publish" in str(service.event_delivery_error).lower()
    assert len(scripted_provider.requests) == 2
    assert (isolated_workspace / "once.txt").read_text(encoding="utf-8") == "actual effect"
    assert len([event for event in snapshot["events"] if event["kind"] == "tool_started"]) == 1
    assert published[-1]["kind"] == "run_ended"
    assert [event["seq"] for event in published if event["session_id"] == "active"] == list(
        range(1, snapshot["last_seq"] + 1)
    )


async def test_bridge_compacts_large_auxiliary_payload_and_keeps_sql_sequence(isolated_workspace: Path) -> None:
    from polaris.delivery.ws.endpoints.protocol_utils import build_v2_subscription_subjects
    from polaris.delivery.ws.engineering_events import engineering_event_envelope

    event = {
        "session_id": "events",
        "workspace": str(isolated_workspace),
        "seq": 42,
        "role": "director",
        "kind": "provider_response",
        "payload": {"raw": {"secret_body": "x" * 400000}, "ok": True},
        "created_at": 1.0,
    }
    subject, envelope = engineering_event_envelope(event)
    assert subject in build_v2_subscription_subjects(envelope.workspace_key, ["event.engineering"])
    assert envelope.cursor == 0 and envelope.payload["seq"] == 42
    assert envelope.channel == "event.engineering" and envelope.payload["workspace"] == str(isolated_workspace)
    encoded = json.dumps(envelope.to_dict(), ensure_ascii=False)
    assert len(encoded.encode("utf-8")) < 20000 and "secret_body" not in encoded


def test_runtime_observation_uses_bound_host_request_and_same_events(isolated_workspace: Path) -> None:
    from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType

    service = EngineeringRunService(isolated_workspace)
    _create(service.store)
    turn = service.store.append_event("events", kind="turn_started", role="director", payload={})
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="goal",
        context={
            "session_id": "events",
            "workspace": str(isolated_workspace),
            "engine_version": "engineering_v2",
            "turn_seq": turn,
            "call_id": f"events:director:{turn}",
        },
    )
    service.record_native_request_observation(
        request,
        {"phase": "prepared", "context_snapshot_ref": "a" * 24, "physical_send_count": None, "usage_status": "unknown"},
    )
    event = service.store.load_events("events")[-1]
    assert event["kind"] == "llm_request_observed" and event["role"] == "director"
    assert event["payload"]["turn_seq"] == turn and event["payload"]["physical_send_count"] is None
    request.context["workspace"] = str(isolated_workspace.parent)
    with pytest.raises(PermissionError):
        service.record_native_request_observation(request, {"phase": "prepared"})


async def test_default_native_client_records_actual_capture_metadata(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Configuration only: the service constructs the actual default K1 client.
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    scripted_provider.enqueue_json(
        {
            "choices": [{"message": {"role": "assistant", "content": "actual"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
        }
    )
    service = EngineeringRunService(isolated_workspace)
    await service.start(
        RoleRunCommand("captured", "director", "respond", isolated_workspace),
        scope=PermissionScope((), (), frozenset(), False),
        limits=RunLimits(None, 1000000, 64000),
    )
    assert (await service.wait("captured", workspace=isolated_workspace)).status == "completed"
    observations = [event for event in service.store.load_events("captured") if event["kind"] == "llm_request_observed"]
    assert {event["payload"]["phase"] for event in observations} == {"prepared", "terminal"}
    assert all(event["role"] == "director" and event["payload"]["turn_seq"] > 0 for event in observations)
    assert all(len(event["payload"]["context_snapshot_ref"]) == 24 for event in observations)
    assert all(event["payload"]["physical_send_count"] is None for event in observations)
    assert observations[-1]["payload"]["usage_status"] == "reported"
    assert len(scripted_provider.requests) == 1


async def test_native_observation_journal_failure_does_not_resend_provider(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    scripted_provider.enqueue_json(
        {"choices": [{"message": {"role": "assistant", "content": "single actual reply"}, "finish_reason": "stop"}]}
    )
    service = EngineeringRunService(isolated_workspace)
    append = service.store.append_event

    def unavailable(session_id: str, *, kind: str, role: str, payload: Any) -> int:
        if kind == "llm_request_observed":
            raise RuntimeError("observation journal unavailable")
        return append(session_id, kind=kind, role=role, payload=payload)

    monkeypatch.setattr(service.store, "append_event", unavailable)
    await service.start(
        RoleRunCommand("observation-error", "director", "respond", isolated_workspace),
        scope=PermissionScope((), (), frozenset(), False),
        limits=RunLimits(None, 1000000, 64000),
    )
    result = await service.wait("observation-error", workspace=isolated_workspace)
    assert result.text == "single actual reply" and result.verification == "not_run"
    assert len(scripted_provider.requests) == 1
    assert (
        "observation journal unavailable"
        in service.snapshot("observation-error", workspace=isolated_workspace)["native_observation_error"]
    )


async def test_real_private_jetstream_ack_reconnect_and_workspace_filter(
    isolated_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    scripted_provider: ScriptedProvider,
) -> None:
    from polaris.delivery.ws import engineering_events
    from polaris.infrastructure.messaging.nats import ws_consumer_manager
    from polaris.infrastructure.messaging.nats.client import NATSClient, NATSConfig

    executable = shutil.which("nats-server")
    if executable is None:
        pytest.skip("real JetStream probe requires nats-server")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with (isolated_workspace / "nats-server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [executable, "-a", "127.0.0.1", "-p", str(port), "-js", "-sd", str(isolated_workspace / "jetstream")],
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        client = NATSClient(
            NATSConfig(
                servers=[f"nats://127.0.0.1:{port}"],
                allow_reconnect=False,
                max_reconnect_attempts=0,
                connect_timeout=0.2,
            )
        )
        managers: list[Any] = []
        try:
            async with asyncio.timeout(8):
                while not client.is_connected:
                    try:
                        await client.connect()
                    except (OSError, RuntimeError):
                        assert process.poll() is None
                        await asyncio.sleep(0.02)

            async def private_client() -> NATSClient:
                return client

            monkeypatch.setattr(engineering_events, "get_default_client", private_client)
            monkeypatch.setattr(
                engineering_events, "get_default_client_snapshot", lambda: {"is_connected": client.is_connected}
            )
            monkeypatch.setattr(ws_consumer_manager, "get_default_client", private_client)
            event: dict[str, Any] = {
                "session_id": "events",
                "workspace": str(isolated_workspace),
                "seq": 42,
                "role": "director",
                "kind": "run_updated",
                "payload": {"status": "running"},
                "created_at": 1.0,
            }
            _, envelope = engineering_events.engineering_event_envelope(event)
            manager = ws_consumer_manager.JetStreamConsumerManager(
                client_id=f"engineering-{uuid4().hex}",
                workspace_key=envelope.workspace_key,
                channels=["event.engineering"],
                tail=0,
            )
            managers.append(manager)
            assert await manager.connect()
            assert await engineering_events.publish_engineering_event(event)
            first = await manager.next_message(timeout=5)
            assert first is not None and first.payload["seq"] == 42 and first.cursor > 0 and first.cursor != 42
            await manager.ack_cursor(first.cursor)
            cursor = manager.get_current_cursor()
            await manager.disconnect()
            event["seq"] = 43
            assert await engineering_events.publish_engineering_event(event)
            resumed = ws_consumer_manager.JetStreamConsumerManager(
                client_id=f"engineering-{uuid4().hex}",
                workspace_key=envelope.workspace_key,
                channels=["event.engineering"],
                initial_cursor=cursor,
            )
            managers.append(resumed)
            assert await resumed.connect()
            second = await resumed.next_message(timeout=5)
            assert second is not None and second.payload["seq"] == 43 and second.cursor > cursor
            foreign = isolated_workspace / "foreign"
            foreign.mkdir()
            assert await engineering_events.publish_engineering_event({**event, "workspace": str(foreign), "seq": 999})
            assert await engineering_events.publish_engineering_event({**event, "seq": 44})
            third = await resumed.next_message(timeout=5)
            assert third is not None and third.payload["seq"] == 44 and third.workspace_key == envelope.workspace_key
            await resumed.ack_cursor(third.cursor)
            # The real native capture, SQLite hook and JetStream share one run.
            scripted_provider.client("openai", isolated_workspace, monkeypatch)
            scripted_provider.enqueue_json(
                {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "captured over native HTTP"},
                            "finish_reason": "stop",
                        }
                    ]
                }
            )
            service = EngineeringRunService(
                isolated_workspace, event_publisher=engineering_events.publish_engineering_event
            )
            await service.start(
                RoleRunCommand("bridge-native", "director", "respond", isolated_workspace),
                scope=PermissionScope((), (), frozenset(), False),
                limits=RunLimits(None, 1000000, 64000),
            )
            await service.wait("bridge-native", workspace=isolated_workspace)
            await service.flush_events()
            received: list[dict[str, Any]] = []
            async with asyncio.timeout(8):
                while not received or received[-1]["kind"] != "run_ended":
                    message = await resumed.next_message(timeout=5)
                    assert message is not None
                    if message.run_id == "bridge-native":
                        received.append(message.payload)
                    await resumed.ack_cursor(message.cursor)
            snapshot = service.snapshot("bridge-native", workspace=isolated_workspace)
            assert [event["seq"] for event in received] == list(range(1, snapshot["last_seq"] + 1))
            assert {event["payload"]["phase"] for event in received if event["kind"] == "llm_request_observed"} == {
                "prepared",
                "terminal",
            }
            assert not service.event_delivery_error and not snapshot["native_observation_error"]
            assert len(scripted_provider.requests) == 1
        finally:
            for manager in managers:
                await manager.disconnect()
            await client.disconnect()
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


@pytest.mark.parametrize(
    ("required", "verdict", "outcome", "basis"),
    [
        (True, "approved", "accepted", "independent_qa"),
        (True, "unknown", "unknown", None),
        (False, "not_run", "accepted", "checks_only"),
    ],
)
def test_oversize_live_projection_preserves_authoritative_qa_outcome_facts(
    isolated_workspace: Path,
    required: bool,
    verdict: str,
    outcome: str,
    basis: str | None,
) -> None:
    from polaris.delivery.ws.engineering_events import compact_engineering_payload

    fixture = _oversize_fixture(isolated_workspace, 20, "actual failure " + "x" * 5000)
    facts = {"require_qa": required, "qa_verdict": verdict, "project_outcome": outcome, "acceptance_basis": basis}
    fixture["after"].update(facts)
    fixture["after"].update(status="completed" if outcome == "accepted" else "interrupted", verification="passed")
    for check in fixture["after"]["checks"]:
        check.update(ok=True, exit_code=0)
    raw = {
        key: value
        for key, value in fixture["after"].items()
        if key
        not in {
            "events",
            "last_seq",
            "workspace",
            "session_id",
            "run_id",
            "engine_version",
            "resource_url",
            "permission_profile_id",
            "event_delivery_error",
            "native_observation_error",
        }
    }
    fixture["event"]["payload"]["payload"] = compact_engineering_payload("run_updated", raw)
    result = _actual_typescript_fold(isolated_workspace, fixture)
    assert result["liveError"] is None, result
    assert (result["liveRequireQa"], result["liveQa"], result["liveOutcome"], result["liveBasis"]) == (
        required,
        verdict,
        outcome,
        basis,
    )
    assert result["liveChecks"] == result["coldChecks"] == 20
