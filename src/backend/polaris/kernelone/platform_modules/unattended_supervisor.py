"""Pure next-step planner for unattended L1 solidification loops.

Does not run LLM, Factory, or pytest itself. Consumers (CLI, outer agent
supervisor) execute the returned gate commands. Platform success conditions
remain module gates + cascade + four pillars — never external agent reports.

Complexity: O(1) decision over attribution + cascade readiness flags.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from polaris.kernelone.platform_modules.residual_attribution import (
    ResidualAttributionV1,
    attribute_factory_audit_record,
    attribute_residual,
)


@dataclass(frozen=True, slots=True)
class UnattendedStepPlanV1:
    """One unattended loop step: what to run next and what is forbidden."""

    schema_version: str
    phase: str
    primary_module_id: str
    commands: tuple[str, ...]
    allow_l1_01_bench: bool
    allow_l1_02: bool
    stop: bool
    stop_reason: str
    attribution: dict[str, Any]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_unattended_step(
    *,
    attribution: ResidualAttributionV1 | Mapping[str, Any] | None = None,
    cascade_ok: bool | None = None,
    module_gate_ok: bool | None = None,
    last_l1_01_four_pillars_ok: bool | None = None,
    n_batch_streak: int = 0,
    n_batch_required: int = 3,
    consecutive_same_phase_residual_shifts: int = 0,
) -> UnattendedStepPlanV1:
    """Plan the next unattended action under the five hard modular rules."""

    if attribution is None:
        attr = attribute_residual()
    elif isinstance(attribution, ResidualAttributionV1):
        attr = attribution
    else:
        attr = ResidualAttributionV1(
            schema_version=str(attribution.get("schema_version") or "platform.residual_attribution.v1"),
            primary_module_id=str(attribution.get("primary_module_id") or "M07_factory_stage_chain"),
            defect_subtype=str(attribution.get("defect_subtype") or "unknown"),
            root_cause_signature=str(attribution.get("root_cause_signature") or ""),
            failure_category=str(attribution.get("failure_category") or "control_plane"),
            ladder_matched_hints=tuple(attribution.get("ladder_matched_hints") or ()),
            forbidden_same_round=tuple(attribution.get("forbidden_same_round") or ()),
            gate_commands=tuple(attribution.get("gate_commands") or ()),
            is_model_ceiling=bool(attribution.get("is_model_ceiling")),
            delivery_status=str(attribution.get("delivery_status") or "STATUS_UNKNOWN"),
            preconditions=dict(attribution.get("preconditions") or {}),
            status=str(attribution.get("status") or "attributed"),
            next_action=str(attribution.get("next_action") or ""),
            evidence_notes=tuple(attribution.get("evidence_notes") or ()),
        )

    if attr.is_model_ceiling:
        return UnattendedStepPlanV1(
            schema_version="platform.unattended_step.v1",
            phase="model_ceiling",
            primary_module_id=attr.primary_module_id,
            commands=(),
            allow_l1_01_bench=False,
            allow_l1_02=False,
            stop=True,
            stop_reason="model_ceiling: do not expand platform rules; rebind model or forced tools",
            attribution=attr.to_dict(),
            notes=("stop_expanding_m10", "forced_tool_surface_or_stronger_model"),
        )

    if consecutive_same_phase_residual_shifts >= 3 and attr.primary_module_id.startswith("M10"):
        return UnattendedStepPlanV1(
            schema_version="platform.unattended_step.v1",
            phase="stop_m10_thrash",
            primary_module_id=attr.primary_module_id,
            commands=(),
            allow_l1_01_bench=False,
            allow_l1_02=False,
            stop=True,
            stop_reason="three residual class shifts under M10 — move to prevention, not new source_tools",
            attribution=attr.to_dict(),
            notes=("prevention_over_repair", "coverage_first"),
        )

    if last_l1_01_four_pillars_ok is True and n_batch_streak >= n_batch_required:
        return UnattendedStepPlanV1(
            schema_version="platform.unattended_step.v1",
            phase="l1_01_n_batch_ready",
            primary_module_id=attr.primary_module_id,
            commands=(),
            allow_l1_01_bench=False,
            allow_l1_02=True,
            stop=False,
            stop_reason="",
            attribution=attr.to_dict(),
            notes=("may_consider_l1_02_or_seal_hardening", f"n_batch_streak={n_batch_streak}"),
        )

    if module_gate_ok is not True:
        return UnattendedStepPlanV1(
            schema_version="platform.unattended_step.v1",
            phase="module_gate",
            primary_module_id=attr.primary_module_id,
            commands=(attr.gate_commands[0],) if attr.gate_commands else (),
            allow_l1_01_bench=False,
            allow_l1_02=False,
            stop=False,
            stop_reason="",
            attribution=attr.to_dict(),
            notes=("only_module_gate_first",),
        )

    if cascade_ok is not True:
        cascade_cmd = (
            attr.gate_commands[1]
            if len(attr.gate_commands) > 1
            else "python src/backend/scripts/platform_modules/run_module_gates.py --mode cascade"
        )
        return UnattendedStepPlanV1(
            schema_version="platform.unattended_step.v1",
            phase="cascade",
            primary_module_id=attr.primary_module_id,
            commands=(cascade_cmd,),
            allow_l1_01_bench=False,
            allow_l1_02=False,
            stop=False,
            stop_reason="",
            attribution=attr.to_dict(),
            notes=("cascade_before_bench",),
        )

    # Cascade green: one isolated L1-01 allowed.
    return UnattendedStepPlanV1(
        schema_version="platform.unattended_step.v1",
        phase="isolated_l1_01",
        primary_module_id=attr.primary_module_id,
        commands=(
            "python src/backend/scripts/factory_bench/run_factory_bench.py "
            "--project-ids L1-01 "
            "--work-dir /tmp/factory-bench-l1-01-unattended "
            "--timeout 5400 --max-failed 0 --real-run-timeout 120 "
            "--launcher-instance-mode isolated --bench-session-reporting off",
        ),
        allow_l1_01_bench=True,
        allow_l1_02=False,
        stop=False,
        stop_reason="",
        attribution=attr.to_dict(),
        notes=("one_bench_only_after_cascade", "no_l1_02_until_n_batch"),
    )


def plan_from_factory_audit_record(
    record: Mapping[str, Any],
    **kwargs: Any,
) -> UnattendedStepPlanV1:
    """Attribute a factory audit record then plan the next step."""

    return plan_unattended_step(attribution=attribute_factory_audit_record(record), **kwargs)


__all__ = [
    "UnattendedStepPlanV1",
    "plan_from_factory_audit_record",
    "plan_unattended_step",
]
