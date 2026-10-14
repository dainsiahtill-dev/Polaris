"""Readonly resource closure is distinct from provider correlation qualification."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.tests.test_committed_turn_receipts import _append, _fixture, _hash, _query


def test_absent_provider_hash_does_not_pin_proved_readonly_resources(tmp_path: Path) -> None:
    payload, lifecycle = _fixture(tmp_path)
    payload["outcome"]["decision"]["metadata"].pop("provider_response_hash")
    lifecycle["provider_response_hash"] = ""
    payload["outcome_hash"] = _hash(payload["outcome"])
    _append(tmp_path, payload)
    runtime = tmp_path / ".polaris" / "runtime"
    before = {str(p.relative_to(runtime)): p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    observed = _query(tmp_path, lifecycle)[0]
    assert observed.status == "observed"
    assert observed.provider_correlation_qualified is False
    assert observed.provider_response_hash == ""
    assert observed.reason == "immutable_readonly_owner_receipt_observed_provider_binding_unavailable"
    assert before == {str(p.relative_to(runtime)): p.read_bytes() for p in runtime.rglob("*") if p.is_file()}


@pytest.mark.parametrize("defect", ["different", "malformed", "one_sided"])
def test_present_conflicting_provider_binding_remains_unknown(tmp_path: Path, defect: str) -> None:
    payload, lifecycle = _fixture(tmp_path)
    if defect == "different":
        lifecycle["provider_response_hash"] = "b" * 64
    elif defect == "malformed":
        lifecycle["provider_response_hash"] = "invalid"
        payload["outcome"]["decision"]["metadata"]["provider_response_hash"] = "invalid"
    else:
        payload["outcome"]["decision"]["metadata"].pop("provider_response_hash")
    payload["outcome_hash"] = _hash(payload["outcome"])
    _append(tmp_path, payload)
    observed = _query(tmp_path, lifecycle)[0]
    assert observed.status == "unknown"
    assert observed.reason == "owner_provider_response_mismatch"


def test_complete_provider_binding_remains_qualified(tmp_path: Path) -> None:
    payload, lifecycle = _fixture(tmp_path)
    _append(tmp_path, payload)
    observed = _query(tmp_path, lifecycle)[0]
    assert observed.status == "observed"
    assert observed.provider_correlation_qualified is True
    assert observed.provider_response_hash == "a" * 64
