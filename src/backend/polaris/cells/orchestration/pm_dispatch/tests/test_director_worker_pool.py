"""Director multi-backend concurrency driver (Phase 1).

`_build_director_worker_pool` fans out N worker consumers round-robined over the
role's endpoint pool; `_drive_director_workers` runs their poll loops
concurrently, each thread bound to its assigned backend via the thread-local
provider override. The market's per-step leasing keeps the claims distinct.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline import (
    _build_director_worker_pool,
    _drive_director_workers,
)
from polaris.kernelone.llm.runtime_config import (
    RuntimeConfigManager,
    get_role_model,
    reset_runtime_config_manager,
    set_runtime_config_manager,
)

_REACHABLE = "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._endpoint_reachable"


@pytest.fixture(autouse=True)
def _all_endpoints_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default: treat every endpoint as reachable so the round-robin / drive logic is
    # tested without real network. The resilient-pool tests override this per-case.
    monkeypatch.setattr(_REACHABLE, lambda base_url, **_kw: True)


def _install_config(tmp_path: Path, director: dict[str, object]) -> None:
    config = {
        "schema_version": 2,
        "providers": {
            "prov-local": {"type": "openai_compat", "base_url": "http://localhost:8189", "model": "qwen"},
            "prov-lan": {"type": "openai_compat", "base_url": "http://192.168.1.50:8189", "model": "qwen"},
        },
        "roles": {"director": director},
    }
    path = tmp_path / "llm_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    set_runtime_config_manager(RuntimeConfigManager(config_path_resolver=lambda: str(path)))


class _FakeConsumer:
    """Records its worker_id and the provider its thread resolves at poll time."""

    instances: list[_FakeConsumer] = []

    def __init__(self, *, workspace: str, worker_id: str, visibility_timeout_seconds: int, **_kw: Any) -> None:
        self.worker_id = worker_id
        self.resolved_provider: str | None = None
        _FakeConsumer.instances.append(self)

    def poll_once(self) -> list[dict[str, Any]]:
        # Reads the thread-local override installed by the driver.
        self.resolved_provider, _model = get_role_model("director")
        return [{"task_id": f"step-{self.worker_id}", "ok": True}]


def _teardown() -> None:
    reset_runtime_config_manager()
    _FakeConsumer.instances = []


class TestBuildPool:
    def teardown_method(self) -> None:
        _teardown()

    def test_no_pool_when_concurrency_one(self, tmp_path: Path) -> None:
        _install_config(tmp_path, {"provider_id": "prov-local", "model": "qwen"})
        workers = _build_director_worker_pool(
            _FakeConsumer, workspace_full="/ws", worker_suffix="s", exec_timeout=1800, enable_safe_parallel=False
        )
        assert workers == []

    def test_round_robin_binding(self, tmp_path: Path) -> None:
        _install_config(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 3},
        )
        workers = _build_director_worker_pool(
            _FakeConsumer, workspace_full="/ws", worker_suffix="s", exec_timeout=1800, enable_safe_parallel=False
        )
        # 3 workers over 2 endpoints: local, lan, local
        assert [pid for _c, pid in workers] == ["prov-local", "prov-lan", "prov-local"]
        assert [c.worker_id for c, _p in workers] == [
            "pm_inline_director_s_w0",
            "pm_inline_director_s_w1",
            "pm_inline_director_s_w2",
        ]


class TestDriveWorkers:
    def teardown_method(self) -> None:
        _teardown()

    def test_each_worker_routes_to_its_backend_and_results_merge(self, tmp_path: Path) -> None:
        _install_config(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 2},
        )
        workers = _build_director_worker_pool(
            _FakeConsumer, workspace_full="/ws", worker_suffix="s", exec_timeout=1800, enable_safe_parallel=False
        )
        merged = _drive_director_workers(workers)
        # both workers ran, each resolved its bound endpoint inside its own thread
        assert {c.resolved_provider for c, _p in workers} == {"prov-local", "prov-lan"}
        assert len(merged) == 2
        assert {row["task_id"] for row in merged} == {
            "step-pm_inline_director_s_w0",
            "step-pm_inline_director_s_w1",
        }

    def test_worker_error_is_surfaced(self, tmp_path: Path) -> None:
        _install_config(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 2},
        )

        class _BoomConsumer(_FakeConsumer):
            def poll_once(self) -> list[dict[str, Any]]:
                raise RuntimeError("backend down")

        workers: list[tuple[Any, str]] = [
            (_BoomConsumer(workspace="/ws", worker_id="w0", visibility_timeout_seconds=1), "prov-local"),
        ]
        try:
            _drive_director_workers(workers)
            raise AssertionError("expected worker error to surface")
        except RuntimeError as exc:
            assert "backend down" in str(exc)


class TestResilientPool:
    """Multi-LLM resilience: skip offline backends, auto-adjust the worker count."""

    def teardown_method(self) -> None:
        _teardown()

    def test_skips_unreachable_backend_and_adjusts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_config(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 4},
        )
        # prov-lan (192.168.1.50) is offline; prov-local is reachable.
        monkeypatch.setattr(_REACHABLE, lambda base_url, **_kw: "192.168.1.50" not in (base_url or ""))
        workers = _build_director_worker_pool(
            _FakeConsumer, workspace_full="/ws", worker_suffix="s", exec_timeout=1800, enable_safe_parallel=False
        )
        bound = [pid for _c, pid in workers]
        assert "prov-lan" not in bound  # the offline backend is never assigned
        assert set(bound) == {"prov-local"}  # all workers routed to the live backend
        assert len(bound) == 4  # requested parallelism preserved (round-robin over live)

    def test_no_backend_reachable_falls_back_to_single(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_config(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 4},
        )
        monkeypatch.setattr(_REACHABLE, lambda base_url, **_kw: False)
        workers = _build_director_worker_pool(
            _FakeConsumer, workspace_full="/ws", worker_suffix="s", exec_timeout=1800, enable_safe_parallel=False
        )
        assert workers == []  # no live backend → single inline consumer fallback
