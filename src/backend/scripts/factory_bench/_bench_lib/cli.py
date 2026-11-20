"""Argparse CLI entry (main) for factory-bench runner.

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


from scripts.factory_bench._bench_lib import chain as _chain

_pull_namespace(_chain)
del _chain


import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from polaris.delivery.cli.engineering_acceptance import _inventory
from scripts.factory_bench._bench_lib import comparison
from scripts.factory_bench._bench_lib.catalog import (
    _resolve_explicit_project_selection,
    load_projects,
)
from scripts.factory_bench._bench_lib.catalog_quality import catalog_quality_report
from scripts.factory_bench._bench_lib.chain import prepare_engineering_case, run_engineering_case
from scripts.factory_bench._bench_lib.constants import _FIXTURE
from scripts.factory_bench._bench_lib.reliability import batch_reliability, repeated_delivery
from scripts.factory_bench._bench_lib.routing_evidence import routing_witness

_RETIRED_OPTIONS = frozenset(
    {
        "--use-legacy-chain",
        "--director-workflow-execution-mode",
        "--director-dispatch-driver",
        "--start-from",
        "--real-run-timeout",
        "--launcher-supervisor-url",
    }
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal isolated engineering acceptance runner")
    parser.add_argument("--project-ids", "--case", default="")
    parser.add_argument("--levels", default="1,2,3,4,5,6,7,8,9,10,11,12")
    parser.add_argument("--projects-file", type=Path, default=Path(_FIXTURE))
    parser.add_argument("--work-dir", type=Path, default=Path.home() / ".polaris" / "internal-engineering-bench")
    parser.add_argument("--timeout", type=int, default=5400)
    parser.add_argument("--overall-timeout", type=int, default=6000)
    parser.add_argument("--max-failed", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workflow", choices=("direct", "collaborative"), default="direct")
    parser.add_argument("--host-config-file", type=Path)
    parser.add_argument("--llm-config-file", type=Path)
    parser.add_argument("--registry-home", type=Path)
    parser.add_argument("--permission-profile-id")
    parser.add_argument("--checks-file", type=Path, help="Explicit user shell recipes; catalog codes remain in goal")
    parser.add_argument("--launcher-instance-mode", choices=("isolated",), default="isolated")
    parser.add_argument("--bench-session-reporting", choices=("off",), default="off")
    parser.add_argument(
        "--qa-semantic-review-required",
        action="store_true",
        help="Independent QA is always required by frozen host config",
    )
    parser.add_argument("--comparison-plan", type=Path, help="Predeclared held-out project pair and thresholds")
    parser.add_argument("--comparison-arm", choices=comparison.ARMS)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _observed_catalog_quality(project: dict[str, Any], workspace: Path, record: dict[str, Any]) -> dict[str, Any]:
    observation = record.get("final_observation") or record.get("observation") or {}
    expected = observation.get("artifact_inventory")
    if record.get("resources_closed") is not True or not expected or expected.get("status") != "read_only_observation":
        return {"catalog_quality": "UNKNOWN", "catalog_quality_detail": {"reason": "settled inventory unavailable"}}
    try:
        if _inventory(workspace) == expected:
            result = catalog_quality_report(project, workspace)
            if result.get("verifier_resources_closed") is False or _inventory(workspace) == expected:
                return result
    except OSError as error:
        return {"catalog_quality": "UNKNOWN", "catalog_quality_detail": {"reason": type(error).__name__}}
    return {"catalog_quality": "UNKNOWN", "catalog_quality_detail": {"reason": "terminal artifact inventory changed"}}


def _bench_gate(record: dict[str, Any]) -> str:
    if record.get("harness_status") == "NOT_RUN":
        return "NOT_RUN"
    status = (record.get("observation") or {}).get("harness_status")
    if status == "REJECTED" or record.get("catalog_quality") == "FAIL":
        return "FAIL"
    if (
        record.get("exit_code") == 0
        and record.get("resources_closed") is True
        and status == "OBSERVED_ACCEPTED"
        and record.get("catalog_quality") == "PASS"
    ):
        return "PASS"
    return "UNKNOWN"


def _write_summary(directory: Path, report: dict[str, Any]) -> None:
    report["reliability"] = batch_reliability(report["selected_project_ids"], report["records"])
    path = directory / "summary.json"
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    scratch.replace(path)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["repeatability"]:
        try:
            batches = [json.loads(Path(path).read_text(encoding="utf-8")) for path in argv[1:]]
            print(json.dumps(repeated_delivery(batches), ensure_ascii=False, indent=2))
        except (KeyError, TypeError, ValueError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 2
        return 0
    if argv[:1] == ["compare"]:
        return comparison.comparison_cli(argv[1:])
    if argv[:1] == ["artifact-fingerprint"] and len(argv) == 3:
        batch = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        matches = [r for r in batch["records"] if r["project_id"] == argv[2]]
        if len(matches) != 1:
            print("expected exactly one project observation", file=sys.stderr)
            return 2
        observation = matches[0].get("final_observation") or matches[0].get("observation") or {}
        print(comparison.artifact_digest(observation.get("artifact_inventory") or {}))
        return 0
    if argv[:1] == ["arm-identity"]:
        identity_parser = argparse.ArgumentParser(description="Fingerprint the frozen launch host policy")
        identity_parser.add_argument("--host-config-file", type=Path, required=True)
        identity_parser.add_argument("--host-treatment")
        identity_args = identity_parser.parse_args(argv[1:])
        print(
            json.dumps(comparison.arm_identity(identity_args.host_config_file, identity_args.host_treatment), indent=2)
        )
        return 0
    if argv[:1] == ["evidence-fingerprint"] and len(argv) == 2:
        print(comparison.evidence_fingerprint(Path(argv[1])))
        return 0
    retired = sorted({word.split("=", 1)[0] for word in argv} & _RETIRED_OPTIONS)
    if retired:
        print("retired execution/repair option(s): " + ", ".join(retired), file=sys.stderr)
        return 2
    parser = _parser()
    args = parser.parse_args(argv)
    report: dict[str, Any] | None = None
    directory: Path | None = None
    pending_record: dict[str, Any] | None = None
    try:
        if args.timeout != 5400 or args.overall_timeout != 6000:
            raise ValueError("original budgets required: project5400 / overall6000 seconds")
        if args.limit < 0 or args.max_failed < 0:
            raise ValueError("limit and max-failed must be nonnegative")
        catalog = args.projects_file.resolve()
        catalog_bytes = catalog.read_bytes()
        projects = load_projects(catalog)
        if args.project_ids.strip():
            selected, missing, aliases = _resolve_explicit_project_selection(
                projects, [value.strip() for value in args.project_ids.split(",") if value.strip()]
            )
            if missing:
                raise ValueError("unknown or duplicate project id(s): " + ", ".join(missing))
        else:
            levels = {int(value) for value in args.levels.split(",") if value.strip()}
            if not levels or levels - set(range(1, 13)):
                raise ValueError("levels must be within1..12")
            selected, aliases = [p for p in projects if int(p["level"]) in levels], {}
        if args.limit:
            selected = selected[: args.limit]
        if not selected:
            raise ValueError("nothing selected")
        if not args.dry_run and (args.host_config_file is None or args.llm_config_file is None):
            raise ValueError("explicit frozen --host-config-file and --llm-config-file required")
        frozen_files = {"catalog": catalog}
        if not args.dry_run:
            frozen_files.update(host_config=args.host_config_file.resolve(), llm_config=args.llm_config_file.resolve())
            if args.checks_file is not None:
                frozen_files["checks"] = args.checks_file.resolve()
        frozen_inputs = {
            label: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for label, path in frozen_files.items()
        }
        registration = None
        setup = None
        plan = None
        if args.comparison_plan or args.comparison_arm:
            if not (args.comparison_plan and args.comparison_arm) or args.dry_run or args.max_failed:
                raise ValueError("paired execution requires both plan and arm and a fixed sample")
            plan, plan_sha = comparison.load_plan(args.comparison_plan)
            canonical_ids = {str(p["id"]) for p in projects}
            if not set(plan["development"] + plan["calibration"] + plan["holdout"]) <= canonical_ids:
                raise ValueError("splits must identify canonical projects in the frozen catalog")
            setup = plan["arms"][args.comparison_arm]
            registration = {**setup, "arm": args.comparison_arm, "plan_sha256": plan_sha}
            arm_settings = {k: v for k, v in setup.items() if k != "batch_id"}
            if comparison.arm_identity(args.host_config_file, plan.get("host_treatment")) != arm_settings:
                raise ValueError("local arm source or policy differs from preregistration")
        batch_id = setup["batch_id"] if setup else uuid4().hex
        base = args.work_dir.expanduser().resolve()
        destination = base / "reports" / batch_id

        source_files = (
            Path(__file__),
            Path(__file__).with_name("chain.py"),
            Path(__file__).with_name("catalog_quality.py"),
            Path(__file__).with_name("reliability.py"),
            Path(__file__).with_name("comparison.py"),
            Path(__file__).with_name("routing_evidence.py"),
            Path(__file__).parents[3] / "polaris" / "delivery" / "cli" / "engineering_acceptance.py",
        )
        report = {
            "internal_test_only": True,
            "delivery_proven": False,
            "batch_id": batch_id,
            "catalog_file": str(catalog),
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "catalog_total": len(projects),
            "selected_count": len(selected),
            "selected_project_ids": [str(p.get("canonical_catalog_project_id") or p["id"]) for p in selected],
            "aliases": aliases,
            "project_budget_seconds": 5400,
            "overall_budget_seconds": 6000,
            "workflow": args.workflow,
            "permission_profile_id": args.permission_profile_id,
            "frozen_inputs": frozen_inputs,
            "catalog_quality": "UNKNOWN",
            "source_hashes": {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
            "records": [],
        }
        if registration and plan:
            comparison.check_inputs(plan, report, args.comparison_arm)
            report["comparison"] = registration
        # Acquire a fresh report path before enabling partial-report writes.
        destination.mkdir(parents=True, mode=0o700)
        directory = destination
        _write_summary(directory, report)
        failed = 0
        for project in selected:
            try:
                changed = [
                    label
                    for label, frozen in frozen_inputs.items()
                    if hashlib.sha256(Path(frozen["path"]).read_bytes()).hexdigest() != frozen["sha256"]
                ]
            except OSError as error:
                changed = [type(error).__name__]
            if changed:
                report["batch_error"] = {
                    "harness_status": "UNKNOWN",
                    "exit_code": 2,
                    "reason": "frozen inputs changed",
                    "changed_inputs": changed,
                }
                _write_summary(directory, report)
                print(str(directory / "summary.json"))
                return 2
            canonical_id = str(project.get("canonical_catalog_project_id") or project["id"])
            case_dir = directory / canonical_id
            case_dir.mkdir(mode=0o700)
            workspace = base / "workspaces" / batch_id / canonical_id
            record: dict[str, Any] = {
                "project_id": canonical_id,
                "requested_project_id": project["id"],
                "original_project": next(p for p in projects if p["id"] == canonical_id),
                "workspace": str(workspace),
            }
            if registration and workspace.exists():
                raise ValueError("paired projects must start from fresh empty workspaces")
            pending_record = record
            if args.dry_run:
                record.update(harness_status="NOT_RUN", exit_code=0, resources_closed=True)
            else:
                inputs = prepare_engineering_case(
                    project,
                    workspace=workspace,
                    evidence_dir=case_dir,
                    catalog_file=catalog,
                    host_config_file=args.host_config_file,
                    llm_config_file=args.llm_config_file,
                    instance_id=f"engineering-{batch_id[:12]}-{canonical_id.lower()}",
                    registry_home=args.registry_home,
                    permission_profile_id=args.permission_profile_id,
                    checks_file=args.checks_file,
                    workflow=args.workflow,
                    timeout=args.timeout,
                )
                record.update(run_engineering_case(inputs))
                record.update(_observed_catalog_quality(project, workspace, record))
                if record.get("verifier_resources_closed") is False:
                    record["resources_closed"] = False
            if plan and plan.get("host_treatment") == "routing_record" and setup:
                observation = record.get("final_observation") or record.get("observation") or {}
                record["routing_witness"] = routing_witness(case_dir, observation, setup.get("routing_record_sha256"))
            if plan and comparison.arm_identity(args.host_config_file, plan.get("host_treatment")) != arm_settings:
                raise ValueError("paired arm source or policy changed during execution")
            record["bench_gate"] = _bench_gate(record)
            report["records"].append(record)
            pending_record = None
            _write_summary(directory, report)
            if record["bench_gate"] not in {"PASS", "NOT_RUN"}:
                failed += 1
            # Uncertain resources never overlap the next project, regardless of
            # ordinary rejected-project early-stop preference.
            if not record["resources_closed"] or (args.max_failed and failed >= args.max_failed):
                break
        print(str(directory / "summary.json"))
        return 1 if failed else 0
    except Exception as error:  # noqa: BLE001 - external report retains partial attempts without replay.
        if report is not None and directory is not None:
            report["batch_error"] = {"harness_status": "UNKNOWN", "exit_code": 2, "error_type": type(error).__name__}
            if pending_record is not None:
                pending_record.update(
                    harness_status="UNKNOWN",
                    bench_gate="UNKNOWN",
                    exit_code=2,
                    resources_closed=False,
                    error_type=type(error).__name__,
                )
                report["records"].append(pending_record)
            _write_summary(directory, report)
        print(f"engineering bench input/execution error: {error}", file=sys.stderr)
        return 2
