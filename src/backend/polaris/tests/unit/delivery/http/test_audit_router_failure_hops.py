"""Tests for the audit router failure-hops handler.

Focus: the pre-computed ``failure_hops.json`` read path is offloaded off the
request event loop via ``asyncio.to_thread`` while preserving the existing V2
response shape and explicit UTF-8 decoding.

External boundaries (auth, runtime root) are overridden via FastAPI
dependency overrides so the handler runs in isolation.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http import audit_router
from polaris.delivery.http.dependencies import require_auth

VALID_RUN_ID = "dir-00001"


def _seed_failure_hops(runtime_root: Path, run_id: str, payload: dict[str, object]) -> Path:
    """Write a pre-computed failure_hops.json under the runtime artifacts tree."""
    hops_path = runtime_root / "artifacts" / "runs" / run_id / "failure_hops.json"
    hops_path.parent.mkdir(parents=True, exist_ok=True)
    hops_path.write_text(json.dumps(payload), encoding="utf-8")
    return hops_path


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


@pytest.fixture
async def client(runtime_root: Path) -> AsyncIterator[AsyncClient]:
    """Async client over a minimal app mounting only the audit router."""
    app = FastAPI()
    app.include_router(audit_router.router, prefix="/v2")
    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[audit_router.get_runtime_root] = lambda: runtime_root

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Handler: GET /v2/audit/failures/{run_id}/hops (pre-computed file path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_hops_reads_precomputed_file(client: AsyncClient, runtime_root: Path) -> None:
    # Arrange
    _seed_failure_hops(
        runtime_root,
        VALID_RUN_ID,
        {
            "run_id": VALID_RUN_ID,
            "generated_at": "2026-06-20T00:00:00+00:00",
            "ready": True,
            "has_failure": True,
            "failure_code": "QA_FAIL",
            "failure_event_seq": 7,
            "hop1_phase": {"phase": "tool_exec"},
            "hop2_evidence": {"related_action_seq": 1},
            "hop3_tool_output": {"source": "artifact_paths"},
        },
    )

    # Act
    response = await client.get(f"/v2/audit/failures/{VALID_RUN_ID}/hops")

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == 2
    assert data["run_id"] == VALID_RUN_ID
    assert data["ready"] is True
    assert data["has_failure"] is True
    assert data["failure_code"] == "QA_FAIL"
    assert data["failure_event_seq"] == 7
    assert data["hop1_phase"] == {"phase": "tool_exec"}
    assert data["hop3_tool_output"] == {"source": "artifact_paths"}


@pytest.mark.asyncio
async def test_failure_hops_offloads_read_via_to_thread(client: AsyncClient, runtime_root: Path) -> None:
    # Arrange
    _seed_failure_hops(runtime_root, VALID_RUN_ID, {"run_id": VALID_RUN_ID, "ready": True})

    # Act
    with patch.object(audit_router.asyncio, "to_thread", wraps=audit_router.asyncio.to_thread) as spy:
        response = await client.get(f"/v2/audit/failures/{VALID_RUN_ID}/hops")

    # Assert: the blocking read was dispatched to a worker thread.
    assert response.status_code == 200
    spy.assert_awaited_once()
    assert spy.await_args is not None
    assert spy.await_args.args[0] is audit_router._read_failure_hops_json


@pytest.mark.asyncio
async def test_failure_hops_invalid_run_id_returns_400(client: AsyncClient) -> None:
    # Act: "bad$id" is a valid URL segment but fails validate_run_id, so it
    # reaches the handler and is rejected with a 400 (not a routing 404).
    response = await client.get("/v2/audit/failures/bad%24id/hops")

    # Assert
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_failure_hops_corrupt_file_returns_500(client: AsyncClient, runtime_root: Path) -> None:
    # Arrange: invalid JSON triggers a ValueError inside the offloaded read.
    hops_path = runtime_root / "artifacts" / "runs" / VALID_RUN_ID / "failure_hops.json"
    hops_path.parent.mkdir(parents=True, exist_ok=True)
    hops_path.write_text("{not json", encoding="utf-8")

    # Act
    response = await client.get(f"/v2/audit/failures/{VALID_RUN_ID}/hops")

    # Assert
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_failure_hops_missing_file_and_no_events_returns_404(client: AsyncClient, runtime_root: Path) -> None:
    # Arrange: no pre-computed file and no events source available.
    runtime_root.mkdir(parents=True, exist_ok=True)

    # Act
    response = await client.get(f"/v2/audit/failures/{VALID_RUN_ID}/hops")

    # Assert
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Helper: _read_failure_hops_json
# ---------------------------------------------------------------------------


@pytest.fixture
def hops_file(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "failure_hops.json"
    yield path


def test_read_failure_hops_json_parses_utf8_object(hops_file: Path) -> None:
    # Arrange
    hops_file.write_text(json.dumps({"failure_code": "崩溃", "ready": True}), encoding="utf-8")

    # Act
    data = audit_router._read_failure_hops_json(hops_file)

    # Assert
    assert data == {"failure_code": "崩溃", "ready": True}


def test_read_failure_hops_json_rejects_non_object(hops_file: Path) -> None:
    # Arrange
    hops_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    # Act / Assert
    with pytest.raises(ValueError, match="not an object"):
        audit_router._read_failure_hops_json(hops_file)
