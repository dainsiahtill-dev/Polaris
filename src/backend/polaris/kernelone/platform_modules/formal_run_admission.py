"""Machine-checkable pre-bench / formal-run admission for the critical path.

Does not start Factory Bench.  Returns a structured readiness report so
supervisors cannot invent COMPLETED_VERIFIED without gate evidence.
"""

from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FormalRunAdmissionV1:
    schema_version: str
    ok: bool
    blockers: tuple[str, ...]
    checks: dict[str, bool]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _import_ok(module: str, attr: str) -> bool:
    try:
        mod = importlib.import_module(module)
        return hasattr(mod, attr)
    except (ImportError, AttributeError, ModuleNotFoundError):
        return False


def evaluate_formal_run_admission() -> FormalRunAdmissionV1:
    """Evaluate critical-path surfaces required before an isolated L1 probe."""

    checks: dict[str, bool] = {
        "managed_process_orchestrator": _import_ok(
            "polaris.cells.runtime.execution_broker.public",
            "run_managed_process",
        ),
        "managed_process_lifecycle_projection": _import_ok(
            "polaris.cells.control_plane.run_ledger.public",
            "project_managed_process_lifecycle",
        ),
        "audit_evidence_receipt_owner": _import_ok(
            "polaris.cells.audit.evidence.public",
            "persist_managed_process_receipt",
        ),
        "residual_attribution": _import_ok(
            "polaris.kernelone.platform_modules",
            "attribute_residual",
        ),
        "unattended_step_planner": _import_ok(
            "polaris.kernelone.platform_modules",
            "plan_unattended_step",
        ),
        "factory_bench_runner": _import_ok(
            "scripts.factory_bench.run_factory_bench",
            "main",
        )
        or _import_ok("scripts.factory_bench", "run_factory_bench"),
    }
    # runner path may not import as package; check file existence via pathlib
    from pathlib import Path

    # formal_run_admission.py → platform_modules → kernelone → polaris → backend → src → repo
    here = Path(__file__).resolve()
    candidates = [
        here.parents[5] / "src" / "backend" / "scripts" / "factory_bench" / "run_factory_bench.py",
        here.parents[4] / "scripts" / "factory_bench" / "run_factory_bench.py",
        here.parents[3] / "scripts" / "factory_bench" / "run_factory_bench.py",
    ]
    if not checks["factory_bench_runner"]:
        checks["factory_bench_runner"] = any(path.is_file() for path in candidates)

    blockers = tuple(name for name, ok in checks.items() if not ok)
    notes = (
        "formal_run_admission_is_not_completed_verified",
        "l1_true_run_still_required_for_delivery_evidence",
        "no_external_agent_report_is_platform_ssot",
    )
    return FormalRunAdmissionV1(
        schema_version="platform.formal_run_admission.v1",
        ok=not blockers,
        blockers=blockers,
        checks=checks,
        notes=notes,
    )


__all__ = ["FormalRunAdmissionV1", "evaluate_formal_run_admission"]
