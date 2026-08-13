"""PM Dispatch Pipeline Module.

This module handles task dispatch to Director, result merging, and integration QA.
Designed to be testable with proper dependency injection.

Design invariant: this file MUST NOT contain any import of
``polaris.delivery.*`` at module level.  The local Shangshuling port is
implemented inside the Cell so the module can always be imported in
isolation.  Validated by ``tests/test_pm_dispatch_no_delivery_import.py``.
"""

from __future__ import annotations

import hashlib  # noqa: F401 — preserved as a module attribute for surface parity
import ipaddress
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _load_dispatch_submodule(_modname: str) -> Any:
    """Load a sibling ``internal.dispatch`` submodule, importable in isolation.

    Tries the normal package import first (production path). Falls back to a
    file-relative ``spec_from_file_location`` load when the parent package is a
    bare stub with no usable ``__path__`` — the exact situation set up by
    ``tests/test_pm_dispatch_no_delivery_import.py``, which loads this module by
    file path under stubbed package parents. The loaded module is registered in
    ``sys.modules`` under its full dotted name so any sibling that imports it via
    a normal ``from ...dispatch.X import Y`` statement resolves from the cache.
    """
    import importlib
    import importlib.util
    import sys

    full = f"polaris.cells.orchestration.pm_dispatch.internal.dispatch.{_modname}"
    cached = sys.modules.get(full)
    if cached is not None:
        return cached
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dispatch", f"{_modname}.py")
        spec = importlib.util.spec_from_file_location(full, path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
        return module


# Load leaf submodules first so siblings that import them (e.g. integration_qa
# imports the lazy loaders) resolve from sys.modules during bootstrap.
_lazy_imports = _load_dispatch_submodule("_lazy_imports")
_task_market_publish = _load_dispatch_submodule("task_market_publish")
_worker_pool = _load_dispatch_submodule("worker_pool")
_engine_dispatch = _load_dispatch_submodule("engine_dispatch")
_integration_qa = _load_dispatch_submodule("integration_qa")
_inline_qa_consumer = _load_dispatch_submodule("inline_qa_consumer")

# ---------------------------------------------------------------------------
# Re-export every symbol moved into the .dispatch submodules so this module's
# public AND privately-imported surface is byte-for-byte unchanged, and so the
# canonical orchestrators below resolve their moved collaborators by bare name.
# Names that tests monkeypatch on this module are intentionally NOT moved.
# ---------------------------------------------------------------------------
_get_chief_engineer_service = _lazy_imports._get_chief_engineer_service
_get_workflow_runtime = _lazy_imports._get_workflow_runtime
_get_task_market_services = _lazy_imports._get_task_market_services
_get_task_market_requeue_services = _lazy_imports._get_task_market_requeue_services
_get_task_market_revision_services = _lazy_imports._get_task_market_revision_services
_get_task_market_consumers = _lazy_imports._get_task_market_consumers
_get_shared_quality = _lazy_imports._get_shared_quality
_get_cognitive_runtime_services = _lazy_imports._get_cognitive_runtime_services
_get_io_utils = _lazy_imports._get_io_utils
_get_tasks_utils = _lazy_imports._get_tasks_utils
_get_shangshuling_port = _lazy_imports._get_shangshuling_port
_get_traceability_safety = _lazy_imports._get_traceability_safety
_TASK_MARKET_TERMINAL_STATUSES = _task_market_publish._TASK_MARKET_TERMINAL_STATUSES
_TASK_ROUTE_DIRECT_TO_DIRECTOR = _task_market_publish._TASK_ROUTE_DIRECT_TO_DIRECTOR
_TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED = _task_market_publish._TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED
_resolve_task_market_mode = _task_market_publish._resolve_task_market_mode
_resolve_task_market_rollout_mode = _task_market_publish._resolve_task_market_rollout_mode
_hash_payload = _task_market_publish._hash_payload
_build_revision_context = _task_market_publish._build_revision_context
_extract_task_dependencies = _task_market_publish._extract_task_dependencies
_task_bool = _task_market_publish._task_bool
_normalize_task_market_route = _task_market_publish._normalize_task_market_route
_task_market_route_for_task = _task_market_publish._task_market_route_for_task
_task_market_stage_for_route = _task_market_publish._task_market_stage_for_route
_task_market_lineage_snapshot = _task_market_publish._task_market_lineage_snapshot
_BACKEND_ALIVE_REASONS = _worker_pool._BACKEND_ALIVE_REASONS
_pool_trace = _worker_pool._pool_trace
_read_positive_int_env = _worker_pool._read_positive_int_env
_read_bool_env = _worker_pool._read_bool_env
_drive_role_workers = _worker_pool._drive_role_workers
_drive_director_workers = _worker_pool._drive_director_workers
_log_director_backend_distribution = _worker_pool._log_director_backend_distribution
_nop_update_role_status = _engine_dispatch._nop_update_role_status
_chief_engineer_preflight_block_reason = _engine_dispatch._chief_engineer_preflight_block_reason
_build_chief_engineer_blocked_director_result = _engine_dispatch._build_chief_engineer_blocked_director_result
_build_workflow_input = _engine_dispatch._build_workflow_input
_build_director_workflow_result = _engine_dispatch._build_director_workflow_result
_classify_integration_qa_evidence = _integration_qa._classify_integration_qa_evidence
_context_snapshot_evidence = _integration_qa._context_snapshot_evidence
_record_pm_dispatch_qa_cognitive_receipt = _integration_qa._record_pm_dispatch_qa_cognitive_receipt
_attach_pm_dispatch_qa_cognitive_receipt = _integration_qa._attach_pm_dispatch_qa_cognitive_receipt
run_integration_qa = _integration_qa.run_integration_qa
_tasks_touch_docs_only = _integration_qa._tasks_touch_docs_only
_build_post_dispatch_integration_qa_result = _integration_qa._build_post_dispatch_integration_qa_result
_apply_post_dispatch_skip_reason = _integration_qa._apply_post_dispatch_skip_reason
_resolve_verify_runner = _integration_qa._resolve_verify_runner
_execute_post_dispatch_integration_qa = _integration_qa._execute_post_dispatch_integration_qa
_integration_qa_failure_should_requeue = _integration_qa._integration_qa_failure_should_requeue
_extract_task_id = _integration_qa._extract_task_id
_is_director_assigned_task = _integration_qa._is_director_assigned_task
_string_list_from_task = _integration_qa._string_list_from_task
_string_list_from_result_field = _integration_qa._string_list_from_result_field
_build_integration_qa_last_failure = _integration_qa._build_integration_qa_last_failure
_resolve_integration_qa_requeue_target_ids = _integration_qa._resolve_integration_qa_requeue_target_ids
_requeue_director_tasks_after_integration_qa_failure = (
    _integration_qa._requeue_director_tasks_after_integration_qa_failure
)
_persist_post_dispatch_integration_qa_result = _integration_qa._persist_post_dispatch_integration_qa_result
_emit_post_dispatch_integration_qa_result = _integration_qa._emit_post_dispatch_integration_qa_result
run_post_dispatch_integration_qa = _integration_qa.run_post_dispatch_integration_qa
record_dispatch_status_to_shangshuling = _integration_qa.record_dispatch_status_to_shangshuling


@dataclass(frozen=True)
class DispatchCallbacks:
    """Cell-layer abstraction for host-layer callbacks used during dispatch.

    This dataclass severs the Cell→Host reverse-dependency that previously
    required ``run_engine_dispatch`` to receive a ``PolarisEngine`` instance.
    The delivery layer provides an implementation that delegates to the engine;
    tests provide a mock/capture implementation.

    Attributes:
        update_role_status: Called when Director status changes.
            Signature: (role, *, status, running, detail, task_id, task_title, meta)
        analysis_runner: Optional Chief Engineer analysis runner injected by
            the host layer. Keeping it here prevents argparse namespaces from
            crossing into the Cell boundary.
    """

    update_role_status: Callable[..., None] = field(default_factory=lambda: _nop_update_role_status)
    analysis_runner: Callable[..., Any] | None = None


def _endpoint_reachable(base_url: str, *, timeout: float = 4.0) -> bool:
    """True if an OpenAI-compatible ``/v1/models`` endpoint responds.

    Empty/unknown URL → True (cloud providers without this probe are not skipped).
    Any connection error / non-2xx-4xx → False (treated as offline).
    """
    if not base_url:
        return True
    import urllib.request

    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    url += "/models"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 0) or resp.getcode() or 0) < 500
    except (OSError, ValueError):  # connection refused / timeout / DNS / bad response → offline
        return False


def _base_url_requires_anonymous_probe(base_url: str, provider_type: str = "") -> bool:
    """Return true when an anonymous ``/models`` probe is a reliable health signal."""

    normalized_type = str(provider_type or "").strip().lower()
    if normalized_type in {"ollama", "local", "local_http", "openai_local", "local_openai"}:
        return True

    value = str(base_url or "").strip()
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    scheme = str(parsed.scheme or "").strip().lower()
    host = str(parsed.hostname or "").strip().lower()
    if not host:
        return False
    if scheme == "http":
        return True
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _reachable_provider_pool(pool: tuple[str, ...], *, probe_timeout: float = 3.0) -> list[str]:
    """Filter a provider pool to currently-reachable backends (skip offline endpoints).

    Probes run concurrently so re-evaluating the pool every cycle (real-time
    load-balancing over whatever LLMs are responding) costs ~one probe timeout,
    not the serial sum — a single down backend can't slow the whole cycle.
    Order of the input pool is preserved in the result.
    """
    from concurrent.futures import ThreadPoolExecutor

    from polaris.kernelone.llm.runtime_config import get_provider_base_url, get_provider_entry

    base_urls = {provider_id: get_provider_base_url(provider_id) for provider_id in pool}
    provider_types = {
        provider_id: str(get_provider_entry(provider_id).get("type") or "").strip().lower() for provider_id in pool
    }
    if not pool:
        return []
    with ThreadPoolExecutor(max_workers=max(1, len(pool))) as executor:
        probe_ids = [
            provider_id
            for provider_id in pool
            if _base_url_requires_anonymous_probe(base_urls[provider_id], provider_types.get(provider_id, ""))
        ]
        probed = dict(
            zip(
                probe_ids,
                executor.map(lambda pid: _endpoint_reachable(base_urls[pid], timeout=probe_timeout), probe_ids),
                strict=True,
            )
        )
        reachable = {provider_id: probed.get(provider_id, True) for provider_id in pool}
    live: list[str] = []
    for provider_id in pool:
        if reachable.get(provider_id):
            live.append(provider_id)
        else:
            logger.warning(
                "director pool: skipping unreachable backend %s (%s)", provider_id, base_urls[provider_id] or "?"
            )
    return live


def _build_role_worker_pool(
    role_id: str,
    consumer_factory: Callable[[str], Any],
    *,
    worker_suffix: str,
) -> list[tuple[Any, Any]]:
    """Build N market worker consumers for a ROLE, one per CONFIGURED worker slot.

    Role-agnostic + config-driven: the worker count and per-worker provider binding
    come from ``resolve_role_worker_plan(role_id)`` (the single source of truth —
    concurrency decoupled from provider count, so a role may run N workers on ONE
    provider OR N providers). ``consumer_factory(worker_id)`` builds the role's own
    consumer with the right kwargs (CE needs ``analysis_runner``, Director needs
    ``enable_safe_parallel``, QA neither) so the builder references no role-specific
    construction. Returns ``[]`` (→ the single inline consumer, unchanged) when
    concurrency<=1, the plan is single-slot, or no bound backend is reachable.
    Fail-open on any resolution error.
    """
    try:
        from polaris.kernelone.llm.runtime_config import resolve_role_worker_plan

        plan = resolve_role_worker_plan(role_id)
    except (ImportError, RuntimeError, ValueError) as exc:
        logger.debug("%s worker pool resolution failed, using single consumer: %s", role_id, exc)
        return []
    if len(plan) <= 1:
        return []
    slot_provider_ids = tuple(dict.fromkeys(str(slot.provider_id) for slot in plan if slot.provider_id))
    live_provider_ids = set(_reachable_provider_pool(slot_provider_ids))
    if not live_provider_ids:
        logger.warning("%s pool: NO bound backend reachable; falling back to single inline consumer", role_id)
        return []
    live_slots = [slot for slot in plan if str(slot.provider_id) in live_provider_ids]
    if not live_slots:
        return []
    # Preserve the configured worker COUNT (== len(plan) == concurrency) and round-robin
    # each worker over the LIVE slots: a worker whose backend went dark is reassigned to a
    # live binding (never stranded, never silently dropped) — this is what "honor the
    # configured concurrency" means, and matches the current round-robin-over-live policy.
    workers: list[tuple[Any, Any]] = []
    for idx in range(len(plan)):
        slot = live_slots[idx % len(live_slots)]
        consumer = consumer_factory(f"pm_inline_{role_id}_{worker_suffix}_w{idx}")
        workers.append((consumer, slot))
    logger.info("%s role-pool concurrency: %d worker(s) over %d live slot(s)", role_id, len(workers), len(live_slots))
    _pool_trace(
        f"POOL BUILT ({role_id}): {len(workers)} worker(s); "
        f"bindings={[(c.worker_id if hasattr(c, 'worker_id') else f'w{i}', str(slot.provider_id), getattr(slot, 'model', '')) for i, (c, slot) in enumerate(workers)]}"
    )
    return workers


def _build_director_worker_pool(
    director_consumer_type: type,
    *,
    workspace_full: str,
    worker_suffix: str,
    exec_timeout: int,
    enable_safe_parallel: bool,
) -> list[tuple[Any, Any]]:
    """Byte-identical Director facade over the role-generalized ``_build_role_worker_pool``."""

    def _director_factory(worker_id: str) -> Any:
        return director_consumer_type(
            workspace=workspace_full,
            worker_id=worker_id,
            visibility_timeout_seconds=exec_timeout,
            poll_interval=0.05,
            enable_safe_parallel=enable_safe_parallel,
        )

    return _build_role_worker_pool("director", _director_factory, worker_suffix=worker_suffix)


def _is_transient_worker_requeue_result(result: dict[str, Any]) -> bool:
    """Return True for expected transient worker results that requeued work."""
    reason = str(result.get("reason") or "").strip().lower()
    error_code = str(result.get("error_code") or "").strip().upper()
    return reason == "scope_conflict" or error_code == "SCOPE_CONFLICT"


def _build_inline_qa_consumer(
    qa_consumer_type: Any,
    *,
    workspace_full: str,
    worker_id: str,
    visibility_timeout_seconds: int,
    poll_interval: float,
) -> Any:
    # Delegated to dispatch.inline_qa_consumer (kept as a thin wrapper so the
    # canonical module surface and any call site resolve unchanged).
    return _inline_qa_consumer._build_inline_qa_consumer(
        qa_consumer_type,
        workspace_full=workspace_full,
        worker_id=worker_id,
        visibility_timeout_seconds=visibility_timeout_seconds,
        poll_interval=poll_interval,
    )


def _write_mainline_full_integration_qa_result(
    *,
    workspace_full: str,
    cache_root_full: str,
    run_id: str,
    iteration: int,
    integration_qa_result: dict[str, Any],
) -> str:
    return _inline_qa_consumer._write_mainline_full_integration_qa_result(
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        run_id=run_id,
        iteration=iteration,
        integration_qa_result=integration_qa_result,
    )


def _run_inline_task_market_consumers(
    *,
    workspace_full: str,
    run_id: str,
    iteration: int,
    published_task_ids: tuple[str, ...],
    analysis_runner: Any | None = None,
) -> dict[str, Any]:
    """Run one bounded PM->CE->Director->QA cycle for mainline-full mode."""
    rollout_mode = _resolve_task_market_rollout_mode()
    if rollout_mode != "mainline-full":
        return {
            "enabled": False,
            "rollout_mode": rollout_mode,
            "reason": "not_mainline_full",
            "ok": True,
        }

    try:
        ce_consumer_type, director_consumer_type, qa_consumer_type = _get_task_market_consumers()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "enabled": True,
            "rollout_mode": rollout_mode,
            "ok": False,
            "reason": "consumer_import_failed",
            "error": str(exc),
        }

    worker_suffix = f"{iteration}-{_hash_payload(run_id)[:8]}"
    max_cycles = _read_positive_int_env(
        "KERNELONE_TASK_MARKET_MAINLINE_FULL_MAX_CYCLES",
        default=12,
        minimum=1,
        maximum=100,
    )
    design_timeout = _read_positive_int_env(
        "KERNELONE_TASK_MARKET_DESIGN_VISIBILITY_TIMEOUT_SECONDS",
        default=900,
        minimum=30,
        maximum=7200,
    )
    exec_timeout = _read_positive_int_env(
        "KERNELONE_TASK_MARKET_EXEC_VISIBILITY_TIMEOUT_SECONDS",
        default=1800,
        minimum=30,
        maximum=7200,
    )
    qa_timeout = _read_positive_int_env(
        "KERNELONE_TASK_MARKET_QA_VISIBILITY_TIMEOUT_SECONDS",
        default=900,
        minimum=30,
        maximum=7200,
    )
    enable_safe_parallel = _read_bool_env(
        "KERNELONE_TASK_MARKET_ENABLE_SAFE_PARALLEL_DIRECTOR",
        default=False,
    )

    try:
        ce_consumer = ce_consumer_type(
            workspace=workspace_full,
            analysis_runner=analysis_runner,
            worker_id=f"pm_inline_ce_{worker_suffix}",
            visibility_timeout_seconds=design_timeout,
            poll_interval=0.05,
        )
        director_consumer = director_consumer_type(
            workspace=workspace_full,
            worker_id=f"pm_inline_director_{worker_suffix}",
            visibility_timeout_seconds=exec_timeout,
            poll_interval=0.05,
            enable_safe_parallel=enable_safe_parallel,
        )
        qa_consumer = _build_inline_qa_consumer(
            qa_consumer_type,
            workspace_full=workspace_full,
            worker_id=f"pm_inline_qa_{worker_suffix}",
            visibility_timeout_seconds=qa_timeout,
            poll_interval=0.05,
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "enabled": True,
            "rollout_mode": rollout_mode,
            "ok": False,
            "reason": "consumer_init_failed",
            "error": str(exc),
        }

    def _ce_factory(worker_id: str) -> Any:
        """Build a CE consumer for a pooled worker (role-specific kwargs: analysis_runner)."""
        return ce_consumer_type(
            workspace=workspace_full,
            analysis_runner=analysis_runner,
            worker_id=worker_id,
            visibility_timeout_seconds=design_timeout,
            poll_interval=0.05,
        )

    def _role_pool_enabled(role_id: str) -> bool:
        """A non-Director role drains via a config-driven worker pool ONLY when explicitly
        opted in (KERNELONE_TASK_MARKET_ROLE_POOLS comma list) AND its resolved plan is
        multi-worker. Default OFF, so behaviour is byte-identical until enabled per role."""
        raw = os.environ.get("KERNELONE_TASK_MARKET_ROLE_POOLS", "")
        enabled = {token.strip().lower() for token in raw.split(",") if token.strip()}
        return role_id.strip().lower() in enabled

    def _refresh_role_workers(role_id: str, consumer_factory: Callable[[str], Any]) -> list[tuple[Any, Any]]:
        """Re-probe reachability and (re)build a role's config-driven worker pool for THIS
        cycle (real-time load-balancing). Fail-open to [] (single consumer)."""
        try:
            return _build_role_worker_pool(role_id, consumer_factory, worker_suffix=worker_suffix)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("%s pool refresh failed this cycle, using single consumer: %s", role_id, exc)
            return []

    def _refresh_director_workers() -> list[tuple[Any, Any]]:
        """Re-evaluate backend reachability and (re)build the Director worker pool for
        THIS cycle, so recovered backends rejoin and dead ones drop out — real-time
        load-balancing over whatever LLMs are currently responding (per user directive
        2026-06-15). Returns [] when concurrency<=1 or nothing is reachable, in which
        case the cycle uses the single inline consumer."""
        try:
            return _build_director_worker_pool(
                director_consumer_type,
                workspace_full=workspace_full,
                worker_suffix=worker_suffix,
                exec_timeout=exec_timeout,
                enable_safe_parallel=enable_safe_parallel,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("director pool refresh failed this cycle, using single consumer: %s", exc)
            return []

    ce_results: list[dict[str, Any]] = []
    director_results: list[dict[str, Any]] = []
    qa_results: list[dict[str, Any]] = []
    terminal_status_by_task: dict[str, str] = {}
    published_ids = tuple(dict.fromkeys(task_id for task_id in published_task_ids if task_id))
    published_id_set = set(published_ids)
    lineage_drain_snapshot: dict[str, Any] = {"available": False, "reason": "not_queried"}
    cycles_ran = 0
    loop_error = ""

    for cycle_index in range(max_cycles):
        cycles_ran = cycle_index + 1
        try:
            # CE drains via its config-driven pool when opted in (default OFF → single
            # consumer, byte-identical). chief_engineer already resolves to its configured
            # concurrency (e.g. 3 on MiniMax) via resolve_role_worker_plan.
            ce_workers = (
                _refresh_role_workers("chief_engineer", _ce_factory) if _role_pool_enabled("chief_engineer") else []
            )
            ce_cycle = _drive_role_workers("chief_engineer", ce_workers) if ce_workers else ce_consumer.poll_once()
            # Real-time pool: re-probe reachable backends every cycle so the run
            # absorbs LLMs that come online mid-run and routes around ones that drop.
            director_workers = _refresh_director_workers()
            director_cycle = (
                _drive_director_workers(director_workers) if director_workers else director_consumer.poll_once()
            )
            qa_cycle = qa_consumer.poll_once()
        except Exception as exc:
            loop_error = str(exc)
            logger.exception("mainline-full inline consumer loop failed: %s", exc)
            break

        ce_results.extend(ce_cycle)
        director_results.extend(director_cycle)
        qa_results.extend(qa_cycle)

        for qa_item in qa_cycle:
            if not isinstance(qa_item, dict):
                continue
            task_id = str(qa_item.get("task_id") or "").strip()
            task_status = str(qa_item.get("status") or "").strip().lower()
            if task_id and task_status in {"resolved", "rejected", "dead_letter"}:
                terminal_status_by_task[task_id] = task_status

        if not ce_cycle and not director_cycle and not qa_cycle:
            break

        try:
            _, get_task_market_service = _get_task_market_services()
            task_market_service = get_task_market_service()
            lineage_drain_snapshot = _task_market_lineage_snapshot(
                task_market_service=task_market_service,
                workspace_full=workspace_full,
                published_id_set=published_id_set,
            )
            if bool(lineage_drain_snapshot.get("available")) and not lineage_drain_snapshot.get("open_task_ids"):
                break
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            lineage_drain_snapshot = {"available": False, "reason": "lineage_snapshot_error", "error": str(exc)}

    reconciliation_result: dict[str, Any] = {"ok": False, "reason": "not_available"}
    lineage_final_snapshot: dict[str, Any] = lineage_drain_snapshot
    try:
        _, get_task_market_service = _get_task_market_services()
        task_market_service = get_task_market_service()
        if hasattr(task_market_service, "reconcile_parent_statuses"):
            raw_result = task_market_service.reconcile_parent_statuses(workspace_full)
            if isinstance(raw_result, dict):
                reconciliation_result = {"ok": True, **raw_result}
            else:
                reconciliation_result = {"ok": True, "result": raw_result}
        # Fold post-reconcile market state into the terminal map: a
        # director-side dead-letter never reaches QA, and parents change
        # status only at reconcile — computing the report from qa_cycle
        # observations alone misfiled dead fission clusters as "unresolved"
        # instead of "rejected". Scope strictly to this dispatch's lineage
        # (published ids + their fission descendants): the market store is
        # workspace-persistent, and an unscoped fold would let one
        # historical dead-letter row fail every later run forever.
        lineage_final_snapshot = _task_market_lineage_snapshot(
            task_market_service=task_market_service,
            workspace_full=workspace_full,
            published_id_set=published_id_set,
        )
        if bool(lineage_final_snapshot.get("available")):
            for row_task_id, row_status in dict(lineage_final_snapshot.get("terminal_status_by_task") or {}).items():
                if row_task_id and row_status in _TASK_MARKET_TERMINAL_STATUSES:
                    terminal_status_by_task[row_task_id] = row_status
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        reconciliation_result = {"ok": False, "reason": "reconcile_error", "error": str(exc)}
        lineage_final_snapshot = {"available": False, "reason": "lineage_snapshot_error", "error": str(exc)}

    unresolved_ids = [task_id for task_id in published_ids if task_id not in terminal_status_by_task]
    rejected_ids = [
        task_id
        for task_id, task_status in terminal_status_by_task.items()
        if task_status in {"rejected", "dead_letter"}
    ]

    has_worker_failure = any(
        not bool(result.get("ok", False))
        for result in (ce_results + director_results + qa_results)
        if isinstance(result, dict) and not _is_transient_worker_requeue_result(result)
    )
    ok = not has_worker_failure and not unresolved_ids and not rejected_ids and not loop_error
    reason = "mainline_full_complete" if ok else "mainline_full_incomplete"
    if loop_error:
        reason = "mainline_full_loop_error"

    return {
        "enabled": True,
        "rollout_mode": rollout_mode,
        "ok": ok,
        "reason": reason,
        "max_cycles": max_cycles,
        "cycles_ran": cycles_ran,
        "published_task_ids": published_ids,
        "terminal_status_by_task": dict(terminal_status_by_task),
        "unresolved_task_ids": tuple(unresolved_ids),
        "rejected_task_ids": tuple(rejected_ids),
        "open_task_ids": tuple(lineage_final_snapshot.get("open_task_ids") or ()),
        "lineage_status_counts": dict(lineage_final_snapshot.get("status_counts") or {}),
        "ce_results": tuple(ce_results),
        "director_results": tuple(director_results),
        "qa_results": tuple(qa_results),
        "loop_error": loop_error,
        "reconciliation": reconciliation_result,
    }


def _start_durable_consumer_loops(
    *,
    workspace_full: str,
    run_id: str,
) -> dict[str, Any]:
    """Start durable consumer daemon threads for mainline-durable mode.

    PM publishes and returns immediately; background consumers handle the
    rest.  The outbox relay also runs as a daemon thread.
    """
    try:
        _, get_task_market_service = _get_task_market_services()
        service = get_task_market_service()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "enabled": True,
            "rollout_mode": "mainline-durable",
            "ok": False,
            "reason": "service_import_failed",
            "error": str(exc),
        }

    # Composition root supplies the concrete CE/Director/QA consumer classes.
    # The task_market Cell no longer imports them itself (ACGA 2.0: the
    # producer is the lower layer; runtime.task_market must not depend on
    # chief_engineer.blueprint / director.task_consumer / qa.audit_verdict).
    try:
        ce_consumer, director_consumer, qa_consumer = _get_task_market_consumers()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "enabled": True,
            "rollout_mode": "mainline-durable",
            "ok": False,
            "reason": "consumer_import_failed",
            "error": str(exc),
        }
    consumer_types: dict[str, type] = {
        "chief_engineer": ce_consumer,
        "director": director_consumer,
        "qa": qa_consumer,
    }

    try:
        started = service.start_consumer_loops(workspace_full, consumer_types=consumer_types)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "enabled": True,
            "rollout_mode": "mainline-durable",
            "ok": False,
            "reason": "consumer_start_failed",
            "error": str(exc),
        }

    status = service.query_consumer_loop_status(workspace_full)
    return {
        "enabled": True,
        "rollout_mode": "mainline-durable",
        "ok": started,
        "reason": "durable_consumers_started" if started else "durable_consumers_already_running",
        "consumer_status": status,
    }


def _mainline_publish_dispatch_tasks_to_task_market(
    *,
    workspace_full: str,
    run_id: str,
    tasks: list[dict[str, Any]],
    run_dir: str = "",
    cache_root_full: str = "",
    iteration: int = 0,
    normalized: dict[str, Any] | None = None,
    docs_stage: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Publish dispatch tasks to task market in mainline mode.

    In mainline mode, tasks are published to the design queue
    (PENDING_DESIGN) for CE consumption, not directly to execution.
    CE consumer will claim, generate blueprint, and advance to PENDING_EXEC.

    Args:
        workspace_full: Workspace path for task market operations.
        run_id: Run identifier for the dispatch session.
        tasks: List of dispatch tasks to publish.
        run_dir: Run directory for CE consumer context.
        cache_root_full: Cache root for CE consumer context.
        iteration: PM iteration number for CE consumer context.
    """
    mode = _resolve_task_market_mode()
    rollout_mode = _resolve_task_market_rollout_mode()
    if mode != "mainline":
        return []

    results: list[dict[str, Any]] = []

    try:
        publish_contract_type, get_task_market_service = _get_task_market_services()
        service = get_task_market_service()
    except (ImportError, RuntimeError, ValueError) as exc:
        logger.debug("task_market mainline publish unavailable: %s", exc)
        return results

    revision_context = _build_revision_context(
        workspace_full=workspace_full,
        run_id=run_id,
        tasks=tasks,
        normalized=normalized,
        docs_stage=docs_stage,
    )
    _sync_revision_and_change_order(
        service=service,
        workspace_full=workspace_full,
        trace_id=run_id,
        tasks=tasks,
        revision_context=revision_context,
        source_role="PM",
        docs_stage=docs_stage,
    )

    plan_task_ids = {str(entry.get("id") or "").strip() for entry in tasks if isinstance(entry, dict)}
    plan_task_ids.discard("")
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        trace_id = str(task.get("trace_id") or run_id).strip() or run_id
        task_route = _task_market_route_for_task(task)
        task_stage = _task_market_stage_for_route(task_route)
        blueprint_required = task_route == _TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED
        pm_contract_hash = _hash_payload(dict(task))
        control_plane_lineage = {
            "source": "pm_dispatch.publish",
            "contract_hash": pm_contract_hash,
            "plan_id": revision_context["plan_id"],
            "plan_revision_id": revision_context["plan_revision_id"],
            "requirement_digest": revision_context["requirement_digest"],
            "constraint_digest": revision_context["constraint_digest"],
        }
        payload: dict[str, Any] = {
            "source_pm_task_id": task_id,
            "title": str(task.get("title") or task.get("goal") or task_id).strip(),
            "goal": str(task.get("goal") or task.get("title") or "").strip(),
            "scope_paths": task.get("scope_paths") if isinstance(task.get("scope_paths"), list) else [],
            "target_files": task.get("target_files") if isinstance(task.get("target_files"), list) else [],
            "acceptance_criteria": (
                task.get("acceptance_criteria") if isinstance(task.get("acceptance_criteria"), list) else []
            ),
            "execution_checklist": (
                task.get("execution_checklist") if isinstance(task.get("execution_checklist"), list) else []
            ),
            "constraints": task.get("constraints") if isinstance(task.get("constraints"), dict) else {},
            "risks": task.get("risks") if isinstance(task.get("risks"), list) else [],
            "risk_flags": task.get("risk_flags") if isinstance(task.get("risk_flags"), list) else [],
            "qa_contract": task.get("qa_contract") if isinstance(task.get("qa_contract"), dict) else {},
            "pm_contract": dict(task),
            "contract_hash": pm_contract_hash,
            "pm_contract_hash": pm_contract_hash,
            "control_plane_lineage": control_plane_lineage,
            "task": dict(task),
            # CE consumer PreflightContext requires these runtime fields.
            "workspace": workspace_full,
            "run_dir": run_dir,
            "cache_root": cache_root_full,
            "run_id": run_id,
            "pm_iteration": iteration,
            "route": task_route,
            "task_market_route": task_route,
            "blueprint_required": blueprint_required,
            "plan_id": revision_context["plan_id"],
            "plan_revision_id": revision_context["plan_revision_id"],
        }
        try:
            command = publish_contract_type(
                workspace=workspace_full,
                trace_id=trace_id,
                run_id=run_id,
                task_id=task_id,
                stage=task_stage,
                source_role="PM",
                payload=payload,
                metadata={
                    "dispatch_mode": mode,
                    "dispatch_rollout_mode": rollout_mode,
                    "published_via": "mainline",
                    "route": task_route,
                    "task_market_route": task_route,
                    "blueprint_required": blueprint_required,
                    "plan_id": revision_context["plan_id"],
                    "plan_revision_id": revision_context["plan_revision_id"],
                },
                plan_id=revision_context["plan_id"],
                plan_revision_id=revision_context["plan_revision_id"],
                root_task_id=str(task.get("root_task_id") or task_id).strip() or task_id,
                parent_task_id=str(task.get("parent_task_id") or task.get("parent_id") or "").strip(),
                is_leaf=bool(task.get("is_leaf", True)),
                depends_on=_extract_task_dependencies(task, known_task_ids=plan_task_ids),
                requirement_digest=revision_context["requirement_digest"],
                constraint_digest=revision_context["constraint_digest"],
                change_policy=str(task.get("change_policy") or "strict").strip().lower() or "strict",
                compensation_group_id=str(task.get("compensation_group_id") or revision_context["plan_id"]).strip(),
            )
            result = service.publish_work_item(command)
            results.append(
                {
                    "task_id": task_id,
                    "ok": result.ok,
                    "status": result.status,
                    "stage": task_stage,
                    "route": task_route,
                    "reason": result.reason,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("task_market mainline publish failed: task_id=%s error=%s", task_id, exc)
            results.append(
                {
                    "task_id": task_id,
                    "ok": False,
                    "status": "",
                    "reason": str(exc),
                }
            )

    # For mainline-durable: start background consumer daemon threads after publishing.
    if rollout_mode == "mainline-durable":
        durable_result = _start_durable_consumer_loops(
            workspace_full=workspace_full,
            run_id=run_id,
        )
        logger.info(
            "mainline-durable consumer loop start: ok=%s reason=%s",
            durable_result.get("ok"),
            durable_result.get("reason"),
        )

    return results


def _publish_dispatch_tasks_to_governed_design_queue(
    *,
    workspace_full: str,
    run_id: str,
    tasks: list[dict[str, Any]],
    normalized: dict[str, Any] | None = None,
    docs_stage: dict[str, Any] | None = None,
) -> None:
    """Publish PM dispatch tasks to the governed design queue.

    PM dispatch tasks always enter ``pending_design`` with a Chief Engineer
    blueprint requirement; direct-to-Director routes are normalized before
    publish.
    """
    mode = _resolve_task_market_mode()
    rollout_mode = _resolve_task_market_rollout_mode()
    if not isinstance(tasks, list) or not tasks:
        return

    try:
        publish_contract_type, get_task_market_service = _get_task_market_services()
        service = get_task_market_service()
    except (ImportError, RuntimeError, ValueError):
        return

    revision_context = _build_revision_context(
        workspace_full=workspace_full,
        run_id=run_id,
        tasks=tasks,
        normalized=normalized,
        docs_stage=docs_stage,
    )
    _sync_revision_and_change_order(
        service=service,
        workspace_full=workspace_full,
        trace_id=run_id,
        tasks=tasks,
        revision_context=revision_context,
        source_role="PM",
        docs_stage=docs_stage,
    )

    plan_task_ids = {str(entry.get("id") or "").strip() for entry in tasks if isinstance(entry, dict)}
    plan_task_ids.discard("")
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        trace_id = str(task.get("trace_id") or run_id).strip() or run_id
        task_route = _TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED
        pm_contract_hash = _hash_payload(dict(task))
        control_plane_lineage = {
            "source": "pm_dispatch.publish",
            "contract_hash": pm_contract_hash,
            "plan_id": revision_context["plan_id"],
            "plan_revision_id": revision_context["plan_revision_id"],
            "requirement_digest": revision_context["requirement_digest"],
            "constraint_digest": revision_context["constraint_digest"],
        }
        payload: dict[str, Any] = {
            "source_pm_task_id": task_id,
            "title": str(task.get("title") or task.get("goal") or task_id).strip(),
            "goal": str(task.get("goal") or task.get("title") or "").strip(),
            "scope_paths": task.get("scope_paths") if isinstance(task.get("scope_paths"), list) else [],
            "target_files": task.get("target_files") if isinstance(task.get("target_files"), list) else [],
            "acceptance_criteria": (
                task.get("acceptance_criteria") if isinstance(task.get("acceptance_criteria"), list) else []
            ),
            "execution_checklist": (
                task.get("execution_checklist") if isinstance(task.get("execution_checklist"), list) else []
            ),
            "constraints": task.get("constraints") if isinstance(task.get("constraints"), dict) else {},
            "risks": task.get("risks") if isinstance(task.get("risks"), list) else [],
            "risk_flags": task.get("risk_flags") if isinstance(task.get("risk_flags"), list) else [],
            "qa_contract": task.get("qa_contract") if isinstance(task.get("qa_contract"), dict) else {},
            "pm_contract": dict(task),
            "contract_hash": pm_contract_hash,
            "pm_contract_hash": pm_contract_hash,
            "control_plane_lineage": control_plane_lineage,
            "task": dict(task),
            "workspace": workspace_full,
            "run_id": run_id,
            "route": task_route,
            "task_market_route": task_route,
            "blueprint_required": True,
            "plan_id": revision_context["plan_id"],
            "plan_revision_id": revision_context["plan_revision_id"],
        }
        try:
            command = publish_contract_type(
                workspace=workspace_full,
                trace_id=trace_id,
                run_id=run_id,
                task_id=task_id,
                stage="pending_design",
                source_role="PM",
                payload=payload,
                metadata={
                    "dispatch_mode": mode,
                    "dispatch_rollout_mode": rollout_mode,
                    "published_via": "task_market_contract_normalized",
                    "route": task_route,
                    "task_market_route": task_route,
                    "blueprint_required": True,
                    "plan_id": revision_context["plan_id"],
                    "plan_revision_id": revision_context["plan_revision_id"],
                },
                plan_id=revision_context["plan_id"],
                plan_revision_id=revision_context["plan_revision_id"],
                root_task_id=str(task.get("root_task_id") or task_id).strip() or task_id,
                parent_task_id=str(task.get("parent_task_id") or task.get("parent_id") or "").strip(),
                is_leaf=bool(task.get("is_leaf", True)),
                depends_on=_extract_task_dependencies(task, known_task_ids=plan_task_ids),
                requirement_digest=revision_context["requirement_digest"],
                constraint_digest=revision_context["constraint_digest"],
                change_policy=str(task.get("change_policy") or "strict").strip().lower() or "strict",
                compensation_group_id=str(task.get("compensation_group_id") or revision_context["plan_id"]).strip(),
            )
            service.publish_work_item(command)
        except (RuntimeError, ValueError) as exc:
            logger.debug("task_market shadow publish skipped: task_id=%s error=%s", task_id, exc)


def _sync_revision_and_change_order(
    *,
    service: Any,
    workspace_full: str,
    trace_id: str,
    tasks: list[dict[str, Any]],
    revision_context: dict[str, str],
    source_role: str = "PM",
    docs_stage: dict[str, Any] | None = None,
) -> None:
    """Best-effort revision registration and change-order submission."""
    if not (
        hasattr(service, "register_plan_revision")
        and hasattr(service, "query_plan_revisions")
        and hasattr(service, "submit_change_order")
    ):
        return
    try:
        register_cmd, change_order_cmd, query_cmd = _get_task_market_revision_services()
    except (ImportError, RuntimeError, ValueError):
        return

    plan_id = str(revision_context.get("plan_id") or "").strip()
    plan_revision_id = str(revision_context.get("plan_revision_id") or "").strip()
    if not plan_id or not plan_revision_id:
        return

    try:
        history = service.query_plan_revisions(query_cmd(workspace=workspace_full, plan_id=plan_id, limit=1))
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        history = ()

    latest = history[0] if history else {}
    latest_revision_id = str((latest or {}).get("plan_revision_id") or "").strip()
    if latest_revision_id == plan_revision_id:
        return

    service.register_plan_revision(
        register_cmd(
            workspace=workspace_full,
            plan_id=plan_id,
            plan_revision_id=plan_revision_id,
            parent_revision_id=latest_revision_id,
            source_role=source_role,
            requirement_digest=revision_context.get("requirement_digest", ""),
            constraint_digest=revision_context.get("constraint_digest", ""),
            metadata={"registered_via": "pm_dispatch"},
        )
    )

    if not latest_revision_id:
        return

    docs_payload = docs_stage if isinstance(docs_stage, dict) else {}
    change_type = "doc_patch" if bool(docs_payload.get("enabled")) else "manual_task_edit"
    affected_task_ids = tuple(
        str(task.get("id") or "").strip()
        for task in tasks
        if isinstance(task, dict) and str(task.get("id") or "").strip()
    )
    service.submit_change_order(
        change_order_cmd(
            workspace=workspace_full,
            plan_id=plan_id,
            from_revision_id=latest_revision_id,
            to_revision_id=plan_revision_id,
            source_role=source_role,
            change_type=change_type,
            trace_id=trace_id,
            summary="PM dispatch detected revision drift",
            affected_task_ids=affected_task_ids,
            metadata={"submitted_via": "pm_dispatch"},
        )
    )


def resolve_director_dispatch_tasks(
    *,
    workspace_full: str,
    tasks: list[dict[str, Any]],
    shangshuling_port: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve tasks ready for Director dispatch.

    Uses the shangshuling port to sync and filter tasks.  The port is loaded
    lazily from the Cell-local registry implementation when *shangshuling_port*
    is not provided, which keeps tests free of any delivery dependency.

    Args:
        workspace_full: Workspace path
        tasks: List of PM tasks
        shangshuling_port: Optional pre-injected ShangshulingPort; when None,
            the Cell-local registry port is loaded lazily.

    Returns:
        Tuple of (dispatch_tasks, metadata)
    """
    meta: dict[str, Any] = {
        "enabled": False,
        "sync_count": 0,
        "ready_count": 0,
        "selected_count": len(tasks) if isinstance(tasks, list) else 0,
    }
    if not isinstance(tasks, list) or not tasks:
        return [], meta

    port = shangshuling_port if shangshuling_port is not None else _get_shangshuling_port()

    try:
        sync_count = int(port.sync_tasks_to_shangshuling(workspace_full, tasks) or 0)
        ready_tasks = port.get_shangshuling_ready_tasks(
            workspace_full,
            limit=max(6, len(tasks) * 2),
        )
        ready_ids = {
            str(item.get("id") or "").strip()
            for item in ready_tasks
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        selected = [task for task in tasks if isinstance(task, dict) and str(task.get("id") or "").strip() in ready_ids]
        meta["enabled"] = True
        meta["sync_count"] = sync_count
        meta["ready_count"] = len(ready_tasks)
        return selected, meta
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return tasks, meta


def run_dispatch_pipeline(
    *,
    callbacks: DispatchCallbacks | None = None,
    workspace_full: str,
    cache_root_full: str,
    run_dir: str,
    run_id: str,
    iteration: int,
    normalized: dict[str, Any],
    run_events: str,
    dialogue_full: str,
    runtime_pm_tasks_full: str,
    pm_out_full: str,
    run_pm_tasks: str,
    run_director_result: str,
    docs_stage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the full dispatch pipeline.

    This is the main entry point for running the dispatch pipeline including
    chief engineer preflight, engine dispatch, and integration QA.

    Args:
        callbacks: Cell-layer callback abstraction. When None, a no-op callback is used.
            Prefer passing an explicit ``DispatchCallbacks`` over ``args``/``engine``.
        workspace_full: Workspace path
        cache_root_full: Cache root path
        run_dir: Run directory
        run_id: Run identifier
        iteration: Iteration number
        normalized: Normalized PM payload
        run_events: Events file path
        dialogue_full: Dialogue file path
        runtime_pm_tasks_full: Runtime PM tasks path
        pm_out_full: PM output path
        run_pm_tasks: Run PM tasks path
        run_director_result: Director result path
        docs_stage: Docs stage configuration

    Returns:
        Pipeline outcome dict
    """
    if callbacks is None:
        callbacks = DispatchCallbacks()

    outcome: dict[str, Any] = {
        "used": False,
        "exit_code": 0,
        "chief_engineer_result": None,
        "engine_dispatch": None,
        "integration_qa_result": None,
        "director_result": None,
        "error": "",
    }

    tasks = normalized.get("tasks") if isinstance(normalized.get("tasks"), list) else []
    dispatch_tasks, _shangshuling_dispatch_meta = resolve_director_dispatch_tasks(
        workspace_full=workspace_full,
        tasks=tasks,  # type: ignore[arg-type]  # tasks is guaranteed list after isinstance check
    )

    if not dispatch_tasks:
        outcome["error"] = "No tasks ready for dispatch"
        return outcome

    outcome["chief_engineer_result"] = run_chief_engineer_preflight(
        analysis_runner=callbacks.analysis_runner,
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        run_dir=run_dir,
        run_id=run_id,
        pm_iteration=iteration,
        tasks=dispatch_tasks,
        run_events=run_events,
        dialogue_full=dialogue_full,
    )

    preflight_block_reason = _chief_engineer_preflight_block_reason(outcome["chief_engineer_result"])
    if preflight_block_reason:
        director_result = _build_chief_engineer_blocked_director_result(
            run_id=run_id,
            task_count=len(dispatch_tasks),
            reason=preflight_block_reason,
            chief_engineer_result=outcome["chief_engineer_result"],
        )
        error = f"chief_engineer_preflight_failed: {preflight_block_reason}"
        outcome.update(
            {
                "used": True,
                "exit_code": 1,
                "error": error,
                "engine_dispatch": {
                    "skipped": True,
                    "reason": "chief_engineer_preflight_failed",
                    "preflight_reason": preflight_block_reason,
                    "task_count": len(dispatch_tasks),
                },
                "director_result": director_result,
            }
        )
        _emit_engine_dispatch_status(
            run_events=run_events,
            run_id=run_id,
            iteration=iteration,
            task_count=len(dispatch_tasks),
            name="engine_dispatch_blocked",
            summary="Engine dispatch blocked by ChiefEngineer preflight",
            ok=False,
            status="blocked",
            error=error,
        )
        return outcome

    # Traceability: register blueprint nodes per task after CE preflight
    safe_find_node, safe_link, safe_register_node = _get_traceability_safety()
    trace_service = None
    if isinstance(normalized, dict):
        trace_service = normalized.get("trace_service")
    if trace_service is not None and isinstance(outcome.get("chief_engineer_result"), dict):
        chief_engineer_result = outcome["chief_engineer_result"]
        for task in dispatch_tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("id") or "").strip()
            if not task_id:
                continue
            blueprint_id = f"bp-{run_id}-{task_id}"
            task["blueprint_id"] = blueprint_id
            bp_node = safe_register_node(
                trace_service,
                node_kind="blueprint",
                role="chief_engineer",
                external_id=blueprint_id,
                content=json.dumps(chief_engineer_result, ensure_ascii=False)[:1024],
            )
            task_node = safe_find_node(trace_service, task_id, "task")
            if task_node is None:
                task_node = safe_register_node(
                    trace_service,
                    node_kind="task",
                    role="pm",
                    external_id=task_id,
                    content=json.dumps(task, ensure_ascii=False)[:1024],
                )
            if bp_node is not None and task_node is not None:
                safe_link(trace_service, bp_node, task_node, "implements")

    # Persist minimal blueprint so traceability gate 14 can verify approval status
    for task in dispatch_tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        blueprint_id = f"bp-{run_id}-{task_id}"
        task["blueprint_id"] = blueprint_id
        target_files = (
            [str(item) for item in task.get("target_files", []) if str(item).strip()]
            if isinstance(task.get("target_files"), list)
            else []
        )
        scope_paths = (
            [str(item) for item in task.get("scope_paths", []) if str(item).strip()]
            if isinstance(task.get("scope_paths"), list)
            else []
        )
        acceptance_criteria = (
            [str(item) for item in task.get("acceptance_criteria", []) if str(item).strip()]
            if isinstance(task.get("acceptance_criteria"), list)
            else []
        )
        execution_checklist = (
            [str(item) for item in task.get("execution_checklist", []) if str(item).strip()]
            if isinstance(task.get("execution_checklist"), list)
            else []
        )
        missing_fields: list[str] = []
        if not target_files:
            missing_fields.append("target_files")
        if not acceptance_criteria:
            missing_fields.append("acceptance_criteria")
        if not execution_checklist:
            missing_fields.append("execution_checklist")
        try:
            from polaris.cells.chief_engineer.blueprint.public import BlueprintPersistence

            bp_persistence = BlueprintPersistence(workspace_full)
            bp_persistence.save(
                blueprint_id,
                {
                    "blueprint_id": blueprint_id,
                    "status": "approved",
                    "task_id": task_id,
                    "run_id": run_id,
                    "title": str(task.get("title") or task.get("goal") or task_id).strip(),
                    "objective": str(task.get("goal") or task.get("title") or "").strip(),
                    "source": "pm_dispatch.traceability_reference",
                    "traceability_only": True,
                    "target_files": list(target_files),
                    "scope_paths": list(scope_paths),
                    "acceptance_criteria": list(acceptance_criteria),
                    "execution_checklist": list(execution_checklist),
                    "dependencies": list(_extract_task_dependencies(task)),
                    "constraints": task.get("constraints") if isinstance(task.get("constraints"), dict) else {},
                    "risks": task.get("risks") if isinstance(task.get("risks"), list) else [],
                    "pm_task": dict(task),
                    "contract_completeness": {
                        "handoff_ready": not missing_fields,
                        "missing_fields": missing_fields,
                        "requires": ["target_files", "acceptance_criteria", "execution_checklist"],
                    },
                    "handoff_ready": False,
                    "created_at": datetime.now().isoformat(),
                },
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug("blueprint persistence skipped for task_id=%s", task_id)

    mode = _resolve_task_market_mode()
    rollout_mode = _resolve_task_market_rollout_mode()

    # Publish to task market based on mode.
    # Runtime PM dispatch has a single governed path:
    # PM -> Chief Engineer blueprint -> Director execution -> QA.
    # Retired off/shadow/direct modes must never fall back to engine dispatch.
    if mode == "mainline":
        mainline_results = _mainline_publish_dispatch_tasks_to_task_market(
            workspace_full=workspace_full,
            run_id=run_id,
            tasks=dispatch_tasks,
            run_dir=run_dir,
            cache_root_full=cache_root_full,
            iteration=iteration,
            normalized=normalized,
            docs_stage=docs_stage,
        )
        outcome["task_market_results"] = mainline_results
        outcome["used"] = True
        publish_failed_task_ids = tuple(
            str(item.get("task_id") or "").strip()
            for item in mainline_results
            if isinstance(item, dict) and not bool(item.get("ok", False))
        )
        if rollout_mode == "mainline-full":
            published_task_ids = tuple(
                str(task.get("id") or "").strip()
                for task in dispatch_tasks
                if isinstance(task, dict) and str(task.get("id") or "").strip()
            )
            inline_result = _run_inline_task_market_consumers(
                analysis_runner=callbacks.analysis_runner,
                workspace_full=workspace_full,
                run_id=run_id,
                iteration=iteration,
                published_task_ids=published_task_ids,
            )
            director_results = (
                list(inline_result.get("director_results", ()))
                if isinstance(inline_result.get("director_results"), tuple | list)
                else []
            )
            director_successes = sum(
                1 for item in director_results if isinstance(item, dict) and bool(item.get("ok", False))
            )
            outcome["task_market_inline"] = inline_result
            outcome["engine_dispatch"] = {
                "skipped": True,
                "reason": "task_market_mainline_full",
            }
            outcome["director_result"] = {
                "run_id": run_id,
                "mode": "task_market_mainline_full",
                "total": len(director_results),
                "successes": director_successes,
            }
            outcome["integration_qa_result"] = {
                "enabled": True,
                "ran": True,
                "passed": bool(inline_result.get("ok", False)) and not publish_failed_task_ids,
                "reason": str(inline_result.get("reason") or "task_market_mainline_full"),
                "summary": "Task-market inline CE/Director/QA flow executed",
                "qa_results": inline_result.get("qa_results", ()),
                "unresolved_task_ids": inline_result.get("unresolved_task_ids", ()),
                "rejected_task_ids": inline_result.get("rejected_task_ids", ()),
                "publish_failed_task_ids": publish_failed_task_ids,
            }
            qa_result_path = _write_mainline_full_integration_qa_result(
                workspace_full=workspace_full,
                cache_root_full=cache_root_full,
                run_id=run_id,
                iteration=iteration,
                integration_qa_result=outcome["integration_qa_result"],
            )
            if qa_result_path:
                outcome["integration_qa_result"]["result_path"] = qa_result_path
            success = bool(outcome["integration_qa_result"]["passed"])
            outcome["exit_code"] = 0 if success else 1
            outcome["error"] = "" if success else "task_market_mainline_full_failed"
            return outcome

        # In mainline mode, PM's job is done once tasks are queued for design.
        # CE consumer will claim PENDING_DESIGN and generate blueprints.
        outcome["exit_code"] = 0 if not publish_failed_task_ids else 1
        outcome["error"] = "" if outcome["exit_code"] == 0 else "task_market_publish_failed"
        return outcome

    outcome["engine_dispatch"] = {
        "skipped": True,
        "reason": "task_market_mainline_required",
        "dispatch_mode": mode,
        "dispatch_rollout_mode": rollout_mode,
    }
    outcome["director_result"] = {
        "run_id": run_id,
        "mode": "task_market_mainline_required",
        "status": "blocked",
        "total": len(dispatch_tasks),
        "successes": 0,
        "error": "task_market_mainline_required",
    }
    outcome["integration_qa_result"] = {
        "enabled": True,
        "ran": False,
        "passed": False,
        "reason": "task_market_mainline_required",
    }
    outcome["exit_code"] = 1
    outcome["error"] = "task_market_mainline_required"
    outcome["used"] = True
    return outcome


def run_chief_engineer_preflight(
    *,
    analysis_runner: Callable[..., Any] | None = None,
    workspace_full: str,
    cache_root_full: str,
    run_dir: str,
    run_id: str,
    pm_iteration: int,
    tasks: list[dict[str, Any]],
    run_events: str,
    dialogue_full: str,
) -> dict[str, Any] | None:
    """Run Chief Engineer preflight checks.

    Args:
        analysis_runner: Optional Chief Engineer analysis runner override.
        workspace_full: Workspace path
        cache_root_full: Cache root path
        run_dir: Run directory
        run_id: Run identifier
        pm_iteration: PM iteration number
        tasks: Tasks to preflight
        run_events: Runtime event log path
        dialogue_full: Dialogue log path

    Returns:
        Chief engineer result or None
    """
    _run_pre_dispatch = (
        run_pre_dispatch_chief_engineer
        if run_pre_dispatch_chief_engineer is not None
        else _get_chief_engineer_service()
    )
    return _run_pre_dispatch(
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        run_dir=run_dir,
        run_id=run_id,
        pm_iteration=pm_iteration,
        tasks=tasks,
        run_events=run_events,
        dialogue_full=dialogue_full,
        analysis_runner=analysis_runner,
    )


def _resolve_workflow_submit_fn(explicit_submit_fn: Callable[..., Any] | None) -> Callable[..., Any]:
    """Resolve the workflow submit function without import-time side effects."""
    if explicit_submit_fn is not None:
        return explicit_submit_fn
    if submit_pm_workflow_sync is not None:
        return submit_pm_workflow_sync
    return _submit_pm_workflow_sync_resolve()


def _emit_engine_dispatch_status(
    *,
    run_events: str,
    run_id: str,
    iteration: int,
    task_count: int,
    name: str,
    summary: str,
    ok: bool,
    status: str,
    error: str = "",
) -> None:
    """Emit a structured engine dispatch status event."""
    from polaris.kernelone.events import emit_event

    emit_event(
        run_events,
        kind="status",
        actor="PM",
        name=name,
        refs={"run_id": run_id, "phase": "dispatch"},
        summary=summary,
        ok=ok,
        output={
            "task_count": task_count,
            "status": status,
            "iteration": int(iteration or 0),
        },
        error=str(error or "").strip(),
    )


def run_engine_dispatch(
    *,
    callbacks: DispatchCallbacks | None = None,
    workspace_full: str,
    run_id: str,
    iteration: int,
    tasks: list[dict[str, Any]],
    run_events: str,
    dialogue_full: str,
    _submit_fn: Any | None = None,
) -> dict[str, Any]:
    """Run engine dispatch for tasks.

    Args:
        callbacks: Cell-layer callback abstraction for host-layer side-effects
            (e.g. role status updates).  The delivery layer provides an
            implementation that delegates to ``PolarisEngine.update_role_status``.
            When None, a no-op callback is used (tests / standalone use).
        workspace_full: Workspace path
        run_id: Run identifier
        iteration: Iteration number
        tasks: Tasks to dispatch
        run_events: Events file path
        dialogue_full: Dialogue file path
        _submit_fn: Optional submit function for testing; when None the module-level
            cached lazy loader is used.

    Returns:
        Dispatch result dict
    """
    if callbacks is None:
        callbacks = DispatchCallbacks()

    resolved_submit_fn = _resolve_workflow_submit_fn(_submit_fn)
    pm_workflow_input_type, workflow_submission_result_type, _ = _get_workflow_runtime_ref()

    result: dict[str, Any] = {
        "exit_code": 0,
        "director_result": None,
        "error": "",
    }

    try:
        _emit_engine_dispatch_status(
            run_events=run_events,
            run_id=run_id,
            iteration=iteration,
            task_count=len(tasks),
            name="engine_dispatch_started",
            summary="Engine dispatch started",
            ok=True,
            status="starting",
        )

        callbacks.update_role_status(
            "Director",
            status="running",
            running=True,
            detail=f"Executing {len(tasks)} tasks",
        )

        workflow_input = _build_workflow_input(
            pm_workflow_input_type,
            workspace_full=workspace_full,
            run_id=run_id,
            iteration=iteration,
            tasks=tasks,
        )
        workflow_result = resolved_submit_fn(workflow_input)
        if not isinstance(workflow_result, workflow_submission_result_type):
            result["error"] = f"Unexpected workflow result: {type(workflow_result)}"
            result["exit_code"] = 1
        else:
            result["director_result"] = _build_director_workflow_result(
                run_id=run_id,
                task_count=len(tasks),
                workflow_result=workflow_result,
            )
            if not bool(getattr(workflow_result, "submitted", False)):
                result["error"] = str(
                    getattr(workflow_result, "error", "") or getattr(workflow_result, "status", "") or ""
                ).strip()
                result["exit_code"] = 1

        _emit_engine_dispatch_status(
            run_events=run_events,
            run_id=run_id,
            iteration=iteration,
            task_count=len(tasks),
            name="engine_dispatch_completed",
            summary="Engine dispatch completed",
            ok=result["exit_code"] == 0,
            status="completed",
            error="" if result["exit_code"] == 0 else str(result.get("error") or "").strip(),
        )

    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        result["error"] = str(exc)
        result["exit_code"] = 1
        _emit_engine_dispatch_status(
            run_events=run_events,
            run_id=run_id,
            iteration=iteration,
            task_count=len(tasks),
            name="engine_dispatch_error",
            summary="Engine dispatch failed",
            ok=False,
            status="error",
            error=str(exc),
        )

    return result


def _submit_pm_workflow_sync_resolve() -> Callable:
    """Lazily resolve submit_pm_workflow_sync and cache at module level."""
    global submit_pm_workflow_sync
    if submit_pm_workflow_sync is None:
        _, _, submit_pm_workflow_sync = _get_workflow_runtime_ref()
    return submit_pm_workflow_sync


# Re-export lazy-loaded functions as module-level names so external tests can
# monkeypatch them (e.g. test_dispatch_pipeline_engine_dispatch.py).
# These are populated on first access via the lazy loaders above.
submit_pm_workflow_sync = None  # type: ignore[assignment]
"""Re-exported from _get_workflow_runtime() — replaced on first _submit_pm_workflow_sync_resolve()."""

run_pre_dispatch_chief_engineer = None  # type: ignore[assignment]
"""Re-exported from _get_chief_engineer_service() for test patchability."""


# Provide a module-level hook that tests can monkeypatch.
# run_engine_dispatch calls _get_workflow_runtime() internally; for test-patchability
# we also expose this resolver so tests patching _submit_pm_workflow_sync_resolve
# take effect.
_get_workflow_runtime_ref = _get_workflow_runtime
"""Exposed for test monkeypatching of the lazy-workflow-runtime loader."""
_get_chief_engineer_service_ref = _get_chief_engineer_service
"""Exposed for test monkeypatching of the lazy-chief-engineer loader."""


__all__ = [
    "DispatchCallbacks",
    "_mainline_publish_dispatch_tasks_to_task_market",
    "_publish_dispatch_tasks_to_governed_design_queue",
    "record_dispatch_status_to_shangshuling",
    "resolve_director_dispatch_tasks",
    "run_chief_engineer_preflight",
    "run_dispatch_pipeline",
    "run_engine_dispatch",
    "run_integration_qa",
    "run_post_dispatch_integration_qa",
]
