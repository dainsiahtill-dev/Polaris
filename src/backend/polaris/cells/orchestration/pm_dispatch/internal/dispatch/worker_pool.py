"""Multi-backend Director worker-pool drive loop.

Holds the threaded drain (``_drive_role_workers`` / Director facade), its
small env-reading and trace helpers and the per-backend distribution log,
extracted verbatim from ``dispatch_pipeline.py``. The reachability probe
(``_endpoint_reachable``) and pool builders stay in the canonical module
because tests monkeypatch them there; cross-Cell imports stay in-function."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_BACKEND_ALIVE_REASONS: frozenset[str] = frozenset(
    {"step_target_missing", "repair_shrank_file", "scope_conflict", "missing_blueprint"}
)


def _pool_trace(msg: str) -> None:
    """Emit a Director-pool trace to stderr (captured by run wrappers) when
    ``KERNELONE_DIRECTOR_POOL_TRACE`` is set. INFO logs are suppressed by the
    default WARNING root level in the bench runner, so this gives reliable
    visibility into worker spawn / per-claim routing for multi-backend runs."""
    if os.environ.get("KERNELONE_DIRECTOR_POOL_TRACE"):
        import sys

        print(f"[director-pool] {msg}", file=sys.stderr, flush=True)


def _read_positive_int_env(name: str, *, default: int, minimum: int = 1, maximum: int = 3600) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def _read_bool_env(name: str, *, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _drive_role_workers(
    role_id: str,
    workers: list[tuple[Any, Any]],
    *,
    poll_interval: float = 0.05,
    max_claims_per_worker: int = 256,
    stall_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Continuously drain ready stage items across a ROLE's worker pool.

    Role-agnostic (``role_id`` keys the per-worker provider/binding override); the
    Director is just ``role_id="director"`` via the :func:`_drive_director_workers`
    wrapper. F15 retirement, fairness yield, all-idle quorum and the stall watchdog
    are unchanged and apply to any role.

    Continuously drain ready exec steps across the Director worker pool.

    Each worker runs its OWN poll loop and, the instant it finishes a step, it
    immediately tries to claim the next ready leaf step — keeping every bound
    backend at high load instead of idling at a per-cycle barrier. A worker only
    stops when it claims nothing AND every sibling is simultaneously idle
    (i.e. the market has no claimable exec step and none is mid-flight), at which
    point control returns to the caller so CE/QA can advance the pipeline (fission
    new parents, resolve steps) and unblock the next wave.

    The market's per-step leasing guarantees workers claim DISTINCT leaf steps and
    ``_exec_claim_ready`` keeps ``depends_on`` order, so parallelism is realised
    across INDEPENDENT steps while the DAG is respected. Each worker sets a
    thread-local provider override so its LLM calls route to the assigned backend;
    every returned step is tagged with that backend for observability.

    F15 resilience — a backend that dies MID-RUN must never freeze the pool: a
    worker that keeps failing against a dead/hung endpoint RETIRES itself (Layer A)
    so the steps it would otherwise monopolise requeue to a LIVE worker, and a
    stall watchdog bounds the join (Layer B) so a worker stuck *inside* a hung
    ``poll_once`` can never block the whole dispatch.

    Args:
        workers: ``(consumer, provider_id)`` pairs, one per bound backend.
        poll_interval: deprecated compatibility argument; inline workers now wait on
            TaskMarket wake events instead of timer-driven empty polls.
        max_claims_per_worker: defensive cap on claims per worker per drain so a
            pathological always-ready market can never spin forever (the outer
            cycle loop picks up any remainder).
        stall_seconds: Layer-B watchdog deadline; when None (production), read from
            ``KERNELONE_DIRECTOR_DRIVE_STALL_SECONDS`` (default 900s, floored at 30s
            so a legitimately-slow all-workers-mid-turn window never false-fires).
            Tests inject a small value directly to exercise the watchdog quickly.
    """
    import threading
    import time

    from polaris.kernelone.llm.runtime_config import (
        clear_role_provider_override,
        set_role_binding_override,
        set_role_provider_override,
    )

    death_threshold = _read_positive_int_env(
        "KERNELONE_DIRECTOR_WORKER_DEATH_THRESHOLD", default=3, minimum=1, maximum=100
    )
    stall_timeout = (
        float(stall_seconds)
        if stall_seconds is not None
        else float(
            _read_positive_int_env("KERNELONE_DIRECTOR_DRIVE_STALL_SECONDS", default=900, minimum=30, maximum=86400)
        )
    )

    results: list[list[dict[str, Any]]] = [[] for _ in workers]
    errors: list[BaseException] = []
    # Per-worker "this worker raised at least one poll exception". Only a TOTAL
    # failure (every worker errored and nothing was accomplished) re-raises to the
    # caller's loop guard; a single transient poll blip on ONE backend while the
    # market is otherwise drained must NOT crash the whole PM->CE->Director->QA
    # mainline (see the docstring's F15 contract and the raise gate below).
    errored = [False] * len(workers)
    # Per-worker "my last poll returned nothing"; all-True under the lock means the
    # whole pool is collectively idle and the drain is complete. Start False so a
    # worker never declares the pool idle before any sibling has polled once.
    # A retired (dead-backend) worker also sets its slot True so the pool can still
    # reach the all-idle quorum and terminate without it. Retirement is per-cycle:
    # the caller re-probes reachability and rebuilds the pool every cycle, so a
    # backend that recovers rejoins next cycle and a still-dead one is health-checked
    # out — no cross-cycle benching that would strand a recovered LLM.
    idle = [False] * len(workers)
    lock = threading.Lock()
    stop = threading.Event()
    wake_condition = threading.Condition()
    wake_generation = 0

    def _consumer_workspace() -> str:
        for consumer, _binding in workers:
            for attr in ("workspace", "_workspace"):
                token = str(getattr(consumer, attr, "") or "").strip()
                if token:
                    return token
        return ""

    workspace_token = _consumer_workspace()
    work_event: threading.Event | None = None
    if workspace_token:
        try:
            from polaris.cells.runtime.task_market.public.service import get_task_market_work_event

            work_event = get_task_market_work_event(workspace_token, role_id)
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug(
                "director pool wake-bus unavailable for role=%s workspace=%s: %s", role_id, workspace_token, exc
            )

    def _notify_pool() -> None:
        nonlocal wake_generation
        with wake_condition:
            wake_generation += 1
            wake_condition.notify_all()

    def _stop_pool() -> None:
        stop.set()
        if work_event is not None:
            work_event.set()
        _notify_pool()

    def _wait_for_pool_signal() -> None:
        if stop.is_set() or work_event is None:
            return
        with wake_condition:
            observed_generation = wake_generation
            wake_condition.wait_for(lambda: stop.is_set() or wake_generation != observed_generation)

    def _wait_until_deadline(deadline: float) -> None:
        remaining = max(0.0, float(deadline) - time.monotonic())
        if remaining <= 0.0:
            return
        with wake_condition:
            observed_generation = wake_generation
            wake_condition.wait_for(
                lambda: stop.is_set() or wake_generation != observed_generation,
                timeout=remaining,
            )

    def _run_wake_bridge() -> None:
        if work_event is None:
            return
        while not stop.is_set():
            work_event.wait()
            work_event.clear()
            _notify_pool()

    _pool_trace(f"DRIVE: starting {len(workers)} worker thread(s)")

    def _batch_shows_live_backend(batch: list[dict[str, Any]]) -> bool:
        """True if the claim proves the bound backend ran (a resolved step or a
        model-ran failure reason), as opposed to the empty-output dead signature."""
        for row in batch:
            if not isinstance(row, dict):
                continue
            if row.get("ok"):
                return True
            if row.get("reason") in _BACKEND_ALIVE_REASONS:
                return True
        return False

    def _binding_provider_id(binding: Any) -> str:
        return str(getattr(binding, "provider_id", binding) or "").strip()

    def _binding_model(binding: Any) -> str:
        return str(getattr(binding, "model", "") or "").strip()

    def _binding_id(binding: Any) -> str:
        return str(getattr(binding, "binding_id", "") or "").strip()

    def _binding_slot_index(binding: Any) -> int | None:
        raw = getattr(binding, "slot_index", None)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _active_claim_snapshot(consumer: Any) -> tuple[str, float, float] | None:
        snapshotter = getattr(consumer, "active_claim_watchdog_snapshot", None)
        if not callable(snapshotter):
            return None
        try:
            snapshot = snapshotter()
        except (RuntimeError, TypeError, ValueError):
            return None
        if not isinstance(snapshot, dict):
            return None
        started_raw = snapshot.get("started_monotonic")
        if not isinstance(started_raw, int | float):
            return None
        timeout_raw = snapshot.get("timeout_seconds", stall_timeout)
        if timeout_raw is None:
            timeout_raw = stall_timeout
        try:
            timeout_seconds = float(timeout_raw)
        except (TypeError, ValueError):
            timeout_seconds = stall_timeout
        return (
            str(snapshot.get("task_id") or "").strip(),
            float(started_raw),
            max(0.1, timeout_seconds),
        )

    def _retire(index: int, provider_id: str, reason: str) -> None:
        logger.warning("director worker w%d (%s) retired mid-cycle (F15): %s", index, provider_id, reason)
        _pool_trace(f"w{index} ({provider_id}) RETIRED: {reason}")
        with lock:
            idle[index] = True  # count toward all-idle quorum so the pool can still terminate
            pool_idle = all(idle)
        if pool_idle:
            _stop_pool()
        else:
            _notify_pool()

    def _run(index: int, consumer: Any, binding: Any) -> None:
        provider_id = _binding_provider_id(binding)
        model = _binding_model(binding)
        binding_id = _binding_id(binding)
        slot_index = _binding_slot_index(binding)
        if model:
            set_role_binding_override(role_id, provider_id=provider_id, model=model, binding_id=binding_id)
        else:
            set_role_provider_override(role_id, provider_id)
        backend_failures = 0
        try:
            claims = 0
            while not stop.is_set() and claims < max_claims_per_worker:
                try:
                    batch = consumer.poll_once()
                except Exception as exc:  # noqa: BLE001 — a transport error must not kill the whole pool
                    errors.append(exc)
                    errored[index] = True
                    backend_failures += 1
                    _pool_trace(f"w{index} ({provider_id}) poll raised ({backend_failures}/{death_threshold}): {exc}")
                    if backend_failures >= death_threshold:
                        _retire(index, provider_id, f"poll_exception:{exc}")
                        return
                    with lock:
                        idle[index] = True
                        pool_idle = all(idle)
                    if pool_idle:
                        _stop_pool()
                        return
                    _notify_pool()
                    _wait_for_pool_signal()
                    continue
                if batch:
                    ids = [r.get("task_id") for r in batch if isinstance(r, dict)]
                    _pool_trace(f"w{index} ({provider_id}) claimed {ids}")
                    for row in batch:
                        if isinstance(row, dict):
                            row.setdefault("_director_backend", provider_id)
                            if model:
                                row.setdefault("_director_model", model)
                            if binding_id:
                                row.setdefault("_director_binding_id", binding_id)
                            if slot_index is not None:
                                row.setdefault("_director_slot_index", slot_index)
                    claims += len(batch)
                    resolved = any(isinstance(r, dict) and r.get("ok") for r in batch)
                    batch_shows_live = _batch_shows_live_backend(batch)
                    if batch_shows_live:
                        backend_failures = 0
                    else:
                        # Empty-output / no-evidence claim: the dead-backend signature.
                        # Keep idle=False (we are NOT drained, the step just requeued) so a
                        # transient all-idle race can't terminate the pool prematurely.
                        backend_failures += 1
                        _pool_trace(
                            f"w{index} ({provider_id}) unproductive claim ({backend_failures}/{death_threshold}): {ids}"
                        )
                    with lock:
                        results[index].extend(batch)
                        idle[index] = False
                    _notify_pool()
                    if backend_failures >= death_threshold:
                        # Stop re-claiming so the requeued step(s) route to a LIVE worker.
                        _retire(index, provider_id, "backend_unproductive_streak")
                        return
                    if resolved:
                        continue  # real progress → immediately reach for the next step (high load)
                    if not batch_shows_live:
                        continue
                    # Fairness: the claim requeued WITHOUT resolving (a failing/churning step).
                    # Wake idle siblings and wait for a market/progress signal instead of
                    # sleeping on an interval; this prevents one backend from monopolising a
                    # requeued step while preserving event-driven dispatch.
                    if len(workers) > 1:
                        with lock:
                            idle[index] = True
                            pool_idle = all(idle)
                        if pool_idle:
                            _stop_pool()
                            return
                        _notify_pool()
                        _wait_for_pool_signal()
                    continue
                # Nothing ready for me right now (a genuinely empty claim = market drained for me).
                with lock:
                    idle[index] = True
                    pool_idle = all(idle)
                if pool_idle:
                    _stop_pool()  # market drained + no sibling mid-flight → yield to CE/QA
                    return
                _notify_pool()
                _wait_for_pool_signal()  # wake when a sibling or TaskMarket commit changes availability
        finally:
            clear_role_provider_override(role_id)

    threads = [
        threading.Thread(target=_run, args=(i, consumer, binding), name=f"{role_id}-w{i}", daemon=True)
        for i, (consumer, binding) in enumerate(workers)
    ]
    bridge_thread: threading.Thread | None = None
    if work_event is not None:
        bridge_thread = threading.Thread(
            target=_run_wake_bridge,
            name=f"{role_id}-task-market-wake-bridge",
            daemon=True,
        )
        bridge_thread.start()
    for thread in threads:
        thread.start()

    # Layer B backstop: bound the wait. Layer A only fires once poll_once RETURNS;
    # if a worker is stuck INSIDE a hung poll_once (a socket blocked below the HTTP
    # timeout, or an un-cancellable call) it never returns and an unbounded join
    # would freeze the whole dispatch. Watch global forward progress (claimed rows);
    # if nothing advances for stall_timeout, signal stop and abandon the stuck
    # daemon thread — its lease expires and the step requeues to a live worker.
    last_progress = 0
    last_progress_ts = time.monotonic()
    while any(thread.is_alive() for thread in threads):
        if stop.is_set():
            break
        with lock:
            progressed = sum(len(batch) for batch in results)
        now = time.monotonic()
        if progressed != last_progress:
            last_progress = progressed
            last_progress_ts = now
        else:
            active_claims = [
                (index, provider_id, snapshot)
                for index, (consumer, provider_id) in enumerate(workers)
                if (snapshot := _active_claim_snapshot(consumer)) is not None
            ]
            fresh_active_claims = [
                (index, provider_id, snapshot)
                for index, provider_id, snapshot in active_claims
                if now - snapshot[1] <= snapshot[2]
            ]
            if fresh_active_claims:
                last_progress_ts = now
                next_deadline = min(snapshot[1] + snapshot[2] for _index, _provider_id, snapshot in fresh_active_claims)
                _wait_until_deadline(next_deadline)
                continue
            if active_claims:
                index, provider_id, snapshot = max(active_claims, key=lambda item: now - item[2][1])
                task_id, started_at, timeout_seconds = snapshot
                age = now - started_at
                logger.warning(
                    "director drive active claim stalled %.0fs > %.0fs; retiring pool (F15 backstop): "
                    "worker=w%d provider=%s task_id=%s",
                    age,
                    timeout_seconds,
                    index,
                    provider_id,
                    task_id,
                )
                _pool_trace(
                    f"DRIVE STALL: active claim {task_id or '<unknown>'} on w{index} "
                    f"({provider_id}) age {age:.0f}s > {timeout_seconds:.0f}s; stopping"
                )
                _stop_pool()
                break
        if now - last_progress_ts > stall_timeout:
            logger.warning(
                "director drive stalled %.0fs with no claim progress; retiring pool (F15 backstop)",
                now - last_progress_ts,
            )
            _pool_trace(f"DRIVE STALL: no progress {now - last_progress_ts:.0f}s > {stall_timeout:.0f}s; stopping")
            _stop_pool()
            break
        _wait_until_deadline(last_progress_ts + stall_timeout)

    join_timeout = max(2.0, poll_interval * 4)
    for thread in threads:
        thread.join(timeout=join_timeout)
    _stop_pool()
    if bridge_thread is not None:
        bridge_thread.join(timeout=1.0)

    merged: list[dict[str, Any]] = []
    for batch in results:
        merged.extend(batch)
    # Only surface an error to the caller's loop guard on TOTAL failure (EVERY worker
    # errored and nothing was accomplished); a single backend blip on one worker —
    # e.g. a transient poll exception while the market is otherwise drained (zero
    # claimable work, so no ok=True rows) — must not crash a dispatch the rest of the
    # pool carried. Gating on ``all(errored)`` makes the code match the comment: a
    # raise needs both no success AND no surviving (non-erroring) worker.
    any_success = any(isinstance(row, dict) and row.get("ok") for batch in results for row in batch)
    if errors and not any_success and all(errored):
        raise errors[0]
    _log_director_backend_distribution(workers, merged)
    return merged


def _drive_director_workers(
    workers: list[tuple[Any, Any]],
    *,
    poll_interval: float = 0.05,
    max_claims_per_worker: int = 256,
    stall_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Byte-identical Director facade over the role-generalized :func:`_drive_role_workers`."""
    return _drive_role_workers(
        "director",
        workers,
        poll_interval=poll_interval,
        max_claims_per_worker=max_claims_per_worker,
        stall_seconds=stall_seconds,
    )


def _log_director_backend_distribution(workers: list[tuple[Any, Any]], merged: list[dict[str, Any]]) -> None:
    """Emit a one-line per-backend claim distribution so 4-Director load is visible."""
    if not merged:
        return
    from polaris.kernelone.llm.runtime_config import get_provider_base_url

    counts: dict[str, int] = {}
    for row in merged:
        if isinstance(row, dict):
            pid = str(row.get("_director_backend") or "?")
            counts[pid] = counts.get(pid, 0) + 1
    bound = {str(getattr(binding, "provider_id", binding) or "").strip() for _c, binding in workers}
    parts = []
    for pid in sorted(bound | set(counts)):
        try:
            host = get_provider_base_url(pid) or pid
        except (KeyError, RuntimeError, ValueError):
            host = pid
        parts.append(f"{host}={counts.get(pid, 0)}")
    logger.info("director drain: %d step(s) across %d backend(s) | %s", len(merged), len(bound), " ".join(parts))
