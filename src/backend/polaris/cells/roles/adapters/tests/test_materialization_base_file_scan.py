"""Bounded materialization scans must not poison TS planners with audit dumps."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    plan_director_repair,
)
from polaris.cells.roles.adapters.internal.director.materialization_quality_callback_ports import (
    _add_bounded_workspace_materialization_base_files,
    _collect_materialization_runtime_base_files,
    _run_materialization_typescript_compiler,
    _should_skip_bounded_materialization_path,
)
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1

_TS2341_WEB = (
    "src/web.ts(3,21): error TS2341: Property 'trajectory' is private "
    "and only accessible within class 'FlightController'."
)
_CONSUMER = (
    "import { FlightController } from './engine/simulation.js';\n"
    "export function paint(controller: FlightController): number {\n"
    "  return controller.trajectory.length;\n"
    "}\n"
)
_OWNER = "export class FlightController {\n  private trajectory: number[] = [];\n}\n"


def _write_l108_shape(workspace: Path) -> None:
    (workspace / "src" / "engine").mkdir(parents=True)
    (workspace / "src" / "web.ts").write_text(_CONSUMER, encoding="utf-8")
    (workspace / "src" / "engine" / "simulation.ts").write_text(_OWNER, encoding="utf-8")
    (workspace / "package.json").write_text('{"name":"lab","private":true}\n', encoding="utf-8")
    poison = workspace / ".polaris" / "cognitive_sessions"
    poison.mkdir(parents=True)
    (poison / "tx-fake.json").write_text(
        '{"snippet":"private trajectory: SimulationStep[] = [];"}',
        encoding="utf-8",
    )


def test_should_skip_polaris_session_and_non_manifest_json() -> None:
    assert _should_skip_bounded_materialization_path(".polaris/cognitive_sessions/tx.json") is True
    assert _should_skip_bounded_materialization_path("node_modules/foo/index.ts") is True
    assert _should_skip_bounded_materialization_path("src/engine/simulation.ts") is False
    assert _should_skip_bounded_materialization_path("package.json") is False
    assert _should_skip_bounded_materialization_path("tsconfig.json") is False
    assert _should_skip_bounded_materialization_path("docs/notes.json") is True


def test_bounded_scan_does_not_ingest_polaris_session_json(tmp_path: Path) -> None:
    _write_l108_shape(tmp_path)
    base_files: dict[str, str] = {}
    _add_bounded_workspace_materialization_base_files(
        base_files,
        tmp_path,
        allowed_suffixes=(".ts", ".tsx", ".js", ".json"),
        max_files=256,
    )
    assert not any(path.startswith(".polaris/") for path in base_files)
    assert "src/engine/simulation.ts" in base_files
    assert "src/web.ts" in base_files
    assert "package.json" in base_files


def test_private_property_still_plans_when_workspace_has_session_dumps(tmp_path: Path) -> None:
    """Live L1-08: 77-file scan including session JSON made planned=False."""

    _write_l108_shape(tmp_path)
    task = {
        "target_files": ["src/web.ts", "src/engine/simulation.ts"],
        "metadata": {"target_files": ["src/web.ts", "src/engine/simulation.ts"]},
    }
    collected = _collect_materialization_runtime_base_files(
        tmp_path,
        artifact_quality_errors=[_TS2341_WEB],
        source_tool="deterministic_typescript_private_property_access_repair",
        allowed_suffixes=(".ts", ".tsx", ".js", ".jsx", ".json"),
        collect_unmatched_diagnostic_paths=False,
        task=task,
    )
    expanded = dict(collected)
    _add_bounded_workspace_materialization_base_files(
        expanded,
        tmp_path,
        allowed_suffixes=(".ts", ".tsx", ".js", ".json"),
        max_files=256,
    )
    assert not any(path.startswith(".polaris/") for path in expanded)
    planned = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_private_property_access_repair",
            artifact_quality_errors=(_TS2341_WEB,),
            base_files=expanded,
            deterministic_only=True,
        )
    )
    assert planned.ok is True
    assert planned.planned is True


def test_typescript_compiler_returns_deferred_despite_session_dumps(tmp_path: Path) -> None:
    _write_l108_shape(tmp_path)
    adapter = type("Adapter", (), {"workspace": str(tmp_path)})()
    attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace=str(tmp_path),
        task_id=2,
        external_task_id="TASK-2",
        session_id="tx-test",
        attempt=1,
        role_id="director",
        worker_id="director",
        run_id="factory_test",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    results = _run_materialization_typescript_compiler(
        adapter,
        task={
            "target_files": ["src/web.ts", "src/engine/simulation.ts"],
            "metadata": {"target_files": ["src/web.ts", "src/engine/simulation.ts"]},
        },
        task_id="TASK-2",
        artifact_quality_errors=[_TS2341_WEB],
        execution_attempt=attempt,
    )
    assert results, "compiler must not silently drop a plannable TS2341 repair"
    payload = results[0].get("result") or {}
    assert payload.get("source_tool") == "deterministic_typescript_private_property_access_repair"
    assert payload.get("deferred_request") is not None
