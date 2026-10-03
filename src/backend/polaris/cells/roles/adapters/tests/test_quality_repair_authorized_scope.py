"""Repair-loop candidates must consume admitted writes, not reference inventory."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
    _run_materialization_quality_repair_retry,
)

OWNED = ["index.html", "src/entry/browser-main.ts"]
REFERENCE = "src/engine/renderer.ts"


class _RepairBoundaryAdapter:
    """Record the real loop's external call; never execute Provider or tools."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = str(workspace)
        self.calls: list[dict[str, Any]] = []
        self._execution = SimpleNamespace(
            extract_kernel_tool_results=lambda _result: [],
            execute_tools=self._no_tools,
        )

    @staticmethod
    async def _no_tools(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    @staticmethod
    def _update_task_progress(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _invoke_role_dialogue_with_timeout(
        self, message: str, *, context: dict[str, Any], timeout_seconds: float, stage_label: str
    ) -> dict[str, Any]:
        self.calls.append({"message": message, "context": context, "timeout": timeout_seconds, "stage": stage_label})
        return {"content": "", "tool_results": []}


def _task(*, reference_in_targets: bool = False) -> dict[str, Any]:
    return {
        "task_id": "TASK-2",
        "target_files": [*OWNED, *([REFERENCE] if reference_in_targets else [])],
        "metadata": {"scope_paths": ["index.html", REFERENCE], "external_task_id": "TASK-2"},
    }


def _context(allowed: Any = None) -> dict[str, Any]:
    return {
        "director_execution_envelope": {
            "authorization": {"allowed_write_paths": list(OWNED) if allowed is None else allowed}
        }
    }


def _owned_workspace(workspace: Path) -> None:
    (workspace / "index.html").write_text("<!doctype html><main></main>\n", encoding="utf-8")
    source = workspace / OWNED[1]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("export const view = 1;\n", encoding="utf-8")


async def _repair(
    adapter: _RepairBoundaryAdapter, task: dict[str, Any], context: dict[str, Any], errors: list[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return await _run_materialization_quality_repair_retry(
        adapter,
        task=task,
        target_task_id="TASK-2",
        run_id="factory-authorized-repair-unit",
        context=context,
        original_message="Repair the current task's browser entrypoints.",
        llm_call_timeout=30,
        artifact_quality_errors=errors,
        changed_files=["index.html", "src/entry/browser-main.ts"],
    )


@pytest.mark.parametrize("reference_in_targets", [False, True])
@pytest.mark.asyncio
async def test_real_loop_defers_reference_missing_file_before_provider(
    tmp_path: Path, reference_in_targets: bool
) -> None:
    _owned_workspace(tmp_path)
    adapter = _RepairBoundaryAdapter(tmp_path)
    task = _task(reference_in_targets=reference_in_targets)
    context = _context()
    before_task, before_context = deepcopy(task), deepcopy(context)

    results, summary = await _repair(
        adapter, task, context, [f"Artifact quality scan failed: declared target file missing '{REFERENCE}'"]
    )

    assert summary["stage"] == "task_boundary_repair_targets_deferred"
    assert summary["repair_target_files"] == []
    assert summary["success"] is False
    assert summary["write_tool_evidence"] is False
    assert adapter.calls == []
    assert results == []
    assert not (tmp_path / REFERENCE).exists()
    assert task == before_task
    assert context == before_context


@pytest.mark.parametrize(
    "error",
    [
        f"{OWNED[1]}(2,8): error TS2345: Argument of type 'number' is not assignable to parameter of type 'string'.",
        f"Artifact quality scan failed: unresolved relative import '../engine/renderer.js' in {OWNED[1]}",
    ],
)
@pytest.mark.asyncio
async def test_real_loop_keeps_owned_importer_edit_without_creating_reference(tmp_path: Path, error: str) -> None:
    _owned_workspace(tmp_path)
    source = tmp_path / "src/entry/browser-main.ts"
    source.write_text("import { render } from '../engine/renderer.js';\n", encoding="utf-8")
    adapter = _RepairBoundaryAdapter(tmp_path)
    context = _context()
    # Real catalog/probe remains unmodified. This is the existing caller-owned
    # route authorization, not a fake executable repair plan or success receipt.
    context["director_interface_discrepancy_retry"] = {
        "authorized": True,
        "reason": "coverage_matched_but_unplannable",
        "recommended_owner": "director",
        "recommended_route": "director_retry_with_interface_discrepancy_context",
    }

    _, summary = await _repair(
        adapter,
        _task(reference_in_targets=True),
        context,
        [error],
    )

    assert summary["repair_target_files"] == [OWNED[1]]
    assert summary.get("llm_fallback_blocked") is not True
    assert len(adapter.calls) == 1
    repair_context = adapter.calls[0]["context"]
    assert repair_context["director_quality_repair"]["repair_target_files"] == [OWNED[1]]
    assert "write_only_single_target" not in repair_context["director_quality_repair"]
    assert repair_context["metadata"]["tool_path_contract"]["allowed_target_files"] == [OWNED[1]]
    assert not (tmp_path / REFERENCE).exists()


@pytest.mark.parametrize("task_allowed", [[], OWNED])
@pytest.mark.asyncio
async def test_real_loop_context_cannot_widen_task_admitted_authority(tmp_path: Path, task_allowed: list[str]) -> None:
    _owned_workspace(tmp_path)
    adapter = _RepairBoundaryAdapter(tmp_path)
    task = _task(reference_in_targets=True)
    task["director_execution_envelope"] = {"authorization": {"allowed_write_paths": task_allowed}}
    context = _context([*OWNED, REFERENCE])
    before_task, before_context = deepcopy(task), deepcopy(context)

    results, summary = await _repair(
        adapter, task, context, [f"Artifact quality scan failed: declared target file missing '{REFERENCE}'"]
    )

    assert summary["stage"] == "task_boundary_repair_targets_deferred"
    assert summary["repair_target_files"] == []
    assert results == []
    assert adapter.calls == []
    assert task == before_task
    assert context == before_context


@pytest.mark.asyncio
async def test_real_loop_valid_context_cannot_hide_malformed_task_authority(tmp_path: Path) -> None:
    adapter = _RepairBoundaryAdapter(tmp_path)
    task = _task(reference_in_targets=True)
    task["director_execution_envelope"] = {}

    with pytest.raises(ValueError, match="task_write_guidance_authorization_missing"):
        await _repair(
            adapter,
            task,
            _context([*OWNED, REFERENCE]),
            [f"Artifact quality scan failed: declared target file missing '{REFERENCE}'"],
        )

    assert adapter.calls == []


@pytest.mark.asyncio
async def test_real_loop_explicit_empty_write_authority_defers_all_candidates(tmp_path: Path) -> None:
    adapter = _RepairBoundaryAdapter(tmp_path)

    results, summary = await _repair(
        adapter, _task(), _context([]), ["Artifact quality scan failed: declared target file missing 'index.html'"]
    )

    assert summary["stage"] == "task_boundary_repair_targets_deferred"
    assert summary["repair_target_files"] == []
    assert results == []
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_real_loop_malformed_authority_does_not_fallback_to_inventory(tmp_path: Path) -> None:
    adapter = _RepairBoundaryAdapter(tmp_path)

    with pytest.raises(ValueError, match="task_write_guidance_paths_invalid"):
        await _repair(
            adapter,
            _task(),
            _context("index.html"),
            ["Artifact quality scan failed: declared target file missing 'index.html'"],
        )

    assert adapter.calls == []


@pytest.mark.asyncio
async def test_real_loop_without_envelope_keeps_legacy_declared_scope(tmp_path: Path) -> None:
    _owned_workspace(tmp_path)
    adapter = _RepairBoundaryAdapter(tmp_path)

    _, summary = await _repair(
        adapter, _task(), {}, [f"Artifact quality scan failed: declared target file missing '{REFERENCE}'"]
    )

    assert summary["repair_target_files"] == [REFERENCE]
    assert len(adapter.calls) == 1
