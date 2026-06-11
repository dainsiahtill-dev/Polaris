#!/usr/bin/env python3
"""SWE-bench NORMAL MODE harness — drive the REAL Director agentic session.

Unlike ``arch_b_converge.py`` (which bypassed the agent OS: script-level
localization + single-shot ``_complete_for_role`` HTTP + manual ``_apply_blocks``),
this harness runs the genuine product path:

    RoleConsoleHost(workspace, role="director")
      -> _resolve_role_session(...)                      -> session_id
      -> _run_streaming_turn(host, "director", session_id, message)
           -> SessionOrchestrator.execute_stream(...)    # multi-turn agentic loop
              over TurnEngine + the Director's 44 real tools (repo_map / read_file /
              edit_file / edit_blocks / apply_patch / execute_command / ...),
              driven by the role-bound LLM in llm_config (director -> local gemma).

The harness ONLY clones the repo, hands the Director the problem statement, harvests
the resulting ``git diff`` as ``model_patch``, and scores it with the OFFICIAL
SWE-bench harness. It never localizes or edits code on the role's behalf.

See docs/blueprints/SWEBENCH_NORMAL_MODE_HARNESS_BLUEPRINT_20260609.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Repo + official-harness helpers (pure I/O, no bypass primitives).
from arch_b_converge import (
    DATASET,
    ensure_clone,
    run_git,
    run_harness_round,
)
from polaris.kernelone.benchmark.swebench_failure_taxonomy import (
    aggregate_labels,
    label_session,
    read_events,
)
from polaris.kernelone.benchmark.swebench_metrics import (
    SCORE_SCHEMA_VERSION,
    aggregate_score_records,
    build_score_record,
)
from polaris_solve_one import _is_test_path

MODEL_NAME = "polaris-director-normal"


def instance_report(work_dir: Path, run_id: str, instance_id: str) -> tuple[dict[str, Any], str]:
    """Read per-instance report.json + test_output.txt for the round.

    Local copy: arch_b_converge.instance_report builds the report path with ITS
    OWN MODEL_NAME ("Polaris-V1-Lightweight"), while this harness writes
    predictions as "polaris-director-normal" — importing it silently read a
    nonexistent path and reported resolved=False/applied=False unconditionally.
    """
    base = work_dir / "logs" / "run_evaluation" / run_id / MODEL_NAME / instance_id
    rep: dict[str, Any] = {}
    report_path = base / "report.json"
    if report_path.is_file():
        data = json.loads(report_path.read_text(encoding="utf-8"))
        rep = data.get(instance_id, {})
    test_output = ""
    test_output_path = base / "test_output.txt"
    if test_output_path.is_file():
        test_output = test_output_path.read_text(encoding="utf-8", errors="replace")
    return rep, test_output


# Director role timeout headroom (local gemma can be slow on long contexts).
os.environ.setdefault("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", "600")
# Per-turn in-place retry budget: the director role's platform retry is 0
# (resolve_platform_retry_max -> 0), so a transient local-gemma 502/empty would
# otherwise kill the whole instance. Retry the SAME turn before giving up — this
# reflects product robustness, not model-swapping cover-up.
TURN_RETRY_MAX = 3
# Git clone is network-bound; transient TLS/RPC failures must not abort the whole run.
CLONE_RETRY_MAX = 3
# Autonomous-mode clarification handling: a benchmark has no human to answer
# questions. When the agent asks for clarification (session pauses at
# session_waiting_human) instead of editing, auto-resume with a "proceed" nudge.
AUTONOMOUS_NUDGE_MAX = 2
_AUTONOMOUS_PROCEED_MSG = (
    "[mode:materialize]\n"
    "This is an AUTONOMOUS task — there is NO human available to answer questions, "
    "and no further clarification will ever be provided. Proceed NOW with your best fix "
    "using ONLY the bug report already given. Do NOT ask for clarification again. "
    "Localize the source file with your tools (repo_rg / repo_read_slice / read_file) and "
    "apply the edit with edit_blocks (you may use the line-range form: file + start + end + replace)."
)

_CLARIFY_SIGNALS_CJK = ("澄清", "更多信息", "请问", "请提供", "需要更多", "能否提供", "请说明", "？")
_CLARIFY_SIGNALS_ASCII = (
    "clarif",
    "could you",
    "please provide",
    "please specify",
    "need more",
    "which file",
    "can you confirm",
    "more context",
    "?",
)


def _looks_like_clarification(text: str) -> bool:
    """Heuristic: the agent is asking the user a question rather than acting."""
    token = (text or "").strip()
    if not token or "ALL_TASKS_COMPLETE" in token:
        return False
    low = token.lower()
    if any(sig in token for sig in _CLARIFY_SIGNALS_CJK):
        return True
    return any(sig in low for sig in _CLARIFY_SIGNALS_ASCII)


def ensure_clone_with_retry(repo: str, base_commit: str, ws: Path) -> None:
    """Clone with retries — GitHub TLS/RPC drops are transient, not fatal."""
    last_exc: Exception | None = None
    for attempt in range(1, CLONE_RETRY_MAX + 1):
        try:
            ensure_clone(repo, base_commit, ws)
            return
        except (RuntimeError, OSError) as exc:
            last_exc = exc
            print(f"[normal] clone {repo} attempt {attempt}/{CLONE_RETRY_MAX} failed: {exc}", flush=True)
            # A partial clone leaves a junk dir that would confuse the next attempt.
            shutil.rmtree(ws, ignore_errors=True)
            if attempt < CLONE_RETRY_MAX:
                time.sleep(5 * attempt)
    raise RuntimeError(f"clone failed after {CLONE_RETRY_MAX} attempts: {last_exc}")


def clean_model_patch(ws: Path, base_commit: str) -> str:
    """Capture a noise-free model_patch from the real session.

    ``git diff <base_commit>`` reports ONLY tracked-file changes, so untracked
    scratch the Director may create (reproduction scripts, ``__pycache__``,
    ``*.pyc``, ``*.orig``) is excluded by construction. Then drop any hunk that
    targets a test file (SWE-bench applies its own test patch; the gold patch
    only edits source).
    """
    raw = run_git(["diff", base_commit], cwd=ws).stdout or ""
    return _strip_test_hunks(raw)


def _strip_test_hunks(diff_text: str) -> str:
    """Drop per-file diff blocks whose target path is a test file."""
    kept: list[str] = []
    keep = True
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            parts = line.split()
            path = parts[-1][2:] if len(parts) >= 4 and parts[-1].startswith("b/") else ""
            keep = not _is_test_path(path)
        if keep:
            kept.append(line)
    return "".join(kept)


def _build_problem_message(problem: str) -> str:
    """Frame the SWE-bench task for the Director's materialize loop.

    The only input is the problem statement (the hidden tests are never shown).
    The Director must localize and edit SOURCE files using its own tools.
    """
    return (
        "[mode:materialize]\n"
        "You are fixing a real bug in this repository. The full repository is your "
        "workspace; use your tools to explore it.\n\n"
        "Instructions:\n"
        "- Localize the root cause yourself (repo_map / repo_rg / read_file / treesitter_*).\n"
        "- Then ACTUALLY APPLY the fix to the SOURCE file with an edit tool "
        "(edit_blocks / edit_file / apply_patch). You may use the line-range form of "
        "edit_blocks: file + start + end + replace.\n"
        "- Do NOT modify, add, or delete any test files.\n"
        "- Do NOT just describe or plan the fix — you MUST emit an edit tool call that "
        "changes the source. A plan without an applied edit is a failure.\n"
        "- Do NOT output ALL_TASKS_COMPLETE until you have applied at least one real edit "
        "that changes a source file. Only after the edit is applied, output exactly: "
        "ALL_TASKS_COMPLETE\n\n"
        "ISSUE / BUG REPORT:\n"
        f"{problem.strip()}\n"
    )


def solve_normal_mode(
    instance: dict[str, Any],
    work_dir: Path,
    max_loops: int,
) -> dict[str, Any]:
    """Solve one instance end-to-end through the real Director agentic session."""
    from polaris.delivery.cli.director.console_host import RoleConsoleHost
    from polaris.delivery.cli.terminal.commands import _resolve_role_session
    from polaris.delivery.cli.terminal.console import _director_output_suggests_more_work
    from polaris.delivery.cli.terminal.events import _run_streaming_turn

    iid = str(instance["instance_id"])
    repo = str(instance["repo"])
    base_commit = str(instance["base_commit"])
    problem = str(instance.get("problem_statement") or "")

    ws = work_dir / iid
    ensure_clone_with_retry(repo, base_commit, ws)
    run_git(["checkout", "-f", base_commit], cwd=ws)

    host = RoleConsoleHost(workspace=str(ws), role="director")
    session_id = _resolve_role_session(
        host,
        role="director",
        role_sessions={},
        host_kind=host.config.host_kind,
        session_title=f"swebench-{iid}",
    )

    def run_turn_with_retry(msg: str, label: str) -> Any:
        """One streaming call (= an internal multi-turn agentic loop of up to
        max_auto_turns), retried in place on transient director errors."""
        result = None
        for attempt in range(1, TURN_RETRY_MAX + 1):
            result = _run_streaming_turn(
                host,
                role="director",
                session_id=session_id,
                message=msg,
                json_render="none",
                debug=False,
                spinner_label="",
                dry_run=False,
                output_format="text",
            )
            if not bool(getattr(result, "saw_error", False)):
                return result
            print(
                f"[normal] {iid} {label} attempt {attempt}/{TURN_RETRY_MAX} saw_error -> retry",
                flush=True,
            )
        return result

    # Drive the session with ONE user message. The session orchestrator's own loop
    # (execute_stream, max_auto_turns=10) already drives localize -> read -> edit
    # across internal turns and builds its OWN continuation prompts. The harness must
    # NOT re-send a follow-up "continue" message: once the session reaches its terminal
    # `done` phase, a new user turn triggers an InvariantViolation (phase done ->
    # exploring) and aborts. The single call IS the full agentic session; only transient
    # transport errors are retried in place.
    _ = max_loops  # retained for CLI compat; the inner agentic loop owns turn count
    message = _build_problem_message(problem)
    result = run_turn_with_retry(message, "session")
    final_content = str(getattr(result, "final_content", "") or "")
    saw_error = bool(getattr(result, "saw_error", False))
    loops_run = 1

    # Autonomous-clarification handling: if the agent asked for clarification (the
    # session paused at session_waiting_human) and made no edit, there is no human
    # to answer. Resume with a "proceed" nudge — waiting_human is a resumable PAUSE,
    # so this does not trip the done->exploring invariant.
    for nudge in range(1, AUTONOMOUS_NUDGE_MAX + 1):
        if saw_error or clean_model_patch(ws, base_commit).strip():
            break
        if not _looks_like_clarification(final_content):
            break
        print(f"[normal] {iid} clarification detected -> autonomous nudge {nudge}", flush=True)
        result = run_turn_with_retry(_AUTONOMOUS_PROCEED_MSG, f"nudge {nudge}")
        final_content = str(getattr(result, "final_content", "") or "")
        saw_error = bool(getattr(result, "saw_error", False))
        loops_run += 1

    print(
        f"[normal] {iid} session: saw_error={saw_error} chars={len(final_content)} "
        f"nudges={loops_run - 1} more_work={_director_output_suggests_more_work(final_content)}",
        flush=True,
    )

    patch = clean_model_patch(ws, base_commit)
    print(
        f"[normal] {iid} session done: loops={loops_run} saw_error={saw_error} patch_lines={patch.count(chr(10))}",
        flush=True,
    )
    return {
        "instance_id": iid,
        "model_name_or_path": MODEL_NAME,
        "model_patch": patch,
        "_loops": loops_run,
        "_saw_error": saw_error,
    }


def _load_pinned_subset(name: str) -> list[str]:
    """Resolve a version-pinned instance list from ``pinned_subsets.json``.

    Pinned subsets are the north-star measurement contract: the same ids,
    forever, so paired runs (weak vs strong model, before vs after a harness
    change) stay comparable. New batches get a NEW subset key, never an edit.
    """
    path = Path(__file__).resolve().parent / "pinned_subsets.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    subsets: dict[str, Any] = data.get("subsets", {})
    if name not in subsets:
        raise SystemExit(f"unknown subset {name!r}; available: {', '.join(sorted(subsets))}")
    return [str(iid) for iid in subsets[name]["instance_ids"]]


def main() -> int:
    ap = argparse.ArgumentParser(description="SWE-bench normal-mode (real Director agent) harness")
    ap.add_argument("--instance-ids", help="comma-separated instance ids")
    ap.add_argument("--subset", help="named pinned subset from pinned_subsets.json (e.g. pinned-v1)")
    ap.add_argument("--max-loops", type=int, default=4, help="max Director continuation turns")
    ap.add_argument("--work-dir", default=os.path.expanduser("~/Temp/swebench-work/normal"))
    ap.add_argument("--run-prefix", default="polaris_normal")
    ap.add_argument("--out", default=os.path.expanduser("~/Temp/swebench-work/normal/predictions_normal.jsonl"))
    ap.add_argument("--score", action="store_true", help="run the official harness on each prediction")
    args = ap.parse_args()

    if bool(args.instance_ids) == bool(args.subset):
        ap.error("exactly one of --instance-ids or --subset is required")
    wanted = (
        _load_pinned_subset(args.subset)
        if args.subset
        else [s.strip() for s in args.instance_ids.split(",") if s.strip()]
    )

    from datasets import load_dataset

    ds = load_dataset(DATASET, split="test")
    rows = {str(r["instance_id"]): dict(r) for r in ds if str(r["instance_id"]) in set(wanted)}

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    score_records: list[dict[str, Any]] = []
    resolved_count = 0

    for iid in wanted:
        inst = rows.get(iid)
        if not inst:
            print(f"[normal] SKIP unknown instance {iid}", flush=True)
            continue
        print(f"[normal] === solving {iid} (max_loops={args.max_loops}) ===", flush=True)
        try:
            res = solve_normal_mode(inst, work_dir, args.max_loops)
        except Exception as exc:  # noqa: BLE001 — one instance must never abort the batch
            print(
                f"[normal] {iid} FAILED ({type(exc).__name__}: {exc}) — recording empty patch, continuing", flush=True
            )
            res = {"instance_id": iid, "model_name_or_path": MODEL_NAME, "model_patch": "", "_error": str(exc)}

        resolved = False
        if args.score:
            pred_path = work_dir / f"pred_{iid}.jsonl"
            pred_path.write_text(
                json.dumps(
                    {
                        "instance_id": iid,
                        "model_name_or_path": MODEL_NAME,
                        "model_patch": res["model_patch"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_id = f"{args.run_prefix}_{iid.replace('__', '_')}"
            run_harness_round(work_dir, pred_path, run_id)
            rep, _test_output = instance_report(work_dir, run_id, iid)
            # Full deterministic score record: strict resolved + pure_f2p
            # flakiness shield + gold-file/hunk partial credit (schema-stamped).
            score = build_score_record(
                instance_id=iid,
                model_patch=str(res.get("model_patch") or ""),
                gold_patch=str(inst.get("patch") or ""),
                report=rep,
            )
            # Deterministic failure-mode labels from the session event stream
            # (events.jsonl is pre-enrichment ground truth per the 2026-06-11
            # call_id forensics; execution truth = tool_result + batch_receipt).
            events: list[dict[str, Any]] = []
            events_dir = work_dir / iid / ".polaris" / "runtime" / "events"
            if events_dir.is_dir():
                for events_file in sorted(events_dir.glob("*.jsonl")):
                    events.extend(read_events(events_file))
            taxonomy = label_session(
                events,
                model_patch=str(res.get("model_patch") or ""),
                gold_patch=str(inst.get("patch") or ""),
            )
            score["failure_labels"] = taxonomy["labels"]
            score["failure_counters"] = taxonomy["counters"]
            score_records.append(score)
            res["_score"] = score
            resolved = score["resolved"]
            print(
                f"[normal] {iid} OFFICIAL: resolved={resolved} applied={score['patch_applied']} "
                f"pure_f2p={score['pure_f2p_resolved']} gold_file_hit={score['gold_file_hit']} "
                f"hunk_overlap={score['gold_hunk_overlap']:.2f}",
                flush=True,
            )
        if resolved:
            resolved_count += 1
        res["_resolved"] = resolved
        results.append(res)

    with open(args.out, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(
                json.dumps(
                    {
                        "instance_id": r["instance_id"],
                        "model_name_or_path": MODEL_NAME,
                        "model_patch": r["model_patch"],
                    }
                )
                + "\n"
            )

    if args.score:
        agg = aggregate_score_records(score_records)
        label_agg = aggregate_labels([{"labels": r.get("failure_labels", [])} for r in score_records])
        scores_path = Path(f"{args.out}.scores.json")
        scores_path.write_text(
            json.dumps(
                {"aggregate": agg, "failure_labels": label_agg, "records": score_records},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[normal] failure labels: {label_agg['label_frequency']}", flush=True)
        print(
            f"\n[normal] ===== SCORE ({SCORE_SCHEMA_VERSION}): "
            f"{agg['resolved']}/{agg['total']} resolved | "
            f"pure_f2p {agg['pure_f2p_resolved']}/{agg['total']} | "
            f"gold-file-hit {agg['gold_file_hit_rate'] * 100.0:.0f}% | "
            f"mean hunk-overlap {agg['mean_gold_hunk_overlap']:.2f} "
            f"(slack10 {agg['mean_gold_hunk_overlap_slack10']:.2f}) =====",
            flush=True,
        )
        print(f"[normal] score records -> {scores_path}", flush=True)
    print(f"[normal] predictions -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
