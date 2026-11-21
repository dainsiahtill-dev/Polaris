from polaris.delivery.http.v2.context_manifest import build_projection_manifest


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
