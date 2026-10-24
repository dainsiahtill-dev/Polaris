"""Synthetic bounded file bytes use the actual engineering SQLite journal."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts import engineering_run
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
    tool_observation_payload,
)
from polaris.delivery.ws.engineering_events import compact_engineering_payload


@pytest.fixture
def workspace():
    root = Path(__file__).resolve().parents[7] / ".polaris/test-workspaces/artifact-persistence"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="synthetic-", dir=root) as directory:
        yield Path(directory)


def _store(workspace, session_id="artifact-run"):
    store = EngineeringSessionStore(workspace)
    store.create_run(
        RoleRunCommand(session_id, "qa", "Inspect synthetic geometry", workspace),
        scope=PermissionScope((workspace,), (), frozenset({"execute_command"}), False),
        limits=RunLimits(None, 10000, 2000),
        engine_version="engineering_v2",
        request_key=None,
    )
    return store


def _artifact(data=b"<svg xmlns='http://www.w3.org/2000/svg'/>", **changes):
    assert hasattr(engineering_run, "ArtifactObservation"), "bounded artifact value is missing"
    values = {
        "artifact_ref": "artifact_" + "1" * 32,
        "name": "reports/geometry.svg",
        "media_type": "image/svg+xml",
        "data_base64": base64.b64encode(data).decode("ascii"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "source_digest": "a" * 64,
    }
    return engineering_run.ArtifactObservation(**{**values, **changes})


def _dispatch(store, session_id="artifact-run", call_id="produce-1", turn_seq=1):
    store.record_tool_dispatch(
        session_id,
        turn_seq=turn_seq,
        call_id=call_id,
        tool="execute_command",
        arguments={"command": "synthetic fixture only", "artifacts": ["reports/geometry.svg"]},
        role="qa",
    )


def _observe(store, artifact, session_id="artifact-run", call_id="produce-1", turn_seq=1):
    _dispatch(store, session_id, call_id, turn_seq)
    observed = ToolObservation(
        call_id, "execute_command", True, "Synthetic bytes captured", 0, False, artifacts=(artifact,)
    )
    store.record_tool_observation(session_id, turn_seq=turn_seq, observation=observed, role="qa")
    return observed


def test_actual_bytes_and_original_producer_survive_cold_sqlite_reopen(workspace):
    artifact = _artifact()
    store = _store(workspace)
    observed = _observe(store, artifact)
    cold = EngineeringSessionStore(workspace)
    restored = cold.artifact("artifact-run", artifact.artifact_ref)
    assert restored == artifact
    assert isinstance(restored, engineering_run.ArtifactObservation)
    assert base64.b64decode(restored.data_base64, validate=True) == base64.b64decode(artifact.data_base64)
    producer = cold.artifact_producer("artifact-run", artifact.artifact_ref)
    assert {key: producer[key] for key in ("call_id", "turn_seq", "role", "tool")} == {
        "call_id": "produce-1",
        "turn_seq": 1,
        "role": "qa",
        "tool": "execute_command",
    }
    assert producer["arguments"]["artifacts"] == [artifact.name]
    event = cold.load_events("artifact-run", kinds=("tool_observed",))[0]
    with sqlite3.connect(cold.database_path) as connection:
        raw = json.loads(connection.execute("SELECT observation FROM tool_calls").fetchone()[0])
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert raw == json.loads(json.dumps(tool_observation_payload(observed), ensure_ascii=False))
    assert event["payload"] == {"turn_seq": 1, **raw}
    assert raw["artifacts"][0] == asdict(artifact)
    assert tables == {"runs", "events", "tool_calls"}
    assert cold.pending_tool_calls("artifact-run") == []


def test_old_nonartifact_payload_remains_exact(workspace):
    store = _store(workspace)
    _dispatch(store)
    old = ToolObservation("produce-1", "execute_command", True, "old", 0, False)
    expected = {
        "call_id": "produce-1",
        "tool": "execute_command",
        "ok": True,
        "content": "old",
        "exit_code": 0,
        "interrupted": False,
        "timed_out": False,
    }
    assert tool_observation_payload(old) == expected
    store.record_tool_observation("artifact-run", turn_seq=1, observation=old, role="qa")
    cold = EngineeringSessionStore(workspace)
    assert cold.load_events("artifact-run", kinds=("tool_observed",))[0]["payload"] == {"turn_seq": 1, **expected}
    assert cold.artifact("artifact-run", "artifact_" + "1" * 32) is None


@pytest.mark.parametrize("data", [b"", b"x" * 1_000_000], ids=["empty", "maximum"])
def test_empty_and_maximum_actual_file_bytes_are_valid(data):
    artifact = _artifact(data, source_digest=None)
    assert artifact.source_digest is None
    assert base64.b64decode(artifact.data_base64, validate=True) == data


@pytest.mark.parametrize(
    "changes",
    [
        {"artifact_ref": "forged"},
        {"artifact_ref": "artifact_" + "A" * 32},
        {"name": "/absolute.svg"},
        {"name": "../escape.svg"},
        {"name": "a/../escape.svg"},
        {"name": "a//b.svg"},
        {"name": "a/./b.svg"},
        {"name": "a/"},
        {"name": ""},
        {"name": "a\\b.svg"},
        {"name": "a\0b.svg"},
        {"name": "C:/absolute.svg"},
        {"name": "x" * 4097},
        {"media_type": "application/octet-stream"},
        {"data_base64": "invalid!"},
        {"sha256": "0" * 64},
        {"source_digest": "unknown"},
        {"source_digest": "A" * 64},
    ],
)
def test_invalid_artifact_value_is_rejected(changes):
    with pytest.raises(ValueError):
        _artifact(**changes)


def test_oversize_actual_file_is_rejected():
    with pytest.raises(ValueError):
        _artifact(b"x" * 1_000_001)


def test_foreign_forged_pending_and_unknown_references_never_upgrade(workspace):
    artifact = _artifact()
    store = _store(workspace)
    _observe(store, artifact)
    _store(workspace, "other-run")
    _dispatch(store, call_id="unknown-effect", turn_seq=2)
    before = store.load_events("artifact-run")
    cold = EngineeringSessionStore(workspace)
    assert cold.artifact("other-run", artifact.artifact_ref) is None
    assert cold.artifact("artifact-run", "artifact_" + "2" * 32) is None
    assert cold.artifact("artifact-run", "../sessions.sqlite3") is None
    assert cold.pending_tool_calls("artifact-run")[0]["call_id"] == "unknown-effect"
    assert cold.artifact("artifact-run", artifact.artifact_ref) == artifact
    assert cold.load_events("artifact-run") == before
    assert cold.pending_tool_calls("artifact-run")[0]["status"] == "started"


def test_tampered_durable_bytes_fail_closed(workspace):
    artifact = _artifact()
    store = _store(workspace)
    corrupt = tool_observation_payload(_observe(store, artifact))
    corrupt["artifacts"][0]["data_base64"] = base64.b64encode(b"different").decode("ascii")
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE tool_calls SET observation=?", (json.dumps(corrupt, ensure_ascii=False),))
    with pytest.raises(ValueError):
        EngineeringSessionStore(workspace).artifact("artifact-run", artifact.artifact_ref)


def test_failed_observation_transaction_does_not_publish_artifact(workspace):
    artifact = _artifact()
    store = _store(workspace)
    _dispatch(store)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_observation BEFORE INSERT ON events "
            "WHEN NEW.kind='tool_observed' BEGIN SELECT RAISE(ABORT,'fixture rollback'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="fixture rollback"):
        store.record_tool_observation(
            "artifact-run",
            turn_seq=1,
            observation=ToolObservation(
                "produce-1", "execute_command", True, "captured", 0, False, artifacts=(artifact,)
            ),
            role="qa",
        )
    cold = EngineeringSessionStore(workspace)
    assert cold.artifact("artifact-run", artifact.artifact_ref) is None
    assert cold.pending_tool_calls("artifact-run")[0]["call_id"] == "produce-1"
    assert cold.load_events("artifact-run", kinds=("tool_observed",)) == []


def test_ambiguous_repeated_reference_fails_closed(workspace):
    artifact = _artifact()
    store = _store(workspace)
    _observe(store, artifact)
    _observe(store, artifact, call_id="produce-2", turn_seq=2)
    with pytest.raises(ValueError, match="ambiguous"):
        EngineeringSessionStore(workspace).artifact("artifact-run", artifact.artifact_ref)


def test_public_projection_omits_original_blob_but_keeps_artifact_facts(workspace):
    artifact = _artifact()
    store = _store(workspace)
    _observe(store, artifact)
    raw = store.load_events("artifact-run", kinds=("tool_observed",))[0]["payload"]
    projected = compact_engineering_payload("tool_observed", raw)
    assert "data_base64" not in json.dumps(projected, ensure_ascii=False)
    assert artifact.data_base64 not in json.dumps(projected, ensure_ascii=False)
    assert projected["artifacts"][0] == {key: value for key, value in asdict(artifact).items() if key != "data_base64"}
    assert raw["artifacts"][0]["data_base64"] == artifact.data_base64
