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

from scripts.factory_bench._bench_lib.catalog import (
    _resolve_explicit_project_selection,
    load_projects,
)
from scripts.factory_bench._bench_lib.chain import prepare_engineering_case, run_engineering_case
from scripts.factory_bench._bench_lib.constants import _FIXTURE

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
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
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
        batch_id = uuid4().hex
        base = args.work_dir.expanduser().resolve()
        directory = base / "reports" / batch_id
        directory.mkdir(parents=True, mode=0o700)
        source_files = (
            Path(__file__),
            Path(__file__).with_name("chain.py"),
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
            "aliases": aliases,
            "project_budget_seconds": 5400,
            "overall_budget_seconds": 6000,
            "workflow": args.workflow,
            "frozen_inputs": frozen_inputs,
            "catalog_quality": "UNKNOWN",
            "source_hashes": {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
            "records": [],
        }
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
                (directory / "summary.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
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
            report["records"].append(record)
            pending_record = None
            (directory / "summary.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if record["exit_code"]:
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
                    harness_status="UNKNOWN", exit_code=2, resources_closed=False, error_type=type(error).__name__
                )
                report["records"].append(pending_record)
            (directory / "summary.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(f"engineering bench input/execution error: {error}", file=sys.stderr)
        return 2
