"""Shared test helpers for the v2 Director router test suite.

These build mocked Director diagnostics / blueprint payloads used across the
per-endpoint router test modules. Kept module-private (underscore-prefixed) and
re-exported via relative import in each test file.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch


def _director_run_diagnostics(
    *,
    workspace: str = ".",
    can_execute: bool = True,
    execution_blockers: list[str] | None = None,
    ready_task_ids: list[str] | None = None,
    blueprint_ready_task_ids: list[str] | None = None,
) -> object:
    """Build a Director diagnostics response for /run preflight tests."""

    from polaris.delivery.http.v2.director import (
        DirectorDiagnosticsLLMSection,
        DirectorDiagnosticsResponse,
        DirectorDiagnosticsStatusSection,
        DirectorDiagnosticsTaskSection,
        DirectorDiagnosticsWorkerSection,
    )

    blockers = list(execution_blockers or [])
    return DirectorDiagnosticsResponse(
        ok=not blockers,
        can_execute=can_execute and not blockers,
        role="director",
        generated_at="2026-05-24T00:00:00Z",
        workspace=workspace,
        status=DirectorDiagnosticsStatusSection(
            ok=True,
            state="IDLE",
            running=False,
            source="workflow",
            projection_source="director_merged",
        ),
        tasks=DirectorDiagnosticsTaskSection(
            ok=True,
            source="workflow",
            total=1,
            pending=1,
            ready_to_execute=1,
            ready_task_ids=list(ready_task_ids or ["PM-42"]),
            blueprint_ready_task_ids=list(blueprint_ready_task_ids or []),
        ),
        workers=DirectorDiagnosticsWorkerSection(
            ok=True,
            total=1,
            idle=1,
            healthy=1,
        ),
        llm=DirectorDiagnosticsLLMSection(
            ok=True,
            state="ready",
            blocked_roles=[],
            unsupported_roles=[],
            required_ready_roles=["director"],
            provider_id="qwen",
            model="qwen3-max",
        ),
        issues=list(blockers),
        execution_blockers=blockers,
    )


def _patch_director_blueprint_persistence(payload_by_id: dict[str, dict[str, object]]) -> Any:
    """Patch Director's read-only CE blueprint persistence probe."""

    persistence = MagicMock()
    persistence.list_all.return_value = list(payload_by_id.keys())
    persistence.load.side_effect = lambda blueprint_id: payload_by_id.get(str(blueprint_id))
    return patch("polaris.delivery.http.v2.director.BlueprintPersistence", return_value=persistence)


def _ready_blueprint(blueprint_id: str, task_id: str, *, status: str = "generated") -> dict[str, object]:
    return {
        "blueprint_id": blueprint_id,
        "task_id": task_id,
        "status": status,
        "target_files": [f"src/{task_id.lower()}.ts"],
        "acceptance_criteria": [f"{task_id} acceptance is implemented"],
        "execution_checklist": [f"Implement {task_id}", f"Verify {task_id}"],
        "contract_completeness": {
            "handoff_ready": True,
            "missing_fields": [],
            "requires": ["target_files", "acceptance_criteria", "execution_checklist"],
        },
        "handoff_ready": True,
    }
