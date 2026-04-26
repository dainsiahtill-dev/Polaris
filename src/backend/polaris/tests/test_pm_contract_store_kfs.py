from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.runtime.state_owner.internal.pm_contract_store import (
    read_json_safe,
    write_json_atomic,
)
from polaris.kernelone.storage import resolve_logical_path


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
