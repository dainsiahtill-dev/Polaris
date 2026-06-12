"""I3 forensics: quantitative report from a task-market run (three-tier chain).

Reads work_items + work_item_transitions (+ dead_letter_items) from the keyed
runtime task_market.db and computes the I3 comparison metrics agreed in
THREE_TIER_TASK_DECOMPOSITION_BLUEPRINT_20260612.md §9.4: step-level success
rate, market retry absorption, CE gate interventions, and per-stage wall clock
— the market-mode side of the market-vs-serial baseline comparison.

Usage:
  python scripts/factory_bench/market_forensics.py --workspace ~/Temp/factory-bench/L2-12
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _span_seconds(timestamps: list[datetime]) -> float:
    if len(timestamps) < 2:
        return 0.0
    return (max(timestamps) - min(timestamps)).total_seconds()


def collect_forensics(db_path: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    items = [dict(row) for row in conn.execute("SELECT * FROM work_items ORDER BY task_id")]
    transitions = [dict(row) for row in conn.execute("SELECT * FROM work_item_transitions ORDER BY id")]
    dead_letters = [dict(row) for row in conn.execute("SELECT * FROM dead_letter_items")]
    conn.close()

    steps = [item for item in items if str(item.get("parent_task_id") or "")]
    parents = [item for item in items if not str(item.get("parent_task_id") or "")]

    by_task: dict[str, list[dict[str, Any]]] = {}
    for tr in transitions:
        by_task.setdefault(str(tr["task_id"]), []).append(tr)

    def _count(task_id: str, from_status: str, to_status: str) -> int:
        return sum(
            1 for tr in by_task.get(task_id, []) if tr["from_status"] == from_status and tr["to_status"] == to_status
        )

    step_rows: list[dict[str, Any]] = []
    for step in steps:
        task_id = str(step["task_id"])
        trs = by_task.get(task_id, [])
        # Wall from first claim (work actually started), not from publish —
        # every step is published at fission time, so publish-anchored spans
        # all collapse to the total runtime.
        claim_ts = [
            t for t in (_parse_ts(str(tr["emitted_at"])) for tr in trs if str(tr["to_status"]) == "in_execution") if t
        ]
        all_ts = [t for t in (_parse_ts(str(tr["emitted_at"])) for tr in trs) if t]
        worked_ts = [t for t in all_ts if claim_ts and t >= min(claim_ts)]
        step_rows.append(
            {
                "step_id": task_id,
                "parent": step.get("parent_task_id"),
                "status": step.get("status"),
                "exec_attempts": _count(task_id, "pending_exec", "in_execution"),
                "exec_requeues": _count(task_id, "in_execution", "pending_exec"),
                "qa_rounds": _count(task_id, "pending_qa", "in_qa"),
                "wall_seconds": round(_span_seconds(worked_ts), 1),
            }
        )

    parent_rows: list[dict[str, Any]] = []
    for parent in parents:
        task_id = str(parent["task_id"])
        children = [s for s in step_rows if s["parent"] == task_id]
        parent_rows.append(
            {
                "task_id": task_id,
                "status": parent.get("status"),
                "is_leaf": parent.get("is_leaf"),
                "fission_fanout": len(children),
                "design_claims": _count(task_id, "pending_design", "in_design"),
                "gate_requeues": _count(task_id, "in_design", "pending_design"),
            }
        )

    resolved_steps = [s for s in step_rows if s["status"] == "resolved"]
    all_ts = [t for t in (_parse_ts(str(tr["emitted_at"])) for tr in transitions) if t]
    first_exec_ts = [
        t
        for t in (
            _parse_ts(str(tr["emitted_at"]))
            for tr in transitions
            if tr["to_status"] in ("pending_exec", "in_exec", "pending_qa", "in_qa", "resolved")
        )
        if t
    ]

    return {
        "db_path": db_path,
        "parents": parent_rows,
        "steps": step_rows,
        "summary": {
            "parent_count": len(parents),
            "parents_resolved": sum(1 for p in parent_rows if p["status"] == "resolved"),
            "step_count": len(step_rows),
            "steps_resolved": len(resolved_steps),
            "step_success_rate": round(len(resolved_steps) / len(step_rows), 3) if step_rows else None,
            "total_exec_attempts": sum(s["exec_attempts"] for s in step_rows),
            "total_exec_requeues": sum(s["exec_requeues"] for s in step_rows),
            "total_gate_requeues": sum(p["gate_requeues"] for p in parent_rows),
            "dead_letter_count": len(dead_letters),
            "wall_seconds_total": round(_span_seconds(all_ts), 1),
            "wall_seconds_exec_phase": round(_span_seconds(first_exec_ts), 1),
        },
        "dead_letters": [
            {"task_id": d.get("task_id"), "reason": str(d.get("reason") or d.get("last_error") or "")[:160]}
            for d in dead_letters
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Task-market run forensics (I3)")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--json", action="store_true", help="emit the full JSON report")
    args = parser.parse_args()

    workspace_full = os.path.abspath(os.path.expanduser(args.workspace))
    os.environ.setdefault("KERNELONE_WORKSPACE", workspace_full)
    os.chdir(workspace_full)
    from polaris.bootstrap.config import get_settings

    os.environ.setdefault("KERNELONE_RUNTIME_CACHE_ROOT", str(get_settings().runtime_base))
    from polaris.kernelone.storage import resolve_ramdisk_root
    from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

    cache_root_full = build_cache_root(resolve_ramdisk_root(None), workspace_full) or ""
    db_path = resolve_artifact_path(workspace_full, cache_root_full, "runtime/task_market/task_market.db")
    if not os.path.exists(db_path):
        print(f"[forensics] no market db at {db_path}", file=sys.stderr)
        return 2

    report = collect_forensics(db_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    summary = report["summary"]
    print(f"[forensics] db={report['db_path']}")
    print(
        "[forensics] parents {resolved}/{total} resolved | steps {s_res}/{s_tot} resolved "
        "(success_rate={rate}) | dead_letters={dl}".format(
            resolved=summary["parents_resolved"],
            total=summary["parent_count"],
            s_res=summary["steps_resolved"],
            s_tot=summary["step_count"],
            rate=summary["step_success_rate"],
            dl=summary["dead_letter_count"],
        )
    )
    print(
        f"[forensics] exec attempts={summary['total_exec_attempts']} "
        f"requeues(absorbed retries)={summary['total_exec_requeues']} "
        f"CE gate requeues={summary['total_gate_requeues']}"
    )
    print(f"[forensics] wall total={summary['wall_seconds_total']}s exec-phase={summary['wall_seconds_exec_phase']}s")
    for parent in report["parents"]:
        print(
            f"  parent {parent['task_id']}: status={parent['status']} fanout={parent['fission_fanout']} "
            f"design_claims={parent['design_claims']} gate_requeues={parent['gate_requeues']}"
        )
    for step in report["steps"]:
        print(
            f"    step {step['step_id']}: status={step['status']} attempts={step['exec_attempts']} "
            f"requeues={step['exec_requeues']} qa_rounds={step['qa_rounds']} wall={step['wall_seconds']}s"
        )
    for dead in report["dead_letters"]:
        print(f"  DEAD {dead['task_id']}: {dead['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
