from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.cells.instances.internal import service as instance_service
from polaris.kernelone.storage import workspace_key
from scripts.factory_bench import run_factory_bench
from scripts.factory_bench.run_factory_bench import _bench_project_instance_id


def test_bench_project_instance_id_uses_work_dir_without_session() -> None:
    first = _bench_project_instance_id(
        bench_session_id="",
        project_id="L1-05",
        bench_workspace=Path("/tmp/factory-bench-L1-05-r09"),
    )
    second = _bench_project_instance_id(
        bench_session_id="",
        project_id="L1-05",
        bench_workspace=Path("/tmp/factory-bench-L1-05-r10"),
    )

    assert first == "factory-bench-l1-05-r09-l1-05"
    assert second == "factory-bench-l1-05-r10-l1-05"
    assert first != second


def test_bench_project_instance_id_prefers_session_id() -> None:
    instance_id = _bench_project_instance_id(
        bench_session_id="bench-1234",
        project_id="L1-05",
        bench_workspace=Path("/tmp/factory-bench-L1-05-r09"),
    )

    assert instance_id == "bench-1234-l1-05"


def test_start_isolated_bench_project_instance_preserves_start_error(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    def fake_start_instance(_self: instance_service.InstanceSupervisor, _request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("backend identity mismatch: port 49984 serves workspace /tmp/other")

    monkeypatch.setattr(instance_service.InstanceSupervisor, "start_instance", fake_start_instance)
    monkeypatch.setattr(instance_service, "default_polaris_root", lambda: tmp_path)

    result = run_factory_bench._start_isolated_bench_project_instance(
        bench_session_id="",
        project_id="L1-08",
        project_title="纸飞机航线实验室",
        level=1,
        bench_workspace=tmp_path,
        project_workspace=str(tmp_path / "L1-08"),
        backend_token="polaris-local-dev",
    )

    assert result is not None
    assert result["ok"] is False
    assert result["error"] == "isolated_instance_start_failed"
    assert result["error_type"] == "RuntimeError"
    assert "backend identity mismatch" in result["error_detail"]


def test_runtime_project_contamination_detects_foreign_workspace_key(tmp_path: Path) -> None:
    workspace = tmp_path / "L1-03"
    projects_root = workspace / "runtime" / ".polaris" / "projects"
    current_key = workspace_key(str(workspace.resolve()))
    (projects_root / current_key / "runtime").mkdir(parents=True)
    (projects_root / "l1-02-foreign" / "runtime").mkdir(parents=True)

    contamination = run_factory_bench._runtime_project_contamination(str(workspace))

    assert contamination == ["l1-02-foreign"]
