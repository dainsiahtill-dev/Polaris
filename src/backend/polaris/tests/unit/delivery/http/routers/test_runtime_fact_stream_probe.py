"""Unit coverage for the E2E-only runtime fact-stream and traceability probes.

Fixture contract
----------------
* This module reuses the shared router fixtures in this package's
  ``conftest.py``: ``mock_settings`` is a REAL, JSON-serialisable
  :class:`Settings` instance bound to a project-private workspace, and
  ``client`` builds the real application through ``create_app``.  A local
  ``MagicMock(spec=Settings)`` cannot be used here: ``create_app`` really
  constructs the ``EngineeringRunService`` owner, and that owner serialises
  the settings snapshot into the workspace LLM config.
* The probe route owns the enrollment of its own fixed probe stream through
  the public FactStream maintenance owner.  This module therefore does NOT
  enroll anything for the route; it only establishes the workspace's real
  FactStream authority (the same bootstrap the production lifespan performs)
  through the shared ``fact_stream_authority`` fixture.
* No provider credential, real model call, Bench run or main-instance state is
  involved: the probes are local runtime writes under the private workspace's
  own ``.polaris/runtime`` root.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from httpx import AsyncClient
from polaris.bootstrap.config import Settings
from polaris.delivery.http.workspace import active_workspace_value

PROBE_STREAM = "e2e.fact_stream_probe"


@pytest.mark.asyncio
async def test_fact_stream_probe_is_unavailable_outside_e2e(client: AsyncClient) -> None:
    response = await client.post("/v2/runtime/fact-stream/probe", json={"marker": "blocked"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_fact_stream_probe_appends_and_queries_via_public_service(
    client: AsyncClient,
    mock_settings: Settings,
    fact_stream_authority: Callable[[Path], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real authority for the private workspace, exactly like the production
    # lifespan bootstrap does.  The route itself enrolls the probe stream.
    fact_stream_authority(Path(active_workspace_value(mock_settings)))
    monkeypatch.setenv("KERNELONE_E2E", "1")

    response = await client.post("/v2/runtime/fact-stream/probe", json={"marker": "fact-stream-e2e"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["stream"] == "e2e.fact_stream_probe"
    assert payload["storage_path"] == "runtime/events/e2e.fact_stream_probe.jsonl"
    assert payload["artifact_exists"] is True
    assert payload["queried_total"] >= 1
    assert payload["queried_events"][0]["payload"]["marker"] == "fact-stream-e2e"

    # Append-only sequence projection: must be a positive int sourced from
    # the public append_fact_event result, and must align with the queried
    # event record so dev/acceptance chains can observe append ordering.
    assert isinstance(payload["appended_seq"], int)
    assert payload["appended_seq"] >= 1
    assert isinstance(payload["fact_event_seq"], int)
    assert payload["fact_event_seq"] >= 1
    assert payload["appended_seq"] == payload["fact_event_seq"]
    assert payload["appended_seq"] == int(payload["queried_events"][0]["seq"])

    event_path = Path(str(payload["absolute_path"]))
    assert event_path.is_file()
    assert event_path.name == "e2e.fact_stream_probe.jsonl"


@pytest.mark.asyncio
async def test_fact_stream_probe_repeat_request_advances_the_same_workspace_sequence(
    client: AsyncClient,
    mock_settings: Settings,
    fact_stream_authority: Callable[[Path], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeated probe in one workspace must keep the real append-only order.

    The route enrolls the fixed probe stream on every call.  Enrollment is
    idempotent, so the second request must not reset or fork the stream: the
    appended sequence has to advance and both events stay queryable.

    The route queries with a ``task_id`` filter, so each response only
    reports its own marker.  Proving that BOTH markers exist therefore needs
    the real public query without that filter, not a larger per-marker total.
    """
    from polaris.cells.events.fact_stream.public import (
        QueryFactEventsV1,
        query_fact_events,
    )

    fact_stream_authority(Path(active_workspace_value(mock_settings)))
    monkeypatch.setenv("KERNELONE_E2E", "1")

    first = await client.post("/v2/runtime/fact-stream/probe", json={"marker": "fact-stream-repeat-1"})
    second = await client.post("/v2/runtime/fact-stream/probe", json={"marker": "fact-stream-repeat-2"})

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["stream"] == "e2e.fact_stream_probe"
    assert second_payload["stream"] == "e2e.fact_stream_probe"
    assert int(second_payload["appended_seq"]) == int(first_payload["appended_seq"]) + 1
    assert int(second_payload["fact_event_seq"]) == int(first_payload["fact_event_seq"]) + 1

    # The route filters by its own marker, so each response reports exactly
    # its own event; the stream-wide proof is the unfiltered public query.
    assert int(first_payload["queried_total"]) == 1
    assert int(second_payload["queried_total"]) == 1

    # Both appends land in the one real physical stream file.
    first_path = Path(str(first_payload["absolute_path"]))
    second_path = Path(str(second_payload["absolute_path"]))
    assert first_path == second_path
    assert first_path.is_file()
    assert second_path.is_file()

    # Real public query without a task_id filter: both markers are present in
    # the same physical stream, with consecutive sequence numbers.
    workspace = active_workspace_value(mock_settings)
    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=workspace,
            stream="e2e.fact_stream_probe",
            event_type="probe.appended",
            limit=10,
            offset=0,
        )
    )
    markers = {str(event.get("payload", {}).get("marker")) for event in queried.events}
    assert {"fact-stream-repeat-1", "fact-stream-repeat-2"} <= markers
    seqs = sorted(int(event["seq"]) for event in queried.events)
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))


@pytest.mark.asyncio
async def test_fact_stream_probe_fails_closed_without_workspace_fact_stream_authority(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace without a FactStream authority must not be silently authorized.

    The route may enroll its own probe stream, but enrollment never provisions
    a lock authority.  The negative case therefore needs a genuinely
    unprovisioned environment: the shared session workspace was already
    bootstrapped by earlier tests, so this case points the process at a fresh,
    empty lock-authority root inside its own temporary directory.  Only the
    test process is affected; no instance and no workspace is switched.

    The probe must fail closed with the concrete authorization error code, and
    the physical stream file must be byte-identical before and after the
    rejected request.
    """
    from polaris.kernelone.storage.layout import resolve_runtime_path

    isolated_home = tmp_path / "isolated-kernel-home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_HOME", str(isolated_home))

    workspace = active_workspace_value(mock_settings)
    # Resolve the physical stream file through the platform's own runtime
    # root resolver so the byte comparison targets the real single root.
    event_path = Path(resolve_runtime_path(workspace, "runtime/events/e2e.fact_stream_probe.jsonl"))
    before = event_path.read_bytes() if event_path.is_file() else None

    monkeypatch.setenv("KERNELONE_E2E", "1")
    response = await client.post("/v2/runtime/fact-stream/probe", json={"marker": "fact-stream-no-authority"})

    assert response.status_code == 503
    detail = str(response.json().get("detail", ""))
    assert "lock_authority_missing" in detail

    after = event_path.read_bytes() if event_path.is_file() else None
    assert after == before


@pytest.mark.asyncio
async def test_traceability_probe_is_unavailable_outside_e2e(client: AsyncClient) -> None:
    response = await client.post("/v2/runtime/traceability/probe", json={"marker": "blocked"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_traceability_probe_persists_non_empty_matrix(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_E2E", "1")

    response = await client.post("/v2/runtime/traceability/probe", json={"marker": "traceability-e2e"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["node_count"] >= 3
    assert payload["link_count"] >= 2
    assert payload["artifact_exists"] is True
    assert payload["storage_path"].endswith(".matrix.json")
    assert set(payload["node_kinds"]) >= {"doc", "task", "qa_verdict"}

    matrix_path = Path(str(payload["absolute_path"]))
    assert matrix_path.is_file()


@pytest.mark.asyncio
async def test_traceability_probe_sanitizes_marker_before_persisting_path(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_E2E", "1")

    response = await client.post("/v2/runtime/traceability/probe", json={"marker": "../bad marker/../../matrix"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"].startswith("bad-marker-matrix")
    assert payload["storage_path"].startswith("runtime/traceability/bad-marker-matrix")
    assert ".." not in payload["storage_path"]
    assert "/" not in payload["storage_path"].removeprefix("runtime/traceability/")
