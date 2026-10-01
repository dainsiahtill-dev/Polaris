"""Missing enrollment is not proof of missing physical stream data."""
from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.kernelone.storage import resolve_runtime_path


@pytest.mark.parametrize("existing", (False, True))
def test_unenrolled_query_failure_reports_physical_leaf_presence_without_enrolling(
    tmp_path: Path, existing: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bootstrap_fact_stream_workspace(BootstrapFactStreamWorkspaceCommandV1(
        workspace=str(workspace), maintenance_reason="storage-observation-fixture",
        streams=fact_stream_bootstrap_streams(),
    ))
    logical_path = "runtime/events/unenrolled_fixture.jsonl"
    physical = Path(resolve_runtime_path(str(workspace), logical_path))
    physical.parent.mkdir(parents=True, exist_ok=True)
    if existing:
        physical.write_text("", encoding="utf-8")
    with pytest.raises(FactStreamError) as caught:
        query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="unenrolled_fixture", strict_integrity=True))

    evidence = caught.value.details
    assert evidence["stream_leaf_presence"] == ("present" if existing else "missing")
    assert evidence["storage_path"] == logical_path
    assert evidence["workspace"] == str(workspace)
    # Observation must not turn an unenrolled stream into a readable one.
    with pytest.raises(FactStreamError) as repeated:
        query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="unenrolled_fixture", strict_integrity=True))
    assert repeated.value.code == caught.value.code
