"""Public evidence reads never initialize or probe workspace writability."""

import hashlib
from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    ReadRunLedgerProjectionQueryV1,
    append_run_ledger_event,
    read_run_ledger_projection,
)
from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    QueryFactEventsV1,
    QuerySegmentedFactEventsV1,
    append_segmented_fact_event,
    bootstrap_fact_stream_workspace,
    ensure_segmented_fact_ledger,
    query_fact_events,
    query_segmented_fact_events,
)
from polaris.kernelone.storage import layout


@pytest.fixture(autouse=True)
def project_local_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)


@pytest.mark.parametrize("root_style", ["env", "tilde", "default", "missing", "relative"])
def test_readonly_ramdisk_matches_writer_namespace_without_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_style: str
) -> None:
    ramdisk = tmp_path / "ramdisk"
    ramdisk.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "1")
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    monkeypatch.setenv("POL62_ROOT_PROBE_RAMDISK", str(ramdisk))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(layout, "default_ramdisk_root", lambda: str(ramdisk))
    configured = {
        "env": "$POL62_ROOT_PROBE_RAMDISK",
        "tilde": "~/ramdisk",
        "default": None,
        "missing": str(tmp_path / "missing"),
        "relative": "relative",
    }[root_style]
    layout.clear_storage_roots_cache()
    writer = layout.resolve_workspace_runtime_identity(str(workspace), ramdisk_root=configured)
    layout.clear_storage_roots_cache()

    def no_probe(*args: object, **kwargs: object) -> bool:
        raise AssertionError("read attempted writable probe")

    monkeypatch.setattr(layout, "_is_runtime_base_writable", no_probe)
    reader = layout.resolve_workspace_runtime_identity(str(workspace), ramdisk_root=configured, read_only=True)
    assert reader.runtime_root == writer.runtime_root
    assert reader.token == writer.token
    assert reader.runtime_root.startswith(str(ramdisk)) is (root_style in {"env", "tilde", "default"})


def test_public_read_does_not_probe_or_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="pol62_readonly",
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="parent",
            event={"event_type": "gate_evaluated", "gate": {"ok": True}, "run_id": "parent"},
        )
    )

    def hashes() -> dict[str, str]:
        return {
            str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tmp_path.rglob("*")
            if p.is_file()
        }

    before = hashes()
    layout.clear_storage_roots_cache()

    def no_probe(*args: object, **kwargs: object) -> bool:
        raise AssertionError("read attempted writability probe")

    monkeypatch.setattr(layout, "_is_runtime_base_writable", no_probe)
    result = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="parent"))
    assert result.projection["available"] is True
    assert hashes() == before


def test_missing_read_does_not_initialize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout.clear_storage_roots_cache()
    monkeypatch.setattr(layout, "_is_runtime_base_writable", lambda *_: False)
    with pytest.raises(RuntimeError):
        query_fact_events(
            QueryFactEventsV1(workspace=str(tmp_path), stream="execution.control_plane", strict_integrity=True)
        )
    assert list(tmp_path.iterdir()) == []


def test_historical_read_discovers_existing_authority_without_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.events.sourcing import file_store
    from polaris.kernelone.fs.locked_regular_file import default_platform_lock_root

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane",),
            maintenance_reason="pol62_read_lock_discovery",
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="parent",
            event={"event_type": "gate_evaluated", "gate": {"ok": True}, "run_id": "parent"},
        )
    )
    absent = tmp_path / "absent-authority"
    monkeypatch.setattr(
        file_store, "read_only_platform_lock_roots", lambda: (str(absent), default_platform_lock_root())
    )
    result = query_fact_events(
        QueryFactEventsV1(workspace=str(tmp_path), stream="execution.control_plane", strict_integrity=True)
    )
    assert len(result.events) == 1
    assert not absent.exists()


def test_segmented_public_query_keeps_runtime_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stream = "roles.kernel.provider_attempts.factory.factory-own"
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane",),
            maintenance_reason="pol62_segmented_read",
        )
    )
    ensure_segmented_fact_ledger(
        EnsureSegmentedFactLedgerCommandV1(
            workspace=str(tmp_path), logical_stream=stream, maintenance_reason="pol62_segmented_read"
        )
    )
    append_segmented_fact_event(
        AppendSegmentedFactEventCommandV1(
            workspace=str(tmp_path),
            logical_stream=stream,
            event_type="test.recorded",
            source="test",
            payload={"value": 1},
            idempotency_key="one",
        )
    )
    cursor = next(tmp_path.rglob("cursor.json"))
    cursor.unlink()  # A missing derived index must not be regenerated by a query.
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}
    layout.clear_storage_roots_cache()

    def no_probe(*args: object, **kwargs: object) -> bool:
        raise AssertionError("segmented read attempted writability probe")

    monkeypatch.setattr(layout, "_is_runtime_base_writable", no_probe)
    result = query_segmented_fact_events(QuerySegmentedFactEventsV1(workspace=str(tmp_path), logical_stream=stream))
    assert result.captured_head.total_count == 1
    assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()} == before
