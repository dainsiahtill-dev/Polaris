"""Director dispatch implementation extracted from ``OrchestrationStageExecutor``.

Holds the largest instance-method cluster (Director dispatch) using the
impl-passing pattern: each function takes ``executor`` (the original ``self``)
as its first parameter so it can reach back into the class for shared state and
helper methods. Behavior is preserved verbatim.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import math
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, cast

from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM

from . import factory_stage_helpers as helpers
from .factory_role_evidence_authority import FactoryRoleEvidenceAuthorityPort
from .factory_run_completion import RunCompletionAuthority
from .factory_run_models import FactoryRun, StageResult

_PRE_DIRECTOR_SNAPSHOT_RELATIVE_DIR = ".polaris/factory_snapshots/pre_director"

logger = logging.getLogger(__name__)


# ── Deadline helpers (copied verbatim to avoid a circular import) ─────────


def _call_accepts_keyword(callable_obj: Any, keyword: str) -> bool:
    """Return whether ``callable_obj`` can accept a keyword argument."""

    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == keyword and parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            return True
    return False


def _new_monotonic_deadline(timeout_seconds: float) -> float:
    """Return one absolute deadline for a bounded Factory operation."""

    return asyncio.get_running_loop().time() + max(0.0, float(timeout_seconds))


def _remaining_monotonic_seconds(deadline: float) -> float:
    """Return non-negative wall time left in an absolute operation lease."""

    return max(0.0, float(deadline) - asyncio.get_running_loop().time())


def _whole_wait_seconds(deadline: float) -> int:
    """Return whole seconds safe to pass to an integer-timeout dependency."""

    remaining_seconds = _remaining_monotonic_seconds(deadline)
    return 0 if remaining_seconds <= 0 else math.ceil(remaining_seconds)


def _record_director_binding_skip(
    executor,
    *,
    provider_id: str,
    model: str,
    binding_id: str,
    reason: str,
) -> None:
    skip = {
        "provider_id": str(provider_id or "").strip(),
        "model": str(model or "").strip(),
        "binding_id": str(binding_id or "").strip(),
        "reason": str(reason or "").strip() or "binding_unavailable",
    }
    if not skip["provider_id"] or not skip["model"]:
        return
    skips = getattr(executor, "_last_director_binding_skips", [])
    identity = executor._director_binding_identity(skip["provider_id"], skip["model"], skip["binding_id"])
    if any(
        executor._director_binding_identity(
            str(item.get("provider_id") or ""),
            str(item.get("model") or ""),
            str(item.get("binding_id") or ""),
        )
        == identity
        for item in skips
        if isinstance(item, dict)
    ):
        return
    skips.append(skip)
    executor._last_director_binding_skips = skips


def _director_readiness_skip_reasons(executor, context: dict[str, Any] | None = None) -> dict[str, str]:
    if context is None:
        context = {}
    try:
        from polaris.bootstrap.config import Settings
        from polaris.cells.runtime.projection.public import build_llm_status
    except ImportError as exc:
        logger.debug("Director readiness skip resolution unavailable: %s", exc)
        return {}
    try:
        settings = context.get("settings") or Settings(workspace=Path(executor.workspace))
        status = build_llm_status(settings)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("Director readiness status unavailable: %s", exc)
        return {}
    roles = status.get("roles") if isinstance(status, dict) else {}
    director = roles.get("director") if isinstance(roles, dict) else {}
    skipped = director.get("skipped_bindings") if isinstance(director, dict) else None
    if not isinstance(skipped, list):
        return {}
    reasons: dict[str, str] = {}
    for item in skipped:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider_id") or "").strip()
        model = str(item.get("model") or "").strip()
        binding_id = str(item.get("binding_id") or "").strip()
        reason = str(item.get("reason") or "readiness_skipped").strip()
        readiness_source = str(item.get("readiness_source") or item.get("source") or "").strip()
        if readiness_source == "runtime_dispatch":
            continue
        if not provider_id or not model:
            continue
        reasons[executor._director_binding_identity(provider_id, model, binding_id)] = reason
        reasons.setdefault(executor._director_binding_identity(provider_id, model, ""), reason)
    return reasons


def _resolve_director_binding_fanout(executor, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
    executor._last_director_binding_skips = []
    try:
        from polaris.kernelone.llm.runtime_config import get_role_binding_slots, is_role_binding_healthy
    except (ImportError, RuntimeError) as exc:
        logger.debug("Director binding fanout resolution unavailable: %s", exc)
        return []
    try:
        slots = get_role_binding_slots("director")
    except (RuntimeError, ValueError, TypeError) as exc:
        logger.debug("Director binding slots unavailable: %s", exc)
        return []
    if len(slots) <= 1:
        return []
    readiness_skip_reasons = executor._director_readiness_skip_reasons(context)
    try:
        from polaris.cells.orchestration.pm_dispatch.public.service import reachable_provider_pool

        provider_ids = tuple(dict.fromkeys(str(slot.provider_id) for slot in slots if slot.provider_id))
        live_providers = set(reachable_provider_pool(provider_ids))
    except (ImportError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("Director provider reachability probe failed: %s", exc)
        live_providers = {str(slot.provider_id) for slot in slots if slot.provider_id}
    bindings: list[dict[str, str]] = []
    cooldown_candidates: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    def _append_binding(binding: dict[str, str]) -> None:
        key = f"{binding['provider_id']}|{binding['model']}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        bindings.append(binding)

    for slot in slots:
        pid = str(slot.provider_id or "").strip()
        model = str(slot.model or "").strip()
        binding_id = str(slot.binding_id or "").strip()
        if not pid or pid not in live_providers:
            if pid and model:
                executor._record_director_binding_skip(
                    provider_id=pid,
                    model=model,
                    binding_id=binding_id,
                    reason="provider_unreachable",
                )
            continue
        readiness_reason = readiness_skip_reasons.get(
            executor._director_binding_identity(pid, model, binding_id)
        ) or readiness_skip_reasons.get(executor._director_binding_identity(pid, model, ""))
        if readiness_reason:
            if readiness_reason == "role_binding_cooldown":
                cooldown_candidates.append(
                    {
                        "provider_id": pid,
                        "model": model,
                        "binding_id": binding_id,
                    }
                )
                continue
            executor._record_director_binding_skip(
                provider_id=pid,
                model=model,
                binding_id=binding_id,
                reason=readiness_reason,
            )
            continue
        if not is_role_binding_healthy(
            "director",
            provider_id=pid,
            model=model,
            binding_id=binding_id or None,
        ):
            cooldown_candidates.append(
                {
                    "provider_id": pid,
                    "model": model,
                    "binding_id": binding_id,
                }
            )
            continue
        _append_binding(
            {
                "provider_id": pid,
                "model": model,
                "binding_id": binding_id,
            }
        )
    if not bindings and cooldown_candidates:
        logger.warning(
            "Director binding cooldown would starve dispatch; allowing %d cooled binding(s)",
            len(cooldown_candidates),
        )
        for binding in cooldown_candidates:
            _append_binding(binding)
    else:
        for binding in cooldown_candidates:
            executor._record_director_binding_skip(
                provider_id=binding["provider_id"],
                model=binding["model"],
                binding_id=binding.get("binding_id", ""),
                reason="role_binding_cooldown",
            )
    if len(bindings) <= 1 and not getattr(executor, "_last_director_binding_skips", []):
        return []
    logger.info("Director binding fanout: %d reachable binding(s)", len(bindings))
    return bindings


async def _execute_director_binding_fanout(
    executor,
    *,
    service: Any,
    workspace: str,
    tasks: list[str] | None,
    base_options: dict[str, Any],
    bindings: list[dict[str, str]],
    timeout_seconds: int = 600,
    deadline_monotonic: float | None = None,
    cancel_event: asyncio.Event | None = None,
    abort_checker: Any = None,
    skipped_bindings: list[dict[str, Any]] | None = None,
    authority_port: FactoryRoleEvidenceAuthorityPort,
) -> CommandResult:
    execution_deadline = (
        float(deadline_monotonic) if deadline_monotonic is not None else _new_monotonic_deadline(timeout_seconds)
    )
    submitted: list[tuple[dict[str, str], CommandResult]] = []
    readiness_skipped = [dict(item) for item in list(skipped_bindings or []) if isinstance(item, dict)]
    external_readiness_skipped_count = len(readiness_skipped)

    def _binding_key(binding: dict[str, str]) -> str:
        return f"{binding['provider_id']}:{binding['model']}:{binding.get('binding_id', '')}"

    def _backend_failure_reason(result: CommandResult) -> str:
        status = str(result.status or "").strip().lower()
        if status == "timeout":
            return "timeout"
        text = " ".join(
            str(item or "")
            for item in (
                result.reason_code,
                result.message,
                (result.metadata or {}).get("error") if isinstance(result.metadata, dict) else "",
            )
        ).lower()
        backend_markers = (
            "provider_connectivity_unavailable",
            "connection refused",
            "cannot connect",
            "connect timeout",
            "read timeout",
            "timed out",
            "timeout",
            "circuit_open",
            "llm call error",
            "binding_fanout_error",
        )
        if any(marker in text for marker in backend_markers):
            return "provider_backend_failure"
        return ""

    active_bindings = []
    quarantined_skipped = []
    for binding in bindings:
        key = _binding_key(binding)
        if key in executor._quarantined_bindings:
            quarantined_skipped.append(binding)
            logger.info("Skipping quarantined binding: %s", key)
        else:
            active_bindings.append(binding)

    requested_tasks = [str(item or "").strip() for item in list(tasks or []) if str(item or "").strip()]
    partition_tasks = bool(requested_tasks) and len(active_bindings) > 1
    assigned_tasks_by_key: dict[str, list[str] | None] = {}
    submission_bindings: list[dict[str, str]] = []
    if partition_tasks:
        for idx, binding in enumerate(active_bindings):
            assigned_tasks = requested_tasks[idx :: len(active_bindings)]
            if not assigned_tasks:
                readiness_skipped.append({**binding, "reason": "no_assigned_tasks"})
                continue
            assigned_tasks_by_key[_binding_key(binding)] = assigned_tasks
            submission_bindings.append(binding)
    else:
        for binding in active_bindings:
            assigned_tasks_by_key[_binding_key(binding)] = tasks
            submission_bindings.append(binding)
    active_bindings = submission_bindings
    authority_port.require_grant_capacity("director", len(active_bindings))

    async def _run_binding(binding: dict[str, str]) -> CommandResult:
        binding_tasks = assigned_tasks_by_key.get(_binding_key(binding))
        binding_opts = dict(base_options)
        binding_opts.setdefault("llm_call_timeout_seconds", int(timeout_seconds))
        binding_opts.setdefault("director_llm_timeout_seconds", int(timeout_seconds))
        raw_binding_metadata = base_options.get("metadata")
        binding_metadata: dict[str, Any] = dict(raw_binding_metadata) if isinstance(raw_binding_metadata, dict) else {}
        binding_opts["metadata"] = {
            **binding_metadata,
            "binding_override": {
                "provider_id": binding["provider_id"],
                "model": binding["model"],
                "binding_id": binding.get("binding_id", ""),
            },
            "fanout_assigned_tasks": list(binding_tasks or []),
            "fanout_assigned_task_count": len(binding_tasks or []),
        }
        return cast(
            CommandResult,
            await executor._call_with_factory_role_evidence_authority(
                authority_port,
                "director",
                lambda: service.execute_director_run(
                    workspace=workspace,
                    tasks=binding_tasks,
                    options=binding_opts,
                ),
            ),
        )

    submission_tasks: list[asyncio.Task[CommandResult]] = []
    done_submissions: set[asyncio.Task[CommandResult]] = set()
    try:
        for binding in active_bindings:
            pending_coroutine = _run_binding(binding)
            try:
                submission_tasks.append(asyncio.create_task(pending_coroutine))
            except BaseException:
                pending_coroutine.close()
                raise
        if submission_tasks:
            done_submissions, _pending_submissions = await asyncio.wait(
                submission_tasks,
                timeout=_remaining_monotonic_seconds(execution_deadline),
            )
    finally:
        for task in submission_tasks:
            if not task.done():
                task.cancel()
        if submission_tasks:
            await asyncio.gather(*submission_tasks, return_exceptions=True)

    for idx, task in enumerate(submission_tasks):
        if task not in done_submissions:
            item: CommandResult | BaseException = CommandResult(
                run_id="",
                status="timeout",
                message="Director binding submission exceeded the execution lease",
                reason_code="DIRECTOR_SUBMISSION_TIMEOUT",
                metadata={
                    "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                    "responsible_layer": "execution_control_plane",
                    "submission_outcome_unknown": True,
                },
            )
        elif task.cancelled():
            item = RuntimeError("Director binding submission was cancelled")
        else:
            item = task.exception() or task.result()
        if isinstance(item, Exception):
            logger.warning("Director binding fanout[%d] raised: %s", idx, item)
            submitted.append(
                (
                    active_bindings[idx],
                    CommandResult(run_id="", status="failed", message=str(item), reason_code="BINDING_FANOUT_ERROR"),
                )
            )
        elif isinstance(item, CommandResult):
            submitted.append((active_bindings[idx], item))

    async def _wait_submitted_binding(
        binding: dict[str, str],
        sub_result: CommandResult,
    ) -> tuple[dict[str, str], CommandResult]:
        normalized_status = str(sub_result.status or "").strip().lower()
        if (
            normalized_status in {"failed", "cancelled", "timeout", "blocked"}
            or not str(sub_result.run_id or "").strip()
        ):
            return binding, sub_result
        wait_kwargs: dict[str, Any] = {
            "timeout_seconds": _whole_wait_seconds(execution_deadline),
            "cancel_event": cancel_event,
            "abort_checker": abort_checker,
        }
        remaining_seconds = _remaining_monotonic_seconds(execution_deadline)
        if wait_kwargs["timeout_seconds"] <= 0 or remaining_seconds <= 0:
            return binding, CommandResult(
                run_id=sub_result.run_id,
                status="timeout",
                message="Director binding execution lease expired before completion wait",
                reason_code="DIRECTOR_EXECUTION_LEASE_EXHAUSTED",
                metadata={
                    "cancel_signal_sent": False,
                    "inflight_run_continues": True,
                    "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                    "responsible_layer": "execution_control_plane",
                },
            )
        if _call_accepts_keyword(executor._wait_run_completion, "cancel_on_timeout"):
            wait_kwargs["cancel_on_timeout"] = False
        if _call_accepts_keyword(executor._wait_run_completion, "authority"):
            wait_kwargs["authority"] = RunCompletionAuthority.TASK_RUNTIME_EXECUTION_FACT
        try:
            return binding, await asyncio.wait_for(
                executor._wait_run_completion(
                    service,
                    sub_result,
                    **wait_kwargs,
                ),
                timeout=remaining_seconds,
            )
        except TimeoutError:
            return binding, CommandResult(
                run_id=sub_result.run_id,
                status="timeout",
                message="Director binding completion wait exceeded the execution lease",
                reason_code="DIRECTOR_EXECUTION_LEASE_EXHAUSTED",
                metadata={
                    "cancel_signal_sent": False,
                    "inflight_run_continues": True,
                    "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                    "responsible_layer": "execution_control_plane",
                },
            )
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            logger.warning("Director binding fanout wait failed for run %s: %s", sub_result.run_id, exc)
            return binding, CommandResult(run_id=sub_result.run_id, status="failed", message=f"Wait failed: {exc}")

    final_results: list[tuple[dict[str, str], CommandResult]] = list(
        await asyncio.gather(*[_wait_submitted_binding(binding, sub_result) for binding, sub_result in submitted])
    )

    quarantine_threshold = executor._director_binding_timeout_quarantine_count()
    for binding, result in final_results:
        key = _binding_key(binding)
        if str(result.status or "").strip().lower() == "timeout":
            executor._binding_timeout_counts[key] = executor._binding_timeout_counts.get(key, 0) + 1
            if executor._binding_timeout_counts[key] >= quarantine_threshold:
                executor._quarantined_bindings.add(key)
                logger.warning(
                    "Quarantining binding %s after %d consecutive timeouts",
                    key,
                    executor._binding_timeout_counts[key],
                )
        else:
            executor._binding_timeout_counts[key] = 0
        backend_failure_reason = _backend_failure_reason(result)
        if backend_failure_reason:
            with contextlib.suppress(ImportError, RuntimeError, TypeError, ValueError):
                from polaris.kernelone.llm.runtime_config import mark_role_binding_unhealthy

                mark_role_binding_unhealthy(
                    "director",
                    provider_id=binding["provider_id"],
                    model=binding["model"],
                    binding_id=binding.get("binding_id") or None,
                )

    per_binding: list[dict[str, Any]] = []
    success_count = 0
    fail_count = 0
    first_run_id = ""
    for binding, result in final_results:
        if not first_run_id and result.run_id:
            first_run_id = result.run_id
        status = str(result.status or "").strip().lower()
        if status in {"completed", "success"}:
            success_count += 1
        else:
            fail_count += 1
        key = _binding_key(binding)
        entry: dict[str, Any] = {
            "provider_id": binding["provider_id"],
            "model": binding["model"],
            "binding_id": binding.get("binding_id", ""),
            "run_id": result.run_id or "",
            "status": result.status or "unknown",
            "message": result.message or "",
        }
        result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
        entry_assigned_tasks = assigned_tasks_by_key.get(key)
        if entry_assigned_tasks is not None:
            entry["assigned_tasks"] = list(entry_assigned_tasks)
            entry["assigned_task_count"] = len(entry_assigned_tasks)
        for evidence_key in (
            "cancel_signal_sent",
            "cancel_reason",
            "inflight_run_continues",
            "terminal_source",
            "queried_status",
            "timeout_settle_grace_seconds",
            "active_task_count",
            "active_task_ids",
        ):
            if evidence_key in result_metadata:
                entry[evidence_key] = result_metadata[evidence_key]
        if status == "timeout":
            entry["timeout_count"] = executor._binding_timeout_counts.get(key, 0)
            if key in executor._quarantined_bindings:
                entry["quarantined"] = True
                entry["quarantine_reason"] = "consecutive_timeout"
        backend_failure_reason = _backend_failure_reason(result)
        if backend_failure_reason:
            entry["backend_failure_reason"] = backend_failure_reason
        per_binding.append(entry)

    for binding in quarantined_skipped:
        key = _binding_key(binding)
        per_binding.append(
            {
                "provider_id": binding["provider_id"],
                "model": binding["model"],
                "binding_id": binding.get("binding_id", ""),
                "run_id": "",
                "status": "quarantined",
                "message": "Skipped due to consecutive timeouts",
                "quarantined": True,
                "quarantine_reason": "consecutive_timeout",
                "timeout_count": executor._binding_timeout_counts.get(key, 0),
            }
        )

    for binding in readiness_skipped:
        provider_id = str(binding.get("provider_id") or "").strip()
        model = str(binding.get("model") or "").strip()
        binding_id = str(binding.get("binding_id") or "").strip()
        if not provider_id or not model:
            continue
        per_binding.append(
            {
                "provider_id": provider_id,
                "model": model,
                "binding_id": binding_id,
                "run_id": "",
                "status": "skipped",
                "message": "Skipped by Director binding readiness filter",
                "skipped": True,
                "skip_reason": str(binding.get("reason") or "binding_unavailable").strip(),
                "assigned_tasks": [],
                "assigned_task_count": 0,
            }
        )

    quarantined_count = sum(1 for entry in per_binding if entry.get("quarantined"))
    skipped_count = len(quarantined_skipped)
    readiness_skipped_count = sum(1 for entry in per_binding if entry.get("skipped") and not entry.get("quarantined"))
    merged_status = "completed" if success_count > 0 and fail_count == 0 else "failed"
    total_binding_count = len(bindings) + external_readiness_skipped_count
    return CommandResult(
        run_id=first_run_id,
        status=merged_status,
        message=(
            f"Director binding fanout: {total_binding_count} bindings, {success_count} succeeded, "
            f"{fail_count} failed, {quarantined_count} quarantined, "
            f"{readiness_skipped_count} readiness-skipped"
        ),
        metadata={
            "binding_fanout": True,
            "binding_count": total_binding_count,
            "active_binding_count": len(active_bindings),
            "quarantined_binding_count": quarantined_count,
            "quarantined_skipped_count": skipped_count,
            "timeout_quarantine_threshold": quarantine_threshold,
            "readiness_skipped_count": readiness_skipped_count,
            "per_binding": per_binding,
            "task_assignment_mode": "partitioned" if partition_tasks else "shared",
            "requested_task_ids": requested_tasks,
            "execution_mode": str(base_options.get("execution_mode", "")).strip(),
            "max_workers": int(base_options.get("max_workers", 0)),
        },
    )


async def _execute_director_dispatch(executor, run: FactoryRun, context: dict[str, Any]) -> StageResult:
    logger.info("Executing Director dispatch for run %s", run.id)
    abort_checker = executor._resolve_abort_checker(context)
    authority_port = executor._factory_role_evidence_cutoff_port(context)

    synced_plan_source = executor._ensure_pm_plan_contract_available()
    executor._enrich_pm_plan_contract_artifact("tasks/plan.json")
    pm_tasks = executor._load_pm_plan_tasks("tasks/plan.json")
    plan_task_filter = executor._build_director_task_filter(pm_tasks)
    configured_task_filter = str(context.get("task_filter") or "").strip()
    effective_task_filter = configured_task_filter or plan_task_filter
    requested_task_ids = executor._director_requested_task_ids(context, pm_tasks)

    service = executor._build_orchestration_service(context)
    stage_signals: list[dict[str, Any]] = []
    if synced_plan_source:
        stage_signals.append(
            {
                "code": "director.plan_contract_synced_from_workspace_mirror",
                "severity": "info",
                "detail": "Copied PM workspace plan mirror into runtime tasks/plan.json before Director dispatch.",
                "source_path": synced_plan_source,
            }
        )
    if pm_tasks:
        materialize_summary = executor._materialize_pm_plan_taskboard(
            pm_tasks,
            run_id=run.id,
            source_stage="director_dispatch",
            run_metadata=run.metadata,
        )
        binding_failures = list(materialize_summary.get("binding_failures") or [])
        if binding_failures:
            stage_signals.append(
                {
                    "code": "director.task_runtime_factory_binding_failed",
                    "severity": "error",
                    "detail": "TaskRuntime rejected one or more Factory run bindings before Director dispatch.",
                    **materialize_summary,
                }
            )
            signal_artifact = executor._write_stage_signal_artifact(
                stage="director_dispatch",
                run_id=run.id,
                signals=stage_signals,
            )
            return StageResult(
                stage="director_dispatch",
                status="failed",
                output=("Director dispatch blocked before LLM execution: TaskRuntime Factory run binding failed"),
                artifacts=[signal_artifact],
            )
        if int(materialize_summary.get("created_count") or 0) > 0:
            stage_signals.append(
                {
                    "code": "director.taskboard_materialized_from_plan",
                    "severity": "info",
                    "detail": "Materialized missing PM plan tasks into TaskBoard before Director dispatch.",
                    **materialize_summary,
                }
            )
    snapshot_signals: list[dict[str, Any]] = []
    raw_start_metadata = context.get("metadata")
    start_metadata: dict[str, Any] = dict(raw_start_metadata) if isinstance(raw_start_metadata, dict) else {}
    start_from_hint = str(context.get("factory_start_from") or start_metadata.get("factory_start_from") or "")
    director_only_resume = start_from_hint.strip().lower() == "director_resume"
    if director_only_resume:
        preserved_state = executor._capture_workspace_delivery_state()
        snapshot_signals.append(
            {
                "code": "director.resume_workspace_preserved",
                "severity": "info",
                "detail": (
                    "Preserved current delivery files for Director-local recovery; "
                    "completed task artifacts are not rolled back or regenerated"
                ),
                "preserved_delivery_file_count": len(preserved_state),
            }
        )
    else:
        try:
            snapshot_payload = executor._create_pre_director_snapshot(run_id=run.id)
            snapshot_signals.append(
                {
                    "code": "director.pre_director_snapshot_created",
                    "severity": "info",
                    "detail": "Captured workspace delivery-file snapshot before Director dispatch",
                    "file_count": snapshot_payload.get("file_count"),
                    "snapshot_path": _PRE_DIRECTOR_SNAPSHOT_RELATIVE_DIR,
                }
            )
        except (OSError, RuntimeError, ValueError) as exc:
            stage_signals.append(
                {
                    "code": "director.pre_director_snapshot_create_failed",
                    "severity": "error",
                    "detail": str(exc),
                }
            )
    initial_stats = executor._read_taskboard_stats()
    _observable_rows, task_runtime_projection_failure = executor._query_observable_task_rows(factory_run_id=run.id)
    if task_runtime_projection_failure is not None:
        stage_signals.append(task_runtime_projection_failure)
    attempts: list[dict[str, Any]] = []
    last_command_result: CommandResult | None = None
    last_director_execution_deadline_monotonic: float | None = None
    final_result: CommandResult | None = None
    max_rounds = int(context.get("director_max_rounds") or 0)
    if max_rounds <= 0:
        active_rounds = (
            int(initial_stats.get("pending") or 0)
            + int(initial_stats.get("ready") or 0)
            + int(initial_stats.get("in_progress") or 0)
            + 2
        )
        total_rounds = int(initial_stats.get("total") or 0) + 2
        dynamic_rounds = max(active_rounds, total_rounds)
        max_rounds = max(2, min(dynamic_rounds, 12))
    idle_budget = max(1, int(context.get("director_idle_budget") or 2))
    idle_rounds = 0
    requires_taskboard_convergence = True
    execution_barrier_timeout_observed = False

    # Enforce mainline-full: no silent single-worker fallback
    execution_mode = str(context.get("execution_mode", "parallel")).strip().lower()
    if execution_mode not in ("parallel", "serial"):
        stage_signals.append(
            {
                "code": "director.invalid_execution_mode",
                "severity": "error",
                "detail": f"Invalid execution_mode: {execution_mode}; must be 'parallel' or 'serial'",
            }
        )
        execution_mode = "parallel"

    # Enforce worker count matches configured bindings
    max_workers = int(context.get("max_workers", DEFAULT_DIRECTOR_MAX_PARALLELISM))
    if max_workers < 1:
        stage_signals.append(
            {
                "code": "director.invalid_worker_count",
                "severity": "error",
                "detail": f"Invalid max_workers: {max_workers}; must be >= 1",
            }
        )
        max_workers = DEFAULT_DIRECTOR_MAX_PARALLELISM

    if not pm_tasks:
        stage_signals.append(
            {
                "code": "director.task_lineage_missing",
                "severity": "error",
                "detail": "tasks/plan.json missing or empty tasks array",
            }
        )
    if int(initial_stats.get("total") or 0) <= 0:
        stage_signals.append(
            {
                "code": "director.taskboard_empty",
                "severity": "error",
                "detail": "TaskBoard has no executable task records",
            }
        )
    stage_signals.extend(
        executor._chief_engineer_handoff_signals_for_director(
            pm_tasks,
            run_id=run.id,
        )
    )

    if not any(str(item.get("severity") or "").strip().lower() == "error" for item in stage_signals):
        director_binding_fanout = executor._resolve_director_binding_fanout(context)
        director_binding_skips = list(getattr(executor, "_last_director_binding_skips", []))

        for round_index in range(1, max_rounds + 1):
            before_stats = executor._read_taskboard_stats()
            workspace_state_before = executor._capture_workspace_delivery_state()
            if executor._is_taskboard_converged(before_stats):
                stage_signals.append(
                    {
                        "code": "director.already_converged",
                        "severity": "info",
                        "detail": "TaskBoard already converged before dispatch round",
                        "round": round_index,
                    }
                )
                final_result = CommandResult(
                    run_id="",
                    status="completed",
                    message="TaskBoard already converged",
                )
                break

            raw_context_metadata = context.get("metadata")
            context_metadata: dict[str, Any] = (
                dict(raw_context_metadata) if isinstance(raw_context_metadata, dict) else {}
            )
            base_options: dict[str, Any] = {
                "task_filter": effective_task_filter,
                "max_workers": max_workers,
                "execution_mode": execution_mode,
                "dispatch_mode": "mainline-full",
                "metadata": {
                    **context_metadata,
                    "factory_run_id": str(context.get("factory_run_id") or run.id or "").strip(),
                    "factory_stage": "director_dispatch",
                    "director_binding_skips": director_binding_skips,
                },
            }
            missing_declared_targets = executor._missing_declared_delivery_targets(pm_tasks)
            materialization_pending = bool(missing_declared_targets)
            first_materialization_pending = (
                materialization_pending and not attempts and int(before_stats.get("completed") or 0) == 0
            )
            remaining_task_count = executor._remaining_director_task_count(
                before_stats,
                fallback=len(pm_tasks),
            )
            dependency_schedule = executor._director_dependency_schedule(
                pm_tasks,
                factory_run_id=run.id,
            )
            critical_path_task_count = max(1, dependency_schedule.critical_path_task_count)
            requested_director_dispatch_timeout_seconds = executor._director_dispatch_timeout_seconds(
                context,
                task_count=critical_path_task_count,
                materialization_pending=materialization_pending,
            )
            admission_decision = executor._director_dispatch_deadline_admission_decision(
                context,
                requested_timeout_seconds=requested_director_dispatch_timeout_seconds,
                first_materialization_pending=first_materialization_pending,
                materialization_pending=materialization_pending,
                dependency_schedule=dependency_schedule,
            )
            admission_payload = admission_decision.to_dict()
            if not admission_decision.executable:
                error_code, error_detail, result_status, result_message = (
                    executor._director_admission_failure_projection(admission_decision)
                )
                no_active_tasks = (
                    str(admission_decision.reason or "").strip() == "no_active_director_tasks"
                    and result_status == "completed"
                )
                signal_payload: dict[str, Any] = {
                    "code": error_code,
                    "severity": "info" if no_active_tasks else "error",
                    "detail": error_detail,
                    "round": round_index,
                    "responsible_layer": "execution_control_plane",
                    "repairable_by_director": False,
                    "requires_ce_replan": False,
                    "requires_pm_revision": False,
                    **admission_payload,
                }
                if not no_active_tasks:
                    signal_payload["failure_class"] = FailureClassV1.TASKBOARD_DEADLOCK.value
                stage_signals.append(signal_payload)
                final_result = CommandResult(
                    run_id="",
                    status=result_status,
                    message=result_message,
                    metadata={
                        "deadline_admission": admission_payload,
                    },
                )
                break
            base_options["metadata"].update(
                {
                    "director_dispatch_timeout_seconds": admission_decision.timeout_seconds,
                    "director_dispatch_execution_timeout_seconds": (admission_decision.execution_timeout_seconds),
                    "director_dispatch_settlement_timeout_seconds": (admission_decision.settlement_timeout_seconds),
                    "director_dispatch_requested_timeout_seconds": (requested_director_dispatch_timeout_seconds),
                    "director_deadline_admission": admission_payload,
                    "director_first_materialization_pending": first_materialization_pending,
                    "director_remaining_task_count": remaining_task_count,
                    "director_critical_path_task_count": critical_path_task_count,
                    "director_missing_declared_target_count": len(missing_declared_targets),
                    "director_missing_declared_target_sample": missing_declared_targets[:12],
                }
            )
            director_lease_timeout_seconds = admission_decision.timeout_seconds
            director_execution_timeout_seconds = admission_decision.execution_timeout_seconds
            director_settlement_timeout_seconds = admission_decision.settlement_timeout_seconds
            requested_llm_timeout = int(context.get("llm_call_timeout_seconds") or director_execution_timeout_seconds)
            requested_director_timeout = int(
                context.get("director_llm_timeout_seconds")
                or context.get("llm_call_timeout_seconds")
                or director_execution_timeout_seconds
            )
            admitted_timeout_seconds = director_execution_timeout_seconds
            base_options["llm_call_timeout_seconds"] = min(requested_llm_timeout, admitted_timeout_seconds)
            base_options["director_llm_timeout_seconds"] = min(
                requested_director_timeout,
                admitted_timeout_seconds,
            )
            base_options["metadata"].update(
                {
                    "llm_call_timeout_seconds": base_options["llm_call_timeout_seconds"],
                    "director_llm_timeout_seconds": base_options["director_llm_timeout_seconds"],
                    "request_timeout_seconds": base_options["llm_call_timeout_seconds"],
                    "timeout_seconds": base_options["llm_call_timeout_seconds"],
                }
            )
            claim_kwargs: dict[str, Any] = {
                "limit": max_workers,
                "factory_run_id": run.id,
            }
            if _call_accepts_keyword(executor._read_claimable_director_task_ids, "allowed_task_ids"):
                claim_kwargs["allowed_task_ids"] = (
                    dependency_schedule.waves[0] if dependency_schedule.valid and dependency_schedule.waves else ()
                )
            round_requested_task_ids = executor._read_claimable_director_task_ids(**claim_kwargs)
            base_options["metadata"]["director_admitted_wave_task_ids"] = list(
                dependency_schedule.waves[0] if dependency_schedule.valid and dependency_schedule.waves else ()
            )
            if not round_requested_task_ids and attempts:
                inflight_run_id = str((last_command_result.run_id if last_command_result else "") or "").strip()
                active_execution_observed = bool(
                    inflight_run_id
                    and (
                        executor._active_director_execution_progress_marker(run_id=inflight_run_id)
                        or executor._taskboard_has_active_execution(before_stats)
                    )
                )
                carried_execution_lease_seconds = (
                    _whole_wait_seconds(last_director_execution_deadline_monotonic)
                    if active_execution_observed and last_director_execution_deadline_monotonic is not None
                    else 0
                )
                inflight_settlement_wait_seconds = carried_execution_lease_seconds + director_settlement_timeout_seconds
                settle_result = await executor._settle_inflight_director_run_after_timeout(
                    service,
                    run_id=inflight_run_id,
                    grace_seconds=inflight_settlement_wait_seconds,
                    cancel_event=executor._resolve_cancel_event(context),
                    abort_checker=abort_checker,
                )
                if settle_result is not None:
                    final_result = settle_result
                    settled_stats = executor._read_taskboard_stats()
                    workspace_delta = executor._workspace_delivery_delta(
                        workspace_state_before,
                        executor._capture_workspace_delivery_state(),
                    )
                    workspace_delta_progress = executor._workspace_delta_indicates_materialization_progress(
                        workspace_delta
                    )
                    settled_metadata = settle_result.metadata if isinstance(settle_result.metadata, dict) else {}
                    settled_status = str(settle_result.status or "").strip().lower()
                    attempts.append(
                        {
                            "round": round_index,
                            "run_id": str(settle_result.run_id or "").strip(),
                            "status": str(settle_result.status or "").strip(),
                            "message": str(settle_result.message or "").strip(),
                            "metadata": settled_metadata,
                            "taskboard_before": before_stats,
                            "taskboard_after": settled_stats,
                            "progress_made": executor._has_director_progress(before_stats, settled_stats),
                            "workspace_delta_progress": workspace_delta_progress,
                            "workspace_delta": workspace_delta,
                            "active_execution_observed": active_execution_observed,
                            "carried_execution_lease_seconds": carried_execution_lease_seconds,
                            "settlement_timeout_seconds": director_settlement_timeout_seconds,
                            "execution_barrier_wait_seconds": inflight_settlement_wait_seconds,
                            "settled_after_timeout": True,
                        }
                    )
                    if workspace_delta_progress:
                        stage_signals.append(
                            {
                                "code": "director.workspace_delta_progress_detected",
                                "severity": "info",
                                "detail": (
                                    "Detected added or changed delivery files while settling Director run after timeout"
                                ),
                                "round": round_index,
                                **workspace_delta,
                            }
                        )
                    stage_signals.append(
                        {
                            "code": "director.inflight_timeout_settled",
                            "severity": "info" if settled_status in {"completed", "success"} else "warning",
                            "authoritative": False,
                            "authority_source": "orchestration_lifecycle_diagnostic",
                            "detail": (
                                "Director run reached terminal status during timeout settle grace: "
                                f"{settled_status or 'unknown'}"
                            ),
                            "round": round_index,
                            "run_id": str(settle_result.run_id or "").strip(),
                            "taskboard_after": settled_stats,
                            "active_execution_observed": active_execution_observed,
                            "carried_execution_lease_seconds": carried_execution_lease_seconds,
                            "settlement_timeout_seconds": director_settlement_timeout_seconds,
                            "execution_barrier_wait_seconds": inflight_settlement_wait_seconds,
                        }
                    )
                    if executor._is_taskboard_converged(settled_stats):
                        break
                    claimable_after_settle, settled_stats = await executor._wait_for_claimable_director_tasks(
                        limit=max_workers,
                        grace_seconds=executor._director_dependency_settle_grace_seconds(context),
                        factory_run_id=run.id,
                        dependency_tasks=pm_tasks,
                    )
                    if claimable_after_settle:
                        stage_signals.append(
                            {
                                "code": "director.dependencies_settled_for_next_round",
                                "severity": "info",
                                "detail": (
                                    "TaskRuntime dependency facts exposed new claimable tasks; "
                                    "starting a fresh deadline-admitted dispatch round"
                                ),
                                "round": round_index,
                                "taskboard_after": settled_stats,
                                "claimable_task_ids": claimable_after_settle,
                            }
                        )
                        continue
                    stage_signals.append(
                        {
                            "code": "director.no_claimable_tasks_after_progress",
                            "severity": "warning",
                            "detail": (
                                "TaskBoard has no claimable Director tasks after previous dispatch attempt "
                                "settled; stopping dispatch instead of replaying terminal or blocked PM tasks"
                            ),
                            "round": round_index,
                            "taskboard_before": before_stats,
                            "taskboard_after": settled_stats,
                            "failure_class": FailureClassV1.TASKBOARD_DEADLOCK.value,
                            "responsible_layer": "execution_control_plane",
                        }
                    )
                    break
                claimable_after_grace, grace_stats = await executor._wait_for_claimable_director_tasks(
                    limit=max_workers,
                    grace_seconds=executor._director_dependency_settle_grace_seconds(context),
                    factory_run_id=run.id,
                    dependency_tasks=pm_tasks,
                )
                if claimable_after_grace:
                    stage_signals.append(
                        {
                            "code": "director.dependencies_settled_for_next_round",
                            "severity": "info",
                            "detail": (
                                "TaskRuntime dependency facts exposed new claimable tasks; "
                                "starting a fresh deadline-admitted dispatch round"
                            ),
                            "round": round_index,
                            "taskboard_after": grace_stats,
                            "claimable_task_ids": claimable_after_grace,
                        }
                    )
                    continue
                stage_signals.append(
                    {
                        "code": "director.no_claimable_tasks_after_progress",
                        "severity": "warning",
                        "detail": (
                            "TaskBoard has no claimable Director tasks after previous dispatch attempt; "
                            "stopping dispatch instead of replaying terminal or blocked PM tasks"
                        ),
                        "round": round_index,
                        "taskboard_before": before_stats,
                    }
                )
                break
            if not round_requested_task_ids:
                stage_signals.append(
                    {
                        "code": "director.no_claimable_task_in_admitted_wave",
                        "severity": "warning",
                        "authoritative": False,
                        "detail": (
                            "No TaskRuntime-ready task belongs to the PM dependency wave admitted for this round; "
                            "refusing to bypass the DAG by dispatching the whole portfolio"
                        ),
                        "round": round_index,
                        "admitted_wave_task_ids": list(
                            dependency_schedule.waves[0]
                            if dependency_schedule.valid and dependency_schedule.waves
                            else ()
                        ),
                        "requested_task_ids": list(requested_task_ids or []),
                        "failure_class": FailureClassV1.TASKBOARD_DEADLOCK.value,
                        "responsible_layer": "execution_control_plane",
                    }
                )
                final_result = CommandResult(
                    run_id="",
                    status="failed",
                    message="No claimable Director task in admitted dependency wave",
                    reason_code="DIRECTOR_ADMITTED_WAVE_NOT_CLAIMABLE",
                )
                break
            base_options["metadata"]["director_claimable_task_ids"] = list(round_requested_task_ids)
            execution_deadline_monotonic = _new_monotonic_deadline(director_execution_timeout_seconds)
            last_director_execution_deadline_monotonic = execution_deadline_monotonic
            director_execution_deadline_epoch_seconds = datetime.now(
                timezone.utc
            ).timestamp() + _remaining_monotonic_seconds(execution_deadline_monotonic)
            base_options["metadata"]["factory_director_execution_deadline_epoch_seconds"] = (
                director_execution_deadline_epoch_seconds
            )
            if director_binding_fanout:
                command_result = await executor._execute_director_binding_fanout(
                    service=service,
                    workspace=str(executor.workspace),
                    tasks=round_requested_task_ids,
                    base_options=base_options,
                    bindings=director_binding_fanout,
                    timeout_seconds=director_execution_timeout_seconds,
                    cancel_event=executor._resolve_cancel_event(context),
                    abort_checker=abort_checker,
                    skipped_bindings=director_binding_skips,
                    deadline_monotonic=execution_deadline_monotonic,
                    authority_port=authority_port,
                )
                last_command_result = command_result
                director_result = command_result
            elif director_binding_skips:
                per_binding = [
                    {
                        "provider_id": str(binding.get("provider_id") or "").strip(),
                        "model": str(binding.get("model") or "").strip(),
                        "binding_id": str(binding.get("binding_id") or "").strip(),
                        "run_id": "",
                        "status": "skipped",
                        "message": "Skipped by Director binding readiness filter",
                        "skipped": True,
                        "skip_reason": str(binding.get("reason") or "binding_unavailable").strip(),
                    }
                    for binding in director_binding_skips
                    if isinstance(binding, dict)
                ]
                command_result = CommandResult(
                    run_id="",
                    status="failed",
                    message="No available Director binding after readiness filtering",
                    reason_code="DIRECTOR_BINDINGS_UNAVAILABLE",
                    metadata={
                        "binding_fanout": True,
                        "binding_count": len(per_binding),
                        "active_binding_count": 0,
                        "readiness_skipped_count": len(per_binding),
                        "per_binding": per_binding,
                        "execution_mode": execution_mode,
                        "max_workers": max_workers,
                    },
                )
                last_command_result = command_result
                director_result = command_result
            else:
                submission_remaining_seconds = _remaining_monotonic_seconds(execution_deadline_monotonic)
                try:
                    command_result = await asyncio.wait_for(
                        executor._call_with_factory_role_evidence_authority(
                            authority_port,
                            "director",
                            partial(
                                service.execute_director_run,
                                workspace=str(executor.workspace),
                                tasks=round_requested_task_ids,
                                options=base_options,
                            ),
                        ),
                        timeout=submission_remaining_seconds,
                    )
                except TimeoutError:
                    command_result = CommandResult(
                        run_id="",
                        status="timeout",
                        message="Director submission exceeded the execution lease",
                        reason_code="DIRECTOR_SUBMISSION_TIMEOUT",
                        metadata={
                            "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                            "responsible_layer": "execution_control_plane",
                            "submission_outcome_unknown": True,
                        },
                    )
                last_command_result = command_result
                command_status = str(command_result.status or "").strip().lower()
                wait_timeout_seconds = _whole_wait_seconds(execution_deadline_monotonic)
                wait_remaining_seconds = _remaining_monotonic_seconds(execution_deadline_monotonic)
                if (
                    command_status in {"blocked", "cancelled", "failed", "timeout"}
                    or not str(command_result.run_id or "").strip()
                ):
                    director_result = command_result
                elif wait_timeout_seconds <= 0 or wait_remaining_seconds <= 0:
                    director_result = CommandResult(
                        run_id=command_result.run_id,
                        status="timeout",
                        message="Director execution lease expired before completion wait",
                        reason_code="DIRECTOR_EXECUTION_LEASE_EXHAUSTED",
                        metadata={
                            "cancel_signal_sent": False,
                            "inflight_run_continues": True,
                            "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                            "responsible_layer": "execution_control_plane",
                        },
                    )
                else:
                    director_wait_kwargs: dict[str, Any] = {
                        "timeout_seconds": wait_timeout_seconds,
                        "cancel_event": executor._resolve_cancel_event(context),
                        "abort_checker": abort_checker,
                        "cancel_on_timeout": False,
                    }
                    if _call_accepts_keyword(executor._wait_run_completion, "authority"):
                        director_wait_kwargs["authority"] = RunCompletionAuthority.TASK_RUNTIME_EXECUTION_FACT
                    try:
                        director_result = await asyncio.wait_for(
                            executor._wait_run_completion(
                                service,
                                command_result,
                                **director_wait_kwargs,
                            ),
                            timeout=wait_remaining_seconds,
                        )
                    except TimeoutError:
                        director_result = CommandResult(
                            run_id=command_result.run_id,
                            status="timeout",
                            message="Director completion wait exceeded the execution lease",
                            reason_code="DIRECTOR_EXECUTION_LEASE_EXHAUSTED",
                            metadata={
                                "cancel_signal_sent": False,
                                "inflight_run_continues": True,
                                "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                                "responsible_layer": "execution_control_plane",
                            },
                        )
            # A lifecycle result can report ``inflight_run_continues``
            # before the execution lease has actually elapsed (for
            # example while TaskRuntime's fact projection is still
            # catching up).  Spending only the settlement reserve here
            # lets Factory return and close its stage-bound role-evidence
            # authority while the admitted Director child is still
            # approaching Provider transport.  Preserve the one parent / one
            # child execution boundary by consuming the unused execution
            # lease first, then the dedicated settlement reserve.
            director_barrier_wait_seconds = director_settlement_timeout_seconds
            if executor._inflight_director_run_ids(director_result):
                director_barrier_wait_seconds += _whole_wait_seconds(execution_deadline_monotonic)
            director_result, barrier_observed = await executor._settle_inflight_director_result(
                service,
                result=director_result,
                grace_seconds=director_barrier_wait_seconds,
                cancel_event=executor._resolve_cancel_event(context),
                abort_checker=abort_checker,
            )
            final_result = director_result

            after_stats = executor._read_taskboard_stats()
            workspace_delta = executor._workspace_delivery_delta(
                workspace_state_before,
                executor._capture_workspace_delivery_state(),
            )
            workspace_delta_progress = executor._workspace_delta_indicates_materialization_progress(workspace_delta)
            metadata_payload = director_result.metadata if isinstance(director_result.metadata, dict) else {}
            progress_made = executor._has_director_progress(before_stats, after_stats)
            attempt_entry = {
                "round": round_index,
                "run_id": str(command_result.run_id or "").strip(),
                "status": str(director_result.status or "").strip(),
                "message": str(director_result.message or "").strip(),
                "metadata": metadata_payload,
                "taskboard_before": before_stats,
                "taskboard_after": after_stats,
                "timeout_seconds": director_lease_timeout_seconds,
                "execution_timeout_seconds": director_execution_timeout_seconds,
                "settlement_timeout_seconds": director_settlement_timeout_seconds,
                "execution_barrier_wait_seconds": director_barrier_wait_seconds,
                "materialization_pending": materialization_pending,
                "missing_declared_target_count": len(missing_declared_targets),
                "progress_made": progress_made,
                "workspace_delta_progress": workspace_delta_progress,
                "workspace_delta": workspace_delta,
                "settlement_attempted": barrier_observed,
                "settled_after_timeout": barrier_observed and not bool(metadata_payload.get("inflight_run_continues")),
            }
            attempts.append(attempt_entry)
            if barrier_observed:
                barrier_still_active = bool(metadata_payload.get("inflight_run_continues"))
                stage_signals.append(
                    {
                        "code": (
                            "director.execution_barrier_timeout"
                            if barrier_still_active
                            else "director.inflight_timeout_settled"
                        ),
                        "severity": "error" if barrier_still_active else "info",
                        "detail": (
                            "Director child execution remained active after the settlement barrier"
                            if barrier_still_active
                            else "Director child execution reached a terminal fact before the next dispatch round"
                        ),
                        "round": round_index,
                        "run_id": str(director_result.run_id or command_result.run_id or "").strip(),
                        "failure_class": (
                            FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value if barrier_still_active else ""
                        ),
                        "responsible_layer": "execution_control_plane",
                        "inflight_run_continues": barrier_still_active,
                        "settled_after_timeout": not barrier_still_active,
                    }
                )
                if barrier_still_active:
                    execution_barrier_timeout_observed = True
                    break
            if workspace_delta_progress:
                stage_signals.append(
                    {
                        "code": "director.workspace_delta_progress_detected",
                        "severity": "info",
                        "detail": "Detected added or changed delivery files during Director dispatch",
                        "round": round_index,
                        **workspace_delta,
                    }
                )

            round_authority = helpers.evaluate_canonical_factory_authority(
                executor._canonical_factory_projection(run, context)
            )
            director_status = str(director_result.status or "").strip().lower()
            if round_authority.director_stage_authorized:
                director_status = "completed"
                progress_made = True
                attempt_entry["progress_made"] = True
                attempt_entry["canonical_task_boundary_authorized"] = True
            elif director_status in {"completed", "success"}:
                director_status = "failed"
                stage_signals.append(
                    {
                        "code": "director.canonical_task_boundary_missing",
                        "severity": "error",
                        "detail": round_authority.detail,
                        "round": round_index,
                        "reason_code": round_authority.reason_code,
                        "failure_class": round_authority.failure_class
                        or FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
                        "responsible_layer": round_authority.responsible_layer or "execution_control_plane",
                        "incomplete_task_ids": list(round_authority.incomplete_task_ids),
                    }
                )
            if director_status not in {"completed", "success"}:
                if progress_made:
                    idle_rounds = 0
                    if executor._is_taskboard_converged(after_stats):
                        stage_signals.append(
                            {
                                "code": "director.dispatch_converged_after_partial_failure",
                                "severity": "info",
                                "detail": f"Director dispatch converged after partial failure in round {round_index}",
                                "round": round_index,
                                "upstream_status": director_status,
                            }
                        )
                        break
                    stage_signals.append(
                        {
                            "code": "director.partial_failure_progress_continued",
                            "severity": "warning",
                            "detail": (
                                "Director run returned a non-success status after material progress; "
                                "continuing remaining dispatch rounds until TaskBoard convergence"
                            ),
                            "upstream_status": director_status,
                            "round": round_index,
                        }
                    )
                    continue
                if director_status == "timeout":
                    attempt_timeout_seconds = director_execution_timeout_seconds
                    stage_signals.append(
                        {
                            "code": "director.dispatch_timeout",
                            "severity": "error",
                            "detail": (
                                "Director dispatch timed out after "
                                f"{attempt_timeout_seconds} "
                                "seconds; "
                                "no further progress possible"
                            ),
                            "upstream_status": director_status,
                            "round": round_index,
                            "timeout_seconds": attempt_timeout_seconds,
                            "stage_lease_seconds": director_lease_timeout_seconds,
                            "settlement_timeout_seconds": director_settlement_timeout_seconds,
                            "materialization_pending": materialization_pending,
                            "missing_declared_target_count": len(missing_declared_targets),
                        }
                    )
                else:
                    stage_signals.append(
                        {
                            "code": "director.run_status_non_success",
                            "severity": "error",
                            "detail": str(director_result.message or "").strip()
                            or str(director_result.status or "unknown"),
                            "upstream_status": str(director_result.status or "").strip(),
                            "round": round_index,
                        }
                    )
                break

            if progress_made:
                idle_rounds = 0
            else:
                idle_rounds += 1
                stage_signals.append(
                    {
                        "code": "director.no_progress_round",
                        "severity": "warning",
                        "detail": f"No TaskBoard progress in dispatch round {round_index}",
                        "round": round_index,
                        "idle_rounds": idle_rounds,
                    }
                )

            if executor._is_taskboard_converged(after_stats):
                stage_signals.append(
                    {
                        "code": "director.dispatch_converged",
                        "severity": "info",
                        "detail": f"Director dispatch converged in {round_index} rounds",
                        "round": round_index,
                    }
                )
                break

            if idle_rounds > idle_budget:
                stage_signals.append(
                    {
                        "code": "director.dispatch_stalled",
                        "severity": "error",
                        "detail": (
                            "Director dispatch exceeded idle progress budget; "
                            f"idle_rounds={idle_rounds}, idle_budget={idle_budget}"
                        ),
                        "round": round_index,
                    }
                )
                break

    final_stats = executor._read_taskboard_stats()
    converged = executor._is_taskboard_converged(final_stats)
    final_authority = helpers.evaluate_canonical_factory_authority(executor._canonical_factory_projection(run, context))
    if not final_authority.director_stage_authorized and not any(
        str(item.get("code") or "") == "director.canonical_task_boundary_missing"
        for item in stage_signals
        if isinstance(item, dict)
    ):
        stage_signals.append(
            {
                "code": "director.canonical_task_boundary_missing",
                "severity": "error",
                "detail": final_authority.detail,
                "reason_code": final_authority.reason_code,
                "failure_class": final_authority.failure_class or FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
                "responsible_layer": final_authority.responsible_layer or "execution_control_plane",
                "incomplete_task_ids": list(final_authority.incomplete_task_ids),
            }
        )

    provider_health_signal = executor._director_provider_health_failure_signal()
    if provider_health_signal and not any(
        str(item.get("code") or "") == str(provider_health_signal.get("code") or "")
        for item in stage_signals
        if isinstance(item, dict)
    ):
        stage_signals.append(provider_health_signal)
    stage_signals.extend(snapshot_signals)
    if (
        requires_taskboard_convergence
        and not converged
        and not execution_barrier_timeout_observed
        and not any(
            str(item.get("code") or "") == "director.taskboard_not_converged"
            for item in stage_signals
            if isinstance(item, dict)
        )
    ):
        stage_signals.append(
            {
                "code": "director.taskboard_not_converged",
                "severity": "warning",
                "detail": f"TaskBoard not converged after dispatch rounds; final_stats={final_stats}",
                "authoritative": False,
                "authority_source": "task_runtime_diagnostic_projection",
            }
        )

    stage_status = "success" if final_authority.director_stage_authorized else "failed"
    if not final_authority.director_stage_authorized and (
        str((final_result or CommandResult(run_id="", status="", message="")).status or "").strip().lower()
        == "cancelled"
    ):
        stage_status = "cancelled"

    # Generate per-binding terminal route events from fanout results
    per_binding_route_events: list[dict[str, Any]] = []
    for attempt in attempts:
        metadata = attempt.get("metadata") if isinstance(attempt, dict) else {}
        if not isinstance(metadata, dict):
            continue
        per_binding_raw = metadata.get("per_binding")
        if isinstance(per_binding_raw, list):
            per_binding_items = [item for item in per_binding_raw if isinstance(item, dict)]
            per_binding_route_events.extend(
                executor._build_per_binding_route_events(cast(list[dict[str, Any]], per_binding_items))
            )

    if stage_status != "cancelled":
        _binding_ok, binding_signals = executor._validate_director_binding_coverage(
            additional_events=per_binding_route_events,
        )
        for signal in binding_signals:
            if str(signal.get("severity") or "").strip().lower() == "error":
                signal["severity"] = "warning"
                signal["authoritative"] = False
                signal["authority_source"] = "binding_coverage_diagnostic"
        stage_signals.extend(binding_signals)

    error_code = ""
    root_cause_hint = ""
    for signal in stage_signals:
        if not isinstance(signal, dict):
            continue
        if str(signal.get("severity") or "").strip().lower() != "error":
            continue
        error_code = str(signal.get("code") or "").strip()
        root_cause_hint = str(signal.get("detail") or "").strip()
        if error_code:
            break

    if per_binding_route_events:
        executor._reclassify_binding_coverage_signals(
            stage_signals,
            per_binding_route_events,
        )

    for signal in stage_signals:
        if not isinstance(signal, dict):
            continue
        if str(signal.get("severity") or "").strip().lower() != "error":
            continue
        error_code = str(signal.get("code") or "").strip()
        root_cause_hint = str(signal.get("detail") or "").strip()
        if error_code:
            break

    if final_authority.director_stage_authorized:
        error_code = ""
        root_cause_hint = ""

    fail_closed_events = executor._build_fail_closed_director_route_events(
        attempts=attempts,
        stage_signals=stage_signals,
        per_binding_route_events=per_binding_route_events,
    )
    if fail_closed_events:
        stage_signals.append(
            {
                "code": "director.fail_closed_route_evidence",
                "severity": "info",
                "detail": f"Recorded fail-closed diagnostics for {len(fail_closed_events)} missing director route(s)",
                "count": len(fail_closed_events),
            }
        )

    stage_signal_path = ""
    if stage_signals:
        stage_signal_path = executor._write_stage_signal_artifact(
            stage="director_dispatch",
            run_id=run.id,
            signals=stage_signals,
        )

    dispatch_payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "factory_stage_executor",
        "factory_run_id": run.id,
        "orchestration_run_id": str((last_command_result.run_id if last_command_result else "") or "").strip(),
        "status": str((final_result.status if final_result else stage_status) or "").strip(),
        "message": str((final_result.message if final_result else "") or "").strip(),
        "metadata": final_result.metadata if (final_result and isinstance(final_result.metadata, dict)) else {},
        "taskboard": {
            "initial": initial_stats,
            "final": final_stats,
            "converged": converged,
            "requires_convergence": requires_taskboard_convergence,
        },
        "attempts": attempts,
        "signals": stage_signals,
        "fail_closed_route_events": fail_closed_events,
        "per_binding_route_events": per_binding_route_events,
        "quality_gate_handoff": False,
        "canonical_authority": {
            "source": "run_ledger_projection",
            "authorized": final_authority.director_stage_authorized,
            "reason_code": final_authority.reason_code,
            "detail": final_authority.detail,
            "task_count": final_authority.task_count,
            "incomplete_task_ids": list(final_authority.incomplete_task_ids),
        },
        "failure_stage": "director_dispatch" if stage_status == "failed" else "",
        "error_code": error_code or None,
        "root_cause_hint": root_cause_hint or None,
        "evidence_paths": {
            "plan": "tasks/plan.json" if executor._artifact_exists("tasks/plan.json", min_chars=1) else "",
            "dispatch_log": "dispatch/log.json",
            "stage_signals": stage_signal_path,
        },
    }
    executor._write_json_artifact("dispatch/log.json", dispatch_payload)
    artifacts = ["dispatch/log.json"]
    executor._mirror_director_artifacts(run.id, artifacts)
    if stage_signal_path:
        artifacts.append(stage_signal_path)
    inflight_run_continues = execution_barrier_timeout_observed or any(
        bool(metadata.get("inflight_run_continues"))
        for attempt in attempts
        if isinstance(attempt, dict)
        for metadata in [attempt.get("metadata")]
        if isinstance(metadata, dict)
    )
    settlement_metadata: dict[str, Any] = {
        "child_sessions_settled": not inflight_run_continues,
        "inflight_run_continues": inflight_run_continues,
        "settlement_source": "director_dispatch_settlement_barrier",
    }
    # R165/M06: multi-task Director often times out with partial files on disk
    # (package.json + src) while quality_gate never runs because the stage
    # failed. Run materialization-quality schedule once before leaving
    # director_dispatch so smoke tests and covered tsc repairs still land.
    if stage_status != "cancelled":
        materialization_settle = await executor._run_director_stage_materialization_quality_settle(
            run=run,
            stage_status=stage_status,
            error_code=error_code,
        )
        if materialization_settle:
            settlement_metadata["director_stage_materialization_quality_settle"] = materialization_settle
            stage_signals.append(
                {
                    "code": "director.stage_materialization_quality_settle",
                    "severity": "info",
                    "detail": str(materialization_settle.get("detail") or "materialization quality settle ran"),
                    "ok": bool(materialization_settle.get("ok")),
                    "tool_result_count": int(materialization_settle.get("tool_result_count") or 0),
                    "diagnostic_count": int(materialization_settle.get("diagnostic_count") or 0),
                    "reason": str(materialization_settle.get("reason") or ""),
                }
            )
        # R177/M06: multi-task timeout claims materialization for TASK-N (lifecycle
        # requirement) but never reaches execute_method's no-tools seal path →
        # TOOL_LIFECYCLE_MISSING. Seal blocked incomplete receipts for missing
        # required tasks after settle so ledger integrity distinguishes incomplete
        # work from true missing evidence.
        lifecycle_seal = executor._seal_director_stage_missing_tool_lifecycles(
            run=run,
            incomplete_task_ids=list(final_authority.incomplete_task_ids),
        )
        if lifecycle_seal:
            settlement_metadata["director_stage_missing_tool_lifecycle_seal"] = lifecycle_seal
            stage_signals.append(
                {
                    "code": "director.stage_missing_tool_lifecycle_seal",
                    "severity": "info",
                    "detail": str(lifecycle_seal.get("detail") or "sealed missing tool lifecycles"),
                    "ok": bool(lifecycle_seal.get("ok")),
                    "sealed_count": int(lifecycle_seal.get("sealed_count") or 0),
                    "missing_before": list(lifecycle_seal.get("missing_before") or ()),
                }
            )
        if materialization_settle or lifecycle_seal:
            # R181/M06: settle can complete on-disk delivery after authority was
            # evaluated. Reconcile boundary against workspace + re-evaluate so
            # false task_runtime_not_converged / canonical_task_boundary_missing
            # does not terminal-fail a stage that already real-runs green.
            recovered = executor._recover_director_stage_authority_after_delivery_settle(
                run=run,
                context=context,
                prior_authority=final_authority,
            )
            if recovered is not None and recovered.director_stage_authorized:
                final_authority = recovered
                stage_status = "success"
                error_code = ""
                root_cause_hint = ""
                dispatch_payload["status"] = stage_status
                dispatch_payload["error_code"] = None
                dispatch_payload["root_cause_hint"] = None
                dispatch_payload["canonical_authority"] = {
                    "source": "run_ledger_projection",
                    "authorized": True,
                    "reason_code": final_authority.reason_code,
                    "detail": final_authority.detail,
                    "task_count": final_authority.task_count,
                    "incomplete_task_ids": list(final_authority.incomplete_task_ids),
                    "recovered_after_delivery_settle": True,
                }
                stage_signals.append(
                    {
                        "code": "director.stage_authority_recovered_after_delivery_settle",
                        "severity": "info",
                        "detail": (
                            "Canonical director authority recovered after materialization "
                            "settle reconciled on-disk delivery with task-boundary verdicts"
                        ),
                        "reason_code": final_authority.reason_code,
                    }
                )
            if stage_signal_path or stage_signals:
                # Refresh signal artifact with settle / seal evidence.
                stage_signal_path = executor._write_stage_signal_artifact(
                    stage="director_dispatch",
                    run_id=run.id,
                    signals=stage_signals,
                )
                dispatch_payload["signals"] = stage_signals
                dispatch_payload["evidence_paths"]["stage_signals"] = stage_signal_path
                executor._write_json_artifact("dispatch/log.json", dispatch_payload)
    if stage_status == "cancelled":
        return StageResult(
            stage="director_dispatch",
            status="cancelled",
            output=f"Director dispatch cancelled: {(final_result.message if final_result else 'N/A')}",
            artifacts=artifacts,
            metadata=settlement_metadata,
        )
    return StageResult(
        stage="director_dispatch",
        status=stage_status,
        output=(
            f"Director dispatch {(final_result.status if final_result else 'unknown')}: "
            f"{(final_result.message if final_result else 'N/A')}; "
            f"signals={len(stage_signals)}; "
            f"error_code={error_code or 'none'}; root_cause_hint={root_cause_hint or 'none'}"
        ),
        artifacts=artifacts,
        metadata=settlement_metadata,
    )
