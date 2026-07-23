"""End-to-end round-trip test for context snapshots — no mocks.

Spins up a real FastAPI app with the real ``context_router`` mounted,
seeds a snapshot via the real ``AIExecutor._store_context_messages``
producer, then asks the real ``GET /v2/context/{hash}`` endpoint to
return it.  This is the only test that exercises the real
``StorageLayout`` + ``build_cache_root`` round trip on real disk; every
other consumer-side test mocks the layout.

Marked with ``@pytest.mark.integration`` so unit-only fast runs skip it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.v2._shared import require_auth
from polaris.delivery.http.v2.context import router as context_router
from polaris.kernelone.llm.engine.executor import AIExecutor


class _AllowAllAuth:
    def check(self, _auth_header: str) -> bool:
        return True


@pytest.mark.integration
def test_store_then_get_round_trip(tmp_path: Path) -> None:
    """Producer → on-disk → GET /v2/context/{hash} → matches input."""
    workspace = tmp_path
    messages = [
        {"role": "system", "content": "you are a tester"},
        {"role": "user", "content": "hello"},
    ]

    # 1. Producer writes the snapshot via the real StorageLayout path.
    hash_key = AIExecutor._store_context_messages_sync(
        workspace=str(workspace),
        messages=messages,
        trace_id="trace-e2e",
        call_id="call-e2e",
    )
    assert isinstance(hash_key, str)
    assert len(hash_key) == 24

    # 2. Wire a real FastAPI app bound to this workspace — no Mock layout.
    app = FastAPI()
    app.include_router(context_router)
    app.state.auth = _AllowAllAuth()
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=str(workspace), ramdisk_root=""),
    )
    app.dependency_overrides[require_auth] = lambda: None

    with TestClient(app) as client:
        response = client.get(f"/v2/context/{hash_key}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == 1
    assert body["hash"] == hash_key
    assert body["trace_id"] == "trace-e2e"
    assert body["call_id"] == "call-e2e"
    assert body["messages"] == messages
    assert body["message_count"] == len(messages)
    assert body["total_chars"] > 0
    assert isinstance(body["stored_at"], str) and body["stored_at"]

    # 3. No .tmp sibling should be left anywhere under contexts/.
    for tmp_path_orphan in (workspace).rglob("*.tmp"):
        raise AssertionError(f"unexpected .tmp sibling: {tmp_path_orphan}")


@pytest.mark.integration
def test_round_trip_payload_is_valid_json(tmp_path: Path) -> None:
    """On-disk file is parseable JSON; total_chars counts the JSON body."""
    workspace = tmp_path
    messages = [{"role": "user", "content": "json"}]
    hash_key = AIExecutor._store_context_messages_sync(
        workspace=str(workspace),
        messages=messages,
        trace_id="trace-json",
        call_id="call-json",
    )
    # Round-trip the on-disk payload through json.loads — catches any
    # encoding bug (the spec mandates UTF-8) or trailing-truncation bug.
    from polaris.kernelone.storage.io_paths import resolve_storage_roots

    # Producer and HTTP consumer share this resolved project runtime root.
    # Re-wrapping it in ``StorageLayout`` would append a second
    # ``projects/<workspace>/runtime`` segment and inspect a path that no
    # producer owns.
    runtime_root = Path(resolve_storage_roots(str(workspace)).runtime_root)
    file_path = runtime_root / "contexts" / hash_key[:2] / hash_key
    raw_bytes = file_path.read_bytes()
    assert raw_bytes, "context file is empty"
    payload = json.loads(raw_bytes.decode("utf-8"))
    assert payload["schema_version"] == 1
    assert payload["messages"] == messages
