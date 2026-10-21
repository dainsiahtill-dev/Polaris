"""Internal engineering acceptance adapter; legacy writers are retired.

Private helper module for run_factory_bench.
"""

from __future__ import annotations

# ruff: noqa: E402


def _pull_namespace(module: object) -> None:
    """Copy non-dunder attributes into this module (private helpers + imports)."""
    g = globals()
    for key, value in vars(module).items():
        if key.startswith("__"):
            continue
        g[key] = value


from scripts.factory_bench._bench_lib import gates as _gates

_pull_namespace(_gates)
del _gates


import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from polaris.delivery.cli import engineering_acceptance as acceptance
from scripts.factory_bench._bench_lib.gates import build_requirements_doc


def prepare_engineering_case(
    project: dict[str, Any],
    *,
    workspace: Path,
    evidence_dir: Path,
    catalog_file: Path,
    host_config_file: Path,
    llm_config_file: Path,
    instance_id: str,
    registry_home: Path | None = None,
    permission_profile_id: str | None = None,
    checks_file: Path | None = None,
    workflow: str = "direct",
    polaris_root: Path | None = None,
    timeout: int = 5400,
) -> acceptance.LaunchInputs:
    """Freeze goal inputs outside target; never prepare target files or grants."""
    workspace, evidence_dir = workspace.resolve(), evidence_dir.resolve()
    if evidence_dir == workspace or evidence_dir.is_relative_to(workspace):
        raise ValueError("evidence must remain outside the target")
    canonical_id = str(project.get("canonical_catalog_project_id") or project["id"])
    catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    matches = [row for row in catalog["projects"] if row.get("id") == canonical_id]
    if len(matches) != 1:
        raise ValueError("case must identify exactly one original catalog project")
    original = matches[0]
    evidence_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    requirements_file = evidence_dir / "original-requirements.md"
    # Exclusive files retain first-attempt inputs instead of rewriting a claim.
    with requirements_file.open("x", encoding="utf-8") as stream:
        stream.write(build_requirements_doc(original))
    if checks_file is None:
        checks_file = evidence_dir / "user-checks.json"
        with checks_file.open("x", encoding="utf-8") as stream:
            stream.write("[]\n")
    frozen = {
        "catalog": catalog_file.resolve(),
        "requirements": requirements_file,
        "checks": checks_file.resolve(),
        "host_config": host_config_file.resolve(),
        "llm_config": llm_config_file.resolve(),
    }
    values: dict[str, Any] = {}
    for label, path in frozen.items():
        values[f"{label}_file"] = path
        values[f"{label}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return acceptance.LaunchInputs(
        polaris_root=polaris_root or Path(__file__).resolve().parents[5],
        workspace=workspace,
        evidence_dir=evidence_dir,
        project_id=canonical_id,
        instance_id=instance_id,
        registry_home=registry_home,
        permission_profile_id=permission_profile_id,
        workflow=workflow,
        timeout=timeout,
        **values,
    )


def run_engineering_case(
    inputs: acceptance.LaunchInputs,
    *,
    supervisor: acceptance.SupervisorPort | None = None,
    http_factory: acceptance.HTTPFactory | None = None,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    activity_reader: Callable[[Path], dict[str, Any]] | None = None,
    process_probe: Callable[[int], bool | None] | None = None,
) -> dict[str, Any]:
    """One public launch, bounded observation, explicit cancellation and stop fence.

    Synthetic ports are only dependency injection for internal tests. Ordinary
    execution consumes the adapter's original Supervisor/HTTP implementations.
    Reports observe product facts; they never become engineering authority.
    """
    ports: dict[str, Any] = {"supervisor": supervisor}
    http_options: dict[str, Any] = {"supervisor": supervisor}
    if http_factory is not None:
        ports["http_factory"] = http_factory
        http_options["http_factory"] = http_factory
    launched = acceptance.launch(inputs, now=now, **ports)
    report: dict[str, Any] = {
        "internal_test_only": True,
        "delivery_proven": False,
        "catalog_quality": "UNKNOWN",
        "project_id": inputs.project_id,
        "workspace": str(inputs.workspace),
        "workflow": inputs.workflow,
        "launch": launched,
        "project_budget_seconds": 5400,
        "overall_budget_seconds": 6000,
        "acceptance_authority": "EngineeringRunService; external report is non-authoritative",
    }
    binding = inputs.evidence_dir / "binding.json"
    observation: dict[str, Any] = {}
    if launched.get("harness_status") in {"LAUNCHED", "UNKNOWN"}:
        try:
            observation = acceptance.observe(binding, wait=True, now=now, sleep=sleep, **http_options)
        except Exception as error:  # noqa: BLE001 - boundary failure cannot bypass resource reconciliation.
            observation = {"harness_status": "UNKNOWN", "exit_code": 2, "error_type": type(error).__name__}
        report["observation"] = observation
        if observation.get("harness_status") == "TIMEOUT":
            try:
                report["cancel"] = acceptance.cancel(binding, **http_options)
            except Exception as error:  # noqa: BLE001 - unknown mutation is never replayed.
                report["cancel"] = {
                    "harness_status": "UNKNOWN",
                    "exit_code": 2,
                    "error_type": type(error).__name__,
                    "cancel_replayed": False,
                }
            try:
                report["final_observation"] = acceptance.observe(binding, now=now, **http_options)
            except Exception as error:  # noqa: BLE001 - retain unknown and continue to the shared stop fence.
                report["final_observation"] = {
                    "harness_status": "UNKNOWN",
                    "exit_code": 2,
                    "error_type": type(error).__name__,
                }
    stop_options = dict(ports)
    if activity_reader is not None:
        stop_options["activity_reader"] = activity_reader
    if process_probe is not None:
        stop_options["process_probe"] = process_probe
    # Adapter alone reconciles partial launch and unknown effects. Never guess a
    # run ID or bypass its stop fence, even if a batch cannot safely continue.
    try:
        cleanup = acceptance.stop(binding, **stop_options)
    except Exception as error:  # noqa: BLE001 - retain the failed fence without custom cleanup or replay.
        cleanup = {
            "harness_status": "UNKNOWN",
            "exit_code": 2,
            "error_type": type(error).__name__,
            "stop_replayed": False,
        }
    report["cleanup"] = cleanup
    report["resources_closed"] = cleanup.get("harness_status") == "STOPPED"
    report["exit_code"] = (
        124
        if observation.get("harness_status") == "TIMEOUT"
        else 0
        if observation.get("harness_status") == "OBSERVED_ACCEPTED" and report["resources_closed"]
        else int(observation.get("exit_code") or launched.get("exit_code") or 2)
    )
    return report


def run_factory_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Historical import seam; old Factory write execution is retired."""
    raise RuntimeError("run_factory_chain is retired; use run_engineering_case with frozen LaunchInputs")


def run_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Historical import seam; role subprocess execution is retired."""
    raise RuntimeError("run_chain is retired; use run_engineering_case with frozen LaunchInputs")
