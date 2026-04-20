"""Iterative benchmark loop: run until N failures, then fix and continue."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any

# Add backend to path before polaris imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from polaris.bootstrap.assembly import assemble_core_services
from polaris.cells.llm.evaluation.public.service import run_agentic_benchmark_suite
from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)

MAX_FAILURES = 5
POLL_INTERVAL_SEC = 10


def _run_once(workspace: str, role: str, level: str, transport: str, run_id: str) -> dict[str, Any]:
    """Run one iteration of the benchmark suite."""
    # Ensure full kernel bindings including MessageBus and TypedEventBusAdapter.
    # Without this, UEP stream events (tool_call/tool_result) are silently dropped
    # because the global MessageBus is not initialized (JournalSink has no bus).
    assemble_core_services(container=None, settings=None)

    import os

    os.environ.setdefault("KERNELONE_RUNTIME_ROOT", "X:/")
    from polaris.kernelone.storage.layout import clear_storage_roots_cache

    clear_storage_roots_cache()

    options: dict[str, Any] = {}
    if level:
        from polaris.delivery.cli.agentic_eval import _expand_level_range_to_case_ids

        level_prefixes = _expand_level_range_to_case_ids([level])
        if level_prefixes:
            options["matrix_case_ids"] = level_prefixes
    options["matrix_transport"] = transport
    options["observable"] = True

    context: dict[str, Any] = {}
    result = asyncio.run(
        run_agentic_benchmark_suite(
            {},
            None,  # model - auto resolve
            role,
            workspace=workspace,
            context=context,
            options=options,
        )
    )
    return dict(result)


def _load_audit(workspace: str, run_id: str) -> dict[str, Any] | None:
    """Load AGENTIC_EVAL_AUDIT.json for a given run."""
    audit_path = Path(resolve_runtime_path(str(workspace), f"runtime/llm_evaluations/{run_id}/AGENTIC_EVAL_AUDIT.json"))
    if audit_path.exists():
        try:
            with open(audit_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.debug("Failed to load audit JSON: %s", e)
    return None


def _discover_latest_run_id(workspace: str) -> str | None:
    """Discover the most recent run_id from the evaluations directory."""
    eval_dir = Path(resolve_runtime_path(str(workspace), "runtime/llm_evaluations"))
    if not eval_dir.exists():
        return None
    dirs = [d for d in eval_dir.iterdir() if d.is_dir() and d.name != "baselines"]
    if not dirs:
        return None
    # Sort by mtime descending
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return dirs[0].name


def _print_summary(audit: dict[str, Any]) -> None:
    score = audit.get("score", {})
    failures = audit.get("failures", [])
    benchmark = audit.get("benchmark", {})
    print(f"\n{'=' * 60}")
    print(f"  Run: {benchmark.get('run_id')} | Role: {benchmark.get('role_scope')}")
    print(f"  Score: {score.get('overall_percent')}% | {score.get('passed_cases')}/{score.get('total_cases')} passed")
    print(f"  Failures: {len(failures)}")
    if failures:
        print("  Failed cases:")
        for f in failures[:8]:
            rc = f.get("root_cause", {})
            print(f"    - {f.get('case_id')}: [{rc.get('category')}/{rc.get('code')}] {rc.get('message', '')[:80]}")
        if len(failures) > 8:
            print(f"    ... and {len(failures) - 8} more")
    print(f"{'=' * 60}\n")


def _get_failed_case_ids(audit: dict[str, Any]) -> list[str]:
    return [f.get("case_id", "") for f in audit.get("failures", []) if f.get("case_id")]


def main() -> int:
    parser = argparse.ArgumentParser(description="Iterative benchmark loop with stop-on-N-failures")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--role", default="director")
    parser.add_argument("--level", default="l1-l5")
    parser.add_argument("--transport", default="stream")
    parser.add_argument("--max-failures", type=int, default=MAX_FAILURES)
    parser.add_argument("--max-iterations", type=int, default=999)
    args = parser.parse_args()

    workspace = str(Path(args.workspace).resolve())
    iteration = 0
    total_failures: list[str] = []
    all_passed = False

    while iteration < args.max_iterations:
        iteration += 1
        print(f"\n>>> Iteration {iteration} starting (max_failures={args.max_failures})")

        # Clean runtime cache for fresh run
        runtime_dir = Path(resolve_runtime_path(str(workspace), "runtime"))
        if runtime_dir.exists():
            shutil.rmtree(runtime_dir, ignore_errors=True)

        # Run benchmark
        start = time.time()
        result = _run_once(workspace, args.role, args.level, args.transport, f"iter_{iteration}")
        elapsed = time.time() - start

        # Discover run_id
        run_id = _discover_latest_run_id(workspace)
        if not run_id:
            print(f"[ITER {iteration}] Could not discover run_id, retrying...")
            time.sleep(5)
            continue

        # Load audit
        audit = _load_audit(workspace, run_id)
        if not audit:
            print(f"[ITER {iteration}] No audit found for run {run_id}, retrying...")
            time.sleep(5)
            continue

        _print_summary(audit)

        failures = _get_failed_case_ids(audit)
        new_failures = [f for f in failures if f not in total_failures]
        total_failures.extend(new_failures)

        failed_count = len(failures)

        if failed_count == 0:
            print(f"[ITER {iteration}] ALL CASES PASSED! Iteration={iteration} elapsed={elapsed:.1f}s")
            all_passed = True
            break

        if failed_count >= args.max_failures:
            print(f"[ITER {iteration}] Stop condition reached: {failed_count} failures >= {args.max_failures}")
            print(f"New failures this iteration: {new_failures}")
            print(f"Total unique failures so far: {total_failures}")
            break

        print(f"[ITER {iteration}] {failed_count} failures (below threshold {args.max_failures}), continuing...")

    if all_passed:
        print(f"\n*** BENCHMARK PERFECT PASS: 100% after {iteration} iteration(s) ***")
        return 0
    else:
        print(f"\n*** STOPPED at iteration {iteration} with {len(total_failures)} total unique failures ***")
        print(f"Failed case IDs: {total_failures}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
