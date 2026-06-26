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
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any

# Ranked root-cause tally (velocity harness, 2026-06-15): the per-batch report's
# 4th field. Each known failure signature -> a one-line mechanism so a run log
# becomes a ranked root-cause list without hand-grepping. Order is informational.
_ROOT_CAUSE_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("BudgetExceededError", "context assembly over budget — crashes the turn before any write (order-4)"),
    ("single_batch_contract_violation", "mutation phase emitted no write tool (write-tool wall, F16)"),
    ("Empty write content", "write_file with blank content on a code/markup target (Wall 2)"),
    ("director_no_materialized_changes", "turn produced no disk change (empty/no-tool/verify-miss)"),
    ("tools_executed=0", "weak model emitted NO tool call at all — content stuck in reasoning (5th floor)"),
    ("whole-file write, not an edit", "from-scratch create routed to edit_blocks and rejected"),
    ("circuit_breaker", "step tripped the breaker after repeated failures -> dead-letter"),
    ("missing_execution_evidence", "no changed_files / step target not covered"),
    ("native_tool_call_decode_failed", "tool call could not be decoded (F14 decode wall)"),
    ("scope_conflict", "transient same-scope claim conflict (requeued)"),
    ("cognitive_runtime_blocked", "a HITL approval gate fired in headless mode"),
)


def root_cause_tally(log_path: str) -> list[tuple[int, str, str]]:
    """Grep a run log for known failure signatures; return ranked (count, signature, mechanism)."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    tally: list[tuple[int, str, str]] = []
    for signature, mechanism in _ROOT_CAUSE_SIGNATURES:
        count = len(re.findall(re.escape(signature), text))
        if count:
            tally.append((count, signature, mechanism))
    tally.sort(key=lambda row: row[0], reverse=True)
    return tally


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _step_verify(payload_raw: Any) -> str:
    """Pull the machine verify out of a leaf step's market payload."""
    from polaris.kernelone.quality.step_verify import normalize_step_verify

    try:
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
    except (TypeError, ValueError):
        return ""
    step = payload.get("construction_step") if isinstance(payload, dict) else None
    if isinstance(step, dict) and step.get("verify"):
        return normalize_step_verify(step.get("verify"))
    acceptance = payload.get("acceptance_criteria") if isinstance(payload, dict) else None
    if isinstance(acceptance, list) and acceptance:
        return normalize_step_verify(acceptance[0])
    return ""


_SYNTAX_CHECKERS: dict[tuple[str, ...], list[str]] = {
    (".js", ".mjs", ".cjs"): ["node", "--check"],
    (".py",): [sys.executable, "-m", "py_compile"],
}


def _syntax_check(workspace_full: str, target_files: set[str]) -> dict[str, dict[str, Any]]:
    """Generic, language-agnostic runnability gate: a grep-based step verify
    passes even when the code does not parse (live I3-r15: main.js satisfied
    every grep clause but `node --check` failed on a stray ';'). Run the
    language's own syntax checker on each code file when the tool is available.
    """
    import shutil
    import subprocess

    results: dict[str, dict[str, Any]] = {}
    for tf in sorted(target_files):
        path = os.path.join(workspace_full, tf)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(tf)[1].lower()
        cmd = next((list(base) for exts, base in _SYNTAX_CHECKERS.items() if ext in exts), None)
        if cmd is None:
            continue
        tool = cmd[0]
        if tool != sys.executable and shutil.which(tool) is None:
            results[tf] = {"checked": False, "reason": f"{tool} unavailable"}
            continue
        try:
            proc = subprocess.run([*cmd, path], capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            results[tf] = {"checked": False, "reason": str(exc)}
            continue
        ok = proc.returncode == 0
        results[tf] = {"checked": True, "ok": ok, "error": "" if ok else (proc.stderr or proc.stdout)[:200]}
    return results


def replay_runnable(items: list[dict[str, Any]], workspace_full: str) -> dict[str, Any]:
    """可运行率 (clause-coherence): re-run every leaf step's verify against the
    FINAL workspace.

    A step's own per-file verify passing at acceptance time does not mean the
    product runs — a later step can clobber an earlier step's markers (live
    I3-r14: enhancement index.html overwrote the base id="game" markers, so the
    base step's verify no longer holds and the canvas lookup in main.js breaks).
    Re-running the UNION of all step verifies against the final state catches
    that generically: the product is coherent iff every step still passes, and a
    file targeted by multiple steps where not all pass is an interface conflict.
    """
    from polaris.kernelone.quality.step_verify import assess_legacy_step_verify_command_safety, run_step_verify

    rows: list[dict[str, Any]] = []
    by_file: dict[str, list[bool]] = {}
    for item in items:
        if not str(item.get("parent_task_id") or ""):
            continue
        verify = _step_verify(item.get("payload"))
        if not verify:
            continue
        safety = assess_legacy_step_verify_command_safety(verify)
        safety_evidence: dict[str, Any] = {
            "allowed": safety.allowed,
            "reason": safety.reason,
            "blocked_tokens": list(safety.blocked_tokens),
            "blocked_clauses": list(safety.blocked_clauses),
        }
        failure_evidence = ""
        if not safety.allowed:
            outcome = None
            passes = False
            failure_evidence = f"step verify command rejected by safety policy: {safety.reason} :: {verify!r}"
        else:
            outcome = run_step_verify(verify, cwd=workspace_full)
            passes = outcome is not None and outcome[0] == 0
        target = str(
            (json.loads(item["payload"]) if isinstance(item.get("payload"), str) else {}).get("scope_paths") or ""
        )
        # prefer the construction_step target_file for the conflict grouping
        try:
            payload = json.loads(item["payload"]) if isinstance(item.get("payload"), str) else {}
            tf = (payload.get("construction_step") or {}).get("target_file") or ""
        except (TypeError, ValueError):
            tf = ""
        tf = tf or target
        rows.append(
            {
                "step_id": item.get("task_id"),
                "target_file": tf,
                "status": item.get("status"),
                "verify_passes_vs_final": passes,
                "verify_safety": safety_evidence,
                "verify_failure_evidence": failure_evidence,
            }
        )
        if tf:
            by_file.setdefault(tf, []).append(passes)
    total = len(rows)
    passing = sum(1 for r in rows if r["verify_passes_vs_final"])
    conflicts = {f: v for f, v in by_file.items() if len(v) > 1 and not all(v)}
    syntax = _syntax_check(workspace_full, set(by_file))
    syntax_failures = sorted(f for f, r in syntax.items() if r.get("checked") and not r.get("ok"))
    return {
        "steps": rows,
        "coherent_steps": passing,
        "total_steps_with_verify": total,
        "runnable_rate": round(passing / total, 3) if total else None,
        # Runnable demands BOTH: every step's markers survive in the final state
        # AND every code file actually parses. A grep-pass with broken syntax is
        # not a running product.
        "product_coherent": total > 0 and passing == total and not syntax_failures,
        "interface_conflict_files": sorted(conflicts),
        "syntax": syntax,
        "syntax_failures": syntax_failures,
    }


def _span_seconds(timestamps: list[datetime]) -> float:
    if len(timestamps) < 2:
        return 0.0
    return (max(timestamps) - min(timestamps)).total_seconds()


def collect_forensics(db_path: str, workspace_full: str = "") -> dict[str, Any]:
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

    runnable = replay_runnable(items, workspace_full) if workspace_full else {}

    return {
        "db_path": db_path,
        "parents": parent_rows,
        "steps": step_rows,
        "runnable": runnable,
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
            "runnable_rate": runnable.get("runnable_rate"),
            "product_coherent": runnable.get("product_coherent"),
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
    parser.add_argument(
        "--log",
        default="",
        help="run log path; if given, append a ranked root-cause tally (the 4th per-batch field)",
    )
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

    report = collect_forensics(db_path, workspace_full)
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
    runnable = report.get("runnable") or {}
    if runnable:
        print(
            "[forensics] 可运行率(verify vs final workspace)="
            f"{runnable['coherent_steps']}/{runnable['total_steps_with_verify']} "
            f"(rate={runnable['runnable_rate']}) product_coherent={runnable['product_coherent']}"
        )
        if runnable.get("interface_conflict_files"):
            print(
                f"[forensics] INTERFACE CONFLICTS (same file, steps disagree): {runnable['interface_conflict_files']}"
            )
        for sf in runnable.get("syntax_failures") or []:
            err = (runnable.get("syntax", {}).get(sf, {}).get("error") or "").replace("\n", " ")[:120]
            print(f"[forensics] SYNTAX FAIL {sf}: {err}")
        for srow in runnable["steps"]:
            if not srow["verify_passes_vs_final"]:
                print(
                    f"    DRIFT {srow['step_id']} ({srow['target_file']}): status={srow['status']} "
                    f"but verify FAILS against final workspace"
                )
    for parent in report["parents"]:
        print(
            f"  parent {parent['task_id']}: status={parent['status']} fanout={parent['fission_fanout']} "
            f"design_claims={parent['design_claims']} gate_requeues={parent['gate_requeues']}"
        )
    if args.log:
        tally = root_cause_tally(os.path.abspath(os.path.expanduser(args.log)))
        if tally:
            print("[forensics] 根因清单 (ranked root causes):")
            for count, signature, mechanism in tally:
                print(f"    {count:>4}  {signature} — {mechanism}")
        else:
            print("[forensics] 根因清单: (no known failure signatures in the log)")
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
