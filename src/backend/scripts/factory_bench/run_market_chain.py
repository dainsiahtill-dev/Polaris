"""I3 driver: run the PM→CE→Director→QA TASK-MARKET chain on one workspace.

The pm CLI only routes dispatch through the workflow runtime
(SUPPORTED_ORCHESTRATION_RUNTIMES = ("workflow",)), so the market mainline
(`run_dispatch_pipeline` with KERNELONE_TASK_MARKET_MODE=mainline-full inline
consumers) is reached directly here: planning has already produced
runtime/contracts/pm_tasks.contract.json (run the factory runner with
planning, or any prior chain run); this driver re-dispatches that contract
through the market pipeline with CE step fission.

Usage:
  KERNELONE_TASK_MARKET_MODE=mainline-full KERNELONE_CE_STEP_FISSION=1 \
  python scripts/factory_bench/run_market_chain.py --workspace ~/Temp/factory-bench/L2-12
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Polaris task-market chain driver (I3)")
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--fresh-workspace",
        action="store_true",
        help="remove prior product files (html/js/css/md except AGENTS.md) so step diffs are real",
    )
    parser.add_argument(
        "--fresh-market",
        action="store_true",
        help="wipe runtime/task_market before dispatch (stale dead-letter state from prior broken runs poisons claims)",
    )
    args = parser.parse_args()

    workspace_full = os.path.abspath(os.path.expanduser(args.workspace))
    os.environ.setdefault("KERNELONE_WORKSPACE", workspace_full)
    os.chdir(workspace_full)

    from polaris.bootstrap.config import get_settings

    os.environ.setdefault("KERNELONE_RUNTIME_CACHE_ROOT", str(get_settings().runtime_base))
    from polaris.kernelone.storage import resolve_ramdisk_root
    from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path, resolve_run_dir

    ramdisk_root = resolve_ramdisk_root(None)
    cache_root_full = build_cache_root(ramdisk_root, workspace_full) or ""

    if args.fresh_workspace:
        removed = []
        for entry in sorted(Path(workspace_full).iterdir()):
            if entry.is_file() and entry.suffix.lower() in {".html", ".js", ".css", ".md", ".json", ".py"}:
                if entry.name == "AGENTS.md":
                    continue
                entry.unlink()
                removed.append(entry.name)
        print(f"[market-chain] fresh workspace: removed {removed}", flush=True)

    if args.fresh_market:
        import shutil

        market_dir = resolve_artifact_path(workspace_full, cache_root_full, "runtime/task_market")
        shutil.rmtree(market_dir, ignore_errors=True)
        print(f"[market-chain] fresh market: wiped {market_dir}", flush=True)

    contract_path = resolve_artifact_path(workspace_full, cache_root_full, "runtime/contracts/pm_tasks.contract.json")
    with open(contract_path, encoding="utf-8") as fh:
        normalized = json.load(fh)
    run_id = str(normalized.get("run_id") or "pm-market-00001")
    run_dir = resolve_run_dir(workspace_full, cache_root_full, run_id)
    os.makedirs(run_dir, exist_ok=True)

    def _p(rel: str) -> str:
        return resolve_artifact_path(workspace_full, cache_root_full, rel)

    print(f"[market-chain] workspace={workspace_full}", flush=True)
    print(f"[market-chain] run_id={run_id} tasks={len(normalized.get('tasks') or [])}", flush=True)
    print(
        f"[market-chain] mode={os.environ.get('KERNELONE_TASK_MARKET_MODE')} "
        f"fission={os.environ.get('KERNELONE_CE_STEP_FISSION')}",
        flush=True,
    )

    from types import SimpleNamespace

    from polaris.cells.orchestration.pm_dispatch.public import run_dispatch_pipeline
    from polaris.delivery.cli.pm.chief_engineer import run_chief_engineer_analysis

    driver_args = SimpleNamespace(
        analysis_runner=run_chief_engineer_analysis,
        run_director=True,
        integration_qa=True,
    )

    outcome = run_dispatch_pipeline(
        callbacks=None,
        args=driver_args,
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        run_dir=run_dir,
        run_id=run_id,
        iteration=1,
        normalized=normalized,
        run_events=_p("runtime/events/market_chain.events.jsonl"),
        dialogue_full=_p("runtime/events/market_chain.dialogue.jsonl"),
        runtime_pm_tasks_full=contract_path,
        pm_out_full=_p("runtime/results/pm_market.output.json"),
        run_pm_tasks=os.path.join(run_dir, "contracts", "pm_tasks.contract.json"),
        run_director_result=os.path.join(run_dir, "results", "director.result.json"),
        docs_stage=None,
    )

    summary = {
        "exit_code": outcome.get("exit_code"),
        "used": outcome.get("used"),
        "error": str(outcome.get("error") or "")[:300],
        "director": (outcome.get("director_result") or {}).get("status")
        if isinstance(outcome.get("director_result"), dict)
        else None,
        "integration_qa": (outcome.get("integration_qa_result") or {}).get("passed")
        if isinstance(outcome.get("integration_qa_result"), dict)
        else None,
        "task_market": outcome.get("task_market") or outcome.get("inline_task_market"),
    }
    print("[market-chain] outcome: " + json.dumps(summary, ensure_ascii=False, default=str), flush=True)
    qa = outcome.get("integration_qa_result")
    if isinstance(qa, dict):
        print(
            "[market-chain] inline detail: "
            + json.dumps(
                {
                    k: qa.get(k)
                    for k in ("passed", "reason", "summary", "qa_results", "unresolved_task_ids", "rejected_task_ids")
                },
                ensure_ascii=False,
                default=str,
            )[:1500],
            flush=True,
        )
    return int(outcome.get("exit_code") or 0)


if __name__ == "__main__":
    sys.exit(main())
