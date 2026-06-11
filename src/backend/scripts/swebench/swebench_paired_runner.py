#!/usr/bin/env python3
"""Paired two-arm SWE-bench runner over a pinned subset (Phase 0 north star).

Runs ``swebench_normal_mode.py`` twice (arm A vs arm B, e.g. local weak model
vs an isolated strong-model baseline per adr-0092), each arm × N repeats, and
emits one paired report (per-instance deltas + per-arm aggregates).

Isolation properties by construction:

* Every run is a FRESH SUBPROCESS — required because in-process repeated runs
  tear down the LLM provider (the scout multi-sample bug), and because each
  arm needs its own ``KERNELONE_LLM_CONFIG``.
* Runs execute SEQUENTIALLY — the local vLLM host saturates under concurrent
  load (and a saturated vLLM deadlocks unrelated local pytest), so the runner
  is the load mutex.
* Each (arm, repeat) gets its own ``--work-dir`` and predictions file, so a
  baseline arm's artifacts never mix with the weak-model arm (adr-0092).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from polaris.kernelone.benchmark.swebench_metrics import build_paired_report

_NORMAL_MODE = Path(__file__).resolve().parent / "swebench_normal_mode.py"


def _run_arm_repeat(
    *,
    label: str,
    repeat: int,
    subset: str,
    llm_config: str,
    base_dir: Path,
    max_loops: int,
) -> list[dict[str, Any]]:
    """One subprocess run of the normal-mode harness; returns score records."""
    run_dir = base_dir / f"{label}_r{repeat}"
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "predictions.jsonl"
    env = dict(os.environ)
    if llm_config:
        env["KERNELONE_LLM_CONFIG"] = llm_config
    cmd = [
        sys.executable,
        str(_NORMAL_MODE),
        "--subset",
        subset,
        "--score",
        "--max-loops",
        str(max_loops),
        "--work-dir",
        str(run_dir),
        "--out",
        str(out),
        "--run-prefix",
        f"paired_{label}_r{repeat}",
    ]
    print(f"[paired] === arm={label} repeat={repeat} config={llm_config or '(env default)'} ===", flush=True)
    completed = subprocess.run(cmd, env=env, check=False)
    if completed.returncode != 0:
        print(f"[paired] arm={label} repeat={repeat} exited {completed.returncode} (continuing)", flush=True)
    scores_path = Path(f"{out}.scores.json")
    if not scores_path.is_file():
        print(f"[paired] arm={label} repeat={repeat}: no scores file, recording empty repeat", flush=True)
        return []
    payload = json.loads(scores_path.read_text(encoding="utf-8"))
    return list(payload.get("records") or [])


def main() -> int:
    ap = argparse.ArgumentParser(description="Paired two-arm SWE-bench normal-mode runner")
    ap.add_argument("--subset", required=True, help="pinned subset name (pinned_subsets.json)")
    ap.add_argument("--label-a", default="arm_a", help="name of arm A (e.g. qwen3.6-27b)")
    ap.add_argument("--label-b", default="arm_b", help="name of arm B (e.g. baseline-fable5)")
    ap.add_argument("--config-a", default="", help="KERNELONE_LLM_CONFIG path for arm A ('' = env default)")
    ap.add_argument("--config-b", default="", help="KERNELONE_LLM_CONFIG path for arm B ('' = env default)")
    ap.add_argument("--repeats", type=int, default=1, help="repeats per arm (N>1 for variance)")
    ap.add_argument("--max-loops", type=int, default=4)
    ap.add_argument("--work-dir", default=os.path.expanduser("~/Temp/swebench-work/paired"))
    args = ap.parse_args()

    if args.config_a == args.config_b and args.label_a == args.label_b:
        ap.error("the two arms are identical; give them distinct configs and/or labels")

    base_dir = Path(args.work_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    arms: dict[str, list[list[dict[str, Any]]]] = {args.label_a: [], args.label_b: []}
    for label, config in ((args.label_a, args.config_a), (args.label_b, args.config_b)):
        for repeat in range(1, args.repeats + 1):
            records = _run_arm_repeat(
                label=label,
                repeat=repeat,
                subset=args.subset,
                llm_config=config,
                base_dir=base_dir,
                max_loops=args.max_loops,
            )
            arms[label].append(records)

    report = build_paired_report(args.label_a, arms[args.label_a], args.label_b, arms[args.label_b])
    report["subset"] = args.subset
    report["configs"] = {args.label_a: args.config_a, args.label_b: args.config_b}
    report_path = base_dir / "paired_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    delta = report["delta_summary_b_minus_a"]
    print(
        f"\n[paired] ===== {args.label_b} − {args.label_a} over {len(report['shared_instances'])} shared instances =====",
        flush=True,
    )
    for key in ("resolved", "pure_f2p_resolved", "gold_file_hit", "gold_hunk_overlap"):
        print(f"[paired]   Δ{key}: {delta[key]:+.3f}", flush=True)
    print(f"[paired] report -> {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
