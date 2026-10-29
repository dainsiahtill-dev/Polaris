"""The scout anchor store must distinguish a known no-write from an unknown effect.

The store persists probe anchors into the logical ``runtime`` namespace.  A
persistence that was refused or failed before it could commit leaves the
previously stored anchors in effect: that is a *known* no-write.  A
persistence that may already have committed is *unknown*, and must not be
reported as a confirmed state.

The store's public contract is fail-soft: it never raises and it never
reintroduces a raw-open fallback after a KFS refusal.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.context import scout_anchor_store
from polaris.kernelone.fs.guarded_namespace_ops import NamespaceOperationEffectUnknownError
from polaris.kernelone.fs.types import NamespaceBindingV1, NamespaceCreateReceiptV1
from polaris.kernelone.storage import clear_storage_roots_cache


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated workspace with its own canonical runtime root."""
    workspace = (tmp_path / "workspace").resolve()
    (workspace / ".polaris" / "runtime").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(workspace / ".polaris" / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    clear_storage_roots_cache()
    return workspace


def _finding(path: str, confidence: float) -> dict[str, Any]:
    return {"path": path, "line": 10, "symbol": "f", "confidence": confidence, "snippet": "..."}


class _RefusingAdapter(LocalFileSystemAdapter):
    """Adapter whose policy refuses every managed read and write.

    The subclass overrides the legacy reader and writer, so it must opt into
    the corresponding managed capabilities explicitly; the opt-in keeps the
    refusal in force instead of silently falling back to the base
    implementation.
    """

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
        raise PermissionError("synthetic adapter write policy denial")

    def write_bytes(self, path: str, content: bytes, *, atomic: bool = False) -> int:
        raise PermissionError("synthetic adapter write policy denial")

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        raise PermissionError("synthetic adapter read policy denial")

    def read_bytes(self, path: str) -> bytes:
        raise PermissionError("synthetic adapter read policy denial")

    def read_managed_namespace_bytes(self, binding: NamespaceBindingV1) -> bytes:
        raise PermissionError("synthetic adapter read policy denial")

    def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes) -> int:
        raise PermissionError("synthetic adapter write policy denial")


class _UnknownEffectAdapter(LocalFileSystemAdapter):
    """Adapter that may already have committed when it fails."""

    def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes) -> int:
        LocalFileSystemAdapter.write_managed_namespace_bytes_atomic(self, binding, content)
        raise NamespaceOperationEffectUnknownError(binding, "committed_identity")


def test_a_known_no_write_reports_the_anchors_in_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refused persistence leaves the stored anchors unchanged and is a known state."""
    workspace = _workspace(tmp_path, monkeypatch)

    class WriteDenied(LocalFileSystemAdapter):
        def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes) -> int:
            raise PermissionError("synthetic known pre-effect write refusal")

    monkeypatch.setattr(scout_anchor_store, "get_default_adapter", WriteDenied)

    with caplog.at_level(logging.DEBUG, logger=scout_anchor_store.logger.name):
        count = scout_anchor_store.record_scout_anchors(str(workspace), "q", [_finding("a.py", 0.9)])

    assert count == 0, "read confirmed an absent store; the refused write created nothing"
    assert scout_anchor_store.load_scout_anchors(str(workspace)) == []
    records = [r for r in caplog.records if "persistence failed" in r.getMessage()]
    assert records, "a known no-write is logged as a known failure"
    assert records[0].levelno == logging.DEBUG


def test_an_uncertain_effect_is_not_reported_as_a_known_no_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An uncertain persistence is logged as uncertain, not as a confirmed zero."""
    workspace = _workspace(tmp_path, monkeypatch)
    adapter = _UnknownEffectAdapter()
    monkeypatch.setattr(scout_anchor_store, "get_default_adapter", lambda: adapter)

    with caplog.at_level(logging.DEBUG, logger=scout_anchor_store.logger.name):
        count = scout_anchor_store.record_scout_anchors(str(workspace), "q", [_finding("a.py", 0.9)])

    # A warning alone cannot turn the old numeric zero into UNKNOWN. The
    # adapter committed real bytes before failing, so no stored count is known.
    assert count is None
    assert [a["path"] for a in scout_anchor_store.load_scout_anchors(str(workspace))] == ["a.py"]
    uncertain = [r for r in caplog.records if "effect is unknown" in r.getMessage()]
    assert uncertain, "an uncertain effect is logged separately from a known no-write"
    assert uncertain[0].levelno == logging.WARNING
    known = [r for r in caplog.records if "persistence failed" in r.getMessage()]
    assert not known, "an uncertain effect must not also be reported as a known no-write"


def test_the_store_never_falls_back_to_a_raw_open_after_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A refused read must not be retried through a raw file open."""
    workspace = _workspace(tmp_path, monkeypatch)
    anchor_file = workspace / ".polaris" / "runtime" / "scout_anchors.json"
    anchor_file.parent.mkdir(parents=True, exist_ok=True)
    anchor_file.write_text(json.dumps({"anchors": [{"path": "a.py", "confidence": 0.9}]}), encoding="utf-8")
    adapter = _RefusingAdapter()
    monkeypatch.setattr(scout_anchor_store, "get_default_adapter", lambda: adapter)

    def _forbidden_open(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the store must not open the anchor file directly")

    monkeypatch.setattr("builtins.open", _forbidden_open)

    assert scout_anchor_store.load_scout_anchors(str(workspace)) == []


def test_a_legitimate_store_roundtrip_still_advances(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The ordinary path still stores and reads anchors."""
    workspace = _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(scout_anchor_store, "get_default_adapter", LocalFileSystemAdapter)

    count = scout_anchor_store.record_scout_anchors(str(workspace), "q", [_finding("a.py", 0.9)])

    assert count == 1
    anchors = scout_anchor_store.load_scout_anchors(str(workspace))
    assert [a["path"] for a in anchors] == ["a.py"]


def test_the_create_capability_receipt_is_a_frozen_value() -> None:
    """The create receipt is immutable evidence."""
    receipt = NamespaceCreateReceiptV1(absolute_path="/tmp/a.txt", relative_path="a.txt", created=True, bytes_written=3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        receipt.created = False  # type: ignore[misc]


def test_an_unreadable_existing_store_is_not_overwritten_with_partial_knowledge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refused initial read gives no authority to claim zero or replace history."""
    workspace = _workspace(tmp_path, monkeypatch)
    path = workspace / ".polaris" / "runtime" / "scout_anchors.json"
    original = json.dumps({"anchors": [{"path": "existing.py", "confidence": 0.8}]})
    path.write_text(original, encoding="utf-8")

    class ReadDenied(LocalFileSystemAdapter):
        def read_managed_namespace_bytes(self, binding: NamespaceBindingV1) -> bytes:
            raise PermissionError("synthetic read denial")

    monkeypatch.setattr(scout_anchor_store, "get_default_adapter", ReadDenied)
    count = scout_anchor_store.record_scout_anchors(str(workspace), "q", [_finding("new.py", 0.9)])

    assert count is None
    assert path.read_text(encoding="utf-8") == original
