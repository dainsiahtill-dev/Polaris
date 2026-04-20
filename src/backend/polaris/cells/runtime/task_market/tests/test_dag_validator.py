"""Tests for DAG validator — cycle detection and orphan reference detection."""

from __future__ import annotations

from polaris.cells.runtime.task_market.internal.service import TaskMarketService
from polaris.cells.runtime.task_market.public.contracts import PublishTaskWorkItemCommandV1


def _publish_with_deps(
    service: TaskMarketService,
    workspace: str,
    task_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    plan_id: str = "plan-1",
) -> None:
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id=f"trace-{task_id}",
            run_id="run-1",
            task_id=task_id,
            stage="pending_design",
            source_role="pm",
            plan_id=plan_id,
            depends_on=list(depends_on),
            payload={"title": task_id},
        )
    )


def test_dag_valid_no_deps(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _publish_with_deps(service, workspace, "task-a")
    _publish_with_deps(service, workspace, "task-b")

    result = service.validate_dependency_dag(workspace)
    assert result["valid"] is True
    assert result["cycle_count"] == 0
    assert result["total_nodes"] == 2
    assert result["total_edges"] == 0


def test_dag_valid_linear_chain(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _publish_with_deps(service, workspace, "task-a")
    _publish_with_deps(service, workspace, "task-b", depends_on=("task-a",))
    _publish_with_deps(service, workspace, "task-c", depends_on=("task-b",))

    result = service.validate_dependency_dag(workspace)
    assert result["valid"] is True
    assert result["cycle_count"] == 0
    assert result["total_edges"] == 2


def test_dag_detects_cycle(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _publish_with_deps(service, workspace, "task-a", depends_on=("task-b",))
    _publish_with_deps(service, workspace, "task-b", depends_on=("task-a",))

    result = service.validate_dependency_dag(workspace)
    assert result["valid"] is False
    assert result["cycle_count"] >= 1
    assert len(result["cycles"]) >= 1


def test_dag_detects_three_node_cycle(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _publish_with_deps(service, workspace, "task-a", depends_on=("task-c",))
    _publish_with_deps(service, workspace, "task-b", depends_on=("task-a",))
    _publish_with_deps(service, workspace, "task-c", depends_on=("task-b",))

    result = service.validate_dependency_dag(workspace)
    assert result["valid"] is False
    assert result["cycle_count"] >= 1


def test_dag_orphan_depends_on(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _publish_with_deps(service, workspace, "task-a", depends_on=("nonexistent-task",))

    result = service.validate_dependency_dag(workspace)
    assert result["valid"] is False
    assert "nonexistent-task" in result["orphan_depends_on"]


def test_dag_filters_by_plan(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    # Plan-1: valid DAG.
    _publish_with_deps(service, workspace, "task-a", plan_id="plan-1")
    _publish_with_deps(service, workspace, "task-b", depends_on=("task-a",), plan_id="plan-1")

    # Plan-2: cycle.
    _publish_with_deps(service, workspace, "task-c", depends_on=("task-d",), plan_id="plan-2")
    _publish_with_deps(service, workspace, "task-d", depends_on=("task-c",), plan_id="plan-2")

    result1 = service.validate_dependency_dag(workspace, plan_id="plan-1")
    assert result1["valid"] is True

    result2 = service.validate_dependency_dag(workspace, plan_id="plan-2")
    assert result2["valid"] is False


def test_dag_empty_workspace(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    result = service.validate_dependency_dag(workspace)
    assert result["valid"] is True
    assert result["total_nodes"] == 0
