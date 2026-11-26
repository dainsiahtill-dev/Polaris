import json
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_context_manifest import query_projection_manifest
from polaris.delivery.http.v2.context_manifest import build_projection_manifest


def test_request_manifest_cannot_see_future_observations_or_create_a_journal(tmp_path: Path) -> None:
    key = "a" * 24
    assert query_projection_manifest(tmp_path, key, {"call_id": "missing"}) is None
    assert not (tmp_path / ".polaris").exists()
    store = EngineeringSessionStore(tmp_path)
    store.create_run(
        RoleRunCommand("run", "director", "inspect files", tmp_path),
        scope=PermissionScope((tmp_path,), (), frozenset({"read_file"}), False),
        limits=RunLimits(None, 10000, 5000),
        engine_version="engineering_v2",
        request_key=None,
    )
    turn = store.append_event("run", kind="turn_started", role="director", payload={})
    call = f"run:director:{turn}"
    store.append_event(
        "run", kind="request_prepared", role="director", payload={"turn_seq": turn, "context_input_seq": turn}
    )
    store.append_event(
        "run",
        kind="llm_request_observed",
        role="director",
        payload={
            "call_id": call,
            "turn_seq": turn,
            "snapshot_status": "available",
            "context_snapshot_ref": key,
        },
    )
    future = store.append_event(
        "run", kind="tool_observed", role="director", payload={"tool": "repo_tree", "content": "future evidence"}
    )
    body = {"messages": [{"role": "user", "content": "actual wire message"}]}
    snapshot = {
        "call_id": call,
        "messages": [{"role": "user", "content": "auxiliary message"}],
        "provider_request": {"final_physical_request": {"body": body}},
    }
    result = query_projection_manifest(tmp_path, key, snapshot)
    assert result is not None and result["context_snapshot_ref"] == key
    assert result["journal_input_seq"] == turn
    assert all(node["seq"] != future for node in result["nodes"])
    assert result["nodes"][-1]["summary"] == "actual wire message"


def test_manifest_exposes_context_health_metrics() -> None:
    run = {
        "session_id": "run",
        "workspace": "/ws",
        "workspace_revision": 3,
        "verification": "not_run",
        "instruction": "goal",
    }
    events = [
        {
            "seq": 1,
            "kind": "tool_observed",
            "role": "director",
            "payload": {"tool": "read_command_result", "ok": True, "content": "x"},
        }
    ]
    manifest = build_projection_manifest(run, events, {"messages": []})
    health = manifest["summary"]["context_health"]
    assert health["evidence_recoveries"] == 1
    assert health["memory_card_count"] >= 0
    assert health["fold_rate"] is not None


def test_manifest_tracks_fold_provenance_and_run_card_hierarchy() -> None:
    run = {"session_id": "run", "workspace": "/ws", "workspace_revision": 7, "verification": "passed"}
    events = [
        {"seq": 1, "kind": "turn_started", "role": "qa", "payload": {}},
        {
            "seq": 2,
            "kind": "request_prepared",
            "role": "qa",
            "payload": {"request": {"context": {"phase": "qa_probe"}}},
        },
        {
            "seq": 3,
            "kind": "tool_started",
            "role": "qa",
            "payload": {"call_id": "current-call", "tool": "execute_command", "turn_seq": 1},
        },
        {
            "seq": 4,
            "kind": "tool_observed",
            "role": "qa",
            "payload": {"call_id": "old-call", "tool": "execute_command", "ok": True, "content": "done"},
        },
        {
            "seq": 5,
            "kind": "run_updated",
            "role": "qa",
            "payload": {"workspace_revision": 7, "verification": "passed"},
        },
        {
            "seq": 6,
            "kind": "llm_request_observed",
            "role": "qa",
            "payload": {"call_id": "run:qa:1", "context_snapshot_ref": "abc123"},
        },
    ]
    snapshot = {
        "call_id": "run:qa:1",
        "messages": [
            {"role": "tool", "tool_call_id": "current-call", "content": "ok"},
            {"role": "user", "content": "state"},
        ],
    }
    manifest = build_projection_manifest(run, events, snapshot)
    nodes = {node["id"]: node for node in manifest["nodes"]}
    assert nodes["event-3"]["fold_state"] == "ACTIVE" and nodes["event-3"]["request_message_index"] == 0
    assert nodes["event-4"]["fold_state"] == "REFERENCE"
    assert nodes["event-5"]["fold_state"] == "FOLDED"
    assert nodes["event-5"]["projection_ref"] == "working-state:7"
    assert nodes["event-3"]["parent_id"] == "turn-1"
    assert manifest["run_cards"][0]["phase"] == "qa_probe"
    assert manifest["summary"]["fold_counts"]["ACTIVE"] == 3
    assert manifest["summary"]["compression_ratio"] is not None


def test_manifest_evicts_superseded_run_card() -> None:
    run = {"session_id": "run", "workspace": "/ws", "workspace_revision": 9, "verification": "passed"}
    events = [{"seq": 8, "kind": "run_updated", "role": "qa", "payload": {"workspace_revision": 7}}]
    manifest = build_projection_manifest(run, events, {"messages": []})
    assert manifest["nodes"][0]["fold_state"] == "EVICTED"


def test_manifest_deduplicates_same_path_cards() -> None:
    run = {
        "session_id": "run",
        "workspace": "/ws",
        "workspace_revision": 2,
        "verification": "not_run",
        "instruction": "build",
    }
    old_card = {
        "schema_version": "engineering.file_card.v1",
        "path": "a.py",
        "content_ref": "filecard:old",
        "sha256": "old",
        "size_bytes": 100,
        "content_preview": "old",
    }
    new_card = {
        "schema_version": "engineering.file_card.v1",
        "path": "a.py",
        "content_ref": "filecard:new",
        "sha256": "new",
        "size_bytes": 100,
        "content_preview": "new",
    }
    events = [
        {
            "seq": 1,
            "kind": "tool_observed",
            "role": "director",
            "payload": {"tool": "read_file", "content": json.dumps({"file_card": old_card})},
        },
        {
            "seq": 2,
            "kind": "tool_observed",
            "role": "director",
            "payload": {"tool": "read_file", "content": json.dumps({"file_card": new_card})},
        },
    ]
    manifest = build_projection_manifest(run, events, {"messages": []})
    old, new = manifest["nodes"]
    assert old["fold_state"] == "FOLDED" and old["projection_ref"] == "filecard:new"
    assert old["fold_reason_code"] == "DUPLICATE_PAYLOAD"
    assert new["fold_state"] == "REFERENCE" and new["projection_ref"] == "filecard:new"


def test_manifest_pins_current_edit_file_card() -> None:
    run = {
        "session_id": "run",
        "workspace": "/ws",
        "workspace_revision": 2,
        "verification": "failed",
        "instruction": "fix a.py",
    }
    card = {
        "schema_version": "engineering.file_card.v1",
        "path": "a.py",
        "content_ref": "filecard:a",
        "sha256": "a",
        "size_bytes": 100,
        "content_preview": "source",
    }
    events = [
        {
            "seq": 1,
            "kind": "tool_observed",
            "role": "director",
            "payload": {"tool": "read_file", "content": json.dumps({"file_card": card})},
        },
        {
            "seq": 2,
            "kind": "tool_started",
            "role": "director",
            "payload": {"tool": "edit_file", "arguments": {"file": "a.py"}},
        },
    ]
    manifest = build_projection_manifest(run, events, {"messages": []})
    node = manifest["nodes"][0]
    assert node["fold_state"] == "FOLDED" and node["pin_reason"] == "current_edit"
