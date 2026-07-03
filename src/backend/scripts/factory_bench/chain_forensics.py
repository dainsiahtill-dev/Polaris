"""I3 chain.log forensics — auto-extract the 4-field batch report.

Reads a factory-bench chain.log (interleaved stdout/stderr from
``polaris.delivery.cli.pm.cli`` plus the optional [market-chain] inline
driver plus the [factory-bench] wrap summary) and emits the per-batch
quantified report required by the standing-goal N>=3 floor ritual:

  1. step success rate      — turns where the Director emitted a write tool
                             that did not loop into a retry / dead-letter
  2. runnable product count — workspace files written + chain verdict
  3. wall-clock             — log first/last [LLMInvoker] timestamp span
  4. ranked root-cause tally — top error strings deduped, classified by
                              the 5-category attribution lens

Usage:
  python -m chain_forensics <chain.log>
  python -m chain_forensics <chain.log> --json

Classification is regex-based and intentionally lightweight: it speeds
triage, it does not replace the human/multi-agent verification.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS  # noqa: E402

# ---------------------------------------------------------------------------
# Known failure-signature catalog (the 4th field of the per-batch report).
#
# (regex, mechanism, attribution) — attribution ∈
#   platform_fixable  : harness bug with a concrete floor-safe fix locus
#   model_ceiling     : platform did its job, weak Director just can't
#   working_as_intended : a guard/gate correctly did its job
#   post_failure_noise : shows up AFTER the real failure, did not cause it
#   regression        : a recent change made things WORSE — catch first
#
# Order is informational; the tally is sorted by count desc.
# ---------------------------------------------------------------------------
_SIGNATURES: tuple[tuple[str, str, str], ...] = (
    # platform_fixable
    (
        r"single_batch_contract_violation",
        "mutation phase emitted no write tool (write-tool wall, F16)",
        "platform_fixable",
    ),
    (r"circuit_breaker", "step tripped breaker after repeated failures -> dead-letter", "platform_fixable"),
    (r"native_tool_call_decode_failed", "tool call could not be decoded (F14 decode wall)", "platform_fixable"),
    (r"whole-file write, not an edit", "from-scratch create routed to edit_blocks and rejected", "platform_fixable"),
    (
        r"successful_files.*write-steer|successful_files write-steer",
        "guard-on-write-steer regression on from-scratch creates (C3)",
        "platform_fixable",
    ),
    (
        r"F32.*read-loop bound.*disabled|F24.*read-loop",
        "F24 read-loop bound silently disabled (mode A, F32)",
        "platform_fixable",
    ),
    (r"BudgetExceededError", "context assembly over budget — crashes turn before any write (#46)", "platform_fixable"),
    # model_ceiling
    (r"reasoning-truncation re-ask", "weak Director reasoning overflowed output budget (5th floor)", "model_ceiling"),
    (r"output_length=0 output_preview=''", "weak Director emitted empty body (5th floor / F10)", "model_ceiling"),
    (
        r"PreWriteGuard.*EmptyCode|F29.*EmptyCode",
        "Empty __init__.py blocked (F29) — but pre-write guard intent is sound",
        "model_ceiling",
    ),
    (r"tools_executed=0", "weak model emitted NO tool call — content stuck in reasoning", "model_ceiling"),
    # working_as_intended
    (r"\[PreWriteGuard\] Blocked write", "PreWriteGuard correctly blocked a syntax-bad write", "working_as_intended"),
    (
        r"delivery-contract-downgraded-no-write-tools",
        "PM role correctly kept on PROPOSE_PATCH (no write tools)",
        "working_as_intended",
    ),
    (r"delivery-contract-upgrade-blocked", "intent classifier held the role's delivery mode", "working_as_intended"),
    # post_failure_noise
    (
        r"RuntimeError: no running event loop",
        "sync/async loop boundary noise — fires AFTER the real failure",
        "post_failure_noise",
    ),
    (r"UserWarning: CUDA initialization", "PyTorch CUDA driver warning — unrelated noise", "post_failure_noise"),
    (
        r"Instructor not installed, using fallback",
        "pydantic-instructor fallback banner — unrelated",
        "post_failure_noise",
    ),
    # regression
    (r"unknown to merge|regression|previously working", "explicit regression marker", "regression"),
)


_TIMESTAMP_RE = re.compile(
    r"\[LLMInvoker\.call\] ENTRY(?: POINT REACHED)?: profile=director.*?(?:run|turn)_id=([\w\-]+)"
)
_FILE_WRITE_TOOL_NAMES = tuple(sorted(WRITE_TOOLS, key=len, reverse=True))
_FILE_WRITE_RE = re.compile("|".join(re.escape(tool_name) for tool_name in _FILE_WRITE_TOOL_NAMES))
_TOOL_FAIL_RE = re.compile(r"\[director\] 工具执行返回失败结果")
_QA_VERDICT_RE = re.compile(r'"passed"\s*:\s*(true|false).*?"reason"\s*:\s*"([^"]*)"', re.DOTALL)
_OUTCOME_JSON_RE = re.compile(r"\[market-chain\] outcome: (\{[^\n]+\})")
_FACTORY_VERDICT_RE = re.compile(r"\[factory-bench\] (\S+) (PASS|FAIL): chain=(\S+) files=(\d+)")
_ARCHIVE_RE = re.compile(r"\[market-chain\] archived run evidence -> (\S+) \(run_id=([\w\-]+)\)")
_CIRCUIT_BREAKER_RE = re.compile(r"circuit_breaker_triggered|circuit_breaker.*consecutive_threshold")


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _detect_project(log_path: str, text: str) -> str:
    """Best-effort project id: explicit [factory-bench] === X === line, else the log stem."""
    m = re.search(r"\[factory-bench\] === (\S+)", text)
    if m:
        return m.group(1)
    stem = os.path.basename(log_path)
    for suffix in (".chain.log", ".log"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return os.path.splitext(stem)[0]  # pragma: no cover (defensive only)


def _wall_seconds(text: str) -> tuple[float | None, datetime | None, datetime | None]:
    """Wall-clock span from [LLMInvoker.call] timestamps (sub-second tokens).

    The chain.log itself is opened/closed at run start/end by run_factory_bench,
    so first/last LLM ENTRY line is the most reliable signal we have without
    touching external artifacts.
    """
    from datetime import datetime as _dt

    stamps: list[_dt] = []
    for match in re.finditer(r"\[LLMInvoker\.call\] ENTRY.*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text):
        try:
            stamps.append(_dt.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
    if len(stamps) < 2:
        return None, None, None
    span = (max(stamps) - min(stamps)).total_seconds()
    return span, min(stamps), max(stamps)


def _extract_outcome(text: str) -> dict[str, Any]:
    """Pull the [market-chain] outcome JSON line (exit_code, director, qa)."""
    m = _OUTCOME_JSON_RE.search(text)
    if not m:
        return {}
    raw = m.group(1)
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _extract_qa(text: str) -> dict[str, Any]:
    """integration_qa passed/reason (from [market-chain] inline detail)."""
    m = _QA_VERDICT_RE.search(text)
    if not m:
        return {"passed": None, "reason": ""}
    return {"passed": m.group(1) == "true", "reason": m.group(2)}


def _extract_factory_verdict(text: str) -> dict[str, Any]:
    """Last [factory-bench] X PASS|FAIL: chain=X files=N line (per-project)."""
    last: dict[str, Any] = {}
    for m in _FACTORY_VERDICT_RE.finditer(text):
        last = {"project": m.group(1), "verdict": m.group(2), "chain_state": m.group(3), "files": int(m.group(4))}
    return last


def _step_counts(text: str) -> dict[str, int]:
    """Director turn attempts vs distinct (file) writes.

    A turn with at least one write tool that does NOT also trip a tool-fail
    or circuit-breaker line on the same turn_id is a successful step. Heuristic,
    not perfect — but matches what the chain actually reports to its own audit.
    """
    turn_ids = sorted(set(_TIMESTAMP_RE.findall(text)))
    turn_failed: set[str] = set()
    for m in re.finditer(
        _TIMESTAMP_RE.pattern + r".{0,400}(?:" + _TOOL_FAIL_RE.pattern + r"|" + _CIRCUIT_BREAKER_RE.pattern + r")",
        text,
        re.DOTALL,
    ):
        turn_failed.add(m.group(1))
    turn_with_write: set[str] = set()
    for m in re.finditer(_TIMESTAMP_RE, text):
        if _FILE_WRITE_RE.search(text, m.end(), m.end() + 6000):
            turn_with_write.add(m.group(1))
    return {
        "turns_total": len(turn_ids),
        "turns_with_write": len(turn_with_write),
        "turns_failed": len(turn_failed),
    }


def _top_errors(text: str, top_n: int = 5) -> list[tuple[str, int, str]]:
    """Ranked root-cause tally: dedup by signature, classify by attribution."""
    tally: list[tuple[int, str, str, str]] = []
    for pattern, mechanism, attribution in _SIGNATURES:
        count = len(re.findall(pattern, text))
        if count:
            tally.append((count, pattern, mechanism, attribution))
    tally.sort(key=lambda row: row[0], reverse=True)
    return [(sig, count, attribution) for count, sig, _, attribution in tally[:top_n]]


def _archive_paths(text: str) -> list[str]:
    """Forensic archive directory paths the chain itself emitted (for deep dive)."""
    return [f"{m.group(1)} (run_id={m.group(2)})" for m in _ARCHIVE_RE.finditer(text)]


def summarize(log_path: str) -> dict[str, Any]:
    text = _read(log_path)
    outcome = _extract_outcome(text)
    qa = _extract_qa(text)
    verdict = _extract_factory_verdict(text)
    counts = _step_counts(text)
    wall, start, end = _wall_seconds(text)
    errors = _top_errors(text)
    return {
        "project": _detect_project(log_path, text),
        "log_path": os.path.abspath(log_path),
        "exit_code": outcome.get("exit_code"),
        "chain_state": verdict.get("chain_state") or _classify_state(outcome),
        "director_status": outcome.get("director"),
        "qa": qa,
        "verdict": verdict.get("verdict") or ("qa_passed" if qa.get("passed") else "unknown"),
        "step_counts": counts,
        "step_success_rate": (
            round((counts["turns_with_write"] - counts["turns_failed"]) / counts["turns_total"], 3)
            if counts["turns_total"]
            else None
        ),
        "file_count": verdict.get("files", 0),
        "wall_seconds": round(wall, 1) if wall else None,
        "wall_start": start.isoformat() if start else None,
        "wall_end": end.isoformat() if end else None,
        "archive_runs": _archive_paths(text),
        "top_errors": errors,
    }


_EXIT_BY_CODE = {0: "clean", 4: "director_partial", 5: "qa_failed"}


def _classify_state(outcome: dict[str, Any]) -> str:
    code = outcome.get("exit_code")
    if isinstance(code, int):
        return _EXIT_BY_CODE.get(code, "hard_failed")
    return "unknown"


def _print_summary(report: dict[str, Any]) -> None:
    print(f"=== chain forensics: {report['project']} ===")
    print(f"log            : {report['log_path']}")
    print(f"chain_state    : {report['chain_state']}   exit_code={report['exit_code']}   verdict={report['verdict']}")
    qa = report["qa"]
    qa_line = f"qa_passed={qa.get('passed')}"
    if qa.get("reason"):
        qa_line += f"  reason='{qa['reason'][:80]}'"
    print(qa_line)
    sc = report["step_counts"]
    rate = report["step_success_rate"]
    rate_s = f"{rate:.3f}" if isinstance(rate, float) else "n/a"
    print(
        f"steps          : total={sc['turns_total']} with_write={sc['turns_with_write']} failed={sc['turns_failed']} success_rate={rate_s}"
    )
    print(f"file_count     : {report['file_count']}")
    wall = report["wall_seconds"]
    print(f"wall_seconds   : {wall}   ({report['wall_start']} -> {report['wall_end']})")
    if report["archive_runs"]:
        print("archive_runs   :")
        for a in report["archive_runs"]:
            print(f"    {a}")
    if report["top_errors"]:
        print("top_errors     :")
        for sig, count, attribution in report["top_errors"]:
            print(f"    {count:>4}  [{attribution:<18}]  {sig}")
    else:
        print("top_errors     : (none matched)")


def main() -> int:
    ap = argparse.ArgumentParser(description="chain.log forensics — 4-field per-batch report")
    ap.add_argument("log_path", help="path to a chain.log")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of one-page summary")
    args = ap.parse_args()
    if not os.path.isfile(args.log_path):
        print(f"[chain-forensics] no such file: {args.log_path}", file=sys.stderr)
        return 2
    report = summarize(args.log_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    _print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
