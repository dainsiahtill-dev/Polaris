from __future__ import annotations

from pathlib import Path

from polaris.cells.control_plane.run_ledger.public import (
    RunLedger,
    service as run_ledger_service,
)


def test_run_ledger_writes_only_to_project_local_runtime(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    ledger = RunLedger(workspace, run_id="factory_runtime_root")

    receipt = ledger.append_event(
        {
            "event_type": "gate_evaluated",
            "gate": {"name": "runtime_root", "ok": True},
        }
    )

    canonical = workspace / ".polaris" / "runtime" / "control_plane" / "ledger"
    assert Path(receipt["ledger_path"]) == canonical / "factory_runtime_root.ndjson"
    assert canonical.is_dir()
    assert not (workspace / "runtime").exists()


def test_run_ledger_reads_legacy_runtime_without_writing_it(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    run_id = "factory_legacy_runtime"
    ledger = RunLedger(workspace, run_id=run_id)
    receipt = ledger.append_event(
        {
            "event_type": "gate_evaluated",
            "gate": {"name": "legacy_read", "ok": True},
        }
    )
    canonical_path = Path(receipt["ledger_path"])
    legacy_path = workspace / "runtime" / "control_plane" / "ledger" / canonical_path.name
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.replace(legacy_path)
    canonical_path.parent.rmdir()

    paths = run_ledger_service._ledger_paths(
        workspace,
        run_id=run_id,
        max_runs=1,
    )

    assert paths == [legacy_path]
    assert not canonical_path.exists()
