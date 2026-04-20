"""Smoke test for Traceability + DirectorPool end-to-end pipeline.

Validates the full PM → CE → DirectorPool → QA data flow for a single
synthetic iteration, including matrix persistence, traceability gate,
blueprint approval, and DirectorPool metrics.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from docs.governance.ci.scripts.run_traceability_gate import run_traceability_gate
from polaris.cells.chief_engineer.blueprint.internal.adr_store import ADRStore
from polaris.cells.chief_engineer.blueprint.internal.blueprint_persistence import (
    BlueprintPersistence,
)
from polaris.cells.chief_engineer.blueprint.internal.director_pool import (
    DirectorPool,
)
from polaris.kernelone.traceability.internal.safety import (
    safe_link,
    safe_register_node,
)
from polaris.kernelone.traceability.public.service import create_traceability_service


class _FakeTask:
    def __init__(self, task_id: str, files: list[str]) -> None:
        self.id = task_id
        self.task_id = task_id
        self.target_files = files


class _FakeBlueprint:
    def __init__(self, blueprint_id: str) -> None:
        self.blueprint_id = blueprint_id


@pytest.mark.anyio
async def test_full_traceability_directorpool_smoke() -> None:
    """End-to-end smoke: doc → task → blueprint → commit → verdict + gate pass."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = tmpdir
        trace_service = create_traceability_service(workspace)

        # 1. PM registers doc and task
        run_id = "smoke-run-001"
        doc_node = safe_register_node(
            trace_service,
            node_kind="doc",
            role="pm",
            external_id=run_id,
            content="PM plan for smoke test",
        )
        task_node = safe_register_node(
            trace_service,
            node_kind="task",
            role="pm",
            external_id="task-001",
            content="Task 001",
        )
        assert doc_node is not None
        assert task_node is not None
        safe_link(trace_service, doc_node, task_node, "derives_from")

        # 2. CE registers blueprint and links to task
        bp_node = safe_register_node(
            trace_service,
            node_kind="blueprint",
            role="chief_engineer",
            external_id="bp-task-001",
            content=json.dumps({"scope_paths": ["src/foo.py"]}),
            metadata={"doc_version": 1, "blueprint_version": 1},
        )
        assert bp_node is not None
        safe_link(trace_service, task_node, bp_node, "implements")

        # 3. CE persists approved blueprint for gate 14
        adr_store = ADRStore(workspace=workspace)
        adr_store.create_blueprint(
            "bp-task-001",
            {"scope_paths": ["src/foo.py"], "status": "approved"},
        )
        bp_persistence = BlueprintPersistence(workspace=workspace)
        bp_persistence.save(
            "bp-task-001",
            {
                "blueprint_id": "bp-task-001",
                "status": "approved",
                "task_id": "task-001",
                "run_id": run_id,
            },
        )

        # 4. DirectorPool assigns task and registers commit
        pool = DirectorPool(workspace=workspace, max_directors=2)
        pool.initialize_directors()
        async def _mock_submit(_t: Any, _b: Any) -> None:
            return None

        pool._submit_director_task_workflow = _mock_submit  # type: ignore[method-assign]
        await pool.assign_task(
            _FakeTask("task-001", ["src/foo.py"]),
            _FakeBlueprint("bp-task-001"),
        )

        commit_node = safe_register_node(
            trace_service,
            node_kind="commit",
            role="director",
            external_id="task-001:commit-abc",
            content=json.dumps({"task_id": "task-001", "changed_files": ["src/foo.py"]}),
            metadata={"blueprint_id": "bp-task-001"},
        )
        assert commit_node is not None
        safe_link(trace_service, bp_node, commit_node, "implements")

        # 5. QA registers verdict and links to commit
        verdict_node = safe_register_node(
            trace_service,
            node_kind="qa_verdict",
            role="qa",
            external_id="qa-smoke-001",
            content=json.dumps({"passed": True, "reason": "smoke"}),
        )
        assert verdict_node is not None
        safe_link(trace_service, commit_node, verdict_node, "verifies")

        # 6. Persist matrix
        matrix = trace_service.build_matrix(run_id, iteration=1)
        matrix_path = Path(workspace) / "runtime" / "traceability" / f"{run_id}.1.matrix.json"
        trace_service.persist(matrix, str(matrix_path))

        # 7. Run traceability gate
        gate_result = run_traceability_gate(workspace)
        assert gate_result.passed, f"Gate failed: {gate_result.errors}"

        # 8. Verify DirectorPool metrics populated
        dashboard = pool.get_live_dashboard()
        assert dashboard.directors
        # Active tasks gauge should have been set during dashboard call
        # We can't easily read the internal gauge value, but we can verify
        # the pool state is consistent.

        # 9. Verify blueprint approval in ADRStore
        compiled = adr_store.get_compiled_plan("bp-task-001")
        assert compiled is not None
        base = adr_store._blueprints.get("bp-task-001")
        assert base is not None
        assert base.status == "approved"

        # 10. Mark completed and verify cleanup
        pool.mark_completed("task-001", success=True)
        assert pool.get_director_for_task("task-001") is None
