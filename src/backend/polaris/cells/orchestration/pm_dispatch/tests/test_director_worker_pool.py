"""Director multi-backend concurrency driver (Phase 1).

`_build_director_worker_pool` fans out N worker consumers round-robined over the
role's endpoint pool; `_drive_director_workers` runs their poll loops
concurrently, each thread bound to its assigned backend via the thread-local
provider override. The market's per-step leasing keeps the claims distinct.
"""

from __future__ import annotations

import json
import threading
import time
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


def _install_raw_config(tmp_path: Path, config: dict[str, object]) -> None:
    path = tmp_path / "llm_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    set_runtime_config_manager(RuntimeConfigManager(config_path_resolver=lambda: str(path)))


class _FakeConsumer:
    """Records its worker_id and the provider its thread resolves at poll time.

    Serves exactly one step then drains to empty, mimicking a worker that claims
    its single ready leaf step and then finds the market exhausted.
    """

    instances: list[_FakeConsumer] = []

    def __init__(self, *, workspace: str, worker_id: str, visibility_timeout_seconds: int, **_kw: Any) -> None:
        self.worker_id = worker_id
        self.resolved_provider: str | None = None
        self._served = False
        _FakeConsumer.instances.append(self)

    def poll_once(self) -> list[dict[str, Any]]:
        # Reads the thread-local override installed by the driver.
        self.resolved_provider, _model = get_role_model("director")
        if self._served:
            return []  # market drained for this worker
        self._served = True
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
        assert [str(pid) for _c, pid in workers] == ["prov-local", "prov-lan", "prov-local"]
        assert [c.worker_id for c, _p in workers] == [
            "pm_inline_director_s_w0",
            "pm_inline_director_s_w1",
            "pm_inline_director_s_w2",
        ]

    def test_local_single_provider_capacity_stays_single_worker(self, tmp_path: Path) -> None:
        _install_raw_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {
                    "local": {
                        "type": "ollama",
                        "base_url": "http://127.0.0.1:11434",
                        "model": "qwen",
                    }
                },
                "roles": {
                    "director": {
                        "max_concurrency": 5,
                        "bindings": [{"provider_id": "local", "model": "qwen"}],
                    }
                },
            },
        )

        workers = _build_director_worker_pool(
            _FakeConsumer, workspace_full="/ws", worker_suffix="s", exec_timeout=1800, enable_safe_parallel=False
        )

        assert workers == []

    def test_local_explicit_capacity_builds_multiple_workers(self, tmp_path: Path) -> None:
        _install_raw_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {
                    "local": {
                        "type": "ollama",
                        "base_url": "http://127.0.0.1:11434",
                        "model": "qwen",
                        "max_concurrency": 3,
                    }
                },
                "roles": {
                    "director": {
                        "max_concurrency": 5,
                        "bindings": [{"provider_id": "local", "model": "qwen"}],
                    }
                },
            },
        )

        workers = _build_director_worker_pool(
            _FakeConsumer, workspace_full="/ws", worker_suffix="s", exec_timeout=1800, enable_safe_parallel=False
        )

        assert [str(pid) for _c, pid in workers] == ["local", "local", "local"]
        assert [getattr(pid, "slot_index", None) for _c, pid in workers] == [0, 1, 2]

    def test_same_cloud_provider_capacity_builds_multiple_workers(self, tmp_path: Path) -> None:
        _install_raw_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {"kimi": {"type": "kimi", "model": "kimi-k2", "max_concurrency": 4}},
                "roles": {
                    "director": {
                        "max_concurrency": 3,
                        "bindings": [{"provider_id": "kimi", "model": "kimi-k2"}],
                    }
                },
            },
        )

        workers = _build_director_worker_pool(
            _FakeConsumer, workspace_full="/ws", worker_suffix="s", exec_timeout=1800, enable_safe_parallel=False
        )

        assert [str(pid) for _c, pid in workers] == ["kimi", "kimi", "kimi"]
        assert [getattr(pid, "slot_index", None) for _c, pid in workers] == [0, 1, 2]


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

    def test_continuous_drain_empties_market_and_loads_fast_worker(self, tmp_path: Path) -> None:
        """All ready steps drain in ONE drive call (not one-per-worker), and the
        fast worker grabs more than the slow one — proving immediate re-poll / high load."""
        _install_config(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 2},
        )
        queue: list[str] = [f"step-{i}" for i in range(10)]
        qlock = threading.Lock()

        class _SharedMarketConsumer:
            """Workers pull from one shared queue (mimics the market); optional per-claim delay."""

            def __init__(self, *, worker_id: str, delay: float) -> None:
                self.worker_id = worker_id
                self._delay = delay
                self.claimed: list[str] = []

            def poll_once(self) -> list[dict[str, Any]]:
                with qlock:
                    item = queue.pop(0) if queue else None
                if item is None:
                    return []
                if self._delay:
                    time.sleep(self._delay)
                self.claimed.append(item)
                return [{"task_id": item, "ok": True}]

        fast = _SharedMarketConsumer(worker_id="w0", delay=0.0)
        slow = _SharedMarketConsumer(worker_id="w1", delay=0.05)
        workers: list[tuple[Any, str]] = [(fast, "prov-local"), (slow, "prov-lan")]

        merged = _drive_director_workers(workers, poll_interval=0.005)

        # The whole market (10 steps) is drained in a single drive call — the old
        # one-poll-per-worker barrier would have returned only 2.
        assert len(merged) == 10
        assert {row["task_id"] for row in merged} == {f"step-{i}" for i in range(10)}
        assert not queue  # nothing stranded
        # High load: the always-free fast worker re-polled immediately and took the bulk.
        assert len(fast.claimed) > len(slow.claimed)
        # Every step is tagged with the backend that executed it (observability).
        assert all(row.get("_director_backend") in {"prov-local", "prov-lan"} for row in merged)

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


class TestF15MidRunResilience:
    """A backend that dies MID-RUN must never freeze the pool: the dead worker
    retires itself so its steps requeue to a live worker, and a stall watchdog
    bounds the join so a worker stuck inside a hung poll_once can't freeze dispatch."""

    def teardown_method(self) -> None:
        _teardown()

    def _config(self, tmp_path: Path) -> None:
        _install_config(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 2},
        )

    def test_dead_backend_worker_retires_and_live_worker_drains(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The worker bound to the dead backend keeps getting empty-output
        (``missing_execution_evidence``) claims; after the death threshold it
        retires instead of poison-looping 256×, and the live worker drains the
        whole market. The whole drive returns in bounded time (no 6h freeze)."""
        self._config(tmp_path)
        monkeypatch.setenv("KERNELONE_DIRECTOR_WORKER_DEATH_THRESHOLD", "2")

        queue: list[str] = [f"step-{i}" for i in range(6)]
        qlock = threading.Lock()

        class _LiveConsumer:
            def __init__(self) -> None:
                self.claimed: list[str] = []

            def poll_once(self) -> list[dict[str, Any]]:
                with qlock:
                    item = queue.pop(0) if queue else None
                if item is None:
                    return []
                self.claimed.append(item)
                return [{"task_id": item, "ok": True}]

        class _DeadConsumer:
            """Every claim returns empty-output evidence (dead-backend signature)."""

            def __init__(self) -> None:
                self.polls = 0

            def poll_once(self) -> list[dict[str, Any]]:
                self.polls += 1
                return [{"task_id": "poison", "ok": False, "reason": "missing_execution_evidence"}]

        live = _LiveConsumer()
        dead = _DeadConsumer()
        workers: list[tuple[Any, str]] = [(live, "prov-local"), (dead, "prov-lan")]

        merged = _drive_director_workers(workers, poll_interval=0.005)

        # Live worker drained every real step.
        assert {row["task_id"] for row in merged if row.get("ok")} == {f"step-{i}" for i in range(6)}
        assert not queue
        # Dead worker retired at the threshold instead of looping to max_claims (256).
        assert dead.polls == 2

    def test_pool_rebuild_adapts_to_reachability_changes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Real-time load-balancing: rebuilding the pool each cycle reflects the
        CURRENT reachable set — a backend that drops is routed around, and one that
        recovers rejoins — instead of a frozen dispatch-start snapshot."""
        _install_config(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 2},
        )
        # prov-lan starts offline → only prov-local in the pool.
        reachable = {"prov-local": True, "prov-lan": False}
        monkeypatch.setattr(
            _REACHABLE,
            lambda base_url, **_kw: (
                reachable["prov-lan"] if "192.168.1.50" in (base_url or "") else reachable["prov-local"]
            ),
        )

        def _build() -> list[str]:
            workers = _build_director_worker_pool(
                _FakeConsumer, workspace_full="/ws", worker_suffix="s", exec_timeout=1800, enable_safe_parallel=False
            )
            return [str(pid) for _c, pid in workers]

        # Cycle 1: prov-lan down → all workers on prov-local.
        assert set(_build()) == {"prov-local"}
        # Cycle 2: prov-lan recovered → it rejoins the round-robin.
        reachable["prov-lan"] = True
        assert set(_build()) == {"prov-local", "prov-lan"}
        # Cycle 3: prov-local drops → routed around, run continues on prov-lan.
        reachable["prov-local"] = False
        assert set(_build()) == {"prov-lan"}

    def test_raising_worker_does_not_kill_pool_when_a_sibling_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A worker whose poll_once keeps raising (transport error) must not crash
        the whole dispatch — it retires and the live sibling's results are returned."""
        self._config(tmp_path)
        monkeypatch.setenv("KERNELONE_DIRECTOR_WORKER_DEATH_THRESHOLD", "2")

        queue: list[str] = [f"step-{i}" for i in range(4)]
        qlock = threading.Lock()

        class _LiveConsumer:
            def poll_once(self) -> list[dict[str, Any]]:
                with qlock:
                    item = queue.pop(0) if queue else None
                if item is None:
                    return []
                return [{"task_id": item, "ok": True}]

        class _BoomConsumer:
            def poll_once(self) -> list[dict[str, Any]]:
                raise RuntimeError("backend down")

        workers: list[tuple[Any, str]] = [(_LiveConsumer(), "prov-local"), (_BoomConsumer(), "prov-lan")]

        merged = _drive_director_workers(workers, poll_interval=0.005)

        # No raise: the live sibling carried the run.
        assert {row["task_id"] for row in merged if row.get("ok")} == {f"step-{i}" for i in range(4)}
        assert not queue

    def test_stall_watchdog_bounds_a_hung_poll_once(self, tmp_path: Path) -> None:
        """A worker stuck INSIDE poll_once (hung socket) never returns; the stall
        watchdog must signal stop and return the live worker's results in bounded
        time instead of joining the hung thread forever. ``stall_seconds`` is
        injected directly to exercise the watchdog below its 30s production floor."""
        self._config(tmp_path)

        queue: list[str] = [f"step-{i}" for i in range(3)]
        qlock = threading.Lock()
        release = threading.Event()

        class _LiveConsumer:
            def poll_once(self) -> list[dict[str, Any]]:
                with qlock:
                    item = queue.pop(0) if queue else None
                if item is None:
                    return []
                return [{"task_id": item, "ok": True}]

        class _HungConsumer:
            def poll_once(self) -> list[dict[str, Any]]:
                release.wait(timeout=30)  # blocks until the test releases it
                return []

        workers: list[tuple[Any, str]] = [(_LiveConsumer(), "prov-local"), (_HungConsumer(), "prov-lan")]

        start = time.monotonic()
        try:
            merged = _drive_director_workers(workers, poll_interval=0.005, stall_seconds=0.3)
        finally:
            release.set()  # let the abandoned daemon thread unwind
        elapsed = time.monotonic() - start

        assert {row["task_id"] for row in merged if row.get("ok")} == {f"step-{i}" for i in range(3)}
        assert not queue
        # Returned via the watchdog (~0.3s stall + bounded join), not after the 30s hung wait.
        assert elapsed < 10.0

    def test_active_claim_is_not_retired_by_short_claim_progress_stall(self, tmp_path: Path) -> None:
        """A long but live claimed task must not be treated as a no-progress drive
        stall just because poll_once has not returned its result yet."""
        self._config(tmp_path)

        class _SlowActiveClaimConsumer:
            def __init__(self) -> None:
                self._served = False
                self._active_started_at: float | None = None

            def active_claim_watchdog_snapshot(self) -> dict[str, Any]:
                return {
                    "task_id": "slow-live",
                    "started_monotonic": self._active_started_at,
                    "timeout_seconds": 10.0,
                }

            def poll_once(self) -> list[dict[str, Any]]:
                if self._served:
                    return []
                self._active_started_at = time.monotonic()
                time.sleep(2.3)
                self._active_started_at = None
                self._served = True
                return [{"task_id": "slow-live", "ok": True}]

        class _EmptyConsumer:
            def poll_once(self) -> list[dict[str, Any]]:
                return []

        start = time.monotonic()
        merged = _drive_director_workers(
            [(_SlowActiveClaimConsumer(), "prov-local"), (_EmptyConsumer(), "prov-lan")],
            poll_interval=0.005,
            stall_seconds=0.05,
        )
        elapsed = time.monotonic() - start

        assert {row["task_id"] for row in merged if row.get("ok")} == {"slow-live"}
        assert elapsed >= 2.0

    def test_non_resolving_claims_are_paced_to_yield_to_siblings(self, tmp_path: Path) -> None:
        """Fairness: a churning step that requeues without resolving must NOT be
        re-grabbed at full speed by the same worker (which starves idle siblings on
        other backends). Each non-resolving claim yields ~poll_interval so a sibling
        can claim the requeued step next — observable as a paced claim rate."""
        self._config(tmp_path)
        state = {"n": 0, "cap": 5}

        class _ChurnConsumer:
            def poll_once(self) -> list[dict[str, Any]]:
                if state["n"] >= state["cap"]:
                    return []
                state["n"] += 1
                # model ran (alive) but failed its target → requeues, never resolves.
                return [{"task_id": "hard", "ok": False, "reason": "step_target_missing"}]

        start = time.monotonic()
        _drive_director_workers([(_ChurnConsumer(), "prov-local")], poll_interval=0.05)
        elapsed = time.monotonic() - start
        # 5 non-resolving claims each yield ~0.05s → paced to >= ~0.2s (4 inter-claim yields).
        assert elapsed >= 0.05 * (state["cap"] - 1)

    def test_resolving_claims_are_not_paced(self, tmp_path: Path) -> None:
        """The fairness yield must NOT slow the happy path: claims that RESOLVE a step
        keep the immediate-re-poll high-load behaviour (no inter-claim sleep)."""
        self._config(tmp_path)
        state = {"n": 0, "cap": 5}

        class _ProgressConsumer:
            def poll_once(self) -> list[dict[str, Any]]:
                if state["n"] >= state["cap"]:
                    return []
                state["n"] += 1
                return [{"task_id": f"step-{state['n']}", "ok": True}]

        start = time.monotonic()
        _drive_director_workers([(_ProgressConsumer(), "prov-local")], poll_interval=0.05)
        elapsed = time.monotonic() - start
        # Resolving claims are not paced → finishes well under the paced 0.2s lower bound.
        assert elapsed < 0.05 * 2


class TestTotalFailureRaiseGate:
    """Finding 3: only a TOTAL failure (every worker errored, nothing accomplished)
    re-raises. A transient poll blip on ONE worker while the market is otherwise
    drained must NOT crash the whole PM->CE->Director->QA mainline loop."""

    def teardown_method(self) -> None:
        _teardown()

    def test_drained_market_plus_one_transient_blip_does_not_raise(self, tmp_path: Path) -> None:
        _install_config(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 2},
        )

        class _DrainedConsumer:
            """Healthy backend with nothing to claim (market drained for it)."""

            def poll_once(self) -> list[dict[str, Any]]:
                return []

        class _OneBlipConsumer:
            """Raises exactly once (transient), then drains empty — below death_threshold."""

            def __init__(self) -> None:
                self._raised = False

            def poll_once(self) -> list[dict[str, Any]]:
                if not self._raised:
                    self._raised = True
                    raise RuntimeError("transient poll blip")
                return []

        workers: list[tuple[Any, str]] = [(_DrainedConsumer(), "prov-local"), (_OneBlipConsumer(), "prov-lan")]
        # Must NOT raise: one worker drained cleanly, the other had a single blip.
        merged = _drive_director_workers(workers, poll_interval=0.005)
        assert merged == []

    def test_every_worker_errors_still_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_config(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 2},
        )
        monkeypatch.setenv("KERNELONE_DIRECTOR_WORKER_DEATH_THRESHOLD", "1")

        class _BoomConsumer:
            def poll_once(self) -> list[dict[str, Any]]:
                raise RuntimeError("backend down")

        workers: list[tuple[Any, str]] = [(_BoomConsumer(), "prov-local"), (_BoomConsumer(), "prov-lan")]
        try:
            _drive_director_workers(workers, poll_interval=0.005)
            raise AssertionError("expected total failure to surface")
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
        bound = [str(pid) for _c, pid in workers]
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
