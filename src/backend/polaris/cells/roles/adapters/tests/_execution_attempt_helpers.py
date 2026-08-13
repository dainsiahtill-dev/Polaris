"""Shared test fixtures/helpers for the Director adapter test suite.

These three helpers were previously copy-pasted byte-identically across 11
``test_director_adapter_*.py`` files. Consolidated here as the single source
of truth; each test file imports the names it needs via
``from ._execution_attempt_helpers import ...``.

Nothing here is production code — it exists only to construct canonical
``TaskRuntimeExecutionAttemptIdentityV1`` identities and to project deferred
repair effects forward inside isolated temporary workspaces so planner/content
assertions remain valid at the roles-adapter boundary (production stays
plan-only there).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1
from polaris.cells.runtime.task_runtime.public.service import (
    create_task_runtime_execution_attempt_authority,
)

__all__ = [
    "_project_deferred_repair_results_for_test",
    "_test_execution_attempt",
    "_test_execution_attempt_context",
]


def _test_execution_attempt(workspace: Path, task_id: str) -> TaskRuntimeExecutionAttemptIdentityV1:
    """Return exact attempt identity for test-only deferred-effect projection."""

    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace=workspace.resolve().as_posix(),
        task_id=91,
        external_task_id=task_id,
        session_id=f"session-{task_id}",
        attempt=1,
        role_id="director",
        worker_id="director-test-worker",
        run_id=f"run-{task_id}",
        lease_expires_at="2099-01-01T00:00:00Z",
    )


def _test_execution_attempt_context(workspace: Path, task_id: str) -> dict[str, Any]:
    return {
        "task_runtime_execution_attempt_authority": create_task_runtime_execution_attempt_authority(
            _test_execution_attempt(workspace, task_id)
        )
    }


def _project_deferred_repair_results_for_test(
    workspace: Path,
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply deferred effects only inside the test fixture.

    Production remains plan-only at the roles adapter boundary.  These tests
    retain their planner/content assertions by projecting forward effects in
    their isolated temporary workspace.
    """

    projected: list[dict[str, Any]] = []
    for item in tool_results:
        result = item.get("result")
        result_payload = dict(result) if isinstance(result, dict) else {}
        request = result_payload.get("deferred_request")
        if item.get("tool_name") != "deferred_director_repair" or request is None:
            projected.append(dict(item))
            continue
        repair_kernel = dict(result_payload.get("repair_kernel") or {})
        planning = dict(repair_kernel.get("planning") or {})
        repair_kernel.update(
            {
                "status": "applied",
                "planning_preflight": planning,
                "metadata": {"requires_revalidation": True},
            }
        )
        for effect in request.plan.effects:
            if effect.contingency_kind != "forward":
                continue
            arguments = dict(effect.arguments)
            target = workspace / effect.target_path
            if effect.tool_name == "write_file":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(arguments["content"]), encoding="utf-8")
            elif effect.tool_name == "edit_file":
                original = target.read_text(encoding="utf-8")
                search = str(arguments["search"])
                assert search in original
                target.write_text(original.replace(search, str(arguments["replace"]), 1), encoding="utf-8")
            else:
                target.unlink()
            projected.append(
                {
                    "tool": effect.tool_name,
                    "tool_name": effect.tool_name,
                    "success": True,
                    "result": {
                        "ok": True,
                        "source_tool": request.plan.source_tool,
                        "file": effect.target_path,
                        "repair_kernel": repair_kernel,
                    },
                }
            )
    return projected
