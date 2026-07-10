"""Regression tests for authoritative task file-ownership persistence."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.quality.file_ownership_ledger as ledger_module
import pytest
from polaris.kernelone.quality.file_ownership_ledger import (
    FileOwnershipLedgerError,
    record_task_file_owners,
)


def test_record_task_file_owners_is_idempotent_and_preserves_first_owner(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    target_files = ["src/ownership.py", "./tests/test_ownership.py"]

    first = record_task_file_owners(workspace, "", target_files, task_id="TASK-FIRST")
    repeated = record_task_file_owners(workspace, "", target_files, task_id="TASK-FIRST")
    later = record_task_file_owners(workspace, "", target_files, task_id="TASK-LATER")

    expected = {
        "src/ownership.py": {
            "owner_step_id": "TASK-FIRST",
            "owner_parent": "TASK-FIRST",
        },
        "tests/test_ownership.py": {
            "owner_step_id": "TASK-FIRST",
            "owner_parent": "TASK-FIRST",
        },
    }
    assert first == expected
    assert repeated == expected
    assert later == expected


@pytest.mark.parametrize(
    "target_file",
    ["../outside.py", "/tmp/outside.py", "src/../outside.py", "src//duplicate.py", "src/invalid\x00.py"],
)
def test_record_task_file_owners_rejects_unsafe_authoritative_targets(
    tmp_path: Path,
    target_file: str,
) -> None:
    with pytest.raises(ValueError, match="authoritative target file"):
        record_task_file_owners(str(tmp_path), "", [target_file], task_id="TASK-INVALID")


def test_record_task_file_owners_fails_closed_when_ledger_write_fails(tmp_path: Path, monkeypatch) -> None:
    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(ledger_module, "write_json_atomic", fail_write)

    with pytest.raises(FileOwnershipLedgerError, match="ledger write failed"):
        record_task_file_owners(str(tmp_path), "", ["src/ownership.py"], task_id="TASK-WRITE-FAIL")


def test_record_task_file_owners_fails_closed_when_readback_is_inconsistent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def empty_ledger(_workspace: str, _cache_root: str, *, fail_closed: bool = False) -> dict[str, object]:
        return {"schema_version": "file-ownership-ledger/1", "files": {}}

    def discard_write(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(ledger_module, "_load", empty_ledger)
    monkeypatch.setattr(ledger_module, "write_json_atomic", discard_write)

    with pytest.raises(FileOwnershipLedgerError, match="missing or unverifiable"):
        record_task_file_owners(str(tmp_path), "", ["src/ownership.py"], task_id="TASK-READBACK-FAIL")
