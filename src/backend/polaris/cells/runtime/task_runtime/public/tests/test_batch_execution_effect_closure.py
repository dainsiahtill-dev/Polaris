"""Batch observation shares history reads, never authority or caller proofs."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.runtime.task_runtime.internal import execution_effect_closure as observer
from polaris.cells.runtime.task_runtime.public import execution_effect_closure as api
from polaris.cells.runtime.task_runtime.public.tests.test_historical_execution_effect_closure import (
    _facts,
    _history,
    _successor,
)


def _batch(root: Path, attempts: tuple[Any, ...]) -> tuple[Any, ...]:
    return api.query_execution_effect_closures(api.QueryExecutionEffectClosuresV1(str(root), attempts))


def _track_task_history(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = observer._read_owner_events

    def read(workspace: str, *, stream: str = "task_runtime.execution") -> Any:
        if stream == "task_runtime.execution":
            calls.append(workspace)
        return original(workspace, stream=stream)

    monkeypatch.setattr(observer, "_read_owner_events", read)
    return calls


@pytest.mark.asyncio
async def test_real_history_run_ledger_reads_task_history_twice_not_per_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts, rows = await _history(tmp_path)
    facts = _facts(tmp_path)
    calls = _track_task_history(monkeypatch)
    from polaris.cells.control_plane.run_ledger.public.service import _terminal_execution_effect_observations

    results = _terminal_execution_effect_observations(tmp_path, facts)
    assert [item.status for item in results] == ["closed"] * 3, results
    assert {item.execution_attempt.session_id for item in results} == {a.session_id for a in attempts}
    assert [len(item.receipts) for item in results] == [1, 1, 0]
    assert results[0].receipts[0].receipt_hash == rows[0]["effect_receipt"]["receipt_hash"]
    assert len(calls) == 2, calls


@pytest.mark.asyncio
async def test_public_batch_matches_real_single_observations_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts, _ = await _history(tmp_path)
    expected = tuple(
        api.query_execution_effect_closure(api.QueryExecutionEffectClosureV1(str(tmp_path), a)) for a in attempts
    )
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    calls = _track_task_history(monkeypatch)
    result = _batch(tmp_path, attempts)
    assert result == expected
    assert all(item.execution_authority is False for item in result)
    assert len(calls) == 2
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["workspace", "session_id", "run_id", "worker_id"])
async def test_foreign_item_never_borrows_a_sibling_proof(tmp_path: Path, field: str) -> None:
    attempts, _ = await _history(tmp_path)
    forged = replace(attempts[0], **{field: str(tmp_path / "foreign") if field == "workspace" else "foreign"})
    results = _batch(tmp_path, (attempts[0], forged))
    assert results[0].status == "closed"
    assert results[1].status == "unknown"
    assert results[1].receipts == ()
    assert results[1].execution_authority is False


@pytest.mark.asyncio
async def test_late_real_successor_makes_entire_batch_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts, _ = await _history(tmp_path)
    original = observer._read_owner_events
    calls = 0

    def read(workspace: str, *, stream: str = "task_runtime.execution") -> Any:
        nonlocal calls
        if stream == "task_runtime.execution":
            calls += 1
            if calls == 2:
                _successor(tmp_path, attempts[-1], "late-active-successor")
        return original(workspace, stream=stream)

    monkeypatch.setattr(observer, "_read_owner_events", read)
    results = _batch(tmp_path, attempts)
    assert calls == 2
    assert all(item.status == "unknown" for item in results)
    assert all(item.reason == "closure_observation_changed" for item in results)
    assert all(item.receipts == () and item.terminal_failures == () for item in results)


@pytest.mark.asyncio
async def test_fresh_query_does_not_reuse_history_after_corruption(tmp_path: Path) -> None:
    attempts, _ = await _history(tmp_path)
    assert all(item.status == "closed" for item in _batch(tmp_path, attempts))
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events[-1]["integrity_digest"] = "0" * 64
    path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    results = _batch(tmp_path, attempts)
    assert all(item.status == "unknown" and item.receipts == () for item in results)


@pytest.mark.asyncio
async def test_active_successor_does_not_erase_or_close_other_owners(tmp_path: Path) -> None:
    attempts, _ = await _history(tmp_path)
    active = _successor(tmp_path, attempts[-1], "active-batch-owner")
    results = _batch(tmp_path, (*attempts, active))
    assert [item.status for item in results] == ["closed", "closed", "closed", "active"], results
    assert results[-1].receipts == ()
    assert all(item.execution_authority is False for item in results)


@pytest.mark.asyncio
async def test_corrupt_operation_cannot_borrow_other_identity_proof(tmp_path: Path) -> None:
    from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import DirectedEffectOperationRepository
    from polaris.cells.runtime.task_runtime.public import DirectedEffectParentRegistryIdentityV1

    attempts, _ = await _history(tmp_path)
    identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempts[0])
    registry = DirectedEffectOperationRepository()._load_registry(str(tmp_path), identity)
    binding = next(iter(registry.bindings_by_id.values()))
    path = tmp_path / f".polaris/runtime/events/{binding.operation_stream_token}.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("receipt_hash", "corrupt_hash", 1), encoding="utf-8")
    results = _batch(tmp_path, attempts)
    assert [item.status for item in results] == ["unknown", "closed", "closed"], results
    assert results[0].receipts == ()


@pytest.mark.asyncio
async def test_real_v3_failed_cli_remains_negative_closed_in_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.runtime.task_runtime.public.tests.test_payload_fact_execution_effect_closure import (
        _real_v3_failed_cli,
    )

    attempt, batch = await _real_v3_failed_cli(tmp_path, monkeypatch)
    expected = api.query_execution_effect_closure(api.QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    calls = _track_task_history(monkeypatch)
    (result,) = _batch(tmp_path, (attempt,))
    assert result == expected
    assert result.status == "closed" and result.receipts == ()
    assert result.terminal_failures[0].exit_code == 2
    assert result.terminal_failures[0].owner_raw_result_hash
    assert batch["effect_receipts"] == []
    assert batch["results"][0].get("effect_receipt") is None
    assert len(calls) == 2


def test_public_batch_accepts_identities_only_and_empty_batch_does_no_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("empty batch must not read history")

    monkeypatch.setattr(observer, "_read_owner_events", forbidden)
    assert _batch(tmp_path, ()) == ()
    with pytest.raises(TypeError):
        api.QueryExecutionEffectClosuresV1(str(tmp_path), [])
    with pytest.raises(TypeError):
        api.QueryExecutionEffectClosuresV1(str(tmp_path), ({"facts": []},))
    with pytest.raises(TypeError):
        api.query_execution_effect_closures({"workspace": str(tmp_path), "facts": []})
