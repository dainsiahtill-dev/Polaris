"""Cognitive Life Form Governance Gate - Left-Shifted (Phase 0 Built).

This gate validates cognitive life form compliance at every phase.
Phase 0: Just verify VC schema exists
Phase 1+: Run actual validation against test suites
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GateResult:
    status: str  # PASS | FAIL
    phase: str
    message: str
    details: dict = None


def check_vc_schema_exists(workspace: Path) -> GateResult:
    """Phase 0 check: Verify VC schema exists and is valid YAML."""
    vc_schema = workspace / "docs/governance/schemas/cognitive-protocol-vc.schema.yaml"

    if not vc_schema.exists():
        return GateResult(
            status="FAIL",
            phase="Phase 0",
            message="Cognitive VC schema not found",
            details={"expected_path": str(vc_schema)},
        )

    # Basic YAML validation - check it has expected structure
    try:
        content = vc_schema.read_text(encoding="utf-8")
        if "cognitive_protocol_checklist" not in content:
            return GateResult(
                status="FAIL",
                phase="Phase 0",
                message="VC schema missing cognitive_protocol_checklist",
            )
    except Exception as e:
        return GateResult(
            status="FAIL",
            phase="Phase 0",
            message=f"VC schema read error: {e}",
        )

    return GateResult(
        status="PASS",
        phase="Phase 0",
        message="Cognitive governance infrastructure ready",
        details={"schema_path": str(vc_schema)},
    )


def check_perception_phase(workspace: Path) -> GateResult:
    """Phase 1: Check perception layer exists."""
    perception_init = workspace / "polaris/kernelone/cognitive/perception/__init__.py"

    if not perception_init.exists():
        return GateResult(
            status="FAIL",
            phase="Phase 1",
            message="Perception layer not found",
        )

    return GateResult(
        status="PASS",
        phase="Phase 1",
        message="Perception layer infrastructure exists",
    )


def check_reasoning_phase(workspace: Path) -> GateResult:
    """Phase 2: Check reasoning layer exists."""
    reasoning_init = workspace / "polaris/kernelone/cognitive/reasoning/__init__.py"

    if not reasoning_init.exists():
        return GateResult(
            status="FAIL",
            phase="Phase 2",
            message="Reasoning layer not found",
        )

    return GateResult(
        status="PASS",
        phase="Phase 2",
        message="Reasoning layer infrastructure exists",
    )


def check_execution_phase(workspace: Path) -> GateResult:
    """Phase 3-4: Check execution layer exists."""
    execution_init = workspace / "polaris/kernelone/cognitive/execution/__init__.py"

    if not execution_init.exists():
        return GateResult(
            status="FAIL",
            phase="Phase 3-4",
            message="Execution layer not found",
        )

    return GateResult(
        status="PASS",
        phase="Phase 3-4",
        message="Execution layer infrastructure exists",
    )


def check_evolution_phase(workspace: Path) -> GateResult:
    """Phase 5: Check evolution layer exists."""
    evolution_init = workspace / "polaris/kernelone/cognitive/evolution/__init__.py"

    if not evolution_init.exists():
        return GateResult(
            status="FAIL",
            phase="Phase 5",
            message="Evolution layer not found",
        )

    return GateResult(
        status="PASS",
        phase="Phase 5",
        message="Evolution layer infrastructure exists",
    )


def check_orchestrator_integration(workspace: Path) -> GateResult:
    """Phase 8: Check orchestrator can be imported and basic functionality."""
    try:
        import sys

        sys.path.insert(0, str(workspace / "src" / "backend"))
        from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator

        # Basic instantiation check
        orch = CognitiveOrchestrator()
        return GateResult(
            status="PASS",
            phase="Phase 8",
            message="CognitiveOrchestrator integration ready",
            details={"orchestrator_class": "CognitiveOrchestrator"},
        )
    except Exception as e:
        return GateResult(
            status="FAIL",
            phase="Phase 8",
            message=f"Orchestrator integration failed: {e}",
        )


def check_personality_integration(workspace: Path) -> GateResult:
    """Phase 7: Check personality integration exists."""
    personality_init = workspace / "polaris/kernelone/cognitive/personality/__init__.py"
    integrator_file = workspace / "polaris/kernelone/cognitive/personality/integrator.py"

    if not personality_init.exists():
        return GateResult(
            status="FAIL",
            phase="Phase 7",
            message="Personality layer not found",
        )

    if not integrator_file.exists():
        return GateResult(
            status="FAIL",
            phase="Phase 7",
            message="PersonalityIntegrator not found",
        )

    return GateResult(
        status="PASS",
        phase="Phase 7",
        message="Personality integration exists",
    )


def run_cognitive_gate(workspace: Path, mode: str) -> dict:
    """
    Run cognitive life form governance gate.

    Phase 0 gates are always run (left-shifted governance).
    Later phase gates run based on mode setting.
    """
    results = []

    # Phase 0: Always check - infrastructure
    results.append(check_vc_schema_exists(workspace))

    if mode in ("all", "phase1"):
        results.append(check_perception_phase(workspace))

    if mode in ("all", "phase2"):
        results.append(check_reasoning_phase(workspace))

    if mode in ("all", "phase3"):
        results.append(check_execution_phase(workspace))

    if mode in ("all", "phase5"):
        results.append(check_evolution_phase(workspace))

    if mode in ("all", "phase7"):
        results.append(check_personality_integration(workspace))

    if mode in ("all", "phase8"):
        results.append(check_orchestrator_integration(workspace))

    # Aggregate results
    failures = [r for r in results if r.status == "FAIL"]

    return {
        "gate": "cognitive_life_form",
        "mode": mode,
        "total_checks": len(results),
        "passed": len(results) - len(failures),
        "failed": len(failures),
        "results": [
            {
                "phase": r.phase,
                "status": r.status,
                "message": r.message,
                "details": r.details or {},
            }
            for r in results
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Cognitive Life Form governance gate.")
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root path (default: current directory).",
    )
    parser.add_argument(
        "--mode",
        default="all",
        choices=["all", "phase0", "phase1", "phase2", "phase3", "phase5", "phase7", "phase8"],
        help="Gate mode: all phases or specific phase.",
    )
    parser.add_argument(
        "--report",
        default="",
        help="Optional path to write gate JSON report.",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    result = run_cognitive_gate(workspace, args.mode)

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)

    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = workspace / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")

    # Fail if any checks failed
    if result["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
