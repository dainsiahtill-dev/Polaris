from __future__ import annotations

from pathlib import Path

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
