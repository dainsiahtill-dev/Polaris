#!/usr/bin/env python3
"""Polaris ↔ SWE-bench adapter — Phase A (decoupled host-edit).

Per instance:
  1. Load SWE-bench instance (HuggingFace `datasets`).
  2. `git clone <repo>` + `checkout <base_commit>` into a scratch workspace
     (Polaris runtime artifacts excluded from the repo via .git/info/exclude).
  3. Run Polaris on the workspace so the Director (local gemma) edits files.
     Phase A = Director CLI (proven file-editing path). Phase 1 layers a
     ChiefEngineer codegraph-localization pre-step and a QA review post-step;
     Phase B (separate) runs QA's real FAIL_TO_PASS test inside the SWE-bench
     Docker image for true test feedback.
  4. Extract `git diff` -> model_patch.
  5. Append to predictions.jsonl in SWE-bench prediction format.

Score the predictions with the official harness (separate venv + Docker):

    python -m swebench.harness.run_evaluation \\
        --dataset_name princeton-nlp/SWE-bench_Lite \\
        --predictions_path ~/Temp/swebench-work/predictions.jsonl \\
        --max_workers 4 --run_id polaris_v0

Phase A intentionally fixes "blind" (no per-instance test env); resolution
rates are a floor that Phase B (in-container test feedback) lifts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]  # .../polaris (src/backend/scripts/swebench/<file>)
BACKEND_DIR = REPO_ROOT / "src" / "backend"
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"
DEFAULT_MODEL_NAME = "Polaris-V1-Lightweight"
_EXCLUDE_TOKENS = (".polaris/", "/runtime/", "tags.cache.v1")


def log(msg: str) -> None:
    print(f"[polaris-swebench] {msg}", flush=True)


def load_instances(dataset: str, split: str, instance_ids: list[str] | None, limit: int | None) -> list[dict[str, Any]]:
    from datasets import load_dataset

    ds = load_dataset(dataset, split=split)
    rows: list[dict[str, Any]] = [dict(r) for r in ds]
    if instance_ids:
        wanted = set(instance_ids)
        rows = [r for r in rows if str(r.get("instance_id")) in wanted]
    if limit is not None:
        rows = rows[:limit]
    return rows


def run_git(args: list[str], cwd: Path, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)


def prepare_workspace(instance: dict[str, Any], work_dir: Path) -> Path:
    instance_id = str(instance["instance_id"])
    repo = str(instance["repo"])
    base_commit = str(instance["base_commit"])
    ws = work_dir / instance_id
    if ws.exists():
        shutil.rmtree(ws)
    ws.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo}.git"
    log(f"cloning {url}")
    cp = run_git(["clone", "--quiet", url, str(ws)], cwd=work_dir, timeout=1800)
    if cp.returncode != 0:
        raise RuntimeError(f"git clone failed for {repo}: {cp.stderr[-400:]}")
    cp = run_git(["checkout", "--quiet", base_commit], cwd=ws)
    if cp.returncode != 0:
        raise RuntimeError(f"git checkout {base_commit} failed: {cp.stderr[-400:]}")
    exclude_file = ws / ".git" / "info" / "exclude"
    try:
        with open(exclude_file, "a", encoding="utf-8") as fh:
            fh.write("\n.polaris/\nruntime/\n.polaris.kernelone.tags.cache.v1/\n")
    except OSError:
        pass
    return ws


def build_task_description(instance: dict[str, Any]) -> str:
    problem = str(instance.get("problem_statement") or "").strip()
    hints = str(instance.get("hints_text") or "").strip()
    parts = [
        "You are fixing a real bug in this repository.",
        "Resolve the following issue by editing the necessary SOURCE file(s) only.",
        "Do NOT add or modify test files. Make the smallest correct change.",
        "",
        "## Issue",
        problem,
    ]
    if hints:
        parts += ["", "## Maintainer hints", hints]
    return "\n".join(parts)


def solve_with_polaris(ws: Path, instance: dict[str, Any], iterations: int, dry_run: bool, timeout: int) -> None:
    _ = iterations  # single CE->Director->QA pass per instance (Phase A)
    if dry_run:
        log("dry-run: skipping Polaris solve")
        return
    env = dict(os.environ)
    env["KERNELONE_HOME"] = os.path.expanduser("~/.polaris")
    runtime_root = ws.parent / "_runtime" / str(instance["instance_id"])
    runtime_root.mkdir(parents=True, exist_ok=True)
    env["KERNELONE_RUNTIME_ROOT"] = str(runtime_root)
    env["KERNELONE_WORKSPACE"] = str(ws)
    solver = str(Path(__file__).resolve().parent / "polaris_solve_one.py")
    problem = str(instance.get("problem_statement") or "")
    cp = subprocess.run(
        [str(VENV_PY), solver, str(ws), str(instance["instance_id"])],
        cwd=str(BACKEND_DIR),
        env=env,
        input=problem,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    log(f"solve rc={cp.returncode} summary={cp.stdout.strip()[-300:]}")
    if cp.returncode != 0:
        log(f"solve stderr tail: {cp.stderr[-500:]}")


def extract_patch(ws: Path) -> str:
    # intent-to-add new (non-excluded) files so they show in the diff
    run_git(["add", "-N", "."], cwd=ws)
    cp = run_git(["diff"], cwd=ws)
    patch = cp.stdout or ""
    if any(tok in patch for tok in _EXCLUDE_TOKENS):
        kept: list[str] = []
        for idx, raw in enumerate(patch.split("\ndiff --git ")):
            section = raw if idx == 0 else "diff --git " + raw
            header = section.split("\n", 1)[0]
            if idx > 0 and any(tok in header for tok in _EXCLUDE_TOKENS):
                continue
            kept.append(section)
        patch = "".join(kept)
    return patch


def main() -> int:
    ap = argparse.ArgumentParser(description="Polaris SWE-bench adapter (Phase A)")
    ap.add_argument("--dataset", default="princeton-nlp/SWE-bench_Lite")
    ap.add_argument("--split", default="test")
    ap.add_argument("--instance-ids", default="", help="comma-separated subset")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--work-dir", default=os.path.expanduser("~/Temp/swebench-work"))
    ap.add_argument("--out", default=os.path.expanduser("~/Temp/swebench-work/predictions.jsonl"))
    ap.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--solve-timeout", type=int, default=3600)
    ap.add_argument("--dry-run", action="store_true", help="skip Polaris solve (plumbing test)")
    ap.add_argument("--list", action="store_true", help="list instances (id, repo) and exit")
    args = ap.parse_args()

    ids = [s.strip() for s in args.instance_ids.split(",") if s.strip()] or None
    instances = load_instances(args.dataset, args.split, ids, args.limit)
    log(f"loaded {len(instances)} instance(s) from {args.dataset}[{args.split}]")

    if args.list:
        for inst in instances:
            log(f"  {inst.get('instance_id')}  <-  {inst.get('repo')}  @ {str(inst.get('base_commit'))[:10]}")
        return 0

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    with open(out_path, "w", encoding="utf-8") as out:
        for i, inst in enumerate(instances, 1):
            iid = str(inst["instance_id"])
            log(f"[{i}/{len(instances)}] {iid}")
            patch = ""
            err = ""
            t0 = time.time()
            try:
                ws = prepare_workspace(inst, work_dir)
                solve_with_polaris(ws, inst, args.iterations, args.dry_run, args.solve_timeout)
                patch = extract_patch(ws)
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                err = f"{type(exc).__name__}: {exc}"
                log(f"ERROR {iid}: {err}")
            rec = {"instance_id": iid, "model_name_or_path": args.model_name, "model_patch": patch}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            results.append(
                {"instance_id": iid, "patch_lines": patch.count("\n"), "error": err, "secs": round(time.time() - t0, 1)}
            )

    log("=== summary ===")
    for r in results:
        tail = f" ERR={r['error']}" if r["error"] else ""
        log(f"  {r['instance_id']}: patch_lines={r['patch_lines']} secs={r['secs']}{tail}")
    log(f"predictions -> {out_path}")
    log(
        "score with: python -m swebench.harness.run_evaluation "
        f"--dataset_name {args.dataset} --predictions_path {out_path} --max_workers 4 --run_id polaris_v0"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
