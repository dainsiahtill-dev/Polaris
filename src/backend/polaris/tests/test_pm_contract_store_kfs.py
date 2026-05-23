from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.runtime.state_owner.internal.pm_contract_store import (
    ensure_engine_dispatch_contracts,
    read_json_safe,
    write_json_atomic,
)
from polaris.kernelone.storage import resolve_logical_path
from polaris.kernelone.storage.io_paths import resolve_artifact_path


def test_pm_contract_store_roundtrip_under_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    payload = {"tasks": [{"id": "t-1", "title": "migrate-kfs"}], "version": 1}
    logical_path = "runtime/contracts/pm_tasks.contract.json"

    write_json_atomic(logical_path, payload)

    absolute_path = Path(resolve_logical_path(str(tmp_path), logical_path))
    assert absolute_path.is_file()
    assert read_json_safe(logical_path) == payload


def test_pm_contract_store_rejects_non_kfs_path(tmp_path: Path) -> None:
    outside_path = tmp_path / "pm_state.json"

    with pytest.raises(ValueError, match="KernelFileSystem managed roots"):
        write_json_atomic(str(outside_path), {"status": "invalid-path"})

    assert read_json_safe(str(outside_path)) is None


def test_engine_dispatch_contracts_backfill_missing_runtime_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = workspace / ".polaris" / "runtime"
    workspace.mkdir()
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")

    normalized = {
        "overall_goal": "Stabilize PM to Director dispatch",
        "tasks": [
            {
                "id": "PM-DISPATCH-1",
                "title": "Run Director after PM planning",
                "goal": "Create dispatch prerequisites from PM output.",
                "target_files": ["src/backend/polaris/cells/runtime/state_owner/internal/pm_contract_store.py"],
                "execution_checklist": ["Persist runtime contracts", "Validate runtime plan"],
                "acceptance": ["Director dispatch can start"],
            }
        ],
    }
    runtime_pm_tasks = resolve_artifact_path(str(workspace), "", "runtime/contracts/pm_tasks.contract.json")
    run_pm_tasks = resolve_artifact_path(str(workspace), "", "runtime/runs/pm-00001/contracts/pm_tasks.contract.json")
    runtime_plan = resolve_artifact_path(str(workspace), "", "runtime/contracts/plan.md")

    ensure_engine_dispatch_contracts(
        normalized=normalized,
        run_pm_tasks=run_pm_tasks,
        runtime_pm_tasks_full=runtime_pm_tasks,
        runtime_plan_full=runtime_plan,
    )

    plan_text = Path(runtime_plan).read_text(encoding="utf-8")
    assert "Generated from the PM task contract" in plan_text
    assert "Run Director after PM planning" in plan_text
    assert read_json_safe(runtime_pm_tasks) == normalized
    assert read_json_safe(run_pm_tasks) == normalized
