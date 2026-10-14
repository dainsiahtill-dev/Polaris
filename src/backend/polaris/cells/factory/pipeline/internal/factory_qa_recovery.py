"""QA observations route local rework through the existing Director owner transaction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import factory_workspace_quality_impl as quality
from .factory_stage_executor._helpers import (
    _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS,
    _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS,
)
from .native_validation_session import NativeValidationSession


@dataclass(frozen=True)
class QaRepairReverification:
    """Private continuation evidence issued only after an authenticated repair is reverified."""

    workspace: str
    factory_run_id: str
    report_hash: str
    owner_task_ids: tuple[str, ...]
    effect_receipt_ids: tuple[str, ...]

    def matches(self, workspace: str, run_id: str) -> bool:
        return (
            self.workspace == workspace
            and self.factory_run_id == run_id
            and bool(self.owner_task_ids)
            and bool(self.effect_receipt_ids)
        )


def report_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def qa_report_identity_error(
    payload: Mapping[str, Any],
    *,
    workspace: str,
    factory_run_id: str,
    review_run_id: str,
) -> str:
    """Validate a new report against its actual host-owned QA execution."""
    passed = payload.get("passed")
    verdict = payload.get("verdict")
    if (
        payload.get("source") != "qa_adapter_v3"
        or not isinstance(passed, bool)
        or not isinstance(verdict, str)
        or verdict not in {"PASS", "FAIL"}
        or passed != (verdict == "PASS")
    ):
        return "qa_report_verdict_not_a_typed_independent_observation"
    expected = {"workspace": str(Path(workspace).resolve()), "factory_run_id": factory_run_id, "run_id": review_run_id}
    if not review_run_id or any(str(payload.get(key) or "") != value for key, value in expected.items()):
        return "qa_report_identity_missing_or_foreign"
    task_id = str(payload.get("task_id") or "")
    if not task_id:
        return "qa_report_task_identity_missing"
    from polaris.cells.orchestration.workflow_runtime.public.service import get_orchestration_service

    try:
        runtime = await get_orchestration_service()
        snapshot = await runtime.query_run(review_run_id)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return f"qa_report_runtime_identity_read_failed:{type(exc).__name__}"
    if snapshot is None or snapshot.run_id != review_run_id or snapshot.workspace != expected["workspace"]:
        return "qa_report_runtime_identity_unavailable"
    task = snapshot.tasks.get(task_id)
    status = getattr(getattr(task, "status", None), "value", getattr(task, "status", None))
    if task is None or task.task_id != task_id or task.role_id != "qa" or status not in {"completed", "failed"}:
        return "qa_report_runtime_task_identity_mismatch"
    if payload.get("passed") is True and status != "completed":
        return "qa_report_pass_without_completed_runtime"
    return ""


def repairable_findings(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Only artifact observations qualify; infrastructure failures never grant rework."""
    if payload.get("passed") is not False or payload.get("source") != "qa_adapter_v3":
        return ()
    if any(not isinstance(payload.get(key) or (), list | tuple) for key in ("critical_issues", "major_issues")):
        return ()
    findings = tuple(
        str(item).strip()
        for key in ("critical_issues", "major_issues")
        for item in (payload.get(key) or ())
        if str(item).strip()
    )
    diagnostics = "\n".join(
        (
            *findings,
            *(str(item) for item in payload.get("evidence") or ()),
            *(str(item) for item in payload.get("warnings") or ()),
        )
    )
    blocked = (
        "qa_runtime_exception",
        "qa_llm_judgement_unavailable",
        "upstream_stage_error:",
        "context_snapshot",
        "provider_stream_timeout",
        "authentication failed",
        "billing_exhausted",
        "rate_limit",
        "authority_missing",
        "capability_token",
        "workspace_scope_violation",
        "budget_exhausted",
    )
    return () if any(marker in diagnostics.casefold() for marker in blocked) else findings


def owned_finding_groups(executor: Any, run: Any, findings: tuple[str, ...]) -> dict[str, list[str]]:
    """Resolve observations against committed CE ownership, without deriving authority from prose."""
    owners: dict[str, set[str]] = {}
    for index, task in enumerate(executor._load_pm_plan_tasks("tasks/plan.json"), 1):
        task_id = executor._task_id(task, index)
        restored = quality._workspace_quality_frozen_ce_owner_task(
            executor,
            run_id=run.id,
            task_id=task_id,
            canonical_task=task,
        )
        projection = restored.get("task_completion_projection")
        if not isinstance(projection, Mapping) or projection.get("run_id") != run.id:
            continue
        if projection.get("task_id") != task_id:
            continue
        owned_paths = {
            str(item.get("path") or "")
            for item in projection.get("owned_artifacts") or ()
            if isinstance(item, Mapping) and item.get("owner_task_id") == task_id
        }
        if owned_paths:
            owners[task_id] = owned_paths
    groups: dict[str, list[str]] = {}
    for finding in findings:
        matched = {task_id: [path for path in paths if path in finding] for task_id, paths in owners.items()}
        matched = {task_id: paths for task_id, paths in matched.items() if paths}
        if not matched and len(owners) == 1:
            task_id = next(iter(owners))
            matched = {task_id: sorted(owners[task_id])}
        if len(matched) != 1:
            raise ValueError("qa_repair_exact_owner_missing_or_ambiguous")
        task_id, paths = next(iter(matched.items()))
        groups.setdefault(task_id, []).extend(path for path in paths if path not in groups.get(task_id, []))
    return groups


class ExactQaOwnerExecutor:
    """Narrow existing claim selection to one immutable owner before provider dispatch."""

    def __init__(self, executor: Any, owner_task_id: str) -> None:
        self.executor = executor
        self.owner_task_id = owner_task_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self.executor, name)

    def _claim_workspace_quality_repair_attempt(self, **kwargs: Any) -> Any:
        return quality._claim_workspace_quality_repair_attempt(
            self.executor,
            **kwargs,
            original_owner_task_id=self.owner_task_id,
        )


async def repair_qa_role_findings(
    executor: Any,
    *,
    run: Any,
    context: dict[str, Any],
    qa_payload: dict[str, Any],
    report_hash: str,
) -> QaRepairReverification | None:
    """Rework exact owners; authenticate candidate effects, physically verify, then settle."""
    findings = repairable_findings(qa_payload)
    if not findings:
        return None
    groups = owned_finding_groups(executor, run, findings)
    commands = executor._workspace_quality_commands(context, run=run)
    if not groups or not commands:
        return None
    from .factory_materialization_impl import (
        MaterializationDiagnostics,
        _continue_materialization_verifier_repair,
    )

    receipt_ids: list[str] = []
    for ordinal, (task_id, target_files) in enumerate(groups.items(), 1):
        owner_executor = ExactQaOwnerExecutor(executor, task_id)
        session = NativeValidationSession.from_factory(executor, run, context, commands=commands)
        repair_context = dict(context)
        repair_context["_factory_qa_repair_owner_task_id"] = task_id
        repair_context["factory_workspace_quality_owner_rebind"] = {
            "required": True,
            "source": "independent_qa_observation",
        }

        async def collect(_session=session, _context=repair_context) -> MaterializationDiagnostics:
            results = []
            for command in commands:
                abort = await executor._quality_gate_abort_reason(executor._resolve_abort_checker(_context))
                remaining = executor._factory_deadline_remaining_seconds(_context)
                reserve = _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS + _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS
                if abort or (remaining is not None and remaining <= reserve):
                    raise RuntimeError("independent_qa_verification_cancelled_or_deadline")
                configured = float(_context.get("workspace_validation_timeout_seconds") or 120.0)
                timeout = min(configured, remaining - reserve) if remaining is not None else configured
                row = await _session.run_command(command, max(1.0, timeout))
                row["command"] = list(command)
                results.append(row)
            failures = [row for row in results if row.get("passed") is not True or row.get("exit_code") != 0]
            errors = executor._workspace_quality_repair_errors(failures, run=run) if failures else []
            return MaterializationDiagnostics(
                errors,
                verifier_results=results,
                static_evidence={"scan_mode": "pure_static", "scan_complete": True, "controlled_checks_complete": True},
                blockers=[
                    str(row.get("error") or "verification_input_not_qualified")
                    for row in results
                    if row.get("verification_contract_blocker") is True
                ],
            )

        async def repair(
            *,
            errors: list[str],
            attempt: int,
            _owner=owner_executor,
            _context=repair_context,
            _ordinal=ordinal,
            _targets=target_files,
            _task_id=task_id,
        ) -> Any:
            effects, summary = await _owner._apply_workspace_quality_llm_repairs(
                run=run,
                context=_context,
                artifact_quality_errors=errors,
                repair_attempt=int(context.get("_factory_qa_repair_round") or 0) + _ordinal + attempt - 1,
                owner_target_files=_targets,
            )
            pending = summary.get("_pending_task_runtime_repair_attempt")
            if isinstance(pending, Mapping) and pending.get("task_id") != _task_id:
                await quality._settle_pending_workspace_quality_repair_attempt(
                    executor, pending, accepted=False, reason="independent_qa_repair_owner_mismatch"
                )
                return [], {"attempted": False, "error_code": "independent_qa_repair_owner_mismatch"}
            receipt_ids.extend(
                str(item["effect_receipt"].get("receipt_id") or item["effect_receipt"].get("receipt_hash") or "")
                for item in effects
                if isinstance(item.get("effect_receipt"), Mapping)
                and executor._workspace_quality_repair_result_has_mutation(item)
            )
            return effects, summary

        try:
            baseline = await collect()
            if baseline.blockers:
                return None
            result = await _continue_materialization_verifier_repair(
                owner_executor,
                run=run,
                context=repair_context,
                result={"ok": False, "post_commit_diagnostics": list(findings)},
                sessions=[session],
                observations=[baseline.to_dict()],
                collect_diagnostics=collect,
                repair_candidate=repair,
            )
            if result.get("ok") is not True:
                return None
        finally:
            session.close()
    # Existing full workspace checks own command/entrypoint receipt projection.
    # Their new evidence must pass before a fresh independent QA can run.
    passed, _artifact = await executor._run_workspace_quality_checks(run, context)
    if passed is not True or not all(receipt_ids):
        return None
    return QaRepairReverification(str(executor.workspace), run.id, report_hash, tuple(groups), tuple(receipt_ids))
