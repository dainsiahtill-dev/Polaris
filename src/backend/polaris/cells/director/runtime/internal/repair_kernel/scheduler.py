"""Convergence scheduler for Director deterministic repair rounds."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .composer import PatchComposer
from .contracts import (
    RepairDiagnostic,
    RepairExecutionResult,
    RepairPlan,
    RepairReceipt,
    RepairRevalidationEvidence,
    sha256_text,
)
from .executor import EditFileFn, TransactionalRepairExecutor, WriteFileFn
from .legacy_bridge import summarize_repair_revalidation_coverage
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate
from .receipts import attach_revalidation_evidence, build_receipt

VerifierFn = Callable[[int, tuple[RepairReceipt, ...]], "RepairVerifierSnapshot"]
PlannerFn = Callable[[tuple[RepairDiagnostic, ...], int], Sequence[RepairPlan]]
BaseFilesProviderFn = Callable[[RepairPlan], Mapping[str, str]]
CONVERGENCE_PIPELINE_STAGES = (
    "Diagnostics",
    "Coverage",
    "Plan",
    "Compose",
    "Policy",
    "Execute",
    "Revalidate",
    "Receipt",
    "Next Round",
)
CONVERGENCE_PIPELINE_ORDER = " -> ".join(CONVERGENCE_PIPELINE_STAGES)


def convergence_envelope_metadata(
    *,
    preferred_entrypoint: str = "run_runtime_repair_convergence",
    typed_receipt_path_available: bool = True,
    callback_migration_envelope: bool = False,
) -> dict[str, Any]:
    """Return the runtime-owned convergence envelope contract metadata."""

    return {
        "envelope_owner": "director.runtime.repair_kernel.scheduler",
        "canonical_convergence_executor": "RepairConvergenceScheduler",
        "preferred_entrypoint": preferred_entrypoint,
        "convergence_scheduler_required": True,
        "typed_convergence_scheduler_active": bool(typed_receipt_path_available and not callback_migration_envelope),
        "typed_receipt_path_available": bool(typed_receipt_path_available),
        "callback_migration_envelope": bool(callback_migration_envelope),
        "pipeline": list(CONVERGENCE_PIPELINE_STAGES),
        "pipeline_order": CONVERGENCE_PIPELINE_ORDER,
        "coverage_stage_required": True,
        "coverage_before_plan_required": True,
        "policy_before_execute_required": True,
        "revalidation_receipt_binding_required": True,
        "hidden_language_loop_allowed": False,
        "language_self_loop_allowed": False,
    }


@dataclass(frozen=True)
class RepairVerifierSnapshot:
    """Verifier/compiler diagnostics captured before or after a repair round."""

    diagnostics: tuple[RepairDiagnostic, ...] = ()
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    raw_output_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics or ()))
        object.__setattr__(self, "command", tuple(str(item) for item in self.command if str(item or "").strip()))
        object.__setattr__(self, "raw_output_ref", str(self.raw_output_ref or "").strip() or None)
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def error_count(self) -> int:
        return len(self.diagnostics)

    def diagnostic_signature(self) -> str:
        payload = "|".join(
            sorted(
                f"{diagnostic.code}:{diagnostic.path}:{diagnostic.line}:{diagnostic.column}:{diagnostic.message}"
                for diagnostic in self.diagnostics
            )
        )
        return sha256_text(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "exit_code": self.exit_code,
            "error_count": self.error_count,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "diagnostic_signature": self.diagnostic_signature(),
            "raw_output_ref": self.raw_output_ref,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RepairPlanSchedule:
    """Dependency-aware repair plan ordering for one convergence round."""

    ordered_plans: tuple[RepairPlan, ...]
    blocked_rule_ids: tuple[str, ...] = ()
    cycle_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordered_plan_ids": [plan.plan_id for plan in self.ordered_plans],
            "ordered_rule_ids": [plan.rule_id for plan in self.ordered_plans],
            "blocked_rule_ids": list(self.blocked_rule_ids),
            "cycle_detected": self.cycle_detected,
        }


@dataclass(frozen=True)
class RepairRoundResult:
    """Auditable result for one repair convergence round."""

    round_number: int
    status: str
    schedule: RepairPlanSchedule
    diagnostics_before: tuple[RepairDiagnostic, ...]
    diagnostics_after: tuple[RepairDiagnostic, ...]
    receipts: tuple[RepairReceipt, ...] = ()
    revalidation_evidence: RepairRevalidationEvidence | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "round_number", max(0, int(self.round_number)))
        object.__setattr__(self, "status", str(self.status or "unknown").strip() or "unknown")
        object.__setattr__(self, "diagnostics_before", tuple(self.diagnostics_before or ()))
        object.__setattr__(self, "diagnostics_after", tuple(self.diagnostics_after or ()))
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_number": self.round_number,
            "status": self.status,
            "schedule": self.schedule.to_dict(),
            "errors_before": len(self.diagnostics_before),
            "errors_after": len(self.diagnostics_after),
            "net_error_reduction": len(self.diagnostics_before) - len(self.diagnostics_after),
            "diagnostics_before": [diagnostic.to_dict() for diagnostic in self.diagnostics_before],
            "diagnostics_after": [diagnostic.to_dict() for diagnostic in self.diagnostics_after],
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "revalidation_evidence": self.revalidation_evidence.to_dict()
            if self.revalidation_evidence is not None
            else None,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RepairConvergenceResult:
    """Final result of a deterministic repair convergence loop."""

    status: str
    final_diagnostics: tuple[RepairDiagnostic, ...]
    rounds: tuple[RepairRoundResult, ...] = ()
    receipts: tuple[RepairReceipt, ...] = ()
    max_rounds: int = 3
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", str(self.status or "unknown").strip() or "unknown")
        object.__setattr__(self, "final_diagnostics", tuple(self.final_diagnostics or ()))
        object.__setattr__(self, "rounds", tuple(self.rounds or ()))
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "max_rounds", max(1, int(self.max_rounds)))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def converged(self) -> bool:
        return self.status in {"already_clean", "converged"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "converged": self.converged,
            "max_rounds": self.max_rounds,
            "round_count": len(self.rounds),
            "receipt_count": len(self.receipts),
            "final_error_count": len(self.final_diagnostics),
            "final_diagnostics": [diagnostic.to_dict() for diagnostic in self.final_diagnostics],
            "rounds": [round_result.to_dict() for round_result in self.rounds],
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "metadata": dict(self.metadata),
        }


def order_repair_plans(
    plans: Sequence[RepairPlan],
    *,
    completed_rule_ids: Sequence[str] = (),
) -> RepairPlanSchedule:
    """Return a stable priority/dependency order for repair plans."""

    pending = sorted(plans or (), key=lambda plan: (plan.priority, plan.rule_id, plan.plan_id))
    planned_rule_ids = {plan.rule_id for plan in pending}
    completed = {str(rule_id) for rule_id in completed_rule_ids if str(rule_id or "").strip()}
    ordered: list[RepairPlan] = []
    while pending:
        progressed = False
        remaining: list[RepairPlan] = []
        for plan in pending:
            dependencies_satisfied = all(
                dependency in completed or dependency not in planned_rule_ids for dependency in plan.depends_on
            )
            if dependencies_satisfied:
                ordered.append(plan)
                completed.add(plan.rule_id)
                progressed = True
            else:
                remaining.append(plan)
        if not progressed:
            return RepairPlanSchedule(
                ordered_plans=tuple(ordered),
                blocked_rule_ids=tuple(plan.rule_id for plan in remaining),
                cycle_detected=True,
            )
        pending = remaining
    return RepairPlanSchedule(ordered_plans=tuple(ordered))


def _scheduler_result_metadata(
    status: str,
    *,
    final_diagnostics: Sequence[RepairDiagnostic] = (),
    receipts: Sequence[RepairReceipt] = (),
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    revalidation_coverage = summarize_repair_revalidation_coverage(receipts)
    return {
        **convergence_envelope_metadata(),
        "status": str(status or "unknown"),
        "converged": status in {"already_clean", "converged"},
        "final_error_count": len(tuple(final_diagnostics or ())),
        "post_check_evidence_complete": bool(revalidation_coverage["post_check_evidence_complete"]),
        "evidence_status_counts": dict(revalidation_coverage["evidence_status_counts"]),
        "missing_evidence_receipt_ids": list(revalidation_coverage["missing_evidence_receipt_ids"]),
        "missing_evidence_source_tools": list(revalidation_coverage["missing_evidence_source_tools"]),
        "failed_evidence_receipt_ids": list(revalidation_coverage["failed_evidence_receipt_ids"]),
        "failed_evidence_source_tools": list(revalidation_coverage["failed_evidence_source_tools"]),
        "resolved_evidence_receipt_ids": list(revalidation_coverage["resolved_evidence_receipt_ids"]),
        "resolved_evidence_source_tools": list(revalidation_coverage["resolved_evidence_source_tools"]),
        "revalidation_coverage": revalidation_coverage,
        "failed_revalidation_receipt_count": int(revalidation_coverage["failed_revalidation_receipt_count"]),
        "unconverged": status not in {"already_clean", "converged"},
        **dict(extra or {}),
    }


class RepairConvergenceScheduler:
    """Run deterministic repairs through Plan→Compose→Policy→Execute→Revalidate rounds."""

    def __init__(
        self,
        *,
        max_rounds: int = 3,
        composer: PatchComposer | None = None,
        executor: TransactionalRepairExecutor | None = None,
        policy_gate: RepairPolicyGate | None = None,
    ) -> None:
        self.max_rounds = max(1, int(max_rounds))
        self._composer = composer or PatchComposer()
        self._executor = executor or TransactionalRepairExecutor()
        self._policy_gate = policy_gate or RepairPolicyGate()

    def run(
        self,
        *,
        workspace: Path,
        verifier: VerifierFn,
        planner: PlannerFn,
        base_files_provider: BaseFilesProviderFn,
        writer: WriteFileFn | None = None,
        editor: EditFileFn | None = None,
        allowed_paths: Sequence[str] = (),
        previous_receipts: Sequence[RepairReceipt] = (),
    ) -> RepairConvergenceResult:
        prior_receipts = tuple(previous_receipts or ())
        before = verifier(0, prior_receipts)
        if not before.diagnostics:
            return RepairConvergenceResult(
                status="already_clean",
                final_diagnostics=(),
                receipts=prior_receipts,
                max_rounds=self.max_rounds,
                metadata=_scheduler_result_metadata("already_clean", receipts=prior_receipts),
            )

        seen_signatures = {before.diagnostic_signature()}
        all_receipts: list[RepairReceipt] = list(prior_receipts)
        completed_rule_ids = [receipt.rule_id for receipt in prior_receipts if receipt.status == "applied"]
        rounds: list[RepairRoundResult] = []
        current = before
        for round_number in range(1, self.max_rounds + 1):
            plans = tuple(planner(current.diagnostics, round_number))
            schedule = order_repair_plans(plans, completed_rule_ids=completed_rule_ids)
            if schedule.cycle_detected or schedule.blocked_rule_ids:
                return RepairConvergenceResult(
                    status="dependency_cycle_detected",
                    final_diagnostics=current.diagnostics,
                    rounds=tuple(rounds),
                    receipts=tuple(all_receipts),
                    max_rounds=self.max_rounds,
                    metadata=_scheduler_result_metadata(
                        "dependency_cycle_detected",
                        final_diagnostics=current.diagnostics,
                        receipts=all_receipts,
                        extra={
                            "stopped_reason": "dependency_cycle_detected",
                            "schedule": schedule.to_dict(),
                        },
                    ),
                )
            if not schedule.ordered_plans:
                return RepairConvergenceResult(
                    status="stuck_no_plans",
                    final_diagnostics=current.diagnostics,
                    rounds=tuple(rounds),
                    receipts=tuple(all_receipts),
                    max_rounds=self.max_rounds,
                    metadata=_scheduler_result_metadata(
                        "stuck_no_plans",
                        final_diagnostics=current.diagnostics,
                        receipts=all_receipts,
                        extra={"stopped_reason": "planner_returned_no_plans"},
                    ),
                )

            round_receipts = self._execute_round(
                workspace=workspace,
                plans=schedule.ordered_plans,
                base_files_provider=base_files_provider,
                writer=writer,
                editor=editor,
                allowed_paths=allowed_paths,
                previous_receipts=tuple(all_receipts),
                round_number=round_number,
            )
            if not any(receipt.status in {"applied", "shadow_observed"} for receipt in round_receipts):
                rounds.append(
                    RepairRoundResult(
                        round_number=round_number,
                        status="stuck_no_receipts",
                        schedule=schedule,
                        diagnostics_before=current.diagnostics,
                        diagnostics_after=current.diagnostics,
                        receipts=round_receipts,
                    )
                )
                all_receipts.extend(round_receipts)
                return RepairConvergenceResult(
                    status="stuck_no_receipts",
                    final_diagnostics=current.diagnostics,
                    rounds=tuple(rounds),
                    receipts=tuple(all_receipts),
                    max_rounds=self.max_rounds,
                    metadata=_scheduler_result_metadata(
                        "stuck_no_receipts",
                        final_diagnostics=current.diagnostics,
                        receipts=all_receipts,
                        extra={
                            "stopped_reason": "executed_round_produced_no_authoritative_receipts",
                            "round_receipt_statuses": [receipt.status for receipt in round_receipts],
                        },
                    ),
                )

            after = verifier(round_number, tuple(all_receipts + list(round_receipts)))
            evidence = RepairRevalidationEvidence(
                command=after.command,
                exit_code=after.exit_code,
                diagnostics_before=current.diagnostics,
                diagnostics_after=after.diagnostics,
                round_number=round_number,
                raw_output_ref=after.raw_output_ref,
                metadata=after.metadata,
            )
            evidenced_receipts = tuple(attach_revalidation_evidence(receipt, evidence) for receipt in round_receipts)
            all_receipts.extend(evidenced_receipts)
            completed_rule_ids.extend(
                receipt.rule_id for receipt in evidenced_receipts if receipt.status in {"applied", "shadow_observed"}
            )
            round_status = "converged" if not after.diagnostics else "progressed"
            rounds.append(
                RepairRoundResult(
                    round_number=round_number,
                    status=round_status,
                    schedule=schedule,
                    diagnostics_before=current.diagnostics,
                    diagnostics_after=after.diagnostics,
                    receipts=evidenced_receipts,
                    revalidation_evidence=evidence,
                )
            )
            if not after.diagnostics:
                return RepairConvergenceResult(
                    status="converged",
                    final_diagnostics=(),
                    rounds=tuple(rounds),
                    receipts=tuple(all_receipts),
                    max_rounds=self.max_rounds,
                    metadata=_scheduler_result_metadata("converged", receipts=all_receipts),
                )

            signature = after.diagnostic_signature()
            if signature in seen_signatures:
                return RepairConvergenceResult(
                    status="cycle_detected",
                    final_diagnostics=after.diagnostics,
                    rounds=tuple(rounds),
                    receipts=tuple(all_receipts),
                    max_rounds=self.max_rounds,
                    metadata=_scheduler_result_metadata(
                        "cycle_detected",
                        final_diagnostics=after.diagnostics,
                        receipts=all_receipts,
                        extra={
                            "stopped_reason": "repeated_diagnostic_signature",
                            "diagnostic_signature": signature,
                        },
                    ),
                )
            seen_signatures.add(signature)
            current = after

        return RepairConvergenceResult(
            status="max_rounds_exhausted",
            final_diagnostics=current.diagnostics,
            rounds=tuple(rounds),
            receipts=tuple(all_receipts),
            max_rounds=self.max_rounds,
            metadata=_scheduler_result_metadata(
                "max_rounds_exhausted",
                final_diagnostics=current.diagnostics,
                receipts=all_receipts,
                extra={"stopped_reason": "max_rounds_exhausted"},
            ),
        )

    def _execute_round(
        self,
        *,
        workspace: Path,
        plans: Sequence[RepairPlan],
        base_files_provider: BaseFilesProviderFn,
        writer: WriteFileFn | None,
        editor: EditFileFn | None,
        allowed_paths: Sequence[str],
        previous_receipts: tuple[RepairReceipt, ...],
        round_number: int,
    ) -> tuple[RepairReceipt, ...]:
        receipts: list[RepairReceipt] = []
        for plan in plans:
            context = RepairPolicyContext(
                allowed_paths=tuple(str(path or "").strip().replace("\\", "/") for path in allowed_paths),
                previous_receipts=previous_receipts + tuple(receipts),
            )
            plan_decision = self._policy_gate.evaluate_plan(plan, context)
            if not plan_decision.allowed:
                receipts.append(_policy_denied_receipt(plan, "plan_policy_denied", plan_decision, round_number))
                continue

            base_files = dict(base_files_provider(plan))
            composition = self._composer.compose(base_files, plan.operations)
            composition_decision = self._policy_gate.evaluate_composition(plan, composition)
            if not composition_decision.allowed:
                receipts.append(
                    _policy_denied_receipt(
                        plan,
                        "composition_policy_denied",
                        composition_decision,
                        round_number,
                        metadata={"composition": composition.to_dict()},
                    )
                )
                continue

            execution_result: RepairExecutionResult = self._executor.execute(
                workspace=workspace,
                plan=plan,
                composition=composition,
                writer=writer,
                editor=editor,
            )
            receipts.append(
                _with_round_number(
                    execution_result.receipt,
                    round_number,
                    metadata={"execution_error": execution_result.error, "rolled_back": execution_result.rolled_back},
                )
            )
        return tuple(receipts)


def _policy_denied_receipt(
    plan: RepairPlan,
    status: str,
    decision: PolicyDecision,
    round_number: int,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> RepairReceipt:
    payload = {"policy_decision": decision.to_dict(), **dict(metadata or {})}
    return build_receipt(plan=plan, status=status, mode=plan.mode, round_number=round_number, metadata=payload)


def _with_round_number(
    receipt: RepairReceipt,
    round_number: int,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> RepairReceipt:
    return RepairReceipt(
        receipt_id=receipt.receipt_id,
        plan_id=receipt.plan_id,
        rule_id=receipt.rule_id,
        source_tool=receipt.source_tool,
        status=receipt.status,
        mode=receipt.mode,
        authoritative=receipt.authoritative,
        files_changed=receipt.files_changed,
        operation_ids=receipt.operation_ids,
        diagnostics=receipt.diagnostics,
        before_hashes=receipt.before_hashes,
        after_hashes=receipt.after_hashes,
        round_number=round_number,
        revalidation_evidence=receipt.revalidation_evidence,
        advisor_notes=receipt.advisor_notes,
        metadata={**dict(receipt.metadata), **dict(metadata or {})},
    )


__all__ = [
    "CONVERGENCE_PIPELINE_ORDER",
    "CONVERGENCE_PIPELINE_STAGES",
    "BaseFilesProviderFn",
    "PlannerFn",
    "RepairConvergenceResult",
    "RepairConvergenceScheduler",
    "RepairPlanSchedule",
    "RepairRoundResult",
    "RepairVerifierSnapshot",
    "VerifierFn",
    "convergence_envelope_metadata",
    "order_repair_plans",
]
