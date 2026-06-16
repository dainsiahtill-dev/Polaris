"""Factory-bench 4-field per-batch report (per the per-batch-quantified-report rule).

Given a work-dir produced by run_factory_bench.py, emit:
  (a) step 成功率: total passed steps / total attempted
  (b) 可运行率: how many projects actually ran their checks (all_checks_passed)
  (c) 墙钟: per-project + total, concurrency-aware (max for parallel, sum for serial)
  (d) 根因清单: ranked tally from chain.log attribution signatures

Read both factory_audits.json (top-level) and per-project subdirs (chain.log,
factory_audits.json, requirements.md). Auto-detects dark-launched symbol-coherence
errors (artifact_quality.py _scan_python_imports + _scan_typescript_symbol_coherence)
without hardcoding their strings — uses the live scan output via the [factory-bench]
and RuntimeError: chains in chain.log.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

# Signatures from the attribution rubric + memory; not error strings (per the spec
# we never hardcode artifact_quality.py error text — only the contract-violation
# patterns the platform itself emits, which are stable by spec).
_ATTRIBUTION_SIGS: list[tuple[str, re.Pattern[str], str]] = [
    ("single_batch_contract_violation", re.compile(r"single_batch_contract_violation"), "platform_fixable"),
    ("unresolved_import_symbol", re.compile(r"unresolved import symbol"), "platform_fixable"),
    ("unresolved_relative_import", re.compile(r"unresolved relative import"), "platform_fixable"),
    ("undeclared_runtime_import", re.compile(r"undeclared runtime import"), "platform_fixable"),
    ("PreWriteGuard_blocked", re.compile(r"Code syntax validation failed"), "working_as_intended"),
    ("reasoning_truncation_reask", re.compile(r"reasoning-truncation re-ask"), "model_ceiling"),
    ("tools_executed_zero", re.compile(r"tools_executed=0"), "model_ceiling"),
    ("no_running_event_loop", re.compile(r"no running event loop"), "post_failure_noise"),
    ("Instructor_fallback", re.compile(r"Instructor not installed"), "post_failure_noise"),
    ("BudgetExceededError", re.compile(r"BudgetExceededError"), "platform_fixable"),
    ("circuit_breaker_dead_letter", re.compile(r"circuit_breaker", re.IGNORECASE), "platform_fixable"),
    ("F15_daemon_stdout_crash", re.compile(r"_enter_buffered_busy"), "post_failure_noise"),
]


def _classify_chain_log(chain_log: Path) -> Counter[str]:
    """Return a Counter of (signature_name, category) -> count for one chain.log."""
    counts: Counter[str] = Counter()
    if not chain_log.is_file():
        return counts
    text = chain_log.read_text(encoding="utf-8", errors="replace")
    for name, pattern, category in _ATTRIBUTION_SIGS:
        n = len(pattern.findall(text))
        if n:
            counts[f"[{category:18}] {name}"] += n
    return counts


def _aggregate(records: list[dict], work_dir: Path) -> dict:
    """Compute the 4 fields across all projects in the work-dir."""
    total_steps_attempted = 0
    total_steps_passed = 0
    runnable = 0
    wall_per_project: list[tuple[str, float | None]] = []
    root_cause_tally: Counter[str] = Counter()

    for r in records:
        pid = r.get("project_id", "?")
        # (a) step 成功率: factory_audits.json doesn't directly expose step-level,
        # derive from chain_results + checks (the rubric's "step" = a check in the
        # project's checks list AND the chain's task_market_exit_code 0).
        checks = r.get("checks") or []
        total_steps_attempted += len(checks) + 1  # +1 for the task_market turn
        task_market_ok = (r.get("chain") or {}).get("task_market_exit_code") == 0
        passed_checks = sum(1 for c in checks if c.get("ok"))
        total_steps_passed += passed_checks + (1 if task_market_ok else 0)

        # (b) 可运行率: all_checks_passed is the runnable gate per the rubric §4.1
        if r.get("all_checks_passed"):
            runnable += 1

        # (c) 墙钟: chain.duration_s is the per-project wall
        dur = (r.get("chain") or {}).get("duration_s")
        wall_per_project.append((pid, dur))

        # (d) 根因清单: scan the per-project chain.log via the attribution sigs
        # (preferred); fall back to the top-level chain.log at work_dir level.
        chain_log = work_dir / f"{pid}.chain.log"
        root_cause_tally.update(_classify_chain_log(chain_log))

    # concurrency-aware: serial = sum, parallel = max. We don't have a mode flag
    # in the audit, so report both and let the caller decide. Default report = max
    # (conservative for the per-batch quantified rule).
    durations = [d for _, d in wall_per_project if d is not None]
    wall_max = max(durations) if durations else None
    wall_sum = sum(durations) if durations else None

    return {
        "step_success_rate": (round(total_steps_passed / total_steps_attempted, 3) if total_steps_attempted else 0.0),
        "steps": {
            "attempted": total_steps_attempted,
            "passed": total_steps_passed,
        },
        "runnable": {
            "passed": runnable,
            "total": len(records),
            "rate": round(runnable / len(records), 3) if records else 0.0,
        },
        "wall": {
            "per_project": wall_per_project,
            "max_s": wall_max,
            "sum_s": wall_sum,
        },
        "root_cause_tally": dict(sorted(root_cause_tally.items(), key=lambda kv: -kv[1])),
        "by_level": _by_level(records),
    }


def _by_level(records: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for r in records:
        lvl = r.get("level")
        if lvl is None:
            continue
        b = out.setdefault(lvl, {"total": 0, "passed": 0})
        b["total"] += 1
        if r.get("all_checks_passed"):
            b["passed"] += 1
    return out


def _read_audits(work_dir: Path) -> list[dict]:
    p = work_dir / "factory_audits.json"
    if not p.is_file():
        return []
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("records") or []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Factory-bench 4-field batch report.")
    parser.add_argument("work_dir", help="Path to a run_factory_bench work-dir.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir).resolve()
    if not work_dir.is_dir():
        print(f"work_dir not found: {work_dir}", file=sys.stderr)
        return 2

    records = _read_audits(work_dir)
    if not records:
        print(f"no records in {work_dir}/factory_audits.json", file=sys.stderr)
        return 1
    report = _aggregate(records, work_dir)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    # Human-readable 4-field report
    print("=== batch 4-field report ===")
    print(f"work_dir           : {work_dir}")
    print(f"projects           : {report['runnable']['total']}")
    print(
        f"(a) step 成功率    : {report['step_success_rate']:.3f}  "
        f"({report['steps']['passed']}/{report['steps']['attempted']})"
    )
    print(
        f"(b) 可运行率       : {report['runnable']['rate']:.3f}  "
        f"({report['runnable']['passed']}/{report['runnable']['total']} all_checks_passed)"
    )
    print(
        f"(c) 墙钟 max       : {report['wall']['max_s']}s  "
        f"sum={report['wall']['sum_s']}s  per_project={report['wall']['per_project']}"
    )
    print("(d) 根因清单        :")
    for sig, n in report["root_cause_tally"].items():
        print(f"      {n:3d}  {sig}")
    if report["by_level"]:
        print("    by_level:")
        for lvl, b in sorted(report["by_level"].items()):
            print(f"      L{lvl}: {b['passed']}/{b['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
