"""Proof-derived counts; private real owners, explicit product outcome controls."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.public import service
from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
from polaris.cells.control_plane.run_ledger.tests.test_failed_product_resource_settlement import (
    _actual_layout_resource_control,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("product_ok", [True, False])
async def test_proven_readonly_count_preserves_outcome_and_raw_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, product_ok: bool
) -> None:
    factory, data = await _actual_layout_resource_control(tmp_path, product_ok=product_ok)
    original_project = service.project_factory_settlement_barrier
    raw_counts: list[int] = []

    def observe_projection(**kwargs):
        lifecycle = kwargs["run_projection"]["tool_lifecycle"]
        before = stable_hash(lifecycle)
        raw_counts.append(lifecycle["dispatched_tool_calls_count"])
        result = original_project(**kwargs)
        assert stable_hash(lifecycle) == before
        return result

    monkeypatch.setattr(service, "project_factory_settlement_barrier", observe_projection)
    result = service.query_factory_settlement_barrier(tmp_path, factory)
    assert len(data["calls"]) == 3 and raw_counts == [3]
    assert result.closed is True and result.release_allowed is True
    assert result.passed is product_ok
    assert result.expected_effect_count == 0, result
    assert result.effect_receipt_count == 0 and result.open_effect_count == 0
    assert "effect_receipt_missing" not in result.blocking_reasons
    assert "effect_receipts_open" not in result.blocking_reasons
    if not product_ok:
        assert "qa" in result.missing_required_modalities
        assert "required_evidence_missing" in result.blocking_reasons
        assert "run_ledger_gate_failed" in result.blocking_reasons
    again = service.query_factory_settlement_barrier(tmp_path, factory)
    assert again.barrier_hash == result.barrier_hash
    assert raw_counts == [3, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["committed_read_missing", "task_owner_missing", "foreign_workspace", "batch_hash"])
async def test_unproven_readonly_owner_cannot_reduce_effect_debt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    factory, _ = await _actual_layout_resource_control(tmp_path, product_ok=True)
    if defect == "task_owner_missing":
        monkeypatch.setattr(service, "_terminal_execution_effect_observations", lambda *args, **kwargs: ())
    else:
        query = service.query_committed_turn_receipts

        def invalid_observation(*args, **kwargs):
            rows = query(*args, **kwargs)
            if defect == "committed_read_missing":
                return ()
            if defect == "foreign_workspace":
                return tuple(replace(row, workspace=str(tmp_path / "foreign")) for row in rows)
            return tuple(replace(row, batch_receipt_hash="0" * 64) for row in rows)

        monkeypatch.setattr(service, "query_committed_turn_receipts", invalid_observation)
    result = service.query_factory_settlement_barrier(tmp_path, factory)
    assert result.closed is False and result.release_allowed is False and result.passed is False
    assert result.expected_effect_count == 3 and result.open_effect_count == 3
    assert result.effect_receipt_count == 0
    assert "resource_closure_unproven" in result.blocking_reasons
    assert "effect_receipt_missing" in result.blocking_reasons
    assert "effect_receipts_open" in result.blocking_reasons
