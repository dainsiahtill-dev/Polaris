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
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Repo + official-harness helpers (pure I/O, no bypass primitives).
from arch_b_converge import (
    DATASET,
    ensure_clone,
    instance_report,
    run_git,
    run_harness_round,
)
from polaris_solve_one import _is_test_path

MODEL_NAME = "polaris-director-normal"
# Director role timeout headroom (local gemma can be slow on long contexts).
os.environ.setdefault("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", "600")
# Per-turn in-place retry budget: the director role's platform retry is 0
# (resolve_platform_retry_max -> 0), so a transient local-gemma 502/empty would
# otherwise kill the whole instance. Retry the SAME turn before giving up — this
# reflects product robustness, not model-swapping cover-up.
TURN_RETRY_MAX = 3


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
        "- Apply a minimal fix to the SOURCE files using edit_file / edit_blocks / apply_patch.\n"
        "- Do NOT modify, add, or delete any test files.\n"
        "- Do NOT ask the user questions; just do the work.\n"
        "- When the fix is complete, output exactly: ALL_TASKS_COMPLETE\n\n"
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
    ensure_clone(repo, base_commit, ws)
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
    print(
        f"[normal] {iid} session: saw_error={saw_error} chars={len(final_content)} "
        f"more_work={_director_output_suggests_more_work(final_content)}",
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


def main() -> int:
    ap = argparse.ArgumentParser(description="SWE-bench normal-mode (real Director agent) harness")
    ap.add_argument("--instance-ids", required=True, help="comma-separated instance ids")
    ap.add_argument("--max-loops", type=int, default=4, help="max Director continuation turns")
    ap.add_argument("--work-dir", default=os.path.expanduser("~/Temp/swebench-work/normal"))
    ap.add_argument("--run-prefix", default="polaris_normal")
    ap.add_argument("--out", default=os.path.expanduser("~/Temp/swebench-work/normal/predictions_normal.jsonl"))
    ap.add_argument("--score", action="store_true", help="run the official harness on each prediction")
    args = ap.parse_args()

    from datasets import load_dataset

    ds = load_dataset(DATASET, split="test")
    wanted = [s.strip() for s in args.instance_ids.split(",") if s.strip()]
    rows = {str(r["instance_id"]): dict(r) for r in ds if str(r["instance_id"]) in set(wanted)}

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    resolved_count = 0

    for iid in wanted:
        inst = rows.get(iid)
        if not inst:
            print(f"[normal] SKIP unknown instance {iid}", flush=True)
            continue
        print(f"[normal] === solving {iid} (max_loops={args.max_loops}) ===", flush=True)
        res = solve_normal_mode(inst, work_dir, args.max_loops)

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
            resolved = bool(rep.get("resolved"))
            applied = bool(rep.get("patch_successfully_applied"))
            print(f"[normal] {iid} OFFICIAL: resolved={resolved} applied={applied}", flush=True)
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
        total = len(results)
        rate = (resolved_count / total * 100.0) if total else 0.0
        print(f"\n[normal] ===== SCORE: {resolved_count}/{total} resolved ({rate:.1f}%) =====", flush=True)
    print(f"[normal] predictions -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
