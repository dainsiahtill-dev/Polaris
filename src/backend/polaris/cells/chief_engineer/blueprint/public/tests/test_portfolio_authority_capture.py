"""CE host capture contract; source callbacks below are explicitly synthetic.

These owner-unit snapshots test shape/binding/seal only. They are not durable
Factory PM authority or Native/provider evidence. Real Factory source coverage
belongs to the adjacent Factory test module.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint import public as ce
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.chief_engineer.blueprint.public.service._portfolio import (
    _revalidate_portfolio_authority_carrier,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def _api() -> tuple[Any, Any]:
    assert hasattr(ce, "CaptureChiefEngineerPortfolioAuthorityQueryV1"), "checked host capture query is missing"
    assert hasattr(ce, "capture_chief_engineer_portfolio_authority"), "checked host capture operation is missing"
    return (
        ce.CaptureChiefEngineerPortfolioAuthorityQueryV1,
        ce.capture_chief_engineer_portfolio_authority,
    )


def _tasks() -> tuple[ChiefEngineerPortfolioTaskV1, ...]:
    return (
        ChiefEngineerPortfolioTaskV1(
            task_id="SYNTHETIC-TASK",
            objective="Exercise host boundary without execution",
            target_files=(),
            topology_authority="chief_engineer",
            planning_contract_version="pm.task_intent.v1",
            verification_authority="chief_engineer",
            observable_acceptance=("Synthetic host data stays outside decoded JSON",),
        ),
    )


def _synthetic_snapshot(query: Any) -> dict[str, Any]:
    policy = {
        "schema_version": "evidence_policy.v1",
        "source": "control_plane.verifier_policy.evidence_policy_compiler",
        "policy_hash": "b" * 64,
        "required_evidence_modalities": ["command"],
    }
    return {
        "workspace": query.workspace,
        "run_id": query.run_id,
        "project_id": "synthetic-project",
        "pm_stage_event_id": "synthetic-pm-event-not-durable-proof",
        "pm_contract_hash": "a" * 64,
        "tasks": query.tasks,
        "catalog_snapshot": {},
        "catalog_snapshot_hash": project_completion_catalog_snapshot_hash({}),
        "verifier_policy_hash": "b" * 64,
        "verifier_policy_snapshot": policy,
        "verifier_policy_snapshot_hash": project_completion_verifier_policy_snapshot_hash(policy),
        "verification_command_authority": (),
    }


@pytest.mark.asyncio
async def test_capture_awaits_host_source_and_returns_current_exact_carrier(
    tmp_path: Path,
) -> None:
    query_type, capture = _api()
    query = query_type(str(tmp_path), "synthetic-run", _tasks())
    visited: list[Any] = []

    async def synthetic_source(request: Any) -> dict[str, Any]:
        await asyncio.sleep(0)
        visited.append(request)
        return _synthetic_snapshot(request)

    carrier = await capture(query, source_port=synthetic_source)
    command = BuildChiefEngineerBlueprintPortfolioCommandV1(
        workspace=str(tmp_path),
        run_id="synthetic-run",
        tasks=_tasks(),
        authority_carrier=carrier,
        llm_blueprint={"synthetic": True},
    )
    assert visited == [query]
    assert _revalidate_portfolio_authority_carrier(command) is carrier
    assert carrier.project_id == "synthetic-project"
    assert not hasattr(ce, "_issue_chief_engineer_portfolio_authority_carrier")
    assert not hasattr(ce, "_ChiefEngineerPortfolioAuthorityCarrierV1")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["workspace", "run_id", "tasks"])
async def test_capture_rejects_source_identity_different_from_query(tmp_path: Path, field: str) -> None:
    query_type, capture = _api()
    query = query_type(str(tmp_path), "synthetic-run", _tasks())

    async def synthetic_source(request: Any) -> dict[str, Any]:
        snapshot = _synthetic_snapshot(request)
        snapshot[field] = () if field == "tasks" else "foreign"
        return snapshot

    with pytest.raises(ValueError, match="identity"):
        await capture(query, source_port=synthetic_source)


@pytest.mark.asyncio
async def test_capture_rejects_decoded_json_as_source_port(tmp_path: Path) -> None:
    query_type, capture = _api()
    query = query_type(str(tmp_path), "synthetic-run", _tasks())
    with pytest.raises(TypeError, match="source_port"):
        await capture(query, source_port=_synthetic_snapshot(query))
    with pytest.raises(TypeError, match="unexpected keyword"):
        query_type(str(tmp_path), "synthetic-run", _tasks(), pm_contract_hash="a" * 64)


@pytest.mark.asyncio
async def test_capture_rejects_decoded_query_before_calling_a_source(
    tmp_path: Path,
) -> None:
    _, capture = _api()

    async def unexpected_source(request: Any) -> dict[str, Any]:
        raise AssertionError("decoded query reached a host source")

    with pytest.raises(TypeError, match="query"):
        await capture(
            {"workspace": str(tmp_path), "run_id": "synthetic-run"},
            source_port=unexpected_source,
        )


@pytest.mark.asyncio
async def test_capture_rejects_non_mapping_source_result(tmp_path: Path) -> None:
    query_type, capture = _api()

    async def synthetic_source(request: Any) -> None:
        return None

    with pytest.raises(TypeError, match="source"):
        await capture(
            query_type(str(tmp_path), "synthetic-run", _tasks()),
            source_port=synthetic_source,
        )


@pytest.mark.asyncio
async def test_capture_propagates_real_source_failure_without_seed_fallback(
    tmp_path: Path,
) -> None:
    query_type, capture = _api()

    async def unavailable_source(request: Any) -> dict[str, Any]:
        raise OSError("synthetic source unavailable")

    with pytest.raises(OSError, match="synthetic source unavailable"):
        await capture(
            query_type(str(tmp_path), "synthetic-run", _tasks()),
            source_port=unavailable_source,
        )


@pytest.mark.asyncio
async def test_capture_cancellation_propagates_to_pending_source(
    tmp_path: Path,
) -> None:
    query_type, capture = _api()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending_source(request: Any) -> dict[str, Any]:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return _synthetic_snapshot(request)

    task = asyncio.create_task(
        capture(
            query_type(str(tmp_path), "synthetic-run", _tasks()),
            source_port=pending_source,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled() and cancelled.is_set()
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_captured_carrier_keeps_existing_signature_check(tmp_path: Path) -> None:
    query_type, capture = _api()
    query = query_type(str(tmp_path), "synthetic-run", _tasks())

    async def synthetic_source(request: Any) -> dict[str, Any]:
        return _synthetic_snapshot(request)

    carrier = await capture(query, source_port=synthetic_source)
    command = BuildChiefEngineerBlueprintPortfolioCommandV1(
        workspace=str(tmp_path),
        run_id="synthetic-run",
        tasks=_tasks(),
        authority_carrier=carrier,
        llm_blueprint={"synthetic": True},
    )
    object.__setattr__(carrier, "project_id", "mutated-host-data")
    with pytest.raises(ce.ChiefEngineerBlueprintErrorV1, match="signature"):
        _revalidate_portfolio_authority_carrier(command)


@pytest.mark.asyncio
async def test_captured_carrier_still_rejects_live_catalog_drift(
    tmp_path: Path,
) -> None:
    query_type, capture = _api()
    query = query_type(str(tmp_path), "synthetic-run", _tasks())

    async def synthetic_source(request: Any) -> dict[str, Any]:
        return _synthetic_snapshot(request)

    carrier = await capture(query, source_port=synthetic_source)
    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    fs.workspace_write_text(
        ".polaris/catalog_contract.json",
        json.dumps({"project_kind": "library"}),
        encoding="utf-8",
    )
    command = BuildChiefEngineerBlueprintPortfolioCommandV1(
        workspace=str(tmp_path),
        run_id="synthetic-run",
        tasks=_tasks(),
        authority_carrier=carrier,
        llm_blueprint={"synthetic": True},
    )
    with pytest.raises(ce.ChiefEngineerBlueprintErrorV1, match="drifted"):
        _revalidate_portfolio_authority_carrier(command)


def test_host_capture_is_absent_from_real_model_tool_schemas() -> None:
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    _api()
    name = "capture_chief_engineer_portfolio_authority"
    assert ToolSpecRegistry.get(name) is None
    schemas = ToolSpecRegistry.generate_llm_schemas()
    assert name not in {row["function"]["name"] for row in schemas}
