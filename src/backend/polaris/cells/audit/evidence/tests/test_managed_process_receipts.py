"""Focused GR3B-B2 proof for audit.evidence-owned process receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.audit.evidence.public.contracts import (
    MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1,
    EvidenceAuditError,
    PersistManagedProcessReceiptCommandV1,
    ReadManagedProcessReceiptQueryV1,
)
from polaris.cells.audit.evidence.public.service import (
    persist_managed_process_receipt,
    read_managed_process_receipt,
)


class _FakeKernelFs:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.appends: list[tuple[str, dict[str, Any]]] = []

    def append_jsonl(self, logical_path: str, payload: dict[str, Any]) -> SimpleNamespace:
        self.appends.append((logical_path, payload))
        target = self.root / logical_path
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return SimpleNamespace(logical_path=logical_path)

    def read_text(self, logical_path: str, *, encoding: str = "utf-8") -> str:
        return (self.root / logical_path).read_text(encoding=encoding)


def _canonical_hash(receipt: dict[str, Any]) -> str:
    canonical = json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_same_canonical_content_is_idempotent_and_read_is_immutable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fs = _FakeKernelFs(workspace)
    first_body = {"z": [1, 2], "a": {"summary": "完成"}}
    second_body = {"a": {"summary": "完成"}, "z": [1, 2]}

    first = persist_managed_process_receipt(
        PersistManagedProcessReceiptCommandV1(workspace=str(workspace), receipt=first_body),
        kernel_fs=fs,
    )
    second = persist_managed_process_receipt(
        PersistManagedProcessReceiptCommandV1(
            workspace=str(workspace),
            receipt=second_body,
            claimed_receipt_hash=first.receipt_hash,
        ),
        kernel_fs=fs,
    )
    record = read_managed_process_receipt(
        ReadManagedProcessReceiptQueryV1(workspace=str(workspace), receipt_hash=first.receipt_hash),
        kernel_fs=fs,
    )

    assert first.receipt_hash == _canonical_hash(first_body)
    assert first.already_present is False
    assert second.receipt_ref == first.receipt_ref
    assert second.receipt_hash == first.receipt_hash
    assert second.already_present is True
    assert len(fs.appends) == 1
    assert record is not None
    assert record.receipt_hash == first.receipt_hash
    assert record.receipt["z"] == (1, 2)
    with pytest.raises(TypeError):
        record.receipt["new"] = "forbidden"  # type: ignore[index]
    nested = record.receipt["a"]
    assert isinstance(nested, dict) is False
    with pytest.raises(TypeError):
        nested["summary"] = "tampered"  # type: ignore[index]


def test_distinct_content_has_distinct_hash_and_record(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fs = _FakeKernelFs(workspace)

    first = persist_managed_process_receipt(
        PersistManagedProcessReceiptCommandV1(workspace=str(workspace), receipt={"exit_code": 0}),
        kernel_fs=fs,
    )
    second = persist_managed_process_receipt(
        PersistManagedProcessReceiptCommandV1(workspace=str(workspace), receipt={"exit_code": 1}),
        kernel_fs=fs,
    )

    assert first.receipt_hash != second.receipt_hash
    assert first.receipt_ref != second.receipt_ref
    assert len(fs.appends) == 2


def test_forged_hash_claim_cannot_override_owner_calculation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fs = _FakeKernelFs(workspace)

    with pytest.raises(EvidenceAuditError, match="claimed receipt hash"):
        persist_managed_process_receipt(
            PersistManagedProcessReceiptCommandV1(
                workspace=str(workspace),
                receipt={"exit_code": 0},
                claimed_receipt_hash="0" * 64,
            ),
            kernel_fs=fs,
        )

    assert fs.appends == []


def test_read_is_isolated_by_workspace(tmp_path: Path) -> None:
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    fs_a = _FakeKernelFs(workspace_a)
    fs_b = _FakeKernelFs(workspace_b)
    persisted = persist_managed_process_receipt(
        PersistManagedProcessReceiptCommandV1(workspace=str(workspace_a), receipt={"pid": 123}),
        kernel_fs=fs_a,
    )

    result = read_managed_process_receipt(
        ReadManagedProcessReceiptQueryV1(
            workspace=str(workspace_b),
            receipt_hash=persisted.receipt_hash,
        ),
        kernel_fs=fs_b,
    )

    assert result is None


def test_malformed_stored_line_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1
    target.parent.mkdir(parents=True)
    target.write_text('{"schema_version":', encoding="utf-8")

    with pytest.raises(EvidenceAuditError, match="invalid managed-process receipt"):
        read_managed_process_receipt(
            ReadManagedProcessReceiptQueryV1(workspace=str(workspace), receipt_hash="a" * 64),
            kernel_fs=_FakeKernelFs(workspace),
        )


def test_tampered_stored_ref_or_hash_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fs = _FakeKernelFs(workspace)
    result = persist_managed_process_receipt(
        PersistManagedProcessReceiptCommandV1(workspace=str(workspace), receipt={"exit_code": 0}),
        kernel_fs=fs,
    )
    target = workspace / MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["receipt_ref"] = f"{MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1}#{'f' * 64}"
    target.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(EvidenceAuditError, match="invalid managed-process receipt"):
        read_managed_process_receipt(
            ReadManagedProcessReceiptQueryV1(workspace=str(workspace), receipt_hash=result.receipt_hash),
            kernel_fs=fs,
        )
