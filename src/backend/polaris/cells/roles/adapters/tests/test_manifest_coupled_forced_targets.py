"""A narrow Factory hint must not truncate an admitted causal manifest pair."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
    _run_materialization_quality_repair_retry,
)
from polaris.cells.roles.adapters.tests.test_manifest_causal_quality_targets import (
    ERROR,
    MANIFESTS,
    MISSING_JS,
    TEST_SOURCE,
    _task,
    _workspace,
)
from polaris.cells.roles.adapters.tests.test_quality_repair_authorized_scope import _RepairBoundaryAdapter


async def _repair(
    root: Path,
    *,
    allowed: list[str],
    forced: list[str],
    attempt: int = 1,
    admitted: bool = True,
    context_allowed: list[str] | None = None,
) -> tuple[dict[str, Any], _RepairBoundaryAdapter]:
    adapter = _RepairBoundaryAdapter(root)
    task = _task(allowed)
    if not admitted:
        task.pop("director_execution_envelope")
    context: dict[str, Any] = {"director_quality_repair": {"repair_target_files": forced}}
    if context_allowed is not None:
        context["director_execution_envelope"] = {"authorization": {"allowed_write_paths": context_allowed}}
    before_task, before_context = deepcopy(task), deepcopy(context)
    before_files = {path: (root / path).read_bytes() for path in [*MANIFESTS, TEST_SOURCE]}

    _, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task=task,
        target_task_id="TASK-1",
        run_id="factory-coupled-manifest-unit",
        context=context,
        original_message="Repair the package/compiler test contract without creating unowned outputs.",
        llm_call_timeout=30,
        artifact_quality_errors=[ERROR],
        changed_files=MANIFESTS,
        repair_attempt=attempt,
    )

    assert task == before_task
    assert context == before_context
    assert {path: (root / path).read_bytes() for path in before_files} == before_files
    assert not (root / MISSING_JS).exists()
    return summary, adapter


@pytest.mark.parametrize("forced", [["tsconfig.json"], ["package.json"]])
@pytest.mark.parametrize("attempt", [1, 2, 3])
@pytest.mark.asyncio
async def test_real_forced_overlap_keeps_complete_admitted_manifest_pair(
    tmp_path: Path, forced: list[str], attempt: int
) -> None:
    _workspace(tmp_path)

    summary, adapter = await _repair(tmp_path, allowed=MANIFESTS, forced=forced, attempt=attempt)

    assert summary["repair_target_files"] == MANIFESTS
    assert summary.get("llm_fallback_blocked") is not True
    assert summary["success"] is False  # No tool effect or verifier success was faked.
    assert len(adapter.calls) == 1
    context = adapter.calls[0]["context"]
    assert context["metadata"]["tool_path_contract"]["allowed_target_files"] == MANIFESTS
    assert context["director_quality_repair"]["repair_target_files"] == MANIFESTS
    assert "write_only_single_target" not in context["director_quality_repair"]
    # This fixture stops at the adapter's external invocation boundary. The
    # registry-faithful schemas are scope-pinned later by the real Kernel;
    # do not treat these raw definitions as a final provider-wire schema.


@pytest.mark.parametrize("allowed", [["package.json"], ["tsconfig.json"]])
@pytest.mark.parametrize("context_allowed", [None, MANIFESTS])
@pytest.mark.asyncio
async def test_one_manifest_grant_does_not_expand_to_the_pair(
    tmp_path: Path, allowed: list[str], context_allowed: list[str] | None
) -> None:
    _workspace(tmp_path)

    summary, adapter = await _repair(tmp_path, allowed=allowed, forced=allowed, context_allowed=context_allowed)

    assert summary["repair_target_files"] == allowed
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["context"]["metadata"]["tool_path_contract"]["allowed_target_files"] == allowed


@pytest.mark.asyncio
async def test_foreign_manifest_inventory_does_not_acquire_any_write(tmp_path: Path) -> None:
    _workspace(tmp_path)

    summary, adapter = await _repair(tmp_path, allowed=[TEST_SOURCE], forced=["tsconfig.json"])

    assert summary["repair_target_files"] == []
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_empty_task_grant_is_not_expanded_by_broad_context_or_forced_hint(tmp_path: Path) -> None:
    _workspace(tmp_path)

    summary, adapter = await _repair(tmp_path, allowed=[], forced=["tsconfig.json"], context_allowed=MANIFESTS)

    assert summary["repair_target_files"] == []
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_unknown_compiler_contract_keeps_forced_target_behavior(tmp_path: Path) -> None:
    _workspace(tmp_path)
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"rootDir": "."}, "include": ["**/*.ts"]}), encoding="utf-8"
    )

    summary, adapter = await _repair(tmp_path, allowed=MANIFESTS, forced=["tsconfig.json"])

    assert summary["repair_target_files"] == []
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_no_admitted_envelope_keeps_legacy_narrow_forced_behavior(tmp_path: Path) -> None:
    _workspace(tmp_path)

    summary, adapter = await _repair(tmp_path, allowed=MANIFESTS, forced=["tsconfig.json"], admitted=False)

    assert summary["repair_target_files"] == ["tsconfig.json"]
    assert len(adapter.calls) == 1
